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
#vpHint{font-size:11px;color:var(--muted,#9aa1af);text-align:center;padding:3px 0 6px;font-family:ui-monospace,Menlo,monospace;letter-spacing:.03em}
#vpHint kbd{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:4px;padding:0 5px;font-size:10px}
@media(max-width:900px){#vpHint{display:none}}
@media(max-height:600px){#vpHint{display:none}}
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
.vp-badge.vp-mut{color:var(--warn,#cfae6e);border-color:var(--border,#262b35)}
.vp-badge.vp-desc{color:var(--bad,#d08a82);border-color:var(--border,#262b35)}
.vp-reason{font-size:10px;color:var(--muted,#9aa1af);display:block;margin-top:2px}
#vpForm{padding:10px 18px 14px;border-top:1px solid var(--border,#262b35);font-size:12px;color:var(--ink,#e8eaf0)}
#vpForm label{display:block;margin:8px 0 3px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#9aa1af)}
#vpForm input{width:100%;background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:6px;
  padding:7px 10px;font:inherit;font-size:13px;color:var(--ink,#e8eaf0);outline:none}
#vpForm .vp-run{margin-top:12px;width:100%;padding:8px;border-radius:8px;border:1px solid var(--border,#262b35);
  background:var(--field,#1d2129);color:var(--ink,#e8eaf0);font:inherit;font-size:13px;cursor:pointer}
#vpResult{padding:10px 18px 14px;border-top:1px solid var(--border,#262b35);font-size:12px;max-height:22vh;overflow-y:auto}
#vpResult .vp-ok{color:var(--ok,#8fb59a)}#vpResult .vp-fault{color:var(--bad,#d08a82)}
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
// The registry is the operator's command centre (logs, receipts, wallet approvals, repair stages).
// It is real, but it is not what a person pressing Ctrl+K for "new chat" should be handed: by
// default the palette lists the page's own actions only. "Show developer commands" (a row of the
// palette itself, remembered per browser) brings every registry row back, unchanged.
var DEV_KEY = 'vool.palette.developer_commands';
function devCommandsOn(){ try { return window.localStorage.getItem(DEV_KEY) === '1'; } catch (e) { return false; } }
function setDevCommands(on){ try { if (on) window.localStorage.setItem(DEV_KEY, '1'); else window.localStorage.removeItem(DEV_KEY); } catch (e) {} }
var IS_MAC = /Mac|iPhone|iPad/.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');
var MOD = IS_MAC ? '\u2318' : 'Ctrl+';
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
function register(id, label, run, kbd, keywords){
  ACTIONS.push({ id: String(id), label: String(label), run: run, kbd: kbd || '', keywords: String(keywords || '') });
}
// "setup" finds "Set up VOOL": spaces and dots are ignored, and a row may carry other words for itself.
function squash(t){ return String(t || '').toLowerCase().replace(/[\\s.\u2026]+/g, ''); }
function actionMatches(action, query){
  if (!query) return true;
  if (action.label.toLowerCase().indexOf(query) !== -1) return true;
  var q = squash(query);
  return !!q && squash(action.label + ' ' + (action.keywords || '')).indexOf(q) !== -1;
}

function clickId(id){ var el = document.getElementById(id); if (el && !el.disabled) el.click(); }
register('new-chat', 'New chat', function(){ var p = pageActions(); if (p) p.newChat(); }, MOD + 'N');
register('search-chat', 'Search this chat', function(){ clickId('searchBtn'); }, MOD + 'Shift+F');
register('search-chats', 'Find a chat by name', function(){ var f = document.getElementById('vxFilter'); if (f) f.focus(); }, MOD + 'F');
register('export-chat', 'Export this chat\\…', function(){ clickId('exportBtn'); });
register('pinned', 'Pinned messages', function(){ clickId('pinBtn'); });
register('toggle-activity', 'Show or hide activity', function(){ var p = pageActions(); if (p) p.togglePanel(); }, MOD + 'J');
register('model-menu', 'Choose a model\\…', function(){ var p = pageActions(); if (p) p.openModelMenu(); }, MOD + 'M');
register('mode-menu', 'How much VOOL may do alone\\…', function(){ var p = pageActions(); if (p) p.openModeMenu(); });
register('setup', 'Set up VOOL\\…', function(){ var p = pageActions(); if (p && p.openSetup) p.openSetup(''); }, '', 'setup getting started finish setup');
register('settings', 'Open settings', function(){ var p = pageActions(); if (p) p.openSettings(); }, MOD + ',');
register('council', 'Council\\…', function(){ var p = pageActions(); if (p) p.openCouncil(); });
register('plugins', 'Skills & plugins\\…', function(){ var p = pageActions(); if (p) p.openPlugins(); });
register('files', 'Files VOOL made\\…', function(){ var p = pageActions(); if (p) p.openFiles(); });
register('report', 'Report a problem\\…', function(){ clickId('reportBtn'); });
register('contacts', 'Contacts\\…', function(){ var p = pageActions(); if (p && p.openContacts) p.openContacts(); });
register('insert-contact', 'Insert a saved contact\\…', function(){ if (window.VoolContacts) window.VoolContacts.chooseIntoComposer({}); });
register('focus-composer', 'Go to the message box', function(){ var p = pageActions(); if (p) p.focusComposer(); });
register('companion-lab', 'Pet: choose a character\\…', function(){ var c = companion(); if (c && c.openCharacterLab) c.openCharacterLab(); });
register('companion-dock', 'Pet: move to the corner', function(){ var c = companion(); if (c && c.dock) c.dock(); });
register('companion-hide', 'Pet: hide', function(){ var c = companion(); if (c && c.hide) c.hide(); });
// Chats by name: "Go to chat: <title>" rows come from the sidebar list the page already rendered.
function chatRows(query){
  if (!query) return [];
  var rows = [];
  document.querySelectorAll('#sessions .session[data-id]').forEach(function(row){
    var titleEl = row.querySelector('.s-title');
    var title = titleEl ? titleEl.textContent.trim() : '';
    if (!title || title.toLowerCase().indexOf(query) === -1) return;
    rows.push({ id: 'chat:' + row.getAttribute('data-id'), label: 'Go to chat: ' + title, kbd: '',
      run: function(){ if (titleEl) titleEl.click(); } });
  });
  return rows.slice(0, 8);
}
function devToggleRow(){
  var on = devCommandsOn();
  return { id: 'dev-toggle', label: on ? 'Hide developer commands' : 'Show developer commands', kbd: '', keepOpen: true,
    run: function(){ setDevCommands(!on); renderList(); } };
}

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
  var pageMatches = ACTIONS.filter(function(action){ return actionMatches(action, query); });
  pageMatches = pageMatches.concat(chatRows(query));
  var devRow = devToggleRow();
  if (!query || devRow.label.toLowerCase().indexOf(query) !== -1 || 'developer'.indexOf(query) === 0) pageMatches.push(devRow);
  var tokens = query ? query.toLowerCase().split(/\\s+/).filter(Boolean) : [];
  var registryMatches = (devCommandsOn() ? REGISTRY : []).filter(function(c){
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
          esc(action.label) + (action.kbd ? '<span class="vp-kbd">' + esc(typeof voolKeyLabel === 'function' ? voolKeyLabel(action.kbd) : action.kbd) + '</span>' : '') +
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
  if (action.keepOpen) { try { action.run(); } catch (err) {} inputEl.focus(); return; }
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
function vpHintT(key, fallback){
  // The shortcut labels resolve through the page's i18n bundle; the inline English
  // is the honest fallback until a catalog resolves them. The <kbd> key caps and the
  // separators are structure, never translated words.
  try {
    if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; }
  } catch (e) {}
  return fallback;
}
function vpMountHint(){
  var input = document.getElementById('input');
  if (!input || document.getElementById('vpHint')) return;
  var host = input.closest('.composer-row') ? input.closest('.composer-row').parentNode : input.parentNode;
  if (!host) return;
  var hint = document.createElement('div');
  hint.id = 'vpHint';
  hint.innerHTML = '<kbd>⌘K</kbd> ' + vpHintT('palette.hint_palette', 'palette')
    + ' \u00b7 <kbd>⌘N</kbd> ' + vpHintT('palette.hint_new_chat', 'new chat')
    + ' \u00b7 <kbd>⌘J</kbd> ' + vpHintT('palette.hint_activity', 'activity')
    + ' \u00b7 <kbd>⌘/</kbd> ' + vpHintT('palette.hint_pet', 'pet appearance')
    + ' \u00b7 <kbd>⌘F</kbd> ' + vpHintT('palette.hint_search_chats', 'search chats')
    + ' \u00b7 <kbd>⌘,</kbd> ' + vpHintT('palette.hint_settings', 'settings')
    + ' \u00b7 <kbd>Esc</kbd> ' + vpHintT('palette.hint_esc_stop', 'stop');
  // The key caps name the keys THIS keyboard has: ⌘ on a Mac, Ctrl everywhere else. In a plain
  // browser tab Ctrl+N belongs to the browser (it opens a window), so it is not advertised there.
  if (!IS_MAC) {
    if (typeof voolKeyLabelsIn === 'function') voolKeyLabelsIn(hint);
    else hint.querySelectorAll('kbd').forEach(function(k){ k.textContent = k.textContent.replace('\u2318', 'Ctrl+'); });
    if (!(window.pywebview && window.pywebview.api)) {
      var parts = hint.innerHTML.split(' \u00b7 ');
      hint.innerHTML = parts.filter(function(part){ return part.indexOf('<kbd>Ctrl+N</kbd>') === -1; }).join(' \u00b7 ');
    }
  }
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
// The companion shells (.vool-ninja-menu/-pop, .vool-character-lab) sit in the page from boot,
// hidden with display:none. Present-but-hidden must not count as "a menu is open": only a
// surface the user can actually see owns Escape.
function surfaceReallyOpen(el){
  if (!el || el.hidden) return false;
  if (el.style && el.style.display === 'none') return false;
  var cs = window.getComputedStyle ? window.getComputedStyle(el) : null;
  if (cs && (cs.display === 'none' || cs.visibility === 'hidden')) return false;
  return true;
}
function isShown(el){
  // Hidden by attribute or style, or laid out to nothing where layout is known.
  if (!surfaceReallyOpen(el)) return false;
  return typeof el.getClientRects !== 'function' || el.getClientRects().length > 0;
}
function anyLegacySurfaceOpen(){
  if (!overlay.hidden) return true;
  var ids = ['settingsOverlay', 'councilOverlay', 'pluginsOverlay', 'bypassOverlay', 'pinOverlay'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (el && !el.hidden) return true;
  }
  if (document.querySelector('.pop.open, .popover.open')) return true;
  var overlays = document.querySelectorAll('.modal-overlay');
  for (var k = 0; k < overlays.length; k++) if (!overlays[k].hidden && isShown(overlays[k])) return true;
  var emotes = document.getElementById('vxEmotePop');
  if (emotes && isShown(emotes)) return true;
  if (document.getElementById('emojiPicker') || document.getElementById('projMenu') || document.getElementById('textPromptDialog')) return true;
  var floating = ['vfEventCard', 'vfPop'];
  for (var f = 0; f < floating.length; f++) { var fl = document.getElementById(floating[f]); if (fl && isShown(fl)) return true; }
  if (document.body.classList.contains('sidebar-open') && window.matchMedia && window.matchMedia('(max-width: 720px)').matches) return true;
  // The pet's menu, info pop and Character Lab are ALWAYS in the DOM (hidden with display:none),
  // so presence proves nothing: only one that is actually showing owns this Esc. Testing presence
  // made every bare Esc look "owned" and "Esc stop" never stopped a run.
  var pet = document.querySelectorAll('.vool-ninja-menu, .vool-ninja-pop, .vool-character-lab');
  for (var j = 0; j < pet.length; j++) if (isShown(pet[j])) return true;
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
