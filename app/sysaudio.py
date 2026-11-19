"""系统默认播放设备：查看与交还。

程序只在运行期间把声音写进虚拟声卡，系统默认输出应当始终保持扬声器。
装驱动或其他软件可能把默认输出改成 CABLE，这里负责把它改回来。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .paths import VENDOR

ROOT = Path(__file__).resolve().parent.parent
DLL = VENDOR / "AudioDeviceCmdlets.dll"

VIRTUAL_HINTS = ("cable", "vb-audio", "voicemeeter", "virtual", "ndi")

_LIST = "Get-AudioDevice -List | Select-Object Name,ID,Default | ConvertTo-Json -Compress"


def _powershell(body: str, timeout: int = 25) -> str:
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        f"Import-Module '{DLL}' -Force;{body}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or b"").decode("utf-8", "ignore").strip()
        raise RuntimeError(message or "音频设备操作失败")
    return (result.stdout or b"").decode("utf-8", "ignore").strip()


def is_virtual(name: str) -> bool:
    return any(hint in (name or "").lower() for hint in VIRTUAL_HINTS)


def list_playback() -> list[dict]:
    if not DLL.exists():
        raise RuntimeError("缺少 vendor/AudioDeviceCmdlets.dll")
    raw = _powershell(_LIST)
    if not raw:
        return []
    data = json.loads(raw)
    items = data if isinstance(data, list) else [data]
    return [
        {"name": item.get("Name", ""), "id": item.get("ID", ""),
         "default": bool(item.get("Default")), "virtual": is_virtual(item.get("Name", ""))}
        for item in items
        # 只有 {0.0.0.*} 是播放端点，{0.0.1.*} 是录音端点
        if isinstance(item, dict) and str(item.get("ID", "")).startswith("{0.0.0.")
    ]


def status() -> dict:
    try:
        devices = list_playback()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "devices": []}
    current = next((d for d in devices if d["default"]), None)
    return {
        "ok": True,
        "default": current["name"] if current else "",
        "virtual_default": bool(current and current["virtual"]),
        "devices": devices,
    }


def release_virtual_default() -> dict:
    """如果系统默认输出是虚拟声卡，改回第一个真实设备。"""
    try:
        devices = list_playback()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    current = next((d for d in devices if d["default"]), None)
    if current is None or not current["virtual"]:
        return {"ok": True, "changed": False, "default": current["name"] if current else ""}

    target = next((d for d in devices if not d["virtual"]), None)
    if target is None:
        return {"ok": False, "error": "没有可用的物理播放设备"}

    try:
        _powershell(f"Set-AudioDevice -ID '{target['id']}' | Out-Null")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "changed": True, "default": target["name"],
            "previous": current["name"]}
