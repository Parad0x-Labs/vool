"""The .null browser page script must parse and its controls must act.

Pins a real bug: the page was a non-raw Python string, so the ``\\n`` escapes in the
``window.confirm`` text became real line breaks inside a JS string literal, the whole script
failed to parse, and no control on /web0 did anything.
"""
from __future__ import annotations

import json

from tests.served_browser import launch_chromium


def test_the_web0_page_script_parses_and_go_resolves_the_typed_name() -> None:
    from core.null_browser_page import render_null_browser_html

    manager, browser = launch_chromium()
    page = browser.new_page()
    errors: list[str] = []
    asked: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def answer(route):
        # No resolver, chain or gateway is contacted: every resolve is answered here.
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps({"ok": False, "error": "not_found"}))

    try:
        page.route("**/api/web0/resolve**", answer)
        page.route("http*://**", lambda route: route.abort() if "/api/web0/resolve" not in route.request.url else route.fallback())
        page.set_content(render_null_browser_html())
        page.wait_for_function("() => document.querySelector('#placeholder h2').textContent.includes('web0.null')")
        assert not errors, errors
        assert any("name=web0" in url for url in asked), asked
        page.fill("#addr", "example.null")
        page.click("#go")
        page.wait_for_function("() => document.querySelector('#placeholder h2').textContent.includes('example.null')")
        assert any("name=example" in url for url in asked), asked
        # The failure is told in words, not as a raw error code.
        assert "not_found" not in page.inner_text("#placeholder")
    finally:
        page.close()
        browser.close()
        manager.stop()


def _page_served_at(browser, origin: str, resolve_here: bool, asked: list[str]):
    from core.null_browser_page import render_null_browser_html

    html = render_null_browser_html()
    page = browser.new_page()

    def handle(route):
        url = route.request.url
        if url == origin + "/web0":
            return route.fulfill(status=200, content_type="text/html", body=html)
        if "/api/web0/resolve" in url:
            asked.append(url)
            if url.startswith(origin) and not resolve_here:
                return route.fulfill(status=404, content_type="text/html", body="<h1>Not found</h1>")
            return route.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                                 body=json.dumps({"ok": False, "error": "not_found"}))
        return route.abort()

    page.route(lambda url: url.startswith("http"), handle)
    page.goto(origin + "/web0")
    page.wait_for_function("() => document.querySelector('#placeholder h2').textContent.includes('web0.null')")
    return page


def test_web0_asks_the_server_that_served_it_on_any_port() -> None:
    # Pins a real bug: served on a port other than 11435, the page still sent every resolve to
    # localhost:11435, so a VOOL running on another port could not open any .null name.
    manager, browser = launch_chromium()
    try:
        asked: list[str] = []
        page = _page_served_at(browser, "http://vool.test:11562", True, asked)
        assert asked and all(url.startswith("http://vool.test:11562/api/web0/resolve") for url in asked), asked
        page.close()
        # A server that serves the page but has no VOOL API (the Meet server) falls back to the default API port.
        asked = []
        page = _page_served_at(browser, "http://meet.test:8766", False, asked)
        assert any(url.startswith("http://localhost:11435/api/web0/resolve") for url in asked), asked
        page.close()
    finally:
        browser.close()
        manager.stop()
