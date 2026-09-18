"""A rejected argument goes back to the model instead of ending the turn.

Measured 2026-07-28. A registered plugin tool was offered to qwen3:8b, which chose it correctly on
2 of 2 unseen phrasings and wrote correct JQL — then sent `limit="100"` where the schema declares
an integer. The executor refused it pre-dispatch with `limit: expected integer, got string ('100')`
and the turn ended. Passing `limit=100` by hand, the identical call succeeds. So a one-token
mistake, with its own fix spelled out in the error, cost the user the whole turn.

The fix is the feedback path, not a looser validator. Coercing "100" to 100 would make the next
wrong value — one that cannot be repaired safely — pass silently too.

Two bounds are the substance of this module: only *argument-shaped* failures qualify, so a
permission refusal still reaches the user as the real answer it is; and only twice per turn, so a
model that cannot satisfy a schema fails rather than burning the step budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin


@dataclass
class _Execution:
    status: str
    mode: str = "tool_failed"
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class _Facade(ResearchToolLoopFacadeMixin):
    """Only the loop helper is under test; runtime event emission is recorded, not performed."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, **kwargs):  # type: ignore[override]
        self.events.append(kwargs)


@pytest.fixture()
def facade() -> _Facade:
    return _Facade()


def _retry(facade: _Facade, execution: _Execution, steps: list[dict[str, Any]] | None = None) -> bool:
    return facade._retry_as_observation(
        execution=execution,
        tool_payload={"intent": "vool-jira.search_issues"},
        executed_steps=steps if steps is not None else [],
        loop_source_context={},
        seen_tool_payloads=set(),
    )


# --------------------------------------------------------------------------------------
# What qualifies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["invalid_arguments", "invalid_argument_shape", "missing_argument", "unsupported_argument"],
)
def test_an_argument_failure_is_retried(facade: _Facade, status: str) -> None:
    assert _retry(facade, _Execution(status=status)) is True


@pytest.mark.parametrize(
    "status",
    ["not_allowed", "denied", "timeout", "handler_failed", "not_found", "unsupported_intent"],
)
def test_a_real_world_failure_is_not_retried(facade: _Facade, status: str) -> None:
    """A permission refusal or a missing folder is an answer, not a mistake to retry.

    Retrying these would replace a true statement about the world with a loop.
    """

    assert _retry(facade, _Execution(status=status)) is False


def test_the_observation_carries_the_error_the_model_must_act_on(facade: _Facade) -> None:
    steps: list[dict[str, Any]] = []
    _retry(
        facade,
        _Execution(
            status="invalid_arguments",
            response_text="limit: expected integer, got string ('100')",
        ),
        steps,
    )
    assert len(steps) == 1
    observation = steps[0]["observation"]
    assert "expected integer" in observation
    assert "vool-jira.search_issues" in observation
    assert "call the tool again" in observation.lower()


def test_the_error_is_read_from_details_when_there_is_no_response_text(facade: _Facade) -> None:
    steps: list[dict[str, Any]] = []
    _retry(facade, _Execution(status="invalid_arguments", details={"error": "jql is required"}), steps)
    assert "jql is required" in steps[0]["observation"]


def test_a_correction_step_is_marked_and_not_counted_as_a_success(facade: _Facade) -> None:
    steps: list[dict[str, Any]] = []
    _retry(facade, _Execution(status="invalid_arguments"), steps)
    assert steps[0]["correction"] is True and steps[0]["ok"] is False


def test_the_runtime_event_names_the_tool_and_the_reason(facade: _Facade) -> None:
    _retry(facade, _Execution(status="invalid_arguments", response_text="limit must be an integer"))
    assert facade.events and facade.events[0]["event_type"] == "tool_argument_rejected"
    assert "limit must be an integer" in facade.events[0]["message"]


# --------------------------------------------------------------------------------------
# The bound
# --------------------------------------------------------------------------------------


def test_corrections_stop_after_the_budget(facade: _Facade) -> None:
    """A model that cannot satisfy the schema must fail the turn, not consume every step."""

    steps: list[dict[str, Any]] = []
    assert _retry(facade, _Execution(status="invalid_arguments"), steps) is True
    assert _retry(facade, _Execution(status="invalid_arguments"), steps) is True
    assert _retry(facade, _Execution(status="invalid_arguments"), steps) is False
    assert sum(1 for s in steps if s.get("correction")) == 2


def test_successful_steps_do_not_consume_the_correction_budget(facade: _Facade) -> None:
    steps: list[dict[str, Any]] = [{"tool_name": "a", "ok": True}, {"tool_name": "b", "ok": True}]
    assert _retry(facade, _Execution(status="invalid_arguments"), steps) is True


def test_an_execution_with_no_status_is_not_retried(facade: _Facade) -> None:
    assert _retry(facade, _Execution(status="")) is False
