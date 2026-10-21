# -*- coding: utf-8 -*-
"""安装 / 卸载的**核心动作**（与 UI 解耦，便于单测）。

安装：解压 payload → 校验 → 清理旧进程 → 写注册表 → 建快捷方式 → 写 install.json
卸载：清理旧进程 → 删注册表 → 删快捷方式 → 删安装目录 →（可选）删用户数据
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import payload as payload_mod
from . import registry, shortcuts
from .paths import (
    APP_DISPLAY_NAME,
    APP_EXE_NAME,
    UNINSTALL_EXE_NAME,
    app_version,
    data_dir,
    default_install_dir,
    force_rmtree,
    install_manifest_path,
)
from .processes import purge_processes_under


@dataclass
class InstallResult:
    ok: bool = False
    install_dir: str = ""
    message: str = ""
    steps: list = field(default_factory=list)

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail})
        _log_step(name, ok, detail)


@dataclass
class UninstallResult:
    ok: bool = False
    message: str = ""
    steps: list = field(default_factory=list)

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail})
        _log_step(name, ok, detail)


# ────────────────────────── 安装 ──────────────────────────

def _log_step(name: str, ok: bool, detail: str = "") -> None:
    """把每一步结果写进安装日志（%USERPROFILE%\\.mio_taskhub\\installer.log）。

    用户现场出问题时，这个日志是唯一的线索来源（无黑框 exe 没有控制台）。
    """
    try:
        from .entry_support import log_path
        with open(log_path("installer.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s%s\n" % ("OK" if ok else "!!", name,
                                     (" — " + detail) if detail else ""))
    except Exception:  # noqa: BLE001
        pass


def install(payload_zip=None, install_dir=None, create_shortcuts=True,
            register=True, uninstall_exe=None, progress=None) -> InstallResult:
    """执行安装。progress(step_name) 用于 UI 回调。"""
    res = InstallResult()

    def _notify(msg):
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    install_dir = Path(install_dir or default_install_dir())
    res.install_dir = str(install_dir)

    try:
        zip_path = payload_mod.find_payload(payload_zip)
    except FileNotFoundError as e:
        res.message = str(e)
        res.step("定位安装包", False, str(e))
        return res
    res.step("定位安装包", True, str(zip_path))
    _notify("已找到安装包：%s" % zip_path.name)

    # 旧进程必须先停：运行中的 exe 会钉住安装目录，导致删除/覆盖失败。
    if install_dir.exists():
        if not purge_processes_under(install_dir, timeout=10.0):
            res.step("停止运行中的程序", False, "无法结束占用安装目录的进程")
            res.message = "无法结束占用安装目录的进程，请手动关闭后重试"
            return res
        res.step("停止运行中的程序", True, "安装目录内无残留进程")
    else:
        res.step("停止运行中的程序", True, "全新安装，无残留进程")

    # 解压到临时目录 → 校验 → 再落到安装目录（避免半成品目录）
    staging = install_dir.with_name(install_dir.name + ".installing")
    if staging.exists():
        force_rmtree(staging)
    _notify("正在解压程序文件…")
    try:
        prefix = payload_mod.extract_payload(zip_path, staging)
    except Exception as e:  # noqa: BLE001
        force_rmtree(staging)
        res.step("解压程序文件", False, str(e))
        res.message = "解压失败：%s" % e
        return res
    res.step("解压程序文件", True, "已解压 %s" % (prefix or "（无前缀目录）"))

    ok, msg = payload_mod.verify_payload(staging)
    if not ok:
        force_rmtree(staging)
        res.step("校验程序文件", False, msg)
        res.message = msg
        return res
    res.step("校验程序文件", True, msg)

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    if install_dir.exists():
        if not force_rmtree(install_dir):
            res.step("写入安装目录", False, "无法清空旧目录：%s" % install_dir)
            res.message = "无法清空旧安装目录，请手动关闭程序后重试"
            force_rmtree(staging)
            return res
    try:
        os.rename(str(staging), str(install_dir))
    except OSError as e:
        force_rmtree(staging)
        res.step("写入安装目录", False, str(e))
        res.message = "写入安装目录失败：%s" % e
        return res
    res.step("写入安装目录", True, str(install_dir))

    # 卸载程序：优先用安装器自带的（若在同一目录），否则从 payload 内的副本取。
    _ensure_uninstaller(install_dir, uninstall_exe)
    res.step("部署卸载程序", (install_dir / UNINSTALL_EXE_NAME).exists(),
             str(install_dir / UNINSTALL_EXE_NAME))

    if register:
        reg_ok = registry.write_entry(
            install_dir,
            uninstall_exe=install_dir / UNINSTALL_EXE_NAME,
            version=app_version(),
        )
        res.step("注册到「应用和功能」", reg_ok,
                 "HKCU\\%s" % registry.UNINSTALL_REG_KEY if reg_ok else "写入注册表失败")
    else:
        res.step("注册到「应用和功能」", True, "按参数跳过")

    if create_shortcuts:
        _create_links(install_dir, res)

    _write_manifest(install_dir)
    res.step("写入安装信息", True, str(install_manifest_path(install_dir)))

    res.ok = True
    res.message = "安装完成"
    _notify("安装完成")
    return res


def _ensure_uninstaller(install_dir, uninstall_exe=None) -> None:
    """把卸载程序放进安装目录。

    优先用 payload 自带的 `uninstall.exe`（构建时随包分发，最干净）。
    没有时退而求其次：复制安装器自身（`mio-taskhub-setup.exe`）——
    但**必须让被改名后的副本默认走卸载流程**，否则用户双击「卸载」
    反而会触发一次安装。改名感知逻辑在 `packaging/setup_entry.py`。
    """
    target = Path(install_dir) / UNINSTALL_EXE_NAME
    if target.exists():
        return  # payload 自带，无需覆盖
    candidates = []
    if uninstall_exe:
        candidates.append(Path(uninstall_exe))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable))
    for c in candidates:
        try:
            if c.is_file() and c.resolve() != target.resolve():
                shutil.copy2(str(c), str(target))
                return
        except OSError:
            continue


def _create_links(install_dir, res: InstallResult) -> None:
    target = shortcuts.app_target(install_dir)
    sm = shortcuts.start_menu_link(install_dir)
    ok_sm = shortcuts.create_shortcut(
        sm, target, working_dir=install_dir, description=APP_DISPLAY_NAME)
    res.step("创建开始菜单快捷方式", ok_sm, str(sm))

    dk = shortcuts.desktop_link(install_dir)
    ok_dk = shortcuts.create_shortcut(
        dk, target, working_dir=install_dir, description=APP_DISPLAY_NAME)
    res.step("创建桌面快捷方式", ok_dk, str(dk))


def _write_manifest(install_dir) -> None:
    manifest = {
        "app": APP_DISPLAY_NAME,
        "version": app_version(),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "install_dir": str(install_dir),
        "data_dir": str(data_dir()),
        "exe": str(Path(install_dir) / APP_EXE_NAME),
    }
    try:
        p = install_manifest_path(install_dir)
        p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


# ────────────────────────── 卸载 ──────────────────────────

def read_manifest(install_dir) -> dict:
    try:
        return json.loads(install_manifest_path(install_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def detect_install_dir() -> Path:
    """推断当前安装目录：优先注册表 → 默认目录 → 卸载器自身所在目录。"""
    entry = registry.read_entry()
    loc = str(entry.get("InstallLocation") or "").strip()
    if loc and Path(loc).exists():
        return Path(loc)
    default = default_install_dir()
    if (default / APP_EXE_NAME).exists():
        return default
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return default


def _is_self_inside(install_dir) -> bool:
    """当前进程（卸载器自己）是否位于 install_dir 内。"""
    try:
        exe = Path(sys.executable).resolve() if getattr(sys, "frozen", False) \
            else Path(__file__).resolve()
        return str(exe).lower().startswith(str(Path(install_dir).resolve()).lower())
    except Exception:  # noqa: BLE001
        return False


def _self_delete_dir(install_dir) -> bool:
    """卸载器位于待删目录内时的自删除：派发一个**会重试**的删除器。

    Windows 锁住正在运行的 exe 映像 → 目录删不掉。经典解法是让 cmd 在
    进程退出后再删（`rmdir /s /q` + 延时）。

    关键点：
    1. **必须用 Popen 而不是 run** —— run 会等 cmd 结束，而 cmd 的 rmdir
       恰恰要等本进程退出后才锁得住目录，等着就死锁。
    2. **必须重试** —— 单次 rmdir 会把能删的都删掉、留下被锁住的
       uninstall.exe，然后报错退出（实测：目录只剩卸载器自己）。
       这里派发一个循环重试的 cmd：每 2 秒试一次，最多 20 轮。
    3. **本函数立即返回**（不等待）—— 卸载器自己还活着时永远删不干净，
       等待毫无意义。删除在卸载器退出后由 cmd 完成。
    4. **用 .bat 落地再执行** —— `cmd /c "<字符串>"` 里的 `%i` 会被
       cmd 当作参数占位符吞掉（实测报「此时不应有 %%i」）；写进 .bat 文件
       再执行就没有这层转义问题。
    """
    import subprocess
    import tempfile
    target = str(Path(install_dir).resolve())
    # 注意：批处理里的 for 变量要写 %%i（单 %i 会在 .bat 中被当占位符吞掉）。
    # 用 f-string 而不是 % 格式化 —— % 格式化会把 %%i 变成 %i。
    script = (
        "@echo off\r\n"
        "for /l %%i in (1,1,20) do (\r\n"
        f"  rmdir /s /q \"{target}\" >nul 2>&1\r\n"
        f"  if not exist \"{target}\" exit /b 0\r\n"
        "  ping 127.0.0.1 -n 2 >nul\r\n"
        ")\r\n"
        "exit /b 0\r\n"
    )
    bat = None
    try:
        fd, bat = tempfile.mkstemp(suffix=".bat", prefix="mio_uninst_")
        with os.fdopen(fd, "w", encoding="ascii", errors="replace") as f:
            f.write(script)
        subprocess.Popen(
            ["cmd", "/c", bat],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # **cwd 必须指向待删目录之外**：cmd 会继承当前工作目录，
            # 而 cmd 自己占着这个目录 → 它把要删的目录锁住了，
            # rmdir 永远删不掉（实测：文件全删了，目录壳子留着）。
            cwd=tempfile.gettempdir(),
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "DETACHED_PROCESS", 0)),
        )
    except Exception:  # noqa: BLE001
        if bat:
            try:
                os.unlink(bat)
            except OSError:
                pass
        return False
    return True


def uninstall(install_dir=None, remove_data=False, remove_shortcuts=True,
              remove_registry=True, progress=None) -> UninstallResult:
    """执行卸载。remove_data=True 时一并删除用户数据目录（默认保留）。"""
    res = UninstallResult()
    install_dir = Path(install_dir or detect_install_dir())

    def _notify(msg):
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    # 1) 停进程（否则文件句柄钉住目录，删不掉）
    if not purge_processes_under(install_dir, timeout=10.0):
        res.step("停止运行中的程序", False, "仍有进程占用安装目录")
        res.message = "仍有进程在使用程序，请关闭 mio-taskhub 后重试"
        return res
    res.step("停止运行中的程序", True, "已结束安装目录下的进程")

    # 2) 删注册表
    if remove_registry:
        ok = registry.remove_entry()
        res.step("移除「应用和功能」注册项", ok, "HKCU\\%s" % registry.UNINSTALL_REG_KEY)
    else:
        res.step("移除「应用和功能」注册项", True, "按参数跳过")

    # 3) 删快捷方式
    if remove_shortcuts:
        for link in (shortcuts.start_menu_link(install_dir),
                     shortcuts.desktop_link(install_dir)):
            ok = shortcuts.remove_shortcut(link)
            res.step("删除快捷方式", ok, str(link))

    # 4) 删安装目录
    #    卸载器自己可能就装在 install_dir 里（正常用法：运行安装目录下的
    #    uninstall.exe）—— Windows 锁住正在运行的 exe 映像，**删不掉自己**。
    #    做法：先直接删；删不掉（自己被锁）则派发延迟自删除的 cmd（见
    #    `_self_delete_dir`）。
    self_delete = _is_self_inside(install_dir)
    if install_dir.exists():
        ok = force_rmtree(install_dir)
        if not ok and self_delete:
            ok = _self_delete_dir(install_dir)
        res.step("删除程序目录", ok, str(install_dir))
        if not ok:
            res.message = ("无法删除程序目录：%s\n"
                           "请关闭安装目录下的 uninstall.exe 后手动删除该文件夹。"
                           % install_dir)
            return res
    else:
        res.step("删除程序目录", True, "目录已不存在")

    # 5) 用户数据（默认保留）
    dd = data_dir()
    if remove_data:
        if dd.exists():
            ok = force_rmtree(dd)
            res.step("删除用户数据", ok, str(dd))
        else:
            res.step("删除用户数据", True, "数据目录不存在")
    else:
        res.step("保留用户数据", True, "%s（数据库/日志/备份仍在）" % dd)

    res.ok = True
    res.message = "卸载完成"
    _notify("卸载完成")
    return res


def is_installed(install_dir=None) -> bool:
    install_dir = Path(install_dir or detect_install_dir())
    return (install_dir / APP_EXE_NAME).exists()
