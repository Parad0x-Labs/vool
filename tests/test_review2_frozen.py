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

def test_approval_required_for_work():
    assert "hands_off" not in signals("Don't work without my approval.")

def test_plural_third_party_preference():
    assert "concise_direct" not in signals("My colleagues always prefer concise answers.")

def test_positive_concise_preference():
    assert "concise_direct" in signals("Please keep your answers concise.")

@pytest.mark.parametrize("colour",["red","purple"])
def test_short_answer_not_query_coverage(tmp_path,monkeypatch,colour):
    result=inject(tmp_path,monkeypatch,"The badge is "+colour+".","What color is the badge?")
    assert colour in result, repr(result)

@pytest.mark.parametrize("name",["Li","Lina"])
def test_short_predicate_value_is_information(tmp_path,monkeypatch,name):
    result=inject(tmp_path,monkeypatch,"The access code is ARC-583. The porter is "+name+".",
        "What is the access code and who is the porter?")
    assert "ARC-583" in result, repr(result)
    assert name in result, repr(result)
