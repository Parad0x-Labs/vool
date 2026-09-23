"""Observation, settlement and recovery of Crypto Pilot transfers.

The observer never transmits. It reads the chain for rows that were dispatched, recovers rows whose approving request
died, finalizes cancel requests, and resolves the A6 effect record for every transfer ending. Every write goes through
:func:`core.wallet.transfers.transition` on one connection, so the transfer, its proposal, its hold and its receipt
move together, and a hold is released only after the effect record says the bytes never left.

Evidence rules (Solana): a status read at ``confirmed`` commitment decides. ``processed`` is pending; ``confirmed`` or
``finalized`` without an error is confirmed (the fee comes from the transaction itself when the node serves it); an
error is failed only once it is ``finalized``; a null status proves nothing about a transmitted transaction, so an
``unknown`` row stays unknown and, past the blockhash's last valid height, offers the owner a stop-waiting exit that
keeps the liability counted.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core.wallet import chains, limits, transfers
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.settlement"
logger = logging.getLogger(__name__)

OBSERVE_TICK_SECONDS = 15.0
IDLE_TICK_SECONDS = 60.0
PASS_RPC_BUDGET_SECONDS = 30.0
SIGNER_BUDGET_SECONDS = 30.0
DISPATCH_DEADLINE_SECONDS = 60.0
SEND_TIMEOUT_SECONDS = 20.0
APPROVAL_MARKER_SECONDS = 200.0
OBSERVE_HORIZON_SECONDS = 7 * 24 * 3600
A6_RETRY_LIMIT = 20
#: blocks of blockhash validity a claim needs (a fresh Solana blockhash is valid for 150 blocks after it was produced)
SVM_CLAIM_MARGIN_BLOCKS = 30
#: blocks of validity a send needs right before it transmits
SVM_SEND_MARGIN_BLOCKS = 5
_OBSERVE_LEASE_SECONDS = 60.0
_EVIDENCE_CAP = 40

EXIT_STOP_WAITING = "stop_waiting"
EXIT_RESEND = "resend"
EXIT_SLOT_USED = "stop_waiting_slot_used"
EXIT_REFUSED = "stop_waiting_refused"
#: passes with the base fee above the quoted ceiling before the refused exit is offered, and the span they must cover
FEE_SPIKE_PASSES = 3
FEE_SPIKE_SPAN_SECONDS = 600.0

#: injectable clock for tests: every time comparison in this module reads it
clock = time.time


# --- evidence ------------------------------------------------------------------------------------------

def appended_evidence(row: dict[str, Any], kind: str, **detail: Any) -> str:
    """The row's evidence list plus one entry, as the JSON the column stores (bounded)."""
    try:
        entries = json.loads(row.get("evidence_json") or "[]")
    except ValueError:
        entries = []
    if not isinstance(entries, list):
        entries = []
    entries.append({"at": clock(), "kind": str(kind), **{k: v for k, v in detail.items() if v is not None}})
    return json.dumps(entries[-_EVIDENCE_CAP:], sort_keys=True, default=str)


def record_evidence(proposal_id: str, kind: str, **detail: Any) -> None:
    with connection() as conn:
        row = transfers.get_transfer(conn, proposal_id)
        if row is not None:
            transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, kind, **detail))


def control_baseline() -> dict[str, Any]:
    """The control epochs and the Crypto-switch generation as one reading, taken before any network I/O."""
    from core.wallet import config, controls

    with connection() as conn:
        epochs = controls.epochs(conn)
    return {"epochs": epochs, "enabled_generation": config.enabled_generation()}


# --- A6: the effect record (design v5 §2.3) --------------------------------------------------------------

_A6_DONE_STATES = ("expired_pre_dispatch", "failed_safe_to_retry", "superseded")


def resolve_a6(proposal_id: str, *, target: str, dispatch_cas_happened: bool, tx_id: str = "") -> tuple[bool, str]:
    """Resolve the payment effect for one transfer ending by its logical id, never by an instance id that may be
    missing. Returns (done, note). A pre-dispatch release is done when no instance exists or the instance is
    cancelled, classified failed-safe or resolved; it is never done while the record says the effect applied."""
    from core.runtime_continuity import (
        cancel_effect_reservation,
        classify_effect_outcome,
        get_unresolved_effect,
        resolve_unresolved_effect,
    )
    from core.wallet.reconciliation import logical_effect_id

    logical = logical_effect_id(proposal_id)
    for _attempt in range(3):
        row = get_unresolved_effect(logical)
        state = str(row.get("state") or "") if row else ""
        instance = str(row.get("effect_instance_id") or "") if row else ""
        if row is None or state in _A6_DONE_STATES:
            return True, f"a6:{state or 'none'}"
        if not dispatch_cas_happened:
            if state == "applied":
                # the record says the effect landed: nothing pre-dispatch may release it
                return False, "a6:applied_without_dispatch"
            if state == "prepared":
                if cancel_effect_reservation(logical_effect_id=logical, effect_instance_id=instance, reason=f"transfer_{target}"):
                    return True, "a6:cancelled_prepared"
                continue
            if state == "dispatched":
                if classify_effect_outcome(logical_effect_id=logical, effect_instance_id=instance, outcome="failed_safe_to_retry", reason=f"transfer_{target}", detail="no dispatch CAS; the bytes never left"):
                    return True, "a6:failed_safe_from_dispatched"
                continue
            if state == "unknown":
                answer = resolve_unresolved_effect(logical_effect_id=logical, resolution="CONFIRMED_FAILED_SAFE_TO_RETRY", source="mechanical", evidence="no dispatch CAS; the bytes never left", resolved_by=AUTHORITY)
                if str(answer.get("outcome") or "") in {"resolved", "already_resolved", "no_active_effect"}:
                    return True, "a6:failed_safe_from_unknown"
                continue
            return False, f"a6:unexpected_{state}"
        if target == transfers.STATE_CONFIRMED:
            if state == "dispatched" and classify_effect_outcome(logical_effect_id=logical, effect_instance_id=instance, outcome="applied", reason="confirmed_on_chain", detail=str(tx_id or "")):
                return True, "a6:applied"
            answer = resolve_unresolved_effect(logical_effect_id=logical, resolution="CONFIRMED_APPLIED", source="provider", evidence=str(tx_id or ""), resolved_by=AUTHORITY)
            if str(answer.get("outcome") or "") in {"resolved", "already_resolved", "no_active_effect"}:
                return True, "a6:applied"
            continue
        if target == transfers.STATE_FAILED_ON_CHAIN:
            # the transaction executed and failed: the amount did not move, the fee did; a new proposal is safe
            if state == "dispatched" and classify_effect_outcome(logical_effect_id=logical, effect_instance_id=instance, outcome="failed_safe_to_retry", reason="failed_on_chain", detail=str(tx_id or "")):
                return True, "a6:failed_on_chain"
            answer = resolve_unresolved_effect(logical_effect_id=logical, resolution="CONFIRMED_FAILED_SAFE_TO_RETRY", source="provider", evidence=f"failed on chain: {tx_id}", resolved_by=AUTHORITY)
            if str(answer.get("outcome") or "") in {"resolved", "already_resolved", "no_active_effect"}:
                return True, "a6:failed_on_chain"
            continue
        if target == transfers.STATE_STOPPED_WAITING:
            # nothing proves the outcome: the record stays unknown; the owner's decision is in the transfer receipt
            if state == "dispatched":
                classify_effect_outcome(logical_effect_id=logical, effect_instance_id=instance, outcome="unknown", reason="owner_stopped_waiting", detail=str(tx_id or ""))
            return True, "a6:left_unknown_owner_stopped"
        return True, f"a6:{state}"
    return False, "a6:unresolved_after_retries"


