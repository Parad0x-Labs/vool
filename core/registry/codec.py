"""Bounded, deterministic data formats shared by authors, reviewers and clients."""
from __future__ import annotations

import base64
import hashlib
import json
import re

MAX_DOCUMENT = 24 * 1024 * 1024
MAX_PACKAGE = 16 * 1024 * 1024
HEX = re.compile(r"^[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
IDENTITY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*)?$")


def version_key(value: str) -> tuple:
    main, _, prerelease = value.partition("-")
    return (*(int(part) for part in main.split(".")), not bool(prerelease), tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".") if part))


class RegistryError(ValueError):
    """Untrusted registry input or incomplete evidence was refused."""


def canonical(value) -> bytes:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(result) > MAX_DOCUMENT:
            raise RegistryError("document exceeds byte limit")
        return result
    except (ValueError, TypeError, RecursionError):
        raise RegistryError("document is not bounded JSON data") from None


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(raw: bytes, limit: int = MAX_DOCUMENT):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise RegistryError("document exceeds byte limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RegistryError("duplicate JSON field")
            result[key] = value
        return result

    def constant(_value):
        raise RegistryError("non-finite JSON number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise RegistryError("invalid JSON document") from None


def unbase64(value: str) -> bytes:
    try:
        if not isinstance(value, str) or len(value) > MAX_DOCUMENT:
            raise ValueError()
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise RegistryError("invalid base64 data") from None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)
