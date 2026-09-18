"""Closed opaque identities and their explicit Phase-0 issuance paths.

Identity strings are data, not authority.  Every accepted identity instance is exact-type
and owned by this module's issuance registry.  New authority roots come only from bound
provenance; stored identities come only from a field-bound authenticated decode proof,
issued only to a key authority entitled to the authority domain that record names.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import InitVar, dataclass
from typing import Any, ClassVar, TypeVar

from core.workspace_authority_v3.canonical import (
    MAX_SIGNED_64,
    canonical_bytes,
    parse_strict_json,
    typed_sha256,
)

_OPAQUE_SUFFIX_RE = re.compile(r"[0-9a-f]{64}\Z")
_TURN_ID_RE = re.compile(r"turn_[0-9a-f]{64}\Z")
_IDENTITY_PREFIX_RE = re.compile(r"[a-z]+(?:-[a-z]+)*\Z")
IdentityT = TypeVar("IdentityT", bound="OpaqueIdentity")


def _build_canonical_specification() -> tuple[Any, ...]:
    """Own the immutable canonical specification every authority-bearing derivation reads.

    Field descriptors, identity prefixes, counter bounds, and enum values are captured
    when the type is defined and are never re-read from the caller-visible class
    afterwards.  Replacing ``PREFIX``, ``MINIMUM``, ``_value_``, ``__str__``, ``__int__``,
    or a shadowing field descriptor later therefore cannot change an accepted authority
    value; the public attributes survive only as informational aliases.
    """

    descriptors: dict[tuple[type, str], Any] = {}
    slots_sealed = False
    identity_prefixes: dict[type, str] = {}
    counter_bounds: dict[type, tuple[int, int]] = {}
    enum_values: dict[int, tuple[object, Any]] = {}

    def seal_slots(entries: tuple[tuple[type, str], ...]) -> None:
        nonlocal slots_sealed
        if slots_sealed:
            raise TypeError("canonical field descriptors are already sealed")
        for owner, name in entries:
            descriptor = owner.__dict__.get(name)
            if descriptor is None or not hasattr(descriptor, "__get__"):
                raise TypeError(f"{owner.__name__}.{name} is not a readable field descriptor")
            descriptors[(owner, name)] = descriptor
        slots_sealed = True

    def read_slot(owner: type, name: str, instance: object) -> Any:
        descriptor = descriptors.get((owner, name))
        if descriptor is None:
            raise TypeError(f"{owner.__name__}.{name} is not a sealed canonical field")
        if not isinstance(instance, owner):
            raise TypeError(f"canonical field read requires a {owner.__name__} instance")
        return descriptor.__get__(instance, type(instance))

    def seal_identity_type(identity_type: type, prefix: object) -> None:
        if identity_type in identity_prefixes:
            raise TypeError(f"{identity_type.__name__} canonical prefix is already sealed")
        if not isinstance(prefix, str) or not _IDENTITY_PREFIX_RE.fullmatch(prefix):
            raise TypeError("opaque authority identities require a fixed canonical prefix")
        if prefix in set(identity_prefixes.values()):
            raise TypeError(f"canonical identity prefix {prefix!r} is already claimed")
        identity_prefixes[identity_type] = prefix

    def identity_prefix(identity_type: object) -> str:
        prefix = identity_prefixes.get(identity_type)  # type: ignore[arg-type]
        if prefix is None:
            name = getattr(identity_type, "__name__", identity_type)
            raise TypeError(f"{name} has no sealed canonical identity prefix")
        return prefix

    def seal_counter_type(counter_type: type, minimum: object, maximum: object) -> None:
        if counter_type in counter_bounds:
            raise TypeError(f"{counter_type.__name__} canonical bounds are already sealed")
        for bound in (minimum, maximum):
            if type(bound) is not int:
                raise TypeError("authority counter bounds must be exact integers")
        if not 0 <= minimum <= maximum <= MAX_SIGNED_64:  # type: ignore[operator]
            raise TypeError("authority counter bounds must be an ordered signed-64 range")
        counter_bounds[counter_type] = (minimum, maximum)  # type: ignore[assignment]

    def counter_range(counter_type: object) -> tuple[int, int]:
        bounds = counter_bounds.get(counter_type)  # type: ignore[arg-type]
        if bounds is None:
            name = getattr(counter_type, "__name__", counter_type)
            raise TypeError(f"{name} has no sealed canonical counter bounds")
        return bounds

    def seal_enum(enum_class: type) -> None:
        members = tuple(enum_class)  # type: ignore[call-overload]
        if not members:
            raise TypeError(f"{enum_class.__name__} declares no canonical members")
        for member in members:
            if id(member) in enum_values:
                raise TypeError(f"{enum_class.__name__} canonical values are already sealed")
        for member in members:
            enum_values[id(member)] = (member, member.__dict__["_value_"])

    def enum_value(member: object) -> Any:
        entry = enum_values.get(id(member))
        if entry is None or entry[0] is not member:
            name = type(member).__name__
            raise TypeError(f"{name} member has no sealed canonical value")
        return entry[1]

    return (
        seal_slots,
        read_slot,
        seal_identity_type,
        identity_prefix,
        seal_counter_type,
        counter_range,
        seal_enum,
        enum_value,
    )


(
    _seal_canonical_slots,
    _read_canonical_slot,
    _seal_canonical_identity_type,
    _canonical_identity_prefix,
    _seal_canonical_counter_type,
    _canonical_counter_range,
    seal_canonical_enum,
    _canonical_enum_value,
) = _build_canonical_specification()


@dataclass(frozen=True, slots=True)
class _IdentityOwnership:
    source: str
    record_type: str | None = None
    record_digest: str | None = None
    source_field: str | None = None
    authority_domain_id: str | None = None
    workspace_id: str | None = None
    incarnation_id: str | None = None
    enrollment_operation_id: str | None = None


def _build_identity_owner(
    read_slot: Any,
    identity_prefix: Any,
    counter_range: Any,
    enum_value: Any,
) -> tuple[Any, ...]:
    construction_token = object()
    identities: dict[int, tuple[object, _IdentityOwnership, type[object], str]] = {}
    decode_proofs: dict[int, tuple[object, tuple[object, ...]]] = {}

    def proof_snapshot(proof: AuthenticatedIdentityDecode) -> tuple[object, ...]:
        return (
            proof.record_type,
            proof.record_digest,
            proof.source_field,
            proof.identity_type,
            proof.identity_value,
            proof.authority_domain_id,
            proof.workspace_id,
            proof.incarnation_id,
            proof.enrollment_operation_id,
        )

    def construct(
        identity_type: type[IdentityT],
        value: str,
        ownership: _IdentityOwnership,
    ) -> IdentityT:
        identity = identity_type(value, construction_token)
        identities[id(identity)] = (identity, ownership, identity_type, value)
        return identity

    def constructor_is_owned(token: object | None) -> bool:
        return token is construction_token

    def require_identity(value: object, expected_type: type[IdentityT]) -> _IdentityOwnership:
        require_identity_text(value, expected_type)
        return identities[id(value)][1]

    def require_identity_text(value: object, expected_type: type[IdentityT]) -> str:
        """Return the identity text this owner issued, read only from closure state."""

        if type(value) is not expected_type:
            raise TypeError(f"value must be an exact owned {expected_type.__name__}")
        entry = identities.get(id(value))
        if entry is None or entry[0] is not value:
            raise TypeError(f"{expected_type.__name__} was not issued by the identity owner")
        if entry[2] is not expected_type or read_slot(OpaqueIdentity, "value", value) != entry[3]:
            raise TypeError(f"{expected_type.__name__} changed after identity issuance")
        OpaqueIdentity.__post_init__(value, construction_token)
        return entry[3]

    def issue_decode_proof(
        *,
        verification_key: object,
        source_record: bytes,
        supplied_mac: str,
        record_type: str,
        source_field: str,
        identity_type: type[IdentityT],
    ) -> AuthenticatedIdentityDecode:
        from core.workspace_authority_v3.base import validate_dormant_envelope
        from core.workspace_authority_v3.contracts import (
            KeyFamily,
            VerificationKeyCapability,
            require_verification_domain_authority,
            require_verification_key_capability,
            verify_with_key_capability,
        )

        if type(verification_key) is not VerificationKeyCapability:
            raise TypeError("authenticated identity decode requires an exact verification capability")
        if not isinstance(source_record, bytes):
            raise TypeError("authenticated identity source record must be bytes")
        try:
            raw = parse_strict_json(source_record)
        except ValueError as exc:
            raise ValueError("authenticated identity source record is not canonical JSON") from exc
        if not isinstance(raw, dict) or canonical_bytes(raw) != source_record:
            raise ValueError("authenticated identity source record is not exact canonical JSON")
        if record_type == "EnrollmentClaimV3":
            family = KeyFamily.CLAIM_ENROLLMENT
            mac_domain = "VOOL_ENROLLMENT_CLAIM_MAC_V3"
            record_digest = typed_sha256("VOOL_ENROLLMENT_CLAIM_V3", source_record)
            mac_message = record_digest.encode("ascii")
            declares_authority_domain = True
            permitted_fields = {
                "authority_domain_id": AuthorityDomainId,
                "enrollment_operation_id": EnrollmentOperationId,
                "incarnation_id": IncarnationId,
                "runtime_home_id": RuntimeHomeId,
                "workspace_id": WorkspaceId,
            }
        elif record_type == "AuthenticatedCursorPayload":
            family = KeyFamily.CURSOR
            mac_domain = "VOOL_AUTHENTICATED_CURSOR_V3"
            record_digest = typed_sha256("VOOL_CURSOR_DECODE_PAYLOAD_V3", source_record)
            mac_message = source_record
            declares_authority_domain = False
            permitted_fields = {
                "incarnation_id": IncarnationId,
                "workspace_id": WorkspaceId,
            }
        else:
            raise TypeError("authenticated identity source record type is not supported")
        if permitted_fields.get(source_field) is not identity_type:
            raise TypeError("authenticated identity source field/type binding is invalid")
        record_payload = validate_dormant_envelope(raw, record_type=record_type, schema_version=3)
        verification_reference = require_verification_key_capability(
            verification_key,
            expected_family=family,
        )
        if not verify_with_key_capability(
            verification_key,
            expected_family=family,
            domain=mac_domain,
            message=mac_message,
            supplied_mac=supplied_mac,
        ):
            raise ValueError("authenticated identity source MAC is invalid")
        if record_type == "EnrollmentClaimV3" and (
            record_payload.get("authority_key_id") != verification_reference.key_id
            or record_payload.get("key_generation") != verification_reference.generation
        ):
            raise ValueError("authenticated identity source key reference is invalid")
        if record_type == "AuthenticatedCursorPayload" and (
            record_payload.get("key_generation") != verification_reference.generation
        ):
            raise ValueError("authenticated identity source key generation is invalid")
        identity_value = record_payload.get(source_field)
        authority_domain_id = record_payload.get("authority_domain_id")
        workspace_id = record_payload.get("workspace_id")
        incarnation_id = record_payload.get("incarnation_id")
        enrollment_operation_id = record_payload.get("enrollment_operation_id")
        expected_prefix = f"{identity_prefix(identity_type)}_"
        if not isinstance(identity_value, str) or not identity_value.startswith(expected_prefix):
            raise ValueError("authenticated identity bytes do not match their exact identity type")
        if not _OPAQUE_SUFFIX_RE.fullmatch(identity_value[len(expected_prefix) :]):
            raise ValueError("authenticated identity bytes are not canonical")
        contexts = {
            "authority_domain_id": (authority_domain_id, identity_prefix(AuthorityDomainId)),
            "workspace_id": (workspace_id, identity_prefix(WorkspaceId)),
            "incarnation_id": (incarnation_id, identity_prefix(IncarnationId)),
            "enrollment_operation_id": (
                enrollment_operation_id,
                identity_prefix(EnrollmentOperationId),
            ),
        }
        for field_name, (context_value, prefix) in contexts.items():
            if context_value is None:
                continue
            expected_context_prefix = f"{prefix}_"
            if (
                not isinstance(context_value, str)
                or not context_value.startswith(expected_context_prefix)
                or not _OPAQUE_SUFFIX_RE.fullmatch(context_value[len(expected_context_prefix) :])
            ):
                raise ValueError(f"authenticated identity source has invalid {field_name}")
            if source_field == field_name and identity_value != context_value:
                raise ValueError(f"authenticated identity bytes do not match bound {field_name}")
        # Provenance, not just self-consistency: a MAC only proves that *some* key
        # authority signed these bytes.  The authority domain a record names is authority
        # this decode would hand out, so the verifying ring must be entitled to it.  The
        # entitlement is read from the ring's own closed context, never from the record.
        ring_authority_domain = require_verification_domain_authority(
            verification_key,
            expected_family=family,
        )
        if declares_authority_domain and authority_domain_id is None:
            raise ValueError("authenticated identity source has invalid authority_domain_id")
        if authority_domain_id is not None and authority_domain_id != ring_authority_domain:
            raise ValueError(
                "authenticated identity source names an authority domain this key authority "
                "is not entitled to authenticate"
            )
        # Records that name no authority domain of their own (the cursor payload) still
        # carry the authenticating ring's domain forward, so every decoded identity states
        # which authority vouched for it rather than leaving that provenance blank.
        authority_domain_id = ring_authority_domain
        proof = object.__new__(AuthenticatedIdentityDecode)
        object.__setattr__(proof, "record_type", record_type)
        object.__setattr__(proof, "record_digest", record_digest)
        object.__setattr__(proof, "source_field", source_field)
        object.__setattr__(proof, "identity_type", identity_type)
        object.__setattr__(proof, "identity_value", identity_value)
        object.__setattr__(proof, "authority_domain_id", authority_domain_id)
        object.__setattr__(proof, "workspace_id", workspace_id)
        object.__setattr__(proof, "incarnation_id", incarnation_id)
        object.__setattr__(proof, "enrollment_operation_id", enrollment_operation_id)
        decode_proofs[id(proof)] = (proof, proof_snapshot(proof))
        return proof

    def consume_decode_proof(
        proof: object,
        expected_type: type[IdentityT],
    ) -> IdentityT:
        if type(proof) is not AuthenticatedIdentityDecode:
            raise TypeError("trusted identity decode requires an exact AuthenticatedIdentityDecode")
        issued = decode_proofs.pop(id(proof), None)
        if issued is None or issued[0] is not proof:
            raise TypeError("authenticated identity decode proof is unissued or already consumed")
        if proof_snapshot(proof) != issued[1]:
            raise TypeError("authenticated identity decode proof changed after issuance")
        if proof.identity_type is not expected_type:
            raise TypeError("authenticated identity decode proof is bound to another identity type")
        ownership = _IdentityOwnership(
            source="authenticated_storage",
            record_type=proof.record_type,
            record_digest=proof.record_digest,
            source_field=proof.source_field,
            authority_domain_id=proof.authority_domain_id,
            workspace_id=proof.workspace_id,
            incarnation_id=proof.incarnation_id,
            enrollment_operation_id=proof.enrollment_operation_id,
        )
        return construct(expected_type, proof.identity_value, ownership)

    def new_random_identity(identity_type: type[IdentityT]) -> IdentityT:
        if identity_type in {AuthorityDomainId, WorkspaceId}:
            raise TypeError(f"{identity_type.__name__} requires its trusted provenance factory")
        return construct(
            identity_type,
            f"{identity_prefix(identity_type)}_{secrets.token_hex(32)}",
            _IdentityOwnership(source="cryptographic_randomness"),
        )

    def authority_domain_from_claim(provenance: object) -> AuthorityDomainId:
        trusted = _require_authority_claim(provenance)
        return construct(
            AuthorityDomainId,
            trusted._authority_domain_value,
            _IdentityOwnership(
                source="authority_claim",
                authority_domain_id=trusted._authority_domain_value,
            ),
        )

    def authority_domain_anchor(anchor: object, workspace_id: object) -> str:
        """Durable authority-domain anchor text for one logical key ring.

        A key ring is entitled to authenticate exactly one authority domain, and that
        entitlement has to survive a restart, so the anchor is *text*, not an owned
        ``AuthorityDomainId`` object: requiring the object would mean re-minting the very
        identity whose authenticated decode the anchor is there to authorize.

        Three accepted forms, narrowest first:

        ``AuthorityDomainId``
            The enrollment path.  The text comes from the closed owner, so the anchor is
            rooted in a genuine ``AuthorityClaimProvenance``.
        ``str``
            The restart/storage-reconstruction path.  Canonical authority-domain text
            read back from storage, validated against the sealed prefix and suffix.
        ``None``
            Taken from the ring workspace's own owner-recorded enrollment domain, which
            is closure state this module wrote when that workspace was issued or decoded.
        """

        if anchor is None:
            recorded = require_identity(workspace_id, WorkspaceId).authority_domain_id
            if recorded is None:
                raise TypeError(
                    "ring workspace carries no authority-domain provenance to anchor a key ring"
                )
            return recorded
        if type(anchor) is AuthorityDomainId:
            return require_identity_text(anchor, AuthorityDomainId)
        expected_prefix = f"{identity_prefix(AuthorityDomainId)}_"
        if (
            not isinstance(anchor, str)
            or not anchor.startswith(expected_prefix)
            or not _OPAQUE_SUFFIX_RE.fullmatch(anchor[len(expected_prefix) :])
        ):
            raise TypeError(
                "authority-domain anchor must be an owned AuthorityDomainId or canonical stored text"
            )
        return anchor

    def workspace_from_enrollment(provenance: object) -> WorkspaceId:
        trusted = _require_workspace_enrollment(provenance)
        return construct(
            WorkspaceId,
            trusted._workspace_value,
            _IdentityOwnership(
                source="workspace_enrollment",
                authority_domain_id=require_identity_text(
                    trusted.authority_domain_id,
                    AuthorityDomainId,
                ),
                workspace_id=trusted._workspace_value,
                enrollment_operation_id=require_identity_text(
                    trusted.enrollment_operation_id,
                    EnrollmentOperationId,
                ),
            ),
        )

    def counter_value(counter: object) -> int:
        """Authority-bearing counter value, from the sealed field and sealed bounds.

        The stored value must be an exact ``int``.  An ``int`` subclass would carry its
        own comparison behaviour through this return value into every authority
        comparison built on it, and would decide the range check below itself.
        """

        if not isinstance(counter, _BoundedCounter) or type(counter) is _BoundedCounter:
            raise TypeError("canonical counter value requires an authority counter")
        value = read_slot(_BoundedCounter, "value", counter)
        if type(value) is not int:
            raise TypeError(f"{type(counter).__name__} must be an exact integer")
        minimum, maximum = counter_range(type(counter))
        if not minimum <= value <= maximum:
            raise ValueError(f"{type(counter).__name__} is outside its sealed range")
        return value

    def invocation_fields(invocation: object) -> tuple[str, int, int]:
        if type(invocation) is not InvocationIdentity:
            raise TypeError("canonical invocation derivation requires an exact InvocationIdentity")
        canonical_turn_id = read_slot(InvocationIdentity, "canonical_turn_id", invocation)
        controller_generation = read_slot(
            InvocationIdentity,
            "controller_generation",
            invocation,
        )
        persisted_tool_ordinal = read_slot(
            InvocationIdentity,
            "persisted_tool_ordinal",
            invocation,
        )
        if not isinstance(canonical_turn_id, str) or not _TURN_ID_RE.fullmatch(canonical_turn_id):
            raise ValueError("canonical_turn_id must be an opaque canonical turn identity")
        for name, value in (
            ("controller_generation", controller_generation),
            ("persisted_tool_ordinal", persisted_tool_ordinal),
        ):
            # Same exact-primitive rule as the sealed counters: these ordinals are
            # authority-bearing inputs to the canonical invocation payload.
            if type(value) is not int or not 0 <= value <= MAX_SIGNED_64:
                raise ValueError(f"{name} must be a non-negative signed-64 integer")
        return canonical_turn_id, controller_generation, persisted_tool_ordinal

    def invocation_payload(invocation: object) -> dict[str, object]:
        canonical_turn_id, controller_generation, persisted_tool_ordinal = invocation_fields(
            invocation
        )
        return {
            "canonical_turn_id": canonical_turn_id,
            "controller_generation": controller_generation,
            "persisted_tool_ordinal": persisted_tool_ordinal,
        }

    def invocation_digest(invocation: object) -> str:
        return typed_sha256(
            "VOOL_INVOCATION_IDENTITY_V3",
            canonical_bytes(invocation_payload(invocation)),
        )

    def mutation_digest(invocation: object) -> str:
        return typed_sha256(
            "VOOL_MUTATION_IDENTITY_V3",
            canonical_bytes(invocation_payload(invocation)),
        )

    def binding_fields(
        binding: object,
    ) -> tuple[InvocationIdentity, WorkspaceId, AuthorityEpoch, str]:
        if type(binding) is not OperationIdentityBinding:
            raise TypeError(
                "canonical operation derivation requires an exact OperationIdentityBinding"
            )
        invocation = read_slot(OperationIdentityBinding, "invocation_identity", binding)
        workspace_id = read_slot(OperationIdentityBinding, "workspace_id", binding)
        authority_epoch = read_slot(OperationIdentityBinding, "authority_epoch", binding)
        canonical_action_digest = read_slot(
            OperationIdentityBinding,
            "canonical_action_digest",
            binding,
        )
        invocation_fields(invocation)
        require_identity_text(workspace_id, WorkspaceId)
        if type(authority_epoch) is not AuthorityEpoch:
            raise TypeError("operation identity requires an exact authority epoch")
        counter_value(authority_epoch)
        if not isinstance(canonical_action_digest, str) or not _OPAQUE_SUFFIX_RE.fullmatch(
            canonical_action_digest
        ):
            raise ValueError("canonical_action_digest must be a lowercase SHA-256 digest")
        return invocation, workspace_id, authority_epoch, canonical_action_digest

    def binding_payload(binding: object) -> dict[str, object]:
        invocation, workspace_id, authority_epoch, action_digest = binding_fields(binding)
        return {
            "authority_epoch": counter_value(authority_epoch),
            "canonical_action_digest": action_digest,
            "invocation_identity": invocation_payload(invocation),
            "workspace_id": require_identity_text(workspace_id, WorkspaceId),
        }

    def binding_digest(binding: object) -> str:
        return typed_sha256(
            "VOOL_OPERATION_IDENTITY_BINDING_V3",
            canonical_bytes(binding_payload(binding)),
        )

    def stable_invocation_identity(
        invocation: InvocationIdentity,
        identity_type: type[OperationId] | type[MutationId],
    ) -> OperationId | MutationId:
        if identity_type is OperationId:
            digest = invocation_digest(invocation)
        elif identity_type is MutationId:
            digest = mutation_digest(invocation)
        else:
            raise TypeError("stable invocation can issue only OperationId or MutationId")
        return construct(
            identity_type,
            f"{identity_prefix(identity_type)}_{digest}",
            _IdentityOwnership(source="stable_invocation"),
        )

    def binding_operation_id(binding: object) -> OperationId:
        invocation, _workspace_id, _epoch, _action = binding_fields(binding)
        return stable_invocation_identity(invocation, OperationId)

    def binding_mutation_id(binding: object) -> MutationId:
        invocation, _workspace_id, _epoch, _action = binding_fields(binding)
        return stable_invocation_identity(invocation, MutationId)

    return (
        constructor_is_owned,
        require_identity,
        require_identity_text,
        issue_decode_proof,
        consume_decode_proof,
        new_random_identity,
        authority_domain_from_claim,
        authority_domain_anchor,
        workspace_from_enrollment,
        stable_invocation_identity,
        counter_value,
        invocation_fields,
        invocation_payload,
        invocation_digest,
        mutation_digest,
        binding_fields,
        binding_payload,
        binding_digest,
        binding_operation_id,
        binding_mutation_id,
    )


(
    _identity_constructor_is_owned,
    _require_identity_ownership,
    _require_owned_identity_text,
    _issue_authenticated_identity_decode,
    _consume_authenticated_identity_decode,
    _new_random_owned_identity,
    _authority_domain_from_owned_claim,
    _authority_domain_ring_anchor,
    _workspace_from_owned_enrollment,
    _stable_invocation_owned_identity,
    _canonical_counter_value,
    _canonical_invocation_fields,
    _canonical_invocation_payload,
    _canonical_invocation_digest,
    _canonical_mutation_digest,
    _canonical_operation_binding_fields,
    _canonical_operation_binding_payload,
    _canonical_operation_binding_digest,
    _canonical_binding_operation_id,
    _canonical_binding_mutation_id,
) = _build_identity_owner(
    _read_canonical_slot,
    _canonical_identity_prefix,
    _canonical_counter_range,
    _canonical_enum_value,
)


# Public canonical helpers are wrappers only.  Trusted code inside this package calls the
# private closure-captured primitives above, so replacing an exported name here cannot
# change an accepted authority value.


def canonical_identity_text(value: object, expected_type: type[IdentityT]) -> str:
    """Authority-bearing identity text, taken from the closed owner, never via ``__str__``."""

    return _require_owned_identity_text(value, expected_type)


def canonical_counter_value(counter: object) -> int:
    """Authority-bearing counter value, from the sealed field and sealed bounds."""

    return _canonical_counter_value(counter)


def authority_domain_ring_anchor(anchor: object, workspace_id: object) -> str:
    """Durable authority-domain anchor text for one logical key ring."""

    return _authority_domain_ring_anchor(anchor, workspace_id)


def canonical_enum_value(member: object) -> Any:
    """Authority-bearing enum value captured when the enum was sealed."""

    return _canonical_enum_value(member)


def canonical_invocation_fields(invocation: object) -> tuple[str, int, int]:
    return _canonical_invocation_fields(invocation)


def canonical_invocation_payload(invocation: object) -> dict[str, object]:
    return _canonical_invocation_payload(invocation)


def canonical_invocation_digest(invocation: object) -> str:
    return _canonical_invocation_digest(invocation)


def canonical_mutation_digest(invocation: object) -> str:
    return _canonical_mutation_digest(invocation)


def canonical_operation_binding_fields(
    binding: object,
) -> tuple[InvocationIdentity, WorkspaceId, AuthorityEpoch, str]:
    return _canonical_operation_binding_fields(binding)


def canonical_operation_binding_payload(binding: object) -> dict[str, object]:
    return _canonical_operation_binding_payload(binding)


def canonical_operation_binding_digest(binding: object) -> str:
    return _canonical_operation_binding_digest(binding)


def canonical_binding_operation_id(binding: object) -> OperationId:
    return _canonical_binding_operation_id(binding)


def canonical_binding_mutation_id(binding: object) -> MutationId:
    return _canonical_binding_mutation_id(binding)


class AuthenticatedIdentityDecode:
    """One-use proof bound to one exact identity field in one authenticated record."""

    __slots__ = (
        "authority_domain_id",
        "enrollment_operation_id",
        "identity_type",
        "identity_value",
        "incarnation_id",
        "record_digest",
        "record_type",
        "source_field",
        "workspace_id",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("authenticated identity decode proofs are minted by authenticated record decoders")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AuthenticatedIdentityDecode is final")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("authenticated identity decode proofs are immutable")


def require_owned_identity(value: object, expected_type: type[IdentityT]) -> IdentityT:
    """Require an exact identity instance issued by this module's closed owner."""

    _require_identity_ownership(value, expected_type)
    return value  # type: ignore[return-value]


