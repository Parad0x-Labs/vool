from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.knowledge_freshness import DEFAULT_KNOWLEDGE_TTL_SECONDS, expires_at, iso_now
from core.liquefy_bridge import load_packed_bytes, pack_bytes_artifact
from core.privacy_guard import normalize_share_scope, share_scope_is_public, tokenize_restricted_terms
from network.signer import get_local_peer_id
from storage.cas import get_bytes, put_bytes
from storage.db import get_connection
from storage.knowledge_manifests import all_manifests, manifest_for_shard, upsert_manifest
from storage.replica_table import holders_for_shard, mark_holder_withdrawn, upsert_holder

_TAG_TOKEN_RE = re.compile(r"[a-z0-9_\\-]+")


@dataclass(frozen=True)
class ShareableKnowledgeDecision:
    status: str
    can_promote: bool
    score: float
    reason: str
    missing_requirements: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_promote": self.can_promote,
            "score": self.score,
            "reason": self.reason,
            "missing_requirements": list(self.missing_requirements),
            "metrics": dict(self.metrics),
        }


def _summary_digest(summary: str) -> str:
    return hashlib.sha256((summary or "").strip().lower().encode("utf-8")).hexdigest()[:24]


def summary_digest_for_text(summary: str) -> str:
    return _summary_digest(summary)


def _topic_tags(problem_class: str, summary: str) -> list[str]:
    tags = {problem_class.strip().lower()} if problem_class else set()
    for token in _TAG_TOKEN_RE.findall((summary or "").lower()):
        if len(token) >= 4 and token not in {"with", "from", "that", "this", "have", "will"}:
            tags.add(token)
        if len(tags) >= 8:
            break
    return sorted(tag for tag in tags if tag)[:8]


