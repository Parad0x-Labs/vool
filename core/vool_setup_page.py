"""VOOL guided setup: one step per screen, served at ``/setup`` the way Settings is served.

The page owns no state. It reads ``GET /api/setup/state`` (core/setup_progress.py: done-ness
derived from the authorities that own each value) and every action goes through a door that
already exists — ``/api/onboarding/choice``, ``/api/projects``, ``/api/settings/prefs``,
``/api/profile/remember``. Skipping a step and "Don't remind me" are ordinary preferences.

Presentation rules (operator, 2026-09-07; brief: NN/g onboarding, Apple HIG onboarding + motion,
WCAG 2.2.2 pause/stop/hide, Appcues checklist research):

- radically plain language — a title of at most five words and one sentence of at most eighteen;
- an animated EXAMPLE per step: a stylised mock of the VOOL window, a cursor that moves, clicks and
  types, then the result — labelled "Example", at most six seconds per loop including a hold,
  pausable, and a static final frame under ``prefers-reduced-motion: reduce``;
- the REAL control for the step directly under the example, or one button that opens the exact
  Settings section;
- "Skip for now" on every step, "Do this later" everywhere, Back/Next, progress dots; nothing
  modal traps the user — the chat stays usable behind it.

Self-contained: inline CSS and JS, no CDN, no external font; dark by default with a light palette
the host selects (``?theme=light`` or ``data-theme="light"`` on ``<html>``).
"""
from __future__ import annotations

import json

from core.setup_progress import STEPS

COPY = {
    "title": "Set up VOOL",
    "lead": "Four quick steps. Skip any of them — VOOL already works.",
    "later": "Do this later",
    "back": "Back",
    "next": "Next",
    "finish": "Finish",
    "skip": "Skip for now",
    "example": "Example",
    "pause": "Pause",
    "play": "Play",
    "playExample": "Play the example",
    "stepOf": "Step {n} of {total}",
    "done": "Done — you can change this any time in {later}.",
    "skipped": "Skipped — you can do this later in {later}.",
    "allDone": "All set. You can change any of this later in Settings.",
}

# Per-step caption: the text alternative for the animated example (the mock itself is aria-hidden).
CAPTIONS = {
    "thinking": "The pointer clicks “On my Mac”. A tick appears — nothing you type leaves this computer.",
    "folder": "The pointer clicks “+ Project”, picks the Documents folder, and the folder appears in the list.",
    "permissions": "The pointer opens the list and picks “Ask me first”. It saves right away.",
    "name": "The pointer clicks the box, types a name, and presses Save.",
}

