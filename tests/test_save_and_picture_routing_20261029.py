"""Saving goes where it was asked, picture edits are not image generation, and spoken note titles are cleaned.

Audit 2026-10-29 (tool re-audit, items N5, N6, N10), reproduced in-process at e5b60c60 with VoolAgent.run_once on the
test backend (no model):

* "make the cat picture half the size" / "make the cat picture black and white" -> image_generate_pending_approval;
  "save that as a jpg onto my desktop please" -> memory_command "Locked in. I'll remember that."
* "keep a note for me: garage keypad code is 5523", "save this one: buy zucchini ..." -> memory_command, no note.
* "save nadia, shes my yoga teacher, 086 555 0166" -> memory_command; "nadias got a new number now, its ..." and
  "his number changed, ..." reached the model with no Contacts tool required.
* "save this as a set called night owl: ..." -> memory_command, no set.
* "can you read my note about the flat viewing in apple notes?" -> looked for the title "the flat viewing in apple
  notes?"; "open my note about the flat viewing please" -> "the flat viewing please"; "show me my notes" -> searched
  for the words "show me my notes".
"""
from __future__ import annotations

import pytest

from core.contacts.requests import contact_detail_changed, storage_object_is_a_contact
from core.creative_set import set_save_request
from core.execution.constants import image_generation_intent, picture_edit_request
from core.execution_requirements import classify_requirements
from core.operator.models import OperatorActionIntent
from core.operator.notes import find_notes_query, parse_note_request
from core.operator.parser import parse_operator_action_intent
from core.persistent_memory import maybe_handle_memory_command


# --- N5: editing an existing picture ------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "make the cat picture half the size, its too big for what i need",
    "make the cat picture black and white",
    "turn my holiday photo greyscale",
    "make it black and white",
    "save that as a jpg onto my desktop please",
    "export the screenshot as a png",
    "resize my photo to 800 pixels wide",
    "crop the cat picture in my Pictures into a square please",
])
def test_an_edit_of_a_picture_that_exists_is_not_a_render(text):
    assert picture_edit_request(text)
    assert image_generation_intent(text) is None


@pytest.mark.parametrize("text, subject", [
    ("make a picture of a cat", "a cat"),
    ("make me a black and white picture of a lighthouse", "a lighthouse"),
    ("generate an image of a fox in black and white", "a fox in black and white"),
    ("draw a dragon", "dragon"),
])
def test_asking_for_a_new_picture_still_renders(text, subject):
    assert not picture_edit_request(text)
    assert image_generation_intent(text) == subject


@pytest.mark.parametrize("text", [
    "make the essay half the size", "make it shorter", "make the font bigger", "save that as a note",
    "turn the lights off",
])
def test_resizing_things_that_are_not_pictures_is_not_a_picture_edit(text):
    assert not picture_edit_request(text)


# --- N6: notes, contacts and sets are not memories ------------------------------------------------------------------

@pytest.mark.parametrize("text, body", [
    ("keep a note for me: garage keypad code is 5523", "garage keypad code is 5523"),
    ("save this one: buy zucchini and halloumi for saturdays bbq", "buy zucchini and halloumi for saturdays bbq"),
    ("save this: buy zucchini", "buy zucchini"),
    ("please save this: call the bank at 10:30", "call the bank at 10:30"),
])
def test_a_note_to_keep_goes_to_the_notes_tool_with_its_words(text, body):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "save_note"
    assert parse_note_request(text)["body"] == body
    handled, _ = maybe_handle_memory_command(text, session_id="n6-notes")
    assert handled is False


