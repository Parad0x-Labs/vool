"""Guard: the /chat composer must stay usable while a turn runs -- a second message is
QUEUED (persisted, chip shown), never dropped -- and the queue pumps via the atomic
server-side claim. Regression for "cannot send another message while VOOL works".
"""

from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def test_queue_ui_and_pump_present() -> None:
    assert 'id="queue"' in HTML
    for fn in ("function runTurn", "function pumpQueue", "function queueOp", "function renderQueue"):
        assert fn in HTML, fn


def test_send_queues_when_busy_instead_of_dropping() -> None:
    # send() no longer early-returns on busy; it enqueues.
    assert "if (!text || busy) return" not in HTML  # the old drop
    assert "queueOp('enqueue'" in HTML
    assert "queueOp('claim'" in HTML  # atomic dequeue drives the pump
    assert "queueOp('cancel'" in HTML  # per-chip cancel
    assert "'/api/chat/queue'" in HTML


def test_queue_restored_on_load_and_session_switch() -> None:
    # refreshQueue is called on init, on opening a session, and on new chat.
    assert HTML.count("refreshQueue()") >= 3


def test_idempotency_key_generated_per_submit() -> None:
    # A per-submit idempotency key dedupes double-submit / reconnect replay.
    assert "idempotency_key" in HTML
    assert "idem-" in HTML


def test_one_response_container_no_separate_complete_card() -> None:
    # The status card is appended INTO the assistant message (host), not as a sibling in #log,
    # and collapses to a compact metadata row on a terminal state (no separate "Complete" card).
    assert "function buildCard(run, host)" in HTML
    # DISPATCHER phase 2: the bubble + card are built together by attachRunDom, so re-opening a
    # chat mid-turn rebuilds the SAME pairing rather than a card floating beside the answer.
    assert "attachRunDom(run, addMsg('assistant', '…'))" in HTML
    assert "  buildCard(run, host);" in HTML  # card built into the answer message element
    assert ".msg.assistant .task-card" in HTML  # in-message styling
    assert ".task-card.done .tc-head" in HTML   # collapse the working head on completion


def test_activity_panel_shows_useful_actions_not_event_spam() -> None:
    # Activity/Plan are grouped by real tool steps with duration/model/cost, and the badge counts
    # actions (steps), not raw low-level events.
    assert "function stepMeta" in HTML       # tool · duration · result · model · cost
    assert "function stepDuration" in HTML
    assert "Activity: run ? run.steps.length" in HTML  # count actions, not events
    assert ".xp-row.current" in HTML         # current step highlighted in Plan
    assert "startedAt: Date.now()" in HTML   # per-step timing


def test_header_is_clean_by_default() -> None:
    # Neither Trace nor Web0 sits in the HEADER; both live under Settings -> Advanced. The Web0
    # header link used to be gated on a WEB0_ENABLED flag that no caller ever set, so it was
    # permanently invisible -- the element and the flag are both gone rather than dead weight.
    header_markup = HTML.split("<header>", 1)[1].split("</header>", 1)[0]
    assert 'href="/trace"' not in header_markup
    assert 'href="/web0"' not in header_markup
    assert "/trace" in HTML  # reachable under Settings -> Advanced
    assert "/web0" in HTML   # reachable under Settings -> Advanced
    assert 'id="web0Link"' not in HTML
    assert "WEB0_ENABLED" not in HTML
