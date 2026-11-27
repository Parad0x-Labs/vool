"""The shared poll scheduler (core/page_poll_scheduler.py) and the pages that use it.

An idle chat window used to send close to three requests a second (each surface had its own
setInterval: disclosures every second, the wallet every 2.5 s, notifications every 3 s, ...) and
kept most of them going while hidden; each read costs the API a fresh store connection. These
tests drive the real scheduler script under node with a fake clock, and check that the chat page
and Settings carry exactly one scheduler that every converted surface registers with.
"""

from __future__ import annotations

import re

from core.page_poll_scheduler import HIDDEN_HEARTBEAT_MS, IDLE_HEARTBEAT_MS, POLL_SCHEDULER_JS, STALE_MS
from tests.chat_page_js_harness import run_node, script_blocks

# A fake browser around the real scheduler: a controllable clock and timer queue, a document whose
# visibility a test flips, and a `runs` log of every task call with its time.
PRELUDE = r"""
const realImmediate = setImmediate;
const flush = () => new Promise((r) => realImmediate(r));
let nowMs = 1000000;
Date.now = () => nowMs;
const timers = []; let seq = 0;
globalThis.setTimeout = (fn, ms) => { const id = ++seq; timers.push({ id, at: nowMs + Math.max(0, Number(ms) || 0), fn }); return id; };
globalThis.clearTimeout = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); };
globalThis.setInterval = () => { throw new Error('the scheduler must not use setInterval'); };
async function advance(ms) {
  const end = nowMs + ms;
  await flush();
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = timers[0];
    if (!t || t.at > end) break;
    timers.shift(); nowMs = t.at; t.fn(); await flush();
  }
  nowMs = end;
  await flush();
}
const listeners = {};
const on = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
const fire = async (type) => { for (const fn of listeners[type] || []) fn(); await flush(); };
globalThis.document = { hidden: false, addEventListener: on };
globalThis.window = { addEventListener: on, frameElement: null };
const T0 = nowMs;
const runs = {};
function task(name, body) {
  runs[name] = [];
  return () => { runs[name].push(nowMs - T0); return body ? body() : Promise.resolve(); };
}
const count = (name) => runs[name].length;
""" + POLL_SCHEDULER_JS + "\nconst P = window.VoolPoll;\n"


def _drive(body: str) -> dict:
    return run_node(PRELUDE + body + "\n")


def test_idle_reads_ride_one_shared_heartbeat():
    """No turn, nothing waiting: surfaces with 1 s, 2.5 s and 3 s fast cadences all read once per
    heartbeat, in the same beat, and a 60 s surface reads once a minute."""
    result = _drive(r"""
P.every('disclosure', task('disclosure'), { fast: 1000 });
P.every('wallet', task('wallet'), { fast: 2500 });
P.every('update', task('update'), { fast: 700, duringTurn: false });
P.every('version', task('version'), { idle: 60000, duringTurn: false, immediate: false });
await advance(60000);
const times = new Set();
for (const name of ['disclosure', 'wallet', 'update']) runs[name].slice(1).forEach((t) => times.add(t));
process.stdout.write(JSON.stringify({ runs, times: [...times].sort((a, b) => a - b), mode: P.state().mode }) + '\n');
""")
    beat = IDLE_HEARTBEAT_MS
    for name in ("disclosure", "wallet", "update"):
        assert result["runs"][name] == [0, beat, 2 * beat, 3 * beat], (name, result["runs"][name])
    assert result["times"] == [beat, 2 * beat, 3 * beat], "idle reads must share the beat, not trickle"
    assert result["runs"]["version"] == [3 * beat]
    assert result["mode"] == "idle"


