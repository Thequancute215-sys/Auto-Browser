"""
Auto Browser desktop launcher (packaged as AutoBrowser.exe by tools/build_exe.py).

Layout on start: a real Chromium window on the left (the browser you use, native speed and sound)
and the AI panel on the right (Edge/Chrome "app" window showing the local UI in panel view).
Closing either window quits the app; the Windows Job Object in server.py cleans up every child process.
"""
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

APP = "AutoBrowser"
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
LOG_DIR = os.path.join(DATA_DIR, "logs")
PANEL_W_MIN, PANEL_W_MAX = 380, 470
PANEL_GRACE_S = 6.0  # panel may reconnect (reload) without the app quitting


# ---------------------------------------------------------------- small Win32 helpers

def _message_box(text, title="Auto Browser", icon=0x40):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)
    except Exception:
        pass


def _work_area():
    """Desktop area without the taskbar, in logical pixels (what Chromium's --window-* flags expect)."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        return 0, 0, 1366, 728


def _find_app_browser():
    """
    Browser for the AI panel window (--app mode). Playwright's own Chromium comes first: it always starts a
    separate instance with our profile, honours --window-position and dies with the app's Job Object.
    (Edge can hand the window to the user's already-running personal Edge instead, which we must not touch.)
    """
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    for path in sorted(glob.glob(os.path.join(browsers, "chromium-*", "chrome-win*", "chrome.exe")), reverse=True):
        return path
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    candidates = []
    for r in roots:
        candidates += [os.path.join(r, "Google", "Chrome", "Application", "chrome.exe"),
                       os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


# ---------------------------------------------------------------- setup

def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "app.log")
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".old")
    except OSError:
        pass
    log = open(path, "a", encoding="utf-8", buffering=1)
    # A windowed exe has no console: keep print()/tracebacks in the log file.
    sys.stdout = sys.stderr = log
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start (frozen={getattr(sys, 'frozen', False)}) =====")
    return path


def _port_is_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _running_instance(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as r:
            st = json.loads(r.read().decode())
            return st.get("mode") == "window"
    except Exception:
        return False


def _choose_port():
    for port in range(8000, 8011):
        if _running_instance(port):
            return port, True
        if _port_is_free(port):
            return port, False
    raise RuntimeError("Không tìm được cổng trống (8000-8010).")


def _ensure_chromium():
    """First run on a new machine: download Playwright's Chromium (~150 MB) in a visible console."""
    browsers = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    if glob.glob(os.path.join(browsers, "chromium-*")):
        return
    _message_box("Lần chạy đầu: Auto Browser sẽ tải Chromium (~150 MB).\n"
                 "Một cửa sổ tải xuống sẽ hiện ra, vui lòng chờ đến khi xong.")
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    driver = compute_driver_executable()
    cmd = list(driver) if isinstance(driver, (tuple, list)) else [str(driver)]
    subprocess.run(cmd + ["install", "chromium"], env=get_driver_env(),
                   creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), check=True)


# ---------------------------------------------------------------- main

def main():
    log_path = _setup_logging()
    os.makedirs(DATA_DIR, exist_ok=True)

    ax, ay, aw, ah = _work_area()
    panel_w = max(PANEL_W_MIN, min(PANEL_W_MAX, int(aw * 0.28)))
    browser_rect = (ax, ay, aw - panel_w, ah)
    panel_rect = (ax + aw - panel_w, ay, panel_w, ah)

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"))
    port, already = _choose_port()
    url = f"http://127.0.0.1:{port}/?view=panel"
    if not already:
        _ensure_chromium()
    app_browser = _find_app_browser()

    def open_panel():
        if not app_browser:
            import webbrowser
            webbrowser.open(url)
            return None
        x, y, w, h = panel_rect
        return subprocess.Popen([app_browser, f"--app={url}", f"--user-data-dir={os.path.join(DATA_DIR, 'panel-profile')}",
                                 "--no-first-run", "--no-default-browser-check", "--disable-features=Translate",
                                 "--disable-sync", "--disable-extensions", "--disable-component-update",
                                 f"--window-position={x},{y}", f"--window-size={w},{h}"])

    if already:  # second launch: just bring the panel back
        open_panel()
        return

    os.environ.update({
        "PORT": str(port),
        "AIB_BROWSER_MODE": "window",
        "AIB_WINDOW_RECT": ",".join(str(v) for v in browser_rect),
        "AIB_AI_CONFIG": os.path.join(DATA_DIR, "ai_config.json"),
        "AIB_KILL_STALE": "0",  # the Job Object already guarantees cleanup; never kill other apps' browsers
    })

    t_import = time.time()
    import server  # noqa: E402  (reads the environment above at import time)
    print(f"[timing] server module imported in {time.time() - t_import:.1f}s")

    httpd = server.Server(("127.0.0.1", port), server.AppRequestHandler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    print(f"server on {url}")

    if not server.browser.ready.wait(120) or not server.browser.alive:
        _message_box(f"Không khởi động được Chromium.\nXem nhật ký: {log_path}", icon=0x10)
        return
    panel = open_panel()
    # Playwright's Chromium with a dedicated profile is a real new instance: its process *is* the panel.
    # Other browsers (Edge) may hand the window to an existing process, so the live SSE connection
    # (server.PANEL, with a grace period for reloads) is the fallback signal.
    own_panel = panel is not None and "ms-playwright" in (app_browser or "")
    try:
        while True:
            time.sleep(0.4)
            if server.browser.window_closed or not server.browser.alive:
                print("browser window closed -> quit")
                break
            if own_panel and panel.poll() is not None:
                print("panel window closed -> quit")
                break
            p = server.PANEL
            if p["seen"] and p["clients"] == 0 and time.time() - p["since"] > PANEL_GRACE_S:
                print("panel disconnected -> quit")
                break
    finally:
        server.bus.publish("app_quit", {})  # the panel window closes itself
        server.browser.stop()
        deadline = time.time() + 1.5
        while panel is not None and panel.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if panel is not None and panel.poll() is None:
            panel.terminate()  # dedicated profile => this is the panel's own browser process
        httpd.shutdown()
        server.browser.worker_thread.join(10)
        # Anything still alive is ended by the Windows Job Object when this process exits.


def selftest():
    """`AutoBrowser.exe --selftest`: checks the bundle contains every runtime dependency, then exits."""
    _setup_logging()
    ok = True
    for mod in ("anthropic", "google.genai", "playwright.sync_api", "websocket", "server_deps"):
        try:
            if mod == "server_deps":
                import ai_controller, browser_manager, cdp_fast, events, llm, page_scripts  # noqa: F401
            else:
                __import__(mod)
            print(f"[selftest] ok   {mod}")
        except Exception as e:
            ok = False
            print(f"[selftest] FAIL {mod}: {e}")
    print(f"[selftest] {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    try:
        main()
    except Exception:
        traceback.print_exc()
        _message_box("Auto Browser gặp lỗi khi khởi động:\n\n" + traceback.format_exc()[-800:], icon=0x10)
