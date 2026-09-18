"""`machine.find_folder` given a path instead of a bare name.

Measured 2026-07-28: asked to "open up ~/Desktop/vool-fresh-7z9 and name every file you find
there", the runtime passed the whole path as the folder *name*. Searching for a directory named
"~/Desktop/vool-fresh-7z9" cannot match, so the answer was "No folder matching
'~/Desktop/vool-fresh-7z9' found on /" — for a folder that existed — after walking every drive
to depth 6.

`~` is the specific trap: `os.path.isdir("~/Desktop/x")` is False while the expanded form is
True, so an unexpanded tilde is indistinguishable from a path that is not there.

The disk search still exists and is still correct; it is what a bare name should do. This only
stops a path from being misread as a name.
"""
from __future__ import annotations

import os
import time

import pytest

from core.runtime_execution_tools import _machine_find_folder, _resolved_existing_directory


@pytest.fixture()
def probe(tmp_path):
    directory = tmp_path / "vool-probe-dir"
    directory.mkdir()
    (directory / "a.txt").write_text("a", encoding="utf-8")
    return directory


def test_an_absolute_path_resolves_without_searching(probe) -> None:
    assert _resolved_existing_directory(str(probe)) == str(probe)


def test_a_tilde_path_is_expanded_before_the_existence_check() -> None:
    """The exact defect: unexpanded, this reads as a folder that is not there."""

    home_relative = "~"
    assert os.path.isdir(home_relative) is False
    assert _resolved_existing_directory(home_relative) == os.path.expanduser("~")


def test_a_bare_folder_name_is_not_treated_as_a_path(probe, monkeypatch) -> None:
    """A name is what the disk search is for; it must still reach it.

    The bare name is deliberately one that WOULD resolve against the working directory. A name
    that happens not to exist there passes whether or not the path-shape check is present, so it
    proves nothing — an earlier version of this test used one and let a sabotage through.
    """

    monkeypatch.chdir(probe.parent)
    assert os.path.isdir("vool-probe-dir") is True
    assert _resolved_existing_directory("vool-probe-dir") is None


def test_a_bare_name_still_reaches_the_disk_search(probe, monkeypatch) -> None:
    """End to end: a resolvable bare name must not short-circuit to the cwd-relative directory."""

    monkeypatch.chdir(probe.parent)
    result = _machine_find_folder({"name": "vool-probe-dir"})
    observation = (result.details or {}).get("observation") or {}
    assert not observation.get("resolved_directly")


def test_a_path_shaped_string_that_does_not_exist_falls_through_to_the_search(tmp_path) -> None:
    assert _resolved_existing_directory(str(tmp_path / "definitely-absent")) is None


def test_a_relative_dot_path_resolves(probe, monkeypatch) -> None:
    monkeypatch.chdir(probe.parent)
    assert _resolved_existing_directory("./vool-probe-dir") == str(probe)


def test_quotes_around_a_path_are_tolerated(probe) -> None:
    assert _resolved_existing_directory(f'"{probe}"') == str(probe)


def test_find_folder_returns_the_path_directly_and_scans_nothing(probe) -> None:
    result = _machine_find_folder({"name": str(probe)})
    assert result.ok is True
    assert str(probe) in (result.response_text or "")
    observation = (result.details or {}).get("observation") or {}
    assert observation.get("scanned_dirs") == 0, "a resolvable path must not trigger a disk walk"
    assert observation.get("resolved_directly") is True


def test_find_folder_records_the_resolved_target(probe) -> None:
    """The binder needs the value the tool actually ran against, not the user's spelling."""

    result = _machine_find_folder({"name": str(probe)})
    assert (result.details or {}).get("resolved_target") == str(probe)


def test_resolving_a_path_is_fast(probe) -> None:
    """Guards the cost half of the defect: the old path walked every drive on a miss."""

    started = time.monotonic()
    _machine_find_folder({"name": str(probe)})
    assert time.monotonic() - started < 1.0


def test_a_missing_name_still_asks_for_one() -> None:
    result = _machine_find_folder({})
    assert result.ok is False
    assert result.status == "missing_argument"
