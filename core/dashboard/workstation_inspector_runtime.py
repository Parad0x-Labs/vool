from __future__ import annotations

"""Inspector and workstation-chrome runtime fragment for the dashboard client template."""

WORKSTATION_INSPECTOR_RUNTIME = '''
    function encodeInspectPayload(payload) {
      try {
        return encodeURIComponent(JSON.stringify(payload || {}));
      } catch (_err) {
        return encodeURIComponent('{}');
      }
    }

    function decodeInspectPayload(value) {
      try {
        return JSON.parse(decodeURIComponent(String(value || '')));
      } catch (_err) {
        return {};
      }
    }

    function inspectAttrs(type, label, payload) {
      return `data-inspect-type="${esc(type)}" data-inspect-label="${esc(label)}" data-inspect-payload="${esc(encodeInspectPayload(payload))}"`;
    }

    function inspectorBadges(type, payload) {
      const badges = [`<span class="wk-badge">${esc(type)}</span>`];
      const truth = payload?.truth_label || payload?.truth_source || payload?.source_label || payload?.source || '';
      const freshness = payload?.presence_freshness || payload?.freshness || payload?.freshness_label || '';
      const status = payload?.status || payload?.topic_status || payload?.presence_status || '';
      const conflictCount = Number(payload?.conflict_count || 0);
      if (truth) badges.push(`<span class="wk-badge wk-badge--source">${esc(truth)}</span>`);
      if (freshness) {
        const tone = String(freshness).toLowerCase().includes('stale') ? ' wk-badge--warn' : ' wk-badge--fresh';
        badges.push(`<span class="wk-badge${tone}">${esc(freshness)}</span>`);
      }
      if (status) {
        const lowered = String(status).toLowerCase();
        const tone = lowered.includes('block') || lowered.includes('dispute') || lowered.includes('challenge')
          ? ' wk-badge--bad'
          : lowered.includes('open') || lowered.includes('research') || lowered.includes('live')
            ? ' wk-badge--good'
            : '';
        badges.push(`<span class="wk-badge${tone}">${esc(status)}</span>`);
      }
      if (conflictCount > 0) badges.push(`<span class="wk-badge wk-badge--bad">${esc(`conflicts ${conflictCount}`)}</span>`);
      return badges.join('');
    }

    function inspectorSummary(payload) {
      return compactText(
        payload?.summary ||
        payload?.detail ||
        payload?.body ||
        payload?.preview ||
        payload?.note ||
        payload?.message ||
        payload?.title ||
        'No further detail for this object yet.',
        260,
      ) || 'No further detail for this object yet.';
    }

    function renderInspectorTruthDebug(data) {
      const movement = liveMovementSummary(data || {});
      const generatedAt = data?.generated_at || '';
      document.getElementById('brainInspectorTruthNote').textContent =
        movement.peerSummary.duplicates > 0
          ? `Old raw peer counts were misleading here because ${fmtNumber(movement.peerSummary.rawVisible)} watcher presence rows collapse into ${fmtNumber(movement.peerSummary.distinctVisible)} distinct visible peers.`
          : 'Raw watcher presence and distinct visible peers currently match, so there is no duplicate inflation right now.';
      document.getElementById('brainInspectorTruth').innerHTML = [
        ['Raw presence rows', fmtNumber(movement.peerSummary.rawVisible)],
        ['Collapsed duplicates', fmtNumber(movement.peerSummary.duplicates)],
        ['Distinct online peers', fmtNumber(movement.peerSummary.distinctOnline)],
        ['Stale visible peers', fmtNumber(movement.stalePeers.length)],
        ['Last update', generatedAt ? fmtTime(generatedAt) : 'unknown'],
      ].map(([label, value]) => `<div class="dashboard-inspector-row"><strong>${esc(label)}</strong><br />${esc(String(value))}</div>`).join('');
    }

    function renderBrainInspector(type, label, payload) {
      document.getElementById('brainInspectorTitle').textContent = label || 'Select an object';
      document.getElementById('brainInspectorBadges').innerHTML = inspectorBadges(type || 'object', payload || {});
      document.getElementById('brainInspectorHuman').textContent = inspectorSummary(payload || {});

      const agentRows = Object.entries(payload || {})
        .filter(([_key, value]) => value !== null && value !== undefined && value !== '')
        .slice(0, 10)
        .map(([key, value]) => `<div class="dashboard-inspector-row"><strong>${esc(key)}</strong><br />${esc(typeof value === 'object' ? JSON.stringify(value) : String(value))}</div>`);
      document.getElementById('brainInspectorAgent').innerHTML = agentRows.length
        ? agentRows.join('')
        : '<div class="dashboard-inspector-row">No structured object fields yet.</div>';

      const metaRows = [];
      if (payload?.truth_label || payload?.truth_source || payload?.source_label || payload?.source) {
        metaRows.push(`<div class="dashboard-inspector-row">Source label ${esc(payload.truth_label || payload.truth_source || payload.source_label || payload.source)}</div>`);
      }
      if (payload?.presence_freshness || payload?.freshness || payload?.freshness_label) {
        metaRows.push(`<div class="dashboard-inspector-row">Freshness ${esc(payload.presence_freshness || payload.freshness || payload.freshness_label)}</div>`);
      }
      if (payload?.topic_id) metaRows.push(`<div class="dashboard-inspector-row">Task <span class="wk-code">${esc(payload.topic_id)}</span></div>`);
      if (payload?.linked_task_id) metaRows.push(`<div class="dashboard-inspector-row">Linked task <span class="wk-code">${esc(payload.linked_task_id)}</span></div>`);
      if (payload?.agent_id) metaRows.push(`<div class="dashboard-inspector-row">Peer <span class="wk-code">${esc(payload.agent_id)}</span></div>`);
      if (payload?.claim_id) metaRows.push(`<div class="dashboard-inspector-row">Claim <span class="wk-code">${esc(payload.claim_id)}</span></div>`);
      if (payload?.post_id) metaRows.push(`<div class="dashboard-inspector-row">Observation <span class="wk-code">${esc(payload.post_id)}</span></div>`);
      if (payload?.updated_at || payload?.timestamp || payload?.created_at) {
        metaRows.push(`<div class="dashboard-inspector-row">Last update ${esc(fmtTime(payload.updated_at || payload.timestamp || payload.created_at))}</div>`);
      }
      if (payload?.artifact_count !== undefined && payload?.artifact_count !== null) {
        metaRows.push(`<div class="dashboard-inspector-row">Linked artifacts ${esc(fmtNumber(payload.artifact_count || 0))}</div>`);
      }
      if (payload?.packet_endpoint) metaRows.push(`<div class="dashboard-inspector-row">Packet ${esc(payload.packet_endpoint)}</div>`);
      if (payload?.source_meet_url) metaRows.push(`<div class="dashboard-inspector-row">Watcher source ${esc(payload.source_meet_url)}</div>`);
      if (!metaRows.length) metaRows.push('<div class="dashboard-inspector-row">No linked ids or source metadata yet.</div>');
      document.getElementById('brainInspectorMeta').innerHTML = metaRows.join('');
      renderInspectorTruthDebug(currentDashboardState || {});
      document.getElementById('brainInspectorRaw').textContent = JSON.stringify(payload || {}, null, 2);
    }

    function activateDashboardTab(tab, pushState) {
      const safeTab = String(tab || 'overview');
      document.querySelectorAll('.tab-button[data-tab]').forEach((button) => {
        button.classList.toggle('active', button.dataset.tab === safeTab);
      });
      document.querySelectorAll('[data-tab-target]').forEach((button) => {
        button.classList.toggle('active', button.dataset.tabTarget === safeTab);
      });
      document.querySelectorAll('.tab-panel').forEach((panel) => {
        panel.classList.toggle('active', panel.id === `tab-${safeTab}`);
      });
      if (pushState !== false) {
        const url = new URL(window.location);
        url.searchParams.set('tab', safeTab);
        url.searchParams.delete('mode');
        history.replaceState(null, '', url);
      }
    }

    function renderWorkstationChrome(data) {
      const topics = Array.isArray(data?.topics) ? data.topics : [];
      const claims = Array.isArray(data?.recent_topic_claims) ? data.recent_topic_claims : [];
      const agents = Array.isArray(data?.agents) ? data.agents : [];
      const events = Array.isArray(data?.task_event_stream) ? data.task_event_stream : [];
      const learningTopics = Array.isArray(data?.learning_lab?.active_topics) ? data.learning_lab.active_topics : [];
      const artifactCount = learningTopics.reduce((total, topic) => total + Number(topic?.artifact_count || 0), 0);
      const conflictCount = topics.filter((topic) => String(topic?.status || '').toLowerCase() === 'disputed').length
        + events.filter((event) => ['task_blocked', 'challenge_raised'].includes(String(event?.event_type || '').toLowerCase())).length;
      document.getElementById('objectModelRail').innerHTML = [
        ['Peer', agents.length],
        ['Task', topics.length],
        ['Session', data?.research_queue?.length || 0],
        ['Observation', events.length + (data?.recent_posts?.length || 0)],
        ['Artifact', artifactCount],
        ['Claim', claims.length],
        ['Conflict', conflictCount],
      ].map(([label, value]) => `<span class="wk-chip">${esc(label)} ${fmtNumber(value)}</span>`).join('');

      const stalePeers = agents.filter((agent) => String(agent?.status || '').toLowerCase() === 'stale' || agent?.online === false).length;
      const blocked = events.filter((event) => ['task_blocked', 'challenge_raised'].includes(String(event?.event_type || '').toLowerCase())).length;
      document.getElementById('healthRail').innerHTML = [
        ['active tasks', topics.filter((topic) => ['open', 'researching'].includes(String(topic?.status || '').toLowerCase())).length, 'wk-badge--good'],
        ['stale peers', stalePeers, stalePeers ? 'wk-badge--warn' : ''],
        ['blocked tasks', blocked, blocked ? 'wk-badge--bad' : ''],
        ['changed events', events.length, ''],
      ].map(([label, value, tone]) => `<span class="wk-badge ${tone}">${esc(label)} ${fmtNumber(value)}</span>`).join('');

      document.getElementById('sourceRail').innerHTML = [
        ['watcher-derived', topics.length + agents.length + events.length],
        ['local-only', (data?.recent_activity?.tasks?.length || 0) + (data?.recent_activity?.responses?.length || 0)],
        ['external', data?.trading_learning?.calls?.length || 0],
      ].map(([label, value]) => `<span class="wk-badge wk-badge--source">${esc(label)} ${fmtNumber(value)}</span>`).join('');

      const presence = tradingPresenceState(data?.trading_learning || {}, data?.generated_at, agents);
      document.getElementById('freshnessRail').innerHTML = [
        `<span class="wk-badge wk-badge--source">watcher current</span>`,
        `<span class="wk-badge ${presence.kind === 'warn' ? 'wk-badge--warn' : 'wk-badge--good'}">trading ${esc(presence.label.toLowerCase())}</span>`,
        `<span class="wk-badge ${stalePeers ? 'wk-badge--warn' : 'wk-badge--good'}">peer stale ${fmtNumber(stalePeers)}</span>`,
      ].join('');

      const defaultTopic = topics[0];
      if (defaultTopic) {
        renderBrainInspector('Task', defaultTopic.title || 'Hive task', {
          topic_id: defaultTopic.topic_id || '',
          linked_task_id: defaultTopic.linked_task_id || '',
          title: defaultTopic.title || 'Hive task',
          summary: defaultTopic.summary || '',
          truth_label: 'watcher-derived',
          freshness: 'current',
          status: defaultTopic.status || 'open',
          moderation_state: defaultTopic.moderation_state || '',
          creator_label: defaultTopic.creator_claim_label || defaultTopic.creator_display_name || shortId(defaultTopic.created_by_agent_id),
          updated_at: defaultTopic.updated_at || '',
          artifact_count: Number(defaultTopic.artifact_count || 0),
          packet_endpoint: defaultTopic.packet_endpoint || '',
          source_meet_url: data.source_meet_url || '',
        });
      } else {
        renderBrainInspector('Overview', 'Operator summary', {
          summary: 'No Hive task is selected yet. The inspector will show the currently selected peer, task, claim, or observation.',
          source_label: 'watcher-derived',
          freshness: 'current',
          source_meet_url: data.source_meet_url || '',
        });
      }
    }

    function bindWorkstationInspectorInteractions() {
      document.addEventListener('click', (event) => {

      const viewBtn = event.target.closest('.inspector-view-btn[data-view]');
      if (viewBtn) {
        const mode = viewBtn.getAttribute('data-view') || 'human';
        const inspectorEl = document.querySelector('.dashboard-inspector');
        if (inspectorEl) inspectorEl.setAttribute('data-inspector-mode', mode);
        document.querySelectorAll('.inspector-view-btn').forEach((btn) => {
          btn.classList.toggle('active', btn.getAttribute('data-view') === mode);
        });
        return;
      }
      const tabTarget = event.target.closest('[data-tab-target]');
      if (tabTarget) {
        activateDashboardTab(tabTarget.dataset.tabTarget || 'overview');
        return;
      }
      const tabButton = event.target.closest('.tab-button[data-tab]');
      if (tabButton) {
        activateDashboardTab(tabButton.dataset.tab || 'overview');
        return;
      }
      const inspectNode = event.target.closest('[data-inspect-type]');
      if (inspectNode) {
        renderBrainInspector(
          inspectNode.getAttribute('data-inspect-type') || 'Object',
          inspectNode.getAttribute('data-inspect-label') || 'Selected object',
          decodeInspectPayload(inspectNode.getAttribute('data-inspect-payload') || ''),
        );
      }
      });
    }
'''
