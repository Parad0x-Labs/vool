"""Agent spending in Settings: the owner's doors over an agent's wallet and its allowance, as the page drives them."""
from __future__ import annotations

import json

import pytest

from core.wallet import agent_settings, agent_wallets, caller_binding, custody
from core.wallet import allowances as alw
from core.web.api.wallet_api import handle_wallet_get, handle_wallet_post
from tests.wallet._rig import DEVNET, wallet_env

PIN = "730461"
HEADERS = {"host": "127.0.0.1:8731", "origin": "http://127.0.0.1:8731", "content-type": "application/json"}
DOORS = ("/api/wallet/agents/wallet", "/api/wallet/agents/backup", "/api/wallet/agents/allowance", "/api/wallet/agents/allowance/state")
TERMS = {"per_payment": "0.05", "period": "day", "period_amount": "1", "total": "5", "max_payments": "40", "days": "30",
         "services": [{"address": "api.example.com", "limit": "2"}]}


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
def network(owner) -> str:
    rows = _overview()["networks"]
    assert rows, "the active network environment offers no row an agent's wallet can be created on"
    return rows[0]["network"]


def _ready_wallet(network: str) -> dict:
    status, made = _post("/api/wallet/agents/wallet", {"network": network, "credential": PIN})
    assert status == 200, made
    wallet = agent_wallets.acknowledge_backup(made["wallet"]["wallet_id"], ack_token=made["backup"]["ack_token"])
    assert wallet["ready"] is True
    return wallet


def _coin(network: str) -> str:
    return next(row for row in _overview()["networks"] if row["network"] == network)["coins"][0]["symbol"]


def test_every_agent_door_is_a_trusted_door_and_refuses_a_caller_without_a_request_behind_it(owner):
    for door in DOORS:
        assert caller_binding.is_trusted_door(door)
        status, answer = _post(door, {"credential": PIN}, headers=None)
        assert (status, answer["error"]) == (403, "wallet_caller_refused"), (door, answer)
    assert agent_wallets.list_agent_wallets() == []


def test_with_no_wallet_of_their_own_the_owner_is_told_to_create_one_and_nothing_is_made(wallet_env):
    view = _overview()
    assert view["owner_wallet"] is False and view["agents"][0]["wallets"] == []
    status, answer = _post("/api/wallet/agents/wallet", {"network": view["networks"][0]["network"], "credential": PIN})
    assert status == 409 and "Create your own wallet first" in answer["message"] and answer["message"].endswith("Nothing was changed.")
    assert agent_wallets.list_agent_wallets() == []


def test_creating_the_wallet_needs_the_owners_pin_and_shows_the_backup_once(network):
    status, asked = _post("/api/wallet/agents/wallet", {"network": network})
    assert status == 400 and asked["fault"]["context"]["reason"] == "credential_required" and asked["fault"]["context"]["wallet_label"]
    status, wrong = _post("/api/wallet/agents/wallet", {"network": network, "credential": "000000"})
    assert status == 400 and wrong["error"] == "wallet_pin_invalid"
    assert agent_wallets.list_agent_wallets() == []
    status, made = _post("/api/wallet/agents/wallet", {"network": network, "credential": PIN})
    assert status == 200 and made["wallet"]["agent_id"] == "assistant" and made["wallet"]["ready"] is False
    assert made["backup"]["backup_value"] and made["backup"]["ack_token"]
    # the reveal is once: asking again, even as the owner, shows nothing
    status, again = _post("/api/wallet/agents/backup", {"wallet_id": made["wallet"]["wallet_id"], "credential": PIN})
    assert status != 200 and "backup_value" not in json.dumps(again)
    status, twice = _post("/api/wallet/agents/wallet", {"network": network, "credential": PIN})
    assert status == 409 and "already has a wallet" in twice["message"]
    shown = _overview()["agents"][0]["wallets"]
    assert [row["wallet_id"] for row in shown] == [made["wallet"]["wallet_id"]] and made["backup"]["backup_value"] not in json.dumps(_overview())
    # closed without confirming: the owner confirms later with their own PIN, and only then can the wallet pay
    status, asked = _post("/api/wallet/agents/backup", {"wallet_id": made["wallet"]["wallet_id"], "confirm_saved": True})
    assert status == 400 and asked["fault"]["context"]["reason"] == "credential_required" and shown[0]["ready"] is False
    status, done = _post("/api/wallet/agents/backup", {"wallet_id": made["wallet"]["wallet_id"], "confirm_saved": True, "credential": PIN})
    assert status == 200 and done["wallet"]["ready"] is True and done["backup"] is None


def test_an_unlisted_agent_or_network_creates_nothing(owner):
    for body in ({"agent_id": "someone-else", "network": DEVNET}, {"network": "eip155:999999"}, {}):
        status, answer = _post("/api/wallet/agents/wallet", {**body, "credential": PIN})
        assert status == 409 and answer["message"].endswith("Nothing was changed."), answer
    assert agent_wallets.list_agent_wallets() == []


