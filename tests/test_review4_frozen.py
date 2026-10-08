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


import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace


def _injected_block(tmp_path, monkeypatch, text, query, transcript):
    from core.context_capsule_v2 import resolve_budget
    from core.vool_memory import VoolMemory

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



@pytest.mark.parametrize("text",["Do not delete without approval.","Never simply deploy without confirmation."])
def test_approval_required(text):
    assert "hands_off" not in signals(text)

def test_positive_autonomy():
    assert "hands_off" in signals("Do not ask before routine edits.")

@pytest.mark.parametrize("record,query,value",[
 ("The loading bay is 8.","Which loading bay?","8"),
 ("The switch state is ON.","What is the switch state?","ON"),
 ("The canopy is green.","Is the canopy green?","green")
])
def test_evidence_reaches_context(tmp_path,monkeypatch,record,query,value):
    block=_injected_block(tmp_path,monkeypatch,record,query,[])
    assert value in block,repr(block)

def test_existing_evidence_deduplicates(tmp_path,monkeypatch):
    text="The greenhouse service entrance code is FIR-935."
    block=_injected_block(tmp_path,monkeypatch,text,"What is the greenhouse service entrance code?",[text])
    assert "FIR-935" not in block,repr(block)
