"""Admission refusals settle at their proven zero; every ambiguous outcome keeps its hold.

The starvation this file pins: every 402 the prepaid proxy answered minted a bounded liability
that nothing ever resolved -- reconcile only freed dead-instance reservations and expired quote
windows, so a refusal storm held the whole day's envelope at maxima while almost nothing was
charged (measured live 2026-09-15: ~200 unknown ``inference_expense`` holds ≈ 2.98 USDC against
≈ $0.05 of real charges, starving the 3 USDC daily envelope).

The repair's law: only the provider's operation-bound ADMISSION refusal -- the typed answer to
these exact bytes, one of the codes whose refused-before-service basis the descriptor documents
(``core.usepod.money_law.ADMISSION_REFUSAL_NO_CHARGE_CODES``) -- settles at zero. An HTTP status
alone is not evidence, a balance delta is not evidence, and an ambiguous outcome (timeout after
send, interrupted stream, upstream failure, a dead claimant) keeps its maximum held until real
evidence resolves it. All cases here run on the real money law in an isolated home: no provider,
no chain, no wallet.
"""
from __future__ import annotations

import time
import uuid

import pytest

from tests.usepod.test_usepod_money_law import (
    _fingerprint,
    _liability,
    _mint_grant,
    _observe_balance,
    _settlement,
)

STORM_TURNS = 250
ENVELOPE_ATOMIC = 3_000_000
PER_OPERATION_ATOMIC = 15_000


def _envelope_grant():
    """One account-wide 3 USDC provider budget bound to the bound credential's account -- the
    same grant shape as the operator's daily envelope (no model list, no operation count)."""
    import time as _time

    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator_token = grant_operator_budget_authority(note="synthetic test funds: admission-refusal envelope")
    return grant_money_authority(
        operator_token,
        MoneyGrantSpec(
            kind="provider_budget",
            operation_kinds=("inference_prepaid",),
            provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=ENVELOPE_ATOMIC,
            per_operation_max_atomic=PER_OPERATION_ATOMIC,
            expires_epoch=_time.time() + 3600.0,
            credit_liquidity="required",
            provider_account=_fingerprint(),
            approval_ref="synthetic:admission-refusal",
            note="synthetic test funds: admission-refusal envelope",
        ),
    ).grant_id


@pytest.fixture
def law(tmp_path, monkeypatch):
    """The money-law fixture of test_usepod_money_law, reused verbatim (isolated store, strict
    service with a funded token, fresh authority)."""
    import os

    from core import effect_budget, runtime_paths
    from storage.db import configure_default_db_path

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    effect_budget.reset_effect_budget_process_state()
    token = str(uuid.uuid4())
    from tests.usepod.strict_usepod_service import Listing, StrictUsePodService

    service = StrictUsePodService(tokens={token: 80_000_000}, models={"meridian-synth-chat": []}).start()
    from core.usepod.money_law import EffectBudgetMonetaryAuthority

    authority = EffectBudgetMonetaryAuthority()
    try:
        yield authority, service, token
    finally:
        service.stop()
        effect_budget.reset_effect_budget_process_state()
        configure_default_db_path(None)


def _refused_turn(authority, name: str, *, code: str, outcome: str = "failed_after_send", max_atomic: int = PER_OPERATION_ATOMIC):
    """One refused turn exactly as the adapter runs it: reserve -> claim -> dispatch record ->
    the typed unknown the adapter retains. Returns the reservation's liability operation id."""
    from dataclasses import replace

    liability = _liability(name, max_atomic=max_atomic)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(reservation)
    evidence = replace(_settlement(liability, upper=None, outcome=outcome), detail=code)
    authority.retain_unknown(reservation, evidence)
    return liability.operation_id


def _headroom(grant_id: str) -> int:
    from core.effect_budget_money import grant_headroom

    head = grant_headroom(grant_id)
    assert head is not None
    return int(head["principal_left_atomic"])


# --- THE starvation, and its repair -------------------------------------------------------------------


