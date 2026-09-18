"""A model that ran out of wall clock is not a model that was rate limited.

Measured live 2026-08-03, one `review lbx_pdf.py and tell me what could be improved` turn against
a 24 GB Mac:

    12:07:27  ollama-local:qwen3:14b  Read timed out. (read timeout=180.0)
    12:10:27  ollama-local:qwen3:14b  Read timed out. (read timeout=180.0)
    12:13:55  ollama-local:qwen3:14b  Read timed out. (read timeout=180.0)
    12:13:55  turn completed, 568.6s total

540 of those 568 seconds went to one manifest that could not answer, three times. The audit still
produced three cited findings, so nothing was lost but nine minutes of the operator's day.

`_RESOURCE_GATE_RE` already breaks out immediately on `model_load_gated_low_memory`, with a comment
saying exactly why: it "is a fact about the box, not a transient provider hiccup, so re-asking the
SAME manifest cannot succeed". A read timeout is the same kind of fact — this model cannot answer
this prompt on this machine inside the time allowed — but it fell through to the branch below,
whose comment reasons about rate limits. A rate limit clears; a 180-second local generation does
not.

The retry that branch exists for is real and stays: an empty reply and a malformed artifact still
get their second attempt with the provider-native JSON contract dropped.
"""
from __future__ import annotations

import pytest

from tests import test_an_audit_obeys_the_permission_it_was_given as harness

TIMEOUT_ERROR = (
    "ERROR:ollama-local:qwen3:14b: HTTPConnectionPool(host='127.0.0.1', port=11434): "
    "Read timed out. (read timeout=180.0)"
)
RATE_LIMIT_ERROR = "ERROR:openrouter-byok: 429 rate limit exceeded, retry after 20s"
GATE_ERROR = (
    "ERROR:ollama-local:qwen3:14b: model_load_gated_low_memory — loading qwen3:14b (~9.3 GB) "
    "does not fit the ~3.6 GB of free RAM even after reclaiming"
)


def _nomination_attempts(router) -> int:
    """How many times the SAME manifest was asked to nominate."""

    return sum(
        1
        for request in router.requests
        if str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
        == "nominate"
    )


# --------------------------------------------------------------------------------------
# The measured failure.
# --------------------------------------------------------------------------------------


def test_a_read_timeout_abandons_the_manifest_instead_of_re_asking() -> None:
    """One attempt, not two. The second costs another full timeout and cannot succeed."""

    _decision, router, _tools = harness._drive(
        [TIMEOUT_ERROR, harness.TRUNCATION_FINDING], session_id="audit-timeout"
    )

    assert _nomination_attempts(router) == 1, (
        "the timed-out manifest was asked a second time, buying another 180s and nothing else"
    )


def test_the_report_says_where_the_search_ended() -> None:
    """Abandoning the manifest must not abandon the operator — the reason is still named."""

    decision, _router, _tools = harness._drive(
        [TIMEOUT_ERROR, harness.TRUNCATION_FINDING], session_id="audit-timeout-reason"
    )
    report = harness._report(decision).lower()

    assert "timed out" in report or "timeout" in report


# --------------------------------------------------------------------------------------
# The controls. Narrowing this too far would delete a retry that earns its keep.
# --------------------------------------------------------------------------------------


def test_a_rate_limit_still_gets_its_second_attempt() -> None:
    """The branch's original justification, and it is sound: a rate limit clears on its own.

    Without this control, "abandon on any provider error" would pass every test above while
    throwing away a retry that routinely succeeds on the free cloud lane — which is where most
    real testing runs.
    """

    _decision, router, _tools = harness._drive(
        [RATE_LIMIT_ERROR, harness.TRUNCATION_FINDING], session_id="audit-ratelimit"
    )

    assert _nomination_attempts(router) == 2, (
        "a transient rate limit must still be retried; only wall-clock exhaustion is terminal"
    )


def test_the_memory_gate_still_abandons_immediately() -> None:
    """The behaviour this fix was modelled on, pinned so the two stay consistent."""

    _decision, router, _tools = harness._drive(
        [GATE_ERROR, harness.TRUNCATION_FINDING], session_id="audit-gate"
    )

    assert _nomination_attempts(router) == 1


def test_an_empty_reply_still_gets_its_second_attempt() -> None:
    """A model that answered badly is not a model that could not answer."""

    _decision, router, _tools = harness._drive(
        ["", harness.TRUNCATION_FINDING], session_id="audit-empty"
    )

    assert _nomination_attempts(router) == 2


# --------------------------------------------------------------------------------------
# The predicate, at its edges.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "HTTPConnectionPool(host='127.0.0.1', port=11434): Read timed out. (read timeout=180.0)",
        "ReadTimeout: server did not respond",
        "the request timed out after 60s",
        "urllib3 connect_timeout=5 exceeded",
    ],
)
def test_wall_clock_exhaustion_is_recognised(message: str) -> None:
    from core.agent_runtime.stepped_audit import _CALL_TIMEOUT_RE

    assert _CALL_TIMEOUT_RE.search(message)


@pytest.mark.parametrize(
    "message",
    [
        "429 rate limit exceeded, retry after 20s",
        "provider returned malformed JSON",
        "empty_reply",
        "model_load_gated_low_memory — does not fit free RAM",
        "the model produced a finding that cites no line",
    ],
)
def test_an_ordinary_failure_is_not_a_timeout(message: str) -> None:
    """`timeout` must not become a catch-all for "the call went badly"."""

    from core.agent_runtime.stepped_audit import _CALL_TIMEOUT_RE

    assert not _CALL_TIMEOUT_RE.search(message)
