"""Settings extras: the Memory browser, the truthful Privacy disclosure, and the Toolbelt.

Ported from the ux-pass1 companion drawer, bound to real state only:

- MEMORY — the rows `core/memory/entries.py` actually stores (`/api/memory/entries`), shown
  with their REAL stored scope/category/source (never the prototype's EXPLICIT/INFERRED triad
  forced onto different storage), each with a Forget that removes exactly that record
  (`/api/memory/forget`, id-scoped by construction). Empty is stated.
- PRIVACY & DATA — a read-only disclosure of what is true today: where data lives, what is
  encrypted, what leaves the machine and when. The prototype's retention/cloud-learning
  TOGGLES do not ship: no server enforcement exists for them, and an unenforced toggle is a
  lie (recorded as PARKED in the reconciliation ledger).
- TOOLBELT — the capability inventory (UNKNOWN ≠ FAILED law): real rows from
  `/api/connections`, `/api/cloud/status`, and `/api/plugins`. A thing never probed reads
  UNKNOWN with a dashed chip, never FAILED and never green.

Mounted two ways: into the legacy `#settingsOverlay` before "About this build" (one boot-time DOM
insert, when that overlay exists), and section by section into the integrated Settings page
(`/settings`) through `VoolSettingsExtras.mountInto(host, which)`. The Toolbelt also registers a
palette action. Namespace `window.VoolSettingsExtras`; prefix `vs-`.
"""

from __future__ import annotations

_EXTRAS_CSS = """
.vs-sec{margin-top:18px}
.vs-sec h4{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted,#9aa1af);margin:0 0 8px}
.vs-mem-item{display:flex;gap:8px;align-items:flex-start;padding:7px 0;border-bottom:1px solid var(--border,#262b35);font-size:13px;color:var(--ink,#e8eaf0)}
.vs-mem-item:last-child{border-bottom:none}
.vs-tag{font-family:ui-monospace,Menlo,monospace;font-size:9px;padding:2px 6px;border-radius:4px;flex:none;margin-top:2px;background:var(--field,#1d2129);color:var(--muted,#9aa1af);border:1px solid var(--border,#262b35);text-transform:uppercase}
.vs-forget{margin-left:auto;color:var(--muted,#9aa1af);font-size:11px;background:none;border:none;cursor:pointer;flex:none}
.vs-forget:hover{color:var(--bad,#f2545b)}
.vs-empty{font-size:12px;color:var(--muted,#9aa1af);padding:6px 0}
.vs-privacy p{font-size:12px;color:var(--muted,#9aa1af);margin:4px 0}
.vs-privacy b{color:var(--ink,#e8eaf0)}
#vsToolbeltOverlay{position:fixed;inset:0;background:rgba(5,7,10,.6);z-index:1250;display:flex;align-items:flex-start;justify-content:center}
#vsToolbeltOverlay[hidden]{display:none}
#vsToolbeltModal{margin-top:12vh;width:min(520px,92vw);max-height:64vh;overflow-y:auto;background:var(--panel,#16191f);border:1px solid var(--border,#262b35);border-radius:14px;padding:16px 18px}
#vsToolbeltModal h3{margin:0 0 4px;font-size:14px;color:var(--ink,#e8eaf0)}
#vsToolbeltModal .vs-law{font-size:11px;color:var(--muted,#9aa1af);margin-bottom:10px}
.vs-belt-row{display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border,#262b35);font-size:13px;color:var(--ink,#e8eaf0)}
.vs-belt-row:last-child{border-bottom:none}
.vs-belt-row small{color:var(--muted,#9aa1af);margin-left:auto;text-align:right}
"""

