/* VOOL Kernel Lab — interactive 3D trace bench.
 * Reads /api/turn/<n> graph JSON: stages are lanes, nodes are chips, a
 * signal sphere animates the effect tape. Offline three.js (vendored).
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (s) => document.querySelector(s);
const api = async (p) => {
  const r = await fetch(p);
  if (!r.ok) throw new Error(((await r.json().catch(()=>{}))||{}).error || r.status);
  return r.json();
};
const esc = (s) => String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const LANE_COLORS = {
  context:"#3b6ea8", extract:"#7d5cff", derive:"#bf7bff", synthesize:"#ff9d5c",
  web:"#39d0ff", compute:"#3fe08a", verify:"#ffc24b", repair:"#ff6b9d",
  recall:"#6fffc9", render:"#ffd166", other:"#5c6b84",
};
const STATUS_COLOR = {
  "closed":"#3fe08a", "open":"#ffc24b", "declared unanswerable":"#ff9d5c",
  "declared_unanswerable":"#ff9d5c", "refused":"#ff5d6b",
  "failed_internal":"#ff5d6b", "cancelled":"#8a8fa3",
};
const statusOf = (n) => STATUS_COLOR[n.status_label] || STATUS_COLOR[n.status] ||
  (String(n.label).includes("·") ? STATUS_COLOR[String(n.label).split("·").map(s=>s.trim())[2]||""] : undefined);

const state = { turns: [], current: null, graph: null, effect: [], step: 0,
  playing: null, nodeGrp: {}, bounds: {} };

/* ---------------- scene ---------------- */
let renderer, scene, camera, controls, raycaster, signal;
let sceneModel = [];

function initScene(container) {
  const w = container.clientWidth, h = container.clientHeight;
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);
  renderer.domElement.classList.add("vr");

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(46, w / h, .1, 3000);
  camera.position.set(0, 15, 20); camera.lookAt(0, 0, 0);

  scene.add(new THREE.AmbientLight(0x22303f, .8));
  scene.add(new THREE.HemisphereLight(0x9fd4ff, 0x20242c, .6));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(8, 22, 14); scene.add(key);
  const rim = new THREE.PointLight(0x39d0ff, .5, 70);
  rim.position.set(-12, 6, -14); scene.add(rim);

  scene.add(new THREE.GridHelper(320, 64, 0x1a2636, 0x10161f));

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = .08;
  controls.minDistance = 4; controls.maxDistance = 90;
  controls.maxPolarAngle = Math.PI / 2.05;

  raycaster = new THREE.Raycaster();
  window.addEventListener("resize", () => {
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
  });
  bindPointer(renderer.domElement);
  function tick() { requestAnimationFrame(tick); controls.update(); renderer.render(scene, camera); }
  tick();
}

/* ---------------- geometry ---------------- */
function labelSprite(text, color = "#d8e1ef", wide = 3.0) {
  const c = document.createElement("canvas"); c.width = 512; c.height = 140;
  const g = c.getContext("2d");
  g.clearRect(0, 0, 512, 140);
  g.font = "700 44px Menlo,Consolas,monospace";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.shadowColor = "rgba(0,0,0,.85)"; g.shadowBlur = 10;
  g.fillStyle = color;
  g.fillText(String(text).slice(0, 30), 256, 68);
  const t = new THREE.CanvasTexture(c); t.anisotropy = 4;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false }));
  sp.scale.set(wide, wide * (140 / 512), 1);
  return sp;
}

