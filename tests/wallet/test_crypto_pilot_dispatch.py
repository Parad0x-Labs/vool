"""Crypto Pilot, stage 5: one Solana transfer end to end on the scripted chain, and the worst cases around it.

The chain is the loopback Solana-dialect node answering as Devnet (SIMULATED CHAIN). A ready pilot wallet is made through
the product's own custody; a transfer is proposed, quoted, approved with the wallet PIN, claimed in one transaction,
signed once, sent exactly once and settled from the chain's own answers. The negative cases pin what must never
happen: a second send, a released hold without proof the bytes never left, a label that says more than the chain did,
a legacy door that signs a pilot transfer, a key that outlives its session, signed bytes in any record.

Assumed API (stage 5):
    core.wallet.lifecycle.PaymentLifecycle.approve_pilot_transfer(...) -> {"transfer": view, "duplicate": bool}
    core.wallet.lifecycle.PaymentLifecycle.send_signed(proposal_id, *, origin, rpc=None)
    core.wallet.settlement: observe_once, observe_bounded, observe_open_transfers, request_cancel, resolve_a6, clock
    core.wallet.transfers: latest_receipt, list_transfers, in_flight, get_transfer_by_id, take_marker
    core.wallet.pilot_custody.signing_session(wallet_id, credential) -> PilotSigner
    core.wallet.capabilities.PILOT_TRANSFER_READY_ROWS, pilot_transfer_ready(spec)
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
WRONG_PIN = "593027"
AMOUNT = 500_000  # lamports: under the default per-transaction cap, above the rent minimum for a new account


@pytest.fixture
def node():
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as rig:
        rig.real_signature = True  # the node answers the transaction's own signature, as a real node does
        rig.status_keyed = True  # statuses and transactions exist only for bytes the node recorded
        yield rig


@pytest.fixture
def pilot(monkeypatch, tmp_path, node):
    """Crypto on, Test networks selected, the scripted node as the Devnet endpoint, Solana Devnet's lane ready."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", node.url)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.delenv("VOOL_WALLET_RPC_URLS", raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import capabilities, chains, lifecycle

    monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({SOLANA_DEVNET}))
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield node
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _ready_pilot_wallet() -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_DEVNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"d-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _pending(wallet: dict, destination: str = "", amount: int = AMOUNT):
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=destination or _sol_key(), amount_minor=amount, asset="SOL", origin=proposals.ORIGIN_USER)
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


def _a6_state(proposal_id: str) -> str:
    from core.runtime_continuity import get_unresolved_effect
    from core.wallet.reconciliation import logical_effect_id

    row = get_unresolved_effect(logical_effect_id(proposal_id))
    return str(row["state"]) if row else "none"


def _methods(node) -> list[str]:
    return [str(call.get("method")) for call in node.calls]


# --- the transfer ---------------------------------------------------------------------------------------------

