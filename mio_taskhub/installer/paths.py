# -*- coding: utf-8 -*-
"""安装 / 卸载的路径与常量**单一事实源**。

安装器、卸载器、更新器（`mio_taskhub.update.apply`）三方共用这里的常量，
避免「装在一个目录、卸在另一个目录」这类漂移。
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

APP_NAME = "mio-taskhub"
APP_DISPLAY_NAME = "mio-taskhub 任务中心"
#: **快捷方式名**（桌面 + 开始菜单的 .lnk 文件名）
#:
#: 与 `APP_DISPLAY_NAME` 分开：前者是快捷方式上显示的文字，后者是
#: 「应用和功能」里的显示名 / 安装器窗口标题。两者可以不一样。
SHORTCUT_NAME = "Taskhub"
APP_PUBLISHER = "mldlbs"
APP_EXE_NAME = "mio-taskhub.exe"
UNINSTALL_EXE_NAME = "uninstall.exe"
SETUP_EXE_NAME = "mio-taskhub-setup.exe"
PAYLOAD_ZIP_NAME = "mio-taskhub-win64.zip"
INSTALL_DIR_NAME = "mio-taskhub"

#: 「应用和功能」里的卸载注册表项（HKCU，用户级安装，无需管理员）
UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\mio-taskhub"

#: 安装目录里必须存在的关键项（缺一即视为 payload 不可用）
REQUIRED_PAYLOAD_ENTRIES = (
    APP_EXE_NAME,
    os.path.join("_internal", "web", "dist", "index.html"),
)

#: 用户数据目录（数据库 / 备份 / 日志），卸载时**默认保留**
DATA_DIR_NAME = ".mio_taskhub"


def is_windows() -> bool:
    return sys.platform == "win32"


def app_version() -> str:
    from mio_taskhub.version import __version__
    return __version__


def default_install_dir() -> Path:
    """默认安装目录：%LOCALAPPDATA%\\Programs\\mio-taskhub（用户级，免管理员）。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Programs" / INSTALL_DIR_NAME
    return Path.home() / "AppData" / "Local" / "Programs" / INSTALL_DIR_NAME


def data_dir() -> Path:
    """用户数据目录：%USERPROFILE%\\.mio_taskhub（与运行时 db.py 一致）。"""
    return Path.home() / DATA_DIR_NAME


def start_menu_dir() -> Path:
    """开始菜单目录（用户级）。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def install_manifest_path(install_dir) -> Path:
    return Path(install_dir) / "install.json"


def force_rmtree(path) -> bool:
    """删除目录树，**忽略只读属性**（PyInstaller 的 base_library.zip 是只读的）。

    Windows 上 `shutil.rmtree` 遇到只读文件会抛 PermissionError —— 打包产物的
    `_internal/base_library.zip` 正是只读，不处理就卸不干净。
    """
    path = Path(path)
    if not path.exists():
        return True

    def _onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:  # noqa: BLE001 —— 删除失败不应中断整体流程
            pass

    try:
        shutil.rmtree(path, onerror=_onerror)
    except TypeError:
        # Python 3.12+ 用 onexc，3.11 用 onerror
        shutil.rmtree(path, onexc=lambda f, p, e: _onerror(f, p, e))
    return not path.exists()


def force_remove_file(path) -> bool:
    path = Path(path)
    try:
        if path.exists():
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
    except OSError:
        return False
    return not path.exists()
