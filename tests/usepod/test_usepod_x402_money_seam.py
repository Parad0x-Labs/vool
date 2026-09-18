"""The x402 money seam through the PRODUCTION path: transport → wallet facts → reserve → claim
→ test-wallet proof → paid retry → settlement with provider credit.

A labelled SYNTHETIC wallet authority (payer identity + fee facts + signing against the strict
service's synthetic chain) stands in for the crypto owner's wallet; the monetary path is the
REAL production authority and money law. The default without a wallet still fails closed
(proven elsewhere); here we prove the lane CAN activate through its own contracts when the
wallet authority publishes its facts, and that it refuses BEFORE signing when the facts, fee
liquidity, grant or claim are missing or wrong. No real chain, no real funds.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from core.usepod.monetary import MonetaryAuthorityRefusedError, install_monetary_authority, reset_monetary_authority
from core.usepod.money_law import AUTHORITY_LABEL, EffectBudgetMonetaryAuthority
from core.usepod.transport import (
    PaymentAuthorityRefusedError,
    UsePodHttpTransport,
    UsePodTransportError,
    UsePodX402Client,
    X402WalletFacts,
    install_payment_authority,
    reset_payment_authority,
)
from tests.usepod.strict_usepod_service import DOCUMENTED_MAINNET_NETWORK, Listing, StrictUsePodService

MODEL = "meridian-synth-chat"
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)
GRANT_NOTE = "synthetic test funds: x402 seam"


class SyntheticWalletAuthority:
    """TEST DOUBLE: a labelled synthetic wallet that publishes its identity and fee facts and
    signs against the strict service's synthetic chain. Not a wallet, not production."""

    label = "test_double:synthetic_x402_wallet"
    networks = (DOCUMENTED_MAINNET_NETWORK,)

    def __init__(self, *, service, payer: str = "9SynthPayer" + "1" * 33, fee_balance: int = 5_000_000, principal_balance: int = 50_000_000, publish_facts: bool = True, refuse_proof: str = "", fee_asset: str = "native") -> None:
        self.service = service
        self.fee_asset = fee_asset
        self.payer = payer
        self.fee_balance = fee_balance
        self.principal_balance = principal_balance
        self.publish_facts = publish_facts
        self.refuse_proof = refuse_proof
        self.proof_calls: list[dict] = []
        self.fact_calls: list[dict] = []

    def wallet_facts(self, *, network: str, asset: str, atomic_unit: str) -> X402WalletFacts:
        if not self.publish_facts:
            raise RuntimeError("identity unavailable")
        self.fact_calls.append({"network": network, "asset": asset})
        return X402WalletFacts(
            payer_account=self.payer,
            fee_network=network,
            fee_asset=self.fee_asset,
            fee_decimals=9,
            fee_max_atomic=200_000,
            principal_balance_atomic=self.principal_balance,
            fee_balance_atomic=self.fee_balance,
        )

    def obtain_proof(self, *, quote, option, envelope, liability, reservation):
        self.proof_calls.append({"operation_id": envelope.operation_id, "amount": option.amount_atomic, "claim_token": str(getattr(reservation, "claim_token", "") or "")})
        if self.refuse_proof:
            raise PaymentAuthorityRefusedError(self.refuse_proof)
        from tests.usepod._usepod_doubles import synthetic_signature
        from tests.usepod.strict_usepod_service import SYNTHETIC_PAY_TO

        from core.usepod.transport import X402PaymentProof

        signature = synthetic_signature(envelope.operation_id)
        # The synthetic chain confirms the payment before the paid retry carries the signature.
        self.service.record_chain_payment(signature, asset=option.asset, amount_atomic=int(option.amount_atomic or 0), pay_to=SYNTHETIC_PAY_TO)

        return X402PaymentProof(
            operation_id=envelope.operation_id,
            envelope_binding_sha256=envelope.binding_sha256,
            quote_header_sha256=quote.header_sha256,
            quote_id=quote.quote_id,
            network=option.network,
            asset=option.asset,
            pay_to=option.pay_to,
            amount_atomic=option.amount_atomic,
            payer_wallet=self.payer,
            signature=signature,
            authority_label=self.label,
            issued_at=time.time(),
        )


