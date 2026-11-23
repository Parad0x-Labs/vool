"""A reminder for a named weekday lands on that weekday, whatever date the model typed, in any language.

Mac gate on f3771b6b7 and the fresh-question audit on 02ff11496 (both on a Monday, qwen3:8b): "this Friday 4pm" was
typed as Thursday 26 Nov and "Saturday" as Friday 27, with the coming dates listed beside the clock. The reminder tools
now take the day the person named (in English, which a model gives for "penktadienį", "Freitag" or "viernes" alike)
and work its date out. A typed time that names its zone in words ("...16:00 UTC", as VOOL's own list shows times) is
read as that offset instead of being refused with the reader's raw error.

In process, like tests/test_reminders_typed_in_any_language.py. Days, times and languages are drawn at random.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest

from core.operator.when import align_to_weekday, typed_weekday, when_text_from_iso
from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _allow, _model_call, _now_in_saved_zone, _pending

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
ASKS = (
    ("Primink {day} {h} val. paskambinti santechnikui", "paskambinti santechnikui"),
    ("Erinnere mich am {day} um {h} Uhr an den Ölwechsel", "Ölwechsel"),
    ("recuérdame el {day} a las {h} sacar la basura", "sacar la basura"),
    ("ping me {day} {h}pm to call the bank", "call the bank"),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


@pytest.mark.parametrize("seed", range(8))
def test_a_date_a_day_off_is_moved_to_the_named_day(seed) -> None:
    rng = random.Random(f"align-{seed}")
    today = date(2026, 11, 23) + timedelta(days=rng.randint(0, 30))
    weekday = rng.randrange(7)
    meant = today + timedelta(days=(weekday - today.weekday()) % 7 or 7)
    typed = meant + timedelta(days=rng.choice((-1, 1)))
    moved = align_to_weekday(f"{typed.isoformat()}T16:00", weekday, today)
    assert moved == f"{meant.isoformat()}T16:00", (today, DAYS[weekday], typed, moved)
    # A date already on that day stands, even a week later ("next Friday").
    later = (meant + timedelta(days=7)).isoformat() + "T09:30"
    assert align_to_weekday(later, weekday, today) == later


@pytest.mark.parametrize("word,index", [("Friday", 4), ("fri", 4), ("SATURDAY", 5), ("mon.", 0), ("tomorrow", None),
                                        ("penktadienis", None), ("", None)])
def test_only_an_english_weekday_is_a_typed_day(word, index) -> None:
    assert typed_weekday(word) == index


@pytest.mark.parametrize("typed,words", [
    ("2026-11-27T16:00 UTC", "2026-11-27 16:00 UTC+00:00"),
    ("2026-11-27 16:00 GMT+2", "2026-11-27 16:00 UTC+02:00"),
    ("2026-11-27T16:00Z", "2026-11-27 16:00 UTC+00:00"),
    ("2026-11-27T16:00", "2026-11-27 16:00"),
])
def test_a_typed_time_that_names_its_zone_in_words_is_read(typed, words) -> None:
    assert when_text_from_iso(typed) == words


@pytest.mark.parametrize("seed", range(6))
def test_the_reminder_tool_sets_the_named_day_in_any_language(seed, home) -> None:
    rng = random.Random(f"named-day-{seed}")
    today = _now_in_saved_zone()
    weekday = rng.randrange(7)
    meant = (today + timedelta(days=(weekday - today.weekday()) % 7 or 7)).date()
    off_by_one = meant - timedelta(days=1) if meant - timedelta(days=1) > today.date() else meant + timedelta(days=1)
    hour = rng.randint(9, 18)
    said, note = rng.choice(ASKS)
    session = f"chat-named-day-{seed}"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": note, "due_iso": f"{off_by_one.isoformat()}T{hour:02d}:00", "day": DAYS[weekday].title(),
         "text": said.format(day=DAYS[weekday], h=hour)},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert (wall.date(), wall.hour) == (meant, hour), (row["due_wall"], meant, DAYS[weekday])


def test_a_move_typed_in_utc_words_moves_the_reminder(home) -> None:
    session = "chat-move-utc"
    due = (_now_in_saved_zone() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    assert _model_call("operator.schedule_reminder", {"note": "call the bank", "due_iso": due.strftime("%Y-%m-%dT%H:%M")},
                       session_id=session).ok
    later = due + timedelta(days=1)
    moved = _model_call("operator.move_reminder", {"note": "call the bank",
                                                   "due_iso": later.strftime("%Y-%m-%dT%H:%M") + " UTC"},
                        session_id=session)
    if moved.status == "pending_approval":
        moved = _model_call("operator.move_reminder", {"note": "call the bank",
                                                       "due_iso": later.strftime("%Y-%m-%dT%H:%M") + " UTC"},
                            session_id=session, approval_token=_allow(moved))
    assert moved.ok, moved.response_text
    [row] = _pending(session)
    assert datetime.fromisoformat(str(row["due_at_utc"]).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M") == \
        later.strftime("%Y-%m-%d %H:%M")
