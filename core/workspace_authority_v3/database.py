"""Dedicated SQLite foundation for dormant workspace authority V3 records.

This module intentionally does not import ``storage.db``. Its database is application-ID
scoped, schema-validated, and incapable of storing executable authority.
"""

from __future__ import annotations

import contextlib
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from core.enum_compat import StrEnum
from core.workspace_authority_v3.base import (
    AuthorityPhase,
    AuthorityRecord,
    _canonical_enum_value,
    dormant_envelope_record,
    require_closed_authority_record,
    require_digest,
    sealed_authority_record_bytes,
    sealed_authority_record_digest,
    validate_dormant_envelope,
)
from core.workspace_authority_v3.canonical import canonical_bytes, parse_strict_json, typed_sha256
from core.workspace_authority_v3.contracts import AuthorityFailure, FailureCode, SafeFailureContext
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OperationId,
    OperationIdentityBinding,
    RecoveryCycleId,
    RecoveryDecisionId,
    WorkspaceId,
    _canonical_binding_mutation_id,
    _canonical_binding_operation_id,
    _canonical_counter_value,
    _canonical_invocation_digest,
    _canonical_invocation_fields,
    _canonical_operation_binding_digest,
    _canonical_operation_binding_fields,
    _canonical_operation_binding_payload,
    _require_owned_identity_text,
    _stable_invocation_owned_identity,
    require_owned_identity,
    seal_canonical_enum,
)
from core.workspace_authority_v3.lifecycle import (
    DormantOperation,
    HeadState,
    OwnedHeadState,
    PersistedRecoveryDecision,
    RecoveryCycleBinding,
    RecoveryDecision,
    RecoveryDecisionResult,
    WorkspaceLifecycle,
    require_owned_head_state,
    require_owned_recovery_decision,
    seal_authority_recovery_commit,
)

