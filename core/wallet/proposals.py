"""Typed transaction proposals: what skills, plugins, models and users may ask for.

This module is the whole surface a proposer gets: create a proposal, read it back. It cannot
approve, sign, or reach a key -- those live behind the owner-local lifecycle and the signer
door, and nothing here imports them.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.wallet import chains, custody, idempotency
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault
from core.wallet.store import connection, dumps, loads, utcnow

AUTHORITY = "core.wallet.proposals"

ORIGIN_USER = "user"
ORIGIN_MODEL = "model"
ORIGIN_SKILL = "skill"
ORIGIN_PLUGIN = "plugin"
ORIGIN_X402 = "x402"
ORIGIN_USEPOD = "usepod"
#: A collection of accrued DNA service fees to the designated treasury (core.wallet.dna_fees): minted only by the
#: runtime from the fee ledger's own position, approved on its own sheet, never proposed by a model, skill or plugin.
ORIGIN_DNA_FEE = "dna_fee"
ORIGINS = (ORIGIN_USER, ORIGIN_MODEL, ORIGIN_SKILL, ORIGIN_PLUGIN, ORIGIN_X402, ORIGIN_USEPOD, ORIGIN_DNA_FEE)

STATE_PROPOSED = "proposed"
STATE_SIMULATED = "simulated"
STATE_LIMITS_CHECKED = "limits_checked"
STATE_PENDING_APPROVAL = "pending_approval"
STATE_APPROVED = "approved"
STATE_AWAITING_SIGNATURE = "awaiting_signature"
STATE_SIGNED = "signed"
STATE_BROADCAST = "broadcast"
STATE_CONFIRMED = "confirmed"
STATE_REJECTED = "rejected"
STATE_FAILED = "failed"
STATE_EXPIRED = "expired"
TERMINAL_STATES = frozenset({STATE_CONFIRMED, STATE_REJECTED, STATE_FAILED, STATE_EXPIRED})
#: Terminal states that refused the payment (everything terminal except CONFIRMED): the
#: idempotency key of a proposal in one of these may be lawfully re-used by a fresh proposal.
TERMINAL_REFUSAL_STATES = frozenset({STATE_REJECTED, STATE_FAILED, STATE_EXPIRED})

TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATE_PROPOSED: (STATE_SIMULATED, STATE_FAILED, STATE_REJECTED, STATE_EXPIRED),
    STATE_SIMULATED: (STATE_LIMITS_CHECKED, STATE_REJECTED, STATE_FAILED),
    STATE_LIMITS_CHECKED: (STATE_PENDING_APPROVAL, STATE_REJECTED),
    STATE_PENDING_APPROVAL: (STATE_APPROVED, STATE_REJECTED, STATE_EXPIRED, STATE_FAILED),
    STATE_APPROVED: (STATE_SIGNED, STATE_AWAITING_SIGNATURE, STATE_FAILED, STATE_REJECTED),
    STATE_AWAITING_SIGNATURE: (STATE_SIGNED, STATE_FAILED, STATE_REJECTED, STATE_EXPIRED),
    STATE_SIGNED: (STATE_BROADCAST, STATE_FAILED),
    STATE_BROADCAST: (STATE_CONFIRMED, STATE_FAILED),
}

#: Edges only the pilot transfer store takes, through :func:`_transition` in its own transaction. The legacy
#: :func:`transition` never accepts them: a cancel after signing, before anything was sent.
PILOT_ONLY_EDGES: dict[str, tuple[str, ...]] = {STATE_SIGNED: (STATE_REJECTED,)}
_PILOT_EDGES: dict[str, tuple[str, ...]] = {state: TRANSITIONS.get(state, ()) + PILOT_ONLY_EDGES.get(state, ()) for state in {*TRANSITIONS, *PILOT_ONLY_EDGES}}

_MAX_MEMO = 200
MAX_MEMO = _MAX_MEMO
_COLS = "proposal_id, wallet_id, network, asset, amount_minor, destination, memo, origin, idempotency_key, state, simulation_json, tx_signature, fault_code, created_at, updated_at, recipient_json"

#: the Contacts snapshot fields a proposal keeps: who the destination was resolved to, never a substitute for it
RECIPIENT_FIELDS: tuple[str, ...] = (
    "version", "resolution", "matched_by", "contact_id", "contact_revision", "display_name", "endpoint_id", "endpoint_revision", "kind", "label", "value",
    "canonical", "chain_network", "network_display", "verification", "source", "fingerprint", "saved_matches", "lookalikes", "warning",
)


@dataclass(frozen=True)
class TransactionProposal:
    proposal_id: str
    wallet_id: str
    network: str
    asset: str
    amount_minor: int
    destination: str
    memo: str
    origin: str
    idempotency_key: str
    state: str
    created_at: str
    updated_at: str
    simulation: dict[str, Any] = field(default_factory=dict)
    tx_signature: str = ""
    fault_code: str = ""
    #: the Contacts resolution the destination came from ({} when the caller named no recipient snapshot)
    recipient: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "wallet_id": self.wallet_id, "network": self.network, "asset": self.asset,
            "amount_minor": self.amount_minor, "destination": self.destination, "memo": self.memo, "origin": self.origin,
            "idempotency_key": self.idempotency_key, "state": self.state, "created_at": self.created_at, "updated_at": self.updated_at,
            "simulation": dict(self.simulation), "tx_signature": self.tx_signature, "fault_code": self.fault_code, "recipient": dict(self.recipient),
        }


def _row(row: Any) -> TransactionProposal:
    return TransactionProposal(
        proposal_id=row[0], wallet_id=row[1], network=row[2], asset=row[3], amount_minor=int(row[4]), destination=row[5], memo=row[6],
        origin=row[7], idempotency_key=row[8], state=row[9], created_at=row[13], updated_at=row[14], simulation=loads(row[10], {}),
        tx_signature=row[11] or "", fault_code=row[12] or "", recipient=loads(row[15], {}) if row[15] else {},
    )


def _get(conn: Any, proposal_id: str) -> TransactionProposal | None:
    """:func:`get_proposal` on the caller's connection (reads inside a transfer transaction see its own writes)."""
    row = conn.execute(f"SELECT {_COLS} FROM wallet_proposals WHERE proposal_id = ?", (str(proposal_id or ""),)).fetchone()
    return _row(row) if row else None


