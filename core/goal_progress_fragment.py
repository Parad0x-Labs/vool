"""The compact expandable goal progress card (a page fragment under the fragment law).

One self-contained <style>+<script> IIFE exposing ONE ``window.VoolGoalCard`` namespace. It
touches the page only through ``window.VoolPageActions`` and renders ONLY the canonical goal
state served by ``/api/goal/state`` and the durable ``goal.updated`` events from the runtime
event ledger — never a locally invented counter, timer-driven pretend progress, or a copied
status object. The card is presentation only: closing it never stops the goal, and expanding
it is a presentation preference, never lifecycle authority.

Sections (expanded): current step, pending/queued steps with dependencies, a separately
collapsible completed group, failed/waiting steps with their reason and the real available
action. No Terminals/Agents sections: the goal workflow registers no terminals or agent
workers, so the card shows none — sections appear only when real registered work exists.
"""

from __future__ import annotations


def render_goal_progress_fragment() -> str:
    return """
<style>
  .vool-goal-card{position:fixed;right:16px;bottom:120px;width:340px;max-width:calc(100vw - 24px);
    max-height:min(62vh,calc(100vh - 160px));overflow-y:auto;
    background:var(--vgc-bg,#1c1c1e);color:var(--vgc-fg,#f2f2f7);border:1px solid var(--vgc-border,#3a3a3c);
    border-radius:14px;box-shadow:0 8px 28px rgba(0,0,0,.35);z-index:60;font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    overflow:hidden}
  @media(prefers-color-scheme:light){.vool-goal-card{--vgc-bg:#ffffff;--vgc-fg:#1c1c1e;--vgc-border:#d8d8dc;--vgc-dim:#6e6e73;--vgc-accent:#0a66d0;--vgc-ok:#2e7d32;--vgc-warn:#b26a00;--vgc-bad:#c0392b}}
  @media(prefers-color-scheme:dark){.vool-goal-card{--vgc-dim:#98989d;--vgc-accent:#6db2ff;--vgc-ok:#7bd389;--vgc-warn:#f0b355;--vgc-bad:#ff7a6b}}
  .vgc-head{display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer;user-select:none;border:0;background:transparent;color:inherit;width:100%;text-align:left}
  .vgc-head:focus-visible{outline:2px solid var(--vgc-accent);outline-offset:-2px}
  .vgc-title{font-weight:600;font-size:13px}
  .vgc-elapsed{color:var(--vgc-dim);font-variant-numeric:tabular-nums;font-size:12px}
  .vgc-state{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;background:var(--vgc-border);white-space:nowrap}
  .vgc-state.is-running{background:rgba(48,209,88,.18);color:var(--vgc-ok)}
  .vgc-state.is-completed{background:rgba(48,209,88,.18);color:var(--vgc-ok)}
  .vgc-state.is-failed,.vgc-state.is-cancelled{background:rgba(255,69,58,.16);color:var(--vgc-bad)}
  .vgc-state.is-paused,.vgc-state.is-interrupted,.vgc-state.is-waiting_approval{background:rgba(255,159,10,.16);color:var(--vgc-warn)}
  .vgc-state.is-blocked{background:rgba(191,90,242,.16);color:#a06bd6}
  .vgc-caret{margin-left:auto;color:var(--vgc-dim);transition:transform .15s ease}
  @media(prefers-reduced-motion:reduce){.vgc-caret{transition:none}}
  .vgc-count{color:var(--vgc-dim);font-variant-numeric:tabular-nums;font-size:12px}
  .vgc-body{display:none;padding:2px 12px 10px}
  .vool-goal-card.vgc-open .vgc-body{display:block}
  .vool-goal-card.vgc-open .vgc-caret{transform:rotate(180deg)}
  .vgc-objective{font-size:13px;font-weight:600;margin:2px 0 8px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}
  .vgc-objective.full{-webkit-line-clamp:unset}
  .vgc-current{display:flex;gap:8px;align-items:flex-start;padding:7px 9px;border-radius:9px;background:var(--vgc-border);margin-bottom:8px;font-size:12.5px}
  .vgc-spin{width:12px;height:12px;border:2px solid var(--vgc-dim);border-top-color:var(--vgc-accent);border-radius:50%;margin-top:2px;animation:vgc-rot 1s linear infinite;flex:none}
  @media(prefers-reduced-motion:reduce){.vgc-spin{animation:none}}
  @keyframes vgc-rot{to{transform:rotate(360deg)}}
  .vgc-section{margin-top:7px}
  .vgc-section-h{display:flex;align-items:center;gap:6px;color:var(--vgc-dim);font-size:11px;text-transform:uppercase;letter-spacing:.04em;background:none;border:0;padding:3px 0;color:inherit;cursor:pointer;text-align:left;width:100%}
  .vgc-section-h .vgc-dim{color:var(--vgc-dim)}
  .vgc-step{display:flex;gap:7px;align-items:flex-start;padding:4px 2px;font-size:12.5px}
  .vgc-dot{flex:none;width:14px;height:14px;border-radius:50%;border:1.5px solid var(--vgc-dim);margin-top:1px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;color:var(--vgc-bg)}
  .vgc-step.is-completed .vgc-dot{background:var(--vgc-ok);border-color:var(--vgc-ok)}
  .vgc-step.is-failed .vgc-dot{background:var(--vgc-bad);border-color:var(--vgc-bad)}
  .vgc-step.is-blocked .vgc-dot{background:var(--vgc-warn);border-color:var(--vgc-warn)}
  .vgc-step.is-waiting_approval .vgc-dot{background:var(--vgc-warn);border-color:var(--vgc-warn)}
  .vgc-reason{color:var(--vgc-dim);font-size:11.5px;margin-top:1px;overflow-wrap:anywhere}
  .vgc-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
  .vgc-btn{border:1px solid var(--vgc-border);background:transparent;color:inherit;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer}
  .vgc-btn:hover{border-color:var(--vgc-accent);color:var(--vgc-accent)}
  .vgc-btn:focus-visible{outline:2px solid var(--vgc-accent);outline-offset:1px}
  .vgc-btn.is-primary{background:var(--vgc-accent);border-color:var(--vgc-accent);color:#fff}
  .vgc-link{background:none;border:0;padding:0;color:var(--vgc-accent);cursor:pointer;font-size:12px;text-decoration:underline}
  .vgc-link:focus-visible{outline:2px solid var(--vgc-accent);outline-offset:1px}
  .vgc-foot{display:flex;justify-content:space-between;align-items:center;margin-top:9px;color:var(--vgc-dim);font-size:11.5px}
  .vgc-elapsed-label{font-variant-numeric:tabular-nums}
  .vgc-hidden{display:none!important}
</style>
<div id="voolGoalCard" class="vool-goal-card vgc-hidden" role="group" aria-label="Goal progress">
  <button type="button" class="vgc-head" id="vgcHead" aria-expanded="false" aria-controls="vgcBody">
    <span class="vgc-title">Goal</span>
    <span class="vgc-elapsed" id="vgcElapsed" aria-label="Elapsed time"></span>
    <span class="vgc-count" id="vgcCount"></span>
    <span class="vgc-state" id="vgcState"></span>
    <span class="vgc-caret" aria-hidden="true">&#9660;</span>
  </button>
  <div class="vgc-body" id="vgcBody">
    <div class="vgc-objective" id="vgcObjective" title=""></div>
    <div class="vgc-reason vgc-hidden" id="vgcAcceptance"></div>
    <div class="vgc-current vgc-hidden" id="vgcCurrent"></div>
    <div id="vgcPending"></div>
    <div class="vgc-section vgc-hidden" id="vgcCompletedSection">
      <button type="button" class="vgc-section-h" id="vgcCompletedToggle" aria-expanded="false" aria-controls="vgcCompletedList">
        <span class="vgc-caret" style="display:inline-block;transform:rotate(-90deg)" aria-hidden="true">&#9660;</span>
        <span id="vgcCompletedLabel">Completed</span> <span class="vgc-dim" id="vgcCompletedCount"></span>
      </button>
      <div id="vgcCompletedList"></div>
    </div>
    <div class="vgc-reason vgc-hidden" id="vgcReason"></div>
    <div class="vgc-actions" id="vgcActions"></div>
    <div class="vgc-foot">
      <span class="vgc-elapsed-label"><span id="vgcFootElapsed"></span></span>
      <button type="button" class="vgc-link" id="vgcActivity">View Activity</button>
    </div>
  </div>
</div>
<script>
(function(){
  "use strict";
  if (window.VoolGoalCard) return;
  var el = null, view = null, chatId = "", pollTimer = null, elapsedTimer = null, openPref = null;
  function t(key, fallback){ try { if (typeof VOOLT === 'function') { var v = VOOLT(key, null); if (v && v !== key) return v; } } catch(e){} return fallback; }
  function fmtElapsed(sec){
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
    var mm = (m<10?'0':'')+m, ss = (s<10?'0':'')+s;
    return h > 0 ? h+':'+mm+':'+ss : m+':'+ss;
  }
  function $(id){ return document.getElementById(id); }
  function stateLabel(state){
    var labels = {queued:t('goal.state_queued','Queued'), planning:t('goal.state_planning','Planning'),
      running:t('goal.state_running','Running'), waiting_approval:t('goal.state_waiting','Waiting for approval'),
      paused:t('goal.state_paused','Paused'), interrupted:t('goal.state_interrupted','Interrupted'),
      blocked:t('goal.state_blocked','Blocked'),
      completed:t('goal.state_completed','Completed'), failed:t('goal.state_failed','Failed'), cancelled:t('goal.state_cancelled','Cancelled')};
    return labels[state] || String(state||'');
  }
  function sameChat(){ try { return String(window.VoolPageActions && window.VoolPageActions.displayedChat() || ''); } catch(e){ return ''; } }
  function render(){
    if (!el || !view) return;
    el.classList.remove('vgc-hidden');
    var running = ['queued','planning','running','waiting_approval'].indexOf(view.state) >= 0;
    $('vgcState').textContent = stateLabel(view.state);
    $('vgcState').className = 'vgc-state is-' + view.state;
    var count = $('vgcCount');
    if (view.has_plan && view.total > 0){ count.textContent = view.completed + '/' + view.total; count.classList.remove('vgc-hidden'); }
    else { count.classList.add('vgc-hidden'); }
    var elapsed = fmtElapsed(view.elapsed_seconds);
    $('vgcElapsed').textContent = elapsed;
    $('vgcFootElapsed').textContent = t('goal.elapsed_label','Elapsed') + ' ' + elapsed + (view.paused ? ' \u00b7 ' + t('goal.paused_note','paused') : '');
    var obj = $('vgcObjective');
    obj.textContent = view.objective || '';
    obj.title = view.objective || '';
    obj.onclick = function(){ obj.classList.toggle('full'); };
    // The goal's captured acceptance obligation, when one exists (durable, from the record).
    var acc = $('vgcAcceptance');
    if (view.acceptance){ acc.textContent = t('goal.acceptance_label','Acceptance') + ': ' + view.acceptance; acc.classList.remove('vgc-hidden'); }
    else acc.classList.add('vgc-hidden');
    // current step
    var cur = $('vgcCurrent');
    var currentStep = null;
    (view.steps||[]).forEach(function(s){ if (s.id === view.current_step_id) currentStep = s; });
    if (currentStep && ['running','planning','queued'].indexOf(view.state) >= 0){
      cur.classList.remove('vgc-hidden');
      cur.innerHTML = '<span class="vgc-spin" aria-hidden="true"></span><span></span>';
      cur.lastChild.textContent = currentStep.title || '';
    } else { cur.classList.add('vgc-hidden'); }
    // pending + completed groups
    var pending = $('vgcPending'); pending.innerHTML = '';
    var completedHtml = '';
    var pendingCount = 0, completedCount = 0;
    (view.steps||[]).forEach(function(s){
      if (s.id === view.current_step_id && s.state !== 'completed') return;
      if (s.state === 'completed'){
        completedCount++;
        completedHtml += '<div class="vgc-step is-completed"><span class="vgc-dot" aria-hidden="true">&#10003;</span><span>' + esc(s.title||'') + '</span></div>';
        return;
      }
      pendingCount++;
      var dot = '';
      if (s.state === 'failed') dot = '<span class="vgc-dot" aria-hidden="true">!</span>';
      else if (s.state === 'waiting_approval' || s.state === 'blocked') dot = '<span class="vgc-dot" aria-hidden="true">&#8230;</span>';
      else dot = '<span class="vgc-dot" aria-hidden="true"></span>';
      var row = '<div class="vgc-step is-' + esc(s.state||'pending') + '">' + dot + '<span>' + esc(s.title||'') ;
      if (s.reason) row += '<div class="vgc-reason">' + esc(s.reason) + '</div>';
      row += '</span></div>';
      pending.innerHTML += row;
    });
    var cSection = $('vgcCompletedSection');
    if (completedCount > 0){
      cSection.classList.remove('vgc-hidden');
      var cList = $('vgcCompletedList');
      cList.innerHTML = completedHtml;
      cList.style.display = $('vgcCompletedToggle').getAttribute('aria-expanded') === 'true' ? '' : 'none';
      $('vgcCompletedCount').textContent = completedCount;
      $('vgcCompletedLabel').textContent = t('goal.completed_group','completed');
      var cCaret = $('vgcCompletedToggle').querySelector('.vgc-caret');
      if (cCaret) cCaret.style.transform = $('vgcCompletedToggle').getAttribute('aria-expanded') === 'true' ? '' : 'rotate(-90deg)';
    } else { cSection.classList.add('vgc-hidden'); }
    // reason line for goal-level cause
    var reason = $('vgcReason');
    if (view.reason && ['failed','interrupted','paused','waiting_approval'].indexOf(view.state) >= 0){
      reason.textContent = view.reason; reason.classList.remove('vgc-hidden');
    } else reason.classList.add('vgc-hidden');
    // actions
    var actions = $('vgcActions'); actions.innerHTML = '';
    function btn(label, primary, fn){
      var b = document.createElement('button');
      b.type='button'; b.className='vgc-btn'+(primary?' is-primary':''); b.textContent=label;
      b.addEventListener('click', fn); actions.appendChild(b); return b;
    }
    function post(action, extra){
      var body = Object.assign({session_id: chatId, goal_id: view.goal_id}, extra||{});
      return fetch('/api/goal/'+action, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)})
        .then(function(r){ return r.json().then(function(d){ return {ok: r.ok, d: d}; }); })
        .then(function(res){ if (res.d && res.d.goal) consume(res.d.goal); else schedulePoll(0); })
        .catch(function(){ schedulePoll(1500); });
    }
    if (view.state === 'running' || view.state === 'planning' || view.state === 'queued') btn(t('goal.pause','Pause'), false, function(){ post('pause'); });
    if (view.state === 'paused' || view.state === 'interrupted' || view.state === 'blocked') btn(t('goal.resume','Resume'), true, function(){ post('resume'); });
    if (view.state === 'waiting_approval') btn(t('goal.retry_step','Resume step'), true, function(){ post('retry'); });
    if (['queued','planning','running','waiting_approval','paused','interrupted','blocked'].indexOf(view.state) >= 0) btn(t('goal.stop','Stop'), false, function(){ post('stop'); });
    if (running) schedulePoll(3000); else schedulePoll(0);
    renderElapsedTick();
    anchorAboveComposer();
  }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function renderElapsedTick(){
    if (elapsedTimer){ clearInterval(elapsedTimer); elapsedTimer = null; }
    if (!view) return;
    if (view.ended_at || view.paused || ['queued','planning','running','waiting_approval'].indexOf(view.state) < 0){
      // Frozen truth between polls: show the server-derived elapsed value as-is.
      var e0 = fmtElapsed(view.elapsed_seconds);
      $('vgcElapsed').textContent = e0;
      return;
    }
    // Live interpolation of the server-derived elapsed value between bounded polls. The
    // server value stays the authority; every poll overwrites the interpolation.
    view._tickBase = view.elapsed_seconds; view._tickAt = Date.now();
    elapsedTimer = setInterval(function(){
      if (!view || view.paused) return;
      var eff = (view._tickBase||0) + Math.max(0, (Date.now() - (view._tickAt||Date.now()))/1000);
      var e = fmtElapsed(eff);
      $('vgcElapsed').textContent = e;
      $('vgcFootElapsed').textContent = t('goal.elapsed_label','Elapsed') + ' ' + e + (view.paused ? ' \u00b7 ' + t('goal.paused_note','paused') : '');
    }, 1000);
  }
  function schedulePoll(delay){
    if (pollTimer){ clearTimeout(pollTimer); pollTimer = null; }
    if (!view) return;
    var running = ['queued','planning','running','waiting_approval','blocked'].indexOf(view.state) >= 0;
    if (!running) return;
    pollTimer = setTimeout(function(){
      if (document.hidden){ schedulePoll(3000); return; }
      fetchState().then(function(){ if (view) schedulePoll(3000); });
    }, delay || 3000);
  }
  function fetchState(){
    var cid = sameChat();
    if (!cid) return Promise.resolve();
    return fetch('/api/goal/state?session=' + encodeURIComponent(cid))
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        chatId = cid;
        var goal = d && d.goal;
        if (goal){ applyView(goal, cid); }
        else { view = null; if (el) el.classList.add('vgc-hidden'); }
      }).catch(function(){});
  }
  function applyView(goal, forChatId){
    if (String(goal.session_id||'') !== sameChat()) return;  // a foreign chat's goal never paints here
    // The chat binding travels WITH the canonical view, so controls fired from an
    // event-stream update carry the same session a hydration fetch would.
    chatId = String(goal.session_id||'');
    var wasOpen = el ? el.classList.contains('vgc-open') : null;
    var prevRevision = view ? view.revision : null;
    var prevId = view ? view.goal_id : null;
    view = goal;
    ensureEl();
    if (!el) return;
    el.setAttribute('data-goal-id', view.goal_id || '');
    if (openPref === null) openPref = wasOpen;
    el.classList.toggle('vgc-open', !!openPref);
    $('vgcHead').setAttribute('aria-expanded', openPref ? 'true' : 'false');
    // A plan revision or a different goal resets the expanded completed group to collapsed.
    if (prevId !== view.goal_id || prevRevision !== view.revision){
      $('vgcCompletedToggle').setAttribute('aria-expanded','false');
    }
    render();
  }
  function anchorAboveComposer(){
    // Presentation-only DOM read: keep the card ABOVE the composer whatever height the
    // composer takes at this width. Never touches the composer itself.
    try {
      var composer = document.getElementById('input');
      if (composer && el) {
        var top = composer.getBoundingClientRect().top;
        if (top > 80) el.style.bottom = Math.max(120, Math.round(window.innerHeight - top + 12)) + 'px';
      }
    } catch(e){}
  }
  function ensureEl(){
    if (el) return;
    el = $('voolGoalCard');
    if (!el) return;
    $('vgcHead').addEventListener('click', function(){
      openPref = !el.classList.contains('vgc-open');
      el.classList.toggle('vgc-open', openPref);
      $('vgcHead').setAttribute('aria-expanded', openPref ? 'true' : 'false');
    });
    $('vgcCompletedToggle').addEventListener('click', function(){
      var b = $('vgcCompletedToggle');
      var open = b.getAttribute('aria-expanded') === 'true';
      b.setAttribute('aria-expanded', open ? 'false' : 'true');
      var list = $('vgcCompletedList'); list.style.display = open ? 'none' : '';
      var caret = b.querySelector('.vgc-caret'); if (caret) caret.style.transform = open ? 'rotate(-90deg)' : '';
    });
    $('vgcActivity').addEventListener('click', function(){
      try { if (window.VoolPageActions) window.VoolPageActions.openPanel(); } catch(e){}
    });
    document.addEventListener('visibilitychange', function(){ if (!document.hidden) { anchorAboveComposer(); fetchState(); } });
    window.addEventListener('resize', anchorAboveComposer);
  }
  window.VoolGoalCard = Object.freeze({
    consume: function(goalView, summary){
      if (!goalView){ return; }
      var cid = sameChat();
      if (String(goalView.session_id||'') !== cid) return;  // a foreign chat's goal never paints here
      applyView(goalView, cid);
    },
    refresh: function(){ fetchState(); },
    chatChanged: function(){ view = null; if (el) el.classList.add('vgc-hidden'); fetchState(); },
    _test: { parseElapsed: fmtElapsed, stateLabel: stateLabel }
  });
  // Hydrate after boot; re-hydrate on chat switches by watching the displayed chat id.
  var lastChat = sameChat();
  setInterval(function(){
    var cid = sameChat();
    if (cid !== lastChat){ lastChat = cid; window.VoolGoalCard.chatChanged(); }
  }, 1500);
  setTimeout(function(){ fetchState(); }, 400);
})();
</script>
"""
