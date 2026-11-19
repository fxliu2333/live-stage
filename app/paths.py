"""运行时的目录约定。

开发时所有东西都在项目目录里；打包成安装版后，程序本体是只读的，
配置、语音缓存、浏览器登录态这些可写数据统一放到 %LOCALAPPDATA%\\LiveStage。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller 会把 datas 解到 _MEIPASS（onedir 模式下就是 _internal）
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    BUNDLE = Path(__file__).resolve().parent.parent


def _candidates() -> list[Path]:
    if not FROZEN:
        return [Path(__file__).resolve().parent.parent / "runtime"]
    items: list[Path] = []
    for base in (os.getenv("LOCALAPPDATA"), os.getenv("APPDATA")):
        if base:
            items.append(Path(base) / "LiveStage")
    items.append(Path(tempfile.gettempdir()) / "LiveStage")
    return items


def _pick() -> Path:
    """优先用户目录；都被策略禁止时退回临时目录，至少能跑起来。"""
    for path in _candidates():
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "LiveStage"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


if not FROZEN:
    BUNDLE = Path(__file__).resolve().parent.parent

DATA = _pick()

STATIC = BUNDLE / "app" / "static"
VENDOR = BUNDLE / "vendor"


def assets_dir() -> Path:
    """图标和上传的素材：安装版放数据目录，开发时仍是项目的 assets。"""
    target = (DATA / "assets") if FROZEN else (BUNDLE / "assets")
    target.mkdir(parents=True, exist_ok=True)
    bundled = BUNDLE / "assets"
    if bundled.is_dir():
        for item in bundled.iterdir():
            copy = target / item.name
            if item.is_file() and not copy.exists():
                try:
                    shutil.copy2(item, copy)
                except OSError:
                    pass
    return target
