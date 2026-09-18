from __future__ import annotations

import unittest

from core.dashboard.workstation_card_normalizers import WORKSTATION_CARD_NORMALIZERS
from core.dashboard.workstation_card_render_sections import (
    WORKSTATION_CARD_RENDER_SECTIONS,
)
from core.dashboard.workstation_client import render_workstation_client_script
from core.dashboard.workstation_inspector_runtime import WORKSTATION_INSPECTOR_RUNTIME
from core.dashboard.workstation_learning_program_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_knowledge_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_KNOWLEDGE_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_runtime import (
    WORKSTATION_LEARNING_PROGRAM_RUNTIME,
)
from core.dashboard.workstation_learning_program_shared_runtime import (
    WORKSTATION_LEARNING_PROGRAM_SHARED_RUNTIME,
)
from core.dashboard.workstation_learning_program_topic_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TOPIC_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_activity_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_ACTIVITY_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_market_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_MARKET_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_overview_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_OVERVIEW_RUNTIME,
)
from core.dashboard.workstation_voolbook_runtime import WORKSTATION_VOOLBOOK_RUNTIME
from core.dashboard.workstation_overview_home_board_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME,
)
from core.dashboard.workstation_overview_home_runtime import (
    WORKSTATION_OVERVIEW_HOME_RUNTIME,
)
from core.dashboard.workstation_overview_movement_runtime import (
    WORKSTATION_OVERVIEW_MOVEMENT_RUNTIME,
)
from core.dashboard.workstation_overview_notes_runtime import (
    WORKSTATION_OVERVIEW_NOTES_RUNTIME,
)
from core.dashboard.workstation_overview_proof_runtime import (
    WORKSTATION_OVERVIEW_PROOF_RUNTIME,
)
from core.dashboard.workstation_overview_runtime import WORKSTATION_OVERVIEW_RUNTIME
from core.dashboard.workstation_overview_stats_runtime import (
    WORKSTATION_OVERVIEW_STATS_RUNTIME,
)
from core.dashboard.workstation_overview_streams_runtime import (
    WORKSTATION_OVERVIEW_STREAMS_RUNTIME,
)
from core.dashboard.workstation_overview_surface_runtime import (
    WORKSTATION_OVERVIEW_SURFACE_RUNTIME,
)
from core.dashboard.workstation_trading_learning_runtime import WORKSTATION_TRADING_LEARNING_RUNTIME
from core.dashboard.workstation_trading_presence_runtime import (
    WORKSTATION_TRADING_PRESENCE_RUNTIME,
)
from core.dashboard.workstation_trading_surface_runtime import (
    WORKSTATION_TRADING_SURFACE_RUNTIME,
)


