"""Crypto Pilot, stage 4: typed transfer quotes, approval v3 and the legacy/pilot boundary.

API names follow delivery/STAGE4-QUOTE-APPROVAL-DESIGN.md plus one addition found while writing the corpus:
`wallet_recipient_refused` (policy, retry_after_change) for recipient rules (rent minimum for a new Solana
account, EIP-55 mixed-case, zero address, precompiles and system predeploys).

Assumed API (to be implemented):
    core.wallet.transfers.is_pilot_transfer(proposal) -> bool
    core.wallet.quotes.mint_quote(proposal_id, *, source_context=None) -> dict   # fields per the design
    core.wallet.quotes.get_quote(quote_id) -> dict | None                       # {"state", "digest", "fields"}
    core.wallet.quotes.digest_of(fields) -> str; core.wallet.quotes._now() (the quote clock)
    core.wallet.quotes.GROWTH_BLOCKS, BSC_MIN_PRIORITY_WEI, ARBITRUM_GAS_BUFFER_BPS, QUOTE_TTL_SECONDS
    core.wallet.lifecycle.PaymentLifecycle.approve_pilot_transfer(proposal_id, *, quote_id, quote_digest, approver)
    POST /api/wallet/quote {"proposal_id"} -> {"quote": {...}}   (trusted door)
"""
from __future__ import annotations

import json
import math
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import GAS_PRICE_ORACLE, ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_MAINNET = "eip155:8453"
ETHEREUM_MAINNET = "eip155:1"
BNB_MAINNET = "eip155:56"
ROBINHOOD_MAINNET = "eip155:4663"
PIN = "482913"


@pytest.fixture
def quote_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield tmp_path
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _ready_pilot_wallet(network: str) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"q-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _route(monkeypatch, network: str, url: str) -> None:
    monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({network: url}))


def _propose(wallet: dict, destination: str, amount_minor: int, asset: str):
    from core.wallet import proposals

    return proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=destination, amount_minor=amount_minor, asset=asset, origin=proposals.ORIGIN_USER)


# --- the legacy / pilot boundary --------------------------------------------------------------------------

def test_pilot_transfer_classification_covers_rows_assets_and_wallet_kinds(quote_home, monkeypatch):
    from core.wallet import custody, environment, proposals, transfers

    environment.set_active_environment("testnet")
    legacy_devnet = custody.create_watch_only_wallet(_sol_key(), network="solana-devnet")
    legacy_sepolia = custody.create_watch_only_wallet("0x" + "1" * 40, network="eip155:84532")
    pilot_devnet = _ready_pilot_wallet(SOLANA_DEVNET)
    cases_testnet = [
        (proposals.propose_transaction(wallet_id=legacy_devnet.wallet_id, destination=_sol_key(), amount_minor=1000, asset="SOL", origin="user"), False),
        (proposals.propose_transaction(wallet_id=legacy_sepolia.wallet_id, destination="0x" + "2" * 40, amount_minor=10, asset="ETH", origin="user"), True),
        (proposals.propose_transaction(wallet_id=legacy_sepolia.wallet_id, destination="0x" + "2" * 40, amount_minor=10, asset="USDC", origin="user"), False),
        (_propose(pilot_devnet, _sol_key(), 1000, "SOL"), True),
    ]
    environment.set_active_environment("mainnet")
    legacy_base = custody.create_watch_only_wallet("0x" + "3" * 40, network=BASE_MAINNET)
    cases_mainnet = [(proposals.propose_transaction(wallet_id=legacy_base.wallet_id, destination="0x" + "4" * 40, amount_minor=10, asset="ETH", origin="user"), True)]
    for proposal, expected in cases_testnet + cases_mainnet:
        assert transfers.is_pilot_transfer(proposal) is expected, (proposal.network, proposal.asset)


