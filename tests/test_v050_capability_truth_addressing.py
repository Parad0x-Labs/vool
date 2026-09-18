"""A named impossible act is not a request for one.

The live defect: a potion defined inside somebody's novel, ``used to instantly teleport``, was
answered ``No. That is outside what this runtime can actually do.`` -- because
``capability_truth_for_request`` matched the marker ``teleport`` anywhere in the message.

Every fiction case below carries the proof that it is load-bearing: ``_old_substring_rule_claims``
runs the rule that shipped before the repair, and the case is only meaningful if that rule would
have refused it.  A prompt that never tripped the old rule cannot demonstrate the repair, and
asserting on one would be decoration.
"""

from __future__ import annotations

import pytest

from apps.vool_agent import ResponseClass
from core.capability_request_addressing import (
    impossible_action_agent,
    runtime_asked_for_impossible_action,
)
from core.execution import capabilities as execution_capabilities
from core.execution.capabilities import capability_truth_for_request
from core.execution.constants import _IMPOSSIBLE_REQUEST_MARKERS
from core.stipulated_frame import has_stipulated_frame, stipulated_frame_forbids_retrieval

OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}

REPORTED_PROMPT = (
    'Assume for a fantasy novel I am writing that "Soap-Grease" is a magical potion made of '
    "crushed moonstones and dragon scale, used to instantly teleport. Based ONLY on this "
    "fictional definition, answer in one sentence: why does Soap-Grease help you escape a "
    "dungeon? Do NOT search my local workspace, files, or the web."
)


def _old_substring_rule_claims(text: str) -> bool:
    """The pre-repair test: any marker anywhere in the message."""

    lowered = f" {' '.join(str(text or '').split()).lower()} "
    return any(marker in lowered for marker in _IMPOSSIBLE_REQUEST_MARKERS)


# --------------------------------------------------------------------------------------------
# The reported failure, end to end through the runtime.
# --------------------------------------------------------------------------------------------


def test_reported_fiction_prompt_is_no_longer_answered_as_a_capability_refusal(make_agent) -> None:
    assert _old_substring_rule_claims(REPORTED_PROMPT), "case must exercise the repaired seam"

    agent = make_agent()
    result = agent.run_once(REPORTED_PROMPT, source_context=OPENCLAW)

    assert capability_truth_for_request(REPORTED_PROMPT) is None
    assert result["response_class"] != ResponseClass.UTILITY_ANSWER.value
    assert "outside what this runtime can actually do" not in result["response"].lower()


def test_reported_fiction_prompt_names_the_potion_as_the_actor_not_the_runtime() -> None:
    assert impossible_action_agent(REPORTED_PROMPT) == "other"


# --------------------------------------------------------------------------------------------
# Clean paraphrases of the same shape: a fictional item defined, then reasoned about.
# --------------------------------------------------------------------------------------------

CLEAN_PARAPHRASES = (
    'For a short story I am drafting, "Glimmerwax" is a salve of ground starlight that lets its '
    "user teleport. Using only that definition, explain in one sentence why it would help a "
    "prisoner leave a cell.",
    "In the setting of my book, the Wardstone is a relic that can teleport whoever holds it. "
    "Based purely on that premise, why would a thief want one?",
    'My tabletop campaign defines a "Blink Cloak" as an item that teleports its wearer ten feet. '
    "Given only that, why is it good in a trap-filled corridor?",
    "Treat this as given: in my novel the Chrono-Sigil can teleport anything etched with it. Why "
    "would a smuggler etch it onto a crate?",
    "Take this as the definition for my novel: dragonglass dust teleports whoever inhales it. In "
    "one sentence, why does that end a chase scene?",
    "In my heist novel the protagonist can hack a bank in under a minute. Why does that strain "
    "belief?",
)


