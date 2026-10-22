"""Long pages: the AI must see questions below the screen and must never submit with blanks."""
import json
import re

from ai_controller import AIController


def _ev(browser, script):
    return browser.execute("eval", {"script": script})["result"]


def _state(browser):
    return _ev(browser, "window.__state")


def test_observe_covers_the_whole_page(browser, goto):
    goto("long_form.html")
    obs = browser.execute("ai_observe", {})
    els = obs["elements"]
    labels = {e.get("text") for e in els}
    assert {f"q{i}-b" for i in range(1, 5)} <= labels, "radio options below the screen must be listed"
    fields = [e for e in els if e.get("editable")]
    assert {e["before"].split(":")[0][-11:] for e in fields if "Question" in e.get("before", "")} >= {f"Question {i}" for i in (5, 6, 7, 8, 9)}
    t10 = next(e for e in fields if e.get("editable") == "textarea")
    assert t10.get("offscreen"), "a field inside a scrolled box / below the screen is flagged offscreen"
    assert any(e.get("offscreen") == "below" for e in els)
    assert not any(e.get("offscreen") for e in els if e.get("text") == "Nộp bài"), "sticky header is on screen"
    fs = obs["form_status"]
    assert fs["total"] == 10 and fs["unanswered"] == 10, fs  # the search box is not a question
    assert {it["kind"] for it in fs["items"]} == {"choice", "blank", "dropdown"}


def test_offscreen_fields_can_be_answered_directly(browser, goto):
    goto("long_form.html")
    els = browser.execute("ai_observe", {"marks": False})["elements"]
    t10 = next(e for e in els if e.get("editable") == "textarea")
    q4b = next(e for e in els if e.get("text") == "q4-b")
    sel = next(e for e in els if e["tag"] == "select")
    for act in ({"action": "type", "id": t10["id"], "text": "ok"}, {"action": "click", "id": q4b["id"]},
                {"action": "select", "id": sel["id"], "option": "yes"}):
        assert browser.execute("ai_act", act)["status"] == "ok", act
    assert _ev(browser, "document.getElementById('t10').value") == "ok"
    assert _ev(browser, "document.querySelector('input[name=q4]:checked').value") == "b"
    assert browser.execute("form_status", {})["unanswered"] == 7


def _ids(prompt, pattern):
    return [int(m.group(1)) for m in re.finditer(r"^\[(\d+)\] " + pattern, prompt, re.M)]


class Script:
    def __init__(self, steps):
        self.steps, self.prompts = steps, []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.last_model_used = None

    def generate(self, api_key, model, prompt, **kw):
        self.prompts.append(prompt)
        self.usage["calls"] += 1
        step = self.steps[min(len(self.prompts), len(self.steps)) - 1]
        return json.dumps(step(prompt))


def _plan(actions, status="continue"):
    return {"observation": "-", "plan": "-", "answers": [], "actions": actions, "status": status, "message": "-"}


def test_guard_blocks_submit_with_blanks_then_allows_when_complete(browser, bus, goto):
    goto("long_form.html")

    def step1(p):  # the mistake from the screenshot: answer what is visible, then press Nộp
        assert "10 of 10 questions" in p and "offscreen: below" in p
        return _plan([{"action": "click", "id": _ids(p, r'label/radio "q1-b"')[0]},
                      {"action": "click", "id": _ids(p, r'button "Nộp bài"')[0]}])

    def step2(p):
        assert "BLOCKED by safety guard" in p
        assert "9 of 10 questions" in p
        acts = [{"action": "click", "id": _ids(p, rf'label/radio "q{i}-b"')[0]} for i in (2, 3, 4)]
        acts += [{"action": "type", "id": i, "text": "x"} for i in _ids(p, r"input:?\w* .*Question [5-8]: type x")]
        acts += [{"action": "select", "id": _ids(p, r"select")[0], "option": "yes"},
                 {"action": "type", "id": _ids(p, r"textarea")[0], "text": "ok"},
                 {"action": "click", "id": _ids(p, r'button "Nộp bài"')[0]}]
        return _plan(acts)

    def step3(p):
        assert "Score: 10/10" in p
        return _plan([], status="done")

    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 5, "action_delay_ms": 0, "auto_submit": True})
    ai.llm = Script([step1, step2, step3])
    ai.run_command("Làm bài kiểm tra")
    ai.current_task_thread.join(120)
    msgs = ai.get_status()["messages"]
    assert any("Không bấm «Nộp bài»" in m["text"] for m in msgs), "the early submit must be blocked and reported"
    st = _state(browser)
    assert st["submitted"] == 1 and st["attempts"] == 2, f"only the complete submit may count: {st}"
    assert _ev(browser, "document.getElementById('result').textContent") == "Score: 10/10"