def test_a_pilot_wallet_that_has_not_finished_setup_cannot_propose(quote_home):
    from core.wallet import pilot_custody, proposals

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key="q-unfinished")
    with pytest.raises(WalletFault) as refused:
        proposals.propose_transaction(wallet_id=created["wallet_id"], destination=_sol_key(), amount_minor=1000, asset="SOL", origin="user")
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_setup_state_invalid", "setup_not_finished")
    assert proposals.list_proposals() == []


def test_the_sixth_open_pilot_proposal_on_one_row_is_refused(quote_home):
    from core.wallet import proposals

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    for index in range(5):
        _propose(wallet, _sol_key(), 1000 + index, "SOL")
    with pytest.raises(WalletFault) as refused:
        _propose(wallet, _sol_key(), 2000, "SOL")
    assert refused.value.context.get("reason") == "too_many_open_pilot_proposals"
    assert len(proposals.list_proposals()) == 5


# --- Solana quotes ---------------------------------------------------------------------------------------

def test_a_solana_quote_prices_the_exact_message_and_reads_the_confirmed_balance(quote_home, monkeypatch):
    from core.wallet import quotes

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    destination = _sol_key()
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        node.balances = {wallet["address"]: 2_000_000_000, destination: 1_000_000_000}
        node.slot = 777
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        proposal = _propose(wallet, destination, 100_000_000, "SOL")
        quote = quotes.mint_quote(proposal.proposal_id)
        methods = [call.get("method") for call in node.calls]
    assert methods.index("getGenesisHash") < methods.index("getBalance") and "getFeeForMessage" in methods
    fields = quote["fields"]
    assert (fields["network"], fields["environment"], fields["badge"], fields["value_note"]) == (SOLANA_MAINNET, "mainnet", "MAINNET", "Real funds")
    assert (fields["from_address"], fields["to_address"]) == (wallet["address"], destination)
    assert (fields["balance_minor"], fields["balance_ref"]) == (2_000_000_000, "slot:777")
    assert (fields["amount_minor"], fields["amount_human"], fields["gas_asset"]) == (100_000_000, "0.1", "SOL")
    assert fields["fee_estimate_minor"] == 5_000 and fields["fee_max_minor"] >= 5_000
    assert fields["max_total_minor"] == 100_000_000 + fields["fee_max_minor"]
    assert fields["estimated_after_minor"] == 2_000_000_000 - 100_000_000 - 5_000
    assert fields["expires_at"] > time.time()
    assert quotes.get_quote(quote["quote_id"])["state"] == "open"


def test_solana_rent_rules_refuse_an_underfunded_new_recipient_and_a_dust_remainder(quote_home, monkeypatch):
    from core.wallet import quotes

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    new_recipient = _sol_key()
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        node.balances = {wallet["address"]: 1_000_000_000, new_recipient: 0}
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        below_rent = _propose(wallet, new_recipient, node.rent_minimum - 1, "SOL")
        with pytest.raises(WalletFault) as recipient:
            quotes.mint_quote(below_rent.proposal_id)
        assert (recipient.value.code, recipient.value.context.get("reason")) == ("wallet_recipient_refused", "new_account_below_rent_minimum")
        dust = _propose(wallet, _sol_key(), 1_000_000_000 - 5_000 - 1, "SOL")
        with pytest.raises(WalletFault) as remainder:
            quotes.mint_quote(dust.proposal_id)
        assert (remainder.value.code, remainder.value.context.get("reason")) == ("wallet_insufficient_funds", "remainder_below_rent_minimum")


