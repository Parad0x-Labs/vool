from __future__ import annotations

from core.wallet_spend_policy import (
    ADDRESS_CHANGE_LOCK_SECONDS,
    SpendLedger,
    SpendPolicy,
    address_change_ready,
    cancel_withdraw_address,
    check_spend_allowed,
    commit_withdraw_address,
    freeze,
    propose_withdraw_address,
    remaining_today,
    should_auto_withdraw,
    sign_policy,
    unfreeze,
    verify_policy,
)

_KEY = b"node-signing-key-bytes-for-tests"
_DAY = 24 * 60 * 60


# ---- caps ------------------------------------------------------------------

def test_per_tx_cap():
    p = SpendPolicy(per_tx_cap_lamports=5_000_000)
    assert check_spend_allowed(p, SpendLedger(), 4_000_000, now=1000)[0] is True
    assert check_spend_allowed(p, SpendLedger(), 6_000_000, now=1000)[0] is False


def test_daily_cap_accumulates_and_resets():
    p = SpendPolicy(daily_cap_lamports=10_000_000)
    ledger = SpendLedger()
    ledger.record(now=1000, lamports=8_000_000)
    assert check_spend_allowed(p, ledger, 2_000_000, now=1000)[0] is True  # exactly at cap
    assert check_spend_allowed(p, ledger, 3_000_000, now=1000)[0] is False  # over cap
    # A day later the earlier spend has aged out of the 24h window.
    assert check_spend_allowed(p, ledger, 9_000_000, now=1000 + _DAY + 1)[0] is True


def test_weekly_cap():
    p = SpendPolicy(weekly_cap_lamports=20_000_000)
    ledger = SpendLedger()
    ledger.record(now=1000, lamports=15_000_000)
    assert check_spend_allowed(p, ledger, 4_000_000, now=1000)[0] is True
    assert check_spend_allowed(p, ledger, 6_000_000, now=1000)[0] is False


def test_remaining_today_tracks_spend():
    p = SpendPolicy(daily_cap_lamports=10_000_000)
    ledger = SpendLedger()
    ledger.record(now=1000, lamports=3_000_000)
    assert remaining_today(p, ledger, now=1000) == 7_000_000
    assert remaining_today(SpendPolicy(), ledger, now=1000) is None  # no cap set


def test_zero_or_negative_amount_refused():
    assert check_spend_allowed(SpendPolicy(per_tx_cap_lamports=5), SpendLedger(), 0, now=1)[0] is False


# ---- panic freeze ----------------------------------------------------------

def test_freeze_blocks_all_spending():
    p = freeze(SpendPolicy(per_tx_cap_lamports=10_000_000))
    assert check_spend_allowed(p, SpendLedger(), 1, now=1)[0] is False
    unfreeze(p)
    assert check_spend_allowed(p, SpendLedger(), 1, now=1)[0] is True


# ---- tamper-evident config -------------------------------------------------

def test_signature_round_trips_and_detects_tampering():
    p = SpendPolicy(daily_cap_lamports=10_000_000, auto_withdraw_address="MainWallet1111")
    sig = sign_policy(p, _KEY)
    assert verify_policy(p, sig, _KEY) is True
    # Attacker edits the withdrawal address on disk → signature no longer matches.
    p.auto_withdraw_address = "AttackerWallet9999"
    assert verify_policy(p, sig, _KEY) is False
    # Wrong key also fails.
    assert verify_policy(SpendPolicy(), sign_policy(SpendPolicy(), _KEY), b"other-key") is False


def test_policy_json_round_trip():
    p = SpendPolicy(per_tx_cap_lamports=5, daily_cap_lamports=10, weekly_cap_lamports=20, frozen=True)
    assert SpendPolicy.from_json(p.to_json()) == p


# ---- time-locked withdrawal address ---------------------------------------

def test_address_change_is_time_locked():
    p = SpendPolicy()
    propose_withdraw_address(p, "NewMain1111", now=1000)
    assert p.pending_address == "NewMain1111"
    # Within the lock: not ready, commit refused, active address unchanged.
    assert address_change_ready(p, now=1000 + 3600) is False
    ok, _ = commit_withdraw_address(p, now=1000 + 3600)
    assert ok is False
    assert p.auto_withdraw_address == ""
    # After the full lock: ready, commit promotes it.
    later = 1000 + ADDRESS_CHANGE_LOCK_SECONDS + 1
    assert address_change_ready(p, now=later) is True
    ok, _ = commit_withdraw_address(p, now=later)
    assert ok is True
    assert p.auto_withdraw_address == "NewMain1111"
    assert p.pending_address == ""


def test_pending_address_can_be_cancelled():
    p = SpendPolicy()
    propose_withdraw_address(p, "Suspicious9999", now=1000)
    cancel_withdraw_address(p)
    assert p.pending_address == ""
    assert commit_withdraw_address(p, now=1000 + ADDRESS_CHANGE_LOCK_SECONDS + 1)[0] is False


# ---- auto-withdrawal decision ---------------------------------------------

def test_should_auto_withdraw_requires_all_conditions():
    base = SpendPolicy(
        auto_withdraw_enabled=True,
        auto_withdraw_threshold_lamports=100_000_000,
        auto_withdraw_address="Main1111",
    )
    assert should_auto_withdraw(base, balance_lamports=150_000_000)[0] is True
    assert should_auto_withdraw(base, balance_lamports=50_000_000)[0] is False  # below threshold
    assert should_auto_withdraw(freeze(SpendPolicy(**{**base.__dict__})), 150_000_000)[0] is False  # frozen
    no_addr = SpendPolicy(auto_withdraw_enabled=True, auto_withdraw_threshold_lamports=1)
    assert should_auto_withdraw(no_addr, balance_lamports=10)[0] is False  # no address
    assert should_auto_withdraw(SpendPolicy(), balance_lamports=10**12)[0] is False  # disabled
