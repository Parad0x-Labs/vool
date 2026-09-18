from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from core.workspace_authority_v3.base import AuthorityRecord
from core.workspace_authority_v3.contracts import (
    EnrollmentClaimV3,
    FailureCode,
    KeyFamily,
    KeyGenerationMetadata,
    KeyRingMetadata,
    KeyState,
)
from core.workspace_authority_v3.database import (
    AUTHORITY_APPLICATION_ID,
    AUTHORITY_SCHEMA_DIGEST,
    AUTHORITY_SCHEMA_VERSION,
    AuthorityCommitError,
    AuthorityStorageError,
    DormantAuthorityDatabase,
    TransactionOutcome,
    TransactionResult,
)
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    IncarnationId,
    InvocationIdentity,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceId,
)
from core.workspace_authority_v3.lifecycle import DormantOperation, DormantOperationRegistry
from tests.workspace_authority_v3_fixtures import enrollment_root, key_authority, owned_identity

KEY = b"d" * 32


def _opaque(identity_type: type, character: str):
    return owned_identity(identity_type, character)


def _claim() -> EnrollmentClaimV3:
    authority_domain_id, workspace_id, enrollment_operation_id = enrollment_root("database-claim")
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
        incarnation_id=_opaque(IncarnationId, "3"),
        enrollment_operation_id=enrollment_operation_id,
        incarnation_nonce="5" * 64,
        persistent_volume_identity_digest="6" * 64,
        root_object_identity_digest="7" * 64,
        runtime_home_id=_opaque(RuntimeHomeId, "8"),
        signing_key=key_authority(ring, KEY).signing_capability(
            family=KeyFamily.CLAIM_ENROLLMENT,
            generation=1,
            key_id="claim_enrollment-1",
        ),
    )


def _binding() -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 2, 3),
        workspace_id=_opaque(WorkspaceId, "4"),
        authority_epoch=AuthorityEpoch(5),
        canonical_action_digest="6" * 64,
    )