def test_a_refusal_storm_cannot_starve_the_envelope(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()

    # Refused turns until the envelope can hold no more: 200 x 15,000 = the whole 3 USDC
    # envelope, and the 201st call is refused by the money law itself. That refusal -- the
    # demonstrated starvation -- is the pre-repair state this test must see before recovering.
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    refused_turns = 0
    starved = None
    for index in range(STORM_TURNS):
        try:
            _refused_turn(authority, f"storm-402-{index}", code="payment_or_balance_required")
        except MonetaryAuthorityRefusedError as exc:
            starved = exc
            break
        refused_turns += 1
    assert refused_turns == ENVELOPE_ATOMIC // PER_OPERATION_ATOMIC, refused_turns
    assert starved is not None and str(getattr(starved, "code", "") or "").startswith("MONEY_AUTHORITY_EXHAUSTED"), starved
    assert _headroom(grant_id) == 0, "the storm consumed the envelope before the repair runs"

    from core.effect_budget_money import reconcile_money_liabilities

    changes = reconcile_money_liabilities(force=True)
    settled = [change for change in changes if change.get("evidence_kind") == "provider_refusal_record"]
    assert len(settled) == refused_turns, (len(settled), changes[:3])
    assert all(str(change.get("to") or "") == "settled" for change in settled)
    assert _headroom(grant_id) == ENVELOPE_ATOMIC, "every admission-refusal hold returns its maximum"

    # The call that was refused by starvation fits again, settles on its usage, and counts.
    liability = _liability("after-storm-success", max_atomic=PER_OPERATION_ATOMIC)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _settlement(liability, upper=12_345))
    # Usage the provider reported but never exactly billed keeps the CAP held (the law's own
    # bounded-settlement conservatism), so the served call consumes its full per-operation
    # maximum -- the refused turns' maxima, in contrast, were proven zero and freed.
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC


def test_the_recovery_reruns_idempotently_and_reports_each_settlement(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()
    _refused_turn(authority, "recover-402-a", code="payment_or_balance_required")
    _refused_turn(authority, "recover-402-b", code="token_rejected")

    from core.usepod.money_law import recover_admission_refusal_holds

    first = recover_admission_refusal_holds()
    assert {change.get("refusal_code") for change in first} == {"payment_or_balance_required", "token_rejected"}
    assert _headroom(grant_id) == ENVELOPE_ATOMIC, "both admission-refusal holds returned their maxima"

    second = recover_admission_refusal_holds()
    assert second == [], "a replayed recovery finds nothing new to settle"
    assert _headroom(grant_id) == ENVELOPE_ATOMIC


# --- what may NOT settle ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,code",
    [
        ("unknown", "response_body_interrupted"),      # response lost mid-read
        ("unknown", "stream_interrupted"),             # stream died mid-answer
        ("unknown", "read_timeout_after_send"),        # ambiguous timeout AFTER the request left
        ("failed_after_send", "upstream_failure"),     # 5xx: the upstream state is unknown
        ("failed_after_send", "throttled"),            # 429: not in the documented admission set
    ],
)
def test_ambiguous_outcomes_keep_their_maximum_held(law, outcome, code) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()
    name = f"held-{outcome}-{code}-{uuid.uuid4().hex[:8]}"
    _refused_turn(authority, name, code=code, outcome=outcome)

    from core.effect_budget_money import reconcile_money_liabilities

    changes = [change for change in reconcile_money_liabilities(force=True) if change.get("liability_id")]
    assert not any(str(change.get("evidence_kind") or "") == "provider_refusal_record" for change in changes), changes
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC, "the ambiguous hold keeps its maximum"

    from core.effect_budget_money import liability_for_operation

    row = liability_for_operation(name)
    assert row is not None and str(row["state"]) == "unknown", row


def test_a_dead_claimants_unknown_is_not_an_admission_refusal(law) -> None:
    """The reconciliation path itself mints unknowns ('the payment may have gone out'); the
    recovery must not mistake those recorded reasons for the adapter's admission refusals."""
    from core.usepod.money_law import EffectBudgetMonetaryAuthority

    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()
    liability = _liability("dead-claimant-hold", max_atomic=PER_OPERATION_ATOMIC)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(reservation)
    from core.effect_budget_money import record_unknown

    record_unknown(
        str(reservation.reservation_id),
        EffectBudgetMonetaryAuthority._claim_token(reservation),
        reason="reconciled: dispatch claimant died; the payment may have gone out",
    )

    from core.usepod.money_law import admission_refusal_code_of_unknown_reason, recover_admission_refusal_holds

    assert admission_refusal_code_of_unknown_reason("reconciled: dispatch claimant died; the payment may have gone out") == ""
    assert recover_admission_refusal_holds() == []
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC


