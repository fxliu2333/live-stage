"""本机音频：设备枚举、测试音、系统语音合成。

这一层只负责把声音送到指定输出设备（通常是虚拟声卡的 CABLE Input），
不参与弹幕与话术逻辑。
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
import threading
import time
import wave
from array import array
from collections import deque
from pathlib import Path

import sounddevice as sd
import miniaudio

from .paths import DATA

TTS_CACHE = DATA / "tts_cache"
TTS_CACHE.mkdir(parents=True, exist_ok=True)
RECORDINGS = DATA / "recordings"

# 名称里带这些关键字的设备视为虚拟声卡，排到列表最前面
VIRTUAL_HINTS = ("cable", "vb-audio", "voicemeeter", "virtual", "ndi")
# 只有 VB-Cable 这一类才是真正的「播放端 -> 录音端」回环，ToDesk 等虚拟声卡不能当成它用
CABLE_HINTS = ("cable input", "cable in", "vb-audio virtual cable")
# WDM-KS 通道数与占用要求最苛刻，放到最后尝试
HOST_PRIORITY = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2, "Windows WDM-KS": 9}

_last: dict = {}
# 每次开始新播放都递增；旧播放看到编号变化会立即停下，避免两段声音叠在一起
_play_serial = 0
_serial_lock = threading.Lock()
_active_plays = 0
_play_log: deque = deque(maxlen=20)


def play_log() -> list[dict]:
    return list(_play_log)


def _log_play(entry: dict) -> None:
    _play_log.append(entry)


def active_plays() -> int:
    with _serial_lock:
        return _active_plays


def _enter_play() -> None:
    global _active_plays
    with _serial_lock:
        _active_plays += 1


def _leave_play() -> None:
    global _active_plays
    with _serial_lock:
        _active_plays = max(0, _active_plays - 1)


def stop_playback() -> None:
    """打断当前正在播的音频。"""
    global _play_serial
    with _serial_lock:
        _play_serial += 1


def _begin_playback() -> int:
    global _play_serial
    with _serial_lock:
        _play_serial += 1
        return _play_serial


def _current_serial() -> int:
    with _serial_lock:
        return _play_serial


# 同一路输出同时只允许一条流在写：两段音频叠在一起会听成颤音
_line_lock = threading.Lock()


def monitor_device() -> str:
    """监听目标必须是物理设备，绝不能是虚拟声卡，否则会把声音重复灌进麦克风。"""
    try:
        for device in list_outputs()["devices"]:
            if not device["virtual"]:
                return device["name"]
    except Exception:
        pass
    return ""

PS_TTS = r"""
Add-Type -AssemblyName System.Speech
$sp = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  48000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
$sp.SetOutputToWaveFile($env:STAGE_TTS_PATH, $fmt)
$sp.Rate = 0
$sp.Volume = 100
$sp.Speak($env:STAGE_TTS_TEXT)
$sp.Dispose()
"""


def _record(kind: str, result: dict) -> dict:
    _last.clear()
    _last.update({"kind": kind, "at": time.time(), **result})
    return result


def last_result() -> dict:
    return dict(_last)


def _output_devices() -> list[dict]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    try:
        default_out = sd.default.device[1]
    except Exception:
        default_out = -1

    items = []
    for index, dev in enumerate(devices):
        channels = int(dev.get("max_output_channels") or 0)
        if channels <= 0:
            continue
        name = dev.get("name", f"device {index}")
        host = hostapis[dev["hostapi"]]["name"]
        # WDM-KS 不支持阻塞式写入，选它必然播不出声，直接从列表里隐藏
        if host == "Windows WDM-KS":
            continue
        items.append({
            "index": index,
            "name": name,
            "host_api": host,
            "channels": channels,
            "samplerate": int(dev.get("default_samplerate") or 0),
            "is_default": index == default_out,
            "virtual": any(hint in name.lower() for hint in VIRTUAL_HINTS),
            "cable": any(hint in name.lower() for hint in CABLE_HINTS),
            "priority": HOST_PRIORITY.get(host, 5),
        })
    return items


def list_outputs() -> dict:
    """列出所有可写音频设备，虚拟声卡排在前面。"""
    try:
        items = _output_devices()
    except Exception as exc:  # 驱动异常时不让整个服务起不来
        return {"devices": [], "virtual_cable": False, "virtual_any": False, "error": str(exc)}

    items.sort(key=lambda d: (not d["virtual"], "16ch" in d["name"].lower(), d["priority"], d["name"]))
    return {
        "devices": items,
        "virtual_cable": any(d["cable"] for d in items),
        "virtual_any": any(d["virtual"] for d in items),
        "error": None,
    }


def preferred_output() -> str:
    """默认优先用 VB-Cable，其次才是其他虚拟声卡。"""
    try:
        devices = list_outputs()["devices"]
        for device in devices:
            if device["cable"] and "16ch" not in device["name"].lower():
                return device["name"]
        for device in devices:
            if device["virtual"] and "16ch" not in device["name"].lower():
                return device["name"]
    except Exception:
        pass
    return ""


def _candidates(name: str | None) -> list[dict]:
    devices = _output_devices()
    # 没有明确指定时，优先系统默认设备；默认设备不可用时退回物理设备，绝不默默选虚拟声卡
    fallback = ([d for d in devices if d["is_default"]]
                or [d for d in devices if not d["virtual"]]
                or sorted(devices, key=lambda d: d["priority"])[:1])
    if not name:
        return fallback

    wanted = name.strip().lower()
    exact = [d for d in devices if d["name"].strip().lower() == wanted]
    pool = exact or [d for d in devices if wanted in d["name"].lower()]
    pool.sort(key=lambda d: d["priority"])
    # 配置里保存的设备已不存在时，回退到系统默认设备
    return pool or fallback


def _all_candidates(name: str | None) -> list[dict]:
    """首选设备打不开时，退到同一个虚拟线上的其他端点（如 CABLE In 16ch）。"""
    primary = _candidates(name)
    if not name:
        return primary
    wanted = name.strip().lower()
    if not any(hint in wanted for hint in VIRTUAL_HINTS):
        return primary
    seen = {d["index"] for d in primary}
    extras = [d for d in _output_devices() if d["index"] not in seen and d["virtual"]]
    extras.sort(key=lambda d: (d["priority"], "16ch" not in d["name"].lower()))
    return primary + extras


def _open_stream(candidates: list[dict], samplerate: int):
    """按设备实际能力打开输出流；同一设备名会依次尝试不同宿主 API。"""
    errors: list[str] = []
    for candidate in candidates:
        channels = candidate["channels"]
        rates = []
        for rate in (samplerate, candidate["samplerate"], 48000, 44100):
            if rate and rate not in rates:
                rates.append(rate)
        for rate in rates:
            try:
                sd.check_output_settings(device=candidate["index"], samplerate=rate,
                                         channels=channels, dtype="int16")
                stream = sd.RawOutputStream(
                    device=candidate["index"], samplerate=rate, channels=channels,
                    dtype="int16", blocksize=0, latency="high",
                )
                stream.start()
                return stream, rate, candidate
            except Exception as exc:
                errors.append(f"{candidate['name']} [{candidate['host_api']}] {rate}Hz: {exc}")
                continue
    raise RuntimeError(errors[-1] if errors else "没有可用的音频输出设备")


def _reinitialize() -> None:
    """PortAudio 的设备列表是进程启动时建好的；设备被其他程序重建后索引会失效。"""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def _open_with_retry(device_name: str | None, samplerate: int):
    try:
        return _open_stream(_all_candidates(device_name), samplerate)
    except RuntimeError as error:
        message = str(error)
        if "Invalid device" in message or "-9996" in message:
            _reinitialize()
            return _open_stream(_all_candidates(device_name), samplerate)
        raise


def _interleave(samples: array, in_channels: int, out_channels: int, gain: float) -> array:
    if out_channels == in_channels:
        return array("h", (int(v * gain) for v in samples))
    if in_channels == 1 and out_channels == 2:
        out = array("h")
        for value in samples:
            scaled = int(value * gain)
            out.append(scaled)
            out.append(scaled)
        return out

    out = array("h")
    for frame in range(len(samples) // in_channels):
        base = frame * in_channels
        for channel in range(out_channels):
            out.append(int(samples[base + channel] * gain) if channel < in_channels else 0)
    return out


def _trim_silence(samples, channels: int, rate: int, threshold: int = 260) -> array:
    """掐掉首尾的静音，段落之间就不会凭空多出一截空白。"""
    total = len(samples)
    step = max(1, channels)
    if total < step * 4:
        return samples
    start = 0
    while start + step <= total and max(abs(v) for v in samples[start:start + step]) < threshold:
        start += step
    end = total
    while end - step >= start and max(abs(v) for v in samples[end - step:end]) < threshold:
        end -= step
    if start >= end:
        return samples
    pad = int(rate * 0.05) * step
    start = max(0, start - pad)
    end = min(total, end + pad)
    return samples[start:end]


def play_tone(device_name: str | None, seconds: float = 2.0, frequency: int = 660,
              monitor: bool = False, report: bool = True) -> dict:
    """播放一段带淡入淡出的测试音，用来确认虚拟声卡链路。"""
    serial = _begin_playback() if report else _current_serial()
    if report:
        _enter_play()
        _line_lock.acquire()
    if monitor and report:
        target = monitor_device()
        if target:
            threading.Thread(target=play_tone,
                             args=(target, seconds, frequency, False, False), daemon=True).start()
    stream = None
    try:
        stream, rate, candidate = _open_with_retry(device_name, 48000)
        channels = candidate["channels"]
        total = int(rate * max(0.2, min(seconds, 10.0)))
        fade = max(1, int(rate * 0.05))
        block = max(1, rate // 20)
        written = 0
        while written < total:
            if _current_serial() != serial:
                break
            frames = min(block, total - written)
            chunk = array("h")
            for n in range(written, written + frames):
                env = min(1.0, n / fade, (total - n) / fade)
                sample = int(0.25 * env * 32767 * math.sin(2 * math.pi * frequency * n / rate))
                for _ in range(channels):
                    chunk.append(sample)
            stream.write(chunk.tobytes())
            written += frames
        result = {"ok": True, "device": candidate["name"], "host_api": candidate["host_api"],
                  "samplerate": rate, "channels": channels}
        return _record("tone", result) if report else result
    except Exception as exc:
        failure = {"ok": False, "error": str(exc)}
        return _record("tone", failure) if report else failure
    finally:
        if report:
            _leave_play()
            _line_lock.release()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


def synthesize_speech(text: str) -> Path | None:
    """用 Windows 内置语音合成一段 wav，仅用于本地链路联调。"""
    text = (text or "").strip()
    if not text:
        return None
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    path = TTS_CACHE / f"{digest}.wav"
    if path.exists() and path.stat().st_size > 1024:
        return path

    env = dict(os.environ)
    env["STAGE_TTS_PATH"] = str(path)
    env["STAGE_TTS_TEXT"] = text[:400]
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_TTS],
            env=env, capture_output=True, timeout=25, check=False,
        )
    except Exception:
        return None
    return path if path.exists() and path.stat().st_size > 1024 else None


def _analyze(path: Path) -> dict:
    """对录音做三项体检：延迟副本、断续、音量抖动。"""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    mono = samples[0::channels] if channels > 1 else samples
    if len(mono) < rate:
        return {"ok": False, "error": "录音太短"}

    step = max(1, int(rate * 0.01))
    env = [max((abs(v) for v in mono[i:i + step]), default=0)
           for i in range(0, len(mono) - step, step)]
    loud = [value for value in env if value > 800]
    level = sorted(loud)[len(loud) // 2] if loud else 0

    dropouts = 0
    for index in range(3, len(env) - 3):
        if level and max(env[index - 3:index]) > level * 0.5 \
                and max(env[index + 1:index + 4]) > level * 0.5 \
                and env[index] < level * 0.12:
            dropouts += 1

    modulation = 0.0
    if level:
        for hz in (3, 4, 5, 6, 7, 8):
            re = sum(value * math.cos(2 * math.pi * hz * i / 100.0) for i, value in enumerate(env))
            im = sum(value * math.sin(2 * math.pi * hz * i / 100.0) for i, value in enumerate(env))
            modulation = max(modulation, math.hypot(re, im) / len(env) / level)

    window = mono[len(mono) // 3: len(mono) // 3 + int(rate * 0.5)]
    base = math.sqrt(sum(v * v for v in window)) or 1
    echo = 0.0
    for lag_ms in range(50, 401, 25):
        lag = int(rate * lag_ms / 1000)
        other = mono[len(mono) // 3 + lag: len(mono) // 3 + lag + len(window)]
        if len(other) < len(window):
            break
        den = base * (math.sqrt(sum(v * v for v in other)) or 1)
        echo = max(echo, abs(sum(x * y for x, y in zip(window, other))) / den)

    issues = []
    if echo > 0.35:
        issues.append(f"检测到延迟副本（相关系数 {echo:.2f}）")
    if dropouts > 8:
        issues.append(f"检测到 {dropouts} 处断续")
    if modulation > 0.35:
        issues.append(f"音量抖动偏大（{modulation * 100:.0f}%）")
    return {"ok": True, "clean": not issues, "issues": issues,
            "echo": round(echo, 3), "dropouts": dropouts,
            "modulation": round(modulation, 3)}


def record_line(seconds: float = 10.0) -> dict:
    """录制虚拟线本身的声音，用来判断问题出在程序还是下游。"""
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    picks = []
    for index, device in enumerate(devices):
        name = device.get("name", "")
        if int(device.get("max_input_channels") or 0) >= 2 and "cable" in name.lower():
            host = hostapis[device["hostapi"]]["name"]
            picks.append((HOST_PRIORITY.get(host, 5), index, name,
                          int(device.get("default_samplerate") or 48000)))
    if not picks:
        return {"ok": False, "error": "没有找到 CABLE Output 录音设备"}
    picks.sort()
    _, index, name, rate = picks[0]

    RECORDINGS.mkdir(parents=True, exist_ok=True)
    path = RECORDINGS / f"line-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    frames = []
    total = int(rate * max(2.0, min(seconds, 60.0)))
    got = 0
    try:
        with sd.RawInputStream(device=index, samplerate=rate, channels=2, dtype="int16",
                               latency="high") as stream:
            while got < total:
                size = min(4800, total - got)
                data, _ = stream.read(size)
                frames.append(data)
                got += size
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(frames))
    return {"ok": True, "path": str(path), "device": name,
            "seconds": round(total / rate, 1), "analysis": _analyze(path)}


def play_audio(path: Path, device_name: str | None, volume: float = 0.9,
               monitor: bool = False, report: bool = True) -> dict:
    """播放任意音频文件（wav / mp3），统一解码成 48k 立体声再送设备。"""
    serial = _begin_playback() if report else _current_serial()
    if report:
        _enter_play()
        _line_lock.acquire()
    started = time.time()
    if monitor and report:
        target = monitor_device()
        if target:
            threading.Thread(target=play_audio,
                             args=(path, target, round(volume * 0.8, 3), False, False),
                             daemon=True).start()
    stream = None
    try:
        stream, out_rate, candidate = _open_with_retry(device_name, 48000)
        decoded = miniaudio.decode_file(
            str(path), output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2, sample_rate=out_rate,
        )
        samples = _trim_silence(decoded.samples, 2, out_rate)
        if not len(samples):
            raise RuntimeError("音频内容为空")

        gain = max(0.0, min(volume, 1.0))
        data = _interleave(samples, 2, candidate["channels"], gain)
        block = max(1, out_rate // 10) * candidate["channels"]
        interrupted = False
        for start in range(0, len(data), block):
            if _current_serial() != serial:
                interrupted = True
                break
            stream.write(data[start:start + block].tobytes())
        result = {"ok": True, "device": candidate["name"], "host_api": candidate["host_api"],
                  "samplerate": out_rate, "channels": candidate["channels"],
                  "interrupted": interrupted,
                  "seconds": round(len(samples) / 2 / out_rate, 2)}
        if report:
            _log_play({"kind": "audio", "start": started, "end": time.time(),
                       "seconds": result["seconds"], "device": result["device"],
                       "file": path.name})
        return _record("audio", result) if report else result
    except Exception as exc:
        failure = {"ok": False, "error": str(exc)}
        if report:
            _log_play({"kind": "audio", "start": started, "end": time.time(),
                       "error": str(exc)[:120], "file": path.name})
        return _record("audio", failure) if report else failure
    finally:
        if report:
            _leave_play()
            _line_lock.release()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

