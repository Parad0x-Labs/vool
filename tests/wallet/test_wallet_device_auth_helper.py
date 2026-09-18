"""The device-authentication helper: compiled from the embedded Swift, shipped in the bundle, protocol 2 on stdin.

Nothing here prompts or touches the Keychain: `selftest` only asks LocalAuthentication whether device-owner
authentication is possible, and the request-validation paths fail before any Security call. The prompting paths
(store/read of the Keychain user-presence item) are exercised only by the operator's own Touch ID.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

from core.wallet import device_auth
from tests.wallet._device_auth_fake import FakeDeviceAuthority


def test_the_bundle_build_ships_the_helper():
    src = Path(device_auth.__file__).resolve().parents[2].joinpath("installer", "bundle", "build_macos_app.sh").read_text()
    assert "core.wallet.device_auth --build" in src and "Contents/Resources/bin/vool-devauth" in src


def test_no_environment_variable_selects_an_authority_or_a_helper(monkeypatch, tmp_path):
    decoy = tmp_path / "vool-devauth"
    decoy.write_text("#!/bin/sh\n")
    decoy.chmod(0o700)
    monkeypatch.setenv("VOOL_DEVAUTH_HELPER", str(decoy))
    monkeypatch.setenv("VOOL_WALLET_TOOLS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_DEVICE_AUTH_FAKE", "1")
    device_auth.set_device_authority_for_tests(None)
    assert device_auth.shipped_helper() != decoy
    assert device_auth.tools_dir() != tmp_path
    authority = device_auth.current_authority()
    assert type(authority) is device_auth.SwiftHelperAuthority
    assert not hasattr(authority, "secrets") and not hasattr(authority, "deny_next"), "nothing fake-shaped"
    source = Path(device_auth.__file__).read_text(encoding="utf-8")
    for token in ("VOOL_DEVICE_AUTH_FAKE", "VOOL_DEVAUTH_HELPER", "VOOL_WALLET_TOOLS_DIR", "FakeDeviceAuthority", "_ENV_FAKE"):
        assert token not in source, token


def test_the_harness_fake_records_prompts_and_can_deny():
    fake = FakeDeviceAuthority()
    fake.store_unlock_secret("w1", b"x" * 32, reason="bind")
    assert fake.prompts == [] and fake.stores == ["bind"]
    assert fake.read_unlock_secret("w1", reason="export") == b"x" * 32 and fake.prompts == ["export"]
    fake.deny_next = True
    with pytest.raises(device_auth.DeviceAuthDenied):
        fake.read_unlock_secret("w1", reason="export again")
    with pytest.raises(device_auth.DeviceAuthDenied):
        fake.read_unlock_secret("unknown", reason="never stored")


def test_the_swift_source_takes_the_request_on_stdin_only():
    src = device_auth.swift_source()
    assert "guard args.count == 2 else" in src, "argv is the command and nothing else"
    assert "FileHandle.standardInput.readDataToEndOfFile()" in src
    assert "args[2]" not in src and "args[3]" not in src and "args[4]" not in src and "args[5]" not in src
    assert '"secret_b64"' in src and 'req["service"]' in src and 'req["account"]' in src
    assert device_auth.TOOL_VERSION == "2"


@pytest.mark.skipif(platform.system() != "Darwin" or not os.access(device_auth.SWIFTC, os.X_OK), reason="needs macOS with swiftc")
def test_the_swift_helper_compiles_and_speaks_protocol_2_without_touching_the_keychain(tmp_path):
    binary = device_auth.compile_helper(tmp_path / "vool-devauth")
    assert binary.exists() and os.access(binary, os.X_OK)
    out = subprocess.run([str(binary), "selftest"], capture_output=True, text=True, timeout=60)
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 0 and payload["ok"] is True and payload["protocol"] == 2
    assert "device_owner_authentication" in payload
    # argv beyond the command is refused before anything is read
    out = subprocess.run([str(binary), "store", "svc", "acct", "c2VjcmV0", "reason"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 1 and json.loads(out.stdout.strip().splitlines()[-1])["error"] == "usage"
    # a malformed or incomplete request fails closed before any Security call
    for body in (b"not json", json.dumps({"service": "svc"}).encode(), json.dumps({"service": "svc", "account": "a"}).encode()):
        out = subprocess.run([str(binary), "store"], input=body, capture_output=True, timeout=30)
        assert out.returncode == 1 and json.loads(out.stdout.decode().strip().splitlines()[-1])["error"] == "bad_request", body
    out = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 1 and json.loads(out.stdout.strip().splitlines()[-1])["error"] == "usage"
