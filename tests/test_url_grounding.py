from __future__ import annotations

from core.agent_runtime.action_honesty_validator import enforce_url_grounding, response_describes_unfetched_url


def test_unfetched_url_with_review_intent_is_declined_not_hallucinated() -> None:
    # The exact failure from the screenshots: a pasted repo URL + "check/score" produced an
    # invented summary + made-up score with zero fetches. It must become an honest decline.
    result = {"response": "The repo is OpenClaw, a local-first web framework. The quality is decent. Score: 4/10."}
    out = enforce_url_grounding(
        result,
        user_input="https://github.com/Parad0x-Labs/openclaw-skills check this repo, tell me if any good, score 1/10",
        fetch_attempts=0,
    )
    assert "did not open it on this turn" in out["response"]
    assert "4/10" not in out["response"]
    assert out["url_grounding_validator"]["applied"] is True


def test_the_decline_does_not_deny_a_capability_the_runtime_has() -> None:
    """The refusal was right and its reason was false.

    The old wording was "I can't browse arbitrary web pages in this build". `web.fetch` works —
    driven directly it returns the page — so the message denied a capability the product has and
    sent the user off to paste files by hand. The decline stays; the false claim goes.
    """
    from core.agent_runtime.action_honesty_validator import _UNFETCHED_URL_RESPONSE

    lowered = _UNFETCHED_URL_RESPONSE.lower()
    assert "can't browse" not in lowered
    assert "cannot browse" not in lowered
    assert "in this build" not in lowered


def test_the_guard_does_not_judge_its_own_decline_a_fabrication() -> None:
    """The sentinel that stops the guard re-firing must live in the message it is checking for.

    They were two independent literals, so rewording the message silently disarmed the check.
    """
    from core.agent_runtime.action_honesty_validator import _UNFETCHED_URL_RESPONSE

    assert not response_describes_unfetched_url(
        user_input="https://github.com/x/y check this repo, is it any good",
        response=_UNFETCHED_URL_RESPONSE,
        fetch_attempts=0,
    )


def test_fetched_url_answer_is_kept() -> None:
    # If a fetch actually happened this turn, the answer is grounded -- leave it.
    result = {"response": "The repo has a README describing skills for OpenClaw and 3 open issues."}
    out = enforce_url_grounding(result, user_input="https://github.com/x/y check this repo", fetch_attempts=2)
    assert out["response"].startswith("The repo has a README")


def test_url_without_review_intent_is_untouched() -> None:
    result = {"response": "Sure -- to center a div, use flexbox with justify-content and align-items."}
    out = enforce_url_grounding(
        result, user_input="my project lives at https://github.com/x/y anyway how do I center a div", fetch_attempts=0
    )
    assert out["response"].startswith("Sure")


def test_no_url_is_untouched() -> None:
    result = {"response": "Yo. What needs fixing?"}
    out = enforce_url_grounding(result, user_input="my brains", fetch_attempts=0)
    assert out["response"] == "Yo. What needs fixing?"


def test_deterministic_or_grounded_reply_is_never_replaced() -> None:
    result = {"response": "grounded canned answer", "deterministic": True}
    out = enforce_url_grounding(result, user_input="https://x.dev score this repo", fetch_attempts=0)
    assert out["response"] == "grounded canned answer"
    assert not response_describes_unfetched_url(user_input="no url here, review it", response="x", fetch_attempts=0)
