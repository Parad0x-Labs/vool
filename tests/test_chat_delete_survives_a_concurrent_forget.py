"""A chat delete succeeds when another chat's forget lands while it runs (forget-locks2 REVIEW-1, D2).

`delete_conversation_session` rewrote the conversation log from a read taken outside the forget
fence. When another chat's forget erased its rows in between, the stale copy still carried the
forgotten value, the fenced rewrite refused it ("memory write carries revoked content") and the
delete raised. Fail closed, but the user's delete failed.

The test parks the delete at its transcript rewrite and runs "Forget <value>" in another chat. The
delete must succeed, the forget must apply, and the forgotten value must not be written back.
"""
from __future__ import annotations

import json
import random
import threading

import pytest

from core.memory import entries
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

DEADLINE_S = 60.0


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _value(rng: random.Random) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5)) + f"-{rng.randint(1000, 9999)}"


def test_a_delete_with_another_chats_forget_landing_mid_delete_succeeds(tmp_path, monkeypatch):
    rng = random.Random(7602)
    profile = _profile(tmp_path)
    forgetting, doomed = _chat(rng), _chat(rng)
    value = _value(rng)
    _ingest(profile, forgetting, [(_ts("2026-03-04T09:00:00"), f"The locker code is {value}.")])
    _ingest(profile, doomed, [(_ts("2026-03-04T09:00:00"), "The north gate opens at noon.")])
    log = entries.conversation_log_path()

    parked, go = threading.Event(), threading.Event()
    real = entries.rewrite_jsonl

    def rewrite(path, rows):
        if threading.current_thread().name == "deleter" and str(path) == str(log) and not parked.is_set():
            parked.set()
            go.wait(DEADLINE_S)
        return real(path, rows)

    monkeypatch.setattr(entries, "rewrite_jsonl", rewrite)
    out: dict = {}

    def delete():
        try:
            out["delete"] = entries.delete_conversation_session(doomed)
        except BaseException as exc:  # noqa: BLE001 -- asserted below
            out["delete_error"] = exc

    def forget():
        try:
            out["forget"] = _forget_command(profile, forgetting, f"Forget {value}")[1]
        except BaseException as exc:  # noqa: BLE001
            out["forget_error"] = exc

    deleter = threading.Thread(target=delete, name="deleter", daemon=True)
    deleter.start()
    try:
        assert parked.wait(DEADLINE_S), "the delete never reached its transcript rewrite"
        forgetter = threading.Thread(target=forget, name="forgetter", daemon=True)
        forgetter.start()
        forgetter.join(3.0)  # lands during the park, unless the delete holds the forget off
    finally:
        go.set()
    deleter.join(DEADLINE_S)
    forgetter.join(DEADLINE_S)
    assert not deleter.is_alive() and not forgetter.is_alive(), "a thread hung"
    assert "delete_error" not in out, repr(out.get("delete_error"))
    assert "forget_error" not in out, out
    assert out["delete"] is True
    assert out["forget"].startswith("Forget applied"), out["forget"]
    rows = entries._load_jsonl(log)
    assert not any(row.get("session_id") == doomed for row in rows)
    assert value.lower() not in json.dumps(rows, ensure_ascii=False).lower()
