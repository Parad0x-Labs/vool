from __future__ import annotations

"""Movement selectors and top-stat rendering for the workstation overview runtime."""

WORKSTATION_OVERVIEW_MOVEMENT_RUNTIME = '''
    function renderTopStats(data) {
      const movement = liveMovementSummary(data);
      const latestEvent = movement.events[0] || null;
      const latestActive = movement.activeTopics[0] || null;
      const latestCompletion = movement.completions[0] || null;
      const latestFailure = movement.failures[0] || null;
      const latestStale = movement.stalePeers[0] || null;
      const items = [
        {
          label: 'Distinct peers online',
          value: fmtNumber(movement.peerSummary.distinctOnline),
          detail: movement.peerSummary.duplicates > 0
            ? `${fmtNumber(movement.peerSummary.rawVisible)} raw watcher rows collapsed into ${fmtNumber(movement.peerSummary.distinctVisible)} distinct peers.`
            : `${fmtNumber(movement.peerSummary.distinctVisible)} distinct peer records are visible right now.`,
          tone: movement.peerSummary.distinctOnline > 0 ? 'ok' : '',
          payload: {
            title: 'Distinct peer presence',
            summary: movement.peerSummary.duplicates > 0
              ? `The watcher is reporting ${fmtNumber(movement.peerSummary.rawVisible)} raw presence rows, but only ${fmtNumber(movement.peerSummary.distinctVisible)} distinct peers after collapsing duplicate VOOL leases.`
              : `${fmtNumber(movement.peerSummary.distinctVisible)} distinct peers are visible right now.`,
            truth_label: 'watcher-derived',
            freshness: movement.stalePeers.length ? 'mixed' : 'current',
            status: movement.peerSummary.distinctOnline > 0 ? 'active' : 'quiet',
            source_meet_url: data.source_meet_url || '',
            raw_presence_rows: movement.peerSummary.rawVisible,
            raw_online_rows: movement.peerSummary.rawOnline,
            duplicate_visible_agents: movement.peerSummary.duplicates,
            visible_agents: movement.peerSummary.distinctVisible,
            active_agents: movement.peerSummary.distinctOnline,
          },
        },
        {
          label: 'Active tasks now',
          value: fmtNumber(movement.activeTopics.length),
          detail: latestActive
            ? compactText(`${latestActive.title || 'Active task'} · ${latestActive.summary || ''}`, 104)
            : 'No active task is visible right now.',
          tone: movement.activeTopics.length > 0 ? 'ok' : '',
          payload: latestActive
            ? {
                topic_id: latestActive.topic_id || '',
                linked_task_id: latestActive.linked_task_id || '',
                title: latestActive.title || 'Active task',
                summary: latestActive.summary || '',
                truth_label: latestActive.truth_label || 'watcher-derived',
                freshness: latestActive.freshness || 'current',
                status: latestActive.status || 'researching',
                updated_at: latestActive.updated_at || '',
                source_meet_url: data.source_meet_url || '',
                artifact_count: Number(latestActive.artifact_count || 0),
                packet_endpoint: latestActive.packet_endpoint || '',
              }
            : {
                title: 'No active task visible',
                summary: 'The watcher is live, but there is no currently active task in the visible topic set.',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: 'idle',
                source_meet_url: data.source_meet_url || '',
              },
        },
        {
          label: 'Recent task events',
          value: fmtNumber(movement.events.length),
          detail: latestEvent
            ? compactText(taskEventPreview(latestEvent), 104)
            : 'No recent task-event signal is visible yet.',
          tone: movement.events.length > 0 ? 'ok' : '',
          payload: latestEvent
            ? {
                topic_id: latestEvent.topic_id || '',
                claim_id: latestEvent.claim_id || '',
                title: latestEvent.topic_title || 'Recent change',
                summary: taskEventPreview(latestEvent),
                detail: latestEvent.detail || '',
                truth_label: latestEvent.truth_label || latestEvent.source_label || 'watcher-derived',
                freshness: latestEvent.presence_freshness || 'current',
                status: latestEvent.status || latestEvent.event_type || 'changed',
                timestamp: latestEvent.timestamp || '',
                source_meet_url: data.source_meet_url || '',
              }
            : {
                title: 'No recent change signal',
                summary: 'The watcher payload does not currently include a visible recent change event.',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: 'quiet',
                source_meet_url: data.source_meet_url || '',
              },
        },
        {
          label: latestCompletion ? 'Recent completion' : 'Completion data',
          value: latestCompletion ? fmtNumber(movement.completions.length) : 'not live yet',
          detail: latestCompletion
            ? compactText(taskEventPreview(latestCompletion), 104)
            : 'No verified completion data has reached this watcher yet.',
          tone: latestCompletion ? 'ok' : '',
          payload: latestCompletion
            ? {
                topic_id: latestCompletion.topic_id || '',
                claim_id: latestCompletion.claim_id || '',
                title: latestCompletion.topic_title || 'Recent completion',
                summary: taskEventPreview(latestCompletion),
                detail: latestCompletion.detail || '',
                truth_label: latestCompletion.truth_label || latestCompletion.source_label || 'watcher-derived',
                freshness: latestCompletion.presence_freshness || 'current',
                status: latestCompletion.status || 'completed',
                timestamp: latestCompletion.timestamp || '',
                source_meet_url: data.source_meet_url || '',
                artifact_count: Number(latestCompletion.artifact_count || 0),
              }
            : {
                title: 'No verified completion data yet',
                summary: 'The live watcher/public bridge payload does not currently expose a recent completed result.',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: 'no live data yet',
                source_meet_url: data.source_meet_url || '',
              },
        },
        {
          label: latestFailure ? 'Recent failure' : 'Failure data',
          value: latestFailure ? fmtNumber(movement.failures.length) : 'not live yet',
          detail: latestFailure
            ? compactText(taskEventPreview(latestFailure), 104)
            : 'No verified failure data has reached this watcher yet.',
          tone: latestFailure ? 'warn' : '',
          payload: latestFailure
            ? {
                topic_id: latestFailure.topic_id || '',
                claim_id: latestFailure.claim_id || '',
                title: latestFailure.topic_title || 'Recent failure',
                summary: taskEventPreview(latestFailure),
                detail: latestFailure.detail || '',
                truth_label: latestFailure.truth_label || latestFailure.source_label || 'watcher-derived',
                freshness: latestFailure.presence_freshness || 'current',
                status: latestFailure.status || 'blocked',
                timestamp: latestFailure.timestamp || '',
                source_meet_url: data.source_meet_url || '',
                artifact_count: Number(latestFailure.artifact_count || 0),
                conflict_count: 1,
              }
            : {
                title: 'No verified failure data yet',
                summary: 'The live watcher/public bridge payload does not currently expose a blocked, failed, or challenged task result.',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: 'no live data yet',
                source_meet_url: data.source_meet_url || '',
              },
        },
        {
          label: 'Stale peer/source rows',
          value: fmtNumber(movement.stalePeers.length),
          detail: latestStale
            ? compactText(`${latestStale.display_name || latestStale.claim_label || latestStale.agent_id || 'stale peer'} in ${latestStale.current_region || latestStale.home_region || 'unknown region'}`, 104)
            : 'No stale peer or source row is visible right now.',
          tone: movement.stalePeers.length > 0 ? 'warn' : 'ok',
          payload: latestStale
            ? {
                agent_id: latestStale.agent_id || '',
                title: latestStale.display_name || latestStale.claim_label || latestStale.agent_id || 'Stale source',
                summary: 'A stale watcher-derived presence row is still visible and should not be treated as a live operator.',
                truth_label: 'watcher-derived',
                freshness: 'stale',
                status: latestStale.status || 'stale',
                source_meet_url: data.source_meet_url || '',
                transport_mode: latestStale.transport_mode || '',
                current_region: latestStale.current_region || '',
                home_region: latestStale.home_region || '',
              }
            : {
                title: 'No stale source rows',
                summary: 'No stale peer or source row is currently visible in the watcher payload.',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: 'clear',
                source_meet_url: data.source_meet_url || '',
              },
        },
      ];
      document.getElementById('topStats').innerHTML = items.map((item) => `
        <article class="stat" ${inspectAttrs('Observation', item.label, item.payload)}>
          <span class="stat-label">${esc(item.label)}</span>
          <div class="stat-value">${esc(String(item.value))}</div>
          <p class="stat-detail">${esc(item.detail)}</p>
          <div class="row-meta">
            ${chip(item.payload?.truth_label || item.payload?.source_label || 'watcher-derived')}
            ${item.tone ? chip(item.payload?.status || item.label, item.tone) : ''}
          </div>
        </article>
      `).join('');
    }

    function isActiveTopic(topic) {
      return ['open', 'researching', 'disputed'].includes(String(topic?.status || '').toLowerCase());
    }

    function distinctPeerSummary(data) {
      const stats = data?.stats || {};
      const agents = Array.isArray(data?.agents) ? data.agents : [];
      const distinctVisible = Number(stats.visible_agents || agents.length || 0);
      const distinctOnline = Number(stats.active_agents || agents.filter((agent) => agent?.online).length || 0);
      const rawVisible = Number(stats.raw_visible_agents || agents.length || 0);
      const rawOnline = Number(stats.raw_online_agents || stats.presence_agents || distinctOnline || 0);
      const rawPresence = Number(stats.presence_agents || rawOnline || 0);
      const duplicates = Number(stats.duplicate_visible_agents || Math.max(0, rawVisible - distinctVisible));
      return { distinctVisible, distinctOnline, rawVisible, rawOnline, rawPresence, duplicates };
    }

    function recentCompletionSignals(data) {
      const events = Array.isArray(data?.task_event_stream) ? data.task_event_stream : [];
      const completedEvents = events.filter((event) => {
        const type = String(event?.event_type || '').toLowerCase();
        const status = String(event?.status || event?.progress_state || '').toLowerCase();
        return ['task_completed', 'result_submitted'].includes(type) || ['completed', 'solved'].includes(status);
      });
      if (completedEvents.length) return completedEvents;
      const claims = Array.isArray(data?.recent_topic_claims) ? data.recent_topic_claims : [];
      return claims
        .filter((claim) => ['completed', 'solved'].includes(String(claim?.status || '').toLowerCase()))
        .map((claim) => ({
          event_type: 'claim_completed',
          topic_id: claim?.topic_id || '',
          topic_title: claim?.topic_title || 'Completed claim',
          detail: claim?.note || 'A claim completed successfully.',
          status: claim?.status || 'completed',
          claim_id: claim?.claim_id || '',
          agent_label: claim?.agent_claim_label || claim?.agent_display_name || claim?.agent_id || '',
          timestamp: claim?.updated_at || claim?.created_at || '',
          artifact_count: Number(claim?.artifact_count || 0),
          source_label: claim?.truth_label || claim?.source_label || 'watcher-derived',
        }));
    }

    function recentFailureSignals(data) {
      const events = Array.isArray(data?.task_event_stream) ? data.task_event_stream : [];
      const failedEvents = events.filter((event) => {
        const type = String(event?.event_type || '').toLowerCase();
        const status = String(event?.status || event?.progress_state || '').toLowerCase();
        return ['task_blocked', 'challenge_raised'].includes(type) || ['blocked', 'failed', 'rejected', 'disputed'].includes(status);
      });
      if (failedEvents.length) return failedEvents;
      const claims = Array.isArray(data?.recent_topic_claims) ? data.recent_topic_claims : [];
      return claims
        .filter((claim) => ['blocked', 'failed', 'rejected', 'disputed'].includes(String(claim?.status || '').toLowerCase()))
        .map((claim) => ({
          event_type: 'claim_failed',
          topic_id: claim?.topic_id || '',
          topic_title: claim?.topic_title || 'Failed claim',
          detail: claim?.note || 'A blocked or failed claim is visible.',
          status: claim?.status || 'blocked',
          claim_id: claim?.claim_id || '',
          agent_label: claim?.agent_claim_label || claim?.agent_display_name || claim?.agent_id || '',
          timestamp: claim?.updated_at || claim?.created_at || '',
          artifact_count: Number(claim?.artifact_count || 0),
          source_label: claim?.truth_label || claim?.source_label || 'watcher-derived',
        }));
    }

    function liveMovementSummary(data) {
      const topics = Array.isArray(data?.topics) ? data.topics : [];
      const claims = Array.isArray(data?.recent_topic_claims) ? data.recent_topic_claims : [];
      const agents = Array.isArray(data?.agents) ? data.agents : [];
      const events = Array.isArray(data?.task_event_stream) ? data.task_event_stream : [];
      const activeTopics = topics.filter(isActiveTopic);
      const stalePeers = agents.filter((agent) => String(agent?.status || '').toLowerCase() === 'stale' || agent?.online === false);
      const activeClaims = claims.filter((claim) => ['active', 'researching', 'claimed', 'running'].includes(String(claim?.status || '').toLowerCase()));
      const completions = recentCompletionSignals(data);
      const failures = recentFailureSignals(data);
      return {
        topics,
        claims,
        agents,
        events,
        activeTopics,
        stalePeers,
        activeClaims,
        completions,
        failures,
        peerSummary: distinctPeerSummary(data),
      };
    }
'''
