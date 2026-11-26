"""When a find finds exactly one plain-text file, its first lines come back with it, read by the reader's own rules.

iMac gate37 on f31c60b65 (Offline, qwen3:8b): "and the water meter reading from August?" found the one file, no
read ran, and the reply gave a reading the file does not hold as "Here's the content". Handed only a name, a model
writes contents from nothing. Names and contents here are fresh and built at random.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from core import runtime_paths
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    runtime_paths.configure_runtime_home(tmp_path / "vool")
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    for folder in ("Desktop", "Documents", "Downloads"):
        (tmp_path / folder).mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("core.runtime_execution_tools._safe_machine_roots",
                        lambda: [str(tmp_path / f) for f in ("Desktop", "Documents", "Downloads")])
    return tmp_path


@pytest.mark.parametrize("seed", range(4))
def test_the_one_text_file_found_carries_what_it_says(home, seed):
    rng = random.Random(f"found-read-{seed}")
    thing = rng.choice(("boiler-service-note", "parking-permit-renewal", "piano-lesson-times", "seed-order-list"))
    says = f"{rng.choice(('Due', 'Paid', 'Booked'))} {rng.randint(1, 28)} {rng.choice(('March', 'June', 'October'))}"
    (home / rng.choice(("Desktop", "Documents", "Downloads")) / f"{thing}.txt").write_text(says + "\n", encoding="utf-8")
    found = execute_runtime_tool("machine.find_file", {"name": thing.replace("-", " ")})
    assert found.ok and len(found.details["matches"]) == 1, found.response_text
    assert says in found.response_text and found.details["lines"], found.response_text


def test_two_files_found_are_listed_and_neither_is_read(home):
    (home / "Desktop" / "kayak-notes.txt").write_text("Paddle north\n", encoding="utf-8")
    (home / "Documents" / "kayak-notes-old.txt").write_text("Paddle south\n", encoding="utf-8")
    found = execute_runtime_tool("machine.find_file", {"name": "kayak notes"})
    assert len(found.details["matches"]) == 2 and "Paddle" not in found.response_text
    assert "lines" not in found.details


def test_a_file_that_is_not_plain_text_is_not_read(home):
    (home / "Downloads" / "harbour-fees.pdf").write_bytes(b"%PDF-1.4\n")
    found = execute_runtime_tool("machine.find_file", {"name": "harbour fees"})
    assert len(found.details["matches"]) == 1 and "lines" not in found.details
