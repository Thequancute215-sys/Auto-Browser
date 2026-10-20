"""HTTP layer: static files, frame stream, SSE, input endpoint and security checks (real server subprocess)."""
import http.client
import json
import struct
import urllib.error
import urllib.request

import pytest

from conftest import ROOT, api_get as _get, api_post as _post


def test_ui_is_served_with_primer(server):
    with urllib.request.urlopen(server + "/") as r:
        html = r.read().decode()
    assert "vendor/primer.css" in html and "app.js" in html
    with urllib.request.urlopen(server + "/vendor/primer.css") as r:
        assert r.status == 200 and b"--bgColor-default" in r.read()


def test_path_traversal_blocked(server):
    for path in ("/../config.py", "/%2e%2e/config.py", "/..%2Fai_config.json"):
        conn = http.client.HTTPConnection("127.0.0.1", int(server.rsplit(":", 1)[1]))
        conn.request("GET", path)
        assert conn.getresponse().status == 404, path


def test_frame_stream_sends_jpeg(server):
    conn = http.client.HTTPConnection("127.0.0.1", int(server.rsplit(":", 1)[1]), timeout=15)
    conn.request("GET", "/api/stream")
    resp = conn.getresponse()
    length, w, h = struct.unpack(">IHH", resp.read(8))
    data = resp.read(length)
    assert length > 1000 and (w, h) == (1280, 800) and data[:2] == b"\xff\xd8"
    conn.close()


def test_sse_hello_contains_state_and_chat(server):
    conn = http.client.HTTPConnection("127.0.0.1", int(server.rsplit(":", 1)[1]), timeout=15)
    conn.request("GET", "/api/events")
    resp = conn.getresponse()
    assert resp.getheader("Content-Type").startswith("text/event-stream")
    buf = b""
    while b"\n\n" not in buf:
        buf += resp.read1(4096)
    event, data = buf.split(b"\n\n")[0].decode().split("\n", 1)
    assert event == "event: hello"
    payload = json.loads(data[len("data: "):])
    assert "state" in payload and payload["ai"]["messages"]
    conn.close()


def test_input_endpoint_and_navigation(server):
    page = (ROOT / "tests" / "fixtures" / "quiz.html").as_uri()
    assert _post(server, "/api/goto", {"url": page})[1]["status"] == "ok"
    status, res = _post(server, "/api/input", {"events": [{"t": "move", "x": 200, "y": 200}, {"t": "key", "key": "Tab"}]})
    assert status == 200 and res["status"] == "ok"
    st = _get(server, "/api/state")
    assert st["active_url"].endswith("quiz.html") and st["tabs"]


def test_cross_origin_and_rebinding_blocked(server):
    assert _post(server, "/api/goto", {"url": "https://example.com"}, {"Origin": "https://evil.example"})[0] == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/api/state", {"Host": "evil.example:80"})
    assert e.value.code == 403


def test_command_whitelist(server):
    assert _post(server, "/api/ai_command", {"cmd": "goto", "payload": {"url": "https://example.com"}})[0] == 400
    status, res = _post(server, "/api/ai_command", {"cmd": "eval", "payload": {"script": "1 + 1"}})
    assert status == 200 and res["result"] == 2


def test_api_key_is_never_returned(server):
    key = "AIzaSyTEST-not-a-real-key-123456"
    status, res = _post(server, "/api/ai/config", {"api_key": key, "model": "gemini-flash-latest"})
    assert status == 200
    cfg = _get(server, "/api/ai/config")
    assert cfg["has_key"] and key not in json.dumps(cfg) and key not in json.dumps(res)
    assert cfg["masked_key"].startswith("AIza") and "…" in cfg["masked_key"]
    _post(server, "/api/ai/config", {"clear_key": True})
    assert not _get(server, "/api/ai/config")["has_key"]
