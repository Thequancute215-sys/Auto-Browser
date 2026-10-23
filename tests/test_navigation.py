"""'Vào mục X' anywhere on the page, without an AI call (sidebar scrolled away, headings far below)."""
import pytest

from ai_controller import AIController


def _ev(browser, script):
    return browser.execute("eval", {"script": script})["result"]


@pytest.fixture
def ai_nokey(browser, bus):
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "", "claude_api_key": ""})
    return ai


def _run(ai, cmd):
    ai.run_command(cmd)
    ai.current_task_thread.join(60)
    return ai.get_status()["messages"]


def test_opens_lesson_far_down_the_sidebar(browser, goto, ai_nokey):
    goto("course.html")
    msgs = _run(ai_nokey, "vào mục Lesson 27")
    assert _ev(browser, "document.getElementById('opened').textContent").startswith("Lesson 27:")
    assert any("Đã mở «Lesson 27" in m["text"] and "Không tốn lượt gọi AI" in m["text"] for m in msgs)


def test_lesson_1_is_not_confused_with_lesson_10(browser, goto, ai_nokey):
    goto("course.html")
    _run(ai_nokey, "mở Lesson 1")
    assert _ev(browser, "document.getElementById('opened').textContent").startswith("Lesson 1:")


@pytest.mark.parametrize("cmd", ["đi tới phần 5 bai tap tong hop", "di toi phan 5 BAI TAP tong hop"])
def test_diacritics_and_case_do_not_matter(browser, goto, ai_nokey, cmd):
    goto("course.html")
    msgs = _run(ai_nokey, cmd)
    top = _ev(browser, "document.getElementById('ex').getBoundingClientRect().top")
    assert 0 <= top < 800, "the heading far below must be scrolled into view"
    assert any("Đã cuộn tới «Phần 5" in m["text"] for m in msgs)


def test_unaccented_open_command(browser, goto, ai_nokey):
    goto("course.html")
    _run(ai_nokey, "vao muc lesson 12")
    assert _ev(browser, "document.getElementById('opened').textContent").startswith("Lesson 12:")


def test_ambiguous_query_lists_candidates(browser, goto, ai_nokey):
    goto("course.html")
    msgs = _run(ai_nokey, "vào mục Hoạch định")   # six lessons are called "Hoạch định"
    assert _ev(browser, "document.getElementById('opened').textContent") == "none"
    assert any("Có nhiều mục giống" in m["text"] for m in msgs)


def test_not_found_is_explained(browser, goto, ai_nokey):
    goto("course.html")
    msgs = _run(ai_nokey, "vào mục Thống kê tài chính")
    assert any("Không tìm thấy «Thống kê tài chính»" in m["text"] for m in msgs)


def test_task_with_extra_steps_goes_to_the_agent(browser, goto, ai_nokey):
    goto("course.html")
    msgs = _run(ai_nokey, "vào Lesson 3 rồi làm bài tập")
    assert _ev(browser, "document.getElementById('opened').textContent") == "none"
    assert any("API Key" in m["text"] for m in msgs), "multi-step tasks are for the agent (needs a key)"


def test_offscreen_links_reach_the_agent(browser, goto):
    goto("course.html")
    els = browser.execute("ai_observe", {"marks": False})["elements"]
    texts = [e["text"] for e in els if e.get("tag") == "a"]
    assert any(t.startswith("Lesson 30:") for t in texts), "links scrolled away inside the sidebar are listed"
    assert all(e.get("offscreen") for e in els if e.get("text", "").startswith("Lesson 30:"))


def test_link_in_a_task_is_opened_before_the_first_llm_call(browser, bus, goto, site):
    """'Vào <link> rồi làm bài': the app opens the link itself; the first LLM call already sees the exercise."""
    import json
    goto("course.html")
    seen = []

    class FirstLook:
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        last_model_used = None

        def generate(self, api_key, model, prompt, **kw):
            seen.append(prompt)
            return json.dumps({"observation": "-", "plan": "-", "answers": [], "actions": [], "status": "done", "message": "ok"})

    from ai_controller import AIController
    ai = AIController(browser, bus)
    ai.settings.update({"api_key": "k", "max_steps": 2, "action_delay_ms": 0})
    ai.llm = FirstLook()
    ai.run_command(f"Vào {site}/quiz.html rồi làm hết bài tập")
    ai.current_task_thread.join(60)
    assert seen and "quiz.html" in browser.get_state()["active_url"]
    assert "quiz.html" in seen[0] or "Choose" in seen[0] or "went" in seen[0], "the first call sees the exercise page"
    assert any("không tốn lượt gọi AI" in m["text"] for m in ai.get_status()["messages"])


def test_agent_navigate_blocks_non_web_schemes(browser, goto, site):
    """Security: a model-chosen 'navigate' (steerable by injected page content) must not
    reach file:/data:/javascript: — that would let a hostile page read local files such as
    ai_config.json via page.goto. The local /api/goto path (fixtures) stays unrestricted."""
    goto("quiz.html")
    for bad in ("file:///C:/Windows/win.ini", "data:text/html,<b>x</b>", "javascript:fetch('/x')"):
        res = browser.execute("ai_act", {"action": "navigate", "url": bad})
        assert res.get("status") == "error", f"{bad} must be refused"
    # the browser must not have left the page the agent was on
    assert browser.get_state()["active_url"].endswith("quiz.html")
    # a normal web navigation still works through the agent action
    res = browser.execute("ai_act", {"action": "navigate", "url": f"{site}/course.html"})
    assert res.get("status") != "error"
    assert browser.get_state()["active_url"].endswith("course.html")
