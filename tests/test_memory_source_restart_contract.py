"""Native storage restarts preserve source chronology and lifecycle. Contributor: sls_0x."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests._restart_child_env import scrub_child_env

REPO=Path(__file__).resolve().parents[1]
DRIVER=REPO/"tests/_memory_source_restart_driver.py"


@pytest.fixture(scope="module")
def restarted_sources(tmp_path_factory):
    suffix=uuid.uuid4().hex[:10]
    root=tmp_path_factory.mktemp("source-restart").resolve()
    profiles=root/"profiles";profiles.mkdir()
    profile=profiles/("source-restart-"+suffix)
    profile.mkdir(parents=True,exist_ok=False)
    work=profile/"source-proof";work.mkdir()
    artifact=Path(os.environ.get("VOOL_SOURCE_RESTART_ARTIFACT_DIR",str(root/"artifacts")))/suffix
    artifact.mkdir(parents=True,exist_ok=False)
    identities={}
    for relative in ("core/context_retrieval.py","core/temporal_selection.py","core/vool_memory.py","core/bootstrap_context.py","tests/_memory_source_restart_driver.py","tests/test_memory_source_restart_contract.py"):
        body=(REPO/relative).read_bytes();identities[relative]="sha256:"+hashlib.sha256(body).hexdigest()
        if relative.startswith("tests/"):(artifact/Path(relative).name).write_bytes(body)
    (artifact/"SOURCE-IDENTITY.json").write_text(json.dumps(identities,indent=2)+"\n")
    policy=work/"worker.sb"
    q=lambda p:json.dumps(str(p))
    policy.write_text("\n".join(["(version 1)","(allow default)","(deny network*)","(deny file-write*)",
        '(deny file-read* (subpath "/Users"))',
        '(allow file-read* (subpath '+q(Path(sys.base_prefix).resolve())+'))',
        '(allow file-read* (subpath '+q(Path(sys.prefix).resolve())+'))',
        '(allow file-read* (subpath '+q(REPO)+'))',
        '(allow file-read* (subpath '+q(profile)+'))',
        '(allow file-write* (subpath '+q(profile)+'))',
        '(allow file-write* (literal "/dev/null"))',"(allow file-read-metadata)"])+"\n")
    env=scrub_child_env(os.environ)
    env.update(VOOL_HOME=str(profile),TMPDIR=str(work),TMP=str(work),TEMP=str(work),PYTHONDONTWRITEBYTECODE="1")
    native_python=os.environ.get("VOOL_SOURCE_RESTART_PYTHON",sys.executable)
    assert Path(native_python).is_file(), "Configured native test interpreter is absent"
    phases=[];data={};writer=work/"writer.json"
    for phase in ("writer","reader-backfill","mutator","reader-final"):
        out=work/(phase+".json")
        cmd=["/usr/bin/sandbox-exec","-f",str(policy),native_python,"-B",str(DRIVER),"--home",str(profile),"--home-root",str(profiles),"--out",str(out),"--phase",phase]
        if phase!="writer":cmd.extend(["--manifest",str(writer)])
        start=time.time();proc=subprocess.run(cmd,cwd=REPO,env=env,text=True,capture_output=True,timeout=90)
        (artifact/(phase+".stdout.log")).write_text(proc.stdout);(artifact/(phase+".stderr.log")).write_text(proc.stderr)
        assert proc.returncode==0, {"phase":phase,"code":proc.returncode,"stderr":proc.stderr[-4000:],"artifact":str(artifact)}
        result=json.loads(out.read_text());(artifact/(phase+".json")).write_text(json.dumps(result,indent=2)+"\n")
        phases.append({"phase":phase,"pid":result["pid"],"started_epoch":start,"finished_epoch":time.time(),"exit_code":proc.returncode})
        data[phase]=result
    receipt={"phases":phases,"profile":str(profile),"provider_spending_usd":0,"live_model_quality":"not_measured"}
    (artifact/"PROCESS-RECEIPT.json").write_text(json.dumps(receipt,indent=2)+"\n")
    assert len({p["pid"] for p in phases})==4
    assert all(b["started_epoch"]>=a["finished_epoch"] for a,b in zip(phases,phases[1:]))
    data["artifact"]=str(artifact);return data


def test_native_restart_preserves_tied_import_order_and_live_statement_metadata(restarted_sources):
    d=restarted_sources;writer=d["writer"];reader=d["reader-backfill"]
    assert writer["live_ingestion"]["status"] in {"stored","retained"}
    assert writer["api_clock_contract"]["live_capture_tie"]=="not_executed_not_supported_by_store_turn"
    old,new=reader["rows"]["old"],reader["rows"]["new"]
    assert old["recorded_at"]==new["recorded_at"]==old["statement_at"]==new["statement_at"]
    assert old["source_sequence"]<new["source_sequence"]
    for kind in ("lexical","semantic"):
        read={r["occurrence_id"]:r for r in reader[kind]}
        for name in ("old","new"):
            assert read[writer["ids"][name]]["source_sequence"]==writer["rows"][name]["source_sequence"]
            assert read[writer["ids"][name]]["body_integrity"]=="verified"
    assert "8643" in reader["capsules"]["code"] and "4127" not in reader["capsules"]["code"]
    assert "8643" in reader["permuted_code_capsule"] and "4127" not in reader["permuted_code_capsule"]


def test_native_restart_passes_source_seq_origin_and_neighbor_direction_to_real_caller(restarted_sources):
    d=restarted_sources;writer=d["writer"];reader=d["reader-backfill"]
    for name in ("old","new"):
        candidates=[c for call in reader["temporal_calls"]+reader["permuted_temporal_calls"] for c in call["candidates"] if c["key"]==writer["ids"][name]]
        assert candidates and all(c["seq"]==writer["rows"][name]["source_sequence"] for c in candidates)
        assert all(c["origin"]=="query-leg" for c in candidates)
    assert [r["occurrence_id"] for r in reader["neighbors"]]==[writer["ids"]["new"],writer["ids"]["answer"]]
    assert reader["rows"]["answer"]["source_sequence"]>reader["rows"]["question"]["source_sequence"]
    assert "6532" in reader["capsules"]["neighbor"]
    assert any(r.get("occurrence_id")==writer["ids"]["answer"] and r.get("delivered") for r in reader["neighbor_evidence_refs"])
    chain=reader["hydrated_unrelated_chain_control"]
    assert chain["verdict"]["eligible"] is False and chain["verdict"]["reason"]=="chain-unlinked"
    assert chain["seq"]==writer["rows"]["unrelated"]["source_sequence"]


def test_native_backfill_survives_restart_and_delete_preserves_same_chat_and_foreign_siblings(restarted_sources):
    d=restarted_sources;writer=d["writer"];reader=d["reader-backfill"];final=d["reader-final"]
    backfill=reader["backfill"]
    assert set(backfill["missing_before_ids"])=={writer["ids"][k] for k in ("old","new","question","answer","unrelated")}
    assert all(row["stored"] for row in backfill["upserts"]) and backfill["missing_after_ids"]==[]
    assert backfill["neural_lazy_backfill"]=="not_executed_offline"
    assert d["mutator"]["deleted_count"]==1
    assert final["rows"]["old"]["status"]=="deleted" and final["rows"]["old"]["body_integrity"]=="cleared"
    for kind in ("lexical","semantic"):
        ids={r["occurrence_id"] for r in final[kind]}
        assert writer["ids"]["old"] not in ids and writer["ids"]["new"] in ids
    assert final["missing_after_deletion_ids"]==[]
    assert final["rows"]["answer"]==writer["rows"]["answer"]
    assert final["foreign_lexical"][0]["occurrence_id"]==writer["ids"]["foreign"]
    assert "8643" in final["capsules"]["code"] and "6532" in final["capsules"]["neighbor"]


def test_native_archive_state_is_enforced_after_mutator_exit(restarted_sources):
    d=restarted_sources
    assert d["mutator"]["archived_namespace"]["lifecycle_state"]=="archived"
    assert d["reader-final"]["archived_policy_denial"]=={"class":"ValueError","reason":"chat namespace is archived"}
    assert "61 centimetres" not in d["reader-final"]["archived_capsule"]
