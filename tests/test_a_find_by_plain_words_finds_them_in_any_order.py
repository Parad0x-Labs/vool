"""A find by plain words finds the file that holds every one of them, in whatever order the name has them.

iMac gate36 on 42d3f4c44 (Offline, qwen3:8b): the model looked for "pension letter" and the file was
letter-from-my-company-pension.txt: "No file matching". The words-in-any-order look-up ran only when the name the
model wrote was not already plain words. Names here are fresh; a name missing one of the words is never found.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

_FILES = (
    ("receipt from the bike shop.pdf", "bike receipt"),
    ("notes-on-the-garden-plan.txt", "garden notes"),
    ("2026_insurance_house_renewal.docx", "house insurance renewal"),
    ("menu for sunday lunch.txt", "lunch menu"),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    for folder in ("Desktop", "Documents", "Downloads"):
        (tmp_path / folder).mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.parametrize("seed", range(4))
def test_plain_words_in_another_order_find_the_file(home, seed):
    from core.runtime_execution_tools import _machine_find_file

    rng = random.Random(f"any-order-{seed}")
    on_disk, asked = _FILES[seed]
    (home / rng.choice(("Desktop", "Documents", "Downloads")) / on_disk).write_text("x")
    (home / "Documents" / "unrelated-letter.txt").write_text("x")
    found = _machine_find_file({"name": asked})
    assert [Path(p).name for p in found.details["matches"]] == [on_disk], found.response_text


def test_a_name_missing_one_of_the_words_is_not_found(home):
    from core.runtime_execution_tools import _machine_find_file

    (home / "Documents" / "letter-about-the-car.txt").write_text("x")
    missed = _machine_find_file({"name": "pension letter"})
    assert missed.details["matches"] == [], missed.response_text
