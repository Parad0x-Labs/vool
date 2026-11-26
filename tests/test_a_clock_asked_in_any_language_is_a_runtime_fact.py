"""The time in a named place is a runtime fact, asked in any language.

iMac gate23 on 97d3db22e (qwen3:8b, Web lookups off): "Wie spät ist es gerade in Sydney?" never reached the date and
time lane, whose readers know English clock phrasings only; the turn's judge picked the web, and the web-off line was
the whole answer. The web question now says the clock is not the internet, and the turn's facts carry the time in a
place the message names, so the model answers any wording from the clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.parametrize("ask, place, wall", [
    ("Quelle heure est-il à Tokyo en ce moment ?", "Tokyo", "07:40"),
    ("Hoe laat is het nu in New York?", "New York", "17:40"),
    ("Która jest teraz godzina w Sydney?", "Sydney", "09:40"),
])
def test_the_time_in_a_named_place_is_a_fact_of_the_turn(ask, place, wall):
    from core.prompt_normalizer import _named_place_times_text

    line = _named_place_times_text(ask, datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc))
    assert place in line and wall in line, line


def test_a_message_naming_no_place_adds_nothing():
    from core.prompt_normalizer import _named_place_times_text

    assert _named_place_times_text("could you sum up my notes?", datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc)) == ""


def test_the_web_question_says_the_clock_is_not_the_internet():
    from core.intent_arbiter import _web_ask_prompt

    prompt = _web_ask_prompt("Zeit in Kairo?")
    assert "the computer's clock gives it" in prompt


@pytest.mark.parametrize("said", ["we are off over the easter weekend", "my friend Sofia has an oral exam", "wake me at 7"])
def test_a_place_name_that_is_an_everyday_word_adds_nothing(said):
    from core.prompt_normalizer import _named_place_times_text

    assert _named_place_times_text(said, datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc)) == ""


def test_the_clock_question_reads_back_a_zone_the_database_knows():
    from core.intent_arbiter import _clock_ask_reader

    assert _clock_ask_reader('{"answer": "yes", "zone": "America/Sao_Paulo"}') == "yes|America/Sao_Paulo"
    assert _clock_ask_reader('{"answer": "yes", "zone": "Middle/Earth"}') == "yes"
    assert _clock_ask_reader('{"answer": "no"}') == "no"


def _web_verdict(monkeypatch, text, *, web_on, clock):
    from core import intent_arbiter, online_asks, remote_fetch_policy

    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *a, **k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_clock_ask", lambda *a, **k: asked.append(a) or clock)
    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *a, **k: web_on)
    online_asks._WEB_VERDICTS.clear()
    online_asks._CLOCK_ZONES.clear()
    return online_asks.web_ask_verdict(text), asked


def test_a_clock_ask_in_a_place_named_in_its_own_language_is_not_a_web_ask(monkeypatch):
    """iMac gate24 on a1cf06d6d: "Que horas são agora em Lisboa?" (Web lookups off) was refused as a web lookup;
    "Lisboa" is not a name the time zone database holds, so the judge names the zone and the turn's facts carry it."""
    from core.online_asks import judged_clock_zone
    from core.prompt_normalizer import _named_place_times_text

    ask = "A que horas fecha o dia em Montevidéu agora?"
    verdict, asked = _web_verdict(monkeypatch, ask, web_on=False, clock="yes|America/Montevideo")
    assert verdict == "no" and asked
    assert judged_clock_zone(ask) == "America/Montevideo"
    line = _named_place_times_text(ask, datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc))
    assert "Montevideo" in line and "20:00" in line, line


def test_anything_else_about_a_place_stays_a_web_ask(monkeypatch):
    verdict, asked = _web_verdict(monkeypatch, "how many tourists visit Prague a year?", web_on=True, clock="no")
    assert verdict == "yes" and asked   # Prague is named: asked, and its no keeps the web reading


def test_with_the_web_on_and_no_known_place_the_clock_is_not_asked(monkeypatch):
    verdict, asked = _web_verdict(monkeypatch, "what is in the headlines this evening?", web_on=True, clock="yes")
    assert verdict == "yes" and not asked


def test_the_web_question_reads_back_a_zone_with_its_no():
    from core.intent_arbiter import _web_ask_reader

    assert _web_ask_reader('{"answer": "no", "zone": "America/New_York"}') == "no|America/New_York"
    assert _web_ask_reader('{"answer": "no", "zone": "Nowhere/Town"}') == "no"
    assert _web_ask_reader('{"answer": "yes", "zone": "Europe/Oslo"}') == "yes"


