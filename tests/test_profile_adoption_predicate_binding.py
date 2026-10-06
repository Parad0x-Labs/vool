"""Regression checks for binding the user's adoption predicate and possessors."""
import json
import pytest
from core.memory.files import memory_entries_path, user_heuristics_path
from core.persistent_memory import append_conversation_event

def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

@pytest.mark.parametrize("sentence", [
    "The form asks whether I will adopt this for the workshop.",
    "The placard reads adopt this, remember it for me.",
])
def test_reported_adoption_is_not_an_instruction(sentence):
    append_conversation_event(session_id="predicate-owner",user_input="> I prefer folded summaries.\n\n"+sentence,assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    assert not [r for r in _rows(memory_entries_path()) if r.get("source")=="adopted_user_declaration"]

@pytest.mark.parametrize("sentence", [
    "I have decided to adopt this.",
    "The trainer and I will adopt this.",
    "Yes, please adopt this.",
    "Same here, remember it for me.",
])
def test_explicit_adoption_constructions_still_work(sentence):
    append_conversation_event(session_id="predicate-positive",user_input="> I prefer folded summaries.\n\n"+sentence,assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    assert any(r.get("source")=="adopted_user_declaration" and "folded summaries" in r.get("text","") for r in _rows(memory_entries_path()))

@pytest.mark.parametrize("possessor", ["editors'", "editors’", "editorʼs"])
def test_third_party_possessive_typography_is_not_user_preference(possessor):
    append_conversation_event(session_id="possessive-owner",user_input=f'My {possessor} motto is "Be blunt".',assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    assert not _rows(user_heuristics_path())


@pytest.mark.parametrize("text,expected", [
    ("Please inspect this report.", False),
    ("Use GitHub repos for implementation examples.", True),
])
def test_source_preferences_require_whole_marker_words(text, expected):
    append_conversation_event(session_id="source-markers",user_input=text,assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    found=any(r.get("signal")=="github_repos" for r in _rows(user_heuristics_path()))
    assert found is expected


@pytest.mark.parametrize("text,expected", [
    ("That fits, honestly.", False),
    ("Please be honest in your replies.", True),
])
def test_style_markers_do_not_match_incidental_adverbs(text, expected):
    append_conversation_event(session_id="style-markers",user_input=text,assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    found=any(r.get("signal")=="brutal_honest" for r in _rows(user_heuristics_path()))
    assert found is expected
