"""The pre-model tool gate, and what inverting it does.

`should_attempt_tool_intent` decides whether a turn may reach the tool loop, from the raw user
text, before any model is consulted. The structural checks (a surface that cannot run tools, no
text) are legitimate. The keyword allowlist below them is a guess about intent, and the guess is
what makes a tool's reachability depend on phrasing.

The plugin cases are the sharp edge and are measured, not hypothetical: the strings below are
copied from `vool-video-director`'s own `interface.defaultPrompt` array in
`~/Desktop/Vool-skills-plugins`. A matcher that runs before the catalog is assembled cannot
know about an installed plugin, so every plugin VOOL gains inherits this by construction.
"""
from __future__ import annotations

import pytest

from core import runtime_flags
from core.execution.planner import (
    is_conversational_memory_recall,
    should_attempt_tool_intent,
)

TOOL_CAPABLE = {"surface": "cli", "platform": "local"}

# Verbatim from the installed plugin manifest's interface.defaultPrompt.
PLUGIN_UTTERANCES = [
    "Prepare this local image as an identity-safe reference pack.",
    "Direct this video with VOOL and hard references.",
    "Prepare a cost-capped fal reference-video job.",
    "Diagnose this failed generation and plan one repair.",
]


def _gate(text: str, *, task_class: str = "unknown", context: dict | None = None) -> bool:
    return should_attempt_tool_intent(
        text,
        task_class=task_class,
        source_context=TOOL_CAPABLE if context is None else context,
    )


@pytest.mark.parametrize("utterance", PLUGIN_UTTERANCES)
def test_an_installed_plugins_own_example_prompts_are_still_refused_by_the_allowlist(
    utterance: str,
) -> None:
    """The gap is real and still open in the shipped default.

    These strings are vool-video-director's own interface.defaultPrompt. None names a path, so
    the near_miss fix does not reach them either — only the flag does. Recorded rather than
    papered over: a plugin's advertised wording is still unreachable unless the owner opts in.
    """

    assert _gate(utterance) is False


@pytest.mark.parametrize("utterance", PLUGIN_UTTERANCES)
def test_the_flag_makes_a_plugins_own_example_prompts_reachable(utterance: str) -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate(utterance) is True


def test_conversational_turns_also_reach_the_catalog_when_enabled() -> None:
    """A model handed tools it does not need simply answers; that is the accepted trade."""

    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("what is the capital of France") is True


def test_current_chat_memory_declaration_skips_catalog_when_enabled() -> None:
    """A current-chat fact is conversation, even when the catalog is available."""

    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("For this chat, the fictional project signal is LANTERN-742. Please remember it.") is False


def test_current_chat_memory_recall_skips_catalog_when_enabled() -> None:
    """A question about the current conversation must not turn into an action."""

    prompt = "What fictional project signal did I just name?"
    assert is_conversational_memory_recall(prompt) is True
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate(prompt) is False


def test_remember_to_perform_work_still_reaches_catalog_when_enabled() -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("For this chat, remember to create the release note file.") is True


# --------------------------------------------------------------------------------------
# Structural refusals stay in force. These are capability facts, not guesses about wording,
# and the flag must not override them.
# --------------------------------------------------------------------------------------


def test_empty_text_is_still_refused_with_the_flag_on() -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("   ") is False


def test_a_surface_that_cannot_run_tools_is_still_refused_with_the_flag_on() -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("list every file in /tmp", context={"surface": "email", "platform": "smtp"}) is False


# --------------------------------------------------------------------------------------
# The existing allowlist must keep working untouched while the flag is off, since that is
# what ships until this has been driven.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "list every file in ~/Desktop",
        "read ~/Desktop/notes.md",
        "proceed",
        "do it",
        "run it",
        "https://example.com — what does this page say",
    ],
)
def test_previously_matching_phrasings_still_match_with_the_flag_off(utterance: str) -> None:
    assert _gate(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Exactly four words: describe a workspace where thinking feels easy.",
        "What makes a project feel calm to work in?",
        "Compare an organized workspace with a cluttered one.",
    ],
)
def test_workspace_topic_nouns_do_not_request_execution(utterance: str) -> None:
    assert _gate(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Audit this workspace for stale files.",
        "Search the repo for ContextAccessPolicy.",
        "What is in this project?",
    ],
)
def test_workspace_actions_still_reach_the_tool_gate(utterance: str) -> None:
    assert _gate(utterance) is True


def test_response_constraint_stays_conversational_with_catalog_enabled() -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate(
            "Exactly four words: describe a workspace where thinking feels easy."
        ) is False


def test_constrained_explicit_workspace_action_still_reaches_catalog() -> None:
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate("List the files in this workspace using exactly four words.") is True


def test_the_flag_ships_off() -> None:
    """Off deliberately: the near_miss path fix delivers the measured 8/8 without it."""

    assert runtime_flags.flag_enabled("always_on_tool_catalog") is False


@pytest.mark.parametrize(
    "advice",
    [
        # Verified to reach looks_like_advice_only_execution_prompt and return False.
        "explain how a hash map works",
        "should I use postgres or sqlite for this",
    ],
)
def test_advice_only_prompts_are_refused_only_while_the_flag_is_off(advice: str) -> None:
    """Note the direction: with the flag ON these now reach the catalog, which is the point."""

    """Shows the flag genuinely bypasses the content-guessing branch, not just adds to it.

    Note how narrow that branch turns out to be. "how would I go about listing the files in a
    directory in python" — plainly a question about writing code, not a request to touch this
    machine — passes the gate today, because the allowlist matches on the words rather than on
    what is being asked. That is the same failure as the plugin cases above, in the opposite
    direction: over-matching on wording instead of under-matching.
    """

    assert _gate(advice) is False
    with runtime_flags.override("always_on_tool_catalog", True):
        assert _gate(advice) is True