def test_an_approved_transfer_is_claimed_signed_sent_once_and_confirmed_from_the_chain(pilot):
    from core.wallet import proposals, quotes, transfers

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    fee_max = int(quote["fields"]["fee_max_minor"])
    node.transaction_fee = 4_800  # the chain charges less than the quoted ceiling: the settlement must count the charge

    result = _approve(engine, proposal.proposal_id, quote)
    transfer = result["transfer"]
    assert result["duplicate"] is False
    assert node.send_count() == 1 and node.distinct_transactions() == 1
    tx_id = transfer["tx_id"]
    assert node.recorded_signatures() == {tx_id}, "the recorded transaction identity is the node's own"
    assert (transfer["state"], transfer["state_label"], transfer["evidence_kind"], transfer["finality_seen"]) == ("confirmed", "Confirmed", "submitted", "confirmed")
    assert transfer["explorer_url"] == f"https://solscan.io/tx/{tx_id}?cluster=devnet" and transfer["explorer_link_text"] == "View on Solscan"
    assert (transfer["charged_fee_minor"], transfer["amount_minor"], transfer["display_symbol"], transfer["badge"]) == (str(node.transaction_fee), str(AMOUNT), "SOL", "DEVNET")
    assert transfer["balance_after_label"] == "observed after confirmation" and transfer["balance_after_minor"] == str(node.default_balance)
    current = proposals.get_proposal(proposal.proposal_id)
    assert (current.state, current.tx_signature) == (proposals.STATE_CONFIRMED, tx_id)
    assert _hold(proposal.proposal_id) == (AMOUNT, node.transaction_fee, "settled"), "the settled hold counts what the chain charged, not the ceiling"
    assert fee_max > node.transaction_fee
    assert quotes.get_quote(quote["quote_id"])["state"] == "consumed"
    row = transfers.get_transfer_by_id(proposal.proposal_id)
    assert row["raw_b64"] is None and row["a6_resolved_at"] and row["a6_outcome"] == "a6:applied"
    assert _a6_state(proposal.proposal_id) == "applied"
    states = [json.loads(r["payload_json"]).get("transfer_state") for r in _receipt_rows(proposal.proposal_id)]
    assert states == ["claimed", "signed", "dispatching", "unknown", "confirmed"]
    order = _methods(node)
    assert order.index("sendTransaction") > order.index("getLatestBlockhash"), "the send follows the revalidated blockhash"
    assert [t["proposal_id"] for t in transfers.list_transfers()] == [proposal.proposal_id] and transfers.in_flight() == []


def _receipt_rows(proposal_id: str) -> list[dict]:
    from core.wallet.store import connection

    with connection() as conn:
        rows = conn.execute("SELECT state, tx_signature, payload_json FROM wallet_receipts WHERE proposal_id = ? ORDER BY rowid", (proposal_id,)).fetchall()
    return [{"state": r[0], "tx_signature": r[1], "payload_json": r[2]} for r in rows]


def test_a_second_transfer_to_another_recipient_with_another_amount_settles_independently(pilot):
    node = pilot
    wallet = _ready_pilot_wallet()
    first_engine, first = _pending(wallet, amount=700_000)
    second_engine, second = _pending(wallet, amount=650_500)
    a = _approve(first_engine, first.proposal_id, _quote(first.proposal_id))["transfer"]
    b = _approve(second_engine, second.proposal_id, _quote(second.proposal_id))["transfer"]
    assert node.send_count() == 2 and node.distinct_transactions() == 2
    assert {a["tx_id"], b["tx_id"]} == node.recorded_signatures() and a["tx_id"] != b["tx_id"]
    assert (a["amount_minor"], b["amount_minor"], a["to_address"] != b["to_address"]) == ("700000", "650500", True)
    assert _hold(first.proposal_id) == (700_000, node.transaction_fee, "settled") and _hold(second.proposal_id) == (650_500, node.transaction_fee, "settled")


def test_a_wrong_pin_counts_one_refusal_sends_nothing_and_the_right_pin_then_sends_once(pilot):
    from core.wallet import proposals, quotes

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    with pytest.raises(WalletFault) as wrong:
        _approve(engine, proposal.proposal_id, quote, pin=WRONG_PIN)
    assert wrong.value.code == "wallet_pin_invalid"
    assert proposals.approval_refusals(proposal.proposal_id) == 1
    assert node.send_count() == 0 and _hold(proposal.proposal_id) is None
    assert quotes.get_quote(quote["quote_id"])["state"] == "open", "a refused credential consumes nothing"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
    assert transfer["state"] == "confirmed" and node.send_count() == 1


def test_a_repeated_approval_answers_duplicate_and_never_sends_again(pilot):
    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    first = _approve(engine, proposal.proposal_id, quote)
    again = _approve(engine, proposal.proposal_id, quote)
    assert again["duplicate"] is True and again["transfer"]["tx_id"] == first["transfer"]["tx_id"]
    with pytest.raises(WalletFault) as other:
        engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest="0" * 64, approver=__import__("core.wallet.approval", fromlist=["PinApprover"]).PinApprover(PIN))
    assert (other.value.code, other.value.context.get("reason")) == ("wallet_duplicate_payment", "transfer_already_claimed")
    assert node.send_count() == 1


