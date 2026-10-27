"""A KIND of file in a named folder ("the pictures in my Downloads from the last 2 days") is listed.

Measured on a phone 2026-10-27: "Find me all the pictures in the Downloads folder on my Mac, up to
two days old and fetch me the results" had no owner. find_folder walked the whole disk for a folder
named "pictures", and planned by the conductor the age was cut off into a part nothing answered.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.execution.constants import machine_kind_listing_covers_turn, machine_kind_listing_intent

REPORTED = (
    "Find me all the pictures in the Downloads folder on my Mac, up to two days old and fetch me the "
    "results. I will tell you which one I need to get to my phone"
)


@pytest.mark.parametrize(("text", "expected"), [
    (REPORTED, ("image", "Downloads", 2.0)),
    ("show me the pictures in my downloads", ("image", "Downloads", None)),
    ("what pdfs are in my documents folder", ("pdf", "Documents", None)),
    ("list the videos on my desktop from the last week", ("video", "Desktop", 7.0)),
    ("any screenshots in downloads from today?", ("image", "Downloads", 1.0)),
    ("which files in downloads are from the past 3 hours", ("", "Downloads", 3 / 24)),
    ("give me the photos from my downloads folder, 5 days old at most", ("image", "Downloads", 5.0)),
])
def test_the_sentence_asks_for_a_kind_in_a_folder(text, expected):
    assert machine_kind_listing_intent(text)[:3] == expected


NOT_A_KIND_LISTING = [
    "find a picture in downloads folder named day4", "delete the pictures in my downloads",
    "copy the pdfs in documents to desktop", "send the photos in downloads to bob",
    "what's in my downloads folder", "find my pictures folder", "summarize the documents in my documents folder",
    "show me the pictures on google drive in documents", "how do I list pictures in downloads in python",
    "write a file in downloads", "the pictures in downloads",
]


@pytest.mark.parametrize("text", NOT_A_KIND_LISTING)
def test_not_a_kind_listing(text):
    assert machine_kind_listing_intent(text) is None


@pytest.mark.parametrize(("text", "whole"), [
    (REPORTED, True),
    ("show me the pictures in my downloads", True),
    ("find pictures in downloads from yesterday please", True),
    ("list the pdfs on my desktop and show me the list", True),
    ("show me the pictures in my downloads and what's the weather in Paris", False),
    ("list the videos on my desktop. also what is 2+2", False),
    ("show me the photos in downloads, then tell me a joke", False),
])
def test_only_a_listing_that_is_the_whole_message_skips_the_planner(text, whole):
    assert machine_kind_listing_covers_turn(text) is whole


def test_the_reported_sentence_is_claimed_by_one_family():
    from core.agent_runtime.intent_claims import FAMILY_FIND_FILE, probe_claims

    assert [c.family for c in probe_claims(REPORTED)] == [FAMILY_FIND_FILE]


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    now = time.time()

    def put(name: str, age_days: float) -> None:
        path = root / "Downloads" / name
        path.write_bytes(b"x")
        os.utime(path, (now - age_days * 86400, now - age_days * 86400))

    put("new.png", 0.1)
    put("Screenshot 1.PNG", 1.5)
    put("old.jpg", 5)
    put("notes.txt", 0.1)
    (root / "Downloads" / "photos.png").mkdir()  # a folder is never listed as a picture
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


@pytest.fixture
def listing_tool(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.runtime_execution_tools import _list_machine_directory

    monkeypatch.setattr(
        ate, "execute_authorized_runtime_tool",
        lambda intent, args, source_context=None: _list_machine_directory(args, workspace_root=home),
    )


def _names(result) -> list[str]:
    return [Path(entry["path"]).name for entry in result.details["entries"]]


def test_pictures_are_listed_newest_first_and_the_age_is_kept(listing_tool):
    from core.agent_runtime.fast_paths_machine import list_files_of_kind

    every = list_files_of_kind(("image", "Downloads", None), source_context={})
    assert _names(every) == ["new.png", "Screenshot 1.PNG", "old.jpg"]
    recent = list_files_of_kind(("image", "Downloads", 2.0), source_context={})
    assert _names(recent) == ["new.png", "Screenshot 1.PNG"]
    assert recent.response_text.startswith("2 pictures in ~/Downloads from the last 2 days, newest first:")


def test_one_match_reads_as_one(listing_tool):
    from core.agent_runtime.fast_paths_machine import list_files_of_kind

    one = list_files_of_kind(("image", "Downloads", 1.0), source_context={})
    assert one.response_text.startswith("1 picture in ~/Downloads from the last day")
    none = list_files_of_kind(("pdf", "Downloads", None), source_context={})
    assert none.response_text == "No PDFs in ~/Downloads." and none.status == "no_results"


def test_a_busy_folder_still_finds_the_newest_picture(home, listing_tool):
    from core.agent_runtime.fast_paths_machine import list_files_of_kind

    # The tool pages alphabetically at 200; the new screenshot sorts after all of these.
    for index in range(230):
        (home / "Downloads" / f"aaa-{index:03}.txt").write_text("x")
    (home / "Downloads" / "zz-screenshot.png").write_bytes(b"x")
    recent = list_files_of_kind(("image", "Downloads", 2.0), source_context={})
    assert "zz-screenshot.png" in _names(recent)


def test_a_folder_pick_on_a_listing_sentence_runs_the_listing(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_machine
    from core.agent_runtime import turn_frontdoor as frontdoor

    seen = {}
    monkeypatch.setattr(fast_paths_machine, "list_files_of_kind", lambda listing, source_context: seen.setdefault("listing", listing))
    monkeypatch.setattr(fast_paths_machine, "_machine_tool_fast_path_result", lambda agent, **kw: {"intent": kw["intent"]})
    for family in ("find_folder", "list_directory"):
        seen.clear()
        result = frontdoor._execute_arbitrated_family(
            SimpleNamespace(), decision=SimpleNamespace(family=family, argument="pictures"),
            effective_input=REPORTED, session_id="chat-x", source_surface="api", source_context={},
        )
        assert result == {"intent": "machine.list_directory"}, family
        assert seen["listing"][:3] == ("image", "Downloads", 2.0)


def test_the_folder_search_looks_in_home_first(home):
    from core.runtime_execution_tools import _machine_find_folder

    (home / "Pictures").mkdir()
    found = _machine_find_folder({"name": "pictures"})
    assert str(home / "Pictures") in found.details["matches"]
