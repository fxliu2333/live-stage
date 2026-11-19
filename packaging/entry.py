"""安装版入口：默认启动演播厅；带 --selftest 时只做打包自检。"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback


def selftest() -> int:
    lines: list[str] = []
    ok = True
    try:
        from app import paths
        lines.append(f"frozen={paths.FROZEN}")
        lines.append(f"bundle={paths.BUNDLE}")
        lines.append(f"data={paths.DATA}")
        for label, path in (
            ("control.html", paths.STATIC / "control.html"),
            ("stage.html", paths.STATIC / "stage.html"),
            ("app.ico", paths.assets_dir() / "app.ico"),
            ("AudioDeviceCmdlets.dll", paths.VENDOR / "AudioDeviceCmdlets.dll"),
        ):
            exists = path.exists()
            ok = ok and exists
            lines.append(("OK   " if exists else "FAIL ") + label)
    except Exception:
        ok = False
        lines.append("FAIL paths\n" + traceback.format_exc())

    for label, statement in (
        ("pywebview winforms", "import webview.platforms.winforms"),
        ("sounddevice+miniaudio", "import sounddevice, miniaudio"),
        ("websocket-client", "import websocket"),
        ("fastapi+uvicorn", "import fastapi, uvicorn"),
        ("app.main", "import app.main"),
    ):
        try:
            exec(statement, {})
            lines.append("OK   " + label)
        except Exception:
            ok = False
            lines.append("FAIL " + label + "\n" + traceback.format_exc())

    report = "\n".join(lines)
    out = os.path.join(tempfile.gettempdir(), "livestage-selftest.txt")
    try:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
    except OSError:
        pass
    if sys.stdout:
        print(report)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    from app.launcher import main as run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
