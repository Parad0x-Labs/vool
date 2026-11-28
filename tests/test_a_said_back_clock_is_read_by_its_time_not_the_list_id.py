"""A reply that says a listed reminder back with its clock's zero seconds keeps it; an id's digits back no time.

CI on ef4440775 (2026-11-28): the served reminder-list check asked "show my reminders pls", the list showed
"renew the parking permit — due Fri 18 Dec 2026 at 08:20 (id 1cf1c520)", and the model said it back as "There is a
reminder scheduled for 18 Dec 2026 at 08:20:00 UTC: renew the parking permit." The ":00" seconds read as a 0 the list
never showed, and the person was told "No reminder or timer was set, changed or cancelled". On the day before it
passed only because that day's seeded id held a 0: the digits inside "0a0b0c0d" were read as numbers the list stated.

So a clock is read as its hour and minute (and seconds only when they are not zero), and the digits inside a code word
such as a reminder id are no number at all: a change to an hour that only the id spells is still a change claim.
Notes, times, ids and wordings are drawn at random, in the list's own format.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from core.operator.handlers import format_due_time
from tests.test_a_reply_that_says_back_a_read_keeps_it import NOTES, _gate_after
from tests.test_tool_cards_use import fresh_turns

_SAID_WITH_SECONDS = (
    "There is a reminder scheduled for {day} at {clock}:00 UTC: {note}.",
    'You have one reminder set: "{note}", due {day} at {clock}:00.',
    "Your reminder to {note} is scheduled for {day}, {clock}:00 UTC.",
)


def _listed(rng: random.Random) -> tuple[str, datetime, str]:
    note = rng.choice(NOTES)
    due = (datetime.now(timezone.utc) + timedelta(days=rng.randint(1, 20))).replace(
        hour=rng.randint(7, 21), minute=rng.choice((5, 15, 20, 35, 45)), second=0, microsecond=0
    )
    return note, due, format_due_time(due.isoformat(), "UTC", "")


def _hex_id(rng: random.Random, *, holding: str = "") -> str:
    """An 8-character reminder id; ``holding`` digits are placed after a letter, as ids carry them."""
    body = f"{rng.choice('abcdef')}{holding}" if holding else ""
    return (body + "".join(rng.choice("0123456789abcdef") for _ in range(8)))[:8]


@pytest.mark.parametrize("seed", range(12))
def test_a_listed_reminder_said_back_with_zero_seconds_is_kept(seed, fresh_turns) -> None:
    rng = random.Random(f"said-seconds-{seed}")
    note, due, wall = _listed(rng)
    rid = _hex_id(rng)
    reply = rng.choice(_SAID_WITH_SECONDS).format(note=note, day=due.strftime("%d %b %Y"), clock=due.strftime("%H:%M"))
    returned = f"Pending reminders:\n- {note} — due {wall} (id {rid})"

    published, record = _gate_after(rng.choice(("what reminders do i have", "anything pending for me?")), reply,
                                    f"req:said-seconds:{seed}", tool="operator.list_reminders", returned=returned)

    assert published == reply, (reply, returned, record)


@pytest.mark.parametrize("seed", range(8))
def test_an_hour_only_the_list_id_spells_is_still_a_change_claim(seed, fresh_turns) -> None:
    rng = random.Random(f"said-id-hour-{seed}")
    note, due, wall = _listed(rng)
    shown = {due.hour, due.minute, due.day, due.year, due.hour % 12}
    hour = rng.choice([h for h in range(1, 12) if h not in shown and h + 12 not in shown])
    rid = _hex_id(rng, holding=str(hour))
    told = f"Your reminder to {note} is now set for {hour}pm."
    returned = f"Pending reminders:\n- {note} — due {wall} (id {rid})"

    published, record = _gate_after(f"move my {note.split()[-1]} reminder", told, f"req:said-id-hour:{seed}",
                                    tool="operator.list_reminders", returned=returned)

    assert record.get("publication") == "action_claims_corrected", (told, returned, record)
    assert told not in published
