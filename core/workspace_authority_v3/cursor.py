"""Bounded authenticated cursor contracts, disconnected from the live Changes API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from core.enum_compat import StrEnum
from core.workspace_authority_v3.base import (
    AuthorityRecord,
    ContractValidationError,
    require_digest,
    require_identifier,
    require_nonnegative,
    require_positive,
    sealed_authority_record_bytes,
    validate_dormant_envelope,
)
from core.workspace_authority_v3.canonical import (
    CanonicalizationError,
    canonical_bytes,
    parse_strict_json,
    urlsafe_b64decode_unpadded,
    urlsafe_b64encode_unpadded,
)
from core.workspace_authority_v3.contracts import (
    KeyFamily,
    SigningKeyCapability,
    VerificationKeyCapability,
    require_signing_key_capability,
    require_verification_key_capability,
    sign_with_key_capability,
    verify_with_key_capability,
)
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    IncarnationId,
    WorkspaceId,
    WorkspaceSequence,
    _issue_authenticated_identity_decode,
    require_exact_authority_value,
    seal_canonical_enum,
)

MAX_CURSOR_TOKEN_BYTES = 8192


class CursorError(ContractValidationError):
    pass


class CursorTokenTooLargeError(CursorError):
    pass


class CursorDirection(StrEnum):
    FORWARD = "FORWARD"
    BACKWARD = "BACKWARD"


seal_canonical_enum(CursorDirection)


@dataclass(frozen=True, slots=True)
class AuthenticatedCursorPayload(AuthorityRecord):
    RECORD_TYPE: ClassVar[str] = "AuthenticatedCursorPayload"

    principal_id: str
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    query_digest: str
    direction: CursorDirection
    page_size: int
    highwater: WorkspaceSequence
    exclusive_boundary: str
    projection_version: int
    issued_at_unix_ms: int
    expires_at_unix_ms: int
    key_generation: int

    def __post_init__(self) -> None:
        typed_values = (
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.highwater, WorkspaceSequence, "highwater"),
            (self.direction, CursorDirection, "direction"),
        )
        for value, expected_type, field_name in typed_values:
            require_exact_authority_value(value, expected_type, field_name)
        require_identifier(self.principal_id, "principal_id")
        require_digest(self.query_digest, "query_digest")
        require_positive(self.page_size, "page_size")
        require_identifier(self.exclusive_boundary, "exclusive_boundary")
        require_positive(self.projection_version, "projection_version")
        require_nonnegative(self.issued_at_unix_ms, "issued_at_unix_ms")
        require_nonnegative(self.expires_at_unix_ms, "expires_at_unix_ms")
        if self.expires_at_unix_ms <= self.issued_at_unix_ms:
            raise CursorError("cursor expiry must be later than issuance")
        require_positive(self.key_generation, "key_generation")

    @classmethod
    def from_record(
        cls,
        raw: dict[str, Any],
        *,
        verification_key: VerificationKeyCapability,
        supplied_mac: str,
    ) -> AuthenticatedCursorPayload:
        if type(verification_key) is not VerificationKeyCapability:
            raise TypeError("cursor identity decode requires an exact verification capability")
        source_record = canonical_bytes(raw)
        expected_keys = {
            "authority_epoch",
            "direction",
            "exclusive_boundary",
            "expires_at_unix_ms",
            "highwater",
            "incarnation_id",
            "issued_at_unix_ms",
            "key_generation",
            "page_size",
            "principal_id",
            "projection_version",
            "query_digest",
            "workspace_id",
        }
        record_payload = validate_dormant_envelope(
            raw,
            record_type=cls.RECORD_TYPE,
            schema_version=cls.SCHEMA_VERSION,
        )
        if set(record_payload) != expected_keys:
            raise CursorError("cursor payload fields do not match schema V3")
        try:
            direction = CursorDirection(record_payload["direction"])
        except (TypeError, ValueError) as exc:
            raise CursorError("cursor direction is invalid") from exc
        return cls(
            principal_id=record_payload["principal_id"],
            workspace_id=WorkspaceId._from_trusted_storage(
                _issue_authenticated_identity_decode(
                    verification_key=verification_key,
                    source_record=source_record,
                    supplied_mac=supplied_mac,
                    record_type=cls.RECORD_TYPE,
                    source_field="workspace_id",
                    identity_type=WorkspaceId,
                )
            ),
            incarnation_id=IncarnationId._from_trusted_storage(
                _issue_authenticated_identity_decode(
                    verification_key=verification_key,
                    source_record=source_record,
                    supplied_mac=supplied_mac,
                    record_type=cls.RECORD_TYPE,
                    source_field="incarnation_id",
                    identity_type=IncarnationId,
                )
            ),
            authority_epoch=AuthorityEpoch(record_payload["authority_epoch"]),
            query_digest=record_payload["query_digest"],
            direction=direction,
            page_size=record_payload["page_size"],
            highwater=WorkspaceSequence(record_payload["highwater"]),
            exclusive_boundary=record_payload["exclusive_boundary"],
            projection_version=record_payload["projection_version"],
            issued_at_unix_ms=record_payload["issued_at_unix_ms"],
            expires_at_unix_ms=record_payload["expires_at_unix_ms"],
            key_generation=record_payload["key_generation"],
        )


@dataclass(frozen=True, slots=True)
class CursorExpectation:
    principal_id: str
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    query_digest: str
    direction: CursorDirection
    page_size: int
    highwater: WorkspaceSequence
    projection_version: int

    def __post_init__(self) -> None:
        require_identifier(self.principal_id, "principal_id")
        require_digest(self.query_digest, "query_digest")
        require_positive(self.page_size, "page_size")
        require_positive(self.projection_version, "projection_version")
        typed_values = (
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.direction, CursorDirection, "direction"),
            (self.highwater, WorkspaceSequence, "highwater"),
        )
        for value, expected_type, field_name in typed_values:
            require_exact_authority_value(value, expected_type, field_name)


class CursorCodec:
    execution_authority = False

    def __init__(
        self,
        *,
        key: SigningKeyCapability | VerificationKeyCapability,
        max_token_bytes: int = MAX_CURSOR_TOKEN_BYTES,
    ) -> None:
        if type(key) is SigningKeyCapability:
            reference = require_signing_key_capability(key, expected_family=KeyFamily.CURSOR)
        elif type(key) is VerificationKeyCapability:
            reference = require_verification_key_capability(key, expected_family=KeyFamily.CURSOR)
        else:
            raise TypeError("cursor codec requires a validated key-use capability")
        require_positive(max_token_bytes, "max_token_bytes")
        self._key = key
        self._key_generation = reference.generation
        self._max_token_bytes = max_token_bytes

    def _authenticate(self, payload: bytes) -> str:
        if type(self._key) is not SigningKeyCapability:
            raise CursorError("verification-only cursor key cannot sign a new token")
        return sign_with_key_capability(
            self._key,
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_AUTHENTICATED_CURSOR_V3",
            message=payload,
        )

    def _verify(self, payload: bytes, supplied_mac: str) -> bool:
        if type(self._key) is not VerificationKeyCapability:
            raise CursorError("signing-only cursor key cannot verify a token")
        return verify_with_key_capability(
            self._key,
            expected_family=KeyFamily.CURSOR,
            domain="VOOL_AUTHENTICATED_CURSOR_V3",
            message=payload,
            supplied_mac=supplied_mac,
        )

    def encode(self, payload: AuthenticatedCursorPayload) -> str:
        if type(self._key) is not SigningKeyCapability:
            raise CursorError("verification-only cursor key cannot sign a new token")
        if payload.key_generation != self._key_generation:
            raise CursorError("cursor key generation mismatch")
        raw = sealed_authority_record_bytes(payload)
        token = f"{urlsafe_b64encode_unpadded(raw)}.{self._authenticate(raw)}"
        if len(token.encode("ascii")) > self._max_token_bytes:
            raise CursorTokenTooLargeError("cursor token exceeds encoded bound")
        return token

    def decode(
        self,
        token: str,
        *,
        expectation: CursorExpectation,
        now_unix_ms: int,
    ) -> AuthenticatedCursorPayload:
        if not isinstance(token, str):
            raise CursorError("cursor token must be text")
        try:
            encoded_size = len(token.encode("ascii", errors="strict"))
        except UnicodeEncodeError as exc:
            raise CursorError("cursor token must be ASCII") from exc
        if encoded_size > self._max_token_bytes:
            raise CursorTokenTooLargeError("cursor token exceeds encoded bound")
        parts = token.split(".")
        if len(parts) != 2:
            raise CursorError("cursor token framing is invalid")
        encoded_payload, supplied_mac = parts
        if len(supplied_mac) != 64:
            raise CursorError("cursor MAC framing is invalid")
        try:
            raw = urlsafe_b64decode_unpadded(encoded_payload)
        except CanonicalizationError as exc:
            raise CursorError("cursor payload encoding is invalid") from exc
        if not self._verify(raw, supplied_mac):
            raise CursorError("cursor authentication failed")
        try:
            parsed = parse_strict_json(raw)
        except CanonicalizationError as exc:
            raise CursorError("cursor canonical payload is invalid") from exc
        if not isinstance(parsed, dict) or canonical_bytes(parsed) != raw:
            raise CursorError("cursor payload is not exact canonical JSON")
        payload = AuthenticatedCursorPayload.from_record(
            parsed,
            verification_key=self._key,
            supplied_mac=supplied_mac,
        )
        if payload.key_generation != self._key_generation:
            raise CursorError("cursor key generation is stale")
        require_nonnegative(now_unix_ms, "now_unix_ms")
        if now_unix_ms < payload.issued_at_unix_ms or now_unix_ms >= payload.expires_at_unix_ms:
            raise CursorError("cursor is outside its validity window")
        if payload.principal_id != expectation.principal_id:
            raise CursorError("cursor principal mismatch")
        if payload.workspace_id != expectation.workspace_id or payload.incarnation_id != expectation.incarnation_id:
            raise CursorError("cursor workspace binding mismatch")
        if payload.authority_epoch != expectation.authority_epoch:
            raise CursorError("CURSOR_STALE_EPOCH")
        if payload.query_digest != expectation.query_digest:
            raise CursorError("cursor query binding mismatch")
        if payload.direction is not expectation.direction:
            raise CursorError("cursor direction binding mismatch")
        if payload.page_size != expectation.page_size:
            raise CursorError("cursor page-size binding mismatch")
        if payload.highwater != expectation.highwater:
            raise CursorError("cursor highwater binding mismatch")
        if payload.projection_version != expectation.projection_version:
            raise CursorError("cursor projection version mismatch")
        return payload


__all__ = [
    "MAX_CURSOR_TOKEN_BYTES",
    "AuthenticatedCursorPayload",
    "CursorCodec",
    "CursorDirection",
    "CursorError",
    "CursorExpectation",
    "CursorTokenTooLargeError",
]