def test_a_send_whose_answer_was_lost_keeps_the_liability_and_a_restarted_observer_settles_it_from_the_chain(pilot):
    """The node records the bytes and answers 500: nothing proves the outcome. The hold stays, the proposal reads
    Status unknown, and no code path retransmits. After a 'restart' (a fresh observer pass with no request memory)
    the chain answers and the transfer confirms with the fee the chain charged."""
    from core.wallet import proposals, settlement, transfers

    node = pilot
    node.send_mode = "accept_then_500"
    node.status_mode = "none"
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    transfer = _approve(engine, proposal.proposal_id, quote)["transfer"]
    assert (transfer["state"], transfer["state_label"], transfer["evidence_kind"]) == ("unknown", "Status unknown", "no_answer")
    assert transfer["in_flight"] is True and transfer["tx_id"] in node.recorded_signatures()
    assert _hold(proposal.proposal_id) == (AMOUNT, int(quote["fields"]["fee_max_minor"]), "reserved"), "an unknown outcome keeps amount and fee ceiling counted"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_BROADCAST
    assert _a6_state(proposal.proposal_id) == "dispatched"
    sends_before = node.send_count()

    # the process dies here; the row is what the next process finds
    assert [t["proposal_id"] for t in transfers.in_flight()] == [proposal.proposal_id]
    node.status_mode = "none"
    assert settlement.observe_open_transfers() == 1
    assert transfers.latest_receipt(proposal.proposal_id)["state"] == "unknown", "a null status is not evidence of anything"
    node.status_mode = "confirmed"
    assert settlement.observe_open_transfers() == 1
    after = transfers.latest_receipt(proposal.proposal_id)
    assert (after["state"], after["charged_fee_minor"], after["explorer_link_text"]) == ("confirmed", str(node.transaction_fee), "View on Solscan")
    assert _hold(proposal.proposal_id) == (AMOUNT, node.transaction_fee, "settled")
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_CONFIRMED
    assert _a6_state(proposal.proposal_id) == "applied"
    assert node.send_count() == sends_before == 1, "observation never transmits"


def test_a_validated_refusal_keeps_the_hold_and_is_labelled_as_a_refusal_not_as_never_sent(pilot):
    from core.wallet import proposals

    node = pilot
    node.send_mode = "preflight_error"
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
    assert (transfer["state"], transfer["evidence_kind"], transfer["state_label"]) == ("unknown", "refused", "Status unknown: the network refused this transaction")
    assert node.send_count() == 0, "a preflight refusal records nothing on the node"
    assert _hold(proposal.proposal_id)[2] == "reserved" and proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_BROADCAST
    assert transfer["tx_id"] and transfer["explorer_url"].endswith(f"/tx/{transfer['tx_id']}?cluster=devnet")


def test_an_already_processed_answer_reads_as_submitted_and_settles(pilot):
    node = pilot
    node.send_mode = "already_processed"
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
    assert transfer["state"] == "confirmed" and node.send_count() == 1
    kinds = [e.get("kind") for e in json.loads(__import__("core.wallet.transfers", fromlist=["get_transfer_by_id"]).get_transfer_by_id(proposal.proposal_id)["evidence_json"])]
    assert "send_answer" in kinds


def test_a_node_answering_another_id_is_recorded_and_the_chain_still_decides(pilot):
    from core.wallet import transfers

    node = pilot
    node.real_signature = False  # the legacy synthetic id: not this transaction's signature
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
    row = transfers.get_transfer_by_id(proposal.proposal_id)
    answers = [e for e in json.loads(row["evidence_json"]) if e.get("kind") == "send_answer"]
    assert answers and answers[0]["detail"] == "id_mismatch" and answers[0]["kind"] == "send_answer"
    assert transfer["state"] == "confirmed" and transfer["tx_id"] in node.recorded_signatures()


# --- cancel, freeze, controls -------------------------------------------------------------------------------

