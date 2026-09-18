"""The UsePod receipt rendered in the chat Activity panel: a working derivation, not raw JSON.

Executes the REAL chat-page script under node (tests/chat_page_js_harness.py boots it in a DOM
complete enough to run) and drives ``ledgerRow`` with the receipt shapes the adapter actually
emits (built here through the production ``call_receipt``), asserting the distinctions the
receipt itself makes survive rendering: an upper bound is never a charge, a missing balance is
never zero, a retained liability stays visible, and the snapshot/reservation/network/signature
links appear only where present.
"""
from __future__ import annotations

import json

from adapters.usepod_adapter import call_receipt
from tests.chat_page_js_harness import DOM, run_node, script

PAGE = script()


def _evidence(**overrides) -> dict:
    evidence = {
        "status": "completed",
        "operation_id": "op-1",
        "transport_mode": "prepaid_token",
        "protocol": "openai",
        "endpoint": "/proxy/{token}/v1/chat/completions",
        "credential_fingerprint": "upc_0123456789abcdef0123456789abcdef",
        "resolved_model": "meridian-synth-chat",
        "route_approval_id": "apr_123",
        "reservation_id": "res_1",
        "route_policy": {
            "approval_id": "apr_123",
            "routing_mode": "marketplace-only",
            "max_input_microunits_per_million": 510000,
            "max_output_microunits_per_million": 1530000,
            "price_source": {"snapshot_sha256": "a1b2c3d4e5f6"},
        },
        "route": {
            "route_raw": "marketplace",
            "route_class": "marketplace",
            "provider_id": "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a",
            "compliance": "compliant",
            "balance_remaining_raw": "79.980000",
            "balance_remaining_decimal": "79.980000",
            "balance_unit": "USDC",
        },
        "liability": {"asset": "USDC", "unit": "usdc_microunit", "max_amount_atomic": 990000},
        "settlement": {
            "outcome": "completed",
            "upper_bound_cost_atomic": 12345,
            "exact_cost_atomic": None,
            "exact_cost_state": "not_supplied_by_provider",
            "input_tokens": 21,
            "output_tokens": 13,
        },
        "reservation": {"reservation_id": "res_1", "authority_label": "test_double:served_journaling_monetary_authority"},
        "settlement_recording": "settled_with_evidence",
    }
    evidence.update(overrides)
    return evidence


def _render(events: list[dict]) -> dict:
    program = (
        DOM
        + "\n" + PAGE
        + """
out(ledgerRow(""" + json.dumps(events[0]) + """));
"""
    )
    return run_node(program)


def _details(event: dict) -> list:
    """The Activity item's detail lines for one event, from the page's own ``activityDetailLines``."""
    result = run_node(DOM + "\n" + PAGE + "\nout({ lines: activityDetailLines(" + json.dumps(event) + ") });\n")
    assert not result.get("errors"), result.get("errors")
    return [tuple(line) for line in result.get("lines") or []]


def test_a_completed_prepaid_receipt_renders_as_a_readable_line() -> None:
    receipt = call_receipt(_evidence())
    row = _render([{"event_type": "model.call_completed", "message": "Model call completed.", "provider_receipt": receipt, "model_id": "meridian-synth-chat"}])
    sub = str(row.get("sub") or "")
    assert "UsePod receipt" in sub
    # The route and its verdict.
    assert "route marketplace" in sub and "compliant" in sub
    # An upper bound is named as an upper bound, never as the charge.
    assert "up to 0.012345 USDC" in sub and "upper bound, not the charge" in sub
    assert "exact charge not supplied by provider" in sub
    # The balance is the reported observation.
    assert "balance 79.980000 USDC after the call" in sub
    assert "settled with evidence" in sub
    # The correlation links, only where present.
    assert "snapshot a1b2c3d4e5" in sub and "reservation res_1" in sub and "approval apr_123" in sub
    assert "money: test_double:served_journaling_monetary_authority" in sub
    # Raw JSON is not the rendering.
    assert '{"schema"' not in sub


def test_a_retained_liability_stays_visible_and_a_missing_balance_is_not_zero() -> None:
    evidence = _evidence(
        status="failed",
        settlement={"outcome": "outcome_unknown", "upper_bound_cost_atomic": None, "exact_cost_atomic": None, "exact_cost_state": "not_supplied_by_provider"},
        settlement_recording="retained_unknown",
        route={"route_class": "marketplace", "provider_id": "p", "compliance": "unverified", "balance_remaining_raw": None},
    )
    receipt = call_receipt(evidence)
    row = _render([{"event_type": "model.call_failed", "message": "Model call failed.", "provider_receipt": receipt}])
    sub = str(row.get("sub") or "")
    assert "LIABILITY RETAINED — outcome unknown" in sub
    assert "balance not reported by the provider" in sub
    assert "balance 0" not in sub
    # The liability bound is still stated (usage was not reported).
    assert "bounded at 0.990000 USDC" in sub


