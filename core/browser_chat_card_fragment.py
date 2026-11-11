"""The browser's approval card, in the chat where the request was made.

Before this card the only place a browser request could be answered was the Browser pane, which
is closed unless the person opens it. A turn that asked to open a page sat on "Planning" for two
minutes, the request expired unseen, and the reply then told the person to ask again for a page
VOOL had in fact tried to open. The request now shows up in the chat itself, with the same two
answers the pane gives.

What this fragment is careful about:

* **It renders server state; it decides nothing itself.** A card exists only while
  ``/api/browser/state`` reports a pending proposal made by VOOL (``actor == "agent"``). Approve and
  Refuse go through the same owner door the pane uses, ``/api/browser/decide``, with the exact
  proposal id the server issued. A proposal the owner made in the pane is answered in the pane.
* **It polls only while a turn is running in the chat on screen**, driven by the page's own run
  clock (one named call site), and only when the Browser switch is on.
* **Page content never becomes HTML.** Every value is set through ``textContent``; the page picture
  is a ``data:image/jpeg`` the server produced.

Namespace ``window.VoolBrowserCard``; classes ``vbc*``.
"""

from __future__ import annotations

_CSS = """
.vbc-msg{max-width:none}
.vbc{border:1px solid rgba(207,174,110,.5);border-radius:12px;padding:12px 14px;background:var(--field,#232326);
  font-size:13px;line-height:1.5;max-width:560px}
.vbc[data-state=done]{border-color:var(--border,#39393f)}
.vbc[data-state=refused],.vbc[data-state=expired],.vbc[data-state=paused]{border-color:var(--border,#39393f);opacity:.85}
.vbc-head{display:flex;align-items:center;gap:8px;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;
  color:var(--warn,#cfae6e);font-weight:600}
.vbc[data-state=done] .vbc-head,.vbc[data-state=refused] .vbc-head,.vbc[data-state=expired] .vbc-head,.vbc[data-state=paused] .vbc-head{color:var(--muted,#aaa5a0)}
.vbc-head svg{width:15px;height:15px;flex:0 0 auto}
.vbc-verb{margin-top:6px;color:var(--ink,#e6e2dc);font-size:14px}
.vbc-target{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted,#aaa5a0);overflow-wrap:anywhere}
.vbc-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.vbc-btn{height:32px;padding:0 14px;border-radius:9px;border:1px solid var(--border,#39393f);background:var(--panel,#1b1b1e);
  color:var(--ink,#e6e2dc);font:inherit;font-size:13px;cursor:pointer}
.vbc-btn.primary{background:var(--accent,#c3b8a8);border-color:var(--accent,#c3b8a8);color:var(--accent-ink,#171513);font-weight:600}
.vbc-btn:disabled{opacity:.5;cursor:default}
.vbc-timer{font-size:12px;color:var(--muted,#aaa5a0)}
.vbc-link{background:none;border:0;padding:0;color:var(--accent,#c3b8a8);font:inherit;font-size:12.5px;cursor:pointer;margin-inline-start:auto}
.vbc-link:hover{text-decoration:underline}
.vbc-shot{display:block;margin-top:10px;width:100%;max-width:360px;border-radius:8px;border:1px solid var(--border,#39393f);cursor:pointer}
.vbc-shot[hidden]{display:none}
"""