# --- transitions with their A6 order -------------------------------------------------------------------------

_RELEASING = frozenset({transfers.STATE_RELEASED, transfers.STATE_DISCARDED, transfers.STATE_CANCELLED})
_SETTLING = frozenset({transfers.STATE_CONFIRMED, transfers.STATE_FAILED_ON_CHAIN, transfers.STATE_STOPPED_WAITING})


def apply_transition(proposal_id: str, new_state: str, expected_state: str, *, detail: dict[str, Any] | None = None, **columns: Any) -> dict[str, Any] | None:
    """One transfer transition with the effect record resolved in the right order: before a release (the bytes must
    provably never have left), after a settlement. Returns the row, or None when the CAS lost or the effect record
    refused a release (the row stays for a later pass)."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None or row["state"] != expected_state:
        return None
    tx_id = str(row.get("tx_id") or "")
    if new_state in _RELEASING:
        done, note = resolve_a6(proposal_id, target=new_state, dispatch_cas_happened=False, tx_id=tx_id)
        if not done:
            with connection() as conn:
                transfers.update_columns(conn, proposal_id, a6_outcome=note, evidence_json=appended_evidence(row, "a6_refused_release", note=note, target=new_state))
            return None
        columns.update(a6_outcome=note, a6_resolved_at=utcnow())
    try:
        with connection() as conn:
            limits._begin_immediate(conn)
            moved = transfers.transition(conn, proposal_id, new_state, expected_state=expected_state, now=clock(), detail=detail, **columns)
    except transfers.TransferTransitionError:
        return None
    if new_state in _SETTLING:
        done, note = resolve_a6(proposal_id, target=new_state, dispatch_cas_happened=True, tx_id=tx_id)
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, a6_outcome=note, a6_resolved_at=utcnow() if done else None)
        moved["a6_outcome"] = note
    return moved


def release_claimed(proposal_id: str, *, reason: str) -> dict[str, Any] | None:
    """A claim that ends before dispatch: proposal failed (approval_not_completed), hold released, effect failed-safe."""
    return apply_transition(proposal_id, transfers.STATE_RELEASED, transfers.STATE_CLAIMED, detail={"why": reason})


def revoke_signed(proposal_id: str, *, reason: str) -> dict[str, Any] | None:
    """A signed transfer that may no longer be sent (controls changed, validity consumed, a guard refused)."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None:
        return None
    return apply_transition(proposal_id, transfers.STATE_SIGNED_REVOKED, transfers.STATE_SIGNED, detail={"why": reason},
                            evidence_json=appended_evidence(row, "signed_revoked", reason=reason))


