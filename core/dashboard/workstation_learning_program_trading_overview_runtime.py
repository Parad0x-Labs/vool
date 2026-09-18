from __future__ import annotations

"""Trading learning-program overview and decision runtime helper."""

WORKSTATION_LEARNING_PROGRAM_TRADING_OVERVIEW_RUNTIME = '''
    function renderTradingProgramOverviewSection(summary, decision) {
      const passReasons = decision.top_pass_reasons || [];
      const tradingOverviewHtml = renderLearningMiniStats([
        ['Token learnings', summary.token_learnings || 0],
        ['Missed mooners', summary.missed_opportunities || 0],
        ['Discoveries', summary.discoveries || 0],
        ['Hidden edges', summary.hidden_edges || 0],
        ['Patterns', summary.mined_patterns || 0],
        ['Learning events', summary.learning_events || 0],
      ].map(([label, value]) => [label, fmtNumber(value)]));

      return `
        <article class="card">
          <h3>Overview</h3>
          ${tradingOverviewHtml}
        </article>
        <article class="card">
          <h3>Decision Funnel</h3>
          <div class="row-meta">
            ${chip(`PASS ${fmtNumber(decision.pass || 0)}`)}
            ${chip(`BUY_REJECTED ${fmtNumber(decision.buy_rejected || 0)}`, 'warn')}
            ${chip(`BUY ${fmtNumber(decision.buy || 0)}`, 'ok')}
          </div>
          <div class="small" style="margin-top:8px;">
            ${passReasons.length ? passReasons.slice(0, 6).map((row) => `${row.reason} ${fmtNumber(row.count || 0)}`).join(' · ') : 'No pass reasons posted yet.'}
          </div>
        </article>
      `;
    }
'''
