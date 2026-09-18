from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_connection
from storage.migrations import run_migrations


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        if raw is None or raw == "":
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _reconstruct_receipt_payload(row: Any) -> dict[str, Any]:
    """Rebuild the exact payload that append_contribution_proof_receipt hashed, from the
    row's business columns (the display truth) plus the evidence/created_at carried in
    payload_json. Recomputing the hash from this catches tampering of any business column,
    not just of payload_json itself."""
    stored = _json_loads(row["payload_json"], {})
    return {
        "entry_id": str(row["entry_id"] or "").strip(),
        "task_id": str(row["task_id"] or "").strip(),
        "helper_peer_id": str(row["helper_peer_id"] or "").strip(),
        "parent_peer_id": str(row["parent_peer_id"] or "").strip(),
        "stage": str(row["stage"] or "").strip(),
        "outcome": str(row["outcome"] or "").strip(),
        "finality_state": str(row["finality_state"] or "").strip(),
        "finality_depth": max(0, int(row["finality_depth"] or 0)),
        "finality_target": max(0, int(row["finality_target"] or 0)),
        "compute_credits": round(max(0.0, float(row["compute_credits"] or 0.0)), 6),
        "points_awarded": max(0, int(row["points_awarded"] or 0)),
        "challenge_reason": str(row["challenge_reason"] or "").strip(),
        "evidence": dict(stored.get("evidence") or {}),
        "created_at": str(row["created_at"] or ""),
        "previous_receipt_hash": str(row["previous_receipt_hash"] or ""),
    }


def _receipt_row_is_authentic(row: Any) -> bool:
    """A receipt with a non-empty receipt_hash is authentic iff its columns + evidence
    reconstruct to that hash. Rows with an empty receipt_hash are legacy/unhashed display
    rows that never claimed integrity and are passed through unverified.

    This is a tamper-evidence check over a SHA-256 hash chain, not a cryptographic
    non-repudiation guarantee: an attacker with DB write access who recomputes the hash
    (and every forward link) can still forge a self-consistent chain. It catches partial
    edits, corruption, and broken links — signatures on the execution receipts
    (core.proof_of_execution) are the identity-binding layer."""
    receipt_hash = str(row["receipt_hash"] or "")
    if not receipt_hash:
        return True
    rebuilt = _canonical_payload(_reconstruct_receipt_payload(row))
    return hashlib.sha256(rebuilt.encode("utf-8")).hexdigest() == receipt_hash


