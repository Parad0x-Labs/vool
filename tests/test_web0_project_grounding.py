from __future__ import annotations

import pytest

from core.web0_project_grounding import (
    NULL_REGISTRAR_V2_PROGRAM,
    web0_null_project_response,
)


def test_general_null_buy_question_is_grounded() -> None:
    result = web0_null_project_response("can we buy a .null address?")
    assert result is not None
    text = result["response"]
    assert "null_registrar v2" in text
    assert "vool resolve <name>.null" in text
    # Cost must be stated honestly: it's NOT free (registry is at go-live pricing), costs SOL,
    # points to the live-quoting register command, and notes premium/auction names.
    assert "free" not in text.lower()
    assert "sol" in text.lower()
    assert "vool register <name>.null" in text
    assert "auction" in text.lower()


def test_named_registration_request_refuses_auto_spend_and_points_to_resolve() -> None:
    result = web0_null_project_response(
        "can you help me to buy a .null name? I want for example test123.null"
    )
    assert result is not None
    text = result["response"]
    assert "`test123.null`" in text
    assert "vool resolve test123.null" in text
    assert "will not sign, spend, or submit" in text


def test_fee_followup_without_null_token_is_answered_not_dead_ended() -> None:
    # This is the exact turn that used to dead-end with "I couldn't map that cleanly to a
    # real action": a registration follow-up whose .null token was in a previous message.
    result = web0_null_project_response(
        "ok lets grab it, tell me where to send the registration fee and you will do it for me"
    )
    assert result is not None
    assert result["intent"] == "web0_null_registration_fee_followup"
    text = result["response"].lower()
    # Correct the user's mental model: it isn't free, it costs SOL, and VOOL can register it
    # via the gated `vool register` command (previews cost, wallet prompt) — never auto.
    assert "isn't free" in text or "not free" in text
    assert "sol" in text
    assert "vool register" in text


def test_registration_word_alone_does_not_over_fire() -> None:
    # A plain "register" with no fee/exec phrasing must not trigger a .null answer.
    assert web0_null_project_response("how do I register for the newsletter") is None
    assert web0_null_project_response("hi, what's up?") is None


def test_program_id_is_surfaced_for_named_requests() -> None:
    result = web0_null_project_response("register mycoolname.null for me please")
    assert result is not None
    assert NULL_REGISTRAR_V2_PROGRAM in result["response"]


@pytest.mark.parametrize("prompt", [
    "Review this pasted Python snippet only. Do not use tools, files, shell commands, or browsing.\n\n"
    "VOOL charges a 0.1% service fee and must preserve fractions of a micro-USDC between payments. "
    "Find the bug, give corrected Python using integer arithmetic, and show two short examples, "
    "including repeated tiny payments. Keep the answer under 250 words.\n\n"
    "def accrue_fee(payment_micro, carry=0):\n"
    "    fee_micro = payment_micro * 10 // 10_000\n    return fee_micro, carry",
    "Explain how reclaimed storage reduces our costs.",
    "Review /tmp/register/fee.py and explain its arithmetic.",
    "Review register_fee_counter and show how much it increments.",
    "How do I register for the coffee newsletter?",
])
def test_registration_followup_does_not_claim_unrelated_prose_or_paths(prompt: str) -> None:
    assert web0_null_project_response(prompt) is None


@pytest.mark.parametrize("prompt", [
    "Where do I send the registration fee?",
    "Please reserve it and tell me the price.",
    "Claim it, you do it for me.",
])
def test_explicit_registration_followup_still_reaches_grounding(prompt: str) -> None:
    result = web0_null_project_response(prompt)
    assert result is not None
    assert result["intent"] == "web0_null_registration_fee_followup"
