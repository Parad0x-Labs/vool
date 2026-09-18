"""The optional VOOL Wallet, served: one daemon proves the whole off → refused → enable → create →
status → disable → re-enable arc with no data loss, at the real HTTP doors and the served page.

The chain is the loopback Solana-dialect node answering as Devnet (tests/wallet/_rig). Nothing real
is spent; the wallet is disposable and its home is deleted with the test."""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import served_browser
from tests._blackbox_served_rig import ServedDaemon
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"


@pytest.fixture()
def served(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    # OFF is the shipped default: the preference is absent and the env override is unset
    daemon = ServedDaemon(home, env_extra={
        "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        "VOOL_WALLET_ENABLED": "",
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
        "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        daemon.start(timeout=240)
        seed_daemon(home, provider.base_url)
        provider.reset()
        yield daemon, chain
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        chain.__exit__(None, None, None)


def _get(daemon, path: str) -> tuple[int, dict]:
    with urlopen(daemon.base_url + path, timeout=60) as response:
        return response.status, json.loads(response.read())


def _post(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _prefs(daemon, on: bool) -> tuple[int, dict]:
    return _post(daemon, "/api/settings/prefs", {"wallet_enabled": bool(on)})


def test_off_refusals_enable_create_disable_reenable_without_loss(served):
    daemon, chain = served

    # -- OFF: the status carries the description facts and no wallet exists -----------------------------
    _, status = _get(daemon, "/api/wallet/status")
    off = status["status"]
    assert off["enabled"] is False and off["has_wallet"] is False and off["public_key"] == ""

    # every creation and spend door refuses at the authority, not only in the UI
    for path, body in (
        ("/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": "I ACCEPT THAT THIS DEVICE HOLDS THE KEY", "pin": PIN}),
        ("/api/wallet/external", {"public_key": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "label": "phantom"}),
        ("/api/wallet/watch-only", {"public_key": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"}),
        ("/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN}),
    ):
        code, refused = _post(daemon, path, body)
        assert code == 403 and refused.get("error") == "wallet_disabled", (path, code, refused.get("error"))
    # reconciliation keeps its safe seam
    code, refreshed = _post(daemon, "/api/wallet/transfers/refresh", {})
    assert code == 200 and refreshed.get("ok") is True

    # -- ENABLE (the same authority the UI's Enable button uses) ----------------------------------------
    code, enabled = _prefs(daemon, True)
    assert code == 200 and enabled.get("ok") is True

    # -- CREATE a disposable pilot wallet through the setup doors ---------------------------------------
    code, created = _post(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN,
                                                               "credential_confirmation": PIN, "creation_key": f"optional-{uuid.uuid4().hex}",
                                                               "label": "Optional-state served"})
    assert code == 200, created
    wallet_id = created["setup"]["wallet_id"]
    address = created["setup"]["address"]
    code, revealed = _post(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    ack_token = (revealed.get("backup") or {}).get("ack_token")
    assert code == 200 and ack_token
    code, ready = _post(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert code == 200 and ready["setup"]["setup_state"] == "ready"
    revealed = None

    # -- STATUS/RECOVERY reads answer while enabled ------------------------------------------------------
    _, status = _get(daemon, "/api/wallet/status")
    on = status["status"]
    assert on["enabled"] is True and on["public_key"] == address and on["custody_mode"] != "none"

    # -- DISABLE: creation refuses again, nothing is deleted ---------------------------------------------
    code, disabled = _prefs(daemon, False)
    assert code == 200 and disabled.get("ok") is True
    code, refused_again = _post(daemon, "/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": "I ACCEPT THAT THIS DEVICE HOLDS THE KEY", "pin": PIN})
    assert code == 403 and refused_again.get("error") == "wallet_disabled"
    _, status = _get(daemon, "/api/wallet/status")
    kept = status["status"]
    # existence and permission are separate facts: the wallet is kept, visible as existing, no key served
    assert kept["enabled"] is False and kept["has_wallet"] is True and kept["custody_mode"] != "none"
    assert kept["public_key"] == ""
    _, receipts = _get(daemon, "/api/wallet/receipts")
    assert receipts.get("ok") is True, "read-only history stays available while off"

    # -- RE-ENABLE: the same wallet and its setup state return ------------------------------------------
    _prefs(daemon, True)
    _, status = _get(daemon, "/api/wallet/status")
    back = status["status"]
    assert back["enabled"] is True and back["public_key"] == address and back.get("wallet_id") == wallet_id
    assert chain.send_count() == 0, "nothing was ever dispatched in this arc"

    # -- the served page shows the off state's Enable entry (the UI side of the same authority) -----------
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
        _prefs(daemon, False)
        # the integrated Settings page (the current Home/Settings pattern) hosts the wallet section
        page.goto(daemon.base_url + "/settings#wallet", wait_until="domcontentloaded")
        page.wait_for_selector("#vwSec", timeout=15_000)
        page.wait_for_function("document.getElementById('vwEnableBtn') !== null", timeout=10_000)
        assert page.locator("#vwActions").is_hidden(), "creation entries wait for the enabled state"
        enable = page.locator("#vwEnableBtn")
        enable.click()
        page.wait_for_function("document.getElementById('vwActions') !== null && !document.getElementById('vwActions').hidden", timeout=10_000)
        _, status = _get(daemon, "/api/wallet/status")
        assert status["status"]["enabled"] is True, "the Enable entry drove the real preference authority"
        assert [e for e in errors if "vw" in e] == [], errors
    finally:
        with __import__("contextlib").suppress(Exception):
            browser.close()
        manager.stop()
