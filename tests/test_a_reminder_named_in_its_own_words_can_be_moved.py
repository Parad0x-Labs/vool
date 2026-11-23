"""A model that was never shown a reminder's id names it in the reminder's own words, and that reminder is moved.

Mac gate on 12efa4e5 (2026-11-23, installed app, qwen3:8b): "nudge me thursday 6pm to water the tomatoes" was set
after an approval, and the next turn in the same chat, "actually make it 7pm", was answered "I could not find a
pending reminder for that unambiguously". The reply that set the reminder never showed its id, so the model typed
what it had: the reminder's words, in the reminder_id field. Every value in that field was read as an id, matched
nothing, and was refused, even with one pending reminder in the chat.

Only an id-shaped value is an id now. Any other value, or the note field, names the reminder in words: the reminder
those words name is the one moved or cancelled; words that name no reminder of this chat move nothing and ask
nothing. An id-shaped value that is no reminder of this chat is still refused.

In process, as tests/test_reminders_typed_in_any_language.py: the executor the served tool loop calls, a tmp home
per test, no model and no network. The notes, the times and which field carries the words are drawn at random.
"""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _allow, _model_call, _pending
from tests.test_reminders_typed_in_any_language import NOTES, WHEN_WORDS, _due


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


def _set(rng: random.Random, session: str, row) -> str:
    language, note, said = row
    stored = _model_call(
        "operator.schedule_reminder",
        {"note": note, "due_iso": _due(rng).strftime("%Y-%m-%dT%H:%M"), "text": said.format(when=WHEN_WORDS[language])},
        session_id=session,
    )
    assert stored.ok, stored.response_text
    return str(stored.details["reminder_id"])


def _words_for(rng: random.Random, note: str) -> str:
    """The reminder's words as a model may type them: whole, in another case, or only some of them."""
    words = note.split()
    picked = rng.choice((words, [max(words, key=len)], words[-2:] if len(words) > 2 else words))
    text = " ".join(picked)
    return rng.choice((text, text.lower(), text.capitalize()))


def _call_allowed(intent: str, arguments: dict, *, session_id: str):
    execution = _model_call(intent, arguments, session_id=session_id)
    if execution.status == "pending_approval":
        execution = _model_call(intent, arguments, session_id=session_id, approval_token=_allow(execution))
    return execution


@pytest.mark.parametrize("seed", range(8))
def test_a_reminder_named_in_its_words_is_moved_and_keeps_its_id(seed, home) -> None:
    rng = random.Random(f"named-move-{seed}")
    session = f"chat-named-move-{seed}"
    first, second = rng.sample(NOTES, 2)
    ids = {first[1]: _set(rng, session, first), second[1]: _set(rng, session, second)}
    meant = rng.choice((first, second))[1]
    new_due = _due(rng) + timedelta(days=7)
    field = rng.choice(("reminder_id", "note"))
    arguments = {field: _words_for(rng, meant), "due_iso": new_due.strftime("%Y-%m-%dT%H:%M")}

    moved = _call_allowed("operator.move_reminder", arguments, session_id=session)

    assert moved.ok and moved.mode == "tool_executed", (arguments, moved.response_text)
    rows = {row["note"]: row for row in _pending(session)}
    assert rows[meant]["reminder_id"] == ids[meant]
    assert str(rows[meant]["due_wall"]).startswith(new_due.strftime("%Y-%m-%dT%H:%M")), rows[meant]
    other = next(note for note in ids if note != meant)
    assert not str(rows[other]["due_wall"]).startswith(new_due.strftime("%Y-%m-%dT%H:%M")), rows[other]


@pytest.mark.parametrize("seed", range(4))
def test_the_only_reminder_named_in_its_words_can_be_cancelled(seed, home) -> None:
    rng = random.Random(f"named-cancel-{seed}")
    session = f"chat-named-cancel-{seed}"
    row = rng.choice(NOTES)
    _set(rng, session, row)

    asked = _model_call("operator.cancel_reminder", {"reminder_id": _words_for(rng, row[1])}, session_id=session)

    assert asked.status == "pending_approval", asked.response_text
    assert row[1] in str(asked.details["approval_request"].get("action_plain") or ""), asked.details


@pytest.mark.parametrize("seed", range(4))
def test_words_that_name_no_reminder_of_this_chat_move_nothing(seed, home) -> None:
    rng = random.Random(f"named-none-{seed}")
    session = f"chat-named-none-{seed}"
    kept, absent = rng.sample(NOTES, 2)
    _set(rng, session, kept)
    before = _pending(session)
    field = rng.choice(("reminder_id", "note"))
    arguments = {field: absent[1], "due_iso": (_due(rng) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")}

    asked = _model_call("operator.move_reminder", arguments, session_id=session)

    assert not asked.ok and "approval_request" not in (asked.details or {}), asked.details
    assert _pending(session) == before


@pytest.mark.parametrize("seed", range(4))
def test_an_id_that_is_no_reminder_of_this_chat_is_still_refused(seed, home) -> None:
    rng = random.Random(f"named-bad-id-{seed}")
    session = f"chat-named-bad-id-{seed}"
    _set(rng, session, rng.choice(NOTES))
    before = _pending(session)
    unknown = "".join(rng.choice("0123456789abcdef") for _ in range(7)) + "9"
    arguments = {"reminder_id": unknown, "due_iso": (_due(rng) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")}

    asked = _model_call("operator.move_reminder", arguments, session_id=session)

    assert asked.status == "invalid_request" and "approval_request" not in (asked.details or {}), asked.details
    assert _pending(session) == before