@pytest.fixture
def x402_rig(tmp_path, monkeypatch):
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "eb.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 50_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    authority = EffectBudgetMonetaryAuthority()
    install_monetary_authority(authority, label=AUTHORITY_LABEL)
    wallet = SyntheticWalletAuthority(service=service)
    install_payment_authority(wallet, label=wallet.label)
    try:
        yield service, authority, wallet, token
    finally:
        service.stop()
        reset_payment_authority()
        reset_monetary_authority()
        effect_budget.reset_effect_budget_process_state()
        configure_default_db_path(None)


def _mint_x402_grant(*, per_operation: int = 5_000_000, payer: str = "9SynthPayer" + "1" * 33, revoked: bool = False, asset: str = "USDC", decimals: int = 6, fee_asset: str = "native", model: str = MODEL) -> str:
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority, revoke_money_authority

    operator = grant_operator_budget_authority(note=GRANT_NOTE)
    grant = grant_money_authority(
        operator,
        MoneyGrantSpec(
            kind="single_payment",
            operation_kinds=("inference_x402",),
            provider_id="usepod",
            asset=AssetIdentity(network=DOCUMENTED_MAINNET_NETWORK, asset=asset, decimals=decimals),
            max_total_atomic=per_operation,
            per_operation_max_atomic=per_operation,
            models=(model,),
            routes=("key_relay+marketplace",),
            network=DOCUMENTED_MAINNET_NETWORK,
            payer_account=payer,
            fee_asset=AssetIdentity(network=DOCUMENTED_MAINNET_NETWORK, asset=fee_asset, decimals=9),
            max_fee_total_atomic=1_000_000,
            per_operation_max_fee_atomic=1_000_000,
            expires_epoch=time.time() + 3600.0,
            credit_liquidity="not_required",
            approval_ref="synthetic:x402-seam",
            note=GRANT_NOTE,
        ),
    )
    if revoked:
        revoke_money_authority(operator, grant.grant_id, reason="test")
    return grant.grant_id


def _exchange(service, wallet, *, operation_id: str | None = None, body: str = "Name three Baltic ports.", assets: tuple[str, ...] = ("USDC",), bounds: dict[str, int] | None = None):
    from core.usepod.transport import seal_request_envelope

    envelope = seal_request_envelope(
        payload={"model": MODEL, "messages": [{"role": "user", "content": body}], "max_tokens": 64},
        protocol="openai",
        transport_mode="x402",
        origin=service.origin,
        model_id=MODEL,
        route_approval_id="apr_x402_test",
    )
    client = UsePodX402Client(transport=UsePodHttpTransport())
    return client.execute(
        envelope,
        origin=service.origin,
        allowed_assets=assets,
        local_bounds_atomic=bounds if bounds is not None else {"USDC": 5_000_000},
        liability_basis={"route_classes": ["marketplace", "key_relay"]},
        read_timeout_seconds=30.0,
    )