def finalize_cancel(proposal_id: str) -> dict[str, Any] | None:
    """Finish a cancel request from any pre-dispatch state; the copy the sheet shows depends on that state."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None or row["state"] not in (transfers.STATE_CLAIMED, transfers.STATE_SIGNED, transfers.STATE_SIGNED_REVOKED):
        return None
    return apply_transition(proposal_id, transfers.STATE_CANCELLED, row["state"], detail={"why": "owner_cancelled", "signed": row["state"] != transfers.STATE_CLAIMED})


def request_cancel(proposal_id: str) -> dict[str, Any]:
    """The reject door for a Crypto Pilot proposal, in ONE transaction: a transfer row gets the cancel flag (which
    every later sign, send and release CAS refuses); a proposal that was never claimed is rejected. The answer says
    exactly what happened; a request that reached the send answers ``cancelled: False`` with the transfer."""
    from core.wallet import proposals

    now = clock()
    rejected: dict[str, Any] | None = None
    origin = ""
    with connection() as conn:
        limits._begin_immediate(conn)
        row = transfers.get_transfer(conn, proposal_id)
        if row is None:
            proposal = proposals._get(conn, proposal_id)
            if proposal is None:
                return {"cancelled": False, "reason": "unknown_proposal"}
            try:
                proposals._transition(conn, proposal_id, proposals.STATE_REJECTED, expected_state=proposals.STATE_PENDING_APPROVAL, detail={"reason": "owner_rejected"})
            except proposals.ProposalTransitionError:
                return {"cancelled": False, "reason": f"not_awaiting_approval:{proposal.state}", "proposal": proposal.to_dict()}
            current = proposals._get(conn, proposal_id)
            if str(proposal.origin) == proposals.ORIGIN_DNA_FEE:
                from core.wallet import dna_fees

                dna_fees.on_transfer_state(conn, proposal_id, transfers.STATE_CANCELLED)
            rejected = {"cancelled": True, "reason": "rejected_before_claim", "proposal": current.to_dict() if current else proposal.to_dict()}
            origin = str(proposal.origin)
        else:
            rejected = None
    if rejected is not None:
        if origin == proposals.ORIGIN_USEPOD:
            # the collection planned to ride this payment's approval goes back to accrued (its own proposal expires);
            # after the commit, under the ledger's fence: a companion already claimed with the payment is the lane's
            from core.wallet import dna_fees

            rejected["dna_fee_collection"] = dna_fees.release_companion_for_payment(proposal_id, reason="payment_rejected")
        return rejected
    with connection() as conn:
        limits._begin_immediate(conn)
        row = transfers.get_transfer(conn, proposal_id)
        if row is None:
            return {"cancelled": False, "reason": "transfer_row_vanished"}
        cursor = conn.execute(
            "UPDATE wallet_transfers SET cancel_requested_at = ?, updated_at = ? WHERE proposal_id = ? AND state IN (?, ?, ?) AND cancel_requested_at IS NULL",
            (now, utcnow(), proposal_id, transfers.STATE_CLAIMED, transfers.STATE_SIGNED, transfers.STATE_SIGNED_REVOKED),
        )
        row = transfers.get_transfer(conn, proposal_id) or row
        if cursor.rowcount != 1:
            if row.get("cancel_requested_at") and row["state"] in (transfers.STATE_CLAIMED, transfers.STATE_SIGNED, transfers.STATE_SIGNED_REVOKED):
                reason = "cancel_already_requested"
            elif row["state"] == transfers.STATE_CANCELLED:
                reason = "already_cancelled"
            else:
                reason = "transfer_already_dispatched" if row["state"] not in transfers.NEVER_SENT_STATES else f"transfer_{row['state']}"
            return {"cancelled": row["state"] == transfers.STATE_CANCELLED, "reason": reason, "transfer": transfers.view_of(row)}
        idle = float(row.get("lease_until") or 0) < now
    finalized = finalize_cancel(proposal_id) if idle else None
    view = transfers.view_of(finalized) if finalized else transfers.latest_receipt(proposal_id)
    return {"cancelled": True, "reason": "cancelled" if finalized else "cancel_requested", "transfer": view}


# --- observation ----------------------------------------------------------------------------------------------

def _svm_client(row: dict[str, Any], rpc: Any = None) -> Any:
    if rpc is not None:
        return rpc
    from core.wallet.lifecycle import RpcClient

    return RpcClient(chains.network_rpc_url(row["network"]), network=row["network"])


def _svm_fee(client: Any, tx_id: str) -> int | None:
    """The fee the chain charged, or None when the node does not serve the transaction (never zero by default)."""
    with contextlib.suppress(Exception):
        answer = client._call("getTransaction", [tx_id, {"commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        if isinstance(answer, dict):
            fee = ((answer.get("meta") or {}).get("fee"))
            if fee is not None:
                return int(fee)
    return None


def _svm_balance(client: Any, address: str) -> int | None:
    with contextlib.suppress(Exception):
        return int(client.balance(address))
    return None


def _is_token_transfer(row: dict[str, Any]) -> bool:
    from core.wallet import svm_tokens

    return svm_tokens.is_token_transfer(str(row.get("network") or ""), str(row.get("asset") or ""))


def _svm_token_principal(client: Any, row: dict[str, Any], tx_id: str) -> bool | None:
    """Whether the confirmed transaction's token balances show this transfer's principal leaving the payer and reaching
    the recipient (None when the node served no answer or no token balances)."""
    from core.wallet import svm_tokens

    try:
        answer = client._call("getTransaction", [tx_id, {"commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        asset = svm_tokens.token_asset(str(row["network"]), str(row["asset"]))
    except Exception:
        return None
    return svm_tokens.principal_moved(answer, mint=asset.address, payer=str(row["from_address"]), recipient_owner=str(row["to_address"]), amount_minor=int(row["amount_minor"]))


def _null_reads(row: dict[str, Any]) -> int:
    try:
        entries = json.loads(row.get("evidence_json") or "[]")
    except ValueError:
        return 0
    count = 0
    for entry in reversed(entries if isinstance(entries, list) else []):
        if isinstance(entry, dict) and entry.get("kind") == "status_null":
            count += 1
        else:
            break
    return count


def _observe_svm(row: dict[str, Any], client: Any) -> dict[str, Any] | None:
    proposal_id, tx_id, state = str(row["proposal_id"]), str(row.get("tx_id") or ""), str(row["state"])
    answer = client._call("getSignatureStatuses", [[tx_id], {"searchTransactionHistory": True}])
    statuses = (answer or {}).get("value") or []
    entry = statuses[0] if statuses else None
    context_slot = int(((answer or {}).get("context") or {}).get("slot") or 0)
    if not entry:
        height = None
        with contextlib.suppress(Exception):
            height = int(client._call("getBlockHeight", [{"commitment": "confirmed"}]))
        last_valid = int(row.get("last_valid_block_height") or 0)
        nulls = _null_reads(row) + 1
        evidence = appended_evidence(row, "status_null", height=height, last_valid_block_height=last_valid)
        if state == transfers.STATE_PENDING and nulls >= 2:
            return apply_transition(proposal_id, transfers.STATE_UNKNOWN, state, evidence_json=evidence, evidence_kind="no_answer")
        offered = json.loads(row.get("offered_exit_json") or "{}") if row.get("offered_exit_json") else {}
        if state == transfers.STATE_UNKNOWN and height is not None and last_valid and height > last_valid:
            offered = {"exit": EXIT_STOP_WAITING, "observed_at": clock(), "height": height, "last_valid_block_height": last_valid, "status": "null_after_last_valid"}
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, evidence_json=evidence, offered_exit_json=json.dumps(offered, sort_keys=True))
        return transfers.get_transfer_by_id(proposal_id)
    status = str(entry.get("confirmationStatus") or "")
    err = entry.get("err")
    slot = int(entry.get("slot") or context_slot or 0)
    if status == "processed":
        if state in (transfers.STATE_UNKNOWN, transfers.STATE_DISPATCHING):
            return apply_transition(proposal_id, transfers.STATE_PENDING, state, block_ref=slot, finality_seen=status, evidence_json=appended_evidence(row, "status", status=status, slot=slot))
        return row
    if status not in ("confirmed", "finalized"):
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, "status_unrecognized", status=status[:40]))
        return row
    if err is None:
        if state != transfers.STATE_CONFIRMED and _is_token_transfer(row):
            proven = _svm_token_principal(client, row, tx_id)
            if proven is not True:
                # a confirmed token transfer is paid only when its own token balances show the principal moved;
                # until then it stays where it is and the evidence says why
                with connection() as conn:
                    transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(
                        row, "principal_unproven", status=status, slot=slot, answer="token_balances_disagree" if proven is False else "no_token_balances"))
                return transfers.get_transfer_by_id(proposal_id)
        fee = _svm_fee(client, tx_id)
        balance_after = _svm_balance(client, str(row["from_address"]))
        columns: dict[str, Any] = {"block_ref": slot, "finality_seen": status, "evidence_json": appended_evidence(row, "status", status=status, slot=slot, fee=fee), "offered_exit_json": "{}"}
        if balance_after is not None:
            columns["balance_after_minor"] = balance_after
        if state == transfers.STATE_CONFIRMED:
            return row
        return apply_transition(proposal_id, transfers.STATE_CONFIRMED, state, charged_fee_minor=fee, **columns)
    if status == "finalized":
        fee = _svm_fee(client, tx_id)
        if state == transfers.STATE_STOPPED_WAITING or state in (transfers.STATE_PENDING, transfers.STATE_UNKNOWN):
            return apply_transition(proposal_id, transfers.STATE_FAILED_ON_CHAIN, state, charged_fee_minor=fee, block_ref=slot, finality_seen=status,
                                    evidence_json=appended_evidence(row, "status", status=status, slot=slot, err=json.dumps(err, sort_keys=True, default=str)[:200]), offered_exit_json="{}")
        return row
    # an error at confirmed commitment: failed unless a fork drops it; nothing is settled before finality
    evidence = appended_evidence(row, "status", status=status, slot=slot, err=json.dumps(err, sort_keys=True, default=str)[:200], note="failed on chain, awaiting finality")
    if state in (transfers.STATE_UNKNOWN, transfers.STATE_DISPATCHING):
        return apply_transition(proposal_id, transfers.STATE_PENDING, state, block_ref=slot, finality_seen=status, evidence_json=evidence)
    with connection() as conn:
        transfers.update_columns(conn, proposal_id, evidence_json=evidence, block_ref=slot, finality_seen=status)
    return transfers.get_transfer_by_id(proposal_id)


def _evm_client(row: dict[str, Any], rpc: Any = None) -> Any:
    if rpc is not None:
        return rpc
    from core.wallet.lifecycle import RpcClient

    return RpcClient(chains.network_rpc_url(row["network"]), network=row["network"])


def _hex_int(value: Any) -> int | None:
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None


FORK_ISTHMUS = "isthmus"  # Holocene/Isthmus header (extraData version 0): operator fee gas × scalar ÷ 1e6 + constant
FORK_JOVIAN = "jovian"  # Jovian header (extraData version 1): operator fee gas × scalar × 100 + constant
FORK_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FeeEvidence:
    """What one receipt proves about the charge on its row: the subtotal of the usable components and the names of the
    required components it does not carry usably. Complete evidence is an exact total; anything else is bounded.
    ``fork`` names the OP-stack fork the operator term was read under ("" off OP-stack rows or without an operator
    term); ``note`` says why the fork could not be established."""

    known_minor: int
    missing: tuple[str, ...]
    fork: str = ""
    note: str = ""

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def total(self) -> int | None:
        return int(self.known_minor) if self.complete else None


def _extra_data_version(block: dict[str, Any] | None) -> int | None:
    """The OP-stack header's extraData version byte (Holocene 0, Jovian 1), or None when the header does not carry a
    versioned extraData (no block, no field, empty bytes, unreadable hex)."""
    if not isinstance(block, dict):
        return None
    raw = str(block.get("extraData") or "").strip().lower()
    if not raw.startswith("0x") or len(raw) < 4:
        return None
    try:
        return int(raw[2:4], 16)
    except ValueError:
        return None


def op_stack_fork(receipt: dict[str, Any], block: dict[str, Any] | None) -> tuple[str, str]:
    """The fork a receipt's operator fee must be read under, from validated evidence only: the canonical block header's
    extraData version byte, corroborated by the receipt's Jovian fields (`daFootprintGasScalar`, `blobGasUsed`).
    Returns (fork, note). Anything unknown or inconsistent is FORK_UNKNOWN — never guessed from the receipt alone."""
    receipt_jovian = "daFootprintGasScalar" in receipt
    version = _extra_data_version(block)
    if version is None:
        return FORK_UNKNOWN, "header_extra_data_unavailable"
    if version == 1:
        return (FORK_JOVIAN, "extra_data_version_1") if receipt_jovian else (FORK_UNKNOWN, "jovian_header_without_receipt_da_footprint")
    if version == 0:
        return (FORK_UNKNOWN, "pre_jovian_header_with_receipt_da_footprint") if receipt_jovian else (FORK_ISTHMUS, "extra_data_version_0")
    return FORK_UNKNOWN, f"extra_data_version_{version}_unknown"


def evm_fee_evidence(receipt: dict[str, Any], spec: chains.ChainIdentity, block: dict[str, Any] | None = None) -> FeeEvidence:
    """The fee components a receipt must carry on this row, read strictly.

    Every EVM row: gas used times the effective price (unreadable: the execution component is missing). OP-stack rows
    (Base): `l1Fee`, the L1 data fee op-geth sets on every non-deposit receipt -- absent or malformed it is UNKNOWN,
    never zero; the Isthmus operator fee fields `operatorFeeScalar`/`operatorFeeConstant` are added to a receipt
    "if and only if at least one of them is non zero" (the specification), so both absent is a zero by the chain's own
    rule, and exactly one present, or a malformed one, is missing evidence. Ethereum, BNB Smart Chain and Robinhood
    Chain (Arbitrum Nitro folds its L1 cost into gas used) carry no separate component.

    The operator term's formula is the fork's — Isthmus `gas × scalar ÷ 1e6 + constant`, Jovian `gas × scalar × 100 +
    constant` (the Base and OP Stack Jovian execution-engine specifications) — established from ``block``, the
    receipt's canonical header (see op_stack_fork). Without that evidence, or with inconsistent evidence, the operator
    component is missing: the fee stays bounded by the approved ceiling, never an exact total under a guessed formula."""
    gas_used, price = _hex_int(receipt.get("gasUsed")), _hex_int(receipt.get("effectiveGasPrice"))
    if gas_used is None or price is None:
        return FeeEvidence(0, ("execution",))
    known = gas_used * price
    missing: list[str] = []
    fork, note = "", ""
    if spec.fee_model == chains.FEE_OP_STACK:
        l1 = _hex_int(receipt.get("l1Fee"))
        if l1 is None:
            missing.append("l1Fee")
        else:
            known += l1
        scalar_raw, constant_raw = receipt.get("operatorFeeScalar"), receipt.get("operatorFeeConstant")
        if scalar_raw is not None or constant_raw is not None:
            scalar, constant = _hex_int(scalar_raw), _hex_int(constant_raw)
            fork, note = op_stack_fork(receipt, block)
            if scalar is None or constant is None:
                missing.append("operatorFee")
                note = "operator_fields_malformed"
            elif fork == FORK_JOVIAN:
                known += gas_used * scalar * 100 + constant
            elif fork == FORK_ISTHMUS:
                known += gas_used * scalar // 10**6 + constant
            else:
                missing.append("operatorFee")
    return FeeEvidence(int(known), tuple(missing), fork, note)


def evm_charged_fee(receipt: dict[str, Any], spec: chains.ChainIdentity, block: dict[str, Any] | None = None) -> int | None:
    """The exact charge from the receipt and its header, or None when the row's evidence is incomplete (see evm_fee_evidence)."""
    return evm_fee_evidence(receipt, spec, block=block).total


