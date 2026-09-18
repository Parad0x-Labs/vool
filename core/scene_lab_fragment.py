"""SCENE LAB — a TEMPORARY dev/QA control bar for companion presentation review.

Operator decision (2026-08-28): keep the ux-pass1 Scene Lab in the app while iterating, built so
removal before beta is exactly ONE line — delete `render_scene_lab_fragment()` from
`_page_fragments()` in core/vool_chat_page.py and this whole surface (button, bar, all
puppeting) is gone; the engine's `_devPuppet` hook stays as an inert function.

Truth boundary, stated on the surface itself: the lab puppets the SPRITE'S PRESENTATION only —
the typed reducer, worker counts, transcripts, receipts, and every truth surface are untouched,
and the caption shows "· DEV" the whole time a puppet is active. "TRUTH" releases the puppet and
the sprite snaps back to the real resolved state.

Namespace `window.VoolSceneLab`; prefix `vl-`.
"""

from __future__ import annotations

_LAB_CSS = """
#vlBtn{background:transparent;color:var(--muted,#9aa1af);border:1px dashed var(--border,#262b35);
  border-radius:8px;padding:6px 10px;font:inherit;font-size:13px;cursor:pointer}
#vlBtn:hover{color:var(--ink,#e8eaf0);border-color:var(--warn,#f0b429)}
#vlBar{position:fixed;left:16px;bottom:96px;z-index:1140;display:none;flex-direction:column;gap:6px;
  background:rgba(22,25,31,.94);border:1px dashed var(--warn,#f0b429);border-radius:12px;padding:10px;
  max-width:230px}
#vlBar.vl-open{display:flex}
#vlBar b{font-size:9px;letter-spacing:.14em;color:var(--warn,#f0b429);font-family:ui-monospace,Menlo,monospace}
#vlBar .vl-row{display:flex;gap:4px;flex-wrap:wrap}
#vlBar button{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:7px;
  padding:4px 7px;font-size:10px;color:var(--muted,#9aa1af);cursor:pointer;font-family:ui-monospace,Menlo,monospace}
#vlBar button:hover{border-color:var(--accent,#5eead4);color:var(--ink,#e8eaf0)}
#vlBar button.vl-truth{border-color:var(--accent,#5eead4);color:var(--accent,#5eead4);font-weight:700}
#vlBar small{font-size:9px;color:var(--muted,#9aa1af)}
"""

_LAB_JS = """
(function(){
'use strict';
if (window.VoolSceneLab) return;

var STATES = ['idle','starting','thinking','tool','waiting','approval','retry','success','failure','cancelled','unknown'];
var SCENES = ['pair','huddle'];

var btn = document.createElement('button');
btn.id = 'vlBtn';
btn.type = 'button';
btn.title = 'SCENE LAB (DEV) \\u2014 puppet the companion presentation for QA; runtime truth unaffected';
btn.setAttribute('aria-label', btn.title);
btn.textContent = '\\ud83c\\udfac';
var bar = document.createElement('div');
bar.id = 'vlBar';
bar.innerHTML = '<b>SCENE LAB \\u00b7 DEV</b>' +
  '<div class="vl-row" id="vlStates"></div>' +
  '<div class="vl-row" id="vlScenes"></div>' +
  '<div class="vl-row"><button type="button" class="vl-truth" id="vlTruth">TRUTH</button></div>' +
  '<small>Presentation puppet only \\u2014 reducer, workers, transcripts and receipts stay real. Removed before beta.</small>';
document.body.appendChild(bar);

function companion(){ return window.VoolCompanion || null; }
function puppet(spec){
  var c = companion();
  if (c && c._devPuppet) c._devPuppet(spec);
}
var statesRow = bar.querySelector('#vlStates');
STATES.forEach(function(state){
  var cell = document.createElement('button');
  cell.type = 'button';
  cell.textContent = state;
  cell.addEventListener('click', function(){ puppet({ state: state }); });
  statesRow.appendChild(cell);
});
var scenesRow = bar.querySelector('#vlScenes');
SCENES.forEach(function(scene){
  var cell = document.createElement('button');
  cell.type = 'button';
  cell.textContent = scene;
  cell.addEventListener('click', function(){ puppet({ scene: scene }); });
  scenesRow.appendChild(cell);
});
bar.querySelector('#vlTruth').addEventListener('click', function(){ puppet(null); });
btn.addEventListener('click', function(){ bar.classList.toggle('vl-open'); });

function mount(){
  var anchor = document.getElementById('vfBell') || document.getElementById('panelBtn');
  if (anchor && anchor.parentNode && !document.getElementById('vlBtn')) {
    anchor.parentNode.insertBefore(btn, anchor);
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();

window.VoolSceneLab = Object.freeze({
  open: function(){ bar.classList.add('vl-open'); },
  close: function(){ bar.classList.remove('vl-open'); },
});
})();
"""


def render_scene_lab_fragment() -> str:
    """The DEV scene lab as an appended fragment (delete its mount line for beta)."""
    return "<style>" + _LAB_CSS + "</style><script>" + _LAB_JS + "</script>"


__all__ = ["render_scene_lab_fragment"]
