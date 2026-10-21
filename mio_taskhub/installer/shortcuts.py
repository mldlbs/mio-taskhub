# -*- coding: utf-8 -*-
"""开始菜单 / 桌面快捷方式（.lnk）。

用 COM（`WScript.Shell`）创建快捷方式；不依赖 pywin32，也不需要管理员权限。
失败一律返回 False 并由调用方降级（不因快捷方式问题让整个安装失败）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .paths import APP_DISPLAY_NAME, APP_EXE_NAME


def _powershell() -> str:
    return "powershell"


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create_shortcut(link_path, target, arguments="", working_dir=None,
                     icon=None, description="") -> bool:
    """创建 .lnk 快捷方式。返回 True/False（失败不抛异常）。"""
    if sys.platform != "win32":
        return False
    link_path = Path(link_path)
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    target = str(target)
    working_dir = str(working_dir or Path(target).parent)
    icon = str(icon or target)

    # 用 -EncodedCommand 传脚本：中文路径 / 引号在 -Command 下易被 shell 改写。
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%s);"
        "$s.TargetPath=%s;$s.Arguments=%s;$s.WorkingDirectory=%s;"
        "$s.IconLocation=%s;$s.Description=%s;$s.Save()"
        % (
            _ps_quote(str(link_path)),
            _ps_quote(target),
            _ps_quote(str(arguments or "")),
            _ps_quote(working_dir),
            _ps_quote(icon),
            _ps_quote(str(description or APP_DISPLAY_NAME)),
        )
    )
    import base64
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-EncodedCommand", encoded],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and link_path.exists()


def remove_shortcut(link_path) -> bool:
    """删除快捷方式（不存在视为成功）。"""
    link_path = Path(link_path)
    try:
        if link_path.exists():
            link_path.unlink()
    except OSError:
        return False
    return not link_path.exists()


def start_menu_link(install_dir, name=None) -> Path:
    from .paths import start_menu_dir
    return start_menu_dir() / ("%s.lnk" % (name or APP_DISPLAY_NAME))


def desktop_link(install_dir, name=None) -> Path:
    from .paths import desktop_dir
    return desktop_dir() / ("%s.lnk" % (name or APP_DISPLAY_NAME))


def app_target(install_dir) -> Path:
    return Path(install_dir) / APP_EXE_NAME
