"""R4 action-claim gate against the independent review's corpus (tests/action_claim_review_corpus.py).

Every FP line is a model reply the gate must publish unchanged; every TOOL_RAN line is a reply
from a turn that ran a tool (or ran none and only says something is waiting for approval) and
must also pass unchanged; every FN line is a claim of something no tool did and must be corrected.
Driven through `gate_action_claims` with the turn's own recorders, not through the reader alone.
"""
from __future__ import annotations

import itertools

import pytest

from core.action_claim_gate import gate_action_claims
from core.grounding_lifecycle import record_model_authorship, register_required
from core.grounding_lifecycle import reset_for_tests as reset_lifecycles
from core.semantic.semantic_admissions import bound_request_context
from core.turn_model_call_ledger import begin_turn, record_tool_execution
from tests.action_claim_review_corpus import AMBIGUOUS_RECAP, FN, FP_EDGE, FP_MAIN, FP_RECAPS, TOOL_RAN

_ids = itertools.count()


@pytest.fixture(autouse=True)
def _fresh():
    from core.final_answer_authorship import reset_for_tests

    reset_lifecycles()
    reset_for_tests()
    yield
    reset_lifecycles()
    reset_for_tests()


def gate(text: str, tools=(), request: str = "help me with this") -> tuple[str, dict]:
    rid = f"req:review-corpus:{next(_ids)}"
    with bound_request_context(rid):
        context = {"session_id": "review-corpus", "request_id": rid, "turn_id": rid}
        register_required(context, request_text=request, reason_codes=("test",))
        record_model_authorship(context)
        begin_turn(context)
        for tool in tools:
            record_tool_execution(context, tool)
        return gate_action_claims(text, turn_id=rid, source_context=context, connected=lambda name: True)


@pytest.mark.parametrize("text", FP_MAIN + FP_EDGE + FP_RECAPS)
def test_ordinary_replies_pass_unchanged(text: str) -> None:
    published, record = gate(text)
    assert published == text, record


@pytest.mark.parametrize("text,tools", [tuple(row) for row in TOOL_RAN])
def test_replies_from_turns_that_ran_a_tool_or_await_approval_pass_unchanged(text: str, tools: list) -> None:
    published, record = gate(text, tools)
    assert published == text, record


@pytest.mark.parametrize("text", FN)
def test_claims_of_things_no_tool_did_are_corrected(text: str) -> None:
    published, record = gate(text)
    assert record["publication"] == "action_claims_corrected", record
    assert published != text


@pytest.mark.parametrize("text", AMBIGUOUS_RECAP)
def test_a_past_time_marker_reads_as_a_recap_even_for_a_short_send_line(text: str) -> None:
    # Known miss, by design until the gate can read the session's earlier tool runs.
    published, record = gate(text)
    assert published == text and record["publication"] == "stood_down:no_action_claims"
