"""In a chat bound to a folder, a listing of "here" lists that folder, and a judged find proves the folder demand.

iMac gate on c78c018da (qwen3:8b, a chat bound to a project folder outside the home holding sodo-planas.txt):
"kur laikau savo sodo planą?" ("where do I keep my garden plan?") was judged Find; the model called
machine.list_directory {"path": "."}; the English word probes found no folder demand and the turn answered "I did not
inspect a local directory because this turn did not establish a machine or path target".
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from core import plugin_ask


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.mark.parametrize("path", ["", ".", "./"])
def test_here_in_a_bound_chat_lists_the_bound_folder(path, home, tmp_path) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    project = tmp_path / "Volumes" / "Work" / "garden"
    project.mkdir(parents=True)
    (project / "sodo-planas.txt").write_text("x")
    (home / "Documents" / "elsewhere.txt").write_text("y")

    listed = _dispatch_runtime_tool(
        "machine.list_directory", {"path": path},
        source_context={"workspace": str(project), "workspace_binding": "project"},
    )

    assert listed.ok, listed.response_text
    assert "sodo-planas.txt" in listed.response_text and "elsewhere.txt" not in listed.response_text


ASKS = ("kur laikau savo sodo planą?", "wo habe ich meinen Gartenplan abgelegt?", "¿dónde tengo el plan del jardín?",
        "gdzie trzymam plan ogrodu?")


@pytest.mark.parametrize("seed", range(4))
def test_a_judged_find_or_read_is_a_folder_demand_in_any_language(seed) -> None:
    from core.agent_runtime.agent import _owner_folder_demand
    from core.request_trust import OWNER_LOCAL_KEY

    rng = random.Random(f"judged-folder-{seed}")
    text = rng.choice(ASKS)
    owner = {OWNER_LOCAL_KEY: True}
    assert not _owner_folder_demand(text, owner), "the English probes alone read none of these"
    plugin_ask._record_builtins(text, (rng.choice(["find", "read"]),))
    assert _owner_folder_demand(text, owner)
    assert not _owner_folder_demand(text, {OWNER_LOCAL_KEY: False}), "never for a turn that is not the owner's own"


def test_a_judged_memory_recall_is_no_folder_demand() -> None:
    from core.agent_runtime.agent import _owner_folder_demand
    from core.request_trust import OWNER_LOCAL_KEY

    plugin_ask._record_builtins(ASKS[0], ("memory_recall",))
    assert not _owner_folder_demand(ASKS[0], {OWNER_LOCAL_KEY: True})
