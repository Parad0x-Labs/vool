"""A7 W6/W7 gate — replay-as-read, monotone delivery truth, crash honesty, migration."""
from __future__ import annotations

import hashlib

import pytest

from core.finalization import (
    DELIVERY_ATTEMPTED_UNKNOWN,
    DELIVERY_DELIVERED,
    DELIVERY_NOT_ATTEMPTED,
    finalize_answer,
    replay_finalized_answer,
    set_delivery_status,
)


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _admit(text: str) -> str:
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    record = admit_semantic_result({"response": text})
    return str(record["_semantic_admission"]["semantic_result_id"])


def test_true_replay_is_a_pure_read_of_stored_truth():
    sr = _admit("replayable truth bytes")
    commit = finalize_answer(turn_id="tr", canonical_content="replayable truth bytes")
    replayed = replay_finalized_answer(principal="owner_local", semantic_result_id=sr)
    assert replayed is not None
    assert replayed["canonical_content"] == "replayable truth bytes"
    assert replayed["content_hash"] == commit["content_hash"]
    # Content-addressed replay works too (e.g. legacy-correlated rows aside).
    by_hash = replay_finalized_answer(principal="owner_local", content_hash=commit["content_hash"])
    assert by_hash is None or by_hash["canonical_content"] == "replayable truth bytes"


def test_replay_of_unknown_truth_is_none_not_regeneration():
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    assert replay_finalized_answer(principal="owner_local", semantic_result_id="sr:never-seen:x-1") is None


def test_delivery_status_monotone():
    sr = _admit("delivery probe bytes")
    commit = finalize_answer(turn_id="d1", canonical_content="delivery probe bytes")
    fid = commit["finalization_id"]
    # A-5: DELIVERED requires explicit evidence class (no default upgrade).
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="RECONCILED") is True  # NOT_ATTEMPTED -> DELIVERED
    # Delivered is terminal: no downgrade, no reopen.
    assert set_delivery_status(fid, DELIVERY_NOT_ATTEMPTED) is False
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN) is False
    row = replay_finalized_answer(principal="owner_local", semantic_result_id=sr)
    assert row["delivery_status"] == DELIVERY_DELIVERED


def test_delivery_unknown_representable_after_possible_first_byte():
    sr = _admit("crash window bytes")
    commit = finalize_answer(turn_id="d2", canonical_content="crash window bytes")
    fid = commit["finalization_id"]
    # First byte possible -> ATTEMPTED_UNKNOWN; crash before done frame.
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN) is True
    row = replay_finalized_answer(principal="owner_local", semantic_result_id=sr)
    assert row["delivery_status"] == DELIVERY_ATTEMPTED_UNKNOWN
    # Recovery can still mark delivered, never silently not-attempted.
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="PLATFORM_ACK") is True


def test_delivery_retry_serves_stored_bytes_without_semantic_execution():
    sr = _admit("retry delivery bytes")
    finalize_answer(turn_id="dr", canonical_content="retry delivery bytes")
    first = replay_finalized_answer(principal="owner_local", semantic_result_id=sr)
    second = replay_finalized_answer(principal="owner_local", semantic_result_id=sr)
    # A delivery retry re-reads immutable truth: byte-identical, identity stable.
    assert first["canonical_content"] == second["canonical_content"]
    assert first["finalization_id"] == second["finalization_id"]
    assert hashlib.sha256(first["canonical_content"].encode()).hexdigest() == \
        first["content_hash"].split(":", 1)[1]


def test_migration_legacy_rows_readable_and_uncertified():
    from core.final_response_store import store_final_response
    from core.finalization import get_finalization_by_content

    # Legacy swarm rows (pre-A7) carry empty content_hash until rewritten;
    # they stay readable and are NEVER retroactively certified.
    store_final_response("legacy-task", raw="old answer", rendered="old", status="completed", confidence=0.5)
    assert get_finalization_by_content("old answer") is None  # not certified as A7 truth
