"""Settings panel: calendar accounts (the Calendars group).

Add a calendar source (Google Calendar, Microsoft 365, an iCloud or other CalDAV server, Apple Calendar on this Mac) with
the capability each path really has, pick its credential by binding id (never a password or token), refresh the calendar
list, choose which calendars VOOL reads and which one takes new events, opt in to sync and alerts, set the default lead
time, sync now, disconnect and reconnect. Every state an attempt proved is shown with its recovery step.

The panel only calls the account routes (core/web/api/service.py -> core/operator/calendar_accounts.py) and renders what
they return; calendar names and account labels are provider data and are always written as text, never as markup.
Namespace ``window.VoolCalendarSettings``; prefix ``vcs-``.
"""
from __future__ import annotations

_CSS = """
.vcs-root{display:flex;flex-direction:column;gap:10px}
.vcs-note,.vcs-detail{font-size:12px;color:var(--muted,#9aa1af)}
.vcs-status{font-size:12px;min-height:16px;color:var(--muted,#9aa1af)}
.vcs-bad{color:var(--bad,#f2545b)}
.vcs-card,.vcs-add{border:1px solid var(--border,#262b35);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.vcs-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.vcs-chip{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--border,#262b35);color:var(--muted,#9aa1af)}
.vcs-connected{color:var(--ok,#3ecf8e);border-color:var(--ok,#3ecf8e)}
.vcs-revoked,.vcs-denied,.vcs-unreadable,.vcs-provider_error,.vcs-unsupported,.vcs-egress_refused{color:var(--bad,#f2545b);border-color:var(--bad,#f2545b)}
.vcs-recovery{font-size:12px;color:var(--ink,#e8eaf0)}
.vcs-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
.vcs-cals{display:flex;flex-direction:column;gap:4px}
.vcs-cal{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
.vcs-gone{opacity:.6}
.vcs-toggle{display:inline-flex;align-items:center;gap:6px;font-size:13px}
.vcs-input{font-size:13px}
.vcs-title{font-weight:600;font-size:13px}
.vcs-form{display:flex;flex-direction:column;gap:8px}
.vcs-field{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
.vcs-field[hidden]{display:none}
.vcs-empty{font-size:12px;color:var(--muted,#9aa1af);padding:4px 0}
"""

