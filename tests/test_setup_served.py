"""Served real-Chromium proofs for the guided setup: a real daemon on its own home, the real pages.

One daemon and one browser for the module (booting a daemon is the expensive part); the tests run
in file order and each states the home's condition it starts from. No model is launched: the
scripted provider only exists so the daemon boots the way the product does.
"""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

BLOCK_POPUPS = (
    "window.open = () => null;"
    "try { Object.defineProperty(window, 'pywebview', { value: undefined, configurable: true }); } catch (e) {}"
)
DRAFT = "an unsent draft that must survive the setup window"


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except Exception as exc:  # HTTPError carries the body
        body_bytes = getattr(exc, "read", lambda: b"{}")()
        try:
            return int(getattr(exc, "code", 0) or 0), json.loads(body_bytes or b"{}")
        except Exception:
            return int(getattr(exc, "code", 0) or 0), {}


def _get(base: str, path: str) -> dict:
    with urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("setup-served") / "home"
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={"PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        try:
            daemon.start(timeout=180)
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            yield daemon, browser
        finally:
            browser.close()
            manager.stop()
            daemon.stop()


def _wait_frame(page) -> None:
    page.wait_for_function(
        "(() => { const f = document.getElementById('settingsFrame'); const d = f && f.contentDocument;"
        " return !!(d && d.readyState === 'complete' && d.getElementById('nextBtn') && d.querySelectorAll('.dot').length === 4); })()",
        timeout=30_000,
    )


def test_fresh_home_shows_the_line_not_the_checklist_and_the_window_walks_skips_and_leaves(served):
    daemon, browser = served
    state = _get(daemon.base_url, "/api/setup/state")
    assert state["done_count"] == 0 and state["show_chat_line"] is True, state

    page = browser.new_page()
    page.add_init_script(BLOCK_POPUPS)
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=30_000)
    assert page.evaluate("document.getElementById('pactCard')") is None, "the eight-step checklist is gone"
    page.wait_for_selector("#setupLine:not([hidden])", timeout=15_000)
    assert "four quick steps" in page.inner_text("#setupLine").lower()
    page.fill("#input", DRAFT)
    href_before = page.evaluate("location.href")

    # Open the window the way the chat opens Settings when popups are blocked: the same frame overlay.
    page.click("#setupLineOpen")
    page.wait_for_selector("#settingsFrameOverlay:not([hidden])", timeout=15_000)
    _wait_frame(page)
    assert page.evaluate("document.getElementById('settingsFrame').contentWindow.location.pathname") == "/setup"
    frame = page.frame_locator("#settingsFrame")
    assert frame.locator(".dot").count() == 4
    assert frame.locator(".dot.current").get_attribute("data-step") == "thinking"
    assert frame.locator("#stepTitle").inner_text() == "Where should it think?"
    assert frame.locator(".mock-badge").inner_text().strip().lower() == "example"
    assert frame.locator("#skipBtn").is_visible() and frame.locator("#laterBtn").is_visible()
    # Next moves, Back returns, Skip records and moves on.
    frame.locator("#nextBtn").click()
    page.wait_for_function("document.getElementById('settingsFrame').contentWindow.__voolSetup.stepId() === 'folder'", timeout=10_000)
    frame.locator("#backBtn").click()
    page.wait_for_function("document.getElementById('settingsFrame').contentWindow.__voolSetup.stepId() === 'thinking'", timeout=10_000)
    frame.locator("#nextBtn").click()
    page.wait_for_function("document.getElementById('settingsFrame').contentWindow.__voolSetup.stepId() === 'folder'", timeout=10_000)
    frame.locator("#skipBtn").click()
    page.wait_for_function("document.getElementById('settingsFrame').contentWindow.__voolSetup.stepId() === 'permissions'", timeout=10_000)
    assert _get(daemon.base_url, "/api/setup/state")["skipped"] == ["folder"], "skip is persisted as a preference"
    assert frame.locator(".dot[data-step='folder']").get_attribute("class") == "dot skipped"
    # The real control does the real thing: the autonomy choice lands in the preferences authority.
    frame.locator("button.choice[data-autonomy='strict']").click()
    page.wait_for_function(
        "(() => { const w = document.getElementById('settingsFrame').contentWindow; const s = w.__voolSetup.state();"
        " return !!(s && s.steps && s.steps[2].done); })()", timeout=15_000)
    assert _get(daemon.base_url, "/api/settings/prefs")["autonomy_mode"] == "strict"
    assert frame.locator("#stepStatus").inner_text().startswith("Done")
    assert not frame.locator("#skipBtn").is_visible(), "nothing left to skip on a done step"
    # "Do this later" closes the frame; the chat, its draft and its location are untouched.
    frame.locator("#laterBtn").click()
    page.wait_for_selector("#settingsFrameOverlay", state="hidden", timeout=15_000)
    assert page.input_value("#input") == DRAFT
    assert page.evaluate("location.href") == href_before
    page.wait_for_function("document.getElementById('setupLineText').textContent.includes('1 of 4')", timeout=15_000)
    page.close()


