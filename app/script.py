"""讲解文案：切分、保存、逐段合成播放。"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import audio
from .cloud import CloudError, cloud, forget, library_entries, remember
from .paths import DATA

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = DATA
SCRIPT_FILE = RUNTIME / "script.json"
MAX_SEGMENT = 120

_lock = threading.RLock()
_stop = threading.Event()
_generation = 0
_state = {"segments": [], "source": "", "index": -1, "playing": False,
          "preparing": False, "error": ""}
_publish = None


def configure(publish) -> None:
    global _publish
    _publish = publish


def _emit(event: str, data: dict) -> None:
    if _publish is not None:
        try:
            _publish(event, data)
        except Exception:
            pass


def _load_file() -> dict:
    if not SCRIPT_FILE.exists():
        return {"segments": [], "source": ""}
    try:
        data = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"segments": [], "source": ""}
    segments = [str(item) for item in data.get("segments", []) if str(item).strip()]
    return {"segments": segments, "source": data.get("source", "")}


def _init() -> None:
    data = _load_file()
    if data["segments"]:
        _state["segments"] = data["segments"]
        _state["source"] = data["source"]
        # 老版本下载过、但还没进语音库的段落，这里补登记一次
        for segment in data["segments"]:
            cached = cloud.cached(segment)
            if cached is not None:
                remember(segment, cached)
        return
    # 文案文件丢了（清空或换机器）时，用本地已下载的语音把段落恢复出来
    entries = library_entries()
    if entries:
        _state["segments"] = [entry["text"] for entry in entries]
        _state["source"] = "本地语音库"
        try:
            SCRIPT_FILE.write_text(
                json.dumps({"segments": _state["segments"], "source": "本地语音库"},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


_init()


def split_text(text: str, limit: int = MAX_SEGMENT) -> list[str]:
    """按空行分块，块内再按句号切分，保证每段不超过上限。"""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if not blocks:
        return []

    segments: list[str] = []
    for block in blocks:
        if len(block) <= limit:
            segments.append(block)
            continue
        buffer = ""
        for part in re.split(r"(?<=[。！？；!?;])", block):
            part = part.strip()
            if not part:
                continue
            if len(buffer) + len(part) <= limit:
                buffer += part
            else:
                if buffer:
                    segments.append(buffer)
                buffer = part
        if buffer:
            segments.append(buffer)
    return [segment for segment in segments if segment]


def save(text: str, source: str = "") -> dict:
    segments = split_text(text)
    if not segments:
        raise ValueError("文案为空")
    with _lock:
        _state["segments"] = segments
        _state["source"] = source
        _state["index"] = -1
        _state["error"] = ""
        RUNTIME.mkdir(parents=True, exist_ok=True)
        SCRIPT_FILE.write_text(
            json.dumps({"segments": segments, "source": source}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return status()


def clear() -> dict:
    with _lock:
        stop()
        forget(_state["segments"])
        _state.update({"segments": [], "source": "", "index": -1, "error": ""})
        try:
            SCRIPT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return status()


def status() -> dict:
    segments = list(_state["segments"])
    return {
        "segments": segments,
        "source": _state["source"],
        "index": _state["index"],
        "playing": _state["playing"],
        "preparing": _state["preparing"],
        "cached": [bool(cloud.cached(segment)) for segment in segments],
        "error": _state["error"],
    }


def prepare() -> dict:
    """把还没合成过的段落一次性下载到本地，播放时就完全离线。"""
    global _generation
    with _lock:
        if _state["playing"]:
            raise ValueError("正在讲解中，先停止再下载")
        if _state["preparing"]:
            return status()
        if not _state["segments"]:
            raise ValueError("还没有导入文案")
        if not cloud.status().get("logged_in"):
            raise ValueError("请先登录测试端，讲解音色来自云端")
        _stop.clear()
        _generation += 1
        _state["preparing"] = True
        _state["error"] = ""
        threading.Thread(target=_prepare_run, args=(_generation,), daemon=True).start()
    return status()


def _prepare_run(generation: int) -> None:
    segments = list(_state["segments"])
    total = len(segments)
    try:
        for index, segment in enumerate(segments):
            if generation != _generation or _stop.is_set():
                return
            if cloud.cached(segment) is not None:
                _emit("script_state", {"state": "preparing", "index": index, "total": total,
                                       "skipped": True})
                continue
            _emit("script_state", {"state": "preparing", "index": index, "total": total,
                                   "skipped": False})
            try:
                cloud.synthesize(segment)
            except CloudError as error:
                _state["error"] = f"{error.code}: {error.message}"
                _emit("script_state", {"state": "error", "index": index,
                                       "detail": _state["error"]})
                return
            # 云端对动态合成有频率上限，隔一会儿再发下一段，减少被限流
            time.sleep(1.2)
        _emit("script_state", {"state": "prepared", "total": total})
    finally:
        if generation == _generation:
            _state["preparing"] = False


def start(device: str, volume: float, monitor: bool, loop: bool = False) -> dict:
    global _generation
    with _lock:
        if _state["playing"]:
            return status()
        if not _state["segments"]:
            raise ValueError("还没有导入文案")
        if not cloud.status().get("logged_in"):
            raise ValueError("请先登录测试端，讲解音色来自云端")
        _stop.clear()
        _generation += 1
        _state["playing"] = True
        _state["index"] = -1
        _state["error"] = ""
        threading.Thread(target=_run, args=(_generation, device, volume, monitor, loop),
                         daemon=True).start()
    return status()


def stop() -> dict:
    global _generation
    _generation += 1
    _stop.set()
    _state["playing"] = False
    _state["preparing"] = False
    _state["index"] = -1
    audio.stop_playback()
    return status()


def _run(generation: int, device: str, volume: float, monitor: bool, loop: bool) -> None:
    def alive() -> bool:
        return generation == _generation and not _stop.is_set()

    def prefetch(segments: list[str]) -> None:
        """播放的同时按顺序把后面的段提前合成好，避免段落之间长时间空等。"""
        for segment in segments:
            if not alive():
                return
            if cloud.cached(segment) is not None:
                continue
            try:
                cloud.synthesize(segment)
            except CloudError as error:
                _state["error"] = f"{error.code}: {error.message}"
                _emit("script_state", {"state": "error", "detail": _state["error"]})
                return

    try:
        while alive():
            segments = list(_state["segments"])
            total = len(segments)
            # 第一段没缓存时也要先合成，后续段落同时预取
            threading.Thread(target=prefetch, args=(segments,), daemon=True).start()
            index = 0
            while alive() and index < total:
                segment = segments[index]
                _state["index"] = index
                cached = cloud.cached(segment) is not None
                _emit("script_state", {"state": "local" if cached else "synthesizing",
                                       "index": index, "total": total})
                _emit("subtitle", {"nickname": "", "text": segment, "duration_ms": 12000})
                # 正常情况下播上一段时已经合成完，这里几乎是立刻就有文件
                while alive() and cloud.cached(segment) is None:
                    if _state["error"]:
                        return
                    time.sleep(0.15)
                if not alive():
                    break
                path = cloud.cached(segment)
                if path is None:
                    return
                # 弹幕回复优先：当前有回复在播时先让路，别把人家打断
                while alive() and audio.active_plays() > 0:
                    time.sleep(0.1)
                if not alive():
                    break
                result = audio.play_audio(path, device, volume, monitor)
                if result.get("interrupted") and alive():
                    # 被弹幕回复插播打断：等回复播完，再把这一段完整念一遍
                    while alive() and audio.active_plays() > 0:
                        time.sleep(0.1)
                    time.sleep(0.2)
                    continue
                _emit("script_state", {"state": "playing", "index": index, "total": total})
                time.sleep(0.15)
                index += 1
            if not loop:
                break
            time.sleep(0.2)
    finally:
        if generation == _generation:
            _state["playing"] = False
            _state["index"] = -1
        _emit("script_state", {"state": "stopped", "total": len(_state["segments"])})
