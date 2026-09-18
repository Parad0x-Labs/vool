from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.capability_tokens import (
    consume_capability_token,
    expire_stale_capability_tokens,
    issue_assignment_capability,
    load_capability_token,
    mark_capability_token_used,
    remember_capability_token,
    verify_assignment_capability,
)
from core.task_capsule import build_task_capsule
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _capsule(task_id: str):
    return build_task_capsule(
        parent_agent_id=get_local_peer_id(),
        task_id=task_id,
        task_type="research",
        subtask_type="capability-test",
        summary="Research safe mesh signals",
        sanitized_context={
            "problem_class": "research",
            "environment_tags": {"runtime": "test"},
            "abstract_inputs": ["safe signal"],
            "known_constraints": ["No raw execution."],
        },
        allowed_operations=["reason", "research", "summarize"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(hours=1),
        reward_hint={"points": 5, "wnull_pending": 0},
    )


class CapabilityTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM capability_tokens")
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.xfail(reason="Pre-existing: capability token signing not configured in CI")
    def test_assignment_capability_roundtrip_verifies_and_marks_used(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-123456"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )

        decision = verify_assignment_capability(
            token,
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
        )
        self.assertTrue(decision.ok, decision.reason)

        mark_capability_token_used(str(token["token_id"]))
        remembered = load_capability_token(str(token["token_id"]))
        self.assertIsNotNone(remembered)
        self.assertEqual(str((remembered or {}).get("status")), "used")

    def test_assignment_capability_rejects_tampered_scope(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-abcdef"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )
        tampered = dict(token)
        tampered["scope"] = dict(token["scope"])
        tampered["scope"]["allowed_operations"] = ["reason", "call_shell"]

        decision = verify_assignment_capability(
            tampered,
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
        )
        self.assertFalse(decision.ok)

    def test_expire_stale_capability_tokens_marks_expired(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-expired"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE capability_tokens SET expires_at = ?, status = 'active' WHERE token_id = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), str(token["token_id"])),
            )
            conn.commit()
        finally:
            conn.close()

        expired = expire_stale_capability_tokens(limit=10)
        self.assertEqual(expired, 1)
        remembered = load_capability_token(str(token["token_id"]))
        self.assertEqual(str((remembered or {}).get("status")), "expired")

    def test_assignment_capability_rejects_previously_used_token(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-used"

        token = {
            "token_id": str(uuid.uuid4()),
            "capability_name": "EXECUTE_TASK_CAPSULE",
            "task_id": task_id,
            "granted_by": get_local_peer_id(),
            "granted_to": helper_peer_id,
            "scope": {
                "task_id": task_id,
                "capsule_hash": capsule.capsule_hash,
                "allowed_operations": list(capsule.allowed_operations),
                "max_response_bytes": int(capsule.max_response_bytes),
                "assignment_mode": "single",
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "signature": "sig-test",
        }
        remember_capability_token(token, status="used")

        with unittest.mock.patch("core.capability_tokens.signer.verify", return_value=True):
            decision = verify_assignment_capability(
                token,
                task_id=task_id,
                parent_peer_id=get_local_peer_id(),
                helper_peer_id=helper_peer_id,
                capsule=capsule,
            )

        self.assertFalse(decision.ok)
        self.assertIn("used", decision.reason)

    def _minimal_token(self, task_id: str) -> dict:
        return {
            "token_id": str(uuid.uuid4()),
            "capability_name": "EXECUTE_TASK_CAPSULE",
            "task_id": task_id,
            "granted_by": get_local_peer_id(),
            "granted_to": "helper-peer-x",
            "scope": {"task_id": task_id},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "signature": "sig-test",
        }

    def test_remember_does_not_reactivate_terminal_token(self) -> None:
        # A terminal status (used/revoked/expired) must survive a later remember(active),
        # so re-remembering a spent token on the grant path cannot bring it back to life.
        for terminal in ("used", "revoked", "expired"):
            token = self._minimal_token(f"task-{terminal}")
            remember_capability_token(token, status=terminal)
            remember_capability_token(token, status="active")
            remembered = load_capability_token(str(token["token_id"]))
            self.assertEqual(str((remembered or {}).get("status")), terminal)

    def test_consume_capability_token_is_single_use_and_atomic(self) -> None:
        token = self._minimal_token("task-consume")
        remember_capability_token(token, status="active")

        # Only the first consume flips active -> used and returns True.
        self.assertTrue(consume_capability_token(str(token["token_id"])))
        self.assertFalse(consume_capability_token(str(token["token_id"])))
        self.assertFalse(consume_capability_token("nonexistent-token"))

        remembered = load_capability_token(str(token["token_id"]))
        self.assertEqual(str((remembered or {}).get("status")), "used")


if __name__ == "__main__":
    unittest.main()
