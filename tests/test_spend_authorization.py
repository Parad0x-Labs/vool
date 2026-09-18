"""The shared spend-authorization brake: owner-local + freeze + SOL caps + OS consent, and
the consent-gated per-spend-approval toggle (brakes ON by default, opt-out with a warning).

No signing wallet is loaded on this path any more: the policy store is keyed off the device
secret, `wallet=None` is the production shape, and a missing wallet is never an excuse to mint
one. The gate order and every fail-closed branch are unchanged and pinned below.
"""
from __future__ import annotations

import pytest

import core.spend_authorization as sa
from core.wallet_spend_policy import SpendLedger, SpendPolicy


@pytest.fixture(autouse=True)
def _no_real_os_prompt(monkeypatch):
    # Safety: default-deny the OS consent hook so no test in this file pops a live dialog when a
    # path reaches require_os_user_consent; tests that need a grant override _TEST_OVERRIDE.
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: False)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    yield tmp_path
    assert not list(tmp_path.rglob("solana_wallet.enc")), "spend authorization minted a legacy key file"


def _loader(policy: SpendPolicy):
    ledger = SpendLedger()
    return lambda _wallet: (policy, ledger)


def _auth(**over):
    kw = dict(
        asset="SOL",
        amount=1000,
        recipient="rcpt",
        reason="test spend",
        owner_local=True,
        wallet=None,
        consent_fn=lambda _r: True,
        policy_loader=_loader(SpendPolicy()),
        now=1_700_000_000.0,
    )
    kw.update(over)
    return sa.require_spend_authorized(**kw)


# ── the brake ────────────────────────────────────────────────────────────────

def test_non_owner_is_refused():
    ok, why = _auth(owner_local=False)
    assert ok is False and "own local session" in why


def test_zero_amount_refused():
    ok, _ = _auth(amount=0)
    assert ok is False


def test_frozen_wallet_refused():
    ok, why = _auth(policy_loader=_loader(SpendPolicy(frozen=True)))
    assert ok is False and "frozen" in why


def test_over_per_tx_cap_refused():
    ok, why = _auth(amount=2000, policy_loader=_loader(SpendPolicy(per_tx_cap_lamports=1000)))
    assert ok is False and "per-transaction cap" in why


def test_consent_declined_refused():
    ok, why = _auth(consent_fn=lambda _r: False)
    assert ok is False and "consent" in why.lower()


def test_consent_unavailable_fails_closed():
    def _boom(_r):
        raise RuntimeError("no OS prompt")

    ok, why = _auth(consent_fn=_boom)
    assert ok is False and "unavailable" in why


def test_tampered_policy_fails_closed():
    def _raise(_wallet):
        raise ValueError("HMAC mismatch")

    ok, why = _auth(policy_loader=_raise)
    assert ok is False and "refusing to spend" in why


def test_gate_order_freeze_before_consent():
    # A frozen policy must never reach the OS prompt.
    asked: list[str] = []
    ok, _ = _auth(policy_loader=_loader(SpendPolicy(frozen=True)), consent_fn=lambda r: asked.append(r) or True)
    assert ok is False and asked == []


def test_happy_path_allowed():
    ok, why = _auth()
    assert ok is True and why == "ok"


def test_loader_receives_no_wallet_and_none_is_minted(isolated_home):
    # The production shape: wallet=None reaches the loader untouched; nothing tries to create one.
    seen: list = []

    def _loader_spy(wallet):
        seen.append(wallet)
        return SpendPolicy(), SpendLedger()

    ok, _ = _auth(policy_loader=_loader_spy)
    assert ok is True and seen == [None]


def test_default_store_path_needs_no_wallet(isolated_home):
    # With no loader injected the device-keyed store answers; no wallet is loaded or created and the
    # outcome is decided by policy + consent alone.
    ok, why = _auth(policy_loader=None, consent_fn=lambda _r: False)
    assert ok is False and "consent" in why.lower()
    ok2, why2 = _auth(policy_loader=None)
    assert ok2 is True and why2 == "ok"