function chipMesh(n) {
  const kind = n.kind, ok = !!n.ok;
  const dims = kind === "answer" ? [3.8, .75, 2.5]
             : kind === "stamp" ? [1.5, .6, 1.25]
             : kind === "obligation" ? [1.2, .42, 1.2]
             : [1.7, .5, 1.35];
  const base = kind === "answer" ? "#ffd166"
             : kind === "obligation" ? (statusOf(n) || "#9aa8bd")
             : (LANE_COLORS[n.layer] || "#5c6b84");
  const color = kind === "effect" && !ok ? "#ff5d6b" : base;
  const intensity = kind === "obligation" ? .34 : kind === "answer" ? .5 : (!ok ? .55 : .42);

  const g = new THREE.Group();
  const box = new THREE.Mesh(
    new THREE.BoxGeometry(...dims),
    new THREE.MeshStandardMaterial({ color: 0x0d1420, roughness: .4, metalness: .18,
      emissive: new THREE.Color(color), emissiveIntensity: intensity })
  );
  box.castShadow = true; box.receiveShadow = true;
  g.add(box);
  g.userData = { nodeId: n.id, base: intensity };

  if (kind === "effect" || kind === "stamp") addPins(g, dims, color);

  // status LED
  const led = new THREE.Mesh(new THREE.SphereGeometry(.065, 10, 10),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(ok ? "#3fe08a" : "#ff5d6b") }));
  led.position.y = dims[1] / 2 + .06; g.add(led);

  const lbl = labelSprite(n.label, "#e6edfb");
  lbl.position.y = dims[1] / 2 + 1.15; g.add(lbl);
  return g;
}
function addPins(g, dims, color) {
  const pin = new THREE.Mesh(new THREE.CylinderGeometry(.045,.045,.22,6),
    new THREE.MeshStandardMaterial({ color: 0x9fb4c8, metalness: .6, emissive: color, emissiveIntensity: .25 }));
  const nx = 6;
  for (let i = 0; i < nx; i++) {
    const px = (i - (nx - 1) / 2) * (dims[0] / (nx - 1)) * .9;
    for (const zz of [-.4, .4]) {
      const p = pin.clone(); p.position.set(px, -dims[1] / 2 - .06, zz * dims[2]);
      g.add(p);
    }
  }
}
function wire(a, b, color = "#2e6b96") {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length(); if (len < .01) return null;
  const unit = dir.clone().normalize();
  const mid = a.clone().add(unit.clone().multiplyScalar(len / 2));
  const cyl = new THREE.Mesh(new THREE.CylinderGeometry(.032, .032, len, 6),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .85 }));
  cyl.position.copy(mid);
  cyl.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), unit);
  return cyl;
}

/* ---------------- turn → scene ---------------- */
function clearModel() {
  for (const o of sceneModel) scene.remove(o);
  sceneModel = [];
  state.nodeGrp = {}; state.bounds = {};
}
function buildTurn(graph) {
  clearModel();
  const layers = graph.layers || [];
  const laneOf = {}; layers.forEach((L, i) => laneOf[L] = i);
  const LANE_W = 3.1, ROW_Z = 2.1;
  const xOf = i => (i - (layers.length - 1) / 2) * LANE_W;
  const rows = {};

  // lane labels + bench rails
  layers.forEach((L, i) => {
    const x = xOf(i);
    const ll = labelSprite(L.toUpperCase(), LANE_COLORS[L] || "#8da2bd", 3.0);
    ll.position.set(x, -1.5, 0); scene.add(ll); sceneModel.push(ll);
    const rail = new THREE.Mesh(new THREE.BoxGeometry(1.7, .05, 46),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(LANE_COLORS[L] || "#40556b"), transparent: true, opacity: .16 }));
    rail.position.set(x, -.3, 0); scene.add(rail); sceneModel.push(rail);
  });

  // layout
  for (const n of graph.nodes) {
    const li = laneOf[n.layer] != null ? laneOf[n.layer] : layers.length - 1;
    const r = (rows[li] = (rows[li] || 0) + 1);
    state.bounds[n.id] = { x: xOf(li), z: r * ROW_Z };
  }
  for (const n of graph.nodes) {
    const g = chipMesh(n);
    g.position.set(state.bounds[n.id].x, .4, state.bounds[n.id].z);
    g.userData = { ...g.userData, nodeId: n.id, label: n.label, detail: n.detail };
    scene.add(g); sceneModel.push(g);
    state.nodeGrp[n.id] = g;
  }
  for (const e of graph.edges) {
    const a = state.bounds[e.src], b = state.bounds[e.dst];
    if (!a || !b) continue;
    const w = wire(new THREE.Vector3(a.x, .55, a.z), new THREE.Vector3(b.x, .55, b.z));
    if (w) { scene.add(w); sceneModel.push(w); }
  }
  if (!signal) {
    signal = new THREE.Mesh(new THREE.SphereGeometry(.17, 18, 18),
      new THREE.MeshBasicMaterial({ color: 0xffffff }));
    scene.add(signal);
  }
  signal.visible = false;

  state.effect = graph.nodes.filter(n => n.kind === "effect")
    .sort((a, b) => (parseInt(String(a.id).replace(/\D/g, ""), 10) || 0) -
                    (parseInt(String(b.id).replace(/\D/g, ""), 10) || 0));
  state.graph = graph;
}
const sceneModelLook = (o) => { sceneModel.push(o); return o; };
void sceneModelLook;

