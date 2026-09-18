"""The fault plane is WIRED: every required family has a producer at its owning boundary.

A catalog nobody produces from is documentation. Each test here drives a REAL
production seam -- the tool boundary, the provider-call ledger, the network door,
the build confinement gate, the grounding publication gate, the credential vault,
the receipt-chain verifier, the execution-truth witness -- with a forced failure,
and asserts the durable fault record (and, where declared, the security event) that
the boundary itself filed. Nobody in this pack calls ``record_fault`` to fake a
producer: the producing code under test is the same code production runs.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.faults.catalog import (
    FAULT_CANCELLED,
    FAULT_CONFINEMENT_REFUSAL,
    FAULT_CREDENTIAL_FAILURE,
    FAULT_EVIDENCE_CORRUPTION,
    FAULT_INTEGRITY_VERIFICATION_FAILURE,
    FAULT_PERMISSION_DENIED,
    FAULT_PROVIDER_UNAVAILABLE,
    FAULT_TIMEOUT,
    FAULT_TOOL_UNAVAILABLE,
    FAULT_UNSUPPORTED_CLAIM,
)
from core.faults.recorder import faults_for_turn, list_faults
from core.security_events.store import events_for_turn, list_security_events

SESSION = "fault-producer-test"


def _context(turn_id: str) -> dict[str, object]:
    return {"session_id": SESSION, "runtime_session_id": SESSION, "cancel_turn_id": turn_id}


def _codes_for_turn(turn_id: str) -> list[str]:
    return [row.code for row in faults_for_turn(turn_id)]


# --------------------------------------------------------------------------- tool boundary


def test_a_permission_denied_tool_call_produces_the_permission_denied_fault():
    from core.runtime_execution_tools import execute_runtime_tool

    turn_id = "turn-perm-1"
    # The real audit policy denies workspace writes under this policy blob.
    context = {
        **_context(turn_id),
        "audit_execution_policy": {"proof_write_scope": "none", "network_research": False},
    }
    result = execute_runtime_tool(
        "machine.write_file", {"path": "notes.txt", "content": "hi"}, source_context=context
    )
    assert result is not None and result.status == "permission_denied"
    assert FAULT_PERMISSION_DENIED in _codes_for_turn(turn_id)
    rows = [row for row in faults_for_turn(turn_id) if row.code == FAULT_PERMISSION_DENIED]
    assert rows[0].context.get("tool_name") == "machine.write_file"
    # The denial is also a security observation.
    assert any(e.sec_code == "SEC_PERMISSION_DENIED" for e in events_for_turn(turn_id))


def test_an_unsupported_tool_produces_the_tool_unavailable_fault():
    from core.runtime_execution_tools import execute_runtime_tool
    from core.runtime_tool_contracts import runtime_tool_contract_map

    turn_id = "turn-tool-1"
    disabled = [
        intent
        for intent, contract in runtime_tool_contract_map().items()
        if getattr(contract, "handler", "") == "runtime" and not getattr(contract, "supported", True)
    ]
    assert disabled, "the fixture needs one runtime-handled tool that is disabled in this build"
    intent = disabled[0]
    contract = runtime_tool_contract_map()[intent]
    arguments = {name: "" for name in list(getattr(contract, "required_arguments", []) or [])[:2]}
    result = execute_runtime_tool(intent, arguments, source_context=_context(turn_id))
    assert result is not None and result.status == "disabled"
    assert FAULT_TOOL_UNAVAILABLE in _codes_for_turn(turn_id)
    assert not events_for_turn(turn_id), "tool unavailability is a fault, not a security event"


# --------------------------------------------------------------------------- provider calls


def test_a_failed_provider_call_produces_its_fault_at_the_call_ledger():
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        record_provider_call_outcome,
    )

    turn_id = "turn-prov-1"
    # ONE context dict: begin_turn stamps the ledger id into it and the later calls read it.
    context = _context(turn_id)
    begin_turn(context)
    call_id = record_provider_call(context, provider_id="test-provider", model_id="m1")
    assert call_id
    assert record_provider_call_outcome(
        context, call_id, outcome="failed", error_class="ConnectionResetError"
    )
    assert FAULT_PROVIDER_UNAVAILABLE in _codes_for_turn(turn_id)


def test_a_timed_out_provider_call_produces_the_timeout_fault():
    from core.turn_model_call_ledger import (
        begin_turn,
        fail_pending_provider_calls,
        record_provider_call,
    )

    turn_id = "turn-prov-2"
    context = _context(turn_id)
    begin_turn(context)
    call_id = record_provider_call(context, provider_id="test-provider", model_id="m1")
    assert call_id
    closed = fail_pending_provider_calls(context, error_class="TimeoutError")
    assert closed, "the pending call must have been terminalized"
    assert FAULT_TIMEOUT in _codes_for_turn(turn_id)


# --------------------------------------------------------------------------- network door


def _door_failure(exc: BaseException, turn_id: str) -> None:
    """Drive the real network door with a stubbed transport that raises `exc`."""
    import urllib.request

    from core.remote_fetch_policy import open_remote_url, remote_fetch_policy_scope

    with remote_fetch_policy_scope(_context(turn_id)):
        with pytest.MonkeyPatch.context() as patch:
            def _raise(request, timeout=None, context=None):
                raise exc

            patch.setattr(urllib.request, "urlopen", _raise)
            with pytest.raises(type(exc)):
                open_remote_url("http://example.invalid/fetch", timeout=1.0)
    assert _codes_for_turn(turn_id), f"the door filed no fault for {type(exc).__name__}"


def test_a_timeout_at_the_network_door_produces_the_timeout_fault():
    _door_failure(TimeoutError("timed out"), "turn-net-1")
    assert FAULT_TIMEOUT in _codes_for_turn("turn-net-1")


def test_a_cancellation_at_the_network_door_produces_the_cancellation_fault():
    _door_failure(asyncio.CancelledError(), "turn-net-2")
    assert FAULT_CANCELLED in _codes_for_turn("turn-net-2")


# --------------------------------------------------------------------------- confinement


def test_an_out_of_scope_write_produces_the_confinement_refusal_fault():
    """An EXACT scope that carries an escape path is refused at the gate, and the refusal
    is filed -- the module's own law: a scope is plain data, so the gate trusts no input."""
    from core.agent_runtime.builder.app_builder import build_app_from_spec
    from core.agent_runtime.builder.mutation_scope import EXACT, MutationScope

    turn_id = "turn-confine-1"
    report = build_app_from_spec(
        request="create a file called notes.txt",
        target_rel="",
        source_context=dict(_context(turn_id)),
        generate_fn=lambda prompt: "",
        run_tool_fn=None,
        scope=MutationScope(kind=EXACT, paths=("../escape.txt",), root_dir=""),
    )
    assert report.paths_refused, "the gate must refuse the escape path"
    assert FAULT_CONFINEMENT_REFUSAL in _codes_for_turn(turn_id)
    assert any(e.sec_code == "SEC_CONFINEMENT_REFUSAL" for e in events_for_turn(turn_id))


