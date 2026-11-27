"""The Activity feed and the Proof Chip check each piece of text against erasure once per response.

Owner's iMac, 2026-11-27 (installed 0.8 build): after chat turns, GET /api/runtime/events went
1.8 s -> 4.9 s and GET /api/chat/proof 1.2 s -> 8.1 s, growing with every turn. Each listed turn's
witness re-read the session's event window and ran the serve gate on every string of it again,
each check on a fresh connection; the proof's binding lookup authorized every row of the whole
conversation log to find one.

* One response is one governance read scope: a verdict is reused only until another connection
  commits, so an erase or withhold committed while the response is built is seen by the next check.
* The answers are the same as without the scope, and a store error still fails every string closed.
* The witness parses only the execution events of its window; the binding lookup authorizes only
  the rows that bind.
"""
from __future__ import annotations

import contextlib
import hashlib
import json

from core import execution_truth, finalization, proof_projection
from core.memory import files as memory_files
from core.persistent_memory import append_conversation_event
from core.runtime_continuity import (
    append_runtime_event,
    list_runtime_session_events,
    list_runtime_session_events_of_types,
)
from core.runtime_task_events import emit_runtime_event
from core.semantic.semantic_admissions import bound_request_context
from storage.db import get_connection

SESSION = "chat-each-text-once"
WITHHELD_LINE = "a reply its owner withheld after it was shown"


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commit(sql: str, args: tuple) -> None:
    conn = get_connection()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _finalization(request_id: str, turn: str, content: str, availability: str) -> None:
    _commit(
        "INSERT OR IGNORE INTO a7_finalizations (finalization_id, semantic_result_id, turn_id, content_hash,"
        " canonical_content, status, request_id, payload_ref, availability)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        ("fc-" + request_id, "sr-" + request_id, turn, _hash(content), content, "committed",
         request_id, availability),
    )


def _erase(text: str) -> None:
    _commit(
        "INSERT INTO a7_governance_events (event_id, finalization_id, event_kind, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("tomb-" + _hash(text)[-12:], "no-live-row", finalization.EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
         _hash(text), "2026-11-27"),
    )


def _chat(turns: int = 4) -> None:
    """A chat of several turns whose events repeat the same lines, one of them withheld."""
    for t in range(turns):
        request, turn = f"req-{t}", f"turn-{t}"
        answer = f"Answer {t} to the question."
        with bound_request_context(request):
            append_conversation_event(session_id=SESSION, user_input=f"question {t}", assistant_output=answer)
        _finalization(request, turn, answer, finalization.AVAILABILITY_AVAILABLE)
        for event_type in ("task_received", "tool_executed", "task_completed"):
            # The real emit seam: it records the execution fact the witness holds the event against.
            emit_runtime_event(
                {"session_id": SESSION, "runtime_session_id": SESSION, "cancel_turn_id": turn},
                event_type=event_type,
                message=f"{event_type} of {turn}",
                details={"turn_key": turn, "request_id": request, "tool_name": "workspace.read_file",
                         "preview": WITHHELD_LINE, "shared": "the same line on every row"},
            )
    _finalization("req-withheld", "turn-withheld", WITHHELD_LINE, finalization.AVAILABILITY_WITHHELD)


def _count_verdicts(monkeypatch) -> list[str]:
    seen: list[str] = []
    original = finalization._availability_verdict

    def verdict(conn, clean):
        seen.append(clean)
        return original(conn, clean)

    monkeypatch.setattr(finalization, "_availability_verdict", verdict)
    return seen


def _without_scope(monkeypatch) -> None:
    monkeypatch.setattr(finalization, "availability_read_scope", contextlib.nullcontext)


def _proof(request: str = "req-3") -> dict:
    return proof_projection.build_turn_proof(session_id=SESSION, request_id=request)


