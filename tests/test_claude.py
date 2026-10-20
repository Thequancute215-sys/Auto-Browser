"""Claude provider: request shape sent to the Anthropic SDK, error handling, and provider switching in the agent."""
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from ai_controller import AGENT_SCHEMA, AIController
from llm import CLAUDE_FALLBACK_BETA, ClaudeClient, LLMError


def _text_response(text, stop_reason="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop_reason, model=model, stop_details=None,
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=0))


class FakeAnthropic:
    """Records the kwargs of every messages.create call; returns scripted responses or raises."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.messages = SimpleNamespace(create=lambda **kw: self._create("messages", kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._create("beta", kw)))
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="claude-opus-5-5", display_name="Claude Opus 5.5", max_input_tokens=1_000_000)])

    def _create(self, ns, kw):
        self.calls.append((ns, kw))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client_with(fake):
    """A ClaudeClient that can only ever talk to the fake (never to the real API, whatever key is passed)."""
    c = ClaudeClient()
    c._get_client = lambda api_key: fake
    return c


def _api_error(cls, status):
    resp = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(f"HTTP {status}", response=resp, body=None)


def test_request_shape_follows_current_api():
    fake = FakeAnthropic(_text_response('{"ok": true}'))
    out = _client_with(fake).generate("sk-ant-test", "claude-opus-5-5", "PROMPT", image=b"\xff\xd8jpeg",
                                      system="SYS", json_mode=True, schema=AGENT_SCHEMA, effort="medium")
    assert out == '{"ok": true}'
    ns, kw = fake.calls[0]
    assert ns == "beta" and kw["betas"] == [CLAUDE_FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["model"] == "claude-opus-5-5"
    assert "temperature" not in kw and "thinking" not in kw, "Opus 5.5 rejects sampling params / disabled thinking"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": AGENT_SCHEMA}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[-1] == {"type": "text", "text": "PROMPT"}


def test_models_without_server_fallback_use_plain_endpoint():
    fake = FakeAnthropic(_text_response("hi", model="claude-haiku-5-5"))
    _client_with(fake).generate("k", "claude-haiku-5-5", "p")
    ns, kw = fake.calls[0]
    assert ns == "messages" and "fallbacks" not in kw and "betas" not in kw


def test_refusal_is_reported_not_returned_as_answer():
    resp = _text_response("", stop_reason="refusal")
    resp.stop_details = SimpleNamespace(category="cyber")
    with pytest.raises(LLMError, match="từ chối"):
        _client_with(FakeAnthropic(resp)).generate("k", "claude-opus-5-5", "p")


def test_bad_key_gives_friendly_error():
    with pytest.raises(LLMError, match="console.anthropic.com"):
        _client_with(FakeAnthropic(_api_error(anthropic.AuthenticationError, 401))).generate("k", "claude-opus-5-5", "p")


def test_rate_limit_moves_to_fallback_model():
    fake = FakeAnthropic(_api_error(anthropic.RateLimitError, 429), _text_response("ok", model="claude-sonnet-5-5"))
    notes = []
    c = _client_with(fake)
    assert c.generate("k", "claude-opus-5-5", "p", fallbacks=["claude-sonnet-5-5"], on_retry=notes.append) == "ok"
    assert [kw["model"] for _, kw in fake.calls] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert notes and c.last_model_used == "claude-sonnet-5-5"


def test_audio_is_rejected_with_explanation():
    with pytest.raises(LLMError, match="Gemini"):
        ClaudeClient().generate("k", "claude-opus-5-5", "p", attachments=[(b"RIFF", "audio/wav")])


def test_agent_schema_is_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s.get("additionalProperties") is False
            assert set(s["required"]) <= set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(AGENT_SCHEMA)


class RecordingLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.last_model_used = None

    def generate(self, api_key, model, prompt, **kw):
        self.calls.append({"api_key": api_key, "model": model, "prompt": prompt, **kw})
        self.usage["calls"] += 1
        return json.dumps(self.reply)


DONE = {"observation": "-", "plan": "-", "answers": [], "actions": [], "status": "done", "message": "ok"}


def test_agent_uses_claude_when_selected(browser, bus, goto):
    goto("quiz.html")
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "claude", "claude_api_key": "sk-ant-x", "claude_effort": "high", "api_key": "g-key"})
    ai.claude, ai.gemini = RecordingLLM(DONE), RecordingLLM(DONE)
    ai.run_command("Giải bài này")
    ai.current_task_thread.join(60)
    assert ai.claude.calls and not ai.gemini.calls
    call = ai.claude.calls[0]
    assert call["api_key"] == "sk-ant-x" and call["model"] == "claude-opus-5-5"
    assert call["schema"] is AGENT_SCHEMA and call["effort"] == "high"
    assert any("lượt gọi Claude" in m["text"] for m in ai.get_status()["messages"])


def test_listening_with_claude_transcribes_on_gemini(browser, bus, goto):
    goto("listening.html")
    browser.execute("eval", {"script": "document.getElementById('play').click(); 1"})
    browser.execute("ai_act", {"action": "wait", "ms": 600})
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "claude", "claude_api_key": "sk-ant-x", "api_key": "g-key", "model": "gemini-flash-latest"})
    ai.claude = RecordingLLM(DONE)
    ai.gemini = SimpleNamespace(usage={"calls": 0, "input_tokens": 0, "output_tokens": 0}, calls=[],
                                generate=lambda key, model, prompt, **kw: ai.gemini.calls.append((key, model, kw)) or "Meeting on Friday.")
    ai.run_command("Làm bài nghe")
    ai.current_task_thread.join(60)
    key, model, kw = ai.gemini.calls[0]
    assert key == "g-key" and model == "gemini-flash-latest" and kw["attachments"]
    assert "Meeting on Friday." in ai.claude.calls[0]["prompt"], "the Gemini transcript must reach Claude's prompt"
    assert any(m["kind"] == "transcript" for m in ai.get_status()["messages"])


def test_listening_without_gemini_key_warns(browser, bus, goto):
    goto("listening.html")
    browser.execute("eval", {"script": "document.getElementById('play').click(); 1"})
    browser.execute("ai_act", {"action": "wait", "ms": 600})
    ai = AIController(browser, bus)
    ai.settings.update({"provider": "claude", "claude_api_key": "sk-ant-x", "api_key": ""})
    ai.claude = RecordingLLM(DONE)
    ai.run_command("Làm bài nghe")
    ai.current_task_thread.join(60)
    assert any(m["kind"] == "warning" and "Gemini API Key" in m["text"] for m in ai.get_status()["messages"])


def test_config_never_exposes_either_key(browser, bus):
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "AIzaSecretGemini123456", "claude_api_key": "sk-ant-secret-claude-999"})
    ai.save_config({"provider": "claude"})
    cfg = json.dumps(ai.get_config())
    assert "AIzaSecretGemini123456" not in cfg and "sk-ant-secret-claude-999" not in cfg
    c = ai.get_config()
    assert c["provider"] == "claude" and c["has_claude_key"] and c["has_gemini_key"] and c["active_model"] == "claude-opus-5-5"
