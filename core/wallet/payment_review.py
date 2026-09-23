"""The compact payment review: the words and figures the approval sheet shows ABOVE the fold, and the rows it keeps
under ``View details``, composed here from the typed quote so the page never does money arithmetic or invents names.

Every figure is the quote's integer, formatted by :func:`core.wallet.amounts.format_minor` (exact decimal, never a
binary float). Maximum debits are per asset and never summed across assets; an amount owed for later collection is
never presented as money leaving now. Recipient names come only from bound payment facts: a provider payment names
the provider the quote was minted for, a fee collection names the DNA treasury, and an unidentified recipient stays
an unidentified wallet with its shortened address. No conversion to fiat is ever shown: no trusted fresh rate exists
on this surface.
"""
from __future__ import annotations

from typing import Any

from core.wallet import amounts, proposals

AUTHORITY = "core.wallet.payment_review"
PROVIDER_LABELS = {proposals.ORIGIN_USEPOD: "UsePod"}


def short_address(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 14 else f"{text[:8]}…{text[-4:]}"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _human(minor: Any, decimals: Any) -> str:
    return amounts.format_minor(_int(minor), _int(decimals))


def _max_debits(fields: dict[str, Any], companion: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Maximum wallet debit now, per asset: the principal (plus a collected amount in the same asset) and the network
    cost ceiling of every transaction in the gas asset. A token payment never adds its SOL fee to its USDC amount."""
    asset, gas = str(fields.get("display_symbol") or fields.get("asset") or ""), str(fields.get("gas_asset") or "")
    decimals, fee_decimals = _int(fields.get("decimals")), _int(fields.get("fee_decimals", fields.get("decimals")))
    principal = _int(fields.get("amount_minor"))
    gas_max = _int(fields.get("fee_max_minor"))
    rows: list[dict[str, Any]] = []
    if companion:
        if str(companion.get("asset") or "") == asset:
            principal += _int(companion.get("amount_minor"))
        gas_max += _int(companion.get("fee_max_minor"))
    if fields.get("token_transfer"):
        rows.append({"asset": asset, "minor": principal, "human": _human(principal, decimals), "what": "principal" + (" + fees collected" if companion else "")})
        rows.append({"asset": gas, "minor": gas_max, "human": _human(gas_max, fee_decimals), "what": "network cost, maximum" + (" for both transactions" if companion else "")})
    else:
        total = principal + gas_max
        rows.append({"asset": gas or asset, "minor": total, "human": _human(total, decimals), "what": "principal + network cost, maximum" + (" for both transactions" if companion else "")})
    return rows


def _remaining(fields: dict[str, Any], companion: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Estimated remaining balances after, per asset, rounded DOWN by construction (the maximum debit is subtracted,
    never the estimate), labelled as estimates."""
    asset, gas = str(fields.get("display_symbol") or fields.get("asset") or ""), str(fields.get("gas_asset") or "")
    decimals, fee_decimals = _int(fields.get("decimals")), _int(fields.get("fee_decimals", fields.get("decimals")))
    rows: list[dict[str, Any]] = []
    extra_principal = _int(companion.get("amount_minor")) if companion and str(companion.get("asset") or "") == asset else 0
    extra_gas = _int(companion.get("fee_max_minor")) if companion else 0
    if fields.get("token_transfer"):
        principal_left = _int(fields.get("principal_balance_minor")) - _int(fields.get("held_elsewhere_minor")) - _int(fields.get("amount_minor")) - extra_principal
        gas_left = _int(fields.get("fee_balance_minor")) - _int(fields.get("held_native_elsewhere_minor")) - _int(fields.get("fee_max_minor")) - extra_gas
        rows.append({"asset": asset, "minor": principal_left, "human": _human(principal_left, decimals), "label": "at least, after the maximum debit"})
        rows.append({"asset": gas, "minor": gas_left, "human": _human(gas_left, fee_decimals), "label": "at least, if every network cost reaches its maximum"})
    else:
        left = _int(fields.get("minimum_after_minor")) - extra_principal - extra_gas
        rows.append({"asset": gas or asset, "minor": left, "human": _human(left, decimals), "label": "at least, if the network cost reaches its maximum"})
    return rows


def compose(fields: dict[str, Any], proposal: Any) -> dict[str, Any]:
    """The review block for one quote: heading, always-visible lines, warnings, the primary action, the per-asset
    maxima and remaining balances, the finite list of actions the approval covers, and the detail rows."""
    origin = str(getattr(proposal, "origin", "") or "")
    asset = str(fields.get("display_symbol") or fields.get("asset") or "")
    gas = str(fields.get("gas_asset") or asset)
    amount_human = str(fields.get("amount_human") or "")
    companion = fields.get("companion") if isinstance(fields.get("companion"), dict) else None
    fee = fields.get("dna_fee") if isinstance(fields.get("dna_fee"), dict) else None
    to_address = str(fields.get("to_address") or "")
    warnings: list[dict[str, str]] = []
    if origin == proposals.ORIGIN_USEPOD:
        from core.wallet import purpose

        payment_purpose = fields.get("purpose") or {}
        provider = PROVIDER_LABELS.get(origin, "the provider")
        if payment_purpose.get("kind") == purpose.KIND_SERVICE:
            headline = f"Pay {provider} for this AI response"
            amount_line = f"Pay at most {amount_human} {asset} (the provider's cap for this response; the final usage bill can be lower, never higher)"
        elif payment_purpose.get("kind") == purpose.KIND_CREDIT:
            headline = "Prepay provider credit"
            amount_line = f"Prepay {amount_human} {asset} (provider credit for later requests)"
            warnings.append({"code": "prepaid_credit", "text": str(payment_purpose.get("note") or "Payment accepted is not service delivered.")})
        else:
            headline = "Payment purpose unknown"
            amount_line = f"Pay {amount_human} {asset} (the operation does not record what this payment buys)"
            warnings.append({"code": "unknown_purpose", "text": str(payment_purpose.get("note") or "The payment purpose is not on record.")})
        recipient_line = f"To {provider}'s payment account {to_address}"
        recipient_kind = "provider_pay_to"
        primary = "Approve and pay"
    elif origin == proposals.ORIGIN_DNA_FEE:
        headline = "Pay accrued DNA service fees to the treasury"
        amount_line = f"Pay {amount_human} {asset} of fees already owed"
        recipient_line = f"To the DNA treasury {to_address}"
        recipient_kind = "dna_treasury"
        primary = "Approve and send"
    else:
        headline = f"Send {amount_human} {asset}"
        amount_line = f"Send {amount_human} {asset} (exact amount)"
        if fields.get("recipient_saved") and str(fields.get("recipient_label") or "").strip():
            recipient_line = f"To {fields['recipient_label']} · {short_address(to_address)} (saved contact; the full address is under View details)"
            recipient_kind = "saved_contact"
        else:
            recipient_line = f"To wallet {short_address(to_address)} (not a saved contact; the full address is under View details)"
            recipient_kind = "unidentified_wallet"
        primary = "Approve and send"
    source_line = f"From {fields.get('from_label') or 'your wallet'!s} {short_address(fields.get('from_address'))}"
    fee_line = ""
    collection_line = ""
    if fee:
        rate = str(fee.get("rate_label") or "0.1%")
        fee_line = f"DNA fee: {rate} — accumulates until economical to collect ({fee.get('fee_exact')} {fee.get('asset')} on this payment, owed once the provider accepts it)"
        if str(fee.get("treasury_source") or "") == "operator_override_disposable_recipient":
            warnings.append({"code": "treasury_override", "text": f"DNA treasury is an OPERATOR OVERRIDE (disposable recipient) {fee.get('treasury_owner')}, not the designated owner."})
        if companion:
            collection_line = f"Previously accrued fees collected now: {companion.get('amount_human')} {companion.get('asset')} to the DNA treasury (a second transaction under this approval)"
        else:
            reason = str(((fee.get("collection") or {}).get("reason")) or "not_planned").replace("_", " ")
            collection_line = f"No fees are collected with this payment ({reason}); {fee.get('collectible_now_exact')} {fee.get('asset')} stays accrued"
    payment_fee_max = _human(fields.get("fee_max_minor"), fields.get("fee_decimals", fields.get("decimals")))
    if companion:
        both = _human(_int(fields.get("fee_max_minor")) + _int(companion.get("fee_max_minor")), fields.get("fee_decimals", fields.get("decimals")))
        network_line = f"Network fee: at most {both} {gas} for both transactions (payment at most {payment_fee_max} {gas}, fee collection at most {_human(companion.get('fee_max_minor'), companion.get('fee_decimals'))} {gas})"
    else:
        network_line = f"Network fee: at most {payment_fee_max} {gas} (estimated {_human(fields.get('fee_estimate_minor'), fields.get('fee_decimals', fields.get('decimals')))} {gas})"
    if fields.get("balance_stale"):
        warnings.append({"code": "stale_quote", "text": "The balance this preview shows was not read just now; refresh before approving."})
    for cue in fields.get("cues") or []:
        if cue == "new_recipient_account":
            warnings.append({"code": cue, "text": "The recipient account does not exist yet; this transfer creates it."})
        elif cue == "non_system_owner_recipient":
            warnings.append({"code": cue, "text": "The recipient account is owned by a program, not a plain wallet."})
    actions: list[dict[str, Any]] = [{
        "kind": "provider_payment" if origin == proposals.ORIGIN_USEPOD else ("fee_collection" if origin == proposals.ORIGIN_DNA_FEE else "transfer"),
        "proposal_id": str(getattr(proposal, "proposal_id", "") or ""), "to": to_address, "amount_minor": _int(fields.get("amount_minor")), "asset": asset,
        "network_fee_max_minor": _int(fields.get("fee_max_minor")), "gas_asset": gas,
        "authorization": "this approval, exactly as shown; one signature, one send",
    }]
    if companion:
        actions.append({
            "kind": "fee_collection", "proposal_id": str(companion.get("proposal_id") or ""), "to": str(companion.get("treasury_owner") or ""),
            "amount_minor": _int(companion.get("amount_minor")), "asset": str(companion.get("asset") or ""), "network_fee_max_minor": _int(companion.get("fee_max_minor")),
            "gas_asset": str(companion.get("gas_asset") or gas), "collection_id": str(companion.get("collection_id") or ""), "state_version": _int(companion.get("state_version")),
            "authorization": "this same approval; a second signature in the same signing session, sent only after the payment left",
        })
    details: list[list[str]] = [
        ["Payer address", str(fields.get("from_address") or "")],
        ["Recipient address", to_address],
    ]
    if fields.get("token_transfer"):
        details.append(["Recipient token account", str(fields.get("recipient_token_account") or "")])
        details.append(["Payer token account", str(fields.get("payer_token_account") or "")])
        details.append(["Token mint", str(fields.get("mint") or "")])
    details.append(["Exact amount", f"{amount_human} {asset} = {_int(fields.get('amount_minor'))} atomic units"])
    details.append(["Network fee bound", f"estimated {_human(fields.get('fee_estimate_minor'), fields.get('fee_decimals', fields.get('decimals')))} {gas} · at most {payment_fee_max} {gas} = {_int(fields.get('fee_max_minor'))} atomic units"])
    fee_parts = fields.get("fee_parts") or {}
    for key, label in (
        ("base_minor", "Base network fee"),
        ("execution_estimate_minor", "Execution fee estimate"),
        ("execution_max_minor", "Execution fee maximum"),
        ("l1_estimate_minor", "L1 data fee estimate"),
        ("l1_ceiling_minor", "L1 data fee maximum"),
        ("operator_minor", "Operator fee"),
    ):
        if key in fee_parts:
            details.append([label, f"{_human(fee_parts[key], fields.get('fee_decimals', fields.get('decimals')))} {gas} = {_int(fee_parts[key])} atomic units"])
    if fee:
        details.append(["DNA fee numerator", f"{fee.get('fee_numerator')} / 10000 atomic units = {fee.get('fee_exact_atomic')} atomic ({fee.get('fee_exact')} {fee.get('asset')})"])
        details.append(["DNA fee reserved ceiling", f"{fee.get('fee_reserved_ceiling_atomic')} atomic units ({fee.get('fee_reserved_ceiling_exact')} {fee.get('asset')})"])
        details.append(["DNA fees accrued so far", f"{fee.get('accrued_before_exact')} {fee.get('asset')} (carry {fee.get('carry_before_numerator')} / 10000) · after this payment {fee.get('accrued_after_exact')} {fee.get('asset')}"])
        details.append(["DNA fee policy", f"{fee.get('policy_id')} · treasury {fee.get('treasury_owner')} ({str(fee.get('treasury_source') or '').replace('_', ' ')})"])
        details.append(["Collection threshold", f"{fee.get('collect_min_exact')} {fee.get('asset')} ({str(fee.get('collect_min_source') or '').replace('_', ' ')}) · cost bound {((fee.get('collection') or {}).get('cost_bound_bps'))} bps"])
    if companion:
        econ = companion.get("economics") if isinstance(companion.get("economics"), dict) else {}
        details.append(["Fee collection", f"{companion.get('collection_id')} v{companion.get('state_version')} · {companion.get('amount_human')} {companion.get('asset')} = {_int(companion.get('amount_minor'))} atomic · treasury {companion.get('treasury_owner')}"])
        if companion.get("treasury_token_account"):
            details.append(["Treasury token account", str(companion.get("treasury_token_account"))])
        details.append(["Collection network fee bound", f"at most {_human(companion.get('fee_max_minor'), companion.get('fee_decimals'))} {companion.get('gas_asset')} = {_int(companion.get('fee_max_minor'))} atomic units"])
        details.append(["Collection cost check", f"cost {econ.get('cost_in_collected_asset_atomic')} atomic {companion.get('asset')} under the operator's conversion bound = {econ.get('cost_bps_of_amount')} bps of the amount (bound {econ.get('cost_bound_bps')} bps)"])
        details.append(["Collection offer expires", str(companion.get("expires_at") or "")])
    details.append(["Quote", f"expires at {fields.get('expires_at')} · balance observed at {fields.get('balance_ref')}"])
    for index, action in enumerate(actions, start=1):
        details.append([f"Action {index}: {str(action['kind']).replace('_', ' ')}", f"{action['amount_minor']} atomic {action['asset']} to {action['to']} · network fee at most {action['network_fee_max_minor']} atomic {action['gas_asset']} · {action['authorization']}"])
    return {
        "headline": headline,
        "amount_line": amount_line,
        "recipient_line": recipient_line,
        "recipient_kind": recipient_kind,
        "source_line": source_line,
        "badge": str(fields.get("badge") or ""),
        "environment": str(fields.get("environment") or ""),
        "network_name": str(fields.get("display_name") or ""),
        "fee_line": fee_line,
        "collection_line": collection_line,
        "network_line": network_line,
        "max_debits": _max_debits(fields, companion),
        "remaining": _remaining(fields, companion),
        "warnings": warnings,
        "primary_action": primary,
        "actions": actions,
        "details": details,
    }


__all__ = ["AUTHORITY", "PROVIDER_LABELS", "compose", "short_address"]