def test_the_real_seam_reserves_claims_proves_pays_and_settles_with_credit(x402_rig) -> None:
    service, authority, wallet, token = x402_rig
    _mint_x402_grant()
    exchange = _exchange(service, wallet)
    assert exchange.response.status == 200
    # The proof was obtained AFTER the claim (the transport claims before obtain_proof).
    assert wallet.proof_calls and wallet.proof_calls[0]["claim_token"], "the claim handle must precede signing"
    from core.effect_budget_money import liability_for_operation

    # After the paid retry the liability is PENDING under this invocation's claim; settlement is
    # the adapter's step, performed here exactly as the adapter does it (usage priced as a
    # bound, the PAYMENT-RESPONSE surplus carried as provider credit).
    row = liability_for_operation(exchange.reservation.liability.operation_id)
    assert row is not None and row["state"] == "pending", row["state"]
    from core.usepod.monetary import SettlementEvidence
    from core.usepod.transport import decode_payment_response

    decoded = decode_payment_response(exchange.response.header("payment-response"))
    assert decoded["state"] == "decoded_schema_unpublished", decoded
    surplus = int(decoded["fields"].get("surplus_credited_microunits") or 0)
    assert surplus > 0, "the SYNTHETIC service credits the unused cap; a zero here means the receipt was not read"
    authority.settle(
        exchange.reservation,
        SettlementEvidence(
            operation_id=exchange.reservation.liability.operation_id,
            outcome="completed",
            http_status=200,
            usage={},
            input_tokens=None,
            output_tokens=None,
            upper_bound_cost_atomic=None,
            exact_cost_atomic=None,
            exact_cost_state="not_supplied_by_provider",
            route={"route_class": "marketplace"},
            balance_remaining_raw=None,
            usage_exceeds_liability_bound=False,
            provider_credit_atomic=surplus or None,
            provider_credit_account=exchange.option.pay_to,
        ),
    )
    flows = {line["flow"]: line for line in row["lines"]}
    # the native DNA service fee's whole-unit ceiling rides the liability on the Mainnet row (core.wallet.dna_fees)
    assert set(flows) == {"wallet_outflow", "inference_expense", "network_fee", "service_fee"}
    # Truthful settlement: nothing exact without an exact charge; the wallet line waits for chain
    # confirmation; the surplus is provider credit on the quote's pay-to, in the paid asset.
    assert all(line["actual_atomic"] is None for line in flows.values()), row["lines"]
    assert flows["network_fee"]["asset_key"].endswith("|native|9"), flows["network_fee"]
    credit = None
    settled = liability_for_operation(exchange.reservation.liability.operation_id)
    for line in settled["lines"]:
        if line["flow"] == "provider_credit_credit":
            credit = line
    assert credit is not None and credit["account"] == exchange.option.pay_to, credit


def test_no_wallet_facts_refuses_before_any_reservation_or_proof(x402_rig) -> None:
    service, authority, wallet, token = x402_rig
    _mint_x402_grant()
    wallet.publish_facts = False
    with pytest.raises(UsePodTransportError) as caught:
        _exchange(service, wallet)
    assert caught.value.code == "wallet_payer_identity_unavailable"
    assert wallet.proof_calls == []
    from core.effect_budget_money import liabilities

    assert liabilities() == [], "no liability may exist without the wallet's identity facts"


def test_insufficient_fee_liquidity_refuses_before_signing(x402_rig) -> None:
    service, authority, wallet, token = x402_rig
    _mint_x402_grant()
    wallet.fee_balance = 100  # below the fee line's own maximum
    with pytest.raises((UsePodTransportError, MonetaryAuthorityRefusedError)) as caught:
        _exchange(service, wallet)
    code = str(getattr(caught.value, "code", "") or caught.value)
    assert "LIQUIDITY" in code, code
    assert wallet.proof_calls == [], "nothing may be signed when the fee asset cannot cover its line"


def test_a_revoked_x402_grant_refuses_before_the_proof(x402_rig) -> None:
    service, authority, wallet, token = x402_rig
    _mint_x402_grant(revoked=True)
    with pytest.raises((UsePodTransportError, MonetaryAuthorityRefusedError)) as caught:
        _exchange(service, wallet)
    code = str(getattr(caught.value, "code", "") or caught.value)
    assert "REVOKED" in code or "AUTHORITY" in code, code
    assert wallet.proof_calls == []


def test_a_competing_operation_cannot_claim_the_paid_one(x402_rig) -> None:
    """Genuinely novel schedule: two operations against a one-operation grant; the second is
    refused at reservation, before any quote-payment or signing, and the first completes."""
    service, authority, wallet, token = x402_rig
    _mint_x402_grant()
    first = _exchange(service, wallet, body="Name two Nordic capitals.")
    assert first.response.status == 200
    with pytest.raises((UsePodTransportError, MonetaryAuthorityRefusedError)) as caught:
        _exchange(service, wallet, body="Name two Baltic rivers.")
    code = str(getattr(caught.value, "code", "") or caught.value)
    assert code in {"MONEY_AUTHORITY_EXHAUSTED", "MONEY_BUDGET_EXCEEDED", "MONEY_AUTHORITY_INVALID"} or "EXHAUST" in code, code
    assert len(wallet.proof_calls) == 1, "the refused operation never reached signing"


