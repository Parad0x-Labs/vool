"""
core/earnings_page.py
=====================
Earnings / Task Queue panel for OpenClaw.
Served at GET /earnings on the meet server.
Polls the VOOL API and meet server every 10 s to show:
  - Wallet pubkey + SOL/USDC balance
  - Credit balance + recent ledger entries
  - Open task queue with claim buttons
  - Mesh worker count and capability metrics
"""
from __future__ import annotations

_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Earnings · VOOL</title>
<style>
  :root {
    --bg: #0a0a0a; --panel: #111; --border: #222; --accent: #6cf;
    --green: #4c4; --amber: #fa0; --red: #f44; --purple: #a8f;
    --text: #ddd; --muted: #666; --radius: 6px; --font: "Courier New", monospace;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
    font-size: 13px; min-height: 100vh; display: flex; flex-direction: column; }

  header { border-bottom: 1px solid var(--border); padding: 12px 20px;
    display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 14px; color: var(--accent); letter-spacing: 1px; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
    animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .hdr-sub { color: var(--muted); font-size: 11px; margin-left: auto; }

  main { flex: 1; padding: 16px 20px; display: flex; flex-direction: column; gap: 16px;
    max-width: 1100px; width: 100%; margin: 0 auto; }

  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 14px; flex: 1; min-width: 200px; }
  .card-title { font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: .8px; margin-bottom: 8px; }
  .card-value { font-size: 22px; color: var(--green); font-weight: bold; letter-spacing: -0.5px; }
  .card-sub { font-size: 10px; color: var(--muted); margin-top: 4px; }

  .section-label { font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: .8px; margin-bottom: 8px; }

  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { font-size: 10px; color: var(--muted); text-align: left; padding: 4px 8px;
    border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid #181818; vertical-align: middle; }
  tr:hover td { background: #141414; }

  .badge { display: inline-block; font-size: 9px; padding: 2px 6px; border-radius: 3px;
    font-weight: bold; }
  .badge-green  { background: #0f2e0f; color: var(--green); }
  .badge-amber  { background: #2e1e00; color: var(--amber); }
  .badge-blue   { background: #0a1a2e; color: var(--accent); }
  .badge-purple { background: #1a0a2e; color: var(--purple); }
  .badge-muted  { background: #1a1a1a; color: var(--muted); }

  button.claim-btn {
    background: var(--accent); color: #000; border: none; border-radius: 4px;
    padding: 3px 10px; font-family: var(--font); font-size: 11px; font-weight: bold;
    cursor: pointer; transition: opacity .15s;
  }
  button.claim-btn:disabled { opacity: .3; cursor: default; }
  button.claim-btn:hover:not(:disabled) { opacity: .8; }

  .pubkey { font-size: 11px; color: var(--accent); word-break: break-all; }
  .empty  { color: var(--muted); font-size: 11px; padding: 10px 0; }

  footer { border-top: 1px solid var(--border); padding: 10px 20px;
    font-size: 10px; color: var(--muted); display: flex; gap: 16px; align-items: center; }
  footer a { color: var(--muted); text-decoration: none; }
  footer a:hover { color: var(--accent); }
  #last-refresh { margin-left: auto; }
</style>
</head>
<body>
<header>
  <div class="pulse" id="pulse"></div>
  <h1>⬡ Earnings &amp; Task Queue</h1>
  <span class="hdr-sub" id="worker-count">workers: —</span>
</header>

<main>
  <!-- Wallet + Credits row -->
  <div class="row">
    <div class="card">
      <div class="card-title">Credit Balance</div>
      <div class="card-value" id="credit-balance">—</div>
      <div class="card-sub">VOOL credits (simulated)</div>
    </div>
    <div class="card">
      <div class="card-title">SOL Balance</div>
      <div class="card-value" id="sol-balance">—</div>
      <div class="card-sub" id="wallet-pubkey">—</div>
    </div>
    <div class="card">
      <div class="card-title">USDC Balance</div>
      <div class="card-value" id="usdc-balance">—</div>
      <div class="card-sub">on-chain USDC (mainnet)</div>
    </div>
    <div class="card">
      <div class="card-title">Price / Token</div>
      <div class="card-value" id="price-token">—</div>
      <div class="card-sub" id="tier-info">—</div>
    </div>
  </div>

  <!-- Task Queue -->
  <div>
    <div class="section-label">Open Task Queue</div>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Task ID</th>
            <th>Type</th>
            <th>Summary</th>
            <th>Reward</th>
            <th>Priority</th>
            <th>Deadline</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="task-tbody">
          <tr><td colspan="7" class="empty">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Recent ledger entries -->
  <div>
    <div class="section-label">Recent Earnings</div>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Amount</th>
            <th>Reason</th>
            <th>Receipt ID</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody id="ledger-tbody">
          <tr><td colspan="4" class="empty">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Mesh peers -->
  <div>
    <div class="section-label">Mesh Workers</div>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Worker ID</th>
            <th>TPS</th>
            <th>Tier</th>
            <th>Price/tok</th>
            <th>Context</th>
            <th>Privacy</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="workers-tbody">
          <tr><td colspan="7" class="empty">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<footer>
  <a href="/null-browser">.null browser</a>
  <a href="/v1/wallet/info">wallet API</a>
  <a href="/v1/credits/balance">credits API</a>
  <a href="/v1/tasks/queue">tasks API</a>
  <a href="/v1/workers">workers API</a>
  <span id="last-refresh">–</span>
</footer>

<script>
const MEET = "";   // same origin

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status);
  const d = await r.json();
  return d.result !== undefined ? d.result : d;
}

function fmt(n, dec=4) {
  if (n === null || n === undefined) return "—";
  return Number(n).toFixed(dec);
}

function shortId(id) {
  if (!id) return "—";
  return id.length > 18 ? id.slice(0,8)+"…"+id.slice(-4) : id;
}

function priorityBadge(p) {
  const cls = p === "high" ? "badge-amber" : p === "low" ? "badge-muted" : "badge-blue";
  return `<span class="badge ${cls}">${p||"normal"}</span>`;
}

function tierBadge(t) {
  const cls = t === "queen" ? "badge-purple" : "badge-muted";
  return `<span class="badge ${cls}">${t||"drone"}</span>`;
}

async function claimTask(taskId, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const r = await fetch(MEET + "/v1/tasks/" + taskId + "/claim", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({}),
    });
    const d = await r.json();
    if (r.ok) {
      btn.textContent = "✓ claimed";
      btn.style.background = "var(--green)";
    } else {
      btn.textContent = (d.error || "failed").slice(0,12);
      btn.disabled = false;
    }
  } catch(e) {
    btn.textContent = "err";
    btn.disabled = false;
  }
}

async function refresh() {
  // Wallet
  try {
    const w = await fetchJSON(MEET + "/v1/wallet/info");
    document.getElementById("sol-balance").textContent = fmt(w.sol_balance, 4) + " SOL";
    document.getElementById("usdc-balance").textContent = "$" + fmt(w.usdc_balance, 2);
    const pk = w.pubkey || "—";
    document.getElementById("wallet-pubkey").textContent = pk.slice(0,8)+"…"+pk.slice(-8);
    document.getElementById("wallet-pubkey").title = pk;
  } catch(e) { /* ignore */ }

  // Credits
  try {
    const c = await fetchJSON(MEET + "/v1/credits/balance");
    document.getElementById("credit-balance").textContent = fmt(c.balance, 2);
    const tbody = document.getElementById("ledger-tbody");
    const entries = c.entries || [];
    if (!entries.length) {
      tbody.innerHTML = "<tr><td colspan='4' class='empty'>No earnings yet</td></tr>";
    } else {
      tbody.innerHTML = entries.map(e => `
        <tr>
          <td style="color:${e.amount>0?'var(--green)':'var(--red)'}">${e.amount>0?"+":""}${fmt(e.amount,2)}</td>
          <td>${e.reason||"—"}</td>
          <td style="color:var(--muted);font-size:10px">${shortId(e.receipt_id)}</td>
          <td style="color:var(--muted);font-size:10px">${(e.timestamp||"").slice(0,16).replace("T"," ")}</td>
        </tr>`).join("");
    }
  } catch(e) { /* ignore */ }

  // Tasks
  try {
    const tasks = await fetchJSON(MEET + "/v1/tasks/queue");
    const tbody = document.getElementById("task-tbody");
    if (!tasks.length) {
      tbody.innerHTML = "<tr><td colspan='7' class='empty'>No open tasks</td></tr>";
    } else {
      tbody.innerHTML = tasks.map(t => {
        const pts = (t.reward_hint||{}).points || 0;
        const btnId = "btn-"+t.task_id;
        return `<tr>
          <td style="font-size:10px;color:var(--muted)">${shortId(t.task_id)}</td>
          <td><span class="badge badge-blue">${t.subtask_type||t.task_type||"—"}</span></td>
          <td style="max-width:280px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis" title="${t.summary||""}">${t.summary||"—"}</td>
          <td style="color:var(--amber)">${pts} pts</td>
          <td>${priorityBadge(t.priority)}</td>
          <td style="font-size:10px;color:var(--muted)">${(t.deadline_ts||"").slice(0,16).replace("T"," ")}</td>
          <td><button class="claim-btn" id="${btnId}" onclick="claimTask('${t.task_id}',this)">Claim</button></td>
        </tr>`;
      }).join("");
    }
  } catch(e) {
    document.getElementById("task-tbody").innerHTML = "<tr><td colspan='7' class='empty'>Error loading tasks</td></tr>";
  }

  // Workers
  try {
    const data = await fetchJSON(MEET + "/v1/workers?active_only=true");
    const workers = Array.isArray(data) ? data : (data.workers || []);
    document.getElementById("worker-count").textContent = "workers: " + workers.length;
    const tbody = document.getElementById("workers-tbody");
    // Capability data (price/tier) from own entry
    const me = workers.find(w => w.active);
    if (me) {
      document.getElementById("price-token").textContent = "$"+fmt(me.price_per_token_usdc, 7);
      document.getElementById("tier-info").textContent = me.top_tier + " · " + fmt(me.top_tps,1) + " t/s";
    }
    if (!workers.length) {
      tbody.innerHTML = "<tr><td colspan='7' class='empty'>No workers announced</td></tr>";
    } else {
      tbody.innerHTML = workers.map(w => `
        <tr>
          <td style="font-size:11px">${w.worker_id||"—"}</td>
          <td style="color:var(--green)">${fmt(w.top_tps,1)}</td>
          <td>${tierBadge(w.top_tier)}</td>
          <td style="font-size:10px;color:var(--muted)">$${fmt(w.price_per_token_usdc,7)}</td>
          <td style="font-size:10px;color:var(--muted)">${(w.context_window||0).toLocaleString()}</td>
          <td><span class="badge ${w.privacy_mode==='plain'?'badge-muted':'badge-purple'}">${w.privacy_mode||"plain"}</span></td>
          <td><span class="badge ${w.active?'badge-green':'badge-muted'}">${w.active?"live":"expired"}</span></td>
        </tr>`).join("");
    }
  } catch(e) { /* ignore */ }

  document.getElementById("last-refresh").textContent =
    "refreshed " + new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


def render_earnings_html() -> str:
    return _PAGE


__all__ = ["render_earnings_html"]
