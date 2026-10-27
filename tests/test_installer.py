# -*- coding: utf-8 -*-
"""安装 / 卸载子系统单测。

覆盖：
- payload 布局契约（顶层前缀剥离 / 根目录内容）——与更新器共用同一份事实源
- 安装：解压 → 校验 → 落盘 → 写清单（不碰注册表/快捷方式）
- 卸载：删目录、保留用户数据、可选删数据
- 注册表项读写（Windows 真实 HKCU；非 Windows 自动跳过）
- 快捷方式创建 / 删除（Windows 真实 .lnk）
- 自更新保留卸载程序（防止更新后卸载入口消失）
"""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

from mio_taskhub import payload_layout
from mio_taskhub.installer import install as install_mod
from mio_taskhub.installer import payload as payload_mod
from mio_taskhub.installer import registry, shortcuts
from mio_taskhub.installer.paths import (
    APP_EXE_NAME,
    UNINSTALL_EXE_NAME,
    force_remove_file,
    force_rmtree,
)

IS_WINDOWS = sys.platform == "win32"
needs_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Windows 专属")


# ──────────────────── payload 布局契约 ────────────────────

def _make_zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _payload_entries(prefix: str = "") -> dict:
    p = (prefix + "/") if prefix else ""
    return {
        p + APP_EXE_NAME: b"fake-exe",
        p + "_internal/web/dist/index.html": b"<html></html>",
        p + "使用说明.txt": "说明".encode("utf-8"),
    }


def test_detect_prefix_none_for_flat_zip():
    assert payload_layout.detect_prefix(list(_payload_entries())) == ""


def test_detect_prefix_finds_single_top_dir():
    names = list(_payload_entries("mio-taskhub-v3"))
    assert payload_layout.detect_prefix(names) == "mio-taskhub-v3"


def test_extract_zip_flat_layout(tmp_path):
    z = _make_zip(tmp_path / "flat.zip", _payload_entries())
    dest = tmp_path / "out"
    prefix = payload_layout.extract_zip(z, dest)
    assert prefix == ""
    assert (dest / APP_EXE_NAME).exists()
    assert (dest / "_internal" / "web" / "dist" / "index.html").exists()


def test_extract_zip_strips_top_level_dir(tmp_path):
    """历史产物顶层带一层目录 —— 解压后必须剥掉，否则布局不对。"""
    z = _make_zip(tmp_path / "pref.zip", _payload_entries("mio-taskhub-v3"))
    dest = tmp_path / "out"
    prefix = payload_layout.extract_zip(z, dest)
    assert prefix == "mio-taskhub-v3"
    assert (dest / APP_EXE_NAME).exists()
    assert not (dest / "mio-taskhub-v3").exists()


def test_verify_detects_missing_entry(tmp_path):
    z = _make_zip(tmp_path / "bad.zip", {"readme.txt": b"x"})
    dest = tmp_path / "out"
    payload_layout.extract_zip(z, dest)
    ok, msg = payload_layout.verify(dest)
    assert ok is False and APP_EXE_NAME in msg


def test_extract_zip_rejects_path_traversal(tmp_path):
    z = _make_zip(tmp_path / "evil.zip", {"../escape.txt": b"x"})
    dest = tmp_path / "out"
    with pytest.raises(OSError):
        payload_layout.extract_zip(z, dest)


# ──────────────────── 安装 / 卸载 ────────────────────

def _build_fake_payload(tmp_path: Path) -> Path:
    z = _make_zip(tmp_path / "payload.zip", _payload_entries())
    return z


def test_install_writes_tree_and_manifest(tmp_path):
    payload = _build_fake_payload(tmp_path)
    target = tmp_path / "install"
    res = install_mod.install(payload_zip=payload, install_dir=target,
                              create_shortcuts=False, register=False)
    assert res.ok, res.message
    assert (target / APP_EXE_NAME).exists()
    assert (target / "_internal" / "web" / "dist" / "index.html").exists()
    manifest = json.loads((target / "install.json").read_text(encoding="utf-8"))
    assert manifest["install_dir"] == str(target)
    assert manifest["version"]


def test_install_from_prefixed_payload(tmp_path):
    """带顶层目录的历史 payload 也要能装。"""
    payload = _make_zip(tmp_path / "pref.zip", _payload_entries("mio-taskhub-v3"))
    target = tmp_path / "install"
    res = install_mod.install(payload_zip=payload, install_dir=target,
                              create_shortcuts=False, register=False)
    assert res.ok, res.message
    assert (target / APP_EXE_NAME).exists()


def test_install_missing_payload_fails_cleanly(tmp_path):
    res = install_mod.install(payload_zip=tmp_path / "nope.zip",
                              install_dir=tmp_path / "install",
                              create_shortcuts=False, register=False)
    assert res.ok is False
    assert any(s["name"] == "定位安装包" and not s["ok"] for s in res.steps)


def test_install_rejects_incomplete_payload(tmp_path):
    payload = _make_zip(tmp_path / "bad.zip", {"readme.txt": b"x"})
    res = install_mod.install(payload_zip=payload, install_dir=tmp_path / "install",
                              create_shortcuts=False, register=False)
    assert res.ok is False
    assert not (tmp_path / "install").exists()


def test_uninstall_removes_install_dir_keeps_data(tmp_path):
    payload = _build_fake_payload(tmp_path)
    target = tmp_path / "install"
    res = install_mod.install(payload_zip=payload, install_dir=target,
                              create_shortcuts=False, register=False)
    assert res.ok
    res2 = install_mod.uninstall(install_dir=target, remove_data=False,
                                 remove_shortcuts=False, remove_registry=False)
    assert res2.ok, res2.message
    assert not target.exists()
    assert any(s["name"] == "保留用户数据" for s in res2.steps)


