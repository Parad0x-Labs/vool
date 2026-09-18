"""Causal regressions for the Phase-0 durable database boundary (repair 2A, lane D).

Each test here fails when the production guard it names is removed, so a green run means
the guard executed rather than that the path was merely reachable.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.workspace_authority_v3.contracts import (
    EnrollmentClaimV3,
    KeyFamily,
    KeyGenerationMetadata,
    KeyRingMetadata,
    KeyState,
)
from core.workspace_authority_v3.database import (
    AuthorityStorageError,
    DormantAuthorityDatabase,
    RecoveryCycleStorageStatus,
    TransactionOutcome,
    _authenticated_head_state,
    _canonical_recovery_decision,
    _decision_binds_authenticated_cycle,
)
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceId,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperation,
    DormantOperationRegistry,
    OwnedHeadState,
    RecoveryDecisionResult,
    enter_recovery,
    issue_recovery_decision,
)
from tests.workspace_authority_v3_fixtures import (
    active_state_owner,
    enrollment_root,
    key_authority,
    owned_identity,
)

KEY = b"d" * 32


class _LyingDigest(str):
    """A caller-supplied digest whose equality claims to match anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


@contextlib.contextmanager
def _replaced_head_properties(**overrides: object) -> Iterator[None]:
    """Replace public ``OwnedHeadState`` properties, exactly as a caller could."""

    saved = {name: OwnedHeadState.__dict__[name] for name in overrides}
    for name, value in overrides.items():
        setattr(OwnedHeadState, name, property(lambda _self, _value=value: _value))
    try:
        yield
    finally:
        for name, descriptor in saved.items():
            setattr(OwnedHeadState, name, descriptor)


def _claim() -> EnrollmentClaimV3:
    authority_domain_id, workspace_id, enrollment_operation_id = enrollment_root("lane-d-claim")
    ring = KeyRingMetadata(
        tuple(
            KeyGenerationMetadata(
                family,
                f"{family.value.lower()}-1",
                1,
                KeyState.PENDING if family is KeyFamily.JOURNAL_EFFECT_RECEIPT else KeyState.ACTIVE,
                "HMAC-SHA256",
                1,
            )
            for family in KeyFamily
        )
    )
    return EnrollmentClaimV3.issue(
        authority_domain_id=authority_domain_id,
        workspace_id=workspace_id,
        incarnation_id=owned_identity(IncarnationId, "lane-d"),
        enrollment_operation_id=enrollment_operation_id,
        incarnation_nonce="5" * 64,
        persistent_volume_identity_digest="6" * 64,
        root_object_identity_digest="7" * 64,
        runtime_home_id=owned_identity(RuntimeHomeId, "lane-d"),
        signing_key=key_authority(ring, KEY).signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim_enrollment-1",
        ),
    )


def _binding(seed: str) -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
        workspace_id=owned_identity(WorkspaceId, seed),
        authority_epoch=AuthorityEpoch(5),
        canonical_action_digest="6" * 64,
    )


# --------------------------------------------------------------------------------------
# D7 — the durable predicate binds the internally authenticated state
# --------------------------------------------------------------------------------------


