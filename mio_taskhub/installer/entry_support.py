# -*- coding: utf-8 -*-
"""安装器 / 卸载器入口的共用支撑：stdio 重定向 + 用法输出。

**为什么需要**：
打包成 `console=False`（无黑框）的 exe 时，PyInstaller 会把 `sys.stdout`
与 `sys.stderr` 置为 None —— 此时 `print()` 会抛
`AttributeError: 'NoneType' object has no attribute 'write'`。
因此无界面（--silent）模式必须先把 stdio 重绑到日志文件，
否则静默安装会直接崩在第一行 print 上。

用法输出同样走该通道：GUI 程序没有控制台，用法只能写日志文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

USAGE_SETUP = """mio-taskhub 安装程序

用法：
  mio-taskhub-setup.exe                 启动图形安装向导
  mio-taskhub-setup.exe --silent        静默安装（无界面，CI / 批量部署）
  mio-taskhub-setup.exe --uninstall     启动图形卸载向导
  mio-taskhub-setup.exe --uninstall --silent   静默卸载
  mio-taskhub-setup.exe --status        查看安装状态
  mio-taskhub-setup.exe --help          显示本帮助

安装目录默认：%LOCALAPPDATA%\\Programs\\mio-taskhub
用户数据（数据库/日志/备份）默认保留在：%USERPROFILE%\\.mio_taskhub
"""

USAGE_UNINSTALL = """mio-taskhub 卸载程序

用法：
  uninstall.exe                    启动图形卸载向导
  uninstall.exe --silent           静默卸载（保留用户数据）
  uninstall.exe --silent --purge-data   静默卸载并删除用户数据（不可恢复）
  uninstall.exe --help             显示本帮助
"""


def log_path(name: str) -> Path:
    """安装器日志文件：%USERPROFILE%\\.mio_taskhub\\installer.log。"""
    d = Path.home() / ".mio_taskhub"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / name


def ensure_stdio(log_name: str = "installer.log"):
    """无控制台环境下把 stdout/stderr 重绑到日志文件。

    有控制台时原样返回（不干扰正常的命令行用法）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    path = log_path(log_name)
    try:
        f = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    return path


def emit(text: str, log_name: str = "installer.log") -> None:
    """输出一段文本：控制台可用则打印，否则写日志文件。"""
    if sys.stdout is not None:
        try:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
            sys.stdout.flush()
            return
        except Exception:  # noqa: BLE001
            pass
    path = log_path(log_name)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass
