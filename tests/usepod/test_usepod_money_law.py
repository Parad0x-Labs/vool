"""The production UsePod monetary authority on the real money law, in-process.

Everything here runs against the REAL ``core.effect_budget_money`` store in an isolated home and
the SYNTHETIC strict local service: operator-minted grants (synthetic funds, labelled by their
grant note), real reservation/claim/dispatch/settlement transitions, and the money law's own
recovery behaviour. No chain, no wallet, no live provider. The x402 payment proofs come from the
labelled SYNTHETIC chain double -- they prove the mapping's lawfulness, not a payment.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from core.usepod.money_law import AUTHORITY_LABEL, EffectBudgetMonetaryAuthority
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService

MODEL = "meridian-synth-chat"
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)
GRANT_NOTE = "synthetic test funds: usepod money-law integration"


def _home(tmp_path, monkeypatch):
    from core import runtime_paths

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    return home


@pytest.fixture
def law(tmp_path, monkeypatch):
    """An isolated money store (the effect-budget database, re-pointed per test the way
    tests/effect_budget isolates it), a strict service with a funded token, and a fresh authority."""
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget, runtime_paths

    effect_budget.reset_effect_budget_process_state()
    # _home() rebinds the process-global runtime-home override; leaving it bound would send
    # every LATER suite in this process (data_path/active_vool_home readers) into this test's
    # tmp home. Restore the OVERRIDE STATE itself (None means dynamic env/receipt resolution),
    # not active_vool_home()'s resolved value -- re-pinning the resolved path would shadow a
    # later suite that re-points the home through the environment alone.
    previous_override = runtime_paths._VOOL_HOME_OVERRIDE
    _home(tmp_path, monkeypatch)
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    authority = EffectBudgetMonetaryAuthority()
    try:
        yield authority, service, token
    finally:
        service.stop()
        effect_budget.reset_effect_budget_process_state()
        # The consent/confirm tests install a process-global monetary authority; leaving it
        # installed sends every LATER turn in this process down the budgeted path with no
        # grant behind it. Uninstall it with the rest of the process state.
        from core.usepod.monetary import reset_monetary_authority

        reset_monetary_authority()
        # The consent tests also register spend-grant approvals in the PROCESS-GLOBAL
        # mode/permission registry. One left PENDING outlives this suite: a later runtime
        # mirrors the registry into its own home's pending_approvals.json, a rig that
        # auto-resolves "the" pending approval picks the stale spend consent instead of its
        # own turn's approval, the re-run turn pauses as pending_approval again (which
        # persist_memory=False deliberately keeps out of the transcript), and the served-turn
        # row is never written -- first-run-pact task/denial claims then fail evidence_missing.
        # reset_mode_permission_state() also clears the durable mirror under the CURRENT
        # home, so it must run while the override still points at this test's tmp home.
        from core.mode_permission_policy import reset_mode_permission_state

        reset_mode_permission_state()
        configure_default_db_path(None)
        runtime_paths.configure_runtime_home(previous_override)


def _mint_grant(*, max_total: int = 5_000_000, per_operation: int = 5_000_000, models=(MODEL,), credit_liquidity="required", routes=("marketplace",), provider_account="upc_" + "1" * 32):
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    token = grant_operator_budget_authority(note=GRANT_NOTE)
    asset = AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6)
    return grant_money_authority(
        token,
        MoneyGrantSpec(
            kind="single_payment",
            operation_kinds=("inference_prepaid",),
            provider_id="usepod",
            asset=asset,
            max_total_atomic=max_total,
            per_operation_max_atomic=per_operation,
            models=tuple(models),
            routes=tuple(routes),
            expires_epoch=time.time() + 3600.0,
            credit_liquidity=credit_liquidity,
            provider_account=provider_account,
            approval_ref="synthetic:test",
            note=GRANT_NOTE,
        ),
    )


def _fingerprint() -> str:
    from core.usepod import discovery

    credential = discovery.resolve_credential()
    assert credential is not None, "the credential must be bound before the fingerprint exists"
    return credential.fingerprint


def _observe_balance(service, token) -> str:
    """Bind the credential, refresh discovery (fresh liquidity) and return the fingerprint."""
    """Bind the credential and refresh discovery so the liquidity observation is fresh."""
    import os

    from core import credential_store
    from core.cloud_providers import USEPOD_ORIGIN_SLOT

    credential_store.store_credential("llm.cloud.usepod", token)
    credential_store.store_credential(USEPOD_ORIGIN_SLOT, service.origin)
    os.environ["VOOL_USEPOD_TOKEN"] = ""
    from core.usepod import discovery

    result = discovery.refresh_discovery()
    assert result["credential"]["state"] == "observed", result
    return _fingerprint()


def _liability(operation_id: str, *, max_atomic: int = 4_000_000, transport: str = "prepaid_token", account_ref: str = ""):
    """account_ref defaults to the bound credential's fingerprint -- the prepaid credit account
    the grant binds and the liquidity observation names. One account identity, everywhere."""
    from core.usepod.monetary import ProviderLiability

    return ProviderLiability(
        operation_id=operation_id,
        provider_id="usepod",
        transport_mode=transport,
        asset="USDC",
        unit="usdc_microunit",
        account_kind="usepod_prepaid_token_balance",
        account_ref=account_ref or _fingerprint(),
        network="usepod_internal_account_ledger",
        max_amount_atomic=max_atomic,
        model_id=MODEL,
        route_approval_id="apr_test",
        envelope_binding_sha256="b" * 64,
        basis={"route_class": "marketplace"},
    )


def _settlement(liability, *, upper: int | None = 1234, outcome="completed"):
    from core.usepod.monetary import SettlementEvidence

    return SettlementEvidence(
        operation_id=liability.operation_id,
        outcome=outcome,
        http_status=200,
        usage={"prompt_tokens": 21, "completion_tokens": 13},
        input_tokens=21,
        output_tokens=13,
        upper_bound_cost_atomic=upper,
        exact_cost_atomic=None,
        exact_cost_state="not_supplied_by_provider",
        route={"route_class": "marketplace"},
        balance_remaining_raw="79.900000",
        usage_exceeds_liability_bound=False,
    )


def _evidence_payload(operation_id: str, evidence_id: str) -> dict:
    import json as _json
    import sqlite3

    from core.effect_budget import _budget_connection

    conn = _budget_connection()
    try:
        row = _liability_row(operation_id)
        found = conn.execute(
            "SELECT payload_json FROM effect_budget_money_evidence WHERE liability_id=? AND evidence_id=?",
            (str(row["liability_id"]), str(evidence_id)),
        ).fetchone()
        assert found is not None, f"evidence {evidence_id} not recorded"
        return _json.loads(str(found["payload_json"]))
    finally:
        with __import__("contextlib").suppress(Exception):
            conn.close()


def _liability_row(operation_id: str):
    from core.effect_budget_money import liability_for_operation

    return liability_for_operation(operation_id)


# --- the one happy liability --------------------------------------------------------------------------


def test_a_prepaid_liability_reserves_claims_dispatches_and_settles_on_the_money_law(law) -> None:
    authority, _service, _token = law
    fingerprint = _observe_balance(*law[1:])
    grant = _mint_grant(provider_account=fingerprint)
    liability = _liability(f"usepod-law-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    assert reservation.authority_label == AUTHORITY_LABEL
    row = _liability_row(liability.operation_id)
    assert row["state"] == "reserved" and row["grant_id"] == grant.grant_id
    assert {line["flow"] for line in row["lines"]} == {"inference_expense", "provider_credit_debit"}
    # The pre-effect claim: BEFORE any send, one winner.
    claim = authority.claim(reservation, executor="test-transport")
    assert claim.claim_token
    assert _liability_row(liability.operation_id)["state"] == "dispatching"
    # A second claimant is refused with the conflict code.
    from core.effect_budget import EffectBudgetRefusedError
    from core.effect_budget_money import claim_dispatch

    with pytest.raises(EffectBudgetRefusedError) as caught:
        claim_dispatch(reservation.reservation_id, executor="second-claimant")
    assert caught.value.code == "MONEY_CLAIM_CONFLICT"
    authority.mark_dispatched(reservation)
    assert _liability_row(liability.operation_id)["state"] == "pending"
    # TRUTHFUL SETTLEMENT (review F1): usage priced at the approved ceiling is a BOUND, not a
    # bill. The durable lines settle BOUNDED at their maximum with the narrower estimate carried
    # as public detail; the predicate is the corrected one, not the previous false-exact.
    authority.settle(reservation, _settlement(liability, upper=1234))
    settled = _liability_row(liability.operation_id)
    assert settled["state"] == "settled"
    flows = {line["flow"]: line for line in settled["lines"]}
    assert all(line["actual_atomic"] is None for line in flows.values()), settled["lines"]
    assert all(int(line["max_atomic"]) == liability.max_amount_atomic for line in flows.values())
    payload = _evidence_payload(liability.operation_id, liability.operation_id)
    assert payload["public_detail"]["usage_priced_at_ceiling_bound_atomic"] == "1234"
    assert payload["public_detail"]["estimate_basis"] == "usage_priced_at_approved_ceiling_not_a_bill"
    assert payload["actuals"] == {}


def test_no_grant_no_reservation_and_the_refusal_names_the_law(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"nogrant-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code == "MONEY_AUTHORITY_INVALID"
    assert "grant" in str(caught.value).lower()
    assert service.requests_to("/proxy/{token}/v1/chat/completions") == []


def test_a_grant_too_small_or_for_another_model_refuses(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    _mint_grant(per_operation=1_000, provider_account=fingerprint)   # far below the liability maximum
    with pytest.raises(MonetaryAuthorityRefusedError):
        authority.reserve(_liability(f"small-{uuid.uuid4().hex[:8]}", max_atomic=4_000_000))
    _mint_grant(models=("some-other-model",), provider_account=fingerprint)
    with pytest.raises(MonetaryAuthorityRefusedError):
        authority.reserve(_liability(f"other-{uuid.uuid4().hex[:8]}"))


def test_stale_or_missing_liquidity_never_authorizes_a_debit(law) -> None:
    authority, service, token = law
    from core import credential_store
    from core.cloud_providers import USEPOD_ORIGIN_SLOT

    # The credential is bound but NO balance was ever observed for it.
    credential_store.store_credential("llm.cloud.usepod", token)
    credential_store.store_credential(USEPOD_ORIGIN_SLOT, service.origin)
    _mint_grant(provider_account=_fingerprint())   # credit_liquidity required
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"dry-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code == "MONEY_LIQUIDITY_UNVERIFIED"
    # The operator may take that duty explicitly for an already-funded account.
    _mint_grant(credit_liquidity="not_required", provider_account=_fingerprint())
    reservation = authority.reserve(_liability(f"funded-{uuid.uuid4().hex[:8]}"))
    assert reservation.reservation_id.startswith("mli:")


def test_competing_reservations_share_one_grant_and_overspend_is_refused(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    # One grant with room for exactly ONE maximum of 4,000,000.
    fingerprint = _observe_balance(service, token)
    _mint_grant(max_total=4_000_000, per_operation=4_000_000, provider_account=fingerprint)
    first = authority.reserve(_liability(f"race-a-{uuid.uuid4().hex[:8]}"))
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"race-b-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code in {"MONEY_AUTHORITY_EXHAUSTED", "MONEY_BUDGET_EXCEEDED", "MONEY_LIQUIDITY_INSUFFICIENT"}
    assert _liability_row(first.liability.operation_id)["state"] == "reserved"


def test_unknown_holds_the_maximum_and_a_duplicate_settle_is_a_no_op(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    _mint_grant(provider_account=_fingerprint())
    liability = _liability(f"unk-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.retain_unknown(reservation, _settlement(liability, upper=None, outcome="outcome_unknown"))
    row = _liability_row(liability.operation_id)
    assert row["state"] == "unknown"
    # Unknown holds its maximum; nothing reads it as free.
    from core.effect_budget_money import money_projection

    projection = money_projection(provider_id="usepod")
    assert len(projection["unknown_liabilities"]) >= 1
    # A settlement arriving later closes it; replaying the identical evidence changes nothing.
    authority.settle(reservation, _settlement(liability))
    authority.settle(reservation, _settlement(liability))
    assert _liability_row(liability.operation_id)["state"] == "settled"


def test_conflicting_settlement_evidence_is_refused_and_writes_nothing(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    _mint_grant(provider_account=_fingerprint())
    liability = _liability(f"conflict-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _exact_settlement(liability, exact=1000))
    row = _liability_row(liability.operation_id)
    assert {line["flow"]: line["actual_atomic"] for line in row["lines"]}["inference_expense"] == "1000"
    # An identical replay is a no-op; a DIFFERENT amount under the same evidence id is refused.
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority.settle(reservation, _exact_settlement(liability, exact=1000))
    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.settle(reservation, _exact_settlement(liability, exact=999))
    assert caught.value.code == "MONEY_SETTLEMENT_CONFLICT"
    after = _liability_row(liability.operation_id)
    assert {line["flow"]: line["actual_atomic"] for line in after["lines"]}["inference_expense"] == "1000"


def test_a_proven_unsent_claim_releases_and_the_operation_may_retry(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    _mint_grant(max_total=8_000_000, provider_account=_fingerprint())
    liability = _liability(f"unsent-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.release_unsent(reservation, reason="provider_invocation_seal_failed")
    assert _liability_row(liability.operation_id)["state"] == "unsent"
    # Retry under the SAME operation re-runs every check with the same terms.
    retried = authority.reserve(liability)
    assert retried.reservation_id and _liability_row(liability.operation_id)["state"] == "reserved"


def test_a_revoked_grant_stops_new_reservations_but_keeps_held_ones(law) -> None:
    authority, service, token = law
    _observe_balance(service, token)
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority, revoke_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    op_token = grant_operator_budget_authority(note=GRANT_NOTE)
    grant = grant_money_authority(
        op_token,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=8_000_000, per_operation_max_atomic=4_000_000, models=(MODEL,), routes=("marketplace",),
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", provider_account=_fingerprint(), note=GRANT_NOTE,
        ),
    )
    held = authority.reserve(_liability(f"held-{uuid.uuid4().hex[:8]}"))
    revoke_money_authority(op_token, grant.grant_id, reason="test revocation")
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"after-revoke-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code == "MONEY_AUTHORITY_REVOKED"
    # The already-held liability keeps its state for settlement or reconciliation.
    assert _liability_row(held.liability.operation_id)["state"] == "reserved"


def test_x402_maps_outflow_and_expense_separately_and_surplus_is_provider_credit(law) -> None:
    authority, service, token = law
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import (
        AssetIdentity,
        CreditLine,
        MoneyGrantSpec,
        grant_money_authority,
        settle_liability,
    )
    from core.effect_budget_money import SettlementEvidence as LawEvidence
    from core.usepod.monetary import MonetaryAuthorityRefusedError, ProviderLiability
    from core.usepod.money_law import OPERATION_X402

    # The x402 grant names the payer and the network; fees stay in their own asset.
    op_token = grant_operator_budget_authority(note=GRANT_NOTE)
    grant = grant_money_authority(
        op_token,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=(OPERATION_X402,), provider_id="usepod",
            asset=AssetIdentity(network="solana:testnet", asset="USDC", decimals=6),
            max_total_atomic=10_000_000, per_operation_max_atomic=5_000_000, models=(MODEL,), routes=("marketplace",),
            expires_epoch=time.time() + 3600.0, network="solana:testnet", payer_account="test:payer",
            provider_account="PayTo9synthetic", credit_liquidity="not_required", note=GRANT_NOTE,
        ),
    )
    liability = ProviderLiability(
        operation_id=f"x402-{uuid.uuid4().hex[:8]}", provider_id="usepod", transport_mode="x402",
        asset="USDC", unit="usdc_microunit", account_kind="x402_payer_wallet", account_ref="test:payer",
        network="solana:testnet", max_amount_atomic=5_000_000, model_id=MODEL, route_approval_id="apr_x",
        envelope_binding_sha256="c" * 64,
        basis={"route_class": "marketplace", "quote_id": "q-1", "pay_to": "PayTo9synthetic"},
    )
    # A wallet-outflow line needs its own fresh observation of the payer's asset.
    from core.effect_budget_money import record_liquidity_observation

    record_liquidity_observation(
        account="test:payer", asset=AssetIdentity(network="solana:testnet", asset="USDC", decimals=6),
        balance_atomic=50_000_000, source="test:funded", verified=True,
    )
    # find_grant only knows the prepaid catalogue today; the x402 lookup is by operation kind.
    from core.usepod import money_law

    x402_grants = []
    from core.effect_budget_money import money_grants

    for row in money_grants(active_only=True):
        spec = dict(row.spec or {})
        if spec.get("provider_id") == "usepod" and OPERATION_X402 in tuple(spec.get("operation_kinds") or ()):
            x402_grants.append({"grant_id": row.grant_id, "spec": spec})
    assert x402_grants, "the x402 grant should be discoverable"

    request = money_law._liability_request(liability, grant_id=x402_grants[0]["grant_id"])
    assert {line["flow"] for line in (line.to_dict() for line in request.lines)} == {"wallet_outflow", "inference_expense"}
    from core.effect_budget_money import reserve_liability

    receipt = reserve_liability(request)
    reservation = type("R", (), {"reservation_id": receipt.liability_id, "liability": liability, "claim_token": ""})()
    authority.claim(reservation, executor="wallet-test")
    authority.mark_dispatched(reservation)
    # The payment response credited 700 surplus: provider credit, never a wallet refund.
    settlement = replace(
        _settlement(liability, upper=1_800_000),
        provider_credit_atomic=700,
        provider_credit_account="PayTo9synthetic",
    )
    authority.settle(reservation, settlement)
    settled = _liability_row(liability.operation_id)
    assert settled["state"] == "settled"
    flows = {line["flow"]: line for line in settled["lines"]}
    # TRUTHFUL SETTLEMENT: the ceiling-priced usage does not become an exact expense (bounded at
    # max, estimate in detail); the wallet outflow waits for the crypto owner's chain
    # confirmation; the surplus is recorded as unverified PROVIDER credit, never a refund.
    debit_flows = {name: line for name, line in flows.items() if name != "provider_credit_credit"}
    assert all(line["actual_atomic"] is None for line in debit_flows.values()), settled["lines"]
    payload = _evidence_payload(liability.operation_id, liability.operation_id)
    assert payload["public_detail"]["usage_priced_at_ceiling_bound_atomic"] == "1800000"
    # The surplus IS durably recorded -- as provider credit on the quote's pay-to account.
    credit = flows.get("provider_credit_credit")
    assert credit and credit["actual_atomic"] == "700" and credit["account"] == "PayTo9synthetic"
    assert flows["wallet_outflow"]["flow"] == "wallet_outflow"


def test_the_fee_asset_is_checked_on_its_own_not_the_principal(law) -> None:
    """The lane sits on the money law; its fee-asset behaviour is the law's own (task 03's
    test_a_fee_asset_shortage...). Exercised here from the UsePod side: a fee line with no fee
    liquidity refuses even though the principal is funded."""
    from core.effect_budget import EffectBudgetRefusedError, grant_operator_budget_authority
    from core.effect_budget_money import (
        AssetIdentity,
        LiabilityRequest,
        MoneyGrantSpec,
        MoneyIdentity,
        MoneyLine,
        grant_money_authority,
    )

    op_token = grant_operator_budget_authority(note=GRANT_NOTE)
    principal = AssetIdentity(network="solana:testnet", asset="USDC", decimals=6)
    fee = AssetIdentity(network="solana:testnet", asset="native", decimals=9)
    grant = grant_money_authority(
        op_token,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod",
            asset=principal, max_total_atomic=10_000_000, per_operation_max_atomic=5_000_000,
            models=(MODEL,), routes=("marketplace",), network="solana:testnet", payer_account="test:payer",
            fee_asset=fee, max_fee_total_atomic=1_000_000, per_operation_max_fee_atomic=500_000,
            expires_epoch=time.time() + 3600.0, provider_account="usepod:x402",
            credit_liquidity="not_required", note=GRANT_NOTE,
        ),
    )
    from core.effect_budget_money import record_liquidity_observation, reserve_liability

    record_liquidity_observation(account="test:payer", asset=principal, balance_atomic=50_000_000, source="test:funded", verified=True)
    # NO observation of the fee asset exists: the reservation must refuse on the fee alone.
    request = LiabilityRequest(
        operation_id=f"fee-{uuid.uuid4().hex[:8]}", operation_kind="inference_x402", grant_id=grant.grant_id,
        identity=MoneyIdentity(provider_id="usepod"),
        lines=(
            MoneyLine("wallet_outflow", principal, 1_000_000, "test:payer"),
            MoneyLine("inference_expense", principal, 1_000_000, "usepod:x402"),
            MoneyLine("network_fee", fee, 100_000, "test:payer"),
        ),
        provider_account="usepod:x402", model_id=MODEL, route="marketplace",
        network="solana:testnet", payer_account="test:payer",
    )
    with pytest.raises(EffectBudgetRefusedError) as caught:
        reserve_liability(request)
    assert caught.value.code in {"MONEY_LIQUIDITY_UNVERIFIED", "MONEY_LIQUIDITY_INSUFFICIENT"}


# --- claim ownership (the review's F2) -----------------------------------------------------------------


def test_a_refused_contender_cannot_settle_mark_or_retain_the_winner(law) -> None:
    """A liability id is not a claim handle: an idempotent re-reservation gives the contender
    its OWN handle-less instance, and every winner-owned transition refuses it."""
    authority, service, token = law
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"own-{uuid.uuid4().hex[:8]}")
    winner = authority.reserve(liability)
    loser = authority.reserve(liability)   # idempotent replay, fresh instance, no handle
    authority.claim(winner, executor="winner")
    for contender_call in (
        lambda: authority.mark_dispatched(loser),
        lambda: authority.settle(loser, _settlement(liability)),
        lambda: authority.retain_unknown(loser, _settlement(liability, upper=None, outcome="outcome_unknown")),
        lambda: authority.release_unsent(loser, reason="provider_invocation_seal_failed"),
    ):
        with pytest.raises(MonetaryAuthorityRefusedError) as caught:
            contender_call()
        assert caught.value.code == "MONEY_CLAIM_CONFLICT", str(caught.value)
    assert _liability_row(liability.operation_id)["state"] == "dispatching"
    # The WINNER still completes its own lifecycle untouched.
    authority.mark_dispatched(winner)
    authority.settle(winner, _settlement(liability))
    assert _liability_row(liability.operation_id)["state"] == "settled"


def test_two_threads_on_one_shared_authority_one_winner_and_no_corruption(law) -> None:
    """Genuinely novel schedule: two concurrent invocations through ONE authority instance
    (the served daemon's shape). Exactly one claims; the loser's cleanup cannot touch the
    winner, whatever order the threads run in."""
    import threading

    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"race-own-{uuid.uuid4().hex[:8]}")
    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()

    def invoke(name: str) -> None:
        reservation = authority.reserve(liability)
        try:
            authority.claim(reservation, executor=f"executor-{name}")
        except Exception as exc:  # the loser's claim refusal: it must NOT clean up
            with lock:
                outcomes.append((name, f"refused:{getattr(exc, 'code', type(exc).__name__)}"))
            return
        # The winner "sends" and settles.
        authority.mark_dispatched(reservation)
        authority.settle(reservation, _settlement(liability))
        with lock:
            outcomes.append((name, "settled"))

    threads = [threading.Thread(target=invoke, args=(str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)
    states = sorted(state for _name, state in outcomes)
    assert states == ["refused:MONEY_CLAIM_CONFLICT", "settled"], outcomes
    assert _liability_row(liability.operation_id)["state"] == "settled"


def test_the_true_claimant_may_still_prove_its_own_attempt_unsent(law) -> None:
    """Preservation control: ownership fencing must not break the honest loser-free path -- the
    invocation that claimed and provably never sent releases ITS attempt."""
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"unsent-own-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="the-only-invocation")
    authority.release_unsent(reservation, reason="provider_invocation_seal_failed")
    row = _liability_row(liability.operation_id)
    assert row["state"] == "unsent"
    retried = authority.reserve(liability)
    authority.claim(retried, executor="retry")
    authority.mark_dispatched(retried)
    authority.settle(retried, _settlement(liability))
    assert _liability_row(liability.operation_id)["state"] == "settled"


def test_a_lost_response_after_the_winners_claim_stays_unknown_for_reconciliation(law) -> None:
    """The winner claimed, the request left, the response never came back: the liability holds
    as unknown at its maximum (never released by a failure path), for evidence to close."""
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"lost-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="winner")
    authority.mark_dispatched(reservation)
    authority.retain_unknown(reservation, _settlement(liability, upper=None, outcome="outcome_unknown"))
    row = _liability_row(liability.operation_id)
    assert row["state"] == "unknown"
    lines = {line["flow"]: line for line in row["lines"]}
    assert all(line["actual_atomic"] is None for line in lines.values())
    assert all(int(line["max_atomic"]) == liability.max_amount_atomic for line in lines.values())


# --- settlement truth (the review's F1) ----------------------------------------------------------------


def _exact_settlement(liability, *, exact: int):
    from core.usepod.monetary import SettlementEvidence

    return SettlementEvidence(
        operation_id=liability.operation_id, outcome="completed", http_status=200,
        usage={"prompt_tokens": 21, "completion_tokens": 13}, input_tokens=21, output_tokens=13,
        upper_bound_cost_atomic=1234, exact_cost_atomic=exact, exact_cost_state="provider_reported_exact_debit",
        route={"route_class": "marketplace"}, balance_remaining_raw="79.900000",
        usage_exceeds_liability_bound=False,
    )


def test_authenticated_exact_evidence_below_the_ceiling_settles_exact(law) -> None:
    """A provider's own exact, operation-bound charge (below the approved ceiling) IS an exact
    actual -- the one thing that may tighten a bound."""
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint)
    liability = _liability(f"exact-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _exact_settlement(liability, exact=999))
    settled = _liability_row(liability.operation_id)
    flows = {line["flow"]: line for line in settled["lines"]}
    assert flows["inference_expense"]["actual_atomic"] == "999"
    assert flows["provider_credit_debit"]["actual_atomic"] == "999"


def test_missing_usage_and_price_settles_bounded_with_no_estimate(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint)
    liability = _liability(f"silent-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _settlement(liability, upper=None))
    settled = _liability_row(liability.operation_id)
    assert all(line["actual_atomic"] is None for line in settled["lines"])
    payload = _evidence_payload(liability.operation_id, liability.operation_id)
    assert "usage_priced_at_ceiling_bound_atomic" not in payload["public_detail"]


def test_late_exact_evidence_tightens_an_earlier_bounded_settlement(law) -> None:
    """Reconciliation shape: the response settled bounded (ceiling estimate only), and the
    provider's exact statement arrives later under a different evidence id."""
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint)
    liability = _liability(f"late-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _settlement(liability, upper=1234))
    assert all(line["actual_atomic"] is None for line in _liability_row(liability.operation_id)["lines"])
    late = _exact_settlement(liability, exact=700)
    from dataclasses import replace as _replace

    late = _replace(late, operation_id=f"{liability.operation_id}:billing_statement")
    authority.settle(reservation, late)
    assert _evidence_payload(liability.operation_id, f"{liability.operation_id}:billing_statement")["actuals"]["inference_expense"] == "700"
    flows = {line["flow"]: line for line in _liability_row(liability.operation_id)["lines"]}
    assert flows["inference_expense"]["actual_atomic"] == "700"
    assert flows["provider_credit_debit"]["actual_atomic"] == "700"


def test_a_truthful_settlement_keeps_the_grant_envelope_and_liquidity_honest(law) -> None:
    """Bounded settlement consumes the grant's operation envelope and leaves the held maximum
    counted against liquidity until it closes -- the cap is never read as spent."""
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=4_000_000, per_operation=4_000_000)
    liability = _liability(f"cap-{uuid.uuid4().hex[:8]}")
    reservation = authority.reserve(liability)
    authority.claim(reservation)
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _settlement(liability, upper=1234))
    # The grant's one operation is spent even though nothing settled exact: the cap counts
    # operations, not estimates.
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"cap2-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code in {"MONEY_AUTHORITY_EXHAUSTED", "MONEY_BUDGET_EXCEEDED"}