# --------------------------------------------------------------------------- evidence truth


def test_a_failed_witness_check_files_the_evidence_corruption_fault():
    """The truth gate catching a ledger the witness disproves is an evidence fault."""
    from core.execution_truth import verify_execution_truth
    from core.runtime_continuity import _conn as runtime_conn
    from core.runtime_task_events import emit_runtime_event

    turn_id = "turn-ev-1"
    emit_runtime_event(
        _context(turn_id),
        event_type="tool_executed",
        message="Finished live_data.weather_lookup: available",
        details={
            "tool_name": "live_data.weather_lookup",
            "summary": "Finished live_data.weather_lookup",
            "client_turn_id": turn_id,
        },
    )
    # Simulate the ledger losing the row (the corruption the witness exists to catch):
    # delete the fact directly, leaving the independent event stream intact.
    conn = runtime_conn()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (turn_id,))
        conn.commit()
    finally:
        conn.close()

    verdict = verify_execution_truth(turn_id, session_id=SESSION)
    assert not verdict.consistent, "the witness must catch the missing execution"
    assert FAULT_EVIDENCE_CORRUPTION in _codes_for_turn(turn_id)
    assert any(e.sec_code == "SEC_EVIDENCE_CORRUPTION" for e in events_for_turn(turn_id))


# --------------------------------------------------------------------------- claims


