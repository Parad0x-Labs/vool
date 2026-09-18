"""Unit tests for the local on-chain x402 receipt verifier.

These tests drive :func:`core.x402.receipt_verifier.verify_payment_receipt` with
hand-built jsonParsed ``getTransaction`` FIXTURES via an injected RPC callable —
there is NO network access. They assert the fail-closed contract: a matching
USDC transfer confirms, and every doubtful case (erred tx, wrong mint, short
amount, missing tx, stub mode, RPC None) is rejected.
"""

from __future__ import annotations

from typing import Any

from core.x402.receipt_verifier import (
    USDC_MINT_DEVNET,
    USDC_MINT_MAINNET,
    verify_payment_receipt,
)

RECIPIENT = "9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5"
OTHER_MINT = "So11111111111111111111111111111111111111112"
TX_SIG = "5xPaymentTxSignatureBase58Fixture0000000000000000000000000000000"

# A facilitator escrow ATA (an SPL token ACCOUNT address) owned by the
# facilitator, plus the facilitator owner. This mirrors core.x402.client, which
# transfers the USDC into the escrow ATA (dest=escrow_ata), NOT the recipient
# wallet's own account — so the recipient WALLET's balance is unchanged.
ESCROW_ATA = "Escrow1111111111111111111111111111111111111"
FACILITATOR_OWNER = "Fac11111111111111111111111111111111111111111"
PAYER_ATA = "Payer111111111111111111111111111111111111111"


def _token_balance(
    owner: str, mint: str, atomic: int, *, account_index: int = 1
) -> dict[str, Any]:
    """A single jsonParsed pre/postTokenBalances entry."""
    return {
        "accountIndex": account_index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {
            "amount": str(atomic),
            "decimals": 6,
            "uiAmount": atomic / 1_000_000,
            "uiAmountString": str(atomic / 1_000_000),
        },
    }


def _get_transaction_result(
    *,
    err: Any = None,
    pre: list[dict[str, Any]] | None = None,
    post: list[dict[str, Any]] | None = None,
    account_keys: list[str] | None = None,
) -> dict[str, Any]:
    """A minimal jsonParsed getTransaction result with the meta we inspect.

    ``account_keys`` populates ``transaction.message.accountKeys`` (jsonParsed
    object form) so token-balance ``accountIndex`` values resolve to real token
    account addresses, the way the credited-account check needs.
    """
    keys = account_keys or []
    return {
        "slot": 123456789,
        "blockTime": 1_700_000_000,
        "meta": {
            "err": err,
            "preTokenBalances": pre or [],
            "postTokenBalances": post or [],
        },
        "transaction": {
            "signatures": [TX_SIG],
            "message": {
                "accountKeys": [
                    {"pubkey": k, "signer": False, "writable": True, "source": "transaction"}
                    for k in keys
                ],
            },
        },
    }


def _rpc_returning(result: Any):
    """Build an injected rpc_call that asserts the request shape, returns result."""
    captured: dict[str, Any] = {}

    def _call(method: str, params: list[Any]) -> Any:
        captured["method"] = method
        captured["params"] = params
        return result

    _call.captured = captured  # type: ignore[attr-defined]
    return _call


def test_confirms_matching_usdc_transfer() -> None:
    # Recipient had 1.0 USDC, now has 2.0 USDC: a clean +1.0 USDC transfer.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_000_000)],
            post=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 2_000_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is True
    )

    # The lookup is a finalized, jsonParsed getTransaction over the injected RPC.
    assert rpc.captured["method"] == "getTransaction"  # type: ignore[attr-defined]
    opts = rpc.captured["params"][1]  # type: ignore[attr-defined]
    assert opts["encoding"] == "jsonParsed"
    assert opts["commitment"] == "finalized"
    assert opts["maxSupportedTransactionVersion"] == 0


