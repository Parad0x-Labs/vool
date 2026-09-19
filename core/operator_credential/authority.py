"""The operator credential: the app-level PIN or password a person sets to protect changes to saved contacts.

Law:

* One credential per runtime database, stored as verifiers only (tables: storage.migrations, version 8). The secret is
  stretched with PBKDF2-SHA256 at the wallet's cost (200,000 iterations) over a random salt and then keyed with HMAC-SHA256
  under a device key derived from this runtime's node signing key (network.signer.derive_local_secret). The secret itself is
  never stored, journaled, logged or returned, and no function here returns anything that can be replayed as a credential.
* Scoped: it authenticates only the capabilities in ``SCOPES`` (a protected change to saved contacts). It is not a wallet
  PIN: it never unlocks, signs or approves a payment, an email or an invitation, and it shares no verifier with the wallet.
* Enrolling, changing and resetting each need a live operating-system consent prompt (core.os_consent_gate: Touch ID or the
  login password in the system's own dialog -- never a web page). Changing also needs the current secret; resetting needs
  the recovery code shown once at enrollment. There is no other reset: without the recovery code saved contacts stay
  readable and protected changes keep waiting. The recovery verifier is PBKDF2 only (no device key), so a replaced node key
  can still be recovered from; the code carries about 98 bits, which PBKDF2 does not need a device key to protect.
* Verification is throttled durably with the wallet unlock policy's own values (core.wallet.pilot_custody): the attempt is
  counted before the secret is tried, one verification runs at a time, the credential scope locks after
  ``WALLET_LOCK_AFTER`` failures and the install scope after ``APP_LOCK_AFTER``, for ``LOCK_BASE_SECONDS`` doubling up to
  ``LOCK_MAX_SECONDS``. The counters are database rows, so a restart does not reset them.
* ``verify`` returns an in-memory ``Verification`` (principal, credential generation, scope, time). Enrollment, change and
  reset each raise the generation; an authorization minted under an older generation is refused at commit.

Bootstrap trust (stated, not hidden): the first enrollment is trusted to the operating system's consent dialog. A process
that can already drive this user's desktop session, read the runtime's files and keychain, or change its code is outside
what this credential defends against.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

AUTHORITY = "core.operator_credential.authority"
SCOPE_CONTACTS = "contacts.protected_change"
SCOPES: tuple[str, ...] = (SCOPE_CONTACTS,)
PRINCIPAL = "local_operator"
CREDENTIAL_ID = "operator"
KIND_PIN = "pin"
KIND_PASSWORD = "password"
KINDS: tuple[str, ...] = (KIND_PIN, KIND_PASSWORD)
PIN_MIN, PIN_MAX = 6, 12
PASSWORD_MIN, PASSWORD_MAX = 10, 128
KDF_ITERATIONS = 200_000
KDF_NAME = "pbkdf2-sha256-200000+hmac-sha256-device-key-v1"
RECOVERY_KDF_NAME = "pbkdf2-sha256-200000-v1"
SETUP_PATH = "Home → Contacts → Protection"
VERIFY_SCOPE = "credential:operator"
INSTALL_SCOPE = "credential:install"
IN_FLIGHT_STALE_SECONDS = 30.0
_DEVICE_LABEL = "vool-operator-credential-v1"
_SECRET_DOMAIN = b"vool-operator-credential|"
_RECOVERY_DOMAIN = b"vool-operator-recovery|"
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_RECOVERY_LENGTH = 20
TABLES: tuple[str, ...] = ("operator_credentials", "operator_credential_attempts", "operator_credential_events")


class CredentialError(Exception):
    """A typed refusal. ``reason`` is stable, ``message`` is for people, ``status`` is the HTTP status an owner door uses."""

    def __init__(self, reason: str, message: str, *, status: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "reason": self.reason, "message": self.message, **({"details": self.details} if self.details else {})}


@dataclass(frozen=True)
class Verification:
    """One successful check of the credential, in memory only."""

    principal: str
    credential_generation: int
    scope: str
    verified_at: float


def lock_policy() -> dict[str, int]:
    """The wallet unlock policy's values, read from their owner so the two cannot drift apart."""
    from core.wallet import pilot_custody

    return {"credential_lock_after": int(pilot_custody.WALLET_LOCK_AFTER), "install_lock_after": int(pilot_custody.APP_LOCK_AFTER),
            "lock_base_seconds": int(pilot_custody.LOCK_BASE_SECONDS), "lock_max_seconds": int(pilot_custody.LOCK_MAX_SECONDS)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _tx() -> Iterator[Any]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _reader() -> Iterator[Any]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _event(conn: Any, action: str, generation: int, detail: dict[str, Any] | None = None) -> None:
    conn.execute("INSERT INTO operator_credential_events (action, generation, detail_json, created_at) VALUES (?, ?, ?, ?)",
                 (action, int(generation), json.dumps(detail or {}, sort_keys=True), _now_iso()))


def _load(conn: Any | None = None) -> dict[str, Any] | None:
    if conn is None:
        with _reader() as reader:
            return _load(reader)
    row = conn.execute("SELECT * FROM operator_credentials WHERE credential_id = ?", (CREDENTIAL_ID,)).fetchone()
    return dict(row) if row else None


def current_generation(conn: Any | None = None) -> int:
    """The enrolled credential's generation, or 0 when none is enrolled. Pass ``conn`` to read inside a caller's transaction."""
    row = _load(conn)
    return int(row["generation"]) if row else 0


def _next_generation(conn: Any) -> int:
    logged = conn.execute("SELECT MAX(generation) FROM operator_credential_events").fetchone()[0]
    return max(int(logged or 0), current_generation(conn)) + 1


# --- secrets ------------------------------------------------------------------------------------------------------------

def _validated(secret: Any, kind: str) -> str:
    if kind not in KINDS:
        raise CredentialError("credential_kind_unknown", "Choose a PIN or a password.")
    if not isinstance(secret, str):
        raise CredentialError("credential_weak", "Enter a PIN or password.")
    value = unicodedata.normalize("NFC", secret)
    if kind == KIND_PIN:
        if not (value.isascii() and value.isdigit() and PIN_MIN <= len(value) <= PIN_MAX):
            raise CredentialError("credential_weak", f"A PIN is {PIN_MIN} to {PIN_MAX} digits.")
        if len(set(value)) == 1 or value in "01234567890123" or value in "98765432109876":
            raise CredentialError("credential_weak", "Choose a PIN that is not one repeated digit or a run such as 123456.")
        return value
    if not (PASSWORD_MIN <= len(value) <= PASSWORD_MAX) or not value.strip():
        raise CredentialError("credential_weak", f"A password is {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    return value


def _device_key() -> bytes:
    from network.signer import derive_local_secret

    return derive_local_secret(_DEVICE_LABEL)


def _stretch(value: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, KDF_ITERATIONS, dklen=32)


def _secret_verifier(value: str, salt: bytes) -> bytes:
    return hmac.new(_device_key(), _SECRET_DOMAIN + _stretch(value, salt), hashlib.sha256).digest()


def _recovery_verifier(code: str, salt: bytes) -> bytes:
    return hashlib.sha256(_RECOVERY_DOMAIN + _stretch(code, salt)).digest()


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
    return "-".join(raw[index:index + 5] for index in range(0, _RECOVERY_LENGTH, 5))


def _normalized_recovery(code: Any) -> str:
    text = "".join(ch for ch in str(code or "").upper() if ch not in " -\t")
    if len(text) != _RECOVERY_LENGTH or any(ch not in _RECOVERY_ALPHABET for ch in text):
        return ""
    return text


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _sealed(value: str, kind: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {"kind": kind, "kdf": KDF_NAME, "salt": _b64(salt), "verifier": _b64(_secret_verifier(value, salt))}


def _sealed_recovery() -> tuple[str, dict[str, Any]]:
    code = _new_recovery_code()
    salt = secrets.token_bytes(16)
    return code, {"recovery_kdf": RECOVERY_KDF_NAME, "recovery_salt": _b64(salt), "recovery_verifier": _b64(_recovery_verifier(_normalized_recovery(code), salt))}


# --- consent ------------------------------------------------------------------------------------------------------------

def _require_consent(reason: str) -> None:
    from core import os_consent_gate

    try:
        granted = os_consent_gate.require_os_user_consent(reason)
    except os_consent_gate.ConsentDeniedError:
        raise CredentialError("consent_denied", "The Mac's own confirmation was declined or failed; nothing changed.", status=403) from None
    except Exception:
        raise CredentialError(
            "consent_unavailable",
            f"This device could not show its own confirmation prompt, so nothing changed. Open VOOL on the device itself and use {SETUP_PATH}.",
            status=409, details={"setup_path": SETUP_PATH},
        ) from None
    if not granted:
        raise CredentialError("consent_denied", "The Mac's own confirmation was declined or failed; nothing changed.", status=403)


# --- the durable throttle -----------------------------------------------------------------------------------------------

def _scopes() -> tuple[tuple[str, int], ...]:
    policy = lock_policy()
    return ((VERIFY_SCOPE, policy["credential_lock_after"]), (INSTALL_SCOPE, policy["install_lock_after"]))


def _reserve_attempt() -> None:
    """Count this attempt before the secret is tried; refuse while a scope is locked or another check is running."""
    now = time.time()
    with _tx() as conn:
        for scope, _threshold in _scopes():
            row = conn.execute("SELECT locked_until, in_flight, in_flight_since FROM operator_credential_attempts WHERE scope = ?", (scope,)).fetchone()
            if row is None:
                continue
            if float(row["locked_until"] or 0) > now:
                wait = max(1, math.ceil(float(row["locked_until"]) - now))
                raise CredentialError("credential_throttled", f"Too many wrong attempts. Try again in {wait} seconds; nothing changed.", status=429,
                                      details={"retry_after_seconds": wait, "scope": "credential" if scope == VERIFY_SCOPE else "install"})
            if int(row["in_flight"] or 0) and now - float(row["in_flight_since"] or 0) < IN_FLIGHT_STALE_SECONDS:
                raise CredentialError("credential_busy", "Another check of the PIN is still running. Try again in a moment; nothing changed.", status=429,
                                      details={"retry_after_seconds": 1})
        for scope, _threshold in _scopes():
            conn.execute(
                "INSERT INTO operator_credential_attempts (scope, failures, locked_until, in_flight, in_flight_since, updated_at) VALUES (?, 1, 0, 1, ?, ?)"
                " ON CONFLICT(scope) DO UPDATE SET failures = failures + 1, in_flight = 1, in_flight_since = excluded.in_flight_since,"
                " updated_at = excluded.updated_at",
                (scope, now, now),
            )


def _settle_attempt(outcome: str) -> int:
    """success: the credential count clears and the install count gives this attempt back; failure: lock a scope that
    reached its threshold; refund: the attempt never reached a comparison. Returns attempts left before the credential locks."""
    now = time.time()
    policy = lock_policy()
    left = policy["credential_lock_after"]
    with _tx() as conn:
        for scope, threshold in _scopes():
            row = conn.execute("SELECT failures FROM operator_credential_attempts WHERE scope = ?", (scope,)).fetchone()
            failures = int(row["failures"]) if row else 0
            if outcome == "failure":
                locked_until = 0.0
                if failures >= threshold:
                    locked_until = now + min(policy["lock_max_seconds"], policy["lock_base_seconds"] * (2 ** min(failures - threshold, 16)))
                conn.execute("UPDATE operator_credential_attempts SET locked_until = ?, in_flight = 0, updated_at = ? WHERE scope = ?",
                             (locked_until, now, scope))
                if scope == VERIFY_SCOPE:
                    left = max(0, threshold - failures)
                continue
            remaining = 0 if (outcome == "success" and scope == VERIFY_SCOPE) else max(0, failures - 1)
            conn.execute("UPDATE operator_credential_attempts SET failures = ?, locked_until = 0, in_flight = 0, updated_at = ? WHERE scope = ?",
                         (remaining, now, scope))
        if outcome == "failure":
            _event(conn, "verification_failed", current_generation(conn), {"attempts_before_lock": left})
    return left


def _compare_with_throttle(compare: Any, *, failure: CredentialError) -> None:
    _reserve_attempt()
    try:
        matched = bool(compare())
    except Exception:
        _settle_attempt("refund")
        raise CredentialError("credential_unavailable", "The PIN could not be checked on this device right now; nothing changed.", status=503) from None
    if not matched:
        left = _settle_attempt("failure")
        raise CredentialError(failure.reason, failure.message, status=failure.status, details={**failure.details, "attempts_before_lock": left})
    _settle_attempt("success")


# --- public -------------------------------------------------------------------------------------------------------------

def status() -> dict[str, Any]:
    """What the Contacts surface shows: whether a credential is set, its kind and generation, and any lock in force."""
    row = _load()
    now = time.time()
    with _reader() as conn:
        attempts = {r["scope"]: dict(r) for r in conn.execute("SELECT scope, failures, locked_until FROM operator_credential_attempts")}
    locked_until = max((float(a.get("locked_until") or 0) for a in attempts.values()), default=0.0)
    return {
        "ok": True, "enrolled": row is not None, "kind": row["kind"] if row else "", "generation": int(row["generation"]) if row else 0,
        "recovery_available": bool(row and row.get("recovery_verifier")), "scopes": list(SCOPES), "kdf": KDF_NAME,
        "retry_after_seconds": max(0, math.ceil(locked_until - now)) if locked_until > now else 0, "policy": lock_policy(), "setup_path": SETUP_PATH,
        "pin_digits": [PIN_MIN, PIN_MAX], "password_chars": [PASSWORD_MIN, PASSWORD_MAX],
    }


def verify(secret: Any, *, scope: str = SCOPE_CONTACTS) -> Verification:
    """Check the credential once, under the durable throttle. Raises ``CredentialError``; returns nothing reusable."""
    if scope not in SCOPES:
        raise CredentialError("scope_not_granted", "This PIN does not authorize that action.", status=403)
    row = _load()
    if row is None:
        raise CredentialError("credential_not_enrolled", f"Set up a PIN or password in {SETUP_PATH} first; nothing changed.", status=409,
                              details={"setup_path": SETUP_PATH})
    if not isinstance(secret, str) or not secret:
        raise CredentialError("credential_invalid", "Enter your PIN or password; nothing changed.", status=403)
    value = unicodedata.normalize("NFC", secret)
    salt, stored = base64.b64decode(row["salt"]), base64.b64decode(row["verifier"])
    _compare_with_throttle(lambda: hmac.compare_digest(_secret_verifier(value, salt), stored),
                           failure=CredentialError("credential_invalid", "That PIN or password is not right; nothing changed.", status=403))
    generation = current_generation()
    if generation != int(row["generation"]):
        raise CredentialError("credential_changed", "The PIN or password changed while this check ran; nothing changed.", status=409)
    return Verification(principal=PRINCIPAL, credential_generation=generation, scope=scope, verified_at=time.time())


def enroll(secret: Any, *, kind: str) -> dict[str, Any]:
    """Set the credential the first time. Needs the operating system's consent prompt. Returns the recovery code once."""
    value = _validated(secret, kind)
    if _load() is not None:
        raise CredentialError("credential_already_enrolled", "A PIN or password is already set. Change it with the current one, or reset it with the recovery code.",
                              status=409)
    _require_consent("Set the PIN or password that protects changes to your saved contacts in VOOL")
    sealed = _sealed(value, kind)
    code, recovery = _sealed_recovery()
    now = _now_iso()
    with _tx() as conn:
        if _load(conn) is not None:
            raise CredentialError("credential_already_enrolled", "A PIN or password was set at the same moment; nothing changed.", status=409)
        generation = _next_generation(conn)
        conn.execute(
            "INSERT INTO operator_credentials (credential_id, kind, kdf, salt, verifier, recovery_kdf, recovery_salt, recovery_verifier, generation, scopes_json,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (CREDENTIAL_ID, kind, sealed["kdf"], sealed["salt"], sealed["verifier"], recovery["recovery_kdf"], recovery["recovery_salt"],
             recovery["recovery_verifier"], generation, json.dumps(list(SCOPES)), now, now),
        )
        conn.execute("DELETE FROM operator_credential_attempts")
        _event(conn, "enrolled", generation, {"kind": kind})
    return {"ok": True, "status": "enrolled", "kind": kind, "generation": generation, "recovery_code": code,
            "recovery_note": "Shown once. Keep it somewhere safe outside this device; it is the only way to reset a forgotten PIN or password."}


def change(current_secret: Any, new_secret: Any, *, kind: str) -> dict[str, Any]:
    """Replace the credential: the current secret (throttled) and the operating system's consent prompt. The recovery code stays."""
    value = _validated(new_secret, kind)
    verification = verify(current_secret)
    _require_consent("Change the PIN or password that protects changes to your saved contacts in VOOL")
    sealed = _sealed(value, kind)
    with _tx() as conn:
        if current_generation(conn) != verification.credential_generation:
            raise CredentialError("credential_changed", "The PIN or password changed while this was open; nothing changed.", status=409)
        generation = _next_generation(conn)
        conn.execute("UPDATE operator_credentials SET kind = ?, kdf = ?, salt = ?, verifier = ?, generation = ?, updated_at = ? WHERE credential_id = ?",
                     (kind, sealed["kdf"], sealed["salt"], sealed["verifier"], generation, _now_iso(), CREDENTIAL_ID))
        _event(conn, "changed", generation, {"kind": kind})
    return {"ok": True, "status": "changed", "kind": kind, "generation": generation}


def reset_with_recovery(recovery_code: Any, new_secret: Any, *, kind: str) -> dict[str, Any]:
    """Reset a forgotten credential with the recovery code (throttled) and the operating system's consent prompt. A new
    recovery code replaces the used one and is returned once."""
    value = _validated(new_secret, kind)
    row = _load()
    if row is None:
        raise CredentialError("credential_not_enrolled", f"No PIN or password is set yet; set one up in {SETUP_PATH}.", status=409,
                              details={"setup_path": SETUP_PATH})
    if not row.get("recovery_verifier"):
        raise CredentialError("recovery_unavailable", "No recovery code exists for this PIN, so it cannot be reset. Saved contacts stay readable; protected changes keep waiting.",
                              status=409)
    code = _normalized_recovery(recovery_code)
    salt, stored = base64.b64decode(row["recovery_salt"]), base64.b64decode(row["recovery_verifier"])
    _compare_with_throttle(lambda: bool(code) and hmac.compare_digest(_recovery_verifier(code, salt), stored),
                           failure=CredentialError("recovery_invalid", "That recovery code is not right; nothing changed.", status=403))
    _require_consent("Reset the PIN or password that protects changes to your saved contacts in VOOL")
    sealed = _sealed(value, kind)
    new_code, recovery = _sealed_recovery()
    with _tx() as conn:
        if current_generation(conn) != int(row["generation"]):
            raise CredentialError("credential_changed", "The PIN or password changed while this was open; nothing changed.", status=409)
        generation = _next_generation(conn)
        conn.execute(
            "UPDATE operator_credentials SET kind = ?, kdf = ?, salt = ?, verifier = ?, recovery_kdf = ?, recovery_salt = ?, recovery_verifier = ?, generation = ?,"
            " updated_at = ? WHERE credential_id = ?",
            (kind, sealed["kdf"], sealed["salt"], sealed["verifier"], recovery["recovery_kdf"], recovery["recovery_salt"], recovery["recovery_verifier"],
             generation, _now_iso(), CREDENTIAL_ID),
        )
        conn.execute("DELETE FROM operator_credential_attempts")
        _event(conn, "reset_with_recovery", generation, {"kind": kind})
    return {"ok": True, "status": "reset", "kind": kind, "generation": generation, "recovery_code": new_code,
            "recovery_note": "The old recovery code no longer works. This new one is shown once."}


def reset_for_tests() -> None:
    """Tests: remove the credential and its counters. Events stay, so generations keep rising within a database."""
    with _tx() as conn:
        conn.execute("DELETE FROM operator_credentials")
        conn.execute("DELETE FROM operator_credential_attempts")


__all__ = [
    "AUTHORITY",
    "KIND_PASSWORD",
    "KIND_PIN",
    "PRINCIPAL",
    "SCOPES",
    "SCOPE_CONTACTS",
    "SETUP_PATH",
    "CredentialError",
    "Verification",
    "change",
    "current_generation",
    "enroll",
    "lock_policy",
    "reset_for_tests",
    "reset_with_recovery",
    "status",
    "verify",
]
