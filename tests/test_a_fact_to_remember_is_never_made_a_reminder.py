"""A fact to remember is never turned into a reminder because the tool judgement picked Reminders.

Gate iMac on 3c92e8df3 (qwen3:8b, from source): "zapamiętaj: mój szwagier ma psa o imieniu Burek" ("remember: my
brother-in-law has a dog named Burek") and "my godson Jonas has a goldfish called Bulvė" were both picked Reminders by
the tool judgement while the memory question read them as a keep. The model wrote a reminder out of the fact, raised an
approval for it, and the Polish fact was not kept. When the two readings disagree, a yes/no of its own decides; no
answer leaves the pick as it was. Wordings drawn at random from several languages.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, plugin_ask

FACTS = (
    "zapamiętaj: mój szwagier ma psa o imieniu Burek",
    "my godson Jonas has a goldfish called Bulvė",
    "įsimink, mano pusbrolio šuo vardu Rudis",
    "merk dir, meine Tante hat eine Katze namens Mimi",
    "remember my aunt's parrot is called Kiwi",
)


@pytest.fixture
def readings(monkeypatch):
    asked: list[str] = []

    def setup(kind: str | None, reminder: str | None):
        monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)

        def reminder_ask(text, **_k):
            asked.append(text)
            return reminder
        monkeypatch.setattr(intent_arbiter, "arbitrate_reminder_ask", reminder_ask)
        monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "no")
        monkeypatch.setattr(plugin_ask, "_continues_a_contact_save", lambda *_a, **_k: False)
        return asked
    return setup


@pytest.mark.parametrize("seed", range(6))
def test_a_reminders_pick_beside_a_keep_reading_is_dropped_when_no_reminder_was_asked(seed, readings) -> None:
    text = random.Random(f"fact-{seed}").choice(FACTS)
    asked = readings("keep", "no")
    assert plugin_ask._memory_kind_confirmed(text, ("reminders",), {}, "chat-1") == ("memory_keep",)
    assert asked == [text]


def test_a_reminder_asked_for_keeps_its_tool(readings, monkeypatch) -> None:
    asked = readings("keep", "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no")
    text = "remember to remind me tomorrow at 9 to feed Burek"
    assert plugin_ask._memory_kind_confirmed(text, ("reminders",), {}, "chat-1") == ("reminders",)
    assert asked == [text]


DATED_FACTS = (
    "just so you know, my goddaughter Ieva has her ballet exam on 21 february at 10:40",
    "beje, mano kaimyno sūnaus krikštynos kovo 7 d. 12 val.",
    "nur zur Info: die Hochzeit meiner Cousine ist am 4. Juli um 15 Uhr",
)


@pytest.mark.parametrize("text", DATED_FACTS)
def test_a_dated_fact_the_reminder_question_reads_yes_beside_a_keep_is_kept_when_it_is_told(text, readings,
                                                                                            monkeypatch) -> None:
    """iMac gate57 on 21d4d4046 (qwen3:8b): a dated fact told with no ask was read keep by the memory question and
    remind by the reminder question; a reminder was set with no card and nothing was kept. The told-as-fact question
    settles the two readings: told, it is a keep; an order (told no, or no answer) keeps the reminder."""
    readings("keep", "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "yes")
    assert plugin_ask._memory_kind_confirmed(text, ("reminders",), {}, "chat-1") == ("memory_keep",)
    for told in ("no", None):
        monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, _t=told, **_k: _t)
        assert plugin_ask._memory_kind_confirmed(text, ("reminders",), {}, "chat-1") == ("reminders",)


def test_no_answer_leaves_the_pick_as_it_was(readings) -> None:
    readings("keep", None)
    assert plugin_ask._memory_kind_confirmed(FACTS[0], ("reminders",), {}, "chat-1") == ("reminders",)


def test_a_reminders_pick_without_a_keep_reading_is_confirmed_by_its_own_yes(readings) -> None:
    asked = readings(None, "yes")
    assert plugin_ask._memory_kind_confirmed("show my reminders", ("reminders",), {}, "chat-1") == ("reminders",)
    assert asked == ["show my reminders"]


def test_the_question_is_asked_of_the_turns_model(monkeypatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda _ctx: "")
    monkeypatch.setattr(intent_arbiter, "_judged_for_the_turn", lambda p, _c, parse=None: prompts.append(p) or "no")
    assert intent_arbiter.arbitrate_reminder_ask(FACTS[0]) == "no"
    assert len(prompts) == 1 and FACTS[0] in prompts[0]


# iMac gate on 95fae99a0: "zapamiętaj proszę: ..." was judged keep by the memory question alone and not kept, and a
# German reminder delete ("Erinnerung" is also "memory") was judged a memory forget while a reminder was pending.
KEEPS = ("zapamiętaj: mój kolega Marek ma papugę Kiwi", "įsimink: mano dėdės ožka vardu Baltė",
         "merk dir bitte, meine Nichte heißt Lotte")
REMINDER_ORDERS = ("entferne meine Erinnerung", "zeig mir meine Erinnerungen", "atšauk priminimą")


@pytest.mark.parametrize("seed", range(4))
def test_a_keep_read_by_the_memory_question_alone_is_kept_when_the_message_tells_a_fact(seed, readings, monkeypatch):
    text = random.Random(f"keep-{seed}").choice(KEEPS)
    readings("keep", None)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "yes")
    assert plugin_ask._memory_kind_confirmed(text, (), {}, "chat-1") == ("memory_keep",)


def test_a_keep_read_alone_is_not_kept_when_the_message_tells_no_fact(readings, monkeypatch) -> None:
    readings("keep", None)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "yes")
    assert plugin_ask._memory_kind_confirmed("pamirsk ka pasakojau apie valti", (), {}, "chat-1") == ()


@pytest.mark.parametrize("kind", ["forget", "recall"])
@pytest.mark.parametrize("seed", range(3))
def test_a_memory_reading_of_a_reminder_order_goes_to_the_reminders_while_one_is_pending(kind, seed, readings,
                                                                                          monkeypatch) -> None:
    text = random.Random(f"order-{seed}").choice(REMINDER_ORDERS)
    asked = readings(kind, "yes")
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: True)
    assert plugin_ask._memory_kind_confirmed(text, (), {}, "chat-1") == ("reminders",)
    assert asked == [text]


def test_with_no_reminder_pending_a_forget_is_not_asked_about_reminders(readings, monkeypatch) -> None:
    asked = readings("forget", "yes")
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)
    assert "reminders" not in plugin_ask._memory_kind_confirmed("vergiss mein Fahrrad", (), {}, "chat-1")
    assert asked == []
