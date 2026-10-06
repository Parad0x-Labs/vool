"""Frozen review of admission revision 3: quoted values and affirmative adoption."""
import json
from core.context_scope import ContextAccessPolicy
from core.memory.files import memory_entries_path
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command
LOCAL={"surface":"cli","platform":"cli"}
def rows():
    p=memory_entries_path()
    return [json.loads(s) for s in p.read_text().splitlines() if s.strip()] if p.exists() else []
def finalize(text):
    append_conversation_event(session_id="review3",user_input=text,assistant_output="Acknowledged.",source_context=LOCAL)
def command(text):
    ContextAccessPolicy.for_request(session_id="review3-command",source_context=LOCAL)
    handled,reply=maybe_handle_memory_command(text,session_id="review3-command",source_context=LOCAL)
    assert handled,reply
def adopted():
    return [r for r in rows() if r.get("source")=="adopted_user_declaration"]
def profile():
    return [r for r in rows() if r.get("scope")=="user_profile"]
def test_emphatic_negation_is_not_adoption():
    finalize('> I prefer theatrical summaries.\n\nI will never, ever adopt this.')
    assert not adopted(),adopted()
def test_modal_with_adverb_is_not_an_affirmative_directive():
    finalize('> I prefer rhymed release notes.\n\nI could potentially adopt this after a trial.')
    assert not adopted(),adopted()
def test_quoted_personal_value_is_not_severed_from_its_declaration():
    command('Remember my preferred motto is "Be honest".')
    assert any("be honest" in json.dumps(r).lower() for r in profile()),rows()
    assert not any(r.get("source")=="remembered_source_material" for r in rows()),rows()
def test_multiple_third_party_quotes_stay_out_of_personal_profile():
    command('Remember my timezone is Europe/Oslo, alongside these interview excerpts: "My name is Vesta." and "I prefer poetic reports."')
    p=json.dumps(profile()).lower()
    assert "europe/oslo" in p,rows()
    assert "vesta" not in p and "poetic reports" not in p,rows()
    source=[r for r in rows() if r.get("source")=="remembered_source_material" and r.get("scope")=="chat"]
    s=json.dumps(source).lower()
    assert "vesta" in s and "poetic reports" in s,rows()
def test_unambiguous_adoption_still_writes():
    finalize('> I like numbered agendas.\n\nThat also describes me.')
    assert any("numbered agendas" in json.dumps(r).lower() for r in adopted()),rows()
