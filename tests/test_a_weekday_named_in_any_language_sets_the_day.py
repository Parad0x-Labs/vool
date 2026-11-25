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
