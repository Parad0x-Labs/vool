"""DISPATCHER phase 1: a run belongs to its chat, not to whatever chat is on screen.

Before this, the chat page held ONE `history` array, ONE `activeRun`, ONE approval token and ONE
set of recovered-ledger state for the whole application. Navigation was blocked while a turn ran
(`if (busy) return` in openSession/newChat/newChatInProject) precisely because it had to be: with a
single global transcript, letting the user switch chats mid-turn would have appended chat A's answer
to chat B, streamed into B's bubble, and released B's composer when A finished.

Phase 1 replaces that with one bucket per chat plus a `displayedChat` that is presentation only.
Every run carries `run.chatId`, stamped once by `adoptRun()`, and every state mutation on a run's
behalf resolves its bucket through `ownerOf(run)`. The busy guards deliberately stay up this round,
so none of this is user-visible yet -- which is exactly why it has to be proven here, by moving the
displayed chat AWAY from the running one and driving the real functions.

These tests lift the real source out of the emitted page and execute it under node. They do not
re-implement the model in Python and assert against a copy of itself, and they do not assert on the
shape of the source: every assertion below is about what the code actually did to real state.

The scenarios are deliberately hostile rather than sequential-happy-path:

* three runs finishing OUT OF ORDER, with byte-identical answer text, while the display sits on a
  fourth chat -- so attribution cannot be passing by position, recency, or LIFO guessing;
* a chat whose bucket did not exist until its own run completed;
* a completion arriving after the owning chat has already started a SECOND run.

Sabotage (`test_mutation_*`) re-runs the same scenarios against source that has been mutated back to
the old global model, and requires the isolation assertion to fail. A mutation that changes nothing
would mean the scenario never reached the seam.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()

DISPATCHER_BEGIN = "const chatStates = Object.create(null);"
DISPATCHER_END = "// ====================== DISPATCHER: per-chat state — END ======================"
RUNLIFE_BEGIN = "function newRun(chatId) {"
RUNLIFE_END = "// ---- Execution panel ----"


def _script() -> str:
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def _slice(source: str, start: str, end: str) -> str:
    lo = source.index(start)
    hi = source.index(end, lo)
    return source[lo:hi]


def _dispatcher_js() -> str:
    """The per-chat state module, verbatim. It is self-contained by construction."""
    return _slice(_script(), DISPATCHER_BEGIN, DISPATCHER_END)


def _runlife_js() -> str:
    """newRun / releaseComposer / buildCard / renderCard / applyResultSummary / finishRun /
    applyTaskEvent -- the real run lifecycle, verbatim."""
    return _slice(_script(), RUNLIFE_BEGIN, RUNLIFE_END)


# A DOM small enough to read and real enough to catch a cross-chat write: elements track their own
# children, so "did a background run put a node in the displayed chat's log?" is a countable fact
# rather than something inferred from a call that didn't throw.
STUBS = """
globalThis.self = globalThis;
// The verbatim slice references `window.VoolComposerExtras` (guarded) in setDisplayedChat;
// a browser always has `window`, the node harness must too or the guard itself throws.
globalThis.window = globalThis;
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = []; this.textContent = ''; this._html = '';
    this.style = {}; this.attrs = {}; this.hidden = false; this.title = '';
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      toggle: (c, on) => { if (on) this.classList._s.add(c); else this.classList._s.delete(c); },
      contains: (c) => this.classList._s.has(c),
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); if (!v) this.children = []; }
  appendChild(c) { this.children.push(c); return c; }
  // Good enough for buildCard's `card.querySelector('.tc-x')`: every lookup gets its own element,
  // so refs are distinct objects and a write to one cannot be mistaken for a write to another.
  querySelector(sel) { if (!this._q) this._q = {}; return this._q[sel] || (this._q[sel] = new El('div')); }
  querySelectorAll() { return []; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener() {}
  remove() {}
}
const logEl = new El('div');
const sendEl = new El('button');
globalThis.logEl = logEl; globalThis.sendEl = sendEl;
// reflectComposer (in the verbatim slice) guards `if (inputEl)` -- a bare undeclared
// identifier still throws under node, and the page declares inputEl outside the slice.
const inputEl = new El('textarea'); globalThis.inputEl = inputEl;
globalThis.document = {
  createElement: (t) => new El(t),
  body: { classList: { contains: () => false, add: () => {}, remove: () => {}, toggle: () => {} } },
  getElementById: () => null,
  addEventListener: () => {},
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
// node's own crypto.randomUUID is used -- the page reads `self.crypto && crypto.randomUUID`, and
// globalThis.crypto is getter-only here, so stubbing it would fail rather than isolate anything.
globalThis.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
globalThis.AbortController = class { constructor() { this.signal = {}; } abort() {} };
// buildCard installs the live elapsed/ledger intervals on a displayed run. unref() them so a
// finished harness can exit instead of being held open by its own timers.
const _setInterval = globalThis.setInterval;
globalThis.setInterval = (fn, ms) => { const t = _setInterval(fn, ms); if (t && t.unref) t.unref(); return t; };

// Page collaborators this slice calls but does not own. Each records its calls so a test can assert
// that a background run did NOT reach the shared, display-owned surfaces.
const CALLS = { renderPanel: 0, renderCard: 0, pollLedger: 0, activityHistory: 0, permBar: [] };
globalThis.MODE_LABELS = { manual: 'Manual', auto: 'Auto', bypass_permissions: 'Bypass permissions' };
globalThis.VoolCore = class {
  constructor() { this.dots = []; }
  sync() {} dot(k) { this.dots.push(k); } pause() {} resume() {} pulse() {}
  finish() {} start() {} stop() {}
};
globalThis.esc = (t) => String(t == null ? '' : t);
globalThis.pageT = (_key, fallback) => fallback;
globalThis.fmtElapsed = () => '0s';
globalThis.fmtUsd = () => '';
globalThis.stepsRollupText = (steps) => String(steps.length);
globalThis.setMsgTime = (el, ts) => { if (el) el.attrs['data-ts'] = ts; };
globalThis.renderCard = (run) => { CALLS.renderCard++; if (typeof __realRenderCard === 'function') __realRenderCard(run); };
globalThis.renderPanel = () => { CALLS.renderPanel++; };
globalThis.pollLedger = () => { CALLS.pollLedger++; };
globalThis.loadActivityHistory = () => { CALLS.activityHistory++; };
globalThis.showPermBar = (chatId, ev) => { CALLS.permBar.push(chatId); };
globalThis.rememberStickyModel = (chatId, m) => { chatState(chatId).stickyModel = (m && m.model_id) || ''; };
globalThis.openPanel = () => {};
globalThis.setCloudWorking = () => {};
globalThis.pumpQueue = () => {};
globalThis.resumeApprovedTurn = () => {};
globalThis.renderActivityTree = () => '';
globalThis.busy = false;
globalThis.panelTab = 'Activity';
globalThis.out = (o) => console.log(JSON.stringify(o));
"""


def _run(harness: str, *, mutate: str = "") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    source = _dispatcher_js() + "\n" + _runlife_js()
    if mutate:
        source = MUTATIONS[mutate](source)
    # renderCard is stubbed above (it is a paint), but the slice also defines the real one; the local
    # declaration wins inside the slice, which is what we want to exercise.
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(STUBS + source + "\n" + harness)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat page JS failed under node:\n{result.stderr}"
    return json.loads(result.stdout)


# --------------------------------------------------------------------------------------------- #
# Mutations. Each restores one piece of the old global model, and names the test it must break.
# --------------------------------------------------------------------------------------------- #
def _mutate_global_history(source: str) -> str:
    """Restore ONE global transcript: resolve the append target from the display, not the run."""
    old = "function recordAssistantMessage(run, text, metadata) {\n  const owner = ownerOf(run);"
    assert old in source, "recordAssistantMessage no longer has the shape this mutation targets"
    return source.replace(
        old,
        "function recordAssistantMessage(run, text, metadata) {\n  const owner = view;",
    )


def _mutate_global_active_run(source: str) -> str:
    """Restore ONE global active run: ownership resolves to whatever chat is displayed."""
    old_owner = "function ownerOf(run) { return chatState(run && run.chatId); }"
    old_adopt_owner = (
        "function adoptRun(chatId, run) {\n"
        "  run.chatId = String(chatId || '');\n"
        "  const owner = chatState(run.chatId);"
    )
    assert old_owner in source and old_adopt_owner in source, (
        "ownership helpers no longer have the shape this mutation targets"
    )
    return source.replace(old_owner, "function ownerOf(run) { return view; }").replace(
        old_adopt_owner,
        "function adoptRun(chatId, run) {\n"
        "  run.chatId = String(chatId || '');\n"
        "  const owner = view;",
    )


def _mutate_display_scoped_token(source: str) -> str:
    """Restore ONE global approval grant: read the token off the displayed chat."""
    old = "    approval_token: owner.approvalToken,"
    assert old in source, "buildTurnRequestBody no longer has the shape this mutation targets"
    return source.replace(old, "    approval_token: view.approvalToken,")


MUTATIONS = {
    "global_history": _mutate_global_history,
    "global_active_run": _mutate_global_active_run,
    "display_scoped_token": _mutate_display_scoped_token,
}

# Which guard each mutation must break. This is the anti-vacuity contract: a mutation that leaves
# its named guard green means the guard never reached the seam, which is a TEST defect and is
# reported as one by test_every_mutation_turns_its_named_guard_red below.
#
# The map is deliberately not "every mutation vs every test". Two of the scenarios here do not
# exercise the run-registry seam and must not be claimed as covering it:
#   * test_a_late_completion_does_not_overwrite_its_own_chats_newer_run keeps both runs in ONE chat,
#     where per-chat and per-display resolve to the same bucket by construction;
#   * the shared-container and throwaway-bucket tests are about allocation, not ownership.
MUTATION_GUARDS: dict[str, tuple[str, ...]] = {
    "global_history": (
        "test_out_of_order_completions_land_in_the_chat_that_asked",
        "test_a_completion_reaches_a_chat_whose_bucket_did_not_exist_yet",
    ),
    "global_active_run": (
        "test_a_background_completion_cannot_replace_the_displayed_chats_run",
        "test_a_failure_in_a_background_chat_leaves_the_displayed_chat_untouched",
    ),
    "display_scoped_token": (
        "test_an_approval_granted_in_one_chat_never_rides_another_chats_request",
    ),
}


def _rc(history: list) -> list:
    """(role, content) pairs. Transcript entries also carry a `ts` so re-opening a chat restores the
    timestamps SHOWRUNNER shows; these tests are about WHICH chat got WHAT, not about that field."""
    return [(m["role"], m["content"]) for m in history]


# --------------------------------------------------------------------------------------------- #
# A -- a completion lands in the chat that asked, not the chat on screen
# --------------------------------------------------------------------------------------------- #
# Three concurrent runs, IDENTICAL answer text, finishing in an order that matches neither the order
# they started nor the order they were displayed, while the display sits on a fourth, unrelated chat.
# Identical text is the point: if attribution were positional, recency-based, or LIFO, every one of
# these would still "look right" with distinct strings.
_OUT_OF_ORDER_HARNESS = """
setDisplayedChat('chat-A');
recordUserMessage('chat-A', 'ask A');
const runA = adoptRun('chat-A', newRun('chat-A'));

setDisplayedChat('chat-B');
recordUserMessage('chat-B', 'ask B');
const runB = adoptRun('chat-B', newRun('chat-B'));

setDisplayedChat('chat-C');
recordUserMessage('chat-C', 'ask C');
const runC = adoptRun('chat-C', newRun('chat-C'));

// The user walks away to a chat that has never run anything.
setDisplayedChat('chat-D');

// Same bytes for every answer, delivered middle-first, then last, then first.
recordAssistantMessage(runB, 'the same answer');
finishRun(runB, 'completed', 'Complete');
recordAssistantMessage(runC, 'the same answer');
finishRun(runC, 'completed', 'Complete');
recordAssistantMessage(runA, 'the same answer');
finishRun(runA, 'completed', 'Complete');

out({
  a: chatState('chat-A').history,
  b: chatState('chat-B').history,
  c: chatState('chat-C').history,
  d: chatState('chat-D').history,
  displayed: displayedChat,
  statuses: [chatState('chat-A').run.status, chatState('chat-B').run.status, chatState('chat-C').run.status],
  dRun: chatState('chat-D').run,
});
"""


def test_out_of_order_completions_land_in_the_chat_that_asked() -> None:
    data = _run(_OUT_OF_ORDER_HARNESS)
    for key in ("a", "b", "c"):
        assert _rc(data[key]) == [
            ("user", "ask " + key.upper()),
            ("assistant", "the same answer"),
        ], f"chat-{key.upper()} did not get exactly its own question and its own answer"
    # The chat the user was actually looking at received nothing at all.
    assert data["d"] == []
    assert data["dRun"] is None
    assert data["displayed"] == "chat-D"
    assert data["statuses"] == ["completed", "completed", "completed"]


def test_a_completion_reaches_a_chat_whose_bucket_did_not_exist_yet() -> None:
    # The owning bucket is created on demand by chatState(). A run for a chat that was never opened
    # in this page session must still deliver -- not fall back to the displayed chat.
    data = _run(
        """
        setDisplayedChat('chat-visible');
        const run = adoptRun('chat-never-opened', newRun('chat-never-opened'));
        recordAssistantMessage(run, 'answer for a chat nobody opened');
        finishRun(run, 'completed', 'Complete');
        out({
          hidden: chatState('chat-never-opened').history,
          visible: chatState('chat-visible').history,
          visibleRun: chatState('chat-visible').run,
        });
        """
    )
    assert _rc(data["hidden"]) == [("assistant", "answer for a chat nobody opened")]
    assert data["visible"] == []
    assert data["visibleRun"] is None


def test_a_late_completion_does_not_overwrite_its_own_chats_newer_run() -> None:
    # The stale-stream case, per chat: chat A's first run finishes AFTER A has already started a
    # second. The late run must record its own result without becoming the chat's current run again,
    # and must not release the composer on the newer one's behalf.
    data = _run(
        """
        setDisplayedChat('chat-A');
        const first = adoptRun('chat-A', newRun('chat-A'));
        const second = adoptRun('chat-A', newRun('chat-A'));
        recordAssistantMessage(first, 'late answer');
        finishRun(first, 'completed', 'Complete');
        releaseComposer(first);
        out({
          current: chatState('chat-A').run.turnId,
          secondTurn: second.turnId,
          firstReleased: !!first.released,
          secondReleased: !!second.released,
          secondStatus: second.status,
          history: chatState('chat-A').history,
        });
        """
    )
    assert data["current"] == data["secondTurn"], "the newer run must remain the chat's current run"
    assert data["firstReleased"] is False, "a superseded run must not release the composer"
    assert data["secondReleased"] is False
    assert data["secondStatus"] == "running"
    assert _rc(data["history"]) == [("assistant", "late answer")]


# --------------------------------------------------------------------------------------------- #
# B -- a failure belongs to the chat that failed
# --------------------------------------------------------------------------------------------- #
def test_a_failure_in_a_background_chat_leaves_the_displayed_chat_untouched() -> None:
    # A's run is adopted while B is ALREADY on screen -- the genuine background case. Adopting in
    # lockstep with the display (start A, switch, start B) would let a display-resolved registry
    # pass this by coincidence; it has to be reached with the display standing still.
    data = _run(
        """
        setDisplayedChat('chat-B');
        const runB = adoptRun('chat-B', newRun('chat-B'));
        const runA = adoptRun('chat-A', newRun('chat-A'));
        // A dies while B is on screen and mid-flight.
        runA.error = 'Read timed out';
        finishRun(runA, 'failed', 'Connection error');
        out({
          aStatus: runA.status, aEnded: runA.ended, aSummary: runA.endSummary,
          bStatus: runB.status, bEnded: runB.ended, bSummary: runB.endSummary || '',
          bIsStillCurrent: chatState('chat-B').run === runB,
          bHistory: chatState('chat-B').history,
          bError: runB.error || '',
        });
        """
    )
    assert data["aStatus"] == "failed" and data["aEnded"] is True
    assert data["aSummary"] == "Connection error"
    assert data["bStatus"] == "running", "B must still be running after A failed"
    assert data["bEnded"] is False
    assert data["bSummary"] == ""
    assert data["bError"] == ""
    assert data["bIsStillCurrent"] is True
    assert data["bHistory"] == []


# --------------------------------------------------------------------------------------------- #
# C -- an approval granted in one chat cannot ride out on another chat's turn
# --------------------------------------------------------------------------------------------- #
def test_an_approval_granted_in_one_chat_never_rides_another_chats_request() -> None:
    # Asserted on the real outbound body, not on an intermediate variable: this is exactly the
    # payload the server would receive. The grant, the mode, the autonomy flag, the bypass token and
    # the transcript must all come from the chat that owns the run.
    data = _run(
        """
        setDisplayedChat('chat-A');
        chatState('chat-A').mode = 'bypass_permissions';
        chatState('chat-A').bypassGrant = { token: 'bypass-A' };
        setApprovalToken('chat-A', 'approval-A');
        recordUserMessage('chat-A', 'ask A');
        const runA = adoptRun('chat-A', newRun('chat-A'));

        setDisplayedChat('chat-B');
        recordUserMessage('chat-B', 'ask B');
        const runB = adoptRun('chat-B', newRun('chat-B'));

        // B is the chat on screen. Build BOTH bodies from here.
        const bodyB = buildTurnRequestBody(runB, 'vool');
        const bodyA = buildTurnRequestBody(runA, 'vool');
        out({ bodyA, bodyB, tokenA: approvalTokenFor('chat-A'), tokenB: approvalTokenFor('chat-B') });
        """
    )
    body_b, body_a = data["bodyB"], data["bodyA"]
    # The displayed chat's request carries none of the other chat's grants or posture.
    assert body_b["approval_token"] == ""
    assert body_b["bypass_token"] == ""
    assert body_b["mode"] == "manual"
    assert body_b["autonomy"] == ""
    assert body_b["session_id"] == "chat-B"
    assert _rc(body_b["messages"]) == [("user", "ask B")]
    # The granting chat's own request still carries them, built from the same screen.
    assert body_a["approval_token"] == "approval-A"
    assert body_a["bypass_token"] == "bypass-A"
    assert body_a["mode"] == "bypass_permissions"
    assert body_a["autonomy"] == "auto"
    assert body_a["session_id"] == "chat-A"
    assert _rc(body_a["messages"]) == [("user", "ask A")]
    # Building B's request did not spend A's grant.
    assert data["tokenA"] == "approval-A"
    assert data["tokenB"] == ""


def test_request_body_model_selection_is_explicit_and_has_safe_defaults() -> None:
    data = _run(
        """
        const run = adoptRun('chat-A', newRun('chat-A'));
        out({
          automatic: buildTurnRequestBody(run, 'vool').model_selection,
          sticky: buildTurnRequestBody(run, 'provider/free-model').model_selection,
          pinned: buildTurnRequestBody(run, 'provider/paid-model', 'pin').model_selection,
        });
        """
    )

    assert data == {"automatic": "auto", "sticky": "sticky", "pinned": "pin"}


def test_a_permission_prompt_is_recorded_against_the_chat_that_raised_it() -> None:
    data = _run(
        """
        setDisplayedChat('chat-B');
        const runA = adoptRun('chat-A', newRun('chat-A'));
        applyTaskEvent(runA, { type: 'permission.required', summary: 'Approve one write', stage: 'Editing' });
        out({
          permBarChats: CALLS.permBar,
          aPermission: runA.permission,
          bRun: chatState('chat-B').run,
        });
        """
    )
    # showPermBar is addressed with the raising chat's id -- the real implementation then declines to
    # paint because that chat is not displayed.
    assert data["permBarChats"] == ["chat-A"]
    assert data["aPermission"] is True
    assert data["bRun"] is None


# --------------------------------------------------------------------------------------------- #
# D -- Activity events cannot reach another chat's run
# --------------------------------------------------------------------------------------------- #
def test_activity_events_for_a_background_run_never_touch_the_displayed_run() -> None:
    data = _run(
        """
        setDisplayedChat('chat-B');
        const runA = adoptRun('chat-A', newRun('chat-A'));
        const runB = adoptRun('chat-B', newRun('chat-B'));
        applyTaskEvent(runA, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' });
        applyTaskEvent(runA, { type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading', path: '/tmp/a.txt' });
        applyTaskEvent(runA, { type: 'tool.started', tool: 'run_tests', summary: 'Testing', stage: 'Testing' });
        applyTaskEvent(runA, { type: 'tool.failed', tool: 'run_tests', summary: 'Tests failed', stage: 'Testing' });
        applyTaskEvent(runA, { type: 'model.changed', model: { lane: 'cloud', model_id: 'x/y:free', paid: false } });
        out({
          aSteps: runA.steps.map((s) => [s.tool, s.status]),
          aFiles: Object.keys(runA.files),
          aEvents: runA.events.length,
          bSteps: runB.steps.length, bFiles: Object.keys(runB.files), bEvents: runB.events.length,
          bModel: runB.model, bStage: runB.stage,
          aStickyModel: chatState('chat-A').stickyModel,
          bStickyModel: chatState('chat-B').stickyModel,
        });
        """
    )
    assert data["aSteps"] == [["read_file", "completed"], ["run_tests", "failed"]]
    assert data["aFiles"] == ["/tmp/a.txt"]
    assert data["aEvents"] == 5
    # Nothing at all crossed into the displayed chat's run.
    assert data["bSteps"] == 0
    assert data["bFiles"] == []
    assert data["bEvents"] == 0
    assert data["bModel"] is None
    assert data["bStage"] == "Queued"
    # Model stickiness is chat-scoped too: A's cloud model must not become B's.
    assert data["aStickyModel"] == "x/y:free"
    assert data["bStickyModel"] == ""


# --------------------------------------------------------------------------------------------- #
# E -- a completion cannot replace another chat's current run
# --------------------------------------------------------------------------------------------- #
def test_a_background_completion_cannot_replace_the_displayed_chats_run() -> None:
    data = _run(
        """
        setDisplayedChat('chat-B');
        const runB = adoptRun('chat-B', newRun('chat-B'));
        const runA = adoptRun('chat-A', newRun('chat-A'));
        applyTaskEvent(runA, { type: 'tool.started', tool: 'edit_file', summary: 'Editing', stage: 'Editing' });
        applyTaskEvent(runA, { type: 'tool.completed', tool: 'edit_file', summary: 'Edited', stage: 'Editing' });
        applyTaskEvent(runA, { type: 'task.completed', summary: 'Complete', ts: '2026-08-07T10:00:00Z' });
        out({
          bIsStillCurrent: chatState('chat-B').run === runB,
          bStatus: runB.status, bEnded: runB.ended,
          bResultParts: runB.resultParts || null,
          aStatus: runA.status, aEndedAt: runA.endedAt,
          aResultParts: runA.resultParts,
          aIsCurrentOfA: chatState('chat-A').run === runA,
        });
        """
    )
    assert data["bIsStillCurrent"] is True
    assert data["bStatus"] == "running" and data["bEnded"] is False
    assert data["bResultParts"] is None, "the displayed run must not acquire another run's result"
    assert data["aStatus"] == "completed"
    assert data["aEndedAt"] == "2026-08-07T10:00:00Z", "the server ts must survive to the owning run"
    assert data["aResultParts"] == ["Tool action completed", "1 action"]
    assert data["aIsCurrentOfA"] is True


# --------------------------------------------------------------------------------------------- #
# F -- no two chats and no two runs share a mutable container
# --------------------------------------------------------------------------------------------- #
def test_buckets_and_runs_never_share_mutable_containers() -> None:
    data = _run(
        """
        const a = chatState('chat-A'), b = chatState('chat-B');
        const runA = newRun('chat-A'), runB = newRun('chat-B');
        const distinct = {
          history: a.history !== b.history,
          pins: a.pins !== b.pins,
          recoveredLedger: a.recoveredLedger !== b.recoveredLedger,
          recoveredActivityOpen: a.recoveredActivityOpen !== b.recoveredActivityOpen,
          steps: runA.steps !== runB.steps,
          events: runA.events !== runB.events,
          files: runA.files !== runB.files,
          tests: runA.tests !== runB.tests,
          activityOpen: runA.activityOpen !== runB.activityOpen,
          eventSeen: runA.eventSeen !== runB.eventSeen,
          ledger: runA.ledger !== runB.ledger,
          ledgerSeen: runA.ledgerSeen !== runB.ledgerSeen,
          turnId: runA.turnId !== runB.turnId,
        };
        // Aliasing would not show up in an identity check alone if a getter handed back a copy, so
        // mutate every container and re-read the other side.
        a.history.push({ role: 'user', content: 'x' });
        a.pins.push({ id: 'p' });
        a.recoveredLedger.push({ seq: 1 });
        a.recoveredActivityOpen.node = true;
        runA.steps.push({ tool: 't', status: 'running' });
        runA.events.push({ type: 'x' });
        runA.files['/f'] = 1;
        runA.tests.total = 9;
        runA.activityOpen.n = true;
        runA.eventSeen.k = 1;
        runA.ledger.push({ seq: 2 });
        runA.ledgerSeen.k = 1;
        out({
          distinct,
          bAfter: {
            history: b.history.length, pins: b.pins.length,
            recoveredLedger: b.recoveredLedger.length,
            recoveredActivityOpen: Object.keys(b.recoveredActivityOpen).length,
            steps: runB.steps.length, events: runB.events.length,
            files: Object.keys(runB.files).length, testsTotal: runB.tests.total,
            activityOpen: Object.keys(runB.activityOpen).length,
            eventSeen: Object.keys(runB.eventSeen).length,
            ledger: runB.ledger.length, ledgerSeen: Object.keys(runB.ledgerSeen).length,
          },
        });
        """
    )
    assert all(data["distinct"].values()), f"shared container(s): {[k for k, v in data['distinct'].items() if not v]}"
    assert data["bAfter"] == {
        "history": 0, "pins": 0, "recoveredLedger": 0, "recoveredActivityOpen": 0,
        "steps": 0, "events": 0, "files": 0, "testsTotal": 0,
        "activityOpen": 0, "eventSeen": 0, "ledger": 0, "ledgerSeen": 0,
    }


def test_an_unidentified_caller_gets_a_throwaway_bucket_not_a_shared_one() -> None:
    # A missing chat id must never become a channel between two chats. Two calls with an empty id
    # get two different buckets, and neither is any real chat's.
    data = _run(
        """
        const one = chatState(''), two = chatState('');
        one.history.push({ role: 'user', content: 'leak?' });
        setDisplayedChat('chat-A');
        out({ same: one === two, twoLen: two.history.length, aLen: chatState('chat-A').history.length });
        """
    )
    assert data["same"] is False
    assert data["twoLen"] == 0
    assert data["aLen"] == 0


# --------------------------------------------------------------------------------------------- #
# DOM ownership -- a background run updates state and paints nothing
# --------------------------------------------------------------------------------------------- #
def test_a_background_run_updates_state_but_writes_no_dom() -> None:
    data = _run(
        """
        setDisplayedChat('chat-A');
        const runA = adoptRun('chat-A', newRun('chat-A'));
        buildCard(runA, null);                       // A is displayed -> a real card
        const logAfterA = logEl.children.length;

        const runB = adoptRun('chat-B', newRun('chat-B'));
        const cardB = buildCard(runB, null);         // B is NOT displayed -> nothing painted
        applyTaskEvent(runB, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' });
        applyTaskEvent(runB, { type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading' });
        const panelsBefore = CALLS.renderPanel;
        finishRun(runB, 'completed', 'Complete', '2026-08-07T11:00:00Z');
        out({
          aHasCard: !!runA.card, aHasRefs: !!runA.refs,
          bCard: cardB, bHasCard: !!runB.card, bHasRefs: !!runB.refs,
          logAfterA, logFinal: logEl.children.length,
          bSteps: runB.steps.length, bStatus: runB.status, bEndedAt: runB.endedAt,
          bResultParts: runB.resultParts,
          panelRepaints: CALLS.renderPanel - panelsBefore,
        });
        """
    )
    assert data["aHasCard"] is True and data["aHasRefs"] is True
    assert data["bCard"] is None and data["bHasCard"] is False and data["bHasRefs"] is False
    # Exactly one card in the log, and the background run added nothing to it.
    assert data["logAfterA"] == 1
    assert data["logFinal"] == 1
    # ...while its own state is complete.
    assert data["bSteps"] == 1
    assert data["bStatus"] == "completed"
    assert data["bEndedAt"] == "2026-08-07T11:00:00Z"
    assert data["bResultParts"] == ["Tool action completed", "1 action"]
    # The displayed chat's panel was not repainted on another chat's completion.
    assert data["panelRepaints"] == 0


# --------------------------------------------------------------------------------------------- #
# Sabotage -- each mutation must turn a specific scenario red, and name which one
# --------------------------------------------------------------------------------------------- #
def test_mutation_one_global_history_contaminates_across_chats() -> None:
    # Restoring a single, display-resolved transcript is the pre-phase-1 model. Every answer must
    # then pile into whichever chat is on screen.
    data = _run(_OUT_OF_ORDER_HARNESS, mutate="global_history")
    assert _rc(data["d"]) == [("assistant", "the same answer")] * 3, (
        "MUTATION DID NOT BITE: with one global transcript the three answers must land in the "
        "displayed chat. test_out_of_order_completions_land_in_the_chat_that_asked is the guard."
    )
    assert _rc(data["a"]) == [("user", "ask A")]
    assert _rc(data["b"]) == [("user", "ask B")]
    assert _rc(data["c"]) == [("user", "ask C")]


def test_mutation_one_global_active_run_breaks_run_isolation() -> None:
    # Restoring a single, display-resolved active run: adopting a run for a background chat now
    # overwrites the displayed chat's run, which is the state corruption the busy guards exist for.
    data = _run(
        """
        setDisplayedChat('chat-B');
        const runB = adoptRun('chat-B', newRun('chat-B'));
        const runA = adoptRun('chat-A', newRun('chat-A'));
        out({ bIsStillCurrent: chatState('chat-B').run === runB, bTurn: runB.turnId, aTurn: runA.turnId });
        """,
        mutate="global_active_run",
    )
    assert data["bIsStillCurrent"] is False, (
        "MUTATION DID NOT BITE: with one global active run, adopting chat A's run must displace "
        "chat B's. test_a_background_completion_cannot_replace_the_displayed_chats_run is the guard."
    )


def test_mutation_display_scoped_token_leaks_a_grant_into_another_chat() -> None:
    data = _run(
        """
        setDisplayedChat('chat-A');
        setApprovalToken('chat-A', 'approval-A');
        const runB = adoptRun('chat-B', newRun('chat-B'));
        out({ token: buildTurnRequestBody(runB, 'vool').approval_token });
        """,
        mutate="display_scoped_token",
    )
    assert data["token"] == "approval-A", (
        "MUTATION DID NOT BITE: reading the grant off the displayed chat must attach chat A's "
        "approval to chat B's request. "
        "test_an_approval_granted_in_one_chat_never_rides_another_chats_request is the guard."
    )


def test_every_mutation_turns_its_named_guard_red() -> None:
    """The anti-vacuity gate: a mutation that changes no test result proves nothing.

    Each mutation is applied and every guard named for it in MUTATION_GUARDS is re-run against the
    mutated source. A guard that stays green did not reach the seam it claims to protect, and is
    reported here by name rather than being left to look like coverage.
    """
    import sys

    module = sys.modules[__name__]
    original_run = module._run
    survived: list[str] = []
    try:
        for mutation, guards in MUTATION_GUARDS.items():
            module._run = (lambda m: (lambda harness, mutate="": original_run(harness, mutate=m)))(mutation)
            for guard in guards:
                try:
                    getattr(module, guard)()
                except AssertionError:
                    continue
                survived.append(f"{mutation} -> {guard}")
    finally:
        module._run = original_run
    assert survived == [], (
        "these guards stayed GREEN under a mutation that removes the property they claim to "
        f"protect, so they never reached that seam: {survived}"
    )


# --------------------------------------------------------------------------------------------- #
# Phase boundary -- the navigation guards stay up this round, on purpose
# --------------------------------------------------------------------------------------------- #
def test_the_navigation_busy_guards_are_gone() -> None:
    """Phase 2's point: navigation no longer depends on whether anything is running.

    Phase 1 required these four `busy` gates to be present, because the state model underneath them
    could not survive a mid-run switch. Phase 2 replaced that model, so the gates are removed --
    and the process-global `busy` flag with them. What remains is `isChatBusy(chatId)`, which
    answers only "does THIS chat run the next message now, or queue it".
    """
    source = _script()
    for fn in ("async function openSession(id) {", "function newChat() {", "async function newChatInProject(pid) {"):
        assert fn in source, fn
        body = source[source.index(fn) + len(fn):]
        head = body[: body.index("\n}\n")] if "\n}\n" in body else body[:2000]
        assert "if (busy) return;" not in head, f"{fn} still refuses to navigate while something runs"
    # The Activity-panel history card navigates unconditionally now.
    assert "if (sid && sid !== displayedChat) openSession(sid);" in source
    # No process-global flag survives: every remaining mention is a comment or the per-chat helper.
    code = [
        line for line in source.splitlines()
        if re.search(r"(?<![.\w])busy(?![\w])", line.replace("'aria-busy'", "''")) and not line.strip().startswith("//")
    ]
    offenders = [ln.strip() for ln in code if "isChatBusy" not in ln and "busyChatIds" not in ln and "ledgerBusy" not in ln]
    assert offenders == [], f"a global busy flag is still in play: {offenders}"


def test_is_chat_busy_is_per_chat_and_never_global() -> None:
    data = _run(
        """
        setDisplayedChat('chat-A');
        const runA = adoptRun('chat-A', newRun('chat-A'));
        const before = { a: isChatBusy('chat-A'), b: isChatBusy('chat-B'), all: busyChatIds() };
        setDisplayedChat('chat-B');
        const afterSwitch = { a: isChatBusy('chat-A'), b: isChatBusy('chat-B') };
        const runB = adoptRun('chat-B', newRun('chat-B'));
        const both = { a: isChatBusy('chat-A'), b: isChatBusy('chat-B'), all: busyChatIds().sort() };
        finishRun(runA, 'completed', 'Complete'); releaseComposer(runA);
        const afterA = { a: isChatBusy('chat-A'), b: isChatBusy('chat-B') };
        out({ before, afterSwitch, both, afterA });
        """
    )
    assert data["before"] == {"a": True, "b": False, "all": ["chat-A"]}
    # Switching the display changes nothing about who is busy -- that is the whole point.
    assert data["afterSwitch"] == {"a": True, "b": False}
    assert data["both"] == {"a": True, "b": True, "all": ["chat-A", "chat-B"]}
    # A finishing in the background frees only A's slot.
    assert data["afterA"] == {"a": False, "b": True}


def test_no_execution_path_resolves_ownership_from_the_display() -> None:
    """The invariant, checked where it is cheapest to check: the run lifecycle never reads the
    display to decide WHAT TO MUTATE.

    `isDisplayed(run.chatId)` and `view.*` reads are PAINT decisions and are allowed -- they answer
    "is anyone looking at this?", never "whose state is this?". A bare `displayedChat` read is the
    dangerous shape, because it names the screen rather than the run, so it is banned outright with
    one exemption: `reflectComposer`, whose entire job is to paint the Send button for whichever
    chat is on screen. The exemption is listed by name here so it cannot silently grow.
    """
    runlife = _runlife_js()
    paint_only_exemptions = {
        ": (isChatBusy(displayedChat) ? pageT('composer.queue', 'Queue') : pageT('composer.send', 'Send'));",
    }
    offenders = [
        line.strip()
        for line in runlife.splitlines()
        if "displayedChat" in line
        and not line.strip().startswith("//")
        and line.strip() not in paint_only_exemptions
    ]
    assert offenders == [], f"run lifecycle reads the display to decide ownership: {offenders}"
    # And the exemption must still be real -- a stale entry would silently widen the ban's hole.
    for exempt in paint_only_exemptions:
        assert exempt in runlife, f"exemption no longer present, remove it: {exempt}"
