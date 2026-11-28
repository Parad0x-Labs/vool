"""A home folder the model writes in other letter case is that folder on a disk that ignores case (a Mac's default).

Benchmark on d9a520dca: listing a top-level home folder was refused "I can only inspect safe local directories in this
lane", and the reply said the request got blocked. Folder names here are invented for this contract.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import runtime_execution_tools as rt


def test_a_folder_that_is_the_root_on_disk_resolves_under_its_own_spelling(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Receipts" / "2026").mkdir(parents=True)
    root = (home / "Receipts").resolve()
    monkeypatch.setattr(os.path, "samefile", lambda a, b: Path(a).name.casefold() == Path(b).name.casefold())
    monkeypatch.setattr(Path, "exists", lambda self: True)
    assert rt._root_spelled_otherwise(home.resolve() / "receipts" / "2026", (root,)) == root / "2026"


def test_a_folder_that_only_shares_letters_with_a_root_is_not_it(tmp_path):
    home = tmp_path / "home"
    (home / "Receipts").mkdir(parents=True)
    (home / "receipts-old").mkdir()
    root = (home / "Receipts").resolve()
    assert rt._root_spelled_otherwise(home.resolve() / "receipts-old", (root,)) is None
    # On a disk that keeps case apart, "receipts" is not "Receipts".
    if not (home / "receipts").exists():
        assert rt._root_spelled_otherwise(home.resolve() / "receipts", (root,)) is None


@pytest.mark.parametrize("name", ["receipts", "RECEIPTS"])
def test_a_listing_of_a_home_folder_in_other_case_is_not_refused(name, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Receipts").mkdir(parents=True)
    (home / "Receipts" / "march.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    real_samefile = os.path.samefile
    monkeypatch.setattr(os.path, "samefile", lambda a, b: Path(a).name.casefold() == Path(b).name.casefold()
                        or real_samefile(a, b))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: real_exists(self) or real_exists(self.parent / "Receipts"))
    assert rt._resolve_safe_machine_directory(f"~/{name}") == (home / "Receipts").resolve()
