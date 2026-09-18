"""A token transfer holds its principal in the token and its network fee in the native coin -- never one sum of both.

A USDC payment on Solana spends USDC microunits and a fee in lamports. Holding them as one number would let a fee
count against a USDC ceiling (and a USDC principal against the SOL balance). The spend ledger keeps one row per asset:
the principal row carries a zero fee and a companion row carries the fee in SOL; reservation, settlement, release and
re-settlement move them together, and the quote's view of what is already held reads per asset. A native SOL transfer
keeps its single row of amount plus fee.
"""
from __future__ import annotations

import time

import pytest

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
DESTINATION = "9SynthPayTo111111111111111111111111111111111"
WALLET = "wallet-split-holds"


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.delenv("VOOL_WALLET_GLOBAL_DAILY_MINOR", raising=False)
    from core.wallet import limits

    wide = limits.SpendLimits(per_tx_minor=10_000_000_000, daily_minor=50_000_000_000, per_destination_daily_minor=50_000_000_000)
    limits.set_limits(WALLET, "USDC", wide)
    limits.set_limits(WALLET, "SOL", wide)
    yield limits


def _rows(proposal_id: str) -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT proposal_id, asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
            (proposal_id, f"{proposal_id}:fee"),
        ).fetchall()]


def _held(limits, asset: str) -> int:
    from core.wallet.store import connection

    with connection() as conn:
        return limits._held_within(conn, WALLET, asset, time.time() - 3600, chain=SOLANA_MAINNET)


def test_a_usdc_hold_is_its_principal_in_usdc_and_its_fee_in_sol(ledger) -> None:
    verdict = ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=150_000, destination=DESTINATION, proposal_id="p-usdc", fee_minor=5_000, chain=SOLANA_MAINNET)
    assert verdict.ok, verdict
    assert _rows("p-usdc") == [("p-usdc", "USDC", 150_000, 0, "reserved"), ("p-usdc:fee", "SOL", 0, 5_000, "reserved")]
    assert (_held(ledger, "USDC"), _held(ledger, "SOL")) == (150_000, 5_000)
    again = ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=150_000, destination=DESTINATION, proposal_id="p-usdc", fee_minor=5_000, chain=SOLANA_MAINNET)
    assert again.reason == "already held" and len(_rows("p-usdc")) == 2


def test_settlement_release_and_resettlement_move_both_rows(ledger) -> None:
    from core.wallet.store import connection

    ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=150_000, destination=DESTINATION, proposal_id="p-settle", fee_minor=5_000, chain=SOLANA_MAINNET)
    with connection() as conn:
        ledger._begin_immediate(conn)
        ledger._settle(conn, "p-settle", charged_fee_minor=4_200, amount_moved=True, now=time.time())
    assert _rows("p-settle") == [("p-settle", "USDC", 150_000, 0, "settled"), ("p-settle:fee", "SOL", 0, 4_200, "settled")]
    with connection() as conn:
        ledger._begin_immediate(conn)
        ledger._resettle(conn, "p-settle", charged_fee_minor=5_000, amount_moved=False)
    assert _rows("p-settle") == [("p-settle", "USDC", 0, 0, "settled"), ("p-settle:fee", "SOL", 0, 5_000, "settled")]

    ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=90_000, destination=DESTINATION, proposal_id="p-release", fee_minor=5_000, chain=SOLANA_MAINNET)
    ledger.release_spend("p-release")
    assert [row[-1] for row in _rows("p-release")] == ["released", "released"]

    ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=70_000, destination=DESTINATION, proposal_id="p-absent-fee", fee_minor=5_000, chain=SOLANA_MAINNET)
    with connection() as conn:
        ledger._begin_immediate(conn)
        ledger._settle(conn, "p-absent-fee", charged_fee_minor=None, amount_moved=True, now=time.time())
    assert _rows("p-absent-fee")[1][3] == 5_000, "an unknown charged fee keeps the reserved maximum counted, never zero"


def test_each_ceiling_judges_only_its_own_asset(ledger) -> None:
    # A USDC per-transaction ceiling equal to the principal admits it: the lamport fee is not added to microunits.
    ledger.set_limits(WALLET, "USDC", ledger.SpendLimits(per_tx_minor=150_000, daily_minor=10_000_000, per_destination_daily_minor=10_000_000))
    assert ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=150_000, destination=DESTINATION, proposal_id="p-cap-usdc", fee_minor=5_000, chain=SOLANA_MAINNET).ok
    # A SOL per-transaction ceiling below the fee refuses the whole reservation, and nothing is written.
    ledger.set_limits(WALLET, "SOL", ledger.SpendLimits(per_tx_minor=4_000, daily_minor=10_000_000, per_destination_daily_minor=10_000_000))
    refused = ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=10_000, destination=DESTINATION, proposal_id="p-cap-fee", fee_minor=5_000, chain=SOLANA_MAINNET)
    assert (refused.ok, refused.limit) == (False, "per_transaction") and _rows("p-cap-fee") == []


def test_a_native_transfer_keeps_one_row_and_the_quote_reads_holds_per_asset(ledger) -> None:
    from core.wallet import quotes

    ledger.reserve_spend(wallet_id=WALLET, asset="SOL", amount_minor=1_000_000, destination=DESTINATION, proposal_id="p-sol", fee_minor=5_000, chain=SOLANA_MAINNET)
    assert _rows("p-sol") == [("p-sol", "SOL", 1_000_000, 5_000, "reserved")]
    ledger.reserve_spend(wallet_id=WALLET, asset="USDC", amount_minor=150_000, destination=DESTINATION, proposal_id="p-token", fee_minor=5_000, chain=SOLANA_MAINNET)
    assert quotes._held_elsewhere(WALLET, SOLANA_MAINNET, "p-other", asset="SOL") == 1_005_000 + 5_000
    assert quotes._held_elsewhere(WALLET, SOLANA_MAINNET, "p-other", asset="USDC") == 150_000
    # the quoted proposal's own principal and fee companion are not "elsewhere"
    assert quotes._held_elsewhere(WALLET, SOLANA_MAINNET, "p-token", asset="SOL") == 1_005_000
    assert quotes._held_elsewhere(WALLET, SOLANA_MAINNET, "p-token", asset="USDC") == 0
