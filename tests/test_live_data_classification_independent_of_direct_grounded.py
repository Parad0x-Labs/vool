"""LIVE_DATA fires regardless of what the generic DIRECT/GROUNDED classifier decides.

Found live, through the isolated daemon, testing fresh phrasings never used while building the
LIVE_DATA path: "Weather only for Berlin and Copenhagen." got a bare, undelivered model promise
("I'll check the weather... let me look that up") and "Market data only for Bitcoin and gold." got
the model HALLUCINATING fake prices ("$35,200" for Bitcoin; the real price at the time was ~$64,000)
while claiming "real-time data from major exchanges" -- exactly the failure class this whole
project's LIVE_DATA path exists to prevent.

Root cause: `_live_data_classification` was nested inside `if mode is AnswerMode.GROUNDED:`, and
`answer_mode_for()` (a separate, pre-existing classifier, not touched here) returned DIRECT for
both phrasings (no temporal marker like "current"/"now"), so LIVE_DATA never got a chance to run.
Fixed by checking `_live_data_classification` unconditionally, before the DIRECT/GROUNDED branch --
the same "escalate, never de-escalate" principle already applied to `forbids_inference` two lines
above it in the same function.
"""

from __future__ import annotations

from unittest import mock

from core.execution_requirements import requirements_for


def test_weather_only_phrasing_is_live_data_not_direct() -> None:
    requirements = requirements_for("Weather only for Berlin and Copenhagen.")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.tools_required is True
    assert requirements.multipart is True


def test_market_data_only_phrasing_is_live_data_not_direct() -> None:
    requirements = requirements_for("Market data only for Bitcoin and gold.")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.tools_required is True


def test_unrelated_direct_requests_are_still_direct() -> None:
    """The fix must not over-trigger: LIVE_DATA's own recognizers still gate it narrowly."""
    for text in ("what is the capital of France", "what is your current mood", "explain compression versus encryption"):
        requirements = requirements_for(text)
        assert requirements.answer_mode == "DIRECT", text
        assert requirements.tools_required is False, text


def test_sabotage_renesting_live_data_inside_grounded_only_reproduces_the_incident() -> None:
    """Proves the restructuring is load-bearing: patching answer_mode_for to always return DIRECT
    (simulating the original bug's effective behavior for these phrasings) must not silently
    disable LIVE_DATA -- if it did, this test would need to catch answer_mode reverting to DIRECT."""
    from core.agent_runtime.grounded_mode import AnswerMode

    with mock.patch("core.agent_runtime.grounded_mode.answer_mode_for", return_value=AnswerMode.DIRECT):
        requirements = requirements_for("Market data only for Bitcoin and gold.")
    # Even with the underlying classifier forced to DIRECT (reproducing the exact condition that
    # caused the live incident), LIVE_DATA must still fire -- proving the check no longer depends
    # on answer_mode_for's own DIRECT/GROUNDED decision.
    assert requirements.answer_mode == "LIVE_DATA"
