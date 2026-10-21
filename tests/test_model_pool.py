"""Model pool (Flash → Flash-Lite → Gemma), Gemma request shape, Ollama client and the switch to the local model."""
import http.server
import json
import threading
import time
from types import SimpleNamespace

import pytest

from ai_controller import AGENT_SCHEMA, AIController
from llm import GeminiClient, LLMError, OllamaClient, QuotaExhausted
from test_gemini_quota import DAILY, FakeGenai


def test_pool_order_flash_then_lite_then_gemma():
    fake = FakeGenai({"gemini-3.8-flash": [DAILY], "gemma-4-26b-a4b-it": ["OK"]},
                     available=["gemini-3.8-flash", "gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.5-flash-lite", "gemini-3.6-flash"])
    c = GeminiClient()
    c._get_client = lambda key: fake
    c.generate("k", "gemini-3.8-flash", "p")
    assert fake.calls == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"]


def test_gemma_gets_system_prompt_inline_and_no_json_mode():
    seen = {}

    class Rec(FakeGenai):
        def _gen(self, model, contents, config):
            seen[model] = (contents, config)
            return SimpleNamespace(text='{"ok": 1}', usage_metadata=None)

    c = GeminiClient()
    c._get_client = lambda key: Rec({})
    c.generate("k", "gemma-4-26b-a4b-it", "PROMPT", system="SYSTEM", json_mode=True)
    contents, config = seen["gemma-4-26b-a4b-it"]
    assert contents[-1].startswith("SYSTEM") and contents[-1].endswith("PROMPT")
    assert not config.system_instruction and not config.response_mime_type
    c.generate("k", "gemini-3.8-flash", "PROMPT", system="SYSTEM", json_mode=True)
    contents, config = seen["gemini-3.8-flash"]
    assert contents[-1] == "PROMPT" and config.system_instruction == "SYSTEM" and config.response_mime_type == "application/json"


def test_whole_pool_exhausted_raises_quota_exhausted():
    fake = FakeGenai({}, available=["gemini-3.8-flash", "gemma-4-26b-a4b-it"])
    c = GeminiClient()
    c._get_client = lambda key: fake
    with pytest.raises(QuotaExhausted):
        c.generate("k", "gemini-3.8-flash", "p")


class FakeOllama(http.server.BaseHTTPRequestHandler):
    bodies = []
    reply = '{"observation":"-","plan":"-","answers":[],"actions":[],"status":"done","message":"xong"}'

    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            self._json({"version": "0.40.7"})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": "gemma4:e4b", "details": {"parameter_size": "8B"}}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.bodies.append(body)
        self._json({"message": {"role": "assistant", "content": FakeOllama.reply}, "prompt_eval_count": 900, "eval_count": 60})


@pytest.fixture(scope="module")
def ollama_url():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ollama_request_shape(ollama_url):
    FakeOllama.bodies.clear()
    c = OllamaClient(ollama_url)
    assert c.available() and c.list_models()[0]["name"] == "gemma4:e4b"
    out = c.generate("", "gemma4:e4b", "PROMPT", image=b"\xff\xd8jpg", system="SYS", schema=AGENT_SCHEMA)
    assert json.loads(out)["status"] == "done"
    b = FakeOllama.bodies[-1]
    assert b["model"] == "gemma4:e4b" and b["stream"] is False
    assert b["format"] == AGENT_SCHEMA, "structured output constrained to the agent schema"
    assert b["messages"][0] == {"role": "system", "content": "SYS"}
    assert b["messages"][1]["content"] == "PROMPT" and b["messages"][1]["images"]
    assert c.usage["calls"] == 1 and c.usage["input_tokens"] == 900


def test_ollama_unreachable_is_explained():
    c = OllamaClient("http://127.0.0.1:9")
    assert not c.available()
    with pytest.raises(LLMError, match="Không kết nối được Ollama"):
        c.list_models()


def test_ollama_rejects_audio(ollama_url):
    with pytest.raises(LLMError, match="audio"):
        OllamaClient(ollama_url).generate("", "gemma4:e4b", "p", attachments=[(b"RIFF", "audio/wav")])


def test_agent_switches_to_local_model_when_cloud_pool_is_exhausted(browser, bus, goto, ollama_url):
    goto("quiz.html")
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "gemini", "api_key": "g", "local_model": "gemma4:e4b", "local_fallback": True})

    class Exhausted:
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        last_model_used = None

        def generate(self, *a, **k):
            raise QuotaExhausted("Tất cả mô hình Google đã hết lượt")

    ai.gemini = Exhausted()
    ai.ollama = OllamaClient(ollama_url)
    ai._local_cache = None
    ai.run_command("Giải bài này")
    ai.current_task_thread.join(60)
    msgs = ai.get_status()["messages"]
    assert any("Chuyển sang **gemma4:e4b**" in m["text"] for m in msgs)
    assert any(m["kind"] == "success" for m in msgs), "the run finishes on the local model"
    assert ai.ollama.usage["calls"] == 1


def test_no_local_fallback_when_disabled(browser, bus, goto, ollama_url):
    goto("quiz.html")
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "gemini", "api_key": "g", "local_fallback": False})
    ai.gemini = SimpleNamespace(usage={"calls": 0, "input_tokens": 0, "output_tokens": 0}, last_model_used=None,
                                generate=lambda *a, **k: (_ for _ in ()).throw(QuotaExhausted("hết lượt")))
    ai.ollama = OllamaClient(ollama_url)
    ai.run_command("Giải bài này")
    ai.current_task_thread.join(60)
    msgs = ai.get_status()["messages"]
    assert any(m["kind"] == "error" and "hết lượt" in m["text"] for m in msgs), [(m["kind"], m["text"][:80]) for m in msgs]
    assert ai.ollama.usage["calls"] == 0


def test_local_provider_needs_no_key(browser, bus, goto, ollama_url):
    goto("quiz.html")
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "local", "api_key": "", "claude_api_key": "", "local_model": "gemma4:e4b"})
    ai.ollama = OllamaClient(ollama_url)
    ai.run_command("Giải bài này")
    ai.current_task_thread.join(60)
    msgs = ai.get_status()["messages"]
    assert not any("API Key" in m["text"] for m in msgs if m["kind"] != "intro")
    assert any("lượt gọi AI trên máy" in m["text"] for m in msgs)
    cfg = ai.get_config()
    assert cfg["provider"] == "local" and cfg["active_model"] == "gemma4:e4b"
