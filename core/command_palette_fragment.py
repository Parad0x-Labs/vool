"""The command palette + global shortcuts + the Esc cascade — one keyboard-first fragment.

Ported from the ux-pass1 design authority (`#palOverlay` / `openPalette` / the ⌘K-⌘N-⌘J family /
the layered Esc rule) and bound to REAL page actions only — every entry calls a named
`window.VoolPageActions` or `window.VoolCompanion` capability; the prototype's demo rows
("Run demo task", "Simulate policy refusal") deliberately do not exist here.

The Esc cascade is the prototype's law made precise against this page's real overlays:

    overlay/popover open  -> the legacy closers own it (this fragment does NOTHING; it only
                             SNAPSHOTS, capture-phase, whether one was open at press time)
    nothing open, run live -> stop the displayed run through its own Stop control (the exact
                             user-click path: server-first cancel by turn_id, then abort)
    nothing at all        -> no-op

Namespace: `window.VoolPalette` (open/close/register/registerShortcut). Class prefix `vp-`.
Shortcut events targeting an editable element are ignored unless the combo carries meta/ctrl —
a bare key must never be stolen from the composer, and the companion's own element-scoped keys
(arrows/D/H/Space on the focused sprite) are untouched because they never bubble as ⌘-combos.
"""

from __future__ import annotations

_PALETTE_CSS = """
#vpHint{font-size:10px;color:var(--muted,#9aa1af);text-align:center;padding:3px 0 6px;font-family:ui-monospace,Menlo,monospace;letter-spacing:.03em}
#vpHint kbd{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:4px;padding:0 5px;font-size:9px}
@media(max-width:900px){#vpHint{display:none}}
#vpOverlay{position:fixed;inset:0;background:rgba(5,7,10,.6);z-index:1200;display:flex;
  align-items:flex-start;justify-content:center}
#vpOverlay[hidden]{display:none}
#vpPalette{margin-top:12vh;width:min(560px,92vw);background:var(--panel,#16191f);
  border:1px solid var(--border,#262b35);border-radius:14px;overflow:hidden;
  box-shadow:0 18px 60px rgba(0,0,0,.5);display:flex;flex-direction:column;max-height:60vh}
#vpInput{width:100%;background:none;border:none;outline:none;padding:15px 18px;font:inherit;
  font-size:15px;color:var(--ink,#e8eaf0);border-bottom:1px solid var(--border,#262b35)}
#vpList{overflow-y:auto;padding:6px 0}
.vp-item{padding:9px 18px;display:flex;gap:10px;align-items:center;cursor:pointer;
  color:var(--muted,#9aa1af);font-size:13px}
.vp-item.vp-sel,.vp-item:hover{background:var(--field,#1d2129);color:var(--ink,#e8eaf0)}
.vp-item .vp-kbd{margin-left:auto;font-family:'SF Mono',ui-monospace,Menlo,monospace;
  font-size:10px;background:var(--bg,#101216);border:1px solid var(--border,#262b35);
  border-radius:4px;padding:1px 6px;color:var(--muted,#9aa1af)}
.vp-empty{padding:14px 18px;font-size:12px;color:var(--muted,#9aa1af)}
.vp-item.vp-unavail{opacity:.55}
.vp-badge{font-size:9px;text-transform:uppercase;letter-spacing:.06em;border:1px solid var(--border,#262b35);
  border-radius:4px;padding:1px 5px;color:var(--muted,#9aa1af);flex:none}
.vp-badge.vp-mut{color:#e0b34d;border-color:#6a5320}
.vp-badge.vp-desc{color:#e06c75;border-color:#6a2a30}
.vp-reason{font-size:10px;color:var(--muted,#9aa1af);display:block;margin-top:2px}
#vpForm{padding:10px 18px 14px;border-top:1px solid var(--border,#262b35);font-size:12px;color:var(--ink,#e8eaf0)}
#vpForm label{display:block;margin:8px 0 3px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#9aa1af)}
#vpForm input{width:100%;background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:6px;
  padding:7px 10px;font:inherit;font-size:13px;color:var(--ink,#e8eaf0);outline:none}
#vpForm .vp-run{margin-top:12px;width:100%;padding:8px;border-radius:8px;border:1px solid var(--border,#262b35);
  background:var(--field,#1d2129);color:var(--ink,#e8eaf0);font:inherit;font-size:13px;cursor:pointer}
#vpResult{padding:10px 18px 14px;border-top:1px solid var(--border,#262b35);font-size:12px;max-height:22vh;overflow-y:auto}
#vpResult .vp-ok{color:#7ee2a8}#vpResult .vp-fault{color:#e06c75}
#vpResult .vp-receipts{margin-top:6px;font-family:ui-monospace,Menlo,monospace;font-size:10px;color:var(--muted,#9aa1af)}
#vpResult .vp-next{margin-top:6px}
#vpResult .vp-next button{font:inherit;font-size:11px;background:none;border:1px solid var(--border,#262b35);
  border-radius:6px;padding:3px 8px;color:var(--muted,#9aa1af);cursor:pointer;margin:2px 4px 2px 0}
@media (prefers-reduced-motion: reduce){ #vpPalette, .vp-item, #vpForm input, [data-vp-run]{transition:none !important; animation:none !important} }
"""