_SETUP_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL Setup</title>
<style>
:root { --bg:#101216; --panel:#16191f; --field:#1d2129; --ink:#e8eaf0; --muted:#9aa1af; --accent:#5eead4; --accent-ink:#0b0f14;
        --accent-soft:rgba(94,234,212,.28); --border:#262b35; --ok:#34d399; --bad:#f87171; --mk-bg:#0c0e12; --mk-border:#2b313c; --shadow:rgba(0,0,0,.45); }
/* The light palette is a HOST choice (?theme=light, or data-theme set on <html>), not the OS setting:
   the chat and Settings surfaces are dark-only today, and a framed page that followed the OS would
   sit light inside a dark window (seen 2026-09-07 in the frame overlay). Same tokens, both ways. */
:root[data-theme="light"] { --bg:#f4f5f8; --panel:#ffffff; --field:#eef0f4; --ink:#14171c; --muted:#5b6472; --accent:#0f9d8a; --accent-ink:#ffffff;
          --accent-soft:rgba(15,157,138,.25); --border:#d9dde5; --ok:#15803d; --bad:#b91c1c; --mk-bg:#e9ecf1; --mk-border:#c9cfda; --shadow:rgba(0,0,0,.14); }
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system,'Inter',system-ui,Segoe UI,Roboto,sans-serif; display:flex; flex-direction:column; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:6px; }
button { font:inherit; }

header { display:flex; align-items:center; gap:12px; padding:14px 20px; border-bottom:1px solid var(--border); background:var(--panel); }
header h1 { margin:0; font-size:16px; font-weight:700; letter-spacing:-.1px; }
#stepOf { color:var(--muted); font-size:13px; }
#laterBtn { margin-left:auto; background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:6px 12px; cursor:pointer; }
#laterBtn:hover { color:var(--ink); border-color:var(--accent); }

main { flex:1 1 auto; overflow:auto; padding:22px 20px 26px; }
#card { max-width:640px; margin:0 auto; }
.dots { list-style:none; margin:0 0 18px; padding:0; display:flex; gap:10px; justify-content:center; }
.dot { width:12px; height:12px; border-radius:50%; border:2px solid var(--border); background:transparent; position:relative; }
.dot.current { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
.dot.done { background:var(--ok); border-color:var(--ok); }
.dot.skipped { border-style:dashed; border-color:var(--muted); }

h2 { margin:0 0 6px; font-size:28px; font-weight:700; letter-spacing:-.3px; line-height:1.2; }
.sentence { margin:0 0 18px; font-size:17px; color:var(--ink); }

/* ---- the animated example ---- */
.mock { position:relative; border:1px solid var(--border); border-radius:12px; background:var(--mk-bg); overflow:hidden; height:240px; user-select:none; -webkit-user-select:none; }
.mock-badge { position:absolute; top:8px; right:8px; z-index:5; font-size:10.5px; font-weight:700; letter-spacing:.4px; text-transform:uppercase;
              padding:2px 8px; border-radius:999px; background:var(--panel); color:var(--muted); border:1px solid var(--border); }
.mk-win { position:absolute; inset:14px; display:flex; border:1px solid var(--mk-border); border-radius:8px; overflow:hidden; background:var(--bg); box-shadow:0 8px 24px var(--shadow); }
.mk-side { flex:0 0 32%; background:var(--panel); border-right:1px solid var(--mk-border); padding:8px; font-size:10px; color:var(--muted); }
.mk-side-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
.mk-plus { border:1px solid var(--mk-border); border-radius:5px; padding:1px 5px; color:var(--ink); background:var(--bg); }
.mk-chat { height:10px; border-radius:4px; background:var(--field); margin:4px 0; }
.mk-proj { margin:6px 0; padding:3px 5px; border-radius:5px; color:var(--ink); background:var(--field); border-left:2px solid var(--accent); }
.mk-main { flex:1 1 auto; display:flex; flex-direction:column; min-width:0; }
.mk-head { padding:6px 10px; font-size:10px; font-weight:700; border-bottom:1px solid var(--mk-border); color:var(--ink); }
.mk-body { flex:1 1 auto; padding:10px; display:flex; flex-direction:column; gap:8px; justify-content:center; font-size:11px; color:var(--ink); }
.mk-composer { margin:8px; padding:6px 9px; border:1px solid var(--mk-border); border-radius:8px; color:var(--muted); font-size:10px; background:var(--panel); }
.mk-choices { display:flex; gap:8px; }
.mk-choice { flex:1 1 0; border:1px solid var(--mk-border); border-radius:8px; padding:8px; background:var(--panel); position:relative; }
.mk-choice b { display:block; font-size:11px; }
.mk-choice span { color:var(--muted); font-size:9.5px; }
.mk-choice.on { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-soft); }
.mk-tick { position:absolute; top:-7px; right:-7px; width:18px; height:18px; border-radius:50%; background:var(--ok); color:#fff; font-size:11px; line-height:18px; text-align:center; font-weight:700; }
.mk-row { display:flex; align-items:center; justify-content:space-between; gap:8px; border:1px solid var(--mk-border); border-radius:8px; padding:8px 10px; background:var(--panel); position:relative; }
.mk-row b { font-size:11px; }
.mk-select { position:relative; min-width:120px; border:1px solid var(--mk-border); border-radius:6px; padding:4px 8px; background:var(--bg); font-size:10px; }
.mk-select::after { content:"\25BE"; position:absolute; right:6px; top:3px; color:var(--muted); }
.mk-sel-a { opacity:0; position:absolute; left:8px; top:4px; }
.mk-sel-b { opacity:1; }
.mk-menu { position:absolute; right:10px; top:38px; width:150px; background:var(--panel); border:1px solid var(--mk-border); border-radius:8px; padding:4px; font-size:10px; z-index:4; opacity:0; visibility:hidden; box-shadow:0 6px 18px var(--shadow); }
.mk-menu div { padding:4px 6px; border-radius:5px; }
.mk-menu .pick { background:var(--accent-soft); }
.mk-saved { color:var(--ok); font-size:10px; font-weight:700; opacity:1; }
.mk-field { display:flex; align-items:center; gap:8px; }
.mk-input { flex:1 1 auto; border:1px solid var(--mk-border); border-radius:6px; padding:5px 8px; background:var(--bg); font-size:11px; min-height:24px; display:flex; align-items:center; }
.mk-typed { display:inline-block; overflow:hidden; white-space:nowrap; width:3ch; }
.mk-caret { display:inline-block; width:1px; height:12px; background:var(--ink); margin-left:1px; opacity:0; }
.mk-save { border-radius:6px; padding:5px 9px; background:var(--accent); color:var(--accent-ink); font-size:10px; font-weight:700; }
.mk-dialog { position:absolute; left:22%; top:18%; width:56%; background:var(--panel); border:1px solid var(--mk-border); border-radius:10px; padding:8px; font-size:10px; z-index:4; opacity:0; visibility:hidden; box-shadow:0 10px 30px var(--shadow); }
.mk-dialog .t { font-weight:700; margin-bottom:6px; color:var(--ink); }
.mk-dialog div.f { padding:4px 6px; border-radius:5px; color:var(--ink); }
.mk-dialog .f.pick { background:var(--accent-soft); }
.cursor { position:absolute; z-index:6; width:16px; height:20px; left:33%; top:52%; pointer-events:none; filter:drop-shadow(0 1px 1px rgba(0,0,0,.5)); }
.cursor svg { display:block; width:16px; height:20px; }
.cursor::after { content:""; position:absolute; left:-8px; top:-8px; width:28px; height:28px; border-radius:50%; border:2px solid var(--accent); opacity:0; }

/* Timelines: 6 s per loop including the hold on the final state; base styles ARE the final frame. */
.mock[data-step] .cursor, .mock[data-step] .cursor::after, .mock[data-step] .mk-choice.on, .mock[data-step] .mk-tick,
.mock[data-step] .mk-plus, .mock[data-step] .mk-dialog, .mock[data-step] .mk-proj, .mock[data-step] .mk-select,
.mock[data-step] .mk-menu, .mock[data-step] .mk-sel-a, .mock[data-step] .mk-sel-b, .mock[data-step] .mk-saved,
.mock[data-step] .mk-typed, .mock[data-step] .mk-caret, .mock[data-step] .mk-save { animation-duration:6s; animation-iteration-count:infinite; animation-timing-function:ease-in-out; }
.mock.paused * , .mock.paused .cursor::after { animation-play-state:paused !important; }

@keyframes click { 0%,27% { opacity:0; transform:scale(.4); } 30% { opacity:.8; transform:scale(1); } 36%,100% { opacity:0; transform:scale(1.5); } }
@keyframes click2 { 0%,55% { opacity:0; transform:scale(.4); } 58% { opacity:.8; transform:scale(1); } 64%,100% { opacity:0; transform:scale(1.5); } }
@keyframes click3 { 0%,66% { opacity:0; transform:scale(.4); } 69% { opacity:.8; transform:scale(1); } 75%,100% { opacity:0; transform:scale(1.5); } }

/* thinking */
@keyframes cur-thinking { 0% { left:82%; top:86%; } 25%,100% { left:33%; top:52%; } }
@keyframes pick-on { 0%,28% { border-color:var(--mk-border); box-shadow:none; } 33%,100% { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-soft); } }
@keyframes tick-in { 0%,36% { opacity:0; transform:scale(.4); } 44%,100% { opacity:1; transform:scale(1); } }
.mock[data-step="thinking"] .cursor { animation-name:cur-thinking; }
.mock[data-step="thinking"] .cursor::after { animation-name:click; }
.mock[data-step="thinking"] .mk-choice.on { animation-name:pick-on; }
.mock[data-step="thinking"] .mk-tick { animation-name:tick-in; }

/* folder */
@keyframes cur-folder { 0% { left:82%; top:86%; } 22%,34% { left:24%; top:11%; } 55%,100% { left:52%; top:50%; } }
@keyframes plus-hot { 0%,27% { border-color:var(--mk-border); background:var(--bg); } 30%,40% { border-color:var(--accent); background:var(--accent-soft); } 44%,100% { border-color:var(--mk-border); background:var(--bg); } }
@keyframes dialog-in { 0%,33% { opacity:0; visibility:hidden; } 37%,62% { opacity:1; visibility:visible; } 66%,100% { opacity:0; visibility:hidden; } }
@keyframes proj-in { 0%,66% { opacity:0; transform:translateY(4px); } 72%,100% { opacity:1; transform:none; } }
.mock[data-step="folder"] .cursor { animation-name:cur-folder; }
.mock[data-step="folder"] .cursor::after { animation-name:click, click2; animation-duration:6s, 6s; }
.mock[data-step="folder"] .mk-plus { animation-name:plus-hot; }
.mock[data-step="folder"] .mk-dialog { animation-name:dialog-in; }
.mock[data-step="folder"] .mk-proj { animation-name:proj-in; }

/* permissions */
@keyframes cur-perm { 0% { left:82%; top:86%; } 24%,34% { left:70%; top:38%; } 54%,100% { left:64%; top:66%; } }
@keyframes menu-in { 0%,31% { opacity:0; visibility:hidden; } 35%,60% { opacity:1; visibility:visible; } 64%,100% { opacity:0; visibility:hidden; } }
@keyframes sel-a { 0%,62% { opacity:1; } 66%,100% { opacity:0; } }
@keyframes sel-b { 0%,62% { opacity:0; } 66%,100% { opacity:1; } }
@keyframes saved-in { 0%,70% { opacity:0; } 76%,100% { opacity:1; } }
.mock[data-step="permissions"] .cursor { animation-name:cur-perm; }
.mock[data-step="permissions"] .cursor::after { animation-name:click, click2; animation-duration:6s, 6s; }
.mock[data-step="permissions"] .mk-menu { animation-name:menu-in; }
.mock[data-step="permissions"] .mk-sel-a { animation-name:sel-a; }
.mock[data-step="permissions"] .mk-sel-b { animation-name:sel-b; }
.mock[data-step="permissions"] .mk-saved { animation-name:saved-in; }

/* name */
@keyframes cur-name { 0% { left:82%; top:86%; } 22%,50% { left:40%; top:50%; } 64%,100% { left:76%; top:50%; } }
@keyframes typed { 0%,30% { width:0; } 50%,100% { width:3ch; } }
@keyframes caret { 0%,27% { opacity:0; } 28%,34% { opacity:1; } 35%,41% { opacity:0; } 42%,48% { opacity:1; } 49%,54% { opacity:0; } 55%,60% { opacity:1; } 61%,100% { opacity:0; } }
@keyframes save-hot { 0%,66% { filter:none; } 69%,74% { filter:brightness(1.25); } 78%,100% { filter:none; } }
.mock[data-step="name"] .cursor { animation-name:cur-name; }
.mock[data-step="name"] .cursor::after { animation-name:click, click3; animation-duration:6s, 6s; }
.mock[data-step="name"] .mk-typed { animation-name:typed; animation-timing-function:steps(3, end); }
.mock[data-step="name"] .mk-caret { animation-name:caret; animation-timing-function:step-end; }
.mock[data-step="name"] .mk-save { animation-name:save-hot; }
.mock[data-step="name"] .mk-saved { animation-name:saved-in; }

/* Reduced motion: the final frame, static, until the person asks for the example to play. */
@media (prefers-reduced-motion: reduce) {
  .mock:not(.motion-on) *, .mock:not(.motion-on) .cursor::after { animation:none !important; }
  .mock:not(.motion-on) .cursor { display:none; }
}

.example-bar { display:flex; align-items:center; gap:10px; margin:8px 0 18px; color:var(--muted); font-size:13px; }
.example-bar .caption { flex:1 1 auto; }
.mini { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:4px 10px; font-size:12px; cursor:pointer; }
.mini:hover { color:var(--ink); border-color:var(--accent); }

/* ---- the real control ---- */
.control { border:1px solid var(--border); border-radius:12px; background:var(--panel); padding:16px; display:flex; flex-direction:column; gap:12px; }
.choices { display:flex; gap:10px; flex-wrap:wrap; }
.choice { flex:1 1 200px; text-align:left; border:1px solid var(--border); border-radius:10px; padding:12px 14px; background:var(--bg); color:var(--ink); cursor:pointer; }
.choice:hover { border-color:var(--accent); }
.choice b { display:block; font-size:15px; margin-bottom:2px; }
.choice span { color:var(--muted); font-size:13px; }
.choice[aria-pressed="true"] { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent-soft); }
.inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.inp { background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:9px 11px; font:inherit; font-size:15px; min-width:220px; flex:1 1 220px; }
.inp:focus { outline:none; border-color:var(--accent); }
.btn { background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:9px 14px; font-weight:600; cursor:pointer; }
.btn:hover:not(:disabled) { border-color:var(--accent); }
.btn:disabled { opacity:.5; cursor:not-allowed; }
.btn.primary { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }
.hint { color:var(--muted); font-size:13px; margin:0; }
.status { min-height:22px; font-size:14px; }
.status.done { color:var(--ok); font-weight:600; }
.status.skipped { color:var(--muted); }
.status.failed { color:var(--bad); font-weight:600; }

