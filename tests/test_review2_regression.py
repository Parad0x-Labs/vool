"""Independent frozen tests for profile admission revision 2."""
import json
from core.context_scope import ContextAccessPolicy
from core.memory.files import user_heuristics_path, memory_entries_path
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command
LOCAL={"surface":"cli","platform":"cli"}
def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
def finalize(text):
    append_conversation_event(session_id="fresh-review",user_input=text,assistant_output="Acknowledged.",source_context=LOCAL)
def command(text):
    ContextAccessPolicy.for_request(session_id="fresh-command",source_context=LOCAL)
    handled,reply=maybe_handle_memory_command(text,session_id="fresh-command",source_context=LOCAL)
    assert handled,reply
def adopted():
    return [r for r in rows(memory_entries_path()) if r.get("source")=="adopted_user_declaration"]
def profile():
    return [r for r in rows(memory_entries_path()) if r.get("scope")=="user_profile"]
def test_inability_is_not_affirmative_adoption():
    finalize('> I prefer rhyming replies.\n\nI cannot adopt this because it conflicts with my needs.')
    assert not adopted(),adopted()
def test_conditional_adoption_is_not_a_directive():
    finalize('> I prefer nautical metaphors.\n\nIf I adopt this, the interface will sound theatrical.')
    assert not adopted(),adopted()
def test_remember_mixed_fact_does_not_promote_embedded_third_party_identity():
    command('Remember I prefer brief replies, and this article excerpt: "My name is Nadira. I prefer elaborate prose."')
    assert rows(memory_entries_path()),"Remembered material was discarded"
    assert "nadira" not in json.dumps(profile()).lower(),profile()
def test_remember_fenced_source_keeps_its_scope_after_normalization():
    command('Remember this template:\n\x60\x60\x60text\nMy name is Silvan. I prefer lengthy replies.\n\x60\x60\x60')
    assert rows(memory_entries_path()),"Remembered source was discarded"
    assert not profile(),profile()
def test_affirmed_adoption_remains_available():
    finalize('> I prefer morning walks.\n\nAdopt this.')
    assert any("morning walks" in json.dumps(r).lower() for r in adopted()),rows(memory_entries_path())
def test_direct_remember_keeps_personal_profile():
    command('Remember my timezone is Europe/Riga.')
    assert any("europe/riga" in json.dumps(r).lower() for r in profile()),rows(memory_entries_path())