def get_proposal(proposal_id: str) -> TransactionProposal | None:
    with connection() as conn:
        return _get(conn, proposal_id)


def proposal_events(proposal_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT state, detail_json, created_at FROM wallet_proposal_events WHERE proposal_id = ? ORDER BY id ASC", (str(proposal_id),)).fetchall()
    return [{"state": r[0], "detail": loads(r[1], {}), "created_at": r[2]} for r in rows]


def list_proposals(*, state: str = "", limit: int = 50) -> list[TransactionProposal]:
    with connection() as conn:
        if state:
            rows = conn.execute(f"SELECT {_COLS} FROM wallet_proposals WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, int(limit))).fetchall()
        else:
            rows = conn.execute(f"SELECT {_COLS} FROM wallet_proposals ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [_row(r) for r in rows]


def propose_transaction(*, wallet_id: str, destination: str, amount_minor: int, asset: str, origin: str, memo: str = "", idempotency_key: str = "", source_context: dict[str, Any] | None = None, network: str = "", recipient: dict[str, Any] | None = None) -> TransactionProposal:
    """Mint a typed proposal in state ``proposed``. Idempotent under ``idempotency_key``.

    ``network`` is the chain-qualified identity the payment rides (CAIP-2 or a declared
    legacy name); it must resolve to the SAME chain the wallet account lives on — an
    account/network switch between the account and the proposal is refused here, before
    any simulation, approval or key exists.

    ``recipient`` is the Contacts snapshot the destination was resolved from. It is kept beside the destination (the
    quote binds it, approval re-checks it); it must name exactly this destination, and a saved contact's address must
    have been saved for this very network."""
    custody.require_enabled(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    requested = str(network or "").strip() or profile.network
    resolved_network = custody.require_network(requested, source_context=source_context)
    from core.wallet import environment

    environment.require_active(resolved_network, source_context=source_context)
    if custody.seal_policy(profile.wallet_id) == custody.PILOT_SEAL_POLICY and custody.setup_state(profile.wallet_id) not in {"", "ready"}:
        # "I've saved my backup" finishes setup; until then a pilot wallet cannot be asked to send
        raise wallet_fault("wallet_setup_state_invalid", authority=AUTHORITY, context={"wallet_id": profile.wallet_id, "reason": "setup_not_finished"}, source_context=source_context)
    if _canonical_network(profile.network) != _canonical_network(resolved_network):
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "account_network_mismatch", "wallet_network": _canonical_network(profile.network), "proposal_network": _canonical_network(resolved_network)}, source_context=source_context)
    if origin not in ORIGINS:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "unknown_origin", "origin": str(origin)}, source_context=source_context)
    dest = str(destination or "").strip()
    if not chains.destination_matches_family(resolved_network, dest):
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "invalid_destination"}, source_context=source_context)
    recipient_record: dict[str, Any] = {key: recipient[key] for key in RECIPIENT_FIELDS if key in recipient} if isinstance(recipient, dict) else {}
    if recipient_record:
        if str(recipient_record.get("value") or "").strip() != dest:
            raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={"reason": "recipient_names_another_destination"}, source_context=source_context)
        saved_network = str(recipient_record.get("chain_network") or "")
        if recipient_record.get("resolution") == "contact" and _canonical_network(saved_network) != _canonical_network(resolved_network):
            # the same-looking address saved for another network is not this payment's destination
            raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={"reason": "recipient_saved_for_another_network", "saved_network": saved_network, "proposal_network": _canonical_network(resolved_network)}, source_context=source_context)
    try:
        amount = int(amount_minor)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY, context={"limit": "non_positive_amount", "reason": "amount_must_be_positive"}, source_context=source_context)
    from core.wallet import amounts

    if amount > amounts.INT64_MAX:
        # the amount columns are signed 64-bit; a larger native-unit amount is refused before any write
        raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY, context={"limit": "storage", "reason": "amount_exceeds_storage_limit", "maximum_minor": str(amounts.INT64_MAX)}, source_context=source_context)
    asset_name = _validated_asset(resolved_network, asset, source_context=source_context)
    _require_token_lane(resolved_network, asset_name, origin=str(origin), source_context=source_context)
    clean_memo = str(memo or "")[:_MAX_MEMO]
    key = str(idempotency_key or "").strip()[:128]
    digest = idempotency.content_digest(wallet_id=profile.wallet_id, destination=dest, amount_minor=amount, asset=asset_name, memo=clean_memo, network=resolved_network)

    existing = idempotency.existing_for_key(profile.wallet_id, key)
    supersede_id = ""
    if existing is not None:
        if existing["content_digest"] != digest:
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"idempotency_key": key, "reason": "same_key_different_content", "proposal_id": existing["proposal_id"]}, source_context=source_context)
        found = get_proposal(existing["proposal_id"])
        if found is not None and found.state in TERMINAL_REFUSAL_STATES:
            # a terminal REFUSAL is not a payment: the same lawful request may be proposed
            # again. The dead row keeps its audit trail under a superseded key.
            supersede_id = found.proposal_id
        elif found is not None:
            return found

    from core.wallet import transfers

    if transfers.is_pilot_transfer_parts(wallet_id=profile.wallet_id, network=resolved_network, asset=asset_name):
        open_count = transfers.open_pilot_proposals(profile.wallet_id, resolved_network)
        if open_count >= transfers.MAX_OPEN_PILOT_PROPOSALS:
            raise wallet_fault(
                "wallet_limit_exceeded", authority=AUTHORITY,
                context={"limit": "open_pilot_proposals", "reason": "too_many_open_pilot_proposals", "open": open_count, "maximum": transfers.MAX_OPEN_PILOT_PROPOSALS},
                source_context=source_context,
            )
    now = utcnow()
    proposal_id = f"pay-{uuid.uuid4().hex[:20]}"
    try:
        with connection() as conn:
            if supersede_id:
                conn.execute(
                    "UPDATE wallet_proposals SET idempotency_key = ?, updated_at = ? WHERE proposal_id = ?",
                    (f"{key}#superseded-{uuid.uuid4().hex[:8]}", now, supersede_id),
                )
            conn.execute(
                "INSERT INTO wallet_proposals (proposal_id, wallet_id, network, asset, amount_minor, destination, memo, origin, idempotency_key, content_digest, state, created_at, updated_at, recipient_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (proposal_id, profile.wallet_id, resolved_network, asset_name, amount, dest, clean_memo, origin, key, digest, STATE_PROPOSED, now, now,
                 dumps(recipient_record) if recipient_record else ""),
            )
            conn.execute("INSERT INTO wallet_proposal_events (proposal_id, state, detail_json, created_at) VALUES (?, ?, ?, ?)", (proposal_id, STATE_PROPOSED, dumps({"origin": origin}), now))
    except sqlite3.IntegrityError:
        # a concurrent proposer won the unique index: converge on their proposal
        existing = idempotency.existing_for_key(profile.wallet_id, key)
        if existing is not None and existing["content_digest"] == digest:
            found = get_proposal(existing["proposal_id"])
            if found is not None:
                return found
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"idempotency_key": key, "reason": "concurrent_conflict"}, source_context=source_context) from None
    return get_proposal(proposal_id)  # type: ignore[return-value]


