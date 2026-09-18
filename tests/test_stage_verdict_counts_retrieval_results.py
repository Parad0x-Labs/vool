"""The terminal-stage verdict counts what retrieval RETURNED, not only that it ran.

Measured 2026-09-06 (served comparison, six notes bound and read by the model): the turn's stage
verdict read `retrieval_empty -- search ran and returned nothing -- check query generation and
providers`. `observation_from_trace` marked retrieval as attempted for any search/retrieval event and
never filled `retrieval_result_count`, so every retrieving turn classified as empty.
"""
from __future__ import annotations

from core.turn_failure_stage import TerminalState, classify_turn, observation_from_trace

COMPLETED_NESTED = {"event_type": "web_retrieval_completed", "plan_id": "livedata-1",
                    "receipts": [{"schema": "vool.web_retrieval_receipt.v1", "action": "market_quote", "status": "available", "source_count": 1}]}
COMPLETED_FLAT = {"event_type": "web_retrieval_completed", "schema": "vool.web_retrieval_receipt.v1", "action": "initial_search",
                  "status": "available", "source_count": 2, "kind": "adaptive_research"}
BOUND = {"event_type": "evidence_bound_to_synthesis", "note_ids": ["evnote-1", "evnote-2", "evnote-3"], "source_count": 3}
STARTED = {"event_type": "web_retrieval_started", "kind": "adaptive_research"}
CALL = {"event_type": "model.call_completed"}


def test_a_turn_whose_retrieval_returned_notes_is_not_retrieval_empty() -> None:
    obs = observation_from_trace([STARTED, COMPLETED_FLAT, BOUND, CALL])
    assert obs.retrieval_attempted is True
    assert obs.retrieval_result_count >= 2, obs
    assert classify_turn(obs) != TerminalState.RETRIEVAL_EMPTY


def test_nested_receipts_count_their_sources() -> None:
    obs = observation_from_trace([STARTED, COMPLETED_NESTED, CALL])
    assert obs.retrieval_result_count == 1, obs


def test_a_retrieval_that_truly_returned_nothing_still_reads_empty() -> None:
    empty = {"event_type": "web_retrieval_completed", "schema": "vool.web_retrieval_receipt.v1", "status": "unavailable", "source_count": 0}
    obs = observation_from_trace([STARTED, empty])
    assert obs.retrieval_attempted is True and obs.retrieval_result_count == 0
    assert classify_turn(obs) == TerminalState.RETRIEVAL_EMPTY
