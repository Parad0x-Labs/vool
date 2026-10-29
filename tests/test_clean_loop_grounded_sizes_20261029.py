"""A size-of-these-files answer must not be built on the drive figure alone, and a size-of-these-
files ask is not staged as a free-disk ask (fresh-45 blind jury, 2026-10-29: a confident "12.3 GB"
for a few KB of photos, with only the drive total in front of the model)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.clean_loop import tools
from core.clean_loop.loop import run_loop
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import SIZE_NOTICE, grounded_sizes, install_builtin_tools

OWNER = {"surface": "cli"}
DRIVE = "Drive space:\n/  28.8 GB free of 460.4 GB (93.7% used)"
LISTING = "- tram.jpg (2.9 KB, modified today)\n- river.jpg (2.9 KB, modified today)"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for folder in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / folder).mkdir(parents=True)
    (root / "Pictures" / "lisbon").mkdir()
    (root / "Pictures" / "lisbon" / "tram.jpg").write_bytes(b"j" * 3000)
    monkeypatch.setenv("HOME", str(root))
    return root


def _registry() -> Registry:
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _ctx(home: Path) -> ToolContext:
    return ToolContext(home=home, source_context=dict(OWNER))


def _seen(*texts: str, said: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(seen=list(texts), said=list(said), proposals=[])


# --- the size gate ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    ["Your Lisbon photos take up about 12.3 GB.", "Your photos take about 12.3 gigabytes.", "About 12.3GiB.", "Roughly 12 GBs."],
)
def test_a_size_built_on_the_drive_figure_alone_is_cut(answer):
    out = grounded_sizes(answer, _seen(DRIVE))
    assert "12" not in out and out == SIZE_NOTICE


def test_the_drive_figures_and_used_space_are_kept():
    for answer in ("You have 28.8 GB free out of 460.4 GB.", "You have used about 431.6 GB of 460.4 GB.", "Your drive is about 0.45 TB."):
        assert grounded_sizes(answer, _seen(DRIVE)) == answer


def test_a_size_the_owner_gave_is_theirs():
    answer = "Yes: a 20 GB game fits, you have 28.8 GB free."
    assert grounded_sizes(answer, _seen(DRIVE, said=("do i have enough space for a 20 GB game?",))) == answer


def test_a_turn_where_nothing_was_read_is_general_knowledge():
    answer = "A two-hour 4K movie is usually 15-25 GB."
    assert grounded_sizes(answer, _seen()) == answer


def test_a_turn_that_read_real_file_sizes_is_the_models_to_sum():
    answer = "Together they take 5.8 KB, and you have 28.8 GB free."
    assert grounded_sizes(answer, _seen(DRIVE, LISTING)) == answer


def test_only_the_invented_sentence_goes_and_the_true_one_stays():
    out = grounded_sizes("You have 28.8 GB free. 12.3 GB of it is photos.", _seen(DRIVE))
    assert out.startswith("You have 28.8 GB free.") and "12.3" not in out and SIZE_NOTICE in out


def test_rows_and_bullets_keep_their_shape():
    out = grounded_sizes("| folder | size |\n| Pictures | 12.3 GB |\n- Downloads: 4.1 GB", _seen(DRIVE))
    assert "| Pictures | not measured |" in out and "- Downloads: not measured" in out


def test_an_integer_figure_is_not_given_a_huge_slack():
    assert "1 TB" not in grounded_sizes("Your photos are about 1 TB.", _seen(DRIVE))


def test_an_answer_without_sizes_is_untouched():
    assert grounded_sizes("Your to-do list has one item.", _seen(DRIVE)) == "Your to-do list has one item."


# --- the staging no longer treats a size-of ask as a free-disk ask -------------------------------


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
    ],
)
def test_a_free_disk_ask_still_stages_the_drive(ask, home):
    staged = tools.look_first_pre_step(ask, _ctx(home))
    assert isinstance(staged, str) and staged.strip()


def test_the_loop_never_hands_back_an_invented_size(home, monkeypatch):
    monkeypatch.setattr(tools, "look_first_pre_step", lambda ask, ctx: DRIVE)
    registry = Registry()
    install_builtin_tools(registry)
    registry.register_pre_step("look_first", lambda ask, ctx: DRIVE)
    result = run_loop(
        "how big is that lisbon folder?", brain=lambda messages: "Your Lisbon photos take about 12.3 GB.",
        registry=registry, ctx=_ctx(home),
    )
    assert "12.3" not in result.answer
