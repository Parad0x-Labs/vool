"""The 2026-08-19 core-authority repairs, one invariant per family.

1. A multi-clause CURRENCY turn answers EVERY conversion clause. Measured at b7857c6c:
   "1000 EUR to USD. Also 500 GBP to JPY." passed the whole-turn coverage gate, then
   `_currency_reply` over the fused text answered only the first frame `fx_conversion_intent`
   returned; the second conversion was silently dropped.

2. A drive letter is a single letter NAMING a drive; "i drive" (pronoun + verb) and "a drive"
   (article + common noun) are not. `\b[a-z] drive\b` matched both, so "can I drive from Rome to
   Paris in one day?" was claimed as a this-machine disk question and the fabrication backstop
   would have refused the real answer. The closed-class fix: the only one-letter English words
   are "a" and "I".

3. A live-data turn's finality is its ATTEMPT verdict, never its prose. The attempt store
   finalized PARTIAL_SUCCESS while the turn trace recorded FULFILLED because the rendered answer
   was non-empty. The lane now carries the attempt lifecycle onto the result as an explicit
   `fulfillment_outcome`, which `terminal_fulfillment_outcome` reads before any prose fallback.
"""
from __future__ import annotations

import tempfile

import pytest

from core.agent_runtime.answer_coverage import FAMILY_CURRENCY, coverage_for
from core.execution.constants import local_fact_capability_required
from core.runtime_task_outcome import (
    fulfillment_outcome_from_attempt_lifecycle,
    terminal_fulfillment_outcome,
)

GENERAL_CHAT_BINDING = {"workspace_binding": "default", "project_id": ""}


@pytest.fixture(scope="module")
def real_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _drive_front_door(agent, text: str, *, session_id: str, workspace: str):
    """The real front door, offline: nothing is stubbed; retrieval is vetoed so the currency
    lane's honest no-rate branch is what answers."""
    context = {
        "workspace": workspace,
        "workspace_root": workspace,
        "session_id": session_id,
        "operating_mode": "auto",
        "surface": "api",
        "allow_remote_fetch": False,
        **GENERAL_CHAT_BINDING,
    }
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=text,
        effective_input=text,
        normalized_input=text.lower(),
        source_surface="api",
        session_id=session_id,
        source_context=context,
        persona=None,
        interpreted=None,
    )
    return (outcome or {}).get("result"), context


# --- family 1: currency multi-slice ----------------------------------------------------------

# User-supplied rates keep the lane deterministic offline: each clause computes, so the answer
# carries verifiable content per clause.
MULTI_SLICE_CASES = [
    ("100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5.", ("110 USD", "95,250 JPY")),
    ("Convert 50 USD to EUR at 0.92. And 75 GBP to USD at 1.27.", ("46 EUR", "95.25 USD")),
    ("100 EUR to USD at 1.10; 250 GBP to JPY at 190.0", ("110 USD", "47,500 JPY")),
    ("how much is 100 EUR in USD at 1.10? and 500 CHF in JPY at 170?", ("110 USD", "85,000 JPY")),
    ("100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5. And 10 CHF to EUR at 0.95.",
     ("110 USD", "95,250 JPY", "9.50 EUR")),
]

SINGLE_CLAUSE_CONTROLS = [
    ("1000 TRY to USD?", "currency"),
    ("what is 500 GBP in EUR", "currency"),
    ("1000 TRY to USD at 33.4", "33,400 USD"),
]


@pytest.mark.parametrize("text, expected_fragments", MULTI_SLICE_CASES)
def test_every_currency_clause_is_answered(real_agent, text: str, expected_fragments: tuple[str, ...]) -> None:
    with tempfile.TemporaryDirectory() as workspace:
        result, _ctx = _drive_front_door(real_agent, text, session_id="fx-multi", workspace=workspace)
    assert result is not None, f"the currency lane lost the turn entirely: {text}"
    response = str(result.get("response") or "")
    for fragment in expected_fragments:
        assert fragment in response, f"{fragment!r} dropped from the answer to {text!r}"


@pytest.mark.parametrize("text, fragment", SINGLE_CLAUSE_CONTROLS)
def test_a_single_currency_clause_still_takes_the_whole_turn(real_agent, text: str, fragment: str) -> None:
    with tempfile.TemporaryDirectory() as workspace:
        result, _ctx = _drive_front_door(real_agent, text, session_id="fx-single", workspace=workspace)
    assert result is not None
    assert fragment in (str(result.get("response") or "") + str(result.get("route_reason") or ""))


def test_sabotage_fused_text_answers_only_the_first_frame() -> None:
    """The pre-repair behavior: one reply over the fused text drops the second conversion."""
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fused = _currency_reply(
        "1000 EUR to USD. Also 500 GBP to JPY.", session_id="sabotage", source_context={}
    )
    assert fused is not None
    assert "JPY" not in str(fused["response"]), (
        "the fused-text path no longer drops the second frame; sabotage moot"
    )
    text = "1000 EUR to USD. Also 500 GBP to JPY."
    coverage = coverage_for(text, FAMILY_CURRENCY)
    assert coverage.covers_whole_turn and len(coverage.consumed) == 2


# --- family 2: a drive letter is not the pronoun "i" -----------------------------------------

NOT_MACHINE_CLAIMS = [
    "can I drive from Rome to Paris in one day?",
    "I drive?",
    "I drive a lorry for work",
    "how do I drive in the snow?",
    "is it safe to drive after one beer?",
    "can I drive there and back before dark?",
]

