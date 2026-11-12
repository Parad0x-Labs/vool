/* VOOL kernel trace — client. Renders the layered DAG + drives inspector. */
const $ = (s) => document.querySelector(s);

const state = {
  turns: [],
  current: null,
  graph: null,
  step: 0,
  tilt: 0,
  _iv: null,
};

const CODE_MAP = {
  "model.extract": "repl.py:1584 _extract() obligation extraction (fail-closed :1608)",
  "kernel.today": "repl.py:1733 kernel.today — date sticker / user receipt",
  "web.search": "repl.py:2023 runner.run('web.search.'+provider) — one egress door",
  "model.derive": "repl.py:2087 model.derive — derived computations (token expr)",
  "kernel.arith": "repl.py:1894/:2138 kernel.arith — _eval_arith",
  "machine.": "repl.py:1975 machine.<op> — machine ops registry",
  "model.synthesize": "repl.py:2389 model.synthesize — claim synthesis",
  "model.verify": "repl.py:2946 model.verify — verifier tier",
  "kernel.fastpath": "repl.py:2918 verifier skipped (single kernel-computed claim)",
  "web.fetch_page": "repl.py:3164 web.fetch_page — page body read, one door",
  "model.compose": "repl.py:2758 claimless-compose raw fallback",
  "session.recall": "repl.py:1988 session.recall — reads the session journal",
};
function codeHint(label) {
  for (const k of Object.keys(CODE_MAP)) {
    if (label.startsWith(k)) return CODE_MAP[k];
  }
  return "no static code hint";
}

/* ---------- helpers ---------- */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

/* ---------- API ---------- */
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(((await r.json().catch(()=>{})) || {}).error || r.status);
  return r.json();
}

/* ---------- turn list ---------- */
function renderList() {
  const host = $("#tl-items");
  host.innerHTML = "";
  for (const t of state.turns) {
    const d = document.createElement("div");
    d.className = "turn-item" + (t.turn_index === state.current ? " sel" : "");
    let commit = "—", cls = "";
    const c = (t.commit || "").toLowerCase();
    if (t.crashed) { commit = "crash"; cls = "t-refused"; }
    else if (c.includes("partial") || c.includes("refused") || c.includes("settled")) { commit = "partial"; cls = "t-partial"; }
    else if (c.includes("committed")) { commit = "committed"; cls = "t-committed"; }
    const tags = [t.web_torn ? "⛔web" : "", t.machine_torn ? "⛔machine" : ""].filter(Boolean).join(" ");
    d.innerHTML =
      `<div class="q">#${t.turn_index} ${esc(t.question)}</div>` +
      `<div class="meta"><span class="commit-tag ${cls}">${commit}</span> ` +
      `${t.effects.length}fx${tags ? " · " + tags : ""}</div>`;
    d.onclick = () => selectTurn(t.turn_index);
    host.appendChild(d);
  }
}

/* ---------- DAG render ---------- */
function drawGraph(g) {
  if (!g) return;
  const L = g.layers.length;
  const svg = $("#dag");
  const rect = svg.getBoundingClientRect();
  const W = Math.max(rect.width || 900, 700);
  const H = Math.max(rect.height || 600, 420);
  const colW = W / L, rowH = 46, top = 34;
  const colIndex = {};
  g.layers.forEach((name, i) => { colIndex[name] = i; });

  // lane bands
  let layersHtml = `<rect x="0" y="0" width="${W}" height="${H}" fill="transparent"/>`;
  g.layers.forEach((name, i) => {
    const x = i * colW;
    layersHtml += `<rect class="lane-band" x="${x}" y="16" width="${colW-14}" height="${H-60}"/>` +
      `<text class="layer-label" x="${x+8}" y="12">${name}</text>`;
  });
  $("#layers").innerHTML = layersHtml;

  // position nodes by column+index
  const pos = {}, colCount = {};
  for (const n of g.nodes) {
    const c = colIndex[n.layer] != null ? colIndex[n.layer] : L - 1;
    const i = colCount[c] = (colCount[c] || 0) + 1;
    pos[n.id] = { x: c * colW + 8, y: top + (i - 1) * rowH };
  }

  // edges (effect chain + stamp->node + answer)
  let eg = "";
  for (const e of g.edges) {
    const a = pos[e.src], b = pos[e.dst];
    if (!a || !b) continue;
    const hot = e.src !== "stamp";
    eg += `<line class="edge${hot ? " edge-hot" : ""}" x1="${a.x+65}" y1="${a.y+40}" x2="${b.x+65}" y2="${b.y}"/>`;
  }
  $("#edges").innerHTML = eg;

  // nodes
  let ng = "";
  for (const n of g.nodes) {
    const p = pos[n.id];
    if (!p) continue;
    const cls = (n.kind === "obligation" ? " ob" :
                 n.kind === "stamp" ? " stamp" :
                 n.kind === "answer" ? " answer" : "") + (!n.ok ? " bad" : " ok");
    const label = sub(n);
    ng += `<g class="node" data-id="${esc(n.id)}" data-label="${esc(n.label)}" data-detail="${esc(n.detail||"")}">` +
      `<rect class="node-rect${cls}" x="${p.x}" y="${p.y}" width="130" height="40"/>` +
      `<text class="node-text" x="${p.x+65}" y="${p.y+19}" text-anchor="middle">${esc(trunc(n.label, 22))}</text>` +
      `<text class="node-sub" x="${p.x+65}" y="${p.y+33}" text-anchor="middle">${esc(trunc(label, 24))}</text></g>`;
  }
  $("#nodes").innerHTML = ng;
  document.querySelectorAll("#nodes g.node").forEach(el => {
    el.onclick = e => showNodeDetail(el.dataset.id, el);
  });

  $("#dag").style.transform = state.tilt ? `rotateY(${state.tilt * 6}deg) scaleX(${1 - 0.05 * state.tilt})` : "";
}
function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function sub(n) {
  if (n.kind === "effect") return n.ok ? "ok" : trunc(n.detail || "error", 18);
  if (n.kind === "obligation") { const bits = n.label.split("·"); return trunc(bits[2] || bits[1] || "", 20); }
  return n.label;
}

