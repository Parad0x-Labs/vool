"""One shared poll scheduler for VOOL's pages (the chat page, its fragments, Settings).

Each surface used to run its own `setInterval`, so an idle chat window with no turn running still
sent close to three requests a second (the disclosure poll every second, the wallet every 2.5 s,
the notification centre every 3 s, the update chip, the council lock, the screen-privacy switch,
the sidebar lifecycle), and kept most of them going in a hidden tab. Every read costs the API a
fresh store connection, so the idle window alone held the API at 8-12 % CPU.

`window.VoolPoll` owns the timing instead. A surface registers what it reads and how often:

    VoolPoll.every(name, fn, {fast: ms, idle: ms, hidden: ms, hot: fn, duringTurn: bool,
                              immediate: bool, onShow: bool})

- fast: the cadence while it matters now: a turn is running on this page (any `busyWhen` probe
  answers true) and `duringTurn` is not false, or the task's own `hot()` answers true (an approval
  or disclosure waiting, an update installing). A hot() that turns true from the surface's own
  work (its first read, a sheet opening) is noticed within HOT_CHECK_MS, without a request.
- idle: the cadence otherwise. Idle reads ride ONE heartbeat (IDLE_HEARTBEAT_MS): the scheduler
  wakes once per beat and runs every idle task whose interval has passed, so an idle window wakes
  the API in one short burst per beat instead of a steady trickle. An idle interval is a multiple
  of the beat.
- hidden: the cadence while the page cannot be seen (`document.hidden`, or this page sits in a
  frame its host has hidden). 0, the default, pauses the task. Showing the page, focusing it, or a
  turn starting or ending (`wake()`) re-plans at once; showing or focusing the page (`kick()`) also
  runs every task that has not run in the last FRESH_MS (onShow: false leaves out a task whose
  surface already reads on show itself).
- immediate: false is for a surface that makes its own first read at boot.

Every task is single-flight: a read that has not answered holds the next one of the same task
back until it answers or goes stale (STALE_MS), so a slow machine never stacks requests up.
"""

from __future__ import annotations

IDLE_HEARTBEAT_MS = 20000
HIDDEN_HEARTBEAT_MS = 120000
STALE_MS = 15000
FRESH_MS = 10000
# How soon a task whose own hot() turned true starts its fast cadence. Checking runs page code only.
HOT_CHECK_MS = 1000