def test_confirms_when_recipient_token_account_created_by_transfer() -> None:
    # No pre-balance entry (ATA created by this tx); post shows the full amount.
    # mode="devnet" selects the devnet USDC mint, which the entry must carry.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[],
            post=[_token_balance(RECIPIENT, USDC_MINT_DEVNET, 500_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=0.5,
            mode="devnet",
            rpc_call=rpc,
        )
        is True
    )


def test_rejects_transaction_with_error() -> None:
    rpc = _rpc_returning(
        _get_transaction_result(
            err={"InstructionError": [0, {"Custom": 1}]},
            pre=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_000_000)],
            post=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 2_000_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is False
    )


def test_rejects_wrong_mint() -> None:
    # Balance increased, but for a different mint (e.g. wSOL), not USDC.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(RECIPIENT, OTHER_MINT, 1_000_000)],
            post=[_token_balance(RECIPIENT, OTHER_MINT, 2_000_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is False
    )


def test_rejects_short_amount() -> None:
    # Recipient received 0.4 USDC but 1.0 was claimed → fail closed.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_000_000)],
            post=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_400_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is False
    )


def test_rejects_balance_credited_to_other_owner() -> None:
    # The +1 USDC went to a different wallet, not the claimed recipient.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(OTHER_MINT[:32], USDC_MINT_MAINNET, 1_000_000)],
            post=[_token_balance(OTHER_MINT[:32], USDC_MINT_MAINNET, 2_000_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is False
    )


def test_rejects_missing_transaction() -> None:
    # getTransaction for an unknown signature returns JSON-RPC null result.
    rpc = _rpc_returning(None)

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=rpc,
        )
        is False
    )


def test_rejects_rpc_none_failure() -> None:
    # The shared _rpc_call returns None when every endpoint fails. Same path as
    # a missing tx — but make the intent explicit as its own case.
    def _failing(method: str, params: list[Any]) -> Any:
        return None

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=0.001,
            mode="mainnet",
            rpc_call=_failing,
        )
        is False
    )


def test_rejects_rpc_exception() -> None:
    def _boom(method: str, params: list[Any]) -> Any:
        raise OSError("network down")

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=0.001,
            mode="mainnet",
            rpc_call=_boom,
        )
        is False
    )


def test_rejects_stub_mode() -> None:
    # A perfectly valid on-chain transfer, but mode is "stub": fail closed
    # without even consulting the RPC.
    called = {"hit": False}

    def _rpc(method: str, params: list[Any]) -> Any:
        called["hit"] = True
        return _get_transaction_result(
            pre=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 0)],
            post=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_000_000)],
        )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="stub",
            rpc_call=_rpc,
        )
        is False
    )
    assert called["hit"] is False


def test_rejects_unknown_mode_and_blank_inputs() -> None:
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 0)],
            post=[_token_balance(RECIPIENT, USDC_MINT_MAINNET, 1_000_000)],
        )
    )
    # Unknown mode.
    assert (
        verify_payment_receipt(
            TX_SIG, recipient_wallet=RECIPIENT, amount_usdc=1.0, mode="testnet", rpc_call=rpc
        )
        is False
    )
    # Blank tx signature.
    assert (
        verify_payment_receipt(
            "", recipient_wallet=RECIPIENT, amount_usdc=1.0, mode="mainnet", rpc_call=rpc
        )
        is False
    )
    # Non-positive amount.
    assert (
        verify_payment_receipt(
            TX_SIG, recipient_wallet=RECIPIENT, amount_usdc=0.0, mode="mainnet", rpc_call=rpc
        )
        is False
    )


def test_rejects_malformed_meta_and_amount() -> None:
    # meta missing entirely.
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=_rpc_returning({"slot": 1}),
        )
        is False
    )
    # uiTokenAmount.amount is non-numeric garbage → contributes 0, no inflation.
    bad_post = [
        {
            "mint": USDC_MINT_MAINNET,
            "owner": RECIPIENT,
            "uiTokenAmount": {"amount": "not-a-number"},
        }
    ]
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=_rpc_returning(_get_transaction_result(pre=[], post=bad_post)),
        )
        is False
    )


