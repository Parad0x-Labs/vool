"""Reported instruction boundaries and retained literal values."""
from __future__ import annotations
from pathlib import Path
import pytest
from core.agent_runtime import fast_paths_machine as machine
from core.execution.constants import image_generation_intent

@pytest.mark.parametrize("text", (
    "“The note begins here.\nDraw a lighthouse.\nThe note ends here.”",
    "‘A copied example.\nPlease generate an image of a canoe.\nEnd of example.’",
    "The article says:\nDraw a lighthouse.",
    "Quoted instructions:\nPlease generate an image of a canoe.",
))
def test_reported_image_instruction_is_not_a_requested_render(text: str) -> None:
    assert image_generation_intent(text) is None

@pytest.mark.parametrize("text", (
    "“The note begins here.\nCreate a folder on my Desktop.\nThe note ends here.”",
    "‘A copied example.\nPlease write a file to Documents.\nEnd of example.’",
    "The article says:\nCreate a folder on my Desktop.",
    "Quoted instructions:\nPlease write a file to Documents.",
))
def test_reported_machine_instruction_is_not_a_requested_write(text: str) -> None:
    assert not machine._has_affirmative_machine_write_verb(text)
    assert not machine.looks_like_safe_machine_write_request(text)

def test_real_file_request_keeps_typographic_literal_value(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "Desktop"
    root.mkdir()
    monkeypatch.setattr(machine, "_resolve_safe_machine_root_directory", lambda **kwargs: root)
    target = machine._extract_machine_text_file_write_target(
        "Please write a file called fresh.txt on my Desktop with text: “quoted text”",
        source_context={},
    )
    assert target is not None
    assert target["content"] == "“quoted text”"
    assert Path(target["path"]) == root / "fresh.txt"

def test_real_image_request_keeps_typographic_literal_subject() -> None:
    assert image_generation_intent(
        "Please draw an image of a sign saying “quiet garden”."
    ) == "a sign saying “quiet garden”"

def test_explicit_request_outside_reported_paragraph_remains_requested() -> None:
    assert machine._has_affirmative_machine_write_verb(
        "The article says:\nCreate a folder on my Desktop.\n\nPlease write a new file in Documents."
    )

def test_undelimited_colon_body_is_conservatively_treated_as_data() -> None:
    assert not machine._has_affirmative_machine_write_verb(
        "Here are the next steps:\nCreate a folder on Desktop."
    )