def test_wrong_payer_identity_refuses_at_the_grant_binding(x402_rig) -> None:
    """The grant binds the wallet's payer account; a different payer's facts cannot use it."""
    service, authority, wallet, token = x402_rig
    _mint_x402_grant(payer="8OtherPayer" + "2" * 33)
    with pytest.raises((UsePodTransportError, MonetaryAuthorityRefusedError)) as caught:
        _exchange(service, wallet)
    code = str(getattr(caught.value, "code", "") or caught.value)
    assert code in {"MONEY_AUTHORITY_INVALID", "MONEY_IDENTITY_CONFLICT"} or "AUTHORITY" in code, code
    assert wallet.proof_calls == []


# --- SOL and per-asset consent ----------------------------------------------------------------------------


def test_a_sol_option_reserves_in_lamports_with_its_fee_on_the_same_balance(x402_rig) -> None:
    """The transport names SOL amounts in ``lamport``; the law scales them, and a SOL fee is a line in the SAME
    asset, so principal and fee are checked together against one balance."""
    service, _authority, wallet, _token = x402_rig
    wallet.fee_asset = "SOL"
    _mint_x402_grant(asset="SOL", decimals=9, fee_asset="SOL", per_operation=5_000_000)
    exchange = _exchange(service, wallet, assets=("SOL",), bounds={"SOL": 5_000_000})
    assert exchange.response.status == 200 and exchange.option.asset == "SOL" and exchange.option.atomic_unit == "lamport"
    from core.effect_budget_money import liability_for_operation

    row = liability_for_operation(exchange.reservation.liability.operation_id)
    keys = {line["flow"]: line["asset_key"] for line in row["lines"]}
    assert keys["wallet_outflow"] == keys["network_fee"] == f"{DOCUMENTED_MAINNET_NETWORK}|SOL|9", keys


def test_a_sol_balance_that_covers_the_amount_and_the_fee_only_separately_refuses(x402_rig) -> None:
    service, _authority, wallet, _token = x402_rig
    wallet.fee_asset = "SOL"
    # 200_100 lamports cover the fee maximum (200_000) and the quoted amount, but not both together.
    wallet.principal_balance = wallet.fee_balance = 200_100
    _mint_x402_grant(asset="SOL", decimals=9, fee_asset="SOL", per_operation=5_000_000)
    with pytest.raises(UsePodTransportError) as caught:
        _exchange(service, wallet, assets=("SOL",), bounds={"SOL": 5_000_000})
    assert caught.value.code == "MONEY_LIQUIDITY_INSUFFICIENT", caught.value.code
    assert wallet.proof_calls == [], "nothing may be signed against a balance that cannot carry principal plus fee"


def test_a_usdc_consent_never_covers_a_sol_payment(x402_rig) -> None:
    service, _authority, wallet, _token = x402_rig
    wallet.fee_asset = "SOL"
    _mint_x402_grant(per_operation=50_000_000)  # USDC, a large number
    with pytest.raises(UsePodTransportError) as caught:
        _exchange(service, wallet, assets=("SOL",), bounds={"SOL": 5_000_000})
    assert caught.value.code == "MONEY_AUTHORITY_INVALID", caught.value.code
    assert wallet.proof_calls == []


