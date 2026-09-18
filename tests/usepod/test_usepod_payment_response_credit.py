"""An x402 PAYMENT-RESPONSE names the surplus the provider credited; the adapter carries it as UNVERIFIED provider
credit and keeps the receipt's fields as evidence. Local and SYNTHETIC only.

Found on 15 September 2026 while reading the live receipt of a completed paid call: the adapter waited for a decode
state the decoder never reports, so every surplus was dropped and the receipt said the provider reported none.
"""
from __future__ import annotations

import base64
import json

import pytest

from adapters.usepod_adapter import UsePodAdapter, call_receipt
from core.usepod import transport as tp
from core.usepod.monetary import SettlementEvidence
from tests.usepod.test_usepod_receipt_rendering import PAY_TO, PAYER, SOLANA_MAINNET, _evidence

CAP = 112


def _header(receipt: dict) -> str:
    return base64.b64encode(json.dumps(receipt).encode("utf-8")).decode("ascii")


def _exchange() -> tp.X402PaidExchange:
    option = tp.X402PaymentOption(0, "USDC", "exact", SOLANA_MAINNET, PAY_TO, CAP, "usdc_microunit", "cap-with-surplus-credit", (), ())
    return tp.X402PaidExchange(response=None, quote=None, option=option, proof=None, reservation=None, attempts=1)  # type: ignore[arg-type]


def _settlement() -> SettlementEvidence:
    return SettlementEvidence(
        operation_id="upo_synthetic", outcome="completed", http_status=200, usage={"prompt_tokens": 920, "completion_tokens": 27},
        input_tokens=920, output_tokens=27, upper_bound_cost_atomic=101, exact_cost_atomic=None, exact_cost_state="not_supplied_by_provider",
        route={"route_class": "centralized"}, balance_remaining_raw=None, usage_exceeds_liability_bound=False,
    )


def _receipt_for(header: str | None) -> dict:
    decoded = tp.decode_payment_response(header)
    settlement = UsePodAdapter._with_payment_credit(_settlement(), _exchange(), header)
    evidence = _evidence(
        transport_mode="x402", credential_fingerprint="", finish_reason="stop",
        route={"route_class": "centralized", "provider_id": "openrouter", "compliance": "compliant", "balance_remaining_raw": None},
        settlement=settlement.as_dict(),
        x402={
            "quote": {"quote_id": "q-usdc"},
            "proof": {"network": SOLANA_MAINNET, "asset": "USDC", "pay_to": PAY_TO, "amount_atomic": CAP, "payer_wallet": PAYER, "signature": "UsdcSigBase58Chars", "authority_label": "core.wallet.usepod_x402:v1"},
            "paid_option": {"network": SOLANA_MAINNET, "pay_to": PAY_TO, "asset": "USDC"},
            "wallet_outflow_atomic": CAP,
            "payment_response": decoded,
        },
    )
    return call_receipt(evidence)


def test_a_receipt_naming_a_surplus_becomes_unverified_provider_credit_and_its_fields_are_kept() -> None:
    header = _header({"quote_id": "q-usdc", "charged_microunits": 40, "surplus_credited_microunits": 72})
    settlement = UsePodAdapter._with_payment_credit(_settlement(), _exchange(), header)
    assert (settlement.provider_credit_atomic, settlement.provider_credit_account) == (72, PAY_TO)
    receipt = _receipt_for(header)
    assert (receipt["provider_credit"]["state"], receipt["provider_credit"]["atomic"], receipt["provider_credit"]["account"]) == ("credited", 72, PAY_TO)
    assert receipt["x402"]["payment_response_fields"] == {"quote_id": "q-usdc", "charged_microunits": 40, "surplus_credited_microunits": 72}
    assert receipt["x402"]["payment_response_state"] == "decoded_schema_unpublished"
    # the credit never touches what the wallet paid or what the usage bounds
    assert receipt["x402"]["wallet_outflow_atomic"] == CAP and receipt["cost"]["usage_upper_bound_atomic"] == 101


@pytest.mark.parametrize(
    ("receipt", "fields"),
    [
        pytest.param({"quote_id": "q-other", "charged_microunits": 112, "surplus_credited_microunits": 0}, {"quote_id": "q-other", "charged_microunits": 112, "surplus_credited_microunits": 0}, id="zero-surplus"),
        pytest.param({"quote_id": "q-other", "surplus_credited_microunits": -5}, {"quote_id": "q-other", "surplus_credited_microunits": -5}, id="negative-surplus"),
        pytest.param({"quote_id": "q-other", "surplus_credited_microunits": True}, {"quote_id": "q-other", "surplus_credited_microunits": True}, id="boolean-surplus"),
        pytest.param({"quote_id": "q-other", "surplus_credited_microunits": "72"}, {"quote_id": "q-other", "surplus_credited_microunits": "72"}, id="string-surplus"),
        pytest.param({"quote_id": "q-other", "settled": "yes"}, {"quote_id": "q-other", "settled": "yes"}, id="no-surplus-field"),
    ],
)
def test_a_receipt_that_names_no_positive_integer_surplus_credits_nothing_but_is_still_kept(receipt: dict, fields: dict) -> None:
    header = _header(receipt)
    settlement = UsePodAdapter._with_payment_credit(_settlement(), _exchange(), header)
    assert (settlement.provider_credit_atomic, settlement.provider_credit_account) == (None, "")
    rendered = _receipt_for(header)
    assert rendered["provider_credit"]["state"] == "not_reported_by_provider" and rendered["provider_credit"]["atomic"] is None
    assert rendered["x402"]["payment_response_fields"] == fields


@pytest.mark.parametrize(("header", "state"), [(None, "absent"), ("", "absent"), ("%%%", "undecodable"), (base64.b64encode(b"[1]").decode(), "not_an_object")])
def test_an_absent_or_unreadable_receipt_credits_nothing_and_says_why(header, state) -> None:
    settlement = UsePodAdapter._with_payment_credit(_settlement(), _exchange(), header)
    assert settlement.provider_credit_atomic is None
    rendered = _receipt_for(header)
    assert (rendered["x402"]["payment_response_state"], rendered["x402"]["payment_response_fields"]) == (state, None)
    assert rendered["provider_credit"]["state"] == "not_reported_by_provider"
