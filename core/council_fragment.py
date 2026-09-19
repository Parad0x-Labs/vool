"""Council convene surface — seat picker, live round progress, verdict card.

Mounts INSIDE the existing Council overlay (#councilBody) whenever it opens: the truth
room the overlay already renders stays untouched above; this fragment appends the
CONVENE section under it. Everything rendered is read back from the server's run state
— no invented progress, no seat theatre. A seat is shown working only because the
polled state says its report has not landed yet, and a failed seat renders as FAILED.

Namespace `window.VoolCouncilConvene`; ids `vcc*`.
"""

from __future__ import annotations

_COUNCIL_CSS = """
#vccRoot{margin-top:14px;border-top:1px solid var(--border,#262b35);padding-top:12px}
#vccRoot h4{margin:0 0 4px;font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted,#9aa1af)}
#vccRoot .vcc-note{font-size:11.5px;color:var(--muted,#9aa1af);margin:0 0 10px;line-height:1.5}
.vcc-seat{display:flex;gap:6px;align-items:center;margin-bottom:6px}
.vcc-seat select,.vcc-seat input{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  border-radius:7px;padding:5px 7px;font:inherit;font-size:12px;color:var(--ink,#e8eaf0)}
.vcc-seat input{flex:1;min-width:0}
.vcc-vote{font-size:9px;letter-spacing:.1em;border:1px solid var(--border,#262b35);border-radius:999px;
  padding:2px 8px;color:var(--muted,#9aa1af);flex:0 0 auto}
.vcc-vote.vcc-judge{color:var(--accent,#5eead4);border-color:var(--accent,#5eead4)}
.vcc-x{background:none;border:none;color:var(--muted,#9aa1af);cursor:pointer;font-size:14px;padding:2px 5px}
.vcc-x:hover{color:var(--bad,#f87171)}
#vccRoot .vcc-add{display:flex;gap:6px;margin:4px 0 10px}
#vccRoot .vcc-add button,#vccConvene,#vccStop{background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  border-radius:8px;padding:6px 10px;font:inherit;font-size:12px;color:var(--ink,#e8eaf0);cursor:pointer}
#vccConvene{border-color:var(--accent,#5eead4);color:var(--accent,#5eead4);font-weight:600}
#vccStop{border-color:var(--bad,#f87171);color:var(--bad,#f87171)}
#vccRoot textarea{width:100%;box-sizing:border-box;background:var(--field,#1d2129);color:var(--ink,#e8eaf0);
  border:1px solid var(--border,#262b35);border-radius:8px;padding:7px 9px;font:inherit;font-size:12.5px;
  min-height:52px;resize:vertical;margin-bottom:8px}
#vccError{color:var(--bad,#f87171);font-size:12px;margin:6px 0;white-space:pre-wrap}
#vccStatus{margin-top:10px}
.vcc-round{border:1px solid var(--border,#262b35);border-radius:9px;padding:7px 10px;margin-bottom:6px}
.vcc-round b{font-size:10px;letter-spacing:.08em;color:var(--muted,#9aa1af)}
.vcc-seat-line{display:flex;gap:8px;align-items:center;font-size:12px;margin-top:4px}
.vcc-seat-line .vcc-role{color:var(--ink,#e8eaf0)}
.vcc-seat-line .vcc-st{font-size:9px;letter-spacing:.1em;border-radius:999px;border:1px solid var(--border,#262b35);
  padding:1px 8px;color:var(--muted,#9aa1af)}
.vcc-st.vcc-landed{color:var(--accent,#5eead4);border-color:var(--accent,#5eead4)}
.vcc-st.vcc-failed{color:var(--bad,#f87171);border-color:var(--bad,#f87171)}
.vcc-st.vcc-agree{color:var(--accent,#5eead4);border-color:var(--accent,#5eead4)}
.vcc-st.vcc-disagree{color:var(--warn,#f0b429);border-color:var(--warn,#f0b429)}
#vccVerdict{border:1px solid var(--accent,#5eead4);border-radius:10px;padding:9px 12px;margin-top:8px;font-size:12.5px}
#vccVerdict.vcc-bad{border-color:var(--bad,#f87171)}
#vccVerdict small{display:block;color:var(--muted,#9aa1af);margin-top:4px}
.vcc-candidate{font-size:12px;color:var(--ink,#e8eaf0);border-left:3px solid var(--accent,#5eead4);
  padding:4px 8px;margin:6px 0;background:rgba(94,234,212,.05)}
#vccRuns{margin-top:10px;font-size:11.5px}
#vccRuns button{background:none;border:none;color:var(--muted,#9aa1af);cursor:pointer;font-size:11.5px;
  text-decoration:underline;padding:1px 2px}
#vccRuns button:hover{color:var(--ink,#e8eaf0)}
#vccNerdToggle{background:none;border:none;color:var(--muted,#9aa1af);cursor:pointer;font-size:10.5px;
  letter-spacing:.06em;padding:8px 0 2px;display:block}
#vccNerdToggle:hover{color:var(--ink,#e8eaf0)}
#vccConvene:disabled{opacity:.55;cursor:default}
"""

