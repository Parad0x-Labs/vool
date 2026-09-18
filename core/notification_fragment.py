"""The notification bell + inbox — the ux-pass1 alarms bound to real sources.

Three truthful sources, nothing else:

- LIFECYCLE — a run finishing in a chat the operator was NOT looking at (fed by the one named
  `finishRun` call site; the fragment drops completions of the displayed chat because the card
  in front of the operator already says it). Session-scoped: listed while this page lives.
- MODEL MARKET — typed catalog-diff events from `/api/cloud/market-events` (price rises/drops,
  FREE→PAID, delistings, and one FULLY-IDENTIFIED `new_free_model` event per model: human name,
  id, provider, observed prices, free basis, context, capabilities, evidence time and source).
  A fresh install polls, receives nothing, and the popover says "No notifications yet".

This is the INBOX (what changed while you were elsewhere). Model Radar (✦, a separate chip)
is the DISCOVERY surface: qualified findings with evidence cards, Try once and Set as default.
The popover says so; neither entry point replaces the other.

Every market alert renders from ITS OWN EVENT SNAPSHOT — the fields the catalog observation
carried — so a later catalog refresh can never relabel an old alert. Unknown fields say
"unavailable"; an observed zero price is stated as an observation with its basis, never as a
certification that a route is free. Read/unread and dismissal state persist in
`vool_notify_state_v1`; the first-ever load baselines existing events as seen (listed, not
unread) instead of replaying the whole log as new mail.

Deep links are real actions: a lifecycle item opens its chat; a market item's Inspect opens
the model menu (and the price-gate variants fire on the next pin attempt through the server's
own 409s). Unread count is presentation state only. Namespace `window.VoolNotify`; prefix `vf-`.
"""

from __future__ import annotations

_NOTIFY_CSS = """
#vfBell{position:relative;background:transparent;color:var(--muted,#9aa1af);
  border:1px solid var(--border,#262b35);border-radius:8px;padding:6px 10px;font:inherit;
  font-size:13px;cursor:pointer}
#vfBell:hover{color:var(--ink,#e8eaf0);border-color:var(--accent,#5eead4)}
#vfBadge{position:absolute;top:-6px;right:-6px;min-width:16px;height:16px;border-radius:8px;
  background:var(--warn,#f0b429);color:#201500;font-size:10px;font-weight:800;
  display:grid;place-items:center;padding:0 4px}
#vfBadge[hidden]{display:none}
#vfPop{position:fixed;z-index:1150;width:min(380px,92vw);max-height:min(560px,80vh);overflow:auto;
  background:var(--panel,#16191f);border:1px solid var(--border,#262b35);border-radius:12px;
  box-shadow:0 10px 40px rgba(0,0,0,.5)}
#vfPop[hidden]{display:none}
.vf-head{padding:10px 14px;font-size:11px;letter-spacing:.1em;color:var(--muted,#9aa1af);
  border-bottom:1px solid var(--border,#262b35);text-transform:uppercase}
.vf-item{padding:10px 14px;font-size:13px;display:flex;flex-direction:column;gap:4px;
  border-bottom:1px solid var(--border,#262b35);color:var(--ink,#e8eaf0)}
.vf-item.vf-clickable{cursor:pointer}
.vf-item.vf-clickable:hover{background:var(--field,#1d2129)}
.vf-item:last-of-type{border-bottom:none}
.vf-item small{color:var(--muted,#9aa1af)}
.vf-item .vf-line{display:flex;gap:8px;align-items:baseline}
.vf-item .vf-time{margin-left:auto;white-space:nowrap;color:var(--muted,#9aa1af);font-size:11px}
.vf-item.vf-warn{border-left:2px solid var(--warn,#f0b429)}
.vf-item.vf-bad{border-left:2px solid var(--bad,#f2545b)}
.vf-item.vf-unread .vf-title{font-weight:700}
.vf-sub{font-size:11.5px;color:var(--muted,#9aa1af)}
.vf-details{font-size:12px;color:var(--ink,#e8eaf0);background:var(--field,#1d2129);
  border:1px solid var(--border,#262b35);border-radius:8px;padding:8px 10px;display:grid;gap:3px}
.vf-details[hidden]{display:none}
.vf-row{display:flex;gap:8px;justify-content:space-between}
.vf-row b{font-weight:600;color:var(--muted,#9aa1af)}
.vf-row span{text-align:right;word-break:break-word}
.vf-row a{color:var(--accent,#5eead4)}
.vf-actions{display:flex;gap:8px;margin-top:4px}
.vf-btn{font:inherit;font-size:11.5px;border-radius:6px;padding:3px 9px;cursor:pointer;
  background:transparent;color:var(--ink,#e8eaf0);border:1px solid var(--border,#262b35)}
.vf-btn:hover{border-color:var(--accent,#5eead4)}
.vf-btn.vf-ghost{color:var(--muted,#9aa1af)}
.vf-cert{font-size:10.5px;color:var(--muted,#9aa1af);margin-top:4px;line-height:1.45}
.vf-empty{padding:14px;font-size:12px;color:var(--muted,#9aa1af)}
.vf-foot{padding:8px 14px;font-size:10.5px;color:var(--muted,#9aa1af);border-top:1px solid var(--border,#262b35);line-height:1.5}
"""

