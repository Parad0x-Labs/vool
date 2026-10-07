"""Frozen review: actor ownership, hypothetical adoption, possessive typography."""
import json

from core.context_scope import ContextAccessPolicy
from core.memory.files import memory_entries_path, user_heuristics_path
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command

LOCAL={"surface":"cli","platform":"cli"}
def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
def finalize(text):
    append_conversation_event(session_id="review5",user_input=text,assistant_output="Acknowledged.",source_context=LOCAL)
def adopted():
    return [r for r in rows(memory_entries_path()) if r.get("source")=="adopted_user_declaration"]
def test_witness_is_not_the_adopting_subject():
    finalize('> I prefer handwritten digests.\n\nI watched her adopt this for the museum bulletin.')
    assert not adopted(),adopted()
def test_supposed_adoption_is_not_actual_adoption():
    finalize('> I prefer color-coded minutes.\n\nSuppose I adopt this for the next workshop.')
    assert not adopted(),adopted()
def test_typographic_possessive_keeps_third_party_value_out_of_profile():
    finalize('My editor’s motto is "Be direct".')
    assert not rows(user_heuristics_path()),rows(user_heuristics_path())
def test_actual_first_person_choice_remains_available():
    finalize('> I prefer annotated agendas.\n\nI choose to adopt this for my meetings.')
    assert any("annotated agendas" in json.dumps(r).lower() for r in adopted()),rows(memory_entries_path())
def test_actual_owned_quoted_value_remains_intact():
    ContextAccessPolicy.for_request(session_id="review5-command",source_context=LOCAL)
    handled,reply=maybe_handle_memory_command('Remember my preferred sign-off is "Be concise".',session_id="review5-command",source_context=LOCAL)
    assert handled,reply
    p=[r for r in rows(memory_entries_path()) if r.get("scope")=="user_profile"]
    assert any("be concise" in json.dumps(r).lower() for r in p),rows(memory_entries_path())
