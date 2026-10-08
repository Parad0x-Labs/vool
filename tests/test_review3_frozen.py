"""Independent frozen review-2 acceptance; expectations declared before execution."""
import json

import pytest


def signals(text):
    from core.memory.files import user_heuristics_path
    from core.persistent_memory import append_conversation_event
    append_conversation_event(session_id="review2-profile",user_input=text,
        assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    p=user_heuristics_path()
    return [json.loads(line)["signal"] for line in p.read_text().splitlines()] if p.exists() else []

def inject(tmp_path,monkeypatch,text,query):
    import core.context_retrieval as cr
    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.vool_memory import VoolMemory
    sid="review2-recall"
    ensure_chat_namespace(sid,grant_current_receipts=False)
    monkeypatch.setattr(cr,"embed_stamped",lambda s:([1.,0.,0.],"review"))
    mem=VoolMemory(runtime_home=tmp_path)
    scope=cr._session_scope_key(sid)
    mem.node_store(content=text,keywords=[],tags=["user","session:"+scope],
        context_description="session="+scope+" role=user",
        embedding=[1.,0.,0.],embedding_backend="review")
    mem.close()
    result=cr._capsule_v2_inject_retrieved(sid,query,[],
        budget=resolve_budget(bucket="B",role="general"),runtime_home=str(tmp_path))
    return "\n".join(m["content"] for m in result)


@pytest.mark.parametrize("text",["Never act without approval.","Don't proceed without confirmation."])
def test_approval_required_short_clause(text):
    assert "hands_off" not in signals(text)

def test_positive_autonomy():
    assert "hands_off" in signals("Proceed without asking.")

def test_single_character_answer(tmp_path,monkeypatch):
    result=inject(tmp_path,monkeypatch,"The terminal is Z.","Which terminal?")
    assert "Z" in result,repr(result)

@pytest.mark.parametrize("name",["Will","Evan"])
def test_name_is_not_function_word(tmp_path,monkeypatch,name):
    result=inject(tmp_path,monkeypatch,"The guide is "+name+".","Who is the guide?")
    assert name in result,repr(result)

def test_third_party_remains_unowned():
    assert "concise_direct" not in signals("Our editors always prefer brief answers.")

def test_owned_preference_remains_valid():
    assert "concise_direct" in signals("Always keep your replies concise.")