footer { display:flex; align-items:center; gap:10px; padding:14px 20px; border-top:1px solid var(--border); background:var(--panel); }
footer .grow { flex:1 1 auto; }
#live { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
@media (max-width: 560px) { h2 { font-size:23px; } .mock { height:200px; } main { padding:16px 12px; } }
</style>
</head>
<body>
<header>
  <h1 id="pageTitle"></h1>
  <span id="stepOf" role="status" aria-live="polite"></span>
  <button id="laterBtn" type="button"></button>
</header>
<main id="pane" tabindex="-1">
  <div id="card">
    <ol class="dots" id="dots" aria-label="Setup progress" data-i18n-aria-label="setup.progress_aria"></ol>
    <h2 id="stepTitle"></h2>
    <p class="sentence" id="stepSentence"></p>
    <div class="mock" id="mock" aria-hidden="true"></div>
    <div class="example-bar">
      <span class="caption" id="caption"></span>
      <button class="mini" id="motionBtn" type="button" aria-pressed="false"></button>
    </div>
    <div class="control" id="control"></div>
    <div class="status" id="stepStatus" role="status" aria-live="polite"></div>
  </div>
</main>
<footer>
  <button class="btn" id="backBtn" type="button"></button>
  <span class="grow"></span>
  <button class="btn" id="skipBtn" type="button"></button>
  <button class="btn primary" id="nextBtn" type="button"></button>
