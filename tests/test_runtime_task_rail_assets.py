from __future__ import annotations

import unittest

from core.runtime_task_rail_assets import (
    RUNTIME_TASK_RAIL_SHELL_HTML,
    RUNTIME_TASK_RAIL_STYLE_BLOCK,
)
from core.runtime_task_rail_event_feed_styles import (
    RUNTIME_TASK_RAIL_EVENT_FEED_STYLES,
)
from core.runtime_task_rail_panel_session_styles import (
    RUNTIME_TASK_RAIL_PANEL_SESSION_STYLES,
)
from core.runtime_task_rail_panel_shell_styles import (
    RUNTIME_TASK_RAIL_PANEL_SHELL_STYLES,
)
from core.runtime_task_rail_panel_styles import RUNTIME_TASK_RAIL_PANEL_STYLES
from core.runtime_task_rail_panel_trace_styles import (
    RUNTIME_TASK_RAIL_PANEL_TRACE_STYLES,
)
from core.runtime_task_rail_shell import RUNTIME_TASK_RAIL_SHELL_HTML as RUNTIME_TASK_RAIL_SHELL_FRAGMENT
from core.runtime_task_rail_styles import RUNTIME_TASK_RAIL_STYLE_BLOCK as RUNTIME_TASK_RAIL_STYLE_FRAGMENT
from core.runtime_task_rail_trace_styles import RUNTIME_TASK_RAIL_TRACE_STYLES
from core.runtime_task_rail_workbench_styles import (
    RUNTIME_TASK_RAIL_WORKBENCH_STYLES,
)


class RuntimeTaskRailAssetsTests(unittest.TestCase):
    def test_assets_facade_reexports_style_and_shell_fragments(self) -> None:
        self.assertIs(RUNTIME_TASK_RAIL_SHELL_HTML, RUNTIME_TASK_RAIL_SHELL_FRAGMENT)
        self.assertIs(RUNTIME_TASK_RAIL_STYLE_BLOCK, RUNTIME_TASK_RAIL_STYLE_FRAGMENT)

    def test_style_and_shell_fragments_keep_trace_workbench_contract(self) -> None:
        self.assertIn(".trace-workbench", RUNTIME_TASK_RAIL_STYLE_BLOCK)
        self.assertIn(".trace-raw-panel", RUNTIME_TASK_RAIL_STYLE_BLOCK)
        self.assertIn("VOOL Task Rail", RUNTIME_TASK_RAIL_SHELL_HTML)
        self.assertIn("Selected-step center", RUNTIME_TASK_RAIL_SHELL_HTML)
        self.assertIn(".panel-header", RUNTIME_TASK_RAIL_PANEL_STYLES)
        self.assertIn(".panel-header", RUNTIME_TASK_RAIL_PANEL_SHELL_STYLES)
        self.assertIn(".session-card", RUNTIME_TASK_RAIL_PANEL_SESSION_STYLES)
        self.assertIn(".trace-stage-head", RUNTIME_TASK_RAIL_PANEL_TRACE_STYLES)
        self.assertIn(".summary-grid", RUNTIME_TASK_RAIL_TRACE_STYLES)
        self.assertIn(".event-card", RUNTIME_TASK_RAIL_EVENT_FEED_STYLES)
        self.assertIn(".trace-workbench", RUNTIME_TASK_RAIL_WORKBENCH_STYLES)


if __name__ == "__main__":
    unittest.main()
