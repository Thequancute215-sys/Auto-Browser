import json
import mimetypes
import os
import struct
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def cleanup_zombie_processes():
    """Kill dangling chrome-headless-shell processes from previous crashed sessions."""
    if sys.platform == "win32" and config.KILL_STALE_BROWSERS:
        try:
            import subprocess
            subprocess.run(["taskkill", "/F", "/IM", "chrome-headless-shell.exe", "/T"], capture_output=True)
        except Exception:
            pass


def setup_windows_job_object():
    """Binds Python and all child processes to a Windows Job Object to guarantee cleanup on exit."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoCounters", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        proc = kernel32.GetCurrentProcess()
        kernel32.AssignProcessToJobObject(job, proc)
        return job
    except Exception:
        return None


# Clean up any leftover zombie processes and register auto-cleanup job
cleanup_zombie_processes()
_win_job = setup_windows_job_object()

from events import EventBus  # noqa: E402
from browser_manager import BrowserManager, COMMAND_WHITELIST  # noqa: E402
from ai_controller import AIController  # noqa: E402

bus = EventBus()
browser = BrowserManager(bus)
ai = AIController(browser, bus)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
FRAME_HEADER = struct.Struct(">IHH")  # jpeg length, width, height

# POST route -> (browser command, payload builder)
BROWSER_ROUTES = {
    "/api/input": "input",
    "/api/click": "click",
    "/api/type": "type",
    "/api/goto": "goto",
    "/api/scroll": "scroll",
    "/api/scroll_ratio": "scroll_ratio",
    "/api/action": "action",
    "/api/tab/new": "new_tab",
    "/api/tab/switch": "switch_tab",
    "/api/tab/close": "close_tab",
    "/api/select_text": "select_text",
    "/api/viewport": "set_viewport",
}


class AppRequestHandler(BaseHTTPRequestHandler):
    server_version = "AIBrowserHub/5.0"

    # ------------------------------------------------------------------ helpers

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def _request_allowed(self) -> bool:
        """Blocks DNS-rebinding and cross-site requests: the browser behind this API holds your logins."""
        if config.ALLOW_LAN:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host and host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            if (urlparse(origin).hostname or "").lower() not in LOCAL_HOSTS:
                return False
        return True

    def _serve_static(self, rel_path: str):
        base = os.path.realpath(config.STATIC_DIR)
        full = os.path.realpath(os.path.join(base, rel_path))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            self._send_json({"error": "Not found"}, 404)
            return
        ctype, _ = mimetypes.guess_type(full)
        if full.endswith(".js"):
            ctype = "application/javascript"
        ctype = (ctype or "application/octet-stream")
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ValueError("Payload too large")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------ streaming endpoints

    def _stream_frames(self):
        """Length-prefixed JPEG stream: [u32 len][u16 w][u16 h][jpeg]... Zero-length frames are keep-alives."""
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = -1
        try:
            while True:
                fid, data, (w, h) = browser.wait_frame(last, timeout=8.0)
                if fid == last or not data:
                    self.wfile.write(FRAME_HEADER.pack(0, 0, 0))
                    continue
                last = fid
                self.wfile.write(FRAME_HEADER.pack(len(data), w, h) + data)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            return

    def _stream_events(self):
        """Server-Sent Events: state, chat messages, AI status, logs, pointer animations."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def send(event, data):
            payload = json.dumps(data, ensure_ascii=False, default=str)
            self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))

        try:
            cursor = bus.seq
            send("hello", {"state": browser.get_state(), "ai": ai.get_status()})
            while True:
                events = bus.wait(cursor, timeout=15.0)
                if not events:
                    self.wfile.write(b": ping\n\n")
                    continue
                for seq, etype, data in events:
                    send(etype, data)
                    cursor = seq
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            return

    # ------------------------------------------------------------------ routes

    def do_GET(self):
        if not self._request_allowed():
            self._send_json({"error": "Forbidden"}, 403)
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif not path.startswith("/api/"):
            self._serve_static(path.lstrip("/"))
        elif path == "/api/stream":
            self._stream_frames()
        elif path == "/api/events":
            self._stream_events()
        elif path == "/api/screenshot":
            fid = parse_qs(parsed.query).get("fid", [None])[0]
            current = str(browser.frame_id)
            if fid == current or (self.headers.get("If-None-Match") or "").strip('"') == current:
                self.send_response(304)
                self.send_header("ETag", f'"{current}"')
                self.end_headers()
                return
            shot = browser.get_screenshot()
            if not shot:
                self._send_json({"error": "No screenshot available"}, 404)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(shot)))
                self.send_header("ETag", f'"{current}"')
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(shot)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass
        elif path == "/api/state":
            self._send_json(browser.get_state())
        elif path == "/api/ai/status":
            self._send_json(ai.get_status())
        elif path == "/api/ai/config":
            self._send_json(ai.get_config())
        elif path == "/api/ai/models":
            try:
                self._send_json({"status": "ok", "models": ai.list_models()})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)[:300], "models": []})
        elif path == "/api/page/text":
            res = browser.execute("page_text", {})
            self._send_json(res.get("analysis", {"textBlocks": [], "error": res.get("message")}))
        elif path == "/api/selection":
            self._send_json(browser.execute("get_selection", {}))
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._request_allowed():
            self._send_json({"error": "Forbidden"}, 403)
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send_json({"status": "error", "message": str(e)}, 413)
            return

        if path == "/api/ai/command":
            self._send_json(ai.run_command(payload.get("command", ""), payload.get("overrides")))
        elif path == "/api/ai/stop":
            ai.stop()
            self._send_json({"status": "ok"})
        elif path == "/api/ai/clear":
            ai.clear_messages()
            self._send_json({"status": "ok"})
        elif path == "/api/ai/config":
            ai.save_config(payload)
            ok, msg = True, "Đã lưu cài đặt."
            if payload.get("test"):
                if not ai.api_key:
                    ok, msg = False, "Chưa có API Key để kiểm tra."
                else:
                    try:
                        msg = f"Kết nối Gemini thành công: {ai.test_connection()}"
                    except Exception as e:
                        ok, msg = False, f"Lỗi kết nối: {e}"
            self._send_json({"status": "ok" if ok else "error", "message": msg, "config": ai.get_config()})
        elif path == "/api/ai_command":
            cmd = payload.get("cmd", "")
            if cmd not in COMMAND_WHITELIST:
                self._send_json({"status": "error", "message": f"Lệnh không được phép: {cmd}"}, 400)
            else:
                self._send_json(browser.execute(cmd, payload.get("payload", {})))
        elif path in BROWSER_ROUTES:
            self._send_json(browser.execute(BROWSER_ROUTES[path], payload))
        else:
            self._send_json({"error": "Invalid API endpoint"}, 404)

    def log_message(self, format, *args):
        return


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = Server((config.HOST, config.PORT), AppRequestHandler)
    where = "mạng LAN + localhost" if config.ALLOW_LAN else "chỉ máy này"
    print(f"=== AI Interactive Browser Hub: http://localhost:{config.PORT} ({where}) ===", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Đang tắt...", flush=True)
    finally:
        browser.stop()
        server.server_close()


if __name__ == "__main__":
    main()