class DashboardWorkstationClientTests(unittest.TestCase):
    def test_workstation_card_runtime_keeps_normalizers_and_renderers_split(self) -> None:
        self.assertIn("function compactText(value, maxLen = 180)", WORKSTATION_CARD_NORMALIZERS)
        self.assertIn("function taskEventPreview(event)", WORKSTATION_CARD_NORMALIZERS)
        self.assertIn("function renderCompactPostCard(post, options = {})", WORKSTATION_CARD_RENDER_SECTIONS)
        self.assertIn("function renderTaskEvents(events, limit, emptyText)", WORKSTATION_CARD_RENDER_SECTIONS)

    def test_workstation_trading_learning_runtime_exports_market_and_learning_functions(self) -> None:
        self.assertIn("function latestTradingPresence(trading)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function tradingPresenceState(trading, generatedAt, agents)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function renderTrading(data)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function renderLearningLab(data)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function tradingHeartbeatState(heartbeat, generatedAt)", WORKSTATION_TRADING_PRESENCE_RUNTIME)
        self.assertIn("function renderTradingMiniStats(summary, heartbeat, presenceState)", WORKSTATION_TRADING_SURFACE_RUNTIME)
        self.assertIn("function renderLearningProgramCard({title, summaryText, chipsHtml, bodyHtml, open = false, openStateKey = ''})", WORKSTATION_LEARNING_PROGRAM_CARDS_RUNTIME)
        self.assertIn("function renderLearningLab(data)", WORKSTATION_LEARNING_PROGRAM_RUNTIME)
        self.assertIn("function renderLearningMiniStats(items)", WORKSTATION_LEARNING_PROGRAM_SHARED_RUNTIME)
        self.assertIn("function buildTradingProgramBody(summary, decision, patternHealth, missed, edges, discoveries, flow, recentCalls)", WORKSTATION_LEARNING_PROGRAM_TRADING_CARDS_RUNTIME)
        self.assertIn("function renderTradingProgramOverviewSection(summary, decision)", WORKSTATION_LEARNING_PROGRAM_TRADING_OVERVIEW_RUNTIME)
        self.assertIn("function renderTradingProgramMarketSection(patternHealth, missed, edges)", WORKSTATION_LEARNING_PROGRAM_TRADING_MARKET_RUNTIME)
        self.assertIn("function renderTradingProgramActivitySection(discoveries, flow, recentCalls)", WORKSTATION_LEARNING_PROGRAM_TRADING_ACTIVITY_RUNTIME)
        self.assertIn("function buildGenericKnowledgeBody(learning, memory, mesh, recentLearning)", WORKSTATION_LEARNING_PROGRAM_KNOWLEDGE_CARDS_RUNTIME)
        self.assertIn("function buildActiveTopicProgramCards(activeTopics)", WORKSTATION_LEARNING_PROGRAM_TOPIC_CARDS_RUNTIME)

    def test_workstation_inspector_runtime_exports_inspector_and_chrome_functions(self) -> None:
        self.assertIn("function encodeInspectPayload(payload)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function renderBrainInspector(type, label, payload)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function renderWorkstationChrome(data)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function bindWorkstationInspectorInteractions()", WORKSTATION_INSPECTOR_RUNTIME)

    def test_workstation_overview_runtime_exports_home_runtime_functions(self) -> None:
        self.assertIn("function renderTopStats(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertIn("function liveMovementSummary(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertIn("function renderOverview(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertNotIn("function renderAgents(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertIn("function distinctPeerSummary(data)", WORKSTATION_OVERVIEW_MOVEMENT_RUNTIME)
        self.assertIn("function renderOverviewMiniStats(data, movement)", WORKSTATION_OVERVIEW_SURFACE_RUNTIME)
        self.assertIn("function renderWorkstationHomeBoard(data, movement)", WORKSTATION_OVERVIEW_SURFACE_RUNTIME)
        self.assertIn("function renderAdaptationStatusLine(adaptation)", WORKSTATION_OVERVIEW_STATS_RUNTIME)
        self.assertIn("function renderProofMiniStats(proof, data)", WORKSTATION_OVERVIEW_PROOF_RUNTIME)
        self.assertIn("function renderResearchGravityList(data)", WORKSTATION_OVERVIEW_STREAMS_RUNTIME)
        self.assertIn("function renderOverview(data)", WORKSTATION_OVERVIEW_HOME_RUNTIME)
        self.assertIn("function renderWorkstationHomeBoard(data, movement)", WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME)
        self.assertIn("function renderWatchStationNotes(activeTopics, stats, stalePeers, blockedEvents, recentChangePreview)", WORKSTATION_OVERVIEW_NOTES_RUNTIME)

    def test_workstation_voolbook_runtime_exports_voolbook_surface(self) -> None:
        self.assertIn("function renderVoolBook(data)", WORKSTATION_VOOLBOOK_RUNTIME)
        self.assertIn("initButterflyCanvas", WORKSTATION_VOOLBOOK_RUNTIME)

    def test_workstation_client_script_keeps_runtime_placeholders_and_surface_contract(self) -> None:
        script = render_workstation_client_script()

        self.assertIn("__WORKSTATION_SCRIPT__", script)
        self.assertIn("__INITIAL_STATE__", script)
        self.assertIn("__API_ENDPOINT__", script)
        self.assertIn("__TOPIC_BASE_PATH__", script)
        self.assertIn("__INITIAL_MODE__", script)
        self.assertIn("function tradingPresenceState(", script)
        self.assertIn("renderWorkstationChrome", script)
        self.assertIn("workstationHomeBoard", script)
        self.assertIn("function renderInto(containerId, html, {preserveDetails = false} = {})", script)
        self.assertIn("function renderTopStats(data)", script)
        self.assertIn("function renderOverview(data)", script)
        self.assertIn("function renderAgents(data)", script)
        self.assertIn("function renderTrading(data)", script)
        self.assertIn("function renderLearningLab(data)", script)
        self.assertIn("function renderVoolBook(data)", script)
        self.assertIn("function renderBrainInspector(type, label, payload)", script)
        self.assertIn("function renderWorkstationChrome(data)", script)
        self.assertIn("function bindWorkstationInspectorInteractions()", script)
        self.assertIn("function renderCompactPostCard(post, options = {})", script)
        self.assertIn("function renderTaskEventFold(event)", script)
        self.assertIn("window.refresh = refresh;", script)


if __name__ == "__main__":
    unittest.main()
