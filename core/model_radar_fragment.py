"""The Model Radar surface — a quiet top-right chip, not an alarm bell.

Fragment law: one self-contained <style>+<script> IIFE, namespace
``window.VoolModelRadar``, prefix ``mr-``, appended before </body> so it evaluates
after the page script it calls into. Discover models lives in the model selector;
the popover renders one card per QUALIFIED finding the server
already judged (this fragment never decides what qualifies — it renders
``/api/model-radar/feed`` rows or nothing).

Every card states its evidence: per-component before/after prices (input, output,
cached — never blended), the offer kind (permanent / promotion / free quota /
subsidy) with expiry when temporary, context, tool/image support, data terms (or
"not stated" — the honest value), the evidence timestamp and source URL, and the
WHY sentence from the authority. Actions: Try once (one turn, server-receipted),
Set as default (the page's own A11-gated model switch — never a bypass), Hide offer
(permanent, server-side; separate from dismissing an inbox notification). Preferences cover providers, lane, required
capabilities and notification frequency.
"""

from __future__ import annotations

_MR_CSS = """
#mrPop{position:fixed;z-index:1150;width:min(420px,94vw);max-height:min(560px,80vh);overflow:auto;
  background:var(--panel,#16191f);border:1px solid var(--border,#262b35);border-radius:12px;
  box-shadow:0 10px 40px rgba(0,0,0,.5)}
#mrPop[hidden]{display:none}
.mr-head{padding:10px 14px;font-size:11px;letter-spacing:.1em;color:var(--muted,#9aa1af);
  border-bottom:1px solid var(--border,#262b35);text-transform:uppercase;display:flex;
  align-items:center;gap:8px}
.mr-head .mr-prefs-btn{margin-left:auto;letter-spacing:0;text-transform:none;font:inherit;
  font-size:12px;background:transparent;color:var(--muted,#9aa1af);border:1px solid
  var(--border,#262b35);border-radius:6px;padding:3px 8px;cursor:pointer}
.mr-head .mr-prefs-btn:hover{color:var(--ink,#e8eaf0)}
.mr-card{padding:12px 14px;border-bottom:1px solid var(--border,#262b35);font-size:13px;
  color:var(--ink,#e8eaf0)}
.mr-card:last-of-type{border-bottom:none}
.mr-title{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.mr-title b{font-size:14px}
.mr-prov{font-size:11px;color:var(--muted,#9aa1af);border:1px solid var(--border,#262b35);
  border-radius:6px;padding:1px 6px}
.mr-kind{font-size:11px;font-weight:700;color:var(--accent,#5eead4)}
.mr-kind.mr-free{color:#7ee2a8}
.mr-prices{margin:8px 0 4px;display:grid;gap:2px;font-size:12.5px;color:var(--ink,#e8eaf0)}
.mr-prices .mr-row{display:flex;gap:8px;align-items:baseline}
.mr-prices .mr-lab{color:var(--muted,#9aa1af);width:74px;flex:none}
.mr-prices .mr-pct{margin-left:auto;color:var(--accent,#5eead4);font-weight:700;white-space:nowrap}
.mr-prices .mr-na{color:var(--muted,#9aa1af);font-style:italic}
.mr-meta{margin:6px 0 2px;font-size:12px;color:var(--muted,#9aa1af);display:flex;
  gap:10px;flex-wrap:wrap}
.mr-meta .mr-ok{color:#7ee2a8}
.mr-meta .mr-no{color:var(--bad,#f2545b)}
.mr-why{margin:8px 0 2px;font-size:12.5px;color:var(--ink,#e8eaf0);background:var(--field,#1d2129);
  border-left:2px solid var(--accent,#5eead4);padding:7px 9px;border-radius:0 6px 6px 0}
.mr-ev{margin-top:6px;font-size:11px;color:var(--muted,#9aa1af);word-break:break-all}
.mr-ev a{color:var(--accent,#5eead4)}
.mr-actions{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.mr-btn{font:inherit;font-size:12px;border-radius:6px;padding:5px 10px;cursor:pointer;
  background:transparent;color:var(--ink,#e8eaf0);border:1px solid var(--border,#262b35)}
.mr-btn:hover{border-color:var(--accent,#5eead4)}
.mr-btn.mr-primary{background:var(--accent,#5eead4);color:#04231d;border-color:transparent;font-weight:700}
.mr-btn.mr-ghost{color:var(--muted,#9aa1af)}
.mr-status{margin-top:6px;font-size:12px;color:var(--accent,#5eead4)}
.mr-status.mr-err{color:var(--bad,#f2545b)}
.mr-empty{padding:16px 14px;font-size:12.5px;color:var(--muted,#9aa1af)}
.mr-prefs{padding:12px 14px;border-top:1px solid var(--border,#262b35);font-size:12.5px;
  color:var(--ink,#e8eaf0);display:grid;gap:8px}
.mr-prefs[hidden]{display:none}
.mr-prefs label{display:flex;gap:8px;align-items:center;color:var(--ink,#e8eaf0)}
.mr-prefs .mr-hint{color:var(--muted,#9aa1af);font-size:11.5px}
.mr-prefs input[type=text],.mr-prefs select{font:inherit;font-size:12px;color:var(--ink,#e8eaf0);
  background:var(--field,#1d2129);border:1px solid var(--border,#262b35);border-radius:6px;padding:4px 6px}
.mr-foot{padding:8px 14px;font-size:11px;color:var(--muted,#9aa1af);border-top:1px solid var(--border,#262b35)}
"""

