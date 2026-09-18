"""§9 security-acceptance tests for the macOS-Keychain credential backend.

`keyring` is replaced with an in-memory fake (via monkeypatching `credential_store._active_keyring`),
so these run cross-platform and NEVER touch the real login Keychain. Covers: roundtrip on both
backends, backend selection, isolation, disconnect-deletes-the-key, no-value-leaks, and the
safe/idempotent/loss-safe vault→keychain migration.
"""
from __future__ import annotations

import json

import pytest

import core.credential_store as cs
from core import runtime_paths


class FakeKeyring:
    """Minimal in-memory stand-in for the keyring module (service, account -> password)."""

    class errors:  # noqa: N801  (mirrors keyring.errors namespace)
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.set_calls = 0

    def set_password(self, service, account, password):
        self._store[(service, account)] = password
        self.set_calls += 1

    def get_password(self, service, account):
        return self._store.get((service, account))

    def delete_password(self, service, account):
        if (service, account) not in self._store:
            raise self.errors.PasswordDeleteError("not found")
        del self._store[(service, account)]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    # The conftest pins VOOL_CREDENTIAL_STORE=vault for safety; these tests choose per-test.
    monkeypatch.delenv("VOOL_CREDENTIAL_STORE", raising=False)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def keychain(monkeypatch):
    """Route the store through an in-memory fake Keychain (never the real one)."""
    fake = FakeKeyring()
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: fake)
    return fake


# ------------------------------------------------------------------ backend selection

def test_active_backend_reflects_selection(monkeypatch, keychain):
    assert cs.active_backend() == "keychain"
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: None)
    assert cs.active_backend() == "vault"


def test_vault_mode_never_touches_keyring(monkeypatch):
    # Even when a real keyring would be present, VOOL_CREDENTIAL_STORE=vault forces the file path.
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    sentinel = FakeKeyring()
    monkeypatch.setattr(cs, "_load_keyring", lambda: sentinel)
    assert cs._active_keyring() is None
    cs.store_credential("llm.cloud.openrouter", "sk-or-v1-secret", label="OpenRouter")
    assert sentinel.set_calls == 0                     # never written to keyring
    assert cs.get_credential("llm.cloud.openrouter") == "sk-or-v1-secret"


def test_fail_backend_degrades_to_vault(monkeypatch):
    class _FailKeyring:
        class Backend:
            __module__ = "keyring.backends.fail"
            __name__ = "Keyring"

        def get_keyring(self):
            return self.Backend()
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setitem(__import__("sys").modules, "keyring", _FailKeyring())
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    assert cs._load_keyring() is None                  # a fail/null backend is never trusted


# ------------------------------------------------------------------ roundtrip (both backends)

def test_keychain_roundtrip(keychain):
    cs.store_credential("llm.cloud.openrouter", "sk-or-v1-abc", label="OpenRouter")
    assert cs.has_credential("llm.cloud.openrouter") is True
    assert cs.get_credential("llm.cloud.openrouter") == "sk-or-v1-abc"
    assert keychain.get_password(cs._KC_SERVICE, "llm.cloud.openrouter") == "sk-or-v1-abc"
    assert cs.delete_credential("llm.cloud.openrouter") is True
    assert cs.get_credential("llm.cloud.openrouter") is None
    assert cs.has_credential("llm.cloud.openrouter") is False


def test_vault_roundtrip(monkeypatch):
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: None)
    cs.store_credential("email.smtp.gmail", "app-pw-1234", label="Gmail")
    assert cs.get_credential("email.smtp.gmail") == "app-pw-1234"
    assert cs.delete_credential("email.smtp.gmail") is True
    assert cs.get_credential("email.smtp.gmail") is None


def test_two_names_are_isolated(keychain):
    cs.store_credential("a", "secret-A", label="A")
    cs.store_credential("b", "secret-B", label="B")
    assert cs.get_credential("a") == "secret-A"
    assert cs.get_credential("b") == "secret-B"
    cs.delete_credential("a")
    assert cs.get_credential("a") is None
    assert cs.get_credential("b") == "secret-B"       # deleting one never affects the other


# ------------------------------------------------------------------ no value leaks

