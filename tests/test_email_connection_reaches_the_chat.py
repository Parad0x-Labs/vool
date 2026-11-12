"""A Settings-green email account must be a chat-capable email account.

Measured 2026-11-03 (build 8af687ca, served chats): the Settings page showed the email account
connected (core.email_accounts state "configured" — credentials stored, transport selectable),
while every chat ask died — the tool contracts read
``policy_engine.get("email.read_enabled"/"email.send_enabled", False)`` and NOTHING in the
product ever wrote those keys: not the setup flow, not the settings page, not the API, not the
shipped default policy. The two green systems never met; the model honestly reported "I cannot
access your email directly". These tests pin the repaired contract: an explicitly set policy key
wins in both directions, and an UNSET key (the shipped state) follows the account the operator
actually connected — the same authority the Settings page shows.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_policy(tmp_path, monkeypatch):
    """Hermetic policy AND credential state.

    The credential store resolves outside VOOL_HOME by default, so without the store override a
    real account from the developer's own machine leaks into the no-account tests (measured: the
    no-account controls saw email.read supported=True). ``VOOL_CREDENTIAL_STORE=vault`` under the
    temp home is the same isolation the operator-profile rig uses."""
    from core import policy_engine, runtime_paths

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    runtime_paths.configure_runtime_home(home)
    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    policy_engine._POLICY_CACHE = None  # force a reload from the empty home
    yield
    policy_engine._POLICY_CACHE = previous_cache
    runtime_paths.configure_runtime_home(None)


def _with_account(monkeypatch, imap: str, smtp: str):
    """Patch the authority the helper reads: configured_accounts() with one account record.

    A real record is built from stored credential blobs; the flag contract under test is the
    record's own read/send booleans, so a stand-in carrying the same contract exercises the
    wiring without standing up a credential store."""
    from types import SimpleNamespace

    import core.email_accounts as accounts_module

    record = SimpleNamespace(read=bool(imap), send=bool(smtp), state="configured")
    monkeypatch.setattr(accounts_module, "configured_accounts", lambda: {"gmail-main": record})


def _contracts():
    from core.runtime_tool_contracts import runtime_tool_contracts

    return {contract.intent: contract for contract in runtime_tool_contracts()}


def test_unset_policy_and_no_account_stays_unsupported_with_an_honest_reason() -> None:
    contracts = _contracts()
    for intent in ("email.read", "email.send", "email.open"):
        contract = contracts[intent]
        assert not contract.supported, intent
        assert "Settings" in contract.unsupported_reason, (intent, contract.unsupported_reason)


def test_connected_account_enables_read_without_any_policy_key(monkeypatch) -> None:
    _with_account(monkeypatch, imap="gmail-main", smtp="")
    contracts = _contracts()
    for intent in ("email.read", "email.open"):
        assert contracts[intent].supported, (intent, contracts[intent].unsupported_reason)
    assert not contracts["email.send"].supported
    assert "Settings" in contracts["email.send"].unsupported_reason


def test_both_transports_connected_enable_send_and_read(monkeypatch) -> None:
    _with_account(monkeypatch, imap="gmail-main", smtp="gmail-main")
    contracts = _contracts()
    assert contracts["email.read"].supported
    assert contracts["email.send"].supported
    assert contracts["email.draft.save"].supported


def test_explicit_policy_false_beats_a_connected_account(monkeypatch) -> None:
    from core import policy_engine

    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": False, "send_enabled": False}
    policy_engine._POLICY_CACHE = base
    _with_account(monkeypatch, imap="gmail-main", smtp="gmail-main")
    contracts = _contracts()
    assert not contracts["email.read"].supported
    assert not contracts["email.send"].supported


def test_explicit_policy_true_holds_without_an_account() -> None:
    from core import policy_engine

    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": True, "send_enabled": True}
    policy_engine._POLICY_CACHE = base
    contracts = _contracts()
    assert contracts["email.read"].supported
    assert contracts["email.send"].supported


def test_connected_account_escalates_the_ask_into_actions(monkeypatch) -> None:
    """The whole served dead end, end to end: 'check my email...' now requires tools."""
    from core.execution_requirements import requirements_for

    _with_account(monkeypatch, imap="gmail-main", smtp="")
    req = requirements_for(
        "check my email, tell me if anything important is there",
        task_class="chat_conversation",
    )
    assert req.tools_required, req.reason_codes
    assert "email" in req.allowed_toolsets
    assert "email_account_action_request" in req.reason_codes


def test_without_an_account_the_ask_stays_an_ordinary_turn() -> None:
    """Preservation: no account and no policy — nothing changes for existing users."""
    from core.execution_requirements import requirements_for

    req = requirements_for(
        "check my email, tell me if anything important is there",
        task_class="chat_conversation",
    )
    assert "email_account_action_request" not in req.reason_codes
