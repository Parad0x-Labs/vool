"""A hallucinated account name must teach the model the recoverable path.

Measured 2026-11-05 (operator's live 0.7.8 test): "check for Kucoin emails with deposit
notification" — the model passed ``account='Kucoin'`` (a SENDER it hallucinated into an
account name), the refusal answered "configure that account", and the model retried the same
wrong account twice, burning two turns. The refusal is the one text the tool loop reads: it
must name the recoverable path (default account + ``sender=`` filter) before the configure
path, and never suggest a password pasted into chat.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_policy(tmp_path, monkeypatch):
    from core import policy_engine, runtime_paths

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    runtime_paths.configure_runtime_home(home)
    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    policy_engine._POLICY_CACHE = None
    yield
    policy_engine._POLICY_CACHE = previous_cache
    runtime_paths.configure_runtime_home(None)


def _read_refusal(account: str = "Kucoin") -> str:
    from core.email_tools import read_email

    result = read_email(account=account, folder="inbox")
    assert result.ok is False and result.status == "needs_credentials"
    return result.message


def test_imap_refusal_names_the_sender_filter_path_first() -> None:
    message = _read_refusal()
    assert "do NOT pass `account`" in message
    assert "sender='Kucoin'" in message
    assert "default account" in message


def test_imap_refusal_keeps_the_never_paste_a_password_law() -> None:
    message = _read_refusal()
    assert "never ask you to paste a password into chat" in message
    assert "app password via email account setup" in message


def test_imap_refusal_names_configured_alternatives(monkeypatch) -> None:
    """The measured session: the refusal listed the one account that could read."""
    from types import SimpleNamespace

    import core.email_accounts as accounts_module

    record = SimpleNamespace(
        read=True, send=False, state="configured",
        identities={"imap": "owner-mailbox", "smtp": ""},
    )
    monkeypatch.setattr(accounts_module, "configured_accounts", lambda: {"owner-mailbox": record})
    message = _read_refusal()
    assert "Accounts that can read:" in message
    assert "owner-mailbox" in message


def test_smtp_refusal_carries_the_same_two_paths() -> None:
    from core.email_tools import send_email

    result = send_email(to="someone" + "@" + "example.com", subject="s", body="b", account="NotARealOne")
    assert result.ok is False and result.status == "needs_credentials"
    assert "retry without it to send from the default account" in result.message