def test_database_is_dedicated_and_does_not_call_generic_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import storage.db

    monkeypatch.setattr(storage.db, "get_connection", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic DB used")))
    database = DormantAuthorityDatabase(tmp_path / "authority-phase0.sqlite3")
    assert database.execution_authority is False
    assert database.path == tmp_path / "authority-phase0.sqlite3"


def test_database_enforces_wal_full_foreign_keys_trusted_schema_and_identity(tmp_path: Path) -> None:
    database = DormantAuthorityDatabase(tmp_path / "authority-phase0.sqlite3")
    connection = database._connect()
    try:
        assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold() == "wal"
        assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 2
        assert int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        assert int(connection.execute("PRAGMA trusted_schema").fetchone()[0]) == 0
        assert int(connection.execute("PRAGMA application_id").fetchone()[0]) == AUTHORITY_APPLICATION_ID
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == AUTHORITY_SCHEMA_VERSION
        meta = connection.execute("SELECT schema_digest, phase, execution_authority FROM authority_phase0_meta").fetchone()
        assert tuple(meta) == (AUTHORITY_SCHEMA_DIGEST, "DORMANT_PHASE0", 0)
    finally:
        connection.close()
    database.validate_storage()


def test_database_refuses_to_claim_nonempty_or_foreign_application_databases(tmp_path: Path) -> None:
    nonempty = tmp_path / "nonempty.sqlite3"
    with sqlite3.connect(nonempty) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    with pytest.raises(AuthorityStorageError, match="refusing to claim"):
        DormantAuthorityDatabase(nonempty)

    authority_path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(authority_path)
    with sqlite3.connect(authority_path) as connection:
        connection.execute("PRAGMA application_id = 12345")
    with pytest.raises(AuthorityStorageError, match="identity changed"):
        database.validate_storage()


def test_database_validation_rejects_actual_schema_drift_even_when_meta_digest_survives(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE hostile_extra_table (value TEXT)")
    with pytest.raises(AuthorityStorageError, match="schema structure changed"):
        database.validate_storage()


def test_database_never_creates_reserved_namespace_or_missing_parents(tmp_path: Path) -> None:
    reserved_parent = tmp_path / ".vool-authority"
    with pytest.raises(ValueError, match="cannot place"):
        DormantAuthorityDatabase(reserved_parent / "authority.sqlite3")
    assert not reserved_parent.exists()
    with pytest.raises(ValueError, match="parent must already exist"):
        DormantAuthorityDatabase(tmp_path / "missing" / "authority.sqlite3")


def test_database_constraints_make_execution_authority_upgrade_impossible(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE authority_phase0_meta SET execution_authority = 1 WHERE singleton = 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO authority_phase0_records (
                    record_digest, record_type, canonical_bytes, phase, execution_authority
                ) VALUES (?, 'HostileUpgrade', ?, 'DORMANT_PHASE0', 1)
                """,
                ("a" * 64, b"{}"),
            )
    database.validate_storage()


def test_record_persistence_distinguishes_new_replay_and_tampering(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    claim = _claim()
    first = database.persist_record(claim)
    replay = database.persist_record(claim)
    assert first == TransactionResult(TransactionOutcome.COMMITTED_NEW, claim.digest())
    assert replay == TransactionResult(TransactionOutcome.COMMITTED_REPLAY, claim.digest())
    assert database.read_record_bytes(claim.digest()) == claim.canonical_bytes()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE authority_phase0_records SET canonical_bytes = ? WHERE record_digest = ?",
            (b'{"execution_authority":false}', claim.digest()),
        )
    with pytest.raises(AuthorityStorageError, match="identity mismatch"):
        database.read_record_bytes(claim.digest())


def test_record_persistence_rejects_executable_claim_hidden_inside_canonical_bytes(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="sealed"):

        @dataclass(frozen=True, slots=True)
        class HostileRecord(AuthorityRecord):
            RECORD_TYPE: ClassVar[str] = "HostileRecord"
            marker: str = "hostile"

            def to_record(self) -> dict[str, object]:
                return {"execution_authority": True}


def test_database_idempotency_replay_keeps_original_mutation_and_plan_conflict_is_typed(tmp_path: Path) -> None:
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    registry = DormantOperationRegistry()
    first_operation = registry.register(_binding(), "a" * 64).operation
    first = database.register_operation(first_operation)
    replay_candidate = DormantOperation(
        first_operation.operation_id,
        first_operation.identity_binding.mutation_id(),
        first_operation.identity_binding,
        first_operation.plan_digest,
    )
    replay = database.register_operation(replay_candidate)
    assert first.outcome is TransactionOutcome.COMMITTED_NEW
    assert replay.outcome is TransactionOutcome.COMMITTED_REPLAY
    assert replay.value is not None and first.value is not None
    assert replay.value.mutation_id == first.value.mutation_id
    assert replay.value.mutation_id == replay_candidate.mutation_id

    conflict_candidate = DormantOperation(
        first_operation.operation_id,
        first_operation.identity_binding.mutation_id(),
        first_operation.identity_binding,
        "b" * 64,
    )
    conflict = database.register_operation(conflict_candidate)
    assert conflict.outcome is TransactionOutcome.CAS_CONFLICT
    assert conflict.failure is not None and conflict.failure.code is FailureCode.IDEMPOTENCY_CONFLICT


def test_database_operation_replay_detects_hidden_canonical_payload_tampering(tmp_path: Path) -> None:
    path = tmp_path / "authority.sqlite3"
    database = DormantAuthorityDatabase(path)
    operation = DormantOperationRegistry().register(_binding(), "a" * 64).operation
    assert database.register_operation(operation).outcome is TransactionOutcome.COMMITTED_NEW
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE authority_phase0_operations SET canonical_bytes = ? WHERE operation_id = ?",
            (b'{"execution_authority":true}', str(operation.operation_id)),
        )
    assert database.register_operation(operation).outcome is TransactionOutcome.STORAGE_UNSAFE


def test_transaction_rolls_back_noncommitted_decisions(tmp_path: Path) -> None:
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")

    def callback(connection: sqlite3.Connection) -> TransactionResult[None]:
        connection.execute(
            "INSERT INTO authority_phase0_records (record_digest, record_type, canonical_bytes) VALUES (?, ?, ?)",
            ("f" * 64, "RolledBack", b"{}"),
        )
        return TransactionResult(TransactionOutcome.CAS_CONFLICT)

    assert database.run_transaction(callback).outcome is TransactionOutcome.CAS_CONFLICT
    assert database.read_record_bytes("f" * 64) is None


def test_commit_errors_are_propagated_with_unknown_outcome(tmp_path: Path) -> None:
    class CommitFailingDatabase(DormantAuthorityDatabase):
        fail_commits = False

        def _connect(self) -> sqlite3.Connection:
            connection = super()._connect()
            if self.fail_commits:
                def authorizer(action: int, argument: str | None, *_rest: object) -> int:
                    if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                connection.set_authorizer(authorizer)
            return connection

    database = CommitFailingDatabase(tmp_path / "authority.sqlite3")
    database.fail_commits = True
    with pytest.raises(AuthorityCommitError) as captured:
        database.persist_record(_claim())
    assert captured.value.result.outcome is TransactionOutcome.OUTCOME_UNKNOWN


def test_database_public_surface_contains_no_executor_or_head_mutator(tmp_path: Path) -> None:
    database = DormantAuthorityDatabase(tmp_path / "authority.sqlite3")
    forbidden = {
        "apply",
        "execute",
        "authorize",
        "rollback",
        "mutate_workspace",
        "change_head",
        "issue_broker_authority",
    }
    assert forbidden.isdisjoint(name for name in dir(database) if not name.startswith("_"))
