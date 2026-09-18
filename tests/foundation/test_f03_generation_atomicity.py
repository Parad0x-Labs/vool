"""F-03 repair proof: generation allocation is one atomic fence operation.

Concurrent distinct-trigger retry mints must receive DISTINCT monotonically
authoritative generations — the returned/persisted value comes from the same
statement that allocates it (UPDATE ... RETURNING), never from a
read-after-commit re-read.

Mutation target: reverting bump_generation to commit-then-separate-SELECT
reintroduces duplicate persisted generations under contention → RED.
"""
from __future__ import annotations

import threading

import pytest

import storage.db as sdb


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f03.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _open_execution(execution_id: str) -> None:
    from core.invocation.ledger import accept_invocation, open_execution

    accepted = accept_invocation(
        external_kind="test",
        external_value=f"ext-{execution_id}",
        principal="owner_local",
        raw_digest="sha256:test",
    )
    open_execution(
        request_id=accepted["request_id"],
        root_attempt_id=execution_id,
        max_generation=1000,
        budget_remaining_calls=100000,
        budget_remaining_effects=100000,
    )


def _stored_generations() -> list[int]:
    conn = sdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT execution_generation FROM runtime_attempts "
            "WHERE execution_id != '' AND execution_id IS NOT NULL"
        ).fetchall()
        return sorted(int(r["execution_generation"]) for r in rows)
    finally:
        conn.close()


def test_concurrent_retry_mints_receive_distinct_generations(fresh_store):
    from core.runtime_continuity import create_runtime_attempt

    _open_execution("exec-f03")
    threads_n = 8
    barrier = threading.Barrier(threads_n)
    errors: list[BaseException] = []

    def _mint(i: int) -> None:
        try:
            barrier.wait()
            create_runtime_attempt(
                session_id="s-f03",
                original_request="q",
                root_attempt_id="exec-f03",
                parent_attempt_id="exec-f03",
                trigger_user_turn_id=f"t-f03-{i}",
                execution_generation=1,  # caller arithmetic must be ignored
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=_mint, args=(i,)) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert not errors, errors[:3]
    gens = _stored_generations()
    assert len(gens) == threads_n, gens
    assert len(set(gens)) == threads_n, (
        f"duplicate durable execution_generation persisted: {gens}"
    )


def test_bump_generation_returns_allocated_value_matching_fence_row(fresh_store):
    """The returned value must equal the fence row's authoritative value after
    the call — no skew between lease and custody."""
    from core.invocation.ledger import bump_generation, get_execution

    _open_execution("exec-f03b")
    for expected in range(1, 6):
        got = bump_generation("exec-f03b")
        assert got == expected, (got, expected)
        assert int(get_execution("exec-f03b")["generation"]) == expected
