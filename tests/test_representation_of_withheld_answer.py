"""Re-presenting a withheld answer yields a typed refusal, never model prose or a fallback table.

Measured 2026-09-06 (served probe): turn 2's comparison was REFUSED (nothing retrieved supported
it). Turn 3, "put that in a table", rendered a table whose only row read "No usable answer was
produced for this request."; turn 4, "shorter please", was read as a fresh DIRECT turn and published
the model's unsupported comparison verbatim -- the very text the previous turn had refused.
"""
from __future__ import annotations

import uuid

from core.grounding_lifecycle import (
    EXIT_REFUSED,
    STAGE_PUBLISHED,
    adopt_previous_publication_if_representation,
    lifecycle_for_context,
    record_publication,
    register_required,
    re_presentation_target,
)
from core.grounding_publication import publication_verdict


def _session() -> str:
    return "sess-withheld-" + uuid.uuid4().hex[:8]


def _previous(session: str, state: str, claims: list[dict] | None = None) -> str:
    ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    lifecycle_id = register_required(ctx, request_text="compare the vw passat and the vw golf in detail: production periods, sales, regions, engines and prices")
    payload = {"state": state, "supported_claim_count": len(claims or []), "withheld_claims": [],
               "claim_support": {"claims": claims or []}}
    assert record_publication(lifecycle_id, payload)
    return lifecycle_id


def test_shorter_please_is_a_re_presentation_of_the_previous_published_answer() -> None:
    session = _session()
    _previous(session, STAGE_PUBLISHED, [{"text": "The Passat has been produced since 1973.", "status": "supported"}])
    assert re_presentation_target("shorter please", session) is not None
    assert re_presentation_target("make it briefer", session) is not None
    assert re_presentation_target("explain photosynthesis in a table", session) is None


def test_a_table_of_a_refused_answer_is_a_typed_refusal_not_a_fallback_table() -> None:
    session = _session()
    _previous(session, EXIT_REFUSED)
    ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    assert adopt_previous_publication_if_representation(ctx, request_text="put that in a table") is True
    lifecycle = lifecycle_for_context(ctx)
    assert lifecycle is not None
    verdict = publication_verdict(lifecycle, "| Model | Sales |\n|---|---|\n| Golf | 37 million |")
    assert verdict.state != STAGE_PUBLISHED, verdict
    assert "37 million" not in verdict.content
    assert "withheld" in verdict.content.lower(), verdict.content


def test_shorter_of_a_refused_answer_never_publishes_model_prose() -> None:
    session = _session()
    _previous(session, EXIT_REFUSED)
    ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    assert adopt_previous_publication_if_representation(ctx, request_text="shorter please") is True
    verdict = publication_verdict(lifecycle_for_context(ctx), "Golf: 37 million sold; Passat: 34 million.")
    assert verdict.state != STAGE_PUBLISHED
    assert "37 million" not in verdict.content


def test_a_new_subject_after_a_refused_answer_is_not_a_re_presentation() -> None:
    session = _session()
    _previous(session, EXIT_REFUSED)
    ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    assert adopt_previous_publication_if_representation(ctx, request_text="what is the population of vilnius, in a table") is False


def test_a_re_presentation_of_a_re_presentation_inherits_the_original_support() -> None:
    """comparison (partial) -> "put that in a table" -> "shorter please": the third turn adopts the
    comparison's supported rows through the table turn, whose own bytes asserted nothing."""
    from core.grounding_lifecycle import EXIT_PARTIAL, record_typed_observations
    session = _session()
    _previous(session, EXIT_PARTIAL, [{"text": "Golf production began in 1974 and Passat production in 1973.", "status": "supported"},
                                      {"text": "Sales: Golf about 37 million units by end of 2024, Passat about 34 million.", "status": "supported"}])
    table_ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    assert adopt_previous_publication_if_representation(table_ctx, request_text="put that in a table") is True
    table_lifecycle = lifecycle_for_context(table_ctx)
    assert len(table_lifecycle.typed_observations) == 2
    # the table turn publishes bytes that assert nothing new (a header-only table, say)
    assert record_publication(table_lifecycle.lifecycle_id, {"state": STAGE_PUBLISHED, "coverage": "no_claims", "supported_claim_count": 0, "claim_support": {"claims": []}})
    shorter_ctx = {"session_id": session, "runtime_session_id": session, "request_id": "req-" + uuid.uuid4().hex[:8]}
    assert adopt_previous_publication_if_representation(shorter_ctx, request_text="shorter please") is True
    shorter_lifecycle = lifecycle_for_context(shorter_ctx)
    assert len(shorter_lifecycle.typed_observations) == 2, "the comparison's support travels through the table turn"
    verdict = publication_verdict(shorter_lifecycle, "Golf production began in 1974 and Passat production in 1973.")
    assert verdict.state in {STAGE_PUBLISHED, EXIT_PARTIAL}, verdict
    assert "1974" in verdict.content