def test_an_x402_receipt_names_network_and_signature_only_when_present() -> None:
    evidence = _evidence(
        transport_mode="x402",
        protocol="anthropic",
        endpoint="/proxy/x402/v1/messages",
        x402={"quote": {"quote_id": "q-9"}, "proof": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "signature": "SigBase58NineChars", "authority_label": "wallet:test"},
               "paid_option": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"}, "wallet_outflow_atomic": 500000},
    )
    receipt = call_receipt(evidence)
    row = _render([{"event_type": "model.call_completed", "message": "Model call completed.", "provider_receipt": receipt}])
    sub = str(row.get("sub") or "")
    assert "network solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" in sub
    assert "signature SigBase58" in sub
    # Without x402 evidence the prepaid line names neither.
    prepaid = call_receipt(_evidence())
    sub2 = str(_render([{"event_type": "model.call_completed", "provider_receipt": prepaid}]).get("sub") or "")
    assert "network" not in sub2 and "signature" not in sub2


def test_an_exact_charge_is_rendered_as_the_charge() -> None:
    receipt = call_receipt(_evidence(settlement={"outcome": "completed", "upper_bound_cost_atomic": 12345, "exact_cost_atomic": 9900, "exact_cost_state": "reported_by_provider", "input_tokens": 21, "output_tokens": 13}))
    row = _render([{"event_type": "model.call_completed", "provider_receipt": receipt}])
    sub = str(row.get("sub") or "")
    assert "charged 0.009900 USDC exactly" in sub
    assert "upper bound" not in sub


def test_a_row_without_a_usepod_receipt_renders_without_one() -> None:
    row = _render([{"event_type": "model.call_completed", "message": "Model call completed.", "model_id": "qwen3:4b"}])
    sub = str(row.get("sub") or "")
    assert "UsePod receipt" not in sub


def test_an_accountless_sol_receipt_renders_each_amount_in_its_own_unit() -> None:
    evidence = _evidence(
        transport_mode="x402",
        endpoint="/proxy/x402/v1/chat/completions",
        credential_fingerprint="",
        liability={"asset": "SOL", "unit": "lamport", "max_amount_atomic": 18662},
        settlement={"outcome": "completed", "upper_bound_cost_atomic": 31, "exact_cost_atomic": None, "exact_cost_state": "not_supplied_by_provider", "input_tokens": 21, "output_tokens": 13},
        route={"route_class": "marketplace", "provider_id": "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a", "compliance": "compliant", "balance_remaining_raw": None},
        x402={
            "quote": {"quote_id": "q-sol"},
            "proof": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "signature": "SolSigBase58Chars", "authority_label": "core.wallet.usepod_x402:v1"},
            "paid_option": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"},
            "wallet_outflow_atomic": 18662,
            "chain_confirmation": {"state": "recorded", "signature": "SolSigBase58Chars", "wallet_outflow_atomic": 18662, "network_fee_atomic": 5000, "fee_asset": "SOL"},
        },
    )
    receipt = call_receipt(evidence)
    assert (receipt["pricing_unit"], receipt["cost"]["unit"]) == ("usdc_microunit", "lamport")
    sub = str(_render([{"event_type": "model.call_completed", "provider_receipt": receipt}]).get("sub") or "")
    # the paid principal and the fee in SOL; the usage estimate in the USDC it was priced in, never relabelled as lamports
    assert "paid 0.000018662 SOL from your wallet" in sub and "network fee 0.000005000 SOL" in sub and "chain-confirmed" in sub, sub
    assert "up to 0.000031 USDC" in sub and "0.000000031 SOL" not in sub, sub
    # an accountless call has no provider account, so there is no balance line at all
    assert "balance" not in sub, sub
    assert "signature SolSigBase" in sub


def test_an_accountless_usdc_receipt_without_a_chain_read_says_so() -> None:
    evidence = _evidence(
        transport_mode="x402",
        credential_fingerprint="",
        route={"route_class": "marketplace", "provider_id": "p", "compliance": "compliant", "balance_remaining_raw": None},
        x402={
            "quote": {"quote_id": "q-usdc"},
            "proof": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "signature": "UsdcSigBase58Chars", "authority_label": "core.wallet.usepod_x402:v1"},
            "paid_option": {"network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"},
            "wallet_outflow_atomic": 2625,
            "chain_confirmation": {"state": "not_recorded_provider_settlement_missing"},
        },
    )
    sub = str(_render([{"event_type": "model.call_completed", "provider_receipt": call_receipt(evidence)}]).get("sub") or "")
    assert "paid up to 0.002625 USDC from your wallet (chain confirmation not recorded provider settlement missing)" in sub, sub
    assert "chain-confirmed" not in sub


# --- the payment transaction, the inference and provider credit, and the receipt's details ----------------------------

PAY_TO = "PayToAccount" + "1" * 32
PAYER = "PayerWa11et" + "1" * 33
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


