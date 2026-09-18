"""The chat surface may change how an answer reads. It may not change what the turn can do.

Measured 2026-08-05, session `openclaw:20c336689d2a0ec3b35f`. An aviation question --
"most produced commercial aircraft ... use authoritative sources and do not guess" -- was classified
`research`, and its complete 18-event trace contains **no search, no tool call, no fetch**. The
model then narrated sources that were never fetched: "The GitHub link mentions the Boeing 737's
dominance in the narrow-body fleet segment".

Cause: `model_execution_profile` forced `task_kind="normalization_assist"` for every AI-first chat
class, and `research` is in that set. `normalization_assist` declares only {"format"};
`summarization` declares {"summarize"}. The turn accepted a grounded contract and was handed a
helper task kind that carries no retrieval.

Nothing was wrong with the search backend. Driven directly it returns
`en.wikipedia.org/wiki/List_of_most-produced_aircraft`, `airfleets.net/exploit/production-b737.htm`
and the Boeing 737 page -- six relevant hits, zero GitHub. Retrieval simply never ran.

This also explains why the same console prompt swung between all-UNAVAILABLE and fully-SUPPORTED
across runs: the variable was whether retrieval happened, not how good the search was.

The invariant is read off the capability table rather than a list of exceptions, so a task kind
added later is covered without anyone remembering this incident.
"""
from __future__ import annotations

import pytest

from core.model_capabilities import TASK_KIND_TO_CAPABILITIES
from core.task_router import _AI_FIRST_CHAT_DOMAIN_TASK_CLASSES, model_execution_profile


@pytest.mark.parametrize("task_class", sorted(_AI_FIRST_CHAT_DOMAIN_TASK_CLASSES))
def test_the_chat_surface_never_drops_a_capability(task_class) -> None:
    """The general invariant, over every class the override can touch."""
    base = model_execution_profile(task_class, chat_surface=False)
    chat = model_execution_profile(task_class, chat_surface=True)
    base_caps = set(TASK_KIND_TO_CAPABILITIES.get(str(base["task_kind"]), set()))
    chat_caps = set(TASK_KIND_TO_CAPABILITIES.get(str(chat["task_kind"]), set()))
    lost = base_caps - chat_caps
    assert not lost, (
        f"{task_class}: chat surface downgraded {base['task_kind']} -> {chat['task_kind']}, "
        f"losing {sorted(lost)}"
    )


@pytest.mark.parametrize("task_class", ["research", "chat_research"])
def test_a_research_turn_keeps_a_task_kind_that_can_retrieve(task_class) -> None:
    """The exact aviation failure. `normalization_assist` cannot retrieve, so a research request
    routed to it accepts a grounded contract it has no way to honour."""
    profile = model_execution_profile(task_class, chat_surface=True)
    assert profile["task_kind"] != "normalization_assist"
    assert "summarize" in TASK_KIND_TO_CAPABILITIES[str(profile["task_kind"])]


@pytest.mark.parametrize("task_class", sorted(_AI_FIRST_CHAT_DOMAIN_TASK_CLASSES))
def test_the_chat_surface_still_answers_in_prose(task_class) -> None:
    """The override's actual purpose must survive the fix: a conversational answer, not a
    summary_block. Preserving capability at the cost of readable output would trade one defect
    for another."""
    assert model_execution_profile(task_class, chat_surface=True)["output_mode"] == "plain_text"


@pytest.mark.parametrize("task_class", ["chat_conversation", "general_advisory", "creative_ideation"])
def test_ordinary_chat_classes_are_untouched(task_class) -> None:
    """Their base kind is already normalization_assist, so nothing changes and the fast
    conversational path pays nothing for this."""
    assert model_execution_profile(task_class, chat_surface=True)["task_kind"] == "normalization_assist"


def test_an_explicit_planner_request_still_wins() -> None:
    """The planner_style branch runs before this and must keep doing so."""
    profile = model_execution_profile("research", chat_surface=True, planner_style_requested=True)
    assert profile["task_kind"] == "action_plan"


def test_paid_fallback_and_provider_role_are_carried_through_unchanged() -> None:
    base = model_execution_profile("research", chat_surface=False)
    chat = model_execution_profile("research", chat_surface=True)
    assert chat["allow_paid_fallback"] == base["allow_paid_fallback"]
    assert chat["provider_role"] == base["provider_role"]
