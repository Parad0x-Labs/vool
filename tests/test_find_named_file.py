"""A file asked for by name ("a picture named daily 4 in Downloads") is found as a FILE.

Measured on a phone 2026-10-27: the request had no file family, the arbiter picked find_folder, and
the answer was "No folder matching 'daily 4 or day 4 something' found".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.execution.constants import machine_file_search_intent


@pytest.mark.parametrize(("text", "expected"), [
    ("In my iMac Downloads folder there should be a picture named daily 4 or day 4 something, find it please and fetch it for me.",
     ("image", ("daily 4", "day 4"), "Downloads")),
    ("Downloads folder should have that picture, it's picture named day4 or something, not a folder named like this lol",
     ("image", ("day4",), "Downloads")),
    ("find the pdf called invoice-7.pdf on my desktop", ("pdf", ("invoice-7.pdf",), "Desktop")),
    ("find a picture in downloads folder named day4 or daily 4", ("image", ("day4", "daily 4"), "Downloads")),
    ("find day4.png", ("image", ("day4.png",), "")),
    ("where did I save invoice.pdf", ("pdf", ("invoice.pdf",), "")),
    ("search downloads for day4.jpg", ("image", ("day4.jpg",), "Downloads")),
    ("find the photo named day4 in my Desktop folder", ("image", ("day4",), "Desktop")),
    ("where is the file named report on my desktop folder", ("", ("report",), "Desktop")),
    ("find a picture named day4 anywhere", ("image", ("day4",), "")),
    ("do i have a video called wedding", ("video", ("wedding",), "")),
    # The name itself may hold words the reject lists look for; only what was ASKED is checked.
    ("find the pdf called web design brief", ("pdf", ("web design brief",), "")),
    ("find the file named read me", ("", ("read me",), "")),
    ("find the video named how to cook rice", ("video", ("how to cook rice",), "")),
    ("find day4.png on my external drive", ("image", ("day4.png",), "")),
    ("find the pdf named terms and conditions", ("pdf", ("terms and conditions",), "")),
    ("find the photo named sunset at the beach", ("image", ("sunset at the beach",), "")),
])
def test_the_sentence_names_a_file(text, expected):
    assert machine_file_search_intent(text) == expected


# Found in review: each of these used to reach another lane (the model, web, email, a write or read
# lane, the folder search) and must keep doing so. Only a LOOKUP on this disk is a file search.
NOT_A_FILE_SEARCH = [
    "find my dropbox folder", "delete the file named budget", "a file named it", "what is a file called in French",
    "open my documents folder named taxes", "read notes.txt", "find /Users/me/a.png", "delete old.zip", "find vool.dev site",
    "how do I rename a file called foo.txt in linux", "in python, how do I open a file named data.csv",
    "my brother has a song called yesterday, what genre is it", "there's a song called Hello by Adele, who wrote it",
    "search the web for a song called Hello", "search my email for the pdf called invoice",
    "find a picture named day4 on google drive", "read the file named notes.txt", "open the file called notes.txt in downloads",
    "summarize the document named Q3 plan", "copy the picture named day4 to documents", "rename the file called foo to bar",
    "write a file named hello.py", "make a document called plan", "what's a file called that ends in .tar.gz",
    "find my pictures folder called vacation", 'search for "foo" in notes.txt', "find a pdf named invoice and read it to me", "find the picture named day4 and send it to me",
    "find the file named day4 to send to john", "find the pdf named invoice and email john",
    "find the picture called day4 then open it", "find the picture named day4 at google photos",
    "find the picture named day4 or day5 and send both", "find lyrics for a song called hello",
    "what is the picture named day4 about", "send the pdf named invoice to bob", "how big can a file be on FAT32",
]


@pytest.mark.parametrize("text", NOT_A_FILE_SEARCH)
def test_not_a_file_search(text):
    assert machine_file_search_intent(text) is None


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Downloads" / "Daily_4.PNG").write_bytes(b"x")
    (root / "Downloads" / "daily 4 notes.txt").write_text("x")
    (root / "Desktop" / "daily4.jpg").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


def test_find_file_matches_across_spaces_and_underscores(home):
    from core.runtime_execution_tools import _machine_find_file

    found = _machine_find_file({"name": "daily 4"})
    assert sorted(Path(p).name for p in found.details["matches"]) == ["Daily_4.PNG", "daily 4 notes.txt", "daily4.jpg"]


def test_the_fast_path_keeps_the_kind_and_place(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime.fast_paths_machine import find_named_files
    from core.runtime_execution_tools import _machine_find_file

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", lambda intent, args, source_context=None: _machine_find_file(args))
    found = find_named_files(("image", ("daily 4", "day 4"), "Downloads"), source_context={})
    assert [Path(p).name for p in found.details["matches"]] == ["Daily_4.PNG"]
    assert "Found 1 picture" in found.response_text
    missing = find_named_files(("pdf", ("nothing here",), ""), source_context={})
    assert missing.details["matches"] == [] and "No PDF matching 'nothing here'" in missing.response_text


def test_the_arbiter_can_pick_a_file_search():
    from core import intent_arbiter
    from core.agent_runtime.intent_claims import FAMILY_FIND_FILE, probe_claims

    assert FAMILY_FIND_FILE in intent_arbiter._MENU
    claims = probe_claims("there should be a picture named daily 4 in my Downloads, find it")
    assert [c.family for c in claims][:1] == [FAMILY_FIND_FILE]


def test_a_hit_outside_the_named_place_says_so(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime.fast_paths_machine import find_named_files
    from core.runtime_execution_tools import _machine_find_file

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", lambda intent, args, source_context=None: _machine_find_file(args))
    found = find_named_files(("image", ("daily4",), "Documents"), source_context={})
    assert sorted(Path(p).name for p in found.details["matches"]) == ["Daily_4.PNG", "daily4.jpg"]
    assert found.response_text.startswith("Nothing in ~/Documents, but I found 2 pictures")


def test_folder_asks_stay_folder_asks():
    from tests.test_machine_find_folder_phrasings import FINDS_THE_FOLDER

    assert [text for text in FINDS_THE_FOLDER if machine_file_search_intent(text) is not None] == []


REPORTED = "find a picture in downloads folder named day4 or daily 4"


def test_the_reported_sentence_is_claimed_by_the_file_search_alone():
    from core.agent_runtime.intent_claims import FAMILY_FIND_FILE, probe_claims

    assert [c.family for c in probe_claims(REPORTED)] == [FAMILY_FIND_FILE]


def test_a_folder_pick_on_a_file_sentence_runs_the_file_search(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_machine
    from core.agent_runtime import turn_frontdoor as frontdoor

    seen = {}
    monkeypatch.setattr(fast_paths_machine, "find_named_files", lambda search, source_context: seen.setdefault("search", search))
    monkeypatch.setattr(fast_paths_machine, "_machine_tool_fast_path_result", lambda agent, **kw: {"intent": kw["intent"]})
    result = frontdoor._execute_arbitrated_family(
        SimpleNamespace(), decision=SimpleNamespace(family="find_folder", argument="downloads"),
        effective_input=REPORTED, session_id="chat-x", source_surface="api", source_context={},
    )
    assert result == {"intent": "machine.find_file"}
    assert seen["search"] == ("image", ("day4", "daily 4"), "Downloads")


def test_a_file_pick_the_sentence_does_not_back_falls_open(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime import turn_frontdoor as frontdoor

    result = frontdoor._execute_arbitrated_family(
        SimpleNamespace(), decision=SimpleNamespace(family="find_file", argument="FAT32"),
        effective_input="how big can a file be on FAT32", session_id="chat-x", source_surface="api", source_context={},
    )
    assert result is None


def test_squeezed_names_match_from_a_word_start_only():
    from core.runtime_execution_tools import _squeezed_word_match

    assert _squeezed_word_match("day4", "day 4.png") and _squeezed_word_match("day4", "trip_day_4.jpg")
    assert not _squeezed_word_match("day4", "holiday4.jpg")


def test_a_cut_short_search_says_so(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime.fast_paths_machine import find_named_files
    from core.runtime_execution_tools import _machine_find_file

    for index in range(30):
        (home / "Desktop" / f"day4 notes {index}.txt").write_text("x")
    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", lambda intent, args, source_context=None: _machine_find_file(args))
    found = find_named_files(("image", ("day4",), "Downloads"), source_context={})
    assert found.details["observation"]["truncated"] is True
    assert "longer part of the name" in found.response_text
    # A search that stopped early never claims the place is empty.
    (home / "Downloads" / "day4.png").write_bytes(b"x")
    (home / "Desktop" / "holiday4_1.jpg").write_bytes(b"x")
    again = find_named_files(("image", ("day4",), "Downloads"), source_context={})
    first = again.response_text.splitlines()[0]
    assert not first.startswith(("Nothing in", "No ")), first
