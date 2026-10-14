"""The Settings Automatic online checks switch.

Automatic online checks (the policy key ``network.outbound_enabled``) are off on a fresh
install and this row is the in-app control for them. The row reads the current policy
through the pact boundary projection (GET /api/onboarding/pact?view=boundary) and writes
through the boundary command that already exists (POST /api/onboarding/pact/boundary).
Everything here drives the real served dispatch seam -- the same routes the browser calls.

A fresh install must still have the value false and make zero network calls; the
network-default tests own that claim and this file must never disturb it.
"""
from __future__ import annotations

import pytest

from tests.first_run_pact_rig import pact_rig  # the shared isolated-home rig fixture

BOUNDARY_READ = "/api/onboarding/pact?view=boundary"
BOUNDARY_WRITE = "/api/onboarding/pact/boundary"


@pytest.fixture(autouse=True)
def _no_web_env(monkeypatch):
    """The switch under test is the app's own; no environment variable may answer for it."""
    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
        monkeypatch.delenv(name, raising=False)


def _read(pact_rig) -> dict:
    status, payload = pact_rig.get(BOUNDARY_READ)
    assert status == 200 and payload["ok"] is True, (status, payload)
    return payload


def test_a_fresh_install_reads_automatic_online_checks_off(pact_rig) -> None:
    from core import policy_engine

    payload = _read(pact_rig)
    assert payload["outbound_enabled"] is False
    assert payload["local_only_mode"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_the_row_switches_checks_on_and_the_policy_agrees(pact_rig) -> None:
    from core import policy_engine

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is True
    assert policy_engine.get("network.outbound_enabled") is True
    # the gate every reader consults, not just the stored value
    assert policy_engine.automatic_outbound_enabled() is True

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_turning_the_checks_off_is_allowed_while_local_only_is_on(pact_rig) -> None:
    """Off is always allowed: the refusal only guards turning background network work on."""
    from core import policy_engine

    pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is False


def test_local_only_mode_refuses_the_switch_and_it_stays_off(pact_rig) -> None:
    from core import policy_engine

    policy_engine.set_operator_policy_values({"system.local_only_mode": True})

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 409 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "invalid_transition"

    payload = _read(pact_rig)
    assert payload["local_only_mode"] is True
    assert payload["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    # local-only vetoes the composite gate too, value or not
    assert policy_engine.automatic_outbound_enabled() is False


def test_a_non_loopback_caller_gets_403_and_nothing_changes(pact_rig) -> None:
    from core import policy_engine

    status, payload = pact_rig.post(
        BOUNDARY_WRITE, {"key": "automatic_checks", "value": True}, client_host="10.1.2.3"
    )
    assert status == 403 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "owner_local_required"

    assert _read(pact_rig)["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_the_switch_never_changes_local_only_mode_or_the_other_boundaries(pact_rig) -> None:
    from core import policy_engine

    for value in (True, False, True):
        status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": value})
        assert status == 200 and payload["ok"] is True, (status, payload)
        payload = _read(pact_rig)
        assert payload["local_only_mode"] is False
        assert payload["allow_web_fallback"] is False  # the web lookups row owns that key
        assert payload["outbound_enabled"] is value
        assert policy_engine.local_only_mode() is False
        assert policy_engine.allow_web_fallback() is False


def test_the_settings_row_is_wired_to_the_boundary_authority() -> None:
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    rows = {row["id"]: row for group in settings_groups() for row in group["rows"]}
    assert rows["web_lookups"]["read"]["url"] == BOUNDARY_READ  # the neighbour it sits next to
    row = rows["automatic_checks"]
    assert row["read"]["url"] == BOUNDARY_READ
    assert row["widget"] == "automatic_checks"
    html = render_vool_settings_html()
    assert "widgetAutomaticChecks" in html
    # the row's widget posts this exact key through the shared boundary command
    assert "{ key: 'automatic_checks', value: want }" in html
    assert BOUNDARY_WRITE in html
    assert BOUNDARY_READ in html


def test_a_home_from_before_the_tour_can_use_the_switch_without_touching_the_tour(pact_rig) -> None:
    from core import policy_engine

    pact_file = _pre_tour_home()
    before = pact_file.read_bytes()

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is True
    assert policy_engine.automatic_outbound_enabled() is True

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert policy_engine.get("network.outbound_enabled") is False

    # The tour record is not touched: such a home still shows no tour, exactly as before.
    assert pact_file.read_bytes() == before


def _pre_tour_home():
    """A home that existed before the tour shipped: its pact file says ``not_applicable``.

    Same shape as the web lookups switch's pre-tour test: most upgraded users have this.
    """
    import json

    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.parent.mkdir(parents=True, exist_ok=True)
    pact_file.write_text(json.dumps({
        "schema_name": "vool.first_run_pact", "schema_version": 1, "state": "not_applicable",
        "revision": 0, "welcome_hidden": False, "seed_reason": "existing_user",
        "steps": {}, "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:00Z",
    }))
    return pact_file


def test_another_website_cannot_switch_the_checks_on(pact_rig) -> None:
    """A page on another origin (or a plain form post) is refused before anything is written."""
    from core import policy_engine

    status, payload = pact_rig.post(
        BOUNDARY_WRITE, {"key": "automatic_checks", "value": True},
        headers={"Content-Type": "application/json", "Origin": "http://attacker.example"},
    )
    assert status == 403, (status, payload)
    status, payload = pact_rig.post(
        BOUNDARY_WRITE, {"key": "automatic_checks", "value": True},
        headers={"Content-Type": "text/plain"},
    )
    assert status == 415, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_an_unreadable_policy_file_refuses_the_write_and_leaves_it_untouched(pact_rig) -> None:
    """Fail closed: when the saved policy cannot be parsed, the switch writes nothing at all."""
    from core import policy_engine
    from core.runtime_paths import active_config_home_dir

    policy_file = active_config_home_dir() / "default_policy.yaml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text("network: [not closed\n", encoding="utf-8")
    before = policy_file.read_bytes()

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 409 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "authority_write_failed"
    assert policy_file.read_bytes() == before
    assert policy_engine.automatic_outbound_enabled() is False


def test_after_the_tour_the_switch_still_works_and_local_only_still_refuses(pact_rig) -> None:
    """Most homes have a tour record; the row must work there too, with the same veto."""
    from core import policy_engine

    status, payload = pact_rig.post("/api/onboarding/pact/begin", {})
    assert status == 200, (status, payload)

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert policy_engine.automatic_outbound_enabled() is True

    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    # local-only vetoes the gate even though the stored value is still on
    assert policy_engine.automatic_outbound_enabled() is False
    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 409 and payload["error"] == "invalid_transition", (status, payload)
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.local_only_mode() is True


# --- the served Settings page, driven in a real browser -------------------------------------------


def _serve_settings(pact_rig):
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.service import dispatch_get, dispatch_post

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, res):
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.end_headers()
            self.wfile.write(res.body)

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/settings":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(render_vool_settings_html().encode())
                return
            self.send(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=pact_rig.runtime,
                                   model_name="vool", client_host="127.0.0.1"))

        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
            self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                                    runtime=pact_rig.runtime, model_name="vool", client_host="127.0.0.1",
                                    workspace_root_provider=lambda: pact_rig.workspace))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


