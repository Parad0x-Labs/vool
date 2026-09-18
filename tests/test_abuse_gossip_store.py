from __future__ import annotations

import unittest

from storage.abuse_gossip_store import mark_report_seen
from storage.db import get_connection
from storage.migrations import run_migrations


class AbuseGossipStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DROP TABLE IF EXISTS abuse_gossip_seen")
            conn.commit()
        finally:
            conn.close()

    def test_mark_report_seen_is_idempotent(self) -> None:
        first = mark_report_seen("report-12345678")
        second = mark_report_seen("report-12345678")
        self.assertTrue(first)
        self.assertFalse(second)

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT seen_count FROM abuse_gossip_seen WHERE report_id = ? LIMIT 1",
                ("report-12345678",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["seen_count"]), 2)


if __name__ == "__main__":
    unittest.main()

