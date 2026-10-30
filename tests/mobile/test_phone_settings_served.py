"""Owner-local Settings uses the same live runtime as the phone socket."""
import socket
from urllib.error import URLError

import pytest
from test_phone_listener_served import desktop, http


def test_fresh_off_and_upgrade_ignore_legacy_bind(desktop, monkeypatch):
    base, runtime, home = desktop
    monkeypatch.setenv("VOOL_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("VOOL_MOBILE_ADVERTISE_HOST", "192.168.1.30:9999")
    status, payload = http(base, "/api/mobile/settings")
    assert status == 200, payload
    assert payload["enabled"] is False and payload["running"] is False
    assert not (home / "data/mobile_companion/listener.json").exists()
    assert runtime.phone_listener.server is None


@pytest.mark.parametrize("path", [
    "/api/chat", "/api/commands", "/api/wallet/status", "/api/wallet/approve",
    "/api/settings/credentials", "/api/settings/prefs", "/settings",
    "/api/mobile/settings", "/api/mobile/pairing/start", "/api/mobile/pairing/page",
    "/api/mobile/devices", "/api/mobile/devices/revoke", "/api/mobile/receipts",
    "/api/mobile/companion/../devices", "/api/mobile/%64evices", "/healthz",
])
def test_only_closed_companion_routes_answer_even_with_spoofed_host(desktop, path):
    base, runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
    assert status == 200, value
    phone = f"http://127.0.0.1:{value['port']}"
    assert runtime.phone_listener.server is not None
    for body in (None, {}):
        status, refused = http(phone, path, body, {"Host": "localhost"})
        assert status == 404, (path, status, refused)


def test_pairing_routes_and_switch_off_close_socket(desktop):
    base, runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
    assert status == 200, value
    phone = f"http://127.0.0.1:{value['port']}"
    assert http(phone, "/api/mobile/info")[0] == 200
    status, pairing = http(base, "/api/mobile/pairing/start", {})
    assert status == 200, pairing
    assert f":{value['port']}?" in pairing["qr_uri"]
    assert http(phone, "/api/mobile/pairing/challenge?pairing_id=" + pairing["pairing_id"])[0] == 200
    assert http(phone, "/api/mobile/pairing/claim", {})[0] == 400
    assert http(phone, "/api/mobile/info", headers={"Origin": base})[0] == 403
    assert http(base, "/api/mobile/settings", {"enabled": False})[0] == 200
    assert runtime.phone_listener.server is None
    with pytest.raises((URLError, ConnectionError)):
        http(phone, "/api/mobile/info")


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "224.0.0.1", "::", "invalid"])
def test_network_choice_never_widens_to_public_or_all_interfaces(desktop, address):
    base, runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": address})
    assert status == 400, value
    assert runtime.phone_listener.server is None


def test_remote_peer_cannot_change_settings_even_with_local_host_header():
    from starlette.testclient import TestClient

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    with TestClient(create_app(RuntimeServices()), client=("192.168.1.56", 54000)) as client:
        for method in ("get", "post"):
            kwargs = {"headers": {"Host": "localhost"}}
            if method == "post":
                kwargs["json"] = {"enabled": True, "address": "127.0.0.1"}
            response = getattr(client, method)("/api/mobile/settings", **kwargs)
            assert response.status_code == 403


def test_settings_switch_in_chromium(desktop, tmp_path):
    import os
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    base, runtime, _home = desktop
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(base + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            toggle = page.get_by_role("switch", name="Use VOOL from my phone")
            assert toggle.get_attribute("aria-checked") == "false"
            network = page.get_by_label("Network address")
            assert network.locator("option").count() > 0, "no home/private interface is available"
            toggle.click()
            page.wait_for_function("document.querySelector('[role=switch][aria-label=\"Use VOOL from my phone\"]').getAttribute('aria-checked') === 'true'")
            assert runtime.phone_listener.running
            shots = Path(os.environ.get("VOOL_PHONE_SHOTS_DIR") or tmp_path)
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / "phone-settings-on.png"), full_page=True)
            toggle.click()
            page.wait_for_function("document.querySelector('[role=switch][aria-label=\"Use VOOL from my phone\"]').getAttribute('aria-checked') === 'false'")
            assert not runtime.phone_listener.running
        finally:
            browser.close()


def test_registry_configures_the_live_socket_and_read_requires_owner(desktop):
    base, runtime, _ = desktop
    status, value = http(base, "/api/commands/dispatch", {
        "command_id": "mobile.settings.configure", "input": {"body": {"enabled": True, "address": "127.0.0.1"}}})
    assert status == 200, value
    assert runtime.phone_listener.running
    status, value = http(base, "/api/commands/dispatch", {"command_id": "mobile.settings.read", "input": {}})
    assert status == 200, value
    assert value["data"]["running"] is True
    from core.command_registry.execute import ExecutionContext, execute_command
    refused = execute_command("mobile.settings.configure", {"body": {"enabled": False}},
        context=ExecutionContext(principal="model", extra={"client_host": "127.0.0.1", "runtime": runtime}))
    assert not refused.ok and runtime.phone_listener.running
    refused = execute_command("mobile.settings.read", {}, context=ExecutionContext(extra={"runtime": runtime}))
    assert not refused.ok
    http(base, "/api/mobile/settings", {"enabled": False})