def test_x402_payment_bounds_follow_the_consents_for_this_model_and_route(x402_rig) -> None:
    from core.usepod.money_law import x402_payment_bounds

    route = "key_relay+marketplace"
    assert x402_payment_bounds(model_id=MODEL, route=route, usdc_route_bound_atomic=12_345) == (("USDC",), {"USDC": 12_345})
    _mint_x402_grant(per_operation=5_000)
    assert x402_payment_bounds(model_id=MODEL, route=route, usdc_route_bound_atomic=12_345) == (("USDC",), {"USDC": 5_000})
    _mint_x402_grant(asset="SOL", decimals=9, fee_asset="SOL", per_operation=777_000)
    _mint_x402_grant(asset="SOL", decimals=9, fee_asset="SOL", per_operation=9_999_000, model="another-model")
    assert x402_payment_bounds(model_id=MODEL, route=route, usdc_route_bound_atomic=12_345) == (("USDC", "SOL"), {"USDC": 5_000, "SOL": 777_000})
    assert x402_payment_bounds(model_id=MODEL, route="centralized", usdc_route_bound_atomic=12_345) == (("USDC",), {"USDC": 12_345})


def test_a_used_consent_in_one_asset_does_not_shadow_a_fresh_consent_in_the_other(x402_rig) -> None:
    """Genuinely novel schedule: a single-payment USDC consent pays once, the owner then consents in SOL. The used
    consent stays listed as active (grants leave the list only when revoked or expired), so the bounds must come from
    the law's usage arithmetic, or the transport keeps choosing USDC and the reservation refuses the fresh consent."""
    from core.effect_budget_money import grant_headroom
    from core.usepod.money_law import x402_payment_bounds

    service, _authority, wallet, _token = x402_rig
    route = "key_relay+marketplace"
    usdc = _mint_x402_grant(per_operation=5_000_000)
    first = _exchange(service, wallet)
    assert first.response.status == 200 and first.option.asset == "USDC"
    assert grant_headroom(usdc)["operations_left"] == 0, "a single-payment consent authorizes exactly one operation"
    # only the used consent exists: it is still named, so a reservation refuses with the law's exhaustion code
    assert x402_payment_bounds(model_id=MODEL, route=route, usdc_route_bound_atomic=5_000_000) == (("USDC",), {"USDC": 5_000_000})
    sol = _mint_x402_grant(per_operation=900_000_000, asset="SOL", decimals=9)
    assert grant_headroom(sol)["operations_left"] == 1
    assets, bounds = x402_payment_bounds(model_id=MODEL, route=route, usdc_route_bound_atomic=5_000_000)
    assert (assets, bounds) == (("SOL",), {"SOL": 900_000_000})
    second = _exchange(service, wallet, body="Suggest a name for a harbour bakery.", assets=assets, bounds=bounds)
    assert second.response.status == 200 and second.option.asset == "SOL", second.option
    assert grant_headroom(sol)["operations_left"] == 0 and len(wallet.proof_calls) == 2


# --- one operation through uncertainty: resume with the recorded bytes and proof ------------------------------------


def test_a_lost_paid_answer_is_resumed_with_the_same_bytes_and_proof_and_is_never_paid_twice(x402_rig) -> None:
    from core.effect_budget_money import liability_for_operation
    from core.usepod.transport import DISPATCH_OUTCOME_UNKNOWN, X402_COMPLETED, X402_OUTCOME_UNKNOWN, X402OperationStateError

    service, _authority, wallet, _token = x402_rig
    _mint_x402_grant()
    service.faults["drop_paid_retry"] = True  # SIMULATION: the paid request reaches nobody who processes it
    with pytest.raises(UsePodTransportError) as lost:
        _exchange(service, wallet)
    service.faults.pop("drop_paid_retry")
    operation_id = wallet.proof_calls[-1]["operation_id"]
    client = UsePodX402Client(transport=UsePodHttpTransport())
    assert lost.value.dispatch_state == DISPATCH_OUTCOME_UNKNOWN, lost.value.dispatch_state
    assert (client.journal.get(operation_id)["state"], liability_for_operation(operation_id)["state"]) == (X402_OUTCOME_UNKNOWN, "unknown")
    listed = client.journal.unresolved_operations()
    assert [(item["operation_id"], item["resumable"]) for item in listed] == [(operation_id, 1)], listed
    assert "body" not in " ".join(sorted(listed[0])), "the listing carries public facts only"
    paid_path_before = len(service.requests_to("/proxy/x402/v1/chat/completions"))
    resumed = client.resume(operation_id, read_timeout_seconds=30.0)
    assert resumed.response.status == 200 and resumed.proof.operation_id == operation_id
    assert client.journal.get(operation_id)["state"] == X402_COMPLETED and client.journal.resume_record(operation_id) is None
    assert len(wallet.proof_calls) == 1, "the wallet was asked to pay once"
    assert len(service.requests_to("/proxy/x402/v1/chat/completions")) - paid_path_before == 1, "one resend, no new quote"
    with pytest.raises(X402OperationStateError) as again:
        client.resume(operation_id, read_timeout_seconds=30.0)
    assert again.value.code == "operation_not_unresolved"


