"""Agent loop with a scripted fake Gemini: exercises observe → think → act → verify without network or quota."""
import json
import re

import pytest

from ai_controller import AIController
from llm import LLMError, parse_json_response


def _id(prompt: str, needle: str) -> int:
    """Finds the element id whose description line contains `needle`."""
    for line in prompt.splitlines():
        m = re.match(r"\[(\d+)\] ", line)
        if m and needle in line:
            return int(m.group(1))
    raise AssertionError(f"no element line contains {needle!r}")


class FakeLLM:
    def __init__(self, script):
        self.script = script
        self.prompts = []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.last_model_used = "fake"

    def generate(self, api_key, model, prompt, image=None, **kw):
        self.prompts.append(prompt)
        self.usage["calls"] += 1
        step = self.script[min(len(self.prompts), len(self.script)) - 1]
        return json.dumps(step(prompt) if callable(step) else step)


@pytest.fixture
def make_ai(browser, bus):
    def _make(script, **settings):
        ai = AIController(browser, bus)
        ai.settings.update({"api_key": "fake-key", "max_steps": 6, "action_delay_ms": 0, **settings})
        ai.llm = FakeLLM(script)
        return ai
    return _make


def _run(ai, cmd="Giải bài này", **overrides):
    assert ai.run_command(cmd, overrides or None)["status"] == "ok"
    ai.current_task_thread.join(120)
    assert not ai.is_running
    return ai.get_status()["messages"]


def _page_result(browser):
    return browser.execute("eval", {"script": "document.getElementById('result').textContent"})["result"]


def test_agent_solves_mixed_quiz_and_verifies(make_ai, goto, browser):
    goto("quiz.html")

    def step1(p):
        return {"observation": "quiz", "plan": "answer all", "status": "continue",
                "answers": [{"q": "1", "a": "went", "why": "past of go"}],
                "actions": [
                    {"action": "type", "id": _id(p, "Yesterday she ___"), "text": "went"},
                    {"action": "type", "id": _id(p, "We ___"), "text": "didn't see"},
                    {"action": "click", "id": _id(p, '"were"')},
                    {"action": "select", "id": _id(p, "options="), "option": "bought"},
                    {"action": "drag", "id": _id(p, '"did" (draggable'), "target_id": _id(p, "[HERE] my homework")},
                    {"action": "type", "id": _id(p, "textarea"), "text": "I went to Da Lat last summer."},
                    {"action": "click", "id": _id(p, "Check answers")},
                ]}

    def step2(p):
        assert "Score: 6/6" in p, "second turn must see the graded page"
        assert "-> ok" in p, "history must contain the results of step 1"
        return {"observation": "6/6", "plan": "finish", "status": "done", "actions": [], "message": "Xong 6/6"}

    ai = make_ai([step1, step2])
    msgs = _run(ai)
    assert len(ai.llm.prompts) == 2
    assert _page_result(browser).startswith("Score: 6/6")
    assert any(m["kind"] == "success" and "6/6" in m["text"] for m in msgs)
    assert any(m["kind"] == "answers" for m in msgs)
    assert not any(m["kind"] == "action" and m["status"] == "warning" for m in msgs[-12:])


def test_agent_stops_when_screen_never_changes(make_ai, goto):
    goto("quiz.html")
    ai = make_ai([{"observation": "x", "plan": "wait", "status": "continue", "actions": [{"action": "wait", "ms": 100}]}])
    msgs = _run(ai)
    assert len(ai.llm.prompts) == 3
    assert "did not change" in ai.llm.prompts[-1]
    assert any(m["kind"] == "warning" and "không thay đổi" in m["text"] for m in msgs)


def test_suggest_mode_never_touches_the_page(make_ai, goto, browser):
    goto("quiz.html")

    def step(p):
        return {"observation": "quiz", "plan": "suggest", "status": "done",
                "answers": [{"q": "3", "a": "were", "why": "you → were"}],
                "actions": [{"action": "click", "id": _id(p, '"were"')}], "message": "Đáp án ở trên"}

    ai = make_ai([step], mode="suggest")
    msgs = _run(ai)
    assert "SUGGEST-ONLY MODE" in ai.llm.prompts[0]
    assert browser.execute("eval", {"script": "!!document.querySelector('input[name=q3]:checked')"})["result"] is False
    assert any(m["kind"] == "answers" for m in msgs)


def test_run_overrides_do_not_change_saved_settings(make_ai, goto):
    goto("quiz.html")
    ai = make_ai([{"observation": "-", "plan": "-", "status": "done", "actions": [], "message": "ok"}],
                 auto_submit=False, auto_next=False)
    _run(ai, auto_submit=True, auto_next=True)
    assert "go to the next activity" in ai.llm.prompts[0]
    assert ai.settings["auto_next"] is False and ai.settings["auto_submit"] is False


def test_failed_drag_is_reported_to_the_model(make_ai, goto):
    goto("dnd/html5.html")
    first = {}

    def step1(p):
        first["ids"] = (_id(p, '"did"'), _id(p, '"done"'))
        return {"observation": "-", "plan": "drag onto a word (wrong target)", "status": "continue",
                "actions": [{"action": "drag", "id": first["ids"][0], "target_id": first["ids"][1]}]}

    def step2(p):
        assert "FAILED" in p and "đã thử" in p
        return {"observation": "-", "plan": "-", "status": "done", "actions": [], "message": "stop"}

    _run(make_ai([step1, step2]))


def test_local_commands_do_not_call_the_llm(make_ai, goto, browser):
    goto("quiz.html")
    ai = make_ai([])
    msgs = _run(ai, "cuộn xuống 300")
    assert ai.llm.usage["calls"] == 0
    assert any("Đã cuộn xuống 300px" in m["text"] for m in msgs)


def test_missing_key_gives_clear_warning(make_ai, goto):
    goto("quiz.html")
    ai = make_ai([], api_key="")
    msgs = _run(ai)
    assert any(m["kind"] == "warning" and "API Key" in m["text"] for m in msgs)


def test_llm_error_is_shown_not_hidden(make_ai, goto):
    goto("quiz.html")
    ai = make_ai([])

    def boom(*a, **k):
        raise LLMError("Đã hết hạn mức (quota)")

    ai.llm.generate = boom
    msgs = _run(ai)
    assert any(m["kind"] == "error" and "quota" in m["text"] for m in msgs)
    assert not any(m["kind"] == "success" for m in msgs)


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 2}\n```', {"a": 2}),
    ('Here you go: {"a": 3} hope it helps', {"a": 3}),
])
def test_parse_json_response(raw, expected):
    assert parse_json_response(raw) == expected


def test_parse_json_response_rejects_garbage():
    with pytest.raises(LLMError):
        parse_json_response("no json here")
