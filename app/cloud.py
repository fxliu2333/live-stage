"""预发云端语音合成客户端。

只做三件事：登录测试账号、拿到账号音色、把一句话合成成音频。
不写业务数据，不走计费（回复任务本身不计积分）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .paths import DATA

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = DATA
CACHE = RUNTIME / "tts_cache"
SESSION_FILE = RUNTIME / "cloud_session.json"
CACHE.mkdir(parents=True, exist_ok=True)
LIBRARY_FILE = RUNTIME / "voice_library.json"
_library_lock = threading.Lock()


def _load_library() -> list[dict]:
    if not LIBRARY_FILE.exists():
        return []
    try:
        data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else data
    return [item for item in entries or [] if isinstance(item, dict) and item.get("text")]


def _save_library(entries: list[dict]) -> None:
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        LIBRARY_FILE.write_text(json.dumps({"entries": entries[-500:]}, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError:
        pass


def library_entries() -> list[dict]:
    """还留在本地磁盘上的语音条目，按合成时间排列。"""
    entries = []
    for entry in _load_library():
        path = CACHE / str(entry.get("file") or "")
        if path.exists() and path.stat().st_size > 1024:
            entries.append(entry)
    return entries


def remember(text: str, path: Path) -> None:
    text = (text or "").strip()
    if not text or not path.exists():
        return
    with _library_lock:
        entries = [item for item in _load_library() if item.get("text") != text]
        entries.append({"text": text, "file": path.name, "created": time.time()})
        _save_library(entries)


def forget(texts: list[str]) -> None:
    drop = {text.strip() for text in texts if text and text.strip()}
    if not drop:
        return
    with _library_lock:
        _save_library([item for item in _load_library() if item.get("text") not in drop])


class CloudError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def normalize_base(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise CloudError("INVALID_BASE", "请填写云端地址")
    parsed = urlsplit(raw)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise CloudError("INVALID_BASE", "云端地址必须是 https")
    if parsed.path.rstrip("/") in {"", "/"}:
        return raw + "/api/live/v1"
    return raw


class CloudSession:
    def __init__(self) -> None:
        self.base = ""
        self.client_version = "1.0.1"
        self.token = ""
        self.device_id = ""
        self.user: dict = {}
        self.projects: list[dict] = []
        self.project_id = ""
        self.voice: dict = {}
        self._run_id = ""
        self._task_id = ""
        self._lock = threading.RLock()
        self._restore()
        if self.token:
            # 恢复上次会话后后台补全账号资料，避免界面显示成空账号
            threading.Thread(target=self._resume, daemon=True).start()

    def _resume(self) -> None:
        try:
            self.refresh()
        except CloudError as error:
            if error.status in (401, 403):
                self.logout()
        except Exception:
            pass

    # ---------- 基础请求 ----------

    def _restore(self) -> None:
        if not SESSION_FILE.exists():
            return
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.base = data.get("base", "")
        self.client_version = data.get("client_version", self.client_version)
        self.token = data.get("token", "")
        self.device_id = data.get("device_id", "")
        self.user = data.get("user", {})
        self.project_id = data.get("project_id", "")

    def _persist(self) -> None:
        payload = {"base": self.base, "client_version": self.client_version,
                   "token": self.token, "device_id": self.device_id,
                   "user": self.user, "project_id": self.project_id}
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def request(self, method: str, path: str, body=None, idempotent: bool = False,
                extra_headers: dict | None = None) -> dict:
        if not self.base:
            raise CloudError("NO_CLOUD", "还没有配置云端地址")
        url = self.base + path
        headers = {
            "Accept": "application/json",
            "X-Client-Version": self.client_version,
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if extra_headers:
            headers.update(extra_headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotent:
            headers["Idempotency-Key"] = uuid4().hex

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=25) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as error:
            raw = error.read() if hasattr(error, "read") else b""
            raise self._error_from(raw, error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise CloudError("CLOUD_UNREACHABLE", f"连不上云端：{error}") from None

        if content_type.startswith("audio/") or content_type == "application/octet-stream":
            return {"_bytes": raw, "_content_type": content_type}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CloudError("BAD_RESPONSE", "云端响应格式异常") from None
        if not isinstance(value, dict):
            raise CloudError("BAD_RESPONSE", "云端响应格式异常")
        return value.get("data", value)

    @staticmethod
    def _error_from(raw: bytes, status: int) -> CloudError:
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception:
            return CloudError("HTTP_ERROR", f"云端返回 {status}", status)
        detail = value.get("detail", value)
        if isinstance(detail, dict):
            return CloudError(str(detail.get("code") or "HTTP_ERROR"),
                              str(detail.get("message") or f"云端返回 {status}"), status)
        if isinstance(detail, list) and detail:
            first = detail[0]
            message = first.get("msg") if isinstance(first, dict) else str(first)
            return CloudError("INVALID_INPUT", str(message), status)
        return CloudError("HTTP_ERROR", str(detail), status)

    # ---------- 登录与配置 ----------

    def _installation_id(self) -> str:
        path = RUNTIME / "installation.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))["installation_id"]
            except Exception:
                pass
        value = uuid4().hex
        RUNTIME.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"installation_id": value}), encoding="utf-8")
        return value

    def login(self, base_url: str, phone: str, password: str, client_version: str = "") -> dict:
        with self._lock:
            self.base = normalize_base(base_url)
            self.client_version = (client_version or self.client_version).strip() or "1.0.1"
            self.token = ""
            data = self.request("POST", "/auth/login", {
                "phone": phone.strip(),
                "password": password,
                "installation_id": self._installation_id(),
                "client_version": self.client_version,
            })
            self.token = data.get("access_token") or data.get("token") or ""
            self.device_id = data.get("device_id") or (data.get("device") or {}).get("id") or ""
            self.user = data.get("user") or {}
            self._run_id = ""
            self._task_id = ""
            self._persist()
            self.refresh()
            return self.status()

    def logout(self) -> dict:
        with self._lock:
            if self.token:
                try:
                    self.request("POST", "/auth/logout", {})
                except CloudError:
                    pass
            self.token = ""
            self.device_id = ""
            self.user = {}
            self.projects = []
            self.voice = {}
            self._run_id = ""
            self._task_id = ""
            try:
                SESSION_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return self.status()

    def refresh(self) -> dict:
        with self._lock:
            if not self.token:
                raise CloudError("NOT_LOGGED_IN", "请先登录测试账号")
            me = self.request("GET", "/auth/me")
            self.user = me.get("user") or self.user
            self.device_id = me.get("device_id") or self.device_id

            projects = self.request("GET", "/projects?limit=20").get("items") or []
            self.projects = [
                {"id": item.get("id"), "name": item.get("product_name") or item.get("name") or "未命名商品"}
                for item in projects if item.get("id")
            ]
            if self.project_id not in {p["id"] for p in self.projects}:
                self.project_id = self.projects[0]["id"] if self.projects else ""

            slots = self.request("GET", "/voice-slots")
            items = slots.get("items") if isinstance(slots, dict) else None
            slot = (items or [{}])[0] if items else (slots if slots else {})
            self.voice = {
                "name": slot.get("name") or slot.get("voice_name") or "",
                "speaker_id": slot.get("speaker_id") or "",
                "status": slot.get("cloud_status") or slot.get("status") or "",
                "trained": bool(slot.get("initial_training_completed")),
            } if isinstance(slot, dict) else {}
            self._persist()
            return self.status()

    def status(self) -> dict:
        project = next((p for p in self.projects if p["id"] == self.project_id), None)
        return {
            "base_url": self.base,
            "client_version": self.client_version,
            "logged_in": bool(self.token),
            "phone": self.user.get("phone") or "",
            "nickname": self.user.get("nickname") or "",
            "projects": self.projects,
            "project_id": self.project_id,
            "project_name": project["name"] if project else "",
            "voice": self.voice,
        }

    def select_project(self, project_id: str) -> dict:
        with self._lock:
            if project_id and project_id not in {p["id"] for p in self.projects}:
                raise CloudError("PROJECT_UNKNOWN", "该商品不在当前账号下")
            self.project_id = project_id
            self._run_id = ""
            self._task_id = ""
            self._persist()
            return self.status()

    def product(self) -> dict:
        """当前商品的资料字段，自动回答就按这些字段说话。"""
        if not self.project_id:
            raise CloudError("NO_PROJECT", "还没有选择商品")
        return self.request("GET", f"/projects/{self.project_id}")

    def update_product(self, fields: dict, version=None) -> dict:
        if not self.project_id:
            raise CloudError("NO_PROJECT", "还没有选择商品")
        extra = {"If-Match": f'"{version}"'} if version else None
        self.request("PUT", f"/projects/{self.project_id}", fields,
                     idempotent=True, extra_headers=extra)
        return self.product()

    def warmup(self, texts: list[str] | None = None) -> None:
        """开播前把场次和常用短句先准备好，第一条弹幕就不用再等这些。"""
        try:
            self._ensure_run()
        except CloudError:
            return
        for text in texts or []:
            text = (text or "").strip()
            if text and self.cached(text) is None:
                try:
                    self.synthesize(text)
                except CloudError:
                    pass

    # ---------- 合成 ----------

    def _ensure_run(self) -> str:
        project_id = self.project_id
        if not project_id:
            raise CloudError("NO_PROJECT", "当前账号没有可用商品，请先在测试端创建")
        if self._run_id:
            return self._run_id

        tasks = self.request("GET", "/live-tasks").get("items") or []
        task = next((t for t in tasks if t.get("project_id") == project_id), None)
        if task is None:
            task = self.request("POST", "/live-tasks", {
                "name": "演播厅联调", "platform": "douyin",
                "room_id": "", "room_url": "", "project_id": project_id,
            }, idempotent=True)
        self._task_id = task["id"]
        run = self.request("POST", f"/live-tasks/{task['id']}/runs", {
            "device_id": self.device_id,
            "client_run_id": f"stage-{uuid4().hex[:12]}",
            "project_id": project_id,
        }, idempotent=True)
        self._run_id = run["id"]
        return self._run_id

    def answer(self, question: str, nickname: str = "") -> tuple[str, Path]:
        """把一条弹幕交给云端：基于商品资料和知识库回答，并合成语音。"""
        question = (question or "").strip()
        if not question:
            raise CloudError("EMPTY_QUESTION", "问题为空")
        with self._lock:
            run_id = self._ensure_run()
            occurred = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            job = self.request("POST", "/reply-jobs", {
                "run_id": run_id,
                "project_id": self.project_id,
                "events": [{
                    "client_event_id": uuid4().hex,
                    "question": question[:500],
                    "nickname": (nickname or "")[:100],
                    "occurred_at": occurred,
                }],
                "response_kind": "text_and_audio",
            }, idempotent=True)
            job_id = job.get("job_id") or job.get("id")
            if not job_id:
                raise CloudError("BAD_RESPONSE", "云端没有返回任务号")
        # 轮询放在锁外：讲解预取会在锁内跑，不能把弹幕回复堵住
        deadline = time.time() + 30
        reply_text, audio_id = "", ""
        while time.time() < deadline:
            view = self.request("GET", f"/jobs/{job_id}")
            status = view.get("status")
            if status == "succeeded":
                reply_text = view.get("reply_text") or view.get("text") or ""
                audio_id = view.get("audio_id") or ""
                if not reply_text:
                    # 云端判定资料里没有答案时也会返回 succeeded，但文字为空；
                    # 这种情况要立刻结束，不能傻等 30 秒
                    reason = str(view.get("reason") or "")
                    if reason == "knowledge_missing":
                        raise CloudError("NO_KNOWLEDGE", "商品资料里没有这条答案")
                    raise CloudError("EMPTY_ANSWER", f"云端没有给出回答（{reason or '空'}）")
                break
            if status in {"failed", "expired", "cancelled"}:
                reason = view.get("last_error") or view.get("reason") or status
                raise CloudError("REPLY_FAILED", f"云端未生成回复：{reason}")
            time.sleep(0.3)
        if not reply_text:
            raise CloudError("REPLY_TIMEOUT", "云端 30 秒内没有给出回复")

        cached = self.cached(reply_text)
        if cached is not None:
            return reply_text, cached
        if not audio_id:
            raise CloudError("REPLY_NO_AUDIO", "云端只返回了文字，没有音频")
        return reply_text, self._audio_for(reply_text, audio_id)

    def _audio_for(self, text: str, audio_id: str) -> Path:
        cache_key = self.cache_path(text)
        path = self._download(audio_id, text)
        try:
            path.replace(cache_key)
            path = cache_key
        except OSError:
            pass
        remember(text, path)
        return path

    def cache_path(self, text: str) -> Path:
        digest = hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]
        return CACHE / f"text-{digest}.mp3"

    def cached(self, text: str) -> Path | None:
        """同一句文案只需要合成一次，命中本地文件就不再请求云端。"""
        path = self.cache_path(text)
        return path if path.exists() and path.stat().st_size > 1024 else None

    # 云端对动态合成有频率上限，撞上了就等一会儿再试，别整段文案中断
    SYNTH_RETRY_DELAYS = (8, 15, 25, 40)

    def synthesize(self, text: str) -> Path:
        text = (text or "").strip()
        if not text:
            raise CloudError("EMPTY_TEXT", "回复内容为空")
        cached = self.cached(text)
        if cached is not None:
            remember(text, cached)
            return cached
        for attempt in range(len(self.SYNTH_RETRY_DELAYS) + 1):
            try:
                return self._synthesize_once(text)
            except CloudError as error:
                limited = (error.status == 429 or error.code == "RATE_LIMITED"
                           or "频繁" in error.message)
                if not limited or attempt >= len(self.SYNTH_RETRY_DELAYS):
                    raise
                time.sleep(self.SYNTH_RETRY_DELAYS[attempt])
        raise CloudError("SYNTH_FAILED", "云端合成失败")

    def _synthesize_once(self, text: str) -> Path:
        cache_key = self.cache_path(text)
        with self._lock:
            run_id = self._ensure_run()
            job = self.request("POST", "/manual-reply-jobs", {
                "run_id": run_id,
                "project_id": self.project_id,
                "answer": text[:500],
                "response_kind": "text_and_audio",
            }, idempotent=True)
            job_id = job.get("job_id") or job.get("id")
            if not job_id:
                raise CloudError("BAD_RESPONSE", "云端没有返回任务号")
        # 轮询和下载放在锁外，弹幕回复不用排队等整段讲解合成完
        deadline = time.time() + 45
        audio_id = ""
        while time.time() < deadline:
            view = self.request("GET", f"/jobs/{job_id}")
            status = view.get("status")
            if status == "succeeded" and view.get("audio_id"):
                audio_id = view["audio_id"]
                break
            if status in {"failed", "expired", "cancelled"}:
                reason = view.get("last_error") or view.get("reason") or status
                raise CloudError("SYNTH_FAILED", f"云端合成未成功：{reason}")
            time.sleep(0.4)
        if not audio_id:
            raise CloudError("SYNTH_TIMEOUT", "云端合成超过 45 秒仍未完成")

        path = self._download(audio_id, text)
        try:
            # 同一句文案只合成一次，循环播放时直接命中缓存
            path.replace(cache_key)
            path = cache_key
        except OSError:
            pass
        remember(text, path)
        return path

    def _download(self, audio_id: str, text: str) -> Path:
        digest = hashlib.sha1((self.base + audio_id + text).encode("utf-8")).hexdigest()[:16]
        try:
            payload = self.request("GET", f"/audios/{audio_id}/file")
            raw = payload.get("_bytes") or b""
            ctype = payload.get("_content_type") or "audio/mpeg"
            suffix = ".mp3" if "mpeg" in ctype else ".wav"
        except CloudError as error:
            if error.status != 409:
                raise
            # 音频存放在对象存储时改走下载链接
            info = self.request("POST", f"/audios/{audio_id}/download-url", {}, idempotent=True)
            url = info.get("url") or info.get("download_url")
            if not url:
                raise CloudError("NO_AUDIO_URL", "云端没有返回下载地址") from None
            with urlopen(url, timeout=45) as response:
                raw = response.read()
            suffix = ".mp3"

        if len(raw) < 512:
            raise CloudError("EMPTY_AUDIO", "云端返回的音频为空")
        path = CACHE / f"cloud-{digest}{suffix}"
        path.write_bytes(raw)
        return path


cloud = CloudSession()
