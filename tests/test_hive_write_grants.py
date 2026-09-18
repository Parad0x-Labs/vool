from __future__ import annotations

import unittest

from core.hive_write_grants import build_hive_write_grant, consume_hive_write_grant
from network.signer import get_local_peer_id
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations


class HiveWriteGrantTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_default_connection()
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM hive_write_grants")
            conn.commit()
        finally:
            conn.close()
        self.peer_id = get_local_peer_id()

    def test_built_grant_round_trips_through_consume(self) -> None:
        payload = {
            "created_by_agent_id": self.peer_id,
            "title": "Grant round-trip",
            "summary": "Grant should validate and persist with the signed timestamps intact.",
            "topic_tags": ["grants"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        }
        grant = build_hive_write_grant(
            granted_to=self.peer_id,
            allowed_paths=["/v1/hive/topics"],
            max_uses=2,
            review_required_by_default=True,
        )

        consumed = consume_hive_write_grant(
            raw_grant=grant,
            target_path="/v1/hive/topics",
            signer_peer_id=self.peer_id,
            payload=payload,
            allowed_issuer_peer_ids={self.peer_id},
        )

        self.assertEqual(consumed.grant_id, grant["grant_id"])
        self.assertTrue(consumed.review_required_by_default)


if __name__ == "__main__":
    unittest.main()
