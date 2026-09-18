"""Dormant authority schemas that freeze V3 identities, proofs, and budgets."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from enum import IntFlag
from typing import Any, ClassVar, NamedTuple

from core.enum_compat import StrEnum
from core.workspace_authority_v3.base import (
    AuthorityRecord,
    ContractValidationError,
    _canonical_enum_value,
    dormant_envelope_record,
    require_closed_authority_record,
    require_digest,
    require_identifier,
    require_nonnegative,
    require_positive,
    sealed_authority_record,
    sealed_authority_record_digest,
    validate_dormant_envelope,
)
from core.workspace_authority_v3.canonical import (
    MAX_SIGNED_64,
    canonical_bytes,
    constant_time_hex_digest_equal,
    parse_strict_json,
    typed_hmac_sha256,
    typed_sha256,
)
from core.workspace_authority_v3.identity import (
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    MutationId,
    OperationId,
    RecoveryDecisionId,
    RuntimeHomeId,
    WorkspaceId,
    _authority_domain_ring_anchor,
    _canonical_counter_value,
    _issue_authenticated_identity_decode,
    _require_owned_identity_text,
    require_exact_authority_value,
    require_identity_context,
    require_owned_identity,
    seal_canonical_enum,
)
from core.workspace_authority_v3.lifecycle import HeadState

_SAFE_TEXT_RE = re.compile(r"[^\x00-\x1f\x7f]{1,256}\Z")


def _safe_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not _SAFE_TEXT_RE.fullmatch(value):
        raise ContractValidationError(f"{field_name} must be bounded printable text without surrounding whitespace")
    return value


def _checked_sum(values: tuple[int, ...], field_name: str) -> int:
    total = 0
    for value in values:
        require_nonnegative(value, field_name)
        if total > MAX_SIGNED_64 - value:
            raise ContractValidationError(f"{field_name} overflows signed-64 reservation mathematics")
        total += value
    return total


@dataclass(frozen=True, slots=True)
class EnrollmentClaimV3(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "EnrollmentClaimV3"

    authority_domain_id: AuthorityDomainId
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    enrollment_operation_id: EnrollmentOperationId
    incarnation_nonce: str
    persistent_volume_identity_digest: str
    root_object_identity_digest: str
    runtime_home_id: RuntimeHomeId
    authority_key_id: str
    key_generation: int
    algorithm: str
    algorithm_version: int
    claim_digest: str
    claim_mac: str

    def __post_init__(self) -> None:
        typed_identities = (
            (self.authority_domain_id, AuthorityDomainId, "authority_domain_id"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.enrollment_operation_id, EnrollmentOperationId, "enrollment_operation_id"),
            (self.runtime_home_id, RuntimeHomeId, "runtime_home_id"),
        )
        for value, expected_type, field_name in typed_identities:
            require_exact_authority_value(value, expected_type, field_name)
        require_identity_context(
            self.workspace_id,
            WorkspaceId,
            authority_domain_id=self.authority_domain_id,
            enrollment_operation_id=self.enrollment_operation_id,
        )
        require_digest(self.incarnation_nonce, "incarnation_nonce")
        require_digest(self.persistent_volume_identity_digest, "persistent_volume_identity_digest")
        require_digest(self.root_object_identity_digest, "root_object_identity_digest")
        require_identifier(self.authority_key_id, "authority_key_id")
        require_positive(self.key_generation, "key_generation")
        if (
            self.algorithm != "HMAC-SHA256"
            or isinstance(self.algorithm_version, bool)
            or self.algorithm_version != 1
        ):
            raise ContractValidationError("Phase-0 enrollment claims require HMAC-SHA256 version 1")
        require_digest(self.claim_digest, "claim_digest")
        require_digest(self.claim_mac, "claim_mac")
        if not constant_time_hex_digest_equal(self.claim_digest, self.expected_claim_digest()):
            raise ContractValidationError("claim_digest does not bind the canonical enrollment claim")

    def unsigned_record(self) -> dict[str, Any]:
        record = sealed_authority_record(self)
        del record["payload"]["claim_digest"]
        del record["payload"]["claim_mac"]
        return record

    def expected_claim_digest(self) -> str:
        return typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", canonical_bytes(self.unsigned_record()))

    def verify_mac(self, key: VerificationKeyCapability) -> bool:
        if type(key) is not VerificationKeyCapability:
            raise TypeError("enrollment verification requires VerificationKeyCapability")
        reference = require_verification_key_capability(
            key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
        )
        if reference.key_id != self.authority_key_id or reference.generation != self.key_generation:
            return False
        # Same rule as authenticated decode, at the other authentication entry point on
        # this record: a key authority answers only for the domain it is anchored to.
        entitled_domain = require_verification_domain_authority(
            key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
        )
        if _require_owned_identity_text(self.authority_domain_id, AuthorityDomainId) != entitled_domain:
            return False
        return verify_with_key_capability(
            key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
            message=self.claim_digest.encode("ascii"),
            supplied_mac=self.claim_mac,
        )

    @classmethod
    def issue(
        cls,
        *,
        authority_domain_id: AuthorityDomainId,
        workspace_id: WorkspaceId,
        incarnation_id: IncarnationId,
        enrollment_operation_id: EnrollmentOperationId,
        incarnation_nonce: str,
        persistent_volume_identity_digest: str,
        root_object_identity_digest: str,
        runtime_home_id: RuntimeHomeId,
        signing_key: SigningKeyCapability,
    ) -> EnrollmentClaimV3:
        if type(signing_key) is not SigningKeyCapability:
            raise TypeError("enrollment issuance requires SigningKeyCapability")
        require_owned_identity(authority_domain_id, AuthorityDomainId)
        require_identity_context(
            workspace_id,
            WorkspaceId,
            authority_domain_id=authority_domain_id,
            enrollment_operation_id=enrollment_operation_id,
        )
        require_owned_identity(incarnation_id, IncarnationId)
        require_owned_identity(enrollment_operation_id, EnrollmentOperationId)
        require_owned_identity(runtime_home_id, RuntimeHomeId)
        signing_reference = require_signing_key_capability(
            signing_key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
        )
        authority_key_id = signing_reference.key_id
        key_generation = signing_reference.generation
        unsigned = dormant_envelope_record(
            record_type=cls.RECORD_TYPE,
            schema_version=cls.SCHEMA_VERSION,
            payload={
                "algorithm": "HMAC-SHA256",
                "algorithm_version": 1,
                "authority_domain_id": _require_owned_identity_text(authority_domain_id, AuthorityDomainId),
                "authority_key_id": authority_key_id,
                "enrollment_operation_id": _require_owned_identity_text(
                    enrollment_operation_id,
                    EnrollmentOperationId,
                ),
                "incarnation_id": _require_owned_identity_text(incarnation_id, IncarnationId),
                "incarnation_nonce": incarnation_nonce,
                "key_generation": key_generation,
                "persistent_volume_identity_digest": persistent_volume_identity_digest,
                "root_object_identity_digest": root_object_identity_digest,
                "runtime_home_id": _require_owned_identity_text(runtime_home_id, RuntimeHomeId),
                "workspace_id": _require_owned_identity_text(workspace_id, WorkspaceId),
            },
        )
        claim_digest = typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", canonical_bytes(unsigned))
        claim_mac = sign_with_key_capability(
            signing_key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
            message=claim_digest.encode("ascii"),
        )
        return cls(
            authority_domain_id=authority_domain_id,
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            enrollment_operation_id=enrollment_operation_id,
            incarnation_nonce=incarnation_nonce,
            persistent_volume_identity_digest=persistent_volume_identity_digest,
            root_object_identity_digest=root_object_identity_digest,
            runtime_home_id=runtime_home_id,
            authority_key_id=authority_key_id,
            key_generation=key_generation,
            algorithm="HMAC-SHA256",
            algorithm_version=1,
            claim_digest=claim_digest,
            claim_mac=claim_mac,
        )

    @classmethod
    def from_json(
        cls,
        payload: str | bytes,
        *,
        verification_key: VerificationKeyCapability,
    ) -> EnrollmentClaimV3:
        raw = parse_strict_json(payload)
        if not isinstance(raw, dict) or canonical_bytes(raw) != (payload.encode("utf-8") if isinstance(payload, str) else payload):
            raise ContractValidationError("enrollment claim must use exact canonical JSON")
        expected_keys = {
            "algorithm",
            "algorithm_version",
            "authority_domain_id",
            "authority_key_id",
            "claim_digest",
            "claim_mac",
            "enrollment_operation_id",
            "incarnation_id",
            "incarnation_nonce",
            "key_generation",
            "persistent_volume_identity_digest",
            "root_object_identity_digest",
            "runtime_home_id",
            "workspace_id",
        }
        record_payload = validate_dormant_envelope(
            raw,
            record_type=cls.RECORD_TYPE,
            schema_version=cls.SCHEMA_VERSION,
        )
        if set(record_payload) != expected_keys:
            raise ContractValidationError("enrollment claim fields do not match schema V3")
        if type(verification_key) is not VerificationKeyCapability:
            raise TypeError("enrollment decode requires VerificationKeyCapability")
        verification_reference = require_verification_key_capability(
            verification_key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
        )
        if (
            verification_reference.key_id != record_payload["authority_key_id"]
            or verification_reference.generation != record_payload["key_generation"]
        ):
            raise ContractValidationError("enrollment claim verification key mismatch")
        unsigned = dict(raw)
        unsigned["payload"] = dict(record_payload)
        supplied_digest = unsigned["payload"].pop("claim_digest")
        supplied_mac = unsigned["payload"].pop("claim_mac")
        expected_digest = typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", canonical_bytes(unsigned))
        if not constant_time_hex_digest_equal(supplied_digest, expected_digest):
            raise ContractValidationError("claim_digest does not bind the canonical enrollment claim")
        if not verify_with_key_capability(
            verification_key,
            expected_family=KeyFamily.CLAIM_ENROLLMENT,
            domain="VOOL_ENROLLMENT_CLAIM_MAC_V3",
            message=supplied_digest.encode("ascii"),
            supplied_mac=supplied_mac,
        ):
            raise ContractValidationError("enrollment claim authentication failed")
        authenticated_source = canonical_bytes(unsigned)

        def decode_identity(field_name: str, identity_type: type) -> object:
            proof = _issue_authenticated_identity_decode(
                verification_key=verification_key,
                source_record=authenticated_source,
                supplied_mac=supplied_mac,
                record_type=cls.RECORD_TYPE,
                source_field=field_name,
                identity_type=identity_type,
            )
            return identity_type._from_trusted_storage(proof)

        return cls(
            authority_domain_id=decode_identity("authority_domain_id", AuthorityDomainId),
            workspace_id=decode_identity("workspace_id", WorkspaceId),
            incarnation_id=decode_identity("incarnation_id", IncarnationId),
            enrollment_operation_id=decode_identity("enrollment_operation_id", EnrollmentOperationId),
            incarnation_nonce=record_payload["incarnation_nonce"],
            persistent_volume_identity_digest=record_payload["persistent_volume_identity_digest"],
            root_object_identity_digest=record_payload["root_object_identity_digest"],
            runtime_home_id=decode_identity("runtime_home_id", RuntimeHomeId),
            authority_key_id=record_payload["authority_key_id"],
            key_generation=record_payload["key_generation"],
            algorithm=record_payload["algorithm"],
            algorithm_version=record_payload["algorithm_version"],
            claim_digest=record_payload["claim_digest"],
            claim_mac=record_payload["claim_mac"],
        )


class KeyFamily(StrEnum):
    CLAIM_ENROLLMENT = "CLAIM_ENROLLMENT"
    JOURNAL_EFFECT_RECEIPT = "JOURNAL_EFFECT_RECEIPT"
    BACKUP_MANIFEST = "BACKUP_MANIFEST"
    CURSOR = "CURSOR"
    WORKER_SNAPSHOT = "WORKER_SNAPSHOT"


seal_canonical_enum(KeyFamily)


class KeyFamilyPhase0Status(StrEnum):
    DORMANT_TYPED_RECORD = "DORMANT_TYPED_RECORD"
    DEFERRED_PHASE0 = "DEFERRED_PHASE0"


seal_canonical_enum(KeyFamilyPhase0Status)


# Informational alias only.  Trusted code reads the sealed status below, so rebinding
# this module attribute cannot re-open a deferred key family.
JOURNAL_EFFECT_RECEIPT_PHASE0_STATUS = KeyFamilyPhase0Status.DEFERRED_PHASE0


class KeyState(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    VERIFY_ONLY = "VERIFY_ONLY"
    RETIRED = "RETIRED"
    REVOKED_COMPROMISED = "REVOKED_COMPROMISED"
    LOST = "LOST"


seal_canonical_enum(KeyState)


def _build_key_structure_specification() -> tuple[Any, ...]:
    """Own the structural key-ring facts that must not move under ordinary substitution.

    The same root as the key-lifecycle repair, one layer earlier.  ``KeyRingMetadata``
    accepts or rejects a ring *shape* -- at most one ACTIVE generation per family, and no
    ACTIVE generation at all for the family deferred in Phase 0 -- and it used to decide
    that by reading ``KeyState.ACTIVE`` and ``KeyFamily.JOURNAL_EFFECT_RECEIPT`` live on
    every construction.  ``type.__setattr__`` redirects either name past ``EnumMeta``'s
    member-reassignment guard, so a ring holding two genuinely ACTIVE generations, or a
    genuinely ACTIVE deferred-family generation, would have been accepted as well-formed
    and then installed as the authority's lifecycle truth.

    ``key_family_phase0_status`` had the same shape twice over: a live class-attribute
    lookup *and* a module-level status constant, both reachable from the two places the
    closed key boundary gates a deferred family.

    Both now read constants captured here, from the enums as just defined, before any
    caller-reachable code has run.
    """

    active_state = KeyState.ACTIVE
    families = tuple(KeyFamily)
    deferred_family = KeyFamily.JOURNAL_EFFECT_RECEIPT
    deferred_status = KeyFamilyPhase0Status.DEFERRED_PHASE0
    dormant_status = KeyFamilyPhase0Status.DORMANT_TYPED_RECORD

    def phase0_status(family: object) -> KeyFamilyPhase0Status:
        if type(family) is not KeyFamily:
            raise TypeError("key family status requires an exact KeyFamily")
        return deferred_status if family is deferred_family else dormant_status

    def require_ring_structure(generations: tuple[Any, ...]) -> None:
        read = _read_key_record_slot
        for family in families:
            active = [
                item
                for item in generations
                if read(KeyGenerationMetadata, "family", item) is family
                and read(KeyGenerationMetadata, "state", item) is active_state
            ]
            if len(active) > 1:
                raise ContractValidationError(
                    f"{_canonical_enum_value(family)} may have at most one ACTIVE generation"
                )
            if family is deferred_family and active:
                raise ContractValidationError(
                    "JOURNAL_EFFECT_RECEIPT is explicitly deferred in dormant Phase 0"
                )

    return phase0_status, require_ring_structure


(
    _key_family_phase0_status,
    _require_key_ring_structure,
) = _build_key_structure_specification()


def key_family_phase0_status(family: KeyFamily) -> KeyFamilyPhase0Status:
    """Public wrapper only; trusted code calls the sealed status captured above."""

    return _key_family_phase0_status(family)


@dataclass(frozen=True, slots=True)
class KeyGenerationMetadata(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "KeyGenerationMetadata"

    family: KeyFamily
    key_id: str
    generation: int
    state: KeyState
    algorithm: str
    algorithm_version: int

    def __post_init__(self) -> None:
        # Validate what is stored, through the sealed descriptors: a replaced field
        # descriptor must not be able to show one value here and another to a
        # lifecycle decision.
        read = _read_key_record_slot
        family = read(KeyGenerationMetadata, "family", self)
        state = read(KeyGenerationMetadata, "state", self)
        if type(family) is not KeyFamily or type(state) is not KeyState:
            raise TypeError("key metadata requires exact family and state values")
        require_identifier(read(KeyGenerationMetadata, "key_id", self), "key_id")
        require_positive(read(KeyGenerationMetadata, "generation", self), "generation")
        require_identifier(read(KeyGenerationMetadata, "algorithm", self), "algorithm")
        require_positive(
            read(KeyGenerationMetadata, "algorithm_version", self),
            "algorithm_version",
        )


@dataclass(frozen=True, slots=True)
class KeyRingMetadata(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "KeyRingMetadata"

    generations: tuple[KeyGenerationMetadata, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.generations, tuple) or not all(
            type(item) is KeyGenerationMetadata for item in self.generations
        ):
            raise TypeError("generations must be an immutable tuple of KeyGenerationMetadata")
        seen_generation: set[tuple[KeyFamily, int]] = set()
        seen_key_ids: set[str] = set()
        for item in self.generations:
            require_closed_authority_record(item)
            item_family = _read_key_record_slot(KeyGenerationMetadata, "family", item)
            item_generation = _read_key_record_slot(KeyGenerationMetadata, "generation", item)
            item_key_id = _read_key_record_slot(KeyGenerationMetadata, "key_id", item)
            generation_key = (item_family, item_generation)
            if generation_key in seen_generation or item_key_id in seen_key_ids:
                raise ContractValidationError("key metadata contains a duplicate generation or key ID")
            seen_generation.add(generation_key)
            seen_key_ids.add(item_key_id)
        object.__setattr__(
            self,
            "generations",
            tuple(
                sorted(
                    self.generations,
                    key=lambda item: (
                        _canonical_enum_value(
                            _read_key_record_slot(KeyGenerationMetadata, "family", item)
                        ),
                        _read_key_record_slot(KeyGenerationMetadata, "generation", item),
                    ),
                )
            ),
        )
        _require_key_ring_structure(self.generations)

    def find(self, family: KeyFamily, generation: int) -> KeyGenerationMetadata | None:
        read = _read_key_record_slot
        return next(
            (
                item
                for item in self.generations
                if read(KeyGenerationMetadata, "family", item) is family
                and read(KeyGenerationMetadata, "generation", item) == generation
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class KeyReference:
    family: KeyFamily
    key_id: str
    generation: int

    def __post_init__(self) -> None:
        if type(self.family) is not KeyFamily:
            raise TypeError("key reference family must be an exact KeyFamily")
        require_identifier(self.key_id, "key_id")
        require_positive(self.generation, "generation")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KeyReference is final")


def _build_key_record_readers() -> tuple[Any, ...]:
    """Capture the key-record field descriptors once, before any caller code can run.

    Key lifecycle is decided from these reads.  A dataclass field descriptor stays
    replaceable after class definition, so a property installed later could otherwise
    answer one value to sealed-record validation and another to a permission decision.
    """

    slots: dict[tuple[type, str], Any] = {
        (KeyGenerationMetadata, name): KeyGenerationMetadata.__dict__[name]
        for name in ("family", "key_id", "generation", "state", "algorithm", "algorithm_version")
    }
    slots.update(
        {
            (KeyReference, name): KeyReference.__dict__[name]
            for name in ("family", "key_id", "generation")
        }
    )

    def seal(owner: type, names: tuple[str, ...]) -> None:
        """Seal a key-record type defined later in this module (once, at import)."""

        for name in names:
            if (owner, name) in slots:
                raise TypeError(f"{owner.__name__}.{name} is already a sealed key-record field")
            slots[(owner, name)] = owner.__dict__[name]

    def read(owner: type, name: str, instance: object) -> Any:
        descriptor = slots.get((owner, name))
        if descriptor is None:
            raise TypeError(f"{owner.__name__}.{name} is not a sealed key-record field")
        if type(instance) is not owner:
            raise TypeError(f"sealed key-record read requires an exact {owner.__name__}")
        return descriptor.__get__(instance, owner)

    return read, seal


(_read_key_record_slot, _seal_key_record_slots) = _build_key_record_readers()



class _KeyUseCapability:
    """Passive key-use handle; every authoritative fact lives in the closed key boundary."""

    __slots__ = ("family", "generation", "key_id")
    _USE: ClassVar[str]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("key-use capabilities are minted only by DormantKeyAuthority")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("key-use capabilities are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("key-use capabilities are immutable")

    @property
    def state(self) -> KeyState:
        return _key_capability_state(self)

    def require_family(self, expected: KeyFamily) -> None:
        _require_key_capability_use(self, use=type(self)._USE, expected_family=expected)

    def owned_by(self, authority: DormantKeyAuthority) -> bool:
        return _key_capability_owned_by(self, authority)


class SigningKeyCapability(_KeyUseCapability):
    __slots__ = ()
    _USE = "sign"

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("SigningKeyCapability is final")

    def sign(self, *, expected_family: KeyFamily, domain: str, message: bytes) -> str:
        return sign_with_key_capability(
            self,
            expected_family=expected_family,
            domain=domain,
            message=message,
        )


class VerificationKeyCapability(_KeyUseCapability):
    __slots__ = ()
    _USE = "verify"

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("VerificationKeyCapability is final")

    def verify(
        self,
        *,
        expected_family: KeyFamily,
        domain: str,
        message: bytes,
        supplied_mac: str,
    ) -> bool:
        return verify_with_key_capability(
            self,
            expected_family=expected_family,
            domain=domain,
            message=message,
            supplied_mac=supplied_mac,
        )


class DormantKeyAuthority:
    """Single non-executing owner of key material, lifecycle truth, and issued leases.

    Authoritative key state lives in the closed key-authority boundary, never on this
    instance: the owner object is an opaque handle with no caller-writable slots, one
    logical ring (owned WorkspaceId) admits exactly one authority owner, and every
    authenticated use binds that ring context.

    A ring is also anchored to exactly one authority domain.  ``authority_domain`` takes
    an owned ``AuthorityDomainId`` (enrollment), canonical stored authority-domain text
    (restart/reconstruction), or ``None`` to inherit the ring workspace's own recorded
    enrollment domain.  The anchor is captured once, is immutable for the life of the
    ring, and is what an authenticated identity decode checks a record's
    ``authority_domain_id`` against.
    """

    __slots__ = ()
    execution_authority = False

    def __init__(
        self,
        metadata: KeyRingMetadata,
        key_materials: Mapping[KeyReference, bytes],
        *,
        workspace_id: WorkspaceId,
        authority_domain: AuthorityDomainId | str | None = None,
    ) -> None:
        _issue_key_authority_owner(
            self,
            metadata=metadata,
            key_materials=key_materials,
            workspace_id=workspace_id,
            authority_domain=authority_domain,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DormantKeyAuthority is final")

    @property
    def metadata(self) -> KeyRingMetadata:
        return _key_authority_metadata(self)

    @property
    def workspace_id(self) -> WorkspaceId:
        return _key_authority_workspace(self)

    @property
    def authority_domain(self) -> str:
        """Canonical text of the one authority domain this ring may authenticate."""

        return _key_authority_domain(self)

    def signing_capability(
        self,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> SigningKeyCapability:
        return _issue_signing_key_capability(
            self,
            family=family,
            generation=generation,
            key_id=key_id,
        )

    def verification_capability(
        self,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> VerificationKeyCapability:
        return _issue_verification_key_capability(
            self,
            family=family,
            generation=generation,
            key_id=key_id,
        )

    def install_metadata(
        self,
        metadata: KeyRingMetadata,
        *,
        key_materials: Mapping[KeyReference, bytes] | None = None,
    ) -> None:
        """Atomically install a forward-only lifecycle snapshot for this same owner."""

        _install_key_ring_metadata(self, metadata, key_materials=key_materials)

    def owns_capability(self, capability: object) -> bool:
        return _key_capability_owned_by(capability, self)


def _build_key_authority_boundary(
    *,
    authority_type: type,
    capability_base_type: type,
    signing_type: type,
    verification_type: type,
    family_phase0_status: Any,
    domain_ring_anchor: Any,
) -> tuple[Any, ...]:
    """Keep authoritative key state, issuance, and use outside module-reachable data."""

    boundary_lock = threading.RLock()

    # Deferred-family gating reads the sealed status primitive and the sealed status
    # member captured here, not the module-level wrapper or a live class attribute.
    _deferred_phase0_status = KeyFamilyPhase0Status.DEFERRED_PHASE0

    # Sealed key-lifecycle specification: captured once, from the just-defined KeyState
    # class, before any caller-reachable code can run.  Every permission decision below
    # reads these closure-private constants, never a live ``KeyState.ACTIVE``-style class
    # attribute lookup, so replacing a KeyState class attribute after this boundary is
    # built cannot change which lifecycle state is treated as ACTIVE or VERIFY_ONLY.
    _active_state = KeyState.ACTIVE
    _signable_states = frozenset((KeyState.ACTIVE,))
    _verifiable_states = frozenset((KeyState.ACTIVE, KeyState.VERIFY_ONLY))
    _key_state_transitions: dict[KeyState, frozenset[KeyState]] = {
        KeyState.PENDING: frozenset({KeyState.ACTIVE, KeyState.REVOKED_COMPROMISED, KeyState.LOST}),
        KeyState.ACTIVE: frozenset(
            {KeyState.VERIFY_ONLY, KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST}
        ),
        KeyState.VERIFY_ONLY: frozenset(
            {KeyState.RETIRED, KeyState.REVOKED_COMPROMISED, KeyState.LOST}
        ),
        KeyState.RETIRED: frozenset({KeyState.REVOKED_COMPROMISED, KeyState.LOST}),
        KeyState.REVOKED_COMPROMISED: frozenset(),
        KeyState.LOST: frozenset(),
    }

    class _KeyAuthorityOwnerState:
        __slots__ = (
            "materials",
            "metadata",
            "owner",
            "ring_authority_domain",
            "ring_key",
            "ring_workspace_id",
        )

        def __init__(
            self,
            owner: object,
            ring_key: str,
            ring_workspace_id: WorkspaceId,
            ring_authority_domain: str,
        ) -> None:
            self.materials: dict[KeyReference, bytes] = {}
            self.metadata: KeyRingMetadata | None = None
            self.owner = owner
            self.ring_key = ring_key
            self.ring_workspace_id = ring_workspace_id
            self.ring_authority_domain = ring_authority_domain

    ring_owners: dict[str, object] = {}
    owner_states: dict[int, _KeyAuthorityOwnerState] = {}
    issued_capabilities: dict[int, tuple[object, _KeyAuthorityOwnerState, str, KeyReference]] = {}
    mirror_slots = {
        name: getattr(capability_base_type, name) for name in ("family", "generation", "key_id")
    }

    # Sealed key-record reads, captured into this closure at boundary construction.
    # Every lifecycle and permission decision below resolves key metadata and key
    # references through these, never through a live attribute lookup.
    _read_key_slot = _read_key_record_slot
    _GENERATION_FIELDS = (
        "family",
        "key_id",
        "generation",
        "state",
        "algorithm",
        "algorithm_version",
    )
    _REFERENCE_FIELDS = ("family", "key_id", "generation")

    class _SealedGeneration(NamedTuple):
        family: KeyFamily
        key_id: str
        generation: int
        state: KeyState
        algorithm: str
        algorithm_version: int

    class _SealedReference(NamedTuple):
        family: KeyFamily
        key_id: str
        generation: int

    def sealed_generation(item: object) -> _SealedGeneration:
        """One generation's authority-bearing fields, read from the sealed descriptors."""

        return _SealedGeneration(
            *(
                _read_key_slot(KeyGenerationMetadata, name, item)
                for name in _GENERATION_FIELDS
            )
        )

    def sealed_reference(reference: object) -> _SealedReference:
        """One key reference's binding fields, read from the sealed descriptors."""

        return _SealedReference(
            *(_read_key_slot(KeyReference, name, reference) for name in _REFERENCE_FIELDS)
        )

    def read_mirror(capability: object, name: str) -> object:
        return mirror_slots[name].__get__(capability, type(capability))

    def require_unchanged_mirrors(capability: object, reference: KeyReference) -> None:
        bound = sealed_reference(reference)
        try:
            unchanged = (
                read_mirror(capability, "family") is bound.family
                and read_mirror(capability, "key_id") == bound.key_id
                and read_mirror(capability, "generation") == bound.generation
            )
        except AttributeError:
            unchanged = False
        if not unchanged:
            raise TypeError("key-use capability changed after owner issuance")

    def owned_reference_for(
        metadata: KeyRingMetadata,
        reference: object,
        absence_message: str,
    ) -> KeyReference:
        if type(reference) is not KeyReference:
            raise TypeError("key material ownership requires exact KeyReference keys")
        KeyReference.__post_init__(reference)
        requested = sealed_reference(reference)
        owned = KeyReference(requested.family, requested.key_id, requested.generation)
        current = next(
            (
                item
                for item in metadata.generations
                if sealed_generation(item).family is requested.family
                and sealed_generation(item).generation == requested.generation
            ),
            None,
        )
        if current is None or sealed_generation(current).key_id != requested.key_id:
            raise ContractValidationError(absence_message)
        return owned

    def validated_material(material: object) -> bytes:
        if not isinstance(material, bytes) or len(material) < 32:
            raise ContractValidationError("key material must contain at least 256 bits")
        return bytes(material)

    def require_owner_state(owner: object) -> _KeyAuthorityOwnerState:
        if type(owner) is not authority_type:
            raise TypeError("key authority operations require the exact DormantKeyAuthority owner")
        state = owner_states.get(id(owner))
        if (
            state is None
            or state.owner is not owner
            or ring_owners.get(state.ring_key) is not owner
        ):
            raise TypeError("key authority owner was not issued by the closed key boundary")
        return state

    def generation_metadata(
        state: _KeyAuthorityOwnerState,
        family: KeyFamily,
        generation: int,
    ) -> KeyGenerationMetadata | None:
        require_closed_authority_record(state.metadata)
        return next(
            (
                item
                for item in state.metadata.generations
                if sealed_generation(item).family is family
                and sealed_generation(item).generation == generation
            ),
            None,
        )

    def resolve_reference(
        state: _KeyAuthorityOwnerState,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> KeyReference:
        reference = KeyReference(family, key_id, generation)
        bound = sealed_reference(reference)
        metadata = generation_metadata(state, bound.family, bound.generation)
        current = None if metadata is None else sealed_generation(metadata)
        if current is None or current.key_id != bound.key_id:
            raise ContractValidationError("key reference is not valid for requested use")
        if current.algorithm != "HMAC-SHA256" or current.algorithm_version != 1:
            raise ContractValidationError("Phase-0 key use requires HMAC-SHA256 version 1")
        if bound not in state.materials:
            raise ContractValidationError("key material is not owned for the requested reference")
        if family_phase0_status(bound.family) is _deferred_phase0_status:
            raise ContractValidationError("JOURNAL_EFFECT_RECEIPT signing is deferred in Phase 0")
        return reference

    def mint_capability(
        state: _KeyAuthorityOwnerState,
        capability_type: type,
        use: str,
        reference: KeyReference,
    ) -> object:
        bound = sealed_reference(reference)
        capability = object.__new__(capability_type)
        object.__setattr__(capability, "family", bound.family)
        object.__setattr__(capability, "generation", bound.generation)
        object.__setattr__(capability, "key_id", bound.key_id)
        issued_capabilities[id(capability)] = (capability, state, use, reference)
        return capability

    def issue_owner(
        owner: object,
        *,
        metadata: KeyRingMetadata,
        key_materials: Mapping[KeyReference, bytes],
        workspace_id: WorkspaceId,
        authority_domain: object,
    ) -> None:
        if type(owner) is not authority_type:
            raise TypeError("key authority issuance requires the exact DormantKeyAuthority type")
        if type(metadata) is not KeyRingMetadata:
            raise TypeError("key authority requires exact KeyRingMetadata")
        require_closed_authority_record(metadata)
        ring_workspace_id = require_owned_identity(workspace_id, WorkspaceId)
        ring_key = _require_owned_identity_text(ring_workspace_id, WorkspaceId)
        ring_authority_domain = domain_ring_anchor(authority_domain, ring_workspace_id)
        if not isinstance(key_materials, Mapping):
            raise TypeError("key materials must be mapped by exact KeyReference values")
        materials: dict[KeyReference, bytes] = {}
        for reference, material in key_materials.items():
            owned = owned_reference_for(
                metadata,
                reference,
                "key material reference is absent from ring metadata",
            )
            materials[sealed_reference(owned)] = validated_material(material)
        with boundary_lock:
            if id(owner) in owner_states:
                raise ContractValidationError("key authority owner is already issued")
            if ring_key in ring_owners:
                raise ContractValidationError("logical key ring already has an authority owner")
            state = _KeyAuthorityOwnerState(
                owner,
                ring_key,
                ring_workspace_id,
                ring_authority_domain,
            )
            state.metadata = metadata
            state.materials = materials
            owner_states[id(owner)] = state
            ring_owners[ring_key] = owner

    def usable_generation(
        owner: object,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> KeyGenerationMetadata:
        """Authoritative current lifecycle state for one reference, from the closed owner.

        Trusted validation calls this instead of reading the public ``metadata`` property,
        which a caller can replace with an older snapshot.
        """

        if type(family) is not KeyFamily:
            raise TypeError("key generation lookup requires an exact KeyFamily")
        with boundary_lock:
            state = require_owner_state(owner)
            metadata = generation_metadata(state, family, generation)
            current = None if metadata is None else sealed_generation(metadata)
            if (
                current is None
                or current.key_id != key_id
                or current.state not in _verifiable_states
            ):
                raise ContractValidationError("STALE_KEY_BACKUP")
            return metadata

    def metadata_of(owner: object) -> KeyRingMetadata:
        with boundary_lock:
            state = require_owner_state(owner)
            require_closed_authority_record(state.metadata)
            return state.metadata

    def workspace_of(owner: object) -> WorkspaceId:
        with boundary_lock:
            state = require_owner_state(owner)
            return require_owned_identity(state.ring_workspace_id, WorkspaceId)

    def authority_domain_of(owner: object) -> str:
        with boundary_lock:
            return require_owner_state(owner).ring_authority_domain

    def issue_signing(
        owner: object,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> object:
        with boundary_lock:
            state = require_owner_state(owner)
            reference = resolve_reference(
                state,
                family=family,
                generation=generation,
                key_id=key_id,
            )
            bound = sealed_reference(reference)
            metadata = generation_metadata(state, bound.family, bound.generation)
            current = None if metadata is None else sealed_generation(metadata)
            if current is None or current.state is not _active_state:
                raise ContractValidationError("only current ACTIVE key authority may issue a signing lease")
            return mint_capability(state, signing_type, "sign", reference)

    def issue_verification(
        owner: object,
        *,
        family: KeyFamily,
        generation: int,
        key_id: str,
    ) -> object:
        with boundary_lock:
            state = require_owner_state(owner)
            reference = resolve_reference(
                state,
                family=family,
                generation=generation,
                key_id=key_id,
            )
            bound = sealed_reference(reference)
            metadata = generation_metadata(state, bound.family, bound.generation)
            current = None if metadata is None else sealed_generation(metadata)
            if current is None or current.state not in _verifiable_states:
                raise ContractValidationError("current key state cannot issue a verification lease")
            return mint_capability(state, verification_type, "verify", reference)

    def install_metadata(
        owner: object,
        metadata: KeyRingMetadata,
        *,
        key_materials: Mapping[KeyReference, bytes] | None = None,
    ) -> None:
        if type(metadata) is not KeyRingMetadata:
            raise TypeError("key lifecycle update requires exact KeyRingMetadata")
        require_closed_authority_record(metadata)
        additions = {} if key_materials is None else key_materials
        if not isinstance(additions, Mapping):
            raise TypeError("key material additions must be a mapping")
        with boundary_lock:
            state = require_owner_state(owner)
            require_closed_authority_record(state.metadata)
            old_by_generation = {
                (sealed.family, sealed.generation): sealed
                for sealed in map(sealed_generation, state.metadata.generations)
            }
            new_by_generation = {
                (sealed.family, sealed.generation): sealed
                for sealed in map(sealed_generation, metadata.generations)
            }
            if not set(old_by_generation).issubset(new_by_generation):
                raise ContractValidationError("key lifecycle history cannot remove generations")
            for generation_key, old in old_by_generation.items():
                new = new_by_generation[generation_key]
                if (
                    new.key_id != old.key_id
                    or new.algorithm != old.algorithm
                    or new.algorithm_version != old.algorithm_version
                ):
                    raise ContractValidationError("key family/generation identity is immutable")
                if new.state is not old.state and new.state not in _key_state_transitions[old.state]:
                    raise ContractValidationError("illegal key lifecycle transition")
            for generation_key, new in new_by_generation.items():
                if generation_key in old_by_generation:
                    continue
                prior_generations = [
                    generation
                    for family, generation in old_by_generation
                    if family is new.family
                ]
                if prior_generations and new.generation <= max(prior_generations):
                    raise ContractValidationError("new key generation must advance monotonically")
            merged_materials = dict(state.materials)
            # Keyed by the sealed reference tuple for the same reason as issuance.
            for reference, material in additions.items():
                owned = owned_reference_for(
                    metadata,
                    reference,
                    "key material reference is absent from updated metadata",
                )
                validated = validated_material(material)
                owned_key = sealed_reference(owned)
                if owned_key in merged_materials and merged_materials[owned_key] != validated:
                    raise ContractValidationError("owned key material cannot change for an existing reference")
                merged_materials[owned_key] = validated
            state.materials = merged_materials
            state.metadata = metadata

    def require_capability_use(
        capability: object,
        *,
        use: str,
        expected_family: KeyFamily,
    ) -> tuple[_KeyAuthorityOwnerState, _SealedReference]:
        expected_type = signing_type if use == "sign" else verification_type
        if type(capability) is not expected_type:
            raise TypeError(f"key use requires an exact {expected_type.__name__}")
        with boundary_lock:
            entry = issued_capabilities.get(id(capability))
            if entry is None or entry[0] is not capability or entry[2] != use:
                raise TypeError("key-use capability was not issued by its owning key authority")
            state, reference = entry[1], entry[3]
            KeyReference.__post_init__(reference)
            if (
                owner_states.get(id(state.owner)) is not state
                or ring_owners.get(state.ring_key) is not state.owner
            ):
                raise TypeError("key-use capability owner is not the current ring authority")
            require_unchanged_mirrors(capability, reference)
            bound = sealed_reference(reference)
            if type(expected_family) is not KeyFamily or bound.family is not expected_family:
                raise ContractValidationError("key family does not match the requested operation")
            metadata = generation_metadata(state, bound.family, bound.generation)
            current = None if metadata is None else sealed_generation(metadata)
            permitted = _signable_states if use == "sign" else _verifiable_states
            if (
                current is None
                or current.key_id != bound.key_id
                or current.state not in permitted
                or bound not in state.materials
                or family_phase0_status(bound.family) is _deferred_phase0_status
            ):
                raise ContractValidationError("key authority is not current for requested use")
            return state, bound

    def capability_mac(
        capability: object,
        *,
        use: str,
        expected_family: KeyFamily,
        domain: str,
        message: bytes,
    ) -> str:
        require_identifier(domain, "domain")
        if not isinstance(message, bytes):
            raise TypeError("key-use messages must be bytes")
        with boundary_lock:
            state, bound = require_capability_use(
                capability,
                use=use,
                expected_family=expected_family,
            )
            # Both the authenticated context and the material selection come from the
            # sealed reference the boundary just validated.  Rebuilding either from a
            # live attribute read would let a replaced descriptor bind a family, key ID,
            # or generation the boundary never authorized.
            context = canonical_bytes(
                {
                    "domain": domain,
                    "family": _canonical_enum_value(bound.family),
                    "generation": bound.generation,
                    "key_id": bound.key_id,
                    "message_digest": typed_sha256("VOOL_KEY_USE_MESSAGE_V3", message),
                    "ring_context": state.ring_key,
                }
            )
            return typed_hmac_sha256(
                "VOOL_KEY_USE_CAPABILITY_V3",
                state.materials[bound],
                context,
            )

    def require_use_reference(
        capability: object,
        *,
        use: str,
        expected_family: KeyFamily,
    ) -> KeyReference:
        with boundary_lock:
            _state, bound = require_capability_use(
                capability,
                use=use,
                expected_family=expected_family,
            )
            return KeyReference(bound.family, bound.key_id, bound.generation)

    def require_verification_domain(capability: object, *, expected_family: KeyFamily) -> str:
        """Authority domain this verification capability's ring is entitled to authenticate.

        Read from the ring's closed state after full capability validation, so an
        authenticated decode never learns entitlement from the record it is decoding.
        """

        with boundary_lock:
            state, _reference = require_capability_use(
                capability,
                use="verify",
                expected_family=expected_family,
            )
            return state.ring_authority_domain

    def require_signing(capability: object, *, expected_family: KeyFamily) -> KeyReference:
        return require_use_reference(capability, use="sign", expected_family=expected_family)

    def require_verification(capability: object, *, expected_family: KeyFamily) -> KeyReference:
        return require_use_reference(capability, use="verify", expected_family=expected_family)

    def sign(
        capability: object,
        *,
        expected_family: KeyFamily,
        domain: str,
        message: bytes,
    ) -> str:
        return capability_mac(
            capability,
            use="sign",
            expected_family=expected_family,
            domain=domain,
            message=message,
        )

    def verify(
        capability: object,
        *,
        expected_family: KeyFamily,
        domain: str,
        message: bytes,
        supplied_mac: str,
    ) -> bool:
        expected = capability_mac(
            capability,
            use="verify",
            expected_family=expected_family,
            domain=domain,
            message=message,
        )
        return constant_time_hex_digest_equal(supplied_mac, expected)

    def capability_state(capability: object) -> KeyState:
        with boundary_lock:
            if type(capability) is signing_type:
                use = "sign"
            elif type(capability) is verification_type:
                use = "verify"
            else:
                raise TypeError("key-use capability type mismatch")
            entry = issued_capabilities.get(id(capability))
            if entry is None or entry[0] is not capability or entry[2] != use:
                raise TypeError("key-use capability was not issued by its owning key authority")
            state, reference = entry[1], entry[3]
            if (
                owner_states.get(id(state.owner)) is not state
                or ring_owners.get(state.ring_key) is not state.owner
            ):
                raise TypeError("key-use capability owner is not the current ring authority")
            require_unchanged_mirrors(capability, reference)
            bound = sealed_reference(reference)
            metadata = generation_metadata(state, bound.family, bound.generation)
            current = None if metadata is None else sealed_generation(metadata)
            if current is None or current.key_id != bound.key_id:
                raise ContractValidationError("key lifecycle reference disappeared")
            return current.state

    def capability_owned_by(capability: object, owner: object) -> bool:
        if type(owner) is not authority_type:
            return False
        with boundary_lock:
            entry = issued_capabilities.get(id(capability))
            if entry is None or entry[0] is not capability:
                return False
            state, reference = entry[1], entry[3]
            if state.owner is not owner:
                return False
            if (
                owner_states.get(id(owner)) is not state
                or ring_owners.get(state.ring_key) is not owner
            ):
                return False
            try:
                require_unchanged_mirrors(capability, reference)
            except TypeError:
                return False
            return True

    return (
        issue_owner,
        usable_generation,
        metadata_of,
        workspace_of,
        authority_domain_of,
        issue_signing,
        issue_verification,
        install_metadata,
        require_capability_use,
        capability_state,
        capability_owned_by,
        require_signing,
        require_verification,
        require_verification_domain,
        sign,
        verify,
    )


(
    _issue_key_authority_owner,
    _require_usable_key_generation,
    _key_authority_metadata,
    _key_authority_workspace,
    _key_authority_domain,
    _issue_signing_key_capability,
    _issue_verification_key_capability,
    _install_key_ring_metadata,
    _require_key_capability_use,
    _key_capability_state,
    _key_capability_owned_by,
    require_signing_key_capability,
    require_verification_key_capability,
    require_verification_domain_authority,
    sign_with_key_capability,
    verify_with_key_capability,
) = _build_key_authority_boundary(
    authority_type=DormantKeyAuthority,
    capability_base_type=_KeyUseCapability,
    signing_type=SigningKeyCapability,
    verification_type=VerificationKeyCapability,
    family_phase0_status=_key_family_phase0_status,
    domain_ring_anchor=_authority_domain_ring_anchor,
)


_BUDGET_FIELDS = (
    "raw_before_bytes_per_file",
    "staged_after_bytes_per_file",
    "mutation_bytes",
    "entries",
    "directory_manifest_work",
    "path_bytes",
    "retained_undo_bytes",
    "staging_bytes",
    "tombstone_bytes",
    "domain_storage_bytes",
    "backup_generation_bytes",
    "worker_snapshot_bytes",
    "required_free_space_bytes",
    "change_page_size",
    "preview_work_bytes",
    "stored_preview_bytes",
)

_STORAGE_RESERVATION_FIELDS = (
    "retained_undo_bytes",
    "staging_bytes",
    "tombstone_bytes",
    "backup_generation_bytes",
    "worker_snapshot_bytes",
    "stored_preview_bytes",
)


@dataclass(frozen=True, slots=True)
class AuthorityBudgetRequest:
    raw_before_bytes_per_file: int = 0
    staged_after_bytes_per_file: int = 0
    mutation_bytes: int = 0
    entries: int = 0
    directory_manifest_work: int = 0
    path_bytes: int = 0
    retained_undo_bytes: int = 0
    staging_bytes: int = 0
    tombstone_bytes: int = 0
    domain_storage_bytes: int = 0
    backup_generation_bytes: int = 0
    worker_snapshot_bytes: int = 0
    required_free_space_bytes: int = 0
    change_page_size: int = 0
    preview_work_bytes: int = 0
    stored_preview_bytes: int = 0

    def __post_init__(self) -> None:
        for name in _BUDGET_FIELDS:
            require_nonnegative(getattr(self, name), name)
        self.storage_commitment_bytes()

    def storage_commitment_bytes(self) -> int:
        return _checked_sum(tuple(getattr(self, name) for name in _STORAGE_RESERVATION_FIELDS), "storage commitment")


@dataclass(frozen=True, slots=True)
class AuthorityBudgetReservation:
    profile_version: int
    profile_digest: str
    raw_before_bytes_per_file: int = 0
    staged_after_bytes_per_file: int = 0
    mutation_bytes: int = 0
    entries: int = 0
    directory_manifest_work: int = 0
    path_bytes: int = 0
    retained_undo_bytes: int = 0
    staging_bytes: int = 0
    tombstone_bytes: int = 0
    domain_storage_bytes: int = 0
    backup_generation_bytes: int = 0
    worker_snapshot_bytes: int = 0
    required_free_space_bytes: int = 0
    change_page_size: int = 0
    preview_work_bytes: int = 0
    stored_preview_bytes: int = 0

    def __post_init__(self) -> None:
        require_positive(self.profile_version, "profile_version")
        require_digest(self.profile_digest, "profile_digest")
        for name in _BUDGET_FIELDS:
            require_nonnegative(getattr(self, name), name)
        self.storage_commitment_bytes()

    def storage_commitment_bytes(self) -> int:
        return _checked_sum(tuple(getattr(self, name) for name in _STORAGE_RESERVATION_FIELDS), "storage commitment")


@dataclass(frozen=True, slots=True)
class AuthorityBudgetProfile(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "AuthorityBudgetProfile"

    profile_version: int
    raw_before_bytes_per_file: int
    staged_after_bytes_per_file: int
    mutation_bytes: int
    entries: int
    directory_manifest_work: int
    path_bytes: int
    retained_undo_bytes: int
    staging_bytes: int
    tombstone_bytes: int
    domain_storage_bytes: int
    backup_generation_bytes: int
    worker_snapshot_bytes: int
    required_free_space_bytes: int
    change_page_size: int
    preview_work_bytes: int
    stored_preview_bytes: int

    def __post_init__(self) -> None:
        if self.profile_version != 3:
            raise ContractValidationError("Phase 0 freezes AuthorityBudgetProfile version 3")
        for name in _BUDGET_FIELDS:
            require_positive(getattr(self, name), name)
        _checked_sum(tuple(getattr(self, name) for name in _STORAGE_RESERVATION_FIELDS), "profile storage capacity")

    def reserve(self, request: AuthorityBudgetRequest) -> AuthorityBudgetReservation:
        if not isinstance(request, AuthorityBudgetRequest):
            raise TypeError("budget reservation requires AuthorityBudgetRequest")
        for name in _BUDGET_FIELDS:
            if getattr(request, name) > getattr(self, name):
                raise ContractValidationError(f"BUDGET_EXCEEDED_BEFORE_PREPARE:{name}")
        if request.storage_commitment_bytes() > self.domain_storage_bytes:
            raise ContractValidationError("BUDGET_EXCEEDED_BEFORE_PREPARE:domain_storage_bytes")
        return AuthorityBudgetReservation(
            profile_version=self.profile_version,
            profile_digest=sealed_authority_record_digest(self),
            **{name: getattr(request, name) for name in _BUDGET_FIELDS},
        )

    def validate_reservation(self, reservation: AuthorityBudgetReservation) -> None:
        if not isinstance(reservation, AuthorityBudgetReservation):
            raise TypeError("reservation must be AuthorityBudgetReservation")
        if (
            reservation.profile_version != self.profile_version
            or reservation.profile_digest != sealed_authority_record_digest(self)
        ):
            raise ContractValidationError("budget reservation profile identity mismatch")
        for name in _BUDGET_FIELDS:
            if getattr(reservation, name) > getattr(self, name):
                raise ContractValidationError(f"BUDGET_EXCEEDED_BEFORE_PREPARE:{name}")
        if reservation.storage_commitment_bytes() > self.domain_storage_bytes:
            raise ContractValidationError("BUDGET_EXCEEDED_BEFORE_PREPARE:domain_storage_bytes")


class CapabilityEvidenceState(StrEnum):
    UNPROVEN = "UNPROVEN"
    PROVEN = "PROVEN"


seal_canonical_enum(CapabilityEvidenceState)


class MountPrimitive(IntFlag):
    NONE = 0
    ATOMIC_RENAME = 1 << 0
    DIRECTORY_FSYNC = 1 << 1
    NOFOLLOW_OPEN = 1 << 2
    FILE_IDENTITY = 1 << 3
    HARDLINK_DETECTION = 1 << 4
    REPARSE_POINT_DETECTION = 1 << 5
    CASE_BEHAVIOR_PROBED = 1 << 6
    UNICODE_BEHAVIOR_PROBED = 1 << 7


_ALL_MOUNT_PRIMITIVES = sum(int(item) for item in MountPrimitive)


@dataclass(frozen=True, slots=True)
class MountCertificate(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "MountCertificate"

    certificate_id: str
    adapter_id: str
    adapter_version: str
    os_name: str
    kernel_version: str
    mount_instance_id: str
    persistent_volume_identity_digest: str
    filesystem_type: str
    filesystem_subtype: str
    mount_flags: tuple[str, ...]
    root_identity_digest: str
    authority_namespace_identity_digest: str
    supported_primitive_bitset: int
    generation: int
    probe_suite_digest: str
    evidence_state: CapabilityEvidenceState

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_state, CapabilityEvidenceState):
            raise TypeError("evidence_state must be CapabilityEvidenceState")
        for name in ("certificate_id", "adapter_id", "adapter_version", "mount_instance_id"):
            require_identifier(getattr(self, name), name)
        for name in ("os_name", "kernel_version", "filesystem_type", "filesystem_subtype"):
            _safe_text(getattr(self, name), name)
        normalized_flags = tuple(sorted(self.mount_flags))
        if len(normalized_flags) != len(set(normalized_flags)):
            raise ContractValidationError("mount_flags must be unique")
        for flag in normalized_flags:
            require_identifier(flag, "mount_flag")
        object.__setattr__(self, "mount_flags", normalized_flags)
        for name in (
            "persistent_volume_identity_digest",
            "root_identity_digest",
            "authority_namespace_identity_digest",
            "probe_suite_digest",
        ):
            require_digest(getattr(self, name), name)
        require_positive(self.generation, "generation")
        require_nonnegative(self.supported_primitive_bitset, "supported_primitive_bitset")
        if self.supported_primitive_bitset & ~_ALL_MOUNT_PRIMITIVES:
            raise ContractValidationError("supported_primitive_bitset contains unknown capabilities")
        if self.evidence_state is CapabilityEvidenceState.PROVEN and self.supported_primitive_bitset == 0:
            raise ContractValidationError("a PROVEN mount certificate must prove at least one primitive")
        if self.evidence_state is CapabilityEvidenceState.UNPROVEN and self.supported_primitive_bitset != 0:
            raise ContractValidationError("an UNPROVEN mount cannot advertise supported primitives")

    def supports(self, primitive: MountPrimitive) -> bool:
        if not isinstance(primitive, MountPrimitive):
            raise TypeError("requested mount capability must be MountPrimitive")
        requested = int(primitive)
        if requested == 0 or requested & ~_ALL_MOUNT_PRIMITIVES:
            return False
        return (
            self.evidence_state is CapabilityEvidenceState.PROVEN
            and (self.supported_primitive_bitset & requested) == requested
        )


class BackupGenerationState(StrEnum):
    REQUESTED = "REQUESTED"
    FROZEN_AND_PINNED = "FROZEN_AND_PINNED"
    DB_SNAPSHOTTED = "DB_SNAPSHOTTED"
    ARTIFACTS_COPIED = "ARTIFACTS_COPIED"
    SEALED_COMPLETE = "SEALED_COMPLETE"


seal_canonical_enum(BackupGenerationState)


_BACKUP_TRANSITIONS = {
    BackupGenerationState.REQUESTED: BackupGenerationState.FROZEN_AND_PINNED,
    BackupGenerationState.FROZEN_AND_PINNED: BackupGenerationState.DB_SNAPSHOTTED,
    BackupGenerationState.DB_SNAPSHOTTED: BackupGenerationState.ARTIFACTS_COPIED,
}


def validate_backup_transition(current: BackupGenerationState, target: BackupGenerationState) -> None:
    if target is BackupGenerationState.SEALED_COMPLETE:
        raise ContractValidationError("SEALED_COMPLETE requires validated backup closure proof")
    if _BACKUP_TRANSITIONS.get(current) is not target:
        raise ContractValidationError(f"illegal backup transition: {current.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class RequiredKeyReference:
    family: KeyFamily
    generation: int
    key_id: str

    def __post_init__(self) -> None:
        # Validate the stored values through the sealed descriptors, so a replaced
        # descriptor cannot present one identity here and another at validation time.
        read = _read_key_record_slot
        if not isinstance(read(RequiredKeyReference, "family", self), KeyFamily):
            raise TypeError("family must be KeyFamily")
        require_positive(read(RequiredKeyReference, "generation", self), "generation")
        require_identifier(read(RequiredKeyReference, "key_id", self), "key_id")


_seal_key_record_slots(RequiredKeyReference, ("family", "key_id", "generation"))
def _sealed_required_key(reference: object) -> tuple[KeyFamily, int, str]:
    """The required-key identity captured at construction: (family, generation, key_id).

    Backup required-key validation must judge what was sealed into the manifest, not what
    a later-replaced public descriptor reports, so every authoritative consumer of a
    RequiredKeyReference resolves the tuple through this reader.
    """

    if type(reference) is not RequiredKeyReference:
        raise TypeError("required-key identity requires an exact RequiredKeyReference")
    return (
        _read_key_record_slot(RequiredKeyReference, "family", reference),
        _read_key_record_slot(RequiredKeyReference, "generation", reference),
        _read_key_record_slot(RequiredKeyReference, "key_id", reference),
    )


def _required_key_sort_key(reference: object) -> tuple[str, int, str]:
    """Canonical ordering/duplicate key for one required-key reference, from its seal."""

    family, generation, key_id = _sealed_required_key(reference)
    return (_canonical_enum_value(family), generation, key_id)


class BackupPredecessorKind(StrEnum):
    GENESIS = "GENESIS"
    COMPLETED = "COMPLETED"


seal_canonical_enum(BackupPredecessorKind)


@dataclass(frozen=True, slots=True)
class BackupPredecessor:
    kind: BackupPredecessorKind
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    authority_generation: int
    manifest_digest: str

    def __post_init__(self) -> None:
        typed = (
            (self.kind, BackupPredecessorKind, "kind"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
        )
        for value, expected_type, field_name in typed:
            require_exact_authority_value(value, expected_type, field_name)
        require_nonnegative(self.authority_generation, "authority_generation")
        require_digest(self.manifest_digest, "manifest_digest")
        if self.kind is BackupPredecessorKind.GENESIS:
            if self.authority_generation != 0 or self.manifest_digest != self._genesis_digest():
                raise ContractValidationError("genesis predecessor must use the exact explicit genesis binding")
        elif self.authority_generation == 0:
            raise ContractValidationError("completed predecessor generation must be positive")
        _record_backup_predecessor(self)

    def _genesis_digest(self) -> str:
        return typed_sha256(
            "VOOL_BACKUP_GENESIS_V3",
            canonical_bytes(
                {
                    "authority_epoch": _canonical_counter_value(self.authority_epoch),
                    "incarnation_id": _require_owned_identity_text(self.incarnation_id, IncarnationId),
                    "workspace_id": _require_owned_identity_text(self.workspace_id, WorkspaceId),
                }
            ),
        )

    @classmethod
    def _sealed(cls, predecessor: object) -> _SealedPredecessor:
        """The predecessor identity recorded when this predecessor was constructed."""

        return _sealed_backup_predecessor(predecessor)

    @classmethod
    def genesis(
        cls,
        *,
        workspace_id: WorkspaceId,
        incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
    ) -> BackupPredecessor:
        digest = typed_sha256(
            "VOOL_BACKUP_GENESIS_V3",
            canonical_bytes(
                {
                    "authority_epoch": _canonical_counter_value(authority_epoch),
                    "incarnation_id": _require_owned_identity_text(incarnation_id, IncarnationId),
                    "workspace_id": _require_owned_identity_text(workspace_id, WorkspaceId),
                }
            ),
        )
        return cls(
            BackupPredecessorKind.GENESIS,
            workspace_id,
            incarnation_id,
            authority_epoch,
            0,
            digest,
        )

    @classmethod
    def completed(
        cls,
        *,
        workspace_id: WorkspaceId,
        incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
        authority_generation: int,
        manifest_digest: str,
    ) -> BackupPredecessor:
        return cls(
            BackupPredecessorKind.COMPLETED,
            workspace_id,
            incarnation_id,
            authority_epoch,
            authority_generation,
            manifest_digest,
        )


_BACKUP_PREDECESSOR_FIELDS = (
    "kind",
    "workspace_id",
    "incarnation_id",
    "authority_epoch",
    "authority_generation",
    "manifest_digest",
)
_seal_key_record_slots(BackupPredecessor, _BACKUP_PREDECESSOR_FIELDS)


class _SealedPredecessor(NamedTuple):
    """Predecessor identity as primitives, captured when the predecessor was constructed.

    Holding the field objects is not enough: ``object.__setattr__`` can rewrite a
    predecessor's slots after the owning manifest was issued, and identity/counter
    objects compare equal to themselves however they are mutated.
    """

    kind: str
    workspace_id: str
    incarnation_id: str
    authority_epoch: int
    authority_generation: int
    manifest_digest: str


def _read_backup_predecessor_state(predecessor: object) -> _SealedPredecessor:
    """Read a predecessor's current slots into primitives, through the sealed fields."""

    read = _read_key_record_slot
    return _SealedPredecessor(
        kind=_canonical_enum_value(read(BackupPredecessor, "kind", predecessor)),
        workspace_id=_require_owned_identity_text(
            read(BackupPredecessor, "workspace_id", predecessor), WorkspaceId
        ),
        incarnation_id=_require_owned_identity_text(
            read(BackupPredecessor, "incarnation_id", predecessor), IncarnationId
        ),
        authority_epoch=_canonical_counter_value(
            read(BackupPredecessor, "authority_epoch", predecessor)
        ),
        authority_generation=read(BackupPredecessor, "authority_generation", predecessor),
        manifest_digest=read(BackupPredecessor, "manifest_digest", predecessor),
    )


def _build_backup_predecessor_owner() -> tuple[Any, ...]:
    """Own each predecessor's construction-time identity, outside instance-writable state.

    The snapshot is recorded once, when the constructor validates the predecessor.  A
    predecessor whose slots have since diverged is refused rather than re-read, and one
    that never ran its constructor carries no identity at all.
    """

    constructed: dict[int, tuple[object, _SealedPredecessor]] = {}

    def record(predecessor: object) -> None:
        if type(predecessor) is not BackupPredecessor:
            raise TypeError("backup predecessor issuance requires an exact BackupPredecessor")
        state = _read_backup_predecessor_state(predecessor)
        existing = constructed.get(id(predecessor))
        if existing is not None and existing[0] is predecessor:
            # Re-validating the same object must not launder a slot rewrite.
            if existing[1] != state:
                raise ContractValidationError("backup predecessor changed after construction")
            return
        constructed[id(predecessor)] = (predecessor, state)

    def sealed(predecessor: object) -> _SealedPredecessor:
        if type(predecessor) is not BackupPredecessor:
            raise TypeError("backup predecessor comparison requires an exact BackupPredecessor")
        entry = constructed.get(id(predecessor))
        if entry is None or entry[0] is not predecessor:
            raise TypeError("backup predecessor was not issued by its constructor")
        if _read_backup_predecessor_state(predecessor) != entry[1]:
            raise ContractValidationError("backup predecessor changed after construction")
        return entry[1]

    return record, sealed


(_record_backup_predecessor, _sealed_backup_predecessor) = _build_backup_predecessor_owner()


@dataclass(frozen=True, slots=True)
class BackupManifestV3(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "BackupManifestV3"

    backup_generation_id: BackupGenerationId
    authority_generation: int
    db_snapshot_identity_digest: str
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    head_revision: HeadRevision
    claim_digest: str
    claim_key_generation: int
    blob_inventory_digest: str
    tombstone_inventory_digest: str
    required_keys: tuple[RequiredKeyReference, ...]
    budget_profile_digest: str
    predecessor: BackupPredecessor
    manifest_key_id: str
    manifest_key_generation: int
    manifest_digest: str
    manifest_mac: str

    def __post_init__(self) -> None:
        typed_identities = (
            (self.backup_generation_id, BackupGenerationId, "backup_generation_id"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.head_revision, HeadRevision, "head_revision"),
            (self.predecessor, BackupPredecessor, "predecessor"),
        )
        for value, expected_type, field_name in typed_identities:
            require_exact_authority_value(value, expected_type, field_name)
        if not isinstance(self.required_keys, tuple) or not all(
            isinstance(item, RequiredKeyReference) for item in self.required_keys
        ):
            raise TypeError("required_keys must be an immutable tuple of RequiredKeyReference")
        require_positive(self.authority_generation, "authority_generation")
        require_positive(self.claim_key_generation, "claim_key_generation")
        require_identifier(self.manifest_key_id, "manifest_key_id")
        require_positive(self.manifest_key_generation, "manifest_key_generation")
        for name in (
            "db_snapshot_identity_digest",
            "claim_digest",
            "blob_inventory_digest",
            "tombstone_inventory_digest",
            "budget_profile_digest",
            "manifest_digest",
            "manifest_mac",
        ):
            require_digest(getattr(self, name), name)
        sealed_predecessor = BackupPredecessor._sealed(self.predecessor)
        if (
            sealed_predecessor.workspace_id
            != _require_owned_identity_text(self.workspace_id, WorkspaceId)
            or sealed_predecessor.incarnation_id
            != _require_owned_identity_text(self.incarnation_id, IncarnationId)
            or sealed_predecessor.authority_epoch
            != _canonical_counter_value(self.authority_epoch)
        ):
            raise ContractValidationError("backup predecessor authority scope mismatch")
        if self.authority_generation == 1:
            if sealed_predecessor.kind != _canonical_enum_value(BackupPredecessorKind.GENESIS):
                raise ContractValidationError("genesis backup requires the explicit genesis predecessor")
        elif (
            sealed_predecessor.kind != _canonical_enum_value(BackupPredecessorKind.COMPLETED)
            or sealed_predecessor.authority_generation != self.authority_generation - 1
        ):
            raise ContractValidationError("later backup must bind the immediately previous completed generation")
        sealed_required = tuple(_sealed_required_key(item) for item in self.required_keys)
        identities = tuple(_required_key_sort_key(item) for item in self.required_keys)
        if len(identities) != len(set(identities)):
            raise ContractValidationError("required_keys contains duplicates")
        object.__setattr__(
            self,
            "required_keys",
            tuple(sorted(self.required_keys, key=_required_key_sort_key)),
        )
        if not any(
            family is KeyFamily.CLAIM_ENROLLMENT and generation == self.claim_key_generation
            for family, generation, _key_id in sealed_required
        ):
            raise ContractValidationError("backup manifest must bind its exact claim key generation")
        if not any(
            family is KeyFamily.BACKUP_MANIFEST
            and generation == self.manifest_key_generation
            and key_id == self.manifest_key_id
            for family, generation, key_id in sealed_required
        ):
            raise ContractValidationError("backup manifest must bind its exact manifest-signing key generation")
        if not constant_time_hex_digest_equal(self.manifest_digest, self.expected_manifest_digest()):
            raise ContractValidationError("manifest_digest does not bind the complete backup closure")
        _record_backup_manifest(self)

    def unsigned_record(self) -> dict[str, Any]:
        record = sealed_authority_record(self)
        del record["payload"]["manifest_digest"]
        del record["payload"]["manifest_mac"]
        return record

    def expected_manifest_digest(self) -> str:
        return typed_sha256("VOOL_BACKUP_MANIFEST_V3", canonical_bytes(self.unsigned_record()))

    @classmethod
    def issue(
        cls,
        *,
        backup_generation_id: BackupGenerationId,
        authority_generation: int,
        db_snapshot_identity_digest: str,
        workspace_id: WorkspaceId,
        incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
        head_revision: HeadRevision,
        claim_digest: str,
        claim_key_generation: int,
        blob_inventory_digest: str,
        tombstone_inventory_digest: str,
        required_keys: tuple[RequiredKeyReference, ...],
        budget_profile_digest: str,
        predecessor: BackupPredecessor,
        signing_key: SigningKeyCapability,
    ) -> BackupManifestV3:
        if type(signing_key) is not SigningKeyCapability:
            raise TypeError("backup manifest issuance requires SigningKeyCapability")
        signing_reference = require_signing_key_capability(
            signing_key,
            expected_family=KeyFamily.BACKUP_MANIFEST,
        )
        normalized_keys = tuple(sorted(required_keys, key=_required_key_sort_key))
        unsigned = dormant_envelope_record(
            record_type=cls.RECORD_TYPE,
            schema_version=cls.SCHEMA_VERSION,
            payload={
                "authority_epoch": _canonical_counter_value(authority_epoch),
                "authority_generation": authority_generation,
                "backup_generation_id": _require_owned_identity_text(
                    backup_generation_id,
                    BackupGenerationId,
                ),
                "blob_inventory_digest": blob_inventory_digest,
                "budget_profile_digest": budget_profile_digest,
                "claim_digest": claim_digest,
                "claim_key_generation": claim_key_generation,
                "db_snapshot_identity_digest": db_snapshot_identity_digest,
                "head_revision": _canonical_counter_value(head_revision),
                "incarnation_id": _require_owned_identity_text(incarnation_id, IncarnationId),
                "manifest_key_generation": signing_reference.generation,
                "manifest_key_id": signing_reference.key_id,
                "predecessor": predecessor,
                "required_keys": normalized_keys,
                "tombstone_inventory_digest": tombstone_inventory_digest,
                "workspace_id": _require_owned_identity_text(workspace_id, WorkspaceId),
            },
        )
        manifest_digest = typed_sha256("VOOL_BACKUP_MANIFEST_V3", canonical_bytes(unsigned))
        manifest_mac = sign_with_key_capability(
            signing_key,
            expected_family=KeyFamily.BACKUP_MANIFEST,
            domain="VOOL_BACKUP_MANIFEST_MAC_V3",
            message=manifest_digest.encode("ascii"),
        )
        return cls(
            backup_generation_id=backup_generation_id,
            authority_generation=authority_generation,
            db_snapshot_identity_digest=db_snapshot_identity_digest,
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
            head_revision=head_revision,
            claim_digest=claim_digest,
            claim_key_generation=claim_key_generation,
            blob_inventory_digest=blob_inventory_digest,
            tombstone_inventory_digest=tombstone_inventory_digest,
            required_keys=normalized_keys,
            budget_profile_digest=budget_profile_digest,
            predecessor=predecessor,
            manifest_key_id=signing_reference.key_id,
            manifest_key_generation=signing_reference.generation,
            manifest_digest=manifest_digest,
            manifest_mac=manifest_mac,
        )

    def validate_required_keys(self, key_authority: DormantKeyAuthority) -> None:
        # The manifest must still be its exact sealed record before any authority
        # decision reads it.  The construction-time fingerprint already covers every
        # field, including values nested inside the predecessor and required keys.
        require_closed_authority_record(self)
        if type(key_authority) is not DormantKeyAuthority:
            raise TypeError("required-key validation needs the exact key authority owner")
        for family, generation, key_id in _sealed_manifest_state(self).required_keys:
            _require_usable_key_generation(
                key_authority,
                family=family,
                generation=generation,
                key_id=key_id,
            )

    def validate_closure(
        self,
        *,
        key_authority: DormantKeyAuthority,
        verification_key: VerificationKeyCapability,
        budget_profile: AuthorityBudgetProfile,
        previous_closure: ValidatedBackupClosure | None,
    ) -> ValidatedBackupClosure:
        # Enforce the whole-record seal before any continuity decision, so a manifest
        # mutated after issuance is refused rather than read field by field.
        require_closed_authority_record(self)
        if type(key_authority) is not DormantKeyAuthority:
            raise TypeError("key_authority must be DormantKeyAuthority")
        if type(verification_key) is not VerificationKeyCapability:
            raise TypeError("verification_key must be VerificationKeyCapability")
        if not _key_capability_owned_by(verification_key, key_authority):
            raise ContractValidationError("backup verifier belongs to another key authority owner")
        if not isinstance(budget_profile, AuthorityBudgetProfile):
            raise TypeError("budget_profile must be AuthorityBudgetProfile")
        current_state = _sealed_manifest_state(self)
        if current_state.authority_generation == 1:
            if previous_closure is not None:
                raise ContractValidationError("genesis backup cannot bind a completed predecessor closure")
        else:
            if not isinstance(previous_closure, ValidatedBackupClosure):
                raise ContractValidationError("later backup requires the previous validated completed closure")
            previous = _validated_closure_state(previous_closure)
            if (
                previous.authority_generation != current_state.authority_generation - 1
                or previous.workspace_id != current_state.workspace_id
                or previous.incarnation_id != current_state.incarnation_id
                or previous.authority_epoch != current_state.authority_epoch
                or previous.manifest_digest != current_state.predecessor_manifest_digest
            ):
                raise ContractValidationError("backup predecessor closure is wrong, stale, future, or out of scope")
        self.validate_required_keys(key_authority)
        if current_state.budget_profile_digest != sealed_authority_record_digest(budget_profile):
            raise ContractValidationError("backup closure budget profile mismatch")
        verification_reference = require_verification_key_capability(
            verification_key,
            expected_family=KeyFamily.BACKUP_MANIFEST,
        )
        if (
            verification_reference.key_id != current_state.manifest_key_id
            or verification_reference.generation != current_state.manifest_key_generation
            or not verify_with_key_capability(
                verification_key,
                expected_family=KeyFamily.BACKUP_MANIFEST,
                domain="VOOL_BACKUP_MANIFEST_MAC_V3",
                message=current_state.manifest_digest.encode("ascii"),
                supplied_mac=current_state.manifest_mac,
            )
        ):
            raise ContractValidationError("backup closure manifest authentication failed")
        return ValidatedBackupClosure._from_validated(self)


_BACKUP_MANIFEST_CLOSURE_FIELDS = (
    "backup_generation_id",
    "authority_generation",
    "workspace_id",
    "incarnation_id",
    "authority_epoch",
    "manifest_digest",
    "manifest_mac",
    "manifest_key_id",
    "manifest_key_generation",
    "budget_profile_digest",
    "predecessor",
    "required_keys",
)
_seal_key_record_slots(BackupManifestV3, _BACKUP_MANIFEST_CLOSURE_FIELDS)


class _SealedManifestClosure(NamedTuple):
    backup_generation_id: BackupGenerationId
    authority_generation: int
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    manifest_digest: str
    manifest_mac: str
    manifest_key_id: str
    manifest_key_generation: int
    budget_profile_digest: str
    predecessor: BackupPredecessor
    required_keys: tuple[RequiredKeyReference, ...]


def _sealed_manifest_closure(manifest: object) -> _SealedManifestClosure:
    """One manifest's closure-authority fields, read through descriptors sealed at import.

    Chain continuity compares two manifests, so both sides -- the manifest being
    validated and the previously validated one -- resolve through this reader.  A
    replaced public descriptor on either side must not move a continuity decision.
    """

    if type(manifest) is not BackupManifestV3:
        raise TypeError("backup closure authority requires an exact BackupManifestV3")
    return _SealedManifestClosure(
        *(
            _read_key_record_slot(BackupManifestV3, name, manifest)
            for name in _BACKUP_MANIFEST_CLOSURE_FIELDS
        )
    )


_VALIDATED_BACKUP_CLOSURE = object()


@dataclass(frozen=True, slots=True)
class ValidatedBackupClosure:
    manifest: BackupManifestV3
    _validation_capability: InitVar[object | None] = None

    def __post_init__(self, _validation_capability: object | None) -> None:
        if _validation_capability is not _VALIDATED_BACKUP_CLOSURE:
            raise TypeError("ValidatedBackupClosure must be produced by manifest validation")
        if not isinstance(self.manifest, BackupManifestV3):
            raise TypeError("manifest must be BackupManifestV3")

    @classmethod
    def _from_validated(cls, manifest: BackupManifestV3) -> ValidatedBackupClosure:
        return _issue_validated_closure(cls(manifest, _VALIDATED_BACKUP_CLOSURE), manifest)


class _ValidatedManifestState(NamedTuple):
    """Backup authority fields as primitive values, so a later slot write cannot move them.

    Binding the manifest object is not enough: ``object.__setattr__`` can rewrite its
    slots after validation, and identity/counter objects would compare equal to
    themselves however they were mutated.  Every field here is a plain ``str``/``int``
    captured at validation time.
    """

    backup_generation_id: str
    authority_generation: int
    workspace_id: str
    incarnation_id: str
    authority_epoch: int
    manifest_digest: str
    manifest_mac: str
    manifest_key_id: str
    manifest_key_generation: int
    budget_profile_digest: str
    predecessor_kind: str
    predecessor_authority_generation: int
    predecessor_manifest_digest: str
    predecessor_workspace_id: str
    predecessor_incarnation_id: str
    predecessor_authority_epoch: int
    required_keys: tuple[tuple[KeyFamily, int, str], ...]


def _manifest_authority_state(manifest: object) -> _ValidatedManifestState:
    """Read one manifest's backup-authority fields into primitives, through sealed reads."""

    sealed = _sealed_manifest_closure(manifest)
    predecessor = BackupPredecessor._sealed(sealed.predecessor)
    return _ValidatedManifestState(
        backup_generation_id=_require_owned_identity_text(
            sealed.backup_generation_id, BackupGenerationId
        ),
        authority_generation=sealed.authority_generation,
        workspace_id=_require_owned_identity_text(sealed.workspace_id, WorkspaceId),
        incarnation_id=_require_owned_identity_text(sealed.incarnation_id, IncarnationId),
        authority_epoch=_canonical_counter_value(sealed.authority_epoch),
        manifest_digest=sealed.manifest_digest,
        manifest_mac=sealed.manifest_mac,
        manifest_key_id=sealed.manifest_key_id,
        manifest_key_generation=sealed.manifest_key_generation,
        budget_profile_digest=sealed.budget_profile_digest,
        predecessor_kind=predecessor.kind,
        predecessor_authority_generation=predecessor.authority_generation,
        predecessor_manifest_digest=predecessor.manifest_digest,
        predecessor_workspace_id=predecessor.workspace_id,
        predecessor_incarnation_id=predecessor.incarnation_id,
        predecessor_authority_epoch=predecessor.authority_epoch,
        required_keys=tuple(_sealed_required_key(item) for item in sealed.required_keys),
    )


def _build_backup_manifest_owner() -> tuple[Any, ...]:
    """Own each manifest's construction-time authority state, from one reader.

    ``require_closed_authority_record`` recomputes its fingerprint through ordinary
    attribute access while continuity reads go through the sealed field descriptors.
    A caller could exploit the split: rewrite a slot, then install a class descriptor
    that reports the original value, so the integrity guard saw an intact record while
    the continuity read consumed the rewrite.  Snapshot and divergence check here both
    go through the sealed descriptors, so both halves see the same thing.
    """

    constructed: dict[int, tuple[object, _ValidatedManifestState]] = {}

    def record(manifest: object) -> None:
        if type(manifest) is not BackupManifestV3:
            raise TypeError("backup manifest issuance requires an exact BackupManifestV3")
        state = _manifest_authority_state(manifest)
        existing = constructed.get(id(manifest))
        if existing is not None and existing[0] is manifest:
            if existing[1] != state:
                raise ContractValidationError("backup manifest changed after construction")
            return
        constructed[id(manifest)] = (manifest, state)

    def sealed_state(manifest: object) -> _ValidatedManifestState:
        if type(manifest) is not BackupManifestV3:
            raise TypeError("backup manifest authority requires an exact BackupManifestV3")
        entry = constructed.get(id(manifest))
        if entry is None or entry[0] is not manifest:
            raise TypeError("backup manifest was not issued by its constructor")
        if _manifest_authority_state(manifest) != entry[1]:
            raise ContractValidationError("backup manifest changed after construction")
        return entry[1]

    return record, sealed_state


(_record_backup_manifest, _sealed_manifest_state) = _build_backup_manifest_owner()


def _build_validated_closure_owner() -> tuple[Any, ...]:
    """Bind each validated closure to the manifest state that actually passed validation.

    The closure's ``manifest`` field stays readable for display, but it is instance
    state, and so are the manifest's own slots.  Post-closure authority therefore comes
    from the primitive snapshot recorded here when manifest validation mints the
    closure, never from a current read.  A manifest whose state has since diverged from
    its snapshot also fails closed, so tampering is refused rather than ignored.

    The registry holds the closure itself, so its identity key cannot be recycled while
    the binding is live.
    """

    validated: dict[int, tuple[object, object, _ValidatedManifestState]] = {}

    def issue(closure: object, manifest: object) -> object:
        if type(closure) is not ValidatedBackupClosure:
            raise TypeError("validated backup closure issuance requires the exact closure type")
        if type(manifest) is not BackupManifestV3:
            raise TypeError("validated backup closure must bind an exact BackupManifestV3")
        validated[id(closure)] = (closure, manifest, _sealed_manifest_state(manifest))
        return closure

    def state_of(closure: object) -> _ValidatedManifestState:
        if type(closure) is not ValidatedBackupClosure:
            raise TypeError("validated backup closure required")
        entry = validated.get(id(closure))
        if entry is None or entry[0] is not closure:
            raise TypeError("validated backup closure was not issued by manifest validation")
        if _sealed_manifest_state(entry[1]) != entry[2]:
            raise ContractValidationError(
                "validated backup closure manifest changed after validation"
            )
        return entry[2]

    return issue, state_of


(_issue_validated_closure, _validated_closure_state) = _build_validated_closure_owner()


@dataclass(frozen=True, slots=True)
class BackupGenerationContract(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "BackupGenerationContract"

    backup_generation_id: BackupGenerationId
    state: BackupGenerationState
    manifest_digest: str | None
    _seal_capability: InitVar[object | None] = None

    def __post_init__(self, _seal_capability: object | None) -> None:
        require_owned_identity(self.backup_generation_id, BackupGenerationId)
        if not isinstance(self.state, BackupGenerationState):
            raise TypeError("state must be BackupGenerationState")
        if self.state is BackupGenerationState.SEALED_COMPLETE:
            if _seal_capability is not _VALIDATED_BACKUP_CLOSURE or self.manifest_digest is None:
                raise ContractValidationError("SEALED_COMPLETE requires validated backup closure proof")
            require_digest(self.manifest_digest, "manifest_digest")
        elif self.manifest_digest is not None:
            raise ContractValidationError("only SEALED_COMPLETE may carry a manifest digest")

    @classmethod
    def seal(
        cls,
        current: BackupGenerationContract,
        closure: ValidatedBackupClosure,
    ) -> BackupGenerationContract:
        if not isinstance(current, BackupGenerationContract):
            raise TypeError("current must be BackupGenerationContract")
        if current.state is not BackupGenerationState.ARTIFACTS_COPIED:
            raise ContractValidationError("backup must reach ARTIFACTS_COPIED before sealing")
        if not isinstance(closure, ValidatedBackupClosure):
            raise TypeError("closure must be ValidatedBackupClosure")
        validated = _validated_closure_state(closure)
        if (
            _require_owned_identity_text(current.backup_generation_id, BackupGenerationId)
            != validated.backup_generation_id
        ):
            raise ContractValidationError("backup closure generation identity mismatch")
        return cls(
            current.backup_generation_id,
            BackupGenerationState.SEALED_COMPLETE,
            validated.manifest_digest,
            _VALIDATED_BACKUP_CLOSURE,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceUserSnapshot(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "WorkspaceUserSnapshot"

    source_workspace_id: WorkspaceId
    source_incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    head_revision: HeadRevision
    included_entry_manifest_digest: str
    excluded_authority_proof_digest: str
    snapshot_key_id: str
    snapshot_key_generation: int
    snapshot_identity_digest: str
    snapshot_mac: str
    authority_namespace_absent: bool = True
    no_live_object_aliases: bool = True
    sealed_source_identity: bool = True

    def __post_init__(self) -> None:
        typed_identities = (
            (self.source_workspace_id, WorkspaceId, "source_workspace_id"),
            (self.source_incarnation_id, IncarnationId, "source_incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.head_revision, HeadRevision, "head_revision"),
        )
        for value, expected_type, field_name in typed_identities:
            require_exact_authority_value(value, expected_type, field_name)
        for name in (
            "included_entry_manifest_digest",
            "excluded_authority_proof_digest",
            "snapshot_identity_digest",
            "snapshot_mac",
        ):
            require_digest(getattr(self, name), name)
        require_identifier(self.snapshot_key_id, "snapshot_key_id")
        require_positive(self.snapshot_key_generation, "snapshot_key_generation")
        proof_flags = (
            self.authority_namespace_absent,
            self.no_live_object_aliases,
            self.sealed_source_identity,
        )
        if not all(isinstance(flag, bool) for flag in proof_flags) or not all(proof_flags):
            raise ContractValidationError("worker snapshots must exclude authority state, aliases, and source drift")
        if not constant_time_hex_digest_equal(self.snapshot_identity_digest, self.expected_identity_digest()):
            raise ContractValidationError("snapshot identity does not bind its sealed source")

    def unsigned_record(self) -> dict[str, Any]:
        record = sealed_authority_record(self)
        del record["payload"]["snapshot_identity_digest"]
        del record["payload"]["snapshot_mac"]
        return record

    def expected_identity_digest(self) -> str:
        return typed_sha256("VOOL_WORKSPACE_USER_SNAPSHOT_V3", canonical_bytes(self.unsigned_record()))

    def verify_mac(self, key: VerificationKeyCapability) -> bool:
        if type(key) is not VerificationKeyCapability:
            raise TypeError("snapshot verification requires VerificationKeyCapability")
        reference = require_verification_key_capability(
            key,
            expected_family=KeyFamily.WORKER_SNAPSHOT,
        )
        if reference.key_id != self.snapshot_key_id or reference.generation != self.snapshot_key_generation:
            return False
        return verify_with_key_capability(
            key,
            expected_family=KeyFamily.WORKER_SNAPSHOT,
            domain="VOOL_WORKSPACE_USER_SNAPSHOT_MAC_V3",
            message=self.snapshot_identity_digest.encode("ascii"),
            supplied_mac=self.snapshot_mac,
        )

    def validate_source(self, current: HeadState) -> None:
        if (
            current.workspace_id != self.source_workspace_id
            or current.incarnation_id != self.source_incarnation_id
            or current.authority_epoch != self.authority_epoch
            or current.head_revision != self.head_revision
        ):
            raise ContractValidationError("source drift invalidates worker snapshot")

    @classmethod
    def seal(
        cls,
        *,
        source_workspace_id: WorkspaceId,
        source_incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
        head_revision: HeadRevision,
        included_entry_manifest_digest: str,
        excluded_authority_proof_digest: str,
        signing_key: SigningKeyCapability,
    ) -> WorkspaceUserSnapshot:
        if type(signing_key) is not SigningKeyCapability:
            raise TypeError("snapshot sealing requires SigningKeyCapability")
        signing_reference = require_signing_key_capability(
            signing_key,
            expected_family=KeyFamily.WORKER_SNAPSHOT,
        )
        unsigned = dormant_envelope_record(
            record_type=cls.RECORD_TYPE,
            schema_version=cls.SCHEMA_VERSION,
            payload={
                "authority_epoch": _canonical_counter_value(authority_epoch),
                "authority_namespace_absent": True,
                "excluded_authority_proof_digest": excluded_authority_proof_digest,
                "head_revision": _canonical_counter_value(head_revision),
                "included_entry_manifest_digest": included_entry_manifest_digest,
                "no_live_object_aliases": True,
                "sealed_source_identity": True,
                "snapshot_key_generation": signing_reference.generation,
                "snapshot_key_id": signing_reference.key_id,
                "source_incarnation_id": _require_owned_identity_text(
                    source_incarnation_id,
                    IncarnationId,
                ),
                "source_workspace_id": _require_owned_identity_text(source_workspace_id, WorkspaceId),
            },
        )
        identity_digest = typed_sha256("VOOL_WORKSPACE_USER_SNAPSHOT_V3", canonical_bytes(unsigned))
        snapshot_mac = sign_with_key_capability(
            signing_key,
            expected_family=KeyFamily.WORKER_SNAPSHOT,
            domain="VOOL_WORKSPACE_USER_SNAPSHOT_MAC_V3",
            message=identity_digest.encode("ascii"),
        )
        return cls(
            source_workspace_id=source_workspace_id,
            source_incarnation_id=source_incarnation_id,
            authority_epoch=authority_epoch,
            head_revision=head_revision,
            included_entry_manifest_digest=included_entry_manifest_digest,
            excluded_authority_proof_digest=excluded_authority_proof_digest,
            snapshot_key_id=signing_reference.key_id,
            snapshot_key_generation=signing_reference.generation,
            snapshot_identity_digest=identity_digest,
            snapshot_mac=snapshot_mac,
        )


class FailureCode(StrEnum):
    REJECTED_RESERVED_NAMESPACE = "REJECTED_RESERVED_NAMESPACE"
    RESERVED_NAMESPACE_COLLISION = "RESERVED_NAMESPACE_COLLISION"
    AUTHORITY_NAMESPACE_COMPROMISED = "AUTHORITY_NAMESPACE_COMPROMISED"
    AUTHORITY_COMMAND_ISOLATION_UNAVAILABLE = "AUTHORITY_COMMAND_ISOLATION_UNAVAILABLE"
    MOUNT_MISMATCH_PREPARE = "MOUNT_MISMATCH_PREPARE"
    MOUNT_TOPOLOGY_CHANGED_NO_EFFECT = "MOUNT_TOPOLOGY_CHANGED_NO_EFFECT"
    MOUNT_TOPOLOGY_CHANGED_AFTER_EFFECT = "MOUNT_TOPOLOGY_CHANGED_AFTER_EFFECT"
    UNSUPPORTED_REVERSIBLE_DIRECTORY_OPERATION = "UNSUPPORTED_REVERSIBLE_DIRECTORY_OPERATION"
    CUTOVER_BLOCKED_LEGACY_PROCESS = "CUTOVER_BLOCKED_LEGACY_PROCESS"
    OWNERSHIP_KEY_UNAVAILABLE = "OWNERSHIP_KEY_UNAVAILABLE"
    STALE_KEY_BACKUP = "STALE_KEY_BACKUP"
    BUDGET_EXCEEDED_BEFORE_PREPARE = "BUDGET_EXCEEDED_BEFORE_PREPARE"
    AUTHORITY_NAMESPACE_EXPOSURE_DETECTED = "AUTHORITY_NAMESPACE_EXPOSURE_DETECTED"
    AUTHORITY_GIT_INDEX_CONTAMINATED = "AUTHORITY_GIT_INDEX_CONTAMINATED"
    CURSOR_STALE_EPOCH = "CURSOR_STALE_EPOCH"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


seal_canonical_enum(FailureCode)


_FAILURE_MESSAGES = {code: code.value.replace("_", " ").title() for code in FailureCode}


@dataclass(frozen=True, slots=True)
class SafeFailureContext:
    """Closed, structurally safe failure metadata; no caller text is serializable."""

    operation_id: OperationId | None = None
    mutation_id: MutationId | None = None
    workspace_id: WorkspaceId | None = None
    recovery_decision_id: RecoveryDecisionId | None = None
    expected_epoch: AuthorityEpoch | None = None
    observed_epoch: AuthorityEpoch | None = None
    generation: int | None = None
    family: KeyFamily | None = None
    certificate_digest: str | None = None
    redacted_metadata_digest: str | None = None

    def __post_init__(self) -> None:
        typed = (
            (self.operation_id, OperationId, "operation_id"),
            (self.mutation_id, MutationId, "mutation_id"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.recovery_decision_id, RecoveryDecisionId, "recovery_decision_id"),
            (self.expected_epoch, AuthorityEpoch, "expected_epoch"),
            (self.observed_epoch, AuthorityEpoch, "observed_epoch"),
            (self.family, KeyFamily, "family"),
        )
        for value, expected_type, field_name in typed:
            if value is not None:
                require_exact_authority_value(value, expected_type, field_name)
        if self.generation is not None:
            require_positive(self.generation, "generation")
        if self.certificate_digest is not None:
            require_digest(self.certificate_digest, "certificate_digest")
        if self.redacted_metadata_digest is not None:
            require_digest(self.redacted_metadata_digest, "redacted_metadata_digest")

    def to_record(self) -> dict[str, Any]:
        values = {
            "certificate_digest": self.certificate_digest,
            "expected_epoch": (
                None if self.expected_epoch is None else _canonical_counter_value(self.expected_epoch)
            ),
            "family": None if self.family is None else _canonical_enum_value(self.family),
            "generation": self.generation,
            "mutation_id": (
                None if self.mutation_id is None else _require_owned_identity_text(self.mutation_id, MutationId)
            ),
            "observed_epoch": (
                None if self.observed_epoch is None else _canonical_counter_value(self.observed_epoch)
            ),
            "operation_id": (
                None if self.operation_id is None else _require_owned_identity_text(self.operation_id, OperationId)
            ),
            "recovery_decision_id": (
                None
                if self.recovery_decision_id is None
                else _require_owned_identity_text(self.recovery_decision_id, RecoveryDecisionId)
            ),
            "redacted_metadata_digest": self.redacted_metadata_digest,
            "workspace_id": (
                None if self.workspace_id is None else _require_owned_identity_text(self.workspace_id, WorkspaceId)
            ),
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class AuthorityFailure:
    code: FailureCode
    retryable: bool
    safe_context: SafeFailureContext = SafeFailureContext()

    def __post_init__(self) -> None:
        if not isinstance(self.code, FailureCode) or not isinstance(self.retryable, bool):
            raise TypeError("authority failures require a typed code and boolean retryability")
        if not isinstance(self.safe_context, SafeFailureContext):
            raise TypeError("safe_context must be SafeFailureContext")

    def to_safe_record(self) -> dict[str, Any]:
        return {
            "code": _canonical_enum_value(self.code),
            "message": _FAILURE_MESSAGES[self.code],
            "retryable": self.retryable,
            "context": self.safe_context.to_record(),
        }


__all__ = [
    "JOURNAL_EFFECT_RECEIPT_PHASE0_STATUS",
    "AuthorityBudgetProfile",
    "AuthorityBudgetRequest",
    "AuthorityBudgetReservation",
    "AuthorityFailure",
    "BackupGenerationContract",
    "BackupGenerationState",
    "BackupManifestV3",
    "BackupPredecessor",
    "BackupPredecessorKind",
    "CapabilityEvidenceState",
    "DormantKeyAuthority",
    "EnrollmentClaimV3",
    "FailureCode",
    "KeyFamily",
    "KeyFamilyPhase0Status",
    "KeyGenerationMetadata",
    "KeyReference",
    "KeyRingMetadata",
    "KeyState",
    "MountCertificate",
    "MountPrimitive",
    "RequiredKeyReference",
    "SafeFailureContext",
    "SigningKeyCapability",
    "ValidatedBackupClosure",
    "VerificationKeyCapability",
    "WorkspaceUserSnapshot",
    "key_family_phase0_status",
    "require_signing_key_capability",
    "require_verification_domain_authority",
    "require_verification_key_capability",
    "sign_with_key_capability",
    "validate_backup_transition",
    "verify_with_key_capability",
]