def _canonical_network(name: str) -> str:
    from core.wallet import chains

    return chains.resolve_network(name).network


def _validated_asset(network: str, asset: str, *, source_context: dict[str, Any] | None) -> str:
    """The asset must be a registered row of this chain; the stored form is the row's
    symbol. A native asset defaults in; an unknown or cross-chain asset refuses."""
    from core.wallet import chains

    spec = chains.resolve_network(network)
    if spec.is_svm and not str(asset or "").strip():
        return "SOL"
    if spec.is_evm and not str(asset or "").strip():
        native = next((a.symbol for a in spec.assets.values() if a.native), "")
        return native
    try:
        # asset_for owns the casing rules (symbols case-insensitively, addresses and mints
        # exact): uppercasing here would corrupt an 0x contract or a base58 mint
        return chains.asset_for(network, str(asset or "").strip()).symbol
    except WalletFault as exc:
        raise wallet_fault(exc.code, authority=AUTHORITY, context=dict(exc.context), source_context=source_context) from None


def _require_token_lane(network: str, asset: str, *, origin: str, source_context: dict[str, Any] | None) -> None:
    """A token on a Mainnet row moves only for the UsePod x402 payment lane; the pilot's own transfers there stay native
    coins. Test-network rows keep their registered tokens for every origin (the existing x402 lane pays with them)."""
    from core.wallet import chains

    spec = chains.resolve_network(network)
    if not spec.is_mainnet or origin in (ORIGIN_USEPOD, ORIGIN_DNA_FEE) or chains.asset_for(spec.network, asset).native:
        return
    raise wallet_fault(
        "wallet_network_disabled", authority=AUTHORITY,
        context={"network": spec.network, "asset": str(asset)[:16], "origin": origin[:16], "reason": "mainnet_tokens_move_only_for_usepod_x402"},
        source_context=source_context,
    )


