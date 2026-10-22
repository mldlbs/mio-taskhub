# -*- mode: python ; coding: utf-8 -*-
"""安装程序 / 卸载程序的 PyInstaller spec。

产出两个**单文件** exe（onefile，便于随 zip 分发与自复制）：
  dist/mio-taskhub-setup.exe   安装向导（GUI）+ 静默安装（--silent）
  dist/uninstall.exe           卸载向导（GUI）+ 静默卸载（--silent）

与主程序 spec（mio-taskhub.spec）分开：主程序是 onedir（体积大、启动快），
安装器是 onefile（要能独立复制出去跑）。
"""
import os

from PyInstaller.utils.hooks import collect_submodules

SPECPATH = SPECPATH  # noqa: F821 —— PyInstaller 注入

# 安装器/卸载器只需少量依赖：mio_taskhub.installer + tkinter + psutil。
# 刻意**不**打整个 mio_taskhub（那会拖进 fastapi/sqlmodel/uvicorn，体积翻倍）。
hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "psutil",
    "mio_taskhub.installer",
    "mio_taskhub.installer.cli",
    "mio_taskhub.installer.gui",
    "mio_taskhub.installer.install",
    "mio_taskhub.installer.paths",
    "mio_taskhub.installer.payload",
    "mio_taskhub.installer.processes",
    "mio_taskhub.installer.registry",
    "mio_taskhub.installer.shortcuts",
    "mio_taskhub.payload_layout",
    "mio_taskhub.version",
]

excludes = [
    "torch", "tensorflow", "torchvision", "keras", "pandas", "numpy",
    "scipy", "matplotlib", "cv2", "transformers", "onnxruntime", "timm",
    "sympy", "hypothesis", "IPython", "jupyter", "notebook", "sklearn",
    "skimage", "jax", "cupy", "fsspec", "av", "sentry_sdk",
    "psycopg2", "psycopg2_binary", "psycopg",
    # 主程序的重量级依赖：安装器用不到
    "fastapi", "starlette", "uvicorn", "sqlmodel", "sqlalchemy",
    "mcp", "pydantic", "apscheduler", "pystray", "PIL",
    "croniter", "opentelemetry", "websockets", "httpx",
]


def _analysis(script, name):
    a = Analysis(
        [script],
        pathex=[SPECPATH, os.path.join(SPECPATH, "packaging")],
        # 安装器要显示**项目 logo**（标题栏图标 + 品牌头）→ 必须把
        # web/public/icon.png 打进包里（源码态从仓库根目录读，
        # 打包态从 sys._MEIPASS 读）。
        binaries=[],
        datas=[(os.path.join(SPECPATH, "web", "public", "icon.png"),
                "web/public")],
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
        optimize=0,
    )
    pyz = PYZ(a.pure)
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=name,
        console=False,
        disable_windowed_traceback=True,
        icon=os.path.join(SPECPATH, "web", "public", "icon.ico"),
    )
    return exe


setup_exe = _analysis(
    os.path.join(SPECPATH, "packaging", "setup_entry.py"), "mio-taskhub-setup")

uninstall_exe = _analysis(
    os.path.join(SPECPATH, "packaging", "uninstall_entry.py"), "uninstall")