AUTHORITY_APPLICATION_ID = int.from_bytes(b"VOOL", "big")
AUTHORITY_SCHEMA_VERSION = 3

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE authority_phase0_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_digest TEXT NOT NULL,
        phase TEXT NOT NULL CHECK (phase = 'DORMANT_PHASE0'),
        execution_authority INTEGER NOT NULL DEFAULT 0 CHECK (execution_authority = 0)
    ) STRICT
    """,
    """
    CREATE TABLE authority_phase0_records (
        record_digest TEXT PRIMARY KEY NOT NULL
            CHECK (length(record_digest) = 64 AND record_digest NOT GLOB '*[^0-9a-f]*'),
        record_type TEXT NOT NULL CHECK (length(record_type) BETWEEN 1 AND 128),
        canonical_bytes BLOB NOT NULL CHECK (length(canonical_bytes) > 0),
        phase TEXT NOT NULL DEFAULT 'DORMANT_PHASE0' CHECK (phase = 'DORMANT_PHASE0'),
        execution_authority INTEGER NOT NULL DEFAULT 0 CHECK (execution_authority = 0)
    ) STRICT
    """,
    """
    CREATE TABLE authority_phase0_operations (
        operation_id TEXT PRIMARY KEY NOT NULL,
        invocation_digest TEXT UNIQUE NOT NULL
            CHECK (length(invocation_digest) = 64 AND invocation_digest NOT GLOB '*[^0-9a-f]*'),
        binding_digest TEXT NOT NULL
            CHECK (length(binding_digest) = 64 AND binding_digest NOT GLOB '*[^0-9a-f]*'),
        mutation_id TEXT UNIQUE NOT NULL,
        plan_digest TEXT NOT NULL
            CHECK (length(plan_digest) = 64 AND plan_digest NOT GLOB '*[^0-9a-f]*'),
        canonical_bytes BLOB NOT NULL CHECK (length(canonical_bytes) > 0),
        phase TEXT NOT NULL DEFAULT 'DORMANT_PHASE0' CHECK (phase = 'DORMANT_PHASE0'),
        execution_authority INTEGER NOT NULL DEFAULT 0 CHECK (execution_authority = 0)
    ) STRICT
    """,
    """
    CREATE TABLE authority_phase0_recovery_cycles (
        recovery_cycle_id TEXT PRIMARY KEY NOT NULL,
        workspace_id TEXT NOT NULL,
        recovery_generation INTEGER NOT NULL CHECK (recovery_generation > 0),
        expected_recovery_decision_id TEXT UNIQUE NOT NULL,
        incarnation_id TEXT NOT NULL,
        authority_epoch INTEGER NOT NULL CHECK (authority_epoch > 0),
        expected_head_revision INTEGER NOT NULL CHECK (expected_head_revision >= 0),
        entered_from TEXT NOT NULL CHECK (entered_from <> 'RECOVERY_REQUIRED'),
        decision_digest TEXT UNIQUE
            CHECK (decision_digest IS NULL OR (length(decision_digest) = 64 AND decision_digest NOT GLOB '*[^0-9a-f]*')),
        status TEXT NOT NULL CHECK (status IN ('OPEN', 'DECISION_PERSISTED', 'CONSUMED')),
        canonical_bytes BLOB NOT NULL CHECK (length(canonical_bytes) > 0),
        phase TEXT NOT NULL DEFAULT 'DORMANT_PHASE0' CHECK (phase = 'DORMANT_PHASE0'),
        execution_authority INTEGER NOT NULL DEFAULT 0 CHECK (execution_authority = 0),
        UNIQUE (workspace_id, recovery_generation)
    ) STRICT
    """,
)
AUTHORITY_SCHEMA_DIGEST = typed_sha256(
    "VOOL_AUTHORITY_DATABASE_SCHEMA_V3",
    canonical_bytes(tuple(" ".join(statement.split()) for statement in _SCHEMA_STATEMENTS)),
)
_EXPECTED_TABLE_SQL = {
    statement.split()[2]: " ".join(statement.split())
    for statement in _SCHEMA_STATEMENTS
}

T = TypeVar("T")


class TransactionOutcome(StrEnum):
    COMMITTED_NEW = "COMMITTED_NEW"
    COMMITTED_REPLAY = "COMMITTED_REPLAY"
    NOT_COMMITTED_RETRYABLE = "NOT_COMMITTED_RETRYABLE"
    CAS_CONFLICT = "CAS_CONFLICT"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    STORAGE_UNSAFE = "STORAGE_UNSAFE"


seal_canonical_enum(TransactionOutcome)


class RecoveryCycleStorageStatus(StrEnum):
    OPEN = "OPEN"
    DECISION_PERSISTED = "DECISION_PERSISTED"
    CONSUMED = "CONSUMED"


seal_canonical_enum(RecoveryCycleStorageStatus)


_COMMITTED_OUTCOMES = frozenset({TransactionOutcome.COMMITTED_NEW, TransactionOutcome.COMMITTED_REPLAY})


@dataclass(frozen=True, slots=True)
class TransactionResult(Generic[T]):
    outcome: TransactionOutcome
    value: T | None = None
    failure: AuthorityFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, TransactionOutcome):
            raise TypeError("transaction outcome must be TransactionOutcome")
        if self.failure is not None and not isinstance(self.failure, AuthorityFailure):
            raise TypeError("transaction failure must be AuthorityFailure or None")

    @property
    def committed(self) -> bool:
        return self.outcome in _COMMITTED_OUTCOMES


class AuthorityStorageError(RuntimeError):
    def __init__(self, message: str, result: TransactionResult[object]) -> None:
        super().__init__(message)
        self.result = result


class AuthorityCommitError(AuthorityStorageError):
    """Commit failed after transaction work; callers must treat the outcome as unknown."""


@dataclass(frozen=True, slots=True)
class PersistedOperationIdentity:
    operation_id: OperationId
    mutation_id: MutationId
    invocation_digest: str
    binding_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        require_owned_identity(self.operation_id, OperationId)
        require_owned_identity(self.mutation_id, MutationId)
        require_digest(self.invocation_digest, "invocation_digest")
        require_digest(self.binding_digest, "binding_digest")
        require_digest(self.plan_digest, "plan_digest")


def _is_retryable_sqlite_error(exc: sqlite3.Error) -> bool:
    message = str(exc).casefold()
    return any(fragment in message for fragment in ("locked", "busy"))



_RECOVERY_REQUIRED_VALUE = _canonical_enum_value(WorkspaceLifecycle.RECOVERY_REQUIRED)
_REACTIVATE_VALUE = _canonical_enum_value(RecoveryDecisionResult.REACTIVATE)
_REQUIRE_REBIND_VALUE = _canonical_enum_value(RecoveryDecisionResult.REQUIRE_REBIND)
_REACTIVATE_TARGET = WorkspaceLifecycle.ACTIVE
_REQUIRE_REBIND_TARGET = WorkspaceLifecycle.REBIND_REQUIRED
_RECOVERY_DECISION_RECORD_TYPE = RecoveryDecision.__dict__["RECORD_TYPE"]
_DORMANT_PHASE_VALUE = _canonical_enum_value(AuthorityPhase.DORMANT_PHASE0)


def _exact_text(value: object, field_name: str) -> str:
    """Require an exact ``str``; a subclass can redirect an authority comparison."""

    if type(value) is not str:
        raise TypeError(f"{field_name} must be an exact canonical string")
    return value


def _exact_int(value: object, field_name: str) -> int:
    """Require an exact ``int``; a subclass can redirect an authority comparison."""

    if type(value) is not int:
        raise TypeError(f"{field_name} must be an exact canonical integer")
    return value


def _canonical_digest_text(value: object, field_name: str) -> str:
    """Validate a digest and pin it to an exact primitive before authority comparison.

    ``require_digest`` accepts any ``str`` instance, which is right for storage and
    encoding: canonical encoding reads the text, not the type.  A CAS or replay decision
    is different — it is settled by ``==`` against a value read back from storage, and
    Python offers the right-hand operand's ``__eq__`` first whenever its type is a
    subclass of the left-hand one.  A ``str`` subclass with altered equality therefore
    turns a conflict into a replay unless the comparison runs on exact primitives.
    """

    require_digest(value, field_name)
    if type(value) is not str:
        raise TypeError(f"{field_name} must be an exact str to bear authority")
    return value


@dataclass(frozen=True, slots=True)
class _AuthenticatedRecoveryCycle:
    """Exact primitives of the recovery cycle carried by one authenticated snapshot."""

    recovery_cycle_id: str
    workspace_id: str
    incarnation_id: str
    recovery_generation: int
    expected_recovery_decision_id: str
    authority_epoch: int
    expected_head_revision: int
    entered_from: str


@dataclass(frozen=True, slots=True)
class _AuthenticatedHeadState:
    """Exact primitives of the head state whose ownership the lifecycle owner proved."""

    workspace_id: str
    incarnation_id: str
    authority_epoch: int
    head_revision: int
    lifecycle: str
    recovery_generation: int
    recovery_cycle: _AuthenticatedRecoveryCycle | None


@dataclass(frozen=True, slots=True)
class _CanonicalRecoveryDecision:
    """One recovery decision read as exact primitives from its own sealed bytes."""

    record_digest: str
    record_bytes: bytes
    recovery_decision_id: str
    recovery_cycle_id: str
    recovery_generation: int
    workspace_id: str
    incarnation_id: str
    authority_epoch: int
    expected_head_revision: int
    prior_state: str
    result: str


def _build_authenticated_head_reader() -> Callable[[object], _AuthenticatedHeadState]:
    """Own the field descriptors a durable predicate is allowed to read, sealed at import.

    ``OwnedHeadState`` authenticates its private ``_snapshot`` — the fingerprint the
    lifecycle owner checks is taken from that slot.  Its public properties are an
    ordinary class-attribute layer that any caller can replace, and replacing one does
    not disturb the fingerprint.  Reading the durable workspace, cycle, epoch or head
    revision through those properties would therefore let a replacement bind storage to
    a different state than the one just authenticated.  The descriptors are captured
    here once and never re-read from the caller-visible classes afterwards.
    """

    head_field_names = (
        "workspace_id",
        "incarnation_id",
        "authority_epoch",
        "head_revision",
        "lifecycle",
        "recovery_generation",
        "recovery_cycle",
    )
    cycle_field_names = (
        "recovery_cycle_id",
        "workspace_id",
        "incarnation_id",
        "generation",
        "expected_recovery_decision_id",
        "authority_epoch",
        "expected_head_revision",
        "entered_from",
    )
    snapshot_slot = OwnedHeadState.__dict__["_snapshot"]
    head_slots = {name: HeadState.__dict__[name] for name in head_field_names}
    cycle_slots = {name: RecoveryCycleBinding.__dict__[name] for name in cycle_field_names}
    for descriptor in (snapshot_slot, *head_slots.values(), *cycle_slots.values()):
        if not hasattr(descriptor, "__get__"):
            raise TypeError("authority state fields must be readable field descriptors")

    def unsafe(message: str) -> AuthorityStorageError:
        return AuthorityStorageError(message, TransactionResult(TransactionOutcome.STORAGE_UNSAFE))

    def read_cycle(cycle: object) -> _AuthenticatedRecoveryCycle:
        if type(cycle) is not RecoveryCycleBinding:
            raise unsafe("authenticated head state does not carry an exact recovery cycle")

        def field(name: str) -> object:
            return cycle_slots[name].__get__(cycle, RecoveryCycleBinding)

        entered_from = field("entered_from")
        if type(entered_from) is not WorkspaceLifecycle:
            raise unsafe("authenticated recovery cycle does not bind an exact lifecycle")
        return _AuthenticatedRecoveryCycle(
            recovery_cycle_id=_require_owned_identity_text(field("recovery_cycle_id"), RecoveryCycleId),
            workspace_id=_require_owned_identity_text(field("workspace_id"), WorkspaceId),
            incarnation_id=_require_owned_identity_text(field("incarnation_id"), IncarnationId),
            recovery_generation=_exact_int(field("generation"), "recovery_generation"),
            expected_recovery_decision_id=_require_owned_identity_text(
                field("expected_recovery_decision_id"),
                RecoveryDecisionId,
            ),
            authority_epoch=_canonical_counter_value(field("authority_epoch")),
            expected_head_revision=_canonical_counter_value(field("expected_head_revision")),
            entered_from=_canonical_enum_value(entered_from),
        )

    def authenticated_head_state(state: object) -> _AuthenticatedHeadState:
        owned = require_owned_head_state(state)
        snapshot = snapshot_slot.__get__(owned, OwnedHeadState)
        if type(snapshot) is not HeadState:
            raise unsafe("owned head state does not carry an exact authenticated snapshot")

        def field(name: str) -> object:
            return head_slots[name].__get__(snapshot, HeadState)

        lifecycle = field("lifecycle")
        if type(lifecycle) is not WorkspaceLifecycle:
            raise unsafe("authenticated head state does not bind an exact lifecycle")
        raw_cycle = field("recovery_cycle")
        cycle = None if raw_cycle is None else read_cycle(raw_cycle)
        head = _AuthenticatedHeadState(
            workspace_id=_require_owned_identity_text(field("workspace_id"), WorkspaceId),
            incarnation_id=_require_owned_identity_text(field("incarnation_id"), IncarnationId),
            authority_epoch=_canonical_counter_value(field("authority_epoch")),
            head_revision=_canonical_counter_value(field("head_revision")),
            lifecycle=_canonical_enum_value(lifecycle),
            recovery_generation=_exact_int(field("recovery_generation"), "recovery_generation"),
            recovery_cycle=cycle,
        )
        if cycle is not None and (
            cycle.workspace_id != head.workspace_id
            or cycle.incarnation_id != head.incarnation_id
            or cycle.authority_epoch != head.authority_epoch
            or cycle.expected_head_revision != head.head_revision
            or cycle.recovery_generation != head.recovery_generation
        ):
            raise unsafe("authenticated recovery cycle does not bind its authenticated head")
        return head

    return authenticated_head_state


_authenticated_head_state = _build_authenticated_head_reader()


def _canonical_recovery_decision(decision: RecoveryDecision) -> _CanonicalRecoveryDecision:
    """Read one decision's authority fields from the exact bytes storage will hold.

    The sealed record bytes are the only view of a decision that is guaranteed free of
    caller virtual dispatch: every value returned here came out of the strict JSON
    parser, so a durable predicate compares exact primitives rather than whatever a
    field's declared type chooses to say about itself.
    """

    record_bytes = sealed_authority_record_bytes(decision)
    record_digest = sealed_authority_record_digest(decision)
    parsed = parse_strict_json(record_bytes)
    payload = validate_dormant_envelope(
        parsed,
        record_type=_RECOVERY_DECISION_RECORD_TYPE,
        schema_version=AUTHORITY_SCHEMA_VERSION,
    )
    result = _exact_text(payload.get("result"), "result")
    if result not in {_REACTIVATE_VALUE, _REQUIRE_REBIND_VALUE}:
        raise ValueError("sealed recovery decision carries an unknown result")
    return _CanonicalRecoveryDecision(
        record_digest=record_digest,
        record_bytes=record_bytes,
        recovery_decision_id=_exact_text(payload.get("recovery_decision_id"), "recovery_decision_id"),
        recovery_cycle_id=_exact_text(payload.get("recovery_cycle_id"), "recovery_cycle_id"),
        recovery_generation=_exact_int(payload.get("recovery_generation"), "recovery_generation"),
        workspace_id=_exact_text(payload.get("workspace_id"), "workspace_id"),
        incarnation_id=_exact_text(payload.get("incarnation_id"), "incarnation_id"),
        authority_epoch=_exact_int(payload.get("authority_epoch"), "authority_epoch"),
        expected_head_revision=_exact_int(
            payload.get("expected_head_revision"),
            "expected_head_revision",
        ),
        prior_state=_exact_text(payload.get("prior_state"), "prior_state"),
        result=result,
    )


def _decision_binds_authenticated_cycle(
    decision: _CanonicalRecoveryDecision,
    cycle: _AuthenticatedRecoveryCycle,
) -> bool:
    """Require the decision to name the exact cycle the authenticated snapshot owns."""

    return (
        decision.recovery_cycle_id == cycle.recovery_cycle_id
        and decision.recovery_decision_id == cycle.expected_recovery_decision_id
        and decision.recovery_generation == cycle.recovery_generation
        and decision.workspace_id == cycle.workspace_id
        and decision.incarnation_id == cycle.incarnation_id
        and decision.authority_epoch == cycle.authority_epoch
        and decision.expected_head_revision == cycle.expected_head_revision
        and decision.prior_state == cycle.entered_from
    )


_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"
_SQLITE_HEADER_LENGTH = 100
_SQLITE_SIDE_FILE_SUFFIXES = ("-journal", "-shm", "-wal")


def _require_unclaimed_or_owned_database_file(path: Path) -> None:
    """Refuse pre-existing filesystem objects this authority did not create or validate.

    Opening a SQLite database is not a read: it applies journal-mode conversion to the
    file it was pointed at, and it rolls back or discards a hot journal beside it.  Both
    are correct for a database this authority owns and both are destructive against one
    it does not, so ownership is settled from the on-disk header — one 100-byte read,
    no connection, no lock — before anything is opened.  Nothing here removes a file;
    ambiguity is refused and left exactly as it was found for an operator to resolve.
    """

    if not path.exists():
        size = 0
    elif not path.is_file():
        raise AuthorityStorageError(
            "authority database path is not a regular file",
            TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
        )
    else:
        size = path.stat().st_size
    if size == 0:
        present = tuple(
            suffix
            for suffix in _SQLITE_SIDE_FILE_SUFFIXES
            if path.with_name(path.name + suffix).exists()
        )
        if present:
            raise AuthorityStorageError(
                "refusing to open an authority database beside unowned SQLite side files",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            )
        return
    with path.open("rb") as handle:
        header = handle.read(_SQLITE_HEADER_LENGTH)
    if len(header) < _SQLITE_HEADER_LENGTH or not header.startswith(_SQLITE_HEADER_MAGIC):
        raise AuthorityStorageError(
            "refusing to claim a pre-existing file that is not a SQLite database",
            TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
        )
    application_id = int.from_bytes(header[68:72], "big")
    user_version = int.from_bytes(header[60:64], "big")
    schema_cookie = int.from_bytes(header[40:44], "big")
    if application_id == AUTHORITY_APPLICATION_ID:
        return
    if application_id == 0 and user_version == 0 and schema_cookie == 0:
        return
    raise AuthorityStorageError(
        "refusing to claim a pre-existing database owned by another application",
        TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
    )


class DormantAuthorityDatabase:
    """Non-authorizing storage for canonical Phase-0 artifacts and replay identities."""

    phase = AuthorityPhase.DORMANT_PHASE0
    execution_authority = False

    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)):
            raise TypeError("authority database path must be a string or Path")
        raw_path = str(path)
        if not raw_path.strip():
            raise ValueError("authority database path is required")
        self._path = Path(path)
        if self._path.name in {"", ".", ".."} or (self._path.exists() and self._path.is_dir()):
            raise ValueError("authority database path must identify a file")
        if any(
            unicodedata.normalize("NFKC", component).rstrip(" .").casefold() == ".vool-authority"
            for component in self._path.parts
        ):
            raise ValueError("Phase 0 cannot place storage in .vool-authority")
        if not self._path.parent.exists():
            raise ValueError("authority database parent must already exist")
        _require_unclaimed_or_owned_database_file(self._path)
        self._initialize_or_validate()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal is None or str(journal[0]).casefold() != "wal":
                raise AuthorityStorageError(
                    "authority database could not enter WAL mode",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            settings = {
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "trusted_schema": int(connection.execute("PRAGMA trusted_schema").fetchone()[0]),
            }
            if settings != {"synchronous": 2, "foreign_keys": 1, "trusted_schema": 0}:
                raise AuthorityStorageError(
                    "authority database safety pragmas were not retained",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize_or_validate(self) -> None:
        connection = self._connect()
        try:
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            )
            if application_id == 0:
                if user_version != 0 or tables:
                    raise AuthorityStorageError(
                        "refusing to claim a non-empty unscoped database",
                        TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                    )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA application_id = {AUTHORITY_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {AUTHORITY_SCHEMA_VERSION}")
                    connection.execute(
                        """
                        INSERT INTO authority_phase0_meta (singleton, schema_digest, phase, execution_authority)
                        VALUES (1, ?, 'DORMANT_PHASE0', 0)
                        """,
                        (AUTHORITY_SCHEMA_DIGEST,),
                    )
                    self._commit_or_raise_unknown(connection)
                except Exception:
                    with contextlib.suppress(Exception):
                        connection.rollback()
                    raise
            elif application_id != AUTHORITY_APPLICATION_ID or user_version != AUTHORITY_SCHEMA_VERSION:
                raise AuthorityStorageError(
                    "authority database application or schema identity mismatch",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
        finally:
            connection.close()
        self.validate_storage()

    @staticmethod
    def _commit_or_raise_unknown(connection: sqlite3.Connection) -> None:
        try:
            connection.commit()
        except BaseException as exc:
            with contextlib.suppress(BaseException):
                connection.rollback()
            raise AuthorityCommitError(
                "authority database commit failed; durable outcome is unknown",
                TransactionResult(TransactionOutcome.OUTCOME_UNKNOWN),
            ) from exc

    def validate_storage(self) -> None:
        connection = self._connect()
        try:
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id != AUTHORITY_APPLICATION_ID or user_version != AUTHORITY_SCHEMA_VERSION:
                raise AuthorityStorageError(
                    "authority database identity changed",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            integrity_rows = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall())
            if integrity_rows != ("ok",):
                raise AuthorityStorageError(
                    "authority database integrity check failed",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise AuthorityStorageError(
                    "authority database foreign-key check failed",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            actual_table_sql = {
                str(row["name"]): " ".join(str(row["sql"]).split())
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_schema "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            }
            if actual_table_sql != _EXPECTED_TABLE_SQL:
                raise AuthorityStorageError(
                    "authority database schema structure changed",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
            meta = connection.execute(
                "SELECT schema_digest, phase, execution_authority FROM authority_phase0_meta WHERE singleton = 1"
            ).fetchone()
            if (
                meta is None
                or str(meta["schema_digest"]) != AUTHORITY_SCHEMA_DIGEST
                or str(meta["phase"]) != _DORMANT_PHASE_VALUE
                or int(meta["execution_authority"]) != 0
            ):
                raise AuthorityStorageError(
                    "authority database metadata is unsafe",
                    TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
                )
        except sqlite3.Error as exc:
            raise AuthorityStorageError(
                "authority database validation failed",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            ) from exc
        finally:
            connection.close()

    def run_transaction(
        self,
        callback: Callable[[sqlite3.Connection], TransactionResult[T]],
    ) -> TransactionResult[T]:
        if not callable(callback):
            raise TypeError("transaction callback must be callable")
        try:
            connection = self._connect()
        except sqlite3.Error as exc:
            outcome = TransactionOutcome.NOT_COMMITTED_RETRYABLE if _is_retryable_sqlite_error(exc) else TransactionOutcome.STORAGE_UNSAFE
            return TransactionResult(outcome)
        began = False
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                began = True
            except sqlite3.Error as exc:
                outcome = TransactionOutcome.NOT_COMMITTED_RETRYABLE if _is_retryable_sqlite_error(exc) else TransactionOutcome.STORAGE_UNSAFE
                return TransactionResult(outcome)
            decision = callback(connection)
            if not isinstance(decision, TransactionResult):
                raise TypeError("transaction callback must return TransactionResult")
            if decision.committed:
                self._commit_or_raise_unknown(connection)
                return decision
            connection.rollback()
            return decision
        except AuthorityCommitError:
            raise
        except Exception:
            if began:
                with contextlib.suppress(Exception):
                    connection.rollback()
            raise
        finally:
            connection.close()

    def persist_record(self, record: AuthorityRecord) -> TransactionResult[str]:
        require_closed_authority_record(record)
        record_type = _exact_text(type(record).__dict__["RECORD_TYPE"], "RECORD_TYPE")
        payload = sealed_authority_record_bytes(record)
        record_digest = sealed_authority_record_digest(record)
        try:
            parsed_payload = parse_strict_json(payload)
        except ValueError as exc:
            raise ValueError("authority record must contain canonical JSON") from exc
        try:
            validate_dormant_envelope(
                parsed_payload,
                record_type=record_type,
                schema_version=AuthorityRecord.SCHEMA_VERSION,
            )
        except ValueError as exc:
            raise ValueError("authority record payload violates the dormant hard gate") from exc
        if canonical_bytes(parsed_payload) != payload:
            raise ValueError("authority record payload violates the dormant hard gate")

        def persist(connection: sqlite3.Connection) -> TransactionResult[str]:
            existing = connection.execute(
                "SELECT record_type, canonical_bytes, phase, execution_authority FROM authority_phase0_records WHERE record_digest = ?",
                (record_digest,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["record_type"]) != record_type
                    or bytes(existing["canonical_bytes"]) != payload
                    or str(existing["phase"]) != _DORMANT_PHASE_VALUE
                    or int(existing["execution_authority"]) != 0
                ):
                    return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
                return TransactionResult(TransactionOutcome.COMMITTED_REPLAY, record_digest)
            connection.execute(
                """
                INSERT INTO authority_phase0_records (
                    record_digest, record_type, canonical_bytes, phase, execution_authority
                ) VALUES (?, ?, ?, 'DORMANT_PHASE0', 0)
                """,
                (record_digest, record_type, payload),
            )
            return TransactionResult(TransactionOutcome.COMMITTED_NEW, record_digest)

        return self.run_transaction(persist)

    def read_record_bytes(self, record_digest: str) -> bytes | None:
        record_digest = _canonical_digest_text(record_digest, "record_digest")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT record_type, canonical_bytes, phase, execution_authority FROM authority_phase0_records WHERE record_digest = ?",
                (record_digest,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        payload = bytes(row["canonical_bytes"])
        record_type = str(row["record_type"])
        if str(row["phase"]) != _DORMANT_PHASE_VALUE or int(row["execution_authority"]) != 0:
            raise AuthorityStorageError(
                "persisted authority record claims executable authority",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            )
        try:
            parsed = parse_strict_json(payload)
        except ValueError as exc:
            raise AuthorityStorageError(
                "persisted authority record is not canonical",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            ) from exc
        try:
            validate_dormant_envelope(
                parsed,
                record_type=record_type,
                schema_version=AUTHORITY_SCHEMA_VERSION,
            )
        except ValueError as exc:
            raise AuthorityStorageError(
                "persisted authority record identity mismatch",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            ) from exc
        if (
            canonical_bytes(parsed) != payload
            or typed_sha256(f"VOOL_{record_type.upper()}_V3", payload) != record_digest
        ):
            raise AuthorityStorageError(
                "persisted authority record identity mismatch",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            )
        return payload

    def register_operation(self, operation: DormantOperation) -> TransactionResult[PersistedOperationIdentity]:
        if type(operation) is not DormantOperation:
            raise TypeError("operation registry accepts exact DormantOperation values only")
        DormantOperation.__post_init__(operation)
        require_owned_identity(operation.operation_id, OperationId)
        require_owned_identity(operation.mutation_id, MutationId)
        source_binding = operation.identity_binding
        source_invocation = source_binding.invocation_identity
        (
            _source_invocation_fields,
            source_workspace_id,
            source_authority_epoch,
            source_action_digest,
        ) = _canonical_operation_binding_fields(source_binding)
        binding = OperationIdentityBinding(
            invocation_identity=InvocationIdentity(
                *_canonical_invocation_fields(source_invocation)
            ),
            workspace_id=source_workspace_id,
            authority_epoch=AuthorityEpoch(_canonical_counter_value(source_authority_epoch)),
            canonical_action_digest=source_action_digest,
        )
        plan_digest = _canonical_digest_text(operation.plan_digest, "plan_digest")
        operation = DormantOperation(
            operation_id=_canonical_binding_operation_id(binding),
            mutation_id=_canonical_binding_mutation_id(binding),
            identity_binding=binding,
            plan_digest=plan_digest,
        )
        operation_id_text = _require_owned_identity_text(operation.operation_id, OperationId)
        mutation_id_text = _require_owned_identity_text(operation.mutation_id, MutationId)
        invocation_digest = _canonical_invocation_digest(
            _canonical_operation_binding_fields(operation.identity_binding)[0]
        )
        binding_digest = _canonical_operation_binding_digest(operation.identity_binding)
        persisted = PersistedOperationIdentity(
            operation.operation_id,
            operation.mutation_id,
            invocation_digest,
            binding_digest,
            plan_digest,
        )
        payload = canonical_bytes(
            dormant_envelope_record(
                record_type="DormantOperation",
                schema_version=AUTHORITY_SCHEMA_VERSION,
                payload={
                    "binding_digest": binding_digest,
                    "identity_binding": _canonical_operation_binding_payload(
                        operation.identity_binding
                    ),
                    "invocation_digest": invocation_digest,
                    "mutation_id": mutation_id_text,
                    "operation_id": operation_id_text,
                    "plan_digest": plan_digest,
                },
            )
        )

        def persist(connection: sqlite3.Connection) -> TransactionResult[PersistedOperationIdentity]:
            existing = connection.execute(
                """
                SELECT operation_id, invocation_digest, binding_digest, mutation_id, plan_digest, canonical_bytes,
                       phase, execution_authority
                FROM authority_phase0_operations
                WHERE operation_id = ? OR invocation_digest = ?
                """,
                (operation_id_text, invocation_digest),
            ).fetchone()
            if existing is not None:
                existing_operation_id = str(existing["operation_id"])
                existing_mutation_id = str(existing["mutation_id"])
                existing_invocation_digest = str(existing["invocation_digest"])
                existing_binding_digest = str(existing["binding_digest"])
                existing_plan_digest = str(existing["plan_digest"])
                try:
                    existing_payload = bytes(existing["canonical_bytes"])
                    parsed_existing = parse_strict_json(existing_payload)
                except (TypeError, ValueError):
                    existing_payload = b""
                    parsed_existing = None
                safe = (
                    str(existing["phase"]) == _DORMANT_PHASE_VALUE
                    and int(existing["execution_authority"]) == 0
                    and isinstance(parsed_existing, dict)
                    and canonical_bytes(parsed_existing) == existing_payload
                )
                try:
                    existing_record_payload = validate_dormant_envelope(
                        parsed_existing,
                        record_type="DormantOperation",
                        schema_version=AUTHORITY_SCHEMA_VERSION,
                    )
                except (TypeError, ValueError):
                    existing_record_payload = None
                safe = safe and (
                    isinstance(existing_record_payload, dict)
                    and set(existing_record_payload)
                    == {
                        "binding_digest",
                        "identity_binding",
                        "invocation_digest",
                        "mutation_id",
                        "operation_id",
                        "plan_digest",
                    }
                    and existing_record_payload.get("operation_id") == existing_operation_id
                    and existing_record_payload.get("mutation_id") == existing_mutation_id
                    and existing_record_payload.get("invocation_digest") == existing_invocation_digest
                    and existing_record_payload.get("binding_digest") == existing_binding_digest
                    and existing_record_payload.get("plan_digest") == existing_plan_digest
                    and typed_sha256(
                        "VOOL_OPERATION_IDENTITY_BINDING_V3",
                        canonical_bytes(existing_record_payload.get("identity_binding")),
                    )
                    == existing_binding_digest
                    and typed_sha256(
                        "VOOL_INVOCATION_IDENTITY_V3",
                        canonical_bytes(existing_record_payload.get("identity_binding", {}).get("invocation_identity")),
                    )
                    == existing_invocation_digest
                    and existing_operation_id
                    == _require_owned_identity_text(
                        _canonical_binding_operation_id(operation.identity_binding),
                        OperationId,
                    )
                    and existing_mutation_id
                    == _require_owned_identity_text(
                        _canonical_binding_mutation_id(operation.identity_binding),
                        MutationId,
                    )
                )
                same_plan = (
                    existing_operation_id == operation_id_text
                    and existing_invocation_digest == invocation_digest
                    and existing_binding_digest == binding_digest
                    and existing_plan_digest == plan_digest
                )
                if not safe:
                    return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
                binding_payload = existing_record_payload["identity_binding"]
                stored_invocation_payload = binding_payload["invocation_identity"]
                try:
                    stored_invocation = InvocationIdentity(
                        canonical_turn_id=stored_invocation_payload["canonical_turn_id"],
                        controller_generation=stored_invocation_payload["controller_generation"],
                        persisted_tool_ordinal=stored_invocation_payload["persisted_tool_ordinal"],
                    )
                except (KeyError, TypeError, ValueError):
                    return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
                derived_operation_id = _stable_invocation_owned_identity(
                    stored_invocation,
                    OperationId,
                )
                derived_mutation_id = _stable_invocation_owned_identity(
                    stored_invocation,
                    MutationId,
                )
                if (
                    _require_owned_identity_text(derived_operation_id, OperationId) != existing_operation_id
                    or _require_owned_identity_text(derived_mutation_id, MutationId) != existing_mutation_id
                ):
                    return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
                existing_identity = PersistedOperationIdentity(
                    derived_operation_id,
                    derived_mutation_id,
                    existing_invocation_digest,
                    existing_binding_digest,
                    existing_plan_digest,
                )
                if not same_plan:
                    return TransactionResult(
                        TransactionOutcome.CAS_CONFLICT,
                        existing_identity,
                        AuthorityFailure(
                            FailureCode.IDEMPOTENCY_CONFLICT,
                            False,
                            SafeFailureContext(operation_id=operation.operation_id),
                        ),
                    )
                return TransactionResult(TransactionOutcome.COMMITTED_REPLAY, existing_identity)
            connection.execute(
                """
                INSERT INTO authority_phase0_operations (
                    operation_id, invocation_digest, binding_digest, mutation_id, plan_digest, canonical_bytes,
                    phase, execution_authority
                ) VALUES (?, ?, ?, ?, ?, ?, 'DORMANT_PHASE0', 0)
                """,
                (
                    operation_id_text,
                    invocation_digest,
                    binding_digest,
                    mutation_id_text,
                    plan_digest,
                    payload,
                ),
            )
            return TransactionResult(TransactionOutcome.COMMITTED_NEW, persisted)

        return self.run_transaction(persist)

    def register_recovery_cycle(self, state: OwnedHeadState) -> TransactionResult[str]:
        """Durably open the exact recovery cycle owned by the lifecycle state owner."""

        authenticated = _authenticated_head_state(state)
        cycle = authenticated.recovery_cycle
        if authenticated.lifecycle != _RECOVERY_REQUIRED_VALUE or cycle is None:
            raise ValueError("recovery-cycle registration requires RECOVERY_REQUIRED owner state")
        payload = canonical_bytes(
            dormant_envelope_record(
                record_type="DormantRecoveryCycle",
                schema_version=AUTHORITY_SCHEMA_VERSION,
                payload={
                    "authority_epoch": cycle.authority_epoch,
                    "entered_from": cycle.entered_from,
                    "expected_head_revision": cycle.expected_head_revision,
                    "expected_recovery_decision_id": cycle.expected_recovery_decision_id,
                    "incarnation_id": cycle.incarnation_id,
                    "recovery_cycle_id": cycle.recovery_cycle_id,
                    "recovery_generation": cycle.recovery_generation,
                    "workspace_id": cycle.workspace_id,
                },
            )
        )

        def persist(connection: sqlite3.Connection) -> TransactionResult[str]:
            existing = connection.execute(
                """
                SELECT * FROM authority_phase0_recovery_cycles
                WHERE recovery_cycle_id = ? OR expected_recovery_decision_id = ?
                   OR (workspace_id = ? AND recovery_generation = ?)
                """,
                (
                    cycle.recovery_cycle_id,
                    cycle.expected_recovery_decision_id,
                    cycle.workspace_id,
                    cycle.recovery_generation,
                ),
            ).fetchone()
            if existing is not None:
                exact = (
                    str(existing["recovery_cycle_id"]) == cycle.recovery_cycle_id
                    and str(existing["workspace_id"]) == cycle.workspace_id
                    and int(existing["recovery_generation"]) == cycle.recovery_generation
                    and str(existing["expected_recovery_decision_id"])
                    == cycle.expected_recovery_decision_id
                    and str(existing["incarnation_id"]) == cycle.incarnation_id
                    and int(existing["authority_epoch"]) == cycle.authority_epoch
                    and int(existing["expected_head_revision"]) == cycle.expected_head_revision
                    and str(existing["entered_from"]) == cycle.entered_from
                    and bytes(existing["canonical_bytes"]) == payload
                    and str(existing["phase"]) == _DORMANT_PHASE_VALUE
                    and int(existing["execution_authority"]) == 0
                )
                return TransactionResult(
                    TransactionOutcome.COMMITTED_REPLAY if exact else TransactionOutcome.CAS_CONFLICT,
                    cycle.recovery_cycle_id if exact else None,
                )
            open_cycle = connection.execute(
                """
                SELECT 1 FROM authority_phase0_recovery_cycles
                WHERE workspace_id = ? AND status <> 'CONSUMED'
                """,
                (cycle.workspace_id,),
            ).fetchone()
            if open_cycle is not None:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            latest = connection.execute(
                "SELECT MAX(recovery_generation) FROM authority_phase0_recovery_cycles WHERE workspace_id = ?",
                (cycle.workspace_id,),
            ).fetchone()
            latest_generation = None if latest is None or latest[0] is None else int(latest[0])
            expected_generation = 1 if latest_generation is None else latest_generation + 1
            if cycle.recovery_generation != expected_generation:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            connection.execute(
                """
                INSERT INTO authority_phase0_recovery_cycles (
                    recovery_cycle_id, workspace_id, recovery_generation,
                    expected_recovery_decision_id, incarnation_id, authority_epoch,
                    expected_head_revision, entered_from, decision_digest, status,
                    canonical_bytes, phase, execution_authority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'OPEN', ?, 'DORMANT_PHASE0', 0)
                """,
                (
                    cycle.recovery_cycle_id,
                    cycle.workspace_id,
                    cycle.recovery_generation,
                    cycle.expected_recovery_decision_id,
                    cycle.incarnation_id,
                    cycle.authority_epoch,
                    cycle.expected_head_revision,
                    cycle.entered_from,
                    payload,
                ),
            )
            return TransactionResult(TransactionOutcome.COMMITTED_NEW, cycle.recovery_cycle_id)

        return self.run_transaction(persist)

    def recovery_cycle_status(
        self,
        recovery_cycle_id: RecoveryCycleId,
    ) -> RecoveryCycleStorageStatus | None:
        """Read the durable lifecycle of one exact recovery cycle without granting authority."""

        cycle_id = _require_owned_identity_text(recovery_cycle_id, RecoveryCycleId)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT recovery_cycle_id, status, phase, execution_authority
                FROM authority_phase0_recovery_cycles WHERE recovery_cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        if (
            str(row["recovery_cycle_id"]) != cycle_id
            or str(row["phase"]) != _DORMANT_PHASE_VALUE
            or int(row["execution_authority"]) != 0
        ):
            raise AuthorityStorageError(
                "persisted recovery cycle violates its dormant owner",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            )
        try:
            return RecoveryCycleStorageStatus(str(row["status"]))
        except ValueError as exc:
            raise AuthorityStorageError(
                "persisted recovery cycle status is invalid",
                TransactionResult(TransactionOutcome.STORAGE_UNSAFE),
            ) from exc

    def persist_recovery_decision(
        self,
        state: OwnedHeadState,
        decision: RecoveryDecision,
    ) -> TransactionResult[PersistedRecoveryDecision]:
        require_owned_recovery_decision(state, decision)
        if type(decision) is not RecoveryDecision:
            raise TypeError("decision must be an exact RecoveryDecision")
        require_closed_authority_record(decision)
        authenticated = _authenticated_head_state(state)
        owned_cycle = authenticated.recovery_cycle
        if authenticated.lifecycle != _RECOVERY_REQUIRED_VALUE or owned_cycle is None:
            return TransactionResult(TransactionOutcome.CAS_CONFLICT)
        sealed = _canonical_recovery_decision(decision)
        if not _decision_binds_authenticated_cycle(sealed, owned_cycle):
            return TransactionResult(TransactionOutcome.CAS_CONFLICT)
        record_digest = sealed.record_digest
        record_bytes = sealed.record_bytes

        def persist(connection: sqlite3.Connection) -> TransactionResult[str]:
            cycle = connection.execute(
                "SELECT * FROM authority_phase0_recovery_cycles WHERE recovery_cycle_id = ?",
                (owned_cycle.recovery_cycle_id,),
            ).fetchone()
            if cycle is None:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            exact_cycle = (
                str(cycle["workspace_id"]) == owned_cycle.workspace_id
                and int(cycle["recovery_generation"]) == owned_cycle.recovery_generation
                and str(cycle["expected_recovery_decision_id"])
                == owned_cycle.expected_recovery_decision_id
                and str(cycle["incarnation_id"]) == owned_cycle.incarnation_id
                and int(cycle["authority_epoch"]) == owned_cycle.authority_epoch
                and int(cycle["expected_head_revision"]) == owned_cycle.expected_head_revision
                and str(cycle["entered_from"]) == owned_cycle.entered_from
                and str(cycle["phase"]) == _DORMANT_PHASE_VALUE
                and int(cycle["execution_authority"]) == 0
            )
            if not exact_cycle or str(cycle["status"]) == "CONSUMED":
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            existing_digest = cycle["decision_digest"]
            if existing_digest is not None and str(existing_digest) != record_digest:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            existing_record = connection.execute(
                """
                SELECT record_type, canonical_bytes, phase, execution_authority
                FROM authority_phase0_records WHERE record_digest = ?
                """,
                (record_digest,),
            ).fetchone()
            if existing_record is not None:
                safe_record = (
                    str(existing_record["record_type"]) == _RECOVERY_DECISION_RECORD_TYPE
                    and bytes(existing_record["canonical_bytes"]) == record_bytes
                    and str(existing_record["phase"]) == _DORMANT_PHASE_VALUE
                    and int(existing_record["execution_authority"]) == 0
                )
                if not safe_record:
                    return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
            else:
                connection.execute(
                    """
                    INSERT INTO authority_phase0_records (
                        record_digest, record_type, canonical_bytes, phase, execution_authority
                    ) VALUES (?, ?, ?, 'DORMANT_PHASE0', 0)
                    """,
                    (record_digest, _RECOVERY_DECISION_RECORD_TYPE, record_bytes),
                )
            replayed = str(cycle["status"]) == "DECISION_PERSISTED"
            connection.execute(
                """
                UPDATE authority_phase0_recovery_cycles
                SET decision_digest = ?, status = 'DECISION_PERSISTED'
                WHERE recovery_cycle_id = ? AND status IN ('OPEN', 'DECISION_PERSISTED')
                """,
                (record_digest, owned_cycle.recovery_cycle_id),
            )
            return TransactionResult(
                TransactionOutcome.COMMITTED_REPLAY if replayed else TransactionOutcome.COMMITTED_NEW,
                record_digest,
            )

        stored = self.run_transaction(persist)
        if not stored.committed or stored.value is None:
            return TransactionResult(stored.outcome, failure=stored.failure)
        receipt = PersistedRecoveryDecision(decision, stored.value)
        return TransactionResult(stored.outcome, receipt, stored.failure)

    def commit_recovery_decision(
        self,
        state: OwnedHeadState,
        decision: RecoveryDecision,
    ) -> TransactionResult[WorkspaceLifecycle]:
        authenticated = _authenticated_head_state(state)
        require_owned_recovery_decision(state, decision)
        cycle = authenticated.recovery_cycle
        if authenticated.lifecycle != _RECOVERY_REQUIRED_VALUE or cycle is None:
            return TransactionResult(TransactionOutcome.CAS_CONFLICT)
        sealed = _canonical_recovery_decision(decision)
        if not _decision_binds_authenticated_cycle(sealed, cycle):
            return TransactionResult(TransactionOutcome.CAS_CONFLICT)
        record_digest = sealed.record_digest
        target = _REACTIVATE_TARGET if sealed.result == _REACTIVATE_VALUE else _REQUIRE_REBIND_TARGET

        def consume(connection: sqlite3.Connection) -> TransactionResult[WorkspaceLifecycle]:
            row = connection.execute(
                "SELECT * FROM authority_phase0_recovery_cycles WHERE recovery_cycle_id = ?",
                (cycle.recovery_cycle_id,),
            ).fetchone()
            if row is None:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            exact = (
                str(row["status"]) in {"DECISION_PERSISTED", "CONSUMED"}
                and str(row["decision_digest"]) == record_digest
                and str(row["expected_recovery_decision_id"]) == cycle.expected_recovery_decision_id
                and str(row["workspace_id"]) == authenticated.workspace_id
                and str(row["incarnation_id"]) == authenticated.incarnation_id
                and int(row["authority_epoch"]) == authenticated.authority_epoch
                and int(row["expected_head_revision"]) == authenticated.head_revision
                and int(row["recovery_generation"]) == authenticated.recovery_generation
                and str(row["entered_from"]) == cycle.entered_from
                and str(row["phase"]) == _DORMANT_PHASE_VALUE
                and int(row["execution_authority"]) == 0
            )
            if not exact:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            persisted_bytes = connection.execute(
                "SELECT canonical_bytes FROM authority_phase0_records WHERE record_digest = ?",
                (record_digest,),
            ).fetchone()
            if persisted_bytes is None or bytes(persisted_bytes[0]) != sealed.record_bytes:
                return TransactionResult(TransactionOutcome.STORAGE_UNSAFE)
            if str(row["status"]) == "CONSUMED":
                return TransactionResult(TransactionOutcome.COMMITTED_REPLAY, target)
            updated = connection.execute(
                """
                UPDATE authority_phase0_recovery_cycles SET status = 'CONSUMED'
                WHERE recovery_cycle_id = ? AND status = 'DECISION_PERSISTED'
                """,
                (cycle.recovery_cycle_id,),
            )
            if updated.rowcount != 1:
                return TransactionResult(TransactionOutcome.CAS_CONFLICT)
            return TransactionResult(TransactionOutcome.COMMITTED_NEW, target)

        return self.run_transaction(consume)


# Hand the lifecycle boundary the exact commit operation this type was created with, the
# same way the sealed enums above are registered at definition time.  From here on the
# recovery transition calls that captured function and never re-reads this class.
seal_authority_recovery_commit(DormantAuthorityDatabase)


__all__ = [
    "AUTHORITY_APPLICATION_ID",
    "AUTHORITY_SCHEMA_DIGEST",
    "AUTHORITY_SCHEMA_VERSION",
    "AuthorityCommitError",
    "AuthorityStorageError",
    "DormantAuthorityDatabase",
    "PersistedOperationIdentity",
    "RecoveryCycleStorageStatus",
    "TransactionOutcome",
    "TransactionResult",
]
