"""A chat "forget" reaches the copies outside the live stores: update backups, log files and file undo copies.

Red-team F1 (0.7.7 train, 2026-10-30), measured here before the fix: after "Forget applied" the forgotten words
were still in the update backup VOOL takes before every update (a full copy of the profile, kept with no end date,
which a rollback restores), in the app's log and its rotated copies, and in the undo copies of a workspace file
VOOL wrote in that chat. The forget list itself keeps the words by design, and the reply now says so.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.test_blackbox_flight_recorder import _write
from tests.test_forget_scrubs_run_history import CANARY, _remember, command
from tests.test_forget_scrubs_run_history import isolated_pages as isolated_pages  # the fixture

FORGET_LIST = "In this chat, the forgotten words now stay only in VOOL's forget list on this computer, so they are never used again."
NEEDLE = CANARY.lower().encode()


def _data() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir()


def _holding(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")
                  if path.is_file() and NEEDLE in path.read_bytes().lower())


def _backup() -> Path:
    """What the updater takes before an update (core.updater.transaction, runtime-snapshot)."""
    from core.updater.snapshot import snapshot_runtime

    destination = _data() / "update_v2" / "transactions" / "tx-before-0.7.7" / "runtime-snapshot"
    snapshot_runtime(_data(), destination)
    return destination


def test_forget_reaches_the_update_backup_and_the_backup_still_restores(isolated_pages, tmp_path):
    chat = "backup-forget"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _remember(isolated_pages, chat, "the harbor lantern is painted signal green.")
    backup = _backup()
    assert _holding(backup), "the backup copied the remembered words (otherwise this proves nothing)"

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert _holding(backup) == []
    assert reply.endswith(FORGET_LIST), reply
    # the rest of the backup is untouched and it still restores into a working profile
    assert any(b"signal green" in path.read_bytes() for path in backup.rglob("*") if path.is_file())
    for database in backup.rglob("*.db"):
        conn = sqlite3.connect(database)
        try:
            assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)], database
        finally:
            conn.close()
    from core.updater.snapshot import restore_runtime

    restored = tmp_path / "restored-home" / "data"
    restored.mkdir(parents=True)
    restore_runtime(restored, backup)
    assert _holding(restored.parent) == []


def test_forget_reaches_the_log_and_its_rotated_copies(isolated_pages):
    chat = "log-forget"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    logs = _data() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "vool_api.log").write_text(
        "2026-10-30 12:00:01,000 INFO core.web: daemon ready\n"
        "2026-10-30 12:00:02,000 WARNING core.router: no route for 'order bread at the Flourstone Bakehouse'\n"
        "2026-10-30 12:00:03,000 INFO core.web: turn finished\n", encoding="utf-8")
    (logs / "vool_api.log.1").write_text(
        "2026-10-29 09:00:00,000 WARNING core.router: no route for 'is the Flourstone Bakehouse open'\n", encoding="utf-8")

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert _holding(logs) == []
    kept = (logs / "vool_api.log").read_text(encoding="utf-8")
    assert "daemon ready" in kept and "turn finished" in kept


def test_forget_drops_this_chats_undo_copies_and_keeps_another_chats(isolated_pages, tmp_path, monkeypatch):
    from core.blackbox import store as store_module
    from core.mode_permission_policy import reset_mode_permission_state

    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(_data() / "blackbox"))
    store_module.reset_default_store()
    reset_mode_permission_state()
    chat, other = "undo-forget", "undo-other"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    try:
        _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
        assert _write(workspace, "bakery.txt", "Bread order for the Flourstone Bakehouse.\n", session=chat).ok
        assert _write(workspace, "bakery.txt", "Bread order: two loaves.\n", session=chat).ok
        assert _write(workspace, "their.txt", "The Flourstone Bakehouse opens at 7.\n", session=other).ok
        store = store_module.default_store()

        def carrying(data: bytes) -> bool:
            return NEEDLE in data.lower()

        assert store.copies_carrying(carrying, sessions={chat}), "the undo copies held the words"

        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

        assert handled and reply.startswith("Forget applied"), reply
        assert store.copies_carrying(carrying, sessions={chat, ""}) == []
        # another chat's file change keeps its undo copy, and the journal still verifies
        assert store.copies_carrying(carrying, sessions={other})
        assert store.verify().ok
        assert any(entry.get("kind") == "retention_pruned" and entry.get("policy") == {"forget": True}
                   for entry in store.entries())
    finally:
        store_module.reset_default_store()
        reset_mode_permission_state()


def _team_folder(chat: str) -> Path:
    """Where core.agent_team keeps one chat's team, with an agent's task and answer and the registry."""
    import hashlib

    folder = _data() / "agent_teams" / hashlib.sha256(chat.encode()).hexdigest()[:16]
    agent = folder / "agents" / "a1"
    agent.mkdir(parents=True)
    (folder / "session.txt").write_text(chat)
    (agent / "result_full.txt").write_text("Checked the Flourstone Bakehouse hours.\nThey open at 7.\n")
    (agent / "status.jsonl").write_text('{"state": "done", "note": "asked about the Flourstone Bakehouse"}\n')
    conn = sqlite3.connect(folder / "registry.db")
    conn.execute("CREATE TABLE agents (agent_id TEXT PRIMARY KEY, task TEXT)")
    conn.execute("INSERT INTO agents VALUES ('a1', 'find when the Flourstone Bakehouse opens')")
    conn.commit()
    conn.close()
    return folder


def test_forget_reaches_this_chats_agent_team_and_not_another_chats(isolated_pages):
    chat, other = "team-forget", "team-other"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    own, theirs = _team_folder(chat), _team_folder(other)

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert _holding(own) == []
    assert "They open at 7." in (own / "agents" / "a1" / "result_full.txt").read_text()
    assert len(_holding(theirs)) == 3


def test_a_sealed_backup_file_that_still_holds_the_words_is_said(isolated_pages):
    chat = "backup-sealed"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    sealed = _data() / "update_v2" / "transactions" / "tx-sealed" / "runtime-snapshot" / "data" / "vault.bin"
    sealed.parent.mkdir(parents=True)
    sealed.write_bytes(b"\xff\xfe\x00packed:" + CANARY.encode() + b"\x00\xc3")

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert reply.endswith("In this chat, the forgotten words now stay in VOOL's forget list on this computer, so they are "
                          "never used again, and in an update backup, which could not be changed."), reply
    assert sealed.exists()


@pytest.mark.parametrize("record, kept", [
    ({"session_id": "other-chat", "text": "Flourstone Bakehouse opens at 7"}, True),
    ({"session_id": "this-chat", "text": "Flourstone Bakehouse opens at 7"}, False),
    ({"text": "Flourstone Bakehouse opens at 7"}, False),
    ({"chats": {"a": {"session_id": "other-chat", "text": "Flourstone Bakehouse"},
                "b": {"session_id": "this-chat", "text": "Flourstone Bakehouse"}}}, None),
])
def test_a_backup_record_answers_to_this_chats_forget_only(record, kept):
    from core.memory.copies import _scrubbed_json

    scrubbed, count = _scrubbed_json(record, CANARY, "this-chat")
    if kept is None:
        assert scrubbed["chats"]["a"] == record["chats"]["a"] and scrubbed["chats"]["b"]["text"] == "" and count == 1
    elif kept:
        assert scrubbed == record and count == 0
    else:
        assert scrubbed["text"] == "" and count == 1


def _scrub(token: str, chat: str = "this-chat") -> dict:
    from core.memory.copies import forget_copies

    erasures: dict = {}
    forget_copies(token, chat_id=chat, erasures=erasures)
    return erasures["stores"]


def test_a_word_in_the_backups_own_paths_never_breaks_the_rollback(isolated_pages, tmp_path):
    """Review: "Forget config" blanked the backup's index.json and restore_runtime then failed. The updater's own
    record beside the profile's files is never scrubbed."""
    from core.updater.snapshot import restore_runtime

    _remember(isolated_pages, "path-forget", "the harbor lantern is painted signal green.")
    backup = _backup()
    index = (backup / "index.json").read_text()
    stores = _scrub("config")
    assert stores["update_backups"]["status"] == "verified"
    assert (backup / "index.json").read_text() == index
    restored = tmp_path / "restored-home" / "data"
    restored.mkdir(parents=True)
    restore_runtime(restored, backup)