def test_default_rpc_is_publicnode_only() -> None:
    # Guardrail: the verifier's default RPC must be the compliant publicnode
    # path, never api.mainnet-beta. We assert via the shared _rpc_call's
    # endpoint table rather than making a network call.
    from core.vool_wallet import _RPC_ENDPOINTS

    assert all("api.mainnet-beta.solana.com" not in url for url in _RPC_ENDPOINTS)
    assert any("publicnode.com" in url for url in _RPC_ENDPOINTS)


# ---------------------------------------------------------------------------
# Regression: the x402 client (core/x402/client.py) transfers USDC into the
# facilitator ESCROW ATA (dest=escrow_ata), NOT the recipient wallet's ATA. A
# genuine settlement therefore leaves the recipient WALLET's balance unchanged.
# The fixed verifier confirms the credit to the credited token account; the old
# recipient-wallet-owner check returned False on this same fixture.
# ---------------------------------------------------------------------------


def test_confirms_credit_to_escrow_ata_when_recipient_wallet_unchanged() -> None:
    # The +1 USDC lands in the facilitator escrow ATA (owned by the facilitator,
    # NOT the recipient wallet). accountIndex 0 -> escrow ATA, 1 -> payer ATA.
    keys = [ESCROW_ATA, PAYER_ATA]
    result = _get_transaction_result(
        account_keys=keys,
        pre=[
            _token_balance(FACILITATOR_OWNER, USDC_MINT_MAINNET, 1_000_000, account_index=0),
            _token_balance(RECIPIENT, USDC_MINT_MAINNET, 5_000_000, account_index=1),
        ],
        post=[
            _token_balance(FACILITATOR_OWNER, USDC_MINT_MAINNET, 2_000_000, account_index=0),
            _token_balance(RECIPIENT, USDC_MINT_MAINNET, 4_000_000, account_index=1),
        ],
    )

    # FIXED behavior: checking the credited escrow ATA confirms the +1 USDC.
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            credited_account=ESCROW_ATA,
            rpc_call=_rpc_returning(result),
        )
        is True
    )

    # PRE-FIX behavior would have failed: the recipient WALLET's own balance does
    # not increase on this tx (it actually drops, since RECIPIENT is the payer
    # side here), so the old owner-match returned False. The fallback path (no
    # credited_account) still returns False on the very same fixture — proving the
    # fix is what flips it to True.
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            rpc_call=_rpc_returning(result),
        )
        is False
    )


def test_rejects_credited_account_not_in_account_keys() -> None:
    # The escrow ATA was credited, but the caller asks about an account the tx
    # never touched → fail closed (cannot resolve it in accountKeys).
    keys = [ESCROW_ATA, PAYER_ATA]
    result = _get_transaction_result(
        account_keys=keys,
        pre=[_token_balance(FACILITATOR_OWNER, USDC_MINT_MAINNET, 0, account_index=0)],
        post=[_token_balance(FACILITATOR_OWNER, USDC_MINT_MAINNET, 1_000_000, account_index=0)],
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            credited_account="Unrelated11111111111111111111111111111111111",
            rpc_call=_rpc_returning(result),
        )
        is False
    )


def test_rejects_escrow_credit_with_wrong_mint_for_credited_account() -> None:
    # The credited escrow ATA increased, but in a non-USDC mint → fail closed.
    keys = [ESCROW_ATA]
    result = _get_transaction_result(
        account_keys=keys,
        pre=[_token_balance(FACILITATOR_OWNER, OTHER_MINT, 1_000_000, account_index=0)],
        post=[_token_balance(FACILITATOR_OWNER, OTHER_MINT, 2_000_000, account_index=0)],
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            credited_account=ESCROW_ATA,
            rpc_call=_rpc_returning(result),
        )
        is False
    )


