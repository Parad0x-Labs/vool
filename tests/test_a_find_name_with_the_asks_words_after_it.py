"""A file name the model wrote with the ask's next words still on it is found by its first words.

App from source on qwen3:8b (2026-11-24, a chat bound to a project folder holding atostogos-892.txt): "can u check if
i've got a file named atostogos-892 around here, and where" was called as machine.find_file {"name": "atostogos-892
around here"} and answered "No file matching ... found". Names, trailing words and languages are drawn at random.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

NAMES = ("atostogos-892", "kita_pastaba", "budget.q3", "urlaub-plan-7", "factura339")
TAILS = ("around here", "somewhere", "on this laptop pls", "kur nors", "irgendwo hier", "por aquí")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.parametrize("seed", range(8))
def test_a_name_with_words_after_it_finds_the_file_and_says_by_what(seed, home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    rng = random.Random(f"find-tail-{seed}")
    name = rng.choice(NAMES)
    folder = home / rng.choice(["proj", "Documents", "work-stuff"])
    folder.mkdir()
    (folder / f"{name}.txt").write_text("x")
    (folder / "other-note.txt").write_text("y")
    asked = f"{name} {rng.choice(TAILS)}"

    found = _machine_find_file({"name": asked})

    assert [Path(p).name for p in found.details["matches"]] == [f"{name}.txt"], found.response_text
    assert asked in found.response_text and f"matching '{name}'" in found.response_text, found.response_text


def test_a_plain_first_word_is_never_searched_alone(home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    (home / "Documents").mkdir()
    (home / "Documents" / "notes.txt").write_text("x")
    missing = _machine_find_file({"name": "notes from the boat trip"})
    assert missing.details["matches"] == [], missing.response_text
    assert "No file matching 'notes from the boat trip'" in missing.response_text