def require_exact_authority_value(value: object, expected_type: type[Any], field_name: str) -> Any:
    """Shared exact-type guard; opaque identities additionally require owner issuance."""

    if issubclass(expected_type, OpaqueIdentity):
        try:
            return require_owned_identity(value, expected_type)
        except TypeError as exc:
            raise TypeError(f"{field_name} must be an exact owned {expected_type.__name__}") from exc
    if type(value) is not expected_type:
        raise TypeError(f"{field_name} must be an exact {expected_type.__name__}")
    return value


def require_identity_context(
    value: object,
    expected_type: type[IdentityT],
    *,
    authority_domain_id: AuthorityDomainId | None = None,
    workspace_id: WorkspaceId | None = None,
    incarnation_id: IncarnationId | None = None,
    enrollment_operation_id: EnrollmentOperationId | None = None,
) -> IdentityT:
    ownership = _require_identity_ownership(value, expected_type)
    expected = {
        "authority_domain_id": (
            None
            if authority_domain_id is None
            else _require_owned_identity_text(authority_domain_id, AuthorityDomainId)
        ),
        "workspace_id": (
            None if workspace_id is None else _require_owned_identity_text(workspace_id, WorkspaceId)
        ),
        "incarnation_id": (
            None
            if incarnation_id is None
            else _require_owned_identity_text(incarnation_id, IncarnationId)
        ),
        "enrollment_operation_id": (
            None
            if enrollment_operation_id is None
            else _require_owned_identity_text(enrollment_operation_id, EnrollmentOperationId)
        ),
    }
    for field_name, expected_value in expected.items():
        if expected_value is not None and getattr(ownership, field_name) != expected_value:
            raise ValueError(f"{expected_type.__name__} provenance does not match {field_name}")
    return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class OpaqueIdentity:
    value: str
    _construction: InitVar[object | None] = None

    PREFIX: ClassVar[str] = "identity"

    def __post_init__(self, _construction: object | None) -> None:
        if not _identity_constructor_is_owned(_construction):
            raise TypeError(f"{type(self).__name__} must be issued by its closed identity owner")
        expected_prefix = f"{_canonical_identity_prefix(type(self))}_"
        value = _read_canonical_slot(OpaqueIdentity, "value", self)
        if not isinstance(value, str) or not value.startswith(expected_prefix):
            raise ValueError(f"{type(self).__name__} must start with {expected_prefix!r}")
        if not _OPAQUE_SUFFIX_RE.fullmatch(value[len(expected_prefix) :]):
            raise ValueError(f"{type(self).__name__} must contain a 256-bit lowercase opaque suffix")

    def __init_subclass__(cls, **kwargs: object) -> None:
        if cls.__bases__ != (OpaqueIdentity,):
            raise TypeError("opaque authority identity types are final and disallow multiple inheritance")
        declared = cls.__dict__.get("PREFIX")
        if not isinstance(declared, str):
            raise TypeError("opaque authority identities require a fixed prefix")
        _seal_canonical_identity_type(cls, declared)

    @classmethod
    def new(cls: type[IdentityT]) -> IdentityT:
        return _new_random_owned_identity(cls)

    @classmethod
    def _from_trusted_storage(
        cls: type[IdentityT],
        authentication: AuthenticatedIdentityDecode,
    ) -> IdentityT:
        """Consume a proof already bound to this exact identity type and stored value."""

        return _consume_authenticated_identity_decode(authentication, cls)

    def __str__(self) -> str:
        return self.value


