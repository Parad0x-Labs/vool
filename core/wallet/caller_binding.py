"""Who may drive a trusted wallet door.

Loopback is not caller authorization: a page on another localhost port, a DNS-rebound host the operator
allow-listed, a framed page, a text/plain form and the command-dispatch projection all arrive from 127.0.0.1.
A trusted door (one that releases a key, a signature, a payment, custody or a policy change) needs
same-origin evidence from the real request and, when the native host configured one for this launch, its
capability. The decision is a pure function of the request headers; nothing here reads a wallet.

The native host holds the per-launch capability and gives the runtime only its sha256 through
``VOOL_WALLET_UI_CAPABILITY_SHA256``. The digest is taken out of the process environment the first time it
is read, so no child process inherits it, and the digest itself is never accepted as the capability.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from typing import Any

AUTHORITY = "core.wallet.caller_binding"
CAPABILITY_HEADER = "x-vool-wallet-capability"
CAPABILITY_DIGEST_ENV = "VOOL_WALLET_UI_CAPABILITY_SHA256"

MODE_UNBOUND = "unbound"
MODE_NATIVE_CAPABILITY = "native_capability"
MODE_NATIVE_CAPABILITY_INVALID = "native_capability_invalid"

#: The doors that release a key, a signature, a payment, custody or a policy change. Every other wallet door
#: keeps its open loopback contract (registering a watch-only account, proposing, rejecting, reading).
TRUSTED_DOORS: frozenset[str] = frozenset({
    "/api/wallet/approve", "/api/wallet/pocket/create", "/api/wallet/pocket/restore", "/api/wallet/export",
    "/api/wallet/approval-method", "/api/wallet/limits", "/api/wallet/external/submit", "/api/wallet/x402/fetch",
    "/api/wallet/x402/retry", "/api/wallet/environment",
    "/api/wallet/setup/create", "/api/wallet/setup/reveal", "/api/wallet/setup/acknowledge", "/api/wallet/setup/cancel",
    "/api/wallet/setup/resume", "/api/wallet/quote",
    "/api/wallet/transfers/stop-waiting", "/api/wallet/transfers/resend", "/api/wallet/transfers/discard",
    "/api/wallet/dna-fees/policy",
    "/api/wallet/recovery/backup", "/api/wallet/recovery/device",
})

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})
_INVALID = "!invalid"
_digest: str | None = None  # None: not read yet; "": unbound; _INVALID: configured but malformed


def _load() -> str:
    global _digest
    if _digest is None:
        raw = str(os.environ.pop(CAPABILITY_DIGEST_ENV, "") or "").strip().lower()
        if not raw:
            _digest = ""
        elif len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
            _digest = raw
        else:
            # a malformed digest binds every trusted door shut; it never falls back to unbound
            _digest = _INVALID
    return _digest


def reset_for_tests() -> None:
    """Forget the loaded digest and read the environment again (tests configure the variable first)."""
    global _digest
    _digest = None
    _load()


def mode() -> str:
    digest = _load()
    if not digest:
        return MODE_UNBOUND
    return MODE_NATIVE_CAPABILITY_INVALID if digest == _INVALID else MODE_NATIVE_CAPABILITY


def is_trusted_door(path: str) -> bool:
    return str(path or "") in TRUSTED_DOORS


def _host_name(value: str) -> str:
    host = str(value or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host.strip("[]")
    return host.split(":", 1)[0] if host.count(":") == 1 else host


def _is_loopback_name(name: str) -> bool:
    return name in _LOOPBACK_NAMES or name.startswith("127.")


def refusal_reason(headers: Mapping[str, Any] | None) -> str:
    """"" when the caller may drive a trusted door, else the typed reason. The order below is the contract:
    the first failing check names the refusal."""
    if headers is None:
        return "no_caller_evidence"
    lowered = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    host = lowered.get("host", "")
    if host and not _is_loopback_name(_host_name(host)):
        return "host_not_loopback"
    origin = lowered.get("origin", "")
    if origin.lower() == "null":
        return "origin_null"
    if origin and (not host or origin.rstrip("/").lower() not in {f"http://{host.lower()}", f"https://{host.lower()}"}):
        return "origin_mismatch"
    if lowered.get("sec-fetch-site", "").lower() in {"cross-site", "same-site"}:
        return "cross_site_fetch"
    if origin and lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return "content_type_not_json"
    digest = _load()
    if digest:
        presented = lowered.get(CAPABILITY_HEADER, "")
        if not presented:
            return "capability_missing"
        if digest == _INVALID or not hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), digest):
            return "capability_mismatch"
    return ""


__all__ = [
    "AUTHORITY",
    "CAPABILITY_DIGEST_ENV",
    "CAPABILITY_HEADER",
    "MODE_NATIVE_CAPABILITY",
    "MODE_NATIVE_CAPABILITY_INVALID",
    "MODE_UNBOUND",
    "TRUSTED_DOORS",
    "is_trusted_door",
    "mode",
    "refusal_reason",
    "reset_for_tests",
]
