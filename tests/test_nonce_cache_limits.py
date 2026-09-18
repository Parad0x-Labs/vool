from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from network.protocol import prune_nonce_cache
from storage.db import get_connection
from storage.migrations import run_migrations


class NonceCacheLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM nonce_cache")
            conn.commit()
        finally:
            conn.close()

    def test_prune_nonce_cache_honors_max_rows(self) -> None:
        peer_id = "peer-nonce-limit"
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conn = get_connection()
        try:
            for idx in range(25):
                seen_at = (base + timedelta(seconds=idx)).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
                    (peer_id, f"nonce-{idx}", seen_at),
                )
            conn.commit()
            removed = prune_nonce_cache(conn=conn, max_age_hours=9999, max_rows=10)
            conn.commit()
            row = conn.execute("SELECT COUNT(*) AS cnt FROM nonce_cache").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row["cnt"]), 10)
            self.assertGreaterEqual(removed, 15)
        finally:
            conn.close()

    def test_prune_nonce_cache_honors_age_cutoff(self) -> None:
        peer_id = "peer-nonce-age"
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=72)).isoformat()
        new_ts = (now - timedelta(hours=2)).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
                (peer_id, "old-nonce", old_ts),
            )
            conn.execute(
                "INSERT OR IGNORE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
                (peer_id, "new-nonce", new_ts),
            )
            conn.commit()
            prune_nonce_cache(conn=conn, max_age_hours=24, max_rows=1000)
            conn.commit()
            rows = conn.execute("SELECT nonce FROM nonce_cache").fetchall()
            values = {str(r["nonce"]) for r in rows}
            self.assertNotIn("old-nonce", values)
            self.assertIn("new-nonce", values)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
