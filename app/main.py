"""直播演播厅 · 本地联调服务（P0）。

只在本机 127.0.0.1 上跑，不连云端、不写业务库，
用来验证「窗口捕获 + 虚拟声卡 + 字幕」这条链路。
"""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import audio
from . import comments
from . import paths
from . import script
from . import sysaudio
from .cloud import CloudError, cloud

STATIC_DIR = paths.STATIC
ASSETS_DIR = paths.assets_dir()
RUNTIME_DIR = paths.DATA
CONFIG_PATH = RUNTIME_DIR / "config.json"

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}
DEFAULT_CONFIG = {
    "stage": {"video": "", "camera": "", "main": "camera", "overlay": "none",
              "pip": {"corner": "br", "size": "medium"}},
    "audio": {"output_device": "", "reply_volume": 0.9, "monitor": False},
    "subtitle": {"duration_ms": 7000, "font_size": 30},
    "cloud": {"base_url": "https://live-staging.tuoxiaoxi.com", "client_version": "1.0.1"},
    "live": {"room_url": "", "filler": "嗯，我看一下哈"},
}
RUNTIME_INFO: dict = {"stage_width": 540, "stage_height": 960, "port": 0}

_lock = threading.Lock()
_subscribers: set[asyncio.Queue] = set()
_output_initialized = False
_loop: asyncio.AbstractEventLoop | None = None


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config() -> dict:
    global _output_initialized
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            _deep_merge(config, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    if config["stage"]["video"] and not (ASSETS_DIR / config["stage"]["video"]).exists():
        config["stage"]["video"] = ""

    # 首次启动自动选中虚拟声卡，省掉手动挑设备这一步
    if not config["audio"]["output_device"] and not _output_initialized:
        _output_initialized = True
        preferred = audio.preferred_output()
        if preferred:
            config["audio"]["output_device"] = preferred
            save_config(config)
    return config


def save_config(config: dict) -> dict:
    with _lock:
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def _dispatch(event: str, data: dict) -> None:
    for queue in list(_subscribers):
        queue.put_nowait({"event": event, "data": data})


def publish(event: str, data: dict) -> None:
    """允许从工作线程推送事件。"""
    loop = _loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(_dispatch, event, data)
    else:
        _dispatch(event, data)


@asynccontextmanager
async def lifespan(_app):
    global _loop
    _loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="live-stage", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
script.configure(publish)
comments.configure(publish)


@app.get("/")
def index():
    return RedirectResponse("/control")


@app.get("/stage")
def stage_page():
    return FileResponse(STATIC_DIR / "stage.html", headers={"Cache-Control": "no-store"})


@app.get("/control")
def control_page():
    return FileResponse(STATIC_DIR / "control.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"ok": True, **RUNTIME_INFO}


@app.get("/api/runtime")
def runtime_info():
    config = load_config()
    return {
        **RUNTIME_INFO,
        "assets": sorted(p.name for p in ASSETS_DIR.iterdir() if p.is_file()),
        "video": config["stage"]["video"],
        "config": config,
    }


@app.post("/api/stage/metrics")
def stage_metrics(payload: dict):
    """演播页上报真实绘制尺寸，用来确认窗口捕获的分辨率。"""
    RUNTIME_INFO["stage_paint"] = {
        "width": int(payload.get("width") or 0),
        "height": int(payload.get("height") or 0),
        "dpr": float(payload.get("dpr") or 1),
        "css_width": int(payload.get("css_width") or 0),
        "css_height": int(payload.get("css_height") or 0),
    }
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def update_config(patch: dict):
    config = save_config(_deep_merge(load_config(), patch))
    publish("config", config)
    return config


@app.get("/api/audio/devices")
def audio_devices():
    return audio.list_outputs()


@app.get("/api/audio/status")
def audio_status():
    return {**audio.last_result(), "active": audio.active_plays(), "log": audio.play_log()}


@app.get("/api/audio/system")
def audio_system():
    return sysaudio.status()


@app.post("/api/audio/system/release")
def audio_system_release():
    return sysaudio.release_virtual_default()


@app.post("/api/audio/record")
def audio_record(payload: dict | None = None):
    seconds = float((payload or {}).get("seconds") or 10)
    return audio.record_line(seconds)


@app.post("/api/audio/test-tone")
def test_tone(payload: dict | None = None):
    payload = payload or {}
    config = load_config()
    device = payload.get("device") or config["audio"]["output_device"]
    seconds = float(payload.get("seconds") or 2.0)
    threading.Thread(
        target=audio.play_tone,
        args=(device, seconds, 660, bool(config["audio"].get("monitor", True))),
        daemon=True,
    ).start()
    return {"ok": True, "device": device, "seconds": seconds}


@app.post("/api/stage/reply")
def stage_reply(payload: dict):
    """推一条回复：立刻出字幕，语音优先走测试端云端音色。"""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="回复内容为空")

    config = load_config()
    nickname = (payload.get("nickname") or "观众").strip() or "观众"
    publish("subtitle", {"nickname": nickname, "text": text,
                         "duration_ms": config["subtitle"]["duration_ms"]})

    device = config["audio"]["output_device"]
    volume = config["audio"]["reply_volume"]
    monitor = bool(config["audio"].get("monitor", True))
    use_cloud = cloud.status()["logged_in"] and payload.get("source") != "local"

    if use_cloud:
        def run_cloud():
            try:
                publish("reply_state", {"state": "synthesizing", "text": text})
                path = cloud.synthesize(text)
                publish("reply_state", {"state": "playing", "text": text})
                result = audio.play_audio(path, device, volume, monitor)
                publish("reply_state", {"state": "done" if result.get("ok") else "error",
                                        "text": text, "detail": result.get("error", "")})
            except CloudError as error:
                publish("reply_state", {"state": "error", "text": text,
                                        "detail": f"{error.code}: {error.message}"})
            except Exception as error:  # 兜底，避免线程静默死掉
                publish("reply_state", {"state": "error", "text": text, "detail": str(error)})

        threading.Thread(target=run_cloud, daemon=True).start()
        return {"ok": True, "audio": "cloud", "notice": "已提交测试端云端合成"}

    wav = audio.synthesize_speech(text)
    if wav is not None:
        threading.Thread(
            target=audio.play_audio, args=(wav, device, volume, monitor), daemon=True
        ).start()
        return {"ok": True, "audio": "speech", "notice": "未登录测试端，使用本机语音"}

    threading.Thread(target=audio.play_tone, args=(device, 1.2, 880), daemon=True).start()
    return {"ok": True, "audio": "tone", "notice": "系统语音合成不可用，已改用测试音"}


@app.get("/api/cloud/status")
def cloud_status():
    status = cloud.status()
    if not status["base_url"]:
        status["base_url"] = load_config()["cloud"]["base_url"]
    return status


@app.post("/api/cloud/login")
def cloud_login(payload: dict):
    phone = (payload.get("phone") or "").strip()
    password = payload.get("password") or ""
    config = load_config()
    base_url = (payload.get("base_url") or config["cloud"]["base_url"]).strip()
    client_version = (payload.get("client_version") or config["cloud"]["client_version"]).strip()
    if not phone or not password:
        raise HTTPException(status_code=400, detail="请填写手机号和密码")
    try:
        status = cloud.login(base_url, phone, password, client_version)
    except CloudError as error:
        raise HTTPException(status_code=401 if error.status == 401 else 400,
                            detail=f"{error.code}: {error.message}") from None
    config["cloud"] = {"base_url": status["base_url"], "client_version": client_version}
    save_config(config)
    return status


@app.post("/api/cloud/logout")
def cloud_logout():
    return cloud.logout()


@app.post("/api/cloud/refresh")
def cloud_refresh():
    try:
        return cloud.refresh()
    except CloudError as error:
        raise HTTPException(status_code=400, detail=f"{error.code}: {error.message}") from None


@app.post("/api/cloud/project")
def cloud_project(payload: dict):
    try:
        return cloud.select_project((payload.get("project_id") or "").strip())
    except CloudError as error:
        raise HTTPException(status_code=400, detail=f"{error.code}: {error.message}") from None


PRODUCT_FIELDS = ("logistics", "after_sales", "specs", "target_audience",
                  "promotion_info", "usage_scenarios", "selling_points", "notes")


@app.get("/api/cloud/product")
def cloud_product():
    try:
        return cloud.product()
    except CloudError as error:
        raise HTTPException(status_code=400, detail=f"{error.code}: {error.message}") from None


@app.post("/api/cloud/product")
def cloud_save_product(payload: dict):
    fields = {name: str(payload.get(name) or "") for name in PRODUCT_FIELDS if name in payload}
    if not fields:
        raise HTTPException(status_code=400, detail="没有要保存的字段")
    try:
        product = cloud.update_product(fields, payload.get("version"))
    except CloudError as error:
        raise HTTPException(status_code=400, detail=f"{error.code}: {error.message}") from None
    return {"ok": True, "product": product}


@app.get("/api/script")
def script_status():
    return script.status()


@app.post("/api/script/import")
def script_import(payload: dict):
    text = payload.get("text") or ""
    name = (payload.get("name") or "").strip()
    try:
        return script.save(text, name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@app.post("/api/script/upload")
async def script_upload(request: Request, name: str = ""):
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 2MB")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = body.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="只支持 UTF-8 或 GBK 文本") from None
    try:
        return script.save(text, Path(name).name or "粘贴文本")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@app.delete("/api/script")
def script_clear():
    return script.clear()


@app.post("/api/script/prepare")
def script_prepare():
    try:
        return script.prepare()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@app.post("/api/script/start")
def script_start(payload: dict | None = None):
    payload = payload or {}
    config = load_config()
    try:
        return script.start(
            config["audio"]["output_device"],
            config["audio"]["reply_volume"],
            bool(config["audio"].get("monitor", True)),
            bool(payload.get("loop", False)),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@app.post("/api/script/stop")
def script_stop():
    return script.stop()


@app.get("/api/live/status")
def live_status():
    return comments.live_comments.status()


@app.post("/api/live/start")
def live_start(payload: dict):
    room_url = (payload.get("room_url") or "").strip()
    config = load_config()
    filler = (payload.get("filler") or config["live"].get("filler") or "").strip()
    try:
        status = comments.live_comments.start(room_url, bool(payload.get("auto_reply")), filler)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    config["live"] = {"room_url": room_url, "filler": filler}
    save_config(config)
    return status


@app.post("/api/live/stop")
def live_stop():
    return comments.live_comments.stop()


@app.post("/api/live/probe")
def live_probe(payload: dict):
    return comments.probe((payload.get("room_url") or "").strip())


@app.post("/api/live/auto-reply")
def live_auto_reply(payload: dict):
    return comments.live_comments.set_auto_reply(bool(payload.get("enabled")))


@app.get("/api/events")
async def events():
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                payload = json.dumps(message["data"], ensure_ascii=False)
                yield f"event: {message['event']}\ndata: {payload}\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/assets/video")
async def upload_video(request: Request, name: str = ""):
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 mp4 / webm / mov")

    # 边收边写盘，避免几分钟的大文件整段塞进内存
    limit = 4 * 1024 * 1024 * 1024
    filename = f"loop{suffix}"
    target = ASSETS_DIR / filename
    temp = ASSETS_DIR / f".upload{suffix}"
    size = 0
    try:
        with temp.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="文件超过 4GB")
                handle.write(chunk)
    except HTTPException:
        temp.unlink(missing_ok=True)
        raise
    except Exception as error:
        temp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"写入失败：{error}") from None

    if size == 0:
        temp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="文件为空")

    temp.replace(target)
    config = load_config()
    config["stage"]["video"] = filename
    save_config(config)
    publish("config", config)
    return {"ok": True, "video": filename, "size": size}


@app.delete("/api/assets/video")
def clear_video():
    config = load_config()
    name = config["stage"]["video"]
    config["stage"]["video"] = ""
    save_config(config)
    if name:
        try:
            (ASSETS_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass
    publish("config", config)
    return {"ok": True}