def _fee_columns(evidence: FeeEvidence) -> dict[str, Any]:
    """The transfer columns a settlement writes for its fee evidence; a bounded fee keeps the row observable for the
    horizon so a later complete receipt can refine it."""
    if evidence.complete:
        return {"fee_state": transfers.FEE_EXACT, "fee_known_minor": int(evidence.known_minor), "fee_missing_json": "[]", "fee_fork": evidence.fork}
    return {"fee_state": transfers.FEE_BOUNDED, "fee_known_minor": int(evidence.known_minor), "fee_missing_json": json.dumps(list(evidence.missing)), "fee_fork": evidence.fork,
            "observe_until": clock() + OBSERVE_HORIZON_SECONDS}


def _refine_fee(row: dict[str, Any], evidence: FeeEvidence) -> dict[str, Any] | None:
    """A settled row whose fee was bounded meets a complete receipt: the ledger's settled hold is corrected to the exact
    charge once (`limits._resettle` replaces, never adds) and the row records the refinement. A second look at the
    same complete evidence changes nothing: the row is exact by then."""
    proposal_id = str(row["proposal_id"])
    if str(row.get("fee_state") or "") != transfers.FEE_BOUNDED or not evidence.complete:
        return row
    total = int(evidence.known_minor)
    with connection() as conn:
        limits._begin_immediate(conn)
        current = transfers.get_transfer(conn, proposal_id)
        if current is None or str(current.get("fee_state") or "") != transfers.FEE_BOUNDED:
            return current or row
        limits._resettle(conn, proposal_id, charged_fee_minor=total, amount_moved=str(current["state"]) == transfers.STATE_CONFIRMED)
        try:
            was_missing = json.loads(current.get("fee_missing_json") or "[]")
        except ValueError:
            was_missing = []
        transfers.update_columns(
            conn, proposal_id, charged_fee_minor=total, fee_state=transfers.FEE_EXACT, fee_known_minor=total, fee_missing_json="[]", fee_fork=evidence.fork, observe_until=None,
            evidence_json=appended_evidence(current, "fee_refined", fee=total, was_missing=was_missing, fork=evidence.fork),
        )
    return transfers.get_transfer_by_id(proposal_id)