def _shard_row(shard_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM learning_shards WHERE shard_id = ? LIMIT 1",
            (shard_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _json_value(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback
    if raw is None:
        return fallback
    return raw


def _parse_iso(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolution_pattern_from_row(row: dict[str, Any]) -> list[str]:
    values = _json_value(row.get("resolution_pattern_json"), [])
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if isinstance(item, str)]


def _shard_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "shard_id": str(row.get("shard_id") or ""),
        "schema_version": int(row.get("schema_version") or 1),
        "problem_class": str(row.get("problem_class") or "unknown"),
        "problem_signature": str(row.get("problem_signature") or ""),
        "summary": str(row.get("summary") or ""),
        "resolution_pattern": list(_json_value(row.get("resolution_pattern_json"), [])),
        "environment_tags": _json_value(row.get("environment_tags_json"), {}),
        "quality_score": float(row.get("quality_score") or 0.0),
        "trust_score": float(row.get("trust_score") or 0.0),
        "risk_flags": list(_json_value(row.get("risk_flags_json"), [])),
        "freshness_ts": str(row.get("freshness_ts") or ""),
        "expires_ts": row.get("expires_ts"),
        "signature": str(row.get("signature") or ""),
    }


def _canonical_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    public_payload = _shard_payload_from_row(row)
    public_payload.update(
        {
            "source_type": str(row.get("source_type") or ""),
            "source_node_id": str(row.get("source_node_id") or ""),
            "origin_task_id": str(row.get("origin_task_id") or ""),
            "origin_session_id": str(row.get("origin_session_id") or ""),
            "share_scope": normalize_share_scope(str(row.get("share_scope") or "local_only")),
            "restricted_terms": tokenize_restricted_terms(list(_json_value(row.get("restricted_terms_json"), []))),
        }
    )
    return public_payload


def _knowledge_utility_score(
    *,
    summary: str,
    quality_score: float,
    trust_score: float,
    resolution_steps: int = 0,
    validation_count: int = 0,
    failure_count: int = 0,
) -> float:
    summary_tokens = len(_TAG_TOKEN_RE.findall((summary or "").lower()))
    token_bonus = min(summary_tokens, 12) / 12.0 * 0.08
    resolution_bonus = min(max(0, int(resolution_steps)), 4) / 4.0 * 0.08
    validation_bonus = min(max(0, int(validation_count)), 4) / 4.0 * 0.16
    failure_penalty = min(max(0, int(failure_count)), 3) / 3.0 * 0.18
    return round(
        min(
            1.0,
            max(0.0, 0.50 * quality_score + 0.26 * trust_score + token_bonus + resolution_bonus + validation_bonus - failure_penalty),
        ),
        4,
    )


def evaluate_shareable_knowledge(row: dict[str, Any]) -> ShareableKnowledgeDecision:
    summary = str(row.get("summary") or "")
    share_scope = normalize_share_scope(str(row.get("share_scope") or "local_only"))
    quality_score = float(row.get("quality_score") or 0.0)
    trust_score = float(row.get("trust_score") or 0.0)
    validation_count = int(row.get("local_validation_count") or 0)
    failure_count = int(row.get("local_failure_count") or 0)
    resolution_steps = len(_resolution_pattern_from_row(row))
    summary_tokens = len(_TAG_TOKEN_RE.findall(summary.lower()))
    freshness_dt = _parse_iso(row.get("freshness_ts"))
    expires_dt = _parse_iso(row.get("expires_ts"))
    now = datetime.now(timezone.utc)
    freshness_age_days = ((now - freshness_dt).total_seconds() / 86400.0) if freshness_dt else None
    risk_flags = {str(item).strip().lower() for item in list(_json_value(row.get("risk_flags_json"), [])) if str(item).strip()}
    blocked_risk_flags = {
        str(item).strip().lower()
        for item in list(policy_engine.get("knowledge_sharing.blocked_risk_flags", []))
        if str(item).strip()
    }
    utility_score = _knowledge_utility_score(
        summary=summary,
        quality_score=quality_score,
        trust_score=trust_score,
        resolution_steps=resolution_steps,
        validation_count=validation_count,
        failure_count=failure_count,
    )
    default_min_trust = max(
        float(policy_engine.get("trust.min_trust_to_promote_shard", 0.65) or 0.65),
        float(policy_engine.get("knowledge_sharing.min_trust_to_promote", 0.65) or 0.65),
    )
    if share_scope == "hive_mind":
        min_trust = max(
            0.0,
            min(
                1.0,
                float(policy_engine.get("knowledge_sharing.min_trust_to_promote_hive_mind", 0.25) or 0.25),
            ),
        )
    else:
        min_trust = default_min_trust
    missing: list[str] = []
    if str(row.get("quarantine_status") or "active") != "active":
        missing.append("quarantine_status_not_active")
    if expires_dt and expires_dt <= now:
        missing.append("knowledge_expired")
    max_age_days = float(policy_engine.get("knowledge_sharing.max_freshness_age_days", 45) or 45)
    if freshness_age_days is not None and freshness_age_days > max_age_days:
        missing.append("stale_freshness_window")
    if quality_score < float(policy_engine.get("knowledge_sharing.min_quality_to_promote", 0.72) or 0.72):
        missing.append("quality_below_threshold")
    if trust_score < min_trust:
        missing.append("trust_below_threshold")
    if utility_score < float(policy_engine.get("knowledge_sharing.min_utility_to_promote", 0.64) or 0.64):
        missing.append("utility_below_threshold")
    if summary_tokens < int(policy_engine.get("knowledge_sharing.min_summary_tokens", 5) or 5):
        missing.append("summary_too_thin")
    if resolution_steps < int(policy_engine.get("knowledge_sharing.min_resolution_steps", 1) or 1):
        missing.append("missing_resolution_pattern")
    if validation_count < int(policy_engine.get("knowledge_sharing.min_validation_count", 0) or 0):
        missing.append("insufficient_validation_count")
    if failure_count > int(policy_engine.get("knowledge_sharing.max_failure_count", 1) or 1):
        missing.append("failure_count_too_high")
    if risk_flags & blocked_risk_flags:
        missing.append("blocked_risk_flags_present")

    gate_score = round(
        min(
            1.0,
            max(
                0.0,
                0.35 * quality_score
                + 0.20 * trust_score
                + 0.25 * utility_score
                + min(validation_count, 3) / 3.0 * 0.10
                + (0.10 if not (risk_flags & blocked_risk_flags) else 0.0),
            ),
        ),
        4,
    )
    can_promote = not missing
    status = "promoted" if can_promote else "candidate_only"
    if "knowledge_expired" in missing:
        status = "expired"
    elif "quarantine_status_not_active" in missing:
        status = "quarantined"
    return ShareableKnowledgeDecision(
        status=status,
        can_promote=can_promote,
        score=gate_score,
        reason="shareability_gate_passed" if can_promote else "shareability_gate_blocked",
        missing_requirements=missing,
        metrics={
            "share_scope": share_scope,
            "quality_score": quality_score,
            "trust_score": trust_score,
            "utility_score": utility_score,
            "summary_tokens": summary_tokens,
            "resolution_steps": resolution_steps,
            "validation_count": validation_count,
            "failure_count": failure_count,
            "freshness_age_days": None if freshness_age_days is None else round(freshness_age_days, 3),
            "risk_flags": sorted(risk_flags),
        },
    )


def _pack_dense_shard_capsule(row: dict[str, Any]) -> tuple[dict[str, Any], bytes, dict[str, Any], dict[str, Any]]:
    canonical_payload = _canonical_payload_from_row(row)
    canonical_bytes = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    packed = pack_bytes_artifact(
        artifact_id=str(row.get("shard_id") or hashlib.sha256(canonical_bytes).hexdigest()),
        payload=canonical_bytes,
        category="knowledge",
        file_stem=f"knowledge-{hashlib.sha256(canonical_bytes).hexdigest()[:24]}",
        profile="knowledge",
        text_like=True,
    )
    compressed_bytes = bytes(packed["compressed_payload"])
    cas_manifest = put_bytes(compressed_bytes)
    return canonical_payload, canonical_bytes, packed, cas_manifest


def build_manifest_from_learning_shard(row: dict[str, Any], *, gate: ShareableKnowledgeDecision | None = None) -> dict[str, Any]:
    problem_class = str(row.get("problem_class") or "unknown")
    summary = str(row.get("summary") or "")
    share_scope = normalize_share_scope(str(row.get("share_scope") or "local_only"))
    try:
        restricted_terms = json.loads(row.get("restricted_terms_json") or "[]")
    except json.JSONDecodeError:
        restricted_terms = []
    topic_tags = _topic_tags(problem_class, summary)
    version = 1
    quality_score = float(row.get("quality_score") or 0.0)
    trust_score = float(row.get("trust_score") or 0.0)
    resolution_steps = len(_resolution_pattern_from_row(row))
    validation_count = int(row.get("local_validation_count") or 0)
    failure_count = int(row.get("local_failure_count") or 0)
    utility_score = _knowledge_utility_score(
        summary=summary,
        quality_score=quality_score,
        trust_score=trust_score,
        resolution_steps=resolution_steps,
        validation_count=validation_count,
        failure_count=failure_count,
    )
    gate = gate or evaluate_shareable_knowledge(row)
    _, canonical_bytes, packed, cas_manifest = _pack_dense_shard_capsule(row)
    content_hash = str(packed["content_sha256"])
    manifest_id = f"manifest-{content_hash[:24]}"
    metadata = {
        "problem_class": problem_class,
        "source_type": row.get("source_type"),
        "source_node_id": row.get("source_node_id"),
        "quality_score": quality_score,
        "trust_score": trust_score,
        "utility_score": utility_score,
        "freshness_ts": row.get("freshness_ts"),
        "expires_ts": row.get("expires_ts"),
        "home_region": "global",
        "origin_task_id": str(row.get("origin_task_id") or ""),
        "origin_session_id": str(row.get("origin_session_id") or ""),
        "share_scope": share_scope,
        "restricted_terms": tokenize_restricted_terms(list(restricted_terms or [])),
        "dense_storage_backend": str(packed["storage_backend"]),
        "dense_compressed_sha256": str(packed["compressed_sha256"]),
        "dense_raw_bytes": int(packed["raw_bytes"]),
        "dense_compressed_bytes": int(packed["compressed_bytes"]),
        "dense_compression_ratio": float(packed["compression_ratio"]),
        "dense_compression_level": int(packed["compression_level"]),
        "dense_pack_profile": str(packed["profile"]),
        "dense_canonical_format": "learning_shard_v1",
        "dense_storage_policy": "liquefy_canonical_capsule",
        "index_storage_policy": "metadata_only",
        "dense_cas_manifest_id": cas_manifest["manifest_id"],
        "dense_cas_blob_hash": cas_manifest["blob_hash"],
        "dense_cas_chunk_hashes": list(cas_manifest["chunk_hashes"]),
        "dense_cas_chunk_count": len(cas_manifest["chunk_hashes"]),
        "dense_cas_total_bytes": int(cas_manifest["total_bytes"]),
        "cas_manifest_id": cas_manifest["manifest_id"],
        "cas_chunk_hashes": list(cas_manifest["chunk_hashes"]),
        "cas_chunk_count": len(cas_manifest["chunk_hashes"]),
        "cas_total_bytes": int(cas_manifest["total_bytes"]),
        "cas_blob_hash": cas_manifest["blob_hash"],
        "canonical_format": "learning_shard_v1",
        "canonical_raw_bytes": len(canonical_bytes),
        "advertised_size_basis": "dense_compressed_bytes",
        "canonical_status": gate.status,
        "shareability_gate_score": gate.score,
        "shareability_gate_reason": gate.reason,
        "shareability_gate_missing_requirements": list(gate.missing_requirements),
    }
    size_bytes = int(packed["compressed_bytes"])
    return {
        "manifest_id": manifest_id,
        "shard_id": str(row.get("shard_id")),
        "content_hash": content_hash,
        "version": version,
        "topic_tags": topic_tags,
        "summary_digest": _summary_digest(summary),
        "size_bytes": size_bytes,
        "metadata": metadata,
    }


def register_local_shard(
    shard_id: str,
    *,
    ttl_seconds: int = DEFAULT_KNOWLEDGE_TTL_SECONDS,
    restricted_terms: list[str] | None = None,
) -> dict[str, Any] | None:
    row = _shard_row(shard_id)
    if not row:
        return None
    share_scope = normalize_share_scope(str(row.get("share_scope") or "local_only"))
    if not share_scope_is_public(share_scope):
        return None
    try:
        row_restricted_terms = json.loads(row.get("restricted_terms_json") or "[]")
    except json.JSONDecodeError:
        row_restricted_terms = []
    effective_restricted_terms = tokenize_restricted_terms(list(restricted_terms or row_restricted_terms or []))
    shard_payload = {
        "schema_version": int(row.get("schema_version") or 1),
        "problem_class": str(row.get("problem_class") or ""),
        "summary": str(row.get("summary") or ""),
        "resolution_pattern": json.loads(row.get("resolution_pattern_json") or "[]"),
        "risk_flags": json.loads(row.get("risk_flags_json") or "[]"),
    }
    if not policy_engine.validate_outbound_shard(
        shard_payload,
        share_scope=share_scope,
        restricted_terms=effective_restricted_terms,
    ):
        mark_holder_withdrawn(shard_id, get_local_peer_id())
        return None
    decision = evaluate_shareable_knowledge(row)
    if not decision.can_promote:
        mark_holder_withdrawn(shard_id, get_local_peer_id())
        audit_logger.log(
            "shareable_shard_promotion_blocked",
            target_id=shard_id,
            target_type="shard",
            details=decision.to_dict(),
        )
        return None
    manifest = build_manifest_from_learning_shard(row, gate=decision)
    upsert_manifest(**manifest)
    fetch_route = {
        "method": "request_shard",
        "shard_id": manifest["shard_id"],
        "content_hash": manifest["content_hash"],
        "dense_storage_backend": manifest["metadata"].get("dense_storage_backend"),
        "dense_storage_policy": manifest["metadata"].get("dense_storage_policy"),
    }
    upsert_holder(
        shard_id=manifest["shard_id"],
        holder_peer_id=get_local_peer_id(),
        home_region=str(manifest["metadata"].get("home_region") or "global"),
        content_hash=manifest["content_hash"],
        version=manifest["version"],
        freshness_ts=str(row.get("freshness_ts") or iso_now()),
        expires_at=str(row.get("expires_ts") or expires_at(ttl_seconds)),
        access_mode=share_scope,
        fetch_route=fetch_route,
        trust_weight=float(row.get("trust_score") or 0.25),
        status="active",
        source="local",
    )
    audit_logger.log(
        "shareable_shard_promoted",
        target_id=shard_id,
        target_type="shard",
        details=decision.to_dict(),
    )
    return manifest


def sync_local_learning_shards(limit: int = 500) -> int:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT shard_id
            FROM learning_shards
            WHERE quarantine_status = 'active'
              AND COALESCE(share_scope, 'local_only') != 'local_only'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    synced = 0
    for row in rows:
        if register_local_shard(str(row["shard_id"])):
            synced += 1
    return synced


def withdraw_local_shard(shard_id: str) -> bool:
    row = _shard_row(shard_id)
    if not row:
        return False
    mark_holder_withdrawn(shard_id, get_local_peer_id())
    return True


def record_remote_holder(
    *,
    shard_id: str,
    holder_peer_id: str,
    content_hash: str,
    version: int,
    freshness_ts: str,
    ttl_seconds: int,
    topic_tags: list[str],
    summary_digest: str,
    size_bytes: int,
    metadata: dict[str, Any],
    fetch_route: dict[str, Any],
    trust_weight: float,
    home_region: str = "global",
    access_mode: str = "public",
) -> None:
    manifest_id = f"manifest-{content_hash[:24]}"
    merged_metadata = dict(metadata)
    merged_metadata.setdefault("home_region", home_region)
    upsert_manifest(
        manifest_id=manifest_id,
        shard_id=shard_id,
        content_hash=content_hash,
        version=version,
        topic_tags=topic_tags,
        summary_digest=summary_digest,
        size_bytes=size_bytes,
        metadata=merged_metadata,
    )
    upsert_holder(
        shard_id=shard_id,
        holder_peer_id=holder_peer_id,
        home_region=home_region,
        content_hash=content_hash,
        version=version,
        freshness_ts=freshness_ts,
        expires_at=expires_at(ttl_seconds),
        access_mode=access_mode,
        fetch_route=fetch_route,
        trust_weight=trust_weight,
        status="active",
        source="advertised",
    )


def withdraw_holder(shard_id: str, holder_peer_id: str) -> None:
    mark_holder_withdrawn(shard_id, holder_peer_id)


def holders_for_fetch(shard_id: str) -> list[dict[str, Any]]:
    return holders_for_shard(shard_id, active_only=True)


def local_manifest(shard_id: str) -> dict[str, Any] | None:
    return manifest_for_shard(shard_id)


def load_canonical_shareable_shard_payload(shard_id: str) -> dict[str, Any] | None:
    row = _shard_row(shard_id)
    if row:
        if str(row.get("quarantine_status") or "active") != "active":
            return None
        share_scope = normalize_share_scope(str(row.get("share_scope") or "local_only"))
        if not share_scope_is_public(share_scope):
            return None
        if not evaluate_shareable_knowledge(row).can_promote:
            return None
        if not any(holder.get("holder_peer_id") == get_local_peer_id() for holder in holders_for_shard(shard_id, active_only=True)):
            return None
        return _canonical_payload_from_row(row)
    if not any(holder.get("holder_peer_id") == get_local_peer_id() for holder in holders_for_shard(shard_id, active_only=True)):
        return None
    manifest = manifest_for_shard(shard_id)
    if not manifest:
        return None
    metadata = dict(manifest.get("metadata") or {})
    blob_hash = str(metadata.get("dense_cas_blob_hash") or metadata.get("cas_blob_hash") or "").strip()
    storage_backend = str(metadata.get("dense_storage_backend") or "local_archive")
    if not blob_hash:
        return None
    compressed = get_bytes(blob_hash)
    if compressed is None:
        return None
    try:
        raw = load_packed_bytes(payload=compressed, storage_backend=storage_backend)
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "shard_id": str(payload.get("shard_id") or shard_id),
        "schema_version": int(payload.get("schema_version") or 1),
        "problem_class": str(payload.get("problem_class") or "unknown"),
        "problem_signature": str(payload.get("problem_signature") or ""),
        "summary": str(payload.get("summary") or ""),
        "resolution_pattern": list(payload.get("resolution_pattern") or []),
        "environment_tags": payload.get("environment_tags") or {},
        "source_type": str(payload.get("source_type") or ""),
        "source_node_id": str(payload.get("source_node_id") or ""),
        "quality_score": float(payload.get("quality_score") or 0.0),
        "trust_score": float(payload.get("trust_score") or 0.0),
        "risk_flags": list(payload.get("risk_flags") or []),
        "freshness_ts": str(payload.get("freshness_ts") or ""),
        "expires_ts": payload.get("expires_ts"),
        "signature": str(payload.get("signature") or ""),
        "origin_task_id": str(payload.get("origin_task_id") or ""),
        "origin_session_id": str(payload.get("origin_session_id") or ""),
        "share_scope": normalize_share_scope(str(payload.get("share_scope") or "local_only")),
        "restricted_terms": tokenize_restricted_terms(list(payload.get("restricted_terms") or [])),
    }


def load_transportable_shard_payload(shard_id: str) -> dict[str, Any] | None:
    payload = load_canonical_shareable_shard_payload(shard_id)
    if not payload:
        return None
    return {
        "shard_id": str(payload.get("shard_id") or shard_id),
        "schema_version": int(payload.get("schema_version") or 1),
        "problem_class": str(payload.get("problem_class") or "unknown"),
        "problem_signature": str(payload.get("problem_signature") or ""),
        "summary": str(payload.get("summary") or ""),
        "resolution_pattern": list(payload.get("resolution_pattern") or []),
        "environment_tags": dict(payload.get("environment_tags") or {}),
        "source_type": str(payload.get("source_type") or ""),
        "source_node_id": str(payload.get("source_node_id") or ""),
        "quality_score": float(payload.get("quality_score") or 0.0),
        "trust_score": float(payload.get("trust_score") or 0.0),
        "risk_flags": list(payload.get("risk_flags") or []),
        "freshness_ts": str(payload.get("freshness_ts") or ""),
        "expires_ts": payload.get("expires_ts"),
        "signature": str(payload.get("signature") or ""),
    }


def load_shareable_shard_payload(shard_id: str) -> dict[str, Any] | None:
    payload = load_transportable_shard_payload(shard_id)
    if not payload:
        return None
    return {
        "shard_id": str(payload.get("shard_id") or shard_id),
        "schema_version": int(payload.get("schema_version") or 1),
        "problem_class": str(payload.get("problem_class") or "unknown"),
        "problem_signature": str(payload.get("problem_signature") or ""),
        "summary": str(payload.get("summary") or ""),
        "resolution_pattern": list(payload.get("resolution_pattern") or []),
        "environment_tags": dict(payload.get("environment_tags") or {}),
        "quality_score": float(payload.get("quality_score") or 0.0),
        "trust_score": float(payload.get("trust_score") or 0.0),
        "risk_flags": list(payload.get("risk_flags") or []),
        "freshness_ts": str(payload.get("freshness_ts") or ""),
        "expires_ts": payload.get("expires_ts"),
        "signature": str(payload.get("signature") or ""),
    }


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TAG_TOKEN_RE.findall((text or "").lower()) if len(tok) >= 4}