def test_an_accountless_receipt_keeps_the_transaction_the_inference_and_provider_credit_apart() -> None:
    evidence = _evidence(
        transport_mode="x402",
        credential_fingerprint="",
        finish_reason="stop",
        route={"route_class": "marketplace", "provider_id": "p", "compliance": "compliant", "balance_remaining_raw": None},
        settlement={
            "outcome": "completed",
            "upper_bound_cost_atomic": 2600,
            "exact_cost_atomic": None,
            "exact_cost_state": "not_supplied_by_provider",
            "input_tokens": 21,
            "output_tokens": 13,
            "provider_credit_atomic": 23,
            "provider_credit_account": PAY_TO,
        },
        x402={
            "quote": {"quote_id": "q-usdc"},
            "proof": {"network": SOLANA_MAINNET, "asset": "USDC", "pay_to": PAY_TO, "amount_atomic": 2623, "payer_wallet": PAYER, "signature": "UsdcSigBase58Chars", "authority_label": "core.wallet.usepod_x402:v1"},
            "paid_option": {"network": SOLANA_MAINNET, "pay_to": PAY_TO, "asset": "USDC"},
            "wallet_outflow_atomic": 2623,
            "chain_confirmation": {"state": "recorded", "signature": "UsdcSigBase58Chars", "wallet_outflow_atomic": 2623, "network_fee_atomic": 5000, "fee_asset": "SOL"},
        },
    )
    receipt = call_receipt(evidence)
    assert (receipt["x402"]["transaction_state"], receipt["inference"]["state"], receipt["provider_credit"]["state"]) == ("confirmed", "completed", "credited"), receipt
    event = {"event_type": "model.call_completed", "provider_receipt": receipt}
    sub = str(_render([event]).get("sub") or "")
    assert "transaction confirmed on chain" in sub and "inference completed (stop)" in sub, sub
    assert "provider credit 0.000023 USDC on the provider account, not a wallet refund" in sub, sub
    assert "RESULT UNKNOWN" not in sub, sub
    details = dict(_details(event))
    assert (details["paid to"], details["paid from wallet"], details["payment network"]) == (PAY_TO, PAYER, SOLANA_MAINNET), details
    assert details["payment amount (exact)"] == "2623 usdc_microunit (USDC)", details
    assert (details["wallet outflow on chain (exact)"], details["network fee on chain (exact)"]) == ("2623 usdc_microunit", "5000 lamport"), details
    assert details["provider credit (exact)"] == "23 usdc_microunit to " + PAY_TO, details
    assert (details["payment transaction"], details["inference"]) == ("UsdcSigBase58Chars", "completed"), details


def test_a_paid_call_whose_answer_never_arrived_says_paid_result_unknown_with_the_full_destination() -> None:
    evidence = _evidence(
        status="sent_outcome_unknown",
        transport_mode="x402",
        credential_fingerprint="",
        route=None,
        settlement=None,
        settlement_recording="retained_unknown",
        transport_evidence={
            "payment_retained": True,
            "paid_attempts": 1,
            "x402_operation_id": "op-1",
            "x402_payment": {"quote_id": "q-lost", "network": SOLANA_MAINNET, "asset": "USDC", "pay_to": PAY_TO, "amount_atomic": 2623, "payer_wallet": PAYER, "signature": "LostSigBase58Chars", "authority_label": "core.wallet.usepod_x402:v1"},
        },
    )
    receipt = call_receipt(evidence)
    assert (receipt["x402"]["transaction_state"], receipt["inference"]["state"], receipt["provider_credit"]["state"]) == ("confirmed", "result_unknown", "not_reported_by_provider"), receipt
    event = {"event_type": "model.call_failed", "message": "Model call failed.", "provider_receipt": receipt}
    sub = str(_render([event]).get("sub") or "")
    assert "PAID, RESULT UNKNOWN" in sub and "no refund is assumed" in sub, sub
    assert "transaction confirmed on chain" in sub and "inference result unknown" in sub and "LIABILITY RETAINED" in sub, sub
    assert "paid up to 0.002623 USDC from your wallet" in sub, sub
    details = dict(_details(event))
    assert (details["paid to"], details["payment transaction"], details["inference"]) == (PAY_TO, "LostSigBase58Chars", "result_unknown"), details


def test_a_prepaid_receipt_names_the_inference_and_no_wallet_transaction() -> None:
    receipt = call_receipt(_evidence(finish_reason="stop"))
    assert (receipt["inference"]["state"], receipt["provider_credit"]["state"]) == ("completed", "not_applicable"), receipt
    event = {"event_type": "model.call_completed", "provider_receipt": receipt}
    sub = str(_render([event]).get("sub") or "")
    assert "inference completed (stop)" in sub and "transaction" not in sub and "provider credit" not in sub, sub
    details = dict(_details(event))
    assert details["usepod account"] == "upc_0123456789abcdef0123456789abcdef" and "paid to" not in details, details


def test_balance_header_with_no_verified_unit_is_not_named_usdc():
    evidence = _evidence()
    evidence["route"].pop("balance_unit")
    sub = _render([{"event_type": "model.call_completed", "provider_receipt": call_receipt(evidence)}])["sub"]
    assert "unit is unverified" in sub
    assert "balance 79.980000 USDC" not in sub
