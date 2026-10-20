"""Gemini free-tier quota handling: daily exhaustion is skipped instantly, per-minute limits are waited out."""
import time
from types import SimpleNamespace

import pytest
from google.genai import types  # noqa: F401  (first import takes ~1 s; keep it out of the timing checks)

from llm import GeminiClient, LLMError, _parse_quota

DAILY = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota ... "
         "limit: 20, model: gemini-3.8-flash\\nPlease retry in 9h50m5.002616136s.', 'status': 'RESOURCE_EXHAUSTED', "
         "'details': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}}")
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. {'error': {'message': 'Quota exceeded ... Please retry in 1.2s.', "
              "'details': [{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}}")


def test_parse_quota():
    wait, per_day = _parse_quota(DAILY)
    assert per_day and abs(wait - (9 * 3600 + 50 * 60 + 5)) < 1
    wait, per_day = _parse_quota(PER_MINUTE)
    assert not per_day and abs(wait - 1.2) < 0.01
    assert _parse_quota("400 INVALID_ARGUMENT") is None


class FakeGenai:
    """models.generate_content follows a script per model: an Exception message (raise) or a reply text."""

    def __init__(self, script, available=()):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.available = list(available)
        self.models = SimpleNamespace(generate_content=self._gen, list=self._list)

    def _gen(self, model, contents, config):
        self.calls.append(model)
        nxt = self.script[model].pop(0) if self.script.get(model) else "429 RESOURCE_EXHAUSTED PerDay retry in 1h"
        if nxt.startswith("429") or nxt.startswith("503"):
            raise RuntimeError(nxt)
        return SimpleNamespace(text=nxt, usage_metadata=None)

    def _list(self):
        return [SimpleNamespace(name=f"models/{n}", supported_actions=["generateContent"], display_name=n, input_token_limit=1)
                for n in self.available]


def _client(fake):
    c = GeminiClient()
    c._get_client = lambda key: fake
    return c


def test_daily_quota_switches_immediately_without_retries():
    fake = FakeGenai({"gemini-3.7-flash": [DAILY], "gemini-flash-latest": [DAILY], "gemini-3.5-flash": ["OK"]})
    c = _client(fake)
    notes = []
    t0 = time.time()
    out = c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest", "gemini-3.5-flash"], on_retry=notes.append)
    assert out == "OK" and time.time() - t0 < 1.0, "no backoff sleeps on daily quota"
    assert fake.calls == ["gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash"], "each exhausted model tried once"
    assert c.last_model_used == "gemini-3.5-flash" and "hết lượt miễn phí" in c.last_switch_reason

    # Next call skips the exhausted models without even asking Google.
    fake.calls.clear()
    fake.script["gemini-3.5-flash"] = ["OK2"]
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest", "gemini-3.5-flash"]) == "OK2"
    assert fake.calls == ["gemini-3.5-flash"]


def test_per_minute_limit_waits_then_retries_same_model():
    fake = FakeGenai({"gemini-3.5-flash": [PER_MINUTE, "OK"]})
    c = _client(fake)
    t0 = time.time()
    assert c.generate("k", "gemini-3.5-flash", "p") == "OK"
    assert fake.calls == ["gemini-3.5-flash", "gemini-3.5-flash"]
    assert 1.2 <= time.time() - t0 < 4, "waits the delay Google asked for"


def test_discovers_other_models_when_all_configured_are_exhausted():
    fake = FakeGenai({"gemini-3.7-flash": [DAILY], "gemini-flash-latest": [DAILY], "gemini-3.6-flash": ["OK"]},
                     available=["gemini-3.7-flash", "gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-pro-latest"])
    c = _client(fake)
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest"]) == "OK"
    assert fake.calls[-1] == "gemini-3.6-flash", "full Flash is preferred over Flash-Lite"
    assert "gemini-pro-latest" not in fake.calls


def test_all_exhausted_gives_clear_message_with_reset_time():
    fake = FakeGenai({"gemini-3.7-flash": [DAILY], "gemini-flash-latest": [DAILY]}, available=[])
    with pytest.raises(LLMError, match="hết lượt miễn phí hôm nay.*Claude"):
        _client(fake).generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest"])