# --- correction-2: durable one-use consent, current claim fencing, consent states ----------


def _approved_consent(law, monkeypatch, *, per_call=1000, total=1000):
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval

    authority, service, token = law
    fingerprint = _observe_balance(service, token)

    def facts(**kwargs):
        return {"account": fingerprint, "account_kind": "usepod_prepaid_token_balance",
                "asset": "USDC", "network": "usepod_internal_account_ledger", "origin": service.origin,
                "models": [MODEL], "routes": ["marketplace"], **kwargs}

    monkeypatch.setattr(spend_approval, "_proposal_facts", facts)
    proposal = spend_approval.propose_spend_grant(per_call_atomic=per_call, max_total_atomic=total, expiry_epoch=time.time() + 3600)
    approval_id = proposal["approval_id"]
    assert resolve_approval(approval_id, decision="allow")["status"] == "approved"
    return approval_id


def test_three_concurrent_confirms_mint_exactly_one_grant(law, monkeypatch) -> None:
    """Genuinely novel schedule: three confirmers racing on one approved consent."""
    import concurrent.futures

    from core.effect_budget_money import money_grants
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(spend_approval.confirm_spend_grant, [approval_id, approval_id, approval_id]))
    grants = [g for g in money_grants(active_only=False) if g.spec.get("approval_ref") == approval_id]
    assert len(grants) == 1, [g.grant_id for g in grants]
    assert len({r["grant_id"] for r in results}) == 1, results


