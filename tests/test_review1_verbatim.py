"""Fresh independent review cases, frozen before execution; no product edits."""
import json,time
from dataclasses import replace
from pathlib import Path
import pytest
from core.persistent_memory import append_conversation_event
from core.memory.files import user_heuristics_path
import core.context_retrieval as cr
from core.vool_memory import VoolMemory
from core.context_namespace import ensure_chat_namespace
from core.context_capsule_v2 import resolve_budget,estimate_tokens
from core.context_history_authority import enforce_history_budget

def _signals(text):
    append_conversation_event(session_id="review-profile",user_input=text,assistant_output="Acknowledged.",source_context={"surface":"cli","platform":"cli"})
    p=user_heuristics_path()
    return [r["signal"] for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())] if p.exists() else []

def test_approval_required_is_not_hands_off():
    assert "hands_off" not in _signals("Never act without my approval.")

def test_physical_clearance_is_not_response_style():
    assert "concise_direct" not in _signals("Please keep the freezer clear of ice.")

def test_third_party_habit_is_not_user_preference():
    assert "concise_direct" not in _signals("My colleague always wants concise answers.")

def test_genuine_response_preference_still_stores():
    assert "concise_direct" in _signals("Please keep your answers concise.")

def _inject(tmp_path,monkeypatch,text,query,budget):
    sid="review-recall"
    ensure_chat_namespace(sid,grant_current_receipts=False)
    monkeypatch.setattr(cr,"embed_stamped",lambda s:([1.,0.,0.],"review"))
    mem=VoolMemory(runtime_home=tmp_path)
    scope=cr._session_scope_key(sid)
    mem.node_store(content=text,keywords=[],tags=["user","session:"+scope],context_description="session="+scope+" role=user",embedding=[1.,0.,0.],embedding_backend="review")
    mem.close()
    # Real reopened store -> scoped search -> distillation -> packed model context.
    result=cr._capsule_v2_inject_retrieved(sid,query,[],budget=budget,runtime_home=str(tmp_path))
    return "\n".join(m["content"] for m in result)

def test_coordinated_facts_both_reach_model_context(tmp_path,monkeypatch):
    text=_inject(tmp_path,monkeypatch,"The access code is GAL-482 and the caretaker is Amara.","What is the access code and who is the caretaker?",resolve_budget(bucket="B",role="general"))
    assert "GAL-482" in text and "Amara" in text,text

def test_injection_header_is_inside_budget(tmp_path,monkeypatch):
    budget=replace(resolve_budget(bucket="B",role="general"),free_tokens=20)
    text=_inject(tmp_path,monkeypatch,"The gallery access code is GAL-482.","What is the gallery access code?",budget)
    # Empty output is safe for this budget-bound check; visibility is tested separately.
    payload=text.replace("<retrieved_context>","").replace("</retrieved_context>","").strip()
    assert estimate_tokens(payload)<=budget.free_tokens,{"tokens":estimate_tokens(payload),"allowed":budget.free_tokens,"output":text}

def test_line_shrink_does_not_remove_condition_from_permission():
    summary={"role":"assistant","_history_retention_priority":1,"content":"<context_summary>\nThe archive badge may be shared\nonly with the curator after "+("identity verification and logging "*12)+".\n</context_summary>"}
    newest=[{"role":"user","content":"Continue."},{"role":"assistant","content":"Ready."}]
    result=enforce_history_budget([summary,*newest],max_messages=10,max_chars=135)
    blob="\n".join(x["content"] for x in result)
    assert "badge may be shared" not in blob or "only with the curator" in blob,blob

def test_short_gap_facts_resist_access_popularity(tmp_path,monkeypatch):
    now=2_000_000_000.
    monkeypatch.setattr(time,"time",lambda:now)
    sid="review-rank";ensure_chat_namespace(sid,grant_current_receipts=False)
    scope=cr._session_scope_key(sid)
    mem=VoolMemory(db_path=tmp_path/"rank.db")
    for text,ts in [("Willow delivery window is Tuesday.",now-86400),("Willow delivery window is Sunday.",now-3600)]:
        mem.node_store(content=text,keywords=[],tags=["user","session:"+scope],context_description="session="+scope+" role=user",embedding=[0.,1.,0.],embedding_backend="review",timestamp=ts)
    def query(q,k=2):
        return [n.content for n,s in mem.node_search_hybrid(q,[1.,0.,0.],top_k=k,session_id=sid,query_embedding_backend="review")]
    before=query("Willow delivery window")
    for _ in range(60): query("Tuesday",1)
    after=query("Willow delivery window")
    mem.close()
    assert after[0]=="Willow delivery window is Sunday.",{"before":before,"after":after}
