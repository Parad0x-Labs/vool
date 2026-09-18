"""Settings panel: alerts and macOS notifications (the Notifications group).

Turn the macOS channel on or off, see what macOS reported (permission, the bridge's last contact, recent requests with
their separate states), ask macOS for permission, send a test notification, and set quiet hours, sound, what a macOS
notification may show and how late an alert may still be shown. The panel calls only
``/api/notifications/preferences`` and the ``/api/notifications/native/*`` routes (core/operator/native_notifications.py)
and never claims a banner was shown: macOS does not report that.

Namespace ``window.VoolNotificationSettings``; prefix ``vns-``.
"""
from __future__ import annotations

_CSS = """
.vns-root{display:flex;flex-direction:column;gap:10px}
.vns-box{border:1px solid var(--border,#262b35);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.vns-title{font-weight:600;font-size:13px}
.vns-line{font-size:13px;color:var(--ink,#e8eaf0)}
.vns-recovery{font-size:12px;color:var(--ink,#e8eaf0)}
.vns-note,.vns-detail{font-size:12px;color:var(--muted,#9aa1af)}
.vns-status{font-size:12px;min-height:16px;color:var(--muted,#9aa1af)}
.vns-bad{color:var(--bad,#f2545b)}
.vns-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
.vns-toggle{display:inline-flex;align-items:center;gap:6px;font-size:13px}
.vns-list{display:flex;flex-direction:column;gap:2px}
.vns-input{font-size:13px}
"""

