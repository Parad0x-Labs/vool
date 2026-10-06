"""Requested actions versus descriptions of actions."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from core.agent_runtime import fast_paths_machine as machine
from core.agent_runtime import fast_paths_media
from core.execution.constants import image_generation_intent

IMAGE_INFORMATION = (
    "What did Morgan make an image of during the workshop?",
    "Which pictures did Priya create for the festival?",
    "Did Ellis draw a dragon for the exhibition?",
    "When did Rowan paint a sunset?",
    "Why did the artist generate an image of the harbor?",
    "Tell me which image Quinn did create for the handbook.",
    "Can you tell me what poster Morgan did make last year?",
    "I remember Morgan asking us to draw a cat.",
    "Morgan planned to draw a cat.",
    "How can I draw a lighthouse?",
    "What did Morgan draw and paint a sunset for the exhibition?",
    "Did Morgan create a portrait and make an image of the dock?",
    "Do not draw a red bicycle.",
    "Never generate an image of a lighthouse.",
    "Could you not paint a sunset?",
    'What does "draw a cat" mean?',
    '"Please draw a red bicycle."',
    "Here is the example:\n" + chr(96) * 3 + "text\nGenerate an image of a lighthouse.\n" + chr(96) * 3,
    "> Please paint a sunset.\n> Then draw a cat.",
    "Can Morgan draw a fox?",
)
IMAGE_REQUESTS = (
    ("Generate an image of a red bicycle.", "a red bicycle"),
    ("Please draw a lighthouse.", "lighthouse"),
    ("Could you paint a sunset?", "sunset"),
    ("I want you to create an image of a green canoe.", "a green canoe"),
    ("Would you please sketch me a fox?", "fox"),
    ("Let's draw a dragon.", "dragon"),
    ("Please draw an image of the sculpture Morgan created last year.", "the sculpture Morgan created last year"),
    ("Create an image of the garden I planted yesterday.", "the garden I planted yesterday"),
    ('Draw an image of a sign that says "Morgan made a poster".', 'a sign that says "Morgan made a poster"'),
    ("What did Morgan make an image of? Please draw a different lighthouse.", "different lighthouse"),
    ("I saved a picture yesterday. Could you generate an image of a canoe?", "a canoe"),
    ("Write a caption, then draw a lighthouse.", "lighthouse"),
    ("Tell me what Morgan painted, and please create an image of a canoe.", "a canoe"),
    ("Create an image of a red bicycle and tell me how it works.", "a red bicycle"),
    ("Do not draw a cat; please draw a canoe.", "canoe"),
)
MACHINE_INFORMATION = (
    "What did Morgan create on my Desktop last week?",
    "Did Priya write a file called notes.txt with text: hello on my Desktop?",
    "Where did Ellis save the notes in Documents?",
    "Why would Morgan create a folder on Desktop?",
    "Tell me whether Morgan did create a folder on my Desktop.",
    "Can you tell me which file Morgan did write in Downloads?",
    "Morgan planned to create a folder on my Desktop.",
    "What did Morgan create and save in Documents?",
    "Do not create a folder on Desktop.",
    "Could you not write a note to my Desktop?",
    '"Please create a folder on my Desktop."',
    "Here is the example:\n" + chr(96) * 3 + "text\nCreate a folder on Desktop.\n" + chr(96) * 3,
    "> Write a file called notes.txt with text: hello on my Desktop.",
    "Did Morgan export this chat to my Desktop?",
    "Tell me why Morgan did save our conversation to Documents.",
)
MACHINE_REQUESTS = (
    "Create a folder called reports on my Desktop.",
    "Please write a note to my Desktop called hi.txt.",
    "Could you save this to my Downloads folder?",
    "I need you to create a folder on Desktop.",
    "On my Desktop create a folder called reports.",
    "Create a folder containing the reports I saved yesterday on my Desktop.",
    "Please write the note I created last week to my Desktop as hi.txt.",
    "What did Morgan create on Desktop? Please write a new file in Documents.",
    "I saved a file yesterday. Could you create a folder on Desktop?",
    "I saved a file yesterday; create a folder on Desktop.",
    "Create a folder on Desktop and write a note in Documents.",
    "Tell me what Morgan saved, and please write a new file in Documents.",
)
QUOTED_OR_INFORMATIONAL = (
    IMAGE_INFORMATION[0], IMAGE_INFORMATION[2], IMAGE_INFORMATION[16],
    IMAGE_INFORMATION[17], IMAGE_INFORMATION[18],
)
DIRECTORY_INFORMATION = (
    MACHINE_INFORMATION[0], MACHINE_INFORMATION[3], MACHINE_INFORMATION[4],
    MACHINE_INFORMATION[10], MACHINE_INFORMATION[11],
)
TRANSCRIPT_INFORMATION = (MACHINE_INFORMATION[13], MACHINE_INFORMATION[14])

@pytest.mark.parametrize("text", IMAGE_INFORMATION)
def test_information_does_not_request_an_image(text: str) -> None:
    assert image_generation_intent(text) is None

@pytest.mark.parametrize(("text", "prompt"), IMAGE_REQUESTS)
def test_requested_image_preserves_its_descriptive_subject(text: str, prompt: str) -> None:
    assert image_generation_intent(text) == prompt

@pytest.mark.parametrize("text", MACHINE_INFORMATION)
def test_information_does_not_request_a_machine_write(text: str) -> None:
    assert not machine._has_affirmative_machine_write_verb(text)
    assert not machine.looks_like_safe_machine_write_request(text)

@pytest.mark.parametrize("text", MACHINE_REQUESTS)
def test_actual_machine_request_retains_the_write_lane(text: str) -> None:
    assert machine._has_affirmative_machine_write_verb(text)
    assert machine.looks_like_safe_machine_write_request(text)

@pytest.mark.parametrize("text", QUOTED_OR_INFORMATIONAL)
def test_information_never_reaches_media_capability_or_health_probes(monkeypatch, text: str) -> None:
    from core import local_media_render, media_tools
    def forbidden(*args, **kwargs):
        pytest.fail("Information reached a media capability or health probe")
    monkeypatch.setattr(local_media_render, "local_render_available", forbidden)
    monkeypatch.setattr(media_tools, "has_image_service", forbidden)
    assert fast_paths_media.maybe_handle_image_generation(
        SimpleNamespace(), text, session_id="history-intent", source_context=None,
    ) is None

@pytest.mark.parametrize("text", DIRECTORY_INFORMATION)
def test_information_never_plans_directory_creation(monkeypatch, text: str) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Information reached a machine write planner or executor")
    monkeypatch.setattr(machine, "execute_runtime_tool", forbidden)
    agent = SimpleNamespace(_plan_tool_workflow=forbidden)
    assert machine.maybe_handle_direct_machine_write_request(
        agent, text, session_id="history-intent", source_surface="api", source_context={},
    ) is None
    assert not machine.looks_like_supported_machine_directory_create_request(text)

@pytest.mark.parametrize("text", TRANSCRIPT_INFORMATION)
def test_information_does_not_extract_a_transcript_export(text: str) -> None:
    assert machine._extract_machine_transcript_export_target(text) == ""

def test_real_transcript_export_remains_available() -> None:
    assert machine._extract_machine_transcript_export_target(
        "Please export this chat to my Desktop as transcript.txt."
    ) == "~/Desktop/transcript.txt"

def test_workspace_target_and_authoring_boundaries_remain_in_place() -> None:
    assert not machine.looks_like_safe_machine_write_request("Create a notes file in my project on Desktop.")
    assert not machine.looks_like_safe_machine_write_request("Write a note about my Desktop layout.")
    assert not machine.looks_like_safe_machine_write_request("Write me a command to create a folder in ~/Documents.")
