from __future__ import annotations

"""Workstation overview home-board card rendering helpers."""

from core.dashboard.workstation_overview_home_board_items_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_ITEMS_RUNTIME,
)
from core.dashboard.workstation_overview_home_board_render_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_RENDER_RUNTIME,
)

WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME = (
    WORKSTATION_OVERVIEW_HOME_BOARD_ITEMS_RUNTIME
    + WORKSTATION_OVERVIEW_HOME_BOARD_RENDER_RUNTIME
    + '''
    function renderWorkstationHomeBoard(data, movement) {
      const items = buildWorkstationHomeBoardItems(data, movement);
      document.getElementById('workstationHomeBoard').innerHTML = renderWorkstationHomeBoardCards(items);
    }
'''
)