def test_a_running_turn_brings_back_the_fast_reads_it_needs():
    """While a turn runs, a disclosure/approval read is back at 1 s at once (wake sends nothing
    by itself), surfaces that opted out of the turn cadence stay on the heartbeat, and the end of
    the turn returns everything to the heartbeat."""
    result = _drive(r"""
let turn = false;
P.busyWhen(() => turn);
P.every('disclosure', task('disclosure'), { fast: 1000, immediate: false });
P.every('screen', task('screen'), { duringTurn: false, immediate: false });
await advance(5000);
const beforeTurn = count('disclosure');
turn = true; P.wake();
await flush();
const atWake = count('disclosure');
const mode = P.state().mode;
await advance(10000);
const duringTurn = count('disclosure');
const screenDuring = count('screen');
turn = false; P.wake();
const endAt = nowMs - T0;
await advance(60000);
const afterTurn = runs.disclosure.filter((t) => t > endAt);
process.stdout.write(JSON.stringify({ beforeTurn, atWake, mode, duringTurn, screenDuring, afterTurn }) + '\n');
""")
    assert result["beforeTurn"] == 0
    assert result["atWake"] == 0, "wake() re-plans; it must not send a burst at a turn's start"
    assert result["mode"] == "turn"
    assert 9 <= result["duringTurn"] <= 11, result
    assert result["screenDuring"] == 0
    # The turn ended at 15 s, 5 s after a fast read: the 20 s beat skips it (under half a beat
    # old), then the read is back on the beat at 40 s and 60 s.
    assert result["afterTurn"] == [40000, 60000], "after the turn the read rides the 20 s heartbeat again"


def test_a_waiting_request_is_hot_without_a_turn():
    """An approval or disclosure found waiting (hot) is read at its fast cadence even with no turn
    running on this page (a council run, another tab), and drops back when it is answered."""
    result = _drive(r"""
let waiting = false;
P.every('disclosure', task('disclosure', () => { if (count('disclosure') === 2) waiting = true; return Promise.resolve(); }), { fast: 1000, hot: () => waiting });
await advance(20000);           // the beat read finds a request waiting
const found = count('disclosure');
await advance(5000);
const hotRuns = count('disclosure') - found;
waiting = false;
await advance(1000);
const settled = count('disclosure');
await advance(13000);           // up to 39 s: nothing until the next beat
const quiet = count('disclosure') - settled;
await advance(1000);            // the 40 s beat
process.stdout.write(JSON.stringify({ found, hotRuns, quiet, beat: count('disclosure') - settled }) + '\n');
""")
    assert result["found"] == 2
    assert 4 <= result["hotRuns"] <= 6, result
    assert result["quiet"] == 0
    assert result["beat"] == 1


def test_hidden_pauses_reads_and_showing_reads_everything_at_once():
    """Hidden: display-only reads pause, a read registered with a hidden cadence runs on the slow
    hidden beat; shown again, every surface reads at once (no waiting for the next beat)."""
    result = _drive(r"""
P.every('disclosure', task('disclosure'), { fast: 1000, immediate: false });
P.every('bell', task('bell'), { fast: 3000, hidden: P.HIDDEN_MS, immediate: false });
P.every('setup', task('setup'), { immediate: false, onShow: false });
document.hidden = true; await fire('visibilitychange');
let turn = true; P.busyWhen(() => turn); P.wake();
await advance(250000);
const hidden = { disclosure: count('disclosure'), bell: count('bell'), mode: P.state().mode };
document.hidden = false; await fire('visibilitychange');
const shown = { disclosure: count('disclosure'), bell: count('bell'), setup: count('setup') };
process.stdout.write(JSON.stringify({ hidden, shown }) + '\n');
""")
    assert result["hidden"] == {"disclosure": 0, "bell": 2, "mode": "hidden"}, (
        "hidden: paused, or one read per hidden beat (" + str(HIDDEN_HEARTBEAT_MS) + " ms), even with a turn running"
    )
    assert result["shown"]["disclosure"] == 1 and result["shown"]["bell"] == 3
    assert result["shown"]["setup"] == 0, "onShow: false leaves a surface that reads on show itself"


def test_reads_are_single_flight():
    """A read that has not answered holds back the next read of the same surface, even at a 1 s
    turn cadence, until it answers or goes stale."""
    result = _drive(r"""
P.busyWhen(() => true);
let release = null;
P.every('slow', task('slow', () => new Promise((r) => { release = r; })), { fast: 1000 });
await advance(10000);
const stuck = count('slow');
await advance(STALE - 10000 + 500);
const afterStale = count('slow');
release(); await flush();
await advance(3000);
process.stdout.write(JSON.stringify({ stuck, afterStale, inFlight: P.state().tasks.slow.inFlight }) + '\n');
""".replace("STALE", str(STALE_MS)))
    assert result["stuck"] == 1, "a second read went out while the first had not answered"
    assert result["afterStale"] == 2, "a read that never answers must not block the surface for ever"