EVENT_APPROVAL_REFUSED = "approval_refused"


def record_approval_refusal(proposal_id: str, *, method: str, reason: str) -> int:
    """Append a refused-approval event WITHOUT moving the state; return the refusal count so far."""
    now = utcnow()
    with connection() as conn:
        conn.execute("INSERT INTO wallet_proposal_events (proposal_id, state, detail_json, created_at) VALUES (?, ?, ?, ?)", (str(proposal_id), EVENT_APPROVAL_REFUSED, dumps({"method": method, "reason": reason}), now))
        row = conn.execute("SELECT COUNT(*) FROM wallet_proposal_events WHERE proposal_id = ? AND state = ?", (str(proposal_id), EVENT_APPROVAL_REFUSED)).fetchone()
    return int(row[0] or 0)


def approval_refusals(proposal_id: str) -> int:
    with connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM wallet_proposal_events WHERE proposal_id = ? AND state = ?", (str(proposal_id), EVENT_APPROVAL_REFUSED)).fetchone()
    return int(row[0] or 0)


class ProposalTransitionError(Exception):
    """A proposal CAS refused: unknown proposal, another state, an edge that is not allowed, or a lost race."""


def _cas(conn: Any, proposal_id: str, new_state: str, *, expected_state: str | None, edges: dict[str, tuple[str, ...]], detail: dict[str, Any] | None, columns: dict[str, Any]) -> None:
    """The one proposal compare-and-set: state check, allowed edge, guarded UPDATE, event row. Raises on refusal."""
    now = utcnow()
    sets = ["state = ?", "updated_at = ?"]
    values: list[Any] = [new_state, now]
    if "simulation" in columns:
        sets.append("simulation_json = ?")
        values.append(dumps(columns["simulation"]))
    if "tx_signature" in columns:
        sets.append("tx_signature = ?")
        values.append(str(columns["tx_signature"]))
    if "fault_code" in columns:
        sets.append("fault_code = ?")
        values.append(str(columns["fault_code"]))
    current = conn.execute("SELECT state FROM wallet_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    if current is None:
        raise ProposalTransitionError(f"unknown_proposal:{proposal_id}")
    from_state = str(current[0])
    if expected_state is not None and from_state != expected_state:
        raise ProposalTransitionError(f"expected_{expected_state}_found_{from_state}")
    if new_state not in edges.get(from_state, ()):
        raise ProposalTransitionError(f"edge_not_allowed:{from_state}->{new_state}")
    cursor = conn.execute(f"UPDATE wallet_proposals SET {', '.join(sets)} WHERE proposal_id = ? AND state = ?", (*values, proposal_id, from_state))
    if cursor.rowcount != 1:
        raise ProposalTransitionError(f"lost_cas:{from_state}->{new_state}")
    conn.execute("INSERT INTO wallet_proposal_events (proposal_id, state, detail_json, created_at) VALUES (?, ?, ?, ?)", (proposal_id, new_state, dumps(detail or {}), now))


def _transition(conn: Any, proposal_id: str, new_state: str, *, expected_state: str, detail: dict[str, Any] | None = None, **columns: Any) -> None:
    """A proposal transition on the caller's connection (the pilot transfer store's projection). Raises
    ProposalTransitionError on refusal and also accepts :data:`PILOT_ONLY_EDGES`."""
    _cas(conn, proposal_id, new_state, expected_state=expected_state, edges=_PILOT_EDGES, detail=detail, columns=columns)


def transition(proposal_id: str, new_state: str, *, detail: dict[str, Any] | None = None, expected_state: str | None = None, **columns: Any) -> TransactionProposal | None:
    """Move a proposal along the typed state machine atomically (compare-and-set on the state).

    Returns the updated proposal, or None when the CAS lost (someone else moved it first).
    Package-internal: only the lifecycle calls this.
    """
    with connection() as conn:
        try:
            _cas(conn, proposal_id, new_state, expected_state=expected_state, edges=TRANSITIONS, detail=detail, columns=columns)
        except ProposalTransitionError:
            return None
    return get_proposal(proposal_id)


__all__ = [
    "EVENT_APPROVAL_REFUSED",
    "ORIGINS",
    "ORIGIN_MODEL",
    "ORIGIN_PLUGIN",
    "ORIGIN_SKILL",
    "ORIGIN_USER",
    "ORIGIN_X402",
    "STATE_APPROVED",
    "STATE_AWAITING_SIGNATURE",
    "STATE_BROADCAST",
    "STATE_CONFIRMED",
    "STATE_EXPIRED",
    "STATE_FAILED",
    "STATE_LIMITS_CHECKED",
    "STATE_PENDING_APPROVAL",
    "STATE_PROPOSED",
    "STATE_REJECTED",
    "STATE_SIMULATED",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "TransactionProposal",
    "approval_refusals",
    "get_proposal",
    "list_proposals",
    "proposal_events",
    "propose_transaction",
    "record_approval_refusal",
    "transition",
]