_COUNCIL_JS = """
(function(){
'use strict';
if (window.VoolCouncilConvene) return;

var ROLES = [
  ['builder','Builder / root-causer'],
  ['falsifier','Falsifier / counterexample'],
  ['reviewer','Independent reviewer'],
  ['verifier','Evidence verifier'],
  ['adjudicator','Adjudicator']
];
var seats = [];
var pollTimer = null;
var activeRun = null;
var modelIds = null;

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
  });
}

function defaultSeats(){
  return [
    { role_id:'builder', model:'', votes:true },
    { role_id:'falsifier', model:'', votes:true },
    { role_id:'reviewer', model:'', votes:true }
  ];
}

function roleOptions(selected){
  return ROLES.map(function(pair){
    return '<option value="' + pair[0] + '"' + (pair[0] === selected ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
  }).join('');
}

function seatRows(){
  return seats.map(function(seat, index){
    return '<div class="vcc-seat" data-i="' + index + '">' +
      '<select data-k="role">' + roleOptions(seat.role_id) + '</select>' +
      '<input data-k="model" list="vccModels" value="' + esc(seat.model) + '" ' +
        'placeholder="provider\\/model \\u2014 cloud only" spellcheck="false">' +
      '<span class="vcc-vote ' + (seat.votes ? 'vcc-judge' : '') + '">' + (seat.votes ? 'JUDGE' : 'ADVISOR') + '</span>' +
      '<button type="button" class="vcc-x" title="Remove seat">\\u00d7</button>' +
      '</div>';
  }).join('');
}

function renderForm(root){
  root.innerHTML = '<h4>Convene a council</h4>' +
    '<p class="vcc-note">Seats are roles, not personalities \\u2014 the model behind a seat is replaceable, ' +
    'the role is not. Judges vote; advisors report without a vote. One receipt-backed counterexample beats ' +
    'any vote count. The council adjudicates \\u2014 promotion, merge and spend stay with you. Cloud seats only on this machine \\u2014 the server refuses auto\\/local seats.</p>' +
    '<div id="vccSeats">' + seatRows() + '</div>' +
    '<datalist id="vccModels"></datalist>' +
    '<div class="vcc-add">' +
      '<button type="button" id="vccAddJudge">+ Judge</button>' +
      '<button type="button" id="vccAddAdvisor">+ Advisor</button>' +
    '</div>' +
    '<textarea id="vccProblem" placeholder="The problem. Root cause + best fix with minimal blast radius is the default ask."></textarea>' +
    '<textarea id="vccExhibits" placeholder="Optional proof \\u2014 logs, repro output, paths. Empty = blind round 1: every seat gathers its own evidence."></textarea>' +
    '<button type="button" id="vccConvene">Convene</button>' +
    '<div id="vccError"></div>' +
    '<div id="vccStatus"></div>' +
    '<div id="vccRuns"></div>';
  wireForm(root);
  loadModels(root);
  loadRuns(root);
}

function wireForm(root){
  var seatsEl = root.querySelector('#vccSeats');
  if (seatsEl) seatsEl.addEventListener('change', function(ev){
    var row = ev.target.closest ? ev.target.closest('.vcc-seat') : null;
    if (!row) return;
    var index = parseInt(row.getAttribute('data-i'), 10);
    var key = ev.target.getAttribute('data-k');
    if (!seats[index] || !key) return;
    if (key === 'role') seats[index].role_id = ev.target.value;
    if (key === 'model') seats[index].model = ev.target.value.trim();
  });
  if (seatsEl) seatsEl.addEventListener('click', function(ev){
    if (!ev.target.classList || !ev.target.classList.contains('vcc-x')) return;
    var row = ev.target.closest('.vcc-seat');
    if (!row) return;
    seats.splice(parseInt(row.getAttribute('data-i'), 10), 1);
    seatsEl.innerHTML = seatRows();
  });
  var addJudge = root.querySelector('#vccAddJudge');
  if (addJudge) addJudge.addEventListener('click', function(){
    seats.push({ role_id:'reviewer', model:'', votes:true });
    if (seatsEl) seatsEl.innerHTML = seatRows();
  });
  var addAdvisor = root.querySelector('#vccAddAdvisor');
  if (addAdvisor) addAdvisor.addEventListener('click', function(){
    seats.push({ role_id:'verifier', model:'', votes:false });
    if (seatsEl) seatsEl.innerHTML = seatRows();
  });
  var convene = root.querySelector('#vccConvene');
  if (convene) convene.addEventListener('click', function(){ doConvene(root); });
}

function loadModels(root){
  if (modelIds) { fillDatalist(root); return; }
  fetch('/api/cloud/models').then(function(r){ return r.json(); }).then(function(data){
    modelIds = (data.models || []).map(function(m){ return m.id; }).filter(Boolean);
    fillDatalist(root);
  }).catch(function(){ modelIds = []; fillDatalist(root); });
}

function fillDatalist(root){
  var list = root.querySelector('#vccModels');
  if (!list) return;
  list.innerHTML = (modelIds || []).map(function(id){ return '<option value="' + esc(id) + '">'; }).join('');
}

function doConvene(root){
  var errorEl = root.querySelector('#vccError');
  var problem = (root.querySelector('#vccProblem') || {}).value || '';
  var exhibits = (root.querySelector('#vccExhibits') || {}).value || '';
  if (!problem.trim()) { if (errorEl) errorEl.textContent = 'A council needs a problem statement.'; return; }
  var body = {
    problem: problem,
    exhibits: exhibits,
    seats: seats.map(function(seat){
      return { role_id: seat.role_id, model: seat.model, votes: seat.votes };
    })
  };
  var actions = window.VoolPageActions;
  var displayed = actions && actions.displayedChat ? actions.displayedChat() : '';
  if (displayed) body.chat_session = String(displayed);
  if (errorEl) errorEl.textContent = '';
  fetch('/api/council/convene', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  }).then(function(r){ return r.json().then(function(data){ return { status: r.status, data: data }; }); })
    .then(function(result){
      if (!result.data.ok) { if (errorEl) errorEl.textContent = result.data.error || ('convene failed (' + result.status + ')'); return; }
      activeRun = result.data.run_id;
      // The modal is the entry FORM, not the run's home: hand the run to the chat it was
      // convened from, where it survives this overlay closing, a chat switch and a reload.
      if (window.VoolCouncilCard && displayed) {
        try { window.VoolCouncilCard.adopt(String(displayed), activeRun); } catch (err) {}
      }
      var statusEl = root.querySelector('#vccStatus');
      if (statusEl) statusEl.innerHTML = '<div class="vcc-round"><b>RUN ' + esc(activeRun) +
        ' \u00b7 CONVENED</b><div class="vcc-seat-line">dispatching round 1 \u2014 blind: every seat gathers its own evidence\u2026</div></div>';
      setConveneEnabled(root, false);
      poll(root);
    })
    .catch(function(err){ if (errorEl) errorEl.textContent = 'convene failed: ' + err; });
}

function seatLabel(run, seatId){
  var seat = (run.seats || []).find(function(s){ return s.seat_id === seatId; });
  if (!seat) return seatId;
  return seat.role_id + (seat.votes ? '' : ' (advisor)');
}

function verdictChip(report){
  if (report.status === 'failed') return '<span class="vcc-st vcc-failed">FAILED</span>';
  if (report.verdict === 'AGREE') return '<span class="vcc-st vcc-agree">AGREE</span>';
  if (report.verdict === 'DISAGREE') return '<span class="vcc-st vcc-disagree">DISAGREE</span>';
  if (report.counterexample_backed) return '<span class="vcc-st vcc-disagree">COUNTEREXAMPLE</span>';
  return '<span class="vcc-st vcc-landed">LANDED</span>';
}

function renderStatus(root, run, live){
  var statusEl = root.querySelector('#vccStatus');
  if (!statusEl) return;
  var html = '<div class="vcc-round"><b>RUN ' + esc(run.run_id) + ' \\u00b7 ' + esc(String(run.state || '').toUpperCase()) + '</b></div>';
  (run.rounds || []).forEach(function(round){
    html += '<div class="vcc-round"><b>ROUND ' + round.round_no + '</b>';
    var reported = {};
    (round.reports || []).forEach(function(report){
      reported[report.seat_id] = true;
      html += '<div class="vcc-seat-line"><span class="vcc-role">' + esc(seatLabel(run, report.seat_id)) + '</span>' +
        verdictChip(report) +
        (report.receipt_count ? '<span class="vcc-st">' + report.receipt_count + ' receipts</span>' : '') +
        '</div>';
    });
    (run.seats || []).forEach(function(seat){
      if (!reported[seat.seat_id] && live && run.state === 'round_open') {
        html += '<div class="vcc-seat-line"><span class="vcc-role">' + esc(seatLabel(run, seat.seat_id)) + '</span>' +
          '<span class="vcc-st">WORKING</span></div>';
      }
    });
    html += '</div>';
  });
  if (run.candidate) {
    html += '<div class="vcc-candidate">CANDIDATE: ' + esc(run.candidate) +
      (run.candidate_source === 'unstructured' ? ' <small>(unstructured \\u2014 no DIAGNOSIS line)</small>' : '') + '</div>';
  }
  var outcome = run.outcome || {};
  if (outcome.result) {
    var bad = outcome.result !== 'adjudicated';
    html += '<div id="vccVerdict"' + (bad ? ' class="vcc-bad"' : '') + '><b>' + esc(outcome.result.toUpperCase()) + '</b>';
    if (outcome.result === 'adjudicated') {
      html += ' \\u2014 ' + outcome.agree + ' agree / ' + outcome.disagree + ' disagree in ' + outcome.rounds + ' rounds';
      html += '<small>' + esc(outcome.authority_note || '') + '</small>';
    } else if (outcome.detail) {
      html += '<small>' + esc(outcome.detail) + '</small>';
    }
    html += '</div>';
  } else if (live) {
    html += '<button type="button" id="vccStop">Stop council</button>';
  }
  statusEl.innerHTML = html;
  var stopBtn = statusEl.querySelector('#vccStop');
  if (stopBtn) stopBtn.addEventListener('click', function(){
    fetch('/api/council/stop', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ run_id: activeRun }) }).catch(function(){});
  });
}

function poll(root){
  if (pollTimer) clearTimeout(pollTimer);
  if (!activeRun) return;
  fetch('/api/council/status?run=' + encodeURIComponent(activeRun))
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (!data.ok) { pollTimer = setTimeout(function(){ poll(root); }, 2500); return; }
      renderStatus(root, data.run || {}, !!data.live);
      var state = (data.run || {}).state || '';
      var terminal = ['converged','failed','no_convergence','stopped','crashed'].indexOf(state) >= 0;
      if (!terminal) pollTimer = setTimeout(function(){ poll(root); }, 2500);
      else { setConveneEnabled(root, true); loadRuns(root); }
    })
    .catch(function(){ pollTimer = setTimeout(function(){ poll(root); }, 5000); });
}

function loadRuns(root){
  fetch('/api/council/runs').then(function(r){ return r.json(); }).then(function(data){
    var el = root.querySelector('#vccRuns');
    if (!el || !data.ok) return;
    var rows = (data.runs || []).slice(0, 5);
    if (!rows.length) { el.textContent = ''; return; }
    el.innerHTML = '<b style="font-size:9px;letter-spacing:.1em;color:var(--muted,#9aa1af)">PAST RUNS</b> ' +
      rows.map(function(row){
        return '<button type="button" data-run="' + esc(row.run_id) + '">' +
          esc(row.run_id) + ' \\u00b7 ' + esc(row.state || '?') + '</button>';
      }).join(' ');
    el.querySelectorAll('button[data-run]').forEach(function(btn){
      btn.addEventListener('click', function(){ activeRun = btn.getAttribute('data-run'); poll(root); });
    });
  }).catch(function(){});
}

function setConveneEnabled(root, enabled){
  var btn = root.querySelector('#vccConvene');
  if (btn) { btn.disabled = !enabled; btn.textContent = enabled ? 'Convene' : 'Council in session\u2026'; }
}

var NERD_LABEL = ' Technical details \u2014 build, lifecycle & finality';
function ensureMounted(){
  var bodyEl = document.getElementById('councilBody');
  if (!bodyEl) return;
  var rootEl = document.getElementById('vccRoot');
  if (!rootEl) {
    if (!seats.length) seats = defaultSeats();
    rootEl = document.createElement('div');
    rootEl.id = 'vccRoot';
    bodyEl.insertBefore(rootEl, bodyEl.firstChild);
    renderForm(rootEl);
    if (activeRun) poll(rootEl);
  }
  // Fold the pre-existing truth room (SHA grids, lifecycle law, authority note) into a
  // collapsed drawer: the convene surface is the everyday face, the record is one click away.
  var nerd = document.getElementById('vccNerd');
  if (!nerd) {
    var toggle = document.createElement('button');
    toggle.id = 'vccNerdToggle';
    toggle.type = 'button';
    toggle.textContent = '\u25b8' + NERD_LABEL;
    nerd = document.createElement('div');
    nerd.id = 'vccNerd';
    nerd.hidden = true;
    toggle.addEventListener('click', function(){
      nerd.hidden = !nerd.hidden;
      toggle.textContent = (nerd.hidden ? '\u25b8' : '\u25be') + NERD_LABEL;
    });
    bodyEl.appendChild(toggle);
    bodyEl.appendChild(nerd);
  }
  Array.prototype.slice.call(bodyEl.children).forEach(function(child){
    if (child.id === 'vccRoot' || child.id === 'vccNerd' || child.id === 'vccNerdToggle') return;
    nerd.appendChild(child);
  });
}

var overlay = document.getElementById('councilOverlay');
if (overlay && typeof MutationObserver === 'function') {
  new MutationObserver(function(){
    if (!overlay.hidden) ensureMounted();
  }).observe(overlay, { attributes: true, attributeFilter: ['hidden'], childList: true, subtree: true });
  if (!overlay.hidden) ensureMounted();
}

window.VoolCouncilConvene = Object.freeze({
  mount: ensureMounted,
  seats: function(){ return seats.map(function(s){ return Object.assign({}, s); }); },
  activeRun: function(){ return activeRun; }
});
})();
"""


def render_council_fragment() -> str:
    return "<style>" + _COUNCIL_CSS + "</style>\n<script>" + _COUNCIL_JS + "</script>"
