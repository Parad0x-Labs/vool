"""One durable inbox for approvals, reminders, background work and model offers.

All read and dismissal state belongs to notification_center. Model discovery and
selection stay behind their existing evidence and approval authorities.
"""

from __future__ import annotations

_NOTIFY_CSS = """
.vf-tabs{display:flex;gap:4px;padding:8px}.vf-tabs button{font:inherit;font-size:12px;background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:6px;cursor:pointer}.vf-tabs button[aria-selected="true"]{color:var(--ink);background:var(--field)}
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
.vf-main{font:inherit;text-align:left;color:inherit;background:transparent;border:0;padding:0;cursor:pointer;display:grid;gap:4px}
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

var STATE_KEY = 'vool_notify_state_v1';
var section = 'needs';
function NTF(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
/* The {param}-carrying twin: the bundle's own formatter resolves locale placeholders and
   plurals; the inline English fallback formats identically without a bundle. */
function NTFF(key, fallback, params){
  var t = NTF(key, fallback);
  try {
    if (typeof VOOLFMT === 'function') return VOOLFMT(t, params || {});
  } catch (e) {}
  return String(t).replace(/\\{(\\w+)\\}/g, function(m, name){ return (params && params[name] != null) ? String(params[name]) : m; });
}
var centre = [];          // the DB-backed notification centre (calendar alerts, reminders)
var centreUnread = 0;
var centreBusy = false;
var centreFlight = null;
var audioCursor = null;
var CENTRE_POLL_MS = 3000;
var state = loadState();   // one-time migration of the previous browser inbox

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
function pageActions(){ return window.VoolPageActions || null; }
function toast(text){ var p = pageActions(); if (p && typeof p.toast === 'function') p.toast(String(text || '')); }
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function when(ts){
  var t = Date.parse(String(ts || ''));
  if (!isFinite(t)) return NTF('notif.time_unavailable', 'time unavailable');
  return new Date(t).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
function clock(at){
  return new Date(at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}

// Completion facts were already recorded by the runtime, not authored in the browser.
function runFinished(info){
  return pollCentre().then(pollCentre).then(function(){
    if (info && info.displayed && info.turnId) {
      return readItems(centre.filter(function(item){
        return item.source_kind === 'background_run' && item.session_id === info.chatId
          && (item.payload || {}).turn_id === info.turnId;
      }));
    }
  });
}

// ---- source 2: model market (typed server events, each its own snapshot) ------------------
function money(v){
  if (v === null || v === undefined || v === '') return null;
  var n = Number(v);
  if (!isFinite(n)) return null;
  return '$' + (n >= 1 ? n.toFixed(2) : n.toFixed(4)) + '/1M';
}
function freeBasisWords(basis){
  if (basis === 'provider_free_variant') return NTF('notif.basis.provider_free', 'observed basis: the provider\\u2019s own :free variant');
  if (basis === 'all_published_prices_zero') return NTF('notif.basis.all_zero', 'observed basis: every published price is an explicit $0');
  if (basis === 'unspecified') return NTF('notif.basis.unspecified', 'observed basis unspecified');
  return NTFF('notif.basis.other', 'observed basis: {basis}', { basis: String(basis || 'unspecified') });
}
function ctxWords(n){
  var v = Number(n) || 0;
  if (v <= 0) return NTF('notif.ctx_unavailable', 'context length unavailable');
  return NTFF('notif.ctx_tokens', '{n}k tokens context', { n: Math.round(v / 1000) });
}
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
card.setAttribute('aria-label', NTF('notif.details_aria', 'Notification details'));
document.body.appendChild(card);
var cardItem = null;

function unreadCount(){ return centreUnread; }
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
  if (left === null && right === null) return NTF('notif.prices_unavailable', 'prices unavailable');
  return (left === null ? NTF('notif.input_unavailable', 'input unavailable') : NTFF('notif.input_price', 'input {price}', { price: left }))
    + ' \\u00b7 ' + (right === null ? NTF('notif.output_unavailable', 'output unavailable') : NTFF('notif.output_price', 'output {price}', { price: right }));
}
function marketTitle(ev){
  var name = String(ev.display_name || '').trim();
  var id = String(ev.model || '');
  var model = id || NTF('notif.model_unavailable', 'unknown model');
  if (ev.type === 'new_free_model') {
    if (!name) name = NTF('notif.name_unavailable', 'name unavailable');
    return id ? NTFF('notif.market.new_free_id', 'Observed free: {name} ({id})', { name: name, id: id })
              : NTFF('notif.market.new_free', 'Observed free: {name}', { name: name });
  }
  if (ev.type === 'free_to_paid') return NTFF('notif.market.free_to_paid', 'Watched model stopped being free: {model}', { model: model });
  if (ev.type === 'paid_to_free') return NTFF('notif.market.paid_to_free', 'Watched model is now published at $0: {model}', { model: model });
  if (ev.type === 'price_increased') return NTFF('notif.market.price_increased', 'Watched model price rose: {model}', { model: model });
  if (ev.type === 'price_decreased') return NTFF('notif.market.price_decreased', 'Watched model price dropped: {model}', { model: model });
  if (ev.type === 'model_delisted') return NTFF('notif.market.delisted', 'Watched model delisted by the provider: {model}', { model: model });
  return NTFF('notif.market.change', 'Market change: {model}', { model: model });
}
function marketDetails(ev){
  var html = '';
  if (ev.type === 'new_free_model') {
    html += rowHtml(NTF('notif.row.model', 'model'), String(ev.model || 'unavailable'));
    html += rowHtml(NTF('notif.row.name', 'name'), String(ev.display_name || 'unavailable'));
    html += rowHtml(NTF('notif.row.provider', 'provider'), String(ev.provider_id || 'unavailable'));
    html += rowHtml(NTF('notif.row.prices', 'observed prices'), moneyPair(ev.prices && ev.prices.input_usd_per_m, ev.prices && ev.prices.output_usd_per_m));
    html += rowHtml(NTF('notif.row.qualification', 'free qualification'), freeBasisWords(ev.free_basis));
    html += rowHtml(NTF('notif.row.context', 'context'), ctxWords(ev.context_length));
    html += rowHtml(NTF('notif.row.capabilities', 'capabilities'),
      (ev.supports_tools === true ? NTF('notif.tools', 'tools') + ' \\u2713' : ev.supports_tools === false ? NTF('notif.tools', 'tools') + ' \\u2717' : NTF('notif.tools_unavailable', 'tools unavailable'))
      + ' \\u00b7 ' + (ev.supports_images === true ? NTF('notif.images', 'images') + ' \\u2713' : ev.supports_images === false ? NTF('notif.images', 'images') + ' \\u2717' : NTF('notif.images_unavailable', 'images unavailable')));
  } else {
    html += rowHtml(NTF('notif.row.model', 'model'), String(ev.model || 'unavailable'));
    var before = ev.before || {}, after = ev.after || {};
    html += rowHtml(NTF('notif.row.before', 'before'), moneyPair(before.prompt_usd_per_m, before.completion_usd_per_m)
      + (before.free === true ? ' \\u00b7 ' + NTF('notif.free', 'free') : before.free === false ? ' \\u00b7 ' + NTF('notif.paid', 'paid') : ''));
    html += rowHtml(NTF('notif.row.after', 'after'), moneyPair(after.prompt_usd_per_m, after.completion_usd_per_m)
      + (after.free === true ? ' \\u00b7 ' + NTF('notif.free', 'free') : after.free === false ? ' \\u00b7 ' + NTF('notif.paid', 'paid') : ''));
  }
  html += rowHtml(NTF('notif.row.observed', 'observed'), when(ev.observed_at || ev.ts));
  var source = String(ev.source_feed || NTF('notif.catalog_refresh', 'catalog refresh'));
  var url = String(ev.evidence_url || '');
  html += rowHtml(NTF('notif.row.source', 'source'), url
    ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(source) + '</a>'
    : source + ' (' + NTF('notif.no_source_url', 'no source URL recorded') + ')', true);
  return html;
}
function pollCentre(){
  if (centreBusy) return centreFlight;
  centreBusy = true;
  centreFlight = fetch('/api/notifications?after=0&limit=200')
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok) return;
      var previous = audioCursor;
      audioCursor = Number(j.cursor) || 0;
      centre = Array.isArray(j.items) ? j.items : [];
      centreUnread = Number(j.unread) || 0;
      // The first read establishes observation, never plays historical unread items.
      if (previous !== null && audioCursor > previous && window.VoolNotificationAudio && window.VoolNotificationAudio.ready()) {
        return fetch('/api/notifications/audio', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({after:previous})})
          .then(function(r){return r.json();}).then(function(a){
            if (a.ok && a.notification_ids && a.notification_ids.length && window.VoolNotificationAudio) return window.VoolNotificationAudio.cue();
          }).catch(function(){}).then(function(){paintBadge(); if (!pop.hidden) paintList();});
      }
      centreUnread = Number(j.unread) || 0;
      paintBadge();
      if (!pop.hidden) paintList();
    })
    .catch(function(){})
    .then(function(){ centreBusy = false; });
  return centreFlight;
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
  if (p.market_event && !p.finding) {
    return '<div data-vmarket="' + index + '" class="vf-item' + (item.read_at ? '' : ' vf-unread') + '"><span class="vf-title">' + esc(marketTitle(p.market_event)) + '</span>'
      + '<div class="vf-details">' + marketDetails(p.market_event) + '</div>'
      + '<div class="vf-actions"><button type="button" data-vc-open="' + index + '">' + NTF('notif.inspect_models', 'Inspect in Models') + '</button>'
      + '<button type="button" data-vc-act="dismiss" data-vc="' + index + '">' + NTF('notif.dismiss', 'Dismiss') + '</button></div>'
      + '<small>' + NTF('notif.offer_cert', 'Observed catalog values, not a certification. Selecting a paid route still follows your approvals.') + '</small></div>';
  }
  var join = httpsLink(p.meeting_url);
  var acts = '';
  if (snoozable(item)) acts += '<button type="button" data-vc-act="snooze" data-vc="' + index + '">' + NTF('notif.snooze_10', 'Snooze 10 min') + '</button>';
  if (join) acts += '<a href="' + esc(join) + '" target="_blank" rel="noopener noreferrer" data-vc-join="' + index + '">' + NTF('notif.join', 'Join') + '</a>';
  acts += '<button type="button" data-vc-act="dismiss" data-vc="' + index + '">' + NTF('notif.dismiss', 'Dismiss') + '</button>';
  var cls = 'vf-item vf-centre' + (item.source_kind === 'calendar_catch_up' ? ' vf-warn' : '') + (item.read_at ? '' : ' vf-unread');
  return '<div class="' + cls + '">' +
    '<button type="button" class="vf-main" data-vc-open="' + index + '"><span class="vf-title">' + esc(item.title) + '</span><small>' + esc(centreMeta(item)) + '</small></button>' +
    '<div class="vf-acts">' + acts + '</div></div>';
}

function centreMeta(item){
  var p = item.payload || {};
  if (item.source_kind === 'calendar_alert') {
    return [(p.late ? NTF('notif.shown_late', 'shown late') : ''), whenText(p.start_local || p.start_utc, false), p.calendar_name, p.location]
      .filter(Boolean).join(' \\u00b7 ');
  }
  if (item.source_kind === 'reminder') return NTF('notif.reminder', 'Reminder') + (p.due_local ? ' \\u00b7 ' + NTF('notif.due_label', 'due') + ' ' + p.due_local : '');
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

function itemSection(item){
  var p = item.payload || {};
  if (p.section) return p.section;
  return ['calendar_alert', 'calendar_catch_up', 'reminder'].indexOf(item.source_kind) >= 0 ? 'needs' : 'updates';
}
function visibleItems(){ return centre.filter(function(item){ return itemSection(item) === section; }); }
function paintList(){
  var tabs = [['needs', NTF('notif.tab.needs', 'Needs you')], ['updates', NTF('notif.tab.updates', 'Updates')], ['offers', NTF('notif.tab.offers', 'Model offers')]];
  var nav = tabs.map(function(tab){
    var n = centre.filter(function(item){ return itemSection(item) === tab[0] && !item.read_at; }).length;
    return '<button type="button" role="tab" aria-selected="' + (section === tab[0]) + '" data-vf-section="' + tab[0] + '">' + tab[1] + (n ? ' (' + n + ')' : '') + '</button>';
  }).join('');
  var rows = [];
  centre.forEach(function(item, index){ if (itemSection(item) === section) rows.push(centreRow(item, index)); });
  pop.innerHTML = '<div class="vf-head">' + NTF('notif.pop_title', 'Notifications') + '</div><div class="vf-tabs" role="tablist" aria-label="' + NTF('notif.sections_aria', 'Notification sections') + '">' + nav + '</div>'
    + (rows.join('') || '<div class="vf-empty">' + NTF('notif.empty', 'Nothing here yet.') + '</div>')
    + '<div class="vf-foot"><button type="button" data-vf-discover>' + NTF('notif.discover', 'Discover models') + '</button> \\u00b7 ' + NTF('notif.foot_note', 'Dismiss hides an inbox item; your alerts and model choice stay unchanged.') + '</div>';
}
function readItems(items){
  var ids = items.filter(function(item){ return !item.read_at; }).map(function(item){ return item.notification_id; });
  if (!ids.length) return Promise.resolve();
  return fetch('/api/notifications/read', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({notification_ids: ids})})
    .then(function(r){ return r.json(); }).then(function(j){
    if (j && j.ok) return pollCentre();
    toast(NTF('notif.read_fail', 'Could not save read state.'));
    }).catch(function(){ toast(NTF('notif.read_fail', 'Could not save read state.')); });
}
function openPop(){
  paintList();
  pop.hidden = false;
  readItems(visibleItems());
  var r = bell.getBoundingClientRect();
  pop.style.top = (r.bottom + 6) + 'px';
  pop.style.left = Math.max(8, Math.min(r.right - 380, window.innerWidth - 388)) + 'px';
}
function openCard(item){
  centreAction(item, 'open');
  var payload = item.payload || {};
  if (payload.finding) {
    pop.hidden = true;
    if (window.VoolModelRadar) window.VoolModelRadar.open(payload.finding.fingerprint);
    return;
  }
  if (payload.market_event) {
    pop.hidden = true;
    var actions = pageActions();
    if (actions) actions.openModelMenu();
    return;
  }
  cardItem = item;
  var p = item.payload || {};
  var join = httpsLink(p.meeting_url);
  var web = httpsLink(p.web_url);
  var rows = [];
  if (item.source_kind === 'calendar_alert') {
    rows.push([NTF('notif.row.when', 'When'), whenText(p.start_local || p.start_utc, true) + (p.end_local ? ' \\u2013 ' + whenText(p.end_local, false) : '')]);
    if (p.late) rows.push([NTF('notif.row.note', 'Note'), NTF('notif.late_note', 'This alert was shown later than its lead time.')]);
    if (p.calendar_name) rows.push([NTF('notif.row.calendar', 'Calendar'), p.calendar_name + (p.account_label ? ' \\u00b7 ' + p.account_label : '')]);
    if (p.location) rows.push([NTF('notif.row.where', 'Where'), p.location]);
    rows.push([NTF('notif.row.alert', 'Alert'), p.lead_minutes ? NTFF('notif.lead_before', '{n} min before', { n: p.lead_minutes }) : NTF('notif.at_start', 'at the start')]);
  } else if (item.source_kind === 'calendar_catch_up') {
    rows.push([NTF('notif.row.missed', 'Missed'), (p.titles || []).join(', ') + NTFF('notif.missed_more', ' and {n} more', { n: p.more })]);
  } else if (p.due_local) {
    rows.push([NTF('notif.row.due', 'Due'), p.due_local]);
  }
  var mac = (item.channels || {}).macos;
  if (mac && mac.state) rows.push([NTF('notif.row.macos', 'macOS'), (MAC_STATE_TEXT[mac.state] || mac.state) + (mac.detail ? ' \\u00b7 ' + mac.detail : '')]);
  var html = '<div class="vf-card"><div class="vf-card-head"><strong>' + esc(item.title) + '</strong>' +
    '<button type="button" class="vf-x" data-vcard="close" aria-label="' + NTF('notif.close', 'Close') + '">\\u00d7</button></div>';
  rows.forEach(function(row){ html += '<div class="vf-row"><span>' + esc(row[0]) + '</span><b>' + esc(row[1]) + '</b></div>'; });
  html += '<div class="vf-card-acts">';
  if (join) html += '<a class="vf-btn vf-primary" href="' + esc(join) + '" target="_blank" rel="noopener noreferrer">' + NTF('notif.join_meeting', 'Join meeting') + '</a>';
  if (web) html += '<a class="vf-btn" href="' + esc(web) + '" target="_blank" rel="noopener noreferrer">' + NTF('notif.open_calendar', 'Open in calendar') + '</a>';
  if (item.session_id && typeof window.openSession === 'function') html += '<button type="button" class="vf-btn" data-vcard="chat">' + NTF('notif.open_chat', 'Open chat') + '</button>';
  if (snoozable(item)) {
    html += '<button type="button" class="vf-btn" data-vcard="snooze" data-min="5">' + NTF('notif.snooze_5', 'Snooze 5 min') + '</button>' +
      '<button type="button" class="vf-btn" data-vcard="snooze" data-min="15">' + NTF('notif.snooze_15', '15 min') + '</button>' +
      '<button type="button" class="vf-btn" data-vcard="snooze" data-min="60">' + NTF('notif.snooze_60', '1 hour') + '</button>';
  }
  if (!item.dismissed_at) html += '<button type="button" class="vf-btn" data-vcard="dismiss">' + NTF('notif.dismiss', 'Dismiss') + '</button>';
  html += '</div>';
  if (item.source_kind === 'calendar_alert' && item.event_key) {
    html += '<form class="vf-policy" data-vcard-form="policy"><label>' + NTF('notif.remind_prefix', 'Remind me') + ' ' +
      '<input name="minutes" inputmode="numeric" autocomplete="off" value="' + esc(p.lead_minutes == null ? '' : String(p.lead_minutes)) + '">' +
      ' ' + NTF('notif.remind_suffix', 'minutes before this event') + '</label><div class="vf-card-acts">' +
      '<button type="submit" class="vf-btn">' + NTF('notif.save', 'Save') + '</button>' +
      '<button type="button" class="vf-btn" data-vcard="policy-off">' + NTF('notif.no_alerts', 'No alerts for this event') + '</button></div>' +
      '<small>' + NTF('notif.policy_help', 'Separate several lead times with commas, for example 30, 5. VOOL does not change the alarms saved in your calendar.') + '</small></form>';
  }
  html += '</div>';
  card.innerHTML = html;
  card.hidden = false;
}
function closeCard(){ card.hidden = true; cardItem = null; }
function savePolicy(item, minutes){
  return fetch('/api/calendar/alerts/policy', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({event_key: item.event_key || (item.payload || {}).event_key, lead_minutes: minutes, apply_now: true})})
    .then(function(r){ return r.json(); }).then(function(j){
      if (!j || !j.ok) { toast((j && (j.detail || j.reason)) || 'Could not save alert times.'); return; }
      if (cardItem === item) closeCard();
      toast(minutes.length ? 'Alert times saved.' : 'Alerts disabled for this event.');
      return pollCentre();
    }).catch(function(){ toast('Could not save alert times.'); });
}
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
  fetch('/api/notifications?notification_id=' + encodeURIComponent(id))
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
  else if (what === 'policy-off') { savePolicy(current, []); }
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
    toast(NTF('notif.enter_whole', 'Enter whole minutes, for example 15 or 30, 5.'));
    return;
  }
  var current = cardItem;
  savePolicy(current, values);
});
document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape') { pop.hidden = true; if (!card.hidden) closeCard(); } });

bell.addEventListener('click', function(){ pop.hidden ? openPop() : (pop.hidden = true); });
pop.addEventListener('click', function(ev){
  var tab = ev.target.closest('[data-vf-section]');
  if (tab) { section = tab.getAttribute('data-vf-section'); paintList(); readItems(visibleItems()); return; }
  if (ev.target.closest('[data-vf-discover]')) { pop.hidden = true; if (window.VoolModelRadar) window.VoolModelRadar.open(); return; }
  var join = ev.target.closest('[data-vc-join]');
  if (join) { var joined = centre[Number(join.getAttribute('data-vc-join'))]; if (joined) centreAction(joined, 'open'); return; }
  var act = ev.target.closest('[data-vc-act]');
  if (act) { ev.stopPropagation(); var item = centre[Number(act.getAttribute('data-vc'))]; if (item) centreAction(item, act.getAttribute('data-vc-act'), 10); return; }
  var row = ev.target.closest('[data-vc-open]');
  if (row) { var found = centre[Number(row.getAttribute('data-vc-open'))]; if (found) openCard(found); }
});
document.addEventListener('mousedown', function(ev){
  if (!pop.hidden && !pop.contains(ev.target) && ev.target !== bell && !bell.contains(ev.target)) pop.hidden = true;
});

function mount(){
  var anchor = document.getElementById('panelBtn');
  if (anchor && anchor.parentNode && !document.getElementById('vfBell')) {
    anchor.parentNode.insertBefore(bell, anchor);
  }
  var previous = null;
  try { previous = localStorage.getItem(STATE_KEY); } catch (e) {}
  if (previous) {
    fetch('/api/notifications/migrate', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({read_seq:state.readSeq, dismissed:Object.keys(state.dismissed).filter(function(k){ return /^s[0-9]+$/.test(k); }).map(function(k){ return Number(k.slice(1)); }).slice(0,200)})})
      .then(function(r){ return r.json(); }).then(function(j){ if (j && j.ok) { try { localStorage.removeItem(STATE_KEY); } catch (e) {} } return pollCentre(); }).catch(pollCentre);
  } else pollCentre();
  setInterval(pollCentre, CENTRE_POLL_MS);
  window.addEventListener('focus', pollCentre);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();

window.VoolNotify = Object.freeze({
  runFinished: runFinished,
  openItem: openItem,
  refresh: pollCentre,
  pending: function(){ return centre.length; },
  unread: function(){ return unreadCount(); },
  _state: function(){ return {unread: centreUnread, section: section}; },
  _items: function(){ return centre.slice(); },
});
})();
"""


def render_notification_fragment() -> str:
    """The notification bell + notification centre as an appended fragment."""
    from core.notification_audio import audio_script
    return "<style>" + _NOTIFY_CSS + "</style><script>" + _NOTIFY_JS + "</script>" + audio_script()


__all__ = ["render_notification_fragment"]