ROW_URL = "/settings#permissions/automatic_checks"


def test_the_served_row_switches_on_and_off_and_shows_the_local_only_veto(pact_rig) -> None:
    from core import policy_engine
    from tests.served_browser import launch_chromium

    server = _serve_settings(pact_rig)
    manager, browser = launch_chromium()
    errors: list[str] = []
    try:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}{ROW_URL}")
        row = page.locator('[data-row="automatic_checks"]')
        sw = row.get_by_role("switch", name="Go online on its own", exact=True)
        sw.wait_for()
        assert "Off by default." in row.inner_text()
        assert sw.get_attribute("aria-checked") == "false" and sw.is_enabled()
        assert not row.get_by_text("Local-only mode is on. Turn it off first.").is_visible()
        # no horizontal scroll at phone width
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        sw.focus()
        page.keyboard.press("Space")
        row.get_by_text("Saved", exact=True).wait_for()
        assert sw.get_attribute("aria-checked") == "true"
        assert policy_engine.automatic_outbound_enabled() is True

        sw.click()
        row.get_by_text("Saved", exact=True).wait_for()
        assert sw.get_attribute("aria-checked") == "false"
        assert policy_engine.get("network.outbound_enabled") is False

        # Local-only mode turned on elsewhere: the row shows off, cannot be switched, and says why.
        policy_engine.set_operator_policy_values({"system.local_only_mode": True})
        page.reload()
        sw.wait_for()
        assert sw.get_attribute("aria-checked") == "false"
        assert sw.is_disabled()
        assert row.get_by_text("Local-only mode is on. Turn it off first.").is_visible()
        assert policy_engine.get("network.outbound_enabled") is False
        assert policy_engine.local_only_mode() is True
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()


def test_the_served_row_refused_by_local_only_mid_click_says_so_and_stays_off(pact_rig) -> None:
    """The page was opened before local-only mode was turned on elsewhere: the click is refused
    by the authority, the row re-reads and shows the real (off, locked) state in plain words."""
    from core import policy_engine
    from tests.served_browser import launch_chromium

    server = _serve_settings(pact_rig)
    manager, browser = launch_chromium()
    errors: list[str] = []
    try:
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}{ROW_URL}")
        row = page.locator('[data-row="automatic_checks"]')
        sw = row.get_by_role("switch", name="Go online on its own", exact=True)
        sw.wait_for()
        policy_engine.set_operator_policy_values({"system.local_only_mode": True})
        sw.click()
        row.get_by_text("Not saved — Local-only mode is on. Turn it off first.", exact=True).wait_for()
        assert sw.get_attribute("aria-checked") == "false"
        assert sw.is_disabled()
        assert "invalid_transition" not in row.inner_text()
        assert policy_engine.get("network.outbound_enabled") is False
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()


def _answer_with(status: int, body: str):
    def handler(route, request=None):
        route.fulfill(status=status, content_type="application/json", body=body)
    return handler


def test_the_served_row_never_guesses_a_state_it_could_not_read(pact_rig) -> None:
    """A consent switch fails closed: a failed or incomplete read draws no switch, shows no raw
    error code, and sends nothing."""
    from core import policy_engine
    from tests.served_browser import launch_chromium

    server = _serve_settings(pact_rig)
    manager, browser = launch_chromium()
    errors: list[str] = []
    posts: list[str] = []
    try:
        for status, body in ((500, '{"ok": false, "error": "policy_unreadable"}'),
                             (200, '{"ok": true, "local_only_mode": false}'),
                             (200, '{"ok": true, "outbound_enabled": "yes", "local_only_mode": false}')):
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
            page.route("**/api/onboarding/pact?view=boundary", _answer_with(status, body))
            page.goto(f"http://127.0.0.1:{server.server_port}{ROW_URL}")
            row = page.locator('[data-row="automatic_checks"]')
            row.get_by_text("VOOL could not read this setting. Reopen Settings to try again.", exact=True).wait_for()
            assert row.get_by_role("switch").count() == 0
            assert "policy_unreadable" not in row.inner_text() and "HTTP" not in row.inner_text()
            page.close()
        assert posts == []
        assert policy_engine.get("network.outbound_enabled") is False
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()
