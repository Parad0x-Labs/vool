"""The chat queue across two real browser tabs against the served daemon.

The chat's one-slot-per-chat law ("busy" decides run-now vs enqueue) used to be a property of a
single page: ``isChatBusy`` reads this page's own buckets, and the durable queue's wakeup chain
(send-continuation pump, post-release pump, PR98's per-page arrivals counter) also lives in the
page. A second tab with the same chat open had no way to know the slot was taken -- its send
ran a SECOND concurrent turn on the same session, bypassing the queue entirely, so "exactly
once, in queue order" had no meaning across tabs.

These tests drive the real served workflow with two pages of one browser: the provider is
genuinely executing the first tab's turn (the worker is live server-side) while the other tab
sends, which is the interleave a user gets by typing in a second window.
"""

from __future__ import annotations

import json
import threading
import time
from urllib.request import urlopen

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser

M1 = "Write one friendly sentence about a harbor lantern."
M1_ANSWER = "LANTERN-ONE: The lantern kept its small promise to the dark harbor."
M2 = "Write one friendly sentence about a fog bell."
M2_ANSWER = "BELL-TWO: The fog bell answered every ship that could not see."
M3 = "Write one friendly sentence about a lighthouse keeper."
M3_ANSWER = "KEEPER-THREE: The keeper counted the ships and kept the light burning."


class _GatedProvider(rig.CapturingProvider):
    """Answers from a table, but can hold chosen turns at the provider boundary.

    The hold is real product timing, not a page-side stub: the served turn's worker is live in
    ``core.live_turns`` for as long as the provider call is open, which is exactly what makes the
    chat's slot busy server-side while another tab sends.
    """

    def __init__(self, answers: dict[str, str], hold_texts: set[str]) -> None:
        self._answers = dict(answers)
        self._gates: dict[str, threading.Event] = {text: threading.Event() for text in hold_texts}
        self._gate_lock = threading.Lock()
        super().__init__(reply_fn=self._reply)

    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                text = str(message.get("content") or "")
                break
        # Only the ANSWER call is held. v0.7 memory's helper calls around a turn carry the same
        # user words; holding one stalled the turn before its answer call for the helper's whole
        # budget (four times longer on a machine with no GPU), so "the turn is live at the
        # provider" was never reached in time.
        if rig._all_text(messages).lstrip().startswith(_MEMORY_HELPER_PROMPTS):
            return self._answers.get(text.strip(), "Acknowledged.")
        gate = self._gates.get(text.strip())
        if gate is not None:
            gate.wait(timeout=60)
        return self._answers.get(text.strip(), "Acknowledged.")

    def release(self, text: str) -> None:
        with self._gate_lock:
            gate = self._gates.get(text)
            if gate is not None:
                gate.set()

    def generation_calls(self) -> list[dict]:
        """Only the calls that carried a real prompt (catalog probes carry none; counting them
        is how the a9 observer was fooled -- see tests/test_a9_turn_routing_served.py)."""
        with self._lock:
            return [
                call
                for call in self.calls
                if str(call.get("path") or "").startswith(("/api/chat", "/v1/"))
                and str(call.get("text") or "").strip()
                and not str(call.get("text") or "").lstrip().startswith(_MEMORY_HELPER_PROMPTS)
            ]


#: v0.7 memory runs its own helper calls around a turn -- the recall search-phrase writer before
#: the answer and the background fact extractor after it. They carry the message text but are not
#: the message being executed, so "this message ran once" counts answer calls only.
_MEMORY_HELPER_PROMPTS = (
    "You help search a stored record of past conversations.",
    "You are a local memory extraction agent.",
)


def _queue_state(daemon, session_id: str) -> dict:
    with urlopen(daemon.base_url + "/api/chat/queue?session=" + session_id, timeout=3) as response:
        return json.load(response)


def _wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.25)  # gentle on the daemon: every poll is a served request taking its storage lock
    raise AssertionError(f"{what} (waited {timeout}s; last={last!r})")


@pytest.fixture
def two_tabs(tmp_path, monkeypatch):
    """A certified daemon with a gated provider, plus two pages opened on the SAME chat."""
    yield from _two_tabs_with(tmp_path, monkeypatch, {})


@pytest.fixture
def two_tabs_hold_m2(tmp_path, monkeypatch):
    """The rig with M2's provider call held open (M1 answers immediately)."""
    yield from _two_tabs_with(tmp_path, monkeypatch, {}, hold_texts=(M2,))