</footer>
<div id="live" aria-live="polite" role="status"></div>
<script>
const STEPS = __SETUP_STEPS__;
const COPY = __SETUP_COPY__;
const CAPTIONS = __SETUP_CAPTIONS__;
const BUILD_COMMIT = "__PAGE_BUILD_COMMIT__";
</script>
"""

_SETUP_JS = r"""<script>
/* ------------------------------------------------------------------ state: read, never owned */
const view = { snap: null, index: 0, motion: 'auto' };
const $ = (sel, root) => (root || document).querySelector(sel);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const live = (msg) => { const l = $('#live'); if (l) l.textContent = msg; };
// Catalog lookup: the deterministic i18n bundle resolves the key when the UI locale
// carries it; the inline English is the honest fallback when no bundle shipped.
function T(key, fallback) {
  try {
    if (typeof VOOLT === 'function') { const t = VOOLT(key); if (t && t !== key) return t; }
  } catch (e) {}
  return fallback;
}
const framed = () => { try { return window.top !== window.self; } catch (e) { return true; } };
const reducedMotion = () => { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
const fmt = (s, vars) => String(s).replace(/\{(\w+)\}/g, (_, k) => (vars[k] == null ? '' : vars[k]));

async function getJSON(url) {
  const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j && (j.error || j.detail)) || ('HTTP ' + r.status));
  return j || {};
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => null);
  return { ok: r.ok && !(j && (j.error || j.ok === false)), status: r.status, json: j };
}

async function refresh() {
  try { view.snap = await getJSON('/api/setup/state'); }
  catch (e) { view.snap = null; }
  render();
  return view.snap;
}

function stepAt(i) { return view.snap && view.snap.steps ? view.snap.steps[i] : null; }
function current() { return stepAt(view.index) || { id: STEPS[view.index].id, title: STEPS[view.index].title, sentence: STEPS[view.index].sentence, later: STEPS[view.index].later, done: false, skipped: false }; }

