from __future__ import annotations

import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from core.my_things.store import ThingsError, ThingsStore, file_payload


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "things-home"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    return ThingsStore()


def test_empty_store_does_not_create_files_or_keys(store):
    assert store.list() == []
    assert store.preferences() == {"enabled": True}
    assert not store.root.exists()


def test_encrypted_records_edit_export_stale_and_delete(store):
    marker = "Synthetic receipt REF-64921"
    file = file_payload("receipt.txt", marker.encode(), "receipt")
    item = store.save({"name": "Headphones", "model": "WH-1000", "warranty_end": "2027-03-02"}, files=[file])
    assert item["revision"] == 1 and "data" not in item["files"][0]
    assert all(marker.encode() not in f.read_bytes() and b"WH-1000" not in f.read_bytes() for f in store.root.rglob("*") if f.is_file())
    for path in store.root.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
    saved = store.save({"name": "Headphones updated", "model": "WH-1000"}, thing_id=item["id"], revision=1)
    assert saved["revision"] == 2
    with pytest.raises(ThingsError, match="changed"):
        store.delete(item["id"], 1)
    with zipfile.ZipFile(io.BytesIO(store.export())) as archive:
        assert marker.encode() in [archive.read(n) for n in archive.namelist()]
        assert json.loads(archive.read("things.json"))[0]["name"] == "Headphones updated"
    store.delete(item["id"], 2)
    assert store.list() == []
    assert not list((store.root / "objects").rglob("?" * 64))
    assert all(marker.encode() not in f.read_bytes() for f in store.root.rglob("*") if f.is_file())
    conn = sqlite3.connect(store.root / "records.sqlite3")
    assert conn.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    conn.close()


def test_forget_scans_file_text_and_conservatively_erases_unreadable_binary(store):
    store.save({"name": "One"}, files=[file_payload("a.txt", b"erase-CEDAR-8172", "receipt")])
    store.save({"name": "Two"}, files=[file_payload("label.png", b"\x89PNG\r\n", "photo")])
    keep = store.save({"name": "Keep this"})
    assert store.forget("CEDAR-8172") == {"status": "verified", "removed": 2}
    assert [r["id"] for r in store.list()] == [keep["id"]]
    assert store.forget("CEDAR-8172")["removed"] == 0


def test_disabled_store_refuses_reads_and_writes_but_allows_deletion(store):
    item = store.save({"name": "Radio"})
    store.set_enabled(False)
    with pytest.raises(ThingsError, match="turned off"):
        store.list()
    with pytest.raises(ThingsError, match="turned off"):
        store.save({"name": "Other"})
    assert store.delete(item["id"], 1)["deleted"]
    store.set_enabled(True)
    assert store.list() == []


def test_missing_key_source_refuses_without_a_plaintext_fallback(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(tmp_path / "absent"))
    with pytest.raises(ThingsError, match="private storage"):
        store.save({"name": "never plaintext"})
    assert b"never plaintext" not in (store.root / "records.sqlite3").read_bytes()


@pytest.mark.parametrize("name", ["../secret", "a/b", "a\\b", "\x00", ""])
def test_file_names_cannot_escape_the_packet(store, name):
    with pytest.raises(ThingsError):
        file_payload(name, b"test", "receipt")


def test_invalid_fields_are_refused_without_persistence(store):
    for fields in ({"name": ""}, {"name": "x", "warranty_end": "later"}, {"name": "x", "unknown": "x"}):
        with pytest.raises(ThingsError):
            store.save(fields)
    assert not store.root.exists()