def test_uninstall_is_idempotent(tmp_path):
    res = install_mod.uninstall(install_dir=tmp_path / "nothing",
                                remove_data=False, remove_shortcuts=False,
                                remove_registry=False)
    assert res.ok is True


def test_uninstall_refuses_to_delete_non_install_dir(tmp_path):
    """**回归**：卸载**不许**删掉「不是安装目录」的文件夹。

    实测踩过的坑（很严重）：`detect_install_dir()` 在冻结态会回退到
    「卸载器自身所在目录」。用户把 `mio-taskhub-setup.exe` 下载到
    `D:\\Downloads` 后双击 `--uninstall` → 程序把**整个 Downloads
    文件夹删掉**（用户的数据、别的下载文件全没了）。

    对策：两道闸门
    ① `looks_like_install_dir()` —— 目录里必须有 `mio-taskhub.exe`
       或 `install.json` 才认；
    ② `uninstall()` 里再校验一次，不满足就**什么都不删**直接返回。
    """
    # 造一个「不是安装目录」的目录，里面放用户文件
    fake = tmp_path / "Downloads"
    fake.mkdir()
    keep = fake / "我的重要文件.txt"
    keep.write_text("别删我", encoding="utf-8")

    assert install_mod.looks_like_install_dir(fake) is False

    res = install_mod.uninstall(install_dir=fake, remove_data=False,
                                remove_shortcuts=False, remove_registry=False)
    assert res.ok is False, "不该对非安装目录报成功"
    assert fake.exists(), "非安装目录被删了！"
    assert keep.exists(), "用户的文件被删了！"
    assert any(s["name"] == "校验安装目录" and not s["ok"] for s in res.steps)


def test_looks_like_install_dir_accepts_real_install_dir(tmp_path):
    """装了之后的目录必须被认成安装目录（否则安全闸门会挡住正常卸载）。"""
    payload = _build_fake_payload(tmp_path)
    target = tmp_path / "install"
    res = install_mod.install(payload_zip=payload, install_dir=target,
                              create_shortcuts=False, register=False)
    assert res.ok
    assert install_mod.looks_like_install_dir(target) is True
    # 卸载器（uninstall.exe）单独存在**不算**安装目录 ——
    # 用户从官网单独下载卸载器放在 Downloads 里，那不是安装目录。
    only_uninst = tmp_path / "only-uninstaller"
    only_uninst.mkdir()
    (only_uninst / "uninstall.exe").write_bytes(b"x")
    assert install_mod.looks_like_install_dir(only_uninst) is False


def test_single_file_bundle_has_no_standalone_uninstaller():
    """**回归**：安装包目录必须是**单文件**（只有 setup.exe）。

    卸载器已经合并进 setup.exe（payload 里有 uninstall.exe，payload 又
    被合并进 setup.exe）—— 再单独放一份只是副本，白占 10 MB，
    还让用户纠结「装的时候用哪个」。
    """
    repo = Path(__file__).resolve().parent.parent
    bundle = repo / "dist" / "mio-taskhub-installer"
    if not bundle.exists():
        pytest.skip("未构建安装包目录")
    names = sorted(p.name for p in bundle.iterdir() if p.is_file())
    assert "uninstall.exe" not in names, (
        "安装包目录里不该有独立的 uninstall.exe（已合并进 setup.exe）")
    assert "mio-taskhub-setup.exe" in names


def test_is_installed(tmp_path):
    payload = _build_fake_payload(tmp_path)
    target = tmp_path / "install"
    assert install_mod.is_installed(target) is False
    install_mod.install(payload_zip=payload, install_dir=target,
                        create_shortcuts=False, register=False)
    assert install_mod.is_installed(target) is True


# ────────────── payload 里的卸载器必须是「当前这一版」 ──────────────

def test_payload_uninstaller_is_not_stale(tmp_path):
    """**回归**：payload 里打包的 uninstall.exe 不能是旧版本。

    实测踩过（2026-10-21）：`dist/mio-taskhub-win64.zip` 是 14:08 打的，
    之后重建了 `dist_installer/uninstall.exe`（16:19），但**没重新打 zip**。
    于是安装到用户机器上的是**旧卸载器** —— 它连 `--dir` 都不透传，
    静默卸载直接异常退出（rc=23），表现为「卸载没反应、目录还在」。

    对策：
    1. 打包流程里 zip 必须用**当次构建**的 uninstall.exe（build.ps1 负责）
    2. 这里加一条测试：payload 里的 uninstall.exe 必须与 dist_installer 下
       当前产物**同哈希** —— 谁改了没重打 zip，测试立刻红。
    """
    repo = Path(__file__).resolve().parents[1]
    zip_path = repo / "dist" / "mio-taskhub-win64.zip"
    live = repo / "dist_installer" / "uninstall.exe"
    if not zip_path.exists() or not live.exists():
        pytest.skip("未构建安装包产物（dist/ 与 dist_installer/ 不齐）")
    import hashlib

    def _sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert UNINSTALL_EXE_NAME in names, (
            "payload 里没有 %s" % UNINSTALL_EXE_NAME)
        in_zip = hashlib.sha256(zf.read(UNINSTALL_EXE_NAME)).hexdigest()
    assert in_zip == _sha(live), (
        "payload 里的 %s 与 dist_installer/ 下的当前产物不一致 —— "
        "重新构建后必须重打 payload zip（build.ps1 步骤 4.5）"
        % UNINSTALL_EXE_NAME)


