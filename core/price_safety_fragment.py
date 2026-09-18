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
.vg-max{margin:10px 0 2px}
.vg-max-row{display:flex;align-items:center;gap:8px;margin:6px 0}
.vg-max-row label{flex:0 0 128px;color:var(--muted,#9aa1af);font-size:12px}
.vg-max-row input{flex:1;min-width:0;background:var(--field,#1d2129);border:1px solid var(--border,#262b35);
  color:var(--ink,#e8eaf0);border-radius:8px;padding:7px 10px;font:inherit;font-size:12px;
  font-family:'SF Mono',ui-monospace,Menlo,monospace}
.vg-max-row input:focus{outline:none;border-color:var(--accent,#5eead4)}
.vg-max-unit{flex:0 0 auto;color:var(--muted,#9aa1af);font-size:11px}
.vg-error{color:#ff8f8f;font-size:12px;margin-top:8px;white-space:pre-wrap}
details.vg-advanced{margin-top:8px;font-size:11px;color:var(--muted,#9aa1af)}
details.vg-advanced summary{cursor:pointer;color:var(--muted,#9aa1af)}
"""

_GATE_JS = """
(function(){
'use strict';
if (window.VoolPriceGate) return;

var STALE_AFTER_S = 600;
var catalogMemo = { at: 0, age: null, rows: {}, provider: '' };
function selectionParts(id, provider){
  var value = String(id || '');
  var match = value.match(/^(openrouter|openai|anthropic|groq|google|deepseek|moonshot|usepod|custom):/);
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
  if (ctx.kind === 'review') return 'REVIEW USEPOD PRICE LIMITS';
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
// Exact display of a USDC-per-1M microunit integer: string math only, no float rounding, because
// the value typed back from this display becomes the saved maximum the server converts exactly.
function microToUsdcString(micro){
  var n = String(Math.trunc(Number(micro) || 0));
  while (n.length < 7) n = '0' + n;
  var s = n.slice(0, -6) + '.' + n.slice(-6);
  if (s.indexOf('.') !== -1) s = s.replace(/0+$/, '').replace(/\\.$/, '');
  return s || '0';
}
// A reviewed maximum as the server will receive it: a plain finite positive decimal string with
// microunit precision (<= 6 fractional digits). Returns the trimmed string or ''.
function parseReviewMax(raw){
  var text = String(raw == null ? '' : raw).trim();
  if (!/^\\d{1,9}(?:\\.\\d{1,6})?$/.test(text)) return '';
  var n = Number(text);
  if (!isFinite(n) || n <= 0) return '';
  return text;
}
// The saved UsePod route bound for one model, from the server-owned route authority the dispatch
// re-check reads. Cache-only per gate open; a failure reads as "no bound" (fields prefill from
// the observed catalog rates instead).
var routeBoundMemo = { at: 0, rows: {} };
function fetchUsePodRouteBounds(force){
  if (!force && Date.now() - routeBoundMemo.at < 15000 && routeBoundMemo.fetched) {
    return Promise.resolve(routeBoundMemo);
  }
  return priceRead('/api/cloud/usepod/discovery').then(function(j){
    var rows = {};
    var bounds = (j && j.approved_routes) || {};
    Object.keys(bounds).forEach(function(modelId){ if (bounds[modelId]) rows[String(modelId)] = bounds[modelId]; });
    routeBoundMemo = { at: Date.now(), fetched: true, rows: rows };
    return routeBoundMemo;
  }).catch(function(error){ return {error:error.message}; });
}
function usePodBoundFor(id){
  var parts = selectionParts(id, 'usepod');
  return routeBoundMemo.rows[parts.id] || null;
}
var PROVIDER_NAMES = { usepod: 'UsePod', openrouter: 'OpenRouter', openai: 'OpenAI', anthropic: 'Anthropic', groq: 'Groq', google: 'Google', deepseek: 'DeepSeek', moonshot: 'Moonshot', custom: 'Custom endpoint' };
// UsePod prices are USDC per million tokens (the marketplace feed's own unit); the others are USD.
function moneyFor(provider, value){
  var amount = Number(value || 0).toFixed(2);
  return provider === 'usepod' ? amount + ' USDC' : '$' + amount;
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
// What accepting a price here DOES and does not do. A UsePod one-call spending approval is a
// separate, narrower authority (Settings \\u2192 UsePod spending): accepting a price for a chat
// never widens it, so this review must not read as "permission for this conversation".
function scopeHtml(provider){
  if (provider === 'usepod') {
    return '<p class="vg-note">' + T('pay.scope_usepod', 'Accepting the price covers <b>price only</b>: this chat, this model, for 24 hours. Each paid UsePod request stays within your approved UsePod budget (Settings \u2192 UsePod spending); accepting a price never widens that approval. Your existing spending limits still apply; higher prices require a new decision.') + '</p>';
  }
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

// The editable two-axis maxima, next to the observed prices, in human units (USDC per 1M
// tokens). Prefilled with the SAVED bound when one exists (reopening shows what dispatch
// enforces), otherwise with the observed catalog rates. Rendered only for UsePod and only once
// loading has finished, so a paint never destroys a draft being edited. Advanced route facts
// (mode, allowed classes, approval basis) stay collapsed behind their own summary.
function formatApprovedAt(value){
  var n = Number(value);
  if (!isFinite(n) || n <= 0) return String(value || 'earlier');
  try { return new Date(n * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC'; }
  catch (e) { return String(value); }
}
function reviewMaximaHtml(ctx, row, parts){
  if (parts.provider !== 'usepod' || ctx._loading) return '';
  var bound = usePodBoundFor(ctx.id);
  var defInput = bound && bound.max_input_microunits_per_million != null
    ? microToUsdcString(bound.max_input_microunits_per_million)
    : (row && row.prompt_usd_per_m != null ? String(row.prompt_usd_per_m) : '');
  var defOutput = bound && bound.max_output_microunits_per_million != null
    ? microToUsdcString(bound.max_output_microunits_per_million)
    : (row && row.completion_usd_per_m != null ? String(row.completion_usd_per_m) : '');
  var axes = '';
  if (ctx.refusal_axes && ctx.refusal_axes.length) {
    axes = '<div class="vg-note">' + T('pay.refused_prefix', 'Refused: ') + ctx.refusal_axes.map(function(axis){
      return esc(axis.axis) + ' ' + T('pay.axis_observed', 'observed') + ' ' + esc(axis.observed_usdc_per_million) + ' > ' + T('pay.axis_saved_max', 'saved max') + ' ' + esc(axis.saved_max_usdc_per_million);
    }).join(' · ') + ' USDC ' + T('pay.per_1m', 'per 1M') + '.</div>';
  }
  var advanced = '';
  if (bound) {
    advanced = '<details class="vg-advanced"><summary>' + T('pay.advanced_facts', 'Advanced route facts') + '</summary>' +
      '<div>routing mode ' + esc(bound.header_routing_mode || 'marketplace-only') +
      ' · allowed ' + esc((bound.allowed_route_classes || []).join(', ')) +
      ' · basis ' + esc(bound.input_basis || '') + ' / ' + esc(bound.output_basis || '') +
      ' · approved ' + esc(formatApprovedAt(bound.approved_at)) + '</div></details>';
  }
  return axes +
    '<div class="vg-max"' + (bound ? ' data-has-bound="1"' : '') + '><div><b>' + T('pay.maxima_title', 'Your maximum prices') + '</b> — ' + T('pay.maxima_note', 'USDC per 1M tokens. These saved limits are exactly what dispatch enforces; a price above them is refused, never silently allowed.') + '</div>' +
    '<div class="vg-max-row"><label for="vgMaxIn">' + T('pay.max_input', 'Maximum input') + '</label>' +
    '<input id="vgMaxIn" class="vg-max-in" inputmode="decimal" autocomplete="off" value="' + esc(defInput) + '" placeholder="' + T('pay.eg_1_25', 'e.g. 1.25') + '">' +
    '<span class="vg-max-unit">' + T('pay.unit_in', 'USDC / 1M in') + '</span></div>' +
    '<div class="vg-max-row"><label for="vgMaxOut">' + T('pay.max_output', 'Maximum output') + '</label>' +
    '<input id="vgMaxOut" class="vg-max-out" inputmode="decimal" autocomplete="off" value="' + esc(defOutput) + '" placeholder="' + T('pay.eg_4_00', 'e.g. 4.00') + '">' +
    '<span class="vg-max-unit">' + T('pay.unit_out', 'USDC / 1M out') + '</span></div>' +
    (bound ? '<div class="vg-fresh">' + T('pay.saved_bound', 'saved maximum from the approved route · ') + esc(formatApprovedAt(bound.approved_at)) + '</div>' : '') +
    advanced + '</div>';
}
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
function setGateBusy(busy){
  var actions = document.getElementById('vgActions');
  if (!actions) return;
  for (var i = 0; i < actions.children.length; i++) actions.children[i].disabled = !!busy;
}
// Collect + validate the two reviewed maxima. Returns null when this gate has no maxima to save
// (non-UsePod, or still loading), '' when the draft is invalid (error already shown), or the
// pair of exact decimal strings the server will convert.
function collectReviewedMaxima(ctx){
  var parts = selectionParts(ctx.id, ctx.provider);
  if (parts.provider !== 'usepod' || ctx._loading) return null;
  var inEl = document.getElementById('vgMaxIn'), outEl = document.getElementById('vgMaxOut');
  if (!inEl || !outEl) return null;
  // Save only what the operator actually reviewed: an unedited confirmation under an EXISTING
  // bound writes nothing (the bound stands; a price rise above it refuses at dispatch with the
  // axes named), while a first confirmation — no bound yet — records the accepted observed
  // prices exactly as the derived approval always did.
  var hasBound = !!document.querySelector('.vg-max[data-has-bound="1"]');
  if (hasBound && inEl.value === inEl.defaultValue && outEl.value === outEl.defaultValue) return null;
  var maxIn = parseReviewMax(inEl.value), maxOut = parseReviewMax(outEl.value);
  if (!maxIn || !maxOut) {
    showGateError(T('pay.err_maxima', 'Give both maximum prices as positive numbers in USDC per 1M tokens (up to 6 decimals), e.g. 1.25 and 4.00.'));
    return '';
  }
  return { max_input_usdc_per_million: maxIn, max_output_usdc_per_million: maxOut };
}
// Save the reviewed maxima through the server-owned route authority.
//
// OWNERSHIP OF ASYNC COMPLETION: the callbacks here belong to the ONE review that started the
// save (identified by its `resolve`). Liveness is re-checked after every hop; once that review
// is gone (closed, escaped, or replaced by a newer review), the outcome is `superseded` and the
// CALLER MUST DO NOTHING WITH IT — no toast, no closeGate, no repaint, no re-enabling buttons.
// The server-side write of an explicitly confirmed save still lands; what is discarded is the
// obsolete review's authority over a dialog that now belongs to someone else. (Measured in the
// browser before this shape: a superseded save resolved `true`, so its handler toasted and called
// closeGate(true) — approving and closing whatever NEWER review was open — and a superseded
// FAILURE painted its error into the newer dialog and re-enabled its buttons.)
function saveReviewedMaxima(ctx, maxima){
  var parts = selectionParts(ctx.id, ctx.provider);
  var resolve = activeResolve;
  function live(){ return activeResolve === resolve; }
  return fetch('/api/cloud/usepod/approve-route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: parts.id,
      max_input_usdc_per_million: maxima.max_input_usdc_per_million,
      max_output_usdc_per_million: maxima.max_output_usdc_per_million })
  }).then(function(r){ return r.json().then(function(j){ return { ok: r.ok, j: j }; }); }).then(function(result){
    if (!live()) return { superseded: true };
    if (!result.ok) {
      return { superseded: false, saved: false,
        error: (result.j && (result.j.error || result.j.code)) || 'The server refused these price limits; nothing was saved.' };
    }
    routeBoundMemo = { at: 0, fetched: false, rows: routeBoundMemo.rows || {} };
    return fetchUsePodRouteBounds(true).then(function(){
      if (!live()) return { superseded: true };
      return { superseded: false, saved: true };
    });
  }).catch(function(){
    if (!live()) return { superseded: true };
    return { superseded: false, saved: false,
      error: T('pay.err_save', 'Could not reach the server to save these price limits; nothing was saved.') };
  });
}
function paint(ctx, memo){
  var parts = selectionParts(ctx.id, ctx.provider);
  if (memo.provider !== parts.provider) memo = { at: 0, age: null, rows: {}, provider: parts.provider };
  var row = memo.rows[parts.id] || null;
  headEl.textContent = titleFor(ctx);
  bodyEl.innerHTML =
    '<p><b>' + esc(ctx.label || ctx.id) + '</b> \\u2014 ' + esc(reasonFor(ctx)) + '</p>' +
    '<div class="vg-note vg-identity">Provider <b>' + esc(PROVIDER_NAMES[parts.provider] || parts.provider) + '</b> \\u00b7 model <b>' + esc(parts.id) + '</b>' +
      (parts.provider === 'usepod' ? ' \u00b7 ' + T('pay.usepod_prices_note', 'prices in USDC per 1M tokens (marketplace feed)') : '') + '</div>' +
    (ctx._loading ? '<p role="status">' + T('pay.loading', 'Checking current prices and saved limits… You can cancel while this loads.') + '</p>' : (ctx.code === 'price_above_accepted' ? riseHtml(ctx) : ratesHtml(row, parts.provider))) +
    reviewMaximaHtml(ctx, row, parts) +
    (ctx.code !== 'price_above_accepted' && acceptanceFor(ctx.id) && acceptanceFor(ctx.id).rates_known
      ? '<div class="vg-note">' + T('pay.recorded_ceiling_prefix', 'Your recorded ceiling for this model: ') + moneyFor(parts.provider, acceptanceFor(ctx.id).prompt_usd_per_m) + ' / ' + moneyFor(parts.provider, acceptanceFor(ctx.id).completion_usd_per_m) + ' ' + T('pay.per_1m_tokens', 'per 1M tokens') + ' (' + T('pay.accepted_at', 'accepted') + ' ' + esc(acceptanceFor(ctx.id).accepted_at || T('pay.earlier', 'earlier')) + ').</div>'
      : '') +
    '<div>' + freshHtml(memo.age) + '</div>' +
    scopeHtml(parts.provider) +
    '<div class="vg-note">' + T('pay.nothing_sent_note', 'Nothing is sent until you decide. Your spending limits still apply.') + '</div>';
  if (ctx._actionsMounted) {
    actionsEl.children[0].disabled = !!ctx._loading;
    actionsEl.children[1].disabled = !!ctx._loading;
    return;
  }
  ctx._actionsMounted = true;
  actionsEl.innerHTML = '';
  var confirmBtn = document.createElement('button');
  confirmBtn.className = 'vg-btn vg-danger vg-confirm';
  confirmBtn.disabled = !!ctx._loading;
  confirmBtn.textContent = ctx.kind === 'review' ? T('pay.btn_save_limits', 'Save price limits') : (ctx.kind === 'per-send' ? T('pay.btn_accept_send', 'Accept price and send') : T('pay.btn_accept_select', 'Accept price and select'));
  confirmBtn.addEventListener('click', function(){
    if (ctx._loading || ctx._saving) return;
    var maxima = collectReviewedMaxima(ctx);
    // BOTH accept buttons resolve the SAME chat-scoped acceptance the dialog's own scope text
    // promises ("this chat, this model, for 24 hours"). The primary used to resolve bare
    // `true`, which the page stores as a ONE-TURN ack consumed by the first send -- so the next
    // send re-asked the identical price decision the operator had just made (and a per-send
    // acceptance never persisted at all). A narrower one-turn decision is not offered by any
    // button, so nothing the operator chose is narrowed by making the scopes agree.
    if (maxima === null) { closeGate('conversation'); return; }
    if (!maxima) return;                      // invalid draft: the error line says which axis
    ctx._saving = true;
    setGateBusy(true);
    showGateError('');
    saveReviewedMaxima(ctx, maxima).then(function(outcome){
      if (outcome.superseded) return;         // this review is gone: approve/close/repaint/enable NOTHING
      ctx._saving = false;
      if (!outcome.saved) { setGateBusy(false); showGateError(outcome.error); return; }   // keep the draft and the dialog open
      var p = pageActions();
      if (p) p.toast(T('pay.toast_saved_prefix', 'Price limits saved for ') + (ctx.label || ctx.id) + ' \u2014 ' + T('pay.toast_saved_suffix', 'nothing was sent by saving them.'));
      closeGate('conversation');
    });
  });
  var conversationBtn = document.createElement('button');
  conversationBtn.className = 'vg-btn vg-danger vg-conversation';
  conversationBtn.disabled = !!ctx._loading;
  conversationBtn.textContent = T('pay.btn_accept_chat', 'Accept price for this chat (24 hours)');
  conversationBtn.addEventListener('click', function(){
    if (ctx._loading || ctx._saving) return;
    var maxima = collectReviewedMaxima(ctx);
    if (maxima === null) { closeGate('conversation'); return; }
    if (!maxima) return;
    ctx._saving = true;
    setGateBusy(true);
    showGateError('');
    saveReviewedMaxima(ctx, maxima).then(function(outcome){
      if (outcome.superseded) return;         // same rule as the primary confirm button
      ctx._saving = false;
      if (!outcome.saved) { setGateBusy(false); showGateError(outcome.error); return; }
      closeGate('conversation');
    });
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
      var fresh = provider !== 'usepod' && catalogMemo.provider === provider && Date.now() - catalogMemo.at < 60000;
      // Refresh before offering a decision, not as a side effect of Confirm spend.
      // The single click then accepts the rates the operator has actually reviewed.
      var prices = (fresh ? Promise.resolve(catalogMemo) : fetchCatalog(provider === 'usepod', provider)).then(function(memo){
        if (activeResolve !== resolve) return null;
        return memo.age != null && memo.age > STALE_AFTER_S ? fetchCatalog(true, provider) : memo;
      });
      // Publish one complete review. A late acceptance read must never replace live buttons
      // between pointer-down and pointer-up, or shift the price the operator is reviewing.
      var acceptances = Date.now() - acceptanceMemo.at > 60000 ? fetchAcceptances() : Promise.resolve(acceptanceMemo);
      // The saved route bound (the authority dispatch enforces) so reopening shows the saved
      // maxima, and the review kind opens with the current enforcement in front of the operator.
      var bounds = provider === 'usepod' ? fetchUsePodRouteBounds() : Promise.resolve(routeBoundMemo);
      Promise.all([prices, acceptances, bounds]).then(function(results){
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
