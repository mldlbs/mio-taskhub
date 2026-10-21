# -*- coding: utf-8 -*-
"""「应用和功能」注册表项（HKCU 用户级，无需管理员）。

用 `winreg` 直接读写，不引入 pywin32（打包产物里没有）。
非 Windows 平台上所有函数退化为 no-op，测试可跨平台跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .paths import (
    APP_DISPLAY_NAME,
    APP_PUBLISHER,
    UNINSTALL_REG_KEY,
)

try:  # pragma: no cover —— 非 Windows 走 stub
    import winreg
except ImportError:  # pragma: no cover
    winreg = None


def available() -> bool:
    return winreg is not None


def _key_path() -> str:
    return UNINSTALL_REG_KEY


def write_entry(install_dir, uninstall_exe=None, version=None,
                display_name=None, publisher=None, icon=None,
                quiet_uninstall="") -> bool:
    """写入卸载注册表项。返回 True 表示成功（非 Windows 恒返回 True 空操作）。"""
    if not available():
        return True
    install_dir = Path(install_dir)
    if uninstall_exe is None:
        uninstall_exe = install_dir / "uninstall.exe"
    uninstall_exe = Path(uninstall_exe)
    values = {
        "DisplayName": display_name or APP_DISPLAY_NAME,
        "DisplayVersion": str(version or ""),
        "Publisher": publisher or APP_PUBLISHER,
        "InstallLocation": str(install_dir),
        "UninstallString": '"%s" %s' % (uninstall_exe, quiet_uninstall) if quiet_uninstall
                           else '"%s"' % uninstall_exe,
        "QuietUninstallString": '"%s" /S' % uninstall_exe,
        "DisplayIcon": str(icon or (install_dir / "mio-taskhub.exe")),
        "InstallDate": _today(),
        "NoModify": 1,
        "NoRepair": 1,
    }
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _key_path(),
            0,
            winreg.KEY_WRITE,
        ) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    except OSError:
        return False
    return True


def remove_entry() -> bool:
    """删除卸载注册表项（不存在视为成功）。"""
    if not available():
        return True
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _key_path())
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def read_entry() -> dict:
    """读取卸载注册表项（不存在返回空 dict）。"""
    if not available():
        return {}
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key_path()) as key:
            i = 0
            while True:
                try:
                    name, value, _t = winreg.EnumValue(key, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    return out


def entry_exists() -> bool:
    return bool(read_entry())


def _today() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y%m%d")


def is_admin() -> bool:
    """当前进程是否管理员（用户级安装不需要，仅用于提示）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False
