from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger
from storage.db import get_connection


@dataclass
class ReassembledPlan:
    parent_task_id: str
    is_complete: bool
    merged_summary: str
    merged_evidence: list[str]
    merged_steps: list[str]
    pending_subtasks: int
    confidence: float = 0.0
    completeness_score: float = 0.0
    result_hash: str = ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_parent_task(parent_task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM local_tasks WHERE task_id = ? LIMIT 1",
            (parent_task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_child_offers(parent_task_id: str) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, capsule_json, parent_task_ref
            FROM task_capsules
            WHERE parent_task_ref = ?
            """,
            (parent_task_id,),
        ).fetchall()

        # Legacy fallback for older rows without explicit parent_task_ref column values.
        if not rows:
            rows = conn.execute(
                """
                SELECT task_id, capsule_json, parent_task_ref
                FROM task_capsules
                WHERE capsule_json LIKE ?
                ORDER BY updated_at DESC
                LIMIT 200
                """,
                (f"%parent_task_ref:{parent_task_id}%",),
            ).fetchall()

        child_task_ids: list[str] = []
        for row in rows:
            parent_ref = str(row["parent_task_ref"] or "").strip()
            if parent_ref == parent_task_id:
                child_task_ids.append(str(row["task_id"]))
                continue
            try:
                data = json.loads(row["capsule_json"])
            except Exception:
                continue
            constraints = data.get("sanitized_context", {}).get("known_constraints", [])
            marker_full = f"parent_task_ref:{parent_task_id}"
            marker_legacy = f"parent_task_ref:{parent_task_id[:16]}"
            if marker_full in constraints or marker_legacy in constraints:
                child_task_ids.append(str(row["task_id"]))

        if not child_task_ids:
            return []

        placeholders = ",".join("?" for _ in child_task_ids)
        offers = conn.execute(
            f"SELECT * FROM task_offers WHERE task_id IN ({placeholders})",
            tuple(child_task_ids),
        ).fetchall()
        return [dict(r) for r in offers]
    finally:
        conn.close()


def _get_accepted_results_for_offers(offer_ids: list[str]) -> list[dict[str, Any]]:
    if not offer_ids:
        return []

    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in offer_ids)
        # We only want results that have been formally accepted through review
        results = conn.execute(
            f"""
            SELECT * FROM task_results
            WHERE task_id IN ({placeholders})
            AND status IN ('accepted', 'partial')
            """,
            tuple(offer_ids),
        ).fetchall()
        return [dict(r) for r in results]
    finally:
        conn.close()


def check_and_reassemble(parent_task_id: str) -> ReassembledPlan | None:
    """
    Checks if all spawned subtasks for a given parent task have completed successfully.
    If so, merges their evidence and steps into a higher-level plan context.
    """
    child_offers = _get_child_offers(parent_task_id)
    if not child_offers:
        return None

    offer_ids = [offer["task_id"] for offer in child_offers]

    pending_offers = [o for o in child_offers if o["status"] not in {"completed", "cancelled"}]

    accepted_results = _get_accepted_results_for_offers(offer_ids)

    # If we have pending offers, the reassembly is not complete.
    # But we can still return a partial state so the parent knows progress.
    if pending_offers:
        completeness = len(accepted_results) / max(1, len(child_offers))
        return ReassembledPlan(
            parent_task_id=parent_task_id,
            is_complete=False,
            merged_summary="",
            merged_evidence=[],
            merged_steps=[],
            pending_subtasks=len(pending_offers),
            confidence=0.0,
            completeness_score=max(0.0, min(1.0, completeness)),
            result_hash="",
        )

    # All offers completed or cancelled. Let's merge the accepted ones.
    if not accepted_results:
        return ReassembledPlan(
            parent_task_id=parent_task_id,
            is_complete=True,
            merged_summary="All subtasks failed or were cancelled.",
            merged_evidence=[],
            merged_steps=[],
            pending_subtasks=0,
            confidence=0.0,
            completeness_score=0.0,
            result_hash=hashlib.sha256(f"{parent_task_id}:empty".encode()).hexdigest(),
        )

    merged_evidence = []
    merged_steps = []
    confidences: list[float] = []

    for res in accepted_results:
        with contextlib.suppress(Exception):
            confidences.append(max(0.0, min(1.0, float(res.get("confidence") or 0.0))))
        try:
            evidence = json.loads(res["evidence_json"])
            if isinstance(evidence, list):
                merged_evidence.extend(evidence)
        except Exception:
            pass

        try:
            steps = json.loads(res["abstract_steps_json"])
            if isinstance(steps, list):
                merged_steps.extend(steps)
        except Exception:
            pass

    # Deduplicate while preserving order roughly
    unique_evidence = list(dict.fromkeys(merged_evidence))
    unique_steps = list(dict.fromkeys(merged_steps))

    summary = f"Synthesized results from {len(accepted_results)} approved helper subtasks."
    confidence = sum(confidences) / max(1, len(confidences))
    completeness = len(accepted_results) / max(1, len(child_offers))
    result_hash = hashlib.sha256(
        json.dumps(
            {
                "parent_task_id": parent_task_id,
                "summary": summary,
                "steps": unique_steps,
                "evidence": unique_evidence,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    audit_logger.log(
        "task_reassembled",
        target_id=parent_task_id,
        target_type="task",
        details={
            "child_tasks": len(child_offers),
            "accepted_results": len(accepted_results),
            "merged_evidence_items": len(unique_evidence),
            "merged_steps": len(unique_steps),
        }
    )

    return ReassembledPlan(
        parent_task_id=parent_task_id,
        is_complete=True,
        merged_summary=summary,
        merged_evidence=unique_evidence,
        merged_steps=unique_steps,
        pending_subtasks=0,
        confidence=max(0.0, min(1.0, confidence)),
        completeness_score=max(0.0, min(1.0, completeness)),
        result_hash=result_hash,
    )
