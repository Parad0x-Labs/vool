"""One authority decides what a turn requires; every consumer enforces the same answer.

These tests are deliberately NOT a list of prompts that must route correctly. A prompt list is what
produced five separate single-prompt fixes over 2026-08-05/06 while a sixth instance of the same
defect kept appearing: `should_attempt_tool_intent`, the AI-first-chat keep-set and the curiosity
gate each carried their own semantic reading of "does this request demand evidence", so any new
wording could satisfy one and miss another.

The invariant below is what actually holds the design together: across a corpus of deliberately
varied phrasings -- including ones nobody wrote a rule for -- the consumers may not DISAGREE with
`requirements_for`. A future contributor who adds a sixth private reading fails
`test_no_consumer_may_disagree_with_the_authority` without having to guess which sentence exposes it.
"""

from __future__ import annotations

import dataclasses

import pytest

from core.execution_requirements import (
    ExecutionRequirements,
    RequiredToolsNotOfferedError,
    assert_lane_can_satisfy,
    requirements_for,
)

# Phrasings the runtime has never been tuned against, mixed with ones it has. The point is coverage
# of SHAPES -- evidence promise, currency, breakdown, self-reference, plain knowledge -- not of these
# exact sentences, which are just samples of each shape.
CORPUS = [
    # plainly stable knowledge -> no tools
    "why is the sky blue",
    "explain the difference between a mutex and a semaphore",
    "tell me about stoicism",
    "write a haiku about winter",
    "what does the acronym RAII stand for",
    # about the assistant, currency markers and all -> still no tools
    "what is your current mood right now",
    "how are you today",
    "which model did I select for this turn?",
    "what is your latest version",
    # the request itself promises evidence -> tools required
    "give me the exact figures with sources, do not guess",
    "fact-check this and cite your sources",
    "what is the current market share, sources please",
    "break down the numbers by country and year",
    "which region sells the most units, and separate confirmed from unavailable",
    "what's the latest on this, as of today",
    # never-before-seen wordings of the same contracts
    "i need per-quarter revenue, no speculation whatsoever",
    "only tell me what you can verify",
    "what year had the highest output, exact numbers",
]

TOOL_CAPABLE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}


def _authority(text: str, task_class: str = "unknown") -> ExecutionRequirements:
    return requirements_for(text, task_class=task_class, source_context=TOOL_CAPABLE_CONTEXT)


def _lane_probe_agent():
    """A minimal agent carrying the REAL predicate mixins the lane policy calls.

    Hand-written stubs for `_looks_like_builder_request` and friends would let this test drift away
    from shipped behaviour and keep passing after the real predicates changed. Only
    `_is_chat_truth_surface` is forced -- that is the surface precondition, not the decision under
    test.
    """
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
    from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin

    class _Agent(ProceedIntentSupportMixin, FastPathFacadeMixin, HiveTopicFacadeMixin):
        def _is_chat_truth_surface(self, _source_context: object) -> bool:
            return True

    return _Agent()


@pytest.mark.parametrize("text", CORPUS)
def test_no_consumer_may_disagree_with_the_authority(text: str) -> None:
    """The whole point of the consolidation, stated as an executable invariant.

    A consumer is allowed to be MORE permissive (offer tools where none are required -- costs a
    slower answer). It is never allowed to be more restrictive: refusing tools to a turn that
    requires them produces prose that reads as researched and is not.
    """
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    required = _authority(text).tools_required

    if required:
        assert should_attempt_tool_intent(
            text, task_class="unknown", source_context=TOOL_CAPABLE_CONTEXT
        ), f"authority requires tools for {text!r} but the tool gate refused them"

        # The turn must reach SOME retrieving path. Two qualify, and the distinction is real rather
        # than cosmetic: the live-info fast path keeps the turn in the AI-first lane but every one
        # of its modes performs a `WebAdapter` search (`fast_live_info_search.live_info_search_notes`
        # -- weather, news and fresh_lookup all retrieve). So `kept is True` only means "no tools"
        # when `_live_info_mode` is empty. Asserting a bare `kept is False` here failed on
        # "what's the latest on this, as of today" (mode `news`) and "what is the current market
        # share, sources please" (mode `fresh_lookup`) -- both of which do retrieve, so the
        # assertion was wrong, not the runtime.
        agent = _lane_probe_agent()
        retrieves_via_live_info = bool(agent._live_info_mode(text, interpretation=None))
        kept = should_keep_ai_first_chat_lane(
            agent,
            user_input=text,
            classification={"task_class": "chat_research"},
            interpretation=None,
            source_context=dict(TOOL_CAPABLE_CONTEXT),
            checkpoint_state=None,
        )
        assert (kept is False) or retrieves_via_live_info, (
            f"tools-less lane claimed {text!r}, which requires tools, "
            "and no live-info retrieval mode applies to it"
        )


