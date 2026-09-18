"""Crypto Pilot, stage 5: native EVM transfers through the same authority, on the scripted EVM chain.

SIMULATED CHAIN: the loopback EVM node enforces what a node enforces on decoded bytes (chain id, nonce, funds, base
fee) and mines on the next receipt read. A ready pilot wallet on Base Mainnet or Ethereum Mainnet proposes a native
transfer, the quote prices it, the approval signs an EIP-1559 transaction once, sends it once, and the observer settles
it at the row's display tag with the fee the receipt reports. The negative cases pin: a reverted transaction counts
only its fee and only at finality; a receipt above the tag stays pending; a non-canonical receipt moves nothing; a
second transfer waits while one is live on the account; a dropped transaction offers a resend, never a release; a
refusal is labelled as a refusal and offers only a stop-waiting exit that keeps counting; a slot used by another
transaction is proven at the finalized tag before any exit is offered; the key never leaves its session.
"""
from __future__ import annotations

import json
import uuid

import pytest
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

BASE_MAINNET = "eip155:8453"
ETHEREUM_MAINNET = "eip155:1"
PIN = "482913"
WRONG_PIN = "593027"
AMOUNT = 10**15  # 0.001 ETH
RECIPIENT = "0x" + "5" * 40


@pytest.fixture
def pilot(monkeypatch, tmp_path):
    """Crypto on, Mainnet view (a fresh installation), loopback endpoints allowed, the EVM rows ready."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import capabilities, chains, lifecycle

    monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({BASE_MAINNET, ETHEREUM_MAINNET}))
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield monkeypatch
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _route(monkeypatch, network: str, url: str) -> None:
    monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({network: url}))


def _ready_pilot_wallet(network: str) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"e-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _pending(wallet: dict, network: str, amount: int = AMOUNT, recipient: str = RECIPIENT, asset: str = "ETH"):
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=recipient, amount_minor=amount, asset=asset, origin=proposals.ORIGIN_USER, network=network)
    engine = lifecycle.default_lifecycle()
    assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    return engine, proposal


def _quote(proposal_id: str) -> dict:
    from core.wallet import quotes

    return quotes.mint_quote(proposal_id)


def _approve(engine, proposal_id: str, quote: dict, pin: str = PIN) -> dict:
    from core.wallet import approval

    return engine.approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(pin))


def _hold(proposal_id: str):
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row else None


def _base(monkeypatch, **overrides):
    chain = ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=1_000, **overrides)
    chain.__enter__()
    _route(monkeypatch, BASE_MAINNET, chain.url)
    return chain


def _ready_on_base(monkeypatch, chain, *, funds: int = 10**18):
    wallet = _ready_pilot_wallet(BASE_MAINNET)
    chain.fund(wallet["address"], funds)
    engine, proposal = _pending(wallet, BASE_MAINNET)
    return wallet, engine, proposal


# --- the transfer ------------------------------------------------------------------------------------------------

def test_a_native_base_transfer_is_signed_once_sent_once_and_confirmed_at_the_safe_tag_with_the_receipt_fee(pilot):
    from core.wallet import proposals, transfers

    chain = _base(pilot)
    try:
        wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        params = quote["fields"]["tx_params"]
        result = _approve(engine, proposal.proposal_id, quote)
        transfer = result["transfer"]
        assert len(chain.sent) == 1 and len(chain.receipts) == 1
        tx_hash = next(iter(chain.receipts))
        assert transfer["tx_id"] == tx_hash and transfer["explorer_url"] == f"https://basescan.org/tx/{tx_hash}"
        assert (transfer["state"], transfer["state_label"], transfer["evidence_kind"], transfer["finality_seen"], transfer["badge"]) == ("confirmed", "Confirmed", "submitted", "safe", "MAINNET")
        receipt = chain.receipts[tx_hash]
        charged = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16) + chain.l1_fee + chain.operator_fee
        assert transfer["charged_fee_minor"] == str(charged) and int(transfer["charged_fee_minor"]) < int(quote["fields"]["fee_max_minor"])
        assert _hold(proposal.proposal_id) == (AMOUNT, charged, "settled")
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_CONFIRMED
        decoded = chain._decode("0x" + chain.sent[0].hex())
        assert (decoded["from"], decoded["to"], decoded["value"], decoded["nonce"], decoded["chainId"]) == (wallet["address"].lower(), RECIPIENT.lower(), AMOUNT, params["nonce"], 8453)
        assert (decoded["maxFeePerGas"], decoded["maxPriorityFeePerGas"], decoded["gas"]) == (params["max_fee_per_gas"], params["max_priority_fee_per_gas"], params["gas_limit"])
        row = transfers.get_transfer_by_id(proposal.proposal_id)
        assert row["nonce"] == params["nonce"] and row["raw_b64"] is None and row["block_hash"] == receipt["blockHash"]
        assert transfer["balance_after_minor"] == str(chain.balances[wallet["address"].lower()] if wallet["address"].lower() in chain.balances else chain.balances[wallet["address"]])
    finally:
        chain.__exit__(None, None, None)


def test_an_ethereum_transfer_waits_for_the_finalized_tag_and_a_receipt_above_it_stays_pending(pilot):
    from core.wallet import settlement, transfers

    chain = ScriptedEvmNativeChain(chain_id=1, fee_model="eip1559", base_fee=20_000_000_000, priority_fee=1_000_000_000)
    chain.__enter__()
    try:
        _route(pilot, ETHEREUM_MAINNET, chain.url)
        wallet = _ready_pilot_wallet(ETHEREUM_MAINNET)
        chain.fund(wallet["address"], 10**18)
        engine, proposal = _pending(wallet, ETHEREUM_MAINNET)
        chain.finalized_block = 50  # the finalized tag trails the inclusion block
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        assert (transfer["state"], transfer["state_label"], transfer["in_flight"]) == ("pending", "Pending", True)
        assert _hold(proposal.proposal_id)[2] == "reserved"
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["finality_seen"], view["explorer_url"]) == ("confirmed", "finalized", f"https://etherscan.io/tx/{view['tx_id']}")
        assert len(chain.sent) == 1
    finally:
        chain.__exit__(None, None, None)


def test_a_reverted_transfer_counts_only_its_fee_and_only_once_finalized(pilot):
    from core.wallet import proposals, settlement, transfers

    chain = _base(pilot)
    try:
        chain.mine_mode = "revert"
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        chain.finalized_block = 50
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        assert transfer["state"] == "pending" and _hold(proposal.proposal_id)[2] == "reserved", "a failure is settled only at finality"
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["state_label"]) == ("failed_on_chain", "Failed on chain")
        fee = int(view["charged_fee_minor"])
        assert _hold(proposal.proposal_id) == (0, fee, "settled") and fee > 0
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_FAILED
    finally:
        chain.__exit__(None, None, None)


def test_a_non_canonical_receipt_moves_nothing(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        chain.forked_blocks.add(chain.block_number + 1)  # the block the transaction will be included in answers another hash
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        assert transfer["state"] == "unknown" and _hold(proposal.proposal_id)[2] == "reserved"
        chain.forked_blocks.clear()
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["state"] == "confirmed"
    finally:
        chain.__exit__(None, None, None)


# --- one live transfer per account ------------------------------------------------------------------------------------

def test_a_second_transfer_waits_while_one_is_live_on_the_account_without_consuming_a_credential_attempt(pilot):
    from core.wallet import proposals, settlement, transfers

    chain = _base(pilot)
    try:
        chain.mine_mode = "never"
        wallet, engine, first = _ready_on_base(pilot, chain)
        transfer = _approve(engine, first.proposal_id, _quote(first.proposal_id))["transfer"]
        assert transfer["state"] in ("unknown", "pending") and transfer["in_flight"] is True
        engine2, second = _pending(wallet, BASE_MAINNET, amount=2 * 10**14)
        quote2 = _quote(second.proposal_id)
        with pytest.raises(WalletFault) as waiting:
            _approve(engine2, second.proposal_id, quote2, pin=WRONG_PIN)
        assert (waiting.value.code, waiting.value.context.get("reason"), waiting.value.context.get("previous_proposal_id")) == ("wallet_duplicate_payment", "previous_transfer_in_flight", first.proposal_id)
        assert proposals.approval_refusals(second.proposal_id) == 0 and len(chain.sent) == 1
        chain.mine_mode = "auto"
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(first.proposal_id)["state"] == "confirmed"
        quote2 = _quote(second.proposal_id)  # the nonce moved: a fresh preview
        assert _approve(engine2, second.proposal_id, quote2)["transfer"]["state"] == "confirmed" and len(chain.sent) == 2
    finally:
        chain.__exit__(None, None, None)


# --- dropped, refused and used slots ----------------------------------------------------------------------------------

def test_a_dropped_transaction_offers_a_resend_and_never_a_release(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        chain.mine_mode = "never"
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        assert transfer["state"] == "unknown" and transfer["offered_exit"] == ""
        chain.pending.clear()  # the node dropped it from its pool: pending == latest == nonce, no receipt
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["offered_exit"], view["in_flight"]) == ("unknown", "resend", True)
        assert _hold(proposal.proposal_id)[2] == "reserved" and len(chain.sent) == 1
    finally:
        chain.__exit__(None, None, None)


def test_a_refused_send_is_labelled_a_refusal_and_offers_only_a_stop_waiting_exit_that_keeps_counting(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        chain.txpool_refusal = "insufficient funds for gas * price + value"  # another app spent from the account after the preview
        transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
        assert (transfer["state"], transfer["evidence_kind"], transfer["state_label"]) == ("unknown", "refused", "Status unknown: the network refused this transaction")
        assert len(chain.sent) == 0 and _hold(proposal.proposal_id)[2] == "reserved"
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["offered_exit"], view["state"]) == ("stop_waiting_refused", "unknown")
        assert _hold(proposal.proposal_id)[2] == "reserved", "absence of a pooled transaction never releases the liability"
    finally:
        chain.__exit__(None, None, None)


def test_a_slot_used_by_another_transaction_is_proven_at_the_finalized_tag_before_an_exit_is_offered(pilot):
    from core.wallet import settlement, transfers

    chain = _base(pilot)
    try:
        chain.mine_mode = "never"
        wallet, engine, proposal = _ready_on_base(pilot, chain)
        transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
        nonce = transfers.get_transfer_by_id(proposal.proposal_id)["nonce"]
        chain.pending.clear()
        chain.nonces[wallet["address"].lower()] = nonce + 1  # another wallet app used the slot
        chain.block_number += 3
        chain.finalized_block = chain.block_number - 10
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["offered_exit"]) == ("unknown", ""), "a nonce ahead of ours is not proof until the crossing block is final"
        row = transfers.get_transfer_by_id(proposal.proposal_id)
        assert row["crossing_block"] == chain.block_number
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["offered_exit"]) == ("unknown", "stop_waiting_slot_used")
        assert _hold(proposal.proposal_id)[2] == "reserved" and len(chain.sent) == 1
        assert transfer["tx_id"]
    finally:
        chain.__exit__(None, None, None)


def test_a_signed_transfer_left_behind_is_released_only_once_its_slot_was_used_by_another_transaction(pilot):
    from core.wallet import controls, limits, proposals, settlement, transfers
    from core.wallet.store import connection

    chain = _base(pilot)
    try:
        wallet, _engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        params = quote["fields"]["tx_params"]
        now = settlement.clock()
        with connection() as conn:
            limits._begin_immediate(conn)
            transfers.insert_claim(
                conn, proposal=proposal, quote_id=quote["quote_id"], quote_digest=quote["digest"], challenge_digest="c", environment="mainnet", family="evm",
                from_address=wallet["address"], fee_max_minor=int(quote["fields"]["fee_max_minor"]), owner_token="dead-request", lease_until=now + 30,
                dispatch_deadline=now + 60, epochs=controls.epochs(conn), enabled_generation=0, now=now, nonce=int(params["nonce"]),
            )
            transfers.transition(conn, proposal.proposal_id, "signed", expected_state="claimed", now=now, tx_id="0x" + "ab" * 32, raw_b64="cmF3")
        pilot.setattr(settlement, "clock", lambda: now + 61)
        settlement.observe_open_transfers()
        assert transfers.latest_receipt(proposal.proposal_id)["state"] == "signed_revoked" and _hold(proposal.proposal_id)[2] == "reserved"
        chain.nonces[wallet["address"].lower()] = int(params["nonce"]) + 1
        settlement.observe_open_transfers()
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], proposals.get_proposal(proposal.proposal_id).state, _hold(proposal.proposal_id)[2]) == ("released", proposals.STATE_FAILED, "released")
        assert len(chain.sent) == 0
    finally:
        chain.__exit__(None, None, None)


# --- revalidation, legacy doors, the key -------------------------------------------------------------------------------

def test_a_moved_nonce_or_a_spent_balance_after_the_preview_expires_the_quote_before_any_claim(pilot):
    from core.wallet import quotes, transfers

    chain = _base(pilot)
    try:
        wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        chain.nonces[wallet["address"].lower()] = 7  # another app used the account meanwhile
        with pytest.raises(WalletFault) as moved:
            _approve(engine, proposal.proposal_id, quote)
        assert (moved.value.code, moved.value.context.get("reason")) == ("wallet_quote_expired", "nonce_changed")
        assert quotes.get_quote(quote["quote_id"])["state"] == "superseded" and transfers.get_transfer_by_id(proposal.proposal_id) is None
        chain.nonces[wallet["address"].lower()] = 0
        fresh = _quote(proposal.proposal_id)
        assert fresh["fields"]["tx_params"]["nonce"] == 7 or fresh["fields"]["tx_params"]["nonce"] == 0
        chain.fund(wallet["address"], 10**12)  # the balance no longer covers the maximum debit
        with pytest.raises(WalletFault) as spent:
            _approve(engine, proposal.proposal_id, fresh)
        assert (spent.value.code, spent.value.context.get("reason")) == ("wallet_quote_expired", "balance_changed")
        assert len(chain.sent) == 0
    finally:
        chain.__exit__(None, None, None)


def test_the_legacy_doors_refuse_a_ready_evm_pilot_transfer_and_a_legacy_wallet_still_cannot_prepare_one(pilot):
    from core.wallet import approval, custody, lifecycle, proposals

    chain = _base(pilot)
    try:
        _wallet, engine, proposal = _ready_on_base(pilot, chain)
        for door in (
            lambda: engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN)),
            lambda: engine.request_external_signature(proposal.proposal_id),
        ):
            with pytest.raises(WalletFault) as refused:
                door()
            assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "pilot_transfer_needs_quote_approval")
        assert len(chain.sent) == 0
        watcher = custody.create_watch_only_wallet(RECIPIENT, network=BASE_MAINNET)
        legacy = proposals.propose_transaction(wallet_id=watcher.wallet_id, destination="0x" + "6" * 40, amount_minor=AMOUNT, asset="ETH", origin=proposals.ORIGIN_USER, network=BASE_MAINNET)
        with pytest.raises(WalletFault):
            lifecycle.default_lifecycle().prepare(legacy.proposal_id)
        assert proposals.get_proposal(legacy.proposal_id).state == proposals.STATE_FAILED
    finally:
        chain.__exit__(None, None, None)


def test_the_evm_signer_verifies_its_own_output_and_drops_the_key(pilot):
    from core.wallet import pilot_custody

    chain = _base(pilot)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        params = {"chain_id": 8453, "nonce": 3, "max_priority_fee_per_gas": 10**6, "max_fee_per_gas": 2 * 10**9, "gas_limit": 21_000, "to": RECIPIENT, "value": 12_345}
        with pilot_custody.signing_session(wallet["wallet_id"], PIN) as signer:
            raw, tx_id = signer.sign_evm_type2(params)
            decoded = chain._decode("0x" + raw.hex())
            assert (decoded["from"], decoded["nonce"], decoded["value"], decoded["hash"]) == (wallet["address"].lower(), 3, 12_345, tx_id)
            with pytest.raises(WalletFault):
                signer.sign_svm(b"not this family")
        assert signer.closed
        with pytest.raises(WalletFault) as closed:
            signer.sign_evm_type2(params)
        assert closed.value.context.get("reason") == "signing_session_closed"
    finally:
        chain.__exit__(None, None, None)


# --- the send pre-check --------------------------------------------------------------------------------------------

def test_a_slot_used_between_signing_and_sending_revokes_the_bytes_sends_nothing_and_the_hold_waits_for_the_proof(pilot):
    """Between the signature and the transmit, another transaction of the account is mined with the signed nonce. The
    send's own pre-check sees the latest count past the nonce: the bytes are revoked and never transmitted, the hold
    stays until the observer proves the used slot at the finalized tag, then the request can be made again."""
    from core.wallet import lifecycle, proposals, settlement, transfers

    chain = _base(pilot)
    try:
        wallet, engine, proposal = _ready_on_base(pilot, chain)
        quote = _quote(proposal.proposal_id)
        nonce = int(quote["fields"]["tx_params"]["nonce"])
        original = lifecycle.PaymentLifecycle.send_signed

        def send_after_the_slot_was_used(self, proposal_id, **kwargs):
            assert transfers.latest_receipt(proposal_id)["state"] == "signed"
            chain.inject_replacement(wallet["address"], nonce)
            return original(self, proposal_id, **kwargs)

        pilot.setattr(lifecycle.PaymentLifecycle, "send_signed", send_after_the_slot_was_used)
        with pytest.raises(WalletFault) as refused:
            _approve(engine, proposal.proposal_id, quote)
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_broadcast_failed", "signed_not_sent:nonce_used_elsewhere")
        view = transfers.latest_receipt(proposal.proposal_id)
        assert (view["state"], view["in_flight"], _hold(proposal.proposal_id)[2]) == ("signed_revoked", True, "reserved"), "still listed while its hold waits"
        assert len(chain.sent) == 0 and len(chain.pending) == 0, "nothing was transmitted"
        # the used slot is proven at the finalized tag: the hold is released and the same request can be made again
        chain.finalized_block = chain.block_number
        settlement.observe_open_transfers()
        after = transfers.latest_receipt(proposal.proposal_id)
        assert (after["state"], proposals.get_proposal(proposal.proposal_id).state, _hold(proposal.proposal_id)[2]) == ("released", proposals.STATE_FAILED, "released")
        _engine, again = _pending(wallet, BASE_MAINNET)
        assert again.proposal_id != proposal.proposal_id and len(chain.sent) == 0
    finally:
        chain.__exit__(None, None, None)


def test_the_evm_signer_refuses_output_whose_recovered_sender_is_not_the_account(pilot, monkeypatch):
    """A signing library that signs with another key (a corrupted dependency, a wrong secret) produces bytes whose
    recovered sender is not the account: the session refuses them typed before anything can be recorded or sent."""
    from eth_account import Account

    from core.wallet import pilot_custody

    chain = _base(pilot)
    try:
        wallet = _ready_pilot_wallet(BASE_MAINNET)
        stranger = Account.create()
        real_sign = Account.sign_transaction

        def signs_with_a_stranger(transaction, private_key, *args, **kwargs):
            return real_sign(transaction, stranger.key, *args, **kwargs)

        monkeypatch.setattr(Account, "sign_transaction", staticmethod(signs_with_a_stranger))
        params = {"chain_id": 8453, "nonce": 0, "max_priority_fee_per_gas": 10**6, "max_fee_per_gas": 2 * 10**9, "gas_limit": 21_000, "to": RECIPIENT, "value": 12_345}
        with pilot_custody.signing_session(wallet["wallet_id"], PIN) as signer, pytest.raises(WalletFault) as refused:
            signer.sign_evm_type2(params)
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_signature_invalid", "pilot_signer_mismatch")
        assert signer.closed and len(chain.sent) == 0
    finally:
        chain.__exit__(None, None, None)