_NOTIFY_JS = """
(function(){
'use strict';
if (window.VoolNotify) return;

var ITEM_CAP = 30;
var STATE_KEY = 'vool_notify_state_v1';
var lifecycle = [];        // session-scoped: background completions this page session
var unreadLifecycle = 0;
var market = [];           // rendered from the SERVER's events, re-read every poll
var marketSeqs = [];       // seqs currently listed (ordered newest-first)
function NTF(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
var centre = [];          // the DB-backed notification centre (calendar alerts, reminders)
var centreUnread = 0;
var centreBusy = false;
var CENTRE_POLL_MS = 30000;
var state = loadState();   // {seenSeq, readSeq, dismissed:{}} — persists across reloads

function loadState(){
  var s = { seenSeq: 0, readSeq: 0, dismissed: {} };
  try {
    var raw = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
    if (raw && typeof raw === 'object') {
      if (Number.isFinite(raw.seenSeq)) s.seenSeq = Number(raw.seenSeq);
      if (Number.isFinite(raw.readSeq)) s.readSeq = Number(raw.readSeq);
      if (raw.dismissed && typeof raw.dismissed === 'object') s.dismissed = raw.dismissed;
    }
  } catch (e) {}
  return s;
}
function saveState(){
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (e) {}
}

function pageActions(){ return window.VoolPageActions || null; }
function toast(text){ var p = pageActions(); if (p && typeof p.toast === 'function') p.toast(String(text || '')); }
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function when(ts){
  var t = Date.parse(String(ts || ''));
  if (!isFinite(t)) return 'time unavailable';
  return new Date(t).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
function clock(at){
  return new Date(at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}

// ---- source 1: lifecycle (fed by the finishRun call site) ---------------------------------
function runFinished(info){
  info = info || {};
  if (info.displayed) return;   // the operator watched it end; the card already says so
  var status = String(info.status || '');
  var wording = status === 'done' ? 'completed' :
    status === 'failed' ? 'failed' :
    status === 'cancelled' ? 'was stopped' :
    status === 'awaiting_approval' ? 'needs your approval' : ('ended: ' + status);
  lifecycle.unshift({
    kind: 'lifecycle',
    cls: status === 'failed' ? 'vf-bad' : (status === 'awaiting_approval' ? 'vf-warn' : ''),
    text: 'A background chat ' + wording + (info.summary ? ' \\u2014 ' + String(info.summary).slice(0, 80) : ''),
    chatId: String(info.chatId || ''),
    at: Date.now(),
    unread: true,
  });
  if (lifecycle.length > ITEM_CAP) lifecycle.length = ITEM_CAP;
  unreadLifecycle += 1;
  paintBadge();
  if (!pop.hidden) paintList();
}

// ---- source 2: model market (typed server events, each its own snapshot) ------------------
function money(v){
  var n = Number(v);
  if (!isFinite(n)) return null;
  return '$' + (n >= 1 ? n.toFixed(2) : n.toFixed(4)) + '/1M';
}
function freeBasisWords(basis){
  if (basis === 'provider_free_variant') return 'observed basis: the provider\\u2019s own :free variant';
  if (basis === 'all_published_prices_zero') return 'observed basis: every published price is an explicit $0';
  if (basis === 'unspecified') return 'observed basis unspecified';
  return 'observed basis: ' + String(basis || 'unspecified');
}
function ctxWords(n){
  var v = Number(n) || 0;
  if (v <= 0) return 'context length unavailable';
  return Math.round(v / 1000) + 'k tokens context';
}
function pollMarket(){
  fetch('/api/cloud/market-events?after=' + (state.seenSeq - 20 > 0 ? state.seenSeq - 20 : 0))
    .then(function(r){ return r.json(); })
    .then(function(j){
      var events = (j && j.events ? j.events : []);
      var firstEver = state.seenSeq === 0;
      var maxSeq = state.seenSeq;
      events.forEach(function(ev){
        var seq = Number(ev.seq) || 0;
        if (seq > maxSeq) maxSeq = seq;
      });
      if (firstEver) {
        // BASELINE: the very first poll on this browser lists what already happened as READ
        // mail. It never replays the whole log as unread notifications.
        state.seenSeq = maxSeq;
        state.readSeq = maxSeq;
      } else if (maxSeq > state.seenSeq) {
        state.seenSeq = maxSeq;   // new arrivals become unread below (seq > readSeq)
      }
      saveState();
      marketSeqs = [];
      market = [];
      events.slice(-ITEM_CAP).reverse().forEach(function(ev){
        var seq = Number(ev.seq) || 0;
        if (state.dismissed['s' + seq]) return;
        marketSeqs.push(seq);
        market.push(ev);
      });
      paintBadge();
      if (!pop.hidden) paintList();
    })
    .catch(function(){});
}

// ---- rendering -------------------------------------------------------------------------------
var bell = document.createElement('button');
bell.id = 'vfBell';
bell.type = 'button';
bell.title = NTF('notif.bell_title', 'Notifications \u2014 background completions and watched-model market changes');
bell.setAttribute('aria-label', bell.title);
bell.innerHTML = '\\ud83d\\udd14<span id="vfBadge" hidden></span>';
var pop = document.createElement('div');
pop.id = 'vfPop';
pop.hidden = true;
pop.setAttribute('role', 'dialog');
pop.setAttribute('aria-label', NTF('notif.pop_aria', 'Notifications'));
document.body.appendChild(pop);
var badge = bell.querySelector('#vfBadge');
var card = document.createElement('div');
card.id = 'vfEventCard';
card.hidden = true;
card.setAttribute('role', 'dialog');
card.setAttribute('aria-modal', 'true');
card.setAttribute('aria-label', 'Notification details');
document.body.appendChild(card);
var cardItem = null;

function unreadCount(){
  var marketUnread = 0;
  for (var i = 0; i < marketSeqs.length; i++) if (marketSeqs[i] > state.readSeq) marketUnread += 1;
  return marketUnread + unreadLifecycle;
}
function paintBadge(){
  var n = unreadCount();
  if (n > 0) { badge.hidden = false; badge.textContent = n > 9 ? '9+' : String(n); }
  else badge.hidden = true;
}
function rowHtml(label, value, isHtml){
  return '<div class="vf-row"><b>' + esc(label) + '</b><span>' + (isHtml ? value : esc(value)) + '</span></div>';
}
function moneyPair(a, b){
  var left = money(a), right = money(b);
  if (left === null && right === null) return 'prices unavailable';
  return (left === null ? 'input unavailable' : 'input ' + left)
    + ' \\u00b7 ' + (right === null ? 'output unavailable' : 'output ' + right);
}
function marketTitle(ev){
  var name = String(ev.display_name || '').trim();
  var id = String(ev.model || '');
  if (ev.type === 'new_free_model')
    return 'Observed free: ' + (name || 'name unavailable') + (id ? ' (' + id + ')' : '');
  if (ev.type === 'free_to_paid') return 'Watched model stopped being free: ' + (id || 'unknown model');
  if (ev.type === 'paid_to_free') return 'Watched model is now published at $0: ' + (id || 'unknown model');
  if (ev.type === 'price_increased') return 'Watched model price rose: ' + (id || 'unknown model');
  if (ev.type === 'price_decreased') return 'Watched model price dropped: ' + (id || 'unknown model');
  if (ev.type === 'model_delisted') return 'Watched model delisted by the provider: ' + (id || 'unknown model');
  return 'Market change: ' + (id || 'unknown model');
}
function marketDetails(ev){
  var html = '';
  if (ev.type === 'new_free_model') {
    html += rowHtml('model', String(ev.model || 'unavailable'));
    html += rowHtml('name', String(ev.display_name || 'unavailable'));
    html += rowHtml('provider', String(ev.provider_id || 'unavailable'));
    html += rowHtml('observed prices', moneyPair(ev.prices && ev.prices.input_usd_per_m, ev.prices && ev.prices.output_usd_per_m));
    html += rowHtml('free qualification', freeBasisWords(ev.free_basis));
    html += rowHtml('context', ctxWords(ev.context_length));
    html += rowHtml('capabilities',
      (ev.supports_tools === true ? 'tools \\u2713' : ev.supports_tools === false ? 'tools \\u2717' : 'tools unavailable')
      + ' \\u00b7 ' + (ev.supports_images === true ? 'images \\u2713' : ev.supports_images === false ? 'images \\u2717' : 'images unavailable'));
  } else {
    html += rowHtml('model', String(ev.model || 'unavailable'));
    var before = ev.before || {}, after = ev.after || {};
    html += rowHtml('before', moneyPair(before.prompt_usd_per_m, before.completion_usd_per_m)
      + (before.free === true ? ' \\u00b7 free' : before.free === false ? ' \\u00b7 paid' : ''));
    html += rowHtml('after', moneyPair(after.prompt_usd_per_m, after.completion_usd_per_m)
      + (after.free === true ? ' \\u00b7 free' : after.free === false ? ' \\u00b7 paid' : ''));
  }
  html += rowHtml('observed', when(ev.observed_at || ev.ts));
  var source = String(ev.source_feed || 'catalog refresh');
  var url = String(ev.evidence_url || '');
  html += rowHtml('source', url
    ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(source) + '</a>'
    : source + ' (no source URL recorded)', true);
  return html;
}
function pollCentre(){
  if (centreBusy) return;
  centreBusy = true;
  fetch('/api/notifications?after=0&limit=50')
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok) return;
      centre = Array.isArray(j.items) ? j.items : [];
      centreUnread = Number(j.unread) || 0;
      paintBadge();
      if (!pop.hidden) paintList();
    })
    .catch(function(){})
    .then(function(){ centreBusy = false; });
}

function centreAction(item, action, minutes){
  var body = {notification_id: item.notification_id, action: action};
  if (minutes) body.minutes = minutes;
  return fetch('/api/notifications/action', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  })
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (j && !j.ok) toast(j.detail || (NTF('notif.action_failed', 'That did not work (') + (j.reason || 'unknown') + ').'));
      else if (action === 'snooze' && j && j.snoozed_until) toast(NTF('notif.snoozed_until', 'Snoozed until ') + whenText(j.snoozed_until, false));
      pollCentre();
      return j;
    })
    .catch(function(){ toast(NTF('notif.no_answer', 'VOOL did not answer; nothing was changed.')); });
}

function centreRow(item, index){
  var p = item.payload || {};
  var join = httpsLink(p.meeting_url);
  var acts = '';
  if (snoozable(item)) acts += '<button type="button" data-vc-act="snooze" data-vc="' + index + '">' + NTF('notif.snooze_10', 'Snooze 10 min') + '</button>';
  if (join) acts += '<a href="' + esc(join) + '" target="_blank" rel="noopener noreferrer" data-vc-join="' + index + '">Join</a>';
  acts += '<button type="button" data-vc-act="dismiss" data-vc="' + index + '">' + NTF('notif.dismiss', 'Dismiss') + '</button>';
  var cls = 'vf-item vf-centre' + (item.source_kind === 'calendar_catch_up' ? ' vf-warn' : '') + (item.read_at ? '' : ' vf-unread');
  return '<div class="' + cls + '" data-vc-open="' + index + '">' +
    '<div class="vf-main"><span>' + esc(item.title) + '</span><small>' + esc(centreMeta(item)) + '</small></div>' +
    '<div class="vf-acts">' + acts + '</div></div>';
}

function centreMeta(item){
  var p = item.payload || {};
  if (item.source_kind === 'calendar_alert') {
    return [(p.late ? 'shown late' : ''), whenText(p.start_local || p.start_utc, false), p.calendar_name, p.location]
      .filter(Boolean).join(' \\u00b7 ');
  }
  if (item.source_kind === 'reminder') return 'Reminder' + (p.due_local ? ' \\u00b7 due ' + p.due_local : '');
  return String(item.body || '');
}

function snoozable(item){
  return (item.source_kind === 'calendar_alert' || item.source_kind === 'reminder') && !item.dismissed_at;
}

function whenText(iso, withDay){
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  var time = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  return withDay ? d.toLocaleDateString([], {weekday:'short', month:'short', day:'numeric'}) + ' ' + time : time;
}

function httpsLink(url){
  var s = String(url || '');
  if (s.slice(0, 8).toLowerCase() !== 'https://' || /[\\s"'<>]/.test(s)) return '';
  return s;
}

function itemHtml(item, index){
  if (item.kind === 'lifecycle') {
    return '<div class="vf-item vf-clickable ' + (item.cls || '') + (item.unread ? ' vf-unread' : '') + '" data-vf="' + index + '">'
      + '<div class="vf-line"><span class="vf-title">' + esc(item.text) + '</span>'
      + '<small class="vf-time">' + clock(item.at) + '</small></div>'
      + '<div class="vf-sub">' + NTF('notif.opens_chat', 'Opens its chat') + '</div></div>';
  }
  var ev = item.ev;
  var unread = item.seq > state.readSeq;
  return '<div class="vf-item ' + (ev.type === 'price_increased' || ev.type === 'free_to_paid' ? 'vf-warn ' : '')
    + (ev.type === 'model_delisted' ? 'vf-bad ' : '') + (unread ? 'vf-unread' : '') + '" data-vf="' + index + '">'
    + '<div class="vf-line"><span class="vf-title">' + esc(marketTitle(ev)) + '</span>'
    + '<small class="vf-time">' + when(ev.observed_at || ev.ts) + '</small></div>'
    + '<div class="vf-details" data-vf-details="' + index + '" hidden>' + marketDetails(ev) + '</div>'
    + '<div class="vf-actions">'
    + '<button type="button" class="vf-btn" data-vf-act="details">Details</button>'
    + '<button type="button" class="vf-btn" data-vf-act="inspect">Inspect in Models</button>'
    + '<button type="button" class="vf-btn vf-ghost" data-vf-act="dismiss">Dismiss</button>'
    + '</div>'
    + '<div class="vf-cert">Observed catalog values, snapshotted at alert time \\u2014 not a certification. '
    + 'Selecting a paid route still follows your approvals.</div>'
    + '</div>';
}
function paintList(){
  var rows = [];
  var i;
  var centreRows = centre.map(centreRow).join('');
  for (i = 0; i < market.length; i++) rows.push(itemHtml({ kind: 'market', ev: market[i], seq: marketSeqs[i] }, rows.length));
  for (i = 0; i < lifecycle.length; i++) rows.push(itemHtml(lifecycle[i], rows.length));
  pop.innerHTML = '<div class="vf-head">' + NTF('notif.head', 'Notifications \u00b7 background chats \u00b7 model market') + '</div>'
    + (centreRows + rows.join('') ? centreRows + rows.join('') : '<div class="vf-empty">' + NTF('notif.empty', 'No notifications yet \u2014 background completions and real catalog changes will land here.') + '</div>')
    + '<div class="vf-foot">This is the inbox: what changed while you were elsewhere. '
    + 'Model Radar (\\u2726) is where qualified free models and \\u226550% price cuts live, with Try once and Set as default.</div>';
}
function openPop(){
  paintList();
  pop.hidden = false;
  // Opening the inbox marks everything visible as read — unread is a "you have not looked"
  // signal, not a permanent state. Dismissal is separate and explicit.
  state.readSeq = Math.max(state.readSeq, state.seenSeq);
  unreadLifecycle = 0;
  lifecycle.forEach(function(item){ item.unread = false; });
  saveState();
  paintBadge();
  var r = bell.getBoundingClientRect();
  pop.style.top = (r.bottom + 6) + 'px';
  pop.style.left = Math.max(8, Math.min(r.right - 380, window.innerWidth - 388)) + 'px';
}
function openCard(item){
  cardItem = item;
  var p = item.payload || {};
  var join = httpsLink(p.meeting_url);
  var web = httpsLink(p.web_url);
  var rows = [];
  if (item.source_kind === 'calendar_alert') {
    rows.push(['When', whenText(p.start_local || p.start_utc, true) + (p.end_local ? ' \\u2013 ' + whenText(p.end_local, false) : '')]);
    if (p.late) rows.push(['Note', 'This alert was shown later than its lead time.']);
    if (p.calendar_name) rows.push(['Calendar', p.calendar_name + (p.account_label ? ' \\u00b7 ' + p.account_label : '')]);
    if (p.location) rows.push(['Where', p.location]);
    rows.push(['Alert', p.lead_minutes ? p.lead_minutes + ' min before' : 'at the start']);
  } else if (item.source_kind === 'calendar_catch_up') {
    rows.push(['Missed', (p.titles || []).join(', ') + (p.more ? ' and ' + p.more + ' more' : '')]);
  } else if (p.due_local) {
    rows.push(['Due', p.due_local]);
  }
  var mac = (item.channels || {}).macos;
  if (mac && mac.state) rows.push(['macOS', (MAC_STATE_TEXT[mac.state] || mac.state) + (mac.detail ? ' \\u00b7 ' + mac.detail : '')]);
  var html = '<div class="vf-card"><div class="vf-card-head"><strong>' + esc(item.title) + '</strong>' +
    '<button type="button" class="vf-x" data-vcard="close" aria-label="Close">\\u00d7</button></div>';
  rows.forEach(function(row){ html += '<div class="vf-row"><span>' + esc(row[0]) + '</span><b>' + esc(row[1]) + '</b></div>'; });
  html += '<div class="vf-card-acts">';
  if (join) html += '<a class="vf-btn vf-primary" href="' + esc(join) + '" target="_blank" rel="noopener noreferrer">Join meeting</a>';
  if (web) html += '<a class="vf-btn" href="' + esc(web) + '" target="_blank" rel="noopener noreferrer">Open in calendar</a>';
  if (item.session_id && typeof window.openSession === 'function') html += '<button type="button" class="vf-btn" data-vcard="chat">Open chat</button>';
  if (snoozable(item)) {
    html += '<button type="button" class="vf-btn" data-vcard="snooze" data-min="5">' + NTF('notif.snooze_5', 'Snooze 5 min') + '</button>' +
      '<button type="button" class="vf-btn" data-vcard="snooze" data-min="15">15 min</button>' +
      '<button type="button" class="vf-btn" data-vcard="snooze" data-min="60">1 hour</button>';
  }
  if (!item.dismissed_at) html += '<button type="button" class="vf-btn" data-vcard="dismiss">' + NTF('notif.dismiss', 'Dismiss') + '</button>';
  html += '</div>';
  if (item.source_kind === 'calendar_alert' && item.event_key) {
    html += '<form class="vf-policy" data-vcard-form="policy"><label>Remind me ' +
      '<input name="minutes" inputmode="numeric" autocomplete="off" value="' + esc(p.lead_minutes == null ? '' : String(p.lead_minutes)) + '">' +
      ' minutes before this event</label><div class="vf-card-acts">' +
      '<button type="submit" class="vf-btn">Save</button>' +
      '<button type="button" class="vf-btn" data-vcard="policy-off">No alerts for this event</button></div>' +
      '<small>Separate several lead times with commas, for example 30, 5. VOOL does not change the alarms saved in your calendar.</small></form>';
  }
  html += '</div>';
  card.innerHTML = html;
  card.hidden = false;
  if (!item.read_at) centreAction(item, 'open');
}
function closeCard(){ card.hidden = true; cardItem = null; }
// What the macOS channel proved about an item, in words. There is no 'shown' state: macOS does not report one.
var MAC_STATE_TEXT = {
  queued: NTF('notif.mac.queued', 'handed to macOS'), submitted: NTF('notif.mac.submitted', 'accepted by macOS'), listed: NTF('notif.mac.listed', 'listed in Notification Center'),
  acknowledged: NTF('notif.mac.acknowledged', 'answered on the macOS notification'), suppressed: NTF('notif.mac.suppressed', 'not sent to macOS'), failed: NTF('notif.mac.failed', 'macOS refused it'),
  withdraw_requested: NTF('notif.mac.withdraw_requested', 'removal asked of macOS'), withdrawn: NTF('notif.mac.withdrawn', 'removed from macOS'),
};
// Opened by the window host when the person clicks a macOS notification.
function openItem(notificationId){
  var id = String(notificationId || '');
  if (!id) return false;
  var found = centre.filter(function(entry){ return entry.notification_id === id; })[0];
  if (found) { openCard(found); return true; }
  fetch('/api/notifications?after=0&limit=50&include_dismissed=1')
    .then(function(r){ return r.json(); })
    .then(function(j){
      var match = ((j && Array.isArray(j.items)) ? j.items : []).filter(function(entry){ return entry.notification_id === id; })[0];
      if (match) openCard(match);
    })
    .catch(function(){});
  return true;
}
card.addEventListener('click', function(ev){
  if (ev.target === card) { closeCard(); return; }
  var control = ev.target.closest('[data-vcard]');
  if (!control || !cardItem) return;
  var what = control.getAttribute('data-vcard');
  var current = cardItem;
  if (what === 'close') closeCard();
  else if (what === 'snooze') { closeCard(); centreAction(current, 'snooze', Number(control.getAttribute('data-min')) || 10); }
  else if (what === 'dismiss') { closeCard(); centreAction(current, 'dismiss'); }
  else if (what === 'policy-off') { closeCard(); savePolicy(current, []); }
  else if (what === 'chat') { closeCard(); window.openSession(current.session_id); }
});
card.addEventListener('submit', function(ev){
  var form = ev.target.closest('form[data-vcard-form="policy"]');
  if (!form || !cardItem) return;
  ev.preventDefault();
  var raw = String(form.elements.minutes ? form.elements.minutes.value : '');
  var parts = raw.split(',').map(function(part){ return part.trim(); }).filter(Boolean);
  var values = parts.map(Number);
  if (!parts.length || values.some(function(v){ return !isFinite(v) || v < 0 || Math.floor(v) !== v; })) {
    toast('Enter whole minutes, for example 15 or 30, 5.');
    return;
  }
  var current = cardItem;
  closeCard();
  savePolicy(current, values);
});
document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape' && !card.hidden) closeCard(); });

bell.addEventListener('click', function(){ pop.hidden ? openPop() : (pop.hidden = true); });
pop.addEventListener('click', function(ev){
  var btn = ev.target.closest('[data-vf-act]');
  if (btn) {
    ev.stopPropagation();
    var item = itemAt(btn);
    if (!item) return;
    if (btn.getAttribute('data-vf-act') === 'details') {
      var box = pop.querySelector('[data-vf-details="' + btn.closest('[data-vf]').getAttribute('data-vf') + '"]');
      if (box) box.hidden = !box.hidden;
      return;
    }
    if (btn.getAttribute('data-vf-act') === 'dismiss') {
      if (item.kind === 'market') {
        state.dismissed['s' + item.seq] = 1;
        saveState();
        pollMarket();
      } else {
        lifecycle.splice(lifecycle.indexOf(item), 1);
        paintList();
      }
      return;
    }
    if (btn.getAttribute('data-vf-act') === 'inspect') {
      pop.hidden = true;
      var p = pageActions();
      if (p) p.openModelMenu();
      return;
    }
    return;
  }
  var row = ev.target.closest('[data-vf]');
  if (!row) return;
  var item2 = itemAt(row);
  pop.hidden = true;
  if (!item2) return;
  if (item2.kind === 'lifecycle' && item2.chatId && typeof window.openSession === 'function') {
    window.openSession(item2.chatId);
  } else if (item2.kind === 'market') {
    var p2 = pageActions();
    if (p2) p2.openModelMenu();
  }
});
function itemAt(node){
  var row = node.closest ? node.closest('[data-vf]') : null;
  if (!row) return null;
  var index = Number(row.getAttribute('data-vf'));
  var before = 0, i;
  for (i = 0; i < market.length; i++) {
    if (before === index) return { kind: 'market', ev: market[i], seq: marketSeqs[i] };
    before += 1;
  }
  for (i = 0; i < lifecycle.length; i++) {
    if (before === index) return lifecycle[i];
    before += 1;
  }
  return null;
}
document.addEventListener('mousedown', function(ev){
  if (!pop.hidden && !pop.contains(ev.target) && ev.target !== bell && !bell.contains(ev.target)) pop.hidden = true;
});

function mount(){
  var anchor = document.getElementById('panelBtn');
  if (anchor && anchor.parentNode && !document.getElementById('vfBell')) {
    anchor.parentNode.insertBefore(bell, anchor);
  }
  pollMarket();
  setInterval(pollMarket, 60000);
  pollCentre();
  setInterval(pollCentre, CENTRE_POLL_MS);
  window.addEventListener('focus', pollCentre);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();

window.VoolNotify = Object.freeze({
  runFinished: runFinished,
  pending: function(){ return market.length + lifecycle.length; },
  unread: function(){ return unreadCount(); },
  _state: function(){ return JSON.parse(JSON.stringify(state)); },
  _items: function(){ return { market: market.slice(), lifecycle: lifecycle.slice() }; },
});
})();
"""


def render_notification_fragment() -> str:
    """The notification bell + notification centre as an appended fragment."""
    return "<style>" + _NOTIFY_CSS + "</style><script>" + _NOTIFY_JS + "</script>"


__all__ = ["render_notification_fragment"]