def test_list_and_diagnostics_never_expose_a_value(keychain, tmp_path):
    secret = "sk-or-v1-topsecretvalue0000"
    cs.store_credential("llm.cloud.openrouter", secret, label="OpenRouter")
    listing = cs.list_credentials()
    assert listing == [{"name": "llm.cloud.openrouter", "label": "OpenRouter"}]
    assert secret not in json.dumps(listing)
    diag = cs.export_diagnostics()
    assert secret not in json.dumps(diag, ensure_ascii=False)   # full value never present
    assert diag["credentials"][0]["masked_suffix"] == "…0000"   # only the masked last-4
    assert diag["backend"] == "keychain"
    # the sidecar file on disk must carry names/labels only, never the secret
    sidecar = cs._meta_path()
    assert sidecar.exists()
    assert secret not in sidecar.read_text(encoding="utf-8")
    assert "llm.cloud.openrouter" in sidecar.read_text(encoding="utf-8")


def test_disconnect_erases_from_keyring_and_sidecar(keychain):
    cs.store_credential("llm.cloud.openrouter", "sk-or-v1-erase-me", label="OpenRouter")
    assert cs._meta_path().exists() and "llm.cloud.openrouter" in cs._meta_load()
    cs.delete_credential("llm.cloud.openrouter")
    assert keychain.get_password(cs._KC_SERVICE, "llm.cloud.openrouter") is None
    assert "llm.cloud.openrouter" not in cs._meta_load()
    assert cs.get_credential("llm.cloud.openrouter") is None


# ------------------------------------------------------------------ migration (vault -> keychain)

def test_migration_moves_vault_to_keychain_and_scrubs(monkeypatch):
    # 1) seed the vault while keyring is inactive
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: None)
    cs.store_credential("llm.cloud.openrouter", "sk-or-v1-legacy", label="OpenRouter")
    cs.store_credential("email.smtp.gmail", "app-pw", label="Gmail")
    assert cs._store_path().exists()
    ciphertext_before = cs._store_path().read_text(encoding="utf-8")
    assert "nonce_b64" in ciphertext_before

    # 2) keyring becomes available -> a read triggers migration
    fake = FakeKeyring()
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: fake)
    assert cs.get_credential("llm.cloud.openrouter") == "sk-or-v1-legacy"

    # both values now live in the Keychain
    assert fake.get_password(cs._KC_SERVICE, "llm.cloud.openrouter") == "sk-or-v1-legacy"
    assert fake.get_password(cs._KC_SERVICE, "email.smtp.gmail") == "app-pw"
    # vault ciphertext scrubbed; metadata + migration marker written
    assert cs._load_raw() == {}
    assert {"llm.cloud.openrouter", "email.smtp.gmail"} <= set(cs._meta_load())
    from core.runtime_paths import data_path
    assert data_path(cs._MIGRATION_MARKER).exists()
    # labels preserved through migration
    assert {c["name"]: c["label"] for c in cs.list_credentials()}["email.smtp.gmail"] == "Gmail"


def test_migration_is_idempotent(monkeypatch):
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: None)
    cs.store_credential("k", "v", label="L")
    fake = FakeKeyring()
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: fake)
    cs.get_credential("k")                              # first migration
    first_calls = fake.set_calls
    cs.list_credentials()                               # more reads must not re-migrate
    cs.has_credential("k")
    cs.get_credential("k")
    assert fake.set_calls == first_calls               # no re-migration (marker short-circuits)


def test_migration_keeps_vault_entry_when_keychain_write_fails(monkeypatch):
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: None)
    cs.store_credential("k", "v", label="L")

    class _BrokenKeyring(FakeKeyring):
        def set_password(self, service, account, password):
            raise RuntimeError("keychain locked")

    broken = _BrokenKeyring()
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: broken)
    # migration attempt fails the read-back -> the vault entry must NOT be dropped (no data loss)
    cs.get_credential("k")
    assert "k" in cs._load_raw()                        # still in the vault, recoverable
    from core.runtime_paths import data_path
    assert not data_path(cs._MIGRATION_MARKER).exists()  # not marked done -> retried next run


# ------------------------------------------------------------------ diagnostics chat surface + redaction lock