def test_uninstall_entry_forwards_dir_argument():
    """**回归**：uninstall.exe 必须把 --dir 透传给 CLI。

    实测踩过：旧版 uninstall_entry.py 调 `cli_main(['uninstall', '--yes'])`，
    **丢掉了 --dir** → CLI 走 `detect_install_dir()`（读注册表），
    静默卸载在「注册表指向别的目录」时会卸错地方。
    """
    src = (Path(__file__).resolve().parents[1]
           / "packaging" / "uninstall_entry.py")
    text = src.read_text(encoding="utf-8")
    assert "_passthrough" in text or "--dir" in text, (
        "uninstall_entry.py 没有透传 --dir")
    if "_passthrough" in text:
        assert "setup_entry" in text or "from mio_taskhub.installer" in text


def test_cli_reports_internal_error_instead_of_silent_exit():
    """**回归**：CLI 内部异常必须留痕，不能静默退出。

    打包成无黑框 exe 后 `disable_windowed_traceback=True` 会吞掉 traceback，
    用户只看到「进程秒退、目录还在、退出码 23」，完全无从排查。
    """
    from mio_taskhub.installer import cli

    def _boom(_args):
        raise RuntimeError("故意炸的")

    orig = cli._cmd_uninstall
    cli._cmd_uninstall = _boom
    try:
        rc = cli.main(["uninstall", "--yes", "--dir", "x"])
    finally:
        cli._cmd_uninstall = orig
    assert rc == 23, "内部异常应返回 23（区别于业务失败的 1）"


# ──────────────────── 只读文件删除 ────────────────────

def test_force_rmtree_removes_readonly_files(tmp_path):
    """打包产物的 base_library.zip 是只读的 —— 不处理就卸不干净。"""
    import stat
    d = tmp_path / "ro"
    d.mkdir()
    f = d / "base_library.zip"
    f.write_bytes(b"x")
    os.chmod(f, stat.S_IREAD)
    assert force_rmtree(d) is True
    assert not d.exists()


def test_force_remove_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert force_remove_file(f) is True
    assert not f.exists()


# ──────────────────── 进程路径匹配（回归） ────────────────────

def test_bare_exe_name_is_not_under_root():
    """裸 exe 名（无目录部分）不得被判为「位于 root 之下」。

    回归：Windows 的 Registry 等系统进程，psutil 报的 exe 没有路径。
    `os.path.realpath("")` 会解析成**当前工作目录** —— 卸载器恰好在安装目录
    里运行时，它就被误判成占用者 → 永远删不掉安装目录（2026-10-21 实测）。
    """
    from mio_taskhub.installer.processes import _is_under, _norm

    root_s = _norm(r"E:\some\install\dir")
    assert _is_under("Registry", root_s) is False
    assert _is_under("", root_s) is False
    assert _is_under(r"E:\some\install\dir\app.exe", root_s) is True


def test_norm_keeps_empty_string():
    from mio_taskhub.installer.processes import _norm

    assert _norm("") == ""


def test_list_processes_under_ignores_bare_names(monkeypatch, tmp_path):
    """枚举时不得把裸 exe 名的系统进程算进来。"""
    import mio_taskhub.installer.processes as proc

    class _FakeProc:
        def __init__(self, info):
            self.info = info

    class _FakePsutil:
        @staticmethod
        def process_iter(_attrs):
            return [_FakeProc({"pid": 1, "exe": "Registry"}),
                    _FakeProc({"pid": 2, "exe": str(tmp_path / "app.exe")})]

    monkeypatch.setitem(__import__("sys").modules, "psutil", _FakePsutil)
    got = proc.list_processes_under(tmp_path)
    assert got == [(2, str(tmp_path / "app.exe"))]


# ──────────────────── 注册表（Windows 真实 HKCU） ────────────────────

@needs_windows
def test_registry_entry_roundtrip():
    target = Path(os.environ.get("TEMP", "/tmp")) / "mio-reg-test"
    try:
        assert registry.write_entry(target, version="9.9.9") is True
        data = registry.read_entry()
        assert data.get("DisplayVersion") == "9.9.9"
        assert data.get("InstallLocation") == str(target)
        assert "uninstall.exe" in str(data.get("UninstallString"))
    finally:
        registry.remove_entry()
    assert registry.read_entry() == {}


@needs_windows
def test_registry_remove_missing_is_ok():
    assert registry.remove_entry() is True


# ──────────────────── 快捷方式（Windows 真实 .lnk） ────────────────────

@needs_windows
def test_create_and_remove_shortcut(tmp_path):
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")
    link = tmp_path / "app.lnk"
    ok = shortcuts.create_shortcut(link, target, working_dir=tmp_path)
    assert ok is True
    assert link.exists()
    assert shortcuts.remove_shortcut(link) is True
    assert not link.exists()


