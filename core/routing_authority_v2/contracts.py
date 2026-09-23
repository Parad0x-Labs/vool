"""Immutable, non-executable VOOL routing authority V2 Phase-0 contracts."""

from __future__ import annotations

import re
import types
import unicodedata
from dataclasses import dataclass, fields
from enum import Enum
from ipaddress import IPv6Address, ip_address
from typing import Any, ClassVar, TypeVar, Union, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

import idna

from core.enum_compat import StrEnum
from core.routing_authority_v2.canonical import (
    CanonicalizationError,
    canonical_bytes,
    canonical_set,
    parse_strict_json,
    typed_sha256,
)
from core.routing_authority_v2.money import MAX_PICO_USD, PicoUSD

IDNA_CANONICALIZER_IMPLEMENTATION = "idna"
IDNA_CANONICALIZER_VERSION = "3.20"
ENDPOINT_CANONICALIZATION_REVISION = "idna2008-uts46-nontransitional-idna-3.20-v1"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[^\s?#\\]{1,256}\Z")
_SAFE_KEY_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_REVISION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_IDNA_LABEL_SEPARATOR_RE = re.compile(r"[.\u3002\uff0e\uff61]")
_NUMERIC_ADDRESS_TOKEN = r"(?:0[xX][0-9A-Fa-f]+|0[oO][0-7]+|0[bB][01]+|[0-9]+)"
_NUMERIC_ADDRESS_LIKE_RE = re.compile(rf"{_NUMERIC_ADDRESS_TOKEN}(?:\.{_NUMERIC_ADDRESS_TOKEN})*\Z")
_ENDPOINT_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+\Z")
_RECORD_TYPES: dict[str, type[AuthorityRecord]] = {}
T = TypeVar("T", bound="AuthorityRecord")


class ContractValidationError(ValueError):
    """A routing authority record violates its schema or an architecture invariant."""


class ExecutionGeneration(StrEnum):
    LEGACY_V1 = "LEGACY_V1"
    AUTHORITY_V2 = "AUTHORITY_V2"


class AuthorityState(StrEnum):
    NON_EXECUTABLE_SHADOW = "NON_EXECUTABLE_SHADOW"


class RoutingIntentMode(StrEnum):
    AUTO = "AUTO"
    AUTO_PREFER = "AUTO_PREFER"
    PINNED = "PINNED"
    LOCAL_ONLY = "LOCAL_ONLY"
    PRIVATE = "PRIVATE"


class RemoteRuleKind(StrEnum):
    EXACT_MODEL = "EXACT_MODEL"
    PROVIDER_WILDCARD = "PROVIDER_WILDCARD"


class CandidateLocality(StrEnum):
    LOCAL_MACHINE = "LOCAL_MACHINE"
    REMOTE_PROVIDER = "REMOTE_PROVIDER"


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    RESTRICTED = "RESTRICTED"


class ProviderOperation(StrEnum):
    TEXT_GENERATION = "TEXT_GENERATION"
    STRUCTURED_GENERATION = "STRUCTURED_GENERATION"
    EMBEDDING = "EMBEDDING"


class CostDimension(StrEnum):
    INPUT_TOKEN = "INPUT_TOKEN"
    OUTPUT_TOKEN = "OUTPUT_TOKEN"
    REQUEST = "REQUEST"
    IMAGE = "IMAGE"
    AUDIO_SECOND = "AUDIO_SECOND"


class CostConflictStatus(StrEnum):
    CLEAR = "CLEAR"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class UnknownExternalAuthorityKind(StrEnum):
    EXPLICIT_UNKNOWN_EXTERNAL_COST = "EXPLICIT_UNKNOWN_EXTERNAL_COST"


class UnknownExternalGrantScope(StrEnum):
    SINGLE_EXACT_CANDIDATE_OPERATION = "SINGLE_EXACT_CANDIDATE_OPERATION"


class FailureStage(StrEnum):
    SCHEMA = "SCHEMA"
    INTENT = "INTENT"
    CONTEXT_ADMISSION = "CONTEXT_ADMISSION"
    PERMISSION = "PERMISSION"
    CANDIDATE_IDENTITY = "CANDIDATE_IDENTITY"
    COST_ATTESTATION = "COST_ATTESTATION"
    SHADOW_PERSISTENCE = "SHADOW_PERSISTENCE"
    PROVIDER_OPERATION = "PROVIDER_OPERATION"


class FailureClass(StrEnum):
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    POLICY_DENIED = "POLICY_DENIED"
    STALE_AUTHORITY = "STALE_AUTHORITY"
    COST_UNPROVEN = "COST_UNPROVEN"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class DeliveryState(StrEnum):
    NOT_SENT = "NOT_SENT"
    MAYBE_SENT = "MAYBE_SENT"
    SENT = "SENT"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"


class BillingState(StrEnum):
    NOT_BILLABLE = "NOT_BILLABLE"
    NOT_CHARGED = "NOT_CHARGED"
    MAYBE_CHARGED = "MAYBE_CHARGED"
    CHARGED = "CHARGED"
    UNKNOWN = "UNKNOWN"


class RetryDisposition(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    SAME_CANDIDATE = "SAME_CANDIDATE"
    NEW_AUTHORITY_REQUIRED = "NEW_AUTHORITY_REQUIRED"


class RecoveryAction(StrEnum):
    REMAIN_LOCAL = "REMAIN_LOCAL"
    REQUEST_EXPLICIT_ACKNOWLEDGEMENT = "REQUEST_EXPLICIT_ACKNOWLEDGEMENT"
    REFRESH_AUTHORITY = "REFRESH_AUTHORITY"
    REPORT_ONLY = "REPORT_ONLY"
    NONE = "NONE"


class RoutingReasonCode(StrEnum):
    REMOTE_AUTO_UNCONFIGURED = "REMOTE_AUTO_UNCONFIGURED"
    REMOTE_AUTO_EMPTY = "REMOTE_AUTO_EMPTY"
    REMOTE_RULE_DENIED = "REMOTE_RULE_DENIED"
    INTENT_CONTRADICTION = "INTENT_CONTRADICTION"
    PREFERENCE_NOT_AUTHORITY = "PREFERENCE_NOT_AUTHORITY"
    UNKNOWN_EXTERNAL_ACK_REQUIRED = "UNKNOWN_EXTERNAL_ACK_REQUIRED"
    UNKNOWN_EXTERNAL_BINDING_MISMATCH = "UNKNOWN_EXTERNAL_BINDING_MISMATCH"
    COST_UNKNOWN = "COST_UNKNOWN"
    COST_CONFLICT = "COST_CONFLICT"
    CANDIDATE_IDENTITY_MISMATCH = "CANDIDATE_IDENTITY_MISMATCH"
    CONTEXT_BINDING_MISMATCH = "CONTEXT_BINDING_MISMATCH"
    NON_EXECUTABLE_PHASE_0 = "NON_EXECUTABLE_PHASE_0"
    LEGACY_V1_SOLE_AUTHORITY = "LEGACY_V1_SOLE_AUTHORITY"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    CANONICAL_INVALID = "CANONICAL_INVALID"


class ReceiptOutcome(StrEnum):
    SHADOW_PLANNED = "SHADOW_PLANNED"
    SHADOW_REJECTED = "SHADOW_REJECTED"
    SHADOW_OBSERVED_LEGACY = "SHADOW_OBSERVED_LEGACY"


PRODUCTION_INGRESS_GENERATION = ExecutionGeneration.LEGACY_V1


def _text(value: str, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != normalized.strip():
        raise ContractValidationError(f"{field_name} cannot contain surrounding whitespace")
    if not allow_empty and not normalized:
        raise ContractValidationError(f"{field_name} is required")
    if any(ord(char) < 0x20 for char in normalized):
        raise ContractValidationError(f"{field_name} cannot contain control characters")
    return normalized


def _identifier(value: str, *, field_name: str) -> str:
    normalized = _text(value, field_name=field_name)
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ContractValidationError(f"{field_name} is not a canonical opaque identifier")
    return normalized


def _optional_identifier(value: str | None, *, field_name: str) -> str | None:
    return None if value is None else _identifier(value, field_name=field_name)


def _revision(value: str, *, field_name: str) -> str:
    normalized = _text(value, field_name=field_name)
    if not _REVISION_RE.fullmatch(normalized):
        raise ContractValidationError(f"{field_name} is not a canonical revision")
    return normalized


def _digest(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ContractValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _optional_digest(value: str | None, *, field_name: str) -> str | None:
    return None if value is None else _digest(value, field_name=field_name)


def _unix_ms(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= (2**63) - 1:
        raise ContractValidationError(f"{field_name} must be an explicit non-negative signed-64 Unix millisecond")
    return value


def _nonnegative_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > (2**63) - 1:
        raise ContractValidationError(f"{field_name} must be a non-negative signed-64 integer")
    return value


def _positive_int(value: int, *, field_name: str) -> int:
    parsed = _nonnegative_int(value, field_name=field_name)
    if parsed == 0:
        raise ContractValidationError(f"{field_name} must be positive")
    return parsed


def _ordered_identifiers(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    return tuple(_identifier(item, field_name=field_name) for item in tuple(values))


def _ordered_digests(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    return tuple(_digest(item, field_name=field_name) for item in tuple(values))


def _set_strings(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(_identifier(item, field_name=field_name) for item in tuple(values))
    try:
        return canonical_set(normalized)
    except CanonicalizationError as exc:
        raise ContractValidationError(f"{field_name} contains a duplicate") from exc


def _set_digests(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(_digest(item, field_name=field_name) for item in tuple(values))
    try:
        return canonical_set(normalized)
    except CanonicalizationError as exc:
        raise ContractValidationError(f"{field_name} contains a duplicate") from exc


def _enum_tuple(values: tuple[Enum, ...], enum_type: type[Enum], *, field_name: str) -> tuple[Enum, ...]:
    parsed: list[Enum] = []
    for raw in tuple(values):
        try:
            parsed.append(raw if isinstance(raw, enum_type) else enum_type(raw))
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(f"{field_name} contains an invalid value") from exc
    try:
        return tuple(enum_type(value) for value in canonical_set(tuple(item.value for item in parsed)))
    except CanonicalizationError as exc:
        raise ContractValidationError(f"{field_name} contains a duplicate") from exc


def _fixed_enum(value: Enum, expected: Enum, *, field_name: str) -> Enum:
    if value != expected:
        raise ContractValidationError(f"{field_name} is fixed to {expected.value} in Phase 0")
    return value


def _verify_endpoint_canonicalizer() -> None:
    if getattr(idna, "__version__", None) != IDNA_CANONICALIZER_VERSION:
        raise ContractValidationError(
            f"endpoint canonicalization requires {IDNA_CANONICALIZER_IMPLEMENTATION}=={IDNA_CANONICALIZER_VERSION}"
        )


def _input_hostname(netloc: str) -> str:
    if netloc.startswith("["):
        closing_bracket = netloc.find("]")
        if closing_bracket <= 1:
            raise ContractValidationError("endpoint_origin contains an invalid bracketed host")
        if netloc[closing_bracket + 1 :] and not netloc[closing_bracket + 1 :].startswith(":"):
            raise ContractValidationError("endpoint_origin contains data after a bracketed host")
        return netloc[1:closing_bracket]
    if ":" in netloc:
        return netloc.rsplit(":", 1)[0]
    return netloc


def _canonical_dns_hostname(hostname: str) -> str:
    input_labels = _IDNA_LABEL_SEPARATOR_RE.split(hostname)
    if any(label[:4].lower() == "xn--" and label != label.lower() for label in input_labels):
        raise ContractValidationError("endpoint_origin A-label input must use exact lowercase canonical form")
    if _NUMERIC_ADDRESS_LIKE_RE.fullmatch(hostname):
        raise ContractValidationError("endpoint_origin numeric host is not a canonical IP literal")
    try:
        encoded = idna.encode(
            hostname,
            uts46=True,
            transitional=False,
            std3_rules=True,
        )
        unicode_hostname = idna.decode(encoded, uts46=True, std3_rules=True)
        round_trip = idna.encode(
            unicode_hostname,
            uts46=True,
            transitional=False,
            std3_rules=True,
        )
        canonical_host = encoded.decode("ascii").lower()
    except (idna.IDNAError, UnicodeError) as exc:
        raise ContractValidationError("endpoint_origin host violates strict non-transitional IDNA") from exc
    if round_trip.decode("ascii").lower() != canonical_host:
        raise ContractValidationError("endpoint_origin host does not have stable IDNA round-trip identity")
    if _NUMERIC_ADDRESS_LIKE_RE.fullmatch(canonical_host):
        raise ContractValidationError("endpoint_origin numeric host is not a canonical IP literal")
    labels = canonical_host.split(".")
    if (
        not labels
        or any(not label or not _DNS_LABEL_RE.fullmatch(label) for label in labels)
        or len(canonical_host) > 253
    ):
        raise ContractValidationError("endpoint_origin host is not a canonical DNS name")
    return canonical_host


def _canonical_endpoint_origin(value: str) -> str:
    _verify_endpoint_canonicalizer()
    origin = _text(value, field_name="endpoint_origin")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise ContractValidationError("endpoint_origin contains an invalid host or port") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ContractValidationError("endpoint_origin must use HTTP or HTTPS")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise ContractValidationError("endpoint_origin requires an unambiguous host without userinfo")
    input_hostname = _input_hostname(parsed.netloc)
    if not input_hostname:
        raise ContractValidationError("endpoint_origin requires an unambiguous host")
    if parsed.netloc.endswith(":"):
        raise ContractValidationError("endpoint_origin port cannot be empty")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ContractValidationError("endpoint_origin cannot contain base path, query, or fragment")
    if port is not None and not 1 <= port <= 65_535:
        raise ContractValidationError("endpoint_origin port must be between 1 and 65535")

    if "%" in input_hostname or input_hostname.endswith("."):
        raise ContractValidationError("endpoint_origin host is ambiguous")
    try:
        parsed_ip = ip_address(input_hostname)
    except ValueError:
        rendered_host = _canonical_dns_hostname(input_hostname)
    else:
        rendered_host = f"[{parsed_ip.compressed.lower()}]" if isinstance(parsed_ip, IPv6Address) else parsed_ip.compressed

    default_port = 443 if scheme == "https" else 80
    rendered_port = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{rendered_port}"


def _canonical_endpoint_base_path(value: str) -> str:
    base_path = _text(value, field_name="endpoint_base_path")
    if not base_path.startswith("/") or any(character in base_path for character in ("?", "#", "\\", "%")):
        raise ContractValidationError("endpoint_base_path must be an absolute unescaped URL path")
    if "//" in base_path:
        raise ContractValidationError("endpoint_base_path cannot contain empty path segments")
    segments = base_path.split("/")[1:]
    if segments and segments[-1] == "":
        segments.pop()
    if any(segment in {"", ".", ".."} or not _ENDPOINT_PATH_SEGMENT_RE.fullmatch(segment) for segment in segments):
        raise ContractValidationError("endpoint_base_path contains a non-canonical path segment")
    return "/" if not segments else f"/{'/'.join(segments)}"


def _encode_field(value: Any) -> Any:
    if isinstance(value, AuthorityRecord):
        return value.to_record()
    if isinstance(value, PicoUSD):
        return value.canonical_value()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_encode_field(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ContractValidationError(f"mutable or unsupported authority field: {type(value).__name__}")


def _decode_field(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        candidates = tuple(item for item in args if item is not type(None))
        if len(candidates) != 1:
            raise ContractValidationError("unsupported union in authority schema")
        return _decode_field(candidates[0], value)
    if origin is tuple:
        if not isinstance(value, list):
            raise ContractValidationError("tuple authority field must be encoded as a JSON array")
        if len(args) != 2 or args[1] is not Ellipsis:
            raise ContractValidationError("only homogeneous authority tuples are supported")
        return tuple(_decode_field(args[0], item) for item in value)
    if isinstance(annotation, type) and issubclass(annotation, AuthorityRecord):
        parsed = parse_authority_record(value)
        if not isinstance(parsed, annotation):
            raise ContractValidationError("nested authority record has the wrong type")
        return parsed
    if annotation is PicoUSD:
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            raise ContractValidationError("pico-USD must be encoded as an unsigned decimal string")
        try:
            parsed = int(value, 10)
        except ValueError as exc:
            raise ContractValidationError("pico-USD decimal string is too large") from exc
        if value != str(parsed):
            raise ContractValidationError("pico-USD must use its shortest canonical decimal encoding")
        if parsed > MAX_PICO_USD:
            raise ContractValidationError("pico-USD is outside the unsigned 128-bit range")
        return PicoUSD.from_picos(parsed)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid authority enum value") from exc
    if annotation is str:
        if not isinstance(value, str):
            raise ContractValidationError("authority string field has the wrong JSON type")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ContractValidationError("authority bool field has the wrong JSON type")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractValidationError("authority integer field has the wrong JSON type")
        return value
    raise ContractValidationError(f"unsupported authority schema annotation: {annotation!r}")


class AuthorityRecord:
    """Base for versioned records whose canonical bytes are their entire authority."""

    RECORD_TYPE: ClassVar[str] = ""
    SCHEMA_VERSION: ClassVar[int] = 0
    HASH_DOMAIN: ClassVar[str] = ""

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if cls.RECORD_TYPE:
            previous = _RECORD_TYPES.get(cls.RECORD_TYPE)
            if previous is not None and (
                previous.__module__ != cls.__module__ or previous.__qualname__ != cls.__qualname__
            ):
                raise RuntimeError(f"duplicate authority record type: {cls.RECORD_TYPE}")
            _RECORD_TYPES[cls.RECORD_TYPE] = cls

    def to_record(self) -> dict[str, Any]:
        if not self.RECORD_TYPE or self.SCHEMA_VERSION <= 0 or not self.HASH_DOMAIN:
            raise ContractValidationError("authority record is missing type metadata")
        record = {"record_type": self.RECORD_TYPE, "schema_version": self.SCHEMA_VERSION}
        for field in fields(self):
            record[field.name] = _encode_field(getattr(self, field.name))
        return record

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_record())

    def digest(self) -> str:
        return typed_sha256(self.HASH_DOMAIN, self.canonical_bytes())

    @classmethod
    def from_record(cls: type[T], record: dict[str, Any]) -> T:
        if not isinstance(record, dict):
            raise ContractValidationError("authority record must be a JSON object")
        expected = {"record_type", "schema_version", *(field.name for field in fields(cls))}
        if set(record) != expected:
            missing = sorted(expected - set(record))
            extra = sorted(set(record) - expected)
            raise ContractValidationError(f"authority record field mismatch: missing={missing}, extra={extra}")
        if (
            not isinstance(record["record_type"], str)
            or isinstance(record["schema_version"], bool)
            or not isinstance(record["schema_version"], int)
            or record["record_type"] != cls.RECORD_TYPE
            or record["schema_version"] != cls.SCHEMA_VERSION
        ):
            raise ContractValidationError("authority record type or schema version mismatch")
        hints = get_type_hints(cls)
        kwargs = {field.name: _decode_field(hints[field.name], record[field.name]) for field in fields(cls)}
        return cls(**kwargs)


def parse_authority_record(record: Any) -> AuthorityRecord:
    if not isinstance(record, dict):
        raise ContractValidationError("authority record must be a JSON object")
    record_type = record.get("record_type")
    if not isinstance(record_type, str) or record_type not in _RECORD_TYPES:
        raise ContractValidationError("unknown authority record type")
    return _RECORD_TYPES[record_type].from_record(record)


def parse_authority_json(payload: str | bytes) -> AuthorityRecord:
    try:
        record = parse_strict_json(payload)
    except CanonicalizationError as exc:
        raise ContractValidationError("invalid canonical authority JSON") from exc
    return parse_authority_record(record)


@dataclass(frozen=True, slots=True, kw_only=True)
class SubjectBindingV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "SubjectBindingV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_SUBJECT_BINDING_V2"

    authenticated_subject_id: str
    authenticated_session_id: str
    turn_id: str
    context_digest: str
    credential_generation: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "authenticated_subject_id", _identifier(self.authenticated_subject_id, field_name="authenticated_subject_id"))
        object.__setattr__(self, "authenticated_session_id", _identifier(self.authenticated_session_id, field_name="authenticated_session_id"))
        object.__setattr__(self, "turn_id", _identifier(self.turn_id, field_name="turn_id"))
        object.__setattr__(self, "context_digest", _digest(self.context_digest, field_name="context_digest"))
        object.__setattr__(self, "credential_generation", _nonnegative_int(self.credential_generation, field_name="credential_generation"))


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingIntentV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingIntentV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_INTENT_V2"

    subject_binding_digest: str
    mode: RoutingIntentMode
    preferred_provider_id: str | None = None
    preferred_native_model_id: str | None = None
    exact_candidate_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_binding_digest", _digest(self.subject_binding_digest, field_name="subject_binding_digest"))
        try:
            mode = self.mode if isinstance(self.mode, RoutingIntentMode) else RoutingIntentMode(self.mode)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid routing intent mode") from exc
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "preferred_provider_id", _optional_identifier(self.preferred_provider_id, field_name="preferred_provider_id"))
        object.__setattr__(self, "preferred_native_model_id", _optional_identifier(self.preferred_native_model_id, field_name="preferred_native_model_id"))
        object.__setattr__(self, "exact_candidate_digest", _optional_digest(self.exact_candidate_digest, field_name="exact_candidate_digest"))
        has_preference = self.preferred_provider_id is not None or self.preferred_native_model_id is not None
        if mode is RoutingIntentMode.AUTO_PREFER:
            if not has_preference or self.exact_candidate_digest is not None:
                raise ContractValidationError("AUTO_PREFER requires a preference and forbids exact candidate authority")
        elif mode is RoutingIntentMode.PINNED:
            if self.exact_candidate_digest is None or has_preference:
                raise ContractValidationError("PINNED requires one exact candidate and forbids preferences")
        elif has_preference or self.exact_candidate_digest is not None:
            raise ContractValidationError(f"{mode.value} forbids preference and exact candidate fields")


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteAutoRuleV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RemoteAutoRuleV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_REMOTE_AUTO_RULE_V2"

    kind: RemoteRuleKind
    provider_instance_id: str
    native_model_id: str | None = None

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, RemoteRuleKind) else RemoteRuleKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid remote Auto rule kind") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "provider_instance_id", _identifier(self.provider_instance_id, field_name="provider_instance_id"))
        object.__setattr__(self, "native_model_id", _optional_identifier(self.native_model_id, field_name="native_model_id"))
        if kind is RemoteRuleKind.EXACT_MODEL and self.native_model_id is None:
            raise ContractValidationError("exact remote rule requires provider and native model")
        if kind is RemoteRuleKind.PROVIDER_WILDCARD and self.native_model_id is not None:
            raise ContractValidationError("provider wildcard must be explicit and cannot carry a model")

    def permits(self, candidate: ExecutableCandidateRefV2) -> bool:
        if candidate.locality is not CandidateLocality.REMOTE_PROVIDER:
            return False
        if candidate.provider_instance_id != self.provider_instance_id:
            return False
        return self.kind is RemoteRuleKind.PROVIDER_WILDCARD or candidate.native_model_id == self.native_model_id


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteAutoRulesV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RemoteAutoRulesV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_REMOTE_AUTO_RULES_V2"

    configured: bool
    rules: tuple[RemoteAutoRuleV2, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.configured, bool):
            raise ContractValidationError("configured must be a bool")
        rules = tuple(self.rules)
        if any(not isinstance(rule, RemoteAutoRuleV2) for rule in rules):
            raise ContractValidationError("rules must contain RemoteAutoRuleV2 records")
        ordered = tuple(sorted(rules, key=lambda item: item.canonical_bytes()))
        if len({rule.digest() for rule in ordered}) != len(ordered):
            raise ContractValidationError("remote Auto rule set contains a duplicate")
        if not self.configured and ordered:
            raise ContractValidationError("unconfigured remote Auto rules cannot contain hidden rules")
        object.__setattr__(self, "rules", ordered)

    @property
    def grants_any_remote_auto(self) -> bool:
        return self.configured and bool(self.rules)

    @property
    def local_machine_inventory_allowed(self) -> bool:
        return True

    def permits(self, candidate: ExecutableCandidateRefV2) -> bool:
        return self.grants_any_remote_auto and any(rule.permits(candidate) for rule in self.rules)

    def permitted_candidates(
        self,
        candidates: tuple[ExecutableCandidateRefV2, ...],
        *,
        intent_mode: RoutingIntentMode = RoutingIntentMode.AUTO,
    ) -> tuple[ExecutableCandidateRefV2, ...]:
        try:
            mode = intent_mode if isinstance(intent_mode, RoutingIntentMode) else RoutingIntentMode(intent_mode)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid routing intent mode") from exc
        values = tuple(candidates)
        if any(not isinstance(candidate, ExecutableCandidateRefV2) for candidate in values):
            raise ContractValidationError("candidate inventory must contain executable candidate references")
        return tuple(
            candidate
            for candidate in values
            if candidate.locality is CandidateLocality.LOCAL_MACHINE
            or (mode is not RoutingIntentMode.LOCAL_ONLY and self.permits(candidate))
        )

    def permitted_remote_candidates(
        self,
        candidates: tuple[ExecutableCandidateRefV2, ...],
        *,
        intent_mode: RoutingIntentMode = RoutingIntentMode.AUTO,
    ) -> tuple[ExecutableCandidateRefV2, ...]:
        return tuple(
            candidate
            for candidate in self.permitted_candidates(candidates, intent_mode=intent_mode)
            if candidate.locality is CandidateLocality.REMOTE_PROVIDER
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingPermissionSnapshotV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingPermissionSnapshotV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_PERMISSION_SNAPSHOT_V2"

    subject_binding_digest: str
    routing_intent_digest: str
    remote_auto_rules_digest: str
    allowed_localities: tuple[CandidateLocality, ...]
    remote_auto_candidate_digests: tuple[str, ...]
    local_machine_inventory_allowed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_binding_digest", _digest(self.subject_binding_digest, field_name="subject_binding_digest"))
        object.__setattr__(self, "routing_intent_digest", _digest(self.routing_intent_digest, field_name="routing_intent_digest"))
        object.__setattr__(self, "remote_auto_rules_digest", _digest(self.remote_auto_rules_digest, field_name="remote_auto_rules_digest"))
        object.__setattr__(self, "allowed_localities", _enum_tuple(self.allowed_localities, CandidateLocality, field_name="allowed_localities"))
        object.__setattr__(self, "remote_auto_candidate_digests", _set_digests(self.remote_auto_candidate_digests, field_name="remote_auto_candidate_digests"))
        if self.local_machine_inventory_allowed is not True:
            raise ContractValidationError("Phase-0 remote Auto configuration cannot disable local inventory")
        if CandidateLocality.REMOTE_PROVIDER not in self.allowed_localities and self.remote_auto_candidate_digests:
            raise ContractValidationError("remote candidates require remote locality permission")


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskRequirementsV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "TaskRequirementsV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_TASK_REQUIREMENTS_V2"

    required_capabilities: tuple[str, ...]
    minimum_context_tokens: int
    expected_output_tokens: int
    data_classification: DataClassification
    allowed_localities: tuple[CandidateLocality, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_capabilities", _set_strings(self.required_capabilities, field_name="required_capabilities"))
        object.__setattr__(self, "minimum_context_tokens", _nonnegative_int(self.minimum_context_tokens, field_name="minimum_context_tokens"))
        object.__setattr__(self, "expected_output_tokens", _nonnegative_int(self.expected_output_tokens, field_name="expected_output_tokens"))
        try:
            classification = self.data_classification if isinstance(self.data_classification, DataClassification) else DataClassification(self.data_classification)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid data classification") from exc
        object.__setattr__(self, "data_classification", classification)
        object.__setattr__(self, "allowed_localities", _enum_tuple(self.allowed_localities, CandidateLocality, field_name="allowed_localities"))
        if not self.allowed_localities:
            raise ContractValidationError("at least one locality is required")


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextEnvelopeV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ContextEnvelopeV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_CONTEXT_ENVELOPE_V2"

    turn_id: str
    context_digest: str
    ordered_item_digests: tuple[str, ...]
    utf8_byte_length: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_id", _identifier(self.turn_id, field_name="turn_id"))
        object.__setattr__(self, "context_digest", _digest(self.context_digest, field_name="context_digest"))
        object.__setattr__(self, "ordered_item_digests", _ordered_digests(self.ordered_item_digests, field_name="ordered_item_digests"))
        object.__setattr__(self, "utf8_byte_length", _nonnegative_int(self.utf8_byte_length, field_name="utf8_byte_length"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextAttestationV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ContextAttestationV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_CONTEXT_ATTESTATION_V2"

    context_envelope_digest: str
    context_policy_revision: str
    attestor_id: str
    attested_at_unix_ms: int
    admitted: bool
    admitted_item_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_envelope_digest", _digest(self.context_envelope_digest, field_name="context_envelope_digest"))
        object.__setattr__(self, "context_policy_revision", _revision(self.context_policy_revision, field_name="context_policy_revision"))
        object.__setattr__(self, "attestor_id", _identifier(self.attestor_id, field_name="attestor_id"))
        object.__setattr__(self, "attested_at_unix_ms", _unix_ms(self.attested_at_unix_ms, field_name="attested_at_unix_ms"))
        if not isinstance(self.admitted, bool):
            raise ContractValidationError("admitted must be a bool")
        object.__setattr__(self, "admitted_item_digests", _ordered_digests(self.admitted_item_digests, field_name="admitted_item_digests"))
        object.__setattr__(self, "reason_codes", _set_strings(self.reason_codes, field_name="reason_codes"))
        if not self.admitted and self.admitted_item_digests:
            raise ContractValidationError("denied context cannot carry admitted items")


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnRoutingEnvelopeV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "TurnRoutingEnvelopeV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_TURN_ROUTING_ENVELOPE_V2"

    subject_binding_digest: str
    routing_intent_digest: str
    permission_snapshot_digest: str
    task_requirements_digest: str
    context_attestation_digest: str
    generation: ExecutionGeneration = ExecutionGeneration.AUTHORITY_V2
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        for field_name in (
            "subject_binding_digest",
            "routing_intent_digest",
            "permission_snapshot_digest",
            "task_requirements_digest",
            "context_attestation_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name=field_name))
        _fixed_enum(self.generation, ExecutionGeneration.AUTHORITY_V2, field_name="generation")
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class CostChargeV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "CostChargeV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_COST_CHARGE_V2"

    dimension: CostDimension
    pico_usd_per_unit: PicoUSD
    units_per_charge: int = 1

    def __post_init__(self) -> None:
        try:
            dimension = self.dimension if isinstance(self.dimension, CostDimension) else CostDimension(self.dimension)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid cost dimension") from exc
        object.__setattr__(self, "dimension", dimension)
        if not isinstance(self.pico_usd_per_unit, PicoUSD):
            raise ContractValidationError("pico_usd_per_unit must be PicoUSD")
        object.__setattr__(self, "units_per_charge", _positive_int(self.units_per_charge, field_name="units_per_charge"))


@dataclass(frozen=True, slots=True, kw_only=True)
class CostAttestationV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "CostAttestationV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_COST_ATTESTATION_V2"

    candidate_binding_digest: str
    catalog_id: str
    catalog_revision: str
    pricing_revision: str
    retrieved_at_unix_ms: int
    valid_until_unix_ms: int
    currency: str
    charges: tuple[CostChargeV2, ...]
    promotion_id: str | None
    quota_id: str | None
    provider_manifest_digest: str
    conflict_status: CostConflictStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_binding_digest", _digest(self.candidate_binding_digest, field_name="candidate_binding_digest"))
        object.__setattr__(self, "catalog_id", _identifier(self.catalog_id, field_name="catalog_id"))
        object.__setattr__(self, "catalog_revision", _revision(self.catalog_revision, field_name="catalog_revision"))
        object.__setattr__(self, "pricing_revision", _revision(self.pricing_revision, field_name="pricing_revision"))
        object.__setattr__(self, "retrieved_at_unix_ms", _unix_ms(self.retrieved_at_unix_ms, field_name="retrieved_at_unix_ms"))
        object.__setattr__(self, "valid_until_unix_ms", _unix_ms(self.valid_until_unix_ms, field_name="valid_until_unix_ms"))
        if self.valid_until_unix_ms <= self.retrieved_at_unix_ms:
            raise ContractValidationError("cost validity must expire after retrieval")
        if self.currency != "USD":
            raise ContractValidationError("cost attestation currency must be USD")
        charges = tuple(self.charges)
        if not charges or any(not isinstance(charge, CostChargeV2) for charge in charges):
            raise ContractValidationError("cost attestation requires typed charge dimensions")
        ordered = tuple(sorted(charges, key=lambda item: item.dimension.value))
        if len({charge.dimension for charge in ordered}) != len(ordered):
            raise ContractValidationError("cost charge dimensions must be unique")
        object.__setattr__(self, "charges", ordered)
        object.__setattr__(self, "promotion_id", _optional_identifier(self.promotion_id, field_name="promotion_id"))
        object.__setattr__(self, "quota_id", _optional_identifier(self.quota_id, field_name="quota_id"))
        object.__setattr__(self, "provider_manifest_digest", _digest(self.provider_manifest_digest, field_name="provider_manifest_digest"))
        try:
            conflict = self.conflict_status if isinstance(self.conflict_status, CostConflictStatus) else CostConflictStatus(self.conflict_status)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid cost conflict status") from exc
        object.__setattr__(self, "conflict_status", conflict)

    @property
    def verified_zero_cost(self) -> bool:
        return self.conflict_status is CostConflictStatus.CLEAR and all(charge.pico_usd_per_unit.picos == 0 for charge in self.charges)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutableCandidateRefV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ExecutableCandidateRefV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_EXECUTABLE_CANDIDATE_REF_V2"
    COST_BINDING_DOMAIN: ClassVar[str] = "VOOL_EXECUTABLE_CANDIDATE_COST_BINDING_V2"

    provider_instance_id: str
    native_model_id: str
    billing_scope_id: str
    credential_generation: int
    provider_manifest_revision: str
    provider_manifest_digest: str
    endpoint_origin: str
    endpoint_base_path: str
    tls_policy_digest: str
    proxy_policy_digest: str
    redirect_policy_digest: str
    adapter_contract_revision: str
    adapter_contract_digest: str
    catalog_id: str
    catalog_revision: str
    catalog_digest: str
    cost_attestation_digest: str
    capability_evidence_digest: str
    locality: CandidateLocality
    locality_evidence_digest: str
    local_artifact_digest: str | None = None
    endpoint_canonicalization_revision: str = ENDPOINT_CANONICALIZATION_REVISION

    def __post_init__(self) -> None:
        for field_name in ("provider_instance_id", "native_model_id", "billing_scope_id", "catalog_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "credential_generation", _nonnegative_int(self.credential_generation, field_name="credential_generation"))
        for field_name in ("provider_manifest_revision", "adapter_contract_revision", "catalog_revision"):
            object.__setattr__(self, field_name, _revision(getattr(self, field_name), field_name=field_name))
        for field_name in (
            "provider_manifest_digest",
            "tls_policy_digest",
            "proxy_policy_digest",
            "redirect_policy_digest",
            "adapter_contract_digest",
            "catalog_digest",
            "cost_attestation_digest",
            "capability_evidence_digest",
            "locality_evidence_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "local_artifact_digest", _optional_digest(self.local_artifact_digest, field_name="local_artifact_digest"))
        canonicalization_revision = _text(
            self.endpoint_canonicalization_revision,
            field_name="endpoint_canonicalization_revision",
        )
        if canonicalization_revision != ENDPOINT_CANONICALIZATION_REVISION:
            raise ContractValidationError(
                f"endpoint_canonicalization_revision is fixed to {ENDPOINT_CANONICALIZATION_REVISION} in Phase 0"
            )
        object.__setattr__(self, "endpoint_canonicalization_revision", canonicalization_revision)
        object.__setattr__(self, "endpoint_origin", _canonical_endpoint_origin(self.endpoint_origin))
        object.__setattr__(self, "endpoint_base_path", _canonical_endpoint_base_path(self.endpoint_base_path))
        try:
            locality = self.locality if isinstance(self.locality, CandidateLocality) else CandidateLocality(self.locality)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid candidate locality") from exc
        object.__setattr__(self, "locality", locality)
        if locality is CandidateLocality.LOCAL_MACHINE and self.local_artifact_digest is None:
            raise ContractValidationError("local candidate requires a local artifact digest")
        if locality is CandidateLocality.REMOTE_PROVIDER and self.local_artifact_digest is not None:
            raise ContractValidationError("remote candidate cannot carry local artifact authority")

    def cost_binding_digest(self) -> str:
        record = self.to_record()
        del record["cost_attestation_digest"]
        return typed_sha256(self.COST_BINDING_DOMAIN, canonical_bytes(record))


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingPlanV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingPlanV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_PLAN_V2"

    turn_routing_envelope_digest: str
    permission_snapshot_digest: str
    ordered_candidate_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_routing_envelope_digest", _digest(self.turn_routing_envelope_digest, field_name="turn_routing_envelope_digest"))
        object.__setattr__(self, "permission_snapshot_digest", _digest(self.permission_snapshot_digest, field_name="permission_snapshot_digest"))
        object.__setattr__(self, "ordered_candidate_digests", _ordered_digests(self.ordered_candidate_digests, field_name="ordered_candidate_digests"))
        object.__setattr__(self, "reason_codes", _set_strings(self.reason_codes, field_name="reason_codes"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingAttemptPermitV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingAttemptPermitV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_ATTEMPT_PERMIT_V2"

    permit_id: str
    routing_plan_digest: str
    subject_binding_digest: str
    candidate_digest: str
    operation: ProviderOperation
    attempt_ordinal: int
    expires_at_unix_ms: int
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "permit_id", _identifier(self.permit_id, field_name="permit_id"))
        for field_name in ("routing_plan_digest", "subject_binding_digest", "candidate_digest"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name=field_name))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "attempt_ordinal", _nonnegative_int(self.attempt_ordinal, field_name="attempt_ordinal"))
        object.__setattr__(self, "expires_at_unix_ms", _unix_ms(self.expires_at_unix_ms, field_name="expires_at_unix_ms"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderNetworkOperationPermitV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ProviderNetworkOperationPermitV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_PROVIDER_NETWORK_OPERATION_PERMIT_V2"

    permit_id: str
    routing_attempt_permit_digest: str
    candidate_digest: str
    payload_provenance_map_digest: str
    operation: ProviderOperation
    expires_at_unix_ms: int
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "permit_id", _identifier(self.permit_id, field_name="permit_id"))
        for field_name in ("routing_attempt_permit_digest", "candidate_digest", "payload_provenance_map_digest"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name=field_name))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "expires_at_unix_ms", _unix_ms(self.expires_at_unix_ms, field_name="expires_at_unix_ms"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class SpendReservationV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "SpendReservationV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_SPEND_RESERVATION_V2"

    reservation_id: str
    subject_binding_digest: str
    candidate_digest: str
    operation: ProviderOperation
    reserved_amount: PicoUSD
    expires_at_unix_ms: int
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "reservation_id", _identifier(self.reservation_id, field_name="reservation_id"))
        object.__setattr__(self, "subject_binding_digest", _digest(self.subject_binding_digest, field_name="subject_binding_digest"))
        object.__setattr__(self, "candidate_digest", _digest(self.candidate_digest, field_name="candidate_digest"))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        if not isinstance(self.reserved_amount, PicoUSD):
            raise ContractValidationError("reserved_amount must be PicoUSD")
        object.__setattr__(self, "expires_at_unix_ms", _unix_ms(self.expires_at_unix_ms, field_name="expires_at_unix_ms"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownExternalAcknowledgementV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "UnknownExternalAcknowledgementV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_UNKNOWN_EXTERNAL_ACKNOWLEDGEMENT_V2"

    acknowledgement_id: str
    authority_kind: UnknownExternalAuthorityKind
    authenticated_subject_id: str
    turn_id: str
    context_digest: str
    exact_candidate_digest: str
    credential_generation: int
    operation: ProviderOperation
    expires_at_unix_ms: int

    def __post_init__(self) -> None:
        for field_name in ("acknowledgement_id", "authenticated_subject_id", "turn_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name=field_name))
        _fixed_enum(self.authority_kind, UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST, field_name="authority_kind")
        object.__setattr__(self, "context_digest", _digest(self.context_digest, field_name="context_digest"))
        object.__setattr__(self, "exact_candidate_digest", _digest(self.exact_candidate_digest, field_name="exact_candidate_digest"))
        object.__setattr__(self, "credential_generation", _nonnegative_int(self.credential_generation, field_name="credential_generation"))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "expires_at_unix_ms", _unix_ms(self.expires_at_unix_ms, field_name="expires_at_unix_ms"))


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownExternalAttemptGrantV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "UnknownExternalAttemptGrantV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_UNKNOWN_EXTERNAL_ATTEMPT_GRANT_V2"

    grant_id: str
    acknowledgement_digest: str
    authority_kind: UnknownExternalAuthorityKind
    scope: UnknownExternalGrantScope
    authenticated_subject_id: str
    turn_id: str
    context_digest: str
    exact_candidate_digest: str
    credential_generation: int
    operation: ProviderOperation
    expires_at_unix_ms: int
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        for field_name in ("grant_id", "authenticated_subject_id", "turn_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "acknowledgement_digest", _digest(self.acknowledgement_digest, field_name="acknowledgement_digest"))
        _fixed_enum(self.authority_kind, UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST, field_name="authority_kind")
        _fixed_enum(self.scope, UnknownExternalGrantScope.SINGLE_EXACT_CANDIDATE_OPERATION, field_name="scope")
        object.__setattr__(self, "context_digest", _digest(self.context_digest, field_name="context_digest"))
        object.__setattr__(self, "exact_candidate_digest", _digest(self.exact_candidate_digest, field_name="exact_candidate_digest"))
        object.__setattr__(self, "credential_generation", _nonnegative_int(self.credential_generation, field_name="credential_generation"))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "expires_at_unix_ms", _unix_ms(self.expires_at_unix_ms, field_name="expires_at_unix_ms"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")

    def matches_acknowledgement(self, acknowledgement: UnknownExternalAcknowledgementV2) -> bool:
        return (
            self.acknowledgement_digest == acknowledgement.digest()
            and self.authenticated_subject_id == acknowledgement.authenticated_subject_id
            and self.turn_id == acknowledgement.turn_id
            and self.context_digest == acknowledgement.context_digest
            and self.exact_candidate_digest == acknowledgement.exact_candidate_digest
            and self.credential_generation == acknowledgement.credential_generation
            and self.operation is acknowledgement.operation
            and self.expires_at_unix_ms <= acknowledgement.expires_at_unix_ms
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PayloadProvenanceEntryV1(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "PayloadProvenanceEntryV1"
    SCHEMA_VERSION: ClassVar[int] = 1
    HASH_DOMAIN: ClassVar[str] = "VOOL_PROVIDER_PAYLOAD_PROVENANCE_ENTRY_V1"

    provider_field_path: str
    source_kind: str
    source_digest: str

    def __post_init__(self) -> None:
        path = _text(self.provider_field_path, field_name="provider_field_path")
        if not path.startswith("/") or ".." in path.split("/"):
            raise ContractValidationError("provider_field_path must be a normalized JSON pointer")
        object.__setattr__(self, "provider_field_path", path)
        object.__setattr__(self, "source_kind", _revision(self.source_kind, field_name="source_kind"))
        object.__setattr__(self, "source_digest", _digest(self.source_digest, field_name="source_digest"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderPayloadProvenanceMapV1(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ProviderPayloadProvenanceMapV1"
    SCHEMA_VERSION: ClassVar[int] = 1
    HASH_DOMAIN: ClassVar[str] = "VOOL_PROVIDER_PAYLOAD_PROVENANCE_MAP_V1"

    entries: tuple[PayloadProvenanceEntryV1, ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, PayloadProvenanceEntryV1) for entry in entries):
            raise ContractValidationError("provenance map entries must be typed records")
        ordered = tuple(sorted(entries, key=lambda item: item.provider_field_path))
        if len({entry.provider_field_path for entry in ordered}) != len(ordered):
            raise ContractValidationError("provider payload field provenance must be unique")
        object.__setattr__(self, "entries", ordered)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderInvocationManifestV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "ProviderInvocationManifestV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_PROVIDER_INVOCATION_MANIFEST_V2"

    manifest_id: str
    candidate_digest: str
    network_operation_permit_digest: str
    payload_digest: str
    payload_provenance_map_digest: str
    context_digest: str
    operation: ProviderOperation
    generation: ExecutionGeneration = ExecutionGeneration.AUTHORITY_V2
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _identifier(self.manifest_id, field_name="manifest_id"))
        for field_name in (
            "candidate_digest",
            "network_operation_permit_digest",
            "payload_digest",
            "payload_provenance_map_digest",
            "context_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name=field_name))
        try:
            operation = self.operation if isinstance(self.operation, ProviderOperation) else ProviderOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid provider operation") from exc
        object.__setattr__(self, "operation", operation)
        _fixed_enum(self.generation, ExecutionGeneration.AUTHORITY_V2, field_name="generation")
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpaqueAuthorityRefV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "OpaqueAuthorityRefV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_OPAQUE_AUTHORITY_REF_V2"

    authority_kind: str
    authority_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority_kind", _revision(self.authority_kind, field_name="authority_kind"))
        object.__setattr__(self, "authority_digest", _digest(self.authority_digest, field_name="authority_digest"))


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingFailureV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingFailureV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_FAILURE_V2"

    stage: FailureStage
    failure_class: FailureClass
    delivery_state: DeliveryState
    billing_state: BillingState
    retry_disposition: RetryDisposition
    recovery_action: RecoveryAction
    reason_code: RoutingReasonCode
    safe_message_key: str
    authority_refs: tuple[OpaqueAuthorityRefV2, ...]

    def __post_init__(self) -> None:
        enum_fields: tuple[tuple[str, type[Enum]], ...] = (
            ("stage", FailureStage),
            ("failure_class", FailureClass),
            ("delivery_state", DeliveryState),
            ("billing_state", BillingState),
            ("retry_disposition", RetryDisposition),
            ("recovery_action", RecoveryAction),
            ("reason_code", RoutingReasonCode),
        )
        for field_name, enum_type in enum_fields:
            raw = getattr(self, field_name)
            try:
                object.__setattr__(self, field_name, raw if isinstance(raw, enum_type) else enum_type(raw))
            except (TypeError, ValueError) as exc:
                raise ContractValidationError(f"invalid {field_name}") from exc
        safe_key = _text(self.safe_message_key, field_name="safe_message_key")
        if not _SAFE_KEY_RE.fullmatch(safe_key):
            raise ContractValidationError("safe_message_key must be a non-sensitive localization key")
        object.__setattr__(self, "safe_message_key", safe_key)
        refs = tuple(self.authority_refs)
        if any(not isinstance(ref, OpaqueAuthorityRefV2) for ref in refs):
            raise ContractValidationError("authority_refs must contain opaque authority references")
        ordered = tuple(sorted(refs, key=lambda item: (item.authority_kind, item.authority_digest)))
        if len({(ref.authority_kind, ref.authority_digest) for ref in ordered}) != len(ordered):
            raise ContractValidationError("authority_refs contains a duplicate")
        object.__setattr__(self, "authority_refs", ordered)


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingReceiptV2(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "RoutingReceiptV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_RECEIPT_V2"

    receipt_id: str
    turn_routing_envelope_digest: str
    routing_plan_digest: str
    ordered_attempt_digests: tuple[str, ...]
    selected_candidate_digest: str | None
    failure_digest: str | None
    observed_cost_attestation_digest: str | None
    outcome: ReceiptOutcome
    observed_at_unix_ms: int
    authority_state: AuthorityState = AuthorityState.NON_EXECUTABLE_SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _identifier(self.receipt_id, field_name="receipt_id"))
        object.__setattr__(self, "turn_routing_envelope_digest", _digest(self.turn_routing_envelope_digest, field_name="turn_routing_envelope_digest"))
        object.__setattr__(self, "routing_plan_digest", _digest(self.routing_plan_digest, field_name="routing_plan_digest"))
        object.__setattr__(self, "ordered_attempt_digests", _ordered_digests(self.ordered_attempt_digests, field_name="ordered_attempt_digests"))
        object.__setattr__(self, "selected_candidate_digest", _optional_digest(self.selected_candidate_digest, field_name="selected_candidate_digest"))
        object.__setattr__(self, "failure_digest", _optional_digest(self.failure_digest, field_name="failure_digest"))
        object.__setattr__(self, "observed_cost_attestation_digest", _optional_digest(self.observed_cost_attestation_digest, field_name="observed_cost_attestation_digest"))
        try:
            outcome = self.outcome if isinstance(self.outcome, ReceiptOutcome) else ReceiptOutcome(self.outcome)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid receipt outcome") from exc
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "observed_at_unix_ms", _unix_ms(self.observed_at_unix_ms, field_name="observed_at_unix_ms"))
        _fixed_enum(self.authority_state, AuthorityState.NON_EXECUTABLE_SHADOW, field_name="authority_state")
        if outcome is ReceiptOutcome.SHADOW_REJECTED and self.failure_digest is None:
            raise ContractValidationError("rejected routing receipt requires a failure digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingDecisionShadowV2(AuthorityRecord):
    """Non-authorizing shadow of one front-door dispatch decision.

    The first live seam of the Phase-0 contracts (NIA-010): the JSONL routing
    telemetry is upgraded to typed canonical bytes at decision time. Carries only
    what the live seam knew — never provider credentials, never raw user text
    (only a digest of the already-redacted, already-truncated message).
    """

    RECORD_TYPE: ClassVar[str] = "RoutingDecisionShadowV2"
    SCHEMA_VERSION: ClassVar[int] = 2
    HASH_DOMAIN: ClassVar[str] = "VOOL_ROUTING_DECISION_SHADOW_V2"

    recorded_at_unix_ms: int
    session_ref: str
    family: str
    handled: bool
    message_redacted_digest: str
    claims: tuple[str, ...] = ()
    arbiter: str = ""
    source: str = "routing_decision_log"

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at_unix_ms", _unix_ms(self.recorded_at_unix_ms, field_name="recorded_at_unix_ms"))
        object.__setattr__(self, "session_ref", _text(self.session_ref[:80], field_name="session_ref"))
        object.__setattr__(self, "family", _text(self.family[:80], field_name="family"))
        object.__setattr__(self, "handled", bool(self.handled))
        object.__setattr__(self, "message_redacted_digest", _digest(self.message_redacted_digest, field_name="message_redacted_digest"))
        object.__setattr__(self, "claims", _set_strings(self.claims, field_name="claims"))
        object.__setattr__(self, "arbiter", _text(self.arbiter[:80], field_name="arbiter", allow_empty=True))
        object.__setattr__(self, "source", _text(self.source, field_name="source"))


__all__ = [
    "ENDPOINT_CANONICALIZATION_REVISION",
    "IDNA_CANONICALIZER_IMPLEMENTATION",
    "IDNA_CANONICALIZER_VERSION",
    "PRODUCTION_INGRESS_GENERATION",
    "AuthorityRecord",
    "AuthorityState",
    "BillingState",
    "CandidateLocality",
    "ContextAttestationV2",
    "ContextEnvelopeV2",
    "ContractValidationError",
    "CostAttestationV2",
    "CostChargeV2",
    "CostConflictStatus",
    "CostDimension",
    "DataClassification",
    "DeliveryState",
    "ExecutableCandidateRefV2",
    "ExecutionGeneration",
    "FailureClass",
    "FailureStage",
    "OpaqueAuthorityRefV2",
    "PayloadProvenanceEntryV1",
    "ProviderInvocationManifestV2",
    "ProviderNetworkOperationPermitV2",
    "ProviderOperation",
    "ProviderPayloadProvenanceMapV1",
    "ReceiptOutcome",
    "RecoveryAction",
    "RemoteAutoRuleV2",
    "RemoteAutoRulesV2",
    "RemoteRuleKind",
    "RetryDisposition",
    "RoutingAttemptPermitV2",
    "RoutingDecisionShadowV2",
    "RoutingFailureV2",
    "RoutingIntentMode",
    "RoutingIntentV2",
    "RoutingPermissionSnapshotV2",
    "RoutingPlanV2",
    "RoutingReasonCode",
    "RoutingReceiptV2",
    "SpendReservationV2",
    "SubjectBindingV2",
    "TaskRequirementsV2",
    "TurnRoutingEnvelopeV2",
    "UnknownExternalAcknowledgementV2",
    "UnknownExternalAttemptGrantV2",
    "UnknownExternalAuthorityKind",
    "UnknownExternalGrantScope",
    "parse_authority_json",
    "parse_authority_record",
]