def test_a_cancel_before_the_claim_rejects_and_a_cancel_after_the_send_is_refused_with_the_transfer(pilot):
    from core.wallet import proposals, settlement

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, cancelled = _pending(wallet)
    answer = settlement.request_cancel(cancelled.proposal_id)
    assert (answer["cancelled"], answer["reason"]) == (True, "rejected_before_claim")
    assert proposals.get_proposal(cancelled.proposal_id).state == proposals.STATE_REJECTED
    engine, sent = _pending(wallet)
    _approve(engine, sent.proposal_id, _quote(sent.proposal_id))
    late = settlement.request_cancel(sent.proposal_id)
    assert (late["cancelled"], late["reason"], late["transfer"]["state"]) == (False, "transfer_already_dispatched", "confirmed")
    assert node.send_count() == 1


def test_a_freeze_after_the_credential_refuses_the_claim_and_leaves_the_quote_open(pilot, monkeypatch):
    from core.wallet import limits, pilot_custody, proposals, quotes, transfers

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    real_verify = pilot_custody._verify

    def verify_then_freeze(row, credential, *, source_context=None):
        secret = real_verify(row, credential, source_context=source_context)
        limits.set_frozen(True)  # the owner hits Freeze between the prompt and the claim
        return secret

    monkeypatch.setattr(pilot_custody, "_verify", verify_then_freeze)
    try:
        with pytest.raises(WalletFault) as frozen:
            _approve(engine, proposal.proposal_id, quote)
    finally:
        limits.set_frozen(False)
    assert (frozen.value.code, frozen.value.context.get("limit")) == ("wallet_limit_exceeded", "frozen")
    assert transfers.get_transfer_by_id(proposal.proposal_id) is None and _hold(proposal.proposal_id) is None
    assert quotes.get_quote(quote["quote_id"])["state"] == "open" and proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert node.send_count() == 0


def test_an_environment_switch_after_the_credential_refuses_the_claim(pilot, monkeypatch):
    from core.wallet import environment, pilot_custody, quotes, transfers

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    real_verify = pilot_custody._verify

    def verify_then_switch(row, credential, *, source_context=None):
        secret = real_verify(row, credential, source_context=source_context)
        monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
        environment.set_active_environment("mainnet")
        return secret

    monkeypatch.setattr(pilot_custody, "_verify", verify_then_switch)
    with pytest.raises(WalletFault) as refused:
        _approve(engine, proposal.proposal_id, quote)
    assert refused.value.code in {"wallet_environment_inactive", "wallet_approval_rejected", "wallet_quote_expired"}
    assert transfers.get_transfer_by_id(proposal.proposal_id) is None and node.send_count() == 0
    assert quotes.get_quote(quote["quote_id"])["state"] in {"open", "superseded"}


def test_the_legacy_doors_refuse_a_pilot_transfer_on_a_ready_row_by_name(pilot):
    from core.wallet import approval, proposals

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    for door in (
        lambda: engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN)),
        lambda: engine.request_external_signature(proposal.proposal_id),
    ):
        with pytest.raises(WalletFault) as refused:
            door()
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "pilot_transfer_needs_quote_approval")
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert proposals.approval_refusals(proposal.proposal_id) == 0 and node.send_count() == 0


def test_a_second_approval_while_one_is_in_progress_is_refused_without_a_prompt(pilot):
    from core.wallet import transfers

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    token = transfers.take_marker(f"approving:{proposal.proposal_id}", ttl_seconds=60)
    try:
        with pytest.raises(WalletFault) as busy:
            _approve(engine, proposal.proposal_id, quote, pin=WRONG_PIN)
    finally:
        transfers.release_marker(f"approving:{proposal.proposal_id}", token)
    assert (busy.value.code, busy.value.context.get("reason")) == ("wallet_duplicate_payment", "approval_in_progress")
    assert __import__("core.wallet.proposals", fromlist=["approval_refusals"]).approval_refusals(proposal.proposal_id) == 0, "no prompt, no counted attempt"
    assert node.send_count() == 0


# --- recovery and observation --------------------------------------------------------------------------------------

