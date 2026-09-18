from __future__ import annotations

import unittest

from core.runtime_task_rail_client import RUNTIME_TASK_RAIL_CLIENT_SCRIPT
from core.runtime_task_rail_document import render_runtime_task_rail_document
from core.runtime_task_rail_event_render import RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
from core.runtime_task_rail_polling import RUNTIME_TASK_RAIL_POLLING_SCRIPT


class RuntimeTaskRailSplitTests(unittest.TestCase):
    def test_document_renders_shell_and_client_payload(self) -> None:
        html = render_runtime_task_rail_document(client_script="console.log('trace');")

        self.assertIn("VOOL Trace Rail", html)
        self.assertIn("VOOL Task Rail", html)
        self.assertIn("Selected-step center", html)
        self.assertIn("console.log('trace');", html)
        self.assertNotIn("__RUNTIME_TASK_RAIL_STYLES__", html)
        self.assertNotIn("__RUNTIME_TASK_RAIL_SHELL__", html)

    def test_client_is_composed_from_render_and_polling_seams(self) -> None:
        self.assertIn("function renderEvents()", RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT)
        self.assertIn("async function fetchSessions()", RUNTIME_TASK_RAIL_POLLING_SCRIPT)
        self.assertIn("function renderEvents()", RUNTIME_TASK_RAIL_CLIENT_SCRIPT)
        self.assertIn("async function fetchSessions()", RUNTIME_TASK_RAIL_CLIENT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
