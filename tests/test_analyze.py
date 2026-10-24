"""Right-click "Phân tích AI": selection + context, the answer box, the AI call, the extension and its click queue."""
import json
import threading
import time

import pytest

import config
from ai_controller import AIController
from browser_manager import BrowserManager
from conftest import ROOT, api_post
from llm import LLMError

SELECT_Q3 = """() => {
  const q = [...document.querySelectorAll('.q')].find(d => d.textContent.includes('Where'));
  const r = document.createRange();
  r.setStart(q.firstChild, 0);
  r.setEnd(q.firstChild, q.firstChild.textContent.indexOf('?') + 1);
  getSelection().removeAllRanges();
  getSelection().addRange(r);
}"""

BOX = """() => {
  const h = document.getElementById('__aib_answer');
  if (!h) return null;
  const box = h.shadowRoot.querySelector('.box'), cs = getComputedStyle(h), r = box.getBoundingClientRect();
  return { cls: box.className, text: h.shadowRoot.querySelector('.body').innerText, opacity: cs.opacity,
           position: cs.position, left: r.left, bottom: innerHeight - r.bottom, width: r.width, height: r.height };
}"""


class TextLLM:
    def __init__(self, reply="Đáp án: were\nChủ ngữ «you» đi với «were».", error=None):
        self.reply, self.error = reply, error
        self.calls = []
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.last_model_used = "fake"

    def generate(self, api_key, model, prompt, image=None, **kw):
        self.calls.append({"prompt": prompt, **kw})
        if self.error:
            raise self.error
        return self.reply


def _eval(browser, script):
    return browser.execute("eval", {"script": script})["result"]


@pytest.fixture
def make_ai(browser, bus):
    def _make(llm, **settings):
        ai = AIController(browser, bus)
        ai.settings.update({"api_key": "fake-key", **settings})
        ai.llm = llm
        return ai
    return _make


def test_selection_context_includes_the_options(browser, goto):
    goto("quiz.html")
    _eval(browser, SELECT_Q3)
    res = browser.execute("selection_context", {})
    assert res["text"] == "3. Where ___ you born?"
    assert "were" in res["context"] and "did" in res["context"]  # the options sit outside the selection
    assert len(res["context"]) <= 1500


def test_selection_context_reads_an_iframe(browser, goto):
    goto("quiz.html")
    _eval(browser, "() => { getSelection().removeAllRanges(); const w = document.getElementById('fr').contentWindow;"
                   " w.getSelection().selectAllChildren(w.document.querySelector('p')); }")
    assert "last holiday" in browser.execute("selection_context", {})["text"]


def test_analyze_shows_a_small_answer_box_bottom_left(browser, goto, make_ai):
    goto("quiz.html")
    _eval(browser, SELECT_Q3)
    llm = TextLLM()
    ai = make_ai(llm, answer_transparency=40)
    ai.analyze_selection("menu copy")
    box = _eval(browser, BOX)
    assert box["cls"] == "box answer"
    assert box["text"].startswith("Đáp án: were") and "Chủ ngữ" in box["text"]
    assert float(box["opacity"]) == pytest.approx(0.6)
    assert box["position"] == "fixed" and box["left"] <= 20 and box["bottom"] <= 20
    assert box["width"] <= 300 and box["height"] <= 170
    call = llm.calls[0]
    assert "Where ___ you born?" in call["prompt"] and "were" in call["prompt"].split("Surrounding", 1)[1]
    assert call["effort"] == "low" and "Đáp án" in call["system"]


def test_answer_box_closes_with_button_and_escape(browser, goto, make_ai):
    goto("quiz.html")
    _eval(browser, SELECT_Q3)
    ai = make_ai(TextLLM())
    ai.analyze_selection()
    _eval(browser, "() => document.getElementById('__aib_answer').shadowRoot.querySelector('.x').click()")
    assert _eval(browser, BOX) is None
    ai.analyze_selection()
    assert _eval(browser, BOX) is not None
    browser.execute("ai_act", {"action": "press", "key": "Escape"})
    assert _eval(browser, BOX) is None


def test_answer_box_is_invisible_to_the_agent(browser, goto, make_ai):
    goto("quiz.html")
    _eval(browser, SELECT_Q3)
    make_ai(TextLLM(reply="Đáp án: MARKER-XYZ")).analyze_selection()
    obs = browser.execute("ai_observe", {})
    assert "MARKER-XYZ" not in json.dumps(obs.get("elements", []), ensure_ascii=False)
    assert "MARKER-XYZ" not in json.dumps(obs.get("texts", []), ensure_ascii=False)


