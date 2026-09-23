from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.runtime_paths import data_path

try:
    from nacl import encoding, signing  # type: ignore

    _SIGNER_BACKEND = "pynacl"
except ImportError:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

    _SIGNER_BACKEND = "cryptography"


_KEY_DIR = data_path("keys")
_LEGACY_PRIV_KEY_PATH = _KEY_DIR / "node_signing_key.b64"
_KEY_RECORD_PATH = _KEY_DIR / "node_signing_key.json"
_KEYRING_RECORD_PATH = _KEY_DIR / "node_signing_key.keyring.json"
_KEY_ARCHIVE_DIR = _KEY_DIR / "archive"
_KEY_RECORD_VERSION = 1
_KEY_PASSPHRASE_ENV = "VOOL_KEY_PASSPHRASE"
_KEY_STORAGE_MODE_ENV = "VOOL_KEY_STORAGE_MODE"
_PBKDF2_ITERATIONS = 390_000
_KEYRING_SERVICE = "vool"
_KEYRING_ACCOUNT = "node_signing_key"

_LOG = logging.getLogger("vool.signer")
_LOCAL_KEYPAIR: LocalKeypair | None = None
_KEYPAIR_LOCK = threading.RLock()


@dataclass
class LocalKeypair:
    signing_key: object
    verify_key: object

    @property
    def peer_id(self) -> str:
        if _SIGNER_BACKEND == "pynacl":
            return self.verify_key.encode(encoder=encoding.HexEncoder).decode("utf-8")
        raw = self.verify_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()


@dataclass(frozen=True)
class KeyStorageMetadata:
    format: str
    path: Path


def _ensure_dir() -> None:
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    _KEY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _chmod_safe(_KEY_DIR, 0o700)
    _chmod_safe(_KEY_ARCHIVE_DIR, 0o700)


def _chmod_safe(path: Path, mode: int) -> None:
    try:
        if os.name == "posix":
            path.chmod(mode)
    except Exception:
        return


def _enforce_private_key_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        st_mode = path.stat().st_mode & 0o777
        if st_mode != 0o600:
            path.chmod(0o600)
    except Exception:
        return


def _generate_signing_key():
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey.generate()
    return Ed25519PrivateKey.generate()


def _signing_key_from_seed(seed: bytes):
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey(seed)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _signing_key_bytes(signing_key: object) -> bytes:
    if _SIGNER_BACKEND == "pynacl":
        return bytes(signing_key)
    return signing_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _verify_key(signing_key: object):
    if _SIGNER_BACKEND == "pynacl":
        return signing_key.verify_key
    return signing_key.public_key()


def _key_passphrase() -> str | None:
    raw = str(os.environ.get(_KEY_PASSPHRASE_ENV, "") or "").strip()
    return raw or None


def _key_storage_preference() -> str:
    raw = str(os.environ.get(_KEY_STORAGE_MODE_ENV, "") or "").strip().lower()
    if raw in {"auto", "file", "keyring", "ephemeral"}:
        return raw
    return "auto"


def _keyring_backend():
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring


from core.bounded_keyring import bounded_keyring_call as _bounded_keyring_call


def _peer_id_for_seed(seed: bytes) -> str:
    signing_key = _signing_key_from_seed(seed)
    return LocalKeypair(signing_key=signing_key, verify_key=_verify_key(signing_key)).peer_id


def _keyring_record_payload(*, service: str, account: str, peer_id: str) -> dict[str, object]:
    return {
        "version": _KEY_RECORD_VERSION,
        "format": "keyring_seed",
        "service": service,
        "account": account,
        "peer_id": peer_id,
    }


