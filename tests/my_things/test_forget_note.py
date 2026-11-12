"""Chat Forget and saved household records: say what else holds the words, never delete it.

Forget erases memory. Saved things and contacts are the person's own records; their own pages
delete them, with confirmation. When the forgotten words are also in such records, the forget
reply says how many and where; with no match the reply is the standard one. A record store that
cannot be read never blocks forgetting memory: the reply says it could not be checked. Records
match by the memory forget matcher, so both agree on what "matches" means.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.copies import FORGET_LIST_NOTE
from core.memory.entries import resolve_memory_access_policy
from core.memory.files import memory_path
from core.my_things.store import ThingsStore, record_text_carries
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import configure_runtime_home

CHAT = "things-forget-note-chat"
NOTE_ONE = ("The same words are in 1 saved thing in My things. Forget keeps saved records; "
            "delete it there to remove it and its files.")


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime-home"
    monkeypatch.setenv("VOOL_HOME", str(runtime))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    configure_runtime_home(runtime)
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    yield runtime
    configure_runtime_home(None)


def _say(text: str) -> str:
    ensure_chat_namespace(CHAT, grant_confirmed_profile=True)
    handled, reply = maybe_handle_memory_command(
        text, session_id=CHAT, access_policy=resolve_memory_access_policy(chat_id=CHAT))
    assert handled, text
    return reply


def test_forget_names_a_matching_saved_thing_and_keeps_it(home: Path) -> None:
    item = ThingsStore().save({"name": "Pine lamp", "model": "PL-7731"})
    assert _say("Remember that the Pine lamp came from the corner shop").startswith("Locked in")

    reply = _say("Forget Pine lamp")

    assert reply.startswith("Forget applied. Removed 1 memory entr"), reply
    assert reply.endswith(FORGET_LIST_NOTE + " " + NOTE_ONE), reply
    assert "Pine lamp" not in memory_path().read_text(encoding="utf-8")
    # The record is the person's own: forget points at it, the My things page deletes it.
    assert [t["id"] for t in ThingsStore().list()] == [item["id"]]


def test_forget_counts_things_and_contacts_together(home: Path) -> None:
    from core.contacts import store as contacts

    ThingsStore().save({"name": "Workshop drill", "notes": "lent to Harbor-5530"})
    ThingsStore().save({"name": "Harbor-5530 ladder"})
    contacts.create_contact(display_name="Rowan Vale", notes="Harbor-5530 workshop", actor=contacts.ACTOR_OWNER)

    reply = _say("Forget Harbor-5530")

    assert reply.endswith("The same words are in 2 saved things in My things and 1 saved contact in Contacts. "
                          "Forget keeps saved records; delete them there to remove them and their files."), reply
    assert len(ThingsStore().list()) == 2 and len(contacts.active_contacts()) == 1


def test_forget_without_a_matching_record_keeps_the_standard_reply(home: Path) -> None:
    ThingsStore().save({"name": "Kettle", "model": "KT-2"})
    _say("Remember that my locker code is 4417")

    reply = _say("Forget 4417")

    assert reply.startswith("Forget applied. Removed 1 memory entr"), reply
    assert reply.endswith(FORGET_LIST_NOTE), reply
    assert "The same words" not in reply and "could not check" not in reply


def test_forget_with_no_records_store_at_all_keeps_the_standard_reply(home: Path) -> None:
    _say("Remember that the garden gate code is 9930")

    reply = _say("Forget 9930")

    assert reply.endswith(FORGET_LIST_NOTE) and "The same words" not in reply, reply
    assert not (home / "data" / "my_things").exists(), "a forget must not create the records store"


def test_an_unreadable_records_store_never_blocks_forget(home: Path) -> None:
    _say("Remember that the boat key hangs by the door 5521")
    store = home / "data" / "my_things"
    store.mkdir(parents=True)
    broken = b"this is not a database" * 64
    (store / "records.sqlite3").write_bytes(broken)

    reply = _say("Forget 5521")

    assert reply.startswith("Forget applied. Removed 1 memory entr"), reply
    assert reply.endswith("Forget could not check your saved things in My things for these words."), reply
    assert "5521" not in memory_path().read_text(encoding="utf-8")
    assert (store / "records.sqlite3").read_bytes() == broken, "forget never repairs or deletes the records store"


def test_a_forget_that_did_not_go_through_says_nothing_about_saved_records(home: Path) -> None:
    # A chat document holding the words cannot be read, so the forget is not verified as applied.
    import os

    from core.chat_attachments import bind_to_turn, stage_dir, stage_document

    ThingsStore().save({"name": "Pine lamp", "model": "PL-7731"})
    ensure_chat_namespace(CHAT, grant_confirmed_profile=True)
    text = "The Pine lamp model is PL-7731.\n" + "".join(f"Shelf note {n:03d}: spare bulbs.\n" for n in range(200))
    record = stage_document(session_id=CHAT, data=text.encode("utf-8"), declared_name="lamp.txt")
    bind_to_turn(session_id=CHAT, turn_id="t1", attachment_ids=[record["id"]])
    path = stage_dir() / f"{record['id']}.bin"
    held = path.with_name("held-elsewhere.bin")
    path.rename(held)
    os.symlink(held, path)

    reply = _say("Forget PL-7731")

    assert reply.startswith("Forget could not be verified as fully applied: "), reply
    assert "My things" not in reply and "saved records" not in reply and "could not check" not in reply, reply


def test_numbers_match_records_the_way_forget_matches_memory(home: Path) -> None:
    # "forget 12" never erases "room 312" from memory, so it never points at a thing saved as "Room 312 lamp".
    ThingsStore().save({"name": "Room 312 lamp"})
    _say("Remember that the spare key number is 12")

    reply = _say("Forget 12")

    assert reply.startswith("Forget applied"), reply
    assert "The same words" not in reply, reply


def test_records_match_by_the_memory_forget_matcher() -> None:
    # A phone keyboard's wide letters are the same words; separators are not meaning.
    assert record_text_carries("ＫＸ－２０４１ kettle", "KX-2041")
    assert record_text_carries("KX 2041 kettle", "kx-2041")
    # A composed and a decomposed spelling stay distinct, exactly as memory forget keeps them.
    assert not record_text_carries("Café table", "Café")
    assert not record_text_carries("Room 312 lamp", "12")


def test_the_note_never_repeats_the_forgotten_words(home: Path) -> None:
    ThingsStore().save({"name": "Cedar-9921 chair", "notes": "bought with Cedar-9921 voucher"})
    _say("Remember that my voucher is Cedar-9921")

    reply = _say("Forget Cedar-9921")

    note = reply.split(FORGET_LIST_NOTE, 1)[1]
    assert note.strip() == NOTE_ONE, reply
    assert "cedar" not in note.casefold() and "9921" not in note, note