def test_analyze_without_selection_or_key_explains_what_to_do(browser, goto, make_ai):
    goto("quiz.html")
    _eval(browser, "() => getSelection().removeAllRanges()")
    llm = TextLLM()
    make_ai(llm).analyze_selection()
    box = _eval(browser, BOX)
    assert box["cls"] == "box error" and "bôi đen" in box["text"].lower()

    _eval(browser, SELECT_Q3)
    make_ai(llm, api_key="").analyze_selection()
    assert "API Key" in _eval(browser, BOX)["text"]
    assert llm.calls == []


def test_analyze_shows_llm_errors(browser, goto, make_ai):
    goto("quiz.html")
    _eval(browser, SELECT_Q3)
    make_ai(TextLLM(error=LLMError("Gemini đang quá tải"))).analyze_selection()
    box = _eval(browser, BOX)
    assert box["cls"] == "box error" and "quá tải" in box["text"]


def test_answer_transparency_setting_is_clamped(browser, bus):
    ai = AIController(browser, bus)
    assert ai.settings["answer_transparency"] == 15
    ai.save_config({"answer_transparency": 300})
    assert ai.settings["answer_transparency"] == 80
    ai.save_config({"answer_transparency": "x"})
    assert ai.settings["answer_transparency"] == 80


def test_analyze_endpoint(server):
    status, body = api_post(server, "/api/ai/analyze", {"text": "hello"})
    assert status == 200 and body["status"] == "ok"
    status, _ = api_post(server, "/api/ai/analyze", {"text": "hello"}, headers={"Origin": "http://evil.example"})
    assert status == 403


# ---------------------------------------------------------------- extension (window mode)

def test_extension_only_asks_for_the_context_menu():
    manifest = json.loads((ROOT / "browser_ext" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3 and manifest["permissions"] == ["contextMenus"]
    assert "host_permissions" not in manifest and "content_scripts" not in manifest
    assert "Phân tích AI" in (ROOT / "browser_ext" / manifest["background"]["service_worker"]).read_text(encoding="utf-8")


def _bare_manager(**attrs):
    bm = BrowserManager.__new__(BrowserManager)  # no worker thread, no Chromium
    bm.log = lambda msg: None
    for k, v in attrs.items():
        setattr(bm, k, v)
    return bm


def test_window_mode_loads_only_our_extension():
    seen = {}

    class FakeChromium:
        def launch_persistent_context(self, **kw):
            seen.update(kw)
            return object()

    class FakeP:
        chromium = FakeChromium()

    bm = _bare_manager(window_mode=True, viewport=dict(config.VIEWPORT))
    bm._launch(FakeP())
    assert f"--load-extension={config.EXTENSION_DIR}" in seen["args"]
    assert f"--disable-extensions-except={config.EXTENSION_DIR}" in seen["args"]
    assert "--disable-extensions" not in seen["args"]

    seen.clear()
    bm = _bare_manager(window_mode=False, viewport=dict(config.VIEWPORT), _read_devtools_port=lambda d: None)
    bm._launch(FakeP())
    assert "--disable-extensions" in seen["args"] and not any("load-extension" in a for a in seen["args"])


def test_menu_clicks_are_drained_into_analyses():
    class Worker:
        def __init__(self, clicks):
            self.clicks = clicks

        def evaluate(self, script):
            out, self.clicks = self.clicks, []
            return out

    got, done = [], threading.Event()

    def on_analyze(text):
        got.append(text)
        if len(got) == 2:
            done.set()

    bm = _bare_manager(_ext_worker=Worker([{"text": "Q1", "frameId": 0}, {"text": "Q2", "frameId": 4}]), on_analyze=on_analyze)
    bm._poll_ext_clicks()
    assert done.wait(5) and sorted(got) == ["Q1", "Q2"]
    bm._poll_ext_clicks()  # empty queue: nothing more
    time.sleep(0.1)
    assert len(got) == 2

    class DeadWorker:
        def evaluate(self, script):
            raise RuntimeError("Target closed")

    bm = _bare_manager(_ext_worker=DeadWorker(), on_analyze=on_analyze)
    bm._poll_ext_clicks()  # a failing worker is skipped, not fatal
    assert len(got) == 2

    class FakeWorker:
        url = "chrome-extension://abc/background.js"

        def __init__(self):
            self.handlers = {}

        def on(self, event, fn):
            self.handlers[event] = fn

    bm = _bare_manager(_ext_worker=None)
    w = FakeWorker()
    bm._on_service_worker(w)
    assert bm._ext_worker is w
    w.handlers["close"](w)
    assert bm._ext_worker is None
