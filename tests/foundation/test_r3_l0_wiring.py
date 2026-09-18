"""R-3: L0 execution wiring — every canonical turn has execution identity;
generation is allocated by the fence CAS, never caller arithmetic."""
from __future__ import annotations

import threading

import pytest

import storage.db as sdb
from core.invocation import ledger
from core.invocation.ledger import FenceRefused, accept_invocation, open_execution
from core.runtime_continuity import (
    AttemptClaimRefused,
    MintRefused,
    create_runtime_attempt,
)
from core.semantic.semantic_admissions import set_request_context


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r3.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def test_distinct_trigger_concurrent_retries_get_distinct_cas_generations(fresh_store):
    """RED MUTATION R3 target: caller arithmetic would hand both retries the
    same N+1; the fence CAS serializes them into distinct generations."""
    req = accept_invocation(
        external_kind="http", external_value="r3-gen", principal="owner_local",
        raw_digest="d1",
    )["request_id"]
    token = set_request_context(req)
    try:
        parent = create_runtime_attempt(session_id="s", original_request="req")
        root = parent["root_attempt_id"]
        open_execution(request_id=req, root_attempt_id=root)

        barrier = threading.Barrier(2)
        results: list = []

        def _mint(trigger: str) -> None:
            barrier.wait()
            try:
                child = create_runtime_attempt(
                    session_id="s", original_request="req",
                    parent_attempt_id=parent["attempt_id"], root_attempt_id=root,
                    trigger_user_turn_id=trigger,
                    execution_generation=2,  # stale caller arithmetic — must be ignored
                )
                results.append(int(child["execution_generation"]))
            except Exception as exc:  # noqa: BLE001 - recorded for assertion
                results.append(exc)

        threads = [threading.Thread(target=_mint, args=(f"turn-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        gens = sorted(r for r in results if isinstance(r, int))
        assert len(gens) == 2, results
        assert gens[0] != gens[1], "duplicate generation numbers ⇒ INV-1 unsound"
    finally:
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        _CURRENT_REQUEST_ID.reset(token)


def test_max_generation_ceiling_refuses_further_retries(fresh_store):
    req = accept_invocation(
        external_kind="http", external_value="r3-cap", principal="owner_local",
        raw_digest="d2",
    )["request_id"]
    token = set_request_context(req)
    try:
        parent = create_runtime_attempt(session_id="s", original_request="req")
        root = parent["root_attempt_id"]
        ledger.open_execution(request_id=req, root_attempt_id=root, max_generation=1)
        # First retry consumes the single allowed bump...
        first = create_runtime_attempt(
            session_id="s", original_request="req",
            parent_attempt_id=parent["attempt_id"], root_attempt_id=root,
            trigger_user_turn_id="turn-cap-1",
        )
        assert int(first["execution_generation"]) == 2
        # ...the ceiling then refuses further generations.
        with pytest.raises(MintRefused, match="generation allocation refused"):
            create_runtime_attempt(
                session_id="s", original_request="req",
                parent_attempt_id=first["attempt_id"], root_attempt_id=root,
                trigger_user_turn_id="turn-cap-2",
            )
    finally:
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        _CURRENT_REQUEST_ID.reset(token)


def test_run_once_turn_door_aborts_without_a0_context():
    """Fail-closed per H1 §2: no A0 request bound ⇒ execution identity refused."""
    from apps.vool_agent import _r3_open_turn_execution
    from core.turn_contract import TurnRequest

    # R1b: the seam reads the turn's identity off the canonical request instead of
    # re-deriving it, so "no A0 request bound" is an EMPTY request_id on that object.
    unbound = TurnRequest.from_ingress(
        user_text="hi",
        source_context={"surface": "cli"},
        request_id="",
        turn_id="turn-unbound",
        session_id="s",
    )
    with pytest.raises(RuntimeError, match="no A0 request bound"):
        _r3_open_turn_execution({}, turn_request=unbound)