def test_manual_rollback_backups_escaped_json_and_other_chats_rows(isolated_pages):
    """Review: the manual rollback's copy was missed; JSON written with escaped letters ("Zo\\u00eb") was read as
    not carrying the words; a row owned through origin_chat_id was blanked for another chat; a counter equal to a
    forgotten number was blanked; a unique column could not take a blank."""
    payload = _data() / "update_v2" / "manual_rollbacks" / "tx-manual" / "current-data" / "data"
    payload.mkdir(parents=True)
    (payload / "notes.json").write_text(json.dumps({"note": "Zoë 4417 lives here", "elapsed_ms": 4417}))
    (payload / "events.jsonl").write_text(json.dumps({"session_id": "this-chat", "text": "ask Zoë"}) + "\n"
                                          + json.dumps({"session_id": "other-chat", "text": "ask Zoë"}) + "\n")
    conn = sqlite3.connect(payload / "copy.db")
    conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, origin_chat_id TEXT, body TEXT UNIQUE)")
    conn.execute("INSERT INTO entries (origin_chat_id, body) VALUES ('other-chat', 'Zoë keeps the key'), "
                 "('this-chat', 'Zoë has the key'), ('this-chat', '')")
    conn.commit()
    conn.close()

    stores = _scrub("Zoë")

    assert stores["update_backups"]["status"] == "verified", stores
    notes = json.loads((payload / "notes.json").read_text())
    assert notes == {"note": "", "elapsed_ms": 4417}
    events = [json.loads(line) for line in (payload / "events.jsonl").read_text().splitlines()]
    assert events[0]["text"] == "" and events[1]["text"] == "ask Zoë"
    conn = sqlite3.connect(payload / "copy.db")
    rows = conn.execute("SELECT origin_chat_id, body FROM entries ORDER BY id").fetchall()
    conn.close()
    assert rows == [("other-chat", "Zoë keeps the key"), ("this-chat", "")]  # the row that could not take a blank went


