"""Offline native capsule producer observation; no product source mutation.

Contributor: sls_0x. Run only with the existing isolated native sandbox and
parent-only transport controls. This observes actual producer output and exact
PreparedRequest bytes; it does not repair routing or reconstruct earlier evidence.
"""
from __future__ import annotations
import argparse,base64,hashlib,importlib,json,os,sys,time,traceback
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
    original_distill=memory._distill_retrieved_hits
    receipts=[];assembly_stack=[];inject_stack=[]
    owner_file=Path(memory.__file__).resolve()
    owner_lines=owner_file.read_text().splitlines()
    owner_sha256=digest(owner_file.read_bytes())
    facet_capture_line=next(i+1 for i,line in enumerate(owner_lines) if line.startswith("    def facet_noise(key: str, body: str)"))

    def hit_view(hits):
        return [{"occurrence_id":str(getattr(obj,"occurrence_id","") or ""),
                 "node_id":str(getattr(obj,"node_id","") or ""),
                 "role":str(getattr(obj,"role","") or ""),
                 "body":str(getattr(obj,"body",getattr(obj,"content","")) or ""),
                 "score":score} for obj,score in hits or []]

    def distill(query,selected,**kwargs):
        row=inject_stack[-1] if inject_stack else None
        call={"decision_owner":"core.context_retrieval._distill_retrieved_hits",
              "query_sha256":digest(str(query or "")),"selected_input":snapshot(selected),
              "record_roles":snapshot(kwargs.get("record_roles")),
              "target_tokens":kwargs.get("target_tokens")}
        if row is not None:row.setdefault("actual_distiller_calls",[]).append(call)
        result=original_distill(query,selected,**kwargs)
        call["actual_output_text"]=result[0]
        call["actual_output_telemetry"]=snapshot(result[1])
        return result

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
        debug_before=len(memory._CAPSULE_DEBUG_DROPS)
        old_debug=os.environ.get("VOOL_CAPSULE_DEBUG_DROPS")
        old_trace=sys.gettrace()
        os.environ["VOOL_CAPSULE_DEBUG_DROPS"]="1"
        inject_stack.append(row)
        def capture(frame,event,arg):
            if frame.f_code.co_filename==str(owner_file) and frame.f_code.co_name=="_capsule_v2_inject_retrieved":
                if event=="line" and frame.f_lineno==facet_capture_line:
                    local=frame.f_locals
                    row.setdefault("actual_facet_gate",[]).append({
                        "decision_owner":"core.context_retrieval._capsule_v2_inject_retrieved:facet_precision",
                        "file":str(owner_file),"line":frame.f_lineno,"source_sha256":owner_sha256,
                        "active":local.get("facet_noise_gate"),
                        "discriminators":sorted(local.get("_fp_discriminators") or []),
                        "present":sorted(local.get("_fp_present") or []),
                        "absent":sorted(local.get("_fp_absent_terms") or []),
                        "question_scope":snapshot(getattr(local.get("_fp_scope"),"__dict__",None)),
                        "actual_post_temporal_node_hits":hit_view(local.get("hits")),
                        "actual_post_temporal_evidence_hits":hit_view(local.get("evidence_hits")),
                        "temporal_winner_keys":sorted(local.get("temporal_winner_keys") or []),
                        "temporal_coexist_keys":sorted(local.get("temporal_coexist_keys") or [])})
                return capture
            return None
        if old_trace is None:sys.settrace(capture)
        else:row["facet_local_observation"]="not_captured_existing_trace_preserved"
        try:
            result=original_inject(session_id,query,transcript,**kwargs)
            row["execution"]="returned";row["returned_transcript"]=snapshot(result)
            row["telemetry_at_actual_return_before_bootstrap_restoration"]=snapshot(memory.get_last_retrieval_telemetry())
            return result
        except BaseException as exc:
            row["execution"]="raised";row["exception_type"]=type(exc).__name__
            row["exception_frames"]=[{"file":f.filename,"function":f.name,"line":f.lineno} for f in traceback.extract_tb(exc.__traceback__)]
            raise
        finally:
            if old_trace is None:sys.settrace(None)
            if old_debug is None:os.environ.pop("VOOL_CAPSULE_DEBUG_DROPS",None)
            else:os.environ["VOOL_CAPSULE_DEBUG_DROPS"]=old_debug
            row["actual_debug_drops"]=snapshot(memory._CAPSULE_DEBUG_DROPS[debug_before:])
            row["debug_drop_owner"]="core.context_retrieval._capsule_v2_inject_retrieved"
            row["source_sha256"]=owner_sha256
            inject_stack.pop();row["finished_monotonic"]=time.monotonic()

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

    memory.inject_retrieved=inject;memory._distill_retrieved_hits=distill;bootstrap._with_semantic_memory_capsule=with_capsule
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
        diagnostic={"schema":"vool.station_capsule_actual_observation.v2","contributor":"sls_0x","pid":os.getpid(),"code":code,
                    "observations":receipts,"selected_to_wire":wire_joins,
                    "scope":"actual native producer return/outcome and wire capture; no paid model accuracy or earlier-run reconstruction",
                    "product_source_modified":False,"prior_attempt_scores_modified":False,
                    "uncaptured_internal_rejections":"unknown outside captured existing debug drops and actual facet/distiller observations"}
        data["capsule_producer_diagnostic"]=diagnostic
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
        return code
    except BaseException as exc:error=type(exc).__name__;raise
    finally:
        memory.inject_retrieved=original_inject;memory._distill_retrieved_hits=original_distill;bootstrap._with_semantic_memory_capsule=original_with
        sidecar=output.with_suffix(".capsule-diagnostic.json")
        assert not sidecar.exists(),"diagnostic receipt must preserve earlier attempts"
        sidecar.write_text(json.dumps({"schema":"vool.station_capsule_attempt.v1","code":code,"error_type":error,"observations":receipts},ensure_ascii=False,indent=2)+"\n")

if __name__=="__main__":raise SystemExit(main())
