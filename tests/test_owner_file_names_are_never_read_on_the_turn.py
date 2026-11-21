"""The owner's file names are read off the turn (installed 0.8, 3078b8cb4, 2026-11-21).

The first chat after installing the app ("Calculate 23 * 19") answered after 129 s: the app's stall
watch showed the turn inside `core.clean_loop.tools.file_words` -> `os.scandir` for 80+ s, before the
route had decided anything. macOS holds the first read of a protected folder by a newly installed
app until its privacy prompt is answered, so any folder read on the turn can block for as long as
the OS wants. Nothing here depends on what is asked: the folder read itself is made to hang.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from core.clean_loop import tools


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in ("Desktop", "Documents", "Downloads"):
        (tmp_path / name).mkdir()
    (tmp_path / "Documents" / "flat viewing notes.md").write_text("x")
    monkeypatch.setattr(tools, "_NAME_CACHE", {})
    monkeypatch.setattr(tools, "_NAME_BUILDS", {})
    monkeypatch.setattr(tools, "_name_roots", lambda h: [h / "Desktop", h / "Documents", h / "Downloads"])
    return tmp_path


def _hang_on(folder_name: str, monkeypatch):
    gate = threading.Event()
    real = os.scandir

    def scandir(path):
        if os.path.basename(str(path)) == folder_name:
            gate.wait(30)
        return real(path)

    monkeypatch.setattr(tools.os, "scandir", scandir)
    return gate


@pytest.mark.parametrize("held", ["Desktop", "Documents", "Downloads"])
def test_a_folder_read_that_hangs_does_not_hold_the_turn(home, monkeypatch, held):
    gate = _hang_on(held, monkeypatch)
    try:
        started = time.monotonic()
        words = tools.file_words(home, wait_s=0.3)
        assert time.monotonic() - started < 2.0, "the turn waited on the folder read"
        assert words == frozenset()  # not known yet: routed as naming no file
    finally:
        gate.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not tools._NAME_CACHE:
        time.sleep(0.05)
    # The walk the turn did not wait for finished in the background, and the next turn has it.
    assert {"flat", "viewing"} <= set(tools.file_words(home))


def test_an_expired_list_is_used_while_a_fresh_one_is_read(home, monkeypatch):
    first = tools.file_words(home)
    assert "viewing" in first
    key = str(home)
    stamp, words = tools._NAME_CACHE[key]
    tools._NAME_CACHE[key] = (stamp - tools._NAME_TTL_S - 1, words)
    (home / "Desktop" / "garage sale list.txt").write_text("x")
    gate = _hang_on("Desktop", monkeypatch)
    try:
        started = time.monotonic()
        again = tools.file_words(home)
        assert time.monotonic() - started < 0.5
        assert again == first
    finally:
        gate.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "garage" not in tools.file_words(home):
        time.sleep(0.05)
    assert "garage" in tools.file_words(home)


def test_one_walk_at_a_time_per_home(home, monkeypatch):
    gate = _hang_on("Documents", monkeypatch)
    try:
        for _ in range(5):
            tools.file_words(home, wait_s=0.05)
        alive = [t for t in threading.enumerate() if t.name == "clean-loop-file-names" and t.is_alive()]
        assert len(alive) == 1
    finally:
        gate.set()
