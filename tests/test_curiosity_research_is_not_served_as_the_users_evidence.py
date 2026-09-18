"""The curiosity roamer's own research must not become a user question's grounding observations.

`_should_frontload_curiosity` returned True for every `research` turn, and `turn_reasoning` merges
the resulting snippets into `context_snippets` -- so the model receives the roamer's topics as THIS
turn's evidence. Measured across three prompts on 2026-08-05/06:

    aviation -> "Apple/Android developer docs, GitHub"
    EV       -> "calendar software, project management trade-offs, Wikipedia meta-pages"
    aviation -> "The GitHub link mentions the Boeing 737's dominance"

The model was not inventing those. It was describing what it was handed, which is why the earlier
"fabricated tool history" reading was wrong -- and why this looked like cross-turn contamination
when no cache or leak existed.

Frontloading made sense when a research turn could not reach a tool. It now can, so the crutch only
poisons the evidence. A turn that does NOT demand current evidence keeps it: curiosity is a real
feature and this must not disable it wholesale.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

EV = ("What is the best-selling battery-electric car model of all time worldwide? Compare the Tesla "
      "Model 3 and Nissan Leaf by cumulative global deliveries or sales. Use current evidence "
      "retrieved during this turn from Tesla or Nissan disclosures, official registration agencies, "
      "regulatory filings, or reputable market-research sources. When reliable worldwide data does "
      "not exist, mark the field UNAVAILABLE. Do not guess.")
AVIATION = ("what is the most produced commercial aircraft of all time? compare the boeing 737 and "
            "the cessna 172 by total units built. use authoritative sources and do not guess")


class _Agent(ResearchToolLoopFacadeMixin):
    """Only the one method under test; the mixin needs nothing else for this decision."""


class _Interp:
    topic_hints: list[str] = []


@pytest.mark.parametrize("prompt", [EV, AVIATION])
def test_an_evidence_demanding_turn_is_not_preloaded_with_curiosity_research(prompt) -> None:
    """These are the measured failures. The user asked for current sources; the roamer's stored
    topics are not sources for their question."""
    assert _Agent()._should_frontload_curiosity(
        query_text=prompt,
        classification={"task_class": "research"},
        interpretation=_Interp(),
    ) is False


@pytest.mark.parametrize(
    "prompt",
    ["tell me about stoicism", "explain how a diesel engine works", "what is a monad"],
)
def test_an_ordinary_research_turn_still_gets_curiosity(prompt) -> None:
    """The control. Curiosity is a real feature -- a turn that does not demand current evidence
    still benefits from it, and disabling it wholesale would trade one defect for a worse one."""
    assert _Agent()._should_frontload_curiosity(
        query_text=prompt,
        classification={"task_class": "research"},
        interpretation=_Interp(),
    ) is True


def test_system_design_without_an_evidence_demand_still_frontloads() -> None:
    assert _Agent()._should_frontload_curiosity(
        query_text="design a clean agent architecture for a local telegram bot",
        classification={"task_class": "system_design"},
        interpretation=_Interp(),
    ) is True


def test_ordinary_chat_never_frontloaded() -> None:
    assert _Agent()._should_frontload_curiosity(
        query_text="hey how are you", classification={"task_class": "chat_conversation"},
        interpretation=_Interp(),
    ) is False
