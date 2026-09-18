"""A research turn must not be kept in the conversational lane, because it cannot retrieve there.

Third and final link of a chain that each independently prevented retrieval, which is why fixing
the first two changed the trace without producing a single search:

  1. `model_execution_profile` forced `normalization_assist` on the chat surface, stripping the
     `summarize` capability from a research turn.                                    (203bc97d)
  2. `should_attempt_tool_intent` returned False for `research`/`chat_research`, so the gate
     refused.                                                                        (1f573145)
  3. `should_keep_ai_first_chat_lane` returned True for `chat_research` -- the class that MEANS
     research on the chat surface -- so `_maybe_execute_model_tool_intent` returned None before
     the gate was ever consulted.                                                    (this)

Measured 2026-08-06, session `openclaw:86ae9a9b420d418c7254`, with links 1 and 2 already fixed: the
aviation prompt still produced a 22-event trace with no search, no fetch and no tool call, and the
model fabricated the tool history it never had -- "The grounding search returned irrelevant sources
(Apple/Android developer docs, GitHub)". That fabrication is where the "contaminated retrieval"
reports came from: nothing was retrieved and nothing leaked.

After this fix, session `openclaw:778a427edeaf61a7fa9b`: 36 events including `web.search`,
`web.fetch`, `web.research` and `tool_synthesizing`.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.runtime_checkpoint_lane_policy import chat_surface_execution_task_class

AVIATION = ("what is the most produced commercial aircraft of all time? compare the boeing 737 and "
            "the cessna 172 by total units built, and tell me the most common variant, the country "
            "where most were delivered, and the single year with the highest production. use "
            "authoritative sources and do not guess")
CONSOLE = ("What is the best-selling video game console of all time worldwide? Compare the "
           "PlayStation 2 and Nintendo Switch by cumulative sales. Use current, authoritative sources.")


def _keep_set() -> set[str]:
    """The literal set the lane policy returns membership in."""
    import inspect

    from core.agent_runtime import runtime_checkpoint_lane_policy as policy

    source = inspect.getsource(policy.should_keep_ai_first_chat_lane)
    tail = source.rsplit("return routed_task_class in {", 1)[1]
    return {
        line.strip().strip(",").strip('"')
        for line in tail.split("}", 1)[0].splitlines()
        if line.strip().strip(",").strip('"')
    }


def test_an_evidence_demanding_research_turn_leaves_the_conversational_lane() -> None:
    """The class alone is the wrong discriminator -- "tell me about stoicism" is also
    `chat_research` and belongs in this lane. What separates them is whether the request demands
    evidence, which `answer_mode_for` decides."""
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for

    assert answer_mode_for(AVIATION) is not AnswerMode.DIRECT
    assert answer_mode_for(CONSOLE) is not AnswerMode.DIRECT


def test_ordinary_knowledge_stays_in_the_conversational_lane() -> None:
    """The regression the suite caught: sweeping every chat_research turn into the tool lane costs
    stable questions a browsing round trip they never needed."""
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for

    for prompt in ("tell me about stoicism", "explain how a diesel engine works",
                   "why is the sky blue?"):
        assert answer_mode_for(prompt) is AnswerMode.DIRECT, prompt


def test_ordinary_conversation_classes_are_still_kept() -> None:
    """The control. This lane exists so small talk does not pay for a tool catalog, and that must
    survive -- removing the whole set would trade one defect for a slower, worse one."""
    kept = _keep_set()
    for task_class in ("chat_conversation", "general_advisory", "creative_ideation",
                       "relationship_advisory", "food_nutrition"):
        assert task_class in kept, f"{task_class} should stay in the conversational lane"


@pytest.mark.parametrize("prompt", [AVIATION, CONSOLE])
def test_a_research_prompt_routes_to_chat_research_on_the_chat_surface(prompt) -> None:
    """The routing step that made the keep-set membership decisive. Recorded so the chain stays
    legible: `research` does not reach the lane policy under its own name."""
    assert chat_surface_execution_task_class(
        "research", user_input=prompt, context={}
    ) == "chat_research"


@pytest.mark.parametrize("prompt", [AVIATION, CONSOLE])
def test_the_whole_chain_now_permits_retrieval(prompt) -> None:
    """All three links together, asserted as one decision rather than three isolated predicates.

    Each link alone was sufficient to prevent retrieval, so testing them separately would have
    stayed green while the product could not search.
    """
    from core.execution.planner import should_attempt_tool_intent
    from core.model_capabilities import TASK_KIND_TO_CAPABILITIES
    from core.task_router import model_execution_profile

    chat = {"surface": "openclaw", "platform": "openclaw"}
    profile = model_execution_profile("research", chat_surface=True)
    assert "summarize" in TASK_KIND_TO_CAPABILITIES[str(profile["task_kind"])], "link 1: capability"
    assert should_attempt_tool_intent(prompt, task_class="research", source_context=chat), "link 2: gate"
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for
    assert answer_mode_for(prompt) is not AnswerMode.DIRECT, "link 3: lane"
