from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core import audit_logger
from core.final_response_store import FinalizationRejected, store_final_response
from core.identity_manager import load_active_persona, render_with_persona
from core.liquefy_bridge import export_task_bundle
from core.task_reassembler import ReassembledPlan
from core.task_reassembler import check_and_reassemble as reassemble_parent_task
from storage.db import get_connection


@dataclass
class FinalizedResponse:
    parent_task_id: str
    response_text: str
    confidence: float
    completeness_score: float
    status: str  # in_progress | partial | finalized
    merged_steps: list[str]
    merged_evidence: list[str]


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _clean_lines(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for item in items:
        norm = " ".join((item or "").strip().split())
        if not norm:
            continue
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
        if len(out) >= limit:
            break

    return out


def _status_from_plan(plan: ReassembledPlan) -> str:
    if plan.completeness_score >= 0.85 and plan.confidence >= 0.65 and plan.is_complete:
        return "finalized"
    if plan.is_complete:
        return "finalized"
    if plan.completeness_score >= 0.40 and not plan.pending_subtasks:
        return "partial"
    return "in_progress"


def _load_parent_task_row(parent_task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT task_id, task_class, task_summary, confidence, outcome
            FROM local_tasks
            WHERE task_id = ?
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _persist_parent_output(parent_task_id: str, status: str, response_text: str, confidence: float, completeness: float) -> None:
    # v1: store as audit trail + update parent task row
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM local_tasks WHERE task_id = ? LIMIT 1",
            (parent_task_id,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE local_tasks
                SET outcome = ?,
                    confidence = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (
                    "success" if status == "finalized" else ("partial" if status == "partial" else "pending"),
                    _clamp(confidence),
                    parent_task_id,
                ),
            )
            conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "parent_output_finalized",
        target_id=parent_task_id,
        target_type="task",
        details={
            "status": status,
            "confidence": round(confidence, 4),
            "completeness_score": round(completeness, 4),
            "preview": response_text[:280],
        },
    )


def _format_parent_response(plan: ReassembledPlan, parent_row: dict[str, Any] | None) -> str:
    status = _status_from_plan(plan)
    steps = _clean_lines(plan.merged_steps, limit=6)
    evidence = _clean_lines(plan.merged_evidence, limit=5)

    intro = plan.merged_summary.strip()
    if not intro:
        intro = "The swarm assembled a consolidated answer from accepted helper work."

    lines: list[str] = [intro, ""]

    if parent_row and parent_row.get("task_class"):
        lines.append(f"Task class: {parent_row['task_class']}")
    lines.append(f"Confidence: {plan.confidence:.2f}")
    lines.append(f"Coverage: {plan.completeness_score:.2f}")
    lines.append(f"Status: {status}")
    lines.append("")

    if steps:
        lines.append("Best next steps:")
        for step in steps:
            lines.append(f"- {step}")
        lines.append("")

    if evidence:
        lines.append("Useful signals considered:")
        for item in evidence:
            lines.append(f"- {item}")
        lines.append("")

    if status == "in_progress":
        lines.append("The swarm has partial signal, but more helper work may still improve the final answer.")
    elif status == "partial":
        lines.append("This is a usable partial answer assembled from current accepted subtask output.")
    else:
        lines.append("This is the current best finalized answer assembled from accepted swarm subtasks.")

    return "\n".join(lines).strip()


def finalize_parent_response(parent_task_id: str, *, persona_id: str = "default") -> FinalizedResponse | None:
    plan = reassemble_parent_task(parent_task_id)
    if not plan:
        return None

    parent_row = _load_parent_task_row(parent_task_id)
    persona = load_active_persona(persona_id)

    raw = _format_parent_response(plan, parent_row)
    rendered = render_with_persona(raw, persona)
    status = _status_from_plan(plan)

    _persist_parent_output(
        parent_task_id=parent_task_id,
        status=status,
        response_text=rendered,
        confidence=plan.confidence,
        completeness=plan.completeness_score,
    )

    try:
        # A8 payload lineage: stamp the governing request (and finalization,
        # when the turn's committed truth is already bound) on the legacy lane.
        _lineage_fid = ""
        try:
            from core.finalization import get_finalization_by_content

            _binding = get_finalization_by_content(rendered)
            _lineage_fid = str((_binding or {}).get("finalization_id") or "")
        except Exception:
            _lineage_fid = ""
        _lineage_rid = ""
        try:
            from core.semantic.semantic_admissions import current_request_id

            _lineage_rid = str(current_request_id() or "")
        except Exception:
            _lineage_rid = ""
        store_final_response(
            parent_task_id=parent_task_id,
            raw=raw,
            rendered=rendered,
            status=status,
            confidence=plan.confidence,
            finalization_id=_lineage_fid,
            request_id=_lineage_rid,
        )
    except FinalizationRejected as exc:
        # A7 W1: durable answer truth for this parent already exists with
        # different bytes. First content wins; this divergent re-assembly is
        # rejected honestly instead of silently replacing user-visible truth.
        audit_logger.log(
            "a7_finalization_rejected_different",
            target_id=parent_task_id,
            target_type="task",
            details={
                "stored_content_hash": exc.stored_content_hash,
                "attempted_content_hash": exc.attempted_content_hash,
            },
        )

    export_task_bundle(parent_task_id)
    # Gated + off-thread: a real anchor does serial blocking RPC (up to ~30s),
    # so it must never run inline on the user's finalize turn. dispatch_anchor_in_background
    # is a strict no-op unless anchoring is opted in (VOOL_ANCHOR_RECEIPTS=1), and
    # when enabled it spawns a daemon worker that broadcasts and persists the real
    # tx signature onto the finalized row (set_anchored_signature) once the tx lands.
    # The shared gate keeps this in lockstep with the API service's anchor path.
    # The background anchor broadcast is retired: a signed, broadcast effect only happens through the
    # canonical wallet lifecycle with the operator's approval (core.wallet). Nothing is dispatched here.

    return FinalizedResponse(
        parent_task_id=parent_task_id,
        response_text=rendered,
        confidence=plan.confidence,
        completeness_score=plan.completeness_score,
        status=status,
        merged_steps=_clean_lines(plan.merged_steps, 8),
        merged_evidence=_clean_lines(plan.merged_evidence, 8),
    )