def test_d7_recovery_cycle_registration_binds_the_authenticated_cycle(tmp_path: Path) -> None:
    """A replaced ``recovery_cycle`` property must not redirect the durable cycle."""

    owner = active_state_owner("lane-d-register-owned")
    other_owner = active_state_owner("lane-d-register-other")
    state = enter_recovery(owner.current)
    other_state = enter_recovery(other_owner.current)
    owned_cycle = state.recovery_cycle
    other_cycle = other_state.recovery_cycle
    assert owned_cycle is not None and other_cycle is not None

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(other_state).outcome is TransactionOutcome.COMMITTED_NEW

    with _replaced_head_properties(recovery_cycle=other_cycle):
        redirected = database.register_recovery_cycle(state)

    # The authenticated snapshot owns `owned_cycle`, so that is the cycle that must exist.
    assert redirected.outcome is TransactionOutcome.COMMITTED_NEW
    assert redirected.value == str(owned_cycle.recovery_cycle_id)
    assert (
        database.recovery_cycle_status(owned_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.OPEN
    )
    assert (
        database.recovery_cycle_status(other_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.OPEN
    )


def test_d7_decision_persistence_cannot_be_redirected_onto_another_workspace(
    tmp_path: Path,
) -> None:
    """The database's own authenticated-cycle guard, exercised directly.

    A genuine, legitimately-issued decision for one workspace's cycle must never be
    accepted as durable proof for a different workspace's cycle. The redirection here
    is built from real objects rather than a property patch: the victim issues its own
    decision for its own cycle through the normal production API, and that decision is
    then presented as if it belonged to a different, unrelated authenticated state.

    persist_recovery_decision and commit_recovery_decision both call
    require_owned_recovery_decision (core.workspace_authority_v3.lifecycle) before
    they reach any database logic, and that lifecycle-layer guard independently
    compares every one of these same fields against the caller's own authenticated
    snapshot -- so it rejects this exact cross-workspace pairing first, by field
    comparison, with no path through it for a genuine mismatch. Calling the public
    method here would prove only that the lifecycle guard works, which
    tests/test_workspace_authority_v3_repair2a_lifecycle.py already covers -- it
    would never reach the database's own predicate. So the database's authenticated
    read and its binding predicate -- the exact production functions
    persist_recovery_decision/commit_recovery_decision call internally, at the same
    call shape -- are exercised directly, below the lifecycle pre-check, which is
    what actually proves the database enforces this on its own rather than merely
    inheriting it from the layer above.
    """

    owner = active_state_owner("lane-d-persist-owned")
    victim_owner = active_state_owner("lane-d-persist-victim")
    state = enter_recovery(owner.current)
    victim_state = enter_recovery(victim_owner.current)
    owned_cycle = state.recovery_cycle
    victim_cycle = victim_state.recovery_cycle
    assert owned_cycle is not None and victim_cycle is not None

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(state).outcome is TransactionOutcome.COMMITTED_NEW
    assert database.register_recovery_cycle(victim_state).outcome is TransactionOutcome.COMMITTED_NEW

    # A genuine decision for the VICTIM's own cycle, issued legitimately by the victim
    # itself -- no property patching, no forged fields, no bypass of any kind.
    victim_decision = issue_recovery_decision(
        victim_state,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="c" * 64,
    )

    # The database's own guard: read the OWNER's authenticated cycle (never the victim's)
    # and confirm the victim's decision does not bind it. This is the exact predicate
    # persist_recovery_decision and commit_recovery_decision both call, at the same
    # call shape, immediately after their own lifecycle pre-check.
    owner_authenticated = _authenticated_head_state(state)
    sealed_victim_decision = _canonical_recovery_decision(victim_decision)
    assert owner_authenticated.recovery_cycle is not None
    assert not _decision_binds_authenticated_cycle(
        sealed_victim_decision, owner_authenticated.recovery_cycle
    ), (
        "the database's own authenticated-cycle predicate must refuse a decision bound "
        "to a different workspace's cycle -- this is the guard persist_recovery_decision "
        "and commit_recovery_decision both depend on once the lifecycle pre-check passes"
    )

    # End-to-end confirmation through the full production entrypoint: the redirection is
    # refused (by the lifecycle pre-check, which runs first) and neither durable cycle is
    # left mutated.
    with pytest.raises(CasConflictError):
        database.persist_recovery_decision(state, victim_decision)

    assert (
        database.recovery_cycle_status(victim_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.OPEN
    )
    assert (
        database.recovery_cycle_status(owned_cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.OPEN
    )


def test_d7_recovery_commit_reads_the_authenticated_head_not_public_properties(
    tmp_path: Path,
) -> None:
    """Replaced head properties must not decide whether the durable cycle is consumed."""

    owner = active_state_owner("lane-d-commit")
    other_owner = active_state_owner("lane-d-commit-other")
    state = enter_recovery(owner.current)
    cycle = state.recovery_cycle
    assert cycle is not None

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(state).outcome is TransactionOutcome.COMMITTED_NEW
    decision = issue_recovery_decision(
        state,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="d" * 64,
    )
    assert database.persist_recovery_decision(state, decision).outcome is TransactionOutcome.COMMITTED_NEW

    with _replaced_head_properties(
        workspace_id=other_owner.current.workspace_id,
        incarnation_id=other_owner.current.incarnation_id,
        authority_epoch=AuthorityEpoch(99),
        head_revision=HeadRevision(99),
        recovery_generation=999,
    ):
        consumed = database.commit_recovery_decision(state, decision)

    assert consumed.outcome is TransactionOutcome.COMMITTED_NEW
    assert (
        database.recovery_cycle_status(cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.CONSUMED
    )


# --------------------------------------------------------------------------------------
# D8 — authority comparisons run on exact primitives, never caller virtual equality
# --------------------------------------------------------------------------------------


def test_d8_plan_digest_conflict_survives_a_lying_str_subclass(tmp_path: Path) -> None:
    """A ``str`` subclass claiming equality must not turn an idempotency conflict into replay."""

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    binding = _binding("lane-d-plan")
    original = DormantOperationRegistry().register(binding, "a" * 64).operation
    assert database.register_operation(original).outcome is TransactionOutcome.COMMITTED_NEW

    lying = DormantOperation(
        original.operation_id,
        binding.mutation_id(),
        binding,
        _LyingDigest("b" * 64),
    )
    with pytest.raises(TypeError, match="exact str"):
        database.register_operation(lying)

    honest = DormantOperation(original.operation_id, binding.mutation_id(), binding, "b" * 64)
    conflict = database.register_operation(honest)
    assert conflict.outcome is TransactionOutcome.CAS_CONFLICT
    assert conflict.value is not None and conflict.value.plan_digest == "a" * 64
    with sqlite3.connect(database.path) as connection:
        stored = connection.execute("SELECT plan_digest FROM authority_phase0_operations").fetchall()
    assert [row[0] for row in stored] == ["a" * 64]


def test_d8_stored_byte_integrity_survives_a_lying_digest_subclass(tmp_path: Path) -> None:
    """The stored-byte identity check must not be bypassable by caller equality."""

    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    claim = _claim()
    assert database.persist_record(claim).outcome is TransactionOutcome.COMMITTED_NEW
    assert database.read_record_bytes(claim.digest()) == claim.canonical_bytes()

    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE authority_phase0_records SET canonical_bytes = ? WHERE record_digest = ?",
            (b'{"execution_authority":false}', claim.digest()),
        )

    with pytest.raises(AuthorityStorageError, match="identity mismatch"):
        database.read_record_bytes(claim.digest())
    with pytest.raises(TypeError, match="exact str"):
        database.read_record_bytes(_LyingDigest(claim.digest()))


# --------------------------------------------------------------------------------------
# D9 — construction never adopts or disturbs filesystem objects it does not own
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_d9_construction_refuses_and_preserves_unowned_side_files(
    tmp_path: Path,
    suffix: str,
) -> None:
    """Pre-existing side files beside an absent database are refused, never consumed."""

    database_path = tmp_path / "authority.sqlite3"
    side_file = tmp_path / f"authority.sqlite3{suffix}"
    side_file.write_bytes(b"operator side-file content")

    with pytest.raises(AuthorityStorageError, match="unowned SQLite side files"):
        DormantAuthorityDatabase(database_path)

    assert side_file.exists()
    assert side_file.read_bytes() == b"operator side-file content"
    assert not database_path.exists()


def test_d9_construction_does_not_adopt_or_convert_a_foreign_database(tmp_path: Path) -> None:
    """A database owned by another application is refused without being written to."""

    foreign = tmp_path / "foreign.sqlite3"
    connection = sqlite3.connect(foreign)
    try:
        connection.execute("PRAGMA application_id = 999")
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    before = foreign.read_bytes()

    with pytest.raises(AuthorityStorageError, match="refusing to claim"):
        DormantAuthorityDatabase(foreign)

    assert foreign.read_bytes() == before
    assert not (tmp_path / "foreign.sqlite3-wal").exists()
    assert not (tmp_path / "foreign.sqlite3-shm").exists()


def test_d9_construction_refuses_a_non_database_file_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "notes.sqlite3"
    path.write_bytes(b"an operator file that merely shares the name")

    with pytest.raises(AuthorityStorageError, match="not a SQLite database"):
        DormantAuthorityDatabase(path)

    assert path.read_bytes() == b"an operator file that merely shares the name"


def test_d9_owned_database_still_reopens_with_its_own_side_files(tmp_path: Path) -> None:
    """The refusal must not reach a database this authority created."""

    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    assert database.persist_record(_claim()).committed
    connection = database._connect()
    try:
        connection.execute("SELECT 1").fetchone()
        assert (tmp_path / "authority.sqlite3-wal").exists()
        reopened = DormantAuthorityDatabase(path)
    finally:
        connection.close()
    reopened.validate_storage()


# --------------------------------------------------------------------------------------
# Previously uncovered load-bearing durable guards
# --------------------------------------------------------------------------------------


def test_one_open_cycle_per_workspace_is_enforced_durably(tmp_path: Path) -> None:
    """A second cycle must not open while the workspace still has an unconsumed one."""

    from core.workspace_authority_v3.lifecycle import apply_persisted_recovery_decision

    owner = active_state_owner("lane-d-one-open")
    first = enter_recovery(owner.current)
    first_cycle = first.recovery_cycle
    assert first_cycle is not None
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(first).outcome is TransactionOutcome.COMMITTED_NEW
    decision = issue_recovery_decision(
        first,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="e" * 64,
    )
    assert database.persist_recovery_decision(first, decision).committed
    active = apply_persisted_recovery_decision(first, database, decision)
    second = enter_recovery(active)
    assert second.recovery_cycle is not None and second.recovery_cycle.generation == 2

    # Reopen generation 1 durably: the workspace now holds an unconsumed cycle again.
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE authority_phase0_recovery_cycles SET status = 'OPEN' WHERE recovery_cycle_id = ?",
            (str(first_cycle.recovery_cycle_id),),
        )

    assert database.register_recovery_cycle(second).outcome is TransactionOutcome.CAS_CONFLICT
    assert database.recovery_cycle_status(second.recovery_cycle.recovery_cycle_id) is None


def test_recovery_generation_monotonicity_is_enforced_durably(tmp_path: Path) -> None:
    """Generation N must not open when the durable history does not end at N-1."""

    from core.workspace_authority_v3.lifecycle import apply_persisted_recovery_decision

    owner = active_state_owner("lane-d-monotonic")
    first = enter_recovery(owner.current)
    first_cycle = first.recovery_cycle
    assert first_cycle is not None
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(first).outcome is TransactionOutcome.COMMITTED_NEW
    decision = issue_recovery_decision(
        first,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="f" * 64,
    )
    assert database.persist_recovery_decision(first, decision).committed
    active = apply_persisted_recovery_decision(first, database, decision)
    second = enter_recovery(active)
    assert second.recovery_cycle is not None and second.recovery_cycle.generation == 2

    # Lose generation 1 durably; generation 2 no longer follows the stored history.
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "DELETE FROM authority_phase0_recovery_cycles WHERE recovery_cycle_id = ?",
            (str(first_cycle.recovery_cycle_id),),
        )

    assert database.register_recovery_cycle(second).outcome is TransactionOutcome.CAS_CONFLICT
    assert database.recovery_cycle_status(second.recovery_cycle.recovery_cycle_id) is None


def test_recovery_commit_refuses_when_the_stored_decision_bytes_changed(tmp_path: Path) -> None:
    """Consumption must re-read the stored decision bytes, not trust the cycle digest."""

    owner = active_state_owner("lane-d-stored-bytes")
    state = enter_recovery(owner.current)
    cycle = state.recovery_cycle
    assert cycle is not None
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    assert database.register_recovery_cycle(state).outcome is TransactionOutcome.COMMITTED_NEW
    decision = issue_recovery_decision(
        state,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="a" * 64,
    )
    assert database.persist_recovery_decision(state, decision).committed

    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE authority_phase0_records SET canonical_bytes = ? WHERE record_digest = ?",
            (b'{"tampered":true}', decision.digest()),
        )

    assert database.commit_recovery_decision(state, decision).outcome is TransactionOutcome.STORAGE_UNSAFE
    assert (
        database.recovery_cycle_status(cycle.recovery_cycle_id)
        is RecoveryCycleStorageStatus.DECISION_PERSISTED
    )
