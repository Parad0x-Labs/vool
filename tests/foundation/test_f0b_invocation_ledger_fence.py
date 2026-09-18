"""F0-B / K-01 + K-02 invocation ledger + execution fence tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.invocation import ledger
from core.invocation.ledger import (
    FenceRefused,
    InvocationConflict,
    PrincipalDenied,
    accept_invocation,
    bump_generation,
    charge_root_budget,
    open_execution,
    require_generation,
    set_execution_terminal,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f0b.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _accept(**kw):
    params = dict(
        external_kind="http",
        external_value="evt-1",
        principal="owner_local",
        raw_digest="digest-1",
    )
    params.update(kw)
    return accept_invocation(**params)


# --- K-01: ACCEPT-ONCE / BIND-BEFORE-WORK / DEFAULT-DENY PRINCIPAL ------------

def test_same_external_id_delivered_twice_is_one_request(fresh_store):
    first = _accept()
    second = _accept()
    assert first["outcome"] == "ACCEPTED_FIRST"
    assert second["outcome"] == "ACCEPTED_IDENTICAL"
    assert first["request_id"] == second["request_id"]


def test_byte_diff_under_same_id_conflicts(fresh_store):
    _accept(raw_digest="aaa")
    with pytest.raises(InvocationConflict):
        _accept(raw_digest="bbb")


def test_missing_principal_never_becomes_owner(fresh_store):
    with pytest.raises(PrincipalDenied):
        _accept(principal="")
    with pytest.raises(PrincipalDenied):
        _accept(principal="  ")
    # A forged free-string principal class is refused too.
    with pytest.raises(PrincipalDenied):
        _accept(principal="i_am_owner_trust_me")


def test_bindings_are_frozen_no_update_path(fresh_store):
    got = _accept()
    row = ledger.get_invocation(got["request_id"])
    assert row["principal"] == "owner_local"
    assert row["privacy_local_only"] == 1


# --- K-02: fence ------------------------------------------------------------

def test_open_execution_requires_accepted_request(fresh_store):
    with pytest.raises(ValueError, match="A0 must accept first"):
        open_execution(request_id="req:none", root_attempt_id="attempt-x")


def test_generation_cas_and_stale_epoch_refused(fresh_store):
    req = _accept()["request_id"]
    ex = open_execution(request_id=req, root_attempt_id="attempt-root")
    assert ex["generation"] == 0
    assert bump_generation("attempt-root") == 1
    # EPOCH REFUSE: a writer from a dead process incarnation is refused.
    with pytest.raises(FenceRefused):
        bump_generation("attempt-root", runtime_epoch="proc-dead-process")
    # Stale generation presentation is refused.
    with pytest.raises(FenceRefused):
        require_generation("attempt-root", 0)
    require_generation("attempt-root", 1)


def test_max_generation_ceiling(fresh_store):
    req = _accept(external_value="evt-ceiling")["request_id"]
    open_execution(request_id=req, root_attempt_id="attempt-cap", max_generation=2)
    assert bump_generation("attempt-cap") == 1
    assert bump_generation("attempt-cap") == 2
    with pytest.raises(FenceRefused):
        bump_generation("attempt-cap")


def test_terminal_execution_refuses_bumps_and_double_terminal(fresh_store):
    req = _accept(external_value="evt-term")["request_id"]
    open_execution(request_id=req, root_attempt_id="attempt-term")
    assert set_execution_terminal("attempt-term", "COMPLETED")
    # Second terminal write: fence refuses (rowcount 0).
    assert not set_execution_terminal("attempt-term", "FAILED")
    with pytest.raises(FenceRefused):
        bump_generation("attempt-term")


def test_root_budget_decrement_not_reset(fresh_store):
    req = _accept(external_value="evt-budget")["request_id"]
    open_execution(
        request_id=req,
        root_attempt_id="attempt-budget",
        budget_remaining_calls=3,
        budget_remaining_effects=1,
    )
    assert charge_root_budget("attempt-budget", calls=2)
    # Retry/new generation draws down the SAME root custody.
    bump_generation("attempt-budget")
    assert charge_root_budget("attempt-budget", calls=1)
    assert not charge_root_budget("attempt-budget", calls=1)  # exhausted
    row = ledger.get_execution("attempt-budget")
    assert row["budget_remaining_calls"] == 0
    assert row["budget_remaining_effects"] == 1


def test_one_live_attempt_partial_index_enforced(fresh_store):
    import sqlite3 as _sq

    req = _accept(external_value="evt-live")["request_id"]
    open_execution(request_id=req, root_attempt_id="attempt-live")
    conn = sdb.get_connection()
    try:
        conn.execute(
            "INSERT INTO runtime_attempts (attempt_id, session_id, execution_id,"
            " lifecycle_state, created_at, updated_at)"
            " VALUES ('a-live-1', 's', 'attempt-live', 'RUNNING', 't', 't')"
        )
        with pytest.raises(_sq.IntegrityError):
            conn.execute(
                "INSERT INTO runtime_attempts (attempt_id, session_id, execution_id,"
                " lifecycle_state, created_at, updated_at)"
                " VALUES ('a-live-2', 's', 'attempt-live', 'RUNNING', 't', 't')"
            )
        # Terminal sibling rows are fine; legacy '' rows are untouched.
        conn.execute(
            "INSERT INTO runtime_attempts (attempt_id, session_id, execution_id,"
            " lifecycle_state, created_at, updated_at)"
            " VALUES ('a-done', 's', 'attempt-live', 'SUCCEEDED', 't', 't')"
        )
        conn.commit()
    finally:
        conn.close()


def test_create_runtime_attempt_stamps_execution_id(fresh_store):
    from core.runtime_continuity import create_runtime_attempt

    created = create_runtime_attempt(session_id="s", original_request="hello")
    assert created["execution_id"] == created["root_attempt_id"] == created["attempt_id"]
