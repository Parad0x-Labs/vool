"""Activity shows the chat it says it is showing, not just the latest turn.

Investigated 2026-08-12 against the live daemon. The backend was fine: `append_runtime_event` is a
pure INSERT with a monotonic per-session seq, nothing prunes, and one real chat held 307 events
across 12 turns. The panel rendered 13 of them.

Two independent defects produced the reported symptom -- "latest-turn-heavy, repeated mode_changed,
short work log":

1. SCOPE. Both readers were turn-scoped while the selector above them said "Current chat".
   `pollLedger` kept `e.client_turn_id === run.turnId`; `loadRecoveredLedger` kept only the LAST
   turn, falling back to `slice(-40)`. Measured on the 307-event chat: 13 rendered, 294 (95%)
   discarded with nothing on screen to say so.

2. UNTAGGED LEAK. `mode_changed` and `permission_*` carry no turn id, and `pollLedger` kept every
   untagged row it drained -- which is the whole chat's history. Measured on session
   dec623f743b6d6ff7562: 12 mode_changed rows from earlier in the chat replayed into every later
   turn, so a turn with little real work showed as mostly mode changes.

A third, smaller one showed up while fixing the first: grouping by ADJACENT rows split a turn that
had an untagged row in the middle of it, reporting 17 turns where the chat had 12.
"""

from __future__ import annotations

import json

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

TURN_A = "turn-aaaa"
TURN_B = "turn-bbbb"
TURN_C = "turn-cccc"


def _event(seq: int, event_type: str, message: str, turn: str | None = None) -> dict:
    row = {"seq": seq, "event_type": event_type, "message": message}
    if turn:
        row["client_turn_id"] = turn
    return row


# Three turns, with untagged rows before, between, and INSIDE a turn -- the shape that split a turn
# in two under adjacency grouping.
LEDGER = [
    _event(1, "mode_changed", "Mode changed to Manual"),
    _event(2, "task_received", "Received request: first", TURN_A),
    _event(3, "tool_selected", "Running workspace.read_file", TURN_A),
    _event(4, "tool_executed", "Read alpha.txt", TURN_A),
    _event(5, "task_completed", "Task completed", TURN_A),
    _event(6, "mode_changed", "Mode changed to Auto"),
    _event(7, "mode_changed", "Mode changed to Manual"),
    _event(8, "task_received", "Received request: second", TURN_B),
    _event(9, "tool_selected", "Running workspace.write_file", TURN_B),
    _event(10, "permission_approved", "Approved write"),          # untagged, INSIDE turn B
    _event(11, "tool_executed", "Wrote beta.txt", TURN_B),
    _event(12, "task_completed", "Task completed", TURN_B),
    _event(13, "task_received", "Received request: third", TURN_C),
    _event(14, "model.call_started", "Model call started with ollama-local:qwen2.5:7b.", TURN_C),
    _event(15, "task_completed", "Task completed", TURN_C),
]


def _launch():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    return served_browser.launch_chromium()


def _route_factory(ledger):
    def _route(route):
        request = route.request
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
            return
        if "/api/runtime/events" in request.url:
            from urllib.parse import parse_qs, urlparse

            after = int((parse_qs(urlparse(request.url).query).get("after") or ["0"])[0])
            page = [e for e in ledger if int(e["seq"]) > after][:200]
            nxt = max([int(e["seq"]) for e in page], default=after)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"events": page, "next_after": nxt}))
            return
        route.fulfill(status=200, content_type="application/json", body="{}")
    return _route


OPEN_ACTIVITY = """async () => {
    await loadRecoveredLedger();
    document.body.classList.add('panel-open');
    panelTab = 'Activity';
    buildTabs();
    renderPanel();
    await new Promise((r) => setTimeout(r, 120));
    const sections = [...xpBodyEl.querySelectorAll('.xp-turn')];
    return {
      chatLedger: (view.chatLedger || []).length,
      note: (xpBodyEl.querySelector('.xp-note') || {}).textContent || '',
      labels: sections.map((s) => s.querySelector('summary').textContent),
      openCount: sections.filter((s) => s.open).length,
      openLabel: (sections.find((s) => s.open) || {}).textContent ? sections.find((s) => s.open).querySelector('summary').textContent : '',
    };
}"""


