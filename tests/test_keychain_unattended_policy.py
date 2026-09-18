"""Zero Keychain dialogs from unattended or isolated runs (2026-09-02 operator addendum).

The operator saw repeated macOS dialogs — "A keychain cannot be found to store
node_signing_key" — triggered by SCRATCH/test processes: ``bounded_keyring_call`` only bounds
the caller, it cannot cancel the pending dialog, and every fresh process re-attempted the
keyring, so prompts accumulated.

The root policy under test:

1. Boot, tests, background daemons and isolated/sandbox homes NEVER initiate an interactive
   Keychain operation: keyring WRITES require an explicit operator grant
   (``VOOL_KEYCHAIN_ALLOWED=1`` or the Settings-written ``keychain.enabled`` flag in the
   active config home); keyring READS additionally stay allowed when THIS runtime has
   previously used the Keychain, so existing saved credentials stay readable through the
   normal signed-app path.
2. A Keychain timeout arms a process-wide circuit breaker — no retries, no further prompts.
3. Without the grant, secrets fall back to LOCAL PROTECTED storage (AES-GCM encrypted files),
   never silently plaintext, and the degradation is surfaced once.
4. The test session selects non-interactive storage BEFORE runtime modules are imported.
5. Proven here with a spy keyring: multiple concurrent scratch processes make ZERO
   set_password/get_password calls.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_keychain_breaker():
    import core.bounded_keyring as bounded_keyring

    bounded_keyring._KEYCHAIN_BLOCKED = False
    yield
    bounded_keyring._KEYCHAIN_BLOCKED = False


# ------------------------------------------------------------------------------------------
# The spy keyring: counts every call to a log file; a WORKING in-memory store so a granted
# run succeeds end-to-end while an unattended run must show ZERO log entries.
# ------------------------------------------------------------------------------------------

_SPY_KEYRING_SOURCE = '''
import json, os

_LOG = os.environ.get("VOOL_KEYRING_SPY_LOG", "")


def _rec(op, service, account):
    if _LOG:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"op": op, "service": service, "account": account,
                                 "pid": os.getpid()}) + "\\n")


class SpyWorkingBackend:
    """Counts every call; stores in memory so granted flows succeed."""

    def __init__(self):
        self._store = {}

    def set_password(self, service, account, password):
        _rec("set_password", service, account)
        self._store[(service, account)] = password

    def get_password(self, service, account):
        _rec("get_password", service, account)
        return self._store.get((service, account))

    def delete_password(self, service, account):
        _rec("delete_password", service, account)
        self._store.pop((service, account), None)


_BACKEND = SpyWorkingBackend()


def get_keyring():
    return _BACKEND


def set_password(service, account, password):
    _BACKEND.set_password(service, account, password)


def get_password(service, account):
    return _BACKEND.get_password(service, account)


def delete_password(service, account):
    _BACKEND.delete_password(service, account)
'''


def _spy_dir(tmp_path: Path, spy_log: Path) -> Path:
    shim = tmp_path / "spy-shim"
    shim.mkdir(exist_ok=True)
    (shim / "keyring.py").write_text(_SPY_KEYRING_SOURCE, encoding="utf-8")
    return shim


_SCRATCH_DRIVER = """
import sys
sys.path.insert(0, {repo!r})

from network.signer import load_or_create_local_keypair, key_storage_mode
kp = load_or_create_local_keypair()

