"""A plugin pays a real x402 request from the Skills & plugins wallet, inside its own ticked allowance only.

The same lane the assistant's paid fetch rides (tests/wallet/test_agent_pay_x402_sdk.py): a v2 402 offering USDC on
Base Sepolia -> ONE proposal bound to that request -> the allowance of THAT plugin decides -> ONE EIP-3009 authorization
signed by the Skills & plugins wallet's real sealed key -> the chain decides. What is new is whose allowance it is: the
plugin's own row, with its own count per period, on a wallet no other agent and none of the owner's wallets share.

SIMULATED CHAIN: the loopback node answers as Base Sepolia and runs USDC as the token runs it.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.wallet._rig_x402_sdk import BASE_SEPOLIA, PAID_BODY, ScriptedUsdcChain, ScriptedX402SdkResource

pytest.importorskip("x402", reason="the x402 SDK lane is the optional `x402` extra")

pytestmark = [pytest.mark.safety]

PIN = "482913"
AMOUNT = 250_000  # 0.25 USDC
FUNDED = 2_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


@pytest.fixture
def lane(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 4))
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with ScriptedUsdcChain(network=BASE_SEPOLIA) as chain:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: chain.url}))
        environment.set_active_environment("testnet")
        yield chain
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


@pytest.fixture
def addons(lane):
    """The owner's own funded wallet, the assistant's funded wallet, and the funded Skills & plugins wallet."""
    from core.wallet import addon_wallet, agent_wallets, pilot_custody

    created = pilot_custody.create_pilot_wallet(network=BASE_SEPOLIA, method="pin", credential=PIN, credential_confirmation=PIN, creation_key="owner-1")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    owner = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    made = {}
    for agent in (addon_wallet.ADDONS, "assistant"):
        wallet = agent_wallets.create_agent_wallet(agent, network=BASE_SEPOLIA, credential=PIN)
        shown = agent_wallets.reveal_backup(wallet["wallet_id"], credential=PIN)
        made[agent] = agent_wallets.acknowledge_backup(wallet["wallet_id"], ack_token=shown["ack_token"])
        lane.fund_token(made[agent]["address"], FUNDED)
    lane.fund_token(owner["address"], FUNDED)
    return {"owner": owner, "wallet": made[addon_wallet.ADDONS], "assistant": made["assistant"], "chain": lane}


class Site(ScriptedX402SdkResource):
    asked_at = ""

    def challenge(self):
        from x402.schemas import PaymentRequired

        return PaymentRequired.model_validate({"x402Version": 2, "error": "payment required", "accepts": [self.requirement()],
                                               "resource": {"url": self.asked_at or self.url, "description": "Forecast", "mimeType": "application/json"}})


def _declare(monkeypatch, resource) -> None:
    from core.wallet import addon_wallet

    listed = [{"kind": "plugin", "id": "weather-pro", "name": "Weather Pro", "services": [resource.url]},
              {"kind": "plugin", "id": "maps", "name": "Maps", "services": [resource.url]}]
    monkeypatch.setattr(addon_wallet, "_SOURCES", [lambda: listed])


def _allow(addons, resource, agent: str, **over) -> str:
    from core.wallet import allowances as alw

    terms = dict(agent_id=agent, wallet_id=addons["wallet"]["wallet_id"], network=BASE_SEPOLIA, asset="USDC", per_payment_minor=AMOUNT, window_seconds=alw.DAY,
                 window_minor=AMOUNT * 3, total_minor=AMOUNT * 4, max_payments=10, min_interval_seconds=0, expires_at=time.time() + alw.DAY,
                 services=(alw.ServiceRule(resource.url, AMOUNT * 4, PAYEE),), window_payments=1)
    terms.update(over)
    return alw.create_allowance(alw.AllowanceSpec(**terms), credential=PIN)


def _fetch(agent: str, resource, url: str, context: dict):
    from core.wallet import addon_wallet

    resource.asked_at = url
    return addon_wallet.fetch_for_addon(agent, url, source_context=context)


def test_a_ticked_plugin_pays_from_the_skills_wallet_inside_its_own_count_and_nothing_else_moves(addons, monkeypatch):
    from core.request_trust import OWNER_LOCAL_KEY, TURN_AUTHOR_KEY, in_process_owner_source_context
    from core.wallet import addon_wallet
    from core.wallet import allowances as alw

    chain = addons["chain"]
    weather, maps = addon_wallet.addon_agent_id("plugin", "weather-pro"), addon_wallet.addon_agent_id("plugin", "maps")
    owner_turn = {"session_id": "chat-1", **in_process_owner_source_context()}
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _declare(monkeypatch, resource)
        allowance_id = _allow(addons, resource, weather)

        for index, not_the_owner in enumerate(({"session_id": "phone", OWNER_LOCAL_KEY: False}, {**owner_turn, TURN_AUTHOR_KEY: "agent"})):
            refused = _fetch(weather, resource, resource.url + f"?r={index}", not_the_owner)
            assert (refused["status"], refused["reason"]) == ("not_paid", "not_the_owners_turn")
        assert _fetch(maps, resource, resource.url + "?m=1", owner_turn)["reason"] == "not_allowed", "a plugin nobody ticked pays nothing"
        assert resource.paid_requests == []

        paid = _fetch(weather, resource, resource.url + "?ok=1", owner_turn)
        assert paid["status"] == "delivered" and paid["body"] == PAID_BODY and paid["payment"]["paid"] is True, paid
        assert len(resource.paid_requests) == 1
        assert chain.token_balance(addons["wallet"]["address"]) == FUNDED - AMOUNT and chain.token_balance(PAYEE) == AMOUNT
        for other in ("owner", "assistant"):
            assert chain.token_balance(addons[other]["address"]) == FUNDED, f"the {other}'s wallet never pays for a plugin"
        view = alw.allowance_view(allowance_id)
        assert (view["total_used_minor"], view["window_payments_used"]) == (AMOUNT, 1)

        # one payment per day was the owner's limit: the second waits for the owner and moves nothing
        second = _fetch(weather, resource, resource.url + "?ok=2", owner_turn)
        assert second["status"] == "not_paid" and second["payment"]["decision"] == "ask_owner", second
        assert second["payment"]["reason"] == alw.REASON_WINDOW_COUNT_USED
        assert len(resource.paid_requests) == 1 and chain.token_balance(PAYEE) == AMOUNT

        # unticking ends it at once, with no credential
        alw.revoke_allowance(allowance_id)
        assert _fetch(weather, resource, resource.url + "?ok=3", owner_turn)["reason"] == "not_allowed"
        assert len(resource.paid_requests) == 1