def _freshness_rank(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def find_relevant_remote_shards(problem_class: str, summary: str, *, limit: int = 5) -> list[dict[str, Any]]:
    local_peer = get_local_peer_id()
    query_tokens = _tokenize(problem_class) | _tokenize(summary)
    ranked: list[tuple[float, float, dict[str, Any]]] = []
    for manifest in all_manifests(limit=500):
        tags = set(manifest.get("topic_tags") or [])
        metadata = dict(manifest.get("metadata") or {})
        manifest_problem_class = str(metadata.get("problem_class") or "").strip().lower()
        overlap = len(query_tokens & (tags | _tokenize(manifest_problem_class)))
        exact_problem_match = 1.0 if manifest_problem_class == problem_class.strip().lower() else 0.0
        if not overlap and not exact_problem_match:
            continue
        holders = [holder for holder in holders_for_shard(manifest["shard_id"]) if holder["holder_peer_id"] != local_peer]
        if not holders:
            continue
        best_holder = holders[0]
        freshness_rank = _freshness_rank(str(best_holder.get("freshness_ts") or ""))
        score = exact_problem_match + float(overlap) + float(best_holder.get("trust_weight") or 0.0)
        ranked.append(
            (
                score,
                freshness_rank,
                {
                    "shard_id": manifest["shard_id"],
                    "topic_tags": manifest["topic_tags"],
                    "summary_digest": manifest["summary_digest"],
                    "holder_peer_id": best_holder["holder_peer_id"],
                    "home_region": best_holder.get("home_region") or "global",
                    "fetch_route": best_holder["fetch_route"],
                    "trust_weight": best_holder["trust_weight"],
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:limit]]


def search_swarm_memory_metadata(problem_class: str, summary: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """
    Metadata-only swarm search for canonical shard holders.

    This intentionally does not return candidate model outputs from the local
    candidate lane. Provider output remains separate until it passes the normal
    review and promotion flow.
    """
    local_peer = get_local_peer_id()
    query_tokens = _tokenize(problem_class) | _tokenize(summary)
    ranked: list[tuple[float, float, dict[str, Any]]] = []
    for manifest in all_manifests(limit=500):
        tags = set(manifest.get("topic_tags") or [])
        metadata = dict(manifest.get("metadata") or {})
        manifest_problem_class = str(metadata.get("problem_class") or "").strip().lower()
        overlap = len(query_tokens & (tags | _tokenize(manifest_problem_class)))
        exact_problem_match = 1.0 if manifest_problem_class == problem_class.strip().lower() else 0.0
        if not overlap and not exact_problem_match:
            continue
        holders = [holder for holder in holders_for_shard(manifest["shard_id"]) if holder["holder_peer_id"] != local_peer]
        if not holders:
            continue
        best_holder = holders[0]
        freshness_rank = _freshness_rank(str(best_holder.get("freshness_ts") or ""))
        score = exact_problem_match + float(overlap) + float(best_holder.get("trust_weight") or 0.0)
        ranked.append(
            (
                score,
                freshness_rank,
                {
                    "shard_id": manifest["shard_id"],
                    "manifest_id": manifest.get("manifest_id"),
                    "topic_tags": list(manifest.get("topic_tags") or []),
                    "summary_digest": manifest.get("summary_digest"),
                    "holder_peer_id": best_holder["holder_peer_id"],
                    "home_region": best_holder.get("home_region") or "global",
                    "fetch_route": best_holder["fetch_route"],
                    "trust_weight": float(best_holder.get("trust_weight") or 0.0),
                    "relevance_score": float(score),
                    "freshness_rank": float(freshness_rank),
                    "metadata_only": True,
                    "version": int(best_holder.get("version") or manifest.get("version") or 1),
                    "problem_class": manifest_problem_class or str(metadata.get("problem_class") or "unknown"),
                    "quality_score": float(metadata.get("quality_score") or 0.0),
                    "utility_score": float(metadata.get("utility_score") or 0.0),
                    "canonical_status": str(metadata.get("canonical_status") or ""),
                    "share_scope": str(metadata.get("share_scope") or ""),
                    "origin_task_id": str(metadata.get("origin_task_id") or ""),
                    "manifest_updated_at": str(manifest.get("updated_at") or ""),
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:limit]]
