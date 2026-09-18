"""Web UI dashboard — single-file HTML/JS app served from the meet server.

Renders a rich, interactive dashboard showing:
- Live task feed
- Peer network status
- Knowledge shards
- Credit balances
- System health / circuit breaker status
"""
from __future__ import annotations


def render_web_dashboard_html() -> str:
    """Return the full dashboard HTML as a string."""
    return _DASHBOARD_HTML


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VOOL Swarm Dashboard</title>
  <style>
    :root {
      --bg: #0a0a0f;
      --card: #12121a;
      --border: #1e1e2e;
      --accent: #7c3aed;
      --accent2: #06b6d4;
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #e2e8f0;
      --muted: #64748b;
      --font: 'Inter', -apple-system, sans-serif;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    .header {
      padding: 1.5rem 2rem;
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(135deg, rgba(124,58,237,0.08), rgba(6,182,212,0.05));
    }
    .logo { font-size: 1.5rem; font-weight: 700; }
    .logo span { background: linear-gradient(135deg, var(--accent), var(--accent2)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .status-badge {
      display: inline-flex; align-items: center; gap: 0.5rem;
      padding: 0.35rem 0.75rem; border-radius: 999px;
      font-size: 0.75rem; font-weight: 600;
      background: rgba(34,197,94,0.15); color: var(--success);
    }
    .status-badge .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); animation: pulse 2s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1rem; padding: 1.5rem 2rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      transition: border-color 0.2s;
    }
    .card:hover { border-color: var(--accent); }
    .card-title {
      font-size: 0.75rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--muted); margin-bottom: 1rem;
    }
    .metric-value { font-size: 2rem; font-weight: 700; }
    .metric-label { font-size: 0.8rem; color: var(--muted); }
    .metric-row { display: flex; gap: 2rem; flex-wrap: wrap; }
    .metric-item { flex: 1; min-width: 100px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { text-align: left; color: var(--muted); font-weight: 600; padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
    td { padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
    .tag {
      display: inline-block; padding: 0.15rem 0.5rem;
      border-radius: 4px; font-size: 0.7rem; font-weight: 600;
    }
    .tag-success { background: rgba(34,197,94,0.15); color: var(--success); }
    .tag-warning { background: rgba(245,158,11,0.15); color: var(--warning); }
    .tag-danger { background: rgba(239,68,68,0.15); color: var(--danger); }
    .tag-info { background: rgba(6,182,212,0.15); color: var(--accent2); }
    .feed { max-height: 300px; overflow-y: auto; }
    .feed-item {
      padding: 0.75rem; border-bottom: 1px solid var(--border);
      display: flex; gap: 0.75rem; align-items: flex-start;
      transition: background 0.15s;
    }
    .feed-item:hover { background: rgba(124,58,237,0.05); }
    .feed-icon { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .feed-body { flex: 1; }
    .feed-title { font-size: 0.85rem; font-weight: 500; }
    .feed-time { font-size: 0.7rem; color: var(--muted); }
    .progress-bar { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; margin-top: 0.5rem; }
    .progress-fill { height: 100%; border-radius: 2px; transition: width 0.5s ease; }
    .full-width { grid-column: 1 / -1; }
    @media (max-width: 768px) {
      .grid { padding: 1rem; grid-template-columns: 1fr; }
      .header { padding: 1rem; }
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="logo"><span>VOOL</span> Swarm</div>
    <div>
      <span class="status-badge"><span class="dot"></span> Live</span>
    </div>
  </div>

  <div class="grid" id="dashboard">
    <!-- Metrics cards -->
    <div class="card">
      <div class="card-title">Network Overview</div>
      <div class="metric-row">
        <div class="metric-item">
          <div class="metric-value" id="m-peers">~</div>
          <div class="metric-label">Active Peers</div>
        </div>
        <div class="metric-item">
          <div class="metric-value" id="m-tasks">~</div>
          <div class="metric-label">Total Tasks</div>
        </div>
        <div class="metric-item">
          <div class="metric-value" id="m-knowledge">~</div>
          <div class="metric-label">Knowledge Shards</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">System Health</div>
      <div class="metric-row">
        <div class="metric-item">
          <div class="metric-value" id="m-uptime">~</div>
          <div class="metric-label">Uptime</div>
        </div>
        <div class="metric-item">
          <div class="metric-value" id="m-circuits">~</div>
          <div class="metric-label">Circuit Breakers</div>
        </div>
        <div class="metric-item">
          <div class="metric-value" id="m-queue">~</div>
          <div class="metric-label">Queue Depth</div>
        </div>
      </div>
    </div>

    <!-- Live Task Feed -->
    <div class="card full-width">
      <div class="card-title">Live Task Feed</div>
      <div class="feed" id="task-feed">
        <div class="feed-item">
          <div class="feed-icon" style="background:rgba(124,58,237,0.15);">🧠</div>
          <div class="feed-body">
            <div class="feed-title">Waiting for tasks...</div>
            <div class="feed-time">Dashboard connected</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Peer Table -->
    <div class="card full-width">
      <div class="card-title">Connected Peers</div>
      <table>
        <thead><tr><th>Peer ID</th><th>Region</th><th>Score</th><th>Tasks</th><th>Status</th></tr></thead>
        <tbody id="peer-table">
          <tr><td colspan="5" style="color:var(--muted)">Loading peer data...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Credit Balances -->
    <div class="card">
      <div class="card-title">Credit Economy</div>
      <div id="credit-info" style="color:var(--muted);">Loading...</div>
    </div>

    <!-- Knowledge Browser -->
    <div class="card">
      <div class="card-title">Knowledge Shards</div>
      <div id="knowledge-list" style="color:var(--muted);">Loading...</div>
    </div>
  </div>

  <script>
    const BASE = window.location.origin;
    async function fetchJSON(path) {
      try { const r = await fetch(BASE + path); return await r.json(); }
      catch(e) { return null; }
    }

    async function refreshDashboard() {
      const dashboard = await fetchJSON('/v1/hive/dashboard');
      if (dashboard) {
        document.getElementById('m-peers').textContent = dashboard.active_peers || 0;
        document.getElementById('m-tasks').textContent = dashboard.total_tasks || 0;
        document.getElementById('m-knowledge').textContent = dashboard.knowledge_shards || 0;
        document.getElementById('m-uptime').textContent = formatUptime(dashboard.uptime_seconds || 0);
        document.getElementById('m-circuits').textContent = dashboard.circuits_healthy || '~';
        document.getElementById('m-queue').textContent = dashboard.queue_depth || 0;
      }

      const peers = await fetchJSON('/v1/peers');
      if (peers && peers.peers) {
        const tbody = document.getElementById('peer-table');
        tbody.innerHTML = peers.peers.slice(0, 20).map(p =>
          `<tr>
            <td style="font-family:monospace;font-size:0.75rem">${(p.peer_id||'').slice(0,12)}…</td>
            <td>${p.region || '?'}</td>
            <td>${(p.score||0).toFixed(2)}</td>
            <td>${p.tasks_completed||0}</td>
            <td><span class="tag ${p.online ? 'tag-success' : 'tag-warning'}">${p.online ? 'online' : 'idle'}</span></td>
          </tr>`
        ).join('');
      }
    }

    function formatUptime(s) {
      if (s < 60) return s + 's';
      if (s < 3600) return Math.floor(s/60) + 'm';
      if (s < 86400) return Math.floor(s/3600) + 'h';
      return Math.floor(s/86400) + 'd';
    }

    refreshDashboard();
    setInterval(refreshDashboard, 10000);
  </script>
</body>
</html>"""