def test_a_page_in_a_hidden_frame_is_paused():
    """Settings opened in the chat page's frame keeps its document after the overlay closes; its
    reads pause while the overlay hides it."""
    result = _drive(r"""
let hiddenFrame = true;
window.frameElement = { isConnected: true, closest: (sel) => (sel === '[hidden]' && hiddenFrame ? {} : null) };
P.every('wallet', task('wallet'), { fast: 2500, immediate: false });
await advance(120000);
const paused = count('wallet');
hiddenFrame = false; P.kick();
process.stdout.write(JSON.stringify({ paused, shown: count('wallet') }) + '\n');
""")
    assert result == {"paused": 0, "shown": 1}


def _registrations(html: str) -> list[str]:
    return re.findall(r"VoolPoll\.every\('([a-z-]+)'", html)


def test_the_chat_page_runs_one_scheduler_that_every_poll_registers_with():
    from core.screen_privacy import inject
    from core.vool_chat_page import render_vool_chat_html

    html = inject(render_vool_chat_html())
    assert html.count("window.VoolPoll = Object.freeze") == 1
    blocks = script_blocks(html)
    house = max(range(len(blocks)), key=lambda i: len(blocks[i]))
    defined = next(i for i, b in enumerate(blocks) if "window.VoolPoll = Object.freeze" in b)
    assert defined == house, "the scheduler is part of the house script, ahead of every fragment"
    assert blocks[house].index("window.VoolPoll = Object.freeze") < blocks[house].index("const PAGE_LEASE")
    names = set(_registrations(html))
    assert {
        "disclosure-pending", "wallet-status", "notifications", "mail-alerts", "update-status",
        "council-lock", "sidebar-activity", "screen-privacy", "runtime-version", "model-radar", "no-model-setup",
    } <= names, names
    # The turn signal and the turn-start re-plan are wired to the page's own run state.
    assert "VoolPoll.busyWhen(() => busyChatIds().length > 0)" in html
    start = blocks[house].index("function startRunTimers(run)")
    assert "VoolPoll.wake()" in blocks[house][start:start + 600]


def test_settings_carries_the_same_scheduler_before_its_own_script():
    from core.vool_settings_page import render_vool_settings_html

    html = render_vool_settings_html()
    assert html.count("window.VoolPoll = Object.freeze") == 1
    assert html.index("window.VoolPoll = Object.freeze") < html.index("<script>\nconst MODEL")
    assert {"wallet-status", "live-browser-switch", "private-web-route"} <= set(_registrations(html))


def test_the_served_chat_page_is_quiet_when_idle_and_hidden_and_fast_in_a_turn():
    """The real chat page and its real API doors in a browser, on a fake clock. Idle and visible,
    every surface reads once per 20 s beat (before: the disclosure poll alone read 60 times a
    minute, ~170 requests in all); a running turn brings the disclosure read back to every second;
    hidden, the page goes quiet; shown again, it reads at once."""
    import json
    from collections import Counter
    from urllib.parse import parse_qs, urlsplit

    from playwright.sync_api import sync_playwright

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    runtime = RuntimeServices(display_name="VOOL")
    counts: Counter = Counter()
    phase = ["load"]

    def route(r):
        url = urlsplit(r.request.url)
        if url.path.startswith("/api/") or url.path == "/healthz":
            counts[(phase[0], url.path)] += 1
        if r.request.method == "POST":
            resp = dispatch_post(path=url.path, body=json.loads(r.request.post_data or "{}"),
                                 headers={"content-type": "application/json"}, runtime=runtime, model_name="vool",
                                 workspace_root_provider=lambda: None, client_host="127.0.0.1")
        else:
            resp = dispatch_get(path=url.path, query=parse_qs(url.query), runtime=runtime, model_name="vool",
                                client_host="127.0.0.1")
        return r.fulfill(status=resp.status, content_type=resp.content_type, body=resp.body)

    def phase_counts(name):
        return {path: n for (ph, path), n in counts.items() if ph == name}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context().new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def run_for(seconds):
                for _ in range(seconds):
                    page.clock.run_for(1000)

            page.clock.install()
            page.route("**/*", route)
            page.goto("http://127.0.0.1:11435/chat")
            run_for(10)
            phase[0] = "idle"
            run_for(60)
            phase[0] = "turn"
            page.evaluate("() => { window.__turn = true; window.VoolPoll.busyWhen(() => window.__turn); window.VoolPoll.wake(); }")
            run_for(10)
            page.evaluate("() => { window.__turn = false; window.VoolPoll.wake(); }")
            phase[0] = "hidden"
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                document.dispatchEvent(new Event('visibilitychange')); }""")
            run_for(60)
            phase[0] = "shown"
            page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                document.dispatchEvent(new Event('visibilitychange')); }""")
            run_for(1)
        finally:
            browser.close()

    idle, turn, hidden, shown = (phase_counts(p) for p in ("idle", "turn", "hidden", "shown"))
    assert errors == [], errors
    assert idle.get("/api/disclosure/pending") == 3, idle
    assert max(idle.values()) <= 3, f"an idle read runs more often than once per beat: {idle}"
    assert sum(idle.values()) <= 40, idle
    assert turn.get("/api/disclosure/pending", 0) >= 9, f"a turn must bring the 1 s disclosure read back: {turn}"
    # Surfaces a turn does not need stay on the beat (at most one beat falls in these 10 s).
    assert turn.get("/api/screen-privacy", 0) <= 1 and turn.get("/api/update/status", 0) <= 1, turn
    # Hidden: only the cloud pill's own once-a-minute cache read may still run.
    assert set(hidden) <= {"/api/connections"} and sum(hidden.values()) <= 1, hidden
    for path in ("/api/disclosure/pending", "/api/notifications", "/api/wallet/status", "/api/update/status"):
        assert shown.get(path) == 1, f"{path} is not read at once when the page is shown: {shown}"


