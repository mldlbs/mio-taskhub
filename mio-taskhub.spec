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

all_hiddenimports = (
    hiddenimports
    + ["httpx", "mcp", "mcp.server.fastmcp", "pydantic",
       "webview", "clr", "pythonnet", "pystray", "PIL", "PIL.Image",
       "PIL._imaging", "PIL._imagingft"]
    + collect_submodules("mcp")
    + collect_submodules("webview")
    + collect_submodules("pystray")
    + collect_submodules("PIL")
)

excludes = [
    "torch", "tensorflow", "torchvision", "keras", "pandas", "numpy",
    "scipy", "matplotlib", "cv2", "transformers", "onnxruntime", "timm",
    "sympy", "hypothesis", "IPython", "jupyter", "notebook", "sklearn",
    "skimage", "jax", "cupy", "PIL", "fsspec", "av", "sentry_sdk",
    "psycopg2", "psycopg2_binary", "psycopg",
]

widget_excludes = [x for x in excludes if x != "PIL"]

a_hub = Analysis(
    ["packaging/run.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        (os.path.join(SPECPATH, "web", "dist"), "web/dist"),
        (os.path.join(SPECPATH, "web", "public", "icon.ico"), "web/public"),
    ],
    hiddenimports=all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=widget_excludes,
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
    icon=os.path.join(SPECPATH, "web", "public", "icon.ico"),
)

coll = COLLECT(
    exe_hub,
    a_hub.binaries,
    a_hub.datas,
    name="mio-taskhub",
)