def _build_provenance_owner(identity_prefix: Any) -> tuple[Any, ...]:
    claims: dict[int, tuple[object, bytes, str]] = {}
    enrollments: dict[int, tuple[object, bytes, object, object, str]] = {}

    def establish_claim() -> AuthorityClaimProvenance:
        provenance = object.__new__(AuthorityClaimProvenance)
        object.__setattr__(provenance, "_nonce", secrets.token_bytes(32))
        object.__setattr__(
            provenance,
            "_authority_domain_value",
            f"{identity_prefix(AuthorityDomainId)}_{secrets.token_hex(32)}",
        )
        claims[id(provenance)] = (
            provenance,
            provenance._nonce,
            provenance._authority_domain_value,
        )
        return provenance

    def require_claim(provenance: object) -> AuthorityClaimProvenance:
        issued = claims.get(id(provenance))
        if (
            type(provenance) is not AuthorityClaimProvenance
            or issued is None
            or issued[0] is not provenance
            or provenance._nonce != issued[1]
            or provenance._authority_domain_value != issued[2]
        ):
            raise TypeError("authority identity requires owner-issued AuthorityClaimProvenance")
        return provenance

    def establish_enrollment(
        authority_domain_id: AuthorityDomainId,
        enrollment_operation_id: EnrollmentOperationId,
    ) -> WorkspaceEnrollmentProvenance:
        require_owned_identity(authority_domain_id, AuthorityDomainId)
        require_owned_identity(enrollment_operation_id, EnrollmentOperationId)
        provenance = object.__new__(WorkspaceEnrollmentProvenance)
        object.__setattr__(provenance, "_nonce", secrets.token_bytes(32))
        object.__setattr__(provenance, "authority_domain_id", authority_domain_id)
        object.__setattr__(provenance, "enrollment_operation_id", enrollment_operation_id)
        object.__setattr__(
            provenance,
            "_workspace_value",
            f"{identity_prefix(WorkspaceId)}_{secrets.token_hex(32)}",
        )
        enrollments[id(provenance)] = (
            provenance,
            provenance._nonce,
            authority_domain_id,
            enrollment_operation_id,
            provenance._workspace_value,
        )
        return provenance

    def require_enrollment(provenance: object) -> WorkspaceEnrollmentProvenance:
        issued = enrollments.get(id(provenance))
        if (
            type(provenance) is not WorkspaceEnrollmentProvenance
            or issued is None
            or issued[0] is not provenance
            or provenance._nonce != issued[1]
            or provenance.authority_domain_id is not issued[2]
            or provenance.enrollment_operation_id is not issued[3]
            or provenance._workspace_value != issued[4]
        ):
            raise TypeError("workspace identity requires owner-issued WorkspaceEnrollmentProvenance")
        require_owned_identity(provenance.authority_domain_id, AuthorityDomainId)
        require_owned_identity(provenance.enrollment_operation_id, EnrollmentOperationId)
        return provenance

    return establish_claim, require_claim, establish_enrollment, require_enrollment


