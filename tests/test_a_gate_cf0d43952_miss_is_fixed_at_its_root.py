"""The misses of the iMac gate on cf0d43952 (qwen3:8b, fresh wording), each at its root and in other words.

- A recall nothing saved answers, in a chat with earlier messages, went to the model, which made up "You told me:
  Daiva has a bird named Klaudia". This chat's earlier messages are now asked whether they hold the answer.
- A turn the person asked not to keep (in words the private-chat reader missed) was stored with its chat and quoted
  back in another chat. A judged refusal now makes the rest of the turn private.
- A personal question was picked Run and answered with a folder listing. A Run or file-writing pick now stands only
  on a yes/no of its own.
Wordings drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, persistent_memory, plugin_ask, private_chat
from core.semantic.semantic_admissions import bound_request_context

QUESTIONS = ("what kind of bird does Daiva have?", "kokį šunį turi Rimas?", "wie heißt die Katze von Jonas?",
             "what's my aunt's tortoise called?")


@pytest.fixture
def judged_recall(monkeypatch):
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda _t: "recall")
    monkeypatch.setattr(plugin_ask, "judged_tool_ask", lambda _t: False)
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns", lambda _s: ["what's Paweł's dog called?"])


@pytest.mark.parametrize("seed", range(4))
def test_a_recall_the_chat_never_answered_is_not_saved_never_a_guess(seed, judged_recall, monkeypatch) -> None:
    question = random.Random(f"q-{seed}").choice(QUESTIONS)
    asked: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_answered_in_chat",
                        lambda q, earlier, **_k: asked.append((q, list(earlier))) or "no")
    assert persistent_memory._judged_recall_with_nothing_behind_it(question, session_id="chat-b") == (
        persistent_memory.JUDGED_RECALL_NOTHING)
    assert asked == [(question, ["what's Paweł's dog called?"])]


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_chat_that_may_hold_the_answer_is_left_to_the_model(verdict, judged_recall, monkeypatch) -> None:
    monkeypatch.setattr(intent_arbiter, "arbitrate_answered_in_chat", lambda *_a, **_k: verdict)
    assert persistent_memory._judged_recall_with_nothing_behind_it(QUESTIONS[0], session_id="chat-b") is None


def test_the_question_carries_the_earlier_messages() -> None:
    prompt = intent_arbiter._answered_in_chat_prompt("what kind of bird does Daiva have?", ["what's Paweł's dog called?"])
    assert "what's Paweł's dog called?" in prompt and "Daiva" in prompt


@pytest.mark.parametrize("seed", range(3))
def test_a_judged_refusal_makes_the_rest_of_the_turn_private(seed, monkeypatch) -> None:
    rng = random.Random(f"refuse-{seed}")
    text = rng.choice(("mano treneris Tomas turi kurtą, bet šito neišsaugok",
                       "mein Kollege hat einen Hund namens Rex, das aber bitte nicht speichern",
                       "my running buddy walks a greyhound, keep that to yourself"))
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "no")
    request = f"req-refuse-{seed}"
    context: dict = {}
    with bound_request_context(request):
        assert persistent_memory._keep_not_wanted(text, session_id="chat-a", source_context=context) is True
        assert context.get(private_chat.PRIVATE_TURN_KEY) is True
        assert private_chat.turn_is_private({}, "chat-a")
        assert not private_chat.is_private("chat-a"), "the chat itself is not switched"
    private_chat.end_private_request(request)


def test_a_keep_that_is_wanted_leaves_the_turn_as_it_was(monkeypatch) -> None:
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "yes")
    context: dict = {}
    with bound_request_context("req-wanted"):
        assert persistent_memory._keep_not_wanted("įsimink: mano dėdė augina bites", session_id="chat-a",
                                                  source_context=context) is False
        assert private_chat.PRIVATE_TURN_KEY not in context and not private_chat.request_is_private()


@pytest.mark.parametrize(("key", "verdict", "kept"), [("run", "no", ()), ("files", "no", ()), ("run", "yes", ("run",)),
                                                       ("run", None, ("run",)), ("find", "no", ("find",))])
def test_a_pick_that_runs_or_writes_stands_on_its_own_yes(key, verdict, kept, monkeypatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_choice_ask", lambda t, c, **_k: asked.append(c) or verdict)
    assert plugin_ask._actions_confirmed("what is the name of my dentist's cat?", (key,), {}, "chat-1") == kept
    assert bool(asked) == (key in {"run", "files", "contacts"})


@pytest.mark.parametrize("seed", range(3))
def test_a_reminders_pick_with_no_memory_reading_is_confirmed_too(seed, monkeypatch) -> None:
    text = random.Random(f"rem-{seed}").choice((
        "by the way my climbing partner keeps a ferret, don't save that part",
        "beje, mano kolegė augina šinšilą", "übrigens hat meine Schwester ein Pony"))
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(intent_arbiter, "arbitrate_reminder_ask", lambda *_a, **_k: "no")
    assert plugin_ask._memory_kind_confirmed(text, ("reminders",), {}, "chat-1") == ()