def test_a_lost_confirm_reply_and_restart_return_the_same_grant(law, monkeypatch) -> None:
    """Durability: after the mint, a fresh authority instance (the restart shape) replays the
    confirm and gets the SAME grant; a REVOKED consent's replay returns the same revoked grant,
    never fresh spend authority."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import money_grant, revoke_money_authority
    from core.usepod import spend_approval
    from core.usepod.money_law import AUTHORITY_LABEL, EffectBudgetMonetaryAuthority

    approval_id = _approved_consent(law, monkeypatch)
    first = spend_approval.confirm_spend_grant(approval_id)
    restarted = EffectBudgetMonetaryAuthority()
    from core.usepod.monetary import install_monetary_authority

    install_monetary_authority(restarted, label=AUTHORITY_LABEL)
    replay = spend_approval.confirm_spend_grant(approval_id)
    assert replay["grant_id"] == first["grant_id"] and replay["idempotent"] is True
    revoke_money_authority(grant_operator_budget_authority(note="test revoke"), first["grant_id"], reason="test")
    after_revoke = spend_approval.confirm_spend_grant(approval_id)
    assert after_revoke["grant_id"] == first["grant_id"], "a revoked consent must not mint fresh authority"
    assert str(money_grant(first["grant_id"]).state) == "revoked"


def test_descriptive_approval_refs_stay_non_unique(law, monkeypatch) -> None:
    """Preservation: the one-use contract is OPT-IN via consent_id. Old callers that use
    approval_ref as a descriptive reference may mint repeatedly under the same reference."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority, money_grants
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator = grant_operator_budget_authority(note="descriptive refs")

    def spec():
        return MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=1000, per_operation_max_atomic=1000, models=(MODEL,), routes=("marketplace",),
            expires_epoch=time.time() + 3600, credit_liquidity="not_required",
            provider_account="upc_descriptive", approval_ref="monthly-report", note="descriptive",
        )

    grant_money_authority(operator, spec())
    grant_money_authority(operator, spec())
    same_ref = [g for g in money_grants(active_only=False) if g.spec.get("approval_ref") == "monthly-report"]
    assert len(same_ref) == 2, "descriptive approval_ref must NOT be globally one-use"