_PALETTE_JS = """
(function(){
'use strict';
if (window.VoolPalette) return;

var ACTIONS = [];
var REGISTRY = [];          // fetched from GET /api/commands/palette — NEVER hardcoded here
var SCHEMA = null;          // fetched from GET /api/commands/schema (typed argument forms)
function pageActions(){ return window.VoolPageActions || null; }
function fetchJson(url, opts){
  return fetch(url, opts || {}).then(function(r){ return r.json().catch(function(){ return null; }); });
}
function loadRegistry(){
  fetchJson('/api/commands/palette').then(function(data){
    if (!data || !data.commands) return;
    REGISTRY = data.commands;
    renderList();
  });
  fetchJson('/api/commands/schema').then(function(data){ SCHEMA = data || null; });
}
function companion(){ return window.VoolCompanion || null; }
function register(id, label, run, kbd){
  ACTIONS.push({ id: String(id), label: String(label), run: run, kbd: kbd || '' });
}

register('new-chat', 'New chat', function(){ var p = pageActions(); if (p) p.newChat(); }, '\\⌘N');
register('toggle-activity', 'Toggle Activity panel', function(){ var p = pageActions(); if (p) p.togglePanel(); }, '\\⌘J');
register('model-menu', 'Switch model\\…', function(){ var p = pageActions(); if (p) p.openModelMenu(); }, '\\⌘M');
register('mode-menu', 'Autonomy mode\\…', function(){ var p = pageActions(); if (p) p.openModeMenu(); });
register('settings', 'Open Settings', function(){ var p = pageActions(); if (p) p.openSettings(); }, '\\⌘,');
register('council', 'Council control room', function(){ var p = pageActions(); if (p) p.openCouncil(); });
register('plugins', 'Plugins\\…', function(){ var p = pageActions(); if (p) p.openPlugins(); });
register('skills', 'Skills\\…', function(){ var p = pageActions(); if (p) p.openSkills(); });
register('files', 'Generated files\\…', function(){ var p = pageActions(); if (p) p.openFiles(); });
register('contacts', 'Contacts\\…', function(){ var p = pageActions(); if (p && p.openContacts) p.openContacts(); });
register('insert-contact', 'Insert a saved contact\\…', function(){ if (window.VoolContacts) window.VoolContacts.chooseIntoComposer({}); });
register('focus-composer', 'Focus composer', function(){ var p = pageActions(); if (p) p.focusComposer(); });
register('companion-lab', 'Companion: Character Lab\\…', function(){ var c = companion(); if (c && c.openCharacterLab) c.openCharacterLab(); });
register('companion-dock', 'Companion: dock in corner', function(){ var c = companion(); if (c && c.dock) c.dock(); });
register('companion-hide', 'Companion: hide', function(){ var c = companion(); if (c && c.hide) c.hide(); });

var overlay = document.createElement('div');
overlay.id = 'vpOverlay';
overlay.hidden = true;
overlay.setAttribute('role', 'dialog');
overlay.setAttribute('aria-modal', 'true');
overlay.setAttribute('aria-label', 'Command palette');
overlay.innerHTML = '<div id="vpPalette">' +
  '<input id="vpInput" type="text" role="combobox" aria-expanded="true" aria-controls="vpList"' +
  ' placeholder="Type a command\\… model, activity, settings, companion" autocomplete="off">' +
  '<div id="vpList" role="listbox"></div></div>';
document.body.appendChild(overlay);
var inputEl = overlay.querySelector('#vpInput');
var listEl = overlay.querySelector('#vpList');
var selected = 0;
var visible = [];

function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function registryRow(command, index){
  var unavail = !command.available;
  return '<div class="vp-item' + (index === selected ? ' vp-sel' : '') + (unavail ? ' vp-unavail' : '') +
    '" role="option" aria-selected="' + (index === selected) + '" data-vp="cmd:' + esc(command.command_id) + '">' +
    '<span class="vp-badge">' + esc(command.group) + '</span>' +
    (command.effects !== 'read_only' ? '<span class="vp-badge vp-' + (command.effects === 'destructive' ? 'desc' : 'mut') + '">' + esc(command.effects) + '</span>' : '') +
    esc(command.label) +
    (unavail ? '<span class="vp-reason">unavailable: ' + esc(command.unavailable_reason || 'not available now') + '</span>' : '') +
    '<span class="vp-kbd">' + esc(command.command_id) + '</span></div>';
}
function renderList(){
  var query = inputEl.value.trim().toLowerCase();
  var pageMatches = ACTIONS.filter(function(action){ return !query || action.label.toLowerCase().indexOf(query) !== -1; });
  var tokens = query ? query.toLowerCase().split(/\\s+/).filter(Boolean) : [];
  var registryMatches = REGISTRY.filter(function(c){
    if (!tokens.length) return true;
    var hay = (c.command_id + ' ' + (c.aliases || []).join(' ') + ' ' + c.group + ' ' + c.label).toLowerCase();
    return tokens.every(function(token){ return hay.indexOf(token) !== -1; });
  });
  visible = pageMatches.concat(registryMatches.map(function(c){ return { id: 'cmd:' + c.command_id, command: c }; }));
  if (selected >= visible.length) selected = Math.max(0, visible.length - 1);
  listEl.innerHTML = visible.length
    ? visible.map(function(action, index){
        if (action.command) return registryRow(action.command, index);
        return '<div class="vp-item' + (index === selected ? ' vp-sel' : '') + '" role="option"' +
          ' aria-selected="' + (index === selected) + '" data-vp="' + esc(action.id) + '">' +
          esc(action.label) + (action.kbd ? '<span class="vp-kbd">' + esc(action.kbd) + '</span>' : '') +
          '</div>';
      }).join('')
    : '<div class="vp-empty">No matching command</div>';
}
var paletteEl = overlay.querySelector('#vpPalette');
var formEl = document.createElement('div'); formEl.id = 'vpForm'; formEl.hidden = true;
var resultEl = document.createElement('div'); resultEl.id = 'vpResult'; resultEl.hidden = true;
paletteEl.appendChild(formEl); paletteEl.appendChild(resultEl);
function schemaFor(commandId){
  if (!SCHEMA || !SCHEMA.commands) return null;
  for (var i = 0; i < SCHEMA.commands.length; i++) if (SCHEMA.commands[i].command_id === commandId) return SCHEMA.commands[i];
  return null;
}
function showForm(command){
  formEl.hidden = false; resultEl.hidden = true;
  var schema = schemaFor(command.command_id) || { input_schema: null };
  var fields = schema.input_schema || {};
  var names = Object.keys(fields);
  var html = '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#9aa1af)">' +
    esc(command.command_id) + '</div>';
  if (!names.length) html += '<div style="margin-top:6px;color:var(--muted,#9aa1af)">No arguments.</div>';
  names.forEach(function(name){
    html += '<label>' + esc(name) + (fields[name].required ? ' (required)' : '') + '</label>' +
      '<input data-vp-arg="' + esc(name) + '" type="text" autocomplete="off">';
  });
  html += '<button class="vp-run" data-vp-run="1">Run ' + esc(command.command_id) + '</button>';
  formEl.innerHTML = html;
  formEl.querySelector('[data-vp-run]').addEventListener('click', function(){ dispatchCommand(command); });
  var first = formEl.querySelector('input'); if (first) first.focus();
}
function dispatchCommand(command){
  var args = {};
  formEl.querySelectorAll('[data-vp-arg]').forEach(function(input){
    var name = input.getAttribute('data-vp-arg');
    var raw = input.value;
    if (raw === '') return;
    if (raw === 'true' || raw === 'false') args[name] = raw === 'true';
    else if (/^-?[0-9]+$/.test(raw)) args[name] = parseInt(raw, 10);
    else args[name] = raw;
  });
  fetchJson('/api/commands/dispatch', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ command_id: command.command_id, input: args }),
  }).then(function(env){ showResult(command, env); });
}
function showResult(command, env){
  env = env || {};
  formEl.hidden = true; resultEl.hidden = false;
  var fault = env.fault || null;
  var html = '<div class="' + (env.ok ? 'vp-ok' : 'vp-fault') + '">' + (env.ok ? '\u2713 ' : '\u2717 ') + esc(env.summary || '') + '</div>';
  if (fault && fault.code === 'permission_required') {
    html += '<div class="vp-reason">Approval required' +
      (fault.detail && fault.detail.reason ? ': ' + esc(fault.detail.reason) : '') + '</div>';
    (fault.remediation || []).forEach(function(r){ html += '<div class="vp-reason">\u2192 ' + esc(r) + '</div>'; });
  } else if (fault && fault.code === 'unavailable' && fault.detail && fault.detail.reason) {
    html += '<div class="vp-reason">' + esc(fault.detail.reason) + '</div>';
  }
  var receipts = env.receipts || [];
  if (receipts.length) {
    html += '<div class="vp-receipts">receipts: ' + receipts.map(function(r){
      return esc(r.kind + (r.id ? ':' + r.id : '') + (r.turn_id ? ':' + r.turn_id : ''));
    }).join(' \u00b7 ') + '</div>';
  }
  var crumbs = env.breadcrumbs || [];
  if (crumbs.length) {
    html += '<div class="vp-next">' + crumbs.map(function(c){ return '<button data-vp-crumb="' + esc(c.invocation) + '">' + esc(c.label) + '</button>'; }).join('') + '</div>';
  }
  resultEl.innerHTML = html;
  resultEl.querySelectorAll('[data-vp-crumb]').forEach(function(button){
    button.addEventListener('click', function(){
      inputEl.value = button.getAttribute('data-vp-crumb').replace(/^vool[ ]+/, '');
      resultEl.hidden = true;
      renderList();
    });
  });
}
function openPalette(){
  overlay.hidden = false;
  inputEl.value = '';
  selected = 0;
  if (formEl) formEl.hidden = true;
  if (resultEl) resultEl.hidden = true;
  renderList();
  inputEl.focus();
}
function closePalette(){
  overlay.hidden = true;
  var p = pageActions();
  if (p) p.focusComposer();
}
function runSelected(){
  var action = visible[selected];
  if (!action) return;
  if (action.command) { showForm(action.command); return; }
  closePalette();
  try { action.run(); } catch (err) { /* an action failing must not brick the palette */ }
}
overlay.addEventListener('mousedown', function(ev){ if (ev.target === overlay) closePalette(); });
listEl.addEventListener('click', function(ev){
  var item = ev.target.closest('[data-vp]');
  if (!item) return;
  selected = visible.findIndex(function(action){ return action.id === item.getAttribute('data-vp'); });
  runSelected();
});
inputEl.addEventListener('input', function(){ selected = 0; if (formEl) formEl.hidden = true; if (resultEl) resultEl.hidden = true; renderList(); });
inputEl.addEventListener('keydown', function(ev){
  if (ev.key === 'ArrowDown') { ev.preventDefault(); selected = Math.min(selected + 1, visible.length - 1); renderList(); }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); selected = Math.max(selected - 1, 0); renderList(); }
  else if (ev.key === 'Enter') { ev.preventDefault(); runSelected(); }
  else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closePalette(); }
});

overlay.addEventListener('keydown', function(ev){
  if (!overlay.hidden && ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closePalette(); }
  if (!overlay.hidden && ev.key === 'Tab') {
    var focusables = overlay.querySelectorAll('input, button');
    if (!focusables.length) return;
    var first = focusables[0], last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }
});

// ---- global shortcuts --------------------------------------------------------------------
function isEditable(el){
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}
function vpMountHint(){
  var input = document.getElementById('input');
  if (!input || document.getElementById('vpHint')) return;
  var host = input.closest('.composer-row') ? input.closest('.composer-row').parentNode : input.parentNode;
  if (!host) return;
  var hint = document.createElement('div');
  hint.id = 'vpHint';
  hint.innerHTML = '<kbd>⌘K</kbd> palette \u00b7 <kbd>⌘N</kbd> new chat \u00b7 <kbd>⌘J</kbd> activity \u00b7 <kbd>⌘/</kbd> pet appearance \u00b7 <kbd>⌘F</kbd> search chats \u00b7 <kbd>⌘,</kbd> settings \u00b7 <kbd>Esc</kbd> stop';
  host.appendChild(hint);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', vpMountHint);
else vpMountHint();
loadRegistry();

document.addEventListener('keydown', function(ev){
  var combo = ev.metaKey || ev.ctrlKey;
  if (!combo) return;
  var key = String(ev.key || '').toLowerCase();
  if (key === 'k') { ev.preventDefault(); overlay.hidden ? openPalette() : closePalette(); }
  else if (key === 'n' && !ev.shiftKey) { var p1 = pageActions(); if (p1) { ev.preventDefault(); p1.newChat(); } }
  else if (key === 'j') { var p2 = pageActions(); if (p2) { ev.preventDefault(); p2.togglePanel(); } }
  else if (key === 'm') { var p3 = pageActions(); if (p3) { ev.preventDefault(); p3.openModelMenu(); } }
  else if (key === ',') { var p4 = pageActions(); if (p4) { ev.preventDefault(); p4.openSettings(); } }
});

// Escape closes the palette no matter which of its children (or body, after the
// result replaced a focused control) holds focus. Capture phase, before legacy closers.
document.addEventListener('keydown', function(ev){
  if (ev.key === 'Escape' && !overlay.hidden) { ev.stopPropagation(); closePalette(); }
}, true);

// ---- the Esc cascade ---------------------------------------------------------------------
// Capture phase runs before every legacy bubble listener: snapshot what was open AT PRESS TIME.
var escSawOpenSurface = false;
function anyLegacySurfaceOpen(){
  if (!overlay.hidden) return true;
  var ids = ['settingsOverlay', 'councilOverlay', 'pluginsOverlay', 'bypassOverlay', 'pinOverlay'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (el && !el.hidden) return true;
  }
  if (document.querySelector('.pop.open')) return true;
  if (document.querySelector('.vool-ninja-menu, .vool-ninja-pop, .vool-character-lab')) return true;
  return false;
}
document.addEventListener('keydown', function(ev){
  if (ev.key === 'Escape') escSawOpenSurface = anyLegacySurfaceOpen();
}, true);
// Bubble phase: this fragment evaluates after the page script, so this listener runs after every
// legacy Escape closer. If something was open, a closer owned it. Only a bare Esc stops the run.
document.addEventListener('keydown', function(ev){
  if (ev.key !== 'Escape' || ev.defaultPrevented) return;
  if (escSawOpenSurface) { escSawOpenSurface = false; return; }
  if (isEditable(ev.target) && ev.target.id !== 'input') return;   // dialogs' own inputs keep Esc
  var p = pageActions();
  if (p && p.hasLiveRun()) {
    if (p.stopDisplayedRun()) p.toast('Stopping\\…');
  }
});

window.VoolPalette = Object.freeze({
  open: openPalette,
  close: closePalette,
  register: register,
  actions: function(){ return ACTIONS.map(function(action){ return action.id; }); },
});
})();
"""


def render_palette_fragment() -> str:
    """The command palette + shortcuts + Esc cascade as an appended fragment."""
    return "<style>" + _PALETTE_CSS + "</style><script>" + _PALETTE_JS + "</script>"


__all__ = ["render_palette_fragment"]
