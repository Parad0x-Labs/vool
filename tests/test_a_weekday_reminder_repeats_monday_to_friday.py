"""A reminder asked for every weekday comes back Monday to Friday, in any language.

Fresh-question audit on 02ff11496: "remind me every weekday at 7:45 to take the bins out" was stored once, on one
day, with no repeat at all, and the reply said it would come back. The repeat rule was read only from English words
("every day", "every week"), and only daily and weekly existed. The reminder tool now takes a typed ``repeat``
(daily, weekdays or weekly) that the model fills in whatever language the person wrote, the store knows a weekday
rule (no Saturday or Sunday occurrence, a first due time on a weekend moves to Monday), and a rule VOOL cannot keep
is refused instead of being stored as a one-off.

In process, like tests/test_reminders_typed_in_any_language.py: the executor the served tool loop calls runs
directly, a tmp VOOL home per test, no model and no network. Notes, languages and times are drawn at random.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _model_call, _now_in_saved_zone, _pending

ASKS = (
    ("lt", "išnešti šiukšles", "Kiekvieną darbo dieną 7:45 primink išnešti šiukšles"),
    ("de", "die Tabletten nehmen", "Erinnere mich jeden Werktag um 7:45 an die Tabletten"),
    ("es", "regar las plantas", "recuérdame de lunes a viernes a las 7:45 regar las plantas"),
    ("fr", "sortir le chien", "rappelle-moi chaque jour de semaine à 7h45 de sortir le chien"),
    ("pl", "zadzwonić do mamy", "przypominaj mi w dni robocze o 7:45, żeby zadzwonić do mamy"),
    ("en", "stand-up notes", "ping me every workday at quarter to eight about the stand-up notes"),
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


def _next(day: datetime, weekday: int) -> datetime:
    return day + timedelta(days=(weekday - day.weekday()) % 7 or 7)


@pytest.mark.parametrize("seed", range(6))
def test_a_typed_weekday_rule_is_kept_and_skips_the_weekend(seed, home) -> None:
    from core.operator.reminders import next_repeat_occurrence

    rng = random.Random(f"weekday-repeat-{seed}")
    _lang, note, said = rng.choice(ASKS)
    friday = _next(_now_in_saved_zone(), 4).replace(hour=7, minute=45, second=0, microsecond=0)
    session = f"chat-weekdays-{seed}"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": note, "due_iso": friday.strftime("%Y-%m-%dT%H:%M"), "repeat": rng.choice(["weekdays", "Weekdays"]),
         "text": said},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    assert json.loads(row["payload_json"])["repeat_every"] == "weekdays"
    assert "Monday to Friday" in stored.response_text
    # The occurrence after Friday's is Monday's, at the same wall time: never Saturday or Sunday.
    after = datetime.fromisoformat(str(row["due_at_utc"]).replace("Z", "+00:00")) + timedelta(minutes=1)
    nxt = next_repeat_occurrence(row, now_fn=lambda: after.isoformat())
    wall = datetime.fromisoformat(nxt["due_wall"])
    assert wall.weekday() == 0 and (wall.hour, wall.minute) == (7, 45), nxt
    assert (wall.date() - friday.date()).days == 3


def test_a_first_due_time_on_a_weekend_moves_to_monday(home) -> None:
    saturday = _next(_now_in_saved_zone(), 5).replace(hour=7, minute=45, second=0, microsecond=0)
    session = "chat-weekdays-weekend"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": "išnešti šiukšles", "due_iso": saturday.strftime("%Y-%m-%dT%H:%M"), "repeat": "weekdays"},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert wall.weekday() == 0 and wall.date() == (saturday + timedelta(days=2)).date(), row["due_wall"]


@pytest.mark.parametrize("rule", ["monthly", "every second tuesday", "hourly"])
def test_a_repeat_rule_vool_cannot_keep_stores_nothing(rule, home) -> None:
    due = _now_in_saved_zone() + timedelta(days=2)
    session = "chat-weekdays-unknown"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": "pay the rent", "due_iso": due.strftime("%Y-%m-%dT%H:%M"), "repeat": rule},
        session_id=session,
    )
    assert not stored.ok and stored.status == "invalid_request", stored.response_text
    assert "Nothing was set" in stored.response_text
    assert _pending(session) == []


@pytest.mark.parametrize("rule", ["", "once", "none"])
def test_no_repeat_typed_stores_a_one_off(rule, home) -> None:
    due = (_now_in_saved_zone() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    session = "chat-weekdays-once"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": "call the garage", "due_iso": due.strftime("%Y-%m-%dT%H:%M"), "repeat": rule},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    assert "repeat_every" not in json.loads(row["payload_json"])


def test_the_reminder_tool_offers_the_repeat_rule() -> None:
    from core.runtime_tool_contracts import runtime_tool_contracts

    [contract] = [c for c in runtime_tool_contracts() if c.intent == "operator.schedule_reminder"]
    assert "weekdays" in str(contract.input_schema.get("repeat", ""))


@pytest.mark.parametrize("seed", range(4))
def test_a_repeat_whose_first_slot_passed_today_starts_at_the_next_one(seed, home) -> None:
    """02ff11496 audit: "Erinnere mich jeden Montag um 18 Uhr ..." asked on a Monday at 20:43 was refused because
    today's 18:00 had passed. A repeating reminder starts at its next slot; a one-off at a passed time is still refused."""
    rng = random.Random(f"passed-slot-{seed}")
    rule, step = rng.choice([("weekly", 7), ("daily", 1), ("Weekly", 7)])
    earlier = (_now_in_saved_zone() - timedelta(hours=rng.randint(1, 5))).replace(second=0, microsecond=0)
    session = f"chat-passed-slot-{seed}"
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": rng.choice(["die Mülltonne rausstellen", "išnešti šiukšles", "water the basil"]),
         "due_iso": earlier.strftime("%Y-%m-%dT%H:%M"), "repeat": rule},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    [row] = _pending(session)
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert wall.date() == (earlier + timedelta(days=step)).date(), row["due_wall"]
    assert (wall.hour, wall.minute) == (earlier.hour, earlier.minute)

    once = f"chat-passed-once-{seed}"
    refused = _model_call(
        "operator.schedule_reminder",
        {"note": "call the garage", "due_iso": earlier.strftime("%Y-%m-%dT%H:%M")},
        session_id=once,
    )
    assert not refused.ok and _pending(once) == []