def test_a_number_forget_leaves_counters_and_ids_alone(isolated_pages):
    payload = _data() / "update_v2" / "transactions" / "tx-n" / "runtime-snapshot" / "data"
    payload.mkdir(parents=True)
    (payload / "turns.jsonl").write_text(json.dumps({"created_at": "2026-10-12T10:00:00Z", "elapsed_ms": 12, "step_count": 12,
                                                     "text": "my locker is 12"}) + "\n")
    stores = _scrub("12")
    assert stores["update_backups"]["status"] == "verified"
    assert json.loads((payload / "turns.jsonl").read_text()) == {"created_at": "2026-10-12T10:00:00Z", "elapsed_ms": 12, "step_count": 12, "text": ""}


def test_a_backup_with_a_chunk_and_an_undo_journal_still_restores_and_says_what_it_kept(isolated_pages, tmp_path):
    """Delta review of 130b4b0e: removing a content chunk from a backup made restore_runtime fail (the backup's index
    lists it), and blanking a path in the backup's sealed undo journal made every undo refuse after a restore. The
    chunk is emptied; the journal is never rewritten and is said."""
    from core.updater.snapshot import restore_runtime

    chunk = _data() / "cas_chunks" / "ab" / "cd" / ("abcd" + "0" * 60)
    chunk.parent.mkdir(parents=True)
    chunk.write_text("order bread at the Flourstone Bakehouse")
    journal = _data() / "blackbox" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"seq": 1, "path": "notes/Flourstone Bakehouse.txt", "mac": "x"}) + "\n")
    _remember(isolated_pages, "chunk-forget", "my sister's bakery is called Flourstone Bakehouse.")
    backup = _backup()
    journal.unlink()  # the live journal is not what this test is about

    handled, reply = command(isolated_pages, "chunk-forget", f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert reply.endswith("and in an update backup, which could not be changed."), reply
    copied = backup / "data" / "blackbox" / "journal.jsonl"
    assert _holding(backup) == ["data/blackbox/journal.jsonl"] and json.loads(copied.read_text())["mac"] == "x"
    restored = tmp_path / "restored-home" / "data"
    restored.mkdir(parents=True)
    restore_runtime(restored, backup)


def test_a_key_that_is_the_words_and_a_text_index_with_no_text_of_its_own(isolated_pages):
    payload = _data() / "update_v2" / "transactions" / "tx-k" / "runtime-snapshot" / "data"
    payload.mkdir(parents=True)
    (payload / "labels.json").write_text(json.dumps({"Flourstone Bakehouse": "bread", "harbor": "lantern"}))
    conn = sqlite3.connect(payload / "index.db")
    conn.execute("CREATE VIRTUAL TABLE words USING fts5(body, content='')")
    conn.execute("INSERT INTO words(rowid, body) VALUES (1, 'order at the Flourstone Bakehouse')")
    conn.commit()
    conn.close()

    stores = _scrub(CANARY)

    assert json.loads((payload / "labels.json").read_text()) == {"harbor": "lantern"}
    assert stores["update_backups"]["status"] == "kept", stores  # the index alone holds the words: said, not hidden
