from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from storage.db import get_connection


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _insert_local_task(task_id: str = "task-123", session_id: str = "session-123") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO local_tasks (
                task_id, session_id, task_class, task_summary, redacted_input_hash,
                environment_os, environment_shell, environment_runtime, environment_version_hint,
                plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            ) VALUES (
                ?, ?, 'research', 'Summarize Liquefy-backed proof receipts', 'hash-local',
                'macOS', 'zsh', 'python', '3.9',
                'default', 'local_only', 0.42, 'pending', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (task_id, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def _sample_shard(shard_id: str = "shard-123") -> dict[str, object]:
    return {
        "shard_id": shard_id,
        "schema_version": 1,
        "problem_class": "operator_action",
        "problem_signature": "sig-123",
        "summary": "Verified bounded workspace edit loop",
        "resolution_pattern": {"steps": ["inspect", "edit", "verify"]},
        "environment_tags": ["macos", "zsh", "python"],
        "source_type": "local_task",
        "source_node_id": "agent:vool",
        "quality_score": 0.91,
        "trust_score": 0.88,
        "risk_flags": [],
        "freshness_ts": "2026-03-24T12:00:00+00:00",
        "expires_ts": None,
        "signature": "sig-local-123",
    }


def test_task_persistence_support_updates_local_task_rows() -> None:
    _insert_local_task()
    agent = _build_agent()

    agent._update_task_class("task-123", "builder")
    agent._update_task_result("task-123", outcome="success", confidence=1.4)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT task_class, outcome, confidence FROM local_tasks WHERE task_id = ?",
            ("task-123",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert str(row["task_class"]) == "builder"
    assert str(row["outcome"]) == "success"
    assert float(row["confidence"]) == 1.0


def test_promote_verified_action_shard_builds_and_stores_validated_shard() -> None:
    _insert_local_task()
    agent = _build_agent()
    shard = _sample_shard()

    with mock.patch(
        "core.agent_runtime.task_persistence_support.from_task_result",
        return_value=shard,
    ) as from_task_result, mock.patch(
        "core.agent_runtime.task_persistence_support.policy_engine.validate_learned_shard",
        return_value=True,
    ) as validate_learned_shard, mock.patch.object(agent, "_store_local_shard") as store_local_shard:
        agent._promote_verified_action_shard("task-123", SimpleNamespace(confidence=0.7))

    validate_learned_shard.assert_called_once_with(shard)
    store_local_shard.assert_called_once_with(
        shard,
        origin_task_id="task-123",
        origin_session_id="session-123",
    )
    outcome = from_task_result.call_args.args[2]
    assert outcome.is_success is True
    assert outcome.is_durable is True
    assert outcome.confidence_before == 0.7
    assert outcome.confidence_after == 0.75


def test_store_local_shard_downgrades_share_scope_when_privacy_gate_blocks_outbound() -> None:
    agent = _build_agent()
    shard = _sample_shard("shard-privacy")

    with mock.patch(
        "core.agent_runtime.task_persistence_support.session_memory_policy",
        return_value={"share_scope": "hive_mind", "restricted_terms": ["secret"]},
    ), mock.patch(
        "core.agent_runtime.task_persistence_support.policy_engine.outbound_shard_validation_errors",
        return_value=["restricted term"],
    ), mock.patch(
        "core.agent_runtime.task_persistence_support.register_local_shard",
        return_value={"manifest_id": "manifest-1"},
    ) as register_local_shard, mock.patch(
        "core.agent_runtime.task_persistence_support.sync_local_learning_shards",
    ) as sync_local_learning_shards, mock.patch(
        "core.agent_runtime.task_persistence_support.audit_logger.log",
    ) as audit_log:
        agent._store_local_shard(
            shard,
            origin_task_id="task-privacy",
            origin_session_id="session-privacy",
        )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT share_scope, restricted_terms_json FROM learning_shards WHERE shard_id = ?",
            ("shard-privacy",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert str(row["share_scope"]) == "local_only"
    assert str(row["restricted_terms_json"]) == '["secret"]'
    register_local_shard.assert_not_called()
    sync_local_learning_shards.assert_called_once()
    audit_log.assert_called_once_with(
        "local_shard_stored",
        target_id="shard-privacy",
        target_type="shard",
        details={
            "problem_class": "operator_action",
            "requested_share_scope": "hive_mind",
            "effective_share_scope": "local_only",
            "privacy_blocked": True,
            "privacy_reasons": ["restricted term"],
        },
    )


def test_store_local_shard_registers_shareable_shard_and_syncs() -> None:
    agent = _build_agent()
    shard = _sample_shard("shard-shareable")

    with mock.patch(
        "core.agent_runtime.task_persistence_support.session_memory_policy",
        return_value={"share_scope": "hive_mind", "restricted_terms": []},
    ), mock.patch(
        "core.agent_runtime.task_persistence_support.policy_engine.outbound_shard_validation_errors",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.task_persistence_support.policy_engine.get",
        side_effect=lambda key, default=None: {
            "shards.marketplace_auto_list": False,
        }.get(key, default),
    ), mock.patch(
        "core.agent_runtime.task_persistence_support.register_local_shard",
        return_value={"manifest_id": "manifest-1"},
    ) as register_local_shard, mock.patch(
        "core.agent_runtime.task_persistence_support.sync_local_learning_shards",
    ) as sync_local_learning_shards:
        agent._store_local_shard(
            shard,
            origin_task_id="task-shareable",
            origin_session_id="session-shareable",
        )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT share_scope FROM learning_shards WHERE shard_id = ?",
            ("shard-shareable",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert str(row["share_scope"]) == "hive_mind"
    register_local_shard.assert_called_once_with("shard-shareable", restricted_terms=[])
    sync_local_learning_shards.assert_called_once()
