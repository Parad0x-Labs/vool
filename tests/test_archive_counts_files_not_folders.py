"""Archive listings count files; folders are said separately, not counted as files.

`zip -r` (and every archiver that stores the folder itself) puts a folder ENTRY in the
archive, so one folder plus three files used to be announced as "4 member(s) in pack.zip"
-- and a folder-only zip claimed members it did not hold. The headline and both meta
counts now say files, folders are mentioned only when any exist, and folders stay in the
listing marked as directories.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

import core.artifact_readers as readers
from core.artifact_readers import archive
from tests.reader_fixtures import _rar_block, _rar_vint


def _zip(folder_entry: bool) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if folder_entry:
            zf.writestr("docs/", b"")  # the directory record `zip -r` writes
        zf.writestr("docs/a.txt", b"alpha\n")
        zf.writestr("docs/b.txt", b"beta\n")
        zf.writestr("docs/c.txt", b"gamma\n")
    return buffer.getvalue()


def _rar_with_folder_entry() -> bytes:
    """A RAR5 holding one folder record and three files, built the way the fixtures build RARs.

    The folder is a zero-byte record named ``docs/`` carrying a unix directory mode: this
    libarchive (bsdtar) lists it as a ``d`` row, which is how the reader detects a folder.
    """
    def record(name: str, payload: bytes, attrs: int = 0) -> bytes:
        encoded = name.encode("utf-8")
        body = (
            _rar_vint(0x0004)
            + _rar_vint(len(payload))
            + _rar_vint(attrs)
            + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
            + _rar_vint(0)
            + _rar_vint(1)
            + _rar_vint(len(encoded))
            + encoded
        )
        return _rar_block(2, 0x0002, body, data_size=len(payload)) + payload

    out = bytearray(b"Rar!\x1a\x07\x01\x00") + _rar_block(1, 0, _rar_vint(0))
    out += record("docs/", b"", attrs=0o40755)
    for name, payload in (("docs/a.txt", b"alpha\n"), ("docs/b.txt", b"beta\n"), ("docs/c.txt", b"gamma\n")):
        out += record(name, payload)
    out += _rar_block(5, 0, _rar_vint(0))
    return bytes(out)


def test_a_zip_with_a_folder_entry_counts_the_three_files():
    result = readers.read(_zip(folder_entry=True), name="pack.zip", extension=".zip")
    listing = result.units[0].text
    assert "3 file(s) in pack.zip (1 folder(s)):" in listing
    # Folders stay listed and marked, so "what is in this archive" keeps its full answer.
    assert "docs/ (directory)" in listing
    assert result.meta["members"] == 3 and result.meta["folders"] == 1
    assert result.units[0].meta["members"] == 3 and result.units[0].meta["folders"] == 1


def test_a_zip_without_folder_entries_does_not_mention_folders():
    result = readers.read(_zip(folder_entry=False), name="pack.zip", extension=".zip")
    listing = result.units[0].text
    assert "3 file(s) in pack.zip:" in listing
    assert "folder" not in listing
    assert result.meta["members"] == 3 and result.meta["folders"] == 0


@pytest.mark.skipif(not archive.decoders_available()["rar"], reason="listing a RAR needs the confined bsdtar decoder")
def test_a_rar_with_a_folder_entry_counts_the_three_files():
    result = readers.read(_rar_with_folder_entry(), name="pack.rar", extension=".rar")
    listing = result.units[0].text
    assert "3 file(s) in pack.rar (1 folder(s)):" in listing
    assert "(directory)" in listing
    assert result.meta["members"] == 3 and result.meta["folders"] == 1
