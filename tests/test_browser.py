"""BrowserManager: real input, selection, text extraction, element annotation, audio capture, tabs."""
import time

from conftest import frames_eval


def _ev(browser, script):
    res = browser.execute("eval", {"script": script})
    assert res["status"] == "ok", res
    return res["result"]


def _center(browser, selector):
    return _ev(browser, f"(() => {{ const r = document.querySelector({selector!r}).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()")


def test_typing_keys_and_unicode(browser, goto):
    goto("quiz.html")
    x, y = _center(browser, "#g1")
    res = browser.execute("input", {"events": [
        {"t": "down", "x": x, "y": y}, {"t": "up", "x": x, "y": y},
        {"t": "text", "text": "wentt"}, {"t": "key", "key": "Backspace"},
        {"t": "key", "key": "Control+a"}, {"t": "text", "text": "Tiếng Việt có dấu"},
    ]})
    assert res["status"] == "ok"
    assert _ev(browser, "document.getElementById('g1').value") == "Tiếng Việt có dấu"


def test_double_click_selection_is_readable(browser, goto):
    goto("quiz.html")
    # Centre of the first word "Unit" (the <h2> box itself is full-width, mostly empty).
    x, y = _ev(browser, """(() => { const t = document.querySelector('h2').firstChild; const r = document.createRange();
        r.setStart(t, 0); r.setEnd(t, 4); const b = r.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; })()""")
    browser.execute("input", {"events": [
        {"t": "down", "x": x, "y": y, "count": 1}, {"t": "up", "x": x, "y": y, "count": 1},
        {"t": "down", "x": x, "y": y, "count": 2}, {"t": "up", "x": x, "y": y, "count": 2}]})
    sel = browser.execute("get_selection", {})
    assert sel["status"] == "ok" and sel["text"] and sel["text"] in "Unit 3 – Past simple"


def test_page_text_includes_iframe_and_div_rows(browser, goto):
    goto("quiz.html")
    blocks = browser.execute("page_text", {})["analysis"]["textBlocks"]
    joined = "\n".join(blocks)
    assert "Yesterday she" in joined, "question rows in plain <div>s must be extracted"
    assert "last holiday" in joined, "text inside the iframe must be extracted"


def test_observe_describes_exercise_elements(browser, goto):
    goto("quiz.html")
    obs = browser.execute("ai_observe", {})
    assert obs["status"] == "ok" and obs["screenshot"][:2] == b"\xff\xd8"
    els = obs["elements"]
    g1 = next(e for e in els if e.get("editable") == "input" and "Yesterday she" in e.get("before", ""))
    assert "(go)" in g1["after"]
    assert any(e["tag"] == "label" and e.get("toggle") == "radio" and e["text"] == "were" for e in els)
    sel = next(e for e in els if e["tag"] == "select")
    assert "bought" in sel["options"]
    assert any(e.get("editable") == "textarea" and e.get("frame") for e in els), "iframe textarea must be annotated"
    assert len({e["id"] for e in els}) == len(els), "ids must be unique"
    # Set-of-Marks overlay must be removed after the screenshot.
    assert not any(frames_eval(browser, "!!document.querySelector('[data-ai-overlay]')"))


def test_ai_act_fill_select_click_and_iframe(browser, goto):
    goto("quiz.html")
    els = browser.execute("ai_observe", {"marks": False})["elements"]
    by = lambda pred: next(e for e in els if pred(e))
    g1 = by(lambda e: e.get("editable") == "input" and "Yesterday" in e.get("before", ""))
    were = by(lambda e: e.get("text") == "were")
    sel = by(lambda e: e["tag"] == "select")
    ta = by(lambda e: e.get("editable") == "textarea")
    for act in ({"action": "type", "id": g1["id"], "text": "went"}, {"action": "click", "id": were["id"]},
                {"action": "select", "id": sel["id"], "option": "bought"},
                {"action": "type", "id": ta["id"], "text": "I went to Hue last summer."}):
        res = browser.execute("ai_act", act)
        assert res["status"] == "ok", (act, res)
    assert _ev(browser, "document.getElementById('g1').value") == "went"
    assert _ev(browser, "document.querySelector('input[name=q3]:checked').value") == "b"
    assert _ev(browser, "document.getElementById('s4').value") == "bought"
    assert "Hue" in _ev(browser, "document.getElementById('fr').contentDocument.getElementById('t6').value")


def test_stale_element_is_reported(browser, goto):
    goto("quiz.html")
    els = browser.execute("ai_observe", {"marks": False})["elements"]
    goto("quiz.html")  # reload: ids from the old observation are gone
    res = browser.execute("ai_act", {"action": "click", "id": els[0]["id"]})
    assert res["status"] == "error"


def test_audio_detected_from_network_and_fetched(browser, goto):
    goto("listening.html")
    obs = browser.execute("ai_observe", {"marks": False})
    assert not obs["media"], "audio is only requested after pressing Play"
    play = next(e for e in obs["elements"] if "Play" in e.get("text", ""))
    browser.execute("ai_act", {"action": "click", "id": play["id"]})
    browser.execute("ai_act", {"action": "wait", "ms": 800})
    media = browser.execute("ai_observe", {"marks": False})["media"]
    assert any(m["src"].endswith("listening.wav") for m in media), media
    res = browser.execute("fetch_media", {"url": media[0]["src"]})
    assert res["status"] == "ok" and res["data"][:4] == b"RIFF" and res["mime"].startswith("audio/")


def test_fetch_media_refuses_non_audio_local_files(browser):
    res = browser.execute("fetch_media", {"url": "file:///C:/Windows/win.ini"})
    assert res["status"] == "error"


def test_tabs_new_switch_close_and_popup(browser, goto, site):
    goto("quiz.html")
    before = len(browser.tabs)
    browser.execute("new_tab", {"url": f"{site}/dnd/html5.html"})
    assert len(browser.tabs) == before + 1 and browser.tabs[-1]["active"]
    browser.execute("switch_tab", {"index": 0})
    assert browser.tabs[0]["active"]
    browser.execute("close_tab", {"index": len(browser.tabs) - 1})
    assert len(browser.tabs) == before
    # window.open popups become the active tab
    _ev(browser, f"window.open('{site}/dnd/touch.html'); 1")
    deadline = time.time() + 10
    while time.time() < deadline and "touch" not in (browser.url or ""):
        time.sleep(0.2)
    assert "touch" in browser.url
    browser.execute("close_tab", {"index": browser.active_index})


def test_viewport_change_restarts_stream(browser):
    fid = browser.frame_id
    res = browser.execute("set_viewport", {"width": 1366, "height": 768})
    assert res["viewport"] == {"width": 1366, "height": 768}
    new_id, data, size = browser.wait_frame(fid, timeout=5)
    assert new_id > fid and data[:2] == b"\xff\xd8"
    browser.execute("set_viewport", {"width": 1280, "height": 800})
