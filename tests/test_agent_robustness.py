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


def test_hash_and_pushstate_changes_do_not_leave_the_tab_loading(browser, goto):
    """Live: after h5p.org's carousel set #example=..., the tab reported loading=True forever ("load" never comes
    for a same-document navigation), so anything waiting for the page to finish loading waited for nothing."""
    goto("carousel.html")
    browser.execute("ai_act", {"action": "click", "id": _by(_els(browser), text="Site logo")["id"]})  # href="#home"
    _ev(browser, "history.pushState({}, '', '/next-activity')")
    browser.execute("ai_act", {"action": "wait", "ms": 300})
    st = browser.get_state()
    assert st["active_url"].endswith("/next-activity") and st["loading"] is False


def test_h5p_item_placed_over_its_zone_counts_as_answered(browser, goto):
    """H5P Drag and Drop keeps a dropped item outside the zone in the DOM and positions it over the zone."""
    goto("carousel.html")
    _ev(browser, """(() => {
      document.body.insertAdjacentHTML('beforeend', `<div id="task" style="position:relative;width:600px;height:200px">
        <div class="h5p-dropzone" style="position:absolute;left:0;top:0;width:200px;height:150px">
          <div class="h5p-inner ui-droppable" style="width:100%;height:100%"></div></div>
        <div class="h5p-draggable" role="button" style="position:absolute;left:400px;top:10px;width:80px;height:20px">Fragaria</div>
      </div>`);
    })()""")
    before = browser.execute("form_status", {})
    assert any(it["kind"] == "drop" for it in before["items"]), "the zone is empty while the item sits elsewhere"
    _ev(browser, "Object.assign(document.querySelector('.h5p-draggable').style, {left: '60px', top: '60px'})")
    after = browser.execute("form_status", {})
    assert not any(it["kind"] == "drop" for it in after["items"]) and after["unanswered"] == before["unanswered"] - 1


def test_questions_hidden_behind_next_count_as_unanswered(browser, goto):
    """Live (British Council question set): only one question is in view, the page says "6 items remaining";
    FORM_STATUS saw 0 blanks and Finish went through after 2 of 8."""
    goto("question_set.html")
    _ev(browser, "document.querySelector('input[name=a0][value=of]').click()")
    fs = browser.execute("form_status", {})
    assert fs["unanswered"] == 2 and any(it["kind"] == "hidden" for it in fs["items"]), fs


def test_finish_is_blocked_while_the_page_says_items_remain(browser, bus, goto):
    goto("question_set.html")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 3, "action_delay_ms": 0, "auto_submit": True})

    def step(p, n):
        ids = {name: _button(p, name) for name in ("Finish",)}
        acts = [{"action": "click", "id": next(int(l[1:l.index("]")]) for l in p.splitlines() if '"of"' in l and "radio" in l)},
                {"action": "click", "id": ids["Finish"]}] if n == 1 else []
        return {"observation": "-", "plan": "-", "progress": "-", "answers": [], "actions": acts,
                "status": "continue" if n == 1 else "need_user", "message": "-"}
    ai.llm = Script(step)
    ai.run_command("Làm bài này rồi bấm Finish")
    ai.current_task_thread.join(60)
    assert _ev(browser, "window.__finished") is None, "Finish must not be pressed with 2 questions left"
    assert any("Không bấm «Finish»" in m["text"] for m in ai.get_status()["messages"])
    assert "hidden" in ai.llm.prompts[1] and "BLOCKED" in ai.llm.prompts[1]


def test_silent_placeholder_audio_is_not_transcribed(browser, bus, goto):
    """Live (British Council grammar page): the player's "empty.mp3" was sent for transcription (100 s, then 504)."""
    goto("carousel.html")
    _ev(browser, "document.body.insertAdjacentHTML('beforeend', '<audio src=\"empty.mp3\"></audio>')")
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 1, "action_delay_ms": 0})
    ai.llm = Script(lambda p, n: {"observation": "-", "plan": "-", "progress": "-", "answers": [], "actions": [],
                                  "status": "done", "message": "-"})
    ai.run_command("Xem trang này")
    ai.current_task_thread.join(60)
    assert len(ai.llm.prompts) == 1 and "TASK FROM USER" in ai.llm.prompts[0], "only the agent step, no transcription call"
    assert not any("empty.mp3" in m["text"] for m in ai.get_status()["messages"])
