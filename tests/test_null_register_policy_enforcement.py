"""Enforcement tests: the spend policy (freeze + daily/weekly caps) still gates
execute_registration and is checked before the OS-consent prompt.

The sign+broadcast tail of .null registration is a retired money surface: an allowed policy
now ends in a typed, receipt-backed refusal from `sign_and_broadcast_registration` -- nothing
is signed, nothing is broadcast, nothing is recorded. The former
`test_record_failure_does_not_fail_a_successful_broadcast` is deleted: its entire subject
(a successful broadcast whose ledger record fails) no longer exists.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core.null_register_execute import (
    NULL_REGISTRAR_MAINNET,
    SpendGate,
    execute_registration,
    sign_and_broadcast_registration,
)
from core.null_registrar import RegisterPlan
from core.wallet.errors import WalletFault
from core.wallet_spend_policy import SpendLedger, SpendPolicy

_PUBKEY = "28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX"
LEGACY = "wallet_legacy_surface_retired"


def _plan(total_lamports: int = 11_200_000):
    fee = total_lamports
    return RegisterPlan(
        name="mysite.null",
        program_id=NULL_REGISTRAR_MAINNET,
        domain_pda="",
        config_pda="",
        owner_cap_pda="",
        owner=_PUBKEY,
        treasury="",
        sol_fee_lamports=fee,
        rent_lamports=0,
        owner_cap_rent_lamports=0,
        instruction_data_hex="",
        accounts=[],
    )


def _wallet():
    return mock.Mock(pubkey=_PUBKEY, sign_transaction=mock.Mock(return_value=b"\x00" * 64))


def _passing_gate():
    return SpendGate(allow_spend=True, approve=True, wallet_present=True, max_spend_lamports=30_000_000)


def _run(policy, ledger, *, consent, wallet=None, recorder=None, rpc_calls=None):
    wallet = wallet or _wallet()
    calls = rpc_calls if rpc_calls is not None else []

    def _rpc(method, params):
        calls.append(method)
        return "SIGRESULT" if method == "sendTransaction" else {}

    with mock.patch("core.null_register_execute._is_available", return_value=True), mock.patch(
        "core.null_register_execute._plan_with_costs", return_value=(_plan(), None)
    ), mock.patch("core.null_register_execute._build_register_message", return_value=b"msg"):
        outcome = execute_registration(
            "mysite.null",
            gate=_passing_gate(),
            wallet=wallet,
            consent=consent,
            rpc=_rpc,
            blockhash_fn=lambda: "blockhash11111111111111111111111111111111111",
            policy_loader=lambda _w: (policy, ledger),
            recorder=recorder or (lambda *a, **k: None),
            now_fn=lambda: 1_000_000.0,
        )
    return outcome, wallet


def test_frozen_wallet_blocks_registration_before_consent() -> None:
    consent = mock.Mock(return_value=True)
    outcome, wallet = _run(SpendPolicy(frozen=True), SpendLedger(), consent=consent)
    assert outcome.status == "blocked"
    assert "frozen" in outcome.message.lower()
    consent.assert_not_called()  # never even prompted the human
    wallet.sign_transaction.assert_not_called()  # nothing signed


def test_daily_cap_blocks_registration() -> None:
    consent = mock.Mock(return_value=True)
    ledger = SpendLedger()
    ledger.record(1_000_000.0 - 100, 9_000_000)  # already spent today
    # daily cap 15M, already 9M, this tx 11.2M -> 20.2M > 15M -> blocked
    policy = SpendPolicy(daily_cap_lamports=15_000_000)
    outcome, wallet = _run(policy, ledger, consent=consent)
    assert outcome.status == "blocked"
    assert "daily" in outcome.message.lower()
    consent.assert_not_called()
    wallet.sign_transaction.assert_not_called()


def test_per_tx_cap_in_policy_blocks() -> None:
    consent = mock.Mock(return_value=True)
    policy = SpendPolicy(per_tx_cap_lamports=5_000_000)  # tx is 11.2M > 5M
    outcome, wallet = _run(policy, SpendLedger(), consent=consent)
    assert outcome.status == "blocked"
    consent.assert_not_called()
    wallet.sign_transaction.assert_not_called()


def test_tampered_policy_fails_closed_as_blocked() -> None:
    from core.wallet_spend_policy_store import PolicyIntegrityError

    def _loader_raises(_w):
        raise PolicyIntegrityError("HMAC mismatch")

    consent = mock.Mock(return_value=True)
    wallet = _wallet()
    with mock.patch("core.null_register_execute._is_available", return_value=True), mock.patch(
        "core.null_register_execute._plan_with_costs", return_value=(_plan(), None)
    ):
        outcome = execute_registration(
            "mysite.null",
            gate=_passing_gate(),
            wallet=wallet,
            consent=consent,
            policy_loader=_loader_raises,
            now_fn=lambda: 1_000_000.0,
        )
    assert outcome.status == "blocked"
    assert "could not be verified" in outcome.message.lower()
    consent.assert_not_called()
    wallet.sign_transaction.assert_not_called()


def test_allowed_policy_ends_in_a_typed_refusal_not_a_broadcast() -> None:
    consent = mock.Mock(return_value=True)
    recorder = mock.Mock()
    rpc_calls: list[str] = []
    policy = SpendPolicy(daily_cap_lamports=100_000_000)
    outcome, wallet = _run(policy, SpendLedger(), consent=consent, recorder=recorder, rpc_calls=rpc_calls)
    assert outcome.status == "refused"
    assert outcome.message.startswith(LEGACY + ":")
    assert not outcome.signature
    wallet.sign_transaction.assert_not_called()  # nothing signed
    assert "sendTransaction" not in rpc_calls  # nothing broadcast
    recorder.assert_not_called()  # nothing to record


def test_sign_and_broadcast_tail_refuses_typed_and_receipt_backed() -> None:
    from core.faults.recorder import list_faults

    wallet = _wallet()
    with pytest.raises(WalletFault) as exc:
        sign_and_broadcast_registration(_plan(), wallet, blockhash_fn=lambda: "bh", rpc=lambda *a: "SIG")
    assert exc.value.code == LEGACY
    assert exc.value.fault_id.startswith("fault-")
    assert exc.value.context["surface"] == "null_register_execute.sign_and_broadcast_registration"
    wallet.sign_transaction.assert_not_called()
    receipts = [r for r in list_faults(code=LEGACY, limit=50) if r.fault_id == exc.value.fault_id]
    assert receipts and receipts[0].context.get("surface") == "null_register_execute.sign_and_broadcast_registration"


def test_the_retired_tail_never_prompts_the_operator_for_consent() -> None:
    # A consent dialog for a spend this path cannot perform would be theatre: the typed refusal
    # lands BEFORE the OS prompt, with the policy gate still checked first.
    consent = mock.Mock(side_effect=AssertionError("OS consent must not be prompted for a retired tail"))
    policy = SpendPolicy(daily_cap_lamports=100_000_000)
    outcome, wallet = _run(policy, SpendLedger(), consent=consent)
    assert outcome.status == "refused"
    assert outcome.message.startswith(LEGACY + ":")
    consent.assert_not_called()
    wallet.sign_transaction.assert_not_called()
