"""A folder read the OS holds never holds a chat turn (installed 0.8, 315c57e65, 2026-11-22).

The real-app gate on the Mac: the first turn never finished. The app's stall watch showed it inside
`site_followups.find_built_site_page` -> `os.walk` of the owner's Desktop (macOS holds a new
program's first read of a protected folder until its privacy prompt is answered), and every later
turn waited at `warm_index` on the document index lock that a background index slice held while it
was itself blocked in a folder read. Nothing here depends on what is asked: the folder read (or the
lock behind it) is made to hang, and every turn-side caller must come back within a bounded time.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from core import off_turn
from core.agent_runtime.builder import site_followups
from core.clean_loop import tools
from core.clean_loop.registry import ToolContext

OWNER = {"surface": "cli"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Documents", "Downloads"):
        (root / name).mkdir(parents=True)
    (root / "Documents" / "boat.txt").write_text("Brennick keeps the boat at Quarrow.\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("VOOL_HOME", str(root / "Documents" / "vool-data"))
    tools._TEXT_CACHE.clear()
    tools._TEXT_SCANS.clear()
    monkeypatch.setattr(tools, "_WARMING", {})
    yield root
    tools._TEXT_CACHE.clear()
    tools._TEXT_SCANS.clear()


def _bounded(seconds: float):
    started = time.monotonic()
    return lambda: time.monotonic() - started < seconds


# --- the shared helper ----------------------------------------------------------------------------


def test_a_blocked_read_gives_the_fallback_and_no_second_read_piles_up():
    gate = threading.Event()
    calls = []

    def read():
        calls.append(1)
        gate.wait(30)
        return "done"

    key = "test-blocked-read"
    try:
        in_time = _bounded(1.5)
        assert off_turn.run_bounded(key, read, wait_s=0.2, fallback="unknown") == "unknown"
        assert in_time()
        for _ in range(5):
            in_time = _bounded(0.2)
            assert off_turn.run_bounded(key, read, wait_s=5.0, fallback="unknown") == "unknown"
            assert in_time(), "a turn waited behind a read that is still blocked"
        assert len(calls) == 1 and off_turn.still_blocked(key)
    finally:
        gate.set()
    deadline = time.monotonic() + 10
    while off_turn.still_blocked(key) and time.monotonic() < deadline:
        time.sleep(0.02)
    # Once the OS lets go, the next turn reads again and gets the real answer.
    assert off_turn.run_bounded(key, read, wait_s=5.0, fallback="unknown") == "done"


def test_a_read_that_raises_gives_the_fallback():
    def read():
        raise PermissionError("not allowed")

    assert off_turn.run_bounded("test-raises", read, wait_s=2.0, fallback=None) is None


# --- the built-page search ------------------------------------------------------------------------


def _hang_walk(monkeypatch):
    gate = threading.Event()
    real = os.walk
    walked = []

    def walk(top, *args, **kwargs):
        walked.append(top)
        gate.wait(30)
        return real(top, *args, **kwargs)

    monkeypatch.setattr(site_followups.os, "walk", walk)
    return gate, walked


def test_the_built_page_search_does_not_hold_the_turn_when_the_folder_read_hangs(home, monkeypatch):
    (home / "Desktop" / "shop").mkdir()
    (home / "Desktop" / "shop" / "index.html").write_text("<footer>Made on this computer</footer>")
    gate, walked = _hang_walk(monkeypatch)
    try:
        in_time = _bounded(3.0)
        assert site_followups.find_built_site_page(str(home / "Desktop"), wait_s=0.3) is None
        assert in_time()
        assert walked
    finally:
        gate.set()


def test_a_turn_that_is_not_about_a_built_page_reads_no_folder(home, monkeypatch):
    gate, walked = _hang_walk(monkeypatch)
    try:
        for text in ("hi there", "what is 23 times 19", "remind me to call the vet tomorrow", "tell me a joke"):
            in_time = _bounded(0.5)
            assert site_followups.maybe_handle_site_followup(
                object(), text, {"workspace": str(home / "Desktop")}
            ) is None
            assert in_time()
        assert walked == []
    finally:
        gate.set()


# --- the document index ---------------------------------------------------------------------------


def _hold_index_lock():
    """A background index slice that is blocked in a folder read while it holds the index lock."""
    held, release = threading.Event(), threading.Event()

    def slice_blocked_in_a_read():
        with tools._INDEX_LOCK:
            held.set()
            release.wait(30)

    threading.Thread(target=slice_blocked_in_a_read, daemon=True).start()
    assert held.wait(5)
    return release


def test_a_question_is_routed_without_waiting_on_a_held_index(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    release = _hold_index_lock()
    try:
        in_time = _bounded(tools._TEXT_SCAN_S + tools._INDEX_WAIT_S + 2.0)
        assert tools.document_matches("Where is Quarrow now?", ctx) is False
        assert in_time(), "the route waited on the index lock"
        in_time = _bounded(0.5)
        tools.warm_index(ctx)
        assert in_time(), "starting the background index waited on the index lock"
    finally:
        release.set()
    deadline = time.monotonic() + 15
    found = False
    while not found and time.monotonic() < deadline:
        found = tools.document_matches("Where is Quarrow now?", ctx)
        time.sleep(0.05)
    assert found  # once the read is let go, the owner's document answers the route again


def test_the_search_inside_documents_does_not_wait_on_a_held_index(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    release = _hold_index_lock()
    try:
        in_time = _bounded(tools._TEXT_FIND_SCAN_S + tools._INDEX_WAIT_S + 2.0)
        assert tools._find_in_text(["quarrow"], ctx) == []
        assert in_time(), "the search waited on the index lock"
    finally:
        release.set()
