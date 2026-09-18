"""Restart-first-turn P0 (2026-09-02): demand-ownership pins.

The served-reality bench and the 10-cycle RED run (ops/restart_repro/evidence/red-base)
proved the first model turn after restart failing through two faces with ONE root:
runtime-authored state mutated the turn's DEMAND ownership.

* Face A -- ``explicit_heavy_lane_unavailable`` before the adapter: numbers inside the
  "Grounding observations ..." scaffolding spliced into the interpreted prompt parsed as
  model-size markers, so a benign turn "asked" for a heavy model nobody requested.
* Face B -- a false current-information lifecycle: bare "confirm" (a conversational verb) in
  the roamer's verify/research vocabularies escalated a chat turn to research, and the
  escalation widened the canonical requirement to ``current_information_required=True``.

These pins hold the repaired invariants at their owning predicates: a benign turn cannot
become explicit-heavy, cannot open a current-information lifecycle, restored history cannot
mutate demand ownership, and GENUINE heavy/current demands keep their routing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.curiosity_roamer import _adaptive_research_decision
from core.execution_requirements import (
    current_requirement_record,
    requirements_for,
)
from core.local_inference_autopilot import build_local_inference_autopilot_plan

BENIGN_CONFIRM_TURNS = (
    "And now confirm you are back with: RESTART-SECOND-3302.",
    "And confirm once more that you still remember the code RESTART-THIRD-4403.",
    "Remember this code for our next exchange: RESTART-FIRST-2201.",
)

#: The exact scaffolding shape `observation_prompt` composes (channel, sources, numbers).
#: The numbers are chosen to parse as >=24B parameter markers if anyone feeds this text to
#: a size predicate -- that is Face A.
COMPOSED_SCAFFOLD = (
    "Grounding observations for this turn. Use them as evidence, not as a template:"
    '{"channel": "adaptive_research", "source_count": 4, "note": "artifacts 89.0b and 405b"}'
)


def _chat_classification() -> dict[str, object]:
    return {"task_class": "chat_conversation"}


def _trusted_surface() -> dict[str, object]:
    return {"surface": "channel", "platform": "openclaw"}


def _decision(text: str) -> dict[str, object]:
    return _adaptive_research_decision(
        user_input=text,
        classification=_chat_classification(),
        interpretation=SimpleNamespace(topic_hints=[]),
        source_context=_trusted_surface(),
    )


# ---------------------------------------------------------------------------
# Pin 1 -- a benign conversational turn cannot become research (Face B's trigger)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", BENIGN_CONFIRM_TURNS)
def test_benign_confirm_turn_does_not_escalate_to_research(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Neutralize the deployment policy gate (off under the suite conftest) so the MARKER
    # VOCABULARY is what decides; otherwise this pin would pass vacuously at base.
    monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda: True)
    decision = _decision(text)
    assert decision["enabled"] is False, decision
    assert decision["escalated_from_chat"] is False, decision


def test_benign_confirm_turn_never_opens_a_current_information_lifecycle() -> None:
    """The whole Face B chain in two steps: no escalation, no lane-sourced widening."""

    for text in BENIGN_CONFIRM_TURNS:
        requirements = requirements_for(text)
        assert requirements.current_information_required is False, text
        assert "stable_knowledge" in list(requirements.reason_codes or ()), text


# ---------------------------------------------------------------------------
# Pin 2 -- genuine research/current demands keep their routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "Please verify whether the release notes for Node 24 mention the fetch API",
        "Can you fact check that rumor about the acquisition?",
        "Confirm that the pricing page still lists the free tier",
    ),
)
def test_genuine_verification_still_escalates(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # The vocabulary pin is about RECOGNITION, not deployment policy: the suite's conftest
    # pins allow_web_fallback off, which (correctly) declines research turns for another
    # reason entirely. Neutralize that gate so the marker vocabulary is what's under test.
    monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda: True)
    decision = _decision(text)
    assert decision["enabled"] is True, decision


def test_genuine_current_information_demand_still_required() -> None:
    requirements = requirements_for("What is the latest news on the merger today?")
    assert requirements.current_information_required is True, requirements


def test_lane_escalation_still_widens_a_genuine_research_turn() -> None:
    """A lane's sanctioned escalation keeps working when the engagement is real."""

    source_context: dict[str, object] = {}
    from core.execution_requirements import escalate_current_requirement

    requirements_for("verify whether the rumor about the acquisition is accurate", source_context=source_context)
    widened = escalate_current_requirement(
        source_context,
        text="verify whether the rumor about the acquisition is accurate",
        source="adaptive_research",
        reason_code="lane:adaptive_research",
    )
    assert widened is True
    record = current_requirement_record(source_context)
    assert record is not None and record.requirements.current_information_required is True


