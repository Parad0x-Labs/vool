from __future__ import annotations

"""Workstation overview home-board item shaping helpers."""

WORKSTATION_OVERVIEW_HOME_BOARD_ITEMS_RUNTIME = '''
    function buildWorkstationHomeBoardItems(data, movement) {
      const events = movement.events;
      const activeTopics = movement.activeTopics;
      const stalePeers = movement.stalePeers;
      const blockedEvents = movement.failures;
      const recentChangePreview = events.slice(0, 4).map((event) => event.topic_title || event.detail || event.event_type || 'event').join(' · ');
      const firstCompletion = movement.completions[0] || null;
      const firstFailure = movement.failures[0] || null;

      return [
        buildWorkstationHomeBoardActiveTasksItem(data, activeTopics),
        buildWorkstationHomeBoardStalePeersItem(data, stalePeers),
        buildWorkstationHomeBoardCompletionItem(data, movement, firstCompletion),
        buildWorkstationHomeBoardFailureItem(data, firstFailure, blockedEvents),
        buildWorkstationHomeBoardEventsItem(data, events, recentChangePreview),
      ];
    }

    function buildWorkstationHomeBoardItem(label, value, detail, payload) {
      return {label, value, detail, payload};
    }

    function buildWorkstationHomeBoardFallbackPayload(sourceMeetUrl, title, summary, status, freshness = 'current') {
      return {
        title,
        summary,
        truth_label: 'watcher-derived',
        freshness,
        status,
        source_meet_url: sourceMeetUrl || '',
      };
    }

    function buildWorkstationHomeBoardActiveTasksItem(data, activeTopics) {
      const sourceMeetUrl = data.source_meet_url || '';
      return buildWorkstationHomeBoardItem(
        'Active tasks',
        fmtNumber(activeTopics.length),
        activeTopics.length ? compactText(activeTopics[0].title || activeTopics[0].summary || 'Live task flow present.', 96) : 'No live tasks are visible right now.',
        activeTopics.length
          ? {
              topic_id: activeTopics[0].topic_id || '',
              linked_task_id: activeTopics[0].linked_task_id || '',
              title: activeTopics[0].title || 'Active task',
              summary: activeTopics[0].summary || '',
              truth_label: 'watcher-derived',
              freshness: 'current',
              status: activeTopics[0].status || 'researching',
              updated_at: activeTopics[0].updated_at || '',
              source_meet_url: sourceMeetUrl,
              artifact_count: Number(activeTopics[0].artifact_count || 0),
              packet_endpoint: activeTopics[0].packet_endpoint || '',
            }
          : buildWorkstationHomeBoardFallbackPayload(
              sourceMeetUrl,
              'No active task visible',
              'No active task flow is visible in the current watcher payload.',
              'quiet',
            ),
      );
    }

    function buildWorkstationHomeBoardStalePeersItem(data, stalePeers) {
      const sourceMeetUrl = data.source_meet_url || '';
      return buildWorkstationHomeBoardItem(
        'Stale peer/source rows',
        fmtNumber(stalePeers.length),
        stalePeers.length ? compactText(stalePeers[0].claim_label || stalePeers[0].display_name || stalePeers[0].agent_id || 'Stale presence detected.', 96) : 'No stale peer presence is visible.',
        stalePeers.length
          ? {
              agent_id: stalePeers[0].agent_id || '',
              title: stalePeers[0].claim_label || stalePeers[0].display_name || stalePeers[0].agent_id || 'Stale source',
              summary: 'This peer/source row is stale and should not be read as live movement.',
              truth_label: 'watcher-derived',
              freshness: 'stale',
              status: stalePeers[0].status || 'stale',
              updated_at: stalePeers[0].updated_at || '',
              source_meet_url: sourceMeetUrl,
              transport_mode: stalePeers[0].transport_mode || '',
            }
          : buildWorkstationHomeBoardFallbackPayload(
              sourceMeetUrl,
              'No stale sources',
              'No stale peer/source rows are visible right now.',
              'clear',
            ),
      );
    }

    function buildWorkstationHomeBoardCompletionItem(data, movement, firstCompletion) {
      const sourceMeetUrl = data.source_meet_url || '';
      return buildWorkstationHomeBoardItem(
        firstCompletion ? 'Recent completion' : 'Completion data',
        firstCompletion ? fmtNumber(movement.completions.length) : 'not live yet',
        firstCompletion ? compactText(taskEventPreview(firstCompletion), 96) : 'No verified completion data has reached this watcher yet.',
        firstCompletion
          ? {
              topic_id: firstCompletion.topic_id || '',
              claim_id: firstCompletion.claim_id || '',
              title: firstCompletion.topic_title || 'Recent completion',
              summary: taskEventPreview(firstCompletion),
              detail: firstCompletion.detail || '',
              truth_label: firstCompletion.truth_label || firstCompletion.source_label || 'watcher-derived',
              freshness: firstCompletion.presence_freshness || 'current',
              status: firstCompletion.status || 'completed',
              timestamp: firstCompletion.timestamp || '',
              source_meet_url: sourceMeetUrl,
              artifact_count: Number(firstCompletion.artifact_count || 0),
            }
          : buildWorkstationHomeBoardFallbackPayload(
              sourceMeetUrl,
              'No verified completion data yet',
              'The current watcher/public bridge payload does not expose a recent completion.',
              'no live data yet',
            ),
      );
    }

    function buildWorkstationHomeBoardFailureItem(data, firstFailure, blockedEvents) {
      const sourceMeetUrl = data.source_meet_url || '';
      return buildWorkstationHomeBoardItem(
        firstFailure ? 'Recent failure' : 'Failure data',
        firstFailure ? fmtNumber(blockedEvents.length) : 'not live yet',
        firstFailure ? compactText(taskEventPreview(firstFailure), 96) : 'No verified failure data has reached this watcher yet.',
        firstFailure
          ? {
              topic_id: firstFailure.topic_id || '',
              claim_id: firstFailure.claim_id || '',
              title: firstFailure.topic_title || 'Recent failure',
              summary: taskEventPreview(firstFailure),
              detail: firstFailure.detail || '',
              truth_label: firstFailure.truth_label || firstFailure.source_label || 'watcher-derived',
              freshness: firstFailure.presence_freshness || 'current',
              status: firstFailure.status || 'blocked',
              timestamp: firstFailure.timestamp || '',
              source_meet_url: sourceMeetUrl,
              conflict_count: 1,
            }
          : buildWorkstationHomeBoardFallbackPayload(
              sourceMeetUrl,
              'No verified failure data yet',
              'The current watcher/public bridge payload does not expose a recent blocked or failed task.',
              'no live data yet',
            ),
      );
    }

    function buildWorkstationHomeBoardEventsItem(data, events, recentChangePreview) {
      const sourceMeetUrl = data.source_meet_url || '';
      return buildWorkstationHomeBoardItem(
        'Recent task events',
        fmtNumber(events.length),
        recentChangePreview || 'No recent event change yet.',
        events.length
          ? {
              topic_id: events[0].topic_id || '',
              title: events[0].topic_title || 'Recent change',
              summary: taskEventPreview(events[0]),
              detail: events[0].detail || '',
              truth_label: events[0].truth_label || events[0].source_label || 'watcher-derived',
              freshness: events[0].presence_freshness || 'current',
              status: events[0].status || events[0].event_type || 'changed',
              timestamp: events[0].timestamp || '',
              source_meet_url: sourceMeetUrl,
            }
          : buildWorkstationHomeBoardFallbackPayload(
              sourceMeetUrl,
              'No recent change',
              'No recent change event is visible in the watcher payload.',
              'quiet',
            ),
      );
    }
'''
