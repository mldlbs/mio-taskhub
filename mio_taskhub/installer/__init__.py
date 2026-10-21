# -*- coding: utf-8 -*-
"""安装 / 卸载子系统。

提供 Windows 用户级安装（无需管理员）所需的能力：安装目录管理、
「应用和功能」注册表项、开始菜单/桌面快捷方式、进程清理、payload 解压与校验。

设计约束：
- **不引入新依赖**：只用标准库 + 已在打包产物里的 psutil。
- **更新器兼容**：自更新会整体替换安装目录，因此卸载程序必须随 payload
  一起分发（见 `mio_taskhub.update.apply` 的 `_preserve_uninstaller`）。
"""
from __future__ import annotations

from .paths import (  # noqa: F401 —— 对外统一出口
    APP_DISPLAY_NAME,
    APP_EXE_NAME,
    APP_NAME,
    APP_PUBLISHER,
    DATA_DIR_NAME,
    INSTALL_DIR_NAME,
    PAYLOAD_ZIP_NAME,
    REQUIRED_PAYLOAD_ENTRIES,
    SETUP_EXE_NAME,
    UNINSTALL_EXE_NAME,
    UNINSTALL_REG_KEY,
    app_version,
    data_dir,
    default_install_dir,
    desktop_dir,
    force_remove_file,
    force_rmtree,
    install_manifest_path,
    is_windows,
    start_menu_dir,
)

__all__ = [
    "APP_DISPLAY_NAME", "APP_EXE_NAME", "APP_NAME", "APP_PUBLISHER",
    "DATA_DIR_NAME", "INSTALL_DIR_NAME", "PAYLOAD_ZIP_NAME",
    "REQUIRED_PAYLOAD_ENTRIES", "SETUP_EXE_NAME", "UNINSTALL_EXE_NAME",
    "UNINSTALL_REG_KEY", "app_version", "data_dir", "default_install_dir",
    "desktop_dir", "force_remove_file", "force_rmtree",
    "install_manifest_path", "is_windows", "start_menu_dir",
]
