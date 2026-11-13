"""The price-safety gate — the ux-pass1 spend-gate modal bound to the server's real authority.

The design authority's law survives intact: THE UX GATE IS A COURTESY SURFACE — the server's
fail-closed classification (`/api/cloud/model` 409 codes, per-send `cost_state`) remains the
safety authority; this modal only makes the operator's decision informed. It replaces three bare
`window.confirm` dialogs with:

- a typed variant title per server refusal code (`paid_model_confirm_required`,
  `MODEL_COST_UNKNOWN`, `PAID_STATUS_UNKNOWN`) or per client context (pre-pin row click,
  per-send ack on a paid/unknown pin);
- the model's REAL catalog rates (Input/Output per 1M from `/api/cloud/models` rows — cache/
  reasoning rows are rendered only if the catalog ever carries them, never invented);
- catalog freshness ("catalog Xm old", STALE past 10 minutes) and the refresh-before-paid-
  dispatch law: opening a stale catalog re-fetches `?refresh=1` before enabling confirmation,
  so one click confirms against the refreshed discovery;
- Find-cheaper: closes the gate undispatched and opens the model menu, where free rows are
  grouped first and local models are always free;
- Decline: nothing is dispatched, stated in a REFUSED chip toastline.

`window.confirm` stays as the fallback wherever this fragment is not mounted — a fragment
failure can never brick model switching. Namespace: `window.VoolPriceGate`. Prefix `vg-`.

The accepted-ceiling record (`core/model_price_acceptance.py`, written by the server on every
confirmed paid pin) powers two more surfaces here: the `price_above_accepted` variant renders
the exact before→after table with the recorded acceptance date, and every other variant shows
the operator's recorded ceiling for the model when one exists (`/api/cloud/acceptances`,
owner-local). The ceiling can be raised ONLY by confirming through this gate — the server
rewrites the record on that confirmation and on no other path.
"""

from __future__ import annotations

_GATE_CSS = """
#vgOverlay{position:fixed;inset:0;background:rgba(5,7,10,.65);z-index:1300;display:flex;
  align-items:flex-start;justify-content:center}
#vgOverlay[hidden]{display:none}
#vgModal{margin-top:8vh;height:min(540px,84vh);display:flex;flex-direction:column;width:min(580px,92vw);background:var(--panel,#16191f);
  border:1px solid var(--border,#262b35);border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.5);
  overflow-x:hidden}
#vgHead{flex-shrink:0;padding:14px 18px;border-bottom:1px solid var(--border,#262b35);font-weight:700;
  font-size:14px;color:var(--warn,#f0b429)}
#vgBody{flex:1;min-height:0;overflow-y:auto;padding:14px 18px;font-size:13px;color:var(--muted,#9aa1af)}
#vgBody b{color:var(--ink,#e8eaf0)}
.vg-rates{width:100%;border-collapse:collapse;margin:12px 0;font-family:'SF Mono',ui-monospace,Menlo,monospace;font-size:12px}
.vg-rates td{padding:5px 0;color:var(--ink,#e8eaf0)}
.vg-rates td:first-child{color:var(--muted,#9aa1af)}
.vg-fresh{font-size:11px;color:var(--muted,#9aa1af)}
.vg-fresh.vg-stale{color:var(--warn,#f0b429);font-weight:600}
.vg-note{font-size:11px;color:var(--muted,#9aa1af);border:1px solid var(--border,#262b35);
  border-radius:8px;padding:8px 10px;margin-top:10px}
#vgActions{flex-shrink:0;display:flex;gap:8px;flex-wrap:wrap;padding:0 18px 16px}
.vg-btn{border:1px solid var(--border,#262b35);background:var(--field,#1d2129);
  color:var(--ink,#e8eaf0);border-radius:8px;padding:7px 14px;font:inherit;font-size:12px;
  font-weight:600;cursor:pointer}
.vg-btn:hover{border-color:var(--accent,#5eead4)}
.vg-btn.vg-danger{background:var(--warn,#f0b429);color:#201500;border-color:transparent}
.vg-btn.vg-quiet{background:transparent}
.vg-error{color:#ff8f8f;font-size:12px;margin-top:8px;white-space:pre-wrap}
"""

