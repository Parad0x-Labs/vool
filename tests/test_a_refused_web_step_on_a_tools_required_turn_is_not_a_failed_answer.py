"""A tools-required memory question whose only web step the turn's fetch veto refused gets the model's answer.

Measured 2026-10-07 on the v0.7 candidate: memory questions such as "what is my current job" or "the average price
of the items I bought this year" read as needing tools, the workflow planner planned web.search, the turn's
`allow_remote_fetch: false` veto refused it before any socket opened, and the turn broke to the tool loop's synthesis
("tool_failed_after_evidence") on a step that never ran. The model's right answer was discarded for "0 of 1 tool
steps succeeded". The veto net (tests/test_a_refused_web_step_is_not_a_failed_answer_20261006.py) sat after that
break, so a tools-required turn never reached it. Every question and answer below is written for this file.
"""
from __future__ import annotations

from unittest import mock

import pytest

from tests.test_a_refused_web_step_is_not_a_failed_answer_20261006 import DEAD_LINE, FRAME, VETO

FAILED_STEPS = "tool steps succeeded"


@pytest.fixture
def agent_answering(enable_web):
    from apps.vool_agent import VoolAgent
    from core.curiosity_roamer import CuriosityResult
    from core.memory_first_router import ModelExecutionDecision
    from storage.migrations import run_migrations
    from tests.conftest import make_stub_context

    run_migrations()
    agent = VoolAgent(backend_name="test-backend", device="tools-required-veto-test", persona_id="default")
    agent.start()
    agent.context_loader.load = mock.Mock(return_value=make_stub_context())
    agent.curiosity.maybe_roam = mock.Mock(return_value=CuriosityResult(enabled=False, mode="off", reason="test"))

    def answer(text: str):
        agent.memory_router.resolve = mock.Mock(return_value=ModelExecutionDecision(
            source="provider_execution", task_hash="tools-required-veto", used_model=True,
            provider_id="ollama-local:test", provider_name="ollama-local", model_name="test", output_text=text,
            confidence=0.7, trust_score=0.75, validation_state="valid"))
        return agent

    return answer


def _turn(agent, question: str, context: dict, records: str = ""):
    # The records the capsule delivered for this turn, as the guards read them (the retrieval itself is not under
    # test here, and the model call is scripted).
    capsule = f"<retrieved_context>\n{records}\n</retrieved_context>" if records else ""
    events: list[dict] = []
    with mock.patch("core.bootstrap_context.admitted_capsule_evidence_text", return_value=capsule), \
            mock.patch.object(agent, "_live_info_mode", return_value=""), \
            mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), \
            mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), \
            mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None), \
            mock.patch.object(agent, "_sync_public_presence", return_value=None), \
            mock.patch.object(agent, "_emit_runtime_event", side_effect=lambda ctx, **kw: events.append(kw) or {}):
        result = agent.run_once(FRAME + "\n" + question, source_context=dict(context))
    return str(result.get("response") or ""), events


GYM = "- user said (stated 2025-03-02): My gym membership is 42 euros a month since March."
BOOTS = ("- user said (stated 2025-02-10): I bought trail boots for 120 dollars.\n"
         "- user said (stated 2025-05-21): I bought a second pair of hiking boots for 150 dollars.")


@pytest.mark.parametrize("question,answer,needle,records", [
    # "current" also arms the current-claim guard downstream, which withholds this answer for its own reason
    # (a separate miss family); this case pins only that the tool loop no longer ends the turn.
    ("What is my current gym membership fee?", "Your gym membership is 42 euros a month since March.", None, GYM),
    ("What is the average price of the hiking boots I bought this year?",
     "(120 + 150) / 2 = 135 dollars for the two pairs.", "135 dollars", BOOTS),
], ids=["current-fee", "average-price"])
def test_a_tools_required_memory_question_gets_the_model_answer_when_the_veto_refuses_its_web_step(
        agent_answering, question, answer, needle, records):
    from core.execution_requirements import requirements_for

    assert requirements_for(FRAME + "\n" + question, task_class="research", source_context=dict(VETO)).tools_required
    response, events = _turn(agent_answering(answer), question, VETO, records)
    planned = [e for e in events if e.get("event_type") == "workflow_planner_step"]
    assert planned and planned[0].get("tool_name") == "web.search", events   # the misroute this repair is for
    assert FAILED_STEPS not in response and DEAD_LINE not in response, response
    if needle:
        assert needle in response, response


# Without the veto the net does not apply; that side is pinned at the seam by
# tests/test_a_refused_web_step_is_not_a_failed_answer_20261006.py::test_without_the_veto_a_failed_web_step_keeps_its_failure_report.
