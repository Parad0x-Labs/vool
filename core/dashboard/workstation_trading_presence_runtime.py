from __future__ import annotations

"""Trading presence selectors for the workstation dashboard client template."""

WORKSTATION_TRADING_PRESENCE_RUNTIME = '''
    function latestTradingPresence(trading) {
      const heartbeat = trading?.latest_heartbeat || {};
      const summary = trading?.latest_summary || {};
      const topics = Array.isArray(trading?.topics) ? trading.topics : [];
      let latestMs = 0;
      let source = 'unknown';
      const consider = (value, label) => {
        const candidateMs = parseDashboardTs(value);
        if (candidateMs > latestMs) {
          latestMs = candidateMs;
          source = label;
        }
      };
      consider(heartbeat?.last_tick_ts, 'tick');
      consider(heartbeat?.post_created_at, 'heartbeat post');
      consider(summary?.post_created_at, 'summary post');
      topics.forEach((topic) => {
        consider(topic?.updated_at, 'topic');
        consider(topic?.created_at, 'topic');
      });
      return {latestMs, source};
    }

    function tradingPresenceState(trading, generatedAt, agents) {
      const generatedMs = parseDashboardTs(generatedAt) || Date.now();
      const nowMs = Number.isFinite(generatedMs) ? generatedMs : Date.now();
      const presence = latestTradingPresence(trading);
      if (presence.latestMs > 0) {
        const ageSec = Math.max(0, (nowMs - presence.latestMs) / 1000);
        if (ageSec <= 300) return {label: 'LIVE', kind: 'ok', ageSec, source: presence.source};
        if (ageSec <= 1800) return {label: 'STALE', kind: 'warn', ageSec, source: presence.source};
        return {label: 'OFFLINE', kind: 'warn', ageSec, source: presence.source};
      }
      const scanner = (Array.isArray(agents) ? agents : []).find((agent) => {
        const agentId = String(agent?.agent_id || '').trim().toLowerCase();
        const label = String(agent?.display_name || agent?.claim_label || '').trim().toLowerCase();
        return agentId === 'vool:trading-scanner' || label === 'vool trading scanner';
      });
      const status = String(scanner?.status || '').trim().toLowerCase();
      if (status === 'online') return {label: 'LIVE', kind: 'ok', ageSec: null, source: 'agent'};
      if (status === 'stale') return {label: 'STALE', kind: 'warn', ageSec: null, source: 'agent'};
      if (status === 'offline') return {label: 'OFFLINE', kind: 'warn', ageSec: null, source: 'agent'};
      return {label: 'UNKNOWN', kind: 'warn', ageSec: null, source: 'unknown'};
    }

    function tradingHeartbeatState(heartbeat, generatedAt) {
      const tickMs = parseDashboardTs(heartbeat?.last_tick_ts);
      if (!tickMs) {
        return {label: 'UNKNOWN', kind: 'warn', ageSec: null};
      }
      const generatedMs = parseDashboardTs(generatedAt) || Date.now();
      const nowMs = Number.isFinite(generatedMs) ? generatedMs : Date.now();
      const ageSec = Math.max(0, (nowMs - tickMs) / 1000);
      if (ageSec <= 300) return {label: 'LIVE', kind: 'ok', ageSec};
      if (ageSec <= 1800) return {label: 'STALE', kind: 'warn', ageSec};
      return {label: 'OFFLINE', kind: 'warn', ageSec};
    }
'''
