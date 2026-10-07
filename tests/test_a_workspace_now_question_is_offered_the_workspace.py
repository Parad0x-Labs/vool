"""A current-information question about the workspace is offered the workspace, not only the web.

Measured on 9adff83 (2026-10-06), with no model in the loop:

* "Which files in my workspace are markdown right now?" read as current information ("right now"),
  and `requirements_for` handed the offer `("web_search", "web_fetch")` alone. The model was shown
  six tools, none of which can observe a workspace.
* "List the files in my workspace right now and tell me which ones are markdown." counted as two
  ordinary plain requests ("list ...", "tell me ..."), so `should_attempt_tool_intent` declined and
  the turn went to the no-tools multi-part lane.

Both ended in the correct no-sources refusal, because nothing was ever observed. The phrasings
below are written for this file.
"""
from __future__ import annotations

import pytest

from core.capability_graph import (
    capability_hint_from_task_class,
    family_hint_from_task_class,
    model_visible_specs,
)
from core.execution.planner import should_attempt_tool_intent
from core.execution_requirements import requirements_for
from core.plain_task_routing import is_ordinary_multi_part_plain_task

WORKSPACE_NOW_QUESTIONS = (
    "Which files in my workspace are markdown right now?",
    "What files are in this repo right now?",
    "List the files in my workspace right now and tell me which ones are markdown.",
    "Show me the current files in this project and tell me which are tests.",
)

WEB_NOW_QUESTIONS = (
    "what is the latest news about the James Webb telescope right now?",
    "who is the current prime minister of Japan right now?",
    "what's the current status of the project Artemis right now?",
)


def _toolsets(text: str) -> tuple[str, ...]:
    return tuple(requirements_for(text, task_class="unknown", source_context={}).allowed_toolsets)


def _offered_intents(text: str) -> list[str]:
    # The served offer, built the way the router builds it (memory_first_router: model_visible_specs over the
    # turn's allowed toolsets). This line has no core.tool_offer_assembly; the patch's test called that module.
    specs = model_visible_specs(
        family_hint=family_hint_from_task_class("unknown"),
        capability_hint=capability_hint_from_task_class("unknown"),
        toolset_hints=_toolsets(text),
    )
    return [str(spec.get("intent") or "") for spec in specs]


@pytest.mark.parametrize("text", WORKSPACE_NOW_QUESTIONS)
def test_a_workspace_now_question_may_use_the_workspace(text: str) -> None:
    toolsets = _toolsets(text)
    assert "workspace" in toolsets, (text, toolsets)


@pytest.mark.parametrize("text", WORKSPACE_NOW_QUESTIONS)
def test_the_offer_seats_workspace_read_tools(text: str) -> None:
    intents = _offered_intents(text)
    assert any(intent.startswith("workspace.") for intent in intents), intents


@pytest.mark.parametrize("text", WEB_NOW_QUESTIONS)
def test_a_web_now_question_keeps_exactly_the_web_lanes(text: str) -> None:
    assert _toolsets(text) == ("web_search", "web_fetch"), text


def test_a_price_now_question_keeps_its_live_data_lane() -> None:
    assert _toolsets("what's the BTC price right now") == ("market_prices",)


@pytest.mark.parametrize(
    "text",
    (
        "List the files in my workspace and tell me which ones are markdown.",
        "List the files in my workspace right now and tell me which ones are markdown.",
    ),
)
def test_a_workspace_listing_compound_is_not_an_ordinary_plain_task(text: str) -> None:
    assert not is_ordinary_multi_part_plain_task(text, task_class="unknown"), text
    assert should_attempt_tool_intent(text, task_class="unknown", source_context={"surface": "api"}), text


@pytest.mark.parametrize(
    "text",
    (
        "list three fruits and tell me which ones are red",
        "explain recursion and give an example in python",
        "name two planets and tell me which one is bigger",
        "describe my project idea and suggest a name for it",
    ),
)
def test_ordinary_multi_part_prose_stays_in_the_plain_lane(text: str) -> None:
    assert is_ordinary_multi_part_plain_task(text, task_class="unknown"), text