def test_insufficient_funds_including_the_fee_names_the_shortfall_and_unknown_data_is_not_zero(quote_home, monkeypatch):
    from core.wallet import quotes

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    destination = _sol_key()
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        node.balances = {wallet["address"]: 10_000_000, destination: 1_000_000_000}
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        too_much = _propose(wallet, destination, 10_000_000, "SOL")
        with pytest.raises(WalletFault) as short:
            quotes.mint_quote(too_much.proposal_id)
        assert short.value.code == "wallet_insufficient_funds" and int(short.value.context["shortfall_minor"]) > 0
        node.fee_for_message = None  # the node cannot price the message: unknown, never zero
        fine = _propose(wallet, destination, 1_000, "SOL")
        with pytest.raises(WalletFault) as unknown:
            quotes.mint_quote(fine.proposal_id)
        assert unknown.value.code == "wallet_quote_unavailable"


# --- EVM quotes per fee model ------------------------------------------------------------------------------

def _evm_quote(monkeypatch, network: str, chain: ScriptedEvmNativeChain, *, amount: int = 10**15, recipient: str = "0x" + "5" * 40):
    from core.wallet import quotes

    wallet = _ready_pilot_wallet(network)
    chain.fund(wallet["address"], 10**18)
    _route(monkeypatch, network, chain.url)
    proposal = _propose(wallet, recipient, amount, "BNB" if network == BNB_MAINNET else "ETH")
    return wallet, proposal, quotes.mint_quote(proposal.proposal_id)


def test_an_eip1559_quote_bounds_base_fee_growth_over_its_lifetime(quote_home, monkeypatch):
    from core.wallet import quotes

    with ScriptedEvmNativeChain(chain_id=1, fee_model="eip1559", base_fee=20_000_000_000, priority_fee=1_000_000_000) as chain:
        _wallet, _proposal, quote = _evm_quote(monkeypatch, ETHEREUM_MAINNET, chain)
    params = quote["fields"]["tx_params"]
    expected_max = math.ceil(20_000_000_000 * (1.125 ** quotes.GROWTH_BLOCKS)) + params["max_priority_fee_per_gas"]
    assert params["max_fee_per_gas"] == expected_max and params["gas_limit"] == 21_000
    assert quote["fields"]["fee_max_minor"] == 21_000 * expected_max


def test_a_bsc_quote_applies_the_priority_floor(quote_home, monkeypatch):
    from core.wallet import quotes

    with ScriptedEvmNativeChain(chain_id=56, fee_model="bsc", gas_price=10_000_000) as chain:  # below the floor
        _wallet, _proposal, quote = _evm_quote(monkeypatch, BNB_MAINNET, chain)
    assert quote["fields"]["tx_params"]["max_priority_fee_per_gas"] >= quotes.BSC_MIN_PRIORITY_WEI
    assert quote["fields"]["gas_asset"] == "BNB"


def test_an_op_stack_quote_prices_l1_from_the_unsigned_transaction_bytes(quote_home, monkeypatch):
    from eth_account.typed_transactions import DynamicFeeTransaction
    from eth_hash.auto import keccak

    with ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=1_000) as chain:
        wallet, _proposal, quote = _evm_quote(monkeypatch, BASE_MAINNET, chain)
        oracle_calls = [call for call in chain.calls if call.get("method") == "eth_call" and str(call["params"][0].get("to") or "").lower() == GAS_PRICE_ORACLE]
    parts = quote["fields"]["fee_parts"]
    assert parts["l1_estimate_minor"] == 40_000_000_000_000 and parts["operator_minor"] >= 0 and parts["l1_is_estimate"] is True
    data = bytes.fromhex(str(oracle_calls[0]["params"][0]["data"])[10:])
    length = int.from_bytes(data[32:64], "big")
    unsigned = data[64:64 + length]
    params = quote["fields"]["tx_params"]
    expected = DynamicFeeTransaction.from_dict({
        "type": 2, "chainId": 8453, "nonce": params["nonce"], "maxPriorityFeePerGas": params["max_priority_fee_per_gas"],
        "maxFeePerGas": params["max_fee_per_gas"], "gas": params["gas_limit"], "to": params["to"], "value": params["value"],
        "data": b"", "accessList": [],
    })
    assert keccak(unsigned) == expected.hash()
    assert wallet["address"]


