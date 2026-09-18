"""The tamper-evident spend-policy store, keyed off the DEVICE secret.

The integrity key no longer comes from a signing wallet (`wallet.sign(domain)`): reading a spend
policy must not require -- or grant -- signing authority. The store accepts ``wallet=None`` and
ignores any handle it is given. The former "a different wallet cannot read the policy" test is
replaced by its real successor: a different DEVICE secret cannot read (or forge) the policy.
"""
from __future__ import annotations

import pytest

from core.wallet_spend_policy import SpendLedger, SpendPolicy
from core.wallet_spend_policy_store import (
    PolicyIntegrityError,
    load_policy_and_ledger,
    policy_path,
    record_spend,
    save_policy_and_ledger,
)


class _PresentHandle:
    """A handle that reports an encrypted wallet file on disk. A missing policy alongside a present
    wallet is the deletion-attack signature and must still fail closed on read paths. It has no
    sign(): presence is all the store may ask of it."""

    def exists(self) -> bool:
        return True


class _NoSigner:
    """A handle with neither sign() nor exists(): proves the store never needs signing authority."""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    yield
    assert not list(tmp_path.rglob("solana_wallet.enc")), "the policy store minted a legacy key file"


def test_missing_file_returns_permissive_defaults() -> None:
    # No wallet on disk -> nothing to protect -> permissive defaults (first-run).
    policy, ledger = load_policy_and_ledger(None)
    assert policy.frozen is False
    assert policy.daily_cap_lamports == 0 and policy.weekly_cap_lamports == 0
    assert ledger.entries == []


def test_missing_policy_fails_closed_when_a_wallet_is_present() -> None:
    # A wallet handle reports a key file but its policy file is gone: refuse permissive defaults so
    # a deleted policy can't silently clear a freeze/caps. Read/authorization paths must raise.
    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(_PresentHandle())


def test_missing_policy_bootstraps_on_establish_path() -> None:
    # The establish/write path (first freeze or set-cap) may bootstrap defaults even with a
    # present wallet, so setting up a policy for the first time is not blocked.
    policy, ledger = load_policy_and_ledger(_PresentHandle(), allow_missing_policy=True)
    assert policy.frozen is False
    assert ledger.entries == []


def test_roundtrip_policy_and_ledger_with_no_wallet() -> None:
    policy = SpendPolicy(per_tx_cap_lamports=20_000_000, daily_cap_lamports=50_000_000, frozen=True)
    ledger = SpendLedger()
    ledger.record(1000.0, 7_000_000)
    ledger.record(1001.0, 3_000_000)
    save_policy_and_ledger(None, policy, ledger)

    loaded_policy, loaded_ledger = load_policy_and_ledger(None)
    assert loaded_policy.frozen is True
    assert loaded_policy.per_tx_cap_lamports == 20_000_000
    assert loaded_policy.daily_cap_lamports == 50_000_000
    assert sorted(loaded_ledger.entries) == [(1000.0, 7_000_000), (1001.0, 3_000_000)]


def test_wallet_handle_is_ignored_and_never_asked_to_sign() -> None:
    # Saved with one handle, read with another and with None: the key is the device's, not the handle's.
    save_policy_and_ledger(_NoSigner(), SpendPolicy(frozen=True), SpendLedger())
    assert load_policy_and_ledger(None)[0].frozen is True
    assert load_policy_and_ledger(_NoSigner())[0].frozen is True
    assert load_policy_and_ledger(object())[0].frozen is True


def test_tampered_file_fails_closed() -> None:
    save_policy_and_ledger(None, SpendPolicy(daily_cap_lamports=10_000_000), SpendLedger())
    # Attacker raises the cap on disk without knowing the HMAC key.
    path = policy_path()
    text = path.read_text(encoding="utf-8")
    tampered = text.replace("10000000", "999000000000")
    assert tampered != text
    path.write_text(tampered, encoding="utf-8")

    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(None)


def test_different_device_secret_cannot_read_or_forge_the_policy(monkeypatch) -> None:
    import network.signer as signer

    save_policy_and_ledger(None, SpendPolicy(frozen=True), SpendLedger())
    real = signer.derive_local_secret

    def _other_device(label, *, length=32):
        return bytes((b ^ 0xFF) for b in real(label, length=length))

    monkeypatch.setattr(signer, "derive_local_secret", _other_device)
    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(None)
    # And a policy the other device writes is rejected back on this one.
    save_policy_and_ledger(None, SpendPolicy(frozen=False), SpendLedger())
    monkeypatch.setattr(signer, "derive_local_secret", real)
    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(None)


def test_hmac_domain_is_the_v2_device_label(monkeypatch) -> None:
    import network.signer as signer

    labels: list[bytes] = []
    real = signer.derive_local_secret

    def _spy(label, *, length=32):
        labels.append(label if isinstance(label, bytes) else label.encode())
        return real(label, length=length)

    monkeypatch.setattr(signer, "derive_local_secret", _spy)
    save_policy_and_ledger(None, SpendPolicy(), SpendLedger())
    assert b"vool/spend-policy/hmac/v2" in labels
    assert all(len(real(label, length=32)) == 32 for label in labels)


def test_corrupt_json_fails_closed() -> None:
    save_policy_and_ledger(None, SpendPolicy(), SpendLedger())
    policy_path().write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(None)


def test_record_spend_accumulates_atomically() -> None:
    save_policy_and_ledger(None, SpendPolicy(daily_cap_lamports=100_000_000), SpendLedger())
    # Two sequential records must both survive (no last-writer-wins drop): each reloads
    # the current ledger under the lock before appending.
    record_spend(None, 4_000_000, 1_000.0)
    record_spend(None, 6_000_000, 1_001.0)
    _policy, ledger = load_policy_and_ledger(None)
    assert sorted(ledger.entries) == [(1_000.0, 4_000_000), (1_001.0, 6_000_000)]
    assert ledger.spent_within(1_002.0, 24 * 60 * 60) == 10_000_000


def test_record_spend_on_a_tampered_policy_records_nothing() -> None:
    save_policy_and_ledger(None, SpendPolicy(daily_cap_lamports=100_000_000), SpendLedger())
    path = policy_path()
    before = path.read_text(encoding="utf-8")
    path.write_text(before.replace('"hmac": "', '"hmac": "00'), encoding="utf-8")
    record_spend(None, 4_000_000, 1_000.0)  # never raises; never re-signs a tampered file
    assert path.read_text(encoding="utf-8") != before
    with pytest.raises(PolicyIntegrityError):
        load_policy_and_ledger(None)


def test_ledger_prune_on_save_keeps_recent_only() -> None:
    ledger = SpendLedger()
    now = 10_000_000.0
    ledger.record(now - (8 * 24 * 60 * 60), 1_000)  # >1 week old -> pruned
    ledger.record(now - 60, 2_000)  # recent -> kept
    save_policy_and_ledger(None, SpendPolicy(), ledger, now=now)
    _policy, loaded = load_policy_and_ledger(None)
    assert loaded.entries == [(now - 60, 2_000)]
