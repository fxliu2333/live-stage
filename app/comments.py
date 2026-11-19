"""抖音弹幕采集与自动回复。

用 Edge 的调试端口读直播间评论区的 DOM，识别出新弹幕后交给云端
（基于当前商品的知识库回答），再把回答用测试端音色播出去。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import websocket

from . import audio
from .cloud import CloudError, cloud
from .paths import DATA

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = DATA / "browser"
CDP_PORT = 9333

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

EXTRACT_JS = r"""
(() => {
  const clean = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const NOISE = /https?:\/\/|feedback@|举报邮箱|违法和不良信息/;
  // 礼物栏、在线观众榜也会被结构兜底扫到：昵称是价格/排名/占位符的直接丢弃
  const JUNK_NICK = /^([-–—+]+|\d{1,4}|.{0,2}钻)$/;
  const BADGE = /^(粉丝团|粉丝牌|Lv\.?\s?\d+|等级|主播|管理员|房管|超管|粉丝)$/i;
  const seen = new Set();
  const out = [];
  const push = (nickname, text) => {
    nickname = clean(nickname).replace(/[：:]\s*$/, '');
    text = clean(text);
    if (!text || text.length > 180) return;
    if (JUNK_NICK.test(nickname)) return;
    if (/赠送/.test(text) && /钻/.test(text)) return;
    if (NOISE.test(nickname + text)) return;
    const key = nickname + '|' + text;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ nickname: nickname, text: text });
  };
  const leaves = (el) => {
    const result = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const value = clean(walker.currentNode.nodeValue);
      if (value) result.push(value);
    }
    return result;
  };
  const split = (whole) => {
    const m = clean(whole).match(/^([^:：]{1,24})[：:]\s*([\s\S]{1,180})$/);
    return m ? { nickname: clean(m[1]), text: clean(m[2]) } : null;
  };
  const pickText = (root, selector) => {
    const node = root.querySelector(selector);
    return node ? clean(node.textContent) : '';
  };
  // 页面里藏着不少 display:none 的面板（私信列表等），必须排除，否则会抓到假弹幕
  const visible = (el) => {
    if (!el) return false;
    try {
      if (typeof el.checkVisibility === 'function'
          && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    } catch (e) { /* 老版本内核不支持就退回尺寸判断 */ }
    const rect = el.getBoundingClientRect();
    return rect.width >= 4 && rect.height >= 4;
  };

  // A. 已知的评论条目类名（抖音直播间的老结构）
  const known = document.querySelectorAll('.webcast-chatroom___item, [data-e2e="chat-item"], .chatroom___item');
  if (known.length) {
    for (const el of Array.from(known).slice(-30)) {
      if (!visible(el)) continue;
      const nickname = pickText(el, '.v8LY0gZF, [class*="nickname"]');
      const content = pickText(el, '.webcast-chatroom___content-with-emoji-text, .cL385mHb, [class*="content"]');
      if (content) { push(nickname, content); continue; }
      const parsed = split(el.textContent);
      if (parsed) { push(parsed.nickname, parsed.text); continue; }
      const list = leaves(el).filter((t) => !BADGE.test(t));
      if (list.length >= 2) push(list[0], list.slice(1).join(' '));
    }
    if (out.length) return JSON.stringify(out.slice(-20));
  }

  // B. 文本里带“昵称：内容”的，按父容器聚组，取条目最多的一组
  const groups = new Map();
  for (const el of document.querySelectorAll('div,span,p,li,section')) {
    if (el.childElementCount > 2) continue;
    if (!visible(el)) continue;
    const whole = clean(el.textContent);
    if (whole.length < 3 || whole.length > 200) continue;
    const parsed = split(whole);
    if (!parsed || !parsed.text) continue;
    if (NOISE.test(parsed.nickname + parsed.text)) continue;
    const parent = el.parentElement;
    if (!parent) continue;
    if (!groups.has(parent)) groups.set(parent, []);
    groups.get(parent).push(parsed);
  }
  let best = [];
  for (const list of groups.values()) if (list.length > best.length) best = list;
  if (best.length >= 3) {
    for (const item of best.slice(-20)) push(item.nickname, item.text);
    if (out.length) return JSON.stringify(out.slice(-20));
  }

  // B2. 评论区容器还在，但每条评论的类名变了：直接看容器里的子节点
  let fromList = 0;
  const lists = document.querySelectorAll('.webcast-chatroom___list, [class*="chatroom"][class*="list"]');
  for (const list of lists) {
    for (const kid of Array.from(list.children).slice(-20)) {
      if (!visible(kid)) continue;
      const items = leaves(kid).filter((value) => !BADGE.test(value));
      if (items.length < 2 || items.length > 5) continue;
      const text = items.slice(1).join(' ');
      if (text.length < 2 || text.length > 180 || text === items[0]) continue;
      push(items[0], text);
      fromList += 1;
    }
  }
  if (fromList >= 2) return JSON.stringify(out.slice(-20));
  out.length = 0;
  seen.clear();

  // C. 结构兜底：页面上不带冒号时，每条评论通常是“昵称 + 内容”两段文本
  let bestKids = [];
  let bestScore = 0;
  const candidates = document.querySelectorAll('div,ul,ol,section,article');
  for (const parent of candidates) {
    const kids = Array.from(parent.children);
    if (kids.length < 3 || kids.length > 200) continue;
    if (!visible(parent)) continue;
    const matched = [];
    for (const kid of kids) {
      if (!visible(kid)) continue;
      const list = leaves(kid);
      if (list.length < 2 || list.length > 5) continue;
      const nickname = list[0];
      if (!nickname || nickname.length > 20 || BADGE.test(nickname)) continue;
      const text = list.slice(1).join(' ');
      if (text.length < 2 || text.length > 180 || text === nickname) continue;
      if (NOISE.test(nickname + text)) continue;
      matched.push({ nickname: nickname, text: text });
    }
    if (matched.length < 3) continue;
    const hint = String(parent.className || '') + ' ' + String(parent.id || '');
    const score = matched.length + (/chat|comment|danmu|barrage/i.test(hint) ? 8 : 0);
    if (score > bestScore) { bestScore = score; bestKids = matched; }
  }
  if (bestKids.length) {
    for (const item of bestKids.slice(-20)) push(item.nickname, item.text);
    if (out.length) return JSON.stringify(out.slice(-20));
  }
  return '[]';
})()
"""

PROBE_JS = r"""
(() => {
  const clean = (value) => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const bodyText = clean(document.body ? document.body.innerText : '');
  const info = {
    url: location.href,
    title: document.title,
    bodyLength: bodyText.length,
    ended: /直播已结束|主播暂时离开|直播已关闭/.test(bodyText),
    loginWall: /扫码登录|登录后|请登录|密码登录|验证码登录/.test(bodyText.slice(0, 3000)),
    hits: {
      webcastItem: document.querySelectorAll('.webcast-chatroom___item').length,
      chatroomList: document.querySelectorAll('.webcast-chatroom___list').length,
      listItems: document.querySelectorAll('.webcast-chatroom___list > *').length,
      chatroomClass: document.querySelectorAll('[class*="chatroom"]').length
    },
    containers: []
  };
  const scored = [];
  for (const parent of document.querySelectorAll('div,ul,ol,section,article')) {
    const kids = Array.from(parent.children);
    if (kids.length < 3) continue;
    const texted = kids.filter((kid) => clean(kid.textContent).length >= 2);
    if (texted.length < 3) continue;
    scored.push({ parent: parent, count: texted.length });
  }
  scored.sort((a, b) => b.count - a.count);
  for (const entry of scored.slice(0, 8)) {
    const parent = entry.parent;
    info.containers.push({
      tag: parent.tagName.toLowerCase(),
      cls: clean(parent.className).slice(0, 100),
      kids: parent.children.length,
      samples: Array.from(parent.children).slice(-3).map((kid) => ({
        tag: kid.tagName.toLowerCase(),
        cls: clean(kid.className).slice(0, 80),
        text: clean(kid.textContent).slice(0, 60)
      }))
    });
  }
  return JSON.stringify(info);
})()
"""


def _extract_expr(room_id: str) -> str:
    """只在页面确实是目标直播间时才提取，避免把首页推荐当成弹幕。"""
    if not room_id:
        return EXTRACT_JS
    return ('(() => { if (location.href.indexOf(' + json.dumps(room_id) + ') < 0) return "[]"; '
            'return ' + EXTRACT_JS.strip() + '; })()')


def _room_id(room_url: str) -> str:
    found = re.findall(r"/(\d{6,})", room_url or "")
    return found[-1] if found else ""

SKIP_WORDS = ("来了", "进入直播间", "关注了", "赞了", "点亮", "加入了", "分享了直播间",
              "为主播点赞", "送出", "送来了", "加入粉丝团", "购买了", "正在观看")

# 价格类问题按用户要求不自动回答（避免说了不该说的数字）
PRICE_WORDS = re.compile(r"多少钱|什么价|价格|售价|怎么卖|几块钱|多少米|便宜|最低多少|贵不贵")


def _skip_reason(text: str) -> str:
    """返回不回复的原因，空字符串表示可以回复。"""
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", text or ""):
        return "只有表情或符号"
    stripped = re.sub(r"[\s，。！？,.!?~～…、:：;；\"'“”‘’]", "", text or "")
    if stripped in SKIP_WORDS:
        return f"“{stripped}”是进场/礼物提示"
    if len(stripped) < 2:
        return "内容太短"
    if re.fullmatch(r"\d+", stripped):
        return "只有数字"
    return ""



def _edge_path() -> str:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("没有找到 Microsoft Edge")


def _cdp(path: str):
    with urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=2.5) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _ensure_edge(room_url: str) -> bool:
    try:
        _cdp("/json/version")
        return False
    except Exception:
        pass

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        _edge_path(),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        room_url,
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=creationflags)

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _cdp("/json/version")
            return True
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("Edge 调试端口没有起来，可能被安全软件拦截")


def _open_tab(url: str) -> bool:
    """Edge 开着但没有抖音页面时（比如被手动关掉了标签），新开一个。"""
    try:
        request = Request(f"http://127.0.0.1:{CDP_PORT}/json/new?{quote(url, safe='')}",
                          method="PUT")
        with urlopen(request, timeout=6) as response:
            return bool(json.loads(response.read().decode("utf-8", "ignore")))
    except Exception:
        return False


def _room_page(room_url: str) -> tuple[str, str]:
    try:
        targets = _cdp("/json")
    except Exception:
        return "", ""
    wanted = _room_id(room_url)
    fallback: tuple[str, str] = ("", "")
    for target in targets or []:
        if target.get("type") != "page":
            continue
        url = str(target.get("url") or "")
        if "douyin.com" not in url:
            continue
        ws_url = str(target.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            continue
        if wanted and wanted in url:
            return ws_url, url
        if not fallback[0]:
            fallback = (ws_url, url)
    return fallback


def _navigate(ws_url: str, url: str) -> None:
    ws = websocket.create_connection(ws_url, timeout=6, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
        time.sleep(0.3)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _page_list() -> list[dict]:
    try:
        targets = _cdp("/json")
    except Exception:
        return []
    pages = []
    for target in targets or []:
        if target.get("type") != "page":
            continue
        pages.append({"title": target.get("title", ""), "url": target.get("url", "")})
    return pages


def probe(room_url: str = "") -> dict:
    """把当前抖音页面的结构导出来，用来定位采集不到的原因。"""
    try:
        if room_url:
            _ensure_edge(room_url)
    except Exception as error:
        return {"ok": False, "error": str(error), "pages": _page_list()}

    ws_url, page_url = _room_page(room_url)
    deadline = time.time() + 25
    while time.time() < deadline:
        if ws_url:
            actual = str(_evaluate(ws_url, "location.href") or "")
            if "douyin.com" in actual:
                page_url = actual
                break
        time.sleep(0.8)
        ws_url, page_url = _room_page(room_url)
    if not ws_url:
        return {"ok": False, "error": "没有找到抖音页面，请先在 Edge 里打开直播间", "pages": _page_list()}

    raw = _evaluate(ws_url, PROBE_JS)
    if raw is None:
        return {"ok": False, "error": "页面没有响应调试协议", "page": page_url, "pages": _page_list()}
    try:
        info = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"ok": False, "error": "页面结构读取失败", "page": page_url, "raw": str(raw)[:300]}
    if not isinstance(info, dict):
        return {"ok": False, "error": "页面结构读取失败", "page": page_url}
    info["ok"] = True
    info["page"] = page_url
    info["extract"] = json.loads(_evaluate(ws_url, _extract_expr(_room_id(room_url))) or "[]")
    info["pages"] = _page_list()
    return info


def _evaluate(ws_url: str, expression: str):
    ws = websocket.create_connection(ws_url, timeout=6, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expression, "returnByValue": True}}))
        deadline = time.time() + 6
        while time.time() < deadline:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            return message.get("result", {}).get("result", {}).get("value")
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _call(ws_url: str, method: str) -> None:
    try:
        ws = websocket.create_connection(ws_url, timeout=4, suppress_origin=True)
    except Exception:
        return
    try:
        ws.send(json.dumps({"id": 9, "method": method, "params": {}}))
        time.sleep(0.2)
    except Exception:
        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass


class _PageLink:
    """保持一条调试连接，避免每轮采集都重新握手（这是弹幕上屏慢的主因）。"""

    def __init__(self) -> None:
        self._ws = None
        self._seq = 0
        self.url = ""

    @property
    def closed(self) -> bool:
        return self._ws is None

    def open(self, ws_url: str) -> None:
        self.close()
        self._ws = websocket.create_connection(ws_url, timeout=6, suppress_origin=True)
        self.url = ws_url

    def close(self) -> None:
        if self._ws is None:
            return
        try:
            self._ws.close()
        except Exception:
            pass
        self._ws = None
        self.url = ""

    def evaluate(self, expression: str):
        if self._ws is None:
            raise RuntimeError("调试连接已关闭")
        self._seq += 1
        ident = self._seq
        self._ws.send(json.dumps({"id": ident, "method": "Runtime.evaluate",
                                  "params": {"expression": expression, "returnByValue": True}}))
        deadline = time.time() + 6
        while time.time() < deadline:
            message = json.loads(self._ws.recv())
            if message.get("id") != ident:
                continue
            return message.get("result", {}).get("result", {}).get("value")
        raise TimeoutError("页面没有响应")


class LiveComments:
    def __init__(self, publish) -> None:
        self._publish = publish
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._room_url = ""
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._recent: deque = deque(maxlen=30)
        self._error = ""
        self._running = False
        self._auto_reply = False
        self._reply_times: deque = deque()
        self._replying = False
        self._counts = {"collected": 0, "replied": 0, "skipped": 0}
        self._last_skip = ""
        self._filler = ""

    def status(self) -> dict:
        return {
            "running": self._running,
            "room_url": self._room_url,
            "auto_reply": self._auto_reply,
            "error": self._error,
            "last_skip": self._last_skip,
            "filler": self._filler,
            "comments": list(self._recent),
            "counts": dict(self._counts),
        }

    def start(self, room_url: str, auto_reply: bool, filler: str = "") -> dict:
        with self._lock:
            if self._running:
                return self.status()
            if not room_url.startswith("http"):
                raise ValueError("直播间地址要以 http 开头")
            _ensure_edge(room_url)
            self._room_url = room_url
            self._auto_reply = auto_reply
            self._filler = (filler or "").strip()
            self._error = ""
            self._last_skip = ""
            self._counts = {"collected": 0, "replied": 0, "skipped": 0}
            self._recent.clear()
            self._seen.clear()
            self._stop.clear()
            self._running = True
            threading.Thread(target=self._warmup, daemon=True).start()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self.status()

    def _warmup(self) -> None:
        """提前建好场次、把过渡语合成好，第一条回复能快一两秒。"""
        cloud.warmup([self._filler])

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self._running = False
            audio.stop_playback()
        return self.status()

    def set_auto_reply(self, enabled: bool) -> dict:
        self._auto_reply = bool(enabled)
        return self.status()

    def _emit(self, event: str, data: dict) -> None:
        try:
            self._publish(event, data)
        except Exception:
            pass

    def _loop(self) -> None:
        link = _PageLink()
        prepared = False
        warned = False
        wanted = _room_id(self._room_url)
        started_at = time.time()
        while not self._stop.is_set():
            try:
                if link.closed:
                    ws_url, page_url = _room_page(self._room_url)
                    if not ws_url:
                        try:
                            _ensure_edge(self._room_url)
                        except Exception:
                            pass
                        if _open_tab(self._room_url):
                            time.sleep(2.0)
                        self._error = "Edge 里还没有抖音页面，正在打开直播间…"
                        time.sleep(1.5)
                        continue
                    if not prepared:
                        prepared = True
                        if wanted and wanted not in page_url:
                            _navigate(ws_url, self._room_url)
                            time.sleep(3.0)
                    link.open(ws_url)
                    self._error = ""

                raw = link.evaluate(_extract_expr(wanted))
                if raw is None:
                    link.close()
                    time.sleep(0.4)
                    continue
                for item in json.loads(raw or "[]"):
                    self._handle(item)
                if self._counts["collected"]:
                    warned = False
                    self._error = ""
                elif not warned and time.time() - started_at > 12:
                    warned = True
                    self._error = self._diagnose(link)
            except Exception as exc:
                self._error = str(exc)[:120]
                link.close()
                time.sleep(1.0)
            time.sleep(0.35)
        link.close()
        self._running = False

    def _diagnose(self, link: _PageLink) -> str:
        try:
            raw = link.evaluate(PROBE_JS)
        except Exception:
            raw = None
        if not raw:
            return "已连上页面，但没读到弹幕；可能页面还没加载完，等几秒或刷新直播间再试"
        try:
            info = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return "已连上页面，但没读到弹幕；请点「诊断采集」查看页面结构"
        if info.get("loginWall"):
            _call(link.url, "Page.bringToFront")
            return "抖音还没登录：已把 Edge 切到前面，扫码登录后会自动恢复采集"
        wanted = _room_id(self._room_url)
        if wanted and wanted not in str(info.get("url") or ""):
            _navigate(link.url, self._room_url)
            return "页面还没打开你填的直播间，正在跳转过去，请稍等"
        if info.get("ended"):
            return "这个直播间已经结束（页面显示“直播已结束”），请换成正在直播的房间地址"
        if not info.get("containers") and not info.get("hits", {}).get("chatroomList"):
            return "这个页面没有评论区：请用直播中的房间地址，例如 live.douyin.com/房间号"
        return "页面有评论区但没提取到文字，请点「诊断采集」把结果发给我"

    def _handle(self, item: dict) -> None:
        text = str(item.get("text") or "").strip()
        nickname = str(item.get("nickname") or "").strip()
        if not text:
            return

        key = f"{nickname}|{text}"
        now = time.time()
        with self._lock:
            for stale in [k for k, at in self._seen.items() if now - at > 120]:
                self._seen.pop(stale, None)
            if key in self._seen:
                return
            self._seen[key] = now

        self._counts["collected"] += 1
        entry = {"nickname": nickname, "text": text, "at": round(now, 1)}
        self._recent.append(entry)
        self._emit("comment", entry)

        if not self._auto_reply:
            self._skip("自动回复没有勾选", text, nickname)
            return
        if PRICE_WORDS.search(text):
            self._skip("价格类问题不回答", text, nickname)
            return
        reason = _skip_reason(text)
        if reason:
            self._skip(reason, text, nickname)
            return
        self._reply(text, nickname)

    def _skip(self, reason: str, text: str, nickname: str) -> None:
        self._counts["skipped"] += 1
        self._last_skip = f"{reason}：{text[:20]}"
        self._emit("reply_state", {"state": "skipped", "text": text,
                                   "nickname": nickname, "detail": reason})

    def _reply(self, text: str, nickname: str) -> None:
        with self._lock:
            if self._replying:
                self._counts["skipped"] += 1
                self._last_skip = "上一条还在合成/播放，本条跳过"
                return
            now = time.time()
            while self._reply_times and now - self._reply_times[0] > 60:
                self._reply_times.popleft()
            if len(self._reply_times) >= 4:
                self._counts["skipped"] += 1
                self._last_skip = "每分钟最多回复 4 条，本条跳过"
                return
            self._reply_times.append(now)
            self._replying = True

        def worker() -> None:
            started = time.time()
            if not cloud.status().get("logged_in"):
                self._emit("reply_state", {"state": "error", "text": text,
                                           "detail": "未登录测试端，无法生成回复"})
                return

            box: dict = {}

            def ask() -> None:
                try:
                    box["value"] = cloud.answer(text, nickname)
                except Exception as exc:
                    box["error"] = exc

            threading.Thread(target=ask, daemon=True).start()
            try:
                self._emit("reply_state", {"state": "synthesizing", "text": text,
                                           "nickname": nickname})
                # 云端要几秒。先等 0.6 秒，还不出结果就播过渡语，避免冷场；
                # 命中缓存很快时就不插过渡语，免得把回答往后推。
                filler = cloud.cached(self._filler) if self._filler else None
                wait_until = time.time() + 0.6
                while time.time() < wait_until and "value" not in box and "error" not in box:
                    time.sleep(0.05)
                if filler is not None and "value" not in box and "error" not in box:
                    audio.play_audio(filler, None, 0.9, True)
                while "value" not in box and "error" not in box:
                    time.sleep(0.05)
                if "error" in box:
                    raise box["error"]
                answer, path = box["value"]
                self._emit("subtitle", {"nickname": nickname, "text": answer,
                                        "duration_ms": 9000})
                self._emit("reply_state", {"state": "playing", "text": answer,
                                           "nickname": nickname})
                result = audio.play_audio(path, None, 0.9, True)
                self._counts["replied"] += 1
                self._emit("reply_state", {"state": "done" if result.get("ok") else "error",
                                           "text": answer, "nickname": nickname,
                                           "detail": f"{time.time() - started:.1f} 秒"
                                                     + (f" · {result.get('error')}" if result.get("error") else "")})
            except CloudError as error:
                if error.code in {"NO_KNOWLEDGE", "EMPTY_ANSWER"}:
                    self._counts["skipped"] += 1
                    self._last_skip = f"{error.message}：{text[:20]}"
                    self._emit("reply_state", {"state": "skipped", "text": text,
                                               "nickname": nickname, "detail": error.message})
                elif error.status == 429 or "频繁" in error.message:
                    # 云端自己也在给回复限速，这里当成跳过，别报成失败
                    self._counts["skipped"] += 1
                    self._last_skip = f"云端限流：{text[:20]}"
                    self._emit("reply_state", {"state": "skipped", "text": text,
                                               "nickname": nickname, "detail": "云端限流，本条跳过"})
                else:
                    self._emit("reply_state", {"state": "error", "text": text,
                                               "nickname": nickname,
                                               "detail": f"{error.code}: {error.message}"})
            except Exception as error:
                self._emit("reply_state", {"state": "error", "text": text,
                                           "detail": str(error)})
            finally:
                with self._lock:
                    self._replying = False

        threading.Thread(target=worker, daemon=True).start()


live_comments = LiveComments(lambda *_: None)


def configure(publish) -> None:
    live_comments._publish = publish