_MR_JS = """
(function(){
'use strict';
if (window.VoolModelRadar) return;

var COMPONENT_WORDS = {input_usd_per_m: 'input', output_usd_per_m: 'output', cached_input_usd_per_m: 'cached'};
var feed = {findings: [], unread: 0, preferences: {}, open_conflicts: 0};
var pop, prefsOpen = false;

/* The page's i18n catalog owns this surface's words (the chat page's bootstrap defines
   VOOLT/VOOLFMT before this fragment loads); the inline English fallbacks are the catalog
   source, so without a bundle the surface renders byte-identical English. */
function RT(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
function RTF(key, fallback, params){
  var t = RT(key, fallback);
  try {
    if (typeof VOOLFMT === 'function') return VOOLFMT(t, params || {});
  } catch (e) {}
  return String(t).replace(/\\{(\\w+)\\}/g, function(m, name){ return (params && params[name] != null) ? String(params[name]) : m; });
}
var OFFER_WORDS = {
  permanent: function(){ return RT('radar.offer.permanent', 'standing price'); },
  promotion: function(){ return RT('radar.offer.promotion', 'temporary promotion'); },
  free_quota: function(){ return RT('radar.offer.free_quota', 'free quota'); },
  trial: function(){ return RT('radar.offer.trial', 'trial'); },
  subsidy: function(){ return RT('radar.offer.subsidy', 'provider subsidy'); },
  unknown: function(){ return RT('radar.offer.unknown', 'offer kind unknown'); },
};
var COMPONENT_LABELS = {
  input_usd_per_m: function(){ return RT('radar.component.input', 'input'); },
  output_usd_per_m: function(){ return RT('radar.component.output', 'output'); },
  cached_input_usd_per_m: function(){ return RT('radar.component.cached', 'cached'); },
};

function pageActions(){ return window.VoolPageActions || null; }
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function money(v){
  if (v === null || v === undefined || v === '') return '\\u2014';
  var n = Number(v);
  if (!isFinite(n)) return '\\u2014';
  return '$' + (n >= 1 ? n.toFixed(2) : n.toFixed(4));
}
function pct(f){
  var n = Number(f);
  if (!isFinite(n)) return '';
  return '\\u2212' + Math.round(n * 100) + '%';
}
function when(iso){
  var t = Date.parse(String(iso || ''));
  if (!isFinite(t)) return String(iso || RT('radar.unknown_time', 'unknown time'));
  return new Date(t).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}

// ---- data --------------------------------------------------------------------------------

function refresh(){
  return fetch('/api/model-radar/feed')
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok) return;
      feed = j;
      paintChip();
      if (!pop.hidden) paintList();
    })
    .catch(function(){});
}
function post(path, body){
  return fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)})
    .then(function(r){ return r.json().then(function(j){ return {status: r.status, j: j}; }); });
}

// Discovery is always reachable from the model selector, with no second badge.
var chip = document.getElementById('discoverModelsBtn');
pop = document.createElement('div');
pop.id = 'mrPop';
pop.hidden = true;
pop.setAttribute('role', 'dialog');
pop.setAttribute('aria-label', RT('radar.aria', 'Discover models'));
document.body.appendChild(pop);
function paintChip(){}

// ---- cards ---------------------------------------------------------------------------------

function priceRow(name, before, after, reduction){
  var lab = (COMPONENT_LABELS[name] || function(){ return name; })();
  if (after === undefined || after === null){
    if (before === undefined || before === null) return '';
    return '<div class="mr-row"><span class="mr-lab">' + esc(lab) + '</span><span class="mr-na">' + RT('radar.price_unpublished', 'price no longer published') + '</span></div>';
  }
  var right = money(before) + ' \\u2192 ' + money(after) + ' /1M';
  var p = (reduction !== undefined && reduction !== null) ? '<span class="mr-pct">' + esc(pct(reduction)) + '</span>' : '';
  return '<div class="mr-row"><span class="mr-lab">' + esc(lab) + '</span><span>' + esc(right) + '</span>' + p + '</span></div>';
}
function cardHtml(f, index){
  var kindLabel = f.kind === 'new_free' ? RT('radar.kind.new_free', 'now genuinely free') : RT('radar.kind.price_cut', 'price cut \\u226550%');
  var kindClass = f.kind === 'new_free' ? 'mr-kind mr-free' : 'mr-kind';
  var prices = ['input_usd_per_m','output_usd_per_m','cached_input_usd_per_m'].map(function(n){
    var hasAfter = f.after && (f.after[n] !== undefined && f.after[n] !== null);
    var hasBefore = f.before && (f.before[n] !== undefined && f.before[n] !== null);
    if (!hasAfter && !hasBefore) return '';
    if (!hasAfter) return priceRow(n, f.before ? f.before[n] : null, null, null);
    var red = (f.reductions && f.reductions[n] !== undefined && f.reductions[n] !== null) ? f.reductions[n] : null;
    return priceRow(n, hasBefore ? f.before[n] : null, f.after[n], red);
  }).join('');
  var ctx = f.context_length ? RTF('radar.ctx_k', '{n}k ctx', { n: Math.round(f.context_length / 1000) }) : RT('radar.ctx_unknown', 'ctx unknown');
  var tools = '<span class="' + (f.supports_tools ? 'mr-ok' : 'mr-no') + '">' + RT('radar.tools', 'tools') + ' ' + (f.supports_tools ? '\\u2713' : '\\u2717') + '</span>';
  var images = '<span class="' + (f.supports_images ? 'mr-ok' : 'mr-no') + '">' + RT('radar.images', 'images') + ' ' + (f.supports_images ? '\\u2713' : '\\u2717') + '</span>';
  var offerFn = OFFER_WORDS[f.offer_kind];
  var offer = offerFn ? offerFn() : (f.offer_kind || 'unknown');
  var expiry = f.expires_at ? ' \\u00b7 ' + RTF('radar.ends', 'ends {time}', { time: when(f.expires_at) }) : '';
  var privacy = f.privacy_terms ? esc(f.privacy_terms) : RT('radar.privacy_unstated', 'data terms not stated by the provider');
  var limits = f.limits_stated ? '<div class="mr-meta"><span>' + RTF('radar.limits', 'limits: {limits}', { limits: f.limits_stated }) + '</span></div>' : '';
  var evUrl = f.evidence_url ? '<a href="' + esc(f.evidence_url) + '" target="_blank" rel="noopener">' + RT('radar.source', 'source') + '</a>' : RT('radar.no_source_url', 'no source URL recorded');
  return '<div class="mr-card" data-mr="' + index + '">' +
    '<div class="mr-title"><b>' + esc(f.display_name || f.model_id) + '</b>' +
    '<span class="mr-prov">' + esc(f.provider_id) + '</span>' +
    '<span class="' + kindClass + '">' + kindLabel + '</span></div>' +
    '<div class="mr-prices">' + prices + '</div>' +
    '<div class="mr-meta"><span>' + ctx + '</span>' + tools + images +
    '<span>' + esc(offer) + expiry + '</span></div>' +
    limits +
    '<div class="mr-meta"><span>' + privacy + '</span></div>' +
    '<div class="mr-why">' + esc(f.why) + '</div>' +
    '<div class="mr-ev">' + RTF('radar.evidence', 'evidence {time}', { time: when(f.evidence_fetched_at) }) + ' \\u00b7 ' + evUrl +
    ' \\u00b7 ' + RTF('radar.first_seen', 'first seen {time}', { time: when(f.created_at) }) + '</div>' +
    '<div class="mr-actions">' +
    '<button type="button" class="mr-btn mr-primary" data-mr-act="try">' + RT('radar.try_once', 'Try once') + '</button>' +
    '<button type="button" class="mr-btn" data-mr-act="pin">' + RT('radar.set_default', 'Set as default') + '</button>' +
    '<button type="button" class="mr-btn mr-ghost" data-mr-act="dismiss">' + RT('radar.hide_offer', 'Hide offer') + '</button>' +
    '</div><div class="mr-status" data-mr-status hidden></div></div>';
}

// ---- preferences -----------------------------------------------------------------------------

function prefsHtml(){
  var p = feed.preferences || {};
  var providers = Array.isArray(p.providers) ? p.providers.join(', ') : '';
  var caps = Array.isArray(p.required_capabilities) ? p.required_capabilities : [];
  var interval = Number(p.min_interval_hours || 0);
  return '<div class="mr-prefs" id="mrPrefs"' + (prefsOpen ? '' : ' hidden') + '>' +
    '<label><input type="checkbox" id="mrPEnabled"' + (p.enabled !== false ? ' checked' : '') + '> ' + RT('radar.prefs_enabled', 'Radar enabled') + '</label>' +
    '<div><label for="mrPProviders">' + RT('radar.prefs_providers', 'Providers to watch') + '</label>' +
    '<input type="text" id="mrPProviders" placeholder="' + RT('radar.prefs_providers_ph', 'empty = every provider') + '" value="' + esc(providers) + '">' +
    '<div class="mr-hint">' + RT('radar.prefs_providers_hint', 'comma-separated provider ids, e.g. openrouter') + '</div></div>' +
    '<div><label for="mrPLane">' + RT('radar.prefs_lane', 'Lane') + '</label> <select id="mrPLane">' +
    ['any','cloud','local'].map(function(l){ return '<option value="' + l + '"' + ((p.lane || 'any') === l ? ' selected' : '') + '>' + RT('radar.lane.' + l, l) + '</option>'; }).join('') +
    '</select></div>' +
    '<div><label><input type="checkbox" id="mrPCapTools"' + (caps.indexOf('tools') >= 0 ? ' checked' : '') + '> ' + RT('radar.prefs_tools_only', 'only suggest models with tool support') + '</label>' +
    '<label><input type="checkbox" id="mrPCapImages"' + (caps.indexOf('images') >= 0 ? ' checked' : '') + '> ' + RT('radar.prefs_images', 'only suggest models with image support') + '</label></div>' +
    '<div><label for="mrPFreq">' + RT('radar.prefs_notify', 'Notify me') + '</label> <select id="mrPFreq">' +
    '<option value="0"' + (interval === 0 ? ' selected' : '') + '>' + RT('radar.freq.as_it_happens', 'as it happens') + '</option>' +
    '<option value="24"' + (interval === 24 ? ' selected' : '') + '>' + RT('radar.freq.daily', 'at most daily per model') + '</option>' +
    '<option value="168"' + (interval === 168 ? ' selected' : '') + '>' + RT('radar.freq.weekly', 'at most weekly per model') + '</option>' +
    '</select></div>' +
    '<button type="button" class="mr-btn mr-primary" id="mrPSave">' + RT('radar.prefs_save', 'Save preferences') + '</button>' +
    '<div class="mr-status" id="mrPStatus" hidden></div></div>';
}

function paintList(){
  var rows = (feed.findings || []).map(function(f, i){ return cardHtml(f, i); }).join('');
  var conflicts = Number(feed.open_conflicts || 0);
  pop.innerHTML = '<div class="mr-head">' + RT('radar.head', 'Discover models \\u00b7 prices and evidence') +
    '<button type="button" class="mr-prefs-btn" id="mrPrefsBtn">' + RT('radar.preferences_btn', 'Preferences') + '</button></div>' +
    (rows || '<div class="mr-empty">' + RT('radar.empty', 'Nothing qualified yet. The radar only speaks when a model becomes genuinely free under stated limits, or the same provider cuts a recorded price by half or more \\u2014 with fresh evidence. Quiet is the design, not an outage.') + '</div>') +
    prefsHtml() +
    '<div class="mr-foot">' + RT('radar.foot', 'No auto-switching, ever. \\u201cTry once\\u201d runs one turn; \\u201cSet as default\\u201d goes through the ordinary model gate.') +
    (conflicts ? ' ' + RTF('radar.foot_conflicts', '{n} unresolved provider feed conflict(s) \\u2014 those models stay silent.', { n: conflicts }) : '') + '</div>';
  var prefsBtn = pop.querySelector('#mrPrefsBtn');
  if (prefsBtn) prefsBtn.addEventListener('click', function(){
    prefsOpen = !prefsOpen;
    var el = pop.querySelector('#mrPrefs');
    if (el) el.hidden = !prefsOpen;
  });
  var save = pop.querySelector('#mrPSave');
  if (save) save.addEventListener('click', savePrefs);
}

function savePrefs(){
  var caps = [];
  if (pop.querySelector('#mrPCapTools') && pop.querySelector('#mrPCapTools').checked) caps.push('tools');
  if (pop.querySelector('#mrPCapImages') && pop.querySelector('#mrPCapImages').checked) caps.push('images');
  var providers = String((pop.querySelector('#mrPProviders') || {}).value || '').split(',')
    .map(function(s){ return s.trim().toLowerCase(); }).filter(Boolean);
  post('/api/model-radar/preferences', {
    enabled: !!(pop.querySelector('#mrPEnabled') && pop.querySelector('#mrPEnabled').checked),
    providers: providers,
    lane: String((pop.querySelector('#mrPLane') || {}).value || 'any'),
    required_capabilities: caps,
    min_interval_hours: Number((pop.querySelector('#mrPFreq') || {}).value || 0),
  }).then(function(r){
    var el = pop.querySelector('#mrPStatus');
    if (el) { el.hidden = false; el.textContent = (r.j && r.j.ok) ? RT('radar.saved', 'Saved.') : RTF('radar.save_failed', 'Could not save: {error}', { error: ((r.j && r.j.error) || r.status) }); }
    refresh();
  }).catch(function(){});
}

// ---- actions -----------------------------------------------------------------------------------

function statusFor(card, text, err){
  var el = card.querySelector('[data-mr-status]');
  if (el) { el.hidden = false; el.textContent = text; if (err) el.classList.add('mr-err'); }
}
function findingFor(card){
  return feed.findings[Number(card.getAttribute('data-mr'))];
}
function act(f, action, card){
  if (action === 'dismiss') {
    post('/api/model-radar/dismiss', {fingerprint: f.fingerprint}).then(function(r){
      if (r.j && r.j.ok) {
        feed.findings = (feed.findings || []).filter(function(x){ return x.fingerprint !== f.fingerprint; });
        feed.unread = Math.max(0, Number(feed.unread || 0) - 1);  // optimistic; server truth lands on next poll
        paintChip();
        paintList();
        refresh();
      }
      else statusFor(card, RTF('radar.dismiss_failed', 'Could not dismiss: {error}', { error: ((r.j && r.j.error) || r.status) }), true);
    }).catch(function(){ statusFor(card, RT('radar.unreachable', 'Could not reach the radar.'), true); });
    return;
  }
  var p = pageActions();
  if (!p) { statusFor(card, RT('radar.actions_unavailable', 'Page actions unavailable.'), true); return; }
  if (action === 'try') {
    var chatId = p.displayedChat ? p.displayedChat() : '';
    post('/api/model-radar/try-once', {fingerprint: f.fingerprint, session_id: String(chatId || '')}).then(function(r){
      if (!(r.j && r.j.ok)) { statusFor(card, RTF('radar.try_refused', 'Try-once refused: {error}', { error: ((r.j && r.j.error) || r.status) }), true); return; }
      var armed = p.tryModelOnce(r.j.model_id, r.j.display_name || f.display_name);
      statusFor(card, armed
        ? RTF('radar.armed', 'Armed: your next message runs \\u201c{model}\\u201d for one turn, then this chat reverts on its own.', { model: (r.j.display_name || f.model_id) })
        : RT('radar.arm_failed', 'Could not arm the trial in this chat.'), !armed);
    }).catch(function(){ statusFor(card, RT('radar.unreachable', 'Could not reach the radar.'), true); });
    return;
  }
  if (action === 'pin') {
    var id = (f.provider_id && f.provider_id !== 'openrouter') ? f.provider_id + ':' + f.model_id : f.model_id;
    statusFor(card, RT('radar.asking_gate', 'Asking the model gate\\u2026'), false);
    Promise.resolve(p.pinCloudModel(id, f.display_name || f.model_id)).then(function(ok){
      statusFor(card, ok ? RTF('radar.default_set', 'Default set to \\u201c{model}\\u201d through the ordinary model gate.', { model: (f.display_name || f.model_id) })
                         : RT('radar.pin_refused', 'The model gate refused this pin (paid confirmation or catalog check) \\u2014 nothing changed.'), !ok);
    }).catch(function(){ statusFor(card, RT('radar.pin_failed', 'Pin failed.'), true); });
  }
}

// ---- surface -------------------------------------------------------------------------------------

function openPop(fingerprint){
  paintList();
  pop.hidden = false;
  paintChip();
  var fingerprints = (feed.findings || []).filter(function(f){ return !fingerprint || f.fingerprint === fingerprint; }).map(function(f){ return String(f.fingerprint || ''); }).filter(Boolean);
  if (fingerprints.length) {
    post('/api/model-radar/viewed', {fingerprints: fingerprints.slice(0, 100)}).then(function(){ refresh(); }).catch(function(){});
  }
  var anchor = document.getElementById('modelBtn') || chip;
  var r = anchor.getBoundingClientRect();
  pop.style.top = (r.bottom + 6) + 'px';
  pop.style.left = Math.max(8, Math.min(r.right - 420, window.innerWidth - 428)) + 'px';
}
if (chip) chip.addEventListener('click', function(ev){ ev.stopPropagation(); var menu = document.getElementById('modelPop'); if (menu) menu.classList.remove('open'); var modelBtn = document.getElementById('modelBtn'); if (modelBtn) modelBtn.setAttribute('aria-expanded', 'false'); refresh().then(function(){ openPop(); }); });
pop.addEventListener('click', function(ev){
  var btn = ev.target.closest('[data-mr-act]');
  if (!btn) return;
  var card = btn.closest('[data-mr]');
  var f = card && findingFor(card);
  if (f) act(f, btn.getAttribute('data-mr-act'), card);
});
document.addEventListener('mousedown', function(ev){
  if (!pop.hidden && !pop.contains(ev.target) && ev.target !== chip && !(chip && chip.contains(ev.target))) pop.hidden = true;
});

document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape') pop.hidden = true; });

function mount(){
  refresh();
  setInterval(refresh, 60000);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
else mount();

window.VoolModelRadar = Object.freeze({
  open: function(fingerprint){ return refresh().then(function(){ openPop(fingerprint); }); },
  refresh: refresh,
  unread: function(){ return Number(feed.unread || 0); },
  pending: function(){ return (feed.findings || []).length; },
});
})();
"""


def render_model_radar_fragment() -> str:
    """The model discovery cards and preferences as an appended fragment."""
    return "<style>" + _MR_CSS + "</style><script>" + _MR_JS + "</script>"


__all__ = ["render_model_radar_fragment"]