_JS = r"""
(function(){
if (window.VoolNotificationSettings) return;
var REQUEST_TEXT = {
  queued: 'handed to macOS', submitted: 'accepted by macOS', listed: 'listed in Notification Center',
  acknowledged: 'answered on the notification', suppressed: 'not sent to macOS', failed: 'macOS refused it',
  withdraw_requested: 'removal asked of macOS', withdrawn: 'removed from macOS'
};
var AUTH_TEXT = {
  authorized: 'allowed', provisional: 'allowed quietly', ephemeral: 'allowed for now',
  denied: 'turned off in System Settings', not_determined: 'not answered yet', unknown: 'not reported yet'
};
var LOCK = [['title_only', 'The title only'], ['full', 'The title and the details'], ['hidden', 'Nothing personal (for example: A calendar alert is due)']];
function h(tag, cls, text){ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function request(method, url, body){
  var options = {method: method, headers: {'Accept': 'application/json'}};
  if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  return fetch(url, options).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){ j = j || {}; if (j.ok === undefined) j.ok = r.ok; j.http_status = r.status; return j; });
  });
}
function button(text, onClick){ var b = h('button', 'btn vns-btn', text); b.type = 'button'; b.addEventListener('click', onClick); return b; }
function toggle(text, on, onChange){
  var label = h('label', 'vns-toggle'); var box = h('input'); box.type = 'checkbox'; box.checked = !!on;
  if (onChange) box.addEventListener('change', function(){ onChange(box.checked); });
  label.appendChild(box); label.appendChild(h('span', null, text)); return label;
}
function mountInto(host){
  host.textContent = '';
  var root = h('div', 'vns-root');
  host.appendChild(root);
  var status = h('div', 'vns-status', 'Loading…');
  var macBox = h('div', 'vns-box');
  var prefBox = h('div', 'vns-box');
  var why = h('div', 'vns-note');
  root.appendChild(status); root.appendChild(macBox); root.appendChild(prefBox); root.appendChild(why);
  var prefs = null, native = null;
  function say(text, bad){ status.textContent = text || ''; status.className = 'vns-status' + (bad ? ' vns-bad' : ''); }
  function load(){
    return Promise.all([request('GET', '/api/notifications/preferences'), request('GET', '/api/notifications/native/status')]).then(function(r){
      if (!r[0].ok || !r[1].ok) { say('Notification settings are unavailable right now.', true); return; }
      prefs = r[0].preferences; native = r[1]; paint();
    }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
  }
  function save(patch, done){
    say('Saving…');
    return request('POST', '/api/notifications/preferences', {preferences: patch}).then(function(j){
      return load().then(function(){ if (!j.ok) say(j.detail || j.reason || 'Not saved.', true); else say(done || 'Saved.'); });
    }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
  }
  function act(url, done){
    say('Working…');
    return request('POST', url, {}).then(function(j){
      return load().then(function(){ if (!j.ok) say(j.detail || j.reason || 'That did not work.', true); else say(done); });
    }).catch(function(){ say('VOOL did not answer; nothing was changed.', true); });
  }
  function paint(){ paintMac(); paintPrefs(); why.textContent = native.explanation || ''; }
  function paintMac(){
    macBox.textContent = '';
    macBox.appendChild(h('div', 'vns-title', 'macOS notifications'));
    macBox.appendChild(toggle('Also send VOOL alerts to macOS notifications', prefs.native_notifications, function(on){
      save({native_notifications: on}, on ? 'On. VOOL asks macOS for permission if macOS has not given it.' : 'Off. What macOS still holds is withdrawn; the bell keeps every alert.');
    }));
    var line;
    if (!prefs.native_notifications) line = 'Off: the bell keeps every alert.';
    else if (!native.connected) line = 'On: waiting for the VOOL app window, which hands notifications to macOS while it runs.';
    else line = 'On: macOS permission is ' + (AUTH_TEXT[native.authorization] || native.authorization) + '.';
    macBox.appendChild(h('div', 'vns-line', line));
    if (native.recovery) macBox.appendChild(h('div', 'vns-recovery', native.recovery));
    var answer = native.authorization_request || {};
    if (answer.detail) macBox.appendChild(h('div', 'vns-detail', 'macOS answered: ' + answer.detail));
    var actions = h('div', 'vns-row');
    var ask = button('Ask macOS for permission', function(){ act('/api/notifications/native/authorize', 'Asked. macOS shows its prompt once; answer it there.'); });
    ask.disabled = !prefs.native_notifications || native.authorization === 'authorized';
    var test = button('Send a test notification', function(){
      act('/api/notifications/native/test', 'Sent to the bell and handed to macOS. VOOL records when macOS accepts it; it cannot see whether a banner appeared.');
    });
    test.disabled = !prefs.native_notifications;
    actions.appendChild(ask); actions.appendChild(test);
    macBox.appendChild(actions);
    var recent = (native.requests || []).slice(0, 6);
    if (recent.length) {
      var rows = h('div', 'vns-list');
      recent.forEach(function(row){
        rows.appendChild(h('div', 'vns-detail', (row.kind === 'scheduled' ? 'Scheduled' : 'Now') + ' · ' + (REQUEST_TEXT[row.state] || row.state) +
          (row.detail ? ' · ' + row.detail : '') + (row.updated_at ? ' · ' + new Date(row.updated_at).toLocaleTimeString() : '')));
      });
      macBox.appendChild(rows);
    }
  }
  function paintPrefs(){
    prefBox.textContent = '';
    prefBox.appendChild(h('div', 'vns-title', 'Quiet hours, sound and what notifications show'));
    var quiet = prefs.quiet_hours || {};
    var quietRow = h('div', 'vns-row');
    var quietToggle = toggle('Quiet hours from', quiet.enabled, null);
    var start = h('input', 'vns-input'); start.type = 'time'; start.value = quiet.start || '22:00'; start.setAttribute('aria-label', 'Quiet hours start');
    var end = h('input', 'vns-input'); end.type = 'time'; end.value = quiet.end || '07:00'; end.setAttribute('aria-label', 'Quiet hours end');
    quietRow.appendChild(quietToggle); quietRow.appendChild(start); quietRow.appendChild(h('span', null, 'to')); quietRow.appendChild(end);
    quietRow.appendChild(button('Save', function(){
      save({quiet_hours: {enabled: quietToggle.querySelector('input').checked, start: start.value, end: end.value}},
           'Quiet hours saved. During them macOS is not asked; the bell still records every alert.');
    }));
    prefBox.appendChild(quietRow);
    prefBox.appendChild(toggle('Play a sound with macOS notifications', prefs.sound, function(on){ save({sound: on}); }));
    var lockRow = h('label', 'vns-row'); lockRow.appendChild(h('span', null, 'A macOS notification may show'));
    var lock = h('select', 'vns-input'); lock.setAttribute('aria-label', 'What a macOS notification may show');
    LOCK.forEach(function(pair){ var option = h('option', null, pair[1]); option.value = pair[0]; lock.appendChild(option); });
    lock.value = prefs.lock_screen; lock.addEventListener('change', function(){ save({lock_screen: lock.value}); });
    lockRow.appendChild(lock); prefBox.appendChild(lockRow);
    var lateRow = h('div', 'vns-row'); lateRow.appendChild(h('span', null, 'Show an alert that comes due late, up to'));
    var late = h('input', 'vns-input'); late.type = 'number'; late.min = '0'; late.max = '60'; late.value = String(prefs.late_grace_minutes);
    late.setAttribute('aria-label', 'Minutes after an event started');
    lateRow.appendChild(late); lateRow.appendChild(h('span', null, 'minutes after its event started'));
    lateRow.appendChild(button('Save', function(){
      var value = Number(late.value);
      if (!isFinite(value) || value < 0 || value > 60 || Math.floor(value) !== value) { say('Enter whole minutes from 0 to 60.', true); return; }
      save({late_grace_minutes: value});
    }));
    prefBox.appendChild(lateRow);
  }
  var timer = setInterval(function(){
    if (!document.body.contains(host)) { clearInterval(timer); return; }
    request('GET', '/api/notifications/native/status').then(function(j){ if (j.ok && prefs) { native = j; paintMac(); } }).catch(function(){});
  }, 5000);
  load();
  return true;
}
window.VoolNotificationSettings = Object.freeze({mountInto: mountInto});
})();
"""


def render_notification_settings_fragment() -> str:
    """The alerts and macOS notifications panel, mounted by the Settings page's Notifications group."""
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>"


__all__ = ["render_notification_settings_fragment"]
