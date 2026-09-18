"""Composer extras: per-chat drafts, chat-list filter, per-chat accent, emotes, voice shell.

Ported from the ux-pass1 design authority and bound to real page state only:

- DRAFTS  — the prototype's law "never silently carry a draft across chats": the composer's text
  is saved per chat (`localStorage['vool_ui_draft:<chatId>']`) at the ONE switch seam
  (`setDisplayedChat` -> `VoolComposerExtras.chatSwitched`) plus a debounced input save, and
  restored when the chat returns. Storage failures are swallowed — a private window loses
  drafts, never the composer.
- FILTER  — the sidebar search the prototype sketched: a client-side filter over the rendered
  session rows. Display-only; it never mutates session data, and it re-applies when the list
  re-renders (loadSessions repaints are observed).
- ACCENT  — the per-chat identity accent: the composer carries a left border in the chat's REAL
  colour (the server-side appearance colour via `VoolPageActions.sessionMeta`); no colour, no
  border. A short flash marks the switch; reduced motion suppresses it. Colour is never the only
  signal — the sidebar row still shows emoji + title.
- EMOTES  — VOOL emotes as `[:vool-<state>:]` shortcodes, rendered inline by the REAL companion
  renderer (`window.VoolCompanionWorld.draw`) — the same pixel art, never a second art system.
  A picker button inserts codes; a scoped observer replaces tokens inside message text nodes.
- VOICE   — the prototype's voice bar exists here ONLY as a truthfully disabled control: no
  speech engine (STT/TTS) exists in this runtime, so the mic button ships disabled with that
  exact statement. No fake listening, no timers. (Operator decision 2026-08-28.)

Namespace: `window.VoolComposerExtras`. Class prefix `vx-`.
"""

from __future__ import annotations

_EXTRAS_CSS = """
#vxFilterWrap{padding:8px 10px 10px;margin-top:auto}
#vxFilter{width:100%;background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  border-radius:8px;padding:6px 10px;font:inherit;font-size:12px;color:var(--ink,#e8eaf0)}
#vxFilter::placeholder{color:var(--muted,#9aa1af)}
.composer-row.vx-accented #input{border-left:3px solid var(--vx-accent,transparent)}
.vx-flash #input{animation:vxFlash .6s ease}
@keyframes vxFlash{30%{box-shadow:0 0 0 3px var(--vx-accent,transparent)}}
@media (prefers-reduced-motion: reduce){.vx-flash #input{animation:none}}
.vx-btn{flex:0 0 auto;background:transparent;color:var(--muted,#9aa1af);
  border:1px solid var(--border,#262b35);border-radius:8px;padding:6px 9px;font:inherit;
  font-size:13px;cursor:pointer}
.vx-btn:hover{color:var(--ink,#e8eaf0);border-color:var(--accent,#5eead4)}
.vx-btn[disabled]{opacity:.45;cursor:not-allowed}
.vx-btn[disabled]:hover{color:var(--muted,#9aa1af);border-color:var(--border,#262b35)}
#vxEmotePop{position:fixed;z-index:1100;background:var(--panel,#16191f);
  border:1px solid var(--border,#262b35);border-radius:12px;padding:10px;display:none;
  grid-template-columns:repeat(6,1fr);gap:4px;box-shadow:0 10px 40px rgba(0,0,0,.5)}
#vxEmotePop.vx-open{display:grid}
#vxEmotePop button{background:none;border:none;border-radius:7px;padding:4px;cursor:pointer}
#vxEmotePop button:hover{background:var(--field,#1d2129)}
#vxEmotePop canvas{width:32px;height:32px;image-rendering:pixelated;display:block}
#vxEmotePop .vx-note{grid-column:1/-1;font-size:10px;color:var(--muted,#9aa1af);padding:4px 2px 0}
.vx-emote{width:28px;height:28px;image-rendering:pixelated;vertical-align:middle;display:inline-block}
"""

