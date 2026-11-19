"""启动本地服务并打开「控制台 + 演播窗口」两个窗口。

演播窗口是给抖音直播伴侣做窗口捕获用的：
无边框、固定 9:16、不可拉伸，客户区正好是可以被捕获的画面。
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import threading
import time
from ctypes import wintypes
from urllib.request import urlopen

import uvicorn
import webview

from . import sysaudio
from .main import app, RUNTIME_INFO

HOST = "127.0.0.1"
STAGE_HEIGHT_LIMIT = 960
STAGE_WIDTH_LIMIT = 540


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def _work_area() -> tuple[int, int]:
    try:
        rect = _Rect()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        return 1920, 1040


def _dpi_scale() -> float:
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def stage_size() -> tuple[int, int]:
    """按屏幕可用高度取最大的 9:16 尺寸，保证窗口不会被屏幕截断。"""
    _, work_height = _work_area()
    scale = max(1.0, _dpi_scale())
    max_logical = int((work_height - 96) / scale)
    height = min(STAGE_HEIGHT_LIMIT, max(640, max_logical))
    height -= height % 2
    width = int(round(height * 9 / 16))
    width -= width % 2
    return width, height


def pick_port(start: int = 8770) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    raise SystemExit("8770-8789 端口都被占用，请先关闭占用程序")


def instance_running(port: int = 8770) -> bool:
    """已经开着一份程序时不要再开第二份，否则两路声音会叠在一起。"""
    try:
        with urlopen(f"http://{HOST}:{port}/api/health", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def main() -> None:
    if instance_running():
        print("直播演播厅已经在运行，请直接从任务栏切回已打开的窗口。")
        print("如果确实要重开，请先关掉现有窗口再启动。")
        time.sleep(3)
        return

    port = pick_port()
    width, height = stage_size()
    RUNTIME_INFO.update(stage_width=width, stage_height=height, port=port)

    # 启动时如果系统输出还停在虚拟声卡上（装驱动或误选导致），先把声音交还扬声器
    threading.Thread(target=sysaudio.release_virtual_default, daemon=True).start()

    # 窗口捕获兼容性：WebView2 开 GPU 合成时部分采集端会黑屏，
    # 需要软渲染时把 STAGE_SOFTWARE_RENDER 设为 1（默认开启）。
    if os.getenv("STAGE_SOFTWARE_RENDER", "1") == "1":
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--disable-gpu"

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise SystemExit("本地服务启动失败")

    base = f"http://{HOST}:{port}"
    screen_width, _ = _work_area()

    control = webview.create_window(
        "直播演播厅 · 控制台", f"{base}/control",
        width=min(1040, screen_width - 120), height=760,
        x=48, y=48, resizable=True, min_size=(900, 640),
    )
    stage = webview.create_window(
        "STAGE", f"{base}/stage",
        width=width, height=height,
        x=max(48, screen_width - width - 48), y=40,
        frameless=True, easy_drag=True, resizable=False,
    )

    print(f"控制台 {base}/control")
    print(f"演播窗口 {base}/stage  ·  {width}x{height}")
    print("直播伴侣里用「窗口捕获」选 STAGE，麦克风选 CABLE Output")

    # 关掉控制台就一起关掉演播窗口，避免留下一个无法关闭的无边框窗口
    def close_stage():
        try:
            stage.destroy()
        except Exception:
            pass

    control.events.closed += close_stage

    webview.start(debug=False)

    # 退出时把系统默认输出交还扬声器，不把虚拟声卡留在系统里
    try:
        sysaudio.release_virtual_default()
    except Exception:
        pass
    server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
