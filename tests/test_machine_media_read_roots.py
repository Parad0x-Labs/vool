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
