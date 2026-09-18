"""F1-D / K-09 A7 payload/finality separation tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.finalization import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_ERASED,
    REPLAY_UNAVAILABLE_BY_POLICY,
    finalize_answer,
    get_finalization_by_semantic_id,
    replay_finalized_answer,
    set_availability,
)
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f1d.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _finalize(text="short answer"):
    reset_admission()
    admitted = admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t", canonical_content=text)


def test_finalization_id_is_not_derived_from_content_hash(fresh_store):
    c1 = _finalize("yes")
    reset_admission()
    # Different sr id (fresh admission), SAME one-word content.
    c2 = _finalize("yes")
    assert c1["finalization_id"] != c2["finalization_id"]
    assert c1["content_hash"] == c2["content_hash"]
    # Identity inputs are opaque: no content hash inside the id derivation.
    assert "fc:" == c1["finalization_id"][:3]


def test_availability_monotone_with_tombstones_and_erasure_clears_bytes(fresh_store):
    commit = _finalize("secret-ish bytes")
    fid = commit["finalization_id"]
    assert set_availability(fid, "ERASED", reason="user erasure", governance_actor="owner_local")
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row["availability"] == AVAILABILITY_ERASED
    assert row["canonical_content"] == ""
    conn = sdb.get_connection()
    try:
        events = conn.execute(
            "SELECT * FROM a7_governance_events WHERE finalization_id = ? "
            "AND event_kind = 'availability_transition'",
            (fid,),
        ).fetchall()
        assert len(events) == 1 and events[0]["new_state"] == "ERASED"
    finally:
        conn.close()


def test_erased_replay_returns_unavailable_by_policy_never_regenerates(fresh_store):
    commit = _finalize("to be erased")
    fid = commit["finalization_id"]
    set_availability(fid, "ERASED", reason="test")
    replay = replay_finalized_answer(principal="owner_local", semantic_result_id=commit["semantic_result_id"])
    assert replay is not None
    assert replay["replay_outcome"] == REPLAY_UNAVAILABLE_BY_POLICY
    assert replay["canonical_content"] == ""
    # Downgrade transitions are refused.
    assert not set_availability(fid, AVAILABILITY_AVAILABLE)


def test_digest_retained_keyed_only_after_erase_lookup_honest(fresh_store):
    commit = _finalize("digest law")
    fid = commit["finalization_id"]
    set_availability(fid, "ERASED", reason="law")
    found = get_finalization_by_semantic_id(commit["semantic_result_id"])
    # Digest-retention law: the unsalted confirmation oracle does NOT survive
    # erasure — the stored digest is keyed/salted, never the raw sha256.
    assert found["content_hash"] != commit["content_hash"]
    assert found["content_hash"].startswith("salted-sha256:")
    assert not found["canonical_content"]
