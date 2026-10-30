"""A phone turn never spends the owner's chain-data keys, whatever rights the phone holds.

Red-team R1 (0.7.7 train, 2026-10-30): ``web.chain_lookup`` sat in the "paid services" class, so a phone
the owner gave "paid services" could run it, and every chain-data lookup reads one of the owner's saved
Helius / Alchemy / Solscan / Etherscan / Moralis keys (core.chain_data has no free source). The class
comment and the fence both said chain-data keys stay the owner's; this pins the fence and the lookup itself.
"""
from __future__ import annotations

import time

import pytest

from core import chain_data
from core import request_trust as rt
from tests.mobile.test_phone_full_vool_rights import _turn
from tests.mobile.test_phone_full_vool_rights import phone as phone  # noqa: F401  (fixture)


@pytest.mark.parametrize("rules", [
    {"paid_services": True},
    {"paid_services": True, "computer_tools": True},
])
def test_no_phone_right_opens_the_chain_lookup(phone, rules):
    phone["rules"] = dict(rules)
    phone["right_expiry"] = {name: time.time() + 3600 for name in rules}
    assert rt.remote_turn_tool_refusal("web.chain_lookup", _turn()) == "owner_local_required"
    # the same right still opens the keyless web lookups it was made for
    assert rt.remote_turn_tool_refusal("web.search", _turn()) == ""


@pytest.mark.parametrize("context", [
    _turn(),                                                         # a paired phone
    {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False},          # a chat app or other device
    {rt.HTTP_INGRESS_KEY: True},                                     # came over HTTP with no owner stamp
])
def test_the_lookup_refuses_a_remote_turn_before_reading_a_key(monkeypatch, context):
    def never(*_args, **_kwargs):
        raise AssertionError("a remote turn reached the owner's chain-data key")

    monkeypatch.setattr(chain_data, "_read_key", never)
    monkeypatch.setattr(chain_data, "lookup", never)
    result = chain_data.run_tool(chain_data.INTENT, {"query": "balance", "address": "So11111111111111111111111111111111111111112"}, context)
    assert result.ok is False and result.status == "owner_local_required"
    assert result.response_text == rt.REMOTE_TURN_REFUSAL_TEXT


@pytest.mark.parametrize("context", [None, {}, {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: True}])
def test_the_owners_own_turn_still_looks_up(monkeypatch, context):
    calls = []
    monkeypatch.setattr(chain_data, "lookup", lambda args: calls.append(args) or {
        "text": "1 SOL", "provider": "helius", "chain": "solana", "rows": []})
    result = chain_data.run_tool(chain_data.INTENT, {"query": "balance"}, context)
    assert result.ok is True and calls == [{"query": "balance"}]