/* ------------------------------------------------------------------ closing: never a trap */
function closeSetup() {
  if (framed()) {
    // Inside the chat's frame overlay: the host hides the frame; the chat and its draft are untouched.
    try { window.parent.postMessage({ type: 'vool-setup-close' }, window.location.origin); } catch (e) {}
    return;
  }
  // A native shell (the .app) owns this window: ask it to close, never navigate the page away.
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.close_setup) { window.pywebview.api.close_setup(); return; }
  } catch (e) {}
  try { window.close(); } catch (e) {}
  if (!window.closed) window.location.href = '/chat';
}
function openSettings(section) {
  const frag = section ? ('#' + section) : '';
  if (framed()) {
    try { window.parent.postMessage({ type: 'vool-setup-open-settings', section: section || '' }, window.location.origin); } catch (e) {}
    return;
  }
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_settings) { window.pywebview.api.open_settings(section || ''); return; }
  } catch (e) {}
  let opened = null;
  try { opened = window.open('/settings' + frag, 'vool-settings'); } catch (e) { opened = null; }
  if (opened) { try { opened.focus(); } catch (e) {} return; }
  window.location.href = '/settings' + frag;
}

/* ------------------------------------------------------------------ the mock (aria-hidden; caption is the text) */
const CURSOR_SVG = '<svg viewBox="0 0 16 20" xmlns="http://www.w3.org/2000/svg"><path d="M1 1 L1 15 L4.6 11.6 L7.4 18 L9.8 17 L7 10.8 L12 10.8 Z" fill="#fff" stroke="#111" stroke-width="1.2" stroke-linejoin="round"/></svg>';
function mockFor(id) {
  const win = '<div class="mk-win"><div class="mk-side"><div class="mk-side-head"><span>Chats</span><span class="mk-plus">+ Project</span></div>'
    + (id === 'folder' ? '<div class="mk-proj">&#128193; Documents</div>' : '')
    + '<div class="mk-chat"></div><div class="mk-chat"></div></div>'
    + '<div class="mk-main"><div class="mk-head">VOOL</div><div class="mk-body">' + mockBody(id) + '</div><div class="mk-composer">Message VOOL…</div></div></div>';
  const extra = id === 'folder'
    ? '<div class="mk-dialog"><div class="t">Choose a folder</div><div class="f">&#128193; Desktop</div><div class="f pick">&#128193; Documents</div><div class="f">&#128193; Pictures</div></div>'
    : '';
  return '<span class="mock-badge">' + COPY.example + '</span>' + win + extra + '<div class="cursor">' + CURSOR_SVG + '</div>';
}
function mockBody(id) {
  if (id === 'thinking') return '<div class="mk-choices"><div class="mk-choice on"><b>On my Mac</b><span>Private. Free.</span><span class="mk-tick">&#10003;</span></div><div class="mk-choice"><b>Online</b><span>Smarter. Needs a key.</span></div></div>';
  if (id === 'permissions') return '<div class="mk-row"><b>What may it do alone?</b><span class="mk-select"><span class="mk-sel-a">Just do routine work</span><span class="mk-sel-b">Ask me first</span></span><div class="mk-menu"><div>Just do routine work</div><div>Ask before risky changes</div><div class="pick">Ask me first</div></div></div><span class="mk-saved">Saved &#10003;</span>';
  if (id === 'name') return '<div class="mk-field"><span class="mk-input"><span class="mk-typed">Sam</span><span class="mk-caret"></span></span><span class="mk-save">Save</span></div><span class="mk-saved">Saved &#10003;</span>';
  return '<div class="mk-row"><b>Pick a folder</b></div>';
}
function renderMock(id) {
  const mock = $('#mock');
  mock.className = 'mock';
  mock.dataset.step = id;
  mock.innerHTML = mockFor(id);
  applyMotion();
}
function applyMotion() {
  const mock = $('#mock'), btn = $('#motionBtn');
  const reduced = reducedMotion();
  mock.classList.remove('motion-on', 'paused');
  if (reduced) {
    // Static final frame by default; playing is the person's explicit choice.
    if (view.motion === 'on') mock.classList.add('motion-on');
    btn.textContent = view.motion === 'on' ? COPY.pause : COPY.playExample;
    btn.setAttribute('aria-pressed', view.motion === 'on' ? 'true' : 'false');
    return;
  }
  if (view.motion === 'off') mock.classList.add('paused');   // Pause freezes the frame; Play resumes
  btn.textContent = view.motion === 'off' ? COPY.play : COPY.pause;
  btn.setAttribute('aria-pressed', view.motion === 'off' ? 'true' : 'false');
}
function toggleMotion() {
  if (reducedMotion()) view.motion = view.motion === 'on' ? 'auto' : 'on';
  else view.motion = view.motion === 'off' ? 'auto' : 'off';
  applyMotion();
}