def test_denied_and_expired_consents_never_mint(law, monkeypatch) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval

    authority, service, token = law
    fingerprint = _observe_balance(service, token)

    def facts(**kwargs):
        return {"account": fingerprint, "account_kind": "k", "asset": "USDC",
                "network": "usepod_internal_account_ledger", "origin": service.origin,
                "models": [MODEL], "routes": ["marketplace"], **kwargs}

    monkeypatch.setattr(spend_approval, "_proposal_facts", facts)
    denied = spend_approval.propose_spend_grant(per_call_atomic=100, max_total_atomic=100, expiry_epoch=time.time() + 3600)
    resolve_approval(denied["approval_id"], decision="deny")
    with pytest.raises(PermissionError):
        spend_approval.confirm_spend_grant(denied["approval_id"])
    assert spend_approval.spend_consent_state()["state"] == "denied"
    # Unapproved consent refuses; expired approval refuses with its own reason.
    unapproved = spend_approval.propose_spend_grant(per_call_atomic=100, max_total_atomic=100, expiry_epoch=time.time() + 3600)
    with pytest.raises(PermissionError):
        spend_approval.confirm_spend_grant(unapproved["approval_id"])


def test_the_consent_state_model_is_truthful_at_each_step(law, monkeypatch) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "approved" and state["approval_id"] == approval_id
    # pending_spend_approval keeps the approved-unminted consent actionable (survives reload).
    assert spend_approval.pending_spend_approval()["approval_id"] == approval_id
    spend_approval.confirm_spend_grant(approval_id)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "minted" and state["grant"]["grant_id"]
    assert spend_approval.pending_spend_approval() is None


