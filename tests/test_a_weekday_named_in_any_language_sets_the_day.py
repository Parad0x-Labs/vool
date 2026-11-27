"""A reminder whose call left the day out lands on the weekday the request names, in any of VOOL's languages.

iMac gate on 88337f6b8 (qwen3:8b, from source, a Wednesday): "primink penktadienį 17 val. paimti siuntinį iš
paštomato" (Friday) went out with due_iso Thursday 26 November and no "day", and was stored on Thursday; the run before,
"šeštadienį" (Saturday) the same. The call's own "day" is read first; without it the request's weekday is. Words,
days and hours are drawn at random; no model and no network.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from core.operator.when import align_to_weekday, weekday_named_in

SAID = {
    0: ("primink pirmadienį {h} val. {what}", "erinnere mich am Montag um {h} Uhr an {what}", "lundi à {h}h {what}"),
    1: ("antradienį {h} val. {what}", "recuérdame el martes a las {h} {what}", "w wtorek o {h} {what}"),
    2: ("trečiadienį {h} val. {what}", "напомни в среду в {h} {what}", "mercoledì alle {h} {what}"),
    3: ("ketvirtadienį {h} val. {what}", "am Donnerstag um {h} {what}", "jeudi à {h}h {what}"),
    4: ("primink penktadienį {h} val. {what}", "напомни в пятницу в {h} {what}", "przypomnij w piątek o {h} {what}"),
    5: ("priminimas: šeštadienį {h} val. {what}", "le samedi à {h}h {what}", "cumartesi saat {h} {what}"),
    6: ("sekmadienį {h} val. {what}", "erinnere mich am Sonntag um {h} Uhr {what}", "星期日 {h}点 {what}"),
}
WHAT = ("paimti siuntinį", "pay the internet", "die Oma anrufen", "llamar al fontanero")


@pytest.mark.parametrize("seed", range(14))
def test_the_named_weekday_moves_a_miscounted_date(seed) -> None:
    rng = random.Random(f"weekday-{seed}")
    weekday = seed % 7
    text = rng.choice(SAID[weekday]).format(h=rng.randint(8, 20), what=rng.choice(WHAT))
    today = date(2026, 11, 25) + timedelta(days=rng.randint(0, 6))
    miscounted = today + timedelta(days=rng.randint(1, 6))
    if miscounted.weekday() == weekday:
        miscounted += timedelta(days=1)

    assert weekday_named_in(text) == weekday, text
    aligned = align_to_weekday(f"{miscounted.isoformat()}T17:00", weekday, today)
    assert date.fromisoformat(aligned[:10]).weekday() == weekday and aligned.endswith("T17:00")
    assert 0 < (date.fromisoformat(aligned[:10]) - today).days <= 7


@pytest.mark.parametrize("text", [
    "remind me tomorrow at 7 to take the bins out", "primink rytoj 9 val. paskambinti mamai", "in 2 Stunden erinnern",
    "remind me on monday about the friday meeting", "czwarty raz przypominam", "Lukas Berger +49 151 1234567",
])
def test_no_weekday_or_two_weekdays_leave_the_date_alone(text) -> None:
    assert weekday_named_in(text) is None


def test_the_handler_reads_the_requests_weekday_when_the_call_left_it_out(monkeypatch) -> None:
    import core.operator.handlers as handlers
    from core.operator.models import OperatorActionIntent

    monkeypatch.setattr(handlers, "_owner_today", lambda: date(2026, 11, 25))
    seen: list[str] = []
    intent = OperatorActionIntent(kind="schedule_reminder", raw_text="primink penktadienį 17 val. paimti siuntinį",
                                  due_iso="2026-11-26T17:00")
    handlers._typed_reminder_when(intent, lambda words: seen.append(words) or words)
    typed_day = handlers._typed_reminder_when(
        OperatorActionIntent(kind="schedule_reminder", raw_text="primink penktadienį 17 val.", due_iso="2026-11-26T17:00",
                             due_weekday="thursday"), lambda words: seen.append(words) or words)

    # The request's Friday moves the miscounted Thursday; a day the call typed itself is read first.
    assert seen[0].startswith("2026-11-27") and seen[1].startswith("2026-11-26"), seen
    assert typed_day is not None


_SPOKEN = {
    0: ("pirmadienį {h}:30 turiu pasiimti siuntinį, neleisk pamiršti", "am Montag um {h}:30 Zahnarzt anrufen"),
    1: ("antradienį {h}:15 sumokėti už internetą", "Dienstag {h}:15 Reifen wechseln lassen"),
    3: ("ketvirtadienį {h}:45 nunešti batus taisyti", "am Donnerstag um {h}:45 die Oma besuchen"),
    6: ("sekmadienį {h}:00 palaistyti gėles", "Sonntag {h}:00 Brötchen holen"),
}


@pytest.mark.parametrize("seed", range(12))
def test_a_weekday_the_reader_does_not_know_moves_the_spoken_reading(seed, monkeypatch) -> None:
    """The person's own words outrank the model's typed time, but the when-reader reads English day words only:
    iMac ace1c42cc, on a Friday, "pirmadienį 18:30 turiu pasiimti siuntinį iš paštomato, neleisk pamiršti" was set for
    Saturday 18:30, the next 18:30. A day the reader did read ("tomorrow") stands, and so does a clock with no day."""
    from datetime import datetime, timezone

    import core.operator.handlers as handlers
    from core.operator.when import parse_when_expression

    rng = random.Random(f"spoken-weekday-{seed}")
    today = date(2026, 11, 23) + timedelta(days=rng.randint(0, 6))
    now = datetime(today.year, today.month, today.day, 21, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(handlers, "_owner_today", lambda: today)

    def read(words: str):
        return parse_when_expression(words, now_fn=lambda: now)

    weekday = rng.choice(sorted(_SPOKEN))
    hour = rng.randint(7, 19)
    text = rng.choice(_SPOKEN[weekday]).format(h=hour)
    when, problem = handlers._reminder_when(text, read)
    assert not problem and when.ok, (text, when)
    landed = date.fromisoformat(str(when.due_wall)[:10])
    assert landed.weekday() == weekday and today < landed <= today + timedelta(days=7), (text, today, when.due_wall)
    assert str(when.due_wall)[11:13] == f"{hour:02d}", (text, when.due_wall)

    tomorrow, _ = handlers._reminder_when(f"tomorrow at {hour} call about the {DAY_NAMES[weekday]} meeting", read)
    assert str(tomorrow.due_wall)[:10] == (today + timedelta(days=1)).isoformat(), tomorrow.due_wall
    alone, _ = handlers._reminder_when(f"at {hour}:30 call mum", read)
    assert str(alone.due_wall)[:10] == (today + timedelta(days=1)).isoformat(), alone.due_wall


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