def test_shortcut_name_is_taskhub(tmp_path):
    """**回归**：快捷方式名是「Taskhub」。

    用户反馈「快捷方式名 Taskhub」。
    与 `APP_DISPLAY_NAME`（「mio-taskhub 任务中心」）**分开** ——
    后者是「应用和功能」里的显示名和安装器窗口标题，前者只是
    桌面/开始菜单上的 .lnk 文件名。
    """
    from mio_taskhub.installer.paths import (
        APP_DISPLAY_NAME,
        SHORTCUT_NAME,
        desktop_dir,
        start_menu_dir,
    )

    assert SHORTCUT_NAME == "Taskhub"
    # 两者确实是不同的东西（不是同一个常量）
    assert SHORTCUT_NAME != APP_DISPLAY_NAME

    # 桌面 / 开始菜单的 .lnk 路径用 Taskhub 命名
    sm = shortcuts.start_menu_link(tmp_path)
    dk = shortcuts.desktop_link(tmp_path)
    assert sm.name == "Taskhub.lnk", sm.name
    assert dk.name == "Taskhub.lnk", dk.name
    assert sm.parent == start_menu_dir()
    assert dk.parent == desktop_dir()


# ──────────────────── 自更新保留卸载程序 ────────────────────

def test_preserve_uninstaller_copies_from_old_install(tmp_path):
    """自更新整体替换安装目录 —— 必须把 uninstall.exe 带到新目录。"""
    from mio_taskhub.update.apply import _preserve_uninstaller

    old = tmp_path / "install"
    old.mkdir()
    (old / UNINSTALL_EXE_NAME).write_bytes(b"uninstaller")
    staging = tmp_path / "staging"
    staging.mkdir()

    assert _preserve_uninstaller(old, staging) is True
    assert (staging / UNINSTALL_EXE_NAME).exists()


def test_preserve_uninstaller_keeps_new_one(tmp_path):
    from mio_taskhub.update.apply import _preserve_uninstaller

    old = tmp_path / "install"
    old.mkdir()
    (old / UNINSTALL_EXE_NAME).write_bytes(b"old")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / UNINSTALL_EXE_NAME).write_bytes(b"new")

    assert _preserve_uninstaller(old, staging) is True
    assert (staging / UNINSTALL_EXE_NAME).read_bytes() == b"new"


def test_preserve_uninstaller_noop_when_absent(tmp_path):
    from mio_taskhub.update.apply import _preserve_uninstaller

    old = tmp_path / "install"
    old.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    assert _preserve_uninstaller(old, staging) is False


# ──────────────────── 更新器与安装器共用同一契约 ────────────────────

def test_updater_and_installer_share_required_entries():
    """更新器的 REQUIRED_ENTRIES 必须来自 payload_layout（避免两处漂移）。"""
    from mio_taskhub.update.apply import REQUIRED_ENTRIES as updater_entries

    assert tuple(updater_entries) == tuple(payload_layout.REQUIRED_ENTRIES)
    assert payload_layout.REQUIRED_ENTRIES == (
        APP_EXE_NAME,
        os.path.join("_internal", "web", "dist", "index.html"),
    )


def test_installer_payload_module_uses_shared_contract():
    assert payload_mod.extract_payload is not None
    assert payload_mod.verify_payload is not None


def test_find_payload_searches_exe_dir(monkeypatch, tmp_path):
    """回归：打包分发时 zip 与 setup.exe **同目录** → 必须能找到。

    2026-10-21 实测缺陷：搜索路径不含 exe 自身目录 → 打包后的静默安装
    在真实分发形态下报「未找到安装包」。
    """
    from mio_taskhub.installer import payload as pmod

    pkg = tmp_path / "mio-taskhub-win64.zip"
    pkg.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    monkeypatch.setattr(pmod, "_exe_dir", lambda: tmp_path)
    found = pmod.find_payload()
    assert found == pkg


def test_find_payload_prefers_exe_dir_over_cwd(monkeypatch, tmp_path):
    """exe 同目录的包优先于当前工作目录的包。"""
    from mio_taskhub.installer import payload as pmod

    exe_dir = tmp_path / "bundle"
    exe_dir.mkdir()
    near = exe_dir / "mio-taskhub-win64.zip"
    near.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    far = tmp_path / "mio-taskhub-win64.zip"
    far.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    monkeypatch.setattr(pmod, "_exe_dir", lambda: exe_dir)
    monkeypatch.chdir(tmp_path)
    assert pmod.find_payload() == near


def test_purge_does_not_taskkill_after_psutil_kill():
    """**回归**：psutil 杀成功之后**不能再 taskkill 同一个 pid**。

    实测踩过（2026-10-21，最难查的一个）：原代码是
    ```
    psutil.Process(pid).kill()
    _taskkill_tree(pid)      # ← 又 taskkill 一次同一个 pid
    ```
    进程已经死了 → **pid 被系统回收** → 下一个进程可能立刻复用它 →
    `taskkill /F /PID <pid>` 命中**被复用的新 pid** → 杀掉无辜进程。

    实测被杀的是**启动卸载器的 shell** → 连带把卸载器自己也带走 →
    **卸载器在 main() 之前就死了**（连日志都没写），表现为
    「进程秒退、目录还在、退出码 23」，且**偶发**（取决于 pid 是否被复用）。

    对策：
    1. psutil 杀成功后**不再 taskkill**
    2. 杀完 `wait()` 确认进程真的退出
    3. 只有 psutil 失败时才用 taskkill 兜底，且兜底前再核对一次身份
    """
    import inspect

    from mio_taskhub.installer import processes as proc

    src = inspect.getsource(proc.purge_processes_under)
    # taskkill 必须被包在「psutil 没杀掉」的分支里
    assert "if not killed:" in src, (
        "taskkill 必须在 psutil 杀失败时才执行（否则会命中被复用的 pid）")
    # 确认 kill 之后有 wait
    assert ".wait(" in src, "kill 之后应 wait 确认进程真的退出"


