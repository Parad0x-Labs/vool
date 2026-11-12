"""Which folder a routine may work in: the places VOOL's own file moves may write, nothing else."""
from __future__ import annotations

import os

import pytest

from core.routines.folders import SAFE_NAMES, admit, inside
from core.routines.model import RoutineError


def refused(text, code):
    with pytest.raises(RoutineError) as caught:
        admit(text)
    assert caught.value.code == code, (text, caught.value.code)


def test_the_safe_places_are_the_ones_vools_file_moves_use():
    from core.runtime_execution_tools import _SAFE_MACHINE_DIRECTORY_NAMES

    assert tuple(SAFE_NAMES) == tuple(_SAFE_MACHINE_DIRECTORY_NAMES)


def test_desktop_downloads_documents_and_folders_inside_them(person):
    for name in SAFE_NAMES:
        assert admit(str(person / name)) == (person / name).resolve()
    (person / "Downloads" / "Scans" / "2026").mkdir(parents=True)
    assert admit("~/Downloads/Scans/2026") == (person / "Downloads" / "Scans" / "2026").resolve()
    assert admit(str(person / "Downloads" / "Scans" / ".." / "Scans")) == (person / "Downloads" / "Scans").resolve()


def test_everything_else_is_refused_with_a_plain_reason(person, tmp_path):
    refused("", "no_folder")
    refused("   ", "no_folder")
    refused("Downloads", "bad_folder")  # not a full path
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refused(str(elsewhere), "bad_folder")
    refused(str(person), "bad_folder")  # the home folder itself
    refused(str(person / "Downloads" / ".." / ".."), "bad_folder")
    (person / "Downloads" / ".private" / "inner").mkdir(parents=True)
    refused(str(person / "Downloads" / ".private"), "bad_folder")
    refused(str(person / "Downloads" / ".private" / "inner"), "bad_folder")
    os.symlink(elsewhere, person / "Downloads" / "link")
    refused(str(person / "Downloads" / "link"), "bad_folder")
    os.symlink(person / "Documents", person / "Downloads" / "docs-link")
    refused(str(person / "Downloads" / "docs-link"), "bad_folder")  # even a link to a safe place
    (person / "Downloads" / "file.pdf").write_bytes(b"%PDF")
    refused(str(person / "Downloads" / "file.pdf"), "bad_folder")
    refused(str(person / "Downloads" / "gone"), "folder_missing")


def test_vools_own_home_is_never_a_routine_folder(person, tmp_path, monkeypatch):
    own = person / "Documents" / "VOOL"
    (own / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(own))
    refused(str(own), "bad_folder")
    refused(str(own / "data"), "bad_folder")
    # Its parent would let a run move VOOL's own data: refused too.
    refused(str(person / "Documents"), "bad_folder")
    assert admit(str(person / "Downloads")) == (person / "Downloads").resolve()


def test_inside_follows_links_to_where_they_really_point(person, tmp_path):
    folder = (person / "Downloads").resolve()
    (folder / "a").mkdir()
    assert inside(folder, folder / "a")
    assert inside(folder, folder)
    assert not inside(folder, folder / ".." / "Documents")
    os.symlink(tmp_path, folder / "out")
    assert not inside(folder, folder / "out")
