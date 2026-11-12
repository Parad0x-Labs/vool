"""Everyday file asks answered from the disk, not refused or handed to a model with no tools.

Measured 2026-10-27 on 58 plain asks against a served daemon with no model: "read notes.txt in my
documents" said the PROJECT had no notes.txt, "list everything in Documents/Taxes" was refused as a
folder at the disk root, "how big is my downloads folder" was refused as unmeasured, "what's the
biggest file in my downloads" measured the whole home folder, "find my taxes pdf" and "where did I
save the invoice" waited for a model, and "what's using my CPU right now" ran the process list and
then had it replaced by "I didn't run any live lookup on this turn".
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.execution.constants import (
    explicit_path_in,
    machine_file_search_intent,
    machine_folder_size_intent,
    machine_place_file_read_path,
)


@pytest.mark.parametrize(("text", "path"), [
    ("read notes.txt in my documents", "~/Documents/notes.txt"),
    ("open the file todo.txt on my desktop", "~/Desktop/todo.txt"),
    ("what's in meeting notes.docx on my desktop", "~/Desktop/meeting notes.docx"),
    ("what does notes.txt in documents say", "~/Documents/notes.txt"),
    ("copy notes.txt in my documents to desktop", None),
    ("summarize report.pdf in downloads", None),
    ("read the docs in documents", None),
])
def test_a_file_named_in_a_folder_is_read_there(text, path):
    assert machine_place_file_read_path(text) == path


def test_a_home_folder_then_a_slash_is_that_folder():
    assert explicit_path_in("list everything in Documents/Taxes") == "~/Documents/Taxes"
    assert explicit_path_in("read ~/Documents/a.txt") == "~/Documents/a.txt"
    assert explicit_path_in("list /tmp/x") == "/tmp/x"


@pytest.mark.parametrize(("text", "expected"), [
    ("find my taxes pdf", ("pdf", ("taxes",), "")),
    ("find my taxes pdf in documents", ("pdf", ("taxes",), "Documents")),
    ("where is the wedding video please", ("video", ("wedding",), "")),
    ("where did I save the invoice", ("", ("invoice",), "")),
    ("where did I put my resume from last week", ("", ("resume",), "")),
    ("look for a file with 77 in the name", ("", ("77",), "")),
    ("find my tax documents", ("", ("tax",), "")),
])
def test_a_file_asked_for_without_named(text, expected):
    assert machine_file_search_intent(text) == expected


@pytest.mark.parametrize("text", [
    "find my latest pdf", "find my pictures folder", "find the cheapest pdf editor", "where did i save the file",
    "where is the closest pharmacy", "find my phone", "search for the invoice pdf on google drive",
    "find my taxes pdf and email it to bob", "where is my resume",
])
def test_not_a_loose_file_lookup(text):
    assert machine_file_search_intent(text) is None


@pytest.mark.parametrize(("text", "place"), [
    ("how big is my downloads folder", "Downloads"),
    ("what is the size of my documents", "Documents"),
    ("how much space does my desktop take", "Desktop"),
    ("how big is a pdf", None),
    ("how big is the moon", None),
])
def test_folder_size_asks(text, place):
    assert machine_folder_size_intent(text) == place


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Documents" / "notes.txt").write_text("buy milk\n")
    (root / "Downloads" / "big.bin").write_bytes(b"x" * 4096)
    (root / "Downloads" / "inner").mkdir()
    (root / "Downloads" / "inner" / "small.txt").write_text("x")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


@pytest.fixture
def machine_tools(home, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.runtime_execution_tools import _list_machine_directory, _machine_find_file, _read_machine_file

    calls = []

    def run(intent, args, source_context=None):
        calls.append((intent, dict(args)))
        if intent == "machine.list_directory":
            return _list_machine_directory(args, workspace_root=home)
        if intent == "machine.find_file":
            return _machine_find_file(args)
        if intent == "machine.read_file":
            return _read_machine_file(args)
        raise AssertionError(intent)

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", run)
    return calls


def test_a_folder_size_is_measured(machine_tools):
    from core.agent_runtime.fast_paths_machine import measure_folder_size

    measured = measure_folder_size("Downloads", source_context={})
    assert measured.details["size_bytes"] == 4097 and measured.details["file_count"] == 2
    assert measured.response_text == "~/Downloads uses 4 KB: 2 files in 1 folder."


def _fake_agent():
    return SimpleNamespace(
        _emit_runtime_event=lambda *a, **k: None,
        _fast_path_result=lambda **kw: {"response": kw["response"], "reason": kw["reason"]},
    )


def test_a_file_the_project_lacks_is_read_from_home(machine_tools, tmp_path):
    from core.agent_runtime.fast_paths_machine import read_home_file_the_project_lacks

    project = tmp_path / "project"
    project.mkdir()
    read = read_home_file_the_project_lacks(_fake_agent(), "open notes.txt", session_id="s", source_context={"workspace": str(project)})
    assert read is not None and "buy milk" in read["response"]
    (project / "notes.txt").write_text("project notes\n")
    assert read_home_file_the_project_lacks(_fake_agent(), "open notes.txt", session_id="s", source_context={"workspace": str(project)}) is None
    assert read_home_file_the_project_lacks(_fake_agent(), "open google.com", session_id="s", source_context={}) is None


def test_the_biggest_file_in_a_folder_is_measured_in_that_folder(monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime import fast_paths_machine

    seen = {}

    def run(intent, args, source_context=None):
        seen.setdefault(intent, dict(args))
        return None

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", run)
    monkeypatch.setattr(fast_paths_machine, "execute_authorized_runtime_tool", run, raising=False)
    fast_paths_machine.maybe_handle_direct_machine_read_request(
        _fake_agent(), "what's the biggest file in my downloads", session_id="s", source_surface="api", source_context={},
    )
    assert seen.get("machine.find_largest", {}).get("drive") == "~/Downloads"


def test_a_machine_read_counts_as_an_observation_for_the_guards():
    from core.agent_runtime.fast_paths_machine import _machine_tool_fast_path_result
    from core.model_output_guard import turn_ran_observations
    from core.runtime_execution_tools import RuntimeExecutionResult

    context: dict = {}
    execution = RuntimeExecutionResult(
        handled=True, ok=True, status="executed", response_text="python 24.7% CPU",
        details={"observation": {"intent": "machine.list_processes", "ok": True}},
    )
    _machine_tool_fast_path_result(
        _fake_agent(), user_input="what's using my CPU right now", session_id="s", source_context=context,
        intent="machine.list_processes", execution=execution, reason="machine_read_fast_path",
    )
    assert turn_ran_observations(context)
    refused: dict = {}
    _machine_tool_fast_path_result(
        _fake_agent(), user_input="x", session_id="s", source_context=refused, intent="machine.list_processes",
        execution=RuntimeExecutionResult(handled=True, ok=False, status="failed", response_text="no", details={}),
        reason="machine_read_fast_path",
    )
    assert not turn_ran_observations(refused)