def _quote_params(row: dict[str, Any]) -> dict[str, Any]:
    from core.wallet import quotes

    record = quotes.get_quote(str(row.get("quote_id") or ""))
    return dict(((record or {}).get("fields") or {}).get("tx_params") or {})


def _offered(row: dict[str, Any]) -> dict[str, Any]:
    try:
        offered = json.loads(row.get("offered_exit_json") or "{}")
    except ValueError:
        offered = {}
    return offered if isinstance(offered, dict) else {}


def _evm_tags(client: Any, spec: chains.ChainIdentity) -> dict[str, int | None]:
    display = spec.finality if spec.finality in (chains.FINALITY_SAFE_TAG, chains.FINALITY_FINALIZED_TAG) else chains.FINALITY_FINALIZED_TAG
    numbers: dict[str, int | None] = {}
    for tag in {display, chains.FINALITY_FINALIZED_TAG}:
        try:
            block = client._call("eth_getBlockByNumber", [tag, False]) or {}
            numbers[tag] = _hex_int(block.get("number"))
        except Exception:
            numbers[tag] = None
    numbers["display"] = numbers.get(display)
    numbers["display_tag"] = display  # type: ignore[assignment]
    return numbers


def _observe_evm_unknown(row: dict[str, Any], client: Any, spec: chains.ChainIdentity, tags: dict[str, Any]) -> dict[str, Any] | None:
    """An unknown EVM transfer with no receipt: read what the exits need (the account's counts, the base fee, the
    transaction by hash) and record the exit the evidence supports. Nothing is released here."""
    proposal_id, tx_id = str(row["proposal_id"]), str(row.get("tx_id") or "")
    from_address = str(row["from_address"])
    nonce = int(row.get("nonce") if row.get("nonce") is not None else -1)
    pending_count = _hex_int(client._call("eth_getTransactionCount", [from_address, "pending"]))
    latest_count = _hex_int(client._call("eth_getTransactionCount", [from_address, "latest"]))
    latest_block = client._call("eth_getBlockByNumber", ["latest", False]) or {}
    base_fee, latest_number = _hex_int(latest_block.get("baseFeePerGas")), _hex_int(latest_block.get("number"))
    by_hash = client._call("eth_getTransactionByHash", [tx_id])
    params = _quote_params(row)
    max_fee = int(params.get("max_fee_per_gas") or 0)
    offered = _offered(row)
    columns: dict[str, Any] = {}
    evidence_kind = str(row.get("evidence_kind") or "")
    if pending_count is None or latest_count is None:
        columns["evidence_json"] = appended_evidence(row, "counts_unreadable")
    elif latest_count > nonce:
        # the account's slot was used: prove the crossing at a block the finalized tag reaches with an unchanged hash
        crossing_block, crossing_hash = row.get("crossing_block"), str(row.get("crossing_block_hash") or "")
        if crossing_block is None and latest_number is not None:
            columns.update(crossing_block=latest_number, crossing_block_hash=str(latest_block.get("hash") or ""))
            columns["evidence_json"] = appended_evidence(row, "crossing_seen", latest_count=latest_count, block=latest_number)
        else:
            block = client._call("eth_getBlockByNumber", [hex(int(crossing_block)), False]) or {}
            finalized = tags.get(chains.FINALITY_FINALIZED_TAG)
            if str(block.get("hash") or "").lower() != crossing_hash.lower():
                columns.update(crossing_block=None, crossing_block_hash=None)
                columns["evidence_json"] = appended_evidence(row, "crossing_reorged", block=crossing_block)
            elif finalized is not None and finalized >= int(crossing_block) and by_hash is None:
                offered = {"exit": EXIT_SLOT_USED, "observed_at": clock(), "crossing_block": int(crossing_block), "finalized": finalized, "latest_count": latest_count}
                columns["evidence_json"] = appended_evidence(row, "crossing_final", block=crossing_block, finalized=finalized)
            else:
                columns["evidence_json"] = appended_evidence(row, "crossing_waiting", block=crossing_block, finalized=finalized)
    elif pending_count == latest_count == nonce and by_hash is None:
        if evidence_kind == "refused":
            offered = {"exit": EXIT_REFUSED, "observed_at": clock(), "answer": evidence_kind}
            columns["evidence_json"] = appended_evidence(row, "not_pooled", answer=evidence_kind)
        elif base_fee is not None and max_fee and base_fee > max_fee:
            entries = [e for e in json.loads(row.get("evidence_json") or "[]") if isinstance(e, dict) and e.get("kind") == "base_fee_above_ceiling"]
            first_seen = min((float(e.get("at") or clock()) for e in entries), default=clock())
            columns["evidence_json"] = appended_evidence(row, "base_fee_above_ceiling", base_fee=base_fee, max_fee=max_fee)
            if len(entries) + 1 >= FEE_SPIKE_PASSES and clock() - first_seen >= FEE_SPIKE_SPAN_SECONDS:
                offered = {"exit": EXIT_REFUSED, "observed_at": clock(), "answer": "base_fee_above_ceiling"}
        else:
            offered = {"exit": EXIT_RESEND, "observed_at": clock(), "pending_count": pending_count, "base_fee": base_fee, "max_fee": max_fee}
            columns["evidence_json"] = appended_evidence(row, "dropped_from_pool", pending_count=pending_count, base_fee=base_fee)
    else:
        offered = {}
        columns["evidence_json"] = appended_evidence(row, "still_pooled" if by_hash is not None else "counts_ahead", pending_count=pending_count, latest_count=latest_count)
    columns["offered_exit_json"] = json.dumps(offered, sort_keys=True, default=str)
    with connection() as conn:
        transfers.update_columns(conn, proposal_id, **columns)
    return transfers.get_transfer_by_id(proposal_id)


