"""A fact told after a short label ("for later: ...") is saved, and asking for it with a lead-in word or a slip in
"called"/"name" still finds it.

Real-app check v3 on fe98e849 (installed app, local qwen3:8b, fresh home, 2026-11-22): "hey for later: my turtle is
called noodle" was answered by the model and nothing was saved; "remind me what my turtle is claled :)" and "Alright
what name did I tell you for my turtle?" were both answered "I don't have that saved". The first because a fact behind
a label was never read as a tell; the other two because the typo and the lead-in word were counted as words the saved
fact had to hold. The wording is built from parts at random here, so no fixed sentence is what passes.

All names and things here are invented for this contract.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime.cross_chat_recall import cross_chat_recall
from tests.test_everyday_remember_v08 import _facts, _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}

OPENERS = ["", "hey ", "ok ", "oh and ", "btw "]
LABELS = ["for later", "one more thing to remember", "note to self", "fyi", "quick note", "for the record",
          "something to keep"]
SEPARATORS = [": ", " - ", " — "]
THINGS = ["turtle", "sailboat", "goldfish", "old campervan", "sourdough starter", "electric bike"]
NAMES = ["noodle", "Pistachio", "Captain Wobble", "Fennimore", "Ziggy"]
FACTS = ["my {thing} is called {name}", "my {thing} is named {name}", "my {thing}'s name is {name}"]
LEADS = ["", "Alright ", "ok so ", "hmm ", "So, ", "right "]
SLOTS = ["called", "claled", "caled", "callled", "named", "naemd", "nmaed"]
ASKS = ["{lead}what's my {thing} {slot} again?", "{lead}remind me what my {thing} is {slot} :)",
        "{lead}what is my {thing} {slot}?", "{lead}what name did I tell you for my {thing}?",
        "{lead}what was my {thing} {slot}"]


@pytest.mark.parametrize("seed", range(8))
def test_a_labelled_tell_is_saved_and_found_with_a_lead_in_or_a_slip(isolated_home, seed):
    rng = random.Random(f"framed-tell-{seed}")
    thing, name = rng.choice(THINGS), rng.choice(NAMES)
    fact = rng.choice(FACTS).format(thing=thing, name=name)
    told = rng.choice(OPENERS) + rng.choice(LABELS) + rng.choice(SEPARATORS) + fact
    assert _say(told, "told")[1].startswith("Locked in"), told
    assert any(name in saved for saved in _facts("told")), (told, _facts("told"))
    _policy("ask")
    for _ in range(4):
        asked = rng.choice(ASKS).format(lead=rng.choice(LEADS), thing=thing, slot=rng.choice(SLOTS))
        found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
        assert found is not None and found.found and name in found.response, (told, asked, found)


@pytest.mark.parametrize(
    "said",
    [
        "for later: remind me to call mom at 6",   # a request behind a label is not a fact
        "todo: buy milk",
        "imagine this: my car is a spaceship",     # a made-up frame is not a tell
        "quick question: is my locker 214?",
    ],
)
def test_a_label_in_front_of_something_that_is_not_a_fact_saves_nothing(isolated_home, said):
    _say(said, "told")
    assert _facts("told") == [], (said, _facts("told"))


@pytest.mark.parametrize(
    "asked",
    [
        "Noodle, what is it again?",        # the lead-in is the subject: kept
        "what game did I say my turtle likes?",  # "game" is a real word, not a slip of "name"
    ],
)
def test_a_lead_in_that_is_the_subject_or_a_real_word_is_not_dropped(isolated_home, asked):
    from core.agent_runtime.cross_chat_recall import _asked_terms

    terms = _asked_terms(asked)
    assert ("noodl" in terms or "noodle" in terms) if asked.startswith("Noodle") else "gam" in terms, terms


@pytest.mark.parametrize("seed", range(6))
def test_a_label_that_says_the_fact_changes_is_an_update_not_a_new_tell(isolated_home, seed):
    """"Update: my cello is kept at the music school now" replaces the saved fact, and with nothing saved saves
    nothing: the update reading owns a change, and a label in front of it does not turn it into a new tell."""
    rng = random.Random(f"framed-update-{seed}")
    thing = rng.choice(THINGS)
    old, new = rng.sample(NAMES, 2)
    label = rng.choice(["update", "Update", "correction", "Correction"])
    _say(f"{label}{rng.choice(SEPARATORS)}my {thing} is called {new} now", "empty")
    assert _facts("empty") == [], _facts("empty")
    assert _say(f"Remember that my {thing} is called {old}", "told")[1].startswith("Locked in")
    said = f"{label}{rng.choice(SEPARATORS)}my {thing} is called {new} now"
    assert _say(said, "told")[1].startswith("Updated"), said
    facts = _facts("told")
    assert [fact for fact in facts if new in fact] and not [fact for fact in facts if old in fact], (said, facts)


@pytest.mark.parametrize("seed", range(6))
def test_an_order_in_front_of_the_question_is_not_a_lead_in(seed):
    """"Forget what I told you about Ilona's favourite tree" is a forget, not "what did I tell you" with a lead-in in
    front: the words before the question are an order whose object the question is, and they stay."""
    from core.agent_runtime.cross_chat_recall import _without_lead_in

    rng = random.Random(f"order-lead-{seed}")
    thing = rng.choice(THINGS)
    order = rng.choice(["Forget", "forget", "Erase", "Delete"]) + " " + rng.choice(
        ["what I told you about", "everything I told you about", "what I said about"]) + f" my {thing}"
    assert _without_lead_in(order) == order, order
    asked = rng.choice(["Alright", "hmm", "ok so", "right"]) + " " + rng.choice(
        [f"what did I tell you about my {thing}?", f"what is my {thing} called?"])
    assert _without_lead_in(asked) != asked, asked
