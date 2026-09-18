from __future__ import annotations

import unittest

from core.web.meet.write_limits import reserve_meet_write_rate_limit
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations


class MeetWriteLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_default_connection()
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM meet_write_rate_limit_events")
            conn.commit()
        finally:
            conn.close()

    def test_reserve_meet_write_rate_limit_exhausts_bucket(self) -> None:
        first = reserve_meet_write_rate_limit("signed:peer-1", 2, now_ts=100.0)
        second = reserve_meet_write_rate_limit("signed:peer-1", 2, now_ts=101.0)
        blocked = reserve_meet_write_rate_limit("signed:peer-1", 2, now_ts=102.0)

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "rate_limit_exceeded")

    def test_reserve_meet_write_rate_limit_reopens_after_window(self) -> None:
        first = reserve_meet_write_rate_limit("signed:peer-2", 1, now_ts=100.0)
        blocked = reserve_meet_write_rate_limit("signed:peer-2", 1, now_ts=120.0)
        reopened = reserve_meet_write_rate_limit("signed:peer-2", 1, now_ts=161.0)

        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertTrue(reopened.allowed)


if __name__ == "__main__":
    unittest.main()
