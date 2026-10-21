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
        p + "mio-taskhub.exe": b"fake-exe",
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
