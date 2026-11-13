"""The Skills & plugins wallet's own limits in Settings: the owner sees and sets the caps every payment from that wallet
is reserved against (``core.wallet.limits``). Lowering needs nothing; raising is the owner's act (their own wallet's
PIN); an owner's own wallet is not reached through this door; and the numbers shown are the ones enforced."""
from __future__ import annotations

import json

import pytest

from core.wallet import agent_wallets, caller_binding, custody, limits
from core.web.api.wallet_api import handle_wallet_get, handle_wallet_post
from tests.wallet._rig import wallet_env

PIN = "730461"
HEADERS = {"host": "127.0.0.1:8731", "origin": "http://127.0.0.1:8731", "content-type": "application/json"}
DOOR = "/api/wallet/agents/caps"


def _post(path: str, body: dict, headers: dict | None = HEADERS) -> tuple[int, dict]:
    response = handle_wallet_post(path, body, client_host="127.0.0.1", headers=headers)
    return response.status, json.loads(response.body)


def _overview() -> dict:
    response = handle_wallet_get("/api/wallet/agents", {}, client_host="127.0.0.1")
    payload = json.loads(response.body)
    assert response.status == 200 and payload["ok"] is True
    return payload["agent_spending"]


@pytest.fixture
def owner(wallet_env):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


@pytest.fixture
def addons_wallet(owner) -> dict:
    network = _overview()["networks"][0]["network"]
    status, made = _post("/api/wallet/agents/wallet", {"agent_id": "addons", "network": network, "credential": PIN})
    assert status == 200, made
    return agent_wallets.acknowledge_backup(made["wallet"]["wallet_id"], ack_token=made["backup"]["ack_token"])


def _shown(wallet_id: str) -> list[dict]:
    return next(w for w in _overview()["addons"]["wallets"] if w["wallet_id"] == wallet_id)["caps"]


def test_the_caps_door_is_a_trusted_door(owner):
    assert caller_binding.is_trusted_door(DOOR)
    status, answer = _post(DOOR, {"credential": PIN}, headers=None)
    assert (status, answer["error"]) == (403, "wallet_caller_refused"), answer


def test_the_wallet_shows_the_caps_that_are_enforced(addons_wallet):
    caps = _shown(addons_wallet["wallet_id"])
    assert caps, "the Skills & plugins wallet shows no limits"
    for row in caps:
        stored = limits.load_limits(addons_wallet["wallet_id"], row["asset"])
        assert stored.per_tx_minor > 0 and stored.daily_minor >= stored.per_tx_minor
        assert row["per_payment"] and row["daily"] and row["per_service_daily"]


def test_lowering_needs_no_pin_and_raising_needs_the_owners_pin(addons_wallet):
    wid = addons_wallet["wallet_id"]
    first = _shown(wid)[0]
    asset, before = first["asset"], limits.load_limits(wid, first["asset"])
    lower = {"wallet_id": wid, "asset": asset, "per_payment": str(float(first["per_payment"]) / 2), "daily": first["daily"], "per_service_daily": first["per_service_daily"]}
    status, saved = _post(DOOR, lower)
    assert status == 200, saved
    after = limits.load_limits(wid, asset)
    assert after.per_tx_minor * 2 == before.per_tx_minor and after.daily_minor == before.daily_minor
    raise_back = {**lower, "per_payment": first["per_payment"]}
    status, asked = _post(DOOR, raise_back)
    assert status == 400 and asked["fault"]["context"]["reason"] == "credential_required", asked
    assert limits.load_limits(wid, asset) == after, "a raise without the PIN changed the caps"
    status, wrong = _post(DOOR, {**raise_back, "credential": "000000"})
    assert status != 200 and limits.load_limits(wid, asset) == after
    status, raised = _post(DOOR, {**raise_back, "credential": PIN})
    assert status == 200, raised
    assert limits.load_limits(wid, asset).per_tx_minor == before.per_tx_minor
    assert next(r for r in raised["caps"] if r["asset"] == asset)["per_payment"] == first["per_payment"]


def test_a_payment_or_a_service_bigger_than_the_day_is_refused_and_nothing_changes(addons_wallet):
    wid = addons_wallet["wallet_id"]
    first = _shown(wid)[0]
    before = limits.load_limits(wid, first["asset"])
    base = {"wallet_id": wid, "asset": first["asset"], "per_payment": first["per_payment"], "daily": first["daily"], "per_service_daily": first["per_service_daily"], "credential": PIN}
    for change in ({"per_payment": str(float(first["daily"]) * 2)}, {"per_service_daily": str(float(first["daily"]) * 2)}):
        status, answer = _post(DOOR, {**base, **change})
        assert status != 200 and "fit inside the day" in answer.get("message", ""), answer
    assert limits.load_limits(wid, first["asset"]) == before


def test_the_owners_own_wallet_is_not_reached_through_this_door(owner):
    status, answer = _post(DOOR, {"wallet_id": owner.wallet_id, "asset": "SOL", "per_payment": "100", "daily": "100", "per_service_daily": "100", "credential": PIN})
    assert status != 200, answer