def test_gui_window_size_is_fixed():
    """**回归**：窗口尺寸必须**固定**，且日志面板展开后不变。

    实测踩过（2026-10-21）：Tk 默认按内容自动算尺寸 → 日志面板一展开
    窗口就「自己变大」，用户看到按钮位置跳动、显得不专业。

    对策：
    1. `WINDOW_W/H` 显式指定，`minsize == maxsize`（用户也拖不动）
    2. 日志区**固定高度** + 内部滚动条（不随行数增长）
    3. 展开日志时回调 `_lock_size()` 重新钉死
    """
    import tkinter as tk

    from mio_taskhub.installer import gui

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    try:
        app = gui.InstallerApp(root)
        root.update()
        root.update_idletasks()
        w1, h1 = root.winfo_width(), root.winfo_height()
        assert (w1, h1) == (gui._Base.WINDOW_W, gui._Base.WINDOW_H), (
            "窗口尺寸应为 %dx%d，实际 %dx%d"
            % (gui._Base.WINDOW_W, gui._Base.WINDOW_H, w1, h1))
        # 用户拖不动
        assert root.resizable() == (0, 0)
        assert root.minsize() == root.maxsize() == (gui._Base.WINDOW_W,
                                                   gui._Base.WINDOW_H)
        # 展开日志 + 灌 20 行 → 尺寸不变
        for i in range(20):
            app.log.append("[OK] 步骤 %d — 某某某某某某某某某某某某某某某某" % i)
        root.update()
        root.update_idletasks()
        w2, h2 = root.winfo_width(), root.winfo_height()
        assert (w2, h2) == (w1, h1), (
            "日志展开后窗口尺寸变了：%dx%d → %dx%d" % (w1, h1, w2, h2))
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_gui_window_is_centered_and_uses_project_logo():
    """**回归**：窗口要在**屏幕正中**，且用**项目 logo**（不是画的「M」圆标）。

    实测踩过的坑（2026-10-21）：
    - `winfo screen` 的参数是**窗口**不是坐标 → `winfo screen x y` 报
      "wrong # args" → 居中逻辑被 except 吞掉，窗口停在默认位置。
      正解：用 Windows API `MonitorFromPoint` + `GetMonitorInfoW`。
    - Tk **读不了 .ico**（报 "couldn't recognize data in image file"），
      标题栏图标只能用 PNG。
    """
    import tkinter as tk

    from mio_taskhub.installer import gui

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    try:
        app = gui.InstallerApp(root)
        root.update()
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        work = app._work_area()
        assert work, "取不到工作区（_work_area 抛异常了）"
        sx, sy, sw, sh = work
        exp_x = sx + (sw - w) // 2
        # 期望 y 用**工作区**（去掉任务栏）算
        exp_y = sy + (sh - h) // 2
        got_x, got_y = root.winfo_rootx(), root.winfo_rooty()
        assert abs(got_x - exp_x) <= 3, (
            "窗口水平没居中：%d，期望 %d" % (got_x, exp_x))
        assert abs(got_y - exp_y) <= 3, (
            "窗口垂直没居中：%d，期望 %d" % (got_y, exp_y))
        # 标题栏图标（项目 logo 的 PNG）
        assert gui._ICON_KEEPALIVE, "项目 logo 没被加载"
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_gui_window_opens_directly_in_center_without_flicker():
    """**回归**：窗口**打开时就在屏幕正中**，不能「先在左上角闪一下再跳过来」。

    用户反馈：「为啥窗口打开不能直接到中心位置，先在左上角打开，
    再闪到屏幕中心？」

    **根因**：Tk 创建 toplevel 时会**立刻把它映射出来**（位置由 Windows
    决定，通常在左上角），之后我们才调 `geometry` 挪到中间 → 用户看到
    「先在左上角闪一下」。

    **解法**：`withdraw()` → 建控件 + 算位置 → `deiconify()`，
    全程窗口不显示。

    **验证方式**：hook `<Map>` 事件（窗口被真正映射到屏幕时触发），
    断言**第一次映射时位置就已经在中心**。

    顺带固化两个坐标语义（实测确认，别写反）：
    - `geometry` 的 `+x+y` = **整个窗口**左上角（GetWindowRect.l/t）
    - `winfo_x/rootx` = **客户区**左上角（ClientToScreen(0,0)）
    """
    import tkinter as tk

    from mio_taskhub.installer import gui

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    seen = []

    def _on_map(_e=None):
        seen.append((root.winfo_rootx(), root.winfo_rooty()))

    root.bind("<Map>", _on_map)
    try:
        app = gui.InstallerApp(root)
        root.update()
        root.update_idletasks()

        work = app._work_area()
        assert work, "取不到工作区"
        sx, sy, sw, sh = work
        w, h = root.winfo_width(), root.winfo_height()
        exp_x = sx + (sw - w) // 2
        exp_y = sy + (sh - h) // 2

        assert seen, "没捕获到 <Map> 事件 —— 窗口可能从没被真正显示过"
        first_x, first_y = seen[0]
        assert abs(first_x - exp_x) <= 3 and abs(first_y - exp_y) <= 3, (
            "窗口首次显示时不在中心（会先在左上角闪一下）："
            "首次 (%d,%d)，期望 (%d,%d)" % (first_x, first_y, exp_x, exp_y))
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_frame_borders_measured_by_win32_while_hidden():
    """**回归**：隐藏状态下测边框偏移必须用 Win32，不能用 Tk 的读数。

    Tk 的 `winfo_x` 在窗口**从未映射**时返回 0（不是真实位置）→
    `winfo_rootx - winfo_x` 算出来是 320 这种垃圾值 → 居中会偏出去。

    正解：`ClientToScreen(0,0)`（客户区屏幕位置）减
    `GetWindowRect.l/t`（整个窗口位置）→ 隐藏/显示都准。
    """
    import tkinter as tk

    from mio_taskhub.installer import gui

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    try:
        app = gui.InstallerApp(root)
        root.update()
        off_x, off_y = app._frame_borders()
        # 左边框 8px 左右、标题栏 30px 左右 —— 合理范围即可，
        # 关键是**不能是几百**（那说明读到了隐藏状态的假值）
        assert 0 <= off_x <= 20, "左边框偏移异常：%s" % off_x
        assert 0 <= off_y <= 60, "标题栏偏移异常：%s" % off_y
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_browse_buttons_are_icon_only():
    """**回归**：浏览按钮只画图标，文字走悬停提示。

    用户反馈「选择文件夹按钮用图标」—— 620 宽的窗口里文字按钮太啰嗦。
    """
    import inspect

    from mio_taskhub.installer import gui

    src = inspect.getsource(gui)
    # 两个浏览按钮都要传 icon=
    assert 'icon="folder"' in src, "浏览目录按钮应使用文件夹图标"
    assert 'icon="file"' in src, "浏览安装包按钮应使用文件图标"
    # 按钮类要支持 icon 参数
    assert "icon=None" in inspect.signature(gui._Button.__init__).__str__() \
        or "icon" in inspect.signature(gui._Button.__init__).parameters


