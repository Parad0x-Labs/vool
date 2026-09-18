"""Acceptance regressions for `machine.read_file`, from phrasings driven at the live daemon.

Eight phrasings were driven against a fixture folder on the Desktop holding five real files. Six
completed, and all six failed, in two shapes:

* "show me whats in <file>" and "print the contents of <file>" were claimed by the DIRECTORY lane,
  because the listing detector matched the sentence and never checked what the path pointed at.
  Both answered "Local directory `...` does not exist." about a file sitting on the Desktop -- a
  confident negative about something that plainly exists, which is the worst shape this can fail in.

* "whats written in <file>", "i want to see <file>" and "cat <file>" reached the read lane's marker
  list (" read ", " open ", " quote ", " what does ", " tell me exactly ", " tell me what "), missed
  every entry, and fell through to a model for 60+ seconds before returning "I couldn't map that
  cleanly to a real action".

The fix is the same principle the directory lane already uses, applied to the other side of the
boundary: a path the user typed decides which tool answers, and the DISK decides whether that path
is a file or a folder. Adding five more literals to the marker list would only move the edge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.fast_paths_machine import _extract_machine_file_read_target
from core.execution.constants import machine_file_read_path, machine_path_listing_intent
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture()
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Desktop folder holding real files, so the disk can settle file-vs-folder."""

    folder = tmp_path / "Desktop" / "vool-acceptance-probe"
    (folder / "nested").mkdir(parents=True)
    (folder / "haiku.txt").write_text("the disk does not lie\n")
    (folder / "ledger.md").write_text("# Ledger\n- rent: 1200\n")
    (folder / "config.json").write_text('{"retries": 7}\n')
    (folder / "five-lines.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    (folder / "nested" / "deep.txt").write_text("deep secret token is PLUM-9182\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    return folder


def _read_path(text: str) -> str | None:
    target = _extract_machine_file_read_target(text)
    return None if target is None else target["path"]


# --------------------------------------------------------------------------------------
# the five phrasings that missed the marker list
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("show me whats in ~/Desktop/vool-acceptance-probe/haiku.txt", "~/Desktop/vool-acceptance-probe/haiku.txt"),
        ("whats written in ~/Desktop/vool-acceptance-probe/five-lines.txt", "~/Desktop/vool-acceptance-probe/five-lines.txt"),
        ("i want to see ~/Desktop/vool-acceptance-probe/haiku.txt", "~/Desktop/vool-acceptance-probe/haiku.txt"),
        ("cat ~/Desktop/vool-acceptance-probe/ledger.md", "~/Desktop/vool-acceptance-probe/ledger.md"),
        (
            "print the contents of ~/Desktop/vool-acceptance-probe/nested/deep.txt",
            "~/Desktop/vool-acceptance-probe/nested/deep.txt",
        ),
    ],
)
def test_a_typed_file_plus_a_content_cue_is_a_read(text: str, expected: str, probe: Path) -> None:
    assert _read_path(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "read the file ~/Desktop/vool-acceptance-probe/ledger.md to me",
        "can u open ~/Desktop/vool-acceptance-probe/config.json and tell me what it says",
    ],
)
def test_the_phrasings_the_marker_list_already_caught_still_work(text: str, probe: Path) -> None:
    """The fallback is additive; it must not disturb what already routed."""

    assert _read_path(text) is not None


# --------------------------------------------------------------------------------------
# the boundary: a file is never listed, a folder is never read
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "show me whats in ~/Desktop/vool-acceptance-probe/haiku.txt",
        "print the contents of ~/Desktop/vool-acceptance-probe/nested/deep.txt",
    ],
)
def test_the_listing_lane_does_not_claim_a_file(text: str, probe: Path) -> None:
    """Both of these were answered "Local directory ... does not exist" about a real file."""

    assert machine_path_listing_intent(text) is None


def test_the_listing_lane_still_claims_the_folder(probe: Path) -> None:
    assert machine_path_listing_intent("whats in ~/Desktop/vool-acceptance-probe") == (
        "~/Desktop/vool-acceptance-probe"
    )


