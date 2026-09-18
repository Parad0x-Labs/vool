"""Local-only API-key format classification — the FIRST step of credential intake.

A pasted key is classified by SHAPE ALONE: matched vendor prefix, length, charset, JWT/PEM
structure. This module is a pure computation by law — no sockets, no provider calls, no model
calls, no store reads, no env reads (the structural test forbids the imports). Classification
may narrow a key down to a FORMAT FAMILY and at most a LIKELY-provider shortlist; which
provider it really belongs to is the OPERATOR'S decision, made on the shortlist, and only that
one provider is ever asked (``verification.verify_provider_credential``).

Family vocabulary (what ``KeyFormat.family`` returns):

* a vendor family when a unique prefix identifies it outright — ``openrouter`` (``sk-or-``),
  ``anthropic`` (``sk-ant-``), ``openai`` (``sk-proj-``), ``groq`` (``gsk_``), ``google``
  (``AIza``), ``tavily`` (``tvly-``);
* ``openai_style`` for a bare ``sk-…`` — the FORMAT is known, the PROVIDER is not (OpenAI,
  DeepSeek and Moonshot all use it); the shortlist carries all three at medium confidence;
* recognized families that no configured provider claims (``slack``, ``github``, ``aws``,
  ``gitlab``, ``npm``, ``jwt``, ``pem``) — reported honestly, never mapped onto some other
  provider's slot;
* ``unknown`` when nothing matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: A value shorter than this is not a complete API key (mirrors the fast-command gate).
MIN_COMPLETE_KEY_LENGTH = 16

# Unique vendor prefixes, longest first so "sk-or-"/"sk-ant-" beat the bare "sk-".
_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("sk-or-", "openrouter"),
    ("sk-ant-", "anthropic"),
    ("sk-proj-", "openai"),
    ("gsk_", "groq"),
    ("AIza", "google"),
    ("tvly-", "tavily"),
)

#: Families the industry knows even when no configured provider claims them.
_RECOGNIZED_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("xoxb-", "xoxp-", "xoxa-", "xoxs-", "xoxr-"), "slack"),
    (("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"), "github"),
    (("AKIA",), "aws"),
    (("glpat-",), "gitlab"),
    (("npm_",), "npm"),
)

_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}$")
_PEM_RE = re.compile(r"^-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_B62_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class KeyFormat:
    """The shape of a pasted key. Deliberately carries NO copy of the value."""

    family: str
    prefix: str
    length: int
    charset: str
    is_jwt: bool
    is_pem: bool
    plausible_key: bool


def _charset(value: str) -> str:
    if _HEX_RE.match(value):
        return "hex"
    if re.match(r"^[A-Za-z0-9_=-]+$", value):
        return "base64url" if ("=" in value or "-" in value or "_" in value) else "base62"
    return "mixed"


def classify_format(secret: str, *, known_prefixes: tuple[str, ...] = ()) -> KeyFormat:
    """Classify a pasted value by shape alone. Pure, total, never raises on input.

    ``known_prefixes`` are the documented key prefixes the caller's provider registry carries —
    plain data, so this module stays free of registries, stores and transports. A value starting
    with one of them (and with no built-in vendor prefix) reports family ``registry_prefix`` and
    that prefix, so a provider missing from the vendor table above (a search API, a gateway) is
    still hinted instead of reported as unknown.
    """
    value = str(secret or "").strip()
    length = len(value)
    if not value:
        return KeyFormat("unknown", "", 0, "mixed", False, False, False)

    is_pem = bool(_PEM_RE.match(value))
    is_jwt = bool(_JWT_RE.match(value))
    single_token = " " not in value and "\n" not in value.strip()
    plausible = single_token and length >= MIN_COMPLETE_KEY_LENGTH and not is_pem

    if is_pem:
        return KeyFormat("pem", "-----BEGIN", length, "mixed", False, True, False)
    if is_jwt:
        return KeyFormat("jwt", "eyJ", length, "base64url", True, False, True)

    for prefix, family in _VENDOR_PREFIXES:
        if value.startswith(prefix):
            return KeyFormat(family, prefix, length, _charset(value), False, False, plausible)

    for prefix in sorted({str(p) for p in known_prefixes if str(p or "").strip()}, key=lambda p: (-len(p), p)):
        if value.startswith(prefix):
            return KeyFormat("registry_prefix", prefix, length, _charset(value), False, False, plausible)

    if value.startswith("sk-"):
        return KeyFormat("openai_style", "sk-", length, _charset(value), False, False, plausible)

    for prefixes, family in _RECOGNIZED_FAMILIES:
        for prefix in prefixes:
            if value.startswith(prefix):
                return KeyFormat(family, prefix, length, _charset(value), False, False, plausible)

    if _HEX_RE.match(value) and 32 <= length <= 64:
        return KeyFormat("hex", "", length, "hex", False, False, True)

    return KeyFormat("unknown", "", length, _charset(value), False, False, plausible)


__all__ = [
    "MIN_COMPLETE_KEY_LENGTH",
    "KeyFormat",
    "classify_format",
]