/* ------------------------------------------------------------------ render */
function render() {
  const total = STEPS.length;
  const step = current();
  $('#pageTitle').textContent = COPY.title;
  $('#laterBtn').textContent = COPY.later;
  $('#backBtn').textContent = COPY.back;
  $('#skipBtn').textContent = COPY.skip;
  $('#nextBtn').textContent = view.index >= total - 1 ? COPY.finish : COPY.next;
  $('#stepOf').textContent = fmt(COPY.stepOf, { n: view.index + 1, total: total });
  const dots = $('#dots');
  dots.textContent = '';
  STEPS.forEach((spec, i) => {
    const s = stepAt(i) || {};
    const li = el('li', 'dot' + (i === view.index ? ' current' : '') + (s.done ? ' done' : (s.skipped ? ' skipped' : '')));
    li.dataset.step = spec.id;
    li.setAttribute('aria-label', fmt(COPY.stepOf, { n: i + 1, total: total }) + ': ' + spec.title + (s.done ? ' (done)' : (s.skipped ? ' (skipped)' : '')));
    dots.appendChild(li);
  });
  $('#stepTitle').textContent = step.title;
  $('#stepSentence').textContent = step.sentence;
  $('#caption').textContent = CAPTIONS[step.id] || '';
  renderMock(step.id);
  renderControl(step);
  const st = $('#stepStatus');
  st.className = 'status' + (step.done ? ' done' : (step.skipped ? ' skipped' : ''));
  st.textContent = step.done ? fmt(COPY.done, { later: step.later }) : (step.skipped ? fmt(COPY.skipped, { later: step.later }) : '');
  $('#backBtn').disabled = view.index === 0;
  $('#skipBtn').hidden = !!step.done;
  document.body.dataset.step = step.id;
}

/* ------------------------------------------------------------------ the real controls */
function renderControl(step) {
  const host = $('#control');
  host.textContent = '';
  if (step.id === 'thinking') return controlThinking(host, step);
  if (step.id === 'folder') return controlFolder(host, step);
  if (step.id === 'permissions') return controlPermissions(host, step);
  if (step.id === 'name') return controlName(host, step);
}
function fail(host, msg) { const n = el('p', 'status failed', msg); n.dataset.fail = '1'; host.appendChild(n); live(msg); }

function controlThinking(host, step) {
  const row = el('div', 'choices');
  const local = el('button', 'choice'); local.type = 'button'; local.id = 'chooseLocal';
  local.appendChild(el('b', null, T('setup.thinking.local_title', 'On my Mac'))); local.appendChild(el('span', null, T('setup.thinking.local_sub', 'Private and free. Nothing you type leaves this computer.')));
  const online = el('button', 'choice'); online.type = 'button'; online.id = 'chooseOnline';
  online.appendChild(el('b', null, T('setup.thinking.online_title', 'Online, with a key'))); online.appendChild(el('span', null, T('setup.thinking.online_sub', 'Smarter answers. You paste a key from a company you pay; it is kept only on this Mac.')));
  local.setAttribute('aria-pressed', step.done ? 'true' : 'false');
  local.addEventListener('click', async () => {
    local.disabled = true;
    const res = await postJSON('/api/onboarding/choice', { choice: 'local_only' });
    local.disabled = false;
    // 409 invalid_transition: a choice already stands. The authority's truth is what refresh shows.
    if (!res.ok && res.status !== 409) { fail(host, fmt(T('setup.status.not_saved', 'Not saved — {reason}'), { reason: ((res.json && (res.json.error || res.json.detail)) || ('HTTP ' + res.status)) })); return; }
    await refresh();
    live(current().done ? T('setup.status.saved_thinking', 'Saved: VOOL thinks on this Mac.') : T('setup.status.not_confirmed', 'Not confirmed yet.'));
  });
  online.addEventListener('click', () => openSettings('keys'));
  row.appendChild(local); row.appendChild(online);
  host.appendChild(row);
  host.appendChild(el('p', 'hint', T('setup.thinking.hint', 'You can switch between the two whenever you like.')));
}

function controlFolder(host, step) {
  const row = el('div', 'inline');
  const path = document.createElement('input');
  path.className = 'inp'; path.id = 'folderPath'; path.type = 'text'; path.placeholder = T('setup.folder.placeholder', 'Type the folder’s full path, e.g. /Users/you/Documents');
  path.setAttribute('aria-label', T('setup.folder.aria', 'Folder path')); path.autocomplete = 'off'; path.spellcheck = false;
  const choose = el('button', 'btn', T('setup.folder.choose', 'Choose a folder…')); choose.type = 'button'; choose.id = 'chooseFolder';
  const use = el('button', 'btn primary', T('setup.folder.use', 'Use this folder')); use.type = 'button'; use.id = 'useFolder';
  const bind = async (root) => {
    const clean = String(root || '').trim();
    if (!clean) { fail(host, T('setup.folder.err_empty', 'Type or choose a folder first.')); return; }
    const name = clean.split('/').filter(Boolean).pop() || 'Project';
    use.disabled = true;
    const res = await postJSON('/api/projects', { name: name, root: clean });
    use.disabled = false;
    if (!res.ok) { fail(host, fmt(T('setup.status.not_saved', 'Not saved — {reason}'), { reason: ((res.json && (res.json.error || res.json.detail)) || ('HTTP ' + res.status)) })); return; }
    await refresh();
    live(current().done ? fmt(T('setup.status.saved_folder', 'Saved: VOOL works inside {name}.'), { name: name }) : T('setup.status.not_confirmed', 'Not confirmed yet.'));
  };
  choose.addEventListener('click', async () => {
    const picked = await pickFolder();
    if (picked) { path.value = picked; await bind(picked); }
    else { path.focus(); live(T('setup.folder.none_chosen', 'No folder was chosen. You can type its path instead.')); }
  });
  use.addEventListener('click', () => bind(path.value));
  path.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); bind(path.value); } });
  row.appendChild(choose); row.appendChild(path); row.appendChild(use);
  host.appendChild(row);
  host.appendChild(el('p', 'hint', T('setup.folder.hint', 'Pick a normal folder such as Documents. Not your whole disk.')));
}
function pickFolder() {
  // Own native window: the bridge is right here. Framed inside the chat: ask the host, which has it.
  return new Promise((resolve) => {
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
        window.pywebview.api.pick_folder().then((res) => resolve(res && res.ok && res.path ? res.path : null)).catch(() => resolve(null));
        return;
      }
    } catch (e) {}
    if (!framed()) { resolve(null); return; }
    const timer = setTimeout(() => { window.removeEventListener('message', onMsg); resolve(null); }, 120000);
    function onMsg(e) {
      if (e.origin !== window.location.origin) return;
      const d = e.data;
      if (d && typeof d === 'object' && d.type === 'vool-setup-folder') {
        clearTimeout(timer); window.removeEventListener('message', onMsg);
        resolve(d.path ? String(d.path) : null);
      }
    }
    window.addEventListener('message', onMsg);
    try { window.parent.postMessage({ type: 'vool-setup-pick-folder' }, window.location.origin); } catch (e) { resolve(null); }
  });
}

