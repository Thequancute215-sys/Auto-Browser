"""
Direct Chrome DevTools Protocol channel for the latency-critical path: live frames and user input.

Playwright routes every call through its Node.js driver and our single worker thread, which adds a hop
and a queue wait to each frame and each mouse event. This channel opens its own WebSocket to the active
page target (Chromium is launched with --remote-debugging-port=0) and runs on its own reader thread:

- Page.screencastFrame is acknowledged immediately on receipt, so Chrome can paint the next frame
  without waiting for the Playwright worker.
- Mouse, wheel, keys and text from the UI are dispatched straight to Chrome, in order, from the
  HTTP thread, never queued behind an AI step.

Playwright remains the engine for everything else (navigation, AI actions, frames, tabs).
"""
import base64
import itertools
import json
import threading

import websocket

# Playwright-style key names -> (key, code, windowsVirtualKeyCode, text)
_NAMED = {
    "Enter": ("Enter", "Enter", 13, "\r"), "Tab": ("Tab", "Tab", 9, ""), "Backspace": ("Backspace", "Backspace", 8, ""),
    "Delete": ("Delete", "Delete", 46, ""), "Escape": ("Escape", "Escape", 27, ""), "Insert": ("Insert", "Insert", 45, ""),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, ""), "ArrowUp": ("ArrowUp", "ArrowUp", 38, ""),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39, ""), "ArrowDown": ("ArrowDown", "ArrowDown", 40, ""),
    "Home": ("Home", "Home", 36, ""), "End": ("End", "End", 35, ""),
    "PageUp": ("PageUp", "PageUp", 33, ""), "PageDown": ("PageDown", "PageDown", 34, ""),
    "Space": (" ", "Space", 32, " "),
}
_NAMED.update({f"F{i}": (f"F{i}", f"F{i}", 111 + i, "") for i in range(1, 13)})
_MODIFIERS = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
_BUTTON_BITS = {"left": 1, "right": 2, "middle": 4}


def key_event_params(combo: str):
    """Parses 'Control+Shift+KeyA' / 'Enter' / 'a' into CDP Input.dispatchKeyEvent parameters."""
    if combo == "+" or combo.endswith("++"):  # the "+" key itself, e.g. "Control++"
        mods, main = [m for m in combo[:-1].split("+") if m], "+"
    else:
        *mods, main = combo.split("+")
    modifiers = 0
    for m in mods:
        if m not in _MODIFIERS:
            raise ValueError(f"Unknown modifier: {m}")
        modifiers |= _MODIFIERS[m]
    shift = bool(modifiers & 8)
    if main in _NAMED:
        key, code, vk, text = _NAMED[main]
    elif len(main) == 4 and main.startswith("Key") and main[3].isalpha():
        ch = main[3]
        key, code, vk, text = (ch.upper() if shift else ch.lower()), main, ord(ch.upper()), ""
    elif len(main) == 6 and main.startswith("Digit") and main[5].isdigit():
        key, code, vk, text = main[5], main, ord(main[5]), ""
    elif len(main) == 1:
        ch = main
        code = f"Key{ch.upper()}" if ch.isalpha() else (f"Digit{ch}" if ch.isdigit() else "")
        key, vk, text = ch, (ord(ch.upper()) if ch.isalnum() else 0), ""
    else:
        raise ValueError(f"Unknown key: {main}")
    if not (modifiers & (1 | 2 | 4)) and len(key) == 1 and not text:
        text = key  # plain printable character
    if modifiers & (1 | 2 | 4):
        text = ""   # shortcuts never insert text
    return {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
            "modifiers": modifiers, "text": text, "unmodifiedText": text}


