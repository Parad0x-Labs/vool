"""Pet appearance controls, reached from Settings or the command palette.

The chat renderer remains the sole owner of pet presentation. General preferences,
memory and privacy use their existing Settings surfaces. No prototype controls or
extra header buttons are mounted here.
"""

from __future__ import annotations

_DRAWER_CSS = """
#vcdDrawer{position:fixed;top:0;right:0;bottom:0;width:min(400px,100vw);background:var(--panel,#161a21);
  border-left:1px solid var(--border,#262b35);z-index:1150;transform:translateX(105%);
  transition:transform .25s ease;display:flex;flex-direction:column}
@media (prefers-reduced-motion: reduce){#vcdDrawer{transition:none}}
#vcdDrawer[hidden]{display:none}
#vcdDrawer.vcd-show{transform:none}
.vcd-head{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--border,#262b35)}
.vcd-head b{font-size:14px}
.vcd-head .vcd-x{margin-left:auto;background:none;border:none;color:var(--muted,#9aa1af);font-size:15px;cursor:pointer}
.vcd-head .vcd-x:hover{color:var(--ink,#e8eaf0)}
.vcd-body{flex:1;overflow-y:auto;padding:14px 16px;font-size:13px;color:var(--ink,#e8eaf0)}
.vcd-field{margin-bottom:14px}
.vcd-field label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted,#9aa1af);margin-bottom:5px}
.vcd-seg{display:flex;background:var(--field,#1d2129);border-radius:8px;padding:3px;gap:2px}
.vcd-seg button{flex:1;padding:5px;border-radius:6px;font-size:12px;color:var(--muted,#9aa1af);background:none;border:none;cursor:pointer}
.vcd-seg button.vcd-on{background:var(--panel,#161a21);color:var(--ink,#e8eaf0)}
.vcd-seg button:disabled{opacity:.45;cursor:default}
.vcd-note{font-size:11px;color:var(--muted,#9aa1af);margin-top:12px;line-height:1.5}
.vcd-card{display:flex;gap:10px;align-items:center;background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  border-radius:9px;padding:8px 10px;margin-bottom:6px;width:100%;cursor:pointer;color:var(--ink,#e8eaf0);text-align:left;font:inherit;font-size:12.5px}
.vcd-card.vcd-on{outline:1px solid var(--accent,#5eead4)}
.vcd-card small{color:var(--muted,#9aa1af)}
#vcdDrawer kbd,#sidebar kbd{font-family:ui-monospace,Menlo,monospace;font-size:10px;background:var(--bg,#101318);
  border:1px solid var(--border,#262b35);border-radius:4px;padding:1px 5px;color:var(--muted,#9aa1af)}
#newChat{display:flex;align-items:center;gap:6px}
#newChat kbd{margin-left:auto}
"""

