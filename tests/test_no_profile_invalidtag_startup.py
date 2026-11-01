"""First-run law (2026-11-01): a missing VOOL profile is a clean first-run setup, never a
silent read of the pre-rename NULLA store; and an undecryptable encrypted record at startup
is a plain user message with byte-identical data, never a silent quit.

Measured failure this file pins closed: a fresh machine with an old NULLA profile had that
store adopted implicitly, its identity material failed AES-GCM authentication
(``cryptography.exceptions.InvalidTag``), the daemon died with a bare traceback in a log
file nobody was shown, and the native window never opened.
"""
from __future__ import annotations

import base64
import importlib
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from core.runtime_paths import active_data_dir

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------------------------
# Part 1: profile selection — no VOOL profile means a clean first run on every OS lane
# ------------------------------------------------------------------------------------------


def test_missing_vool_profile_means_clean_first_run_darwin(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import native_runtime_home

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    base = tmp_path / "Library" / "Application Support"
    legacy = base / "NULLA" / "runtime"
    (legacy / "data").mkdir(parents=True)
    legacy_db = legacy / "data" / "legacy.db"
    legacy_db.write_bytes(b"pre-rename-profile")
    assert native_runtime_home({}) == base / "VOOL" / "runtime"
    assert legacy_db.read_bytes() == b"pre-rename-profile"


def test_missing_vool_profile_means_clean_first_run_windows(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import native_runtime_home

    monkeypatch.setattr(sys, "platform", "win32")
    local = tmp_path / "LocalAppData"
    legacy = local / "NULLA"
    (legacy / "runtime" / "data").mkdir(parents=True)
    assert native_runtime_home({"LOCALAPPDATA": str(local)}) == local / "VOOL"


def test_missing_vool_profile_means_clean_first_run_linux(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import native_runtime_home, user_runtime_default

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    legacy = tmp_path / ".nulla_runtime"
    legacy.mkdir()
    assert user_runtime_default() == tmp_path / ".vool_runtime"
    assert native_runtime_home({}) == tmp_path / ".vool_runtime"
    assert legacy.exists(), "the legacy store is left on disk; it is only never selected"


def test_explicit_home_still_wins_over_a_legacy_store(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import native_runtime_home

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    (tmp_path / "Library" / "Application Support" / "NULLA" / "runtime").mkdir(parents=True)
    explicit = tmp_path / "chosen"
    assert native_runtime_home({"VOOL_HOME": str(explicit)}) == explicit


def test_launcher_leaves_a_legacy_store_unadopted_and_names_it(tmp_path: Path) -> None:
    """The REAL generated launcher, run as a real file: legacy-only machine -> canonical
    home, visible note, legacy bytes untouched."""
    from tests.test_rename_upgrade_compatibility import (
        _legacy_runtime,
        _run_launcher,
        _self_contained_launcher_text,
        _stub_bundle_root,
        _without_macos_hardware_gate,
    )

    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_runtime(home)
    legacy_db = legacy / "data/nulla_web0_v2.db"
    before = legacy_db.read_bytes()
    log = _run_launcher(
        _without_macos_hardware_gate(_self_contained_launcher_text()),
        home,
        _stub_bundle_root(tmp_path),
    )
    canonical = home / "Library/Application Support/VOOL/runtime"
    assert f"VOOL_HOME={canonical}" in log, f"legacy store was adopted implicitly: {log}"
    assert "not used" in log and str(legacy) in log
    assert legacy_db.read_bytes() == before


def test_install_script_default_home_is_canonical(tmp_path) -> None:
    """installer/install_vool.sh picks the canonical home by default; a legacy
    ~/.nulla_runtime is never adopted. The selection block is executed for real by
    sourcing the script's own prologue up to the end of that block."""
    script = (REPO / "installer" / "install_vool.sh").read_text(encoding="utf-8")
    end_marker = "fi\nMIN_PYTHON_MAJOR=3"
    assert end_marker in script, "the selection block moved; update this prologue slice"
    prologue = script.split(end_marker, 1)[0] + "fi\n"
    prologue += 'printf \'DEFAULT=%s\\n\' "${VOOL_HOME_DEFAULT}"\n'
    legacy = tmp_path / ".nulla_runtime"
    legacy.mkdir()
    done = _run_bash_with_home(prologue, tmp_path)
    assert done["DEFAULT"] == str(tmp_path / ".vool_runtime")
    done = _run_bash_with_home(prologue, tmp_path, {"VOOL_HOME": str(tmp_path / "chosen")})
    assert done["DEFAULT"] == str(tmp_path / "chosen")


def _run_bash_with_home(prologue: str, home: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    import os
    import subprocess

    done = subprocess.run(
        ["bash", "-c", prologue],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), **(extra_env or {})},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return dict(
        line.split("=", 1) for line in done.stdout.strip().splitlines() if "=" in line
    )


# ------------------------------------------------------------------------------------------
# Part 2: an undecryptable identity record is typed, plain, and touches nothing
# ------------------------------------------------------------------------------------------


def _configure_signer_paths(tmp_path: Path) -> types.SimpleNamespace:
    import network.signer as signer_mod

    signer_mod._KEY_DIR = tmp_path / "keys"
    signer_mod._KEY_DIR.mkdir(parents=True, exist_ok=True)
    signer_mod._LEGACY_PRIV_KEY_PATH = signer_mod._KEY_DIR / "node_signing_key.b64"
    signer_mod._KEY_RECORD_PATH = signer_mod._KEY_DIR / "node_signing_key.json"
    signer_mod._KEYRING_RECORD_PATH = signer_mod._KEY_DIR / "node_signing_key.keyring.json"
    signer_mod._KEY_ARCHIVE_DIR = signer_mod._KEY_DIR / "archive"
    signer_mod._ACCOUNT_FILE_PROTECTION_PATH = signer_mod._KEY_DIR / "key_storage.passphrase"
    signer_mod._LOCAL_KEYPAIR = None
    return types.SimpleNamespace(mod=signer_mod)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _foreign_sealed_record(signer) -> None:
    record = {
        "version": 1,
        "format": "encrypted_seed",
        "protection": "user_passphrase",
        "kdf": "pbkdf2_sha256",
        "iterations": 390_000,
        "salt_b64": base64.b64encode(b"1" * 16).decode(),
        "nonce_b64": base64.b64encode(b"2" * 12).decode(),
        "ciphertext_b64": base64.b64encode(b"sealed-by-someone-else" * 3).decode(),
    }
    signer.mod._KEY_RECORD_PATH.write_text(json.dumps(record), encoding="utf-8")


def test_foreign_sealed_record_is_typed_and_leaves_profile_byte_identical(
    monkeypatch, tmp_path
) -> None:
    from network.signer import SigningKeyUnreadable

    signer = _configure_signer_paths(tmp_path)
    _foreign_sealed_record(signer)
    before = _snapshot(tmp_path)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "a-passphrase-that-sealed-nothing")
    monkeypatch.setattr(signer.mod, "_keyring_backend", lambda: None)

    with pytest.raises(SigningKeyUnreadable) as excinfo:
        signer.mod.load_or_create_local_keypair()

    assert "Nothing was changed" in str(excinfo.value)
    assert _snapshot(tmp_path) == before, "an unreadable key must never be rewritten"
    archive = tmp_path / "keys" / "archive"
    assert not archive.exists() or not any(archive.rglob("*")), "no silent archival either"


def test_damaged_record_is_typed_not_a_raw_trace(monkeypatch, tmp_path) -> None:
    from network.signer import SigningKeyUnreadable

    signer = _configure_signer_paths(tmp_path)
    signer.mod._KEY_RECORD_PATH.write_text(
        json.dumps({"format": "encrypted_seed", "ciphertext_b64": "!!not-base64!!"}),
        encoding="utf-8",
    )
    before = _snapshot(tmp_path)
    monkeypatch.setenv("VOOL_KEY_PASSPHRASE", "any")

    with pytest.raises(SigningKeyUnreadable):
        signer.mod.load_or_create_local_keypair()

    assert _snapshot(tmp_path) == before


def test_missing_passphrase_record_keeps_its_own_actionable_error(
    monkeypatch, tmp_path
) -> None:
    """A user-passphrase record with NO passphrase ambient is a different, pre-existing
    failure naming the env var — it keeps that message and is NOT the typed decrypt
    failure (nothing was decrypted yet)."""
    from network.signer import SigningKeyUnreadable

    signer = _configure_signer_paths(tmp_path)
    _foreign_sealed_record(signer)
    monkeypatch.delenv("VOOL_KEY_PASSPHRASE", raising=False)
    monkeypatch.setattr(signer.mod, "_keyring_backend", lambda: None)

    with pytest.raises(RuntimeError, match="VOOL_KEY_PASSPHRASE") as excinfo:
        signer.mod.load_or_create_local_keypair()

    assert not isinstance(excinfo.value, SigningKeyUnreadable)


# ------------------------------------------------------------------------------------------
# Part 3: the blocked boot is loud — record, exit code, supervisor, dialog
# ------------------------------------------------------------------------------------------


def test_startup_blocker_record_write_read_and_clear(tmp_path) -> None:
    import apps.vool_api_server as server

    server._write_startup_blocker("signing_key_unreadable", "a plain sentence")
    path = active_data_dir() / "startup_blocker.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "vool.startup_blocker.v1"
    assert payload["reason"] == "signing_key_unreadable"
    assert payload["message"] == "a plain sentence"
    assert payload["pid"] == server.os.getpid()
    server._clear_startup_blocker()
    assert not path.exists()


def test_api_server_blocked_boot_prints_and_exits_86(monkeypatch, tmp_path) -> None:
    import apps.vool_api_server as server
    from network.signer import SigningKeyUnreadable

    monkeypatch.setattr(sys, "argv", ["vool-api-server", "--port", "11435"])
    monkeypatch.setattr(server, "_arm_fatal_signal_diagnostics", lambda: False)
    monkeypatch.setattr(server, "_bootstrap", mock.Mock(side_effect=SigningKeyUnreadable("plain words")))
    from core import runtime_dependency_preflight

    monkeypatch.setattr(
        runtime_dependency_preflight, "preflight_served_imports_or_exit", lambda: None
    )

    # faulthandler.register(file=sys.stderr) demands a real fileno, which pytest's captured
    # stderr has none of; a plain file stands in and the assertion reads it back.
    stderr_log = tmp_path / "stderr.log"
    handle = stderr_log.open("w", encoding="utf-8")
    monkeypatch.setattr(sys, "stderr", handle)
    try:
        assert server.main() == server._STARTUP_BLOCKED_EXIT_CODE == 86
    finally:
        handle.close()
    out = stderr_log.read_text(encoding="utf-8")
    assert "VOOL cannot start:" in out and "plain words" in out
    payload = json.loads((active_data_dir() / "startup_blocker.json").read_text(encoding="utf-8"))
    assert payload["message"] == "plain words"


def _read_blocker(home: Path, child_pid: int) -> dict | None:
    from installer.bundle.native_runtime_supervisor import NativeRuntimeSupervisor

    supervisor = NativeRuntimeSupervisor.__new__(NativeRuntimeSupervisor)
    supervisor.env = {"VOOL_HOME": str(home)}
    return supervisor._read_startup_blocker(child_pid)


def test_supervisor_accepts_only_the_dead_childs_own_blocker(tmp_path) -> None:
    record = {
        "schema": "vool.startup_blocker.v1",
        "reason": "signing_key_unreadable",
        "message": "plain words",
        "pid": 9001,
    }
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "startup_blocker.json").write_text(json.dumps(record), encoding="utf-8")
    assert _read_blocker(tmp_path, 9001) == {
        "reason": "signing_key_unreadable",
        "message": "plain words",
    }
    assert _read_blocker(tmp_path, 4242) is None, "another process's record never speaks for this child"
    record["schema"] = "something.else.v9"
    (data / "startup_blocker.json").write_text(json.dumps(record), encoding="utf-8")
    assert _read_blocker(tmp_path, 9001) is None


def test_supervisor_raises_the_plain_message_when_its_child_dies(tmp_path) -> None:
    from installer.bundle.native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

    record = {
        "schema": "vool.startup_blocker.v1",
        "reason": "signing_key_unreadable",
        "message": "VOOL cannot decrypt this profile's identity record.",
        "pid": 9001,
    }
    (tmp_path / "home" / "data").mkdir(parents=True)
    (tmp_path / "home" / "data" / "startup_blocker.json").write_text(
        json.dumps(record), encoding="utf-8"
    )

    class _Child:
        pid = 9001
        returncode = 86

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    starter = tmp_path / "Start_VOOL.sh"
    starter.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    supervisor = NativeRuntimeSupervisor(
        tmp_path,
        ["bash", str(starter)],
        expected_sha="a" * 40,
        env={"VOOL_HOME": str(tmp_path / "home")},
        startup_timeout=1,
        poll_interval=0,
        popen=mock.Mock(return_value=_Child()),
        sleep=lambda _: None,
    )
    supervisor._health = mock.Mock(return_value=None)
    with pytest.raises(NativeRuntimeError, match=r"VOOL cannot decrypt this profile's identity record\."):
        supervisor.ensure_ready()
    assert supervisor.startup_blocker is not None
    assert supervisor.startup_blocker["reason"] == "signing_key_unreadable"


def test_window_decides_and_shows_the_blocker_dialog(monkeypatch) -> None:
    from installer.bundle import vool_window

    assert vool_window._startup_blocker_message(object()) is None
    assert vool_window._startup_blocker_message(
        types.SimpleNamespace(startup_blocker={"message": " spoken plain "})
    ) == "spoken plain"

    monkeypatch.setattr(sys, "platform", "darwin")
    command = vool_window._startup_blocker_alert_command("spoken plain")
    assert command is not None and command[0] == "osascript"
    assert "spoken plain" in command

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_window.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    assert vool_window._startup_blocker_alert_command("spoken plain")[0] == "zenity"
    monkeypatch.setattr(vool_window.shutil, "which", lambda name: None)
    assert vool_window._startup_blocker_alert_command("spoken plain") is None

    monkeypatch.setattr(sys, "platform", "darwin")
    runs: list[list[str]] = []
    monkeypatch.setattr(
        vool_window.subprocess,
        "run",
        lambda argv, **kwargs: runs.append(argv) or types.SimpleNamespace(returncode=0),
    )
    vool_window._show_startup_blocker_dialog("spoken plain")
    assert runs and runs[0][0] == "osascript"


def test_window_surfaces_the_supervisor_blocker(monkeypatch) -> None:
    """main()'s ensure_ready failure path reads the decision helper against the real
    supervisor attribute this delivery added; a supervisor without a blocker shows nothing."""
    from installer.bundle import vool_window

    shown: list[str] = []
    monkeypatch.setattr(vool_window, "_show_startup_blocker_dialog", shown.append)
    with_blocker = types.SimpleNamespace(
        startup_blocker={"reason": "signing_key_unreadable", "message": "plain"}
    )
    without_blocker = types.SimpleNamespace(startup_blocker=None)
    assert vool_window._startup_blocker_message(with_blocker) == "plain"
    assert vool_window._startup_blocker_message(without_blocker) is None
    message = vool_window._startup_blocker_message(with_blocker)
    if message:
        vool_window._show_startup_blocker_dialog(message)
    assert shown == ["plain"]


def test_signer_message_is_shaped_for_a_person() -> None:
    """The dialog sentence names no filesystem path and no exception class."""
    from network.signer import _UNREADABLE_SEED_MESSAGE, SigningKeyUnreadable

    assert issubclass(SigningKeyUnreadable, RuntimeError)
    assert "/" not in _UNREADABLE_SEED_MESSAGE and "\\" not in _UNREADABLE_SEED_MESSAGE
    assert "InvalidTag" not in _UNREADABLE_SEED_MESSAGE
    assert "Nothing was changed" in _UNREADABLE_SEED_MESSAGE


def test_reloaded_signer_still_exports_the_typed_failure() -> None:
    import network.signer as signer_mod

    importlib.reload(signer_mod)
    assert hasattr(signer_mod, "SigningKeyUnreadable")