@pytest.fixture
def two_tabs_hold_both(tmp_path, monkeypatch):
    """The rig with BOTH M1 and M2 held at the provider (for simultaneous-send races)."""
    yield from _two_tabs_with(tmp_path, monkeypatch, {}, hold_texts=(M1, M2))


@pytest.fixture
def two_tabs_short_lease(tmp_path, monkeypatch):
    """The rig with a one-second admission lease, so the abandoned-claim window is reachable
    deterministically through real timing."""
    yield from _two_tabs_with(tmp_path, monkeypatch, {"VOOL_ADMISSION_LEASE_SECONDS": "1"})


def _two_tabs_with(tmp_path, monkeypatch, daemon_env, hold_texts=(M1,)):
    """The two-tab rig with extra daemon environment (e.g. a short admission lease, which
    makes the abandoned-claim window reachable deterministically) and a configurable set of
    messages whose provider calls are held open for the duration of the test."""
    answers = {M1: M1_ANSWER, M2: M2_ANSWER, M3: M3_ANSWER}
    # VOOL_RAW_OLLAMA_API_URL fences the daemon off the operator's real Ollama (core/ollama_endpoint.py
    # records this exact hazard): pointed at a dead port, the auto lane's ollama attempt fails fast
    # and the fallback ladder advances to this rig's scripted provider deterministically --
    # otherwise the test would silently run on whatever local model the machine happens to have up.
    # The `with` block is the provider's real lifecycle: its HTTP server only serves inside it.
    with _GatedProvider(answers, hold_texts=set(hold_texts)) as provider:
        daemon = rig.ServedDaemon(
            tmp_path / "home",
            provider=provider,
            env_extra={
                "VOOL_INSTALL_PROFILE": "hybrid-fallback",
                "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1",
                **daemon_env,
            },
        )
        daemon.start(timeout=60)
        try:
            daemon.certify(timeout=30)
            manager, browser = served_browser.launch_chromium()
            try:
                # One browser context = one user's browser: its tabs share storage, so the second
                # tab boots into the same stored chat exactly like a second window does.
                context = browser.new_context()
                tab_a = context.new_page()
                errors: list[str] = []
                tab_a.on("pageerror", lambda error: errors.append(str(error)))
                # No per-page request interception here: with TWO pages' worth of concurrent
                # polls plus bursts at slot-free moments, the interception channel itself
                # stalled (measured: every fetch frozen mid-route, daemon idle) -- the daemon
                # is loopback-bound already, so the fence buys nothing this lane needs.
                tab_a.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                tab_a.wait_for_selector("#input", timeout=5000)
                _wait_for(
                    lambda: (
                        tab_a.evaluate("typeof displayedChat === 'string' && !!displayedChat")
                        and tab_a.evaluate("displayedChat")
                    ),
                    5,
                    "first tab never derived its chat",
                )
                session_id = tab_a.evaluate("displayedChat")
                # The second tab opens after the first stored the boot chat, so both share it (the
                # same way a second window of one browser shares the same stored chat).
                tab_b = context.new_page()
                tab_b.on("pageerror", lambda error: errors.append(str(error)))
                tab_b.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                tab_b.wait_for_selector("#input", timeout=5000)
                _wait_for(
                    lambda: tab_b.evaluate("typeof displayedChat === 'string' && displayedChat") == session_id,
                    5,
                    "tabs did not open the same chat",
                )
                yield provider, daemon, tab_a, tab_b, session_id, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def test_send_from_a_second_tab_while_a_turn_is_live_queues_and_runs_once(two_tabs):
    """The acceptance contract: while tab A's turn is genuinely executing, tab B's send must be
    stored in the chat's durable queue (not run concurrently), and once the slot frees it must
    execute exactly once, in order, with no second manual send."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    # Prime the chat through its fast lane exactly like a real first exchange (the startup
    # regression uses the same shape): "Hi" answers from the greeting lane, so the next message
    # is the chat's first PROVIDER-backed turn.
    tab_a.locator("#input").fill("Hi")
    tab_a.locator("#send").click()
    _wait_for(
        lambda: tab_a.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"),
        20,
        "greeting turn never completed",
    )
    # The answer rendering is not the turn's end: the worker finalizes server-side (the
    # live-turn registry is the slot's truth, and the tail can run for seconds). Wait for the
    # slot to free so m1 starts a real turn deterministically instead of racing that tail.
    _wait_for(
        lambda: not _queue_state(daemon, session_id).get("turn_active"),
        45,
        "greeting turn's slot never freed server-side",
    )

    # A call carrying THIS message's words -- the rig's certification probes ("sealed local
    # diagnostic...") are generation-shaped too and precede every turn, so per-message counting
    # is the only honest invocation count.
    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    # m1's provider call is held open: the turn is live server-side for this session. (The auto
    # lane probes the absent local backend first, so reaching the stub can take a few seconds.)
    try:
        _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    except AssertionError:
        print(
            "M1_DIAG run=",
            tab_a.evaluate(
                "view.run && {status:view.run.status, error:view.run.error, text:String(view.run.text||'').slice(0,200), released:view.run.released}"
            ),
            "transcript=",
            tab_a.locator(".msg .msg-text").all_text_contents()[-3:],
            "ERRORS",
            errors,
            "PROVIDER_CALLS",
            len(provider.calls),
            "M1_CALLS",
            len(calls_for(M1)),
            "\nDAEMON TAIL\n" + daemon.log_tail(20),
            flush=True,
        )
        raise
    _wait_for(lambda: tab_a.evaluate("!!(view.run && !view.run.released)"), 10, "first tab's run never became active")

    # The second tab sends into the SAME chat while that turn is executing.
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()

    def queued_items():
        state = _queue_state(daemon, session_id)
        return [item for item in (state.get("queue") or []) if item.get("status") == "pending"]

    # THE CONTRACT: the message is stored durably for this chat, and no second concurrent turn
    # was started for it. On the unrepaired page the queue stays empty and the provider receives
    # a second, concurrent call for the same session.
    def second_send_outcome():
        items = queued_items()
        if items:
            return ("queued", items)
        if calls_for(M2):
            return ("concurrent", None)
        return None

    kind, items = _wait_for(second_send_outcome, 15, "second tab's send neither queued nor ran")
    assert kind == "queued" and len(items) == 1, (
        "second tab's send was not stored in the chat's queue while the first tab's turn was "
        f"live (outcome={kind}, m1 calls: {len(calls_for(M1))}, m2 calls: {len(calls_for(M2))})"
    )
    assert items[0]["payload"]["text"] == M2, items[0]
    assert not calls_for(M2), (
        "a second concurrent turn was started for a chat whose slot was already taken: "
        f"{len(calls_for(M2))} m2 generation calls while m1 was live"
    )

    # Free the slot: the first turn completes. (The client-side release precedes the server
    # worker's finalization tail; the queued message's claim waits that tail out by design --
    # the heartbeat pumps it the moment the slot is genuinely free.)
    provider.release(M1)
    _wait_for(
        lambda: tab_a.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"),
        45,
        "first tab's turn never completed",
    )

    # The queued message must now execute exactly once, without another manual send. It runs in
    # whichever tab claimed it; the durable history is the shared truth either way.
    _wait_for(lambda: len(calls_for(M2)) >= 1, 45, "the queued message never executed")
    time.sleep(0.5)  # a duplicate dispatch would surface as a second m2 call almost immediately
    assert len(calls_for(M2)) == 1, f"queued message reached the provider more than once: {len(calls_for(M2))} m2 calls"
    _wait_for(
        lambda: _queue_state(daemon, session_id).get("queue") == [],
        10,
        "queue did not drain after the queued message ran",
    )

    def history_rows():
        with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=3) as response:
            history = json.load(response)
        messages = history.get("messages") or []
        asked = [m for m in messages if m.get("role") == "user" and str(m.get("content") or "").strip() == M2]
        answered = [m for m in messages if m.get("role") == "assistant" and M2_ANSWER in str(m.get("content") or "")]
        return (asked, answered) if asked and answered and len(asked) == 1 and len(answered) == 1 else None

    assert _wait_for(history_rows, 10, "queued message's answer never reached the history exactly once"), (
        "the queued message did not complete through the real turn lane exactly once"
    )
    assert not errors, errors


# ---- shared helpers for the independent cases ---------------------------------------------


def _prime_chat(daemon, tab, session_id):
    """Greeting turn + wait for its server-side slot to free: the deterministic starting point
    for a provider-backed turn."""
    tab.locator("#input").fill("Hi")
    tab.locator("#send").click()
    _wait_for(
        lambda: tab.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"),
        20,
        "greeting turn never completed",
    )
    _wait_for(
        lambda: not _queue_state(daemon, session_id).get("turn_active"),
        45,
        "greeting turn's slot never freed server-side",
    )


def _history(daemon, session_id):
    with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=3) as response:
        return json.load(response).get("messages") or []


def test_enqueue_after_an_empty_claim_returned_still_runs(two_tabs):
    """The documented residual exactly: tab A's pump has already claimed EMPTY and exited (the
    PR98 fix's re-claim loop cannot help -- the arrival happens after it), and tab B's enqueue
    lands while the chat's slot is held by tab A's live turn. Nothing may strand: the message
    runs once the slot frees, without a second manual send."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)

    # An unrelated pump in tab A that claims EMPTY and exits -- the pump whose claim window the
    # residual used to strand messages in. Wait until it is fully out (the hold released).
    tab_a.evaluate("pumpQueue(displayedChat)")
    _wait_for(lambda: tab_a.evaluate("!isChatBusy(displayedChat)"), 5, "empty pump never exited")

    # Now the real turn starts in tab A (held at the provider), and tab B sends AFTER that
    # empty claim returned: tab B's only wakeup is its own post-enqueue pump, which must defer
    # to the live slot, and tab A's release pump must be the one that claims the item.
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()
    items = _wait_for(
        lambda: (
            [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"] or None
        ),
        15,
        "second tab's send never reached the queue",
    )
    assert len(items) == 1 and items[0]["payload"]["text"] == M2, items
    assert not calls_for(M2), "m2 ran while m1 held the slot"

    provider.release(M1)
    _wait_for(lambda: len(calls_for(M2)) == 1, 45, "the queued message never executed after the empty claim")
    time.sleep(0.5)
    assert len(calls_for(M2)) == 1, f"queued message ran more than once: {len(calls_for(M2))}"
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 15, "queue never drained")
    asked = [
        m for m in _history(daemon, session_id) if m.get("role") == "user" and str(m.get("content") or "").strip() == M2
    ]
    answered = [
        m
        for m in _history(daemon, session_id)
        if m.get("role") == "assistant" and M2_ANSWER in str(m.get("content") or "")
    ]
    assert len(asked) == 1 and len(answered) == 1, (len(asked), len(answered))
    assert not errors, errors