def test_installer_window_offers_uninstall_when_already_installed(tmp_path,
                                                                 monkeypatch):
    """**回归**：已装过时，安装器窗口要出现「卸载」按钮。

    **单文件安装包**：`mio-taskhub-setup.exe` 既是安装器也是卸载器。
    用户重新双击这一个文件时，应该能直接卸载，而不是让他去找别的 exe。
    """
    import tkinter as tk

    from mio_taskhub.installer import gui, install as install_mod

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    try:
        # 造一个「已安装」的目录
        fake_install = tmp_path / "installed"
        fake_install.mkdir()
        (fake_install / APP_EXE_NAME).write_bytes(b"x")
        monkeypatch.setattr(gui, "detect_install_dir", lambda: fake_install)

        app = gui.InstallerApp(root)
        root.update()
        root.update_idletasks()
        # _detect_existing 是 after(140) 排的 —— 跑够时间让它触发
        for _ in range(12):
            root.update()
            time.sleep(0.05)

        assert app._btn_uninstall.winfo_ismapped(), (
            "已安装时安装器窗口应显示「卸载」按钮")
        assert app.var_dir.get() == str(fake_install), (
            "应把安装目录指向已安装的那份")
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def test_installer_window_has_no_uninstall_button_when_fresh(tmp_path,
                                                             monkeypatch):
    """**回归**：没装过时**不该**显示「卸载」按钮（避免误点）。"""
    import tkinter as tk

    from mio_taskhub.installer import gui

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    try:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(gui, "detect_install_dir", lambda: empty)

        app = gui.InstallerApp(root)
        root.update()
        root.update_idletasks()
        for _ in range(12):
            root.update()
            time.sleep(0.05)

        assert not app._btn_uninstall.winfo_ismapped(), (
            "未安装时不该显示「卸载」按钮")
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


# ──────────────────── 单文件安装包（内嵌 payload） ────────────────────

def test_embedded_payload_roundtrip(tmp_path):
    """**回归**：payload 合并进 exe 后能原样解出来。

    用户反馈「打包能把安装程序和软件包合并吗？没必要分两个包」——
    真实安装器（NSIS/Inno）都是单文件 setup.exe。
    """
    from mio_taskhub.installer import embedded

    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"FAKE-EXE-CONTENT" * 100)
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr(APP_EXE_NAME, b"x" * 1000)
        zf.writestr("_internal/web/dist/index.html", b"y" * 50)

    embedded.append_payload(exe, payload)

    marker = embedded.read_embedded_marker(exe)
    assert marker, "读不到内嵌标记"
    offset, plen = marker
    assert plen == payload.stat().st_size

    out = embedded.extract_embedded_payload(exe, tmp_path / "out")
    with zipfile.ZipFile(out) as zf:
        assert APP_EXE_NAME in zf.namelist()
        assert zf.read(APP_EXE_NAME) == b"x" * 1000

    # exe 前半段没被动过（Windows 仍认这是合法 PE）
    assert exe.read_bytes()[:22] == (b"FAKE-EXE-CONTENT" * 100)[:22]


def test_embedded_payload_no_marker_is_detected(tmp_path):
    """没有内嵌 payload 时要能识别出来（不能把普通 exe 误判成安装包）。"""
    from mio_taskhub.installer import embedded

    plain = tmp_path / "plain.exe"
    plain.write_bytes(b"not an installer" * 50)
    assert embedded.read_embedded_marker(plain) is None
    assert embedded.has_embedded_payload(plain) is False


def test_embedded_payload_reappend_does_not_grow(tmp_path):
    """**回归**：重复合并**不能**让 exe 越滚越大（必须先剥掉旧的）。"""
    from mio_taskhub.installer import embedded

    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"BASE")
    p1 = tmp_path / "p1.zip"
    p1.write_bytes(b"Z" * 5000)
    p2 = tmp_path / "p2.zip"
    p2.write_bytes(b"Y" * 3000)

    embedded.append_payload(exe, p1)
    size1 = exe.stat().st_size
    embedded.append_payload(exe, p2)
    size2 = exe.stat().st_size

    # 第二次合并后只应是 BASE + p2（不是 BASE + p1 + p2）
    assert size2 < size1, "重复合并导致 exe 变大（旧的没剥掉）"
    assert size2 == 4 + 3000 + embedded.MARKER_LEN
    out = embedded.extract_embedded_payload(exe, tmp_path / "o")
    assert out.read_bytes() == b"Y" * 3000


