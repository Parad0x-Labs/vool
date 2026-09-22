"""What a payment is for, from typed facts only.

The preview, the pending card and the receipt say who receives what and why. The answer comes from the records the
operation's owner wrote -- the proposal's typed origin, the UsePod operation record, the x402 binding -- never from
the memo's wording and never from a guess:

* ``direct``  -- a transfer the owner or the model asked for: "Send 0.001 ETH to <address>" · Direct transfer.
* ``service`` -- an x402 payment for one resource: "Pay <provider> for <resource>" · Service payment · x402.
* ``credit``  -- a UsePod top-up: prepayment that becomes provider credit; an accepted payment is not a delivered
  service, and the line says so.
* ``unknown`` -- an origin without a record: shown as unknown, never dressed up.

A provider's description is the provider's own text, attributed as such: it is not proof of who the merchant is.
A contact label, when the Contacts owner lands, supplements the address through ``recipient_label`` /
``recipient_label_source``; it never replaces the address and it is never spend authority.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from core.wallet import amounts, chains, proposals

AUTHORITY = "core.wallet.purpose"

KIND_DIRECT = "direct"
KIND_SERVICE = "service"
KIND_CREDIT = "credit"
KIND_UNKNOWN = "unknown"

MECHANISM_DIRECT = "Direct transfer"
MECHANISM_X402 = "Service payment · x402"
MECHANISM_CREDIT = "Provider credit · UsePod top-up"

CHARGE_ONE_RESPONSE = "one paid response for this resource"
CHARGE_PREPAID_CREDIT = "prepayment: credit held by the provider, spent by later requests"
CHARGE_DIRECT = "this transfer only"

_DIRECT_ORIGINS = frozenset({proposals.ORIGIN_USER, proposals.ORIGIN_MODEL, proposals.ORIGIN_SKILL, proposals.ORIGIN_PLUGIN})
_MAX_TEXT = 200


def _amount_text(proposal: Any) -> str:
    try:
        spec = chains.resolve_network(proposal.network)
        asset = chains.asset_for(spec.network, proposal.asset)
        symbol = asset.symbol
        decimals = asset.decimals
        if asset.symbol.upper() == chains.native_asset(spec.network).symbol.upper():
            symbol = spec.native_display_symbol or asset.symbol
        return f"{amounts.format_minor(int(proposal.amount_minor), decimals)} {symbol}"
    except Exception:
        return f"{int(proposal.amount_minor)} {proposal.asset} (minor units)"


def _origin_of_url(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def _host_of_url(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    return parts.netloc or ""


def _base(kind: str, *, headline: str, mechanism_label: str, beneficiary: str, charge_scope: str) -> dict[str, Any]:
    return {
        "kind": kind, "headline": headline[:_MAX_TEXT], "mechanism_label": mechanism_label, "beneficiary": str(beneficiary or ""),
        "charge_scope": charge_scope, "provider": "", "provider_source": "", "resource": "", "resource_method": "",
        "description": "", "description_source": "", "recipient_label": "", "recipient_label_source": "",
        "note": "",
    }


def _direct(proposal: Any) -> dict[str, Any]:
    return _base(KIND_DIRECT, headline=f"Send {_amount_text(proposal)} to {proposal.destination}", mechanism_label=MECHANISM_DIRECT,
                 beneficiary=proposal.destination, charge_scope=CHARGE_DIRECT)


def _service(proposal: Any) -> dict[str, Any]:
    from core.wallet import x402

    binding = x402.binding_for_proposal(proposal.proposal_id)
    if binding is None:
        view = _base(KIND_SERVICE, headline=f"Pay {_amount_text(proposal)} to a provider whose request is not on record",
                     mechanism_label=MECHANISM_X402, beneficiary=proposal.destination, charge_scope=CHARGE_ONE_RESPONSE)
        view["note"] = "The provider's 402 request is not on record for this proposal; the resource and provider are unknown."
        return view
    url = str(binding.get("url") or "")
    origin = str(binding.get("resource_origin") or "") or _origin_of_url(url)
    provider = _host_of_url(origin or url) or "an unnamed provider"
    method = str(binding.get("resource_method") or binding.get("method") or "GET").upper()
    description = ""
    try:
        import json

        offer = json.loads(str(binding.get("offer_json") or "{}")) if binding.get("offer_json") else {}
        description = str((offer or {}).get("description") or (offer or {}).get("resource_description") or "")[:_MAX_TEXT]
    except (ValueError, TypeError, AttributeError):
        description = ""
    version = int(binding.get("version") or 1)
    view = _base(KIND_SERVICE, headline=f"Pay {provider} for {method} {url}" if url else f"Pay {provider} for a resource",
                 mechanism_label=f"{MECHANISM_X402} v{version}", beneficiary=str(binding.get("pay_to") or proposal.destination), charge_scope=CHARGE_ONE_RESPONSE)
    view.update({"provider": provider, "provider_source": "the resource's own origin (not a verified merchant identity)", "resource": url,
                 "resource_method": method, "description": description, "description_source": "the provider's own description (untrusted)" if description else ""})
    return view


def _usepod_payment(proposal: Any) -> dict[str, Any]:
    from core.wallet import usepod

    record = usepod.operation_for_proposal(proposal.proposal_id)
    if record is None or record.get("payment_kind") not in {usepod.KIND_CREDIT, usepod.KIND_RESPONSE}:
        view = _base(KIND_UNKNOWN, headline=f"Pay {_amount_text(proposal)} to UsePod — payment purpose unknown",
                     mechanism_label="Purpose unknown", beneficiary=proposal.destination, charge_scope="")
        view["note"] = "This operation does not record whether it buys provider credit or one response. Its memo cannot establish that purpose."
        return view
    provider = str(record.get("provider") or "an unnamed provider")
    resource = str(record.get("resource") or "")
    if record["payment_kind"] == usepod.KIND_RESPONSE:
        view = _base(KIND_SERVICE, headline=f"Pay {provider} for this response", mechanism_label=MECHANISM_X402,
                     beneficiary=str(record.get("pay_to") or proposal.destination), charge_scope=CHARGE_ONE_RESPONSE)
        view.update({"provider": provider, "provider_source": "the UsePod requirement's provider (not a verified merchant identity)",
                     "resource": resource, "note": "This payment covers one response, not prepaid credit for later requests."})
        return view
    view = _base(KIND_CREDIT, headline=f"Prepay {provider} credit" + (f" for {resource}" if resource else ""), mechanism_label=MECHANISM_CREDIT,
                 beneficiary=str(record.get("pay_to") or proposal.destination), charge_scope=CHARGE_PREPAID_CREDIT)
    view.update({"provider": provider, "provider_source": "the UsePod requirement's provider (not a verified merchant identity)", "resource": resource,
                 "note": "Payment accepted is not service delivered: this buys credit the provider spends on later requests."})
    return view


def purpose_for(proposal: Any) -> dict[str, Any]:
    """The typed purpose of one proposal. The memo is never consulted."""
    origin = str(getattr(proposal, "origin", "") or "")
    if origin in _DIRECT_ORIGINS:
        return _direct(proposal)
    if origin == proposals.ORIGIN_X402:
        return _service(proposal)
    if origin == proposals.ORIGIN_USEPOD:
        return _usepod_payment(proposal)
    view = _base(KIND_UNKNOWN, headline=f"Pay {_amount_text(proposal)} to {proposal.destination} — purpose unknown",
                 mechanism_label="Purpose unknown", beneficiary=proposal.destination, charge_scope="")
    view["note"] = f"No record explains this request (origin {origin or 'none'!r})."
    return view


def purpose_for_id(proposal_id: str) -> dict[str, Any] | None:
    proposal = proposals.get_proposal(proposal_id)
    return purpose_for(proposal) if proposal is not None else None


__all__ = [
    "CHARGE_DIRECT", "CHARGE_ONE_RESPONSE", "CHARGE_PREPAID_CREDIT", "KIND_CREDIT", "KIND_DIRECT", "KIND_SERVICE", "KIND_UNKNOWN",
    "MECHANISM_CREDIT", "MECHANISM_DIRECT", "MECHANISM_X402", "purpose_for", "purpose_for_id",
]
