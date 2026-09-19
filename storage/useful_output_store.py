from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_connection
from storage.migrations import run_migrations

_GENERIC_FALLBACK_PATTERNS = (
    re.compile(r"\bi won't fake it\b", re.IGNORECASE),
    re.compile(r"\binvalid tool payload\b", re.IGNORECASE),
    re.compile(r"\bi'm here and ready to help\b", re.IGNORECASE),
    re.compile(r"^here'?s what i'?d suggest", re.IGNORECASE),
    re.compile(r"^real steps completed:\s*- unknown", re.IGNORECASE),
)

_SUCCESS_MARKERS = {"success", "finalized", "completed", "resolved", "solved"}
_ACCEPTED_TASK_STATES = {"accepted", "reviewed"}
_APPROVED_PARTIAL_STATES = {"partial"}
_POSITIVE_REVIEW_OUTCOMES = {"accepted", "approved", "reviewed", "partial"}
_NEGATIVE_REVIEW_OUTCOMES = {"rejected", "harmful", "failed"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(raw: Any, fallback: Any) -> Any:
    try:
        if raw in (None, ""):
            return fallback
        loaded = json.loads(str(raw))
    except Exception:
        return fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    return loaded


def _stable_useful_output_id(source_type: str, source_id: str) -> str:
    digest = hashlib.sha256(f"{source_type}:{source_id}".encode()).hexdigest()[:24]
    return f"uo-{digest}"


def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    return {str(row["name"]) for row in rows if row and row["name"]}


def _extract_artifact_ids(value: Any) -> list[str]:
    artifact_ids: list[str] = []
    items = value if isinstance(value, list) else list(_json_loads(value, []))
    for item in items:
        if isinstance(item, dict):
            for key in ("artifact_id", "bundle_artifact_id", "packet_artifact_id", "id"):
                candidate = str(item.get(key) or "").strip()
                if candidate and candidate not in artifact_ids:
                    artifact_ids.append(candidate)
            ref = str(item.get("ref") or item.get("reference") or "").strip()
            if ref.startswith("artifact:"):
                candidate = ref.split(":", 1)[1].strip()
                if candidate and candidate not in artifact_ids:
                    artifact_ids.append(candidate)
        elif isinstance(item, str):
            candidate = item.strip()
            if candidate.startswith("artifact:"):
                candidate = candidate.split(":", 1)[1].strip()
            if candidate and candidate not in artifact_ids:
                artifact_ids.append(candidate)
    return artifact_ids


def _text_has_fallback_noise(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return True
    return any(pattern.search(candidate) for pattern in _GENERIC_FALLBACK_PATTERNS)


def _normalize_text(text: Any, *, max_chars: int = 12000) -> str:
    clean = " ".join(str(text or "").strip().split())
    if len(clean) > max_chars:
        return clean[:max_chars]
    return clean


def _bool_label(value: bool, positive: str, negative: str) -> str:
    return positive if value else negative


def _task_instruction(row: dict[str, Any]) -> str:
    task_type = str(row.get("task_type") or row.get("result_type") or "task").strip()
    task_summary = _normalize_text(row.get("task_summary") or row.get("summary") or "", max_chars=800)
    return (
        f"Task type: {task_type}\n"
        f"Task summary: {task_summary}\n"
        "Write the kind of accepted worker result VOOL should produce."
    ).strip()


def _final_response_instruction(row: dict[str, Any]) -> str:
    task_class = str(row.get("task_class") or "general").strip()
    task_summary = _normalize_text(row.get("task_summary") or "", max_chars=800)
    return (
        f"Task class: {task_class}\n"
        f"Task summary: {task_summary}\n"
        "Write the final user-facing response VOOL should return when the task is actually complete."
    ).strip()


def _hive_instruction(row: dict[str, Any]) -> str:
    title = _normalize_text(row.get("title") or "", max_chars=240)
    summary = _normalize_text(row.get("topic_summary") or row.get("summary") or "", max_chars=500)
    post_kind = str(row.get("post_kind") or "analysis").strip()
    stance = str(row.get("stance") or "propose").strip()
    return (
        f"Hive topic: {title}\n"
        f"Topic summary: {summary}\n"
        f"Post kind: {post_kind}\n"
        f"Stance: {stance}\n"
        "Write a durable Hive contribution grounded in evidence or useful downstream work."
    ).strip()


def _is_commons_hive_row(row: dict[str, Any]) -> bool:
    tags = {str(item or "").strip().lower() for item in _json_loads(row.get("topic_tags_json"), []) if str(item or "").strip()}
    combined = f"{row.get('title') or ''!s} {row.get('topic_summary') or row.get('summary') or ''!s}".lower()
    return (
        "agent_commons" in tags
        or "commons" in tags
        or "brainstorm" in tags
        or "curiosity" in tags
        or "agent commons" in combined
        or "brainstorm lane" in combined
        or "idle curiosity" in combined
    )


def _quality_round(value: float) -> float:
    return round(max(0.0, min(1.0, float(value or 0.0))), 4)


def _normalized_finality_state(value: Any, outcome: Any = None) -> str:
    finality = str(value or "").strip().lower()
    if finality:
        return finality
    outcome_label = str(outcome or "").strip().lower()
    if outcome_label == "released":
        return "confirmed"
    if outcome_label in {"rejected", "harmful", "failed"}:
        return "rejected"
    if outcome_label == "slashed":
        return "slashed"
    if outcome_label == "pending":
        return "pending"
    return ""


def _review_support_score(row: dict[str, Any]) -> float:
    positive = max(0, int(row.get("positive_review_count") or 0))
    negative = max(0, int(row.get("negative_review_count") or 0))
    total = positive + negative
    if total <= 0:
        latest_outcome = str(row.get("review_outcome") or row.get("outcome") or "").strip().lower()
        if latest_outcome in _POSITIVE_REVIEW_OUTCOMES:
            return 1.0
        if latest_outcome in _NEGATIVE_REVIEW_OUTCOMES:
            return 0.0
        return 0.0
    return round(max(0.0, min(1.0, positive / total)), 4)


def _task_quality(row: dict[str, Any], evidence_count: int) -> float:
    status = str(row.get("status") or "").strip().lower()
    confidence = max(0.0, float(row.get("confidence") or 0.0))
    helpfulness = max(0.0, float(row.get("helpfulness_score") or 0.0))
    review_quality = max(0.0, float(row.get("review_quality_score") or 0.0))
    reviewer_count = max(0, int(row.get("reviewer_count") or 0))
    review_support = _review_support_score(row)
    finality_state = _normalized_finality_state(row.get("finality_state"), row.get("ledger_outcome"))
    quality = 0.42 + (confidence * 0.18) + (helpfulness * 0.18) + (review_quality * 0.18)
    if status in _ACCEPTED_TASK_STATES:
        quality += 0.16
    elif status in _APPROVED_PARTIAL_STATES:
        quality += 0.06
    quality += min(0.08, review_support * 0.08)
    if reviewer_count >= 2:
        quality += min(0.06, reviewer_count * 0.02)
    if evidence_count:
        quality += min(0.08, evidence_count * 0.02)
    if finality_state == "confirmed":
        quality += 0.08
    elif finality_state == "finalized":
        quality += 0.14
    elif finality_state == "pending":
        quality -= 0.12
    elif finality_state in {"rejected", "slashed"}:
        quality -= 0.45
    if list(_json_loads(row.get("risk_flags_json"), [])):
        quality -= 0.28
    return _quality_round(quality)


def _final_response_quality(row: dict[str, Any], linked_result_count: int) -> float:
    confidence = max(0.0, float(row.get("confidence_score") or 0.0))
    quality = 0.38 + (confidence * 0.26)
    status_marker = str(row.get("status_marker") or "").strip().lower()
    if status_marker in _SUCCESS_MARKERS:
        quality += 0.18
    task_outcome = str(row.get("task_outcome") or "").strip().lower()
    if task_outcome in {"success", "completed", "solved", "done"}:
        quality += 0.12
    if linked_result_count > 0:
        quality += 0.12
    if _text_has_fallback_noise(str(row.get("rendered_persona_text") or "")):
        quality -= 0.45
    return _quality_round(quality)


def _hive_quality(row: dict[str, Any], evidence_count: int) -> float:
    post_kind = str(row.get("post_kind") or "analysis").strip().lower()
    topic_status = str(row.get("topic_status") or "open").strip().lower()
    promotion_status = str(row.get("promotion_status") or "").strip().lower()
    promotion_review_state = str(row.get("promotion_review_state") or "").strip().lower()
    support_weight = max(0.0, float(row.get("support_weight") or 0.0))
    challenge_weight = max(0.0, float(row.get("challenge_weight") or 0.0))
    downstream_use_count = max(0, int(row.get("downstream_use_count") or 0))
    training_signal_count = max(0, int(row.get("training_signal_count") or 0))
    quality = 0.36
    if post_kind in {"result", "summary", "verdict"}:
        quality += 0.24
    elif post_kind in {"analysis", "progress"}:
        quality += 0.14
    if evidence_count:
        quality += min(0.16, evidence_count * 0.03)
    if topic_status in {"researching", "solved", "disputed", "partial", "needs_improvement"}:
        quality += 0.08
    if promotion_review_state == "approved":
        quality += 0.12
    if promotion_status in {"approved", "promoted"}:
        quality += 0.08
    quality += min(0.14, support_weight * 0.03)
    quality -= min(0.16, challenge_weight * 0.04)
    quality += min(0.08, downstream_use_count * 0.02)
    quality += min(0.06, training_signal_count * 0.02)
    if _text_has_fallback_noise(str(row.get("body") or "")):
        quality -= 0.45
    return _quality_round(quality)


def _task_useful_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence = _json_loads(row.get("evidence_json"), [])
    artifact_ids = _extract_artifact_ids(evidence)
    status = str(row.get("status") or "").strip().lower()
    review_outcome = str(row.get("review_outcome") or "").strip().lower()
    harmful = bool(int(row.get("harmful_flag") or 0))
    reviewer_count = max(0, int(row.get("reviewer_count") or 0))
    positive_review_count = max(0, int(row.get("positive_review_count") or 0))
    negative_review_count = max(0, int(row.get("negative_review_count") or 0))
    review_support_score = _review_support_score(row)
    review_supported = positive_review_count > 0 and positive_review_count >= negative_review_count
    finality_state = _normalized_finality_state(row.get("finality_state"), row.get("ledger_outcome"))
    finality_depth = max(0, int(row.get("finality_depth") or 0))
    finality_target = max(0, int(row.get("finality_target") or 0))
    has_proof_record = bool(str(row.get("contribution_entry_id") or "").strip())
    approved_partial = status in _APPROVED_PARTIAL_STATES and review_outcome in {"accepted", "reviewed", "approve", "approved"}
    base_eligible = (status in _ACCEPTED_TASK_STATES or approved_partial) and not harmful and not _text_has_fallback_noise(str(row.get("summary") or ""))
    eligible = base_eligible and review_supported
    if has_proof_record:
        eligible = eligible and finality_state in {"confirmed", "finalized"}
    durability_reasons: list[str] = []
    if status:
        durability_reasons.append(status)
    if review_outcome:
        durability_reasons.append(review_outcome)
    if review_supported:
        durability_reasons.append("review_supported")
    if reviewer_count >= 2:
        durability_reasons.append("multi_reviewer_support")
    if artifact_ids:
        durability_reasons.append("artifact_backed")
    if evidence:
        durability_reasons.append("evidence_backed")
    if has_proof_record and finality_state:
        durability_reasons.append(f"proof_{finality_state}")
        if finality_state in {"confirmed", "finalized"}:
            durability_reasons.append("proof_backed")
    eligibility_reasons: list[str] = []
    if eligible:
        eligibility_reasons.append("training_eligible")
    else:
        if harmful:
            eligibility_reasons.append("harmful_review")
        if status not in _ACCEPTED_TASK_STATES and not approved_partial:
            eligibility_reasons.append("unaccepted_result")
        if base_eligible and not review_supported:
            eligibility_reasons.append("insufficient_review_support")
        if has_proof_record and finality_state == "pending":
            eligibility_reasons.append("proof_pending")
        if has_proof_record and finality_state in {"rejected", "slashed"}:
            eligibility_reasons.append("proof_rejected_or_slashed")
        if _text_has_fallback_noise(str(row.get("summary") or "")):
            eligibility_reasons.append("low_signal_output")
    archive_state = "candidate" if eligible else "transient"
    quality_score = _task_quality(row, len(evidence))
    return {
        "source_type": "task_result",
        "source_id": str(row.get("result_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "topic_id": "",
        "claim_id": str(row.get("claim_id") or ""),
        "result_id": str(row.get("result_id") or ""),
        "artifact_ids": artifact_ids,
        "instruction_text": _task_instruction(row),
        "output_text": _normalize_text(row.get("summary") or ""),
        "summary": _normalize_text(row.get("summary") or "", max_chars=240),
        "acceptance_state": status,
        "review_state": review_outcome,
        "archive_state": archive_state,
        "eligibility_state": _bool_label(eligible, "eligible", "ineligible"),
        "durability_reasons": sorted({item for item in durability_reasons if item}),
        "eligibility_reasons": sorted({item for item in eligibility_reasons if item}),
        "quality_score": quality_score,
        "source_created_at": str(row.get("created_at") or ""),
        "source_updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
        "metadata": {
            "helper_peer_id": str(row.get("helper_peer_id") or ""),
            "result_type": str(row.get("result_type") or ""),
            "task_type": str(row.get("task_type") or ""),
            "confidence": float(row.get("confidence") or 0.0),
            "review_outcome": review_outcome,
            "helpfulness_score": float(row.get("helpfulness_score") or 0.0),
            "quality_score": float(row.get("review_quality_score") or 0.0),
            "reviewer_count": reviewer_count,
            "positive_review_count": positive_review_count,
            "negative_review_count": negative_review_count,
            "review_support_score": review_support_score,
            "harmful_flag": harmful,
            "risk_flags": _json_loads(row.get("risk_flags_json"), []),
            "evidence_refs": evidence,
            "finality_state": finality_state,
            "finality_depth": finality_depth,
            "finality_target": finality_target,
            "proof_backed": has_proof_record and finality_state in {"confirmed", "finalized"},
            "structured_signal": True,
        },
    }


def _final_response_useful_row(row: dict[str, Any]) -> dict[str, Any]:
    linked_result_count = int(row.get("linked_result_count") or 0)
    task_outcome = str(row.get("task_outcome") or "").strip().lower()
    status_marker = str(row.get("status_marker") or "").strip().lower()
    successful_parent = task_outcome in {"success", "completed", "solved", "done"} or status_marker in _SUCCESS_MARKERS or linked_result_count > 0
    # A8 law 11: eligibility is never fabricated on lineage-less rows. A
    # finalized_response without payload lineage is legacy truth — honest
    # LEGACY_UNVERIFIED verdict, not training_eligible.
    has_lineage = bool(str(row.get("finalization_id") or "").strip())
    eligible = (
        successful_parent
        and has_lineage
        and not _text_has_fallback_noise(str(row.get("rendered_persona_text") or ""))
    )
    durability_reasons: list[str] = []
    if successful_parent:
        durability_reasons.append("accepted_parent")
    if status_marker:
        durability_reasons.append(f"status:{status_marker}")
    if linked_result_count > 0:
        durability_reasons.append("linked_accepted_result")
    if str(row.get("share_scope") or "").strip().lower() in {"hive_mind", "shared_pack"}:
        durability_reasons.append("shared_scope")
    eligibility_reasons: list[str] = []
    if eligible:
        eligibility_reasons.append("training_eligible")
    else:
        if not successful_parent:
            eligibility_reasons.append("parent_not_accepted")
        if not has_lineage:
            eligibility_reasons.append("legacy_unverified_lineage")
        if _text_has_fallback_noise(str(row.get("rendered_persona_text") or "")):
            eligibility_reasons.append("low_signal_output")
    quality_score = _final_response_quality(row, linked_result_count)
    return {
        "source_type": "final_response",
        "source_id": str(row.get("parent_task_id") or ""),
        "task_id": str(row.get("parent_task_id") or ""),
        "topic_id": "",
        "claim_id": "",
        "result_id": "",
        "artifact_ids": [],
        "instruction_text": _final_response_instruction(row),
        "output_text": _normalize_text(row.get("rendered_persona_text") or ""),
        "summary": _normalize_text(row.get("rendered_persona_text") or "", max_chars=240),
        "acceptance_state": task_outcome or status_marker,
        "review_state": _bool_label(successful_parent, "accepted_parent", "unverified_parent"),
        "archive_state": "candidate" if eligible else "transient",
        "eligibility_state": _bool_label(eligible, "eligible", "ineligible"),
        "durability_reasons": sorted({item for item in durability_reasons if item}),
        "eligibility_reasons": sorted({item for item in eligibility_reasons if item}),
        "quality_score": quality_score,
        "source_created_at": str(row.get("created_at") or ""),
        "source_updated_at": str(row.get("created_at") or ""),
        "metadata": {
            "task_class": str(row.get("task_class") or ""),
            "task_outcome": task_outcome,
            "status_marker": status_marker,
            "confidence_score": float(row.get("confidence_score") or 0.0),
            "linked_result_count": linked_result_count,
            "share_scope": str(row.get("share_scope") or ""),
            "finalization_id": str(row.get("finalization_id") or ""),
            "structured_signal": True,
        },
    }


def _hive_post_useful_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = _json_loads(row.get("evidence_refs_json"), [])
    artifact_ids = _extract_artifact_ids(evidence_refs)
    moderation_state = str(row.get("moderation_state") or "legacy_unverified").strip().lower()
    post_kind = str(row.get("post_kind") or "analysis").strip().lower()
    topic_status = str(row.get("topic_status") or "open").strip().lower()
    evidence_backed = bool(evidence_refs or artifact_ids)
    durable_kind = post_kind in {"result", "summary", "verdict"}
    commons_post = _is_commons_hive_row(row)
    promotion_status = str(row.get("promotion_status") or "").strip().lower()
    promotion_review_state = str(row.get("promotion_review_state") or "").strip().lower()
    promotion_archive_state = str(row.get("promotion_archive_state") or "").strip().lower()
    support_weight = max(0.0, float(row.get("support_weight") or 0.0))
    challenge_weight = max(0.0, float(row.get("challenge_weight") or 0.0))
    downstream_use_count = max(0, int(row.get("downstream_use_count") or 0))
    training_signal_count = max(0, int(row.get("training_signal_count") or 0))
    promotion_ready = promotion_status in {"approved", "promoted"} or promotion_review_state == "approved"
    eligible = moderation_state == "approved" and (evidence_backed or durable_kind) and not _text_has_fallback_noise(str(row.get("body") or ""))
    if commons_post:
        eligible = eligible and promotion_ready
    durability_reasons: list[str] = []
    if moderation_state == "approved":
        durability_reasons.append("approved")
    if evidence_backed:
        durability_reasons.append("evidence_backed")
    if artifact_ids:
        durability_reasons.append("artifact_backed")
    if durable_kind:
        durability_reasons.append("durable_post_kind")
    if topic_status in {"researching", "solved", "disputed", "partial", "needs_improvement"}:
        durability_reasons.append(f"topic:{topic_status}")
    if commons_post:
        durability_reasons.append("commons_post")
    if promotion_ready:
        durability_reasons.append("promotion_review_approved")
    if support_weight > 0.0:
        durability_reasons.append("support_weighted")
    if downstream_use_count > 0:
        durability_reasons.append("downstream_reused")
    if training_signal_count > 0:
        durability_reasons.append("training_signal_backed")
    if promotion_archive_state in {"candidate", "approved"}:
        durability_reasons.append(f"archive:{promotion_archive_state}")
    eligibility_reasons: list[str] = []
    if eligible:
        eligibility_reasons.append("training_eligible")
    else:
        if moderation_state != "approved":
            eligibility_reasons.append("unapproved_hive_post")
        if not (evidence_backed or durable_kind):
            eligibility_reasons.append("missing_evidence_or_durable_kind")
        if commons_post and not promotion_ready:
            if promotion_status == "rejected" or promotion_review_state == "rejected":
                eligibility_reasons.append("commons_review_rejected")
            else:
                eligibility_reasons.append("commons_review_pending")
        if _text_has_fallback_noise(str(row.get("body") or "")):
            eligibility_reasons.append("low_signal_output")
    quality_score = _hive_quality(row, len(evidence_refs))
    return {
        "source_type": "hive_post",
        "source_id": str(row.get("post_id") or ""),
        "task_id": str(row.get("linked_task_id") or ""),
        "topic_id": str(row.get("topic_id") or ""),
        "claim_id": "",
        "result_id": "",
        "artifact_ids": artifact_ids,
        "instruction_text": _hive_instruction(row),
        "output_text": _normalize_text(row.get("body") or ""),
        "summary": _normalize_text(row.get("body") or "", max_chars=240),
        "acceptance_state": moderation_state,
        "review_state": promotion_review_state or moderation_state,
        "archive_state": "approved" if promotion_archive_state == "approved" else ("candidate" if eligible or promotion_archive_state == "candidate" else "transient"),
        "eligibility_state": _bool_label(eligible, "eligible", "ineligible"),
        "durability_reasons": sorted({item for item in durability_reasons if item}),
        "eligibility_reasons": sorted({item for item in eligibility_reasons if item}),
        "quality_score": quality_score,
        "source_created_at": str(row.get("created_at") or ""),
        "source_updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
        "metadata": {
            "author_agent_id": str(row.get("author_agent_id") or ""),
            "post_kind": post_kind,
            "stance": str(row.get("stance") or ""),
            "topic_status": topic_status,
            "moderation_state": moderation_state,
            "promotion_status": promotion_status,
            "promotion_review_state": promotion_review_state,
            "promotion_archive_state": promotion_archive_state,
            "support_weight": support_weight,
            "challenge_weight": challenge_weight,
            "downstream_use_count": downstream_use_count,
            "training_signal_count": training_signal_count,
            "evidence_refs": evidence_refs,
            "topic_tags": _json_loads(row.get("topic_tags_json"), []),
            "structured_signal": True,
        },
    }


def _task_result_source_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tr.result_id, tr.task_id, tr.helper_peer_id, tr.result_type, tr.summary, tr.confidence,
               tr.evidence_json, tr.risk_flags_json, tr.status, tr.created_at, tr.updated_at,
               toff.summary AS task_summary, toff.task_type,
               ta.claim_id,
               rv.outcome AS review_outcome,
               rv.helpfulness_score,
               rv.quality_score AS review_quality_score,
               rv.harmful_flag,
               COALESCE(rva.reviewer_count, 0) AS reviewer_count,
               COALESCE(rva.positive_review_count, 0) AS positive_review_count,
               COALESCE(rva.negative_review_count, 0) AS negative_review_count,
               cl.entry_id AS contribution_entry_id,
               cl.outcome AS ledger_outcome,
               cl.finality_state,
               cl.finality_depth,
               cl.finality_target
        FROM task_results tr
        LEFT JOIN task_offers toff ON toff.task_id = tr.task_id
        LEFT JOIN task_assignments ta
          ON ta.assignment_id = (
              SELECT assignment_id
              FROM task_assignments assignment_pick
              WHERE assignment_pick.task_id = tr.task_id
                AND assignment_pick.helper_peer_id = tr.helper_peer_id
              ORDER BY assignment_pick.updated_at DESC
              LIMIT 1
          )
        LEFT JOIN task_reviews rv
          ON rv.review_id = (
              SELECT review_id
              FROM task_reviews review_pick
              WHERE review_pick.task_id = tr.task_id
                AND review_pick.helper_peer_id = tr.helper_peer_id
              ORDER BY review_pick.created_at DESC
              LIMIT 1
          )
        LEFT JOIN (
            SELECT
                task_id,
                helper_peer_id,
                COUNT(DISTINCT reviewer_peer_id) AS reviewer_count,
                SUM(CASE WHEN harmful_flag = 0 AND LOWER(outcome) IN ('accepted', 'approved', 'reviewed', 'partial') THEN 1 ELSE 0 END) AS positive_review_count,
                SUM(CASE WHEN harmful_flag = 1 OR LOWER(outcome) IN ('rejected', 'harmful', 'failed') THEN 1 ELSE 0 END) AS negative_review_count
            FROM task_reviews
            GROUP BY task_id, helper_peer_id
        ) rva
          ON rva.task_id = tr.task_id
         AND rva.helper_peer_id = tr.helper_peer_id
        LEFT JOIN contribution_ledger cl
          ON cl.entry_id = (
              SELECT entry_id
              FROM contribution_ledger ledger_pick
              WHERE ledger_pick.task_id = tr.task_id
                AND ledger_pick.helper_peer_id = tr.helper_peer_id
              ORDER BY
                  CASE LOWER(COALESCE(ledger_pick.finality_state, ''))
                      WHEN 'finalized' THEN 5
                      WHEN 'confirmed' THEN 4
                      WHEN 'pending' THEN 3
                      WHEN 'rejected' THEN 2
                      WHEN 'slashed' THEN 1
                      ELSE 0
                  END DESC,
                  COALESCE(ledger_pick.finalized_at, ledger_pick.confirmed_at, ledger_pick.updated_at, ledger_pick.created_at) DESC,
                  ledger_pick.created_at DESC
              LIMIT 1
          )
        ORDER BY tr.updated_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _final_response_source_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT fr.parent_task_id, fr.rendered_persona_text, fr.status_marker, fr.confidence_score, fr.created_at,
               fr.finalization_id,
               lt.task_summary, lt.task_class, lt.outcome AS task_outcome, lt.share_scope,
               (
                   SELECT COUNT(*)
                   FROM task_results tr
                   WHERE tr.task_id = fr.parent_task_id
                     AND tr.status IN ('accepted', 'reviewed', 'partial')
               ) AS linked_result_count
        FROM finalized_responses fr
        LEFT JOIN local_tasks lt ON lt.task_id = fr.parent_task_id
        ORDER BY fr.created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _hive_post_source_rows(conn: Any) -> list[dict[str, Any]]:
    post_columns = _table_columns(conn, "hive_posts")
    candidate_columns = _table_columns(conn, "hive_commons_promotion_candidates")
    moderation_select = "COALESCE(hp.moderation_state, 'legacy_unverified') AS moderation_state," if "moderation_state" in post_columns else "'legacy_unverified' AS moderation_state,"
    updated_select = "COALESCE(hp.updated_at, hp.created_at) AS updated_at," if "updated_at" in post_columns else "hp.created_at AS updated_at,"
    if candidate_columns:
        candidate_join = "LEFT JOIN hive_commons_promotion_candidates cpc ON cpc.post_id = hp.post_id"
        candidate_select = (
            "COALESCE(cpc.status, '') AS promotion_status, "
            "COALESCE(cpc.review_state, '') AS promotion_review_state, "
            "COALESCE(cpc.archive_state, '') AS promotion_archive_state, "
            "COALESCE(cpc.support_weight, 0.0) AS support_weight, "
            "COALESCE(cpc.challenge_weight, 0.0) AS challenge_weight, "
            "COALESCE(cpc.downstream_use_count, 0) AS downstream_use_count, "
            "COALESCE(cpc.training_signal_count, 0) AS training_signal_count,"
        )
    else:
        candidate_join = ""
        candidate_select = (
            "'' AS promotion_status, "
            "'' AS promotion_review_state, "
            "'' AS promotion_archive_state, "
            "0.0 AS support_weight, "
            "0.0 AS challenge_weight, "
            "0 AS downstream_use_count, "
            "0 AS training_signal_count,"
        )
    rows = conn.execute(
        f"""
        SELECT hp.post_id, hp.topic_id, hp.author_agent_id, hp.post_kind, hp.stance, hp.body,
               hp.evidence_refs_json, hp.created_at,
               {updated_select}
               {moderation_select}
               {candidate_select}
               ht.title, ht.summary AS topic_summary, ht.status AS topic_status, ht.linked_task_id, ht.topic_tags_json
        FROM hive_posts hp
        JOIN hive_topics ht ON ht.topic_id = hp.topic_id
        {candidate_join}
        ORDER BY hp.created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _upsert_useful_output(conn: Any, row: dict[str, Any]) -> None:
    source_type = str(row.get("source_type") or "").strip()
    source_id = str(row.get("source_id") or "").strip()
    if not source_type or not source_id:
        return
    now = _utcnow()
    useful_output_id = _stable_useful_output_id(source_type, source_id)
    conn.execute(
        """
        INSERT INTO useful_outputs (
            useful_output_id, source_type, source_id, task_id, topic_id, claim_id, result_id,
            artifact_ids_json, instruction_text, output_text, summary, acceptance_state, review_state,
            archive_state, eligibility_state, durability_reasons_json, eligibility_reasons_json,
            quality_score, source_created_at, source_updated_at, metadata_json, finalization_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_type, source_id) DO UPDATE SET
            task_id = excluded.task_id,
            topic_id = excluded.topic_id,
            claim_id = excluded.claim_id,
            result_id = excluded.result_id,
            artifact_ids_json = excluded.artifact_ids_json,
            instruction_text = excluded.instruction_text,
            output_text = excluded.output_text,
            summary = excluded.summary,
            acceptance_state = excluded.acceptance_state,
            review_state = excluded.review_state,
            archive_state = excluded.archive_state,
            eligibility_state = excluded.eligibility_state,
            durability_reasons_json = excluded.durability_reasons_json,
            eligibility_reasons_json = excluded.eligibility_reasons_json,
            quality_score = excluded.quality_score,
            source_created_at = excluded.source_created_at,
            source_updated_at = excluded.source_updated_at,
            metadata_json = excluded.metadata_json,
            finalization_id = excluded.finalization_id,
            updated_at = excluded.updated_at
        """,
        (
            useful_output_id,
            source_type,
            source_id,
            str(row.get("task_id") or ""),
            str(row.get("topic_id") or ""),
            str(row.get("claim_id") or ""),
            str(row.get("result_id") or ""),
            json.dumps(list(row.get("artifact_ids") or []), sort_keys=True),
            str(row.get("instruction_text") or ""),
            str(row.get("output_text") or ""),
            str(row.get("summary") or ""),
            str(row.get("acceptance_state") or ""),
            str(row.get("review_state") or ""),
            str(row.get("archive_state") or "transient"),
            str(row.get("eligibility_state") or "ineligible"),
            json.dumps(list(row.get("durability_reasons") or []), sort_keys=True),
            json.dumps(list(row.get("eligibility_reasons") or []), sort_keys=True),
            _quality_round(float(row.get("quality_score") or 0.0)),
            str(row.get("source_created_at") or ""),
            str(row.get("source_updated_at") or row.get("source_created_at") or ""),
            json.dumps(dict(row.get("metadata") or {}), sort_keys=True),
            str((row.get("metadata") or {}).get("finalization_id") or ""),
            now,
            now,
        ),
    )


def sync_useful_outputs(db_path: str | None = None) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    conn = get_connection(db_target)
    try:
        task_rows = _task_result_source_rows(conn) if _table_columns(conn, "task_results") else []
        final_rows = _final_response_source_rows(conn) if _table_columns(conn, "finalized_responses") else []
        hive_rows = _hive_post_source_rows(conn) if _table_columns(conn, "hive_posts") and _table_columns(conn, "hive_topics") else []
        # A8 resurrection fence: sources whose governing payload is not
        # AVAILABLE are never re-imported — and any useful_output row a prior
        # sync derived from them is removed, so synchronization can never undo
        # an erasure (M-07 law).
        from core.finalization import (
            AVAILABILITY_AVAILABLE,
            payload_availability_for_finalization_id,
        )

        def _source_unavailable(finalization_id: str) -> bool:
            fid = str(finalization_id or "").strip()
            if not fid:
                return False
            verdict = payload_availability_for_finalization_id(fid)
            return verdict is not None and verdict != AVAILABILITY_AVAILABLE

        # PASS-002 resurrection fence extension (T09/T10): the lineage fence
        # above covers stamped sources only. task_results/hive_posts bodies
        # are legacy-shaped derivative text — their availability is resolved
        # by content hash, and BOTH a withheld/erased verdict AND an
        # unreadable governance store block re-import (and delete any
        # previously derived row).
        from core.finalization import writer_may_persist_text

        def _commit_time_veto(text: str) -> bool:
            """PASS003 (CE06) ERASE-dominance, split into its two real halves.

            WAITING POLICY (this function): the ordinary availability verdict,
            taken with NO write lock held. It opens its own connection, which is
            exactly why it cannot run inside this lane's BEGIN IMMEDIATE: a nested
            connection contends with the transaction that opened it, and the whole
            turn then races a 5 s busy timeout it did not need to race.

            PRIVACY SAFETY (`_unserveable_now`, below): re-asked INSIDE the
            transaction on the SAME connection, immediately before the write. That
            is what closes the check-then-write window, and it closes it with a
            read that cannot deadlock against its own writer.

            Store outage fails closed.
            """
            try:
                return not writer_may_persist_text(str(text or ""))
            except Exception:
                return True

        def _unserveable_now(text: str) -> bool:
            """The in-transaction guard, on this lane's own connection.

            Conservative: it may refuse more than the full law, never less. A
            store that cannot answer refuses.
            """
            from core.finalization import text_is_unserveable_on

            try:
                return text_is_unserveable_on(conn, str(text or ""))
            except Exception:
                return True

        def _begin_serialized() -> None:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")

        for row in task_rows:
            candidate = _task_useful_row(row)
            text = str(candidate.get("output_text") or "")
            # Verdict first, holding NO write lock (waiting policy).
            refused = not writer_may_persist_text(text) or _commit_time_veto(text)
            _begin_serialized()
            # Re-asked inside the transaction, same connection (privacy safety).
            if refused or _unserveable_now(text):
                conn.execute(
                    "DELETE FROM useful_outputs WHERE source_type = ? AND source_id = ?",
                    ("task_result", str(candidate.get("source_id") or "")),
                )
            else:
                _upsert_useful_output(conn, candidate)
            conn.commit()
        for row in hive_rows:
            candidate = _hive_post_useful_row(row)
            text = str(candidate.get("output_text") or "")
            refused = not writer_may_persist_text(text) or _commit_time_veto(text)
            _begin_serialized()
            if refused or _unserveable_now(text):
                conn.execute(
                    "DELETE FROM useful_outputs WHERE source_type = ? AND source_id = ?",
                    ("hive_post", str(candidate.get("source_id") or "")),
                )
            else:
                _upsert_useful_output(conn, candidate)
            conn.commit()
        for row in final_rows:
            candidate = _final_response_useful_row(row)
            text = str(candidate.get("output_text") or "")
            refused = (
                _source_unavailable(row.get("finalization_id"))
                or not writer_may_persist_text(text)
                or _commit_time_veto(text)
            )
            _begin_serialized()
            if refused or _unserveable_now(text):
                conn.execute(
                    "DELETE FROM useful_outputs WHERE finalization_id = ?",
                    (str(row.get("finalization_id") or "").strip(),),
                )
            else:
                _upsert_useful_output(conn, candidate)
            conn.commit()
    finally:
        conn.close()
    return summarize_useful_outputs(db_target)


def list_useful_outputs(
    *,
    db_path: str | None = None,
    source_types: list[str] | tuple[str, ...] | None = None,
    eligibility_state: str | None = None,
    archive_state: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    conn = get_connection(db_target)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if source_types:
            clean = [str(item).strip() for item in source_types if str(item).strip()]
            if clean:
                clauses.append(f"source_type IN ({','.join('?' for _ in clean)})")
                params.extend(clean)
        if eligibility_state:
            clauses.append("eligibility_state = ?")
            params.append(str(eligibility_state))
        if archive_state:
            clauses.append("archive_state = ?")
            params.append(str(archive_state))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT useful_output_id, source_type, source_id, task_id, topic_id, claim_id, result_id,
                   artifact_ids_json, instruction_text, output_text, summary, acceptance_state,
                   review_state, archive_state, eligibility_state, durability_reasons_json,
                   eligibility_reasons_json, quality_score, source_created_at, source_updated_at,
                   metadata_json, created_at, updated_at
            FROM useful_outputs
            {where}
            ORDER BY quality_score DESC, source_updated_at DESC
            LIMIT ?
            """,
            tuple([*params, max(1, int(limit))]),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["artifact_ids"] = _json_loads(item.pop("artifact_ids_json", "[]"), [])
        item["durability_reasons"] = _json_loads(item.pop("durability_reasons_json", "[]"), [])
        item["eligibility_reasons"] = _json_loads(item.pop("eligibility_reasons_json", "[]"), [])
        item["metadata"] = _json_loads(item.pop("metadata_json", "{}"), {})
        out.append(item)
    return out


def summarize_useful_outputs(db_path: str | None = None) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    conn = get_connection(db_target)
    try:
        rows = conn.execute(
            """
            SELECT source_type, eligibility_state, archive_state, quality_score,
                   durability_reasons_json, eligibility_reasons_json
            FROM useful_outputs
            """
        ).fetchall()
    finally:
        conn.close()
    source_counts: dict[str, int] = {}
    eligible_source_counts: dict[str, int] = {}
    archive_state_counts: dict[str, int] = {}
    ineligible_reasons: dict[str, int] = {}
    durability_reasons: dict[str, int] = {}
    training_eligible_count = 0
    high_signal_count = 0
    archive_candidate_count = 0
    proof_backed_count = 0
    finalized_task_result_count = 0
    commons_reviewed_count = 0
    for row in rows:
        source_type = str(row["source_type"] or "unknown")
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        eligibility_state = str(row["eligibility_state"] or "ineligible")
        archive_state = str(row["archive_state"] or "transient")
        archive_state_counts[archive_state] = archive_state_counts.get(archive_state, 0) + 1
        quality = float(row["quality_score"] or 0.0)
        row_durability = {str(item) for item in _json_loads(row["durability_reasons_json"], [])}
        if eligibility_state == "eligible":
            training_eligible_count += 1
            eligible_source_counts[source_type] = eligible_source_counts.get(source_type, 0) + 1
        for reason in _json_loads(row["eligibility_reasons_json"], []):
            ineligible_reasons[str(reason)] = ineligible_reasons.get(str(reason), 0) + 1
        for reason in row_durability:
            durability_reasons[str(reason)] = durability_reasons.get(str(reason), 0) + 1
        if quality >= 0.72 and eligibility_state == "eligible":
            high_signal_count += 1
        if archive_state in {"candidate", "approved"}:
            archive_candidate_count += 1
        if "proof_backed" in row_durability:
            proof_backed_count += 1
        if source_type == "task_result" and "proof_finalized" in row_durability:
            finalized_task_result_count += 1
        if source_type == "hive_post" and "promotion_review_approved" in row_durability:
            commons_reviewed_count += 1
    structured_total = sum(source_counts.get(name, 0) for name in ("task_result", "final_response", "hive_post"))
    return {
        "generated_at": _utcnow(),
        "total_count": len(rows),
        "structured_total": structured_total,
        "training_eligible_count": training_eligible_count,
        "high_signal_count": high_signal_count,
        "archive_candidate_count": archive_candidate_count,
        "proof_backed_count": proof_backed_count,
        "finalized_task_result_count": finalized_task_result_count,
        "commons_reviewed_count": commons_reviewed_count,
        "source_counts": source_counts,
        "eligible_source_counts": eligible_source_counts,
        "archive_state_counts": archive_state_counts,
        "durability_reasons": durability_reasons,
        "ineligible_reasons": ineligible_reasons,
    }
