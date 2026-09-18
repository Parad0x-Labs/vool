"""Software-authoring turns stay whole and get artifact room -- the 2026-09-17 incident.

Two logged failure shapes, one register:

* CARVING. "Build me a small Python provider-health checker ... - verify ... - retrieve ... -
  make a small request" matched no software-artifact noun ("checker" was absent from the
  register), so neither decomposer declined it: the planner split it into five fragment
  sub-turns ("- measure total request latency" became a standalone task), each classified
  differently, each dying separately, and the merge reported every part unanswered beside a
  grounding refusal.
* TRUNCATION. A "build one complete index.html" turn was cut at the chat table's output
  ceiling and shipped "(Incomplete: this answer stopped before it finished)" -- twice, on two
  different providers. The partial was preserved honestly; the ceiling simply cannot hold a
  whole artifact.

The repairs: the artifact register learned the agent-noun family (checker, viewer, monitor,
...); both decomposers decline a software-authoring request (its bullet list is the spec of
ONE build, not several requests); and the generation profile carries 2600 tokens of
authoring room through the lane policy, the same carried-intent shape the creative and
X-draft raises use.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.grounded_mode import is_software_authoring_request

CHECKER_REQUEST = (
    "Build me a small Python provider-health checker for OpenRouter. Research the current "
    "OpenRouter API and available documentation first. The program should accept a model ID such "
    "as: z-ai/glm-5.3-flash Then: - verify that the model currently exists - retrieve whatever "
    "current pricing/context metadata OpenRouter exposes - make a very small chat completion "
    "request - measure total request latency"
)

HTML_APP_REQUEST = (
    "Build a polished single-file HTML application called Agent Trace Viewer. - shows the events "
    "as a horizontal execution timeline - lets me filter event types - includes a compact summary "
    "panel - Put everything in one complete index.html file. Make it genuinely usable, not just "
    "a mockup."
)


@pytest.mark.parametrize(
    "text",
    [
        CHECKER_REQUEST,
        HTML_APP_REQUEST,
        "Build me a small disk-usage monitor in Go",
        "Create a config linter for our YAML files",
        "Write a log viewer script",
        "make me a temperature tracker",
    ],
)
def test_agent_noun_artifacts_are_authoring_requests(text: str) -> None:
    assert is_software_authoring_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "What is the price of Bitcoin right now?",
        "write a poem about the sea",
        "check the latest news about OpenRouter pricing",
        "Explain how a hash table works",
    ],
)
def test_non_artifact_requests_are_not_authoring(text: str) -> None:
    assert not is_software_authoring_request(text)


def test_the_planner_refuses_to_split_an_authoring_request() -> None:
    from core.agent_runtime.turn_planner import plan_turn

    def _must_not_be_called(_system: str, _prompt: str) -> str:
        raise AssertionError("the planner model must not be consulted for an authoring turn")

    assert plan_turn(CHECKER_REQUEST, ask_model=_must_not_be_called) == []
    assert plan_turn(HTML_APP_REQUEST, ask_model=_must_not_be_called) == []


def test_authoring_turns_get_artifact_room_in_the_profile() -> None:
    from core.prompt_normalizer import _generation_profile

    profile = _generation_profile(
        surface="web",
        task_kind="conversation",
        output_mode="plain_text",
        task_class="system_design",
        user_text=HTML_APP_REQUEST,
    )
    assert profile["max_output_tokens"] >= 2600
    assert profile["output_budget_intent"]["reason"] == "software_authoring_room"
    # The lane policy must not clamp the room back to the paid chat target.
    from core.output_budget_policy import (
        LaneCapability,
        OutputBudgetIntent,
        resolve_output_budget,
    )

    resolved = resolve_output_budget(
        OutputBudgetIntent(
            output_mode="plain_text",
            base_tokens=profile["max_output_tokens"],
            floor=profile["max_output_tokens"],
            ceiling=0,
        ),
        LaneCapability(context_window=128000, max_output_tokens=8192, cost_class="paid_cloud"),
    )
    assert resolved.tokens >= 2600


def test_ordinary_chat_keeps_the_ordinary_budget() -> None:
    from core.prompt_normalizer import _generation_profile

    profile = _generation_profile(
        surface="web",
        task_kind="conversation",
        output_mode="plain_text",
        task_class="chat_conversation",
        user_text="Explain how a hash table works, briefly.",
    )
    assert profile["max_output_tokens"] < 2600


def test_a_utility_request_is_authoring_and_not_a_machine_inspection() -> None:
    """The 2026-09-17 hard-degradation incident, both halves.

    "Build me a small Python provider-health checker ... The program should accept a model ID"
    was answered, wholesale, with this machine's HARDWARE SPECIFICATIONS: the machine-specs
    extractor matched "ram" as a substring of "progRAM" and "check" inside "checker", and its
    authoring guard knew "write/create/edit" but not "build"."""
    from core.execution.planner import _extract_machine_specs_request

    assert _extract_machine_specs_request(CHECKER_REQUEST) is None
    assert is_software_authoring_request(
        "I need a small Python utility for an AI gateway. Build a script that lists models"
    )
    # Genuine spec questions keep their tool.
    for text in (
        "what are my machine specs",
        "how much ram do i have",
        "check my cpu cores",
        "which chip is this machine running on",
        "how many cores does it have",
    ):
        assert _extract_machine_specs_request(text) is not None, text
    # Words that merely CONTAIN the short hardware words must not trigger an inspection.
    for text in (
        "run this program again",
        "the telegram bot is offline",
        "a hardcore workout plan",
        "my chipotle order",
        "a corelogic report",
    ):
        assert _extract_machine_specs_request(text) is None, text
