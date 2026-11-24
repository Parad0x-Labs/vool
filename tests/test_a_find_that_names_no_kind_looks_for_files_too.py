"""A find that does not say "folder" looks for files of that name too, and says when it could not.

CI on 4b39b40d5 (2026-11-24, setup first tries): "can u find budget-8bb7 on my computer" was read as a
FOLDER search because only the this-host phrase marked it. The folder walk found nothing, the file
half ran as a side note whose "none of the searchable folders are readable" was dropped, and the
answer "No folder matching" counted as a find that worked. The name is all the person gave, so both
kinds are the search: a file of that name is the answer, and a file half that could not run is a
search that did not fully run.
"""
from __future__ import annotations

import random
import uuid
from pathlib import Path

import pytest

_RNG = random.Random()


def _stem() -> str:
    return _RNG.choice(("budget", "packing", "garden", "lease", "notes")) + "-" + uuid.uuid4().hex[:4]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("template", [
    "can u find {s} on my computer", "find {s} on my mac", "locate {s} on this machine",
    "look for {s} on my laptop", "search for {s} on my pc",
])
def test_a_name_with_no_kind_word_asks_for_both_kinds(template):
    from core.execution.constants import machine_folder_search_arguments, machine_folder_search_intent

    text = template.format(s=_stem())
    found = machine_folder_search_intent(text)
    assert found is not None, text
    assert machine_folder_search_arguments(text, found[1]).get("any_kind") is True, text


@pytest.mark.parametrize("template", [
    "find the {s} folder", "where is my {s} folder", "locate the folder called {s}",
    "find my {s} directory on my computer",
])
def test_a_named_folder_stays_a_folder_search(template):
    from core.execution.constants import machine_folder_search_arguments, machine_folder_search_intent

    text = template.format(s=_stem())
    found = machine_folder_search_intent(text)
    assert found is not None, text
    assert "any_kind" not in machine_folder_search_arguments(text, found[1]), text


def test_a_file_of_that_name_is_the_answer(home):
    from core.runtime_execution_tools import _machine_find_folder

    stem = _stem()
    (home / "Documents").mkdir()
    (home / "Documents" / f"{stem}.txt").write_text("x", encoding="utf-8")
    found = _machine_find_folder({"name": stem, "any_kind": True})
    assert found.ok is True
    assert found.details["file_matches"] == [str(home / "Documents" / f"{stem}.txt")]
    assert f"{stem}.txt" in found.response_text


def test_a_file_half_that_could_not_run_is_a_failed_search_in_its_own_words(home):
    from core.runtime_execution_tools import _machine_find_folder

    stem = _stem()
    found = _machine_find_folder({"name": stem, "any_kind": True})
    assert found.ok is False
    assert found.details["observation"]["ok"] is False
    assert "searchable local folders are readable" in found.response_text
    assert f"No folder matching '{stem}'" in found.response_text


def test_both_halves_ran_and_found_nothing_is_a_completed_search(home):
    from core.runtime_execution_tools import _machine_find_folder

    stem = _stem()
    (home / "Desktop").mkdir()
    (home / "Desktop" / "other.txt").write_text("x", encoding="utf-8")
    found = _machine_find_folder({"name": stem, "any_kind": True})
    assert found.ok is True
    assert f"No folder matching '{stem}'" in found.response_text
    assert f"No file matching '{stem}'" in found.response_text


def test_an_asked_for_folder_in_an_empty_home_is_still_a_completed_search(home):
    from core.runtime_execution_tools import _machine_find_folder

    found = _machine_find_folder({"name": _stem()})
    assert found.ok is True

