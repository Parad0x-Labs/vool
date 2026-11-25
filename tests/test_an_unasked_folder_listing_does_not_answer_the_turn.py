"""A folder listing the model proposed on a turn that named no folder does not become the turn's reply (2026-11-25).

iMac gate on 4e06fdc5c (qwen3:8b, fresh profile): a question about the person's own life, asked in Lithuanian, had the
model call machine.list_directory. The semantic preflight refused it before anything ran, and that refusal, in English,
was the whole reply. A model-picked tool is a proposal, never the turn's demand: when the only failure is such a
refused proposal and nothing ran, the turn is answered on the ordinary path. Every wording below is written for this
file.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

REFUSED = "blocked_by_semantic_preflight"


def _decide(*, ok=False, status=REFUSED, steps=None):
    execution = SimpleNamespace(ok=ok, mode="tool_failed", status=status, tool_name="machine.list_directory")
    failed = {"tool_name": "machine.list_directory", "mode": "tool_failed", "status": status}
    return ResearchToolLoopFacadeMixin._listing_proposal_the_turn_never_asked_for(
        None, execution=execution, executed_steps=[failed] if steps is None else steps)


def test_a_refused_listing_proposal_goes_back_to_the_ordinary_answer():
    assert _decide() is True
    assert _decide(steps=[]) is True


def test_a_turn_where_a_step_already_ran_keeps_its_failure_report():
    ran = {"tool_name": "memory.search", "mode": "tool_executed", "status": "ok"}
    assert _decide(steps=[ran]) is False


def test_other_failures_are_not_taken_for_an_unasked_listing():
    for status in ("execution_failed", "blocked_by_action_policy", "missing_intent", "disabled", ""):
        assert _decide(status=status) is False, status


def test_a_listing_that_ran_is_not_a_failure_at_all():
    assert _decide(ok=True) is False


def test_the_preflight_refusal_is_only_raised_when_the_turn_named_no_target():
    """The decision above leans on this: the executor stamps the status only when the front door found no machine or
    path target in the person's own words, so a turn that did name a folder never reaches it."""
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    owner = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
             "runtime_session_id": "s-unasked", "turn_id": "t-unasked", "action_policy": "allowed"}
    refused = execute_tool_intent(
        {"intent": "machine.list_directory", "arguments": {"path": "~/Desktop"}},
        task_id="t-unasked",
        session_id="s-unasked",
        source_context={**owner, "_semantic_machine_list_directory_admitted": False},
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )
    assert refused.ok is False and refused.status == REFUSED
    assert refused.details.get("executed") is False