# --- the evidence itself is bounded -------------------------------------------------------------------


def _refusal_evidence(operation_id: str, **overrides):
    from core.effect_budget_money import (
        EVIDENCE_PROVIDER_REFUSAL_RECORD,
        FLOW_INFERENCE_EXPENSE,
        FLOW_PROVIDER_CREDIT_DEBIT,
        SettlementEvidence,
    )

    fields = {
        "evidence_kind": EVIDENCE_PROVIDER_REFUSAL_RECORD,
        "evidence_id": operation_id,
        "source": "provider",
        "actuals": {FLOW_INFERENCE_EXPENSE: 0, FLOW_PROVIDER_CREDIT_DEBIT: 0},
        "public_detail": {"refusal_code": "payment_or_balance_required", "refusal_status": "402", "transport_mode": "prepaid_token"},
    }
    fields.update(overrides)
    return SettlementEvidence(**fields)


def test_the_refusal_record_proves_zero_and_nothing_else(law) -> None:
    from core.effect_budget import EffectBudgetRefusedError
    from core.effect_budget_money import FLOW_INFERENCE_EXPENSE, liability_for_operation, settle_liability

    authority, service, token = law
    _observe_balance(service, token)
    _envelope_grant()
    operation_id = _refused_turn(authority, "evidence-shape-402", code="payment_or_balance_required")
    row = liability_for_operation(operation_id)
    liability_id = str(row["liability_id"])

    with pytest.raises(EffectBudgetRefusedError, match="zero"):
        settle_liability(liability_id, _refusal_evidence(operation_id, actuals={FLOW_INFERENCE_EXPENSE: 5, "provider_credit_debit": 0}))
    with pytest.raises(EffectBudgetRefusedError, match="refusal code"):
        settle_liability(liability_id, _refusal_evidence(operation_id, public_detail={"refusal_code": "", "refusal_status": "402"}))
    with pytest.raises(EffectBudgetRefusedError, match="refusal code"):
        settle_liability(liability_id, _refusal_evidence(operation_id, public_detail={"refusal_code": "payment_or_balance_required", "refusal_status": "later"}))
    with pytest.raises(EffectBudgetRefusedError, match="provider source"):
        settle_liability(liability_id, _refusal_evidence(operation_id, source="mechanical"))
    # The liability is untouched by every refused shape above.
    assert str(liability_for_operation(operation_id)["state"]) == "unknown"

    result = settle_liability(liability_id, _refusal_evidence(operation_id))
    assert result["state"] == "settled" and result["flows"], result


def test_an_admission_refusal_never_applies_to_the_x402_lane(law) -> None:
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority, service, token = law
    _observe_balance(service, token)
    _envelope_grant()
    x402_reservation = type(
        "_R",
        (),
        {"reservation_id": "mli:none", "claim_token": "", "liability": _liability("x402-op", transport="x402")},
    )()
    with pytest.raises(MonetaryAuthorityRefusedError):
        # The x402 lane pays before dispatch; a refusal after payment is the paid-retry path.
        authority.settle_admission_refusal(x402_reservation, refusal_code="payment_or_balance_required", http_status=402)


def test_an_undocumented_code_is_refused_by_the_authority(law) -> None:
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority, service, token = law
    _observe_balance(service, token)
    _envelope_grant()
    liability = _liability("undocumented-code", max_atomic=PER_OPERATION_ATOMIC)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(reservation)
    with pytest.raises(MonetaryAuthorityRefusedError):
        authority.settle_admission_refusal(reservation, refusal_code="throttled", http_status=429)


# --- ordering: one settlement wins, the loser conflicts, replays are no-ops ---------------------------


