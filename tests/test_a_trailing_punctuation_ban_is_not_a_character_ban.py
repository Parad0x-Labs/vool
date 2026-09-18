"""'No punctuation at the end' scopes to the end; it never convicts the deliverable's own brackets.

Measured live 2026-08-15 (session openclaw:758b2dcdb797d010a0f5, build 72012401): the deterministic
sequence lane computed the exact requested "[4,3,10]" for the operator's verbatim array turn, and
the raw-output-contract seal -- reading "No punctuation at the end." as a blanket ``no_punctuation``
-- stripped the brackets the SAME directive demanded ("with brackets and commas"), judged the rest
non-compliant, and shipped "The generated reply did not contain the requested output format" over a
correct answer. The turn_trace showed task_class=direct_math_fast_path with the notice as the
fast-path response.

Two rules land here: a trailing-scope phrase parses to ``no_trailing_punctuation`` (strip only
sentence punctuation at the very end), and a blanket "no punctuation" is downgraded to trailing
scope when the same directive explicitly requests punctuation-bearing structure -- the specific ask
beats the generic ban.
"""

from __future__ import annotations

import pytest

from core.raw_output_contract import (
    apply_raw_output_contract,
    parse_raw_output_contract,
    raw_output_contract_from_metadata,
)

MEASURED_TURN = (
    "Array A starts with [1, 2, 3]. I append the number 4. Then I remove the number 2. "
    "Then I multiply the first element by 10. Then I reverse the entire array. What is the "
    "final state of Array A? Output exactly the array with brackets and commas, but with "
    "absolutely no spaces (e.g., [10,3,4]). No punctuation at the end. No markdown. "
    "No explanation."
)


def test_the_measured_turn_parses_trailing_scope_not_a_blanket_ban() -> None:
    contract = parse_raw_output_contract(MEASURED_TURN)
    assert contract is not None
    assert contract.no_trailing_punctuation is True
    assert contract.no_punctuation is False


def test_the_computed_answer_survives_the_seal_untouched() -> None:
    contract = parse_raw_output_contract(MEASURED_TURN)
    applied = apply_raw_output_contract("[4,3,10]", contract)
    assert applied.text == "[4,3,10]"
    assert applied.compliant is True
    assert applied.violations == ()


def test_a_trailing_period_is_repaired_without_touching_the_brackets() -> None:
    contract = parse_raw_output_contract(MEASURED_TURN)
    applied = apply_raw_output_contract("[4,3,10].", contract)
    assert applied.text == "[4,3,10]"
    assert applied.compliant is True


@pytest.mark.parametrize(
    ("turn", "answer", "expected"),
    (
        # Fresh phrasings of the trailing scope, never seen by the parser's authoring session.
        ("What is 6*7? Output only the number, no trailing punctuation.", "42.", "42"),
        ("Name the capital of France. One word. No punctuation at the end.", "Paris.", "Paris"),
        (
            "Give the ISO date only. Do not add punctuation at the end.",
            "2026-08-15.",
            "2026-08-15",
        ),
    ),
)
def test_fresh_trailing_scope_phrasings_strip_only_the_tail(
    turn: str, answer: str, expected: str
) -> None:
    contract = parse_raw_output_contract(turn)
    assert contract is not None
    assert contract.no_trailing_punctuation is True
    assert contract.no_punctuation is False
    applied = apply_raw_output_contract(answer, contract)
    assert applied.text == expected
    assert applied.compliant is True


def test_a_blanket_ban_narrows_when_the_directive_demands_brackets() -> None:
    # "with brackets and commas" and "no punctuation" in one directive contradict; the explicit
    # structural ask wins and the ban applies only to the tail.
    contract = parse_raw_output_contract(
        "List B is [5,6]. Output exactly the list with brackets and commas, no spaces. "
        "No punctuation."
    )
    assert contract is not None
    assert contract.no_punctuation is False
    assert contract.no_trailing_punctuation is True
    applied = apply_raw_output_contract("[5,6]", contract)
    assert applied.text == "[5,6]"
    assert applied.compliant is True


def test_a_true_blanket_ban_still_convicts_and_strips_as_before() -> None:
    # Negative control pair: without a structural request, blanket behaviour is unchanged.
    contract = parse_raw_output_contract(
        "Just output the name of the country, backwards, in all uppercase letters. "
        "No markdown, no punctuation."
    )
    assert contract is not None
    assert contract.no_punctuation is True
    assert contract.no_trailing_punctuation is False
    boundary = apply_raw_output_contract("AINOSET.", contract)
    assert boundary.text == "AINOSET"
    assert boundary.compliant is True
    inner = apply_raw_output_contract("ALPHA,BETA", contract)
    assert inner.compliant is False
    assert "punctuation" in inner.violations


def test_the_flag_survives_the_metadata_dict_round_trip() -> None:
    # The live seam hands the contract through source_context as a dict; a flag the loader drops
    # is a flag the product never has.
    contract = parse_raw_output_contract(MEASURED_TURN)
    restored = raw_output_contract_from_metadata({"raw_output_contract": contract.to_dict()})
    assert restored is not None
    assert restored.no_trailing_punctuation is True
    assert restored.no_punctuation is False


def test_the_real_frontdoor_ships_the_computed_array_through_the_seal(
    tmp_path, monkeypatch
) -> None:
    """End to end: the same run_once path that failed live now ships "[4,3,10]" as the answer."""
    from unittest import mock

    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="seal-check", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("deterministic turn reached a model"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)

    result = agent.run_once(
        MEASURED_TURN,
        session_id_override="openclaw:trailingpunctseal",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["response"] == "[4,3,10]"
    model.assert_not_called()
