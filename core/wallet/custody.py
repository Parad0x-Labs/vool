"""Custody: WHO holds the key. Watch-only by default, external signing preferred, and a sealed
pocket wallet only behind a typed warning and the exact confirmation phrase.

The pocket seed is AES-256-GCM sealed under a key that needs BOTH this device's local secret
and the owner's PIN. It is unsealed only inside :func:`_unseal_seed`, only for the duration of
one signature, and never returned to any caller outside :mod:`core.wallet.signers`.

The recovery phrase is a standard 12-word BIP39 mnemonic; the sealed key is the Phantom-compatible
derivation of it (m/44'/501'/0'/0', see :mod:`core.wallet.mnemonic`), so the same phrase imported
into any standard Solana wallet yields the same address. The phrase is returned to the caller
exactly once at creation and not retained in any form: no column, cache, log line or receipt.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.vool_wallet import b58encode, is_solana_pubkey
from core.wallet import config, device_auth, environment, mnemonic
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.custody"

MODE_WATCH_ONLY = "watch_only"
MODE_EXTERNAL_SIGNER = "external_signer"
MODE_POCKET_SEALED = "pocket_sealed"
MODES = (MODE_WATCH_ONLY, MODE_EXTERNAL_SIGNER, MODE_POCKET_SEALED)

POCKET_CONFIRMATION_PHRASE = "I ACCEPT THAT THIS DEVICE HOLDS THE KEY"
POCKET_WARNING_TEXT = (
    "A VOOL Wallet stores a signing key on this device, sealed under your PIN. Anyone with this device "
    "and your PIN can spend from it, and VOOL cannot recover funds if the device or the PIN is lost. "
    "Watch-only and external-wallet signing keep the key off this device and are the recommended modes. "
    "The recovery phrase is shown exactly once at creation and is never stored."
)

_PIN_MIN, _PIN_MAX = 4, 8  # operator decision 2026-09-07: a wallet PIN is 4-8 digits
_PASSWORD_MIN, _PASSWORD_MAX = 10, 128
#: How the owner approves a payment from a pocket wallet. Export is device-only whatever the choice.
APPROVAL_PIN = "pin"
APPROVAL_PASSWORD = "password"
APPROVAL_DEVICE = "device"
APPROVAL_METHODS = (APPROVAL_PIN, APPROVAL_PASSWORD, APPROVAL_DEVICE)
_KDF_ITERATIONS = 200_000
_SEAL_LABEL = "vool-wallet-pocket-seal-v1"
_KDF_NAME = "pbkdf2-sha256-200000+device-secret-v1"


@dataclass(frozen=True)
class WalletProfile:
    wallet_id: str
    mode: str
    network: str
    public_key: str
    label: str
    created_at: str
    approval_method: str = APPROVAL_PIN

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_id": self.wallet_id, "mode": self.mode, "network": self.network, "public_key": self.public_key,
            "label": self.label, "created_at": self.created_at, "can_sign": self.mode != MODE_WATCH_ONLY,
            "approval_method": self.approval_method if self.mode == MODE_POCKET_SEALED else "",
        }


@dataclass(frozen=True)
class PocketCreation:
    profile: WalletProfile
    #: Shown once. Not retained anywhere. Callers must not log, persist or forward it.
    recovery_phrase: str


def preferred_signing_mode() -> str:
    return MODE_EXTERNAL_SIGNER


def require_enabled(*, source_context: dict[str, Any] | None = None) -> None:
    if not config.wallet_enabled():
        raise wallet_fault("wallet_disabled", authority=AUTHORITY, source_context=source_context, dedupe="wallet-disabled")


def require_network(network: str, *, source_context: dict[str, Any] | None = None) -> str:
    """Validate against the one chain registry; the declared form the caller used is kept so
    existing `solana-devnet` profiles stay byte-identical. Canonical identity is derived
    through :mod:`core.wallet.chains` wherever two names must compare equal."""
    from core.wallet import chains

    name = str(network or "").strip() or config.NETWORK_SOLANA_DEVNET
    try:
        chains.resolve_network(name)
    except WalletFault:
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": name}, source_context=source_context) from None
    return name


def _row_to_profile(row: Any) -> WalletProfile:
    return WalletProfile(wallet_id=row[0], mode=row[1], network=row[2], public_key=row[3], label=row[4], created_at=row[5], approval_method=str(row[6] or APPROVAL_PIN) if len(row) > 6 else APPROVAL_PIN)


_SELECT = "SELECT wallet_id, mode, network, public_key, label, created_at, approval_method FROM wallet_profiles"


def _insert(profile: WalletProfile, *, sealed_blob: str = "", pin_hash: str = "", pin_salt: str = "", device_sealed_blob: str = "", seal_kind: str = "") -> WalletProfile:
    with connection() as conn:
        conn.execute("UPDATE wallet_profiles SET is_default = 0")
        conn.execute(
            "INSERT INTO wallet_profiles (wallet_id, mode, network, public_key, label, created_at, sealed_blob, pin_hash, pin_salt, device_sealed_blob, seal_kind, approval_method, is_default)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (profile.wallet_id, profile.mode, profile.network, profile.public_key, profile.label, profile.created_at, sealed_blob, pin_hash, pin_salt, device_sealed_blob, seal_kind, profile.approval_method or APPROVAL_PIN),
        )
    return profile


def _new_profile(mode: str, public_key: str, network: str, label: str) -> WalletProfile:
    return WalletProfile(wallet_id=f"wallet-{uuid.uuid4().hex[:16]}", mode=mode, network=network, public_key=public_key, label=str(label or ""), created_at=utcnow())


def _require_pubkey(public_key: str, *, network: str = config.NETWORK_SOLANA_DEVNET) -> str:
    """The account address, shaped for the wallet's chain family: base58 on svm, EIP-55
    tolerant hex on evm. A mismatched shape is not an account."""
    from core.wallet import chains

    key = str(public_key or "").strip()
    try:
        spec = chains.resolve_network(network)
    except WalletFault:
        spec = None  # type: ignore[assignment]
    family = spec.family if spec is not None else chains.FAMILY_SVM
    if family == chains.FAMILY_EVM:
        if not chains.EVM_ADDRESS_RE.match(key):
            raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "invalid_public_key"})
        return key
    if not is_solana_pubkey(key):
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "invalid_public_key"})
    return key


def create_watch_only_wallet(public_key: str, *, network: str = config.NETWORK_SOLANA_DEVNET, label: str = "", source_context: dict[str, Any] | None = None) -> WalletProfile:
    require_enabled(source_context=source_context)
    net = require_network(network, source_context=source_context)
    environment.require_active(net, source_context=source_context)
    return _insert(_new_profile(MODE_WATCH_ONLY, _require_pubkey(public_key, network=net), net, label))


def register_external_signer_wallet(public_key: str, *, network: str = config.NETWORK_SOLANA_DEVNET, label: str = "", source_context: dict[str, Any] | None = None) -> WalletProfile:
    require_enabled(source_context=source_context)
    net = require_network(network, source_context=source_context)
    environment.require_active(net, source_context=source_context)
    return _insert(_new_profile(MODE_EXTERNAL_SIGNER, _require_pubkey(public_key, network=net), net, label))


def evm_pocket_custody_available() -> bool:
    """Always False in this lane: there is no in-process EVM private-key custody. A
    separately versioned, explicitly opted-in recovery design that provably leaves every
    existing Solana account untouched would be reviewed as its own change."""
    return False


def refuse_evm_pocket_custody(*, source_context: dict[str, Any] | None = None) -> WalletFault:
    return wallet_fault(
        "evm_pocket_custody_unavailable", authority=AUTHORITY,
        context={"reason": "no_in_process_evm_custody", "modes": "watch_only,external_signer"},
        source_context=source_context,
    )


# --- pocket wallet -----------------------------------------------------------------------------

def _valid_pin(pin: str) -> bool:
    value = str(pin or "")
    return value.isdigit() and _PIN_MIN <= len(value) <= _PIN_MAX


def _valid_password(password: str) -> bool:
    """A wallet-only password: 10-128 characters and not just digits (that would be a PIN with a weaker policy)."""
    value = str(password or "")
    return _PASSWORD_MIN <= len(value) <= _PASSWORD_MAX and not value.isdigit()


def _validate_secret_for(method: str, *, pin: str, password: str, source_context: dict[str, Any] | None) -> str:
    """The secret that seals the seed for this approval method ("" for device), after policy."""
    if method == APPROVAL_PIN:
        if not _valid_pin(pin):
            raise wallet_fault("wallet_pin_invalid", authority=AUTHORITY, context={"reason": "pin_policy", "policy": f"{_PIN_MIN}-{_PIN_MAX} digits"}, source_context=source_context)
        return str(pin)
    if method == APPROVAL_PASSWORD:
        if not _valid_password(password):
            raise wallet_fault("wallet_password_invalid", authority=AUTHORITY, context={"reason": "password_policy", "policy": f"{_PASSWORD_MIN}-{_PASSWORD_MAX} characters, not only digits"}, source_context=source_context)
        return str(password)
    if method == APPROVAL_DEVICE:
        if not device_auth.current_authority().available():
            raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"reason": "no_device_authentication_on_this_machine", "remediation": "Choose a PIN or a password on this machine, or use a Mac with Touch ID or a login password available to the app."}, source_context=source_context)
        return ""
    raise wallet_fault("wallet_confirmation_required", authority=AUTHORITY, context={"reason": "unknown_approval_method", "methods": list(APPROVAL_METHODS)}, source_context=source_context)


def _pin_key(pin: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), salt, _KDF_ITERATIONS, dklen=32)


def _device_secret() -> bytes:
    from network.signer import derive_local_secret

    return bytes(derive_local_secret(_SEAL_LABEL, length=32))


def _seal_key(pin: str, salt: bytes) -> bytes:
    return hashlib.sha256(_device_secret() + _pin_key(pin, salt)).digest()


#: What the sealed ciphertext holds. v1 (before 2026-09-07): the derived 32-byte Ed25519 secret, so only the
#: Solana key ever existed. v2: the 64-byte BIP-39 seed, so the Phantom key (m/44'/501'/0'/0') and the MetaMask
#: key (m/44'/60'/0'/0/0) both derive on demand and the wallet can be exported into either.
SEAL_KIND_ED25519_SECRET_V1 = "sealv1-ed25519"
SEAL_KIND_BIP39_SEED_V2 = "sealv2-bip39"
EVM_DERIVATION_PATH = "m/44'/60'/0'/0/0"


def _seal(seed: bytes, *, pin: str, public_key: str, kind: str = SEAL_KIND_BIP39_SEED_V2) -> tuple[str, str, str]:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_seal_key(pin, salt)).encrypt(nonce, seed, public_key.encode("ascii"))
    blob = json.dumps({"ciphertext": base64.b64encode(ciphertext).decode(), "nonce": base64.b64encode(nonce).decode(), "salt": base64.b64encode(salt).decode(), "kdf": _KDF_NAME, "sealkind": kind})
    pin_salt = os.urandom(16)
    pin_hash = _pin_key(pin, pin_salt).hex()
    return blob, pin_hash, base64.b64encode(pin_salt).decode()


def _device_seal_key(unlock_secret: bytes) -> bytes:
    return hashlib.sha256(_device_secret() + b"device-unlock" + bytes(unlock_secret)).digest()


def _device_seal(material: bytes, *, unlock_secret: bytes, public_key: str, kind: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_device_seal_key(unlock_secret)).encrypt(nonce, material, public_key.encode("ascii"))
    return json.dumps({"ciphertext": base64.b64encode(ciphertext).decode(), "nonce": base64.b64encode(nonce).decode(), "sealkind": kind})


def _bind_device(wallet_id: str, material: bytes, *, public_key: str, kind: str) -> str:
    """Seal the material a second time under a random unlock secret that lives only in the device's Keychain
    (user-presence). Storing never prompts; reading -- export, device approval -- does. Returns "" when this
    machine has no device authentication: the wallet still works by PIN, and export is then unavailable."""
    authority = device_auth.current_authority()
    if not authority.available():
        return ""
    unlock_secret = os.urandom(32)
    try:
        authority.store_unlock_secret(wallet_id, unlock_secret, reason=f"VOOL wallet {wallet_id[:12]}: unlock secret for export and device approval")
    except device_auth.DeviceAuthUnavailable:
        return ""
    except device_auth.DeviceAuthDenied:
        return ""
    return _device_seal(material, unlock_secret=unlock_secret, public_key=public_key, kind=kind)


def seal_kind(wallet_id: str) -> str:
    with connection() as conn:
        row = conn.execute("SELECT sealed_blob, seal_kind FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    if not row or not row[0]:
        return ""
    if row[1]:
        return str(row[1])
    try:
        return str(json.loads(row[0]).get("sealkind") or SEAL_KIND_ED25519_SECRET_V1)
    except Exception:
        return SEAL_KIND_ED25519_SECRET_V1


#: A Crypto Pilot wallet: credential-first setup, one backup reveal, no offline verifier (core.wallet.pilot_custody).
PILOT_SEAL_POLICY = "pilot-v3"


def seal_policy(wallet_id: str) -> str:
    with connection() as conn:
        row = conn.execute("SELECT seal_policy FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    return str(row[0] or "") if row else ""


def setup_state(wallet_id: str) -> str:
    with connection() as conn:
        row = conn.execute("SELECT setup_state FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    return str(row[0] or "") if row else ""


def device_bound(wallet_id: str) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT device_sealed_blob FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    return bool(row and row[0])


def _solana_secret_from(kind: str, material: bytes) -> bytes:
    """The 32-byte Ed25519 secret the signer needs, whatever the seal holds."""
    if kind == SEAL_KIND_BIP39_SEED_V2:
        return bytes(mnemonic.derive_solana_keypair(material).secret())
    return bytes(material)


def _pubkey_of(seed: bytes) -> str:
    return b58encode(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw())


def _create_pocket_from_mnemonic(phrase: str, *, pin: str, network: str, label: str, approval_method: str = APPROVAL_PIN) -> WalletProfile:
    """Seal the BIP-39 SEED of the phrase (kind v2); the Phantom key and the MetaMask key derive from it on demand.
    The phrase itself is never stored. Self-check: the seed's Solana derivation equals the phrase's."""
    seed = mnemonic.mnemonic_to_seed(phrase)
    keypair = mnemonic.derive_solana_keypair(seed)
    if str(mnemonic.keypair_for_mnemonic(phrase).pubkey()) != str(keypair.pubkey()):
        raise wallet_fault("wallet_confirmation_required", authority=AUTHORITY, context={"reason": "derivation_self_check_failed"})
    public_key = str(keypair.pubkey())
    del keypair
    return _create_pocket_from_material(seed, kind=SEAL_KIND_BIP39_SEED_V2, public_key=public_key, pin=pin, network=network, label=label, approval_method=approval_method)


def _create_pocket_from_material(material: bytes, *, kind: str, public_key: str, pin: str, network: str, label: str, approval_method: str = APPROVAL_PIN) -> WalletProfile:
    """Seal per approval method. PIN/password: the secret seals the seed, and the device blob is added when the
    machine can (export). Device: NO secret-sealed copy exists -- the seed lives only behind the Keychain
    user-presence item, so every approval is a Touch ID / password prompt."""
    profile = _new_profile(MODE_POCKET_SEALED, public_key, network, label)
    profile = WalletProfile(**{**profile.__dict__, "approval_method": approval_method})
    device_blob = _bind_device(profile.wallet_id, material, public_key=public_key, kind=kind)
    if approval_method == APPROVAL_DEVICE:
        if not device_blob:
            raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"reason": "device_binding_failed"})
        return _insert(profile, sealed_blob="", pin_hash="", pin_salt="", device_sealed_blob=device_blob, seal_kind=kind)
    blob, pin_hash, pin_salt = _seal(material, pin=pin, public_key=public_key, kind=kind)
    return _insert(profile, sealed_blob=blob, pin_hash=pin_hash, pin_salt=pin_salt, device_sealed_blob=device_blob, seal_kind=kind)


def _create_pocket_from_seed(seed: bytes, *, pin: str, network: str, label: str) -> WalletProfile:
    """Legacy entry (a bare Ed25519 secret): kept for callers that hold one; new wallets seal the BIP-39 seed."""
    return _create_pocket_from_material(bytes(seed), kind=SEAL_KIND_ED25519_SECRET_V1, public_key=_pubkey_of(seed), pin=pin, network=network, label=label)


def _create_legacy_pocket_for_tests(*, pin: str, network: str = config.NETWORK_SOLANA_DEVNET) -> WalletProfile:
    """A wallet exactly as the pre-2026-09-07 code sealed it (32-byte secret, no kind), for compatibility tests."""
    require_enabled()
    keypair = mnemonic.keypair_for_mnemonic(mnemonic.generate_mnemonic())
    secret = bytes(keypair.secret())
    public_key = str(keypair.pubkey())
    blob, pin_hash, pin_salt = _seal(secret, pin=pin, public_key=public_key, kind=SEAL_KIND_ED25519_SECRET_V1)
    legacy = json.loads(blob)
    legacy.pop("sealkind", None)
    profile = _new_profile(MODE_POCKET_SEALED, public_key, network, "")
    device_blob = _bind_device(profile.wallet_id, secret, public_key=public_key, kind=SEAL_KIND_ED25519_SECRET_V1)
    return _insert(profile, sealed_blob=json.dumps(legacy), pin_hash=pin_hash, pin_salt=pin_salt, device_sealed_blob=device_blob, seal_kind="")


def create_pocket_wallet(*, acknowledged_warning: bool, confirmation_phrase: str, pin: str = "", password: str = "", approval_method: str = APPROVAL_PIN, network: str = config.NETWORK_SOLANA_DEVNET, label: str = "", source_context: dict[str, Any] | None = None) -> PocketCreation:
    require_enabled(source_context=source_context)
    if not acknowledged_warning or str(confirmation_phrase or "").strip() != POCKET_CONFIRMATION_PHRASE:
        raise wallet_fault("wallet_confirmation_required", authority=AUTHORITY, context={"reason": "typed_confirmation_missing"}, source_context=source_context)
    method = str(approval_method or APPROVAL_PIN).strip().lower()
    secret = _validate_secret_for(method, pin=pin, password=password, source_context=source_context)
    net = require_network(network, source_context=source_context)
    _require_legacy_test_row(net, source_context=source_context)
    environment.require_active(net, source_context=source_context)
    _require_pocket_family(net, source_context=source_context)
    phrase = mnemonic.generate_mnemonic()
    profile = _create_pocket_from_mnemonic(phrase, pin=secret, network=net, label=label, approval_method=method)
    return PocketCreation(profile=profile, recovery_phrase=phrase)


def restore_pocket_wallet(recovery_phrase: str, *, pin: str = "", password: str = "", approval_method: str = APPROVAL_PIN, network: str = config.NETWORK_SOLANA_DEVNET, label: str = "", source_context: dict[str, Any] | None = None) -> WalletProfile:
    require_enabled(source_context=source_context)
    method = str(approval_method or APPROVAL_PIN).strip().lower()
    secret = _validate_secret_for(method, pin=pin, password=password, source_context=source_context)
    net = require_network(network, source_context=source_context)
    _require_legacy_test_row(net, source_context=source_context)
    environment.require_active(net, source_context=source_context)
    _require_pocket_family(net, source_context=source_context)
    if not mnemonic.validate_mnemonic(recovery_phrase):
        raise wallet_fault("wallet_confirmation_required", authority=AUTHORITY, context={"reason": "recovery_phrase_malformed"}, source_context=source_context)
    return _create_pocket_from_mnemonic(recovery_phrase, pin=secret, network=net, label=label, approval_method=method)


def has_secret_seal(wallet_id: str) -> bool:
    """Whether a PIN/password-sealed copy of the seed exists (False for a device-approval wallet)."""
    with connection() as conn:
        row = conn.execute("SELECT sealed_blob FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    return bool(row and row[0])


def change_approval_method(wallet_id: str, *, new_method: str, new_secret: str = "", current_secret: str = "", source_context: dict[str, Any] | None = None) -> WalletProfile:
    """Switch how this wallet approves, after proving the current method; the seed is re-sealed, never re-created."""
    require_enabled(source_context=source_context)
    profile = require_wallet(wallet_id, source_context=source_context)
    if profile.mode != MODE_POCKET_SEALED:
        raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "not_a_pocket_wallet"}, source_context=source_context)
    if seal_policy(wallet_id) == PILOT_SEAL_POLICY:
        # a pilot wallet keeps its policy: its own authority re-seals the same key under the new credential after the
        # current one (or the enrolled device secret) proves control, fenced by the credential generation
        from core.wallet import pilot_custody

        pilot_custody.change_pilot_credential(wallet_id, current_credential=current_secret, method=new_method, credential=new_secret, credential_confirmation=new_secret, source_context=source_context)
        return require_wallet(wallet_id, source_context=source_context)
    method = str(new_method or "").strip().lower()
    secret = _validate_secret_for(method, pin=new_secret, password=new_secret, source_context=source_context)
    if profile.approval_method == APPROVAL_DEVICE:
        kind, material, public_key = _unseal_with_device(wallet_id, reason=f"VOOL: change how wallet {public_key_short(profile.public_key)} approves payments")
    else:
        kind, material = _unseal_material(wallet_id, current_secret)
        public_key = profile.public_key
    try:
        if method == APPROVAL_DEVICE:
            if not device_bound(wallet_id):
                device_blob = _bind_device(wallet_id, material, public_key=public_key, kind=kind)
                if not device_blob:
                    raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "device_binding_failed"}, source_context=source_context)
                with connection() as conn:
                    conn.execute("UPDATE wallet_profiles SET device_sealed_blob = ? WHERE wallet_id = ?", (device_blob, str(wallet_id)))
            with connection() as conn:
                conn.execute("UPDATE wallet_profiles SET sealed_blob = '', pin_hash = '', pin_salt = '', approval_method = ? WHERE wallet_id = ?", (method, str(wallet_id)))
        else:
            blob, pin_hash, pin_salt = _seal(material, pin=secret, public_key=public_key, kind=kind)
            with connection() as conn:
                conn.execute("UPDATE wallet_profiles SET sealed_blob = ?, pin_hash = ?, pin_salt = ?, seal_kind = ?, approval_method = ? WHERE wallet_id = ?", (blob, pin_hash, pin_salt, kind, method, str(wallet_id)))
    finally:
        del material
    from core.wallet import receipts

    receipts.journal_security_event("wallet_approval_method_changed", {"wallet_id": wallet_id, "from": profile.approval_method, "to": method}, source_context=source_context)
    return require_wallet(wallet_id, source_context=source_context)


def _require_legacy_test_row(net: str, *, source_context: dict[str, Any] | None) -> None:
    """The phrase-based pocket (create and restore) serves Test networks only. A Mainnet account is created
    by the Crypto Pilot setup lifecycle (credential first, one-time backup), never by this door."""
    from core.wallet import chains

    spec = chains.resolve_network(net)
    if spec.is_mainnet:
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "legacy_creation_is_test_networks_only"}, source_context=source_context)


def _require_pocket_family(net: str, *, source_context: dict[str, Any] | None) -> None:
    """The sealed pocket holds an Ed25519 Solana seed. An EVM pocket would be a different
    curve, a different seal and a different recovery story: typed unavailable, never
    pseudo-support."""
    from core.wallet import chains

    if chains.resolve_network(net).family != chains.FAMILY_SVM:
        raise refuse_evm_pocket_custody(source_context=source_context)


def reveal_recovery_phrase(wallet_id: str) -> None:
    """Always None: the phrase was shown at creation and is not retained. There is nothing to reveal."""
    return None


def verify_secret(wallet_id: str, secret: str) -> bool:
    """The wallet's PIN or password matches its sealed copy. Always False for a device-approval wallet (no copy)."""
    with connection() as conn:
        row = conn.execute("SELECT pin_hash, pin_salt, approval_method FROM wallet_profiles WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    if not row or not row[0]:
        return False
    method = str(row[2] or APPROVAL_PIN)
    if method == APPROVAL_PIN and not _valid_pin(secret):
        return False
    if method == APPROVAL_PASSWORD and not _valid_password(secret):
        return False
    expected = bytes.fromhex(row[0])
    return hmac.compare_digest(expected, _pin_key(str(secret or ""), base64.b64decode(row[1])))


def verify_pin(wallet_id: str, pin: str) -> bool:
    return verify_secret(wallet_id, pin)


def _unseal_material(wallet_id: str, secret: str) -> tuple[str, bytes]:
    """The PIN/password path from ciphertext to (kind, material)."""
    with connection() as conn:
        row = conn.execute("SELECT sealed_blob, public_key FROM wallet_profiles WHERE wallet_id = ? AND mode = ?", (str(wallet_id), MODE_POCKET_SEALED)).fetchone()
    if not row or not row[0]:
        raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "no_secret_sealed_key"})
    blob = json.loads(row[0])
    try:
        material = AESGCM(_seal_key(str(secret or ""), base64.b64decode(blob["salt"]))).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ciphertext"]), str(row[1]).encode("ascii"))
    except Exception:
        raise wallet_fault("wallet_pin_invalid", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "unseal_failed"}) from None
    return str(blob.get("sealkind") or SEAL_KIND_ED25519_SECRET_V1), material