@pytest.fixture(scope="module")
def session():
    ctx, browser = _launch()
    try:
        page = browser.new_page()
        page.on("pageerror", lambda e: None)
        page.route("**/*", _route_factory(LEDGER))
        page.set_viewport_size({"width": 1280, "height": 900})
        yield page
    finally:
        browser.close()
        ctx.stop()


@pytest.fixture(scope="module")
def opened(session):
    session.goto("http://vool.test/chat")
    session.wait_for_timeout(200)
    return session.evaluate(OPEN_ACTIVITY)


# ---------------------------------------------------------------- scope


def test_the_whole_chat_is_kept_not_just_the_last_turn(opened) -> None:
    assert opened["chatLedger"] == len(LEDGER), (
        f"the panel kept {opened['chatLedger']} of {len(LEDGER)} events"
    )


def test_every_turn_gets_its_own_section_newest_first(opened) -> None:
    turn_labels = [l for l in opened["labels"] if l.startswith("Turn ")]
    assert len(turn_labels) == 3, f"expected 3 turns, got {turn_labels}"
    assert turn_labels[0].startswith("Turn 3"), f"newest turn is not first: {turn_labels}"
    assert turn_labels[-1].startswith("Turn 1"), f"oldest turn is not last: {turn_labels}"


def test_only_the_newest_turn_is_open_by_default(opened) -> None:
    """Chat-wide evidence is useless if it arrives as a wall of open trees."""
    assert opened["openCount"] == 1, opened["labels"]
    assert opened["openLabel"].startswith("Turn 3")


def test_the_header_states_what_is_on_screen(opened) -> None:
    assert "This chat" in opened["note"]
    assert "3 turns" in opened["note"], opened["note"]
    assert f"{len(LEDGER)} events" in opened["note"], opened["note"]


# ---------------------------------------------------------------- the untagged leak


def test_a_turn_interrupted_by_an_untagged_event_is_still_one_turn(opened) -> None:
    """Adjacency grouping reported 17 turns for a chat that had 12."""
    turn_labels = [l for l in opened["labels"] if l.startswith("Turn ")]
    assert len(turn_labels) == 3, f"turn B was split by the approval inside it: {opened['labels']}"


def test_untagged_events_are_shown_between_turns_not_inside_one(opened) -> None:
    between = [l for l in opened["labels"] if l.startswith("Between turns")]
    assert between, f"the untagged mode changes vanished: {opened['labels']}"
    # Two mode changes sit together between turn A and turn B; they are one block, not two.
    assert any("2 events" in l for l in between), between


def test_history_is_not_replayed_into_a_live_turn(session) -> None:
    """The reported 'repeated mode_changed': every untagged row in the chat landed in every turn."""
    session.goto("http://vool.test/chat")
    session.wait_for_timeout(200)
    result = session.evaluate(
        """async () => {
            await loadRecoveredLedger();          // the chat already has history, incl. untagged rows
            const run = adoptRun(displayedChat, newRun(displayedChat));
            run.turnId = 'turn-cccc';
            const baseline = run.ledgerBaseline;
            await pollLedger(run);
            const types = run.ledger.map((e) => e.event_type);
            return { baseline, ledger: run.ledger.length, types,
                     inheritedModeChanges: types.filter((t) => t === 'mode_changed').length,
                     inheritedApprovals: types.filter((t) => t === 'permission_approved').length };
        }"""
    )
    assert result["baseline"] == 15, f"the run did not record where the chat's history ended: {result}"
    assert result["inheritedModeChanges"] == 0, (
        f"older mode changes were replayed into this turn: {result['types']}"
    )
    assert result["inheritedApprovals"] == 0, f"an older approval was replayed: {result['types']}"
    # A newly minted run starts at the chat tail. Even reusing an old turn id in this adversarial
    # fixture cannot make historical rows re-enter the live run projection.
    assert result["ledger"] == 0, result["types"]


# ---------------------------------------------------------------- truncation is visible


