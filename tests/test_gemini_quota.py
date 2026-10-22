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


OVERLOADED = "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.'}}"


def test_overloaded_model_gets_one_retry_then_is_tried_last():
    fake = FakeGenai({"gemini-3.7-flash": [OVERLOADED, OVERLOADED], "gemini-3.5-flash": ["OK", "OK2"]})
    c = _client(fake)
    notes = []
    t0 = time.time()
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-3.5-flash"], on_retry=notes.append) == "OK"
    assert fake.calls == ["gemini-3.7-flash", "gemini-3.7-flash", "gemini-3.5-flash"], "one retry, not three"
    assert time.time() - t0 < 4
    # Next step: the busy model goes to the back of the queue instead of costing another two 503s.
    fake.calls.clear()
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-3.5-flash"]) == "OK2"
    assert fake.calls == ["gemini-3.5-flash"]


def test_all_overloaded_discovers_lite_and_gemma():
    fake = FakeGenai({"gemini-3.7-flash": [OVERLOADED] * 2, "gemini-flash-latest": [OVERLOADED] * 2,
                      "gemini-3.5-flash-lite": ["OK"]},
                     available=["gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemma-4-26b-a4b-it"])
    c = _client(fake)
    notes = []
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest"], on_retry=notes.append) == "OK"
    assert fake.calls[-1] == "gemini-3.5-flash-lite"
    assert any("quá tải, thử thêm" in n for n in notes)


def test_all_overloaded_without_alternatives_says_so():
    fake = FakeGenai({"gemini-3.7-flash": [OVERLOADED] * 2}, available=[])
    with pytest.raises(LLMError, match="quá tải"):
        _client(fake).generate("k", "gemini-3.7-flash", "p")


def test_slow_model_is_raced_against_the_next_one(monkeypatch):
    """Google can hold a request ~80 s before answering 503; after HEDGE_AFTER_S the next model gets it too."""
    import llm
    monkeypatch.setattr(llm, "HEDGE_AFTER_S", 0.3)
    fake = FakeGenai({"gemini-3.5-flash": ["FAST"]})
    slow = fake._gen

    def gen(model, contents, config):
        if model == "gemini-3.7-flash":
            fake.calls.append(model)
            time.sleep(3)
            return SimpleNamespace(text="SLOW", usage_metadata=None)
        return slow(model, contents, config)

    fake.models.generate_content = gen
    c = _client(fake)
    notes = []
    t0 = time.time()
    out = c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-3.5-flash"], on_retry=notes.append)
    assert out == "FAST" and time.time() - t0 < 2, "the first good answer wins, the slow one is not awaited"
    assert c.last_model_used == "gemini-3.5-flash"
    assert any("trả lời chậm" in n for n in notes)
    assert fake.calls.count("gemini-3.5-flash") == 1, "the backup is not asked twice"


def test_failed_backup_is_replaced_by_the_next_model_at_once(monkeypatch):
    """Live: the primary hung, its backup was out of quota in a second, and the step then waited ~60 s more
    for the primary. The next model must get the request as soon as the backup fails."""
    import llm
    monkeypatch.setattr(llm, "HEDGE_AFTER_S", 0.3)
    fake = FakeGenai({"gemini-flash-latest": [DAILY], "gemini-flash-lite-latest": ["LITE"]})
    plain = fake._gen

    def gen(model, contents, config):
        if model == "gemini-3.7-flash":
            fake.calls.append(model)
            time.sleep(4)
            raise Exception(OVERLOADED)
        return plain(model, contents, config)

    fake.models.generate_content = gen
    c = _client(fake)
    t0 = time.time()
    out = c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-flash-latest", "gemini-flash-lite-latest"])
    assert out == "LITE" and time.time() - t0 < 2.5
    assert fake.calls.count("gemini-flash-lite-latest") == 1


def test_slow_503_is_not_retried(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "HEDGE_AFTER_S", 60)  # no racing here: only the retry rule
    fake = FakeGenai({"gemini-3.5-flash": ["OK"]})
    plain = fake._gen
    real_time = time.time
    clock = {"skew": 0.0}

    def gen(model, contents, config):
        if model == "gemini-3.7-flash":
            fake.calls.append(model)
            clock["skew"] += 30  # as if Google held the request 30 s before answering 503
            raise Exception(OVERLOADED)
        return plain(model, contents, config)

    fake.models.generate_content = gen
    monkeypatch.setattr(llm.time, "time", lambda: real_time() + clock["skew"])
    c = _client(fake)
    assert c.generate("k", "gemini-3.7-flash", "p", fallbacks=["gemini-3.5-flash"]) == "OK"
    assert fake.calls == ["gemini-3.7-flash", "gemini-3.5-flash"], "a 503 that took 30 s is not a blip"
