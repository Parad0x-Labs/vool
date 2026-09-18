from __future__ import annotations

"""Home-board and overview orchestration runtime fragment for the workstation dashboard."""

from core.dashboard.workstation_overview_home_board_runtime import (
    WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME,
)
from core.dashboard.workstation_overview_notes_runtime import (
    WORKSTATION_OVERVIEW_NOTES_RUNTIME,
)

WORKSTATION_OVERVIEW_HOME_RUNTIME = (
    WORKSTATION_OVERVIEW_HOME_BOARD_RUNTIME
    + WORKSTATION_OVERVIEW_NOTES_RUNTIME
    + '''
    function renderOverview(data) {
      const stats = data.stats || {};
      const adaptation = data.adaptation_overview || {};
      const adaptationProof = data.adaptation_proof || {};
      const proof = data.proof_of_useful_work || {};
      const movement = liveMovementSummary(data);
      const events = movement.events;
      const activeTopics = movement.activeTopics;
      const stalePeers = movement.stalePeers;
      const blockedEvents = movement.failures;
      const recentChangePreview = events.slice(0, 4).map((event) => event.topic_title || event.detail || event.event_type || 'event').join(' · ');

      renderOverviewMiniStats(data, movement);
      renderAdaptationStatusLine(adaptation);
      renderProofMiniStats(proof, data);
      renderGloryLeaderList(proof);
      renderProofReceiptList(proof);
      renderWorkstationHomeBoard(data, movement);
      renderAdaptationProofList(adaptationProof);
      renderResearchGravityList(data);
      renderTopicList(movement.topics);
      renderInto('feedList', renderTaskEvents(events, 5, 'No visible task events yet.'), {preserveDetails: true});
      renderInto('recentChangeList', renderTaskEvents(events.slice(0, 4), 4, 'No recent changes yet.'), {preserveDetails: true});
      renderClaimStreamList(movement.claims);
      renderRegionList(stats.region_stats || []);
      renderWatchStationNotes(activeTopics, stats, stalePeers, blockedEvents, recentChangePreview);
    }
'''
)