def test_the_agents_wallet_is_never_the_owners_default_and_is_named_as_the_agents(network, owner):
    wallet = _ready_wallet(network)
    assert custody.default_wallet().wallet_id == owner.wallet_id
    listed = {row["wallet_id"]: row for row in custody.list_wallets()}
    assert listed[wallet["wallet_id"]]["agent_id"] == "assistant" and listed[owner.wallet_id]["agent_id"] == ""
    # what the owner is asked for on the agent's wallet is their own wallet's PIN, never a password nobody knows
    assert listed[wallet["wallet_id"]]["approval_method"] == custody.APPROVAL_PIN == custody.shown_approval_method(wallet["wallet_id"])


def test_setting_an_allowance_takes_typed_amounts_and_needs_the_pin(network):
    wallet = _ready_wallet(network)
    body = {**TERMS, "wallet_id": wallet["wallet_id"], "asset": _coin(network)}
    status, asked = _post("/api/wallet/agents/allowance", body)
    assert status == 400 and asked["fault"]["context"]["reason"] == "credential_required"
    assert alw.list_allowances(agent_id="assistant") == []
    status, saved = _post("/api/wallet/agents/allowance", {**body, "credential": PIN})
    assert status == 200, saved
    shown = saved["allowance"]
    assert (shown["per_payment"], shown["period"], shown["period_amount"], shown["total"], shown["max_payments"], shown["days_left"], shown["state"]) == ("0.05", "day", "1", "5", 40, 30, "active")
    assert shown["services"] == [{"address": "https://api.example.com", "limit": "2", "used": "0", "pay_to": "", "pinned": False}]
    assert saved["agent_spending"]["agents"][0]["allowances"][0]["allowance_id"] == shown["allowance_id"]
    status, twice = _post("/api/wallet/agents/allowance", {**body, "credential": PIN})
    assert status == 403 and "already has an allowance" in twice["message"]


@pytest.mark.parametrize(("change", "said"), [
    ({"per_payment": "2"}, "One payment has to fit inside the period amount"),
    ({"per_payment": ""}, "Type an amount for one payment"),
    ({"total": "abc"}, "could not be read"),
    ({"period": "year"}, "Choose an hour, a day, a week or a month"),
    ({"days": "400"}, "between 1 and 90"),
    ({"services": []}, "Name at least one service"),
    ({"services": [{"address": "http://plain.example.com", "limit": "1"}]}, "has to start with https://"),
    ({"services": [{"address": "api.example.com", "limit": "9"}]}, "has to fit inside the total"),
    ({"asset": "NOPE"}, "Choose one of the coins"),
])
def test_terms_that_do_not_fit_are_refused_in_plain_words_and_nothing_is_saved(network, change, said):
    wallet = _ready_wallet(network)
    status, answer = _post("/api/wallet/agents/allowance", {**TERMS, "wallet_id": wallet["wallet_id"], "asset": _coin(network), "credential": PIN, **change})
    assert status in (400, 403) and said in answer["message"] and answer["message"].endswith("Nothing was changed."), answer
    assert alw.list_allowances(agent_id="assistant") == []


def test_tightening_pausing_and_ending_need_no_pin_but_loosening_and_resuming_do(network):
    wallet = _ready_wallet(network)
    status, saved = _post("/api/wallet/agents/allowance", {**TERMS, "wallet_id": wallet["wallet_id"], "asset": _coin(network), "credential": PIN})
    allowance_id = saved["allowance"]["allowance_id"]
    keep = {**TERMS, "allowance_id": allowance_id, "days": ""}
    status, lower = _post("/api/wallet/agents/allowance", {**keep, "per_payment": "0.01"})
    assert status == 200 and lower["allowance"]["per_payment"] == "0.01" and lower["allowance"]["days_left"] == 30
    status, higher = _post("/api/wallet/agents/allowance", {**keep, "per_payment": "0.5"})
    assert status == 400 and higher["fault"]["context"]["reason"] == "credential_required"
    assert _overview()["agents"][0]["allowances"][0]["per_payment"] == "0.01"
    status, higher = _post("/api/wallet/agents/allowance", {**keep, "per_payment": "0.5", "credential": PIN})
    assert status == 200 and higher["allowance"]["per_payment"] == "0.5"
    status, paused = _post("/api/wallet/agents/allowance/state", {"allowance_id": allowance_id, "action": "pause"})
    assert status == 200 and paused["allowance"]["state"] == "paused" and paused["allowance"]["pause_reason"] == "owner"
    status, asked = _post("/api/wallet/agents/allowance/state", {"allowance_id": allowance_id, "action": "resume"})
    assert status == 400 and asked["fault"]["context"]["reason"] == "credential_required"
    assert alw.allowance_view(allowance_id)["state"] == "paused"
    status, resumed = _post("/api/wallet/agents/allowance/state", {"allowance_id": allowance_id, "action": "resume", "credential": PIN})
    assert status == 200 and resumed["allowance"]["state"] == "active"
    status, ended = _post("/api/wallet/agents/allowance/state", {"allowance_id": allowance_id, "action": "end"})
    assert status == 200 and ended["allowance"]["state"] == "revoked"
    assert ended["agent_spending"]["agents"][0]["allowances"] == []
    status, gone = _post("/api/wallet/agents/allowance/state", {"allowance_id": "no-such", "action": "pause"})
    assert status == 409 and "no longer exists" in gone["message"]


def test_the_overview_names_refusals_in_plain_words(network):
    assert agent_settings.refusal_text("invalid_allowance", "anything else").startswith("Those limits cannot be saved")
    assert agent_settings.refusal_text("credential_required") == ""
