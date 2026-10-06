"""A web step the turn's own fetch veto refused does not end the turn on a failed-action line (2026-10-06).

Measured on round 3's LoCoMo questions (v12 and v13 alike): a question asked under "Answer using the imported
prior conversations ..." was labelled research, the workflow planner planned web.search, the turn's
`allow_remote_fetch: false` veto refused it before any socket opened, and the turn shipped "I wasn't able to turn
that into a completed action" without any model answer. A refused web step did nothing, so the turn now goes back
to the ordinary answer path; a turn that asked for the web itself keeps its failure report.
Every name, place and question below is written for this file.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

FRAME = ("Answer using the imported prior conversations. You may derive only answers supported by those records. "
         "If the records do not support an answer, say you do not know. Give a concise final answer.")
DEAD_LINE = "turn that into a completed action"
VETO = {"surface": "api", "platform": "api", "allow_remote_fetch": False}


# --- the decision, at its seam -------------------------------------------------------------------------------------
def _refused(tool="web.search", *, steps=None, context=None, ask="What is a good place for dogs to run around freely?"):
    execution = SimpleNamespace(ok=False, mode="tool_failed", status="execution_failed", tool_name=tool)
    failed = {"tool_name": tool, "mode": "tool_failed", "status": "execution_failed"}
    return ResearchToolLoopFacadeMixin._web_step_refused_by_turn_veto(
        None, execution=execution, executed_steps=[failed] if steps is None else steps,
        effective_input=FRAME + "\n" + ask, source_context=VETO if context is None else context)


@pytest.mark.parametrize("ask", [
    "What is a good place for dogs to run around freely?",                       # the measured wording
    "What are Wren's plans for her finished novel? Select the correct answer: (a) send it to agents "
    "(b) Not mentioned in the conversation",                                      # the measured multiple-choice shape
    "Where did Priya say the farmers market moved to?",
    "which cafe did sofia like best",
    "Who organised the bake sale at the library?",
    "what did tomas recommend for the kids on rainy days?",
])
def test_a_vetoed_web_step_that_never_ran_hands_the_turn_back(ask):
    assert _refused(ask=ask)
    assert _refused("web.fetch", ask=ask)
    assert _refused("browser.render", ask=ask)


def test_without_the_veto_a_failed_web_step_keeps_its_failure_report():
    assert not _refused(context={"surface": "api", "allow_remote_fetch": True})
    assert not _refused(context={"surface": "api"})


def test_a_refused_non_web_tool_keeps_its_failure_report():
    assert not _refused("workspace.read_file")
    assert not _refused("machine.specs")


def test_a_turn_where_a_tool_already_ran_keeps_its_failure_report():
    ran = {"tool_name": "workspace.search_text", "mode": "tool_executed", "status": "executed"}
    assert not _refused(steps=[ran, {"tool_name": "web.search", "mode": "tool_failed"}])


@pytest.mark.parametrize("ask", [
    "Search the web for the best dog parks in Lyon.",
    "google the opening hours of the botanical garden",
    "look it up online: who won the regional chess final?",
])
def test_a_user_who_asked_for_the_web_keeps_the_refusal(ask):
    assert not _refused(ask=ask)


# --- the real turn: the call site, through the agent ---------------------------------------------------------------
@pytest.fixture
def agent_with_a_model_answer(enable_web):
    from apps.vool_agent import VoolAgent
    from core.curiosity_roamer import CuriosityResult
    from core.memory_first_router import ModelExecutionDecision
    from storage.migrations import run_migrations
    from tests.conftest import make_stub_context

    run_migrations()
    agent = VoolAgent(backend_name="test-backend", device="refused-web-step-test", persona_id="default")
    agent.start()
    agent.context_loader.load = mock.Mock(return_value=make_stub_context())
    agent.memory_router.resolve = mock.Mock(return_value=ModelExecutionDecision(
        source="provider_execution", task_hash="refused-web-step", used_model=True, provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test", output_text="The dog park by the river, Audrey said.",
        confidence=0.7, trust_score=0.75, validation_state="valid"))
    agent.curiosity.maybe_roam = mock.Mock(return_value=CuriosityResult(enabled=False, mode="off", reason="test"))
    return agent


def test_the_measured_turn_reaches_the_model_answer_when_the_veto_refuses_its_web_step(agent_with_a_model_answer):
    agent = agent_with_a_model_answer
    events = []
    with mock.patch.object(agent, "_live_info_mode", return_value=""), \
            mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), \
            mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), \
            mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None), \
            mock.patch.object(agent, "_sync_public_presence", return_value=None), \
            mock.patch.object(agent, "_emit_runtime_event", side_effect=lambda ctx, **kw: events.append(kw) or {}):
        result = agent.run_once(FRAME + "\nWhat is a good place for dogs to run around freely and meet new friends?",
                                source_context=dict(VETO))
    response = str(result.get("response") or "")
    planned = [e for e in events if e.get("event_type") == "workflow_planner_step"]
    assert planned and planned[0].get("tool_name") == "web.search", events   # the misroute this net is for happened
    assert DEAD_LINE not in response, response
    assert "dog park" in response.lower(), response