def test_all_provider_key_prefixes_are_redacted():
    # Locks §9 "logs never contain a full key" across the multi-provider BYOK prefixes -- the bare
    # sk- branch already covers sk-proj- (OpenAI), sk-or- (OpenRouter), and DeepSeek/Kimi sk- keys.
    from core.secret_redaction import contains_secret, redact_secrets

    for secret in ("sk-proj-abcdefghijklmnop1234567890", "sk-or-v1-abcdefghijklmnop1234",
                   "sk-deadbeefdeadbeefdeadbeef00", "gsk_abcdefghijklmnopqrstuvwx", "AIzaSyAbcdefghijklmnop_qrstuvwxy"):
        assert contains_secret(secret), secret
        assert secret not in redact_secrets(f"key {secret} end"), secret


def test_cloud_diagnostics_command_is_owner_gated_and_redacted(monkeypatch, keychain):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_diagnostics_intent

    cs.store_credential("llm.cloud.openrouter", "sk-or-v1-diagnose-me-9999", label="OpenRouter")
    # non-owner: refusal, no store read
    assert "owner-only" in maybe_handle_cloud_diagnostics_intent("cloud diagnostics", owner_local=False)
    # owner: masked last-4 only, backend named, full value absent
    reply = maybe_handle_cloud_diagnostics_intent("cloud diagnostics", owner_local=True)
    assert reply is not None
    assert "sk-or-v1-diagnose-me-9999" not in reply
    assert "…9999" in reply and "llm.cloud.openrouter" in reply and "Keychain" in reply
    # unrelated text does not trigger it
    assert maybe_handle_cloud_diagnostics_intent("what's the weather", owner_local=True) is None


# ------------------------------------------------------------------ reinstall / wiped-index recovery

def test_wiped_index_does_not_hide_a_live_keychain_credential(keychain, tmp_path) -> None:
    """Reproduces the reinstall case: runtime dir wiped, keychain untouched.

    Found by actually wiping ~/.vool_runtime: get_credential("llm.cloud.openrouter") returned a
    73-character value while list_credentials() returned [] and has_credential() said False -- so the
    product reports "no cloud key configured" with a working key sitting in the keychain.
    """
    cs.store_credential("llm.cloud.openrouter", "or-live-key-value", label="OpenRouter")
    assert cs.has_credential("llm.cloud.openrouter") is True

    # Wipe only the runtime-dir side, exactly like deleting the data directory on reinstall.
    cs._meta_path().unlink()
    assert cs._meta_load() == {}

    assert cs.get_credential("llm.cloud.openrouter") == "or-live-key-value", "the secret survives"
    assert cs.has_credential("llm.cloud.openrouter") is True, "and must not be reported as missing"
    assert [item["name"] for item in cs.list_credentials()] == ["llm.cloud.openrouter"]
    assert cs._meta_load()["llm.cloud.openrouter"]["label"] == "OpenRouter", "index is rebuilt"


def test_reconcile_never_invents_a_credential(keychain) -> None:
    # It may only re-index names that really resolve in the keychain. Nothing stored -> nothing listed.
    assert cs.list_credentials() == []
    assert cs.has_credential("llm.cloud.openrouter") is False


def test_a_deleted_credential_stays_deleted(keychain) -> None:
    # delete_credential removes the keychain entry too, so the reconcile has nothing to find -- a
    # disconnect must not be undone by the next list call.
    cs.store_credential("llm.cloud.openrouter", "or-key", label="OpenRouter")
    assert cs.delete_credential("llm.cloud.openrouter") is True

    assert cs.list_credentials() == []
    assert cs.has_credential("llm.cloud.openrouter") is False


def test_reconcile_survives_an_unreadable_keychain(keychain, monkeypatch) -> None:
    # A locked keychain raises on read; startup must not break because of a diagnostic re-index.
    cs.store_credential("llm.cloud.openrouter", "or-key", label="OpenRouter")
    cs._meta_path().unlink()

    def boom(service, account):
        raise RuntimeError("keychain is locked")

    monkeypatch.setattr(keychain, "get_password", boom)
    assert cs.list_credentials() == []  # degrades to "not visible", does not raise
