"""Consensus validator persists its verification task locally, not just broadcasts it.

Before this, decide_consensus_for_task broadcast a verification offer on disagreement but
recorded nothing locally, so `_verification_exists` never saw it (the idempotency guard was
toothless) and "spawns a verification job" was not actually true. These lock in that the
verification task+capsule is stored with verification_of_task_id set.
"""
from __future__ import annotations

import json
import uuid

from core.consensus_validator import _build_verification_offer, _verification_exists
from network.assist_router import _store_task_offer
from storage.db import get_connection
from storage.migrations import run_migrations


def _seed_base_task(task_id: str) -> None:
    capsule_id = f"cap_{task_id}"
    capsule_json = json.dumps(
        {
            "capsule_id": capsule_id,
            "task_id": task_id,
            "summary": "base task summary",
            "sanitized_context": {
                "problem_class": "reasoning",
                "environment_tags": {},
                "abstract_inputs": [],
                "known_constraints": [],
            },
            "allowed_operations": ["reason"],
        }
    )
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                priority, deadline_ts, status, created_at, updated_at
            ) VALUES (?, 'parent', ?, 'reasoning', 'generic', 'base task', 'h', '[]', '{}',
                      2, 'normal', '2030-01-01T00:00:00Z', 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (task_id, capsule_id),
        )
        conn.execute(
            """
            INSERT INTO task_capsules (
                capsule_id, task_id, parent_peer_id, capsule_hash, capsule_json,
                parent_task_ref, verification_of_task_id, created_at, updated_at
            ) VALUES (?, ?, 'parent', 'h', ?, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (capsule_id, task_id, capsule_json),
        )
        conn.commit()
    finally:
        conn.close()


def test_verification_task_is_persisted_with_verification_of_task_id() -> None:
    run_migrations()
    base = f"base-{uuid.uuid4().hex}"
    _seed_base_task(base)

    # No verification job exists yet for the base task.
    assert _verification_exists(base) is False

    built = _build_verification_offer(base)
    assert built is not None
    _vid, offer, capsule = built

    # Persisting the built offer+capsule (what decide_consensus_for_task now does before
    # broadcasting) records a real local job keyed by verification_of_task_id.
    _store_task_offer(offer, capsule)

    assert _verification_exists(base) is True

    # And it is stored in task_capsules with the verification_of_task_id column set.
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT verification_of_task_id FROM task_capsules WHERE verification_of_task_id = ? LIMIT 1",
            (base,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["verification_of_task_id"] == base
