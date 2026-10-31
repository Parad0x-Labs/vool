"""The owner's other visible home folders are READ roots, never write roots (owner's choice 2026-10-31).

Round 4: "the list of stuff im selling" lived in ~/Projects/garage sale/price list.txt, which no
machine tool could see. Visible home folders are now readable; hidden folders, app and system data,
links that point elsewhere and VOOL's own data folder are not; writes stay in Desktop, Downloads
and Documents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import runtime_execution_tools as ret


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Projects", "Taxes", "Library", "Applications", ".ssh"):
        (root / name).mkdir(parents=True)
    (root / "Projects" / "garage sale").mkdir()
    (root / "Projects" / "garage sale" / "price list.txt").write_text("Total: $303\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "LinkedAway").symlink_to(elsewhere, target_is_directory=True)
    (root / "notes.txt").write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("VOOL_HOME", str(root / "VOOL Data"))
    (root / "VOOL Data").mkdir()
    return root


def _names(roots) -> list[str]:
    return [Path(r).name for r in roots]


def test_visible_home_folders_are_readable(home):
    names = _names(ret._readable_machine_roots())
    assert names[:4] == ["Desktop", "Downloads", "Documents", "Pictures"]
    assert "Projects" in names and "Taxes" in names


def test_hidden_system_linked_and_vool_folders_are_never_roots(home):
    names = _names(ret._readable_machine_roots())
    for never in ("Library", "Applications", ".ssh", "LinkedAway", "VOOL Data", "notes.txt"):
        assert never not in names


def test_other_home_folders_are_never_write_roots(home):
    assert _names(ret._safe_machine_roots()) == ["Desktop", "Downloads", "Documents"]


def test_the_clean_loop_finds_a_file_in_projects(home):
    from core.clean_loop.registry import Registry, ToolContext
    from core.clean_loop.tools import install_builtin_tools

    registry = Registry()
    install_builtin_tools(registry)
    ctx = ToolContext(home=home, source_context={"surface": "cli"}, task_id="t")
    outcome = registry.tool("find").run({"query": "price list"}, ctx)
    assert "price list.txt" in outcome.text
