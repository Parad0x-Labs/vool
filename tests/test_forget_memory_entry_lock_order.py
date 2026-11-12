"""A forget and the memory entries' own erasures, run at the same time, never deadlock (REVIEW-1, E1).

A forget holds the forget fence (`revocation.serialized()`) for its whole run and takes
`entries._MEMORY_ENTRY_LOCK` inside it (`forget_structured_memory`). Deleting a chat
(`delete_conversation_session`), deleting a project (`forget_sessions_memory`), the finalization
erase (`sweep_governed_memory_rows`) and the convergence erase (`forget_memory_record`) held the
entry lock and then entered the fence through `rewrite_jsonl` -> `_writing_rows`. With the two in
different threads, each held what the other waited for.

Each case starts the erase in one thread and, once that thread holds the entry lock (its rewrite is
reached, then held back one second so the forget surely starts), runs "Forget <value>" for another
chat in a second thread. Both are joined against a deadline: a hang fails the test instead of
hanging pytest (and a detected deadlock is broken, so later tests still run). Both must also
finish their work.
"""
from __future__ import annotations

import json
import random
import threading
import time

import pytest

from core.memory import entries, revocation
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

DEADLINE_S = 30.0
HOLD_S = 1.0


@pytest.fixture(autouse=True)
def _not_wedged():
    """A deadlock left by an earlier test holds these locks for good: fail fast rather than hang."""
    for name, lock in (("revocation fence", revocation._LOCK), ("entries._MEMORY_ENTRY_LOCK", entries._MEMORY_ENTRY_LOCK)):
        if not lock.acquire(timeout=10):
            pytest.fail(f"{name} is held by a thread an earlier deadlock left behind", pytrace=False)
        lock.release()
    yield


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _value(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5)) + f"-{rng.randint(1000, 9999)}"


ERASES = {
    "chat_delete": lambda other, record_id, request_id: entries.delete_conversation_session(other),
    "project_delete": lambda other, record_id, request_id: entries.forget_sessions_memory([other]),
    "finalization_erase": lambda other, record_id, request_id: entries.sweep_governed_memory_rows(request_id),
    "convergence_erase": lambda other, record_id, request_id: entries.forget_memory_record(record_id),
}


@pytest.mark.parametrize("erase", sorted(ERASES))
def test_a_forget_and_a_memory_erase_in_another_chat_never_deadlock(tmp_path, monkeypatch, erase):
    rng = random.Random(f"7401-{erase}")
    profile = _profile(tmp_path)
    chat, other = _chat(rng), _chat(rng)
    value = _value(rng)
    _ingest(profile, chat, [(_ts("2026-03-04T09:00:00"), f"The locker code is {value}.")])
    _ingest(profile, other, [(_ts("2026-03-04T09:00:00"), "The north gate opens at noon.")])
    record_id, request_id = f"rec-{rng.randrange(10**8)}", f"req-{rng.randrange(10**8)}"
    entries._ensure_memory_files()
    with open(entries.memory_entries_path(), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"record_id": record_id, "session_id": other, "request_id": request_id,
                                 "text": "the north gate opens at noon", "scope": "chat"}) + "\n")

    eraser_has_entry_lock = threading.Event()
    real_rewrite = entries.rewrite_jsonl
    held_back: list[int] = []

    def rewrite(path, rows):
        # Reached with the entry lock held. Hold it a moment so the forget is surely under way.
        # (A chat delete also rewrites its transcript before it takes the entry lock: not that one.)
        if threading.current_thread().name == "eraser" and not held_back and entries._MEMORY_ENTRY_LOCK.locked():
            held_back.append(1)
            eraser_has_entry_lock.set()
            time.sleep(HOLD_S)
        return real_rewrite(path, rows)

    monkeypatch.setattr(entries, "rewrite_jsonl", rewrite)
    out: dict = {}
    errors: dict = {}

    def run_erase():
        try:
            out["erase"] = ERASES[erase](other, record_id, request_id)
        except BaseException as exc:  # reported below
            errors["erase"] = exc
        eraser_has_entry_lock.set()

    def run_forget():
        try:
            eraser_has_entry_lock.wait(DEADLINE_S)
            out["forget"] = _forget_command(profile, chat, f"Forget {value}")[1]
        except BaseException as exc:
            errors["forget"] = exc

    threads = [threading.Thread(target=run_erase, name="eraser", daemon=True),
               threading.Thread(target=run_forget, name="forget", daemon=True)]
    for thread in threads:
        thread.start()
    end = time.monotonic() + DEADLINE_S
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    hung = [thread.name for thread in threads if thread.is_alive()]
    if hung:
        # Break the deadlock so the rest of the session can run: the entry lock is a plain Lock, which
        # any thread may release. The forget then finishes and frees the fence; the erase follows.
        entries._MEMORY_ENTRY_LOCK.release()
        for thread in threads:
            thread.join(DEADLINE_S)
        pytest.fail(f"{erase}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s: a lock-order deadlock "
                    "between the forget fence and the memory entry lock", pytrace=False)
    assert held_back, "the erase never reached its rewrite under the entry lock"
    assert not errors, errors
    assert out["erase"], out
    assert out["forget"].startswith("Forget applied"), out
    assert not any(str(row.get("record_id") or "") == record_id and not row.get("tombstone") and not row.get("erased_at")
                   and str(row.get("text") or "") for row in entries._load_jsonl(entries.memory_entries_path())
                   if str(row.get("session_id") or "") == other and str(row.get("status") or "active") == "active"), erase
