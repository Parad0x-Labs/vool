"""Offline native capsule producer observation; no product source mutation.

Contributor: sls_0x. Run only with the existing isolated native sandbox and
parent-only transport controls. This observes actual producer output and exact
PreparedRequest bytes; it does not repair routing or reconstruct earlier evidence.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value,str) else value).hexdigest()


def snapshot(value):
    return json.loads(json.dumps(value,ensure_ascii=False,default=str))


def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home",required=True)
    parser.add_argument("--out",required=True)
    parser.add_argument("--repo-root",required=True)
    parser.add_argument("--reference-date",default="")
    args,_=parser.parse_known_args()
    home=Path(args.home).resolve();repo=Path(args.repo_root).resolve();output=Path(args.out)
    assert not output.exists(),"A diagnostic attempt must preserve any earlier output"
    os.environ["VOOL_HOME"]=str(home);os.environ["VOOL_HOME"]=str(home)
    sys.path[:0]=[str(repo),str(repo/"tests")]
    import _memory_evidence_contract_driver as native
    native.install_fixture_reference_clock(args.reference_date)
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path
    configure_runtime_home(home);configure_default_db_path(home/"data/vool_web0_v2.db")
    import core.bootstrap_context as bootstrap
    import core.context_retrieval as memory
    original_inject=memory.inject_retrieved
    original_with=bootstrap._with_semantic_memory_capsule
    receipts=[];assembly_stack=[]

    def receipt(kind,session_id,query):
        row={"observation_id":f"capsule-observation-{os.getpid()}-{len(receipts)+1}",
             "kind":kind,"started_monotonic":time.monotonic(),"session_id":str(session_id or ""),
             "query_sha256":digest(str(query or "")),"query":str(query or ""),
             "runtime_assembly_id":"not_captured","capsule_flag":os.environ.get("VOOL_CONTEXT_CAPSULE_V2")}
        receipts.append(row);return row

    def inject(session_id,query,transcript,**kwargs):
        row=receipt("context_retrieval.inject_retrieved",session_id,query)
        row["parent_observation_id"]=assembly_stack[-1] if assembly_stack else "not_captured"
        row["input_transcript"]=snapshot(transcript)
        row["source_context"]=snapshot(kwargs.get("source_context"))
        row["budget"]=snapshot(kwargs.get("budget"))
        try:
            result=original_inject(session_id,query,transcript,**kwargs)
            row["execution"]="returned";row["returned_transcript"]=snapshot(result)
            row["telemetry_at_actual_return_before_bootstrap_restoration"]=snapshot(memory.get_last_retrieval_telemetry())
            return result
        except BaseException as exc:
            row["execution"]="raised";row["exception_type"]=type(exc).__name__
            row["exception_frames"]=[{"file":f.filename,"function":f.name,"line":f.lineno} for f in traceback.extract_tb(exc.__traceback__)]
            raise
        finally:row["finished_monotonic"]=time.monotonic()

    def with_capsule(transcript,*,session_id,current_user_text,source_context=None):
        row=receipt("bootstrap_context._with_semantic_memory_capsule",session_id,current_user_text)
        row["input_transcript"]=snapshot(transcript)
        assembly_stack.append(row["observation_id"])
        try:
            result=original_with(transcript,session_id=session_id,current_user_text=current_user_text,source_context=source_context)
            row["execution"]="returned";row["returned_transcript"]=snapshot(result)
            row["input_sha256"]=digest(json.dumps(transcript,ensure_ascii=False,sort_keys=True))
            row["output_sha256"]=digest(json.dumps(result,ensure_ascii=False,sort_keys=True))
            row["returned_capsules"]=[m["content"] for m in result if m.get("role")=="system" and "<retrieved_context>" in str(m.get("content"))]
            row["inner_inject_observation_ids"]=[r["observation_id"] for r in receipts if r.get("parent_observation_id")==row["observation_id"]]
            if not row["inner_inject_observation_ids"]:
                row["inject_execution"]="not_captured";row["reason"]="no_wrapped_inject_observation; branch execution not established"
            return result
        except BaseException as exc:
            row["execution"]="raised";row["exception_type"]=type(exc).__name__;raise
        finally:assembly_stack.pop();row["finished_monotonic"]=time.monotonic()

    memory.inject_retrieved=inject;bootstrap._with_semantic_memory_capsule=with_capsule
    code=None;error=None
    try:
        finalized=importlib.import_module("_memory_finalized_workflow_driver")
        code=finalized.main()
        data=json.loads(output.read_text())
        question_ids={digest(t["question"]):t["id"] for t in data.get("turns",[])}
        for row in receipts:row["question_id"]=question_ids.get(row["query_sha256"],"not_captured")
        wire_joins=[]
        for turn in data.get("turns",[]):
            observations=[r for r in receipts if r.get("question_id")==turn["id"] and r["kind"]=="context_retrieval.inject_retrieved"]
            for call in turn.get("request_calls",[]):
                body=call.get("request_body_b64")
                if not body:continue
                raw=base64.b64decode(body,validate=True)
                assert digest(raw)==call["request_body_sha256"]
                payload=json.loads(raw);assert payload==call["payload"]
                contents=[str(m.get("content") or "") for m in payload.get("messages",[])]
                selected=[]
                for row in observations:
                    telem=row.get("telemetry_at_actual_return_before_bootstrap_restoration") or {}
                    for line in telem.get("selected_facts") or []:
                        spans=[{"message_index":i,"start":content.find(line),"end":content.find(line)+len(line)} for i,content in enumerate(contents) if line in content]
                        selected.append({"observation_id":row["observation_id"],"selected_line_sha256":digest(line),"selected_line":line,"final_message_spans":spans})
                wire_joins.append({"question_id":turn["id"],"request_sha256":digest(raw),"capture_boundary":"actual requests.PreparedRequest.body from original native driver","selected_to_final_messages":selected})
        diagnostic={"schema":"vool.station_capsule_actual_observation.v1","contributor":"sls_0x","pid":os.getpid(),"code":code,
                    "observations":receipts,"selected_to_wire":wire_joins,
                    "scope":"actual native producer return/outcome and wire capture; no paid model accuracy or earlier-run reconstruction",
                    "product_source_modified":False,"prior_attempt_scores_modified":False,
                    "uncaptured_internal_rejections":"unknown; telemetry records only what the owning injector actually returned"}
        data["capsule_producer_diagnostic"]=diagnostic
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
        return code
    except BaseException as exc:error=type(exc).__name__;raise
    finally:
        memory.inject_retrieved=original_inject;bootstrap._with_semantic_memory_capsule=original_with
        sidecar=output.with_suffix(".capsule-diagnostic.json")
        assert not sidecar.exists(),"diagnostic receipt must preserve earlier attempts"
        sidecar.write_text(json.dumps({"schema":"vool.station_capsule_attempt.v1","code":code,"error_type":error,"observations":receipts},ensure_ascii=False,indent=2)+"\n")

if __name__=="__main__":raise SystemExit(main())