/* ---------------- playback ---------------- */
function playTurn() {
  if (state.playing) { stopPlaying(); return; }
  if (!state.effect.length) return;
  state.step = 0;
  const list = state.effect;
  state.playing = setInterval(() => {
    if (state.step >= list.length) { stopPlaying(); return; }
    const target = list[state.step];
    activate(target.id);
    const grp = state.nodeGrp[target.id];
    if (grp && signal) { signal.position.set(grp.position.x, 1.6, grp.position.z); signal.visible = true; }
    $("#steplabel").textContent = `${target.id} · ${target.label}`;
    state.step++;
  }, 460);
}
function stopPlaying() {
  if (state.playing) { clearInterval(state.playing); state.playing = null; }
  resetGlow();
  if (signal) signal.visible = false;
  state.step = 0;
  $("#steplabel").textContent = "—";
  document.querySelectorAll(".scope-cell").forEach(el => el.classList.remove("scope-active"));
}
function activate(nodeId) {
  for (const id in state.nodeGrp) {
    const grp = state.nodeGrp[id];
    const mat = grp && grp.children[0] && grp.children[0].material;
    if (mat && typeof mat.emissiveIntensity === "number")
      mat.emissiveIntensity = id === nodeId ? 1.2 : (grp.userData.base || .25);
  }
  document.querySelectorAll(".scope-cell").forEach((el, i) =>
    el.classList.toggle("scope-active", i === state.step));
  const tg = state.nodeGrp[nodeId];
  if (tg && tg.userData) { const ud = tg.userData; showNodeDetail(ud.nodeId, ud.label, ud.detail); }
}
function resetGlow() {
  for (const id in state.nodeGrp) {
    const grp = state.nodeGrp[id];
    const m = grp && grp.children[0] && grp.children[0].material;
    if (m && typeof m.emissiveIntensity === "number") m.emissiveIntensity = grp.userData.base || .25;
  }
}

/* ---------------- drag + orbit ---------------- */
function ndc(canvas, ev) {
  const r = canvas.getBoundingClientRect();
  return new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1,
                           -((ev.clientY - r.top) / r.height) * 2 + 1);
}
function bindPointer(canvas) {
  canvas.addEventListener("pointerdown", (ev) => {
    raycaster.setFromCamera(ndc(canvas, ev), camera);
    const hits = raycaster.intersectObjects(scene.children, true)
      .filter(h => h.object.parent && h.object.parent.userData && h.object.parent.userData.nodeId);
    if (hits.length) {
      const nid = hits[0].object.parent.userData.nodeId;
      const grp = state.nodeGrp[nid];
      if (grp) {
        state._drag = { id: nid, grp };
        controls.enabled = false;
        canvas.classList.add("drag");
      }
    }
  });
  canvas.addEventListener("pointermove", (ev) => {
    raycaster.setFromCamera(ndc(canvas, ev), camera);
    if (state._drag) {
      const hit = new THREE.Vector3();
      const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.4);
      if (raycaster.ray.intersectPlane(plane, hit)) {
        state._drag.grp.position.x = hit.x;
        state._drag.grp.position.z = hit.z;
      }
      return;
    }
    const over = raycaster.intersectObjects(scene.children, true)
      .some(h => h.object.parent && h.object.parent.userData && h.object.parent.userData.nodeId);
    canvas.style.cursor = over ? "grab" : "default";
  });
  const up = () => {
    if (state._drag) { state._drag = null; controls.enabled = true; canvas.classList.remove("drag"); }
  };
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", up);
}