def test_default_store_freeze_is_honoured(isolated_home):
    from core.wallet_spend_policy import freeze
    from core.wallet_spend_policy_store import save_policy_and_ledger

    save_policy_and_ledger(None, freeze(SpendPolicy()), SpendLedger())
    asked: list[str] = []
    ok, why = _auth(policy_loader=None, consent_fn=lambda r: asked.append(r) or True)
    assert ok is False and "frozen" in why and asked == []
    ok_usdc, why_usdc = _auth(asset="USDC", amount=500_000, policy_loader=None, consent_fn=lambda r: asked.append(r) or True)
    assert ok_usdc is False and "frozen" in why_usdc and asked == []


def test_usdc_passes_without_lamport_caps_but_still_needs_consent(monkeypatch):
    # A per-tx SOL cap must NOT block a USDC spend (units differ); freeze + consent still apply.
    ok, _ = _auth(asset="USDC", amount=500_000, policy_loader=_loader(SpendPolicy(per_tx_cap_lamports=1)))
    assert ok is True
    ok2, why2 = _auth(asset="USDC", amount=500_000, consent_fn=lambda _r: False)
    assert ok2 is False and "consent" in why2.lower()


def test_brake_off_skips_consent(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    # Owner turned per-spend approval OFF -> no consent prompt is consulted.
    assert sa.set_spend_consent_required(False, consent_fn=lambda _r: True)[0] is True

    def _must_not_be_called(_r):
        raise AssertionError("consent must not be prompted when the brake is off")

    ok, _ = _auth(consent_fn=_must_not_be_called)
    assert ok is True


# ── the toggle ───────────────────────────────────────────────────────────────

def test_consent_required_defaults_true(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    assert sa.spend_consent_required() is True  # no file -> brake on


def test_turning_off_requires_consent(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    # Declined at the OS prompt -> stays ON.
    changed, msg = sa.set_spend_consent_required(False, consent_fn=lambda _r: False)
    assert changed is False and sa.spend_consent_required() is True
    assert "ON" in msg
    # Approved -> turns OFF, with a warning.
    changed, msg = sa.set_spend_consent_required(False, consent_fn=lambda _r: True)
    assert changed is True and sa.spend_consent_required() is False
    assert "WARNING" in msg


def test_turning_on_needs_no_consent(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    sa.set_spend_consent_required(False, consent_fn=lambda _r: True)

    def _must_not_be_called(_r):
        raise AssertionError("turning the brake back ON must not prompt")

    changed, msg = sa.set_spend_consent_required(True, consent_fn=_must_not_be_called)
    assert changed is True and sa.spend_consent_required() is True
    assert "ON" in msg


# ── the `brakes on|off|status` chat command ──────────────────────────────────

def _brakes(text, owner_local):
    from core.agent_runtime.fast_command_surface import maybe_handle_brakes_command

    return maybe_handle_brakes_command(text, owner_local=owner_local)


def test_brakes_command_ignores_non_commands():
    assert _brakes("what are spend brakes?", True) is None
    assert _brakes("hello", True) is None


def test_brakes_status_readable_from_anywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    out = _brakes("brakes status", owner_local=False)
    assert out is not None and "ON" in out


def test_brakes_non_owner_cannot_change(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    out = _brakes("brakes off", owner_local=False)
    assert "local session" in (out or "").lower()
    assert sa.spend_consent_required() is True  # unchanged


def test_brakes_owner_off_then_on(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_setting_path", lambda: tmp_path / "spend_brake.json")
    # Turning OFF is OS-consent-gated; approve it at the prompt.
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: True)
    out = _brakes("brakes off", owner_local=True)
    assert "WARNING" in out and sa.spend_consent_required() is False
    out2 = _brakes("brakes on", owner_local=True)
    assert sa.spend_consent_required() is True and "ON" in out2
