"""The optional-wallet contract: OFF is permission, never deletion; creation doors answer to the
same authority the UI does. Proven at the status/API seam (the served-browser proof rides the
browser gates)."""
from __future__ import annotations

import pytest

from core.user_preferences import default_preferences, save_preferences
from core.web.api import wallet_api

pytestmark = [pytest.mark.safety]

HEADERS = {"user-agent": "vool-test", "content-type": "application/json"}


@pytest.fixture()
def off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    save_preferences(default_preferences())
    return tmp_path


def _post(path: str, body: dict):
    return wallet_api.handle_wallet_post(path, body, client_host="127.0.0.1", headers=HEADERS)


def _status() -> dict:
    import json

    response = wallet_api.handle_wallet_get("/api/wallet/status", {}, client_host="127.0.0.1")
    payload = response.body.decode() if hasattr(response, "body") else response
    return json.loads(payload)["status"]


def _enable(on: bool) -> None:
    prefs = default_preferences()
    prefs.wallet_enabled = on
    save_preferences(prefs)


def test_off_reports_existence_without_exposing_or_losing_the_wallet(off_by_default):
    assert _status()["enabled"] is False and _status()["has_wallet"] is False
    _enable(True)
    made = _post("/api/wallet/setup/create", {"network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "method": "pin", "credential": "482913",
                                              "credential_confirmation": "482913", "creation_key": "optional-state-1"})
    import json as _json

    assert made.status == 200, made.body
    public_key = _json.loads(made.body)["setup"]["address"]
    _enable(False)

    off = _status()
    # OFF: permission is False, EXISTENCE stays a visible fact, and no key material is served
    assert off["enabled"] is False and off["has_wallet"] is True
    assert off["custody_mode"] != "none" and off["public_key"] == ""
    # read-only history stays available while off
    receipts = wallet_api.handle_wallet_get("/api/wallet/receipts", {}, client_host="127.0.0.1")
    assert receipts.status == 200

    _enable(True)
    back = _status()
    assert back["enabled"] is True and back["public_key"] == public_key, "re-enable restores the same wallet"
    # disown the disposable wallet evidence is not required: the tmp VOOL_HOME vanishes with the test


def test_every_creation_and_spend_door_refuses_while_off(off_by_default):
    phrase_mn = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    doors = [
        ("/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": "I ACCEPT THAT THIS DEVICE HOLDS THE KEY", "pin": "482913"}),
        ("/api/wallet/pocket/restore", {"recovery_phrase": phrase_mn, "pin": "482913"}),
        ("/api/wallet/watch-only", {"public_key": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"}),
        ("/api/wallet/external", {"public_key": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "label": "phantom"}),
        ("/api/wallet/setup/create", {"network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "method": "pin", "credential": "482913", "credential_confirmation": "482913"}),
        ("/api/wallet/propose", {"destination": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "amount_minor": 1}),
        ("/api/wallet/approve", {"proposal_id": "pay-nonexistent000000000", "pin": "482913"}),
    ]
    for path, body in doors:
        response = _post(path, body)
        import json

        answered = json.loads(response.body)
        assert answered.get("ok") is False and answered.get("error") == "wallet_disabled", (path, answered.get("error"))


def test_reconciliation_keeps_moving_while_off(off_by_default):
    # the observer wake is the safe reconciliation seam: unresolved transfers keep moving with the
    # wallet off, while every new-spend door above stays closed
    response = _post("/api/wallet/transfers/refresh", {})
    import json

    assert json.loads(response.body)["ok"] is True
