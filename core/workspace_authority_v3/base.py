"""Shared immutable-record machinery for dormant workspace authority contracts.

Phase-0 trust boundary
----------------------

What the record machinery here enforces, and what it deliberately does not, so that
neither this module nor its tests are read as a stronger guarantee than the code gives.

Defended — ordinary manipulation of the authority object graph:

* subclassing or multiple-inheriting a sealed record type;
* replacing instance methods, class methods, properties, or descriptors that trusted
  derivation would otherwise dispatch through;
* mutating a frozen field after construction, including via ``object.__setattr__``;
* replacing a public module-level wrapper such as ``encode_value``;
* bypassing a sealed constructor, and the construction bypasses named in the contract.

Enforced — construction-to-validation consistency: a record's canonical bytes, its
issuance fingerprint, its envelope view, and its validation all resolve the same closed
encoding, so a record cannot be built under one encoding and later accepted under another.

Not claimed — integrity against arbitrary code substitution inside the same Python
interpreter.  Closure-cell rewriting, ``__code__``/``__globals__`` replacement, rebinding
functions this module imports, and swapping stdlib primitives such as ``json.dumps`` or
the ``hashlib`` digests all defeat any pure-Python arrangement, including this one.  No
amount of module-level or closure-level sealing changes that, and Phase-0 does not
pretend otherwise.  An actor who can already rewrite trusted Python execution in-process
is outside this trust boundary, and the correct mitigation is a process/runtime-source
boundary rather than more sealing here (see the integration note below).

Phase-0 remains dormant and non-authorizing: nothing in this package performs or
authorizes a filesystem mutation, and these guarantees describe record integrity only.

Future integration note (Phase-1 lane, not implemented here)
------------------------------------------------------------

Before Mutation Authority becomes executable, trusted authority code must not share an
agent-writable runtime/import root without an enforced boundary.  While the agent runtime
can write the same source tree it imports trusted authority code from, the "not claimed"
row above is reachable in normal operation rather than only under an assumed compromise.
Establishing that boundary — separating the trusted authority runtime source from
agent-writable workspace content — belongs to the Phase-1/integration lane and is
deliberately out of scope for Phase-0 repair work.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, ClassVar

from core.enum_compat import StrEnum
from core.workspace_authority_v3.canonical import MAX_SIGNED_64, canonical_bytes, typed_sha256
from core.workspace_authority_v3.identity import (
    OpaqueIdentity,
    _BoundedCounter,
    _canonical_counter_value,
    _canonical_enum_value,
    _require_owned_identity_text,
    seal_canonical_enum,
)

DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")


class ContractValidationError(ValueError):
    """A dormant authority object violates a frozen Phase-0 invariant."""


class AuthorityPhase(StrEnum):
    DORMANT_PHASE0 = "DORMANT_PHASE0"


seal_canonical_enum(AuthorityPhase)


_ENVELOPE_KEYS = frozenset({"authority_metadata", "payload", "record_type", "schema_version"})
_RESERVED_AUTHORITY_FIELDS = frozenset({"execution_authority", "phase", *_ENVELOPE_KEYS})
_SEALED_SERIALIZER_NAMES = frozenset(
    {"__getattr__", "__getattribute__", "canonical_bytes", "digest", "envelope", "to_record"}
)
_SUPPORTED_AUTHORITY_RECORDS = frozenset(
    {
        ("core.workspace_authority_v3.contracts", "AuthorityBudgetProfile", "AuthorityBudgetProfile"),
        ("core.workspace_authority_v3.contracts", "BackupGenerationContract", "BackupGenerationContract"),
        ("core.workspace_authority_v3.contracts", "BackupManifestV3", "BackupManifestV3"),
        ("core.workspace_authority_v3.contracts", "EnrollmentClaimV3", "EnrollmentClaimV3"),
        ("core.workspace_authority_v3.contracts", "KeyGenerationMetadata", "KeyGenerationMetadata"),
        ("core.workspace_authority_v3.contracts", "KeyRingMetadata", "KeyRingMetadata"),
        ("core.workspace_authority_v3.contracts", "MountCertificate", "MountCertificate"),
        ("core.workspace_authority_v3.contracts", "WorkspaceUserSnapshot", "WorkspaceUserSnapshot"),
        ("core.workspace_authority_v3.cursor", "AuthenticatedCursorPayload", "AuthenticatedCursorPayload"),
        ("core.workspace_authority_v3.lifecycle", "RecoveryDecision", "RecoveryDecision"),
    }
)


def require_digest(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
        raise ContractValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def require_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ContractValidationError(f"{field_name} must be a canonical safe identifier")
    return value


def require_nonnegative(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SIGNED_64:
        raise ContractValidationError(f"{field_name} must be a non-negative signed-64 integer")
    return value


def require_positive(value: int, field_name: str) -> int:
    require_nonnegative(value, field_name)
    if value == 0:
        raise ContractValidationError(f"{field_name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class _AuthorityRecordSpec:
    concrete_type: type
    module: str
    qualname: str
    record_type: str
    validator: Any
    field_names: tuple[str, ...] = ()


def _build_serialization_boundary(
    identity_text: Any,
    counter_value: Any,
    enum_value: Any,
    phase_value: str,
    envelope_keys: frozenset,
    reserved_fields: frozenset,
    sealed_serializer_names: frozenset,
    supported_records: frozenset,
    safe_identifier: Any,
    positive_integer: Any,
) -> tuple[Any, ...]:
    """One closed owner for the schema registry, issuance state, and canonical encoding.

    The encoder, envelope writer, fingerprint, and record sealer are siblings in this
    closure and resolve one another through closure cells, and no module-visible
    dictionary, list, or function binding carries the trusted encoder.  The property this
    buys is that replacing a *public module attribute* — the ordinary object-graph move —
    does not redirect trusted encoding, so construction and validation cannot be brought
    into agreement on an altered encoding that way.

    This is not a defence against arbitrary same-process code substitution: rewriting the
    cells of this closure, patching ``__code__``/``__globals__``, or swapping the stdlib
    primitives underneath ``canonical_bytes`` all still change the encoding.  See the
    module docstring for the Phase-0 trust boundary that governs this claim.
    """

    specs_by_discriminator: dict[str, _AuthorityRecordSpec] = {}
    specs_by_type: dict[type, _AuthorityRecordSpec] = {}
    instances: dict[int, tuple[object, bytes | None]] = {}
    construction_state = threading.local()
    validation_state = threading.local()
    authority_base: type | None = None

    def encode(value: Any) -> Any:
        if authority_base is not None and isinstance(value, authority_base):
            return seal_record(value)
        if isinstance(value, OpaqueIdentity):
            return identity_text(value, type(value))
        if isinstance(value, _BoundedCounter):
            return counter_value(value)
        if isinstance(value, Enum):
            return enum_value(value)
        if is_dataclass(value):
            return {field.name: encode(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        if isinstance(value, list):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {str(key): encode(item) for key, item in value.items()}
        return value

    def payload_fingerprint(record: object, spec: _AuthorityRecordSpec) -> bytes:
        return canonical_bytes(
            {field_name: encode(getattr(record, field_name)) for field_name in spec.field_names}
        )

    class AuthorityRecordMeta(type):
        def __new__(
            mcls,
            name: str,
            bases: tuple[type, ...],
            namespace: dict[str, Any],
            **kwargs: Any,
        ) -> type:
            nonlocal authority_base
            if authority_base is None:
                created_base = super().__new__(mcls, name, bases, namespace, **kwargs)
                authority_base = created_base
                return created_base
            if bases != (authority_base,):
                raise TypeError("authority record types are final and cannot use multiple inheritance")
            annotations = namespace.get("__annotations__", {})
            collisions = set(annotations) & reserved_fields
            if collisions:
                raise TypeError(
                    f"authority record fields collide with the sealed envelope: {sorted(collisions)!r}"
                )
            overrides = set(namespace) & (
                sealed_serializer_names | {"SCHEMA_VERSION", "execution_authority", "phase"}
            )
            if overrides:
                raise TypeError(
                    f"authority record cannot override sealed hard-gate members: {sorted(overrides)!r}"
                )
            record_type = namespace.get("RECORD_TYPE")
            if not isinstance(record_type, str):
                raise TypeError("AuthorityRecord subclasses must declare a string RECORD_TYPE")
            safe_identifier(record_type, "RECORD_TYPE")
            validator = namespace.get("__post_init__")
            if not callable(validator):
                raise TypeError("AuthorityRecord subclasses must own an exact __post_init__ validator")

            created = super().__new__(mcls, name, bases, namespace, **kwargs)
            field_names = tuple(field.name for field in fields(created)) if is_dataclass(created) else ()
            candidate = _AuthorityRecordSpec(
                concrete_type=created,
                module=created.__module__,
                qualname=created.__qualname__,
                record_type=record_type,
                validator=validator,
                field_names=field_names,
            )
            existing = specs_by_discriminator.get(record_type)
            if existing is None:
                specs_by_discriminator[record_type] = candidate
            elif (
                "__slots__" not in existing.concrete_type.__dict__
                and "__slots__" in created.__dict__
                and is_dataclass(created)
                and existing.module == created.__module__
                and existing.concrete_type.__name__ == created.__name__
                and existing.validator is validator
            ):
                specs_by_type.pop(existing.concrete_type, None)
                specs_by_discriminator[record_type] = candidate
            else:
                raise TypeError(f"authority record discriminator {record_type!r} is already sealed")
            specs_by_type[created] = candidate
            return created

        def __call__(cls, *args: Any, **kwargs: Any) -> Any:
            constructing = getattr(construction_state, "types", ())
            construction_state.types = (*constructing, cls)
            try:
                instance = super().__call__(*args, **kwargs)
            finally:
                construction_state.types = constructing
            spec = specs_by_type.get(cls)
            if spec is not None:
                instances[id(instance)] = (instance, None)
                instances[id(instance)] = (instance, payload_fingerprint(instance, spec))
            return instance

        def __setattr__(cls, name: str, value: Any) -> None:
            if name in sealed_serializer_names | {
                "EXECUTION_AUTHORITY",
                "RECORD_TYPE",
                "SCHEMA_VERSION",
                "execution_authority",
                "phase",
            }:
                raise TypeError(f"{name} is sealed by the dormant authority hard gate")
            super().__setattr__(name, value)

    def require_spec(record: object) -> _AuthorityRecordSpec:
        if authority_base is None or not isinstance(record, authority_base):
            raise TypeError("value must be a sealed AuthorityRecord")
        record_class = type(record)
        spec = specs_by_type.get(record_class)
        if spec is None or spec.concrete_type is not record_class:
            raise TypeError("authority record type is outside the sealed Phase-0 registry")
        if (spec.module, spec.qualname, spec.record_type) not in supported_records:
            raise TypeError("authority record type is not a supported Phase-0 schema")
        if record_class.__bases__ != (authority_base,) or not is_dataclass(record):
            raise TypeError("authority record type is outside the sealed Phase-0 boundary")
        if hasattr(record, "__dict__"):
            raise TypeError("authority record instances cannot expose a mutable attribute dictionary")
        dataclass_parameters = getattr(record_class, "__dataclass_params__", None)
        if dataclass_parameters is None or not dataclass_parameters.frozen:
            raise TypeError("authority record types must be frozen dataclasses")
        if record_class.__dict__.get("RECORD_TYPE") != spec.record_type:
            raise TypeError("authority record schema discriminator changed after sealing")
        if record_class.__dict__.get("__post_init__") is not spec.validator:
            raise TypeError("authority record validator changed after sealing")
        if set(record_class.__dict__) & (
            sealed_serializer_names | {"SCHEMA_VERSION", "execution_authority", "phase"}
        ):
            raise TypeError("authority record serializer boundary changed after sealing")
        actual_field_names = tuple(field.name for field in fields(record))
        if actual_field_names != spec.field_names:
            raise TypeError("authority record field schema changed after sealing")
        collisions = set(spec.field_names) & reserved_fields
        if collisions:
            raise ContractValidationError(
                f"authority record fields collide with the sealed envelope: {sorted(collisions)!r}"
            )
        issued = instances.get(id(record))
        if issued is None:
            constructing = getattr(construction_state, "types", ())
            if record_class not in constructing:
                raise TypeError("authority record was not issued by its sealed constructor")
            instances[id(record)] = (record, None)
            issued = (record, None)
        if issued[0] is not record:
            raise TypeError("authority record issuance identity is invalid")
        if issued[1] is not None and issued[1] != payload_fingerprint(record, spec):
            raise ContractValidationError("authority record changed after sealed construction")
        validating = getattr(validation_state, "record_ids", set())
        if id(record) not in validating:
            validation_state.record_ids = {*validating, id(record)}
            try:
                spec.validator(record)
            finally:
                validation_state.record_ids = validating
        return _AuthorityRecordSpec(
            concrete_type=spec.concrete_type,
            module=spec.module,
            qualname=spec.qualname,
            record_type=spec.record_type,
            validator=spec.validator,
            field_names=spec.field_names,
        )

    def envelope_record(
        *,
        record_type: str,
        schema_version: int,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        safe_identifier(record_type, "record_type")
        positive_integer(schema_version, "schema_version")
        if not isinstance(payload, Mapping):
            raise TypeError("authority payload must be a mapping")
        if any(not isinstance(key, str) for key in payload):
            raise TypeError("authority payload keys must be strings")
        collisions = set(payload) & reserved_fields
        if collisions:
            raise ContractValidationError(
                f"authority payload collides with reserved envelope fields: {sorted(collisions)!r}"
            )
        return {
            "authority_metadata": {
                "execution_authority": False,
                "phase": phase_value,
            },
            "payload": {key: encode(payload[key]) for key in sorted(payload)},
            "record_type": record_type,
            "schema_version": schema_version,
        }

    def validate_envelope(
        raw: object,
        *,
        record_type: str,
        schema_version: int,
    ) -> dict[str, Any]:
        """Validate the closed discriminator/hard-gate envelope and return its payload."""

        if not isinstance(raw, dict) or set(raw) != envelope_keys:
            raise ContractValidationError("authority record envelope fields do not match schema V3")
        metadata = raw.get("authority_metadata")
        if metadata != {"execution_authority": False, "phase": phase_value}:
            raise ContractValidationError("authority record is not dormant")
        if raw.get("record_type") != record_type or raw.get("schema_version") != schema_version:
            raise ContractValidationError("authority record schema identity mismatch")
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise ContractValidationError("authority record payload must be an object")
        collisions = set(payload) & reserved_fields
        if collisions:
            raise ContractValidationError(
                "authority record payload collides with reserved envelope fields"
            )
        return payload

    def seal_record(record: Any) -> dict[str, Any]:
        """Serialize through the closed codec, never through virtual instance methods."""

        spec = require_spec(record)
        payload = {field_name: getattr(record, field_name) for field_name in spec.field_names}
        return envelope_record(
            record_type=spec.record_type,
            schema_version=authority_base.SCHEMA_VERSION,
            payload=payload,
        )

    def record_bytes(record: Any) -> bytes:
        return canonical_bytes(seal_record(record))

    def record_digest(record: Any) -> str:
        spec = require_spec(record)
        return typed_sha256(f"VOOL_{spec.record_type.upper()}_V3", record_bytes(record))

    def build_envelope_view(record: Any) -> Any:
        spec = require_spec(record)
        payload = {field_name: getattr(record, field_name) for field_name in spec.field_names}
        return AuthorityRecordEnvelope(
            record_type=spec.record_type,
            schema_version=authority_base.SCHEMA_VERSION,
            payload=AuthorityRecordPayload.from_mapping(payload),
        )

    @dataclass(frozen=True, slots=True)
    class DormantAuthorityMetadata:
        """Closed hard-gate metadata that caller-controlled payloads cannot populate."""

        def to_record(self) -> dict[str, object]:
            return {"execution_authority": False, "phase": phase_value}

        def __init_subclass__(cls, **kwargs: Any) -> None:
            raise TypeError("DormantAuthorityMetadata is a final authority value")

    @dataclass(frozen=True, slots=True)
    class AuthorityRecordPayload:
        """Typed payload partition for an authority envelope."""

        items: tuple[tuple[str, Any], ...]

        def __post_init__(self) -> None:
            if not isinstance(self.items, tuple) or not all(
                isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
                for item in self.items
            ):
                raise TypeError("authority payload items must be immutable string-key pairs")
            keys = tuple(item[0] for item in self.items)
            if len(keys) != len(set(keys)):
                raise ContractValidationError("authority payload contains duplicate fields")
            collisions = set(keys) & reserved_fields
            if collisions:
                raise ContractValidationError(
                    f"authority payload collides with reserved envelope fields: {sorted(collisions)!r}"
                )

        @classmethod
        def from_mapping(cls, payload: Mapping[str, Any]) -> Any:
            if not isinstance(payload, Mapping):
                raise TypeError("authority payload must be a mapping")
            if any(not isinstance(key, str) for key in payload):
                raise TypeError("authority payload keys must be strings")
            return cls(tuple(sorted(payload.items())))

        def to_record(self) -> dict[str, Any]:
            return {key: encode(value) for key, value in self.items}

        def __init_subclass__(cls, **kwargs: Any) -> None:
            raise TypeError("AuthorityRecordPayload is a final authority value")

    @dataclass(frozen=True, slots=True)
    class AuthorityRecordEnvelope:
        """Canonical dormant envelope with metadata structurally outside record payload."""

        record_type: str
        schema_version: int
        payload: Any
        authority_metadata: Any = DormantAuthorityMetadata()

        def __post_init__(self) -> None:
            safe_identifier(self.record_type, "record_type")
            positive_integer(self.schema_version, "schema_version")
            if type(self.payload) is not AuthorityRecordPayload:
                raise TypeError("payload must be AuthorityRecordPayload")
            if type(self.authority_metadata) is not DormantAuthorityMetadata:
                raise TypeError("authority_metadata must be DormantAuthorityMetadata")

        def to_record(self) -> dict[str, Any]:
            return {
                "authority_metadata": {"execution_authority": False, "phase": phase_value},
                "payload": AuthorityRecordPayload.to_record(self.payload),
                "record_type": self.record_type,
                "schema_version": self.schema_version,
            }

        def __init_subclass__(cls, **kwargs: Any) -> None:
            raise TypeError("AuthorityRecordEnvelope is a final authority value")

    return (
        AuthorityRecordMeta,
        require_spec,
        encode,
        envelope_record,
        validate_envelope,
        seal_record,
        record_bytes,
        record_digest,
        build_envelope_view,
        DormantAuthorityMetadata,
        AuthorityRecordPayload,
        AuthorityRecordEnvelope,
    )


(
    _AuthorityRecordMeta,
    _require_authority_record_spec,
    encode_value,
    dormant_envelope_record,
    validate_dormant_envelope,
    sealed_authority_record,
    sealed_authority_record_bytes,
    sealed_authority_record_digest,
    _build_authority_envelope_view,
    DormantAuthorityMetadata,
    AuthorityRecordPayload,
    AuthorityRecordEnvelope,
) = _build_serialization_boundary(
    _require_owned_identity_text,
    _canonical_counter_value,
    _canonical_enum_value,
    _canonical_enum_value(AuthorityPhase.DORMANT_PHASE0),
    _ENVELOPE_KEYS,
    _RESERVED_AUTHORITY_FIELDS,
    _SEALED_SERIALIZER_NAMES,
    _SUPPORTED_AUTHORITY_RECORDS,
    require_identifier,
    require_positive,
)


def canonical_enum_value(member: Enum) -> Any:
    """Public wrapper over the enum value captured when the enum was sealed."""

    return _canonical_enum_value(member)


class AuthorityRecord(metaclass=_AuthorityRecordMeta):
    """Base for records that are mechanically incapable of claiming execution authority."""

    RECORD_TYPE: ClassVar[str]
    SCHEMA_VERSION: ClassVar[int] = 3
    phase: ClassVar[AuthorityPhase] = AuthorityPhase.DORMANT_PHASE0
    execution_authority: ClassVar[bool] = False
    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @staticmethod
    def require_closed(record: object) -> AuthorityRecord:
        """Reject open, inherited, or construction-bypassed record implementations."""
        return require_closed_authority_record(record)

    def envelope(self) -> AuthorityRecordEnvelope:
        return _build_authority_envelope_view(self)

    def to_record(self) -> dict[str, Any]:
        return sealed_authority_record(self)

    def canonical_bytes(self) -> bytes:
        return sealed_authority_record_bytes(self)

    def digest(self) -> str:
        return sealed_authority_record_digest(self)


def _sealed_authority_record_spec(record: object) -> _AuthorityRecordSpec:
    return _require_authority_record_spec(record)


def require_closed_authority_record(record: object) -> AuthorityRecord:
    """Validate exact registered type, fixed schema, and current field invariants."""

    _sealed_authority_record_spec(record)
    return record  # type: ignore[return-value]


__all__ = [
    "DIGEST_RE",
    "AuthorityPhase",
    "AuthorityRecord",
    "AuthorityRecordEnvelope",
    "AuthorityRecordPayload",
    "ContractValidationError",
    "DormantAuthorityMetadata",
    "canonical_enum_value",
    "dormant_envelope_record",
    "encode_value",
    "require_closed_authority_record",
    "require_digest",
    "require_identifier",
    "require_nonnegative",
    "require_positive",
    "sealed_authority_record",
    "sealed_authority_record_bytes",
    "sealed_authority_record_digest",
    "validate_dormant_envelope",
]
