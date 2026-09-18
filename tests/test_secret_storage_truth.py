"""M1 storage truth (2026-09-02): every surfaced claim about secret storage is pinned to the
ACTUAL mechanism. The AES fallback's sealing key sits in a 0600 file beside the ciphertext, so
its truthful class is `account-file-permissions` — protected by OS account/file permissions
ONLY, never described as machine-protected, hardware-bound, or encrypted against disk theft.

The vocabulary is `core/secret_storage.py`; the surfaces under test are the signer
(`key_storage_class`), the credential store (`active_storage_class`, `export_diagnostics`),
and the fallback notice.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]

_FORBIDDEN = ("machine-protected", "machine protected", "hardware-bound", "hardware protected")


# ------------------------------------------------------------------------------------------
# The vocabulary itself: four classes, honest descriptions, forbidden claims banned
# ------------------------------------------------------------------------------------------


def test_storage_class_vocabulary_is_the_four_truthful_classes() -> None:
    from core.secret_storage import STORAGE_CLASSES

    assert STORAGE_CLASSES == ("keychain", "user-passphrase", "account-file-permissions", "ephemeral")


def test_account_file_description_denies_what_it_does_not_protect() -> None:
    from core.secret_storage import (
        STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS,
        STORAGE_CLASS_DESCRIPTIONS,
    )

    text = STORAGE_CLASS_DESCRIPTIONS[STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS]
    lowered = text.lower()
    assert "account and file permissions" in lowered or "file permissions" in lowered
    assert "not encrypted at rest" in lowered
    assert "profile or disk theft" in lowered
    for phrase in _FORBIDDEN:
        assert phrase not in lowered


def test_no_storage_surface_claims_machine_protection_or_hardware_binding() -> None:
    """Sabotage pin: if any runtime surface CLAIMS machine-protected or hardware-bound storage
    (outside the vocabulary's own definition and explicit negations), this fails."""
    offenders: list[str] = []
    _NEGATIONS = ("never", "not ", "forbidden")
    for rel in (
        "core/secret_storage.py",
        "core/keychain_policy.py",
        "core/credential_store.py",
        "network/signer.py",
        "installer/bundle/build_macos_app.sh",
        "installer/bundle/README.md",
    ):
        for lineno, line in enumerate(
            (REPO / rel).read_text(encoding="utf-8").lower().splitlines(), start=1
        ):
            if rel == "core/secret_storage.py" and "def " not in line and lineno > 40:
                pass  # the FORBIDDEN list itself lives near the bottom; still negation-checked below
            for phrase in _FORBIDDEN:
                if phrase not in line:
                    continue
                if not any(neg in line for neg in _NEGATIONS) and rel != "core/secret_storage.py":
                    offenders.append(f"{rel}:{lineno}: {phrase!r}")
                elif rel == "core/secret_storage.py" and "def " in line:
                    offenders.append(f"{rel}:{lineno}: {phrase!r}")
    assert offenders == [], f"forbidden security claims: {offenders}"


def test_fallback_notice_is_truthful_about_the_mechanism() -> None:
    from core.keychain_policy import FALLBACK_NOTICE

    lowered = FALLBACK_NOTICE.lower()
    assert "file permissions" in lowered
    assert "not against profile or disk theft" in lowered
    for phrase in _FORBIDDEN:
        assert phrase not in lowered


# ------------------------------------------------------------------------------------------
# The signer reports the class of the ACTUAL record on disk
# ------------------------------------------------------------------------------------------


def _reload_signer(monkeypatch, tmp_path: Path):
    import importlib

    import tests.test_signer_key_storage as existing
    from network import signer as signer_mod

    importlib.reload(signer_mod)
    existing._configure_signer_paths(tmp_path)
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    return signer_mod


def test_signer_machine_fallback_reports_account_file_permissions(monkeypatch, tmp_path) -> None:
    signer = _reload_signer(monkeypatch, tmp_path)
    monkeypatch.setattr(signer, "_keyring_backend", lambda: None)

    signer.load_or_create_local_keypair()

    assert signer.key_storage_class() == "account-file-permissions"
    payload = json.loads(signer._KEY_RECORD_PATH.read_text(encoding="utf-8"))
    assert payload["protection"] == "account_file", "the record must name its real protection"


def test_signer_user_passphrase_record_reports_user_passphrase(monkeypatch, tmp_path) -> None:
    signer = _reload_signer(monkeypatch, tmp_path)
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "closed-test-secret")
    monkeypatch.setattr(signer, "_keyring_backend", lambda: None)

    signer.load_or_create_local_keypair()

    assert signer.key_storage_class() == "user-passphrase"
    payload = json.loads(signer._KEY_RECORD_PATH.read_text(encoding="utf-8"))
    assert payload["protection"] == "user_passphrase"


def test_signer_keyring_record_reports_keychain_only_with_grant(monkeypatch, tmp_path) -> None:
    signer = _reload_signer(monkeypatch, tmp_path)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")

    class FakeKeyringBackend:
        def __init__(self):
            self.store = {}

        def set_password(self, service, account, password):
            self.store[(service, account)] = password

        def get_password(self, service, account):
            return self.store.get((service, account))

        def delete_password(self, service, account):
            self.store.pop((service, account), None)

    fake = FakeKeyringBackend()
    monkeypatch.setattr(signer, "_keyring_backend", lambda: fake)

    signer.load_or_create_local_keypair()
    assert signer.key_storage_class() == "keychain"


def test_signer_ephemeral_mode_writes_nothing(monkeypatch, tmp_path) -> None:
    signer = _reload_signer(monkeypatch, tmp_path)
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "ephemeral")
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setattr(signer, "_keyring_backend", lambda: None)

    keypair = signer.load_or_create_local_keypair()
    assert keypair.peer_id
    assert signer.key_storage_class() == "ephemeral"
    assert not signer._KEY_RECORD_PATH.exists()
    assert not signer._LEGACY_PRIV_KEY_PATH.exists()
    assert not signer._KEYRING_RECORD_PATH.exists()


# ------------------------------------------------------------------------------------------
# The credential store reports the class of the ACTUAL backend
# ------------------------------------------------------------------------------------------


def test_credential_store_vault_reports_account_file_permissions(tmp_path, monkeypatch) -> None:
    from core import credential_store

    monkeypatch.setattr(credential_store, "_load_keyring", lambda: None)
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    assert credential_store.active_storage_class() == "account-file-permissions"

    credential_store.store_credential("probe.truth", "v", label="t")
    diag = credential_store.export_diagnostics()
    assert diag["storage_class"] == "account-file-permissions"
    assert diag["backend"] == "vault"


def test_credential_store_granted_keychain_reports_keychain(tmp_path, monkeypatch) -> None:
    from core import credential_store

    class Backend:
        def __init__(self):
            self.store = {}

        def set_password(self, s, a, p):
            self.store[(s, a)] = p

        def get_password(self, s, a):
            return self.store.get((s, a))

    backend = Backend()
    fake = mock.Mock()
    fake.get_keyring.return_value = backend
    fake.set_password = backend.set_password
    fake.get_password = backend.get_password
    monkeypatch.setattr(credential_store, "_load_keyring", lambda: fake)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")  # conftest defaults the session to vault

    credential_store.store_credential("probe.truth", "v", label="t")
    assert credential_store.active_storage_class() == "keychain"
    diag = credential_store.export_diagnostics()
    assert diag["storage_class"] == "keychain"
