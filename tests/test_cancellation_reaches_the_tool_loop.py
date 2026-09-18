"""Cancelling a turn must stop the work, not just the talking.

**Fixed 2026-08-02.** `core/tool_intent_executor.execute_tool_intent` is the single entry every
tool dispatch funnels through, and it now reads the turn's cancel signal BEFORE calling the
handler. `core/` is shared runtime, not one lane's property; PR #71
(`agent/context-integrity-firewall-20260730`) was open against this same file at the time and was
checked first — it adds 27 lines and no cancellation check, so this is not a parallel second fix of
the same bug. Landed small so it rebases cleanly under it.

The two reproductions here used to be `xfail(strict=True)` wrappers around
`assert "cancel" in inspect.getsource(...)`. That assertion passes the moment anyone writes the
word in a comment, so it could never have failed for the right reason. Both now drive the real
entry and assert the handler is not reached, with a control proving an uncancelled turn still runs.

What was measured on `main` @ 9e6f769, before the fix:

    $ grep -c "cancel" core/agent_runtime/research_tool_loop_facade.py
    0

Cancellation is observed in exactly six places, all of them model transport:
`core/web/api/runtime.py`, `core/memory_first_router.py`, `adapters/base_adapter.py`,
`adapters/openai_compatible_adapter.py`, `adapters/local_subprocess_adapter.py`, plus the registry
itself. The tool loop reads none of them.

Three consequences, in increasing order of seriousness:

1. Pressing stop interrupts the model call at its next poll boundary and leaves the tool loop
   running.
2. A `sandbox.run_command`, `workspace.write_file` or `email.send` already in flight runs to
   completion — the destructive ones included.
3. The completed tool writes its receipt and its `runtime_checkpoints.state_json`, which
   `core/tiered_context_loader.py:349` can promote into the NEXT request's prompt. So a turn the
   operator cancelled can still shape the answer to their next question.
"""
from __future__ import annotations

import threading

from core.web.api.turn_cancel import register_turn, request_cancel, unregister_turn


def test_the_cancel_registry_itself_works() -> None:
    """The one piece that is not in another lane, and it is sound. The registry is not the defect;
    nothing downstream of it asks."""
    event = register_turn("session-a", "turn-1")
    assert isinstance(event, threading.Event)
    assert event.is_set() is False

    assert request_cancel("session-a", "turn-1") == "cancelled"
    assert event.is_set() is True

    unregister_turn("session-a", "turn-1")
    assert request_cancel("session-a", "turn-1") == "not_found"


def test_a_cancel_for_an_unknown_turn_is_reported_honestly() -> None:
    assert request_cancel("session-b", "never-registered") == "not_found"


def test_cancelling_one_turn_does_not_cancel_another() -> None:
    first = register_turn("session-c", "turn-1")
    second = register_turn("session-c", "turn-2")

    request_cancel("session-c", "turn-1")

    assert first.is_set() is True
    assert second.is_set() is False, "cancelling one turn cancelled another in the same session"
    unregister_turn("session-c", "turn-1")
    unregister_turn("session-c", "turn-2")


def _guarded_call(monkeypatch, source_context):
    """Drive the one entry every tool dispatch funnels through, recording whether it got past."""

    from types import SimpleNamespace

    from core import tool_intent_executor

    dispatched: list[bool] = []
    monkeypatch.setattr(
        tool_intent_executor,
        "_execute_tool_intent_impl",
        lambda *args, **kwargs: dispatched.append(True),
    )
    execution = tool_intent_executor.execute_tool_intent(
        {"intent": "sandbox.run_command", "arguments": {"command": "rm -rf ./build"}},
        task_id="cancelled-task",
        session_id="cancelled-session",
        source_context=source_context,
        hive_activity_tracker=SimpleNamespace(),
    )
    return dispatched, execution


def test_a_tool_is_not_dispatched_after_its_turn_was_cancelled(monkeypatch) -> None:
    """The property that was missing, now executed instead of grepped.

    The previous version of this test asserted `"cancel" in inspect.getsource(...)`. That passes
    the moment anyone writes the word in a comment, so it could never have caught the defect it
    was named for. This drives the real entry and asserts the handler is never reached.
    """

    cancel = threading.Event()
    cancel.set()

    dispatched, execution = _guarded_call(monkeypatch, {"cancel_event": cancel})

    assert dispatched == [], "a destructive tool ran after the operator pressed stop"
    assert execution.status == "cancelled"
    assert execution.details["executed"] is False


def test_an_uncancelled_turn_still_reaches_its_tool(monkeypatch) -> None:
    """The control. A guard that stops everything would pass the test above and break the product."""

    dispatched, _ = _guarded_call(monkeypatch, {"cancel_event": threading.Event()})

    assert dispatched == [True]


def test_a_turn_with_no_cancel_signal_is_not_treated_as_cancelled(monkeypatch) -> None:
    dispatched, _ = _guarded_call(monkeypatch, {})

    assert dispatched == [True]


def test_a_cancel_signal_that_cannot_be_read_fails_closed(monkeypatch) -> None:
    """An unreadable signal is not permission to run.

    A caller can hand down a broken or foreign token. Reading it must not decide "not cancelled" by
    accident -- the expensive direction of this mistake is running a destructive call.
    """

    class _Hostile:
        def is_set(self):
            raise RuntimeError("signal backend is gone")

    dispatched, execution = _guarded_call(monkeypatch, {"cancel_event": _Hostile()})

    assert dispatched == []
    assert execution.status == "cancelled"