def test_reduced_motion_shows_the_static_final_frame_and_full_motion_animates(served):
    daemon, browser = served
    ctx = browser.new_context(reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(daemon.base_url + "/setup#step=thinking", wait_until="networkidle")
    page.wait_for_selector(".mk-tick", timeout=15_000)
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).animationName") == "none"
    assert page.evaluate("getComputedStyle(document.querySelector('.cursor')).display") == "none"
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).opacity") == "1", "the final frame is shown"
    assert page.inner_text("#motionBtn") == "Play the example"
    page.click("#motionBtn")
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).animationName") == "tick-in"
    page.close()
    ctx.close()

    page = browser.new_page()
    page.goto(daemon.base_url + "/setup#step=thinking", wait_until="networkidle")
    page.wait_for_selector(".mk-tick", timeout=15_000)
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).animationName") == "tick-in"
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).animationDuration") == "6s"
    assert page.evaluate("getComputedStyle(document.querySelector('.cursor')).display") == "block"
    assert page.inner_text("#motionBtn") == "Pause"
    page.click("#motionBtn")
    assert page.evaluate("getComputedStyle(document.querySelector('.mk-tick')).animationPlayState") == "paused"
    page.close()


def test_settings_entry_ticks_a_step_configured_in_settings_and_vanishes_when_everything_is_done(served, tmp_path):
    daemon, browser = served
    page = browser.new_page()
    page.goto(daemon.base_url + "/settings", wait_until="networkidle")
    page.wait_for_selector("#setupCount", timeout=15_000)
    assert page.evaluate("window.__voolSettings.groups()[0]") == "setup"
    assert page.inner_text("#setupCount") == "1 of 4 done"   # the strict choice made in the window above
    assert page.locator("#setupSteps [data-step='permissions']").get_attribute("data-done") == "true"
    assert page.locator("#setupSteps [data-step='name']").get_attribute("data-done") == "false"

    # Configure the name directly through the runtime's own profile door (what Settings' Memory
    # section and the chat both write), never through the setup window: the tick must appear anyway.
    status, body = _post(daemon.base_url, "/api/profile/remember", {"category": "preferred_name", "value": "Sam", "scope": "global", "replace": True})
    assert status == 200, body
    page.evaluate("window.__voolSettings.reconcile()")
    page.wait_for_function("document.getElementById('setupCount').textContent === '2 of 4 done'", timeout=15_000)
    assert page.locator("#setupSteps [data-step='name']").get_attribute("data-done") == "true"

    # Finish the remaining two through their real doors; the entry must disappear, not tick to 4.
    folder = tmp_path / "Docs"
    folder.mkdir()
    status, body = _post(daemon.base_url, "/api/projects", {"name": "Docs", "root": str(folder)})
    assert status == 200 and body.get("project"), body
    status, body = _post(daemon.base_url, "/api/onboarding/choice", {"choice": "local_only"})
    assert status == 200, body
    state = _get(daemon.base_url, "/api/setup/state")
    assert state["complete"] is True and state["show_entry"] is False and state["show_chat_line"] is False, state
    page.evaluate("window.__voolSettings.reconcile()")
    page.wait_for_function("window.__voolSettings.groups()[0] !== 'setup'", timeout=15_000)
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#paneTitle", timeout=15_000)
    assert "setup" not in page.evaluate("window.__voolSettings.groups()")
    assert page.inner_text("#paneTitle") == "General"
    assert page.locator(".nav-item[data-group='setup']").count() == 0
    page.close()

    chat = browser.new_page()
    chat.add_init_script(BLOCK_POPUPS)
    chat.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    chat.wait_for_selector("#input", timeout=30_000)
    # The markup starts hidden, so `hidden === true` alone would pass before the state fetch decides.
    chat.wait_for_function(
        "(() => { const l = document.getElementById('setupLine'); return l.dataset.loaded === '1' && l.hidden === true; })()",
        timeout=15_000)
    chat.close()
