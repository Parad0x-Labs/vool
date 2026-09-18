"""A session's turns run one at a time, in arrival order; other sessions never wait.

Measured 2026-08-15 (MF-15, CRITICAL): two /api/chat POSTs 300 ms apart on one session ran
concurrently -- the fast turn's answer ("6 * 7 = 42.") persisted at 12:33:07.880 BETWEEN the slow
turn's question (12:33:07.845... preceded by the slow question at .539) and the slow answer
(12:33:18.812), so the transcript read in order paired every answer with the wrong question. The
operator's 50-question batch showed the same class live: "List B starts as [5, 6, 7]..." answered
with a greeting, the greeting's answer drifting onto the next turn. The queued turn also executed
WITHOUT the prior exchange in its context, because history hydration ran pre-gate.
"""

from __future__ import annotations

import threading
import time
from itertools import pairwise

import pytest

from core.session_turn_gate import _GATES, hold_session_turn_gate


def _run_concurrently(work_items):
    threads = [threading.Thread(target=item) for item in work_items]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a gated worker deadlocked"


def test_same_session_turns_serialize_in_arrival_order() -> None:
    spans: list[tuple[str, float, float]] = []
    spans_lock = threading.Lock()
    started = threading.Barrier(3)

    def _turn(name: str, delay: float, hold: float) -> None:
        started.wait()
        time.sleep(delay)  # stagger arrival: a < b < c
        with hold_session_turn_gate("openclaw:serialize-me"):
            enter = time.monotonic()
            time.sleep(hold)
            with spans_lock:
                spans.append((name, enter, time.monotonic()))

    _run_concurrently(
        [
            lambda: _turn("a", 0.00, 0.15),
            lambda: _turn("b", 0.05, 0.02),
            lambda: _turn("c", 0.10, 0.02),
        ]
    )

    assert [name for name, _, _ in spans] == ["a", "b", "c"]
    for (_, _, prev_exit), (_, next_enter, _) in pairwise(spans):
        assert next_enter >= prev_exit, "two turns of one session overlapped"


def test_distinct_sessions_do_not_wait_on_each_other() -> None:
    # Negative control: the gate must not become a global lock.
    spans: dict[str, tuple[float, float]] = {}
    started = threading.Barrier(2)

    def _turn(session: str) -> None:
        started.wait()
        with hold_session_turn_gate(session):
            enter = time.monotonic()
            time.sleep(0.2)
            spans[session] = (enter, time.monotonic())

    _run_concurrently([lambda: _turn("openclaw:chat-one"), lambda: _turn("openclaw:chat-two")])

    (a_enter, a_exit), (b_enter, b_exit) = spans["openclaw:chat-one"], spans["openclaw:chat-two"]
    overlap = min(a_exit, b_exit) - max(a_enter, b_enter)
    assert overlap > 0, "two DIFFERENT sessions were serialized against each other"


def test_reentrant_hold_by_the_same_thread_does_not_deadlock() -> None:
    with hold_session_turn_gate("openclaw:reenter"):
        with hold_session_turn_gate("openclaw:reenter"):
            pass
    assert not _GATES["openclaw:reenter"].busy()


def test_an_exception_releases_the_gate() -> None:
    with pytest.raises(RuntimeError):
        with hold_session_turn_gate("openclaw:crashy"):
            raise RuntimeError("turn blew up")
    with hold_session_turn_gate("openclaw:crashy"):
        pass  # a second turn still gets in


def test_a_blank_session_runs_ungated() -> None:
    before = set(_GATES)
    with hold_session_turn_gate(""):
        pass
    with hold_session_turn_gate(None):
        pass
    assert set(_GATES) == before


def test_eviction_never_drops_a_busy_gate(monkeypatch) -> None:
    import core.session_turn_gate as gate_module

    monkeypatch.setattr(gate_module, "_MAX_TRACKED_SESSIONS", 2)
    release = threading.Event()
    holding = threading.Event()

    def _hold() -> None:
        with hold_session_turn_gate("openclaw:held-open"):
            holding.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold)
    holder.start()
    assert holding.wait(timeout=5)
    try:
        for index in range(6):  # push far past the cap while one gate is held
            with hold_session_turn_gate(f"openclaw:filler-{index}"):
                pass
        assert "openclaw:held-open" in _GATES, "a HELD gate was evicted"
        held_gate = _GATES["openclaw:held-open"]
        assert held_gate.busy()
    finally:
        release.set()
        holder.join(timeout=5)
    # The same gate object still guards the session afterwards.
    assert _GATES["openclaw:held-open"] is held_gate


# ---------------------------------------------------------------------------------------------
# Wiring: run_agent holds the gate and refreshes history inside it
# ---------------------------------------------------------------------------------------------


def test_run_agent_serializes_same_session_and_refreshes_history(monkeypatch) -> None:
    from core.web.api import runtime as runtime_module

    spans: list[tuple[str, float, float, object]] = []
    spans_lock = threading.Lock()

    def _fake_locked(runtime, user_text, *, session_id=None, source_context=None, **_kw):
        enter = time.monotonic()
        time.sleep(0.15 if user_text == "slow" else 0.01)
        with spans_lock:
            spans.append(
                (user_text, enter, time.monotonic(), (source_context or {}).get("conversation_history"))
            )
        return {"response": user_text, "confidence": 1.0}

    hydrated = [{"role": "user", "content": "before"}, {"role": "assistant", "content": "prior answer"}]
    refresh_calls: list[str] = []

    def _fake_augment(history, *, session_id, user_text, limit=6):
        refresh_calls.append(user_text)
        return hydrated + list(history)

    monkeypatch.setattr(runtime_module, "_run_agent_locked", _fake_locked)
    import core.persistent_memory as persistent_memory

    monkeypatch.setattr(persistent_memory, "augment_history_from_session_log", _fake_augment)

    session = "openclaw:wired-serialize"
    started = threading.Barrier(2)

    def _call(text: str, delay: float) -> None:
        started.wait()
        time.sleep(delay)
        runtime_module.run_agent(
            object(),
            text,
            session_id=session,
            source_context={"client_conversation_history": [{"role": "user", "content": text}]},
        )

    _run_concurrently([lambda: _call("slow", 0.0), lambda: _call("fast", 0.05)])

    assert [text for text, *_ in spans] == ["slow", "fast"], "arrival order was not preserved"
    (_, _, slow_exit, _), (_, fast_enter, _, fast_history) = spans[0], spans[1]
    assert fast_enter >= slow_exit, "same-session run_agent calls overlapped"
    # The queued turn's history was refreshed INSIDE the gate, after the slow turn finished.
    assert refresh_calls == ["slow", "fast"]
    assert fast_history[:2] == hydrated


def test_run_agent_without_a_session_still_runs(monkeypatch) -> None:
    from core.web.api import runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "_run_agent_locked",
        lambda *a, **k: {"response": "ok", "confidence": 1.0},
    )
    assert runtime_module.run_agent(object(), "hi")["response"] == "ok"
