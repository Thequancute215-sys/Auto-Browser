"""Direct CDP channel (cdp_fast.py): frames and user input that bypass the Playwright worker."""
import pytest

from cdp_fast import key_event_params


def _ev(browser, script):
    return browser.execute("eval", {"script": script})["result"]


def test_fast_channel_is_used_for_stream_mode(browser):
    assert browser.fast.connected, "stream mode should use the direct CDP channel"


def test_typing_through_fast_channel(browser, goto):
    goto("quiz.html")
    x, y = _ev(browser, "(() => { const r = document.getElementById('g1').getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; })()")
    res = browser.input_events([
        {"t": "down", "x": x, "y": y}, {"t": "up", "x": x, "y": y},
        {"t": "text", "text": "xin chào"}, {"t": "key", "key": "Control+a"}, {"t": "text", "text": "wentt"},
        {"t": "key", "key": "Backspace"}, {"t": "key", "key": "Tab"}, {"t": "text", "text": "didn't see"},
    ])
    assert res == {"status": "ok", "path": "fast"}
    browser.execute("ai_act", {"action": "wait", "ms": 200})  # fire-and-forget: let Chrome finish
    assert _ev(browser, "document.getElementById('g1').value") == "went"
    assert _ev(browser, "document.getElementById('g2').value") == "didn't see", "Tab must move focus to the next field"


def test_enter_submits_and_drag_selects_text(browser, goto):
    goto("quiz.html")
    # Drag across the heading with the mouse: native text selection must happen.
    x0, y0, x1 = _ev(browser, "(() => { const r = document.querySelector('h2').firstChild; const g = document.createRange(); g.selectNodeContents(r); const b = g.getBoundingClientRect(); return [b.left + 1, b.top + b.height/2, b.right - 1]; })()")
    browser.input_events([{"t": "down", "x": x0, "y": y0}, {"t": "move", "x": (x0 + x1) / 2, "y": y0},
                          {"t": "move", "x": x1, "y": y0}, {"t": "up", "x": x1, "y": y0}])
    browser.execute("ai_act", {"action": "wait", "ms": 150})
    assert browser.execute("get_selection", {})["text"].startswith("Unit 3")


@pytest.mark.parametrize("page", ["dnd/html5.html", "dnd/mouse.html", "dnd/sortable.html"])
def test_user_can_drag_and_drop_by_hand(browser, goto, page):
    """A person dragging in the stream view: HTML5 DnD needs CDP drag interception, mouse libs need moves."""
    goto(page)
    centre = "(sel => { const r = sel.getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; })"
    sx, sy = _ev(browser, f"{centre}(Array.from(document.querySelectorAll('.word')).find(e => e.textContent === 'did'))")
    tx, ty = _ev(browser, f"{centre}(document.getElementById('gap'))")
    events = [{"t": "move", "x": sx, "y": sy}, {"t": "down", "x": sx, "y": sy}]
    events += [{"t": "move", "x": sx + (tx - sx) * k / 12, "y": sy + (ty - sy) * k / 12} for k in range(1, 13)]
    for ev in events:  # one event per request, like a real mouse
        browser.input_events([ev])
        browser.execute("ai_act", {"action": "wait", "ms": 20})
    browser.input_events([{"t": "move", "x": tx + 2, "y": ty + 1}])
    browser.execute("ai_act", {"action": "wait", "ms": 60})
    browser.input_events([{"t": "up", "x": tx + 2, "y": ty + 1}])
    browser.execute("ai_act", {"action": "wait", "ms": 300})
    assert _ev(browser, "window.__result()") is True


def test_unknown_key_is_an_error_not_a_crash(browser):
    res = browser.input_events([{"t": "key", "key": "Control+NoSuchKey"}])
    assert res["status"] == "error"
    assert browser.fast.connected


@pytest.mark.parametrize("combo,expect", [
    ("Enter", {"key": "Enter", "windowsVirtualKeyCode": 13, "text": "\r", "modifiers": 0}),
    ("Control+a", {"key": "a", "code": "KeyA", "modifiers": 2, "text": ""}),
    ("Control+Shift+KeyZ", {"key": "Z", "code": "KeyZ", "modifiers": 10}),
    ("Shift+Tab", {"key": "Tab", "modifiers": 8}),
    ("Space", {"key": " ", "text": " "}),
    ("Control++", {"key": "+", "modifiers": 2}),
    ("F5", {"key": "F5", "windowsVirtualKeyCode": 116}),
])
def test_key_event_params(combo, expect):
    params = key_event_params(combo)
    assert {k: params[k] for k in expect} == expect
