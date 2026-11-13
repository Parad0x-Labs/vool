"""The shared Settings look: cards with a header, one short line per setting, "Learn more" folds,
state chips and quiet line icons.

Every Settings page draws from this one block, so the pages match each other and the Crypto page
(which keeps its own classes for now and may switch to these). It only uses the palette variables
from core/ui_palette.py and the line icons from core/ui_icons.py (``class="vi" data-ico=...``),
so light and dark themes and every skin keep working. Colour is never the only signal: a chip
always carries a word, and the On/Off word sits beside every switch.
"""
from __future__ import annotations

SETTINGS_KIT_CSS = """
/* ---- Settings kit: cards ---- */
.vs-glance { display:flex; flex-wrap:wrap; gap:6px 8px; margin:-8px 0 18px; }
.vs-card { position:relative; border:1px solid var(--border); border-radius:14px; margin-bottom:16px; overflow:hidden;
  background:linear-gradient(180deg, rgba(var(--accent-rgb),.055), rgba(var(--accent-rgb),.015) 72px, transparent 160px), var(--panel); }
.vs-card-head { display:flex; align-items:center; gap:11px; padding:13px 16px 11px; border-bottom:1px solid var(--border); }
.vs-card-ico { flex:0 0 30px; width:30px; height:30px; display:inline-flex; align-items:center; justify-content:center;
  border-radius:9px; background:var(--accent-soft); color:var(--accent); border:1px solid rgba(var(--accent-rgb),.25); font-size:15px; }
.vs-card-ico.vi::before { margin:0; width:16px; height:16px; }
.vs-card-title { margin:0; font-size:14.5px; font-weight:700; letter-spacing:-.1px; color:var(--ink); flex:1 1 auto; min-width:0; }
.vs-card-chips { display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end; }
.vs-card .row { border-bottom:1px solid var(--border); padding:13px 16px; }
.vs-card .row:last-child { border-bottom:none; }
/* ---- one short line, and the rest behind Learn more ---- */
.vs-line { color:var(--muted); font-size:12.5px; line-height:1.45; margin-top:3px; }
.vs-learn { margin-top:4px; font-size:12.5px; color:var(--muted); }
.vs-learn > summary { display:inline-flex; align-items:center; gap:4px; cursor:pointer; color:var(--accent); font-size:12px; font-weight:600;
  list-style:none; border-radius:6px; padding:1px 2px; margin-inline-start:-2px; user-select:none; }
.vs-learn > summary::-webkit-details-marker { display:none; }
.vs-learn > summary::after { content:''; width:6px; height:6px; border-right:1.5px solid currentColor; border-bottom:1.5px solid currentColor;
  transform:rotate(45deg) translate(-1px,-1px); transition:transform .14s; margin-inline-start:3px; }
.vs-learn[open] > summary::after { transform:rotate(-135deg) translate(-1px,-1px); }
.vs-learn > summary:hover { text-decoration:underline; }
.vs-learn-body { margin-top:6px; padding:8px 11px; border-inline-start:2px solid rgba(var(--accent-rgb),.35); background:rgba(var(--accent-rgb),.04);
  border-radius:0 8px 8px 0; line-height:1.55; }
/* ---- state chips: a dot and a word, never colour alone ---- */
.vs-chip { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; font-weight:600; color:var(--muted);
  border:1px solid var(--border); border-radius:999px; padding:3px 10px 3px 8px; background:var(--bg); white-space:nowrap; }
.vs-chip::before { content:''; width:7px; height:7px; border-radius:50%; background:var(--muted); flex:0 0 auto; }
.vs-chip.on { color:var(--ok); border-color:rgba(var(--accent-rgb),.25); }
.vs-chip.on::before { background:var(--ok); }
.vs-chip.off::before { background:var(--muted); }
.vs-chip.warn { color:var(--warn); border-color:var(--warn); }
.vs-chip.warn::before { background:var(--warn); }
.vs-chip.info { color:var(--accent); }
.vs-chip.info::before { background:var(--accent); }
.vs-chip b { font-weight:700; color:var(--ink); }
/* ---- what the panels inside a card render: one type size, calm spacing ---- */
#paneBody .row-ctl, #paneBody .row-ctl p, #paneBody .row-ctl label, #paneBody .row-ctl li { font-size:13px; line-height:1.5; }
#paneBody .row-ctl p { margin:4px 0; }
#paneBody .row-ctl h2, #paneBody .row-ctl h3, #paneBody .row-ctl h4 { font-size:13.5px; margin:10px 0 4px; }
#paneBody .row.wide .row-ctl { padding-top:10px; }
#paneBody input[type=checkbox], #paneBody input[type=radio] { accent-color:var(--accent); }
/* A checkbox that is really an on/off setting looks like the other switches. */
#paneBody input.vs-switch { appearance:none; -webkit-appearance:none; position:relative; width:44px; height:25px; margin:0; flex:0 0 auto;
  background:var(--field); border:1px solid var(--border); border-radius:999px; cursor:pointer; transition:background .14s,border-color .14s; vertical-align:middle; }
#paneBody input.vs-switch::after { content:''; position:absolute; top:2px; left:2px; width:19px; height:19px; border-radius:50%; background:var(--muted); transition:transform .14s,background .14s; }
#paneBody input.vs-switch:checked { background:rgba(var(--accent-rgb),.22); border-color:var(--accent); }
#paneBody input.vs-switch:checked::after { transform:translateX(19px); background:var(--accent); }
#paneBody input.vs-switch:disabled { opacity:.45; cursor:not-allowed; }
a.vs-btn, .vs-btn { display:inline-flex; align-items:center; gap:6px; background:transparent; color:var(--ink); border:1px solid var(--border);
  border-radius:8px; padding:6px 13px; font:inherit; font-size:12.5px; font-weight:600; cursor:pointer; text-decoration:none; }
a.vs-btn:hover, .vs-btn:hover { border-color:var(--accent); }
[dir="rtl"] .vs-learn-body { border-radius:8px 0 0 8px; }
[dir="rtl"] #paneBody input.vs-switch:checked::after { transform:translateX(-19px); }
[dir="rtl"] #paneBody input.vs-switch::after { left:auto; right:2px; }
@media (max-width: 719.98px) { .vs-card-head { flex-wrap:wrap; } .vs-card-chips { justify-content:flex-start; width:100%; } }
"""
SETTINGS_KIT_CSS += """
.vs-line .vs-hint { margin-inline-end:6px; }
.vs-line .vs-learn { display:inline; margin:0; }
.vs-line .vs-learn[open] { display:block; }
.vs-learn-widget { margin-top:8px; }
.vs-sw-pair { display:inline-flex; align-items:center; gap:8px; }
.row-ctl a:not(.btn):not(.vs-btn) { color:var(--accent); }
"""
SETTINGS_KIT_CSS += """
/* ---- a fold: a whole sub-area (X on API Keys) kept closed until wanted ---- */
.vs-fold { border:1px solid var(--border); border-radius:12px; background:var(--bg); margin:6px 0; }
.vs-fold > summary { display:flex; align-items:center; gap:11px; padding:11px 14px; cursor:pointer; list-style:none; border-radius:12px; }
.vs-fold > summary::-webkit-details-marker { display:none; }
.vs-fold > summary::after { content:''; width:7px; height:7px; border-right:1.6px solid var(--muted); border-bottom:1.6px solid var(--muted);
  transform:rotate(45deg); margin-inline-start:auto; transition:transform .14s; flex:0 0 auto; }
.vs-fold[open] > summary::after { transform:rotate(-135deg); }
.vs-fold > summary:hover { background:var(--active); }
.vs-fold-text { display:flex; flex-direction:column; min-width:0; }
.vs-fold-title { font-weight:700; font-size:13.5px; color:var(--ink); }
.vs-fold-body { padding:4px 14px 14px; border-top:1px solid var(--border); }
"""
SETTINGS_KIT_CSS += """
/* The card header already names this panel. */
.vs-label-in-head:not(:has(.badge)) { display:none; }
.vs-label-in-head > span:first-child { display:none; }
"""
SETTINGS_KIT_CSS += """
.inline > .vs-learn[open] { flex-basis:100%; }
.inline > .vs-learn { align-self:center; }
"""
