"""Independent frozen review-2 acceptance; expectations declared before execution."""
import json
import pytest

def signals(text):
    from core.persistent_memory import append_conversation_event
    from core.memory.files import user_heuristics_path
    append_conversation_event(session_id="review2-profile",user_input=text,
        assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    p=user_heuristics_path()
    return [json.loads(line)["signal"] for line in p.read_text().splitlines()] if p.exists() else []

def inject(tmp_path,monkeypatch,text,query):
    from core.vool_memory import VoolMemory
    from core.context_namespace import ensure_chat_namespace
    import core.context_retrieval as cr
    from core.context_capsule_v2 import resolve_budget
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


import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
def _injected_block(tmp_path, monkeypatch, text, query, transcript):
    from core.vool_memory import VoolMemory
    from core.context_capsule_v2 import resolve_budget

    sid = "fresh5-recall"
    ensure_chat_namespace(sid, grant_current_receipts=False)
    monkeypatch.setattr(cr, "embed_stamped", lambda s: ([1.0, 0.0, 0.0], "fresh5"))
    mem = VoolMemory(runtime_home=tmp_path)
    scope = cr._session_scope_key(sid)
    mem.node_store(
        content=text,
        keywords=[],
        tags=["user", "session:" + scope],
        context_description="session=" + scope + " role=user",
        embedding=[1.0, 0.0, 0.0],
        embedding_backend="fresh5",
    )
    mem.close()
    # Real reopened store -> scoped search -> distillation -> packed context.
    out = cr._capsule_v2_inject_retrieved(
        sid, query,
        [{"role": "user", "content": t} for t in transcript],
        budget=resolve_budget(bucket="B", role="general"),
        runtime_home=str(tmp_path),
    )
    return next(
        (
            str(m.get("content") or "")
            for m in out
            if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")
        ),
        "",
    )




@pytest.mark.parametrize("record,query,expected",[
 ("The cabin trim is violet.","Is the cabin trim violet?",["violet"]),
 ("The service bay is 6.","Is the service bay 6?",["6"]),
 ("When the siren sounds, the hatch must stay closed.","Does the hatch stay closed when the siren sounds?",["must","closed"]),
 ("The gallery is not open on Mondays.","Is the gallery open on Mondays?",["not","Mondays"]),
])
def test_assertions_survive(tmp_path,monkeypatch,record,query,expected):
    block=_injected_block(tmp_path,monkeypatch,record,query,[])
    for value in expected:
        assert value in block,repr(block)

def test_actual_evidence_deduplicates(tmp_path,monkeypatch):
    record="The repair cupboard access code is ELM-284."
    block=_injected_block(tmp_path,monkeypatch,record,"What is the repair cupboard access code?",[record])
    assert "ELM-284" not in block,repr(block)

def test_pure_question_is_not_answer(tmp_path,monkeypatch):
    record="Is the cabin trim violet?"
    block=_injected_block(tmp_path,monkeypatch,record,record,[])
    assert not block,repr(block)
