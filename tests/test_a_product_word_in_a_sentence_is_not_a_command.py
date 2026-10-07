"""An ordinary sentence that contains a product word gets a model answer, not a script.

Owner report before the v0.7 release: some words in a normal message made VOOL answer with a canned
reply instead of a real model answer ("web0, null, hive mind and similar"). Every case below was
measured through the real chat entry point, `VoolAgent.run_once()`, on main 9adff83 plus Codex
pack 2, and each one was claimed by a keyword fast path:

* VoolBook profile script: "how do I create an account on github?", "how do I delete my post on
  reddit?", "is my bank account safe ...?", "I need to update display drivers on windows".
* Sharing-scope switch (a settings write): "how do bees use hive mind signals?" set this chat to
  SHARED PACK; "how do I make this public on github?" set it to PUBLIC COMMONS.
* Hive watcher script: "what tasks should I do in my hive this spring?", "we use hive mind tools at
  work, any risks?", "check the hive temperature, it's 5 degrees outside, ...".
* Credit status card: "how many credits do I need to graduate college?", "what is a credit score?".
* Telegram bridge setup: "I opened a telegram account, how do I set a username?".
* Memory erase: "forget about it, tell me a joke instead" ran an erase and said "Forget applied".
* Skill inventory: "which skill would you use to cook pasta?" got the list of installed skills.
* Project grounding: "nulla" (the old product name, and the Latin word for "nothing") and
  "liquefy" marked the turn as a question about VOOL.

Each ordinary sentence must reach the model lane (no model is configured in tests, so the model
lane answers with its own "no model" notice; reaching it is the signal) and must leave no settings
or profile write behind. The explicit commands each fast path exists for keep working.
"""

from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core.canonical_project_knowledge import has_canonical_project_entity
from core.memory.policies import parse_session_scope_command, session_memory_policy

SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}

ORDINARY_SENTENCES = [
    # VoolBook profile script
    "how do I create an account on github?",
    "how do I delete my post on reddit?",
    "should I write a social post about my cat for instagram?",
    "how do I change my name on facebook?",
    "I need to update display drivers on windows",
    "is my bank account safe if I use public wifi?",
    "how do I edit my post on linkedin?",
    "can you check my profile picture idea: a cat in a hat?",
    "what is my name?",
    # sharing-scope switch
    "how do bees use hive mind signals?",
    "because hive mind thinking ruins teams, how do I avoid it?",
    "share with hive members at my beekeeping club: what should I write?",
    "how do I make this public on github?",
    "how do I keep this private from my boss?",
    "hive mind",
    # Hive watcher script
    "we use hive mind tools at work, any risks?",
    "what's on the hive tonight? my bees are noisy",
    "anything on the hive inspection checklist I should add?",
    "check the hive temperature, it's 5 degrees outside, is that ok for bees?",
    "what tasks should I do in my hive this spring?",
    "what are the available tasks for my team? we use a hive spreadsheet",
    "should I ignore my boss or remind him about the deadline?",
    # credit status card
    "how many credits do I need to graduate college?",
    "what is a credit score?",
    "my credit card got declined, what now?",
    "I set a spend cap on my credit card, how does that work?",
    # Telegram bridge setup
    "I opened a telegram account, how do I set a username?",
    # memory erase
    "forget about it, tell me a joke instead",
    # skill inventory
    "which skill would you use to cook pasta?",
    "which skills are in demand for data science jobs?",
    # the words the owner named, in sentences that never were scripted: they must stay that way
    "My uncle wrote a book about web0 and I did not like it.",
    "The variable came back null in my JavaScript code, why?",
    "I named my cat Nulla, is that a good name?",
    "I read an article about hive mind behaviour in bees.",
]

# Kept: each fast path's own explicit command still runs it (matched on the script's own words).
EXPLICIT_COMMANDS = [
    ("check the hive", "Hive"),
    ("show me the open hive tasks", "Hive"),
    ("set hive mind", "Session scope set to SHARED PACK"),
    ("create my voolbook profile", "VoolBook profile"),
    ("how many credits do I have?", "compute credits"),
    ("connect telegram", "@BotFather"),
    ("forget the code ZX-41", "Forget"),
    ("list your skills", "skill(s)"),
]


def _turn(text: str) -> dict:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    return agent.run_once(text, source_context=dict(SOURCE_CONTEXT))


@pytest.mark.parametrize("text", ORDINARY_SENTENCES)
def test_an_ordinary_sentence_reaches_the_model(text: str) -> None:
    result = _turn(text)
    route = str(result.get("route") or "")
    assert route.startswith(("model", "plain_task_minimal")), (
        f"{text!r} was answered by {route!r} ({result.get('route_reason')!r}) instead of the model: "
        f"{str(result.get('response') or '')[:160]!r}"
    )
    scope = session_memory_policy(str(result.get("session_id") or ""))["share_scope"]
    assert scope == "local_only", f"{text!r} changed this chat's sharing scope to {scope!r}"


@pytest.mark.parametrize(("text", "script_words"), EXPLICIT_COMMANDS)
def test_an_explicit_command_still_runs(text: str, script_words: str) -> None:
    result = _turn(text)
    response = str(result.get("response") or "")
    assert script_words in response, f"{text!r} no longer runs its command: {response[:200]!r}"


def test_scope_commands_are_whole_message_commands() -> None:
    assert parse_session_scope_command("set hive mind")["share_scope"] == "hive_mind"
    assert parse_session_scope_command("ok, switch to public commons please")["share_scope"] == "public_knowledge"
    assert parse_session_scope_command("keep this private")["share_scope"] == "local_only"
    for sentence in (
        "hive mind",
        "how do bees use hive mind signals?",
        "because hive mind thinking ruins teams",
        "how do I make this public on github?",
        "how do I keep this private from my boss?",
    ):
        assert parse_session_scope_command(sentence) is None, sentence


def test_project_grounding_names_only_the_project() -> None:
    assert has_canonical_project_entity("what is vool?")
    assert has_canonical_project_entity("how do I register alice.null?")
    for sentence in (
        "I named my cat Nulla, is that a good name?",
        "who is Nulla in the Witcher?",
        "my ice cream started to liquefy in the sun",
        "what is solana?",
    ):
        assert not has_canonical_project_entity(sentence), sentence
