"""Production settings expose shipped capabilities; explicit research stays separate."""
from core.vool_settings_page import render_vool_settings_html, settings_groups


def test_production_has_no_agent_network_category_or_search_rows(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    groups = settings_groups()
    assert "network" not in {group["id"] for group in groups}
    assert not any(row["id"] == "network_research_note" for group in groups for row in group["rows"])
    for locale in ("en", "de", "he"):
        html = render_vool_settings_html(ui_locale=locale)
        assert '"id":"network"' not in html
        assert '"id":"network_research_note"' not in html


def test_explicit_research_retains_network_settings(monkeypatch):
    monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", "1")
    network = next(group for group in settings_groups() if group["id"] == "network")
    assert {"network_research_note", "accept_hive_tasks", "idle_research_assist"} <= {row["id"] for row in network["rows"]}


def test_served_language_picker_is_enabled_and_persists_across_screens(monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    from core.i18n.locales import ui_catalog_tags
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    runtime = RuntimeServices(display_name="Release settings fixture")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def answer(self, result):
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            self.end_headers()
            self.wfile.write(result.body or b"")
        def do_GET(self):
            u = urlsplit(self.path)
            self.answer(dispatch_get(path=u.path, query=parse_qs(u.query), headers=dict(self.headers),
                runtime=runtime, model_name="fixture", client_host="127.0.0.1"))
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            self.answer(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                runtime=runtime, model_name="fixture", client_host="127.0.0.1"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    page = browser.new_page(); errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page.goto(base + "/settings")
        picker = page.locator("#uiLocaleSelect")
        page.wait_for_function("document.getElementById('uiLocaleSelect').options.length > 1")
        assert picker.is_enabled()
        assert set(picker.locator("option").evaluate_all("els => els.map(el => el.value)")) == set(ui_catalog_tags())
        assert page.locator('[data-group="network"]').count() == 0
        for tag in ("lt", "de", "ar", "en"):
            with page.expect_navigation(): picker.select_option(tag)
            page.wait_for_function("tag => document.documentElement.lang === tag", arg=tag)
            assert picker.input_value() == tag
            assert page.locator("html").get_attribute("dir") == ("rtl" if tag == "ar" else "ltr")
            page.reload()
            assert picker.input_value() == tag
            assert page.locator("html").get_attribute("lang") == tag
        with page.expect_navigation(): picker.select_option("de")
        page.goto(base + "/chat")
        assert page.locator("html").get_attribute("lang") == "de"
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()