def test_a_claim_whose_request_died_is_released_by_the_observer_and_the_proposal_can_be_made_again(pilot, monkeypatch):
    from core.wallet import controls, limits, proposals, settlement, transfers
    from core.wallet.store import connection

    node = pilot
    wallet = _ready_pilot_wallet()
    _engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    now = time.time()
    with connection() as conn:
        limits._begin_immediate(conn)
        transfers.insert_claim(
            conn, proposal=proposal, quote_id=quote["quote_id"], quote_digest=quote["digest"], challenge_digest="c", environment="testnet", family="svm",
            from_address=wallet["address"], fee_max_minor=int(quote["fields"]["fee_max_minor"]), owner_token="dead-request", lease_until=now + 30,
            dispatch_deadline=now + 60, epochs=controls.epochs(conn), enabled_generation=0, now=now, blockhash=quote["fields"]["recent_blockhash"],
            blockhash_slot=1, last_valid_block_height=100,
        )
    assert settlement.observe_open_transfers() == 1 and transfers.latest_receipt(proposal.proposal_id)["state"] == "claimed", "a live lease is respected"
    monkeypatch.setattr(settlement, "clock", lambda: now + 31)
    assert settlement.observe_open_transfers() == 1
    view = transfers.latest_receipt(proposal.proposal_id)
    assert (view["state"], view["state_label"]) == ("released", "Not sent")
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_FAILED and _hold(proposal.proposal_id)[2] == "released"
    assert node.send_count() == 0
    again = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=AMOUNT, asset="SOL", origin=proposals.ORIGIN_USER)
    assert again.proposal_id != proposal.proposal_id


def test_a_signed_transfer_left_behind_is_revoked_then_released_when_its_blockhash_expires(pilot, monkeypatch):
    from core.wallet import controls, limits, proposals, settlement, transfers
    from core.wallet.store import connection

    node = pilot
    wallet = _ready_pilot_wallet()
    _engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    now = time.time()
    with connection() as conn:
        limits._begin_immediate(conn)
        transfers.insert_claim(
            conn, proposal=proposal, quote_id=quote["quote_id"], quote_digest=quote["digest"], challenge_digest="c", environment="testnet", family="svm",
            from_address=wallet["address"], fee_max_minor=int(quote["fields"]["fee_max_minor"]), owner_token="dead-request", lease_until=now + 30,
            dispatch_deadline=now + 60, epochs=controls.epochs(conn), enabled_generation=0, now=now, blockhash=quote["fields"]["recent_blockhash"],
            blockhash_slot=1, last_valid_block_height=100,
        )
        transfers.transition(conn, proposal.proposal_id, "signed", expected_state="claimed", now=now, tx_id="sig-never-sent", raw_b64="cmF3")
    monkeypatch.setattr(settlement, "clock", lambda: now + 61)
    settlement.observe_open_transfers()
    assert transfers.latest_receipt(proposal.proposal_id)["state"] == "signed_revoked"
    assert _hold(proposal.proposal_id)[2] == "reserved", "a signed authorization keeps its hold until the bytes provably cannot land"
    node.block_height = 101  # past the last valid height: the bytes can never be included
    settlement.observe_open_transfers()
    view = transfers.latest_receipt(proposal.proposal_id)
    assert (view["state"], proposals.get_proposal(proposal.proposal_id).state, _hold(proposal.proposal_id)[2]) == ("released", proposals.STATE_FAILED, "released")
    assert node.send_count() == 0 and transfers.get_transfer_by_id(proposal.proposal_id)["raw_b64"] is None


def test_an_unknown_transfer_with_no_status_past_its_last_valid_height_offers_stop_waiting_and_keeps_counting(pilot):
    from core.wallet import settlement, transfers

    node = pilot
    node.send_mode = "accept_then_500"
    node.status_mode = "none"
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
    assert transfer["state"] == "unknown" and transfer["offered_exit"] == ""
    node.block_height = 250
    settlement.observe_open_transfers()
    view = transfers.latest_receipt(proposal.proposal_id)
    assert (view["state"], view["offered_exit"]) == ("unknown", "stop_waiting")
    assert _hold(proposal.proposal_id)[2] == "reserved", "absence of a status never releases a transmitted transfer"


