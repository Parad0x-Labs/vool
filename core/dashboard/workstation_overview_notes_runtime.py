from __future__ import annotations

"""Workstation overview note rendering helpers."""

WORKSTATION_OVERVIEW_NOTES_RUNTIME = '''
    function renderWatchStationNotes(activeTopics, stats, stalePeers, blockedEvents, recentChangePreview) {
      document.getElementById('watchStationNotes').innerHTML = [
        `<article class="card"><h3>Active</h3><p>${esc(activeTopics.length ? `${activeTopics.length} tasks are live, with ${fmtNumber(stats.active_agents || 0)} distinct peers active now.` : 'No active task flow is visible.')}</p></article>`,
        `<article class="card"><h3>Stale</h3><p>${esc(stalePeers.length ? `${stalePeers.length} peer rows look stale and should be treated as stale watcher evidence, not live operators.` : 'No stale peer rows are visible right now.')}</p></article>`,
        `<article class="card"><h3>Failed</h3><p>${esc(blockedEvents.length ? `${blockedEvents.length} blocked or challenged task events need operator review.` : 'No blocked or challenged task is visible right now.')}</p></article>`,
        `<article class="card"><h3>Changed</h3><p>${esc(recentChangePreview || 'No fresh change signals are visible yet.')}</p></article>`,
      ].join('');
    }
'''
