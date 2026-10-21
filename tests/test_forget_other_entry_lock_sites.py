"""The other memory-entry-lock holders never deadlock against a forget (forget-locks2 REVIEW-1, Low).

`tests/test_forget_memory_entry_lock_order.py` races the four erases (chat delete, project delete,
finalization erase, convergence erase) against a forget. The other holders of
`entries._MEMORY_ENTRY_LOCK` that can reach the forget fence under it are pinned here:
`record_memory_entry` and `add_memory_fact`'s mirror rewrite (both through `add_memory_fact`),
`forget_profile_derivatives`, and `forget_structured_memory` as a correction calls it (outside a
forget command, so outside its fence).

The entry lock is wrapped so that the operation under test, once it holds the lock at the chosen
acquisition, keeps it for a second while "Forget <value>" for another chat starts. Before 0d61278
the holder then entered the fence the forget holds, while the forget waited for the entry lock.
Both threads are joined against a deadline; a detected deadlock is broken (the entry lock is a plain
Lock) so the session goes on.
"""
from __future__ import annotations

import json
import random
import threading
import time

import pytest

from core.memory import entries
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

DEADLINE_S = 30.0
HOLD_S = 1.0


class _HoldingLock:
    """`_MEMORY_ENTRY_LOCK` that, on the chosen acquisition by the thread under test, holds a moment."""

    def __init__(self, inner, *, nth: int, started: threading.Event):
        self.inner, self.nth, self.started, self.count = inner, nth, started, 0

    def acquire(self, blocking=True, timeout=-1):
        ok = self.inner.acquire(blocking, timeout)
        if ok and threading.current_thread().name == "holder":
            self.count += 1
            if self.count == self.nth:
                self.started.set()
                time.sleep(HOLD_S)
        return ok

    def release(self):
        self.inner.release()

    def locked(self):
        return self.inner.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _value(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5)) + f"-{rng.randint(1000, 9999)}"


def _policy(chat):
    from core.memory.entries import resolve_memory_access_policy

    return resolve_memory_access_policy(chat_id=chat)


# name -> (acquisition to hold at, operation on the other chat)
CASES = {
    "record_memory_entry": (1, lambda other: entries.add_memory_fact(
        "The north gate opens at noon.", session_id=other, source="manual")),
    "add_memory_fact_mirror": (2, lambda other: entries.add_memory_fact(
        "The boiler is serviced in May.", session_id=other, source="manual")),
    "forget_profile_derivatives": (1, lambda other: entries.forget_profile_derivatives(
        "north gate", access_policy=_policy(other), erasures={})),
    "correction_forget": (1, lambda other: entries.forget_memory("north gate", access_policy=_policy(other))),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_entry_lock_holder_and_a_forget_in_another_chat_never_deadlock(tmp_path, monkeypatch, case):
    rng = random.Random(f"7701-{case}")
    profile = _profile(tmp_path)
    chat, other = _chat(rng), _chat(rng)
    value = _value(rng)
    _ingest(profile, chat, [(_ts("2026-03-04T09:00:00"), f"The locker code is {value}.")])
    _ingest(profile, other, [(_ts("2026-03-04T09:00:00"), "The north gate opens at noon.")])
    entries._ensure_memory_files()
    with open(entries.memory_entries_path(), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"record_id": f"rec-{rng.randrange(10**8)}", "session_id": other,
                                 "text": "the north gate opens at noon", "scope": "chat"}) + "\n")

    nth, operation = CASES[case]
    started = threading.Event()
    inner = entries._MEMORY_ENTRY_LOCK
    monkeypatch.setattr(entries, "_MEMORY_ENTRY_LOCK", _HoldingLock(inner, nth=nth, started=started))
    out: dict = {}

    def hold():
        try:
            out["holder"] = operation(other)
        except BaseException as exc:  # noqa: BLE001 -- reported below
            out["holder_error"] = exc
        started.set()

    def forget():
        try:
            started.wait(DEADLINE_S)
            out["forget"] = _forget_command(profile, chat, f"Forget {value}")[1]
        except BaseException as exc:  # noqa: BLE001
            out["forget_error"] = exc

    threads = [threading.Thread(target=hold, name="holder", daemon=True),
               threading.Thread(target=forget, name="forget", daemon=True)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + DEADLINE_S
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    hung = [thread.name for thread in threads if thread.is_alive()]
    if hung:
        inner.release()  # break it: the forget finishes and frees the fence, then the holder
        for thread in threads:
            thread.join(DEADLINE_S)
        pytest.fail(f"{case}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s: a lock-order deadlock "
                    "between the forget fence and the memory entry lock", pytrace=False)
    holder = entries._MEMORY_ENTRY_LOCK
    assert holder.count >= nth, f"{case}: the operation took the entry lock {holder.count} time(s), expected {nth}"
    assert "holder_error" not in out and "forget_error" not in out, out
    assert out["forget"].startswith("Forget applied"), out