def test_an_unsupported_claim_is_filed_at_the_publication_gate():
    from core import grounding_lifecycle as lifecycle_ledger
    from core.grounded_synthesis_binding import binding_record, mint_evidence_set, turn_scope
    from core.grounding_publication import publication_verdict

    turn_id = "turn-claim-1"
    context = {**_context(turn_id), "request_id": "req:fault-claim-1"}
    lifecycle_ledger.register_required(
        context,
        request_text="show me recent news about Rust",
        reason_codes=("current_info_signal:news_request",),
    )
    scope = turn_scope(context, task_id="task-claim-1")
    rows = [
        {
            "summary": "Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released With Debug Helper Messages",
            "result_title": "Rust Coreutils 0.11 Released",
            "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
            "origin_domain": "phoronix.com",
            "source_type": "web_derived",
        },
    ]
    evidence_set = mint_evidence_set(rows, scope=scope, query="recent news about Rust")
    lifecycle_ledger.record_retrieved(
        context,
        outcome="bound",
        receipt={
            "schema": "vool.web_retrieval_receipt.v1",
            "status": "available",
            "lifecycle": "succeeded",
            "source_count": len(rows),
            "search_provider": "brave",
        },
        source_count=len(rows),
        notes=rows,
    )
    lifecycle_ledger.record_bound(context, binding=binding_record(evidence_set), notes=rows, scope=scope)
    model_context = dict(context)
    model_context["evidence_synthesis_binding"] = dict(binding_record(evidence_set))
    lifecycle_ledger.record_synthesis_call(model_context, model_call_id="mc-claim-1", call_role="")
    lifecycle_ledger.record_claim_support(context, payload={}, model_authored=True)

    answer = (
        "Rust Coreutils 0.11 was released with debug helper messages. "
        "Source: [phoronix.com](https://www.phoronix.com/news/rust-coreutils-0-11).\n"
        "Rust 2.0 shipped today with a garbage collector. "
        "Source: [techcrunch.com](https://techcrunch.com/rust-2)."
    )
    verdict = publication_verdict(lifecycle_ledger.lifecycle_for_context(context), answer)
    assert verdict.state in {"partial", "refused"}, "the fixture needs an unsupported claim"
    assert FAULT_UNSUPPORTED_CLAIM in _codes_for_turn(turn_id)


# --------------------------------------------------------------------------- credentials


def test_an_undecryptable_credential_files_the_credential_failure_fault(tmp_path, monkeypatch):
    import core.credential_store as store
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    runtime_paths.configure_runtime_home(tmp_path)
    store._vault_store("llm.cloud.test", "super-secret-value", "test")
    # Corrupt the stored ciphertext in place: the credential exists but cannot be read.
    raw = store._load_raw()
    raw["llm.cloud.test"]["ct_b64"] = "Y29ycnVwdGVkLWNpcGhlcnRleHQ="
    store._save_raw(raw)

    try:
        assert store._vault_get("llm.cloud.test") is None
        faults = [
            row
            for row in list_faults(code=FAULT_CREDENTIAL_FAILURE)
            if row.context.get("credential_name") == "llm.cloud.test"
        ]
        assert faults, "the vault read failure filed no credential fault"
        assert any(e.sec_code == "SEC_CREDENTIAL_FAILURE" for e in list_security_events(limit=50))
    finally:
        runtime_paths.configure_runtime_home(None)


# --------------------------------------------------------------------------- integrity


def test_a_tampered_receipt_chain_files_the_integrity_verification_fault(tmp_path):
    from core.contribution_proof import (
        append_contribution_proof_receipt,
        verify_contribution_proof_chain,
    )
    from storage.db import get_connection
    from storage.migrations import run_migrations

    db_path = tmp_path / "proofs.db"
    run_migrations(db_path)
    # The receipts table carries a foreign key into the contribution ledger: seed the parent.
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO contribution_ledger (
                entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type,
                outcome, created_at, updated_at
            ) VALUES ('entry-int-1', 'task-int-1', 'peer-1', 'parent-1', 'reasoning', 'accepted',
                      '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()
    append_contribution_proof_receipt(
        entry_id="entry-int-1",
        task_id="task-int-1",
        helper_peer_id="peer-1",
        stage="compute",
        evidence={"value": "one"},
        db_path=db_path,
    )
    # Tamper with the stored payload column directly: the chain must catch it and file
    # the fault. This is the corruption the verifier exists to catch.
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM contribution_proof_receipts WHERE entry_id = ?",
            ("entry-int-1",),
        ).fetchone()
        tampered = json.loads(row["payload_json"])
        tampered["evidence"] = {"value": "TAMPERED"}
        conn.execute(
            "UPDATE contribution_proof_receipts SET payload_json = ? WHERE entry_id = ?",
            (json.dumps(tampered, sort_keys=True), "entry-int-1"),
        )
        conn.commit()
    finally:
        conn.close()

    verdict = verify_contribution_proof_chain("entry-int-1", db_path=db_path)
    assert not verdict["ok"] and verdict["tampered_receipt_ids"]
    faults = [
        row
        for row in list_faults(code=FAULT_INTEGRITY_VERIFICATION_FAILURE)
        if row.context.get("entry_id") == "entry-int-1"
    ]
    assert faults, "the failed chain verification filed no integrity fault"
    assert any(receipt_id in faults[0].evidence_refs for receipt_id in verdict["tampered_receipt_ids"])
    assert any(e.sec_code == "SEC_INTEGRITY_VERIFICATION_FAILURE" for e in list_security_events(limit=50))


