from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone

from core.task_reassembler import _get_child_offers
from storage.db import get_connection
from storage.migrations import run_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskReassemblerParentRefTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM task_capsules")
            conn.execute("DELETE FROM task_offers")
            conn.commit()
        finally:
            conn.close()

    def test_child_offer_lookup_prefers_exact_parent_ref(self) -> None:
        parent_full = f"parent-{uuid.uuid4().hex}"
        parent_other = f"{parent_full[:-1]}X"
        task_a = f"task-{uuid.uuid4().hex}"
        task_b = f"task-{uuid.uuid4().hex}"
        now = _now()

        def _insert_offer(conn, task_id: str) -> None:
            conn.execute(
                """
                INSERT INTO task_offers (
                    task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                    input_capsule_hash, required_capabilities_json, reward_hint_json,
                    max_helpers, priority, deadline_ts, status, created_at, updated_at
                ) VALUES (?, 'parent-peer', ?, 'research', 'sub', 'summary', 'hash', '[]', '{}', 1, 'normal', ?, 'open', ?, ?)
                """,
                (task_id, f"cap-{task_id}", now, now, now),
            )

        conn = get_connection()
        try:
            _insert_offer(conn, task_a)
            _insert_offer(conn, task_b)
            conn.execute(
                """
                INSERT INTO task_capsules (
                    capsule_id, task_id, parent_peer_id, capsule_hash, capsule_json, parent_task_ref, verification_of_task_id, created_at, updated_at
                ) VALUES (?, ?, 'parent-peer', 'hash-a', ?, ?, NULL, ?, ?)
                """,
                (
                    f"cap-{task_a}",
                    task_a,
                    json.dumps({"sanitized_context": {"known_constraints": [f"parent_task_ref:{parent_full}"]}}),
                    parent_full,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_capsules (
                    capsule_id, task_id, parent_peer_id, capsule_hash, capsule_json, parent_task_ref, verification_of_task_id, created_at, updated_at
                ) VALUES (?, ?, 'parent-peer', 'hash-b', ?, ?, NULL, ?, ?)
                """,
                (
                    f"cap-{task_b}",
                    task_b,
                    json.dumps({"sanitized_context": {"known_constraints": [f"parent_task_ref:{parent_other}"]}}),
                    parent_other,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        offers = _get_child_offers(parent_full)
        ids = {item["task_id"] for item in offers}
        self.assertEqual(ids, {task_a})


if __name__ == "__main__":
    unittest.main()
