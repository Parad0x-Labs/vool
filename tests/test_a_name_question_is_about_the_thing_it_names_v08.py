"""A question about the name of something the person owns is not a question about the person's own name.

Measured on the installed v0.8 build (d2dacb04, local model, fresh home): "remember that my turtle is named ..."
was saved, and from another chat, and again after a restart, "do you remember the name of my turtle?" was answered
"I don't know your name yet. Just tell me what to call you and I'll remember it." On the same build "which name did
I use for my gecko" got the same reply, "my gecko, what's its name?" was answered with the assistant's own name,
and "what's my name" without a question mark missed the name check and was answered by the model with the
assistant's name. The saved fact was there the whole time: "what is my gecko called?" was answered from it.

One reading error behind all of them: the owner of a name was taken from the nearest possessive word ("my",
"your", "its") instead of from the thing the name belongs to.

All names and things here are invented for this contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agent_runtime.cross_chat_recall import cross_chat_recall, saved_memory_rows
from core.user_identity_authority import classify_identity_question
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

# --- the reading ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what's the name of my ferret?",
        "do you recall the name of my aunt's parrot",
        "tell me the name of my first hamster",
        "what was the name of my last landlord again?",
        "the nickname of my van, what was it",
        "remind me of the name of my storage unit manager",
        "what's the name of my app?",
        "what is the name of your maker?",
        "which name did I use for my rowing boat",
        "what name did I save for my ferret?",
        "what name did I set for the garage wifi?",
        "what name did I use for this file",
        "which name did I save for the app?",
        "my ferret, what's its name?",
        "I have a tortoise. Do you remember its name?",
        "what's its name?",
        "what's my ferret's name?",
    ],
)
def test_the_name_of_another_thing_is_not_an_identity_question(text):
    question = classify_identity_question(text)
    assert not question.subject, (text, question)


@pytest.mark.parametrize(
    "text",
    [
        "what's my name?",
        "what's my name",
        "whats my name",
        "do you know my name",
        "tell me my full name",
        "what is the name of my profile?",
        "what's the name of my user account in settings",
        "what name did I save?",
        "what name did i put in settings",
        "which name did I enter for myself",
        "what name did I save for my profile",
        "what name did I set for me in the app",
    ],
)
def test_the_persons_own_name_is_still_theirs(text):
    assert classify_identity_question(text).asks_user_identity, text


@pytest.mark.parametrize(
    "text",
    [
        "what's your name?",
        "what's your name",
        "what is the name of your assistant?",
        "tell me the name of your app",
        "what is the name of this app?",
        "this app, what's its name?",
    ],
)
def test_the_assistants_own_name_is_still_its_own(text):
    assert classify_identity_question(text).asks_assistant_identity, text


# --- "remind me of ..." asks to be told again; a reminder needs a time or something to do ---------------------------


@pytest.mark.parametrize(
    ("text", "reminder"),
    [
        ("remind me of the name of my aunt's parrot", False),
        ("remind me of what the plumber is called", False),
        ("could you remind me of the ferret's name", False),
        ("remind me of my storage unit number", False),
        ("remind me of the vet tomorrow at 9", True),
        ("remind me to feed the ferret at 6pm", True),
        ("remind me about the vet on friday at 10", True),
    ],
)
def test_remind_me_of_a_thing_is_not_a_reminder_to_schedule(text, reminder):
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert (intent is not None and intent.kind == "schedule_reminder") is reminder, (text, intent)


# --- "my ferret, what's its name": the thing first, then the question, is one request ----------------------------


@pytest.mark.parametrize(
    ("text", "one_request"),
    [
        ("my ferret, what's its name", True),
        ("my aunt's parrot, what was the name?", True),
        ("our rowing boat, which name did we pick", True),
        ("my printer is broken, how do I fix it?", False),  # a statement, then a how-to
        ("my ferret, book the vet for friday", False),  # an order after the comma
        ("my shopping list: eggs, what else?", False),
    ],
)
def test_a_fronted_topic_and_its_question_need_no_planner(text, one_request):
    from core.agent_runtime.turn_planner import recall_question_needs_no_planner

    assert recall_question_needs_no_planner(text) is one_request, text


# --- recall from another chat, in process -------------------------------------------------------------------------

OWNER = {"_owner_local": True}


@pytest.mark.parametrize(
    "asked",
    [
        "what's the name of my ferret?",
        "which name did I use for my ferret",
        "what name did I give my ferret?",
        "what name did I end up choosing for my ferret",
        "what did i end up calling my ferret",
        "what did I finally name my ferret?",
        "my ferret, what's its name?",
        "my ferret, what was the name",
        "what's my ferret's name?",
        "remind me of the name of my ferret",
    ],
)
def test_the_saved_name_is_found_from_another_chat(isolated_home, asked):
    assert _say("keep in mind that my ferret is named Quillon", "told")[1].startswith("Locked in")
    _policy("ask")
    rows = saved_memory_rows(asked, session_id="ask", source_context=OWNER)
    assert rows and "Quillon" in rows[0], (asked, rows)
    found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
    assert found is None or found.found, (asked, found)  # never "I don't have that saved"


@pytest.mark.parametrize(
    "asked",
    [
        "which name did I use for my tortoise",
        "what name did I give my rowing boat?",
        "what's the name of my tortoise?",
    ],
)
def test_a_name_never_told_is_still_not_saved(isolated_home, asked):
    assert _say("keep in mind that my ferret is named Quillon", "told")[1].startswith("Locked in")
    _policy("ask")
    assert not saved_memory_rows(asked, session_id="ask", source_context=OWNER), asked
    found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
    assert found is not None and not found.found and "Quillon" not in found.response, (asked, found)


# --- served: the running app, a fresh home, told in one chat and asked from others -------------------------------

MODEL = "stub-private:4b"
NAME_REPLIES = ("I don't know your name yet", "Your name is", "My name is", "I have you down as")


def test_served_name_of_an_owned_thing_reaches_recall_from_another_chat(tmp_path: Path):
    """The model here answers "Sure, noted." to everything, so the name can only come from recall.

    Each question is asked in its own new chat after the fact was saved in another. Before the repair the turn
    ended in the local name reply and never reached recall; now the saved fact is what the turn hands the model
    (or answers with), and no reply is about the person's or the assistant's own name.
    """
    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_everyday_remember_v08 import _stream
    from tests.test_private_chat_served_workflow import _certify, _Provider

    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Sure, noted."})
    questions = [
        "what's the name of my ferret?",
        "which name did I use for my ferret",
        "what name did I give my ferret?",
        "what did i end up calling my ferret",
        "my ferret, what's its name?",
        "remind me of the name of my ferret",
    ]
    asked: list[tuple[str, str, str]] = []
    with provider:
        run_in_home(home, SEED.format(root=repo, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            told = _stream(daemon.base_url, "keep in mind that my ferret is named Quillon", "name-of-thing-tell")
            assert told.startswith("Locked in"), told
            for n, text in enumerate(questions):
                before = len(provider.calls)
                reply = _stream(daemon.base_url, text, f"name-of-thing-ask-{n}")
                sent = json.dumps(provider.calls[before:])
                asked.append((text, reply, sent))
            own = _stream(daemon.base_url, "what's my name", "name-of-thing-own")
    for text, reply, sent in asked:
        assert not any(marker in reply for marker in NAME_REPLIES), (text, reply)
        assert "Quillon" in reply or "Quillon" in sent, (text, reply)
    # The person's own name is still answered locally, and a pet's name is never offered as theirs.
    assert own.startswith("I don't know your name yet"), own
    assert "Quillon" not in own, own