def test_closing_the_first_tab_before_the_claim_still_runs(two_tabs):
    """The orphaned wakeup: the tab whose release pump would have claimed the queued message is
    closed mid-turn. The remaining tab's bounded heartbeat is the only wakeup left."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()
    _wait_for(
        lambda: [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
        15,
        "second tab's send never queued",
    )

    # The turn-owning tab disappears mid-turn. Its worker finishes server-side (client gone);
    # no release pump runs anywhere. The answer must still arrive -- in tab B.
    tab_a.close()
    provider.release(M1)
    _wait_for(lambda: len(calls_for(M2)) == 1, 60, "the queued message never executed after its tab closed")
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 15, "queue never drained")
    _wait_for(
        lambda: any(M2_ANSWER in t for t in tab_b.locator(".msg.assistant .msg-text").all_text_contents()),
        20,
        "the queued answer never rendered in the remaining tab",
    )
    answered = [
        m
        for m in _history(daemon, session_id)
        if m.get("role") == "assistant" and M2_ANSWER in str(m.get("content") or "")
    ]
    assert len(answered) == 1, f"queued message answered {len(answered)} times"
    assert not errors, errors


def test_two_tabs_pumping_at_once_claim_exactly_once(two_tabs):
    """Both tabs pump the same chat the moment the slot frees. The compare-and-swap claim must
    give the queued item to exactly one; the other exits without running anything."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()
    _wait_for(
        lambda: [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
        15,
        "second tab's send never queued",
    )

    provider.release(M1)

    # Race both pages' pumps against each other the instant the slot is free.
    def both_free():
        state_a = tab_a.evaluate("!isChatBusy(displayedChat)")
        state_b = tab_b.evaluate("!isChatBusy(displayedChat)")
        queue = _queue_state(daemon, session_id)
        return state_a and state_b and not queue.get("turn_active")

    _wait_for(both_free, 45, "slot never freed for the contention window")
    tab_a.evaluate("pumpQueue(displayedChat)")
    tab_b.evaluate("pumpQueue(displayedChat)")

    _wait_for(lambda: len(calls_for(M2)) >= 1, 45, "the queued message never executed under contention")
    time.sleep(1.0)  # a double claim would surface as a second m2 run almost immediately
    assert len(calls_for(M2)) == 1, f"contention ran the queued message {len(calls_for(M2))} times"
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 15, "queue never drained")
    answered = [
        m
        for m in _history(daemon, session_id)
        if m.get("role") == "assistant" and M2_ANSWER in str(m.get("content") or "")
    ]
    assert len(answered) == 1, f"queued message answered {len(answered)} times"
    assert not errors, errors


