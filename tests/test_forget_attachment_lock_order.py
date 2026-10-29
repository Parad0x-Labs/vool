"""A forget and the chat's attachment store, run at the same time, never deadlock (REVIEW-3/4, L1).

A forget holds the forget authority's fence (`revocation.serialized()`, a process-wide RLock) for
its whole run and takes `chat_attachments._LOCK` inside it. Staging a paste or a picked file held
`_LOCK` and then entered the fence (`revocation.writing`), and an image-only or picked-file turn
built its serve screen (`forgotten_words`, the fence again) while holding `_LOCK`. Two threads
taking the two locks in opposite orders froze each other, and every memory writer behind them.

Here a real forget races a paste carrying the forgotten value, an image-only turn, a picked-file
turn, and a serve of another chat's documents (the Windows port's reproduction), over many rounds. Every thread is joined with a deadline: a hang fails the test instead of
hanging pytest. The forget's guarantees are checked on every round: the forget applies, and no
stored record carries the forgotten value (a paste staged after the forget committed is refused).
"""
from __future__ import annotations

import random
import struct
import threading
import time
import zlib

import pytest

from core import chat_attachments as ca
from core.memory.carriers import carries
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

ROUNDS = 12
#: Per round. A healthy round takes about a second; a deadlocked one never ends.
ROUND_DEADLINE_S = 45.0
#: Each side's repeats inside one round, so the turns overlap the forget's whole run.
REPEATS = 4


@pytest.fixture(autouse=True)
def _not_wedged():
    """A deadlock left by an earlier test holds both locks for good: fail fast rather than hang."""
    from core.memory import revocation

    for name, lock in (("revocation._LOCK", revocation._LOCK), ("chat_attachments._LOCK", ca._LOCK)):
        if not lock.acquire(timeout=10):
            pytest.fail(f"{name} is held by a thread an earlier deadlock left behind", pytrace=False)
        lock.release()
    yield


def _png() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00" * 5)) + chunk(b"IEND", b"")


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _value(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5)) + f"-{rng.randint(1000, 9999)}"


def _race(workers: dict, *, deadline_s: float, label: str) -> dict[str, BaseException]:
    """Start every worker together; join each against one deadline. A thread still alive fails."""
    barrier = threading.Barrier(len(workers))
    errors: dict[str, BaseException] = {}

    def run(name, fn):
        try:
            barrier.wait(timeout=deadline_s)
            fn()
        except BaseException as exc:  # noqa: BLE001 -- reported below, by worker
            errors[name] = exc

    threads = [threading.Thread(target=run, args=(name, fn), name=f"lock-order-{name}", daemon=True)
               for name, fn in workers.items()]
    for thread in threads:
        thread.start()
    end = time.monotonic() + deadline_s
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    hung = [thread.name for thread in threads if thread.is_alive()]
    if hung:
        pytest.fail(f"{label}: {', '.join(hung)} still running after {deadline_s:.0f}s: "
                    "a lock-order deadlock between the forget and the attachment store", pytrace=False)
    return errors


def _turn(chat: str, turn: str, *, data: bytes, name: str, media_type: str) -> list[dict]:
    record = ca.stage_attachment(session_id=chat, declared_name=name, declared_type=media_type, data=data)
    ca.bind_to_turn(session_id=chat, turn_id=turn, attachment_ids=[record["id"]])
    try:
        return ca.model_attachments_for_turn(session_id=chat, turn_id=turn, question="what is in it?")
    finally:
        ca.release_turn(session_id=chat, turn_id=turn, outcomes=None)


def test_a_forget_racing_uploads_and_attachment_turns_never_deadlocks_and_keeps_its_guarantees(tmp_path):
    rng = random.Random(7301)
    profile = _profile(tmp_path)
    png = _png()
    other = _chat(rng)
    shared = ca.stage_document(session_id=other, data=b"Another chat's notes.\nNothing forgotten here.\n",
                               declared_name="other.txt")
    ca.bind_to_turn(session_id=other, turn_id="turn-0", attachment_ids=[shared["id"]])
    for round_no in range(ROUNDS):
        chat = _chat(rng)
        value = _value(rng)
        _ingest(profile, chat, [(_ts("2026-03-04T09:00:00"), f"The locker code is {value}.")])
        kept = ca.stage_document(session_id=chat, data=f"Locker list.\nCode {value} for the blue one.\nEnd.\n".encode(),
                                 declared_name="lockers.txt")
        ca.bind_to_turn(session_id=chat, turn_id="turn-0", attachment_ids=[kept["id"]])
        ca.release_turn(session_id=chat, turn_id="turn-0", outcomes=None)

        replies: list[str] = []
        refused: list[str] = []
        served: list[str] = []

        def forget(chat=chat, value=value):
            handled, reply = _forget_command(profile, chat, f"Forget {value}")
            replies.append(reply)

        def upload(chat=chat, value=value):
            for i in range(REPEATS):
                try:
                    ca.stage_document(session_id=chat, data=f"Upload {i}.\nThe code is {value}.\n".encode(),
                                      declared_name=f"upload-{i}.txt")
                except ca.AttachmentRefused as exc:
                    refused.append(exc.code)

        def image_turn(chat=chat):
            for i in range(REPEATS):
                served.extend(e.get("name", "") for e in _turn(chat, f"img-{i}", data=png, name=f"shot-{i}.png",
                                                              media_type="image/png"))

        def picked_turn(chat=chat):
            for i in range(REPEATS):
                served.extend(e.get("name", "") for e in _turn(chat, f"file-{i}", data=f"Plain notes {i}.\n".encode(),
                                                              name=f"notes-{i}.txt", media_type="text/plain"))

        def serve_other_chat():
            for _ in range(REPEATS):
                served_other.extend(e.get("name", "") for e in ca.evidence_items_for_turn(session_id=other, turn_id="turn-0"))
                ca.model_attachments_for_turn(session_id=other, turn_id="turn-0", question="notes?")
                ca.carried_documents_for_turn(session_id=other, exclude_turn_id="turn-x")

        served_other: list[str] = []
        errors = _race({"forget": forget, "upload": upload, "image_turn": image_turn, "picked_turn": picked_turn,
                        "serve_other_chat": serve_other_chat},
                       deadline_s=ROUND_DEADLINE_S, label=f"round {round_no}")
        assert not errors, (round_no, errors)
        assert len(replies) == 1 and replies[0].startswith("Forget applied"), (round_no, replies)
        # A paste staged after the forget committed saw it and was refused; one staged before was erased.
        assert set(refused) <= {"forgotten_content"}, (round_no, refused)
        assert not ca.documents_still_carrying(session_id=chat, carries=lambda words: carries(words, value)), round_no
        assert not any(value in name for name in served), (round_no, served)
        assert served_other and set(served_other) == {"other.txt"}, (round_no, served_other)
