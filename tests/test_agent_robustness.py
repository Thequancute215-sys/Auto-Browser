"""Lessons from a live run on h5p.org's examples carousel (Flash-Lite during a Gemini overload):
- it reused ids from an earlier step, its "type" hit the header and the keys (Ctrl+A, the answer, Enter)
  went into the site's search box: typing must only land in the field that was aimed at;
- it looked 0.6 s after "Next", saw "Loading...", believed it was still on example 1 and pressed Next again;
- then it went back and forth between examples until the step budget ran out."""
import json

from ai_controller import AIController


def _ev(browser, script):
    return browser.execute("eval", {"script": script})["result"]


def _els(browser):
    return browser.execute("ai_observe", {"marks": False})["elements"]


def _by(els, **kw):
    return next(e for e in els if all(e.get(k) == v for k, v in kw.items()))


def test_type_on_a_link_is_refused(browser, goto):
    goto("carousel.html")
    els = _els(browser)
    _ev(browser, "document.getElementById('search').focus()")
    res = browser.execute("ai_act", {"action": "type", "id": _by(els, text="Site logo")["id"], "text": "Paris", "enter": True})
    assert res["status"] == "error" and "không phải ô nhập chữ" in res["message"]
    assert _ev(browser, "document.getElementById('search').value") == ""
    assert _ev(browser, "location.hash") == "", "the link was not clicked either"


def test_keys_never_go_to_a_field_that_took_focus_elsewhere(browser, goto):
    goto("carousel.html")
    tip = _by(_els(browser), text="Tip: search the site")  # clicking it focuses the search box
    res = browser.execute("ai_act", {"action": "type", "id": tip["id"], "text": "Paris"})
    assert res["status"] == "error"
    assert _ev(browser, "document.getElementById('search').value") == ""


def test_gap_replaced_by_an_input_on_click_still_gets_the_text(browser, goto):
    goto("carousel.html")
    gap = next(e for e in _els(browser) if e.get("before", "").endswith("The sky is"))
    res = browser.execute("ai_act", {"action": "type", "id": gap["id"], "text": "blue"})
    assert res["status"] == "ok", res
    assert _ev(browser, "document.getElementById('gapInput').value") == "blue"
    assert _ev(browser, "document.getElementById('search').value") == ""


def test_type_into_a_real_field_still_works(browser, goto):
    goto("carousel.html")
    a0 = next(e for e in _els(browser) if e.get("editable") and "France" in e.get("after", ""))
    assert browser.execute("ai_act", {"action": "type", "id": a0["id"], "text": "Paris"})["status"] == "ok"
    assert _ev(browser, "document.getElementById('a0').value") == "Paris"


class Script:
    def __init__(self, step):
        self.step, self.prompts = step, []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.last_model_used = None

    def generate(self, api_key, model, prompt, **kw):
        self.prompts.append(prompt)
        self.usage["calls"] += 1
        return json.dumps(self.step(prompt, len(self.prompts)))


def _button(prompt, name):
    return next(int(line[1:line.index("]")]) for line in prompt.splitlines() if line.startswith("[") and f'"{name}"' in line)


def test_back_and_forth_between_two_screens_is_stopped(browser, bus, goto):
    goto("carousel.html")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 20, "action_delay_ms": 0})
    # The H5P carousel mistake: Next, Previous, Next, Previous ... each step changes the screen.
    ai.llm = Script(lambda p, n: {"observation": "-", "plan": "-", "progress": "-", "answers": [], "status": "continue",
                                  "message": "-", "actions": [{"action": "click", "id": _button(p, "Next" if n % 2 else "Previous")}]})
    ai.run_command("Chuyển sang ví dụ 2")
    ai.current_task_thread.join(90)
    msgs = [m["text"] for m in ai.get_status()["messages"]]
    assert any("quay lại cùng một màn hình" in t for t in msgs), msgs[-4:]
    assert len(ai.llm.prompts) < 12, "stopped long before the step budget"
    assert any("going back and forth" in p for p in ai.llm.prompts), "the model is warned before the stop"


def test_progress_note_is_carried_to_the_next_step(browser, bus, goto):
    goto("carousel.html")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 3, "action_delay_ms": 0})
    notes = ["opened example 2", ""]

    def step(p, n):
        done = n >= 2
        return {"observation": "-", "plan": "-", "progress": notes[0] if n == 1 else "", "answers": [],
                "actions": [] if done else [{"action": "wait", "ms": 100}], "status": "done" if done else "continue",
                "message": "-"}
    ai.llm = Script(step)
    ai.run_command("Chuyển sang ví dụ 2")
    ai.current_task_thread.join(60)
    assert "(nothing finished yet)" in ai.llm.prompts[0]
    assert "YOUR PROGRESS NOTE FROM THE LAST STEP:\nopened example 2" in ai.llm.prompts[1]


def test_last_action_of_a_step_waits_for_content_loaded_by_script(browser, goto):
    goto("player.html")
    nxt = _by(_els(browser), text="Next")
    # _settle_ms 600 marks the last action of an agent step, as the agent loop sends it.
    assert browser.execute("ai_act", {"action": "click", "id": nxt["id"], "_settle_ms": 600})["status"] == "ok"
    assert "capital of Spain" in _ev(browser, "document.getElementById('player').innerText"), \
        "the next observation must see the new exercise, not 'Loading...'"


def test_agent_does_not_think_while_the_page_says_loading(browser, bus, goto):
    goto("player.html?auto=3000")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 2, "action_delay_ms": 0})
    ai.llm = Script(lambda p, n: {"observation": "-", "plan": "-", "progress": "-", "answers": [], "actions": [],
                                  "status": "done", "message": "-"})
    ai.run_command("Xem bài này")
    ai.current_task_thread.join(60)
    first = ai.llm.prompts[0]
    assert "capital of Spain" in first and "Loading..." not in first
