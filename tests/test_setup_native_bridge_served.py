"""The pages prefer the native bridge when a shell exposes one: "Do this later" asks the shell to close the Setup
window (never navigates the page away), and the chat's "Finish setup" line opens the window through the shell.

Real daemon, real Chromium; the bridge is a fake `window.pywebview.api` installed before the page loads, which
records what the page asked of it. Nothing here needs pywebview itself.
"""
from __future__ import annotations

import os

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

FAKE_BRIDGE = """
window.__bridge = { close_setup: [], open_setup: [], open_settings: [] };
window.pywebview = { api: {
  close_setup: (p) => { window.__bridge.close_setup.push(p === undefined ? null : p); return Promise.resolve({ok: true}); },
  open_setup: (step) => { window.__bridge.open_setup.push(step); return Promise.resolve({ok: true}); },
  open_settings: (section) => { window.__bridge.open_settings.push(section); return Promise.resolve({ok: true}); },
}};
"""


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("setup-bridge") / "home"
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


def test_do_this_later_asks_the_shell_to_close_and_never_navigates_away(served):
    daemon, browser = served
    page = browser.new_page()
    page.add_init_script(FAKE_BRIDGE)
    page.goto(f"{daemon.base_url}/setup", wait_until="networkidle")
    page.wait_for_selector("#laterBtn", state="visible", timeout=20000)
    page.click("#laterBtn")
    page.wait_for_function("() => window.__bridge.close_setup.length === 1", timeout=10000)
    assert page.url.rstrip("/").endswith("/setup"), page.url
    assert page.evaluate("window.__bridge.close_setup") == [None]
    page.close()


def test_online_with_a_key_opens_settings_through_the_shell(served):
    daemon, browser = served
    page = browser.new_page()
    page.add_init_script(FAKE_BRIDGE)
    page.goto(f"{daemon.base_url}/setup#step=thinking", wait_until="networkidle")
    button = page.wait_for_selector("[data-open-settings], .open-settings, button:has-text('Online')", timeout=20000)
    button.click()
    page.wait_for_function("() => window.__bridge.open_settings.length >= 1", timeout=10000)
    assert page.url.rstrip("/").split("#")[0].endswith("/setup"), page.url
    page.close()


def test_the_chat_line_opens_setup_through_the_shell_not_the_frame(served):
    daemon, browser = served
    page = browser.new_page()
    page.add_init_script(FAKE_BRIDGE)
    page.goto(f"{daemon.base_url}/chat", wait_until="networkidle")
    page.wait_for_selector("#setupLineOpen", state="visible", timeout=30000)
    page.click("#setupLineOpen")
    page.wait_for_function("() => window.__bridge.open_setup.length === 1", timeout=10000)
    assert page.evaluate("window.__bridge.open_setup") == [""]
    assert page.query_selector("iframe[src*='/setup']") is None, "the frame overlay must not open when the shell has a window"
    page.close()
