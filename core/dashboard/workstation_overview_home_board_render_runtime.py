from __future__ import annotations

"""Workstation overview home-board render helpers."""

WORKSTATION_OVERVIEW_HOME_BOARD_RENDER_RUNTIME = '''
    function renderWorkstationHomeBoardCards(items) {
      return items.map((item) => `
        <article class="dashboard-home-card" ${inspectAttrs('Observation', item.label, item.payload)}>
          <span>${esc(item.label)}</span>
          <strong>${esc(item.value)}</strong>
          <p>${esc(item.detail)}</p>
        </article>
      `).join('');
    }
'''