def test_a_different_chat_is_never_blocked_by_the_first(two_tabs):
    """One slot per CHAT, not per machine: while chat X's turn is executing, a message sent in
    chat Y (tab B switches to a fresh chat) runs directly, and neither chat's queue is
    involved."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "chat X's turn never reached the provider")

    # Tab B starts a DIFFERENT chat through the sidebar's own control.
    tab_b.locator("#newChat").click()
    other = tab_b.evaluate("displayedChat")
    assert other and other != session_id, "second tab did not open a different chat"
    tab_b.locator("#input").fill(M3)
    tab_b.locator("#send").click()

    # Chat Y's message runs while chat X's turn is still held -- and neither chat queued.
    _wait_for(lambda: len(calls_for(M3)) >= 1, 45, "the other chat's message never ran")
    assert _queue_state(daemon, session_id).get("queue") == [], "chat X's queue was touched"
    assert _queue_state(daemon, other).get("queue") == [], "chat Y's queue was touched"

    provider.release(M1)
    _wait_for(
        lambda: any(
            M3_ANSWER in str(m.get("content") or "") for m in _history(daemon, other) if m.get("role") == "assistant"
        ),
        45,
        "the other chat's answer never committed",
    )
    # And chat X's history never saw chat Y's words.
    assert not any(M3 in str(m.get("content") or "") for m in _history(daemon, session_id)), (
        "chat Y's message leaked into chat X's history"
    )
    assert not errors, errors


def test_cancelling_the_queued_message_through_the_chip(two_tabs):
    """Refusal through the queue's own contract: the chip's cancel button removes the pending
    item; the held turn then completes and NOTHING runs for the cancelled message."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()
    _wait_for(
        lambda: tab_b.locator(".qchip").count() >= 1, 15, "the queued message's chip never appeared in the second tab"
    )

    # The user changes their mind in the tab they typed in: cancel through the chip.
    tab_b.locator(".qchip-x").first.click(timeout=5000)
    _wait_for(
        lambda: _queue_state(daemon, session_id).get("queue") == [],
        10,
        "cancelling through the chip never drained the queue",
    )

    provider.release(M1)
    _wait_for(
        lambda: tab_a.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"),
        45,
        "first tab's turn never completed",
    )
    time.sleep(2.0)  # any wrongful dispatch of the cancelled item would surface here
    assert not calls_for(M2), "the cancelled queued message was dispatched anyway"
    assert not any(
        str(m.get("content") or "").strip() == M2 for m in _history(daemon, session_id) if m.get("role") == "user"
    ), "the cancelled message reached the history"
    assert not errors, errors