def test_devnet_mode_checks_devnet_mint_not_mainnet() -> None:
    # A genuine devnet settlement credits the escrow ATA in the DEVNET USDC mint.
    # The verifier must select the devnet mint for mode="devnet"; checking the
    # mainnet mint (the prior hardcoded behavior) would see no matching entry.
    keys = [ESCROW_ATA]
    result = _get_transaction_result(
        account_keys=keys,
        pre=[_token_balance(FACILITATOR_OWNER, USDC_MINT_DEVNET, 0, account_index=0)],
        post=[_token_balance(FACILITATOR_OWNER, USDC_MINT_DEVNET, 1_000_000, account_index=0)],
    )

    # Devnet mint entry confirms under mode="devnet".
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="devnet",
            credited_account=ESCROW_ATA,
            rpc_call=_rpc_returning(result),
        )
        is True
    )

    # The SAME devnet-mint fixture must NOT confirm under mode="mainnet": the
    # mainnet mint does not match the devnet entry. (Pre-fix the verifier always
    # matched the mainnet mint regardless of mode, so a devnet settlement could
    # never confirm at all.)
    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=1.0,
            mode="mainnet",
            credited_account=ESCROW_ATA,
            rpc_call=_rpc_returning(result),
        )
        is False
    )


def test_devnet_direct_transfer_to_recipient_owner_uses_devnet_mint() -> None:
    # Owner-match fallback path (no credited_account) must also select the devnet
    # mint under mode="devnet" — a direct devnet USDC transfer into the
    # recipient's own ATA confirms; the mainnet mint never would.
    rpc = _rpc_returning(
        _get_transaction_result(
            pre=[_token_balance(RECIPIENT, USDC_MINT_DEVNET, 0)],
            post=[_token_balance(RECIPIENT, USDC_MINT_DEVNET, 750_000)],
        )
    )

    assert (
        verify_payment_receipt(
            TX_SIG,
            recipient_wallet=RECIPIENT,
            amount_usdc=0.75,
            mode="devnet",
            rpc_call=rpc,
        )
        is True
    )


# ---------------------------------------------------------------------------
# Ledger gate integration — release_escrow_to_helper opt-in verifier hook.
# These use the autouse runtime_storage_reset DB fixture from conftest (no net).
# ---------------------------------------------------------------------------

_REAL_HASH = "a" * 64  # a valid-looking 64-hex receipt hash (a stub-mode hash)


def _ledger_row_mode(receipt_id: str) -> str | None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT settlement_mode FROM compute_credit_ledger WHERE receipt_id = ? LIMIT 1",
            (receipt_id,),
        ).fetchone()
        return None if row is None else str(row["settlement_mode"])
    finally:
        conn.close()


def _seed_escrow(task_id: str, poster: str, amount: float) -> None:
    from core import credit_ledger

    assert credit_ledger.award_credits(poster, amount, "test_seed", receipt_id=f"seed:{task_id}")
    assert credit_ledger.escrow_credits_for_task(poster, task_id, amount, receipt_id=f"escrow:{task_id}")


def test_ledger_gate_default_off_is_backward_compatible() -> None:
    # No verifier passed: a claimed real receipt + mainnet hint stamps "mainnet"
    # exactly as today — behavior is unchanged for every existing caller.
    from core import credit_ledger

    task_id = "task-compat"
    poster = "poster-compat"
    helper = "helper-compat"
    _seed_escrow(task_id, poster, 1.0)

    release_receipt = f"rel:{task_id}:{helper}"
    assert credit_ledger.release_escrow_to_helper(
        task_id,
        helper,
        1.0,
        receipt_id=release_receipt,
        receipt_hash=_REAL_HASH,
        settlement_mode_hint="mainnet",
    )
    assert _ledger_row_mode(release_receipt) == "mainnet"