def _observe_evm(row: dict[str, Any], client: Any) -> dict[str, Any] | None:
    proposal_id, tx_id, state = str(row["proposal_id"]), str(row.get("tx_id") or ""), str(row["state"])
    spec = chains.resolve_network(row["network"])
    receipt = client._call("eth_getTransactionReceipt", [tx_id])
    tags = _evm_tags(client, spec)
    if not receipt:
        if state == transfers.STATE_PENDING:
            nulls = _null_reads(row) + 1
            evidence = appended_evidence(row, "status_null")
            if nulls >= 2:
                return apply_transition(proposal_id, transfers.STATE_UNKNOWN, state, evidence_json=evidence, evidence_kind="no_answer")
            with connection() as conn:
                transfers.update_columns(conn, proposal_id, evidence_json=evidence)
            return transfers.get_transfer_by_id(proposal_id)
        if state == transfers.STATE_UNKNOWN:
            return _observe_evm_unknown(row, client, spec, tags)
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, "receipt_null"))
        return transfers.get_transfer_by_id(proposal_id)
    number = _hex_int(receipt.get("blockNumber"))
    receipt_hash = str(receipt.get("blockHash") or "").lower()
    if number is None or not receipt_hash:
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, "receipt_malformed"))
        return transfers.get_transfer_by_id(proposal_id)
    canonical = client._call("eth_getBlockByNumber", [hex(number), False]) or {}
    if str(canonical.get("hash") or "").lower() != receipt_hash:
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, "receipt_not_canonical", block=number))
        return transfers.get_transfer_by_id(proposal_id)
    display_number, finalized_number, display_tag = tags.get("display"), tags.get(chains.FINALITY_FINALIZED_TAG), str(tags.get("display_tag"))
    succeeded = str(receipt.get("status") or "").lower() == "0x1"
    fee_evidence = evm_fee_evidence(receipt, spec, block=canonical)  # the header just proven canonical carries the fork
    fee = fee_evidence.total
    if state in (transfers.STATE_CONFIRMED, transfers.STATE_FAILED_ON_CHAIN):
        # a settled row is looked at again only to refine a bounded fee from complete evidence
        return _refine_fee(row, fee_evidence)
    fee_columns = _fee_columns(fee_evidence)
    evidence = appended_evidence(row, "receipt", block=number, status=receipt.get("status"), display_tag=display_tag, display_number=display_number, finalized_number=finalized_number,
                                 fee=fee, fee_known=int(fee_evidence.known_minor), fee_missing=list(fee_evidence.missing), fee_fork=fee_evidence.fork, fork_note=fee_evidence.note)
    if succeeded:
        if display_number is not None and number <= display_number:
            if state == transfers.STATE_CONFIRMED:
                return row
            balance_after = _hex_int(client._call("eth_getBalance", [str(row["from_address"]), "latest"]))
            columns: dict[str, Any] = {"block_ref": number, "block_hash": receipt_hash, "finality_seen": display_tag, "evidence_json": evidence, "offered_exit_json": "{}", **fee_columns}
            if balance_after is not None:
                columns["balance_after_minor"] = balance_after
            return apply_transition(proposal_id, transfers.STATE_CONFIRMED, state, charged_fee_minor=fee, **columns)
        if state in (transfers.STATE_UNKNOWN, transfers.STATE_DISPATCHING):
            return apply_transition(proposal_id, transfers.STATE_PENDING, state, block_ref=number, block_hash=receipt_hash, evidence_json=evidence, offered_exit_json="{}")
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, block_ref=number, block_hash=receipt_hash, evidence_json=evidence, offered_exit_json="{}")
        return transfers.get_transfer_by_id(proposal_id)
    if finalized_number is not None and number <= finalized_number:
        if state in (transfers.STATE_PENDING, transfers.STATE_UNKNOWN, transfers.STATE_STOPPED_WAITING):
            return apply_transition(proposal_id, transfers.STATE_FAILED_ON_CHAIN, state, charged_fee_minor=fee, block_ref=number, block_hash=receipt_hash,
                                    finality_seen=chains.FINALITY_FINALIZED_TAG, evidence_json=evidence, offered_exit_json="{}", **fee_columns)
        return row
    if state in (transfers.STATE_UNKNOWN, transfers.STATE_DISPATCHING):
        return apply_transition(proposal_id, transfers.STATE_PENDING, state, block_ref=number, block_hash=receipt_hash, evidence_json=appended_evidence(row, "receipt", block=number, status=receipt.get("status"), note="failed on chain, awaiting finality"), offered_exit_json="{}")
    with connection() as conn:
        transfers.update_columns(conn, proposal_id, block_ref=number, block_hash=receipt_hash, evidence_json=evidence)
    return transfers.get_transfer_by_id(proposal_id)


def _epochs_changed(row: dict[str, Any]) -> bool:
    from core.wallet import controls

    with connection() as conn:
        epochs = controls.epochs(conn)
    return (epochs["freeze"], epochs["enabled"], epochs["environment"]) != (int(row["epoch_freeze"]), int(row["epoch_enabled"]), int(row["epoch_environment"]))


