"""Crypto Pilot custody: one chain-qualified wallet per Create, a credential set before any key exists, and a
backup key released exactly once.

The lifecycle is a row in ``wallet_profiles`` with ``seal_policy = pilot-v3``:

    generating -> awaiting_backup -> backup_revealed -> ready
            \\______________\\_______________\\-> cancelled -> (resume) awaiting_backup | backup_revealed

* Creation reserves the row (``generating``) inside BEGIN IMMEDIATE before any key material or Keychain write, so a
  lost response or a crash never yields a second wallet: the same ``creation_key`` returns the same wallet.
* The key is derived twice by independent code (Ed25519 via ``cryptography`` and ``solders``; secp256k1 via
  ``eth_keys`` and ``cryptography`` + keccak, with a known-answer self-test) before it is sealed.
* The seal is AES-256-GCM under sha256(device secret || PBKDF2-SHA256-200k(credential)) with an AAD binding the
  policy, family, network and address. No offline verifier is stored: the credential is proven only by opening the
  seal, through a durable throttle counted per wallet and for the whole app.
* The backup is computed and checked in process, and only the caller that wins the compare-and-set
  ``awaiting_backup -> backup_revealed`` receives it. It is registered with the runtime's exact-value scrubber and
  never written to a record, a receipt, a journal or a fault.

``core.wallet`` stays the only money authority: this module adds a custody policy, not a second vault.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import math
import os
import secrets
import time
import uuid
from collections.abc import Iterator
from typing import Any

from core.wallet import chains, custody, device_auth, environment, limits
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.pilot_custody"
SEAL_POLICY = custody.PILOT_SEAL_POLICY

STATE_GENERATING = "generating"
STATE_AWAITING_BACKUP = "awaiting_backup"
STATE_BACKUP_REVEALED = "backup_revealed"
STATE_READY = "ready"
STATE_CANCELLED = "cancelled"

METHODS = (custody.APPROVAL_PIN, custody.APPROVAL_PASSWORD, custody.APPROVAL_DEVICE)
PIN_MIN, PIN_MAX = 6, 12

_KDF_ITERATIONS = 200_000
_SEAL_LABEL = "vool-wallet-pilot-seal-v3"
_KDF_NAME = "pbkdf2-sha256-200000+device-secret-v3"

WALLET_LOCK_AFTER = 5
APP_LOCK_AFTER = 20
LOCK_BASE_SECONDS = 30
LOCK_MAX_SECONDS = 3600
ACK_TTL_SECONDS = 15 * 60

BACKUP_FORMAT_SOLANA = "solana_keypair_base58"
BACKUP_FORMAT_EVM = "evm_private_key_hex"
BACKUP_UNACKNOWLEDGED_NOTE = (
    "The backup key was released once. If you did not see and save it, cancel setup and do not send funds to this address."
)
KAT_EVM_K1_ADDRESS = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

_COLS = (
    "wallet_id", "mode", "network", "public_key", "label", "created_at", "approval_method", "sealed_blob",
    "device_sealed_blob", "seal_policy", "setup_state", "creation_key", "revealed_at", "setup_updated_at",
    "credential_generation",
)

#: Recovery attempts are throttled on their own scopes (same thresholds and delays as unlocks): a wrong backup never
#: touches the PIN throttle, a PIN cooldown never blocks recovery, and a full-entropy key that fits needs no extra wait.
RECOVERY_SCOPE_PREFIX = "recovery:"
RECOVERY_APP_SCOPE = "recovery-app"
#: Official import guidance for the exit "use another compatible wallet" (verify current support before relying on it).
PHANTOM_RESTORE_REFERENCE = "https://help.phantom.com/articles/15079894392851"
PHANTOM_LOGIN_LIMITS_REFERENCE = "https://help.phantom.com/hc/en-us/articles/45793905328787-Fix-issues-when-logging-in-to-your-wallet-in-Phantom"


def _fault(code: str, *, source_context: dict[str, Any] | None = None, **context: Any) -> WalletFault:
    return wallet_fault(code, authority=AUTHORITY, context=context, source_context=source_context)


# --- credential policy -------------------------------------------------------------------------------

def _valid_pin(value: str) -> bool:
    text = str(value or "")
    return text.isascii() and text.isdigit() and PIN_MIN <= len(text) <= PIN_MAX


def _require_credential_shape(method: str, credential: str, *, source_context: dict[str, Any] | None) -> None:
    if method == custody.APPROVAL_PIN and not _valid_pin(credential):
        raise _fault("wallet_pin_invalid", reason="pin_policy", policy=f"{PIN_MIN}-{PIN_MAX} ASCII digits", source_context=source_context)
    if method == custody.APPROVAL_PASSWORD and not custody._valid_password(credential):
        raise _fault("wallet_password_invalid", reason="password_policy", policy="10-128 characters, not only digits", source_context=source_context)


# --- key derivation, each address derived twice by independent code -----------------------------------

def _svm_address(secret: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.from_private_bytes(bytes(secret)).public_key().public_bytes_raw())


def _svm_address_independent(secret: bytes) -> str:
    from solders.keypair import Keypair

    return str(Keypair.from_seed(bytes(secret)).pubkey())


def evm_address_for_key(secret: bytes) -> str:
    """The EIP-55 address of a secp256k1 key (eth_keys)."""
    from eth_keys import keys

    return keys.PrivateKey(bytes(secret)).public_key.to_checksum_address()


def _evm_address_independent(secret: bytes) -> str:
    """The same address by other code: the public point from ``cryptography``, keccak from eth_hash."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from eth_hash.auto import keccak

    numbers = ec.derive_private_key(int.from_bytes(bytes(secret), "big"), ec.SECP256K1()).public_key().public_numbers()
    return "0x" + keccak(numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big"))[-20:].hex()


def _evm_self_test() -> None:
    k1 = (1).to_bytes(32, "big")
    if evm_address_for_key(k1) != KAT_EVM_K1_ADDRESS or _evm_address_independent(k1).lower() != KAT_EVM_K1_ADDRESS.lower():
        raise _fault("wallet_dependency_unavailable", reason="evm_derivation_self_test_failed")


def _generate_material(spec: chains.ChainIdentity) -> tuple[bytes, str]:
    """A fresh 32-byte key for this row's family and its address, verified by two derivations."""
    if spec.is_svm:
        secret = os.urandom(32)
        address = _svm_address(secret)
        if _svm_address_independent(secret) != address:
            raise _fault("wallet_dependency_unavailable", reason="solana_derivation_mismatch")
        return secret, address
    _evm_self_test()
    while True:
        secret = os.urandom(32)
        if 0 < int.from_bytes(secret, "big") < _SECP256K1_N:
            break
    address = evm_address_for_key(secret)
    if _evm_address_independent(secret).lower() != address.lower():
        raise _fault("wallet_dependency_unavailable", reason="evm_derivation_mismatch")
    return secret, address


# --- the v3 seal ---------------------------------------------------------------------------------------

def _aad(spec: chains.ChainIdentity, address: str) -> bytes:
    return f"{SEAL_POLICY}|{spec.family}|{spec.network}|{address}".encode()


def _device_secret() -> bytes:
    from network.signer import derive_local_secret

    return bytes(derive_local_secret(_SEAL_LABEL, length=32))


def _credential_key(credential: str, salt: bytes) -> bytes:
    stretched = hashlib.pbkdf2_hmac("sha256", str(credential).encode("utf-8"), salt, _KDF_ITERATIONS, dklen=32)
    return hashlib.sha256(_device_secret() + stretched).digest()


def _device_key(unlock_secret: bytes) -> bytes:
    return hashlib.sha256(_device_secret() + b"pilot-device-unlock" + bytes(unlock_secret)).digest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _seal_with_credential(secret: bytes, credential: str, aad: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_credential_key(credential, salt)).encrypt(nonce, bytes(secret), aad)
    return json.dumps({"ciphertext": _b64(ciphertext), "nonce": _b64(nonce), "salt": _b64(salt), "kdf": _KDF_NAME, "sealkind": SEAL_POLICY})


def _seal_with_device(wallet_id: str, secret: bytes, aad: bytes, *, source_context: dict[str, Any] | None) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    authority = device_auth.current_authority()
    unlock_secret = os.urandom(32)
    try:
        authority.store_unlock_secret(wallet_id, unlock_secret, reason=f"VOOL Crypto Pilot wallet {wallet_id[:12]}: unlock secret for approvals and the one backup reveal")
    except device_auth.DeviceAuthDenied as exc:
        raise _fault("wallet_device_auth_denied", reason=str(exc) or "denied", source_context=source_context) from None
    except device_auth.DeviceAuthUnavailable as exc:
        raise _fault("wallet_device_auth_unavailable", reason=str(exc) or "unavailable", source_context=source_context) from None
    nonce = os.urandom(12)
    ciphertext = AESGCM(_device_key(unlock_secret)).encrypt(nonce, bytes(secret), aad)
    return json.dumps({"ciphertext": _b64(ciphertext), "nonce": _b64(nonce), "sealkind": SEAL_POLICY})


def _unseal(row: dict[str, Any], credential: str, *, source_context: dict[str, Any] | None) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    spec = chains.resolve_network(row["network"])
    aad = _aad(spec, row["public_key"])
    if row["approval_method"] == custody.APPROVAL_DEVICE:
        authority = device_auth.current_authority()
        if not row["device_sealed_blob"] or not authority.available():
            raise _fault("wallet_device_auth_unavailable", wallet_id=row["wallet_id"], reason="no_device_authentication_on_this_machine", source_context=source_context)
        try:
            unlock_secret = authority.read_unlock_secret(row["wallet_id"], reason=f"VOOL: unlock Crypto Pilot wallet {custody.public_key_short(row['public_key'])}")
        except device_auth.DeviceAuthDenied as exc:
            raise _fault("wallet_device_auth_denied", wallet_id=row["wallet_id"], reason=str(exc) or "denied", source_context=source_context) from None
        except device_auth.DeviceAuthUnavailable as exc:
            raise _fault("wallet_device_auth_unavailable", wallet_id=row["wallet_id"], reason=str(exc) or "unavailable", source_context=source_context) from None
        blob = json.loads(row["device_sealed_blob"])
        try:
            return AESGCM(_device_key(unlock_secret)).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ciphertext"]), aad)
        except Exception:
            raise _fault("wallet_device_auth_denied", wallet_id=row["wallet_id"], reason="device_unseal_failed", source_context=source_context) from None
    if not row["sealed_blob"]:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="no_sealed_key", source_context=source_context)
    blob = json.loads(row["sealed_blob"])
    try:
        return AESGCM(_credential_key(credential, base64.b64decode(blob["salt"]))).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ciphertext"]), aad)
    except Exception:
        code = "wallet_password_invalid" if row["approval_method"] == custody.APPROVAL_PASSWORD else "wallet_pin_invalid"
        raise _fault(code, wallet_id=row["wallet_id"], reason="unseal_failed", source_context=source_context) from None


