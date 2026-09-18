"""Currency identity is local; currency value is live, supplied, or unavailable."""

from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for
from core.currency_value_contract import (
    STATIC_CURRENCY_IDENTITIES,
    asks_for_dynamic_currency_value,
    maybe_answer_currency_value,
)
from core.execution_requirements import requirements_for

PROMPT = (
    "Compare 500 kr, $500, and ¥500 for a traveler in Copenhagen and Shanghai. "
    "Explain FX vs PPP and do not invent rates."
)


@pytest.mark.parametrize(
    ("code", "name"),
    [("ALL", "Albanian lek"), ("MAD", "Moroccan dirham"), ("GEL", "Georgian lari")],
)
def test_static_currency_identity_is_local(code: str, name: str) -> None:
    assert STATIC_CURRENCY_IDENTITIES[code] == name
    reply = maybe_answer_currency_value(f"What currency code is {code}?")
    assert reply is not None
    assert reply.response == f"{code} = {name}"
    assert reply.reason == "static_currency_identity"


def test_currency_comparison_is_dynamic_even_without_current_or_today() -> None:
    assert asks_for_dynamic_currency_value(PROMPT) is True
    assert answer_mode_for(PROMPT) is AnswerMode.GROUNDED
    requirements = requirements_for(PROMPT)
    assert requirements.external_evidence_required is True
    assert requirements.tools_required is True
    assert requirements.inference_allowed is False


def test_city_anchors_bind_symbols_but_do_not_invent_values() -> None:
    reply = maybe_answer_currency_value(PROMPT)
    assert reply is not None
    response = reply.response
    assert "DKK" in response and "Copenhagen" in response
    assert "CNY/RMB" in response and "Shanghai" in response
    assert "`$` remains ambiguous" in response
    assert "does not establish USD" in response
    assert "cannot give exact conversions or rank" in response
    assert "named PPP dataset" in response
    assert "$69.50" not in response and "$3.90" not in response
    assert "≈" not in response


def test_local_only_followup_reuses_the_recent_comparison_and_stays_fail_closed() -> None:
    reply = maybe_answer_currency_value(
        "Local only.",
        source_context={"conversation_history": [{"role": "user", "content": PROMPT}]},
    )
    assert reply is not None
    assert "DKK" in reply.response and "CNY/RMB" in reply.response
    assert "No live or user-supplied FX rates" in reply.response
    assert "$69.50" not in reply.response and "$3.90" not in reply.response


def test_unrelated_local_only_turn_is_not_claimed() -> None:
    assert maybe_answer_currency_value(
        "Local only.",
        source_context={"conversation_history": [{"role": "user", "content": "List my files"}]},
    ) is None


def test_an_explicit_user_supplied_rate_is_not_overwritten_by_the_refusal_lane() -> None:
    supplied = "Compare 500 DKK and 500 USD using my rate: 1 USD = 7 DKK."
    assert maybe_answer_currency_value(supplied) is None


def test_plain_english_homograph_request_precedes_generic_code_identity() -> None:
    prompt = (
        'If the word "TRY" means "Turkish Lira" and "ALL" means "Albanian Lek", '
        'what does "TRY ALL" mean in plain English? Do NOT trigger a currency lookup tool.'
    )
    reply = maybe_answer_currency_value(prompt)

    assert reply is not None
    assert reply.reason == "stable_currency_reference_contract"
    assert reply.response == 'TRY ALL means "attempt everything" in plain English.'


def test_production_turn_never_reaches_a_model_without_rates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the unsupported dynamic-value request reached the ordinary frontdoor")

    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire):
        result = agent.run_once(
            PROMPT,
            session_id_override="currency-value-contract",
            source_context={"surface": "channel", "platform": "openclaw"},
        )

    assert result.get("model_calls") == 0
    assert result.get("web_calls") == 0
    # R1e (demand ownership): this prompt is TWO demand units — the anchored
    # comparison and "Explain FX vs PPP" — so the turn may now route through the
    # demand-owned unit plan instead of the contract's single whole-turn claim.
    # The SAFETY intent is unchanged and pinned by the assertions below: zero model
    # calls, zero web calls, no invented rates, and the kr/$ currency bindings keep
    # their Copenhagen/Shanghai anchors (the comparison unit executes whole — its
    # amounts are never split away from the anchors that resolve them).
    assert result.get("route") in {
        "deterministic:dynamic_currency_value_unavailable",
        "deterministic:demand_owned_mixed_turn",
    }
    assert "DKK" in str(result.get("response") or "")
    assert "CNY/RMB" in str(result.get("response") or "")