def test_a_chat_past_the_page_ceiling_says_so(session) -> None:
    """Eight pages of 200 is the loader's ceiling; it used to stop there silently.

    Re-routes the shared page rather than launching a second browser: sync Playwright refuses a
    second instance on one thread, and a test that launched its own SKIPPED instead of running.
    """
    big = [_event(i, "tool_executed", f"row {i}", f"turn-{i // 10}") for i in range(1, 1801)]
    session.unroute("**/*")
    try:
        session.route("**/*", _route_factory(big))
        session.goto("http://vool.test/chat")
        session.wait_for_timeout(250)
        result = session.evaluate(OPEN_ACTIVITY)
    finally:
        session.unroute("**/*")
        session.route("**/*", _route_factory(LEDGER))
    assert result["chatLedger"] == 1600, f"the ceiling moved: {result['chatLedger']}"
    assert "not loaded" in result["note"], f"the ceiling is silent: {result['note']!r}"
    assert "1600" in result["note"], result["note"]


# ---------------------------------------------------------------- bulk actions and copy


def test_expand_and_collapse_reach_every_turn(session) -> None:
    """A section the user never touched has no entry in the open-state store."""
    session.goto("http://vool.test/chat")
    session.wait_for_timeout(200)
    result = session.evaluate(
        """async () => {
            await loadRecoveredLedger();
            document.body.classList.add('panel-open'); panelTab='Activity'; buildTabs(); renderPanel();
            await new Promise((r) => setTimeout(r, 120));
            const count = () => [...xpBodyEl.querySelectorAll('.xp-turn')];
            const total = count().length;
            setActivityTreeOpen(true); await new Promise((r) => setTimeout(r, 120));
            const opened = count().filter((s) => s.open).length;
            setActivityTreeOpen(false); await new Promise((r) => setTimeout(r, 120));
            const closed = count().filter((s) => s.open).length;
            return { total, opened, closed };
        }"""
    )
    assert result["total"] >= 4
    assert result["opened"] == result["total"], f"Expand all reached {result['opened']} of {result['total']}"
    assert result["closed"] == 0, "Collapse all left sections open"


def test_copy_carries_every_turn_including_the_ones_that_ran_no_tool(session) -> None:
    """Copy used to serialize one tree; with the chat on screen that is one turn's evidence."""
    session.goto("http://vool.test/chat")
    session.wait_for_timeout(200)
    text = session.evaluate(
        """async () => {
            await loadRecoveredLedger();
            document.body.classList.add('panel-open'); panelTab='Activity'; buildTabs(); renderPanel();
            await new Promise((r) => setTimeout(r, 120));
            return panelEvidenceText();
        }"""
    )
    for turn in ("Turn 1", "Turn 2", "Turn 3"):
        assert turn in text, f"{turn} missing from copied evidence"
    assert "alpha.txt" in text and "beta.txt" in text, "tool evidence missing from the copy"
    # Each turn's heading is on its own line -- an escaped "\\n" in the joiner would run the whole
    # chat together as one line, which is what shipped the first time this was written.
    assert "\\n" not in text, "the copy contains a literal backslash-n instead of a line break"
    assert text.count("\n") > 10, "the copy is not line-broken"


# ---------------------------------------------------------------- the label now matches the content


def test_the_scope_selector_still_defaults_to_this_chat() -> None:
    assert "const PANEL_SCOPES = [['chat', 'Current chat'], ['project', 'Current project'], ['all', 'All activity']];" in HTML
    assert "let panelScope = localStorage.getItem('vool_panel_scope') || 'chat'" in HTML


def test_the_turn_filter_that_caused_this_is_gone_from_the_activity_reader() -> None:
    """`loadRecoveredLedger` keeps the whole chat; the last-turn slice is a derived value now."""
    assert "for (const event of more) appendToChatLedger(target, event);" in HTML
    assert "target.chatLedgerCursor = Math.max" in HTML
    assert "function renderChatActivity" in HTML
    assert "function groupLedgerByTurn" in HTML
    # The live poll still scopes run.ledger to its own turn -- that is correct, and is what the
    # review row and the "no tool ran" line read.
    assert "e.client_turn_id !== run.turnId" in HTML