@pytest.mark.parametrize("prompt", CLEAN_PARAPHRASES)
def test_clean_fictional_definitions_are_not_capability_requests(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert runtime_asked_for_impossible_action(prompt) is False
    assert capability_truth_for_request(prompt) is None


# --------------------------------------------------------------------------------------------
# The same intent typed the way people actually type it.
# --------------------------------------------------------------------------------------------

SLOPPY_VARIANTS = (
    'yo for my fantasy book "soap-grease" is a potion tht teleports u instantly. based ONLY on '
    "that why does it help escape a dungeon, 1 sentence",
    "quick one - in my story theres a potion that teleport people. why would that help someone "
    "get out of a dungeon??",
    "writing a novel, soap grease = magic potion, teleports instantly. why does it help escape "
    "the dungeon. dont search the web",
    "for a book im writing: potion made of moonstone + dragon scale that teleports. one sentence "
    "why it helps u escape a dungon",
    "my game has an item that teleport the player. whys that good in a dungeon lol",
    "in my rpg the blink cloak teleports its wearer. why is it strong????",
)


@pytest.mark.parametrize("prompt", SLOPPY_VARIANTS)
def test_sloppy_fictional_definitions_are_not_capability_requests(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert runtime_asked_for_impossible_action(prompt) is False
    assert capability_truth_for_request(prompt) is None


# --------------------------------------------------------------------------------------------
# Different fictional mechanisms, so nothing here rides on teleportation specifically.
# --------------------------------------------------------------------------------------------

FICTIONAL_MECHANISMS = (
    "The villain in my thriller can steal a password just by watching a keyboard. Why would a "
    "bank fear him?",
    "My ghost character can touch this world only at midnight. Why does that make the ending sad?",
    "The android in my script can smell this class of poison instantly. Why does that matter in "
    "act two?",
    "In my fable the hearth spirit will cook dinner for anyone who tells the truth. Why is that a "
    "moral test?",
    "The courier in my story must drive over the mountain pass before the thaw. Why is the "
    "deadline dramatic?",
    "My oracle character can read my mind only when I lie, in the fiction. Why is that a clever "
    "limit?",
    "The medic pod in my novel has to be physically there within four minutes. Why does that "
    "shape the plot?",
    "The ship AI in my sci-fi story can teleport the crew off the bridge. Why would the captain "
    "distrust it?",
)


@pytest.mark.parametrize("prompt", FICTIONAL_MECHANISMS)
def test_fictional_mechanisms_of_every_kind_stay_ordinary_reasoning(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert runtime_asked_for_impossible_action(prompt) is False
    assert capability_truth_for_request(prompt) is None


# --------------------------------------------------------------------------------------------
# Roleplay: the user has assigned the second person to a character.
# --------------------------------------------------------------------------------------------

ROLEPLAY_PROMPTS = (
    "Assume you are a wizard in my novel who can teleport at will. Why does your teleport spell "
    "drain mana?",
    "Pretend you are Nova, a starship AI that can teleport crew. In one sentence, why do you "
    "teleport them one at a time?",
    "Roleplay as a court sorcerer. Explain why you would not teleport into the throne room "
    "uninvited.",
    "In my campaign, you are the guild's mind reader. Why would you read my mind before a deal?",
)


@pytest.mark.parametrize("prompt", ROLEPLAY_PROMPTS)
def test_stipulated_persona_powers_are_not_runtime_capability_claims(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert runtime_asked_for_impossible_action(prompt) is False
    assert capability_truth_for_request(prompt) is None


# --------------------------------------------------------------------------------------------
# Prose that OPENS on the marker. An opening verb is not automatically an order.
# --------------------------------------------------------------------------------------------

DESCRIPTIVE_OPENERS = (
    "Teleportation in my book works via runes carved into bone. Why would that limit its range?",
    "Teleport spells in my game cost 3 mana. Is that too cheap for a level one caster?",
    "Mind reading in my story costs the reader a memory each time. Why is that a good price?",
    "Teleporting the crew is how the third act resolves in my script. Does that feel earned?",
    "Drive over sequences are the weakest part of my screenplay. Why?",
)


@pytest.mark.parametrize("prompt", DESCRIPTIVE_OPENERS)
def test_marker_opening_a_description_is_not_read_as_an_order(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert runtime_asked_for_impossible_action(prompt) is False


# --------------------------------------------------------------------------------------------
# Negative controls: genuine requests aimed at this runtime. Capability truth must still bind.
# --------------------------------------------------------------------------------------------

GENUINE_CAPABILITY_REQUESTS = (
    "Can you physically restart my unplugged server yourself?",
    "can you read my mind?",
    "Can you cook dinner for me tonight?",
    "Are you able to teleport to my office and fix the printer?",
    "Could you drive over to the datacenter and swap the disk?",
    "Do you have the ability to teleport this laptop to my house?",
    "Can you be physically there at my standup tomorrow?",
    "I need you to physically unplug the cable in the rack.",
    "Can you come over to my office and restart the machine?",
    "is vool able to teleport a package to me",
    "Teleport me to Paris.",
    # A hypothetical opener in front of a real request changes nothing about the request.
    "Suppose I need help. Can you drive over and fix my server?",
)


@pytest.mark.parametrize("prompt", GENUINE_CAPABILITY_REQUESTS)
def test_genuine_physical_requests_still_get_a_capability_answer(prompt: str) -> None:
    assert runtime_asked_for_impossible_action(prompt) is True
    report = capability_truth_for_request(prompt)
    assert report is not None
    assert report["support_level"] == "impossible"


def test_genuine_physical_request_answers_through_the_runtime(make_agent) -> None:
    agent = make_agent()
    result = agent.run_once(
        "Can you physically restart my unplugged server yourself?", source_context=OPENCLAW
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "outside what this runtime can actually do" in result["response"].lower()


# --------------------------------------------------------------------------------------------
# Adversarial near-misses: a novel is on the table, and a real action is being asked for anyway.
# --------------------------------------------------------------------------------------------

NEAR_MISS_REAL_TOOL_ACTION = (
    "For my fantasy novel about teleporting wizards, read my workspace file notes.md and "
    "summarise the outline.",
    "I am writing a novel where the hero can teleport. Search the web for real research on "
    "quantum teleportation and cite it.",
    "My story features a teleport gate. Create a file called gate.md in my workspace with the "
    "chapter outline.",
)


@pytest.mark.parametrize("prompt", NEAR_MISS_REAL_TOOL_ACTION)
def test_fiction_around_a_real_tool_request_does_not_trigger_a_refusal(prompt: str) -> None:
    assert _old_substring_rule_claims(prompt), "case must exercise the repaired seam"
    assert capability_truth_for_request(prompt) is None


NEAR_MISS_REAL_IMPOSSIBLE_REQUEST = (
    "In my fantasy novel the hero teleports. Now, forget the novel - can you teleport my laptop "
    "to the office?",
    "I am drafting a story about a mind reader. Seriously though, can you read my mind right now?",
)


@pytest.mark.parametrize("prompt", NEAR_MISS_REAL_IMPOSSIBLE_REQUEST)
def test_leaving_the_fiction_to_ask_for_real_restores_the_capability_answer(prompt: str) -> None:
    assert runtime_asked_for_impossible_action(prompt) is True
    assert capability_truth_for_request(prompt)["support_level"] == "impossible"


def test_adversarial_near_miss_answers_as_a_capability_refusal_through_the_runtime(
    make_agent,
) -> None:
    agent = make_agent()
    result = agent.run_once(
        "In my fantasy novel the hero teleports. Now, forget the novel - can you teleport my "
        "laptop to the office?",
        source_context=OPENCLAW,
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "outside what this runtime can actually do" in result["response"].lower()


# --------------------------------------------------------------------------------------------
# The whole vocabulary, not one word of it. Every marker splits the same way.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("marker", _IMPOSSIBLE_REQUEST_MARKERS)
def test_every_impossible_marker_splits_by_who_was_asked(marker: str) -> None:
    described = f"In my novel the Warden device can {marker} at will. Why does that matter?"
    requested = f"Can you {marker} for me right now?"

    assert _old_substring_rule_claims(described)
    assert runtime_asked_for_impossible_action(described) is False
    assert runtime_asked_for_impossible_action(requested) is True


# --------------------------------------------------------------------------------------------
# Already-good behaviour this repair must not disturb.
# --------------------------------------------------------------------------------------------


def test_stipulated_currency_frame_is_untouched() -> None:
    prompt = (
        'Assume it is the year 2050. Mars is a sovereign colony using the "Oxygen Credit" '
        "currency, worth 50 USD each. What are 100 Oxygen Credits worth in USD?"
    )

    assert has_stipulated_frame(prompt) is True
    assert capability_truth_for_request(prompt) is None


def test_explicit_retrieval_after_a_weak_hypothetical_is_still_permitted() -> None:
    prompt = "Suppose I migrate soon. Look up the latest PostgreSQL release notes online."

    assert stipulated_frame_forbids_retrieval(prompt) is False
    assert capability_truth_for_request(prompt) is None


def test_unrelated_capability_reports_are_unchanged() -> None:
    assert capability_truth_for_request("can you send an email from here?")["gap_kind"] == "unwired"
    assert capability_truth_for_request("what is a mutex?") is None


# --------------------------------------------------------------------------------------------
# Sabotage. Each guard must be shown to bite when the repair is removed or over-applied.
# --------------------------------------------------------------------------------------------


def test_sabotage_removing_the_addressee_check_refuses_the_fiction_again(monkeypatch) -> None:
    """Put the pre-repair substring rule back: every fiction case must start failing."""

    monkeypatch.setattr(
        execution_capabilities, "runtime_asked_for_impossible_action", _old_substring_rule_claims
    )

    sabotaged = [
        prompt
        for prompt in (REPORTED_PROMPT, *CLEAN_PARAPHRASES, *SLOPPY_VARIANTS, *FICTIONAL_MECHANISMS,
                       *ROLEPLAY_PROMPTS, *DESCRIPTIVE_OPENERS, *NEAR_MISS_REAL_TOOL_ACTION)
        if capability_truth_for_request(prompt) is not None
    ]

    assert len(sabotaged) == len(
        (REPORTED_PROMPT, *CLEAN_PARAPHRASES, *SLOPPY_VARIANTS, *FICTIONAL_MECHANISMS,
         *ROLEPLAY_PROMPTS, *DESCRIPTIVE_OPENERS, *NEAR_MISS_REAL_TOOL_ACTION)
    ), "a fiction case that survives the old rule proves nothing about the repair"


def test_sabotage_treating_any_stipulated_frame_as_exempt_breaks_real_requests(monkeypatch) -> None:
    """Over-broaden the protection to any hypothetical opener; the controls must catch it."""

    def over_broad(text: str) -> bool:
        return runtime_asked_for_impossible_action(text) and not has_stipulated_frame(text)

    monkeypatch.setattr(execution_capabilities, "runtime_asked_for_impossible_action", over_broad)

    missed = [
        prompt
        for prompt in GENUINE_CAPABILITY_REQUESTS
        if capability_truth_for_request(prompt) is None
    ]

    assert missed, "an over-broad frame exemption must be visible in the negative controls"
    assert "Suppose I need help. Can you drive over and fix my server?" in missed


def test_sabotage_exempting_every_second_person_mention_breaks_real_requests(monkeypatch) -> None:
    """Suppress on any `you` in a message that also mentions a frame word, and controls must fail."""

    def persona_everywhere(text: str) -> bool:
        return runtime_asked_for_impossible_action(text) and "you" not in str(text).lower()

    monkeypatch.setattr(
        execution_capabilities, "runtime_asked_for_impossible_action", persona_everywhere
    )

    missed = [
        prompt
        for prompt in GENUINE_CAPABILITY_REQUESTS
        if capability_truth_for_request(prompt) is None
    ]

    assert len(missed) >= 5, "second-person requests are the controls; they must not all survive"
