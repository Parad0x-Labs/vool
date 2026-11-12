"""A remember and a forget at the same time never deadlock (core/memory/entries.py, revocation.py).

Found 2026-11-12 on the v0.8 line at 247124ed: the forget-law suite hung 30 minutes under load in
tests/test_memory_forget_resurrection_p0.py::test_concurrent_remember_forget_preserves_unrelated_entries.
The dumped stacks: a remember (`add_memory_fact` -> the fenced writer -> `record_memory_entry`) held the
forget fence (`revocation._LOCK`, entered through `serialized()`) and waited for
`entries._MEMORY_ENTRY_LOCK`; a forget (`forget_memory` -> `forget_structured_memory`) held
`_MEMORY_ENTRY_LOCK` and waited for the fence, entered late from its rewrite (`rewrite_jsonl` ->
`_writing_rows` -> `serialized()`). The same two locks in opposite orders. Every later memory read or
write then waited on one of them: the app's memory would hang until a restart.

Law: one lock order, the fence first. `entries._entry_lock()` takes `revocation.fence_first()` before
`_MEMORY_ENTRY_LOCK` on every path that holds the entry lock (the forget port's "Take the forget fence
before the memory entry lock on every path"); tests/test_forget_memory_entry_lock_order.py races a
forget against the chat, project, finalization and convergence erases, and this file races it against
a remember.

The race is forced, not hoped for: the forget is held for a second inside its rewrite, with the entry
lock held, and the remember starts in that second. On the old order that deadlocks every time. Both
threads are joined against a deadline, so a hang fails the test instead of hanging pytest, and the
deadlock is broken so the rest of the session still runs.

All facts are invented for this contract.
"""
from __future__ import annotations

import threading
import time

import pytest

from core.memory import entries, revocation
from core.memory.entries import add_memory_fact, forget_memory, list_memory_entries
from core.persistent_memory import maybe_handle_memory_command
from tests.test_memory_forget_resurrection_p0 import (  # noqa: F401 (isolated_home is a fixture)
    CANARY,
    CHAT,
    FACT_TEXT,
    _policy,
    isolated_home,
)

DEADLINE_S = 30.0
HOLD_S = 1.0
OTHER_CHAT = "p0-resurrect-other-chat"
NEW_FACT = "Harbor lantern alpha burns steadiest."


@pytest.fixture(autouse=True)
def _not_wedged():
    """A deadlock left by an earlier test holds these locks for good: fail fast rather than hang."""
    for name, lock in (("revocation fence", revocation._LOCK), ("entries._MEMORY_ENTRY_LOCK", entries._MEMORY_ENTRY_LOCK)):
        if not lock.acquire(timeout=10):
            pytest.fail(f"{name} is held by a thread an earlier deadlock left behind", pytrace=False)
        lock.release()
    yield


def _remember_fact(chat: str) -> bool:
    return add_memory_fact(NEW_FACT, session_id=chat, access_policy=_policy(chat))


def _remember_command(chat: str) -> bool:
    handled, reply = maybe_handle_memory_command(f"Remember that {NEW_FACT}", session_id=chat,
                                                 access_policy=_policy(chat))
    return bool(handled and "Locked in" in reply)


REMEMBERS = {
    "fact in the same chat": (_remember_fact, CHAT),
    "fact in another chat": (_remember_fact, OTHER_CHAT),
    "chat command in the same chat": (_remember_command, CHAT),
}


@pytest.mark.parametrize("remember", sorted(REMEMBERS))
def test_a_remember_during_a_forget_never_deadlocks(isolated_home, monkeypatch, remember):
    policy = _policy()
    assert add_memory_fact(FACT_TEXT, session_id=CHAT, access_policy=policy)
    assert add_memory_fact("Unrelated anchor fact about tidewater gears.", session_id=CHAT, access_policy=policy)
    write, chat = REMEMBERS[remember]
    _policy(chat)

    forget_has_entry_lock = threading.Event()
    real_rewrite = entries.rewrite_jsonl
    held_back: list[int] = []

    def rewrite(path, rows):
        # Reached with the entry lock held: hold it a moment so the remember surely starts meanwhile.
        if threading.current_thread().name == "forget" and not held_back and entries._MEMORY_ENTRY_LOCK.locked():
            held_back.append(1)
            forget_has_entry_lock.set()
            time.sleep(HOLD_S)
        return real_rewrite(path, rows)

    monkeypatch.setattr(entries, "rewrite_jsonl", rewrite)
    out: dict = {}
    errors: dict = {}

    def run_forget():
        try:
            out["forget"] = forget_memory(CANARY, access_policy=policy)
        except BaseException as exc:  # noqa: BLE001 -- reported below
            errors["forget"] = exc
        forget_has_entry_lock.set()

    def run_remember():
        try:
            forget_has_entry_lock.wait(DEADLINE_S)
            out["remember"] = write(chat)
        except BaseException as exc:  # noqa: BLE001
            errors["remember"] = exc

    threads = [threading.Thread(target=run_forget, name="forget", daemon=True),
               threading.Thread(target=run_remember, name="remember", daemon=True)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + DEADLINE_S
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    hung = [thread.name for thread in threads if thread.is_alive()]
    if hung:
        # Break the deadlock so the rest of the session can run: the entry lock is a plain Lock, which any
        # thread may release. The remember then finishes and frees the fence; the forget follows.
        entries._MEMORY_ENTRY_LOCK.release()
        for thread in threads:
            thread.join(DEADLINE_S)
        pytest.fail(f"{remember}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s: a lock-order deadlock "
                    "between the forget fence and the memory entry lock", pytrace=False)
    assert held_back, "the forget never reached its rewrite under the entry lock"
    assert not errors, errors
    assert out["forget"] == 1, out
    assert out["remember"] is True, out
    texts = [str(row.get("text") or "") for row in list_memory_entries(access_policy=_policy(chat), limit=50)]
    assert any(NEW_FACT.rstrip(".") in text for text in texts), texts
    kept = [str(row.get("text") or "") for row in list_memory_entries(access_policy=policy, limit=50)]
    assert any("tidewater gears" in text for text in kept), kept
    assert not any(CANARY in text for text in kept), kept
