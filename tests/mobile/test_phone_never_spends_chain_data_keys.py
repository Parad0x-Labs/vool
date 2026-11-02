"""A turn from another device never spends the owner's chain-data keys.

Red-team R1 (0.7.7, 2026-10-30): every chain-data lookup reads one of the owner's saved Helius /
Alchemy / Solscan / Etherscan / Moralis keys (core.chain_data has no free source). The remote-turn fence
already keeps ``web.chain_lookup`` the owner's; this pins the lookup itself, so a lane that forgets the
fence still cannot read a key for a remote turn.
"""
from __future__ import annotations

import pytest

from core import chain_data
from core import request_trust as rt


@pytest.mark.parametrize("context", [
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
