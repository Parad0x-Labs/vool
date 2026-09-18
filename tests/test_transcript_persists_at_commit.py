"""The transcript row is written at the response-commit boundary, with the committed bytes.

Investigation 2 of the 2026-09-06 lane: the served UI showed the grounding gate's refusal while
``/api/chat/history`` held the model's pre-gate draft, because the lane appended its row before
``core.finalization`` ran the gates and the gate's amend matched on exact bytes it did not always
find. Under a served request the row is now STAGED by ``append_conversation_event`` and written
once by ``finalize_answer`` with the exact bytes it certifies. Lanes without request lineage keep
writing immediately. A turn that never seals is flushed at request end, marked ``unsealed``; a
typed no-answer terminal writes an empty assistant text, never the refused draft. Staging is
opt-in by the INGRESS: only a source_context stamped ``transcript_commit_boundary`` stages, so a
test or a channel that writes a row directly still lands it at once.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import (
    append_conversation_event,
    flush_staged_conversation_events,
    has_staged_conversation_event,
    open_transcript_commit_boundary,
)
from core.semantic.semantic_admissions import bound_request_context

DRAFT = "DRAFT: four fabricated headlines the gate will refuse"
#: What the /api/chat ingress stamps on every served turn's source_context.
SERVED = {"surface": "web", "transcript_commit_boundary": True}
COMMITTED = "Committed answer: the Golf is the smaller car."


def _rows() -> list[dict]:
    path = conversation_log_path()
    return [row for row in load_jsonl(path) if isinstance(row, dict)] if path.exists() else []


def _reset_log() -> None:
    path = conversation_log_path()
    if path.exists():
        path.unlink()


def _seal(text: str, *, turn_id: str) -> dict:
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"}, turn_id=turn_id)
    return finalize_answer(turn_id=turn_id, canonical_content=text)


def test_a_served_lane_row_is_written_once_at_the_seal_with_the_committed_bytes() -> None:
    _reset_log()
    request_id = "req:test:commit-1"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-commit", user_input="compare passat and golf", assistant_output=DRAFT)
        # Nothing reached the store yet: the row is staged for the commit boundary.
        assert has_staged_conversation_event(request_id) is True
        assert [row for row in _rows() if row.get("session_id") == "s-commit"] == []
        commit = _seal(COMMITTED, turn_id="t-commit-1")
    rows = [row for row in _rows() if row.get("session_id") == "s-commit"]
    assert len(rows) == 1
    row = rows[0]
    assert row["assistant"] == COMMITTED == commit["canonical_content"]
    assert row["commit_state"] == "committed"
    assert row["finalization_id"] == commit["finalization_id"]
    assert row["content_hash"] == commit["content_hash"]
    assert row["request_id"] == request_id
    assert has_staged_conversation_event(request_id) is False
    raw = conversation_log_path().read_text(encoding="utf-8")
    assert "DRAFT" not in raw


def test_a_lane_without_request_lineage_still_writes_immediately() -> None:
    _reset_log()
    append_conversation_event(session_id="s-channel", user_input="hi", assistant_output="hello from a channel")
    rows = [row for row in _rows() if row.get("session_id") == "s-channel"]
    assert len(rows) == 1
    assert rows[0]["assistant"] == "hello from a channel"
    assert "commit_state" not in rows[0]


def test_a_turn_that_never_seals_is_flushed_at_request_end_marked_unsealed() -> None:
    _reset_log()
    request_id = "req:test:unsealed-1"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-unsealed", user_input="q", assistant_output="lane text nobody sealed")
    # The served door's finally (or the stream's exit) flushes what the seal never took.
    assert flush_staged_conversation_events(request_id) == 1
    rows = [row for row in _rows() if row.get("session_id") == "s-unsealed"]
    assert len(rows) == 1
    assert rows[0]["assistant"] == "lane text nobody sealed"
    assert rows[0]["commit_state"] == "unsealed"
    assert flush_staged_conversation_events(request_id) == 0


def test_a_no_answer_terminal_writes_an_empty_assistant_row_never_the_refused_draft() -> None:
    from core.finalization import no_answer_terminal

    _reset_log()
    request_id = "req:test:no-answer-1"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-noanswer", user_input="q", assistant_output=DRAFT)
        no_answer_terminal(turn_id="t-na", reason_code="provider_no_content", persist=False)
    rows = [row for row in _rows() if row.get("session_id") == "s-noanswer"]
    assert len(rows) == 1
    assert rows[0]["assistant"] == ""
    assert rows[0]["commit_state"] == "no_answer_terminal"
    assert rows[0]["user"] == "q"
    assert "DRAFT" not in conversation_log_path().read_text(encoding="utf-8")


def test_the_publication_gate_reports_a_staged_row_as_amended_rather_than_missed() -> None:
    from core.grounding_publication import _amend_transcript

    request_id = "req:test:gate-1"
    lifecycle = SimpleNamespace(identity=SimpleNamespace(session_id="s-gate"))
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-gate", user_input="q", assistant_output=DRAFT)
        assert _amend_transcript(lifecycle, before=DRAFT, after="gated bytes") is True
    flush_staged_conversation_events(request_id)


def test_a_second_row_staged_under_the_same_request_is_not_taken_by_the_seal() -> None:
    """A7 mints ONE finalization per request; a lane that staged twice cannot have both rows
    certified. The seal takes the oldest row; the other is flushed at request end, unsealed."""
    _reset_log()
    request_id = "req:test:order"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-order", user_input="first", assistant_output="draft one")
        append_conversation_event(source_context=SERVED, session_id="s-order", user_input="second", assistant_output="draft two")
        _seal("answer one", turn_id="t-o1")
        assert has_staged_conversation_event(request_id) is True
    assert flush_staged_conversation_events(request_id) == 1
    rows = [row for row in _rows() if row.get("session_id") == "s-order"]
    assert [(row["user"], row["assistant"], row["commit_state"]) for row in rows] == [
        ("first", "answer one", "committed"),
        ("second", "draft two", "unsealed"),
    ]
    assert json.dumps(rows).count("draft one") == 0


def test_a_bound_request_without_a_declared_boundary_still_writes_immediately() -> None:
    """The proof chip's tests, channels and rigs write rows under a bound request id without an
    ingress that seals; they must keep landing at once (this is the class that the request-id
    inference broke: 30 proof-projection tests found an empty transcript)."""
    _reset_log()
    with bound_request_context("req:test:undeclared"):
        append_conversation_event(session_id="s-undeclared", user_input="q", assistant_output="written at once")
        rows = [row for row in _rows() if row.get("session_id") == "s-undeclared"]
        assert len(rows) == 1 and rows[0]["assistant"] == "written at once"
        assert rows[0]["request_id"] == "req:test:undeclared"
        assert has_staged_conversation_event("req:test:undeclared") is False


def test_a_writer_that_outlives_its_request_lands_at_once_after_the_boundary_closed() -> None:
    """A council run finishes minutes after the /api/council/resume request returned and writes its
    chat message with the served turn's context still bound (contextvars copy). The request-end
    flush already closed the boundary, so the row must land immediately -- staging it would leave
    it to nobody (tests/test_council_needs_attention.py measured zero rows)."""
    _reset_log()
    request_id = "req:test:background"
    open_transcript_commit_boundary(request_id)
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-bg", user_input="q1", assistant_output="in-request draft")
        _seal("in-request answer", turn_id="t-bg1")
    assert flush_staged_conversation_events(request_id) == 0  # request ended; boundary closed
    with bound_request_context(request_id):
        append_conversation_event(source_context=SERVED, session_id="s-bg", user_input="", assistant_output="council | run=r1 finished")
    rows = [row for row in _rows() if row.get("session_id") == "s-bg"]
    assert [(row["assistant"], row.get("commit_state")) for row in rows] == [
        ("in-request answer", "committed"),
        ("council | run=r1 finished", None),
    ]
    assert has_staged_conversation_event(request_id) is False
