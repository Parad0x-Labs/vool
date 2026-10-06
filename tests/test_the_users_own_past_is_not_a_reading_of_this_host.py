"""A question about what the user did is not a reading of this host or this runtime.

Measured 2026-10-05 on the fresh memory benchmark (run 2), two memory questions never reached
memory. One asked how much RAM the user had upgraded their laptop to and was answered "Machine
specs for this host: ... RAM: ..."; the other asked which of two events the user took part in first,
one of them a charity run "to raise money for a local children's hospital", and was answered "Last
model call: none recorded on this runtime yet."

Two owning causes. `asks_runtime_for_a_fact` promised to reject "a question about the past" but knew
only the textbook past ("historically", "used to"), so "did I upgrade" and "the monitor I bought" went
to the machine families on a hardware word. The runtime lane pattern read "run ... local" across a
purpose clause ("run to raise money for a local ..."). Both are fixed where they live, and these
fixtures drive the real front door as well as the detectors.

The phrasings below are written for this file; none is a benchmark question.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.fast_paths_machine import looks_like_supported_machine_read_request
from core.execution.constants import (
    machine_diagnostics_intent,
    machine_display_intent,
    machine_host_state_intent,
    machine_live_load_intent,
    names_a_device_other_than_this_host,
    recalls_the_users_own_history,
)
from core.runtime_lane_truth import runtime_lane_question

# What the user did, said, owns elsewhere, or was told. The conversation holds the answer.
THE_USERS_OWN_HISTORY = (
    "How much RAM did I put in my laptop when I upgraded it?",
    "What GPU did I end up ordering for the new build?",
    "How many cores did the processor I returned have?",
    "What was the battery level on my laptop when I left the office?",
    "Which CPU did you recommend for my video editing rig?",
    "How much memory did you say I should get?",
    "What screen resolution did I pick for the second monitor?",
    "What's the refresh rate of the monitor I bought last month?",
    "Which machine was I training the model on two weeks ago?",
    "What apps did I mention were draining my battery?",
    "Do you remember how much RAM my old desktop had?",
    "How much RAM does my work laptop have?",
    "What's the battery life on my daughter's laptop?",
    "How long does my phone battery last?",
    "Is my laptop on battery when I take it to the library?",
)

# Live questions about this host, several carrying a past form beside the present ask.
LIVE_HOST_QUESTIONS = (
    "how much ram do i have",
    "what gpu do i have",
    "how many cores does this machine have",
    "what chip does my mac have",
    "how much ram have i got",
    "I upgraded my RAM yesterday, how much do I have now?",
    "I was wondering, how much RAM do I have?",
    "I asked before but what gpu do i have?",
    "when did I last restart my mac",
    "how long has this machine been up",
    "is it on battery right now?",
    "I just installed Chrome, what's eating my cpu?",
    "top 5 processes by cpu please",
    "how much disk space do i have left on this mac",
    "how much of the ssd have i used up",
)


def _claims_a_host_read(text: str) -> bool:
    return bool(
        looks_like_supported_machine_read_request(text)
        or machine_host_state_intent(text)
        or machine_live_load_intent(text)
        or machine_diagnostics_intent(text)
        or machine_display_intent(text)
    )


@pytest.mark.parametrize("text", THE_USERS_OWN_HISTORY)
def test_the_users_own_history_is_not_claimed_by_a_machine_family(text: str) -> None:
    assert recalls_the_users_own_history(text) or names_a_device_other_than_this_host(text), text
    assert not _claims_a_host_read(text), f"{text!r} would be answered with this host's state"


@pytest.mark.parametrize("text", LIVE_HOST_QUESTIONS)
def test_a_live_question_still_reaches_the_host(text: str) -> None:
    assert _claims_a_host_read(text), f"{text!r} no longer reaches the host read"


def test_a_purpose_clause_does_not_carry_locality_to_the_serve_verb() -> None:
    # The noun "run" and the adjective "local" in two different predicates.
    for text in (
        "Which did I do first, the book club or the 10K run to raise funds for a local shelter?",
        "Which came first, the bake sale or the fun run for the local school?",
    ):
        assert runtime_lane_question(text) == "", text
    # The serving questions the same pattern exists for.
    for text in (
        "are you running locally right now?",
        "is it running on a local model?",
        "are you running this locally?",
    ):
        assert runtime_lane_question(text) == "lane", text


def test_a_question_about_the_users_past_is_not_a_runtime_status_question() -> None:
    assert runtime_lane_question("did I run the 5K for the local hospital before the volleyball league?") == ""
    # A quoted earlier claim is still a question about the runtime now.
    assert runtime_lane_question("you said you're running locally, are you running locally right now?") == "lane"


@pytest.fixture
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _front_door(agent, text: str) -> dict | None:
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=text,
        effective_input=text,
        normalized_input=text,
        source_surface="api",
        session_id=f"own-past-{abs(hash(text)) % 100_000}",
        source_context={"surface": "api", "_owner_local": True},
        persona=None,
        interpreted=SimpleNamespace(understanding_confidence=0.8),
    )
    return (outcome or {}).get("result")


@pytest.mark.parametrize(
    "text",
    (
        "How much RAM did I put in my laptop when I upgraded it?",
        "Which did I do first, the book club or the 10K run to raise funds for a local shelter?",
        "What's the refresh rate of the monitor I bought last month?",
    ),
)
def test_the_front_door_leaves_a_memory_question_to_the_answer_path(agent, text: str) -> None:
    result = _front_door(agent, text)
    response = str((result or {}).get("response") or "")
    assert "Machine specs for this host" not in response, response
    assert "Last model call" not in response, response
    assert "Current state of this host" not in response, response
    assert result is None, f"{text!r} was claimed by a deterministic lane: {response[:120]!r}"


@pytest.mark.parametrize(
    "text,heading",
    (
        ("how much ram do i have", "Machine specs for this host"),
        ("I upgraded my RAM yesterday, how much do I have now?", "Machine specs for this host"),
        ("when did I last restart my mac", "Current state of this host"),
    ),
)
def test_the_front_door_still_reads_this_host_for_a_live_question(agent, text: str, heading: str) -> None:
    result = _front_door(agent, text)
    assert result is not None, f"{text!r} fell through to the model"
    assert heading in str(result.get("response") or "")