(
    _establish_authority_claim,
    _require_authority_claim,
    _establish_workspace_enrollment,
    _require_workspace_enrollment,
) = _build_provenance_owner(_canonical_identity_prefix)


class AuthorityClaimProvenance:
    """Closed capability binding one authority-claim event to one authority-domain ID."""

    __slots__ = ("_authority_domain_value", "_nonce")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("authority claim provenance is issued only by establish()")

    @classmethod
    def establish(cls) -> AuthorityClaimProvenance:
        if cls is not AuthorityClaimProvenance:
            raise TypeError("AuthorityClaimProvenance is final")
        return _establish_authority_claim()

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AuthorityClaimProvenance is final")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("authority claim provenance is immutable")


class WorkspaceEnrollmentProvenance:
    """Closed capability binding one enrollment event to one workspace ID."""

    __slots__ = (
        "_nonce",
        "_workspace_value",
        "authority_domain_id",
        "enrollment_operation_id",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("workspace enrollment provenance is issued only by establish()")

    @classmethod
    def establish(
        cls,
        *,
        authority_domain_id: AuthorityDomainId,
        enrollment_operation_id: EnrollmentOperationId,
    ) -> WorkspaceEnrollmentProvenance:
        if cls is not WorkspaceEnrollmentProvenance:
            raise TypeError("WorkspaceEnrollmentProvenance is final")
        return _establish_workspace_enrollment(authority_domain_id, enrollment_operation_id)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("WorkspaceEnrollmentProvenance is final")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("workspace enrollment provenance is immutable")


class AuthorityDomainId(OpaqueIdentity):
    PREFIX = "authority-domain"

    @classmethod
    def from_authority_claim(cls, provenance: AuthorityClaimProvenance) -> AuthorityDomainId:
        if cls is not AuthorityDomainId:
            raise TypeError("AuthorityDomainId is final")
        return _authority_domain_from_owned_claim(provenance)


class WorkspaceId(OpaqueIdentity):
    PREFIX = "workspace"

    @classmethod
    def from_enrollment(cls, provenance: WorkspaceEnrollmentProvenance) -> WorkspaceId:
        if cls is not WorkspaceId:
            raise TypeError("WorkspaceId is final")
        return _workspace_from_owned_enrollment(provenance)


class IncarnationId(OpaqueIdentity):
    PREFIX = "incarnation"


class RuntimeHomeId(OpaqueIdentity):
    PREFIX = "runtime-home"


class EnrollmentOperationId(OpaqueIdentity):
    PREFIX = "enrollment-operation"


class OperationId(OpaqueIdentity):
    PREFIX = "operation"


class MutationId(OpaqueIdentity):
    PREFIX = "mutation"


class ToolCallId(OpaqueIdentity):
    PREFIX = "tool-call"


class PermissionDecisionId(OpaqueIdentity):
    PREFIX = "permission-decision"


class BackupGenerationId(OpaqueIdentity):
    PREFIX = "backup-generation"


class RecoveryDecisionId(OpaqueIdentity):
    PREFIX = "recovery-decision"


class RecoveryCycleId(OpaqueIdentity):
    PREFIX = "recovery-cycle"


@dataclass(frozen=True, slots=True, order=True)
class _BoundedCounter:
    value: int
    MINIMUM: ClassVar[int] = 0

    def __post_init__(self) -> None:
        value = _read_canonical_slot(_BoundedCounter, "value", self)
        # Exact ``int`` only.  A subclass would decide the range check below through its
        # own comparison methods, and would then live on inside the counter.
        if type(value) is not int:
            raise TypeError(f"{type(self).__name__} must be an exact integer")
        minimum, maximum = _canonical_counter_range(type(self))
        if not minimum <= value <= maximum:
            raise ValueError(f"{type(self).__name__} is outside its sealed range")

    def __init_subclass__(cls, **kwargs: object) -> None:
        if cls.__bases__ != (_BoundedCounter,):
            raise TypeError("authority counter types are final and disallow multiple inheritance")
        _seal_canonical_counter_type(cls, cls.MINIMUM, MAX_SIGNED_64)

    def __int__(self) -> int:
        return self.value


class AuthorityEpoch(_BoundedCounter):
    MINIMUM = 1


class HeadRevision(_BoundedCounter):
    pass


class WorkspaceSequence(_BoundedCounter):
    pass


@dataclass(frozen=True, slots=True)
class InvocationIdentity:
    """Canonical invocation identity, deliberately independent of workspace scope."""

    canonical_turn_id: str
    controller_generation: int
    persisted_tool_ordinal: int

    def __post_init__(self) -> None:
        # Read through the sealed fields, never through late-bound attribute lookup: a
        # replaced descriptor must not be able to present an acceptable value while a
        # different one stays stored on the instance.
        canonical_turn_id = _read_canonical_slot(InvocationIdentity, "canonical_turn_id", self)
        if not isinstance(canonical_turn_id, str) or not _TURN_ID_RE.fullmatch(canonical_turn_id):
            raise ValueError("canonical_turn_id must be an opaque canonical turn identity")
        for name in ("controller_generation", "persisted_tool_ordinal"):
            value = _read_canonical_slot(InvocationIdentity, name, self)
            # Exact ``int`` only: a subclass would decide the range check through its own
            # comparison methods, and would then live on inside the invocation.
            if type(value) is not int or not 0 <= value <= MAX_SIGNED_64:
                raise ValueError(f"{name} must be a non-negative signed-64 integer")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("InvocationIdentity is final")

    def canonical_payload(self) -> dict[str, object]:
        return canonical_invocation_payload(self)

    def digest(self) -> str:
        return canonical_invocation_digest(self)

    def operation_id(self) -> OperationId:
        return _stable_invocation_owned_identity(self, OperationId)

    def mutation_id(self) -> MutationId:
        return _stable_invocation_owned_identity(self, MutationId)


@dataclass(frozen=True, slots=True)
class OperationIdentityBinding:
    """Scope/action binding for one canonical invocation."""

    invocation_identity: InvocationIdentity
    workspace_id: WorkspaceId
    authority_epoch: AuthorityEpoch
    canonical_action_digest: str

    def __post_init__(self) -> None:
        # Same sealed-field rule as InvocationIdentity above: validate what is stored, not
        # what a replaced descriptor chooses to return.
        invocation_identity = _read_canonical_slot(
            OperationIdentityBinding,
            "invocation_identity",
            self,
        )
        workspace_id = _read_canonical_slot(OperationIdentityBinding, "workspace_id", self)
        authority_epoch = _read_canonical_slot(OperationIdentityBinding, "authority_epoch", self)
        canonical_action_digest = _read_canonical_slot(
            OperationIdentityBinding,
            "canonical_action_digest",
            self,
        )
        if type(invocation_identity) is not InvocationIdentity:
            raise TypeError("operation binding requires an exact invocation identity")
        InvocationIdentity.__post_init__(invocation_identity)
        require_owned_identity(workspace_id, WorkspaceId)
        if type(authority_epoch) is not AuthorityEpoch:
            raise TypeError("operation identity requires an exact authority epoch")
        AuthorityEpoch.__post_init__(authority_epoch)
        if not isinstance(canonical_action_digest, str) or not _OPAQUE_SUFFIX_RE.fullmatch(
            canonical_action_digest
        ):
            raise ValueError("canonical_action_digest must be a lowercase SHA-256 digest")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("OperationIdentityBinding is final")

    def canonical_payload(self) -> dict[str, object]:
        return canonical_operation_binding_payload(self)

    def digest(self) -> str:
        return canonical_operation_binding_digest(self)

    def operation_id(self) -> OperationId:
        return canonical_binding_operation_id(self)

    def mutation_id(self) -> MutationId:
        return canonical_binding_mutation_id(self)


_seal_canonical_slots(
    (
        (OpaqueIdentity, "value"),
        (_BoundedCounter, "value"),
        (InvocationIdentity, "canonical_turn_id"),
        (InvocationIdentity, "controller_generation"),
        (InvocationIdentity, "persisted_tool_ordinal"),
        (OperationIdentityBinding, "invocation_identity"),
        (OperationIdentityBinding, "workspace_id"),
        (OperationIdentityBinding, "authority_epoch"),
        (OperationIdentityBinding, "canonical_action_digest"),
    )
)


__all__ = [
    "AuthenticatedIdentityDecode",
    "AuthorityClaimProvenance",
    "AuthorityDomainId",
    "AuthorityEpoch",
    "BackupGenerationId",
    "EnrollmentOperationId",
    "HeadRevision",
    "IncarnationId",
    "InvocationIdentity",
    "MutationId",
    "OpaqueIdentity",
    "OperationId",
    "OperationIdentityBinding",
    "PermissionDecisionId",
    "RecoveryCycleId",
    "RecoveryDecisionId",
    "RuntimeHomeId",
    "ToolCallId",
    "WorkspaceEnrollmentProvenance",
    "WorkspaceId",
    "WorkspaceSequence",
    "authority_domain_ring_anchor",
    "canonical_binding_mutation_id",
    "canonical_binding_operation_id",
    "canonical_counter_value",
    "canonical_enum_value",
    "canonical_identity_text",
    "canonical_invocation_digest",
    "canonical_invocation_fields",
    "canonical_invocation_payload",
    "canonical_mutation_digest",
    "canonical_operation_binding_digest",
    "canonical_operation_binding_fields",
    "canonical_operation_binding_payload",
    "require_exact_authority_value",
    "require_identity_context",
    "require_owned_identity",
    "seal_canonical_enum",
]