@pytest.mark.parametrize("text", CORPUS)
def test_requirements_are_stable_and_immutable(text: str) -> None:
    """Same input, same contract -- and no stage can edit it after the fact."""
    first = _authority(text)
    assert first == _authority(text)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.tools_required = not first.tools_required  # type: ignore[misc]


def test_a_weak_label_cannot_strip_an_evidence_contract() -> None:
    """The exact shape of the EV-brief defect, as a rule rather than as that one prompt.

    A keyword classifier called the brief `config` because it asked for a battery-capacity
    "configuration", and that label removed the tool requirement the user had stated in their own
    words. Labels may escalate; they may never de-escalate.
    """
    text = "what is the most common configuration, with sources, do not guess"
    for label in ("config", "business_advisory", "chat", "unknown", "summarization", "code"):
        assert _authority(text, task_class=label).tools_required is True, (
            f"task_class={label!r} stripped the evidence contract"
        )


def test_a_label_may_escalate() -> None:
    plain = _authority("look over this module")
    assert plain.answer_mode == "DIRECT"
    escalated = _authority("look over this module", task_class="workspace_audit")
    assert escalated.answer_mode == "AUDIT"
    assert escalated.tools_required is True
    assert "task_class_escalated_to_audit" in escalated.reason_codes


def test_required_tools_not_offered_terminates_rather_than_answering() -> None:
    """A turn needing tools and handed none must fail loudly, not answer from weights."""
    requirements = _authority("give me exact figures with sources, do not guess")
    assert requirements.tools_required is True

    with pytest.raises(RequiredToolsNotOfferedError, match="REQUIRED_TOOLS_NOT_OFFERED"):
        assert_lane_can_satisfy(
            requirements, lane_supports_tools=False, offered_tool_count=0, lane_name="ai_first_chat"
        )
    # A tool-capable lane that ends up offering an empty catalogue is the same failure wearing a
    # different hat -- reachable is not working.
    with pytest.raises(RequiredToolsNotOfferedError):
        assert_lane_can_satisfy(
            requirements, lane_supports_tools=True, offered_tool_count=0, lane_name="tool_intent"
        )
    assert_lane_can_satisfy(
        requirements, lane_supports_tools=True, offered_tool_count=7, lane_name="tool_intent"
    )


def test_a_direct_turn_is_never_forced_through_tooling() -> None:
    """The consolidation must not become 'everything searches'.

    That would be the opposite failure: latency, browsing dependency and orchestration risk spent on
    "why is the sky blue", worst on the local models least able to absorb it.
    """
    for text in ("why is the sky blue", "write a haiku about winter", "what is your current mood"):
        requirements = _authority(text)
        assert requirements.tools_required is False
        assert requirements.answer_mode == "DIRECT"
        assert_lane_can_satisfy(
            requirements, lane_supports_tools=False, offered_tool_count=0, lane_name="ai_first_chat"
        )


def test_summarization_needs_material_not_merely_structure() -> None:
    """A long structured brief is a research request, not a summarization request.

    Reading "bullets + a table + classification instructions" as summarization is how a research
    turn reached the deep lane with no tools at all.
    """
    brief = "give me a table of exact adoption figures by country, with sources, do not guess"
    assert _authority(brief).user_material_supplied is False
    assert _authority(brief).tools_required is True

    with_material = requirements_for(
        brief, task_class="unknown", source_context={**TOOL_CAPABLE_CONTEXT, "attachments": ["r.pdf"]}
    )
    assert with_material.user_material_supplied is True
    assert "user_material_supplied" in with_material.reason_codes


def test_no_guess_suppresses_inference_wherever_it_appears() -> None:
    assert _authority("give me exact figures with sources, do not guess").inference_allowed is False
    assert _authority("what is the current market share").inference_allowed is True