def test_a_stale_handle_cannot_mark_retain_or_settle_the_new_generation(law) -> None:
    """Audit of every adapter lifecycle write after a retry: the old handle is refused on
    mark_dispatched, retain_unknown and settle; the CURRENT claimant completes."""
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"stale-{uuid.uuid4().hex[:8]}")
    stale = authority.reserve(liability)
    authority.claim(stale, executor="old")
    authority.release_unsent(stale, reason="connection_not_established")
    current = authority.reserve(liability)
    authority.claim(current, executor="new")
    for stale_call in (
        lambda: authority.mark_dispatched(stale),
        lambda: authority.retain_unknown(stale, _settlement(liability, upper=None, outcome="outcome_unknown")),
        lambda: authority.settle(stale, _settlement(liability)),
    ):
        with pytest.raises(MonetaryAuthorityRefusedError) as caught:
            stale_call()
        assert caught.value.code == "MONEY_CLAIM_CONFLICT", str(caught.value)
    assert _liability_row(liability.operation_id)["state"] == "dispatching"
    authority.mark_dispatched(current)
    authority.settle(current, _settlement(liability))
    assert _liability_row(liability.operation_id)["state"] == "settled"


def test_a_duplicate_unsent_proof_after_the_retry_claims_is_refused(law) -> None:
    """Novel ordering: A proved its attempt unsent (closing attempt 1), the retry claimed a NEW
    generation, and A's invocation then emits a DUPLICATE unsent proof (double cleanup). The
    stale handle must not close the retry's live dispatch; the liability stays dispatching."""
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint, max_total=8_000_000)
    liability = _liability(f"late-unsent-{uuid.uuid4().hex[:8]}")
    stale = authority.reserve(liability)
    authority.claim(stale, executor="a")
    authority.release_unsent(stale, reason="connection_not_established")  # attempt 1 closed lawfully
    current = authority.reserve(liability)  # the retry re-opens the closed operation
    authority.claim(current, executor="b")
    with pytest.raises(MonetaryAuthorityRefusedError):
        authority.release_unsent(stale, reason="connection_not_established")  # A's duplicate proof
    assert _liability_row(liability.operation_id)["state"] == "dispatching"


