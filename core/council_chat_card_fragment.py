"""The council card that lives in the chat, not in the modal.

A council run convened from a chat belongs to that chat. Until this fragment the whole
run existed inside the convene overlay: closing it hid the run, switching chats lost it,
and a reload ended it as far as the operator could tell — while the bench kept spending
on cloud seats. What survives all three is the chat transcript, so that is where the run
goes.

Three things this fragment is careful about, because each has a failure mode that reads
as working:

* **It renders state; it never invents it.** A seat is shown WORKING only because the
  server says the round is open, the run is live, and that seat's report has not landed.
  Nothing here advances a round, guesses a verdict, or animates progress that no polled
  state supports;
* **A request is not an answer.** Every seat shows what it was asked to run on and,
  separately, what the runtime proved answered. With no evidence the card says "actual
  model unknown" — echoing the request back as the answer is the exact untruth the
  evidence record removed, and it must not come back in a UI;
* **The DOM is not the run.** Polling and ownership live in this module's own registry,
  keyed by run id. Closing the modal, switching chats and re-rendering the log all throw
  DOM away; none of them touch the record, and the card is rebuilt from it on return.

Report bodies are NOT in the polled snapshot — the snapshot carries a `text_ref` and the
ledger carries the text. A seat's reasoning is fetched from
``/api/council/events?run=&seq=`` when its fold is opened, so an open card costs one
request and a closed one costs nothing.

Namespace ``window.VoolCouncilCard``; ids and classes ``vcx*``.
"""

from __future__ import annotations

_CARD_CSS = """
.vcx-msg{max-width:none}
.vcx-card{border:1px solid var(--border,#262b35);border-radius:11px;padding:10px 12px;
  background:var(--field,#1d2129);font-size:12.5px;line-height:1.55}
.vcx-head{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted,#9aa1af);
  margin-bottom:7px;word-break:break-word}
.vcx-candidate{border-left:3px solid var(--accent,#5eead4);padding:3px 9px;margin:6px 0;
  background:rgba(94,234,212,.05);color:var(--ink,#e8eaf0)}
.vcx-seat-row{display:block;font-size:11.5px;color:var(--muted,#9aa1af);margin:2px 0;word-break:break-word}
.vcx-seat-row b{color:var(--ink,#e8eaf0);font-weight:600}
.vcx-round{border:1px solid var(--border,#262b35);border-radius:8px;margin-top:7px;padding:2px 8px}
.vcx-round>summary,.vcx-seat>summary{cursor:pointer;font-size:11px;letter-spacing:.06em;
  padding:5px 0;color:var(--ink,#e8eaf0)}
.vcx-seat{border-top:1px solid var(--border,#262b35);padding:0 0 4px}
.vcx-seat:first-of-type{border-top:none}
.vcx-model,.vcx-attempts{font-size:11px;color:var(--muted,#9aa1af);margin:2px 0}
.vcx-failure{font-size:11px;color:var(--bad,#f87171);margin:2px 0;white-space:pre-wrap}
.vcx-counterexample{font-size:11.5px;color:var(--warn,#f0b429);margin:3px 0;white-space:pre-wrap}
.vcx-report{white-space:pre-wrap;font-size:11.5px;color:var(--ink,#e8eaf0);margin:4px 0 6px;
  max-height:340px;overflow:auto;border-left:2px solid var(--border,#262b35);padding-left:8px}
.vcx-summary{white-space:pre-wrap;color:var(--ink,#e8eaf0);margin:2px 0}
.vcx-note{font-size:11px;color:var(--muted,#9aa1af);margin-top:6px}
.vcx-attention{border:1px solid var(--warn,#f0b429);border-radius:9px;padding:8px 10px;margin:7px 0;
  background:rgba(240,180,41,.06)}
.vcx-attention b{color:var(--warn,#f0b429);font-size:10px;letter-spacing:.09em}
.vcx-dead{font-size:11.5px;color:var(--ink,#e8eaf0);margin:4px 0;white-space:pre-wrap}
.vcx-controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
.vcx-act{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:7px;
  padding:4px 9px;font:inherit;font-size:11px;color:var(--ink,#e8eaf0);cursor:pointer}
.vcx-act:disabled{opacity:.5;cursor:default}
.vcx-act[data-act="resume"]{border-color:var(--accent,#5eead4);color:var(--accent,#5eead4)}
.vcx-act[data-act="disable"]{border-color:var(--bad,#f87171);color:var(--bad,#f87171)}
.vcx-model-input{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  border-radius:7px;padding:4px 7px;font:inherit;font-size:11px;color:var(--ink,#e8eaf0);min-width:150px}
.vcx-action-note{font-size:11px;color:var(--warn,#f0b429);margin-top:6px;white-space:pre-wrap}
.vcx-verdict-summary{font-size:12.5px;color:var(--ink,#e8eaf0);margin:6px 0;line-height:1.55;
  white-space:pre-wrap}
.vcx-score{border:1px solid var(--border,#262b35);border-radius:8px;margin-top:7px;padding:2px 8px}
.vcx-score>summary{cursor:pointer;font-size:11px;letter-spacing:.06em;padding:5px 0;
  color:var(--ink,#e8eaf0)}
.vcx-score-basis{font-size:11px;color:var(--muted,#9aa1af);margin:3px 0 6px}
.vcx-score-seat{font-size:11.5px;color:var(--ink,#e8eaf0);margin:3px 0;white-space:pre-wrap}
.vcx-dispute{font-size:11.5px;color:var(--warn,#f0b429);margin:3px 0;white-space:pre-wrap}
.vcx-stop{background:none;border:1px solid var(--bad,#f87171);color:var(--bad,#f87171);
  border-radius:7px;padding:4px 9px;font:inherit;font-size:11px;cursor:pointer;margin-top:7px}
"""