def test_the_events_page_reads_each_text_once_and_answers_as_before(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    _chat()
    seen = _count_verdicts(monkeypatch)
    scoped = execution_truth.served_events_page(SESSION, after_seq=0, limit=200)
    assert len(seen) == len(set(seen)), "a repeated line was checked again inside one response"
    assert set(scoped["execution_truth"]) == {f"turn-{t}" for t in range(4)}
    assert all(item["preview"] == "" for item in scoped["events"]), "the withheld line is still blanked"
    with monkeypatch.context() as unscoped:
        _without_scope(unscoped)
        seen.clear()
        plain = execution_truth.served_events_page(SESSION, after_seq=0, limit=200)
    assert len(seen) > len(set(seen)), "without the scope the same lines are checked again"
    assert plain == scoped


def test_the_proof_reads_each_text_once_and_answers_as_before(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    _chat()
    seen = _count_verdicts(monkeypatch)
    scoped = _proof()
    assert scoped["bound"] is True
    assert len(seen) == len(set(seen))
    with monkeypatch.context() as unscoped:
        _without_scope(unscoped)
        plain = _proof()
    assert plain == scoped


def test_an_erase_committed_while_the_events_page_is_built_is_honoured(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    _chat(turns=2)
    shown = "task_completed of turn-1"
    original = execution_truth.turn_execution_summary
    after_erase: list[list[dict]] = []

    def summary(turn_key, *, session_id=""):
        if not after_erase:
            # The page already served `shown` from this scope; another connection erases it now.
            assert list_runtime_session_events(SESSION, limit=200)[-1]["message"] == shown
            _erase(shown)
            after_erase.append(list_runtime_session_events(SESSION, limit=200))
        return original(turn_key, session_id=session_id)

    monkeypatch.setattr(execution_truth, "turn_execution_summary", summary)
    execution_truth.served_events_page(SESSION, after_seq=0, limit=200)
    assert after_erase and after_erase[0][-1]["message"] == "", "the next check inside the scope saw the erase"


def test_an_erase_committed_while_the_proof_is_built_is_honoured(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    _chat(turns=2)
    line = "task_received of turn-1"
    original = proof_projection._load_session_events
    verdicts: list[bool] = []

    def load(session_id):
        assert finalization._AVAILABILITY_READ_SCOPE.get() is not None, "the proof runs under one scope"
        verdicts.append(finalization.writer_may_persist_text(line))
        _erase(line)
        verdicts.append(finalization.writer_may_persist_text(line))
        return original(session_id)

    monkeypatch.setattr(proof_projection, "_load_session_events", load)
    proof = _proof("req-1")
    assert verdicts == [True, False]
    assert line not in json.dumps(proof)


def test_a_store_error_fails_every_string_closed(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    _chat(turns=2)

    def broken(conn, clean):
        raise RuntimeError("store busy")

    monkeypatch.setattr(finalization, "_availability_verdict", broken)
    page = execution_truth.served_events_page(SESSION, after_seq=0, limit=200)
    assert page["events"]
    for item in page["events"]:
        assert item["message"] == ""
        assert item["shared"] == "" and item["turn_key"] == "" and item["preview"] == ""
    proof = _proof("req-1")
    text = json.dumps(proof)
    assert "the same line on every row" not in text
    assert "of turn-1" not in text


def test_the_witness_reads_its_window_by_type_and_nothing_else() -> None:
    for seq in range(230):
        append_runtime_event(
            session_id=SESSION,
            event_type="tool_executed" if seq % 3 == 0 else "task_received",
            message=f"row {seq}",
            details={"turn_key": f"turn-{seq // 10}", "tool_name": "workspace.read_file"},
        )
    window = list_runtime_session_events(SESSION, limit=400)
    typed = list_runtime_session_events_of_types(SESSION, execution_truth._EXECUTION_EVENT_RULES, limit=400)
    assert typed == [item for item in window if item["event_type"] in execution_truth._EXECUTION_EVENT_RULES]
    assert list_runtime_session_events_of_types(SESSION, (), limit=400) == []


def test_the_binding_lookup_authorizes_only_the_rows_that_bind(monkeypatch) -> None:
    for index in range(40):
        with bound_request_context(f"other-{index}"):
            append_conversation_event(session_id=f"other-{index % 5}", user_input="q", assistant_output="a")
    for answer in ("first answer", "second answer"):
        with bound_request_context("req-bound"):
            append_conversation_event(session_id=SESSION, user_input="q", assistant_output=answer)
    from core.memory import revocation

    original = revocation.authorize_payload
    asked: list[str] = []

    def authorize(payload, *args, **kwargs):
        asked.append(str(payload.get("assistant") or ""))
        if payload.get("assistant") == "second answer":
            return False  # the newest binding row is revoked: the older one answers, as load_jsonl did
        return original(payload, *args, **kwargs)

    monkeypatch.setattr(revocation, "authorize_payload", authorize)
    row = proof_projection._find_binding_row(SESSION, "req-bound")
    assert row is not None and row["assistant"] == "first answer"
    assert asked == ["second answer", "first answer"]
    served = [r for r in memory_files.load_jsonl(memory_files.conversation_log_path())
              if r.get("session_id") == SESSION and r.get("request_id") == "req-bound"]
    assert served[-1] == row
