"""A memory save is an action with a checked result: what the keep step wrote is read back from the memory store
and filed as the turn's action receipt (checked when the row is there, "reported done" when it is not), and a
refused write is filed as not done. The receipt never rests on the reply's words."""
from __future__ import annotations

import uuid

from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home


def _filed(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def emit(context, *, event_type, message, details):
        seen.append({"event_type": event_type, **details})

    monkeypatch.setattr("core.runtime_task_events.emit_runtime_event", emit)
    return seen


def test_a_saved_fact_is_checked_against_the_store_and_a_missing_one_is_not(isolated_home, monkeypatch):
    from core.action_receipts import receipts_of_events
    from core.memory import keep_receipt
    from core.memory.entries import add_memory_fact

    seen = _filed(monkeypatch)
    chat = "chat-" + uuid.uuid4().hex[:8]
    fact = "my sister's bakery opens at " + str(6 + uuid.uuid4().int % 3) + " on Sundays"
    context = {"session_id": chat, "request_id": uuid.uuid4().hex}
    with keep_receipt.collecting() as receipt:
        assert add_memory_fact(fact, session_id=chat, scope="chat", access_policy=_policy(chat))
    keep_receipt.stamp(context, receipt)
    rows = receipts_of_events(seen)
    assert [(r["action_key"], r["state"], r["note"]) for r in rows] == [("approval.action.profile.remember", "checked", fact)]

    # A writer that said it saved, with no row behind it, is not shown as done and checked.
    ghost = KeepLike(saved=["the spare key is under the " + uuid.uuid4().hex[:4] + " pot"])
    keep_receipt.stamp({"session_id": chat, "request_id": uuid.uuid4().hex}, ghost)
    assert receipts_of_events(seen)[-1]["state"] == "mismatch"


def test_a_refused_write_is_not_done_and_a_turn_without_a_chat_files_nothing(isolated_home, monkeypatch):
    from core.action_receipts import receipts_of_events
    from core.memory import keep_receipt
    from core.memory.entries import add_memory_fact

    seen = _filed(monkeypatch)
    chat = "chat-" + uuid.uuid4().hex[:8]
    with keep_receipt.collecting() as receipt:
        assert not add_memory_fact("my bank card PIN is 4" + str(uuid.uuid4().int % 900 + 100), session_id=chat, scope="chat", access_policy=_policy(chat))
    keep_receipt.stamp({"session_id": chat, "request_id": uuid.uuid4().hex}, receipt)
    assert [(r["state"], r["note"]) for r in receipts_of_events(seen)] == [("failed", "")]

    with keep_receipt.collecting() as receipt:
        assert add_memory_fact("we walk the dog at " + str(uuid.uuid4().int % 4 + 5) + " every evening", session_id=chat, scope="chat", access_policy=_policy(chat))
    before = len(seen)
    keep_receipt.stamp({}, receipt)
    assert len(seen) == before


class KeepLike:
    def __init__(self, saved=(), refused=()):
        self.saved, self.refused, self.in_passing = list(saved), list(refused), False

    def as_dict(self):
        return {"saved": list(self.saved), "refused": list(self.refused), "in_passing": False}