# --------------------------------------------------------------------------- finalization


def test_the_finalization_seam_derives_retry_classification_from_faults():
    from core.faults.recorder import record_fault
    from core.faults.records import fault_record_for_code
    from core.finalization import retry_classification_for_turn

    turn_id = "turn-fin-1"
    record_fault(
        fault_record_for_code(
            FAULT_PROVIDER_UNAVAILABLE,
            authority="test",
            turn_key=turn_id,
            session_id=SESSION,
            dedupe="fin-1",
        )
    )
    assert retry_classification_for_turn(turn_id, session_id=SESSION) == "retry_later"
    # A turn with no faults classifies as empty -- never a guessed repair claim.
    assert retry_classification_for_turn("turn-with-no-faults", session_id=SESSION) == ""


# --------------------------------------------------------------------------- bug report seam


def test_the_bug_report_export_carries_safe_fault_evidence_and_nothing_else():
    from core.bug_report.scanner import scan_text
    from core.faults.bug_export import fault_evidence_export
    from core.faults.recorder import record_fault
    from core.faults.records import fault_record_for_code

    turn_id = "turn-bug-1"
    record_fault(
        fault_record_for_code(
            FAULT_TIMEOUT,
            authority="provider-boundary",
            turn_key=turn_id,
            session_id=SESSION,
            dedupe="bug-1",
            context={"api_key": "sk-live-abcdef0123456789abcdef", "home": "/Users/example-user/secret"},
        )
    )

    export = fault_evidence_export(turn_key=turn_id, session_id=SESSION)
    assert export["fault_count"] == 1
    entry = export["faults"][0]
    assert entry["code"] == FAULT_TIMEOUT
    assert entry["operator_action"]
    raw = json.dumps(export, sort_keys=True)
    assert "sk-live" not in raw, "the export leaked context material"
    assert "/Users/saulius" not in raw, "the export leaked a personal path"
    assert "context" not in entry, "the bug-report export carries codes, never internal context"
    assert not scan_text(raw), "the export must pass the bug-report outbound scanner"


def test_the_preview_builder_includes_fault_evidence_when_asked():
    """The production export seam: the preview the operator approves before submission."""
    from core.bug_report.preview import build_preview
    from core.bug_report.schema import (
        BugReportDraft,
        InvolvedComponents,
        RedactionSummary,
        ReportEnvironment,
    )
    from core.faults.recorder import record_fault
    from core.faults.records import fault_record_for_code

    turn_id = "turn-bug-2"
    record_fault(
        fault_record_for_code(
            FAULT_TIMEOUT,
            authority="provider-boundary",
            turn_key=turn_id,
            session_id=SESSION,
            dedupe="bug-2",
        )
    )

    draft = BugReportDraft(
        report_id="br_000000000001",
        created_at="2026-09-02T00:00:00+00:00",
        schema_version=1,
        environment=ReportEnvironment(
            version="test",
            source_kind="unknown",
            source_sha="",
            source_dirty=None,
            os="darwin",
            arch="arm64",
            python="3.12.13",
        ),
        title="t",
        category="c",
        expected="e",
        actual="a",
        repro_steps=(),
        error=None,
        components=InvolvedComponents(lanes=(), tools=(), models=()),
        logs=(),
        flags_snapshot={},
        attachments=(),
        fingerprint="fp",
        redaction_summary=RedactionSummary(),
        destination_repo="repo",
    )
    preview = build_preview(
        draft, include_fault_evidence=True, fault_turn_key=turn_id, fault_session_id=SESSION
    )
    body = json.dumps(preview.issue)
    assert FAULT_TIMEOUT in body
    assert "fault" in body.lower()
    # A preview built without the flag stays byte-shaped as before.
    plain = build_preview(draft)
    assert "fault" not in json.dumps(plain.issue).lower()