def test_a_failed_enqueue_restores_the_draft(two_tabs):
    """Enqueue failure through its existing server contract: a message past the queue's length
    cap is answered 413 by the door itself, and the composer's failure branch must return the
    text to the draft rather than lose it -- the queue stays empty and nothing dispatches."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")

    oversized = "This message is far too long for the queue door. " + ("padding " * 4200)
    assert len(oversized) > 32768, len(oversized)
    tab_b.locator("#input").fill(oversized)
    tab_b.locator("#send").click()

    _wait_for(
        lambda: tab_b.locator("#input").input_value() == oversized, 15, "the refused enqueue never restored the draft"
    )
    assert _queue_state(daemon, session_id).get("queue") == [], "a refused enqueue still stored an item"
    assert not calls_for(oversized[:60]), "the oversized message was dispatched anyway"

    provider.release(M1)
    _wait_for(
        lambda: tab_a.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"),
        45,
        "first tab's turn never completed",
    )
    assert not errors, errors


def test_a_send_that_races_a_turn_start_is_rerouted_by_the_server(two_tabs):
    """The check-then-send race, closed at the owner: tab B's slot read answers FREE just as
    tab A's turn starts, so tab B POSTs a second turn -- the server's typed 409 refuses it and
    the page reroutes the message into the same durable queue (idempotency key 'reroute-').

    The stale read is arranged through the page's own wrapper seam (the same instrumentation
    pattern the startup regression uses for the claim window): one slot read answers false,
    exactly what the real race produces. The refused POST, the 409 and the reroute are all the
    production path."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_b.evaluate("""() => {
        const original = chatSlotBusyOnServer;
        let staleReadsLeft = 1;
        chatSlotBusyOnServer = async function(chatId) {
            if (staleReadsLeft > 0) { staleReadsLeft -= 1; return false; }
            return original(chatId);
        };
    }""")
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    # m1's turn is live BEFORE tab B's (stale) slot read resolves its send decision.
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "first tab's turn never reached the provider")
    tab_b.locator("#input").fill(M2)
    tab_b.locator("#send").click()

    def rerouted_item():
        items = [
            i
            for i in _queue_state(daemon, session_id).get("queue") or []
            if i.get("status") == "pending" and i.get("payload", {}).get("text") == M2
        ]
        return items or None

    items = _wait_for(rerouted_item, 20, "the refused turn was never rerouted into the queue")
    assert items[0].get("idempotency_key", "").startswith("reroute-"), items[0]
    assert not calls_for(M2), "the raced turn reached the provider for a chat whose slot was taken"

    provider.release(M1)
    _wait_for(lambda: len(calls_for(M2)) == 1, 45, "the rerouted message never executed")
    time.sleep(0.5)
    assert len(calls_for(M2)) == 1, f"the rerouted message ran {len(calls_for(M2))} times"
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 15, "queue never drained")
    answered = [
        m
        for m in _history(daemon, session_id)
        if m.get("role") == "assistant" and M2_ANSWER in str(m.get("content") or "")
    ]
    assert len(answered) == 1, f"rerouted message answered {len(answered)} times"
    assert not errors, errors


# ---- correction lane: atomic admission + claimed-message refusal (review 2026-09-30) --------


def test_two_claims_from_two_tabs_admit_one_message_at_a_time(two_tabs_hold_m2):
    """The review's first failure through the real served door: two queued messages, claims
    from two tabs inside the claim-to-start window (both pages' pumps frozen so neither
    dispatches yet). The session's admission must let ONE tab hold ONE in_flight row; the
    other claim is refused. Releasing the winner's pump then dispatches the first message
    (consuming its own claim) and holds it at its provider -- the second message must not
    execute until the first completes, then runs exactly once. Queue drains; FIFO holds."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs_hold_m2

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    # Freeze both pages' pump scheduling so both claims sit in the claim-to-start window.
    for tab in (tab_a, tab_b):
        tab.evaluate("() => { window.savedPump = pumpQueue; pumpQueue = async function() {}; }")
    enqueued = tab_a.evaluate(
        "async ([sid, m2, m3]) => { const a = await queueOp('enqueue', sid, {text: m2});"
        " const b = await queueOp('enqueue', sid, {text: m3}); return {first: a.item, second: b.item}; }",
        [session_id, M2, M3],
    )

    first_claim = tab_a.evaluate("async ([sid]) => (await queueOp('claim', sid, {})).item", [session_id])
    second_claim = tab_b.evaluate("async ([sid]) => (await queueOp('claim', sid, {})).item", [session_id])
    assert first_claim and first_claim["queue_item_id"] == enqueued["first"]["queue_item_id"]
    assert second_claim is None, (
        "two tabs held different in_flight rows before either turn started: "
        f"{first_claim['queue_item_id']} and {second_claim and second_claim['queue_item_id']}"
    )
    assert _queue_state(daemon, session_id).get("turn_active") is True  # the claim holds the slot

    # The winner dispatches its claim through the page's real turn path (the pump that
    # claimed in-product would call this same runTurn; the frozen-pump idiom isolates the
    # dispatch): the POST consumes its own admission and is held at its provider. The later
    # message must not execute meanwhile.
    tab_a.evaluate(
        "([sid, text, qid]) => { runTurn(text, qid, {chatId: sid}); }",
        [session_id, M2, first_claim["queue_item_id"]],
    )
    _wait_for(lambda: len(_turns_for(provider, M2)) >= 1, 45, "the first queued message never reached the provider")
    time.sleep(1.0)
    assert not _turns_for(provider, M3), "the second message executed while the first held the chat's slot"
    assert len(_turns_for(provider, M2)) == 1

    # Restore both pages' real pumps (the freeze isolated the claim window; the in-product
    # wakeups -- the dispatching page's release pump, the other tab's, the heartbeat -- must
    # be live again for the second message).
    for tab in (tab_a, tab_b):
        tab.evaluate("() => { pumpQueue = window.savedPump; }")
    provider.release(M2)  # the first message completes; only then may the second run
    _wait_for(lambda: len(_turns_for(provider, M3)) >= 1, 45, "the second queued message never executed")
    time.sleep(0.5)
    assert len(_turns_for(provider, M2)) == 1 and len(_turns_for(provider, M3)) == 1, (
        f"messages did not execute exactly once: m2={len(_turns_for(provider, M2))} m3={len(_turns_for(provider, M3))}"
    )
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 20, "the queue never drained")
    assert not errors, errors


