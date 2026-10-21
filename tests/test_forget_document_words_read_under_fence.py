"""The forget's own reads of the chat's forgotten words cannot be interleaved by another forget.

`documents_carrying` and `forget_in_documents` read `forgotten_words` before they take
`chat_attachments._LOCK`. That read is not moved under `_LOCK`: both run only inside
`maybe_handle_memory_command`, whose `_command_erasure_fence` holds the forget authority's fence
(`revocation.serialized()`) for the whole command, so no other forget can commit between the read
and the records it judges. Taking the fence under `_LOCK` is the order that deadlocked (REVIEW-3/4,
L1). This pins both halves: at each read the fence is held and `_LOCK` is not, and a second forget
started at that moment, in another chat and another thread, stays blocked until the first is done.
"""
from __future__ import annotations

import random
import threading

import pytest

from core import chat_attachments as ca
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

DEADLINE_S = 60.0


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _value(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5)) + f"-{rng.randint(1000, 9999)}"


def _attach(chat: str, turn: str, text: str, name: str) -> dict:
    doc = ca.stage_document(session_id=chat, data=text.encode("utf-8"), declared_name=name)
    ca.bind_to_turn(session_id=chat, turn_id=turn, attachment_ids=[doc["id"]])
    ca.release_turn(session_id=chat, turn_id=turn, outcomes=None)
    return doc


def test_no_forget_lands_between_a_forgets_word_read_and_its_document_pass(tmp_path, monkeypatch):
    from core.memory import revocation

    rng = random.Random(7302)
    profile = _profile(tmp_path)
    first, second = _chat(rng), _chat(rng)
    value, other = _value(rng), _value(rng)
    _ingest(profile, first, [(_ts("2026-03-04T09:00:00"), f"The locker code is {value}.")])
    _ingest(profile, second, [(_ts("2026-03-04T09:00:00"), f"The gate code is {other}.")])
    doc = _attach(first, "turn-1", f"Lockers.\nCode {value} for the blue one.\n", "lockers.txt")
    _attach(second, "turn-1", f"Gates.\nCode {other} for the north gate.\n", "gates.txt")

    real = ca.forgotten_words
    reads: list[dict] = []
    competitor: dict = {}
    me = threading.get_ident()

    def run_competitor():
        competitor["reply"] = _forget_command(profile, second, f"Forget {other}")[1]

    def spy(session_id, *, settled=True):
        if threading.get_ident() == me and not settled:
            if "thread" not in competitor:
                competitor["thread"] = threading.Thread(target=run_competitor, daemon=True, name="second-forget")
                competitor["thread"].start()
            competitor["thread"].join(1.0)
            held = getattr(revocation._LOCAL, "held", {})
            reads.append({"fence": revocation._directory() in held, "attachments_lock": ca._LOCK._is_owned(),
                          "second_forget_blocked": competitor["thread"].is_alive() and "reply" not in competitor})
        return real(session_id, settled=settled)

    monkeypatch.setattr(ca, "forgotten_words", spy)
    reply = _forget_command(profile, first, f"Forget {value}")[1]
    assert reply.startswith("Forget applied"), reply
    # The rewrite's read and the verifier's read, each under the fence, outside `_LOCK`, uninterleaved.
    assert len(reads) >= 2, reads
    assert all(r == {"fence": True, "attachments_lock": False, "second_forget_blocked": True} for r in reads), reads

    competitor["thread"].join(DEADLINE_S)
    assert not competitor["thread"].is_alive(), "the second forget never finished"
    assert competitor["reply"].startswith("Forget applied"), competitor["reply"]
    assert ca.documents_carrying(first, value) == [] and ca.documents_carrying(second, other) == []
    assert value not in ca._open_regular(ca._bytes_path(doc["id"])).decode("utf-8")
