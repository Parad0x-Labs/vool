"""The multichain tool surface: models propose (including x402), operators approve, and
every smuggled authority path fails typed with zero effects."""
from __future__ import annotations

import json

import pytest

BASE_SEPOLIA = "eip155:84532"


@pytest.fixture
def wallet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "20000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield
    store_module.reset_default_store()


def test_t1_x402_propose_contract_exists_and_is_proposal_only():
    from core.runtime_tool_contracts import runtime_tool_contracts

    by_intent = {contract.intent: contract for contract in runtime_tool_contracts()}
    x402 = by_intent.get("x402.propose")
    assert x402 is not None and x402.tool_surface == "wallet"
    assert x402.side_effect_class == "creative_state"  # a reversible proposal row, never a spend
    assert "spend_funds" not in x402.permission_actions
    # the forbidden authority names stay absent from the registry
    for forbidden in ("wallet.approve", "wallet.sign", "wallet.broadcast", "wallet.set_limits", "wallet.submit_signature", "wallet.create_pocket", "wallet.reveal"):
        assert forbidden not in by_intent, forbidden


def test_t2_model_x402_propose_parks_a_v2_offer_without_paying(wallet_env):
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import custody, proposals

    profile = custody.register_external_signer_wallet("0x" + "a" * 40, network=BASE_SEPOLIA)
    body = {
        "x402Version": 2,
        "resource": {"url": "https://api.example.test/premium", "description": "d"},
        "accepts": [{"scheme": "exact", "network": BASE_SEPOLIA, "amount": "15000", "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e", "payTo": "0x" + "2" * 40, "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}}],
    }
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": body}, source_context={"session_id": "s1"})
    assert result.handled and result.ok and "parked" in result.response_text
    proposal = proposals.get_proposal(result.details["proposal"]["proposal_id"])
    assert proposal is not None and proposal.origin == "x402" and proposal.state == proposals.STATE_PROPOSED
    assert proposal.network == BASE_SEPOLIA and proposal.wallet_id == profile.wallet_id
    # the owner side (prepare) is what moves it to pending_approval; the model surface cannot


def test_t3_model_x402_propose_refuses_above_cap_and_mainnet_entries_typed(wallet_env):
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import custody

    custody.register_external_signer_wallet("0x" + "b" * 40, network=BASE_SEPOLIA)
    over_cap = {
        "x402Version": 2, "resource": {"url": "https://api.example.test/premium"},
        "accepts": [{"scheme": "exact", "network": BASE_SEPOLIA, "amount": "999999", "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e", "payTo": "0x" + "2" * 40, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}}],
    }
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": over_cap}, source_context={"session_id": "s2"})
    assert result.handled and not result.ok and result.status == "x402_scheme_unavailable"
    mainnet = {
        "x402Version": 2, "resource": {"url": "https://api.example.test/premium"},
        "accepts": [{"scheme": "exact", "network": "eip155:1", "amount": "10", "asset": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "payTo": "0x" + "2" * 40}],
    }
    result2 = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": mainnet}, source_context={"session_id": "s3"})
    assert result2.handled and not result2.ok and result2.status == "x402_scheme_unavailable"


def test_t4_smuggled_approval_arguments_die_at_the_schema(wallet_env):
    from core.runtime_execution_tools import _dispatch_runtime_tool

    result = _dispatch_runtime_tool(
        "wallet.propose",
        {"destination": "0x" + "2" * 40, "amount_minor": 1, "asset": "USDC", "approve": True, "pin": "246810", "_trusted_local_only": True},
        source_context={"session_id": "s4"},
    )
    assert result.handled and not result.ok and result.status == "invalid_arguments"


def test_t5_wallet_disabled_never_reaches_the_tools():
    import os

    saved = os.environ.pop("VOOL_WALLET_ENABLED", None)
    try:
        from core.runtime_tool_contracts import runtime_tool_contracts

        by_intent = {contract.intent: contract for contract in runtime_tool_contracts()}
        assert by_intent["wallet.status"].supported is False
        assert by_intent["x402.propose"].supported is False
    finally:
        if saved is not None:
            os.environ["VOOL_WALLET_ENABLED"] = saved


def test_t6_status_surface_lists_chain_qualified_accounts(wallet_env):
    from core.wallet import chains, custody, status

    custody.register_external_signer_wallet("0x" + "a" * 40, network=BASE_SEPOLIA, label="evm")
    st = status.wallet_status()
    accounts = st.get("accounts") or []
    assert any(a["chain"] == BASE_SEPOLIA and a["family"] == "evm" for a in accounts)
    assert st.get("declared_networks") == list(chains.DECLARED_NETWORKS) and len(st["declared_networks"]) == 10
    blob = json.dumps(st)
    for secret in ("recovery_phrase", "private_key", "sealed_blob", "pin"):
        assert secret not in blob