_DRAWER_JS = """
(function(){
'use strict';
if (window.VoolCompanionDrawer) return;

var drawer = null;
var returnFocus = null;

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
  });
}

function petTab(){
  var world = window.VoolCompanionWorld || null;
  var companion = window.VoolCompanion || null;
  var characters = world && world.characters ? world.characters : {};
  var packs = world && world.packs ? world.packs : {};
  var pos = {};
  try { pos = JSON.parse(localStorage.getItem('vool_ninja_pos_v1') || '{}') || {}; } catch (e) {}
  var active = pos.personality || 'spark';
  var activePack = pos.pack || 'default';
  var html = '<div class="vcd-field"><label>Pet</label><div class="vcd-seg" id="vcdPetOnOff">' +
    '<button type="button" data-pet="on"' + (pos.hidden ? '' : ' class="vcd-on"') + '>On</button>' +
    '<button type="button" data-pet="off"' + (pos.hidden ? ' class="vcd-on"' : '') + '>Off</button></div></div>' +
    '<div class="vcd-field"><label>Character</label>';
  Object.keys(characters).forEach(function(id){
    var spec = characters[id] || {};
    html += '<button type="button" class="vcd-card' + (id === active ? ' vcd-on' : '') + '" data-char="' + esc(id) + '">' +
      '<b>' + esc(spec.name || id) + '</b></button>';
  });
  html += '</div><div class="vcd-field"><label>Style</label><div class="vcd-seg" id="vcdPacks">';
  Object.keys(packs).forEach(function(id){
    html += '<button type="button" data-pack="' + esc(id) + '"' + (id === activePack ? ' class="vcd-on"' : '') + '>' + esc((packs[id] || {}).label || id) + '</button>';
  });
  html += '</div></div>' +
    '<button type="button" class="vcd-card" id="vcdDetach"><b>Move to desktop</b><small>the pet lives on your desktop \\u00b7 drag it anywhere \\u00b7 right-click to return</small></button>' +
    '<p class="vcd-note">Appearance changes only the pet. Your tasks and their status stay the same.</p>';
  if (!companion) html += '<p class="vcd-note">Companion layer not loaded in this view.</p>';
  return html;
}

function renderTab(){
  var body = drawer.querySelector('.vcd-body');
  body.innerHTML = petTab();
  wireTab(body);
}

function wireTab(body){
  var onOff = body.querySelector('#vcdPetOnOff');
  if (onOff) onOff.addEventListener('click', async function(ev){
    var btn = ev.target.closest('button[data-pet]');
    var companion = window.VoolCompanion;
    if (!btn || !companion) return;
    if (btn.getAttribute('data-pet') === 'off' && companion.hideDesktopPet) await companion.hideDesktopPet();
    if (btn.getAttribute('data-pet') === 'on') {
      if (companion.hide) companion.hide(false);
      if (companion.dock) companion.dock();
    }
    renderTab();
  });
  body.querySelectorAll('.vcd-card[data-char]').forEach(function(card){
    card.addEventListener('click', function(){
      var companion = window.VoolCompanion;
      if (companion && companion.chooseCharacter) companion.chooseCharacter(card.getAttribute('data-char'));
      renderTab();
    });
  });
  var packsEl = body.querySelector('#vcdPacks');
  if (packsEl) packsEl.addEventListener('click', function(ev){
    var btn = ev.target.closest('button[data-pack]');
    var companion = window.VoolCompanion;
    if (!btn || !companion) return;
    if (companion.choosePack) companion.choosePack(btn.getAttribute('data-pack'));
    renderTab();
  });
  var detach = body.querySelector('#vcdDetach');
  if (detach) detach.addEventListener('click', function(){
    closeDrawer();
    var companion = window.VoolCompanion;
    if (companion && companion.detachDesktop) companion.detachDesktop();
  });

}

function buildDrawer(){
  drawer = document.createElement('div');
  drawer.id = 'vcdDrawer';
  drawer.setAttribute('role', 'dialog');
  drawer.setAttribute('aria-label', 'Pet appearance');
  drawer.hidden = true;
  drawer.innerHTML = '<div class="vcd-head"><b>Pet appearance</b>' +
    '<button type="button" class="vcd-x" title="Close (Esc)" aria-label="Close pet appearance">Close</button></div>' +
    '<div class="vcd-body"></div>';
  document.body.appendChild(drawer);
  drawer.querySelector('.vcd-x').addEventListener('click', closeDrawer);
}

function openDrawer(){
  if (!drawer) buildDrawer();
  if (!isOpen()) returnFocus = document.activeElement;
  drawer.hidden = false;
  drawer.classList.add('vcd-show');
  renderTab();
  drawer.querySelector('.vcd-x').focus();
  return true;
}
function closeDrawer(){
  if (drawer) { drawer.classList.remove('vcd-show'); drawer.hidden = true; }
  if (returnFocus && returnFocus.isConnected) returnFocus.focus();
}
function isOpen(){ return !!(drawer && drawer.classList.contains('vcd-show')); }
function toggleDrawer(){ if (isOpen()) closeDrawer(); else openDrawer(); }

function mountNewChatKbd(){
  var btn = document.getElementById('newChat');
  if (!btn || btn.querySelector('kbd')) return;
  var chip = document.createElement('kbd');
  chip.textContent = '\\u2318N';
  btn.appendChild(chip);
}

document.addEventListener('keydown', function(ev){
  if ((ev.metaKey || ev.ctrlKey) && ev.key === '/') { ev.preventDefault(); toggleDrawer(); return; }
  if (ev.key === 'Escape' && isOpen()) { ev.preventDefault(); ev.stopPropagation(); closeDrawer(); }
}, true);

function boot(){
  mountNewChatKbd();
  if (window.VoolPalette && window.VoolPalette.register) {
    try { window.VoolPalette.register('companion-drawer', 'Pet appearance', toggleDrawer, '\\u2318/'); } catch (e) {}
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

window.VoolCompanionDrawer = Object.freeze({
  open: openDrawer,
  close: closeDrawer,
  toggle: toggleDrawer,
  isOpen: isOpen,
  tab: function(){ return 'pet'; }
});
})();
"""


def render_companion_drawer_fragment() -> str:
    return "<style>" + _DRAWER_CSS + "</style>\n<script>" + _DRAWER_JS + "</script>"
