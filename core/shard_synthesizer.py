from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core import policy_engine
from network.signer import get_local_peer_id, sign


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    return " ".join(value.split())


def _problem_signature(problem_class: str, summary: str, env_tags: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "problem_class": problem_class,
            "summary": _normalize_text(summary),
            "environment_tags": env_tags,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def shard_signable_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": int(_get(payload, "schema_version", 1) or 1),
        "problem_class": str(_get(payload, "problem_class", "unknown") or "unknown"),
        "problem_signature": str(_get(payload, "problem_signature", "") or ""),
        "summary": str(_get(payload, "summary", "") or ""),
        "resolution_pattern": [str(step) for step in list(_get(payload, "resolution_pattern", []) or []) if str(step).strip()],
        "environment_tags": dict(_get(payload, "environment_tags", {}) or {}),
        "source_type": str(_get(payload, "source_type", "unknown") or "unknown"),
        "source_node_id": str(_get(payload, "source_node_id", "") or ""),
        "quality_score": float(_get(payload, "quality_score", 0.0) or 0.0),
        "trust_score": float(_get(payload, "trust_score", 0.0) or 0.0),
        "risk_flags": [str(flag) for flag in list(_get(payload, "risk_flags", []) or []) if str(flag).strip()],
        "freshness_ts": str(_get(payload, "freshness_ts", "") or ""),
        "expires_ts": _get(payload, "expires_ts", None),
    }


def shard_signable_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        shard_signable_body(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _extract_resolution_pattern(plan: Any) -> list[str]:
    raw = _get(plan, "abstract_steps", None) or _get(plan, "steps", None) or []
    out: list[str] = []

    for step in raw:
        if not isinstance(step, str):
            continue
        clean = " ".join(step.strip().split())
        if not clean:
            continue
        out.append(clean[:128])

    if not out:
        out = ["review_problem", "choose_safe_next_step", "validate_result"]

    return out[:32]


def build_generalized_query(task: Any, classification: dict[str, Any]) -> dict[str, Any]:
    env_tags = {
        "os": _get(task, "environment_os", "unknown"),
        "shell": _get(task, "environment_shell", "unknown"),
        "runtime": _get(task, "environment_runtime", "unknown"),
        "framework": "unknown",
        "version_family": _get(task, "environment_version_hint", "unknown"),
    }

    problem_class = classification.get("task_class", "unknown")
    summary = _get(task, "task_summary", "unknown")
    signature = _problem_signature(problem_class, summary, env_tags)

    return {
        "query_id": str(uuid.uuid4()),
        "problem_class": problem_class,
        "problem_signature": signature,
        "environment_tags": env_tags,
        "max_candidates": int(policy_engine.get("system.max_candidates_per_query", 5)),
    }


def from_task_result(task: Any, plan: Any, outcome: Any) -> dict[str, Any]:
    env_tags = {
        "os": _get(task, "environment_os", "unknown"),
        "shell": _get(task, "environment_shell", "unknown"),
        "runtime": _get(task, "environment_runtime", "unknown"),
        "framework": "unknown",
        "version_family": _get(task, "environment_version_hint", "unknown"),
    }

    problem_class = _get(task, "task_class", "unknown")
    summary = str(_get(plan, "summary", _get(task, "task_summary", "generalized_task")))[:512]
    resolution_pattern = _extract_resolution_pattern(plan)
    risk_flags = list(dict.fromkeys(_get(plan, "risk_flags", []) or []))

    confidence = float(_get(plan, "confidence", 0.5))
    quality_score = max(0.0, min(1.0, confidence))
    trust_score = 0.25 if policy_engine.get("shards.new_shards_start_untrusted", True) else 0.60

    now = _utcnow()
    expiry_days = int(policy_engine.get("shards.default_expiry_days", 90))
    expires_at = now + timedelta(days=expiry_days)

    body = {
        "schema_version": int(policy_engine.get("shards.require_schema_version", 1)),
        "problem_class": problem_class,
        "problem_signature": _problem_signature(problem_class, _get(task, "task_summary", ""), env_tags),
        "summary": summary,
        "resolution_pattern": resolution_pattern,
        "environment_tags": env_tags,
        "source_type": "local_generated",
        "source_node_id": get_local_peer_id(),
        "quality_score": quality_score,
        "trust_score": trust_score,
        "risk_flags": risk_flags,
        "freshness_ts": _iso(now),
        "expires_ts": _iso(expires_at),
    }

    canonical = shard_signable_bytes(body)
    shard_id = hashlib.sha256(canonical).hexdigest()
    signature = sign(canonical)

    return {
        "shard_id": shard_id,
        **body,
        "signature": signature,
    }
