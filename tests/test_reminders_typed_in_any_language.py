"""A model sets, moves and cancels a reminder from typed fields, in any language, and is asked only about the reminder
it means.

Outside review of 261a0389 (2026-11-22), measured through the real handler: a reminder asked in Lithuanian, German,
Spanish or French failed with "I could not read a date or time", because the reminder tool took only the request as
written and read it in English. The calendar tool took a typed time, so a model that had already worked the time out
had one tool that accepted it (the haircut case). Cancel was approved before the reminder was resolved, and the card
could only say "cancel a reminder"; a cancel by the id the listing showed from another chat was told to give the id.

The reminder tools now take the note and an ISO due time (reminder_id for cancel and move, and a move tool that keeps
the id); the reminder a cancel or move means is found before anything is asked, and the card names it.

In process, like tests/test_reminders_the_model_can_set.py: the executor the served tool loop calls runs directly, a
tmp VOOL home per test, no model and no network. The notes and times are built from parts at random.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _allow, _model_call, _now_in_saved_zone, _pending

#: (language, the note in that language, the request as a person writes it, with {when} for the words of the time).
NOTES = (
    ("lt", "nupirkti duonos ir pieno", "Primink man {when} nupirkti duonos ir pieno"),
    ("lt", "paskambinti odontologui", "Gal gali priminti {when} paskambinti odontologui?"),
    ("de", "den Zahnarzt anrufen", "Erinnere mich {when} daran, den Zahnarzt anzurufen"),
    ("de", "die Pflanzen gießen", "Kannst du mich {when} ans Pflanzengießen erinnern?"),
    ("es", "llamar al fontanero", "Recuérdame {when} llamar al fontanero"),
    ("es", "sacar la basura", "ponme un recordatorio {when} para sacar la basura"),
    ("fr", "appeler la banque", "Rappelle-moi {when} d'appeler la banque"),
    ("fr", "arroser les tomates", "mets un rappel {when} pour arroser les tomates"),
    ("ru", "забрать посылку", "Напомни мне {when} забрать посылку"),
)
WHEN_WORDS = {"lt": "rytoj", "de": "morgen", "es": "mañana", "fr": "demain", "ru": "завтра"}


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


def _due(rng: random.Random) -> datetime:
    """A wall time one to five days ahead in the saved zone, on a random quarter hour."""
    day = _now_in_saved_zone() + timedelta(days=rng.randint(1, 5))
    return day.replace(hour=rng.randint(7, 21), minute=rng.choice((0, 15, 30, 45)), second=0, microsecond=0)


def _set(rng: random.Random, session: str, *, besides: str = "") -> tuple[str, datetime, object]:
    language, note, said = rng.choice([row for row in NOTES if row[1] != besides])
    due = _due(rng)
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": note, "due_iso": due.strftime("%Y-%m-%dT%H:%M"), "text": said.format(when=WHEN_WORDS[language])},
        session_id=session,
    )
    return note, due, stored


@pytest.mark.parametrize("seed", range(6))
def test_a_typed_note_and_time_store_one_reminder_in_any_language(seed, home) -> None:
    rng = random.Random(f"typed-reminder-{seed}")
    session = f"chat-typed-{seed}"
    note, due, stored = _set(rng, session)
    assert stored.ok and stored.mode == "tool_executed", stored.response_text
    [row] = _pending(session)
    assert row["note"] == note, row
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert (wall.date(), wall.hour, wall.minute) == (due.date(), due.hour, due.minute), (row["due_wall"], due)
    assert stored.details["reminder_id"] == row["reminder_id"]


@pytest.mark.parametrize("typed", ["2026-13-40T10:00", "next week", "2026-12-01"])
def test_a_typed_time_that_is_not_one_stores_nothing(typed, home) -> None:
    session = "chat-typed-bad"
    stored = _model_call("operator.schedule_reminder", {"note": "call the bank", "due_iso": typed}, session_id=session)
    assert not stored.ok, stored.response_text
    assert _pending(session) == []


def _cancel_allowed(arguments: dict, session: str):
    asked = _model_call("operator.cancel_reminder", arguments, session_id=session)
    if asked.status == "pending_approval":
        return asked, _model_call("operator.cancel_reminder", arguments, session_id=session, approval_token=_allow(asked))
    return asked, asked


@pytest.mark.parametrize("seed", range(4))
def test_another_chats_reminder_by_its_listed_id_is_cancelled_from_here(seed, home) -> None:
    """Mac gate on 608294d38 (2026-11-24): a reminder another chat set, which "list my reminders" in this chat showed
    with its id, could not be cancelled here ("Open that chat and ask there"). Any reminder the listing here shows
    can be cancelled here; the card names it and the chat it came from keeps nothing pending."""
    rng = random.Random(f"typed-elsewhere-{seed}")
    owner, other = f"chat-owner-{seed}", f"chat-other-{seed}"
    kept_note, _due0, kept = _set(rng, owner)
    note, _due_at, stored = _set(rng, owner, besides=kept_note)
    assert kept.ok and stored.ok, stored.response_text
    listed = _model_call("operator.list_reminders", {}, session_id=other)
    shown = next(row for row in listed.details["reminders"] if row["note"] == note)
    short_id = str(shown["reminder_id"])[:8]
    assert short_id in listed.response_text

    arguments = {"reminder_id": rng.choice([short_id, short_id.upper(), str(shown["reminder_id"])])}
    asked, done = _cancel_allowed(arguments, other)
    if asked.status == "pending_approval":
        request = dict(asked.details["approval_request"])
        assert note in str(request.get("action_plain") or ""), request
    assert done.ok and done.mode == "tool_executed", done.response_text
    assert note in done.response_text, done.response_text
    assert [row["note"] for row in _pending(owner)] == [kept_note]


#: (language, the note, a word of it a person uses to name it in another chat, how they ask in that language).
NAMED = (
    ("en", "pay the electricity bill", "electricity", "never mind, remove the {word} reminder"),
    ("en", "book the vet for the cat", "vet", "actually scrap the {word} one"),
    ("lt", "sumokėti už elektrą", "elektrą", "atšauk priminimą apie {word}"),
    ("lt", "nupirkti dovaną mamai", "dovaną", "nebereikia priminimo dėl {word}, ištrink"),
    ("de", "die Stromrechnung bezahlen", "Stromrechnung", "lösch bitte die Erinnerung zur {word}"),
    ("es", "renovar el pasaporte", "pasaporte", "quita el recordatorio del {word}"),
)


@pytest.mark.parametrize("seed", range(6))
def test_a_reminder_named_in_its_own_words_is_cancelled_from_another_chat(seed, home) -> None:
    rng = random.Random(f"named-elsewhere-{seed}")
    owner, other = f"chat-named-owner-{seed}", f"chat-named-other-{seed}"
    language, note, word, said = NAMED[seed % len(NAMED)]
    kept_note = rng.choice([row[1] for row in NAMES_OTHER if row[1] != note])
    due = _due(rng)
    for text in (kept_note, note):
        made = _model_call("operator.schedule_reminder", {"note": text, "due_iso": due.strftime("%Y-%m-%dT%H:%M")},
                           session_id=owner)
        assert made.ok, made.response_text
        due += timedelta(hours=1)
    # The model's call carries the person's words, or the note's word it picked out, in the language they were said.
    arguments = rng.choice([{"text": said.format(word=word)}, {"note": word}])
    _asked, done = _cancel_allowed(arguments, other)
    assert done.ok and done.mode == "tool_executed", (language, arguments, done.response_text)
    assert [row["note"] for row in _pending(owner)] == [kept_note], language


NAMES_OTHER = (("en", "water the tomatoes"), ("en", "call the plumber"), ("lt", "paskambinti odontologui"),
               ("de", "den Zahnarzt anrufen"))


@pytest.mark.parametrize("seed", range(4))
def test_words_that_name_two_reminders_ask_which_and_cancel_nothing(seed, home) -> None:
    rng = random.Random(f"tied-{seed}")
    first, second, here = f"chat-tied-a-{seed}", f"chat-tied-b-{seed}", f"chat-tied-c-{seed}"
    word = rng.choice(["dentist", "passport", "garage"])
    notes = [f"call the {word} about {rng.choice(['monday', 'the bill', 'parking'])}", f"{word} {rng.choice(['visit', 'form'])}"]
    due = _due(rng)
    for chat, text in zip((first, second), notes, strict=True):
        assert _model_call("operator.schedule_reminder", {"note": text, "due_iso": due.strftime("%Y-%m-%dT%H:%M")},
                           session_id=chat).ok
    asked = _model_call("operator.cancel_reminder", {"note": word}, session_id=here)
    assert asked.status == "invalid_request" and not asked.ok, asked.details
    assert all(text in asked.response_text for text in notes), asked.response_text
    assert len(_pending(first)) == 1 and len(_pending(second)) == 1


@pytest.mark.parametrize("seed", range(3))
def test_a_private_chats_reminder_stays_out_of_reach(seed, home) -> None:
    from core.private_chat import set_private

    rng = random.Random(f"private-{seed}")
    owner, other = f"chat-private-owner-{seed}", f"chat-private-other-{seed}"
    note, _due_at, stored = _set(rng, owner)
    assert stored.ok
    assert set_private(owner, True)
    reminder_id = str(stored.details["reminder_id"])
    for arguments in ({"reminder_id": reminder_id[:8]}, {"note": note}):
        asked = _model_call("operator.cancel_reminder", arguments, session_id=other)
        assert not asked.ok and asked.status != "pending_approval", asked.details
        assert note not in asked.response_text, asked.response_text
    assert [row["reminder_id"] for row in _pending(owner)] == [reminder_id]


@pytest.mark.parametrize("seed", range(4))
def test_a_cancel_that_names_no_reminder_of_this_chat_asks_nothing(seed, home) -> None:
    rng = random.Random(f"typed-missing-{seed}")
    session = f"chat-missing-{seed}"
    for _ in range(2):
        assert _set(rng, session)[2].ok
    unknown = "".join(rng.choice("0123456789abcdef") for _ in range(7)) + "9"
    asked = _model_call("operator.cancel_reminder", {"reminder_id": unknown}, session_id=session)
    assert asked.status == "invalid_request" and "approval_request" not in (asked.details or {}), asked.details
    assert len(_pending(session)) == 2


@pytest.mark.parametrize("seed", range(4))
def test_a_move_names_the_reminder_on_its_card_and_keeps_its_id(seed, home) -> None:
    rng = random.Random(f"typed-move-{seed}")
    session = f"chat-move-{seed}"
    note, _first_due, stored = _set(rng, session)
    assert stored.ok, stored.response_text
    reminder_id = str(stored.details["reminder_id"])
    new_due = _due(rng) + timedelta(days=6)
    arguments = {"reminder_id": reminder_id[:8], "due_iso": new_due.strftime("%Y-%m-%dT%H:%M")}

    asked = _model_call("operator.move_reminder", arguments, session_id=session)
    if asked.status == "pending_approval":
        request = dict(asked.details["approval_request"])
        assert note in str(request.get("action_plain") or ""), request
        assert any(note in str(item) for item in request.get("affected_resources") or ()), request
        assert "nothing is sent" in str(request.get("expected_side_effects") or ""), request
        asked = _model_call("operator.move_reminder", arguments, session_id=session, approval_token=_allow(asked))
    assert asked.ok and asked.mode == "tool_executed", asked.response_text

    [row] = _pending(session)
    assert row["reminder_id"] == reminder_id
    wall = datetime.fromisoformat(str(row["due_wall"]))
    assert (wall.date(), wall.hour, wall.minute) == (new_due.date(), new_due.hour, new_due.minute), row["due_wall"]


@pytest.mark.parametrize("seed", range(4))
def test_a_cancel_card_names_the_reminder_and_says_nothing_about_files(seed, home) -> None:
    rng = random.Random(f"typed-cancel-{seed}")
    session = f"chat-cancel-{seed}"
    kept_note, _due1, kept = _set(rng, session)
    note, _due2, stored = _set(rng, session, besides=kept_note)
    assert kept.ok and stored.ok
    reminder_id = str(stored.details["reminder_id"])

    asked = _model_call("operator.cancel_reminder", {"reminder_id": reminder_id[:8]}, session_id=session)
    assert asked.status == "pending_approval", asked.response_text
    request = dict(asked.details["approval_request"])
    assert note in str(request.get("action_plain") or ""), request
    assert "file" not in str(request.get("expected_side_effects") or ""), request

    done = _model_call(
        "operator.cancel_reminder", {"reminder_id": reminder_id[:8]}, session_id=session, approval_token=_allow(asked)
    )
    assert done.ok and done.mode == "tool_executed", done.response_text
    assert [row["note"] for row in _pending(session)] == [kept_note]
