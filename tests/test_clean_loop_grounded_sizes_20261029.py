"""A size-of-these-files ask is not staged as a free-disk ask (fresh-45 blind jury, 2026-10-29: a
confident "12.3 GB" for a few KB of photos, with only the drive total staged in front of the
model). The model measures the files with its own reads; the drive figure is staged only when the
drive is what the ask is about."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.clean_loop import tools
from core.clean_loop.registry import ToolContext

OWNER = {"surface": "cli"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for folder in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    return root


def _ctx(home: Path) -> ToolContext:
    return ToolContext(home=home, source_context=dict(OWNER))


@pytest.mark.parametrize(
    "ask",
    [
        "how much space do my lisbon photos take up?",
        "how much room are my downloads using?",
        "how much storage does the pictures folder eat",
        "how much space is my pictures folder",
        "how much storage are my photos",
        "how much space would i free by deleting my downloads",
    ],
)
def test_a_size_of_these_files_ask_does_not_stage_the_drive(ask, home):
    assert tools.look_first_pre_step(ask, _ctx(home)) is None


@pytest.mark.parametrize(
    "ask",
    [
        "how much space do i have left?", "how much storage is free on my mac", "how full is my disk",
        "how much space is used on my mac", "how much storage am i using",
        "do i have enough space for a 20 GB game?", "how much space do i have left for photos",
        "how full is my mac? i want to download a movie",
    ],
)
def test_a_free_disk_ask_still_stages_the_drive(ask, home):
    staged = tools.look_first_pre_step(ask, _ctx(home))
    assert isinstance(staged, str) and staged.strip()
