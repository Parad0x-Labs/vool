"""F0-C / K-04 durable A2 admission referent tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.finalization import FinalizationRejected, finalize_answer
from core.semantic.semantic_admissions import (
    admission_exists,
    get_admission,
    record_admission,
    set_request_context,
)
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    current_admission,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f0c.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    # Turn-scope hygiene: clear ContextVars that earlier tests may leak.
    from core.conductor import obligation_ledger as _ol
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        set_request_context,
    )

    _ol.clear_active_set()
    clear_execution_context()
    set_request_context("")
    sdb.configure_default_db_path(None)


def test_admission_records_durable_insert_once_row(fresh_store):
    reset_admission()
    token = set_request_context("req:http:evt-9")
    try:
        result = admit_semantic_result({"response": "hello", "route_reason": "model_lane"})
        sr = result["_semantic_admission"]["semantic_result_id"]
        row = get_admission(sr)
        assert row is not None
        assert row["request_id"] == "req:http:evt-9"
        assert row["accepted"] == 1
        # turn_id fed from req: — no "turn-unknown" orphan.
        assert "turn-unknown" not in sr
        assert sr.startswith("sr:req-turn:req:http:evt-9:")
        # Insert-once: re-recording the same id never overwrites (A-2 honest
        # classification). F-04/M11 repair: identity is the WHOLE stored row —
        # an identical re-record (same class) is ACCEPTED_IDENTICAL; a
        # different source_class under the same sr id is honestly
        # REJECTED_DIFFERENT, never laundered into IDENTICAL.
        assert record_admission(sr, source_class=row["source_class"]) == "ACCEPTED_IDENTICAL"
        assert record_admission(sr, source_class=row["source_class"] + "-x") == "REJECTED_DIFFERENT"
    finally:
        reset_admission()
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        _CURRENT_REQUEST_ID.set("")


def test_finalize_citing_unadmitted_sr_is_rejected_zero_rows(fresh_store):
    # Forge an admission context carrying an sr id that has NO durable row.
    reset_admission()

    class FakeRecord:
        semantic_result_id = "sr:forged:deadbeef-1"

    import core.finalization as fin

    orig = fin.current_semantic_result_id
    fin.current_semantic_result_id = lambda: FakeRecord.semantic_result_id
    try:
        with pytest.raises(FinalizationRejected, match="NO_ADMISSION_ROW"):
            finalize_answer(turn_id="t1", canonical_content="forged bytes")
    finally:
        fin.current_semantic_result_id = orig
    # Zero rows written for the forged citation.
    assert not admission_exists(FakeRecord.semantic_result_id)


def test_seam_writes_referent_before_a7_can_cite(fresh_store):
    reset_admission()
    admitted = admit_semantic_result(
        {"response": "real bytes", "route_reason": "model_lane"}
    )
    sr = admitted["_semantic_admission"]["semantic_result_id"]
    record = current_admission()
    assert record is not None and record.semantic_result_id == sr
    assert admission_exists(sr)
    commit = finalize_answer(turn_id="t2", canonical_content="real bytes")
    assert commit["semantic_result_id"] == sr
    assert commit["binding_outcome"] == "ACCEPTED_FIRST"
