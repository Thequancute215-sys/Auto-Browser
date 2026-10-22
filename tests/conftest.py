"""
Shared fixtures: a static HTTP server for test pages and one BrowserManager (isolated temp profile).
Run: python -m pytest            (fast suite, no network / API key needed)
     python -m pytest -m llm     (end-to-end agent tests, uses your Gemini key and quota)
"""
import functools
import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
REPORTS = ROOT / "tests" / "reports"
REPORTS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

_PROFILE = tempfile.mkdtemp(prefix="aib-test-profile-")
REAL_AI_CONFIG = ROOT / "ai_config.json"  # only read by the opt-in LLM tests, never written
TEST_AI_CONFIG = pathlib.Path(_PROFILE) / "ai_config.test.json"
os.environ["AIB_PROFILE_DIR"] = _PROFILE
os.environ["AIB_KILL_STALE"] = "0"
os.environ["AIB_AI_CONFIG"] = str(TEST_AI_CONFIG)
os.environ.pop("GEMINI_API_KEY", None)  # tests never pick up a real key implicitly

import config  # noqa: E402

config.DEFAULT_URL = "about:blank"

from events import EventBus  # noqa: E402
from browser_manager import BrowserManager  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: end-to-end tests that call the Gemini API (opt-in: -m llm)")


def pytest_collection_modifyitems(config, items):
    if "llm" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="LLM test: run with  python -m pytest -m llm")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # /slow/<ms>/<file>: the file, served late (content a player loads by script after a click).
        parts = self.path.split("/", 3)
        if len(parts) == 4 and parts[1] == "slow" and parts[2].isdigit():
            time.sleep(int(parts[2]) / 1000)
            self.path = "/" + parts[3]
        super().do_GET()


class _DualStackServer(http.server.ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once: 'localhost' may resolve to ::1 (e.g. GitHub's Windows runners),
    and the cross-origin iframe tests load the same server as both 127.0.0.1 and localhost."""
    daemon_threads = True
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


@pytest.fixture(scope="session")
def site():
    """Base URL of the fixture site. 'localhost' and '127.0.0.1' both work, which gives two origins."""
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    srv = _DualStackServer(("::", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _fresh_ai_config():
    """Every test starts from default AI settings (save_config() writes to the shared test file)."""
    TEST_AI_CONFIG.unlink(missing_ok=True)
    yield
    TEST_AI_CONFIG.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def bus():
    return EventBus()


@pytest.fixture(scope="session")
def browser(bus):
    bm = BrowserManager(bus)
    assert bm.ready.wait(90), "Chromium did not start"
    assert bm.alive, "Chromium failed to start"
    yield bm
    bm.stop()
    bm.worker_thread.join(15)
    shutil.rmtree(_PROFILE, ignore_errors=True)


@pytest.fixture
def goto(browser, site):
    def _go(path):
        res = browser.execute("goto", {"url": f"{site}/{path.lstrip('/')}"})
        assert res["status"] == "ok", res
        browser.execute("ai_act", {"action": "wait", "ms": 300})
        return res
    return _go


def api_get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def api_post(base, path, body, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture(scope="session")
def server():
    """The real server.py in a subprocess: random port, throwaway profile and AI config, no API key."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tmp = tempfile.mkdtemp(prefix="aib-srv-")
    env = dict(os.environ, PORT=str(port), AIB_PROFILE_DIR=tmp, AIB_KILL_STALE="0",
               AIB_AI_CONFIG=os.path.join(tmp, "ai_config.json"), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if api_get(base, "/api/state").get("ready"):
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.kill()
    proc.wait(10)
    shutil.rmtree(tmp, ignore_errors=True)


def frames_eval(browser, script):
    res = browser.execute("eval_frames", {"script": script})
    assert res["status"] == "ok", res
    return res["results"]
