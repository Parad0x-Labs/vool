"""The `vool spend-*` policy commands after the legacy signing wallet was retired.

The commands operate on the device-keyed spend policy file (HMAC keyed off the node identity
secret, `wallet=None`); no wallet is created, loaded or signed with. Rewritten from a fixture that
minted a real agent wallet through `get_or_create_wallet` -- that door now refuses typed, and the
assertion here is that the commands neither need it nor mint one behind the operator's back.
"""
from __future__ import annotations

import json

import pytest

from apps.vool_cli import (
    _load_agent_wallet_or_none,
    cmd_spend_freeze,
    cmd_spend_policy,
    cmd_spend_set_cap,
    cmd_spend_unfreeze,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Isolated VOOL_HOME with NO wallet: the policy file is keyed off the device secret."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from core.os_consent_gate import set_consent_override_for_tests

    set_consent_override_for_tests(None)
    yield
    set_consent_override_for_tests(None)
    assert not list(tmp_path.rglob("solana_wallet.enc")), "a spend-policy command minted a legacy key file"


def _policy_json(capsys) -> dict:
    assert cmd_spend_policy(json_mode=True) == 0
    return json.loads(capsys.readouterr().out)


def test_no_signing_wallet_is_loaded_for_policy_commands() -> None:
    assert _load_agent_wallet_or_none() is None


def test_freeze_then_policy_shows_frozen(capsys) -> None:
    assert cmd_spend_freeze() == 0
    assert "FROZEN" in capsys.readouterr().out
    assert cmd_spend_policy() == 0
    out = capsys.readouterr().out
    assert "Frozen:" in out and "YES" in out


def test_freeze_persists_in_the_device_keyed_policy_file() -> None:
    from core.wallet_spend_policy_store import load_policy_and_ledger, policy_path

    assert cmd_spend_freeze(json_mode=True) == 0
    assert policy_path().exists()
    policy, _ledger = load_policy_and_ledger(None)
    assert policy.frozen is True


def test_set_cap_persists_and_displays(capsys) -> None:
    assert cmd_spend_set_cap(daily=0.02, weekly=0.05) == 0
    capsys.readouterr()
    payload = _policy_json(capsys)
    assert payload["daily_cap_lamports"] == 20_000_000
    assert payload["weekly_cap_lamports"] == 50_000_000


def test_set_cap_clear_removes_caps(capsys) -> None:
    assert cmd_spend_set_cap(per_tx=0.01, daily=0.02) == 0
    capsys.readouterr()
    assert cmd_spend_set_cap(clear=True) == 0
    capsys.readouterr()
    payload = _policy_json(capsys)
    assert payload["per_tx_cap_lamports"] is None
    assert payload["daily_cap_lamports"] is None


def test_tampered_policy_refuses_cap_change_and_unfreeze(capsys) -> None:
    from core.wallet_spend_policy_store import policy_path

    assert cmd_spend_freeze() == 0
    capsys.readouterr()
    path = policy_path()
    path.write_text(path.read_text(encoding="utf-8").replace('"hmac": "', '"hmac": "00'), encoding="utf-8")
    from core.os_consent_gate import set_consent_override_for_tests

    set_consent_override_for_tests(lambda _reason: True)
    assert cmd_spend_unfreeze() == 1
    assert "integrity" in capsys.readouterr().out.lower()
    assert cmd_spend_set_cap(daily=0.5) == 1
    assert "integrity" in capsys.readouterr().out.lower()
    assert cmd_spend_policy() == 1


def test_unfreeze_denied_by_consent_stays_frozen(capsys) -> None:
    from core.os_consent_gate import set_consent_override_for_tests

    assert cmd_spend_freeze() == 0
    capsys.readouterr()
    set_consent_override_for_tests(lambda _reason: False)  # OS consent DECLINES
    assert cmd_spend_unfreeze() == 1
    assert "frozen" in capsys.readouterr().out.lower()
    assert _policy_json(capsys)["frozen"] is True


def test_unfreeze_with_consent_unfreezes(capsys) -> None:
    from core.os_consent_gate import set_consent_override_for_tests

    assert cmd_spend_freeze() == 0
    capsys.readouterr()
    set_consent_override_for_tests(lambda _reason: True)  # OS consent GRANTS
    assert cmd_spend_unfreeze() == 0
    assert "UNFROZEN" in capsys.readouterr().out
    assert _policy_json(capsys)["frozen"] is False


def test_unfreeze_when_not_frozen_is_noop(capsys) -> None:
    assert cmd_spend_unfreeze() == 0
    assert "not frozen" in capsys.readouterr().out.lower()
