import base64
import json
import os
import queue
import re
import threading
import time
import weakref
from collections import deque
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

import config
import page_scripts as js
from cdp_fast import FastCDP

MOUSE_BUTTONS = {"left", "right", "middle"}
ELEMENT_ACTIONS = {"click", "type", "select", "drag", "hover"}
COMMAND_WHITELIST = {"eval", "extract_text", "page_text", "get_selection"}  # exposed via /api/ai_command
# HLS / DASH playlists list stream segments; they are not audio files a model can transcribe.
STREAM_PLAYLIST = re.compile(r"\.(m3u8|mpd)(\?|#|$)|mpegurl|dash\+xml", re.I)


def _short_err(err) -> str:
    msg = str(err).strip().splitlines()[0] if str(err).strip() else err.__class__.__name__
    return msg[:180]


class BrowserManager:
    """
    Owns Playwright Chromium in one dedicated worker thread (Playwright's sync API is not thread-safe).
    Other threads talk to it through `execute()`, which enqueues a command and waits for the result.

    Live view: a CDP screencast pushes JPEG frames only when the page actually repaints. Frames are
    handed to HTTP stream clients through a Condition, so an idle page costs ~0 CPU and 0 bandwidth.
    """

    def __init__(self, bus):
        self.bus = bus
        self.cmd_queue = queue.Queue()
        self.running = True
        self.ready = threading.Event()
        self.alive = True

        # Latest frame
        self._frame_cond = threading.Condition()
        self.frame_bytes = None
        self.frame_id = 0
        self.frame_size = (config.VIEWPORT_WIDTH, config.VIEWPORT_HEIGHT)
        self._suppress = False
        self.screencast_ok = False
        # Low-latency side channel for frames + user input (see cdp_fast.py)
        self.fast = FastCDP(on_frame=self._set_frame, log=self.log)
        self._devtools_port = None
        # "window" mode: the user works in a real Chromium window; nothing is streamed.
        self.window_mode = config.BROWSER_MODE == "window"
        self.window_closed = False

        # Published state
        self.viewport = dict(config.VIEWPORT)
        self.tabs = []
        self.active_index = 0
        self.url = ""
        self.title = ""
        self.loading = False
        self._loading_since = 0.0
        self.scroll_info = {"currentY": 0, "maxY": 0, "viewH": config.VIEWPORT_HEIGHT, "ratio": 0.0,
                            "canScrollDown": False, "canScrollUp": False}
        self.mouse = {"x": config.VIEWPORT_WIDTH // 2, "y": config.VIEWPORT_HEIGHT // 2}
        self.ai_pointer = None
        self.logs = deque(maxlen=80)
        self._log_lock = threading.Lock()
        self._last_state_json = ""

        # Worker-thread-only objects
        self.context = None
        self.page = None
        self._cdp = None
        self._registered_pages = weakref.WeakSet()
        self._pending_page = None
        self._ai_elements = {}
        self._inflight = {}  # request -> start time, active tab only (see _wait_network_quiet)
        self._scroll_frame = None
        self._typed_buffer = ""
        self._dnd_pref = {}  # host -> drag strategy that worked last time
        self._media = deque(maxlen=30)  # audio responses seen on the pages

        os.makedirs(config.PROFILE_DIR, exist_ok=True)
        self.worker_thread = threading.Thread(target=self._worker_main, name="playwright-worker", daemon=True)
        self.worker_thread.start()

    # ------------------------------------------------------------------ public (any thread)

    def log(self, message: str):
        entry = f"[{time.strftime('%H:%M:%S')}] {message}"
        with self._log_lock:
            self.logs.append(entry)
        self.bus.publish("log", entry)

    def execute(self, action: str, payload=None, timeout: float = 45.0) -> dict:
        """Thread-safe request dispatcher. Never raises; errors come back as {"status": "error"}."""
        if not self.alive:
            return {"status": "error", "message": "Trình duyệt chưa khởi động hoặc đã dừng."}
        res_queue = queue.Queue(maxsize=1)
        self.cmd_queue.put((action, payload or {}, res_queue))
        try:
            return res_queue.get(timeout=timeout)
        except queue.Empty:
            return {"status": "error", "message": f"Hết thời gian chờ lệnh '{action}' ({int(timeout)}s)."}

    @property
    def _suppress_frames(self):
        return self._suppress

    @_suppress_frames.setter
    def _suppress_frames(self, value):
        # While the AI draws Set-of-Marks labels, viewers keep seeing the previous clean frame.
        self._suppress = value
        self.fast.suppress_frames = value

    def input_events(self, events) -> dict:
        """User input from the UI (any thread). Goes straight to Chrome when the fast channel is up."""
        events = events or []
        if self.fast.connected:
            try:
                last = self.fast.dispatch(events)
                if last:
                    self.mouse = {"x": int(last[0]), "y": int(last[1])}
                for ev in events:
                    if ev.get("t") == "up" and ev.get("button", "left") == "left" and int(ev.get("count", 1)) == 1:
                        self.log(f"Click ({int(ev.get('x', 0))}, {int(ev.get('y', 0))})")
                    elif ev.get("t") == "key":
                        self.log(f"Phím: {ev.get('key')}")
                return {"status": "ok", "path": "fast"}
            except ValueError as e:
                return {"status": "error", "message": str(e)}
            except Exception:
                pass  # channel dropped mid-batch: fall back to the Playwright worker
        return self.execute("input", {"events": events})

    def wait_frame(self, last_id: int, timeout: float = 10.0):
        """Blocks until a frame newer than last_id exists. Returns (frame_id, jpeg_bytes, (w, h))."""
        with self._frame_cond:
            if self.frame_id == last_id:
                self._frame_cond.wait(timeout)
            return self.frame_id, self.frame_bytes, self.frame_size

    def get_screenshot(self):
        return self.frame_bytes

    def get_state(self) -> dict:
        with self._log_lock:
            logs = list(self.logs)
        state = self._state_dict()
        state["logs"] = logs
        state["frame_id"] = self.frame_id
        return state

    def stop(self):
        self.running = False

    # ------------------------------------------------------------------ worker thread

    def _worker_main(self):
        try:
            self._run()
        except Exception as e:
            self.log(f"Lỗi nghiêm trọng của trình duyệt: {_short_err(e)}")
        finally:
            self.alive = False
            self.ready.set()
            # Unblock anyone still waiting on a command
            while True:
                try:
                    _, _, rq = self.cmd_queue.get_nowait()
                    rq.put({"status": "error", "message": "Trình duyệt đã dừng."})
                except queue.Empty:
                    break

    def _launch(self, p):
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_file = os.path.join(config.PROFILE_DIR, lock_name)
            try:
                if os.path.exists(lock_file) or os.path.islink(lock_file):
                    os.remove(lock_file)
            except Exception:
                pass
        kwargs = {
            "user_data_dir": config.PROFILE_DIR,
            "headless": True,
            "viewport": self.viewport,
            "user_agent": config.USER_AGENT,
            "locale": "vi-VN",
            # Random local port for the fast CDP channel (cdp_fast.py); read back from DevToolsActivePort.
            "args": config.CHROMIUM_ARGS + ["--remote-debugging-port=0"],
        }
        if self.window_mode:
            # A real browser window: GPU on, sound on, the page follows the window size.
            args = [a for a in config.CHROMIUM_ARGS if a not in ("--disable-gpu", "--disable-software-rasterizer", "--mute-audio")]
            if config.WINDOW_RECT:
                x, y, w, h = (int(v) for v in config.WINDOW_RECT.split(","))
                args += [f"--window-position={x},{y}", f"--window-size={w},{h}"]
            kwargs.update(headless=False, no_viewport=True, args=args)
            kwargs.pop("viewport")
            kwargs.pop("user_agent")  # the real Chromium UA matches the real browser
        try:
            ctx = p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            self.log(f"Profile chính đang bị khóa ({_short_err(e)}), dùng profile tạm...")
            kwargs["user_data_dir"] = os.path.join(config.PROFILE_DIR, f"run_{int(time.time())}")
            ctx = p.chromium.launch_persistent_context(**kwargs)
        if not self.window_mode:  # window mode has no fast stream channel, hence no debugging port
            self._devtools_port = self._read_devtools_port(kwargs["user_data_dir"])
        return ctx

    @staticmethod
    def _read_devtools_port(user_data_dir):
        path = os.path.join(user_data_dir, "DevToolsActivePort")
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return int(f.readline().strip())
            except (OSError, ValueError):
                time.sleep(0.05)
        return None

    def _run(self):
        self.log("Khởi động Chromium (profile lưu đăng nhập lâu dài)...")
        t_start = time.time()
        with sync_playwright() as p:
            print(f"[timing] playwright driver up: {time.time() - t_start:.1f}s", flush=True)
            self.context = self._launch(p)
            print(f"[timing] chromium launched: {time.time() - t_start:.1f}s", flush=True)
            # Safety net for any Playwright call without its own timeout: Playwright's 30 s default equals
            # execute()'s, so a stuck call kept the worker busy past it and every queued command timed out.
            self.context.set_default_timeout(10000)
            if config.BLOCK_ADS:
                # The pattern is matched inside the Playwright driver: other requests never reach Python.
                self.context.route(config.AD_URL_RE, lambda route: route.abort("blockedbyclient"))
            self.context.on("page", self._on_context_page)
            first = self.context.pages[0] if self.context.pages else self.context.new_page()
            self._register_page(first)
            self._attach(first)
            self.ready.set()
            print(f"[timing] browser ready: {time.time() - t_start:.1f}s", flush=True)

            try:
                first.goto(config.DEFAULT_URL, timeout=35000, wait_until="domcontentloaded")
            except Exception as e:
                self.log(f"Không tải được trang mặc định: {_short_err(e)}")
            self.log(f"Trình duyệt sẵn sàng (screencast: {'bật' if self.screencast_ok else 'dự phòng ảnh chụp'})")

            last_tabs = 0.0
            last_fallback = 0.0
            last_scroll_t = 0.0
            last_scroll_frame = -1

            while self.running:
                self._drain_commands()

                if self._pending_page is not None:
                    pg, self._pending_page = self._pending_page, None
                    if not pg.is_closed():
                        self._attach(pg)

                if self.page is None or self.page.is_closed():
                    self._recover_page()
                    if not self.running:
                        break
                elif self.screencast_ok and self._cdp is None and not self.fast.connected:
                    self.log("Kết nối lại luồng hình...")
                    self._start_screencast()

                # Pump Playwright's event loop so screencast frames / dialogs get dispatched.
                try:
                    self.page.wait_for_timeout(12)
                except Exception:
                    time.sleep(0.012)

                now = time.time()
                if not self.screencast_ok and not self.window_mode and now - last_fallback > 0.35:
                    self._capture_screenshot()
                    last_fallback = now
                if now - last_tabs > 1.0:
                    self._sync_tabs()
                    last_tabs = now
                if self.frame_id != last_scroll_frame and now - last_scroll_t > 0.3:
                    self._update_scroll()
                    last_scroll_frame = self.frame_id
                    last_scroll_t = now
                self._flush_typed_log()
                self._publish_state()

            try:
                self.context.close()
            except Exception:
                pass

    def _drain_commands(self, max_n: int = 40):
        for _ in range(max_n):
            try:
                action, payload, rq = self.cmd_queue.get_nowait()
            except queue.Empty:
                return
            handler = getattr(self, f"_cmd_{action}", None)
            try:
                if handler is None:
                    raise ValueError(f"Lệnh không hợp lệ: {action}")
                res = handler(payload) or {}
                res.setdefault("status", "ok")
            except Exception as e:
                self.log(f"Lỗi {action}: {_short_err(e)}")
                res = {"status": "error", "message": _short_err(e)}
            rq.put(res)
            if not self.screencast_ok:
                self._capture_screenshot()

    # ------------------------------------------------------------------ page lifecycle

    def _register_page(self, page):
        if page in self._registered_pages:
            return
        self._registered_pages.add(page)
        page.on("dialog", self._on_dialog)
        page.on("framenavigated", lambda fr, pg=page: self._on_navigated(pg, fr))
        page.on("load", lambda _=None, pg=page: self._on_load(pg))
        page.on("response", lambda resp, pg=page: self._on_response(pg, resp))
        page.on("request", lambda req, pg=page: self._on_request(pg, req))
        page.on("requestfinished", self._on_request_done)
        page.on("requestfailed", self._on_request_done)

    # Requests that never "finish" by design; they must not keep the page looking busy.
    QUIET_IGNORE = ("eventsource", "websocket", "media", "ping", "manifest")

    def _on_request(self, page, req):
        if page is not self.page:
            return
        try:
            if req.is_navigation_request() and req.frame == page.main_frame:
                self.loading = True
                self._loading_since = time.time()
            if req.resource_type in self.QUIET_IGNORE:
                return
        except Exception:
            return
        if len(self._inflight) > 500:
            self._inflight.clear()
        self._inflight[req] = time.time()

    def _on_request_done(self, req):
        self._inflight.pop(req, None)
        try:
            # A navigation that ends without a new document (204, download, aborted) never fires "load".
            if req.is_navigation_request() and req.frame == self.page.main_frame and req.failure:
                self.loading = False
        except Exception:
            pass

    def _wait_network_quiet(self, start, max_ms=3000, quiet_ms=300):
        """Content that scripts load after a click (an H5P player writing its iframe, an LMS fetching the next
        question) arrives after DOMContentLoaded. Seen on h5p.org: the agent looked 0.6 s after "Next", saw an
        empty player and pressed Next again. Waits until no recent request is in flight; requests started more
        than 5 s before the action are long polls and are ignored."""
        deadline = time.time() + max_ms / 1000
        quiet_since = None
        while time.time() < deadline:
            now = time.time()
            if any(t > start - 5 for t in list(self._inflight.values())):
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= quiet_ms / 1000:
                return
            try:
                self.page.wait_for_timeout(100)
            except Exception:
                return

    def _on_response(self, page, resp):
        """Remembers audio the page loads (players often use WebAudio/fetch, with no <audio> tag)."""
        try:
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            url = resp.url
        except Exception:
            return
        if STREAM_PLAYLIST.search(url) or STREAM_PLAYLIST.search(ctype):
            return
        if ctype.startswith("audio/") or re.search(r"\.(mp3|m4a|aac|ogg|oga|wav|weba|opus)(\?|$)", url, re.I):
            if not any(m["url"] == url for m in self._media):
                self._media.append({"url": url, "type": ctype or "audio/mpeg", "page": id(page), "t": time.time()})

    def _on_context_page(self, page):
        # New tab or popup (target=_blank, window.open): follow it like a real browser does.
        self._register_page(page)
        self._pending_page = page

    def _on_dialog(self, dialog):
        msg = (dialog.message or "")[:160]
        self.log(f"Hộp thoại '{dialog.type}' tự động chấp nhận: {msg}")
        self.bus.publish("toast", {"type": "info", "text": f"Trang hiện hộp thoại: {msg}"})
        try:
            dialog.accept()
        except Exception:
            pass

    def _on_navigated(self, page, frame):
        if frame == page.main_frame:
            # Audio captured on the previous document no longer belongs to this tab.
            stale = [m for m in self._media if m["page"] == id(page)]
            for m in stale:
                self._media.remove(m)
        if page is self.page and frame == page.main_frame:
            # Not "loading = True" here: this also fires for #hash and pushState changes (h5p.org's example
            # carousel, single-page apps like Cambridge One), which never fire "load", so the tab looked busy
            # forever. A real navigation sends a document request first (_on_request).
            self.url = page.url
            self._scroll_frame = None

    def _on_load(self, page):
        if page is self.page:
            self.loading = False

    def _attach(self, page):
        """Makes `page` the active tab and points the screencast at it."""
        self._stop_screencast()
        self.page = page
        self._ai_elements = {}
        self._inflight = {}
        self._scroll_frame = None
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            # Window mode: the page must follow the real window, never a fixed emulated size.
            if not self.window_mode and page.viewport_size != self.viewport:
                page.set_viewport_size(self.viewport)
        except Exception:
            pass
        self._start_screencast()
        self._capture_screenshot()
        self._sync_tabs()

    def _recover_page(self):
        pages = [pg for pg in self.context.pages if not pg.is_closed()]
        if not pages and self.window_mode:
            # The user closed the browser window: the desktop app shuts down with it.
            self.log("Cửa sổ trình duyệt đã đóng")
            self.window_closed = True
            self.running = False
            return
        if not pages:
            pg = self.context.new_page()
            self._register_page(pg)
            pages = [pg]
            try:
                pg.goto("https://www.google.com", timeout=20000, wait_until="domcontentloaded")
            except Exception:
                pass
        self._attach(pages[min(self.active_index, len(pages) - 1)])

    # ------------------------------------------------------------------ screencast

    def _start_screencast(self):
        if self.window_mode:
            return  # the user sees the real window; frames are not streamed
        if self._devtools_port and config.FAST_CDP:
            try:
                info = self.context.new_cdp_session(self.page)
                target_id = info.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                info.detach()
                self.fast.connect(f"ws://127.0.0.1:{self._devtools_port}/devtools/page/{target_id}",
                                  self.viewport, config.STREAM_QUALITY)
                self.screencast_ok = True
                return
            except Exception as e:
                self.fast.close()
                self.log(f"Không mở được kênh CDP nhanh, dùng kênh Playwright: {_short_err(e)}")
        try:
            cdp = self.context.new_cdp_session(self.page)
            cdp.on("Page.screencastFrame", lambda ev, c=cdp: self._on_screencast_frame(c, ev))
            cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": config.STREAM_QUALITY,
                "maxWidth": self.viewport["width"],
                "maxHeight": self.viewport["height"],
                "everyNthFrame": 1,
            })
            self._cdp = cdp
            self.screencast_ok = True
        except Exception as e:
            self._cdp = None
            self.screencast_ok = False
            self.log(f"Không bật được screencast, dùng chụp ảnh định kỳ: {_short_err(e)}")

    def _stop_screencast(self):
        self.fast.close()
        cdp, self._cdp = self._cdp, None
        if cdp is None:
            return
        try:
            cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            cdp.detach()
        except Exception:
            pass

    def _on_screencast_frame(self, cdp, ev):
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass
        if cdp is not self._cdp or self._suppress_frames:
            return
        meta = ev.get("metadata") or {}
        w = int(meta.get("deviceWidth") or self.viewport["width"])
        h = int(meta.get("deviceHeight") or self.viewport["height"])
        self._set_frame(base64.b64decode(ev["data"]), (w, h))

    def _capture_screenshot(self):
        if self._suppress_frames or self.page is None:
            return
        try:
            data = self.page.screenshot(type="jpeg", quality=config.STREAM_QUALITY, timeout=5000)
            self._set_frame(data, (self.viewport["width"], self.viewport["height"]))
        except Exception:
            pass

    def _set_frame(self, data: bytes, size):
        with self._frame_cond:
            self.frame_bytes = data
            self.frame_size = size
            self.frame_id += 1
            self._frame_cond.notify_all()

    # ------------------------------------------------------------------ state

    def _sync_tabs(self):
        tabs = []
        pages = [pg for pg in self.context.pages if not pg.is_closed()] if self.context else []
        for i, pg in enumerate(pages):
            try:
                title = pg.title() or pg.url or "Trang mới"
            except Exception:
                title = "Đang tải..."
            if pg is self.page:
                self.active_index = i
                self.title = title
                self.url = pg.url
            tabs.append({"id": i, "title": title[:60], "url": pg.url, "active": pg is self.page})
        self.tabs = tabs
        if self.window_mode and self.page is not None and not self.page.is_closed():
            try:  # the page follows the window size; keep coordinates for the AI in sync
                w, h = self.page.evaluate("[innerWidth, innerHeight]")
                self.viewport = {"width": int(w), "height": int(h)}
            except Exception:
                pass

    def _update_scroll(self):
        try:
            info = self.page.evaluate(js.SCROLL_INFO)
            self._scroll_frame = self.page.main_frame
            if info.get("maxY", 0) < 10:
                # Content players (Cambridge One...) usually scroll inside an iframe.
                best = None
                for fr in [f for f in self.page.frames if f != self.page.main_frame][:8]:
                    try:
                        fi = fr.evaluate(js.SCROLL_INFO)
                    except Exception:
                        continue
                    if fi.get("maxY", 0) > (best[1].get("maxY", 0) if best else 9):
                        best = (fr, fi)
                if best:
                    self._scroll_frame, info = best
            self.scroll_info = info
        except Exception:
            pass

    def _state_dict(self) -> dict:
        return {
            "tabs": self.tabs,
            "active_url": self.url,
            "title": self.title,
            # Safety net for a navigation whose "load" never comes (a page that keeps loading resources forever).
            "loading": self.loading and time.time() - self._loading_since < 45,
            "scroll": self.scroll_info,
            "viewport": self.viewport,
            "screencast": self.screencast_ok,
            "mode": "window" if self.window_mode else "stream",
            "ready": self.ready.is_set() and self.alive,
        }

    def _publish_state(self):
        state = self._state_dict()
        encoded = json.dumps(state, sort_keys=True, ensure_ascii=False)
        if encoded != self._last_state_json:
            self._last_state_json = encoded
            self.bus.publish("state", state)

    def _flush_typed_log(self):
        if self._typed_buffer:
            self.log(f"Gõ: {self._typed_buffer[:40]}")
            self._typed_buffer = ""

    def _ai_point(self, x, y, kind):
        self.ai_pointer = {"x": int(x), "y": int(y), "kind": kind, "id": time.time()}
        self.bus.publish("ai_pointer", self.ai_pointer)
        if self.window_mode:
            try:  # show where the AI acts directly in the real window
                self.page.evaluate(js.AI_RIPPLE, {"x": int(x), "y": int(y)})
            except Exception:
                pass

    # ------------------------------------------------------------------ frames helpers

    def _visible_frames(self, include_offscreen=False):
        """Main frame + child iframes (on-screen ones, or all with include_offscreen) and their offset."""
        main = self.page.main_frame
        out = [(main, 0.0, 0.0)]
        vw, vh = self.viewport["width"], self.viewport["height"]
        for fr in self.page.frames:
            if len(out) >= 14:
                break
            if fr == main or any(fr == o[0] for o in out):
                continue  # page.frames order is not guaranteed; never annotate a frame twice
            try:
                if fr.is_detached():
                    continue
                box = fr.frame_element().bounding_box()
            except Exception:
                continue
            if not box or box["width"] < 20 or box["height"] < 20:
                continue
            offscreen = box["x"] > vw or box["y"] > vh or box["x"] + box["width"] < 0 or box["y"] + box["height"] < 0
            if offscreen and not include_offscreen:
                continue
            out.append((fr, box["x"], box["y"]))
        return out

    def _all_frames_eval(self, script, arg=None, include_offscreen=False):
        results = []
        for fr, _, _ in self._visible_frames(include_offscreen):
            try:
                results.append(fr.evaluate(script, arg) if arg is not None else fr.evaluate(script))
            except Exception:
                continue
        return results

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return "about:blank"
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            return url
        if " " in url or "." not in url:
            return f"https://www.google.com/search?q={quote_plus(url)}"
        return "https://" + url

    # ------------------------------------------------------------------ commands: navigation

    def _cmd_goto(self, p):
        url = self._normalize_url(p.get("url", ""))
        self.log(f"Chuyển tới: {url[:90]}")
        self.loading, self._loading_since = True, time.time()
        try:
            self.page.goto(url, timeout=30000, wait_until="domcontentloaded")
        finally:
            self._sync_tabs()
        return {"url": self.page.url}

    def _cmd_back(self, p):
        self.page.go_back(timeout=15000, wait_until="domcontentloaded")
        self._sync_tabs()

    def _cmd_forward(self, p):
        self.page.go_forward(timeout=15000, wait_until="domcontentloaded")
        self._sync_tabs()

    def _cmd_reload(self, p):
        self.page.reload(timeout=30000, wait_until="domcontentloaded")
        self._sync_tabs()

    def _cmd_stop_loading(self, p):
        self.page.evaluate("window.stop()")
        self.loading = False

    def _cmd_action(self, p):
        # Backwards-compatible /api/action {"action": "back" | "reload" | ...}
        name = p.get("action", "")
        if name in ("back", "forward", "reload", "stop_loading"):
            return getattr(self, f"_cmd_{name}")(p)
        raise ValueError(f"Hành động không hợp lệ: {name}")

    # ------------------------------------------------------------------ commands: tabs

    def _cmd_new_tab(self, p):
        pg = self.context.new_page()
        self._register_page(pg)
        self._pending_page = None
        self._attach(pg)
        url = p.get("url") or "https://www.google.com"
        try:
            pg.goto(self._normalize_url(url), timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            self.log(f"Tab mới lỗi tải trang: {_short_err(e)}")
        self._sync_tabs()
        self.log("Đã mở tab mới")

    def _cmd_switch_tab(self, p):
        pages = [pg for pg in self.context.pages if not pg.is_closed()]
        idx = int(p.get("index", 0))
        if 0 <= idx < len(pages):
            self._attach(pages[idx])

    def _cmd_close_tab(self, p):
        pages = [pg for pg in self.context.pages if not pg.is_closed()]
        idx = int(p.get("index", self.active_index))
        if len(pages) > 1 and 0 <= idx < len(pages):
            target = pages[idx]
            was_active = target is self.page
            target.close()
            if was_active:
                self.active_index = max(0, idx - 1)
                self._recover_page()
            self._sync_tabs()

    # ------------------------------------------------------------------ commands: input

    def _clamp_xy(self, ev):
        x = float(ev.get("x", self.mouse["x"]))
        y = float(ev.get("y", self.mouse["y"]))
        x = max(0.0, min(self.viewport["width"] - 1, x))
        y = max(0.0, min(self.viewport["height"] - 1, y))
        return x, y

    def _cmd_input(self, p):
        """Replays a batch of raw user input events in order (mouse, wheel, keys, text)."""
        mouse, kb = self.page.mouse, self.page.keyboard
        for ev in (p.get("events") or [])[:300]:
            t = ev.get("t")
            button = ev.get("button", "left")
            if button not in MOUSE_BUTTONS:
                button = "left"
            count = max(1, min(3, int(ev.get("count", 1))))
            if t in ("move", "down", "up", "click", "wheel"):
                x, y = self._clamp_xy(ev)
                self.mouse = {"x": int(x), "y": int(y)}
                if t == "move":
                    mouse.move(x, y)
                elif t == "down":
                    mouse.move(x, y)
                    mouse.down(button=button, click_count=count)
                elif t == "up":
                    mouse.move(x, y)
                    mouse.up(button=button, click_count=count)
                    if count == 1 and button == "left":
                        self.log(f"Click ({int(x)}, {int(y)})")
                elif t == "click":
                    mouse.click(x, y, button=button, click_count=count)
                    self.log(f"Click ({int(x)}, {int(y)})")
                else:
                    mouse.move(x, y)
                    mouse.wheel(float(ev.get("dx", 0)), float(ev.get("dy", 0)))
            elif t == "key":
                key = str(ev.get("key", ""))
                if key:
                    kb.press(key)
                    if key not in ("Backspace",):
                        self._flush_typed_log()
                        self.log(f"Phím: {key}")
                    elif self._typed_buffer:
                        self._typed_buffer = self._typed_buffer[:-1]
            elif t == "text":
                text = str(ev.get("text", ""))
                if text:
                    kb.insert_text(text)
                    self._typed_buffer += text
        return {}

    # Legacy endpoints kept for ai_browser.py and older scripts
    def _cmd_click(self, p):
        return self._cmd_input({"events": [{"t": "click", "x": p.get("x", 0), "y": p.get("y", 0),
                                             "button": p.get("button", "left")}]})

    def _cmd_type(self, p):
        if p.get("key"):
            return self._cmd_input({"events": [{"t": "key", "key": p["key"]}]})
        return self._cmd_input({"events": [{"t": "text", "text": p.get("text", "")}]})

    def _cmd_scroll(self, p):
        if p.get("targetRatio") is not None:
            return self._cmd_scroll_ratio({"ratio": p["targetRatio"]})
        x = p.get("x", self.viewport["width"] / 2)
        y = p.get("y", self.viewport["height"] / 2)
        self._cmd_input({"events": [{"t": "wheel", "x": x, "y": y, "dx": p.get("deltaX", 0), "dy": p.get("deltaY", 0)}]})
        self.page.wait_for_timeout(60)
        self._update_scroll()
        return {"scroll": self.scroll_info}

    def _cmd_scroll_ratio(self, p):
        ratio = max(0.0, min(1.0, float(p.get("ratio", 0))))
        frame = self._scroll_frame or self.page.main_frame
        try:
            frame.evaluate(js.SCROLL_TO_RATIO, ratio)
        except Exception:
            self.page.evaluate(js.SCROLL_TO_RATIO, ratio)
        self._update_scroll()
        return {"scroll": self.scroll_info}

    def _cmd_set_viewport(self, p):
        if self.window_mode:
            return {"viewport": self.viewport, "note": "Ở chế độ cửa sổ, hãy đổi kích thước cửa sổ trình duyệt trực tiếp."}
        w =max(480, min(2560, int(p.get("width", config.VIEWPORT_WIDTH))))
        h = max(360, min(1600, int(p.get("height", config.VIEWPORT_HEIGHT))))
        self.viewport = {"width": w, "height": h}
        for pg in self.context.pages:
            try:
                pg.set_viewport_size(self.viewport)
            except Exception:
                pass
        self._stop_screencast()
        self._start_screencast()
        self._capture_screenshot()
        self.log(f"Kích thước màn hình: {w}x{h}")
        return {"viewport": self.viewport}

    # ------------------------------------------------------------------ commands: text

    def _cmd_eval(self, p):
        return {"result": self.page.evaluate(p.get("script", ""))}

    def _cmd_fetch_media(self, p):
        """Downloads a media URL with the browser's cookies (so signed/protected LMS audio works)."""
        url = p.get("url", "")
        if not url.startswith(("http://", "https://", "file://")):
            raise ValueError("URL media không hợp lệ")
        max_bytes = int(p.get("max_bytes", 20 * 1024 * 1024))
        if url.startswith("file://"):
            from urllib.parse import unquote, urlparse
            path = unquote(urlparse(url).path).lstrip("/")
            if not re.search(r"\.(mp3|m4a|aac|ogg|oga|wav|weba|opus)$", path, re.I):
                raise ValueError("Chỉ đọc được file audio cục bộ")
            with open(path, "rb") as f:
                body = f.read(max_bytes + 1)
            ctype = "audio/wav" if path.lower().endswith(".wav") else "audio/mpeg"
        else:
            resp = self.context.request.get(url, timeout=30000, headers={"Referer": self.page.url})
            if not resp.ok:
                raise ValueError(f"Tải audio lỗi HTTP {resp.status}")
            body = resp.body()
            ctype = (resp.headers.get("content-type") or "audio/mpeg").split(";")[0].strip()
        if len(body) > max_bytes:
            raise ValueError(f"Audio quá lớn ({len(body) // 1024 // 1024}MB)")
        if STREAM_PLAYLIST.search(url) or STREAM_PLAYLIST.search(ctype) or ctype.startswith(("text/", "application/json")):
            raise ValueError("đây là danh sách phát luồng (HLS/DASH) hoặc trang, không phải file audio")
        if ctype in ("application/octet-stream", "binary/octet-stream", ""):
            ext = re.search(r"\.(\w+)(\?|$)", url)
            ctype = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "ogg": "audio/ogg",
                     "wav": "audio/wav", "opus": "audio/ogg"}.get(ext.group(1).lower() if ext else "", "audio/mpeg")
        return {"data": body, "mime": ctype, "size": len(body)}

    def _form_status(self):
        total, items, remaining = 0, [], 0
        for res in self._all_frames_eval(js.FORM_STATUS, include_offscreen=True):
            if res:
                total += res.get("total", 0)
                items += res.get("items", [])
                remaining = max(remaining, res.get("remaining", 0))
        if remaining > len(items):
            # Questions hidden behind "Next question": counted from the page's own "N items remaining".
            items.append({"kind": "hidden", "aid": None, "where": "other pages of the quiz",
                          "context": f"the page says {remaining} question(s) are still unanswered (shown one at a time)"})
            return {"total": max(total, remaining), "unanswered": remaining, "items": items[:20]}
        return {"total": total, "unanswered": len(items), "items": items[:20]}

    def _cmd_form_status(self, p):
        """Unanswered questions on the whole page (all frames), used before pressing Submit."""
        return self._form_status()

    def _cmd_find_and_go(self, p):
        """Finds a section/link anywhere on the page (all frames, off-screen too) and opens it.
        Returns {"opened": {...}} when one candidate clearly wins, else {"candidates": [...]} for the AI/user."""
        query = str(p.get("query", "")).strip()
        cands = []
        for fr, _, _ in self._visible_frames(include_offscreen=True):
            try:
                for c in fr.evaluate(js.FIND_TARGETS, query) or []:
                    cands.append((c, fr))
            except Exception:
                continue
        cands.sort(key=lambda cf: -cf[0]["score"])
        public = [c for c, _ in cands[:8]]
        if not cands:
            return {"opened": None, "candidates": []}
        best, fr = cands[0]
        runner_up = cands[1][0]["score"] if len(cands) > 1 else 0
        same_text = len(cands) > 1 and cands[1][0]["text"].lower() == best["text"].lower()
        clear = best["score"] >= 68 and (best["score"] - runner_up >= 8 or same_text)
        if not clear and not p.get("force"):
            return {"opened": None, "candidates": public}
        loc = fr.locator(f'[data-find="{best["idx"]}"]').first
        loc.scroll_into_view_if_needed(timeout=3000)
        box = loc.bounding_box(timeout=2000)
        if box:
            self._ai_point(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, "click")
        if best["kind"] == "link":
            try:
                loc.click(timeout=3000)
            except Exception:
                loc.click(timeout=2000, force=True)
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
        self.log(f"Tìm & mở: {best['text'][:60]}")
        return {"opened": best, "candidates": public}

    def _cmd_eval_frames(self, p):
        """Evaluates a script in every visible frame; returns the list of results (errors skipped)."""
        return {"results": self._all_frames_eval(p.get("script", "undefined"))}

    def _cmd_extract_text(self, p):
        texts = [t for t in self._all_frames_eval(js.FRAME_TEXT, 20000) if t]
        return {"text": "\n\n".join(texts)}

    def _cmd_page_text(self, p):
        blocks = []
        for res in self._all_frames_eval(js.FRAME_TEXT_BLOCKS):
            blocks.extend(res or [])
        unique = list(dict.fromkeys(blocks))[:300]
        return {"analysis": {"url": self.page.url, "title": self.title, "textBlocks": unique}}

    def _cmd_get_selection(self, p):
        best = ""
        for t in self._all_frames_eval(js.FRAME_SELECTION):
            if t and len(t) > len(best):
                best = t
        return {"text": best.strip()}

    def _cmd_select_text(self, p):
        sx, sy = p.get("startX", 0), p.get("startY", 0)
        ex, ey = p.get("endX", 0), p.get("endY", 0)
        if abs(ex - sx) > 3 or abs(ey - sy) > 3:
            m = self.page.mouse
            m.move(sx, sy)
            m.down()
            m.move(ex, ey, steps=6)
            m.up()
        return self._cmd_get_selection(p)

    # ------------------------------------------------------------------ drag & drop engine

    # Order matters: real-input strategies first (pass isTrusted checks), synthetic events last.
    DRAG_STRATEGIES = ("playwright", "mouse_steps", "mouse_hold", "touch", "html5_events", "pointer_events", "click_place")

    def _dnd_snapshot(self, frame, eid):
        try:
            return frame.evaluate(js.DND_SNAPSHOT, f'[data-aid="{eid}"]')
        except Exception:
            return {"gone": True}

    def _dnd_changed(self, before, after) -> bool:
        (sb, tb), (sa, ta) = before, after
        if not sb.get("gone") and sa.get("gone"):
            return True  # source consumed / moved to another container and re-rendered
        if ta.get("gone") != tb.get("gone"):
            return True
        if ta.get("sig") != tb.get("sig"):
            return True  # target content changed (word placed, value set)
        if sa.get("parentSig") != sb.get("parentSig") or sa.get("index") != sb.get("index"):
            return True  # source re-parented or reordered (sortable lists)
        return False

    def _drag(self, sframe, sinfo, tframe, tinfo, only=None):
        """Tries each strategy until the DOM proves the drop happened. Returns (method or None, tried)."""
        sid, tid = sinfo["id"], tinfo["id"]
        host = re.sub(r"^https?://([^/]+).*$", r"\1", self.page.url or "")
        order = list(self.DRAG_STRATEGIES)
        preferred = self._dnd_pref.get(host)
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        if only in order:
            order = [only]
        # Mouse/touch strategies need both ends on screen (the element list also covers off-screen items).
        for fr, eid in ((sframe, sid), (tframe, tid), (sframe, sid)):
            try:
                fr.locator(f'[data-aid="{eid}"]').first.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
        tried = []
        for name in order:
            if name in ("html5_events", "pointer_events") and sframe is not tframe:
                continue  # synthetic events cannot cross frame boundaries
            before = (self._dnd_snapshot(sframe, sid), self._dnd_snapshot(tframe, tid))
            if before[0].get("gone"):
                break
            # Re-read live coordinates: earlier attempts or animations may have moved things.
            s = self._live_center(sframe, sid, sinfo)
            t = self._live_center(tframe, tid, tinfo)
            try:
                getattr(self, f"_dnd_{name}")(sframe, sid, s, tframe, tid, t)
            except Exception as e:
                self.log(f"Kéo thả '{name}' lỗi: {_short_err(e)}")
            finally:
                try:
                    self.page.mouse.up()
                except Exception:
                    pass
            self.page.wait_for_timeout(350)
            after = (self._dnd_snapshot(sframe, sid), self._dnd_snapshot(tframe, tid))
            if self._dnd_changed(before, after):
                # Revert animations (jQuery UI revert, Sortable ghost) settle a bit later: confirm.
                self.page.wait_for_timeout(500)
                settled = (self._dnd_snapshot(sframe, sid), self._dnd_snapshot(tframe, tid))
                if self._dnd_changed(before, settled):
                    self._dnd_pref[host] = name
                    return name, tried + [name]
            tried.append(name)
        return None, tried

    def _live_center(self, frame, eid, info):
        """Element centre in main-viewport coordinates, accounting for the frame offset."""
        try:
            box = frame.locator(f'[data-aid="{eid}"]').first.bounding_box(timeout=1000)
            if box:
                return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        except Exception:
            pass
        return info["x"], info["y"]

    def _dnd_playwright(self, sframe, sid, s, tframe, tid, t):
        src = sframe.locator(f'[data-aid="{sid}"]').first
        tgt = tframe.locator(f'[data-aid="{tid}"]').first
        if sframe is tframe:
            src.drag_to(tgt, timeout=4000)
        else:
            src.hover(timeout=2000)
            self.page.mouse.down()
            tgt.hover(timeout=2000)
            self.page.mouse.up()

    def _dnd_mouse_steps(self, sframe, sid, s, tframe, tid, t):
        m = self.page.mouse
        m.move(s[0], s[1])
        m.down()
        m.move(s[0] + 6, s[1] + 6, steps=4)   # exceed library drag thresholds (3-10px)
        self.page.wait_for_timeout(60)
        m.move(t[0], t[1], steps=22)
        self.page.wait_for_timeout(80)
        m.move(t[0] + 2, t[1] + 1, steps=2)   # many libs only hit-test on a move *inside* the target
        self.page.wait_for_timeout(60)
        m.up()

    def _dnd_mouse_hold(self, sframe, sid, s, tframe, tid, t):
        m = self.page.mouse
        m.move(s[0], s[1])
        m.down()
        self.page.wait_for_timeout(700)       # press-and-hold activation (delay sensors, long-press)
        m.move(s[0] + 3, s[1] + 3, steps=3)
        self.page.wait_for_timeout(120)
        m.move(t[0], t[1], steps=35)
        self.page.wait_for_timeout(250)
        m.move(t[0] + 1, t[1] + 2, steps=3)
        self.page.wait_for_timeout(150)
        m.up()

    def _dnd_touch(self, sframe, sid, s, tframe, tid, t):
        cdp = self._cdp or self.context.new_cdp_session(self.page)

        def touch(kind, pts):
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y} for x, y in pts]})

        touch("touchStart", [s])
        self.page.wait_for_timeout(120)
        steps = 18
        for i in range(1, steps + 1):
            k = i / steps
            touch("touchMove", [(s[0] + (t[0] - s[0]) * k, s[1] + (t[1] - s[1]) * k)])
            self.page.wait_for_timeout(16)
        self.page.wait_for_timeout(80)
        touch("touchEnd", [])

    def _dnd_html5_events(self, sframe, sid, s, tframe, tid, t):
        sframe.evaluate(js.DND_HTML5_EVENTS, {"src": f'[data-aid="{sid}"]', "tgt": f'[data-aid="{tid}"]'})

    def _dnd_pointer_events(self, sframe, sid, s, tframe, tid, t):
        sframe.evaluate(js.DND_POINTER_EVENTS, {"src": f'[data-aid="{sid}"]', "tgt": f'[data-aid="{tid}"]'})

    def _dnd_click_place(self, sframe, sid, s, tframe, tid, t):
        # "Tap a word, then tap the gap" mode offered by many e-learning players (incl. accessibility modes).
        sframe.locator(f'[data-aid="{sid}"]').first.click(timeout=2000)
        self.page.wait_for_timeout(200)
        tframe.locator(f'[data-aid="{tid}"]').first.click(timeout=2000)

    # ------------------------------------------------------------------ commands: AI agent

    def _cmd_ai_observe(self, p):
        """Annotates interactive elements of the whole page (all frames, also off-screen parts),
        then captures a Set-of-Marks screenshot of the visible area and the page's form status."""
        page = self.page
        frames = self._visible_frames(include_offscreen=True)
        limit = int(p.get("limit", 260))
        elements, next_id = [], 1
        self._ai_elements = {}
        vw, vh = self.viewport["width"], self.viewport["height"]
        annotated = []
        # Exercise players (H5P, LMS activities) usually live in iframes while the outer page is menus and
        # links. Annotating the main frame first let a link-heavy page (h5p.org: 260+) use the whole budget,
        # so the exercise itself was invisible. Iframes go first with a shared cap; the main frame keeps the rest
        # (at least MAIN_RESERVE, enough for its Submit/Next buttons and menus).
        main_reserve = min(limit, 120)
        order = list(range(1, len(frames))) + [0]

        for fi in order:
            fr, ox, oy = frames[fi]
            room = limit - len(elements) if fi == 0 else (limit - main_reserve) - len(elements)
            if room <= 0:
                continue
            try:
                res = fr.evaluate(js.ANNOTATE_ELEMENTS, {"start": next_id, "limit": room})
            except Exception:
                continue
            annotated.append(fr)
            next_id = res.get("next", next_id)
            for el in res.get("elements", []):
                el["x"] = int(el["x"] + ox)
                el["y"] = int(el["y"] + oy)
                el["left"] = int(el["left"] + ox)
                el["top"] = int(el["top"] + oy)
                if not el.get("offscreen") and not (0 <= el["x"] <= vw and 0 <= el["y"] <= vh):
                    # Inside the frame's view but outside the main window (tall/offset iframe).
                    el["offscreen"] = "below" if el["y"] > vh else "above" if el["y"] < 0 else "side"
                if fi > 0:
                    el["frame"] = fi
                self._ai_elements[el["id"]] = (fr, el)
                elements.append(el)

        texts = []
        for fi, (fr, _, _) in enumerate(frames):
            try:
                t = fr.evaluate(js.FRAME_TEXT, 15000)  # whole document text, incl. what is scrolled away
            except Exception:
                continue
            if t and len(t) > 2:
                texts.append({"frame": fi, "url": fr.url[:120], "text": t})

        shot = None
        quality = config.AI_SCREENSHOT_QUALITY
        if p.get("marks", True) and elements:
            self._suppress_frames = True
            try:
                for fr in annotated:
                    try:
                        fr.evaluate(js.DRAW_MARKS)
                    except Exception:
                        pass
                shot = page.screenshot(type="jpeg", quality=quality, timeout=10000)
            except Exception:
                shot = None
            finally:
                for fr in annotated:
                    try:
                        fr.evaluate(js.REMOVE_MARKS)
                    except Exception:
                        pass
                self._suppress_frames = False
        if shot is None:
            try:
                shot = page.screenshot(type="jpeg", quality=quality, timeout=10000)
            except Exception:
                shot = self.frame_bytes

        # Audio: <audio>/<video> sources in any frame + audio files seen on the network for this tab.
        media = []
        for res in self._all_frames_eval(js.MEDIA_SOURCES):
            for m in res or []:
                if m.get("src") and not m["src"].startswith(("blob:", "data:")) and not STREAM_PLAYLIST.search(m["src"]):
                    media.append(m)
        for m in self._media:
            if m["page"] == id(page) and not any(x["src"] == m["url"] for x in media):
                media.append({"src": m["url"], "type": m["type"], "source": "network"})

        self._update_scroll()
        return {
            "url": page.url,
            "title": self.title,
            "elements": elements,
            "texts": texts,
            "media": media[:8],
            "form_status": self._form_status(),
            "screenshot": shot,
            "viewport": self.viewport,
            "scroll": self.scroll_info,
        }

    def _cmd_ai_act(self, a):
        kind = a.get("action")
        started = time.time()
        page, kb, mouse = self.page, self.page.keyboard, self.page.mouse
        loc = info = None
        result = {"status": "ok"}

        if kind in ELEMENT_ACTIONS:
            try:
                eid = int(a.get("id"))
            except (TypeError, ValueError):
                return {"status": "error", "message": f"Thiếu id phần tử cho '{kind}'"}
            entry = self._ai_elements.get(eid)
            if not entry:
                return {"status": "error", "message": f"Phần tử #{eid} không có trong lần quan sát gần nhất"}
            frame, info = entry
            loc = frame.locator(f'[data-aid="{eid}"]').first
            try:
                if loc.count() == 0:
                    return {"status": "error", "message": f"Phần tử #{eid} đã biến mất khỏi trang"}
            except Exception:
                return {"status": "error", "message": f"Phần tử #{eid} không còn truy cập được"}
            self._ai_point(info["x"], info["y"], kind)

        if kind == "click":
            try:
                loc.click(timeout=2500)
            except Exception:
                try:
                    loc.click(timeout=1500, force=True)
                except Exception:
                    # Where the element is now, not where it was when the page was observed: the LLM step may
                    # have taken a minute, and late ads shift the layout (a stale point can land on an ad).
                    try:
                        box = loc.bounding_box(timeout=1000)
                    except Exception:
                        box = None
                    if not box or box["width"] < 1 or box["height"] < 1:
                        return {"status": "error", "message": f"Không bấm được #{eid}: phần tử không còn hiển thị"}
                    mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            self.log(f"AI click #{info['id']} {info.get('text') or info.get('label') or ''}"[:90])

        elif kind == "type":
            text = str(a.get("text", ""))
            name = (info.get("text") or info.get("label") or "")[:30]
            not_field = {"status": "error", "message": f"#{eid} «{name}» không phải ô nhập chữ nên không gõ "
                                                       "(tránh gõ nhầm vào ô khác đang được chọn)"}
            # A model that reuses an id from an earlier step can aim "type" at a link or button. Clicking it
            # and then pressing Ctrl+A/Delete would type into whatever has focus (seen: h5p.org's search box).
            if not info.get("editable") and (info.get("tag") in ("a", "button", "select", "option") or info.get("toggle")
                                             or info.get("role") in ("button", "link", "tab", "radio", "checkbox", "option",
                                                                     "menuitem", "switch")):
                return not_field

            def owns_focus():
                try:
                    return bool(frame.evaluate(js.OWNS_TEXT_FOCUS, eid))
                except Exception:
                    return False

            try:
                loc.evaluate(js.MARK_TYPE_TARGET, timeout=1500)
            except Exception:
                pass
            if info.get("editable") in (None, "content"):
                loc.click(timeout=2500)
                if not owns_focus():
                    return not_field
                kb.press("Control+A")
                kb.press("Delete")
                kb.insert_text(text)
            else:
                try:
                    loc.fill(text, timeout=2500)
                except Exception:
                    loc.click(timeout=2500, force=True)
                    if not owns_focus():
                        return {"status": "error", "message": f"Không điền được #{eid}: ô không nhận được con trỏ"}
                    kb.press("Control+A")
                    kb.insert_text(text)
            if a.get("enter"):
                kb.press("Enter")
            try:
                # Bounded: an H5P player re-renders after input and the default locator wait is 30 s,
                # as long as the caller's own timeout, which then made every later command time out too.
                result["value"] = (loc.evaluate(js.READ_FIELD_VALUE, timeout=1500) or "")[:200]
            except Exception:
                pass
            self.log(f"AI điền #{info['id']}: {text[:40]}")

        elif kind == "select":
            option = str(a.get("option") or a.get("text") or "")
            if info.get("tag") == "select":
                try:
                    loc.select_option(label=option, timeout=2500)
                except Exception:
                    loc.select_option(value=option, timeout=2500)
                self.log(f"AI chọn #{info['id']}: {option[:40]}")
            else:
                loc.click(timeout=2500)
                result["note"] = "Không phải <select> gốc: đã click để mở danh sách, hãy chọn mục ở bước sau."

        elif kind == "drag":
            try:
                tid = int(a.get("target_id"))
            except (TypeError, ValueError):
                return {"status": "error", "message": "Thiếu target_id cho drag"}
            tentry = self._ai_elements.get(tid)
            if not tentry:
                return {"status": "error", "message": f"Phần tử đích #{tid} không tồn tại"}
            tframe, tinfo = tentry
            method, tried = self._drag(frame, info, tframe, tinfo, only=a.get("method"))
            self._ai_point(tinfo["x"], tinfo["y"], "drop")
            if not method:
                self.log(f"AI kéo #{info['id']} → #{tid}: không cách nào có tác dụng ({', '.join(tried)})")
                return {"status": "error", "message": f"Kéo thả không làm thay đổi trang (đã thử: {', '.join(tried)})",
                        "tried": tried}
            result["method"] = method
            result["tried"] = tried
            self.log(f"AI kéo #{info['id']} → #{tid} bằng cách '{method}'")

        elif kind == "hover":
            loc.hover(timeout=2500)

        elif kind == "scroll":
            amount = max(100, min(3000, int(a.get("amount") or 600)))
            dy = -amount if str(a.get("direction", "down")).lower() == "up" else amount
            x, y = self.viewport["width"] / 2, self.viewport["height"] / 2
            entry = self._ai_elements.get(int(a["id"])) if str(a.get("id", "")).isdigit() else None
            if entry:
                x, y = entry[1]["x"], entry[1]["y"]
            mouse.move(x, y)
            mouse.wheel(0, dy)
            self.log(f"AI cuộn {'lên' if dy < 0 else 'xuống'} {amount}px")

        elif kind == "press":
            key = str(a.get("key", "")).strip()
            if not key:
                return {"status": "error", "message": "Thiếu tên phím"}
            kb.press(key)
            self.log(f"AI bấm phím {key}")

        elif kind == "navigate":
            self._cmd_goto({"url": a.get("url", "")})

        elif kind == "wait":
            page.wait_for_timeout(max(100, min(5000, int(a.get("ms") or 1000))))

        else:
            return {"status": "error", "message": f"Hành động không hỗ trợ: {kind}"}

        # Let the page react (navigation, re-render) before the agent looks again.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(int(a.get("_settle_ms", 250)))
        except Exception:
            pass
        if int(a.get("_settle_ms", 250)) >= 600 and kind in ("click", "press", "navigate", "select", "type", "drag"):
            self._wait_network_quiet(started)  # last action of an agent step: the next thing is a new observation
        return result