def recover_once(row: dict[str, Any], *, rpc: Any = None) -> dict[str, Any] | None:
    """The recovery rows: a claim whose request died, a signed transfer past its deadline or behind a control change,
    a revoked Solana transfer whose blockhash expired, a dispatch whose answer never came, and a cancel request whose
    thread is gone. The cancel flag wins over every release. No transmit happens here."""
    proposal_id, state, now = str(row["proposal_id"]), str(row["state"]), clock()
    lease_expired = float(row.get("lease_until") or 0) < now
    if row.get("cancel_requested_at") and state in (transfers.STATE_CLAIMED, transfers.STATE_SIGNED, transfers.STATE_SIGNED_REVOKED):
        return finalize_cancel(proposal_id) if lease_expired else row
    if state == transfers.STATE_CLAIMED:
        return release_claimed(proposal_id, reason="approving_request_lost") if lease_expired else row
    if state == transfers.STATE_SIGNED:
        if now > float(row.get("dispatch_deadline") or 0):
            return revoke_signed(proposal_id, reason="dispatch_deadline_passed")
        if _epochs_changed(row):
            return revoke_signed(proposal_id, reason="controls_changed")
        return row
    if state == transfers.STATE_SIGNED_REVOKED and str(row.get("family")) == chains.FAMILY_SVM:
        client = _svm_client(row, rpc)
        with contextlib.suppress(Exception):
            height = int(client._call("getBlockHeight", [{"commitment": "confirmed"}]))
            if height > int(row.get("last_valid_block_height") or 0):
                # the signed bytes can never be included any more: the authorization is worthless, the hold returns
                return apply_transition(proposal_id, transfers.STATE_RELEASED, state, detail={"why": "blockhash_expired_never_sent"},
                                        evidence_json=appended_evidence(row, "blockhash_expired", height=height))
        return row
    if state == transfers.STATE_SIGNED_REVOKED and str(row.get("family")) == chains.FAMILY_EVM:
        client = _evm_client(row, rpc)
        with contextlib.suppress(Exception):
            latest_count = _hex_int(client._call("eth_getTransactionCount", [str(row["from_address"]), "latest"]))
            if latest_count is not None and latest_count > int(row.get("nonce") if row.get("nonce") is not None else -1):
                # the account's slot was used by another transaction: the signed bytes can never land
                return apply_transition(proposal_id, transfers.STATE_RELEASED, state, detail={"why": "nonce_used_elsewhere_never_sent"},
                                        evidence_json=appended_evidence(row, "nonce_used_elsewhere", latest_count=latest_count))
        return row
    if state == transfers.STATE_DISPATCHING and now > float(row.get("dispatch_deadline") or 0) + SEND_TIMEOUT_SECONDS:
        return apply_transition(proposal_id, transfers.STATE_UNKNOWN, state, evidence_kind="no_answer", evidence_json=appended_evidence(row, "send_answer_lost"))
    return row


def observe_once(proposal_id: str, *, rpc: Any = None) -> dict[str, Any] | None:
    """One recovery-and-observation pass for one transfer. Returns the read-model view."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None:
        return None
    row = recover_once(row, rpc=rpc) or transfers.get_transfer_by_id(proposal_id)
    if row is None:
        return None
    state = str(row["state"])
    observable = state in (transfers.STATE_UNKNOWN, transfers.STATE_PENDING) or (
        state == transfers.STATE_STOPPED_WAITING and float(row.get("observe_until") or 0) > clock())
    # a settled EVM row with a bounded fee stays observable within its horizon, for the refinement alone
    refining = (state in (transfers.STATE_CONFIRMED, transfers.STATE_FAILED_ON_CHAIN) and str(row.get("fee_state") or "") == transfers.FEE_BOUNDED
                and float(row.get("observe_until") or 0) > clock() and str(row.get("family")) == chains.FAMILY_EVM)
    if (observable or refining) and row.get("tx_id"):
        try:
            if str(row.get("family")) == chains.FAMILY_SVM:
                row = _observe_svm(row, _svm_client(row, rpc)) or row
            else:
                row = _observe_evm(row, _evm_client(row, rpc)) or row
        except Exception as exc:  # the node did not answer well: recorded, nothing moves
            with connection() as conn:
                transfers.update_columns(conn, proposal_id, evidence_json=appended_evidence(row, "read_failed", error=type(exc).__name__))
            row = transfers.get_transfer_by_id(proposal_id) or row
    elif str(row["state"]) in transfers.TERMINAL_STATES and not row.get("a6_resolved_at"):
        done, note = resolve_a6(proposal_id, target=str(row["state"]), dispatch_cas_happened=str(row["state"]) not in transfers.NEVER_SENT_STATES, tx_id=str(row.get("tx_id") or ""))
        with connection() as conn:
            transfers.update_columns(conn, proposal_id, a6_outcome=note, a6_resolved_at=utcnow() if done else None)
    return transfers.view_of(transfers.get_transfer_by_id(proposal_id) or row)


def observe_bounded(proposal_id: str, *, rpc: Any = None, budget_seconds: float = 20.0, poll_seconds: float = 0.25) -> dict[str, Any] | None:
    """Poll one transfer until it settles or the budget ends; the in-request half of observation."""
    deadline = time.monotonic() + max(0.0, float(budget_seconds))
    view = observe_once(proposal_id, rpc=rpc)
    while view is not None and view["state"] in (transfers.STATE_UNKNOWN, transfers.STATE_PENDING, transfers.STATE_DISPATCHING) and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        view = observe_once(proposal_id, rpc=rpc)
    return view


def select_observable(conn: Any, *, limit: int = 50) -> list[str]:
    """Rows a pass must look at: live rows, stopped rows within their horizon, endings whose effect record is not
    resolved yet, and cancel requests still waiting. Read only."""
    now = clock()
    placeholders = ", ".join("?" for _ in transfers.LIVE_STATES)
    rows = conn.execute(
        f"SELECT proposal_id FROM wallet_transfers WHERE state IN ({placeholders}) OR (state = ? AND COALESCE(observe_until, 0) > ?) "
        "OR (fee_state = ? AND state IN (?, ?) AND COALESCE(observe_until, 0) > ?) "
        "OR a6_resolved_at IS NULL OR (cancel_requested_at IS NOT NULL AND state IN (?, ?, ?)) ORDER BY updated_at ASC LIMIT ?",
        (*transfers.LIVE_STATES, transfers.STATE_STOPPED_WAITING, now, transfers.FEE_BOUNDED, transfers.STATE_CONFIRMED, transfers.STATE_FAILED_ON_CHAIN, now,
         transfers.STATE_CLAIMED, transfers.STATE_SIGNED, transfers.STATE_SIGNED_REVOKED, int(limit)),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _take_observe_lease(proposal_id: str) -> str | None:
    return transfers.take_marker(f"observe:{proposal_id}", ttl_seconds=_OBSERVE_LEASE_SECONDS)


def observe_open_transfers(*, limit: int = 50, rpc: Any = None, should_stop: Any = None) -> int:
    """One observer pass over every observable row, each under its own lease. Returns how many rows were looked at.

    ``should_stop`` is the daemon's shutdown door: checked between rows so a stop takes
    effect within ONE row instead of one whole pass. A pass may hold up to
    PASS_RPC_BUDGET_SECONDS of in-flight per-row work, and the observer's host (the API
    server's ``main``) joins it for 5s at shutdown — without this door a stopped observer
    could keep opening the wallet store against databases switched underneath it for a
    whole pass past the join.
    """
    with connection() as conn:
        ids = select_observable(conn, limit=limit)
    started = time.monotonic()
    looked = 0
    for proposal_id in ids:
        if time.monotonic() - started > PASS_RPC_BUDGET_SECONDS:
            break
        if should_stop is not None and should_stop():
            break
        token = _take_observe_lease(proposal_id)
        if token is None:
            continue
        try:
            observe_once(proposal_id, rpc=rpc)
            looked += 1
        except Exception:  # one bad row never stops the pass
            logger.exception("wallet transfer observation failed for %s", proposal_id)
        finally:
            transfers.release_marker(f"observe:{proposal_id}", token)
    return looked


# --- the owner exits (design v5 §6): each door re-runs its predicate under the row's lease, then acts once ----------

class ExitUnavailableError(Exception):
    """The exit the owner asked for is not the one the fresh evidence supports."""


def _fresh_offer(row: dict[str, Any]) -> str:
    """Re-run the observation for this row now and return the exit it offers after that pass."""
    current = observe_once(str(row["proposal_id"])) or {}
    return str(current.get("offered_exit") or "")


def owner_stop_waiting(proposal_id: str, *, decision_note: str = "") -> dict[str, Any]:
    """D7: the owner stops waiting on an uncertain transfer. The hold settles as spent and unconfirmed (it keeps counting
    in its daily window), the account's slot is free for a new transfer, observation continues for the horizon, and a
    receipt records the owner's decision with the evidence the exit rested on."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None or str(row["state"]) not in (transfers.STATE_UNKNOWN, transfers.STATE_PENDING):
        raise ExitUnavailableError("not_an_uncertain_transfer")
    token = _take_observe_lease(proposal_id)
    if token is None:
        raise ExitUnavailableError("row_busy")
    try:
        offered = _fresh_offer(row)
        if offered not in (EXIT_STOP_WAITING, EXIT_SLOT_USED, EXIT_REFUSED):
            raise ExitUnavailableError(f"exit_no_longer_available:{offered or 'none'}")
        row = transfers.get_transfer_by_id(proposal_id) or row
        now = clock()
        moved = apply_transition(
            proposal_id, transfers.STATE_STOPPED_WAITING, str(row["state"]), detail={"why": "owner_stopped_waiting", "exit": offered, "note": str(decision_note or "")[:200]},
            stopped_waiting_at=now, observe_until=now + OBSERVE_HORIZON_SECONDS,
            evidence_json=appended_evidence(row, "owner_stopped_waiting", exit=offered, offered=_offered(row)),
        )
        if moved is None:
            raise ExitUnavailableError("transfer_moved_meanwhile")
        return transfers.view_of(moved)
    finally:
        transfers.release_marker(f"observe:{proposal_id}", token)


