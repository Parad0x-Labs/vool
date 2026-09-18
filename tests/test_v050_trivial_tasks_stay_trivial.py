"""QA-050-024: a trivial turn must not buy a heavy lane.

The requirement is cross-cutting: exact-text responses, simple arithmetic, small local file reads,
ordinary conversational corrections and short multi-part Q&A must not invoke deep reasoning,
heavyweight models, multi-minute planning or cascading fallback unless something concrete fails.

These tests assert on the three decisions that actually buy that weight, because each is a real
mechanism and not a proxy:

* `task_class` -> `model_execution_profile`, where `research` means `provider_role=queen` and
  `allow_paid_fallback=True` -- a heavier candidate ranking and a paid arm;
* the autopilot LANE, where `deep` is the heavyweight model tier;
* `resolve_fallback_budget_seconds`, where the deep/cloud/human lanes return **None** -- an
  UNBOUNDED sequential fallback loop, which is the mechanism behind a multi-minute turn.

Measured on the untouched base a569e458, these are the rows that failed:

    small file read            research   summarization  queen  paid=True   daily  60.0
    conversational correction  research   summarization  queen  paid=True   daily  60.0
    one-sentence steps         unknown    action_plan    auto   paid=False  deep   None
"""
from __future__ import annotations

import pytest

from core.local_inference_autopilot import _resolve_lane
from core.memory_first_router import resolve_fallback_budget_seconds
from core.reasoning_engine import explicit_planner_style_requested
from core.task_router import classify, model_execution_profile

# The trivial classes QA-050-024 names, in the phrasings the smoke run used.
_TRIVIAL_TURNS = (
    "Reply with exactly: CLOUD-050-OK",
    "what is 137 x 29",
    "read ~/Desktop/notes.txt and tell me what is in it",
    "what is that? this is not what i have asked",
    "No, that is wrong. Lumen is a photo editor, not a note-taking app. Fix the description.",
    "Answer in one sentence: give me the numbered steps to boil an egg.",
)


def _route(text: str) -> dict[str, object]:
    """The real decisions, taken through the real functions rather than a mirror of them."""
    classification = classify(text, {"chat_surface": True})
    planner = explicit_planner_style_requested(text)
    profile = model_execution_profile(
        classification["task_class"],
        chat_surface=True,
        planner_style_requested=planner,
    )
    lane = _resolve_lane(
        user_text=text,
        task_kind=str(profile["task_kind"]),
        output_mode=str(profile["output_mode"]),
        source_context={},
        local_available=True,
        has_tiny_lane=True,
        has_deep_lane=True,
    )
    return {
        "task_class": classification["task_class"],
        "profile": profile,
        "lane": lane,
        "budget": resolve_fallback_budget_seconds(
            lane,
            forced_cpu=False,
            no_usable_gpu=False,
            output_mode=str(profile["output_mode"]),
        ),
    }


@pytest.mark.parametrize("turn", _TRIVIAL_TURNS)
def test_a_trivial_turn_does_not_reach_the_deep_lane(turn: str) -> None:
    assert _route(turn)["lane"] != "deep"


@pytest.mark.parametrize("turn", _TRIVIAL_TURNS)
def test_a_trivial_turn_keeps_a_bounded_fallback_budget(turn: str) -> None:
    """`None` is unbounded -- the sequential loop may then spend minutes across candidates."""
    budget = _route(turn)["budget"]

    assert budget is not None, f"{turn!r} bought an unbounded provider-fallback loop"
    assert float(budget) <= 180.0


@pytest.mark.parametrize(
    "turn",
    [
        "Reply with exactly: CLOUD-050-OK",
        "what is 137 x 29",
        "read ~/Desktop/notes.txt and tell me what is in it",
        "what is that? this is not what i have asked",
        "No, that is wrong. Lumen is a photo editor, not a note-taking app. Fix the description.",
    ],
)
def test_a_trivial_turn_does_not_buy_the_queen_role_or_a_paid_arm(turn: str) -> None:
    profile = _route(turn)["profile"]

    assert profile["provider_role"] == "auto"
    assert profile["allow_paid_fallback"] is False


def test_a_local_file_read_is_not_web_research() -> None:
    """QA-050-011's phrasing: routed `research` on the substring "what is" inside a path read."""
    assert _route("read ~/Desktop/notes.txt and tell me what is in it")["task_class"] != "research"


@pytest.mark.parametrize(
    "turn",
    [
        "what is in the file config.yaml",
        "search my repo for retry logic",
        "find the terminal command i ran",
    ],
)
def test_a_turn_naming_a_local_thing_is_not_web_research(turn: str) -> None:
    """The exclusion set is the module's own -- this branch just stops being the one that ignores it."""
    assert classify(turn, {"chat_surface": True})["task_class"] != "research"


@pytest.mark.parametrize(
    "turn",
    [
        "search for the latest ollama release",
        "look up who founded solana on x",
        "find the best python http library",
        "what is the capital of France",
    ],
)
def test_a_real_lookup_still_routes_to_research(turn: str) -> None:
    """The control. Narrowing the branch must not stop genuine web research reaching its lane."""
    assert classify(turn, {"chat_surface": True})["task_class"] == "research"


def test_a_genuine_plan_request_still_reaches_the_deep_lane() -> None:
    """The other control: this fix must not make VOOL shallow, only stop it overthinking."""
    routed = _route("give me a step-by-step rollout plan for the migration")

    assert routed["lane"] == "deep"
    assert routed["budget"] is None
