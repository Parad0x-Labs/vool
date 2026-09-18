"""C19 slice 2: the ONE derived repair, proven at the router seam (free_local).

The spec's lane law is explicit: the derived repair flows ONLY on a
free_local manifest — every other lane is ratify-only. The SERVED repair
journey on a genuinely free_local classified lane is
tests/test_presentation_served_turns.py::test_s1b_free_local_served_journey_...
(the rig's default lane is deliberately cost_class=paid_cloud, so its
everyday turns are ratify-only). THIS file proves the repair's own mechanics
at the router seam — real ``MemoryFirstRouter._invoke_manifest``, scripted
adapter, free_local manifest — including the failure path (a quantity
rewriting repair refused; original prose restored) and the lane law, on the
same harness as tests/test_response_constraint_router.py.

Proven here:

- S1  election → one derived contract (origin="automatic") → the existing
      single retry → acceptance holds → the repaired table ships with every
      citation intact and the record reconciled (gap_detected=False);
- S1b the lane gate: on a paid lane the same election requests no transform —
      the prose ships unchanged, exactly once;
- S1c acceptance failure (a reworded quantity) → the ORIGINAL prose ships
      byte-identically, fallback="prose_default", and the canned fallback
      never touches the answer;
- S6  the bound repair guidance speaks honestly: the automatic phrasing is
      present and the user is never told their request was "explicitly
      requested".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest, ModelResponse

from tests.test_response_constraint_router import _certified_manifest

RETRY_MARKER = "using only the values already in your answer"

_PROSE = (
    "Plan A: deductible €10 [receipt: read 1 file, 0 writes], coverage basic.\n"
    "Plan B: deductible €25, coverage full."
)
_REPAIRED_TABLE = (
    "| Plan | Deductible | Coverage |\n|---|---|---|\n"
    "| A | €10 [receipt: read 1 file, 0 writes] | basic |\n"
    "| B | €25 | full |"
)
_QUANTITY_REWRITE_TABLE = (
    "| Plan | Deductible | Coverage |\n|---|---|---|\n"
    "| A | €10 [receipt: read 1 file, 0 writes] | basic |\n"
    "| B | €26 | full |"
)


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt="walk me through both plans in detail",
        messages=[
            {"role": "user", "content": "walk me through both plans in detail"},
        ],
        metadata={
            "defer_stream_until_verified": True,
        },
    )


def _invoke(adapter, source_context=None):
    from core.memory_first_router import MemoryFirstRouter

    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="presentation-selection-repair"),
            source_context=source_context if source_context is not None else {},
        )
    return response, error


def test_s1_election_flows_one_derived_repair_and_the_accepted_table_ships() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=_PROSE, usage={"prompt_tokens": 9, "output_tokens": 20}),
        ModelResponse(output_text=_REPAIRED_TABLE, usage={"prompt_tokens": 14, "output_tokens": 30}),
    ]
    source_context: dict = {}
    response, error = _invoke(adapter, source_context)

    assert error is None
    assert response is not None
    assert "[receipt: read 1 file, 0 writes]" in response.output_text
    assert "| A | €10" in response.output_text, response.output_text[:300]
    assert adapter.run_text_task.call_count == 2, "exactly one bounded repair"

    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.metadata["response_constraint_retry"] == 1
    assert retry_request.metadata["response_constraint_origin"] == "automatic"
    assert retry_request.temperature == 0.0
    instruction = retry_request.messages[-1]["content"]
    assert RETRY_MARKER in instruction
    assert "explicitly requested" not in instruction, (
        "the automatic guidance must never claim the user requested the shape"
    )

    filed = source_context.get("presentation_selection") or {}
    assert filed.get("elected") == "comparison_matrix"
    assert filed.get("gap_detected") is False, "the repaired bytes carry the elected shape"


def test_s1b_paid_lane_is_ratify_only_and_asks_no_transform() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=_PROSE, usage={"prompt_tokens": 9, "output_tokens": 20}),
    ]
    from core.memory_first_router import MemoryFirstRouter

    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=False),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="presentation-selection-paid"),
            source_context={},
        )

    assert error is None
    assert response.output_text == _PROSE, "a paid lane must not transform the answer"
    assert adapter.run_text_task.call_count == 1, (
        "ratify-only: the paid lane binds no repair call"
    )


def test_s1c_acceptance_failure_restores_the_original_prose() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=_PROSE, usage={"prompt_tokens": 9, "output_tokens": 20}),
        ModelResponse(
            output_text=_QUANTITY_REWRITE_TABLE, usage={"prompt_tokens": 14, "output_tokens": 30}
        ),
    ]
    source_context: dict = {}
    response, error = _invoke(adapter, source_context)

    assert error is None
    assert response.output_text == _PROSE, (
        "a repair that rewrites a quantity is refused; the original prose ships"
    )
    assert adapter.run_text_task.call_count == 2, "the repair was attempted, then rejected"
    filed = source_context.get("presentation_selection") or {}
    assert filed.get("fallback") == "prose_default"
    assert "No usable answer" not in response.output_text, (
        "the canned constraint fallback must never overwrite an honest answer"
    )


def test_s6_the_derived_contract_never_claims_an_explicit_request() -> None:
    from core.response_constraints import formatting_retry_instruction
    from core.turn_ir import ResponseConstraint

    derived = ResponseConstraint(presentation_format="table", origin="automatic")
    instruction = formatting_retry_instruction(derived)
    assert RETRY_MARKER in instruction
    assert "explicitly requested" not in instruction

    from core.memory_first_router import _response_constraint_guidance

    guidance = _response_constraint_guidance(derived)
    assert RETRY_MARKER in guidance
    assert "explicitly requested" not in guidance

    # The EXPLICIT path is byte-unchanged from base: it names the format and
    # may say who asked, because there the user really did.
    explicit = ResponseConstraint(presentation_format="table")
    explicit_instruction = formatting_retry_instruction(explicit)
    assert "the answer presented as a table" in explicit_instruction
    explicit_guidance = _response_constraint_guidance(explicit)
    assert "The user explicitly requested a table presentation" in explicit_guidance