/* ---------------- HUD ---------------- */
function renderTurns(turns) {
  const host = $("#turnlist"); host.innerHTML = "";
  state.turns = turns;
  $("#turncount").textContent = turns.length;
  for (const t of turns) {
    const c = (t.commit || "").toLowerCase();
    let pill = "pill-partial", label = "partial";
    if (t.crashed) { pill = "pill-crash"; label = "crash"; }
    else if (c.includes("committed")) { pill = "pill-committed"; label = "committed"; }
    const el = document.createElement("div");
    el.className = "turn" + (t.turn_index === state.current ? " sel" : "");
    el.innerHTML = `<div class="q">#${t.turn_index} ${esc(t.question)}</div>
      <div class="m"><span class="pill ${pill}">${label}</span><span>${t.effects.length}fx</span>
      ${t.crashed ? "<span title='crash'>✖</span>" : ""}
      ${t.web_torn ? "<span title='web torn'>⛔w</span>" : ""}${t.machine_torn ? "<span title='machine torn'>⛔m</span>" : ""}</div>`;
    el.onclick = () => selectTurn(t.turn_index);
    host.appendChild(el);
  }
}
async function selectTurn(n) {
  state.current = n;
  stopPlaying();
  renderTurns(state.turns);                       // marks .sel on the rail
  const t = await api(`/api/turn/${n}`);
  state.graph = t.graph;
  buildTurn(t.graph);
  $("#turnid").textContent = n;
  $("#turnmeta").innerHTML =
    div(esc(t.question.slice(0, 130))) +
    div("commit: " + esc(t.commit || "—")) +
    div("stamps: " + esc(JSON.stringify(t.stamps))) +
    div("crash: " + (t.crashed ? esc(t.crash_exception || "yes") : "no"));
  renderLegend(t.graph.layers);
  renderScope(t.graph);
  // deep-link support: ?t=N opens that turn from the inline rail too
  selectTurnFromUrl();
  $("#statusline").textContent =
    `TURN ${n}: ${t.effects.length} effects · ${t.terminal_state.length} obligations · ` +
    `${t.graph.edges.length} wires — drag chips to move, orbit to view, ▶ to run the signal.`;
}
function selectTurnFromUrl() {
  const m = (location.search || "").match(/[?&]t=(\d+)/);
  if (m) { const n = +m[1]; if (n && n !== state.current) selectTurn(n); }
}
function div(s) { return `<div>${s}</div>`; }
function renderLegend(layers) {
  $("#legend").innerHTML = (layers || []).map(L =>
    `<span class="lg" style="border-color:${LANE_COLORS[L] || '#2a3344'};color:${LANE_COLORS[L] || '#9aa8bd'}">${L}</span>`).join("");
}
function renderScope(graph) {
  const scope = $("#scope"); scope.innerHTML = "";
  const len = Math.max(graph.effect.length, 1);
  graph.effect.forEach((e, i) => {
    const cell = document.createElement("div");
    cell.className = "scope-cell";
    cell.style.left = `${(i / len) * 100}%`;
    cell.style.width = `${(1 / len) * 100}%`;
    cell.style.background = e.ok ? (LANE_COLORS[e.layer] || "#39d0ff") : "#ff5d6b";
    cell.title = `${e.id} ${e.label}`;
    scope.appendChild(cell);
  });
}
function showNodeDetail(nodeId, label, detail) {
  $("#nodedetail").innerHTML =
    `<div style="color:var(--cyan)">${esc(label)}</div><div>${esc(detail || "(no detail)")}</div>`;
}
function renderKnobs() {
  const defs = [
    ["word-count contract", "exact-words gate"],
    ["entity binding", "labeled per-entity value owner"],
    ["harvest binding", "presence ≠ grounding"],
    ["render echo ban", "request ≠ answer"],
    ["multipart UNKNOWN", "nothing-drop backstop"],
    ["stale-aging", "5-turn carryover expiry"],
  ];
  $("#knobs").innerHTML = defs.map(([n, d]) =>
    `<div class="knob"><div><div class="k">${n}</div><div class="small muted">${d}</div></div>` +
    `<div class="sw" title="read-only during review"></div></div>`).join("");
}

/* ---------------- wiring ---------------- */
document.getElementById("btn2d").onclick = () => { location.href = "/index.html"; };
document.getElementById("btn3d").onclick = () => { location.href = "/lab.html"; };
$("#btn-play").onclick = () => playTurn();
$("#btn-next").onclick = () => {
  const i = state.turns.findIndex(t => t.turn_index === state.current);
  if (i >= 0 && i < state.turns.length - 1) selectTurn(state.turns[i + 1].turn_index);
};
$("#btn-prev").onclick = () => {
  const i = state.turns.findIndex(t => t.turn_index === state.current);
  if (i > 0) selectTurn(state.turns[i - 1].turn_index);
};

async function boot() {
  // Fetch + render the turn RUN FIRST so history is never blocked by the 3D
  // scene (e.g. if WebGL is unavailable). Scene init failures surface visibly.
  const d = await api("/api/state").catch(ex => {
    $("#statusline").textContent = "state error: " + ex.message;
    return { turns: [], journal: "" };
  });
  $("#run-id").textContent = "journal · " + (d.journal || "").split(/[\\/]/).pop();
  $("#health").textContent = (d.turns ? d.turns.length : 0) + " turns";
  renderTurns(d.turns || []);
  renderKnobs();
  if (d.turns && d.turns.length) selectTurn(d.turns[0].turn_index);
  try {
    initScene($("#viewport"));
  } catch (ex) {
    $("#statusline").textContent = "3D scene failed to init (WebGL?): " + ex.message +
      " — turns still listed on the left.";
    $("#health").textContent = "3D unavailable";
  }
}
boot().catch(e => { $("#statusline").textContent = "boot error: " + e.message; });
