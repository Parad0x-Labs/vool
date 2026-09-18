from __future__ import annotations

"""Trading learning-program card runtime fragment for the workstation dashboard."""

from core.dashboard.workstation_learning_program_trading_activity_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_ACTIVITY_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_market_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_MARKET_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_overview_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_OVERVIEW_RUNTIME,
)

WORKSTATION_LEARNING_PROGRAM_TRADING_CARDS_RUNTIME = (
    WORKSTATION_LEARNING_PROGRAM_TRADING_OVERVIEW_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_TRADING_MARKET_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_TRADING_ACTIVITY_RUNTIME
    + '''
    function buildTradingProgramBody(summary, decision, patternHealth, missed, edges, discoveries, flow, recentCalls) {
      return [
        `<div class="learning-program-grid">${renderTradingProgramOverviewSection(summary, decision)}</div>`,
        `<div class="learning-program-grid">${renderTradingProgramMarketSection(patternHealth, missed, edges)}</div>`,
        renderTradingProgramActivitySection(discoveries, flow, recentCalls),
      ].join('');
    }
'''
)
