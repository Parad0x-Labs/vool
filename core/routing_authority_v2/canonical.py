"""Single canonical JSON and hashing profile for routing authority records."""

from __future__ import annotations

import hashlib
import itertools
import json
import unicodedata
from collections.abc import Mapping, Sequence, Set
from enum import Enum
from typing import Any, NoReturn

MIN_CANONICAL_INTEGER = -(2**63)
MAX_CANONICAL_INTEGER = (2**63) - 1
MAX_RAW_BLOB_LENGTH = (2**64) - 1


class CanonicalizationError(ValueError):
    """Input cannot be represented by the VOOL canonical authority profile."""


def _reject_float(_value: str) -> NoReturn:
    raise CanonicalizationError("binary and JSON floating-point numbers are forbidden")


def _reject_constant(_value: str) -> NoReturn:
    raise CanonicalizationError("NaN and Infinity are forbidden")


def _parse_bounded_integer(value: str) -> int:
    parsed = int(value, 10)
    _validate_integer(parsed)
    return parsed


def _validate_integer(value: int) -> None:
    if not MIN_CANONICAL_INTEGER <= value <= MAX_CANONICAL_INTEGER:
        raise CanonicalizationError("integer is outside the signed 64-bit canonical range")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in pairs:
        key = unicodedata.normalize("NFC", raw_key)
        if key in result:
            raise CanonicalizationError("duplicate JSON object key after NFC normalization")
        result[key] = value
    return result


def parse_strict_json(payload: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate keys, floats, constants, and huge integers."""

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CanonicalizationError("canonical JSON must be valid UTF-8") from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise CanonicalizationError("canonical JSON input must be str or bytes")
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_int=_parse_bounded_integer,
            parse_constant=_reject_constant,
        )
    except CanonicalizationError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CanonicalizationError("malformed canonical JSON") from exc


def _normalized(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        _validate_integer(value)
        return value
    if isinstance(value, float):
        raise CanonicalizationError("binary floating-point numbers are forbidden")
    if isinstance(value, Enum):
        return _normalized(value.value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise CanonicalizationError("canonical JSON object keys must be strings")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise CanonicalizationError("duplicate object key after NFC normalization")
            normalized[key] = _normalized(raw_value)
        return normalized
    if isinstance(value, Set):
        members = [_normalized(item) for item in value]
        members.sort(key=_canonical_sort_key)
        for previous, current in itertools.pairwise(members):
            if _canonical_sort_key(previous) == _canonical_sort_key(current):
                raise CanonicalizationError("canonical mathematical set contains duplicate members")
        return members
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalized(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical value type: {type(value).__name__}")


def _canonical_sort_key(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_bytes(value: Any) -> bytes:
    """Encode a value as deterministic UTF-8 JSON under the authority profile."""

    return _canonical_sort_key(_normalized(value))


def canonical_text(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def canonical_set(values: Sequence[Any] | Set[Any]) -> tuple[Any, ...]:
    """Return a duplicate-free, canonical projection for a mathematical set."""

    normalized = [_normalized(item) for item in values]
    normalized.sort(key=_canonical_sort_key)
    result: list[Any] = []
    previous_key: bytes | None = None
    for item in normalized:
        key = _canonical_sort_key(item)
        if key == previous_key:
            raise CanonicalizationError("canonical mathematical set contains duplicate members")
        result.append(item)
        previous_key = key
    return tuple(result)


def typed_sha256(domain: str, payload: bytes) -> str:
    """Hash length-bound raw bytes in a mandatory, type-specific domain."""

    if not isinstance(domain, str) or not domain or "\x00" in domain:
        raise CanonicalizationError("hash domain must be a non-empty NUL-free string")
    if not isinstance(payload, bytes):
        raise CanonicalizationError("typed hashing accepts bytes only")
    if len(payload) > MAX_RAW_BLOB_LENGTH:
        raise CanonicalizationError("raw byte blob is too large")
    framed = domain.encode("utf-8") + b"\x00" + len(payload).to_bytes(8, "big") + payload
    return hashlib.sha256(framed).hexdigest()


__all__ = [
    "MAX_CANONICAL_INTEGER",
    "MIN_CANONICAL_INTEGER",
    "CanonicalizationError",
    "canonical_bytes",
    "canonical_set",
    "canonical_text",
    "parse_strict_json",
    "typed_sha256",
]