def _unseal_seed(wallet_id: str, pin: str) -> bytes:
    """Package-private. The PIN/password path to the 32-byte Ed25519 SIGNING secret; consumed by the pocket signer."""
    kind, material = _unseal_material(wallet_id, pin)
    return _solana_secret_from(kind, material)


def _unseal_signing_secret_with_device(wallet_id: str, *, reason: str) -> bytes:
    """Package-private. The DEVICE path to the 32-byte Ed25519 signing secret (a Touch ID / password prompt)."""
    kind, material, _public_key = _unseal_with_device(wallet_id, reason=reason)
    return _solana_secret_from(kind, material)


def _unseal_with_device(wallet_id: str, *, reason: str) -> tuple[str, bytes, str]:
    """The DEVICE path: Touch ID or the Mac password releases the Keychain unlock secret, which opens the device
    seal. Returns (kind, material, public_key). The only door to a private-key export."""
    with connection() as conn:
        row = conn.execute("SELECT device_sealed_blob, public_key FROM wallet_profiles WHERE wallet_id = ? AND mode = ?", (str(wallet_id), MODE_POCKET_SEALED)).fetchone()
    if not row:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"wallet_id": wallet_id})
    if not row[0]:
        raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "wallet_not_device_bound",
                           "remediation": "This wallet was created on a machine without device authentication (Touch ID or password). Restore it from its recovery phrase on this Mac to enable export."})
    authority = device_auth.current_authority()
    if not authority.available():
        raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "no_device_authentication_on_this_machine"})
    try:
        unlock_secret = authority.read_unlock_secret(wallet_id, reason=reason)
    except device_auth.DeviceAuthDenied as exc:
        raise wallet_fault("wallet_device_auth_denied", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": str(exc) or "denied"}) from None
    except device_auth.DeviceAuthUnavailable as exc:
        raise wallet_fault("wallet_device_auth_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": str(exc)}) from None
    blob = json.loads(row[0])
    try:
        material = AESGCM(_device_seal_key(unlock_secret)).decrypt(base64.b64decode(blob["nonce"]), base64.b64decode(blob["ciphertext"]), str(row[1]).encode("ascii"))
    except Exception:
        raise wallet_fault("wallet_device_auth_denied", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "device_unseal_failed"}) from None
    finally:
        del unlock_secret
    return str(blob.get("sealkind") or SEAL_KIND_ED25519_SECRET_V1), material, str(row[1])


