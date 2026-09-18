from __future__ import annotations

import pytest

from core import os_consent_gate
from core.os_consent_gate import (
    ConsentDeniedError,
    ConsentUnavailableError,
    require_os_user_consent,
    set_consent_override_for_tests,
)


@pytest.fixture(autouse=True)
def _clear_override_and_env(monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_SKIP_CONSENT_GATE", raising=False)
    set_consent_override_for_tests(None)
    # Safety net: NO test may invoke a real OS prompt (Touch ID / osascript / pkexec / Hello).
    # Default every native tier to "unavailable"; each test re-patches only the tiers it exercises.
    for _name in (
        "_try_windows_hello",
        "_try_windows_credential_prompt",
        "_try_macos_localauth",
        "_try_macos_presence_prompt",
        "_try_linux_pkexec",
        "_try_linux_presence_prompt",
    ):
        monkeypatch.setattr(os_consent_gate, _name, lambda reason: None)
    yield
    set_consent_override_for_tests(None)


def test_override_granted_allows_consent():
    set_consent_override_for_tests(lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_override_denied_returns_false():
    set_consent_override_for_tests(lambda reason: False)
    assert require_os_user_consent("reveal key") is False


def test_env_bypass_requires_literal_yes(monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_SKIP_CONSENT_GATE", "yes")
    assert require_os_user_consent("reveal key") is True


def test_env_bypass_ignores_non_yes_values(monkeypatch):
    # Anything other than the literal "yes" must NOT bypass the gate.
    monkeypatch.setenv("VOOL_WALLET_SKIP_CONSENT_GATE", "1")
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    monkeypatch.setattr(os_consent_gate, "_try_linux_pkexec", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_linux_presence_prompt", lambda reason: None)
    with pytest.raises(ConsentUnavailableError):
        require_os_user_consent("reveal key")


# --- macOS: LocalAuthentication (Touch ID) -> osascript presence -----------------


def test_macos_localauth_verified_grants(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "darwin")
    monkeypatch.setattr(os_consent_gate, "_try_macos_localauth", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_macos_localauth_declined_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "darwin")
    monkeypatch.setattr(os_consent_gate, "_try_macos_localauth", lambda reason: False)
    with pytest.raises(ConsentDeniedError):
        require_os_user_consent("reveal key")


def test_macos_falls_back_to_presence_prompt(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "darwin")
    monkeypatch.setattr(os_consent_gate, "_try_macos_localauth", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_macos_presence_prompt", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_macos_presence_cancelled_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "darwin")
    monkeypatch.setattr(os_consent_gate, "_try_macos_localauth", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_macos_presence_prompt", lambda reason: False)
    with pytest.raises(ConsentDeniedError):
        require_os_user_consent("reveal key")


def test_macos_no_mechanism_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "darwin")
    monkeypatch.setattr(os_consent_gate, "_try_macos_localauth", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_macos_presence_prompt", lambda reason: None)
    with pytest.raises(ConsentUnavailableError):
        require_os_user_consent("reveal key")


# --- Linux: polkit pkexec -> zenity/kdialog presence -----------------------------


def test_linux_pkexec_verified_grants(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    monkeypatch.setattr(os_consent_gate, "_try_linux_pkexec", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_linux_pkexec_declined_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    monkeypatch.setattr(os_consent_gate, "_try_linux_pkexec", lambda reason: False)
    with pytest.raises(ConsentDeniedError):
        require_os_user_consent("reveal key")


def test_linux_falls_back_to_presence_prompt(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    monkeypatch.setattr(os_consent_gate, "_try_linux_pkexec", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_linux_presence_prompt", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_linux_no_mechanism_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    monkeypatch.setattr(os_consent_gate, "_try_linux_pkexec", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_linux_presence_prompt", lambda reason: None)
    with pytest.raises(ConsentUnavailableError):
        require_os_user_consent("reveal key")


def test_unknown_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "sunos5")
    with pytest.raises(ConsentUnavailableError):
        require_os_user_consent("reveal key")


def test_windows_hello_verified_grants(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_windows_hello_declined_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: False)
    with pytest.raises(ConsentDeniedError):
        require_os_user_consent("reveal key")


def test_falls_back_to_credential_prompt_when_hello_unavailable(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_credential_prompt_cancelled_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: False)
    with pytest.raises(ConsentDeniedError):
        require_os_user_consent("reveal key")


def test_no_mechanism_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: None)
    with pytest.raises(ConsentUnavailableError):
        require_os_user_consent("reveal key")
