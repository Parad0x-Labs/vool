""""Whats inside the lisbon folder?" lists the folder, found by a word of its name.

Measured 2026-10-28 on qwen3:8b: the model answered "Are you referring to the city of Lisbon, the album by The
Kinks, or the film?" while ~/Pictures/Holiday Lisbon 2026 sat in the home folder. The folder is found by name and,
when exactly one matches, its contents are listed. Several matches or none answer with the search itself, so
VOOL never picks one folder for the person.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.execution.constants import machine_named_folder_contents_intent


@pytest.mark.parametrize(
    ("said", "name"),
    [
        ("whats inside the lisbon folder?", "lisbon"),
        ("open the holiday folder", "holiday"),
        ("what do i have in my taxes folder", "taxes"),
        ("show me whats in the tax 2025 folder please", "tax 2025"),
        ("can you show me whats inside my Holiday Lisbon 2026 folder", "Holiday Lisbon 2026"),
    ],
)
def test_a_folder_named_for_its_contents(said, name):
    assert machine_named_folder_contents_intent(said) == name


@pytest.mark.parametrize("said", [
    # Places VOOL already knows, and the bound folder, keep their own answers.
    "whats in my downloads folder", "what is in the photos folder", "list this folder",
    "what's in the work folder?",
    # A locator, and a compound ask, are not a listing.
    "where is the lisbon folder", "whats inside the lisbon folder and email it to bob",
])
def test_not_a_named_folder_listing(said):
    assert machine_named_folder_contents_intent(said) is None


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    trip = root / "Pictures" / "Holiday Lisbon 2026"
    trip.mkdir(parents=True)
    for name in ("tram.jpg", "belem.jpg", "pasteis.heic"):
        (trip / name).write_bytes(b"x" * 20)
    (root / "Documents" / "Porto tax 2025").mkdir(parents=True)
    (root / "Documents" / "Porto rent").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    import core.agent_runtime.fast_paths_machine as fpm
    from core.runtime_execution_tools import _list_machine_directory, _machine_find_folder

    ran: list[str] = []

    def execute(intent, args, source_context=None):
        ran.append(intent)
        if intent == "machine.find_folder":
            return _machine_find_folder(args, workspace_root=root)
        return _list_machine_directory(args, workspace_root=root)

    monkeypatch.setattr(fpm, "execute_authorized_runtime_tool", execute)
    return root, ran


def test_the_one_matching_folder_is_listed(home):
    _, ran = home
    from core.agent_runtime.fast_paths_machine import list_named_folder

    result = list_named_folder("lisbon", source_context={})
    assert result.ok, result.response_text
    for name in ("tram.jpg", "belem.jpg", "pasteis.heic"):
        assert name in result.response_text, result.response_text
    assert ran == ["machine.find_folder", "machine.list_directory"]


def test_several_matches_are_named_and_none_is_picked(home):
    _, ran = home
    from core.agent_runtime.fast_paths_machine import list_named_folder

    result = list_named_folder("porto", source_context={})
    assert "Porto tax 2025" in result.response_text and "Porto rent" in result.response_text, result.response_text
    assert ran == ["machine.find_folder"]


def test_no_match_is_said_plainly(home):
    _, ran = home
    from core.agent_runtime.fast_paths_machine import list_named_folder

    result = list_named_folder("kinks", source_context={})
    assert "No folder matching 'kinks'" in result.response_text, result.response_text
    assert ran == ["machine.find_folder"]
