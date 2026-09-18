"""Bounded correction (operator, 2026-09-07): no secret in the helper's argv, environment, logs or exception text;
no environment variable selects a fake authority; genuine unavailable / denied authentication still refuses.

Every proof runs the REAL `SwiftHelperAuthority` against a controlled helper that journals what reached it. Nothing
here touches the Keychain or prompts.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from core.wallet import device_auth
from tests.wallet._controlled_helper import ControlledHelper, install_for_runtime, runtime_cache_path

CANARY = b"CANARY-unlock-secret-7f3a9c1e5b2d4e6f8091a2b3c4d5e6f7"
CANARY_B64 = base64.b64encode(CANARY).decode("ascii")


def _no_canary(text: str) -> bool:
    return CANARY_B64 not in text and CANARY.decode() not in text


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture
def helper(tmp_path):
    return ControlledHelper(tmp_path / "tools" / "vool-devauth")


def test_store_and_read_carry_the_secret_on_stdin_and_nowhere_else(helper):
    authority = helper.authority()
    authority.store_unlock_secret("wallet-abc123", CANARY, reason="VOOL wallet wallet-abc12: unlock secret")
    got = authority.read_unlock_secret("wallet-abc123", reason="VOOL: export")
    assert got == CANARY
    store, read = helper.journal()
    assert store["argv"][1:] == ["store"] and read["argv"][1:] == ["read"], "argv is the command alone"
    assert "wallet-abc123" not in " ".join(store["argv"]) and "wallet-abc123" not in " ".join(read["argv"])
    for call in (store, read):
        assert _no_canary(json.dumps(call["env"])), "the helper's environment carries no secret"
        # LC_CTYPE is added by the helper's own Python interpreter at start-up (PEP 538 locale coercion), not passed by us
        assert set(call["env"]) - {"LC_CTYPE"} <= set(device_auth._ENV_KEEP) | {"PATH"}, sorted(call["env"])
        assert call["env"]["PATH"] == "/usr/bin:/bin"
    assert json.loads(store["stdin"])["secret_b64"] == CANARY_B64, "the secret travelled on stdin"
    assert "secret_b64" not in json.loads(read["stdin"])
    assert _no_canary(json.dumps(dict(os.environ))), "the parent process environment was never touched"


def test_a_hostile_helper_echoing_the_request_cannot_put_the_secret_into_exception_text(helper):
    helper.set_mode("hostile_echo")
    with pytest.raises(device_auth.DeviceAuthUnavailable) as exc:
        helper.authority().store_unlock_secret("w1", CANARY, reason="r")
    rendered = "".join(traceback.format_exception(exc.value))
    assert _no_canary(str(exc.value)) and _no_canary(repr(exc.value)) and _no_canary(rendered), rendered
    assert str(exc.value) == "helper_failed", str(exc.value)  # a non-whitelisted error code collapses to the generic one
    assert json.loads(helper.journal()[-1]["stdin"])["secret_b64"] == CANARY_B64, "the request did reach the helper"


def test_a_numeric_status_is_the_only_detail_that_survives(helper):
    helper.set_mode("unavailable")
    with pytest.raises(device_auth.DeviceAuthUnavailable) as exc:
        helper.authority().store_unlock_secret("w1", CANARY, reason="r")
    assert str(exc.value) == "helper_failed", str(exc.value)  # "controlled: unavailable" is free text, dropped
    assert device_auth._exception_text({"ok": False, "error": "store_failed", "detail": "-25299"}) == "store_failed (status -25299)"
    assert device_auth._exception_text({"ok": False, "error": "Store Failed!", "detail": CANARY_B64}) == "helper_failed"
    assert device_auth._exception_text(["not", "a", "dict"]) == "helper_failed"


def test_a_hanging_helper_times_out_without_chaining_its_partial_output(tmp_path):
    slow = tmp_path / "slow" / "vool-devauth"
    slow.parent.mkdir()
    slow.write_text(f"#!{sys.executable}\nimport sys, time\nsys.stdout.write('{CANARY_B64}')\nsys.stdout.flush()\ntime.sleep(5)\n")
    slow.chmod(0o700)
    with pytest.raises(device_auth.DeviceAuthDenied) as exc:
        device_auth.SwiftHelperAuthority(binary=slow)._run("read", {"service": "s", "account": "a"}, timeout=0.5)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(exc.value))
    assert _no_canary(rendered), rendered


def test_denied_and_unavailable_refuse_through_the_real_class(helper):
    authority = helper.authority()
    authority.store_unlock_secret("w1", CANARY, reason="r")
    helper.set_mode("deny")
    with pytest.raises(device_auth.DeviceAuthDenied) as exc:
        authority.read_unlock_secret("w1", reason="export")
    assert str(exc.value).startswith("denied")
    helper.set_mode("ok")
    with pytest.raises(device_auth.DeviceAuthDenied):
        authority.read_unlock_secret("never-stored", reason="export")


def test_a_bundle_uses_its_shipped_helper_whatever_the_environment_says(monkeypatch, tmp_path):
    bundle = tmp_path / "VOOL.app" / "Contents" / "Resources"
    (bundle / "python" / "bin").mkdir(parents=True)
    (bundle / "bin").mkdir()
    fake_python = bundle / "python" / "bin" / "python3"
    fake_python.write_text("")
    shipped = bundle / "bin" / "vool-devauth"
    shipped.write_text("#!/bin/sh\n")
    shipped.chmod(0o700)
    decoy = tmp_path / "decoy"
    decoy.write_text("#!/bin/sh\n")
    decoy.chmod(0o700)
    monkeypatch.setattr(device_auth.sys, "executable", str(fake_python))
    monkeypatch.setenv("VOOL_DEVAUTH_HELPER", str(decoy))
    assert device_auth.shipped_helper() == shipped
    assert device_auth.SwiftHelperAuthority()._binary() == shipped


def test_the_runtime_cache_path_is_derived_from_the_home_alone(monkeypatch, tmp_path):
    from core.runtime_paths import active_data_dir

    monkeypatch.setenv("VOOL_WALLET_TOOLS_DIR", str(tmp_path / "elsewhere"))
    assert Path(device_auth.tools_dir()) == Path(active_data_dir()) / "wallet_tools", "the runtime's own data directory, whatever the environment says"
    expected = runtime_cache_path(tmp_path / "home")
    assert expected.parent == (tmp_path / "home").resolve() / "data" / "wallet_tools"
    assert expected.name == f"{device_auth.TOOL_NAME}-{device_auth.TOOL_VERSION}-{device_auth._source_digest()}"


@pytest.mark.served
def test_served_daemon_ignores_the_fake_variable_and_refuses_when_the_device_is_unavailable_or_denies(tmp_path):
    """One daemon, four truths: VOOL_DEVICE_AUTH_FAKE=1 changes nothing; an unavailable helper refuses creation of a
    device wallet; a denying helper refuses export as a counted refusal; a working helper exports through the real
    IPC with no secret in argv or environment."""
    import tests._reader_served_rig as rig
    from core.wallet import custody

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_DEVICE_AUTH_FAKE": "1"})
        daemon.register_provider()
        controlled = install_for_runtime(tmp_path / "home")
        controlled.set_mode("unavailable")
        try:
            try:
                daemon.start()
            except Exception as exc:  # pragma: no cover - environment
                pytest.skip(f"served daemon could not boot here: {exc}")
            base = f"http://127.0.0.1:{daemon.port}"
            body = {"acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE, "approval_method": "device"}
            status, refused = _post(base, "/api/wallet/pocket/create", body)
            assert status == 403 and refused["error"] == "wallet_device_auth_unavailable", (status, refused)
            controlled.set_mode("ok")
            status, created = _post(base, "/api/wallet/pocket/create", body)
            assert status == 200 and created["wallet"]["wallet_id"], (status, created)
            wallet_id = created["wallet"]["wallet_id"]
            controlled.set_mode("deny")
            status, denied = _post(base, "/api/wallet/export", {"wallet_id": wallet_id, "target": "phantom"})
            assert status == 403 and denied["error"] == "wallet_device_auth_denied", (status, denied)
            controlled.set_mode("ok")
            status, exported = _post(base, "/api/wallet/export", {"wallet_id": wallet_id, "target": "phantom"})
            assert status == 200 and exported["export"]["format"] == "solana_keypair_base58" and len(exported["export"]["value"]) >= 86, (status, exported)
            calls = controlled.journal()
            assert [c["argv"][1] for c in calls] == ["store", "store", "read", "read"], [c["argv"][1:] for c in calls]
            stored = json.loads(calls[1]["stdin"])["secret_b64"]
            for c in calls:
                assert stored not in " ".join(c["argv"]) and stored not in json.dumps(c["env"])
                assert c["env"]["PATH"] == "/usr/bin:/bin" and "VOOL_DEVICE_AUTH_FAKE" not in c["env"]
        finally:
            daemon.stop()
