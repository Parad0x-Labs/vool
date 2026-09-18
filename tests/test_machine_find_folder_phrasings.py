"""Asking where a folder went is a folder search, however it is phrased -- and a folder is one folder.

Measured live 2026-07-31 against folders that exist on this machine's Desktop:

  "i lost my orchid folder, where did it go"   -> 75s, "I couldn't map that cleanly to a real action"
  "hey where'd my maple folder end up"          -> 60s, "I couldn't map that cleanly to a real action"
  "locate polybets on this machine"             -> 24s, emitted a `find` command for the user to run
  "do i have a folder called scraper anywhere"  -> reached no family
  "which directory holds my invoices"           -> answered with the workspace root, while
                                                   ~/Desktop/my-invoices-folder sat on the disk

Every one of those folders was on the disk and the search tool finds them in seconds. The rule
required a search VERB and a name sitting between that verb and the word "folder"; a question about
where something went satisfies neither.

Separately, "find the folder called vool-lego-probe" answered "Found 2 folders" and listed the same
directory twice -- once as /Users/... and once as /System/Volumes/Data/Users/..., which macOS mounts
via a firmlink. `ls -di` reports inode 102487738 for both. Two is the wrong count for one folder, and
walking the tree under both names is why these searches took the better part of a minute.
"""

from __future__ import annotations

import os

import pytest

from core.execution.constants import machine_folder_search_intent
from core.runtime_execution_tools import _machine_find_folder

# phrasing -> the folder name the search must be run FOR. A search that runs for the wrong string
# ("scraper anywhere") walks the whole disk and correctly reports nothing.
FINDS_THE_FOLDER = {
    "i lost my orchid folder, where did it go": "orchid",
    "hey where'd my maple folder end up": "maple",
    "locate polybets on this machine": "polybets",
    "do i have a folder called scraper anywhere": "scraper",
    "which directory holds my invoices": "invoices",
    "where is my token-hunter folder": "token-hunter",
    "find the folder called vool-lego-probe": "vool-lego-probe",
    "search for a folder named web0-internal": "web0-internal",
    "is there a folder called needle-folder-xyz": "needle-folder-xyz",
    "where did my polybets folder go": "polybets",
}

NOT_A_FOLDER_SEARCH = [
    # Creation and deletion belong to the write lanes, not a read.
    "create a folder named demo",
    "delete the scraper folder",
    # "where" about something that is not on this disk.
    "where is the nearest post office",
    "i lost my keys",
    "where did the time go",
    "i lost my train of thought",
    "where do i put my api keys",
    # An opinion, not a lookup.
    "which folder should i use for react components",
    "is there a better way to do this",
    # The bare-name form has only a this-host phrase marking it as a directory search, so the name
    # must also look like one. Otherwise this is a search for a folder called "good restaurant".
    "find me a good restaurant on this machine",
    "where is the best coffee on this machine",
]


@pytest.mark.parametrize("phrasing,expected_name", sorted(FINDS_THE_FOLDER.items()))
def test_asking_where_a_folder_is_runs_the_folder_search(phrasing: str, expected_name: str) -> None:
    result = machine_folder_search_intent(phrasing)
    assert result is not None, (
        f"{phrasing!r} asks where a folder is. The folder is on the disk and the search finds it in "
        f"seconds; returning 'I couldn't map that cleanly to a real action' is a refusal the runtime "
        f"did not need to make."
    )
    intent, name = result
    assert intent == "machine.find_folder"
    assert name == expected_name, (
        f"searching for {name!r} instead of {expected_name!r} walks the disk and finds nothing"
    )


@pytest.mark.parametrize("phrasing", NOT_A_FOLDER_SEARCH)
def test_a_sentence_that_is_not_about_a_folder_does_not_search_the_disk(phrasing: str) -> None:
    assert machine_folder_search_intent(phrasing) is None


def test_a_trailing_locative_is_not_part_of_the_folder_name() -> None:
    """"a folder called scraper anywhere" is a search for "scraper"; "scraper anywhere" cannot match."""
    assert machine_folder_search_intent("do i have a folder called scraper anywhere") == (
        "machine.find_folder",
        "scraper",
    )


def test_one_directory_is_reported_once_even_when_reachable_by_two_paths(tmp_path, monkeypatch) -> None:
    """The firmlink defect, reproduced with two roots that reach the same tree.

    macOS exposes the data volume at both `/Users/...` and `/System/Volumes/Data/Users/...`. Neither
    is a symlink, so `realpath` does not collapse them and the walk counts every hit twice.
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "needle-dir").mkdir()

    # A second name for the SAME directory, the way a firmlink presents one.
    alias = tmp_path / "alias"
    os.symlink(real, alias)

    import core.machine_diagnostics as md

    monkeypatch.setattr(
        md, "disk_usage", lambda *a, **k: [{"mount": str(real)}, {"mount": str(alias)}]
    )

    result = _machine_find_folder({"name": "needle-dir"})
    matches = (result.details or {}).get("matches") or []
    assert len(matches) == 1, (
        f"one directory reachable by two paths was reported {len(matches)} times: {matches}. "
        f"'Found 2 folders' is the wrong count for one folder."
    )
    assert "Found 1 folder" in (result.response_text or "")
