""""Open the newest screenshot" finds it, and never claims it was opened.

Measured 2026-10-28 on qwen3:8b: after listing the Desktop, "open the newest screenshot" went to the model,
which answered "I'll open the newest screenshot for you. Let me locate it." and ran nothing. VOOL has no tool
that opens a file in another app: on the computer it now names the file and where it is; on a phone it sends
the file to the phone. "Screenshot" also means a screenshot, not the newest picture of any kind.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.execution.constants import machine_open_newest_intent


@pytest.mark.parametrize(
    ("said", "kind", "place", "name"),
    [
        ("open the newest screenshot", "image", "", "screenshot"),
        ("open the newest screenshot on my desktop", "image", "Desktop", "screenshot"),
        ("show me the latest photo in my downloads", "image", "Downloads", ""),
        ("can you open the most recent pdf in documents please", "pdf", "Documents", ""),
        ("pull up the last picture i took", "image", "", ""),
    ],
)
def test_an_open_the_newest_ask(said, kind, place, name):
    listing = machine_open_newest_intent(said)
    assert listing is not None
    assert (listing.kind, listing.place, listing.name_contains, listing.limit, listing.max_age_days) == (
        kind, place, name, 1, None)


@pytest.mark.parametrize("said", [
    "show me the latest news", "open the latest one", "open the newest screenshot in narnia",
    "open the newest screenshot and email it to bob", "what is the newest iphone",
])
def test_not_an_open_the_newest_ask(said):
    assert machine_open_newest_intent(said) is None


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / name).mkdir(parents=True)
    now = time.time()

    def put(folder: str, name: str, age_days: float) -> None:
        path = root / folder / name
        path.write_bytes(b"x" * 20)
        os.utime(path, (now - age_days * 86400, now - age_days * 86400))

    put("Desktop", "Screenshot 2026-10-20 at 11.40.png", 8)
    put("Desktop", "Screenshot 2026-10-27 at 18.02.png", 1)
    put("Desktop", "cat_on_sofa.png", 0.1)  # newer, but no screenshot
    put("Desktop", "todo.txt", 0.05)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    import core.authorized_tool_execution as ate
    from core.runtime_execution_tools import _list_machine_directory

    monkeypatch.setattr(
        ate, "execute_authorized_runtime_tool",
        lambda intent, args, source_context=None: _list_machine_directory(args, workspace_root=root),
    )
    return root


def test_on_the_computer_the_newest_screenshot_is_named_and_nothing_is_claimed(home):
    from core.agent_runtime.fast_paths_machine import OPEN_ON_COMPUTER_NOTE, open_newest_file

    result = open_newest_file(machine_open_newest_intent("open the newest screenshot"), phone_turn=False,
                              source_context={})
    assert result.ok, result.response_text
    assert "Screenshot 2026-10-27 at 18.02.png" in result.response_text, result.response_text
    assert "cat_on_sofa" not in result.response_text and "2026-10-20" not in result.response_text
    assert result.response_text.endswith(OPEN_ON_COMPUTER_NOTE)
    assert "opened" not in result.response_text.lower()


def test_no_screenshot_is_said_plainly(home):
    from core.agent_runtime.fast_paths_machine import open_newest_file

    for path in (home / "Desktop").glob("Screenshot*"):
        path.unlink()
    result = open_newest_file(machine_open_newest_intent("open the newest screenshot on my desktop"),
                              phone_turn=False, source_context={})
    assert result.response_text == "No screenshots in ~/Desktop.", result.response_text


@pytest.mark.parametrize(("said", "previous", "place"), [
    ("open the latest one", "Visible entries under `~/Desktop`:\n- todo.txt", "Desktop"),
    ("ok show me the newest one please", "6 pictures in ~/Downloads from the last 2 days, newest first:", "Downloads"),
])
def test_the_latest_one_is_from_the_folder_just_listed(said, previous, place):
    from core.execution.constants import machine_open_newest_one_followup

    listing = machine_open_newest_one_followup(said, previous)
    assert listing is not None and listing.place == place and listing.limit == 1


@pytest.mark.parametrize(("said", "previous"), [
    ("open the latest one", "Here is a joke about penguins."),
    ("open the latest one", ""),
    ("whats the latest one from apple", "Visible entries under `~/Desktop`:"),
])
def test_no_folder_just_listed_means_no_guess(said, previous):
    from core.execution.constants import machine_open_newest_one_followup

    assert machine_open_newest_one_followup(said, previous) is None