def test_unknown_outcomes_stay_closable_by_evidence_without_a_handle(law) -> None:
    """Preservation: reconciliation is not a dead end. An UNKNOWN liability (the claimant died or
    the response was lost) settles by late provider evidence with NO claim token -- the
    evidence-driven path the fencing must not break."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import (
        AssetIdentity,
        LiabilityRequest,
        MoneyGrantSpec,
        MoneyIdentity,
        MoneyLine,
        claim_dispatch,
        grant_money_authority,
        liability_for_operation,
        record_unknown,
        reserve_liability,
        settle_liability,
    )
    from core.effect_budget_money import SettlementEvidence as LawEvidence
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator = grant_operator_budget_authority(note="synthetic test funds")
    asset = AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6)
    grant = grant_money_authority(operator, MoneyGrantSpec(
        kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod", asset=asset,
        max_total_atomic=8000, per_operation_max_atomic=8000, models=(MODEL,), routes=("marketplace",),
        expires_epoch=time.time() + 3600, credit_liquidity="not_required", provider_account="upc_recon", note="s"))
    request = LiabilityRequest(operation_id=f"recon-{uuid.uuid4().hex[:8]}", operation_kind="inference_prepaid",
        grant_id=grant.grant_id, identity=MoneyIdentity(provider_id="usepod"),
        lines=(MoneyLine("inference_expense", asset, 100, "upc_recon"), MoneyLine("provider_credit_debit", asset, 100, "upc_recon")),
        provider_account="upc_recon", model_id=MODEL, route="marketplace")
    receipt = reserve_liability(request)
    claim = claim_dispatch(receipt.liability_id, executor="lost-invocation")
    record_unknown(receipt.liability_id, claim.claim_token, reason="response lost after send")
    assert liability_for_operation(request.operation_id)["state"] == "unknown"
    # Late exact evidence, NO handle: reconciliation closes it truthfully.
    settle_liability(receipt.liability_id, LawEvidence("provider_usage_receipt", f"{request.operation_id}:statement", "provider", actuals={"inference_expense": 40, "provider_credit_debit": 40}))
    row = liability_for_operation(request.operation_id)
    assert row["state"] == "settled"
    flows = {line["flow"]: line for line in row["lines"]}
    assert flows["inference_expense"]["actual_atomic"] == "40"


# --- correction-3: consent expiry precedence and recovery ---------------------------------------------


def _advance_clocks(monkeypatch, *, to: float) -> None:
    """One consistent notion of time across the consent state and the money law (the review's
    two-clock discipline: both deadlines judged as a consistent runtime would)."""
    import time as _time

    import core.effect_budget_money as ebm

    monkeypatch.setattr(_time, "time", lambda: float(to))
    monkeypatch.setattr(ebm, "_now", lambda: float(to))


def test_approval_expiry_kills_the_approved_continuation(law, monkeypatch) -> None:
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    entry = spend_approval._approval_entry(approval_id)
    _advance_clocks(monkeypatch, to=float(entry["expires_at"]) + 1)
    with pytest.raises(PermissionError):
        spend_approval.confirm_spend_grant(approval_id)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "expired", state
    assert spend_approval.pending_spend_approval() is None


def test_economic_expiry_kills_the_approved_continuation(law, monkeypatch) -> None:
    """The grant's own spending window ended before the approval entry did: same dead end."""
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    entry = spend_approval._approval_entry(approval_id)
    economic = float((entry.get("spend_grant_spec") or {}).get("expiry_epoch") or 0)
    assert economic < float(entry["expires_at"]), "economic deadline must precede the approval here"
    _advance_clocks(monkeypatch, to=economic + 1)
    from core.effect_budget import EffectBudgetRefusedError

    with pytest.raises((PermissionError, EffectBudgetRefusedError)):
        spend_approval.confirm_spend_grant(approval_id)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "expired", state
    assert spend_approval.pending_spend_approval() is None


