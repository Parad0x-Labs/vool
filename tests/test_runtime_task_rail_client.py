from __future__ import annotations

import unittest

from core.runtime_task_rail_client import RUNTIME_TASK_RAIL_CLIENT_SCRIPT


class RuntimeTaskRailClientTests(unittest.TestCase):
    def test_client_script_keeps_runtime_polling_contract(self) -> None:
        script = RUNTIME_TASK_RAIL_CLIENT_SCRIPT
        self.assertIn("function buildSummary(session, events)", script)
        self.assertIn("async function fetchSessions()", script)
        self.assertIn("async function fetchEvents(reset = false)", script)
        self.assertIn("/api/runtime/sessions", script)
        self.assertIn("/api/runtime/events", script)
        self.assertIn("selectedStepTitleEl", script)
        self.assertIn("traceRawPanelEl", script)
        self.assertIn("setInterval(tick, 1200);", script)


if __name__ == "__main__":
    unittest.main()