function showNodeDetail(id, meta) {
  const d = $("#detail");
  d.classList.remove("hidden");
  d.innerHTML = `<div class="d-head">${esc(meta.label)}</div>${esc(meta.detail || "(no detail)")}`;
  $("#code-pane").textContent = codeLoc(meta.label);
}
function codeLoc(label) {
  for (const k of Object.keys(CODE_MAP)) if (label.startsWith(k)) return CODE_MAP[k];
  return "no static code hint for this node";
}

/* ---------- turn panel ---------- */
function renderTurn(t) {
  $("#turn-detail").innerHTML = `<div class="kv">` +
    `<span class="k">question</span><span class="v">${esc(t.question)}</span>` +
    `<span class="k">commit</span><span class="v">${esc(t.commit || "—")}</span>` +
    `<span class="k">answer</span><span class="v">${esc((t.answer || "").slice(0, 320))}</span>` +
    `<span class="k">stamps</span><span class="v">${esc(JSON.stringify(t.stamps))}</span>` +
    `<span class="k">crashed</span><span class="v">${t.crashed ? esc(t.crash_exception || "yes") : "no"}</span>` +
    `</div>`;
}

/* ---------- step playback ---------- */
function playEffect() {
  if (!state.graph) return;
  const effs = state.graph.nodes.filter(n => n.kind === "effect")
    .sort((a, b) => (a.step || 0) - (b.step || 0));
  if (!effs.length) return;
  const cur = effs[state.step % effs.length];
  state.step++;
  document.querySelectorAll("#nodes g.node").forEach(el => {
    el.classList.toggle("node-glow", el.dataset.id === cur.id);
  });
  if (cur) { const m = { label: cur.label, detail: cur.detail }; showNodeDetail(cur.id, m); }
}
function stopEffects() { clearInterval(state._iv); state.playing = false; }

/* ---------- boot ---------- */
async function refresh() {
  try {
    const d = await api("/api/state");
    $("#hdr-journal").textContent = "journal: " + (d.journal || "").split("/").pop();
    state.turns = d.turns;
    renderList();
    $("#hdr-health").textContent = d.turns.length + " turns";
  } catch (e) { $("#hdr-health").textContent = "offline"; }
}
async function selectTurn(n) {
  state.current = n;
  renderList();
  try {
    const t = await api(`/api/turn/${n}`);
    state.currentData = t;
    state.graph = t.graph;
    state.step = 0;
    drawGraph(t.graph);
    renderTurn(t);
    $("#stage-title").textContent = `Turn ${n}`;
    $("#prompt-pane").innerHTML = "<em>Click a model effect node to inspect it.</em>";
  } catch (e) { $("#stage-title").textContent = "Error: " + e.message; }
}

async function boot() {
  $("#refresh").onclick = refresh;
  $("#reset").onclick = () => { $("#dag").style.transform = ""; state.tilt = 0; $("#show3d").checked = false; };
  $("#show3d").onchange = e => { state.tilt = e.target.checked ? 1 : 0; if (state.graph) drawGraph(state.graph); };
  $("#play").onclick = () => { playEffect(); };
  document.getElementById("toggle-glow")?.addEventListener("click", () => {});
  await refresh();
}
boot();
