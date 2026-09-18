"""A stored answer whose own evidence refuted it must not be marked `valid`.

`candidate_knowledge_lane.validation_state` was written `"valid"` from a SHAPE check — did the
output parse, did it match the contract — so an audit whose every load-bearing claim was false was
stored valid at full trust and could be replayed from cache on the next identical request. A state
that says nothing about whether the content is true is a state that makes a wrong answer durable.

`audit_claims_contradicted` was already computed and set on the turn result, and then dropped: the
operator saw the annotation once, and the row stayed `valid`.

Deliberately not `invalidate_candidate`. The answer is not garbage, and the operator saw it with the
contradictions annotated. It must simply never be served again as though it had been verified.
"""
from __future__ import annotations

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.candidate_knowledge_lane import (
    build_task_hash,
    get_candidate_by_id,
    get_exact_candidate,
    mark_candidate_claims_contradicted,
    record_candidate_output,
)

EVIDENCE = {
    "workspace_audit_evidence": {
        "all_paths": ["a.py", "tests/test_a.py"],
        "inspected_paths": ["a.py"],
        "sources": {"a.py": "import os\n"},
    }
}


def _stored(text: str, *, marker: str) -> tuple[str, str]:
    task_hash = build_task_hash(
        normalized_input=f"audit {marker}", task_class="analysis", output_mode="plain_text"
    )
    candidate_id = record_candidate_output(
        task_hash=task_hash, task_id="t", trace_id="tr", task_class="analysis",
        task_kind="analysis", output_mode="plain_text", provider_name="p", model_name="m",
        raw_output=text, normalized_output=text, structured_output=None,
        confidence=0.9, trust_score=0.9, validation_state="valid",
    )
    return candidate_id, task_hash


def test_a_contradicted_answer_stops_being_valid(request) -> None:
    candidate_id, _hash = _stored("The repository has zero tests.", marker=request.node.name)
    assert get_candidate_by_id(candidate_id)["validation_state"] == "valid"

    result = enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9,
         "candidate_id": candidate_id},
        user_input="audit this project", session_id="verdict", source_context=dict(EVIDENCE),
    )

    assert result["audit_claims_contradicted"]
    assert result["audit_verdict_recorded"] is True
    assert get_candidate_by_id(candidate_id)["validation_state"] == "claims_contradicted"


def test_it_is_never_served_from_cache_again(request) -> None:
    """Setting the key and going no further is what the previous shape did."""

    candidate_id, task_hash = _stored("The repository has zero tests.", marker=request.node.name)
    assert get_exact_candidate(task_hash, output_mode="plain_text") is not None

    enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9,
         "candidate_id": candidate_id},
        user_input="audit this project", session_id="verdict", source_context=dict(EVIDENCE),
    )

    assert get_exact_candidate(task_hash, output_mode="plain_text") is None


def test_the_defect_kinds_are_recorded_on_the_row(request) -> None:
    """"Contradicted" without saying by what is not a verdict anyone can act on."""

    candidate_id, _hash = _stored("The repository has zero tests.", marker=request.node.name)
    enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9,
         "candidate_id": candidate_id},
        user_input="audit", session_id="verdict", source_context=dict(EVIDENCE),
    )

    metadata = get_candidate_by_id(candidate_id).get("metadata") or {}
    assert "tests_exist" in (metadata.get("claims_contradicted") or [])
    assert metadata.get("claims_contradicted_at")


def test_an_uncontradicted_answer_keeps_its_state(request) -> None:
    """The verdict must not be bought by marking everything suspect."""

    text = "The exception handling is broad; consider narrowing it."
    candidate_id, task_hash = _stored(text, marker=request.node.name)

    result = enforce_final_action_honesty(
        {"response": text, "confidence": 0.9, "candidate_id": candidate_id},
        user_input="audit", session_id="verdict", source_context=dict(EVIDENCE),
    )

    assert "audit_claims_contradicted" not in result
    assert get_candidate_by_id(candidate_id)["validation_state"] == "valid"
    assert get_exact_candidate(task_hash, output_mode="plain_text") is not None


def test_a_turn_with_no_candidate_id_still_annotates(request) -> None:
    """The annotation is the operator-facing half and must not depend on the bookkeeping half."""

    result = enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9},
        user_input="audit", session_id="verdict", source_context=dict(EVIDENCE),
    )

    assert result["audit_claims_contradicted"]
    assert "audit_verdict_recorded" not in result


def test_a_write_failure_never_costs_the_operator_their_answer(monkeypatch) -> None:
    import core.candidate_knowledge_lane as lane

    monkeypatch.setattr(
        lane, "mark_candidate_claims_contradicted",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    result = enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9, "candidate_id": "x"},
        user_input="audit", session_id="verdict", source_context=dict(EVIDENCE),
    )

    assert "The repository has zero tests." in result["response"]
    assert result["audit_claims_contradicted"]


def test_marking_an_unknown_candidate_is_a_no_op() -> None:
    assert mark_candidate_claims_contradicted("", kinds=("tests_exist",)) is False
    assert mark_candidate_claims_contradicted("no-such-id", kinds=("tests_exist",)) is False


def test_the_verdict_is_written_where_the_flag_was_dropped() -> None:
    """The flag existed and had no consumer — that is the shape being removed."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "core" / "agent_runtime" / "action_honesty_validator.py"
    ).read_text(encoding="utf-8")

    position = source.index('output["audit_claims_contradicted"]')
    assert "mark_candidate_claims_contradicted" in source[position:position + 1400]
