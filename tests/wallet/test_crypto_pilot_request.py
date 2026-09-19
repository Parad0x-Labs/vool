"""Crypto Pilot, stage 5: the caller path from a request to one account on one row (design v5 §7).

A chat or API request names the recipient, the asset, a decimal amount and, when the caller knows it, the chain or the
account. The wallet resolves that to exactly one signing account on one row of the active environment, or refuses
typed: several fits name the candidates, none says what is missing. Amounts are exact decimals; the caller never
converts units. Nothing here signs or sends: the result is a proposal waiting for the owner's approval.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
BASE_MAINNET = "eip155:8453"
PIN = "482913"
EVM_TO = "0x" + "5" * 40


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256", "VOOL_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield monkeypatch
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


@pytest.fixture
def nodes(home):
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as sol, ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack") as base, ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack") as base_main:
        home.setenv("VOOL_WALLET_TESTNET_RPC_URL", sol.url)
        home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: base.url, BASE_MAINNET: base_main.url}))
        yield {"sol": sol, "base": base, "base_main": base_main}


def _env(name: str) -> None:
    from core.wallet import environment

    assert environment.set_active_environment(name).environment == name


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _pilot(network: str, label: str) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"x-{uuid.uuid4().hex}", label=label)
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _propose(args: dict):
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("wallet.propose", dict(args), source_context={"session_id": "request-proof"})


def _count() -> int:
    from core.wallet.store import connection

    with connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM wallet_proposals").fetchone()[0])


def _proposal(result):
    from core.wallet import proposals

    return proposals.get_proposal(result.details["proposal"]["proposal_id"])


def test_a_chain_name_and_a_decimal_amount_pick_the_pilot_account_on_that_row_and_the_amount_is_exact(nodes):
    from core.wallet import proposals

    _env("testnet")
    sol = _pilot(SOLANA_DEVNET, "sol pocket")
    base = _pilot(BASE_SEPOLIA, "base pocket")
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert result is not None and result.handled and result.ok, (result and result.status, result and result.response_text)
    proposal = _proposal(result)
    assert (proposal.wallet_id, proposal.network, proposal.amount_minor, proposal.asset, proposal.origin, proposal.state) == (
        base["wallet_id"], BASE_SEPOLIA, 10**15, "ETH", proposals.ORIGIN_MODEL, proposals.STATE_PENDING_APPROVAL)
    assert "Nothing has been signed" in result.response_text and "pending_approval" in result.response_text
    # the asset symbol and the recipient's shape are enough when exactly one account fits
    result = _propose({"destination": _sol_key(), "amount": "0.0005", "asset": "SOL"})
    assert result.ok, (result.status, result.response_text)
    proposal = _proposal(result)
    assert (proposal.wallet_id, proposal.network, proposal.amount_minor) == (sol["wallet_id"], SOLANA_DEVNET, 500_000)
    # an atomic amount still works, a chain alias too, and a decimal that agrees with it is accepted
    result = _propose({"destination": EVM_TO, "amount_minor": 12345, "asset": "ETH", "chain": "Base"})
    assert result.ok and _proposal(result).amount_minor == 12345
    result = _propose({"destination": EVM_TO, "amount": "0.000000000000012345", "amount_minor": "12345", "asset": "ETH", "chain": "base"})
    assert result.ok and _proposal(result).amount_minor == 12345


def test_two_accounts_that_fit_are_settled_by_the_default_account_or_refused_naming_them(nodes):
    _env("testnet")
    first = _pilot(BASE_SEPOLIA, "spending")
    second = _pilot(BASE_SEPOLIA, "savings")
    # the owner's default account (the latest created, here) is one of the fits: it settles the request
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert result.ok and _proposal(result).wallet_id == second["wallet_id"], (result.status, result.response_text)
    # the default moves to another row: the two Base accounts are an ambiguity the request must settle itself
    _pilot(SOLANA_DEVNET, "sol pocket")
    before = _count()
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert result.handled and not result.ok and result.status == "wallet_request_ambiguous", (result.status, result.response_text)
    fault = result.details["fault"]
    assert fault["context"]["reason"] == "several_accounts_fit"
    assert {c["wallet_id"]: c["label"] for c in fault["context"]["candidates"]} == {first["wallet_id"]: "spending", second["wallet_id"]: "savings"}
    assert "Nothing was signed and nothing was sent." in result.response_text
    assert _count() == before, "an ambiguous request writes nothing"
    # the same request with the account named
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "wallet_id": second["wallet_id"]})
    assert result.ok and _proposal(result).wallet_id == second["wallet_id"]
    # an account id that is not one of the fits is nothing, not the other account
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "wallet_id": "wallet-nope"})
    assert not result.ok and result.status == "wallet_not_found"


def test_an_account_outside_the_active_environment_is_never_substituted(nodes):
    _env("mainnet")
    main = _pilot(BASE_MAINNET, "main base")
    _env("testnet")
    before = _count()
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert not result.ok and result.status == "wallet_not_found", (result.status, result.response_text)
    context = result.details["fault"]["context"]
    assert (context["reason"], context["active_environment"], context["chain"]) == ("no_signing_account_for_request", "testnet", "base")
    assert _count() == before
    # naming the mainnet account by id does not reach across environments either
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "wallet_id": main["wallet_id"]})
    assert not result.ok and result.status == "wallet_not_found"
    _env("mainnet")
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert result.ok, (result.status, result.response_text)
    assert (_proposal(result).wallet_id, _proposal(result).network) == (main["wallet_id"], BASE_MAINNET)


@pytest.mark.parametrize(("amount_args", "code", "reason"), [
    ({"amount": "0.0000000000000000001"}, "wallet_amount_invalid", "amount_precision_exceeds_asset"),
    ({"amount": "1e3"}, "wallet_amount_invalid", "amount_not_a_plain_decimal"),
    ({"amount": "0"}, "wallet_amount_invalid", "amount_must_be_positive"),
    ({"amount": "9300000000"}, "wallet_amount_invalid", "amount_exceeds_pilot_storage_ceiling"),
    ({"amount": 0.1234567890123456789}, "wallet_amount_invalid", "amount_float_precision_ambiguous"),  # a binary float with more digits than it can carry
    ({"amount": "0.001", "amount_minor": 999}, "wallet_limit_exceeded", "amount_and_amount_minor_disagree"),
    ({"amount_minor": "12.5"}, "wallet_limit_exceeded", "amount_minor_not_an_integer"),
    ({}, "wallet_limit_exceeded", "amount_missing"),
])
def test_an_amount_that_is_not_an_exact_positive_decimal_within_storage_is_refused_before_any_write(nodes, amount_args, code, reason):
    _env("testnet")
    _pilot(BASE_SEPOLIA, "base pocket")
    before = _count()
    result = _propose({"destination": EVM_TO, "asset": "ETH", "chain": "base", **amount_args})
    assert result.handled and not result.ok and result.status == code, (result.status, result.response_text)
    assert result.details["fault"]["context"]["reason"] == reason, result.details["fault"]
    assert _count() == before


def test_a_recipient_whose_shape_does_not_match_the_row_or_an_unknown_chain_finds_no_account(nodes):
    _env("testnet")
    _pilot(SOLANA_DEVNET, "sol pocket")
    _pilot(BASE_SEPOLIA, "base pocket")
    before = _count()
    for args in (
        {"destination": EVM_TO, "amount": "0.0005", "asset": "SOL", "chain": "solana"},  # an EVM address on the Solana row
        {"destination": _sol_key(), "amount": "0.001", "asset": "ETH"},  # a Solana key with an EVM coin
        {"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "polygon"},  # a chain the pilot does not carry
        {"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "network": "eip155:11155111"},  # a declared row without an account
        {"destination": EVM_TO, "amount": "0.001", "asset": "USDC", "chain": "base"},  # a token, not the row's native coin
    ):
        result = _propose(args)
        assert not result.ok and result.status == "wallet_not_found", (args, result.status, result.response_text)
        assert result.details["fault"]["context"]["reason"] == "no_signing_account_for_request", args
    assert _count() == before


def test_accounts_that_cannot_sign_are_never_candidates(nodes):
    from core.wallet import custody, pilot_custody

    _env("testnet")
    custody.create_watch_only_wallet("0x" + "a" * 40, network=BASE_SEPOLIA, label="watched")
    created = pilot_custody.create_pilot_wallet(network=BASE_SEPOLIA, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"x-{uuid.uuid4().hex}", label="unfinished")
    before = _count()
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert not result.ok and result.status == "wallet_not_found", (result.status, result.response_text)
    assert _count() == before
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    result = _propose({"destination": EVM_TO, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert result.ok and _proposal(result).wallet_id == created["wallet_id"]


@pytest.mark.parametrize("text", [
    "Send 0.0005 SOL on solana to 2fgqQMyTitjLceky7ZTFwQDKCFjitKAc5A4EpVpk54FQ for the devnet proof",
    "send 0.001 ETH on base to 0x5555555555555555555555555555555555555555",
    "transfer 2.5 BNB to 0x5555555555555555555555555555555555555555 on bnb",
    "Please pay 500000 lamports to 2fgqQMyTitjLceky7ZTFwQDKCFjitKAc5A4EpVpk54FQ for the September invoice",
])
def test_a_request_with_a_decimal_amount_of_a_pilot_coin_is_a_wallet_demand_the_runtime_offers_the_tools_for(text):
    """The served turn offers the wallet tools only when the words carry a wallet demand; a decimal amount is not a
    sentence boundary and every pilot row's native coin counts (the model still makes the tool call)."""
    from core.tool_demand_signals import resolve_demand_signals

    signals = resolve_demand_signals(text)
    assert signals.explicit_intents == ("wallet.propose",) and signals.required_families == ("wallet",)


@pytest.mark.parametrize("text", [
    "send me the report on solar power to review",
    "Sol invited me. Send the notes to him",
    "transfer the 2.5 GB archive to the other laptop",
])
def test_words_without_a_pilot_coin_are_not_a_wallet_demand(text):
    from core.tool_demand_signals import resolve_demand_signals

    assert "wallet.propose" not in resolve_demand_signals(text).explicit_intents


def test_a_legacy_external_signer_account_still_proposes_with_a_decimal_amount_and_no_hints(nodes):
    from core.wallet import custody, transfers

    _env("testnet")
    profile = custody.register_external_signer_wallet(_sol_key(), network=SOLANA_DEVNET, label="phantom")
    result = _propose({"destination": _sol_key(), "amount": "0.0005", "asset": "SOL"})
    assert result.ok, (result.status, result.response_text)
    proposal = _proposal(result)
    assert (proposal.wallet_id, proposal.amount_minor, proposal.network) == (profile.wallet_id, 500_000, SOLANA_DEVNET)
    assert transfers.is_pilot_transfer(proposal) is False