from core import credential_store
credential_store.store_credential("probe.scratch", "probe-value", label="scratch")
value = credential_store.get_credential("probe.scratch")
assert value == "probe-value", value
print("MODE=%s BACKEND=%s" % (key_storage_mode(), credential_store.active_backend()))
"""


def _spy_lines(spy_log: Path) -> list[dict]:
    if not spy_log.exists():
        return []
    return [json.loads(line) for line in spy_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_scratch_driver(tmp_path: Path, home: Path, spy_log: Path, *, grant: bool) -> str:
    shim = _spy_dir(tmp_path, spy_log)
    driver = tmp_path / "scratch_driver.py"
    driver.write_text(_SCRATCH_DRIVER.format(repo=str(REPO)), encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "VOOL_HOME": str(home / "vool-home"),
        "VOOL_KEYRING_SPY_LOG": str(spy_log),
        "PYTHONPATH": os.pathsep.join([str(shim), str(REPO)]),
    }
    if grant:
        env["VOOL_KEYCHAIN_ALLOWED"] = "1"
    done = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                          env=env, timeout=180)
    assert done.returncode == 0, f"scratch driver failed: {done.stdout} {done.stderr}"
    return done.stdout


# ------------------------------------------------------------------------------------------
# 1. The test session itself must opt out of interactive storage before imports
# ------------------------------------------------------------------------------------------


def test_test_session_selects_non_interactive_storage_before_runtime_import() -> None:
    assert os.environ.get("VOOL_CREDENTIAL_STORE") == "vault", (
        "pytest must select explicit vault credential storage before runtime modules load"
    )
    assert os.environ.get("VOOL_KEY_STORAGE_MODE") == "file", (
        "pytest must select explicit file signer storage before runtime modules load"
    )
    assert os.environ.get("VOOL_KEYCHAIN_ALLOWED", "") == "", (
        "tests must never carry the interactive-keychain operator grant"
    )


# ------------------------------------------------------------------------------------------
# 2. Scratch processes: zero keyring calls, protected (encrypted) local fallback
# ------------------------------------------------------------------------------------------


def test_scratch_processes_make_zero_keyring_calls(tmp_path: Path) -> None:
    spy_log = tmp_path / "spy.jsonl"
    homes = [tmp_path / f"home{i}" for i in range(3)]
    for home in homes:
        home.mkdir()
        out = _run_scratch_driver(tmp_path, home, spy_log, grant=False)
        assert "BACKEND=vault" in out, f"scratch run must use the vault fallback: {out}"
        assert "MODE=keyring" not in out, "scratch signer must not store the seed in the keyring"
    assert _spy_lines(spy_log) == [], (
        f"unattended scratch processes issued keyring calls: {_spy_lines(spy_log)[:3]}"
    )


def test_scratch_signing_key_falls_back_to_protected_storage_not_plaintext(tmp_path: Path) -> None:
    home = tmp_path / "home-protected"
    home.mkdir()
    out = _run_scratch_driver(tmp_path, home, tmp_path / "spy2.jsonl", grant=False)
    assert "MODE=encrypted_file" in out, (
        f"unattended fallback must be an encrypted record, got: {out}"
    )
    key_dir = home / "vool-home" / "data" / "keys"
    record = json.loads((key_dir / "node_signing_key.json").read_text(encoding="utf-8"))
    assert record["format"] == "encrypted_seed"
    assert (key_dir / "node_signing_key.b64").exists() is False, "no plaintext seed may be written"
    assert not list(key_dir.glob("node_signing_key.keyring.json")), "no keyring record without grant"
    passphrase_file = key_dir / "key_storage.passphrase"
    assert passphrase_file.exists() and passphrase_file.stat().st_mode & 0o777 == 0o600, (
        "the local protection secret must exist with 0600 permissions"
    )


# ------------------------------------------------------------------------------------------
# 3. The explicit operator grant keeps the real Keychain path working (item 2 + 8)
# ------------------------------------------------------------------------------------------


def test_explicit_operator_grant_uses_the_keychain(tmp_path: Path) -> None:
    spy_log = tmp_path / "spy-grant.jsonl"
    home = tmp_path / "home-granted"
    home.mkdir()
    out = _run_scratch_driver(tmp_path, home, spy_log, grant=True)
    assert "MODE=keyring" in out, f"granted run must use the keyring: {out}"
    ops = [entry["op"] for entry in _spy_lines(spy_log)]
    assert "set_password" in ops and "get_password" in ops, ops
    key_dir = home / "vool-home" / "data" / "keys"
    assert (key_dir / "node_signing_key.keyring.json").exists()


# ------------------------------------------------------------------------------------------
# 4. Credential store, in-process: reads of EXISTING keychain creds survive; writes need the grant
# ------------------------------------------------------------------------------------------

@pytest.fixture()
def counting_keyring(monkeypatch):
    class CountingBackend:
        def __init__(self):
            self.store = {}
            self.counts = {"set_password": 0, "get_password": 0}

        def set_password(self, service, account, password):
            self.counts["set_password"] += 1
            self.store[(service, account)] = password

        def get_password(self, service, account):
            self.counts["get_password"] += 1
            return self.store.get((service, account))

    backend = CountingBackend()
    fake = mock.Mock(wraps=None)
    fake.get_keyring.return_value = backend
    fake.set_password = backend.set_password
    fake.get_password = backend.get_password
    backend.fake = fake  # tests that need to re-route the MODULE-level fake (e.g. a hang) patch here
    monkeypatch.setattr("core.credential_store._load_keyring", lambda: fake)
    return backend


def test_credential_store_unattended_uses_vault_with_zero_keyring_calls(
    tmp_path: Path, monkeypatch, counting_keyring
) -> None:
    import core.runtime_paths
    from core import credential_store

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    credential_store.store_credential("llm.cloud.openrouter", "skk-test", label="t")
    assert credential_store.get_credential("llm.cloud.openrouter") == "skk-test"
    assert credential_store.active_backend() == "vault"
    assert counting_keyring.counts == {"set_password": 0, "get_password": 0}, (
        "an unattended runtime must never touch the keyring"
    )


def test_existing_keychain_credential_stays_readable_but_writes_need_the_grant(
    tmp_path: Path, monkeypatch, counting_keyring
) -> None:
    """Item 8: a runtime that previously used the Keychain keeps READING its saved credentials
    without the grant; item 2: new WRITES still require the explicit operator grant."""
    from core import credential_store
    from core.runtime_paths import data_path

    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    monkeypatch.setattr("core.runtime_paths._VOOL_HOME_OVERRIDE", None)
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    data_path("credentials.keychain_migrated").write_text("1", encoding="utf-8")  # prior-use marker

    counting_keyring.store[("nulla-credentials", "llm.cloud.openrouter")] = "skk-existing"
    assert credential_store.get_credential("llm.cloud.openrouter") == "skk-existing", (
        "existing saved credentials must remain readable through the normal path"
    )

    credential_store.store_credential("llm.cloud.openrouter", "skk-new", label="t")
    assert counting_keyring.counts["set_password"] == 0, (
        "a keychain WRITE without the operator grant must never be attempted"
    )
    assert credential_store.get_credential("llm.cloud.openrouter") in {"skk-new", "skk-existing"}


# ------------------------------------------------------------------------------------------
# 5. The circuit breaker: one timed-out keychain worker, zero further prompts in that process
# ------------------------------------------------------------------------------------------


def test_keychain_timeout_arms_a_process_wide_circuit_breaker(
    tmp_path: Path, monkeypatch, counting_keyring
) -> None:
    import core.bounded_keyring as bounded_keyring
    import core.runtime_paths
    from core import credential_store

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    monkeypatch.setattr(bounded_keyring, "DEFAULT_TIMEOUT_S", 0.2)

    def hang(*args, **kwargs):
        counting_keyring.counts["get_password"] += 1  # the attempt itself IS a prompt risk
        time.sleep(2)
        return "too-late"

    counting_keyring.get_password = hang
    counting_keyring.fake.get_password = hang  # the module-level fake is what _active_keyring calls
    counting_keyring.counts["get_password"] = 0

    first = credential_store.get_credential("llm.cloud.openrouter")
    assert first is None, "a timed-out keychain read degrades to absent/vault"
    reads_after_first = counting_keyring.counts["get_password"]
    assert reads_after_first >= 1

    credential_store.store_credential("llm.cloud.openrouter", "v", label="t")
    assert counting_keyring.counts["set_password"] == 0, (
        "with the breaker armed even a GRANTED write must not re-attempt the keyring"
    )
    for _ in range(3):
        credential_store.get_credential("llm.cloud.openrouter")
        credential_store.store_credential("llm.cloud.openrouter", "v", label="t")
    assert bounded_keyring.keychain_blocked() is True
    assert counting_keyring.counts["get_password"] == reads_after_first, (
        "after a keychain timeout the process must never retry the keyring (no further prompts)"
    )
    assert counting_keyring.counts["set_password"] == 0