@pytest.mark.parametrize(
    "text",
    [
        "delete ~/Desktop/vool-acceptance-probe/haiku.txt",
        "summarize ~/Desktop/vool-acceptance-probe/ledger.md",
        "rename ~/Desktop/vool-acceptance-probe/ledger.md to old.md",
        # these carry a content cue too, so only the other-intent check can decline them
        "summarize what you see in ~/Desktop/vool-acceptance-probe/ledger.md",
        "copy the contents of ~/Desktop/vool-acceptance-probe/haiku.txt somewhere else",
    ],
)
def test_a_verb_asking_for_something_else_is_not_a_read(text: str, probe: Path) -> None:
    """The fallback is broad, so what it must NOT claim is the load-bearing half."""

    assert machine_file_read_path(text) is None


def test_a_path_that_is_not_a_real_file_is_not_a_read(probe: Path) -> None:
    assert machine_file_read_path("show me whats in ~/Desktop/vool-acceptance-probe/absent.txt") is None


# --------------------------------------------------------------------------------------
# and if anything ever lands on the wrong tool anyway, it must say the true thing
# --------------------------------------------------------------------------------------


def test_listing_a_file_says_it_is_a_file_not_that_it_is_missing(probe: Path) -> None:
    result = execute_runtime_tool(
        "machine.list_directory",
        {"path": "~/Desktop/vool-acceptance-probe/haiku.txt"},
        source_context={},
    )
    assert result.status == "not_a_directory"
    assert "is a file, not a directory" in result.response_text
    assert "does not exist" not in result.response_text


def test_listing_a_genuinely_absent_path_still_says_missing(probe: Path) -> None:
    result = execute_runtime_tool(
        "machine.list_directory",
        {"path": "~/Desktop/vool-acceptance-probe/no-such-folder"},
        source_context={},
    )
    assert result.status == "not_found"
    assert "does not exist" in result.response_text


def test_the_read_returns_the_real_bytes(probe: Path) -> None:
    """The point of all of it: the answer has to be what is actually in the file."""

    result = execute_runtime_tool(
        "machine.read_file",
        {"path": "~/Desktop/vool-acceptance-probe/nested/deep.txt", "start_line": 1, "max_lines": 120},
        source_context={},
    )
    assert result.ok
    assert "PLUM-9182" in result.response_text


# --------------------------------------------------------------------------------------
# the gate, not just the extractor
# --------------------------------------------------------------------------------------
#
# This section exists because the extractor above was fixed FIRST, its tests went green, and the
# live daemon kept failing every phrasing. `looks_like_supported_machine_read_request` stands the
# machine lane down on any typed path under a home folder, and that stand-down sat ABOVE the
# file-read check -- so the corrected extractor was never consulted. Measured on the deployed build
# 2026-07-30 with the extractor already returning the right path: "whats inside <file>",
# "display <file>", "lemme see the contents of <file>" and "view <file> please" all answered
# "I wasn't able to turn that into a completed action".
#
# A test on the extractor cannot see that. These drive the gate.

from core.agent_runtime.fast_paths_machine import (
    looks_like_supported_machine_read_request,
)


@pytest.mark.parametrize(
    "text",
    [
        "whats inside ~/Desktop/vool-acceptance-probe/haiku.txt",
        "give me the text of ~/Desktop/vool-acceptance-probe/ledger.md",
        "display ~/Desktop/vool-acceptance-probe/five-lines.txt",
        "lemme see the contents of ~/Desktop/vool-acceptance-probe/nested/deep.txt",
        "view ~/Desktop/vool-acceptance-probe/haiku.txt please",
        "quote me ~/Desktop/vool-acceptance-probe/ledger.md",
    ],
)
def test_the_gate_admits_a_read_of_a_real_file(text: str, probe: Path) -> None:
    assert looks_like_supported_machine_read_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # the stand-down exists for these: a path named while asking something a read cannot answer
        "take a look inside /Users/me/Desktop/ledger-demo and tell me what it does",
        "give me an inventory of everything stored in /Users/me/Documents/pollen-index",
    ],
)
def test_the_typed_path_stand_down_still_holds(text: str, probe: Path) -> None:
    """Admitting file reads earlier must not cost the stand-down the cases it was written for."""

    assert looks_like_supported_machine_read_request(text) is False