_CARD_JS = r"""
(function(){
'use strict';
if (window.VoolCouncilCard) return;

// Terminal states, stated once. `core/council/chat_record.py` holds the same list; both
// sides answer "is anything further going to happen" and must agree.
var TERMINAL = ['converged', 'failed', 'no_convergence', 'stopped', 'crashed'];
var POLL_MS = 2500;
//: How often the composer re-reads the server's lock. It is not this page's card that
//: decides whether a turn may run: a council convened in ANOTHER TAB holds the same
//: machine-wide pin, and this page may hold no card for it at all.
var LOCK_POLL_MS = 4000;
var PRUNED_NOTE = 'Round and seat details are unavailable — this run\'s record is no longer on ' +
  'disk. What is above is the summary written when the council ended.';

// run_id -> record. THIS is the run as far as the page is concerned; every DOM node
// below is a view of it that can be thrown away and rebuilt.
var runs = {};
var probedChats = {};

function isTerminal(state){ return TERMINAL.indexOf(String(state || '')) >= 0; }
function actions(){ return window.VoolPageActions || null; }
function displayedChat(){
  var a = actions();
  return a && a.displayedChat ? String(a.displayedChat() || '') : '';
}

function el(tag, className, text){
  var node = document.createElement(tag);
  if (className) node.className = className;
  // textContent, never innerHTML: every string on this path is server-shaped or model-
  // written, and one innerHTML assignment turns a seat's report into live markup.
  if (text != null) node.textContent = String(text);
  return node;
}

function fmtElapsed(ms){
  var s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return s + 's';
  var m = Math.floor(s / 60), r = s % 60;
  return m + 'm' + (r < 10 ? '0' : '') + r + 's';
}

function count(n, one, many){ return n + ' ' + (n === 1 ? one : many); }

/** "4.4k tok", "930 tok" — one compact token rendering, no silent rounding past 1 decimal. */
function fmtTok(n){
  n = Math.max(0, Number(n) || 0);
  if (n >= 1000) {
    var k = (Math.round(n / 100) / 10);
    return (k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)) + 'k';
  }
  return String(n);
}

// ------------------------------------------------------------------- the marker
// The one string a client that predates this fragment still renders as plain text.
var MARKER = /^council \| run=([A-Za-z0-9_-]{1,80}) \| ([a-z_]{1,40}) \| r(\d{1,4})\/(\d{1,4})$/m;

function parseMarker(text){
  var m = MARKER.exec(String(text || ''));
  if (!m) return null;
  return { run_id: m[1], state: m[2], round_no: parseInt(m[3], 10), max_rounds: parseInt(m[4], 10) };
}

// ------------------------------------------------------------------- the record

function record(chatId, runId){
  var id = String(runId || '');
  var rec = runs[id];
  if (!rec) {
    rec = {
      runId: id, chatId: String(chatId || ''), state: '', run: null, live: false,
      missing: false, summaryText: '', markerRounds: 0, markerMax: 0,
      node: null, headEl: null, seatsEl: null, roundsEl: null, noteEl: null,
      attentionEl: null, attentionAttached: false, actionNote: '', modelDraft: {},
      verdictEl: null, scoreEl: null, score: null, scoreAsked: false,
      candidateEl: null, summaryEl: null, stopEl: null,
      timer: null, sig: '', open: {}, bodies: {}
    };
    runs[id] = rec;
  }
  return rec;
}

function activeRuns(){
  return Object.keys(runs).map(function(id){ return runs[id]; })
    .filter(function(rec){ return !isTerminal(rec.state); });
}

// ------------------------------------------------------------------- the lock
// The composer is closed by the SERVER's answer to one question — does a council own
// this machine's model pin — never by what this page's registry happens to know. The
// registry knows the runs THIS page adopted; the pin is a fact about the machine, and a
// run convened in another tab holds it just as hard. The server refuses the turn either
// way; this only makes the refusal visible before the operator types into it.
var lockTimer = null;

function applyServerLock(payload){
  var a = actions();
  if (!a || !a.setCouncilLock) return;
  a.setCouncilLock(payload && payload.locked === true, String((payload || {}).reason || ''));
}

function refreshLock(){
  return fetch('/api/council/lock')
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (!data || data.ok !== true) return null;
      applyServerLock(data);
      return data;
    })
    // A failed read is NOT an unlock: the last known state stands rather than the page
    // concluding from a network error that the machine is free.
    .catch(function(){ return null; });
}

function startLockPolling(){
  if (lockTimer) return;
  var tick = function(){
    refreshLock().then(function(){ lockTimer = setTimeout(tick, LOCK_POLL_MS); });
  };
  lockTimer = setTimeout(tick, 0);
}

// --------------------------------------------------------------------- placement

function stripEmptyGreeting(host){
  if (!host || !host.children) return;
  Array.prototype.slice.call(host.children).forEach(function(child){
    if (child.classList && child.classList.contains('empty')) host.removeChild(child);
  });
}

/** Build (or rebuild) this run's card inside `holder`, an element that becomes its body. */
function mount(rec, holder, bubble){
  var card = el('div', 'vcx-card');
  rec.node = card;
  rec.headEl = el('div', 'vcx-head');
  card.appendChild(rec.headEl);
  rec.candidateEl = el('div', 'vcx-candidate');
  rec.candidateEl.hidden = true;
  card.appendChild(rec.candidateEl);
  rec.summaryEl = el('div', 'vcx-summary');
  rec.summaryEl.hidden = true;
  card.appendChild(rec.summaryEl);
  rec.seatsEl = el('div', 'vcx-seats');
  card.appendChild(rec.seatsEl);
  // Built at mount but NOT attached: a running council has no operator decisions to
  // offer, and an empty hidden panel sitting in the card is a control surface that
  // happens to be invisible. It is attached only while the run is actually paused.
  rec.attentionEl = el('div', 'vcx-attention');
  rec.attentionAttached = false;
  rec.verdictEl = el('div', 'vcx-verdict-summary');
  rec.verdictEl.hidden = true;
  card.appendChild(rec.verdictEl);
  rec.scoreEl = null;
  rec.roundsEl = el('div', 'vcx-rounds');
  card.appendChild(rec.roundsEl);
  rec.noteEl = el('div', 'vcx-note');
  rec.noteEl.hidden = true;
  card.appendChild(rec.noteEl);
  holder.appendChild(card);
  if (bubble) {
    bubble.dataset.councilRun = rec.runId;
    bubble.setAttribute('data-council-run', rec.runId);
  }
  rec.sig = '';   // a fresh body must be filled, never assumed current
  render(rec);
}

/** Put the card on screen for the chat currently displayed. No-op for any other chat. */
function place(rec){
  if (!rec.chatId || rec.chatId !== displayedChat()) return;
  var a = actions();
  var host = a && a.chatLog ? a.chatLog() : null;
  if (!host) return;
  stripEmptyGreeting(host);
  var bubble = el('div', 'msg assistant vcx-msg');
  var body = el('span', 'msg-text');
  bubble.appendChild(body);
  host.appendChild(bubble);
  mount(rec, body, bubble);
}

// ------------------------------------------------------------------- rendering

function latestReportFor(run, seatId){
  var found = null;
  ((run && run.rounds) || []).forEach(function(round){
    ((round && round.reports) || []).forEach(function(report){
      if (String(report.seat_id || '') === String(seatId)) found = report;
    });
  });
  return found;
}

/** "asked for X · answered by Y" or "asked for X · actual model unknown". Never one string. */
function provenance(report, seat){
  var requested = String((report && report.model_requested) || (seat && seat.model) || '').trim();
  var actual = String((report && report.model_actual) || '').trim();
  var evidence = String((report && report.model_evidence) || '').trim();
  var parts = ['asked for ' + (requested || 'no model named')];
  parts.push(actual ? ('answered by ' + actual) : 'actual model unknown');
  if (evidence) parts.push('evidence ' + evidence);
  return parts.join(' · ');
}

function headText(rec){
  var run = rec.run || {};
  var rounds = (run.rounds || []);
  var last = rounds.length ? rounds[rounds.length - 1] : null;
  var reports = (last && last.reports) || [];
  var failed = reports.filter(function(r){ return String(r.status || '') === 'failed'; }).length;
  var seats = run.seats || [];
  var retries = 0;
  rounds.forEach(function(round){
    ((round.reports) || []).forEach(function(r){ retries += Number(r.retries_used) || 0; });
  });
  // The rounds the server actually reported, whenever it reported any -- `round_no` is a
  // redundant echo of the round in flight, and preferring it made a run with an empty
  // rounds array claim it was on round 2. Only a card rebuilt from a marker with no
  // record behind it falls back to what the marker said.
  var roundNo = run.rounds ? run.rounds.length : (Number(run.round_no) || rec.markerRounds || 0);
  var maxRounds = Number(run.max_rounds) || rec.markerMax || 0;
  var state = String(run.state || rec.state || 'unknown');
  var parts = ['Council', state.toUpperCase(), 'r' + roundNo + '/' + maxRounds];
  if (rec.run) {
    parts.push(reports.length + ' reported');
    // A WORKING seat is a claim about right now. It is made only when the server says
    // this round is open AND the run is live; anything else would be invented activity.
    if (rec.live && state === 'round_open') {
      parts.push(Math.max(0, seats.length - reports.length) + ' working');
    }
    parts.push(failed + ' failed');
    parts.push(count(retries, 'retry', 'retries'));
  }
  // MEASURED spend beats inference. The snapshot's report rows carry the usage the seat
  // turns' own streams reported (core/council/dispatch.py sums the provider's numbers);
  // when any measurement exists the card shows that total and never falls back to
  // counting cloud models — a model string is not a meter reading. The bound travels
  // with the number: partial coverage renders as "≥" (at least), never as exact.
  var measuredTok = 0; var measuredAny = false; var measuredPartial = false;
  rounds.forEach(function (round) {
    ((round && round.reports) || []).forEach(function (r) {
      if (!r || r.status !== 'landed' || !r.usage) return;
      var pin = Number(r.usage.prompt_tokens) || 0;
      var pout = Number(r.usage.output_tokens) || 0;
      if (r.usage.complete === false) measuredPartial = true;
      if (pin || pout) { measuredTok += pin + pout; measuredAny = true; }
    });
  });
  // A background run is the DEFAULT once the operator navigates away, so the chat it was
  // convened from has to say what is still happening in their name. "Running" alone is not
  // the whole fact: the server refuses local seats outright, so every seat turn of a live
  // council is a paid cloud call. A run parked on a failed seat is the opposite case and
  // must not borrow the word — it is waiting, and waiting costs nothing.
  if (rec.run && !isTerminal(state)) {
    if (state === 'needs_attention') {
      parts.push('paused — waiting on you · no paid turns in flight');
    } else if (measuredAny) {
      parts.push('still running · measured ' + (measuredPartial ? '≥' : '') + fmtTok(measuredTok) + ' tok');
    } else {
      var cloud = seats.filter(function(s){ return String((s && s.model) || '').indexOf('/') >= 0; }).length;
      parts.push('still running · spending on ' + cloud + ' cloud ' + (cloud === 1 ? 'seat' : 'seats'));
    }
  } else if (measuredAny) {
    parts.push('measured ' + (measuredPartial ? '≥' : '') + fmtTok(measuredTok) + ' tok');
  }
  var started = Number(run.started_at) || 0;
  if (started) {
    var endMs = (rec.live && !isTerminal(state))
      ? Date.now()
      : (Number(run.updated_at) || 0) * 1000 || Date.now();
    parts.push(fmtElapsed(endMs - started * 1000));
  }
  return parts.join(' · ');
}

function seatSummary(report, seat){
  var bits = [String(seat.role_id || seat.seat_id || '')];
  if (seat.votes === false) bits.push('advisor');
  if (!report) { bits.push('no report'); return bits.join(' · '); }
  if (String(report.status || '') === 'failed') bits.push('FAILED');
  else if (report.verdict) bits.push(String(report.verdict));
  else bits.push('landed');
  if (report.counterexample) bits.push('COUNTEREXAMPLE');
  var receipts = Number(report.receipt_count) || 0;
  if (receipts) bits.push(count(receipts, 'receipt', 'receipts'));
  return bits.join(' · ');
}

function seatKey(report){ return 'r' + (report.round_no || 0) + ':' + (report.seat_id || ''); }

function fetchReport(rec, report, target){
  var ref = report.text_ref;
  var seq = ref && ref.seq;
  if (!seq) {
    // Legacy rows carry their text inline and have no reference. Reading the inline
    // text is correct for them; fabricating a reference would not be.
    target.textContent = String(report.text || '') || 'No report text was recorded for this seat.';
    return;
  }
  var cached = rec.bodies[seq];
  if (cached != null) { target.textContent = cached; return; }
  target.textContent = 'loading the report from the run ledger…';
  fetch('/api/council/events?run=' + encodeURIComponent(rec.runId) + '&seq=' + encodeURIComponent(seq))
    .then(function(r){ return r.json().then(function(d){ return { status: r.status, data: d }; }); })
    .then(function(res){
      if (!res.data || !res.data.ok || !(res.data.events || []).length) {
        target.textContent = 'This report is no longer in the run ledger.';
        return;
      }
      var row = res.data.events[0] || {};
      var text = String(row.text == null ? '' : row.text);
      if (res.data.text_verified === false) {
        text += '\n\n[the stored hash does not match these bytes]';
      }
      rec.bodies[seq] = text;
      target.textContent = text;
    })
    .catch(function(err){ target.textContent = 'The report could not be fetched: ' + err; });
}

function buildSeat(rec, round, report, seat){
  var details = el('details', 'vcx-seat');
  var key = seatKey(report);
  details.setAttribute('data-seat', String(report.seat_id || ''));
  var summary = el('summary', null, seatSummary(report, seat));
  details.appendChild(summary);
  details.appendChild(el('div', 'vcx-model', provenance(report, seat)));
  var attempts = [count(Number(report.attempts) || 1, 'attempt', 'attempts')];
  var retries = Number(report.retries_used) || 0;
  if (retries) attempts.push(count(retries, 'retry', 'retries'));
  if (report.outcome) attempts.push('outcome ' + String(report.outcome));
  details.appendChild(el('div', 'vcx-attempts', attempts.join(' · ')));
  if (report.failure) details.appendChild(el('div', 'vcx-failure', String(report.failure)));
  if (report.counterexample) {
    details.appendChild(el('div', 'vcx-counterexample',
      'COUNTEREXAMPLE: ' + String(report.counterexample) +
      (report.counterexample_backed ? ' (receipt-backed)' : ' (unverified — no receipts)')));
  }
  var body = el('div', 'vcx-report');
  details.appendChild(body);
  var load = function(){
    rec.open[key] = true;
    if (String(report.status || '') === 'failed' && !(report.text_ref && report.text_ref.seq)) {
      body.textContent = 'This seat produced no report — its failure is above.';
      return;
    }
    fetchReport(rec, report, body);
  };
  summary.addEventListener('click', load);
  if (rec.open[key]) { details.open = true; load(); }
  return details;
}

function buildRounds(rec){
  var run = rec.run || {};
  var seats = run.seats || [];
  rec.roundsEl.innerHTML = '';
  (run.rounds || []).forEach(function(round){
    var details = el('details', 'vcx-round');
    details.setAttribute('data-round', String(round.round_no || 0));
    var reports = round.reports || [];
    var landed = reports.filter(function(r){ return String(r.status || '') !== 'failed'; }).length;
    details.appendChild(el('summary', null,
      'ROUND ' + (round.round_no || 0) + ' · ' + landed + '/' + reports.length + ' landed'));
    reports.forEach(function(report){
      var seat = seats.filter(function(s){ return s.seat_id === report.seat_id; })[0] || {};
      details.appendChild(buildSeat(rec, round, report, seat));
    });
    rec.roundsEl.appendChild(details);
  });
}

function buildSeatRows(rec){
  var run = rec.run || {};
  rec.seatsEl.innerHTML = '';
  (run.seats || []).forEach(function(seat){
    var row = el('div', 'vcx-seat-row');
    var name = el('b', null, String(seat.role_id || seat.seat_id || '') + (seat.votes === false ? ' (advisor)' : ''));
    row.appendChild(name);
    row.appendChild(document.createTextNode(' · ' + provenance(latestReportFor(run, seat.seat_id), seat)));
    rec.seatsEl.appendChild(row);
  });
}

// -------------------------------------------------------------- needs attention
// A paused run is the ONE state where this card is more than a window: the operator's
// four decisions live here. Every one of them is a POST the server validates, types and
// ledgers — nothing below changes what the card claims the run is doing, and a refusal is
// shown as the refusal it is rather than being drawn as if it had worked.
var ACTION_LABELS = {
  retry: 'Retry seat',
  replace: 'Replace model',
  disable: 'Disable seat',
  resume: 'Resume council',
};

function postAction(rec, url, body, button){
  if (button) button.disabled = true;
  rec.actionNote = '';
  return fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(function(r){ return r.json().then(function(d){ return { status: r.status, data: d }; }); })
    .then(function(res){
      if (!res.data || res.data.ok !== true) {
        // The server said no. Say what it said: a refused disable that silently redrew the
        // card as though the seat were out would be the card inventing a run state.
        rec.actionNote = String((res.data && (res.data.detail || res.data.error))
          || ('the server refused this action (' + res.status + ')'));
      }
      return refresh(rec.runId);
    })
    .catch(function(err){
      rec.actionNote = 'the action could not be sent: ' + err;
      render(rec);
    })
    .then(function(){ if (button) button.disabled = false; });
}

function actionButton(rec, act, onClick){
  var button = el('button', 'vcx-act', ACTION_LABELS[act] || act);
  button.type = 'button';
  button.dataset.act = act;
  button.setAttribute('data-act', act);
  button.addEventListener('click', function(){ onClick(button); });
  return button;
}

function buildAttention(rec){
  var run = rec.run || {};
  var outcome = run.outcome || {};
  var blocking = outcome.blocking_seats || [];
  rec.attentionEl.innerHTML = '';
  if (String(run.state || '') !== 'needs_attention' || !blocking.length) {
    if (rec.attentionAttached) {
      rec.node.removeChild(rec.attentionEl);
      rec.attentionAttached = false;
    }
    return;
  }
  if (!rec.attentionAttached) {
    rec.node.appendChild(rec.attentionEl);
    rec.attentionAttached = true;
  }
  rec.attentionEl.appendChild(el('b', null, 'NEEDS ATTENTION'));
  if (outcome.detail) rec.attentionEl.appendChild(el('div', 'vcx-note', String(outcome.detail)));
  blocking.forEach(function(dead){
    var seatId = String(dead.seat_id || '');
    var facts = [
      String(dead.role_id || seatId),
      'model ' + String(dead.model || 'none named'),
      count(Number(dead.attempts) || 0, 'attempt', 'attempts'),
    ];
    var retries = Number(dead.retries_used) || 0;
    if (retries) facts.push(count(retries, 'retry', 'retries'));
    if (dead.outcome) facts.push(String(dead.outcome));
    if (dead.round_no) facts.push('round ' + dead.round_no);
    rec.attentionEl.appendChild(el('div', 'vcx-dead', facts.join(' · ')));
    if (dead.failure) rec.attentionEl.appendChild(el('div', 'vcx-dead', String(dead.failure)));

    var row = el('div', 'vcx-controls');
    row.appendChild(actionButton(rec, 'retry', function(button){
      postAction(rec, '/api/council/seat',
        { run_id: rec.runId, seat_id: seatId, action: 'retry' }, button);
    }));
    var field = el('input', 'vcx-model-input');
    field.setAttribute('placeholder', 'provider/model');
    field.value = rec.modelDraft[seatId] || '';
    field.addEventListener('input', function(){ rec.modelDraft[seatId] = field.value; });
    row.appendChild(field);
    row.appendChild(actionButton(rec, 'replace', function(button){
      var model = String(field.value || '').trim();
      if (!model) {
        // Not sent. An empty replacement is not a decision, and posting one would spend a
        // round trip to be told what this already knows.
        rec.actionNote = 'Name the model to put behind this seat, then press Replace model.';
        render(rec);
        return;
      }
      rec.modelDraft[seatId] = model;
      postAction(rec, '/api/council/seat',
        { run_id: rec.runId, seat_id: seatId, action: 'replace', model: model }, button);
    }));
    row.appendChild(actionButton(rec, 'disable', function(button){
      postAction(rec, '/api/council/seat',
        { run_id: rec.runId, seat_id: seatId, action: 'disable' }, button);
    }));
    rec.attentionEl.appendChild(row);
  });
  var resumeRow = el('div', 'vcx-controls');
  resumeRow.appendChild(actionButton(rec, 'resume', function(button){
    postAction(rec, '/api/council/resume', { run_id: rec.runId }, button);
  }));
  rec.attentionEl.appendChild(resumeRow);
  if (rec.actionNote) {
    rec.attentionEl.appendChild(el('div', 'vcx-action-note', rec.actionNote));
  }
}

// ------------------------------------------------------------- the scorecard
// What was decided, and whose contributions carried it — read from the server, which
// derives it from the run's own append-only ledger. This fragment renders counts; it
// computes none of them, and it never says a model is "best": the only claim it makes is
// how many of a seat's proposals were adopted, next to the number itself.
function fetchScorecard(rec){
  if (rec.scoreAsked) return Promise.resolve(rec.score);
  rec.scoreAsked = true;
  return fetch('/api/council/scorecard?run=' + encodeURIComponent(rec.runId))
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (data && data.ok === true && data.scorecard) {
        rec.score = data.scorecard;
        render(rec);
      }
      return rec.score;
    })
    .catch(function(){ return null; });
}

function scoreSeatLine(seat){
  var parts = [
    String(seat.role_id || seat.seat_id || ''),
    String(seat.model || 'no model named'),
    seat.claims_proposed + ' proposed',
    seat.claims_adopted + ' adopted',
    seat.claims_supported + ' supported',
    seat.claims_refuted + ' refuted',
    seat.claims_unresolved + ' unresolved',
    seat.valid_attempts + ' valid attempts',
  ];
  if (seat.retries) parts.push(count(seat.retries, 'retry', 'retries'));
  if (seat.refutations_landed) parts.push(seat.refutations_landed + ' refutations landed');
  if (seat.failures) {
    parts.push(seat.failures + ' failed' + (seat.timeouts ? ' (' + seat.timeouts + ' timed out)' : ''));
  }
  if (seat.active === false) parts.push('disabled');
  var line = parts.join(' · ');
  // Reliability rides ALONGSIDE the contribution counts, never folded into them: a seat
  // that never returned proposed nothing, which is not the same as having been wrong.
  if (seat.reliability_note) line += '\n' + String(seat.reliability_note);
  return line;
}

function verdictSummaryText(rec){
  var score = rec.score;
  if (!score || !score.run) return '';
  var run = score.run;
  var lines = [];
  if (run.provisional && run.provisional_note) lines.push(String(run.provisional_note));
  (run.adopted_conclusions || []).forEach(function(text){
    lines.push('Adopted: ' + String(text));
  });
  if (run.agreement_first_reached_round) {
    var strength = run.agreement_strength || {};
    lines.push(
      'Agreement first reached in round ' + run.agreement_first_reached_round + ' — ' +
      (strength.agree || 0) + ' agree / ' + (strength.disagree || 0) + ' disagree of ' +
      (strength.voting_seats || 0) + ' voting seats.');
  } else if (!run.provisional) {
    lines.push('No round reached agreement.');
  }
  var most = run.most_adopted || {};
  if (most.insufficient_evidence) {
    // Nothing was adopted, so there is nobody to credit. Naming one anyway is exactly the
    // invented popularity score this surface exists instead of.
    lines.push(String(most.label || 'insufficient evidence') + '.');
  } else {
    var named = (most.seat_ids || []).map(function(seatId){
      var seat = (score.seats || []).filter(function(s){ return s.seat_id === seatId; })[0];
      // Credit follows the model that PRODUCED the adopted claim, never the seat's
      // CURRENT model: an operator may replace a seat mid-run, and the replacement
      // must not inherit its predecessor's adopted work on the visible card.
      var claim = (most.adopted_claims || []).filter(function(c){ return c.seat_id === seatId; })[0];
      var label = seat ? seat.role_id : seatId;
      if (claim && claim.produced_by) {
        label += ' (' + claim.produced_by + (claim.produced_by_is_proven ? '' : ', requested') + ')';
      } else if (seat) {
        label += ' (' + seat.model + ')';
      }
      return label;
    });
    lines.push(String(most.label || 'most adopted contributions') + ': ' + named.join(', ') +
               ' — ' + most.claims_adopted + ' adopted.');
  }
  var refuted = run.substantially_refuted || {};
  if ((refuted.seat_ids || []).length) {
    lines.push(String(refuted.label) + ': ' + refuted.seat_ids.join(', ') +
               ' — ' + refuted.claims_refuted + ' refuted.');
  }
  var disputes = run.remaining_disagreements || [];
  if (disputes.length) lines.push(count(disputes.length, 'disagreement', 'disagreements') + ' unresolved.');
  return lines.join('\n');
}

function buildScore(rec){
  var score = rec.score;
  if (rec.scoreEl && rec.scoreEl.__attached) {
    rec.node.removeChild(rec.scoreEl);
    rec.scoreEl = null;
  }
  rec.verdictEl.hidden = true;
  if (!score || !score.run) return;
  var text = verdictSummaryText(rec);
  if (text) { rec.verdictEl.hidden = false; rec.verdictEl.textContent = text; }

  var fold = el('details', 'vcx-score');
  fold.appendChild(el('summary', null, 'Contribution evidence — per seat, per dispute'));
  var most = score.run.most_adopted || {};
  if (most.basis) fold.appendChild(el('div', 'vcx-score-basis', String(most.basis)));
  (score.seats || []).forEach(function(seat){
    fold.appendChild(el('div', 'vcx-score-seat', scoreSeatLine(seat)));
  });
  (score.run.remaining_disagreements || []).forEach(function(dispute){
    var backing = dispute.receipt_backed ? 'receipt-backed' : 'unverified — no receipts';
    fold.appendChild(el('div', 'vcx-dispute', [
      String(dispute.role_id || dispute.seat_id || ''),
      String(dispute.model || ''),
      'round ' + dispute.round_no,
      String(dispute.verdict || ''),
      String(dispute.counterexample || '(no counterexample text)'),
      '(' + backing + ')',
    ].join(' · ')));
  });
  fold.__attached = true;
  rec.node.appendChild(fold);
  rec.scoreEl = fold;
}

function render(rec){
  if (!rec.node) return;
  rec.headEl.textContent = headText(rec);
  var run = rec.run;
  var candidate = String((run && (run.candidate || (run.outcome || {}).candidate)) || '');
  rec.candidateEl.hidden = !candidate;
  if (candidate) rec.candidateEl.textContent = 'CANDIDATE: ' + candidate;
  // The persisted summary is the fallback surface: it is what remains readable when the
  // run's own record has been pruned. With a live record the card itself says more.
  var showSummary = !!rec.summaryText && !run;
  rec.summaryEl.hidden = !showSummary;
  if (showSummary) rec.summaryEl.textContent = rec.summaryText;
  var note = '';
  if (rec.missing) note = PRUNED_NOTE;
  else if (run && (run.outcome || {}).authority_note) note = String(run.outcome.authority_note);
  else if (run && (run.outcome || {}).detail) note = String(run.outcome.detail);
  else if (run && run.error) note = String(run.error);
  rec.noteEl.hidden = !note;
  if (note) rec.noteEl.textContent = note;
  if (!run) {
    rec.seatsEl.innerHTML = ''; rec.roundsEl.innerHTML = ''; rec.sig = '';
    if (rec.attentionAttached) {
      rec.node.removeChild(rec.attentionEl);
      rec.attentionAttached = false;
    }
    return;
  }
  buildAttention(rec);
  // The card explains itself once the run has something to explain: a terminal ending, or
  // a pause whose evidence is provisional and says so.
  if (isTerminal(String(run.state || '')) || String(run.state || '') === 'needs_attention') {
    fetchScorecard(rec);
  }
  buildScore(rec);
  // Rounds are rebuilt only when the shape actually changed, so a poll never collapses a
  // fold the operator opened while a seat was still writing.
  var sig = String(run.state || '') + '|' + (run.rounds || []).map(function(round){
    return (round.reports || []).map(function(r){ return r.seat_id + ':' + r.status + ':' + (r.verdict || ''); }).join(',');
  }).join(';');
  if (sig !== rec.sig) {
    rec.sig = sig;
    buildSeatRows(rec);
    buildRounds(rec);
  }
  ensureStop(rec);
}

function ensureStop(rec){
  // NOT gated on `rec.live`. `live` means "a thread is driving it right now", and a run
  // paused at NEEDS_ATTENTION has left the live registry while still being stoppable —
  // `/api/council/stop` handles the paused registry explicitly. Gating the button on `live`
  // removed the only way out of exactly the run that is sitting there waiting for the
  // operator. What disqualifies a run is being FINISHED, or having no record to act on.
  var wanted = !!rec.run && !rec.missing && !isTerminal(String((rec.run || {}).state || ''));
  if (wanted && !rec.stopEl) {
    rec.stopEl = el('button', 'vcx-stop', 'Stop council');
    rec.stopEl.type = 'button';
    rec.stopEl.addEventListener('click', function(){
      rec.stopEl.disabled = true;
      rec.stopEl.textContent = 'stopping…';
      fetch('/api/council/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: rec.runId }) }).catch(function(){});
    });
    rec.node.appendChild(rec.stopEl);
  } else if (!wanted && rec.stopEl) {
    rec.node.removeChild(rec.stopEl);
    rec.stopEl = null;
  }
}

// --------------------------------------------------------------------- polling

function schedule(rec, delay){
  if (rec.timer) clearTimeout(rec.timer);
  rec.timer = setTimeout(function(){ refresh(rec.runId); }, delay == null ? POLL_MS : delay);
}

function finish(rec){
  if (rec.timer) { clearTimeout(rec.timer); rec.timer = null; }
  // The run ended here, so the pin is being handed back there. Read it rather than
  // assuming it: another council may still be holding the machine.
  refreshLock();
}

function refresh(runId){
  var rec = runs[String(runId || '')];
  if (!rec) return Promise.resolve(null);
  return fetch('/api/council/status?run=' + encodeURIComponent(rec.runId))
    .then(function(r){ return r.json().then(function(d){ return { status: r.status, data: d }; }); })
    .then(function(res){
      if (res.status === 404) {
        // The run's record is gone. Everything already known stays on screen; the card
        // says the details are unavailable rather than blanking into an empty box.
        rec.missing = true;
        rec.run = null;
        render(rec);
        finish(rec);
        return rec;
      }
      if (!res.data || !res.data.ok) { schedule(rec); return rec; }
      rec.missing = false;
      rec.run = res.data.run || {};
      rec.live = res.data.live === true;
      rec.state = String(rec.run.state || '');
      render(rec);
      if (isTerminal(rec.state)) finish(rec); else schedule(rec);
      return rec;
    })
    .catch(function(){ schedule(rec, POLL_MS * 2); return rec; });
}

// ----------------------------------------------------------------- the surface

function adopt(chatId, runId, opts){
  var id = String(runId || '');
  if (!id) return null;
  var fresh = !runs[id];
  var rec = record(chatId, runId);
  if (chatId) rec.chatId = String(chatId);
  opts = opts || {};
  if (opts.summary) rec.summaryText = String(opts.summary);
  if (opts.state && !rec.run) rec.state = String(opts.state);
  if (opts.rounds) rec.markerRounds = Number(opts.rounds) || 0;
  if (opts.maxRounds) rec.markerMax = Number(opts.maxRounds) || 0;
  if (opts.holder) mount(rec, opts.holder, opts.bubble);
  else if (!rec.node || fresh) place(rec);
  refreshLock();
  if (fresh || !rec.timer) schedule(rec, 0);
  return rec;
}

/** Adopt any LIVE run the server says belongs to this chat. Once per chat per page. */
function ensureLiveRuns(chatId){
  var key = String(chatId || '');
  if (!key || probedChats[key]) return;
  probedChats[key] = true;
  fetch('/api/council/runs').then(function(r){ return r.json(); }).then(function(data){
    if (!data || !data.ok) return;
    (data.runs || []).forEach(function(row){
      if (!row || row.live !== true) return;
      if (String(row.chat_session || '') !== key) return;
      if (runs[String(row.run_id || '')]) return;
      adopt(key, row.run_id, { state: String(row.state || '') });
    });
  }).catch(function(){});
}

/**
 * Rebuild every council surface this chat owns, after the log has been re-rendered.
 *
 * `rows` are the assistant bubbles the page just painted, in order: any carrying a
 * council marker becomes that run's card in place, so a reloaded transcript reads as one
 * message rather than a summary and a card side by side. Runs still live with nothing in
 * the transcript yet are appended at the end.
 */
function repaint(chatId, host, rows){
  var key = String(chatId || '');
  var mounted = {};
  (rows || []).forEach(function(row){
    if (!row || !row.textEl) return;
    var marker = parseMarker(row.text);
    if (!marker) return;
    row.textEl.innerHTML = '';
    var rec = adopt(key, marker.run_id, {
      summary: String(row.text || ''), state: marker.state,
      rounds: marker.round_no, maxRounds: marker.max_rounds,
      holder: row.textEl, bubble: row.el,
    });
    if (rec) mounted[rec.runId] = true;
  });
  Object.keys(runs).forEach(function(id){
    var rec = runs[id];
    if (rec.chatId !== key || mounted[id]) return;
    place(rec);
  });
  ensureLiveRuns(key);
}

startLockPolling();

window.VoolCouncilCard = Object.freeze({
  adopt: adopt,
  repaint: repaint,
  refresh: refresh,
  refreshLock: refreshLock,
  parseMarker: parseMarker,
  tracked: function(){
    return activeRuns().map(function(rec){
      return { run_id: rec.runId, chat_session: rec.chatId, state: rec.state, live: rec.live };
    });
  },
});
})();
"""


def render_council_card_fragment() -> str:
    return "<style>" + _CARD_CSS + "</style>\n<script>" + _CARD_JS + "</script>"