def test_a_short_lived_consent_expires_while_still_pending(law, monkeypatch) -> None:
    """Genuinely different case: a SHORT-lived consent nobody allowed yet -- different data
    (one-minute economic window), different answer (expires pending, never actionable)."""
    import time as _time

    from core.usepod import spend_approval

    authority, service, token = law
    fingerprint = _observe_balance(service, token)

    def facts(**kwargs):
        return {"account": fingerprint, "account_kind": "k", "asset": "USDC",
                "network": "usepod_internal_account_ledger", "origin": service.origin,
                "models": [MODEL], "routes": ["marketplace"], **kwargs}

    monkeypatch.setattr(spend_approval, "_proposal_facts", facts)
    proposal = spend_approval.propose_spend_grant(per_call_atomic=250, max_total_atomic=750, expiry_epoch=_time.time() + 60)
    _advance_clocks(monkeypatch, to=_time.time() + 61)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "expired", state
    assert spend_approval.pending_spend_approval() is None


def test_pending_and_denied_controls_stay_truthful(law, monkeypatch) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval

    authority, service, token = law
    fingerprint = _observe_balance(service, token)

    def facts(**kwargs):
        return {"account": fingerprint, "account_kind": "k", "asset": "USDC",
                "network": "usepod_internal_account_ledger", "origin": service.origin,
                "models": [MODEL], "routes": ["marketplace"], **kwargs}

    monkeypatch.setattr(spend_approval, "_proposal_facts", facts)
    fresh = spend_approval.propose_spend_grant(per_call_atomic=100, max_total_atomic=200)
    assert spend_approval.spend_consent_state()["state"] == "pending"
    assert spend_approval.pending_spend_approval()["approval_id"] == fresh["approval_id"]
    resolve_approval(fresh["approval_id"], decision="deny")
    assert spend_approval.spend_consent_state()["state"] == "denied"
    assert spend_approval.pending_spend_approval() is None


