"""A home folder the OS holds never holds a chat request.

Mac gate on 91e898675 (2026-11-23, installed app): a new chat's first message, a contact save, sat 7 min 53 s inside
the chat request before its turn existed, while the owner's Desktop was held by a pending privacy prompt: the binding
of folders named in words lists the home's containers (Desktop among them) on the request path. Here listing the
Desktop hangs; the request carries on without it, and folders elsewhere are still found. Names are drawn at random.
"""
from __future__ import annotations

import os
import random
import threading
import time

import pytest

FOLDERS = ("garden planner", "recipe-app", "BudgetTracker", "knitting patterns", "tax receipts 2025")


@pytest.fixture
def held_desktop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for sub in ("Desktop/holiday photos", "Documents", "Code"):
        (home / sub).mkdir(parents=True)
    gate = threading.Event()
    real = os.scandir

    def scandir(path="."):
        if os.path.basename(os.fspath(path).rstrip("/")) == "Desktop":
            gate.wait(30)
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setenv("HOME", str(home))
    yield home
    gate.set()


@pytest.mark.parametrize("seed", range(4))
def test_a_named_folder_is_found_while_the_desktop_is_held(seed, held_desktop) -> None:
    from core.workspace_from_words import resolve_folder_from_words

    rng = random.Random(f"held-home-{seed}")
    name, container = rng.choice(FOLDERS), rng.choice(("Projects", "Code", "Documents"))
    (held_desktop / container / name).mkdir(parents=True)
    words = rng.choice((f"open the {name} project", f"work on the {name} project in {container}",
                        f"look at my {name} project"))

    started = time.monotonic()
    resolution = resolve_folder_from_words(words, held_desktop)

    assert time.monotonic() - started < 8.0
    assert resolution.status == "found", (words, resolution)
    assert resolution.path == os.path.realpath(held_desktop / container / name)


def test_a_named_project_lookup_skips_a_held_desktop(held_desktop) -> None:
    from core.named_project_binding import _folder_candidates

    (held_desktop / "Code" / "recipe-app").mkdir(parents=True)

    started = time.monotonic()
    found = _folder_candidates("recipe-app", str(held_desktop))

    assert time.monotonic() - started < 8.0
    assert found == [os.path.realpath(held_desktop / "Code" / "recipe-app")]


def test_a_message_naming_nothing_returns_at_once_while_the_desktop_is_held(held_desktop) -> None:
    from core.workspace_from_words import resolve_folder_from_words

    started = time.monotonic()
    resolve_folder_from_words("save a contact: Jonas Petraitis, phone +37061234567", held_desktop)
    resolve_folder_from_words("open the holiday photos project", held_desktop)

    assert time.monotonic() - started < 8.0
