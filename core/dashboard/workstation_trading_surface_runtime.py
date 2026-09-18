from __future__ import annotations

"""Markets/trading tab renderers for the workstation dashboard client template."""

WORKSTATION_TRADING_SURFACE_RUNTIME = '''
    function renderTradingMiniStats(summary, heartbeat, presenceState) {
      document.getElementById('tradingMiniStats').innerHTML = [
        ['Scanner', presenceState.label],
        ['Last seen', presenceState.ageSec == null ? 'unknown' : fmtAgeSeconds(presenceState.ageSec)],
        ['Tracked', heartbeat.tracked_tokens || 0],
        ['Open pos', heartbeat.open_positions || 0],
        ['New mints', heartbeat.new_tokens_seen || 0],
        ['Tracked calls', summary.total_calls || 0],
        ['Wins', summary.wins || 0],
        ['Mode', heartbeat.last_tick_ts ? (heartbeat.signal_only ? 'signal-only' : 'live') : 'unknown'],
        ['Safe exit', `${fmtPct(summary.safe_exit_pct || 0).replace('+', '')}`],
        ['ATH avg', fmtPct(summary.avg_ath_pct || 0)],
      ].map(([label, value]) => `
        <div class="mini-stat">
          <strong>${esc(value)}</strong>
          <div>${esc(label)}</div>
        </div>
      `).join('');
    }

    function renderTradingHeartbeat(heartbeat, summary, presenceState) {
      const heartbeatMessage = summary.total_calls
        ? 'Scanner is alive. The call table only fills when a setup actually passes the gate.'
        : 'No qualifying WATCH or ENTRY bell yet. Scanner is alive; silence is intentional until a setup passes the filters.';
      document.getElementById('tradingHeartbeatList').innerHTML = heartbeat.last_tick_ts ? `
        <article class="card">
          <h3>Scanner ${esc(presenceState.label)}</h3>
          <p>${esc(heartbeatMessage)}</p>
          <div class="row-meta">
            ${chip(presenceState.label, presenceState.kind)}
            ${chip(heartbeat.signal_only ? 'Signal only' : 'Live mode', heartbeat.signal_only ? '' : 'warn')}
            ${chip(`tick ${fmtNumber(heartbeat.tick || 0)}`)}
            ${chip(`track ${fmtNumber(heartbeat.tracked_tokens || 0)}`)}
            ${chip(`new mints ${fmtNumber(heartbeat.new_tokens_seen || 0)}`)}
          </div>
          <div class="small">
            Last tick ${esc(fmtTime(heartbeat.last_tick_ts || 0))} · Engine started ${esc(fmtTime(heartbeat.engine_started_ts || 0))} · Last Hive post ${esc(fmtTime(heartbeat.post_created_at || summary.post_created_at || 0))}
          </div>
          <div class="small" style="margin-top:6px;">
            Presence source ${esc(presenceState.source || 'unknown')} · Effective status age ${esc(presenceState.ageSec == null ? 'unknown' : fmtAgeSeconds(presenceState.ageSec))}
          </div>
          <div class="small" style="margin-top:6px;">
            Regime ${esc(heartbeat.market_regime || 'UNKNOWN')} · Poll ${esc(String(Math.round(Number(heartbeat.poll_interval_sec || 0))))}s · Track window ${esc(String(Math.round((Number(heartbeat.track_duration_sec || 0)) / 60)))}m · Max ${esc(String(heartbeat.max_tokens || 0))}
          </div>
          <div class="small" style="margin-top:6px;">
            APIs: Helius ${esc(heartbeat.helius_ready ? 'yes' : 'no')} · BirdEye ${esc(heartbeat.birdeye_ready ? 'yes' : 'no')} · Jupiter ${esc(heartbeat.jupiter_ready ? 'yes' : 'no')} · LLM ${esc(heartbeat.llm_enabled ? 'on' : 'off')} · Curiosity ${esc(heartbeat.curiosity_enabled ? 'on' : 'off')}
          </div>
        </article>
      ` : '<div class="empty">No scanner heartbeat posted yet.</div>';
    }

    function renderTradingCallTable(calls) {
      document.getElementById('tradingCallTable').innerHTML = calls.length ? calls.map((call) => `
        <tr>
          <td>
            <strong>${esc(call.token_name || shortId(call.token_mint || ''))}</strong><br />
            <span class="small">${esc(call.call_event || '')} · ${esc(call.call_status || '')}</span>
          </td>
          <td>
            <div class="mono">${esc(shortId(call.token_mint || '', 18))}</div>
            <div class="row-meta">
              <button class="copy-button" onclick='copyText(${JSON.stringify(String(call.token_mint || ""))}, this)'>Copy CA</button>
              <a class="copy-button" href="${esc(call.gmgn_url || '#')}" target="_blank" rel="noreferrer noopener">GMGN</a>
            </div>
          </td>
          <td>
            ${chip(call.call_status || 'pending', call.call_status === 'WIN' ? 'ok' : (call.call_status === 'LOSS' ? 'warn' : ''))}
            ${(call.stealth_verdict ? chip(call.stealth_verdict, call.stealth_verdict === 'ACCUMULAR' ? 'ok' : '') : '')}
          </td>
          <td>${fmtUsd(call.entry_mc_usd || 0)}</td>
          <td>
            <strong>${fmtPct(call.ath_pct || 0)}</strong><br />
            <span class="small">${fmtUsd(call.ath_mc_usd || 0)}</span>
          </td>
          <td>
            <strong>${fmtUsd(call.safe_exit_mc_usd || 0)}</strong><br />
            <span class="small">${fmtPct(call.safe_exit_pct || 0)}</span>
          </td>
          <td>
            <div>${esc(call.strategy_name || 'manual')}</div>
            <div class="small">${esc(call.stealth_summary || call.reason || '').slice(0, 64)}</div>
          </td>
        </tr>
      `).join('') : '<tr><td colspan="7" class="empty">No tracked trading calls yet.</td></tr>';
    }

    function renderTradingLessons(lessons) {
      document.getElementById('tradingLessonList').innerHTML = lessons.length ? lessons.map((item) => `
        <article class="card">
          <h3>${esc(item.token || 'Lesson')}</h3>
          <p>${esc(item.insight || '')}</p>
          <div class="row-meta">
            ${chip(item.outcome || 'learned', item.outcome === 'WIN' ? 'ok' : '')}
            <span>${fmtPct(item.pnl_pct || 0)}</span>
            <span>${fmtTime(item.ts || 0)}</span>
          </div>
        </article>
      `).join('') : '<div class="empty">No new trading lessons posted yet.</div>';
    }

    function renderTrading(data) {
      const trading = data.trading_learning || {};
      const summary = trading.latest_summary || {};
      const heartbeat = trading.latest_heartbeat || {};
      const presenceState = tradingPresenceState(trading, data.generated_at, data.agents || []);
      renderTradingMiniStats(summary, heartbeat, presenceState);
      renderTradingHeartbeat(heartbeat, summary, presenceState);
      renderTradingCallTable(trading.calls || []);
      renderInto('tradingUpdateList', renderCompactPostList(trading.recent_posts || [], {
        limit: 6,
        previewLen: 220,
        emptyText: 'No Hive trading updates yet.',
      }), {preserveDetails: true});
      renderTradingLessons(trading.lessons || []);
    }
'''
