"""Frozen review of subject ownership in profile-admission revision 4."""
import json
from core.context_scope import ContextAccessPolicy
from core.memory.files import memory_entries_path,user_heuristics_path
from core.persistent_memory import append_conversation_event,maybe_handle_memory_command
LOCAL={"surface":"cli","platform":"cli"}
def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
def finalize(text):
    append_conversation_event(session_id="review4",user_input=text,assistant_output="Acknowledged.",source_context=LOCAL)
def adopted():
    return [r for r in rows(memory_entries_path()) if r.get("source")=="adopted_user_declaration"]
def test_third_party_adoption_does_not_adopt_for_user():
    finalize('> I prefer illustrated summaries.\n\nThe editor decided to adopt this for her own newsletter.')
    assert not adopted(),adopted()
def test_mention_of_button_label_does_not_adopt():
    finalize('> I prefer numbered paragraphs.\n\nThe button labelled Adopt this is broken.')
    assert not adopted(),adopted()
def test_first_person_framing_does_not_own_someone_elses_motto():
    finalize('I think the editor\'s motto is "Be brief".')
    assert not rows(user_heuristics_path()),rows(user_heuristics_path())
def test_actual_user_adoption_remains_available():
    finalize('> I prefer illustrated agendas.\n\nI choose to adopt this for my planning sessions.')
    assert any("illustrated agendas" in json.dumps(r).lower() for r in adopted()),rows(memory_entries_path())
def test_actual_personal_quoted_value_remains_intact():
    ContextAccessPolicy.for_request(session_id="review4-command",source_context=LOCAL)
    handled,reply=maybe_handle_memory_command('Remember my preferred motto is "Stay clear".',session_id="review4-command",source_context=LOCAL)
    assert handled,reply
    p=[r for r in rows(memory_entries_path()) if r.get("scope")=="user_profile"]
    assert any("stay clear" in json.dumps(r).lower() for r in p),rows(memory_entries_path())