class FastCDP:
    def __init__(self, on_frame, log):
        self._on_frame = on_frame
        self._log = log
        self._ws = None
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending = {}
        self._reader = None
        self._buttons = 0
        self.suppress_frames = False
        self.viewport = (1280, 800)

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ------------------------------------------------------------------ connection

    def connect(self, ws_url: str, viewport, quality: int):
        self.close()
        ws = websocket.create_connection(ws_url, suppress_origin=True, enable_multithread=True, timeout=10)
        ws.settimeout(None)
        self._ws = ws
        self.viewport = (viewport["width"], viewport["height"])
        self._buttons = 0
        self._reader = threading.Thread(target=self._read_loop, args=(ws,), name="cdp-fast-reader", daemon=True)
        self._reader.start()
        self.call("Page.enable")
        self.call("Page.startScreencast", {"format": "jpeg", "quality": quality, "maxWidth": viewport["width"],
                                           "maxHeight": viewport["height"], "everyNthFrame": 1})

    def close(self):
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            ws.send(json.dumps({"id": next(self._ids), "method": "Page.stopScreencast"}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass
        for ev in list(self._pending.values()):
            ev[0].set()
        self._pending.clear()

    def _read_loop(self, ws):
        while True:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") == "Page.screencastFrame":
                p = msg["params"]
                self.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})  # ack first: keeps frames flowing
                if not self.suppress_frames:
                    meta = p.get("metadata") or {}
                    size = (int(meta.get("deviceWidth") or self.viewport[0]), int(meta.get("deviceHeight") or self.viewport[1]))
                    self._on_frame(base64.b64decode(p["data"]), size)
            elif "id" in msg:
                waiter = self._pending.pop(msg["id"], None)
                if waiter:
                    waiter[1].update(msg)
                    waiter[0].set()
        if self._ws is ws:
            self._ws = None
            self._log("Kênh CDP nhanh bị ngắt, chuyển sang đường dự phòng")

    def send(self, method, params=None):
        """Fire-and-forget (ordering is preserved by the single WebSocket)."""
        ws = self._ws
        if ws is None:
            raise ConnectionError("fast CDP channel closed")
        with self._send_lock:
            ws.send(json.dumps({"id": next(self._ids), "method": method, "params": params or {}}))

    def call(self, method, params=None, timeout=5.0):
        ws = self._ws
        if ws is None:
            raise ConnectionError("fast CDP channel closed")
        mid = next(self._ids)
        ev, box = threading.Event(), {}
        self._pending[mid] = (ev, box)
        with self._send_lock:
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not ev.wait(timeout):
            self._pending.pop(mid, None)
            raise TimeoutError(method)
        if "error" in box:
            raise RuntimeError(box["error"].get("message", "CDP error"))
        return box.get("result", {})

    # ------------------------------------------------------------------ input

    def dispatch(self, events):
        """Replays UI input events (same schema as BrowserManager._cmd_input). Returns last mouse position."""
        last = None
        vw, vh = self.viewport
        for ev in events[:300]:
            t = ev.get("t")
            if t in ("move", "down", "up", "click", "wheel"):
                x = max(0.0, min(vw - 1, float(ev.get("x", 0))))
                y = max(0.0, min(vh - 1, float(ev.get("y", 0))))
                last = (x, y)
                button = ev.get("button", "left")
                button = button if button in _BUTTON_BITS else "left"
                count = max(1, min(3, int(ev.get("count", 1))))
                if t == "move":
                    self._mouse("mouseMoved", x, y, button="none")
                elif t == "down":
                    self._mouse("mouseMoved", x, y, button="none")
                    self._buttons |= _BUTTON_BITS[button]
                    self._mouse("mousePressed", x, y, button=button, click_count=count)
                elif t == "up":
                    self._buttons &= ~_BUTTON_BITS[button]
                    self._mouse("mouseReleased", x, y, button=button, click_count=count)
                elif t == "click":
                    self._buttons |= _BUTTON_BITS[button]
                    self._mouse("mousePressed", x, y, button=button, click_count=count)
                    self._buttons &= ~_BUTTON_BITS[button]
                    self._mouse("mouseReleased", x, y, button=button, click_count=count)
                else:
                    self.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y,
                                                          "deltaX": float(ev.get("dx", 0)), "deltaY": float(ev.get("dy", 0)),
                                                          "buttons": self._buttons})
            elif t == "key":
                params = key_event_params(str(ev.get("key", "")))
                self.send("Input.dispatchKeyEvent", {**params, "type": "keyDown" if params["text"] else "rawKeyDown"})
                self.send("Input.dispatchKeyEvent", {**params, "type": "keyUp", "text": "", "unmodifiedText": ""})
            elif t == "text":
                text = str(ev.get("text", ""))
                if text:
                    self.send("Input.insertText", {"text": text})
        return last

    def _mouse(self, kind, x, y, button="none", click_count=0):
        if kind == "mouseMoved" and self._buttons:
            # While a button is held Chrome only treats moves as a drag (text selection, DnD)
            # when the move names the pressed button, not just the bitmask.
            button = next(name for name, bit in _BUTTON_BITS.items() if self._buttons & bit)
        self.send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": button,
                                              "buttons": self._buttons, "clickCount": click_count})