def append_contribution_proof_receipt(
    *,
    entry_id: str,
    task_id: str,
    helper_peer_id: str,
    parent_peer_id: str = "",
    stage: str,
    outcome: str = "",
    finality_state: str = "",
    finality_depth: int = 0,
    finality_target: int = 0,
    compute_credits: float = 0.0,
    points_awarded: int = 0,
    challenge_reason: str = "",
    evidence: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    now = str(created_at or _utcnow()).strip() or _utcnow()
    conn = get_connection(db_target)
    try:
        previous = conn.execute(
            """
            SELECT receipt_id, receipt_hash
            FROM contribution_proof_receipts
            WHERE entry_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (str(entry_id or "").strip(),),
        ).fetchone()
        previous_receipt_id = str(previous["receipt_id"] or "") if previous else ""
        previous_receipt_hash = str(previous["receipt_hash"] or "") if previous else ""
        payload = {
            "entry_id": str(entry_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "helper_peer_id": str(helper_peer_id or "").strip(),
            "parent_peer_id": str(parent_peer_id or "").strip(),
            "stage": str(stage or "").strip(),
            "outcome": str(outcome or "").strip(),
            "finality_state": str(finality_state or "").strip(),
            "finality_depth": max(0, int(finality_depth)),
            "finality_target": max(0, int(finality_target)),
            "compute_credits": round(max(0.0, float(compute_credits or 0.0)), 6),
            "points_awarded": max(0, int(points_awarded or 0)),
            "challenge_reason": str(challenge_reason or "").strip(),
            "evidence": dict(evidence or {}),
            "created_at": now,
            "previous_receipt_hash": previous_receipt_hash,
        }
        payload_json = _canonical_payload(payload)
        receipt_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        receipt_id = f"proof-{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO contribution_proof_receipts (
                receipt_id, entry_id, task_id, helper_peer_id, parent_peer_id,
                stage, outcome, finality_state, finality_depth, finality_target,
                compute_credits, points_awarded, challenge_reason,
                previous_receipt_id, previous_receipt_hash, receipt_hash, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                payload["entry_id"],
                payload["task_id"],
                payload["helper_peer_id"],
                payload["parent_peer_id"],
                payload["stage"],
                payload["outcome"],
                payload["finality_state"],
                payload["finality_depth"],
                payload["finality_target"],
                payload["compute_credits"],
                payload["points_awarded"],
                payload["challenge_reason"],
                previous_receipt_id,
                previous_receipt_hash,
                receipt_hash,
                payload_json,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "receipt_id": receipt_id,
        "entry_id": str(entry_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "helper_peer_id": str(helper_peer_id or "").strip(),
        "parent_peer_id": str(parent_peer_id or "").strip(),
        "stage": str(stage or "").strip(),
        "outcome": str(outcome or "").strip(),
        "finality_state": str(finality_state or "").strip(),
        "finality_depth": max(0, int(finality_depth)),
        "finality_target": max(0, int(finality_target)),
        "compute_credits": round(max(0.0, float(compute_credits or 0.0)), 6),
        "points_awarded": max(0, int(points_awarded or 0)),
        "challenge_reason": str(challenge_reason or "").strip(),
        "previous_receipt_id": previous_receipt_id,
        "previous_receipt_hash": previous_receipt_hash,
        "receipt_hash": receipt_hash,
        "payload": dict(evidence or {}),
        "created_at": now,
    }


def list_contribution_proof_receipts(
    *,
    entry_id: str | None = None,
    helper_peer_id: str | None = None,
    stages: list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    where: list[str] = []
    params: list[Any] = []
    if str(entry_id or "").strip():
        where.append("entry_id = ?")
        params.append(str(entry_id or "").strip())
    if str(helper_peer_id or "").strip():
        where.append("helper_peer_id = ?")
        params.append(str(helper_peer_id or "").strip())
    clean_stages = [str(item or "").strip() for item in list(stages or []) if str(item or "").strip()]
    if clean_stages:
        where.append(f"stage IN ({', '.join('?' for _ in clean_stages)})")
        params.extend(clean_stages)
    query = "SELECT * FROM contribution_proof_receipts"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(max(1, int(limit)))
    conn = get_connection(db_target)
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
    finally:
        conn.close()
    # Fail closed: drop rows whose stored receipt_hash does not reconstruct (tampered), so
    # a forged points/credits edit cannot surface through the listing. Legacy rows with an
    # empty receipt_hash pass through with verified=False.
    return [
        {
            "receipt_id": str(row["receipt_id"] or ""),
            "entry_id": str(row["entry_id"] or ""),
            "task_id": str(row["task_id"] or ""),
            "helper_peer_id": str(row["helper_peer_id"] or ""),
            "parent_peer_id": str(row["parent_peer_id"] or ""),
            "stage": str(row["stage"] or ""),
            "outcome": str(row["outcome"] or ""),
            "finality_state": str(row["finality_state"] or ""),
            "finality_depth": int(row["finality_depth"] or 0),
            "finality_target": int(row["finality_target"] or 0),
            "compute_credits": float(row["compute_credits"] or 0.0),
            "points_awarded": int(row["points_awarded"] or 0),
            "challenge_reason": str(row["challenge_reason"] or ""),
            "previous_receipt_id": str(row["previous_receipt_id"] or ""),
            "previous_receipt_hash": str(row["previous_receipt_hash"] or ""),
            "receipt_hash": str(row["receipt_hash"] or ""),
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": str(row["created_at"] or ""),
            "verified": bool(str(row["receipt_hash"] or "")),
        }
        for row in rows
        if _receipt_row_is_authentic(row)
    ]


def verify_contribution_proof_chain(
    entry_id: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Verify the receipt chain for one ledger entry.

    Recomputes each hashed receipt's hash from its columns (rejecting tampered rows) and
    follows the recorded previous_receipt_id / previous_receipt_hash links (rejecting
    broken links). Returns a verdict; ``ok`` is True only when every hashed receipt
    reconstructs and every link resolves to the referenced receipt's hash. See
    _receipt_row_is_authentic for the honest guarantee (tamper-evidence, not
    non-repudiation)."""
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    conn = get_connection(db_target)
    try:
        rows = conn.execute(
            """
            SELECT * FROM contribution_proof_receipts
            WHERE entry_id = ?
            ORDER BY created_at ASC, receipt_id ASC
            """,
            (str(entry_id or "").strip(),),
        ).fetchall()
    finally:
        conn.close()

    by_id = {str(row["receipt_id"] or ""): row for row in rows}
    tampered: list[str] = []
    broken_links: list[str] = []
    for row in rows:
        receipt_id = str(row["receipt_id"] or "")
        if not _receipt_row_is_authentic(row):
            tampered.append(receipt_id)
        prev_id = str(row["previous_receipt_id"] or "")
        prev_hash = str(row["previous_receipt_hash"] or "")
        if not prev_id and not prev_hash:
            continue  # genesis receipt for this entry
        referenced = by_id.get(prev_id)
        if referenced is None or str(referenced["receipt_hash"] or "") != prev_hash:
            broken_links.append(receipt_id)

    verdict = {
        "ok": not tampered and not broken_links,
        "entry_id": str(entry_id or "").strip(),
        "checked": len(rows),
        "tampered_receipt_ids": tampered,
        "broken_link_receipt_ids": broken_links,
    }
    if tampered or broken_links:
        # The verifier OWNS this mapping: it is the only place a failed chain verification
        # is decided, so the fault (and the security plane's observation of it) is filed
        # here, once, with the failing receipt ids as evidence. Best-effort, never raises.
        _file_integrity_fault(entry_id, tampered, broken_links)
    return verdict


def _file_integrity_fault(entry_id: str, tampered: list[str], broken_links: list[str]) -> None:
    try:
        from core.faults.recorder import record_fault
        from core.faults.records import FaultRecord

        record_fault(
            FaultRecord.for_code(
                "integrity_verification_failure",
                authority="core.contribution_proof",
                dedupe=f"chain:{entry_id}:{len(tampered)}:{len(broken_links)}",
                evidence_refs=tuple([*tampered, *broken_links]),
                context={
                    "entry_id": str(entry_id or "").strip(),
                    "status": "chain_verification_failed",
                    "operation": "verify_contribution_proof_chain",
                    "missing_count": len(tampered) + len(broken_links),
                },
            )
        )
    except Exception:
        pass
