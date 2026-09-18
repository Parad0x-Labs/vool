"""A turn the runtime classified as research must be able to reach a tool.

Measured 2026-08-05, session `openclaw:b964a2c7056128822d22`: "what is the most produced commercial
aircraft of all time ... use authoritative sources and do not guess" was classified `research` and
`should_attempt_tool_intent` returned False, so it never reached a tool. Its whole trace carried no
search, no fetch and no tool call, and the model then narrated sources that were never retrieved.

The gate's own comment names the cause: below a certain line it is "a keyword allowlist -- an
attempt to guess, from the raw words, whether the user wanted a tool, decided BEFORE the model is
consulted". That allowlist has no vocabulary for an aviation question, exactly as it had none for
"BNB"/"ARB" and none for vool-video-director's own shipped example prompts. This is the third
instance recorded in that function.

`task_class` is not a raw-word guess. It is the classifier's own verdict, produced before this gate
runs, and `research` means "go and find out".
"""
from __future__ import annotations

import pytest

from core.execution.planner import should_attempt_tool_intent
from core.plain_task_routing import (
    is_ordinary_multi_part_plain_task,
    multipart_task_class_requires_tool_reachability,
)

CHAT = {"surface": "openclaw", "platform": "openclaw"}

AVIATION = ("what is the most produced commercial aircraft of all time? compare the boeing 737 and "
            "the cessna 172 by total units built, and tell me the most common variant, the country "
            "where most were delivered, and the single year with the highest production. use "
            "authoritative sources and do not guess")

# Original reported wording, six clean semantic families, and five typo/user-style variants. The
# vocabulary is intentionally eclectic: the authority being tested is the upstream research class,
# not whether this gate happens to recognize a product, database, historical topic, or market.
RESEARCH_FAMILY = (
    "what is the most sold vw model, what engine and what colour?",
    "Which compact tractor sold most last year, and what transmission and paint option led?",
    "Compare PostgreSQL and MariaDB adoption and support the result with primary sources.",
    "Which heat-pump line leads its segment, and what capacity and refrigerant does it use?",
    "Compare two historical housing policies and provide dates plus supporting evidence.",
    "Identify the leading e-reader model, then report its storage and screen attributes with sources.",
    "Which observability database gained adoption fastest, in what sectors, and according to whom?",
    "wht crm got most adoption, what plan n region? pls use sources",
    "which solar panel model sold best, wat wattage n warranty, cite proof",
    "compare rust n go use in infra w sources pls",
    "old vaccine rollout vs newer one, need dates n evidence",
    "top e reader last year? model storage color? dont guess",
)


@pytest.mark.parametrize("task_class", ["research", "chat_research"])
def test_a_research_turn_reaches_the_tool_lane(task_class) -> None:
    assert should_attempt_tool_intent(AVIATION, task_class=task_class, source_context=CHAT) is True


@pytest.mark.parametrize(
    "prompt",
    (AVIATION, *RESEARCH_FAMILY, "tell me about stoicism"),
)
def test_research_reachability_does_not_depend_on_the_domain_vocabulary(prompt) -> None:
    """The allowlist's failure mode was domain-shaped: aviation, tickers and plugin prompts each
    missed it for lack of the right words. Classification does not care about the domain."""
    assert should_attempt_tool_intent(prompt, task_class="research", source_context=CHAT) is True


@pytest.mark.parametrize("prompt", RESEARCH_FAMILY)
@pytest.mark.parametrize("task_class", ("research", "chat_research"))
def test_related_research_subquestions_are_never_demoted_to_ordinary_chat(
    prompt: str,
    task_class: str,
) -> None:
    assert multipart_task_class_requires_tool_reachability(task_class) is True
    assert is_ordinary_multi_part_plain_task(prompt, task_class=task_class) is False
    assert should_attempt_tool_intent(prompt, task_class=task_class, source_context=CHAT) is True


def test_overbroad_ordinary_classification_would_recreate_research_dead_end(monkeypatch) -> None:
    """Load-bearing sabotage: remove the task-class distinction and the report fails again."""

    monkeypatch.setattr(
        "core.plain_task_routing._TOOL_REACHABLE_MULTIPART_TASK_CLASSES",
        frozenset(),
    )
    prompt = "what is the most sold vw model, what engine and what colour?"

    assert is_ordinary_multi_part_plain_task(prompt, task_class="research") is True
    assert should_attempt_tool_intent(prompt, task_class="research", source_context=CHAT) is False


def test_research_word_inside_an_ordinary_request_is_not_a_task_class_override() -> None:
    """Adversarial near-miss: user vocabulary cannot impersonate the classifier's verdict."""

    prompt = "Explain the phrase 'research and compare'; calculate 4 x 5; give a short title."
    assert should_attempt_tool_intent(
        prompt,
        task_class="chat_conversation",
        source_context=CHAT,
    ) is False


def test_same_related_questions_without_a_research_verdict_remain_tool_free() -> None:
    prompt = "what is the most sold camera model, what lens and what colour?"
    assert is_ordinary_multi_part_plain_task(prompt, task_class="chat_conversation") is True
    assert should_attempt_tool_intent(
        prompt,
        task_class="chat_conversation",
        source_context=CHAT,
    ) is False


def test_explicit_retrieval_prohibition_still_beats_research_classification() -> None:
    assert should_attempt_tool_intent(
        "Without web search or tools, compare the two systems from general knowledge.",
        task_class="research",
        source_context=CHAT,
    ) is False


def test_ordinary_chat_is_still_not_pushed_into_the_tool_lane() -> None:
    """The control. Offering tools to every conversational turn is the cost this gate exists to
    avoid, and small talk must not start paying it."""
    assert should_attempt_tool_intent(
        "hey how are you doing today", task_class="chat_conversation", source_context=CHAT
    ) is False


def test_a_surface_that_cannot_run_tools_still_refuses() -> None:
    """Structural checks run before the classification shortcut and must keep winning."""
    assert should_attempt_tool_intent(
        AVIATION, task_class="research", source_context={"surface": "web", "platform": "web"}
    ) is False


def test_an_empty_message_is_never_a_research_tool_turn() -> None:
    assert should_attempt_tool_intent("", task_class="research", source_context=CHAT) is False
