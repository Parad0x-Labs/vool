"""A pick no question of its own confirms stops the keep of a fact told beside it only when its own yes/no confirms it.

Memory wiring audit of efb8e474c (B2): only Run, Files and Contacts (and Reminders, Calendar, Find and the web, each
with its own question) were checked. A dated fact the tool judgement read as Notes, Mail or My things kept nothing and
handed the model a tool. On a message that tells a fact those picks now go to the shared yes/no; a pick it turns down
leaves the message as one keep reading, which the told-as-fact and forget questions settle. A message that tells no
fact keeps its pick unasked, as before. All names and wordings here are invented for this contract."""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask

_FACT = ("btw the plumber said our boiler is from 2014", "beje, mano sesers šuo Rikis jau trylikos metų",
         "übrigens hat mein Schwager im März Geburtstag")


def _judges(monkeypatch, *, told, choice):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: asked.append("told") or told)
    monkeypatch.setattr(intent_arbiter, "arbitrate_choice_ask",
                        lambda _t, description, **_k: asked.append(description.split(":")[0]) or choice)
    return asked


@pytest.mark.parametrize("text", _FACT)
@pytest.mark.parametrize("pick", sorted(plugin_ask._CHOICES_CONFIRMED_BESIDE_A_TOLD_FACT))
def test_a_pick_beside_a_told_fact_that_its_yes_no_turns_down_is_dropped(text, pick, monkeypatch):
    asked = _judges(monkeypatch, told="yes", choice="no")
    assert plugin_ask._actions_confirmed(text, (pick,), {}, "chat-1") == ()
    assert asked[0] == "told" and len(asked) == 2


@pytest.mark.parametrize("pick", sorted(plugin_ask._CHOICES_CONFIRMED_BESIDE_A_TOLD_FACT))
def test_beside_a_told_fact_only_a_yes_keeps_the_pick(pick, monkeypatch):
    _judges(monkeypatch, told="yes", choice="yes")
    assert plugin_ask._actions_confirmed(_FACT[0], (pick,), {}, "chat-1") == (pick,)
    _judges(monkeypatch, told="yes", choice=None)
    assert plugin_ask._actions_confirmed(_FACT[0], (pick,), {}, "chat-1") == ()


@pytest.mark.parametrize("told", ["no", None])
def test_a_message_that_tells_no_fact_keeps_its_pick_unasked(told, monkeypatch):
    asked = _judges(monkeypatch, told=told, choice="no")
    assert plugin_ask._actions_confirmed("find my notes about the boiler", ("notes", "things"), {}, "c") == (
        "notes", "things")
    assert asked == ["told"]  # asked once for the whole message


def test_the_told_as_fact_question_is_not_asked_for_picks_with_a_question_of_their_own(monkeypatch):
    asked = _judges(monkeypatch, told="yes", choice="yes")
    assert plugin_ask._actions_confirmed(_FACT[0], ("run", "web"), {}, "c") == ("run", "web")
    assert "told" not in asked


def _readings(monkeypatch, *, kind, told, forget="no"):
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: told)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: forget)
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)


@pytest.mark.parametrize("text", _FACT)
def test_a_fact_whose_only_pick_was_turned_down_is_kept(text, monkeypatch):
    _readings(monkeypatch, kind="none", told="yes")
    assert plugin_ask._memory_kind_confirmed(text, (), {}, "chat-1", dropped_a_pick=True) == ("memory_keep",)
    # Without a dropped pick nothing new happens: no reading named a keep.
    assert plugin_ask._memory_kind_confirmed(text, (), {}, "chat-1") == ()


def test_a_turned_down_pick_never_turns_a_recall_or_a_non_fact_into_a_keep(monkeypatch):
    _readings(monkeypatch, kind="recall", told="yes")
    assert "memory_keep" not in plugin_ask._memory_kind_confirmed("kiek metų Rikiui?", (), {}, "c", dropped_a_pick=True)
    _readings(monkeypatch, kind="none", told="no")
    assert plugin_ask._memory_kind_confirmed("what's a good boiler brand?", (), {}, "c", dropped_a_pick=True) == ()


_DATED = ("my sister lands at 14:40 on the 9th, flight QX 418", "the chimney sweep is booked for 21 february",
          "mano brolio vestuvės birželio 14 d. 15 val.", "der TÜV-Termin für mein Auto ist am 3. Mai um 8:30")


def _tool_readings(monkeypatch, *, kind, told, reminder=None, calendar=None):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: asked.append("told") or told)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_reminder_ask", lambda *_a, **_k: asked.append("reminder") or reminder)
    monkeypatch.setattr(intent_arbiter, "arbitrate_calendar_ask", lambda *_a, **_k: asked.append("calendar") or calendar)
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)
    return asked


@pytest.mark.parametrize("text", _DATED)
@pytest.mark.parametrize(("pick", "verdict"), [("reminders", "no"), ("reminders", None), ("calendar", "no"),
                                               ("calendar", None)])
def test_a_dated_fact_read_as_a_reminder_or_calendar_pick_is_kept_with_no_tool(text, pick, verdict, monkeypatch):
    """Benchmark of a6a5f29b7 and efb8e474c: 7 of 8 Manual-mode approval cards on stated facts were
    operator.schedule_reminder or check_availability. A date or time in a fact is no ask to be reminded."""
    _tool_readings(monkeypatch, kind="none", told="yes", **{pick.removesuffix("s"): verdict})
    assert plugin_ask._memory_kind_confirmed(text, (pick,), {}, "chat-1") == ("memory_keep",)


@pytest.mark.parametrize(("pick", "verdict"), [("reminders", "yes"), ("calendar", "yes")])
def test_a_reminder_or_calendar_ask_its_own_question_confirms_stands(pick, verdict, monkeypatch):
    _tool_readings(monkeypatch, kind="none", told="no", **{pick.removesuffix("s"): verdict})
    assert plugin_ask._memory_kind_confirmed("remind me on the 9th at 14:00 to pick up my sister", (pick,), {},
                                             "c") == (pick,)


@pytest.mark.parametrize("pick", ["reminders", "calendar"])
def test_no_answer_on_an_ask_that_tells_no_fact_leaves_the_pick(pick, monkeypatch):
    _tool_readings(monkeypatch, kind="none", told="no")
    assert plugin_ask._memory_kind_confirmed("set something for the 9th at 14:00", (pick,), {}, "c") == (pick,)
