"""Offline source-owner process restart proof. Contributor: sls_0x.

Live statements use store_turn. Tied imported clocks use occurrence_store's
supported recorded_at argument; no SQL write or live capture-clock fabrication.
Hash derivatives exercise storage lifecycle only, not neural retrieval quality.
"""
from __future__ import annotations
import argparse,dataclasses,hashlib,json,os,sys,urllib.error,urllib.request
from pathlib import Path

SCOPE="restart-source-order"
FOREIGN="restart-foreign-sibling"
LIVE="restart-live-source"
TIED=1751328000.0
OLD="The amber cabinet access code is 4127."
NEW="The amber cabinet access code is 8643."
QUESTION="What is the beacon shelf entry code?"
ANSWER="It is 6532."
UNRELATED="The unrelated tea crate contains 18 jars."


def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def brief(row):
    d=dataclasses.asdict(row);d["body_sha256"]=digest(d.pop("body"));return d


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--home",required=True);ap.add_argument("--home-root",required=True);ap.add_argument("--out",required=True)
    ap.add_argument("--phase",choices=["writer","reader-backfill","mutator","reader-final"],required=True)
    ap.add_argument("--manifest",default="");args=ap.parse_args()
    home=Path(args.home).resolve();root=Path(args.home_root).resolve();assert home!=root and home.is_relative_to(root)
    os.environ.update(VOOL_HOME=str(home),VOOL_WORKSPACE_ROOT=str(home/"workspace"),VOOL_REGISTER_INSTALLED_OLLAMA_MODELS="0")
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import requests
    def deny(*a,**kw):raise requests.ConnectionError("source restart proof: network disabled")
    requests.sessions.Session.request=deny
    def no_urlopen(*a,**kw):raise urllib.error.URLError("source restart proof: network disabled")
    urllib.request.urlopen=no_urlopen
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path
    configure_runtime_home(home);configure_default_db_path(home/"data"/"vool_web0_v2.db")
    from storage.migrations import run_migrations
    run_migrations()
    from core import context_retrieval as cr,embedding_service,temporal_selection as ts
    from core.context_namespace import ensure_chat_namespace,set_chat_namespace_state
    from core.memory.entries import resolve_memory_access_policy
    from core.vool_memory import VoolMemory
    mem=VoolMemory(runtime_home=home,agent_id=cr._AGENT_ID)
    result={"phase":args.phase,"pid":os.getpid(),"network_disabled":True,"live_model_quality":"not_measured","provider_spending_usd":0}
    try:
        if args.phase=="writer":
            for chat in (SCOPE,FOREIGN,LIVE):ensure_chat_namespace(chat,grant_current_receipts=False)
            live=cr.store_turn(LIVE,"My workshop stool height is 61 centimetres.","Noted: 61 centimetres.",
                access_policy=resolve_memory_access_policy(chat_id=LIVE),source_context={"runtime_home":str(home),"statement_at":TIED})
            assert live["status"] in {"stored","retained"},live
            rows={}
            for name,body,role in [("old",OLD,"user"),("new",NEW,"user"),("question",QUESTION,"user"),("answer",ANSWER,"assistant"),("unrelated",UNRELATED,"user")]:
                rows[name]=mem.occurrence_store(chat_scope=SCOPE,role=role,body=body,
                    authority="assistant-output" if role=="assistant" else "imported-historical",
                    recorded_at=TIED,statement_at=TIED,source_kind="historical-import",import_batch="authored-native-import")
            rows["foreign"]=mem.occurrence_store(chat_scope=FOREIGN,role="user",body=OLD,
                authority="imported-historical",recorded_at=TIED,statement_at=TIED,source_kind="historical-import",import_batch="authored-native-import")
            result.update(live_ingestion=live,rows={k:brief(v) for k,v in rows.items()},ids={k:v.occurrence_id for k,v in rows.items()},
                api_clock_contract={"store_turn_statement_at_supported":True,"store_turn_recorded_at_supported":False,
                "occurrence_store_import_recorded_at_supported":True,"live_capture_tie":"not_executed_not_supported_by_store_turn"})
        else:
            manifest=json.loads(Path(args.manifest).read_text());ids=manifest["ids"]
            if args.phase=="mutator":
                result["deleted_count"]=mem.occurrence_delete(occurrence_id=ids["old"])
                result["archived_namespace"]=dataclasses.asdict(set_chat_namespace_state(LIVE,"archived"))
            else:
                result["rows"]={name:brief(mem.occurrence_get(oid)) for name,oid in ids.items()}
                result["lexical"]=[brief(o) for o,_ in mem.occurrence_search("amber cabinet",chat_scope=SCOPE,limit=10)]
                anchor=mem.occurrence_get(ids["question"])
                result["neighbors"]=[brief(o) for o in mem.occurrence_neighbors(anchor,before=1,after=1)]
                observed=[];owner=ts.apply_temporal_selection
                def capture(candidates,**kw):
                    candidates=list(candidates);verdicts=owner(candidates,**kw)
                    observed.append({"candidates":[{k:getattr(c,k) for k in ("key","seq","origin","role","source_kind")} for c in candidates],
                        "verdicts":{k:dataclasses.asdict(v) for k,v in verdicts.items()}})
                    return verdicts
                ts.apply_temporal_selection=capture
                def capsule(chat,q):
                    messages=cr.inject_retrieved(chat,q,[{"role":"user","content":q}],access_policy=resolve_memory_access_policy(chat_id=chat),
                        source_context={"chat_id":chat,"runtime_home":str(home)},env={"VOOL_CONTEXT_CAPSULE_V2":"1"})
                    return "\n".join(m["content"] for m in messages if m["role"]=="system")
                result["capsules"]={"code":capsule(SCOPE,"What is the amber cabinet access code?"),
                                    "neighbor":capsule(SCOPE,"What entry code did you give me for the beacon shelf?")}
                result["temporal_calls"]=observed.copy();result["neighbor_evidence_refs"]=cr.get_last_retrieval_telemetry().get("evidence_refs",[]);observed.clear()
                original_search=VoolMemory.occurrence_search
                def reverse_search(self,*a,**kw):return list(reversed(original_search(self,*a,**kw)))
                VoolMemory.occurrence_search=reverse_search
                try:result["permuted_code_capsule"]=capsule(SCOPE,"What is the amber cabinet access code?")
                finally:VoolMemory.occurrence_search=original_search
                result["permuted_temporal_calls"]=observed.copy();ts.apply_temporal_selection=owner
                unrelated=mem.occurrence_get(ids["unrelated"])
                chain_record={"key":unrelated.occurrence_id,"body":unrelated.body,"role":unrelated.role,
                    "statement_at":unrelated.statement_at,"recorded_at":unrelated.recorded_at,"seq":unrelated.source_sequence,"origin":"chain"}
                v=owner([chain_record],intent=ts.AsOfIntent())[unrelated.occurrence_id]
                result["hydrated_unrelated_chain_control"]={"origin_assignment":"explicit_test_caller_control_not_reconstructed_discovery","seq":unrelated.source_sequence,"verdict":dataclasses.asdict(v)}
                vec,backend=embedding_service.embed_stamped(NEW)
                result["embedding_backend"]=backend
                if args.phase=="reader-backfill":
                    missing=mem.occurrence_embeddings_missing(chat_scope=SCOPE,backend=backend,limit=32)
                    upserts=[]
                    for oid,body in missing:
                        body_vector,body_backend=embedding_service.embed_stamped(body)
                        assert body_backend==backend
                        upserts.append({"occurrence_id":oid,"stored":mem.occurrence_embedding_upsert(oid,backend=backend,vector=body_vector,body_sha256=digest(body))})
                    result["backfill"]={"missing_before_ids":[oid for oid,_ in missing],"upserts":upserts,
                        "missing_after_ids":[oid for oid,_ in mem.occurrence_embeddings_missing(chat_scope=SCOPE,backend=backend,limit=32)],
                        "purpose":"real_hash_derivative_storage_lifecycle_not_neural_lazy_retrieval","neural_lazy_backfill":"not_executed_offline"}
                result["semantic"]=[brief(o) for o,_ in mem.occurrence_search_semantic(vec,chat_scope=SCOPE,backend=backend,floor=0,limit=32)]
                if args.phase=="reader-final":
                    try:
                        result["archived_capsule"]=capsule(LIVE,"What is my workshop stool height?")
                        result["archived_policy_denial"]=None
                    except ValueError as exc:
                        assert str(exc)=="chat namespace is archived", str(exc)
                        result["archived_capsule"]=""
                        result["archived_policy_denial"]={"class":type(exc).__name__,"reason":str(exc)}
                    result["foreign_lexical"]=[brief(o) for o,_ in mem.occurrence_search("amber cabinet",chat_scope=FOREIGN,limit=10)]
                    result["missing_after_deletion_ids"]=[oid for oid,_ in mem.occurrence_embeddings_missing(chat_scope=SCOPE,backend=backend,limit=32)]
    finally:mem.close()
    out=Path(args.out);assert not out.exists();out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"phase":args.phase,"pid":os.getpid(),"output":str(out)}));return 0


if __name__=="__main__":raise SystemExit(main())