def test_a_clock_ask_the_web_question_answers_no_still_carries_the_time_there(monkeypatch):
    """iMac gate25 on cbcb090a4 (Web lookups off): "Która godzina jest teraz w Nowym Jorku?" was read "no" for the web,
    rightly, so the clock question was never asked, and the city named in Polish gave the turn no time there: the
    model wrote the computer's own time with a New York label. The web question's "no" names the zone itself."""
    from core import intent_arbiter, online_asks
    from core.prompt_normalizer import _named_place_times_text

    ask = "Kolik je teď hodin v Novém Yorku?"
    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *a, **k: "no|America/New_York")
    monkeypatch.setattr(intent_arbiter, "arbitrate_clock_ask", lambda *a, **k: asked.append(a) or "no")
    online_asks._WEB_VERDICTS.clear()
    online_asks._CLOCK_ZONES.clear()
    assert online_asks.web_ask_verdict(ask) == "no"
    assert not asked
    line = _named_place_times_text(ask, datetime(2026, 11, 25, 23, 42, tzinfo=timezone.utc))
    assert "New York:" in line and "18:42" in line and "Wednesday" in line, line


def _picked_turn(monkeypatch, ask, picks, verdict):
    from core import intent_arbiter, online_asks, plugin_ask

    asked = []
    plugin_ask.forget()
    online_asks._WEB_VERDICTS.clear()
    online_asks._CLOCK_ZONES.clear()
    if picks:
        plugin_ask._record_first_picks(ask, picks)
    monkeypatch.setattr(intent_arbiter, "_turn_key", lambda ctx: "turn-clock-1" if isinstance(ctx, dict) else "")
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *a, **k: asked.append(a) or verdict)
    return asked


def test_a_clock_ask_the_judge_picked_another_tool_for_still_carries_the_time_there(monkeypatch):
    """iMac gate26 on 11e7a9468 (Web lookups off): "Cât e ceasul acum la Viena?" was picked as a Reminders ask, the pick
    was dropped, and the web question ran only after the answer: no time in Vienna reached the model, which gave the
    computer's own time. A turn the judge picked a tool for is put the web question before the answer."""
    from core.prompt_normalizer import _named_place_times_text

    ask = "Ce oră e acum la Viena, te rog?"
    asked = _picked_turn(monkeypatch, ask, ("reminders",), "no|Europe/Vienna")
    line = _named_place_times_text(ask, datetime(2026, 11, 26, 0, 44, tzinfo=timezone.utc), {"turn_id": "t"})
    assert asked and "Vienna:" in line and "01:44" in line and "Thursday" in line, line


def test_with_the_web_on_a_message_the_judge_picked_nothing_for_is_not_asked_the_web_question(monkeypatch):
    from core import remote_fetch_policy
    from core.prompt_normalizer import _named_place_times_text

    ask = "povesteste-mi ceva despre Viena"
    asked = _picked_turn(monkeypatch, ask, (), "no|Europe/Vienna")
    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *a, **k: True)
    assert _named_place_times_text(ask, datetime(2026, 11, 26, 0, 44, tzinfo=timezone.utc), {"turn_id": "t"}) == ""
    assert not asked


def test_with_the_web_off_a_clock_ask_the_judge_picked_nothing_for_still_carries_the_time_there(monkeypatch):
    """iMac gate27 on 601705f2c (Web lookups off): "Paljonko kello on nyt Tukholmassa?" was picked no tool, the web
    question ran only after the answer (the out-of-date check asks it anyway), and the reply gave the computer's own
    time as Stockholm's. With the web off the question is asked before the answer, once for the turn."""
    from core import remote_fetch_policy
    from core.prompt_normalizer import _named_place_times_text

    ask = "Mikä on kellonaika nyt Oslossa?"
    asked = _picked_turn(monkeypatch, ask, (), "no|Europe/Oslo")
    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *a, **k: False)
    line = _named_place_times_text(ask, datetime(2026, 11, 26, 2, 7, tzinfo=timezone.utc), {"turn_id": "t"})
    assert len(asked) == 1 and "Oslo:" in line and "03:07" in line, line


def test_a_clock_ask_is_not_answered_as_a_recall(monkeypatch):
    """iMac gate28 on d2262de22: "Mis kell on praegu Varssavis?" was read as a clock ask by the turn's clock question
    and as a recall by the memory question; the memory lane answered "I don't have that saved". The clock answers it."""
    from core import intent_arbiter, online_asks, plugin_ask

    ask = "Kiek dabar valandų Varšuvoje?"
    online_asks._CLOCK_ZONES.clear()
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *a, **k: "recall")
    for other in ("arbitrate_forget_order", "arbitrate_told_as_fact", "arbitrate_reminder_ask"):
        monkeypatch.setattr(intent_arbiter, other, lambda *a, **k: None)
    assert plugin_ask._memory_kind_confirmed(ask, ("memory_recall",), {}, "chat-clock") == ("memory_recall",)
    online_asks._hold_clock_zone(ask, "Europe/Warsaw")
    assert plugin_ask._memory_kind_confirmed(ask, ("memory_recall",), {}, "chat-clock") == ()
    online_asks._CLOCK_ZONES.clear()