_JS = r"""
(function(){
if (window.VoolCalendarSettings) return;
// Strings resolve through the page's i18n catalog (VOOLT, defined before this fragment
// loads); the fallback literals are the English catalog source. Calendar names, account
// labels, provider names and ids are PROVIDER DATA and always pass through untranslated.
function CT(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
function CTF(key, fallback, params){
  try {
    if (typeof VOOLT === 'function' && typeof VOOLFMT === 'function') {
      var t = VOOLT(key);
      if (t && t !== key) return VOOLFMT(t, params || {});
    }
  } catch (e) {}
  return fallback;
}
function stateText(status, raw){
  var key = {configured: 'vcs.state.configured', connected: 'vcs.state.connected', stale: 'vcs.state.stale',
             revoked: 'vcs.state.revoked', denied: 'vcs.state.denied', rate_limited: 'vcs.state.rate_limited',
             offline: 'vcs.state.offline', egress_refused: 'vcs.state.egress_refused', unreadable: 'vcs.state.unreadable',
             calendar_missing: 'vcs.state.calendar_missing', unsupported: 'vcs.state.unsupported',
             provider_error: 'vcs.state.provider_error', disconnected: 'vcs.state.disconnected'}[status];
  return key ? CT(key, raw) : raw;
}
function h(tag, cls, text){ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function request(method, url, body){
  var options = {method: method, headers: {'Accept': 'application/json'}};
  if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  return fetch(url, options).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){ j = j || {}; if (j.ok === undefined) j.ok = r.ok; j.http_status = r.status; return j; });
  });
}
function button(text, onClick){ var b = h('button', 'btn vcs-btn', text); b.type = 'button'; b.addEventListener('click', onClick); return b; }
function toggle(text, on, onChange){
  var label = h('label', 'vcs-toggle'); var box = h('input'); box.type = 'checkbox'; box.checked = !!on;
  if (onChange) box.addEventListener('change', function(){ onChange(box.checked); });
  label.appendChild(box); label.appendChild(h('span', null, text)); return label;
}
function parseMinutes(raw){
  var parts = String(raw || '').split(',').map(function(p){ return p.trim(); }).filter(Boolean);
  var values = parts.map(Number);
  if (values.some(function(v){ return !isFinite(v) || v < 0 || Math.floor(v) !== v; })) return null;
  return values;
}
function mountInto(host){
  host.textContent = '';
  var root = h('div', 'vcs-root');
  host.appendChild(root);
  root.appendChild(h('div', 'vcs-note', CT('vcs.note.intro', 'VOOL reads only the calendars you choose, and only after you turn sync on. A credential is chosen by its binding; no password or token is typed or shown here.')));
  var status = h('div', 'vcs-status', CT('vcs.status.loading', 'Loading calendar accounts…'));
  var list = h('div', 'vcs-list');
  var addBox = h('div', 'vcs-add');
  root.appendChild(status); root.appendChild(list); root.appendChild(addBox);
  var view = null;
  function say(text, bad){ status.textContent = text || ''; status.className = 'vcs-status' + (bad ? ' vcs-bad' : ''); }
  function load(){
    return request('GET', '/api/calendar/accounts').then(function(j){
      if (!j.ok) { say(CTF('vcs.status.unavailable', 'Calendar accounts are unavailable: {reason}', {reason: (j.error || j.detail || ('HTTP ' + j.http_status))}), true); return; }
      view = j; paint();
    }).catch(function(){ say(CT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function act(url, body, done){
    say(CT('vns.status.working', 'Working…'));
    return request('POST', url, body).then(function(j){
      return load().then(function(){
        if (!j.ok) say(j.detail || j.recovery || j.reason || CT('vns.status.failed', 'That did not work.'), true);
        else say(done || CT('vcs.status.done', 'Done.'));
        return j;
      });
    }).catch(function(){ say(CT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function paint(){
    list.textContent = '';
    var accounts = view.accounts || [];
    if (!accounts.length) list.appendChild(h('div', 'vcs-empty', CT('vcs.empty.accounts', 'No calendar accounts yet. Add one below.')));
    accounts.forEach(function(account){ list.appendChild(accountCard(account)); });
    paintAdd();
  }
  function accountCard(account){
    var capability = account.capability || {};
    var card = h('div', 'vcs-card');
    var head = h('div', 'vcs-head');
    head.appendChild(h('strong', null, account.label || capability.label || account.provider));
    head.appendChild(h('span', 'vcs-chip vcs-' + account.status, stateText(account.status, account.status)));
    card.appendChild(head);
    card.appendChild(h('div', 'vcs-detail', (capability.label || account.provider) + (account.credential_binding ? ' · ' + CT('vcs.detail.binding', 'credential binding {binding}', {binding: account.credential_binding}) : '')));
    if (account.recovery) card.appendChild(h('div', 'vcs-recovery', account.recovery));
    if (account.status_detail) card.appendChild(h('div', 'vcs-detail', account.status_detail));
    if (account.status === 'disconnected') {
      card.appendChild(button(CT('vcs.btn.reconnect', 'Reconnect'), function(){ act('/api/calendar/accounts/reconnect', {account_id: account.account_id}, CT('vcs.saved.reconnected', 'Reconnected. Choose calendars and turn sync on to fetch again.')); }));
      return card;
    }
    var toggles = h('div', 'vcs-row');
    toggles.appendChild(toggle(CT('vcs.toggle.sync', 'Sync this account'), account.sync_enabled, function(on){
      act('/api/calendar/accounts/opt-in', {account_id: account.account_id, sync_enabled: on}, on ? CT('vcs.saved.sync_on', 'Sync is on.') : CT('vcs.saved.sync_off', 'Sync is off.'));
    }));
    toggles.appendChild(toggle(CT('vcs.toggle.alerts', 'Alerts from this account'), account.alerts_enabled, function(on){
      act('/api/calendar/accounts/opt-in', {account_id: account.account_id, alerts_enabled: on}, on ? CT('vcs.saved.alerts_on', 'Alerts are on.') : CT('vcs.saved.alerts_off', 'Alerts are off; pending alerts from this account were cancelled.'));
    }));
    card.appendChild(toggles);
    var lead = h('div', 'vcs-row');
    lead.appendChild(h('span', null, CT('vcs.lead.label', 'Alert')));
    var leadInput = h('input', 'vcs-input');
    leadInput.size = 8;
    leadInput.value = (account.default_lead_minutes || []).join(', ');
    leadInput.setAttribute('aria-label', CT('vcs.lead.aria', 'Minutes before each event, separated by commas'));
    lead.appendChild(leadInput);
    lead.appendChild(h('span', null, CT('vcs.lead.after', 'minutes before each event')));
    lead.appendChild(button(CT('vns.btn.save', 'Save'), function(){
      var values = parseMinutes(leadInput.value);
      if (values === null) { say(CT('vcs.lead.invalid', 'Enter whole minutes, for example 15 or 30, 5; leave it empty for no alerts.'), true); return; }
      act('/api/calendar/alerts/policy', {account_id: account.account_id, lead_minutes: values, apply_now: true},
          values.length ? CTF('vcs.saved.lead_set', 'Alerts: {minutes} min before each event.', {minutes: values.join(', ')}) : CT('vcs.saved.lead_none', 'No alerts by default for this account.'));
    }));
    card.appendChild(lead);
    var calendars = account.calendars || [];
    var table = h('div', 'vcs-cals');
    if (!calendars.length) table.appendChild(h('div', 'vcs-empty', CT('vcs.empty.calendars', 'No calendars read yet. Refresh the calendar list.')));
    calendars.forEach(function(calendar){ table.appendChild(calendarRow(account, calendar)); });
    card.appendChild(table);
    var actions = h('div', 'vcs-row');
    actions.appendChild(button(CT('vcs.btn.refresh', 'Refresh calendar list'), function(){ act('/api/calendar/accounts/discover', {account_id: account.account_id}, CT('vcs.saved.refreshed', 'Calendar list refreshed.')); }));
    actions.appendChild(button(CT('vcs.btn.sync_now', 'Sync now'), function(){ act('/api/calendar/sync', {account_id: account.account_id}, CT('vcs.saved.synced', 'Synced.')); }));
    actions.appendChild(button(CT('vcs.btn.disconnect', 'Disconnect'), function(){
      act('/api/calendar/accounts/disconnect', {account_id: account.account_id}, CT('vcs.saved.disconnected', 'Disconnected. Pending alerts from this account were cancelled; alerts already shown stay in the bell.'));
    }));
    card.appendChild(actions);
    if (account.last_sync_ok_at) card.appendChild(h('div', 'vcs-detail', CTF('vcs.detail.last_sync', 'Last good sync: {when}', {when: new Date(account.last_sync_ok_at).toLocaleString()})));
    return card;
  }
  function calendarRow(account, calendar){
    var row = h('div', 'vcs-cal' + (calendar.present ? '' : ' vcs-gone'));
    row.appendChild(toggle(calendar.display_name, calendar.selected, function(on){
      act('/api/calendar/accounts/select', {account_id: account.account_id, calendar_id: calendar.calendar_id, selected: on},
          on ? CTF('vcs.saved.reads', 'VOOL reads {name}.', {name: calendar.display_name})
             : CTF('vcs.saved.unreads', 'VOOL no longer reads {name}; its pending alerts were cancelled.', {name: calendar.display_name}));
    }));
    var access = calendar.can_write === true ? CT('vcs.access.write', 'can write') : calendar.can_write === false ? CT('vcs.access.read_only', 'read-only') : CT('vcs.access.unknown', 'write access not stated');
    row.appendChild(h('span', 'vcs-detail', access + (calendar.provider_default ? ' · ' + CT('vcs.detail.provider_default', 'provider default') : '') + (calendar.present ? '' : ' · ' + CT('vcs.detail.gone', 'no longer on the account'))));
    if (calendar.is_default_write) row.appendChild(h('span', 'vcs-chip vcs-connected', CT('vcs.chip.default_write', 'New events go here')));
    else if (calendar.can_write !== false && calendar.present) {
      row.appendChild(button(CT('vcs.btn.default_write', 'Use for new events'), function(){
        act('/api/calendar/accounts/select', {account_id: account.account_id, calendar_id: calendar.calendar_id, selected: true, default_write: true},
            CTF('vcs.saved.default_write', 'New events go to {name}.', {name: calendar.display_name}));
      }));
    }
    return row;
  }
  function paintAdd(){
    addBox.textContent = '';
    addBox.appendChild(h('div', 'vcs-title', CT('vcs.add.title', 'Add a calendar account')));
    var providers = view.providers || {};
    var form = h('form', 'vcs-form');
    var sourceRow = h('label', 'vcs-field'); sourceRow.appendChild(h('span', null, CT('vcs.add.source', 'Source')));
    var providerSelect = h('select', 'vcs-input'); providerSelect.setAttribute('aria-label', CT('vcs.add.source_aria', 'Calendar source'));
    Object.keys(providers).forEach(function(key){ var option = h('option', null, providers[key].label); option.value = key; providerSelect.appendChild(option); });
    sourceRow.appendChild(providerSelect);
    var capability = h('div', 'vcs-detail');
    var nameRow = h('label', 'vcs-field'); nameRow.appendChild(h('span', null, CT('vcs.add.name', 'Name in VOOL')));
    var nameInput = h('input', 'vcs-input'); nameInput.setAttribute('aria-label', CT('vcs.add.name_aria', 'Account name shown in VOOL')); nameRow.appendChild(nameInput);
    var addressRow = h('label', 'vcs-field'); addressRow.appendChild(h('span', null, CT('vcs.add.address', 'Server address')));
    var addressInput = h('input', 'vcs-input'); addressInput.setAttribute('aria-label', CT('vcs.add.address_aria', 'CalDAV server address')); addressRow.appendChild(addressInput);
    var bindingRow = h('label', 'vcs-field'); bindingRow.appendChild(h('span', null, CT('vcs.add.binding', 'Credential binding')));
    var bindingSelect = h('select', 'vcs-input'); bindingSelect.setAttribute('aria-label', CT('vcs.add.binding_aria', 'Credential binding')); bindingRow.appendChild(bindingSelect);
    function refreshFields(){
      var key = providerSelect.value; var provider = providers[key] || {};
      capability.textContent = CTF('vcs.add.capability', 'Connects with {connection}. Reads: {events}. Links: {links}.', {connection: (provider.connection || ''), events: (provider.events || ''), links: (provider.links || '')});
      addressRow.hidden = key !== 'caldav';
      bindingRow.hidden = key === 'eventkit';
      bindingSelect.textContent = '';
      var none = h('option', null, key === 'caldav' ? CT('vcs.add.binding_none_caldav', 'No credential (a server without sign-in)') : CT('vcs.add.binding_none', 'Choose a verified binding'));
      none.value = ''; bindingSelect.appendChild(none);
      (view.bindings || []).forEach(function(binding){
        var option = h('option', null, binding.binding_id + ' — ' + (binding.provider_label || binding.provider_id) + (binding.account ? ' (' + binding.account + ')' : '') + (binding.status === 'verified' ? '' : ' · ' + binding.status));
        option.value = binding.binding_id; option.disabled = binding.status !== 'verified'; bindingSelect.appendChild(option);
      });
    }
    providerSelect.addEventListener('change', refreshFields);
    var submit = h('button', 'btn vcs-btn', CT('vcs.btn.add', 'Add account')); submit.type = 'submit';
    [sourceRow, capability, nameRow, addressRow, bindingRow, submit].forEach(function(node){ form.appendChild(node); });
    form.addEventListener('submit', function(ev){
      ev.preventDefault();
      var body = {provider: providerSelect.value, label: nameInput.value.trim(), base_url: addressRow.hidden ? '' : addressInput.value.trim(),
                  auth_binding: bindingRow.hidden ? '' : bindingSelect.value};
      say(CT('vcs.status.adding', 'Adding…'));
      request('POST', '/api/calendar/accounts/add', body).then(function(j){
        if (!j.ok) { say(j.detail || j.reason || CT('vcs.status.not_added', 'The account was not added.'), true); return; }
        return act('/api/calendar/accounts/discover', {account_id: j.account_id}, CT('vcs.saved.added', 'Added. Choose the calendars VOOL should read, then turn sync on.'));
      }).catch(function(){ say(CT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
    });
    addBox.appendChild(form);
    if (view.bindings_error) addBox.appendChild(h('div', 'vcs-bad', CTF('vcs.bindings_error', 'The credential binding list could not be read ({error}).', {error: view.bindings_error})));
    else if (!(view.bindings || []).length) addBox.appendChild(h('div', 'vcs-detail', CT('vcs.no_bindings', 'No credential bindings yet. Google and Microsoft accounts need a verified binding: add and verify the account credential under Keys first.')));
    refreshFields();
  }
  load();
  return true;
}
window.VoolCalendarSettings = Object.freeze({mountInto: mountInto});
})();
"""


def render_calendar_settings_fragment() -> str:
    """The calendar accounts panel, mounted by the Settings page's Calendars group."""
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>"


__all__ = ["render_calendar_settings_fragment"]