# --- the durable throttle ------------------------------------------------------------------------------

def _scopes(wallet_id: str) -> tuple[tuple[str, int], ...]:
    return ((f"wallet:{wallet_id}", WALLET_LOCK_AFTER), ("app", APP_LOCK_AFTER))


def _recovery_scopes(wallet_id: str) -> tuple[tuple[str, int], ...]:
    return ((f"{RECOVERY_SCOPE_PREFIX}{wallet_id}", WALLET_LOCK_AFTER), (RECOVERY_APP_SCOPE, APP_LOCK_AFTER))


def _per_wallet_scope(scope: str) -> bool:
    return scope.startswith("wallet:") or scope.startswith(RECOVERY_SCOPE_PREFIX)


def _lock_state(scopes: tuple[tuple[str, int], ...]) -> tuple[str, int] | None:
    now = time.time()
    with connection() as conn:
        for scope, _threshold in scopes:
            row = conn.execute("SELECT locked_until FROM wallet_unlock_attempts WHERE scope = ?", (scope,)).fetchone()
            if row and float(row[0] or 0) > now:
                return (scope, max(1, math.ceil(float(row[0]) - now)))
    return None


def _reserve_attempt(wallet_id: str, *, source_context: dict[str, Any] | None, scopes: tuple[tuple[str, int], ...] | None = None) -> None:
    """Count the attempt before the credential is tried; refuse while either scope is locked."""
    now = time.time()
    scopes = scopes or _scopes(wallet_id)
    locked: tuple[str, int] | None = None
    with connection() as conn:
        limits._begin_immediate(conn)
        for scope, _threshold in scopes:
            row = conn.execute("SELECT locked_until FROM wallet_unlock_attempts WHERE scope = ?", (scope,)).fetchone()
            if row and float(row[0] or 0) > now:
                locked = (scope, max(1, math.ceil(float(row[0]) - now)))
                break
        if locked is None:
            for scope, _threshold in scopes:
                conn.execute(
                    "INSERT INTO wallet_unlock_attempts (scope, failures, locked_until, updated_at) VALUES (?, 1, 0, ?)"
                    " ON CONFLICT(scope) DO UPDATE SET failures = failures + 1, updated_at = excluded.updated_at",
                    (scope, now),
                )
    if locked is not None:
        raise _fault(
            "wallet_unlock_throttled", wallet_id=wallet_id, reason="too_many_failed_unlocks" if not locked[0].startswith(RECOVERY_SCOPE_PREFIX) and locked[0] != RECOVERY_APP_SCOPE else "too_many_failed_recovery_attempts",
            scope="wallet" if _per_wallet_scope(locked[0]) else "app", retry_after_seconds=locked[1], source_context=source_context,
        )