STILL_MACHINE_CLAIMS = [
    "is my C drive full?",
    "how much free space is left on the D drive?",
    "how much free disk space do I have?",
    "check the E drive please",
]


@pytest.mark.parametrize("text", NOT_MACHINE_CLAIMS)
def test_the_pronoun_i_drive_is_not_a_drive_letter(text: str) -> None:
    assert local_fact_capability_required(text) is None


@pytest.mark.parametrize("text", STILL_MACHINE_CLAIMS)
def test_a_real_drive_letter_still_claims(text: str) -> None:
    assert local_fact_capability_required(text) is not None


def test_sabotage_the_bare_letter_class_reclaims_the_verb() -> None:
    import re

    import core.execution.constants as constants

    real = constants._LOCAL_FACT_THIS_MACHINE_RE
    constants._LOCAL_FACT_THIS_MACHINE_RE = re.compile(
        real.pattern.replace(r"(?!\ba\s)(?!\bi\s)\b[a-z]\s+drive\b", r"\b[a-z] drive\b"),
        re.IGNORECASE,
    )
    try:
        assert local_fact_capability_required("can I drive from Rome to Paris in one day?") == "your drives"
    finally:
        constants._LOCAL_FACT_THIS_MACHINE_RE = real
    assert local_fact_capability_required("can I drive from Rome to Paris in one day?") is None


# --- family 3: prose never upgrades finality --------------------------------------------------

def test_the_attempt_verdict_maps_into_fulfillment() -> None:
    assert fulfillment_outcome_from_attempt_lifecycle("SUCCEEDED") == {
        "fulfillment_status": "fulfilled"
    }
    partial = fulfillment_outcome_from_attempt_lifecycle("PARTIAL_SUCCESS")
    assert partial is not None and partial["fulfillment_status"] == "partially_fulfilled"
    waiting = fulfillment_outcome_from_attempt_lifecycle("WAITING_APPROVAL")
    assert waiting is not None and waiting["fulfillment_status"] == "blocked"
    failed = fulfillment_outcome_from_attempt_lifecycle("FAILED_TOOL", terminal_reason="no tool")
    assert failed is not None and failed["fulfillment_status"] == "failed"
    assert fulfillment_outcome_from_attempt_lifecycle("") is None


def test_an_explicit_outcome_outranks_nonempty_prose() -> None:
    """The exact divergence: non-empty answer text over a PARTIAL attempt must not read FULFILLED."""
    result = {
        "response": "Ripple: $2.91 (source: CoinGecko)\n\nUsde — not a recognized market entity",
        "fulfillment_outcome": fulfillment_outcome_from_attempt_lifecycle("PARTIAL_SUCCESS"),
    }
    outcome = terminal_fulfillment_outcome(result)
    assert outcome.fulfillment_status.value == "partially_fulfilled"


def test_sabotage_without_the_explicit_outcome_prose_wins() -> None:
    result = {"response": "Ripple: $2.91 (source: CoinGecko)\n\nUsde — not a recognized market entity"}
    outcome = terminal_fulfillment_outcome(result)
    assert outcome.fulfillment_status.value == "fulfilled", (
        "the prose default did not upgrade; the divergence this repair closes is gone"
    )


# --- family 4: one model, one size for one question ------------------------------------------
#
# Measured 2026-08-19 (Fable E2): a pinned unregistered MoE name ("gemma-4-26b-a4b") was read as
# 26B by the autopilot's heavy flag (largest name token) and as 4B by the router's heavy-lane
# admission (MoE-ACTIVE count), so the pinned model was flagged heavy and then refused as
# non-heavy -- before any adapter ran. Residency/eligibility is a TOTAL-parameter question;
# inference-cost lanes are an ACTIVE-parameter question. Each now has one owner.

def test_total_parameters_are_metadata_first_and_name_fallback() -> None:
    from core.local_model_bundles import model_total_parameter_billions

    assert model_total_parameter_billions("gemma-4-26b-a4b") == 26.0
    assert model_total_parameter_billions("nemotron-3-ultra-550b-a55b:free") == 550.0
    # An MoE PRODUCT name is the product, not one expert.
    assert model_total_parameter_billions("mixtral-8x22b") == 176.0
    assert model_total_parameter_billions("qwen3:32b") == 32.0


def test_the_heavy_flag_and_the_router_gate_now_agree() -> None:
    from core.local_inference_autopilot import _explicit_heavy_requested
    from core.local_model_bundles import model_total_parameter_billions

    for name in ("gemma-4-26b-a4b", "nemotron-3-ultra-550b-a55b:free", "qwen3:32b", "llama-3.1-8b"):
        flag = _explicit_heavy_requested(user_text="", source_context={"requested_model": name})
        assert flag == (model_total_parameter_billions(name) >= 24.0), name


def test_the_active_count_still_owns_inference_cost_lanes() -> None:
    from core.local_model_bundles import model_parameter_billions

    # The tiny lane's question is what a call COSTS, which for an MoE is the active count.
    assert model_parameter_billions("gemma-4-26b-a4b") == 4.0


def test_sabotage_active_count_in_the_heavy_gate_reopens_the_refusal() -> None:
    """Reverting the heavy gate to the inference-cost number refuses the pinned model again."""
    from core.local_model_bundles import model_parameter_billions

    assert model_parameter_billions("gemma-4-26b-a4b") < 24.0, (
        "the active count now clears the heavy floor; the sabotage is moot"
    )