def test_make_setup_script_merges_and_verifies(tmp_path):
    """合并脚本要能真的产出可用的单文件安装包。"""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"FAKE-PE" * 200)
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr(APP_EXE_NAME, b"x" * 100)
        zf.writestr("_internal/web/dist/index.html", b"y" * 10)

    out = tmp_path / "merged.exe"
    r = subprocess.run(
        [sys.executable, str(repo / "scripts" / "make_setup.py"),
         "--setup", str(setup), "--payload", str(payload),
         "--out", str(out), "--verify"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.exists() and out.stat().st_size > setup.stat().st_size

    # **必须清临时目录**：make_setup.py --verify 会解出内嵌 payload 做校验，
    # 那个临时目录不能留在 %TEMP% 里（实测跑一次就多一个）。
    import tempfile
    leftovers = [d for d in os.listdir(tempfile.gettempdir())
                 if d.startswith("mio_verify_") or d.startswith("mio_embedded_")]
    assert not leftovers, "合并脚本留下了临时目录：%s" % leftovers


def test_installer_tests_leave_no_residue(tmp_path):
    """**回归**：安装器相关测试**不许在用户机器上留残留**。

    实测踩过（2026-10-21）：跑一遍 `tests/test_installer.py` 之后，
    用户的 HKCU 里多了一个指向临时目录的「应用和功能」卸载条目，
    %TEMP% 里多了一个空的 `mio_embedded_*` 目录。

    根因：
    1. GUI 安装测试走的是 `install()` 默认参数（register=True）
       → 真的往 HKCU 写注册表项
    2. `make_setup.py --verify` 解出内嵌 payload 做校验，临时目录没删

    对策：测试自己清理 + 这条断言兜底。
    """
    import tempfile

    from mio_taskhub.installer import registry

    # 注册表：不该有指向临时目录的卸载项
    entry = registry.read_entry()
    if entry:
        loc = str(entry.get("InstallLocation") or "")
        assert "tmp" not in loc.lower() and "temp" not in loc.lower(), (
            "测试留下了指向临时目录的注册表项：%s" % loc)
    # 临时目录：不该有内嵌 payload 的残留
    leftovers = [d for d in os.listdir(tempfile.gettempdir())
                 if d.startswith("mio_embedded_") or d.startswith("mio_verify_")]
    assert not leftovers, "测试留下了临时目录：%s" % leftovers


# ──────────────────── 进程清理的时序（回归） ────────────────────

def test_purge_kills_parent_before_child():
    """**回归**：必须**先杀 supervisor 再杀 worker**。

    实测踩过（2026-10-21）：hub 是 supervisor→worker 双进程，supervisor
    在 worker 意外退出时会**重新 spawn** 它。先杀 worker 正好给了它重启的
    窗口 → 新 worker 又把安装目录钉住 → 目录永远删不掉。

    所以要按祖先链深度排序，父在前。
    """
    from mio_taskhub.installer import processes as proc

    # 构造「父 → 子」的进程链：伪造 psutil
    class _FakeProc:
        def __init__(self, pid, ppid):
            self.pid = pid
            self.ppid_ = ppid

        def ppid(self):
            return self.ppid_

        def parent(self):
            if self.ppid_ <= 1:
                return None
            return _FakeProc(self.ppid_, 1)

        def is_running(self):
            return True

        def exe(self):
            return ""

    class _FakePsutil:
        @staticmethod
        def Process(pid):
            # 700 是 supervisor（父）， 701 是 worker（子）
            return _FakeProc(pid, 1 if pid == 700 else 700)

    orig = sys.modules.get("psutil")
    sys.modules["psutil"] = _FakePsutil
    try:
        assert proc._depth_of(700) < proc._depth_of(701), \
            "父进程（supervisor）的深度必须小于子进程（worker）"
        # 排序后父在前
        left = [(701, "w.exe"), (700, "s.exe")]
        ordered = sorted(left, key=lambda item: proc._depth_of(item[0]))
        assert ordered[0][0] == 700, "排序后必须先杀 supervisor"
        assert ordered[1][0] == 701
    finally:
        if orig is not None:
            sys.modules["psutil"] = orig
        else:
            sys.modules.pop("psutil", None)


def test_purge_rechecks_process_identity_before_kill():
    """**回归**：杀之前要重新核对进程身份，防止 pid 复用误杀。

    `psutil.Process(pid)` 只缓存 pid，目标若在两次调用之间退出，
    pid 可能被系统**复用**给别的进程 → `taskkill /PID` 杀掉无辜进程
    （实测把用户的 shell 一起杀掉 → 卸载器秒退且无日志）。
    """
    from mio_taskhub.installer import processes as proc

    class _FakeProc:
        def __init__(self, pid, exe):
            self.pid = pid
            self._exe = exe

        def is_running(self):
            return True

        def exe(self):
            return self._exe

    class _FakePsutil:
        exe_of = {}

        @staticmethod
        def Process(pid):
            return _FakeProc(pid, _FakePsutil.exe_of.get(pid, ""))

    orig = sys.modules.get("psutil")
    sys.modules["psutil"] = _FakePsutil
    try:
        target = r"E:\some\dir\mio-taskhub.exe"
        # pid 还在、exe 一致 → 允许杀
        _FakePsutil.exe_of[900] = target
        assert proc._still_same_process(900, target) is True
        # pid 被复用给别的 exe → 拒绝杀
        _FakePsutil.exe_of[900] = r"C:\Windows\System32\cmd.exe"
        assert proc._still_same_process(900, target) is False
        # exe 为 None → 只看存活
        _FakePsutil.exe_of[901] = target
        assert proc._still_same_process(901, None) is True
    finally:
        if orig is not None:
            sys.modules["psutil"] = orig
        else:
            sys.modules.pop("psutil", None)


# ──────────────────── GUI 线程安全（回归） ────────────────────

def test_gui_worker_thread_must_not_touch_tk_variables():
    """**回归**：工作线程不能读 Tk 变量 / 调 root.after。

    实测踩过（2026-10-21）：`_work()` 里直接 `self.var_payload.get()`，
    从后台线程调 Tk 的变量存取会抛
    `RuntimeError: main thread is not in main loop` → 安装**根本没开始**，
    界面却卡在「正在安装…」，日志一片空白（异常处理器自己调
    `root.after` 也抛同样的错 → 连错误都显示不出来）。

    对策：
    1. Tk 变量在**主线程**读成纯 Python 值，再传给工作线程
    2. 工作线程 → 主线程的 UI 操作用**队列**交接（`_Base.post`），
       不再跨线程调 `root.after`
    """
    import inspect

    from mio_taskhub.installer import gui

    for cls_name in ("InstallerApp", "UninstallerApp"):
        cls = getattr(gui, cls_name, None)
        if cls is None:
            continue
        work = cls._work
        src = inspect.getsource(work)
        # 工作线程里不能出现 Tk 变量读取
        for bad in ("var_payload.get()", "var_dir.get()", "var_purge.get()",
                    "var_shortcuts.get()"):
            assert bad not in src, (
                "%s._work() 里读了 Tk 变量 %s —— 必须在主线程读好再传进来"
                % (cls_name, bad))
        # 也不能直接调 root.after
        assert "_ui_call(self.root" not in src, (
            "%s._work() 用了 _ui_call（跨线程调 root.after）—— "
            "请改用 self.post()" % cls_name)

    # 队列机制存在
    assert hasattr(gui._Base, "post")
    assert hasattr(gui._Base, "_drain_queue")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专属")
def test_gui_install_runs_under_real_mainloop(tmp_path):
    """**回归**：GUI 安装在**真 mainloop** 下要能跑完并显示「安装完成」。

    验证 GUI 必须用 `root.mainloop()`，用 `root.update()` 手动循环会报
    `main thread is not in main loop`（那是测试脚本的锅，不是程序 bug）。
    """
    import os
    import time

    import tkinter as tk

    from mio_taskhub.installer.gui import InstallerApp

    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip("无显示环境：%r" % e)

    target = tmp_path / "install"
    payload = _build_fake_payload(tmp_path)
    try:
        app = InstallerApp(root, payload_zip=str(payload))
        app.var_dir.set(str(target))
        app.var_shortcuts.set(False)
        root.update()
        root.after(200, app._start)

        state = {}

        def _check():
            state["status"] = app.status.cget("text")
            state["btn"] = app._btn_install._text
            state["log"] = app.log._text.get("1.0", "end")
            root.quit()

        deadline = time.time() + 120

        def _poll():
            if time.time() > deadline or "安装完成" in app.status.cget("text"):
                _check()
                return
            root.after(200, _poll)

        root.after(400, _poll)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        # **必须清注册表**：GUI 走的是 install() 的默认参数
        # （register=True）→ 会真的往 HKCU 写「应用和功能」条目。
        # 不清的话，跑一次测试就往用户机器上留一个指向临时目录的
        # 卸载入口（实测会残留）。
        try:
            from mio_taskhub.installer import registry
            registry.remove_entry()
        except Exception:  # noqa: BLE001
            pass

    assert state.get("status") == "安装完成", (
        "GUI 安装未完成，status=%r log=%r" % (state.get("status"),
                                             (state.get("log") or "")[:300]))
    assert state.get("btn") == "完成"
    assert (target / APP_EXE_NAME).exists()


# ──────────────────── 更新器：历史形态 payload（回归） ────────────────────

def test_updater_accepts_prefixed_payload(tmp_path):
    """回归：已发布的 zip 顶层带一层 `mio-taskhub-v3/`，更新器必须能解开。

    2026-10-21 实测缺陷：更新器 `verify_staging` 按「根目录即内容」校验，
    而发布资产的 zip 顶层带目录 → 校验必失败 → **自动更新永远装不上**。
    """
    from mio_taskhub.update.apply import verify_staging

    z = _make_zip(tmp_path / "payload.zip", _payload_entries("mio-taskhub-v3"))
    staging = tmp_path / "staging"
    payload_layout.extract_zip(z, staging)

    ok, msg = verify_staging(staging)
    assert ok, msg
    assert (staging / APP_EXE_NAME).exists()
    assert not (staging / "mio-taskhub-v3").exists()


def test_updater_accepts_flat_payload(tmp_path):
    """新形态（根目录即内容）同样要能解开。"""
    from mio_taskhub.update.apply import verify_staging

    z = _make_zip(tmp_path / "payload.zip", _payload_entries())
    staging = tmp_path / "staging"
    payload_layout.extract_zip(z, staging)

    ok, msg = verify_staging(staging)
    assert ok, msg
