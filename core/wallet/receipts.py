"""Effect receipts for payments: the wallet's own durable receipt, the Blackbox journal entry,
the turn's effect ledger, and the execution-truth facts the honesty gates read. Every record
passes :func:`redact_wallet_record` on the way out; the journal never sees a PIN or a phrase.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from core.wallet.redaction import publish_identifier, redact_wallet_record, vouch_digest_field
from core.wallet.store import connection, dumps, loads, utcnow

KIND_PAYMENT_INTENDED = "payment_intended"
KIND_PAYMENT_TERMINAL = "payment_terminal"
TOOL_NAME = "wallet.pay"


@dataclass(frozen=True)
class WalletReceipt:
    receipt_id: str
    proposal_id: str
    wallet_id: str
    network: str
    asset: str
    amount_minor: int
    destination: str
    origin: str
    state: str
    tx_signature: str
    fault_code: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id, "proposal_id": self.proposal_id, "wallet_id": self.wallet_id, "network": self.network,
            "asset": self.asset, "amount_minor": self.amount_minor, "destination": self.destination, "origin": self.origin,
            "state": self.state, "tx_signature": self.tx_signature, "fault_code": self.fault_code, "created_at": self.created_at,
        }


def record_receipt(proposal: Any, *, state: str, tx_signature: str = "", fault_code: str = "", extra: dict[str, Any] | None = None) -> WalletReceipt:
    with connection() as conn:
        return _record(conn, proposal, state=state, tx_signature=tx_signature, fault_code=fault_code, extra=extra)


def _record(conn: Any, proposal: Any, *, state: str, tx_signature: str = "", fault_code: str = "", extra: dict[str, Any] | None = None) -> WalletReceipt:
    """:func:`record_receipt` on the caller's connection, so a receipt commits with the transition it records."""
    receipt = WalletReceipt(
        receipt_id=f"wrcpt-{uuid.uuid4().hex[:16]}", proposal_id=proposal.proposal_id, wallet_id=proposal.wallet_id, network=proposal.network,
        asset=proposal.asset, amount_minor=int(proposal.amount_minor), destination=proposal.destination, origin=proposal.origin, state=state,
        tx_signature=str(tx_signature or ""), fault_code=str(fault_code or ""), created_at=utcnow(),
    )
    payload = redact_wallet_record({**receipt.to_dict(), **dict(extra or {})})
    conn.execute(
        "INSERT INTO wallet_receipts (receipt_id, proposal_id, wallet_id, network, asset, amount_minor, destination, origin, state, tx_signature, fault_code, created_at, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (receipt.receipt_id, receipt.proposal_id, receipt.wallet_id, receipt.network, receipt.asset, receipt.amount_minor, receipt.destination,
         receipt.origin, receipt.state, receipt.tx_signature, receipt.fault_code, receipt.created_at, dumps(payload)),
    )
    return receipt


def _revouch_stored_digests(payload: Any) -> Any:
    """Re-vouch the digest fields of a payload this store itself persisted. Everything here
    already passed the write-time redaction boundary; the public-identifier registry is
    process-local, so on readback -- after a restart, or before a surface re-redacts the
    payload (wallet status embeds last_receipt) -- the store re-states which values are its
    own minted digests. Only digest-named values in the producer's exact spelling register."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if vouch_digest_field(str(key), value):
                publish_identifier(value)
            else:
                _revouch_stored_digests(value)
    elif isinstance(payload, list | tuple):
        for entry in payload:
            _revouch_stored_digests(entry)
    return payload


def list_receipts(*, limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT payload_json FROM wallet_receipts ORDER BY created_at DESC, rowid DESC LIMIT ?", (int(limit),)).fetchall()
    return [_revouch_stored_digests(loads(r[0], {})) for r in rows]


def last_receipt() -> dict[str, Any] | None:
    found = list_receipts(limit=1)
    return found[0] if found else None


# --- Blackbox journal ---------------------------------------------------------------------------

def _journal(kind: str, payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    try:
        from core.blackbox.identity import identity_from_context
        from core.blackbox.store import default_store

        entry = {"schema": "blackbox_effect_v1", "kind": kind, **identity_from_context(source_context).to_dict(), **redact_wallet_record(dict(payload))}
        return default_store().append(entry)
    except Exception:
        return None


def journal_security_event(kind: str, payload: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A wallet security event (a key export, a device-auth denial) -- the fact, never the material."""
    return _journal(kind, dict(payload), source_context=source_context)


def journal_intended(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    return _journal(KIND_PAYMENT_INTENDED, payload, source_context=source_context)


def journal_terminal(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    return _journal(KIND_PAYMENT_TERMINAL, payload, source_context=source_context)


# --- execution truth (what the honesty gates read) --------------------------------------------

def register_execution(*, source_context: dict[str, Any] | None, proposal: Any, ok: bool, status: str, tx_signature: str = "") -> None:
    """Best-effort: file the payment as an executed tool fact + a runtime tool receipt for the turn."""
    context = source_context if isinstance(source_context, dict) else {}
    session_id = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    if not session_id:
        return
    detail = redact_wallet_record({"proposal_id": proposal.proposal_id, "tx_signature": tx_signature, "network": proposal.network, "amount_minor": proposal.amount_minor, "asset": proposal.asset})
    try:
        from core.execution_truth import KIND_TOOL, record_execution, resolve_turn_key

        record_execution(session_id=session_id, turn_key=resolve_turn_key(context, None), kind=KIND_TOOL, name=TOOL_NAME, ok=bool(ok), status=status, detail=detail, dedupe=f"wallet:{proposal.proposal_id}:{status}")
    except Exception:
        pass
    try:
        from core.runtime_continuity import store_tool_receipt

        store_tool_receipt(
            receipt_key=f"wallet:{proposal.proposal_id}", session_id=session_id, checkpoint_id=str(context.get("checkpoint_id") or ""),
            tool_name=TOOL_NAME, idempotency_key=proposal.idempotency_key or proposal.proposal_id, arguments=detail,
            execution={"executed": bool(ok), "ok": bool(ok), "status": status, "tx_signature": tx_signature},
        )
    except Exception:
        pass
