"""Pictures, Movies, Videos and Music are readable by the machine tools and never writable.

The owner chose read-only media access on 2026-10-28: list, find, read and measure may reach the
media folders; write, create and move stay inside Desktop, Downloads and Documents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import runtime_execution_tools as rt


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents", "Pictures/Holiday Lisbon 2026", "Movies", "Music", ".ssh"):
        (root / name).mkdir(parents=True)
    (root / "Pictures" / "cat_on_sofa.png").write_bytes(b"png")
    (root / "Pictures" / "Holiday Lisbon 2026" / "lisbon_001.jpg").write_bytes(b"jpg")
    (root / "Movies" / "kids_school_play.mov").write_bytes(b"x" * 4096)
    (root / ".ssh" / "id_ed25519").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    return root


def test_reads_resolve_into_the_media_folders(home):
    assert rt._resolve_machine_directory("Pictures/Holiday Lisbon 2026") == home / "Pictures" / "Holiday Lisbon 2026"
    assert rt._resolve_machine_directory("my photos") == home / "Pictures"
    assert rt._resolve_machine_directory("~/Movies") == home / "Movies"


@pytest.mark.parametrize("path", ["Pictures", "Pictures/new", "~/Movies", "Music/a.txt", "my photos"])
def test_writes_never_resolve_into_the_media_folders(home, path):
    with pytest.raises(ValueError):
        rt._resolve_machine_directory(path, write=True)


@pytest.mark.parametrize("path", ["~/.ssh", "/etc", "Library", ".."])
def test_everything_else_stays_refused(home, path):
    with pytest.raises(ValueError):
        rt._resolve_machine_directory(path)


def test_list_find_and_largest_reach_the_media_folders(home):
    listing = rt._list_machine_directory({"path": "Pictures"}, workspace_root=home / "Documents")
    assert listing.ok and "cat_on_sofa.png" in listing.response_text
    found = rt._machine_find_file({"name": "lisbon_001"})
    assert found.ok and "lisbon_001.jpg" in found.response_text
    largest = rt._machine_find_largest({"drive": str(home / "Movies"), "kind": "files"})
    assert largest.ok and "kids_school_play.mov" in largest.response_text


def test_find_never_walks_into_hidden_or_unlisted_folders(home):
    result = rt._machine_find_file({"name": "id_ed25519"})
    assert "No file matching" in result.response_text and str(home / ".ssh") not in result.response_text


def test_a_write_into_pictures_is_refused_by_the_writer(home):
    result = rt._machine_move_path(
        {"source": str(home / "Pictures" / "cat_on_sofa.png"), "destination": str(home / "Desktop" / "cat.png")}
    )
    assert not result.ok
    assert (home / "Pictures" / "cat_on_sofa.png").exists() and not (home / "Desktop" / "cat.png").exists()


def test_the_writer_target_resolver_never_names_a_media_folder(home):
    assert rt.resolve_write_target("machine.write_file", "~/Pictures/new.txt", {}) is None
    assert rt.resolve_write_target("machine.ensure_directory", "pictures", {}) is None
    assert rt.resolve_write_target("machine.write_file", "~/Desktop/new.txt", {}) == home / "Desktop" / "new.txt"


def test_a_pdf_search_never_follows_a_link_out_of_pictures(home, tmp_path):
    from core.agent_runtime import fast_paths_pdf

    outside = tmp_path / "outside" / "secret"
    outside.mkdir(parents=True)
    (outside / "taxes.pdf").write_bytes(b"%PDF-1.4")
    (home / "Pictures" / "Archive").symlink_to(tmp_path / "outside")
    (home / "Pictures" / "taxes_link.pdf").symlink_to(outside / "taxes.pdf")
    assert fast_paths_pdf._find_by_basename("taxes.pdf") is None
    assert fast_paths_pdf._find_by_basename("taxes_link.pdf") is None


def test_the_pdf_search_runs_on_python_3_12(home, tmp_path, monkeypatch):
    # Path.is_dir takes no follow_symlinks before 3.13; the daemon ships on 3.12.
    import os
    import pathlib

    from core.agent_runtime import fast_paths_pdf

    monkeypatch.setattr(pathlib.Path, "is_dir", lambda self: os.path.isdir(self))
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "taxes.pdf").write_bytes(b"%PDF-1.4")
    (home / "Pictures" / "Archive").symlink_to(tmp_path / "outside")
    (home / "Pictures" / "Holiday Lisbon 2026" / "trip.pdf").write_bytes(b"%PDF-1.4")
    assert fast_paths_pdf._find_by_basename("trip.pdf") is not None
    assert fast_paths_pdf._find_by_basename("taxes.pdf") is None


def test_a_media_folder_that_is_a_link_is_not_a_root(home, tmp_path):
    (home / "Music").rmdir()
    (home / "Music").symlink_to(tmp_path)
    assert home / "Music" not in rt._readable_machine_roots()
    assert tmp_path.resolve() not in rt._readable_machine_roots()
    with pytest.raises(ValueError):
        rt._resolve_machine_directory("Music")
