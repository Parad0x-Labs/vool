"""Extraction runs when the person goes quiet, batching the turns since the last run.

A background model call that interleaves the chat evicts the engine's prompt cache for the
next turn (measured on the hidden set: tool turns paid full prefill and ran 17% slower than
BASE) and lands in every turn's byte and serial-call counts. The schedule is decided from
turn state and time only: while turns keep arriving inside the quiet window the excerpts
batch; when quiet arrives (timer, or the gap seen at the next schedule point) the batch runs
as ONE extraction over the accumulated conversation. Default 0 keeps run-after-every-turn.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

import core.web.api.runtime as runtime_module
from core.web.api.runtime import schedule_memory_extraction

_CAP = 8

#: Set by _schedule when the dispatch path runs (the immediate-mode branch).
_DISPATCHED: list[list] = []
#: Set when a flush happened through the mocked _flush_memory_extraction.
_FLUSHED: list[str] = []
#: Timer objects created (never really started).
_TIMERS: list[float] = []


class _FakeTimer:
    def __init__(self, seconds, fn, args=None):
        _TIMERS.append(seconds)

    def cancel(self):  # pragma: no cover - shims only
        return None

    def start(self):  # pragma: no cover - shims only
        return None


def _quiet(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    monkeypatch.setattr(runtime_module, "_extraction_quiet_seconds", lambda: seconds)


def _schedule(monkeypatch: pytest.MonkeyPatch, text: str, when: float) -> None:
    monkeypatch.setattr(
        runtime_module,
        "_dispatch_memory_extraction",
        lambda runtime, source_context, access_policy, messages, session_id: _DISPATCHED.append(messages),
    )
    monkeypatch.setattr(
        runtime_module, "_flush_memory_extraction",
        lambda runtime, session_id: _FLUSHED.append(session_id),
    )
    import threading

    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    runtime = SimpleNamespace(runtime_home=None)
    context = {"platform": "api", "surface": "api", "runtime_session_id": "sched-test"}
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: when)
        m.setattr(
            runtime_module, "_messages_for_memory_extraction",
            lambda **kwargs: [{"role": "user", "content": text}],
        )
        m.setattr(
            "core.context_scope.ContextAccessPolicy.for_request",
            classmethod(lambda cls, **kw: SimpleNamespace(
                namespace_state="active", chat_id=kw.get("session_id"), project_id="")),
        )
        # The same chat, still active when a held batch is admitted again at dispatch.
        m.setattr(
            "core.context_namespace.load_chat_namespace",
            lambda chat_id: SimpleNamespace(chat_id=chat_id, lifecycle_state="active"),
        )
        m.setattr("core.request_trust.turn_is_owner_authored", lambda sc: True)
        m.setattr("core.request_trust.request_is_owner_local", lambda sc: True)
        schedule_memory_extraction(
            runtime, user_text=text, assistant_output="ok",
            session_id="sched-test", source_context=context,
        )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_module, "_extraction_pending", {})
    monkeypatch.setattr(runtime_module, "_extraction_timer", {})
    _DISPATCHED.clear()
    _FLUSHED.clear()
    _TIMERS.clear()
    yield


def test_turns_inside_the_quiet_window_batch_and_never_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch, 90.0)
    start = time.time()
    _schedule(monkeypatch, "first fact", start)
    _schedule(monkeypatch, "second fact", start + 10)
    _schedule(monkeypatch, "third fact", start + 20)
    assert not _DISPATCHED and not _FLUSHED
    batch = runtime_module._extraction_pending["sched-test"]
    assert batch["turns"] == 3 and len(batch["messages"]) == 3
    assert len(_TIMERS) == 3  # each turn re-arms the quiet flush


def test_a_quiet_gap_flushes_the_held_batch_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch, 90.0)
    start = time.time()
    _schedule(monkeypatch, "morning fact", start)
    # The person was quiet for two minutes before the next turn: the held batch is due.
    _schedule(monkeypatch, "afternoon fact", start + 150)
    assert len(_DISPATCHED) == 1 and len(_DISPATCHED[0]) == 1  # the OLD batch, without the new turn
    assert runtime_module._extraction_pending["sched-test"]["turns"] == 1


def test_the_batch_cap_flushes_even_while_chatty(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch, 90.0)
    start = time.time()
    for i in range(_CAP):
        _schedule(monkeypatch, f"fact {i}", start + i)
    assert len(_DISPATCHED) == 1 and len(_DISPATCHED[0]) == _CAP, (
        "the cap bounds staleness while the person keeps talking"
    )


def test_default_zero_keeps_run_after_every_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch, 0.0)
    _schedule(monkeypatch, "immediate fact", time.time())
    assert len(_DISPATCHED) == 1 and not runtime_module._extraction_pending


def test_the_product_default_lands_after_import_in_a_fresh_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The served daemon applies product env defaults AFTER this module is imported.

    A module-level read froze the test default "0" into the daemon and the quiet batch never
    ran in the product. The window is read at use, so applying the
    product defaults post-import puts the 90 s quiet window live.
    """
    import subprocess
    import sys

    code = (
        "import os\n"
        "os.environ.pop('VOOL_EXTRACTION_QUIET_SECONDS', None)\n"
        "import core.web.api.runtime as rt\n"
        "assert rt._extraction_quiet_seconds() == 0.0, 'test default before product defaults'\n"
        "from core.runtime_provider_defaults import apply_product_runtime_defaults\n"
        "apply_product_runtime_defaults()\n"
        "assert os.environ['VOOL_EXTRACTION_QUIET_SECONDS'] == '90'\n"
        "assert rt._extraction_quiet_seconds() == 90.0, 'product window must be live at use time'\n"
        "print('PRODUCT-DEFAULT-OK')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=os.getcwd(),
        env={**os.environ, "VOOL_HOME": os.environ.get("VOOL_HOME", "")},
    )
    assert "PRODUCT-DEFAULT-OK" in out.stdout, out.stdout + out.stderr


