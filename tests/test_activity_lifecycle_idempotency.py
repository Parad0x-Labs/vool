"""Cross-boundary contracts for chat-scoped Activity ingestion and draft lifecycle truth."""

from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

HTML = render_vool_chat_html()


def _run(body: str) -> dict:
    return run_node(DOM + script() + body)


def test_more_than_two_hundred_events_stay_unique_across_multiple_runs() -> None:
    result = _run(
        """
setDisplayedChat('chat:long-lived');
await new Promise(setImmediate);
const owner = view;
resetChatLedger(owner);
const backend = Array.from({ length: 240 }, (_, i) => ({
  session_id: owner.chatId, seq: i + 1, client_turn_id: 'turn-' + (Math.floor(i / 40) + 1),
  event_type: 'model.call_completed', message: 'event ' + (i + 1),
}));
for (let turn = 0; turn < 4; turn++) {
  const run = newRun(owner.chatId);
  // Replaying every row reproduces the shipped per-run cursor=0 shape. Identity must remain owned
  // by the chat even after an event is more than 50 rows behind the tail.
  for (const event of backend) appendToChatLedger(owner, event);
  run.ended = true;
}
out({
  count: owner.chatLedger.length,
  unique: new Set(owner.chatLedger.map((event) => event.seq)).size,
  cursor: owner.chatLedgerCursor,
  sorted: owner.chatLedger.every((event, i, rows) => !i || rows[i - 1].seq < event.seq),
});
"""
    )
    assert result["errors"] == []
    assert result["count"] == result["unique"] == result["cursor"] == 240
    assert result["sorted"] is True


def test_overlapping_duplicate_pages_continue_from_the_chat_cursor() -> None:
    result = _run(
        """
setDisplayedChat('chat:overlap');
await new Promise(setImmediate);
const owner = view;
resetChatLedger(owner);
const backend = Array.from({ length: 240 }, (_, i) => ({
  session_id: owner.chatId, seq: i + 1, client_turn_id: 'turn-' + (Math.floor(i / 80) + 1),
  event_type: 'tool_executed', message: 'event ' + (i + 1),
}));
const requestedAfter = [];
globalThis.fetch = async (url) => {
  const after = Number(new URL(String(url), 'http://vool.test').searchParams.get('after') || 0);
  requestedAfter.push(after);
  // Page two deliberately overlaps page one by 50 rows. A caught-up poll deliberately returns the
  // last 20 again, like a reconnecting/eventually-consistent transport.
  const events = after === 0 ? backend.slice(0, 200) : (after === 200 ? backend.slice(150) : backend.slice(-20));
  const next = Math.max(after, ...events.map((event) => event.seq));
  return { ok: true, json: async () => ({ events, next_after: next }) };
};
const first = adoptRun(owner.chatId, newRun(owner.chatId)); first.turnId = 'turn-3';
await pollLedger(first);
const second = adoptRun(owner.chatId, newRun(owner.chatId)); second.turnId = 'turn-4';
await pollLedger(second);
out({
  requestedAfter,
  count: owner.chatLedger.length,
  unique: new Set(owner.chatLedger.map((event) => event.seq)).size,
  cursor: owner.chatLedgerCursor,
  firstRun: first.ledger.length,
  secondRun: second.ledger.length,
});
"""
    )
    assert result["errors"] == []
    assert result["requestedAfter"] == [0, 200, 240]
    assert result["count"] == result["unique"] == 240
    assert result["cursor"] == 240
    assert result["firstRun"] == 80
    assert result["secondRun"] == 0


def test_recovered_ledger_seeds_live_cursor_without_replay() -> None:
    result = _run(
        """
setDisplayedChat('chat:refresh');
await new Promise(setImmediate);
const owner = view;
const backend = Array.from({ length: 241 }, (_, i) => ({
  session_id: owner.chatId, seq: i + 1,
  client_turn_id: i === 240 ? 'turn-live' : 'turn-history',
  event_type: i === 240 ? 'task_received' : 'model.call_completed', message: 'event ' + (i + 1),
}));
const requestedAfter = [];
let recovering = true;
globalThis.fetch = async (url) => {
  const after = Number(new URL(String(url), 'http://vool.test').searchParams.get('after') || 0);
  requestedAfter.push(after);
  let events;
  if (recovering) events = after === 0 ? backend.slice(0, 200) : backend.slice(150, 240);
  else events = after >= 240 ? backend.slice(240) : [];
  const next = Math.max(after, ...events.map((event) => event.seq));
  return { ok: true, json: async () => ({ events, next_after: next }) };
};
await loadRecoveredLedger();
const recovered = { count: owner.chatLedger.length, cursor: owner.chatLedgerCursor };
recovering = false;
const run = adoptRun(owner.chatId, newRun(owner.chatId)); run.turnId = 'turn-live';
await pollLedger(run);
out({
  requestedAfter, recovered,
  count: owner.chatLedger.length,
  unique: new Set(owner.chatLedger.map((event) => event.seq)).size,
  cursor: owner.chatLedgerCursor,
  liveSeqs: run.ledger.map((event) => event.seq),
});
"""
    )
    assert result["errors"] == []
    assert result["recovered"] == {"count": 240, "cursor": 240}
    assert result["requestedAfter"] == [0, 200, 240]
    assert result["count"] == result["unique"] == result["cursor"] == 241
    assert result["liveSeqs"] == [241]


