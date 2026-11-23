"""The browser pane's Private web button: it shows the route's state and switches it.

Real pane page in a real engine, the real owner doors, the real Private web service. Tor itself
is the repository's stand-in program (tests/fixtures/fake_tor.py), which speaks Tor's command
line and control protocol and opens a real loopback SOCKS port, so no outside network is used.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from core import private_web, private_web_service, tor_runtime
from core.i18n import catalog_for
from tests.first_run_pact_rig import pact_rig as pact_rig  # noqa: F401  (fixture)
from tests.test_live_browser import world as world  # noqa: F401  (fixture)
from tests.test_tor_runtime import fake_tor as fake_tor  # noqa: F401  (fixture)


@pytest.fixture
def tor_here(tmp_path, monkeypatch, fake_tor):  # noqa: F811
    """This computer has Tor: the stand-in program, as VOOL's one shared Tor."""
    runtime = tor_runtime.TorRuntime(data_dir=tmp_path / "tor-data", binary=fake_tor)
    monkeypatch.setattr(tor_runtime, "_SHARED", runtime)
    monkeypatch.setenv("VOOL_TOR_BINARY", fake_tor)
    yield runtime
    runtime.stop()


@pytest.fixture
def no_tor_here(monkeypatch):
    monkeypatch.delenv("VOOL_TOR_BINARY", raising=False)
    monkeypatch.setattr(tor_runtime, "find_tor_binary", lambda env=None: None)


@pytest.fixture
def pane(world, tmp_path):  # noqa: F811
    """The pane, opened in a real engine against the app's own doors."""
    from playwright.sync_api import sync_playwright

    from core.vool_browser.engine import find_engine_binary, isolation_launch_flags
    from core.web.api.service import dispatch_get, dispatch_post

    rig = world[0]

    class App(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_result(self, response):
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body or b"")))
            self.end_headers()
            self.wfile.write(response.body or b"")

        def do_GET(self):
            parts = urlsplit(self.path)
            self.send_result(dispatch_get(path=parts.path, query=parse_qs(parts.query), runtime=rig.runtime,
                                          model_name="vool", client_host="127.0.0.1", headers=dict(self.headers)))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_result(dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=rig.runtime,
                                           model_name="vool", client_host="127.0.0.1",
                                           workspace_root_provider=lambda: tmp_path))

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    flags = [f for f in isolation_launch_flags(profile_dir=str(tmp_path / "viewer")) if not f.startswith("--user-data-dir=")]
    try:
        with sync_playwright() as driver:
            engine = driver.chromium.launch(executable_path=find_engine_binary(), headless=True, args=flags)
            page = engine.new_page(viewport={"width": 1100, "height": 800})
            page.set_default_timeout(15000)
            page.goto(f"http://127.0.0.1:{server.server_port}/browser?chat=live-test")
            yield page
            engine.close()
    finally:
        # Whatever a check did, Private web ends off and its helper stopped.
        private_web_service.set_route("off", wait=True)
        server.shutdown()


def test_one_click_turns_private_web_on_and_turning_it_off_asks_once(tor_here, pane):
    from playwright.sync_api import expect

    runtime = tor_here
    words = catalog_for("en")
    button, note = pane.locator("#route"), pane.locator("#routeNote")

    # Off: the button says so, and nothing is asked.
    expect(button).to_be_visible()
    expect(button).to_have_attribute("data-state", "off")
    expect(button).to_have_attribute("aria-pressed", "false")
    assert words.text("settings.private_web.state.off") in button.get_attribute("title")
    assert private_web.current_route().kind == "off" and not runtime.running

    # One click turns it on: more private needs no question.
    button.click()
    expect(button).to_have_attribute("data-state", "on", timeout=30000)
    expect(button).to_have_attribute("aria-pressed", "true")
    assert words.text("settings.private_web.state.on") in button.get_attribute("title")
    state = private_web.current_route()
    assert state.kind == "tor" and state.socks_port == runtime.socks_port and runtime.running
    assert private_web_service.status()["state"] == "on"
    expect(note).to_be_hidden()

    # Turning it off is less private: the pane says what changes and waits. Nothing has changed yet.
    button.click()
    expect(pane.locator("#routeText")).to_have_text(words.text("live.route_warning"))
    assert private_web.current_route().kind == "tor" and runtime.running
    pane.get_by_role("button", name=words.text("live.route_keep"), exact=True).click()
    expect(note).to_be_hidden()
    assert private_web.current_route().kind == "tor"

    button.click()
    pane.get_by_role("button", name=words.text("live.route_turn_off"), exact=True).click()
    expect(button).to_have_attribute("data-state", "off")
    expect(note).to_be_hidden()
    assert private_web.current_route().kind == "off" and not runtime.running


def test_on_a_computer_without_a_route_the_button_does_not_switch_the_web_off(no_tor_here, pane):
    from playwright.sync_api import expect

    words = catalog_for("en")
    button = pane.locator("#route")
    expect(button).to_have_attribute("data-state", "off")
    button.click()
    # A route that cannot work is not switched on, because that would only stop every page.
    expect(pane.locator("#routeText")).to_have_text(words.text("live.route_missing"))
    expect(pane.locator("#routeSetup")).to_be_visible()
    assert pane.locator("#routeSetup").get_attribute("href") == "/settings#permissions"
    assert private_web.current_route().kind == "off"
    assert private_web_service.status()["state"] == "off"


@pytest.fixture
def tor_cannot_connect(tor_here, monkeypatch):
    """Tor is there but cannot reach its network. The wait for it is two seconds here, not two minutes."""
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "never")
    start = tor_here.start
    monkeypatch.setattr(tor_here, "start", lambda reason, **more: start(reason, **{**more, "timeout": 2.0}))


def test_a_route_that_is_down_shows_on_the_button_and_in_words(tor_here, tor_cannot_connect, pane):
    from playwright.sync_api import expect

    # The route is chosen, it does not come up, and nothing goes online.
    words = catalog_for("en")
    button = pane.locator("#route")
    expect(button).to_have_attribute("data-state", "off")
    button.click()
    expect(button).to_have_attribute("data-state", "down", timeout=40000)
    expect(pane.locator("#routeText")).to_contain_text(words.text("settings.private_web.state.down"))
    assert private_web.current_route().kind == "tor"
    # The same button gets the person back out.
    button.click()
    pane.get_by_role("button", name=words.text("live.route_turn_off"), exact=True).click()
    expect(button).to_have_attribute("data-state", "off")
    assert private_web.current_route().kind == "off"


def test_the_pane_has_private_web_words_in_every_language():
    from core.i18n.catalog import CATALOGS_DIR
    from core.vool_browser.live_page import render

    english = catalog_for("en")
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        own = catalog_for(tag)
        page = render("chat", tag)
        for key in ("live.route_warning", "live.route_turn_off", "live.route_keep", "live.route_missing"):
            text = own.text(key)
            assert text and (tag == "en" or text != english.text(key)), (tag, key)
            assert json.dumps(text, ensure_ascii=False).strip('"') in page, (tag, key)