_EXTRAS_JS = """
(function(){
'use strict';
if (window.VoolComposerExtras) return;

var DRAFT_PREFIX = 'vool_ui_draft:';
var EMOTE_STATES = ['idle','thinking','tool','waiting','approval','retry','success','failure','unknown','cancelled'];
var EMOTE_TOKEN_RE = /\\[:vool-([a-z]+):\\]/g;

function input(){ return document.getElementById('input'); }
function pageActions(){ return window.VoolPageActions || null; }
function world(){ return window.VoolCompanionWorld || null; }
function notifyInput(el){
  try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
}
function readDraft(chatId){
  try { return localStorage.getItem(DRAFT_PREFIX + String(chatId || '')) || ''; } catch (e) { return ''; }
}
function writeDraft(chatId, text){
  try {
    var key = DRAFT_PREFIX + String(chatId || '');
    if (String(text || '').trim()) localStorage.setItem(key, String(text));
    else localStorage.removeItem(key);
  } catch (e) {}
}

// The send boundary consumes persisted text synchronously, before navigation or reload can
// restore it. A failed queue handoff restores into the owning chat without replacing newer text.
function draftConsumed(chatId){ writeDraft(chatId, ''); }
function restoreDraft(chatId, text){
  var p = pageActions();
  var el = input();
  var visible = !!(p && String(p.displayedChat()) === String(chatId) && el);
  var newer = visible ? el.value : readDraft(chatId);
  var restored = String(text || '') + (newer ? '\\n\\n' + newer : '');
  writeDraft(chatId, restored);
  if (visible) { el.value = restored; notifyInput(el); }
}

// ---- accent -------------------------------------------------------------------------------
function applyAccentFor(chatId){
  var row = document.querySelector('.composer-row');
  if (!row) return;
  var p = pageActions();
  var meta = p && p.sessionMeta ? p.sessionMeta(chatId) : null;
  var color = meta && meta.color ? String(meta.color) : '';
  if (color) {
    row.classList.add('vx-accented');
    row.style.setProperty('--vx-accent', color);
  } else {
    row.classList.remove('vx-accented');
    row.style.removeProperty('--vx-accent');
  }
}
function flashComposer(){
  var row = document.querySelector('.composer-row');
  if (!row) return;
  row.classList.remove('vx-flash');
  void row.offsetWidth;
  row.classList.add('vx-flash');
}

// ---- the switch seam ----------------------------------------------------------------------
function chatSwitched(previousChat, nextChat){
  var el = input();
  if (el) {
    writeDraft(previousChat, el.value);
    var draft = readDraft(nextChat);
    el.value = draft;
    notifyInput(el);   // page reflows its own send state
  }
  applyAccentFor(nextChat);
  if (el && el.value) flashComposer();
}
var draftTimer = null;
document.addEventListener('input', function(ev){
  if (!ev.target || ev.target.id !== 'input') return;
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(function(){
    var p = pageActions();
    if (p) writeDraft(p.displayedChat(), input() ? input().value : '');
  }, 400);
});
// The one synchronous draft flush: the UI-locale switcher calls this through
// window.VOOL_BEFORE_UI_RELOAD before its location.reload(), so switching the app
// language can never cost the operator the last <400ms of an unstarted sentence.
function flushDraft(){
  if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
  var p = pageActions();
  if (p) writeDraft(p.displayedChat(), input() ? input().value : '');
}
window.VOOL_BEFORE_UI_RELOAD = function(){ try { flushDraft(); } catch (e) {} };

// ---- chat-list filter ---------------------------------------------------------------------
var filterValue = '';
function applyFilter(){
  var sessions = document.getElementById('sessions');
  if (!sessions) return;
  var query = filterValue.trim().toLowerCase();
  sessions.querySelectorAll('.session').forEach(function(row){
    var text = (row.textContent || '').toLowerCase();
    row.style.display = !query || text.indexOf(query) !== -1 ? '' : 'none';
  });
  sessions.querySelectorAll('.proj-head').forEach(function(head){
    if (!query) { head.style.display = ''; return; }
    var anyVisible = false;
    var sibling = head.nextElementSibling;
    while (sibling && !sibling.classList.contains('proj-head')) {
      if (sibling.classList.contains('session') && sibling.style.display !== 'none') anyVisible = true;
      sibling = sibling.nextElementSibling;
    }
    head.style.display = anyVisible || (head.textContent || '').toLowerCase().indexOf(query) !== -1 ? '' : 'none';
  });
}
document.addEventListener('keydown', function(ev){
  if ((ev.metaKey || ev.ctrlKey) && !ev.shiftKey && (ev.key === 'f' || ev.key === 'F')) {
    var field = document.getElementById('vxFilter');
    if (field) { ev.preventDefault(); ev.stopPropagation(); field.focus(); field.select(); }
  }
  if ((ev.metaKey || ev.ctrlKey) && ev.shiftKey && (ev.key === 'f' || ev.key === 'F')) {
    var btn = document.getElementById('searchBtn');
    if (btn) { ev.preventDefault(); ev.stopPropagation(); btn.click(); }
  }
}, true);

function mountFilter(){
  var sessions = document.getElementById('sessions');
  if (!sessions || document.getElementById('vxFilter')) return;
  var wrap = document.createElement('div');
  wrap.id = 'vxFilterWrap';
  wrap.innerHTML = '<input id="vxFilter" type="text" placeholder="Search chats\\u2026  \\u2318F" autocomplete="off" aria-label="Search chats">';
  sessions.parentNode.appendChild(wrap);
  var field = wrap.querySelector('#vxFilter');
  field.addEventListener('input', function(){ filterValue = field.value; applyFilter(); });
  if (typeof MutationObserver === 'function') {
    new MutationObserver(function(){ if (filterValue.trim()) applyFilter(); }).observe(sessions, { childList: true, subtree: true });
  }
}

// ---- emotes -------------------------------------------------------------------------------
var emoteCache = {};
function emoteDataUrl(state){
  if (emoteCache[state]) return emoteCache[state];
  var w = world();
  if (!w || typeof w.draw !== 'function') return '';
  var canvas = document.createElement('canvas');
  canvas.width = 48; canvas.height = 48;
  try { w.draw(canvas.getContext('2d'), state, 8, {}); } catch (e) { return ''; }
  var url = canvas.toDataURL();
  emoteCache[state] = url;
  return url;
}
function renderEmoteTokens(root){
  if (!root || !root.querySelectorAll) return;
  if (!world()) return;   // companion evaluates later; never mark nodes done before it can draw
  root.querySelectorAll('.msg-text').forEach(function(node){
    if (node.dataset.vxEmotes === '1') return;
    if ((node.textContent || '').indexOf('[:vool-') === -1) { return; }
    node.dataset.vxEmotes = '1';
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    var textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    textNodes.forEach(function(textNode){
      var text = textNode.nodeValue || '';
      if (text.indexOf('[:vool-') === -1) return;
      var frag = document.createDocumentFragment();
      var last = 0, match;
      EMOTE_TOKEN_RE.lastIndex = 0;
      while ((match = EMOTE_TOKEN_RE.exec(text))) {
        var state = match[1];
        var url = EMOTE_STATES.indexOf(state) !== -1 ? emoteDataUrl(state) : '';
        frag.appendChild(document.createTextNode(text.slice(last, match.index)));
        if (url) {
          var img = document.createElement('img');
          img.className = 'vx-emote';
          img.alt = 'vool ' + state;
          img.title = '[:vool-' + state + ':]';
          img.src = url;
          frag.appendChild(img);
        } else {
          frag.appendChild(document.createTextNode(match[0]));
        }
        last = match.index + match[0].length;
      }
      frag.appendChild(document.createTextNode(text.slice(last)));
      textNode.parentNode.replaceChild(frag, textNode);
    });
  });
}
function mountEmoteButton(){
  var send = document.getElementById('send');
  if (!send || document.getElementById('vxEmoteBtn')) return;
  var btn = document.createElement('button');
  btn.id = 'vxEmoteBtn';
  btn.type = 'button';
  btn.className = 'vx-btn';
  btn.title = 'VOOL emotes \\u2014 inserted as [:vool-state:] shortcodes';
  btn.setAttribute('aria-label', 'Insert a VOOL emote');
  btn.textContent = '\\u263a';
  send.parentNode.insertBefore(btn, send);
  var pop = document.createElement('div');
  pop.id = 'vxEmotePop';
  document.body.appendChild(pop);
  function fillPop(){
    if (pop.dataset.done === '1') return;
    var w = world();
    if (!w) return;
    pop.dataset.done = '1';
    EMOTE_STATES.forEach(function(state){
      var cell = document.createElement('button');
      cell.type = 'button';
      cell.title = '[:vool-' + state + ':]';
      var canvas = document.createElement('canvas');
      canvas.width = 48; canvas.height = 48;
      try { w.draw(canvas.getContext('2d'), state, 8, {}); } catch (e) {}
      cell.appendChild(canvas);
      cell.addEventListener('click', function(){
        var el = input();
        if (el) {
          var token = '[:vool-' + state + ':]';
          var at = typeof el.selectionStart === 'number' ? el.selectionStart : el.value.length;
          el.setRangeText ? el.setRangeText(token, at, at, 'end') : (el.value += token);
          notifyInput(el);
          el.focus();
        }
        pop.classList.remove('vx-open');
      });
      pop.appendChild(cell);
    });
    var note = document.createElement('div');
    note.className = 'vx-note';
    note.textContent = 'Companion-universe reactions \\u2014 cosmetics, never system truth.';
    pop.appendChild(note);
  }
  btn.addEventListener('click', function(){
    fillPop();
    var open = pop.classList.toggle('vx-open');
    if (open) {
      var r = btn.getBoundingClientRect();
      var popWidth = pop.offsetWidth || 300;
      pop.style.left = Math.max(8, Math.min(r.left - 90, window.innerWidth - popWidth - 8)) + 'px';
      pop.style.bottom = (window.innerHeight - r.top + 8) + 'px';
    }
  });
  document.addEventListener('mousedown', function(ev){
    if (pop.classList.contains('vx-open') && !pop.contains(ev.target) && ev.target !== btn) {
      pop.classList.remove('vx-open');
    }
  });
}

// ---- boot ---------------------------------------------------------------------------------
function boot(){
  // Each mount is independent; one failing (an unexpected DOM shape, a stubbed environment)
  // must not take the others down or abort scripts evaluating after this fragment.
  try { mountFilter(); } catch (e) {}
  try { mountEmoteButton(); } catch (e) {}
  var p = pageActions();
  if (p) {
    var chatId = p.displayedChat();
    var el = input();
    if (el && !el.value) {
      var draft = readDraft(chatId);
      if (draft) { el.value = draft; notifyInput(el); }
    }
    applyAccentFor(chatId);
  }
  var log = document.getElementById('log');
  if (log) {
    renderEmoteTokens(log);
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function(){ renderEmoteTokens(log); }).observe(log, { childList: true, subtree: true });
    }
    // The companion world evaluates after this fragment; historical tokens render once it can draw.
    window.addEventListener('load', function(){ setTimeout(function(){ renderEmoteTokens(log); }, 0); });
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

window.VoolComposerExtras = Object.freeze({
  chatSwitched: chatSwitched,
  draftConsumed: draftConsumed,
  restoreDraft: restoreDraft,
  renderEmoteTokens: renderEmoteTokens,
  applyFilter: function(query){ filterValue = String(query == null ? '' : query); applyFilter(); },
});
})();
"""


def render_composer_extras_fragment() -> str:
    """Composer extras as an appended fragment."""
    return "<style>" + _EXTRAS_CSS + "</style><script>" + _EXTRAS_JS + "</script>"


__all__ = ["render_composer_extras_fragment"]
