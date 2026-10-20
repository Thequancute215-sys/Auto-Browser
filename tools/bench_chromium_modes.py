"""
Compares Chromium launch modes for the live view: click→frame latency, animation/scroll fps, idle CPU.
Frames are captured through cdp_fast.FastCDP exactly like the app does.

Run: python tools/bench_chromium_modes.py
"""
import os
import pathlib
import statistics
import sys
import tempfile
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from cdp_fast import FastCDP  # noqa: E402

BASE_ARGS = [a for a in config.CHROMIUM_ARGS if a not in ("--disable-gpu", "--disable-software-rasterizer")]
GPU_OFF = ["--disable-gpu", "--disable-software-rasterizer"]

MODES = {
    "shell + gpu off (cũ)": dict(channel=None, args=BASE_ARGS + GPU_OFF),
    "shell + gpu": dict(channel=None, args=BASE_ARGS),
    "chromium headless mới + gpu off": dict(channel="chromium", args=BASE_ARGS + GPU_OFF),
    "chromium headless mới + gpu": dict(channel="chromium", args=BASE_ARGS),
    "chromium + gpu d3d11": dict(channel="chromium", args=BASE_ARGS + ["--use-angle=d3d11", "--enable-gpu-rasterization"]),
    "chromium + gpu, q55": dict(channel="chromium", args=BASE_ARGS, quality=55),
    "chromium + d3d11 (không gpu-raster)": dict(channel="chromium", args=BASE_ARGS + ["--use-angle=d3d11"]),
    "shell + d3d11": dict(channel=None, args=BASE_ARGS + ["--use-angle=d3d11", "--enable-gpu-rasterization"]),
}


def cpu_seconds(pids):
    import ctypes
    from ctypes import wintypes
    total = 0.0
    k32 = ctypes.WinDLL("kernel32")
    for pid in pids:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            total += ((kt.dwHighDateTime << 32 | kt.dwLowDateTime) + (ut.dwHighDateTime << 32 | ut.dwLowDateTime)) / 1e7
        k32.CloseHandle(h)
    return total


def chrome_pids():
    import subprocess
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() in ("chrome.exe", "chrome-headless-shell.exe"):
            pids.append(int(parts[1]))
    return pids


def run_mode(p, name, mode):
    profile = tempfile.mkdtemp(prefix="aib-mode-")
    before_pids = set(chrome_pids())
    ctx = p.chromium.launch_persistent_context(profile, headless=True, channel=mode["channel"], viewport=config.VIEWPORT,
                                               args=mode["args"] + ["--remote-debugging-port=0"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto((ROOT / "tests" / "fixtures" / "perf.html").as_uri())
    port = None
    for _ in range(100):
        try:
            port = int(open(os.path.join(profile, "DevToolsActivePort")).readline())
            break
        except Exception:
            time.sleep(0.05)
    tid = ctx.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]

    frames = []
    cond = threading.Condition()

    def on_frame(data, size):
        with cond:
            frames.append((time.perf_counter(), len(data)))
            cond.notify_all()

    fast = FastCDP(on_frame, lambda m: None)
    fast.connect(f"ws://127.0.0.1:{port}/devtools/page/{tid}", config.VIEWPORT, mode.get("quality", config.STREAM_QUALITY))
    time.sleep(1.0)

    def wait_next(n, timeout=2.0):
        with cond:
            cond.wait_for(lambda: len(frames) > n, timeout)

    lat = []
    for i in range(20):
        n = len(frames)
        t0 = time.perf_counter()
        fast.dispatch([{"t": "down", "x": 900, "y": 500}, {"t": "up", "x": 900, "y": 500}])
        wait_next(n)
        lat.append((frames[-1][0] - t0) * 1000 if len(frames) > n else 2000)
        time.sleep(0.15)

    n0 = len(frames)
    page.evaluate("""() => { let n = 0; const b = document.getElementById('box');
        const f = () => { b.style.left = (100 + (n * 7) % 900) + 'px'; b.style.top = '300px'; if (++n < 180) requestAnimationFrame(f); };
        requestAnimationFrame(f); }""")
    time.sleep(2.0)
    anim_fps = (len(frames) - n0) / 2.0

    n0 = len(frames)
    t0 = time.perf_counter()
    for _ in range(60):
        fast.dispatch([{"t": "wheel", "x": 640, "y": 400, "dx": 0, "dy": 40}])
        time.sleep(0.016)
    time.sleep(0.15)
    scroll_fps = (len(frames) - n0) / (time.perf_counter() - t0)
    avg_kb = statistics.mean(f[1] for f in frames[n0:]) / 1024 if len(frames) > n0 else 0

    pids = [x for x in chrome_pids() if x not in before_pids]
    c0 = cpu_seconds(pids)
    time.sleep(3.0)
    idle_cpu = (cpu_seconds(pids) - c0) / 3.0 * 100 / os.cpu_count()

    fast.close()
    ctx.close()
    return {"mode": name, "click_p50": statistics.median(lat), "click_min": min(lat), "anim_fps": anim_fps,
            "scroll_fps": scroll_fps, "kb": avg_kb, "idle_cpu": idle_cpu}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    only = sys.argv[1:] or list(MODES)
    with sync_playwright() as p:
        print(f"{'mode':36} {'click p50':>9} {'min':>5} {'anim fps':>9} {'scroll fps':>10} {'KB/frame':>8} {'idle CPU%':>9}")
        for name in only:
            try:
                r = run_mode(p, name, MODES[name])
                print(f"{r['mode']:36} {r['click_p50']:9.0f} {r['click_min']:5.0f} {r['anim_fps']:9.1f} {r['scroll_fps']:10.1f} {r['kb']:8.0f} {r['idle_cpu']:9.2f}")
            except Exception as e:
                print(f"{name:36} FAILED: {str(e).splitlines()[0][:80]}")


if __name__ == "__main__":
    main()
