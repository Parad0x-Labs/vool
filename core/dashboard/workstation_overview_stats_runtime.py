from __future__ import annotations

"""Overview mini-stat and adaptation status runtime fragment for the workstation dashboard."""

WORKSTATION_OVERVIEW_STATS_RUNTIME = '''
    function renderOverviewMiniStats(data, movement) {
      document.getElementById('overviewMiniStats').innerHTML = [
        {
          label: 'Distinct peers',
          value: movement.peerSummary.distinctVisible,
          payload: {
            title: 'Distinct visible peers',
            summary: `${fmtNumber(movement.peerSummary.distinctVisible)} distinct peers remain after collapsing duplicate watcher leases.`,
            truth_label: 'watcher-derived',
            freshness: movement.stalePeers.length ? 'mixed' : 'current',
            status: movement.peerSummary.distinctOnline > 0 ? 'active' : 'quiet',
            source_meet_url: data.source_meet_url || '',
            visible_agents: movement.peerSummary.distinctVisible,
            raw_visible_agents: movement.peerSummary.rawVisible,
            duplicate_visible_agents: movement.peerSummary.duplicates,
          },
        },
        {
          label: 'Raw presence rows',
          value: movement.peerSummary.rawVisible,
          payload: {
            title: 'Raw watcher presence rows',
            summary: `${fmtNumber(movement.peerSummary.rawVisible)} raw watcher rows are currently visible.`,
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: movement.peerSummary.duplicates > 0 ? 'deduped' : 'clean',
            source_meet_url: data.source_meet_url || '',
            raw_visible_agents: movement.peerSummary.rawVisible,
            raw_online_agents: movement.peerSummary.rawOnline,
          },
        },
        {
          label: 'Collapsed duplicates',
          value: movement.peerSummary.duplicates,
          payload: {
            title: 'Duplicate watcher leases',
            summary: movement.peerSummary.duplicates
              ? `${fmtNumber(movement.peerSummary.duplicates)} duplicate watcher presence rows were collapsed out of the visible peer count.`
              : 'No duplicate watcher leases are visible right now.',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: movement.peerSummary.duplicates ? 'deduped' : 'clear',
            source_meet_url: data.source_meet_url || '',
            duplicate_visible_agents: movement.peerSummary.duplicates,
          },
        },
        {
          label: 'Active claims',
          value: movement.activeClaims.length,
          payload: {
            title: 'Active claims',
            summary: movement.activeClaims.length
              ? `${fmtNumber(movement.activeClaims.length)} claims are still active in the current watcher/public Hive view.`
              : 'No active claims are visible right now.',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: movement.activeClaims.length ? 'active' : 'quiet',
            source_meet_url: data.source_meet_url || '',
          },
        },
        {
          label: 'Recent events',
          value: movement.events.length,
          payload: {
            title: 'Recent task events',
            summary: movement.events.length
              ? `${fmtNumber(movement.events.length)} recent watcher-derived task events are visible.`
              : 'No recent task events are visible right now.',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: movement.events.length ? 'moving' : 'quiet',
            source_meet_url: data.source_meet_url || '',
          },
        },
        {
          label: 'Recent observations',
          value: Array.isArray(data.recent_posts) ? data.recent_posts.length : 0,
          payload: {
            title: 'Recent observations',
            summary: Array.isArray(data.recent_posts) && data.recent_posts.length
              ? `${fmtNumber(data.recent_posts.length)} recent watcher observations are visible.`
              : 'No recent watcher observations are visible right now.',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: Array.isArray(data.recent_posts) && data.recent_posts.length ? 'moving' : 'quiet',
            source_meet_url: data.source_meet_url || '',
          },
        },
      ].map((item) => `
        <div class="mini-stat" ${inspectAttrs('Observation', item.label, item.payload)}>
          <strong>${fmtNumber(item.value)}</strong>
          <div>${esc(item.label)}</div>
        </div>
      `).join('');
    }

    function renderAdaptationStatusLine(adaptation) {
      const latestEval = adaptation.latest_eval || {};
      const adaptationChips = [
        chip(`loop ${adaptation.status || 'idle'}`),
        chip(`decision ${adaptation.decision || 'none'}`),
        chip(`blocker ${adaptation.blocker || 'none'}`),
        chip(`proof ${adaptation.proof_state || 'no_recent_eval'}`, adaptation.proof_state === 'candidate_beating_baseline' ? 'ok' : ''),
        chip(`ready ${fmtNumber(adaptation.training_ready || 0)}`, (adaptation.training_ready || 0) > 0 ? 'ok' : ''),
        chip(`high signal ${fmtNumber(adaptation.high_signal || 0)}`, (adaptation.high_signal || 0) > 0 ? 'ok' : ''),
      ];
      if (latestEval.eval_id) {
        const delta = Number(latestEval.score_delta || 0);
        adaptationChips.push(chip(`eval Δ ${delta.toFixed(3)}`, delta >= 0 ? 'ok' : 'warn'));
        adaptationChips.push(chip(`candidate ${Number(latestEval.candidate_score || 0).toFixed(2)}`));
      }
      document.getElementById('adaptationStatusLine').innerHTML = adaptationChips.join('');
    }
'''
