"""The ONE truthful vocabulary for where secrets live (2026-09-02 M1 storage truth).

Every surface that describes secret storage (diagnostics, API payloads, notices, docs) must
use these classes and only these descriptions. The AES fallback seals ciphertext with a key
that itself lives in a 0600 file in the same user profile — that is protected by OS
account/file permissions ONLY. It is never to be described as machine-protected,
hardware-bound, or encrypted-at-rest against profile or disk theft.
"""
from __future__ import annotations

STORAGE_CLASS_KEYCHAIN = "keychain"
STORAGE_CLASS_USER_PASSPHRASE = "user-passphrase"
STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS = "account-file-permissions"
STORAGE_CLASS_EPHEMERAL = "ephemeral"

STORAGE_CLASSES = (
    STORAGE_CLASS_KEYCHAIN,
    STORAGE_CLASS_USER_PASSPHRASE,
    STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS,
    STORAGE_CLASS_EPHEMERAL,
)

STORAGE_CLASS_DESCRIPTIONS = {
    STORAGE_CLASS_KEYCHAIN: (
        "Secret stored in the OS Keychain. Only available after the explicit operator "
        "grant (Settings); the Keychain protects the item under its own ACL."
    ),
    STORAGE_CLASS_USER_PASSPHRASE: (
        "Secret sealed with AES-GCM under a key derived from a passphrase only the user "
        "enters (VOOL_KEY_PASSPHRASE). Security is exactly the strength and secrecy of "
        "that passphrase; the ciphertext file alone is not the secret."
    ),
    STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS: (
        "Secret sealed with AES-GCM whose key is itself stored in a 0600 file in the same "
        "user profile. Protected ONLY by OS account and file permissions: any process or "
        "person that can read the profile can decrypt it. NOT encrypted at rest against "
        "profile or disk theft."
    ),
    STORAGE_CLASS_EPHEMERAL: (
        "Secret exists in process memory only. Nothing is written to disk; it is lost when "
        "the process exits."
    ),
}

#: Forbidden phrases: any surface describing our storage with these words is lying.
FORBIDDEN_CLAIM_PHRASES = (
    "machine-protected",
    "hardware-bound",
    "hardware protected",
    "encrypted against disk theft",
)


def assert_known_storage_class(storage_class: str) -> str:
    if storage_class not in STORAGE_CLASSES:
        raise ValueError(f"unknown storage class: {storage_class!r}")
    return storage_class