def test_an_arbitrum_quote_always_buffers_gas(quote_home, monkeypatch):
    from core.wallet import quotes

    with ScriptedEvmNativeChain(chain_id=4663, fee_model="arbitrum", gas_price=10_000_000, estimate_gas=300_000) as chain:
        _wallet, _proposal, quote = _evm_quote(monkeypatch, ROBINHOOD_MAINNET, chain)
    assert quote["fields"]["tx_params"]["gas_limit"] == math.ceil(300_000 * (10_000 + quotes.ARBITRUM_GAS_BUFFER_BPS) / 10_000)


@pytest.mark.parametrize("recipient, reason", [
    # mixed case that is NOT the EIP-55 form (the canonical checksum is 0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed,
    # verified with eth_utils.is_checksum_address in the composed runtime on 2026-09-14)
    ("0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "bad_checksum"),
    ("0x" + "0" * 40, "zero_address"),
    ("0x" + "0" * 39 + "1", "precompile_or_system_address"),
])
def test_evm_recipients_that_cannot_receive_safely_are_refused(quote_home, monkeypatch, recipient, reason):
    with ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack") as chain, pytest.raises(WalletFault) as refused:
        _evm_quote(monkeypatch, BASE_MAINNET, chain, recipient=recipient)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_recipient_refused", reason)


def test_contract_and_delegated_recipients_carry_a_cue(quote_home, monkeypatch):
    contract = "0x" + "6" * 40
    delegated = "0x" + "7" * 40
    with ScriptedEvmNativeChain(chain_id=1, fee_model="eip1559", estimate_gas=45_000) as chain:
        chain.code[contract] = "0x6080604052"
        chain.code[delegated] = "0xef0100" + "8" * 40
        _w1, _p1, contract_quote = _evm_quote(monkeypatch, ETHEREUM_MAINNET, chain, recipient=contract)
        _w2, _p2, delegated_quote = _evm_quote(monkeypatch, ETHEREUM_MAINNET, chain, recipient=delegated)
    assert "contract_recipient" in contract_quote["fields"]["cues"] and contract_quote["fields"]["tx_params"]["gas_limit"] >= 45_000
    assert "eip7702_delegated_recipient" in delegated_quote["fields"]["cues"]


# --- digest, supersede, approval v3 ----------------------------------------------------------------------

def test_the_quote_digest_is_stable_and_an_environment_switch_supersedes_open_quotes(quote_home, monkeypatch):
    from core.wallet import environment, quotes

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        proposal = _propose(wallet, _sol_key(), 1_000_000, "SOL")
        first = quotes.mint_quote(proposal.proposal_id)
        assert quotes.digest_of(first["fields"]) == first["digest"]
        changed = dict(first["fields"], amount_minor=first["fields"]["amount_minor"] + 1)
        assert quotes.digest_of(changed) != first["digest"]
        refreshed = quotes.mint_quote(proposal.proposal_id)
        assert quotes.get_quote(first["quote_id"])["state"] == "superseded" and quotes.get_quote(refreshed["quote_id"])["state"] == "open"
    environment.set_active_environment("testnet")
    assert quotes.get_quote(refreshed["quote_id"])["state"] == "superseded"


def test_approval_v3_refuses_a_changed_binding_and_an_expired_quote_then_a_fresh_quote_sends_once(quote_home, monkeypatch):
    from core.wallet import approval, lifecycle, limits, proposals, quotes

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        node.real_signature = True
        node.status_keyed = True
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        proposal = _propose(wallet, _sol_key(), 1_000_000, "SOL")
        engine = lifecycle.default_lifecycle()
        assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        quote = quotes.mint_quote(proposal.proposal_id)
        with pytest.raises(WalletFault) as mismatch:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest="0" * 64, approver=approval.PinApprover(PIN))
        assert mismatch.value.code == "wallet_quote_mismatch"
        assert limits.reservation_state(proposal.proposal_id) == "" and quotes.get_quote(quote["quote_id"])["state"] == "open"
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        monkeypatch.setattr(quotes, "_now", lambda: time.time() + quotes.QUOTE_TTL_SECONDS + 1)
        with pytest.raises(WalletFault) as expired:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
        assert expired.value.code == "wallet_quote_expired"
        assert "sendTransaction" not in [call.get("method") for call in node.calls]
        # a fresh quote, the correct credential: the transfer lane claims, signs and sends exactly once
        monkeypatch.setattr(quotes, "_now", time.time)
        fresh = quotes.mint_quote(proposal.proposal_id)
        result = engine.approve_pilot_transfer(proposal.proposal_id, quote_id=fresh["quote_id"], quote_digest=fresh["digest"], approver=approval.PinApprover(PIN))
        assert result["transfer"]["state"] == "confirmed" and result["duplicate"] is False
        assert [call.get("method") for call in node.calls].count("sendTransaction") == 1
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_CONFIRMED


