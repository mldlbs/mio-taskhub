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

a = Analysis(
    ["packaging/run_hub.py"],
    pathex=["."],
    binaries=[],
    datas=[("web/dist", "web/dist")],
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
    [("packaging/run_mcp.py", None, None)],
    exclude_binaries=True,
    name="mio-taskhub-mcp",
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe_hub,
    exe_mcp,
    a.binaries,
    a.datas,
    name="mio-taskhub",
)