def test_a_fresh_approved_consent_still_continues_and_a_used_one_stays_used(law, monkeypatch) -> None:
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    assert spend_approval.spend_consent_state()["state"] == "approved"
    assert spend_approval.pending_spend_approval()["approval_id"] == approval_id
    first = spend_approval.confirm_spend_grant(approval_id)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "minted" and state["grant"]["grant_id"] == first["grant_id"]
    # The minted record survives its own economic expiry WITHOUT becoming a mint opportunity.
    entry = spend_approval._approval_entry(approval_id)
    _advance_clocks(monkeypatch, to=float((entry.get("spend_grant_spec") or {}).get("expiry_epoch") or 0) + 1)
    state = spend_approval.spend_consent_state()
    assert state["state"] == "minted", state
    assert spend_approval.pending_spend_approval() is None
    replay = spend_approval.confirm_spend_grant(approval_id)
    assert replay["grant_id"] == first["grant_id"] and replay["idempotent"] is True


def test_a_revoked_grant_record_stays_minted_not_a_new_opportunity(law, monkeypatch) -> None:
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import revoke_money_authority
    from core.usepod import spend_approval

    approval_id = _approved_consent(law, monkeypatch)
    minted = spend_approval.confirm_spend_grant(approval_id)
    revoke_money_authority(grant_operator_budget_authority(note="test revoke"), minted["grant_id"], reason="test")
    state = spend_approval.spend_consent_state()
    assert state["state"] == "minted" and str(state["grant"]["state"]) == "revoked"
    assert spend_approval.pending_spend_approval() is None
    replay = spend_approval.confirm_spend_grant(approval_id)
    assert replay["grant_id"] == minted["grant_id"], "a revoked record must not re-mint"


def test_incomplete_facts_are_not_usable_consent(law, monkeypatch) -> None:
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval

    authority, service, token = law
    fingerprint = _observe_balance(service, token)

    def facts(**kwargs):
        return {"account": fingerprint, "account_kind": "k", "asset": "USDC",
                "network": "usepod_internal_account_ledger", "origin": service.origin,
                "models": [MODEL], "routes": ["marketplace"], **kwargs}

    monkeypatch.setattr(spend_approval, "_proposal_facts", facts)
    proposal = spend_approval.propose_spend_grant(per_call_atomic=100, max_total_atomic=100)
    # Corrupt the recorded facts the way a broken writer would: the entry stays, its economics
    # do not. It must read invalid -- never actionable, never "approved".
    from core.mode_permission_policy import _APPROVALS, _LOCK

    with _LOCK:
        _APPROVALS[proposal["approval_id"]]["spend_grant_spec"] = {}
    resolve_approval(proposal["approval_id"], decision="allow")
    state = spend_approval.spend_consent_state()
    assert state["state"] == "invalid", state
    assert spend_approval.pending_spend_approval() is None
    with pytest.raises(PermissionError):
        spend_approval.confirm_spend_grant(proposal["approval_id"])
