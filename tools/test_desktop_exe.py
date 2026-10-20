"""
Smoke test of the packaged desktop app (dist/AutoBrowser/AutoBrowser.exe):
start → server in window mode → Chromium window + AI panel placed side by side → real-window fps →
closing the browser window quits the app and leaves no Chromium process behind.

Run: python tools/test_desktop_exe.py [path\\to\\AutoBrowser.exe]
"""
import ctypes
import json
import pathlib
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXE = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "AutoBrowser" / "AutoBrowser.exe"
user32 = ctypes.windll.user32


def windows():
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                out.append((hwnd, buf.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def api(port, path, body=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def chromium_count():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/V", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if "ms-playwright" in line or "Chrome for Testing" in line)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("--- run 1: close the browser window")
    run(close="browser")
    print("--- run 2: close the AI panel window")
    run(close="panel")


def run(close):
    proc = subprocess.Popen([str(EXE)])
    try:
        _run(proc, close)
    finally:
        if proc.poll() is None:  # never leave the app running after a failed check
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def _run(proc, close):
    t0 = time.time()
    port = None
    while time.time() - t0 < 90 and port is None:
        for p in range(8000, 8011):
            try:
                if api(p, "/api/state", timeout=0.5).get("mode") == "window":
                    port = p
                    break
            except Exception:
                pass
        time.sleep(0.5)
    assert port, "server did not come up"
    while not api(port, "/api/state").get("ready"):
        time.sleep(0.3)
    print(f"[ok] server ready on port {port} after {time.time() - t0:.1f}s")

    browser_win = panel_win = None
    deadline = time.time() + 30
    while time.time() < deadline and not (browser_win and panel_win):
        for hwnd, title, rect in windows():
            if title == "Auto Browser" or "127.0.0.1" in title:
                panel_win = (hwnd, title, rect)
            elif "Chrome for Testing" in title:
                browser_win = (hwnd, title, rect)
        time.sleep(0.5)
    assert browser_win, "Chromium window not found"
    assert panel_win, "AI panel window not found"
    print(f"[ok] browser window {browser_win[2]}  '{browser_win[1][:50]}'")
    print(f"[ok] AI panel window {panel_win[2]}  '{panel_win[1][:50]}'")
    bx, by, bw, bh = browser_win[2]
    px, py, pw, ph = panel_win[2]
    assert px >= bx + bw - 20, "panel should sit to the right of the browser"

    fps = api(port, "/api/ai_command", {"cmd": "eval", "payload": {"script":
        "new Promise(r => { let n = 0; const t = performance.now(); const f = () => { n++; performance.now() - t < 1000 ? requestAnimationFrame(f) : r(n); }; requestAnimationFrame(f); })"}})["result"]
    print(f"[ok] real browser window renders at {fps} fps")
    time.sleep(1.5)  # viewport is re-read with the tab list once per second
    st = api(port, "/api/state")
    vw = st["viewport"]["width"]
    assert abs(vw - bw) < 40, f"page width {vw} should follow the window width {bw}"
    print(f"[ok] page viewport follows the window: {st['viewport']} (window {bw}x{bh})")

    WM_CLOSE = 0x0010
    target = browser_win if close == "browser" else panel_win
    t_close = time.time()
    # Like a user clicking X; if Chromium had a second window with the same title, close those too.
    if close == "browser":
        targets = [w[0] for w in windows() if "Chrome for Testing" in w[1] and w[1] != "Auto Browser"]
    else:
        targets = [target[0]]
    for hwnd in targets:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(3)
    stuck = [h for h in targets if user32.IsWindow(h)]
    if stuck:
        print(f"     {len(stuck)} window(s) still open after WM_CLOSE (of {len(targets)}), clicking close again")
        for hwnd in stuck:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        proc.wait(30)
        print(f"[ok] closing the {close} window quit the app in {time.time() - t_close:.1f}s (exit code {proc.returncode})")
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError(f"app did not quit after the {close} window was closed")
    time.sleep(2)
    left = chromium_count()
    assert left == 0, f"{left} Chromium processes left behind"
    still_open = [t for _, t, _ in windows() if "Chrome for Testing" in t or t.startswith("Auto Browser")]
    assert not still_open, f"windows left open: {still_open}"
    print("[ok] no Chromium processes or app windows left behind")


if __name__ == "__main__":
    main()
