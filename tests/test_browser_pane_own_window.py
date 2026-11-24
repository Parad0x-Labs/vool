"""The browser pane offers the browser as a window of its own, where it is seen at once.

The real pane page in a real engine, the app's own doors, and the real everyday window: a
click on the bar or on the empty view has the engine open the typed address in a window
outside VOOL. The failure paths are the product's own: a computer with no engine, and a
Browser that is switched off.
"""
from __future__ import annotations

import os

import pytest

from core.i18n import catalog_for
from core.vool_browser import desk as desk_module
from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_browser_pane_private_web import pane as pane
from tests.test_desk_browser import _quiet_test_engine, _wait
from tests.test_desk_browser import site as site
from tests.test_live_browser import world as world


def _window_state(pane) -> dict:
    return pane.evaluate("fetch('/api/browser/window').then(r => r.json())")


@pytest.fixture
def a_window_can_open(request, monkeypatch, tmp_path):
    if not desk_module.engine_binary():
        pytest.skip("no Chromium-family engine on this machine")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    if os.name == "posix" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and os.uname().sysname != "Darwin":
        pytest.skip("a window needs a display")
    request.addfinalizer(desk_module.stop_all)
    _quiet_test_engine(monkeypatch, tmp_path)


def test_the_bar_and_the_empty_view_each_open_the_typed_address_in_a_window_of_its_own(pane, site, a_window_can_open):
    from playwright.sync_api import expect

    base, hits = site
    words = catalog_for("en").text("live.window")
    on_the_bar = pane.get_by_role("button", name=words, exact=True).and_(pane.locator("#popOut"))
    on_the_empty_view = pane.locator("#blankWindow")

    # Seen without opening any menu: one on the bar, one in the middle of the empty view.
    expect(on_the_bar).to_be_visible()
    expect(pane.locator("#menu")).to_be_hidden()
    expect(on_the_empty_view).to_be_visible()
    expect(on_the_empty_view).to_have_text(words)
    assert _window_state(pane)["open"] is False and hits == []

    pane.locator("#address").fill(base + "/from-the-bar")
    on_the_bar.click()
    assert _wait(lambda: any(path == "/from-the-bar" for path, _ in hits), 30), hits
    assert _window_state(pane)["open"] is True

    # The window is the engine's own, not the pane's view: the pane showed no page for it,
    # and the site was asked by a browser that runs page scripts and keeps its cookie.
    expect(pane.locator("#frame")).to_be_hidden()
    pane.locator("#address").fill(base + "/from-the-empty-view")
    on_the_empty_view.click()
    assert _wait(lambda: any(path == "/from-the-empty-view" for path, _ in hits), 30), hits
    assert dict(hits)["/from-the-empty-view"] == "seen=yes"


def test_on_a_computer_with_no_engine_the_button_says_so_and_nothing_opens(pane, site, a_window_can_open, monkeypatch):
    from playwright.sync_api import expect

    base, hits = site
    monkeypatch.setattr(desk_module, "engine_binary", lambda: None)
    pane.locator("#address").fill(base + "/nowhere")
    pane.locator("#popOut").click()
    expect(pane.locator("#status")).to_have_text(catalog_for("en").text("live.engine_missing"))
    assert _window_state(pane)["open"] is False and hits == []


def test_with_the_browser_switched_off_no_window_is_offered_or_opened(pane, site, a_window_can_open):
    from playwright.sync_api import expect

    base, hits = site
    switched = pane.evaluate(
        "fetch('/api/browser/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({enabled: false})}).then(r => r.status)"
    )
    assert switched == 200
    pane.locator("#address").fill(base + "/while-off")
    pane.locator("#popOut").click()
    # The empty view now says the Browser is off, and no longer offers a window that cannot open.
    expect(pane.locator("#blankTitle")).to_have_text(catalog_for("en").text("vcs.state.egress_refused"))
    expect(pane.locator("#blankWindow")).to_be_hidden()
    assert _window_state(pane)["open"] is False and hits == []
