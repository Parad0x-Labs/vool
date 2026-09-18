from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.knowledge_registry import register_local_shard, sync_local_learning_shards
from core.persistent_memory import session_memory_policy
from core.shard_synthesizer import from_task_result
from storage.db import get_connection


class TaskPersistenceSupportMixin:
    def _update_task_class(self, task_id: str, task_class: str) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE local_tasks
                SET task_class = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (task_class, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _promote_verified_action_shard(self, task_id: str, plan: Any) -> None:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT task_id, session_id, task_class, task_summary, environment_os, environment_shell,
                       environment_runtime, environment_version_hint
                FROM local_tasks
                WHERE task_id = ?
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return

        task_view = type("TaskView", (), dict(row))()
        outcome = type(
            "ActionOutcome",
            (),
            {
                "status": "success",
                "is_success": True,
                "is_durable": True,
                "harmful_flag": False,
                "confidence_before": float(plan.confidence),
                "confidence_after": min(1.0, float(plan.confidence) + 0.05),
            },
        )()
        shard = from_task_result(task_view, plan, outcome)
        if policy_engine.validate_learned_shard(shard):
            self._store_local_shard(
                shard,
                origin_task_id=task_id,
                origin_session_id=str(getattr(task_view, "session_id", "") or ""),
            )

    def _update_task_result(self, task_id: str, *, outcome: str, confidence: float) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE local_tasks
                SET outcome = ?,
                    confidence = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    str(outcome),
                    max(0.0, min(1.0, float(confidence))),
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _store_local_shard(
        self,
        shard: dict[str, Any],
        *,
        origin_task_id: str | None = None,
        origin_session_id: str | None = None,
    ) -> None:
        policy = session_memory_policy(origin_session_id)
        requested_share_scope = str(policy.get("share_scope") or "local_only")
        restricted_terms = list(policy.get("restricted_terms") or [])
        effective_share_scope = requested_share_scope
        outbound_reasons: list[str] = []
        if requested_share_scope != "local_only":
            outbound_reasons = policy_engine.outbound_shard_validation_errors(
                shard,
                share_scope=requested_share_scope,
                restricted_terms=restricted_terms,
            )
            if outbound_reasons:
                effective_share_scope = "local_only"

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_task_id, origin_session_id, share_scope,
                    restricted_terms_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                    'active', ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM learning_shards WHERE shard_id = ?), CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    shard["shard_id"],
                    int(shard["schema_version"]),
                    shard["problem_class"],
                    shard["problem_signature"],
                    shard["summary"],
                    json.dumps(shard["resolution_pattern"], sort_keys=True),
                    json.dumps(shard["environment_tags"], sort_keys=True),
                    shard["source_type"],
                    shard["source_node_id"],
                    float(shard["quality_score"]),
                    float(shard["trust_score"]),
                    json.dumps(shard["risk_flags"], sort_keys=True),
                    shard["freshness_ts"],
                    shard["expires_ts"],
                    shard["signature"],
                    str(origin_task_id or ""),
                    str(origin_session_id or ""),
                    effective_share_scope,
                    json.dumps(restricted_terms, sort_keys=True),
                    shard["shard_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

        audit_logger.log(
            "local_shard_stored",
            target_id=shard["shard_id"],
            target_type="shard",
            details={
                "problem_class": shard["problem_class"],
                "requested_share_scope": requested_share_scope,
                "effective_share_scope": effective_share_scope,
                "privacy_blocked": bool(outbound_reasons),
                "privacy_reasons": outbound_reasons,
            },
        )
        if effective_share_scope != "local_only":
            manifest = register_local_shard(str(shard["shard_id"]), restricted_terms=restricted_terms)
            if not manifest:
                audit_logger.log(
                    "local_shard_kept_candidate_only",
                    target_id=shard["shard_id"],
                    target_type="shard",
                    details={"reason": "shareability_gate_blocked"},
                )
            elif policy_engine.get("shards.marketplace_auto_list", False):
                with contextlib.suppress(Exception):
                    from core.knowledge_marketplace import publish_listing
                    from network.signer import get_local_peer_id as _mp_peer

                    publish_listing(
                        shard_id=str(shard["shard_id"]),
                        seller_peer_id=_mp_peer(),
                        title=str(shard.get("summary", ""))[:128] or shard["problem_class"],
                        description=str(shard.get("summary", "")),
                        domain_tags=[shard["problem_class"]],
                        price_credits=float(policy_engine.get("shards.marketplace_default_price", 1.0)),
                        quality_score=float(shard.get("quality_score", 0.5)),
                    )
        with contextlib.suppress(Exception):
            sync_local_learning_shards()