def _settle_attempt(wallet_id: str, outcome: str, scopes: tuple[tuple[str, int], ...] | None = None) -> None:
    """success: the wallet count clears and the app count gives back this attempt; failure: lock once a scope
    reaches its threshold; refund: the attempt never reached a credential check."""
    now = time.time()
    with connection() as conn:
        limits._begin_immediate(conn)
        for scope, threshold in scopes or _scopes(wallet_id):
            row = conn.execute("SELECT failures FROM wallet_unlock_attempts WHERE scope = ?", (scope,)).fetchone()
            failures = int(row[0]) if row else 0
            if outcome == "failure":
                if failures >= threshold:
                    delay = min(LOCK_MAX_SECONDS, LOCK_BASE_SECONDS * (2 ** min(failures - threshold, 16)))
                    conn.execute("UPDATE wallet_unlock_attempts SET locked_until = ?, updated_at = ? WHERE scope = ?", (now + delay, now, scope))
                continue
            remaining = 0 if (outcome == "success" and _per_wallet_scope(scope)) else max(0, failures - 1)
            conn.execute("UPDATE wallet_unlock_attempts SET failures = ?, locked_until = 0, updated_at = ? WHERE scope = ?", (remaining, now, scope))


def _clear_wallet_unlock_attempts(wallet_id: str) -> None:
    """After a valid recovery proof only: the wallet's own unlock count and lock end (the app count is untouched)."""
    with connection() as conn:
        conn.execute("UPDATE wallet_unlock_attempts SET failures = 0, locked_until = 0, updated_at = ? WHERE scope = ?", (time.time(), f"wallet:{wallet_id}"))


def _generation(row: dict[str, Any]) -> int:
    try:
        return max(1, int(row.get("credential_generation") or 1))
    except (TypeError, ValueError):
        return 1


def credential_generation(wallet_id: str) -> int:
    row = _load(wallet_id)
    return _generation(row) if row else 0


def _verify(row: dict[str, Any], credential: str, *, source_context: dict[str, Any] | None) -> tuple[bytes, int]:
    """Open the seal through the throttle. The row is read again after the attempt is reserved: a recovery or a
    credential change that committed since the caller loaded it makes the old credential fail against the new seal,
    and the generation that was actually opened is returned so a signer can be fenced before the transmit site."""
    _reserve_attempt(row["wallet_id"], source_context=source_context)
    fresh = _load(row["wallet_id"]) or row
    if _generation(fresh) != _generation(row):
        _settle_attempt(row["wallet_id"], "refund")
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="credential_generation_changed", source_context=source_context)
    try:
        secret = _unseal(fresh, credential, source_context=source_context)
    except WalletFault as exc:
        failed = exc.code in {"wallet_pin_invalid", "wallet_password_invalid", "wallet_device_auth_denied"}
        _settle_attempt(row["wallet_id"], "failure" if failed else "refund")
        raise
    _settle_attempt(row["wallet_id"], "success")
    return secret, _generation(fresh)


# --- rows and views ------------------------------------------------------------------------------------

def _load(wallet_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {', '.join(_COLS)} FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id or ""),)).fetchone()
    return {name: (row[index] if row[index] is not None else "") for index, name in enumerate(_COLS)} if row else None


def _require_pilot_row(wallet_id: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    row = _load(wallet_id)
    if row is None:
        raise _fault("wallet_not_found", wallet_id=str(wallet_id)[:64], source_context=source_context)
    if row["seal_policy"] != SEAL_POLICY:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="not_a_crypto_pilot_wallet", source_context=source_context)
    return row


def _backup_target(spec: chains.ChainIdentity) -> str:
    if spec.is_svm:
        return "Phantom or another Solana wallet: import private key"
    if spec.chain_key == "bnb":
        return "An EVM-compatible wallet such as MetaMask: import private key (Phantom does not list BNB Smart Chain)"
    return "Phantom or MetaMask: import private key"


def _view(row: dict[str, Any], *, created: bool = False) -> dict[str, Any]:
    spec = chains.resolve_network(row["network"])
    view: dict[str, Any] = {
        "wallet_id": row["wallet_id"], "network": spec.network, "family": spec.family, "chain_key": spec.chain_key,
        "environment": spec.environment, "address": row["public_key"], "label": row["label"], "setup_state": row["setup_state"],
        "method": row["approval_method"], "seal_policy": row["seal_policy"], "revealed": bool(row["revealed_at"]), "created": created,
        "backup_format": BACKUP_FORMAT_SOLANA if spec.is_svm else BACKUP_FORMAT_EVM, "backup_target": _backup_target(spec),
        "credential_generation": _generation(row),
    }
    if row["setup_state"] == STATE_BACKUP_REVEALED or (row["setup_state"] == STATE_CANCELLED and row["revealed_at"]):
        view["note"] = BACKUP_UNACKNOWLEDGED_NOTE
    return view


def setup_view(wallet_id: str) -> dict[str, Any]:
    row = _require_pilot_row(wallet_id, source_context=None)
    return _view(row)


def _transition(wallet_id: str, expected: tuple[str, ...], new_state: str, **columns: Any) -> bool:
    sets = ["setup_state = ?", "setup_updated_at = ?"] + [f"{name} = ?" for name in columns]
    values = [new_state, utcnow(), *columns.values()]
    placeholders = ", ".join("?" for _ in expected)
    with connection() as conn:
        cursor = conn.execute(
            f"UPDATE wallet_profiles SET {', '.join(sets)} WHERE wallet_id = ? AND setup_state IN ({placeholders})",
            (*values, str(wallet_id), *expected),
        )
        return cursor.rowcount == 1


