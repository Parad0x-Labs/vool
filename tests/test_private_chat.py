"""Private chat (core.private_chat): the words that switch it, the switch's store, and its failure paths.

Phrases are authored here, not taken from any eval set. The served proof (nothing of a private turn
on disk, the ghost marks on the commit) is tests/test_private_chat_served_workflow.py.
"""
from __future__ import annotations

import pytest

from core import private_chat
from core.private_chat import OFF, ON, STATUS, parse_private_command


@pytest.mark.parametrize(
    "text",
    [
        "don't record this",
        "Don’t record this.",
        "private chat",
        "ok let's go private",
        "off the record",
        "can you stop recording?",
        "incognito mode",
        "please don't save this conversation",
        "keep this private",
    ],
)
def test_a_bare_request_not_to_be_recorded_turns_private_on(text):
    command = parse_private_command(text)
    assert command is not None and command.action == ON and command.bare, (text, command)


def test_a_request_inside_a_longer_message_turns_it_on_and_keeps_the_rest():
    command = parse_private_command("don't record this, but my salary is 90k, is that fair?")
    assert command is not None and command.action == ON
    assert command.rest == "my salary is 90k, is that fair?"
    trailing = parse_private_command("My salary is 90k - don't record this.")
    assert trailing is not None and trailing.action == ON and trailing.rest == "My salary is 90k"


@pytest.mark.parametrize(
    "text",
    [
        "I said don't record this yesterday, why did you?",
        "please don't go private",
        "No logging in this function please",
        "save this file to desktop",
        "record a voice memo",
        "don't save this note to Apple Notes",
        "what is private chat?",
        "turn on",
        "back to normal",
    ],
)
def test_sentences_about_other_things_are_not_commands(text):
    assert parse_private_command(text) is None, text


@pytest.mark.parametrize(
    "text", ["turn recording back on", "end private chat", "we're back on the record", "resume recording"]
)
def test_recording_back_on(text):
    command = parse_private_command(text, currently_private=True)
    assert command is not None and command.action == OFF and command.bare


def test_a_bare_turn_it_on_ends_private_chat_only_while_it_is_on():
    assert parse_private_command("turn it back on", currently_private=True).action == OFF
    assert parse_private_command("turn it back on", currently_private=False) is None


def test_status_question():
    assert parse_private_command("are you recording this?").action == STATUS
    assert parse_private_command("is this chat being saved?").action == STATUS


def test_the_switch_survives_in_the_store_and_turns_off(tmp_path, monkeypatch):
    from storage import db

    monkeypatch.setattr(db, "DEFAULT_DB_PATH", str(tmp_path / "private.db"), raising=False)
    monkeypatch.setattr(private_chat, "get_connection", lambda: db.get_connection(str(tmp_path / "private.db")))
    chat = "openclaw:" + "p" * 20
    assert private_chat.is_private(chat) is False
    assert private_chat.set_private(chat, True) is True
    assert private_chat.is_private(chat) is True
    assert private_chat.private_since(chat)
    assert private_chat.set_private(chat, False) is False
    assert private_chat.is_private(chat) is False


def test_an_unreadable_switch_counts_as_private(monkeypatch):
    def broken():
        raise OSError("disk unavailable")

    monkeypatch.setattr(private_chat, "get_connection", broken)
    assert private_chat.is_private("openclaw:" + "q" * 20) is True
    assert private_chat.turn_is_private({"chat_id": "openclaw:" + "q" * 20}) is True


def test_a_switch_that_cannot_be_written_is_never_reported_as_on(monkeypatch):
    """Turning it on fails: the turn is still kept out of the record, and the reply says it failed."""

    class _Agent:
        def _fast_path_result(self, **kwargs):
            return {"response": kwargs["response"], "reason": kwargs["reason"]}

    def broken():
        raise OSError("disk unavailable")

    monkeypatch.setattr(private_chat, "get_connection", broken)
    context: dict = {"chat_id": "openclaw:" + "r" * 20}
    outcome = private_chat.observe_private_turn(_Agent(), "don't record this", session_id="", source_context=context)
    assert outcome is not None
    assert "couldn't turn private chat on" in outcome["result"]["response"]
    assert context[private_chat.PRIVATE_TURN_KEY] is True


def test_a_private_turn_writes_nothing_to_the_conversation_log(tmp_path, monkeypatch):
    """The one memory writer every lane calls refuses a turn stamped private."""
    from core import persistent_memory

    calls: list[str] = []
    monkeypatch.setattr(persistent_memory, "ensure_memory_files", lambda: calls.append("wrote"))
    persistent_memory._write_conversation_event(
        session_id="openclaw:" + "s" * 20,
        user_input="the venue is Juniper Hall",
        assistant_output="Noted.",
        source_context={private_chat.PRIVATE_TURN_KEY: True},
    )
    assert calls == []


def test_stored_runtime_events_of_a_private_turn_keep_no_words():
    kept = private_chat.private_event_details(
        {"turn_key": "t1", "status": "completed", "request_preview": "the venue is Juniper Hall", "summary": "x" * 10}
    )
    assert kept == {"private_chat": True, "turn_key": "t1", "status": "completed"}
