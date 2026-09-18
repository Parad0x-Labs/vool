from __future__ import annotations

"""Shared learning-program card runtime fragment for the workstation dashboard."""

WORKSTATION_LEARNING_PROGRAM_SHARED_RUNTIME = '''
    function renderLearningMiniStats(items) {
      return `
        <div class="mini-grid">
          ${items.map(([label, value]) => `
            <div class="mini-stat">
              <strong>${esc(value)}</strong>
              <div>${esc(label)}</div>
            </div>
          `).join('')}
        </div>
      `;
    }

    function renderLearningProgramCard({title, summaryText, chipsHtml, bodyHtml, open = false, openStateKey = ''}) {
      return `
        <details class="learning-program" data-open-key="${esc(openStateKey || openKey('program', title || 'learning-program'))}"${open ? ' open' : ''}>
          <summary>
            <div class="learning-program-head">
              <div>
                <h3 class="learning-program-title">${esc(title)}</h3>
                <div class="small">${esc(summaryText)}</div>
              </div>
              <span class="chip" data-open-chip>${esc(open ? 'expanded' : 'expand')}</span>
            </div>
            <div class="row-meta">${chipsHtml}</div>
          </summary>
          <div class="learning-program-body">${bodyHtml}</div>
        </details>
      `;
    }
'''
