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
// The panel's strings resolve through the page's i18n catalog (VOOLT, defined by the
// settings page's bootstrap before this fragment loads). The fallback literals are the
// English catalog source: without the bootstrap the panel renders byte-identical English.
function NT(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
function NTF(key, fallback, params){
  try {
    if (typeof VOOLT === 'function' && typeof VOOLFMT === 'function') {
      var t = VOOLT(key);
      if (t && t !== key) return VOOLFMT(t, params || {});
    }
  } catch (e) {}
  return fallback;
}
// macOS request states share the bell popover's already-translated vocabulary (notif.mac.*).
function requestText(state, raw){
  var key = {queued: 'notif.mac.queued', submitted: 'notif.mac.submitted', listed: 'notif.mac.listed',
             acknowledged: 'notif.mac.acknowledged', suppressed: 'notif.mac.suppressed', failed: 'notif.mac.failed',
             withdraw_requested: 'notif.mac.withdraw_requested', withdrawn: 'notif.mac.withdrawn'}[state];
  return key ? NT(key, raw) : raw;
}
function authText(auth, raw){
  var key = {authorized: 'vns.auth.authorized', provisional: 'vns.auth.provisional', ephemeral: 'vns.auth.ephemeral',
             denied: 'vns.auth.denied', not_determined: 'vns.auth.not_determined', unknown: 'vns.auth.unknown'}[auth];
  return key ? NT(key, raw) : raw;
}
var LOCK = [['title_only', 'The title only'], ['full', 'The title and the details'], ['hidden', 'Nothing personal (for example: A calendar alert is due)']];
function lockOptions(){
  return [['title_only', NT('vns.lock.title_only', LOCK[0][1])],
          ['full', NT('vns.lock.full', LOCK[1][1])],
          ['hidden', NT('vns.lock.hidden', LOCK[2][1])]];
}
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
  var status = h('div', 'vns-status', NT('vns.status.loading', 'Loading…'));
  var macBox = h('div', 'vns-box');
  var prefBox = h('div', 'vns-box');
  var why = h('div', 'vns-note');
  root.appendChild(status); root.appendChild(macBox); root.appendChild(prefBox); root.appendChild(why);
  var prefs = null, native = null;
  function say(text, bad){ status.textContent = text || ''; status.className = 'vns-status' + (bad ? ' vns-bad' : ''); }
  function load(){
    return Promise.all([request('GET', '/api/notifications/preferences'), request('GET', '/api/notifications/native/status')]).then(function(r){
      if (!r[0].ok || !r[1].ok) { say(NT('vns.status.unavailable', 'Notification settings are unavailable right now.'), true); return; }
      prefs = r[0].preferences; native = r[1]; paint();
    }).catch(function(){ say(NT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function save(patch, done){
    say(NT('vns.status.saving', 'Saving…'));
    return request('POST', '/api/notifications/preferences', {preferences: patch}).then(function(j){
      return load().then(function(){ if (!j.ok) say(j.detail || j.reason || NT('vns.status.not_saved', 'Not saved.'), true); else say(done || NT('vns.status.saved', 'Saved.')); });
    }).catch(function(){ say(NT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function act(url, done){
    say(NT('vns.status.working', 'Working…'));
    return request('POST', url, {}).then(function(j){
      return load().then(function(){ if (!j.ok) say(j.detail || j.reason || NT('vns.status.failed', 'That did not work.'), true); else say(done); });
    }).catch(function(){ say(NT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function paint(){ paintMac(); paintPrefs(); why.textContent = native.explanation || ''; }
  function paintMac(){
    macBox.textContent = '';
    macBox.appendChild(h('div', 'vns-title', NT('vns.title.mac', 'macOS notifications')));
    macBox.appendChild(toggle(NT('vns.toggle.native', 'Also send VOOL alerts to macOS notifications'), prefs.native_notifications, function(on){
      save({native_notifications: on}, on ? NT('vns.saved.native_on', 'On. VOOL asks macOS for permission if macOS has not given it.')
                                         : NT('vns.saved.native_off', 'Off. What macOS still holds is withdrawn; the bell keeps every alert.'));
    }));
    var line;
    if (!prefs.native_notifications) line = NT('vns.line.off', 'Off: the bell keeps every alert.');
    else if (!native.connected) line = NT('vns.line.waiting', 'On: waiting for the VOOL app window, which hands notifications to macOS while it runs.');
    else line = NTF('vns.line.auth', 'On: macOS permission is {auth}.', {auth: authText(native.authorization, native.authorization)});
    macBox.appendChild(h('div', 'vns-line', line));
    if (native.recovery) macBox.appendChild(h('div', 'vns-recovery', native.recovery));
    var answer = native.authorization_request || {};
    if (answer.detail) macBox.appendChild(h('div', 'vns-detail', NTF('vns.detail.answered', 'macOS answered: {detail}', {detail: answer.detail})));
    var actions = h('div', 'vns-row');
    var ask = button(NT('vns.btn.authorize', 'Ask macOS for permission'), function(){ act('/api/notifications/native/authorize', NT('vns.saved.asked', 'Asked. macOS shows its prompt once; answer it there.')); });
    ask.disabled = !prefs.native_notifications || native.authorization === 'authorized';
    var test = button(NT('vns.btn.test', 'Send a test notification'), function(){
      act('/api/notifications/native/test', NT('vns.saved.test', 'Sent to the bell and handed to macOS. VOOL records when macOS accepts it; it cannot see whether a banner appeared.'));
    });
    test.disabled = !prefs.native_notifications;
    actions.appendChild(ask); actions.appendChild(test);
    macBox.appendChild(actions);
    var recent = (native.requests || []).slice(0, 6);
    if (recent.length) {
      var rows = h('div', 'vns-list');
      recent.forEach(function(row){
        rows.appendChild(h('div', 'vns-detail', (row.kind === 'scheduled' ? NT('vns.req.scheduled', 'Scheduled') : NT('vns.req.now', 'Now')) + ' · ' + requestText(row.state, row.state) +
          (row.detail ? ' · ' + row.detail : '') + (row.updated_at ? ' · ' + new Date(row.updated_at).toLocaleTimeString() : '')));
      });
      macBox.appendChild(rows);
    }
  }
  function paintPrefs(){
    prefBox.textContent = '';
    prefBox.appendChild(h('div', 'vns-title', NT('vns.title.prefs', 'Quiet hours, sound and what notifications show')));
    var quiet = prefs.quiet_hours || {};
    var quietRow = h('div', 'vns-row');
    var quietToggle = toggle(NT('vns.quiet.label', 'Quiet hours from'), quiet.enabled, null);
    var start = h('input', 'vns-input'); start.type = 'time'; start.value = quiet.start || '22:00'; start.setAttribute('aria-label', NT('vns.quiet.start_aria', 'Quiet hours start'));
    var end = h('input', 'vns-input'); end.type = 'time'; end.value = quiet.end || '07:00'; end.setAttribute('aria-label', NT('vns.quiet.end_aria', 'Quiet hours end'));
    quietRow.appendChild(quietToggle); quietRow.appendChild(start); quietRow.appendChild(h('span', null, NT('vns.quiet.to', 'to'))); quietRow.appendChild(end);
    quietRow.appendChild(button(NT('vns.btn.save', 'Save'), function(){
      save({quiet_hours: {enabled: quietToggle.querySelector('input').checked, start: start.value, end: end.value}},
           NT('vns.saved.quiet', 'Quiet hours saved. During them macOS is not asked; the bell still records every alert.'));
    }));
    prefBox.appendChild(quietRow);
    prefBox.appendChild(toggle(NT('vns.toggle.sound', 'Play a sound with macOS notifications'), prefs.sound, function(on){ save({sound: on}); }));
    var lockRow = h('label', 'vns-row'); lockRow.appendChild(h('span', null, NT('vns.lock.label', 'A macOS notification may show')));
    var lock = h('select', 'vns-input'); lock.setAttribute('aria-label', NT('vns.lock.aria', 'What a macOS notification may show'));
    lockOptions().forEach(function(pair){ var option = h('option', null, pair[1]); option.value = pair[0]; lock.appendChild(option); });
    lock.value = prefs.lock_screen; lock.addEventListener('change', function(){ save({lock_screen: lock.value}); });
    lockRow.appendChild(lock); prefBox.appendChild(lockRow);
    var lateRow = h('div', 'vns-row'); lateRow.appendChild(h('span', null, NT('vns.late.label', 'Show an alert that comes due late, up to')));
    var late = h('input', 'vns-input'); late.type = 'number'; late.min = '0'; late.max = '60'; late.value = String(prefs.late_grace_minutes);
    late.setAttribute('aria-label', NT('vns.late.aria', 'Minutes after an event started'));
    lateRow.appendChild(late); lateRow.appendChild(h('span', null, NT('vns.late.after', 'minutes after its event started')));
    lateRow.appendChild(button(NT('vns.btn.save', 'Save'), function(){
      var value = Number(late.value);
      if (!isFinite(value) || value < 0 || value > 60 || Math.floor(value) !== value) { say(NT('vns.late.invalid', 'Enter whole minutes from 0 to 60.'), true); return; }
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
