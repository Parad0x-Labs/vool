"""Structural receipt metadata survives redaction only after integrity checks."""
from copy import deepcopy
import json
import pytest
from core.agent_runtime import orchestrator
from core.runtime_continuity import (
    configure_runtime_continuity_db_path, load_tool_receipt,
    reset_runtime_continuity_state, store_tool_receipt,
)
from storage.migrations import run_migrations


@pytest.fixture
def receipt_store(tmp_path):
    db = tmp_path / "typed-receipts.db"
    run_migrations(db_path=db)
    configure_runtime_continuity_db_path(str(db))
    reset_runtime_continuity_state()
    yield
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)


def generated(field="record_hash", event="tool_executed"):
    for nonce in range(4096):
        record = orchestrator.build_tool_action_record(
            {"chat_id":"typed-storage-chat", "_trusted_project_id":"typed-storage-project"},
            event_type=event, message="Synthetic receipt storage proof.",
            details={"tool_name":"workspace.write_file", "status":event,
                     "mode":event, "ok":event == "tool_executed",
                     "summary":f"Synthetic receipt storage proof {nonce}.",
                     "checkpoint_id":"typed-storage-checkpoint",
                     "tool_call_id":f"typed-storage-call-{nonce}",
                     "arguments":{"path":"synthetic.txt", "content":f"SYNTHETIC BYTES {nonce}"}})
        safe = orchestrator.redact_tool_arguments({"action_record":record})["action_record"]
        if safe[field] != record[field]:
            return record
    pytest.fail(f"No genuine generated {field} redaction collision within bounded probe")


def write(record, **overrides):
    values = dict(receipt_key=record["receipt_id"], session_id="typed-storage-runtime",
                  checkpoint_id="typed-storage-checkpoint", tool_name=record["tool_name"],
                  idempotency_key=record["action_id"], arguments=record["parameters"],
                  execution={"action_record":record})
    values.update(overrides)
    result = store_tool_receipt(**values)
    assert load_tool_receipt(values["receipt_key"])["execution"] == result["execution"]
    return result


def hashes_valid(record):
    return (record["parameters_hash"] == orchestrator._stable_json_hash(record["parameters"])
            and record["result_hash"] == orchestrator._stable_json_hash(record["result"])
            and record["record_hash"] == orchestrator._stable_json_hash(
                {k:v for k,v in record.items() if k not in {"occurred_at","record_hash"}}))


@pytest.mark.parametrize("field", ["parameters_hash","result_hash","record_hash","action_id","receipt_id"])
def test_verified_producer_metadata_survives_storage_redaction(receipt_store, field):
    record = generated(field)
    assert hashes_valid(record)
    stored = write(record)["execution"]["action_record"]
    assert stored == record
    assert hashes_valid(stored)


@pytest.mark.parametrize("field", ["parameters_hash","result_hash","record_hash"])
def test_invalid_input_hash_is_never_resealed(receipt_store, field):
    record = generated(); record[field] = "b" * 64
    assert not hashes_valid(record)
    stored = write(record)["execution"]["action_record"]
    assert not hashes_valid(stored)
    assert stored[field] != record[field]


@pytest.mark.parametrize("overrides", [
    {"receipt_key":"foreign-receipt"}, {"idempotency_key":"foreign-action"},
    {"tool_name":"workspace.read_file"}, {"arguments":{"path":"foreign.txt"}},
    {"checkpoint_id":"foreign-checkpoint"},
])
def test_row_identity_mismatch_cannot_restore_structural_authority(receipt_store, overrides):
    record = generated()
    stored = write(record, **overrides)["execution"]["action_record"]
    assert stored["record_hash"] != record["record_hash"]
    assert not hashes_valid(stored)


def test_hash_shaped_secret_text_remains_redacted_without_resealing_record(receipt_store):
    record = generated()
    record["result"]["diagnostic"] = {"opaque_text":"b" * 64}
    record["result_hash"] = orchestrator._stable_json_hash(record["result"])
    record["record_hash"] = orchestrator._stable_json_hash(
        {k:v for k,v in record.items() if k not in {"occurred_at","record_hash"}})
    assert hashes_valid(record)
    stored = write(record)["execution"]["action_record"]
    assert "b" * 64 not in json.dumps(stored)
    assert not hashes_valid(stored)


def test_extra_execution_hash_shaped_secret_is_not_a_structural_exemption(receipt_store):
    record = generated()
    result = write(record, execution={"action_record":record, "observation":"b" * 64})
    assert "b" * 64 not in json.dumps(result)
    assert result["execution"]["observation"] == "[redacted-key]"


def test_self_consistent_custom_digest_id_is_not_restored_as_generated_identity(receipt_store):
    record = generated()
    record["action_id"] = "tool-action-" + "b" * 64
    record["receipt_id"] = "tool-receipt-" + orchestrator._stable_json_hash({"action_id":record["action_id"]})
    record["record_hash"] = orchestrator._stable_json_hash(
        {k:v for k,v in record.items() if k not in {"occurred_at","record_hash"}})
    stored = write(record)["execution"]["action_record"]
    assert "b" * 64 not in json.dumps(stored)
    assert not hashes_valid(stored)


@pytest.mark.parametrize("event,outcome", [("tool_failed","failed"),("tool_preview","pending_approval")])
def test_failed_and_preview_records_preserve_nonexecution_meaning(receipt_store, event, outcome):
    record = generated(event=event)
    stored = write(record)["execution"]["action_record"]
    assert stored == record
    assert stored["action_type"] == event
    assert stored["result"]["outcome"] == outcome
    assert stored["result"]["ok"] is False
    assert "executed" not in stored["result"]