# ---------------------------------------------------------------------------
# Pin 3 -- composed scaffolding cannot make the turn explicit-heavy (Face A)
# ---------------------------------------------------------------------------


def _plan(user_text: str, demand_text: str | None) -> object:
    return build_local_inference_autopilot_plan(
        user_text=user_text,
        user_demand_text=demand_text,
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(),
    )


def test_composed_scaffolding_never_reads_as_a_heavy_demand() -> None:
    benign = BENIGN_CONFIRM_TURNS[0]
    plan = _plan(benign + " " + COMPOSED_SCAFFOLD, benign)
    assert plan.explicit_heavy is False, "numbers in the runtime's own scaffolding are not a user demand"


def test_legacy_caller_without_demand_text_is_unchanged() -> None:
    """A caller that never composed anything keeps reading its own text as the demand."""

    plan = _plan(BENIGN_CONFIRM_TURNS[0], None)
    assert plan.explicit_heavy is False


# ---------------------------------------------------------------------------
# Pin 3b -- the demand-text WIRE: composed prompts carry the pure text, and the
# router supplies it to the plan builder. Severing either end re-opens Face A
# silently (the sequential cycles stay green once the roamer is fixed, because
# benign turns no longer escalate -- only escalated turns carry scaffolding).
# ---------------------------------------------------------------------------


def test_composed_prompt_interpretation_carries_the_pure_demand_text() -> None:
    from core.human_input_adapter import adapt_user_input

    benign = BENIGN_CONFIRM_TURNS[0]
    interpretation = adapt_user_input(
        benign + " " + COMPOSED_SCAFFOLD,
        session_id="demand-ownership-pin",
        persist=False,
        user_demand_text=benign,
    )
    assert interpretation.user_demand_text == benign
    assert "Grounding observations" in interpretation.normalized_text


def test_the_demand_text_wire_is_intact_at_both_ends() -> None:
    """Wire-integrity pins: the composed site passes the pure input, the router
    forwards the interpretation's demand text. A silent revert of either end is
    exactly how Face A comes back without any sequential test noticing."""

    import inspect

    import core.agent_runtime.chat_surface as chat_surface_module
    import core.memory_first_router as router_module

    surface_source = inspect.getsource(chat_surface_module)
    router_source = inspect.getsource(router_module)
    assert "user_demand_text=user_input" in surface_source, (
        "chat_surface must pass the PURE user_input as the composed prompt's demand text"
    )
    assert 'getattr(interpretation, "user_demand_text", "")' in router_source, (
        "memory_first_router must supply the interpretation's demand text to the plan builder"
    )


def test_genuine_heavy_demand_still_selects_heavy_routing() -> None:
    benign = BENIGN_CONFIRM_TURNS[0]
    plan = _plan(benign + " " + COMPOSED_SCAFFOLD, "use the 32b model for this one")
    assert plan.explicit_heavy is True


# ---------------------------------------------------------------------------
# Pin 4 -- restored/composed state cannot mutate the turn's demand ownership
# ---------------------------------------------------------------------------


def test_composed_prompt_does_not_mutate_the_external_turns_requirement_record() -> None:
    """A sub-reader computing requirements for the COMPOSED prompt must not touch the
    turn's frozen record: first decision wins, per-text, and the external text's record
    keeps the external text."""

    source_context: dict[str, object] = {}
    external = BENIGN_CONFIRM_TURNS[0]
    requirements_for(external, source_context=source_context)
    requirements_for(external + " " + COMPOSED_SCAFFOLD, source_context=source_context)
    record = current_requirement_record(source_context)
    assert record is not None
    assert record.request_text == " ".join(external.split())
    assert record.requirements.current_information_required is False


def test_demand_decision_is_a_pure_function_of_the_text_not_history() -> None:
    """Restored history may inform recall; it must not inform demand ownership. The
    authority takes no history input at all -- pinned so a future signature change that
    adds one has to come through here and say why."""

    import inspect

    signature = inspect.signature(requirements_for)
    assert set(signature.parameters) == {"user_input", "task_class", "source_context"}, signature
    first = requirements_for(BENIGN_CONFIRM_TURNS[1])
    second = requirements_for(BENIGN_CONFIRM_TURNS[1])
    assert first is second or first == second
