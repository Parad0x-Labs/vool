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


@pytest.fixture
def turn(tmp_path, runtime_storage_reset, monkeypatch):
    import tempfile

    from core.agent_runtime.agent import VoolAgent

    workspace = Path(tempfile.mkdtemp(prefix="vool-here-ws-"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    agent = VoolAgent(backend_name="test-backend", device="here-turns", persona_id="default")
    return agent


@pytest.mark.parametrize(("picked", "admitted"), [(("find",), True), (("read",), True), (("memory_recall",), False), ((), False)])
def test_a_turn_judged_a_find_may_list_a_folder(picked, admitted, turn, monkeypatch) -> None:
    """The listing was admitted before the judge ran, by English word probes alone; the judge's reading now counts."""
    from core.agent_runtime import memory_runtime

    text = random.Random(f"turn-{picked}").choice(ASKS)
    seen: dict = {}

    def judged(message, source_context, session_id):
        plugin_ask._record_builtins(message, picked)
        return None

    def keep(message, *, source_context, **_kw):
        seen.setdefault("admitted", source_context.get("_semantic_machine_list_directory_admitted"))

    monkeypatch.setattr(plugin_ask, "_judge_turn", judged)
    monkeypatch.setattr(memory_runtime, "keep_what_the_turn_states", keep)
    turn.run_once(text, session_id_override="here-chat", source_context={"chat_id": "here-chat"})

    assert bool(seen.get("admitted")) is admitted, (text, picked, seen)


def _project(tmp_path):
    project = tmp_path / "Volumes" / "Work" / "firewood"
    project.mkdir(parents=True)
    (project / "malku-sarasas.txt").write_text("beržas 3 m3\n")
    return project


def test_the_bound_folder_named_by_its_full_path_is_listed(home, tmp_path) -> None:
    """iMac gate on 174b25378: the same folder "." listed was refused by its absolute path."""
    from core.runtime_execution_tools import _dispatch_runtime_tool

    project = _project(tmp_path)
    listed = _dispatch_runtime_tool("machine.list_directory", {"path": str(project)},
                                    source_context={"workspace": str(project), "workspace_binding": "project"})
    assert listed.ok and "malku-sarasas.txt" in listed.response_text, listed.response_text


@pytest.mark.parametrize("how", ["absolute", "relative"])
def test_a_file_in_the_bound_folder_is_read(how, home, tmp_path) -> None:
    """iMac gate on 174b25378: "rodyk, kur mano malkų sąrašas" read projC/malku-sarasas.txt and was refused."""
    from core.runtime_execution_tools import _dispatch_runtime_tool

    project = _project(tmp_path)
    path = str(project / "malku-sarasas.txt") if how == "absolute" else "malku-sarasas.txt"
    read = _dispatch_runtime_tool("machine.read_file", {"path": path},
                                  source_context={"workspace": str(project), "workspace_binding": "project"})
    assert read.ok and "beržas" in read.response_text, read.response_text


def test_outside_the_bound_folder_the_machine_roots_still_decide(home, tmp_path) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    project = _project(tmp_path)
    other = tmp_path / "Volumes" / "Work" / "other"
    other.mkdir()
    (other / "secret.txt").write_text("x")
    (home / "Documents" / "kept.txt").write_text("home file")
    context = {"workspace": str(project), "workspace_binding": "project"}
    assert not _dispatch_runtime_tool("machine.read_file", {"path": str(other / "secret.txt")}, source_context=context).ok
    assert not _dispatch_runtime_tool("machine.list_directory", {"path": str(other)}, source_context=context).ok
    from_home = _dispatch_runtime_tool("machine.read_file", {"path": "Documents/kept.txt"}, source_context=context)
    assert from_home.ok and "home file" in from_home.response_text, from_home.response_text


def test_an_unbound_chat_does_not_reach_a_folder_outside_the_roots(home, tmp_path) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    project = _project(tmp_path)
    assert not _dispatch_runtime_tool("machine.read_file", {"path": str(project / "malku-sarasas.txt")},
                                      source_context={"workspace": str(project), "workspace_binding": "default"}).ok
