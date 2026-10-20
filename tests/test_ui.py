"""The web UI driven like a user, in a separate headless Chromium. Saves light/dark screenshots to tests/reports."""
import pytest
from playwright.sync_api import sync_playwright

from conftest import REPORTS, ROOT, api_post


@pytest.fixture(scope="module")
def ui(server):
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1600, "height": 950}, color_scheme="dark")
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=server)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        api_post(server, "/api/goto", {"url": (ROOT / "tests" / "fixtures" / "quiz.html").as_uri()})
        page.goto(server)
        page.wait_for_function("document.getElementById('screenOverlay').hidden === true", timeout=20000)
        page.errors = errors
        yield page
        b.close()


def _remote(server, script):
    return api_post(server, "/api/ai_command", {"cmd": "eval", "payload": {"script": script}})[1]["result"]


def test_loads_without_errors_and_uses_primer(ui):
    assert ui.locator(".tab").count() >= 1
    assert ui.locator("svg.octicon").count() > 20, "Octicons must be hydrated"
    assert ui.evaluate("getComputedStyle(document.body).getPropertyValue('--bgColor-default').trim()") != ""
    assert ui.locator("#sbConnText").inner_text() == "Đã kết nối"
    assert not ui.errors, ui.errors


def test_click_and_type_through_canvas(ui, server):
    pos = _remote(server, "(() => { const r = document.getElementById('g1').getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; })()")
    box = ui.locator("#screenCanvas").bounding_box()
    ui.mouse.click(box["x"] + pos[0] / 1280 * box["width"], box["y"] + pos[1] / 800 * box["height"])
    ui.keyboard.press("Control+a")
    ui.keyboard.type("wentt", delay=15)
    ui.keyboard.press("Backspace")
    ui.wait_for_timeout(500)
    assert _remote(server, "document.getElementById('g1').value") == "went"
    ui.keyboard.press("Control+a")
    ui.keyboard.press("Control+c")
    ui.wait_for_timeout(700)
    assert ui.evaluate("navigator.clipboard.readText()") == "went"


def test_settings_dialog_and_theme(ui):
    ui.click("#btnSettings")
    assert ui.locator(".Overlay").is_visible()
    assert ui.locator("#setListen").is_checked()
    ui.wait_for_timeout(400)  # let the open animation finish
    ui.screenshot(path=str(REPORTS / "ui_settings_dark.png"))
    ui.keyboard.press("Escape")
    assert not ui.locator(".Overlay").is_visible()
    ui.click("#btnTheme")  # auto -> light
    assert ui.evaluate("document.documentElement.dataset.colorMode") == "light"
    ui.wait_for_timeout(300)
    ui.screenshot(path=str(REPORTS / "ui_light.png"))
    ui.click("#btnTheme")  # light -> dark
    ui.wait_for_timeout(300)
    ui.screenshot(path=str(REPORTS / "ui_dark.png"))
    ui.click("#btnTheme")  # back to auto


def test_text_drawer_lists_iframe_text(ui):
    ui.click("#btnText")
    ui.wait_for_selector(".text-block")
    assert "last holiday" in ui.locator("#textDrawerBody").inner_text()
    ui.fill("#textSearch", "holiday")
    assert ui.locator(".text-block").count() == 1
    ui.click("#btnCloseDrawer")


def test_run_without_key_shows_warning(ui):
    ui.fill("#aiInput", "Giải bài này")
    ui.keyboard.press("Enter")
    ui.wait_for_selector(".msg.warning", timeout=10000)
    assert "API Key" in ui.locator(".msg.warning").last.inner_text()
    assert not ui.errors, ui.errors