def test_guard_also_blocks_the_confirm_dialog_submit(browser, bus, goto):
    goto("long_form.html")
    _ev(browser, "trySubmit(); 1")  # page is already showing "Còn 10 câu chưa trả lời"

    def step1(p):
        assert "Còn 10 câu chưa trả lời" in p
        return _plan([{"action": "click", "id": _ids(p, r'button "Nộp luôn"')[0]}])

    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 1, "action_delay_ms": 0})
    ai.llm = Script([step1])
    ai.run_command("Làm bài")
    ai.current_task_thread.join(60)
    assert _state(browser)["submitted"] == 0


class _OneBlank:
    """Stub browser: the live page always has one unanswered question."""

    def execute(self, cmd, payload=None, timeout=None):
        assert cmd == "form_status"
        return {"status": "ok", "unanswered": 1, "total": 5, "items": [{"aid": 13}]}


def test_guard_never_blocks_an_answer_choice():
    # Cambridge "Test your English": the right answer to "I'd like two tickets..." is "I'll just check for you.",
    # which the guard used to mistake for a "Check" button, so the question could never be answered.
    ai = AIController.__new__(AIController)
    ai.browser, ai._submit_blocks = _OneBlank(), 0
    choices = {
        1: {"id": 1, "tag": "label", "toggle": "radio", "text": "I'll just check for you."},
        2: {"id": 2, "tag": "div", "role": "radio", "text": "Check"},
        3: {"id": 3, "tag": "li", "role": "option", "text": "Done"},
        4: {"id": 4, "tag": "div", "text": "I'll just check for you."},  # plain clickable div: a sentence, not a button
        5: {"id": 5, "tag": "input", "type": "checkbox", "toggle": "checkbox", "text": ""},
    }
    for i in choices:
        assert ai._submit_guard({"action": "click", "id": i}, choices) is None, choices[i]
    assert ai._submit_blocks == 0
    buttons = {10: {"id": 10, "tag": "button", "text": "Check answers"}, 11: {"id": 11, "tag": "a", "text": "Nộp bài"},
               12: {"id": 12, "tag": "div", "role": "button", "text": "Submit"}}
    blocked = [ai._submit_guard({"action": "click", "id": i}, buttons) for i in (10, 11)]
    assert all(b and b["unanswered"] == 1 for b in blocked), "real Submit buttons are still guarded"
    assert ai._submit_guard({"action": "click", "id": 12}, buttons) is None, "after MAX_SUBMIT_BLOCKS the model may insist"


def test_exercise_in_an_iframe_is_seen_on_a_link_heavy_page(browser, goto):
    # h5p.org: 260+ menu/footer links used the whole observation budget, so the drag exercise in the iframe
    # had no ids and the agent pressed Tab blindly.
    goto("h5p_like.html")
    obs = browser.execute("ai_observe", {"marks": False})
    els = obs["elements"]
    words = {e["text"] for e in els if e.get("frame") and e.get("role") == "button"}
    assert {"red", "blue", "orange", "green"} <= words, words
    zones = [e for e in els if e.get("frame") and e.get("dropzone") and "Drop Zone" in (e.get("label") or "")]
    assert len(zones) == 3, zones
    assert any(e.get("text") == "Check" and e.get("frame") for e in els)
    assert sum(1 for e in els if not e.get("frame")) >= 100, "the outer page keeps a fair share"
    fs = obs["form_status"]
    assert fs["unanswered"] == 3 and {it["kind"] for it in fs["items"]} == {"drop"}, fs


def test_filled_h5p_zone_counts_as_answered(browser, goto):
    goto("h5p_like.html")
    els = browser.execute("ai_observe", {"marks": False})["elements"]
    blue = next(e for e in els if e.get("text") == "blue" and e.get("frame"))
    zone1 = next(e for e in els if "Drop Zone 1" in (e.get("label") or ""))
    assert browser.execute("ai_act", {"action": "click", "id": blue["id"]})["status"] == "ok"
    assert browser.execute("ai_act", {"action": "click", "id": zone1["id"]})["status"] == "ok"
    assert browser.execute("form_status", {})["unanswered"] == 2


def test_false_done_is_rejected_while_blanks_remain(browser, bus, goto):
    goto("h5p_like.html")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 6, "action_delay_ms": 0})
    ai.llm = Script([lambda p: _plan([], status="done")])  # Flash-Lite's mistake: "done" without dragging anything
    ai.run_command("Làm bài kéo thả này rồi bấm Check")
    ai.current_task_thread.join(60)
    msgs = [m["text"] for m in ai.get_status()["messages"]]
    assert sum("AI định báo xong" in t for t in msgs) == AIController.MAX_DONE_REJECTS
    assert "you said DONE" in ai.llm.prompts[1] and "3 unanswered" in ai.llm.prompts[1]
    assert len(ai.llm.prompts) == AIController.MAX_DONE_REJECTS + 1, "after two rejections the model may insist"