def test_the_legacy_approve_door_cannot_approve_a_pilot_transfer_with_a_correct_pin(quote_home, monkeypatch):
    from core.wallet import approval, lifecycle, proposals

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        proposal = _propose(wallet, _sol_key(), 1_000_000, "SOL")
        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        with pytest.raises(WalletFault) as refused:
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
        assert refused.value.context.get("reason") in {"pilot_lane_incomplete", "pilot_transfer_needs_quote_approval"}
        assert proposals.approval_refusals(proposal.proposal_id) == 0
        assert "sendTransaction" not in [call.get("method") for call in node.calls]


# --- stage-4 follow-ups: the doors, the attempt law, the stored-quote re-check, legacy native transfers -----------

SAME_ORIGIN = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}


@pytest.fixture
def quote_app(quote_home, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    monkeypatch.delenv("VOOL_ALLOWED_HOSTS", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api import wallet_api
    from core.web.api.runtime import RuntimeServices

    wallet_api.reset_caller_binding_for_tests()
    yield create_app(RuntimeServices(display_name="VOOL"))
    wallet_api.reset_caller_binding_for_tests()


def _door(app, path: str, body: dict) -> tuple[int, dict, dict]:
    from tests.asgi_harness import asgi_request

    status, headers, raw = asgi_request(app, method="POST", path=path, headers=SAME_ORIGIN, body=json.dumps(body).encode())
    return status, {str(key).lower(): value for key, value in headers.items()}, json.loads(raw or b"{}")


def _prepared_quote(monkeypatch, node, wallet: dict):
    from core.wallet import lifecycle, quotes

    _route(monkeypatch, SOLANA_MAINNET, node.url)
    proposal = _propose(wallet, _sol_key(), 1_000_000, "SOL")
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    return engine, proposal, quotes.mint_quote(proposal.proposal_id)


class _CountingApprover:
    def __init__(self, inner) -> None:
        self.inner, self.asked = inner, 0

    def approve(self, challenge):
        self.asked += 1
        return self.inner.approve(challenge)


def test_the_quote_door_answers_no_store_and_the_approve_door_routes_a_quote_to_approval_v3_which_sends_once(quote_app, monkeypatch):
    from core.wallet import lifecycle, proposals

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        node.real_signature = True
        node.status_keyed = True
        _route(monkeypatch, SOLANA_MAINNET, node.url)
        proposal = _propose(wallet, _sol_key(), 1_000_000, "SOL")
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        status, headers, answer = _door(quote_app, "/api/wallet/quote", {"proposal_id": proposal.proposal_id})
        assert status == 200 and "no-store" in str(headers.get("cache-control") or ""), (status, headers, answer)
        quote = answer["quote"]
        assert (quote["fields"]["network"], quote["fields"]["badge"]) == (SOLANA_MAINNET, "MAINNET")
        base = {"proposal_id": proposal.proposal_id, "quote_id": quote["quote_id"], "pin": PIN}
        status, _headers, mismatch = _door(quote_app, "/api/wallet/approve", {**base, "quote_digest": "0" * 64})
        assert (status, mismatch.get("error")) == (409, "wallet_quote_mismatch"), mismatch
        assert "sendTransaction" not in [call.get("method") for call in node.calls]
        status, _headers, sent = _door(quote_app, "/api/wallet/approve", {**base, "quote_digest": quote["digest"]})
        assert status == 200 and sent["transfer"]["state"] == "confirmed", sent
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_CONFIRMED
        assert [call.get("method") for call in node.calls].count("sendTransaction") == 1


def test_a_wrong_credential_or_a_forged_decision_on_the_sheet_is_a_counted_attempt(quote_home, monkeypatch):
    from core.wallet import approval, proposals

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        engine, proposal, quote = _prepared_quote(monkeypatch, node, wallet)
        with pytest.raises(WalletFault) as wrong:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover("593027"))
        assert wrong.value.code == "wallet_pin_invalid"
        assert proposals.approval_refusals(proposal.proposal_id) == 1

        class _Forged:
            def approve(self, challenge):
                return approval.ApprovalDecision(approved=True, method=approval.METHOD_PIN, challenge_digest="f" * 64, pin_unlock=PIN)

        with pytest.raises(WalletFault) as forged:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=_Forged())
        assert forged.value.code == "wallet_approval_rejected"
        assert proposals.approval_refusals(proposal.proposal_id) == 2
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert "sendTransaction" not in [call.get("method") for call in node.calls]


