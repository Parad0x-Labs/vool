"""An order for the chat's only reminder finds it in any language, and the new time a move gives is never a name.

CI on 0c3d44046: "move the reminder to 2027-09-20 14:00" was refused ("No reminder matches that"), because the date's
digits were read as words naming another reminder. The time a move gives is the time reader's (core.operator.when),
never the reminder's name. An order whose other words name nothing there ("lösche meine Erinnerung") is judged by the
chat's own model: the same reminder, or another one that is not there. With no judgement, nothing changes and the
reminders there are are shown. Notes, times and wordings drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter
from core.operator.handlers import _select_reminder_target, reminder_target_or_refusal
from core.operator.models import OperatorActionIntent

NOTES = ("renew the car insurance", "water the tomatoes", "pay the electricity bill", "call grandma")
TIMES = ("2027-09-20 14:00", "friday at 3pm", "tomorrow at 9:30", "in 2 hours", "monday 08:15", "at 7pm")
MOVES = ("move the reminder to {t}", "push my reminder to {t}", "reschedule it for {t}", "shift the reminder to {t}")
OTHER_LANGUAGE = ("lösche meine Erinnerung", "atšauk mano priminimą", "usuń moje przypomnienie",
                  "verschiebe die Erinnerung auf morgen um 9", "przesuń przypomnienie na jutro")


def _rows(note: str) -> list[dict]:
    return [{"reminder_id": "a1b2c3d4e5f6", "session_id": "chat-1", "status": "scheduled", "note": note,
             "due_at_utc": "2026-11-26T08:00:00+00:00", "tz_name": "Europe/Vilnius", "due_wall": "2026-11-26T10:00"}]


def _select(kind: str, text: str, rows: list[dict]):
    return _select_reminder_target(OperatorActionIntent(kind=kind, raw_text=text), session_id="chat-1",
                                   load_reminder_fn=lambda _i: None, list_reminders_fn=lambda **_k: list(rows))


@pytest.fixture
def judged(monkeypatch):
    asked: list[tuple[str, str]] = []

    def judge(verdict):
        def _answer(message, reminder, **_kw):
            asked.append((message, reminder))
            return verdict
        monkeypatch.setattr(intent_arbiter, "arbitrate_means_the_one_reminder", _answer)
        return asked
    return judge


@pytest.mark.parametrize("seed", range(10))
def test_a_move_to_a_new_time_moves_the_only_reminder_without_a_judgement(seed, judged) -> None:
    rng = random.Random(f"move-{seed}")
    rows = _rows(rng.choice(NOTES))
    asked = judged(None)
    assert _select("move_reminder", rng.choice(MOVES).format(t=rng.choice(TIMES)), rows) == rows[0]
    assert asked == [], "a time is not a name; nothing needed judging"


@pytest.mark.parametrize("text", OTHER_LANGUAGE)
def test_an_order_in_another_language_finds_the_only_reminder_when_the_model_says_it_means_it(text, judged) -> None:
    rows = _rows("pay the electricity bill")
    asked = judged("yes")
    assert _select("cancel_reminder", text, rows) == rows[0]
    assert asked == [(text, "pay the electricity bill")]


@pytest.mark.parametrize("verdict", ["no", None])
def test_without_a_yes_nothing_is_cancelled_and_the_reminder_there_is_shown(verdict, judged) -> None:
    rows = _rows("renew the car insurance")
    judged(verdict)
    intent = OperatorActionIntent(kind="cancel_reminder", raw_text="lösche meine Erinnerung an den Zahnarzt")
    target, refusal = reminder_target_or_refusal(intent, kind="cancel_reminder", session_id="chat-1",
                                                 load_reminder_fn=lambda _i: None,
                                                 list_reminders_fn=lambda **_k: list(rows))
    assert target is None and refusal is not None and not refusal.ok
    assert "renew the car insurance" in refusal.response_text


def test_the_judgement_is_asked_of_the_turns_model_with_the_reminder_beside_it(monkeypatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda _ctx: "")
    monkeypatch.setattr(intent_arbiter, "_judged_for_the_turn",
                        lambda prompt, _ctx, parse=None: prompts.append(prompt) or "yes")
    assert intent_arbiter.arbitrate_means_the_one_reminder("lösche meine Erinnerung", "pay rent") == "yes"
    assert len(prompts) == 1 and 'Reminder: "pay rent". Message: "lösche meine Erinnerung" ->' in prompts[0]
