"""A message that asks to find a file on this computer is not answered with a saved fact.

Mac gate on 608294d38 (2026-11-24, a chat bound to a project folder holding atostogos-892.txt): "Um ar turiu failą,
vadinamą „atostogos-892“? surask jį?" ("do I have a file called ...? find it?") was answered "Jūs man sakėte: my boss's
dog is Waffles" in 10 s, and no file was looked for. Measured on qwen3:8b: the tool judgement read it (and five more
file asks in LT/EN/DE) as Find, the memory question read them as a recall, a recall stands beside any tool pick, and the
recall judge named an unrelated fact. The same tool judgement also read real recalls as file asks ("where did i say i
keep my spare keys?" -> run), so neither reading may simply win.

Here the judges are scripted (their answers are what qwen3:8b gave on the probe); the messages, facts and languages
are drawn at random.
"""
from __future__ import annotations

import random

import pytest

import core.intent_arbiter as intent_arbiter
from core import persistent_memory, plugin_ask

FILE_ASKS = (
    "Um ar turiu failą, vadinamą „{name}“? surask jį?",
    "gal žinai kur yra failas {name}.txt? rask",
    "pls find the file called {name} for me",
    "hast du irgendwo eine Datei namens {name}? such sie mal",
    "do i have a doc named {name} somewhere?",
    "dónde guardé el pdf {name}",
)
NAMES = ("atostogos-892", "kelione-2027", "invoice-4471", "urlaub-plan", "budget_q3", "factura-339", "vestuves-foto")
UNRELATED = ("my boss's dog is Waffles", "mano kuprinė žalia", "meine Schwester wohnt in Graz", "my locker is 41")
RECALLS = (
    ("where did i say i keep my spare keys?", "my spare keys are under the blue flowerpot"),
    ("wo hab ich nochmal gesagt dass mein Reisepass liegt?", "mein Reisepass liegt in der Schreibtischschublade"),
    ("ar pamenu kur padėjau pasą? sakiau tau", "pasą padėjau į spintelę prie lovos"),
)


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def _judge(monkeypatch, answer):
    asked: list[str] = []

    def file_ask(message, **_kw):
        asked.append(message)
        return answer

    monkeypatch.setattr(intent_arbiter, "arbitrate_file_ask", file_ask)
    return asked


@pytest.mark.parametrize("seed", range(8))
def test_a_file_ask_beside_a_recall_of_an_unrelated_fact_goes_to_the_file_tool(seed, monkeypatch) -> None:
    rng = random.Random(f"file-ask-{seed}")
    message = rng.choice(FILE_ASKS).format(name=rng.choice(NAMES))
    plugin_ask._record_builtins(message, (rng.choice(["find", "read"]), "memory_recall"))
    asked = _judge(monkeypatch, "yes")
    named = rng.sample(UNRELATED, rng.randint(1, 2))

    assert not persistent_memory._recall_beside_a_file_pick(message, named, session_id="chat", source_context={})
    assert asked == [message]


@pytest.mark.parametrize("seed", range(6))
def test_a_recall_the_tool_judgement_misread_as_a_file_ask_keeps_its_answer(seed, monkeypatch) -> None:
    rng = random.Random(f"recall-misread-{seed}")
    question, fact = rng.choice(RECALLS)
    plugin_ask._record_builtins(question, (rng.choice(["run", "find"]), "memory_recall"))
    # A fact that shares a word with the question stands without asking anything more.
    asked = _judge(monkeypatch, "yes")
    assert persistent_memory._recall_beside_a_file_pick(question, [fact], session_id="chat", source_context={})
    assert asked == []
    # A fact told in another language shares none: the file-ask question decides, and its "no" keeps the recall.
    other = rng.choice([text for text in UNRELATED if text not in fact])
    asked = _judge(monkeypatch, rng.choice(["no", None]))
    assert persistent_memory._recall_beside_a_file_pick(question, [other], session_id="chat", source_context={})
    assert asked == [question]


def test_a_recall_beside_no_file_pick_is_not_questioned(monkeypatch) -> None:
    rng = random.Random("no-file-pick")
    question, _fact = rng.choice(RECALLS)
    plugin_ask._record_builtins(question, (rng.choice(["contacts", "things"]), "memory_recall"))
    asked = _judge(monkeypatch, "yes")
    assert persistent_memory._recall_beside_a_file_pick(question, list(UNRELATED[:1]), session_id="chat",
                                                         source_context={})
    assert asked == []


def test_the_file_ask_question_is_built_from_the_message_only() -> None:
    message = random.choice(FILE_ASKS).format(name=random.choice(NAMES))
    prompt = intent_arbiter._file_ask_prompt(message)
    assert prompt.count(message) == 1 and prompt.rstrip().endswith('{"answer": "no"}')


@pytest.mark.parametrize("seed", range(4))
def test_a_file_ask_read_as_everything_remembered_is_handed_on(seed, monkeypatch) -> None:
    """Reproduced from source on qwen3:8b: the recall judge named nothing for a Lithuanian file ask, the whole-memory
    question read it as "everything you remember", and every held fact was the answer."""
    rng = random.Random(f"whole-memory-{seed}")
    message = rng.choice(FILE_ASKS).format(name=rng.choice(NAMES))
    held = rng.sample(UNRELATED, 3)
    plugin_ask._record_builtins(message, ("find", "memory_recall"))
    _judge(monkeypatch, "yes")
    monkeypatch.setattr(persistent_memory, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(persistent_memory, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(persistent_memory, "_judged_forgotten_named", lambda *_a, **_k: [])
    monkeypatch.setattr(persistent_memory, "_judged_items_named", lambda *_a, **_k: [])
    monkeypatch.setattr(persistent_memory, "_asks_for_the_whole_memory", lambda *_a, **_k: True)

    assert persistent_memory._judged_recall_reply(message, session_id="chat", policy=None, source_context={}) is None

    # The same "everything" read of a message with no file pick still shows what is held.
    plugin_ask.forget()
    plugin_ask._record_builtins(message, ("memory_recall",))
    shown = persistent_memory._judged_recall_reply(message, session_id="chat", policy=None, source_context={})
    assert shown and all(fact in shown for fact in held), shown