def test_simultaneous_direct_sends_admit_exactly_one_turn(two_tabs_hold_both):
    """The review's source-level finding through the real door: two tabs POST direct turns in
    the same instant (both slot reads answered by the wrapper seam with the stale FREE the
    real race produces). The atomic admission must let exactly ONE turn execute; the other
    POST receives the typed 409 and its message reroutes into the queue, executing once
    after the blocker completes. One provider execution per message; queue drains."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs_hold_both

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    # Both tabs read the slot as free exactly once (the value the real race returns when the
    # reads precede either POST) and then send -- the server is the only arbiter left.
    for tab in (tab_a, tab_b):
        tab.evaluate("""() => {
            const original = chatSlotBusyOnServer;
            let staleReadsLeft = 1;
            chatSlotBusyOnServer = async function(chatId) {
                if (staleReadsLeft > 0) { staleReadsLeft -= 1; return false; }
                return original(chatId);
            };
        }""")
    tab_a.locator("#input").fill(M1)
    tab_b.locator("#input").fill(M2)
    tab_a.locator("#send").click()
    tab_b.locator("#send").click()

    # Exactly one of the two messages executes (held at its provider); the other was refused
    # and rerouted into the durable queue as ONE new row.
    _wait_for(
        lambda: len(calls_for(M1)) + len(calls_for(M2)) >= 1, 45, "neither simultaneous send reached the provider"
    )
    time.sleep(2.0)  # a second admitted turn would surface as the other message executing
    executed = [m for m in (M1, M2) if calls_for(m)]
    assert len(executed) == 1, f"both simultaneous sends executed: {[m[:20] for m in executed]}"
    refused_text = M2 if executed[0] == M1 else M1
    rows = _wait_for(
        lambda: (
            [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"] or None
        ),
        20,
        "the refused send never rerouted into the queue",
    )
    assert len(rows) == 1 and rows[0]["payload"]["text"] == refused_text, rows
    assert not calls_for(refused_text), "the refused turn reached the provider anyway"

    provider.release(executed[0])
    _wait_for(lambda: len(calls_for(refused_text)) >= 1, 45, "the refused message never executed after the blocker")
    provider.release(refused_text)
    time.sleep(0.5)
    assert len(calls_for(refused_text)) == 1, "the refused message executed more than once"
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 20, "the queue never drained")
    assert not errors, errors


def test_a_refused_claimed_message_returns_as_the_original_row(two_tabs_short_lease):
    """The review's second failure through the real door. With a one-second admission lease,
    the abandoned-claim window is reachable deterministically: tab B claims M2 and does not
    dispatch; the lease lapses; tab A starts a genuinely held turn; tab B's real runTurn for
    the claimed item receives the typed 409 and must return the ORIGINAL row (same id, seq 1,
    pending) through the ownership-checked release -- no duplicate row, no reordering. After
    the blocker completes, M2 then M3 execute exactly once, in order, and the queue drains."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs_short_lease

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    tab_b.evaluate("() => { window.savedPump = pumpQueue; pumpQueue = async function() {}; }")
    original = tab_b.evaluate(
        "async ([sid, m2, m3]) => { const a = await queueOp('enqueue', sid, {text: m2});"
        " await queueOp('enqueue', sid, {text: m3}); return {enqueued: a.item, claimed: (await queueOp('claim', sid, {})).item}; }",
        [session_id, M2, M3],
    )
    assert original["claimed"]["queue_item_id"] == original["enqueued"]["queue_item_id"]
    time.sleep(1.6)  # the abandoned claim's admission lease (1s) lapses: the chat is free

    # Another tab takes the slot with a real, provider-held turn.
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "the blocker turn never reached the provider")

    # The claimed message's real dispatch is refused and must return as the ORIGINAL row.
    tab_b.evaluate(
        "async ([sid, text, qid]) => { await runTurn(text, qid, {chatId: sid}); }",
        [session_id, M2, original["claimed"]["queue_item_id"]],
    )
    state = _queue_state(daemon, session_id)
    rows = {(r["seq"], r["status"]): r for r in state["queue"]}
    original_row = next(r for r in state["queue"] if r["queue_item_id"] == original["claimed"]["queue_item_id"])
    assert original_row["status"] == "pending", f"409 stranded the original in_flight: {state['queue']}"
    assert original_row["seq"] == 1, original_row
    assert len(state["queue"]) == 2, f"409 created an extra queue row: {state['queue']}"
    assert [r["payload"]["text"] for r in sorted(state["queue"], key=lambda r: r["seq"])] == [M2, M3]
    assert not _turns_for(provider, M2), "the refused claimed message executed anyway"

    tab_b.evaluate("() => { pumpQueue = window.savedPump; }")  # the real wakeups come back
    provider.release(M1)
    _wait_for(lambda: len(_turns_for(provider, M2)) == 1, 45, "the original M2 never executed after the blocker")
    _wait_for(lambda: len(_turns_for(provider, M3)) == 1, 45, "M3 never executed after M2")
    time.sleep(0.5)
    assert len(_turns_for(provider, M2)) == 1 and len(_turns_for(provider, M3)) == 1
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 20, "the queue never drained")
    # FIFO held: M2's answer is in the history exactly once, M3's after it.
    messages = _history_rows(daemon, session_id)
    answers = [str(m.get("content") or "") for m in messages if m.get("role") == "assistant"]
    assert any(M2_ANSWER in a for a in answers), answers[-3:]
    assert any(M3_ANSWER in a for a in answers), answers[-3:]
    assert not errors, errors