@pytest.mark.parametrize("text", [
    "keep the note short", "save this for later", "save it", "save this: my bike rack is spot 12 on level 2",
    "save this: I take the 8:10 train",
])
def test_words_about_a_note_or_a_bare_save_are_not_a_note_save(text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "save_note"


@pytest.mark.parametrize("text", [
    "save nadia, shes my yoga teacher, 086 555 0166",
    "save petras, the window guy, 086 555 0177",
    "add Jo Bloggs - jo@example.test",
])
def test_saving_a_named_person_with_a_number_is_a_contact_save(text):
    assert storage_object_is_a_contact(text)
    assert "contacts" in classify_requirements(text, task_class="chat_conversation").allowed_toolsets
    handled, _ = maybe_handle_memory_command(text, session_id="n6-contacts")
    assert handled is False


@pytest.mark.parametrize("text", [
    "save this: call 086 555 0166", "save money, 1000000 a year", "remember that my dentist is Dr Kelly, 01 555 0199",
])
def test_a_save_without_a_named_person_and_a_detail_is_not_a_contact_save(text):
    assert not storage_object_is_a_contact(text)


@pytest.mark.parametrize("text", [
    "nadias got a new number now, its 086 555 0168",
    "his number changed, its 086 555 0188 now",
    "Petra has a new email: petra@example.test",
    "new number for Tom: 087 123 4567",
])
def test_a_changed_number_for_someone_seats_the_contacts_tools(text):
    assert contact_detail_changed(text)
    requirements = classify_requirements(text, task_class="chat_conversation")
    assert requirements.tools_required and "contacts" in requirements.allowed_toolsets


@pytest.mark.parametrize("text", [
    "ive got a new number", "I have a new phone", "my number changed", "the bus has a new number",
    "whats nadias number", "there is a new number to call",
])
def test_the_owners_own_number_or_a_thing_is_not_a_contact_update(text):
    assert not contact_detail_changed(text)


def test_save_as_a_named_set_is_the_set_lane_not_memory():
    text = "save this as a set called night owl: a tired detective in a trench coat, rainy noir city, teal and amber"
    assert set_save_request(text) == ("night owl", "a tired detective in a trench coat, rainy noir city, teal and amber")
    assert set_save_request("save this as a set called night owl") == ("night owl", "")
    handled, _ = maybe_handle_memory_command(text, session_id="n6-sets")
    assert handled is False


@pytest.mark.parametrize("text", [
    "set a timer called tea", "save my settings", "now do the next shot with the night owl set",
    "remember that my favourite set is the night owl one",
])
def test_other_set_words_are_not_a_set_save(text):
    assert set_save_request(text) is None


@pytest.mark.parametrize("text", [
    "remember that my sister lives in Cork",
    "remember that nadia's got a new number, 086 555 0168",
    "remember my garage code is 5523",
    "save that I prefer tea over coffee",
])
def test_remember_about_the_person_stays_a_memory(text):
    from core.persistent_memory import _memory_capture_match

    assert _memory_capture_match(text) is not None
    assert not storage_object_is_a_contact(text)
    assert set_save_request(text) is None and not picture_edit_request(text)
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "save_note"


# --- N10: spoken note titles ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, kind, title", [
    ("can you read my note about the flat viewing in apple notes?", "apple_note_read", "flat viewing"),
    ("show my note about rent in apple notes", "apple_note_read", "rent"),
    ("open my note about the flat viewing please", "show_note", "flat viewing"),
    ("what's in my note about the garage?", "show_note", "garage"),
    ('read the note titled "The Plan"', "show_note", "The Plan"),
    ("show the note called Groceries", "show_note", "Groceries"),
    ("read my apple note titled Groceries", "apple_note_read", "Groceries"),
])
def test_a_spoken_note_title_drops_the_app_the_please_and_the_question_mark(text, kind, title):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind
    assert intent.target_label == title


@pytest.mark.parametrize("text", [
    "show me my notes", "show me my notes please", "can you list all my notes?", "show my notes",
])
def test_asking_to_see_the_notes_lists_them(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "find_notes"
    assert find_notes_query(intent) == ""


def test_a_notes_search_still_searches_its_terms():
    intent = parse_operator_action_intent("find my notes about the boiler?")
    assert intent is not None and intent.kind == "find_notes"
    assert find_notes_query(intent) == "boiler"
    assert find_notes_query(OperatorActionIntent(kind="find_notes", raw_text="search notes for venue")) == "venue"


@pytest.mark.parametrize("text", ["list my apple notes", "show me my apple notes"])
def test_the_apple_notes_list_is_unchanged(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "apple_note_list"


# --- On the turn: the real front door, test backend, no model --------------------------------------------------------

@pytest.fixture
def turn(tmp_path, runtime_storage_reset, monkeypatch):
    import tempfile
    from pathlib import Path

    import core.creative_set as cs
    from core.agent_runtime.agent import VoolAgent

    workspace = Path(tempfile.mkdtemp(prefix="vool-n6-ws-"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "sets.json")
    agent = VoolAgent(backend_name="test-backend", device="n6-turns", persona_id="default")

    def run(text: str, chat: str = "n6-turn") -> dict:
        return agent.run_once(text, session_id_override=chat, source_context={"chat_id": chat})

    run.workspace = workspace
    return run


def test_turn_picture_edits_are_answered_plainly(turn):
    for text in ("make the cat picture half the size, its too big for what i need",
                 "make the cat picture black and white", "save that as a jpg onto my desktop please"):
        result = turn(text)
        assert result.get("route") == "deterministic:picture_edit_unsupported", result.get("route")
        reply = str(result.get("response"))
        assert "can't edit pictures" in reply and "Locked in" not in reply and "Generating an image" not in reply


def test_turn_keep_a_note_saves_a_note_with_its_words(turn):
    result = turn("keep a note for me: garage keypad code is 5523")
    assert "Note saved" in str(result.get("response")) and "5523" in str(result.get("response"))
    notes = list((turn.workspace / "notes").glob("*.md"))
    assert len(notes) == 1 and "garage keypad code is 5523" in notes[0].read_text()


def test_turn_save_as_a_set_reaches_the_set_tool_not_memory(turn):
    result = turn("save this as a set called night owl: a tired detective in a trench coat, rainy noir city")
    assert result.get("route", "").startswith("deterministic:creative_set_"), result.get("route")
    assert "Locked in" not in str(result.get("response"))


def test_turn_show_me_my_notes_lists_them(turn):
    result = turn("show me my notes")
    reply = str(result.get("response"))
    assert "show me my notes" not in reply and ("no notes in this workspace yet" in reply or "Notes in this workspace" in reply)


def test_turn_remember_still_remembers(turn):
    result = turn("remember that my sister lives in Cork")
    assert result.get("route") == "deterministic:memory_command"
