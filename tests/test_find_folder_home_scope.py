"""A folder search reads the person's own folders, and their drives only when they ask.

Measured on the owner's Mac 2026-10-28: rooted at the drive mounts, "set up a folder in Documents
called Receipts 2026" printed three folders from the work drive, outside the person's home. The
privacy promise is that VOOL reads the person's own folders; a plugged-in drive is searched when
the person names it ("on my external drive").
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def machine(tmp_path, monkeypatch):
    import core.machine_diagnostics as md

    home = tmp_path / "home"
    drive = tmp_path / "drive"
    for folder in (home / "Documents" / "Receipts 2026", home / "Library" / "Receipts 2026",
                   home / "Dropbox", drive / "Backups" / "Receipts 2026"):
        folder.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(md, "disk_usage", lambda *a, **k: [{"mount": "/"}, {"mount": str(drive)}])
    return home, drive


def test_a_folder_search_stays_in_the_home_folder(machine):
    from core.runtime_execution_tools import _machine_find_folder

    home, drive = machine
    found = _machine_find_folder({"name": "Receipts 2026"})
    assert found.details["matches"] == [str(home / "Documents" / "Receipts 2026")]
    assert found.details["observation"]["searched_roots"] == [str(home)]
    assert str(drive) not in found.response_text


def test_a_folder_at_the_top_of_home_is_still_found(machine):
    from core.runtime_execution_tools import _machine_find_folder

    home, _ = machine
    assert _machine_find_folder({"name": "dropbox"}).details["matches"] == [str(home / "Dropbox")]


def test_the_apps_library_in_home_is_not_searched(machine):
    from core.runtime_execution_tools import _machine_find_folder

    home, _ = machine
    matches = _machine_find_folder({"name": "Receipts 2026"}).details["matches"]
    assert str(home / "Library" / "Receipts 2026") not in matches


@pytest.mark.parametrize("arguments", [
    {"name": "Receipts 2026", "drives": True},
    {"name": "Receipts 2026 on my external drive"},
    {"name": "the Receipts 2026 folder on the usb stick"},
])
def test_drives_are_searched_when_the_person_asks(machine, arguments):
    from core.runtime_execution_tools import _machine_find_folder

    home, drive = machine
    found = _machine_find_folder(arguments)
    assert str(drive / "Backups" / "Receipts 2026") in found.details["matches"]
    assert found.details["observation"]["searched_roots"] == [str(home), str(drive)]


def test_a_miss_in_home_says_drives_can_be_searched(machine):
    from core.runtime_execution_tools import _machine_find_folder

    reply = _machine_find_folder({"name": "Backups"}).response_text
    assert "external drive" in reply


@pytest.mark.parametrize("text, wanted", [
    ("find the photos folder on my external drive", True),
    ("where is the Receipts folder on the usb stick", True),
    ("look for the backups folder on all my drives", True),
    ("find my google drive folder", False),
    ("find the Receipts 2026 folder", False),
    ("where is my dropbox folder", False),
])
def test_the_router_hears_a_drive_named(text, wanted):
    from core.execution.constants import machine_folder_search_wants_drives

    assert machine_folder_search_wants_drives(text) is wanted


def test_the_quick_path_passes_the_drive_ask_on(monkeypatch):
    import core.agent_runtime.fast_paths_machine as fpm

    calls = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return None

    monkeypatch.setattr(fpm, "execute_authorized_runtime_tool", fake)
    from types import SimpleNamespace

    fpm.maybe_handle_direct_machine_read_request(
        SimpleNamespace(), "find the photos folder on my external drive", session_id="chat-x",
        source_surface="api", source_context={},
    )
    assert ("machine.find_folder", {"name": "photos", "drives": True}) in calls