const AUTONOMY = [
  { value: 'strict', title: T('setup.autonomy.strict_title', 'Ask me first'), sub: T('setup.autonomy.strict_sub', 'It stops and asks before every action. Slowest, safest.') },
  { value: 'balanced', title: T('setup.autonomy.balanced_title', 'Ask before risky changes'), sub: T('setup.autonomy.balanced_sub', 'Routine work runs; anything that deletes or changes a lot asks first.') },
  { value: 'hands_off', title: T('setup.autonomy.hands_off_title', 'Just do routine work'), sub: T('setup.autonomy.hands_off_sub', 'Fewest questions. Sending, posting and spending still always ask.') },
];
function controlPermissions(host, step) {
  const row = el('div', 'choices');
  const currentValue = step.done && view.snap && view.snap.autonomy_mode ? view.snap.autonomy_mode : '';
  AUTONOMY.forEach((opt) => {
    const b = el('button', 'choice'); b.type = 'button'; b.dataset.autonomy = opt.value;
    b.appendChild(el('b', null, opt.title)); b.appendChild(el('span', null, opt.sub));
    b.setAttribute('aria-pressed', currentValue === opt.value ? 'true' : 'false');
    b.addEventListener('click', async () => {
      b.disabled = true;
      const res = await postJSON('/api/settings/prefs', { autonomy_mode: opt.value });
      b.disabled = false;
      if (!res.ok) { fail(host, fmt(T('setup.status.not_saved', 'Not saved — {reason}'), { reason: ((res.json && (res.json.error || res.json.detail)) || ('HTTP ' + res.status)) })); return; }
      await refresh();
      live(current().done ? opt.title + '.' : T('setup.status.not_confirmed', 'Not confirmed yet.'));
    });
    row.appendChild(b);
  });
  host.appendChild(row);
  host.appendChild(el('p', 'hint', T('setup.autonomy.hint', 'Whatever you pick, sending a message, posting or spending money always asks you first.')));
}

function controlName(host, step) {
  const row = el('div', 'inline');
  const input = document.createElement('input');
  input.className = 'inp'; input.id = 'nameInput'; input.type = 'text'; input.maxLength = 60; input.placeholder = T('setup.name.placeholder', 'e.g. Sam');
  input.setAttribute('aria-label', T('setup.name.aria', 'What VOOL should call you')); input.autocomplete = 'off';
  const save = el('button', 'btn primary', T('setup.name.save', 'Save')); save.type = 'button'; save.id = 'saveName';
  const submit = async () => {
    const value = input.value.trim();
    if (!value) { fail(host, T('setup.name.err_empty', 'Type a name first, or skip this step.')); return; }
    save.disabled = true;
    const res = await postJSON('/api/profile/remember', { category: 'preferred_name', value: value, scope: 'global', replace: true });
    save.disabled = false;
    if (!res.ok) { fail(host, fmt(T('setup.status.not_saved', 'Not saved — {reason}'), { reason: ((res.json && (res.json.error || (res.json.change && res.json.change.report) || res.json.detail)) || ('HTTP ' + res.status)) })); return; }
    await refresh();
    live(current().done ? fmt(T('setup.status.saved_name', 'Saved. VOOL will call you {name}.'), { name: value }) : T('setup.status.not_confirmed', 'Not confirmed yet.'));
  };
  save.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  row.appendChild(input); row.appendChild(save);
  host.appendChild(row);
  host.appendChild(el('p', 'hint', T('setup.name.hint', 'Kept as one remembered item you can edit or forget in Settings.')));
}

/* ------------------------------------------------------------------ navigation */
function go(i) {
  view.index = Math.max(0, Math.min(STEPS.length - 1, i));
  try { history.replaceState(null, '', '#step=' + STEPS[view.index].id); } catch (e) {}
  render();
  $('#pane').scrollTop = 0;
  live(fmt(COPY.stepOf, { n: view.index + 1, total: STEPS.length }) + ': ' + current().title);
}
async function next() {
  if (view.index >= STEPS.length - 1) { live(COPY.allDone); closeSetup(); return; }
  go(view.index + 1);
}
async function skipCurrent() {
  const id = current().id;
  const have = (view.snap && view.snap.skipped) || [];
  const skipped = STEPS.map(s => s.id).filter(sid => sid === id || have.indexOf(sid) >= 0);
  const res = await postJSON('/api/settings/prefs', { setup_skipped_steps: skipped.join(',') });
  if (!res.ok) { fail($('#control'), fmt(T('setup.status.skip_failed', 'Could not record the skip — {reason}'), { reason: ((res.json && res.json.error) || ('HTTP ' + res.status)) })); return; }
  await refresh();
  next();
}
function indexFromHash() {
  const m = /step=([a-z_]+)/.exec(String(location.hash || ''));
  if (!m) return -1;
  return STEPS.findIndex(s => s.id === m[1]);
}