_JS = r"""
(function(){
'use strict';
var GLOBE = "<svg viewBox='0 0 16 16' fill='none' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><circle cx='8' cy='8' r='5.5'/><path d='M2.5 8h11M8 2.5c1.6 1.6 2.3 3.5 2.3 5.5S9.6 11.9 8 13.5C6.4 11.9 5.7 10 5.7 8S6.4 4.1 8 2.5z'/></svg>";
var records = {};          // proposal id -> record
var lastPoll = {};         // chat id -> ms
var available = null, availableAt = 0;

function T(key, fallback, params){
  try { if (window.VOOLT) { var v = window.VOOLT(key, params); if (v && v !== key) return v; } } catch (e) {}
  return params ? String(fallback).replace(/\{(\w+)\}/g, function(m, k){ return params[k] != null ? String(params[k]) : m; }) : fallback;
}
function actions(){ return window.VoolPageActions || null; }
function displayedChat(){ var a = actions(); return a && a.displayedChat ? String(a.displayedChat() || '') : ''; }
function el(tag, cls, text){ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; }

function verbFor(p){
  if (p.submission) return T('composer.send', 'Send');
  var map = {navigate: T('live.open', 'Open'), click: T('live.op_click', 'Click'), type: T('live.op_type', 'Type into'),
             download: T('live.download', 'Download'), upload: T('live.upload', 'Upload')};
  return map[p.op] || T('live.open', 'Open');
}

async function checkAvailable(){
  var now = Date.now();
  if (available !== null && now - availableAt < 15000) return available;
  availableAt = now;
  try { var r = await fetch('/api/browser/settings', {cache: 'no-store'}); var d = await r.json(); available = !!(d && d.available); }
  catch (e) { available = false; }
  return available;
}

function openBrowser(){
  var btn = document.getElementById('browserBtn');
  if (btn) btn.click();
}

function build(rec){
  var card = el('div', 'vbc');
  card.setAttribute('role', 'group');
  var head = el('div', 'vbc-head');
  head.innerHTML = GLOBE;
  rec.headText = el('span', null, T('live.card_title', 'VOOL wants to use the browser'));
  head.appendChild(rec.headText);
  card.appendChild(head);
  card.setAttribute('aria-label', rec.headText.textContent);
  rec.verbEl = el('div', 'vbc-verb'); card.appendChild(rec.verbEl);
  rec.targetEl = el('div', 'vbc-target'); card.appendChild(rec.targetEl);
  rec.rowEl = el('div', 'vbc-row');
  rec.refuseEl = el('button', 'vbc-btn', T('live.refuse', 'Refuse')); rec.refuseEl.type = 'button';
  rec.approveEl = el('button', 'vbc-btn primary', T('live.approve', 'Approve once')); rec.approveEl.type = 'button';
  rec.timerEl = el('span', 'vbc-timer');
  var view = el('button', 'vbc-link', T('live.view', 'Open browser')); view.type = 'button';
  view.onclick = openBrowser;
  rec.refuseEl.onclick = function(){ decide(rec, false); };
  rec.approveEl.onclick = function(){ decide(rec, true); };
  rec.rowEl.appendChild(rec.refuseEl); rec.rowEl.appendChild(rec.approveEl); rec.rowEl.appendChild(rec.timerEl); rec.rowEl.appendChild(view);
  card.appendChild(rec.rowEl);
  rec.shotEl = el('img', 'vbc-shot'); rec.shotEl.hidden = true; rec.shotEl.alt = T('live.browser', 'Browser'); rec.shotEl.onclick = openBrowser;
  card.appendChild(rec.shotEl);
  rec.card = card;
  paint(rec);
  return card;
}

function paint(rec){
  if (!rec.card) return;
  var p = rec.proposal || {}, a = p.args || {};
  rec.card.dataset.state = rec.state;
  rec.verbEl.textContent = verbFor(p) + (a.label ? (' · ' + a.label) : '');
  rec.targetEl.textContent = a.url || a.target || '';
  var waiting = rec.state === 'waiting';
  rec.refuseEl.hidden = rec.approveEl.hidden = !waiting;
  rec.refuseEl.disabled = rec.approveEl.disabled = !!rec.busy;
  rec.timerEl.textContent = waiting && typeof rec.left === 'number'
    ? T('live.seconds_left', '{seconds} s left', {seconds: rec.left}) : '';
  var heads = {waiting: T('live.card_title', 'VOOL wants to use the browser'), running: T('live.running', 'Working'),
               done: T('live.done', 'Done'), paused: T('live.card_paused', 'You took over'), refused: T('live.card_refused', 'You said no'), expired: T('live.card_expired', 'No answer in time'),
               failed: T('live.failed', 'The page could not finish. Check the address or try again.')};
  rec.headText.textContent = heads[rec.state] || heads.waiting;
  if (rec.frame) { rec.shotEl.src = 'data:image/jpeg;base64,' + rec.frame; rec.shotEl.hidden = false; }
}

// How the browser says a proposal ended, as the card shows it.
var ENDINGS = {executed: 'done', paused: 'paused', refused: 'refused', stale_approval: 'expired', operation_timeout: 'expired'};

function place(rec){
  if (rec.chatId !== displayedChat()) return;
  var a = actions(); var host = a && a.chatLog ? a.chatLog() : null;
  if (!host) return;
  if (rec.bubble && host.contains(rec.bubble)) return;
  var bubble = el('div', 'msg assistant vbc-msg');
  bubble.setAttribute('data-browser-proposal', rec.id);
  var body = el('span', 'msg-text');
  body.appendChild(rec.card || build(rec));
  bubble.appendChild(body);
  // The card belongs right under the message that asked for it, above the reply it leads to,
  // and stays there when the log is rebuilt after the turn ends.
  var asks = host.querySelectorAll('.msg.user');
  if (rec.userIndex == null) rec.userIndex = asks.length - 1;
  var anchor = rec.userIndex >= 0 ? asks[rec.userIndex] : null;
  if (anchor) {
    while (anchor.nextElementSibling && anchor.nextElementSibling.classList.contains('vbc-msg')) anchor = anchor.nextElementSibling;
    anchor.insertAdjacentElement('afterend', bubble);
  } else {
    host.appendChild(bubble);
  }
  rec.bubble = bubble;
  try { bubble.scrollIntoView({block: 'nearest'}); } catch (e) {}
}

async function decide(rec, approve){
  if (rec.busy || rec.state !== 'waiting') return;
  rec.busy = true; paint(rec);
  try {
    var r = await fetch('/api/browser/decide', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({chat: rec.chatId, id: rec.id, approve: !!approve})});
    rec.state = r.ok ? (approve ? 'running' : 'refused') : 'expired';
  } catch (e) { /* the next poll tells the truth */ }
  rec.busy = false; paint(rec);
  follow(rec);
}

// After an answer, keep reading the outcome for a short while even if the turn has ended.
function follow(rec){
  var started = Date.now();
  (function again(){
    poll(rec.chatId, true).then(function(){
      if ((rec.state === 'running' || rec.state === 'waiting') && Date.now() - started < 60000) setTimeout(again, 1000);
    });
  })();
}

async function poll(chatId, force){
  var now = Date.now();
  if (!force && lastPoll[chatId] && now - lastPoll[chatId] < 1000) return;
  lastPoll[chatId] = now;
  var snap;
  try { var r = await fetch('/api/browser/state?chat=' + encodeURIComponent(chatId), {cache: 'no-store'}); if (!r.ok) return; snap = await r.json(); }
  catch (e) { return; }
  var p = snap && snap.pending;
  Object.keys(records).forEach(function(id){
    var rec = records[id];
    if (rec.chatId !== chatId || (p && p.id === id)) return;
    // The proposal this card asked about is no longer waiting: show how it ended.
    var ended = snap.ended && snap.ended[id];
    if (ended && (rec.state === 'waiting' || rec.state === 'running'))
      rec.state = ENDINGS[ended] || 'failed';
    if (rec.state === 'waiting') rec.state = (typeof rec.left === 'number' && rec.left <= 2) ? 'expired' : (snap.state === 'refused' ? 'refused' : 'running');
    if (rec.state === 'running' && snap.state !== 'running') rec.state = snap.state === 'ready' ? 'done' : (snap.state === 'refused' ? 'refused' : (snap.closed ? 'expired' : 'failed'));
    if (rec.state === 'done' && snap.frame && !rec.frame) rec.frame = snap.frame;
    paint(rec);
  });
  if (p && p.actor === 'agent') {
    var rec = records[p.id];
    if (!rec) { rec = records[p.id] = {id: p.id, chatId: chatId, state: 'waiting', proposal: p}; }
    rec.proposal = p; rec.left = typeof p.expires_in === 'number' ? p.expires_in : null;
    if (rec.state === 'waiting') { place(rec); paint(rec); }
  }
}

function settling(chatId){
  return Object.keys(records).some(function(id){ var r = records[id]; return r.chatId === chatId && (r.state === 'waiting' || r.state === 'running'); });
}

window.VoolBrowserCard = Object.freeze({
  /** Called once a second by the page's run clock for each turn in flight. */
  tick: async function(chatId, displayed){
    chatId = String(chatId || '');
    if (!chatId || !displayed) return;
    if (!settling(chatId) && !(await checkAvailable())) return;
    poll(chatId, false);
  },
  /** Called after the chat log is rebuilt: put this chat's cards back where they belong. */
  repaint: function(chatId){
    Object.keys(records).forEach(function(id){
      var rec = records[id];
      if (rec.chatId === String(chatId || '')) place(rec);
    });
    if (settling(String(chatId || ''))) poll(String(chatId || ''), true);
  }
});
})();
"""


def render_browser_card_fragment() -> str:
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>"
