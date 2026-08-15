# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "apscheduler.schedulers.asyncio",
    "apscheduler.executors.asyncio",
    "sqlmodel",
    "sqlalchemy.dialects.sqlite",
]

mcp_hiddenimports = (
    ["httpx", "mcp", "mcp.server.fastmcp", "pydantic"]
    + collect_submodules("mcp")
)

widget_hiddenimports = (
    ["webview", "clr", "pythonnet"]
    + collect_submodules("webview")
)

excludes = [
    "torch", "tensorflow", "torchvision", "keras", "pandas", "numpy",
    "scipy", "matplotlib", "cv2", "transformers", "onnxruntime", "timm",
    "sympy", "hypothesis", "IPython", "jupyter", "notebook", "sklearn",
    "skimage", "jax", "cupy", "PIL", "fsspec", "av", "sentry_sdk",
    "psycopg2", "psycopg2_binary", "psycopg",
]

a_hub = Analysis(
    ["packaging/run_hub.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[(os.path.join(SPECPATH, "web", "dist"), "web/dist")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz_hub = PYZ(a_hub.pure)

exe_hub = EXE(
    pyz_hub,
    a_hub.scripts,
    exclude_binaries=True,
    name="mio-taskhub",
    console=False,
    disable_windowed_traceback=True,
    icon=None,
)

a_mcp = Analysis(
    ["packaging/run_mcp.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[],
    hiddenimports=mcp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz_mcp = PYZ(a_mcp.pure)

exe_mcp = EXE(
    pyz_mcp,
    a_mcp.scripts,
    exclude_binaries=True,
    name="mio-taskhub-mcp",
    console=True,
    disable_windowed_traceback=False,
    icon=None,
)

a_widget = Analysis(
    ["packaging/run_widget.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[(os.path.join(SPECPATH, "web", "public", "icon.ico"), "web/public")],
    hiddenimports=widget_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz_widget = PYZ(a_widget.pure)

exe_widget = EXE(
    pyz_widget,
    a_widget.scripts,
    exclude_binaries=True,
    name="mio-taskhub-widget",
    console=False,
    disable_windowed_traceback=True,
    icon=os.path.join(SPECPATH, "web", "public", "icon.ico"),
)

coll = COLLECT(
    exe_hub,
    exe_mcp,
    exe_widget,
    a_hub.binaries,
    a_mcp.binaries,
    a_widget.binaries,
    a_hub.datas,
    a_mcp.datas,
    a_widget.datas,
    name="mio-taskhub",
)