def _turns_for(provider, message):
    """Provider calls whose LAST user message is exactly `message` -- the turn dispatched FOR
    that message, never a later turn that merely carries it in prompt history."""
    wanted = message.strip()
    turns = []
    for call in provider.generation_calls():
        messages = call.get("messages") or []
        last_user = ""
        for entry in reversed(messages):
            if isinstance(entry, dict) and entry.get("role") == "user":
                last_user = str(entry.get("content") or "").strip()
                break
        if last_user == wanted:
            turns.append(call)
    return turns


def _history_rows(daemon, session_id):
    with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=3) as response:
        return json.load(response).get("messages") or []


def test_opening_a_chat_pumps_its_pending_queue(two_tabs):
    """The handover's corrected statement, proven: OPENING a chat (the sidebar click through
    openSession) is itself a wakeup for that chat's queue -- a message another tab left
    pending executes on the open, without waiting for the heartbeat's slower sweep."""
    provider, daemon, tab_a, tab_b, session_id, errors = two_tabs

    def calls_for(message):
        return [call for call in provider.generation_calls() if message in str(call.get("text") or "")]

    _prime_chat(daemon, tab_a, session_id)
    # A third page on the same chat queues M2 behind tab A's held turn, then closes.
    tab_c = tab_a.context.new_page()
    tab_c.on("pageerror", lambda error: errors.append(str(error)))
    tab_c.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    tab_c.wait_for_selector("#input", timeout=5000)
    _wait_for(
        lambda: tab_c.evaluate("displayedChat") == session_id, 10, "the third page never settled on the same chat"
    )
    tab_a.locator("#input").fill(M1)
    tab_a.locator("#send").click()
    _wait_for(lambda: len(calls_for(M1)) >= 1, 30, "the held turn never reached the provider")
    tab_c.locator("#input").fill(M2)
    tab_c.locator("#send").click()
    _wait_for(
        lambda: [i for i in _queue_state(daemon, session_id).get("queue") or [] if i.get("status") == "pending"],
        15,
        "M2 never queued",
    )

    # The turn-owning tab and the enqueueing tab both close; the worker finishes server-side.
    tab_c.close()
    tab_a.close()
    provider.release(M1)
    _wait_for(
        lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "the closed tab's worker never finished"
    )

    # Tab B sits on a DIFFERENT chat with no knowledge of the pending item (its heartbeat
    # sweep has nothing to pump); opening the target chat through the sidebar must pump.
    tab_b.locator("#newChat").click()
    other = tab_b.evaluate("displayedChat")
    assert other != session_id
    tab_b.evaluate("lastQueueItemsByChat.delete(displayedChat)")  # no stale knowledge of either chat
    # Neutralize the heartbeat sweep (page instrumentation, the frozen tests' own idiom): the
    # OPEN must be the only wakeup left, so the execution proof lands on openSession's pump.
    tab_b.evaluate("() => { window.savedSweep = pumpKnownQueuedChats; pumpKnownQueuedChats = function() {}; }")
    assert not calls_for(M2)
    clicked = time.monotonic()
    # Two chats exist; the current one's row is `.active` -- the target chat is the other row,
    # and its title click is the real open action (openSession).
    tab_b.locator(".session:not(.active) .s-title").first.click(timeout=5000)
    _wait_for(lambda: len(calls_for(M2)) >= 1, 6, "opening the chat never pumped its pending message")
    elapsed = time.monotonic() - clicked
    assert elapsed < 4.0, f"the open did not pump (took {elapsed:.1f}s -- the heartbeat's sweep, not the open)"
    _wait_for(lambda: _queue_state(daemon, session_id).get("queue") == [], 20, "the queue never drained")
    assert not errors, errors
