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
var STATE_TEXT = {
  configured: 'Added: choose calendars', connected: 'Connected', stale: 'Not refreshed recently',
  revoked: 'Credential no longer accepted', denied: 'Access refused', rate_limited: 'Slowed down by the provider',
  offline: 'Provider unreachable', egress_refused: 'Network access is off', unreadable: 'Unreadable reply',
  calendar_missing: 'A chosen calendar is gone', unsupported: 'Not available here', provider_error: 'Provider error',
  disconnected: 'Disconnected'
};
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
  root.appendChild(h('div', 'vcs-note', 'VOOL reads only the calendars you choose, and only after you turn sync on. A credential is chosen by its binding; no password or token is typed or shown here.'));
  var status = h('div', 'vcs-status', 'Loading calendar accounts…');
  var list = h('div', 'vcs-list');
  var addBox = h('div', 'vcs-add');
  root.appendChild(status); root.appendChild(list); root.appendChild(addBox);
  var view = null;
  function say(text, bad){ status.textContent = text || ''; status.className = 'vcs-status' + (bad ? ' vcs-bad' : ''); }
  function load(){
    return request('GET', '/api/calendar/accounts').then(function(j){
      if (!j.ok) { say('Calendar accounts are unavailable: ' + (j.error || j.detail || ('HTTP ' + j.http_status)), true); return; }
      view = j; paint();
    }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
  }
  function act(url, body, done){
    say('Working…');
    return request('POST', url, body).then(function(j){
      return load().then(function(){
        if (!j.ok) say(j.detail || j.recovery || j.reason || 'That did not work.', true);
        else say(done || 'Done.');
        return j;
      });
    }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
  }
  function paint(){
    list.textContent = '';
    var accounts = view.accounts || [];
    if (!accounts.length) list.appendChild(h('div', 'vcs-empty', 'No calendar accounts yet. Add one below.'));
    accounts.forEach(function(account){ list.appendChild(accountCard(account)); });
    paintAdd();
  }
  function accountCard(account){
    var capability = account.capability || {};
    var card = h('div', 'vcs-card');
    var head = h('div', 'vcs-head');
    head.appendChild(h('strong', null, account.label || capability.label || account.provider));
    head.appendChild(h('span', 'vcs-chip vcs-' + account.status, STATE_TEXT[account.status] || account.status));
    card.appendChild(head);
    card.appendChild(h('div', 'vcs-detail', (capability.label || account.provider) + (account.credential_binding ? ' · credential binding ' + account.credential_binding : '')));
    if (account.recovery) card.appendChild(h('div', 'vcs-recovery', account.recovery));
    if (account.status_detail) card.appendChild(h('div', 'vcs-detail', account.status_detail));
    if (account.status === 'disconnected') {
      card.appendChild(button('Reconnect', function(){ act('/api/calendar/accounts/reconnect', {account_id: account.account_id}, 'Reconnected. Choose calendars and turn sync on to fetch again.'); }));
      return card;
    }
    var toggles = h('div', 'vcs-row');
    toggles.appendChild(toggle('Sync this account', account.sync_enabled, function(on){
      act('/api/calendar/accounts/opt-in', {account_id: account.account_id, sync_enabled: on}, on ? 'Sync is on.' : 'Sync is off.');
    }));
    toggles.appendChild(toggle('Alerts from this account', account.alerts_enabled, function(on){
      act('/api/calendar/accounts/opt-in', {account_id: account.account_id, alerts_enabled: on}, on ? 'Alerts are on.' : 'Alerts are off; pending alerts from this account were cancelled.');
    }));
    card.appendChild(toggles);
    var lead = h('div', 'vcs-row');
    lead.appendChild(h('span', null, 'Alert'));
    var leadInput = h('input', 'vcs-input');
    leadInput.size = 8;
    leadInput.value = (account.default_lead_minutes || []).join(', ');
    leadInput.setAttribute('aria-label', 'Minutes before each event, separated by commas');
    lead.appendChild(leadInput);
    lead.appendChild(h('span', null, 'minutes before each event'));
    lead.appendChild(button('Save', function(){
      var values = parseMinutes(leadInput.value);
      if (values === null) { say('Enter whole minutes, for example 15 or 30, 5; leave it empty for no alerts.', true); return; }
      act('/api/calendar/alerts/policy', {account_id: account.account_id, lead_minutes: values, apply_now: true},
          values.length ? 'Alerts: ' + values.join(', ') + ' min before each event.' : 'No alerts by default for this account.');
    }));
    card.appendChild(lead);
    var calendars = account.calendars || [];
    var table = h('div', 'vcs-cals');
    if (!calendars.length) table.appendChild(h('div', 'vcs-empty', 'No calendars read yet. Refresh the calendar list.'));
    calendars.forEach(function(calendar){ table.appendChild(calendarRow(account, calendar)); });
    card.appendChild(table);
    var actions = h('div', 'vcs-row');
    actions.appendChild(button('Refresh calendar list', function(){ act('/api/calendar/accounts/discover', {account_id: account.account_id}, 'Calendar list refreshed.'); }));
    actions.appendChild(button('Sync now', function(){ act('/api/calendar/sync', {account_id: account.account_id}, 'Synced.'); }));
    actions.appendChild(button('Disconnect', function(){
      act('/api/calendar/accounts/disconnect', {account_id: account.account_id}, 'Disconnected. Pending alerts from this account were cancelled; alerts already shown stay in the bell.');
    }));
    card.appendChild(actions);
    if (account.last_sync_ok_at) card.appendChild(h('div', 'vcs-detail', 'Last good sync: ' + new Date(account.last_sync_ok_at).toLocaleString()));
    return card;
  }
  function calendarRow(account, calendar){
    var row = h('div', 'vcs-cal' + (calendar.present ? '' : ' vcs-gone'));
    row.appendChild(toggle(calendar.display_name, calendar.selected, function(on){
      act('/api/calendar/accounts/select', {account_id: account.account_id, calendar_id: calendar.calendar_id, selected: on},
          on ? 'VOOL reads ' + calendar.display_name + '.' : 'VOOL no longer reads ' + calendar.display_name + '; its pending alerts were cancelled.');
    }));
    var access = calendar.can_write === true ? 'can write' : calendar.can_write === false ? 'read-only' : 'write access not stated';
    row.appendChild(h('span', 'vcs-detail', access + (calendar.provider_default ? ' · provider default' : '') + (calendar.present ? '' : ' · no longer on the account')));
    if (calendar.is_default_write) row.appendChild(h('span', 'vcs-chip vcs-connected', 'New events go here'));
    else if (calendar.can_write !== false && calendar.present) {
      row.appendChild(button('Use for new events', function(){
        act('/api/calendar/accounts/select', {account_id: account.account_id, calendar_id: calendar.calendar_id, selected: true, default_write: true},
            'New events go to ' + calendar.display_name + '.');
      }));
    }
    return row;
  }
  function paintAdd(){
    addBox.textContent = '';
    addBox.appendChild(h('div', 'vcs-title', 'Add a calendar account'));
    var providers = view.providers || {};
    var form = h('form', 'vcs-form');
    var sourceRow = h('label', 'vcs-field'); sourceRow.appendChild(h('span', null, 'Source'));
    var providerSelect = h('select', 'vcs-input'); providerSelect.setAttribute('aria-label', 'Calendar source');
    Object.keys(providers).forEach(function(key){ var option = h('option', null, providers[key].label); option.value = key; providerSelect.appendChild(option); });
    sourceRow.appendChild(providerSelect);
    var capability = h('div', 'vcs-detail');
    var nameRow = h('label', 'vcs-field'); nameRow.appendChild(h('span', null, 'Name in VOOL'));
    var nameInput = h('input', 'vcs-input'); nameInput.setAttribute('aria-label', 'Account name shown in VOOL'); nameRow.appendChild(nameInput);
    var addressRow = h('label', 'vcs-field'); addressRow.appendChild(h('span', null, 'Server address'));
    var addressInput = h('input', 'vcs-input'); addressInput.setAttribute('aria-label', 'CalDAV server address'); addressRow.appendChild(addressInput);
    var bindingRow = h('label', 'vcs-field'); bindingRow.appendChild(h('span', null, 'Credential binding'));
    var bindingSelect = h('select', 'vcs-input'); bindingSelect.setAttribute('aria-label', 'Credential binding'); bindingRow.appendChild(bindingSelect);
    function refreshFields(){
      var key = providerSelect.value; var provider = providers[key] || {};
      capability.textContent = 'Connects with ' + (provider.connection || '') + '. Reads: ' + (provider.events || '') + '. Links: ' + (provider.links || '') + '.';
      addressRow.hidden = key !== 'caldav';
      bindingRow.hidden = key === 'eventkit';
      bindingSelect.textContent = '';
      var none = h('option', null, key === 'caldav' ? 'No credential (a server without sign-in)' : 'Choose a verified binding');
      none.value = ''; bindingSelect.appendChild(none);
      (view.bindings || []).forEach(function(binding){
        var option = h('option', null, binding.binding_id + ' — ' + (binding.provider_label || binding.provider_id) + (binding.account ? ' (' + binding.account + ')' : '') + (binding.status === 'verified' ? '' : ' · ' + binding.status));
        option.value = binding.binding_id; option.disabled = binding.status !== 'verified'; bindingSelect.appendChild(option);
      });
    }
    providerSelect.addEventListener('change', refreshFields);
    var submit = h('button', 'btn vcs-btn', 'Add account'); submit.type = 'submit';
    [sourceRow, capability, nameRow, addressRow, bindingRow, submit].forEach(function(node){ form.appendChild(node); });
    form.addEventListener('submit', function(ev){
      ev.preventDefault();
      var body = {provider: providerSelect.value, label: nameInput.value.trim(), base_url: addressRow.hidden ? '' : addressInput.value.trim(),
                  auth_binding: bindingRow.hidden ? '' : bindingSelect.value};
      say('Adding…');
      request('POST', '/api/calendar/accounts/add', body).then(function(j){
        if (!j.ok) { say(j.detail || j.reason || 'The account was not added.', true); return; }
        return act('/api/calendar/accounts/discover', {account_id: j.account_id}, 'Added. Choose the calendars VOOL should read, then turn sync on.');
      }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
    });
    addBox.appendChild(form);
    if (view.bindings_error) addBox.appendChild(h('div', 'vcs-bad', 'The credential binding list could not be read (' + view.bindings_error + ').'));
    else if (!(view.bindings || []).length) addBox.appendChild(h('div', 'vcs-detail', 'No credential bindings yet. Google and Microsoft accounts need a verified binding: add and verify the account credential under Keys first.'));
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
