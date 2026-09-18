"""F1-C / K-08 semantic ordering + K-05 certificate-at-finality tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol
from core.finalization import FinalizationRejected, finalize_answer
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f1c.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _admit(text="served bytes"):
    reset_admission()
    admitted = admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return admitted["_semantic_admission"]["semantic_result_id"]


def test_finalize_with_open_obligation_set_rejected(fresh_store):
    sr = _admit("answer bytes")
    opened = ol.open_obligation_set(
        obligations=[{"obligation_id": "ob:1", "text": "pending", "kind": "prose"}]
    )
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        with pytest.raises(FinalizationRejected, match="OBLIGATIONS_OPEN"):
            finalize_answer(turn_id="t", canonical_content="answer bytes")
        # Explicit verdict input behaves the same.
        with pytest.raises(FinalizationRejected):
            finalize_answer(
                turn_id="t",
                canonical_content="answer bytes",
                closure={"covered": False, "open_count": 1, "set_version": opened["version"]},
            )
    finally:
        ol.clear_active_set()


def test_finalize_with_closed_set_carries_certificate(fresh_store):
    sr = _admit("done bytes")
    commit = finalize_answer(
        turn_id="t2",
        canonical_content="done bytes",
        closure={"covered": True, "open_count": 0, "set_version": "v1:x"},
    )
    assert commit["closure_verdict"] == {
        "covered": True,
        "open_count": 0,
        "set_version": "v1:x",
    }


def test_unbound_lane_finalizes_vacuously_covered(fresh_store):
    _admit("plain bytes")
    commit = finalize_answer(turn_id="t3", canonical_content="plain bytes")
    assert commit["closure_verdict"]["covered"] is True


def test_post_admission_transform_is_verify_only_noop(fresh_store):
    """The verify-only contract: control applied to already-admitted bytes must
    not change them; divergence raises (simulated here at unit level)."""
    from core.web.api.response_control import apply_exact_response_control

    result = {"response": "already admitted", "route_reason": "model_lane"}
    controlled = apply_exact_response_control(dict(result), "unrelated input")
    # For a non-exact-contract turn this is a no-op — the invariant the HTTP
    # boundary asserts.
    assert str(controlled.get("response") or "") == str(result["response"])
