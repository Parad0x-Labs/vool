from __future__ import annotations

import base64
import importlib
import json
import threading
import time

import network.signer as signer_mod


def test_concurrent_first_use_creates_one_process_keypair(monkeypatch, tmp_path) -> None:
    """Concurrent provider manifests must not race while creating the signer identity."""
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.setattr(signer_mod, "_persist_seed", lambda seed: None)
    original_generate = signer_mod._generate_signing_key

    def slow_generate():
        time.sleep(0.02)
        return original_generate()

    monkeypatch.setattr(signer_mod, "_generate_signing_key", slow_generate)
    start = threading.Event()
    peer_ids: list[str] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def load() -> None:
        start.wait()
        try:
            peer_id = signer_mod.get_local_peer_id()
            with result_lock:
                peer_ids.append(peer_id)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_lock:
                errors.append(exc)

    threads = [threading.Thread(target=load) for _ in range(16)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(peer_ids) == len(threads)
    assert len(set(peer_ids)) == 1


def _configure_signer_paths(tmp_path) -> None:
    signer_mod._KEY_DIR = tmp_path / "keys"
    signer_mod._LEGACY_PRIV_KEY_PATH = signer_mod._KEY_DIR / "node_signing_key.b64"
    signer_mod._KEY_RECORD_PATH = signer_mod._KEY_DIR / "node_signing_key.json"
    signer_mod._KEYRING_RECORD_PATH = signer_mod._KEY_DIR / "node_signing_key.keyring.json"
    signer_mod._KEY_ARCHIVE_DIR = signer_mod._KEY_DIR / "archive"
    signer_mod._ACCOUNT_FILE_PROTECTION_PATH = signer_mod._KEY_DIR / "key_storage.passphrase"
    signer_mod._LOCAL_KEYPAIR = None


def _peer_id_for_seed(seed: bytes) -> str:
    signing_key = signer_mod._signing_key_from_seed(seed)
    verify_key = signer_mod._verify_key(signing_key)
    if signer_mod._SIGNER_BACKEND == "pynacl":
        return verify_key.encode(encoder=signer_mod.encoding.HexEncoder).decode("utf-8")
    raw = verify_key.public_bytes(
        encoding=signer_mod.serialization.Encoding.Raw,
        format=signer_mod.serialization.PublicFormat.Raw,
    )
    return raw.hex()


class _FakeKeyring:
    """Dict-backed keyring stand-in. The real keyring module is a process singleton, so tests return
    ONE instance from _keyring_backend (write and readback must hit the same store)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


def test_signer_uses_encrypted_record_when_passphrase_is_set(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "closed-test-secret")

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id
    assert signer_mod.key_storage_mode() == "encrypted_file"
    assert signer_mod.local_key_path() == signer_mod._KEY_RECORD_PATH
    assert signer_mod._KEY_RECORD_PATH.exists()
    assert not signer_mod._LEGACY_PRIV_KEY_PATH.exists()

    payload = json.loads(signer_mod._KEY_RECORD_PATH.read_text(encoding="utf-8"))
    assert payload["format"] == "encrypted_seed"
    assert "ciphertext_b64" in payload
    assert payload["ciphertext_b64"] != ""

    signer_mod._LOCAL_KEYPAIR = None
    assert signer_mod.get_local_peer_id() == peer_id


def test_signer_migrates_legacy_seed_to_encrypted_record_when_passphrase_appears(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    seed = bytes(range(32))
    signer_mod._KEY_DIR.mkdir(parents=True, exist_ok=True)
    signer_mod._LEGACY_PRIV_KEY_PATH.write_text(base64.b64encode(seed).decode("utf-8"), encoding="utf-8")
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "closed-test-secret")

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id == _peer_id_for_seed(seed)
    assert signer_mod._KEY_RECORD_PATH.exists()
    assert not signer_mod._LEGACY_PRIV_KEY_PATH.exists()
    assert signer_mod.key_storage_mode() == "encrypted_file"


def test_signer_rejects_encrypted_record_without_passphrase(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "closed-test-secret")
    signer_mod.get_local_peer_id()
    signer_mod._LOCAL_KEYPAIR = None
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)

    try:
        signer_mod.load_or_create_local_keypair()
    except RuntimeError as exc:
        assert "VOOL_KEY_PASSPHRASE" in str(exc)
    else:
        raise AssertionError("expected encrypted signer record to require VOOL_KEY_PASSPHRASE")


def test_rotate_local_keypair_preserves_encrypted_storage_and_archives_old_record(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "closed-test-secret")
    old_peer = signer_mod.get_local_peer_id()

    result = signer_mod.rotate_local_keypair()

    assert result["old_peer_id"] == old_peer
    assert result["new_peer_id"] != old_peer
    archived_path = result["archived_key_path"]
    assert archived_path.exists()
    assert archived_path.suffix == ".json"
    assert signer_mod._KEY_RECORD_PATH.exists()
    assert signer_mod.key_storage_mode() == "encrypted_file"


def test_signer_uses_keyring_record_when_requested(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)

    class FakeKeyring:
        def __init__(self) -> None:
            self.store: dict[tuple[str, str], str] = {}

        def set_password(self, service: str, account: str, value: str) -> None:
            self.store[(service, account)] = value

        def get_password(self, service: str, account: str) -> str | None:
            return self.store.get((service, account))

        def delete_password(self, service: str, account: str) -> None:
            self.store.pop((service, account), None)

    fake_keyring = FakeKeyring()
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "keyring")
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: fake_keyring)

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id
    assert signer_mod.key_storage_mode() == "keyring"
    assert signer_mod.local_key_path() == signer_mod._KEYRING_RECORD_PATH
    assert signer_mod._KEYRING_RECORD_PATH.exists()
    assert not signer_mod._KEY_RECORD_PATH.exists()
    assert not signer_mod._LEGACY_PRIV_KEY_PATH.exists()
    payload = json.loads(signer_mod._KEYRING_RECORD_PATH.read_text(encoding="utf-8"))
    assert payload["format"] == "keyring_seed"
    assert fake_keyring.get_password(payload["service"], payload["account"])

    signer_mod._LOCAL_KEYPAIR = None
    assert signer_mod.get_local_peer_id() == peer_id


def test_signer_rejects_keyring_mode_without_backend(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "keyring")
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: None)

    try:
        signer_mod.load_or_create_local_keypair()
    except RuntimeError as exc:
        assert "keyring backend" in str(exc).lower()
    else:
        raise AssertionError("expected explicit keyring mode to require a keyring backend")


def test_rotate_local_keypair_preserves_keyring_storage_and_archives_old_record(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)

    class FakeKeyring:
        def __init__(self) -> None:
            self.store: dict[tuple[str, str], str] = {}

        def set_password(self, service: str, account: str, value: str) -> None:
            self.store[(service, account)] = value

        def get_password(self, service: str, account: str) -> str | None:
            return self.store.get((service, account))

        def delete_password(self, service: str, account: str) -> None:
            self.store.pop((service, account), None)

    fake_keyring = FakeKeyring()
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "keyring")
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: fake_keyring)
    old_peer = signer_mod.get_local_peer_id()
    old_payload = json.loads(signer_mod._KEYRING_RECORD_PATH.read_text(encoding="utf-8"))
    old_secret = fake_keyring.get_password(old_payload["service"], old_payload["account"])

    result = signer_mod.rotate_local_keypair()

    assert result["old_peer_id"] == old_peer
    assert result["new_peer_id"] != old_peer
    archived_path = result["archived_key_path"]
    assert archived_path.exists()
    assert archived_path.suffix == ".json"
    archived_payload = json.loads(archived_path.read_text(encoding="utf-8"))
    assert archived_payload["format"] == "keyring_seed"
    assert fake_keyring.get_password(archived_payload["service"], archived_payload["account"]) == old_secret
    assert signer_mod._KEYRING_RECORD_PATH.exists()
    assert signer_mod.key_storage_mode() == "keyring"


def test_auto_default_uses_keyring_when_backend_available(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)  # auto
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: fake)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")  # the explicit operator grant

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id
    assert signer_mod.key_storage_mode() == "keyring"  # granted: keyring is the auto default
    assert signer_mod._KEYRING_RECORD_PATH.exists()
    assert not signer_mod._LEGACY_PRIV_KEY_PATH.exists()


def test_migrates_legacy_plaintext_to_keyring_on_load(monkeypatch, tmp_path) -> None:
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    seed = bytes(range(1, 33))
    signer_mod._KEY_DIR.mkdir(parents=True, exist_ok=True)
    signer_mod._LEGACY_PRIV_KEY_PATH.write_text(base64.b64encode(seed).decode("utf-8"), encoding="utf-8")
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: fake)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")  # the explicit operator grant

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id == _peer_id_for_seed(seed)  # identity preserved — wallet/creds unaffected
    assert signer_mod.key_storage_mode() == "keyring"
    assert not signer_mod._LEGACY_PRIV_KEY_PATH.exists()  # plaintext removed only after verified write


def test_auto_falls_back_to_protected_encrypted_file_without_keyring_or_passphrase(
    monkeypatch, tmp_path
) -> None:
    """2026-09-02 contract: with no keyring grant and no passphrase, the signer must NEVER
    silently write a plaintext seed — the fallback is an AES-GCM record under the machine
    protection secret (0600), and the degradation is surfaced once."""
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: None)  # no backend on this host

    peer_id = signer_mod.get_local_peer_id()

    assert peer_id  # never a hard lockout
    assert signer_mod.key_storage_mode() == "encrypted_file"
    assert signer_mod._KEY_RECORD_PATH.exists()
    assert signer_mod._LEGACY_PRIV_KEY_PATH.exists() is False, "no plaintext seed"
    machine = signer_mod._ACCOUNT_FILE_PROTECTION_PATH
    assert machine.exists() and machine.stat().st_mode & 0o777 == 0o600


def test_keyring_verify_failure_keeps_legacy_and_raises(monkeypatch, tmp_path) -> None:
    # If the keyring readback does not match, the plaintext must be kept (no lockout) and explicit
    # keyring mode must raise rather than silently downgrade.
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    seed = bytes(range(2, 34))
    signer_mod._KEY_DIR.mkdir(parents=True, exist_ok=True)
    signer_mod._LEGACY_PRIV_KEY_PATH.write_text(base64.b64encode(seed).decode("utf-8"), encoding="utf-8")

    class _CorruptReadKeyring(_FakeKeyring):
        def get_password(self, service: str, account: str) -> str | None:
            return base64.b64encode(b"\x00" * 32).decode("utf-8")  # never matches the written seed

    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "keyring")
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: _CorruptReadKeyring())

    try:
        signer_mod.load_or_create_local_keypair()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected a keyring verify failure to raise in explicit keyring mode")
    assert signer_mod._LEGACY_PRIV_KEY_PATH.exists()  # plaintext preserved -> recoverable


def test_a_keychain_authorization_prompt_degrades_to_file_storage_instead_of_hanging(
    monkeypatch, tmp_path
) -> None:
    """The macOS first-launch incident, reduced: a backend call from a binary the Keychain has
    never seen BLOCKS on the GUI authorization dialog -- it does not raise. Unbounded, daemon
    bootstrap waits forever (measured live: 90s supervisor timeout, no window). Bounded, auto
    mode must fall back to its file store and still produce a working keypair."""
    import threading
    import time as _time

    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)

    class _PromptingKeyring(_FakeKeyring):
        """Every call blocks the way a pending Keychain dialog does."""

        def set_password(self, service: str, account: str, value: str) -> None:
            _time.sleep(600)

        def get_password(self, service: str, account: str) -> str | None:
            _time.sleep(600)

    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "auto")
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: _PromptingKeyring())
    import core.bounded_keyring as _bk

    monkeypatch.setattr(_bk, "DEFAULT_TIMEOUT_S", 0.2)

    started = _time.monotonic()
    keypair = signer_mod.load_or_create_local_keypair()
    elapsed = _time.monotonic() - started

    assert elapsed < 10, f"bootstrap blocked {elapsed:.1f}s on a pending keychain prompt"
    assert keypair is not None and keypair.peer_id
    # The blocked worker threads are daemons and must never keep the process alive.
    assert all(
        t.daemon for t in threading.enumerate() if t.name.startswith("vool-keyring-")
    ), "keyring worker threads must be daemon threads"


def test_machine_protected_record_reopens_without_passphrase_env(monkeypatch, tmp_path) -> None:
    """2026-09-02 live-matrix finding: the unattended fallback seals the seed under the machine
    protection secret, but the NEXT boot hit the encrypted-record branch which demanded
    VOOL_KEY_PASSPHRASE and hard-failed. A machine-sealed record must reopen without any env."""
    importlib.reload(signer_mod)
    _configure_signer_paths(tmp_path)
    monkeypatch.delenv("VOOL_KEY_STORAGE_MODE", raising=False)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setattr(signer_mod, "_keyring_backend", lambda: None)  # no backend, no grant path

    first = signer_mod.load_or_create_local_keypair()
    peer_first = first.peer_id
    assert signer_mod.key_storage_mode() == "encrypted_file"

    signer_mod._LOCAL_KEYPAIR = None  # simulate a process restart with the record on disk
    second = signer_mod.load_or_create_local_keypair()
    assert second.peer_id == peer_first, "the protected record must reopen across restarts"
    assert signer_mod.key_storage_mode() == "encrypted_file"
