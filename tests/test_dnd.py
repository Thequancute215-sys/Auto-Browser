"""
Drag & drop: one test page per mechanism used by real e-learning players.

test_drag_auto    — the agent's real path: detect source/target, run the strategy chain, verify the result.
test_drag_matrix  — tries every strategy on every page and writes tests/reports/dnd_matrix.md.
"""
import time

import pytest

from browser_manager import BrowserManager
from conftest import REPORTS, frames_eval

PAGES = [
    ("dnd/html5.html", "HTML5 draggable + ondrop"),
    ("dnd/mouse.html", "mousedown/move/up + ngưỡng 5px + ghost"),
    ("dnd/pointer.html", "Pointer events + isTrusted (kiểu dnd-kit)"),
    ("dnd/jqueryui.html", "jQuery UI draggable/droppable"),
    ("dnd/sortable.html", "SortableJS (HTML5 native)"),
    ("dnd/sortable.html?fallback=1", "SortableJS forceFallback (chuột)"),
    ("dnd/interact.html", "interact.js dropzone"),
    ("dnd/touch.html", "Chỉ touch events (kiểu mobile)"),
    ("dnd/taptap.html", "Bấm từ rồi bấm chỗ trống"),
    ("dnd/hold.html", "Nhấn giữ 400ms mới kéo"),
    ("dnd/iframe.html", "HTML5 trong iframe khác origin"),
]


def _observe_pair(browser):
    obs = browser.execute("ai_observe", {"marks": False})
    assert obs["status"] == "ok", obs
    els = obs["elements"]
    src = next((e for e in els if e.get("text") == "did"), None)
    tgt = next((e for e in els if e.get("dropzone")), None)
    return src, tgt, els


def _result(browser):
    return any(r is True for r in frames_eval(browser, "typeof window.__result === 'function' ? window.__result() : null"))


@pytest.mark.parametrize("path,label", PAGES, ids=[p[0].split("/")[-1] for p in PAGES])
def test_drag_auto(browser, goto, path, label):
    browser._dnd_pref.clear()  # exercise the full chain, not a remembered strategy
    goto(path)
    src, tgt, els = _observe_pair(browser)
    assert src, f"source word not detected: {[e.get('text') for e in els]}"
    assert tgt, f"drop target not detected on {label}"
    assert src.get("draggable") or path.endswith("taptap.html"), f"source not flagged draggable: {src}"
    res = browser.execute("ai_act", {"action": "drag", "id": src["id"], "target_id": tgt["id"]}, timeout=60)
    assert res["status"] == "ok", res
    assert _result(browser), f"{label}: engine reported '{res.get('method')}' but the page shows no correct drop"


def test_drag_preference_is_remembered(browser, goto):
    browser._dnd_pref.clear()
    goto("dnd/touch.html")
    src, tgt, _ = _observe_pair(browser)
    first = browser.execute("ai_act", {"action": "drag", "id": src["id"], "target_id": tgt["id"]}, timeout=60)
    assert first["method"] == "touch"
    goto("dnd/touch.html")
    src, tgt, _ = _observe_pair(browser)
    t0 = time.time()
    second = browser.execute("ai_act", {"action": "drag", "id": src["id"], "target_id": tgt["id"]}, timeout=60)
    assert second["method"] == "touch" and time.time() - t0 < 3, "remembered strategy should be tried first"


def test_drag_reports_failure_honestly(browser, goto):
    """Dropping onto an element that ignores drops must be reported as a failure, not success."""
    browser._dnd_pref.clear()
    goto("dnd/html5.html")
    obs = browser.execute("ai_observe", {"marks": False})
    did = next(e for e in obs["elements"] if e.get("text") == "did")
    done = next(e for e in obs["elements"] if e.get("text") == "done")
    res = browser.execute("ai_act", {"action": "drag", "id": did["id"], "target_id": done["id"]}, timeout=90)
    assert res["status"] == "error" and res.get("tried"), res


def test_drag_matrix(browser, goto):
    """Every strategy × every page. Writes a compatibility table; fails only if a page has no working strategy."""
    strategies = BrowserManager.DRAG_STRATEGIES
    rows, missing = [], []
    for path, label in PAGES:
        cells = []
        for strat in strategies:
            goto(path)
            src, tgt, _ = _observe_pair(browser)
            res = browser.execute("ai_act", {"action": "drag", "id": src["id"], "target_id": tgt["id"], "method": strat}, timeout=60)
            ok = res["status"] == "ok" and _result(browser)
            cells.append("✅" if ok else "—")
        if "✅" not in cells:
            missing.append(label)
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    REPORTS.mkdir(exist_ok=True)
    table = ["| Trang thử | " + " | ".join(strategies) + " |", "|---|" + "---|" * len(strategies)] + rows
    (REPORTS / "dnd_matrix.md").write_text(
        "# Ma trận kéo thả\n\n✅ = chiến lược đó một mình đã đặt đúng từ vào chỗ trống.\n\n" + "\n".join(table) + "\n",
        encoding="utf-8")
    assert not missing, f"no strategy works for: {missing}"
