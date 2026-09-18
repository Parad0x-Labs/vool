"""The proof carries per-stage timings read from the stamps the turn's events already carry.

No invented first-token time: every window is the span between two existing runtime events
(`created_at` on the turn's own rows). A stage with no events has no window.
"""
from __future__ import annotations

from core.proof_projection import build_turn_proof
from tests.test_proof_projection import _bind_turn, _emit_turn_event, _insert_finalization


def test_timeline_windows_come_from_existing_event_stamps() -> None:
    session, request, turn = "tl-sess-1", "tl-req-1", "tl-turn-1"
    _bind_turn(session, request, "Answer.")
    _insert_finalization(request, turn, "Answer.")
    _emit_turn_event(session, turn, request, "task_received", {})
    _emit_turn_event(session, turn, request, "web_retrieval_started", {"kind": "adaptive_research"})
    _emit_turn_event(session, turn, request, "web_retrieval_completed", {"kind": "adaptive_research", "schema": "vool.web_retrieval_receipt.v1", "source_count": 2, "status": "available"})
    _emit_turn_event(session, turn, request, "model.call_started", {})
    _emit_turn_event(session, turn, request, "model.call_completed", {})
    _emit_turn_event(session, turn, request, "task_completed", {})
    proof = build_turn_proof(session_id=session, request_id=request)
    timeline = proof["expanded"]["timeline"]
    stages = {row["stage"]: row for row in timeline}
    assert {"total", "retrieval", "synthesis"} <= set(stages), stages
    for row in timeline:
        assert row["ms"] is None or row["ms"] >= 0
        assert row["started_at"] and row["ended_at"]
    assert "first_token" not in stages, "no invented first-token time"


def test_a_turn_without_retrieval_has_no_retrieval_window() -> None:
    session, request, turn = "tl-sess-2", "tl-req-2", "tl-turn-2"
    _bind_turn(session, request, "Hello.")
    _insert_finalization(request, turn, "Hello.")
    _emit_turn_event(session, turn, request, "task_received", {})
    _emit_turn_event(session, turn, request, "model.call_started", {})
    _emit_turn_event(session, turn, request, "model.call_completed", {})
    _emit_turn_event(session, turn, request, "task_completed", {})
    proof = build_turn_proof(session_id=session, request_id=request)
    stages = {row["stage"] for row in proof["expanded"]["timeline"]}
    assert "retrieval" not in stages and "synthesis" in stages, stages
