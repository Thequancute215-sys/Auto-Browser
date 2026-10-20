"""
Smoothness benchmark of the real UI, measured end-to-end ("input → pixel on screen"):

  click latency   UI mousedown → remote page repaints → frame streamed → decoded → drawn on the canvas
  hover latency   UI mousemove → black square drawn under the new cursor position
  scroll          60 wheel events at 60 Hz: frames drawn per second and the worst gap between frames
  idle            frames sent while nothing changes (should be 0)

Run:  python tools/bench_smoothness.py            (starts its own server on a free port)
Writes tests/reports/smoothness.md
"""
import json
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORT = ROOT / "tests" / "reports" / "smoothness.md"

PROBE_JS = r"""
(() => {
  const S = window.__aib, canvas = document.getElementById('screenCanvas');
  const ctx = canvas.getContext('2d');
  const waiters = new Set();
  const draws = [];
  const orig = ctx.drawImage.bind(ctx);
  ctx.drawImage = (...a) => { orig(...a); const t = performance.now(); draws.push(t); for (const w of [...waiters]) w(t); };
  const pixel = (x, y) => Array.from(ctx.getImageData(Math.round(x * canvas.width / S.viewport.width), Math.round(y * canvas.height / S.viewport.height), 1, 1).data.slice(0, 3));
  const client = (x, y) => { const r = canvas.getBoundingClientRect(); return [r.left + x / S.viewport.width * r.width, r.top + y / S.viewport.height * r.height]; };
  const until = (pred, timeout = 3000) => new Promise(resolve => {
    if (pred()) return resolve(performance.now());
    const w = t => { if (pred()) { waiters.delete(w); resolve(t); } };
    waiters.add(w);
    setTimeout(() => { waiters.delete(w); resolve(null); }, timeout);
  });
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const same = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 40);
  window.__probe = {
    draws,
    async clicks(n) {
      const out = [];
      for (let i = 0; i < n; i++) {
        const [cx, cy] = client(900, 500);
        const before = pixel(60, 60);
        const t0 = performance.now();
        canvas.dispatchEvent(new MouseEvent('mousedown', { clientX: cx, clientY: cy, button: 0, detail: 1, bubbles: true }));
        window.dispatchEvent(new MouseEvent('mouseup', { clientX: cx, clientY: cy, button: 0, bubbles: true }));
        const t = await until(() => !same(pixel(60, 60), before));
        out.push(t === null ? null : t - t0);
        await sleep(120);
      }
      return out;
    },
    async moves(n) {
      const out = [];
      for (let i = 0; i < n; i++) {
        const x = 300 + (i % 5) * 150, y = 200 + (i % 3) * 150;
        const [cx, cy] = client(x, y);
        const t0 = performance.now();
        canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: cx, clientY: cy, bubbles: true }));
        const t = await until(() => same(pixel(x, y), [0, 0, 0]));
        out.push(t === null ? null : t - t0);
        await sleep(120);
      }
      return out;
    },
    async scroll() {
      const screen = document.getElementById('screen');
      const [cx, cy] = client(640, 400);
      const start = draws.length, t0 = performance.now();
      for (let i = 0; i < 60; i++) {
        screen.dispatchEvent(new WheelEvent('wheel', { clientX: cx, clientY: cy, deltaY: 40, deltaMode: 0, bubbles: true, cancelable: true }));
        await sleep(16);
      }
      const tEnd = performance.now();
      await sleep(400);
      const ds = draws.slice(start).filter(t => t <= tEnd + 150);
      const gaps = ds.slice(1).map((t, i) => t - ds[i]);
      return { frames: ds.length, seconds: (tEnd + 150 - t0) / 1000, first: ds.length ? ds[0] - t0 : null,
               maxGap: gaps.length ? Math.max(...gaps) : null, gaps };
    },
    async idle(ms) { const n = draws.length; await sleep(ms); return draws.length - n; },
  };
  return true;
})()
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pct(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return float("nan")
    k = (len(vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def main():
    port = _free_port()
    tmp = tempfile.mkdtemp(prefix="aib-bench-")
    env = dict(os.environ, PORT=str(port), AIB_PROFILE_DIR=tmp, AIB_KILL_STALE="0",
               AIB_AI_CONFIG=os.path.join(tmp, "ai.json"), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(180):
            try:
                with urllib.request.urlopen(base + "/api/state", timeout=2) as r:
                    if json.loads(r.read()).get("ready"):
                        break
            except Exception:
                time.sleep(0.5)
        _post(base, "/api/goto", {"url": (ROOT / "tests" / "fixtures" / "perf.html").as_uri()})
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1600, "height": 950})
            page.goto(base)
            page.wait_for_function("document.getElementById('screenOverlay').hidden === true", timeout=30000)
            page.wait_for_timeout(1500)
            page.evaluate(PROBE_JS)
            idle = page.evaluate("__probe.idle(3000)")
            clicks = page.evaluate("__probe.clicks(25)")
            moves = page.evaluate("__probe.moves(25)")
            scroll = page.evaluate("__probe.scroll()")
            b.close()
    finally:
        proc.kill()

    fps = scroll["frames"] / scroll["seconds"] if scroll["seconds"] else 0
    rows = [
        ("Click → điểm ảnh đổi màu", f"{pct(clicks, 50):.0f} ms", f"{pct(clicks, 95):.0f} ms", f"{sum(c is None for c in clicks)}/{len(clicks)} lỗi"),
        ("Rê chuột → ô đen dưới con trỏ", f"{pct(moves, 50):.0f} ms", f"{pct(moves, 95):.0f} ms", f"{sum(m is None for m in moves)}/{len(moves)} lỗi"),
        ("Cuộn 60 Hz", f"{fps:.0f} khung/giây", f"khoảng trống lớn nhất {scroll['maxGap'] or 0:.0f} ms",
         f"khung đầu sau {scroll['first'] or 0:.0f} ms"),
        ("Đứng yên 3 giây", f"{idle} khung", "", ""),
    ]
    lines = ["# Đo độ mượt (end-to-end, đo trong UI thật)", "",
             f"Đo lúc {time.strftime('%Y-%m-%d %H:%M')} · màn hình 1280×800 · `python tools/bench_smoothness.py`", "",
             "| Phép đo | p50 / chỉ số | p95 / phụ | Ghi chú |", "|---|---|---|---|"]
    lines += [f"| {a} | {b_} | {c} | {d} |" for a, b_, c, d in rows]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(lines))
    print("\nraw:", json.dumps({"clicks": [round(c or -1) for c in clicks], "moves": [round(m or -1) for m in moves],
                                 "scroll_gaps": [round(g) for g in scroll["gaps"]]}))


if __name__ == "__main__":
    main()
