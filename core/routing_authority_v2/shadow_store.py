"""SQLite persistence for non-authorizing routing authority V2 shadow records.

Retention law: rows are append-only diagnostics. Chat deletion is the ONE removal
authority — ``RoutingDecisionShadowV2`` rows of the deleted chat's folded session
identity are removed whole via :meth:`delete_shadow_records`; there is no rotation,
pruning or silent trimming. Deletion never rewrites a surviving row, so every
survivor's canonical bytes, digest and type validation remain exactly what they were.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from core.routing_authority_v2 import contracts as _contracts
from core.routing_authority_v2.contracts import (
    AuthorityRecord,
    AuthorityState,
    ContractValidationError,
    parse_authority_json,
)

_SHADOW_TABLE = "routing_authority_v2_shadow_records"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_PHASE_ZERO = 0
_NON_EXECUTABLE_AUTHORITY_STATE = AuthorityState.NON_EXECUTABLE_SHADOW.value
_ALLOWED_SHADOW_TYPES = MappingProxyType({
    _contracts.SubjectBindingV2: ("SubjectBindingV2", 2),
    _contracts.RoutingIntentV2: ("RoutingIntentV2", 2),
    _contracts.RemoteAutoRuleV2: ("RemoteAutoRuleV2", 2),
    _contracts.RemoteAutoRulesV2: ("RemoteAutoRulesV2", 2),
    _contracts.RoutingPermissionSnapshotV2: ("RoutingPermissionSnapshotV2", 2),
    _contracts.TaskRequirementsV2: ("TaskRequirementsV2", 2),
    _contracts.ContextEnvelopeV2: ("ContextEnvelopeV2", 2),
    _contracts.ContextAttestationV2: ("ContextAttestationV2", 2),
    _contracts.TurnRoutingEnvelopeV2: ("TurnRoutingEnvelopeV2", 2),
    _contracts.CostChargeV2: ("CostChargeV2", 2),
    _contracts.CostAttestationV2: ("CostAttestationV2", 2),
    _contracts.ExecutableCandidateRefV2: ("ExecutableCandidateRefV2", 2),
    _contracts.RoutingPlanV2: ("RoutingPlanV2", 2),
    _contracts.RoutingAttemptPermitV2: ("RoutingAttemptPermitV2", 2),
    _contracts.RoutingDecisionShadowV2: ("RoutingDecisionShadowV2", 2),
    _contracts.ProviderNetworkOperationPermitV2: ("ProviderNetworkOperationPermitV2", 2),
    _contracts.SpendReservationV2: ("SpendReservationV2", 2),
    _contracts.UnknownExternalAcknowledgementV2: ("UnknownExternalAcknowledgementV2", 2),
    _contracts.UnknownExternalAttemptGrantV2: ("UnknownExternalAttemptGrantV2", 2),
    _contracts.PayloadProvenanceEntryV1: ("PayloadProvenanceEntryV1", 1),
    _contracts.ProviderPayloadProvenanceMapV1: ("ProviderPayloadProvenanceMapV1", 1),
    _contracts.ProviderInvocationManifestV2: ("ProviderInvocationManifestV2", 2),
    _contracts.OpaqueAuthorityRefV2: ("OpaqueAuthorityRefV2", 2),
    _contracts.RoutingFailureV2: ("RoutingFailureV2", 2),
    _contracts.RoutingReceiptV2: ("RoutingReceiptV2", 2),
})
_ALLOWED_SHADOW_DISCRIMINATORS = MappingProxyType(
    {metadata: record_type for record_type, metadata in _ALLOWED_SHADOW_TYPES.items()}
)
_ALLOWED_RECORD_NAMES = frozenset(record_type for record_type, _version in _ALLOWED_SHADOW_TYPES.values())
_SCHEMA_DISCRIMINATOR_CHECK = " OR ".join(
    f"(record_type = '{record_type}' AND schema_version = {version})"
    for record_type, version in sorted(_ALLOWED_SHADOW_DISCRIMINATORS)
)


class ShadowStoreIntegrityError(RuntimeError):
    """Persisted shadow bytes disagree with their typed canonical identity."""


@dataclass(frozen=True)
class ShadowPayloadCensus:
    """One approved type's rows split by whether their storage shapes are well-formed.

    ``payloads`` are the ``(record_digest, canonical_bytes)`` pairs whose digest and
    payload columns match the declared shapes; ``anomalous_rows`` counts everything else
    of that type — a TEXT ``canonical_bytes`` (legal to SQLite despite the BLOB
    affinity) or an invalid digest spelling. Anomalous rows are never attributed from
    and never deleted by the chat-deletion seam: their bytes are not what their digest
    vouches for, so their session cannot be established soundly — they are preserved
    and reported so the deletion door can say verification was incomplete.
    """

    payloads: tuple[tuple[str, bytes], ...] = ()
    anomalous_rows: int = 0


def _closed_phase0_record_payload(record: AuthorityRecord) -> tuple[bytes, str, int]:
    metadata = _ALLOWED_SHADOW_TYPES.get(type(record))
    if metadata is None:
        raise ContractValidationError("shadow store rejects unregistered or caller-defined authority record types")
    expected_record_type, expected_version = metadata
    if expected_record_type != record.RECORD_TYPE or expected_version != record.SCHEMA_VERSION:
        raise ContractValidationError("shadow record type discriminator is not the closed Phase-0 schema")
    canonical_record = record.to_record()
    if "execution_authority" in canonical_record:
        raise ContractValidationError("shadow records cannot contain execution authority")
    if (
        "authority_state" in canonical_record
        and canonical_record["authority_state"] != AuthorityState.NON_EXECUTABLE_SHADOW.value
    ):
        raise ContractValidationError("shadow authority_state must be NON_EXECUTABLE_SHADOW")
    return record.canonical_bytes(), expected_record_type, expected_version


def _expected_shadow_row(record: AuthorityRecord) -> tuple[str, str, int, bytes, int, str, int]:
    payload, record_type, schema_version = _closed_phase0_record_payload(record)
    return (
        record.digest(),
        record_type,
        schema_version,
        payload,
        _PHASE_ZERO,
        _NON_EXECUTABLE_AUTHORITY_STATE,
        0,
    )


def _validate_stored_shadow_row(
    row: tuple[object, ...],
    *,
    expected_row: tuple[str, str, int, bytes, int, str, int] | None = None,
) -> AuthorityRecord:
    if len(row) != 7:
        raise ShadowStoreIntegrityError("shadow row does not match the closed Phase-0 storage schema")
    record_digest, record_type, schema_version, payload, authority_phase, authority_state, execution_authority = row
    if not isinstance(record_digest, str) or not _DIGEST_RE.fullmatch(record_digest):
        raise ShadowStoreIntegrityError("shadow row contains an invalid record digest")
    if authority_phase != _PHASE_ZERO:
        raise ShadowStoreIntegrityError("shadow row illegally claims a nonzero authority phase")
    if authority_state != _NON_EXECUTABLE_AUTHORITY_STATE:
        raise ShadowStoreIntegrityError("shadow row illegally claims executable authority state")
    if execution_authority != 0:
        raise ShadowStoreIntegrityError("shadow row illegally claims execution authority")
    if not isinstance(record_type, str) or not isinstance(schema_version, int):
        raise ShadowStoreIntegrityError("shadow row contains invalid type metadata")
    expected_type = _ALLOWED_SHADOW_DISCRIMINATORS.get((record_type, schema_version))
    if expected_type is None:
        raise ShadowStoreIntegrityError("shadow row uses an unapproved type or schema version")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ShadowStoreIntegrityError("shadow row canonical payload is not stored as bytes")
    canonical_payload = bytes(payload)
    normalized_row = (
        record_digest,
        record_type,
        schema_version,
        canonical_payload,
        authority_phase,
        authority_state,
        execution_authority,
    )
    if expected_row is not None and normalized_row != expected_row:
        raise ShadowStoreIntegrityError("idempotent shadow replay disagrees with the complete canonical row")
    try:
        record = parse_authority_json(canonical_payload)
    except ContractValidationError as exc:
        raise ShadowStoreIntegrityError("shadow record contains invalid canonical bytes") from exc
    if type(record) is not expected_type:
        raise ShadowStoreIntegrityError("shadow parser resolved outside the closed Phase-0 type allowlist")
    if record_type != record.RECORD_TYPE or schema_version != record.SCHEMA_VERSION:
        raise ShadowStoreIntegrityError("shadow row metadata disagrees with canonical record")
    try:
        validated_payload, validated_type, validated_version = _closed_phase0_record_payload(record)
    except ContractValidationError as exc:
        raise ShadowStoreIntegrityError("shadow record violates the non-executable Phase-0 gate") from exc
    if (
        validated_payload != canonical_payload
        or validated_type != record_type
        or validated_version != schema_version
        or record.digest() != record_digest
    ):
        raise ShadowStoreIntegrityError("shadow record canonical identity mismatch")
    return record


class RoutingAuthorityV2ShadowStore:
    """Non-authorizing diagnostic storage, isolated from provider invocation state."""

    __slots__ = ("_path",)

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if self._path.name in {"", ".", ".."}:
            raise ValueError("shadow store path must identify a file")
        if not self._path.parent.exists():
            raise ValueError("shadow store parent directory must already exist")
        self._initialize_shadow_schema()

    @property
    def execution_authority(self) -> bool:
        return False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_shadow_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_SHADOW_TABLE} (
                    record_digest TEXT PRIMARY KEY NOT NULL,
                    record_type TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                    canonical_bytes BLOB NOT NULL,
                    authority_phase INTEGER NOT NULL DEFAULT 0 CHECK (authority_phase = 0),
                    authority_state TEXT NOT NULL DEFAULT '{_NON_EXECUTABLE_AUTHORITY_STATE}'
                        CHECK (authority_state = '{_NON_EXECUTABLE_AUTHORITY_STATE}'),
                    execution_authority INTEGER NOT NULL DEFAULT 0 CHECK (execution_authority = 0),
                    CHECK ({_SCHEMA_DISCRIMINATOR_CHECK})
                )
                """
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({_SHADOW_TABLE})").fetchall()
            }
            if "authority_phase" not in existing_columns:
                connection.execute(
                    f"""
                    ALTER TABLE {_SHADOW_TABLE}
                    ADD COLUMN authority_phase INTEGER NOT NULL DEFAULT 0 CHECK (authority_phase = 0)
                    """
                )
            if "authority_state" not in existing_columns:
                connection.execute(
                    f"""
                    ALTER TABLE {_SHADOW_TABLE}
                    ADD COLUMN authority_state TEXT NOT NULL DEFAULT '{_NON_EXECUTABLE_AUTHORITY_STATE}'
                    CHECK (authority_state = '{_NON_EXECUTABLE_AUTHORITY_STATE}')
                    """
                )

    def persist_shadow_record(self, record: AuthorityRecord) -> str:
        if not isinstance(record, AuthorityRecord):
            raise TypeError("shadow store accepts AuthorityRecord values only")
        expected_row = _expected_shadow_row(record)
        digest, record_type, schema_version, payload, authority_phase, authority_state, execution_authority = expected_row
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"""
                SELECT record_digest, record_type, schema_version, canonical_bytes,
                       authority_phase, authority_state, execution_authority
                FROM {_SHADOW_TABLE}
                WHERE record_digest = ?
                """,
                (digest,),
            ).fetchone()
            if existing is not None:
                _validate_stored_shadow_row(existing, expected_row=expected_row)
                return digest
            digest_alias = connection.execute(
                f"""
                SELECT record_digest, record_type, schema_version, canonical_bytes,
                       authority_phase, authority_state, execution_authority
                FROM {_SHADOW_TABLE}
                WHERE canonical_bytes = ?
                """,
                (payload,),
            ).fetchone()
            if digest_alias is not None:
                _validate_stored_shadow_row(digest_alias, expected_row=expected_row)
            connection.execute(
                f"""
                INSERT INTO {_SHADOW_TABLE} (
                    record_digest,
                    record_type,
                    schema_version,
                    canonical_bytes,
                    authority_phase,
                    authority_state,
                    execution_authority
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    record_type,
                    schema_version,
                    payload,
                    authority_phase,
                    authority_state,
                    execution_authority,
                ),
            )
        return digest

    def read_shadow_record(self, record_digest: str) -> AuthorityRecord | None:
        if not isinstance(record_digest, str) or not _DIGEST_RE.fullmatch(record_digest):
            raise ContractValidationError("shadow record digest must use lowercase hex")
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT record_digest, record_type, schema_version, canonical_bytes,
                       authority_phase, authority_state, execution_authority
                FROM {_SHADOW_TABLE}
                WHERE record_digest = ?
                """,
                (record_digest,),
            ).fetchone()
        if row is None:
            return None
        return _validate_stored_shadow_row(row)

    def list_shadow_record_digests(self, *, record_type: str | None = None) -> tuple[str, ...]:
        if record_type is not None and record_type not in _ALLOWED_RECORD_NAMES:
            raise ContractValidationError("unknown Phase-0 shadow record type")
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT record_digest, record_type, schema_version, canonical_bytes,
                       authority_phase, authority_state, execution_authority
                FROM {_SHADOW_TABLE}
                ORDER BY record_digest
                """
            ).fetchall()
        validated_rows = tuple((_validate_stored_shadow_row(row), row) for row in rows)
        return tuple(
            str(row[0])
            for record, row in validated_rows
            if record_type is None or record_type == record.RECORD_TYPE
        )

    def shadow_payload_census(self, *, record_type: str) -> ShadowPayloadCensus:
        """Well-formed payloads of ONE approved type, plus the count of anomalous rows.

        The deletion-time attribution read: unlike :meth:`list_shadow_record_digests`
        this does NOT strictly validate each row, because a store holding a corrupted
        row must still let its owner enumerate the rest and decide attribution — the
        strict readers keep their integrity law unchanged. Rows whose digest or payload
        columns violate their declared shapes are NOT silently dropped: they are counted
        as ``anomalous_rows`` (see :class:`ShadowPayloadCensus`) so the deletion owner
        reports incomplete verification instead of claiming the whole sink was removed.
        """
        if record_type not in _ALLOWED_RECORD_NAMES:
            raise ContractValidationError("unknown Phase-0 shadow record type")
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT record_digest, canonical_bytes
                FROM {_SHADOW_TABLE}
                WHERE record_type = ?
                ORDER BY record_digest
                """,
                (record_type,),
            ).fetchall()
        payloads: list[tuple[str, bytes]] = []
        anomalous = 0
        for digest, payload in rows:
            if isinstance(digest, str) and _DIGEST_RE.fullmatch(digest) and isinstance(
                payload, (bytes, bytearray, memoryview)
            ):
                payloads.append((digest, bytes(payload)))
            else:
                anomalous += 1
        return ShadowPayloadCensus(
            payloads=tuple(payloads), anomalous_rows=anomalous
        )

    def list_shadow_payloads(self, *, record_type: str) -> tuple[tuple[str, bytes], ...]:
        """``(record_digest, canonical_bytes)`` for every WELL-FORMED row of ONE type.

        Convenience view over :meth:`shadow_payload_census` for callers that only need
        the parseable payloads. A store also holding anomalous rows (storage types or
        digest shapes that violate the declared contract) is invisible to this listing —
        deletion-time attribution must use the census so those rows are counted, not
        silently skipped as if the sink were fully verified.
        """
        return self.shadow_payload_census(record_type=record_type).payloads

    def delete_shadow_records(self, *, record_type: str, record_digests: tuple[str, ...]) -> int:
        """Delete WHOLE rows by digest, restricted to ONE approved record type.

        The chat-deletion seam. Only rows whose record type AND digest both match are
        removed, in a single transaction; survivors are never rewritten, so their
        canonical identity, idempotent replay and type validation are untouched.
        Re-insertion law: ``persist_shadow_record`` of identical canonical bytes after a
        deletion inserts the same digest again — resurrection is prevented by the
        routing owner's deleted-session guard at record time, not by this store.
        """
        if record_type not in _ALLOWED_RECORD_NAMES:
            raise ContractValidationError("unknown Phase-0 shadow record type")
        digests = tuple(dict.fromkeys(str(digest) for digest in record_digests))
        for digest in digests:
            if not _DIGEST_RE.fullmatch(digest):
                raise ContractValidationError("shadow record digest must use lowercase hex")
        removed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for digest in digests:
                cursor = connection.execute(
                    f"""
                    DELETE FROM {_SHADOW_TABLE}
                    WHERE record_digest = ? AND record_type = ?
                    """,
                    (digest, record_type),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    removed += cursor.rowcount
        return removed


__all__ = ["RoutingAuthorityV2ShadowStore", "ShadowStoreIntegrityError"]
