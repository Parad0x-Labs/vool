"""A chat delete never drops another chat's transcript row appended while it runs (forget-locks2 REVIEW-1, D1).

`delete_conversation_session` read the whole conversation log, dropped the deleted chat's rows and
rewrote the file from that read. Nothing held the appenders off in between, so a row another chat
appended after the read was silently lost when the stale copy was written back.

The test parks the delete at its transcript rewrite (`entries.rewrite_jsonl` for the log) and lets
another chat append a turn. The appended row must survive the delete; the deleted chat's rows must
not. Every thread is joined against a deadline.
"""
from __future__ import annotations

import random
import threading

import pytest

from core.memory import entries
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile, _ts

pytestmark = pytest.mark.usefixtures("_hash_backend")
assert _hash_backend

DEADLINE_S = 30.0


def _chat(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def test_a_turn_appended_by_another_chat_during_a_delete_is_kept(tmp_path, monkeypatch):
    rng = random.Random(7601)
    profile = _profile(tmp_path)
    doomed, other = _chat(rng), _chat(rng)
    _ingest(profile, doomed, [(_ts("2026-03-04T09:00:00"), "The north gate opens at noon.")])
    _ingest(profile, other, [(_ts("2026-03-04T09:00:00"), "The boiler is serviced in May.")])
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

    def append():
        try:
            _ingest(profile, other, [(_ts("2026-03-04T10:00:00"), "The boiler code is on the fridge.")])
        except BaseException as exc:  # noqa: BLE001
            out["append_error"] = exc

    deleter = threading.Thread(target=delete, name="deleter", daemon=True)
    deleter.start()
    try:
        assert parked.wait(DEADLINE_S), "the delete never reached its transcript rewrite"
        appender = threading.Thread(target=append, name="appender", daemon=True)
        appender.start()
        appender.join(1.5)  # lands during the park, unless the delete holds the appenders off
    finally:
        go.set()
    deleter.join(DEADLINE_S)
    appender.join(DEADLINE_S)
    assert not deleter.is_alive() and not appender.is_alive(), "a thread hung"
    assert "delete_error" not in out and "append_error" not in out, out
    assert out["delete"] is True
    rows = entries._load_jsonl(log)
    texts = [str(row.get("user") or "") for row in rows if row.get("session_id") == other]
    assert any("boiler code is on the fridge" in text for text in texts), texts
    assert not any(row.get("session_id") == doomed for row in rows)