def _journal(kind: str, payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> None:
    from core.wallet import receipts

    receipts.journal_security_event(kind, payload, source_context=source_context)


# --- creation ------------------------------------------------------------------------------------------

def create_pilot_wallet(
    *, network: str, method: str, credential: str = "", credential_confirmation: str = "", creation_key: str = "",
    label: str = "", source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve, generate, seal. Returns the public setup view; never key material."""
    custody.require_enabled(source_context=source_context)
    if not str(network or "").strip():
        raise _fault("wallet_setup_state_invalid", reason="network_required", source_context=source_context)
    net = custody.require_network(network, source_context=source_context)
    spec = environment.require_active(net, source_context=source_context)
    chosen = str(method or "").strip().lower()
    if chosen not in METHODS:
        raise _fault("wallet_setup_state_invalid", reason="unknown_approval_method", methods=list(METHODS), source_context=source_context)
    if chosen == custody.APPROVAL_DEVICE:
        if not device_auth.current_authority().available():
            raise _fault("wallet_device_auth_unavailable", reason="no_device_authentication_on_this_machine", source_context=source_context)
    else:
        if str(credential) != str(credential_confirmation):
            raise _fault("wallet_credential_mismatch", reason="confirmation_differs", source_context=source_context)
        _require_credential_shape(chosen, credential, source_context=source_context)
    from network.signer import key_storage_mode

    if key_storage_mode() == "ephemeral":
        raise _fault("wallet_storage_class_refused", reason="ephemeral_key_storage", source_context=source_context)
    if spec.is_evm:
        from core.wallet import evm

        evm.require_evm_dependencies("pilot_wallet_creation", source_context=source_context)
    key = str(creation_key or "").strip()[:128]
    if not key:
        raise _fault("wallet_setup_state_invalid", reason="creation_key_required", source_context=source_context)

    wallet_id = f"wallet-{uuid.uuid4().hex[:16]}"
    now = utcnow()
    with connection() as conn:
        limits._begin_immediate(conn)
        existing = conn.execute("SELECT wallet_id FROM wallet_profiles WHERE network = ? AND creation_key = ?", (spec.network, key)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO wallet_profiles (wallet_id, mode, network, public_key, label, created_at, approval_method, is_default,"
                " seal_policy, setup_state, creation_key, setup_updated_at) VALUES (?, ?, ?, '', ?, ?, ?, 0, ?, ?, ?, ?)",
                (wallet_id, custody.MODE_POCKET_SEALED, spec.network, str(label or "")[:80], now, chosen, SEAL_POLICY, STATE_GENERATING, key, now),
            )
    if existing is not None:
        return _view(_load(existing[0]) or {})

    secret, address = _generate_material(spec)
    try:
        aad = _aad(spec, address)
        if chosen == custody.APPROVAL_DEVICE:
            sealed_blob, device_blob = "", _seal_with_device(wallet_id, secret, aad, source_context=source_context)
        else:
            sealed_blob, device_blob = _seal_with_credential(secret, credential, aad), ""
    finally:
        del secret
    if not _transition(wallet_id, (STATE_GENERATING,), STATE_AWAITING_BACKUP, public_key=address, sealed_blob=sealed_blob, device_sealed_blob=device_blob, seal_kind=SEAL_POLICY):
        raise _fault("wallet_setup_state_invalid", wallet_id=wallet_id, reason="setup_changed_during_generation", source_context=source_context)
    _journal("wallet_pilot_created", {"wallet_id": wallet_id, "network": spec.network, "family": spec.family, "address": address, "method": chosen}, source_context=source_context)
    return _view(_load(wallet_id) or {}, created=True)


def import_solana_pilot_wallet(
    *, network: str, backup_value: str, expected_address: str, method: str,
    credential: str, credential_confirmation: str, creation_key: str,
    backup_saved: bool, label: str = "", source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Local operator import of an already backed-up 64-byte Solana keypair.

    This is not a model tool or a remote endpoint. Import never replaces an existing
    profile or changes its credential. One transaction publishes the sealed, ready
    profile; a crash before commit leaves no partially imported wallet. Approval
    and signing subsequently use the same credential, throttle and signer as Create.
    """
    from core.vool_wallet import b58decode, b58encode
    from core.secret_redaction import register_exact_secret
    from network.signer import key_storage_mode

    custody.require_enabled(source_context=source_context)
    spec = environment.require_active(custody.require_network(network, source_context=source_context), source_context=source_context)
    if not spec.is_svm or backup_saved is not True:
        raise _fault("wallet_setup_state_invalid", reason="solana_backup_and_ack_required", source_context=source_context)
    if method not in (custody.APPROVAL_PIN, custody.APPROVAL_PASSWORD):
        raise _fault("wallet_setup_state_invalid", reason="import_requires_pin_or_password", source_context=source_context)
    if credential != credential_confirmation:
        raise _fault("wallet_credential_mismatch", reason="confirmation_differs", source_context=source_context)
    _require_credential_shape(method, credential, source_context=source_context)
    if key_storage_mode() == "ephemeral":
        raise _fault("wallet_storage_class_refused", reason="ephemeral_key_storage", source_context=source_context)
    key = str(creation_key or "").strip()
    if not key or len(key) > 128:
        raise _fault("wallet_setup_state_invalid", reason="creation_key_required", source_context=source_context)
    text = str(backup_value or "").strip()
    if not 64 <= len(text) <= 88:
        raise _fault("wallet_backup_unavailable", reason="invalid_solana_backup", source_context=source_context)
    register_exact_secret(text)
    try:
        raw = b58decode(text)
    except (ValueError, UnicodeError):
        raise _fault("wallet_backup_unavailable", reason="invalid_solana_backup", source_context=source_context) from None
    if len(raw) != 64:
        raise _fault("wallet_backup_unavailable", reason="invalid_solana_backup", source_context=source_context)
    secret, public = raw[:32], raw[32:]
    address = _svm_address(secret)
    if address != b58encode(public) or address != _svm_address_independent(secret) or address != expected_address:
        raise _fault("wallet_backup_unavailable", reason="backup_address_mismatch", source_context=source_context)
    sealed_blob = _seal_with_credential(secret, credential, _aad(spec, address))
    del secret, raw
    wallet_id, now = f"wallet-{uuid.uuid4().hex[:16]}", utcnow()
    with connection() as conn:
        limits._begin_immediate(conn)
        existing = conn.execute(
            "SELECT wallet_id, creation_key, public_key, approval_method, seal_policy, setup_state FROM wallet_profiles "
            "WHERE network = ? AND (creation_key = ? OR public_key = ?)", (spec.network, key, address),
        ).fetchall()
        if existing:
            if len(existing) != 1 or tuple(existing[0][1:]) != (key, address, method, SEAL_POLICY, STATE_READY):
                raise _fault("wallet_setup_state_invalid", reason="import_conflicts_with_existing_wallet", source_context=source_context)
            wallet_id = existing[0][0]
        else:
            conn.execute(
                "INSERT INTO wallet_profiles (wallet_id, mode, network, public_key, label, created_at, approval_method, is_default, "
                "sealed_blob, seal_kind, seal_policy, setup_state, creation_key, revealed_at, setup_updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (wallet_id, custody.MODE_POCKET_SEALED, spec.network, address, str(label or "")[:80], now, method,
                 sealed_blob, SEAL_POLICY, SEAL_POLICY, STATE_READY, key, now, now),
            )
    if existing:
        prove_credential(wallet_id, credential, source_context=source_context)
    else:
        _journal("wallet_pilot_imported", {"wallet_id": wallet_id, "network": spec.network, "address": address, "method": method}, source_context=source_context)
    return _view(_load(wallet_id) or {}, created=not bool(existing))


# --- the one reveal ------------------------------------------------------------------------------------

def _backup_for(spec: chains.ChainIdentity, secret: bytes, address: str) -> tuple[str, str]:
    if spec.is_svm:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from core.vool_wallet import b58encode

        public = Ed25519PrivateKey.from_private_bytes(bytes(secret)).public_key().public_bytes_raw()
        if b58encode(public) != address or _svm_address_independent(secret) != address:
            raise _fault("wallet_backup_unavailable", reason="backup_verification_failed")
        return b58encode(bytes(secret) + public), BACKUP_FORMAT_SOLANA
    if _evm_address_independent(secret).lower() != str(address).lower() or evm_address_for_key(secret).lower() != str(address).lower():
        raise _fault("wallet_backup_unavailable", reason="backup_verification_failed")
    return "0x" + bytes(secret).hex(), BACKUP_FORMAT_EVM


def _ack_key(wallet_id: str) -> str:
    return f"pilot_ack:{wallet_id}"


def reveal_pilot_backup(wallet_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The backup key, exactly once, to the caller that proves the credential and wins the compare-and-set."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["approval_method"] != custody.APPROVAL_DEVICE:
        _require_credential_shape(row["approval_method"], credential, source_context=source_context)
    if row["setup_state"] != STATE_AWAITING_BACKUP:
        reason = "already_revealed" if row["revealed_at"] else f"setup_state_{row['setup_state'] or 'unknown'}"
        raise _fault("wallet_backup_unavailable", wallet_id=row["wallet_id"], reason=reason, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    secret, _generation_opened = _verify(row, credential, source_context=source_context)
    try:
        backup_value, backup_format = _backup_for(spec, secret, row["public_key"])
    finally:
        del secret
    token = secrets.token_urlsafe(24)
    revealed_at = utcnow()
    with connection() as conn:
        cursor = conn.execute(
            "UPDATE wallet_profiles SET setup_state = ?, revealed_at = ?, setup_updated_at = ? WHERE wallet_id = ? AND setup_state = ?",
            (STATE_BACKUP_REVEALED, revealed_at, revealed_at, row["wallet_id"], STATE_AWAITING_BACKUP),
        )
        won = cursor.rowcount == 1
        if won:
            record = json.dumps({"digest": hashlib.sha256(token.encode()).hexdigest(), "expires_at": time.time() + ACK_TTL_SECONDS})
            conn.execute(
                "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (_ack_key(row["wallet_id"]), record, revealed_at),
            )
    if not won:
        raise _fault("wallet_backup_unavailable", wallet_id=row["wallet_id"], reason="already_revealed", source_context=source_context)
    from core.secret_redaction import register_exact_secret

    register_exact_secret(backup_value)
    _journal("wallet_pilot_backup_revealed", {"wallet_id": row["wallet_id"], "network": spec.network}, source_context=source_context)
    return {
        "wallet_id": row["wallet_id"], "network": spec.network, "address": row["public_key"], "backup_format": backup_format,
        "backup_value": backup_value, "backup_target": _backup_target(spec), "ack_token": token, "shown_once": True,
        "warning": "Store this backup offline. Anyone who has it can spend this wallet's funds. It will not be shown again.",
    }


# --- acknowledge, cancel, resume -------------------------------------------------------------------------

def _ack_token_matches(wallet_id: str, token: str) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (_ack_key(wallet_id),)).fetchone()
    if not row:
        return False
    try:
        record = json.loads(row[0])
    except ValueError:
        return False
    if time.time() > float(record.get("expires_at") or 0):
        return False
    return hmac.compare_digest(str(record.get("digest") or ""), hashlib.sha256(str(token).encode()).hexdigest())


class PilotSigner:
    """The signer a :func:`signing_session` yields: it holds the opened key only until the session closes, and it
    verifies every signature independently before handing it out. Python ``bytes`` cannot be zeroed; the reference is
    dropped on close and that limit is recorded."""

    def __init__(self, secret: bytes, *, public_key: str, family: str, wallet_id: str, generation: int = 1) -> None:
        self._secret: bytes = bytes(secret)
        self.public_key, self.family, self.wallet_id, self.closed = str(public_key), str(family), str(wallet_id), False
        #: the credential generation whose seal released this key; the lifecycle compares it before the transmit site
        self.generation = int(generation)

    def sign_svm(self, message: bytes) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from core.wallet.signers import _verify

        if self.closed or self.family != chains.FAMILY_SVM:
            raise _fault("wallet_signing_unavailable", wallet_id=self.wallet_id, reason="signing_session_closed" if self.closed else "not_a_solana_key")
        signature = Ed25519PrivateKey.from_private_bytes(self._secret).sign(bytes(message))
        if not _verify(self.public_key, bytes(message), signature):
            raise _fault("wallet_signature_invalid", wallet_id=self.wallet_id, reason="pilot_signature_mismatch")
        return signature

    def sign_evm_type2(self, params: dict[str, Any]) -> tuple[bytes, str]:
        """Sign one EIP-1559 native transfer from the quote's parameters. The signed bytes are decoded again and every
        field compared with the parameters, and the sender is recovered from the bytes, before anything is returned."""
        if self.closed or self.family != chains.FAMILY_EVM:
            raise _fault("wallet_signing_unavailable", wallet_id=self.wallet_id, reason="signing_session_closed" if self.closed else "not_an_evm_key")
        from eth_account import Account
        from eth_account.typed_transactions import TypedTransaction
        from eth_hash.auto import keccak
        from eth_utils import to_checksum_address
        from hexbytes import HexBytes

        expected = {
            "chainId": int(params["chain_id"]), "nonce": int(params["nonce"]), "maxPriorityFeePerGas": int(params["max_priority_fee_per_gas"]),
            "maxFeePerGas": int(params["max_fee_per_gas"]), "gas": int(params["gas_limit"]), "value": int(params["value"]),
        }
        to_address = to_checksum_address(str(params["to"]))
        signed = Account.sign_transaction({**expected, "type": 2, "to": to_address, "data": b"", "accessList": []}, self._secret)
        raw = bytes(signed.raw_transaction)
        if Account.recover_transaction(raw).lower() != self.public_key.lower():
            raise _fault("wallet_signature_invalid", wallet_id=self.wallet_id, reason="pilot_signer_mismatch")
        decoded = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
        decoded_to = decoded.get("to")
        decoded_to_text = ("0x" + bytes(decoded_to).hex()) if isinstance(decoded_to, (bytes, bytearray)) else str(decoded_to or "")
        seen = {name: int(decoded[name]) for name in expected}
        if seen != expected or decoded_to_text.lower() != to_address.lower() or bytes(decoded.get("data") or b"") != b"" or int(decoded.get("type") or 0) != 2:
            raise _fault("wallet_signature_invalid", wallet_id=self.wallet_id, reason="signed_fields_mismatch")
        return raw, "0x" + keccak(raw).hex()

    def close(self) -> None:
        self._secret = b""
        self.closed = True


@contextlib.contextmanager
def signing_session(wallet_id: str, credential: str, *, source_context: dict[str, Any] | None = None) -> Iterator[PilotSigner]:
    """Open a ready pilot wallet's seal ONCE (one throttle attempt, one device prompt) and yield a signer for the
    session's body; the key reference is dropped when the body ends, however it ends. Callers keep every network read
    outside the body: no secret exists while the chain is read."""
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] not in {"", STATE_READY}:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_not_finished", source_context=source_context)
    if row["approval_method"] != custody.APPROVAL_DEVICE:
        _require_credential_shape(row["approval_method"], credential, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    secret, generation = _verify(row, credential, source_context=source_context)
    signer = PilotSigner(secret, public_key=row["public_key"], family=spec.family, wallet_id=row["wallet_id"], generation=generation)
    del secret
    try:
        yield signer
    finally:
        signer.close()


def _prove_credential(row: dict[str, Any], credential: str, *, source_context: dict[str, Any] | None) -> None:
    if row["approval_method"] != custody.APPROVAL_DEVICE:
        _require_credential_shape(row["approval_method"], credential, source_context=source_context)
    secret, _generation_opened = _verify(row, credential, source_context=source_context)
    del secret


def prove_credential(wallet_id: str, credential: str, *, source_context: dict[str, Any] | None = None) -> None:
    """Prove a ready pilot wallet's credential by opening its seal through the durable throttle; no key leaves."""
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] not in {"", STATE_READY}:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_not_finished", source_context=source_context)
    _prove_credential(row, credential, source_context=source_context)


def acknowledge_pilot_backup(wallet_id: str, *, credential: str = "", ack_token: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """“I've saved my backup”: finishes setup. Needs the reveal's token or the credential; nothing else acknowledges."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] != STATE_BACKUP_REVEALED:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason=f"setup_state_{row['setup_state'] or 'unknown'}", source_context=source_context)
    if ack_token:
        if not _ack_token_matches(row["wallet_id"], ack_token):
            raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="acknowledgement_token_invalid", source_context=source_context)
    else:
        _prove_credential(row, credential, source_context=source_context)
    if not _transition(row["wallet_id"], (STATE_BACKUP_REVEALED,), STATE_READY):
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_changed", source_context=source_context)
    with connection() as conn:
        conn.execute("DELETE FROM wallet_controls WHERE key = ?", (_ack_key(row["wallet_id"]),))
    _journal("wallet_pilot_backup_acknowledged", {"wallet_id": row["wallet_id"], "network": row["network"]}, source_context=source_context)
    return _view(_load(row["wallet_id"]) or row)


def cancel_pilot_setup(wallet_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stop setup. The wallet row, its address and its sealed key are kept: a possibly funded wallet is never deleted."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    open_states = (STATE_GENERATING, STATE_AWAITING_BACKUP, STATE_BACKUP_REVEALED)
    if row["setup_state"] not in open_states:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason=f"setup_state_{row['setup_state'] or 'unknown'}", source_context=source_context)
    if row["setup_state"] != STATE_GENERATING:
        _prove_credential(row, credential, source_context=source_context)
    if not _transition(row["wallet_id"], open_states, STATE_CANCELLED):
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_changed", source_context=source_context)
    _journal("wallet_pilot_setup_cancelled", {"wallet_id": row["wallet_id"], "network": row["network"], "revealed": bool(row["revealed_at"])}, source_context=source_context)
    return _view(_load(row["wallet_id"]) or row)


def resume_pilot_setup(wallet_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Back to where setup stopped. A backup that was already revealed is never revealed again."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] != STATE_CANCELLED:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason=f"setup_state_{row['setup_state'] or 'unknown'}", source_context=source_context)
    if not row["public_key"]:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="no_key_was_generated", source_context=source_context)
    _prove_credential(row, credential, source_context=source_context)
    target = STATE_BACKUP_REVEALED if row["revealed_at"] else STATE_AWAITING_BACKUP
    if not _transition(row["wallet_id"], (STATE_CANCELLED,), target):
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_changed", source_context=source_context)
    _journal("wallet_pilot_setup_resumed", {"wallet_id": row["wallet_id"], "network": row["network"], "setup_state": target}, source_context=source_context)
    return _view(_load(row["wallet_id"]) or row)




# --- forgotten credential: recovery from the backup VOOL issued, or from an enrolled device secret --------------------
#
# The security decision, stated once: a PIN- or password-sealed key can be opened only by that credential or by the
# material this wallet issued or enrolled -- the one-time backup (the key itself) or an unlock secret the OS releases
# after fresh user presence. No countdown, elapsed time, support request, label or logged-in chat proves ownership,
# so none of them resets anything. Recovery re-seals the SAME key under a new credential in one fenced write; the
# address, the history, the reservations and every unresolved transfer are untouched. A wrong or foreign key writes
# nothing and is counted on the recovery throttle only.

_RECOVERABLE_STATES = (STATE_READY, STATE_BACKUP_REVEALED, STATE_CANCELLED, "")


def _require_recoverable_row(wallet_id: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] not in _RECOVERABLE_STATES or not row["public_key"] or not (row["sealed_blob"] or row["device_sealed_blob"]):
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason=f"recovery_needs_a_sealed_wallet:{row['setup_state'] or 'unknown'}", source_context=source_context)
    if row["setup_state"] in (STATE_BACKUP_REVEALED, STATE_CANCELLED) and not row["revealed_at"]:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="recovery_needs_a_revealed_backup", source_context=source_context)
    return row


def _decode_backup(spec: chains.ChainIdentity, backup_value: str, *, source_context: dict[str, Any] | None) -> tuple[bytes, str]:
    """The 32-byte key inside a backup VOOL issued, and the address it controls -- derived twice, by independent code.
    Anything else (another format, another family, a keypair whose halves disagree, a key outside the curve order) is
    a typed refusal that names the reason but never echoes the material."""
    text = str(backup_value or "").strip()
    if not text:
        raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_missing", source_context=source_context)
    if spec.is_svm:
        from core.vool_wallet import b58decode, b58encode

        if text.startswith("0x") or any(ch not in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for ch in text):
            raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_wrong_format_expected_solana_keypair_base58", source_context=source_context)
        try:
            raw = bytes(b58decode(text))
        except Exception:
            raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_malformed", source_context=source_context) from None
        if len(raw) != 64:
            raise _fault("wallet_recovery_refused", network=spec.network, reason=f"backup_malformed_expected_64_bytes_got_{len(raw)}", source_context=source_context)
        secret, public = raw[:32], raw[32:]
        try:
            address = _svm_address(secret)
        except Exception:
            raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_malformed", source_context=source_context) from None
        if b58encode(public) != address or _svm_address_independent(secret) != address:
            raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_keypair_halves_disagree", source_context=source_context)
        return secret, address
    body = text[2:] if text[:2].lower() == "0x" else text
    if len(body) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in body):
        reason = "backup_wrong_format_expected_evm_private_key_hex" if not text.startswith("0x") and len(text) > 70 else "backup_malformed_expected_32_byte_hex"
        raise _fault("wallet_recovery_refused", network=spec.network, reason=reason, source_context=source_context)
    secret = bytes.fromhex(body)
    if not (0 < int.from_bytes(secret, "big") < _SECP256K1_N):
        raise _fault("wallet_recovery_refused", network=spec.network, reason="backup_key_outside_curve_order", source_context=source_context)
    _evm_self_test()
    address = evm_address_for_key(secret)
    if _evm_address_independent(secret).lower() != address.lower():
        raise _fault("wallet_recovery_refused", network=spec.network, reason="evm_derivation_mismatch", source_context=source_context)
    return secret, address


def _same_address(spec: chains.ChainIdentity, left: str, right: str) -> bool:
    return str(left) == str(right) if spec.is_svm else str(left).lower() == str(right).lower()


def _staged_envelope(spec: chains.ChainIdentity, wallet_id: str, secret: bytes, address: str, method: str, credential: str, *, source_context: dict[str, Any] | None) -> tuple[str, str]:
    """The new seal, built before anything persistent changes (a device seal enrols a fresh unlock secret)."""
    aad = _aad(spec, address)
    if method == custody.APPROVAL_DEVICE:
        if not device_auth.current_authority().available():
            raise _fault("wallet_device_auth_unavailable", wallet_id=wallet_id, reason="no_device_authentication_on_this_machine", source_context=source_context)
        return "", _seal_with_device(wallet_id, secret, aad, source_context=source_context)
    return _seal_with_credential(secret, credential, aad), ""


def _commit_envelope(row: dict[str, Any], *, sealed_blob: str, device_blob: str, method: str, expected_generation: int, event: str,
                     source_context: dict[str, Any] | None) -> dict[str, Any]:
    """ONE fenced write: the new seal replaces the old only while the row still carries the generation the proof was
    made against; the generation advances; a setup left at backup_revealed/cancelled with a proven backup is ready.
    Then the wallet's unlock count clears, every open preview of the wallet is withdrawn (a fresh review is required),
    a device secret that no longer guards the key is forgotten, and the fact -- never the material -- is journaled."""
    from core.wallet import quotes

    wallet_id = row["wallet_id"]
    new_generation = int(expected_generation) + 1
    with connection() as conn:
        limits._begin_immediate(conn)
        cursor = conn.execute(
            "UPDATE wallet_profiles SET sealed_blob = ?, device_sealed_blob = ?, approval_method = ?, credential_generation = ?, setup_state = ?, setup_updated_at = ?"
            " WHERE wallet_id = ? AND credential_generation = ? AND seal_policy = ?",
            (sealed_blob, device_blob, method, new_generation, STATE_READY, utcnow(), wallet_id, int(expected_generation), SEAL_POLICY),
        )
        if cursor.rowcount != 1:
            raise _fault("wallet_setup_state_invalid", wallet_id=wallet_id, reason="recovery_conflict_generation_changed", source_context=source_context)
    if row["approval_method"] == custody.APPROVAL_DEVICE and method != custody.APPROVAL_DEVICE:
        # the device secret no longer opens anything: the blob it guarded is gone; a failed forget changes nothing
        with contextlib.suppress(Exception):
            device_auth.current_authority().forget(wallet_id)
    _clear_wallet_unlock_attempts(wallet_id)
    withdrawn = quotes.supersede_open_quotes_for_wallet(wallet_id, reason=event)
    _journal(event, {"wallet_id": wallet_id, "network": row["network"], "method_from": row["approval_method"], "method_to": method,
                     "credential_generation": new_generation, "previews_withdrawn": withdrawn}, source_context=source_context)
    view = _view(_load(wallet_id) or row)
    view.update({"recovered": event != "wallet_pilot_credential_changed", "credential_generation": new_generation, "previews_withdrawn": withdrawn,
                 "note": "The old credential no longer opens this wallet on this device. Open payment previews were withdrawn; review them again before approving. "
                         "The blockchain key is unchanged: anyone holding a copy of the exported private key still controls the address."})
    return view


def _require_new_credential(method: str, credential: str, confirmation: str, *, source_context: dict[str, Any] | None) -> str:
    chosen = str(method or "").strip().lower()
    if chosen not in METHODS:
        raise _fault("wallet_setup_state_invalid", reason="unknown_approval_method", methods=list(METHODS), source_context=source_context)
    if chosen != custody.APPROVAL_DEVICE:
        if str(credential) != str(confirmation):
            raise _fault("wallet_credential_mismatch", reason="confirmation_differs", source_context=source_context)
        _require_credential_shape(chosen, credential, source_context=source_context)
    return chosen


def recovery_challenge(row: dict[str, Any]) -> str:
    """The handle a device recovery must present: this wallet, this generation. A reset makes every older one stale."""
    return f"recover:{row['wallet_id']}:{_generation(row)}"


def recovery_options(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """What recovery this wallet actually offers, from its own record: the mandatory backup path, the device path
    only when an enrolled secret exists and the machine can release it, the external-import exit, and the plain
    statement that nothing else resets access. Reading this changes nothing."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    authority = device_auth.current_authority()
    enrolled = bool(row["device_sealed_blob"])
    device_available = enrolled and authority.available()
    if device_available:
        device_reason = ""
    elif enrolled:
        device_reason = "device_authentication_unavailable_on_this_machine"
    else:
        device_reason = "not_enrolled"
    locked = _lock_state(_recovery_scopes(row["wallet_id"]))
    unlock_locked = _lock_state(_scopes(row["wallet_id"]))
    if spec.is_svm:
        guidance = ["Phantom (Solana): Settings → Manage accounts → Add account → Import private key; paste the base58 keypair backup.",
                    "The key restores this one Solana account; it does not carry accounts derived from a seed phrase."]
    elif spec.chain_key == "bnb":
        guidance = ["An EVM-compatible wallet such as MetaMask: import account → private key; paste the 0x-prefixed hex backup.",
                    "Phantom does not list BNB Smart Chain; choose a wallet that supports chain 56/97."]
    else:
        guidance = ["Phantom (EVM) or MetaMask: import account → private key; paste the 0x-prefixed hex backup.",
                    "Check that the wallet you import into supports this exact network and environment before sending."]
    return {
        "wallet_id": row["wallet_id"], "network": spec.network, "display_name": spec.display_name, "environment": spec.environment,
        "address": row["public_key"], "label": row["label"], "method": row["approval_method"], "setup_state": row["setup_state"],
        "credential_generation": _generation(row), "recoverable": row["setup_state"] in _RECOVERABLE_STATES and bool(row["sealed_blob"] or row["device_sealed_blob"]),
        "backup": {
            "format": BACKUP_FORMAT_SOLANA if spec.is_svm else BACKUP_FORMAT_EVM,
            "hint": "the base58 keypair VOOL showed once at setup" if spec.is_svm else "the 0x-prefixed 64-hex-character private key VOOL showed once at setup",
            "restores": "this wallet's own address only; a key for another address is refused, never written over this wallet",
            "retry_after_seconds": locked[1] if locked else 0,
        },
        "device_recovery": {"available": device_available, "enrolled": enrolled, "reason": device_reason, "challenge": recovery_challenge(row) if device_available else "",
                            "note": "Device recovery works only for a wallet that already keeps a device-protected copy; VOOL never enrols one after the credential is forgotten."},
        "external_import": {"target": _backup_target(spec), "guidance": guidance, "references": [PHANTOM_RESTORE_REFERENCE, PHANTOM_LOGIN_LIMITS_REFERENCE],
                            "note": "The saved private key controls the address independently of VOOL's PIN. VOOL never opens another wallet's secret form or moves the key there."},
        "no_reset": {"text": "Without the saved backup or an enrolled device secret, VOOL cannot reset access to this wallet. The public address, its status and its records stay visible; "
                             "nothing is deleted, no replacement address is created and no funds move."},
        "unlock_retry_after_seconds": unlock_locked[1] if unlock_locked else 0,
        "pin_change_note": "Changing a remembered PIN is a separate step that needs the current PIN (Settings → Wallet → Payment approval).",
    }


def recover_with_backup(wallet_id: str, *, backup_value: str, method: str, credential: str = "", credential_confirmation: str = "",
                        source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The mandatory path: the backup VOOL issued for THIS wallet proves control; the same key is re-sealed under the
    new credential in one fenced write. Malformed, foreign or inconsistent material is refused before any write."""
    from core.secret_redaction import register_exact_secret

    custody.require_enabled(source_context=source_context)
    text = str(backup_value or "").strip()
    if text:
        register_exact_secret(text)
    row = _require_recoverable_row(wallet_id, source_context=source_context)
    chosen = _require_new_credential(method, credential, credential_confirmation, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    expected_generation = _generation(row)
    _reserve_attempt(row["wallet_id"], source_context=source_context, scopes=_recovery_scopes(row["wallet_id"]))
    try:
        secret, address = _decode_backup(spec, text, source_context=source_context)
        if not _same_address(spec, address, row["public_key"]):
            del secret
            raise _fault("wallet_recovery_refused", wallet_id=row["wallet_id"], network=spec.network, reason="backup_for_another_wallet", source_context=source_context)
    except WalletFault as exc:
        _settle_attempt(row["wallet_id"], "failure" if exc.code == "wallet_recovery_refused" else "refund", scopes=_recovery_scopes(row["wallet_id"]))
        raise
    _settle_attempt(row["wallet_id"], "success", scopes=_recovery_scopes(row["wallet_id"]))
    try:
        sealed_blob, device_blob = _staged_envelope(spec, row["wallet_id"], secret, row["public_key"], chosen, credential, source_context=source_context)
    finally:
        del secret
    return _commit_envelope(row, sealed_blob=sealed_blob, device_blob=device_blob, method=chosen, expected_generation=expected_generation,
                            event="wallet_pilot_recovered_with_backup", source_context=source_context)


def recover_with_device(wallet_id: str, *, challenge: str, method: str, credential: str = "", credential_confirmation: str = "",
                        source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The conditional path: only a wallet that already keeps a device-protected copy. Fresh user presence releases the
    wallet-bound unlock secret; the copy is opened against this wallet's AAD; the same key is re-sealed under the new
    credential. The challenge binds the operation to this wallet and its current generation: a stale one is refused
    before any prompt. A denial or a cancellation writes nothing and counts on the recovery throttle."""
    custody.require_enabled(source_context=source_context)
    row = _require_recoverable_row(wallet_id, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    if not row["device_sealed_blob"]:
        raise _fault("wallet_device_auth_unavailable", wallet_id=row["wallet_id"], reason="device_recovery_not_enrolled", source_context=source_context)
    authority = device_auth.current_authority()
    if not authority.available():
        raise _fault("wallet_device_auth_unavailable", wallet_id=row["wallet_id"], reason="no_device_authentication_on_this_machine", source_context=source_context)
    if not hmac.compare_digest(str(challenge or ""), recovery_challenge(row)):
        raise _fault("wallet_recovery_refused", wallet_id=row["wallet_id"], reason="stale_or_foreign_recovery_challenge", source_context=source_context)
    chosen = _require_new_credential(method, credential, credential_confirmation, source_context=source_context)
    expected_generation = _generation(row)
    _reserve_attempt(row["wallet_id"], source_context=source_context, scopes=_recovery_scopes(row["wallet_id"]))
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            unlock_secret = authority.read_unlock_secret(row["wallet_id"], reason=f"VOOL: recover Crypto Pilot wallet {custody.public_key_short(row['public_key'])} (generation {expected_generation}) and set a new {chosen}")
        except device_auth.DeviceAuthDenied as exc:
            raise _fault("wallet_device_auth_denied", wallet_id=row["wallet_id"], reason=str(exc) or "denied", source_context=source_context) from None
        except device_auth.DeviceAuthUnavailable as exc:
            raise _fault("wallet_device_auth_unavailable", wallet_id=row["wallet_id"], reason=str(exc) or "unavailable", source_context=source_context) from None
        blob = json.loads(row["device_sealed_blob"])
        try:
            secret = AESGCM(_device_key(unlock_secret)).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ciphertext"]), _aad(spec, row["public_key"]))
        except Exception:
            raise _fault("wallet_device_auth_denied", wallet_id=row["wallet_id"], reason="device_unseal_failed", source_context=source_context) from None
        derived = _svm_address(secret) if spec.is_svm else evm_address_for_key(secret)
        if not _same_address(spec, derived, row["public_key"]):
            del secret
            raise _fault("wallet_recovery_refused", wallet_id=row["wallet_id"], reason="device_copy_is_not_this_wallets_key", source_context=source_context)
    except WalletFault as exc:
        _settle_attempt(row["wallet_id"], "failure" if exc.code in {"wallet_device_auth_denied", "wallet_recovery_refused"} else "refund", scopes=_recovery_scopes(row["wallet_id"]))
        raise
    _settle_attempt(row["wallet_id"], "success", scopes=_recovery_scopes(row["wallet_id"]))
    try:
        sealed_blob, device_blob = _staged_envelope(spec, row["wallet_id"], secret, row["public_key"], chosen, credential, source_context=source_context)
    finally:
        del secret
    return _commit_envelope(row, sealed_blob=sealed_blob, device_blob=device_blob, method=chosen, expected_generation=expected_generation,
                            event="wallet_pilot_recovered_with_device", source_context=source_context)


def change_pilot_credential(wallet_id: str, *, current_credential: str, method: str, credential: str = "", credential_confirmation: str = "",
                            source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The separate, authenticated path for a REMEMBERED credential: the current one (or the enrolled device secret)
    opens the seal through the unlock throttle; the same key is re-sealed under the new credential, fenced like a
    recovery. Resetting the local credential never rotates the blockchain key."""
    custody.require_enabled(source_context=source_context)
    row = _require_pilot_row(wallet_id, source_context=source_context)
    if row["setup_state"] not in {"", STATE_READY}:
        raise _fault("wallet_setup_state_invalid", wallet_id=row["wallet_id"], reason="setup_not_finished", source_context=source_context)
    chosen = _require_new_credential(method, credential, credential_confirmation, source_context=source_context)
    if row["approval_method"] != custody.APPROVAL_DEVICE:
        _require_credential_shape(row["approval_method"], current_credential, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    secret, generation = _verify(row, current_credential, source_context=source_context)
    try:
        sealed_blob, device_blob = _staged_envelope(spec, row["wallet_id"], secret, row["public_key"], chosen, credential, source_context=source_context)
    finally:
        del secret
    return _commit_envelope(row, sealed_blob=sealed_blob, device_blob=device_blob, method=chosen, expected_generation=generation,
                            event="wallet_pilot_credential_changed", source_context=source_context)


__all__ = [
    "BACKUP_FORMAT_EVM",
    "BACKUP_FORMAT_SOLANA",
    "BACKUP_UNACKNOWLEDGED_NOTE",
    "KAT_EVM_K1_ADDRESS",
    "METHODS",
    "SEAL_POLICY",
    "STATE_AWAITING_BACKUP",
    "STATE_BACKUP_REVEALED",
    "STATE_CANCELLED",
    "STATE_GENERATING",
    "STATE_READY",
    "acknowledge_pilot_backup",
    "cancel_pilot_setup",
    "change_pilot_credential",
    "create_pilot_wallet",
    "credential_generation",
    "evm_address_for_key",
    "recover_with_backup",
    "recover_with_device",
    "recovery_challenge",
    "recovery_options",
    "resume_pilot_setup",
    "reveal_pilot_backup",
    "setup_view",
]
