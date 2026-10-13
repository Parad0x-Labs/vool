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


def test_words_a_private_turn_blanked_or_deleted_are_not_left_in_the_database_file(tmp_path):
    """A blanked or deleted row must not survive as bytes in SQLite's free pages (or its WAL)."""
    from storage.db import get_connection

    path = tmp_path / "store.db"
    secret = "Quillmere-free-page-probe"
    conn = get_connection(str(path))
    try:
        # Many SQLite builds (python.org, Homebrew, CI's setup-python) default this off; Debian's is on.
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, body TEXT)")
        for i in range(40):
            conn.execute("INSERT INTO t (body) VALUES (?)", (f"{secret} {i} " + "x" * 3000,))
        conn.commit()
        conn.execute("UPDATE t SET body = '' WHERE id <= 20")
        conn.execute("DELETE FROM t WHERE id > 20")
        conn.commit()
    finally:
        conn.close()
    leftovers = [p.name for p in tmp_path.iterdir() if p.is_file() and secret.encode() in p.read_bytes()]
    assert leftovers == []


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        ("don't record this\nmy boss is Grumblewick", "my boss is Grumblewick"),
        ("my pin is 1234 dont save this", "my pin is 1234"),
        ("(don't save this)", ""),
        ("don't record this 🙏", ""),
        ("can we talk privately", ""),
        ("do not record", ""),
        ("can you not record this", ""),
        ("make this private", ""),
        ("I don't want this saved", ""),
        ("dont recrod this", ""),
        ("I need to vent, keep this between us", "I need to vent"),
    ],
)
def test_more_ways_a_person_asks_not_to_be_recorded(text, rest):
    command = parse_private_command(text)
    assert command is not None and command.action == ON and command.rest == rest, (text, command)


@pytest.mark.parametrize(
    "text",
    ["what is private chat?", "how do I make this private", "I told him don't record this",
     "Remind me to tell Sam we should talk privately", "don't save it as a PDF, save it as docx",
     "I don't want this saved as a draft", "keep this between us and the client",
     "subject line: Don't save this", "tell my wife: keep this between us", "Save again"],
)
def test_a_mention_at_the_end_of_a_sentence_is_not_a_command(text):
    assert parse_private_command(text) is None, text


@pytest.mark.parametrize(
    "text",
    ["You can save this, it's just a draft", "start recording, I'm going to sing",
     "Ok, we're on the record now, said the journalist", "turn recording back on. My cat is Tom"],
)
def test_private_chat_ends_only_on_the_command_alone(text):
    command = parse_private_command(text, currently_private=True)
    assert command is None or command.action != OFF, (text, command)


@pytest.mark.parametrize(
    "text", ["private off", "recording on", "disable private mode", "normal mode", "turn on recording again"]
)
def test_more_ways_to_end_private_chat(text):
    command = parse_private_command(text, currently_private=True)
    assert command is not None and command.action == OFF and command.bare, (text, command)


def test_a_privacy_request_the_switch_did_not_read_tells_the_model_it_is_not_on(monkeypatch):
    """Nothing switched: the model is told the chat is still saved, so it can't promise otherwise."""

    class _Agent:
        def _fast_path_result(self, **kwargs):
            raise AssertionError("not a command")

    monkeypatch.setattr(private_chat, "is_private", lambda sid: False)
    context: dict = {"chat_id": "openclaw:" + "t" * 20}
    assert private_chat.observe_private_turn(
        _Agent(), "bitte nicht speichern, mein Gehalt ist 90k", session_id="", source_context=context
    ) is None
    assert context.get(private_chat.NOT_ON_KEY) is True and not context.get(private_chat.PRIVATE_TURN_KEY)
    assert "IS NOT ON" in private_chat.not_on_prompt_block()
    plain: dict = {"chat_id": "openclaw:" + "t" * 20}
    private_chat.observe_private_turn(_Agent(), "what's the weather in Vilnius?", session_id="", source_context=plain)
    assert private_chat.NOT_ON_KEY not in plain


def test_a_failed_switch_names_no_error_type():
    assert "{" not in private_chat.REPLY_FAILED and "{" not in private_chat.REPLY_FAILED_ON


@pytest.mark.parametrize(
    ("text", "asks"),
    [("bitte nicht speichern", True), ("не сохраняй это", True), ("don't remember that", True),
     ("don't save this file yet", False), ("dont log me out", False), ("my private key file", False)],
)
def test_only_a_request_about_the_chat_gets_the_not_on_note(text, asks):
    assert private_chat.looks_like_privacy_request(text) is asks, text


def test_a_message_sent_from_a_tab_that_still_shows_private_is_not_saved(monkeypatch):
    monkeypatch.setattr(private_chat, "is_private", lambda sid: False)
    context: dict = {}
    assert private_chat.observe_private_door(
        "the cake is from Larkspur", session_id="openclaw:" + "u" * 20, source_context=context,
        page_believes_private=True,
    ) is True
    assert context[private_chat.PRIVATE_TURN_KEY] is True
    plain: dict = {}
    assert private_chat.observe_private_door(
        "the cake is from Larkspur", session_id="openclaw:" + "u" * 20, source_context=plain
    ) is False
