"""The protected media library and its USB backup: sealing, sections, incremental copy, unplug, full drive,
tampering, mirrored removals and restore."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
from pathlib import Path

import pytest

from core.media_library import backup as mb
from core.media_library import sealed
from core.media_library.store import Library, LibraryError

KEY = bytes(range(32))
MARK = b"VOOL-PLAIN-MARKER-"


def _bytes(n: int, seed: int = 1) -> bytes:
    head = MARK + str(seed).encode() + b"-"
    body = hashlib.shake_256(str(seed).encode()).digest(max(0, n - len(head)))
    return (head + body)[:n]


def _lib(tmp_path: Path, name: str = "lib") -> Library:
    return Library(tmp_path / name / "media_library.noindex", key=KEY)


def _add(lib: Library, data: bytes, *, name: str = "a.jpg", section: str = "local", taken_at: float = 1000.0, media_type: str = ""):
    return lib.add([data[i:i + 300_000] for i in range(0, len(data), 300_000)], section=section, name=name,
                   media_type=media_type, taken_at=taken_at)


def _all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# --- the sealed format ---------------------------------------------------------------------------------------------

def test_sealed_round_trip_ranges_and_segments(tmp_path):
    data = _bytes(sealed.PIECE * 3 + 12345, seed=7)
    first = tmp_path / "x.vmo"
    size, sha, paths = sealed.seal([data], first, wrap_key=KEY, ident="x", segment_limit=sealed.SEALED_PIECE * 2)
    assert size == len(data) and sha == hashlib.sha256(data).hexdigest()
    assert len(paths) == 3 and all(p.stat().st_size <= sealed.SEALED_PIECE * 2 for p in paths)
    assert sum(p.stat().st_size for p in paths) == sealed.sealed_size(len(data))
    opened = sealed.Opened(first, wrap_key=KEY, ident="x")
    assert b"".join(opened.chunks()) == data
    assert b"".join(opened.range(sealed.PIECE - 5, sealed.PIECE * 2 + 9)) == data[sealed.PIECE - 5: sealed.PIECE * 2 + 9]
    assert all(MARK not in p.read_bytes() for p in paths)


@pytest.mark.parametrize("damage", ["flip", "cut_last", "drop_middle", "wrong_ident", "wrong_key"])
def test_sealed_refuses_changed_files(tmp_path, damage):
    data = _bytes(sealed.PIECE * 3 + 10)
    first = tmp_path / "x.vmo"
    sealed.seal([data], first, wrap_key=KEY, ident="x")
    raw = bytearray(first.read_bytes())
    ident, key = "x", KEY
    if damage == "flip":
        raw[sealed.HEADER + 100] ^= 1
    elif damage == "cut_last":
        raw = raw[: sealed.HEADER + 3 * sealed.SEALED_PIECE]
    elif damage == "drop_middle":
        raw = raw[: sealed.HEADER + sealed.SEALED_PIECE] + raw[sealed.HEADER + 2 * sealed.SEALED_PIECE:]
    elif damage == "wrong_ident":
        ident = "y"
    else:
        key = bytes(32)
    first.write_bytes(bytes(raw))
    with pytest.raises(sealed.SealedError):
        sealed.verify(first, wrap_key=key, ident=ident)


# --- the library ---------------------------------------------------------------------------------------------------

def test_library_sections_kinds_sorting_and_nothing_plain_on_disk(tmp_path):
    lib = _lib(tmp_path)
    a = _add(lib, _bytes(50_000, 1), name="beach.jpg", section="mobile", taken_at=300)
    b = _add(lib, _bytes(900_000, 2), name="talk.m4a", section="mobile", taken_at=100)
    c = _add(lib, _bytes(2_500_000, 3), name="party.mp4", section="local", taken_at=200)
    assert [m["id"] for m in lib.items(section="mobile")] == [a["id"], b["id"]]
    assert [m["id"] for m in lib.items(sort="size")] == [c["id"], b["id"], a["id"]]
    assert [m["kind"] for m in (a, b, c)] == ["photo", "audio", "video"]
    assert lib.items(section="mobile", kind="audio")[0]["name"] == "talk.m4a"
    assert lib.root.name.endswith(".noindex") and (lib.root / ".metadata_never_index").exists()
    for path in _all_files(lib.root):
        blob = path.read_bytes()
        assert MARK not in blob and b"beach" not in blob and b"party" not in blob
    fresh = Library(lib.root, key=KEY)  # names, dates and sizes come back from the sealed notes
    assert {m["name"] for m in fresh.items()} == {"beach.jpg", "talk.m4a", "party.mp4"}
    assert b"".join(fresh.open(c["id"]).chunks()) == _bytes(2_500_000, 3)


def test_library_dedupes_by_content_and_removes_for_good(tmp_path):
    lib = _lib(tmp_path)
    data = _bytes(400_000, 9)
    first = _add(lib, data, section="mobile")
    again = lib.add([data], section="mobile", name="other.jpg", media_type="image/jpeg", taken_at=5,
                    expect_sha256=hashlib.sha256(data).hexdigest())
    assert again["id"] == first["id"] and len(lib.items()) == 1
    with pytest.raises(LibraryError):
        lib.add([data], section="mobile", name="x.jpg", media_type="image/jpeg", taken_at=5, expect_sha256="0" * 64)
    lib.remove(first["id"])
    assert lib.items() == [] and not [p for p in lib.items_dir.iterdir() if p.name.startswith(first["id"])]


def test_upload_in_slices_is_sealed_while_it_waits(tmp_path):
    lib = _lib(tmp_path)
    data = _bytes(1_700_000, 4)
    upload = lib.upload_begin(name="clip.mov", media_type="", size=len(data), taken_at=42)
    offset = lib.upload_piece(upload, 0, data[:800_000])
    with pytest.raises(LibraryError):
        lib.upload_piece(upload, 0, data[:10])
    assert all(MARK not in p.read_bytes() for p in _all_files(lib.uploads_dir))
    lib.upload_piece(upload, offset, data[800_000:])
    item = lib.upload_finish(upload)
    assert item["kind"] == "video" and item["section"] == "local" and item["size"] == len(data)
    assert not any(lib.uploads_dir.iterdir())


def test_unsupported_files_are_refused(tmp_path):
    with pytest.raises(LibraryError):
        _add(_lib(tmp_path), b"MZ....", name="tool.exe")


# --- the USB backup ------------------------------------------------------------------------------------------------

def _drive(tmp_path: Path) -> Path:
    drive = tmp_path / "USB"
    drive.mkdir()
    return drive / mb.FOLDER


def _filled(tmp_path: Path, n: int = 3) -> Library:
    lib = _lib(tmp_path)
    for i in range(n):
        _add(lib, _bytes(300_000 + i * 700_000, 10 + i), name=f"p{i}.jpg", section="mobile" if i % 2 else "local", taken_at=100 + i)
    return lib


def test_backup_is_incremental_checked_and_restorable(tmp_path):
    lib = _filled(tmp_path)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    first = mb.backup(lib, vault)
    assert first["copied"] == 3 and not first["stopped"] and first["left"] == 0
    again = mb.backup(lib, mb.Vault.open(vault.folder, password="a long backup password"))
    assert again["copied"] == 0 and again["already"] == 3
    _add(lib, _bytes(123_456, 99), name="new.jpg")
    assert mb.backup(lib, vault)["copied"] == 1
    assert mb.check(vault) == {"ok": 4, "damaged": [], "total": 4}
    for path in _all_files(vault.folder):
        assert MARK not in path.read_bytes() and b"p1.jpg" not in path.read_bytes()
    other = _lib(tmp_path, "other-computer")
    restored = mb.restore(other, mb.Vault.open(vault.folder, password="a long backup password"))
    assert restored == {"restored": 4, "already": 0, "damaged": [], "stopped": ""}
    assert sorted((m["sha256"], m["name"], m["section"]) for m in other.items()) == sorted((m["sha256"], m["name"], m["section"]) for m in lib.items())


def test_wrong_password_and_short_password(tmp_path):
    folder = _drive(tmp_path)
    with pytest.raises(mb.BackupStop) as short:
        mb.Vault.create(folder, "short")
    assert short.value.code == "password_short"
    mb.Vault.create(folder, "a long backup password")
    with pytest.raises(mb.BackupStop) as wrong:
        mb.Vault.open(folder, password="not the right password")
    assert wrong.value.code == "wrong_password"


def test_tampered_object_fails_check_and_is_not_restored(tmp_path):
    lib = _filled(tmp_path)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    mb.backup(lib, vault)
    target = next(e for e in vault.manifest()["objects"].items() if e[1]["name"] == "p1.jpg")
    path = vault.object_path(target[0])
    raw = bytearray(path.read_bytes())
    raw[sealed.HEADER + 5000] ^= 0x40
    path.write_bytes(bytes(raw))
    assert mb.check(vault)["damaged"] == ["p1.jpg"]
    other = _lib(tmp_path, "other")
    report = mb.restore(other, vault)
    assert report["restored"] == 2 and report["damaged"] == ["p1.jpg"]
    assert "p1.jpg" not in {m["name"] for m in other.items()}


def test_tampered_manifest_stops_with_damaged(tmp_path):
    lib = _filled(tmp_path, 1)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    mb.backup(lib, vault)
    raw = bytearray((vault.folder / "manifest.vmb").read_bytes())
    raw[-3] ^= 1
    (vault.folder / "manifest.vmb").write_bytes(bytes(raw))
    with pytest.raises(mb.BackupStop) as stop:
        mb.check(vault)
    assert stop.value.code == "backup_damaged"


def test_unplugged_mid_copy_stops_and_the_next_run_carries_on(tmp_path, monkeypatch):
    lib = _filled(tmp_path)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    real_seal = sealed.seal
    calls = {"n": 0}

    def unplug_on_second(source, first, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            first.write_bytes(b"half written")  # a part file left behind, as a pulled stick would
            raise OSError(errno.EIO, "I/O error")
        return real_seal(source, first, **kw)

    monkeypatch.setattr(mb.sealed, "seal", unplug_on_second)
    report = mb.backup(lib, vault)
    assert report["stopped"] == "drive_gone" and report["copied"] == 1 and report["left"] == 2
    assert len(vault.manifest()["objects"]) == 1
    monkeypatch.setattr(mb.sealed, "seal", real_seal)
    (vault.objects / "leftover.vmo.part").write_bytes(b"x")
    later = mb.backup(lib, vault)
    assert later["copied"] == 2 and not later["stopped"] and later["left"] == 0
    assert not list(vault.objects.glob("*.part*"))
    assert mb.check(vault)["ok"] == 3


def test_a_complete_object_not_yet_listed_is_checked_and_taken_over(tmp_path):
    lib = _filled(tmp_path, 2)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    mb.backup(lib, vault)
    manifest = vault.manifest()
    manifest["objects"].popitem()
    vault.save_manifest(manifest)  # as if the drive was pulled between the rename and the list
    report = mb.backup(lib, vault)
    assert report["adopted"] == 1 and report["copied"] == 0 and len(vault.manifest()["objects"]) == 2


@pytest.mark.parametrize("how", ["free_space", "enospc"])
def test_full_drive_stops_cleanly(tmp_path, monkeypatch, how):
    lib = _filled(tmp_path)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    if how == "free_space":
        budget = {"left": 2}
        monkeypatch.setattr(mb, "_free", lambda folder: 10 ** 12 if (budget.__setitem__("left", budget["left"] - 1) or budget["left"] >= 0) else 1000)
    else:
        real_seal = sealed.seal
        calls = {"n": 0}

        def no_space(source, first, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_seal(source, first, **kw)

        monkeypatch.setattr(mb.sealed, "seal", no_space)
    report = mb.backup(lib, vault)
    assert report["stopped"] == "drive_full" and report["copied"] == 2 and report["left"] == 1
    assert not list(vault.objects.glob("*.part*"))
    assert mb.check(vault) == {"ok": 2, "damaged": [], "total": 2}


def test_removals_are_kept_unless_mirroring_is_on_and_the_count_confirmed(tmp_path):
    lib = _filled(tmp_path)
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    mb.backup(lib, vault)
    gone = lib.items()[0]
    lib.remove(gone["id"])
    assert mb.plan(lib, vault)["removed_here"] == 1
    kept = mb.backup(lib, vault)
    assert kept["removed"] == 0 and len(vault.manifest()["objects"]) == 3
    asked = mb.backup(lib, vault, mirror_removals=True)
    assert asked["stopped"] == "confirm_removals" and asked["removed_here"] == 1
    wrong = mb.backup(lib, vault, mirror_removals=True, confirm_removals=2)
    assert wrong["stopped"] == "confirm_removals"
    done = mb.backup(lib, vault, mirror_removals=True, confirm_removals=1)
    assert done["removed"] == 1 and len(vault.manifest()["objects"]) == 2
    assert gone["sha256"] not in {e["sha256"] for e in vault.manifest()["objects"].values()}


def test_big_items_are_split_for_fat32_sticks(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "SEGMENT", sealed.SEALED_PIECE * 2)
    lib = _lib(tmp_path)
    data = _bytes(sealed.PIECE * 5 + 77, 5)
    _add(lib, data, name="long.mp4")
    vault = mb.Vault.create(_drive(tmp_path), "a long backup password")
    assert mb.backup(lib, vault)["copied"] == 1
    oid = next(iter(vault.manifest()["objects"]))
    segments = sealed.segment_paths(vault.object_path(oid))
    assert len(segments) == 4 and all(p.stat().st_size <= sealed.SEALED_PIECE * 2 for p in segments)
    other = _lib(tmp_path, "other")
    assert mb.restore(other, vault)["restored"] == 1
    assert b"".join(other.open(other.items()[0]["id"]).chunks()) == data
