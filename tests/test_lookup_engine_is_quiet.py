"""VOOL's own web lookups run in a hidden real browser. That engine may ask the network for
the page it was sent to and for nothing else: no sign-in service, no update or download
server, no reporting. Measured on the real engine through a recording relay; without the
quiet settings it asked for accounts.google.com and the engine maker's download server on
every single lookup. The engine behind a VOOL browser session is held to the same: starting
it asks the network for nothing.
"""
from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_desk_browser import socks  # noqa: F401  (a fixture: a recording relay)
from tools.browser import browser_render


@pytest.fixture
def page():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/slow"):
                # A real results page takes seconds to finish loading. The engine's own services
                # start a moment after it does, so a page that is done at once would hide them.
                time.sleep(4)
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Result</title><p>Jupiter has 95 moons. " + b"More about the moons. " * 200
                             + b"</p><img src='/slow.png'>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()


def _full_engine() -> str:
    """The full browser a person has installed, which is what a lookup runs on their machine.

    The test session otherwise pins lookups to a stripped engine build that has no sign-in or
    update services at all, and on that build this question cannot even be asked.
    """
    from core.vool_browser.engine import find_engine_binary

    binary = find_engine_binary()
    if not binary:
        pytest.skip("no Chromium-family engine on this machine")
    if "headless" in os.path.basename(binary).lower():
        pytest.skip("only a stripped engine build is here; it has none of the services in question")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    return binary


def _lookup(monkeypatch, socks, page) -> tuple[dict, set[str]]:  # noqa: F811
    port, asked = socks
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    # The process's own way out is the relay, so everything the engine asks for is written down.
    monkeypatch.setenv("ALL_PROXY", f"socks5://127.0.0.1:{port}")
    result = browser_render._chrome_render_unchecked(f"http://lookup-site.test:{page}/", timeout_ms=20_000,
                                                     binary=_full_engine())
    time.sleep(1)
    return result, {host for _, host in asked}


def test_a_lookup_asks_the_network_for_its_page_and_nothing_else(monkeypatch, socks, page):  # noqa: F811
    result, hosts = _lookup(monkeypatch, socks, page)
    assert result["status"] == "ok" and "95 moons" in result["text"]
    assert hosts == {"lookup-site.test"}


def test_without_the_quiet_settings_the_same_lookup_contacts_other_servers(monkeypatch, socks, page):  # noqa: F811
    """The measurement that makes the check above mean something on this engine."""
    monkeypatch.setattr(browser_render, "QUIET_FLAGS", ("--disable-background-networking",))
    result, hosts = _lookup(monkeypatch, socks, page)
    assert result["status"] == "ok"
    # If this ever stops holding, the check above no longer proves anything and needs a new measure.
    assert hosts - {"lookup-site.test"}, "this engine made no background contact even without the quiet settings"


def test_the_lookup_engine_and_the_browser_window_share_one_quiet_list():
    from core.vool_browser import desk, engine

    assert desk.QUIET_FLAGS is engine.QUIET_FLAGS is browser_render.QUIET_FLAGS
    argv, profile = browser_render._chrome_argv("engine", "https://example.org/", user_agent="ua",
                                                virtual_time_ms=1000, screenshot_path=None)
    browser_render._cleanup_isolated_profile(profile)
    assert set(engine.QUIET_FLAGS) <= set(argv)
    # Chrome reads only the last of two feature switches: there must be exactly one.
    assert sum(arg.startswith("--disable-features=") for arg in argv) == 1


def _session_start(monkeypatch, socks) -> set[str]:  # noqa: F811
    """Start the engine behind a VOOL browser session on a recorded way out; open no page."""
    from core import private_web
    from core.vool_browser import sessions

    port, asked = socks
    server = f"socks5://127.0.0.1:{port}"
    monkeypatch.setattr(private_web, "browser_route", lambda state=None: private_web.BrowserRoute(
        flags=(f"--proxy-server={server}",), playwright_proxy={"server": server}))
    handle = sessions.SessionHandle(session="quiet", engine_binary=_full_engine(), headless=True)
    try:
        deadline = time.monotonic() + 30
        while handle.state == "opening" and time.monotonic() < deadline:
            time.sleep(0.2)
        assert handle.state == "ready", handle.state
        time.sleep(10)  # the engine's own services start within seconds of it
    finally:
        handle.close()
    return {host for _, host in asked}


def test_starting_a_browser_session_asks_the_network_for_nothing(monkeypatch, socks):  # noqa: F811
    pytest.importorskip("playwright")
    assert _session_start(monkeypatch, socks) == set()


def test_without_the_quiet_settings_a_session_start_contacts_other_servers(monkeypatch, socks):  # noqa: F811
    """The measurement that makes the check above mean something on this engine."""
    pytest.importorskip("playwright")
    from core.vool_browser import engine

    monkeypatch.setattr(engine, "QUIET_FLAGS", ())
    assert _session_start(monkeypatch, socks), "this engine made no background contact even without the quiet settings"
