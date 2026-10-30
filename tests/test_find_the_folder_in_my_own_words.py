"""A folder asked for in a person's own words is found, whatever order the words come in.

Measured 2026-10-28 on qwen3:8b: "find the folder with my lisbon holiday pics" searched for a folder literally
named "my lisbon holiday pics" and answered that none was found, while ~/Pictures/Holiday Lisbon 2026 sat in the
home folder. The picture word says where to look; the other words name the folder in any order.
"""
from __future__ import annotations

import pytest

from core.runtime_execution_tools import _find_folder_name_from_phrase, _machine_find_folder


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Pictures" / "Holiday Porto 2025").mkdir(parents=True)
    (tmp_path / "Pictures" / "Garden").mkdir()
    (tmp_path / "Documents" / "Porto tax").mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize(
    ("said", "name", "scope"),
    [
        ("my porto holiday pics", "porto holiday", "~/Pictures"),
        ("porto holidays photos", "porto holidays", "~/Pictures"),
        # A picture word alone is still the folder's name.
        ("my photos folder", "photos", ""),
        ("find my pictures folder", "pictures", ""),
    ],
)
def test_a_picture_word_says_where_to_look(said, name, scope):
    assert _find_folder_name_from_phrase(said) == (name, scope)


@pytest.mark.parametrize("said", ["my porto holiday pics", "porto holidays photos", "the holiday porto folder"])
def test_the_folder_is_found_whatever_the_word_order(home, said):
    result = _machine_find_folder({"name": said})
    assert result.ok, result.response_text
    assert str(home / "Pictures" / "Holiday Porto 2025") in result.response_text, result.response_text
    assert "Porto tax" not in result.response_text, result.response_text


def test_every_word_must_be_in_the_name(home):
    result = _machine_find_folder({"name": "my porto wedding pics"})
    assert "Holiday Porto 2025" not in result.response_text, result.response_text