async function boot() {
  try { if (/[?&]theme=light\b/.test(String(location.search || ''))) document.documentElement.dataset.theme = 'light'; } catch (e) {}
  $('#laterBtn').addEventListener('click', closeSetup);
  $('#backBtn').addEventListener('click', () => go(view.index - 1));
  $('#nextBtn').addEventListener('click', next);
  $('#skipBtn').addEventListener('click', skipCurrent);
  $('#motionBtn').addEventListener('click', toggleMotion);
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
    if (e.key === 'Escape' && !typing) closeSetup();
  });
  window.addEventListener('focus', () => { refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('hashchange', () => { const i = indexFromHash(); if (i >= 0) go(i); });
  render();                                  // paint the shell first; nothing here blocks
  const snap = await refresh();
  const wanted = indexFromHash();
  if (wanted >= 0) view.index = wanted;
  else if (snap && snap.first_undone) view.index = Math.max(0, STEPS.findIndex(s => s.id === snap.first_undone));
  render();
}
boot();

/* Test surface: the same shape the other pages expose, so a drive can act without pixel hunting. */
window.__voolSetup = {
  state: () => JSON.parse(JSON.stringify(view.snap)),
  index: () => view.index,
  stepId: () => current().id,
  go: (i) => go(i),
  refresh: () => refresh(),
  motion: () => view.motion,
};
</script>
</body>
</html>
"""


def setup_steps_model(ui_locale: str = "en") -> list[dict]:
    """The step model, resolved through the deterministic catalog for one UI locale.

    The StepSpec English stays the authority; the catalog key ``setup.step.<id>.<field>``
    carries the translation, and a missing key falls back to the authority's own English
    (the engine's visible-fallback law). Placeholder fields (``later`` names Settings
    locations) translate as whole strings — no prose parsing anywhere.
    """
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(ui_locale)
    return [
        {
            "id": s.id,
            "title": catalog.text(f"setup.step.{s.id}.title") if s.title else "",
            "sentence": catalog.text(f"setup.step.{s.id}.sentence") if s.sentence else "",
            "later": catalog.text(f"setup.step.{s.id}.later") if s.later else "",
        }
        for s in STEPS
    ]


_COPY_KEY_MAP = {
    "title": "setup.copy.title",
    "lead": "setup.copy.lead",
    "later": "setup.copy.later",
    "back": "setup.copy.back",
    "next": "setup.copy.next",
    "finish": "setup.copy.finish",
    "skip": "setup.copy.skip",
    "example": "setup.copy.example",
    "pause": "setup.copy.pause",
    "play": "setup.copy.play",
    "playExample": "setup.copy.play_example",
    "stepOf": "setup.copy.step_of",
    "done": "setup.copy.done",
    "skipped": "setup.copy.skipped",
    "allDone": "setup.copy.all_done",
}


def _localized_copy(ui_locale: str) -> dict:
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(ui_locale)
    return {name: catalog.text(key) for name, key in _COPY_KEY_MAP.items()}


def _localized_captions(ui_locale: str) -> dict:
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(ui_locale)
    return {step_id: catalog.text(f"setup.caption.{step_id}") for step_id in CAPTIONS}


def render_vool_setup_html(*, build_commit: str = "", ui_locale: str = "en") -> str:
    """The guided setup page: the step model from core.setup_progress, rendered by one script.

    ``ui_locale`` resolves exactly like /chat and /settings (``?ui_locale=`` > the
    ``vool_ui_locale`` cookie > English): the page ships the one deterministic i18n
    bootstrap (which defines VOOLT for the T() lookups above) and renders ``<html lang
    dir>`` for the resolved locale. English stays the fallback for every key a locale
    does not carry.
    """
    from core.i18n.catalog import catalog_for
    from core.i18n.locales import get_locale
    from core.i18n.page_bundle import render_i18n_bootstrap

    tag = catalog_for(ui_locale).locale
    direction = (get_locale(tag) or get_locale("en")).direction
    page = _SETUP_HTML + _SETUP_JS
    return (
        page.replace("__SETUP_STEPS__", json.dumps(setup_steps_model(ui_locale), separators=(",", ":"), ensure_ascii=False))
        .replace("__SETUP_COPY__", json.dumps(_localized_copy(ui_locale), separators=(",", ":"), ensure_ascii=False))
        .replace("__SETUP_CAPTIONS__", json.dumps(_localized_captions(ui_locale), separators=(",", ":"), ensure_ascii=False))
        .replace("__PAGE_BUILD_COMMIT__", str(build_commit or "").strip())
        # The bootstrap defines VOOLT before the page's own script runs (setup has no
        # document-order law; a plain head-adjacent injection is the documented form).
        .replace("<script>\nconst STEPS", render_i18n_bootstrap(tag) + "\n<script>\nconst STEPS", 1)
        .replace('<html lang="en">', f'<html lang="{tag}" dir="{direction}">')
    )
