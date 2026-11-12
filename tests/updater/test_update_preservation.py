"""The update journey's preservation check must be able to fail.

``ops/update_preservation.py`` decides whether a real update kept the person's data. A checker that
cannot see a lost row, a regenerated key or a vanished chat message would turn every journey green, so
each kind of loss is made here on a real profile layout and must be reported; growth that migrations
are allowed to make (new tables, columns, rows, files) must not be.
"""
from __future__ import annotations

import copy
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
import update_preservation as keep


def _profile(home: Path) -> Path:
    data = home / "data"
    (data / "keys").mkdir(parents=True)
    (data / "logs").mkdir()
    (data / "keys" / "node_signing_key.json").write_text('{"private": "abc"}')
    (data / "a8_digest_key.hex").write_text("00ff")
    (data / "chat_pins.json").write_text('{"chat-1": {"pinned_at": "2026-11-12T00:00:00Z"}}')
    (data / "logs" / "vool_api.log").write_text("started\n")
    (data / "vool_api.pid").write_text("123")
    with sqlite3.connect(data / "vool.db") as db:
        db.execute("CREATE TABLE chat_import_messages(id TEXT PRIMARY KEY, body TEXT)")
        db.executemany("INSERT INTO chat_import_messages VALUES (?, ?)",
                       [("m1", "Which tomatoes grow best?"), ("m2", "Cherry tomatoes.")])
        db.execute("CREATE TABLE audit_log(event TEXT)")
        db.execute("INSERT INTO audit_log VALUES ('import')")
    return home


def _losses(home: Path, before: dict) -> list[str]:
    return keep.inventory_losses(before, keep.inventory(home))[0]


def test_an_untouched_profile_and_allowed_growth_lose_nothing(tmp_path):
    home = _profile(tmp_path)
    before = keep.inventory(home)
    assert _losses(home, before) == []
    with sqlite3.connect(home / "data" / "vool.db") as db:  # what a migration may do
        db.execute("ALTER TABLE chat_import_messages ADD COLUMN lang TEXT")
        db.execute("INSERT INTO chat_import_messages VALUES ('m3', 'new', 'en')")
        db.execute("CREATE TABLE new_feature(x)")
        db.execute("INSERT INTO audit_log VALUES ('updated')")
    (home / "data" / "new_store.json").write_text("{}")
    (home / "data" / "logs" / "vool_api.log").write_text("restarted\n")  # volatile: never judged
    (home / "data" / "vool_api.pid").write_text("456")
    losses, changed = keep.inventory_losses(before, keep.inventory(home))
    assert losses == []
    assert changed == ["rows changed in place: data/vool.db:chat_import_messages: 2"]


@pytest.mark.parametrize("loss, expected", [
    (lambda h: (h / "data/keys/node_signing_key.json").write_text('{"private": "regenerated"}'),
     "key or secret file changed: data/keys/node_signing_key.json"),
    (lambda h: (h / "data/a8_digest_key.hex").write_text("1111"), "key or secret file changed: data/a8_digest_key.hex"),
    (lambda h: (h / "data/chat_pins.json").unlink(), "file gone: data/chat_pins.json"),
])
def test_a_lost_or_regenerated_file_is_a_loss(tmp_path, loss, expected):
    home = _profile(tmp_path)
    before = keep.inventory(home)
    loss(home)
    assert expected in _losses(home, before)


def test_a_dropped_row_or_table_is_a_loss(tmp_path):
    home = _profile(tmp_path)
    before = keep.inventory(home)
    with sqlite3.connect(home / "data" / "vool.db") as db:
        db.execute("DELETE FROM chat_import_messages WHERE id = 'm2'")
        db.execute("DROP TABLE audit_log")
    losses = _losses(home, before)
    assert any(item.startswith("rows gone: data/vool.db:chat_import_messages: 1 of 2") for item in losses)
    assert "table gone: data/vool.db:audit_log (1 rows)" in losses


def test_a_rewritten_table_without_its_old_rowids_is_a_loss(tmp_path):
    """A table without a primary key is keyed by rowid: a migration that copies it into a fresh table
    keeps the words but renumbers nothing it should not, so dropping rows shows as rowids gone."""
    home = _profile(tmp_path)
    before = keep.inventory(home)
    with sqlite3.connect(home / "data" / "vool.db") as db:
        db.execute("DELETE FROM audit_log")
    assert any(item.startswith("rows gone: data/vool.db:audit_log: 1 of 1") for item in _losses(home, before))


def _view() -> dict:
    return keep.expected_view("/home/person/Documents/Balcony garden")


def test_the_person_view_accepts_more_and_refuses_less():
    before = _view()
    after = copy.deepcopy(before)
    after["chats"]["A chat made after the update"] = [["user", "hi"]]
    after["chats"]["Trip to Vilnius"].append(["user", "one more message"])
    after["facts"]["locale"] = "lt-LT"
    assert keep.view_losses(before, after) == []

    for change, expected in [
        (lambda v: v["chats"].pop("Trip to Vilnius"), "chat gone: 'Trip to Vilnius'"),
        (lambda v: v["chats"]["Garden plan for spring"].pop(1), "chat 'Garden plan for spring' messages differ"),
        (lambda v: v["pinned"].clear(), "no longer pinned: 'Garden plan for spring'"),
        (lambda v: v["projects"].clear(), "project gone"),
        (lambda v: v["prefs"].update(humor_percent=20), "preference humor_percent was 55, now 20"),
        (lambda v: v["facts"].pop("timezone"), "fact timezone was 'Europe/Vilnius', now None"),
    ]:
        lost = copy.deepcopy(before)
        change(lost)
        assert any(item.startswith(expected) for item in keep.view_losses(before, lost)), expected