POLL_SCHEDULER_JS = r"""
(function () {
  if (typeof window === 'undefined' || window.VoolPoll) return;
  var IDLE_MS = __IDLE_MS__, HIDDEN_MS = __HIDDEN_MS__, STALE_MS = __STALE_MS__, MIN_GAP_MS = 250, FRESH_MS = __FRESH_MS__, HOT_CHECK_MS = __HOT_CHECK_MS__;
  var tasks = {}, order = [], probes = [], timer = null, wakeAt = 0, lastBeat = Date.now();
  function now() { return Date.now(); }
  function frameHidden() {
    try { var f = window.frameElement; return !!(f && (f.closest('[hidden]') || !f.isConnected)); } catch (e) { return false; }
  }
  function pageHidden() { return !!document.hidden || frameHidden(); }
  function turnLive() {
    for (var i = 0; i < probes.length; i++) { try { if (probes[i]()) return true; } catch (e) {} }
    return false;
  }
  function isHot(t, inTurn) {
    if (inTurn && t.duringTurn) return true;
    try { return !!(t.hot && t.hot()); } catch (e) { return false; }
  }
  // The wait before this task's next run in the current state; 0 means paused.
  function waitFor(t, hidden, inTurn) {
    if (hidden) return t.hidden;
    if (isHot(t, inTurn)) return t.fast;
    return t.idle;
  }
  function run(t, force) {
    if (t.since && now() - t.since < STALE_MS) return t.flight;
    if (!force && t.last && now() - t.last < MIN_GAP_MS) return t.flight;
    var mine = t.since = t.last = now();
    t.runs++;
    // an answer can change what is hot (a disclosure found waiting), so the plan is redone after it
    var done = function () { if (t.since === mine) t.since = 0; arm(now() + MIN_GAP_MS); };
    try { t.flight = Promise.resolve(t.fn()).then(done, done); }
    catch (e) { done(); t.flight = Promise.resolve(); }
    return t.flight;
  }
  function tick() {
    timer = null;
    var hidden = pageHidden(), inTurn = !hidden && turnLive(), at = now();
    var beat = hidden ? HIDDEN_MS : IDLE_MS;
    var beatDue = at - lastBeat >= beat - MIN_GAP_MS;
    if (beatDue) lastBeat = at;
    var next = lastBeat + beat;
    for (var i = 0; i < order.length; i++) {
      var t = tasks[order[i]];
      if (!t || t.stopped) continue;
      var wait = waitFor(t, hidden, inTurn);
      if (!wait) continue;
      var slow = hidden || !isHot(t, inTurn);
      // A task's own hot() can turn true from something the scheduler never sees (the surface's
      // own first read, a sheet opening, a press): look again within HOT_CHECK_MS so its fast
      // cadence starts then, not on the next beat. Looking sends nothing.
      if (slow && !hidden && t.hot && next > at + HOT_CHECK_MS) next = at + HOT_CHECK_MS;
      if (slow) {
        // Idle and hidden reads only ever run on the shared beat. A read that ran off the beat (a
        // press, the fast cadence of a turn that just ended) counts if it is half a beat old, so
        // no read waits longer than its interval plus half a beat.
        if (beatDue && (!t.last || at - t.last >= wait - beat / 2)) run(t, false);
      } else {
        var due = (t.last || 0) + wait;
        if (at >= due - MIN_GAP_MS) { run(t, false); due = at + wait; }
        if (due < next) next = due;
      }
    }
    arm(Math.max(next, at + MIN_GAP_MS));
  }
  function arm(at) {
    if (timer && wakeAt <= at) return;
    if (timer) clearTimeout(timer);
    wakeAt = at;
    timer = setTimeout(tick, Math.max(0, at - now()));
  }
  // The page came back into view: run every task that has not run in the last FRESH_MS now
  // (single-flight still holds), then follow the current state's cadence.
  function kick() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (pageHidden()) { arm(now() + MIN_GAP_MS); return; }
    var at = lastBeat = now();
    for (var i = 0; i < order.length; i++) {
      var t = tasks[order[i]];
      if (t && !t.stopped && t.onShow && (!t.last || at - t.last >= FRESH_MS)) run(t, false);
    }
    arm(at + MIN_GAP_MS);
  }
  // Something changed what is hot (a turn started or ended, an approval appeared): re-plan now.
  // It sends nothing by itself, so a turn's start never competes with a burst of idle reads.
  function wake() { if (timer) { clearTimeout(timer); timer = null; } arm(now()); }
  function every(name, fn, opts) {
    opts = opts || {};
    var idle = Math.max(IDLE_MS, Number(opts.idle) || IDLE_MS);
    var t = {
      name: String(name), fn: fn,
      fast: Math.max(MIN_GAP_MS, Number(opts.fast) || idle),
      idle: Math.ceil(idle / IDLE_MS) * IDLE_MS,
      hidden: opts.hidden ? Math.max(HIDDEN_MS, Number(opts.hidden)) : 0,
      hot: typeof opts.hot === 'function' ? opts.hot : null,
      duringTurn: opts.duringTurn !== false,
      onShow: opts.onShow !== false,
      last: 0, since: 0, runs: 0, flight: null, stopped: false,
    };
    if (!tasks[t.name]) order.push(t.name);
    tasks[t.name] = t;
    // immediate: false is for a surface that makes its own first read at boot
    if (opts.immediate === false) t.last = now();
    else if (!pageHidden()) run(t, true);
    arm(now() + MIN_GAP_MS);
    return {
      now: function () { return run(t, true); },
      stop: function () { t.stopped = true; },
      start: function () { t.stopped = false; arm(now() + MIN_GAP_MS); },
    };
  }
  function busyWhen(probe) { if (typeof probe === 'function') probes.push(probe); }
  function state() {
    var hidden = pageHidden(), inTurn = !hidden && turnLive(), out = {};
    for (var i = 0; i < order.length; i++) {
      var t = tasks[order[i]];
      out[t.name] = { wait: t.stopped ? 0 : waitFor(t, hidden, inTurn), runs: t.runs, inFlight: !!t.since, stopped: t.stopped };
    }
    return { mode: hidden ? 'hidden' : (inTurn ? 'turn' : 'idle'), heartbeat: hidden ? HIDDEN_MS : IDLE_MS, tasks: out };
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) kick(); });
  window.addEventListener('focus', kick);
  window.VoolPoll = Object.freeze({ every: every, busyWhen: busyWhen, kick: kick, wake: wake, state: state,
    IDLE_MS: IDLE_MS, HIDDEN_MS: HIDDEN_MS, STALE_MS: STALE_MS });
})();
""".replace("__IDLE_MS__", str(IDLE_HEARTBEAT_MS)).replace("__HIDDEN_MS__", str(HIDDEN_HEARTBEAT_MS)).replace(
    "__STALE_MS__", str(STALE_MS)
).replace("__FRESH_MS__", str(FRESH_MS)).replace("__HOT_CHECK_MS__", str(HOT_CHECK_MS))