_GATE_JS = """
(function(){
'use strict';
if (window.VoolPriceGate) return;

var STALE_AFTER_S = 600;
var catalogMemo = { at: 0, age: null, rows: {}, provider: '' };
function selectionParts(id, provider){
  var value = String(id || '');
  var match = value.match(/^(openrouter|openai|anthropic|groq|google|deepseek|moonshot|custom):/);
  return { provider: provider || (match ? match[1] : 'openrouter'), id: match ? value.slice(match[0].length) : value };
}
function acceptanceKey(provider, id){ return String(provider || 'openrouter').toLowerCase() + ':' + String(id || '').toLowerCase(); }

function pageActions(){ return window.VoolPageActions || null; }
function chips(){ return window.VoolChips || null; }
function T(key, fallback){
  try {
    if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; }
  } catch (e) {}
  return fallback;
}
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
// Bound the whole read, including its response body. A stalled read cannot leave
// the approval hidden or turn unavailable prices into an approvable empty result.
function priceRead(url){
  var controller = new AbortController();
  var timer;
  var timeout = new Promise(function(_, reject){
    timer = setTimeout(function(){ controller.abort(); reject(new Error('Price check took too long. Retry or cancel; no approval was recorded.')); }, 20000);
  });
  var read = fetch(url, {signal:controller.signal}).then(function(r){
    if (!r.ok) throw new Error('Could not read price information. Retry or cancel.');
    return r.json();
  });
  return Promise.race([read, timeout]).finally(function(){ clearTimeout(timer); });
}
function fetchCatalog(refresh, provider){
  provider = provider || 'openrouter';
  var url = '/api/cloud/models?order=featured&provider=' + encodeURIComponent(provider) + (refresh ? '&refresh=1' : '');
  return priceRead(url).then(function(j){
    if (!j || j.provider !== provider) throw new Error('Provider catalogue identity mismatch');
    var rows = {};
    (j && j.models ? j.models : []).forEach(function(m){ if (m && m.id) rows[String(m.id)] = m; });
    catalogMemo = { at: Date.now(), age: (j && typeof j.age_seconds === 'number') ? j.age_seconds : null, rows: rows, provider: provider };
    return catalogMemo;
  }).catch(function(error){ return { at: 0, age: null, rows: {}, provider: provider, error: error.message }; });
}
function titleFor(ctx){
  if (ctx.code === 'price_above_accepted') return T('pay.title_above_accepted', 'MODEL PRICE ABOVE YOUR ACCEPTED CEILING');
  if (ctx.code === 'MODEL_COST_UNKNOWN') return T('pay.title_cost_unknown', 'MODEL COST UNKNOWN');
  if (ctx.code === 'PAID_STATUS_UNKNOWN') return T('pay.title_status_unknown', 'PAID STATUS INDETERMINATE');
  if (ctx.kind === 'per-send') return T('pay.title_per_send', 'PAID MODEL \\u2014 CONFIRM THIS TURN');
  return T('pay.title_confirm_spend', 'PAID MODEL \\u2014 CONFIRM SPEND');
}
function reasonFor(ctx){
  if (ctx.code === 'price_above_accepted') return T('pay.reason_above_accepted', 'The catalog now lists this model ABOVE the price you accepted earlier. Confirming rewrites your ceiling to the new price; declining keeps the old ceiling and dispatches nothing.');
  if (ctx.code === 'paid_model_confirm_required') return T('pay.reason_paid_classified', 'The server classified this model as PAID.');
  if (ctx.code === 'MODEL_COST_UNKNOWN') return T('pay.reason_cost_unknown', 'The server could not verify what this model costs. Unknown cost fails closed \\u2014 nothing runs until you decide.');
  if (ctx.code === 'PAID_STATUS_UNKNOWN') return T('pay.reason_status_unknown', "This model's published pricing is indeterminate on the server. Indeterminate fails closed.");
  if (ctx.kind === 'per-send') return T('pay.reason_per_send', 'This turn would run on a paid pin. Each turn may charge your account.');
  return T('pay.reason_generic', 'This model may charge your account for each turn.');
}
function riseHtml(ctx){
  if (!ctx.accepted || !ctx.current) return '';
  function money(v){ return '$' + Number(v || 0).toFixed(2); }
  return '<table class="vg-rates">' +
    '<tr><td>' + T('pay.row_input', 'Input') + '</td><td><s>' + money(ctx.accepted.prompt_usd_per_m) + '</s> -> <b>' + money(ctx.current.prompt_usd_per_m) + '</b> ' + T('pay.per_1m', 'per 1M') + '</td></tr>' +
    '<tr><td>' + T('pay.row_output', 'Output') + '</td><td><s>' + money(ctx.accepted.completion_usd_per_m) + '</s> -> <b>' + money(ctx.current.completion_usd_per_m) + '</b> ' + T('pay.per_1m', 'per 1M') + '</td></tr>' +
    '</table>' +
    '<div class="vg-note">' + T('pay.accepted_note', 'Accepted {date}: {prices} per 1M - only this confirmation can raise your ceiling.')
      .replace('{date}', esc(ctx.accepted.accepted_at || T('pay.earlier', 'earlier')))
      .replace('{prices}', money(ctx.accepted.prompt_usd_per_m) + '/' + money(ctx.accepted.completion_usd_per_m)) + '</div>';
}
var acceptanceMemo = { at: 0, rows: {} };
function fetchAcceptances(){
  return priceRead('/api/cloud/acceptances').then(function(j){
    var rows = {};
    (j && j.acceptances ? j.acceptances : []).forEach(function(a){ if (a && a.model) rows[acceptanceKey(a.provider, a.model)] = a; });
    acceptanceMemo = { at: Date.now(), rows: rows };
    return acceptanceMemo;
  }).catch(function(error){ return {error:error.message}; });
}
function acceptanceFor(id){
  var parts = selectionParts(id);
  return acceptanceMemo.rows[acceptanceKey(parts.provider, parts.id)] || null;
}
var PROVIDER_NAMES = { openrouter: 'OpenRouter', openai: 'OpenAI', anthropic: 'Anthropic', groq: 'Groq', google: 'Google', deepseek: 'DeepSeek', moonshot: 'Moonshot', custom: 'Custom endpoint' };
function moneyFor(provider, value){
  return '$' + Number(value || 0).toFixed(2);
}
function ratesHtml(row, provider){
  if (!row) return '<div class="vg-note">' + T('pay.no_rates', 'No catalog rates are published for this id \u2014 the server\u2019s own classification above is the only pricing truth available.') + '</div>';
  var prompt = Number(row.prompt_usd_per_m || 0);
  var completion = Number(row.completion_usd_per_m || 0);
  var lines = '';
  if (prompt || completion) {
    lines += '<tr><td>' + T('pay.row_input', 'Input') + '</td><td>' + moneyFor(provider, prompt) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + '</td></tr>';
    lines += '<tr><td>' + T('pay.row_output', 'Output') + '</td><td>' + moneyFor(provider, completion) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + '</td></tr>';
  }
  if (row.cache_read_usd_per_m != null) lines += '<tr><td>' + T('pay.row_cache_read', 'Cache read') + '</td><td>' + moneyFor(provider, row.cache_read_usd_per_m) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + '</td></tr>';
  if (row.reasoning_usd_per_m != null) lines += '<tr><td>' + T('pay.row_reasoning', 'Reasoning') + '</td><td>' + moneyFor(provider, row.reasoning_usd_per_m) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + '</td></tr>';
  if (!lines) return '<div class="vg-note">This row publishes no per-token rates.</div>';
  return '<table class="vg-rates">' + lines + '</table>';
}
// What accepting a price here does and does not do.
function scopeHtml(provider){
  return '<p class="vg-note">' + T('pay.scope_standard', 'Accepting the price for this chat lasts 24 hours for this model only, and survives reloading the app. Your existing spending limits still apply; a price above the accepted bound requires a new decision.') + '</p>';
}
function freshHtml(age){
  if (age == null) return '<span class="vg-fresh">' + T('pay.fresh_unknown', 'catalog age unknown') + '</span>';
  var minutes = Math.round(age / 60);
  var stale = age > STALE_AFTER_S;
  return '<span class="vg-fresh' + (stale ? ' vg-stale' : '') + '">' + T('pay.fresh_prefix', 'catalog ') +
    (minutes < 1 ? T('pay.under_a_minute', 'under a minute') : minutes + ' ' + T('pay.minutes_short', 'min')) + ' ' + T('pay.fresh_suffix', 'old') + (stale ? ' \u00b7 ' + T('pay.stale_mark', 'STALE') : '') + '</span>';
}

var overlay = document.createElement('div');
overlay.id = 'vgOverlay';
overlay.hidden = true;
overlay.setAttribute('role', 'dialog');
overlay.setAttribute('aria-modal', 'true');
overlay.innerHTML = '<div id="vgModal"><div id="vgHead"></div><div id="vgBody"></div><div id="vgActions"></div></div>';
document.body.appendChild(overlay);
var headEl = overlay.querySelector('#vgHead');
var bodyEl = overlay.querySelector('#vgBody');
var actionsEl = overlay.querySelector('#vgActions');
var activeResolve = null;
var activeDecision = null;
var activeKey = null;

function closeGate(result){
  overlay.hidden = true;
  var resolve = activeResolve;
  activeResolve = null;
  activeDecision = null; activeKey = null;
  if (resolve) resolve(result);
}
overlay.addEventListener('mousedown', function(ev){
  if (ev.target === overlay) {
    var p = pageActions();
    if (p) p.toast('Nothing dispatched \\u2014 decide with the buttons.');
  }
});
document.addEventListener('keydown', function(ev){
  if (ev.key === 'Escape' && !overlay.hidden) { ev.stopPropagation(); closeGate(false); }
}, true);

function gateErrorEl(){
  var el = document.getElementById('vgError');
  if (!el) {
    var modal = document.getElementById('vgModal');
    if (!modal) return { textContent: '' };   // unmounted fragment: error display is a no-op
    el = document.createElement('div');
    el.id = 'vgError'; el.className = 'vg-error'; el.setAttribute('role','alert');
    modal.appendChild(el);
  }
  return el;
}
function showGateError(text){ gateErrorEl().textContent = String(text || ''); }
// Whether the dialog can name a price at all. The server's own codes say so first; otherwise a
// catalog row with at least one per-token rate is a known price. While the rates load the usual
// words stay (the buttons are disabled then), so a slow read never flashes "no known price".
function priceKnown(ctx, row){
  if (ctx.code === 'MODEL_COST_UNKNOWN' || ctx.code === 'PAID_STATUS_UNKNOWN') return false;
  if (ctx.code === 'price_above_accepted' || ctx._loading) return true;
  return !!row && (Number(row.prompt_usd_per_m || 0) > 0 || Number(row.completion_usd_per_m || 0) > 0);
}
// The two accept buttons never say "Accept price" when there is no price to accept.
function confirmLabel(ctx, known){
  if (ctx.kind === 'per-send') {
    return known ? T('pay.btn_accept_send', 'Accept price and send') : T('pay.btn_send_price_unknown', 'Send without a known price');
  }
  return known ? T('pay.btn_accept_select', 'Accept price and select') : T('pay.btn_select_price_unknown', 'Select without a known price');
}
function conversationLabel(known){
  return known
    ? T('pay.btn_accept_chat', 'Accept price for this chat (24 hours)')
    : T('pay.btn_chat_price_unknown', 'Allow for this chat without a known price (24 hours)');
}
function paint(ctx, memo){
  var parts = selectionParts(ctx.id, ctx.provider);
  if (memo.provider !== parts.provider) memo = { at: 0, age: null, rows: {}, provider: parts.provider };
  var row = memo.rows[parts.id] || null;
  var known = priceKnown(ctx, row);
  headEl.textContent = titleFor(ctx);
  bodyEl.innerHTML =
    '<p><b>' + esc(ctx.label || ctx.id) + '</b> \\u2014 ' + esc(reasonFor(ctx)) + '</p>' +
    '<div class="vg-note vg-identity">Provider <b>' + esc(PROVIDER_NAMES[parts.provider] || parts.provider) + '</b> \\u00b7 model <b>' + esc(parts.id) + '</b></div>' +
    (ctx._loading ? '<p role="status">' + T('pay.loading', 'Checking current prices and saved limits… You can cancel while this loads.') + '</p>' : (ctx.code === 'price_above_accepted' ? riseHtml(ctx) : ratesHtml(row, parts.provider))) +
    (ctx.code !== 'price_above_accepted' && acceptanceFor(ctx.id) && acceptanceFor(ctx.id).rates_known
      ? '<div class="vg-note">' + T('pay.recorded_ceiling_prefix', 'Your recorded ceiling for this model: ') + moneyFor(parts.provider, acceptanceFor(ctx.id).prompt_usd_per_m) + ' / ' + moneyFor(parts.provider, acceptanceFor(ctx.id).completion_usd_per_m) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + ' (' + T('pay.accepted_at', 'accepted') + ' ' + esc(acceptanceFor(ctx.id).accepted_at || T('pay.earlier', 'earlier')) + ').</div>'
      : '') +
    '<div>' + freshHtml(memo.age) + '</div>' +
    scopeHtml(parts.provider) +
    '<div class="vg-note">' + T('pay.nothing_sent_note', 'Nothing is sent until you decide. Your spending limits still apply.') + '</div>';
  if (ctx._actionsMounted) {
    actionsEl.children[0].disabled = !!ctx._loading;
    actionsEl.children[1].disabled = !!ctx._loading;
    // The rates arrive after the buttons mount: their words follow what the dialog now knows.
    actionsEl.children[0].textContent = confirmLabel(ctx, known);
    actionsEl.children[1].textContent = conversationLabel(known);
    return;
  }
  ctx._actionsMounted = true;
  actionsEl.innerHTML = '';
  var confirmBtn = document.createElement('button');
  confirmBtn.className = 'vg-btn vg-danger vg-confirm';
  confirmBtn.disabled = !!ctx._loading;
  confirmBtn.textContent = confirmLabel(ctx, known);
  confirmBtn.addEventListener('click', function(){
    if (ctx._loading) return;
    // BOTH accept buttons resolve the SAME chat-scoped acceptance the dialog's own scope text
    // promises ("this chat, this model, for 24 hours"). The primary used to resolve bare
    // `true`, which the page stores as a ONE-TURN ack consumed by the first send -- so the next
    // send re-asked the identical price decision the operator had just made (and a per-send
    // acceptance never persisted at all). A narrower one-turn decision is not offered by any
    // button, so nothing the operator chose is narrowed by making the scopes agree.
    closeGate('conversation');
  });
  var conversationBtn = document.createElement('button');
  conversationBtn.className = 'vg-btn vg-danger vg-conversation';
  conversationBtn.disabled = !!ctx._loading;
  conversationBtn.textContent = conversationLabel(known);
  conversationBtn.addEventListener('click', function(){
    if (ctx._loading) return;
    closeGate('conversation');
  });
  var cheaperBtn = document.createElement('button');
  cheaperBtn.className = 'vg-btn vg-cheaper';
  cheaperBtn.textContent = T('pay.btn_find_cheaper', 'Find cheaper (free) models');
  cheaperBtn.addEventListener('click', function(){
    closeGate(false);
    var p = pageActions();
    if (p) {
      p.openModelMenu();
      p.toast(T('pay.toast_free_first', 'Free rows are grouped first \u2014 and local models never spend.'));
    }
  });
  var declineBtn = document.createElement('button');
  declineBtn.className = 'vg-btn vg-quiet vg-decline';
  declineBtn.textContent = T('pay.btn_decline', 'Decline \\u2014 dispatch nothing');
  declineBtn.addEventListener('click', function(){
    closeGate(false);
    var p = pageActions();
    var chip = chips();
    if (p) p.toast((chip ? '' : T('pay.refused_mark', 'REFUSED \u2014 ')) + T('pay.toast_declined', 'declined by you \u2014 nothing was dispatched'));
  });
  actionsEl.appendChild(confirmBtn);
  actionsEl.appendChild(conversationBtn);
  actionsEl.appendChild(cheaperBtn);
  actionsEl.appendChild(declineBtn);
  confirmBtn.focus();
}

function review(ctx){
  ctx = ctx || {};
  var key = JSON.stringify([ctx.kind, ctx.provider, ctx.id]);
  if (activeDecision && activeKey === key) return activeDecision;
  if (activeResolve) closeGate(false);
  activeKey = key;
  activeDecision = new Promise(function(resolve){
    activeResolve = resolve;
    overlay.hidden = false;
    showGateError('');
    ctx._loading = true;
    ctx._actionsMounted = false;
    paint(ctx, catalogMemo);
    // Keep the decision buttons mounted and disabled until all reads finish.
    // A slow provider still leaves the owner a visible Cancel/Escape path.
    var retryBtn = document.createElement('button');
    retryBtn.className = 'vg-btn'; retryBtn.textContent = T('pay.btn_retry', 'Retry price check'); retryBtn.hidden = true;
    actionsEl.appendChild(retryBtn);
    retryBtn.addEventListener('click', function(){ retryBtn.hidden = true; showGateError(''); loadReview(); });
    actionsEl.children[3].focus();
    function loadReview(){
      var provider = selectionParts(ctx.id, ctx.provider).provider;
      var fresh = catalogMemo.provider === provider && Date.now() - catalogMemo.at < 60000;
      // Refresh before offering a decision, not as a side effect of Confirm spend.
      // The single click then accepts the rates the operator has actually reviewed.
      var prices = (fresh ? Promise.resolve(catalogMemo) : fetchCatalog(false, provider)).then(function(memo){
        if (activeResolve !== resolve) return null;
        return memo.age != null && memo.age > STALE_AFTER_S ? fetchCatalog(true, provider) : memo;
      });
      // Publish one complete review. A late acceptance read must never replace live buttons
      // between pointer-down and pointer-up, or shift the price the operator is reviewing.
      var acceptances = Date.now() - acceptanceMemo.at > 60000 ? fetchAcceptances() : Promise.resolve(acceptanceMemo);
      Promise.all([prices, acceptances]).then(function(results){
        var memo = results[0];
        if (activeResolve !== resolve) return;
        var failed = results.find(function(result){ return !result || result.error; });
        if (!memo || failed) {
          showGateError((failed && failed.error) || 'Could not read price information. Retry or cancel.');
          retryBtn.hidden = false;
          return;
        }
        ctx._loading = false;
        showGateError('');
        paint(ctx, memo);
        overlay.hidden = false;
        actionsEl.children[0].focus();
      });
    }
    loadReview();
  });
  return activeDecision;
}

fetchAcceptances();
window.VoolPriceGate = Object.freeze({
  review: review,
  freshness: function(){ return catalogMemo.age; },
  acceptanceFor: acceptanceFor,
});
})();
"""


def render_price_safety_fragment() -> str:
    """The price-safety gate as an appended fragment."""
    return "<style>" + _GATE_CSS + "</style><script>" + _GATE_JS + "</script>"


__all__ = ["render_price_safety_fragment"]