def test_duplicate_late_and_reordered_evidence_settles_once(law) -> None:
    from core.effect_budget import EffectBudgetRefusedError
    from core.effect_budget_money import liability_for_operation, settle_liability

    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()

    # The refusal settles first (the only real ordering: the proxy answered before anything else could).
    operation_id = _refused_turn(authority, "order-402", code="payment_or_balance_required")
    liability_id = str(liability_for_operation(operation_id)["liability_id"])

    first = settle_liability(liability_id, _refusal_evidence(operation_id))
    assert first["state"] == "settled"

    replay = settle_liability(liability_id, _refusal_evidence(operation_id))
    assert replay["idempotent"] is True and replay["state"] == "settled"

    # A LATE receipt claiming a real charge conflicts with the exact zero and changes nothing.
    with pytest.raises(EffectBudgetRefusedError):
        settle_liability(liability_id, _refusal_evidence(operation_id, evidence_id=f"{operation_id}-late", public_detail={"refusal_code": "payment_or_balance_required", "refusal_status": "402", "transport_mode": "prepaid_token"}))
    row = liability_for_operation(operation_id)
    assert str(row["state"]) == "settled"

    # Reordered on a SECOND liability: a completed usage settlement cannot be revised by a
    # refusal record afterwards -- the admission refusal belongs to the refusal moment only.
    served = _liability("order-served", max_atomic=PER_OPERATION_ATOMIC)
    served_reservation = authority.reserve(served)
    authority.claim(served_reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(served_reservation)
    authority.settle(served_reservation, _settlement(served, upper=9_000))
    served_id = str(liability_for_operation(served.operation_id)["liability_id"])
    with pytest.raises(EffectBudgetRefusedError):
        settle_liability(served_id, _refusal_evidence(served.operation_id))

    # Grant truth: the refused operation freed its maximum; the served one keeps its CAP held
    # (bounded settlement consumes the maximum until exact evidence closes it).
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC


# --- restart ------------------------------------------------------------------------------------------


def test_the_settled_zero_and_the_held_unknown_survive_a_restart(law, tmp_path) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()
    settled_op = _refused_turn(authority, "restart-402", code="payment_or_balance_required")
    held_op = _refused_turn(authority, "restart-timeout", code="read_timeout_after_send", outcome="unknown")

    from core import effect_budget
    from core.effect_budget_money import (
        liabilities,
        liability_for_operation,
        reconcile_money_liabilities,
        reset_money_process_state,
    )

    reconcile_money_liabilities(force=True)
    assert str(liability_for_operation(settled_op)["state"]) == "settled"
    assert str(liability_for_operation(held_op)["state"]) == "unknown"
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC

    # A new process on the same store: the per-process caches are gone, the durable truth is not.
    reset_money_process_state()
    effect_budget.reset_effect_budget_process_state()

    changes = reconcile_money_liabilities(force=True)
    assert not any(change.get("liability_id") == str(liability_for_operation(settled_op)["liability_id"]) for change in changes), changes
    assert str(liability_for_operation(settled_op)["state"]) == "settled"
    assert str(liability_for_operation(held_op)["state"]) == "unknown"
    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC
    assert len([row for row in liabilities(state="unknown") if row.get("operation_id") == held_op]) == 1


def test_charged_calls_still_count_after_zero_settled_refusals(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    grant_id = _envelope_grant()
    for index in range(3):
        _refused_turn(authority, f"mixed-402-{index}", code="payment_or_balance_required")
    from core.effect_budget_money import reconcile_money_liabilities

    reconcile_money_liabilities(force=True)

    charged = _liability("mixed-charged", max_atomic=PER_OPERATION_ATOMIC)
    charged_reservation = authority.reserve(charged)
    authority.claim(charged_reservation, executor="usepod_prepaid_transport")
    authority.mark_dispatched(charged_reservation)
    authority.settle(charged_reservation, _settlement(charged, upper=12_345))

    assert _headroom(grant_id) == ENVELOPE_ATOMIC - PER_OPERATION_ATOMIC
    # The envelope still refuses a call that does not fit: caps did not widen.
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as refused:
        too_big = _liability("mixed-too-big", max_atomic=ENVELOPE_ATOMIC)
        authority.reserve(too_big)
    assert str(refused.value.code).startswith("MONEY_AUTHORITY_EXHAUSTED"), refused.value
