from __future__ import annotations

"""Trading learning-program market-pattern runtime helper."""

WORKSTATION_LEARNING_PROGRAM_TRADING_MARKET_RUNTIME = '''
    function renderTradingProgramMarketSection(patternHealth, missed, edges) {
      const byAction = patternHealth.by_action || [];
      const topPatterns = patternHealth.top_patterns || [];

      const tradingPatternHtml = `
        <article class="card">
          <h3>Pattern Bank Health</h3>
          <div class="row-meta">
            ${chip(`Total ${fmtNumber(patternHealth.total_patterns || 0)}`)}
            ${byAction.length ? byAction.map((row) => chip(`${row.action} ${fmtNumber(row.count || 0)}`)).join('') : '<span class="empty">none yet</span>'}
          </div>
          <div class="list" style="margin-top:10px;">
            ${topPatterns.length ? topPatterns.slice(0, 6).map((row) => `
              <article class="card">
                <h3>${esc(row.name || 'pattern')}</h3>
                <p>${esc((row.source || 'unknown') + ' · ' + (row.action || ''))}</p>
                <div class="row-meta">
                  ${chip(row.action || 'pattern', row.action === 'BUY' ? 'ok' : '')}
                  ${chip(`score ${Number(row.score || 0).toFixed(2)}`)}
                  ${chip(`wr ${fmtPct((Number(row.win_rate || 0)) * 100).replace('+', '')}`)}
                  ${chip(`n ${fmtNumber(row.support || 0)}`)}
                </div>
              </article>
            `).join('') : '<div class="empty">No pattern health snapshot yet.</div>'}
          </div>
        </article>
      `;

      const tradingMissedHtml = `
        <article class="card">
          <h3>Missed Mooners</h3>
          <div class="list">
            ${missed.length ? missed.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.token_name || shortId(row.token_mint || ''))}</h3>
                <p>${esc(row.why_not_bought || '')}</p>
                <div class="row-meta">
                  ${chip(fmtPct(row.potential_gain_pct || 0), 'warn')}
                  <span>${esc(fmtUsd(row.entry_mc_usd || 0))} -> ${esc(fmtUsd(row.peak_mc_usd || 0))}</span>
                </div>
                <div class="row-meta">
                  <button class="copy-button" onclick='copyText(${JSON.stringify(String(row.token_mint || ""))}, this)'>Copy CA</button>
                  <a class="copy-button" href="${esc(row.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>
                </div>
                <div class="small">${esc(row.what_to_fix || '')}</div>
              </article>
            `).join('') : '<div class="empty">No missed mooners posted yet.</div>'}
          </div>
        </article>
      `;

      const tradingEdgesHtml = `
        <article class="card">
          <h3>Hidden Edges</h3>
          <div class="list">
            ${edges.length ? edges.slice(0, 8).map((row) => `
              <article class="card">
                <h3>${esc(row.metric || 'edge')}</h3>
                <p>Range ${esc(Number(row.low || 0).toFixed(2))} to ${esc(Number(row.high || 0).toFixed(2))}</p>
                <div class="row-meta">
                  ${chip(`score ${Number(row.score || 0).toFixed(2)}`, Number(row.score || 0) > 0.15 ? 'ok' : '')}
                  ${chip(`wr ${fmtPct((Number(row.win_rate || 0)) * 100).replace('+', '')}`)}
                  ${chip(`n ${fmtNumber(row.support || 0)}`)}
                </div>
                <div class="small">expectancy ${esc(Number(row.expectancy || 0).toFixed(3))} · source ${esc(row.source || 'auto')}</div>
              </article>
            `).join('') : '<div class="empty">No hidden edges posted yet.</div>'}
          </div>
        </article>
      `;

      return `
        <div class="learning-program-grid">
          ${tradingPatternHtml}
          ${tradingMissedHtml}
        </div>
        <div class="learning-program-grid">
          ${tradingEdgesHtml}
        </div>
      `;
    }
'''
