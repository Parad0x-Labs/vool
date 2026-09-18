from __future__ import annotations

import unittest

from core.dashboard.workstation_overview_home_board_items_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_ITEMS_RUNTIME,
)
from core.dashboard.workstation_overview_home_board_render_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_RENDER_RUNTIME,
)
from core.dashboard.workstation_overview_home_board_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME,
)
from core.dashboard.workstation_render_voolbook_feed_layout_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_LAYOUT_STYLES,
)
from core.dashboard.workstation_render_voolbook_feed_post_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_POST_STYLES,
)
from core.dashboard.workstation_render_voolbook_feed_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES,
)


class DashboardWorkstationLeafSplitTests(unittest.TestCase):
    def test_home_board_runtime_is_split_into_item_and_render_helpers(self) -> None:
        self.assertIn("function buildWorkstationHomeBoardItems(data, movement)", WORKSTATION_OVERVIEW_HOME_BOARD_ITEMS_RUNTIME)
        self.assertIn("function renderWorkstationHomeBoardCards(items)", WORKSTATION_OVERVIEW_HOME_BOARD_RENDER_RUNTIME)
        self.assertIn("function renderWorkstationHomeBoard(data, movement)", WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME)
        self.assertIn("dashboard-home-card", WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME)

    def test_voolbook_feed_styles_are_split_into_layout_and_post_helpers(self) -> None:
        self.assertIn(".nb-hero {", WORKSTATION_RENDER_VOOLBOOK_FEED_LAYOUT_STYLES)
        self.assertIn(".nb-post {", WORKSTATION_RENDER_VOOLBOOK_FEED_POST_STYLES)
        self.assertIn(".nb-hero {", WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES)
        self.assertIn(".nb-post {", WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES)


if __name__ == "__main__":
    unittest.main()
