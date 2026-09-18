"""Plan step 4 / Codex #2 — per-turn cancellation keyed by (session_id, turn_id)."""
from __future__ import annotations

import threading

from core.web.api.turn_cancel import (
    active_turn_count,
    register_turn,
    request_cancel,
    unregister_turn,
)


def test_cancel_sets_the_event_and_is_idempotent():
    ev = register_turn("s1", "tA")
    assert not ev.is_set()
    assert request_cancel("s1", "tA") == "cancelled"
    assert ev.is_set()
    assert request_cancel("s1", "tA") == "cancelled"  # idempotent
    unregister_turn("s1", "tA")


def test_cancel_unknown_turn_is_not_found():
    assert request_cancel("s1", "does-not-exist") == "not_found"


def test_cancelling_one_turn_does_not_cancel_another():
    a = register_turn("s1", "tA")
    b = register_turn("s1", "tB")
    assert request_cancel("s1", "tA") == "cancelled"
    assert a.is_set()
    assert not b.is_set()  # a queued/overlapping turn B is untouched (Codex: cancel A must not hit B)
    unregister_turn("s1", "tA")
    unregister_turn("s1", "tB")


def test_registry_cleans_up_in_finally():
    before = active_turn_count()
    register_turn("s2", "t1")
    assert active_turn_count() == before + 1
    unregister_turn("s2", "t1")
    assert active_turn_count() == before
    assert request_cancel("s2", "t1") == "not_found"


def test_cancel_signal_reaches_a_running_consumer():
    # Proves the Event actually reaches an in-flight consumer (the mechanism that stops inference),
    # not merely that a flag flipped: a worker looping on the event exits when we cancel it.
    ev = register_turn("s3", "tX")
    stopped = threading.Event()

    def _worker():
        while not ev.is_set():
            ev.wait(timeout=0.02)
        stopped.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert request_cancel("s3", "tX") == "cancelled"
    assert stopped.wait(timeout=2.0)  # the worker observed the cancel and stopped
    t.join(timeout=1.0)
    unregister_turn("s3", "tX")


def test_page_stop_button_cancels_the_server_turn():
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "/api/chat/cancel" in html          # the Stop button hits the cancel endpoint
    assert "turn_id: run.turnId" in html        # each turn carries its own cancel id
    assert ">Stop<" in html                     # relabeled from the old "Stop showing"
