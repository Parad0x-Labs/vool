"""A file name typed with its accents, or with spaces where the file has dashes, finds the file.

iMac gate on 7ce02be05 (a chat bound to a project folder holding kelioniu-planas-417.txt): "surask failą kelionių planas
417" answered "not found". The letters-and-digits comparison knew only a to z, so the "ų" was dropped from the ask and
the rest never met the "u" on disk. Names, accents, separators and folders are drawn at random, in several languages.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

# (as typed, as on disk without accents)
WORDS = (
    ("kelionių", "kelioniu"), ("planas", "planas"), ("Ölwechsel", "olwechsel"), ("señal", "senal"),
    ("łódź", "lodz"), ("résumé", "resume"), ("žiema", "ziema"), ("Straße", "strasse"), ("café", "cafe"),
    ("ąžuolas", "azuolas"), ("übersicht", "ubersicht"), ("niño", "nino"),
)
SEPARATORS = (" ", "-", "_", ".")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.parametrize("seed", range(12))
def test_a_name_typed_with_accents_and_other_separators_finds_the_plain_file(seed, home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    rng = random.Random(f"folded-find-{seed}")
    picked = rng.sample(WORDS, rng.randint(1, 3))
    number = str(rng.randint(10, 999))
    on_disk = rng.choice(SEPARATORS[1:]).join([plain for _typed, plain in picked] + [number]) + ".txt"
    typed = rng.choice([" ", "-", "_"]).join([said for said, _plain in picked] + [number])
    folder = home / rng.choice(["proj", "Documents", "kelione"])
    folder.mkdir()
    (folder / on_disk).write_text("x")
    (folder / "kita-pastaba-1.txt").write_text("y")

    found = _machine_find_file({"name": typed})

    assert [Path(p).name for p in found.details["matches"]] == [on_disk], (typed, found.response_text)


@pytest.mark.parametrize("seed", range(6))
def test_a_plain_name_typed_finds_the_file_saved_with_accents(seed, home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    rng = random.Random(f"folded-disk-{seed}")
    picked = rng.sample(WORDS, 2)
    on_disk = "_".join(typed for typed, _plain in picked) + ".pdf"
    typed = " ".join(plain for _typed, plain in picked)
    (home / "Documents").mkdir()
    (home / "Documents" / on_disk).write_text("x")

    found = _machine_find_file({"name": typed})

    assert [Path(p).name for p in found.details["matches"]] == [on_disk], (typed, found.response_text)


def test_a_name_in_another_script_still_matches_by_its_words(home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    (home / "Documents").mkdir()
    (home / "Documents" / "отпуск_план_2027.txt").write_text("x")
    (home / "Documents" / "другое.txt").write_text("y")
    found = _machine_find_file({"name": "отпуск план 2027"})
    assert [Path(p).name for p in found.details["matches"]] == ["отпуск_план_2027.txt"], found.response_text


def test_a_word_inside_another_word_is_still_not_a_match(home) -> None:
    from core.runtime_execution_tools import _machine_find_file

    (home / "Documents").mkdir()
    (home / "Documents" / "holiday4.txt").write_text("x")
    assert _machine_find_file({"name": "day 4"}).details["matches"] == []