def _held_batch(chat_id: str) -> dict:
    return {
        "messages": [{"role": "user", "content": "my locker code is 4417"}],
        "turns": 1,
        "session": chat_id,
        "last": 0.0,
        "source_context": {"platform": "api"},
        "chat_id": chat_id,
        "project_id": "",
    }


@pytest.mark.parametrize("state", ["deleted", "archived", None])
def test_a_held_batch_from_a_chat_no_longer_active_is_dropped(
    monkeypatch: pytest.MonkeyPatch, state: str | None
) -> None:
    """Deleting (or archiving) a chat inside the quiet window takes its held turns with it."""
    dispatched: list = []
    monkeypatch.setattr(
        runtime_module, "_dispatch_memory_extraction",
        lambda runtime, source_context, access_policy, messages, session_id: dispatched.append(session_id),
    )
    monkeypatch.setattr(
        "core.context_namespace.load_chat_namespace",
        lambda chat_id: None if state is None else SimpleNamespace(chat_id=chat_id, lifecycle_state=state),
    )
    runtime_module._run_extraction_batch(SimpleNamespace(runtime_home=None), _held_batch("held-chat"))
    assert dispatched == []


def test_a_held_batch_from_an_active_chat_runs_with_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list = []
    monkeypatch.setattr(
        runtime_module, "_dispatch_memory_extraction",
        lambda runtime, source_context, access_policy, messages, session_id: dispatched.append(
            (session_id, access_policy.chat_id, len(messages))
        ),
    )
    monkeypatch.setattr(
        "core.context_namespace.load_chat_namespace",
        lambda chat_id: SimpleNamespace(chat_id=chat_id, lifecycle_state="active"),
    )
    runtime_module._run_extraction_batch(SimpleNamespace(runtime_home=None), _held_batch("held-chat"))
    assert dispatched == [("held-chat", "held-chat", 1)]


def test_a_scheduled_batch_records_its_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch, 90.0)
    _schedule(monkeypatch, "a fact", time.time())
    assert runtime_module._extraction_pending["sched-test"]["session"] == "sched-test"
