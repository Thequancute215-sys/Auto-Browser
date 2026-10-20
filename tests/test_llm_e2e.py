"""
Real Gemini end-to-end runs (opt-in: python -m pytest -m llm). Reads the key from ai_config.json, never writes it.
Each run costs a few API calls.
"""
import json

import pytest

from ai_controller import AIController
from conftest import REAL_AI_CONFIG

pytestmark = pytest.mark.llm


@pytest.fixture
def real_ai(browser, bus):
    if not REAL_AI_CONFIG.exists():
        pytest.skip("ai_config.json with a Gemini key is required")
    saved = json.loads(REAL_AI_CONFIG.read_text(encoding="utf-8"))
    if not saved.get("api_key"):
        pytest.skip("no api_key in ai_config.json")
    ai = AIController(browser, bus)
    ai.settings.update({k: v for k, v in saved.items() if k in ai.settings})
    ai.settings.update({"mode": "auto", "auto_submit": True, "auto_next": False, "max_steps": 8})
    return ai


def _run(ai, cmd):
    ai.run_command(cmd)
    ai.current_task_thread.join(600)
    return ai.get_status()["messages"]


def _result(browser):
    return browser.execute("eval", {"script": "document.getElementById('result').textContent"})["result"]


def test_agent_solves_mixed_quiz(real_ai, goto, browser):
    goto("quiz.html")
    msgs = _run(real_ai, "Giải bài này")
    assert _result(browser).startswith("Score: 6/6"), [m["text"] for m in msgs[-8:]]


def test_agent_solves_listening_exercise(real_ai, goto, browser):
    goto("listening.html")
    msgs = _run(real_ai, "Nghe audio và làm bài nghe này")
    assert any(m["kind"] == "transcript" and "Friday" in m["text"] for m in msgs), "audio must be transcribed"
    assert _result(browser) == "Score: 4/4", [m["text"] for m in msgs[-8:]]