def test_resume_never_takes_over_a_live_claim_and_finishes_once_the_claimant_has_ended(x402_rig, monkeypatch) -> None:
    from core.effect_budget_money import liability_for_operation, record_unknown
    from core.usepod.transport import X402_COMPLETED, X402_PROOF_BOUND, X402OperationStateError

    service, _authority, wallet, _token = x402_rig
    _mint_x402_grant()
    original = UsePodX402Client._send_paid

    def the_invocation_stops_here(self, *args, **kwargs):
        raise RuntimeError("the invocation stopped between the proof and the paid retry")

    monkeypatch.setattr(UsePodX402Client, "_send_paid", the_invocation_stops_here)
    with pytest.raises(RuntimeError):
        _exchange(service, wallet)
    monkeypatch.setattr(UsePodX402Client, "_send_paid", original)
    operation_id = wallet.proof_calls[-1]["operation_id"]
    client = UsePodX402Client(transport=UsePodHttpTransport())
    assert client.journal.get(operation_id)["state"] == X402_PROOF_BOUND
    with pytest.raises(X402OperationStateError) as live:
        client.resume(operation_id, read_timeout_seconds=30.0)
    assert (live.value.code, liability_for_operation(operation_id)["state"]) == ("resume_liability_not_unknown", "pending")
    # The claimant ends: the law's reconciliation of a dead instance performs exactly this transition, with its handle.
    liability_id = liability_for_operation(operation_id)["liability_id"]
    assert record_unknown(liability_id, wallet.proof_calls[-1]["claim_token"], reason="usepod:claimant_process_ended") == "unknown"
    resumed = client.resume(operation_id, read_timeout_seconds=30.0)
    assert resumed.response.status == 200 and client.journal.get(operation_id)["state"] == X402_COMPLETED
    assert len(wallet.proof_calls) == 1 and len(service.used_signatures) == 1


def test_a_resend_the_provider_had_already_served_keeps_the_liability_unknown_and_pays_nothing_more(x402_rig) -> None:
    from core.effect_budget_money import liability_for_operation
    from core.usepod.transport import X402_PAID_RETRY_FAILED, X402OperationStateError
    from tests.usepod._usepod_doubles import synthetic_signature

    service, _authority, wallet, _token = x402_rig
    _mint_x402_grant()
    service.faults["drop_paid_retry"] = True
    with pytest.raises(UsePodTransportError):
        _exchange(service, wallet)
    service.faults.pop("drop_paid_retry")
    operation_id = wallet.proof_calls[-1]["operation_id"]
    # SIMULATION: the provider had served and settled this signature before the answer was lost
    service.used_signatures.add(synthetic_signature(operation_id))
    client = UsePodX402Client(transport=UsePodHttpTransport())
    with pytest.raises(UsePodTransportError) as refused:
        client.resume(operation_id, read_timeout_seconds=30.0)
    assert refused.value.http_status == 409, (refused.value.code, refused.value.http_status)
    assert (client.journal.get(operation_id)["state"], liability_for_operation(operation_id)["state"]) == (X402_PAID_RETRY_FAILED, "unknown")
    assert len(wallet.proof_calls) == 1, "nothing is paid again"
    with pytest.raises(X402OperationStateError) as bounded:
        client.resume(operation_id, read_timeout_seconds=30.0)
    assert bounded.value.code == "paid_retry_attempts_exhausted"
