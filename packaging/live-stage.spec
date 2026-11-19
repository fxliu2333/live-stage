# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

PACKAGING = Path(SPECPATH).resolve()
PROJECT = PACKAGING.parent

hidden = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "pythonnet",
    "clr_loader",
    "clr_loader.util",
    "clr_loader.netfx",
    "websocket",
    "miniaudio",
    "sounddevice",
]

a = Analysis(
    [str(PACKAGING / "entry.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[
        (str(PROJECT / "app" / "static"), "app/static"),
        (str(PROJECT / "assets"), "assets"),
        (str(PROJECT / "vendor"), "vendor"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveStage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(PROJECT / "assets" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LiveStage",
)
