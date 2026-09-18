from __future__ import annotations

import unittest
from unittest.mock import patch

from core.meet_and_greet_service import MeetAndGreetService
from core.web.meet.readiness import build_meet_readiness
from storage.db import reset_default_connection
from storage.migrations import run_migrations


class MeetReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_default_connection()
        run_migrations()
        self.service = MeetAndGreetService()

    def test_build_meet_readiness_reports_ready(self) -> None:
        readiness = build_meet_readiness(self.service)

        self.assertEqual(readiness.status, "ready")
        self.assertEqual(readiness.checks["db"], "ok")
        self.assertEqual(readiness.checks["tables"], "ok")

    def test_build_meet_readiness_reports_not_ready_when_snapshot_fails(self) -> None:
        with patch.object(self.service, "health", side_effect=RuntimeError("snapshot exploded")):
            readiness = build_meet_readiness(self.service)

        self.assertEqual(readiness.status, "not_ready")
        self.assertIn("snapshot", readiness.checks)
        self.assertIn("snapshot exploded", readiness.checks["snapshot"])


if __name__ == "__main__":
    unittest.main()
