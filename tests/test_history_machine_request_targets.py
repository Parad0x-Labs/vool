"""Request clause ownership of machine target extraction."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from core.agent_runtime import fast_paths_machine as machine

def test_a_prior_question_cannot_supply_the_filename_or_content(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Desktop"
    root.mkdir()
    monkeypatch.setattr(machine, "_resolve_safe_machine_root_directory", lambda **kwargs: root)
    target = machine._extract_machine_text_file_write_target(
        "Did Morgan write a file called old.txt on my Desktop with text: old? "
        "Please write a file called fresh.txt on my Desktop with text: new",
        source_context={},
    )
    assert target is not None
    assert Path(target["path"]) == root / "fresh.txt"
    assert target["content"] == "new"

def test_a_prior_question_cannot_supply_the_transcript_destination() -> None:
    target = machine._extract_machine_transcript_export_target(
        "Did Morgan export our conversation to Desktop as old.txt? "
        "Please export this chat to Documents as fresh.txt"
    )
    assert target == "~/Documents/fresh.txt"

def test_a_prior_transcript_question_does_not_turn_a_file_request_into_an_export() -> None:
    assert machine._extract_machine_transcript_export_target(
        "Did Morgan export our conversation to Desktop? "
        "Please write a file called fresh.txt to Documents with text: new"
    ) == ""

@pytest.mark.parametrize("text", (
    "Create a folder called reports on my Desktop.",
    "What did Morgan create yesterday? Please create a folder on Desktop.",
))
def test_a_requested_directory_creation_still_reaches_the_existing_planner(monkeypatch, text: str) -> None:
    calls = []
    def planner(**kwargs):
        calls.append(("plan", kwargs["user_text"]))
        return SimpleNamespace(next_payload={"intent": "machine.ensure_directory", "arguments": {"path": "~/Desktop/reports"}})
    def executor(intent, arguments, **kwargs):
        calls.append(("execute", intent, arguments))
        return SimpleNamespace()
    monkeypatch.setattr(machine, "execute_runtime_tool", executor)
    monkeypatch.setattr(machine, "_machine_tool_fast_path_result", lambda *args, **kwargs: {"intent": kwargs["intent"]})
    result = machine.maybe_handle_direct_machine_write_request(
        SimpleNamespace(_plan_tool_workflow=planner), text,
        session_id="history-intent", source_surface="api", source_context={},
    )
    assert result == {"intent": "machine.ensure_directory"}
    assert [call[0] for call in calls] == ["plan", "execute"]

def test_a_preposed_destination_is_preserved_by_request_scoping(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Desktop"
    root.mkdir()
    def resolve(*, folder, root_label):
        assert root_label == "Desktop"
        return root
    monkeypatch.setattr(machine, "_resolve_safe_machine_root_directory", resolve)
    target = machine._extract_machine_text_file_write_target(
        "On my Desktop write a file called fresh.txt with text: new",
        source_context={},
    )
    assert target is not None and Path(target["path"]) == root / "fresh.txt"
