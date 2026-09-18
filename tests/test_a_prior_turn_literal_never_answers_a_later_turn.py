"""An output contract's literal belongs to the turn that stated it, in every mode.

Measured on the served surface, 2026-08-15::

    turn 2 (exact-literal contract): "Output exactly ERR_AWS_DENIED and nothing else." -> ERR_AWS_DENIED
    turn 6 (unrelated array puzzle):  "Array A starts with [1,2,3]. append 4 ..."       -> ERR_AWS_DENIED

A literal from a turn two back answered a completely different later turn. In another session a
math turn answered "ERR_NZMDFS" the same way.

Two root causes, both measured at HEAD before this fix:
  1. `_guard_literal_values("ERR_AWS_DENIED")` returned () -- the extractor recognised a hyphen+digit
     confirmation code and a multi-word all-caps value, but NOT a single underscore-joined error
     code, so the contaminating literal was never even seen.
  2. The contamination rejection ran only when `policy["mode"] == "ordinary_chat"`; the array puzzle
     was classified `research`, so the guard early-returned allowed and never checked.

The invariant: a prior turn's opaque literal must not appear as a later turn's answer, whatever the
later turn was classified. Two exemptions keep it honest -- a literal the CURRENT turn itself states
(its own contract), and an explicit scoped recall ("what did you say the code was?").
"""

from __future__ import annotations

import pytest

import core.ordinary_chat_response_guard as guard


def _hashes(*prior_user_turns: str, current: str = "") -> list[str]:
    return list(
        guard.prior_turn_literal_hashes(
            [{"role": "user", "content": t} for t in prior_user_turns],
            current_user_text=current,
        )
    )


def _check(rendered: str, *, mode: str, prior: list[str], current: str):
    return guard.inspect_ordinary_chat_output(
        rendered, {"mode": mode, "prior_turn_literal_hashes": prior}, current_user_text=current
    )


# ---------------------------------------------------------------------------------------------
# The extractor now sees a single opaque code, and only a code
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code", ("ERR_AWS_DENIED", "ERR_NZMDFS", "SEC_DENIED_0x9F", "ERR_SQL_DENIED", "ERR_NO_SWARM")
)
def test_a_single_opaque_error_code_is_extracted(code: str) -> None:
    assert code in guard._guard_literal_values(f"the answer is {code} and nothing else")


@pytest.mark.parametrize(
    "constant", ("API_KEY", "MAX_SIZE", "HTTP_OK", "NEW_YORK", "USER_ID", "README", "my_variable")
)
def test_an_ordinary_constant_is_not_treated_as_an_opaque_code(constant: str) -> None:
    """These legitimately appear in code answers; flagging them would reject real work."""

    assert constant not in guard._guard_literal_values(f"set {constant} to the value")


# ---------------------------------------------------------------------------------------------
# The contamination rejection runs in every mode
# ---------------------------------------------------------------------------------------------


def test_the_reported_cross_turn_leak_is_rejected() -> None:
    prior = _hashes("Output exactly ERR_AWS_DENIED and absolutely nothing else.")
    puzzle = "Array A starts with [1,2,3]. append 4. remove 2. reverse."

    assert _check("ERR_AWS_DENIED", mode="research", prior=prior, current=puzzle).allowed is False


@pytest.mark.parametrize("mode", ("research", "unknown", "chat_conversation", "summary_block"))
def test_a_stale_literal_is_rejected_whatever_the_mode(mode: str) -> None:
    prior = _hashes("reply with exactly ERR_NZMDFS and nothing else")

    assert _check("ERR_NZMDFS", mode=mode, prior=prior, current="what is 12 times 12?").allowed is False


# ---------------------------------------------------------------------------------------------
# Negative controls -- the fix must not eat legitimate output
# ---------------------------------------------------------------------------------------------


def test_a_legitimate_answer_is_allowed() -> None:
    prior = _hashes("Output exactly ERR_AWS_DENIED and nothing else.")

    assert _check("The reversed array is [40,3,4].", mode="research", prior=prior, current="Array A ...").allowed is True


def test_a_literal_the_current_turn_states_itself_is_allowed() -> None:
    """The turn's OWN exact-literal contract -- not contamination from a prior turn."""

    prior = _hashes("Output exactly ERR_AWS_DENIED and nothing else.")

    assert _check(
        "ERR_AWS_DENIED",
        mode="research",
        prior=prior,
        current="If you cannot, output exactly ERR_AWS_DENIED and nothing else.",
    ).allowed is True


def test_an_explicit_recall_still_returns_the_prior_literal() -> None:
    """The distinction that keeps this from breaking memory: the user ASKED for the earlier code."""

    prior = _hashes("Output exactly ERR_AWS_DENIED and nothing else.")

    assert _check(
        "ERR_AWS_DENIED", mode="ordinary_chat", prior=prior, current="what did you say the error code was?"
    ).allowed is True


def test_a_turn_with_no_prior_literals_is_untouched() -> None:
    assert _check("ERR_AWS_DENIED", mode="research", prior=[], current="anything").allowed is True


# ---------------------------------------------------------------------------------------------
# The hash extraction end to end
# ---------------------------------------------------------------------------------------------


def test_a_prior_exact_literal_turn_produces_a_hash() -> None:
    assert len(_hashes("Output exactly ERR_NO_SWARM and truly nothing else.")) == 1
    # ...and the CURRENT turn's own literal is excluded from the prior set.
    assert _hashes(
        "Output exactly ERR_NO_SWARM and nothing else.",
        current="output exactly ERR_NO_SWARM and nothing else",
    ) == []