def _write_keyring_record(seed: bytes, *, service: str = _KEYRING_SERVICE, account: str = _KEYRING_ACCOUNT) -> None:
    backend = _keyring_backend()
    if backend is None:
        raise RuntimeError("Keyring storage requested but no keyring backend is available.")
    encoded_seed = base64.b64encode(seed).decode("utf-8")
    _bounded_keyring_call(
        lambda: backend.set_password(service, account, encoded_seed), what="signing-seed write"
    )
    _KEYRING_RECORD_PATH.write_text(
        json.dumps(
            _keyring_record_payload(service=service, account=account, peer_id=_peer_id_for_seed(seed)),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _chmod_safe(_KEYRING_RECORD_PATH, 0o600)


def _load_keyring_seed(path: Path) -> bytes:
    backend = _keyring_backend()
    if backend is None:
        raise RuntimeError("Keyring signing key record exists but no keyring backend is available.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("format") or "") != "keyring_seed":
        raise ValueError(f"Unsupported signing key record format: {payload.get('format')!r}")
    service = str(payload.get("service") or _KEYRING_SERVICE).strip() or _KEYRING_SERVICE
    account = str(payload.get("account") or _KEYRING_ACCOUNT).strip() or _KEYRING_ACCOUNT
    encoded = _bounded_keyring_call(
        lambda: backend.get_password(service, account), what="signing-seed read"
    )
    if not encoded:
        raise RuntimeError(f"Keyring signing key payload is missing for {service}:{account}.")
    return base64.b64decode(encoded)


def _delete_keyring_record(path: Path) -> None:
    if not path.exists():
        return
    backend = _keyring_backend()
    if backend is None:
        return
    # BOUNDED, and it asks the breaker first. This ran unbounded, and its worst
    # caller is `_persist_seed`'s except branch -- which fires precisely when the
    # bounded write or readback just TIMED OUT on a pending Keychain dialog. So the
    # first dialog led straight into a second, unbounded one, at startup, while
    # `_KEYPAIR_LOCK` was held: every `load_or_create_local_keypair()` caller in the
    # process waits behind it.
    from core.bounded_keyring import bounded_keyring_call, keychain_blocked

    if keychain_blocked():
        # The breaker is armed: a prompt is already pending or a call already
        # failed. Do not open a second one.
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        service = str(payload.get("service") or _KEYRING_SERVICE).strip() or _KEYRING_SERVICE
        account = str(payload.get("account") or _KEYRING_ACCOUNT).strip() or _KEYRING_ACCOUNT
        bounded_keyring_call(
            lambda: backend.delete_password(service, account), what="delete seed record"
        )
    except Exception:
        return


def _derive_encryption_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _hkdf_sha256(ikm: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    if length < 1:
        raise ValueError("length must be positive")
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    block = b""
    output = b""
    for counter in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
    return output[:length]


def derive_local_secret(label: str | bytes, *, length: int = 32) -> bytes:
    """Derive scoped local secret material from the node signing key.

    Callers get a label-scoped secret, not the raw node signing seed. That keeps
    wallet/tool encryption coupled to VOOL identity without spreading key bytes.
    """
    normalized_label = label.encode("utf-8") if isinstance(label, str) else bytes(label)
    if not normalized_label:
        raise ValueError("label must not be empty")
    seed = _signing_key_bytes(load_or_create_local_keypair().signing_key)
    return _hkdf_sha256(
        seed,
        salt=b"vool-local-secret-v1",
        info=normalized_label,
        length=length,
    )


def _encrypted_record_payload(seed: bytes, *, passphrase: str, protection: str = "user_passphrase") -> dict[str, object]:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_encryption_key(passphrase=passphrase, salt=salt)
    ciphertext = AESGCM(key).encrypt(nonce, seed, b"vool-node-signing-key")
    return {
        "version": _KEY_RECORD_VERSION,
        "format": "encrypted_seed",
        "protection": protection,
        "kdf": "pbkdf2_sha256",
        "iterations": _PBKDF2_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode("utf-8"),
        "nonce_b64": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("utf-8"),
    }


def _write_encrypted_key_record(seed: bytes, *, passphrase: str, protection: str = "user_passphrase") -> None:
    _KEY_RECORD_PATH.write_text(
        json.dumps(
            _encrypted_record_payload(seed, passphrase=passphrase, protection=protection),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _chmod_safe(_KEY_RECORD_PATH, 0o600)


def _load_encrypted_seed(path: Path, *, passphrase: str) -> bytes:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("format") or "") != "encrypted_seed":
        raise ValueError(f"Unsupported signing key record format: {payload.get('format')!r}")
    salt = base64.b64decode(str(payload.get("salt_b64") or ""))
    nonce = base64.b64decode(str(payload.get("nonce_b64") or ""))
    ciphertext = base64.b64decode(str(payload.get("ciphertext_b64") or ""))
    key = _derive_encryption_key(passphrase=passphrase, salt=salt)
    return AESGCM(key).decrypt(nonce, ciphertext, b"vool-node-signing-key")


def _storage_metadata() -> KeyStorageMetadata:
    if _KEYRING_RECORD_PATH.exists():
        return KeyStorageMetadata(format="keyring_seed", path=_KEYRING_RECORD_PATH)
    if _KEY_RECORD_PATH.exists():
        return KeyStorageMetadata(format="encrypted_seed", path=_KEY_RECORD_PATH)
    return KeyStorageMetadata(format="legacy_plaintext_seed", path=_LEGACY_PRIV_KEY_PATH)


def _archive_keyring_seed(seed: bytes, *, current_peer_id: str) -> Path:
    _ensure_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"{timestamp}-{current_peer_id[:24]}.json"
    archive_account = f"{_KEYRING_ACCOUNT}.archive.{timestamp}.{current_peer_id[:24]}"
    archived_path = _KEY_ARCHIVE_DIR / archive_name
    _write_keyring_record(seed, service=_KEYRING_SERVICE, account=archive_account)
    archived_path.write_text(
        json.dumps(
            _keyring_record_payload(service=_KEYRING_SERVICE, account=archive_account, peer_id=current_peer_id),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _chmod_safe(archived_path, 0o600)
    if _KEYRING_RECORD_PATH.exists():
        _KEYRING_RECORD_PATH.unlink()
    return archived_path


def _archive_current_key_material() -> Path:
    _ensure_dir()
    current_peer_id = get_local_peer_id()
    metadata = _storage_metadata()
    if metadata.format == "keyring_seed":
        return _archive_keyring_seed(_load_keyring_seed(metadata.path), current_peer_id=current_peer_id)
    suffix = ".json" if metadata.format == "encrypted_seed" else ".b64"
    archive_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{current_peer_id[:24]}{suffix}"
    archived_path = _KEY_ARCHIVE_DIR / archive_name
    archived_path.write_text(metadata.path.read_text(encoding="utf-8"), encoding="utf-8")
    _chmod_safe(archived_path, 0o600)
    return archived_path


def _warn_plaintext_seed() -> None:
    _LOG.warning(
        "Node signing key is being stored UNENCRYPTED at %s — a stolen disk can read it. Set %s or "
        "install a keyring backend so the key is protected at rest.",
        _LEGACY_PRIV_KEY_PATH, _KEY_PASSPHRASE_ENV,
    )


def _keychain_write_allowed() -> bool:
    """True only when an explicit operator grant exists (Settings/env) and this process has not
    already timed out on a Keychain prompt. Boot, tests, background daemons and isolated homes
    have neither, so they never initiate an interactive Keychain operation."""
    from core.bounded_keyring import keychain_blocked
    from core.keychain_policy import keychain_write_granted

    return keychain_write_granted() and not keychain_blocked()


_ACCOUNT_FILE_PROTECTION_PATH = _KEY_DIR / "key_storage.passphrase"


def _record_protection(path: Path) -> str:
    """The seal class stamped into an encrypted-seed record ("user_passphrase" or "account_file")."""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("protection") or "")
    except Exception:
        return ""


def _account_file_passphrase() -> str:
    """The account-file fallback secret: a random value in a 0600 file next to the key records.
    Wraps the seed in AES-GCM so the fallback is never plaintext, but the protection boundary is
    OS account/file permissions ONLY — anyone who can read the profile can decrypt it. This is
    never machine-protected storage and never encryption against disk theft; say so plainly."""
    try:
        existing = _ACCOUNT_FILE_PROTECTION_PATH.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    import secrets

    value = secrets.token_urlsafe(48)
    _ACCOUNT_FILE_PROTECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ACCOUNT_FILE_PROTECTION_PATH.write_text(value, encoding="utf-8")
    _chmod_safe(_ACCOUNT_FILE_PROTECTION_PATH, 0o600)
    return value


def _secure_store_available() -> bool:
    """True if the seed can be re-stored somewhere better than plaintext right now (used to decide
    whether to migrate a legacy plaintext seed on load). The machine-passphrase encrypted file is
    always available, so plaintext is never the only option."""
    return True


def _persist_seed(seed: bytes) -> KeyStorageMetadata:
    _ensure_dir()
    preference = _key_storage_preference()
    passphrase = _key_passphrase()

    # Explicit keyring mode must succeed or raise; the "auto" default prefers the OS keyring ONLY
    # after the explicit operator grant (Settings/env) and never after a prompt timed out in this
    # process — otherwise scratch runs, tests and unattended boots would stack Keychain dialogs.
    if preference == "keyring" or (preference == "auto" and passphrase is None and _keychain_write_allowed() and _keyring_backend() is not None):
        try:
            _write_keyring_record(seed)
            # Verify the keyring readback BEFORE deleting any other copy, so a flaky backend can never
            # leave the node with no recoverable seed.
            if _load_keyring_seed(_KEYRING_RECORD_PATH) != seed:
                raise RuntimeError("keyring readback did not match the seed")
        except Exception:
            _delete_keyring_record(_KEYRING_RECORD_PATH)
            if _KEYRING_RECORD_PATH.exists():
                _KEYRING_RECORD_PATH.unlink()
            if preference == "keyring":
                raise  # explicit request: never silently downgrade
        else:
            if _LEGACY_PRIV_KEY_PATH.exists():
                _LEGACY_PRIV_KEY_PATH.unlink()
            if _KEY_RECORD_PATH.exists():
                _KEY_RECORD_PATH.unlink()
            return KeyStorageMetadata(format="keyring_seed", path=_KEYRING_RECORD_PATH)

    if not passphrase:
        # No operator grant (or breaker armed, or backend gone): the local protected fallback —
        # AES-GCM under the machine protection secret — surfaced once. Plaintext is the last
        # resort behind a failure of THIS fallback, never the default.
        from core.keychain_policy import surface_fallback_notice

        surface_fallback_notice()
        passphrase = _account_file_passphrase()

    if preference == "ephemeral":
        # Truthful ephemeral class: the key lives in process memory only, nothing on disk.
        return KeyStorageMetadata(format="ephemeral", path=_KEY_DIR / "ephemeral")

    if passphrase:
        protection = "user_passphrase" if _key_passphrase() else "account_file"
        _write_encrypted_key_record(seed, passphrase=passphrase, protection=protection)
        if _LEGACY_PRIV_KEY_PATH.exists():
            _LEGACY_PRIV_KEY_PATH.unlink()
        if _KEYRING_RECORD_PATH.exists():
            _delete_keyring_record(_KEYRING_RECORD_PATH)
            _KEYRING_RECORD_PATH.unlink()
        return KeyStorageMetadata(format="encrypted_seed", path=_KEY_RECORD_PATH)

    _warn_plaintext_seed()
    _LEGACY_PRIV_KEY_PATH.write_text(base64.b64encode(seed).decode("utf-8"), encoding="utf-8")
    _chmod_safe(_LEGACY_PRIV_KEY_PATH, 0o600)
    if _KEY_RECORD_PATH.exists():
        _KEY_RECORD_PATH.unlink()
    if _KEYRING_RECORD_PATH.exists():
        _delete_keyring_record(_KEYRING_RECORD_PATH)
        _KEYRING_RECORD_PATH.unlink()
    return KeyStorageMetadata(format="legacy_plaintext_seed", path=_LEGACY_PRIV_KEY_PATH)


def key_storage_mode() -> str:
    if _key_storage_preference() == "ephemeral":
        return "ephemeral"
    if _KEYRING_RECORD_PATH.exists():
        return "keyring"
    if _KEY_RECORD_PATH.exists():
        return "encrypted_file"
    return "legacy_plaintext_file"


def key_storage_class() -> str:
    """The TRUTHFUL storage class (core.secret_storage vocabulary) of the live signing key —
    what surfaces and docs must claim. Never a marketing upgrade of the actual mechanism."""
    from core.secret_storage import (
        STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS,
        STORAGE_CLASS_EPHEMERAL,
        STORAGE_CLASS_KEYCHAIN,
        STORAGE_CLASS_USER_PASSPHRASE,
        assert_known_storage_class,
    )

    if _key_storage_preference() == "ephemeral" and not (
        _KEYRING_RECORD_PATH.exists() or _KEY_RECORD_PATH.exists() or _LEGACY_PRIV_KEY_PATH.exists()
    ):
        return assert_known_storage_class(STORAGE_CLASS_EPHEMERAL)
    if _KEYRING_RECORD_PATH.exists():
        return assert_known_storage_class(STORAGE_CLASS_KEYCHAIN)
    if _KEY_RECORD_PATH.exists():
        if _record_protection(_KEY_RECORD_PATH) == "account_file":
            return assert_known_storage_class(STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS)
        return assert_known_storage_class(STORAGE_CLASS_USER_PASSPHRASE)
    if _LEGACY_PRIV_KEY_PATH.exists():
        # Legacy plaintext seed: file permissions are literally the only protection. Upgraded to
        # the sealed account-file record on load; never described as more than it is.
        return assert_known_storage_class(STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS)
    return assert_known_storage_class(STORAGE_CLASS_EPHEMERAL)


def load_or_create_local_keypair() -> LocalKeypair:
    global _LOCAL_KEYPAIR
    if _LOCAL_KEYPAIR is not None:
        return _LOCAL_KEYPAIR
    with _KEYPAIR_LOCK:
        if _LOCAL_KEYPAIR is not None:
            return _LOCAL_KEYPAIR
        return _load_or_create_local_keypair_unlocked()


def _load_or_create_local_keypair_unlocked() -> LocalKeypair:
    global _LOCAL_KEYPAIR
    _ensure_dir()

    if _KEYRING_RECORD_PATH.exists():
        _enforce_private_key_permissions(_KEYRING_RECORD_PATH)
        seed = _load_keyring_seed(_KEYRING_RECORD_PATH)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    if _KEY_RECORD_PATH.exists():
        _enforce_private_key_permissions(_KEY_RECORD_PATH)
        # The seal class is stamped into the record itself. A record sealed by OUR unattended
        # fallback (account-file secret) reopens with that secret even when an operator
        # passphrase is ambient — the two secrets wrap different records, and trying the
        # operator's key against the fallback's ciphertext only raises InvalidTag, bricking
        # the node identity on the next attended boot. Symmetrically, a user-passphrase
        # record never silently falls back to the account-file key: it demands its own seal.
        if _record_protection(_KEY_RECORD_PATH) == "account_file":
            if not _ACCOUNT_FILE_PROTECTION_PATH.exists():
                raise RuntimeError(
                    f"Encrypted signing key record at {_KEY_RECORD_PATH} is sealed by the "
                    f"account-file fallback, but {_ACCOUNT_FILE_PROTECTION_PATH} is missing; "
                    f"the seed cannot be reopened on this profile."
                )
            passphrase = _account_file_passphrase()
        else:
            passphrase = _key_passphrase()
            if not passphrase:
                raise RuntimeError(
                    f"Encrypted signing key record exists at {_KEY_RECORD_PATH} but {_KEY_PASSPHRASE_ENV} is not set."
                )
        seed = _load_encrypted_seed(_KEY_RECORD_PATH, passphrase=passphrase)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    if _LEGACY_PRIV_KEY_PATH.exists():
        _enforce_private_key_permissions(_LEGACY_PRIV_KEY_PATH)
        raw = _LEGACY_PRIV_KEY_PATH.read_text(encoding="utf-8").strip()
        seed = base64.b64decode(raw)
        # Migrate an existing plaintext seed into the keyring / encrypted store when one is available.
        # The seed VALUE is unchanged, so the wallet, credential vault, and receipts are unaffected —
        # only where the seed lives changes. _persist_seed verifies before deleting the plaintext.
        if _secure_store_available():
            _persist_seed(seed)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    sk = _generate_signing_key()
    _persist_seed(_signing_key_bytes(sk))
    _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
    return _LOCAL_KEYPAIR


def sign(payload_bytes: bytes) -> str:
    kp = load_or_create_local_keypair()
    if _SIGNER_BACKEND == "pynacl":
        signed = kp.signing_key.sign(payload_bytes)
        return base64.b64encode(signed.signature).decode("utf-8")
    signature = kp.signing_key.sign(payload_bytes)
    return base64.b64encode(signature).decode("utf-8")


def verify(payload_bytes: bytes, signature: str, peer_id: str) -> bool:
    try:
        sig = base64.b64decode(signature)
        if _SIGNER_BACKEND == "pynacl":
            verify_key = signing.VerifyKey(peer_id, encoder=encoding.HexEncoder)
            verify_key.verify(payload_bytes, sig)
            return True
        verify_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(peer_id))
        verify_key.verify(sig, payload_bytes)
        return True
    except Exception:
        if _SIGNER_BACKEND == "pynacl":
            return False
        return False


def get_local_peer_id() -> str:
    return load_or_create_local_keypair().peer_id


def local_key_path() -> Path:
    _ensure_dir()
    return _storage_metadata().path


def rotate_local_keypair() -> dict[str, object]:
    global _LOCAL_KEYPAIR
    with _KEYPAIR_LOCK:
        existing_peer_id = get_local_peer_id()
        archived_path = _archive_current_key_material()
        sk = _generate_signing_key()
        _persist_seed(_signing_key_bytes(sk))
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return {
            "old_peer_id": existing_peer_id,
            "new_peer_id": _LOCAL_KEYPAIR.peer_id,
            "archived_key_path": archived_path,
        }
