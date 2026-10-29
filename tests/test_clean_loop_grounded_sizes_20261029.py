"""A size the clean loop states must be one its tools showed, and a size-of-these-files ask is not
staged as a free-disk ask (fresh-45 blind jury, 2026-10-29: a confident "12.3 GB" for a few KB of
photos, with only the drive total in front of the model)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.clean_loop import tools
from core.clean_loop.loop import run_loop
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import SIZE_NOTICE, grounded_sizes, install_builtin_tools

OWNER = {"surface": "cli"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for folder in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / folder).mkdir(parents=True)
    (root / "Pictures" / "lisbon").mkdir()
    (root / "Pictures" / "lisbon" / "tram.jpg").write_bytes(b"j" * 3000)
    (root / "Pictures" / "lisbon" / "river.jpg").write_bytes(b"j" * 3000)
    monkeypatch.setenv("HOME", str(root))
    return root


def _registry() -> Registry:
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _ctx(home: Path) -> ToolContext:
    return ToolContext(home=home, source_context=dict(OWNER))


def _seen(*texts: str) -> SimpleNamespace:
    return SimpleNamespace(seen=list(texts), proposals=[])


# --- the size gate ---------------------------------------------------------------------------------


def test_a_size_no_tool_showed_is_cut_and_the_answer_says_it_could_not_measure():
    seen = _seen("Drive space:\n/  28.8 GB free of 460.4 GB (93.7% used)")
    answer = grounded_sizes("Your Lisbon photos take up about 12.3 GB.", seen)
    assert "12.3" not in answer and answer == SIZE_NOTICE


def test_sizes_the_tools_showed_are_kept_as_written():
    seen = _seen("- tram.jpg (2.9 KB, modified 2026-10-20, 8 days ago)\n- river.jpg (2.9 KB, modified 2026-10-20, 8 days ago)")
    answer = "There are two photos: tram.jpg (2.9 KB) and river.jpg (2.9 KB)."
    assert grounded_sizes(answer, seen) == answer


def test_the_total_of_one_listing_counts_as_seen():
    seen = _seen("- tram.jpg (2.9 KB, modified today)\n- river.jpg (2.9 KB, modified today)")
    answer = "Together they take 5.8 KB."
    assert grounded_sizes(answer, seen) == answer


def test_the_drive_figures_themselves_are_grounded():
    seen = _seen("Drive space:\n/  28.8 GB free of 460.4 GB (93.7% used)")
    answer = "You have 28.8 GB free out of 460.4 GB."
    assert grounded_sizes(answer, seen) == answer


def test_only_the_sentence_with_the_made_up_size_goes():
    seen = _seen("- tram.jpg (2.9 KB, modified today)")
    answer = "I found tram.jpg in your Lisbon folder. The whole folder is 4.1 GB."
    out = grounded_sizes(answer, seen)
    assert out.startswith("I found tram.jpg in your Lisbon folder.")
    assert "4.1 GB" not in out and SIZE_NOTICE in out


def test_an_answer_without_sizes_is_untouched():
    assert grounded_sizes("Your to-do list has one item.", _seen()) == "Your to-do list has one item."


# --- the staging no longer treats a size-of ask as a free-disk ask -------------------------------


@pytest.mark.parametrize(
    "ask",
    [
        "how much space do my lisbon photos take up?",
        "how much room are my downloads using?",
        "how much storage does the pictures folder eat",
    ],
)
def test_a_size_of_these_files_ask_does_not_stage_the_drive(ask, home):
    staged = tools.look_first_pre_step(ask, _ctx(home))
    assert staged is None or "Drive space" not in staged


@pytest.mark.parametrize("ask", ["how much space do i have left?", "how much storage is free on my mac", "how full is my disk"])
def test_a_free_disk_ask_still_stages_the_drive(ask, home):
    staged = tools.look_first_pre_step(ask, _ctx(home))
    assert isinstance(staged, str) and staged.strip()


def test_the_loop_never_hands_back_an_invented_size(home):
    replies = iter(["Your Lisbon photos take about 12.3 GB."] * 4)

    def brain(messages):
        return next(replies)

    result = run_loop("how much space do my lisbon photos take up?", brain=brain, registry=_registry(), ctx=_ctx(home))
    assert "12.3" not in result.answer