_EXTRAS_JS = """
(function(){
'use strict';
if (window.VoolSettingsExtras) return;

function chips(){ return window.VoolChips || null; }
function chip(state, label){
  var c = chips();
  return c ? c.html(state, label) : ('[' + (label || state).toUpperCase() + ']');
}
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---- memory section ------------------------------------------------------------------------
var memSec = null;
function renderMemory(){
  if (!memSec) return;
  var list = memSec.querySelector('.vs-mem-list');
  list.innerHTML = '<div class="vs-empty">Reading memory\\u2026</div>';
  fetch('/api/memory/entries?limit=50').then(function(r){ return r.json(); }).then(function(j){
    var rows = (j && j.entries) || [];
    if (!rows.length) {
      list.innerHTML = '<div class="vs-empty">Nothing remembered yet \\u2014 confirmed facts land here with their stored scope, and each can be forgotten.</div>';
      return;
    }
    list.innerHTML = rows.map(function(row){
      var tag = row.scope || row.category || 'fact';
      return '<div class="vs-mem-item" data-vs-record="' + esc(row.record_id) + '">' +
        '<span class="vs-tag">' + esc(tag) + '</span>' +
        '<span>' + esc(row.fact) + (row.source ? ' <small style="color:var(--muted,#9aa1af)">\\u00b7 ' + esc(row.source) + '</small>' : '') + '</span>' +
        (row.record_id ? '<button type="button" class="vs-forget" title="Forget exactly this entry">forget</button>' : '') +
        '</div>';
    }).join('');
  }).catch(function(){
    list.innerHTML = '<div class="vs-empty">Memory could not be read.</div>';
  });
}
document.addEventListener('click', function(ev){
  var btn = ev.target.closest && ev.target.closest('.vs-forget');
  if (!btn) return;
  var row = btn.closest('[data-vs-record]');
  var recordId = row && row.getAttribute('data-vs-record');
  if (!recordId) return;
  btn.disabled = true;
  fetch('/api/memory/forget', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ record_id: recordId }),
  }).then(function(r){ return r.json(); }).then(function(j){
    if (j && j.removed) row.remove();
    else { btn.disabled = false; btn.textContent = 'not removed'; }
  }).catch(function(){ btn.disabled = false; });
});

// ---- toolbelt ------------------------------------------------------------------------------
var beltOverlay = document.createElement('div');
beltOverlay.id = 'vsToolbeltOverlay';
beltOverlay.hidden = true;
beltOverlay.innerHTML = '<div id="vsToolbeltModal"><h3>Toolbelt \\u2014 installed capabilities</h3>' +
  '<div class="vs-law">UNKNOWN \\u2260 FAILED: a capability never probed reads UNKNOWN \\u2014 we do not know yet, and that is the honest state.</div>' +
  '<div class="vs-belt"></div></div>';
document.body.appendChild(beltOverlay);
beltOverlay.addEventListener('mousedown', function(ev){ if (ev.target === beltOverlay) beltOverlay.hidden = true; });
document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape' && !beltOverlay.hidden) beltOverlay.hidden = true; }, true);

function beltRow(name, state, label, detail){
  return '<div class="vs-belt-row"><span>' + esc(name) + '</span>' + chip(state, label) +
    (detail ? '<small>' + esc(detail) + '</small>' : '') + '</div>';
}
function openToolbelt(){
  var belt = beltOverlay.querySelector('.vs-belt');
  belt.innerHTML = '<div class="vs-empty">Probing\\u2026</div>';
  beltOverlay.hidden = false;
  Promise.all([
    fetch('/api/connections').then(function(r){ return r.json(); }).catch(function(){ return null; }),
    fetch('/api/cloud/status').then(function(r){ return r.json(); }).catch(function(){ return null; }),
    fetch('/api/plugins').then(function(r){ return r.json(); }).catch(function(){ return null; }),
    fetch('/api/tags').then(function(r){ return r.json(); }).catch(function(){ return null; }),
  ]).then(function(results){
    var connections = (results[0] && results[0].connections) || [];
    var cloud = results[1];
    var plugins = (results[2] && (results[2].plugins || results[2].items)) || [];
    var localModels = (results[3] && results[3].models) || [];
    var rows = '';
    rows += beltRow('Local models (Ollama)',
      localModels.length ? 'pass' : 'unknown',
      localModels.length ? 'AVAILABLE' : 'UNKNOWN',
      localModels.length ? (localModels.length + ' installed') : 'inventory not readable');
    if (cloud) {
      var st = String(cloud.state || 'no_key');
      rows += beltRow('Cloud (' + esc(cloud.label || cloud.provider || 'provider') + ')',
        st === 'ok' ? 'pass' : st === 'failed' ? 'failed' : st === 'no_key' ? 'unknown' : 'unknown',
        st === 'ok' ? 'CONNECTED' : st === 'failed' ? 'FAILED' : st === 'no_key' ? 'NO KEY' : 'UNTESTED',
        st === 'untested' ? 'key present \\u00b7 never probed' : (cloud.detail || ''));
    } else {
      rows += beltRow('Cloud connection', 'unknown', 'UNKNOWN', 'status not readable');
    }
    connections.forEach(function(conn){
      var st2 = String(conn.state || 'untested');
      rows += beltRow(conn.label || conn.provider || 'connection',
        st2 === 'ok' ? 'pass' : st2 === 'failed' ? 'failed' : 'unknown',
        st2 === 'ok' ? 'CONNECTED' : st2 === 'failed' ? 'FAILED' : st2.toUpperCase(),
        conn.detail || '');
    });
    var enabled = plugins.filter(function(p){ return p && (p.enabled || p.on); }).length;
    rows += beltRow('Plugins', plugins.length ? 'done' : 'unknown',
      plugins.length ? (enabled + '/' + plugins.length + ' ENABLED') : 'UNKNOWN',
      plugins.length ? '' : 'registry not readable');
    belt.innerHTML = rows;
  });
}

// ---- mount into settings -------------------------------------------------------------------
// Each section is built ONCE and moved wherever it is mounted: the legacy overlay took all three in a
// row; the integrated Settings page (/settings) mounts them one at a time into the group they belong
// to (learned facts under Memory, the disclosure under Privacy & Permissions, the Toolbelt under
// About & Diagnostics) through `mountInto(host, which)`.
var sections = {};
function section(which){
  if (sections[which]) return sections[which];
  var node = document.createElement('div');
  if (which === 'memory') {
    node.className = 'vs-sec'; node.id = 'vsMemorySec';
    node.innerHTML = '<h4>Memory</h4><div class="vs-mem-list"></div>';
  } else if (which === 'privacy') {
    node.className = 'vs-sec vs-privacy'; node.id = 'vsPrivacySec';
    node.innerHTML = '<h4>Privacy &amp; data</h4>' +
      '<p><b>Local first.</b> Chats, memory, receipts and generated files live in this machine’s VOOL home; nothing leaves it on the local lane.</p>' +
      '<p><b>Cloud is explicit.</b> A cloud call happens only for a cloud-pinned or Auto-free turn with your key; Local Only blocks the cloud lane entirely, including free models.</p>' +
      '<p><b>Keys are sealed.</b> Provider keys live in the system keychain/encrypted store and are never rendered back into this page.</p>' +
      '<p><b>Spend is gated.</b> Paid pins require server-confirmed consent; accepted ceilings re-gate on price rises.</p>' +
      '<p>Retention windows and cloud-learning controls are not shown because no enforcement for them exists yet — an unenforced toggle would be a lie.</p>';
  } else if (which === 'toolbelt') {
    node.className = 'vs-sec'; node.id = 'vsToolbeltSec';
    node.innerHTML = '<h4>Toolbelt</h4>' +
      '<button type="button" class="vs-forget" id="vsToolbeltBtn" style="margin-left:0;color:var(--accent,#5eead4)">Open capability inventory…</button>';
    node.querySelector('#vsToolbeltBtn').addEventListener('click', openToolbelt);
  } else {
    return null;
  }
  sections[which] = node;
  return node;
}
function mountInto(host, which){
  // Returns whether anything mounted. A section already mounted elsewhere MOVES here, so no
  // control is ever duplicated and the memory list keeps one renderer.
  if (!host) return false;
  var wanted = which ? [which] : ['memory', 'privacy', 'toolbelt'];
  var mounted = false;
  wanted.forEach(function(name){
    var node = section(name);
    if (!node) return;
    if (node.parentNode !== host) host.appendChild(node);
    mounted = true;
    if (name === 'memory') { memSec = node; renderMemory(); }
  });
  return mounted;
}
function mount(){
  var overlay = document.getElementById('settingsOverlay');
  if (!overlay || document.getElementById('vsMemorySec')) return;
  var anchorHeading = Array.from(overlay.querySelectorAll('h3, h4, .set-sec-title, b')).find(function(el){
    return /about this build/i.test(el.textContent || '');
  });
  var host = anchorHeading ? (anchorHeading.closest('.set-sec') || anchorHeading.parentNode) : null;
  var container = document.createElement('div');
  mountInto(container);
  if (host && host.parentNode) host.parentNode.insertBefore(container, host);
  else overlay.appendChild(container);
  document.addEventListener('click', function(ev){
    if (ev.target && (ev.target.id === 'settingsBtn' || (ev.target.closest && ev.target.closest('#settingsBtn')))) {
      setTimeout(renderMemory, 60);
    }
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();

if (window.VoolPalette && window.VoolPalette.register) {
  window.VoolPalette.register('toolbelt', 'Toolbelt \\u2014 installed capabilities\\u2026', openToolbelt);
}

window.VoolSettingsExtras = Object.freeze({
  refreshMemory: renderMemory,
  openToolbelt: openToolbelt,
  mountInto: mountInto,
});
})();
"""


def render_settings_extras_fragment() -> str:
    """Settings extras as an appended fragment."""
    return "<style>" + _EXTRAS_CSS + "</style><script>" + _EXTRAS_JS + "</script>"


__all__ = ["render_settings_extras_fragment"]
