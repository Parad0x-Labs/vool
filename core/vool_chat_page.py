"""VOOL's own chat surface, served by the always-on API server (port 11435).

This is the first piece of the self-contained product (Phase B, option b): a chat UI VOOL
serves itself, so the desktop bundle no longer depends on the external OpenClaw Node app for
its main surface. The page is self-contained (inline CSS/JS, no CDN, no build step) so it works
inside a packaged installer with no network. It talks only to same-origin endpoints that already
exist: POST /api/chat (streaming NDJSON), GET /api/runtime/version, and — for the sessions
sidebar — GET /api/chat/sessions (thread list), GET /api/chat/history (a thread's transcript),
and POST /api/chat/session (rename / archive a thread). Message text is selectable and each
message has a Copy button.
"""
from __future__ import annotations

_VOOL_CHAT_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL</title>
<link rel="icon" type="image/png" href="__VOOL_LOGO_URI__"/>
<style>
:root { --bg:#101216; --panel:#16191f; --panel-solid:#16191f; --chat:#101216; --field:#1d2129; --ink:#e8eaf0; --text:#e8eaf0; --muted:#9aa1af; --accent:#5eead4; --accent2:#34d399; --grad:linear-gradient(135deg,#5eead4,#34d399); --user:#233043; --border:#262b35; --active:#1d2129; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,'Inter',system-ui,Segoe UI,Roboto,sans-serif; height:100vh; display:flex; flex-direction:row; overflow:hidden; }
#sidebar { flex:0 0 240px; width:240px; background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; min-height:0; }
#sidebar .side-head { display:flex; align-items:center; gap:8px; padding:12px 12px 8px; }
#sidebar .side-title { font-size:12px; font-weight:700; letter-spacing:.6px; color:var(--muted); text-transform:uppercase; flex:1; }
#newChat { background:transparent; color:var(--accent); border:1px solid var(--border); border-radius:8px; padding:6px 10px; font:inherit; font-size:13px; font-weight:600; cursor:pointer; }
#sidebar #sessions { flex:1 1 auto; overflow-y:auto; min-height:0; }
#newProject { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:6px 8px; font:inherit; font-size:12px; font-weight:600; cursor:pointer; }
#newProject:hover { color:var(--ink); border-color:var(--accent); }
.proj-head { display:flex; align-items:center; gap:6px; padding:6px 10px 6px 8px; cursor:pointer; color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.3px; }
.proj-head:hover { color:var(--ink); }
.proj-head:hover .proj-actions { opacity:1; }
.proj-head.active { background:var(--active); color:var(--ink); border-radius:8px; box-shadow:inset 2px 0 0 var(--accent); }
.proj-head.active .proj-name { color:var(--ink); }
.proj-head.active .proj-caret { color:var(--accent); }
.proj-caret { width:12px; flex:0 0 auto; font-size:10px; }
.proj-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.proj-count { flex:0 0 auto; color:var(--muted); font-weight:600; font-size:11px; }
.proj-actions { display:flex; gap:2px; opacity:0; transition:opacity .12s; }
.proj-head.general { cursor:pointer; }
.proj-menu { position:fixed; z-index:60; min-width:160px; max-width:220px; background:var(--panel); border:1px solid var(--border); border-radius:10px; box-shadow:0 8px 26px rgba(0,0,0,.5); padding:4px; }
.proj-menu-item { display:block; width:100%; text-align:left; background:transparent; color:var(--ink); border:none; border-radius:6px; padding:6px 9px; font:inherit; font-size:12.5px; cursor:pointer; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.proj-menu-item:hover { background:var(--active); }
.proj-menu-item.on { color:var(--accent); }
.proj-menu-item.new { color:var(--accent); border-top:1px solid var(--border); margin-top:2px; }
.side-foot { padding:8px 12px 12px; border-top:1px solid var(--border); }
.side-foot-btn { width:100%; text-align:left; background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:8px 10px; font:inherit; font-size:13px; font-weight:600; cursor:pointer; }
.side-foot-btn:hover { border-color:var(--accent); }
.home-menu { position:relative; }
.home-menu > summary { display:flex; align-items:center; gap:9px; list-style:none; padding:10px 12px; border:1px solid var(--border); border-radius:10px; cursor:pointer; font-weight:600; user-select:none; }
.home-menu > summary::-webkit-details-marker { display:none; }
.home-menu > summary:hover, .home-menu[open] > summary { background:var(--active); border-color:var(--accent); }
.home-menu > summary:focus-visible, .home-options button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.home-caret { margin-left:auto; color:var(--muted); }
.home-menu[open] .home-caret { transform:rotate(180deg); }
.home-options { position:absolute; bottom:calc(100% + 8px); left:0; right:0; z-index:60; padding:6px; border:1px solid var(--border); border-radius:12px; background:var(--panel-solid); box-shadow:0 8px 30px #0005; max-height:calc(100dvh - 100px); overflow-y:auto; }
.home-options .side-foot-btn { display:block; border-color:transparent; padding:9px 10px; }
.home-options .side-foot-btn:hover { background:var(--active); }
.home-support { margin-top:5px; padding-top:5px; border-top:1px solid var(--border); }
.home-support .side-foot-btn { font-size:12px; font-weight:500; color:var(--muted); }

.modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.72); display:flex; align-items:center; justify-content:center; z-index:50; }
.modal-overlay[hidden] { display:none; }
.modal { width:min(520px,92vw); max-height:86vh; overflow:auto; background:var(--panel-solid); border:1px solid var(--border); border-radius:14px; box-shadow:0 12px 40px rgba(0,0,0,.5); }
.modal-head { display:flex; align-items:center; justify-content:space-between; padding:14px 16px; border-bottom:1px solid var(--border); font-weight:700; }
.modal-x { background:transparent; border:none; color:var(--muted); font-size:22px; line-height:1; cursor:pointer; }
.modal-x:hover { color:var(--ink); }
.modal-body { padding:16px; }
.text-prompt.modal { padding:0; color:var(--ink); }
.text-prompt::backdrop { background:rgba(0,0,0,.72); }
.text-prompt label { display:block; margin-bottom:10px; font-weight:600; }
.text-prompt input { box-sizing:border-box; width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:10px; font:inherit; }
.text-prompt input:focus { outline:2px solid var(--accent); outline-offset:2px; }
.plugin-card { border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:12px; background:var(--bg); }
.plugin-card .p-head { display:flex; align-items:baseline; gap:8px; }
.plugin-card .p-name { font-weight:700; color:var(--ink); }
.plugin-card .p-ver { font-size:11px; color:var(--muted); }
.plugin-card .p-cat { margin-left:auto; font-size:10px; text-transform:uppercase; letter-spacing:.4px; color:var(--or-c); border:1px solid var(--or-c); border-radius:999px; padding:1px 7px; }
.plugin-card .p-desc { font-size:12px; color:var(--muted); margin:4px 0 8px; }
.plugin-card .p-skill { display:flex; gap:8px; font-size:12px; padding:5px 0; border-top:1px dashed var(--border); }
.plugin-card .p-skill b { color:var(--accent); flex:0 0 auto; }
.plugin-card .p-skill span { color:var(--muted); }
.plugins-empty { color:var(--muted); font-size:13px; }
.modal.modal-lg { width:min(820px,94vw); height:min(615px,86vh); max-height:86vh; display:flex; flex-direction:column; }
.modal.settings-frame-modal { padding:0; overflow:hidden; }
#settingsFrame { flex:1 1 auto; width:100%; min-height:0; border:0; background:var(--panel-solid); }
.modal.modal-lg .modal-body { flex:1 1 auto; overflow:auto; min-height:0; }
.plugins-search { width:100%; box-sizing:border-box; margin:2px 0 14px; padding:10px 13px; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:10px; font:inherit; font-size:14px; }
.plugins-search:focus { outline:none; border-color:var(--accent); }
.files-controls { display:flex; gap:8px; align-items:center; margin:2px 0 14px; }
.files-controls .plugins-search { margin:0; flex:1 1 auto; }
.files-sort { flex:0 0 auto; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:10px; font:inherit; font-size:13px; padding:9px 10px; cursor:pointer; }
.files-sort:focus { outline:none; border-color:var(--accent); }
.file-row { display:flex; align-items:center; gap:10px; padding:9px 11px; border:1px solid var(--border); border-radius:10px; margin-bottom:8px; background:var(--bg); cursor:pointer; }
.file-row:hover { border-color:var(--accent); }
.file-ic { flex:0 0 auto; font-size:16px; line-height:1; }
.file-col { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; gap:1px; }
.file-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }
.file-cap { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:11px; }
.file-meta { flex:0 0 auto; color:var(--muted); font-size:12px; align-self:center; }
.files-group { margin:14px 0 6px; color:var(--muted); font-size:12px; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
img.chat-img { display:block; max-width:min(420px,100%); max-height:420px; width:auto; height:auto; margin:8px 0 4px; border-radius:12px; border:1px solid var(--border); cursor:pointer; background:var(--bg); }
img.chat-img:hover { border-color:var(--accent); }
/* Markdown blocks. Before these existed the renderer emitted none of them: every heading, list,
   table and code fence in an answer reached the operator as literal ## ** - and | characters. */
.chat-p { margin:0 0 10px; }
.chat-p:last-child { margin-bottom:0; }
/* The turn's provenance line. One line, always present, never allowed to widen the bubble: on a
   narrow pane it ellipsizes and keeps the full text on its title attribute rather than reflowing. */
.chat-provenance { margin:10px 0 0; opacity:0.72; font-size:0.92em; max-width:100%; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.chat-provenance code { border:0; background:none; padding:0; }
/* The Proof Chip: one served turn's evidence, projected read-only by the server and rendered
   beside the answer. Collapsed it is one quiet line (actions · sources · cost · STATE); opened it
   shows the expanded account. The state word is the SERVER's verdict, rendered verbatim — this
   page never derives an evidence state of its own, so nothing here can upgrade RECORDED into
   something it is not. */
.proof-chip { margin:10px 0 0; max-width:100%; font-size:12px; }
.proof-chip-head { display:inline-flex; align-items:center; gap:7px; max-width:100%; background:var(--bg); color:var(--muted);
  border:1px solid var(--border); border-radius:8px; padding:4px 10px; font:inherit; font-size:11.5px; cursor:pointer; }
.proof-chip-head:hover { color:var(--ink); border-color:var(--accent); }
.proof-chip .pc-state { font-weight:700; letter-spacing:.4px; }
.proof-chip .pc-state-verified { color:var(--accent2); }
.proof-chip .pc-state-recorded { color:var(--muted); }
.proof-chip .pc-state-incomplete { color:#fbbf24; }
.proof-chip .pc-state-unverified { color:#f87171; }
.proof-chip .pc-coverage { color:var(--muted); }
.proof-chip-body { margin-top:6px; border:1px solid var(--border); border-radius:8px; background:var(--bg);
  padding:8px 11px; color:var(--muted); font-size:11.5px; line-height:1.55; overflow-wrap:anywhere; }
.proof-chip-body b { color:var(--ink); }
.proof-chip-body .pc-row { margin:1px 0; }
.proof-chip-body .pc-head { color:var(--ink); font-weight:600; margin-top:4px; }
.proof-chip-body .pc-head:first-child { margin-top:0; }
.proof-chip-body code { border:0; background:none; padding:0; font-size:10.5px; color:var(--muted); }
.proof-chip-body .pc-none { font-style:italic; }
.chat-h { margin:16px 0 8px; line-height:1.3; font-weight:600; }
.chat-h:first-child { margin-top:0; }
h1.chat-h { font-size:1.35em; } h2.chat-h { font-size:1.2em; } h3.chat-h { font-size:1.08em; }
h4.chat-h, h5.chat-h, h6.chat-h { font-size:1em; color:var(--muted); }
.chat-hr { border:0; border-top:1px solid var(--border); margin:14px 0; }
.chat-list { margin:0 0 10px; padding-left:22px; }
.chat-list li { margin:3px 0; }
.chat-quote { margin:0 0 10px; padding:2px 0 2px 12px; border-left:3px solid var(--border); color:var(--muted); }
.chat-code { margin:0 0 10px; padding:10px 12px; background:var(--bg); border:1px solid var(--border);
             border-radius:10px; overflow-x:auto; font-size:0.9em; line-height:1.45; }
.chat-code code { background:none; border:0; padding:0; white-space:pre; }
code { background:var(--bg); border:1px solid var(--border); border-radius:5px; padding:1px 5px; font-size:0.9em; }
/* A wide table scrolls inside its own box; the message column must never scroll sideways. */
.chat-table-wrap { overflow-x:auto; margin:4px 0 20px; border:1px solid var(--border); border-radius:10px; }
.chat-table { width:100%; border-collapse:collapse; font-size:inherit; font-variant-numeric:tabular-nums; }
.chat-table th, .chat-table td { border:0; border-bottom:1px solid var(--border); padding:10px 14px; text-align:left; vertical-align:top; }
.chat-table tbody tr:last-child td { border-bottom:0; }
.chat-table th { background:var(--panel); color:var(--muted); font-size:0.9em; font-weight:600; }
/* Screen-reader-only: present to assistive technology, removed from sight. */
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.n-toast { position:fixed; left:50%; bottom:96px; transform:translateX(-50%) translateY(8px); max-width:520px; background:var(--bg2,#1b1b1f); color:var(--ink); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:13px; box-shadow:0 6px 24px rgba(0,0,0,.35); opacity:0; pointer-events:none; transition:opacity .2s, transform .2s; z-index:60; }
.n-toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
.keys-list { margin-top:10px; }
.keys-title { color:var(--muted); font-size:12px; font-weight:700; margin:4px 0 6px; }
.key-row { display:flex; align-items:center; gap:8px; padding:6px 0; }
.key-dot { width:9px; height:9px; border-radius:50%; background:var(--muted); flex:0 0 auto; }
.key-dot.ok { background:var(--ok); box-shadow:0 0 6px var(--ok); }
.key-dot.bad { background:var(--bad); box-shadow:0 0 6px var(--bad); }
.key-label { flex:1 1 auto; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.key-test, .key-diag, .key-copy { flex:0 0 auto; padding:3px 10px; font-size:12px; }
.key-note { font-size:12px; color:var(--muted); background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:8px 10px; margin:2px 0 8px; display:flex; align-items:flex-start; gap:8px; }
.key-note span { flex:1 1 auto; }
.p-firstparty { display:inline-flex; align-items:center; gap:4px; margin-left:6px; font-size:10px; font-weight:800; letter-spacing:.4px; color:#f0b429; border:1px solid #f0b429; border-radius:999px; padding:1px 8px 1px 6px; text-transform:none; }
.p-firstparty .fp-mark { width:8px; height:8px; background:#f0b429; transform:rotate(45deg); border-radius:1px; }
.plugin-card.firstparty { border-color:#f0b42955; }
.p-actions { display:flex; align-items:center; gap:8px; margin-top:10px; padding-top:8px; border-top:1px solid var(--border); }
.p-status { font-size:11px; color:var(--ok); }
.p-status.off { color:var(--muted); }
.p-toggle { margin-left:auto; background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:4px 12px; font:inherit; font-size:12px; cursor:pointer; }
.p-toggle:hover { border-color:var(--accent); }
.p-toggle.enable { color:var(--accent); border-color:var(--accent); }
.set-sec { margin-bottom:18px; }
/* ---- Operator Profile (P1): chat chip, non-blocking confirmation, folded "used" indicator, settings list ---- */
.pchip { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:6px 0 2px; padding:6px 10px; border:1px solid var(--border); border-radius:10px; background:var(--bg); font-size:12.5px; color:var(--ink); }
.pchip .pchip-text { font-weight:600; }
.pchip button, .pconfirm button, .profile-item button { font:inherit; font-size:12px; padding:2px 8px; border-radius:7px; border:1px solid var(--border); background:transparent; color:var(--ink); cursor:pointer; }
.pchip button:hover, .pconfirm button:hover, .profile-item button:hover { border-color:var(--accent); }
.pchip input { font:inherit; font-size:12px; padding:2px 6px; border-radius:6px; border:1px solid var(--border); background:var(--bg); color:var(--ink); max-width:220px; }
.pconfirm { display:flex; align-items:center; gap:8px; margin:6px 0 2px; font-size:12.5px; color:var(--muted); }
.pconfirm .pconfirm-text { color:var(--ok); }
.pused { margin:4px 0 2px; font-size:12px; color:var(--muted); }
.pused summary { cursor:pointer; list-style:none; }
.pused ul { margin:4px 0 0 16px; padding:0; }
.profile-list { display:flex; flex-direction:column; gap:6px; }
.profile-item { display:grid; grid-template-columns:1fr auto; gap:4px 10px; padding:8px 10px; border:1px solid var(--border); border-radius:8px; background:var(--bg); font-size:12.5px; }
.profile-item .pi-label { font-weight:600; }
.profile-item .pi-meta { color:var(--muted); font-size:11.5px; grid-column:1 / -1; }
.profile-item .pi-actions { display:flex; gap:6px; flex-wrap:wrap; grid-column:1 / -1; }

/* ---- Session bundle export/import (P1): portable signed conversation archives ---- */
.set-check-row { display:flex; align-items:center; gap:6px; font-size:12.5px; color:var(--ink); }
.set-divider { border:0; border-top:1px solid var(--border); margin:12px 0; }
.sb-preview { margin:8px 0; padding:8px 10px; border:1px solid var(--border); border-radius:8px; background:var(--bg); font-size:12.5px; }
.sb-preview .sb-row { display:flex; justify-content:space-between; gap:10px; padding:1px 0; }
.sb-preview .sb-row .sb-k { color:var(--muted); }
.sb-preview .sb-title { font-weight:600; margin-bottom:4px; }
.sb-preview .sb-warn { color:var(--warn, #b58a2c); margin-top:4px; }
.sb-preview .sb-ok { color:var(--ok); margin-top:4px; }
.sb-preview .sb-file { color:var(--muted); font-size:11.5px; }
.profile-item.candidate { border-style:dashed; }
.profile-empty { color:var(--muted); font-size:12.5px; }

.set-sec h3 { margin:0 0 6px; font-size:14px; }
.set-sub { color:var(--muted); font-weight:400; font-size:12px; margin-left:6px; }
.set-help { color:var(--muted); font-size:12.5px; line-height:1.5; margin:0 0 10px; }
.set-help a { color:var(--accent); }
.set-row { display:flex; gap:8px; }
.set-field { margin:0 0 12px; }
.set-field.row { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.set-field label { display:block; font-size:13px; color:var(--ink); margin-bottom:6px; }
.set-field.row label { margin-bottom:0; }
.set-num { color:var(--accent); font-weight:700; }
.set-sub2 { color:var(--muted); font-weight:400; font-size:11.5px; }
.set-range { width:100%; accent-color:var(--accent); cursor:pointer; }
.set-field.row .set-range { width:auto; }
.set-check { width:18px; height:18px; accent-color:var(--accent); cursor:pointer; flex:0 0 auto; }
.set-input { flex:1; min-width:0; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:8px 10px; font:inherit; font-size:13px; }
.set-input:focus { outline:none; border-color:var(--accent); }
.set-btn { background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:0 14px; font:inherit; font-weight:600; cursor:pointer; }
.set-btn:hover { border-color:var(--accent); }
.set-btn.primary { background:var(--grad); color:#fff; border:none; }
.set-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:14px; }
.set-actions .set-btn { min-height:34px; }
.set-status { margin-top:8px; font-size:12.5px; color:var(--muted); }
.set-status.ok { color:var(--accent); }
.set-status.err { color:#f87171; }
/* Credential row: the key field used to be one flex sibling among a select and three buttons, so
   it collapsed to a few characters wide on a narrow modal. It now wraps and holds a real minimum. */
.set-row.key-row { flex-wrap:wrap; align-items:center; row-gap:8px; }
.set-row.key-row .set-btn { min-height:36px; }
.key-field { position:relative; display:flex; align-items:center; flex:1 1 300px; min-width:220px; }
.key-field .set-input { width:100%; padding-right:70px; font-family:ui-monospace,Consolas,monospace; }
.key-reveal { position:absolute; right:6px; background:var(--panel); color:var(--muted); border:1px solid var(--border); border-radius:6px; font:inherit; font-size:11px; font-weight:600; padding:3px 9px; cursor:pointer; }
.key-reveal:hover { color:var(--ink); border-color:var(--accent); }
/* A saved key is a security-relevant state change, so the confirmation is a real banner rather
   than a one-line colour change that reads as decoration. */
.save-banner { display:none; align-items:center; gap:8px; margin-top:10px; padding:9px 12px; border-radius:9px; border:1px solid var(--accent); background:rgba(37,99,255,.12); color:var(--ink); font-size:13px; }
.save-banner.show { display:flex; }
.save-banner .sb-ico { flex:0 0 auto; color:var(--accent); font-weight:700; }
.build-info { display:grid; grid-template-columns:auto minmax(0,1fr); gap:6px 14px; margin:2px 0 0; font-size:12.5px; }
.build-info dt { color:var(--muted); white-space:nowrap; }
.build-info dd { margin:0; color:var(--ink); font-family:ui-monospace,Consolas,monospace; font-size:12px; word-break:break-all; user-select:text; -webkit-user-select:text; }
.usage-tabs { display:flex; gap:6px; margin:4px 0 10px; }
.usage-tab { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:7px; padding:4px 12px; font:inherit; font-size:12px; cursor:pointer; }
.usage-tab:hover { border-color:var(--accent); }
.usage-tab.on { color:var(--ink); border-color:var(--accent); }
.usage-body { font-size:12.5px; color:var(--ink); }
.usage-head { display:flex; gap:16px; margin-bottom:10px; flex-wrap:wrap; }
.usage-head .u-card { border:1px solid var(--border); border-radius:9px; padding:8px 12px; min-width:120px; }
.usage-head .u-card .u-k { color:var(--muted); font-size:11px; }
.usage-head .u-card .u-v { font-size:16px; font-weight:700; margin-top:2px; }
.usage-head .u-card.paid .u-v { color:var(--paid-c); }
.usage-head .u-card.free .u-v { color:var(--ok); }
.usage-table { width:100%; border-collapse:collapse; }
.usage-table th, .usage-table td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--border); font-size:12px; }
.usage-table th { color:var(--muted); font-weight:600; }
.usage-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
.usage-empty { color:var(--muted); }
#newChat:hover { border-color:var(--accent); }
#sessions { flex:1; overflow-y:auto; padding:4px 8px 12px; display:flex; flex-direction:column; gap:4px; }
#sessions .session { display:flex; align-items:center; gap:4px; background:transparent; color:var(--ink); border:1px solid transparent; border-radius:8px; padding:6px 8px; font-size:13px; }
#sessions .session:hover { background:var(--user); }
#sessions .session.active { background:var(--active); border-color:var(--border); }
#sessions .s-emoji { flex:0 0 auto; font-size:13px; line-height:1; }
#sessions .s-emoji:empty { display:none; }
#sessions .s-title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:pointer; }
/* Chats nest under their project/General header as a tree: indented, with a thin left guide line. */
#sessions .session.nested { margin-left:11px; padding-left:12px; border-left:1px solid var(--border); border-top-left-radius:0; border-bottom-left-radius:0; }
#sessions .session.nested.active { border-left-color:var(--accent); }
#sessions .s-rename { flex:1; min-width:0; background:var(--bg); color:var(--ink); border:1px solid var(--accent); border-radius:6px; padding:2px 6px; font:inherit; font-size:13px; }
#sessions .s-actions { display:none; gap:2px; flex:0 0 auto; }
#sessions .session:hover .s-actions { display:flex; }
#sessions .s-act { background:transparent; border:none; color:var(--muted); cursor:pointer; font-size:12px; padding:2px 5px; border-radius:4px; line-height:1; }
#sessions .s-act:hover { color:var(--ink); background:var(--bg); }
#sessions .arch-head { color:var(--muted); font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; padding:12px 10px 4px; cursor:pointer; user-select:none; }
#sessions .arch-head:hover { color:var(--ink); }
#sessions .side-empty { color:var(--muted); font-size:12px; padding:8px 10px; }
/* Chat/project lifecycle indicator: a compact, terminal-style status tag -- never a progress bar,
   never a percentage, only what the runtime has actually reported. Priority (highest first) is
   needs_user > working > complete_unseen, enforced in chatLifecycleState(), not here -- only one
   badge class is ever applied to a row. */
.lc-badge { flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; font-family:ui-monospace,Consolas,monospace; font-size:9px; font-weight:700; letter-spacing:.4px; line-height:1; border-radius:3px; padding:2px 5px; border:1px solid transparent; text-transform:uppercase; }
.lc-badge.lc-working { color:var(--ok); border-color:var(--ok); background:color-mix(in srgb, var(--ok) 14%, transparent); animation:lcPulse 1.7s ease-in-out infinite; }
.lc-badge.lc-needs { color:var(--warn); border-color:var(--warn); background:color-mix(in srgb, var(--warn) 18%, transparent); }
.lc-badge.lc-unseen { width:7px; height:7px; min-width:7px; padding:0; border-radius:50%; background:var(--ink); border-color:transparent; }
@keyframes lcPulse { 0%,100% { opacity:.78; box-shadow:0 0 2px var(--ok); } 50% { opacity:1; box-shadow:0 0 8px var(--ok); } }
@media (prefers-reduced-motion: reduce) { .lc-badge.lc-working { animation:none; } }
.proj-head .lc-badge { margin-left:1px; }
#main { flex:1; display:flex; flex-direction:column; min-width:0; height:100vh; }
/* Wrapping, because the header's own contents are wider than a 360px window: measured there, the
   Activity button sat at x=367-434 -- entirely outside the viewport, unclickable and unscrollable
   to. A second row is worth more than a button nobody can reach, and the overlay panel reads the
   header's real height rather than assuming one line. */
/* flex-shrink:0: #main is a 100vh column and every child carries the default shrink of 1, so on
   short windows (measured at 1440x300) the browser squeezed the WRAPPED header below its content
   height — 49.5px of header with its second control row painting at y=74, underneath #log, which
   buried the Activity toggle. The header may wrap, never compress; #log (flex:1) absorbs instead. */
header { display:flex; flex-wrap:wrap; align-items:center; gap:10px; row-gap:6px; min-height:48px; flex-shrink:0; padding:7px 16px; border-bottom:1px solid var(--border); background:var(--panel); }
header .brand { display:inline-flex; align-items:center; gap:9px; flex:0 0 auto; font-weight:800; letter-spacing:.14em; color:var(--ink); }
header .brand img { display:block; width:34px; height:34px; flex:0 0 auto; object-fit:contain; background:#000; border-radius:8px; }
header .ver { color:var(--muted); font-size:12px; }
header .spacer { flex:1; }
header a { color:var(--accent); text-decoration:none; font-size:13px; margin-left:14px; }
header a:hover { text-decoration:underline; }
/* ---- Update chip (signed updater, 2026-09-01): discreet, top-right, state-colored ---- */
#updateChip { display:inline-flex; align-items:center; gap:6px; font-size:12.5px; padding:4px 10px; border-radius:999px; border:1px solid var(--border); background:transparent; color:var(--muted); cursor:pointer; }
#updateChip .upd-ico { font-size:13px; line-height:1; }
#updateChip.upd-ready { border-color:var(--accent); color:var(--accent); font-weight:600; }
#updateChip.upd-working { color:var(--ink); }
#updateChip.upd-working .upd-ico { display:inline-block; animation:upd-spin 1.1s linear infinite; }
@keyframes upd-spin { to { transform:rotate(360deg); } }
#updateChip.upd-ok { color:var(--ok); }
#updateChip.upd-warn { border-color:#f87171; color:#f87171; }
#updateChip.upd-off { opacity:.55; }
#updatePop { min-width:270px; }
#updatePop .upd-ver { font-weight:700; margin:2px 0 6px; }
#updatePop .upd-notes { font-size:12px; color:var(--muted); white-space:pre-line; max-height:130px; overflow:auto; margin-bottom:6px; }
#updatePop .upd-msg { font-size:12.5px; margin-bottom:8px; }
#updatePop .upd-bar { height:4px; border-radius:2px; background:var(--border); overflow:hidden; margin-bottom:8px; }
#updatePop .upd-bar > i { display:block; height:100%; background:var(--accent); width:0%; transition:width .4s; }
/* Cloud connection pill: red/amber/green/gray reflecting the real OpenRouter auth state. */
#cloudPill { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); border:1px solid var(--border); border-radius:999px; padding:3px 10px; cursor:pointer; background:transparent; font:inherit; line-height:1; }
#cloudPill:hover { border-color:var(--accent); }
#cloudPill .dot { width:8px; height:8px; border-radius:50%; background:var(--muted); flex:0 0 auto; }
#cloudPill.ok .dot { background:var(--ok); box-shadow:0 0 6px var(--ok); }
#cloudPill.bad .dot { background:var(--bad); box-shadow:0 0 6px var(--bad); }
#cloudPill.warn .dot { background:var(--warn); }
#cloudPill .cp-txt { white-space:nowrap; }
#cloudPill .dot.ok { background:var(--ok); box-shadow:0 0 6px var(--ok); }
#cloudPill .dot.bad { background:var(--bad); box-shadow:0 0 6px var(--bad); }
#cloudPill .dot.warn { background:var(--warn); }
/* Idle = steady glow; in use = a slow, gentle pulse on the connected (ok) dots only. */
@keyframes cpPulse { 0%,100% { box-shadow:0 0 3px var(--ok); opacity:.8; } 50% { box-shadow:0 0 11px var(--ok); opacity:1; } }
#cloudPill.working .dot.ok, #cloudPill.working.ok .dot { animation:cpPulse 1.9s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { #cloudPill.working .dot.ok, #cloudPill.working.ok .dot { animation:none; } }
#cloudPill .cp-chip { display:inline-flex; align-items:center; gap:5px; }
#cloudPill .cp-sep { color:var(--muted); margin:0 1px; }
#menu { display:none; background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:4px 10px; font:inherit; cursor:pointer; }
#log { flex:1; overflow-y:auto; padding:28px max(20px,calc((100% - 760px)/2)); display:flex; flex-direction:column; gap:14px; user-select:text; -webkit-user-select:text; cursor:text; background:var(--chat); }
.msg { position:relative; max-width:min(760px,92%); padding:10px 34px 10px 14px; border-radius:12px; white-space:pre-wrap; word-wrap:break-word; user-select:text; -webkit-user-select:text; }
.msg.user { align-self:flex-end; background:var(--user); border:1px solid var(--border); }
.msg.assistant { align-self:stretch; width:100%; max-width:100%; min-width:0; padding:14px 0; border:0; border-radius:0; background:transparent; font-size:15px; line-height:1.65; }
.msg.assistant.pending { color:var(--muted); }
.msg-text a { color:var(--accent); text-decoration:underline; text-underline-offset:2px; word-break:break-word; }
.msg-text a:hover { text-decoration:none; }
.msg-actions { position:absolute; top:6px; right:6px; display:flex; gap:4px; opacity:0; transition:opacity .12s; }
.msg:hover .msg-actions, .msg:focus-within .msg-actions { opacity:1; }
.msg-copy, .msg-act { background:var(--bg); color:var(--muted); border:1px solid var(--border); border-radius:6px; font-size:11px; line-height:1; padding:3px 7px; cursor:pointer; user-select:none; }
.msg-copy:hover, .msg-act:hover { color:var(--ink); border-color:var(--accent); }
.msg-export-status { display:block; max-width:100%; overflow-wrap:anywhere; margin-top:6px; font-size:12px; color:var(--muted); }
.msg-export-status:empty { display:none; }
.msg-pin.on { color:var(--accent); border-color:var(--accent); }
.msg-act.icon { padding:3px 6px; font-size:12px; }
.msg-act.on { color:var(--accent); border-color:var(--accent); }
/* Code-block copy: the button lives at the top-right edge of the fenced block it copies, shown on
   hover/focus of the wrapper exactly like the message action row. It copies the code body only --
   never the fence markers, never any button label. */
.chat-code-wrap { position:relative; }
.chat-code-wrap .code-copy { position:absolute; top:6px; right:6px; opacity:0; transition:opacity .12s; background:var(--bg); color:var(--muted); border:1px solid var(--border); border-radius:6px; font-size:11px; line-height:1; padding:3px 7px; cursor:pointer; user-select:none; }
.chat-code-wrap:hover .code-copy, .chat-code-wrap:focus-within .code-copy { opacity:1; }
.chat-code-wrap .code-copy:hover { color:var(--ink); border-color:var(--accent); }
/* Export dialog controls: rows of the same form vocabulary the settings card uses. */
.export-row { display:flex; align-items:center; gap:8px; margin:10px 0; font-size:13px; }
.export-row label { flex:0 0 auto; color:var(--ink); }
.export-row select { background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:5px 8px; font:inherit; }
.export-row input[type="checkbox"] { accent-color:var(--accent); }
.export-note { color:var(--muted); font-size:12px; line-height:1.5; margin:8px 0; }
.export-preview { background:var(--bg); border:1px solid var(--border); border-radius:8px; color:var(--muted); font-size:12px; line-height:1.5; padding:8px 10px; margin:10px 0; white-space:pre-wrap; }
.export-status { font-size:12px; margin-top:8px; min-height:16px; }
.export-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:12px; }
/* Always visible (not hover-gated like .msg-actions) but visually secondary: small, muted, no
   seconds. A user bubble gets it at send time; an assistant bubble gets it once the turn truly
   ends (finishRun -> setMsgTime), never while still streaming. */
.msg-time { display:block; margin-top:4px; font-size:10px; line-height:1; color:var(--muted); opacity:.75; text-align:right; user-select:none; }
.empty { color:var(--ink); align-self:center; margin-top:12vh; text-align:center; max-width:560px; }
.empty-greet { color:var(--ink); font-size:22px; font-weight:600; margin-bottom:4px; }
.empty-sub { color:var(--ink); font-size:16px; margin-bottom:18px; }
.empty-hint { font-family:Georgia,'Times New Roman',serif; font-style:italic; font-size:14px; line-height:1.5; color:var(--ink); max-width:440px; margin:2px auto 0; }
/* A size container: the control row's breakpoint has to key off the room the FOOTER actually has,
   not off the window. The sidebar and the Activity panel can take 900px of a 1280px window, so a
   viewport-keyed breakpoint reads "wide" while the composer is in fact 340px across. */
footer { background:linear-gradient(transparent,var(--chat) 24%); padding:10px 20px 18px; display:flex; flex-direction:column; gap:8px; flex-shrink:0; container-type:inline-size; container-name:composer; }
/* Extreme-short windows (measured at 1440x300, dragged): the wrapped header (up to three rows)
   plus the composer footer cannot both fit in 300px of height, and the composer's row landed
   below the fold with nothing over it — unreachable. Decorative chrome gives way first: the
   version string returns the moment the window regains height (Media Studio moved to the Skills panel). */
@media (max-height: 360px) {
  header { min-height: 0; padding: 4px 12px; row-gap: 2px; }
  header .ver, header a { display: none; }
  footer { padding: 6px 20px 8px; gap: 6px; }
}
.composer-row { display:flex; gap:10px; }
/* Column, not wrap: a row-wrap flex packs short chips side by side ("hi" + "hello" sharing a
   line), which reads as one merged item instead of two queued jobs. Column direction gives every
   chip its own row regardless of how short its text is, with no wrap math needed. */
#queue { display:none; flex-direction:column; align-items:flex-start; gap:6px; }
.qchip { display:inline-flex; align-items:center; gap:6px; max-width:100%; background:var(--user); border:1px solid var(--border); border-radius:14px; padding:3px 8px 3px 10px; font-size:12px; color:var(--ink); }
.qchip.running { border-color:var(--accent); }
.qchip-text { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:340px; }
.qchip-x { background:transparent; border:none; color:var(--muted); cursor:pointer; font-size:14px; line-height:1; padding:0 2px; }
.qchip-x:hover { color:var(--ink); }
/* The "+N queued" / "Show less" toggle: same pill as a chip, but a plain button (no text/x split). */
.qchip-more { cursor:pointer; font-weight:650; color:var(--ink); font:inherit; font-size:12px; }
.qchip-more:hover { border-color:var(--accent); }
#input { flex:1 1 auto; min-width:200px; resize:none; min-height:44px; max-height:180px; background:var(--field); color:var(--ink); border:1px solid var(--border); border-radius:10px; padding:10px 12px; font:inherit; }
#input::placeholder { color:#aab0c4; opacity:1; }
#send { background:var(--grad); color:#fff; border:none; border-radius:10px; padding:0 18px; font-weight:700; cursor:pointer; }
#send:disabled { opacity:.5; cursor:default; }
/* Dictation: the microphone writes DRAFT text into the textarea; it never sends anything.
   The button is an ICON with four distinct states: idle (plain mic), recording (warm ring,
   live), transcribing (muted, brief wait while the on-device recogniser runs) and error
   (bad-tone ring, shown until the next attempt). The word never disappears: aria/title carry
   the state, colour is never the sole carrier. */
#dictateBtn, #voiceModeBtn { background:var(--field); color:var(--ink); border:1px solid var(--border); border-radius:10px; padding:0 7px; cursor:pointer; font-size:15px; line-height:1; display:inline-flex; align-items:center; }
#dictateBtn .mic-glyph, #voiceModeBtn .mic-glyph { font-size:15px; line-height:1; }
#dictateBtn:disabled, #voiceModeBtn:disabled { opacity:.5; cursor:default; }
#dictateBtn.recording { border-color:var(--warn); box-shadow:0 0 0 2px color-mix(in srgb, var(--warn) 35%, transparent); }
#dictateBtn.transcribing { border-color:var(--accent); opacity:.75; }
#dictateBtn.error { border-color:var(--bad); }
#voiceModeBtn[aria-pressed="true"] { border-color:var(--accent); color:var(--accent); }
#dictationNote { color:var(--muted); font-size:11px; line-height:1.45; margin-top:4px; }
#dictationNote[hidden], #dictationNotice[hidden] { display:none; }
#dictationNotice { display:flex; align-items:flex-start; gap:8px; }
#dictationNote { flex:1; }
#dictationDismiss { flex:none; border:0; background:transparent; color:var(--muted); cursor:pointer; font-size:18px; padding:2px 6px; }
#dictationControls { display:flex; align-items:center; gap:8px; margin-top:4px; font-size:11px; color:var(--muted); }
#dictationControls[hidden] { display:none; }
#dictationLocale { font:inherit; font-size:11px; color:var(--ink); background:var(--field); border:1px solid var(--border); border-radius:6px; padding:2px 4px; }
#dictationRetry { font:inherit; font-size:11px; background:transparent; color:var(--accent); border:1px solid var(--border); border-radius:6px; padding:2px 8px; cursor:pointer; }
#voiceStop { position:fixed; right:18px; bottom:150px; z-index:40; background:var(--panel); color:var(--ink); border:1px solid var(--accent); border-radius:999px; padding:6px 14px; font:inherit; font-size:12px; cursor:pointer; box-shadow:0 6px 18px rgba(0,0,0,.4); }
#voiceStop[hidden] { display:none; }
/* Narrow composer: the mic is an icon-only tap target and the send button gives back its
   slack, so the textarea keeps a usable width (the layout pack requires >=120px at 360).
   The buttons are the reserve: they shrink first so #input keeps its 200px floor. A viewport
   media query alone is not enough — a DRAGGED splitter squeezes the chat column while the
   window stays wide (measured: 1280x860 dragged put the input at 151px against a 200px floor
   once the dictate button joined the row), so the floor lives on the input itself. */
#send, #dictateBtn, #voiceModeBtn { flex:0 1 auto; min-width:0; white-space:nowrap; overflow:hidden; }
@media (max-width: 420px) {
  #input { min-width:120px; }
  #dictateBtn { padding: 0 4px; }
  #voiceModeBtn { padding: 0 4px; }
  #send { padding: 0 10px; }
}
.perm-bar { display:flex; align-items:flex-start; gap:10px; margin:0 0 8px; padding:10px 12px; border:1px solid var(--warn); border-radius:12px; background:color-mix(in srgb, var(--warn) 10%, var(--field)); font-size:13px; color:var(--ink); }
.perm-bar[hidden] { display:none; }
/* OPERATOR-ANSWER SURFACES OUTRANK THE COMPANION LAYER. #companionLayer is a fixed overlay at
   z-index 30 whose 112px sprite takes pointer events, and `footer` (container-type) establishes
   its own stacking context, so the permission bar and the bypass banner inside it could never
   out-stack the pet: with the sprite parked over the footer, an ordinary pointer click landed on
   the pet and Revoke was reachable only through forced clicks. While either answer surface is
   visible, the footer itself is raised above the layer (2026-09-18): the click reaches the
   button, keyboard focus order is unchanged (real buttons in the DOM), and the pet stays
   draggable everywhere else. */
body.answer-pending footer { position: relative; z-index: 35; }
.perm-ico { font-size:15px; flex:0 0 auto; }
.perm-copy { flex:1 1 auto; min-width:0; }
.perm-msg { font-weight:650; }
.perm-meta { color:var(--muted); font-size:11px; line-height:1.45; margin-top:2px; }
.perm-detail { margin-top:7px; padding:7px 8px; border:1px solid var(--border); border-radius:8px; background:var(--bg); color:var(--muted); font-size:11px; white-space:pre-wrap; max-height:160px; overflow:auto; }
.perm-actions { flex:0 0 auto; display:flex; flex-wrap:wrap; justify-content:flex-end; gap:6px; }
.perm-btn { font:inherit; font-size:12.5px; border:1px solid var(--border); background:var(--field); color:var(--ink); border-radius:8px; padding:5px 11px; cursor:pointer; white-space:nowrap; }
.perm-btn:hover { border-color:var(--accent); }
.perm-btn.primary { background:var(--grad); color:#fff; border:none; font-weight:600; }
.perm-btn.ghost { background:transparent; color:var(--muted); }

/* ---- Composer control bar (mode / attach / model / effort) ---- */
/* One line at every supported compact width. The row is Mode | Project/Chat | Model, and a long
   cloud model name used to push Model onto a second line (measured: the row went 32px -> 71px at a
   620px content width with a 41-character label). Nothing here is allowed to grow past its content,
   the flexible parts shrink with an ellipsis instead, and wrapping is only permitted below the
   narrowest supported width, where one line genuinely cannot hold three controls. */
.control-bar { display:flex; align-items:center; gap:8px; flex-wrap:nowrap; min-width:0; }
.council-lock { margin:0 0 8px; padding:7px 11px; border:1px solid var(--accent,#5eead4); border-radius:9px;
  background:rgba(94,234,212,.06); color:var(--muted,#9aa1af); font-size:11.5px; line-height:1.5; }
/* A plain gap. This used to be the thing positioning the model control -- `flex:1` capped at 96px --
   which is why the selector floated: its position was the sum of the widths to its left and nothing
   else, so it sat at x=663 whether the window was 900px or 1900px and whether Activity was open or
   shut, while the composer's right edge moved between 425px and 1805px. The anchor is now the auto
   margin on #modelCtrl, which cannot be undone by retuning a spacer. */
.cb-spacer { flex:0 0 8px; min-width:0; }
/* `.ctrl` was a block, so #modeCtrl's two inline-flex children (the mode button and its round `i`)
   wrapped onto separate LINES the moment the row got tight -- measured at 58px of row height from
   700px to 1280px with the Activity panel open, against 32px unwrapped. A flex row cannot do that. */
.ctrl { position:relative; display:inline-flex; align-items:center; gap:6px; min-width:0; }
/* Priority collapse. Everything shares one line for as long as one line can hold it, and what gives
   way first is what costs least: the context chips ellipsize, then the model name, and the mode
   button -- the control that states what VOOL is allowed to do -- shrinks last and keeps a floor
   wide enough to still read. The full text of anything clipped is on the element's tooltip.
   The floor belongs on the wrapper as well as on the button: `.ctrl { min-width:0 }` let #modelCtrl
   shrink to 81px around a button with a 96px floor, and the button hung 15px out of the row. */
#modeCtrl { flex:0 1 auto; min-width:116px; }
#modeBtn { flex:0 1 auto; min-width:84px; }
/* The anchor. `margin-left:auto` absorbs whatever slack the row has, pushing the model group to the
   end of the row; `.cb-tail` then holds back exactly the width the Send button occupies, so the
   group's right edge lands on the TEXTAREA's right edge rather than the footer's. */
#modelCtrl { flex:0 1 auto; min-width:0; }
/* The reserve, as a flex item rather than padding on the row. Padding would hold that width back
   unconditionally, and at tight sizes it took the space the context chips needed -- measured at a
   520px window, #ctxBar clipped its own text, and a 900px letterboxed one wrapped the row to three
   lines. A shrink factor far above every other item's means this is what gives way first: the
   anchor is exact while there is room for it, and it collapses before anything readable does.
   `--composer-tail` is measured, because the button becomes "Queue" mid-turn and grows 8px. */
.cb-tail { flex:0 20 var(--composer-tail,0px); min-width:0; align-self:stretch; pointer-events:none; }
#modelBtn { min-width:132px; border-radius:999px; padding:5px 12px; color:var(--muted); background:var(--field); }
#modelBtn:hover { color:var(--ink); }
.model-lane { flex:0 0 auto; border-radius:999px; padding:1px 7px; font-size:9px; line-height:16px; font-weight:800; letter-spacing:.08em; }
.model-lane.auto { color:#c4b5fd; background:#27213a; }
.model-lane.local { color:var(--accent); background:#0e3b32; }
.model-lane.cloud { color:#93c5fd; background:#15294d; }
.mode-info { flex:0 0 auto; }
/* Below this the row genuinely cannot hold three controls on one line: the floors above plus the
   gaps come to 304px and there is nothing further to give. The container query is the real rule
   (the footer's own width); the media query is the fallback where container queries are
   unavailable, and is held at the same threshold so the two can never disagree. */
@container composer (max-width: 310px) {
  .control-bar { flex-wrap:wrap; }
}
@media (max-width: 310px) {
  .control-bar { flex-wrap:wrap; }
}
/* Tight-but-workable: the PROJECT chip collapses out before the chat chip is reduced to nothing --
   keyed on the slot, not on `.muted`, which only ever marked the no-project "General" case and so
   left a bound project's chip in place at exactly the widths that could not hold two chips.
   #ctxBar carries the full "Project › Chat" path as its tooltip, so nothing is actually lost. */
@container composer (max-width: 470px) {
  .ctx-chip.cc-project, .ctx-sep { display:none; }
}
.ctrl-btn { display:inline-flex; align-items:center; gap:6px; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:5px 10px; font:inherit; font-size:13px; cursor:pointer; }
.ctrl-btn:hover { border-color:var(--accent); }
.ctrl-btn:disabled { opacity:.55; cursor:default; }
.ctrl-btn .caret { flex:0 0 auto; color:var(--muted); font-size:10px; }
/* The character cap is applied in script (so the tooltip keeps the exact name); this is the layout
   backstop for a label that is still too wide for the room the row actually has. */
.ctrl-btn { max-width:100%; min-width:0; }
.cb-lbl { display:inline-block; max-width:30ch; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom; }
.ctrl-btn.bypass { color:#ffd7d9; border-color:var(--bad); background:color-mix(in srgb,var(--bad) 13%,var(--bg)); }
.mode-info { width:26px; height:26px; padding:0; justify-content:center; border-radius:999px; color:var(--muted); }
.mode-help { min-width:300px; max-width:min(380px,88vw); padding:11px 12px; color:var(--ink); font-size:12px; line-height:1.5; }
.mode-help b { display:block; margin-bottom:3px; }
.mode-tooltip { position:fixed; z-index:80; max-width:300px; padding:7px 9px; border:1px solid var(--border); border-radius:8px; background:#070a0f; color:var(--ink); box-shadow:0 8px 24px #000b; font-size:11px; line-height:1.4; pointer-events:none; }
.bypass-banner { display:flex; align-items:center; gap:8px; padding:7px 10px; border:1px solid var(--bad); border-radius:9px; background:color-mix(in srgb,var(--bad) 12%,var(--field)); color:#ffd7d9; font-size:12px; }
/* ---- Attachments: the draft strip above the composer row, and the chips inside a sent bubble ----
   One draft per chat, uploaded at pick time. The strip sits between the control row and the
   textarea so it can wrap freely at narrow widths without moving the model anchor; chips
   ellipsize their name and keep their controls, so a 360px window still shows every remove
   button. A refused or failed upload carries its reason ON the chip -- the reason is the receipt. */
#attachCtrl { flex:0 0 auto; }
#attachBtn .cb-ico { font-size:14px; line-height:1; }
#attachInput { display:none; }
#attachStrip { display:flex; flex-direction:column; gap:4px; min-width:0; }
#attachStrip[hidden] { display:none; }
.att-chips { display:flex; flex-wrap:wrap; gap:6px; align-items:flex-start; min-width:0; }
.att-hint { color:var(--muted); font-size:11px; line-height:1.4; }
.att-chip { display:inline-flex; flex-wrap:wrap; align-items:center; gap:6px; max-width:min(100%,280px); min-width:0; padding:4px 6px 4px 8px; border:1px solid var(--border); border-radius:12px; background:var(--field); color:var(--ink); font-size:12px; line-height:1.3; }
.att-chip[data-state="uploading"] { opacity:.75; }
.att-chip[data-state="failed"] { border-color:var(--bad); background:color-mix(in srgb,var(--bad) 12%,var(--field)); }
.att-chip .att-thumb { width:22px; height:22px; border-radius:5px; object-fit:cover; flex:0 0 auto; background:#000; }
.att-chip .att-ico { flex:0 0 auto; font-size:12px; color:var(--muted); }
.att-chip .att-name { flex:1 1 60px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.att-chip .att-meta { flex:0 0 auto; color:var(--muted); font-size:10.5px; white-space:nowrap; }
.att-chip .att-warn { flex:1 1 100%; color:#fbbf24; font-size:10.5px; white-space:normal; }
.att-chip .att-error { flex:1 1 100%; color:#ffd7d9; font-size:10.5px; white-space:normal; }
.att-chip .att-x, .att-chip .att-retry { flex:0 0 auto; border:none; background:transparent; color:var(--muted); cursor:pointer; font:inherit; font-size:12px; padding:0 3px; border-radius:6px; }
.att-chip .att-retry { color:var(--accent); font-weight:700; }
.att-chip .att-x:hover, .att-chip .att-retry:hover { color:var(--ink); background:var(--bg); }
/* The bubble's hover actions (Copy) float 6px inside its right edge and are wider than the 34px
   padding reserved for them, so the first row of chips ends 20px early: a document chip's Preview
   button was measured underneath the Copy button, unclickable. User bubbles keep the single Copy
   button this reservation was measured for -- the PB05 copy variants (raw Markdown vs plain text
   vs rich) live on assistant bubbles, which carry no chips. */
.msg-att { display:flex; flex-wrap:wrap; gap:6px; margin:0 20px 6px 0; }
.msg-att .att-chip { cursor:default; }
.msg-att .att-chip[data-outcome="omitted"], .msg-att .att-chip[data-outcome="ignored"] { border-style:dashed; }
/* A pasted DOCUMENT chip: the same chip with a wider ceiling, a Preview toggle and an expandable
   exact-text panel below the row. The panel keeps whitespace verbatim (white-space:pre) and scrolls
   inside itself, so a 3,000-column log line never widens the page; its height is capped against
   the viewport so a phone still shows the composer under it. No animation is attached to the
   toggle, so the reduced-motion rule has nothing to fight. */
.att-chip[data-document="1"] { max-width:min(100%,460px); }
.att-chip .att-expand { flex:0 0 auto; border:none; background:transparent; color:var(--accent); cursor:pointer; font:inherit; font-size:11px; font-weight:700; padding:0 3px; border-radius:6px; }
.att-chip .att-expand:hover { color:var(--ink); background:var(--bg); }
.att-chip .att-preview { flex:1 1 100%; box-sizing:border-box; min-width:0; max-width:100%; margin:4px 0 0; padding:6px 8px; max-height:min(40vh,320px); overflow:auto; white-space:pre; font:11.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:var(--ink); background:var(--bg); border:1px solid var(--border); border-radius:8px; user-select:text; -webkit-user-select:text; cursor:text; }
.att-chip .att-preview[hidden] { display:none; }
.att-chip .att-more { flex:1 1 100%; color:var(--muted); font-size:10.5px; }
.att-chip .att-more[hidden] { display:none; }
footer.att-drop { outline:2px dashed var(--accent); outline-offset:-6px; }
@container composer (max-width: 470px) {
  #attachBtn .cb-lbl { display:none; }
  #attachBtn { min-width:0; padding:5px 8px; }
}
.bypass-banner[hidden] { display:none; }
.bypass-banner button { margin-left:auto; background:transparent; color:#ffd7d9; border:1px solid var(--bad); border-radius:7px; padding:3px 8px; cursor:pointer; }
/* The popover grows UPWARD from a control sitting at the bottom of the window, so a long list ran
   off the top of the app and the top of the list (VOOL Auto included) could only be reached by
   maximising the window. It is now a single scroll container whose height is clamped to the space
   actually available; `positionPopover` sets the exact figure and may flip it below the button. The
   CSS values here are the floor, so the popover is bounded even before any script runs. */
.popover { position:absolute; bottom:calc(100% + 6px); left:0; min-width:230px; max-height:min(72vh,560px); overflow-y:auto; overscroll-behavior:contain; background:var(--panel); border:1px solid var(--border); border-radius:10px; box-shadow:0 8px 28px #000a; padding:6px; z-index:30; flex-direction:column; gap:1px; display:none; }
.popover.open { display:flex; }
.popover.right { left:auto; right:0; }
.popover.below { bottom:auto; top:calc(100% + 6px); }
/* One scroller, not three: the inner lists used to cap themselves at 230px each, which both nested
   scrollbars inside the popover's own and let the total still exceed the window. */
#modelPop .cloud-list, #modelPop .local-list { max-height:none; overflow:visible; }
#modelPop .cloud-picker { position:sticky; top:-6px; z-index:1; background:var(--panel); padding:8px 4px; display:grid; gap:7px; }
#modelPop .cloud-picker label { display:grid; gap:4px; font-size:12px; color:var(--muted); }
#modelPop .cloud-picker select, #modelPop .cloud-picker input { width:100%; min-width:0; box-sizing:border-box; padding:7px 9px; border:1px solid var(--border); border-radius:7px; background:var(--bg); color:var(--text); }
.proj-emoji { flex:0 0 auto; font-size:13px; line-height:1; }
.proj-emoji:empty { display:none; }
.emoji-picker { position:fixed; background:#0b0f14; border:1px solid var(--border); border-radius:10px; box-shadow:0 8px 28px #000a; padding:8px; z-index:40; width:238px; }
.emoji-grid { display:grid; grid-template-columns:repeat(8, 1fr); gap:2px; }
.emoji-cell { background:transparent; border:1px solid transparent; border-radius:6px; font-size:16px; line-height:1; padding:5px 0; cursor:pointer; }
.emoji-cell:hover { background:var(--active); }
.emoji-cell.on { border-color:var(--accent); }
.color-row { display:flex; gap:4px; margin-bottom:6px; }
.color-cell { flex:1 1 0; height:20px; border:1px solid #0006; border-radius:5px; cursor:pointer; padding:0; }
.color-cell:hover { outline:2px solid var(--muted); outline-offset:1px; }
.color-cell.on { outline:2px solid #fff; outline-offset:1px; }
#sideResize { flex:0 0 5px; cursor:col-resize; background:transparent; align-self:stretch; }
#sideResize:hover, #sideResize.dragging { background:var(--accent); }
#xpResize { flex:0 0 5px; cursor:col-resize; background:transparent; align-self:stretch; }
#xpResize:hover, #xpResize.dragging { background:var(--accent); }
body:not(.panel-open) #xpResize { display:none; }
/* `flex:0 1 auto` + `overflow:hidden` cut the chat chip off mid-glyph rather than ellipsizing it
   (measured at a 1000px window with the panel open: #ctxBar's content was wider than its box).
   The chips shrink themselves now, so a clipped name ends in an ellipsis and keeps its tooltip.
   Shrink weight 4 is what makes the chips give ground before the model and mode buttons do, and the
   bar states its floor explicitly: `overflow:hidden` makes flex's automatic minimum resolve to
   ZERO rather than to min-content, so without it the bar shrinks straight through its own chips. */
.ctx-bar { display:flex; align-items:center; gap:6px; flex:0 4 auto; min-width:56px; overflow:hidden; }
.ctx-chip { display:inline-flex; align-items:center; gap:5px; flex:0 1 auto; min-width:56px; max-width:190px; padding:4px 10px; border-radius:8px; border:1px solid var(--border); background:var(--bg); font-size:12px; color:var(--ink); white-space:nowrap; overflow:hidden; }
.ctx-chip:not(.muted) { cursor:default; }
.ctx-chip .cc-emoji { flex:0 0 auto; }
.ctx-chip .cc-name { min-width:0; overflow:hidden; text-overflow:ellipsis; }
.ctx-chip.muted { color:var(--muted); }
.ctx-sep { color:var(--muted); font-size:11px; flex:0 0 auto; }
.emoji-clear { margin-top:6px; width:100%; background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:6px; padding:5px; font:inherit; font-size:12px; cursor:pointer; }
.emoji-clear:hover { color:var(--ink); border-color:var(--accent); }
.pop-title { color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; padding:6px 8px 4px; }
.pop-group { color:var(--muted); font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; padding:8px 8px 2px; border-top:1px solid var(--border); margin-top:3px; }
.pop-item { display:flex; align-items:center; gap:8px; background:transparent; border:none; color:var(--ink); border-radius:7px; padding:7px 8px; font:inherit; font-size:13px; cursor:pointer; text-align:left; width:100%; }
.pop-item:hover:not(:disabled) { background:var(--user); }
.pop-item:disabled, .pop-item[aria-disabled="true"] { color:var(--muted); cursor:default; }
.pop-item .pi-check { flex:0 0 auto; width:14px; color:var(--accent); font-size:12px; }
.pop-item .pi-icon { flex:0 0 auto; width:16px; color:var(--muted); font-size:12px; text-align:center; }
.pop-item .pi-body { flex:1; min-width:0; }
.pop-item .pi-hint { color:var(--muted); font-size:11px; }
.pop-item .pi-price { flex:0 0 auto; color:var(--muted); font-size:11px; margin-left:auto; padding-left:8px; }
.pop-item .pi-price.free { color:var(--ok); }
.pop-note, .pop-hint { color:var(--muted); font-size:11px; padding:5px 8px 4px; }
.pop-hint { line-height:1.45; }
.side-note { color:var(--muted); font-size:11px; line-height:1.45; padding:8px 10px 2px; }
/* Live OpenRouter model rows: an order/price toolbar plus a scroll-capped list, all injected
   under the static tiers and removed wholesale when the key goes away (fail-soft to static). */
.pop-order { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:6px 8px 2px; font-size:11px; color:var(--muted); border-top:1px solid var(--border); margin-top:3px; }
.pop-order select { background:var(--panel); color:var(--ink); border:1px solid var(--border); border-radius:6px; font:inherit; font-size:11px; padding:1px 4px; max-width:100%; }
.pop-order label { display:inline-flex; align-items:center; gap:4px; cursor:pointer; margin-left:auto; }
/* The Auto-fallback bar carries a label, a model select and an explanatory hint. In a popover only
   as wide as its widest static row, those three wrapped one word per line into a tall thin column.
   The popover is given room to breathe and the bar lays its label and hint out on their own rows. */
.popover#modelPop { min-width:330px; max-width:min(430px,92vw); }
.pop-order.auto-fallback { flex-wrap:wrap; row-gap:4px; }
.pop-order.auto-fallback label { display:flex; align-items:center; gap:6px; margin-left:0; width:100%; white-space:nowrap; }
.pop-order.auto-fallback label select { flex:1 1 auto; min-width:0; }
.pop-order.auto-fallback .pi-hint { flex:1 1 100%; line-height:1.4; }
.cloud-list { max-height:230px; overflow-y:auto; }
.local-list { max-height:230px; overflow-y:auto; }
.pop-item.cloud-dyn, .pop-item.local-dyn { font-size:12px; }
.pop-item.local-dyn .pi-body { display:flex; flex-direction:column; gap:1px; }
@media (max-width: 720px) {
  #sidebar { position:fixed; left:0; top:0; bottom:0; z-index:20; box-shadow:2px 0 12px #0008; }
  body:not(.sidebar-open) #sidebar { display:none; }
  #menu { display:inline-flex; }
}

/* ---- Search: chat transcript + Activity panel ---- */
/* A slim bar that costs nothing until it is opened: hidden by default, so it takes no height from
   the transcript and cannot introduce dead space in the composer's column. */
.search-bar { display:flex; align-items:center; gap:6px; padding:6px 16px; border-bottom:1px solid var(--border); background:var(--panel); }
.search-bar[hidden] { display:none; }
.search-field { flex:1 1 auto; min-width:0; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:5px 9px; font:inherit; font-size:13px; }
.search-field:focus { outline:none; border-color:var(--accent); }
.search-count { flex:0 0 auto; color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.search-count.none { color:var(--warn); }
.search-btn { flex:0 0 auto; background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:7px; padding:3px 8px; font:inherit; font-size:12px; line-height:1.2; cursor:pointer; }
.search-btn:hover:not(:disabled) { color:var(--ink); border-color:var(--accent); }
.search-btn:disabled { opacity:.45; cursor:default; }
#searchBtn, #exportBtn { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:4px 10px; font:inherit; font-size:13px; cursor:pointer; margin-left:10px; }
#searchBtn:hover, #searchBtn.on, #exportBtn:hover { color:var(--ink); border-color:var(--accent); }
mark.cs-hit { background:color-mix(in srgb,var(--accent) 38%,transparent); color:inherit; border-radius:3px; padding:0 1px; }
mark.cs-hit.cs-active { background:var(--warn); color:#1a1205; }
.msg.cs-current-turn { box-shadow:0 0 0 2px var(--accent); }
/* The Activity panel's own literal search. Same shape, panel-sized. */
.xp-search-row { display:flex; align-items:center; gap:6px; padding:0 12px 8px; }
.xp-search-row[hidden] { display:none; }
.xp-search { flex:1 1 auto; min-width:0; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:7px; padding:3px 7px; font:inherit; font-size:11.5px; }
.xp-search:focus { outline:none; border-color:var(--accent); }
.xp-row.as-hit, .xp-row-flat.as-hit, .rc-item.as-hit, .xp-item.as-hit, .xp-kv-row.as-hit, .xp-history-card.as-hit { background:color-mix(in srgb,var(--accent) 12%,transparent); border-radius:6px; }
.as-current { box-shadow:0 0 0 2px var(--accent); border-radius:6px; }

/* ---- Live execution: status card ---- */
:root { --paid-c:#f0b429; --warn:#f0b429; --bad:#f2545b; --ok:#2563FF; --or-c:#a78bfa; }
#panelBtn { background:transparent; color:var(--accent); border:1px solid var(--border); border-radius:8px; padding:4px 10px; font:inherit; font-size:13px; cursor:pointer; margin-left:14px; }
#panelBtn:hover, #panelBtn.on { border-color:var(--accent); }
#pinBtn { display:inline-flex; align-items:center; gap:5px; background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:4px 10px; font:inherit; font-size:13px; cursor:pointer; margin-left:10px; }
#pinBtn:hover, #pinBtn.has { border-color:var(--accent); color:var(--ink); }
#pinBtn .pin-n { font-size:11px; color:var(--accent); font-weight:700; }
#pinBtn .pin-n:empty { display:none; }
.pin-card { border:1px solid var(--border); border-radius:10px; padding:10px 12px; margin-bottom:8px; background:var(--panel); }
.pin-meta { font-size:11px; font-weight:700; letter-spacing:.4px; color:var(--muted); text-transform:uppercase; margin-bottom:5px; }
.pin-text { font-size:13px; line-height:1.45; color:var(--ink); white-space:pre-wrap; word-break:break-word; max-height:220px; overflow:auto; }
.pin-row { display:flex; gap:6px; margin-top:8px; }
.pin-btn2 { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:6px; padding:4px 10px; font:inherit; font-size:12px; cursor:pointer; }
.pin-btn2:hover { color:var(--ink); border-color:var(--accent); }
.pin-empty { color:var(--muted); font-size:13px; padding:8px 2px; }
.task-card { align-self:stretch; max-width:min(760px,92%); background:linear-gradient(180deg,var(--panel),#0d1631); border:1px solid var(--border); border-left:3px solid var(--accent); border-radius:12px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; }
.task-card.done { border-left-color:var(--muted); opacity:.92; }
/* A plain model answer needs no completed-status card -- the answer message stands alone (clean). */
.task-card.bare { display:none; }
.task-card.failed { border-left-color:var(--bad); }
.task-card.cancelled { border-left-color:var(--muted); }
.task-card.perm { border-left-color:var(--warn); }
/* The head is a wrapping row, not a fixed one: a long status string, a long model id and the Stop
   button used to compete for the same line and spill out of the card (QA-050-006/007/008). */
.tc-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px 10px; }
/* VOOL Core footprint -- fixed, so the card never reflows as the indicator changes state. */
/* The working mark: VOOL's official hand-drawn mark (V over oo over the L-smile), traced as one
   inline SVG and brought to life with CSS alone. The resting markup IS the complete static mark:
   every path draws plainly (dash gaps live only inside keyframes) and the particle/burst circles
   sit at opacity:0 -- so `animation: none` (reduced motion, or a terminal card) shows the finished
   logo instantly, and removing the card removes the animation with it. No timers, no rAF. */
.tc-mark { flex:0 0 auto; width:56px; height:56px; color:var(--accent); }
.tc-mark path { fill:none; stroke:currentColor; stroke-linecap:round; stroke-linejoin:round; }
.tc-mark .tc-p, .tc-mark .tc-b { fill:currentColor; opacity:0; }
.tc-mark .tc-b { transform-box: fill-box; transform-origin: center; }
.tc-mark .tc-life { transform-box: view-box; transform-origin: 50% 55%; }
/* One shared 3.4s clock drives every layer, so the staging is structural: V sketches and resolves,
   then both O's, then the L, the finished mark holds, then it implodes inward and the cycle
   restarts. pathLength=100 on every stroke lets one dash recipe serve all four parts. */
.tc-mark .tc-v    { animation: tcDrawV  3.4s linear infinite; }
.tc-mark .tc-ol   { animation: tcDrawOl 3.4s linear infinite; }
.tc-mark .tc-or   { animation: tcDrawOr 3.4s linear infinite; }
.tc-mark .tc-l    { animation: tcDrawL  3.4s linear infinite; }
.tc-mark .tc-pv   { animation: tcDustV  3.4s linear infinite; }
.tc-mark .tc-po   { animation: tcDustO  3.4s linear infinite; }
.tc-mark .tc-pl   { animation: tcDustL  3.4s linear infinite; }
.tc-mark .tc-life { animation: tcLife   3.4s linear infinite; }
.tc-mark .tc-b    { animation: tcBurst  3.4s linear infinite; }
@keyframes tcDrawV  { 0%,2% { stroke-dasharray:100 100; stroke-dashoffset:100; } 17%,100% { stroke-dasharray:100 100; stroke-dashoffset:0; } }
@keyframes tcDrawOl { 0%,22% { stroke-dasharray:100 100; stroke-dashoffset:100; } 38%,100% { stroke-dasharray:100 100; stroke-dashoffset:0; } }
@keyframes tcDrawOr { 0%,22% { stroke-dasharray:100 100; stroke-dashoffset:100; } 38%,100% { stroke-dasharray:100 100; stroke-dashoffset:0; } }
@keyframes tcDrawL  { 0%,44% { stroke-dasharray:100 100; stroke-dashoffset:100; } 58%,100% { stroke-dasharray:100 100; stroke-dashoffset:0; } }
/* Sketch dust: each circle starts scattered on its own --dx/--dy and converges onto its stroke as
   that stroke resolves, then goes dark. The scatter offsets are inline per-circle custom props. */
@keyframes tcDustV { 0% { opacity:0; transform:translate(var(--dx,0px),var(--dy,0px)); } 3% { opacity:.85; } 10%,100% { opacity:0; transform:translate(0,0); } }
@keyframes tcDustO { 0%,20% { opacity:0; transform:translate(var(--dx,0px),var(--dy,0px)); } 24% { opacity:.85; } 31%,100% { opacity:0; transform:translate(0,0); } }
@keyframes tcDustL { 0%,42% { opacity:0; transform:translate(var(--dx,0px),var(--dy,0px)); } 46% { opacity:.85; } 53%,100% { opacity:0; transform:translate(0,0); } }
/* The full mark holds complete from 58% to 83%, then implodes into a compact burst of particles
   that fades, leaving a short empty beat before the sketch restarts. */
@keyframes tcLife  { 0%,83% { opacity:1; transform:none; } 95%,100% { opacity:0; transform:scale(.18); } }
@keyframes tcBurst { 0%,84% { opacity:0; transform:scale(.4); } 89% { opacity:.8; } 96%,100% { opacity:0; transform:scale(1); } }
/* Truthful lifecycle: waiting for approval freezes the mark mid-sketch (it is not working);
   a terminal card and reduced motion both fall back to the complete static mark. */
.tc-hold .tc-mark * { animation-play-state: paused !important; }
.task-card.perm .tc-mark * { animation-play-state: paused !important; }
.task-card.done .tc-mark *, .task-card.failed .tc-mark *, .task-card.cancelled .tc-mark * { animation: none !important; }
@media (prefers-reduced-motion: reduce) { .tc-mark * { animation: none !important; } }
.tc-headtext { flex:1 1 180px; min-width:0; }
/* Mode and model travel together and wrap as a pair, so neither is ever pushed under the Stop
   button or squeezed to a sliver against it. */
.tc-badges { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:6px; flex:0 1 auto; min-width:0; max-width:100%; margin-left:auto; }
.tc-badges:empty { display:none; }
.tc-title { display:flex; align-items:center; gap:7px; font-weight:600; font-size:13px; }
/* Static colour cue only now -- the Core itself carries the motion, so the old pulsing ring here
   would just be a second, redundant "loading" animation next to a shape already breathing. */
.tc-dot { width:8px; height:8px; border-radius:50%; background:var(--accent); }
/* A long-running job writes long stage strings. One clipped line hid the end of every one of them;
   two wrapped lines fit the sentence and still bound the card's height. */
/* A running turn's status obeys the same rule as the composer label: one line, ellipsis, exact
   text on the tooltip. It was a two-line clamp, which still grew the card as the string got longer. */
.tc-stage { color:var(--muted); font-size:12px; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tc-model { flex:0 1 auto; min-width:0; max-width:min(260px,100%); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; font-weight:700; letter-spacing:.3px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); color:var(--ok); text-transform:uppercase; }
.tc-model.paid { color:#1a1205; background:var(--paid-c); border-color:var(--paid-c); }
.tc-model.cloud { color:#0d0a1f; background:var(--or-c); border-color:var(--or-c); text-transform:none; }
.tc-model.cloud::before { content:''; display:inline-block; width:7px; height:7px; margin-right:5px; border-radius:2px; background:#0d0a1f; transform:rotate(45deg); vertical-align:middle; opacity:.85; }
.tc-model:empty { display:none; }
.tc-mode { flex:0 0 auto; white-space:nowrap; font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); color:var(--muted); }
.tc-mode.bypass { color:#ffd7d9; border-color:var(--bad); }
.tc-stop { flex:0 0 auto; background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:8px; padding:3px 10px; font:inherit; font-size:12px; cursor:pointer; }
.tc-stop:hover { color:var(--bad); border-color:var(--bad); }
.tc-meta { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--muted); }
.tc-meta b { color:var(--ink); font-weight:600; }
.tc-cost:empty, .tc-steps:empty { display:none; }
.tc-cost { color:var(--paid-c); font-weight:700; }
.tc-last { font-size:12px; color:var(--muted); border-top:1px dashed var(--border); padding-top:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tc-last:empty { display:none; }
.tc-recent { display:flex; flex-direction:column; gap:3px; font-size:11px; color:var(--muted); }
.tc-recent:empty { display:none; }
.tc-recent-row { display:flex; gap:6px; }
.tc-recent-row .ok { color:var(--ok); } .tc-recent-row .fail { color:var(--bad); }
.tc-actions { display:flex; gap:8px; }
.tc-view { background:transparent; color:var(--accent); border:1px solid var(--border); border-radius:8px; padding:3px 10px; font:inherit; font-size:12px; cursor:pointer; }
.tc-view:hover { border-color:var(--accent); }
.tc-summary { display:none; font-size:12px; color:var(--muted); }
.task-card.done .tc-summary, .task-card.failed .tc-summary, .task-card.cancelled .tc-summary { display:block; }
/* One response container: the status card lives INSIDE the assistant message. While running it
   shows the working head under the (streaming) answer; on a terminal state it collapses to a
   compact metadata row -- no separate "Complete" card, no duplicate answer. */
.msg.assistant .task-card { max-width:none; align-self:auto; background:transparent; border:none; border-left:none; border-radius:0; margin-top:8px; padding:8px 0 0; border-top:1px solid var(--border); gap:6px; }
.task-card.done .tc-head, .task-card.done .tc-meta, .task-card.done .tc-last,
.task-card.failed .tc-head, .task-card.failed .tc-meta, .task-card.failed .tc-last,
.task-card.cancelled .tc-head, .task-card.cancelled .tc-meta, .task-card.cancelled .tc-last { display:none; }

/* ---- Execution panel (right) ---- */
#xpanel { flex:0 0 340px; width:340px; max-width:100%; background:var(--panel); border-left:1px solid var(--border); display:flex; flex-direction:column; min-height:0; }
body:not(.panel-open) #xpanel { display:none; }
.xp-head { display:flex; align-items:center; gap:8px; padding:12px 12px 6px; }
.xp-title { font-size:12px; font-weight:700; letter-spacing:.6px; color:var(--muted); text-transform:uppercase; flex:1; }
.xp-close { background:transparent; color:var(--muted); border:none; font-size:16px; cursor:pointer; }
.xp-copy { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:7px; padding:2px 9px; font:inherit; font-size:11px; font-weight:600; cursor:pointer; }
/* Evidence scope. Global used to be the only behaviour, which mixed other projects' activity into
   the chat you were reading -- so the control states the scope rather than implying it. */
.xp-scope-row { display:flex; align-items:center; gap:6px; padding:0 12px 8px; }
.xp-scope-lbl { color:var(--muted); font-size:10px; font-weight:700; letter-spacing:.5px; text-transform:uppercase; }
/* `flex:1` made the select absorb the whole row: measured at the default 340px panel it rendered
   271px wide around a 150px longest option (121px of dead space), and at a 680px panel 611px wide
   (461px dead). A <select> already sizes itself to its widest option, so the fix is to stop
   stretching it -- `max-width:100%` still lets it fill the row when the panel is too narrow to
   hold that option, which is the only case where full width is the right answer. */
.xp-scope { flex:0 1 auto; width:auto; max-width:100%; min-width:0; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:7px; font:inherit; font-size:11.5px; padding:3px 6px; cursor:pointer; text-overflow:ellipsis; }
.xp-scope:focus { outline:none; border-color:var(--accent); }
.xp-scope-note { color:var(--muted); font-size:10.5px; padding:0 12px 6px; line-height:1.4; }
.xp-tree-actions { display:flex; flex-wrap:wrap; gap:6px; padding:0 0 8px; }
.xp-mini { background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:6px; font:inherit; font-size:10.5px; font-weight:600; padding:3px 8px; cursor:pointer; }
.xp-mini:hover { border-color:var(--accent); }
.xp-mini:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.xp-group-head { display:flex; align-items:baseline; gap:6px; margin:12px 0 4px; padding-top:8px; border-top:1px solid var(--border); }
.xp-group-name { color:var(--ink); font-size:11.5px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.xp-group-meta { color:var(--muted); font-size:10px; flex:0 0 auto; }
.xp-copy:hover { color:var(--ink); border-color:var(--accent); }
.xp-tabs { display:flex; flex-wrap:wrap; gap:2px; padding:0 8px 6px; border-bottom:1px solid var(--border); }
.xp-tab { background:transparent; color:var(--muted); border:1px solid transparent; border-radius:7px; padding:3px 8px; font:inherit; font-size:12px; cursor:pointer; }
.xp-tab:hover { color:var(--ink); }
.xp-tab.active { color:var(--accent); border-color:var(--border); background:var(--bg); }
.xp-tab .cnt { color:var(--muted); font-size:10px; }
/* Server-truth provenance strip at the top of the model popover (A11 authority lens). */
.pop-prov { display:flex; flex-direction:column; gap:2px; border:1px solid var(--border); border-radius:8px;
  padding:6px 9px; margin:2px 0 8px; background:rgba(37,99,255,.05); }
.pop-prov .pp-row { display:flex; justify-content:space-between; gap:10px; font-size:11.5px; }
.pop-prov .pp-row b { color:var(--muted); font-weight:600; flex:0 0 auto; }
.pop-prov .pp-row span { text-align:right; word-break:break-word; }
.pop-prov .pp-paid { color:#fb923c; font-weight:700; }
.pop-prov .pp-free { color:#34d399; }
.pop-prov .pp-unknown { color:#b3a284; font-weight:600; }
/* Council control-room surfaces (truthful): no animated seats, no invented workers. */
.council-live { border:1px solid var(--border); border-radius:10px; padding:10px 12px; margin-bottom:10px;
  background:linear-gradient(180deg, rgba(148,163,184,.05), transparent); }
.council-live .cl-state { font-weight:700; letter-spacing:.08em; font-size:12px; color:#b3a284; }
.council-live .cl-note { color:var(--muted); font-size:12px; margin-top:4px; line-height:1.45; }
.council-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:8px; margin-bottom:10px; }
.council-cell { border:1px solid var(--border); border-radius:9px; padding:8px 10px; min-width:0; }
.council-cell h5 { margin:0 0 5px; font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:600; }
.council-cell .cv { font-size:12px; word-break:break-all; color:var(--ink); }
.council-cell .cv.mono, .council-mono { font-family:var(--mono, ui-monospace, monospace); font-size:11px; }
.council-cell .cv.none { color:#b3a284; font-style:italic; }
.council-flow { display:flex; flex-wrap:wrap; gap:5px; margin:6px 0 10px; }
.council-flow span { font-size:10.5px; letter-spacing:.05em; border:1px solid var(--border); border-radius:999px; padding:2px 9px; color:var(--muted); }
.council-flow span.hot { color:var(--accent); border-color:var(--accent); }
.council-authority { border:1px solid rgba(251,191,36,.35); border-radius:10px; padding:8px 11px; font-size:12px; color:var(--ink); background:rgba(251,191,36,.05); }
.council-verdict { display:flex; align-items:center; gap:8px; padding:7px 10px; border:1px solid var(--border); border-radius:9px; margin-bottom:6px; }
.council-verdict .v-dot { width:9px; height:9px; border-radius:50%; flex:0 0 auto; background:var(--muted); }
.council-verdict .v-name { font-weight:600; font-size:12.5px; }
.council-verdict .v-sub { color:var(--muted); font-size:11.5px; margin-left:auto; text-align:right; }
.council-rows .xp-row .bd { min-width:0; }
/* The evidence in this panel is meant to be quoted in a bug report, so every part of it is
   selectable -- including the disclosure headings, which browsers make unselectable by default. */
.xp-body { flex:1; overflow-y:auto; padding:10px 12px; font-size:12px; user-select:text; -webkit-user-select:text; cursor:auto; }
.xp-body summary { user-select:text; -webkit-user-select:text; }
.xp-empty { color:var(--muted); padding:8px 0; }
.xp-row { display:flex; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); }
.xp-row .ic { flex:0 0 auto; width:16px; text-align:center; }
.xp-row .bd { flex:1; min-width:0; }
.xp-row .st { color:var(--muted); font-size:11px; }
.xp-row.ok .ic { color:var(--ok); } .xp-row.fail .ic { color:var(--bad); } .xp-row.run .ic { color:var(--accent); } .xp-row.wait .ic { color:var(--warn); }
.xp-row.current { background:var(--active); border-radius:6px; margin:0 -6px; padding-left:6px; padding-right:6px; }
.xp-row .mono { font-family:ui-monospace,Consolas,monospace; font-size:11px; color:var(--ink); word-break:break-all; }
.xp-note { color:var(--muted); font-size:11px; margin:6px 0; }
/* Receipts open to their full recorded evidence and can be copied out of the panel. */
.rc-item { border-bottom:1px solid var(--border); }
.rc-sum { display:flex; gap:8px; align-items:flex-start; padding:6px 0; cursor:pointer; list-style:none; }
.rc-sum::-webkit-details-marker { display:none; }
.rc-sum:hover .rc-hd { color:var(--accent); }
.rc-item.ok .ic { color:var(--ok); } .rc-item.fail .ic { color:var(--bad); } .rc-item.run .ic { color:var(--accent); }
.rc-hd { flex:1; min-width:0; }
.rc-sub { display:block; color:var(--muted); font-size:11px; }
.rc-body { padding:2px 0 10px 24px; }
.rc-f { display:grid; grid-template-columns:minmax(96px,auto) minmax(0,1fr); gap:4px 10px; padding:2px 0; }
.rc-k { color:var(--muted); font-size:11px; }
.rc-v { color:var(--ink); font-size:11.5px; word-break:break-word; }
.rc-v.mono { font-family:ui-monospace,Consolas,monospace; font-size:11px; word-break:break-all; }
.rc-actions { display:flex; gap:6px; margin-top:8px; }
.rc-btn { background:transparent; color:var(--muted); border:1px solid var(--border); border-radius:6px; padding:3px 9px; font:inherit; font-size:11px; cursor:pointer; }
.rc-btn:hover { color:var(--ink); border-color:var(--accent); }
.xp-history-title { margin:14px 0 6px; color:var(--muted); font-size:10px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; }
.xp-history-card { width:100%; text-align:left; border:1px solid var(--border); border-radius:9px; background:var(--field); color:var(--ink); padding:8px 9px; margin-bottom:6px; cursor:pointer; }
/* A history row wraps the card plus (for a failed chat) its sibling Report button; the
   card keeps its full width minus the button, and the two sit on one baseline. */
.xp-history-row { display:flex; gap:6px; align-items:stretch; margin-bottom:6px; }
.xp-history-row .xp-history-card { margin-bottom:0; flex:1 1 auto; min-width:0; }
.xp-history-row > .xp-mini { flex:0 0 auto; align-self:center; }
.br-tech { margin:6px 0; }
.br-tech summary { color:var(--muted); font-size:11px; cursor:pointer; }
.br-tech .br-pre { margin-top:4px; }
.xp-history-card:hover, .xp-history-card:focus { border-color:var(--accent); outline:none; }
.xp-history-card.current { border-color:color-mix(in srgb,var(--accent) 55%,var(--border)); }
.xp-history-card .ht { display:flex; gap:8px; align-items:center; font-size:12px; font-weight:600; }
.xp-history-card .hs { margin-left:auto; color:var(--muted); font-size:10px; font-weight:500; }
.xp-history-card .hm { margin-top:4px; color:var(--muted); font-size:10px; line-height:1.4; }
/* Nested Activity tree: work log -> category -> item -> raw detail. Native <details> for free
   keyboard/ARIA disclosure semantics; the marker is drawn manually so both themes match the
   rest of the panel's iconography instead of the browser's native triangle glyph. */
/* One section per turn, newest first. The live turn opens by default; older turns are a click away
   rather than discarded, which is what the "Current chat" scope has always claimed to show. */
.xp-turn { border:1px solid var(--border); border-radius:9px; margin-bottom:8px; background:var(--field); }
.xp-turn > summary { padding:7px 10px; cursor:pointer; font-size:12px; color:var(--ink); list-style:none; }
.xp-turn > summary::-webkit-details-marker { display:none; }
/* Literal glyphs, not CSS escapes: this stylesheet lives in a Python string, so a `\\` survives into
   the page as an escaped backslash and the marker rendered as the text "\25B8". */
.xp-turn > summary::before { content:'▸'; display:inline-block; width:12px; color:var(--muted); }
.xp-turn[open] > summary::before { content:'▾'; }
.xp-turn > summary:hover { color:var(--accent); }
.xp-turn-body { padding:0 10px 8px; }
.xp-worklog { border:1px solid var(--border); border-radius:9px; padding:8px 10px; margin-bottom:10px; background:var(--field); }
.xp-worklog > summary { cursor:pointer; font-weight:600; font-size:12px; color:var(--ink); list-style:none; }
.xp-worklog > summary::-webkit-details-marker { display:none; }
.xp-worklog > summary::before { content:'▸'; display:inline-block; width:14px; color:var(--muted); }
.xp-worklog[open] > summary::before { content:'▾'; }
.xp-worklog-body { margin-top:8px; }
.xp-cat { border-bottom:1px solid var(--border); padding:4px 0; }
.xp-cat:last-child { border-bottom:none; }
.xp-cat > summary { cursor:pointer; font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; list-style:none; padding:2px 0; }
.xp-cat > summary::-webkit-details-marker { display:none; }
.xp-cat > summary::before { content:'▸'; display:inline-block; width:14px; }
.xp-cat[open] > summary::before { content:'▾'; }
.xp-cat-count { color:var(--muted); font-weight:400; text-transform:none; letter-spacing:normal; }
.xp-cat-body { padding-left:18px; }
.xp-item { border-bottom:1px solid var(--border); padding:3px 0; }
.xp-item:last-child { border-bottom:none; }
.xp-item > summary { cursor:pointer; display:flex; gap:8px; align-items:baseline; list-style:none; font-size:12px; color:var(--ink); }
.xp-item > summary::-webkit-details-marker { display:none; }
.xp-item .ic { flex:0 0 auto; width:14px; text-align:center; }
.xp-item.ok .ic { color:var(--ok); } .xp-item.fail .ic { color:var(--bad); } .xp-item.run .ic { color:var(--accent); }
.xp-item-body { padding:4px 0 4px 22px; }
.xp-row-flat { display:flex; gap:8px; padding:3px 0; font-size:12px; }
.xp-row-flat .ic { flex:0 0 auto; width:14px; text-align:center; }
.xp-row-flat.ok .ic { color:var(--ok); } .xp-row-flat.fail .ic { color:var(--bad); } .xp-row-flat.run .ic { color:var(--accent); }
.xp-kv { margin-top:2px; }
.xp-kv-row { display:flex; gap:6px; font-size:11px; padding:1px 0; }
.xp-kv-k { flex:0 0 auto; width:64px; color:var(--muted); }
.xp-kv-v { flex:1; min-width:0; word-break:break-word; }
.xp-worklog > summary:focus-visible, .xp-cat > summary:focus-visible, .xp-item > summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
.xp-recovery { border:1px solid var(--warn); border-radius:10px; background:color-mix(in srgb,var(--warn) 9%,var(--field)); padding:9px; margin-bottom:10px; }
.xp-recovery b { display:block; font-size:12px; margin-bottom:3px; }
.xp-recovery p { color:var(--muted); font-size:11px; margin:0 0 8px; }
.xp-recovery-actions { display:flex; flex-wrap:wrap; gap:6px; }
.xp-recovery-actions button { border:1px solid var(--border); border-radius:7px; background:var(--bg); color:var(--ink); padding:4px 8px; font:inherit; font-size:11px; cursor:pointer; }

/* ---- Accessibility ---- */
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; user-select:none; -webkit-user-select:none; }
:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior:auto !important; transition-duration:.01ms !important; animation-duration:.01ms !important; animation-iteration-count:1 !important; }
}
/* Below this width the panel floats over the chat instead of displacing it. `top:0` meant it also
   floated over the app header -- so the Activity button that opens and closes it sat underneath the
   panel and could not be clicked (measured at a 700px window with a 680px stored panel width: the
   button occupied 612-679px, the panel started at 20px). Starting the panel below the header keeps
   every header control reachable at every width, and pins the drag handle to the panel's own left
   edge, which in flow order would otherwise sit under the panel too. `--app-header-h` is written
   from the measured header; the literal is the pre-script fallback. */
@media (max-width: 980px) {
  #xpanel { position:fixed; right:0; top:var(--app-header-h,54px); bottom:0; z-index:25; box-shadow:-2px 0 12px #0008; }
  /* A fixed box is out of flow, so `flex:0 0 5px` no longer sizes it and an empty div collapses
     to nothing. The width has to be stated again here or the handle becomes ungrabbable. */
  #xpResize { position:fixed; width:5px; top:var(--app-header-h,54px); bottom:0; z-index:26; }
}

  /* --- Setup line: at most one small dismissible line on a fresh profile (opens /setup) --- */
  .setup-line { display:flex; align-items:center; gap:10px; margin:0 0 8px; padding:7px 12px; border:1px solid var(--border,#333);
                border-radius:10px; background:var(--bg-elev,#1a1a1e); font-size:13px; color:var(--muted,#9a9aa2); }
  .setup-line[hidden] { display:none; }
  .setup-line-text { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .setup-line-open { background:var(--accent,#4c7df0); border:1px solid var(--accent,#4c7df0); color:#fff; border-radius:6px; padding:4px 11px; cursor:pointer; font-size:12.5px; font-weight:600; }
  .setup-line-x { background:none; border:none; color:var(--muted,#9a9aa2); cursor:pointer; font-size:16px; line-height:1; padding:2px 6px; }
  .setup-line-x:hover { color:var(--text,#eee); }
</style>
</head>
<body>
<aside id="sidebar">
  <div class="side-head">
    <span class="side-title" data-i18n="sidebar.chats">Chats</span>
    <button id="newProject" type="button" title="New project &mdash; pick a folder; chats in it work in that folder and stay isolated" data-i18n="sidebar.new_project" data-i18n-title="sidebar.new_project_title">+ Project</button>
    <button id="newChat" data-i18n="sidebar.new_chat">+ New</button>
  </div>
  <div id="sessions"></div>
  <div class="side-foot">
    <details id="homeMenu" class="home-menu">
      <summary id="homeToggle" aria-controls="homeOptions" title="Home — tools and settings" data-i18n-title="sidebar.home_title"><span aria-hidden="true">&#8962;</span> <span data-i18n="sidebar.home">Home</span> <span class="home-caret" aria-hidden="true">&#8963;</span></summary>
      <div id="homeOptions" class="home-options" role="group" aria-label="Home" data-i18n-aria-label="sidebar.home">
        <button id="pluginsBtn" type="button" class="side-foot-btn" title="Plugins &mdash; powers VOOL can use" data-i18n="sidebar.plugins" data-i18n-title="sidebar.plugins_title">&#9638; Plugins</button>
        <button id="skillsBtn" type="button" class="side-foot-btn" title="Skills &mdash; how VOOL uses those powers" data-i18n="sidebar.skills" data-i18n-title="sidebar.skills_title">&#9635; Skills</button>
        <button id="filesBtn" type="button" class="side-foot-btn" title="Files &mdash; everything VOOL generated, across all chats" data-i18n="sidebar.files" data-i18n-title="sidebar.files_title">&#128193; Files</button>
        <button id="contactsBtn" type="button" class="side-foot-btn" title="Contacts &mdash; the people and services you saved" data-i18n="sidebar.contacts" data-i18n-title="sidebar.contacts_title">&#128100; Contacts</button>
        <button id="settingsBtn" type="button" class="side-foot-btn" title="Settings" data-i18n="sidebar.settings" data-i18n-title="sidebar.settings_title">&#9881; Settings</button>
        <div class="home-support"><button id="reportBtn" type="button" class="side-foot-btn" title="Report a problem &mdash; builds a sanitized local draft you approve byte-for-byte" data-i18n="sidebar.report" data-i18n-title="sidebar.report_title">Report a problem</button></div>
      </div>
    </details>
  </div>
</aside>
<div id="sideResize" title="Drag to resize the sidebar" data-i18n-title="chat.sidebar_resize"></div>
<div id="main">
<header>
  <button id="menu" title="Toggle chats" data-i18n-title="header.menu_title">&#9776;</button>
  <span class="brand"><img src="__VOOL_LOGO_URI__" width="34" height="34" alt="" aria-hidden="true"/>VOOL</span>
  <span class="ver" id="ver"></span>
  <div class="ctrl" id="modelCtrl">
    <button type="button" class="ctrl-btn" id="modelBtn" aria-haspopup="menu" aria-expanded="false" aria-controls="modelPop" title="Which model answers" data-i18n-title="header.model_button_title"><span class="model-lane auto" id="modelLane">AUTO</span><span class="cb-lbl" id="modelLbl">VOOL Auto</span><span class="caret">&#9662;</span></button>
    <div class="popover below" id="modelPop" role="menu" aria-label="Model" data-i18n-aria-label="header.model_popover_title">
      <div class="pop-title" data-i18n="header.model_popover_title">Model</div>
      <div class="pop-prov" id="modelProv" hidden></div>
      <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-model="vool"><span class="pi-check"></span><span class="pi-body"><span data-i18n="header.model_auto_option">VOOL Auto</span> <span class="pi-hint" data-i18n="header.model_auto_hint">local-first &middot; free</span></span></button>
      <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-model="vool-local-only"><span class="pi-check"></span><span class="pi-body"><span data-i18n="header.model_local_only_option">VOOL Auto Local Only</span> <span class="pi-hint" data-i18n="header.model_local_only_hint">this machine only &middot; cloud blocked</span></span></button>
      <div class="pop-hint" data-i18n-html="header.model_auto_explainer">Starts local. If the local lane cannot serve a substantive turn and a cloud key is connected, Auto may use the <b>free fallback you choose below</b>. Auto never selects a paid model. Pick any concrete free or paid model to hard-pin that exact model instead.</div>
      <div class="pop-hint" data-i18n="header.model_local_only_explainer">Local Only routes among the models and tools on this machine and never reaches a cloud provider &mdash; not paid, not free, not for planning. When a turn needs live or current data, or a model this machine does not have, it says so instead of guessing.</div>
      <div class="pop-note" data-i18n="header.model_connect_note">Connect a provider key in Settings to choose an exact cloud model or configure Auto&rsquo;s verified-free fallback.</div>
    </div>
  </div>
  <button type="button" id="cloudPill" title="OpenRouter cloud connection — click for Settings" aria-label="Cloud connection status" data-i18n-title="header.cloud_title" data-i18n-aria-label="header.cloud_aria"><span class="dot"></span><span class="cp-txt" data-i18n="header.cloud_local_only">Local only</span></button>
  <span class="spacer"></span>
  <button id="pinBtn" type="button" title="Pinned messages in this chat" aria-label="Pinned messages" data-i18n-title="header.pins_title" data-i18n-aria-label="header.pins_aria"><span class="pin-i">&#128204;</span><span class="pin-n"></span></button>
  <button id="searchBtn" type="button" aria-pressed="false" title="Search this chat (Cmd/Ctrl+F)" aria-label="Search this chat" data-i18n="header.search" data-i18n-title="header.search_title" data-i18n-aria-label="header.search_aria">Search</button>
  <button id="exportBtn" type="button" title="Export this whole chat as TXT or Markdown — every message, not just what is on screen" aria-label="Export this chat" data-i18n="header.export" data-i18n-title="header.export_title" data-i18n-aria-label="header.export_aria">Export</button>
  <button id="panelBtn" type="button" aria-pressed="false" title="Show what VOOL did this turn — tools, files, tests, receipts" data-i18n="header.activity" data-i18n-title="header.activity_title">Activity</button>
<button id="councilBtn" type="button" title="Council — evidence, review and finality. Model output is not final truth." data-i18n="header.council" data-i18n-title="header.council_title">Council</button>
  <div class="ctrl" id="updateCtrl">
    <button type="button" id="updateChip" data-testid="update-chip" hidden aria-haspopup="menu" aria-expanded="false" aria-controls="updatePop" title="App updates" data-i18n-title="header.update_title"><span class="upd-ico">&#10227;</span><span id="updateChipTxt">Update</span></button>
    <div class="popover below" id="updatePop" data-testid="update-pop" role="menu" aria-label="Update" data-i18n-aria-label="header.update_popover_title">
      <div class="pop-title" data-i18n="header.update_popover_title">Update</div>
      <div class="upd-ver" id="updateVersion" data-testid="update-version"></div>
      <div class="upd-notes" id="updateNotes"></div>
      <div class="upd-bar" id="updateBar" hidden><i id="updateBarFill"></i></div>
      <div class="upd-msg" id="updateMessage" data-testid="update-message" role="status"></div>
      <button type="button" class="pop-item" id="updateActionBtn" data-testid="update-action" hidden></button>
      <button type="button" class="pop-item" id="updateCheckBtn" hidden data-i18n="header.update_check_again">Check again</button>
    </div>
  </div>
  <!-- No Web0 header link: there is no plugin-enabled flag to drive it (the /web0 route is served
       unconditionally), so the element was permanently hidden. /web0 and /trace are reachable from
       Settings -> Advanced, which is a link that actually works. -->
</header>
<div id="searchBar" class="search-bar" role="search" hidden>
  <input type="search" id="searchInput" class="search-field" placeholder="Search this chat — messages, answers, tool results" autocomplete="off" spellcheck="false" aria-label="Search this chat" data-i18n-placeholder="header.search_placeholder" data-i18n-aria-label="header.search_aria">
  <span id="searchCount" class="search-count" aria-live="polite"></span>
  <button type="button" id="searchPrev" class="search-btn" title="Previous match (Shift+Enter)" aria-label="Previous match" data-i18n-title="header.search_prev_title" data-i18n-aria-label="header.search_prev_aria">&#8593;</button>
  <button type="button" id="searchNext" class="search-btn" title="Next match (Enter)" aria-label="Next match" data-i18n-title="header.search_next_title" data-i18n-aria-label="header.search_next_aria">&#8595;</button>
  <button type="button" id="searchClear" class="search-btn" title="Clear search" data-i18n="header.search_clear" data-i18n-title="header.search_clear_title">Clear</button>
  <button type="button" id="searchClose" class="search-btn" title="Close search (Esc)" aria-label="Close search" data-i18n-title="header.search_close_title" data-i18n-aria-label="header.search_close_aria">&times;</button>
</div>
<div id="log" role="log" aria-live="polite" aria-label="Conversation" data-i18n-aria-label="chat.log_aria"></div>
<footer>
  <div id="setupLine" class="setup-line" role="status" hidden>
    <span class="setup-line-text" id="setupLineText" data-i18n="chat.setup_line_text">Finish setting up VOOL &mdash; four quick steps.</span>
    <button type="button" class="setup-line-open" id="setupLineOpen" data-i18n="chat.setup_line_open">Finish setup</button>
    <button type="button" class="setup-line-x" id="setupLineHide" aria-label="Don&rsquo;t remind me" title="Don&rsquo;t remind me &mdash; you can still finish setup from Settings rows" data-i18n-aria-label="chat.setup_line_hide_aria" data-i18n-title="chat.setup_line_hide_title">&times;</button>
  </div>
  <div id="queue" aria-label="Queued messages" data-i18n-aria-label="chat.queue_aria"></div>
  <div id="permBar" class="perm-bar" role="alertdialog" aria-label="Permission needed" data-i18n-aria-label="permissions.bar_aria" hidden>
    <span class="perm-ico">&#128272;</span>
    <span class="perm-copy"><span class="perm-msg" id="permMsg" data-i18n="permissions.bar_message">Permission needed</span><span class="perm-meta" id="permMeta"></span><span class="perm-detail" id="permDetail" hidden></span></span>
    <span class="perm-actions">
      <button type="button" id="permOnce" class="perm-btn" data-i18n="permissions.allow_once">Allow once</button>
      <button type="button" id="permTask" class="perm-btn primary" data-i18n="permissions.allow_task">Allow for this task</button>
      <button type="button" id="permRequest" class="perm-btn primary" title="Allow the file changes this request has already planned -- the exact files listed under Review details, and nothing else. Deletes, moves, commands, network, and payments still ask every time." data-i18n-title="permissions.allow_request_title">Allow all planned changes for this request</button>
      <button type="button" id="permChat" class="perm-btn" title="Stop asking for workspace reads and ordinary file edits in THIS chat -- reads, new files and edits inside the trusted workspace only. Deletes, overwrites, commands, network, secrets, and payments still ask every time." data-i18n="permissions.allow_chat" data-i18n-title="permissions.allow_chat_title">Allow workspace edits in this chat</button>
      <button type="button" id="permProject" class="perm-btn" title="Stop asking for reads and in-project file writes in this project. Deletes, moves, commands, network, and payments still ask every time." data-i18n="permissions.allow_project" data-i18n-title="permissions.allow_project_title">Allow in this project</button>
      <button type="button" id="permReview" class="perm-btn" data-i18n="permissions.review_details">Review details</button>
      <button type="button" id="permDeny" class="perm-btn ghost" data-i18n="permissions.deny">Deny</button>
    </span>
  </div>
  <div id="bypassBanner" class="bypass-banner" role="status" hidden><span>&#9888;</span><span id="bypassBannerText" data-i18n="bypass.banner_active">Bypass permissions is active.</span><button type="button" id="bypassRevoke" data-i18n="bypass.revoke">Revoke</button></div>
  <div class="control-bar">
    <div class="ctrl" id="modeCtrl">
      <button type="button" class="ctrl-btn" id="modeBtn" aria-haspopup="menu" aria-expanded="false" aria-controls="modePop" title="How VOOL may act" data-i18n-title="mode.button_title"><span class="cb-lbl" id="modeLbl">Manual</span> <span class="caret">&#9662;</span></button>
      <div class="popover" id="modePop" role="menu" aria-label="Mode" data-i18n-aria-label="mode.popover_title">
        <div class="pop-title" data-i18n="mode.popover_title">Mode</div>
        <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-mode="manual" data-description="Answers and safe reads run directly. File edits, commands, network actions, settings, Git, deployment, and messages require an exact approval."><span class="pi-check"></span><span class="pi-icon">&#9675;</span><span class="pi-body" data-i18n="mode.manual">Manual</span></button>
        <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-mode="review_edits" data-description="VOOL may inspect and prepare edit diffs. Each clearly identified edit batch must be approved before it is applied."><span class="pi-check"></span><span class="pi-icon">&#9998;</span><span class="pi-body" data-i18n="mode.review_edits">Review edits</span></button>
        <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-mode="plan" data-description="Strictly read-only. VOOL can inspect permitted project context and propose a plan; the controller blocks every mutation and side-effecting action."><span class="pi-check"></span><span class="pi-icon">&#8801;</span><span class="pi-body" data-i18n="mode.plan">Plan</span></button>
        <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-mode="auto" data-description="Ordinary project reads, edits, tests, and corrections may run autonomously. Destructive, external, publishing, money, credential, and security actions remain protected."><span class="pi-check"></span><span class="pi-icon">&#9655;</span><span class="pi-body" data-i18n="mode.auto">Auto</span></button>
        <button type="button" class="pop-item" role="menuitemradio" aria-checked="false" data-mode="bypass_permissions" data-description="Highest risk. Requires explicit confirmation, a limited chat or project scope, automatic expiry, and keeps receipts and hard security boundaries active."><span class="pi-check"></span><span class="pi-icon">&#9888;</span><span class="pi-body" data-i18n="mode.bypass_permissions">Bypass permissions</span></button>
      </div>
      <button type="button" class="ctrl-btn mode-info" id="modeInfo" aria-haspopup="dialog" aria-expanded="false" aria-controls="modeHelpPop" aria-label="Explain the selected mode" data-i18n-aria-label="mode.info_aria">i</button>
      <div class="popover mode-help" id="modeHelpPop" role="dialog" aria-label="Selected mode permissions" data-i18n-aria-label="mode.help_aria"><b id="modeHelpTitle">Manual</b><span id="modeHelpText"></span></div>
    </div>
    <div class="ctrl" id="attachCtrl">
      <button type="button" class="ctrl-btn" id="attachBtn" title="Attach files or photos to this message" aria-label="Attach files or photos to this message" data-i18n-title="composer.attach_title" data-i18n-aria-label="composer.attach_aria"><span class="cb-ico" aria-hidden="true">&#128206;</span> <span class="cb-lbl" data-i18n="composer.attach">Attach</span></button>
      <input type="file" id="attachInput" multiple hidden tabindex="-1" aria-hidden="true">
    </div>
    <div id="ctxBar" class="ctx-bar" aria-label="Current project and chat" data-i18n-aria-label="chat.ctx_aria"></div>
    <span class="cb-spacer"></span>
    <span class="cb-tail" aria-hidden="true"></span>
  </div>
  <div id="councilLock" class="council-lock" role="status" hidden></div>
  <div id="attachStrip" aria-label="Attachments for this message" hidden>
    <div id="attachChips" class="att-chips"></div>
    <span id="attachHint" class="att-hint"></span>
  </div>
  <div class="composer-row">
    <textarea id="input" data-i18n-placeholder="composer.placeholder" placeholder="Message VOOL &mdash; Enter to send, Shift+Enter for a new line" autofocus></textarea>
    <button id="dictateBtn" type="button" title="Dictation is temporarily unavailable in this beta build — it returns in an update" aria-label="Dictation temporarily unavailable in this beta build" aria-pressed="false" disabled><span class="mic-glyph" aria-hidden="true">&#127897;</span></button>
    <button id="voiceModeBtn" type="button" title="Voice conversation is temporarily unavailable in this beta build — it returns in an update" aria-label="Voice conversation temporarily unavailable in this beta build" aria-pressed="false" disabled><span class="mic-glyph" aria-hidden="true">&#128266;</span></button>
    <button id="send" data-i18n="composer.send">Send</button>
  </div>
  <div id="dictationNotice" hidden><div id="dictationNote" role="status" hidden></div><button id="dictationDismiss" type="button" aria-label="Dismiss speech notice permanently" title="Don't show this notice again">×</button></div>
  <div id="dictationControls" hidden>
    <label for="dictationLocale" id="dictationLocaleLabel">Recognition language</label>
    <select id="dictationLocale" aria-label="Dictation recognition language">
      <option value="en-US">English (US) &mdash; English</option>
      <option value="lt-LT">Lietuvi&#377; (Lietuva) &mdash; Lithuanian</option>
    </select>
    <button id="dictationRetry" type="button" hidden>Retry</button>
  </div>
  <button id="voiceStop" type="button" title="Stop reading aloud (Esc)" aria-label="Stop reading aloud" hidden>Stop reading &#9632;</button>
</footer>
</div>
<div id="bypassOverlay" class="modal-overlay" hidden>
  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="bypassTitle" aria-describedby="bypassWarning">
    <div class="modal-head"><span id="bypassTitle">Bypass permissions</span><button type="button" id="bypassClose" class="modal-x" aria-label="Cancel bypass">&times;</button></div>
    <div class="modal-body">
      <p id="bypassWarning" class="set-help"><b>Highest-risk mode.</b> Available tools may run without individual prompts inside the selected scope. Workspace confinement, secret redaction, operating-system security, receipts, and money consent stay enforced.</p>
      <div class="set-field"><label for="bypassScope">Scope</label><select id="bypassScope" class="set-input"><option value="task">Current task only</option><option value="session">This chat session</option><option value="project">This project</option></select></div>
      <div class="set-field"><label for="bypassDuration">Automatic expiry</label><select id="bypassDuration" class="set-input"><option value="900">15 minutes</option><option value="1800">30 minutes</option><option value="3600">1 hour</option><option value="7200">2 hours</option><option value="14400">4 hours</option><option value="28800">8 hours</option><option value="custom">Custom…</option><option value="until_off">Until I turn it off — this chat only</option></select></div>
      <div class="set-field" id="bypassCustomField" hidden><label for="bypassCustomMinutes">Custom duration (minutes, 15–1440)</label><input id="bypassCustomMinutes" class="set-input" type="number" min="15" max="1440" step="1" value="90" inputmode="numeric"></div>
      <p class="set-help">Bypass never grants access outside this workspace and cannot let a model expand its own permissions.</p>
      <div class="set-actions"><button type="button" id="bypassCancel" class="set-btn">Cancel</button><button type="button" id="bypassConfirm" class="set-btn primary">Confirm limited bypass</button></div>
    </div>
  </div>
</div>
<div id="xpResize" title="Drag to resize the panel"></div>
<aside id="xpanel" aria-label="Activity panel">
  <div class="xp-head">
    <span class="xp-title">Activity</span>
    <button type="button" id="xpCopy" class="xp-copy" title="Copy everything shown in this tab">Copy</button>
    <button id="xpClose" class="xp-close" title="Hide panel" aria-label="Hide execution panel">&times;</button>
  </div>
  <div class="xp-scope-row">
    <span class="xp-scope-lbl" id="xpScopeLbl">Scope</span>
    <select class="xp-scope" id="xpScope" aria-labelledby="xpScopeLbl" title="Which activity this panel shows"></select>
  </div>
  <div class="xp-scope-note" id="xpScopeNote"></div>
  <div class="xp-tabs" id="xpTabs" role="tablist"></div>
  <div class="xp-search-row" id="xpSearchRow" role="search">
    <input type="search" id="xpSearchInput" class="xp-search" placeholder="Filter events, tools, paths, models" autocomplete="off" spellcheck="false" aria-label="Search this Activity view">
    <span id="xpSearchCount" class="search-count" aria-live="polite"></span>
    <button type="button" id="xpSearchPrev" class="search-btn" title="Previous match" aria-label="Previous event match">&#8593;</button>
    <button type="button" id="xpSearchNext" class="search-btn" title="Next match" aria-label="Next event match">&#8595;</button>
    <button type="button" id="xpSearchClear" class="search-btn" title="Clear filter">Clear</button>
  </div>
  <div class="xp-body" id="xpBody" role="tabpanel" aria-live="polite"></div>
</aside>
<div id="pluginsOverlay" class="modal-overlay" hidden>
  <div class="modal modal-lg" role="dialog" aria-modal="true" aria-labelledby="pluginsTitle">
    <div class="modal-head">
      <span id="pluginsTitle">Plugins &amp; Skills</span>
      <button type="button" id="pluginsClose" class="modal-x" title="Close" aria-label="Close plugins">&times;</button>
    </div>
    <div class="modal-body">
      <p class="set-help">Plugins give VOOL new powers; skills teach it how to use them. This is what&rsquo;s installed on this machine.</p>
      <input type="search" id="pluginsSearch" class="plugins-search" placeholder="Search plugins and skills&hellip;" autocomplete="off" spellcheck="false" aria-label="Search plugins and skills">
      <div id="pluginsBody">Loading&hellip;</div>
    </div>
  </div>
</div>
<div id="councilOverlay" class="modal-overlay" hidden>
  <div class="modal modal-lg council-modal" role="dialog" aria-modal="true" aria-labelledby="councilTitle">
    <div class="modal-head">
      <span id="councilTitle">Council &mdash; evidence &amp; finality</span>
      <button type="button" id="councilClose" class="modal-x" title="Close" aria-label="Close council">&times;</button>
    </div>
    <div class="modal-body" id="councilBody">Loading&hellip;</div>
  </div>
</div>
<div id="filesOverlay" class="modal-overlay" hidden>
  <div class="modal modal-lg" role="dialog" aria-modal="true" aria-labelledby="filesTitle">
    <div class="modal-head">
      <span id="filesTitle">Files</span>
      <button type="button" id="filesClose" class="modal-x" title="Close" aria-label="Close files">&times;</button>
    </div>
    <div class="modal-body">
      <p class="set-help">Everything VOOL has generated on this machine &mdash; images, renders, docs &mdash; across every chat. Newest first; click a file to reveal it in Finder.</p>
      <div class="files-controls">
        <input type="search" id="filesSearch" class="plugins-search" placeholder="Search files&hellip;" autocomplete="off" spellcheck="false" aria-label="Search files">
        <select id="filesSort" class="files-sort" aria-label="Sort files">
          <option value="date">Newest</option>
          <option value="chat">By chat</option>
          <option value="name">Name</option>
          <option value="type">Type</option>
          <option value="size">Largest</option>
        </select>
      </div>
      <div id="filesBody">Loading&hellip;</div>
    </div>
  </div>
</div>
<div id="pinOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="pinTitle">
    <div class="modal-head">
      <span id="pinTitle">&#128204; Pinned in this chat</span>
      <button type="button" id="pinClose" class="modal-x" title="Close" aria-label="Close pinned">&times;</button>
    </div>
    <div class="modal-body" id="pinBody"></div>
  </div>
</div>
<div id="exportOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="exportTitle">
    <div class="modal-head">
      <span id="exportTitle">&#11015; Export this chat</span>
      <button type="button" id="exportClose" class="modal-x" title="Close" aria-label="Close export">&times;</button>
    </div>
    <div class="modal-body">
      <div class="export-note" id="exportChatName"></div>
      <div class="export-row">
        <label for="exportFormat">Format</label>
        <select id="exportFormat" aria-label="Export format">
          <option value="md">Markdown (.md) — original message source, verbatim</option>
          <option value="txt">Plain text (.txt) — original message source, verbatim</option>
          <option value="pdf">PDF (.pdf)</option>
        </select>
      </div>
      <div class="export-row">
        <input type="checkbox" id="exportTimestamps">
        <label for="exportTimestamps">Include timestamps <span class="export-note">(assistant completion times only — send times are not stored)</span></label>
      </div>
      <div class="export-row">
        <input type="checkbox" id="exportAttachments" checked>
        <label for="exportAttachments">Include attachment references <span class="export-note">(names, kinds, sizes, outcomes — never file bytes)</span></label>
      </div>
      <div class="export-preview" id="exportPreview">Counting…</div>
      <div class="export-note">Exports the whole conversation from the server-side transcript — every message, not just what is on screen, with no hidden cut-off. Not included: model reasoning, tool/activity traces, and anything the store does not persist.</div>
      <div class="export-status" id="exportStatus" role="status"></div>
      <div class="export-actions">
        <button type="button" id="exportCancel" class="set-btn">Cancel</button>
        <button type="button" id="exportGo" class="set-btn" style="color:var(--accent);border-color:var(--accent)">Export</button>
      </div>
    </div>
  </div>
</div>
<div id="bugReportOverlay" class="modal-overlay" hidden>
  <div class="modal modal-lg" role="dialog" aria-modal="true" aria-labelledby="bugReportTitle">
    <div class="modal-head">
      <span id="bugReportTitle">&#128172; Report a problem</span>
      <button type="button" id="bugReportClose" class="modal-x" title="Close" aria-label="Close bug report">&times;</button>
    </div>
    <div class="modal-body" id="bugReportBody"></div>
  </div>
</div>
<div id="settingsFrameOverlay" class="modal-overlay" hidden>
  <div class="modal modal-lg settings-frame-modal" role="dialog" aria-modal="true" aria-label="VOOL Settings">
    <iframe id="settingsFrame" title="VOOL Settings" src="about:blank"></iframe>
  </div>
</div>
<div id="settingsOverlay" class="modal-overlay" hidden>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
    <div class="modal-head">
      <span id="settingsTitle">Settings</span>
      <button type="button" id="settingsClose" class="modal-x" title="Close" aria-label="Close settings">&times;</button>
    </div>
    <div class="modal-body">
      <section class="set-sec" id="profileSection">
        <h3>What VOOL remembers about you <span class="set-sub">learned from conversation; yours to change</span></h3>
        <div class="set-row" style="gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <button type="button" id="profilePause" class="set-btn">Pause memory</button>
          <button type="button" id="profileExport" class="set-btn">Export</button>
          <span id="profileStatus" class="set-status" role="status"></span>
        </div>
        <div id="profileList" class="profile-list" aria-live="polite"></div>
        <p class="set-help">Each item shows its value, scope, origin and last use. Edit, Forget, Move scope and Restore previous act on one item; a candidate from chat waits here until you Save it. Nothing remembered here grants VOOL permission to read, send, post or spend.</p>
      </section>
      <section class="set-sec" id="sessionBundleSection">
        <h3>Export &amp; import a chat <span class="set-sub">portable, signed session bundle</span></h3>
        <p class="set-help">A <code>.voolsession</code> file carries this conversation&rsquo;s turns, receipts and evidence references, signed by this machine&rsquo;s key. Encryption is recommended for any bundle leaving this computer; a bundle signed by an unknown key is refused unless you explicitly accept it, and stays marked untrusted. Model-internal reasoning is not part of a bundle. Passphrases exist only in these fields &mdash; never in history, logs or receipts.</p>
        <div class="set-row" style="gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <label class="set-check-row"><input type="checkbox" id="sbEncrypt" class="set-check" checked> Encrypt with passphrase (recommended)</label>
        </div>
        <div class="set-row" id="sbPassFields" style="gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <input type="password" id="sbPass" class="set-input" placeholder="Passphrase" autocomplete="new-password" spellcheck="false" style="max-width:180px" aria-label="Bundle passphrase">
          <input type="password" id="sbPass2" class="set-input" placeholder="Repeat passphrase" autocomplete="new-password" spellcheck="false" style="max-width:180px" aria-label="Repeat bundle passphrase">
        </div>
        <label class="set-check-row" id="sbPlainWarnRow" hidden><input type="checkbox" id="sbPlainWarn" class="set-check"> I understand an unencrypted bundle can be read by anyone who obtains the file.</label>
        <div class="set-row" style="gap:8px;flex-wrap:wrap;margin:8px 0">
          <button type="button" id="sbExportBtn" class="set-btn primary">Export this chat&hellip;</button>
          <span id="sbExportStatus" class="set-status" role="status"></span>
        </div>
        <div id="sbExportPreview" class="sb-preview" hidden></div>
        <hr class="set-divider">
        <div class="set-row" style="gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <input type="file" id="sbImportFile" accept=".voolsession" aria-label="Bundle file">
          <input type="password" id="sbImportPass" class="set-input" placeholder="Passphrase (if encrypted)" autocomplete="off" spellcheck="false" style="max-width:190px" aria-label="Import passphrase">
          <button type="button" id="sbPreviewBtn" class="set-btn">Preview import</button>
        </div>
        <div id="sbPreviewBox" class="sb-preview" hidden></div>
        <label class="set-check-row" id="sbForeignRow" hidden><input type="checkbox" id="sbConfirmForeign" class="set-check"> This bundle is signed by an UNKNOWN key &mdash; import it anyway as untrusted.</label>
        <div class="set-row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
          <button type="button" id="sbImportBtn" class="set-btn primary" disabled>Import</button>
          <button type="button" id="sbOpenRestored" class="set-btn" hidden>Open restored chat</button>
          <span id="sbImportStatus" class="set-status" role="status"></span>
        </div>
      </section>
      <section class="set-sec">
        <h3>Behaviour <span class="set-sub">how VOOL talks &amp; acts</span></h3>
        <div class="set-field"><label for="setHumor">Humour <span id="setHumorVal" class="set-num">20%</span></label><input type="range" id="setHumor" min="0" max="100" step="5" class="set-range"></div>
        <div class="set-field row"><label for="setCommStyle">Talk style</label><select id="setCommStyle" class="set-input" style="max-width:200px"><option value="casual">Casual</option><option value="business">Business</option><option value="cheeky">Cheeky</option></select></div>
        <div class="set-field row"><label for="setAutonomy">Autonomy</label><select id="setAutonomy" class="set-input" style="max-width:220px"><option value="hands_off">Hands-off — reads run unprompted, actions ask</option><option value="balanced">Balanced</option><option value="strict">Strict</option></select></div>
        <div class="set-field row"><label for="setDeepReason">Deep reasoning <span class="set-sub2">slower, thinks first</span></label><input type="checkbox" id="setDeepReason" class="set-check"></div>
      </section>
      <section class="set-sec">
        <h3>Limits <span class="set-sub">power-user caps</span></h3>
        <div class="set-field"><label for="setReserve">Keep free for me <span id="setReserveVal" class="set-num">20%</span> <span class="set-sub2">RAM heavy tasks won&rsquo;t touch</span></label><input type="range" id="setReserve" min="10" max="80" step="5" class="set-range"></div>
        <div class="set-field row"><label for="setTokenBudget">Daily cloud token budget <span class="set-sub2">0 = unlimited</span></label><input type="number" id="setTokenBudget" class="set-input" min="0" step="1000" style="max-width:160px" placeholder="0"></div>
        <p class="set-help">The RAM reserve is enforced live by the resource governor (a bigger reserve means VOOL leaves more for you). The token budget is a guide today &mdash; nothing enforces it yet and the Usage panel below does not chart against it. A hard daily cut-off is coming next.</p>
      </section>
      <div class="set-row" style="margin-bottom:14px">
        <button type="button" id="setPrefsSave" class="set-btn primary">Save settings</button>
        <span id="setPrefsStatus" class="set-status"></span>
      </div>
      <section class="set-sec">
        <h3>Cloud API key <span class="set-sub">bring your own key</span></h3>
        <p class="set-help">VOOL runs local and free by default. Store a provider key &mdash; OpenAI, Anthropic (Claude), Kimi, Groq, Gemini, DeepSeek, OpenRouter, or a custom OpenAI-compatible endpoint &mdash; encrypted on this machine, and test that the provider accepts it. A concrete model you select is a hard pin. OpenRouter <code>:free</code> models cost nothing; paid models spend your provider credits only when you explicitly select one, and remain subject to VOOL&rsquo;s spend caps. VOOL Auto never selects paid.</p>
        <div class="set-row key-row">
          <select id="orProvider" class="set-input" aria-label="Cloud provider" style="flex:0 0 auto;max-width:180px">
            <option value="">Auto-detect</option>
          </select>
          <span class="key-field">
            <input type="password" id="orKey" class="set-input" placeholder="Paste API key" autocomplete="off" spellcheck="false" aria-label="Cloud provider API key">
            <button type="button" id="orReveal" class="key-reveal" aria-pressed="false" title="Show or hide the key you are typing">Show</button>
          </span>
          <button type="button" id="orSave" class="set-btn primary">Save</button>
          <button type="button" id="orTest" class="set-btn" hidden>Test connection</button>
          <button type="button" id="orRemove" class="set-btn" hidden>Remove</button>
        </div>
        <input type="text" id="orBaseUrl" class="set-input" placeholder="your-endpoint/v1  (custom OpenAI-compatible base URL, must be secure)" hidden style="margin-top:6px">
        <div id="orSavedBanner" class="save-banner" role="status"><span class="sb-ico">&#10003;</span><span id="orSavedText">Key saved and encrypted on this machine.</span></div>
        <div id="orStatus" class="set-status"></div>
        <div id="keysList" class="keys-list" aria-live="polite"></div>
      </section>
      <section class="set-sec">
        <h3>Web search key <span class="set-sub">bring your own key</span></h3>
        <p class="set-help">Paste a search API key &mdash; VOOL recognises which service it belongs to and starts using it for live lookups straight away. Keys are stored encrypted on this machine and are never sent anywhere except to that search provider. With no key VOOL keeps using its built-in keyless search; a key simply makes live answers more reliable. <span id="wsSignup"></span></p>
        <div class="set-row key-row">
          <select id="wsProvider" class="set-input" aria-label="Search provider" style="flex:0 0 auto;max-width:180px">
            <option value="">Auto-detect</option>
          </select>
          <span class="key-field">
            <input type="password" id="wsKey" class="set-input" placeholder="Paste search API key" autocomplete="off" spellcheck="false" aria-label="Web search API key">
            <button type="button" id="wsReveal" class="key-reveal" aria-pressed="false" title="Show or hide the key you are typing">Show</button>
          </span>
          <button type="button" id="wsSave" class="set-btn primary">Save</button>
          <button type="button" id="wsTest" class="set-btn" hidden>Test search</button>
          <button type="button" id="wsRemove" class="set-btn" hidden>Remove</button>
        </div>
        <div id="wsSavedBanner" class="save-banner" role="status"><span class="sb-ico">&#10003;</span><span id="wsSavedText">Key saved and encrypted on this machine.</span></div>
        <div id="wsStatus" class="set-status"></div>
        <div id="wsList" class="keys-list" aria-live="polite"></div>
      </section>
      <section class="set-sec">
        <h3>Token usage <span class="set-sub">local free vs cloud paid</span></h3>
        <p class="set-help">How many tokens ran free on your local model vs a paid cloud lane, per model. Counts served responses on this machine.</p>
        <div class="usage-tabs" id="usageTabs" role="tablist">
          <button type="button" class="usage-tab" data-range="today" role="tab">Today</button>
          <button type="button" class="usage-tab" data-range="week" role="tab">Week</button>
          <button type="button" class="usage-tab" data-range="month" role="tab">Month</button>
          <button type="button" class="usage-tab" data-range="all" role="tab">All time</button>
        </div>
        <div id="usageBody" class="usage-body" aria-live="polite"></div>
      </section>
      <section class="set-sec">
        <h3>About this build <span class="set-sub">exact running version</span></h3>
        <p class="set-help">The precise build answering you right now. Quote this when reporting a problem &mdash; a short commit alone does not identify a build.</p>
        <dl id="buildInfo" class="build-info" aria-live="polite"></dl>
        <div class="set-row" style="margin-top:10px">
          <button type="button" id="buildCopy" class="set-btn">Copy build info</button>
          <span id="buildCopyStatus" class="set-status"></span>
        </div>
        <div class="set-row" style="margin-top:6px">
          <button type="button" id="reportSettingsBtn" class="set-btn">Report a problem</button>
          <span class="set-help">Sanitized draft, exact preview, your approval before anything is sent.</span>
        </div>
      </section>
    </div>
  </div>
</div>
<script>
// The sidebar's native disclosure groups existing actions without changing their handlers.
(function wireHomeMenu() {
  const menu = document.getElementById('homeMenu');
  const toggle = document.getElementById('homeToggle');
  const options = document.getElementById('homeOptions');
  const close = (restoreFocus) => {
    menu.open = false;
    if (restoreFocus) toggle.focus();
  };
  options.addEventListener('click', (event) => {
    if (event.target.closest('button')) close(true);
  }, true);
  document.addEventListener('pointerdown', (event) => {
    if (menu.open && !menu.contains(event.target)) close(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menu.open) {
      event.preventDefault();
      close(true);
    }
  });
  menu.addEventListener('focusout', (event) => {
    if (menu.open && event.relatedTarget && !menu.contains(event.relatedTarget)) close(false);
  });
})();
const logEl = document.getElementById('log');
const inputEl = document.getElementById('input');
const sendEl = document.getElementById('send');
// Catalog lookup for the composer's own words: the deterministic i18n bundle resolves the
// key when the UI locale carries it; otherwise the inline English is the honest fallback
// (and a label never blanks because a catalog is missing).
// The app UI locale as a BCP-47 tag for Intl formatting (dates, grouped numbers). Undefined
// falls back to the host locale — the historical behavior — so a page without a bundle is
// unchanged. Model ANSWER language is deliberately not consulted here.
function appLocale() {
  try { return (window.VOOL_I18N && window.VOOL_I18N.tag) || undefined; } catch (e) { return undefined; }
}
function pageT(key, fallback) {
  try {
    if (typeof VOOLT === 'function') {
      const t = VOOLT(key);
      if (t && t !== key) return t;
    }
  } catch (e) {}
  return fallback;
}
// The {param}-carrying twin: the bundle's own formatter resolves the locale's
// placeholders AND its ICU-lite plurals; the inline English fallback formats identically
// when no bundle ships (plural fallbacks need the same resolver, not a flat replace).
function pageTF(key, fallback, params) {
  const t = pageT(key, fallback);
  try {
    if (typeof VOOLFMT === 'function') return VOOLFMT(t, params || {});
  } catch (e) {}
  return String(t).replace(/\{(\w+)\}/g, (_, k) => ((params && params[k] != null) ? params[k] : ''));
}
const queueEl = document.getElementById('queue');
const verEl = document.getElementById('ver');
const sessionsEl = document.getElementById('sessions');
const newChatEl = document.getElementById('newChat');
const menuEl = document.getElementById('menu');
const panelBtnEl = document.getElementById('panelBtn');
const searchBtnEl = document.getElementById('searchBtn');
const searchBarEl = document.getElementById('searchBar');
const searchInputEl = document.getElementById('searchInput');
const searchCountEl = document.getElementById('searchCount');
const searchPrevEl = document.getElementById('searchPrev');
const searchNextEl = document.getElementById('searchNext');
const xpCloseEl = document.getElementById('xpClose');
const xpTabsEl = document.getElementById('xpTabs');
const xpBodyEl = document.getElementById('xpBody');
// The transcript is NOT global -- it lives in each chat's own bucket (`view.history` for the chat
// on screen, `ownerOf(run).history` for a running turn). See the DISPATCHER block below.
// Navigation guard. Phase 1 deliberately keeps this a single flag: the per-chat ownership model
// below is what has to be proven first, and the guards it protects come down in phase 2.

// ---- Search: the chat transcript, and the Activity panel ----
// Two deliberately different engines. The transcript is prose a person half-remembers, so it is
// matched tolerantly; the Activity panel is machine output -- event names, tool ids, paths, model
// tags -- where a fuzzy match would be a liability, so it stays literal.
//
// Nothing here sends a turn, calls a model, runs a tool or writes a file. It reads the DOM the page
// has already rendered and wraps matches in <mark>. `dataset.raw` is never touched, so Copy still
// yields the original text of a highlighted message.
const SEARCH_MIN_CHARS = 2;                 // a single letter matches nearly every message
const SEARCH_MAX_WORDS_SCANNED = 4000;      // per message, so one pasted log cannot stall a keystroke
const SEARCH_DEBOUNCE_MS = 120;
// Edit distance allowed per query word, by its length. Short words get none: at length four a
// single edit reaches cool/wool/tool/pool from "vool", which is a false positive, not tolerance.
// "xplanation" (10) reaches "explanation" at distance 1, well inside the long-word budget.
function searchMaxDistance(term) {
  if (term.length <= 4) return 0;
  if (term.length <= 7) return 1;
  return 2;
}
// Banded Levenshtein with an early exit: rows outside the diagonal band cannot come back under the
// budget, and a row whose best cell already exceeds it ends the comparison.
function withinEditDistance(a, b, max) {
  if (a === b) return true;
  if (max <= 0) return false;
  if (Math.abs(a.length - b.length) > max) return false;
  let prev = new Array(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    const cur = new Array(b.length + 1);
    cur[0] = i;
    let best = cur[0];
    const from = Math.max(1, i - max), to = Math.min(b.length, i + max);
    for (let j = 1; j <= b.length; j++) {
      if (j < from || j > to) { cur[j] = max + 1; continue; }
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (cur[j] < best) best = cur[j];
    }
    if (best > max) return false;
    prev = cur;
  }
  return prev[b.length] <= max;
}
function searchTerms(query) {
  return String(query == null ? '' : query).toLowerCase().split(/\s+/).filter(Boolean);
}
// Words keep the punctuation that belongs inside an identifier, so exact_one_file_6a73.txt and
// workspace.write_file survive as single words instead of shattering into fragments.
const SEARCH_WORD_RE = /[a-z0-9][a-z0-9._:\/\\-]*/g;
// Returns the literal strings to highlight for this haystack, or null when a term does not match at
// all. Every term must match: "vool explanation" means both, not either.
function matchTermsIn(haystack, terms, exactOnly) {
  const needles = [];
  for (let i = 0; i < terms.length; i++) {
    const term = terms[i];
    if (haystack.indexOf(term) !== -1) { needles.push(term); continue; }
    // A term the transcript contains literally SOMEWHERE is matched literally everywhere. Without
    // this, "marker399" -- present in exactly one of 400 turns -- also fuzz-matched marker397,
    // marker39 and 165 others, because a nine-character term carries a two-edit budget. Tolerance is
    // for words the transcript does not have; it is not licence to blur a word it does.
    if (exactOnly && exactOnly[i]) return null;
    const budget = searchMaxDistance(term);
    if (!budget) return null;
    let found = null, scanned = 0, m;
    SEARCH_WORD_RE.lastIndex = 0;
    while ((m = SEARCH_WORD_RE.exec(haystack)) !== null) {
      if (++scanned > SEARCH_MAX_WORDS_SCANNED) break;
      const word = m[0];
      if (Math.abs(word.length - term.length) > budget) continue;
      if (withinEditDistance(term, word, budget)) { found = word; break; }
    }
    if (!found) return null;
    needles.push(found);
  }
  return needles;
}
// Wrap every occurrence of `needles` in <mark>, walking TEXT NODES only. Replacing innerHTML would
// re-parse the message and drop the Copy/Pin handlers already bound inside it.
function highlightWithin(root, needles) {
  const wanted = [...new Set(needles)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!root || !wanted.length) return 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const targets = [];
  let node;
  while ((node = walker.nextNode()) !== null) {
    if (node.nodeValue && node.nodeValue.trim()) targets.push(node);
  }
  let made = 0;
  for (const textNode of targets) {
    const value = textNode.nodeValue, lower = value.toLowerCase();
    const spans = [];
    for (const needle of wanted) {
      let from = 0, at;
      while ((at = lower.indexOf(needle, from)) !== -1) { spans.push([at, at + needle.length]); from = at + needle.length; }
    }
    if (!spans.length) continue;
    spans.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const span of spans) {
      const last = merged[merged.length - 1];
      if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
      else merged.push([span[0], span[1]]);
    }
    const frag = document.createDocumentFragment();
    let cursor = 0;
    for (const [start, end] of merged) {
      if (start > cursor) frag.appendChild(document.createTextNode(value.slice(cursor, start)));
      const mark = document.createElement('mark');
      mark.className = 'cs-hit';
      mark.textContent = value.slice(start, end);
      frag.appendChild(mark);
      made++;
      cursor = end;
    }
    if (cursor < value.length) frag.appendChild(document.createTextNode(value.slice(cursor)));
    if (textNode.parentNode) textNode.parentNode.replaceChild(frag, textNode);
  }
  return made;
}
function stripHighlights(root) {
  if (!root) return;
  root.querySelectorAll('mark.cs-hit').forEach((mark) => {
    const parent = mark.parentNode;
    if (!parent) return;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  });
}

// ---- Chat transcript search ----
let chatSearchHits = [], chatSearchIndex = -1, chatSearchTimer = null;
function chatSearchOpen() { return !!(searchBarEl && !searchBarEl.hidden); }
function clearChatSearchPaint() {
  if (!logEl) return;
  stripHighlights(logEl);
  logEl.querySelectorAll('.cs-current-turn').forEach((el) => el.classList.remove('cs-current-turn'));
  logEl.querySelectorAll('mark.cs-active').forEach((el) => el.classList.remove('cs-active'));
  chatSearchHits = [];
  chatSearchIndex = -1;
}
function paintChatSearchCount(matched, query) {
  if (!searchCountEl) return;
  if (!query) { searchCountEl.textContent = ''; searchCountEl.className = 'search-count'; }
  else if (query.length < SEARCH_MIN_CHARS) {
    searchCountEl.textContent = 'type ' + SEARCH_MIN_CHARS + '+ characters';
    searchCountEl.className = 'search-count';
  } else if (!matched) {
    searchCountEl.textContent = 'no matches';
    searchCountEl.className = 'search-count none';
  } else {
    searchCountEl.textContent = (chatSearchIndex + 1) + ' / ' + matched;
    searchCountEl.className = 'search-count';
  }
  const idle = !matched;
  if (searchPrevEl) searchPrevEl.disabled = idle;
  if (searchNextEl) searchNextEl.disabled = idle;
}
function runChatSearch(keepIndex) {
  if (!logEl || !searchInputEl) return 0;
  const previous = keepIndex ? chatSearchIndex : -1;
  clearChatSearchPaint();
  const query = searchInputEl.value.trim();
  if (query.length < SEARCH_MIN_CHARS) { paintChatSearchCount(0, query); return 0; }
  const terms = searchTerms(query);
  if (!terms.length) { paintChatSearchCount(0, query); return 0; }
  const messages = [...logEl.querySelectorAll('.msg')];
  // What the reader sees, which already includes any tool/action result text the turn's status card
  // rendered into the bubble. Read once per turn, so a keystroke costs one pass, not one per term.
  const hays = messages.map((msg) => (msg.innerText || msg.textContent || '').toLowerCase());
  const exactOnly = terms.map((term) => hays.some((hay) => hay.indexOf(term) !== -1));
  for (let i = 0; i < messages.length; i++) {
    const hay = hays[i];
    if (!hay) continue;
    const needles = matchTermsIn(hay, terms, exactOnly);
    if (!needles) continue;
    highlightWithin(messages[i], needles);
    chatSearchHits.push(messages[i]);
  }
  if (chatSearchHits.length) {
    chatSearchIndex = (previous >= 0 && previous < chatSearchHits.length) ? previous : 0;
    focusChatHit(chatSearchIndex, !keepIndex);
  }
  paintChatSearchCount(chatSearchHits.length, query);
  return chatSearchHits.length;
}
function focusChatHit(index, scroll) {
  if (!chatSearchHits.length) return;
  chatSearchIndex = (index + chatSearchHits.length) % chatSearchHits.length;
  logEl.querySelectorAll('.cs-current-turn').forEach((el) => el.classList.remove('cs-current-turn'));
  logEl.querySelectorAll('mark.cs-active').forEach((el) => el.classList.remove('cs-active'));
  const turn = chatSearchHits[chatSearchIndex];
  turn.classList.add('cs-current-turn');
  const firstMark = turn.querySelector('mark.cs-hit');
  if (firstMark) firstMark.classList.add('cs-active');
  if (scroll !== false) (firstMark || turn).scrollIntoView({ block: 'center' });
  paintChatSearchCount(chatSearchHits.length, searchInputEl ? searchInputEl.value.trim() : '');
}
function stepChatSearch(delta) {
  if (!chatSearchHits.length) return;
  focusChatHit(chatSearchIndex + delta, true);
}
function openChatSearch() {
  if (!searchBarEl) return;
  searchBarEl.hidden = false;
  if (searchBtnEl) { searchBtnEl.classList.add('on'); searchBtnEl.setAttribute('aria-pressed', 'true'); }
  if (searchInputEl) { searchInputEl.focus(); searchInputEl.select(); }
  if (searchInputEl && searchInputEl.value.trim()) runChatSearch(true);
}
function closeChatSearch() {
  if (!searchBarEl) return;
  clearChatSearchPaint();
  searchBarEl.hidden = true;
  if (searchBtnEl) { searchBtnEl.classList.remove('on'); searchBtnEl.setAttribute('aria-pressed', 'false'); }
  paintChatSearchCount(0, '');
  if (inputEl) inputEl.focus();
}
function clearChatSearch() {
  if (searchInputEl) { searchInputEl.value = ''; searchInputEl.focus(); }
  clearChatSearchPaint();
  paintChatSearchCount(0, '');
}

// ---- Activity search (literal) ----
let activityHits = [], activityIndex = -1;
function activitySearchQuery() {
  const el = document.getElementById('xpSearchInput');
  return el ? el.value.trim().toLowerCase() : '';
}
function paintActivityCount(matched, query) {
  const countEl = document.getElementById('xpSearchCount');
  const prev = document.getElementById('xpSearchPrev'), next = document.getElementById('xpSearchNext');
  if (countEl) {
    if (!query) { countEl.textContent = ''; countEl.className = 'search-count'; }
    else if (query.length < SEARCH_MIN_CHARS) { countEl.textContent = SEARCH_MIN_CHARS + '+ chars'; countEl.className = 'search-count'; }
    else if (!matched) { countEl.textContent = 'none'; countEl.className = 'search-count none'; }
    else { countEl.textContent = (activityIndex + 1) + ' / ' + matched; countEl.className = 'search-count'; }
  }
  if (prev) prev.disabled = !matched;
  if (next) next.disabled = !matched;
}
const ACTIVITY_ROW_SELECTOR = '.xp-row, .xp-row-flat, .xp-kv-row, .rc-item, .xp-history-card, .xp-item, .xp-cat, .xp-note';
function clearActivityPaint() {
  if (!xpBodyEl) return;
  stripHighlights(xpBodyEl);
  xpBodyEl.querySelectorAll('.as-hit').forEach((el) => el.classList.remove('as-hit'));
  xpBodyEl.querySelectorAll('.as-current').forEach((el) => el.classList.remove('as-current'));
  activityHits = [];
  activityIndex = -1;
}
// Literal and case-insensitive: an event name, tool id, path, model tag or status is machine output
// that the operator is quoting back exactly, and a near miss there would be worse than no match.
function runActivitySearch(keepIndex) {
  if (!xpBodyEl) return 0;
  const previous = keepIndex ? activityIndex : -1;
  clearActivityPaint();
  const query = activitySearchQuery();
  if (query.length < SEARCH_MIN_CHARS) { paintActivityCount(0, query); return 0; }
  const made = highlightWithin(xpBodyEl, [query]);
  if (made) {
    const seen = new Set();
    xpBodyEl.querySelectorAll('mark.cs-hit').forEach((mark) => {
      const row = mark.closest(ACTIVITY_ROW_SELECTOR) || mark.parentElement;
      if (!row || seen.has(row)) return;
      seen.add(row);
      row.classList.add('as-hit');
      activityHits.push(row);
    });
  }
  if (activityHits.length) {
    activityIndex = (previous >= 0 && previous < activityHits.length) ? previous : 0;
    focusActivityHit(activityIndex, !keepIndex);
  }
  paintActivityCount(activityHits.length, query);
  return activityHits.length;
}
function focusActivityHit(index, scroll) {
  if (!activityHits.length) return;
  activityIndex = (index + activityHits.length) % activityHits.length;
  xpBodyEl.querySelectorAll('.as-current').forEach((el) => el.classList.remove('as-current'));
  const row = activityHits[activityIndex];
  row.classList.add('as-current');
  if (scroll !== false) row.scrollIntoView({ block: 'center' });
  paintActivityCount(activityHits.length, activitySearchQuery());
}
function stepActivitySearch(delta) {
  if (!activityHits.length) return;
  focusActivityHit(activityIndex + delta, true);
}
function clearActivitySearch() {
  const el = document.getElementById('xpSearchInput');
  if (el) { el.value = ''; el.focus(); }
  clearActivityPaint();
  paintActivityCount(0, '');
}
// The panel rebuilds its body on every render, which throws away the marks. Re-applying here keeps
// an active filter true after new events land.
function reapplyActivitySearch() {
  if (activitySearchQuery().length >= SEARCH_MIN_CHARS) runActivitySearch(true);
}

// ---- Composer controls (mode / model) ----
// Modes are enforced by the server-side permission controller. The browser stores one ordinary
// mode preference per chat only so a chat reopens consistently; the server remains authoritative.
// Bypass is deliberately excluded from durable browser preferences and always expires server-side.
const MODE_LABELS = { manual: pageT('mode.js.manual', 'Manual'), review_edits: pageT('mode.js.review_edits', 'Review edits'), plan: pageT('mode.js.plan', 'Plan'), auto: pageT('mode.js.auto', 'Auto'), bypass_permissions: pageT('mode.js.bypass_permissions', 'Bypass permissions') };
const MODEL_LABELS = { 'vool': 'VOOL Auto', 'vool-local-only': 'VOOL Auto Local Only' };
// The Local Only lane, by the one value the server recognises. Kept as a named constant because
// several checks below have to agree about it exactly: the cloud list is suppressed under it, the
// no-key revert must not treat it as a stale cloud pin, and the footer disclosure keys off it.
const LOCAL_ONLY_MODEL = 'vool-local-only';
function isLocalOnlyMode() { return modelValue === LOCAL_ONLY_MODEL; }
// A concrete cloud model id chosen from the dropdown: a bare id (gpt-4.1-mini, what the direct
// providers use), a vendor/name[:tag] slug, or either with an optional provider: prefix. Friendly
// labels are filled in as the catalog loads; the raw id is the safe fallback. NOTE: this matches
// bare ids, so any check that must EXCLUDE the local tiers pairs it with !MODEL_LABELS[modelValue].
const CLOUD_MODEL_ID_RE = /^(?:[a-z0-9_-]+:)?[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)?(?::[A-Za-z0-9._-]+)?$/;
const CLOUD_MODEL_LABELS = {};
// Id -> catalog row for every row served by /api/cloud/models this session. The
// send-time paid-pin guard reads the SAME server catalog verdicts, never its own guess.
const CLOUD_CATALOG_BY_ID = {};
// The active mode is per chat (`view.mode` / `ownerOf(run).mode`), not global -- a turn is sent
// under the mode of ITS chat, never under whatever the composer happens to show.
let modelValue = localStorage.getItem('vool_model') || 'vool';
const modelSelectionRevisions = Object.create(null);
// Keep a known tier OR a well-formed cloud id; drop anything else that was persisted.
if (!MODEL_LABELS[modelValue] && !CLOUD_MODEL_ID_RE.test(modelValue)) modelValue = 'vool';
// The installed local models, as last reported by the runtime's own /api/tags inventory. Persisted
// because it has to be readable SYNCHRONOUSLY at boot: `CLOUD_MODEL_ID_RE` matches a bare tag like
// `qwen3:8b`, so without this a pinned local model looks like a cloud id to the no-key revert in
// setCloudConnected() and would be silently reset to Auto before the inventory fetch returned.
let localModelIds = new Set();
try { localModelIds = new Set(JSON.parse(localStorage.getItem('vool_local_models') || '[]')); } catch (e) {}
function isLocalModel(value) { return localModelIds.has(String(value || '')); }

// ---- Session model stickiness ------------------------------------------------------------------
// Once "VOOL Auto" lands on a FREE cloud model, keep THIS chat on it for substantive turns — one
// consistent voice, no local/cloud ping-pong. Trivial turns still go local (fast). This reuses the
// exact requested-model rail as manual pinning, and only ever re-sends a FREE cloud model, so it can
// never trigger paid spend and needs no change to the routing core. Resets when you switch chats.
// Stickiness is per chat: it is stored in the owning chat's bucket, so a model that stuck in one
// chat can never decide another chat's turn.
function rememberStickyModel(chatId, m) {
  if (m && m.lane === 'cloud' && !m.paid && m.model_id) {
    chatState(chatId).stickyModel = String(m.model_id);
    if (isDisplayed(chatId)) reflectModel();
  }
}
function isTrivialTurn(t) {
  const s = String(t || '').trim();
  return s.length <= 15 && s.split(/\s+/).filter(Boolean).length <= 2 && !s.includes('?');
}
// MODEL RADAR try-once: a one-turn trial of a radar-suggested model. The grant lives only
// here, only for the chat it was armed in, and is consumed by exactly the next substantive
// turn -- no persistence anywhere, so the trial cannot silently become the default. It may
// serve its one turn even under an explicit pin (the click IS operator intent), but the pin
// itself is never touched and everything after the trial reverts to it.
let tryOnceGrant = null; // {chatId, model, label, consumed:false}
function armTryOnce(chatId, model, label) {
  if (!chatId || !model) return false;
  // One-turn trials may serve a turn even under an explicit pin (the click IS operator
  // intent), but they never REPLACE it: the grant dies with its one turn and the pin
  // is untouched. The send-time paid-ack gate still evaluates the trial model itself.
  tryOnceGrant = { chatId: String(chatId), model: String(model), label: String(label || model), consumed: false };
  return true;
}
function activeTryOnce(chatId, msgText) {
  if (!tryOnceGrant || tryOnceGrant.consumed) return null;
  if (tryOnceGrant.chatId !== String(chatId)) return null; // a grant never crosses chats
  if (isTrivialTurn(msgText)) return null;                 // the trial rides a real turn
  return tryOnceGrant;
}
function consumeTryOnce(chatId) {
  if (tryOnceGrant && tryOnceGrant.chatId === String(chatId) && !tryOnceGrant.consumed) {
    tryOnceGrant.consumed = true;
    try { toast('Tried \u201c' + tryOnceGrant.label + '\u201d for one turn \u2014 back to your normal model now.'); } catch (e) {}
    setTimeout(() => { if (tryOnceGrant && tryOnceGrant.consumed) tryOnceGrant = null; }, 2000);
    return true;
  }
  return false;
}
function effectiveModel(chatId, msgText) {
  const trial = activeTryOnce(chatId, msgText);
  if (trial) return trial.model;                                     // one-turn radar trial, self-expiring
  const sticky = chatState(chatId).stickyModel;
  const pin = modelForChat(chatId);
  if (pin !== 'vool') return pin;                                  // the owning chat's pin wins
  if (sticky && !isTrivialTurn(msgText)) return sticky;              // stick to this chat's cloud model
  return 'vool';                                                    // fresh/trivial -> Auto (local-first)
}

const CANONICAL = /^openclaw:[0-9a-f]{20}$/;
function mintSessionId() {
  const bytes = new Uint8Array(10);
  crypto.getRandomValues(bytes);
  let hex = '';
  for (const b of bytes) hex += b.toString(16).padStart(2, '0');
  return 'openclaw:' + hex;
}
// The chat to show on boot. The storage key stays `vool.sessionId` because it is persisted user
// state, not a variable name.
const bootChatId = (() => {
  let stored = localStorage.getItem('vool.sessionId');
  if (!stored || !CANONICAL.test(stored)) {
    stored = mintSessionId();
    localStorage.setItem('vool.sessionId', stored);
  }
  return stored;
})();

function loadSessionModes() { return _jget('vool_modes_v1', '{}'); }
function loadModeForSession(sid) {
  const saved = loadSessionModes()[sid];
  if (saved && MODE_LABELS[saved] && saved !== 'bypass_permissions') return saved;
  // One-time migration from the retired global selector. Never migrate a bypass-like value.
  const legacy = localStorage.getItem('vool_mode');
  const migrated = legacy === 'plan' ? 'plan' : (legacy === 'build' || legacy === 'auto' ? 'auto' : 'manual');
  if (legacy) localStorage.removeItem('vool_mode');
  return migrated;
}
function saveModeForSession(sid, mode) {
  if (!sid || mode === 'bypass_permissions' || !MODE_LABELS[mode]) return;
  const modes = loadSessionModes(); modes[sid] = mode;
  localStorage.setItem('vool_modes_v1', JSON.stringify(modes));
}

// ===================== DISPATCHER: per-chat state — BEGIN =====================
// One bucket per chat. A chat's execution state -- its live run, its transcript, its approval
// grant, its mode -- belongs to THE CHAT, not to whatever the sidebar happens to be showing.
//
// The contract, in one line: every run carries `run.chatId`, stamped once at creation, and every
// function that mutates state on a run's behalf resolves its bucket through `ownerOf(run)`.
// Nothing on the execution path may read `displayedChat` or `view`. That is what makes a late
// completion in chat A incapable of touching chat B, and it is what phase 2 needs in place before
// the navigation `busy` guards can come down.
//
// Phase 2 removed the four navigation `busy` guards this model was built under, so the displayed
// chat DOES change mid-run now: a turn keeps its fetch, its reader, its AbortController and its
// timers while the user works in another chat, and re-opening its chat re-DRAWS it (renderChat /
// attachRunDom) rather than re-sending it. `busy` is no longer a process-global flag at all --
// isChatBusy(chatId) below answers it per chat, and it decides run-now vs enqueue, never navigation.
// tests/test_multichat_per_chat_state.py lifts the real functions below out of this page and drives
// them under node with the displayed chat moved AWAY from the running one.
//
// Everything between the BEGIN/END markers is deliberately self-contained -- no DOM, no fetch, no
// dependency on anything declared elsewhere on the page -- so it can be lifted out and executed
// verbatim. That is also why it is worth keeping self-contained: a module that can only run inside
// the whole page can only be tested by proxy.
const chatStates = Object.create(null);
function newChatState(chatId) {
  return {
    chatId: String(chatId || ''),
    // Fresh containers per bucket. Never a shared literal, never a prototype default -- two chats
    // that silently shared one array is exactly the contamination this whole model exists to stop.
    history: [],
    // The composer's attachment draft for THIS chat (staged at pick time, spent by the next send)
    // and the metadata of attachments riding this chat's queued messages. Per bucket, like history:
    // switching chats swaps the strip, it never carries one chat's files into another's message.
    attachments: [],
    queuedAttachmentMeta: {},
    run: null,
    // Reserved by pumpQueue across its claim await, before a run object exists. See isChatBusy().
    pumpHold: false,
    mode: 'manual',
    projectId: '',
    pins: [],
    stickyModel: '',
    bypassGrant: null,
    approvalToken: '',
    pendingApproval: null,
    resumeApprovedTurn: false,
    approvalResumeTurnId: '',
    recoveredLedger: [],
    // Every event this chat has ever recorded, in seq order -- the Activity tab's real source.
    // `recoveredLedger` stays the LAST turn's slice, which the Event log and the tab-visibility
    // rules still key off.
    chatLedger: [],
    // Runtime seq is monotonic per chat, so ingestion state belongs to the CHAT, not to whichever
    // turn happens to be polling it.  A new run must continue at the acknowledged chat tail rather
    // than replaying the session from zero.  `chatLedgerSeen` is intentionally unbounded for the
    // retained ledger: pruning identity while retaining rows recreates duplicate Activity entries.
    chatLedgerCursor: 0,
    chatLedgerSeen: Object.create(null),
    chatLedgerBusy: false,
    chatLedgerTruncated: false,
    chatActivityOpen: {},
    recoveredTask: null,
    recoveredActivityOpen: {},
  };
}
function chatState(chatId) {
  const key = String(chatId || '');
  // An unidentified caller gets a throwaway bucket rather than a shared one, so a missing id can
  // never become a channel between two chats.
  if (!key) return newChatState('');
  return chatStates[key] || (chatStates[key] = newChatState(key));
}
// The chat currently ON SCREEN, and its bucket. Presentation state only: they decide what gets
// painted and which chat a NEW turn starts in -- never which chat an already-running turn belongs
// to. Both are reassigned ONLY by setDisplayedChat().
let displayedChat = '';
let view = chatState('');
function setDisplayedChat(chatId) {
  const previousChat = displayedChat;
  displayedChat = String(chatId || '');
  view = chatState(displayedChat);
  // Named fragment call site: per-chat composer state (drafts, accent) follows the ONE place the
  // displayed chat is allowed to change. Guarded — the fragment may not be mounted.
  if (window.VoolComposerExtras && previousChat !== displayedChat) {
    try { window.VoolComposerExtras.chatSwitched(previousChat, displayedChat); } catch (e) {}
  }
  return view;
}
function isDisplayed(chatId) { return String(chatId || '') === displayedChat; }
// The owning bucket of a run -- resolved from the run's OWN chat id, never from what is on screen.
// Every completion, failure, cancellation and event path goes through here.
function ownerOf(run) { return chatState(run && run.chatId); }
function chatLedgerMaxSeq(owner) {
  const led = (owner && owner.chatLedger) || [];
  let max = 0;
  for (const e of led) max = Math.max(max, Number(e.seq) || 0);
  return max;
}
function adoptRun(chatId, run) {
  run.chatId = String(chatId || '');
  const owner = chatState(run.chatId);
  // Where this chat's history already ends. Anything untagged above this line happened during this
  // turn; anything at or below it is older chat history and is not this turn's evidence.
  run.ledgerBaseline = chatLedgerMaxSeq(owner);
  owner.run = run;
  owner.pumpHold = false;   // the reservation below is satisfied the moment a real run exists
  return run;
}
// "Busy" is a property of A CHAT, not of the application. It answers exactly one question -- does
// THIS chat run the next message now, or queue it -- and it must never gate navigation or decide
// ownership. `pumpHold` is the slot pumpQueue reserves across its claim await, before a run object
// exists; without it two sends could race into the same chat.
function isChatBusy(chatId) {
  const st = chatStates[String(chatId || '')];
  return !!(st && (st.pumpHold || (st.run && !st.run.released)));
}
// Chats with a live run right now. Used for indicators and diagnostics -- never as a gate.
function busyChatIds() { return Object.keys(chatStates).filter(isChatBusy); }
// Transcript writes are addressed by the RUN's chat, so an answer that arrives after the user has
// moved on still lands in the chat that asked for it.
function recordUserMessage(chatId, text, tsIso, attachments) {
  const entry = { role: 'user', content: text, ts: tsIso || '' };
  // Attachment metadata rides the transcript entry (names, kinds, sizes, ids, outcomes) so a
  // re-render paints the same chips; it stays OFF the outbound `messages` payload, which
  // canonicalMessagesForModel keeps at exactly [{role, content}].
  if (Array.isArray(attachments) && attachments.length) {
    entry.attachments = attachments.map((a) => attachmentMeta(a));
  }
  chatState(chatId).history.push(entry);
}
// A chat reaches /api/chat/sessions only once the server has logged a turn for it, so a new chat
// stayed invisible in the sidebar for the whole time VOOL was answering -- the one stretch when the
// user most needs to see that the chat exists. The row is shown as soon as the message is sent,
// carrying the text the user just typed, and the next loadSessions() replaces it with the server's
// own record (same session_id, so it never doubles).
// Held separately from the server list, because any refresh during the turn -- the periodic one, or
// the reload after a failed send -- replaces that list wholesale and would drop a row the server
// does not know about yet. The overlay is removed the moment the server reports the chat itself.
const _pendingSessions = new Map();
function ensureSessionInSidebar(chatId, firstMessage) {
  if (!chatId || _pendingSessions.has(chatId)) return;
  if (_lastSessions.some((s) => s.session_id === chatId && !s.pending)) return;
  const title = String(firstMessage || '').split('\n')[0].trim().slice(0, 60);
  _pendingSessions.set(chatId, {
    session_id: chatId,
    title: title || '(new chat)',
    project_id: view.projectId || '',
    archived: false,
    pending: true,
  });
  renderSessions(_lastSessions);
}
function mergePendingSessions(sessions) {
  const server = (sessions || []).filter((s) => !s.pending);
  const known = new Set(server.map((s) => s.session_id));
  _pendingSessions.forEach((_row, id) => { if (known.has(id)) _pendingSessions.delete(id); });
  return [..._pendingSessions.values()].concat(server);
}
function splitAssistantDisplayContent(text, metadata) {
  let content = text == null ? '' : String(text);
  const displayMetadata = Object.assign({}, (metadata && typeof metadata === 'object') ? metadata : {});
  // Migration path for persisted and legacy streamed answers that still carry the old footer in
  // assistant content. It remains visible, but becomes display metadata and can never re-enter the
  // model transcript. The server commit already arrives split this way.
  const footer = content.match(/(?:\n\n)?(`(?:local|cloud|tool|runtime) \| [^`\n]*`)\s*$/);
  if (footer) {
    content = content.slice(0, footer.index).replace(/\s+$/, '');
    if (!displayMetadata.provenance_footer) displayMetadata.provenance_footer = footer[1];
  }
  return { content: content, display_metadata: displayMetadata };
}
function canonicalMessagesForModel(history) {
  return (history || []).map((m) => ({ role: m.role, content: String((m && m.content) || '') }));
}
function applyResponseCommit(run, commit) {
  if (!run || !commit || commit.type !== 'response.commit' || Number(commit.version) < 1) return false;
  const commitTurn = String(commit.turn_id || ''), runTurn = String(run.turnId || '');
  if (commitTurn && runTurn && commitTurn !== runTurn) return false;
  const revision = Number(commit.revision) || 0;
  if (revision < Number(run.responseCommitRevision || 0)) return false;
  if (typeof commit.canonical_content !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(String(commit.content_hash || ''))) return false;
  run.responseCommit = commit;
  run.responseCommitRevision = revision;
  run.text = commit.canonical_content;
  run.displayMetadata = Object.assign({}, (commit.display_metadata && typeof commit.display_metadata === 'object') ? commit.display_metadata : {});
  // The commit frame and the terminal event race: finishRun can have painted the finished card
  // BEFORE the commit identity arrived (measured live — the chip then never mounted, because
  // paintFinishedCard saw no request id). If the run already ended, mount now; also stamp the
  // transcript entry so a later rebuild keeps addressing the same proof.
  const commitRequest = String(commit.request_id || '');
  if (commitRequest) {
    const _owner = ownerOf(run);
    if (run.historyIndex >= 0 && _owner.history[run.historyIndex] && !_owner.history[run.historyIndex].request_id) {
      _owner.history[run.historyIndex].request_id = commitRequest;
    }
    if (run.ended && run.assistantMsgEl && isDisplayed(run.chatId)) {
      try { mountProofChip(run.assistantMsgEl, run.chatId, commitRequest, run.displayMetadata && run.displayMetadata.presentation_selection); } catch (e) {}
    }
  }
  return true;
}
function recordAssistantMessage(run, text, metadata) {
  const owner = ownerOf(run);
  const normalized = splitAssistantDisplayContent(text, metadata);
  owner.history.push({ role: 'assistant', content: normalized.content, ts: '', display_metadata: normalized.display_metadata });
  // Where this run's answer landed in its chat's transcript. Re-opening the chat rebuilds the
  // transcript from history and re-hangs this run's card on exactly that bubble -- an index, not a
  // content match, so two identical answers in one chat cannot swap cards. Kept OFF the history
  // entry so the outbound `messages` payload stays exactly [{role, content}].
  run.historyIndex = owner.history.length - 1;
}
// Approval grants are addressed the same way: a token granted in one chat can never ride out on
// another chat's turn. The server would reject it (it binds tokens to a session), but the grant
// would be destroyed in the process and the approved action silently lost.
function approvalTokenFor(chatId) { return chatState(chatId).approvalToken; }
function setApprovalToken(chatId, token) { chatState(chatId).approvalToken = String(token || ''); }
function releaseApprovalTokenFor(chatId) { chatState(chatId).approvalToken = ''; }
// The exact body a turn sends. Extracted from runTurn so the isolation contract is assertable on
// the real payload: every field is read from the RUN's chat, never from the composer's display.
function buildTurnRequestBody(run, modelId, modelSelection) {
  const owner = ownerOf(run);
  // Sticky is a PREFERENCE, not a pin (MF-22). On Auto, a chat sticks to its last cloud model and
  // sends that concrete id; when a catalog refresh made the stuck name unresolvable, the server
  // read it as an operator pin and refused every turn while the composer showed Auto. The kind
  // lets the server keep the honest-refusal contract for real pins and re-route sticky ones.
  const selectionKind = modelSelection || (modelId !== 'vool' ? 'sticky' : 'auto');
  return {
    // The ids of the attachments THIS turn staged, and only when there are any: a text-only turn
    // carries no attachment field at all, so its body is byte-identical to a build without them.
    ...(run.attachments && run.attachments.length ? { attachments: run.attachments.map((a) => String(a.id)) } : {}),
    model: modelId,
    model_selection: selectionKind,
    // Only canonical role/content pairs cross the model boundary. Completion timestamps,
    // provenance, usage and response hashes belong to the display/audit envelope, not the prompt.
    messages: canonicalMessagesForModel(owner.history),
    stream: true,
    stream_task_events: true,
    session_id: run.chatId,
    turn_id: run.turnId,
    ...(run.queueItemId ? { queue_item_id: run.queueItemId } : {}),
    mode: owner.mode,
    autonomy: (owner.mode === 'auto' || owner.mode === 'bypass_permissions') ? 'auto' : '',
    approval_token: owner.approvalToken,
    bypass_token: (owner.bypassGrant && owner.bypassGrant.token) || '',
  };
}
// DOM writes are the ONLY thing gated on what is on screen. A run whose chat is not displayed
// updates its bucket in full and paints nothing -- it must never reach another chat's nodes.
function withRunDom(run, paint) {
  if (!run || !isDisplayed(run.chatId)) return false;
  paint();
  return true;
}
// ====================== DISPATCHER: per-chat state — END ======================

// =================== LIFECYCLE: sidebar indicator priority — BEGIN ===================
// Maps one chat's live state to exactly one sidebar badge: '' | 'working' | 'needs_user' |
// 'complete_unseen'. Deliberately pure -- no DOM, no fetch, no closure over page globals -- so the
// priority rule itself (needs_user beats working beats an unseen completion) can be lifted and
// driven under node exactly like the DISPATCHER block above it, rather than eyeballed from a
// screenshot.
//
// `opts.chatStates` is this tab's own live per-chat state -- correct instantly, even for a chat off
// screen, because a background run keeps mutating its OWN bucket regardless of what is displayed
// (see the DISPATCHER block above). `opts.serverRow` is that chat's row from
// GET /api/runtime/sessions -- the only source that still knows about a chat this tab has never
// opened, or a turn that finished before this tab existed. Both are optional; a chat backed by
// neither reads as idle, never as any of the three states.
function chatLifecycleState(chatId, opts) {
  const sid = String(chatId || '');
  if (!sid) return '';
  const o = opts || {};
  const st = o.chatStates ? o.chatStates[sid] : null;
  const run = st && st.run;
  const row = o.serverRow || null;

  // NEEDS_USER -- checked first: a run stuck on 'awaiting_approval' may already have released its
  // busy slot (the composer is meant to stay usable while a permission request is pending), so it
  // must never fall through to WORKING, and it is never "done" until the operator actually answers.
  const clientNeedsUser = !!(run && (run.status === 'awaiting_approval' || (run.permission && !run.ended)));
  const serverNeedsUser = !!row && (row.status === 'pending_approval'
    || (row.status === 'interrupted' && row.resume_available === true));
  if (clientNeedsUser || serverNeedsUser) return 'needs_user';

  // WORKING -- a turn is genuinely executing right now. A server row can read `status: 'running'`
  // for a worker that has since crashed (list_runtime_sessions() in runtime_continuity.py calls
  // this out explicitly); `worker_live` is what turns that row into real evidence instead of a
  // stale flag left over from a dead process.
  const clientWorking = !!(st && (st.pumpHold || (run && !run.ended)));
  const serverWorking = !!row && row.status === 'running' && row.worker_live === true;
  if (clientWorking || serverWorking) return 'working';

  // COMPLETE_UNSEEN -- finished since this chat was last opened, and the operator is not looking at
  // it right now. Being the chat on screen only counts as "looking" while the tab itself is
  // actually visible -- a completion behind a hidden tab is still unseen.
  if (sid === o.displayedChat && !o.hidden) return '';
  const clientDoneAt = (run && run.ended && run.status === 'completed') ? run.endedAt : '';
  const serverDoneAt = (row && row.status === 'completed') ? (row.updated_at || '') : '';
  const doneAt = clientDoneAt || serverDoneAt;
  if (!doneAt) return '';
  const seenAt = o.seenAt || '';
  if (!seenAt || Date.parse(doneAt) > Date.parse(seenAt)) return 'complete_unseen';
  return '';
}
// ==================== LIFECYCLE: sidebar indicator priority — END ====================

setDisplayedChat(bootChatId);
// Migrate the old global preference only to the chat that owned it at upgrade.
if (!localStorage.getItem('vool_chat_models_v1')) setModelValue(modelValue, bootChatId);
restoreChatModel(bootChatId);
view.mode = loadModeForSession(displayedChat);

// The commit this PAGE was rendered from, substituted server-side. The header version below is
// fetched live, so a window left open across a daemon upgrade displayed the NEW commit while
// executing OLD JavaScript -- measured 2026-07-31: an operator's window showed (a9ac0da) in this
// header while running a pre-renderer page, so every table and heading in an answer reached them
// as literal markdown, and the version string said they were up to date. The page must reload
// itself when the daemon it is talking to is no longer the daemon that served it.
const PAGE_BUILD_COMMIT = '__PAGE_BUILD_COMMIT__';

fetch('/api/runtime/version').then((r) => r.json()).then((v) => {
  const rel = v.release_version || 'dev';
  const commit = (v.commit || '').slice(0, 7);
  const dirty = !!v.dirty;
  const source = commit ? commit + (dirty ? '+dirty' : '') : (dirty ? 'dirty' : '');
  verEl.textContent = 'v' + rel + (source ? ' (' + source + ')' : '');
  verEl.title = v.build_id || verEl.textContent;
  maybeReloadForNewBuild(v.commit || '');
}).catch(() => {});

function maybeReloadForNewBuild(liveCommit) {
  // Reload once per live commit, marked BEFORE reloading -- if the substitution ever broke, an
  // unconditional reload would loop the page forever. An unsubstituted placeholder disables the
  // check rather than triggering it.
  if (!liveCommit || !PAGE_BUILD_COMMIT || PAGE_BUILD_COMMIT.indexOf('__') === 0) return;
  if (liveCommit === PAGE_BUILD_COMMIT) return;
  const key = 'vool_reloaded_for_' + liveCommit;
  if (sessionStorage.getItem(key)) return;
  sessionStorage.setItem(key, '1');
  location.reload();
}

// A window can sit open for days, so the load-time check alone is not enough: keep an eye on the
// daemon and pick up an upgrade within a minute of it happening.
setInterval(() => {
  fetch('/api/runtime/version').then((r) => r.json())
    .then((v) => maybeReloadForNewBuild(v.commit || '')).catch(() => {});
}, 60000);

function showEmpty() {
  // Time-aware greeting + a rotating opener so a new chat never opens on a blank screen.
  const h = new Date().getHours();
  const period = h < 5 ? ['Still up?', ['🌙', '🦉']] : h < 12 ? ['Good morning', ['👋', '☀️', '☕']]
    : h < 18 ? ['Good afternoon', ['👋', '🙂']] : h < 22 ? ['Good evening', ['👋', '🌆']] : ['Working late?', ['🌙', '🦉']];
  // A friendly emoji once in a while (not every open) — Codex-style "Good morning 👋".
  let greet = period[0];
  if (Math.random() < 0.45) greet += ' ' + period[1][Math.floor(Math.random() * period[1].length)];
  const openers = ['What are we working on today?', "What’s the task?", 'What are you thinking about?',
    'What should we build?', 'What can I help with?', "What’s on your mind?", 'Where do we start?'];
  const line = openers[Math.floor(Math.random() * openers.length)];
  // A rotating fun fact — one per fresh session, quoted in an italic serif so it reads apart from
  // the greeting/opener. Two flavors, both TRUE: what VOOL genuinely does, and real how-AI-works
  // facts (including genuine "fails"). NO invented statistics — no made-up adoption % or market share.
  const facts = [
    // — what VOOL does —
    'Runs on your machine by default — your prompts never leave it unless you turn on the cloud.',
    'Local chat is free — no per-token meter. Add a cloud key only when you want a bigger model.',
    'No subscription and no per-message fee — it runs on hardware you already own.',
    'Reads, searches, and edits files in your workspace, and runs tests in a sandbox.',
    'Ask it about a folder and it reads the real files — not a guess.',
    'Bind a chat to a project folder and its memory stays scoped to that project.',
    'Remembers across sessions — everything stored locally on this machine.',
    'Cloud is opt-in: off until you add a key, and you can cap the spend.',
    'Your API keys stay sealed on this machine — encrypted at rest, never shown back.',
    'Switch between local and your own cloud models per chat — you decide, per turn.',
    'Works offline — the local model needs no connection.',
    // — how AI actually works (and where it trips) —
    'A language model just predicts the next word — it looks nothing up unless you hand it a tool.',
    'Models read text as "tokens," not letters — which is why they can miscount the letters in a word.',
    'A "hallucination" is a model stating something false with full confidence — grounding it in real files is the cure.',
    'The "context window" is how much a model can read at once — overflow it and it forgets the start.',
    '"Temperature" tunes randomness: low keeps answers focused, high lets them wander.',
    'Ask a model the same thing twice and you can get two different answers — that randomness is a dial, not a bug.',
    'The same open models behind many cloud assistants can run right here on your desk.',
    'Bigger is not always better — a small local model answers everyday questions instantly.',
  ];
  const fact = facts[Math.floor(Math.random() * facts.length)];
  logEl.innerHTML = '<div class="empty">'
    + '<div class="empty-greet">' + esc(greet) + '</div>'
    + '<div class="empty-sub">' + esc(line) + '</div>'
    + '<div class="empty-hint">“' + esc(fact) + '”</div>'
    + '</div>';
}

// HH:mm in the viewer's own local timezone (Date's getHours/getMinutes already convert from the
// UTC ISO string the server writes), never seconds. Returns '' for anything that doesn't parse --
// missing/malformed source degrades quietly to no time shown, never a fabricated stand-in.
function formatMsgTime(tsIso) {
  if (!tsIso) return '';
  const d = new Date(tsIso);
  if (isNaN(d.getTime())) return '';
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
// Idempotent: creates the .msg-time element on first call, updates it on any later call (used by
// finishRun once a streaming answer reaches its real terminal state). No-ops on an unparseable or
// absent timestamp, so a bubble simply shows no time rather than one that isn't real.
function setMsgTime(el, tsIso) {
  if (!el) return;
  const text = formatMsgTime(tsIso);
  if (!text) return;
  let t = el.querySelector('.msg-time');
  if (!t) { t = document.createElement('span'); t.className = 'msg-time'; el.appendChild(t); }
  t.textContent = text;
  t.title = new Date(tsIso).toLocaleString(appLocale());
}
function addMsg(role, text, tsIso, displayMetadata, attachments, requestId) {
  const empty = logEl.querySelector('.empty');
  if (empty) empty.remove();
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  // A user message's attachments are painted above its text as read-only chips: what was sent,
  // and -- once the transcript says so -- what became of each.
  if (role === 'user' && Array.isArray(attachments) && attachments.length) el.appendChild(buildMsgAttachments(attachments));
  const span = document.createElement('span');
  span.className = 'msg-text';
  if (role === 'assistant') { renderAssistantContent(span, text, displayMetadata); } else { span.textContent = text; span.dataset.raw = text; }
  el.appendChild(span);
  // The Proof Chip hangs off the assistant bubble by this message's canonical request id.
  // A transcript row without one (legacy turn) mounts nothing — never a borrowed proof.
  if (role === 'assistant' && requestId) { try { mountProofChip(el, displayedChat, requestId, displayMetadata && displayMetadata.presentation_selection); } catch (e) {} }
  // A user bubble gets its send time now (this IS the actual moment it's being sent -- not a later
  // re-guess). An assistant bubble gets none here: it isn't answered yet, and finishRun() stamps
  // the real completion time once the turn genuinely ends. A reload passes the persisted ts through.
  setMsgTime(el, tsIso);
  // Hover-only action row. Revealed on hover/focus. There is no thumbs rating here -- this build
  // has no feedback endpoint, and a rating nothing reads is not a rating.
  // On assistant bubbles the copy controls are split by representation so what lands on the
  // clipboard is never a guess: "Copy" takes the answer's raw Markdown source (exactly what the
  // transcript holds), "Copy text" takes the same content as plain text with Markdown decoration
  // removed, and "Copy rich" (only offered where the browser really supports styled-clipboard
  // writes) adds a sanitized rendered form -- how the receiving app then renders it is the
  // receiving app's business. A user bubble keeps the single Copy of their own typed source:
  // its chips must stay clear of this floating row, and a response is what a person copies.
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  const rawOf = () => (span.dataset.raw != null ? span.dataset.raw : span.textContent);
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'msg-copy';
  copy.textContent = 'Copy';
  copy.title = role === 'assistant' ? 'Copy message source (raw Markdown, exactly as stored)' : 'Copy message';
  copy.addEventListener('click', () => copyText(rawOf(), copy));
  actions.appendChild(copy);
  if (role === 'assistant') {
    const copyTextBtn = document.createElement('button');
    copyTextBtn.type = 'button';
    copyTextBtn.className = 'msg-copy msg-copy-text';
    copyTextBtn.textContent = 'Copy text';
    copyTextBtn.title = 'Copy as plain text (Markdown formatting removed)';
    copyTextBtn.addEventListener('click', () => copyText(mdToPlainText(rawOf()), copyTextBtn));
    actions.appendChild(copyTextBtn);
    if (richClipboardSupported()) {
      const copyRichBtn = document.createElement('button');
      copyRichBtn.type = 'button';
      copyRichBtn.className = 'msg-copy msg-copy-rich';
      copyRichBtn.textContent = 'Copy rich';
      copyRichBtn.title = 'Copy with formatting (sanitized HTML + plain text — the receiving app decides how it renders)';
      copyRichBtn.addEventListener('click', () => copyRichMessage(rawOf(), copyRichBtn));
      actions.appendChild(copyRichBtn);
    }
  }
  // Pin: save this message to the chat so it's findable later (📌 in the header opens the pinned list).
  const pin = document.createElement('button');
  pin.type = 'button'; pin.className = 'msg-act msg-pin';
  setPinBtn(pin, isPinnedLocal(role, text));
  pin.addEventListener('click', () => togglePin(role, text, pin));
  actions.appendChild(pin);
  el.appendChild(actions);
  if (role === 'assistant' && requestId) mountAnswerExport(el, displayedChat, requestId);
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;
  return el;
}

function msgTextEl(el) { return el.querySelector('.msg-text'); }

// ---- Pinned messages: save an answer in a long chat, find it again from the header 📌 ----
function isPinnedLocal(role, text) { const t = (text || '').trim(); return view.pins.some((p) => p.role === role && p.text === t); }
function setPinBtn(btn, on) { btn.textContent = on ? 'Pinned' : 'Pin'; btn.title = on ? 'Unpin this message' : 'Pin this message to the chat'; btn.classList.toggle('on', !!on); }
function updatePinCount() {
  const b = document.getElementById('pinBtn'); if (!b) return;
  const n = view.pins.length; const nEl = b.querySelector('.pin-n'); if (nEl) nEl.textContent = n ? String(n) : '';
  b.classList.toggle('has', n > 0);
}
async function loadPins() {
  const chatId = displayedChat, target = chatState(chatId);   // owner bound before the await
  try { const r = await fetch('/api/chat/pins?session=' + encodeURIComponent(chatId)); target.pins = (await r.json()).pins || []; }
  catch (e) { target.pins = []; }
  updatePinCount();
}
async function togglePin(role, text, btn) {
  const clean = (text || '').trim(); if (!clean) return;
  const chatId = displayedChat, target = chatState(chatId);   // owner bound before the await
  const existing = target.pins.find((p) => p.role === role && p.text === clean);
  try {
    const payload = existing ? { session_id: chatId, pin_id: existing.id, delete: true } : { session_id: chatId, role: role, text: clean };
    const r = await fetch('/api/chat/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await r.json(); if (Array.isArray(d.pins)) target.pins = d.pins;
  } catch (e) {}
  if (btn) setPinBtn(btn, isPinnedLocal(role, clean));
  updatePinCount(); renderPinPanel();
}
function openPinPanel() { const o = document.getElementById('pinOverlay'); if (o) { o.hidden = false; renderPinPanel(); } }
function closePinPanel() { const o = document.getElementById('pinOverlay'); if (o) o.hidden = true; }
function renderPinPanel() {
  const body = document.getElementById('pinBody'); if (!body) return;
  body.innerHTML = '';
  if (!view.pins.length) {
    const e = document.createElement('div'); e.className = 'pin-empty';
    e.textContent = 'No pinned messages in this chat yet. Hover any message and hit Pin.';
    body.appendChild(e); return;
  }
  for (const p of view.pins) {
    const card = document.createElement('div'); card.className = 'pin-card';
    const meta = document.createElement('div'); meta.className = 'pin-meta'; meta.textContent = p.role === 'assistant' ? 'VOOL' : 'You';
    const txt = document.createElement('div'); txt.className = 'pin-text'; txt.textContent = p.text.length > 800 ? (p.text.slice(0, 800) + '…') : p.text;
    const row = document.createElement('div'); row.className = 'pin-row';
    const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'pin-btn2'; copy.textContent = 'Copy'; copy.addEventListener('click', () => copyText(p.text, copy));
    const un = document.createElement('button'); un.type = 'button'; un.className = 'pin-btn2'; un.textContent = 'Unpin'; un.addEventListener('click', () => unpinFromPanel(p.id));
    row.appendChild(copy); row.appendChild(un);
    card.appendChild(meta); card.appendChild(txt); card.appendChild(row); body.appendChild(card);
  }
}
async function unpinFromPanel(pinId) {
  const chatId = displayedChat, target = chatState(chatId);   // owner bound before the await
  try { const r = await fetch('/api/chat/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: chatId, pin_id: pinId, delete: true }) }); const d = await r.json(); if (Array.isArray(d.pins)) target.pins = d.pins; } catch (e) {}
  updatePinCount(); renderPinPanel();
  document.querySelectorAll('#log .msg').forEach((el) => {   // un-mark any visible message that just got unpinned
    const b = el.querySelector('.msg-pin'); const t = msgTextEl(el);
    if (b && t) { const raw = t.dataset.raw != null ? t.dataset.raw : t.textContent; setPinBtn(b, isPinnedLocal(el.classList.contains('assistant') ? 'assistant' : 'user', raw)); }
  });
}

function copyText(text, btn) {
  const done = () => { const prev = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = prev; }, 1200); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, btn, done));
  } else {
    fallbackCopy(text, btn, done);
  }
}
function fallbackCopy(text, btn, done) {
  // The textarea path cannot carry \r faithfully: the HTML spec normalizes textarea values
  // (CRLF and CR become LF), so a payload containing \r would be COPIED MUTATED and then
  // celebrated as Copied. That copy is refused and reported as the failure it is.
  if (/\r/.test(text)) { copiedFail(btn); return; }
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.focus(); ta.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
  document.body.removeChild(ta);
  // A copy that did not happen is never celebrated: execCommand returning false (or throwing)
  // lands here as a visible failure, not a "Copied".
  if (copied) { if (done) done(); } else { copiedFail(btn); }
}
function copiedFail(btn) { btn.textContent = 'Copy failed'; setTimeout(() => { btn.textContent = 'Copy'; }, 1200); }

// Styled-clipboard support is probed, never assumed: a write of an HTML+plain ClipboardItem only
// exists on some browsers/contexts. Where it is missing, no rich button is offered at all -- the
// raw-Markdown and plain-text copies always are.
function richClipboardSupported() {
  try { return typeof window.ClipboardItem === 'function' && !!(navigator.clipboard && navigator.clipboard.write); } catch (e) { return false; }
}

// Copy a message's rendered form as sanitized rich text. The HTML is produced by the page's own
// renderer (the same escaping every bubble on screen already went through) from the message's raw
// source inside a detached element, so what is copied is exactly the canonical content: no
// provenance footer, no verifier flag, nothing the transcript does not hold. The controls the
// renderer generated (code-copy buttons) are stripped — the payload is the message, not the page
// chrome. A plain-text fallback rides along for apps that do not take text/html. If the styled
// write itself fails, the raw source is copied instead; if that copy also fails, the button says
// so — a copy that did not happen is never reported as Copied.
function richHtmlFor(raw) {
  const holder = document.createElement('div');
  renderRichText(holder, raw);
  holder.querySelectorAll('button, .code-copy').forEach((b) => b.remove());
  return holder.innerHTML;
}

function copyRichMessage(raw, btn) {
  const done = () => { const prev = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = prev; }, 1200); };
  let html = '';
  try { html = richHtmlFor(raw); } catch (e) { html = ''; }
  if (!html || typeof window.ClipboardItem !== 'function' || !(navigator.clipboard && navigator.clipboard.write)) { copyText(raw, btn); return; }
  try {
    navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([raw], { type: 'text/plain' })
    })]).then(done).catch(() => copyText(raw, btn));
  } catch (e) { copyText(raw, btn); }
}

// Plain-text form of a message's raw Markdown: the content, readable, with Markdown decoration
// removed rather than shown. Fenced code keeps its exact body (the fence markers themselves are
// the decoration); headings, emphasis markers and quote marks are stripped; links become
// "text (url)"; lists and tables keep their line shapes so structure survives as text. This is
// a copy representation only -- the stored source is never rewritten by it.
function mdToPlainText(raw) {
  const src = raw == null ? '' : String(raw);
  const lines = src.split('\n');
  const out = [];
  let fenceMarker = '';
  const stripInline = (s) => {
    let t = s;
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (m, alt, url) => '[image: ' + (alt || url) + ']');
    t = t.replace(/\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (m, text, url) => (text ? text + ' (' + url + ')' : url));
    t = t.replace(/\*\*([^*]+)\*\*/g, '$1');
    t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1$2');
    t = t.replace(/`([^`]+)`/g, '$1');
    return t;
  };
  for (const line of lines) {
    if (fenceMarker) {
      const close = line.match(/^\s*(`{3,}|~{3,})\s*$/);
      if (close && close[1][0] === fenceMarker[0] && close[1].length >= fenceMarker.length) { fenceMarker = ''; continue; }
      out.push(line); continue;
    }
    const open = line.match(/^\s*(`{3,}|~{3,})(\w*)\s*$/);
    if (open) { fenceMarker = open[1]; continue; }
    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) { out.push(''); continue; }
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (heading) { out.push(stripInline(heading[1])); continue; }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) { out.push(stripInline(quote[1])); continue; }
    out.push(stripInline(line));
  }
  return out.join('\n');
}

// ---- Projects: SERVER-BACKED. A project is a local folder; a chat bound to it works in that folder
// and recalls only that project's memory (isolation is enforced server-side). Projects + bindings live
// on the server, so they persist and are the same in every window. Collapse state is a per-browser view
// preference. ----
function _jget(k, d) { try { return JSON.parse(localStorage.getItem(k) || d); } catch (e) { return JSON.parse(d); } }
function loadCollapsed() { return _jget('vool_project_collapsed', '{}'); }
function saveCollapsed(c) { localStorage.setItem('vool_project_collapsed', JSON.stringify(c)); }
let _serverProjects = {};   // {id: {id, name, root, exists}}
let _lastSessions = [];
let _sessionCounts = null;   // true per-group chat counts from the server; the page is capped
function loadProjects() { return _serverProjects; }
async function refreshProjects() {
  try {
    const r = await fetch('/api/projects'); const d = await r.json();
    const map = {};
    for (const p of (d.projects || [])) map[p.id] = p;
    _serverProjects = map;
  } catch (e) { /* keep the last-known list on a transient error */ }
  return _serverProjects;
}
function requestTextInput(message, initialValue = '') {
  // WKWebView's bundled delegate has no window.prompt implementation. Keep text entry in
  // the served UI, with native dialog focus/keyboard semantics and no HTML from caller values.
  if (document.getElementById('textPromptDialog')) return Promise.resolve(null);
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.id = 'textPromptDialog'; dialog.className = 'modal text-prompt';
  dialog.setAttribute('aria-labelledby', 'textPromptLabel');
  dialog.innerHTML = '<form method="dialog"><div class="modal-body">'
    + '<label id="textPromptLabel" for="textPromptValue"></label>'
    + '<input id="textPromptValue" type="text" autocomplete="off">'
    + '<div class="export-actions"><button type="button" class="set-btn" data-cancel>Cancel</button>'
    + '<button type="submit" class="set-btn">OK</button></div></div></form>';
  dialog.querySelector('label').textContent = String(message || 'Enter a value');
  const input = dialog.querySelector('input'); input.value = String(initialValue == null ? '' : initialValue);
  document.body.appendChild(dialog);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return; settled = true;
      dialog.close(); dialog.remove();
      if (previousFocus && previousFocus.isConnected) previousFocus.focus();
      resolve(value);
    };
    dialog.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); finish(input.value); });
    dialog.querySelector('[data-cancel]').addEventListener('click', () => finish(null));
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); finish(null); });
    dialog.addEventListener('keydown', (e) => { if (e.key === 'Escape') e.stopPropagation(); });
    dialog.showModal(); input.focus(); input.select();
  });
}
async function nativePickFolder() {
  // Native picker cancellation is final. An unavailable picker returns undefined so browsers
  // and unsupported hosts can ask for a typed path.
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
      const res = await window.pywebview.api.pick_folder();
      if (res && res.ok && res.path) return res.path;
      if (res && res.cancelled) return null;
    }
  } catch (e) {}
  return undefined;
}
async function createProjectFlow(assignSid) {
  let root = await nativePickFolder();
  if (root === null) return null;
  if (!root) {
    root = await requestTextInput('Project folder — full path to a folder on this machine:', '');
    if (!root || !root.trim()) return null;
    root = root.trim();
  }
  const suggested = root.split('/').filter(Boolean).pop() || 'Project';
  const name = await requestTextInput('Project name:', suggested);
  if (name === null) return null;
  try {
    const r = await fetch('/api/projects', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), root: root }),
    });
    const d = await r.json();
    if (!r.ok || !d.project) { alert('Could not add project: ' + ((d && d.error) || ('HTTP ' + r.status))); return null; }
    await refreshProjects();
    // Global "+ Project": mint + bind + focus a fresh chat for the new project (reuses the
    // r.ok-gated fail-closed bind in newChatInProject). The move-menu path (assignSid) reassigns the
    // existing chat instead.
    if (assignSid) { await assignSession(assignSid, d.project.id); } else { await newChatInProject(d.project.id); }
    return d.project.id;
  } catch (e) { alert('Could not add project.'); return null; }
}
async function deleteProject(pid) {
  const proj = _serverProjects[pid]; if (!proj) return;
  if (!confirm('Delete project "' + (proj.name || 'this project') + '"?\n\nVOOL will FORGET everything it learned in it, and its chats move back to General (your files are untouched).')) return;
  try { await fetch('/api/projects/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: pid }) }); } catch (e) {}
  await refreshProjects();
  await loadSessions();   // its chats now fall back to General; its memory is purged server-side
}
async function revealProject(pid) {
  if (!_serverProjects[pid]) return;
  try { await fetch('/api/projects/reveal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: pid }) }); } catch (e) {}
}
async function assignSession(sid, pid) {
  let ok = false;
  try {
    const r = await fetch('/api/chat/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, project_id: pid || '' }),
    });
    ok = r.ok;
  } catch (e) {}
  // Fail closed: surface a bind failure instead of silently continuing as if the move succeeded.
  if (!ok) toast('Could not move this chat — it was not changed. Try again.');
  await loadSessions();
}
function closeProjectMenu() { const m = document.getElementById('projMenu'); if (m) m.remove(); }
function openProjectMenu(sid, anchor) {
  closeProjectMenu();
  const projects = loadProjects();
  const cur = ((_lastSessions.find((s) => s.session_id === sid) || {}).project_id) || '';
  const menu = document.createElement('div');
  menu.className = 'proj-menu'; menu.id = 'projMenu';
  const add = (label, pid, on) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'proj-menu-item' + (on ? ' on' : '');
    b.textContent = (on ? '✓ ' : '') + label;
    b.addEventListener('click', (e) => { e.stopPropagation(); assignSession(sid, pid); closeProjectMenu(); });
    menu.appendChild(b);
  };
  add('General', '', !cur);
  for (const id in projects) add(projects[id].name || 'Project', id, cur === id);
  const nb = document.createElement('button');
  nb.type = 'button'; nb.className = 'proj-menu-item new'; nb.textContent = '+ New project…';
  nb.addEventListener('click', (e) => { e.stopPropagation(); closeProjectMenu(); createProjectFlow(sid); });
  menu.appendChild(nb);
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.top = (r.bottom + 4) + 'px';
  menu.style.left = Math.max(6, Math.min(r.left, window.innerWidth - 190)) + 'px';
  setTimeout(() => document.addEventListener('click', closeProjectMenu, { once: true }), 0);
}
function startProjectRename(head, nameEl, pid) {
  const proj = _serverProjects[pid];
  if (!proj) return;
  const input = document.createElement('input');
  input.className = 's-rename'; input.value = nameEl.textContent;
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const val = input.value.trim();
    if (save && val && val !== proj.name) {
      // Rename = re-register the SAME folder with a new name (create is idempotent by real path).
      try { await fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: val.slice(0, 60), root: proj.root }) }); } catch (e) {}
      await refreshProjects();
    }
    renderSessions(_lastSessions);
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } });
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('click', (e) => e.stopPropagation());
  head.replaceChild(input, nameEl);
  input.focus(); input.select();
}

function makeSessionItem(s, archived, nested) {
  const item = document.createElement('div');
  item.className = 'session' + (s.session_id === displayedChat ? ' active' : '') + (nested ? ' nested' : '');
  applyAccent(item, s.color);   // subtle bg tint + left accent bar when a colour is set
  const emo = document.createElement('span'); emo.className = 's-emoji'; if (s.emoji) emo.textContent = s.emoji;
  const title = document.createElement('span');
  title.className = 's-title';
  title.textContent = s.title || '(new chat)';
  title.title = s.title || '';
  title.addEventListener('click', () => openSession(s.session_id));
  const actions = document.createElement('div');
  actions.className = 's-actions';
  const semo = document.createElement('button');
  semo.type = 'button'; semo.className = 's-act'; semo.textContent = '🎨'; semo.title = 'Set chat emoji or colour';
  semo.addEventListener('click', (e) => { e.stopPropagation(); openSessionEmojiPicker(s.session_id, semo); });
  actions.appendChild(semo);
  if (!archived) {
    const move = document.createElement('button');
    move.type = 'button'; move.className = 's-act'; move.textContent = '\u{1F4C1}'; move.title = 'Move to project';
    move.addEventListener('click', (e) => { e.stopPropagation(); openProjectMenu(s.session_id, move); });
    actions.appendChild(move);
  }
  const ren = document.createElement('button');
  ren.type = 'button'; ren.className = 's-act'; ren.textContent = '✎'; ren.title = 'Rename';
  ren.addEventListener('click', (e) => { e.stopPropagation(); startRename(item, title, s.session_id); });
  const arch = document.createElement('button');
  arch.type = 'button'; arch.className = 's-act';
  arch.textContent = archived ? '↩' : '\u{1F5C4}';
  arch.title = archived ? 'Unarchive' : 'Archive';
  arch.addEventListener('click', (e) => { e.stopPropagation(); archiveSession(s.session_id, !archived); });
  const del = document.createElement('button');
  del.type = 'button'; del.className = 's-act'; del.textContent = '\u{1F5D1}'; del.title = 'Delete chat (removes it and its memory)';
  del.addEventListener('click', (e) => { e.stopPropagation(); deleteSession(s.session_id, s.title || ''); });
  actions.appendChild(ren); actions.appendChild(arch); actions.appendChild(del);
  item.appendChild(emo); item.appendChild(title);
  if (!archived) appendLifecycleBadge(item, sidebarLifecycleState(s.session_id));
  item.appendChild(actions);
  return item;
}
async function deleteSession(sid, title) {
  if (!sid) return;
  if (!confirm('Delete "' + (title || 'this chat') + '"? This permanently removes the chat and everything VOOL learned in it.')) return;
  try { await fetch('/api/chat/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sid, delete: true }) }); } catch (e) {}
  if (sid === displayedChat) {
    // Deleting the chat you are IN has to leave you somewhere, so a replacement is opened. It must
    // open in the SAME project: newChat() hard-sets view.projectId = '', so the replacement
    // landed in General and the operator saw their project drop by one while General went UP by
    // one -- a delete that looked like a move. view.projectId is still the deleted chat's
    // project here, because sid is the open chat.
    const pid = view.projectId;
    if (pid) { await newChatInProject(pid); } else { newChat(); }
  }
  await loadSessions();
}

function makeProjectHead(pid, name, count, collapsed, lcState) {
  const head = document.createElement('div');
  head.className = 'proj-head' + (collapsed ? ' collapsed' : '') + (pid && pid === view.projectId ? ' active' : '');
  const proj = _serverProjects[pid] || {};
  const caret = document.createElement('span'); caret.className = 'proj-caret'; caret.textContent = collapsed ? '▸' : '▾';
  const emo = document.createElement('span'); emo.className = 'proj-emoji'; if (proj.emoji) emo.textContent = proj.emoji;
  const nameEl = document.createElement('span'); nameEl.className = 'proj-name';
  nameEl.textContent = (proj.root && proj.exists === false) ? (name + ' ⚠') : name;
  nameEl.title = proj.root ? ((proj.exists === false ? 'Folder not found: ' : '') + proj.root) : name;
  const cnt = document.createElement('span'); cnt.className = 'proj-count'; cnt.textContent = count ? String(count) : '';
  const pact = document.createElement('div'); pact.className = 'proj-actions';
  const prev = document.createElement('button'); prev.type = 'button'; prev.className = 's-act'; prev.textContent = '\u{1F4C2}'; prev.title = 'Reveal folder in Finder';
  prev.addEventListener('click', (e) => { e.stopPropagation(); revealProject(pid); });
  const pren = document.createElement('button'); pren.type = 'button'; pren.className = 's-act'; pren.textContent = '✎'; pren.title = 'Rename project';
  pren.addEventListener('click', (e) => { e.stopPropagation(); startProjectRename(head, nameEl, pid); });
  const pdel = document.createElement('button'); pdel.type = 'button'; pdel.className = 's-act'; pdel.textContent = '\u{1F5D1}'; pdel.title = 'Delete project (forget its memory; chats move to General)';
  pdel.addEventListener('click', (e) => { e.stopPropagation(); deleteProject(pid); });
  const pnew = document.createElement('button'); pnew.type = 'button'; pnew.className = 's-act'; pnew.textContent = '+'; pnew.title = 'New chat in this project';
  pnew.addEventListener('click', (e) => { e.stopPropagation(); newChatInProject(pid); });
  const pemo = document.createElement('button'); pemo.type = 'button'; pemo.className = 's-act'; pemo.textContent = '🎨'; pemo.title = 'Set project emoji or colour';
  pemo.addEventListener('click', (e) => { e.stopPropagation(); openEmojiPicker(pid, pemo); });
  pact.appendChild(pemo); pact.appendChild(pnew); pact.appendChild(prev); pact.appendChild(pren); pact.appendChild(pdel);
  head.appendChild(caret); head.appendChild(emo); head.appendChild(nameEl); head.appendChild(cnt);
  appendLifecycleBadge(head, lcState);
  head.appendChild(pact);
  applyAccent(head, proj.color);   // subtle bg tint + left accent bar when a colour is set
  head.addEventListener('click', () => { const c = loadCollapsed(); c[pid] = !c[pid]; saveCollapsed(c); renderSessions(_lastSessions); });
  return head;
}

// A quick marker per project AND per chat — an emoji and/or an accent colour — so you can tell them
// apart at a glance in the sidebar and in the context bar above the composer. Default is neither.
const EMOJI_PALETTE = ['🎯','🚀','🔥','⭐','💡','🎨','🎬','🎮','🧪','🔬','🛠️','📦','🌐','🔒','💰','📊','🧠','🤖','👾','🦞','🐙','🐳','🦊','🐤','🌙','⚡','💎','🏆','📌','🔑','🧩','📷','🕹️','🧬','⚙️','🌀','🛰️','🧭','🗂️','💼','🏗️','🔭','🪐','🍀','🎵','📝','🌈','✨'];
// Accent colours: [#rrggbb, label]. Tuned to read as subtle tints on the dark UI. '' = none.
const COLOR_PALETTE = [['#e5484d','Red'],['#f76b15','Orange'],['#ffb224','Amber'],['#46a758','Green'],['#12a594','Teal'],['#3e63dd','Blue'],['#8e4ec6','Purple'],['#e93d82','Pink'],['#8b8d98','Slate']];
function hexToRgba(hex, a) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
  return m ? ('rgba(' + parseInt(m[1],16) + ',' + parseInt(m[2],16) + ',' + parseInt(m[3],16) + ',' + a + ')') : '';
}
// Paint an element with an accent colour (subtle bg tint + a left accent bar), or clear it when color is ''.
function applyAccent(el, color) {
  if (color) { el.style.background = hexToRgba(color, 0.20); el.style.boxShadow = 'inset 3px 0 0 ' + color; }
  else { el.style.background = ''; el.style.boxShadow = ''; }
}
function closeEmojiPicker() { const m = document.getElementById('emojiPicker'); if (m) m.remove(); }
// One picker, two callers (project + chat): preselect the current {emoji,color}; onEmoji/onColor apply a change.
function openAppearancePicker(anchor, current, onEmoji, onColor) {
  closeEmojiPicker(); if (typeof closeProjectMenu === 'function') closeProjectMenu();
  const curEmoji = (current && current.emoji) || '';
  const curColor = (current && current.color) || '';
  const box = document.createElement('div'); box.className = 'emoji-picker'; box.id = 'emojiPicker';
  const crow = document.createElement('div'); crow.className = 'color-row';
  for (const pair of COLOR_PALETTE) {
    const sw = document.createElement('button'); sw.type = 'button';
    sw.className = 'color-cell' + (pair[0] === curColor ? ' on' : ''); sw.title = pair[1]; sw.style.background = pair[0];
    sw.addEventListener('click', (ev) => { ev.stopPropagation(); onColor(pair[0]); closeEmojiPicker(); });
    crow.appendChild(sw);
  }
  box.appendChild(crow);
  const grid = document.createElement('div'); grid.className = 'emoji-grid';
  for (const e of EMOJI_PALETTE) {
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'emoji-cell' + (e === curEmoji ? ' on' : ''); b.textContent = e;
    b.addEventListener('click', (ev) => { ev.stopPropagation(); onEmoji(e); closeEmojiPicker(); });
    grid.appendChild(b);
  }
  box.appendChild(grid);
  const clr = document.createElement('button'); clr.type = 'button'; clr.className = 'emoji-clear'; clr.textContent = 'Clear emoji & colour';
  clr.addEventListener('click', (ev) => { ev.stopPropagation(); onColor(''); onEmoji(''); closeEmojiPicker(); });
  box.appendChild(clr);
  document.body.appendChild(box);
  const r = anchor.getBoundingClientRect();
  box.style.top = (r.bottom + 4) + 'px';
  box.style.left = Math.max(6, Math.min(r.left - 100, window.innerWidth - 246)) + 'px';
  setTimeout(() => document.addEventListener('click', closeEmojiPicker, { once: true }), 0);
}
function openEmojiPicker(pid, anchor) {
  const p = _serverProjects[pid] || {};
  openAppearancePicker(anchor, { emoji: p.emoji, color: p.color },
    (e) => setProjectAppearance(pid, { emoji: e }), (c) => setProjectAppearance(pid, { color: c }));
}
function openSessionEmojiPicker(sid, anchor) {
  const s = (_lastSessions || []).find((x) => x.session_id === sid) || {};
  openAppearancePicker(anchor, { emoji: s.emoji, color: s.color },
    (e) => setSessionAppearance(sid, { emoji: e }), (c) => setSessionAppearance(sid, { color: c }));
}
async function setProjectAppearance(pid, patch) {
  try { await fetch('/api/projects/emoji', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ id: pid }, patch)) }); } catch (e) {}
  await refreshProjects(); renderSessions(_lastSessions); renderContextBar();
}
async function setSessionAppearance(sid, patch) {
  try { await fetch('/api/chat/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ session_id: sid }, patch)) }); } catch (e) {}
  await loadSessions(); renderContextBar();
}

// The context bar above the composer: the project you're in and the chat within it, each carrying its
// own emoji + colour, so you always know where a message will land. No project => a muted "General".
function renderContextBar() {
  const bar = document.getElementById('ctxBar');
  if (!bar) return;
  bar.innerHTML = '';
  const mkChip = (extraCls, emoji, name, color, titleText) => {
    const c = document.createElement('span'); c.className = 'ctx-chip' + extraCls;
    if (emoji) { const e = document.createElement('span'); e.className = 'cc-emoji'; e.textContent = emoji; c.appendChild(e); }
    const n = document.createElement('span'); n.className = 'cc-name'; n.textContent = name; c.appendChild(n);
    if (color) { c.style.background = hexToRgba(color, 0.22); c.style.borderColor = color; }
    if (titleText) c.title = titleText;
    return c;
  };
  const proj = view.projectId ? (_serverProjects[view.projectId] || null) : null;
  if (proj) bar.appendChild(mkChip(' cc-project', proj.emoji || '', proj.name || 'Project', proj.color || '', proj.root || proj.name || ''));
  else bar.appendChild(mkChip(' cc-project muted', '', 'General', '', 'Not bound to a project'));
  const sep = document.createElement('span'); sep.className = 'ctx-sep'; sep.textContent = '›'; bar.appendChild(sep);
  const chat = (_lastSessions || []).find((s) => s.session_id === displayedChat) || null;
  const chatName = (chat && chat.title) ? chat.title : 'New chat';
  bar.appendChild(mkChip('', (chat && chat.emoji) || '', chatName, (chat && chat.color) || '', chatName));
  // A tight composer hides the muted project chip and ellipsizes the chat name, so the bar itself
  // carries the whole path -- the collapse is a display decision, never a loss of information.
  const projName = proj ? (proj.name || 'Project') : 'General';
  bar.title = projName + ' › ' + chatName;
}

// ---- Splitter boundaries ----
// Both splitters clamped against constants that knew nothing about the window they were in. A
// 680px Activity panel is fine on a 1900px display and destroys a 1000px one: measured at
// 1000x700, dragging the splitter to its limit left #main 60px wide and the composer input 26px.
// Below 980px the panel is an overlay instead, where the binding constraint is not #main's width
// but the header controls underneath it -- the stylesheet keeps the panel clear of the header, and
// OVERLAY_PEEK keeps it from becoming a full-bleed sheet with no chat left in view.
const SIDEBAR_MIN = 190, SIDEBAR_MAX = 520;
const PANEL_MIN = 260, PANEL_MAX = 680;
// Narrowest #main that still holds a readable control row and composer. Measured, not guessed:
// the composer row is #main minus 40px of footer padding, and inside it the send button, the two
// composer extras (emote 30px, mic 36px) and three 10px gaps are fixed, leaving the input. A 380px
// #main left exactly 176px of input — below the 200px a usable composer needs — so the floor is
// 205 (chrome + padding) + 200, rounded up to 410.
const MAIN_MIN = 410;
const SPLITTER_W = 5;        // #sideResize / #xpResize
const OVERLAY_PEEK = 56;     // chat left uncovered beside an overlaid panel
function layoutViewportWidth() { return document.documentElement.clientWidth || window.innerWidth || 0; }
function panelIsOverlay() {
  const xp = document.getElementById('xpanel');
  return !!xp && getComputedStyle(xp).position === 'fixed';
}
function panelIsOpen() { return document.body.classList.contains('panel-open'); }
function measuredWidth(id) {
  const el = document.getElementById(id);
  if (!el) return 0;
  if (getComputedStyle(el).display === 'none') return 0;
  return Math.round(el.getBoundingClientRect().width);
}
function splitterRoom() { return SPLITTER_W * (panelIsOpen() ? 2 : 1); }
function panelMaxWidth() {
  const vw = layoutViewportWidth();
  if (!vw) return PANEL_MAX;   // not laid out (hidden tab): leave the stored width alone
  if (panelIsOverlay()) return Math.max(PANEL_MIN, Math.min(PANEL_MAX, vw - OVERLAY_PEEK));
  return Math.max(PANEL_MIN, Math.min(PANEL_MAX, vw - measuredWidth('sidebar') - splitterRoom() - MAIN_MIN));
}
function sidebarMaxWidth() {
  const vw = layoutViewportWidth();
  if (!vw) return SIDEBAR_MAX;
  // An overlaid panel does not take room from #main, so it must not take room from the sidebar either.
  const panelRoom = (panelIsOpen() && !panelIsOverlay()) ? measuredWidth('xpanel') : 0;
  return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, vw - panelRoom - splitterRoom() - MAIN_MIN));
}
function applySidebarWidth(w) {
  const sb = document.getElementById('sidebar');
  if (!sb) return;
  sb.style.flex = '0 0 ' + w + 'px'; sb.style.width = w + 'px';
}
function applyPanelWidth(w) {
  const xp = document.getElementById('xpanel'), handle = document.getElementById('xpResize');
  if (!xp) return;
  xp.style.flex = '0 0 ' + w + 'px'; xp.style.width = w + 'px';
  // In overlay mode the handle is pinned (stylesheet) to the panel's left edge; in flow it is a
  // static flex item and ignores `right` entirely, so this is safe to set unconditionally.
  if (handle) handle.style.right = w + 'px';
}
function clampSidebarWidth(w) { return Math.max(SIDEBAR_MIN, Math.min(sidebarMaxWidth(), Math.round(w))); }
function clampPanelWidth(w) { return Math.max(PANEL_MIN, Math.min(panelMaxWidth(), Math.round(w))); }
// The window is resizable after a drag, so a width that was legal when it was chosen has to be
// re-checked whenever the room changes -- otherwise shrinking the window reproduces the defect
// with no drag involved at all. Panel first: the sidebar's own ceiling reads the panel's width.
function reclampLayout() {
  const xp = document.getElementById('xpanel'), sb = document.getElementById('sidebar');
  if (xp) applyPanelWidth(clampPanelWidth(parseInt(xp.style.width, 10) || measuredWidth('xpanel') || 340));
  if (sb) applySidebarWidth(clampSidebarWidth(parseInt(sb.style.width, 10) || measuredWidth('sidebar') || 250));
}
// The overlay panel starts below the header so the header's own Activity toggle stays clickable.
// Only script can know how tall that header actually is -- and sampling it once is not enough: the
// header wraps to a second row when #main gets narrow, which happens when the SIDEBAR is dragged
// out, long after load. Measured at 900x420 with a stored 520px sidebar, a value captured at boot
// said 51px against a header that had become 86px tall, and the panel covered the difference.
function syncHeaderHeight() {
  const header = document.querySelector('#main header');
  const root = document.documentElement;
  if (!header || !root.style || typeof root.style.setProperty !== 'function') return;
  const h = Math.round(header.getBoundingClientRect().height);
  if (h > 0) root.style.setProperty('--app-header-h', h + 'px');
}
function watchHeaderHeight() {
  const header = document.querySelector('#main header');
  if (!header || typeof ResizeObserver !== 'function') return;   // node harness has neither
  new ResizeObserver(syncHeaderHeight).observe(header);
}

// Draggable sidebar width (persisted) — fixes project/chat-name truncation at the default width.
function initSidebarResize() {
  const KEY = 'vool_sidebar_w';
  const sb = document.getElementById('sidebar');
  const handle = document.getElementById('sideResize');
  if (!sb || !handle) return;
  const clamp = clampSidebarWidth;
  const apply = applySidebarWidth;
  const stored = parseInt(localStorage.getItem(KEY) || '', 10);
  if (stored) apply(clamp(stored));
  let dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; handle.classList.add('dragging');
    document.body.style.userSelect = 'none'; document.body.style.cursor = 'col-resize'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    apply(clamp(e.clientX - sb.getBoundingClientRect().left));
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; handle.classList.remove('dragging');
    document.body.style.userSelect = ''; document.body.style.cursor = '';
    localStorage.setItem(KEY, String(parseInt(sb.style.width, 10) || 250));
  });
}
function initPanelResize() {
  const KEY = 'vool_panel_w';
  const xp = document.getElementById('xpanel');
  const handle = document.getElementById('xpResize');
  if (!xp || !handle) return;
  const clamp = clampPanelWidth;
  const apply = applyPanelWidth;
  const stored = parseInt(localStorage.getItem(KEY) || '', 10);
  apply(clamp(stored || measuredWidth('xpanel') || 340));
  let dragging = false;
  handle.addEventListener('mousedown', (e) => {
    dragging = true; handle.classList.add('dragging');
    document.body.style.userSelect = 'none'; document.body.style.cursor = 'col-resize'; e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    apply(clamp(xp.getBoundingClientRect().right - e.clientX));  // panel sits on the right: width = right edge − cursor
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; handle.classList.remove('dragging');
    document.body.style.userSelect = ''; document.body.style.cursor = '';
    localStorage.setItem(KEY, String(parseInt(xp.style.width, 10) || 340));
  });
}
function initLayoutBounds() {
  syncHeaderHeight();
  watchHeaderHeight();
  initSidebarResize();
  initPanelResize();
  // Resizing the window changes the room without touching either splitter, so the same boundaries
  // have to be re-applied there or a legal width silently becomes an illegal one.
  window.addEventListener('resize', () => { syncHeaderHeight(); reclampLayout(); syncComposerTail(); });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initLayoutBounds); else { initLayoutBounds(); }

// ---- Sidebar lifecycle indicators (WORKING / NEEDS_USER / COMPLETE_UNSEEN) ----
// "Seen" is a view concern, not runtime truth: which chat's completion the operator has actually
// looked at lives client-side, keyed by session id, and is never sent to the server.
const _seenAt = _jget('vool_seen_at_v1', '{}');
function markChatSeen(sid) {
  const id = String(sid || ''); if (!id) return;
  _seenAt[id] = new Date().toISOString();
  try { localStorage.setItem('vool_seen_at_v1', JSON.stringify(_seenAt)); } catch (e) {}
}
// Sidebar lifecycle rows omit detailed execution history. The Activity panel
// retains its own detailed projection for explicit inspection.
function _activityRowFor(sid) {
  for (const row of sidebarActivityHistory) { if (row && row.session_id === sid) return row; }
  for (let i = 0; i < activityHistory.length; i++) { if (activityHistory[i] && activityHistory[i].session_id === sid) return activityHistory[i]; }
  return null;
}
function sidebarLifecycleState(sid) {
  return chatLifecycleState(sid, {
    chatStates: chatStates,
    serverRow: _activityRowFor(sid),
    seenAt: _seenAt[String(sid || '')] || '',
    displayedChat: displayedChat,
    hidden: typeof document !== 'undefined' && !!document.hidden,
  });
}
// A project (or General) shows the loudest state any of ITS OWN chats carries -- never a global
// "something somewhere is running" reading, which is what would make an unrelated project look busy.
function aggregateLifecycleState(sessions) {
  let working = false, unseen = false;
  for (const s of (sessions || [])) {
    const st = sidebarLifecycleState(s.session_id);
    if (st === 'needs_user') return 'needs_user';
    if (st === 'working') working = true; else if (st === 'complete_unseen') unseen = true;
  }
  return working ? 'working' : (unseen ? 'complete_unseen' : '');
}
const LIFECYCLE_BADGES = {
  working: { cls: 'lc-working', text: 'RUN', title: 'Working — a turn is running now' },
  needs_user: { cls: 'lc-needs', text: 'ASK', title: 'Needs you — approval or confirmation required' },
  complete_unseen: { cls: 'lc-unseen', text: '', title: 'Finished — not yet viewed' },
};
function appendLifecycleBadge(parent, state) {
  const spec = LIFECYCLE_BADGES[state];
  if (!spec || !parent) return;
  const b = document.createElement('span');
  b.className = 'lc-badge ' + spec.cls;
  if (spec.text) b.textContent = spec.text;
  b.title = spec.title;
  parent.appendChild(b);
}
// Real evidence, polled -- never a manufactured animation. The cheap local tick re-derives from
// state this tab already has (a background run mutates its OWN bucket the instant an event arrives
// -- see the DISPATCHER block), so it catches this tab's own chats within ~1s with no network call.
// The slower tick is the only one that touches the network, refreshing the server's per-chat status
// for chats this tab has not opened itself.
let _lifecycleSnapshot = '';
function _lifecycleSnapshotKey() {
  const parts = [];
  for (const s of _lastSessions) { if (!s.archived) parts.push(s.session_id + ':' + sidebarLifecycleState(s.session_id)); }
  return parts.join('|');
}
function maybeRepaintSidebarLifecycle() {
  if (!sessionsEl) return;
  const active = document.activeElement;
  if (active && sessionsEl.contains(active) && active.tagName === 'INPUT') return;   // never blow away an in-progress rename
  const key = _lifecycleSnapshotKey();
  if (key === _lifecycleSnapshot) return;
  _lifecycleSnapshot = key;
  const top = sessionsEl.scrollTop;
  renderSessions(_lastSessions);
  sessionsEl.scrollTop = top;
}
async function refreshSidebarActivity() {
  try {
    const data = await fetchSidebarLifecycle();
    sidebarActivityHistory = Array.isArray(data.sessions) ? data.sessions : sidebarActivityHistory;
  } catch (e) { /* transient; the next tick retries */ }
  maybeRepaintSidebarLifecycle();
  await refreshReminderArtifacts(displayedChat);
}

// Background deliveries are transcript artifacts, not new user/model turns.
// Merge only their stable identities into the owning idle bucket. Never replace
// local history or splice a late response into a turn that started meanwhile.
const reminderRefreshes = new Set();
async function refreshReminderArtifacts(chatId) {
  const target = chatState(chatId);
  if (!target.history.length || (target.run && !target.run.ended) || reminderRefreshes.has(chatId)) return;
  const run = target.run, length = target.history.length;
  reminderRefreshes.add(chatId);
  try {
    const r = await fetch('/api/chat/history?session=' + encodeURIComponent(chatId) + '&event_kind=reminder_delivery');
    if (!r.ok) return;
    const data = await r.json();
    if (target.run !== run || target.history.length !== length || (target.run && !target.run.ended)) return;
    const key = (m) => m.artifact && m.artifact.kind === 'reminder_delivery' && m.artifact.reminder_id
      ? String(m.artifact.reminder_id) + ':' + String(m.artifact.status || '') : '';
    const seen = new Set(target.history.map(key).filter(Boolean));
    let changed = false;
    for (const m of (data.messages || [])) {
      const identity = key(m);
      if (m.role !== 'assistant' || !identity || !m.content || seen.has(identity)) continue;
      seen.add(identity);
      target.history.push(historyEntryFromServer(m));
      changed = true;
    }
    if (changed && isDisplayed(chatId)) renderChat(chatId);
  } catch (e) { /* preserve local state; the existing activity tick retries */ }
  finally { reminderRefreshes.delete(chatId); }
}

// True number of chats in a group, from the server's count over ALL chats. The rendered list is a
// capped page, so counting the page counts the window rather than the contents -- which is what
// made a delete look like it moved a chat between groups. Falls back to the page length only if a
// server has not sent counts yet.
function groupCount(pid, pageLength) {
  if (_sessionCounts && Object.prototype.hasOwnProperty.call(_sessionCounts, pid)) {
    return _sessionCounts[pid];
  }
  return pageLength;
}

function renderSessions(sessions) {
  // A chat that has sent its first message but is not yet in the server's log is overlaid here, so
  // it survives every re-render until the server's own record replaces it.
  _lastSessions = mergePendingSessions(sessions);
  // Adopt the DISPLAYED chat's project from the record the server just returned. Boot restores
  // the transcript but never the binding, so without this a restart showed "General" until the
  // chat was clicked. The server's project_id stays authoritative (openSession's own derivation
  // on click reads the same field); a chat the server has not listed yet is left untouched.
  const displayedRecord = _lastSessions.find((s) => s.session_id === displayedChat) || null;
  if (displayedRecord) view.projectId = displayedRecord.project_id || '';
  sessionsEl.innerHTML = '';
  const active = _lastSessions.filter((s) => !s.archived);
  const archived = _lastSessions.filter((s) => s.archived);
  const projects = loadProjects();
  const projectIds = Object.keys(projects);
  if (!active.length && !archived.length && !projectIds.length) {
    const none = document.createElement('div');
    none.className = 'side-empty';
    none.textContent = 'No past chats yet.';
    sessionsEl.appendChild(none);
    return;
  }
  const collapsed = loadCollapsed();
  const byProject = {}; const general = [];
  for (const s of active) {
    const pid = s.project_id;   // server-side binding
    if (pid && projects[pid]) { (byProject[pid] = byProject[pid] || []).push(s); }
    else general.push(s);
  }
  // General (unassigned) pinned to the TOP and collapsible, like a project group.
  const GEN_KEY = '__general__';
  if (general.length) {
    const genCollapsed = !!collapsed[GEN_KEY];
    const gh = document.createElement('div'); gh.className = 'proj-head general' + (genCollapsed ? ' collapsed' : '');
    const c = document.createElement('span'); c.className = 'proj-caret'; c.textContent = genCollapsed ? '▸' : '▾';
    const n = document.createElement('span'); n.className = 'proj-name'; n.textContent = 'General';
    // The true count of unbound chats, not how many of the capped page happen to be unbound.
    const cnt = document.createElement('span'); cnt.className = 'proj-count'; cnt.textContent = String(groupCount('', general.length));
    gh.appendChild(c); gh.appendChild(n); gh.appendChild(cnt);
    appendLifecycleBadge(gh, aggregateLifecycleState(general));
    gh.addEventListener('click', () => { const cc = loadCollapsed(); cc[GEN_KEY] = !cc[GEN_KEY]; saveCollapsed(cc); renderSessions(_lastSessions); });
    sessionsEl.appendChild(gh);
    if (!genCollapsed) { for (const s of general) sessionsEl.appendChild(makeSessionItem(s, false, projectIds.length > 0)); }
  }
  // Then each project (even empty, so you can move chats into it) as a collapsible group.
  for (const pid of projectIds) {
    const items = byProject[pid] || [];
    const isCollapsed = !!collapsed[pid];
    sessionsEl.appendChild(makeProjectHead(pid, projects[pid].name || 'Project', groupCount(pid, items.length), isCollapsed, aggregateLifecycleState(items)));
    if (!isCollapsed) { for (const s of items) sessionsEl.appendChild(makeSessionItem(s, false, true)); }
  }
  if (projectIds.length) {
    const pn = document.createElement('div');
    pn.className = 'side-note';
    pn.textContent = 'Chats inside a project work in that folder and stay isolated \u2014 files and memory \u2014 from other projects.';
    sessionsEl.appendChild(pn);
  }
  if (archived.length) {
    const head = document.createElement('div');
    head.className = 'arch-head';
    head.textContent = 'Archived (' + archived.length + ')';
    const box = document.createElement('div');
    box.style.display = 'none';
    head.addEventListener('click', () => { box.style.display = box.style.display === 'none' ? 'block' : 'none'; });
    for (const s of archived) box.appendChild(makeSessionItem(s, true, true));
    sessionsEl.appendChild(head);
    sessionsEl.appendChild(box);
  }
  renderContextBar();   // keep the project/chat context chips in sync with the latest data + selection
}

function startRename(item, titleEl, id) {
  const input = document.createElement('input');
  input.className = 's-rename';
  input.value = titleEl.textContent === '(new chat)' ? '' : titleEl.textContent;
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    if (save) await postSession(id, { title: input.value });
    loadSessions();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
  item.replaceChild(input, titleEl);
  input.focus(); input.select();
}

async function postSession(id, patch) {
  try {
    await fetch('/api/chat/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ session_id: id }, patch)),
    });
  } catch (e) {}
}

async function archiveSession(id, archived) {
  await postSession(id, { archived: archived });   // persist the state change immediately
  // Archiving the ACTIVE thread mid-stream: leave the live output + currentSessionId untouched and
  // let send()'s completion reload the sidebar (moving it after the stream finishes). Active + idle:
  // start a fresh chat so the archived thread leaves the active list. Any other thread: just refresh.
  if (id === displayedChat && isChatBusy(id)) return;
  if (archived && id === displayedChat) newChat();
  else loadSessions();
}

async function loadSessions() {
  try {
    await refreshProjects();   // server-side project list drives the sidebar grouping
    const r = await fetch('/api/chat/sessions');
    const data = await r.json();
    _sessionCounts = (data && typeof data.counts === 'object') ? data.counts : null;
    renderSessions(data.sessions || []);
  } catch (e) {}
}

// Paint a chat: its transcript from its OWN bucket, and -- if it owns a run -- that run's card,
// rebuilt from run state. This is the reattach path. It re-DRAWS; it never re-sends. A turn in
// flight keeps its fetch, its reader, its AbortController, its timers and its accumulated answer;
// all that changes is which DOM nodes it paints into.
// Named fragment call site: hand the council card fragment the bubbles this repaint just
// produced, so any transcript message carrying a council marker becomes that run's folded
// card, and any run still live in this chat gets its card back. Guarded -- the fragment
// may not be mounted, and a chat must still paint if it throws.
function repaintCouncilCards(chatId, rows) {
  if (!window.VoolCouncilCard) return;
  try { window.VoolCouncilCard.repaint(chatId, logEl, rows); } catch (e) {}
}
function renderChat(chatId) {
  const st = chatState(chatId);
  const run = st.run;
  if (run) detachRunDom(run);      // the old nodes are about to be thrown away
  logEl.innerHTML = '';
  if (!st.history.length && !run) { showEmpty(); repaintCouncilCards(chatId, []); return; }
  const councilRows = [];
  st.history.forEach((m, i) => {
    const el = addMsg(m.role, m.content, m.ts, m.display_metadata, m.attachments, m.request_id);
    if (m.role === 'assistant') councilRows.push({ el: el, textEl: msgTextEl(el), text: m.content });
    // Re-hang a finished run's card on the exact bubble it produced, matched by the index recorded
    // when its answer was appended -- never by comparing text, so two identical answers in one chat
    // cannot swap cards.
    if (run && run.ended && i === run.historyIndex) attachRunDom(run, el);
  });
  // A turn still in flight has no history entry yet: give it a live bubble seeded from run.text.
  if (run && !run.ended) attachRunDom(run, addMsg('assistant', run.text || '…'));
  repaintCouncilCards(chatId, councilRows);
  logEl.scrollTop = logEl.scrollHeight;
  // The transcript was just rebuilt, so any marks from an open search went with it.
  if (chatSearchOpen() && searchInputEl && searchInputEl.value.trim()) runChatSearch(true);
}

// Restore the single permission bar for the chat now on screen: show ITS pending request, or hide
// the bar entirely. A grant raised in a background chat is held in that chat's bucket and painted
// only when the user is actually looking at it.
function reflectPermBar() {
  const pending = view.pendingApproval;
  const run = view.run;
  // An awaiting-approval turn has already ended and freed its slot -- `released` says nothing about
  // whether the operator still owes it an answer, so it must not gate the bar.
  if (pending && run && (run.permission || run.status === 'awaiting_approval')) {
    showPermBar(displayedChat, { approval: pending, summary: run.action });
  } else {
    hidePermBar();
  }
}

async function openSession(id) {
  // Point the display at the target chat FIRST, so every `view.*` write below lands in that
  // chat's bucket and not in the one being left behind. No busy guard: a turn running in the chat
  // being LEFT keeps running, and one running in the chat being ENTERED is reattached, not restarted.
  const leaving = view.run;
  if (leaving && !leaving.ended) detachRunDom(leaving);
  setDisplayedChat(id);
  localStorage.setItem('vool.sessionId', displayedChat);
  markChatSeen(id);   // opening a chat is looking at it -- any completion it carried is acknowledged
  view.mode = loadModeForSession(displayedChat);
  reflectMode();
  // Opening a chat restores a browser preference only.  The mode travels in the next turn body;
  // passive navigation must not manufacture a mode receipt or a runtime task/session.
  // Nothing is CLEARED on a switch. The target chat's bucket already holds whatever that chat owns
  // -- its run, its grants, its recovered ledger -- and the chat being left keeps its own.
  // Restore this chat's project + its remembered model, so switching into a project brings back the
  // AI you last used there (guarded free/paid) without you re-picking it.
  view.projectId = ((_lastSessions.find((s) => s.session_id === id) || {}).project_id) || '';
  restoreChatModel(id);
  renderContextBar();   // reflect the project/chat you just switched into, immediately
  reflectModel();       // each chat owns its Auto stickiness -- show THIS chat's, don't wipe it
  // The provenance strip is a lens on the SERVER's per-chat selection: it must follow the chat
  // you just entered, not keep painting the boot chat's verdict until a full reload.
  refreshSelectionProvenance();
  await loadPins();     // load this chat's pins BEFORE rendering, so each message shows its pin state
  // Hydrate the transcript from the server ONLY for a chat with nothing in flight and nothing
  // already in its bucket. A chat that is mid-turn (or that this page has already run turns in)
  // owns the authoritative transcript in memory; refetching would drop the in-flight exchange.
  if (!view.run && !view.history.length) {
    try {
      const r = await fetch('/api/chat/history?session=' + encodeURIComponent(id));
      const data = await r.json();
      if (isDisplayed(id) && !view.run && !view.history.length) for (const m of (data.messages || [])) {
        if (m.role === 'assistant') {
          const normalized = splitAssistantDisplayContent(m.content, m.display_metadata);
          view.history.push({ ...historyEntryFromServer(m), content: normalized.content, display_metadata: normalized.display_metadata });
        } else view.history.push(historyEntryFromServer(m));
      }
    } catch (e) {}
  }
  if (!isDisplayed(id)) return;   // the user moved on again while we were fetching
  restoreStagedAttachments(id);   // a draft left unsent in this chat comes back with the chat
  renderChat(id);
  await refreshReminderArtifacts(id);
  if (!isDisplayed(id)) return;
  reflectComposer();
  reflectPermBar();
  document.body.classList.remove('sidebar-open');
  loadSessions();
  refreshQueue();
  await loadRecoveredLedger(); await loadActivityHistory(); renderPanel();
  inputEl.focus();
}

function newChat() {
  // No busy guard: a turn running in the chat being left keeps running in the background.
  // A minted id has never been seen, so chatState() hands back a FRESH bucket: empty transcript,
  // no run, no grant, no stickiness. Nothing needs clearing, and nothing the previous chat owned
  // can leak in.
  if (view.run && !view.run.ended) detachRunDom(view.run);
  setDisplayedChat(mintSessionId());
  restoreChatModel(displayedChat);
  localStorage.setItem('vool.sessionId', displayedChat);
  view.mode = loadModeForSession(displayedChat);
  reflectMode();
  // This is an unsent draft.  Keep its preference local until the user either explicitly changes
  // the mode or sends the first turn; merely pressing New must not create runtime Activity.
  activityHistory = [];   // the cross-session Activity list is a panel-wide view, not chat state
  renderContextBar();   // fresh chat in General -> update the context chips
  updatePinCount();     // a new chat has no pins
  reflectModel();       // a new chat starts back on local-first Auto (its bucket has no sticky)
  reflectComposer(); hidePermBar();
  showEmpty();
  document.body.classList.remove('sidebar-open');
  loadSessions();
  refreshQueue();
  inputEl.focus();
}

// ---- Per-project exact model memory, and a new chat inside a project ----
function modelForChat(chatId) {
  const choices = _jget('vool_chat_models_v1', '{}');
  return choices[String(chatId)] || 'vool';
}
function restoreChatModel(chatId) {
  modelValue = modelForChat(chatId);
}
function setModelValue(m, chatId = displayedChat) {
  const choices = _jget('vool_chat_models_v1', '{}');
  if (choices[String(chatId)] && choices[String(chatId)] !== (m || 'vool')) {
    try { paidPinAcknowledgements.delete(paidAckKey(chatId, choices[String(chatId)])); } catch (e) {}
  }
  choices[String(chatId)] = m || 'vool';
  modelSelectionRevisions[String(chatId)] = (modelSelectionRevisions[String(chatId)] || 0) + 1;
  localStorage.setItem('vool_chat_models_v1', JSON.stringify(choices));
  if (isDisplayed(chatId)) {
    modelValue = m || 'vool';
    try { reflectModel(); } catch (e) {}
  }
}
function _isLikelyFreeModel(m) {
  if (!m) return true;
  if (MODEL_LABELS[m]) return true;                 // local tiers are free
  if (m === 'vool' || m === 'auto') return true;
  return /:free\b/i.test(m);                          // free cloud variants carry the :free suffix
}
function loadProjectModels() { return _jget('vool_project_model', '{}'); }
function saveProjectModel(pid, m) { if (!pid) return; const s = loadProjectModels(); s[pid] = m; localStorage.setItem('vool_project_model', JSON.stringify(s)); }
function rememberActiveModel() { setModelValue(modelValue); if (view.projectId) saveProjectModel(view.projectId, modelValue); }
function applyProjectModel(pid) {
  const remembered = loadProjectModels()[pid];
  if (!remembered) return;
  // The operator already made an explicit project-scoped choice. Preserve it exactly; resetting a
  // paid pin to local here makes the composer lie. Auto still cannot spend because `vool` has a
  // separate verified-free fallback contract.
  setModelValue(remembered);
}
async function newChatInProject(pid) {
  // No busy guard, for the same reason as newChat(): the chat being left keeps its run.
  if (view.run && !view.run.ended) detachRunDom(view.run);
  const sid = mintSessionId();
  setDisplayedChat(sid); localStorage.setItem('vool.sessionId', sid);
  restoreChatModel(sid);
  view.mode = loadModeForSession(displayedChat);
  reflectMode();
  activityHistory = [];   // panel-wide cross-session list, not chat state
  view.projectId = pid || '';
  renderContextBar();
  updatePinCount();       // a minted chat's bucket is already empty -- nothing to clear
  reflectModel();
  reflectComposer(); hidePermBar();
  showEmpty();
  document.body.classList.remove('sidebar-open');
  if (pid) {
    let bound = false;
    try {
      const r = await fetch('/api/chat/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sid, project_id: pid }) });
      bound = r.ok;
    } catch (e) {}
    if (!bound) {
      // Fail closed: the bind did not confirm, so this chat is NOT isolated to the project. Reflect
      // the true (unbound) state instead of a false "bound" chip that would silently leak the
      // project's memory into a chat the UI called isolated.
      view.projectId = '';
      renderContextBar();
      toast('Could not put this chat in the project — it is NOT isolated. Try again.');
    } else {
      if (isDisplayed(sid)) applyProjectModel(pid);
    }
  }
  // Project binding may persist the chat container, but its mode remains a local draft preference
  // until an explicit mode change or the first task request.
  await loadSessions(); refreshQueue(); inputEl.focus();
}
let _toastTimer = null;
function toast(text) {
  let t = document.getElementById('nToast');
  if (!t) { t = document.createElement('div'); t.id = 'nToast'; t.className = 'n-toast'; document.body.appendChild(t); }
  t.textContent = text; t.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { t.classList.remove('show'); }, 6500);
}

async function restoreCurrent() {
  const chatId = displayedChat;          // the chat this restore belongs to, captured before the await
  const target = chatState(chatId);
  try {
    const r = await fetch('/api/chat/history?session=' + encodeURIComponent(chatId));
    const data = await r.json();
    // The operator may have sent a turn while this snapshot was in flight.
    // Appending OLD user messages after it changes the model's last question.
    // The live bucket wins; persisted history remains intact on the server.
    if (target.run || target.history.length) return;
    const msgs = data.messages || [];
    if (msgs.length) {
      for (const m of msgs) target.history.push(historyEntryFromServer(m));
      // One render path for every way a chat reaches the screen, so boot and re-open cannot drift.
      if (isDisplayed(chatId)) renderChat(chatId);
    } else if (isDisplayed(chatId)) {
      showEmpty();  // fresh/empty session -> the time-aware greeting, never a blank static screen
    }
  } catch (e) { if (isDisplayed(chatId) && !target.run && !target.history.length) showEmpty(); }
  restoreStagedAttachments(chatId);   // an unsent draft survives a reload with its chat
}

// =========================== Live execution UX ===========================
// Consumes the typed `vool_event` NDJSON channel (see core/task_event_model.py)
// to drive a live status card and the right-side execution panel. Card and
// panel read the SAME run state so they cannot disagree. The card's working
// mark (TC_MARK_SVG below) animates purely with CSS -- no timers, no canvas.
const PANEL_TABS = ['Steps', 'Activity', 'Agents', 'Changes', 'Files', 'Event log', 'Tests', 'Receipts', 'Council', 'Preview'];
let panelTab = 'Activity';
let lastTabSig = '';

function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60), r = s % 60;
  return m + 'm ' + (r < 10 ? '0' : '') + r + 's';
}
function fmtUsd(c) { if (!c) return ''; const u = c.usd_actual; if (typeof u !== 'number' || !(u > 0)) return ''; try { return u.toLocaleString(appLocale(), { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 }); } catch (e) { return '$' + u.toFixed(4); } }
// Agent durations are routinely sub-second -- a live-data fetch measured 491ms on the served
// surface -- and `fmtElapsed` floors to whole seconds, so every fast agent read "0s" and looked
// like it had not run. Below a second, report milliseconds; above it, defer to the shared
// formatter so an agent and its turn never disagree about what "2m 05s" means.
function fmtAgentDuration(ms) {
  if (ms == null) return '';
  if (ms < 1000) return Math.max(0, Math.round(ms)) + 'ms';
  return fmtElapsed(ms);
}
// One row per conductor node -- an "agent" -- folded from its lifecycle events.
//
// Reads the CHAT LEDGER (raw `/api/runtime/events` rows, where `details` is FLATTENED onto the
// row) rather than `run.events` (the task stream, keyed `type`). Both are accepted because the
// same record can arrive on either path, and a panel that silently reads the wrong one shows an
// empty list while the work is happening.
//
// Deliberately pure and DOM-free so it can be executed under node in a test. A renderer test that
// greps the page source for a class name proves the string is in the file; it cannot tell a
// working derivation from one that never runs.
//
// LIVENESS is the whole point: a node with a started event and no completed event is RUNNING, and
// its elapsed time is measured from its own start stamp. That is what answers "is an agent at
// work" while the agent is still at work.
function agentRowsFrom(events, nowMs) {
  const byNode = new Map();
  for (const ev of (events || [])) {
    if (!ev) continue;
    const kind = String(ev.event_type || ev.type || '');
    if (kind !== 'agent_node_started' && kind !== 'agent_node_completed') continue;
    const id = String(ev.node_id || '');
    if (!id) continue;
    let row = byNode.get(id);
    if (!row) {
      row = { nodeId: id, operation: '', planId: '', state: 'running', ok: null,
              startedAtIso: '', startedAtMs: null, durationS: null, elapsedMs: null,
              rendered: '', renderedTruncated: false, failureReason: '', dependsOn: [], lane: '', running: true };
      byNode.set(id, row);
    }
    if (ev.operation) row.operation = String(ev.operation);
    if (ev.lane) row.lane = String(ev.lane);
    if (ev.plan_id) row.planId = String(ev.plan_id);
    if (Array.isArray(ev.depends_on)) row.dependsOn = ev.depends_on.map(String);
    if (kind === 'agent_node_started') {
      row.startedAtIso = String(ev.started_at_iso || ev.created_at || '');
      const parsed = Date.parse(row.startedAtIso);
      row.startedAtMs = isNaN(parsed) ? null : parsed;
    } else {
      // A completed event always wins: a duplicate started arriving late must not resurrect a
      // finished node as "running".
      row.state = String(ev.state || 'finished');
      row.ok = ev.ok === true;
      row.durationS = (typeof ev.duration_s === 'number') ? ev.duration_s : null;
      row.rendered = String(ev.rendered == null ? '' : ev.rendered);
      row.renderedTruncated = ev.rendered_truncated === true;
      row.failureReason = String(ev.failure_reason || '');
      row.running = false;
    }
  }
  const rows = Array.from(byNode.values());
  for (const row of rows) {
    if (row.running) {
      row.elapsedMs = (row.startedAtMs && nowMs) ? Math.max(0, nowMs - row.startedAtMs) : null;
    } else if (row.durationS != null) {
      row.elapsedMs = Math.round(row.durationS * 1000);
    }
  }
  return rows;
}
// Header line for the Agents tab: how many are at work right now, out of how many this chat ran.
function agentSummaryOf(rows) {
  const list = rows || [];
  const running = list.filter((r) => r.running).length;
  const failed = list.filter((r) => !r.running && r.ok === false).length;
  return { total: list.length, running: running, failed: failed,
           done: list.length - running };
}
// Plain-text serialization for "copy all agent work". Built from the ROW MODEL, never from the
// DOM, so a collapsed or scrolled-away agent still copies in full.
function agentEvidenceText(rows, nowMs) {
  const list = rows || [];
  if (!list.length) return 'No agent activity recorded.';
  const sum = agentSummaryOf(list);
  const out = ['Agents - ' + sum.total + ' total, ' + sum.running + ' running, ' + sum.failed + ' failed', ''];
  for (const r of list) {
    const when = r.running
      ? ('running' + (r.elapsedMs != null ? ' for ' + fmtAgentDuration(r.elapsedMs) : ''))
      : (r.state + (r.elapsedMs != null ? ' in ' + fmtAgentDuration(r.elapsedMs) : ''));
    out.push(r.nodeId + ' [' + (r.operation || 'node') + '] - ' + when);
    if (r.dependsOn && r.dependsOn.length) out.push('  after: ' + r.dependsOn.join(', '));
    if (r.failureReason) out.push('  failed: ' + r.failureReason);
    if (r.rendered) {
      for (const line of String(r.rendered).split('\n')) out.push('  ' + line);
      if (r.renderedTruncated) out.push('  [truncated]');
    }
    out.push('');
  }
  return out.join('\n').trim();
}
function esc(t) { const d = document.createElement('div'); d.textContent = (t == null ? '' : String(t)); return d.innerHTML; }
function safeUrl(u) {
  try { const url = new URL(String(u), location.origin); return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : ''; }
  catch (e) { return ''; }
}
// Render assistant text with clickable links: markdown [label](url) becomes a short clickable
// source, bare URLs are auto-linked (truncated). Everything is HTML-escaped first, and only
// http/https URLs ever become anchors, so page content can never inject markup.
// Inline markup for one already-ESCAPED run of text: images, links, bare URLs, bold, italic and
// inline code. Escaping happens once, before any of this, so nothing here can inject markup.
function renderInline(html) {
  // Inline images: ![alt](target). A local render path (file:// or /abs) is served via /api/files/raw
  // (a browser can't load file:// from an http page); an https image (cloud) loads directly. Done
  // BEFORE link handling so ![alt](https://img) becomes an <img>, not a link. The raw local path is
  // kept in data-local for the right-click actions (open in Preview / reveal in Finder / copy).
  html = html.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (m, alt, target) => {
    const t = target.replace(/&amp;/g, '&');
    let src = '';
    let local = '';
    if (/^https?:\/\//i.test(t)) { src = t; }
    else if (/^file:\/\//i.test(t)) { local = t.replace(/^file:\/\//, ''); src = '/api/files/raw?path=' + encodeURIComponent(local); }
    else if (t.charAt(0) === '/') { local = t; src = '/api/files/raw?path=' + encodeURIComponent(local); }
    if (!src) return m;
    return '<img class="chat-img" src="' + esc(src) + '" alt="' + esc(alt || 'image') + '" loading="lazy" data-local="' + esc(local) + '" title="Click to open in Preview - right-click for more">';
  });
  html = html.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, label, url) => {
    const safe = safeUrl(url.replace(/&amp;/g, '&'));
    return safe ? '<a href="' + esc(safe) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>' : m;
  });
  html = html.replace(/(^|[^"'>=])(https?:\/\/[^\s<]+)/g, (m, pre, url) => {
    const clean = url.replace(/&amp;/g, '&');
    const safe = safeUrl(clean);
    if (!safe) return m;
    let disp = clean.replace(/^https?:\/\//, '').replace(/\/$/, '');
    if (disp.length > 48) disp = disp.slice(0, 45) + '…';
    return pre + '<a href="' + esc(safe) + '" target="_blank" rel="noopener noreferrer">' + esc(disp) + '</a>';
  });
  // Inline code is lifted out FIRST, so a `**` or a `_` inside a code span stays literal.
  const codes = [];
  html = html.replace(/`([^`\n]+)`/g, (m, body) => {
    codes.push(body);
    return ' CODE' + (codes.length - 1) + ' ';
  });
  html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  // `_` only between word boundaries, so snake_case_names are never italicised.
  html = html.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  html = html.replace(/ CODE(\d+) /g, (m, index) => '<code>' + codes[Number(index)] + '</code>');
  return html;
}

// Render assistant text as real Markdown.
//
// This function used to escape HTML, linkify, and turn "\n" into "<br>" - and nothing else. Every
// heading, bold run, bullet, code fence and table in an answer reached the operator as literal
// "##", "**", "-" and "|" characters, so a well-structured report looked like a broken one. No
// prompt change could fix that: the model was already emitting correct Markdown, and the renderer
// was throwing it away.
//
// Block-level parsing is line-based rather than a pile of regexes, because lists, tables and fenced
// code are stateful and regex-only versions break on the first ragged case. Escaping still happens
// exactly once, up front, so page content can never inject markup, and only http/https URLs ever
// become anchors.
function renderRichText(el, text) {
  const raw = text == null ? '' : String(text);
  el.dataset.raw = raw;
  // Index-aligned with `lines`: escaping adds and removes no newlines, so line i of the escaped
  // text is line i of the raw source. The fence branch uses this to carry the ORIGINAL code span
  // on the wrapper — the browser rewrites \r\n to \n inside innerHTML, so textContent can never
  // be the copy source for code that must arrive byte-exact.
  const rawLines = raw.split('\n');
  const lines = esc(raw).split('\n');
  const out = [];
  let i = 0;

  const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
  const isTableDivider = (line) => /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.indexOf('-') !== -1;
  const cells = (line) => {
    const source = line.trim().slice(1, -1);
    const result = [];
    let cell = '';
    for (let j = 0; j < source.length; j += 1) {
      // Escaped pipes are cell content. Consume paired backslashes together so
      // an even run before a pipe still leaves that pipe as a separator.
      if (source[j] === '\\' && (source[j + 1] === '\\' || source[j + 1] === '|')) {
        cell += source[j + 1];
        j += 1;
      } else if (source[j] === '|') {
        result.push(cell.trim());
        cell = '';
      } else {
        cell += source[j];
      }
    }
    result.push(cell.trim());
    return result;
  };
  const isBlockStart = (line) => (
    /^\s*(?:`{3,}|~{3,})/.test(line) || /^#{1,6}\s/.test(line) || /^\s*(?:&gt;|>)\s?/.test(line)
    || /^(\s*)[-*+]\s+/.test(line) || /^(\s*)\d+[.)]\s+/.test(line)
    || isTableRow(line) || /^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)
  );

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code. Contents are NEVER inline-processed: a code sample containing "**" or a URL must
    // survive exactly as written. The wrapper carries a Copy button (click is delegated once on the
    // log, so re-renders never rebinding anything) AND the original source span, URI-encoded:
    //
    //   fence-boundary rule — the copy payload is the exact characters strictly between the newline
    //   after the opening ``` marker and the newline before the closing ``` marker, joined with \n.
    //   \r characters ride at the ends of those lines, so a CRLF body stays CRLF; the newlines
    //   adjacent to the markers are not part of the payload (an empty first/last body line appears
    //   as an empty line, never as a swallowed newline). Longer backtick and tilde containers
    //   keep inner visual fences literal. The closer must match the character and length.
    //
    // encodeURIComponent round-trips every byte (\r \n \t, Unicode, quotes) exactly, so the
    // attribute is both injection-safe and a faithful carrier of the source span.
    const fence = line.match(/^\s*(`{3,}|~{3,})(\w*)\s*$/);
    if (fence) {
      const bodyStart = i + 1;
      const body = [];
      i += 1;
      while (i < lines.length) {
        const close = lines[i].match(/^\s*(`{3,}|~{3,})\s*$/);
        if (close && close[1][0] === fence[1][0] && close[1].length >= fence[1].length) break;
        body.push(lines[i]); i += 1;
      }
      const rawBody = rawLines.slice(bodyStart, i).join('\n');
      i += 1;
      const lang = fence[2] ? ' class="lang-' + esc(fence[2].toLowerCase()) + '"' : '';
      out.push('<div class="chat-code-wrap" data-raw-code="' + encodeURIComponent(rawBody) + '"><button type="button" class="code-copy" title="Copy this code block exactly">Copy</button><pre class="chat-code"><code' + lang + '>' + body.join('\n') + '</code></pre></div>');
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push('<h' + level + ' class="chat-h">' + renderInline(heading[2]) + '</h' + level + '>');
      i += 1;
      continue;
    }

    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
      out.push('<hr class="chat-hr">');
      i += 1;
      continue;
    }

    // The turn's provenance line (`core/response_provenance.py`), on its own. Recognised as a
    // block so it gets a class of its own: it is machine metadata about the answer, not part of
    // it, and on a narrow pane it must stay one readable line rather than reflowing into the
    // paragraph above. Checked BEFORE the table branch on purpose -- it is pipe-separated and sits
    // directly under an action table, which is the one place a row-consuming loop could eat it.
    if (/^\s*`(?:local|cloud|tool|runtime) \| [^`]*`\s*$/.test(line)) {
      out.push('<p class="chat-provenance">' + renderInline(line.trim()) + '</p>');
      i += 1;
      continue;
    }

    if (isTableRow(line) && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      const header = cells(line);
      i += 2;
      const body = [];
      while (i < lines.length && isTableRow(lines[i])) { body.push(cells(lines[i])); i += 1; }
      const head = header.map(c => '<th scope="col">' + renderInline(c) + '</th>').join('');
      const rows = body.map(r => '<tr>' + r.map(c => '<td>' + renderInline(c) + '</td>').join('') + '</tr>').join('');
      out.push('<div class="chat-table-wrap"><table class="chat-table"><caption class="sr-only">Data table</caption><thead><tr>' + head + '</tr></thead><tbody>' + rows + '</tbody></table></div>');
      continue;
    }

    if (/^\s*(?:&gt;|>)\s?/.test(line)) {
      const body = [];
      // Escaping runs BEFORE this parser, so the marker arrives as `&gt;`. Matching only `>`
      // meant no blockquote ever rendered — found by executing the shipped code, not by
      // reading it.
      while (i < lines.length && /^\s*(?:&gt;|>)\s?/.test(lines[i])) { body.push(lines[i].replace(/^\s*(?:&gt;|>)\s?/, '')); i += 1; }
      out.push('<blockquote class="chat-quote">' + renderInline(body.join('<br>')) + '</blockquote>');
      continue;
    }

    const bullet = line.match(/^(\s*)[-*+]\s+(.*)$/);
    const numbered = line.match(/^(\s*)\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const ordered = !!numbered;
      const items = [];
      while (i < lines.length) {
        const m = ordered ? lines[i].match(/^(\s*)\d+[.)]\s+(.*)$/) : lines[i].match(/^(\s*)[-*+]\s+(.*)$/);
        if (!m) break;
        const parts = [m[2]];
        i += 1;
        // A wrapped continuation line belongs to the item it follows, not to a new paragraph.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !isBlockStart(lines[i])) {
          parts.push(lines[i].trim());
          i += 1;
        }
        items.push('<li>' + renderInline(parts.join(' ')) + '</li>');
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push('<' + tag + ' class="chat-list">' + items.join('') + '</' + tag + '>');
      continue;
    }

    if (!line.trim()) { i += 1; continue; }

    // Everything else is a paragraph: consecutive non-blank lines that started no block.
    const para = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    // FORWARD PROGRESS. `isBlockStart` recognises more shapes than the branches above consume, and
    // a line it claims but none of them took leaves `para` empty with `i` unmoved -- the outer loop
    // then re-reads the same line forever, pushing an empty <p> each pass until the tab dies. Two
    // real inputs reached it: a table row with no divider under it (`| Car | Speed |` on its own,
    // which isTableRow accepts but the table branch rejects for lack of a divider), and a fence
    // whose info string carries anything but word characters (```json title="x"). Consuming the
    // line as literal text keeps every iteration strictly advancing, whatever new shape
    // isBlockStart learns to recognise later.
    if (!para.length) {
      para.push(lines[i]);
      i += 1;
    }
    out.push('<p class="chat-p">' + renderInline(para.join('<br>')) + '</p>');
  }

  let html = out.join('');
  // Drop a bare local render path that trails the image (the fast-path echoes it) - the image and
  // its actions replace it; showing the raw path as text is just noise.
  html = html.replace(/(<img class="chat-img"[^>]*>)\s*(?:<br>)?\s*\/[^\s<]+\.(?:png|jpe?g|webp|gif)\b/gi, '$1');
  el.innerHTML = html;
}

// Render machine-authored response metadata beside the answer without merging it into the bytes
// copied by the user or sent back to a model. This replaces the legacy convention where a
// provenance footer was appended to assistant prose and therefore became conversation history.
function renderAssistantContent(el, text, displayMetadata) {
  const normalized = splitAssistantDisplayContent(text, displayMetadata);
  renderRichText(el, normalized.content);
  // A verifier verdict suppressed from the answer bytes by an output contract still reaches the
  // reader HERE, beside the answer rather than inside it. Measured: a flagged one-word answer
  // rendered as a bare word -- the only trace of the failed review was an Activity row nobody
  // reads mid-conversation, and the wrong answer wore the same face as a right one.
  const vp = (normalized.display_metadata && normalized.display_metadata.verifier_presentation) || null;
  if (vp && vp.review_flagged) {
    const warn = document.createElement('p'); warn.className = 'chat-provenance chat-verifier-flag';
    const code0 = document.createElement('code');
    code0.textContent = '\u26a0 review flagged \u2014 double-check this answer';
    warn.appendChild(code0); el.appendChild(warn);
  }
  const footer = String((normalized.display_metadata && normalized.display_metadata.provenance_footer) || '').trim();
  if (!footer) { if (vp && vp.review_flagged) { el.dataset.raw = normalized.content; } return; }
  const p = document.createElement('p'); p.className = 'chat-provenance';
  const code = document.createElement('code');
  code.textContent = footer.replace(/^`|`$/g, '');
  p.appendChild(code); el.appendChild(p);
  // renderRichText deliberately set this to canonical content. Keep copy/pin/history behavior on
  // that authority even though the metadata is now visible in the same bubble.
  el.dataset.raw = normalized.content;
}

// ---- Proof Chip: one served turn's evidence, beside the answer ----
// Everything shown here is projected server-side (GET /api/chat/proof) from the stores that own
// it, bound to the canonical (session, request) pair of THIS message. The page's only job is to
// render the document it is given: the record label names the server state, missing evidence renders as
// missing, and a message with no request id (legacy rows, replay frames) gets no chip at all —
// an absent identity is never invented, and an unbound answer never borrows another turn's proof.
function mountAnswerExport(host, chatId, requestId) {
  if (!host || !chatId || !requestId || host.querySelector('.msg-export-pdf')) return;
  const actions = host.querySelector('.msg-actions');
  if (!actions) return;
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'msg-act msg-export-pdf';
  button.textContent = '\u2193'; button.title = 'Download this answer as PDF';
  button.setAttribute('aria-label', button.title);
  const status = document.createElement('span');
  status.className = 'msg-export-status'; status.setAttribute('role', 'status');
  button.addEventListener('click', async () => {
    button.disabled = true; status.textContent = '';
    try {
      const nativeApi = window.pywebview && window.pywebview.api;
      if (nativeApi && typeof nativeApi.save_answer_pdf === 'function') {
        const result = await nativeApi.save_answer_pdf(chatId, requestId);
        if (!result.ok && !result.cancelled) throw new Error(result.message || result.error || 'PDF save failed');
        status.textContent = result.cancelled ? 'Cancelled' : 'Saved';
      } else {
        const response = await fetch('/api/chat/export?session=' + encodeURIComponent(chatId)
          + '&request_id=' + encodeURIComponent(requestId) + '&format=pdf');
        if (!response.ok) {
          const refusal = await response.json();
          throw new Error(refusal.message || refusal.error || 'PDF export failed');
        }
        const blob = await response.blob();
        if (!/^application\/pdf\b/i.test(blob.type)) throw new Error('The server did not return a PDF');
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = 'vool-answer.pdf';
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        status.textContent = 'Downloaded';
      }
    } catch (error) { status.textContent = error.message || 'PDF export failed'; }
    finally { button.disabled = false; }
  });
  actions.appendChild(button); host.appendChild(status);
}

async function mountProofChip(host, chatId, requestId, presentationSelection) {
  if (typeof mountAnswerExport === 'function') mountAnswerExport(host, chatId, requestId);
  const id = String(requestId || '');
  if (!host || !id) return null;
  let proof = null;
  try {
    const r = await fetch('/api/chat/proof?session=' + encodeURIComponent(chatId || '') + '&request_id=' + encodeURIComponent(id));
    if (r.ok) proof = await r.json();
  } catch (e) { proof = null; }
  if (!proof || proof.bound !== true) return null;
  const existing = host.querySelector('.proof-chip');
  if (existing) existing.remove();
  const chip = document.createElement('div');
  chip.className = 'proof-chip';
  const compact = (proof && typeof proof.compact === 'object' && proof.compact) || {};
  const state = String(proof.state || 'UNVERIFIED');
  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'proof-chip-head';
  head.title = 'Run record — saved output integrity, actions, sources and cost; not an independent check of every answer claim';
  const cost = (typeof compact.cost === 'object' && compact.cost) || {};
  const costText = cost.display ? String(cost.display) : cost.usd != null ? ('$' + Number(cost.usd).toFixed(4)) : (cost.tokens != null ? (compact.cost.tokens + ' tok') : 'cost not recorded');
  const seg = (cls, text) => { const span = document.createElement('span'); span.className = cls; span.textContent = text; return span; };
  head.appendChild(seg('pc-actions', String(compact.actions == null ? 0 : compact.actions) + ' action' + (compact.actions === 1 ? '' : 's')));
  head.appendChild(seg('pc-sep', '·'));
  head.appendChild(seg('pc-sources', String(compact.sources == null ? 0 : compact.sources) + ' source' + (compact.sources === 1 ? '' : 's')));
  head.appendChild(seg('pc-sep', '·'));
  head.appendChild(seg('pc-cost', costText));
  head.appendChild(seg('pc-sep', '·'));
  head.appendChild(seg('pc-state pc-state-' + String(state).toLowerCase(), String(compact.state_label || ('Record ' + state.toLowerCase()))));
  // Coverage, verbatim from the server: what this turn looked up and whether every derived step
  // was fed by those lookups. The state word above certifies the bytes; this names their scope.
  // A projection without the field renders no segment -- the page never invents coverage.
  const coverage = (typeof compact.coverage === 'object' && compact.coverage) || null;
  if (coverage && coverage.label) {
    head.appendChild(seg('pc-sep', '·'));
    head.appendChild(seg('pc-coverage', String(coverage.label)));
  }
  const body = document.createElement('div');
  body.className = 'proof-chip-body';
  body.hidden = true;
  renderProofChipBody(body, proof, presentationSelection);
  head.addEventListener('click', () => {
    body.hidden = !body.hidden;
    chip.classList.toggle('open', !body.hidden);
  });
  chip.appendChild(head);
  chip.appendChild(body);
  host.appendChild(chip);
  return chip;
}

function _proofRow(label, value) {
  return '<div class="pc-row"><b>' + esc(label) + ':</b> ' + (value == null || value === '' ? '<span class="pc-none">not recorded</span>' : value) + '</div>';
}

function _proofElapsed(ms) {
  if (ms == null) return '';
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return Math.round(ms / 60000) + 'm';
}

function renderProofChipBody(body, proof, presentationSelection) {
  const expanded = (proof && typeof proof.expanded === 'object' && proof.expanded) || {};
  const reasons = Array.isArray(proof.state_reasons) ? proof.state_reasons : [];
  let html = _proofRow('Verification scope', esc(String(proof.verification_explanation ||
    'Checks saved output integrity and recorded execution evidence. These checks do not independently verify every factual or numerical claim.')));
  // C19 presentation-selection provenance, read-only beside the proof. The
  // record travels in this message's own display_metadata — a turn without
  // one (legacy row, replay) renders nothing here and never borrows.
  const selection = (presentationSelection && typeof presentationSelection === 'object' && presentationSelection.schema) ? presentationSelection : null;
  if (selection) {
    html += '<div class="pc-head">Presentation</div>';
    const shape = selection.disabled_by
      ? 'not elected (' + esc(String(selection.disabled_by)) + ')'
      : esc(String(selection.elected || 'prose')) + (selection.gap_detected ? ' (elected against prose)' : '');
    const why = selection.trigger && selection.trigger !== 'none_justified'
      ? ' · trigger ' + esc(String(selection.trigger))
      : (selection.trigger === 'none_justified' ? ' · no shape justified' : '');
    const fell = selection.fallback ? ' · ' + esc(String(selection.fallback)) : '';
    html += _proofRow('Automatic', shape + why + fell);
  }
  html += _proofRow('Model', esc([expanded.provider, expanded.model].filter(Boolean).join(' / ')));
  const actions = Array.isArray(expanded.actions) ? expanded.actions : [];
  if (actions.length) {
    html += '<div class="pc-head">Actions</div>';
    for (const action of actions) {
      html += '<div class="pc-row">' + esc(action.name || 'unnamed') + ' — <span>' + esc(action.outcome || (action.ok ? 'executed' : 'failed')) + '</span>'
        + (action.status && action.status !== action.outcome ? ' <span>(' + esc(action.status) + ')</span>' : '') + '</div>';
    }
  } else {
    html += '<div class="pc-head">Actions</div><div class="pc-row pc-none">no tool or effect ran on this turn</div>';
  }
  const sources = Array.isArray(expanded.sources) ? expanded.sources : [];
  if (sources.length) {
    html += '<div class="pc-head">Sources</div>';
    for (const source of sources) {
      html += '<div class="pc-row">' + esc(source.host || 'local source') + (source.operation ? ' — ' + esc(source.operation) : '') + (source.status ? ' <span>(' + esc(source.status) + ')</span>' : '') + '</div>';
    }
  }
  const timeline = Array.isArray(expanded.timeline) ? expanded.timeline : [];
  if (timeline.length) {
    // Stage windows from the turn's own event stamps -- no first-token time is invented.
    html += '<div class="pc-head">Timing</div>';
    for (const row of timeline) {
      html += '<div class="pc-row">' + esc(row.stage || 'stage') + ' — <span>' + (row.ms == null ? 'not recorded' : _proofElapsed(row.ms)) + '</span></div>';
    }
  }
  const lookups = Array.isArray(expanded.observations) ? expanded.observations : [];
  if (lookups.length) {
    html += '<div class="pc-head">Lookups</div>';
    for (const row of lookups) {
      let outcome;
      if (row.pending) outcome = 'pending';
      else if (!row.ok) outcome = 'failed' + (row.failure_code ? ' (' + esc(String(row.failure_code)) + ')' : '');
      else if (row.kind === 'derived') outcome = row.bound ? 'bound to this turn\'s lookups' : 'unbound — an operand is not a lookup of this turn';
      else outcome = 'observed';
      const where = row.kind === 'derived'
        ? (Array.isArray(row.depends_on) && row.depends_on.length ? 'from ' + esc(row.depends_on.join(', ')) : 'self-contained')
        : esc(row.host || 'no external source');
      const took = (typeof row.duration_s === 'number') ? ' · ' + _proofElapsed(Math.round(row.duration_s * 1000)) : '';
      html += '<div class="pc-row">' + esc(row.operation || row.node_id || 'node') + ' — ' + where + ' — <span>' + outcome + '</span>' + took + '</div>';
    }
  }
  const claims = (typeof expanded.claims === 'object' && expanded.claims) || null;
  if (claims) {
    html += '<div class="pc-head">Claims</div>';
    html += _proofRow('Supported by bound evidence', String(claims.supported_claim_count == null ? '' : claims.supported_claim_count));
    const withheld = Array.isArray(claims.withheld_claims) ? claims.withheld_claims.filter(Boolean) : [];
    if (withheld.length) html += _proofRow('Withheld (unsupported)', esc(withheld.join('; ')));
  }
  const refusals = Array.isArray(expanded.refusals) ? expanded.refusals : [];
  if (refusals.length) {
    html += '<div class="pc-head">Refusals</div>';
    for (const refusal of refusals) html += '<div class="pc-row">' + esc(refusal.name || 'unnamed') + ' — ' + esc(refusal.status || 'refused') + '</div>';
  }
  html += _proofRow('Elapsed', esc(_proofElapsed(expanded.elapsed_ms)));
  const finalization = (typeof expanded.finalization === 'object' && expanded.finalization) || {};
  if (finalization.finalization_id) {
    html += _proofRow('Finalization', '<code>' + esc(finalization.finalization_id) + '</code>'
      + (finalization.availability && finalization.availability !== 'available' ? ' <span>(' + esc(finalization.availability) + ')</span>' : ''));
  }
  const witness = (typeof expanded.witness === 'object' && expanded.witness) || {};
  if (witness.consistent === false) html += _proofRow('Ledger witness', '<span>inconsistent — ' + esc((witness.missing || []).join(', ')) + '</span>');
  if (reasons.length) html += _proofRow('Why ' + esc(String(proof.state)), esc(reasons.join(', ')));
  const receipts = Array.isArray(expanded.receipts) ? expanded.receipts.filter(Boolean) : [];
  if (receipts.length) {
    html += '<div class="pc-head">Receipts</div>';
    for (const receipt of receipts) html += '<div class="pc-row"><code>' + esc(receipt) + '</code></div>';
  }
  body.innerHTML = html;
}

// ---- Chat image actions: click opens in Preview; right-click for Open / Reveal / Copy ----
async function _fileOpen(path, reveal) {
  try { await fetch('/api/files/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: path, reveal: !!reveal }) }); } catch (e) {}
}
function openImageNative(local) { if (local) _fileOpen(local, false); }   // default app (Preview)
function revealImage(local) { if (local) _fileOpen(local, true); }        // Finder
async function copyImageToClipboard(img) {
  try {
    const blob = await (await fetch(img.src)).blob();
    if (navigator.clipboard && window.ClipboardItem) { await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]); return; }
  } catch (e) {}
  try { if (img.dataset.local) await navigator.clipboard.writeText(img.dataset.local); } catch (e) {}
}
function _imgMenuClose() { const m = document.getElementById('imgMenu'); if (m) m.remove(); }
function openImageMenu(img, x, y) {
  _imgMenuClose();
  const local = img.dataset.local || '';
  const menu = document.createElement('div'); menu.className = 'proj-menu'; menu.id = 'imgMenu';
  const add = (label, fn) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'proj-menu-item'; b.textContent = label; b.addEventListener('click', (e) => { e.stopPropagation(); _imgMenuClose(); fn(); }); menu.appendChild(b); };
  if (local) add('Open in Preview', () => openImageNative(local));
  if (local) add('Show in Finder', () => revealImage(local));
  add('Copy image', () => copyImageToClipboard(img));
  if (local) add('Copy path', () => { try { navigator.clipboard.writeText(local); } catch (e) {} });
  document.body.appendChild(menu);
  menu.style.top = Math.min(y, window.innerHeight - 170) + 'px';
  menu.style.left = Math.min(x, window.innerWidth - 190) + 'px';
  setTimeout(() => document.addEventListener('click', _imgMenuClose, { once: true }), 0);
}
if (logEl) {
  logEl.addEventListener('click', (e) => {
    const img = e.target.closest && e.target.closest('img.chat-img');
    if (img && img.dataset.local) { e.preventDefault(); openImageNative(img.dataset.local); }
    // Code-block Copy is delegated here once, so message re-renders can never drop it. The
    // clipboard payload is the ORIGINAL source span the renderer carried on the wrapper —
    // never the DOM's textContent, which innerHTML parsing normalizes (CRLF becomes LF).
    const codeBtn = e.target.closest && e.target.closest('.chat-code-wrap .code-copy');
    if (codeBtn) {
      const wrap = codeBtn.closest('.chat-code-wrap');
      if (wrap) {
        const encoded = wrap.getAttribute('data-raw-code');
        if (encoded != null) { copyText(decodeURIComponent(encoded), codeBtn); }
        else {
          const codeEl = wrap.querySelector('pre.chat-code code');
          if (codeEl) copyText(codeEl.textContent, codeBtn);
        }
      }
    }
  });
  logEl.addEventListener('contextmenu', (e) => {
    const img = e.target.closest && e.target.closest('img.chat-img');
    if (img) { e.preventDefault(); openImageMenu(img, e.clientX, e.clientY); }
  });
}

// A run for one chat. `chatId` is supplied by the caller (never read off the display) and stamped
// by adoptRun(); the mode is the OWNING chat's, so a run carries the permission posture of the
// chat that started it even if the composer is showing something else by the time it finishes.
function newRun(chatId) {
  // 'Request sent' is the client's own fact; acceptance is the server's, and arrives as the typed
  // task.started ('Understanding the request').
  return { start: Date.now(), stage: 'Queued', action: 'Request sent', last: '', status: 'running', activeMode: chatState(chatId).mode,
    model: null, cost: null, permission: false, steps: [], events: [], files: {}, tests: { done: 0, total: 0, failed: 0 },
    preview: null, card: null, refs: null, snake: null, timer: null, aborter: null, ended: false, verifying: false, reviewState: 'not_run', released: false,
    // The chat this run BELONGS to, stamped once by adoptRun() and never re-read from the display.
    // Every ownership decision downstream (history append, completion, failure, events, cancel,
    // approval) resolves through it -- see ownerOf() in the DISPATCHER block.
    tokens: 0, evented: false, chatId: '', eventSeq: 0, eventSeen: {},
    // Ledger-backed Activity: the server tags every runtime event with this turn id, so we can poll
    // /api/runtime/events and scope the timeline to THIS turn (scope, classify, model, tools, timeouts).
    // `ledgerSeen` is only the projection for THIS turn. Chat-wide cursor/identity/busy state lives
    // on newChatState(), otherwise every subsequent turn replays the whole session from seq zero.
    ledger: [], ledgerSeen: {}, ledgerTimer: null,
    // The chat's highest seq at the moment this turn started. An untagged event (a mode change, an
    // approval) carries no turn id, so this is the only way to tell one that happened DURING this
    // turn from one that happened in this chat an hour ago. Without it every untagged event in the
    // chat's history was replayed into every later turn -- measured at 12 inherited mode_changed
    // rows in one chat, which is what made a short work log look like nothing but mode changes.
    ledgerBaseline: 0,
    // Nested Activity tree expand/collapse state, keyed by node id -- see renderActivityTree().
    // Absent key = never touched by the user, so the tree's own open-by-default rules apply.
    activityOpen: {},
    // The DOM this run is currently painting into. Owned by the DISPLAY, not by the run: nulled by
    // detachRunDom() when its chat leaves the screen and rebuilt by attachRunDom() when it comes
    // back. Every paint reads these THROUGH the run (never a closure variable captured at start),
    // which is what lets a mid-stream re-open swap in fresh nodes without restarting anything.
    assistantMsgEl: null, textEl: null,
    // Answer text so far, and where the finished answer landed in the chat's transcript.
    text: '', historyIndex: -1, endedAt: '', endSummary: '', responseCommit: null,
    responseCommitRevision: 0, displayMetadata: {},
    turnId: ((self.crypto && crypto.randomUUID) ? crypto.randomUUID() : ('t-' + Date.now() + '-' + Math.random().toString(36).slice(2))) };
}

// Called at a turn's true end (stream closed). Frees THAT CHAT's run slot and pumps THAT CHAT's
// queue. The composer is never disabled (a send while the chat is busy is queued, not dropped), so
// busy only decides run-now vs enqueue -- and it is per chat, so a turn finishing in one chat
// neither frees nor blocks another. A stale stream from a superseded turn must not free the slot a
// newer run in the same chat holds, which is what the `run !== owner.run` check is for.
function releaseComposer(run) {
  const owner = run ? ownerOf(run) : view;
  if (run && run !== owner.run) return;
  if (run) run.released = true;
  owner.pumpHold = false;
  // The composer belongs to the chat on screen. A background chat finishing must not repaint the
  // Send button of the chat the user is actually typing in.
  reflectComposer();
  if (isDisplayed(owner.chatId)) { try { setCloudWorking(false); } catch (e) {} }
  if (owner.resumeApprovedTurn) { setTimeout(() => resumeApprovedTurn(owner.chatId), 0); return; }
  if (typeof pumpQueue === 'function') pumpQueue(owner.chatId);
}

// The Send button reflects the DISPLAYED chat's slot, and nothing else. This is the line that used
// to read a process-global flag, which is why one chat's long turn made every chat look busy.
// The control row is a sibling of the composer row, so CSS alone cannot tell it how much room the
// Send button takes. This publishes that width as `--composer-tail`, which the row reserves as
// right padding -- making its content edge the textarea's right edge, so the model selector anchors
// to the composer instead of floating at whatever x the controls to its left happen to end at.
function gapOf(el) {
  if (!el) return 0;
  const styles = getComputedStyle(el);
  return parseFloat(styles.columnGap || styles.gap || '0') || 0;
}
function syncComposerTail() {
  const footer = sendEl && typeof sendEl.closest === 'function' ? sendEl.closest('footer') : null;
  const row = document && typeof document.querySelector === 'function' ? document.querySelector('.composer-row') : null;
  const bar = document && typeof document.querySelector === 'function' ? document.querySelector('.control-bar') : null;
  if (!footer || !sendEl || !row || !bar) return;
  const width = sendEl.getBoundingClientRect().width;
  if (!width) return;                       // not laid out yet; a 0 reserve would mis-anchor the row
  // What the Send button occupies (itself plus the composer's gap), less the control row's OWN gap,
  // which already sits between the model group and this reserve. Without that subtraction the
  // selector lands a constant 8px short of the textarea's edge at every size.
  const reserve = width + gapOf(row) - gapOf(bar);
  footer.style.setProperty('--composer-tail', Math.max(0, Math.round(reserve)) + 'px');
}
// The council owns the machine's model pin for the length of a run: seat dispatch swaps
// the ONE global pin per seat and restores the operator's in a `finally`. A turn sent
// meanwhile could answer on whichever seat model was pinned at that instant, silently.
//
// The pin is one fact about this MACHINE, so the lock is too: every chat and every tab is
// closed for the duration, not just the one the council was convened from. (C3 locked the
// originating chat only -- the browser half of a hole the server has since closed.)
//
// This flag is a REFLECTION, never the authority. It is set from /api/council/lock and
// from the server's own typed 409, and the server refuses the turn whatever the DOM says
// -- which is what makes a second tab, a stale poll, or a client that never loaded this
// script equally unable to run a turn under a seat's pin.
let councilLockActive = false, councilLockNote = '';
function councilOwnsComposer() { return councilLockActive; }
function setCouncilLock(locked, note) {
  councilLockActive = !!locked;
  councilLockNote = String(note || '');
  reflectComposer();
}
function reflectComposer() {
  const locked = councilOwnsComposer();
  if (inputEl) inputEl.disabled = locked;
  if (sendEl) {
    sendEl.disabled = locked;
    sendEl.textContent = locked ? pageT('composer.council', 'Council')
      : (isChatBusy(displayedChat) ? pageT('composer.queue', 'Queue') : pageT('composer.send', 'Send'));
  }
  const lockEl = document.getElementById('councilLock');
  if (lockEl) {
    lockEl.hidden = !locked;
    if (locked) lockEl.textContent = councilLockNote;
  }
  // The attachment strip belongs to the chat on screen exactly like the Send button does. The
  // typeof guard keeps function-slicing harnesses (which lift this function alone) running.
  if (typeof renderAttachStrip === 'function') renderAttachStrip();
  // "Queue" is wider than "Send", so the reserve is re-read whenever the label changes.
  syncComposerTail();
}

// Timers belong to the RUN's lifetime, not to the display: a background turn must keep ticking its
// elapsed clock and draining its Activity ledger while the user is in another chat. Started once
// when the turn starts, cleared once when it ends -- never on navigation.
function startRunTimers(run) {
  if (run.timer || run.ledgerTimer) return;
  run.timer = setInterval(() => {
    if (run.ended) return;
    if (!run.refs) return;   // off screen: the clock still runs, there is just nothing to paint it on
    run.refs.elapsed.innerHTML = '<b>' + fmtElapsed(Date.now() - run.start) + '</b> elapsed';
  }, 1000);
  // Poll the runtime ledger so the Activity panel shows the real timeline (scope, classify, model,
  // tools, timeouts, terminal), scoped to this turn. Only while the panel is open AND this run's
  // chat is the one on screen -- otherwise the panel would be reading a chat nobody is looking at.
  // The run's own steps/events keep accruing from the stream regardless, so nothing is lost.
  run.ledgerTimer = setInterval(() => {
    if (!run.ended && isDisplayed(run.chatId) && document.body.classList.contains('panel-open')) pollLedger(run);
  }, 1400);
}
function clearRunTimers(run) {
  if (run.timer) { clearInterval(run.timer); run.timer = null; }
  if (run.ledgerTimer) { clearInterval(run.ledgerTimer); run.ledgerTimer = null; }
}

// Drop this run's DOM without touching a byte of its state. Called when its chat leaves the screen:
// the stream, the timers, the steps, the accumulated answer and the AbortController all keep going.
function detachRunDom(run) {
  if (!run) return;
  // The mark is pure CSS on the card's own DOM: dropping the node stops every animation -- there
  // is no timer, rAF handle or other off-node state to leak between sends.
  run.card = null; run.refs = null;
  run.assistantMsgEl = null; run.textEl = null;
}

// Rebuild this run's bubble + card on `host` and repaint everything it already knows. This is the
// reattach path: opening a chat whose turn is still in flight lands here, and the turn itself is
// never re-sent, re-created or reset -- only re-drawn.
function attachRunDom(run, host) {
  run.assistantMsgEl = host;
  run.textEl = msgTextEl(host);
  buildCard(run, host);
  paintRunState(run);
}

// Replay everything the run already knows onto fresh refs: answer text, live card, and -- for a
// turn that ended while its chat was off screen -- its terminal state.
function paintRunState(run) {
  if (run.textEl) {
    if (run.text) {
      if (run.assistantMsgEl) run.assistantMsgEl.classList.remove('pending');
      if (run.ended) renderAssistantContent(run.textEl, run.text, run.displayMetadata); else run.textEl.textContent = run.text;
    } else if (run.assistantMsgEl) {
      run.assistantMsgEl.classList.add('pending');
      run.textEl.textContent = '…';
    }
  }
  if (run.refs && !run.ended && run.permission && run.card) run.card.classList.add('tc-hold');
  if (run.refs && run.refs.tokens && run.tokens) run.refs.tokens.innerHTML = '<b>' + run.tokens + '</b> tok';
  if (run.ended) paintFinishedCard(run);
  else renderCard(run);
  if (run.assistantMsgEl && run.endedAt) setMsgTime(run.assistantMsgEl, run.endedAt);
}

// The VOOL mark, hand-traced from the approved logo: bold V on top, small O left, larger O right,
// L-shaped smile below -- proportions, tilts and stroke weights follow the source. Every stroke
// carries pathLength="100" so the CSS draw-in uses one dash recipe; the resting markup is the
// complete static mark (no dash attributes; particles and the implode burst start at opacity 0).
// Inline SVG only: no canvas, no images, no libraries -- sharp at any display scale.
const TC_MARK_SVG = '<svg class="tc-mark" viewBox="0 0 80 100" width="56" height="56" aria-hidden="true" focusable="false">'
  + '<g class="tc-life">'
  + '<path class="tc-v" pathLength="100" stroke-width="6.8" d="M3.6 8.2 C13.5 20.5 25.5 34.5 35.4 44 C45.5 33 57.5 16 68.4 0.8"/>'
  + '<path class="tc-ol" pathLength="100" stroke-width="3.6" d="M13.2 47.6 C20.9 47.2 27 53.6 26.9 61.4 C26.8 69.2 20.4 75.3 13.5 75 C6.4 74.7 0.4 68.6 0.5 61.2 C0.6 53.9 6.4 48 13.2 47.6 Z"/>'
  + '<path class="tc-or" pathLength="100" stroke-width="4" d="M56.6 37.2 C66.4 36.6 74.6 45.4 74.5 56.4 C74.4 67.4 66.2 76 56.4 75.6 C46.6 75.2 39.2 66.4 39.3 55.8 C39.4 45.6 47 37.8 56.6 37.2 Z"/>'
  + '<path class="tc-l" pathLength="100" stroke-width="4.8" d="M13.6 97.6 Q46 90.5 78.2 83 L79 68.6"/>'
  + '<circle class="tc-p tc-pv" cx="5" cy="8" r="1.9" style="--dx:-12px;--dy:-9px"/>'
  + '<circle class="tc-p tc-pv" cx="21" cy="25" r="1.6" style="--dx:9px;--dy:-12px"/>'
  + '<circle class="tc-p tc-pv" cx="35" cy="43" r="2.1" style="--dx:-7px;--dy:12px"/>'
  + '<circle class="tc-p tc-pv" cx="50" cy="29" r="1.6" style="--dx:11px;--dy:8px"/>'
  + '<circle class="tc-p tc-pv" cx="67" cy="2" r="1.9" style="--dx:6px;--dy:-11px"/>'
  + '<circle class="tc-p tc-po" cx="13.4" cy="47.5" r="1.7" style="--dx:10px;--dy:-8px"/>'
  + '<circle class="tc-p tc-po" cx="26.9" cy="61" r="2" style="--dx:-11px;--dy:-6px"/>'
  + '<circle class="tc-p tc-po" cx="13.4" cy="74.9" r="1.7" style="--dx:-8px;--dy:10px"/>'
  + '<circle class="tc-p tc-po" cx="0.8" cy="61.5" r="2" style="--dx:9px;--dy:11px"/>'
  + '<circle class="tc-p tc-po" cx="23.5" cy="51.5" r="1.5" style="--dx:7px;--dy:12px"/>'
  + '<circle class="tc-p tc-po" cx="56.6" cy="37.4" r="1.8" style="--dx:-9px;--dy:-11px"/>'
  + '<circle class="tc-p tc-po" cx="74.4" cy="56.4" r="2" style="--dx:11px;--dy:-7px"/>'
  + '<circle class="tc-p tc-po" cx="56.4" cy="75.4" r="1.7" style="--dx:8px;--dy:12px"/>'
  + '<circle class="tc-p tc-po" cx="39.5" cy="55.8" r="2" style="--dx:-12px;--dy:6px"/>'
  + '<circle class="tc-p tc-po" cx="69" cy="44" r="1.5" style="--dx:6px;--dy:13px"/>'
  + '<circle class="tc-p tc-pl" cx="13.6" cy="97.6" r="1.8" style="--dx:-8px;--dy:10px"/>'
  + '<circle class="tc-p tc-pl" cx="46" cy="90.7" r="1.6" style="--dx:6px;--dy:-12px"/>'
  + '<circle class="tc-p tc-pl" cx="78.2" cy="83" r="2" style="--dx:12px;--dy:6px"/>'
  + '<circle class="tc-p tc-pl" cx="78.7" cy="76" r="1.6" style="--dx:-10px;--dy:-9px"/>'
  + '<circle class="tc-p tc-pl" cx="60" cy="87" r="1.8" style="--dx:7px;--dy:13px"/>'
  + '<circle class="tc-b" cx="40" cy="52" r="2.2"/>'
  + '<circle class="tc-b" cx="47" cy="49" r="1.7"/>'
  + '<circle class="tc-b" cx="44" cy="60" r="1.9"/>'
  + '<circle class="tc-b" cx="37" cy="58" r="1.6"/>'
  + '<circle class="tc-b" cx="50" cy="56" r="1.5"/>'
  + '</g></svg>';

// Builds this run's status card on `host`. Pure DOM: no timers, no state. Callers decide whether
// this run's chat is on screen -- attachRunDom for a re-open, runTurn for a fresh turn.
function buildCard(run, host) {
  // Belt and braces: every caller already checks, but the log on screen belongs to ONE chat and a
  // card appended from another would be a cross-chat DOM write.
  if (!isDisplayed(run.chatId)) return null;
  const empty = logEl.querySelector('.empty'); if (empty) empty.remove();
  const card = document.createElement('div');
  card.className = 'task-card'; card.setAttribute('role', 'status');
  card.innerHTML = ''
    + '<div class="tc-head">'
    + TC_MARK_SVG
    + '<div class="tc-headtext"><div class="tc-title"><span class="tc-dot"></span> <span class="tc-titletext">VOOL is working</span></div><div class="tc-stage"></div></div>'
    + '<span class="tc-badges"><span class="tc-mode"></span><span class="tc-model"></span></span>'
    + '<button type="button" class="tc-stop" title="Stop this turn — cancels the running task on your machine and stops the answer.">Stop</button>'
    + '</div>'
    + '<div class="tc-meta"><span class="tc-elapsed"></span><span class="tc-tokens"></span><span class="tc-steps"></span><span class="tc-cost"></span></div>'
    + '<div class="tc-recent"></div><div class="tc-last"></div><div class="tc-summary"></div>'
    + '<div class="tc-actions"><button type="button" class="tc-view">View activity</button></div>'
    + '<span class="sr-only"></span>';
  // One container per turn: append the status card INTO the assistant message (host), not as a
  // separate sibling in the log. So the answer + its live status/collapsed metadata are one block.
  (host || logEl).appendChild(card);
  const q = (sel) => card.querySelector(sel);
  const refs = { card, mark: q('.tc-mark'), title: q('.tc-titletext'), stage: q('.tc-stage'), mode: q('.tc-mode'), model: q('.tc-model'), recent: q('.tc-recent'),
    stop: q('.tc-stop'), elapsed: q('.tc-elapsed'), tokens: q('.tc-tokens'), steps: q('.tc-steps'), cost: q('.tc-cost'), last: q('.tc-last'),
    summary: q('.tc-summary'), view: q('.tc-view'), sr: q('.sr-only') };
  refs.stop.addEventListener('click', () => {
    // Cancel the SERVER turn first (so inference actually stops and stops counting toward usage),
    // then abort the client stream. Keyed by turn_id so it never cancels a different/queued turn.
    // Cancel is addressed to the run's OWN chat -- never to whatever is on screen, or Stop would
    // cancel a turn in the chat the user happens to be looking at.
    try { fetch('/api/chat/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: run.chatId, turn_id: run.turnId }) }); } catch (e) {}
    if (run.aborter) run.aborter.abort();
  });
  refs.view.addEventListener('click', openPanel);
  run.card = card; run.refs = refs;
  if (run.permission || run.pricePaused) card.classList.add('tc-hold');   // re-built mid-approval: the mark holds, it is not working
  if (run.ended) refs.stop.style.display = 'none';   // a re-hung terminal card has nothing to stop
  logEl.scrollTop = logEl.scrollHeight;
  renderCard(run);
  return refs;
}

const HUMAN_ACTIVITY_STATES = {
  Planning: 'Planning', Searching: 'Searching', Reading: 'Reading', Editing: 'Editing',
  Running: 'Running', Testing: 'Testing', 'Waiting for permission': 'Waiting for approval',
};
function humanActivityState(run) {
  if (run.permission) return 'Waiting for approval';
  const stage = String(run.stage || '').trim();
  return HUMAN_ACTIVITY_STATES[stage] || 'Working';
}
function recordedModelProvider(model) {
  if (!model) return '';
  return String(model.provider_id || '').trim();
}
function modelIdentityLine(label, provider, modelId) {
  const cleanProvider = String(provider || '').trim(), cleanModel = String(modelId || '').trim();
  const bits = [label];
  if (cleanProvider) bits.push(cleanProvider);
  if (cleanModel && cleanProvider !== cleanModel && !cleanProvider.endsWith(':' + cleanModel)) bits.push(cleanModel);
  return bits.join(' · ');
}
function modelIdentityDetails(model) {
  if (!model) return [];
  const lines = [];
  if (model.requested_provider_id || model.requested_model_id) lines.push(modelIdentityLine('Requested', model.requested_provider_id, model.requested_model_id));
  if (model.policy_provider_id || model.policy_model_id) lines.push(modelIdentityLine('Policy', model.policy_provider_id, model.policy_model_id));
  if (model.selected_provider_id || model.selected_model_id) lines.push(modelIdentityLine('Selected manifest', model.selected_provider_id, model.selected_model_id));
  if (model.actual_adapter_provider_id || model.actual_adapter_model_id) lines.push(modelIdentityLine('Actual adapter', model.actual_adapter_provider_id, model.actual_adapter_model_id));
  return lines;
}
function modelProviderPresentation(model) {
  if (!model) return '';
  // Strongest available evidence wins the compact line without overwriting any tier's label.
  let strongest = '';
  if (model.actual_adapter_provider_id || model.actual_adapter_model_id) {
    strongest = modelIdentityLine('Actual adapter', model.actual_adapter_provider_id, model.actual_adapter_model_id);
  } else if (model.selected_provider_id || model.selected_model_id) {
    strongest = modelIdentityLine('Selected manifest', model.selected_provider_id, model.selected_model_id);
  } else if (model.requested_provider_id || model.requested_model_id) {
    strongest = modelIdentityLine('Requested', model.requested_provider_id, model.requested_model_id);
  } else if (model.policy_provider_id || model.policy_model_id) {
    strongest = modelIdentityLine('Policy', model.policy_provider_id, model.policy_model_id);
  } else if (recordedModelProvider(model) || model.model_id) {
    strongest = modelIdentityLine('Recorded', recordedModelProvider(model), model.model_id);
  } else {
    strongest = 'Unknown model identity';
  }
  return strongest + (model.paid ? ' · $' : '');
}
// The card badge is a glanceable identity, not the evidence line. The full one
// ("Actual adapter · openrouter-byok · nvidia/nemotron-3-ultra-550b-a55b:free") overflowed the
// working card, so the badge carries the model's own name and the complete identity stays on the
// title attribute and in the Activity panel, where nothing is truncated.
function compactModelName(rawId) {
  let name = String(rawId || '').trim();
  if (!name) return '';
  // Drop a leading "provider:" qualifier only when a vendor path follows it, so a bare local tag
  // like "qwen3:8b" keeps its size (slicing at the first colon would leave "8b").
  const firstColon = name.indexOf(':'), firstSlash = name.indexOf('/');
  if (firstColon !== -1 && firstSlash !== -1 && firstColon < firstSlash) name = name.slice(firstColon + 1);
  const lastSlash = name.lastIndexOf('/');
  if (lastSlash !== -1) name = name.slice(lastSlash + 1);
  return name;
}
function compactModelBadge(model) {
  if (!model) return '';
  const id = compactModelName(model.actual_adapter_model_id || model.selected_model_id
    || model.requested_model_id || model.policy_model_id || model.model_id || '');
  if (id) return id + (model.paid ? ' · $' : '');
  const provider = compactModelName(recordedModelProvider(model));
  return provider ? (provider + (model.paid ? ' · $' : '')) : 'Model unknown';
}

function renderCard(run) {
  const r = run.refs; if (!r) return;
  // Single line + ellipsis, with the untruncated string on the tooltip.
  const stageText = run.action || run.stage || '';
  r.stage.textContent = stageText;
  r.stage.title = stageText;
  if (!run.ended) r.title.textContent = run.pricePaused ? 'Paused — waiting for your price' : 'VOOL is working';
  r.stage.style.whiteSpace = run.pricePaused ? 'normal' : '';
  r.stage.style.overflow = run.pricePaused ? 'visible' : '';
  r.elapsed.innerHTML = '<b>' + fmtElapsed(Date.now() - run.start) + '</b> elapsed';
  r.steps.textContent = run.steps.length ? stepsRollupText(run.steps) : '';
  r.mode.textContent = MODE_LABELS[run.activeMode] || MODE_LABELS.manual;
  r.mode.classList.toggle('bypass', run.activeMode === 'bypass_permissions');
  if (run.model) {
    const _cloud = String(run.model.locality || '').toLowerCase() === 'cloud';
    r.model.classList.toggle('cloud', _cloud);
    r.model.classList.toggle('paid', !!run.model.paid);
    r.model.textContent = compactLabel(compactModelBadge(run.model));
    const _identity = modelIdentityDetails(run.model);
    if (!_identity.length) _identity.push(modelProviderPresentation(run.model));
    r.model.title = _identity.join(' | ') || 'Recorded model identity';
  }
  const usd = fmtUsd(run.cost); r.cost.textContent = usd ? (usd + ' used') : '';
  const recent = run.steps.slice(-3).map((s) => (s.status === 'completed' ? '✓ ' : (s.status === 'failed' ? '× ' : '• ')) + (s.summary || s.tool || 'Action'));
  r.recent.innerHTML = recent.map((s) => '<span>' + esc(s) + '</span>').join('');
  r.last.textContent = run.last || '';
  r.last.title = run.last || '';   // one line + ellipsis, exact text on the tooltip
  if (run.status === 'running') r.title.textContent = run.verifying ? 'Answer ready · model review running…' : humanActivityState(run);
  run.card.classList.toggle('perm', !!run.permission && run.status === 'running');
  r.sr.textContent = (run.permission ? 'Waiting for approval. ' : '') + (run.action || run.stage || '');
}

const REVIEW_STATE_BY_RAW_TYPE = Object.freeze({
  model_lane_verifier_started: 'running',
  model_lane_verifier_completed: 'passed',
  model_lane_verifier_flagged: 'flagged',
  model_lane_verifier_blocked: 'blocked',
  model_lane_verifier_degraded: 'degraded',
  model_lane_verifier_failed: 'runtime_failed',
});
const REVIEW_STATES = new Set(['not_run', 'running', 'passed', 'flagged', 'blocked', 'degraded', 'runtime_failed', 'unavailable']);
function normalizeRunReviewState(value) {
  const state = String(value || 'not_run').toLowerCase();
  // "failed" is the old collapsed state. Without its raw typed event we cannot know whether a
  // reviewer returned a negative verdict or the review path failed before producing one.
  if (state === 'failed') return 'unavailable';
  return REVIEW_STATES.has(state) ? state : 'unavailable';
}
function reviewStateForRun(run) {
  if (!run) return 'not_run';
  if (run.reviewState) return normalizeRunReviewState(run.reviewState);
  if (run.verifierPassed) return 'passed';
  // verifierFailed predates causal review states and is ambiguous when encountered without one.
  if (run.verifierFailed) return 'unavailable';
  return 'not_run';
}
function reviewStateFromEvent(ev) {
  if (ev && ev.review_state) return normalizeRunReviewState(ev.review_state);
  const rawState = ev && REVIEW_STATE_BY_RAW_TYPE[String(ev.raw_type || '')];
  if (rawState) return rawState;
  if (ev && ev.type === 'verification.started') return 'running';
  const status = String((ev && ev.status) || '').toLowerCase();
  if (status === 'completed' || status === 'passed') return 'passed';
  // A generic legacy failure proves only that review evidence is unavailable, not that a reviewer
  // executed and flagged the answer.
  return 'unavailable';
}
function setRunReviewState(run, state) {
  const normalized = normalizeRunReviewState(state);
  run.reviewState = normalized;
  run.verifying = normalized === 'running';
  run.verifierPassed = normalized === 'passed';
  run.verifierFailed = normalized === 'flagged';
  return normalized;
}
function reviewStatePresentation(value) {
  const state = normalizeRunReviewState(value);
  const presentations = {
    not_run: { cls: '', icon: '', label: '', evidence: '', detail: '', meta: '' },
    running: { cls: 'run', icon: '»', label: 'Model review running', evidence: 'Model review running', detail: 'no reviewer verdict recorded yet', meta: 'model review running' },
    passed: { cls: 'ok', icon: '✓', label: 'Independent review passed', evidence: 'Model-reviewed — passed', detail: 'reviewer model returned PASS — this is not validation or answer proof', meta: 'model review passed' },
    flagged: { cls: 'fail', icon: '⚠', label: 'Review flagged', evidence: 'Review flagged', detail: 'reviewer executed and returned a flagged verdict — this is not validation proof', meta: 'review flagged' },
    blocked: { cls: 'fail', icon: '⚠', label: 'Model review unavailable', evidence: 'Model review unavailable', detail: 'review was blocked before a reviewer verdict was produced', meta: 'model review blocked' },
    degraded: { cls: 'fail', icon: '⚠', label: 'Model review degraded', evidence: 'Model review degraded', detail: 'review path was degraded or unavailable; no reviewer verdict was produced', meta: 'model review degraded' },
    runtime_failed: { cls: 'fail', icon: '⚠', label: 'Model review failed to run', evidence: 'Model review failed to run', detail: 'review execution failed before producing a valid verdict', meta: 'model review failed to run' },
    unavailable: { cls: 'fail', icon: '⚠', label: 'Model review unavailable', evidence: 'Model review unavailable', detail: 'legacy review evidence is ambiguous; no reviewer verdict can be inferred', meta: 'model review unavailable' },
  };
  return presentations[state] || presentations.unavailable;
}
function reviewPanelRow(run) {
  const review = reviewStatePresentation(reviewStateForRun(run));
  // The actual cause when the run recorded one ("no verifier lane was available"): the fixed
  // per-state detail stays only as the fallback for runs whose events carried no reason.
  const detail = String(run && run.reviewReason || '').trim() || review.detail;
  return review.label ? panelRow(review.cls, review.icon, review.label, detail) : '';
}

// Evidence labels are intentionally independent from the terminal title (Completed / Stopped /
// Failed safely). A reviewer-model PASS is model review evidence, not expected-result proof, and a
// test state can be shown alongside it rather than being hidden behind one generic verdict.
function evidenceLabels(run) {
  const labels = [];
  if (run.tests && run.tests.total > 0) labels.push(run.tests.failed ? 'Tested — issues found' : 'Tested — passed');
  const review = reviewStatePresentation(reviewStateForRun(run));
  if (review.evidence) labels.push(review.evidence);
  if (run.steps.some((step) => step.status === 'completed')) labels.push('Tool action completed');
  return labels;
}
function hasMeaningfulActivity(run) {
  return !!(run && ((run.steps && run.steps.length) || (run.events && run.events.length)
    || (run.ledger && run.ledger.length) || run.permission || reviewStateForRun(run) !== 'not_run'
    || run.status === 'failed' || run.status === 'cancelled' || run.status === 'awaiting_approval'));
}

function applyResultSummary(run) {
  // Derive the summary onto the RUN first, so a run whose chat is off screen still carries its own
  // result; painting is a second, separate step that only happens when there is a card to paint.
  const actions = run.steps.filter(function (s) { return s.status === 'completed'; }).length;
  const evidence = evidenceLabels(run);
  run.resultLabelText = evidence.join(' · ');
  const bits = [];
  if (actions) bits.push(actions + ' action' + (actions === 1 ? '' : 's'));
  // Only authoritative locality may move model identity into the cloud-emphasis summary.
  if (run.model && String(run.model.locality || '').toLowerCase() === 'cloud') bits.push(modelProviderPresentation(run.model));
  const usd = fmtUsd(run.cost);
  if (usd) {
    // Attribute the spend to the calls that made it. A review that produced no verdict made no
    // model call, so the whole recorded amount belongs to the answer lane -- the bare number
    // beside "Model review unavailable" read as the review's price (2026-09-15 incident).
    const reviewState = reviewStateForRun(run);
    const reviewRan = reviewState === 'passed' || reviewState === 'flagged';
    bits.push(usd + (reviewRan ? ' on answer + review' : ' on the model answer'));
  }
  const parts = evidence.concat(bits).filter(Boolean);
  run.resultParts = parts;
  run.resultActions = actions;
  const r = run.refs; if (!r) return;
  r.summary.textContent = parts.join(' · ');
  r.last.textContent = run.last || '';
  r.last.title = run.last || '';
  r.sr.textContent = parts.join(' · ') || 'Done';
  // Routing, approval, failure, review and ledger events are meaningful even when no tool completed.
  const hasActivity = hasMeaningfulActivity(run);
  if (r.view) r.view.style.display = hasActivity ? '' : 'none';
  if (run.card) run.card.classList.toggle('bare', parts.length === 0 && !hasActivity);
}

// End a run. The STATE half (status, end time, summary) is applied to the run and its owning chat
// unconditionally; only the paint half is gated on that chat being on screen. A completion in a
// background chat therefore lands in full, and touches nothing the displayed chat owns.
function finishRun(run, kind, summaryText, tsIso) {
  if (run.ended) return; run.ended = true; run.status = kind;
  // The bubble's real completion time: the server's own ts on the terminal event when the stream
  // carried one, else the instant this client-detected end (stream close, abort, fetch error) is
  // actually happening right now -- never a re-guess for a message that finished earlier.
  run.endedAt = tsIso || new Date().toISOString();
  run.endSummary = summaryText || '';
  // Stamp the transcript entry too, so re-opening this chat later shows the same completion time
  // the bubble showed live rather than losing it to the re-render.
  const _owner = ownerOf(run);
  if (run.historyIndex >= 0 && _owner.history[run.historyIndex]) _owner.history[run.historyIndex].ts = run.endedAt;
  clearRunTimers(run);
  if (kind !== 'awaiting_approval') applyResultSummary(run);
  // Terminal events (task_completed/failed/cancelled) land right at stream close -- one last drain
  // if this run's chat is the one on screen with the panel open (openPanel does its own fresh read
  // when it is opened later, and a re-opened chat catches up from the run's own cursor).
  if (isDisplayed(run.chatId) && document.body.classList.contains('panel-open')) setTimeout(() => { pollLedger(run); }, 500);
  // The mark's own classes do the lifecycle: `perm` freezes it while approval is pending (painted
  // by paintFinishedCard below), and the terminal kind class (done/failed/cancelled) settles it
  // into the complete static mark.
  if (run.assistantMsgEl) setMsgTime(run.assistantMsgEl, run.endedAt);
  if (run.assistantMsgEl && run.profileFrame) renderProfileFrame(run);
  // No card means this run's chat is off screen: the state above is complete, and paintFinishedCard
  // will replay all of it onto a fresh card the moment the chat is opened again.
  paintFinishedCard(run);
  if (isDisplayed(run.chatId)) renderPanel();
  setTimeout(loadActivityHistory, 250);
  // Named fragment call site: lifecycle notifications. The fragment decides visibility (it only
  // surfaces completions the operator was NOT looking at); this site only reports the truth.
  if (window.VoolNotify && window.VoolNotify.runFinished) {
    try {
      window.VoolNotify.runFinished({
        chatId: run.chatId, status: kind, displayed: isDisplayed(run.chatId),
        summary: String(summaryText || ''),
      });
    } catch (e) {}
  }
  // Voice conversation (opt-in): read the finished answer aloud on this device, only for the
  // chat the operator is looking at, only on a real completion. The transcript is never spoken
  // before the run actually ended, and Stop/Esc/toggle-off cancels at any moment.
  if (kind === 'completed' && typeof voiceMode === 'function' && voiceMode()
      && isDisplayed(run.chatId) && run.text) {
    try { readAloud(run.text, dictationLocale()); } catch (e) {}
  }
}

// The terminal look of a finished run's card. Called by finishRun, and again by attachRunDom when a
// chat is re-opened after its turn ended off screen -- so a completion the user never watched shows
// exactly what it would have shown had they been looking.
function paintFinishedCard(run) {
  const r = run.refs; if (!r) return;
  const kind = run.status;
  run.card.classList.toggle('perm', kind === 'awaiting_approval');
  run.card.classList.add(kind === 'completed' ? 'done' : kind);
  r.stop.style.display = 'none';
  const title = kind === 'completed' ? pageT('run.status_completed', 'Completed') : (kind === 'cancelled' ? pageT('run.status_stopped', 'Stopped') : (kind === 'awaiting_approval' ? pageT('run.status_waiting', 'Waiting for your approval') : pageT('run.failed_safely_label', 'Failed safely')));
  r.title.textContent = title;
  r.stage.textContent = run.endSummary || title;
  r.elapsed.innerHTML = '<b>' + fmtElapsed((run.endedAtMs || (run.endedAtMs = Date.now())) - run.start) + '</b> ' + pageT('run.total_word', 'total');
  // The Proof Chip for a turn that just finished (or re-opened after finishing off screen),
  // addressed by the commit's canonical request id — the same identity the server stamped on
  // the served bytes. No commit frame means no id, and no id means no chip.
  if (run.assistantMsgEl && isDisplayed(run.chatId)) {
    try { mountProofChip(run.assistantMsgEl, run.chatId, run.responseCommit && run.responseCommit.request_id, run.displayMetadata && run.displayMetadata.presentation_selection); } catch (e) {}
  }
  if (kind !== 'awaiting_approval') applyResultSummary(run);
  if (kind === 'failed' && !run.card.dataset.brWired) {
    run.card.dataset.brWired = '1';
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'tc-report';
    link.textContent = pageT('run.report_problem', 'Report a problem');
    link.title = pageT('run.report_problem_title', 'Build a sanitized bug report for this failed turn');
    link.addEventListener('click', () => reportProblemForFailedRun(run));
    run.card.appendChild(link);
  }
}

function applyTaskEvent(run, ev) {
  if (!ev || !ev.type) return;
  if (ev.seq != null) {
    const seq = Number(ev.seq) || 0, eventKey = ev.type + ':' + seq;
    if (run.eventSeen[eventKey] || (seq && seq < run.eventSeq)) return;
    run.eventSeen[eventKey] = 1; if (seq) run.eventSeq = Math.max(run.eventSeq, seq);
  }
  // A late review result (the reviewer runs after the answer already streamed) must still update
  // the evidence label of an already-finished run; everything else is ignored once ended.
  if (run.ended && ev.type !== 'verification.completed' && ev.type !== 'verification.started') return;
  // Companion layer consumes the SAME typed event (presentation only, never authority).
  // Placed before the switch because the terminal cases below return early. The typeof
  // guard keeps function-slicing harnesses (no DOM globals) running unchanged.
  if (typeof window !== 'undefined' && window.VoolCompanion) window.VoolCompanion.consume(run.chatId, ev);
  run.events.push(ev);
  if (ev.stage) { run.stage = ev.stage; run.evented = true; }
  if (ev.summary) { run.action = ev.summary; run.evented = true; }
  if (ev.model) { run.model = ev.model; rememberStickyModel(run.chatId, ev.model); }
  if (ev.cost) run.cost = Object.assign({}, run.cost, ev.cost);
  switch (ev.type) {
    case 'task.price_paused':
      run.pricePaused = true;
      if (run.card) run.card.classList.add('tc-hold');
      break;
    case 'task.price_resumed':
      run.pricePaused = false;
      if (run.card) run.card.classList.remove('tc-hold');
      break;
    case 'tool.started':
      run.permission = false;
      run.steps.push({ tool: ev.tool, summary: ev.summary, status: 'running', stage: ev.stage,
        startedAt: Date.now(), finishedAt: 0, model: run.model, cost: run.cost });
      if (run.card) run.card.classList.remove('tc-hold');  // resume after a permission pause
      break;
    case 'tool.completed': {
      let s = null; for (let i = run.steps.length - 1; i >= 0; i--) { if (run.steps[i].tool === ev.tool && run.steps[i].status === 'running') { s = run.steps[i]; break; } }
      if (!s) s = run.steps[run.steps.length - 1];
      if (s) { s.status = 'completed'; if (ev.summary) s.summary = ev.summary; s.finishedAt = Date.now(); }
      else run.steps.push({ tool: ev.tool, summary: ev.summary, status: 'completed', stage: ev.stage, startedAt: Date.now(), finishedAt: Date.now() });
      run.last = ev.summary || run.last;
      if (ev.path) run.files[ev.path] = (run.files[ev.path] || 0) + 1;
      if ((ev.stage || '') === 'Testing' && ev.measurable) { if (ev.measurable.current != null) run.tests.done = ev.measurable.current; if (ev.measurable.total != null) run.tests.total = ev.measurable.total; }
      break;
    }
    case 'tool.failed': {
      let s = null; for (let i = run.steps.length - 1; i >= 0; i--) { if (run.steps[i].status === 'running') { s = run.steps[i]; break; } }
      if (s) { s.status = 'failed'; if (ev.summary) s.summary = ev.summary; s.finishedAt = Date.now(); }
      else run.steps.push({ tool: ev.tool, summary: ev.summary, status: 'failed', stage: ev.stage, startedAt: Date.now(), finishedAt: Date.now() });
      run.last = ev.summary || run.last;
      break;
    }
    case 'permission.required':
      run.permission = true; run.action = ev.summary || pageT('run.status_waiting', 'Waiting for your approval');
      if (run.card) run.card.classList.add('tc-hold');   // the mark holds: nothing is running
      // The prompt is recorded against the chat that raised it, and only PAINTED into the single
      // permission bar when that chat is on screen -- otherwise one chat's approval request would
      // appear above another chat's composer, and be answered there.
      showPermBar(run.chatId, ev);
      break;
    case 'verification.started':
      // The primary answer is done; the verifier now runs a second model call. Show
      // "Answer ready · verifying…". The composer stays usable throughout (a send while a turn
      // runs is queued), so we don't start the next turn until this stream truly closes.
      setRunReviewState(run, reviewStateFromEvent(ev));
      run.action = ev.summary || 'Answer ready · verifying…';
      break;
    case 'verification.completed': {
      const reviewState = setRunReviewState(run, reviewStateFromEvent(ev));
      const review = reviewStatePresentation(reviewState);
      run.last = review.label || 'Model review outcome unavailable';
      // The reviewer's OWN cause, when the event carried one: "Verifier was required, but no
      // verifier lane was available." is the fact the operator can act on, and the typed state
      // alone ("blocked") does not say which of the blocked shapes it was. A verdict state
      // (passed/flagged) clears it: the verdict, not the cause, is the information then.
      if (reviewState === 'blocked' || reviewState === 'degraded' || reviewState === 'runtime_failed' || reviewState === 'unavailable') {
        const cause = String(ev.summary || '').trim();
        if (cause) run.reviewReason = cause;
      } else {
        run.reviewReason = '';
      }
      // If the answer already finished, re-derive the label from this new evidence.
      if (run.ended) { applyResultSummary(run); if (isDisplayed(run.chatId)) renderPanel(); return; }
      break;
    }
    case 'model.changed': break;
    case 'mode.changed':
      if (ev.active_mode && MODE_LABELS[ev.active_mode]) run.activeMode = ev.active_mode;
      break;
    case 'task.stage_changed': break;
    case 'task.completed': finishRun(run, 'completed', ev.summary, ev.ts); return;
    case 'task.failed': finishRun(run, 'failed', ev.summary, ev.ts); return;
    case 'task.cancelled': finishRun(run, 'cancelled', ev.summary, ev.ts); return;
    default: break;
  }
  // State above is unconditional. Painting is not: a run whose chat is off screen has no card
  // (buildCard declined to make one) and must not repaint the panel the displayed chat owns.
  renderCard(run);
  if (isDisplayed(run.chatId)) renderPanel();
}

// ---- Execution panel ----
function visibleTabs() {
  // Only show a tab when it actually has something for THIS run — otherwise the panel is 8 tabs,
  // 7 of them "No X for this task". Activity is the primary lens, always offered so the panel is
  // never tab-less (it explains when a plain answer used no tools).
  const r = view.run;
  // Event log and Receipts were gated on a LIVE run, so in a project- or VOOL-wide scope -- where
  // the evidence comes from durable per-chat ledgers and no run is on screen -- the two lenses the
  // scope is meant to govern could not be opened at all. They are offered whenever there is
  // something recorded to read: a wider scope, a live run, or this chat's own saved ledger.
  const wide = panelScope !== 'chat';
  const recorded = !!((view.recoveredLedger || []).length);
  const agentRows = agentRowsFrom((view.chatLedger || []), Date.now());
  const has = {
    Steps: !!(r && r.steps.length), Activity: true,
    // Offered only once this chat has actually run a multi-node plan -- an always-visible empty
    // tab teaches the operator to ignore it.
    Agents: !!agentRows.length,
    Changes: !!(r && Object.keys(r.files).length), Files: !!(r && Object.keys(r.files).length),
    'Event log': wide || !!(r && r.events.length) || recorded,
    Tests: !!(r && (r.tests.total || r.tests.done)),
    Receipts: wide || !!(r && r.ended) || recorded,
    // Always offered: the Council lens is truthful when empty too ("no reviewer verdict yet" IS
    // the honest state), and the surface must exist before the first reviewer producer runs.
    Council: true, Preview: !!(r && r.preview),
  };
  return PANEL_TABS.filter((t) => has[t]);
}
function buildTabs() {
  xpTabsEl.innerHTML = '';
  const tabs = visibleTabs();
  if (!tabs.includes(panelTab)) panelTab = tabs[0] || 'Activity';  // current tab got hidden -> first shown
  tabs.forEach((name) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'xp-tab' + (name === panelTab ? ' active' : ''); b.setAttribute('role', 'tab');
    b.innerHTML = esc(name) + ' <span class="cnt" data-tab="' + esc(name) + '"></span>';
    b.addEventListener('click', () => { panelTab = name; buildTabs(); renderPanel(); });
    xpTabsEl.appendChild(b);
  });
  updateTabCounts();
}
function openPanel() {
  document.body.classList.add('panel-open'); panelBtnEl.classList.add('on'); panelBtnEl.setAttribute('aria-pressed', 'true');
  // Opening takes room the sidebar and chat had a moment ago, and the stored width may have been
  // chosen on a larger window, so the boundaries are re-applied here rather than only on a drag.
  reclampLayout();
  // Fetch the ledger now so the timeline is current the instant the panel opens; with no active run
  // (e.g. just after a page refresh) rebuild the last turn so the panel isn't blank.
  loadActivityHistory();
  if (view.run) pollLedger(view.run); else loadRecoveredLedger().then(() => renderPanel());
  renderPanel();
}
function closePanel() { document.body.classList.remove('panel-open'); panelBtnEl.classList.remove('on'); panelBtnEl.setAttribute('aria-pressed', 'false'); }
function togglePanel() { document.body.classList.contains('panel-open') ? closePanel() : openPanel(); }
function rowCls(st) { return st === 'completed' ? 'ok' : st === 'failed' ? 'fail' : st === 'pending_approval' ? 'wait' : 'run'; }
function rowIcon(st) { return st === 'completed' ? '✓' : st === 'failed' ? '✕' : st === 'pending_approval' ? '⏸' : st === 'running' ? '▸' : '•'; }

function updateTabCounts() {
  const run = view.run;
  // Badges count real ACTIONS (tool steps), not noisy low-level events.
  const counts = { Steps: run ? run.steps.length : 0, Activity: run ? run.steps.length : 0,
    Changes: run ? Object.keys(run.files).length : 0, Files: run ? Object.keys(run.files).length : 0,
    Tests: run ? run.tests.total : 0 };
  // A wider scope counts the chats it is actually reporting on, so the badge never claims the open
  // chat's step count for a project- or VOOL-wide view.
  if (panelScope !== 'chat') {
    const set = scopeSessionSet();
    counts.Activity = activityHistory.filter((item) => !set || set.has(item.session_id)).length;
  }
  xpTabsEl.querySelectorAll('.cnt').forEach((el) => { const n = counts[el.getAttribute('data-tab')]; el.textContent = n ? ('(' + n + ')') : ''; });
}
function stepDuration(s) {
  if (!s.startedAt) return '';
  const end = s.finishedAt || Date.now();
  const ms = Math.max(0, end - s.startedAt);
  return ms < 1000 ? (ms + 'ms') : fmtElapsed(ms);
}
function stepMeta(s) {
  const bits = [];
  if (s.tool) bits.push(s.tool);
  const dur = stepDuration(s); if (dur) bits.push(dur);
  if (s.status) bits.push(s.status);
  if (s.model) bits.push(modelProviderPresentation(s.model));
  const usd = fmtUsd(s.cost); if (usd) bits.push(usd);
  return bits.join(' · ');
}
const EV_LABELS = { 'model.changed': 'Switched model', 'cloud.cost_updated': 'Cost updated',
  'task.stage_changed': 'Stage changed', 'verification.started': 'Model review running', 'verification.completed': 'Model review completed',
  'permission.required': 'Waiting for approval', 'task.completed': 'Completed', 'task.failed': 'Failed safely', 'task.cancelled': 'Cancelled' };
function evTitle(ev) {
  if (ev.summary) return ev.summary;
  if (ev.type === 'model.changed' && ev.model) return pageT('activity.model_recorded', 'Model recorded: ') + modelProviderPresentation(ev.model);
  const label = EV_LABELS[ev.type];
  return (label != null ? pageT('activity.ev.' + ev.type, label) : '') || (ev.stage || ev.type);
}
function panelRow(cls, icon, title, sub) {
  return '<div class="xp-row ' + cls + '"><span class="ic">' + icon + '</span><div class="bd">' + esc(title) + (sub ? ('<div class="st">' + esc(sub) + '</div>') : '') + '</div></div>';
}

// ---- Ledger-backed Activity: the REAL runtime timeline, scoped to this turn ----
// The stream only delivers mapped tool events; the runtime ledger (/api/runtime/events) also carries
// scope, classification, model routing, timeouts, verification and the terminal state. We poll it and
// keep only rows tagged with this turn's client_turn_id, so the operator sees exactly what ran.
const LEDGER_SKIP = { model_output_chunk: 1, model_lane_proof: 1, heartbeat: 1 };
const LEDGER_MAP = {
  scope_resolved:               ['ok',  '◎', 'Scope'],
  task_received:                ['run', '≡', 'Request received'],
  task_classified:              ['run', '≡', 'Classified'],
  model_routing_started:        ['run', '◆', 'Routing to a model'],
  model_lane_selected:          ['run', '◆', 'Model selected'],
  model_lane_started:           ['run', '◆', 'Model running'],
  model_lane_completed:         ['ok',  '◆', 'Model answered'],
  model_verification_receipt:   ['meta', '≡', 'Model identity receipt'],
  model_lane_failed:            ['fail','⚠', 'Model call failed'],
  model_lane_contract_failed:   ['fail','⚠', 'Model output rejected'],
  model_lane_verifier_started:  ['run', '»', 'Model review running'],
  model_lane_verifier_completed:['ok',  '✓', 'Independent review passed'],
  model_lane_verifier_degraded: ['fail','⚠', 'Model review degraded'],
  model_lane_verifier_flagged:  ['fail','⚠', 'Review flagged'],
  model_lane_verifier_blocked:  ['fail','⚠', 'Model review unavailable'],
  model_lane_verifier_failed:   ['fail','⚠', 'Model review failed to run'],
  // Attachment receipts (core/chat_attachments.py via the API door and the router): what was
  // attached, refused, sent with a message, read or withheld by a model, and released after.
  attachment_staged:            ['ok',  '\u{1F4CE}', 'Attached'],
  attachment_refused:           ['fail','\u{1F4CE}', 'Attachment refused'],
  attachment_removed:           ['ok',  '\u{1F4CE}', 'Attachment removed'],
  attachment_bound:             ['run', '\u{1F4CE}', 'Attachment sent with message'],
  attachment_read:              ['ok',  '\u{1F4CE}', 'Attachment read by model'],
  attachment_sent:              ['ok',  '\u{1F4CE}', 'Image sent to model'],
  attachment_omitted:           ['fail','\u{1F4CE}', 'Attachment not read'],
  attachment_ignored:           ['run', '\u{1F4CE}', 'Attachment not used'],
  attachment_released:          ['ok',  '\u{1F4CE}', 'Attachment released'],
  tool_selected:                ['run', '▸', 'Tool'],
  tool_executed:                ['ok',  '✓', 'Tool'],
  tool_failed:                  ['fail','✕', 'Tool failed'],
  tool_fallback_to_research:    ['run', '»', 'Falling back to research'],
  tool_loop_resumed:            ['run', '▸', 'Resumed tools'],
  tool_loop_completed:          ['ok',  '✓', 'Tools done'],
  tool_repeat_blocked:          ['fail','⚠', 'Repeated tool blocked'],
  tool_synthesizing:            ['run', '»', 'Synthesizing'],
  workflow_planner_step:        ['run', '»', 'Plan step'],
  workflow_planner_stop:        ['ok',  '✓', 'Plan complete'],
  task_pending_approval:        ['wait','⏸', 'Waiting for approval'],
  task_interrupted:             ['fail','⚠', 'Interrupted'],
  task_completed:               ['ok',  '✓', 'Completed'],
  task_failed:                  ['fail','✕', 'Failed'],
  stale_result_rejected:        ['fail','⚠', 'Stale result rejected'],
  model_routing_failed:         ['fail','⚠', 'Routing failed'],
  // The canonical workspace-mutation receipts emitted by core/runtime_execution_tools.py. These are
  // the only events that testify a file on disk actually changed; without them keyed here they fell
  // to the ['run','•',''] default and rendered as an unlabelled bullet.
  workspace_mutation_completed:          ['ok',  '✎', 'File changed'],
  workspace_mutation_rollback_completed: ['ok',  '↩', 'Change rolled back'],
  workspace_mutation_failed:             ['fail','✕', 'File change failed'],
  workspace_mutation_rollback_conflict:  ['fail','⚠', 'Rollback conflict'],
  // The router emits TWO shapes: model_lane_* for the lane timeline and DOTTED model.call_* for each
  // per-adapter call. Only the underscore names were keyed here, so every dotted type fell to the
  // ['run','•',''] default and rendered as a bare bullet with no label and no sub-line.
  'model.call_started':              ['run', '◆', 'Model running'],
  'model.call_completed':            ['ok',  '◆', 'Model answered'],
  'model.call_failed':               ['fail','⚠', 'Model call failed'],
  'model.response_constraint_retry': ['run', '»', 'Retrying for output shape'],
  // Retrieval receipts (core/retrieval_observability.py, core/fresh_data/fx.py). Unkeyed, a FAILED
  // retrieval fell to ['run','•',''] and drew a neutral running bullet reading "Web retrieval
  // failed." -- the one row that says the turn's grounding never arrived was rendered as ordinary
  // progress, and the `failure_class` it carries had nowhere to go.
  web_retrieval_started:   ['run', '⌕', 'Web retrieval'],
  web_retrieval_completed: ['ok',  '⌕', 'Web retrieval done'],
  web_retrieval_failed:    ['fail','✕', 'Web retrieval failed'],
  fx_retrieval_completed:  ['ok',  '⌕', 'Rate retrieved'],
  fx_retrieval_failed:     ['fail','✕', 'Rate retrieval failed'],
};
function ledgerModelName(e) {
  if (e.actual_adapter_provider_id || e.actual_adapter_model_id) return e.actual_adapter_model_id || '';
  return e.model_id || e.model_name || e.selected_model || e.planned_model_id || '';
}
function ledgerProviderName(e) {
  if (e.actual_adapter_provider_id || e.actual_adapter_model_id) return e.actual_adapter_provider_id || '';
  return e.provider_id || '';
}
// The UsePod provider receipt (vool.usepod.receipt.v1, flattened onto model.call_* rows by the
// router), rendered as a sentence instead of the raw JSON the server logs. Every distinction the
// receipt itself makes is kept: an upper bound is never called a charge, a missing balance is
// never zero, a retained liability stays visible, and the snapshot / reservation / network /
// signature links appear only where the receipt actually carries them.
function ledgerUsePodReceipt(e) {
  const r = e.provider_receipt;
  if (!r || typeof r !== 'object' || String(r.schema || '') !== 'vool.usepod.receipt.v1') return '';
  // Localized prose, deterministic values: every sentence resolves through the i18n bundle
  // (pageTF) with the technical fragment as a structured parameter, so a locale changes the
  // WORDS and never the amounts, identifiers or state codes. The amount params are exact
  // pre-formatted strings — locale must never re-parse or round a charge.
  const bits = [pageT('usepod.receipt.header', 'UsePod receipt')];
  const route = r.route || {};
  if (route.class) bits.push(pageTF('usepod.receipt.route', 'route {route_class}{provider_part}{compliance_part}', {
    route_class: String(route.class),
    provider_part: route.provider_id ? ' (' + String(route.provider_id).slice(0, 12) + '…)' : '',
    compliance_part: route.compliance ? ', ' + String(route.compliance) : '',
  }));
  const cost = r.cost || {};
  const x = r.x402 || {};
  const accountless = String(r.transport_mode || '') === 'x402';
  // Each amount in its own unit: USDC microunits and lamports are different money, never one number.
  const amount = (v, unit) => {
    const n = Number(v);
    if (v == null || !isFinite(n)) return '?';
    const u = String(unit || 'usdc_microunit');
    if (u === 'usdc_microunit') return (n / 1e6).toFixed(6) + ' USDC';
    if (u === 'lamport' || u === 'sol_lamport') return (n / 1e9).toFixed(9) + ' SOL';
    return String(n) + ' ' + u;
  };
  const paidUnit = String(cost.unit || 'usdc_microunit');
  // Route ceilings and usage estimates are priced in USDC microunits whatever asset paid.
  const pricingUnit = String(r.pricing_unit || 'usdc_microunit');
  if (cost.exact_atomic != null) bits.push(pageTF('usepod.receipt.charged_exact', 'charged {amount} exactly', { amount: amount(cost.exact_atomic, paidUnit) }));
  else if (cost.usage_upper_bound_atomic != null) bits.push(pageTF('usepod.receipt.charged_upper_bound', 'up to {amount} (usage at the approved ceiling — an upper bound, not the charge)', { amount: amount(cost.usage_upper_bound_atomic, pricingUnit) }));
  else if (cost.liability_bound_atomic != null) bits.push(pageTF('usepod.receipt.charged_liability_bound', 'bounded at {amount} (no usage reported)', { amount: amount(cost.liability_bound_atomic, paidUnit) }));
  if (cost.exact_atomic == null && cost.exact_state) bits.push(pageTF('usepod.receipt.exact_state', 'exact charge {state}', { state: String(cost.exact_state).replace(/_/g, ' ') }));
  if (accountless) {
    // An accountless call has no provider balance; what it has is a wallet payment, proven or not.
    const chain = x.chain_confirmation || {};
    const feeUnit = String(chain.fee_asset || 'SOL') === 'SOL' ? 'lamport' : 'usdc_microunit';
    if (chain.state === 'recorded') bits.push(pageTF('usepod.receipt.paid_chain_confirmed', 'paid {outflow} from your wallet · network fee {fee} · chain-confirmed', { outflow: amount(chain.wallet_outflow_atomic, paidUnit), fee: amount(chain.network_fee_atomic, feeUnit) }));
    else if (x.wallet_outflow_atomic != null) bits.push(pageTF('usepod.receipt.paid_chain_unconfirmed', 'paid up to {outflow} from your wallet (chain confirmation {state})', { outflow: amount(x.wallet_outflow_atomic, paidUnit), state: String(chain.state || 'not recorded').replace(/_/g, ' ') }));
  } else {
    const bal = r.balance_remaining || {};
    if (bal.state === 'reported' && bal.decimal != null && bal.unit === 'USDC') bits.push(pageTF('usepod.receipt.balance_reported', 'balance {balance} USDC after the call', { balance: String(bal.decimal) }));
    else if (bal.state === 'reported' && bal.raw != null) bits.push(pageT('usepod.receipt.balance_header_unit_unverified', 'provider returned a balance header; its unit is unverified'));
    else bits.push(pageT('usepod.receipt.balance_not_reported', 'balance not reported by the provider'));
  }
  // The payment transaction, the inference and any provider credit are three separate facts, never one.
  const inf = r.inference || {};
  if (accountless && x.payment_retained && inf.state === 'result_unknown') bits.push(pageT('usepod.receipt.paid_result_unknown', 'PAID, RESULT UNKNOWN — the wallet paid and no answer arrived; none was invented and no refund is assumed'));
  if (accountless && x.transaction_state) bits.push(pageTF('usepod.receipt.transaction', 'transaction {state}', { state: x.transaction_state === 'confirmed' ? pageT('usepod.receipt.transaction_confirmed', 'confirmed on chain') : String(x.transaction_state).replace(/_/g, ' ') }));
  if (inf.state) bits.push(pageTF('usepod.receipt.inference', 'inference {state}{finish_part}', { state: String(inf.state).replace(/_/g, ' '), finish_part: inf.finish_reason ? ' (' + String(inf.finish_reason) + ')' : '' }));
  const credit = r.provider_credit || {};
  if (accountless && credit.state === 'credited') bits.push(pageTF('usepod.receipt.credit_credited', 'provider credit {amount} on the provider account, not a wallet refund', { amount: amount(credit.atomic, credit.unit) }));
  else if (accountless && credit.state) bits.push(pageTF('usepod.receipt.credit_state', 'provider credit {state}', { state: String(credit.state).replace(/_/g, ' ') }));
  const fee = r.dna_fee || {};
  if (accountless && fee.state === 'accrued_owed') bits.push(pageTF('usepod.receipt.fee_accrued_owed', 'DNA service fee {fee_exact} {asset} (0.1% of the payment) accrued — owed to the treasury, collected with a later native payment when economical{collection_part}', {
    fee_exact: String(fee.fee_exact), asset: String(fee.asset || ''),
    collection_part: fee.collection && fee.collection.planned ? (fee.collection.collected
      ? pageTF('usepod.receipt.fee_previously_collected', ' · previously accrued fees {amount} {asset} collected with this payment', { amount: String(fee.collection.amount_exact), asset: String(fee.collection.asset || '') })
      : pageTF('usepod.receipt.fee_collection_riding', ' · a collection of {amount} {asset} rode this payment: {state}', { amount: String(fee.collection.amount_exact), asset: String(fee.collection.asset || ''), state: String(fee.collection.state_label || fee.collection.state) })) : '',
  }));
  else if (accountless && fee.state === 'reserved_not_owed') bits.push(pageTF('usepod.receipt.fee_reserved', 'DNA service fee reserved (ceiling {ceiling} atomic), owed only once the payment is accepted', { ceiling: String(fee.reserved_ceiling_atomic) }));
  else if (accountless && fee.state && fee.state !== 'not_charged') bits.push(pageTF('usepod.receipt.fee_state', 'DNA service fee {state}', { state: String(fee.state).replace(/_/g, ' ') }));
  const settle = r.settlement || {};
  if (settle.recording === 'retained_unknown') bits.push(pageT('usepod.receipt.liability_retained', 'LIABILITY RETAINED — outcome unknown, reconciled later'));
  else if (settle.recording === 'released_unsent') bits.push(pageT('usepod.receipt.released_unsent', 'released — nothing was sent'));
  else if (settle.recording === 'settled_with_evidence') bits.push(pageT('usepod.receipt.settled_with_evidence', 'settled with evidence'));
  const links = [];
  if (r.price_snapshot_sha256) links.push(pageTF('usepod.receipt.link_snapshot', 'snapshot {id}', { id: String(r.price_snapshot_sha256).slice(0, 10) + '…' }));
  if (r.reservation_id) links.push(pageTF('usepod.receipt.link_reservation', 'reservation {id}', { id: String(r.reservation_id).slice(0, 18) }));
  if (r.route_approval_id) links.push(pageTF('usepod.receipt.link_approval', 'approval {id}', { id: String(r.route_approval_id).slice(0, 14) + '…' }));
  if (x.network) links.push(pageTF('usepod.receipt.link_network', 'network {network}', { network: String(x.network) }));
  if (x.payment_signature) links.push(pageTF('usepod.receipt.link_signature', 'signature {id}', { id: String(x.payment_signature).slice(0, 10) + '…' }));
  if (links.length) bits.push(links.join(' · '));
  if (r.monetary_authority) bits.push(pageTF('usepod.receipt.monetary_authority', 'money: {authority}', { authority: String(r.monetary_authority) }));
  return bits.join(' · ');
}
// A failure row has to state its own cause. Measured: one audit turn failed on three provider lanes,
// every event carrying reason=prompt_budget_exceeded / error_kind=prompt_shape / prompt_budget={…} in
// the ledger this panel had ALREADY fetched -- and the panel drew three bare "Model call failed" lines.
// Recovering the cause took hours of manual tracing over data that was sitting in the browser.
// The cause fields a failure event ACTUALLY carries, read off the emit sites rather than assumed.
// Different families name the cause in different keys, and reading only three of them is what left
// the operator's row content-free:
//   model.call_failed          (core/memory_first_router.py) reason, error_kind, error_class,
//                              exception_class, retryable, prompt_budget
//   model_lane_failed,         (same file) error, fallback_reason, attempt_seconds -- and NO
//   model_lane_contract_failed `reason` at all. That is the operator's row: "Model call failed:
//                              qwen2.5:7b" over a payload already holding the read-timeout string,
//                              because `error` was never one of the keys this function read.
//   model_routing_failed       rejection_reason
//   web_retrieval_failed,      failure_class, failure_reason
//   fx_retrieval_failed
//   tool_failed                status, reason, error_class
// Order runs from the typed class (which answers timeout vs refusal vs circuit-open in one token)
// down to the free-text string, so the scannable part of the line comes first.
const LEDGER_CAUSE_FIELDS = ['error_class', 'exception_class', 'failure_class', 'reason',
  'error_kind', 'rejection_reason', 'failure_reason', 'error', 'fallback_reason'];
// One row's cause line is capped; the untruncated fields stay available in the expanded row and in
// the copied evidence block, so nothing is lost -- only shortened here.
const LEDGER_CAUSE_MAX = 240;
function ledgerCause(e) {
  const bits = [];
  const seen = {};
  LEDGER_CAUSE_FIELDS.forEach((field) => {
    const text = String(e[field] == null ? '' : e[field]).trim();
    if (!text) return;
    // error_kind often repeats reason verbatim, and fallback_reason is usually error verbatim.
    // Compare on a normalized key so PROVIDER_TIMEOUT and provider_timeout collapse to one bit.
    const key = text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    if (seen[key]) return;
    seen[key] = 1;
    // reason/error can be a raw exception string; this is a compact activity row, so cap it.
    bits.push(text.length > 90 ? (text.slice(0, 89) + '…') : text);
  });
  const budget = e.prompt_budget;
  if (budget && typeof budget === 'object') {
    const need = Number(budget.estimated_prompt_tokens_before || budget.estimated_prompt_tokens_after || 0);
    const have = Number(budget.available_prompt_tokens || 0);
    if (need > 0 && have > 0) bits.push(need + ' tokens vs ' + have + ' available');
  }
  // Qualifiers, not causes: they explain a cause that is already on the line and must never turn a
  // SUCCESSFUL row into one that looks like it has something to report. A candidate that died on
  // the 60s read timeout and one that was refused in 40ms are the same sentence without the clock.
  if (bits.length) {
    const seconds = Number(e.attempt_seconds || 0);
    if (seconds > 0) bits.push('after ' + (seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)) + 's');
    if (e.retryable === false) bits.push('not retryable');
  }
  const line = bits.join(' · ');
  return line.length > LEDGER_CAUSE_MAX ? (line.slice(0, LEDGER_CAUSE_MAX - 1) + '…') : line;
}
// "Brave Search · 3 sources", or "" when the receipt cannot name a provider.
//
// A retrieval row used to render as the bare line "Web retrieval done: Completed
// web retrieval." with an empty sub-line: the browser had already fetched the
// count and the domains and dropped them, and no provider field existed to read
// because no emitter wrote one. This is a read-only projection of what the
// server sent -- nothing here infers a provider, so a receipt that could not
// name one shows no name rather than a plausible guess.
function ledgerRetrievalAttribution(e) {
  const label = String(e.provider_label || '').trim();
  if (!label) return '';
  const keyed = String(e.keyed_or_keyless || '').trim();
  const head = keyed === 'keyed' ? label : (label + ' (keyless)');
  const lifecycle = String(e.lifecycle || '').trim();
  if (lifecycle && lifecycle !== 'succeeded') {
    const why = String(e.failure_class || e.status || lifecycle).trim();
    return head + ' · ' + why;
  }
  const count = Number(e.source_count || 0);
  return head + ' · ' + count + (count === 1 ? ' source' : ' sources');
}
function ledgerRow(e) {
  const t = String(e.event_type || '');
  if (LEDGER_SKIP[t]) return null;
  // One displayed row per state transition. A dotted model.call_started/_completed whose EMITTER
  // stamped lane_receipted=true is the adapter-boundary receipt of a call the router also wraps in
  // model_lane_* / model_lane_verifier_* receipts -- the lane pair is the displayed running/
  // answered row. This is a per-event field the SOURCE set (core/memory_first_router.py::
  // _invoke_manifest), not a pairing guess made here; unwrapped calls (classifier, race, mux)
  // carry no flag and keep rendering, and model.call_failed is never skipped -- its reason is the
  // failure evidence.
  if (e.lane_receipted && (t === 'model.call_started' || t === 'model.call_completed')) return null;
  const rawMap = LEDGER_MAP[t] || ['run', '•', ''];
  // The label localizes through the deterministic catalog; the event type, class and icon
  // stay exactly what the ledger authority shipped (stable keys, never prose parsing).
  const map = [rawMap[0], rawMap[1], rawMap[2] ? pageT('activity.ledger.' + String(t), rawMap[2]) : ''];
  const msg = String(e.message || '').trim();
  let title, sub = '';
  if (t === 'scope_resolved') {
    title = 'Scope: ' + (e.workspace_root || msg || '(workspace)');
    if (e.project_id) sub = 'project ' + e.project_id;
  } else if (t === 'task_classified' || t === 'task_received') {
    title = map[2] + (e.task_class ? (': ' + e.task_class) : (msg ? (': ' + msg) : ''));
  } else if (t.indexOf('tool_') === 0) {
    title = humanToolTitle(e, t, map);
    // The recorded tool id, redacted args, and runtime result explain what actually ran. A selected
    // tool stays running; only an execution event may describe it as finished.
    const bits = [];
    if (e.tool_name) bits.push(String(e.tool_name));
    if (e.tool_args) bits.push(String(e.tool_args));
    if (msg && msg !== title) bits.push(msg);
    if (e.status) bits.push(String(e.status));
    // Why it failed, not just that it did. A tool step's message is the step SUMMARY ("Tool step
    // finished.") and its status is a bucket ("error"); the cause the executor recorded lives in
    // reason/error_class, and nothing here read them.
    const toolCause = ledgerCause(e);
    if (toolCause) bits.push(toolCause);
    sub = bits.join(' · ');
  } else if (t.indexOf('model_lane') === 0 || t.indexOf('model.') === 0 || t.indexOf('model_routing') === 0 || t === 'model_verification_receipt') {
    const m = ledgerModelName(e);
    title = (map[2] || t) + (m ? (': ' + m) : '');
    const bits = [];
    if (e.lane) bits.push(pageT('activity.lane', 'lane') + ' ' + e.lane);
    const provider = ledgerProviderName(e);
    if (provider) bits.push(String(provider));
    const cause = ledgerCause(e);
    if (cause) bits.push(cause);
    // A failure with no reason field still has to say more than the provider name.
    else if (map[0] === 'fail' && msg && msg !== title) bits.push(msg);
    // The UsePod lane's bounded receipt, when this call carries one, as a readable line -- the
    // raw JSON object is evidence for the logs, not a rendering for a person.
    const upReceipt = ledgerUsePodReceipt(e);
    if (upReceipt) bits.push(upReceipt);
    if (e.verification_receipt) {
      const verification = e.verification_receipt.verification || {};
      bits.push(String(verification.status || 'CLAIMED'));
      bits.push(String(verification.explanation || pageT('activity.identity_not_verified', 'Model identity not independently verified.')));
    }
    sub = bits.join(' · ') || (msg && msg !== title ? msg : '');
  } else if (t.indexOf('web_retrieval') === 0) {
    // WHICH provider answered, and on whose key. The row's own message already
    // carries the same line for surfaces that read only `message`, so the title
    // stays the plain label and the provenance goes in the sub-line rather than
    // being printed twice.
    title = map[2] || t;
    const bits = [];
    const attribution = ledgerRetrievalAttribution(e);
    if (attribution) bits.push(attribution);
    const domains = Array.isArray(e.source_domains) ? e.source_domains.filter(Boolean) : [];
    if (domains.length) bits.push(domains.slice(0, 3).join(', '));
    const cause = ledgerCause(e);
    if (cause) bits.push(cause);
    sub = bits.join(' · ') || (msg && !ledgerSameText(msg, title) ? msg : '');
  } else {
    // A label and a message that say the same thing are one fact, not two: "Web retrieval failed"
    // plus its own message rendered as "Web retrieval failed: Web retrieval failed.".
    title = map[2] ? (map[2] + (msg && !ledgerSameText(msg, map[2]) ? (': ' + msg) : '')) : (msg || t);
    sub = ledgerCause(e);
  }
  return { cls: map[0], icon: map[1], title: title, sub: sub };
}
// Whether two labels are the same sentence modulo punctuation and case.
function ledgerSameText(a, b) {
  const norm = (v) => String(v == null ? '' : v).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  return norm(a) === norm(b);
}
function humanToolTitle(e, eventType, map) {
  const tool = String(e.tool_name || '').trim();
  const category = activityCategoryFor(tool)[0];
  const active = { tests: pageT('activity.tool.active.tests', 'Running tests'), reads: pageT('activity.tool.active.reads', 'Reading'), edits: pageT('activity.tool.active.edits', 'Editing'), searches: pageT('activity.tool.active.searches', 'Searching'), commands: pageT('activity.tool.active.commands', 'Running command'), tools: pageT('activity.tool.active.tools', 'Running tool') };
  const finished = { tests: pageT('activity.tool.finished.tests', 'Test command finished'), reads: pageT('activity.tool.finished.reads', 'Read finished'), edits: pageT('activity.tool.finished.edits', 'Edit finished'), searches: pageT('activity.tool.finished.searches', 'Search finished'), commands: pageT('activity.tool.finished.commands', 'Command finished'), tools: pageT('activity.tool.finished.tools', 'Tool finished') };
  const failed = { tests: pageT('activity.tool.failed.tests', 'Test command failed'), reads: pageT('activity.tool.failed.reads', 'Read failed'), edits: pageT('activity.tool.failed.edits', 'Edit failed'), searches: pageT('activity.tool.failed.searches', 'Search failed'), commands: pageT('activity.tool.failed.commands', 'Command failed'), tools: pageT('activity.tool.failed.tools', 'Tool failed') };
  if (eventType === 'tool_selected') return active[category] || pageT('activity.tool.active.running', 'Running');
  if (eventType === 'tool_failed') return failed[category] || pageT('activity.tool.failed.tools', 'Tool failed');
  if (eventType === 'tool_executed') return finished[category] || pageT('activity.tool.finished.tools', 'Tool finished');
  // Only tool_selected / tool_executed / tool_failed are lifecycle outcomes. Every OTHER tool_*
  // event fell through to "finished" and inherited a per-category noun it has no claim to, so
  // `tool_repeat_blocked` -- a fail row in LEDGER_MAP -- drew a red icon over the words "Search
  // finished", and `tool_synthesizing` reported a search that had finished while it was running.
  // The map already holds the right label for these; use it.
  return (map && map[2]) || finished[category] || 'Tool finished';
}
function renderLedgerRows(events) {
  let html = '';
  for (const e of events) { const row = ledgerRow(e); if (row) html += panelRow(row.cls, row.icon, row.title, row.sub); }
  return html;
}

// ---- One block a user can paste into a bug report ----
// A report that says "it failed" costs a round trip to find out which turn, on which route, down
// which lane, and against which build -- and by then the ledger window may have rolled past it.
// Everything needed to skip that round trip is already in the rows this panel has fetched; this
// only selects and serializes it.
//
// Derived, never invented: the failure rows are the SAME ledgerRow() output the panel draws (so a
// pasted block cannot disagree with what the user is looking at), the per-failure fields are the
// SAME activityDetailLines() projection the expanded row shows, and the build commit is the one
// the server substituted into this page. A field that was never recorded is reported as not
// recorded rather than omitted, because "no lane ran" and "lanes were not captured" are different
// bugs.
function ledgerFailureRows(events) {
  const out = [];
  for (const e of (events || [])) {
    const row = ledgerRow(e);
    if (row && row.cls === 'fail') out.push({ event: e, row: row });
  }
  return out;
}
// Which model candidates this turn actually reached, in order, one entry each.
//
// Deduplicated on provider/model ALONE, never on provider/model/lane: a candidate is named by
// several events per attempt and only some of them carry `lane`, so keying on the lane too listed
// the same candidate twice and made a single failed attempt read as two.
const LEDGER_LANE_EVENT_TYPES = {
  model_lane_selected: 1, model_lane_started: 1, model_lane_completed: 1,
  model_lane_failed: 1, model_lane_contract_failed: 1,
  'model.call_started': 1, 'model.call_failed': 1,
};
function ledgerLanesRun(events) {
  const byCandidate = {}, order = [];
  for (const e of (events || [])) {
    if (!LEDGER_LANE_EVENT_TYPES[String(e.event_type || '')]) continue;
    const provider = ledgerProviderName(e), model = ledgerModelName(e);
    if (!provider && !model) continue;
    const key = (provider || '?') + '/' + (model || '?');
    if (!byCandidate[key]) { byCandidate[key] = { lane: '' }; order.push(key); }
    if (!byCandidate[key].lane && e.lane) byCandidate[key].lane = String(e.lane);
  }
  return order.map((key) => key + (byCandidate[key].lane ? (' (' + byCandidate[key].lane + ')') : ''));
}
function turnFailureReport(events, buildCommit) {
  const list = events || [];
  const failures = ledgerFailureRows(list);
  if (!failures.length) return '';
  let turnId = '', route = '', workspace = '';
  for (const e of list) {
    if (!turnId) turnId = String(e.client_turn_id || e.turn_key || '');
    const t = String(e.event_type || '');
    if (t === 'task_classified' && e.task_class) route = String(e.task_class);
    if (t === 'scope_resolved' && e.workspace_root) workspace = String(e.workspace_root);
  }
  // An unsubstituted server placeholder is not a commit; say so rather than pasting the token.
  const commit = (buildCommit && String(buildCommit).indexOf('__') !== 0) ? String(buildCommit) : '';
  const unknown = '(not recorded)';
  const lanes = ledgerLanesRun(list);
  const lines = [
    'VOOL failure report',
    'Turn: ' + (turnId || unknown),
    'Route: ' + (route || unknown),
    'Build: ' + (commit || unknown),
  ];
  if (workspace) lines.push('Workspace: ' + workspace);
  lines.push('Lanes run: ' + (lanes.length ? lanes.join('; ') : unknown));
  lines.push('');
  lines.push('Failed (' + failures.length + '):');
  for (const f of failures) {
    lines.push('  - ' + f.row.title + (f.row.sub ? ('  [' + f.row.sub + ']') : ''));
    // The export keys each fact by its CANONICAL field id, not the localized label, so a
    // pasted report names the same fields in every app language (cross-locale comparability).
    for (const kv of activityDetailTriples(f.event, f.row.title)) {
      lines.push('      ' + kv[0] + ': ' + String(kv[2]).split('\n').join('\n        '));
    }
  }
  return lines.join('\n');
}
// Whether this turn really executed nothing. The authoritative execution ledger answers this when
// it has a record for the turn (`execution_truth` on /api/runtime/events, derived from the same
// facts the signed receipts are built from); the raw event scan is the fallback for turns that
// predate the ledger.
//
// The scan alone was a fifth independent reconstruction of execution truth, and it disagreed with
// the other four: it recognises only `tool_executed`/`tool_selected`, so 80 turns whose retrieval
// was recorded under a different event type rendered "No tool ran -- answered directly" over work
// that had genuinely gone out to the network. Widening the list of event types it greps for would
// have been the same mistake a sixth time; the panel now reads the answer instead of guessing it.
function ledgerRanNoTool(events, truth) {
  if (truth && typeof truth.ran_tool === 'boolean') {
    // Fail closed on receipt/event disagreement: when the independent witness saw a non-model
    // execution the ledger never recorded, "nothing ran" is exactly the claim that must not be
    // made -- the missing record is the defect, not evidence of idleness.
    const missing = (truth.witness && truth.witness.consistent === false)
      ? (truth.witness.missing || []) : [];
    if (missing.some((m) => String(m).indexOf('model:') !== 0)) return false;
    // Retrieval and governed effects are execution too: a turn that fetched the web through the
    // research lane invoked no explicit tool, and it still did not "answer directly".
    return !(truth.ran_tool || truth.ran_retrieval || truth.ran_effect);
  }
  return !events.some((e) => { const t = String(e.event_type || ''); return t === 'tool_executed' || t === 'tool_selected'; });
}
// The authoritative summary for the turn on screen, or null when the server sent none.
function runExecutionTruth(run) {
  if (!run || !run.executionTruth) return null;
  return run.executionTruth[String(run.turnId || '')] || null;
}

// ---- Nested Activity tree ----
// Groups the same flat ledger this panel already renders into a real disclosure hierarchy:
// work log -> category (Commands / Tests / Files read / Files changed / Searches / Tool calls /
// Runtime) -> individual tool call -> its raw fields. No new server data: every value here already
// arrives over /api/runtime/events, this only regroups and re-discloses it.
//
// Category needles mirror core/task_event_model.py::_TOOL_STAGE_RULES (same words, same order) so
// a tool lands in the same bucket here as the stage the server already computes for it elsewhere.
// Kept as a hand-synced literal rather than a shared import: the server module is Python, this is
// the browser.
//
// These rules classify a TOOL CALL by its name. A tool name is evidence of what was attempted, and
// nothing more -- so the mutation bucket is labelled for the calls it actually holds ("Edit tool
// calls"), not "Files changed". "Files changed" is built separately, from canonical mutation
// receipts, in the `changes` category below.
const ACTIVITY_CATEGORY_RULES = [
  [['search', 'research', 'web', 'lookup', 'browse', 'find', 'grep', 'index'], 'searches', 'Searches'],
  [['read', 'fetch', 'open', 'inspect', 'view', 'cat', 'load'], 'reads', 'Files read'],
  [['edit', 'write', 'patch', 'apply', 'create_file', 'modify', 'format'], 'edits', 'Edit tool calls'],
  [['test', 'pytest', 'verify', 'assert'], 'tests', 'Tests'],
  [['run', 'exec', 'start', 'server', 'preview', 'build', 'shell', 'command', 'install'], 'commands', 'Commands'],
];
// ---- Files changed: canonical mutation receipts only ----
// LIVE DEFECT this replaces: "Files changed (24)" was the count of tool calls whose NAME matched an
// edit needle above, so 24 rows reading "event: tool_selected / result: Running workspace.write_file"
// were reported as 24 changed files. A selected tool is an intention; a started tool is an
// intention in flight; a denied or failed write changed nothing at all.
//
// The runtime already emits exactly one canonical receipt per mutation attempt
// (core/runtime_execution_tools.py::_emit_mutation_activity_event), carrying the real affected path
// in `canonical_target` plus the outcome. Only a receipt that says the operation COMPLETED can put
// a file in this category, and the category counts unique paths -- never operations.
const ACTIVITY_MUTATION_SUCCESS_TYPES = { workspace_mutation_completed: 1, workspace_mutation_rollback_completed: 1 };
const ACTIVITY_MUTATION_UNSUCCESSFUL_TYPES = { workspace_mutation_failed: 1, workspace_mutation_rollback_conflict: 1 };
const ACTIVITY_CHANGES_CATEGORY = ['changes', 'Files changed'];
const ACTIVITY_CATEGORY_FALLBACK = ['tools', 'Tool calls'];
const ACTIVITY_RUNTIME_CATEGORY = ['runtime', 'Runtime'];
// The two ledger event types that OPEN a tool call, paired against whichever of the three closes it.
const ACTIVITY_TOOL_START_TYPES = { tool_selected: 1 };
const ACTIVITY_TOOL_END_TYPES = { tool_executed: 1, tool_failed: 1, audit_step: 1, audit_budget_refused: 1 };
const ACTIVITY_CATEGORY_ORDER = ['changes', 'commands', 'tests', 'reads', 'edits', 'searches', 'tools', 'runtime'];
// Localized rollups: one catalog key per category with the ICU-lite plural the engine and
// the mirrored browser formatter both resolve. English fallbacks reproduce the historical
// concatenation byte-for-byte (n === 1 → singular), so an unwired locale renders as before.
const ACTIVITY_ROLLUP_KEYS = {
  changes: 'activity.rollup.changes',
  commands: 'activity.rollup.commands',
  tests: 'activity.rollup.tests',
  reads: 'activity.rollup.reads',
  // Deliberately NOT "changed N files": this counts attempts to edit, which is a different fact.
  edits: 'activity.rollup.edits',
  searches: 'activity.rollup.searches',
  tools: 'activity.rollup.tools',
};
const ACTIVITY_ROLLUP_FALLBACKS = {
  changes: 'changed {n, plural, one {{n} file} other {{n} files}}',
  commands: 'ran {n, plural, one {{n} command} other {{n} commands}}',
  tests: 'ran {n, plural, one {{n} test} other {{n} tests}}',
  reads: 'read {n, plural, one {{n} file} other {{n} files}}',
  edits: 'made {n, plural, one {{n} edit tool call} other {{n} edit tool calls}}',
  searches: 'ran {n, plural, one {{n} search} other {{n} searches}}',
  tools: 'used {n, plural, one {{n} tool} other {{n} tools}}',
};
const ACTIVITY_ROLLUP_PHRASE = {};
Object.keys(ACTIVITY_ROLLUP_KEYS).forEach((key) => {
  ACTIVITY_ROLLUP_PHRASE[key] = (n) => pageTF(ACTIVITY_ROLLUP_KEYS[key], ACTIVITY_ROLLUP_FALLBACKS[key], { n: n });
});
// (event field, kv label). Only fields actually present on the row are ever shown -- nothing here
// is computed or guessed, it is a read-only projection of what the server already sent.
// [canonical field id, English label]. The RENDERED label resolves through the catalog
// (activity.field.<id>) so the expandable technical view localizes like every other
// surface; the canonical id is what the copied failure report exports, so copied evidence
// stays locale-independent (cross-locale comparability by design). The VALUES are raw
// diagnostic data and are never translated.
const ACTIVITY_DETAIL_FIELDS = [
  ['tool_name', 'tool'], ['tool_args', 'args'], ['message', 'result'], ['status', 'status'],
  ['reason', 'reason'], ['error_kind', 'error'], ['workspace_root', 'workspace'],
  // The rest of what a failure event carries. Enumerated from the emit sites (see
  // LEDGER_CAUSE_FIELDS): the compact row caps its cause line, so this disclosure -- and the
  // copied evidence built from it -- is where the untruncated string has to survive.
  ['error', 'error text'], ['fallback_reason', 'fallback reason'],
  ['rejection_reason', 'rejection reason'], ['exception_class', 'exception class'],
  ['failure_class', 'failure class'], ['failure_reason', 'failure reason'],
  ['retryable', 'retryable'], ['locality', 'locality'],
  ['attempt_seconds', 'attempt seconds'], ['phase', 'phase'],
  ['contract_warnings', 'contract warnings'],
  ['provider_health_recorded', 'provider health recorded'],
  ['task_class', 'task class'], ['lane', 'orchestration lane'], ['requested_provider_id', 'requested provider'],
  ['requested_model', 'requested model'], ['planned_provider_id', 'policy provider'],
  ['planned_model_id', 'policy model'], ['selected_provider_id', 'selected provider'],
  ['selected_model', 'selected model'], ['actual_adapter_provider_id', 'actual adapter provider'],
  ['actual_adapter_model_id', 'actual adapter model'], ['provider_id', 'recorded provider'],
  ['model_id', 'recorded model'],
  // Canonical mutation-receipt fields. A "Files changed" row exists because of one of these
  // receipts, so opening it must show the evidence it rests on rather than just the path again.
  ['canonical_target', 'path'], ['tool_intent', 'mutation tool'], ['action', 'action'],
  ['permission_decision', 'permission'], ['result_state', 'result state'],
  ['before_hash', 'before sha256'], ['after_hash', 'after sha256'], ['diff_summary', 'diff'],
  ['operation_completed_at', 'completed at'], ['rollback_id', 'rollback id'],
  ['error_class', 'error class'], ['conflict_reason', 'conflict'], ['daemon_sha', 'daemon commit'],
  // Retrieval provenance. `provider_id` is already above as 'recorded provider';
  // these are the rest of what a `vool.web_retrieval_receipt.v1` carries. They
  // were being fetched by the browser and dropped on the floor, which is why a
  // successful search expanded to nothing but `status`, `action` and `event`.
  ['provider_label', 'search provider'], ['keyed_or_keyless', 'credential'],
  ['source_count', 'sources returned'], ['source_domains', 'source domains'],
  ['lifecycle', 'lifecycle'], ['retrieval_id', 'retrieval id'], ['query_hash', 'query hash'],
  ['event_type', 'event'],
  // The classification of an empty provider reply (output_budget_exhausted, reasoning_only…).
  // The `empty_reply` FACTS it rests on are a structural dict, rendered by their own formatter
  // below (emptyReplyDetailLines) rather than as a bare field, because String(dict) is
  // "[object Object]" — which is how these facts stayed invisible in the Activity panel while
  // the server recorded them on every empty-reply failure.
  ['empty_reply_class', 'empty reply class'],
];
// One field's rendered label: the catalog's localized label under its stable
// activity.field.<canonical-id> key, English by fallback.
function activityFieldLabel(field, englishLabel) {
  return pageT('activity.field.' + field, englishLabel);
}

function activityCategoryFor(toolName) {
  const lowered = String(toolName || '').toLowerCase();
  for (const [needles, key, label] of ACTIVITY_CATEGORY_RULES) {
    if (needles.some((n) => lowered.indexOf(n) !== -1)) return [key, label];
  }
  return ACTIVITY_CATEGORY_FALLBACK;
}
function activityToolLabel(e) {
  const purpose = String(e.purpose || '').trim();
  return String(e.tool_name || '').trim() || (purpose ? ('audit.' + purpose) : 'audit.step');
}
// The real affected paths carried by one mutation receipt. Multi-path operations (rollback,
// apply_unified_diff across files) are emitted comma-joined by the server, so one receipt can
// legitimately testify to several files -- but only to the ones it actually names.
function activityMutationPaths(e) {
  const raw = String((e && e.canonical_target) || '').trim();
  if (!raw) return [];
  const paths = [], seen = {};
  for (const part of raw.split(',')) {
    const path = part.trim();
    if (!path || seen[path]) continue;
    seen[path] = 1;
    paths.push(path);
  }
  return paths;
}
// Every clause below is an independent veto. A receipt has to be a completion type, not be flagged
// not-ok, not have been denied, carry no error class, and name at least one path before it may put
// a file in "Files changed". An event that satisfies some but not all of them changes nothing.
function activityMutationSucceeded(e) {
  if (!e) return false;
  if (!ACTIVITY_MUTATION_SUCCESS_TYPES[String(e.event_type || '')]) return false;
  if (e.ok === false) return false;
  if (String(e.permission_decision || '') === 'denied') return false;
  if (String(e.error_class || '').trim()) return false;
  return activityMutationPaths(e).length > 0;
}
// One path's sub-line. When a path was written more than once the row says "N operations" in
// words, because the count above it is a count of FILES and must not quietly absorb repeats.
function activityChangeSub(e, operations) {
  const bits = [];
  const action = String((e && e.action) || '').trim();
  if (action) bits.push(action);
  const intent = String((e && e.tool_intent) || '').trim();
  if (intent) bits.push(intent);
  if (operations > 1) bits.push(pageTF('activity.operations', '{n, plural, one {{n} operation} other {{n} operations}}', { n: operations }));
  return bits.join(' · ');
}
// The technical disclosure's lines as TRIPLES [canonical id, rendered label, raw value]:
// the label localizes through the catalog (activity.field.*), the canonical id is
// locale-stable and is what the copied failure report exports, and the value is raw
// diagnostic data. activityDetailLines() keeps the historical [label, value] pair shape
// the panel renders and the tests exercise.
function activityDetailTriples(e, skipText) {
  if (!e) return [];
  const triples = [], seen = {};
  if (skipText) seen[skipText] = 1;
  for (const [field, label] of ACTIVITY_DETAIL_FIELDS) {
    const value = e[field];
    if (value === undefined || value === null || value === '') continue;
    const text = String(value);
    // Don't repeat what the row's own title already says. Booleans are exempt: `retryable: false`
    // and `provider health recorded: false` are two independent facts that happen to share the
    // word "false", and collapsing them silently drops one of the two fields a triage reads.
    if (typeof value !== 'boolean') {
      if (seen[text]) continue;
      seen[text] = 1;
    }
    triples.push([field, activityFieldLabel(field, label), text]);
  }
  for (const line of usePodReceiptDetailLines(e)) triples.push(line);
  for (const line of providerVerificationDetailLines(e)) triples.push(line);
  for (const line of emptyReplyDetailLines(e)) triples.push(line);
  return triples;
}
function activityDetailLines(e, skipText) {
  return activityDetailTriples(e, skipText).map((t) => [t[1], t[2]]);
}
// The `empty_reply` facts a model.call_failed carries when the provider returned a readable body
// with no usable answer. Structural evidence only — ids, finish reason, usage (reasoning tokens
// when the provider reports them), the ceiling the request carried, and WHICH fields held text,
// never the text itself (core.normalized_provider_result.empty_reply_diagnostics guarantees the
// shape; this renderer only formats it). Labels localize through activity.field.empty_reply.*;
// the composed value fragments (prompt/completion/… tokens, "none") are canonical diagnostic
// vocabulary and stay English for cross-locale comparability.
const EMPTY_REPLY_LABELS = {
  'empty_reply.finish_reason': 'empty reply finish reason',
  'empty_reply.usage': 'empty reply usage',
  'empty_reply.output_ceiling': 'output ceiling sent',
  'empty_reply.text_fields': 'fields that held text',
  'empty_reply.response_id': 'provider response id',
  'empty_reply.returned_model': 'provider returned model',
};
function emptyReplyDetailLines(e) {
  const r = e && e.empty_reply;
  if (!r || typeof r !== 'object') return [];
  const lines = [];
  const add = (id, value) => { if (value !== undefined && value !== null && value !== '') lines.push([id, pageT('activity.field.' + id, EMPTY_REPLY_LABELS[id] || id), String(value)]); };
  const finish = r.finish_reason
    ? String(r.finish_reason) + (r.native_finish_reason ? ' (native ' + r.native_finish_reason + ')' : '')
    : String(r.native_finish_reason || '');
  add('empty_reply.finish_reason', finish);
  const tokens = [];
  if (r.prompt_tokens != null) tokens.push('prompt ' + r.prompt_tokens);
  if (r.completion_tokens != null) tokens.push('completion ' + r.completion_tokens);
  if (r.reasoning_tokens != null) tokens.push('reasoning ' + r.reasoning_tokens);
  if (tokens.length) add('empty_reply.usage', tokens.join(' · ') + (r.total_tokens != null ? ' · total ' + r.total_tokens : ''));
  add('empty_reply.output_ceiling', r.max_tokens_sent);
  const fields = [];
  if (r.content_present) fields.push('content ' + r.content_chars + ' chars');
  if (r.reasoning_present) fields.push('reasoning ' + r.reasoning_chars + ' chars');
  if (r.tool_calls_present) fields.push('tool calls ' + r.tool_call_count);
  if (r.legacy_text_present) fields.push('legacy text field');
  if (r.refusal_present) fields.push('refusal field');
  if (r.error_present) fields.push('provider error field');
  if (r.choices === 0) fields.push('no choices');
  add('empty_reply.text_fields', fields.length ? fields.join(' · ') : 'none');
  add('empty_reply.response_id', r.provider_response_id);
  if (r.provider_model) add('empty_reply.returned_model', r.provider_model);
  return lines;
}
const VERIFY_LABELS = {
  'verify.verification': 'verification',
  'verify.proves': 'what this proves',
  'verify.requested_model': 'requested model',
  'verify.model_sent': 'model sent',
  'verify.returned_model': 'returned model claim',
  'verify.model_comparison': 'model comparison',
  'verify.gateway': 'gateway',
  'verify.serving_provider': 'serving provider claim',
  'verify.route': 'route',
  'verify.request': 'request',
  'verify.provider_request': 'provider request',
  'verify.call': 'call',
  'verify.operation': 'operation',
  'verify.recorded_at': 'recorded at',
  'verify.actual_charge': 'actual charge',
  'verify.cost_bound': 'cost upper bound',
  'verify.prices_1m': 'approved prices per 1M tokens',
  'verify.snapshot': 'listing snapshot',
  'verify.eligible_1m': 'eligible listing per 1M tokens',
  'verify.upstream_key': 'provider {key}',
  'verify.upstream_header': 'provider header {key}',
  'verify.evidence': 'verification evidence',
  'verify.binding': 'receipt binding',
};
function providerVerificationDetailLines(e) {
  const r = e && e.verification_receipt;
  if (!r || r.schema !== 'vool.provider-verification.v1') return [];
  const lines = [];
  const add = (id, value, extra) => {
    if (value === undefined || value === null || value === '') return;
    const english = VERIFY_LABELS[id] || id;
    // A label carrying a raw upstream field name keeps that name canonical; only the
    // surrounding words localize, with the name as a structured parameter.
    let label;
    if (english.indexOf('{key}') !== -1) {
      label = pageTF('activity.field.' + id, english, { key: String(extra || '') });
    } else {
      label = pageT('activity.field.' + id, english);
    }
    lines.push([id, label, String(value)]);
  };
  add('verify.verification', (r.verification || {}).status || 'CLAIMED');
  add('verify.proves', (r.verification || {}).explanation);
  add('verify.requested_model', r.requested_model);
  add('verify.model_sent', r.selected_model);
  add('verify.returned_model', r.returned_model || pageT('activity.field.not_reported', 'not reported'));
  add('verify.model_comparison', r.model_match);
  add('verify.gateway', r.gateway_provider_id);
  add('verify.serving_provider', r.provider_id || pageT('activity.field.not_reported', 'not reported'));
  add('verify.route', r.route || pageT('activity.field.not_reported', 'not reported'));
  add('verify.request', r.request_id);
  add('verify.provider_request', r.provider_request_id || pageT('activity.field.not_reported', 'not reported'));
  add('verify.call', r.call_id);
  add('verify.operation', r.operation_id);
  add('verify.recorded_at', r.timestamp);
  const actual = r.actual_cost || {}, bound = r.cost_bound || {}, quote = r.quoted_price || {};
  add('verify.actual_charge', actual.state === 'reported' ? String(actual.amount) + ' ' + String(actual.currency) : pageT('activity.field.not_reported_by_provider', 'not reported by provider'));
  if (bound.amount != null) add('verify.cost_bound', String(bound.amount) + ' ' + String(bound.currency));
  const ceiling = quote.approved_ceiling || {};
  if (ceiling.input != null && ceiling.output != null) add('verify.prices_1m', 'input ' + (ceiling.input / 1e6) + ' / output ' + (ceiling.output / 1e6) + ' USDC');
  add('verify.snapshot', quote.snapshot_sha256 || quote.state);
  for (const p of quote.eligible_prices || []) {
    if (p.input_microunits_per_million != null && p.output_microunits_per_million != null)
      add('verify.eligible_1m', (p.route_class || '') + ' · input ' + (p.input_microunits_per_million / 1e6) + ' / output ' + (p.output_microunits_per_million / 1e6) + ' USDC');
  }
  const upstream = r.upstream_metadata || {};
  for (const [key, value] of Object.entries(upstream.response || {})) add('verify.upstream_key', value, key);
  for (const [key, value] of Object.entries(upstream.headers || {})) add('verify.upstream_header', value, key);
  for (const ref of (r.verification || {}).evidence_references || []) add('verify.evidence', ref);
  add('verify.binding', r.binding_sha256);
  return lines;
}

// The UsePod receipt's details: the full destination and the exact atomic values the one-line receipt shortens.
// Labels localize through activity.field.usepod.*; every amount, unit, id and signature passes
// through exactly as recorded — never re-parsed, never rounded, never translated.
const USEPOD_DETAIL_LABELS = {
  'usepod.operation': 'usepod operation',
  'usepod.paid_to': 'paid to',
  'usepod.paid_from': 'paid from wallet',
  'usepod.network': 'payment network',
  'usepod.amount_exact': 'payment amount (exact)',
  'usepod.transaction': 'payment transaction',
  'usepod.transaction_state': 'transaction state',
  'usepod.outflow_exact': 'wallet outflow on chain (exact)',
  'usepod.network_fee_exact': 'network fee on chain (exact)',
  'usepod.chain_confirmation': 'chain confirmation',
  'usepod.quote': 'quote',
  'usepod.account': 'usepod account',
  'usepod.inference': 'inference',
  'usepod.credit_exact': 'provider credit (exact)',
  'usepod.credit': 'provider credit',
  'usepod.dna_fee': 'DNA service fee',
  'usepod.dna_fee_exact': 'DNA service fee (exact)',
  'usepod.dna_fee_ceiling': 'DNA service fee ceiling reserved',
  'usepod.dna_fees_owed': 'DNA fees owed after this call',
  'usepod.dna_treasury': 'DNA treasury owner',
  'usepod.dna_collection': 'DNA fee collection',
  'usepod.liability_bound': 'liability bound (exact)',
  'usepod.reservation': 'reservation',
};
function usePodReceiptDetailLines(e) {
  const r = e && e.provider_receipt;
  if (!r || typeof r !== 'object' || String(r.schema || '') !== 'vool.usepod.receipt.v1') return [];
  const out = [];
  const add = (id, value) => { if (value !== undefined && value !== null && value !== '') out.push([id, pageT('activity.field.' + id, USEPOD_DETAIL_LABELS[id] || id), String(value)]); };
  const x = r.x402 || {};
  const cost = r.cost || {};
  const chain = x.chain_confirmation || {};
  const credit = r.provider_credit || {};
  add('usepod.operation', r.operation_id);
  if (String(r.transport_mode || '') === 'x402') {
    add('usepod.paid_to', x.pay_to);
    add('usepod.paid_from', x.payer_wallet);
    add('usepod.network', x.network);
    if (x.amount_atomic != null) add('usepod.amount_exact', String(x.amount_atomic) + ' ' + String(cost.unit || '') + (x.asset ? ' (' + String(x.asset) + ')' : ''));
    add('usepod.transaction', x.payment_signature);
    add('usepod.transaction_state', x.transaction_state);
    if (chain.wallet_outflow_atomic != null) add('usepod.outflow_exact', String(chain.wallet_outflow_atomic) + ' ' + String(cost.unit || ''));
    if (chain.network_fee_atomic != null) add('usepod.network_fee_exact', String(chain.network_fee_atomic) + ' ' + (String(chain.fee_asset || 'SOL') === 'SOL' ? 'lamport' : 'usdc_microunit'));
    if (chain.state && chain.state !== 'recorded') add('usepod.chain_confirmation', chain.state);
    add('usepod.quote', x.quote_id);
  } else {
    add('usepod.account', r.credential_fingerprint);
  }
  add('usepod.inference', (r.inference || {}).state);
  if (credit.state === 'credited') add('usepod.credit_exact', String(credit.atomic) + ' ' + String(credit.unit || 'usdc_microunit') + ' to ' + String(credit.account || 'the provider account'));
  else add('usepod.credit', credit.state);
  const fee = r.dna_fee || {};
  if (String(r.transport_mode || '') === 'x402' && fee.state && fee.state !== 'not_charged') {
    add('usepod.dna_fee', String(fee.state_label || fee.state));
    if (fee.fee_exact != null) add('usepod.dna_fee_exact', String(fee.fee_exact) + ' ' + String(fee.asset || '') + ' = ' + String(fee.fee_exact_atomic) + ' atomic units (' + String(fee.rate_bps) + ' bps of ' + String(fee.basis_atomic) + ')');
    if (fee.reserved_ceiling_atomic != null) add('usepod.dna_fee_ceiling', String(fee.reserved_ceiling_atomic) + ' ' + String(fee.unit || ''));
    if (fee.owed_after_atomic != null) add('usepod.dna_fees_owed', String(fee.owed_after_atomic) + ' ' + String(fee.unit || '') + ' whole units, carry ' + String(fee.carry_after_numerator) + '/' + String(fee.fee_numerator_scale || 10000));
    add('usepod.dna_treasury', fee.treasury_owner);
    if (fee.collection) add('usepod.dna_collection', fee.collection.planned ? String(fee.collection.amount_exact) + ' ' + String(fee.collection.asset || '') + ' · ' + String(fee.collection.state_label || fee.collection.state) + (fee.collection.tx_signature ? ' · transaction ' + String(fee.collection.tx_signature) : '') : 'none with this payment (' + String(fee.collection.reason || 'not planned').replace(/_/g, ' ') + ')');
  }
  if (cost.liability_bound_atomic != null) add('usepod.liability_bound', String(cost.liability_bound_atomic) + ' ' + String(cost.unit || ''));
  add('usepod.reservation', r.reservation_id);
  return out;
}

// Builds {categories:[{key,label,items:[...]}], totalActions} from a flat, already-turn-scoped
// ledger slice. Tool calls are paired start->end the same way the live status card already pairs
// them (applyTaskEvent's tool.completed handler): most-recently-opened call of that name resolves
// first. Non-tool rows are not dropped -- they move into a "Runtime" folder instead of vanishing.
function buildActivityTree(events) {
  const cats = {};
  const pending = {};   // tool label -> stack of still-open items for that tool
  const changedByPath = {};   // canonical path -> the single row that path owns
  const ensureCat = (key, label) => cats[key] || (cats[key] = { key, label, items: [] });

  for (const e of (events || [])) {
    const t = String(e.event_type || '');
    if (LEDGER_SKIP[t]) continue;

    // Canonical mutation receipts. Handled BEFORE the tool branches so a change can only ever be
    // claimed by a receipt, and so a receipt is never also counted as a tool call.
    if (ACTIVITY_MUTATION_SUCCESS_TYPES[t] || ACTIVITY_MUTATION_UNSUCCESSFUL_TYPES[t]) {
      const row = ledgerRow(e);
      if (!activityMutationSucceeded(e)) {
        // A failed, denied or pathless receipt is still shown -- filed under Runtime, where it
        // reads as what it is. It must never reach "Files changed".
        if (row) {
          const [rk, rl] = ACTIVITY_RUNTIME_CATEGORY;
          ensureCat(rk, rl).items.push({ id: 'ai-' + e.seq, tool: '', display: row, endEvent: e });
        }
        continue;
      }
      const [ck, cl] = ACTIVITY_CHANGES_CATEGORY;
      const cat = ensureCat(ck, cl);
      for (const path of activityMutationPaths(e)) {
        let item = changedByPath[path];
        if (!item) {
          // Keyed by PATH, so ten successful writes to one file stay one changed file.
          item = { id: 'ac-' + cat.items.length, tool: path, path: path, status: 'completed', operations: 0, startEvent: null, endEvent: e };
          changedByPath[path] = item;
          cat.items.push(item);
        }
        item.operations += 1;
        item.endEvent = e;   // the newest receipt is the one that describes the file's current state
        item.display = { cls: 'ok', icon: '✎', title: path, sub: activityChangeSub(e, item.operations) };
      }
      continue;
    }

    if (ACTIVITY_TOOL_START_TYPES[t] || ACTIVITY_TOOL_END_TYPES[t]) {
      const toolName = activityToolLabel(e);
      const row = ledgerRow(e) || { cls: 'run', icon: '▸', title: toolName, sub: '' };
      if (ACTIVITY_TOOL_START_TYPES[t]) {
        const item = { id: 'ai-' + e.seq, tool: toolName, status: 'running', display: row, startEvent: e, endEvent: null };
        (pending[toolName] || (pending[toolName] = [])).push(item);
        const [key, label] = activityCategoryFor(toolName);
        ensureCat(key, label).items.push(item);
        continue;
      }
      const stack = pending[toolName];
      const failed = (t === 'tool_failed') || (t === 'audit_budget_refused')
        || (t === 'audit_step' && /^(error:|rejected:)/i.test(String(e.result || '')));
      let item = stack && stack.length ? stack.pop() : null;
      if (!item) {
        item = { id: 'ai-' + e.seq, tool: toolName, startEvent: null };
        const [key, label] = activityCategoryFor(toolName);
        ensureCat(key, label).items.push(item);
      }
      item.status = failed ? 'failed' : 'completed';
      item.display = row;
      item.endEvent = e;
      continue;
    }

    // Non-tool ledger row (scope, routing, verification, terminal state, ...): same title/sub
    // this panel has always shown for it, just filed under Runtime instead of sitting flat.
    const row = ledgerRow(e);
    if (!row) continue;
    const [key, label] = ACTIVITY_RUNTIME_CATEGORY;
    ensureCat(key, label).items.push({ id: 'ai-' + e.seq, tool: '', display: row, endEvent: e });
  }

  const categories = ACTIVITY_CATEGORY_ORDER.filter((k) => cats[k] && cats[k].items.length).map((k) => cats[k]);
  // Pair start/end events chronologically above; order only the completed presentation.
  const latestSeq = (item) => Number((item.endEvent || item.startEvent || {}).seq || 0);
  for (const cat of categories) cat.items.sort((a, b) => latestSeq(b) - latestSeq(a));
  categories.sort((a, b) => latestSeq(b.items[0]) - latestSeq(a.items[0]));
  const totalActions = categories.filter((c) => c.key !== 'runtime').reduce((n, c) => n + c.items.length, 0);
  return { categories, totalActions };
}
function activityRollupText(tree) {
  const parts = [];
  for (const cat of tree.categories) {
    if (cat.key === 'runtime') continue;
    const phrase = ACTIVITY_ROLLUP_PHRASE[cat.key];
    if (phrase) parts.push(phrase(cat.items.length));
  }
  if (!parts.length) return tree.categories.length ? pageT('activity.rollup.runtime_only', 'Runtime activity only') : pageT('activity.rollup.none', 'No actions taken');
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
function activityWorkLogSummary(tree) {
  return pageTF('activity.worklog', 'Work log · {n, plural, one {{n} action} other {{n} actions}}', { n: tree.totalActions });
}
function stepsRollupText(steps) {
  if (!steps || !steps.length) return '';
  const STAGE_BUCKET = { Running: 'commands', Testing: 'tests', Reading: 'reads', Editing: 'edits', Searching: 'searches' };
  const counts = {}, order = [];
  for (const s of steps) {
    const key = STAGE_BUCKET[s.stage] || 'tools';
    if (counts[key] == null) { counts[key] = 0; order.push(key); }
    counts[key] += 1;
  }
  const parts = order.map((key) => (ACTIVITY_ROLLUP_PHRASE[key] || ((n) => pageTF('activity.rollup.actions', '{n, plural, one {{n} action} other {{n} actions}}', { n: n })))(counts[key]));
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Opening the price review from a refusal row only OPENS the gate: the operator edits and
// explicitly confirms there, and confirmation saves limits -- it never retries or sends the
// refused message. Delegated once, on the panel that renders the rows.
document.addEventListener('click', function(ev) {
  const button = ev.target && ev.target.closest ? ev.target.closest('[data-vool-price-review]') : null;
  if (!button || !window.VoolPriceGate) return;
  const id = String(button.getAttribute('data-vool-price-review-id') || '').trim();
  if (!id) return;
  ev.preventDefault();
  const label = String(button.getAttribute('data-vool-price-review-label') || id);
  window.VoolPriceGate.review({ kind: 'review', id: id, provider: String(button.getAttribute('data-vool-price-review-provider') || 'usepod'), label: label });
});
function activityKvHtml(lines) {
  if (!lines.length) return '';
  return '<div class="xp-kv">' + lines.map(([k, v]) => '<div class="xp-kv-row"><span class="xp-kv-k">' + esc(k) + '</span><span class="xp-kv-v mono">' + esc(v) + '</span></div>').join('') + '</div>';
}
function activityItemDetailLines(item, skipText) {
  const lines = [], seen = {};
  for (const event of [item.startEvent, item.endEvent]) {
    for (const line of activityDetailLines(event, skipText)) {
      const key = line[0] + '\n' + line[1];
      if (!seen[key]) { seen[key] = 1; lines.push(line); }
    }
  }
  return lines;
}
// The typed pre-send price refusal a UsePod row can carry: the one failure whose recovery is
// exactly this mission's price review. Matched on the event's own reason codes, never on prose.
function priceReviewTargetForEvent(e) {
  if (!e) return null;
  const causes = [e.rejection_reason, e.reason, e.error, e.error_kind].map((v) => String(v == null ? '' : v));
  const refused = causes.some((text) => text.indexOf('usepod_dispatch_refused:route_price_above_approved_bound') !== -1);
  if (!refused) return null;
  const model = String(e.requested_model || e.model_id || e.selected_model || '').trim();
  if (!model) return null;
  return { provider: 'usepod', id: model, label: model };
}
function activityItemHtml(item, openState) {
  const cls = item.status === 'completed' ? 'ok' : item.status === 'failed' ? 'fail' : 'run';
  const icon = item.display ? item.display.icon : '•';
  const title = item.display ? item.display.title : (item.tool || 'Action');
  const sub = item.display ? item.display.sub : '';
  const kv = activityItemDetailLines(item, title);
  const kvHtml = activityKvHtml(kv);
  const review = priceReviewTargetForEvent(item.endEvent || item.startEvent);
  // The review target travels as PLAIN data attributes, never as JSON: the panel's rendering
  // re-serializes row markup, and a quoted JSON payload in a quoted attribute truncated at its
  // first inner quote (measured: the attribute read back as "{"), the click's JSON.parse threw,
  // and the action silently did nothing. Provider/model ids carry no quotes, so esc() is enough.
  const reviewHtml = review
    ? '<div class="xp-kv"><div class="xp-kv-row"><span class="xp-kv-k">' + esc(pageT('activity.field.action', 'action')) + '</span><span class="xp-kv-v">'
      + '<button type="button" class="vg-btn" data-vool-price-review="1'
      + '" data-vool-price-review-provider="' + esc(review.provider)
      + '" data-vool-price-review-id="' + esc(review.id)
      + '" data-vool-price-review-label="' + esc(review.label)
      + '">' + esc(pageT('activity.review_prices', 'Review price limits')) + '</button>'
      + ' ' + esc(pageT('activity.review_prices_note', '(opens the max input/output prices this model dispatches under; opening reviews nothing, sends nothing)')) + '</span></div></div>'
    : '';
  if (!kvHtml && !sub && !reviewHtml) {
    // Nothing to drill into -- a plain row, not a disclosure triangle with nothing behind it.
    return '<div class="xp-row-flat ' + cls + '"><span class="ic">' + icon + '</span><div class="bd">' + esc(title) + '</div></div>';
  }
  const openAttr = openState[item.id] ? ' open' : '';
  return '<details class="xp-item ' + cls + '" data-node-id="' + esc(item.id) + '"' + openAttr + '>'
    + '<summary><span class="ic">' + icon + '</span><span class="bd">' + esc(title) + '</span></summary>'
    + '<div class="xp-item-body">' + (sub ? '<div class="st">' + esc(sub) + '</div>' : '') + kvHtml + reviewHtml + '</div>'
    + '</details>';
}
function activityCategoryHtml(cat, openState) {
  const openAttr = openState[cat.key] !== false ? ' open' : '';   // categories default OPEN
  const items = cat.items.map((it) => activityItemHtml(it, openState)).join('');
  return '<details class="xp-cat" data-node-id="' + esc(cat.key) + '"' + openAttr + '>'
    + '<summary>' + esc(pageT('activity.category.' + cat.key, cat.label)) + ' <span class="xp-cat-count">(' + cat.items.length + ')</span></summary>'
    + '<div class="xp-cat-body">' + items + '</div>'
    + '</details>';
}
// `openState` persists per-run (run.activityOpen) across every re-render, so a rebuild-from-scratch
// each poll (simplest way to guarantee no duplicate rows -- see wireActivityTreeToggles) never
// costs the user their place. `ended` decides only the DEFAULT for nodes the user never touched:
// open while the turn runs (so live progress is visible), collapsed to one line once it finishes.
// One event stream -> the turns that produced it, in first-seen order.
//
// A turn is keyed by its id, NOT by a run of adjacent rows: an untagged event (a mode change, an
// approval) can land in the middle of a turn, and grouping by adjacency split that turn in two.
// Measured on a real 307-event chat, that reported 17 turns where there were 12. Untagged events
// are grouped by adjacency, because they have no id to group by and each block is simply whatever
// happened at that point between turns.
function groupLedgerByTurn(events) {
  const groups = [];
  const byTurn = new Map();
  for (const e of events || []) {
    const turnId = String(e.client_turn_id || '');
    if (turnId) {
      const existing = byTurn.get(turnId);
      if (existing) { existing.events.push(e); continue; }
      const group = { turnId: turnId, events: [e] };
      byTurn.set(turnId, group);
      groups.push(group);
      continue;
    }
    const last = groups[groups.length - 1];
    if (last && !last.turnId) { last.events.push(e); continue; }
    groups.push({ turnId: '', events: [e] });
  }
  let number = 0;
  for (const g of groups) if (g.turnId) g.number = ++number;
  return groups;
}
function appendToChatLedger(owner, event) {
  if (!owner || !event) return false;
  if (!owner.chatLedger) owner.chatLedger = [];
  if (!owner.chatLedgerSeen) owner.chatLedgerSeen = Object.create(null);
  // The chat's trusted workspace, learned from the server's own scope events. The until-off
  // bypass approval binds server-side to THIS root, so the grant cannot follow the chat into a
  // different workspace later.
  if (event.workspace_root) owner.workspaceRoot = String(event.workspace_root);
  const seq = Number(event.seq) || 0;
  // The server's durable identity is (session_id, seq). Owner identity is the trusted fallback for
  // older payloads that omit session_id. Missing/non-positive seq cannot safely be deduplicated, so
  // retain it rather than collapsing two distinct legacy events by guessed content.
  const sessionId = String(owner.chatId || event.session_id || '');
  const key = seq > 0 ? (sessionId + ':' + seq) : '';
  if (key && owner.chatLedgerSeen[key]) return false;
  if (key) owner.chatLedgerSeen[key] = 1;
  // Overlapping pages are expected after reconnect; a page may also arrive internally out of order.
  // Keep the projection monotonic so grouping and baseline calculations never depend on arrival order.
  if (seq > 0 && owner.chatLedger.length && Number(owner.chatLedger[owner.chatLedger.length - 1].seq || 0) > seq) {
    let at = owner.chatLedger.length;
    while (at > 0 && Number(owner.chatLedger[at - 1].seq || 0) > seq) at--;
    owner.chatLedger.splice(at, 0, event);
  } else {
    owner.chatLedger.push(event);
  }
  if (seq > 0) owner.chatLedgerCursor = Math.max(Number(owner.chatLedgerCursor) || 0, seq);
  // Named fragment call site: the companion's worker scenes read the SAME ledger truth the
  // Agents tab renders — one derivation, one count, no invented crowd. Only agent-node rows can
  // change the count, so the fold runs only for them.
  const kind = String(event.type || event.event_type || '');
  if ((kind === 'agent_node_started' || kind === 'agent_node_completed')
      && window.VoolCompanion && window.VoolCompanion.agents) {
    try {
      const live = agentRowsFrom(owner.chatLedger, Date.now()).filter((row) => row.running).length;
      window.VoolCompanion.agents(owner.chatId, live);
    } catch (e) {}
  }
  return true;
}
function resetChatLedger(owner) {
  if (!owner) return;
  owner.chatLedger = [];
  owner.chatLedgerCursor = 0;
  owner.chatLedgerSeen = Object.create(null);
  owner.chatLedgerBusy = false;
}
// The whole chat, newest turn first. The live turn (and, with no live turn, the most recent one)
// opens by default; the rest are one click away instead of being discarded.
function renderChatActivity(owner, opts) {
  const options = opts || {};
  const events = (owner && owner.chatLedger) || [];
  if (!events.length) return '';
  const groups = groupLedgerByTurn(events);
  const openState = (owner.chatActivityOpen || (owner.chatActivityOpen = {}));
  lastActivityOpenStore = openState;
  const turns = groups.filter((g) => g.turnId).length;
  const trees = [], nodeIds = [], labels = [];
  let html = '<div class="xp-note">' + esc(activityScopeSummary(events.length, turns, owner.chatLedgerTruncated)) + '</div>'
    + activityTreeActionsHtml();
  const ordered = groups.slice().sort((a, b) =>
    Number(b.events[b.events.length - 1].seq || 0) - Number(a.events[a.events.length - 1].seq || 0));
  ordered.forEach((group, index) => {
    const isLatest = index === 0;
    const nodeId = 'turn:' + (group.turnId || ('untagged:' + group.events[0].seq));
    nodeIds.push(nodeId);
    const open = openState[nodeId] === undefined ? isLatest : openState[nodeId];
    const tree = buildActivityTree(group.events);
    trees.push(tree);
    const label = group.turnId
      ? ('Turn ' + group.number + (isLatest && options.live ? ' · running' : '') + ' — ' + activityWorkLogSummary(tree))
      : ('Between turns — ' + group.events.length + ' event' + (group.events.length === 1 ? '' : 's'));
    labels.push(label);
    html += '<details class="xp-turn" data-node-id="' + esc(nodeId) + '"' + (open ? ' open' : '') + '>'
      + '<summary>' + esc(label) + '</summary>'
      + '<div class="xp-turn-body">' + activityTreeBodyHtml(tree, openState, nodeId) + '</div>'
      + '</details>';
  });
  lastActivityTrees = trees;
  lastActivityTurnNodeIds = nodeIds;
  lastActivityTurnLabels = labels;
  lastActivityTree = trees.length ? trees[0] : null;
  return html;
}
function activityScopeSummary(eventCount, turnCount, truncated) {
  const turnsText = turnCount + (turnCount === 1 ? ' turn' : ' turns');
  const base = 'This chat — ' + turnsText + ', ' + eventCount + (eventCount === 1 ? ' event' : ' events');
  // A bounded view says it is bounded. The loader stops at 8 pages of 200.
  return truncated ? (base + ' (the 1600 most recent; older events not loaded)') : base;
}

function renderActivityTree(events, openState, opts) {
  const tree = buildActivityTree(events);
  // Assigned BEFORE the empty-tree return: leaving the previous chat's tree in place would let the
  // header Copy hand over another chat's evidence under this chat's scope heading.
  lastActivityTree = tree;
  // Same reason, and the same line: an empty tree must REPLACE the previous chat's trees here,
  // before the early return below, or Copy hands over the last chat that had any under this
  // chat's heading.
  lastActivityTrees = tree.categories.length ? [tree] : [];
  lastActivityOpenStore = openState;
  lastActivityTurnNodeIds = [];
  lastActivityTurnLabels = [];
  if (!tree.categories.length) return '';
  const ended = !!(opts && opts.ended);
  const rootOpen = openState.__root === undefined ? !ended : openState.__root;
  const headline = ended ? activityWorkLogSummary(tree) : activityRollupText(tree);
  const cats = tree.categories.map((c) => activityCategoryHtml(c, openState)).join('');
  return activityTreeActionsHtml()
    + '<details class="xp-worklog" data-node-id="__root"' + (rootOpen ? ' open' : '') + '>'
    + '<summary>' + esc(headline) + '</summary>'
    + '<div class="xp-worklog-body">' + cats + '</div>'
    + '</details>';
}
// The work log for ONE already-built tree, without the bulk-action bar -- a per-turn section owns
// its own disclosure, and one Expand all / Copy all at the top governs every turn on screen.
function activityTreeBodyHtml(tree, openState, scopeId) {
  if (!tree || !tree.categories.length) return '<div class="xp-empty">No recorded activity for this turn.</div>';
  return tree.categories.map((c) => activityCategoryHtml(c, openState)).join('');
}
// ---- Bulk disclosure + copy ----
// Inspecting evidence used to mean hand-opening the work log, then every category, then each of
// 30+ rows; and the header Copy read `xpBodyEl.innerText`, which in a browser omits the contents of
// a closed <details> -- so a copy taken from the default (collapsed) panel silently handed over a
// few summary lines instead of the evidence. These controls fix both: the tree opens or closes in
// one click, and Copy all serializes from the tree MODEL, so it is complete whatever is open.
let lastActivityTree = null;
// Every tree the panel is currently showing, so Copy all covers the whole chat rather than whichever
// turn happened to render last.
let lastActivityTrees = [];
// The turn-section ids currently on screen, so Expand all / Collapse all reach sections the user has
// never touched, and their headings so copied evidence says which turn each block came from.
let lastActivityTurnNodeIds = [];
let lastActivityTurnLabels = [];
// The open-state object the panel last rendered the Activity tab from.
let lastActivityOpenStore = null;
function activityTreeActionsHtml() {
  return '<div class="xp-tree-actions">'
    + '<button type="button" class="xp-mini" data-activity-action="expand">Expand all</button>'
    + '<button type="button" class="xp-mini" data-activity-action="collapse">Collapse all</button>'
    + '<button type="button" class="xp-mini" data-activity-action="copy">Copy all</button>'
    + '</div>';
}
function setActivityTreeOpen(open) {
  const trees = lastActivityTrees.length ? lastActivityTrees : (lastActivityTree ? [lastActivityTree] : []);
  if (!trees.length) return;
  const store = currentActivityOpenStore();
  store.__root = open;
  // Every turn on screen, not just the newest: a collapse that left older turns open would report a
  // state the panel is not in, and an expand that skipped them would hide the evidence it claims to
  // be revealing. Driven by the ids actually RENDERED -- a section the user has never toggled has no
  // entry in the store yet, so walking the store alone reached only one of fourteen sections.
  for (const nodeId of lastActivityTurnNodeIds) store[nodeId] = open;
  for (const tree of trees) {
    for (const cat of tree.categories) {
      store[cat.key] = open;
      for (const item of cat.items) store[item.id] = open;
    }
  }
  // The poll's no-change rebuild skip must not swallow THIS render. The skip's signature tracks
  // content (ledger length, steps, model) and not disclosure state, so after writing only open
  // keys the signature still matches and renderPanelBody would return without repainting --
  // measured as "Expand all / Collapse all frequently do nothing" whenever the ledger was idle,
  // the exact moments the buttons exist for. Resetting the signature makes the deliberate
  // re-render always repaint while leaving the poll's protective skip untouched.
  lastActivityBodySig = '';
  renderPanel();
}
// Plain-text evidence for one built tree: every category, every row, and every detail line behind
// every row -- including rows the user never opened.
function activityTreeText(tree) {
  if (!tree || !tree.categories.length) return '';
  const lines = [activityWorkLogSummary(tree)];
  for (const cat of tree.categories) {
    lines.push('');
    lines.push(cat.label + ' (' + cat.items.length + ')');
    for (const item of cat.items) {
      const title = item.display ? item.display.title : (item.tool || 'Action');
      const sub = item.display ? item.display.sub : '';
      lines.push('  - ' + title + (sub ? ('  [' + sub + ']') : ''));
      for (const [k, v] of activityItemDetailLines(item, title)) {
        // A diff is genuinely multi-line; indent its continuations so a pasted block still reads
        // as one field rather than collapsing into the outline.
        lines.push('      ' + k + ': ' + String(v).split('\n').join('\n        '));
      }
    }
  }
  return lines.join('\n');
}
// Non-bubbling in older engines, so this is capture-phase on a container that survives every
// re-render (only its innerHTML is replaced) rather than one listener rebound per node per render.
// The bulk controls and the toggle handler must write to the store the panel actually RENDERED
// with. The Activity tab now renders the whole chat from `chatActivityOpen`, but other callers pass
// their own store; picking by tab alone made Expand all write to one object while the markup was
// built from another, and four of six nodes stayed shut.
function currentActivityOpenStore() {
  if (panelTab === 'Activity' && lastActivityOpenStore) return lastActivityOpenStore;
  return view.run ? (view.run.activityOpen || (view.run.activityOpen = {})) : view.recoveredActivityOpen;
}
function wireActivityTreeToggles() {
  xpBodyEl.addEventListener('toggle', (ev) => {
    const details = ev.target;
    if (!details || details.tagName !== 'DETAILS' || !xpBodyEl.contains(details)) return;
    const id = details.getAttribute('data-node-id');
    if (id) currentActivityOpenStore()[id] = details.open;
  }, true);
  // Bulk disclosure on POINTERDOWN, delegated capture-phase on this container (which survives
  // every re-render; only its innerHTML is replaced). The per-render click handlers die with
  // their nodes: the ledger poll legitimately rebuilds the Activity body whenever events arrive,
  // and a rebuild between pointerdown and pointerup cancels the in-flight click -- measured as
  // "Expand all / Collapse all mostly do nothing" exactly when a turn is moving. pointerdown
  // fires before any poll replacement can occur; canceling it suppresses the compatibility
  // click so the action cannot run twice. Keyboard activation (Enter/Space) still raises click,
  // which the per-render handlers keep serving.
  xpBodyEl.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    const target = ev.target;
    if (!target || !target.closest || !xpBodyEl.contains(target)) return;
    const button = target.closest('[data-activity-action]');
    if (!button || !xpBodyEl.contains(button)) return;
    const action = button.getAttribute('data-activity-action');
    if (action === 'expand') { ev.preventDefault(); setActivityTreeOpen(true); return; }
    if (action === 'collapse') { ev.preventDefault(); setActivityTreeOpen(false); return; }
  }, true);
}
wireActivityTreeToggles();

async function pollLedger(run) {
  if (!run || !run.chatId) return;
  const owner = chatState(run.chatId);
  if (owner.chatLedgerBusy) return;
  owner.chatLedgerBusy = true;
  try {
    // Drain any backlog in one tick (the endpoint caps at 200/page) so a long session history doesn't
    // stall this turn's tail behind old events; keep only rows tagged with THIS turn.
    let pages = 0;
    while (pages < 8) {
      pages++;
      const res = await fetch('/api/runtime/events?session=' + encodeURIComponent(run.chatId) + '&after=' + (owner.chatLedgerCursor || 0) + '&limit=200');
      if (!res.ok) break;
      const d = await res.json();
      const events = d.events || [];
      for (const e of events) {
        const seq = Number(e.seq) || 0;
        const key = String(e.seq);
        // The chat's own ledger takes every event, which is what the Activity tab renders.
        appendToChatLedger(owner, e);
        // A different turn's events belong to another (queued) turn. An UNTAGGED event belongs to
        // this turn only if it arrived after the turn began -- otherwise it is a mode change or an
        // approval from earlier in the chat, and replaying it here is what made every turn look
        // like it had done nothing but change modes.
        if (e.client_turn_id ? (e.client_turn_id !== run.turnId) : (seq <= (run.ledgerBaseline || 0))) {
          run.ledgerSeen[key] = 1;
          continue;
        }
        if (run.ledgerSeen[key]) continue;
        run.ledgerSeen[key] = 1;
        run.ledger.push(e);
      }
      // Carry the server's authoritative per-turn execution summary alongside the raw rows, so the
      // panel renders what actually ran rather than re-deriving it from event types.
      if (d.execution_truth) run.executionTruth = Object.assign({}, run.executionTruth || {}, d.execution_truth);
      if (typeof d.next_after === 'number') owner.chatLedgerCursor = Math.max(owner.chatLedgerCursor || 0, d.next_after);
      if (events.length < 200) break;   // caught up to the tail
    }
    if (isDisplayed(run.chatId) && run === view.run && panelTab === 'Activity' && document.body.classList.contains('panel-open')) renderPanel();
  } catch (e) { /* transient; the next tick retries */ }
  finally { owner.chatLedgerBusy = false; }
}
// ---- Evidence scope ----
// The panel used to render /api/runtime/sessions -- every chat in VOOL -- underneath the current
// turn, so a chat's evidence was mixed with unrelated projects' activity by default. Scope is now
// explicit and defaults to the chat on screen; the global view is still one selection away.
const PANEL_SCOPES = [['chat', 'Current chat'], ['project', 'Current project'], ['all', 'All activity']];
// How many chats a wider scope will read evidence from. A cap is needed (one request per chat), and
// whatever it drops is stated in the panel rather than silently trimmed.
const SCOPE_SESSION_CAP = 12;
let panelScope = localStorage.getItem('vool_panel_scope') || 'chat';
if (!PANEL_SCOPES.some((s) => s[0] === panelScope)) panelScope = 'chat';
function currentProjectName() {
  const pid = view.projectId || '';
  const project = pid && _serverProjects[pid];
  return (project && project.name) ? project.name : 'General';
}
// null means "no filter" -- the global scope. Otherwise the exact set of chats in scope.
function scopeSessionSet() {
  if (panelScope === 'all') return null;
  if (panelScope === 'chat') return new Set([displayedChat]);
  const pid = view.projectId || '';
  const set = new Set(_lastSessions.filter((s) => (s.project_id || '') === pid).map((s) => s.session_id));
  set.add(displayedChat);   // the open chat is always part of its own project's scope
  return set;
}
function scopeWhere() {
  if (panelScope === 'chat') return 'this chat';
  if (panelScope === 'project') return 'project ' + currentProjectName();
  return 'VOOL';
}
// "No receipts for this chat yet." / "...for project Lumen yet." / "...anywhere in VOOL yet."
function emptyScopeText(kind) {
  if (panelScope === 'all') return 'No ' + kind + ' recorded anywhere in VOOL yet.';
  return 'No ' + kind + ' for ' + scopeWhere() + ' yet.';
}
function chatTitleFor(sid) {
  const chat = _lastSessions.find((s) => s.session_id === sid) || {};
  return chat.title || String(sid || '').slice(-8) || 'Chat';
}
function scopeKey() { return panelScope + '|' + displayedChat + '|' + (view.projectId || ''); }
// Durable per-chat ledgers for a wider scope. Chat scope needs none of this: the open chat's own
// run and recovered ledger are already in memory.
let scopedEvidence = { key: '', loading: false, sessions: [], dropped: 0 };
function scopeCandidateSessionIds() {
  const set = scopeSessionSet();
  const ids = [];
  const push = (id) => { if (id && !ids.includes(id) && (!set || set.has(id))) ids.push(id); };
  push(displayedChat);                                     // the open chat reads first
  activityHistory.forEach((item) => push(item.session_id));
  _lastSessions.forEach((s) => push(s.session_id));
  return ids;
}
async function loadScopedEvidence() {
  const key = scopeKey();
  if (panelScope === 'chat') { scopedEvidence = { key: key, loading: false, sessions: [], dropped: 0 }; return; }
  if (scopedEvidence.key === key) return;                  // already loaded or loading for this scope
  scopedEvidence = { key: key, loading: true, sessions: [], dropped: 0 };
  const ids = scopeCandidateSessionIds();
  const capped = ids.slice(0, SCOPE_SESSION_CAP);
  const out = [];
  for (const sid of capped) {
    try {
      const r = await fetch('/api/runtime/events?session=' + encodeURIComponent(sid) + '&limit=200');
      if (!r.ok) continue;
      const d = await r.json();
      const events = d.events || [];
      if (events.length) out.push({ session_id: sid, title: chatTitleFor(sid), events: events });
    } catch (e) { /* one unreadable chat must not empty the whole scope */ }
  }
  if (scopeKey() !== key) return;                          // the user moved on mid-load
  scopedEvidence = { key: key, loading: false, sessions: out, dropped: Math.max(0, ids.length - capped.length) };
  renderPanelBody();
}
function scopeNoteHtml() {
  const note = document.getElementById('xpScopeNote');
  if (!note) return;
  let text = '';
  if (panelScope === 'chat') text = 'Showing only this chat\u2019s evidence.';
  else if (panelScope === 'project') text = 'Showing every chat in ' + currentProjectName() + '.';
  else text = 'Showing activity from every chat in VOOL.';
  if (panelScope !== 'chat' && scopedEvidence.dropped) {
    text += ' Read the ' + SCOPE_SESSION_CAP + ' most recent chats; ' + scopedEvidence.dropped + ' older chat'
      + (scopedEvidence.dropped === 1 ? '' : 's') + ' not read.';
  }
  note.textContent = text;
}

// After a page refresh there's no view.run; rebuild the LAST turn's timeline from the ledger so the
// Activity panel is never blank when the user looks back at what just happened.
let activityHistory = [];
let sidebarActivityHistory = [];
let sidebarLifecycleRequest = null;
function fetchSidebarLifecycle() {
  if (!sidebarLifecycleRequest) {
    sidebarLifecycleRequest = fetchJsonWithin('/api/runtime/sessions?summary=1', null, 10000)
      .finally(() => { sidebarLifecycleRequest = null; });
  }
  return sidebarLifecycleRequest;
}
let runtimeActivityRequest = null;
function fetchRuntimeActivity() {
  // Concurrent detail readers share one request. Periodic sidebar refreshes
  // use the smaller lifecycle projection above.
  if (!runtimeActivityRequest) {
    runtimeActivityRequest = fetchJsonWithin('/api/runtime/sessions', null, 10000)
      .finally(() => { runtimeActivityRequest = null; });
  }
  return runtimeActivityRequest;
}
async function loadActivityHistory() {
  // Bind the chat BEFORE the await: a switch mid-flight must not file this chat's recovered task
  // into whichever bucket happens to be on screen when the response lands.
  const chatId = displayedChat;
  const target = chatState(chatId);
  try {
    const data = await fetchRuntimeActivity();
    activityHistory = Array.isArray(data.sessions) ? data.sessions : [];
    target.recoveredTask = activityHistory.find((item) => item.session_id === chatId && item.resume_available) || null;
  } catch (e) { activityHistory = []; target.recoveredTask = null; }
  renderPanel();
}
function historyProjectLabel(sid) {
  const chat = _lastSessions.find((item) => item.session_id === sid) || {};
  const project = chat.project_id && _serverProjects[chat.project_id];
  return (project && project.name) || 'General';
}
function renderActivityHistoryHtml() {
  // The whole point of QA-050-025: in chat scope this cross-chat list is not shown at all, so the
  // open chat's evidence is never mixed with other projects'. The chat's own timeline is already
  // rendered above it.
  if (panelScope === 'chat') return '';
  const set = scopeSessionSet();
  const items = activityHistory.filter((item) => !set || set.has(item.session_id));
  if (!items.length) return '';
  let html = '<div class="xp-history-title">'
    + esc(panelScope === 'project' ? ('Chats in ' + currentProjectName()) : 'Every chat in VOOL')
    + '</div>';
  for (const item of items) {
    const h = item.execution_history || {}, chat = _lastSessions.find((s) => s.session_id === item.session_id) || {};
    const bounded = h.bounded_execution || {};
    const task = h.request_preview || item.request_preview || h.title || 'Task';
    const status = h.request_state_label || h.status || item.status || 'unknown';
    const chatTitle = chat.title || String(item.session_id || '').slice(-8) || 'Chat';
    const evidence = [];
    if (h.latest_tool) evidence.push('last action ' + h.latest_tool);
    if (Array.isArray(h.changed_paths) && h.changed_paths.length) evidence.push(h.changed_paths.length + ' file' + (h.changed_paths.length === 1 ? '' : 's') + ' changed');
    if (bounded.tool_receipt_count) evidence.push(bounded.tool_receipt_count + ' receipt' + (bounded.tool_receipt_count === 1 ? '' : 's'));
    if (bounded.approval_state && bounded.approval_state !== 'not_required') evidence.push('approval ' + bounded.approval_state);
    const savedReviewState = normalizeRunReviewState(bounded.model_review_state || 'not_run');
    const savedReview = reviewStatePresentation(savedReviewState);
    if (savedReview.meta) evidence.push(savedReview.meta);
    if (bounded.validation_state && bounded.validation_state !== 'not_run') evidence.push('validation ' + bounded.validation_state);
    if (bounded.failure_count) evidence.push(bounded.failure_count + ' failure' + (bounded.failure_count === 1 ? '' : 's'));
    const tool = evidence.length ? (' · ' + evidence.join(' · ')) : '';
    // A chat that recorded failures gets a direct Report affordance next to its card:
    // the missing user path from an Activity failure event to the sanitized report
    // flow. Sibling button, not nested -- interactive elements cannot nest validly.
    const reportBtn = bounded.failure_count
      ? '<button type="button" class="xp-mini" data-history-report="' + esc(item.session_id || '') + '" title="Build a sanitized bug report from this chat\'s failure">Report</button>'
      : '';
    html += '<div class="xp-history-row"' + (item.session_id === displayedChat ? ' data-current="1"' : '') + '>'
      + '<button type="button" class="xp-history-card' + (item.session_id === displayedChat ? ' current' : '') + '" data-history-session="' + esc(item.session_id || '') + '">'
      + '<span class="ht"><span>' + esc(task) + '</span><span class="hs">' + esc(status) + '</span></span>'
      + '<span class="hm">' + esc(historyProjectLabel(item.session_id) + ' · ' + chatTitle + tool) + '</span></button>'
      + reportBtn + '</div>';
  }
  return html;
}
function renderRecoveryHtml() {
  if (!view.recoveredTask) return '';
  const h = view.recoveredTask.execution_history || {}, cp = h.checkpoint || {};
  return '<div class="xp-recovery"><b>Task interrupted by application restart</b><p>' + esc(h.request_preview || view.recoveredTask.request_preview || 'Saved work can be continued from its checkpoint.') + '</p>'
    + '<div class="xp-recovery-actions"><button type="button" data-recovery="resume">Resume</button><button type="button" data-recovery="review">Review activity</button><button type="button" data-recovery="cancel" data-checkpoint="' + esc(cp.checkpoint_id || view.recoveredTask.last_checkpoint_id || '') + '">Cancel task</button></div></div>';
}
// Complete evidence text for whichever tab is active, independent of what is expanded on screen.
// Falls back to the rendered text only for the tabs that have no structured model behind them.
function panelEvidenceText() {
  if (panelTab === 'Activity') {
    // Every turn on screen. Serializing only the newest would hand over one turn's evidence under a
    // heading that says "this chat" -- the same silent narrowing that closed <details> used to cause.
    const trees = lastActivityTrees.length ? lastActivityTrees : (lastActivityTree ? [lastActivityTree] : []);
    const parts = [];
    // The bug report goes FIRST, and only when something actually failed. A user who copies
    // Activity because a turn broke should not have to find the failure inside a work log -- the
    // turn, route, lanes, cause and build are the top of the paste, with the full log under it.
    const failureReport = turnFailureReport(view.chatLedger || [], PAGE_BUILD_COMMIT);
    if (failureReport) parts.push(failureReport);
    trees.forEach((tree, index) => {
      const label = lastActivityTurnLabels[index] || '';
      // A turn that ran no tool still happened. Dropping it because its tree is empty would make a
      // copied chat look like it contained only the turns that touched something.
      const text = activityTreeText(tree) || 'No recorded tool activity for this turn.';
      parts.push(label ? (label + '\n' + text) : text);
    });
    if (parts.length) return parts.join('\n\n');
  }
  if (panelTab === 'Event log') {
    const log = eventLogText(eventLogSections());
    if (log) return log;
  }
  if (panelTab === 'Agents') {
    // From the row model, not innerText. Every agent's work sits inside a collapsed <details>, so
    // the innerText fallback below would hand over a list of headings and no work at all -- which
    // is exactly what "copy all agent work" must not do.
    const text = agentEvidenceText(agentRowsFrom((view.chatLedger || []), Date.now()), Date.now());
    if (text) return text;
  }
  return ((xpBodyEl && xpBodyEl.innerText) || '').trim();
}
function copyPanelEvidence(btn, label) {
  const text = panelEvidenceText();
  if (!text) { toast('Nothing to copy in ' + label + ' yet.'); return; }
  // The copy carries its scope, so pasted evidence can never be read as covering more than it does.
  const heading = 'VOOL ' + label + ' — ' + scopeWhere();
  copyText(heading + '\n' + '='.repeat(heading.length) + '\n' + text, btn);
}
function wireActivityPanelActions() {
  xpBodyEl.querySelectorAll('[data-activity-action]').forEach((button) => button.addEventListener('click', () => {
    const action = button.getAttribute('data-activity-action');
    if (action === 'expand') { setActivityTreeOpen(true); return; }
    if (action === 'collapse') { setActivityTreeOpen(false); return; }
    if (action === 'copy') { copyPanelEvidence(button, 'Activity'); return; }
  }));
  xpBodyEl.querySelectorAll('[data-history-session]').forEach((button) => button.addEventListener('click', () => {
    const sid = button.getAttribute('data-history-session'); if (sid && sid !== displayedChat) openSession(sid);
  }));
  // Activity failure -> Report: switch to that chat (openSession points displayedChat at it
  // synchronously) and open the sanitized report flow for its failed turns.
  xpBodyEl.querySelectorAll('[data-history-report]').forEach((button) => button.addEventListener('click', async (ev) => {
    ev.stopPropagation();
    const sid = button.getAttribute('data-history-report');
    if (sid && sid !== displayedChat) { try { await openSession(sid); } catch (e) { /* the report flow still opens for the current chat */ } }
    openBugReport();
  }));
  xpBodyEl.querySelectorAll('[data-recovery]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.getAttribute('data-recovery');
    if (action === 'review') { toast('Showing the saved task timeline and receipts below.'); return; }
    if (action === 'resume') { const sid = displayedChat; if (!isChatBusy(sid)) { view.recoveredTask = null; renderPanel(); await runTurn('Continue the interrupted task from its saved checkpoint.', null, { chatId: sid }); } return; }
    if (action === 'cancel') {
      button.disabled = true;
      try {
        const r = await fetch('/api/task/recovery', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: displayedChat, checkpoint_id: button.getAttribute('data-checkpoint') || '', action: 'cancel' }) });
        const d = await r.json(); if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        view.recoveredTask = null; toast('Interrupted task cancelled.'); await loadActivityHistory();
      } catch (e) { button.disabled = false; toast('Could not cancel recovered task: ' + e.message); }
    }
  }));
}
async function loadRecoveredLedger() {
  const chatId = displayedChat;           // owner bound before the first await, as above
  const target = chatState(chatId);
  target.recoveredActivityOpen = {};
  try {
    let cursor = 0, pages = 0, truncated = false;
    while (pages < 8) {
      pages++;
      const res = await fetch('/api/runtime/events?session=' + encodeURIComponent(chatId) + '&after=' + cursor + '&limit=200');
      if (!res.ok) break;
      const d = await res.json();
      const more = d.events || [];
      for (const event of more) appendToChatLedger(target, event);
      if (typeof d.next_after === 'number') cursor = d.next_after;
      if (more.length < 200) break;
      // Eight pages of 200 is the ceiling. A chat past it is not fully loaded, and saying so is the
      // difference between a bounded view and a quietly wrong one.
      if (pages === 8) truncated = true;
    }
    // The whole chat, kept. The panel used to keep only the last turn's slice and drop the rest on
    // the floor: measured against a real 307-event chat, 13 events rendered and 294 -- 95% of the
    // session -- were discarded with nothing on screen to say so.
    target.chatLedgerTruncated = truncated;
    target.chatLedgerCursor = Math.max(target.chatLedgerCursor || 0, cursor);
    const events = target.chatLedger;
    let lastTurn = '';
    for (let i = events.length - 1; i >= 0; i--) { if (events[i].client_turn_id) { lastTurn = events[i].client_turn_id; break; } }
    target.recoveredLedger = lastTurn ? events.filter((e) => e.client_turn_id === lastTurn) : events.slice(-40);
  } catch (e) {
    // Hydration is a merge into immutable runtime-event identity, not a destructive replacement.
    // A transient refresh failure must not erase live events already rendered for this chat.
    const existing = target.chatLedger || [];
    let lastTurn = '';
    for (let i = existing.length - 1; i >= 0; i--) { if (existing[i].client_turn_id) { lastTurn = existing[i].client_turn_id; break; } }
    target.recoveredLedger = lastTurn ? existing.filter((event) => event.client_turn_id === lastTurn) : existing.slice(-40);
  }
}
function renderPanel() {
  if (!document.body.classList.contains('panel-open')) return;
  // A run grows tabs as it works (files appear, tests run) -- rebuild the tab bar when the
  // visible set actually changes, so new tabs show live without rebuilding on every event.
  const sig = visibleTabs().join(',');
  if (sig !== lastTabSig) { lastTabSig = sig; buildTabs(); }
  return renderPanelBody();
}
let lastActivityBodySig = '';
function renderPanelBody() {
  updateTabCounts();
  if (!document.body.classList.contains('panel-open')) return;
  // The Activity tab is the panel's INTERACTIVE surface: expandable rows and per-row actions
  // (Review price limits and the recovery buttons live inside it). Its content changes only when
  // the ledger grows or the turn moves, but the 1.4s ledger poll rebuilt it unconditionally --
  // replacing the exact nodes a pointer click had resolved, which killed the click mid-press
  // (observed as an automation stall, and equally a human's click target vanishing). Skip the
  // rebuild when nothing it renders has changed; the open-state maps already restore expansion
  // across the rebuilds that DO happen.
  if (panelTab === 'Activity') {
    const sigRun = view.run;
    const sig = [panelScope,
      sigRun ? (sigRun.chatId + ':' + sigRun.start) : 'none',
      (view.chatLedger || []).length, (view.recoveredLedger || []).length,
      (view.activityHistory || []).length, (view.scopedEvidence && view.scopedEvidence.sessions ? view.scopedEvidence.sessions.length : 0),
      sigRun ? (sigRun.ledger || []).length : 0, sigRun ? sigRun.steps.length : 0,
      sigRun ? (sigRun.ended ? ('end:' + sigRun.status) : 'live') : '',
      sigRun ? String(sigRun.model || '') : '',
      sigRun && sigRun.review ? String((sigRun.review.label != null ? sigRun.review.label : '') + (sigRun.review.state || '')) : ''].join('|');
    if (sig === lastActivityBodySig && xpBodyEl.dataset.activityRendered === '1') return;
    lastActivityBodySig = sig;
  } else {
    xpBodyEl.dataset.activityRendered = '0';
  }
  // Dropped every render and re-set only by a tree that actually draws, so Copy all can never
  // serialize a tree the panel is no longer showing (another chat's, or a cleared one).
  lastActivityTree = null;
  // The one Copy control names the tab it will actually copy ("Copy Activity", "Copy Event log").
  const copyBtn = document.getElementById('xpCopy');
  if (copyBtn) {
    const copyLabel = 'Copy ' + (panelTab || 'Activity') + ' (' + scopeWhere() + ')';
    copyBtn.title = copyLabel; copyBtn.setAttribute('aria-label', copyLabel);
  }
  const run = view.run, body = xpBodyEl;
  scopeNoteHtml();
  if (panelScope !== 'chat') loadScopedEvidence();
  if (panelTab === 'Council') { renderCouncilTab(body); return; }
  if (panelTab === 'Receipts') { renderReceipts(body); return; }
  if (panelTab === 'Event log') { renderEventLog(body); return; }
  // Handled before the `!run` branch: agents outlive the run that started them, and the
  // operator asking "what did it do" is usually asking after it finished.
  if (panelTab === 'Agents') { renderAgents(body); return; }
  body.innerHTML = '';
  if (!run) {
    if (panelTab === 'Activity') {
      let restored = renderRecoveryHtml();
      // The whole chat, newest turn first -- which is what the "Current chat" scope has always
      // claimed. It used to be the last turn only.
      restored += renderChatActivity(view, { live: false });
      restored += renderActivityHistoryHtml();
      body.innerHTML = restored || '<div class="xp-empty">' + esc(emptyScopeText('activity')) + '</div>';
      xpBodyEl.dataset.activityRendered = '1';
      wireActivityPanelActions(); return;
    }
    body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('activity')) + '</div>'; return;
  }
  let html = '';
  if (panelTab === 'Steps') {
    // Retrospective tool-step history: completed/current actions only, never a fabricated planner.
    if (!run.steps.length) {
      body.innerHTML = '<div class="xp-empty">' + (run.ended ? 'No tool steps -- answered directly.' : 'Planning…') + '</div>'; return;
    }
    run.steps.forEach((s, i) => {
      const current = s.status === 'running' ? ' current' : '';
      html += '<div class="xp-row ' + rowCls(s.status) + current + '"><span class="ic">' + rowIcon(s.status) + '</span><div class="bd">'
        + esc((i + 1) + '. ' + (s.summary || s.tool || 'Step ' + (i + 1))) + '<div class="st">' + esc((s.stage || '') + ' · ' + s.status) + '</div></div></div>';
    });
    if (!run.ended && run.steps.length && run.steps[run.steps.length - 1].status === 'completed') {
      html += '<div class="xp-note">Deciding the next step…</div>';
    }
  } else if (panelTab === 'Activity') {
    // The REAL runtime timeline from the ledger (scope, classification, model routing, tools,
    // timeouts, verification, terminal), scoped to this turn. Fall back to the streamed tool steps
    // until the ledger arrives, and say plainly when a turn ran no tool at all -- never a blank panel.
    const led = run.ledger || [];
    const chatLed = view.chatLedger || [];
    if (chatLed.length) {
      // Every turn in this chat, the live one first and open. `run.ledger` stays this turn's slice
      // and still decides the review row and the "no tool ran" line below.
      html = renderChatActivity(view, { live: !run.ended });
      const ledgerHasReview = led.some((e) => String(e.event_type || '').indexOf('verifier') !== -1);
      if (!ledgerHasReview) html += reviewPanelRow(run);
      if (run.ended && ledgerRanNoTool(led, runExecutionTruth(run))) html += panelRow('run', '•', 'No tool ran — answered directly', 'a plain answer needs no tools');
    } else if (run.steps.length) {
      run.steps.forEach((s) => { html += panelRow(rowCls(s.status), rowIcon(s.status), s.summary || s.tool || 'Action', stepMeta(s)); });
      html += reviewPanelRow(run);
    } else {
      // A turn can have meaningful routing, approval, failure, or model-review state without ever
      // executing a tool. Render those recorded dimensions instead of claiming there is no trace.
      if (run.model) html += panelRow('run', '◆', 'Model recorded', modelProviderPresentation(run.model));
      if (run.permission || run.status === 'awaiting_approval') html += panelRow('wait', '⏸', 'Waiting for approval', run.action || 'Review the exact action before it runs');
      html += reviewPanelRow(run);
      if (run.status === 'failed') html += panelRow('fail', '✕', 'Failed safely', run.endSummary || run.last || 'The task stopped without a successful terminal state');
      else if (run.status === 'cancelled') html += panelRow('fail', '■', 'Stopped', run.endSummary || 'Cancelled before completion');
      // Same authoritative check as the ledger branch above: this arm renders when the panel has no
      // steps of its own, which is exactly the case where a real execution went unrecognised.
      else if (run.ended && run.status === 'completed') html += panelRow('ok', '✓', 'Completed', ledgerRanNoTool(led, runExecutionTruth(run)) ? 'No tool ran — answered directly' : 'Completed with recorded tool work');
      if (!html) html = '<div class="xp-empty">' + esc(humanActivityState(run)) + '…</div>';
    }
    html = renderRecoveryHtml() + html + renderActivityHistoryHtml();
  } else if (panelTab === 'Changes' || panelTab === 'Files') {
    const paths = Object.keys(run.files);
    if (!paths.length) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('file changes')) + '</div>'; return; }
    paths.forEach((p) => { html += '<div class="xp-row ok"><span class="ic">✎</span><div class="bd mono">' + esc(p) + '</div></div>'; });
  } else if (panelTab === 'Tests') {
    const t = run.tests;
    if (!t.total && !t.done) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('test runs')) + '</div>'; return; }
    html = '<div class="xp-note">' + esc(t.done + (t.total ? (' of ' + t.total) : '') + ' tests complete' + (t.failed ? (' · ' + t.failed + ' failed') : '')) + '</div>';
  } else if (panelTab === 'Preview') {
    if (run.preview) { html = '<div class="xp-note">Preview: <span class="mono">' + esc(run.preview) + '</span></div>'; }
    else { body.innerHTML = '<div class="xp-empty">No preview for this task.</div>'; return; }
  }
  body.innerHTML = html;
  if (panelTab === 'Activity') { xpBodyEl.dataset.activityRendered = '1'; wireActivityPanelActions(); }
}
// The Event log follows the scope: the live turn's stream for this chat, this chat's durable
// ledger when no turn is running, or the merged per-chat ledgers for a wider scope.
// ONE source for both the rendered rows and the copied text. Built as data so "Copy all" hands over
// exactly the rows the panel is showing -- it can neither miss a row nor invent one, and the
// per-chat cap is applied here once instead of in the renderer alone.
function eventLogSections() {
  const run = view.run;
  if (panelScope === 'chat') {
    // THE WHOLE CHAT, grouped by turn -- not just the newest one.
    //
    // This read `run.events` when a run was live and `recoveredLedger` otherwise, and both are ONE
    // TURN: the first is the task stream of the turn in flight, the second is literally labelled
    // "Last recorded turn in this chat". So a five-turn conversation scoped to "Current chat"
    // showed the events of exactly one turn, and Copy all copied that one turn -- while the
    // Activity tab beside it, reading `chatLedger`, showed the whole chat. Same scope, two answers.
    //
    // `chatLedger` is the chat's real durable history and was already being maintained; the Event
    // log simply never read it.
    const sections = [];
    const ledger = view.chatLedger || [];
    if (ledger.length) {
      const byTurn = new Map();
      for (const ev of ledger) {
        const key = String(ev.client_turn_id || '') || '_untagged';
        if (!byTurn.has(key)) byTurn.set(key, []);
        byTurn.get(key).push(ev);
      }
      let index = 0;
      for (const [, events] of byTurn) {
        index += 1;
        sections.push({
          kind: 'group',
          title: byTurn.size > 1 ? ('Turn ' + index) : 'This chat',
          total: events.length,
          rows: events.slice().reverse().map((ev) => '[' + (ev.event_type || 'event') + '] ' + (ev.message || '')),
        });
      }
    }
    // The turn in flight is not in the durable ledger yet, so its task-stream rows are appended
    // rather than replacing the history -- which is what the old branch did.
    if (run && run.events.length) {
      sections.push({
        kind: sections.length ? 'group' : '',
        title: sections.length ? 'Current turn' : '',
        total: run.events.length,
        rows: run.events.slice().reverse().map((ev) => '[' + (ev.stage || ev.type) + '] ' + evTitle(ev)),
      });
    }
    if (sections.length) return sections.reverse();
    const durable = view.recoveredLedger || [];
    if (!durable.length) return [];
    return [{ kind: 'note', title: 'Last recorded turn in this chat', rows: durable.slice().reverse().map((ev) => '[' + (ev.event_type || 'event') + '] ' + (ev.message || '')) }];
  }
  return scopedEvidence.sessions.map((s) => ({
    kind: 'group', title: s.title, total: s.events.length,
    rows: s.events.slice(-40).reverse().map((ev) => '[' + (ev.event_type || 'event') + '] ' + (ev.message || '')),
  }));
}
function eventLogText(sections) {
  const lines = [];
  for (const section of sections) {
    if (section.title) {
      lines.push(section.title + (section.kind === 'group' ? (' \u2014 ' + section.total + ' event' + (section.total === 1 ? '' : 's')) : ''));
    }
    for (const row of section.rows) lines.push(row);
    lines.push('');
  }
  return lines.join('\n').trim();
}
function renderAgents(body) {
  const rows = agentRowsFrom((view.chatLedger || []), Date.now());
  if (!rows.length) { body.innerHTML = '<div class="xp-empty">No agent activity in ' + esc(scopeWhere()) + '.</div>'; return; }
  const sum = agentSummaryOf(rows);
  let html = '<div class="xp-tree-actions"><button type="button" class="xp-mini" data-agents-action="copy">Copy all</button></div>';
  html += '<div class="xp-group-head"><span class="xp-group-name">Agents</span>'
       + '<span class="xp-group-meta">' + sum.running + ' running · ' + sum.done + ' done'
       + (sum.failed ? ' · ' + sum.failed + ' failed' : '') + '</span></div>';
  for (const r of rows) {
    const when = r.running
      ? ('running' + (r.elapsedMs != null ? ' · ' + fmtAgentDuration(r.elapsedMs) : ''))
      : (esc(r.state) + (r.elapsedMs != null ? ' · ' + fmtAgentDuration(r.elapsedMs) : ''));
    html += '<div class="xp-row"><div class="bd">'
         + '<b>' + esc(r.nodeId) + '</b> <span class="mono">' + esc(r.operation || 'node') + '</span>'
         + ' — ' + when;
    if (r.dependsOn && r.dependsOn.length) html += '<div class="mono">after: ' + esc(r.dependsOn.join(', ')) + '</div>';
    if (r.failureReason) html += '<div class="mono">' + esc(r.failureReason) + '</div>';
    if (r.rendered) {
      html += '<details><summary>what it did</summary><div class="mono">' + esc(r.rendered)
           + (r.renderedTruncated ? '\n[truncated]' : '') + '</div></details>';
    }
    html += '</div></div>';
  }
  body.innerHTML = html;
  const copy = body.querySelector('[data-agents-action="copy"]');
  // Serialized from the row model rather than from innerText, so a collapsed <details> still
  // copies its contents -- the same reason the Activity tree copies from its tree model.
  if (copy) copy.addEventListener('click', () => copyText(agentEvidenceText(rows, Date.now())));
}
function renderEventLog(body) {
  if (panelScope !== 'chat') {
    if (scopedEvidence.loading) { body.innerHTML = '<div class="xp-empty">Reading activity for ' + esc(scopeWhere()) + '\u2026</div>'; return; }
    if (!scopedEvidence.sessions.length) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('events')) + '</div>'; return; }
  }
  const sections = eventLogSections();
  if (!sections.length) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('events')) + '</div>'; return; }
  // The event log is a flat list -- there is nothing to disclose -- so it gets the control that
  // actually applies to it. Copy all still reads from the data, not from what happens to be scrolled.
  let html = '<div class="xp-tree-actions"><button type="button" class="xp-mini" data-eventlog-action="copy">Copy all</button></div>';
  for (const section of sections) {
    if (section.kind === 'note') html += '<div class="xp-note">' + esc(section.title) + '</div>';
    else if (section.kind === 'group') {
      html += '<div class="xp-group-head"><span class="xp-group-name">' + esc(section.title) + '</span>'
        + '<span class="xp-group-meta">' + section.total + ' event' + (section.total === 1 ? '' : 's') + '</span></div>';
    }
    for (const row of section.rows) html += '<div class="xp-row"><div class="bd mono">' + esc(row) + '</div></div>';
  }
  body.innerHTML = html;
  wireEventLogActions();
}
function wireEventLogActions() {
  xpBodyEl.querySelectorAll('[data-eventlog-action]').forEach((button) => button.addEventListener('click', () => {
    copyPanelEvidence(button, 'Event log');
  }));
}

async function renderReceipts(body) {
  body.innerHTML = '<div class="xp-empty">Loading receipts…</div>';
  // A receipt belongs to the chat that produced it, so a wider scope reads each chat in scope and
  // keeps them grouped -- the chain is only meaningful within one chat, never across a merged list.
  if (panelScope !== 'chat') { renderScopedReceipts(body); return; }
  try {
    const r = await fetch('/api/runtime/receipts?session=' + encodeURIComponent(displayedChat));
    const d = await r.json();
    const list = d.receipts || [];
    if (!list.length) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('signed receipts')) + '</div>'; return; }
    let html = '<div class="xp-note">' + (d.chain_verified === true
      ? 'Receipts consistent — every receipt shown is individually valid and correctly linked to the one before it. This does not prove the history is complete: receipts deleted from the end leave no trace. This does not prove the answer is correct.'
      : (d.chain_verified === false
        ? 'Receipt chain broken — a receipt shown here was altered, forged, or removed from the middle of the chain.'
        : 'Receipt chain not checked — receipts do not establish answer correctness.')) + '</div>';
    // Each receipt opens to its full recorded evidence. Only a 16-character hash prefix used to be
    // shown and nothing was clickable, so the one thing a receipt exists to let you do -- quote it
    // and check it -- could not be done from the panel at all.
    list.slice().reverse().forEach((rc) => { html += receiptRowHtml(rc); });
    body.innerHTML = html;
    wireReceiptActions(body, list);
  } catch (e) { body.innerHTML = '<div class="xp-empty">Receipts unavailable.</div>'; }
}
// The store keeps issued_at as an epoch float. Shown raw it is not a time anyone can read, so it
// is rendered as a local timestamp; the untouched receipt is still what "Copy receipt" hands over.
async function renderScopedReceipts(body) {
  const key = scopeKey();
  const ids = scopeCandidateSessionIds();
  const capped = ids.slice(0, SCOPE_SESSION_CAP);
  const groups = [];
  for (const sid of capped) {
    try {
      const r = await fetch('/api/runtime/receipts?session=' + encodeURIComponent(sid));
      if (!r.ok) continue;
      const d = await r.json();
      const list = d.receipts || [];
      if (list.length) groups.push({ session_id: sid, title: chatTitleFor(sid), receipts: list, chain_verified: d.chain_verified });
    } catch (e) { /* one unreadable chat must not empty the panel */ }
  }
  if (scopeKey() !== key || panelTab !== 'Receipts') return;   // the user moved on mid-load
  if (!groups.length) { body.innerHTML = '<div class="xp-empty">' + esc(emptyScopeText('signed receipts')) + '</div>'; return; }
  const dropped = Math.max(0, ids.length - capped.length);
  let html = '<div class="xp-note">Receipts from ' + groups.length + ' chat' + (groups.length === 1 ? '' : 's')
    + ' in ' + esc(scopeWhere()) + '. Each chain is verified within its own chat.'
    + (dropped ? (' ' + dropped + ' older chat' + (dropped === 1 ? '' : 's') + ' not read.') : '') + '</div>';
  const flat = [];
  groups.forEach((g) => {
    html += '<div class="xp-group-head"><span class="xp-group-name">' + esc(g.title) + '</span>'
      + '<span class="xp-group-meta">' + (g.chain_verified === true ? 'receipts consistent' : (g.chain_verified === false ? 'chain broken' : 'chain unchecked')) + '</span></div>';
    g.receipts.slice().reverse().forEach((rc) => { html += receiptRowHtml(rc); flat.push(rc); });
  });
  body.innerHTML = html;
  wireReceiptActions(body, flat);
}
function receiptIssuedText(issuedAt) {
  const seconds = Number(issuedAt);
  if (!isFinite(seconds) || seconds <= 0) return issuedAt || '';
  try { return new Date(seconds * 1000).toLocaleString(appLocale()); } catch (e) { return String(issuedAt); }
}
function receiptRowHtml(rc) {
  const cls = rc.verdict === 'clean' ? 'ok' : (rc.verdict === 'blocked_false_claim' ? 'fail' : 'run');
  const rid = String(rc.receipt_id || '');
  return '<details class="rc-item ' + cls + '" data-receipt="' + esc(rid) + '">'
    + '<summary class="rc-sum"><span class="ic">' + (rc.signed ? '🔏' : '•') + '</span>'
    + '<span class="rc-hd">Turn ' + esc(rc.turn_index) + ' · ' + esc(rc.verdict || 'unrecorded verdict')
    + '<span class="rc-sub mono">' + esc(String(rc.content_hash || 'no content hash').slice(0, 16)) + '</span></span></summary>'
    + '<div class="rc-body">'
    + receiptField('Receipt id', rid)
    + receiptField('Issued', receiptIssuedText(rc.issued_at))
    + receiptField('Verdict', rc.verdict)
    + receiptField('Verdict detail', rc.verdict_detail)
    + receiptField('Content hash', rc.content_hash, true)
    + receiptField('Signed', rc.signed ? 'yes' : 'no')
    + receiptField('Signer', rc.signer_peer_id, true)
    + receiptField('Executed tools', (rc.executed_tools || []).join(', '))
    + receiptField('Claimed actions', (rc.claimed_actions || []).join(', '))
    + '<div class="rc-actions">'
    + '<button type="button" class="rc-btn" data-copy="hash">Copy hash</button>'
    + '<button type="button" class="rc-btn" data-copy="receipt">Copy receipt</button>'
    + '</div></div></details>';
}
function receiptField(label, value, mono) {
  const text = (value === null || value === undefined || value === '') ? '—' : String(value);
  return '<div class="rc-f"><span class="rc-k">' + esc(label) + '</span>'
    + '<span class="rc-v' + (mono ? ' mono' : '') + '">' + esc(text) + '</span></div>';
}
function wireReceiptActions(body, list) {
  const byId = {};
  list.forEach((rc) => { byId[String(rc.receipt_id || '')] = rc; });
  body.querySelectorAll('.rc-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      const host = btn.closest('.rc-item'); if (!host) return;
      const rc = byId[host.getAttribute('data-receipt') || ''];
      if (!rc) return;
      // "Copy receipt" hands over the whole recorded object, so what is pasted is exactly what the
      // runtime stored -- not this panel's rendering of it.
      const text = btn.getAttribute('data-copy') === 'hash'
        ? String(rc.content_hash || '')
        : JSON.stringify(rc, null, 2);
      if (!text) { toast('That receipt carries no hash to copy.'); return; }
      copyText(text, btn);
    });
  });
}


// --- Message queue (composer stays usable; a send while busy is queued, never dropped) ---
async function queueOp(op, chatId, extra) {
  try {
    const res = await fetch('/api/chat/queue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ op: op, session_id: String(chatId || displayedChat) }, extra || {})),
    });
    return await res.json();
  } catch (e) { return null; }
}

// The queue chips belong to the chat on screen. Bound before the await so a switch mid-request
// cannot paint one chat's queue under another's composer.
async function refreshQueue() {
  const chatId = displayedChat;
  try {
    const res = await fetch('/api/chat/queue?session=' + encodeURIComponent(chatId));
    const data = await res.json();
    if (isDisplayed(chatId)) renderQueue((data && data.queue) || []);
  } catch (e) { /* leave chips as-is on a transient error */ }
}

// Presentation-only density cap: past this many chips the strip collapses the rest behind a
// "+N queued" toggle instead of growing the footer without bound. Expand state is per chat (a
// chat switch never leaks one chat's expanded queue onto another's) and is pure client-side UI
// state -- it never reorders, drops, claims, or cancels anything; renderQueue still walks every
// item in `items`, in the order the server sent them, and only skips appendChild for the rest.
const QUEUE_VISIBLE_LIMIT = 2;
const queueExpandedByChat = new Map();
const lastQueueItemsByChat = new Map();

function renderQueue(items) {
  lastQueueItemsByChat.set(displayedChat, items || []);
  const pending = (items || []).filter(function (i) { return i.status === 'pending' || i.status === 'in_flight'; });
  queueEl.innerHTML = '';
  if (!pending.length) { queueEl.style.display = 'none'; queueExpandedByChat.delete(displayedChat); return; }
  queueEl.style.display = 'flex';
  const expanded = !!queueExpandedByChat.get(displayedChat);
  const visibleCount = expanded ? pending.length : Math.min(pending.length, QUEUE_VISIBLE_LIMIT);
  let pos = 0;
  pending.forEach(function (item, idx) {
    const running = item.status === 'in_flight';
    if (!running) pos += 1;
    // Numbering and cancel targets are computed for every item, visible or not, so an expanded
    // chip 3 still reads "Queued 3" and cancels the same queue_item_id it always would have.
    if (idx >= visibleCount) return;
    const chip = document.createElement('div');
    chip.className = 'qchip' + (running ? ' running' : '');
    const preview = (item.payload && item.payload.text) ? item.payload.text : '';
    const label = document.createElement('span');
    label.className = 'qchip-text';
    const queuedAttachmentCount = ((item.payload && item.payload.attachments) || []).length;
    label.textContent = (running ? 'Running: ' : (item.payload && item.payload.price_wait ? 'Waiting for price · ' + item.payload.price_wait.model_id + ': ' : ('Queued ' + pos + ': '))) + preview + (queuedAttachmentCount ? ' \u{1F4CE}' + queuedAttachmentCount : '');
    label.title = preview;
    chip.appendChild(label);
    if (!running) {
      const x = document.createElement('button');
      x.type = 'button'; x.className = 'qchip-x'; x.textContent = '×'; x.title = 'Cancel this queued message';
      x.addEventListener('click', async function () { await queueOp('cancel', displayedChat, { queue_item_id: item.queue_item_id }); refreshQueue(); });
      chip.appendChild(x);
    }
    queueEl.appendChild(chip);
  });
  const hiddenCount = pending.length - visibleCount;
  if (hiddenCount > 0) {
    const more = document.createElement('button');
    more.type = 'button'; more.className = 'qchip qchip-more'; more.id = 'queueMore';
    more.textContent = '+' + hiddenCount + ' queued';
    more.title = 'Show all queued messages'; more.setAttribute('aria-expanded', 'false');
    more.addEventListener('click', function () {
      queueExpandedByChat.set(displayedChat, true);
      renderQueue(lastQueueItemsByChat.get(displayedChat) || []);
    });
    queueEl.appendChild(more);
  } else if (expanded && pending.length > QUEUE_VISIBLE_LIMIT) {
    const less = document.createElement('button');
    less.type = 'button'; less.className = 'qchip qchip-more'; less.id = 'queueMore';
    less.textContent = 'Show less';
    less.title = 'Collapse the queue'; less.setAttribute('aria-expanded', 'true');
    less.addEventListener('click', function () {
      queueExpandedByChat.set(displayedChat, false);
      renderQueue(lastQueueItemsByChat.get(displayedChat) || []);
    });
    queueEl.appendChild(less);
  }
}

let priceQueuePumpActive = false;
async function pumpPriceWaitQueues() {
  if (priceQueuePumpActive) return;
  priceQueuePumpActive = true;
  try {
    const state = await postJsonWithin('/api/cloud/usepod/price-wait/pending', {}, 5000);
    for (const chatId of ((state && state.sessions) || [])) { if (!isChatBusy(chatId)) await pumpQueue(chatId); }
  } catch (e) { /* durable pending work remains queued; try on the next bounded tick */ }
  finally { priceQueuePumpActive = false; }
}
setInterval(pumpPriceWaitQueues, 60000);
// Pump the next queued message, but only once the current turn's stream truly closed. The
// atomic server-side claim (compare-and-swap) guarantees exactly-once even if this races.
async function pumpQueue(forChatId) {
  const chatId = String(forChatId || displayedChat);   // the queue is per chat; bind before any await
  const owner = chatState(chatId);
  if (isChatBusy(chatId)) return;
  owner.pumpHold = true;  // hold THIS chat's slot across the await so a concurrent send() queues
  const res = await queueOp('claim', chatId, {});
  const claimed = res && res.item ? res.item : null;
  if (!claimed) { owner.pumpHold = false; refreshQueue(); reflectComposer(); return; }
  refreshQueue();
  // The attachments the queued item owns, by the ids the server stored; names come from this
  // chat's own bucket (recorded when the item was queued) or fall back to a plain label.
  const queuedIds = (claimed.payload && Array.isArray(claimed.payload.attachments)) ? claimed.payload.attachments : [];
  const queuedMeta = queuedIds.map((id) => owner.queuedAttachmentMeta[id] || { id: id, name: 'attachment', kind: '', size_bytes: 0, previewUrl: '' });
  queuedIds.forEach((id) => { delete owner.queuedAttachmentMeta[id]; });
  await runTurn((claimed.payload && claimed.payload.text) || '', claimed.queue_item_id, { chatId: chatId, attachments: queuedMeta, queuedModel: claimed.payload.price_wait ? 'usepod:' + claimed.payload.price_wait.model_id : '', priceWait: !!claimed.payload.price_wait });
}

async function fetchJsonWithin(url, parentSignal, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (parentSignal) {
    if (parentSignal.aborted) cancel();
    else parentSignal.addEventListener('abort', cancel, { once: true });
  }
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error('Could not read runtime state (HTTP ' + response.status + ').');
    return await response.json();
  } catch (error) {
    if (timedOut) throw new Error('Runtime state did not respond within ' + (timeoutMs / 1000) + ' seconds. Please retry.');
    throw error;
  } finally {
    clearTimeout(timer);
    if (parentSignal) parentSignal.removeEventListener('abort', cancel);
  }
}

async function runTurn(text, queueItemId, opts) {
  opts = opts || {};
  // The chat this turn belongs to is decided ONCE, here, from the chat that is starting it -- and
  // then carried on the run. Nothing after this line re-reads the display to decide ownership.
  const chatId = opts.chatId || displayedChat;
  const owner = chatState(chatId);
  if (isDisplayed(chatId)) {
    hidePermBar();                                        // a new turn clears any stale permission prompt
    try { setCloudWorking(isCloudModel()); } catch (e) {}  // in-use pulse on the connection dot
  }
  // The attachments this turn carries are decided HERE, once, from the caller -- the send that
  // spent the draft, the queue item that owned them, or the resend of a failed turn -- and then
  // ride the run. Nothing after this line re-reads the composer's strip.
  const turnAttachments = Array.isArray(opts.attachments) ? opts.attachments.map((a) => Object.assign({}, a)) : [];
  if (!opts.resend) {
    const sentAt = new Date().toISOString();
    if (isDisplayed(chatId)) addMsg('user', text, sentAt, null, turnAttachments);
    recordUserMessage(chatId, text, sentAt, turnAttachments);
    ensureSessionInSidebar(chatId, text);
    // The draft is spent: its chips now live on the bubble, and the strip is clear for the next
    // message of THIS chat.
    if (turnAttachments.length) { owner.attachments = []; if (isDisplayed(chatId)) renderAttachStrip(); }
  }
  const run = adoptRun(chatId, newRun(chatId));
  // The companion learns that a NEW turn opened on this lane from the page's own fact -- the
  // request left the composer -- not from the server's later `task.started`. Until that
  // acknowledgement arrived, the last card wore the previous turn's terminal phrase under a
  // `Working` header (measured 2026-09-06). `task.submitted` claims nothing about the server.
  if (typeof window !== 'undefined' && window.VoolCompanion) {
    try { window.VoolCompanion.consume(chatId, { type: 'task.submitted', ts: run.start }); } catch (e) {}
  }
  run.attachments = turnAttachments;
  reflectComposer();   // the Send button follows the DISPLAYED chat, which may not be this one
  if (opts.turnId) run.turnId = opts.turnId;
  run.queueItemId = queueItemId || null;
  run.aborter = new AbortController();
  // Timers belong to the run for its whole life -- navigation never starts or stops them.
  startRunTimers(run);
  // The answer bubble belongs to this chat's log, so it is only created when this chat is on
  // screen. `run.text` is the state copy and always accrues; every paint below reads the DOM
  // THROUGH the run, so re-opening this chat mid-stream simply swaps in fresh nodes.
  if (isDisplayed(chatId)) {
    attachRunDom(run, addMsg('assistant', '…'));
    buildTabs(); renderPanel();
  }
  let assistant = '';
  let hasResponseCommit = false;
  run.text = '';
  try {
    const lastUser = owner.history.slice().reverse().find((m) => m && m.role === 'user') || {};
    const selectedModel = opts.queuedModel || effectiveModel(chatId, lastUser.content || '');
    // A try-once grant serving THIS turn reports "sticky" (a concrete id that degrades to
    // auto if unresolvable), never "pin" — it is not the operator's pin and must not
    // inherit the pin's hard-refusal semantics.
    const trialServing = activeTryOnce(chatId, lastUser.content || '');
    const modelSelection = opts.queuedModel ? 'pin' : trialServing ? 'sticky' : (modelForChat(chatId) !== 'vool') ? 'pin' : (selectedModel !== 'vool' ? 'sticky' : 'auto');
    // Restored pins need an explicit decision in this app session. A successful paid selection
    // already supplies it for the selected conversation, so the first send never asks twice.
    let sendRequiresPaidAck = false;
    try {
      const selJ = await fetchJsonWithin('/api/cloud/model?session_id=' + encodeURIComponent(chatId), run.aborter.signal, 5000);
      if (!selJ || !selJ.ok) throw new Error('Model selection is unavailable. Please retry.');
      if (selJ && selJ.ok) {
        const serverPin = String(selJ.model || '').trim();
        const selText = String(selectedModel);
        const cloudShaped = !MODEL_LABELS[selText] && !isLocalModel(selText)
          && (selText.indexOf('openrouter') === 0 || CLOUD_MODEL_ID_RE.test(selText));
        // Cost truth for THIS selection: the persisted pin's server-derived classification
        // when it is the selection; an unhydrated map can no longer widen this away.
        const selectionIsPinnedCloud = cloudShaped && !!serverPin && (serverPin === selText || (selJ.provider + ':' + serverPin) === selText);
        const serverCostState = selectionIsPinnedCloud ? String(selJ.cost_state || '') : '';
        const rowVerdict = CLOUD_CATALOG_BY_ID[selText];
        sendRequiresPaidAck = (selectionIsPinnedCloud
          && (serverCostState === 'paid' || serverCostState === 'unknown')
          && !(rowVerdict && rowVerdict.free))
          || (cloudShaped && !selectionIsPinnedCloud && !(rowVerdict && rowVerdict.free));
      } else {
        // Server could not report its state: fall back to the client map so a degraded GET
        // never silently widens the guard away.
        const degradedRow = CLOUD_CATALOG_BY_ID[String(selectedModel)];
        sendRequiresPaidAck = !!(degradedRow && !degradedRow.free && !isLocalModel(selectedModel));
      }
    } catch (e) {
      // An unreadable cost/identity decision is not permission to dispatch.
      // The outer handler shows the failure or cancellation and frees Send.
      throw e;
    }
    if (sendRequiresPaidAck && !opts.priceWait && !paidPinAcked(String(selectedModel), chatId)) {
      // The typed gate makes this decision informed (real rates, catalog freshness); the bare
      // confirm remains the fallback so an unmounted fragment can never brick sending.
      const ok = window.VoolPriceGate
        ? await window.VoolPriceGate.review({ kind: 'per-send', id: String(selectedModel), label: (CLOUD_MODEL_LABELS[selectedModel]) || selectedModel })
        : window.confirm('Use the PAID model "' + ((CLOUD_MODEL_LABELS[selectedModel]) || selectedModel) + '" for this turn? It may charge your account each turn. VOOL spend caps still apply.');
      if (!ok) {
        finishRun(run, 'cancelled', pageT('run.cancelled_paid', 'Cancelled \u2014 paid model not confirmed'));
        releaseComposer(run);
        return;
      }
      acknowledgePaidPin(chatId, String(selectedModel), ok);
    }
    if (sendRequiresPaidAck) consumePaidPinAck(chatId, String(selectedModel));
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: run.aborter.signal,
      // Every field of this body is read from the RUN's chat -- transcript, mode, autonomy,
      // approval grant, bypass grant -- never from the composer's current display.
      body: JSON.stringify(buildTurnRequestBody(run, selectedModel, modelSelection)),
    });
    // The trial's one turn is now genuinely sent: retire the grant so the NEXT turn
    // reverts to the chat's ordinary selection (pin/sticky/Auto) on its own.
    if (trialServing) consumeTryOnce(chatId);
    // The SERVER decides whether this machine may run an ordinary turn right now. A
    // composer that looked free is not proof it was: a council can take the global model
    // pin between a lock poll and a keystroke, and in another tab there may have been no
    // poll at all. The typed refusal re-closes the composer here rather than leaving the
    // turn waiting on a stream that is never coming.
    if (res.status === 409 && typeof res.clone === 'function') {
      let refusal = null;
      try { refusal = await res.clone().json(); } catch (e) { refusal = null; }
      if (refusal && refusal.error === 'council_model_pin_active') {
        setCouncilLock(true, String(refusal.detail || ''));
        if (window.VoolCouncilCard) { try { window.VoolCouncilCard.refreshLock(); } catch (e) {} }
        // The refusal IS this turn's answer. Leaving the bubble on its typing ellipsis
        // would show a turn still thinking about a request the server declined.
        run.text = String(refusal.detail || pageT('council.locked', 'Council in session \u2014 the model pin is held.'));
        if (run.assistantMsgEl) run.assistantMsgEl.classList.remove('pending');
        if (run.textEl) run.textEl.textContent = run.text;
        finishRun(run, 'cancelled', 'Council in session \u2014 the model pin is held');
        releaseComposer(run);
        return;
      }
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        let obj; try { obj = JSON.parse(line); } catch (e) { continue; }
        if (obj && obj.vool_event) { applyTaskEvent(run, obj.vool_event); continue; }
        if (obj && obj.vool_profile) { applyProfileFrame(run, obj.vool_profile); continue; }
        if (obj && obj.vool_terminal) {
          // A7 LAW 6 typed NO_ANSWER_TERMINAL: the turn ended with NO semantic answer.
          // This frame is machine truth from core.finalization — carrying its stable
          // reason_code to the bubble is render-consumption of an existing code, never
          // fabricated prose, and it must never resurface as "(no response)" + Completed.
          const t = obj.vool_terminal;
          const reasonCode = String(t.reason_code || 'unspecified');
          const detail = String(t.detail || '');
          run.terminal = { status: 'no_answer_terminal', reason_code: reasonCode };
          run.action = detail ? (reasonCode + ' — ' + detail) : reasonCode;
          continue;
        }
        if (obj && obj.vool_response_commit && applyResponseCommit(run, obj.vool_response_commit)) {
          // Deltas are intentionally speculative. The terminal server commit is the only content
          // allowed into the transcript, persistence-parity view, and next-turn model history.
          hasResponseCommit = true;
          assistant = run.text;
          const committedUsage = run.displayMetadata && run.displayMetadata.usage;
          const committedTokens = Number(committedUsage && committedUsage.output_tokens) || 0;
          run.tokens = committedTokens || Math.round(assistant.length / 4);
          withRunDom(run, () => {
            if (!run.textEl) return;
            run.assistantMsgEl.classList.remove('pending');
            renderAssistantContent(run.textEl, assistant, run.displayMetadata);
            if (run.refs && run.refs.tokens && !run.ended) run.refs.tokens.innerHTML = '<b>' + run.tokens + '</b> tok';
            logEl.scrollTop = logEl.scrollHeight;
          });
          continue;
        }
        const chunk = obj && obj.message && obj.message.content ? obj.message.content : '';
        if (chunk) {
          // State first, unconditionally: the answer accrues on the run whether or not anyone is
          // looking at this chat. Painting is the gated second step.
          assistant += chunk;
          run.text = assistant;
          // Live token estimate (~4 chars/token) as the answer streams -- the answer's own footer
          // carries the exact count when it finishes.
          run.tokens = Math.round(assistant.length / 4);
          withRunDom(run, () => {
            if (!run.textEl) return;   // this chat is on screen but the run has no bubble yet
            run.assistantMsgEl.classList.remove('pending');
            run.textEl.textContent = assistant;
            if (run.refs && run.refs.tokens && !run.ended) run.refs.tokens.innerHTML = '<b>' + run.tokens + '</b> tok';
            logEl.scrollTop = logEl.scrollHeight;
          });
        }
      }
    }
  } catch (e) {
    const aborted = e && (e.name === 'AbortError');
    withRunDom(run, () => { if (run.assistantMsgEl) run.assistantMsgEl.classList.remove('pending'); });
    if (aborted) {
      // Aborting the fetch ends the DISPLAY, not the turn: the server keeps running it, so the
      // tokens still land in the usage meter. The Stop button cancels the server turn first.
      if (!assistant) { run.text = pageT('run.stopped_showing', '(stopped showing this answer)'); withRunDom(run, () => { if (run.textEl) run.textEl.textContent = run.text; }); }
      finishRun(run, 'cancelled', pageT('run.cancelled', 'Stopped \u2014 cancelled the turn on your machine.'));
    }
    else {
      run.error = (e && e.message ? e.message : String(e));
      run.text = run.text || (pageT('run.error_prefix', 'Error: ') + run.error);
      withRunDom(run, () => { if (run.textEl) run.textEl.textContent = pageT('run.error_prefix', 'Error: ') + run.error; });
      finishRun(run, 'failed', pageT('run.connection_error', 'Connection error'));
    }
  }
  if (!hasResponseCommit && assistant) {
    // Compatibility with an older server: retain the response, but still migrate any appended
    // provenance footer out of canonical conversation content before it is recorded.
    const normalized = splitAssistantDisplayContent(assistant, run.displayMetadata);
    assistant = normalized.content; run.text = assistant; run.displayMetadata = normalized.display_metadata;
  }
  if (!assistant && !run.ended) {
    // Two distinct honest terminal states, never conflated:
    // - a typed server terminal (run.terminal) renders THAT code/detail and ends FAILED;
    // - a stream that closed with neither content nor a terminal frame is "(no response)".
    const t = run.terminal || null;
    run.text = t ? (pageT('run.turn_ended_prefix', 'Turn ended without an answer \u2014 ') + (t.reason_code || 'no_answer_terminal'))
                 : pageT('run.no_response', '(no response)');
    withRunDom(run, () => { if (!run.textEl) return; run.assistantMsgEl.classList.remove('pending'); run.textEl.textContent = run.text; });
    if (t) finishRun(run, 'failed', pageT('run.failed_safely', 'Failed safely \u2014 no answer was produced'));
  }
  else if (assistant || hasResponseCommit) {
    // The answer is appended to the chat that ASKED for it, resolved off the run -- never off the
    // chat currently on screen. This is the line that would silently move A's answer into B.
    recordAssistantMessage(run, assistant, run.displayMetadata);
    withRunDom(run, () => { if (run.textEl) renderAssistantContent(run.textEl, assistant, run.displayMetadata); });
  }
  if (!run.ended) finishRun(run, run.permissionDenied ? 'failed' : (run.permission ? 'awaiting_approval' : (run.terminal ? 'failed' : 'completed')), run.permissionDenied ? pageT('run.permission_denied', 'Permission denied') : (run.permission ? pageT('run.review_action', 'Review the exact action below.') : (run.terminal ? (run.action || pageT('run.failed_safely_label', 'Failed safely')) : pageT('run.complete', 'Complete'))));
  // The turn ran to the end without asking again, so the server either matched the token or never
  // reached a gated action. Either way it is done; a turn that DID ask again already replaced it.
  if (!run.permission) releaseApprovalTokenFor(run.chatId);
  if (run.queueItemId) {
    queueOp('complete', run.chatId, { queue_item_id: run.queueItemId, status: run.status === 'cancelled' ? 'cancelled' : (run.status === 'failed' ? 'failed' : 'completed') });
  }
  releaseComposer(run);  // frees THIS chat's slot + pumps THIS chat's queue
  loadSessions();
  if (isDisplayed(chatId)) inputEl.focus();
}

let spendingPreflightPending = false;
async function postJsonWithin(url, body, timeoutMs) {
  // The POST twin of fetchJsonWithin: a bounded owner-local read that answers with its body even
  // on a typed refusal (a 409 with a state is an answer, not an outage).
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}), signal: controller.signal });
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (!response.ok && !(data && typeof data === 'object' && data.state)) throw new Error('Could not read runtime state (HTTP ' + response.status + ').');
    return data;
  } catch (error) {
    if (timedOut) throw new Error('Runtime state did not respond within ' + (timeoutMs / 1000) + ' seconds. Please retry.');
    throw error;
  } finally { clearTimeout(timer); }
}
// Why a verified balance cannot let this draft go, in the user's terms. '' when the balance is a
// reported, non-empty amount. The money law refuses a debit against an unverified balance, so the
// check happens BEFORE the draft is consumed; the states are the balance door's own
// (core.usepod.discovery.observe_balance), never a guess at funds: missing is not zero.
function usepodBalanceBlocker(balance) {
  // The balance door's own state names drive the lookup; amounts and codes render as-is.
  if (!balance || typeof balance !== 'object') return pageT('usepod.balance_unchecked', 'Your UsePod balance could not be checked.');
  const state = String(balance.state || '');
  if (state === 'reported') {
    const amount = Number(balance.usdc_balance_microunits);
    return Number.isFinite(amount) && amount > 0 ? '' : pageT('usepod.balance_zero', 'Your UsePod balance is 0 USDC. Top up the account, then send again.');
  }
  if (state === 'no_token') return pageT('usepod.no_account', 'Your UsePod account is not connected.');
  if (state === 'credential_pair_unresolved') return pageT('usepod.token_saving', 'Your UsePod token is still being saved or removed; try again in a moment.');
  if (state === 'unavailable') {
    const where = balance.http_status ? ' (HTTP ' + balance.http_status + ')' : (balance.error_code ? ' (' + balance.error_code + ')' : '');
    return pageT('usepod.unavailable', 'UsePod did not confirm your balance') + where + '.';
  }
  if (state === 'not_observed') return pageT('usepod.not_observed', 'Your UsePod balance has not been read yet.');
  return pageT('usepod.unreadable_form', 'UsePod answered the balance check in a form this build cannot read (') + state + ').';
}
async function checkUsePodSpending(selected, chatId = displayedChat) {
  if (!String(selected).startsWith('usepod:')) return true;
  const model = String(selected).slice(7);
  let readiness;
  try {
    const data = await fetchJsonWithin('/api/cloud/usepod/discovery?q=' + encodeURIComponent(model), undefined, 5000);
    readiness = data && data.spend_readiness;
  } catch (e) { readiness = null; }
  if (!(readiness && readiness.available === true)) {
    const reasons = {expired: pageT('usepod.approval_expired', 'Your spending approval expired.'), revoked: pageT('usepod.approval_revoked', 'Your spending approval was revoked.'),
      spent: pageT('usepod.approval_spent', 'Your one-call spending approval has been used.'), missing: pageT('usepod.approval_missing', 'A spending budget needs your approval.'),
      no_account: pageT('usepod.no_account', 'Your UsePod account is not connected.'), unavailable: pageT('usepod.approval_unchecked', 'Spending approval could not be checked.')};
    const reason = reasons[readiness && readiness.state] || reasons.unavailable;
    if (!readiness) toast(reason + pageT('usepod.nothing_sent', ' Nothing was sent.'));
    openSettingsInFrame('models/usepod/spend');
    return false;
  }
  if (readiness.budget_scope === 'provider') acknowledgePaidPin(chatId, selected, 'conversation');
  // The second half of readiness: the balance the money law will judge. Obtained through the ONE
  // balance door the dispatch reservation reads (a fresh record, else one read-only GET of
  // /proxy/<token>/balance; no inference, no spend) BEFORE the draft is consumed -- so a send never
  // fails on a balance nobody read since Settings was last refreshed (the 02:26 refusal).
  let balance = null;
  try { balance = await postJsonWithin('/api/cloud/usepod/balance', {}, 20000); } catch (e) { balance = null; }
  const blocker = usepodBalanceBlocker(balance);
  if (!blocker) return true;
  toast(blocker + ' Nothing was sent.');
  openSettingsInFrame('models/usepod/spend');
  return false;
}

async function send() {
  // Refused BEFORE the draft is read or cleared: a disabled textarea is the visible half,
  // and a keyboard path or a stale handler must not get past it either -- nor eat what the
  // operator typed on the way.
  if (councilOwnsComposer()) { toast(councilLockNote); return; }
  const draftText = inputEl.value;
  const text = draftText.trim();
  const chatId = displayedChat;
  const draft = attachBucket(chatId);
  if (!text && !draft.length) return;
  // Attachments gate the send BEFORE the typed text is cleared: nothing is sent, nothing is
  // lost, and a second press after the upload is fixed sends the message exactly once.
  if (draft.some((i) => i.state === 'uploading')) { toast('Wait for the attachments to finish uploading.'); return; }
  if (draft.some((i) => i.state === 'failed')) { toast('Remove or retry the failed attachment before sending.'); return; }
  // A message that is only the documents' markers carries no words of the user's own.
  if (!text || (draft.some((i) => i.document) && !textWithoutMarkers(text))) { toast('Add a message saying what to do with the ' + (draft.some((i) => i.document) ? 'document' : 'attachment') + '.'); inputEl.focus(); return; }
  const selected = effectiveModel(chatId, text);
  if (String(selected).startsWith('usepod:') && !isChatBusy(chatId)) {
    if (spendingPreflightPending) return;
    spendingPreflightPending = true;
    let available;
    try { available = await checkUsePodSpending(selected, chatId); }
    finally { spendingPreflightPending = false; }
    if (!available) return;
    // An edit or navigation during the read is not permission to submit a different draft.
    if (!isDisplayed(chatId) || inputEl.value !== draftText || effectiveModel(chatId, text) !== selected) return;
  }
  const attachments = draft.map((i) => attachmentMeta(i));
  if (String(selected).startsWith('usepod:')) {
    let target;
    try { target = await postJsonWithin('/api/cloud/usepod/price-wait/check', {model_id:String(selected).slice(7)}, 20000); }
    catch (e) { toast('Price check unavailable. Your draft is unchanged.'); return; }
    if (!isDisplayed(chatId) || inputEl.value !== draftText || effectiveModel(chatId, text) !== selected) return;
    if (target && target.enabled) {
      const queued = await queueOp('enqueue', chatId, {text, attachments:attachments.map(a=>a.id), price_wait_model:String(selected).slice(7), idempotency_key:'price-' + Date.now()});
      if (!queued || !queued.item) { toast('Could not save this task to the price queue. Your draft is unchanged.'); return; }
      inputEl.value = ''; if (window.VoolComposerExtras) window.VoolComposerExtras.draftConsumed(chatId);
      const owner = chatState(chatId); attachments.forEach(a=>{owner.queuedAttachmentMeta[a.id]=a;});owner.attachments=[];renderAttachStrip();
      await refreshQueue(); toast(target.ready ? 'Price is available. Starting within your UsePod budget.' : 'Task saved. Waiting for your price; keep VOOL open.');
      pumpQueue(chatId); return;
    }
  }
  inputEl.value = '';
  if (window.VoolComposerExtras) window.VoolComposerExtras.draftConsumed(chatId);
  if (isChatBusy(chatId)) {
    // Do not drop it -- persist it in THIS chat's queue and show a chip. Another chat being busy
    // is irrelevant here; only this chat's own slot decides run-now vs enqueue. The staged
    // attachments travel with the queued item by id; their names are remembered in this chat's
    // bucket so the bubble can be painted when the item runs.
    const idem = 'idem-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    const owner = chatState(chatId);
    const extra = { text: text, idempotency_key: idem };
    if (attachments.length) { extra.attachments = attachments.map((a) => a.id); attachments.forEach((a) => { owner.queuedAttachmentMeta[a.id] = a; }); }
    const res = await queueOp('enqueue', chatId, extra);
    if (res && res.item) { if (attachments.length) { owner.attachments = []; if (isDisplayed(chatId)) renderAttachStrip(); } refreshQueue(); }
    else {
      if (window.VoolComposerExtras) window.VoolComposerExtras.restoreDraft(chatId, draftText);
      toast('The message could not be queued. Its text was restored to this chat’s draft.'
        + (attachments.length ? ' Its attachments remain staged.' : ''));
    }
    return;
  }
  await runTurn(text, null, { chatId: chatId, attachments: attachments });
}

// =========================== Attachments: files and photos in the composer ===========================
// One draft per chat (see newChatState), uploaded at pick time so a failed upload is retried in
// place and a send never has to be repeated. The server is the ONE authority on what a file is
// (core/chat_attachments.py); this side asks, shows, and carries ids. Nothing here sends a path.
let ATTACH_LIMITS = { max_files_per_turn: 8, max_bytes_per_file: 10 * 1024 * 1024, max_bytes_per_turn: 25 * 1024 * 1024, document_threshold_chars: 4000, accept: '', summary: '' };
// A paste at or past the server's threshold is a DOCUMENT of the chat (core/chat_attachments.py:
// DOCUMENT_THRESHOLD_CHARS, the transcript's own cap on a user turn), staged through the same door
// as a picked file with its exact bytes. Shorter pastes are ordinary text. The number is read from
// the limits endpoint; this is only the value before that answer arrives.
function docThreshold() { const n = Number(ATTACH_LIMITS.document_threshold_chars); return n > 0 ? n : 4000; }
// How much of a document the chip's preview shows before it says how much more there is.
const DOC_PREVIEW_CHARS = 4000;
const attachBtnEl = document.getElementById('attachBtn');
const attachInputEl = document.getElementById('attachInput');
const attachStripEl = document.getElementById('attachStrip');
const attachChipsEl = document.getElementById('attachChips');
const attachHintEl = document.getElementById('attachHint');
function attachBucket(chatId) { const st = chatState(chatId); if (!Array.isArray(st.attachments)) st.attachments = []; return st.attachments; }
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n >= 1048576) return (n / 1048576).toFixed(n >= 10485760 ? 0 : 1).replace(/\.0$/, '') + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}
function attachHintText() {
  const L = ATTACH_LIMITS;
  // The audio mention follows the server's own accept list: where the speech dependency is
  // absent, audio uploads are refused typed and the picker never advertised them.
  const families = 'text, code, PNG, JPEG, GIF, WebP' + ((L.accept && /audio/.test(L.accept)) ? ', audio' : '');
  return 'Up to ' + L.max_files_per_turn + ' files · ' + fmtBytes(L.max_bytes_per_file) + ' each · ' + fmtBytes(L.max_bytes_per_turn) + ' per message · ' + families;
}
function reflectAttachHint() {
  if (attachHintEl) attachHintEl.textContent = attachHintText();
  if (attachBtnEl) attachBtnEl.title = 'Attach files or photos to this message — ' + attachHintText();
}
async function loadAttachmentLimits() {
  // The rules come from the server's own tables, so the picker's accept list and the hint can
  // never drift from what the door enforces.
  try {
    const r = await fetch('/api/chat/attachments/limits');
    const d = await r.json();
    if (d && d.max_files_per_turn) ATTACH_LIMITS = Object.assign({}, ATTACH_LIMITS, d);
  } catch (e) {}
  if (attachInputEl && ATTACH_LIMITS.accept) attachInputEl.setAttribute('accept', ATTACH_LIMITS.accept);
  reflectAttachHint();
}
function outcomeLabel(o) { return ({ read: 'read', sent: 'read by the model', omitted: 'not read by this model', ignored: 'not used' })[o] || ''; }
// What the SELECTED model can do with a photo, from the catalog's own word -- never a guess.
function imageWarningFor(chatId) {
  let id = 'vool';
  try { id = String(effectiveModel(chatId || displayedChat, '') || modelValue || 'vool'); } catch (e) { id = String(modelValue || 'vool'); }
  if (id === 'vool' || id === 'auto') return 'Auto: read only if the model it picks reads images';
  const row = CLOUD_CATALOG_BY_ID[id];
  if (row && typeof row.supports_images === 'boolean') return row.supports_images ? '' : 'this model cannot read images';
  return 'image support unknown for this model';
}
function buildAttachChip(item, opts) {
  opts = opts || {};
  const chip = document.createElement('div');
  chip.className = 'att-chip';
  chip.dataset.state = item.state || 'ready';
  if (item.outcome) chip.dataset.outcome = item.outcome;
  if (item.id) chip.dataset.id = item.id;
  if (item.document) chip.dataset.document = '1';
  if (item.kind === 'image' && item.previewUrl) {
    const img = document.createElement('img'); img.className = 'att-thumb'; img.alt = ''; img.src = item.previewUrl; chip.appendChild(img);
  } else {
    const ico = document.createElement('span'); ico.className = 'att-ico'; ico.textContent = item.kind === 'image' ? '\u{1F5BC}' : (item.document ? '\u{1F4CB}' : '\u{1F4C4}'); chip.appendChild(ico);
  }
  const name = document.createElement('span'); name.className = 'att-name'; name.textContent = item.name || (item.document ? 'pasted text' : 'attachment'); name.title = item.name || ''; chip.appendChild(name);
  const meta = document.createElement('span'); meta.className = 'att-meta';
  if (item.state === 'uploading') meta.textContent = 'uploading…';
  else if (item.document) {
    // Name, type, size and shape are the chip's whole claim about the document; what the model
    // did with it (once known) rides the same line.
    const parts = [item.media_type || 'text/plain', fmtBytes(item.size)];
    if (Number(item.lines) > 0) parts.push(Number(item.lines).toLocaleString() + ' lines');
    if (item.outcome && outcomeLabel(item.outcome)) parts.push(outcomeLabel(item.outcome));
    meta.textContent = parts.join(' · ');
  } else meta.textContent = item.outcome ? (outcomeLabel(item.outcome) || fmtBytes(item.size)) : fmtBytes(item.size);
  chip.appendChild(meta);
  if (item.document && item.state !== 'failed') buildDocPreview(chip, item);
  if (opts.draft && item.document && item.state === 'ready') {
    const asMessage = document.createElement('button'); asMessage.type = 'button'; asMessage.className = 'att-as-message';
    asMessage.textContent = 'Use as message'; asMessage.title = 'Move the full text into your editable message; nothing is sent';
    asMessage.addEventListener('click', () => useDocumentAsMessage(item.localId)); chip.appendChild(asMessage);
  }
  if (opts.draft && item.kind === 'image' && item.state === 'ready') {
    const warning = imageWarningFor(displayedChat);
    if (warning) { const warn = document.createElement('span'); warn.className = 'att-warn'; warn.textContent = warning; chip.appendChild(warn); }
  }
  if (item.state === 'failed') {
    const err = document.createElement('span'); err.className = 'att-error'; err.textContent = item.error || 'Upload failed.'; chip.appendChild(err);
    if (item.retryable) {
      const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'att-retry'; retry.textContent = 'Retry'; retry.title = 'Upload this file again';
      retry.addEventListener('click', () => retryAttachment(item.localId));
      chip.appendChild(retry);
    }
  }
  if (opts.draft) {
    const x = document.createElement('button'); x.type = 'button'; x.className = 'att-x'; x.textContent = '×'; x.title = 'Remove this attachment'; x.setAttribute('aria-label', 'Remove ' + (item.name || 'attachment'));
    x.addEventListener('click', () => removeAttachment(item.localId));
    chip.appendChild(x);
  }
  return chip;
}
// The expandable preview of a document chip. The text comes from the page's own copy while the
// paste is still a draft here, and from the server's retained bytes otherwise (a sent bubble, a
// reload, another device) -- never from the transcript, which only carries the receipt. An erased
// document says so in the same panel instead of showing nothing.
function buildDocPreview(chip, item) {
  const cid = String(item.sessionId || displayedChat || '');
  const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'att-expand';
  toggle.textContent = 'Preview'; toggle.title = 'Show the beginning of this document exactly as pasted'; toggle.setAttribute('aria-expanded', 'false');
  const pre = document.createElement('pre'); pre.className = 'att-preview'; pre.hidden = true;
  const more = document.createElement('span'); more.className = 'att-more'; more.hidden = true;
  let loaded = false;
  function paint(text) {
    const head = text.length > DOC_PREVIEW_CHARS ? text.slice(0, DOC_PREVIEW_CHARS) : text;
    pre.textContent = head;
    if (text.length > head.length) { more.textContent = '… ' + (text.length - head.length).toLocaleString() + ' more characters; the whole document was kept and is what the model reads.'; more.hidden = false; }
    else more.hidden = true;
  }
  async function load() {
    if (loaded) return;
    loaded = true;
    if (typeof item.text === 'string') { paint(item.text); return; }
    if (!item.id) { pre.textContent = 'This document has no stored copy yet.'; return; }
    try {
      const r = await fetch('/api/chat/attachments/preview?session=' + encodeURIComponent(cid) + '&id=' + encodeURIComponent(item.id));
      if (r.status === 410) { pre.textContent = 'This document was erased from the chat; its name and size are all that remain.'; return; }
      if (!r.ok) { pre.textContent = 'This document is no longer available (HTTP ' + r.status + ').'; return; }
      paint(await r.text());
    } catch (e) { loaded = false; pre.textContent = 'The preview could not be loaded. Check the connection and try again.'; }
  }
  toggle.addEventListener('click', () => {
    const open = pre.hidden;
    pre.hidden = !open; more.hidden = !open || !more.textContent;
    toggle.textContent = open ? 'Hide' : 'Preview';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) load();
  });
  chip.appendChild(toggle);
  chip.appendChild(pre);
  chip.appendChild(more);
}
function renderAttachStrip() {
  if (!attachStripEl || !attachChipsEl) return;
  const items = attachBucket(displayedChat);
  attachChipsEl.innerHTML = '';
  items.forEach((item) => attachChipsEl.appendChild(buildAttachChip(item, { draft: true })));
  attachStripEl.hidden = !items.length;
}
function buildMsgAttachments(list) {
  const wrap = document.createElement('div'); wrap.className = 'msg-att';
  list.forEach((a) => wrap.appendChild(buildAttachChip({ id: a.id, name: a.name, kind: a.kind, size: a.size_bytes, outcome: a.outcome || '', previewUrl: a.previewUrl || '', state: 'ready', document: !!a.document, media_type: a.media_type || '', lines: a.lines || 0, chars: a.chars || 0, sha256: a.sha256 || '' }, { draft: false })));
  return wrap;
}
// The metadata a sent attachment carries on the transcript entry and the bubble: identity and
// shape (a document adds its type, sha256, character and line counts), never contents.
function attachmentMeta(a) {
  return { id: a.id || '', name: a.name || '', kind: a.kind || '', size_bytes: Number(a.size_bytes || a.size || 0), outcome: a.outcome || '', previewUrl: a.previewUrl || '', document: !!a.document, media_type: a.media_type || '', chars: Number(a.chars || 0), lines: Number(a.lines || 0), sha256: a.sha256 || '' };
}
// A transcript row from the server, with the attachment receipt the runtime persisted with it.
function historyEntryFromServer(m) {
  const entry = { role: m.role, content: m.content, ts: m.ts, request_id: m.request_id || '' };
  if (m.artifact && typeof m.artifact === 'object') entry.artifact = { ...m.artifact };
  if (m.role === 'user' && Array.isArray(m.attachments) && m.attachments.length) {
    entry.attachments = m.attachments.map((a) => Object.assign(attachmentMeta(a), { previewUrl: '' }));
  }
  return entry;
}
// A long paste becomes a document of THIS chat: the exact text is encoded once, checked against
// the limits the server published, and staged through the same door as a picked file (with the
// paste source header, so the authority names and retains it). The user's own words in the box
// are untouched; the pasted wall of text never enters the textarea. Text that cannot be encoded
// exactly (an unpaired surrogate) or is over the per-document limit fails ON the chip, visibly,
// and nothing leaves the machine.
// The ONE composer interceptor for composed text: a long paste, a text file dropped on the
// composer, or long text dropped as text. Every path ends in this function, one upload per
// document, and the same chip. `opts.name` is a dropped file's own name (the server keeps it and
// types by its extension); `opts.source` is 'paste' or 'drop' (the door's source header).
async function addPastedDocument(text, chatId, opts) {
  opts = opts || {};
  if (councilOwnsComposer()) { toast(councilLockNote); return null; }
  const cid = String(chatId || displayedChat);
  const bucket = attachBucket(cid);
  if (bucket.length >= ATTACH_LIMITS.max_files_per_turn) { toast('Up to ' + ATTACH_LIMITS.max_files_per_turn + ' attachments per message — the pasted text was not added.'); return null; }
  const item = {
    localId: 'la-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8),
    file: null, bytes: null, text: text, document: true, sessionId: cid, source: opts.source === 'drop' ? 'drop' : 'paste',
    declaredName: opts.name ? String(opts.name) : '',
    name: opts.name ? String(opts.name) : 'pasted text', size: 0, kind: 'text', media_type: 'text/plain', chars: text.length, lines: text.split('\n').length,
    state: 'uploading', id: '', error: '', retryable: false, previewUrl: '', markerText: '',
  };
  const wellFormed = (typeof text.isWellFormed === 'function') ? text.isWellFormed() : !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(text);
  if (!wellFormed) {
    item.state = 'failed'; item.error = 'The pasted text contains invalid characters (an unpaired surrogate), so it could not be kept exactly. Nothing was uploaded.';
  } else {
    item.bytes = new TextEncoder().encode(text); item.size = item.bytes.length;
    if (item.size > ATTACH_LIMITS.max_bytes_per_file) { item.state = 'failed'; item.error = 'The pasted text is ' + fmtBytes(item.size) + ', over the ' + fmtBytes(ATTACH_LIMITS.max_bytes_per_file) + ' per-document limit. Nothing was uploaded.'; }
  }
  bucket.push(item);
  // The marker: the chip's stable identity inside the user's own words, inserted at the caret
  // only for a document that is going to be staged. A refused one never leaves a marker behind.
  if (item.state === 'uploading' && isDisplayed(cid)) item.markerText = insertAtCaret(inputEl, documentMarker(item));
  if (isDisplayed(cid)) renderAttachStrip();
  if (item.state === 'uploading') await uploadAttachment(item, cid);
  return item;
}
// ---- chip <-> marker identity (ported from build/vool-working-mark) --------------------------
// A generated marker carries its chip's identity (`#localId`, rewritten to `#attachmentId` once
// the server has named it), so the two can never drift: if the chip is refused, fails for good
// or is removed, exactly its own marker goes with it -- byte-exactly the text we inserted -- and
// hand-typed text that merely looks like a marker (no token) is never touched.
function documentMarker(item) {
  return '[document: ' + (item.name || 'pasted text') + ' (' + fmtBytes(item.size) + ') #' + (item.id || item.localId) + ']';
}
function insertAtCaret(el, snippet) {
  const at = typeof el.selectionStart === 'number' ? el.selectionStart : el.value.length;
  const before = el.value.slice(0, at), after = el.value.slice(at);
  const padBefore = !before || before.endsWith('\n') ? (before ? '\n' : '') : '\n\n';
  const padAfter = !after || after.startsWith('\n') ? (after ? '\n' : '') : '\n\n';
  const piece = padBefore + snippet + padAfter;
  el.value = before + piece + after;
  const caret = (before + piece).length;
  try { el.setSelectionRange(caret, caret); } catch (e) {}
  try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
  return piece;
}
function rewriteMarkerFor(item) {
  // The staged document has its server name and id now: the marker says so, byte-exactly.
  if (!inputEl || !item || !item.markerText) return;
  const value = inputEl.value;
  if (value.indexOf(item.markerText) === -1) return;
  const fresh = item.markerText.replace(/\[document: [^\]\n]*\]/, documentMarker(item));
  inputEl.value = value.replace(item.markerText, fresh);
  item.markerText = fresh;
  try { inputEl.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
}
function removeMarkerFor(item) {
  if (!inputEl || !item) return;
  const value = inputEl.value;
  let next = value;
  if (item.markerText && value.indexOf(item.markerText) !== -1) {
    next = value.replace(item.markerText, '');
  } else {
    const token = item.id ? ('#' + item.id) : (item.localId ? ('#' + item.localId) : '');
    if (!token || token === '#') return;
    const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    next = value.replace(new RegExp('\\n?\\n?\\[document: [^\\]\\n]*' + escaped + '\\]\\n?\\n?'), '');
  }
  if (next === value) return;
  inputEl.value = next;
  item.markerText = '';
  try { inputEl.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
}
// The user's own words without the generated markers: what decides whether a send has a message.
function textWithoutMarkers(text) {
  return String(text || '').replace(/\[document: [^\]\n]* #(?:la-|srv-)?[A-Za-z0-9_-]+\]/g, '').trim();
}
async function addAttachmentFiles(fileList, chatId) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const cid = String(chatId || displayedChat);
  const bucket = attachBucket(cid);
  const room = ATTACH_LIMITS.max_files_per_turn - bucket.length;
  let accepted = files;
  if (files.length > room) {
    toast('Up to ' + ATTACH_LIMITS.max_files_per_turn + ' attachments per message — ' + (files.length - Math.max(0, room)) + ' not added.');
    accepted = files.slice(0, Math.max(0, room));
  }
  const fresh = [];
  for (const file of accepted) {
    const item = {
      localId: 'la-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8),
      file: file, name: file.name, size: file.size,
      kind: (file.type || '').indexOf('image/') === 0 ? 'image' : 'text',
      state: 'uploading', id: '', error: '', retryable: false, previewUrl: '',
    };
    if (item.kind === 'image') { try { item.previewUrl = URL.createObjectURL(file); } catch (e) { item.previewUrl = ''; } }
    // Limits are stated up front and applied here too, so an oversized file never leaves the
    // machine only to be refused; the server enforces the same numbers regardless.
    if (file.size > ATTACH_LIMITS.max_bytes_per_file) { item.state = 'failed'; item.error = file.name + ' is over the ' + fmtBytes(ATTACH_LIMITS.max_bytes_per_file) + ' per-file limit.'; }
    else if (file.size === 0) { item.state = 'failed'; item.error = file.name + ' is empty; there is nothing to attach.'; }
    bucket.push(item);
    if (item.state === 'uploading') fresh.push(item);
  }
  if (isDisplayed(cid)) renderAttachStrip();
  await Promise.all(fresh.map((item) => uploadAttachment(item, cid)));
}
async function uploadAttachment(item, chatId) {
  item.state = 'uploading'; item.error = '';
  if (isDisplayed(chatId)) renderAttachStrip();
  try {
    const headers = { 'Content-Type': 'application/octet-stream', 'X-Vool-Session-Id': String(chatId) };
    if (item.document) {
      // The authority names, types and retains it; a dropped file's own name rides as a label.
      headers['X-Vool-Attachment-Source'] = item.source === 'drop' ? 'drop' : 'paste';
      if (item.declaredName) headers['X-Vool-Attachment-Name'] = encodeURIComponent(item.declaredName);
    } else { headers['X-Vool-Attachment-Name'] = encodeURIComponent(item.file.name); headers['X-Vool-Attachment-Type'] = item.file.type || ''; }
    const res = await fetch('/api/chat/attachments/upload', { method: 'POST', headers: headers, body: item.file || item.bytes });
    let data = null; try { data = await res.json(); } catch (e) { data = null; }
    if (res.ok && data && data.ok && data.attachment) {
      item.id = data.attachment.id; item.kind = data.attachment.kind || item.kind; item.size = data.attachment.size_bytes || item.size;
      if (item.document || data.attachment.document) {
        item.document = true; item.name = data.attachment.name || item.name; item.media_type = data.attachment.media_type || item.media_type;
        item.chars = data.attachment.chars || item.chars; item.lines = data.attachment.lines || item.lines; item.sha256 = data.attachment.sha256 || '';
        if (isDisplayed(chatId)) rewriteMarkerFor(item);   // the marker now carries the durable id and the server's name
      }
      item.state = 'ready'; item.retryable = false;
    } else {
      // A validation refusal names its reason and is final for these bytes; a server or transport
      // failure is retryable in place.
      item.state = 'failed';
      item.error = (data && data.message) || pageTF('attach.upload_failed_http', 'Upload failed (HTTP {status}).', { status: res.status });
      item.retryable = !(data && data.error && res.status < 500 && data.error !== 'upload_failed');
      // A FINAL refusal (a secret, an unsupported type, over the limit) takes its marker home: the
      // composer returns to its pre-paste bytes; the chip stays, red, with the door's reason.
      if (!item.retryable && item.document && isDisplayed(chatId)) removeMarkerFor(item);
    }
  } catch (e) {
    item.state = 'failed'; item.error = 'The upload did not complete. Check the connection and retry.'; item.retryable = true;
  }
  if (isDisplayed(chatId)) renderAttachStrip();
}
function removeAttachment(localId) {
  const cid = displayedChat;
  const bucket = attachBucket(cid);
  const idx = bucket.findIndex((i) => i.localId === localId);
  if (idx < 0) return;
  const item = bucket.splice(idx, 1)[0];
  renderAttachStrip();
  // The chip's marker goes with it -- only its own, never user-authored text (see removeMarkerFor).
  if (item.document) removeMarkerFor(item);
  if (item.id) {
    fetch('/api/chat/attachments/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: cid, attachment_id: item.id }) }).catch(() => {});
  }
}
async function useDocumentAsMessage(localId) {
  if (councilOwnsComposer()) { toast(councilLockNote); return; }
  const cid = displayedChat, before = inputEl.value;
  const item = attachBucket(cid).find(i => i.localId === localId);
  if (!item || !item.document || item.state !== 'ready') return;
  let text = item.text;
  if (typeof text !== 'string') {
    try {
      const r = await fetch('/api/chat/attachments/preview?session=' + encodeURIComponent(cid) + '&id=' + encodeURIComponent(item.id));
      if (!r.ok) throw new Error('unavailable');
      text = await r.text();
    } catch (e) { toast('Could not load the document. It is still attached.'); return; }
  }
  if (!isDisplayed(cid) || inputEl.value !== before || !attachBucket(cid).includes(item) || councilOwnsComposer()) return;
  const marker = documentMarker(item);
  const next = before.includes(marker) ? before.replace(marker, () => text) : before + (before ? '\n\n' : '') + text;
  removeAttachment(localId);
  inputEl.value = next;
  inputEl.dispatchEvent(new Event('input', {bubbles:true})); inputEl.focus();
  toast('Moved into your message. Review it, then send.');
}
function retryAttachment(localId) {
  const cid = displayedChat;
  const item = attachBucket(cid).find((i) => i.localId === localId);
  if (!item || (!item.file && !item.bytes)) return;
  uploadAttachment(item, cid);
}
// What THIS chat staged and never sent, back on the strip after a reload or a chat switch.
async function restoreStagedAttachments(chatId) {
  const cid = String(chatId || '');
  if (!cid) return;
  try {
    const r = await fetch('/api/chat/attachments?session=' + encodeURIComponent(cid));
    const d = await r.json();
    const bucket = attachBucket(cid);
    const known = new Set(bucket.map((i) => i.id).filter(Boolean));
    ((d && d.attachments) || []).forEach((a) => {
      if (!a || !a.id || known.has(a.id)) return;
      bucket.push({
        localId: 'srv-' + a.id, file: null, id: a.id, name: a.name, size: a.size_bytes, kind: a.kind,
        state: 'ready', error: '', retryable: false, sessionId: cid,
        document: !!a.document, media_type: a.media_type || '', chars: a.chars || 0, lines: a.lines || 0, sha256: a.sha256 || '',
        previewUrl: a.kind === 'image' ? ('/api/chat/attachments/preview?session=' + encodeURIComponent(cid) + '&id=' + encodeURIComponent(a.id)) : '',
      });
    });
    if (isDisplayed(cid)) renderAttachStrip();
  } catch (e) {}
}
// Re-send the LAST turn of a chat after a failed send: same turn id, same attachments, no second
// user bubble. The server treats the same turn as the same turn, so nothing is bound twice.
function resendLastTurn(chatId) {
  const owner = chatState(chatId || displayedChat);
  if (isChatBusy(owner.chatId)) return;
  const run = owner.run;
  if (!run || !run.ended) return;
  const lastUser = owner.history.slice().reverse().find((m) => m && m.role === 'user');
  if (!lastUser) return;
  if (owner.history.length && owner.history[owner.history.length - 1].role === 'assistant') owner.history.pop();
  if (run.assistantMsgEl && typeof run.assistantMsgEl.remove === 'function') run.assistantMsgEl.remove();
  runTurn(lastUser.content, null, { resend: true, turnId: run.turnId, chatId: owner.chatId, attachments: run.attachments || [] });
}
if (attachBtnEl && attachInputEl) {
  attachBtnEl.addEventListener('click', () => { if (councilOwnsComposer()) { toast(councilLockNote); return; } attachInputEl.click(); });
  attachInputEl.addEventListener('change', () => { addAttachmentFiles(attachInputEl.files, displayedChat); attachInputEl.value = ''; });
}
const composerFooterEl = document.querySelector('footer');
if (composerFooterEl && typeof composerFooterEl.addEventListener === 'function') {
  const carriesFiles = (dt) => { const t = dt && dt.types; if (!t) return false; return typeof t.contains === 'function' ? t.contains('Files') : Array.prototype.indexOf.call(t, 'Files') >= 0; };
  composerFooterEl.addEventListener('dragover', (e) => { if (carriesFiles(e.dataTransfer)) { e.preventDefault(); composerFooterEl.classList.add('att-drop'); } });
  composerFooterEl.addEventListener('dragleave', () => composerFooterEl.classList.remove('att-drop'));
  composerFooterEl.addEventListener('drop', (e) => {
    composerFooterEl.classList.remove('att-drop');
    const files = e.dataTransfer && e.dataTransfer.files ? Array.from(e.dataTransfer.files) : [];
    if (files.length) {
      e.preventDefault();
      // A dropped TEXT file is a document of the chat through the same interceptor as a paste
      // (its own name kept, its bytes retained); a dropped image stays a picked-file attachment.
      const images = files.filter((f) => (f.type || '').indexOf('image/') === 0);
      const texts = files.filter((f) => (f.type || '').indexOf('image/') !== 0);
      if (images.length) addAttachmentFiles(images, displayedChat);
      texts.forEach((f) => { const chatId = displayedChat; f.text().then((t) => addPastedDocument(t, chatId, { name: f.name, source: 'drop' })).catch(() => toast(f.name + ' could not be read as text.')); });
      return;
    }
    let text = '';
    try { text = e.dataTransfer ? String(e.dataTransfer.getData('text/plain') || '') : ''; } catch (err) { text = ''; }
    if (text.length >= docThreshold()) { e.preventDefault(); addPastedDocument(text, displayedChat, { source: 'drop' }); }
  });
}
if (inputEl && typeof inputEl.addEventListener === 'function') {
  inputEl.addEventListener('paste', (e) => {
    const files = e.clipboardData && e.clipboardData.files ? Array.from(e.clipboardData.files) : [];
    if (files.length) { e.preventDefault(); addAttachmentFiles(files, displayedChat); return; }
    // Text pasted into the message box stays the user's message, at every length.
    // Attaching/dropping a file is the explicit document path; provider choice never changes this.
    // Leave the browser's text insertion intact, including selection/caret and undo.
  });
}
loadAttachmentLimits();

// ---- Dictation: microphone → on-device transcription → DRAFT text in the composer ----
// The transcript is the operator's own words placed where they type. It is never sent by
// itself: it lands in the textarea, editable and discardable, and becomes a message only
// when the operator sends it — the same authority law every other input path answers to.
// Everything about whether transcription can run at all is the SERVER's typed truth: a
// machine without the speech dependency shows that reason instead of a fake attempt.
//
// The button has four states (idle / recording / transcribing / error), each carried by
// class + title + aria — never colour alone. A second click while recording STOPS and
// transcribes (the old title promised this and the handler refused it); Escape CANCELS the
// recording with no upload. Recognition language is explicit and distinct from both the app
// UI language and the model answer language.
const dictateBtnEl = typeof document !== 'undefined' ? document.getElementById('dictateBtn') : null;
const voiceModeBtnEl = typeof document !== 'undefined' ? document.getElementById('voiceModeBtn') : null;
const dictationNoteEl = typeof document !== 'undefined' ? document.getElementById('dictationNote') : null;
const dictationControlsEl = typeof document !== 'undefined' ? document.getElementById('dictationControls') : null;
const dictationLocaleEl = typeof document !== 'undefined' ? document.getElementById('dictationLocale') : null;
const voiceStopEl = typeof document !== 'undefined' ? document.getElementById('voiceStop') : null;
let dictating = false;
let dictationRecorder = null;
const DICTATION_NOTICE_DISMISSED_KEY = 'vool_dictation_notice_dismissed_v1';
const DICTATION_LOCALE_KEY = 'vool_dictation_locale_v1';
const VOICE_MODE_KEY = 'vool_voice_mode_v1';
const DICTATION_TITLE_IDLE = 'Dictate into this message — recorded and transcribed on this machine only';
const DICTATION_TITLE_RECORDING = 'Recording — click to stop and transcribe';
const DICTATION_TITLE_PTT = 'Hold to talk — release to transcribe into this message';
function dictT(key, fallback, params) {
  // Localised through the page's i18n bundle when it is present; the inline English is the
  // honest fallback until a catalog resolves the key.
  try {
    if (typeof VOOLT === 'function') {
      const t = VOOLT('dictation.' + key, params);
      if (t && t !== 'dictation.' + key) return t;
    }
  } catch (e) {}
  return fallback;
}
let dictationNoticeDismissed = false;
try { dictationNoticeDismissed = localStorage.getItem(DICTATION_NOTICE_DISMISSED_KEY) === '1'; } catch (e) {}
function showDictationNote(text) {
  const hidden = !text || dictationNoticeDismissed;
  if (dictationNoteEl) { dictationNoteEl.textContent = text || ''; dictationNoteEl.hidden = hidden; }
  const notice = document.getElementById('dictationNotice');
  if (notice) notice.hidden = hidden;
}
const dictationDismissEl = document.getElementById('dictationDismiss');
if (dictationDismissEl) dictationDismissEl.addEventListener('click', async () => {
  dictationNoticeDismissed = true;
  try { localStorage.setItem(DICTATION_NOTICE_DISMISSED_KEY, '1'); } catch (e) {}
  showDictationNote('');
  try {
    const response = await fetch('/api/settings/prefs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({speech_notice_dismissed:true})});
    if (!response.ok) throw new Error('Preference was not saved');
  } catch (e) { toast('Notice hidden for now. Could not save this choice for restart.'); }
});
// Native private web sessions can discard browser storage on exit. The existing
// profile preference is the durable authority; wait for it before the boot probe.
const dictationNoticeReady = fetch('/api/settings/prefs').then(r => r.ok ? r.json() : null).then(p => {
  if (p && p.speech_notice_dismissed) {
    dictationNoticeDismissed = true;
    showDictationNote('');
  }
}).catch(() => {});
function dictationLocale() {
  try { return localStorage.getItem(DICTATION_LOCALE_KEY) || 'en-US'; } catch (e) { return 'en-US'; }
}
function setDictating(on) {
  dictating = on;
  if (!dictateBtnEl) return;
  dictateBtnEl.classList.toggle('recording', on);
  dictateBtnEl.classList.remove('error');
  if (voiceMode()) {
    dictateBtnEl.title = on ? DICTATION_TITLE_RECORDING : DICTATION_TITLE_PTT;
    dictateBtnEl.setAttribute('aria-label', on ? 'Recording — release to transcribe' : DICTATION_TITLE_PTT);
  } else {
    dictateBtnEl.title = on ? DICTATION_TITLE_RECORDING : DICTATION_TITLE_IDLE;
    dictateBtnEl.setAttribute('aria-label', on ? DICTATION_TITLE_RECORDING : DICTATION_TITLE_IDLE);
  }
  dictateBtnEl.setAttribute('aria-pressed', on ? 'true' : 'false');
}
function dictationIdleTitle() {
  // The idle contract depends on the interaction mode: hold-to-talk while voice mode is on.
  return voiceMode() ? DICTATION_TITLE_PTT : DICTATION_TITLE_IDLE;
}
function setDictationError(note) {
  showDictationNote(note);
  if (!dictateBtnEl) return;
  dictateBtnEl.classList.remove('recording');
  dictateBtnEl.classList.add('error');
  dictateBtnEl.title = note || dictationIdleTitle();
  dictateBtnEl.setAttribute('aria-pressed', 'false');
}
function setTranscribing(on) {
  if (!dictateBtnEl) return;
  dictateBtnEl.classList.toggle('transcribing', on);
  dictateBtnEl.title = on ? 'Transcribing on this machine…' : dictationIdleTitle();
}
async function beginDictationRecording() {
  if (dictating || !dictateBtnEl) return false;
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    setDictationError(dictT('needs_browser', 'Dictation needs a browser that can record audio. Type your message instead.'));
    return false;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    const reason = e && e.name === 'NotAllowedError'
      ? dictT('permission_denied', 'permission denied')
      : dictT('no_input', 'no input');
    setDictationError(dictT('mic_unavailable', 'The microphone was not available (' + reason + '). Type your message instead.', { reason: reason }));
    return false;
  }
  const mime = (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported) ?
    (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
     MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '') : '';
  let recorder;
  try {
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    setDictationError(dictT('could_not_start', 'Recording could not start in this browser. Type your message instead.'));
    return false;
  }
  const chunks = [];
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  const chatId = displayedChat;
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    dictationRecorder = null;
    setDictating(false);
    const blob = new Blob(chunks, { type: recorder.mimeType || mime || 'audio/webm' });
    if (!blob.size) {
      setDictationError(dictT('nothing_recorded', 'Nothing was recorded.'));
      return;
    }
    setTranscribing(true);
    try {
      const headers = {
        'X-Vool-Session-Id': chatId,
        'X-Vool-Audio-Type': blob.type || '',
        'X-Vool-Dictation-Locale': dictationLocale(),
        'Content-Type': 'application/octet-stream',
      };
      const res = await fetch('/api/chat/dictation', { method: 'POST', headers: headers, body: blob });
      let data = {}; try { data = await res.json(); } catch (e) {}
      if (res.ok && data && data.ok) {
        const text = String(data.text || '');
        const inputEl = document.getElementById('input');
        if (inputEl) inputEl.value = inputEl.value ? (inputEl.value + ' ' + text) : text;
        if (inputEl && isDisplayed(chatId)) inputEl.dispatchEvent(new Event('input'));
        showDictationNote(data.complete ? '' : (data.disclosure || dictT('partial_transcript', 'The recording was longer than the transcription budget; only part was transcribed.')));
      } else {
        // The server's typed truth: a missing dependency names itself and what would fix it.
        setDictationError((data && (data.message + (data.remediation ? ' ' + data.remediation : ''))) || dictT('failed_http', 'Dictation failed (HTTP ' + res.status + ').', { status: res.status }));
      }
    } catch (e) {
      setDictationError(dictT('did_not_complete', 'Dictation did not complete. Check the connection and try again.'));
    } finally {
      setTranscribing(false);
    }
  };
  dictationRecorder = recorder;
  setDictating(true);
  recorder.start();
  // The server refuses recordings past its own bound; stop just short of it here.
  setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 115000);
  return true;
}
function stopDictationRecording() {
  if (dictationRecorder && dictationRecorder.state === 'recording') { dictationRecorder.stop(); return true; }
  return false;
}
function cancelDictationRecording() {
  // Discard: no upload, tracks released, back to idle.
  if (!dictationRecorder) return;
  const recorder = dictationRecorder;
  recorder.onstop = () => { recorder.stream.getTracks().forEach((t) => t.stop()); };
  if (recorder.state === 'recording') recorder.stop();
  dictationRecorder = null;
  setDictating(false);
  showDictationNote('');
}
let pttEndedAt = 0;
async function dictateOnce() {
  if (dictating) { stopDictationRecording(); return; }   // the stop path the title always promised
  // In voice mode the POINTER owns the mic (hold to talk). The click that trails a released
  // hold must not start a second take; a keyboard activation (Enter/Space, no recent
  // pointerup) still starts one, so the button stays keyboard-operable in both modes.
  if (voiceMode() && Date.now() - pttEndedAt < 400) return;
  showDictationNote('');
  await beginDictationRecording();
}
if (dictateBtnEl) dictateBtnEl.addEventListener('click', dictateOnce);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && dictating) { e.preventDefault(); cancelDictationRecording(); }
});
if (dictationLocaleEl) {
  try { dictationLocaleEl.value = dictationLocale(); } catch (e) {}
  dictationLocaleEl.addEventListener('change', () => {
    try { localStorage.setItem(DICTATION_LOCALE_KEY, dictationLocaleEl.value); } catch (e) {}
    probeDictationAvailability();
  });
}
async function probeDictationAvailability() {
  await dictationNoticeReady;
  // The typed availability probe (GET): an absent recogniser shows its reason and remedy
  // BEFORE the operator records, not after a wasted attempt.
  fetch('/api/chat/dictation?locale=' + encodeURIComponent(dictationLocale()))
    .then((r) => r.json())
    .then((j) => {
      if (j && j.available === false) {
        showDictationNote((j.message || 'Dictation is unavailable on this machine.') + (j.remediation ? ' ' + j.remediation : ''));
      }
    })
    .catch(() => {});
}

// ---- Voice conversation: push-to-talk input + optional local read-aloud ----------------
// Explicitly OPT-IN (aria-pressed toggle, persisted): while on, HOLD the microphone to talk
// and release to place the transcript in the composer as a DRAFT — it is still never sent
// until the operator submits, exactly like typed text and exactly like dictation. Completed
// answers in the chat you are looking at are read aloud on this device via the browser's
// local speech synthesis; a Stop control (or Esc) cancels playback. No background listening:
// the microphone opens only while held, and synthesis runs only after a real completion.
function voiceMode() {
  try { return localStorage.getItem(VOICE_MODE_KEY) === 'on'; } catch (e) { return false; }
}
function setVoiceMode(on) {
  try { localStorage.setItem(VOICE_MODE_KEY, on ? 'on' : 'off'); } catch (e) {}
  if (voiceModeBtnEl) voiceModeBtnEl.setAttribute('aria-pressed', on ? 'true' : 'false');
  if (!on) stopReadAloud();
  setDictating(dictating);   // refresh the mic title for the new interaction contract
}
if (voiceModeBtnEl) {
  voiceModeBtnEl.addEventListener('click', () => { setVoiceMode(!voiceMode()); });
  try { voiceModeBtnEl.setAttribute('aria-pressed', voiceMode() ? 'true' : 'false'); } catch (e) {}
}
if (dictateBtnEl && typeof PointerEvent !== 'undefined') {
  dictateBtnEl.addEventListener('pointerdown', (e) => {
    if (!voiceMode() || dictating) return;
    e.preventDefault();
    beginDictationRecording();
  });
  const pttEnd = () => {
    if (voiceMode() && dictating) { pttEndedAt = Date.now(); stopDictationRecording(); }
  };
  dictateBtnEl.addEventListener('pointerup', pttEnd);
  dictateBtnEl.addEventListener('pointerleave', pttEnd);
  dictateBtnEl.addEventListener('pointercancel', () => { if (voiceMode() && dictating) cancelDictationRecording(); });
}
function speech() { return window.speechSynthesis || null; }
function stopReadAloud() {
  const s = speech();
  if (s && s.speaking) { try { s.cancel(); } catch (e) {} }
  if (voiceStopEl) voiceStopEl.hidden = true;
}
function readAloud(text, lang) {
  const s = speech();
  if (!s || !text) return;
  stopReadAloud();
  const u = new SpeechSynthesisUtterance(String(text));
  if (lang) u.lang = lang;
  u.onend = () => { if (voiceStopEl) voiceStopEl.hidden = true; };
  u.onerror = () => { if (voiceStopEl) voiceStopEl.hidden = true; };
  if (voiceStopEl) voiceStopEl.hidden = false;
  try { s.speak(u); } catch (e) { if (voiceStopEl) voiceStopEl.hidden = true; }
}
if (voiceStopEl) voiceStopEl.addEventListener('click', stopReadAloud);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && speech() && speech().speaking) { e.preventDefault(); stopReadAloud(); }
});
document.addEventListener('visibilitychange', () => {
  // No background audio work: leaving the page cancels both a live recording and playback.
  if (document.hidden) { if (dictating) cancelDictationRecording(); stopReadAloud(); }
});
if (typeof window !== 'undefined') {
  window.VoolVoice = Object.freeze({
    enabled: voiceMode,
    setEnabled: setVoiceMode,
    readAloud: readAloud,
    stopReadAloud: stopReadAloud,
    recording: () => dictating,
  });
}
probeDictationAvailability();

// ---- Composer control bar: popovers, selection, persistence, active-mark ----
function reflectControl(popId, lblId, value, labels, prefix) {
  const lbl = document.getElementById(lblId);
  if (lbl) lbl.textContent = (prefix || '') + (labels[value] || '');
  document.querySelectorAll('#' + popId + ' .pop-item').forEach((it) => {
    const on = it.getAttribute(it.hasAttribute('data-mode') ? 'data-mode' : 'data-model') === value;
    const chk = it.querySelector('.pi-check');
    if (chk) chk.textContent = on ? '✓' : '';
    it.setAttribute('aria-checked', on ? 'true' : 'false');
  });
}
let bypassExpiryTimer = null;
function modeDescription(mode) {
  // The catalog carries the reviewed translation; the data-description attribute stays
  // the English authority a missing key falls back to (never a blank explanation).
  const fallback = (() => {
    const item = document.querySelector('#modePop [data-mode="' + mode + '"]');
    return item ? (item.getAttribute('data-description') || '') : '';
  })();
  return pageT('mode.' + String(mode || 'manual') + '_description', fallback);
}
function reflectMode() {
  reflectControl('modePop', 'modeLbl', view.mode, MODE_LABELS, '');
  const btn = document.getElementById('modeBtn');
  if (btn) btn.classList.toggle('bypass', view.mode === 'bypass_permissions');
  // "Bypass permissions" is the longest label and the one it is least acceptable to leave as an
  // ellipsis, so the full name rides on the tooltip whatever the row's width does to the label.
  if (btn) btn.title = pageTF('mode.current_title', '{label} — how VOOL may act', { label: (MODE_LABELS[view.mode] || MODE_LABELS.manual) });
  const title = document.getElementById('modeHelpTitle'), text = document.getElementById('modeHelpText');
  if (title) title.textContent = MODE_LABELS[view.mode] || MODE_LABELS.manual;
  if (text) text.textContent = modeDescription(view.mode);
  const banner = document.getElementById('bypassBanner'), bannerText = document.getElementById('bypassBannerText');
  if (banner) { banner.hidden = view.mode !== 'bypass_permissions'; syncAnswerPendingSurfaces(); }
  if (bannerText && view.bypassGrant) {
    if (view.bypassGrant.until_off) {
      bannerText.textContent = pageT('bypass.chat_only_until_off', 'Bypass permissions · this chat only · active until you turn it off (Revoke stays available)');
    } else {
      const expires = new Date(Number(view.bypassGrant.expires_at || 0) * 1000);
      bannerText.textContent = pageTF('bypass.timed', 'Bypass permissions · {scope} scope · expires {time}', { scope: String(view.bypassGrant.scope || 'task'), time: expires.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) });
    }
  }
  if (bypassExpiryTimer) { clearTimeout(bypassExpiryTimer); bypassExpiryTimer = null; }
  if (view.mode === 'bypass_permissions' && view.bypassGrant) {
    if (view.bypassGrant.until_off) {
      // No timer to arm: the banner says "until you turn it off" and the Revoke button is the
      // lifetime. Deliberately no self-expiry fallback -- an invented timer would quietly
      // convert an explicit no-timer choice into a timed one.
      return;
    }
    const delay = Math.max(0, Number(view.bypassGrant.expires_at || 0) * 1000 - Date.now());
    // The expiry belongs to the chat whose grant it is, captured now -- not to whichever chat is
    // on screen when the timer eventually fires.
    const grantChat = displayedChat, grantOwner = view;
    bypassExpiryTimer = setTimeout(() => { grantOwner.bypassGrant = null; grantOwner.mode = 'manual'; saveModeForSession(grantChat, grantOwner.mode); if (isDisplayed(grantChat)) { reflectMode(); syncModeController(); } toast('Bypass expired. Manual mode is active.'); }, Math.min(delay + 50, 2147483647));
  }
}
async function postMode(payload) {
  const r = await fetch('/api/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ session_id: displayedChat, project_id: view.projectId || '', turn_id: view.run && !view.run.ended ? view.run.turnId : '' }, payload || {})) });
  let data = {}; try { data = await r.json(); } catch (e) {}
  if (!r.ok) { const err = new Error((data && data.error) || ('HTTP ' + r.status)); err.body = data; throw err; }
  return data;
}
async function setModeController(mode, bypassToken) {
  const chatId = displayedChat, owner = chatState(chatId);   // bound before the await
  const data = await postMode({ op: 'set', mode: mode, bypass_token: bypassToken || '' });
  const applied = data && data.state && data.state.mode ? data.state.mode : mode;
  owner.mode = applied;
  if (applied !== 'bypass_permissions') { owner.bypassGrant = null; saveModeForSession(chatId, applied); }
  if (isDisplayed(chatId)) reflectMode();
  if (owner.run && !owner.run.ended) applyTaskEvent(owner.run, { type: 'mode.changed', stage: 'Permissions', summary: 'Mode changed to ' + (MODE_LABELS[applied] || applied) + '.', active_mode: applied });
  return data;
}
async function syncModeController() {
  const chatId = displayedChat, owner = chatState(chatId);   // bound before the await
  try { await setModeController(owner.mode, owner.bypassGrant && owner.bypassGrant.token); }
  catch (e) {
    if (owner.mode === 'bypass_permissions') { owner.bypassGrant = null; owner.mode = 'manual'; saveModeForSession(chatId, owner.mode); if (isDisplayed(chatId)) reflectMode(); }
    toast('Could not activate ' + (MODE_LABELS[owner.mode] || owner.mode) + ': ' + e.message);
  }
}
function openBypassModal() {
  const overlay = document.getElementById('bypassOverlay'), scope = document.getElementById('bypassScope');
  if (!overlay) return;
  if (scope) {
    const taskOption = scope.querySelector('option[value="task"]');
    const projectOption = scope.querySelector('option[value="project"]');
    if (taskOption) taskOption.disabled = !view.run || view.run.ended;
    if (projectOption) projectOption.disabled = !view.projectId;
    if ((scope.value === 'project' && !view.projectId) || (scope.value === 'task' && (!view.run || view.run.ended))) scope.value = view.run && !view.run.ended ? 'task' : 'session';
  }
  overlay.hidden = false;
  const confirm = document.getElementById('bypassConfirm'); if (confirm) confirm.focus();
}
function closeBypassModal() { const overlay = document.getElementById('bypassOverlay'); if (overlay) overlay.hidden = true; }
async function confirmBypass() {
  const scope = document.getElementById('bypassScope'), duration = document.getElementById('bypassDuration'), btn = document.getElementById('bypassConfirm');
  const customField = document.getElementById('bypassCustomField'), customMinutes = document.getElementById('bypassCustomMinutes');
  const untilOff = !!(duration && duration.value === 'until_off');
  if (untilOff && scope && scope.value !== 'session') { toast('“Until I turn it off” applies to this chat only — choose the This chat session scope.'); return; }
  if (scope && scope.value === 'task' && (!view.run || view.run.ended)) { toast('Start a task before choosing task-only bypass, or select this chat session.'); return; }
  let seconds = Number(duration && duration.value || 900);
  if (duration && duration.value === 'custom') {
    const minutes = Math.round(Number(customMinutes && customMinutes.value || 0));
    if (!(minutes >= 15 && minutes <= 1440)) { toast('Custom duration must be between 15 minutes and 24 hours.'); return; }
    seconds = minutes * 60;
  }
  if (btn) btn.disabled = true;
  try {
    const chatId = displayedChat, owner = chatState(chatId);   // bound before the await
    // Two-step activation: mint a single-use, 60-second confirmation bound to THIS
    // exact action, then activate with it. A caller-asserted boolean let any local
    // process confirm on the user's behalf; the nonce is consumed once and only for
    // these exact bindings.
    const minted = await postMode({
      op: 'request_bypass_confirmation',
      scope: scope ? scope.value : 'task',
      duration_seconds: untilOff ? 0 : seconds,
      until_off: untilOff,
      workspace_root: untilOff ? String(owner.workspaceRoot || '') : ''
    });
    const data = await postMode({
      op: 'activate_bypass',
      scope: scope ? scope.value : 'task',
      duration_seconds: untilOff ? 0 : seconds,
      until_off: untilOff,
      workspace_root: untilOff ? String(owner.workspaceRoot || '') : '',
      confirmation_id: minted.confirmation_id
    });
    owner.bypassGrant = data.grant;
    await setModeController('bypass_permissions', owner.bypassGrant.token);
    closeBypassModal();
    toast(untilOff ? 'Bypass is active for this chat until you turn it off. Revoke stays one click away.' : 'Limited bypass is active and will expire automatically.');
  } catch (e) { toast('Bypass was not activated: ' + e.message); }
  finally { if (btn) btn.disabled = false; }
}
async function revokeBypass() {
  const chatId = displayedChat, owner = chatState(chatId);   // bound before the await
  const token = owner.bypassGrant && owner.bypassGrant.token;
  try { await postMode({ op: 'revoke_bypass', token: token || '' }); } catch (e) {}
  owner.bypassGrant = null; owner.mode = 'manual'; saveModeForSession(chatId, owner.mode); if (isDisplayed(chatId)) reflectMode();
  try { await setModeController('manual'); } catch (e) { toast('Bypass ended locally; controller sync failed: ' + e.message); }
}
// The composer shows a short label; the exact name lives on the tooltip and, unchanged, in
// Activity, About and receipts. "NVIDIA: Nemotron 3 Ultra 550B A55B (free)" is 41 characters and
// pushed the control row onto a second line, so a visible label is capped here.
const MODEL_LABEL_MAX_CHARS = 30;
function compactLabel(text, max) {
  const full = String(text == null ? '' : text);
  const cap = max || MODEL_LABEL_MAX_CHARS;
  if (full.length <= cap) return full;
  // The ellipsis counts toward the cap, so the rendered label is never longer than the cap.
  return full.slice(0, cap - 1).replace(/[\s\u00a0]+$/, '') + '\u2026';
}
function reflectModel() {
  // Label priority: known tier -> friendly cloud name -> the raw id itself (never blank).
  const label = MODEL_LABELS[modelValue] || CLOUD_MODEL_LABELS[modelValue] || modelValue;
  reflectControl('modelPop', 'modelLbl', modelValue, { [modelValue]: label }, '');
  // When Auto is sticking to this chat's cloud model, mark the pill (📌) so the switching-stop is visible.
  const btn = document.getElementById('modelBtn'); const lbl = document.getElementById('modelLbl');
  const lane = document.getElementById('modelLane');
  const laneKind = modelValue === 'vool' ? 'auto' : ((modelValue === LOCAL_ONLY_MODEL || isLocalModel(modelValue)) ? 'local' : 'cloud');
  if (lane) { lane.textContent = laneKind.toUpperCase(); lane.className = 'model-lane ' + laneKind; }
  if (lastConnectionSelection !== modelValue) {
    lastConnectionSelection = modelValue;
    renderConnections(lastConnectionRows || []);
    refreshCloudStatus(false);
  }
  if (modelValue === 'vool' && view.stickyModel) {
    if (lbl) lbl.textContent = 'VOOL Auto 📌';
    if (btn) btn.title = 'Auto is staying on this chat’s cloud model (' + view.stickyModel + ') for a consistent voice. Pick a model to change it, or start a new chat to reset.';
  } else {
    if (lbl) lbl.textContent = compactLabel(label);
    // The tooltip carries the exact name unconditionally: the character cap is not the only thing
    // that can hide it -- a narrow composer ellipsizes the label in CSS well before 30 characters,
    // and a name the row happened to fit a moment ago must not lose its tooltip on a resize.
    if (btn) btn.title = label + ' — the model that answers this chat';
  }
}
// Keep an open popover inside the visible window. Measured before the fix at a 900x620 window: the
// model popover rendered 754px tall with its top 256px ABOVE the viewport, so VOOL Auto and the
// Local section were unreachable without maximising the app.
const POPOVER_VIEWPORT_MARGIN = 8;
const POPOVER_MIN_USABLE_HEIGHT = 140;
// The menu is offset from its button by `bottom: calc(100% + 6px)` (or `top` when flipped), so the
// room a given height actually consumes is that height PLUS the gap. Measured without it, a clamped
// menu's top edge landed 2px from the window instead of the 8px margin this code says it keeps.
const POPOVER_ANCHOR_GAP = 6;
function positionPopover(pop, btn) {
  if (!pop || !btn || !pop.classList.contains('open')) return;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight || 0;
  // A zero-height viewport means the page is not laid out (hidden tab, detached frame). Clearing the
  // inline value hands the decision back to the CSS clamp rather than writing a nonsense height.
  if (!viewportHeight) { pop.style.maxHeight = ''; pop.classList.remove('below'); return; }
  const anchor = btn.getBoundingClientRect();
  const roomAbove = anchor.top - POPOVER_VIEWPORT_MARGIN - POPOVER_ANCHOR_GAP;
  const roomBelow = viewportHeight - anchor.bottom - POPOVER_VIEWPORT_MARGIN - POPOVER_ANCHOR_GAP;
  // Only drop below when that genuinely has more usable room -- these controls sit on the footer, so
  // above is nearly always right, and flipping on a near-tie would make the menu jump around.
  const flip = roomBelow > roomAbove && roomBelow >= POPOVER_MIN_USABLE_HEIGHT;
  pop.classList.toggle('below', flip);
  pop.style.maxHeight = Math.max(POPOVER_MIN_USABLE_HEIGHT, Math.floor(flip ? roomBelow : roomAbove)) + 'px';
  // Horizontal clamp. The menu is anchored to one edge of its button, so on a narrow window a
  // right-anchored menu wider than the space to its left hung off the side of the app (measured at
  // a 520px window: left edge at -174px). Width is capped to the window first, then the whole menu
  // is nudged back inside if either edge still sits outside it.
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth || 0;
  if (!viewportWidth) return;
  pop.style.maxWidth = Math.max(220, viewportWidth - POPOVER_VIEWPORT_MARGIN * 2) + 'px';
  // A translate, not a margin: these menus are anchored from one edge (`.right` sets right:0 with
  // left:auto), and a margin on that axis does not move a box positioned from the opposite side.
  pop.style.transform = '';                        // measure unshifted, so the nudge never compounds
  const box = pop.getBoundingClientRect();
  let shift = 0;
  if (box.left < POPOVER_VIEWPORT_MARGIN) shift = POPOVER_VIEWPORT_MARGIN - box.left;
  else if (box.right > viewportWidth - POPOVER_VIEWPORT_MARGIN) shift = (viewportWidth - POPOVER_VIEWPORT_MARGIN) - box.right;
  if (shift) pop.style.transform = 'translateX(' + Math.round(shift) + 'px)';
}
function repositionOpenPopovers() {
  document.querySelectorAll('.popover.open').forEach((pop) => {
    const btn = document.querySelector('[aria-controls="' + pop.id + '"]');
    if (btn) positionPopover(pop, btn);
  });
}
window.addEventListener('resize', repositionOpenPopovers);

function initComposerControls() {
  const groups = [
    { btn: 'modeBtn', pop: 'modePop' },
    { btn: 'modelBtn', pop: 'modelPop' },
  ];
  function closePops(exceptId) {
    groups.forEach((g) => {
      const pop = document.getElementById(g.pop), btn = document.getElementById(g.btn);
      if (!pop || !btn) return;
      if (g.pop !== exceptId) { pop.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }
    });
    const help = document.getElementById('modeHelpPop'), info = document.getElementById('modeInfo');
    if (help && exceptId !== 'modeHelpPop') { help.classList.remove('open'); if (info) info.setAttribute('aria-expanded', 'false'); }
  }
  groups.forEach((g) => {
    const btn = document.getElementById(g.btn), pop = document.getElementById(g.pop);
    if (!btn || !pop) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = pop.classList.contains('open');
      closePops(open ? null : g.pop);
      pop.classList.toggle('open', !open);
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
      if (!open) {
        pop.scrollTop = 0; positionPopover(pop, btn);
        if (g.pop === 'modelPop') {
          // A key saved ANYWHERE since this page loaded must be visible at the NEXT open, not
          // after a reload. The chat page learned about its own overlay's saves, but a key saved
          // through the settings WINDOW left this page's `cloudKeyConnected` false forever — the
          // popover then hid every cloud row over a connected provider (measured on the owner's
          // box, 2026-09-10: test green, Quick Pick empty). Re-read the credential NAMES on
          // open — the same cheap GET boot already does; setCloudConnected renders when it flips.
          refreshCredentials();
        }
      }
    });
    if (g.pop === 'modePop') btn.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault(); closePops(g.pop); pop.classList.add('open'); btn.setAttribute('aria-expanded', 'true');
      const selected = pop.querySelector('[aria-checked="true"]') || pop.querySelector('.pop-item'); if (selected) selected.focus();
    });
  });
  document.addEventListener('click', () => closePops(null));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePops(null); });

  document.querySelectorAll('#modePop .pop-item').forEach((it) => {
    let tipTimer = null, tip = null;
    const hideTip = () => { if (tipTimer) clearTimeout(tipTimer); tipTimer = null; if (tip) tip.remove(); tip = null; };
    const showTip = () => {
      hideTip(); tip = document.createElement('div'); tip.className = 'mode-tooltip'; tip.textContent = it.getAttribute('data-description') || '';
      document.body.appendChild(tip); const r = it.getBoundingClientRect(); tip.style.left = Math.max(8, Math.min(r.right + 8, innerWidth - tip.offsetWidth - 8)) + 'px'; tip.style.top = Math.max(8, Math.min(r.top, innerHeight - tip.offsetHeight - 8)) + 'px';
    };
    it.addEventListener('mouseenter', () => { tipTimer = setTimeout(showTip, 450); });
    it.addEventListener('mouseleave', hideTip); it.addEventListener('focus', showTip); it.addEventListener('blur', hideTip);
    it.addEventListener('click', async (e) => {
      e.stopPropagation();
      const requested = it.getAttribute('data-mode'); closePops(null);
      if (requested === 'bypass_permissions') { openBypassModal(); return; }
      try { await setModeController(requested); } catch (err) { toast('Mode was not changed: ' + err.message); }
    });
  });
  const modeItems = Array.from(document.querySelectorAll('#modePop .pop-item'));
  const modePop = document.getElementById('modePop');
  if (modePop) modePop.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.matches && e.target.matches('.pop-item')) {
      e.preventDefault(); e.target.click(); return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault(); const idx = Math.max(0, modeItems.indexOf(document.activeElement));
    modeItems[(idx + (e.key === 'ArrowDown' ? 1 : -1) + modeItems.length) % modeItems.length].focus();
  });
  const info = document.getElementById('modeInfo'), help = document.getElementById('modeHelpPop');
  if (info && help) info.addEventListener('click', (e) => { e.stopPropagation(); const wasOpen = help.classList.contains('open'); closePops('modeHelpPop'); help.classList.toggle('open', !wasOpen); info.setAttribute('aria-expanded', wasOpen ? 'false' : 'true'); });
  const bypassClose = document.getElementById('bypassClose'), bypassCancel = document.getElementById('bypassCancel'), bypassConfirm = document.getElementById('bypassConfirm'), bypassRevoke = document.getElementById('bypassRevoke');
  if (bypassClose) bypassClose.addEventListener('click', closeBypassModal); if (bypassCancel) bypassCancel.addEventListener('click', closeBypassModal);
  if (bypassConfirm) bypassConfirm.addEventListener('click', confirmBypass); if (bypassRevoke) bypassRevoke.addEventListener('click', revokeBypass);
  const bypassDurationSel = document.getElementById('bypassDuration'), bypassScopeSel = document.getElementById('bypassScope');
  if (bypassDurationSel) bypassDurationSel.addEventListener('change', () => {
    const custom = document.getElementById('bypassCustomField'); if (custom) custom.hidden = bypassDurationSel.value !== 'custom';
    // The no-timer choice is chat-only by construction; switching to it pins the scope so the
    // label the user reads ("this chat only") always matches the authority actually granted.
    if (bypassDurationSel.value === 'until_off' && bypassScopeSel) bypassScopeSel.value = 'session';
  });
  const bypassOverlay = document.getElementById('bypassOverlay'); if (bypassOverlay) bypassOverlay.addEventListener('click', (e) => { if (e.target === bypassOverlay) closeBypassModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && bypassOverlay && !bypassOverlay.hidden) closeBypassModal(); });
  document.querySelectorAll('#modelPop .pop-item:not(.cloud-dyn)').forEach((it) => {
    it.addEventListener('click', (e) => {
      e.stopPropagation();
      // Paid/OpenRouter tiers are disabled until a key is wired -- never select a dead tier.
      if (it.disabled || it.getAttribute('aria-disabled') === 'true') return;
      modelValue = it.getAttribute('data-model');
      rememberActiveModel();
      releaseServerChatPin(displayedChat);
      // Selecting (or leaving) Local Only changes which rows may be offered at all, so the cloud
      // list is rebuilt now rather than at the next key event -- otherwise the popover would keep
      // showing clickable cloud rows for the rest of the session.
      if (cloudKeyConnected) { if (isLocalOnlyMode()) clearCloudModels(); else renderCloudModels(); }
      reflectModel(); closePops(null);
    });
  });
  // Boot restores local preferences only. The first task body carries the selected mode and an
  // explicit user mode change still posts immediately; page load itself creates no runtime row.
  reflectMode(); reflectModel();
}

// ---- Settings modal (BYOK cloud key) ----
const OPENROUTER_CRED = 'llm.cloud.openrouter';
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsFrameOverlay = document.getElementById('settingsFrameOverlay');
const settingsFrame = document.getElementById('settingsFrame');
const orKeyEl = document.getElementById('orKey');
const orSaveEl = document.getElementById('orSave');
const orRemoveEl = document.getElementById('orRemove');
const orTestEl = document.getElementById('orTest');
const orStatusEl = document.getElementById('orStatus');
const orProviderEl = document.getElementById('orProvider');
const orBaseUrlEl = document.getElementById('orBaseUrl');
const cloudPillEl = document.getElementById('cloudPill');
let cloudKeyConnected = false;

// Client-side provider detection mirroring the server's detect_provider: a unique prefix
// preselects the provider; a bare sk- is ambiguous (OpenAI/DeepSeek/Moonshot) and forces the user
// to pick before Save. The server re-validates either way — this is only for a helpful preselect.
function detectProvider(key) {
  const k = (key || '').trim();
  if (k.indexOf('sk-or-') === 0) return { id: 'openrouter', confidence: 'high' };
  if (k.indexOf('sk-ant-') === 0) return { id: 'anthropic', confidence: 'high' };
  if (k.indexOf('sk-proj-') === 0) return { id: 'openai', confidence: 'high' };
  if (k.indexOf('gsk_') === 0) return { id: 'groq', confidence: 'high' };
  if (k.indexOf('AIza') === 0) return { id: 'google', confidence: 'high' };
  if (k.indexOf('sk-') === 0) return { id: '', confidence: 'low' };
  return { id: '', confidence: 'none' };
}

async function loadProviderOptions() {
  if (!orProviderEl || orProviderEl.dataset.loaded) return;
  try {
    const r = await fetch('/api/cloud/providers');
    const j = await r.json();
    (j.providers || []).forEach((p) => {
      const o = document.createElement('option'); o.value = p.id; o.textContent = p.label; orProviderEl.appendChild(o);
    });
    orProviderEl.dataset.loaded = '1';
  } catch (e) { /* fail-soft: Auto-detect still works server-side */ }
}

function syncProviderUi() {
  if (!orProviderEl) return;
  // Auto-detect: preselect a high-confidence provider from the key shape (never override a manual pick).
  if (!orProviderEl.value && orKeyEl && orKeyEl.value) {
    const g = detectProvider(orKeyEl.value);
    if (g.confidence === 'high') orProviderEl.value = g.id;
  }
  if (orBaseUrlEl) orBaseUrlEl.hidden = orProviderEl.value !== 'custom';
}

// The header pill reflects the REAL auth state from /api/cloud/status; each state maps to a
// class the CSS colours (ok=green, failed=red, untested=amber, no_key=gray). The text never
// contains the key.
const CLOUD_PILL_TEXT = { ok: 'Cloud connected', failed: 'Cloud error', untested: 'Cloud untested', no_key: 'Local only' };
function paintCloudPill() { refreshCloudStatus(true); }   // re-fetch the live multi-connection state

// The header pill now shows a dot per REAL connection (cloud provider + fal.ai), each with its own
// hover tooltip; no key -> a single "Local only" dot. paintCloudPill stays as a thin wrapper so the
// existing callers (after a connection test) just re-fetch the live state.
function _connStateClass(s) { return s === 'ok' ? 'ok' : (s === 'failed' ? 'bad' : (s === 'untested' ? 'warn' : '')); }
function _connStateWord(s) { return s === 'ok' ? 'connected' : (s === 'failed' ? 'error' : (s === 'untested' ? 'not verified yet' : 'connected')); }
var lastConnectionSelection = null, lastConnectionRows = [];
// What THIS chat's next turn routes to, read from the same selection the send reads: a pinned
// cloud provider/model, Auto sticking to a cloud model, a local model, Local Only, or plain Auto.
// The header pill paints from this, so it can never show a provider as "this chat's" when the
// chat is on Auto and would only reach that provider as Auto's default.
const PROVIDER_LABELS = { usepod: 'UsePod', openrouter: 'OpenRouter', openai: 'OpenAI', anthropic: 'Anthropic', groq: 'Groq', google: 'Google', deepseek: 'DeepSeek', moonshot: 'Moonshot', custom: 'Custom' };
function chatRoute() {
  const sticky = modelValue === 'vool' && !!view.stickyModel;
  const selected = sticky ? view.stickyModel : modelValue;
  if (!selected || selected === 'vool') return { kind: 'auto', provider: '', model: '' };
  if (selected === LOCAL_ONLY_MODEL) return { kind: 'local-only', provider: '', model: '' };
  if (isLocalModel(selected) || MODEL_LABELS[selected]) return { kind: 'local', provider: '', model: String(selected) };
  const match = String(selected).match(/^(openrouter|openai|anthropic|groq|google|deepseek|moonshot|usepod|custom):/);
  return { kind: sticky ? 'sticky' : 'pin', provider: match ? match[1] : 'openrouter', model: match ? String(selected).slice(match[0].length) : String(selected) };
}
function selectedCloudProvider() { return chatRoute().provider; }
function renderConnections(conns) {
  lastConnectionRows = conns || [];
  const route = chatRoute();
  const selected = route.provider;
  if (selected) {
    const actual = lastConnectionRows.find(c => c.id === 'cloud' && c.provider === selected);
    conns = [actual || {id:'cloud',provider:selected,label:({usepod:'UsePod',openrouter:'OpenRouter'}[selected] || selected),state:'untested'}];
  }
  if (!cloudPillEl) return;
  cloudPillEl.classList.remove('ok', 'bad', 'warn');
  cloudPillEl.innerHTML = '';
  if (route.kind === 'local' || route.kind === 'local-only') {
    // A local route names no provider: nothing here is "connected to" for this chat.
    const d = document.createElement('span'); d.className = 'dot ok'; cloudPillEl.appendChild(d);
    const t = document.createElement('span'); t.className = 'cp-txt'; t.textContent = pageT('header.cloud_local', 'Local'); cloudPillEl.appendChild(t);
    cloudPillEl.title = route.kind === 'local-only'
      ? 'VOOL Auto Local Only — this chat runs on this machine; cloud blocked. Click to choose a model.'
      : 'This chat is pinned to the local model ' + route.model + ' — no cloud provider is used. Click to choose a model.';
    return;
  }
  if (!conns || !conns.length) {
    const d = document.createElement('span'); d.className = 'dot'; cloudPillEl.appendChild(d);
    const t = document.createElement('span'); t.className = 'cp-txt'; t.textContent = pageT('header.cloud_local_only', 'Local only'); cloudPillEl.appendChild(t);
    cloudPillEl.title = 'No cloud key — running fully local. Click for Settings.';
    return;
  }
  if (route.kind === 'auto') {
    // Auto's defaults are labelled as defaults, never painted as this chat's selection.
    const chip = document.createElement('span'); chip.className = 'cp-chip cp-auto';
    chip.title = 'This chat is on VOOL Auto — the connections listed are the defaults Auto may use, not a selection for this chat.';
    const lbl = document.createElement('span'); lbl.className = 'cp-txt'; lbl.textContent = pageT('header.cloud_auto', 'Auto');
    chip.appendChild(lbl); cloudPillEl.appendChild(chip);
    const sep = document.createElement('span'); sep.className = 'cp-sep'; sep.textContent = '·'; cloudPillEl.appendChild(sep);
  }
  conns.forEach((c, i) => {
    if (i) { const sep = document.createElement('span'); sep.className = 'cp-sep'; sep.textContent = '·'; cloudPillEl.appendChild(sep); }
    const chip = document.createElement('span'); chip.className = 'cp-chip';
    const word = _connStateWord(c.state);
    chip.title = selected
      ? c.label + ' — ' + word + (route.kind === 'sticky' ? ' — Auto is staying on ' : ' — this chat’s selected model: ') + route.model
      : c.label + ' — ' + word + (route.kind === 'auto' && c.id === 'cloud' ? ' — an Auto default, not this chat’s selection' : '');
    const dot = document.createElement('span'); dot.className = 'dot ' + _connStateClass(c.state);
    const lbl = document.createElement('span'); lbl.className = 'cp-txt'; lbl.textContent = c.provider === 'usepod' ? 'UsePod' : c.label;
    chip.appendChild(dot); chip.appendChild(lbl); cloudPillEl.appendChild(chip);
  });
  cloudPillEl.title = selected
    ? 'This chat sends to ' + (PROVIDER_LABELS[selected] || selected) + ' · ' + route.model + ' — click to choose a model'
    : 'This chat is on VOOL Auto; the connections shown are Auto’s defaults — click to choose a model';
}
// Is the active model a cloud service (so a turn actually hits OpenRouter/etc.)? A local tier is not.
function isCloudModel() {
  // An installed local tag ("qwen3:8b") also satisfies CLOUD_MODEL_ID_RE, so the inventory has the
  // final say -- otherwise a local turn would pulse the cloud in-use dot and read as cloud spend.
  if (isLocalModel(modelValue)) return false;
  return modelValue.indexOf('openrouter') === 0 || (!MODEL_LABELS[modelValue] && CLOUD_MODEL_ID_RE.test(modelValue));
}
// Idle vs in-use: pulse the connection dot only while a cloud turn is actually running.
function setCloudWorking(on) { if (cloudPillEl) cloudPillEl.classList.toggle('working', !!on); }
async function refreshCloudStatus(probe) {
  try {
    const selected = selectedCloudProvider();
    const r = await fetch('/api/connections?probe=' + (probe ? '1' : '0') + (selected ? '&provider=' + encodeURIComponent(selected) : ''));
    const j = await r.json();
    if (selected !== selectedCloudProvider()) return;
    renderConnections(j.connections || []);
  } catch (e) { /* leave the current pill as-is on a transient failure */ }
}

function setCloudConnected(on) {
  cloudKeyConnected = !!on;
  if (orRemoveEl) orRemoveEl.hidden = !cloudKeyConnected;
  if (orTestEl) orTestEl.hidden = !cloudKeyConnected;
  if (orStatusEl) {
    orStatusEl.textContent = cloudKeyConnected
      ? 'Key saved and encrypted \u2014 choose an exact model or configure VOOL Auto\u2019s free fallback.'
      : 'Not connected — running fully local.';
    orStatusEl.className = 'set-status' + (cloudKeyConnected ? ' ok' : '');
  }
  if (orKeyEl) orKeyEl.value = '';
  const note = document.querySelector('#modelPop .pop-note');
  if (note) note.textContent = isLocalOnlyMode()
    ? 'VOOL Auto Local Only is selected: cloud models are not offered and cannot be reached, whether or not a key is connected.'
    : (cloudKeyConnected
      ? 'Concrete selections are hard pins. Paid rows spend provider credits only when explicitly selected; VOOL Auto stays local-first and can use only the free fallback configured below.'
      : 'Connect a provider key in Settings to choose a cloud model or configure Auto\u2019s free fallback.');
  // Add the live model list when connected; strip it back to the static popover when not.
  if (cloudKeyConnected) renderCloudModels();
  else {
    clearCloudModels();
    // Without a key, a previously-selected cloud model (a concrete id or a paid openrouter tier)
    // can no longer route — revert the composer to local Auto so a send never targets an unusable
    // model. Exclude the local static tiers (now that the id regex also matches a bare id).
    // A pinned LOCAL model needs no key and must survive this revert -- it is the one selection that
    // still routes with no cloud connection at all.
    if (!isLocalModel(modelValue)
        && (modelValue.indexOf('openrouter') === 0 || (!MODEL_LABELS[modelValue] && CLOUD_MODEL_ID_RE.test(modelValue)))) {
      setModelValue('vool');
      reflectModel();
    }
  }
}

// ---- Live OpenRouter model dropdown ----
// A connected key unlocks a live, orderable list of concrete models injected under the static
// tiers. Every guarantee (which models exist, free-vs-paid, the switch itself) lives in the
// tested server endpoints; this renderer is deliberately fail-soft — any error leaves the
// static popover exactly as it was.
let cloudOrder = localStorage.getItem('vool_model_order') || 'featured';
let cloudShowPrices = localStorage.getItem('vool_model_prices') === '1';
let autoFreeModel = 'auto';
let cloudBrowseProvider = '';
function modelSearchKey(value) { return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[\s_\-\u2010-\u2015]+/g, ''); }
const cloudSearchQueries = Object.create(null);
let cloudProviderRows = [];
let cloudCatalogRevision = 0;
function cloudSelectionId(provider, id) {
  return provider && provider !== 'openrouter' ? provider + ':' + id : String(id);
}
function buildCloudPicker() {
  const box = document.createElement('div'); box.className = 'cloud-picker cloud-dyn';
  const label = document.createElement('label'); label.textContent = 'Provider';
  const select = document.createElement('select'); select.setAttribute('aria-label', 'Model provider');
  cloudProviderRows.forEach((p) => {
    const option = document.createElement('option'); option.value = p.id; option.textContent = p.label || p.id;
    select.appendChild(option);
  });
  select.value = cloudBrowseProvider;
  select.addEventListener('change', (e) => {
    e.stopPropagation(); cloudBrowseProvider = select.value; renderCloudModels();
  });
  box.addEventListener('click', (e) => e.stopPropagation());
  label.appendChild(select); box.appendChild(label);
  return box;
}
const CLOUD_ORDER_LABELS = { featured: 'Featured', name: 'Name', context: 'Context' };
if (!CLOUD_ORDER_LABELS[cloudOrder]) cloudOrder = 'featured';

function clearCloudModels() {
  document.querySelectorAll('#modelPop .cloud-dyn, #modelPop .cloud-toolbar').forEach((n) => n.remove());
}

// ---- Installed local models ----
// The picker offered VOOL Auto and, with a key, the cloud catalogue -- so a machine with qwen3:8b
// and qwen3:14b pulled could not be told to use either of them. The runtime already publishes its
// real inventory on /api/tags (the same list `ollama list` shows, minus the embedding and vision
// models the router must never pick for chat); this renders that list and nothing else. A row here
// is an exact pin: `effectiveModel` sends any non-Auto value as the turn's `model`, which the
// server records as an explicit composer pin over its own routing.
function localModelRows(names) {
  const rows = [], seen = new Set();
  (names || []).forEach((raw) => {
    const id = String(raw || '').trim();
    if (!id) return;
    // `vool` is the Auto tier itself and already has its own row at the top of the popover.
    if (MODEL_LABELS[id]) return;
    // The runtime publishes each tag twice: bare (`qwen3:8b`) and provider-qualified
    // (`ollama-local:qwen3:8b`). Both pin the same weights, so show the readable one.
    const bare = id.indexOf('ollama-local:') === 0 ? id.slice('ollama-local:'.length) : id;
    if (seen.has(bare)) return;
    seen.add(bare);
    rows.push({ id: bare, name: bare });
  });
  return rows;
}
function pinLocalModel(id, rowEl) {
  setModelValue(id);
  rememberActiveModel();
  releaseServerChatPin(displayedChat);
  reflectModel();
  document.querySelectorAll('#modelPop .pop-item').forEach((n) => {
    const c = n.querySelector('.pi-check'); if (c) c.textContent = '';
    n.setAttribute('aria-checked', 'false');
  });
  if (rowEl) {
    const chk = rowEl.querySelector('.pi-check'); if (chk) chk.textContent = '✓';
    rowEl.setAttribute('aria-checked', 'true');
  }
  const pop = document.getElementById('modelPop'), btn = document.getElementById('modelBtn');
  if (pop) pop.classList.remove('open');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}
function makeLocalRow(m) {
  const it = document.createElement('button');
  it.type = 'button';
  it.className = 'pop-item local-dyn';
  it.setAttribute('role', 'menuitemradio');
  it.setAttribute('aria-checked', modelValue === m.id ? 'true' : 'false');
  it.setAttribute('data-model', m.id);
  const chk = document.createElement('span'); chk.className = 'pi-check'; chk.textContent = modelValue === m.id ? '✓' : '';
  const body = document.createElement('span'); body.className = 'pi-body'; body.textContent = m.name;
  const hint = document.createElement('span'); hint.className = 'pi-hint'; hint.textContent = 'on this machine · free · exact pin';
  body.appendChild(hint);
  it.appendChild(chk); it.appendChild(body);
  it.addEventListener('click', (e) => { e.stopPropagation(); pinLocalModel(m.id, it); });
  return it;
}
async function renderLocalModels() {
  const pop = document.getElementById('modelPop');
  if (!pop) return;
  let data;
  try {
    const r = await fetch('/api/tags');
    data = await r.json();
  } catch (e) { return; }   // fail-soft: leave the popover exactly as it was
  const names = (data && Array.isArray(data.models)) ? data.models.map((m) => m && (m.name || m.model)) : [];
  const rows = localModelRows(names);
  localModelIds = new Set(rows.map((m) => m.id));
  try { localStorage.setItem('vool_local_models', JSON.stringify([...localModelIds])); } catch (e) {}
  document.querySelectorAll('#modelPop .local-dyn').forEach((n) => n.remove());
  if (!rows.length) return;
  // Anchor above the Auto hint so the local list reads directly under the Auto row it extends.
  const anchor = pop.querySelector('.pop-hint') || pop.querySelector('.pop-note') || null;
  const list = document.createElement('div');
  list.className = 'local-list local-dyn';
  const group = document.createElement('div');
  group.className = 'pop-group';
  group.textContent = 'LOCAL · INSTALLED ON THIS MACHINE';
  list.appendChild(group);
  rows.forEach((m) => list.appendChild(makeLocalRow(m)));
  pop.insertBefore(list, anchor);
  reflectModel();
  positionPopover(pop, document.getElementById('modelBtn'));
}

// ---- Boot hydration from server selection authority (A11) ----
// localStorage `vool_model` is a convenience cache, never a second authority: the
// durable selection truth is the server's persisted policy (GET /api/cloud/model).
// A stale or foreign stored CLOUD pin is replaced by the server's pin — AND when the
// server pin is empty/unset the stale browser pin is CLEARED, never resurrected: a
// reload must not bring a previously paid client-side pin back to life just because
// localStorage still holds it. Local-model pins are machine-derived and stay
// client-owned. Fail-soft: an unreachable daemon leaves the current selection untouched.
// The model popover's provenance strip: renders ONLY the server's own GET /api/cloud/model
// verdict (A11 server authority). Local labels and cached guesses never write here — unknown
// stays the literal word, in the unknown tone, because inventing a spend class is a lie.
function renderSelectionProvenance(sel) {
  const el = document.getElementById('modelProv');
  if (!el) return;
  if (sel == null && isLocalOnlyMode()) {
    el.hidden = false;
    el.innerHTML = '<span class="pp-row"><b>Routing</b><span>Local Only — this machine; cloud blocked</span></span>';
    return;
  }
  if (!sel || !sel.ok) {
    el.hidden = false;
    el.innerHTML = '<span class="pp-row"><b>Server selection</b><span class="pp-unknown">UNKNOWN — server did not answer</span></span>';
    return;
  }
  const pin = String(sel.model || '').trim();
  const provider = String(sel.provider || '').trim();
  const cost = String(sel.cost_state || '');
  const costCls = cost === 'paid' ? 'pp-paid' : cost === 'free' ? 'pp-free' : 'pp-unknown';
  const costWord = cost === 'paid' ? 'PAID — may spend provider credits' : cost === 'free' ? 'free' : cost ? 'UNKNOWN' : 'not classified (local/Auto)';
  const rows = ['<span class="pp-row"><b>Pinned</b><span>' + esc(pin ? (provider ? provider + ' · ' + pin : pin) : 'none — VOOL Auto routing') + '</span></span>',
    '<span class="pp-row"><b>Spend class</b><span class="' + costCls + '">' + esc(costWord) + '</span></span>'];
  if (sel.free_cloud_enabled && sel.auto_free_model && sel.auto_free_model !== 'auto') {
    rows.push('<span class="pp-row"><b>Auto free fallback</b><span>' + esc(String(sel.auto_free_model)) + '</span></span>');
  }
  el.hidden = false;
  el.innerHTML = rows.join('');
}

async function hydrateModelFromServer() {
  if (isLocalOnlyMode()) { renderSelectionProvenance(null); return; }
  const chatId = displayedChat;
  const initialModel = modelValue;
  let j;
  try {
    const r = await fetch('/api/cloud/model?session_id=' + encodeURIComponent(chatId));
    j = await r.json();
  } catch (e) { renderSelectionProvenance(null); return; }
  if (!isDisplayed(chatId) || modelValue !== initialModel) return;
  if (!j || !j.ok) { renderSelectionProvenance(null); return; }
  renderSelectionProvenance(j);
  const serverModel = serverSelectionId(j);
  // Server truth wins in BOTH directions for THIS chat's CLOUD pin: a different server pin
  // replaces the cached one -- and a chat the page holds on Auto or a local tier adopts the pin
  // the operator persisted for it (a pill saying Auto over a strip saying "Pinned: usepod" was
  // the contradiction observed 2026-09-16) -- while an EMPTY server pin clears the stale cached
  // cloud pin instead of resurrecting it. Local-model pins with no server pin stay client-owned.
  if (!serverModel) {
    if (!isCloudPinId(modelValue)) return;
    setModelValue('vool', chatId);
    rememberActiveModel();
    try { reflectModel(); } catch (e) {}
    return;
  }
  if (serverModel === modelValue) return;
  setModelValue(serverModel, chatId);
  rememberActiveModel();
  try { reflectModel(); } catch (e) {}
}

// The server's per-chat selection as ONE composer id: a provider-prefixed cloud pin, a bare
// OpenRouter id, or '' for a chat with no cloud pin.
function serverSelectionId(j) {
  if (!j) return '';
  return j.provider && j.provider !== 'openrouter' && j.model ? String(j.provider) + ':' + String(j.model) : String(j.model || '').trim();
}
function isCloudPinId(id) {
  const value = String(id || '');
  if (!value || value === 'vool' || value === LOCAL_ONLY_MODEL || MODEL_LABELS[value] || isLocalModel(value)) return false;
  return value.indexOf('openrouter') === 0 || CLOUD_MODEL_ID_RE.test(value);
}
// For a chat entered mid-session: a cloud pin the server holds for THIS chat is adopted, so the
// persisted selection, the composer pill and the outgoing turn name one route (a pill saying Auto
// over a strip saying "Pinned: usepod · gpt-6-astra" was the contradiction observed 2026-09-16).
// An EMPTY server answer clears nothing here -- that reload-style clear belongs to boot
// (hydrateModelFromServer); a client-only pin must survive a reopen. Writes nothing when nothing
// changed, so an in-flight switch keeps its revision; a stale answer is dropped by the caller's guards.
function adoptServerSelection(chatId, j) {
  const serverModel = serverSelectionId(j);
  if (!serverModel || serverModel === modelForChat(chatId)) return false;
  setModelValue(serverModel, chatId);
  if (isDisplayed(chatId)) { try { reflectModel(); } catch (e) {} }
  return true;
}
// Choosing Auto, Local Only or a local model in a chat also releases that chat's SERVER cloud pin.
// The server's per-chat selection is what the provenance strip and every other client read; a
// cloud pin left there after the composer moved on painted "Pinned: usepod · gpt-6-astra" over a
// chat about to send on Auto. The revision guards a late answer exactly as a switch does.
async function releaseServerChatPin(chatId) {
  if (!CANONICAL.test(String(chatId || ''))) return false;
  const revision = Math.max(Date.now(), (modelSelectionRevisions[chatId] || 0) + 1);
  modelSelectionRevisions[chatId] = revision;
  try {
    const r = await fetch('/api/cloud/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: 'auto', session_id: chatId, selection_revision: revision }) });
    if (!r || !r.ok) return false;
    if (modelSelectionRevisions[chatId] === revision && isDisplayed(chatId)) refreshSelectionProvenance();
    return true;
  } catch (e) { return false; }
}

// Strip-only refresh for NAVIGATION and landed switches: repaint the provenance lens from
// the server's verdict for the displayed chat, without boot's adoption/clearing side
// effects. Mid-session the composer pin was set through the server door or restored from
// THIS chat's own cache; the reload-style clear of a stale cross-chat pin belongs to boot,
// not to switching chats (a client-only pin must survive a reopen).
let provenanceRefreshRevision = 0;
async function refreshSelectionProvenance() {
  const revision = ++provenanceRefreshRevision;
  if (isLocalOnlyMode()) { renderSelectionProvenance(null); return; }
  const chatId = displayedChat;
  const current = () => revision === provenanceRefreshRevision && isDisplayed(chatId) && !isLocalOnlyMode();
  let j;
  try {
    const r = await fetch('/api/cloud/model?session_id=' + encodeURIComponent(chatId));
    j = await r.json();
  } catch (e) { if (current()) renderSelectionProvenance(null); return; }
  if (!current()) return;
  if (!j || !j.ok) { renderSelectionProvenance(null); return; }
  renderSelectionProvenance(j);
  adoptServerSelection(chatId, j);
}

// Acknowledgement is scoped to this conversation and exact provider/model. It is only a
// presentation decision: server price ceilings, revocation and monetary grants still gate every
// dispatch. A chat-scoped acceptance persists (localStorage) for 24 hours so asking again for
// the same chat and model is a PRICE EVENT (a rise above the accepted bound, a revoked grant),
// not the passage of a page reload; a one-turn acceptance stays in-memory and dies with the
// turn. Switching models clears the previous choice either way.
const paidPinAcknowledgements = (() => {
  const store = 'vool_paid_pin_acks_v1';
  let rows = {};
  try { rows = JSON.parse(localStorage.getItem(store) || '{}') || {}; } catch (e) { rows = {}; }
  const persist = () => { try { localStorage.setItem(store, JSON.stringify(rows)); } catch (e) {} };
  return {
    get(key) { return Object.prototype.hasOwnProperty.call(rows, key) ? rows[key] : undefined; },
    set(key, value) { rows[key] = value; persist(); },
    delete(key) { if (Object.prototype.hasOwnProperty.call(rows, key)) { delete rows[key]; persist(); } },
  };
})();
function paidAckKey(chatId, id) { return JSON.stringify([String(chatId), String(id)]); }
function acknowledgePaidPin(chatId, id, scope) {
  paidPinAcknowledgements.set(paidAckKey(chatId, id), {
    scope: scope === 'conversation' ? 'conversation' : 'once',
    // The per-turn ack is dead with the turn anyway; the chat ack survives a day.
    expiresAt: Date.now() + (scope === 'conversation' ? 86400000 : 3600000),
  });
}
function paidPinAcked(id, chatId = displayedChat) {
  const key = paidAckKey(chatId, id), ack = paidPinAcknowledgements.get(key);
  if (!ack || ack.expiresAt <= Date.now()) { paidPinAcknowledgements.delete(key); return false; }
  return true;
}
function consumePaidPinAck(chatId, id) {
  const key = paidAckKey(chatId, id), ack = paidPinAcknowledgements.get(key);
  if (ack && ack.scope === 'once') paidPinAcknowledgements.delete(key);
}
function priceText(m) {

  if (m.free) return 'free';
  const p = Number(m.prompt_usd_per_m || 0), c = Number(m.completion_usd_per_m || 0);
  if (!p && !c) return '';
  return '$' + p.toFixed(2) + '/' + c.toFixed(2) + ' per 1M';
}

async function modelSelectionPost(body) {
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error('Model selection took too long. Check the connection and try selecting it again.')); }, 20000);
  });
  try {
    return await Promise.race([fetch('/api/cloud/model', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal:controller.signal }).then(async r => ({r, j:await r.json()})), timeout]);
  } finally { clearTimeout(timer); }
}
const pendingModelSelections = new Set();
async function switchCloudModel(id, name, rowEl, provider) {
  const selectionKey = JSON.stringify([displayedChat, provider || '', id]);
  if (pendingModelSelections.has(selectionKey)) return false;
  pendingModelSelections.add(selectionKey);
  try { return await performCloudModelSwitch(id, name, rowEl, provider); }
  finally { pendingModelSelections.delete(selectionKey); }
}
async function performCloudModelSwitch(id, name, rowEl, provider) {
  const chatId = displayedChat;
  const revision = Math.max(Date.now(), (modelSelectionRevisions[chatId] || 0) + 1);
  modelSelectionRevisions[chatId] = revision;
  const stillCurrent = () => modelSelectionRevisions[chatId] === revision;
  try {
    // A11 SERVER-OWNED confirmation: the client never decides what counts as paid. The first
    // POST carries NO confirm_paid — the server classifies the id and derives whether a
    // confirmation is required. Only when the server answers with a machine-readable refusal
    // code does the client ASK the operator, and the retried POST carries confirm_paid=true
    // strictly for THAT request (request-scoped; nothing sticky is stored anywhere).
    // A row names its own provider, and the pin carries it: the server never has to guess the provider from which
    // keys happen to be stored (an accountless lane has none).
    const pinBody = { model: id, session_id: chatId, selection_revision: revision };
    if (provider) pinBody.provider = String(provider);
    let {r, j} = await modelSelectionPost(pinBody);
    let paidDecision = false;
    if (!stillCurrent()) return false;
    if (r.status === 409 && j && typeof j.code === 'string') {
      const code = j.code;
      const classifiedPaid = code === 'paid_model_confirm_required';
      const costUnclassified = code === 'MODEL_COST_UNKNOWN';
      const pricingIndeterminate = code === 'PAID_STATUS_UNKNOWN';
      const priceRose = code === 'price_above_accepted';
      if (classifiedPaid || costUnclassified || pricingIndeterminate || priceRose) {
        const reasonText = classifiedPaid
          ? 'The server classified this model as PAID.'
          : costUnclassified
            ? 'The server could not verify what this model costs.'
            : priceRose
              ? 'The catalog now lists this model ABOVE the price you accepted earlier.'
              : "This model's published pricing is indeterminate on the server.";
        const gateOk = window.VoolPriceGate
          ? await window.VoolPriceGate.review({ kind: 'pin', id: cloudSelectionId(provider, id), provider: provider, label: name || id, code: code, accepted: j.accepted || null, current: j.current || null })
          : window.confirm(reasonText + ' Pin "' + id + '" and allow spend on the pinned lane? Spend caps still apply. This covers this switch only.');
        if (gateOk) {
          paidDecision = gateOk;
          if (!stillCurrent()) return false;
          ({r, j} = await modelSelectionPost(Object.assign({}, pinBody, { confirm_paid: true })));
        } else {
          return false;
        }
      }
    }
    if (r.ok && j && j.ok) {
      if (!stillCurrent()) return false;
      const selected = j.provider && j.provider !== 'openrouter' ? j.provider + ':' + (j.model || id) : (j.model || id);
      CLOUD_MODEL_LABELS[selected] = name || selected;
      setModelValue(selected, chatId);
      if (isDisplayed(chatId)) { rememberActiveModel(); reflectModel(); }
      if (paidDecision) acknowledgePaidPin(chatId, selected, paidDecision);
      // The pin just landed on the server; re-read its verdict so the provenance strip shows
      // THIS chat's new pin (and its spend class), not the profile the page booted with.
      refreshSelectionProvenance();
      return true;
    }
    throw new Error((j && (j.message || j.error)) || 'Could not select this model. Try again.');
  } catch (e) {
    if (!stillCurrent()) return false;
    const message = e.message || 'Could not select this model. Try again.';
    if (rowEl) {
      rowEl.dataset.selectionError = message;
      const hint = rowEl.querySelector('.pi-hint');
      if (hint) hint.textContent = message;
    }
    toast(message);
  }
  return false;
}

async function switchAutoFreeModel(id, selectEl) {
  try {
    const r = await fetch('/api/cloud/auto-model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: id }) });
    const j = await r.json();
    if (r.ok && j.ok) {
      autoFreeModel = j.model || id;
      if (selectEl) selectEl.title = j.message || '';
      // Auto's fallback is one of the rows the provenance strip renders; refresh it from the
      // server rather than leaving the strip describing the previous fallback.
      refreshSelectionProvenance();
      return true;
    }
  } catch (e) {}
  if (selectEl) { selectEl.value = autoFreeModel; selectEl.title = 'Could not change Auto fallback.'; }
  return false;
}

function makeCloudRow(m) {
  const selectionId = cloudSelectionId(m.provider, m.id);
  const it = document.createElement('button');
  it.type = 'button';
  it.className = 'pop-item cloud-dyn';
  it.setAttribute('role', 'menuitemradio');
  it.setAttribute('aria-checked', modelValue === selectionId ? 'true' : 'false');
  it.setAttribute('data-model', selectionId);
  const chk = document.createElement('span'); chk.className = 'pi-check'; chk.textContent = modelValue === selectionId ? '✓' : '';
  const body = document.createElement('span'); body.className = 'pi-body'; body.textContent = m.name || m.id;
  const kind = document.createElement('span'); kind.className = 'pi-hint';
  let hint = m.free ? 'free · exact pin' : 'paid · exact pin';
  if (!m.free && window.VoolPriceGate && window.VoolPriceGate.acceptanceFor) {
    const ceiling = window.VoolPriceGate.acceptanceFor(selectionId);
    if (ceiling && ceiling.rates_known) hint += ' · accepted $' + Number(ceiling.prompt_usd_per_m).toFixed(2) + '/' + Number(ceiling.completion_usd_per_m).toFixed(2);
  }
  kind.textContent = hint; body.appendChild(kind);
  it.appendChild(chk); it.appendChild(body);
  if (cloudShowPrices) { const pr = document.createElement('span'); pr.className = 'pi-price' + (m.free ? ' free' : ''); pr.textContent = priceText(m); it.appendChild(pr); }
  let switching = false;
  it.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (switching) return;
    switching = true; it.disabled = true;
    delete it.dataset.selectionError;
    const previousHint = kind.textContent;
    kind.textContent = 'Checking model and price…';
    it.setAttribute('aria-busy', 'true');
    try {
    // The switch first asks the server to classify this exact provider/model, then
    // presents its one confirmation. A second row-level prompt duplicates that
    // decision and relies on a catalogue label that may already be stale.
    if (await switchCloudModel(m.id, m.name, it, m.provider)) {
      document.querySelectorAll('#modelPop .pop-item').forEach((n) => { const c = n.querySelector('.pi-check'); if (c) c.textContent = ''; n.setAttribute('aria-checked', 'false'); });
      chk.textContent = '✓'; it.setAttribute('aria-checked', 'true');
      const pop = document.getElementById('modelPop'), btn = document.getElementById('modelBtn');
      if (pop) pop.classList.remove('open'); if (btn) btn.setAttribute('aria-expanded', 'false');
    }
    } finally {
      switching = false; it.disabled = false; it.setAttribute('aria-busy', 'false');
      if (!it.dataset.selectionError) kind.textContent = previousHint;
    }
  });
  return it;
}

function buildCloudToolbar(onPricesChange) {
  const bar = document.createElement('div'); bar.className = 'pop-order cloud-toolbar';
  const lab = document.createElement('span'); lab.textContent = 'Sort'; bar.appendChild(lab);
  const sel = document.createElement('select');
  Object.keys(CLOUD_ORDER_LABELS).forEach((k) => { const o = document.createElement('option'); o.value = k; o.textContent = CLOUD_ORDER_LABELS[k]; if (k === cloudOrder) o.selected = true; sel.appendChild(o); });
  sel.addEventListener('click', (e) => e.stopPropagation());
  sel.addEventListener('change', (e) => { e.stopPropagation(); cloudOrder = sel.value; localStorage.setItem('vool_model_order', cloudOrder); renderCloudModels(); });
  bar.appendChild(sel);
  const priceLab = document.createElement('label');
  const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = cloudShowPrices;
  cb.addEventListener('click', (e) => e.stopPropagation());
  cb.addEventListener('change', (e) => { e.stopPropagation(); cloudShowPrices = cb.checked; localStorage.setItem('vool_model_prices', cb.checked ? '1' : '0'); if (onPricesChange) onPricesChange(); else renderCloudModels(); });
  priceLab.appendChild(cb); priceLab.appendChild(document.createTextNode('Prices'));
  bar.appendChild(priceLab);
  return bar;
}

function buildAutoFallbackControl(freeModels) {
  const bar = document.createElement('div'); bar.className = 'pop-order cloud-toolbar auto-fallback';
  const lab = document.createElement('label'); lab.textContent = 'Auto fallback';
  const sel = document.createElement('select');
  const best = document.createElement('option'); best.value = 'auto'; best.textContent = 'Best verified free'; sel.appendChild(best);
  freeModels.forEach((m) => { const o = document.createElement('option'); o.value = m.id; o.textContent = m.name || m.id; sel.appendChild(o); });
  sel.value = autoFreeModel;
  if (sel.value !== autoFreeModel) { autoFreeModel = 'auto'; sel.value = 'auto'; }
  sel.addEventListener('click', (e) => e.stopPropagation());
  sel.addEventListener('change', async (e) => { e.stopPropagation(); const previous = autoFreeModel; if (!(await switchAutoFreeModel(sel.value, sel))) sel.value = previous; });
  lab.appendChild(sel); bar.appendChild(lab);
  const hint = document.createElement('span'); hint.className = 'pi-hint'; hint.textContent = 'used only by VOOL Auto; never paid'; bar.appendChild(hint);
  return bar;
}

async function renderCloudModels() {
  const revision = ++cloudCatalogRevision;
  const pop = document.getElementById('modelPop');
  if (!pop || !cloudKeyConnected) return;
  // Under Local Only the cloud catalogue is not offered at all. Showing rows that the server would
  // refuse would be an invitation to a dead end, and one click on a paid row is exactly the
  // accident the mode exists to make impossible. The rows come back on the next render once the
  // lane changes; `clearCloudModels` leaves the static popover intact.
  if (isLocalOnlyMode()) { clearCloudModels(); return; }
  if (!cloudBrowseProvider || !cloudProviderRows.some((p) => p.id === cloudBrowseProvider)) {
    clearCloudModels(); return;
  }
  const provider = cloudBrowseProvider;
  const current = () => revision === cloudCatalogRevision && cloudKeyConnected && !isLocalOnlyMode() && provider === cloudBrowseProvider;
  const catalogUrl = '/api/cloud/models?provider=' + encodeURIComponent(provider) + '&order=' + encodeURIComponent(cloudOrder);
  clearCloudModels();
  pop.insertBefore(buildCloudPicker(), pop.querySelector('.pop-note'));
  let data;
  let fetchError = '';
  try {
    const r = await fetch(catalogUrl);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (e) { fetchError = String(e && e.message ? e.message : e); }  // fail-soft: keep whatever is already shown
  if (!current()) return;
  if (data && data.provider !== provider) { fetchError = 'Provider catalogue identity mismatch'; data = null; }
  const models = (Array.isArray(data && data.models) ? data.models : [])
    .filter((m) => m && typeof m.id === 'string' && (!m.provider || m.provider === provider))
    .map((m) => Object.assign({}, m, { provider: provider }));
  if (!models.length) {
    // EMPTY IS A STATE, NOT A SHRUG. The connection test proves the KEY authorizes; it says
    // nothing about the catalog (measured on the owner's box, 2026-09-10: test green, Quick Pick
    // silently kept its static "connect a key" note over an unloaded catalog, with no way to tell
    // "not fetched yet" from "fetch failed"). Say which one it is and offer the one action that
    // changes it — a live catalog fetch through the server's own ?refresh=1 door (public catalog
    // GET, no generation, no key material in the response).
    clearCloudModels();
    const note = pop.querySelector('.pop-note');
    const anchor = note || null;
    pop.insertBefore(buildCloudPicker(), anchor);
    const row = document.createElement('div');
    row.className = 'pop-group cloud-dyn cloud-empty';
    const msg = document.createElement('span'); msg.className = 'pi-hint';
    const refreshInfo = data && typeof data.refresh === 'object' && data.refresh ? data.refresh : null;
    if (fetchError) msg.textContent = 'Could not load the model catalog: ' + fetchError;
    else if (refreshInfo && refreshInfo.ok === false) msg.textContent = 'Model catalog refresh failed' + (refreshInfo.error ? ': ' + refreshInfo.error : '') + '.';
    else msg.textContent = 'No model catalog loaded yet. The connection test checks your key, not the catalog.';
    row.appendChild(msg);
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'pop-item cloud-dyn cloud-retry';
    retry.textContent = refreshInfo && refreshInfo.ok === false ? 'Try the catalog fetch again' : 'Fetch the model catalog now';
    retry.addEventListener('click', async (e) => {
      e.stopPropagation();
      retry.disabled = true; retry.textContent = 'Fetching\u2026';
      try {
        const r = await fetch(catalogUrl + '&refresh=1');
        await r.json();
      } catch (err) { /* the next render reports whatever happened */ }
      if (current()) await renderCloudModels();
    });
    pop.insertBefore(row, anchor);
    pop.insertBefore(retry, anchor);
    positionPopover(pop, document.getElementById('modelBtn'));
    return;
  }
  models.forEach((m) => {
    if (m && m.id) {
      const key = cloudSelectionId(provider, m.id);
      CLOUD_CATALOG_BY_ID[key] = m;
      // The same rows feed the friendly-label map, so a pin restored from storage on reload reads
      // as its catalog name once the catalog lands -- the raw id is the LAST-resort label only.
      CLOUD_MODEL_LABELS[key] = String(m.name || m.id);
    }
  });
  const provLabel = (data && data.label) || ((data && data.provider) === 'openrouter' ? 'OpenRouter' : (data && data.provider) || 'Cloud');
  clearCloudModels();
  const note = pop.querySelector('.pop-note');
  const anchor = note || null;  // inject the live list just above the note (or at the end)
  const free = models.filter((m) => m.free);
  const picker = buildCloudPicker();
  const searchLabel = document.createElement('label'); searchLabel.textContent = 'Model';
  const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search models';
  search.setAttribute('aria-label', 'Search provider models');
  search.value = cloudSearchQueries[provider] || '';
  searchLabel.appendChild(search); picker.appendChild(searchLabel);
  pop.insertBefore(picker, anchor);
  autoFreeModel = String((data && data.auto_free_model) || 'auto');
  if ((data && data.provider) === 'openrouter') pop.insertBefore(buildAutoFallbackControl(free), anchor);
  pop.insertBefore(buildCloudToolbar(renderMatches), anchor);
  function group(text) { const g = document.createElement('div'); g.className = 'pop-group cloud-dyn'; g.textContent = text; return g; }
  const list = document.createElement('div'); list.className = 'cloud-list cloud-dyn';
  let visibleLimit = 40;
  function renderMatches() {
    list.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase();
    const matches = models.filter((m) => !query || modelSearchKey(m.id + ' ' + (m.name || '')).includes(modelSearchKey(query)));
    const shown = matches.slice(0, visibleLimit);
    const freeShown = shown.filter((m) => m.free), paidShown = shown.filter((m) => !m.free);
    if (freeShown.length) { list.appendChild(group('FREE · ' + provLabel)); freeShown.forEach((m) => list.appendChild(makeCloudRow(m))); }
    if (paidShown.length) { list.appendChild(group('PAID · ' + provLabel)); paidShown.forEach((m) => list.appendChild(makeCloudRow(m))); }
    if (!matches.length) list.appendChild(group('No matching models'));
    if (matches.length > visibleLimit) {
      const more = document.createElement('button'); more.type = 'button'; more.className = 'pop-item';
      more.textContent = 'Show more (' + (matches.length - visibleLimit) + ' remaining)';
      more.addEventListener('click', (e) => { e.stopPropagation(); visibleLimit += 40; renderMatches(); });
      list.appendChild(more);
    }
    positionPopover(pop, document.getElementById('modelBtn'));
  }
  search.addEventListener('input', () => { cloudSearchQueries[provider] = search.value; visibleLimit = 40; renderMatches(); });
  renderMatches();
  pop.insertBefore(list, anchor);
  reflectModel();  // refresh the header label once friendly names are known
  // The list just changed the popover's height, so the clamp has to be recomputed.
  positionPopover(pop, document.getElementById('modelBtn'));
}

let cloudCredentialsRevision = 0;
async function refreshCredentials() {
  const revision = ++cloudCredentialsRevision;
  try {
    const r = await fetch('/api/settings/credentials');
    if (!r.ok) throw new Error('Credential list unavailable');
    const j = await r.json();
    const rp = await fetch('/api/cloud/providers');
    if (!rp.ok) throw new Error('Provider list unavailable');
    const jp = await rp.json();
    const slots = new Set((j.credentials || []).map((c) => c.name));
    const configured = (jp.providers || []).filter((p) => p && p.id && slots.has('llm.cloud.' + p.id));
    let accountless = false;
    // Include the wallet route even when another provider also has a saved key.
    try {
      const rc = await fetch('/api/connections');
      const jc = await rc.json();
      accountless = rc.ok && (jc.connections || []).some((c) => c && c.id === 'cloud' && c.mode === 'accountless_x402');
    } catch (e) { accountless = false; }
    if (revision !== cloudCredentialsRevision) return;
    if (accountless && !configured.some((p) => p.id === 'usepod')) {
      const walletProvider = (jp.providers || []).find((p) => p.id === 'usepod');
      if (walletProvider) configured.push(walletProvider);
    }
    cloudProviderRows = configured;
    if (!configured.some((p) => p.id === cloudBrowseProvider)) {
      const pinned = configured.find((p) => p.id !== 'openrouter' && modelValue.startsWith(p.id + ':'));
      const preferred = pinned || configured.find((p) => p.id === 'openrouter') || configured[0];
      cloudBrowseProvider = preferred ? preferred.id : '';
    }
    setCloudConnected(configured.length > 0);
  } catch (e) { /* leave current state */ }
}


// ---- Report a problem (privacy-safe bug reporter) --------------------------------
// Everything the browser sees here is ALREADY sanitized server-side: candidates are
// derived from the runtime's own event records with redaction applied before serving,
// and the preview shown below is the exact outbound payload. Nothing is submitted
// until the user approves the exact preview hash and presses submit. No diagnostics
// are ever written to any browser log.
const bugReportOverlay = document.getElementById('bugReportOverlay');
const bugReportBody = document.getElementById('bugReportBody');
const brFlow = {
  step: 'pick', candidates: [], selected: -1, form: null,
  reportId: null, fingerprint: '', redaction: null, destination: '',
  removedFields: [], removedAttachments: [], attachments: [],
  preview: null, approved: false, submitting: false,
};

function openBugReport(prefill) {
  if (!bugReportOverlay) return;
  bugReportOverlay.hidden = false;
  brFlow.step = 'pick'; brFlow.reportId = null; brFlow.preview = null; brFlow.approved = false;
  brFlow.removedFields = []; brFlow.removedAttachments = [];
  loadBugReportCandidates(prefill && prefill.turnId ? prefill.turnId : '');
}
function closeBugReport() { if (bugReportOverlay) bugReportOverlay.hidden = true; }
function reportProblemForFailedRun(run) { openBugReport({ turnId: run && run.turnId ? run.turnId : '' }); }

async function brPost(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) {
    // Plain words first: the diagnostic envelope's message when the server sent one,
    // technical detail kept for the expandable section.
    const diag = data && data.diagnostic;
    const err = new Error((diag && diag.message) || data.error || data.detail || ('HTTP ' + res.status));
    err.diagnostic = diag || null;
    err.httpStatus = res.status;
    throw err;
  }
  return data;
}

// The configured default destination, so the form prefills with WHERE a report goes and
// the owner can see (and change) it before any draft is built. Server-owned: the page
// never invents a destination.
let brDefaultDestination = '';
async function brLoadDefaultDestination() {
  try {
    const res = await fetch('/api/bug-report/destination');
    const data = await res.json();
    if (res.ok && data.ok && data.destination) brDefaultDestination = data.destination;
  } catch (e) { /* the field stays empty and the server applies its own default */ }
}

async function loadBugReportCandidates(turnId) {
  bugReportBody.innerHTML = '<p class="br-status">Looking for recent failed turns&hellip;</p>';
  await brLoadDefaultDestination();
  try {
    const res = await fetch('/api/bug-report/candidates?session=' + encodeURIComponent(displayedChat || ''));
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'candidates unavailable');
    brFlow.candidates = data.candidates || [];
  } catch (e) {
    bugReportBody.innerHTML = '<p class="br-status br-bad">Could not load failed turns: ' + esc(e.message) + '</p>';
    return;
  }
  let start = -1;
  if (turnId) start = brFlow.candidates.findIndex((c) => c.client_turn_id === turnId);
  renderBugReportPick(start === -1 ? (brFlow.candidates.length === 1 ? 0 : -1) : start);
}

function renderBugReportPick(selectedIdx) {
  const f = brFlow.form || { title: '', destination: brDefaultDestination || '', category: 'failure', expected: '', actual: '', repro: '', includeError: true, includeLogs: true, includeComponents: true };
  let candHtml = '';
  if (!brFlow.candidates.length) {
    candHtml = '<p class="br-status">No failed turns recorded for this chat. You can still describe the problem manually.</p>';
  } else {
    candHtml = '<div class="br-field"><label>Affected turn</label>' + brFlow.candidates.map((c, i) =>
      '<div class="br-cand' + (i === selectedIdx ? ' on' : '') + '" data-br-idx="' + i + '" role="radio" aria-checked="' + (i === selectedIdx) + '" tabindex="0">' +
      '<b>' + esc((c.failure_types || []).join(', ') || 'failure') + '</b>' +
      '<small>' + esc(c.ts || '') + ' &middot; ' + esc(c.client_turn_id ? String(c.client_turn_id).slice(0, 8) : '') + '</small>' +
      '<small>' + esc(c.summary || '') + '</small></div>').join('') + '</div>';
  }
  bugReportBody.innerHTML =
    '<div id="brTurnList">' + candHtml + '</div>' +
    (brFlow.candidates.length ? (
      '<div class="br-field"><label>Include diagnostics (already sanitized server-side)</label>' +
      '<label class="set-row"><input type="checkbox" id="brIncludeError"' + (f.includeError ? ' checked' : '') + '> Error / stack</label>' +
      '<label class="set-row"><input type="checkbox" id="brIncludeLogs"' + (f.includeLogs ? ' checked' : '') + '> Runtime event log for the turn</label>' +
      '<label class="set-row"><input type="checkbox" id="brIncludeComponents"' + (f.includeComponents ? ' checked' : '') + '> Lanes / tools / models involved</label>' +
      '</div>') : '') +
    '<div class="br-grid">' +
    '<div class="br-field"><label>Short title</label><input id="brTitle" type="text" value="' + esc(f.title) + '" placeholder="daemon crashes on second tool call"></div>' +
    '<div class="br-field"><label>Destination repository (owner/name) &mdash; where the report goes if you send it</label><input id="brDestination" type="text" value="' + esc(f.destination) + '" placeholder="owner/repo"></div>' +
    '</div>' +
    '<div class="br-field"><label>Category</label><input id="brCategory" type="text" value="' + esc(f.category) + '" placeholder="crash"></div>' +
    '<div class="br-field"><label>Expected behaviour</label><textarea id="brExpected">' + esc(f.expected) + '</textarea></div>' +
    '<div class="br-field"><label>Actual behaviour</label><textarea id="brActual">' + esc(f.actual) + '</textarea></div>' +
    '<div class="br-field"><label>Minimal reproduction &mdash; one step per line</label><textarea id="brRepro">' + esc(f.repro) + '</textarea></div>' +
    '<div class="br-actions"><button type="button" id="brCreateDraft" class="set-btn primary">Create sanitized local draft</button>' +
    '<span class="br-status">Nothing is sent yet &mdash; a local draft is created first.</span></div>';
  brFlow.selected = selectedIdx;
  bugReportBody.querySelectorAll('.br-cand').forEach((el) => {
    const pick = () => { bugReportBody.querySelectorAll('.br-cand').forEach((x) => x.classList.remove('on')); el.classList.add('on'); brFlow.selected = Number(el.dataset.brIdx); };
    el.addEventListener('click', pick);
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
  });
  document.getElementById('brCreateDraft').addEventListener('click', brCreateDraft);
}

function brCollectForm() {
  const repro = (document.getElementById('brRepro') ? document.getElementById('brRepro').value : '') || '';
  brFlow.form = {
    title: (document.getElementById('brTitle') || {}).value || '',
    destination: (document.getElementById('brDestination') || {}).value || '',
    category: (document.getElementById('brCategory') || {}).value || '',
    expected: (document.getElementById('brExpected') || {}).value || '',
    actual: (document.getElementById('brActual') || {}).value || '',
    repro,
    includeError: brFlow.candidates.length ? document.getElementById('brIncludeError').checked : false,
    includeLogs: brFlow.candidates.length ? document.getElementById('brIncludeLogs').checked : false,
    includeComponents: brFlow.candidates.length ? document.getElementById('brIncludeComponents').checked : false,
  };
  return brFlow.form;
}

async function brCreateDraft() {
  const f = brCollectForm();
  const cand = brFlow.selected >= 0 ? brFlow.candidates[brFlow.selected] : null;
  const body = {
    expected: f.expected, actual: f.actual,
    repro_steps: f.repro.split('\n').map((s) => s.trim()).filter(Boolean),
    category: f.category, destination_repo: f.destination.trim(), title: f.title,
  };
  if (cand) {
    if (f.includeError) body.error_text = cand.error_text || '';
    if (f.includeComponents) {
      body.lanes = cand.lanes || []; body.tools = cand.tools || []; body.models = cand.models || [];
    }
    if (f.includeLogs && (cand.log_lines || []).length) body.log_sources = [{ name: 'runtime-events.log', lines: cand.log_lines }];
  }
  bugReportBody.innerHTML = '<p class="br-status">Building the sanitized local draft&hellip;</p>';
  try {
    const data = await brPost('/api/bug-report/draft', body);
    brFlow.reportId = data.report_id; brFlow.fingerprint = data.fingerprint || '';
    brFlow.redaction = (data.draft && data.draft.redaction_summary) || null;
    // The SERVER owns the destination: an empty field means the configured default was
    // applied, so the review shows the destination that consent will actually bind.
    brFlow.destination = (data.draft && data.draft.destination_repo) || f.destination.trim();
    brFlow.form = Object.assign({}, brFlow.form, { destination: brFlow.destination });
    await renderBugReportReview();
  } catch (e) {
    bugReportBody.innerHTML = '<p class="br-status br-bad">Draft failed: ' + esc(e.message) + '</p><div class="br-actions"><button type="button" class="set-btn" id="brBackPick">Back</button></div>';
    document.getElementById('brBackPick').addEventListener('click', () => renderBugReportPick(brFlow.selected));
  }
}

async function brRefreshPreview() {
  const data = await brPost('/api/bug-report/preview', {
    report_id: brFlow.reportId,
    remove_fields: brFlow.removedFields,
    remove_attachments: brFlow.removedAttachments,
  });
  brFlow.preview = data;
  return data;
}

async function renderBugReportReview() {
  bugReportBody.innerHTML = '<p class="br-status">Rendering the exact outbound preview&hellip;</p>';
  try { await brRefreshPreview(); } catch (e) {
    bugReportBody.innerHTML = '<p class="br-status br-bad">Preview failed: ' + esc(e.message) + '</p>';
    return;
  }
  const p = brFlow.preview;
  const counts = brFlow.redaction && brFlow.redaction.rule_counts ? brFlow.redaction.rule_counts : {};
  const countHtml = Object.keys(counts).length
    ? Object.keys(counts).map((k) => '<span class="br-chip">' + esc(k) + ' &times; ' + esc(String(counts[k])) + '</span>').join('')
    : '<span class="br-chip">no redactions needed</span>';
  const attHtml = (p.attachments || []).length
    ? (p.attachments || []).map((a) => '<label class="set-row"><input type="checkbox" class="br-rm-att" value="' + esc(a.name) + '"' + (brFlow.removedAttachments.indexOf(a.name) >= 0 ? ' checked' : '') + '> Remove ' + esc(a.name) + ' (' + esc(String(a.size_bytes)) + ' bytes)</label>').join('')
    : '<p class="br-status">No attachments.</p>';
  const fieldOpts = ['logs', 'flags', 'repro', 'components', 'error'];
  const fieldHtml = fieldOpts.map((name) =>
    '<label class="set-row"><input type="checkbox" class="br-rm-field" value="' + name + '"' + (brFlow.removedFields.indexOf(name) >= 0 ? ' checked' : '') + '> Remove ' + name + '</label>').join('');
  bugReportBody.innerHTML =
    '<p class="br-status">Local draft <b>' + esc(brFlow.reportId) + '</b> &middot; fingerprint <span class="br-sha">' + esc(brFlow.fingerprint) + '</span></p>' +
    '<div class="br-field"><label>Redaction summary (what was removed while building the draft)</label>' + countHtml + '</div>' +
    '<div class="br-grid"><div class="br-field"><label>Destination repository</label><input id="brDestShow" type="text" readonly value="' + esc(brFlow.destination) + '"></div>' +
    '<div class="br-field"><label>Outbound size</label><input type="text" readonly value="' + esc(String(p.total_bytes)) + ' bytes"></div></div>' +
    '<div class="br-grid"><div class="br-field"><label>Remove fields before sending</label>' + fieldHtml + '</div>' +
    '<div class="br-field"><label>Remove attachments before sending</label>' + attHtml + '</div></div>' +
    '<div class="br-field"><label>Exact outbound preview &mdash; these are the precise bytes that would be sent</label>' +
    '<pre class="br-pre" id="brPreviewPre">' + esc('# ' + p.issue.title + '\n\n' + p.issue.body) + '</pre></div>' +
    '<p class="br-sha">payload sha256: ' + esc(p.payload_sha256) + '</p>' +
    '<p class="br-status" id="brState">' + (brFlow.approved ? 'Approved for these exact bytes.' : 'Not approved yet.') + '</p>' +
    '<div class="br-actions">' +
    '<button type="button" id="brEdit" class="set-btn">Edit details</button>' +
    '<button type="button" id="brApprove" class="set-btn"' + (brFlow.approved ? ' disabled' : '') + '>Approve these exact bytes</button>' +
    (brFlow.approved ? '<button type="button" id="brRevoke" class="set-btn">Withdraw approval</button>' : '') +
    '<button type="button" id="brSubmit" class="set-btn primary"' + (brFlow.approved && !brFlow.submitting ? '' : ' disabled') + '>Submit report to GitHub</button>' +
    '<button type="button" id="brExport" class="set-btn">Save a local copy instead</button>' +
    '</div>' +
    '<p class="br-status">Sending needs your explicit approval of these exact bytes. "Save a local copy" keeps the sanitized report on this machine &mdash; nothing is sent.</p>';
  bugReportBody.querySelectorAll('.br-rm-field').forEach((el) => el.addEventListener('change', brRemovalsChanged));
  bugReportBody.querySelectorAll('.br-rm-att').forEach((el) => el.addEventListener('change', brRemovalsChanged));
  document.getElementById('brEdit').addEventListener('click', brEditDraft);
  document.getElementById('brApprove').addEventListener('click', brApprove);
  if (document.getElementById('brRevoke')) document.getElementById('brRevoke').addEventListener('click', brRevoke);
  document.getElementById('brSubmit').addEventListener('click', brSubmit);
  document.getElementById('brExport').addEventListener('click', brExport);
}

async function brRevoke() {
  try {
    await brPost('/api/bug-report/revoke', { report_id: brFlow.reportId });
    brFlow.approved = false;
    toast('Approval withdrawn. Nothing can be sent until you approve again.');
    await renderBugReportReview();
  } catch (e) { toast('Withdraw failed: ' + e.message); }
}

async function brExport() {
  try {
    const data = await brPost('/api/bug-report/export', {
      report_id: brFlow.reportId,
      remove_fields: brFlow.removedFields,
      remove_attachments: brFlow.removedAttachments,
    });
    bugReportBody.innerHTML =
      '<p class="br-status br-ok"><b>Saved a sanitized copy on this machine.</b> Nothing was sent anywhere.</p>' +
      '<p class="br-issue">File: <span class="br-sha">' + esc(data.path) + '</span> &middot; ' + esc(String(data.total_bytes)) + ' bytes &middot; sha256 ' + esc(String(data.payload_sha256).slice(0, 16)) + '&hellip;</p>' +
      '<p class="br-status">You can still send this draft to GitHub later from the preview.</p>' +
      '<div class="br-actions"><button type="button" class="set-btn" id="brBackReview">Back to preview</button>' +
      '<button type="button" class="set-btn" id="brDoneX">Done</button></div>';
    document.getElementById('brBackReview').addEventListener('click', renderBugReportReview);
    document.getElementById('brDoneX').addEventListener('click', closeBugReport);
  } catch (e) {
    bugReportBody.innerHTML = '<p class="br-status br-bad">Local save failed: ' + esc(e.message) + '</p>' +
      '<div class="br-actions"><button type="button" class="set-btn" id="brBackReview">Back to preview</button></div>';
    document.getElementById('brBackReview').addEventListener('click', renderBugReportReview);
  }
}

async function brRemovalsChanged() {
  brFlow.removedFields = Array.from(bugReportBody.querySelectorAll('.br-rm-field:checked')).map((el) => el.value);
  brFlow.removedAttachments = Array.from(bugReportBody.querySelectorAll('.br-rm-att:checked')).map((el) => el.value);
  if (brFlow.approved) toast('Preview changed — approval reset. Approve the new bytes.');
  brFlow.approved = false;
  await renderBugReportReview();
}

async function brApprove() {
  if (!brFlow.preview) return;
  try {
    await brPost('/api/bug-report/approve', {
      report_id: brFlow.reportId,
      payload_sha256: brFlow.preview.payload_sha256,
      remove_fields: brFlow.removedFields,
      remove_attachments: brFlow.removedAttachments,
      confirm: true,
    });
    brFlow.approved = true;
    toast('Approved. You can submit now.');
    await renderBugReportReview();
  } catch (e) { toast('Approval failed: ' + e.message); }
}

function brEditDraft() {
  if (!brFlow.form) brFlow.form = { title: '', destination: brFlow.destination, category: '', expected: '', actual: '', repro: '', includeError: true, includeLogs: true, includeComponents: true };
  const f = brFlow.form;
  bugReportBody.innerHTML =
    '<p class="br-status">Editing draft <b>' + esc(brFlow.reportId) + '</b>. Saving clears the current approval &mdash; the new bytes must be approved again.</p>' +
    '<div class="br-field"><label>Expected behaviour</label><textarea id="brExpected">' + esc(f.expected) + '</textarea></div>' +
    '<div class="br-field"><label>Actual behaviour</label><textarea id="brActual">' + esc(f.actual) + '</textarea></div>' +
    '<div class="br-field"><label>Minimal reproduction &mdash; one step per line</label><textarea id="brRepro">' + esc(f.repro) + '</textarea></div>' +
    '<div class="br-actions"><button type="button" id="brUpdateSave" class="set-btn primary">Save changes</button>' +
    '<button type="button" id="brCancelEdit" class="set-btn">Cancel</button></div>';
  document.getElementById('brUpdateSave').addEventListener('click', brSaveEdit);
  document.getElementById('brCancelEdit').addEventListener('click', renderBugReportReview);
}

async function brSaveEdit() {
  const body = {
    report_id: brFlow.reportId,
    expected: document.getElementById('brExpected').value,
    actual: document.getElementById('brActual').value,
    repro_steps: document.getElementById('brRepro').value.split('\n').map((s) => s.trim()).filter(Boolean),
  };
  bugReportBody.innerHTML = '<p class="br-status">Saving edits&hellip;</p>';
  try {
    const data = await brPost('/api/bug-report/update', body);
    brFlow.approved = false;
    if (data.draft && data.draft.redaction_summary) brFlow.redaction = data.draft.redaction_summary;
    brFlow.form = Object.assign({}, brFlow.form, { expected: body.expected, actual: body.actual, repro: document.getElementById ? body.repro_steps.join('\n') : '' });
    toast('Edits saved — approval was reset.');
    await renderBugReportReview();
  } catch (e) {
    bugReportBody.innerHTML = '<p class="br-status br-bad">Edit failed: ' + esc(e.message) + '</p>';
  }
}

async function brSubmit() {
  if (!brFlow.approved || brFlow.submitting) return;
  brFlow.submitting = true;
  const stateEl = document.getElementById('brState');
  if (stateEl) { stateEl.textContent = 'Submitting…'; stateEl.className = 'br-status'; }
  const submitBtn = document.getElementById('brSubmit');
  if (submitBtn) submitBtn.disabled = true;
  try {
    const data = await brPost('/api/bug-report/submit', { report_id: brFlow.reportId });
    if (data.status === 'submitted') {
      bugReportBody.innerHTML =
        '<p class="br-status br-ok"><b>Submitted.</b> The exact approved bytes left the machine.</p>' +
        '<p class="br-issue" id="brIssueLink">GitHub issue: <a href="' + esc(data.issue_url) + '" target="_blank" rel="noreferrer noopener">' + esc(data.issue_url) + '</a></p>' +
        '<p class="br-status">A receipt with sizes and hashes (never content) is stored locally and listed under <span class="br-sha">vool bug-report receipts</span>.</p>' +
        '<div class="br-actions"><button type="button" class="set-btn" id="brDone">Done</button></div>';
      document.getElementById('brDone').addEventListener('click', closeBugReport);
    } else if (data.status === 'duplicate') {
      bugReportBody.innerHTML =
        '<p class="br-status">Already reported: this fingerprint has an issue.</p>' +
        '<p class="br-issue" id="brIssueLink"><a href="' + esc(data.duplicate_of || '') + '" target="_blank" rel="noreferrer noopener">' + esc(data.duplicate_of || '') + '</a></p>' +
        '<div class="br-actions"><button type="button" class="set-btn" id="brDone">Done</button></div>';
      document.getElementById('brDone').addEventListener('click', closeBugReport);
    } else {
      brRenderFailed({ message: data.detail || 'submission failed', diagnostic: data.diagnostic || null });
    }
  } catch (e) {
    brRenderFailed({ message: e.message, diagnostic: e.diagnostic || null });
  } finally {
    brFlow.submitting = false;
  }
}

function brRenderFailed(failure) {
  // Plain words first; the technical detail (code, hashes, upstream status) sits in an
  // expandable disclosure, not in the first sentence a worried user reads.
  const diag = failure.diagnostic || {};
  const plain = failure.message || 'submission failed';
  const techBits = [];
  if (diag.code) techBits.push('code: ' + diag.code);
  if (diag.upstream_status) techBits.push('upstream HTTP ' + diag.upstream_status);
  if (diag.correlation_id) techBits.push('correlation: ' + diag.correlation_id);
  if (diag.recovery) techBits.push('recovery: ' + diag.recovery);
  const techHtml = techBits.length
    ? '<details class="br-tech"><summary>Technical details</summary><pre class="br-pre">' + esc(techBits.join('\n')) + '</pre></details>'
    : '';
  const retryNote = diag.retryable === false
    ? 'Retrying will not help until ' + (diag.recovery ? 'the suggested recovery is done.' : 'the underlying problem is fixed.')
    : 'You can safely retry — your approval still binds the same bytes.';
  bugReportBody.innerHTML =
    '<p class="br-status br-bad"><b>Submission failed.</b> ' + esc(plain) + '</p>' + techHtml +
    '<p class="br-status">Your sanitized draft is still stored locally — nothing was lost. ' + esc(retryNote) + '</p>' +
    '<div class="br-actions"><button type="button" id="brRetry" class="set-btn primary">Retry submission</button>' +
    '<button type="button" id="brSaveLocal" class="set-btn">Save a local copy instead</button>' +
    '<button type="button" id="brBackReview" class="set-btn">Back to preview</button></div>';
  document.getElementById('brRetry').addEventListener('click', brSubmit);
  document.getElementById('brSaveLocal').addEventListener('click', brExport);
  document.getElementById('brBackReview').addEventListener('click', renderBugReportReview);
}

if (bugReportOverlay) {
  document.getElementById('bugReportClose').addEventListener('click', closeBugReport);
  bugReportOverlay.addEventListener('click', (e) => { if (e.target === bugReportOverlay) closeBugReport(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && bugReportOverlay && !bugReportOverlay.hidden) closeBugReport(); });
  const reportBtn = document.getElementById('reportBtn');
  if (reportBtn) reportBtn.addEventListener('click', () => openBugReport());
  const reportSettingsBtn = document.getElementById('reportSettingsBtn');
  if (reportSettingsBtn) reportSettingsBtn.addEventListener('click', () => { closeSettings(); openBugReport(); });
}

// Settings is its own surface now (/settings): a native window when VOOL is running as the
// desktop app, a tab in a browser. Both load the SAME page against the SAME runtime. The legacy
// in-chat panel below stays as the last resort for a context where neither can be opened, so no
// control is ever unreachable.
function openLegacySettingsPanel() { if (settingsOverlay) { settingsOverlay.hidden = false; hideKeySavedBanner(); loadPrefs(); loadProfile(); refreshCredentials(); renderKeys(); loadSearchProviders(); renderUsage(usageRange); loadProviderOptions(); syncProviderUi(); renderBuildInfo();  sbRenderExportPreview(); } }
function openSettings(section) {
  // Bound directly as a click listener in two places, so the argument may be the click event.
  if (typeof section !== 'string') section = '';
  const frag = section ? ('#' + section) : '';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_settings) {
      window.pywebview.api.open_settings(section || '');   // reuses the one Settings window
      return;
    }
  } catch (e) { /* fall through to the browser path */ }
  let opened = null;
  try { opened = window.open('/settings' + frag, 'vool-settings'); } catch (e) { opened = null; }
  if (opened) {
    // A reused named window keeps its old URL, so re-point it when a section was asked for.
    try { if (frag) opened.location.hash = section; } catch (e) {}
    try { opened.focus(); } catch (e) {}
    return;
  }
  // Popup blocked and no native bridge: the SAME /settings page, framed inside this document. The
  // chat, its session and its unsent draft stay exactly where they are, and the legacy in-chat
  // panel below is never shown from any entry point -- its controls are the ones the census found
  // misleading (a "Test" that makes no request, messages that always name OpenRouter).
  openSettingsInFrame(section);
}
function openSettingsInFrame(section) { openSurfaceInFrame('/settings' + (section ? ('#' + section) : ''), 'VOOL Settings'); }
// The SAME frame overlay carries any second surface of this runtime (Settings, guided setup): one
// overlay, one close path (Back / Escape / click outside), the chat and its draft untouched.
function openSurfaceInFrame(url, label) {
  if (!settingsFrameOverlay || !settingsFrame) return;
  if (settingsFrame.getAttribute('src') !== url) settingsFrame.setAttribute('src', url);
  settingsFrame.title = label || 'VOOL Settings';
  const dialog = settingsFrameOverlay.querySelector('[role="dialog"]');
  if (dialog) dialog.setAttribute('aria-label', label || 'VOOL Settings');
  settingsFrameOverlay.hidden = false;
  try { settingsFrame.focus(); } catch (e) {}
}
// Guided setup (/setup) opens exactly the way Settings does: the native bridge when the shell has
// one, else a named window, else the frame overlay above. Nothing modal traps the user.
function openSetup(step) {
  if (typeof step !== 'string') step = '';
  const frag = step ? ('#step=' + step) : '';
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_setup) {
      window.pywebview.api.open_setup(step || '');
      return;
    }
  } catch (e) { /* fall through to the browser path */ }
  let opened = null;
  try { opened = window.open('/setup' + frag, 'vool-setup'); } catch (e) { opened = null; }
  if (opened) {
    try { if (frag) opened.location.hash = 'step=' + step; } catch (e) {}
    try { opened.focus(); } catch (e) {}
    return;
  }
  openSurfaceInFrame('/setup' + frag, 'VOOL Setup');
}
function closeSettingsFrame() {
  if (!settingsFrameOverlay || settingsFrameOverlay.hidden) return;
  settingsFrameOverlay.hidden = true;
  const composer = document.getElementById('input'); if (composer) { try { composer.focus(); } catch (e) {} }
}
// The framed Settings page asks its host to close it: its own "Back" can neither close a frame nor
// be allowed to navigate the chat away. Same origin only.
window.addEventListener('message', async (e) => {
  if (e.origin !== window.location.origin) return;
  const d = e.data;
  if (!d || typeof d !== 'object') return;
  if (d.type === 'vool-settings-close' || d.type === 'vool-setup-close') { closeSettingsFrame(); setupLineLoad(); return; }
  // The framed setup page asks its host to open a Settings section (it cannot open windows itself).
  if (d.type === 'vool-setup-open-settings') { closeSettingsFrame(); openSettings(typeof d.section === 'string' ? d.section : ''); return; }
  // ...and to run the native folder picker, which only the host document's bridge can reach.
  if (d.type === 'vool-setup-pick-folder') {
    let path = null;
    try { path = await nativePickFolder(); } catch (err) { path = null; }
    try { if (e.source) e.source.postMessage({ type: 'vool-setup-folder', path: path || '' }, window.location.origin); } catch (err) {}
  }
});
async function loadPrefs() {
  let p = {};
  try { const r = await fetch('/api/settings/prefs'); p = await r.json(); } catch (e) { return; }
  const set = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
  set('setHumor', p.humor_percent != null ? p.humor_percent : 20);
  set('setCommStyle', p.communication_style || 'casual');
  set('setAutonomy', p.autonomy_mode || 'hands_off');
  const dr = document.getElementById('setDeepReason'); if (dr) dr.checked = !!p.deep_reasoning;
  set('setReserve', p.ram_reserve_pct != null ? p.ram_reserve_pct : 20);
  set('setTokenBudget', p.daily_token_budget != null ? p.daily_token_budget : 0);
  const hv = document.getElementById('setHumorVal'); if (hv) hv.textContent = (p.humor_percent != null ? p.humor_percent : 20) + '%';
  const rv = document.getElementById('setReserveVal'); if (rv) rv.textContent = (p.ram_reserve_pct != null ? p.ram_reserve_pct : 20) + '%';
}
async function savePrefs() {
  const num = (id, d) => { const el = document.getElementById(id); const n = parseInt(el && el.value, 10); return Number.isFinite(n) ? n : d; };
  const val = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
  const payload = {
    humor_percent: num('setHumor', 20),
    communication_style: val('setCommStyle') || 'casual',
    autonomy_mode: val('setAutonomy') || 'hands_off',
    deep_reasoning: !!(document.getElementById('setDeepReason') || {}).checked,
    ram_reserve_pct: num('setReserve', 20),
    daily_token_budget: Math.max(0, num('setTokenBudget', 0)),
  };
  const status = document.getElementById('setPrefsStatus');
  try {
    const r = await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (status) { status.textContent = r.ok ? 'Saved.' : 'Could not save.'; status.style.color = r.ok ? '' : 'var(--err)'; setTimeout(() => { status.textContent = ''; }, 2000); }
  } catch (e) { if (status) status.textContent = 'Could not save.'; }
}

// ---- Operator Profile (P1): what VOOL remembers about you ----
// One authority behind every control here (core.operator_profile, via /api/profile/*). The chat
// chip, the confirmation line and the folded "Used N preferences" indicator are rendered from the
// server's vool_profile frame; nothing is inferred client-side and nothing here grants permission.
async function profilePost(action, body) {
  const r = await fetch('/api/profile/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let d = {}; try { d = await r.json(); } catch (e) { d = {}; }
  return { ok: r.ok && d.ok !== false, status: r.status, data: d };
}
function profileStatus(text, isErr) {
  const el = document.getElementById('profileStatus'); if (!el) return;
  el.textContent = text || ''; el.style.color = isErr ? 'var(--err)' : '';
  if (text) setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, 3500);
}
function renderProfileChip(host, cand) {
  const chip = document.createElement('div'); chip.className = 'pchip'; chip.dataset.candidateId = String(cand.candidate_id || '');
  const text = document.createElement('span'); text.className = 'pchip-text'; text.textContent = String(cand.text || ''); chip.appendChild(text);
  const actions = Array.isArray(cand.actions) && cand.actions.length ? cand.actions : ['save', 'edit', 'only_this_chat'];
  const labels = { save: 'Save', edit: 'Edit', only_this_chat: 'Only this chat', replace: 'Replace', scope: 'Keep both (work)', keep: 'Keep old' };
  const decide = async (action, value, scope) => {
    const route = (action === 'replace' || action === 'scope' || action === 'keep') ? 'resolve' : 'candidate';
    const body = { candidate_id: cand.candidate_id, action: action };
    if (value != null) body.value = value; if (scope) body.scope = scope;
    const res = await profilePost(route, body);
    const report = (res.data && res.data.change && res.data.change.report) || (res.ok ? 'Done.' : 'Could not save.');
    chip.innerHTML = ''; const done = document.createElement('span'); done.className = 'pchip-text'; done.textContent = report; chip.appendChild(done);
    if (!res.ok) chip.style.color = 'var(--err)';
  };
  for (const action of actions) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = labels[action] || action;
    b.addEventListener('click', () => {
      if (action === 'edit') {
        const input = document.createElement('input'); input.type = 'text'; input.value = String(cand.value || ''); input.maxLength = 200;
        const ok = document.createElement('button'); ok.type = 'button'; ok.textContent = 'Save edit';
        ok.addEventListener('click', () => decide('edit', input.value));
        chip.appendChild(input); chip.appendChild(ok); input.focus();
        return;
      }
      if (action === 'scope') { decide('scope', null, 'work'); return; }
      decide(action);
    });
    chip.appendChild(b);
  }
  host.appendChild(chip);
  return chip;
}
function renderProfileSaved(host, saved) {
  const el = document.createElement('div'); el.className = 'pconfirm';
  const text = document.createElement('span'); text.className = 'pconfirm-text';
  text.textContent = saved.map((s) => String(s.report || '')).filter(Boolean).join(' ');
  el.appendChild(text);
  const undoable = saved.filter((s) => s.item_id);
  if (undoable.length) {
    const undo = document.createElement('button'); undo.type = 'button'; undo.textContent = 'Undo';
    undo.addEventListener('click', async () => {
      const res = await profilePost('restore', { item_id: undoable[undoable.length - 1].item_id });
      text.textContent = (res.data && res.data.change && res.data.change.report) || (res.ok ? 'Restored.' : 'Could not undo.');
      undo.remove(); undo.disabled = true;
    });
    el.appendChild(undo);
  }
  host.appendChild(el);
  return el;
}
function renderProfileUsed(host, used) {
  const det = document.createElement('details'); det.className = 'pused'; det.open = false;
  const labels = used.map((u) => String(u.label || u.category || '')).filter(Boolean);
  const sum = document.createElement('summary');
  sum.textContent = 'Used ' + labels.length + ' preference' + (labels.length === 1 ? '' : 's') + ': ' + labels.join(', ');
  det.appendChild(sum);
  const ul = document.createElement('ul');
  for (const u of used) { const li = document.createElement('li'); li.textContent = String(u.label || u.category || '') + ' (' + String(u.scope || 'global') + ')'; ul.appendChild(li); }
  det.appendChild(ul);
  host.appendChild(det);
  return det;
}
function applyProfileFrame(run, frame) {
  if (!run || !frame || typeof frame !== 'object') return;
  run.profileFrame = frame;
  if (run.assistantMsgEl) renderProfileFrame(run);
}
function renderProfileFrame(run) {
  const frame = run && run.profileFrame; const host = run && run.assistantMsgEl;
  if (!frame || !host || host.dataset.profileRendered === '1') return;
  host.dataset.profileRendered = '1';
  if (Array.isArray(frame.saved) && frame.saved.length) renderProfileSaved(host, frame.saved);
  if (Array.isArray(frame.notices) && frame.notices.length) renderProfileSaved(host, frame.notices.map((n) => ({ report: n })));
  for (const cand of (frame.candidates || [])) renderProfileChip(host, cand);
  for (const conf of (frame.conflicts || [])) renderProfileChip(host, conf);
  if (Array.isArray(frame.used) && frame.used.length) renderProfileUsed(host, frame.used);
}
async function loadProfile() {
  const list = document.getElementById('profileList'); if (!list) return;
  let d = null;
  try { const r = await fetch('/api/profile?session=' + encodeURIComponent(displayedChat || '')); d = await r.json(); } catch (e) { d = null; }
  list.innerHTML = '';
  if (!d || !d.ok) { const e = document.createElement('div'); e.className = 'profile-empty'; e.textContent = 'Profile unavailable.'; list.appendChild(e); return; }
  const pause = document.getElementById('profilePause');
  if (pause) { pause.textContent = d.paused ? 'Resume memory' : 'Pause memory'; pause.onclick = async () => { await profilePost('pause', { paused: !d.paused }); loadProfile(); }; }
  const exp = document.getElementById('profileExport');
  if (exp) { exp.onclick = async () => { try { const r = await fetch('/api/profile/export'); const j = await r.json(); await navigator.clipboard.writeText(JSON.stringify(j, null, 2)); profileStatus('Export copied to clipboard.'); } catch (e) { profileStatus('Could not export.', true); } }; }
  const rows = [].concat(d.candidates || [], d.items || []);
  if (!rows.length) { const e = document.createElement('div'); e.className = 'profile-empty'; e.textContent = 'Nothing remembered yet. Tell VOOL in chat what to remember, or say "call me ..." and confirm the suggestion.'; list.appendChild(e); return; }
  for (const item of rows) list.appendChild(renderProfileItem(item, d));
}
function renderProfileItem(item, d) {
  const el = document.createElement('div'); el.className = 'profile-item' + (item.status === 'candidate' ? ' candidate' : '');
  const label = document.createElement('div'); label.className = 'pi-label'; label.textContent = (item.label || item.category) + ': ' + String(item.value_text || item.value || '');
  const scope = document.createElement('div'); scope.textContent = item.scope === 'chat' ? 'this chat only' : String(item.scope || 'global');
  const meta = document.createElement('div'); meta.className = 'pi-meta';
  meta.textContent = (item.status === 'candidate' ? 'suggested from chat · ' : '') + 'origin: ' + String(item.origin || '') + ' · last used: ' + (item.last_used_at ? String(item.last_used_at).slice(0, 16).replace('T', ' ') : 'never') + ' · rev ' + String(item.revision || 1);
  const actions = document.createElement('div'); actions.className = 'pi-actions';
  const mk = (text, fn) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.addEventListener('click', fn); actions.appendChild(b); };
  if (item.status === 'candidate') {
    for (const a of (item.actions || ['save', 'edit', 'only_this_chat'])) {
      const route = (a === 'replace' || a === 'scope' || a === 'keep') ? 'resolve' : 'candidate';
      mk({ save: 'Save', edit: 'Edit', only_this_chat: 'Only this chat', replace: 'Replace', scope: 'Keep both (work)', keep: 'Keep old' }[a] || a, async () => {
        let value = null; if (a === 'edit') { value = await requestTextInput('New value', String(item.value_text || '')); if (value == null) return; }
        const res = await profilePost(route, { candidate_id: item.item_id, action: a, value: value, scope: a === 'scope' ? 'work' : undefined });
        profileStatus((res.data && res.data.change && res.data.change.report) || (res.ok ? 'Done.' : 'Could not save.'), !res.ok); loadProfile();
      });
    }
  } else {
    mk('Edit', async () => {
      const value = await requestTextInput('New value for ' + (item.label || item.category), String(item.value_text || '')); if (value == null) return;
      const res = await profilePost('item', { item_id: item.item_id, value: value, expected_revision: item.revision });
      profileStatus(res.status === 409 ? 'Changed elsewhere — reloaded.' : ((res.data && res.data.change && res.data.change.report) || (res.ok ? 'Saved.' : 'Could not save.')), !res.ok); loadProfile();
    });
    mk('Forget', async () => { const res = await profilePost('forget', { item_id: item.item_id, expected_revision: item.revision }); profileStatus((res.data && res.data.change && res.data.change.report) || 'Forgotten.', !res.ok); loadProfile(); });
    mk('Move scope', async () => {
      const scopes = (d && d.scopes) || ['global', 'work', 'personal', 'chat'];
      const target = await requestTextInput('Scope (' + scopes.join(' / ') + ')', item.scope || 'global'); if (!target) return;
      const res = await profilePost('scope', { item_id: item.item_id, scope: target, scope_key: target === 'chat' ? (displayedChat || '') : '', expected_revision: item.revision });
      profileStatus((res.data && res.data.change && res.data.change.report) || (res.ok ? 'Moved.' : 'Could not move.'), !res.ok); loadProfile();
    });
    mk('Restore previous', async () => { const res = await profilePost('restore', { item_id: item.item_id }); profileStatus((res.data && res.data.change && res.data.change.report) || (res.ok ? 'Restored.' : 'Nothing to restore.'), !res.ok); loadProfile(); });
  }
  el.appendChild(label); el.appendChild(scope); el.appendChild(meta); el.appendChild(actions);
  return el;
}

// ---- Keys panel: every stored key, a per-key Test (green/red), and a Diagnose that explains the error ----
function _explainCloudError(state, http, detail) {
  // Localized explanation keyed by the HTTP status CODE (a stable fact); the provider's
  // own diagnostic `detail` is appended verbatim — sanitized authority, never translated.
  const code = Number(http || 0);
  const map = {
    401: pageT('cloud.err_401', 'Key rejected (401 Unauthorized): the key is wrong, expired, or revoked. Re-copy it from the provider dashboard and save it again.'),
    402: pageT('cloud.err_402', 'Payment required (402): the account needs billing set up or has run out of credit for this provider.'),
    403: pageT('cloud.err_403', 'Forbidden (403): the key lacks permission — the account is restricted, or the model/region is blocked for this key.'),
    404: pageT('cloud.err_404', 'Not found (404): the model or endpoint does not exist for this provider/key (often a wrong model id or base URL).'),
    429: pageT('cloud.err_429', 'Rate limited (429): too many requests, or over your plan quota. Wait a bit and retry, or check your provider billing/limits.'),
    500: pageT('cloud.err_500', 'Provider error (500): the provider had an internal error. Usually temporary — retry shortly.'),
    502: pageT('cloud.err_502', 'Bad gateway (502): the provider is having trouble upstream. Temporary — retry shortly.'),
    503: pageT('cloud.err_503', 'Service unavailable (503): the provider is down or overloaded. Temporary — retry shortly.'),
    408: pageT('cloud.err_408', 'Timeout (408): the provider took too long to respond. Check your connection and retry.'),
  };
  if (map[code]) return map[code];
  if (code >= 500) return pageTF('cloud.err_5xx', 'Provider error ({code}): likely temporary — retry shortly. ', { code: code }) + (detail || '');
  if (code >= 400) return pageTF('cloud.err_4xx', 'Request rejected ({code}). ', { code: code }) + (detail || pageT('cloud.check_key', 'Check the key and provider.'));
  if (state === 'failed') return pageT('cloud.failed', 'Could not reach the provider — check your internet connection, the key, or the custom base URL. ') + (detail || '');
  return detail || pageT('cloud.unknown', 'Unknown issue — try Test again.');
}
async function renderKeys() {
  const box = document.getElementById('keysList'); if (!box) return;
  box.innerHTML = '';
  let keys = [];
  try { keys = (await (await fetch('/api/cloud/keys')).json()).keys || []; } catch (e) {}
  if (!keys.length) { return; }   // nothing stored yet -> the input above is the whole story
  const title = document.createElement('div'); title.className = 'keys-title'; title.textContent = 'Your keys'; box.appendChild(title);
  keys.forEach((k) => {
    const row = document.createElement('div'); row.className = 'key-row';
    const dot = document.createElement('span'); dot.className = 'key-dot';
    const lbl = document.createElement('span'); lbl.className = 'key-label'; lbl.textContent = k.label;
    const test = document.createElement('button'); test.type = 'button'; test.className = 'set-btn key-test'; test.textContent = 'Test';
    const diag = document.createElement('button'); diag.type = 'button'; diag.className = 'set-btn key-diag'; diag.textContent = 'Diagnose'; diag.hidden = true;
    const note = document.createElement('div'); note.className = 'key-note'; note.hidden = true;
    test.addEventListener('click', async () => {
      test.disabled = true; test.textContent = 'Testing…'; dot.className = 'key-dot'; diag.hidden = true; note.hidden = true;
      let res = {};
      try {
        if (k.kind === 'fal') { res = { state: 'ok' }; }   // fal has no cheap auth probe; a stored key is "added"
        else { res = await (await fetch('/api/cloud/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: k.provider }) })).json(); }
      } catch (e) { res = { state: 'failed', detail: 'request failed' }; }
      test.disabled = false; test.textContent = 'Test';
      const ok = res.state === 'ok';
      dot.className = 'key-dot ' + (ok ? 'ok' : 'bad');
      if (!ok) {
        diag.hidden = false; diag._payload = res;
      }
    });
    diag.addEventListener('click', () => {
      const p = diag._payload || {};
      const text = _explainCloudError(p.state, p.http_status, p.detail);
      note.hidden = false;
      note.innerHTML = '';
      const t = document.createElement('span'); t.textContent = text; note.appendChild(t);
      const cp = document.createElement('button'); cp.type = 'button'; cp.className = 'set-btn key-copy'; cp.textContent = 'Copy';
      cp.addEventListener('click', () => { try { navigator.clipboard.writeText(k.label + ': ' + text); cp.textContent = 'Copied'; setTimeout(() => { cp.textContent = 'Copy'; }, 1500); } catch (e) {} });
      note.appendChild(cp);
    });
    row.appendChild(dot); row.appendChild(lbl); row.appendChild(test); row.appendChild(diag);
    box.appendChild(row); box.appendChild(note);
  });
}
// ---- Web search keys (BYOK). Same shape as the cloud-key block above, one endpoint family apart.
// The provider list is fetched, never hardcoded here: core/search_providers.py is the only table,
// so adding a provider there lights it up in this panel with no JS edit.
let _wsProviders = [];
function _wsEl(id) { return document.getElementById(id); }
function _wsSetStatus(text, bad) {
  const el = _wsEl('wsStatus'); if (!el) return;
  el.textContent = text || '';
  el.style.color = bad ? 'var(--bad, #c0392b)' : '';
}
async function loadSearchProviders() {
  const sel = _wsEl('wsProvider'); if (!sel) return;
  try { _wsProviders = (await (await fetch('/api/search/providers')).json()).providers || []; } catch (e) { _wsProviders = []; }
  const keep = sel.value;
  sel.innerHTML = '';
  const auto = document.createElement('option'); auto.value = ''; auto.textContent = 'Auto-detect'; sel.appendChild(auto);
  _wsProviders.forEach((p) => {
    const o = document.createElement('option');
    o.value = p.provider;
    o.textContent = p.label + (p.connected ? ' ✓' : '');
    sel.appendChild(o);
  });
  if (keep) sel.value = keep;
  const hint = _wsEl('wsSignup');
  if (hint) {
    const none = _wsProviders.filter((p) => !p.connected && p.signup_url);
    hint.innerHTML = '';
    none.slice(0, 4).forEach((p, i) => {
      if (i) hint.appendChild(document.createTextNode(' · '));
      const a = document.createElement('a'); a.href = p.signup_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = p.label + (p.free_tier ? ' (' + p.free_tier + ')' : '');
      hint.appendChild(a);
    });
  }
  renderSearchKeys();
}
async function renderSearchKeys() {
  const box = _wsEl('wsList'); if (!box) return;
  box.innerHTML = '';
  const connected = _wsProviders.filter((p) => p.connected);
  const remove = _wsEl('wsRemove'); const test = _wsEl('wsTest');
  if (remove) remove.hidden = !connected.length;
  if (test) test.hidden = !connected.length;
  if (!connected.length) return;
  const title = document.createElement('div'); title.className = 'keys-title'; title.textContent = 'Your search keys'; box.appendChild(title);
  connected.forEach((p) => {
    const row = document.createElement('div'); row.className = 'key-row';
    const dot = document.createElement('span'); dot.className = 'key-dot';
    const lbl = document.createElement('span'); lbl.className = 'key-label'; lbl.textContent = p.label;
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'set-btn key-test'; btn.textContent = 'Test';
    const note = document.createElement('div'); note.className = 'key-note'; note.hidden = true;
    btn.addEventListener('click', async () => {
      btn.disabled = true; btn.textContent = 'Testing…'; dot.className = 'key-dot'; note.hidden = true;
      let res = {};
      try {
        res = await (await fetch('/api/search/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p.provider }) })).json();
      } catch (e) { res = { state: 'failed', detail: 'request failed' }; }
      btn.disabled = false; btn.textContent = 'Test';
      const ok = res.state === 'ok';
      dot.className = 'key-dot ' + (ok ? 'ok' : 'bad');
      note.hidden = false;
      note.textContent = ok
        ? _searchTestSuccess(res)
        : _explainSearchError(res.state, res.detail);
    });
    const del = document.createElement('button'); del.type = 'button'; del.className = 'set-btn'; del.textContent = 'Remove';
    del.addEventListener('click', async () => {
      del.disabled = true;
      try {
        await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p.provider, delete: true }) });
      } catch (e) {}
      del.disabled = false;
      _wsSetStatus(p.label + ' key removed. Built-in search still works.', false);
      loadSearchProviders();
    });
    row.appendChild(dot); row.appendChild(lbl); row.appendChild(btn); row.appendChild(del);
    box.appendChild(row); box.appendChild(note);
  });
}
// "Brave Search - 3 sources, using your key." A green Test used to say only
// "Live search worked", which proves nothing about WHICH provider ran: the
// chain falls back to keyless scrapers, so a tick could mean the key worked or
// that something else answered instead of it.
function _searchTestSuccess(res) {
  const label = String(res.label || res.provider || 'The provider').trim();
  const count = Number(res.source_count || 0);
  const sources = count > 0 ? (' \u00b7 ' + count + (count === 1 ? ' source' : ' sources')) : '';
  const credential = String(res.keyed_or_keyless || '') === 'keyed'
    ? ', using your key'
    : ', without a key';
  return label + sources + credential + '.';
}
function _explainSearchError(state, detail) {
  // The provider's own state string (`state`) is the stable key; `detail` stays verbatim.
  if (state === 'unauthorized') return pageT('search_err.unauthorized', 'That provider rejected the key. Check you copied the whole key, and that it is for this provider.');
  if (state === 'rate_limited') return pageT('search_err.rate_limited', 'The provider is rate-limiting this key right now. It should work again shortly.');
  if (state === 'quota_exhausted') return pageT('search_err.quota_exhausted', 'This key has no quota left on its plan.');
  if (state === 'unreachable') return pageT('search_err.unreachable', 'Could not reach the provider from this machine (offline, or the network blocked it).');
  if (state === 'no_key') return pageT('search_err.no_key', 'No key is stored for this provider yet.');
  // A REFUSAL is a policy verdict, not a verdict on the key. Reporting it as an
  // unusable search sent people to regenerate a credential that was never used
  // -- which is exactly what shipped, as "(RemoteFetchRefusedError)".
  if (state === 'refused') return pageT('search_err.refused', 'This runtime is not currently permitted to search the web') + (detail ? ' (' + detail + ')' : '') + '. ' + pageT('search_err.key_not_used', 'The key was not used.');
  return pageT('search_err.generic', 'The search did not come back usable') + (detail ? ' (' + detail + ')' : '') + '.';
}
async function saveSearchKey() {
  const keyEl = _wsEl('wsKey'); const sel = _wsEl('wsProvider');
  if (!keyEl) return;
  const value = (keyEl.value || '').trim();
  if (!value) { _wsSetStatus('Paste a key first.', true); return; }
  let provider = sel ? sel.value : '';
  if (!provider) {
    // Auto-detect: the server owns the prefix table, so the browser never guesses.
    try {
      const guess = await (await fetch('/api/search/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: value }) })).json();
      if (guess && guess.provider) provider = guess.provider;
      else {
        // Ambiguous or unknown: ASK rather than store under a guess, because a key in the wrong
        // slot fails auth forever with a key the user knows is good.
        _wsSetStatus('Could not tell which service that key is for — pick the provider from the list.', true);
        return;
      }
    } catch (e) { _wsSetStatus('Could not check that key. Pick the provider from the list.', true); return; }
  }
  const btn = _wsEl('wsSave'); if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  let out = {};
  try {
    out = await (await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: provider, value: value }) })).json();
  } catch (e) { out = { error: 'request failed' }; }
  if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  if (out && out.error) { _wsSetStatus(out.error, true); return; }
  keyEl.value = '';
  const banner = _wsEl('wsSavedBanner'); if (banner) { banner.classList.add('show'); setTimeout(() => banner.classList.remove('show'), 4000); }
  await loadSearchProviders();
  // Saving is presence, not proof. Run the real search probe now so the user learns immediately
  // whether the key actually works, instead of finding out from a bad answer later.
  _wsSetStatus('Key saved. Checking it with a live search…', false);
  let res = {};
  try {
    res = await (await fetch('/api/search/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: provider }) })).json();
  } catch (e) { res = { state: 'failed', detail: 'request failed' }; }
  if (res.state === 'ok') _wsSetStatus(_searchTestSuccess(res) + ' It will be used for live searches.', false);
  else _wsSetStatus(_explainSearchError(res.state, res.detail), true);
}
function closeSettings() { if (settingsOverlay) settingsOverlay.hidden = true; }
// ---------------------------------------------------------------------------
// Council control room. TRUTH LAW: this surface renders only real producers —
// the served runtime's version stamp (candidate identity), the typed
// review/verification state of real turns, and the council runtime's OWN run
// state read from /api/council/runs.
//
// This used to assert "There are no council worker processes in this build, so
// no seats are drawn". That is false: the council runtime is wired, with nine
// registry commands (convene, runs, status, events, lock, scorecard, seat, stop,
// resume) behind availability probes and their /api/council/* routes. Measured,
// council.runs answers {"ok": true, "runs": []} — the runtime is PRESENT and has
// simply not been convened. "No runtime" and "no run yet" are different facts and
// the panel now reports whichever is true, including the third case: the runtime
// did not answer.
// The council runtime's own state, read from it rather than asserted here.
// councilRuns: null = not asked yet or the runtime did not answer; [] = present
// with no run convened; [..] = real runs.
let councilRuns = null;
let councilReachable = null;

async function refreshCouncilRuns() {
  try {
    const r = await fetch('/api/council/runs');
    const d = await r.json();
    councilRuns = Array.isArray(d.runs) ? d.runs : [];
    councilReachable = true;
  } catch (e) {
    councilRuns = null;
    councilReachable = false;
  }
}

function councilStateLabel() {
  if (councilReachable === false) return 'COUNCIL RUNTIME DID NOT ANSWER';
  if (councilRuns === null) return 'COUNCIL STATE NOT READ YET';
  if (!councilRuns.length) return 'NO COUNCIL RUN CONVENED';
  return 'COUNCIL RUNS: ' + councilRuns.length;
}

function councilStateNote() {
  if (councilReachable === false) {
    return 'The council runtime is wired into this build but its run endpoint did not answer, '
      + 'so no run state is shown. That is a reachability fact, not evidence that no council exists.';
  }
  if (councilRuns === null) {
    return 'The council run state has not been read yet. What is already real here: the reviewer '
      + 'verdict recorded on this turn, the model provenance behind it, and the receipts of what ran.';
  }
  if (!councilRuns.length) {
    return 'The council runtime is present and has not been convened, so there is no session to draw. '
      + 'What IS real here: the reviewer verdict recorded on this turn, the model provenance behind it, '
      + 'and the receipts of what actually ran. Model output is not final truth — evidence, review and '
      + 'human promotion decide finality.';
  }
  return 'Council runs recorded by the runtime. Model output is not final truth — evidence, review and '
    + 'human promotion decide finality.';
}

const COUNCIL_LIFECYCLE = ['DRAFT', 'BUILDING', 'AWAITING REVIEW', 'UNDER REVIEW',
  'COUNTEREXAMPLE FOUND', 'FLAGGED', 'PASS', 'REJECTED', 'READY FOR HUMAN PROMOTION', 'FROZEN'];
const COUNCIL_EVIDENCE_CLASSES = [
  ['Writer candidate', 'the exact candidate SHA under review'],
  ['Independent reviewer', 'a reviewer that did not write the candidate'],
  ['Counterexample review', 'a reproduced failure beats any argument'],
  ['Mutation proof', 'RED → restore → GREEN on each invariant'],
  ['Verdict', 'PASS / REJECTED / FLAGGED, with the evidence path'],
];
function councilVerdictDot(state) {
  return state === 'passed' ? '#34d399' : state === 'running' ? 'var(--accent)'
    : (state === 'not_run' || state === '') ? '#9ca3af' : '#fbbf24';
}
function renderCouncilTab(body) {
  const run = view.run;
  const wide = panelScope !== 'chat';
  let html = '<div class="council-live"><div class="cl-state" id="councilLiveState">' + esc(councilStateLabel()) + '</div>'
    + '<div class="cl-note" id="councilLiveNote">' + esc(councilStateNote()) + '</div></div>';
  if (run) {
    const state = reviewStateForRun(run);
    const pres = reviewStatePresentation(state);
    html += '<div class="council-verdict"><span class="v-dot" style="background:' + councilVerdictDot(state) + '"></span>'
      + '<span class="v-name">' + esc(state === 'not_run' ? 'No reviewer verdict yet' : pres.label) + '</span>'
      + '<span class="v-sub">' + esc(state === 'not_run'
        ? (run.ended ? 'this turn ended without a reviewer verdict recorded' : 'reviewer has not reported on this turn') : pres.detail) + '</span></div>';
    const prov = modelIdentityDetails(run.model);
    html += '<div class="council-rows">';
    if (prov.length) prov.forEach((line) => { html += panelRow('run', '◆', line, ''); });
    else html += panelRow('run', '◆', 'Model identity', 'no model provenance recorded for this turn yet');
    if (run.cost && (run.cost.total_usd != null || run.cost.usd != null)) html += panelRow('run', '$', 'Recorded spend', fmtUsd(run.cost));
    html += '</div>';
    html += '<div class="xp-empty" style="margin-top:6px">Full evidence per turn: Receipts (what ran, hashes, signatures) and Event log (typed events, seq).</div>';
  } else if (!wide) {
    html += '<div class="xp-empty">Run a turn to accumulate real review evidence.</div>';
  } else {
    html += '<div class="xp-empty">Wider scope: per-turn reviewer verdicts live in each chat&rsquo;s Activity ledger.</div>';
  }
  html += '<h5 style="margin:12px 0 4px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Candidate lifecycle (council law)</h5>';
  html += '<div class="council-flow">' + COUNCIL_LIFECYCLE.map((s) => '<span>' + esc(s) + '</span>').join('') + '</div>';
  html += '<div class="council-authority"><b>Human authority:</b> promotion and freeze belong to the operator alone. '
    + 'A reviewer PASS on a turn is model-review evidence — never a promoted release, and never self-freezing.</div>';
  body.innerHTML = html;
}
const councilOverlay = document.getElementById('councilOverlay');
let _councilVersionCache = null;
function closeCouncil() { if (councilOverlay) councilOverlay.hidden = true; }
function councilCell(title, value, mono) {
  const none = value == null || value === '';
  return '<div class="council-cell"><h5>' + esc(title) + '</h5><div class="cv' + (mono ? ' mono' : '') + (none ? ' none' : '') + '">'
    + (none ? 'not provided by this build' : esc(String(value))) + '</div></div>';
}
async function openCouncil() {
  if (!councilOverlay) return;
  councilOverlay.hidden = false;
  const body = document.getElementById('councilBody');
  if (!body) return;
  let v = _councilVersionCache;
  if (!v) {
    try {
      const r = await fetch('/api/runtime/version', { cache: 'no-store' });
      if (r.ok) { v = await r.json(); _councilVersionCache = v; }
    } catch (e) { /* offline runtime: cells render their truthful empty state */ }
  }
  const commit = v && (v.commit || v.build_commit);
  const dirty = v ? (v.dirty === true || v.dirty === 'true' || v.dirty === 1) : null;
  let html = '<div class="council-live"><div class="cl-state">' + esc(councilStateLabel()) + '</div>'
    + '<div class="cl-note">No council worker producers are wired into this build — none are drawn. '
    + 'The control room shows the one candidate identity that IS real (the served runtime stamp) and the lawful '
    + 'lifecycle a real session must follow.</div></div>';
  html += '<div class="council-grid">';
  html += councilCell('Candidate SHA (served runtime)', commit, true);
  html += councilCell('Served page build commit', PAGE_BUILD_COMMIT, true);
  html += councilCell('Branch', v && v.branch, true);
  html += councilCell('Build ID', v && v.build_id, true);
  html += councilCell('Bundle ID', v && v.native_bundle_id, true);
  html += councilCell('Native host PID', v && v.native_host_pid, true);
  html += councilCell('Backend PID', v && v.pid, true);
  html += councilCell('API bind', v && v.api_bind, true);
  html += councilCell('Backend ownership', v && v.native_backend_owned === true ? 'OWNED BY NATIVE HOST' : '', false);
  html += councilCell('Runtime started', v && v.started_at);
  html += councilCell('Tree state', dirty == null ? '' : (dirty ? 'DIRTY — uncommitted changes present' : 'clean'));
  html += '</div>';
  html += '<h5 style="margin:10px 0 5px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Evidence a real session must carry</h5>';
  html += '<div class="council-grid">' + COUNCIL_EVIDENCE_CLASSES.map((c) =>
    '<div class="council-cell"><h5>' + esc(c[0]) + '</h5><div class="cv">' + esc(c[1]) + '</div></div>').join('') + '</div>';
  html += '<h5 style="margin:10px 0 4px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)">Lifecycle</h5>';
  html += '<div class="council-flow">' + COUNCIL_LIFECYCLE.map((s) => '<span>' + esc(s) + '</span>').join('') + '</div>';
  html += '<div class="council-authority"><b>Human promotion &amp; freeze authority:</b> the operator alone promotes or freezes a candidate. '
    + 'Nothing in this app self-freezes, and no animated seat is a worker. Per-turn reviewer verdicts live in the Activity panel&rsquo;s Council tab.</div>';
  body.innerHTML = html;
}
const pluginsOverlay = document.getElementById('pluginsOverlay');
let _pluginCatalog = null;
let _pluginMode = 'plugins';   // 'plugins' (powers) | 'skills' (how to use them) — separate panels
function closePlugins() { if (pluginsOverlay) pluginsOverlay.hidden = true; }
async function _openPanel(mode) {
  if (!pluginsOverlay) return;
  _pluginMode = mode;
  pluginsOverlay.hidden = false;
  const title = document.getElementById('pluginsTitle');
  const body = document.getElementById('pluginsBody');
  const search = document.getElementById('pluginsSearch');
  if (title) title.textContent = (mode === 'skills') ? 'Skills' : 'Plugins';
  if (search) { search.value = ''; search.placeholder = (mode === 'skills') ? 'Search skills…' : 'Search plugins…'; }
  if (body) body.textContent = 'Loading…';
  try { _pluginCatalog = await (await fetch('/api/plugins')).json(); renderPluginPanel(_pluginCatalog, ''); if (search) search.focus(); }
  catch (e) { if (body) body.textContent = 'Could not read the catalog.'; }
}
function openPlugins() { return _openPanel('plugins'); }
function openSkills() { return _openPanel('skills'); }
function renderPluginPanel(d, query) { if (_pluginMode === 'skills') { renderSkills(d, query); } else { renderPlugins(d, query); } }

function _firstPartyBadge() {
  const fp = document.createElement('span'); fp.className = 'p-firstparty'; fp.title = 'Built by Parad0x Labs';
  const mk = document.createElement('span'); mk.className = 'fp-mark'; fp.appendChild(mk);
  fp.appendChild(document.createTextNode('parad0x')); return fp;
}

// Plugin STORAGE state, verbatim from the server (`storage.state`: accessible / missing / denied /
// stalled / failed). Anything but accessible is shown with the server's own reason and one action:
// Rescan, the owner-local door that repeats the bounded folder probe and loads the packs when the
// folder answers. Nothing here disables a plugin or touches configuration.
function _storageNotice(d) {
  const st = (d && typeof d.storage === 'object' && d.storage) || null;
  if (!st || !st.state || st.state === 'accessible') return null;
  const wrap = document.createElement('div'); wrap.className = 'plugins-empty plugin-storage-notice';
  wrap.setAttribute('data-storage-state', String(st.state));
  const txt = document.createElement('div');
  txt.textContent = (d && d.reason) ? d.reason : ('Plugin storage is ' + st.state + '.');
  wrap.appendChild(txt);
  if (st.state !== 'missing') {
    const btn = document.createElement('button'); btn.className = 'p-toggle enable'; btn.textContent = 'Rescan';
    btn.title = 'Check the plugin folder again and load the plugins it holds';
    btn.addEventListener('click', () => rescanPlugins());
    wrap.appendChild(btn);
  }
  return wrap;
}

async function rescanPlugins() {
  try {
    const r = await fetch('/api/plugins/rescan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    await r.json();
  } catch (e) { /* the catalog re-read below shows whatever state the server holds */ }
  try {
    _pluginCatalog = await (await fetch('/api/plugins')).json();
    const s = document.getElementById('pluginsSearch');
    renderPluginPanel(_pluginCatalog, s ? s.value : '');
  } catch (e) { /* fail-soft */ }
}

function renderPlugins(d, query) {
  const body = document.getElementById('pluginsBody'); if (!body) return;
  body.innerHTML = '';
  if (!d || !d.installed) {
    const notice = _storageNotice(d);
    if (notice) { body.appendChild(notice); return; }
    const p = document.createElement('div'); p.className = 'plugins-empty';
    p.textContent = (d && d.reason) ? d.reason : 'No plugins installed yet.';
    body.appendChild(p); return;
  }
  const q = String(query || '').trim().toLowerCase();
  let plugins = d.plugins || [];
  if (q) plugins = plugins.filter((pl) => ((pl.name || '') + ' ' + (pl.id || '') + ' ' + (pl.description || '') + ' ' + (pl.category || '')).toLowerCase().indexOf(q) !== -1);
  const head = document.createElement('div'); head.className = 'plugins-empty'; head.style.marginBottom = '12px';
  head.textContent = q
    ? (plugins.length + ' plugin' + (plugins.length === 1 ? '' : 's') + ' match “' + query.trim() + '”')
    : (d.plugin_count + ' plugin' + (d.plugin_count === 1 ? '' : 's') + ' installed · ' + d.skill_count + ' skill' + (d.skill_count === 1 ? '' : 's') + ' (see the Skills panel)');
  body.appendChild(head);
  if (!plugins.length) { const p = document.createElement('div'); p.className = 'plugins-empty'; p.textContent = 'No plugins match that search.'; body.appendChild(p); return; }
  plugins.forEach((pl) => {
    const card = document.createElement('div'); card.className = 'plugin-card' + (pl.first_party ? ' firstparty' : '');
    const h = document.createElement('div'); h.className = 'p-head';
    const nm = document.createElement('span'); nm.className = 'p-name'; nm.textContent = pl.name || pl.id;
    const ver = document.createElement('span'); ver.className = 'p-ver'; ver.textContent = 'v' + (pl.version || '0');
    h.appendChild(nm); h.appendChild(ver);
    if (pl.first_party) h.appendChild(_firstPartyBadge());
    const cat = document.createElement('span'); cat.className = 'p-cat'; cat.textContent = pl.category || '';
    h.appendChild(cat); card.appendChild(h);
    if (pl.description) { const de = document.createElement('div'); de.className = 'p-desc'; de.textContent = pl.description; card.appendChild(de); }
    const act = document.createElement('div'); act.className = 'p-actions';
    const off = pl.enabled === false;
    const nSk = (pl.skills || []).length;
    const st = document.createElement('span'); st.className = 'p-status' + (off ? ' off' : '');
    st.textContent = (off ? 'Disabled' : 'Installed · Enabled') + (nSk ? (' · ' + nSk + ' skill' + (nSk === 1 ? '' : 's')) : '');
    const tg = document.createElement('button'); tg.className = 'p-toggle' + (off ? ' enable' : ''); tg.textContent = off ? 'Enable' : 'Disable';
    tg.addEventListener('click', () => togglePlugin(pl.id, off));
    act.appendChild(st); act.appendChild(tg); card.appendChild(act);
    body.appendChild(card);
  });
}

function renderSkills(d, query) {
  const body = document.getElementById('pluginsBody'); if (!body) return;
  body.innerHTML = '';
  // Media Studio lives here now (the header was too crowded): a built-in tool card that opens the same dedicated
  // editor window the header link used to open. It is offered whether or not any plugin is installed, and a search
  // that does not name it filters it out like any other card.
  const q0 = String(query || '').trim().toLowerCase();
  if (!q0 || 'media studio editor video audio image tool'.indexOf(q0) !== -1) {
    const card = document.createElement('div'); card.className = 'plugin-card firstparty media-studio-card';
    const h = document.createElement('div'); h.className = 'p-head';
    const nm = document.createElement('span'); nm.className = 'p-name'; nm.textContent = 'Media Studio'; h.appendChild(nm);
    const pill = document.createElement('span'); pill.className = 'p-cat'; pill.textContent = 'tool'; pill.title = 'Built into VOOL'; h.appendChild(pill);
    h.appendChild(_firstPartyBadge()); card.appendChild(h);
    const de = document.createElement('div'); de.className = 'p-desc'; de.textContent = 'Cut, trim and export media in a dedicated editor window.'; card.appendChild(de);
    const a = document.createElement('a'); a.className = 'media-studio-open'; a.href = '/media-editor'; a.target = '_blank'; a.rel = 'noopener';
    a.title = 'Open the Media Studio editor in a dedicated window'; a.textContent = 'Open Media Studio';
    card.appendChild(a); body.appendChild(card);
  }
  if (!d || !d.installed) {
    const notice = _storageNotice(d);
    if (notice) { body.appendChild(notice); return; }
    const p = document.createElement('div'); p.className = 'plugins-empty';
    p.textContent = (d && d.reason) ? d.reason : 'No skills installed yet.';
    body.appendChild(p); return;
  }
  const q = String(query || '').trim().toLowerCase();
  let skills = [];
  (d.plugins || []).forEach((pl) => (pl.skills || []).forEach((s) => skills.push({ name: s.name, description: s.description, plugin: pl.name || pl.id, first_party: pl.first_party })));
  if (q) skills = skills.filter((s) => ((s.name || '') + ' ' + (s.description || '') + ' ' + (s.plugin || '')).toLowerCase().indexOf(q) !== -1);
  skills.sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')));
  const head = document.createElement('div'); head.className = 'plugins-empty'; head.style.marginBottom = '12px';
  head.textContent = q
    ? (skills.length + ' skill' + (skills.length === 1 ? '' : 's') + ' match “' + query.trim() + '”')
    : (d.skill_count + ' skill' + (d.skill_count === 1 ? '' : 's') + ' across ' + d.plugin_count + ' plugin' + (d.plugin_count === 1 ? '' : 's'));
  body.appendChild(head);
  if (!skills.length) { const p = document.createElement('div'); p.className = 'plugins-empty'; p.textContent = 'No skills match that search.'; body.appendChild(p); return; }
  skills.forEach((s) => {
    const card = document.createElement('div'); card.className = 'plugin-card' + (s.first_party ? ' firstparty' : '');
    const h = document.createElement('div'); h.className = 'p-head';
    const nm = document.createElement('span'); nm.className = 'p-name'; nm.textContent = s.name;
    h.appendChild(nm);
    const pill = document.createElement('span'); pill.className = 'p-cat'; pill.textContent = s.plugin; pill.title = 'From plugin: ' + s.plugin;
    h.appendChild(pill);
    if (s.first_party) h.appendChild(_firstPartyBadge());
    card.appendChild(h);
    if (s.description) { const de = document.createElement('div'); de.className = 'p-desc'; de.textContent = s.description; card.appendChild(de); }
    body.appendChild(card);
  });
}

async function togglePlugin(id, enable) {
  if (!id) return;
  try {
    const r = await fetch('/api/plugins/enable', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, enabled: !!enable }),
    });
    const d = await r.json();
    if (d && d.ok) {
      _pluginCatalog = await (await fetch('/api/plugins')).json();
      const s = document.getElementById('pluginsSearch');
      renderPluginPanel(_pluginCatalog, s ? s.value : '');
    }
  } catch (e) { /* fail-soft: leave the card as-is */ }
}

// ---- Files panel: everything VOOL generated, across all chats ----
const filesOverlay = document.getElementById('filesOverlay');
let _filesData = null;
function closeFiles() { if (filesOverlay) filesOverlay.hidden = true; }
async function openFiles() {
  if (!filesOverlay) return;
  filesOverlay.hidden = false;
  const body = document.getElementById('filesBody');
  const search = document.getElementById('filesSearch');
  if (search) search.value = '';
  if (body) body.textContent = 'Loading…';
  try { _filesData = await (await fetch('/api/files')).json(); renderFiles(); if (search) search.focus(); }
  catch (e) { if (body) body.textContent = 'Could not read your files.'; }
}
function _fmtBytes(n) {
  n = Number(n || 0);
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}
function _fmtDate(epoch) { try { return new Date(Number(epoch) * 1000).toLocaleString(appLocale()); } catch (e) { return ''; } }
function _typeIcon(t) { return t === 'image' ? '\u{1F5BC}' : (t === 'video' ? '\u{1F3AC}' : (t === 'doc' ? '\u{1F4C4}' : '\u{1F4E6}')); }
function _fileRow(f) {
  const row = document.createElement('div'); row.className = 'file-row'; row.title = f.path;
  const ic = document.createElement('span'); ic.className = 'file-ic'; ic.textContent = _typeIcon(f.type);
  const col = document.createElement('span'); col.className = 'file-col';
  const nm = document.createElement('span'); nm.className = 'file-name'; nm.textContent = f.name;
  col.appendChild(nm);
  if (f.prompt) { const cap = document.createElement('span'); cap.className = 'file-cap'; cap.textContent = f.prompt; col.appendChild(cap); }
  const meta = document.createElement('span'); meta.className = 'file-meta'; meta.textContent = _fmtBytes(f.size) + ' · ' + _fmtDate(f.mtime);
  row.appendChild(ic); row.appendChild(col); row.appendChild(meta);
  row.addEventListener('click', () => openGeneratedFile(f.path));
  return row;
}
function renderFiles() {
  const body = document.getElementById('filesBody'); if (!body || !_filesData) return;
  body.innerHTML = '';
  const q = ((document.getElementById('filesSearch') || {}).value || '').trim().toLowerCase();
  const sort = (document.getElementById('filesSort') || {}).value || 'date';
  let files = (_filesData.files || []).slice();
  if (q) files = files.filter((f) => ((f.name || '') + ' ' + (f.type || '') + ' ' + (f.prompt || '')).toLowerCase().indexOf(q) !== -1);
  const head = document.createElement('div'); head.className = 'plugins-empty'; head.style.marginBottom = '12px';
  head.textContent = q
    ? (files.length + ' file' + (files.length === 1 ? '' : 's') + ' match “' + q + '”')
    : (_filesData.count + ' file' + (_filesData.count === 1 ? '' : 's') + (_filesData.count > _filesData.shown ? (' (showing newest ' + _filesData.shown + ')') : ''));
  body.appendChild(head);
  if (!files.length) {
    const p = document.createElement('div'); p.className = 'plugins-empty';
    p.textContent = _filesData.count ? 'No files match that search.' : 'Nothing generated yet — make an image or a doc and it shows up here.';
    body.appendChild(p); return;
  }
  if (sort === 'chat') {
    // Group by the chat that made each file; files with no chat tag go to an "Unattributed" group last.
    const groups = {}; const order = [];
    files.forEach((f) => { const k = f.session_id || ''; if (!(k in groups)) { groups[k] = []; order.push(k); } groups[k].push(f); });
    order.forEach((k) => groups[k].sort((a, b) => (b.mtime || 0) - (a.mtime || 0)));
    order.sort((a, b) => ((a === '' ? 1 : 0) - (b === '' ? 1 : 0)) || ((groups[b][0].mtime || 0) - (groups[a][0].mtime || 0)));
    order.forEach((k) => {
      const gh = document.createElement('div'); gh.className = 'files-group';
      gh.textContent = k ? (groups[k][0].prompt || ('Chat ' + k.replace(/^openclaw:/, '').slice(0, 8))) : 'Unattributed';
      body.appendChild(gh);
      groups[k].forEach((f) => body.appendChild(_fileRow(f)));
    });
    return;
  }
  if (sort === 'name') files.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  else if (sort === 'type') files.sort((a, b) => String(a.type).localeCompare(String(b.type)) || ((b.mtime || 0) - (a.mtime || 0)));
  else if (sort === 'size') files.sort((a, b) => (b.size || 0) - (a.size || 0));
  else files.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  files.forEach((f) => body.appendChild(_fileRow(f)));
}
async function openGeneratedFile(path) {
  if (!path) return;
  try { await fetch('/api/files/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: path }) }); } catch (e) {}
}

// ---- Token usage panel ----
// Reads the real ledger via /api/runtime/usage; every number shown is a real served-response
// total, never a guess. Fail-soft: a fetch error shows a neutral note, not stale figures.
let usageRange = 'today';
const usageBodyEl = document.getElementById('usageBody');
function fmtInt(n) { return Number(n || 0).toLocaleString(appLocale()); }
function usageCard(cls, key, val) {
  const c = document.createElement('div'); c.className = 'u-card ' + cls;
  const k = document.createElement('div'); k.className = 'u-k'; k.textContent = key;
  const v = document.createElement('div'); v.className = 'u-v'; v.textContent = val;
  c.appendChild(k); c.appendChild(v); return c;
}
async function renderUsage(range) {
  usageRange = range || usageRange;
  document.querySelectorAll('#usageTabs .usage-tab').forEach((t) => t.classList.toggle('on', t.getAttribute('data-range') === usageRange));
  if (!usageBodyEl) return;
  let data;
  try {
    const q = usageRange === 'all' ? '?per_model=1' : ('?range=' + encodeURIComponent(usageRange) + '&per_model=1');
    const r = await fetch('/api/runtime/usage' + q);
    data = await r.json();
  } catch (e) { usageBodyEl.textContent = 'Usage is unavailable right now.'; return; }
  const free = (data && data.free_local) || {}, freeCloud = (data && data.free_cloud) || {}, paid = (data && data.paid_cloud) || {};
  const rows = Array.isArray(data && data.by_model) ? data.by_model : [];
  usageBodyEl.textContent = '';
  const head = document.createElement('div'); head.className = 'usage-head';
  head.appendChild(usageCard('free', 'Local (free) tokens', fmtInt(free.total_tokens)));
  head.appendChild(usageCard('free', 'Cloud (free) tokens', fmtInt(freeCloud.total_tokens)));
  const paidUsd = Number(paid.usd || 0);
  const paidVal = fmtInt(paid.total_tokens) + (paid.total_tokens ? ('  ' + (paid.all_actual ? '$' : '~$') + paidUsd.toFixed(4)) : '');
  head.appendChild(usageCard('paid', 'Cloud (paid) tokens', paidVal));
  head.appendChild(usageCard('', 'Total tokens', fmtInt(data && data.total_tokens)));
  usageBodyEl.appendChild(head);
  if (!rows.length) { const e = document.createElement('div'); e.className = 'usage-empty'; e.textContent = 'No usage recorded in this window yet.'; usageBodyEl.appendChild(e); return; }
  const table = document.createElement('table'); table.className = 'usage-table';
  const thead = document.createElement('thead'); const htr = document.createElement('tr');
  ['Model', 'Tokens', 'Responses', 'Cost'].forEach((h, i) => { const th = document.createElement('th'); th.textContent = h; if (i > 0) th.style.textAlign = 'right'; htr.appendChild(th); });
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = document.createElement('tbody');
  rows.slice(0, 30).forEach((row) => {
    const tr = document.createElement('tr');
    const mtd = document.createElement('td'); mtd.textContent = row.model_id || row.provider_id || 'unknown'; tr.appendChild(mtd);
    const ttd = document.createElement('td'); ttd.className = 'num'; ttd.textContent = fmtInt(row.total_tokens); tr.appendChild(ttd);
    const rtd = document.createElement('td'); rtd.className = 'num'; rtd.textContent = fmtInt(row.responses); tr.appendChild(rtd);
    const ctd = document.createElement('td'); ctd.className = 'num';
    ctd.textContent = (row.cost_class === 'paid_cloud') ? ((row.all_actual ? '$' : '~$') + Number(row.usd || 0).toFixed(4)) : 'free';
    tr.appendChild(ctd);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); usageBodyEl.appendChild(table);
}

async function saveCloudKey() {
  const val = (orKeyEl.value || '').trim();
  if (!val) { orStatusEl.textContent = 'Enter a key first.'; orStatusEl.className = 'set-status err'; return; }
  let provider = orProviderEl ? orProviderEl.value : '';
  if (!provider) {
    const g = detectProvider(val);
    if (g.confidence === 'high') provider = g.id;
    else { orStatusEl.textContent = "This key's provider is ambiguous — pick it from the dropdown."; orStatusEl.className = 'set-status err'; return; }
  }
  const label = (orProviderEl && orProviderEl.selectedIndex >= 0 && orProviderEl.value) ? orProviderEl.options[orProviderEl.selectedIndex].text : provider;
  const payload = { provider: provider, value: val, label: label };
  if (provider === 'custom') {
    const base = (orBaseUrlEl && orBaseUrlEl.value || '').trim();
    if (!/^https:\/\//i.test(base) && !/^http:\/\/(127\.0\.0\.1|localhost)/i.test(base)) {
      orStatusEl.textContent = 'A custom endpoint needs a secure (https) or loopback base URL.'; orStatusEl.className = 'set-status err'; return;
    }
    payload.base_url = base;
  }
  orSaveEl.disabled = true;
  try {
    const r = await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json();
    if (r.ok && j.connected) {
      setCloudConnected(true); refreshCloudStatus(true);   // live-probe the freshly saved key
      // setCloudConnected() writes the standing "key saved and encrypted" state line, which reads
      // the same whether or not anything just happened. Saving a credential is an event, so it also
      // gets an acknowledgement that is unmistakably about THIS action.
      showKeySavedBanner(label || provider);
      toast('API key saved and encrypted on this machine.');
    }
    else { orStatusEl.textContent = 'Could not save: ' + (j.error || r.status); orStatusEl.className = 'set-status err'; hideKeySavedBanner(); }
  } catch (e) { orStatusEl.textContent = 'Could not save: ' + (e && e.message ? e.message : e); orStatusEl.className = 'set-status err'; }
  orSaveEl.disabled = false;
}

let _keySavedTimer = null;
function showKeySavedBanner(providerLabel) {
  const banner = document.getElementById('orSavedBanner'), text = document.getElementById('orSavedText');
  if (!banner) return;
  if (text) text.textContent = providerLabel
    ? ('Saved. Your ' + providerLabel + ' key is encrypted on this machine.')
    : 'Saved. Your key is encrypted on this machine.';
  banner.classList.add('show');
  if (_keySavedTimer) clearTimeout(_keySavedTimer);
  _keySavedTimer = setTimeout(() => banner.classList.remove('show'), 8000);
}
function hideKeySavedBanner() {
  const banner = document.getElementById('orSavedBanner');
  if (banner) banner.classList.remove('show');
  if (_keySavedTimer) { clearTimeout(_keySavedTimer); _keySavedTimer = null; }
}

// ---- Build identity ----
// "Which build am I running?" was answerable only from the header's shortened commit. A bug report
// needs the exact one, so every field the runtime stamps is shown and can be copied in one action.
const BUILD_INFO_FIELDS = [
  ['Version', 'app_version'],
  ['Release version', 'release_version'],
  ['Build id', 'build_id'],
  ['Commit', 'commit_full'],
  ['Branch', 'branch'],
  ['Channel', 'channel_name'],
  ['Platform', 'platform'],
  ['Bundle id', 'native_bundle_id'],
  ['Bundle path', 'native_bundle_path'],
  ['Native host PID', 'native_host_pid'],
  ['Backend PID', 'pid'],
  ['API bind', 'api_bind'],
  ['Backend owned', 'native_backend_owned'],
  ['Workstation', 'workstation_version'],
  ['Model tag', 'model_tag'],
];
let _buildStamp = null;
async function renderBuildInfo() {
  const host = document.getElementById('buildInfo');
  if (!host) return;
  try {
    const r = await fetch('/api/runtime/version');
    _buildStamp = await r.json();
  } catch (e) {
    host.innerHTML = '<dt>Build</dt><dd>Could not read the running build.</dd>';
    return;
  }
  const v = _buildStamp || {};
  let html = '';
  BUILD_INFO_FIELDS.forEach((f) => {
    let value = v[f[1]];
    // The full SHA is the point of the Commit row; fall back to the short one rather than showing
    // an em dash, and say which it is so a 12-character value is never mistaken for the whole SHA.
    if (f[1] === 'commit_full' && !value) value = v.commit ? (v.commit + ' (short)') : '';
    if (f[1] === 'commit_full' && value && v.dirty) value = value + ' + uncommitted changes';
    if (value === undefined || value === null || value === '') return;
    html += '<dt>' + esc(f[0]) + '</dt><dd>' + esc(String(value)) + '</dd>';
  });
  host.innerHTML = html || '<dt>Build</dt><dd>The runtime reported no build stamp.</dd>';
}
function copyBuildInfo() {
  const btn = document.getElementById('buildCopy');
  const v = _buildStamp;
  if (!v) { toast('Build information has not loaded yet.'); return; }
  const lines = ['VOOL build'];
  BUILD_INFO_FIELDS.forEach((f) => {
    let value = v[f[1]];
    if (f[1] === 'commit_full' && !value) value = v.commit || '';
    if (value === undefined || value === null || value === '') return;
    lines.push(f[0] + ': ' + value);
  });
  lines.push('Uncommitted changes: ' + (v.dirty ? 'yes' : 'no'));
  copyText(lines.join('\n'), btn);
}

async function testCloudConnection() {
  if (!orTestEl) return;
  orTestEl.disabled = true;
  if (orStatusEl) { orStatusEl.textContent = 'Testing…'; orStatusEl.className = 'set-status'; }
  try {
    const r = await fetch('/api/cloud/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const j = await r.json();
    paintCloudPill(j.state);
    if (orStatusEl) {
      if (j.state === 'ok') { orStatusEl.textContent = 'Connection verified — OpenRouter accepted your key.'; orStatusEl.className = 'set-status ok'; }
      else if (j.detail === 'unauthorized') { orStatusEl.textContent = 'OpenRouter rejected the key (HTTP ' + (j.http_status || '?') + '). Re-check or replace it.'; orStatusEl.className = 'set-status err'; }
      else { orStatusEl.textContent = 'Could not reach OpenRouter — the key itself was not judged.'; orStatusEl.className = 'set-status err'; }
    }
  } catch (e) { if (orStatusEl) { orStatusEl.textContent = 'Test failed: ' + (e && e.message ? e.message : e); orStatusEl.className = 'set-status err'; } }
  orTestEl.disabled = false;
}

async function removeCloudKey() {
  orRemoveEl.disabled = true;
  try {
    const r = await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: OPENROUTER_CRED, delete: true }) });
    if (r.ok) { setCloudConnected(false); refreshCloudStatus(); }
  } catch (e) { /* ignore */ }
  orRemoveEl.disabled = false;
}

if (pluginsOverlay) {
  const pBtn = document.getElementById('pluginsBtn'); if (pBtn) pBtn.addEventListener('click', openPlugins);
  const sBtn = document.getElementById('skillsBtn'); if (sBtn) sBtn.addEventListener('click', openSkills);
  const pClose = document.getElementById('pluginsClose'); if (pClose) pClose.addEventListener('click', closePlugins);
  const pSearch = document.getElementById('pluginsSearch'); if (pSearch) pSearch.addEventListener('input', () => renderPluginPanel(_pluginCatalog, pSearch.value));
  pluginsOverlay.addEventListener('click', (e) => { if (e.target === pluginsOverlay) closePlugins(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pluginsOverlay.hidden) closePlugins(); });
}
if (councilOverlay) {
  const cBtn = document.getElementById('councilBtn'); if (cBtn) cBtn.addEventListener('click', openCouncil);
  const cClose = document.getElementById('councilClose'); if (cClose) cClose.addEventListener('click', closeCouncil);
  councilOverlay.addEventListener('click', (e) => { if (e.target === councilOverlay) closeCouncil(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !councilOverlay.hidden) closeCouncil(); });
}
if (filesOverlay) {
  const fBtn = document.getElementById('filesBtn'); if (fBtn) fBtn.addEventListener('click', openFiles);
  const fClose = document.getElementById('filesClose'); if (fClose) fClose.addEventListener('click', closeFiles);
  const fSearch = document.getElementById('filesSearch'); if (fSearch) fSearch.addEventListener('input', renderFiles);
  const fSort = document.getElementById('filesSort'); if (fSort) fSort.addEventListener('change', renderFiles);
  filesOverlay.addEventListener('click', (e) => { if (e.target === filesOverlay) closeFiles(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !filesOverlay.hidden) closeFiles(); });
}
// ---- Session bundle export/import (P1). One flow each way: export posts the current chat's
// bundle plan and saves the signed (+usually encrypted) file; import stages the picked file,
// previews EXACTLY what will land (counts, conflicts, signer trust), then imports on confirm.
// Passphrases live only in these fields and POST bodies — never history, logs or receipts.
const sbExportBtn = document.getElementById('sbExportBtn');
const sbEncrypt = document.getElementById('sbEncrypt');
const sbPass = document.getElementById('sbPass');
const sbPass2 = document.getElementById('sbPass2');
const sbPlainWarnRow = document.getElementById('sbPlainWarnRow');
const sbPlainWarn = document.getElementById('sbPlainWarn');
const sbExportStatus = document.getElementById('sbExportStatus');
const sbImportFile = document.getElementById('sbImportFile');
const sbImportPass = document.getElementById('sbImportPass');
const sbPreviewBtn = document.getElementById('sbPreviewBtn');
const sbPreviewBox = document.getElementById('sbPreviewBox');
const sbForeignRow = document.getElementById('sbForeignRow');
const sbConfirmForeign = document.getElementById('sbConfirmForeign');
const sbImportBtn = document.getElementById('sbImportBtn');
const sbOpenRestored = document.getElementById('sbOpenRestored');
const sbImportStatus = document.getElementById('sbImportStatus');
let sbStagedPath = '';
let sbNeedsConfirm = false;
let sbRestoredId = '';

function sbStatus(el, text, warn) { if (el) { el.textContent = text || ''; el.style.color = warn ? 'var(--warn, #b58a2c)' : ''; } }
function sbSyncEncryptUi() {
  if (!sbEncrypt) return;
  const passFields = document.getElementById('sbPassFields');
  if (passFields) passFields.hidden = !sbEncrypt.checked;
  if (sbPlainWarnRow) sbPlainWarnRow.hidden = sbEncrypt.checked;
}
async function sbRenderExportPreview() {
  const box = document.getElementById('sbExportPreview');
  if (!box) return;
  if (!displayedChat) { box.hidden = true; box.textContent = ''; return; }
  try {
    const r = await fetch('/api/session/bundle/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: displayedChat }) });
    const d = await r.json();
    if (!d || !d.counts) { box.hidden = true; return; }
    box.hidden = false; box.textContent = '';
    const title = document.createElement('div'); title.className = 'sb-title'; title.textContent = 'Export preview — exactly what will be written:'; box.appendChild(title);
    const row = (k, v) => { const el = document.createElement('div'); el.className = 'sb-row'; const a = document.createElement('span'); a.className = 'sb-k'; a.textContent = k; const b = document.createElement('span'); b.textContent = v; el.appendChild(a); el.appendChild(b); box.appendChild(el); };
    row('Included', d.counts.turns + ' turns, ' + d.counts.summaries + ' summaries, ' + d.counts.obligation_sets + ' obligation records, ' + d.counts.tool_receipts + ' receipts, ' + d.counts.session_events + ' activity events');
    const bytes = (d.embedded_files || []).reduce((s, f) => s + (f.size_bytes || 0), 0);
    row('Attachment bytes', (d.counts.embedded_files || 0) + ' file(s), ' + bytes + ' bytes');
    row('Redactions applied', String(d.redactions || 0));
    row('Encryption', (sbEncrypt && sbEncrypt.checked) ? 'ON (recommended)' : 'OFF — file will be readable by anyone who obtains it');
    const note = document.createElement('div'); note.className = 'sb-file';
    note.textContent = 'Excluded: ' + (d.scope_note || 'model-internal reasoning is not part of a bundle');
    box.appendChild(note);
  } catch (e) { box.hidden = true; }
}
if (sbEncrypt) sbEncrypt.addEventListener('change', () => { sbSyncEncryptUi(); sbRenderExportPreview(); });
if (sbExportBtn) sbExportBtn.addEventListener('click', async () => {
  if (!displayedChat) { sbStatus(sbExportStatus, 'Open the chat you want to export first.', true); return; }
  let passphrase = '';
  if (sbEncrypt && sbEncrypt.checked) {
    passphrase = String((sbPass && sbPass.value) || '');
    if (!passphrase) { sbStatus(sbExportStatus, 'Type a passphrase, or untick encryption.', true); return; }
    if (sbPass2 && passphrase !== String(sbPass2.value || '')) { sbStatus(sbExportStatus, 'The two passphrases do not match.', true); return; }
  } else if (sbPlainWarn && !sbPlainWarn.checked) {
    sbStatus(sbExportStatus, 'Unticking encryption needs the explicit warning acknowledgement below.', true); return;
  }
  sbStatus(sbExportStatus, 'Working\u2026');
  try {
    const r = await fetch('/api/session/bundle/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: displayedChat, passphrase }) });
    const d = await r.json();
    if (!d.ok) { sbStatus(sbExportStatus, d.error || 'Export refused.', true); return; }
    sbPass && (sbPass.value = ''); sbPass2 && (sbPass2.value = '');
    const a = document.createElement('a');
    a.href = '/api/session/bundle/download?path=' + encodeURIComponent(d.path);
    a.download = (d.session_id || 'chat').replace(/[^a-z0-9_-]/gi, '_') + '.voolsession';
    document.body.appendChild(a); a.click(); a.remove();
    sbStatus(sbExportStatus, d.encrypted ? 'Saved: signed + encrypted bundle (' + d.counts.turns + ' turns).' : 'Saved: signed, UNENCRYPTED bundle (' + d.counts.turns + ' turns).', !d.encrypted);
    sbRenderExportPreview();
  } catch (e) { sbStatus(sbExportStatus, 'Export failed: ' + (e && e.message || e), true); }
});
if (sbImportFile) sbImportFile.addEventListener('change', () => {
  sbStagedPath = ''; sbNeedsConfirm = false;
  if (sbImportBtn) sbImportBtn.disabled = true;
  if (sbPreviewBox) { sbPreviewBox.hidden = true; sbPreviewBox.textContent = ''; }
  if (sbForeignRow) sbForeignRow.hidden = true;
  if (sbConfirmForeign) sbConfirmForeign.checked = false;
  if (sbOpenRestored) sbOpenRestored.hidden = true;
  sbStatus(sbImportStatus, '');
});
if (sbPreviewBtn) sbPreviewBtn.addEventListener('click', async () => {
  const file = sbImportFile && sbImportFile.files && sbImportFile.files[0];
  if (!file) { sbStatus(sbImportStatus, 'Pick a .voolsession file first.', true); return; }
  sbStatus(sbImportStatus, 'Reading file\u2026');
  try {
    const staged = await fetch('/api/session/bundle/upload', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Vool-Bundle-Name': encodeURIComponent(file.name) }, body: file });
    const sj = await staged.json();
    if (!sj.ok) { sbStatus(sbImportStatus, sj.error || 'Could not stage the file.', true); return; }
    sbStagedPath = sj.path;
    sbStatus(sbImportStatus, 'Previewing\u2026');
    const r = await fetch('/api/session/bundle/inspect-import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: sbStagedPath, passphrase: String((sbImportPass && sbImportPass.value) || '') }) });
    const d = await r.json();
    if (!d.ok) { sbStatus(sbImportStatus, d.error || 'Preview refused.', true); return; }
    sbNeedsConfirm = !!d.needs_confirmation;
    renderSbPreview(d);
    if (sbImportBtn) sbImportBtn.disabled = false;
    if (sbForeignRow) sbForeignRow.hidden = !sbNeedsConfirm;
    if (sbOpenRestored) sbOpenRestored.hidden = true;
    sbStatus(sbImportStatus, sbNeedsConfirm ? 'Signed by an UNKNOWN key — tick the acknowledgement to import it untrusted.' : 'Preview ready — review, then Import.');
  } catch (e) { sbStatus(sbImportStatus, 'Preview failed: ' + (e && e.message || e), true); }
});
function renderSbPreview(d) {
  if (!sbPreviewBox) return;
  sbPreviewBox.hidden = false; sbPreviewBox.textContent = '';
  const row = (k, v, cls) => { const r = document.createElement('div'); r.className = 'sb-row'; const a = document.createElement('span'); a.className = 'sb-k'; a.textContent = k; const b = document.createElement('span'); b.className = cls || ''; b.textContent = v; r.appendChild(a); r.appendChild(b); sbPreviewBox.appendChild(r); };
  const title = document.createElement('div'); title.className = 'sb-title'; title.textContent = 'This import will:'; sbPreviewBox.appendChild(title);
  row('Chat', (d.title || d.source_session_id || 'untitled') + (d.conflicts && d.conflicts.collision ? '  (id already exists here — a NEW id will be used)' : ''));
  row('Turns / summaries', (d.counts.turns || 0) + ' / ' + (d.counts.summaries || 0));
  row('Receipts / activity events', (d.counts.tool_receipts || 0) + ' / ' + (d.counts.session_events || 0));
  row('Obligation records', String(d.counts.obligation_sets || 0));
  row('Embedded attachments', (d.counts.embedded_files || 0) + (d.counts.profile_candidates ? '' : ''), '');
  row('Profile references (as candidates)', String(d.counts.profile_candidates || 0));
  row('Signed by', (d.signature && d.signature.signer_fingerprint) || 'unknown', d.signature && d.signature.trusted ? 'sb-ok' : 'sb-warn');
  row('Encryption on file', d.encrypted ? 'yes' : 'no');
  const note = document.createElement('div'); note.className = d.signature && d.signature.trusted ? 'sb-ok' : 'sb-warn';
  note.textContent = d.conflicts && d.conflicts.excluded ? 'Excluded: ' + d.conflicts.excluded : '';
  sbPreviewBox.appendChild(note);
  if (d.conflicts && d.conflicts.collision) { const w = document.createElement('div'); w.className = 'sb-warn'; w.textContent = 'A session with this id already exists here. The import lands under a new id; nothing existing is modified.'; sbPreviewBox.appendChild(w); }
  if (d.needs_confirmation) { const w = document.createElement('div'); w.className = 'sb-warn'; w.textContent = 'Unknown signer: the import stays marked untrusted.'; sbPreviewBox.appendChild(w); }
}
if (sbImportBtn) sbImportBtn.addEventListener('click', async () => {
  if (!sbStagedPath) { sbStatus(sbImportStatus, 'Preview a file first.', true); return; }
  if (sbNeedsConfirm && sbConfirmForeign && !sbConfirmForeign.checked) { sbStatus(sbImportStatus, 'Tick the unknown-signer acknowledgement, or cancel the import.', true); return; }
  sbStatus(sbImportStatus, 'Importing\u2026');
  try {
    const r = await fetch('/api/session/bundle/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: sbStagedPath, passphrase: String((sbImportPass && sbImportPass.value) || ''), confirm_untrusted: !!(sbConfirmForeign && sbConfirmForeign.checked) }) });
    const d = await r.json();
    if (!d.ok) { sbStatus(sbImportStatus, d.error || 'Import refused — nothing was changed.', true); return; }
    sbRestoredId = d.imported_session_id || '';
    sbImportFile && (sbImportFile.value = '');
    if (sbImportBtn) sbImportBtn.disabled = true;
    if (sbPreviewBox) { sbPreviewBox.hidden = true; }
    if (sbOpenRestored) sbOpenRestored.hidden = false;
    sbStatus(sbImportStatus, 'Imported ' + d.counts.turns + ' turns' + (d.collision ? ' under a new id (the original was already here).' : '.') + (d.trust && d.trust.trusted === false ? ' Marked UNTRUSTED (unknown signer).' : ''));
  } catch (e) { sbStatus(sbImportStatus, 'Import failed: ' + (e && e.message || e), true); }
});
if (sbOpenRestored) sbOpenRestored.addEventListener('click', async () => {
  if (!sbRestoredId) return;
  closeSettings();
  await openSession(sbRestoredId);
});

if (settingsOverlay) {
  const sBtn = document.getElementById('settingsBtn'); if (sBtn) sBtn.addEventListener('click', openSettings);
  const sClose = document.getElementById('settingsClose'); if (sClose) sClose.addEventListener('click', closeSettings);
  const prefsSave = document.getElementById('setPrefsSave'); if (prefsSave) prefsSave.addEventListener('click', savePrefs);
  const humor = document.getElementById('setHumor'); const humorVal = document.getElementById('setHumorVal');
  if (humor && humorVal) humor.addEventListener('input', () => { humorVal.textContent = humor.value + '%'; });
  const reserve = document.getElementById('setReserve'); const reserveVal = document.getElementById('setReserveVal');
  if (reserve && reserveVal) reserve.addEventListener('input', () => { reserveVal.textContent = reserve.value + '%'; });
  settingsOverlay.addEventListener('click', (e) => { if (e.target === settingsOverlay) closeSettings(); });
  if (orSaveEl) orSaveEl.addEventListener('click', saveCloudKey);
  if (orTestEl) orTestEl.addEventListener('click', testCloudConnection);
  if (orKeyEl) orKeyEl.addEventListener('input', syncProviderUi);
  if (orProviderEl) orProviderEl.addEventListener('change', syncProviderUi);
  document.querySelectorAll('#usageTabs .usage-tab').forEach((t) => t.addEventListener('click', () => renderUsage(t.getAttribute('data-range'))));
  if (orRemoveEl) orRemoveEl.addEventListener('click', removeCloudKey);
  if (orKeyEl) orKeyEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveCloudKey(); } });
  // Reveal the key being typed. A pasted key is unverifiable behind dots, and a mistyped one is
  // only discoverable from the provider's rejection several steps later.
  const orRevealEl = document.getElementById('orReveal');
  if (orRevealEl && orKeyEl) orRevealEl.addEventListener('click', () => {
    const shown = orKeyEl.type === 'text';
    orKeyEl.type = shown ? 'password' : 'text';
    orRevealEl.textContent = shown ? 'Show' : 'Hide';
    orRevealEl.setAttribute('aria-pressed', shown ? 'false' : 'true');
    orKeyEl.focus();
  });
  // Web-search key controls: same three behaviours as the cloud key above (save, reveal, and
  // Enter-to-save), pointed at the search endpoints.
  const wsKeyEl = document.getElementById('wsKey');
  const wsSaveEl = document.getElementById('wsSave');
  if (wsSaveEl) wsSaveEl.addEventListener('click', saveSearchKey);
  if (wsKeyEl) wsKeyEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveSearchKey(); } });
  const wsRevealEl = document.getElementById('wsReveal');
  if (wsRevealEl && wsKeyEl) wsRevealEl.addEventListener('click', () => {
    const shown = wsKeyEl.type === 'text';
    wsKeyEl.type = shown ? 'password' : 'text';
    wsRevealEl.textContent = shown ? 'Show' : 'Hide';
    wsRevealEl.setAttribute('aria-pressed', shown ? 'false' : 'true');
    wsKeyEl.focus();
  });
  const wsTestEl = document.getElementById('wsTest');
  if (wsTestEl) wsTestEl.addEventListener('click', async () => {
    const connected = _wsProviders.filter((p) => p.connected);
    if (!connected.length) { _wsSetStatus('No search key stored yet.', true); return; }
    wsTestEl.disabled = true; wsTestEl.textContent = 'Testing…';
    _wsSetStatus('Running a live search…', false);
    let res = {};
    try {
      res = await (await fetch('/api/search/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: connected[0].provider }) })).json();
    } catch (e) { res = { state: 'failed', detail: 'request failed' }; }
    wsTestEl.disabled = false; wsTestEl.textContent = 'Test search';
    if (res.state === 'ok') _wsSetStatus(_searchTestSuccess(res) + ' Answered a live search.', false);
    else _wsSetStatus(_explainSearchError(res.state, res.detail), true);
  });
  const wsRemoveEl = document.getElementById('wsRemove');
  if (wsRemoveEl) wsRemoveEl.addEventListener('click', async () => {
    const connected = _wsProviders.filter((p) => p.connected);
    if (!connected.length) return;
    wsRemoveEl.disabled = true;
    for (const p of connected) {
      try { await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: p.provider, delete: true }) }); } catch (e) {}
    }
    wsRemoveEl.disabled = false;
    _wsSetStatus('Search keys removed. Built-in search still works.', false);
    loadSearchProviders();
  });
  const buildCopyEl = document.getElementById('buildCopy');
  if (buildCopyEl) buildCopyEl.addEventListener('click', copyBuildInfo);
  // The paid-cloud note in the model popover opens Settings too.
  const note = document.querySelector('#modelPop .pop-note'); if (note) { note.style.cursor = 'pointer'; note.addEventListener('click', openSettings); }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && settingsOverlay && !settingsOverlay.hidden) closeSettings(); });
  if (settingsFrameOverlay) {
    settingsFrameOverlay.addEventListener('click', (e) => { if (e.target === settingsFrameOverlay) closeSettingsFrame(); });
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && settingsFrameOverlay && !settingsFrameOverlay.hidden) closeSettingsFrame(); });
  // The platform gesture for settings. Bound here as well as in the macOS menu, so it works in a
  // browser tab and inside the native window even before the menu item exists.
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); openSettings(); }
  });
}

sendEl.addEventListener('click', send);
newChatEl.addEventListener('click', newChat);
const newProjectEl = document.getElementById('newProject');
if (newProjectEl) newProjectEl.addEventListener('click', () => { createProjectFlow(); });
menuEl.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
// ---- Exact, expiring permission controls ---------------------------------------------------
// Approval ids come from the controller and authorize only the fingerprinted task/tool/arguments.
// Nothing here can turn an approval into a blanket browser-side "always allow" preference.
const permBarEl = document.getElementById('permBar');
const permMsgEl = document.getElementById('permMsg');
const permMetaEl = document.getElementById('permMeta');
const permDetailEl = document.getElementById('permDetail');
// The token is HELD, not spent, when a turn is sent: only the server can say whether it matched the
// action the model re-planned. Clearing it at send meant a resumed turn whose arguments shifted by a
// byte lost the grant the operator had just given and was asked all over again. It is released below
// when the server acts on it -- a superseding request, or a turn that ended without asking again.
// Grants are held per chat (see the DISPATCHER block): a token granted in chat A must never be
// attached to chat B's turn, where the server would reject it AND the grant would be destroyed.
function showPermBar(chatId, ev) {
  const owner = chatState(chatId);
  const a = (ev && ev.approval) || {};
  // `task_pending_approval` is a lifecycle receipt and intentionally carries no reusable token.
  // Never let that later generic event overwrite the exact, actionable `tool_preview` request.
  if (!a.approval_id) return;
  // A different request means the server did not match the token still in hand: it is spent now.
  if (owner.approvalToken && owner.approvalToken !== a.approval_id) releaseApprovalTokenFor(chatId);
  owner.pendingApproval = a;
  // Painting the single permission bar is display work: only the chat on screen may drive it.
  if (!permBarEl || !isDisplayed(chatId)) return;
  permMsgEl.textContent = a.action || ev.summary || pageT('permissions.needs_approval_default', 'VOOL needs approval for one exact action.');
  const resources = Array.isArray(a.affected_resources) && a.affected_resources.length ? a.affected_resources.join(', ') : pageT('permissions.no_path_reported', 'No specific path reported');
  const effects = a.expected_side_effects || pageT('permissions.side_effect_undescribed', 'Side effect not described');
  permMetaEl.textContent = pageTF('permissions.meta_line', 'Affects: {resources} · Side effects: {effects} · {reversibility}', { resources: resources, effects: effects, reversibility: (a.reversible ? pageT('permissions.reversible', 'Reversible') : pageT('permissions.may_not_reversible', 'May not be reversible')) });
  const detailBits = [];
  if (a.intent) detailBits.push(pageT('permissions.detail_tool', 'Tool: ') + a.intent);
  if (a.task_id) detailBits.push(pageT('permissions.detail_task', 'Task: ') + a.task_id);
  if (Array.isArray(a.planned_actions) && a.planned_actions.length > 1) {
    detailBits.push(pageTF('permissions.planned_header', 'Planned changes in this request ({count}):', { count: a.planned_actions.length }));
    a.planned_actions.forEach((p) => { detailBits.push('  - ' + (p.intent || 'action') + ' ' + (p.target || '')); });
  }
  // What the same plan asked for that this grant refuses. Named, because a truthful count of the
  // covered set still leaves an operator to GUESS whether the delete or the command beside it rode
  // along -- and the whole point of the bounded batch is that it did not.
  if (Array.isArray(a.excluded_actions) && a.excluded_actions.length) {
    detailBits.push(pageTF('permissions.not_covered_prefix', 'Not covered -- each of these asks separately ({count}):', { count: a.excluded_actions.length }));
    a.excluded_actions.forEach((p) => { detailBits.push('  - ' + (p.intent || 'action') + ' ' + (p.target || '')); });
  }
  if (a.diff_preview) detailBits.push('\n' + pageT('permissions.proposed_changes', 'Proposed changes:') + '\n' + a.diff_preview);
  permDetailEl.textContent = detailBits.join('\n'); permDetailEl.hidden = true;
  const taskBtn = document.getElementById('permTask');
  if (taskBtn) taskBtn.hidden = !(Array.isArray(a.scope_options) && a.scope_options.indexOf('task') >= 0);
  // One bounded batch for the writes THIS request already planned. Offered only when the controller
  // enumerated them, and it authorizes exactly those calls -- never a standing filesystem grant.
  const reqBtn = document.getElementById('permRequest');
  const reqOffered = Array.isArray(a.scope_options) && a.scope_options.indexOf('request') >= 0;
  if (reqBtn) {
    reqBtn.hidden = !reqOffered;
    // The middle phrase is the server's: a batch that also sets up a directory reads "planned
    // workspace changes", a pure file batch reads "planned changes". Deciding that here would put a
    // second classifier in the browser, disagreeing with the one that built the set.
    // The count and the server's own batch label ride as parameters: the sentence localizes,
    // the authority's classification (its exact words) stays verbatim inside it.
    if (reqOffered) reqBtn.textContent = pageTF('permissions.allow_all_n', 'Allow all {count} {label} for this request', { count: (a.planned_action_count || 0), label: (a.planned_action_label || pageT('permissions.planned_changes_label', 'planned changes')) });
  }
  // The controller offers 'project' only for a low-risk action landing inside the bound project root.
  const projBtn = document.getElementById('permProject');
  if (projBtn) projBtn.hidden = !(Array.isArray(a.scope_options) && a.scope_options.indexOf('project') >= 0);
  permBarEl.hidden = false;
  syncAnswerPendingSurfaces();
}
function hidePermBar() { if (permBarEl) permBarEl.hidden = true; if (permDetailEl) permDetailEl.hidden = true; syncAnswerPendingSurfaces(); }
// The single authority for "an operator-answer surface is on screen right now". Raises the footer
// above the companion layer (CSS: body.answer-pending) and tells the NATIVE detached pet, when
// one exists, to hand the pointer back over the footer's global rect -- the same law at the OS
// window tier that the stacking rule applies in-page. Cleared the moment neither surface shows.
function syncAnswerPendingSurfaces() {
  const banner = document.getElementById('bypassBanner');
  const pending = !!((permBarEl && !permBarEl.hidden) || (banner && !banner.hidden));
  document.body.classList.toggle('answer-pending', pending);
  try {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.pet_yield_rect) return;
    if (!pending) { api.pet_yield_rect(null); return; }
    const footer = document.querySelector('footer');
    if (!footer) return;
    const r = footer.getBoundingClientRect();
    // Best-effort screen coordinates: window.screenX/Y name the window's frame on screen and the
    // chrome (titlebar) offset is derived, then padded so estimate error cannot shave the bar.
    const chrome = Math.max(0, window.outerHeight - window.innerHeight);
    api.pet_yield_rect({
      x: Math.round(window.screenX + r.left - 16),
      y: Math.round(window.screenY + chrome + r.top - 16),
      width: Math.ceil(r.width + 32),
      height: Math.ceil(r.height + 32)
    });
  } catch (e) { /* presentation-only yield; the stacking rule above is the in-page guarantee */ }
}
// Re-send the approved turn IN THE CHAT THAT WAS APPROVED. The chat id is explicit, so an
// approval granted in one chat can never resend into another.
function resumeApprovedTurn(chatId) {
  const owner = chatState(chatId || displayedChat);
  if (!owner.resumeApprovedTurn || isChatBusy(owner.chatId)) return;
  owner.resumeApprovedTurn = false;
  const lastUser = owner.history.slice().reverse().find((m) => m && m.role === 'user');
  if (!lastUser) return;
  if (owner.history.length && owner.history[owner.history.length - 1].role === 'assistant') {
    owner.history.pop();
    if (isDisplayed(owner.chatId)) {
      const bubbles = logEl.querySelectorAll('.msg.assistant');
      if (bubbles.length) bubbles[bubbles.length - 1].remove();
    }
  }
  const resumeTurnId = owner.approvalResumeTurnId; owner.approvalResumeTurnId = '';
  runTurn(lastUser.content, null, { resend: true, turnId: resumeTurnId, chatId: owner.chatId });
}
// The permission bar only ever shows the DISPLAYED chat's request (showPermBar enforces that), so
// resolving it resolves that chat's grant -- recorded in that chat's bucket, never a global one.
async function resolvePermission(decision, scope) {
  const chatId = displayedChat;
  const owner = chatState(chatId);
  const approval = owner.pendingApproval; if (!approval || !approval.approval_id) { toast(pageT('permissions.approval_gone', 'This approval is no longer available.')); return; }
  try {
    const data = await postMode({ op: 'resolve_approval', approval_id: approval.approval_id, decision: decision, scope: scope || 'once' });
    hidePermBar(); owner.pendingApproval = null;
    if (decision === 'allow') {
      setApprovalToken(chatId, approval.approval_id); owner.resumeApprovedTurn = true;
      owner.approvalResumeTurnId = (owner.run && owner.run.turnId) || approval.task_id || '';
      const grantedScope = (data.approval && data.approval.scope) || 'once';
      toast(grantedScope === 'project'
        ? pageT('permissions.toast_project', 'Allowed in this project: reads and in-project file writes. Deletes, moves, commands, network, and payments still ask.')
        : (grantedScope === 'request'
          ? pageT('permissions.toast_request', 'Approved the file changes this request already planned. Anything it plans later still asks.')
          : (grantedScope === 'task'
            ? pageT('permissions.toast_task', 'Approved for this task. Later edit batches still need their own review.')
            : pageT('permissions.toast_once', 'Approved once for this exact action.'))));
      if (!isChatBusy(chatId)) resumeApprovedTurn(chatId);
    } else {
      releaseApprovalTokenFor(chatId); owner.resumeApprovedTurn = false; owner.approvalResumeTurnId = '';
      if (owner.run) { owner.run.permission = false; owner.run.permissionDenied = true; owner.run.action = pageT('run.permission_denied', 'Permission denied'); renderCard(owner.run); }
      toast(pageT('permissions.denied_toast', 'Denied. The action did not run.'));
    }
  } catch (e) { toast(pageT('permissions.resolve_failed_prefix', 'Could not resolve approval: ') + e.message); }
}
if (permBarEl) {
  document.getElementById('permOnce').addEventListener('click', () => resolvePermission('allow', 'once'));
  document.getElementById('permTask').addEventListener('click', () => resolvePermission('allow', 'task'));
  document.getElementById('permRequest').addEventListener('click', () => resolvePermission('allow', 'request'));
  // Server-side, per-project, and narrow: the grant lives on the project entry the controller checks
  // on every later call. There is deliberately no browser-side "always allow" flag behind this.
  document.getElementById('permProject').addEventListener('click', () => resolvePermission('allow', 'project'));
  // The chat-scoped standing approval: server-minted through /api/mode (grant_chat_workspace),
  // bound to THIS chat and its trusted workspace, reads and ordinary edits only. After minting,
  // the pending approval resolves once so this exact action still completes under it.
  const chatGrantBtn = document.getElementById('permChat');
  if (chatGrantBtn) chatGrantBtn.addEventListener('click', async () => {
    const chatId = displayedChat, owner = chatState(chatId);
    // The SERVER owns the chat's workspace binding; a workspace root learned from ledger
    // events is a HINT, not a precondition. Sending it when known preserves the mismatch
    // check; sending it empty lets the authoritative binding decide -- so the FIRST action
    // in a chat can be approved immediately instead of being refused until "one action has
    // run in this chat".
    try {
      await postMode({ op: 'grant_chat_workspace', workspace_root: String(owner.workspaceRoot || '') });
      toast(pageT('permissions.chat_toast', 'Allowed workspace reads and edits in this chat. Deletes, overwrites, commands, network, secrets, and payments still ask.'));
      await resolvePermission('allow', 'once');
    } catch (e) {
      const reason = (e && e.body && e.body.reason) || '';
      if (reason === 'unbound' || reason === 'missing_chat') {
        // No project folder is bound to this chat, so there is no workspace to grant in.
        // Explain that and offer the EXISTING folder selection (move-to-project menu:
        // existing projects plus "+ New project…" folder picker). The pending approval
        // bar and task stay exactly as they are -- after binding a folder the operator
        // can click this same button and the task continues.
        toast(pageT('permissions.needs_project_folder', 'This chat has no project folder yet, so there is no workspace to allow edits in. Pick a project folder first — your pending request waits.'));
        openProjectMenu(chatId, chatGrantBtn);
        return;
      }
      toast(pageT('permissions.chat_grant_failed_prefix', 'Could not grant chat workspace approval: ') + e.message);
    }
  });
  document.getElementById('permReview').addEventListener('click', () => { permDetailEl.hidden = !permDetailEl.hidden; });
  document.getElementById('permDeny').addEventListener('click', () => resolvePermission('deny', 'once'));
}

panelBtnEl.addEventListener('click', togglePanel);
(function wirePins() {
  const pb = document.getElementById('pinBtn'); if (pb) pb.addEventListener('click', openPinPanel);
  const pc = document.getElementById('pinClose'); if (pc) pc.addEventListener('click', closePinPanel);
  const po = document.getElementById('pinOverlay'); if (po) po.addEventListener('click', (e) => { if (e.target === po) closePinPanel(); });
})();
// ---- Export: the WHOLE conversation, from the server-side persisted transcript ----
// One dialog per chat. The chat id is owned at the moment the dialog opens and is never re-read
// afterwards, so switching chats mid-dialog cannot retarget someone else's transcript into this
// file; the dialog names the chat it will export so that binding is visible, not implied.
(function wireExport() {
  const overlay = document.getElementById('exportOverlay');
  const btn = document.getElementById('exportBtn');
  if (!overlay || !btn) return;
  const el = (id) => document.getElementById(id);
  const statusEl = el('exportStatus'), previewEl = el('exportPreview');
  let exportChat = '';
  let exportAborter = null;
  let exportBusy = false, nativeExportBusy = false;
  const closeExport = () => { if (nativeExportBusy) return; if (exportAborter) { try { exportAborter.abort(); } catch (e) {} exportAborter = null; } overlay.hidden = true; };
  const params = (extra) => '/api/chat/export?session=' + encodeURIComponent(exportChat)
    + '&format=' + el('exportFormat').value
    + '&timestamps=' + (el('exportTimestamps').checked ? 1 : 0)
    + '&no_attachments=' + (el('exportAttachments').checked ? 0 : 1)
    + (extra || '');
  async function refreshPreview() {
    if (!exportChat) { previewEl.textContent = 'This chat has nothing persisted to export yet — send a message first.'; return; }
    previewEl.textContent = 'Counting…';
    try {
      const r = await fetch(params('&preview=1'));
      const j = await r.json();
      if (!r.ok || !j.ok) { previewEl.textContent = (j && j.message) ? j.message : 'Could not count this chat.'; return; }
      previewEl.textContent = j.message_count + ' messages in ' + j.turns + ' turns'
        + ' · attachment references: ' + j.attachment_items
        + ' · artifact cards: ' + j.artifact_cards
        + (j.unavailable_by_policy ? ' · unavailable by privacy policy: ' + j.unavailable_by_policy : '')
        + '\nComplete transcript: ' + (j.complete ? 'yes — collected under a server-side snapshot' : 'no')
        + '.\nNot included: ' + (j.excluded || []).join('; ') + '.';
    } catch (e) { previewEl.textContent = 'Could not reach the export door.'; }
  }
  function openExport() {
    exportChat = displayedChat || '';
    statusEl.textContent = '';
    el('exportChatName').textContent = 'Chat: ' + (exportChat ? (chatTitleFor(exportChat) || exportChat) : '(unsaved)');
    overlay.hidden = false;
    refreshPreview();
  }
  async function runExport() {
    if (exportBusy) return;
    if (!exportChat) { statusEl.textContent = 'This chat has nothing persisted to export yet — send a message first.'; return; }
    const fmt = el('exportFormat').value;
    statusEl.textContent = 'Collecting…';
    exportBusy = true; el('exportGo').disabled = true;
    try {
      const nativeApi = window.pywebview && window.pywebview.api;
      if (nativeApi) {
        if (typeof nativeApi.save_chat_export !== 'function') throw new Error('This app build cannot save chat exports. Update the app and try again.');
        nativeExportBusy = true;
        const result = await nativeApi.save_chat_export(exportChat, fmt,
          el('exportTimestamps').checked, el('exportAttachments').checked);
        if (!result.ok && !result.cancelled) throw new Error(result.message || result.error || 'Export save failed');
        statusEl.textContent = result.cancelled ? 'Export cancelled.' : 'Export saved.';
        if (result.ok) overlay.hidden = true;
        return;
      }
      exportAborter = new AbortController();
      const r = await fetch(params(''), { signal: exportAborter.signal });
      const closed = exportAborter === null;   // closed while the response was in flight
      exportAborter = null;
      if (closed) return;
      if (!r.ok) {
        let msg = 'Export failed (' + r.status + ').';
        try { const j = await r.json(); if (j && j.message) msg = j.message; } catch (e) {}
        statusEl.textContent = msg;
        return;
      }
      const blob = await r.blob();
      const cd = r.headers.get('Content-Disposition') || '';
      const name = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name ? decodeURIComponent(name[1]) : ('vool-chat.' + fmt);
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      statusEl.textContent = 'Export downloaded.';
      overlay.hidden = true;
    } catch (e) {
      exportAborter = null;
      statusEl.textContent = (e && e.name === 'AbortError') ? 'Export cancelled.' : (e && e.message ? e.message : 'Export failed.');
    } finally {
      exportBusy = false; nativeExportBusy = false; el('exportGo').disabled = false;
    }
  }
  btn.addEventListener('click', openExport);
  el('exportClose').addEventListener('click', closeExport);
  el('exportCancel').addEventListener('click', closeExport);
  el('exportGo').addEventListener('click', runExport);
  ['exportFormat', 'exportTimestamps', 'exportAttachments'].forEach((id) => {
    const c = el(id); if (c) c.addEventListener('change', () => { if (!overlay.hidden) refreshPreview(); });
  });
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeExport(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !overlay.hidden) closeExport(); });
})();
xpCloseEl.addEventListener('click', closePanel);
// ---- Search wiring ----
if (searchBtnEl) searchBtnEl.addEventListener('click', () => (chatSearchOpen() ? closeChatSearch() : openChatSearch()));
if (searchInputEl) {
  let pending = null;
  searchInputEl.addEventListener('input', () => {
    if (chatSearchTimer) clearTimeout(chatSearchTimer);
    chatSearchTimer = setTimeout(() => { chatSearchTimer = null; runChatSearch(false); }, SEARCH_DEBOUNCE_MS);
  });
  searchInputEl.addEventListener('keydown', (e) => {
    // Enter walks the results; it must never reach the composer and send a turn.
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation();
      if (chatSearchTimer) { clearTimeout(chatSearchTimer); chatSearchTimer = null; runChatSearch(false); }
      else if (chatSearchHits.length) stepChatSearch(e.shiftKey ? -1 : 1);
      else runChatSearch(false);
      return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeChatSearch(); }
  });
  void pending;
}
if (searchNextEl) searchNextEl.addEventListener('click', () => stepChatSearch(1));
if (searchPrevEl) searchPrevEl.addEventListener('click', () => stepChatSearch(-1));
const searchClearEl = document.getElementById('searchClear');
if (searchClearEl) searchClearEl.addEventListener('click', clearChatSearch);
const searchCloseEl = document.getElementById('searchClose');
if (searchCloseEl) searchCloseEl.addEventListener('click', closeChatSearch);
// Cmd/Ctrl+F opens the transcript search. The browser's own find is still reachable with a second
// press once this bar is focused, because Escape closes it and returns the key to the page.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'f' || e.key === 'F')) {
    if (searchBarEl) { e.preventDefault(); openChatSearch(); }
  }
});
const xpSearchInputEl = document.getElementById('xpSearchInput');
if (xpSearchInputEl) {
  let activityTimer = null;
  xpSearchInputEl.addEventListener('input', () => {
    if (activityTimer) clearTimeout(activityTimer);
    activityTimer = setTimeout(() => { activityTimer = null; runActivitySearch(false); }, SEARCH_DEBOUNCE_MS);
  });
  xpSearchInputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation();
      if (activityTimer) { clearTimeout(activityTimer); activityTimer = null; runActivitySearch(false); }
      else stepActivitySearch(e.shiftKey ? -1 : 1);
      return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); clearActivitySearch(); }
  });
}
const xpSearchNextEl = document.getElementById('xpSearchNext');
if (xpSearchNextEl) xpSearchNextEl.addEventListener('click', () => stepActivitySearch(1));
const xpSearchPrevEl = document.getElementById('xpSearchPrev');
if (xpSearchPrevEl) xpSearchPrevEl.addEventListener('click', () => stepActivitySearch(-1));
const xpSearchClearEl = document.getElementById('xpSearchClear');
if (xpSearchClearEl) xpSearchClearEl.addEventListener('click', clearActivitySearch);
// The panel rebuilds its body on every render -- and some of those renders finish asynchronously,
// after the receipts for a scope have been fetched. Watching the body covers every one of those
// paths without threading a call through each of them. The observer is disconnected while it
// paints, so the marks it inserts cannot retrigger it.
if (xpBodyEl && typeof MutationObserver === 'function') {
  const activityWatcher = new MutationObserver(() => {
    if (activitySearchQuery().length < SEARCH_MIN_CHARS) return;
    if (xpBodyEl.querySelector('mark.cs-hit')) return;   // already painted for this render
    activityWatcher.disconnect();
    try { runActivitySearch(true); }
    finally { activityWatcher.observe(xpBodyEl, { childList: true, subtree: true }); }
  });
  activityWatcher.observe(xpBodyEl, { childList: true, subtree: true });
}
// Scope selector. Defaults to the open chat; the global view stays available, just not automatic.
const xpScopeEl = document.getElementById('xpScope');
if (xpScopeEl) {
  PANEL_SCOPES.forEach((s) => {
    const opt = document.createElement('option');
    opt.value = s[0]; opt.textContent = s[1];
    if (s[0] === panelScope) opt.selected = true;
    xpScopeEl.appendChild(opt);
  });
  xpScopeEl.addEventListener('change', () => {
    panelScope = xpScopeEl.value;
    localStorage.setItem('vool_panel_scope', panelScope);
    scopedEvidence = { key: '', loading: false, sessions: [], dropped: 0 };   // force a reload
    lastTabSig = '';                                                          // counts change with scope
    renderPanel();
  });
}
// Copy the whole active tab. The Activity ledger and the Event log are the evidence a user is
// asked to attach to a bug report, and hand-selecting a scrolling disclosure tree is not workable.
// Routed through the same serializer as the in-body "Copy all": reading `xpBodyEl.innerText` alone
// skipped everything inside a collapsed <details>, so a copy taken from the default panel handed
// over category headlines and none of the evidence under them.
const xpCopyEl = document.getElementById('xpCopy');
if (xpCopyEl) xpCopyEl.addEventListener('click', () => copyPanelEvidence(xpCopyEl, panelTab || 'Activity'));
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

if (cloudPillEl) cloudPillEl.addEventListener('click', () => document.getElementById('modelBtn').click());
initComposerControls();
buildTabs();
loadSessions();
showEmpty();      // paint the welcome instantly so boot never flashes blank; restoreCurrent swaps in the transcript if the session has one
restoreCurrent();
reflectComposer();
syncComposerTail();
// The button's width also changes without a resize or a label change -- a font finishing loading, a
// browser zoom. Watching the element itself covers those without a polling timer.
if (typeof ResizeObserver === 'function' && sendEl) new ResizeObserver(syncComposerTail).observe(sendEl);
refreshQueue();
refreshCredentials();
refreshCloudStatus();
renderLocalModels();   // installed local models are selectable with or without a cloud key
hydrateModelFromServer();   // A11: adopt the server's authoritative selection, never a stale cache
window.addEventListener('load', () => { if (window.VoolCompanionBoot) window.VoolCompanionBoot(); });  // companion layer (idempotent; boots after the fragment script evaluates)
setInterval(refreshCloudStatus, 60000);  // cheap cache-only poll; keeps the pill fresh
refreshSidebarActivity();   // seed activityHistory before the first repaint so boot never reads every chat as idle
// ---- Signed-updater chip (2026-09-01): one authority, two presses, plain language ----
const updateChip = document.getElementById('updateChip');
const updateChipTxt = document.getElementById('updateChipTxt');
const updatePop = document.getElementById('updatePop');
const updateVersionEl = document.getElementById('updateVersion');
const updateNotesEl = document.getElementById('updateNotes');
const updateMessageEl = document.getElementById('updateMessage');
const updateBar = document.getElementById('updateBar');
const updateBarFill = document.getElementById('updateBarFill');
const updateActionBtn = document.getElementById('updateActionBtn');
const updateCheckBtn = document.getElementById('updateCheckBtn');
// The updater's state machine names stay exactly as shipped (phase keys below); only
// their display words resolve through the catalog's update.* keys.
const UPDATE_LABELS = {
  ready: pageT('update.ready', 'Update ready'), downloading: pageT('update.downloading', 'Downloading…'), verifying: pageT('update.verifying', 'Verifying…'),
  ready_to_restart: pageT('update.ready_to_restart', 'Ready to restart'), installing: pageT('update.installing', 'Installing…'), restarting: pageT('update.restarting', 'Restarting…'),
  done: pageT('update.done', 'Updated'), rolled_back: pageT('update.rolled_back', 'Rolled back'), failed: pageT('update.failed', 'Update failed'),
  unavailable: pageT('update.unavailable', 'Updates unavailable'), checking: pageT('update.checking', 'Checking…'), up_to_date: pageT('update.up_to_date', 'Up to date'),
};
const UPDATE_BUSY = new Set(['checking', 'downloading', 'verifying', 'installing', 'restarting']);
let _updateStatus = null;
let _updateFast = false;

function renderUpdateChip(payload) {
  _updateStatus = payload;
  const phase = payload && payload.phase ? payload.phase : 'idle';
  if (!payload || (phase === 'idle' || phase === 'up_to_date' || phase === 'checking')) {
    // idle/up-to-date keep the header clean; the chip reappears the moment there is news
    updateChip.hidden = !(phase === 'up_to_date' && false);
    if (phase === 'checking') { updateChip.hidden = false; }
    if (updateChip.hidden) { updatePop.hidden = true; return; }
  }
  updateChip.hidden = false;
  const label = UPDATE_LABELS[phase] || 'Update';
  let text = label;
  if (phase === 'downloading' && payload.progress_percent != null) text = pageTF('update.downloading_pct', 'Downloading {percent}%', { percent: payload.progress_percent });
  if (phase === 'ready' && payload.target_version) text = pageTF('update.ready_version', 'Update ready · {version}', { version: payload.target_version });
  updateChipTxt.textContent = text;
  updateChip.classList.remove('upd-ready', 'upd-working', 'upd-ok', 'upd-warn', 'upd-off');
  if (phase === 'ready' || phase === 'ready_to_restart') updateChip.classList.add('upd-ready');
  else if (UPDATE_BUSY.has(phase)) updateChip.classList.add('upd-working');
  else if (phase === 'done') updateChip.classList.add('upd-ok');
  else if (phase === 'rolled_back' || phase === 'failed') updateChip.classList.add('upd-warn');
  else if (phase === 'unavailable') updateChip.classList.add('upd-off');
  updateVersionEl.textContent = payload.target_version ? pageTF('update.version_line', 'Version {version}', { version: payload.target_version }) : '';
  updateNotesEl.textContent = (payload.notes || '').trim();
  updateMessageEl.textContent = payload.message || '';
  updateBar.hidden = !(phase === 'downloading' && payload.progress_percent != null);
  updateBarFill.style.width = `${payload.progress_percent || 0}%`;
  updateActionBtn.hidden = true; updateCheckBtn.hidden = true;
  if (phase === 'ready') { updateActionBtn.hidden = false; updateActionBtn.textContent = pageT('update.action_update', 'Update'); }
  else if (phase === 'ready_to_restart') { updateActionBtn.hidden = false; updateActionBtn.textContent = pageT('update.action_restart', 'Restart'); }
  else if (['done', 'rolled_back', 'failed'].includes(phase)) { updateCheckBtn.hidden = false; }
  else if (phase === 'unavailable') { updateCheckBtn.hidden = true; }
  _updateFast = UPDATE_BUSY.has(phase);
}

async function refreshUpdateStatus() {
  try {
    const r = await fetch('/api/update/status', { credentials: 'same-origin' });
    if (!r.ok) return;
    renderUpdateChip(await r.json());
  } catch (e) { /* the chip never breaks the chat */ }
}

async function pressUpdate(action) {
  const url = action === 'restart' ? '/api/update/restart' : '/api/update/install';
  try {
    const r = await fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const payload = await r.json().catch(() => ({}));
    if (!r.ok && payload && payload.detail) { updateMessageEl.textContent = payload.detail; }
  } catch (e) { /* transient; the poll will re-sync */ }
  setTimeout(refreshUpdateStatus, 300);
}

if (updateChip) {
  updateChip.addEventListener('click', () => {
    updatePop.classList.toggle('open');   // the shell's popover convention: .open, not [hidden]
    updateChip.setAttribute('aria-expanded', String(updatePop.classList.contains('open')));
  });
  document.addEventListener('click', (e) => {
    if (!updatePop.classList.contains('open')) return;
    if (!document.getElementById('updateCtrl').contains(e.target)) { updatePop.classList.remove('open'); }
  });
}
if (updateActionBtn) updateActionBtn.addEventListener('click', () => {
  pressUpdate(updateActionBtn.textContent.trim().toLowerCase() === 'restart' ? 'restart' : 'install');
});
if (updateCheckBtn) updateCheckBtn.addEventListener('click', async () => {
  try { await fetch('/api/update/check', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (e) {}
  setTimeout(refreshUpdateStatus, 400);
});
setInterval(() => { if (!document.hidden) refreshUpdateStatus(); }, _updateFast ? 700 : 3500);
refreshUpdateStatus();

setInterval(maybeRepaintSidebarLifecycle, 1200);  // local-only; repaints only when derived state actually changed
setInterval(refreshSidebarActivity, 5000);        // the only tick that touches the network
document.addEventListener('visibilitychange', () => {
  // Regaining focus on the chat already on screen is the same acknowledgment as opening it.
  if (!document.hidden && displayedChat) markChatSeen(displayedChat);
  maybeRepaintSidebarLifecycle();
});

// ---- Page-action adapter (the ONE surface appended fragments may drive) ----------------------
// Fragments (palette, notifications, ...) never reach into page internals; they call these named
// actions, so a monolith refactor breaks HERE, visibly, instead of inside a fragment. Stop goes
// through the displayed run's own Stop control -- the exact code path a user's click takes
// (server-first cancel by turn_id, then stream abort) -- never a second cancel implementation.
window.VoolPageActions = Object.freeze({
  newChat: () => newChat(),
  togglePanel: () => togglePanel(),
  openPanel: () => openPanel(),
  openSettings: () => openSettings(),
  openSetup: (step) => openSetup(typeof step === 'string' ? step : ''),
  closeSettings: () => closeSettings(),
  closeSettingsFrame: () => closeSettingsFrame(),
  openModelMenu: () => { const btn = document.getElementById('modelBtn'); if (btn) btn.click(); },
  openModeMenu: () => { const btn = document.getElementById('modeBtn'); if (btn) btn.click(); },
  openCouncil: () => { if (typeof openCouncil === 'function') openCouncil(); },
  openPlugins: () => { if (typeof openPlugins === 'function') openPlugins(); },
  openSkills: () => { if (typeof openSkills === 'function') openSkills(); },
  openFiles: () => { if (typeof openFiles === 'function') openFiles(); },
  focusComposer: () => { if (inputEl) inputEl.focus(); },
  toast: (text) => toast(String(text == null ? '' : text)),
  displayedChat: () => displayedChat,
  chatLog: () => logEl,
  // Close EVERY composer while a council owns the machine's model pin, and hand them back
  // when it does not. The caller passes what the SERVER said; the page decides what
  // "closed" looks like. It is never asked which chat -- the pin is not per chat.
  setCouncilLock: (locked, note) => setCouncilLock(locked, note),
  sessionMeta: (chatId) => {
    const row = (_lastSessions || []).find((s) => s.session_id === String(chatId || ''));
    return row ? { color: row.color || '', emoji: row.emoji || '', title: row.title || '' } : null;
  },
  liveStopControl: () => {
    const stops = Array.from(logEl ? logEl.querySelectorAll('.task-card .tc-stop') : []);
    return stops.reverse().find((btn) => btn.offsetParent !== null && btn.style.display !== 'none') || null;
  },
  hasLiveRun: function () { return this.liveStopControl() !== null; },
  stopDisplayedRun: function () {
    const stop = this.liveStopControl();
    if (stop) { stop.click(); return true; }
    return false;
  },
  // MODEL RADAR actions (kept last so the compact-adapter contract above holds). Both go
  // through the page's own authorities: tryModelOnce arms a one-turn grant consumed by
  // the next substantive send (never persisted, never a pin), and pinCloudModel is the
  // SAME switchCloudModel the dropdown uses — server-side A11 paid classification,
  // catalog membership and save-verify all still apply.
  tryModelOnce: (model, label) => armTryOnce(displayedChat, model, label),
  pinCloudModel: (id, label) => switchCloudModel(String(id || ''), String(label || id || ''), null),
  // PA Contacts (core/contacts_fragment.py): open the owner's Contacts overlay, and put one exact saved reference into
  // the composer at the caret. Inserting never sends.
  openContacts: () => { if (window.VoolContacts) window.VoolContacts.open(); },
  insertComposerText: (text) => {
    if (!inputEl) return false;
    const value = String(text == null ? '' : text);
    const start = typeof inputEl.selectionStart === 'number' ? inputEl.selectionStart : inputEl.value.length;
    const end = typeof inputEl.selectionEnd === 'number' ? inputEl.selectionEnd : start;
    const before = inputEl.value.slice(0, start);
    const joiner = before && before.slice(-1).trim() !== '' ? ' ' : '';
    inputEl.value = before + joiner + value + inputEl.value.slice(end);
    const caret = (before + joiner + value).length;
    inputEl.focus();
    try { inputEl.setSelectionRange(caret, caret); } catch (e) {}
    inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },
});

// --- Setup line ------------------------------------------------------------------------------
// The guided setup lives on its own page (/setup), presented the way Settings is. The chat carries
// at most ONE small dismissible line, and only on a fresh profile: /api/setup/state decides
// (derived state, core/setup_progress.py). No polling: fetch at boot, on focus/visibility, and
// after the setup frame closes. Dismissing is the one "don't remind me" choice, persisted as an
// ordinary preference through the prefs door; every step stays reachable as a Settings row.
async function setupLineLoad() {
  const line = document.getElementById('setupLine');
  if (!line) return;
  try {
    const r = await fetch('/api/setup/state');
    if (!r.ok) { line.hidden = true; return; }
    const d = await r.json();
    const show = !!(d && d.show_chat_line);
    const text = document.getElementById('setupLineText');
    if (text && d && typeof d.done_count === 'number' && d.done_count > 0) {
      text.textContent = 'Finish setting up VOOL \u2014 ' + d.done_count + ' of ' + d.total + ' done.';
    }
    line.hidden = !show;
  } catch (e) { line.hidden = true; }
  finally { line.dataset.loaded = '1'; }   // the markup starts hidden; this says the decision was made
}
(function setupLineBoot() {
  const line = document.getElementById('setupLine');
  if (!line) return;
  const open = document.getElementById('setupLineOpen');
  if (open) open.addEventListener('click', () => openSetup());
  const hide = document.getElementById('setupLineHide');
  if (hide) hide.addEventListener('click', async () => {
    line.hidden = true;
    try {
      await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setup_dismissed: true }) });
    } catch (e) { /* the authority decides; the next load shows its truth */ }
    setupLineLoad();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) setupLineLoad(); });
  window.addEventListener('focus', () => setupLineLoad());
  // Boot fetch: never blocking, never stealing focus — the composer keeps focus.
  setupLineLoad();
})();
</script>
</body>
</html>
"""


def _page_fragments() -> tuple[str, ...]:
    """Every appended fragment, in evaluation order.

    The fragment law (proven by the companion layer): each surface is a self-contained
    <style>+<script> IIFE exposing ONE `window.Vool*` namespace, appended before </body> so it
    evaluates after the page script it observes, and touching the monolith only at named call
    sites. Order matters only where a fragment consumes another's namespace: chips carry no
    dependencies and load first so every later fragment (and the page's own renderers) can call
    `VoolChips.html`; the companion stays last, exactly where it always evaluated.
    """
    from core.chat_visuals_fragment import render_chat_visuals_fragment
    from core.command_palette_fragment import render_palette_fragment
    from core.companion_drawer_fragment import render_companion_drawer_fragment
    from core.companion_presentation_fragment import render_companion_fragment
    from core.composer_extras_fragment import render_composer_extras_fragment
    from core.contacts_fragment import render_contacts_fragment
    from core.council_chat_card_fragment import render_council_card_fragment
    from core.council_fragment import render_council_fragment
    from core.model_radar_fragment import render_model_radar_fragment
    from core.notification_fragment import render_notification_fragment
    from core.price_safety_fragment import render_price_safety_fragment
    from core.settings_extras_fragment import render_settings_extras_fragment
    from core.ui_chip_fragment import render_chip_fragment
    from core.wallet_fragment import render_wallet_fragment

    return (
        render_chip_fragment(),
        render_chat_visuals_fragment(),
        render_palette_fragment(),
        render_composer_extras_fragment(),
        render_price_safety_fragment(),
        render_notification_fragment(),
        render_model_radar_fragment(),
        render_settings_extras_fragment(),
        render_companion_drawer_fragment(),
        render_council_fragment(),
        render_council_card_fragment(),
        render_companion_fragment(),
        render_wallet_fragment(),
        render_contacts_fragment(),
    )



def render_vool_chat_html(*, build_commit: str = "", ui_locale: str = "en") -> str:
    # The page embeds the approved artwork so branding works offline in the native shell.
    from base64 import b64encode
    from pathlib import Path

    from core.i18n.catalog import catalog_for
    from core.i18n.locales import get_locale

    logo = Path(__file__).with_name("assets") / "vool-logo-dark.png"
    logo_uri = "data:image/png;base64," + b64encode(logo.read_bytes()).decode("ascii")
    # The commit binds this page to the runtime serving it.
    # The header version is fetched live over the API, so without this the page cannot tell that
    # the daemon has been upgraded underneath it -- a window left open across an upgrade displayed
    # the new commit while executing old JavaScript. Everything else reaches the page over the
    # same-origin API, not the template.
    tag = catalog_for(ui_locale).locale
    spec = get_locale(tag)
    page = (
        _VOOL_CHAT_HTML.replace("__PAGE_BUILD_COMMIT__", str(build_commit or "").strip())
        .replace("__VOOL_LOGO_URI__", logo_uri)
        .replace("</body>", "".join(_page_fragments()) + "</body>")
    )
    # The UI locale rides the same deterministic i18n authority the Settings selector drives
    # (cookie-persisted, core/i18n catalogs). The bootstrap's JS body is prepended INSIDE the
    # house script (the page's first <script> -- the council page-script law pins the house
    # script evaluating first), so VOOLT is defined before any of the house script's own
    # statements evaluate while the document's script order is unchanged; the DOM pass waits
    # for DOMContentLoaded when the document is still loading. This is the APP UI language
    # only -- never the model answer language, never the dictation recognition locale.
    # (Goal 2, 2026-09-18: the head-injected element preceded the house script and broke the
    # council script-order law; composing the body into the house script repairs it.)
    from core.i18n.page_bundle import i18n_bootstrap_js

    return (
        page.replace(
            '<html lang="en">',
            f'<html lang="{tag}" dir="{spec.direction if spec else "ltr"}">',
        )
        .replace(
            "<script>\n",
            "<script>\n" + i18n_bootstrap_js(tag) + "\n",
            1,
        )
    )
