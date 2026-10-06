"""Evidence allowance is explicit and stays inside resolved reader headroom."""
from types import SimpleNamespace

import pytest

from core import context_retrieval as cr
from core.context_capsule_v2 import resolve_budget


def test_default_budget_preserves_existing_economy_contract():
    budget = resolve_budget(bucket="B", role="general")
    assert budget.evidence_target_tokens == 420
    assert budget.free_tokens <= 2048


@pytest.mark.parametrize("requested", [0, 420, 8192, 16384, 100000])
def test_quality_allowance_never_borrows_output_or_transcript_capacity(requested):
    budget = resolve_budget(
        bucket="D", role="heavy_reasoning", output_reserve_tokens=2048,
        transcript_tokens=2000, evidence_target_tokens=requested,
        retrieval_ceiling_tokens=requested,
    )
    assert 0 <= budget.evidence_target_tokens <= budget.free_tokens
    assert budget.free_tokens + budget.pin_ceiling_tokens + budget.recent_floor_tokens <= budget.pack_target_tokens
    assert budget.pack_target_tokens + budget.output_reserve_tokens <= budget.num_ctx


def test_exhausted_context_has_zero_evidence_allowance():
    budget = resolve_budget(
        bucket="B", role="general", transcript_tokens=100000,
        evidence_target_tokens=16384, retrieval_ceiling_tokens=16384,
    )
    assert budget.free_tokens == budget.evidence_target_tokens == 0


def test_declared_quality_capacity_is_available_without_ranking_changes():
    default = resolve_budget(bucket="D", role="heavy_reasoning")
    quality = resolve_budget(
        bucket="D", role="heavy_reasoning", evidence_target_tokens=8192,
        retrieval_ceiling_tokens=8192, output_reserve_tokens=2048,
    )
    assert quality.evidence_target_tokens == quality.free_tokens == 8192
    assert quality.min_score == default.min_score
    assert quality.num_ctx == default.num_ctx
    assert quality.output_reserve_tokens == 2048


def test_distillation_uses_the_call_budget_without_changing_global_default():
    bodies = [
        "The archivist prepared a diagram of the old rail station. " + "The ticket office details remain in the archive. " * 5,
        "The archivist prepared a diagram of the harbour tram station. " + "The platform details remain in the archive. " * 5,
        "The archivist prepared a diagram of the canal depot station. " + "The signal box details remain in the archive. " * 5,
        "The archivist prepared a diagram of the hillside station. " + "The crossing details remain in the archive. " * 5,
    ]
    bodies = [body.rstrip() for body in bodies]
    sources = [
        SimpleNamespace(body=body, occurrence_id=str(i), role="user",
                        authority="observed-user-statement", chat_scope="budget-fixture",
                        body_integrity="verified", status="active")
        for i, body in enumerate(bodies)
    ]
    kwargs = dict(record_sources=sources, record_roles=[("user", "")]*len(sources),
                  semantic_record_indices=set(range(len(sources))))
    query = "What station diagrams did the archivist prepare?"
    low, low_telemetry = cr._distill_retrieved_hits(query, [(body, 1.0) for body in bodies], target_tokens=80, **kwargs)
    high, high_telemetry = cr._distill_retrieved_hits(query, [(body, 1.0) for body in bodies], target_tokens=8192, **kwargs)
    assert cr._CAPSULE_TARGET_TOKENS == 420
    assert len(high_telemetry["source_unit_refs"]) == 4
    assert len(high) > len(low)
    assert len("\n".join(low_telemetry["selected_facts"])) <= 80*4
    assert all(body in high for body in bodies)


def test_invalid_allowance_is_refused_instead_of_silently_expanded():
    with pytest.raises(ValueError):
        resolve_budget(bucket="B", role="general", evidence_target_tokens=-1)
    with pytest.raises(ValueError):
        resolve_budget(bucket="B", role="general", retrieval_ceiling_tokens=-1)
