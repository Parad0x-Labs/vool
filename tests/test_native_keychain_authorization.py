"""Human authorization belongs to one native startup budget, not an IO timeout."""

import base64
import json
import time
from unittest import mock

import pytest

import core.bounded_keyring as bk
import network.signer as signer
from installer.bundle.native_runtime_supervisor import NativeRuntimeSupervisor


@pytest.fixture(autouse=True)
def reset_breaker(monkeypatch):
    monkeypatch.setattr(bk, "_KEYCHAIN_BLOCKED", False)
    monkeypatch.setattr(bk, "DEFAULT_TIMEOUT_S", 0.01)


def test_existing_signing_identity_survives_human_authorization_delay(tmp_path, monkeypatch):
    seed = bytes(range(32))
    record = tmp_path / "identity.json"
    record.write_text(json.dumps({"format": "keyring_seed", "service": "test", "account": "owner"}))
    original = record.read_bytes()

    def approve(*args):
        time.sleep(0.05)
        return base64.b64encode(seed).decode()

    backend = mock.Mock(get_password=mock.Mock(side_effect=approve))
    monkeypatch.setattr(signer, "_keyring_backend", lambda: backend)
    with bk.native_bootstrap_authorization(str(time.monotonic() + 1)):
        assert signer._load_keyring_seed(record) == seed
    assert record.read_bytes() == original
    backend.set_password.assert_not_called()
    assert not bk.keychain_blocked()


def test_new_credential_operation_can_finish_after_short_io_budget():
    def authorize_calendar_credential():
        time.sleep(0.05)
        return "synthetic-calendar-value"

    with bk.native_bootstrap_authorization(str(time.monotonic() + 1)):
        assert bk.bounded_keyring_call(authorize_calendar_credential, what="calendar read") == "synthetic-calendar-value"
    assert not bk.keychain_blocked()


def test_budget_is_shared_and_expired_scope_never_starts_a_call(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(bk.time, "monotonic", lambda: now[0])
    with bk.native_bootstrap_authorization("110"):
        assert bk.bounded_keyring_call(lambda: "first", what="first") == "first"
        now[0] = 111.0
        backend = mock.Mock()
        with pytest.raises(TimeoutError, match="window expired"):
            bk.bounded_keyring_call(backend, what="second")
        backend.assert_not_called()


def test_scope_restores_short_bound_after_exception():
    with pytest.raises(RuntimeError):
        with bk.native_bootstrap_authorization(str(time.monotonic() + 1)):
            raise RuntimeError("bootstrap failed")
    with pytest.raises(TimeoutError, match=r"0\.01s"):
        bk.bounded_keyring_call(lambda: time.sleep(0.05), what="background read")


def test_denial_is_not_retried_or_converted_to_success():
    backend = mock.Mock(side_effect=RuntimeError("authorization denied"))
    with bk.native_bootstrap_authorization(str(time.monotonic() + 1)):
        with pytest.raises(RuntimeError, match="authorization denied"):
            bk.bounded_keyring_call(backend, what="identity")
    backend.assert_called_once()
    assert bk.keychain_blocked()


@pytest.mark.parametrize("deadline", ["nan", "inf", "-1", "invalid"])
def test_invalid_deadline_fails_closed(deadline):
    with pytest.raises(ValueError):
        with bk.native_bootstrap_authorization(deadline):
            pytest.fail("invalid budget admitted")


def test_native_child_receives_one_deadline_without_mutating_parent_environment(tmp_path):
    child = mock.Mock()
    child.poll.return_value = None
    spawn = mock.Mock(return_value=child)
    supervisor = NativeRuntimeSupervisor(tmp_path, ["python", "-m", "apps.vool_api_server"],
                                         expected_sha="a" * 40, env={"VOOL_HOME": str(tmp_path)},
                                         startup_timeout=30, popen=spawn)
    supervisor._health = mock.Mock(side_effect=[None, {"runtime": {"commit_full": "a" * 40, "dirty": False}}])
    before = time.monotonic()
    try:
        supervisor.ensure_ready()
        deadline = float(spawn.call_args.kwargs["env"]["VOOL_NATIVE_AUTHORIZATION_DEADLINE"])
        assert before + 26 <= deadline <= time.monotonic() + 27
        assert "VOOL_NATIVE_AUTHORIZATION_DEADLINE" not in supervisor.env
    finally:
        supervisor._close_log()
