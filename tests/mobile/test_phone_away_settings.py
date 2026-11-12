"""The owner switches "Also use VOOL away from home" on in Settings, in a real browser."""
import os
import sys
from pathlib import Path

import pytest
from test_phone_listener_served import desktop, http

from core import tor_runtime
from core.mobile_companion import away
from core.tor_runtime import TorRuntime

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_tor.py"
SWITCH = "Also use VOOL away from home"


@pytest.fixture
def tor(tmp_path, monkeypatch):
    launcher = tmp_path / "tor"
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "never")
    runtime = TorRuntime(data_dir=tmp_path / "tor-data", binary=str(launcher))
    monkeypatch.setattr(tor_runtime, "shared", lambda: runtime)
    store: dict[str, str] = {}
    monkeypatch.setattr(away, "_load_key", lambda: (store.get("key", ""), store.get("address", "")))
    monkeypatch.setattr(away, "_save_key", lambda key, address: store.update(key=key, address=address))
    yield runtime
    runtime.stop()


def _checked(page, name, value):
    page.wait_for_function(
        "([name, value]) => document.querySelector('[role=switch][aria-label=\"' + name + '\"]').getAttribute('aria-checked') === value",
        arg=[name, value])


def test_away_switch_in_chromium_puts_the_address_in_the_pairing_code(desktop, tor, tmp_path):
    from playwright.sync_api import sync_playwright

    base, runtime, _home = desktop
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(base + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            # Phone access off: the away switch cannot be used, and nothing has started.
            assert page.get_by_role("switch", name=SWITCH).is_disabled()
            assert not tor.running
            page.get_by_role("switch", name="Use VOOL from my phone").click()
            _checked(page, "Use VOOL from my phone", "true")
            assert not tor.running and runtime.phone_listener.away is None  # still off by default

            page.get_by_role("switch", name=SWITCH).click()
            _checked(page, SWITCH, "true")
            address = runtime.phone_listener.away.host
            assert away.HIDDEN_HOST.fullmatch(address) and tor.running
            status, pairing = http(base, "/api/mobile/pairing/start", {})
            assert status == 200 and pairing["qr_uri"].endswith("&away=" + address)
            # The pairing page draws its own code: that one must carry the away address too.
            import urllib.request
            with urllib.request.urlopen(base + pairing["pairing_page"], timeout=10) as reply:
                drawn = reply.read().decode()
            assert '"away": "' + address + '"' in drawn and '"&away=" + payload.away' in drawn
            shots = Path(os.environ.get("VOOL_PHONE_SHOTS_DIR") or tmp_path)
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / "phone-settings-away-on.png"), full_page=True)

            page.get_by_role("switch", name=SWITCH).click()
            _checked(page, SWITCH, "false")
            assert runtime.phone_listener.away is None and not tor.running
            status, pairing = http(base, "/api/mobile/pairing/start", {})
            assert status == 200 and "away=" not in pairing["qr_uri"]
        finally:
            browser.close()
        http(base, "/api/mobile/settings", {"enabled": False})