# --- the key and the records --------------------------------------------------------------------------------------

def test_the_signing_session_opens_the_seal_once_signs_and_drops_the_key(pilot, monkeypatch):
    from core.wallet import pilot_custody

    wallet = _ready_pilot_wallet()
    calls = {"unseal": 0}
    real_unseal = pilot_custody._unseal

    def counting_unseal(row, credential, *, source_context=None):
        calls["unseal"] += 1
        return real_unseal(row, credential, source_context=source_context)

    monkeypatch.setattr(pilot_custody, "_unseal", counting_unseal)
    with pilot_custody.signing_session(wallet["wallet_id"], PIN) as signer:
        first = signer.sign_svm(b"message one")
        second = signer.sign_svm(b"message two")
        assert first != second and len(first) == 64
    assert calls["unseal"] == 1 and signer.closed
    with pytest.raises(WalletFault) as closed:
        signer.sign_svm(b"after the session")
    assert closed.value.context.get("reason") == "signing_session_closed"
    with pytest.raises(WalletFault) as wrong:
        with pilot_custody.signing_session(wallet["wallet_id"], WRONG_PIN):
            raise AssertionError("a wrong PIN never yields a signer")
    assert wrong.value.code == "wallet_pin_invalid"


def test_signed_bytes_never_appear_in_receipts_status_views_or_faults(pilot):
    from core.wallet import status, transfers
    from core.wallet.redaction import redact_wallet_record
    from core.wallet.store import connection

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    transfer = _approve(engine, proposal.proposal_id, _quote(proposal.proposal_id))["transfer"]
    import base64

    raw_b64 = base64.b64encode(node.sent[0]).decode("ascii")
    with connection() as conn:
        payloads = " ".join(r[0] for r in conn.execute("SELECT payload_json FROM wallet_receipts WHERE proposal_id = ?", (proposal.proposal_id,)).fetchall())
    assert raw_b64 not in payloads and raw_b64 not in json.dumps(transfer) and raw_b64 not in json.dumps(status.wallet_status())
    assert "raw_b64" not in json.dumps(transfers.list_transfers()) and "raw_b64" not in redact_wallet_record({"raw_b64": raw_b64, "tx_id": transfer["tx_id"]})
    assert redact_wallet_record({"tx_id": transfer["tx_id"], "explorer_url": transfer["explorer_url"]}) == {"tx_id": transfer["tx_id"], "explorer_url": transfer["explorer_url"]}


def test_an_amount_above_the_storage_limit_is_refused_before_any_write(pilot):
    from core.wallet import proposals

    wallet = _ready_pilot_wallet()
    with pytest.raises(WalletFault) as refused:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=2**63, asset="SOL", origin=proposals.ORIGIN_USER)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_limit_exceeded", "amount_exceeds_storage_limit")
    assert proposals.list_proposals(limit=10) == []


def test_the_rows_declared_endpoint_outranks_the_engines_default_client(pilot, monkeypatch):
    """A daemon started without the legacy devnet setting but with a per-row declaration must read and send on the
    declared endpoint, not on the public one its default client was built for (found by the served matrix drive)."""
    from urllib.parse import urlsplit

    from core.wallet import lifecycle

    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as node:
        declared = urlsplit(node.url).netloc
        monkeypatch.delenv("VOOL_WALLET_TESTNET_RPC_URL", raising=False)
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_DEVNET: node.url}))
        engine = lifecycle.default_lifecycle()
        assert urlsplit(engine.rpc.url).netloc != declared, "the default client is the legacy devnet setting"
        assert urlsplit(engine._rpc_for(SOLANA_DEVNET).url).netloc == declared
        monkeypatch.delenv("VOOL_WALLET_RPC_URLS", raising=False)
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", node.url)
        assert urlsplit(lifecycle.default_lifecycle()._rpc_for(SOLANA_DEVNET).url).netloc == declared, "without a declaration the legacy setting still serves the row"
