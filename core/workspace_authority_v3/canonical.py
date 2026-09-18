"""Canonical encoding and typed authentication primitives for dormant authority records."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unicodedata
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, NoReturn

MIN_SIGNED_64 = -(2**63)
MAX_SIGNED_64 = (2**63) - 1


class CanonicalizationError(ValueError):
    """A value cannot be represented by the authority canonical profile."""


def _reject_float(_value: str) -> NoReturn:
    raise CanonicalizationError("floating-point values are forbidden")


def _reject_constant(_value: str) -> NoReturn:
    raise CanonicalizationError("NaN and Infinity are forbidden")


def _bounded_integer(value: str) -> int:
    parsed = int(value, 10)
    if not MIN_SIGNED_64 <= parsed <= MAX_SIGNED_64:
        raise CanonicalizationError("integer is outside the signed 64-bit range")
    return parsed


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in pairs:
        key = unicodedata.normalize("NFC", raw_key)
        if key in result:
            raise CanonicalizationError("duplicate object key after NFC normalization")
        result[key] = value
    return result


def parse_strict_json(payload: str | bytes) -> Any:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CanonicalizationError("canonical JSON must be UTF-8") from exc
    if not isinstance(payload, str):
        raise CanonicalizationError("canonical JSON must be text or bytes")
    try:
        return json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_int=_bounded_integer,
            parse_constant=_reject_constant,
        )
    except CanonicalizationError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CanonicalizationError("malformed canonical JSON") from exc


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        if not MIN_SIGNED_64 <= value <= MAX_SIGNED_64:
            raise CanonicalizationError("integer is outside the signed 64-bit range")
        return value
    if isinstance(value, float):
        raise CanonicalizationError("floating-point values are forbidden")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            key = unicodedata.normalize("NFC", raw_key)
            if key in result:
                raise CanonicalizationError("duplicate object key after NFC normalization")
            result[key] = _normalize(raw_value)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def typed_sha256(domain: str, payload: bytes) -> str:
    if not isinstance(domain, str) or not domain or "\x00" in domain:
        raise CanonicalizationError("hash domain must be a non-empty NUL-free string")
    if not isinstance(payload, bytes):
        raise CanonicalizationError("typed hashing accepts bytes only")
    domain_bytes = domain.encode("utf-8")
    framed = len(domain_bytes).to_bytes(4, "big") + domain_bytes + len(payload).to_bytes(8, "big") + payload
    return hashlib.sha256(framed).hexdigest()


def typed_hmac_sha256(domain: str, key: bytes, payload: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise CanonicalizationError("authentication keys must contain at least 256 bits")
    digest = typed_sha256(domain, payload).encode("ascii")
    return hmac.new(key, digest, hashlib.sha256).hexdigest()


def constant_time_hex_digest_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("ascii", errors="ignore"), right.encode("ascii", errors="ignore"))


def urlsafe_b64encode_unpadded(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def urlsafe_b64decode_unpadded(payload: str) -> bytes:
    if not isinstance(payload, str) or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in payload):
        raise CanonicalizationError("invalid URL-safe base64 payload")
    padding = "=" * (-len(payload) % 4)
    try:
        return base64.b64decode(payload + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise CanonicalizationError("invalid URL-safe base64 payload") from exc


__all__ = [
    "MAX_SIGNED_64",
    "CanonicalizationError",
    "canonical_bytes",
    "constant_time_hex_digest_equal",
    "parse_strict_json",
    "typed_hmac_sha256",
    "typed_sha256",
    "urlsafe_b64decode_unpadded",
    "urlsafe_b64encode_unpadded",
]