def test_ledger_gate_confirms_when_verifier_passes() -> None:
    from core import credit_ledger

    task_id = "task-ok"
    poster = "poster-ok"
    helper = "helper-ok"
    _seed_escrow(task_id, poster, 1.0)

    def _verifier(payment_tx, *, recipient_wallet, amount_usdc, mode) -> bool:
        # Confirm the gate forwarded the claimed settlement details through.
        assert payment_tx == "real-tx-sig"
        assert recipient_wallet == "helper-wallet"
        assert amount_usdc == 1.0
        assert mode == "mainnet"
        return True

    release_receipt = f"rel:{task_id}:{helper}"
    assert credit_ledger.release_escrow_to_helper(
        task_id,
        helper,
        1.0,
        receipt_id=release_receipt,
        receipt_hash=_REAL_HASH,
        settlement_mode_hint="mainnet",
        settlement_verifier=_verifier,
        payment_tx="real-tx-sig",
        payment_recipient_wallet="helper-wallet",
        payment_amount_usdc=1.0,
    )
    assert _ledger_row_mode(release_receipt) == "mainnet"


def test_ledger_gate_downgrades_to_simulated_when_verifier_rejects() -> None:
    # A caller self-claims a real receipt_hash + mainnet, but the on-chain
    # verifier cannot confirm it → the row must stay "simulated", closing the
    # reputation-inflation hole. The payout still happens (credits move).
    from core import credit_ledger

    task_id = "task-bad"
    poster = "poster-bad"
    helper = "helper-bad"
    _seed_escrow(task_id, poster, 1.0)

    def _verifier(payment_tx, *, recipient_wallet, amount_usdc, mode) -> bool:
        return False

    release_receipt = f"rel:{task_id}:{helper}"
    assert credit_ledger.release_escrow_to_helper(
        task_id,
        helper,
        1.0,
        receipt_id=release_receipt,
        receipt_hash=_REAL_HASH,
        settlement_mode_hint="mainnet",
        settlement_verifier=_verifier,
        payment_tx="self-claimed-tx",
        payment_recipient_wallet="helper-wallet",
        payment_amount_usdc=1.0,
    )
    assert _ledger_row_mode(release_receipt) == "simulated"
    # Helper was still paid even though the settlement was downgraded.
    assert credit_ledger.get_credit_balance(helper) == 1.0


def test_ledger_gate_downgrades_when_verifier_raises() -> None:
    from core import credit_ledger

    task_id = "task-raise"
    poster = "poster-raise"
    helper = "helper-raise"
    _seed_escrow(task_id, poster, 1.0)

    def _verifier(payment_tx, *, recipient_wallet, amount_usdc, mode) -> bool:
        raise RuntimeError("verifier blew up")

    release_receipt = f"rel:{task_id}:{helper}"
    assert credit_ledger.release_escrow_to_helper(
        task_id,
        helper,
        1.0,
        receipt_id=release_receipt,
        receipt_hash=_REAL_HASH,
        settlement_mode_hint="mainnet",
        settlement_verifier=_verifier,
        payment_tx="tx",
    )
    assert _ledger_row_mode(release_receipt) == "simulated"


def test_ledger_gate_skips_verifier_for_simulated_payout() -> None:
    # No real receipt hash → mode is already "simulated"; the verifier must not
    # be consulted at all (nothing to confirm).
    from core import credit_ledger

    task_id = "task-sim"
    poster = "poster-sim"
    helper = "helper-sim"
    _seed_escrow(task_id, poster, 1.0)

    called = {"hit": False}

    def _verifier(payment_tx, *, recipient_wallet, amount_usdc, mode) -> bool:
        called["hit"] = True
        return True

    release_receipt = f"rel:{task_id}:{helper}"
    assert credit_ledger.release_escrow_to_helper(
        task_id,
        helper,
        1.0,
        receipt_id=release_receipt,
        receipt_hash="stub-not-real",
        settlement_verifier=_verifier,
    )
    assert _ledger_row_mode(release_receipt) == "simulated"
    assert called["hit"] is False