def owner_discard(proposal_id: str) -> dict[str, Any]:
    """A signed transfer that never left and can no longer be sent: dropped, its hold released (the effect record first)."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None or str(row["state"]) != transfers.STATE_SIGNED_REVOKED or row.get("dispatch_claimed_at") is not None or int(row.get("attempts_sent") or 0) > 0:
        raise ExitUnavailableError("not_a_discardable_transfer")
    token = _take_observe_lease(proposal_id)
    if token is None:
        raise ExitUnavailableError("row_busy")
    try:
        moved = apply_transition(proposal_id, transfers.STATE_DISCARDED, transfers.STATE_SIGNED_REVOKED, detail={"why": "owner_discarded_never_sent"})
        if moved is None:
            raise ExitUnavailableError("transfer_moved_meanwhile")
        return transfers.view_of(moved)
    finally:
        transfers.release_marker(f"observe:{proposal_id}", token)


def resend_available(proposal_id: str) -> str:
    """Re-run the EVM observation and return the reason the resend is not available, or '' when it is."""
    row = transfers.get_transfer_by_id(proposal_id)
    if row is None or str(row["state"]) != transfers.STATE_UNKNOWN or str(row.get("family")) != chains.FAMILY_EVM:
        return "not_an_unknown_evm_transfer"
    if not row.get("raw_b64"):
        return "signed_bytes_not_kept"
    offered = _fresh_offer(row)
    return "" if offered == EXIT_RESEND else f"exit_no_longer_available:{offered or 'none'}"


# --- the daemon observer ----------------------------------------------------------------------------------

class _Observer:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.thread: threading.Thread | None = None
        self.stopped = False
        self.passes = 0
        self.wake_requests = 0

    def wake(self) -> None:
        with self.condition:
            self.wake_requests += 1
            self.condition.notify_all()

    def stop_requested(self) -> bool:
        with self.condition:
            return self.stopped

    def _loop(self) -> None:
        while True:
            with self.condition:
                if self.stopped:
                    return
            try:
                looked = observe_open_transfers(should_stop=self.stop_requested)
            except Exception:
                logger.exception("wallet transfer observer pass failed")
                looked = 0
            with self.condition:
                self.passes += 1
                if self.stopped:
                    return
                waited = self.wake_requests
                self.condition.wait(OBSERVE_TICK_SECONDS if looked else IDLE_TICK_SECONDS)
                if self.wake_requests != waited:
                    continue


_observer = _Observer()


def start_observer() -> bool:
    """Start the daemon thread once; safe to call from any host process. Never raises into boot."""
    with _observer.condition:
        if _observer.thread is not None and _observer.thread.is_alive():
            return False
        _observer.stopped = False
        _observer.thread = threading.Thread(target=_observer._loop, name="vool-wallet-transfer-observer", daemon=True)
        _observer.thread.start()
    return True


def stop_observer() -> None:
    with _observer.condition:
        _observer.stopped = True
        _observer.condition.notify_all()
    thread = _observer.thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5.0)


def wake_observer() -> bool:
    """The refresh door: a pass runs now if the observer is up (True), otherwise nothing changes."""
    with _observer.condition:
        alive = _observer.thread is not None and _observer.thread.is_alive()
    if alive:
        _observer.wake()
    return alive


def observer_state() -> dict[str, Any]:
    with _observer.condition:
        return {"alive": bool(_observer.thread is not None and _observer.thread.is_alive()), "passes": _observer.passes}


def new_owner_token() -> str:
    return uuid.uuid4().hex


__all__ = [
    "APPROVAL_MARKER_SECONDS", "DISPATCH_DEADLINE_SECONDS", "EXIT_STOP_WAITING", "OBSERVE_HORIZON_SECONDS", "SEND_TIMEOUT_SECONDS",
    "SIGNER_BUDGET_SECONDS", "SVM_CLAIM_MARGIN_BLOCKS", "SVM_SEND_MARGIN_BLOCKS", "appended_evidence", "apply_transition", "control_baseline",
    "finalize_cancel", "new_owner_token", "observe_bounded", "observe_once", "observe_open_transfers", "observer_state", "record_evidence",
    "release_claimed", "request_cancel", "resolve_a6", "revoke_signed", "select_observable", "start_observer", "stop_observer", "wake_observer",
]