def test_two_chats_keep_independent_cursors_and_sequence_identity() -> None:
    result = _run(
        """
const catalogs = {};
await new Promise(setImmediate);
for (const sid of ['chat:a', 'chat:b']) catalogs[sid] = Array.from({ length: 205 }, (_, i) => ({
  session_id: sid, seq: i + 1, client_turn_id: 'turn-' + sid.slice(-1),
  event_type: 'tool_executed', message: sid + ' event ' + (i + 1),
}));
globalThis.fetch = async (url) => {
  const parsed = new URL(String(url), 'http://vool.test');
  const sid = parsed.searchParams.get('session');
  const after = Number(parsed.searchParams.get('after') || 0);
  const events = catalogs[sid].filter((event) => event.seq > after).slice(0, 200);
  const next = Math.max(after, ...events.map((event) => event.seq));
  return { ok: true, json: async () => ({ events, next_after: next }) };
};
const runA = adoptRun('chat:a', newRun('chat:a')); runA.turnId = 'turn-a';
const runB = adoptRun('chat:b', newRun('chat:b')); runB.turnId = 'turn-b';
await Promise.all([pollLedger(runA), pollLedger(runB)]);
out({
  a: { count: chatState('chat:a').chatLedger.length, cursor: chatState('chat:a').chatLedgerCursor,
       last: chatState('chat:a').chatLedger.at(-1).message },
  b: { count: chatState('chat:b').chatLedger.length, cursor: chatState('chat:b').chatLedgerCursor,
       last: chatState('chat:b').chatLedger.at(-1).message },
});
"""
    )
    assert result["errors"] == []
    assert result["a"] == {"count": 205, "cursor": 205, "last": "chat:a event 205"}
    assert result["b"] == {"count": 205, "cursor": 205, "last": "chat:b event 205"}


def test_passive_boot_navigation_and_new_drafts_never_post_mode_events() -> None:
    result = _run(
        """
const calls = [];
await new Promise(setImmediate);
globalThis.fetch = async (url, options) => {
  const body = options && options.body ? JSON.parse(options.body) : null;
  calls.push({ url: String(url), method: String((options && options.method) || 'GET'), op: body && body.op });
  return {
    ok: true, status: 200,
    json: async () => ({ sessions: [], messages: [], queue: [], pins: [], projects: [], events: [], next_after: 0 }),
  };
};
const bootId = displayedChat;
const snap = (id) => { const o = chatState(id); return [String(o.mode), JSON.stringify(o.bypassGrant || null)]; };
const before = { boot: snap(bootId), existing: snap('chat:existing-idle') };
newChat();
await newChatInProject('');
await openSession('chat:existing-idle');
out({
  modeOps: calls.filter((call) => call.url === '/api/mode').map((call) => call.op),
  // The boundary: every POST to /api/mode EXCEPT the accepted read-only probe.
  modePosts: calls.filter((call) => call.url === '/api/mode' && call.op !== 'bypass_options'),
  before: before,
  after: { boot: snap(bootId), existing: snap('chat:existing-idle') },
  calls,
});
"""
    )
    assert result["errors"] == []
    # Passive boot/navigation/new drafts send no state-changing mode operation. The one POST
    # they do send is the read-only grant probe the shipped reload-restore feature added to
    # loadSessions (op bypass_options; the server owner mints nothing -- pinned by
    # tests/test_bypass_setup_flow.py and the wallet-home boundary). This test pins BOTH
    # sides: the probe is the only op allowed through, and it grants nothing and changes no
    # chat's mode -- so a probe that silently became a state-changing op, or started minting
    # grants on passive paths, fails here.
    assert result["modeOps"], "the boot probe is expected to run"
    assert set(result["modeOps"]) == {"bypass_options"}
    assert result["modePosts"] == [], "a passive path sent a state-changing mode operation"
    assert result["after"] == result["before"], "a passive path granted or changed a mode"


def test_an_explicit_mode_change_is_still_seen_by_the_passive_boundary() -> None:
    """Detection proof for the boundary above: an explicit user mode change crosses the same
    wire and must be classified as a state-changing operation, so the no-passive-posts filter
    can never pass vacuously."""
    result = _run(
        """
const calls = [];
await new Promise(setImmediate);
globalThis.fetch = async (url, options) => {
  const body = options && options.body ? JSON.parse(options.body) : null;
  calls.push({ url: String(url), method: String((options && options.method) || 'GET'), op: body && body.op });
  return {
    ok: true, status: 200,
    json: async () => ({ sessions: [], messages: [], queue: [], pins: [], projects: [], events: [], next_after: 0, state: { mode: 'auto' } }),
  };
};
await setModeController('auto');
out({
  stateOps: calls.filter((call) => call.url === '/api/mode' && call.op !== 'bypass_options'),
  ops: calls.filter((call) => call.url === '/api/mode').map((call) => call.op),
});
"""
    )
    assert result["errors"] == []
    assert "set" in result["ops"]
    assert result["stateOps"], "the boundary must still see a state-changing mode operation"


def test_passive_paths_do_not_call_the_mode_controller_by_source_contract() -> None:
    for start, end in (
        ("async function openSession(id) {", "function newChat() {"),
        ("function newChat() {", "// ---- Per-project exact model memory"),
        ("async function newChatInProject(pid) {", "let _toastTimer"),
    ):
        assert "syncModeController()" not in HTML[HTML.index(start) : HTML.index(end)]
    init = HTML[HTML.index("function initComposerControls() {") : HTML.index("// ---- Settings modal")]
    assert "syncModeController()" not in init
    # Explicit user changes still cross the controller boundary and remain auditable.
    assert "await setModeController(requested)" in init
