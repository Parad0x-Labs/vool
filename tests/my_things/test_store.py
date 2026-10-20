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


def test_forget_erases_matching_text_and_preserves_unrelated_unreadable_files(store):
    store.save({"name": "One"}, files=[file_payload("a.txt", b"erase-CEDAR-8172", "receipt")])
    unknown = store.save({"name": "Two"}, files=[file_payload("unknown.bin", b"\xff\x00opaque", "manual")])
    keep = store.save({"name": "Keep this"})
    preview = store.forget("CEDAR-8172")
    assert preview["requires_confirmation"] and preview["removed"] == 0
    result = store.forget("CEDAR-8172", confirm=True, expected=preview["matches"])
    assert result["status"] == "verified" and result["removed"] == 1
    assert {r["id"] for r in store.list()} == {keep["id"], unknown["id"]}
    assert store.forget("CEDAR-8172")["removed"] == 0
    preview = store.forget("Two")
    result = store.forget("Two", confirm=True, expected=preview["matches"])
    assert result["status"] == "verified" and result["removed"] == 1
    assert [r["id"] for r in store.list()] == [keep["id"]]


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


def test_missing_key_source_refuses_without_a_plaintext_fallback(store):
    from core import credential_store
    from core.my_things.keys import KEY_NAME

    item = store.save({"name": "Keep encrypted", "model": "KK-9221"})
    ciphertext = {p: p.read_bytes() for p in (store.root / "objects").rglob("*") if p.is_file()}
    assert credential_store.delete_credential(KEY_NAME)
    with pytest.raises(ThingsError, match="private storage"):
        store.get(item["id"])
    with pytest.raises(ThingsError, match="private storage"):
        store.save({"name": "never plaintext"})
    assert ciphertext == {p: p.read_bytes() for p in (store.root / "objects").rglob("*") if p.is_file()}
    assert b"never plaintext" not in (store.root / "records.sqlite3").read_bytes()


def test_household_keys_survive_independent_flight_recorder_rotation(store):
    from core.blackbox.coverage.cas_keys import persist_keyring, resolve_keyring

    item = store.save({"name": "Independent record"})
    blackbox = resolve_keyring()
    assert blackbox is not None
    rotated = blackbox.rotated().without_version(blackbox.current)
    persist_keyring(rotated)
    assert store.get(item["id"])["name"] == "Independent record"


@pytest.mark.parametrize("name", ["../secret", "a/b", "a\\b", "\x00", ""])
def test_file_names_cannot_escape_the_packet(store, name):
    with pytest.raises(ThingsError):
        file_payload(name, b"test", "receipt")


def test_invalid_fields_are_refused_without_persistence(store):
    for fields in ({"name": ""}, {"name": "x", "warranty_end": "later"}, {"name": "x", "bought_on": "20260302"}, {"name": "x", "warranty_end": "2026-02-30"}, {"name": "x", "unknown": "x"}):
        with pytest.raises(ThingsError):
            store.save(fields)
    assert not store.root.exists()


def test_symlinked_object_storage_refuses_before_writing_outside_store(store, tmp_path):
    item = store.save({"name": "Safe radio"})
    import shutil
    folder = store.root / "objects" / item["id"]
    shutil.rmtree(folder)
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ThingsError, match="safely"):
        store.save({"name": "Do not write outside"}, thing_id=item["id"], revision=1)
    assert list(outside.iterdir()) == []


def test_forget_normalizes_unicode_and_matches_values_with_quotes(store):
    store.save({"name": 'Ｈｅａｄｐｈｏｎｅｓ "Cedar"'})
    preview = store.forget('headphones "cedar"')
    result = store.forget('headphones "cedar"', confirm=True, expected=preview["matches"])
    assert result["status"] == "verified" and result["removed"] == 1
    assert store.list() == []


def test_stored_png_metadata_is_removed_through_existing_attachment_api(store):
    from PIL import Image, PngImagePlugin
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", "private-metadata-WILLOW-99241")
    raw = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(raw, format="PNG", pnginfo=meta)
    saved = store.save({"name": "Photo"}, files=[file_payload("photo.png", raw.getvalue(), "photo")])
    record = store.get(saved["id"], with_files=True)
    import base64
    assert b"private-metadata-WILLOW-99241" not in base64.b64decode(record["files"][0]["data"])


def test_a_store_cannot_follow_a_profile_switch_with_an_old_root(store, tmp_path, monkeypatch):
    item = store.save({"name": "Bound to this profile"})
    old_home = str(store.home)
    new_home = tmp_path / "other-profile"
    monkeypatch.setenv("VOOL_HOME", str(new_home))
    with pytest.raises(ThingsError, match="profile changed"):
        store.get(item["id"])
    with pytest.raises(ThingsError, match="profile changed"):
        store.save({"name": "Do not cross profiles"})
    assert not new_home.exists()
    monkeypatch.setenv("VOOL_HOME", old_home)
    assert store.get(item["id"])["name"] == "Bound to this profile"


def test_forget_collects_interrupted_encrypted_revisions_and_temporary_copies(store):
    item = store.save({"name": "Current record"})
    with store._tx() as conn:
        record = store._load(conn, item["id"])
        record["notes"] = "obsolete-ORCHARD-47291"
        cas = store._cas(item["id"])
        ref, _ = cas.put(json.dumps(record).encode())
        orphan = cas.path_for_id(ref)
        interrupted = orphan.with_name(".interrupted.tmp")
        interrupted.write_bytes(orphan.read_bytes())
    assert orphan.exists() and interrupted.exists()
    result = store.forget("ORCHARD-47291")
    assert result["status"] == "verified" and result["removed"] == 0
    assert not orphan.exists() and not interrupted.exists()
    assert store.get(item["id"])["name"] == "Current record"