EXPORT_TARGETS = ("phantom", "metamask")


def export_private_key(wallet_id: str, *, target: str, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Export the pocket wallet's private key for one target wallet, unlocked ONLY by device authentication.

    phantom  -> the 64-byte Solana keypair as base58 (Phantom: Add / Connect wallet -> Import private key)
    metamask -> the secp256k1 key as 0x-hex derived from the same seed at m/44'/60'/0'/0/0 (MetaMask: Import account)
    Shown once, never stored, journaled as a fact without the material. A PIN is not a parameter here on purpose.
    """
    require_enabled(source_context=source_context)
    profile = require_wallet(wallet_id, source_context=source_context)
    if profile.mode != MODE_POCKET_SEALED:
        raise wallet_fault("wallet_export_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": f"{profile.mode}_has_no_key_here"}, source_context=source_context)
    if seal_policy(wallet_id) == PILOT_SEAL_POLICY:
        # a Crypto Pilot backup is released once, during setup; a later export needs an explicit product policy change
        raise wallet_fault("wallet_export_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "pilot_backup_is_setup_only"}, source_context=source_context)
    chosen = str(target or "").strip().lower()
    if chosen not in EXPORT_TARGETS:
        raise wallet_fault("wallet_export_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "unknown_target", "targets": list(EXPORT_TARGETS)}, source_context=source_context)
    kind, material, _public_key = _unseal_with_device(wallet_id, reason=f"VOOL: export the {chosen.capitalize()} private key of wallet {public_key_short(profile.public_key)}")
    try:
        if chosen == "phantom":
            keypair = mnemonic.derive_solana_keypair(material) if kind == SEAL_KIND_BIP39_SEED_V2 else _keypair_from_secret(material)
            value, address, fmt = b58encode(bytes(keypair)), str(keypair.pubkey()), "solana_keypair_base58"
            steps = ["Phantom: Settings -> Manage Accounts -> Add / Connect Wallet -> Import Private Key.", "Paste this text. It is the whole wallet: anyone who has it owns the funds.", "Delete it from your clipboard afterwards."]
        else:
            if kind != SEAL_KIND_BIP39_SEED_V2:
                raise wallet_fault("wallet_export_unavailable", authority=AUTHORITY, context={"wallet_id": wallet_id, "reason": "legacy_seal_has_no_evm_key",
                                   "remediation": "This wallet was created before seed sealing. Restore it from its recovery phrase and the MetaMask key can be exported."}, source_context=source_context)
            value, address = _evm_key_from_seed(material, source_context=source_context)
            fmt = "evm_private_key_hex"
            steps = ["MetaMask: account menu -> Add account or hardware wallet -> Import account -> Private key.", "Paste this text without spaces. It controls the address shown here on every EVM chain.", "Delete it from your clipboard afterwards."]
    finally:
        del material
    from core.wallet import receipts

    receipts.journal_security_event("wallet_private_key_exported", {"wallet_id": wallet_id, "target": chosen, "format": fmt, "address": address, "seal_kind": kind}, source_context=source_context)
    return {"wallet_id": wallet_id, "target": chosen, "format": fmt, "value": value, "address": address, "shown_once": True, "import_steps": steps,
            "warning": "Anyone who has this text owns the wallet. Never send it to anyone; no support, migration or airdrop ever needs it."}


def public_key_short(value: str) -> str:
    text = str(value or "")
    return text[:6] + "…" + text[-4:] if len(text) > 12 else text


def _keypair_from_secret(secret: bytes):
    from solders.keypair import Keypair

    return Keypair.from_seed(bytes(secret))


def _evm_key_from_seed(seed: bytes, *, source_context: dict[str, Any] | None) -> tuple[str, str]:
    try:
        from eth_account import Account
        from eth_account.hdaccount.deterministic import HDPath
    except Exception:
        raise wallet_fault("wallet_dependency_unavailable", authority=AUTHORITY, context={"reason": "eth_account_missing", "remediation": "This build cannot derive an Ethereum key: the Ethereum libraries are not installed on this machine."}, source_context=source_context) from None
    key = bytes(HDPath(EVM_DERIVATION_PATH).derive(bytes(seed)))
    return "0x" + key.hex(), str(Account.from_key(key).address)


# --- reads -------------------------------------------------------------------------------------

def get_wallet(wallet_id: str) -> WalletProfile | None:
    with connection() as conn:
        row = conn.execute(_SELECT + " WHERE wallet_id = ?", (str(wallet_id or ""),)).fetchone()
    return _row_to_profile(row) if row else None


def require_wallet(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> WalletProfile:
    profile = get_wallet(wallet_id)
    if profile is None:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"wallet_id": str(wallet_id or "")}, source_context=source_context)
    return profile


def default_wallet() -> WalletProfile | None:
    with connection() as conn:
        row = conn.execute(_SELECT + " WHERE is_default = 1 ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            row = conn.execute(_SELECT + " ORDER BY created_at DESC LIMIT 1").fetchone()
    return _row_to_profile(row) if row else None


def list_wallets() -> list[dict[str, Any]]:
    """Every registered account with its setup state and seal policy: a Crypto Pilot wallet in setup is listed, never hidden."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT wallet_id, mode, network, public_key, label, created_at, approval_method, setup_state, seal_policy FROM wallet_profiles ORDER BY created_at ASC"
        ).fetchall()
    listed = []
    for row in rows:
        entry = _row_to_profile(row[:7]).to_dict()
        entry["setup_state"] = str(row[7] or "")
        entry["seal_policy"] = str(row[8] or "")
        listed.append(entry)
    return listed


__all__ = [
    "APPROVAL_DEVICE",
    "APPROVAL_METHODS",
    "APPROVAL_PASSWORD",
    "APPROVAL_PIN",
    "EXPORT_TARGETS",
    "MODES",
    "MODE_EXTERNAL_SIGNER",
    "MODE_POCKET_SEALED",
    "MODE_WATCH_ONLY",
    "POCKET_CONFIRMATION_PHRASE",
    "POCKET_WARNING_TEXT",
    "SEAL_KIND_BIP39_SEED_V2",
    "SEAL_KIND_ED25519_SECRET_V1",
    "PocketCreation",
    "WalletFault",
    "WalletProfile",
    "change_approval_method",
    "create_pocket_wallet",
    "create_watch_only_wallet",
    "default_wallet",
    "device_bound",
    "evm_pocket_custody_available",
    "export_private_key",
    "get_wallet",
    "has_secret_seal",
    "list_wallets",
    "preferred_signing_mode",
    "refuse_evm_pocket_custody",
    "register_external_signer_wallet",
    "require_enabled",
    "require_network",
    "require_wallet",
    "restore_pocket_wallet",
    "reveal_recovery_phrase",
    "seal_kind",
    "verify_pin",
    "verify_secret",
]
