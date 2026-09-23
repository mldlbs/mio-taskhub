# -*- coding: utf-8 -*-
"""单一版本源 + 安装目录解析。

`__version__` 是唯一权威版本号：打包时由 packaging/build.ps1 覆写，
运行时被 FastAPI(app version) 与 update 子模块共同引用。
"""
import sys
from pathlib import Path

__version__ = "0.4.0"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """程序目录：frozen → exe 所在目录；开发态 → 仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
