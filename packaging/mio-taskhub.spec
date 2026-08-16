# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("pydantic")
    + collect_submodules("sqlmodel")
    + collect_submodules("sqlalchemy")
    + collect_submodules("apscheduler")
    + collect_submodules("anyio")
    + collect_submodules("httpx")
)

widget_hiddenimports = (
    ["webview", "clr", "pythonnet", "pystray", "PIL", "PIL.Image"]
    + collect_submodules("webview")
)

a = Analysis(
    ["run_hub.py"],
    pathex=[".", ".."],
    binaries=[],
    datas=[("../web/dist", "web/dist")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe_hub = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="mio-taskhub",
    console=False,
    disable_windowed_traceback=False,
    icon="web/public/favicon.ico" if False else None,
)

exe_mcp = EXE(
    PYZ(a.pure),
    ["run_mcp.py"],
    exclude_binaries=True,
    name="mio-taskhub-mcp",
    console=True,
    disable_windowed_traceback=False,
)

a_widget = Analysis(
    ["run_widget.py"],
    pathex=[".", ".."],
    binaries=[],
    datas=[("../web/public/icon.ico", "web/public")],
    hiddenimports=widget_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
exe_widget = EXE(
    PYZ(a_widget.pure),
    a_widget.scripts,
    exclude_binaries=True,
    name="mio-taskhub-widget",
    console=False,
    disable_windowed_traceback=True,
    icon="../web/public/icon.ico",
)

coll = COLLECT(
    exe_hub,
    exe_mcp,
    exe_widget,
    a.binaries,
    a.datas,
    a_widget.binaries,
    a_widget.datas,
    name="mio-taskhub",
)