def test_a_stored_quote_rewritten_for_another_recipient_does_not_approve_with_its_own_digest(quote_home, monkeypatch):
    from core.wallet import approval, proposals, quotes
    from core.wallet.store import connection

    wallet = _ready_pilot_wallet(SOLANA_MAINNET)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        engine, proposal, quote = _prepared_quote(monkeypatch, node, wallet)
        forged = dict(quote["fields"], to_address=_sol_key())
        forged_digest = quotes.digest_of(forged)
        with connection() as conn:
            conn.execute("UPDATE wallet_quotes SET fields_json = ?, digest = ? WHERE quote_id = ?", (json.dumps(forged, sort_keys=True), forged_digest, quote["quote_id"]))
        with pytest.raises(WalletFault) as refused:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest=forged_digest, approver=approval.PinApprover(PIN))
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_quote_mismatch", "binding_changed_since_the_quote")
        assert proposals.approval_refusals(proposal.proposal_id) == 0


def test_a_native_transfer_from_a_legacy_wallet_on_an_evm_test_row_is_refused_before_anyone_is_asked(quote_home):
    """No prepare path reaches pending_approval for a native EVM transfer (the EVM simulation admits EIP-3009 tokens
    only), but a restored or rewound row can say pending_approval: the signing door itself refuses it, typed."""
    from core.wallet import approval, custody, environment, lifecycle, proposals

    environment.set_active_environment("testnet")
    legacy = custody.create_watch_only_wallet("0x" + "1" * 40, network="eip155:84532")
    proposal = proposals.propose_transaction(wallet_id=legacy.wallet_id, destination="0x" + "2" * 40, amount_minor=10, asset="ETH", origin="user")
    for state in (proposals.STATE_SIMULATED, proposals.STATE_LIMITS_CHECKED, proposals.STATE_PENDING_APPROVAL):
        assert proposals.transition(proposal.proposal_id, state) is not None
    approver = _CountingApprover(approval.PinApprover(PIN))
    with pytest.raises(WalletFault) as refused:
        lifecycle.default_lifecycle().approve_and_execute(proposal.proposal_id, approver=approver)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "pilot_transfer_needs_quote_approval")
    assert approver.asked == 0 and proposals.approval_refusals(proposal.proposal_id) == 0
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
