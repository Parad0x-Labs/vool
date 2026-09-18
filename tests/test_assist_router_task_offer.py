from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from network import rate_limiter
from network.assist_router import handle_incoming_assist_message
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations
from tests.task_offer_fixtures import build_signed_task_offer_message


class AssistRouterTaskOfferTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        rate_limiter.reset_peer(local_peer_id())
        conn = get_connection()
        try:
            conn.execute("DELETE FROM task_assignments")
            conn.execute("DELETE FROM task_claims")
            conn.execute("DELETE FROM task_capsules")
            conn.execute("DELETE FROM task_offers")
            conn.commit()
        finally:
            conn.close()

    def test_task_offer_is_stored_with_extracted_capsule_refs(self) -> None:
        parent_ref = f"parent-{uuid.uuid4().hex}"
        verification_of = f"task-{uuid.uuid4().hex}"
        raw, offer, capsule = build_signed_task_offer_message(
            parent_task_ref=parent_ref,
            verification_of=verification_of,
        )

        result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.reason, "Offer stored.")

        conn = get_connection()
        try:
            offer_row = conn.execute(
                "SELECT * FROM task_offers WHERE task_id = ?", (offer.task_id,)
            ).fetchone()
            capsule_row = conn.execute(
                "SELECT * FROM task_capsules WHERE capsule_id = ?", (capsule.capsule_id,)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(offer_row)
        self.assertEqual(offer_row["parent_peer_id"], local_peer_id())
        self.assertEqual(offer_row["status"], "open")
        self.assertIsNotNone(capsule_row)
        self.assertEqual(capsule_row["parent_task_ref"], parent_ref)
        self.assertEqual(capsule_row["verification_of_task_id"], verification_of)

    def test_task_claim_after_offer_generates_assignment(self) -> None:
        raw_offer, offer, _capsule = build_signed_task_offer_message()
        result = handle_incoming_assist_message(raw_bytes=raw_offer, source_addr=None)
        self.assertTrue(result.ok, result.reason)

        claim_payload = {
            "claim_id": str(uuid.uuid4()),
            "task_id": offer.task_id,
            "helper_agent_id": local_peer_id(),
            "declared_capabilities": ["research"],
            "current_load": 0,
            "host_group_hint_hash": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        raw_claim = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_CLAIM",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload=claim_payload,
        )

        result = handle_incoming_assist_message(raw_bytes=raw_claim, source_addr=None)

        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.reason, "Claim stored; assignment generated.")
        self.assertEqual(len(result.generated_messages), 1)

        conn = get_connection()
        try:
            claim_row = conn.execute(
                "SELECT * FROM task_claims WHERE task_id = ?", (offer.task_id,)
            ).fetchone()
            assignment_row = conn.execute(
                "SELECT * FROM task_assignments WHERE task_id = ?", (offer.task_id,)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(claim_row)
        self.assertIsNotNone(assignment_row)
        self.assertEqual(assignment_row["helper_peer_id"], local_peer_id())


if __name__ == "__main__":
    unittest.main()
