"""Settings panel: alerts, macOS notifications and the watch list (the Notifications group).

Turn the macOS channel on or off, see what macOS reported (permission, the bridge's last contact, recent requests with
their separate states), ask macOS for permission, send a test notification, and set quiet hours, sound, what a macOS
notification may show and how late an alert may still be shown. The Watching card lists the wallet and price watches
(Pause, Resume, Remove). The panel calls only ``/api/notifications/preferences``, the ``/api/notifications/native/*``
routes (core/operator/native_notifications.py) and ``/api/market/watches`` (core/market_watch.py), and never claims a
banner was shown: macOS does not report that.

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
.vmw-watch{display:flex;flex-direction:column;gap:4px;padding:8px 0;border-bottom:1px solid var(--border,#262b35)}
.vmw-watch:last-child{border-bottom:none}
.vmw-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:13px}
.vmw-name{font-weight:600}
.vmw-state{font-size:12px;color:var(--muted,#9aa1af)}
.vmw-state.vmw-attention{color:var(--bad,#f2545b)}
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
// English pages show corrected English for keys whose catalog text names one platform for everyone; other
// locales keep their translation through NT until the catalog carries the corrected source.
function pageIsEnglish(){
  /* English only when the page says so; an unknown language keeps the catalog text. */
  var lang = String((document.documentElement && document.documentElement.lang)
    || (window.VOOL_I18N && window.VOOL_I18N.locale) || '').toLowerCase();
  return lang === 'en' || lang.indexOf('en-') === 0;
}
function NE(key, english){ return pageIsEnglish() ? english : NT(key, english); }
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
  var watchBox = h('div', 'vns-box');
  var why = h('div', 'vns-note');
  root.appendChild(status); root.appendChild(macBox); root.appendChild(prefBox); root.appendChild(watchBox); root.appendChild(why);
  var prefs = null, native = null, watches = null;
  function say(text, bad){ status.textContent = text || ''; status.className = 'vns-status' + (bad ? ' vns-bad' : ''); }
  function load(){
    return Promise.all([request('GET', '/api/notifications/preferences'), request('GET', '/api/notifications/native/status').catch(function(){return {ok:false};}), request('GET', '/api/market/watches').catch(function(){return {ok:false};})]).then(function(r){
      if (!r[0].ok) { say(NT('vns.status.unavailable', 'Notification settings are unavailable right now.'), true); return; }
      prefs = r[0].preferences; native = r[1].ok ? r[1] : {supported:false}; watches = r[2].ok ? (r[2].watches || []) : null; paint();
      if (status.textContent === NT('vns.status.loading', 'Loading…')) say('');   /* loaded: no "Loading…" above a drawn panel */
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
  /* The macOS paragraph and channel belong to a Mac; elsewhere one plain line says where alerts go, instead
     of an empty bordered box and a promised test button that is not there. */
  function paint(){ paintMac(); paintPrefs(); paintWatches(); why.textContent = native.supported === false ? '' : (native.explanation || ''); }
  function paintMac(){
    macBox.textContent = '';
    if (native.supported === false) {
      macBox.className = 'vns-note';
      macBox.textContent = NE('vns.unsupported', 'System notifications are available in the VOOL app on a Mac. Here, every alert appears in the bell.');
      return;
    }
    macBox.className = 'vns-box';
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
    prefBox.appendChild(h('div', 'vns-note', NE('vns.prefs.instant', 'Changes here are saved as soon as you make them.')));
    var quiet = prefs.quiet_hours || {};
    var quietRow = h('div', 'vns-row');
    var start = h('input', 'vns-input'); start.type = 'time'; start.value = quiet.start || '22:00'; start.setAttribute('aria-label', NT('vns.quiet.start_aria', 'Quiet hours start'));
    var end = h('input', 'vns-input'); end.type = 'time'; end.value = quiet.end || '07:00'; end.setAttribute('aria-label', NT('vns.quiet.end_aria', 'Quiet hours end'));
    /* Quiet hours save the moment they change, like every other control in this panel (a ticked box used to
       do nothing until a separate Save, and a reload dropped it). A period that starts when it ends is refused. */
    function saveQuiet(){
      var on = quietToggle.querySelector('input').checked;
      if (on && start.value && start.value === end.value) { say(NE('vns.quiet.same', 'Start and end are the same time. Choose two different times.'), true); return; }
      save({quiet_hours: {enabled: on, start: start.value, end: end.value}},
           on ? NE('vns.saved.quiet', 'Quiet hours saved. During them no system notification is sent; the bell still records every alert.') : NE('vns.saved.quiet_off', 'Quiet hours are off.'));
    }
    var quietToggle = toggle(NT('vns.quiet.label', 'Quiet hours from'), quiet.enabled, saveQuiet);
    [start, end].forEach(function(i){ i.addEventListener('change', function(){ if (quietToggle.querySelector('input').checked) saveQuiet(); }); });
    quietRow.appendChild(quietToggle); quietRow.appendChild(start); quietRow.appendChild(h('span', null, NT('vns.quiet.to', 'to'))); quietRow.appendChild(end);
    prefBox.appendChild(quietRow);
    prefBox.appendChild(toggle(NT('vns.toggle.sound', 'Notification sound'), prefs.sound, function(on){
      if (!on && window.VoolNotificationAudio) window.VoolNotificationAudio.mute();
      save({sound: on});
    }));
    prefBox.appendChild(h('div', 'vns-note', NT('vns.sound.help', 'A gentle chime when work completes or needs you.')));
    prefBox.appendChild(button(NT('vns.sound.preview', 'Preview sound'), function(){
      if (window.VoolNotificationAudio) window.VoolNotificationAudio.preview().then(function(played){
        if (!played) say(NT('vns.sound.blocked', 'Sound playback is unavailable. Notifications remain in the bell.'), true);
      });
    }));
    var lockRow = h('label', 'vns-row'); lockRow.appendChild(h('span', null, NE('vns.lock.label', 'A system notification may show')));
    var lock = h('select', 'vns-input'); lock.setAttribute('aria-label', NE('vns.lock.aria', 'What a system notification may show'));
    lockOptions().forEach(function(pair){ var option = h('option', null, pair[1]); option.value = pair[0]; lock.appendChild(option); });
    lock.value = prefs.lock_screen; lock.addEventListener('change', function(){ save({lock_screen: lock.value}); });
    lockRow.appendChild(lock); prefBox.appendChild(lockRow);
    var lateRow = h('div', 'vns-row'); lateRow.appendChild(h('span', null, NT('vns.late.label', 'Show an alert that comes due late, up to')));
    var late = h('input', 'vns-input'); late.type = 'number'; late.min = '0'; late.max = '60'; late.value = String(prefs.late_grace_minutes);
    late.setAttribute('aria-label', NT('vns.late.aria', 'Minutes after an event started'));
    lateRow.appendChild(late); lateRow.appendChild(h('span', null, NT('vns.late.after', 'minutes after its event started')));
    /* Saved on change like every other control here ("saved as soon as you make them").
       An empty box is not 0, and "1e1" is not a number of minutes anyone typed on purpose. */
    late.addEventListener('change', function(){
      var raw = String(late.value || '').trim();
      var value = Number(raw);
      if (!/^\d+$/.test(raw) || value > 60) { say(NT('vns.late.invalid', 'Enter whole minutes from 0 to 60.'), true); return; }
      save({late_grace_minutes: value});
    });
    prefBox.appendChild(lateRow);
  }
  /* The Watching card: every wallet and price watch, in words, with Pause/Resume and Remove.
     The rule is data from the server; the words are composed here so each locale says them its own way. */
  function watchRuleText(watch){
    var rule = watch.rule || {};
    var text;
    if (watch.kind === 'wallet') text = NT('vmw.rule.tx', 'new transactions');
    else if (rule.above !== undefined) text = NTF('vmw.rule.above', 'goes above {price}', {price: String(rule.above)});
    else if (rule.below !== undefined) text = NTF('vmw.rule.below', 'drops below {price}', {price: String(rule.below)});
    else if (rule.move_pct !== undefined) {
      var key = Number(rule.move_pct) >= 0 ? 'vmw.rule.rise' : 'vmw.rule.drop';
      text = NTF(key, key === 'vmw.rule.rise' ? 'rises {pct}% within {window}' : 'drops {pct}% within {window}',
                 {pct: String(Math.abs(Number(rule.move_pct))), window: String(rule.window || '')});
    } else text = '';
    if (rule.repeat) text += NT('vmw.rule.repeat', ' (every time)');
    return text;
  }
  /* Why the last check did not work: the server sends a reason code only, never provider text.
     An unknown code says the generic line, so nothing unworded reaches the card. */
  var WATCH_ERRORS = {
    rate_limited: 'The price or history source is limiting requests. VOOL waits, then checks again.',
    provider_down: 'The source could not answer. VOOL waits, then checks again.',
    unreachable: 'The source could not be reached. VOOL checks again later.',
    bad_answer: 'The source answered with something VOOL could not use. Nothing was alerted.',
    no_pool: 'No trading pool was found for this token.',
    budget: 'The hourly limit of checks is used up. The next check waits.',
    resting: 'The source refused requests, so checks are resting for a few minutes.',
    history_gap: 'The price history does not reach back far enough to compare yet.',
    key_missing: 'No saved key serves this chain. Add one in Settings → API Keys.',
    key_unreadable: 'The saved key could not be read from this computer. Check Settings → API Keys, then Resume.',
    key_rejected: 'The saved key was refused. Replace it in Settings → API Keys, then Resume.',
    quota_exhausted: 'The saved key has no quota left. Check its plan, then Resume.',
    plan_refused: 'The saved key\'s plan does not include this. Check the plan or add another key, then Resume.',
    local_only: 'VOOL may not go online right now (for example Local Only is on). Resume when it may.',
    web_off: 'Web access is off. Turn it on in Settings to check prices.',
    unknown: 'The last check did not work. VOOL checks again later.'
  };
  function watchErrorText(code){
    var known = Object.prototype.hasOwnProperty.call(WATCH_ERRORS, code) ? code : 'unknown';
    return NT('vmw.err.' + known, WATCH_ERRORS[known]);
  }
  function watchStateText(state){
    var map = {active: ['vmw.state.active', 'Watching'], paused: ['vmw.state.paused', 'Paused'], needs_setup: ['vmw.state.needs_setup', 'Needs attention']};
    var pair = map[state] || [null, state];
    return NT(pair[0], pair[1]);
  }
  function watchAct(watch, action, done){
    say(NT('vns.status.working', 'Working…'));
    return request('POST', '/api/market/watches/' + action, {id: watch.id}).then(function(j){
      return load().then(function(){
        if (!j.ok) say(j.detail || j.reason || NT('vns.status.failed', 'That did not work.'), true);
        else say(done);
      });
    }).catch(function(){ say(NT('notif.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
  }
  function paintWatches(){
    watchBox.textContent = '';
    watchBox.appendChild(h('div', 'vns-title', NT('vmw.title', 'Watching')));
    if (watches === null) { watchBox.appendChild(h('div', 'vns-note', NT('vmw.unavailable', 'The watch list is unavailable right now.'))); return; }
    if (!watches.length) { watchBox.appendChild(h('div', 'vns-line', NT('vmw.empty', 'Nothing watched yet. Ask in any chat, for example: watch this wallet.'))); return; }
    watches.forEach(function(watch){
      var row = h('div', 'vmw-watch');
      var head = h('div', 'vmw-head');
      head.appendChild(h('span', 'vmw-name', watch.label));
      head.appendChild(h('span', 'vmw-state' + (watch.state === 'needs_setup' ? ' vmw-attention' : ''), watchStateText(watch.state)));
      row.appendChild(head);
      var line = h('div', 'vns-line');
      line.textContent = watch.chain + ' · ' + watchRuleText(watch);
      row.appendChild(line);
      var when = watch.last_checked ? new Date(watch.last_checked) : null;
      row.appendChild(h('div', 'vns-note', NTF('vmw.last_check', 'Last checked {when}', {when: when ? when.toLocaleString() : NT('vmw.never', 'not yet')})));
      if (watch.last_error) row.appendChild(h('div', 'vns-note', watchErrorText(watch.last_error)));
      var actions = h('div', 'vns-row');
      if (watch.state === 'paused') {
        actions.appendChild(button(NT('vmw.btn.resume', 'Resume'), function(){ watchAct(watch, 'resume', NT('vmw.resumed', 'Resumed.')); }));
      } else {
        actions.appendChild(button(NT('vmw.btn.pause', 'Pause'), function(){ watchAct(watch, 'pause', NT('vmw.paused', 'Paused.')); }));
      }
      actions.appendChild(button(NT('vmw.btn.remove', 'Remove'), function(){ watchAct(watch, 'remove', NT('vmw.removed', 'Removed.')); }));
      row.appendChild(actions);
      watchBox.appendChild(row);
    });
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
    from core.notification_audio import audio_script
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>" + audio_script()


__all__ = ["render_notification_settings_fragment"]