def _drive_rail(status: str, *, hidden: bool = False) -> dict:
    from core.runtime_task_rail_polling import RUNTIME_TASK_RAIL_POLLING_SCRIPT

    return run_node(r"""
const realImmediate = setImmediate;
const flush = () => new Promise((r) => realImmediate(r));
let nowMs = 5000000; Date.now = () => nowMs;
const intervals = [];
globalThis.setInterval = (fn, ms) => { intervals.push({ fn, ms, at: nowMs + ms }); return intervals.length; };
const listeners = {};
globalThis.document = { hidden: HIDDEN, addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); } };
const reads = [];
globalThis.fetch = async (url) => {
  reads.push(String(url).split('?')[0]);
  return { ok: true, json: async () => (String(url).indexOf('/api/runtime/sessions') === 0
    ? { sessions: [{ session_id: 's1', status: 'STATUS', updated_at: '' }] } : { events: [] }) };
};
const pollStatusEl = {}; let sessions = []; let selectedSessionId = ''; let lastSeq = 0; let knownEvents = []; let selectedEventSeq = 0;
const setQuerySession = () => {}; const pickDefaultSession = (s) => s[0].session_id;
const renderSessions = () => {}, renderSessionDetail = () => {}, renderSummary = () => {}, renderEvents = () => {}, renderSelectedStep = () => {};
""".replace("HIDDEN", "true" if hidden else "false").replace("STATUS", status) + RUNTIME_TASK_RAIL_POLLING_SCRIPT + r"""
await flush();
const boot = reads.length;
for (let i = 0; i < 50; i++) { nowMs += 1200; for (const t of intervals) t.fn(); await flush(); }   // 60 s
const minute = reads.filter((u) => u === '/api/runtime/sessions').length;
document.hidden = false; for (const fn of listeners.visibilitychange || []) fn(); await flush();
process.stdout.write(JSON.stringify({ boot, minute, shown: reads.filter((u) => u === '/api/runtime/sessions').length, interval: intervals[0].ms }) + '\n');
""")


def test_the_task_rail_reads_every_1_2_s_only_while_a_session_is_live():
    live = _drive_rail("running")
    assert live["interval"] == 1200
    assert live["minute"] == 51, live
    idle = _drive_rail("completed")
    assert idle["minute"] == 3, f"an idle rail reads once per 20 s beat: {idle}"


def test_the_task_rail_reads_nothing_while_hidden_and_at_once_when_shown():
    hidden = _drive_rail("running", hidden=True)
    assert hidden["boot"] == 0 and hidden["minute"] == 0, hidden
    assert hidden["shown"] == 1
