"""A file asked for in a chat bound to a project folder is looked for in that folder first.

iMac gate on 122dfc5e9: a chat bound to a project folder outside the home asked, in Lithuanian, for a
file that sat in that folder. The search walked only the home folders, for 40 s, and said "not found".
Names, languages and separators are drawn at random; the project always lives outside the home.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

WORDS = (("žemės", "zemes"), ("ūkio", "ukio"), ("ataskaita", "ataskaita"), ("Übersicht", "ubersicht"),
         ("budget", "budget"), ("señal", "senal"), ("plan", "plan"), ("café", "cafe"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("binding", ["project", "explicit"])
def test_the_bound_project_folder_is_searched(seed, binding, home, tmp_path) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    rng = random.Random(f"bound-find-{seed}")
    picked = rng.sample(WORDS, rng.randint(1, 3))
    number = str(rng.randint(2000, 2099))
    on_disk = rng.choice("-_.").join([plain for _said, plain in picked] + [number]) + ".txt"
    typed = " ".join([said for said, _plain in picked] + [number])
    project = tmp_path / "Volumes" / "Work" / rng.choice(["ukis", "projektas", "client-a"])
    (project / "notes").mkdir(parents=True)
    (project / "notes" / on_disk).write_text("x")
    (home / "Documents").mkdir()

    found = _dispatch_runtime_tool(
        "machine.find_file", {"name": typed},
        source_context={"workspace": str(project), "workspace_binding": binding},
    )

    assert [Path(p).name for p in found.details["matches"]] == [on_disk], (typed, found.response_text)


def test_a_default_workspace_adds_no_root(home, tmp_path) -> None:
    from core.runtime_execution_tools import _bound_project_root

    assert _bound_project_root({"workspace": str(tmp_path), "workspace_binding": "default"}) is None
    assert _bound_project_root({"workspace": str(tmp_path)}) is None


def test_the_disk_root_is_never_a_bound_project(home) -> None:
    from core.runtime_execution_tools import _bound_project_root

    assert _bound_project_root({"workspace": "/", "workspace_binding": "explicit"}) is None
