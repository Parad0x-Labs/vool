"""x402 v2: one typed parser, one chain-dispatched builder, deterministic offer selection.

Wire facts are pinned to the official specification (x402-foundation/x402,
``specs/x402-specification-v2.md`` @ 78412bce, v2.0 dated 2025-12-9):

* the 402 carries ``PAYMENT-REQUIRED`` — base64 JSON ``{x402Version: 2, error?, resource:
  {url, ...}, accepts[], extensions?}``;
* a requirements entry is ``{scheme, network (CAIP-2), amount, asset, payTo,
  maxTimeoutSeconds, extra?}`` — ``amount`` is the v2 name of v1's ``maxAmountRequired``;
* the client answers with ``PAYMENT-SIGNATURE`` — base64 JSON ``{x402Version: 2, resource?,
  accepted: <the chosen requirements>, payload: <scheme-specific>}``;
* settlement rides ``PAYMENT-RESPONSE`` — ``{success, errorReason?, payer?, transaction,
  network, ...}``.

A v2 offer is never silently downgraded and a v1 payment is never generated for a v2
requirement; the v1 path (the finished Solana devnet lane) keeps its own bounded reader in
:mod:`core.wallet.x402`. Offer selection evaluates EVERY ``accepts[]`` entry against the
wallet's declared networks, registered assets, schemes, facilitator and cap — deterministically
— and returns a reasoned selection or refusal receipt that never logs opaque authorization
material.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from core.wallet import chains, config, custody, evm, proposals
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.x402_v2"

X402_VERSION = 2
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
HEADER_PAYMENT_RESPONSE = "PAYMENT-RESPONSE"
#: lowercase forms the confinement layer's normalized header dict is keyed by
_L_PAYMENT_REQUIRED = HEADER_PAYMENT_REQUIRED.lower()

SCHEME_EXACT = "exact"

OUTCOME_PAYMENT_REQUIRED = "payment_required"
OUTCOME_DELIVERED = "delivered"
OUTCOME_REFUSED = "refused"


@dataclass(frozen=True)
class V2Requirements:
    """One ``accepts[]`` entry, typed. Everything the wallet judges rides here."""

    scheme: str
    network: str
    amount_minor: int
    asset: str
    pay_to: str
    max_timeout_seconds: int = 60
    description: str = ""
    resource_url: str = ""
    resource_method: str = "GET"
    eip712_name: str = ""
    eip712_version: str = ""
    asset_transfer_method: str = "eip3009"
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "network": self.network, "amount_minor": self.amount_minor, "asset": self.asset,
            "pay_to": self.pay_to, "max_timeout_seconds": self.max_timeout_seconds, "description": self.description,
            "eip712_name": self.eip712_name, "eip712_version": self.eip712_version,
            "asset_transfer_method": self.asset_transfer_method,
        }


@dataclass(frozen=True)
class V2Offer:
    """A parsed 402: the resource plus every admissible requirements entry."""

    resource_url: str
    resource_description: str
    accepts: tuple[V2Requirements, ...]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"resource_url": self.resource_url, "resource_description": self.resource_description, "error": self.error, "accepts": [a.to_dict() for a in self.accepts]}


def _as_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def _requirements_from(entry: Any, resource_url: str) -> V2Requirements | None:
    if not isinstance(entry, dict):
        return None
    try:
        amount = int(str(entry.get("amount") or "0").strip())
    except (TypeError, ValueError):
        return None
    pay_to = str(entry.get("payTo") or "").strip()
    network = str(entry.get("network") or "").strip()
    asset = str(entry.get("asset") or "").strip()
    scheme = str(entry.get("scheme") or "").strip()
    if amount <= 0 or not pay_to or not network or not asset or not scheme:
        return None
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    resource = entry.get("resource")
    url = resource.strip() if isinstance(resource, str) and resource.strip() else resource_url
    return V2Requirements(
        scheme=scheme, network=network, amount_minor=amount, asset=asset, pay_to=pay_to,
        max_timeout_seconds=int(entry.get("maxTimeoutSeconds") or 60),
        description=str(entry.get("description") or "")[:200],
        resource_url=url[:500], resource_method=str(entry.get("method") or "GET").upper(),
        eip712_name=str(extra.get("name") or ""), eip712_version=str(extra.get("version") or ""),
        asset_transfer_method=str(extra.get("assetTransferMethod") or "eip3009"),
        raw=dict(entry),
    )


def parse_payment_required(headers: dict[str, Any] | None, body: Any = None, *, status: int | None = None) -> V2Offer | None:
    """The ONE v2 parser. A well-formed v2 402 becomes a V2Offer; anything else — a wrong
    version, malformed JSON, no usable accepts[] — is None (never a guess)."""
    if status is not None:
        try:
            if int(status) != 402:
                return None
        except (TypeError, ValueError):
            return None
    candidate: dict[str, Any] | None = None
    for name, value in (headers or {}).items():
        if str(name).lower() == _L_PAYMENT_REQUIRED:
            raw = value
            if isinstance(raw, str) and raw.strip():
                try:
                    raw = base64.b64decode(raw.strip())
                except Exception:
                    candidate = None
                    continue
            candidate = _as_json(raw)
            if candidate is not None:
                break
    if candidate is None:
        parsed = _as_json(body)
        if parsed is not None and int(parsed.get("x402Version") or 0) == X402_VERSION:
            candidate = parsed
    if candidate is None or int(candidate.get("x402Version") or 0) != X402_VERSION:
        return None
    resource = candidate.get("resource")
    resource_url = ""
    description = ""
    if isinstance(resource, dict):
        resource_url = str(resource.get("url") or "")
        description = str(resource.get("description") or "")[:200]
    elif isinstance(resource, str):
        resource_url = resource
    accepts_raw = candidate.get("accepts")
    if not isinstance(accepts_raw, list) or not accepts_raw:
        return None
    accepts = tuple(filter(None, (_requirements_from(e, resource_url) for e in accepts_raw)))
    if not accepts:
        return None
    return V2Offer(resource_url=resource_url[:500], resource_description=description, accepts=accepts, error=str(candidate.get("error") or "")[:200])


def select_offer(offer: V2Offer, *, wallet_id: str, source_context: dict[str, Any] | None = None) -> tuple[V2Requirements | None, str]:
    """Deterministic capability selection over EVERY entry. The first admissible entry in
    the offer's own order wins; identical inputs pick the same entry; the returned reason
    names why nothing was chosen without quoting opaque authorization material."""
    cap = config.x402_cap_minor()
    domain_conflicts = False
    for entry in offer.accepts:
        try:
            spec = chains.resolve_network(entry.network)
        except Exception:
            continue
        try:
            asset = chains.asset_for(entry.network, entry.asset)
        except Exception:
            continue
        if entry.scheme != SCHEME_EXACT or SCHEME_EXACT not in spec.schemes:
            continue
        if entry.amount_minor > cap:
            continue
        if spec.is_evm:
            if entry.asset_transfer_method == evm.TRANSFER_METHOD_PERMIT2:
                continue
            if entry.asset_transfer_method != evm.TRANSFER_METHOD_EIP3009:
                continue
            if not asset.address:
                continue
            if not (entry.eip712_name and entry.eip712_version) and not (asset.eip712_name and asset.eip712_version):
                continue  # an unpinned EIP-712 domain is not an offer we can sign honestly
            if asset.eip712_name and asset.eip712_version:
                # a PINNED asset is the domain authority: an offer that names a different
                # name or version is domain-confused or hostile (the signature could never
                # settle on the real token) and is not admissible, whatever it declares.
                # Comparison is exact: EIP-712 names are domain-separator bytes, not labels.
                if (entry.eip712_name and entry.eip712_name != asset.eip712_name) or (entry.eip712_version and entry.eip712_version != asset.eip712_version):
                    domain_conflicts = True
                    continue
        return entry, f"selected:{entry.network}:{asset.symbol}"
    # a Mainnet entry is a declared Mainnet row: x402 is offered on Test networks rows only (no Mainnet row declares a scheme)
    mainnets = [e.network for e in offer.accepts if chains.is_declared(e.network) and chains.resolve_network(e.network).is_mainnet]
    if mainnets:
        return None, "refused:mainnet_entry_only"
    if all(e.amount_minor > cap for e in offer.accepts):
        return None, "refused:above_cap"
    if domain_conflicts:
        return None, "refused:eip712_domain_conflicts_registry_pin"
    return None, "refused:no_admissible_entry"


def propose_from_v2_offer(offer: V2Offer, entry: V2Requirements, *, wallet_id: str, source_context: dict[str, Any] | None = None) -> proposals.TransactionProposal:
    """Turn the SELECTED entry into a typed proposal of origin x402. Never pays; above the
    configured cap refuses before a proposal exists."""
    custody.require_enabled(source_context=source_context)
    spec = chains.resolve_network(entry.network)
    if entry.amount_minor > config.x402_cap_minor():
        raise wallet_fault("wallet_x402_cap_exceeded", authority=AUTHORITY, context={"amount_minor": entry.amount_minor, "limit": str(config.x402_cap_minor()), "reason": "above_automatic_cap"}, source_context=source_context)
    asset = chains.asset_for(entry.network, entry.asset)
    key_material = "|".join([entry.resource_url, entry.pay_to, str(entry.amount_minor), entry.asset, entry.network])
    idempotency_key = "x402v2:" + hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
    return proposals.propose_transaction(
        wallet_id=wallet_id, destination=entry.pay_to, amount_minor=entry.amount_minor, asset=asset.symbol,
        origin=proposals.ORIGIN_X402, memo=f"x402v2 {entry.resource_url}"[:200], idempotency_key=idempotency_key,
        source_context=source_context, network=spec.network,
    )


# --- the v2 payment payload and header ---------------------------------------------------------------


def build_accepted_requirements(entry: V2Requirements) -> dict[str, Any]:
    accepted = {
        "scheme": entry.scheme, "network": entry.network, "amount": str(entry.amount_minor),
        "asset": entry.asset, "payTo": entry.pay_to, "maxTimeoutSeconds": int(entry.max_timeout_seconds),
    }
    extra: dict[str, Any] = {}
    if entry.eip712_name:
        extra["name"] = entry.eip712_name
    if entry.eip712_version:
        extra["version"] = entry.eip712_version
    if entry.asset_transfer_method:
        extra["assetTransferMethod"] = entry.asset_transfer_method
    if extra:
        accepted["extra"] = extra
    return accepted


def build_payment_payload_v2(entry: V2Requirements, *, family: str, account: str, signature_hex: str, transaction_b64: str = "", typed_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """The chain-dispatched PaymentPayload. EVM exact: {signature, authorization} where the
    authorization is the EXACT message of the typed data that was signed (from the signing
    request's stored record — never caller-supplied state); SVM exact: {transaction}."""
    payload: dict[str, Any]
    if family == chains.FAMILY_EVM:
        message = dict((typed_data or {}).get("message") or {})
        authorization = {
            "from": str(message.get("from") or ""), "to": str(message.get("to") or ""),
            "value": str(message.get("value") or ""), "validAfter": str(message.get("validAfter") or ""),
            "validBefore": str(message.get("validBefore") or ""), "nonce": str(message.get("nonce") or ""),
        }
        payload = {"signature": signature_hex, "authorization": authorization}
    else:
        payload = {"transaction": transaction_b64}
    return {
        "x402Version": X402_VERSION,
        "accepted": build_accepted_requirements(entry),
        "payload": payload,
    }


def payment_header_v2(payload: dict[str, Any], *, resource_url: str = "") -> str:
    """The PAYMENT-SIGNATURE header value: base64 JSON, with the resource echoed when known."""
    body = dict(payload)
    if resource_url:
        body.setdefault("resource", {"url": resource_url})
    return base64.b64encode(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")


def parse_settlement_response(header_value: Any) -> dict[str, Any] | None:
    """The PAYMENT-RESPONSE header, typed: {success, errorReason?, payer?, transaction,
    network}. Not a settlement header, or a settlement without a network — None."""
    raw: Any = header_value
    if isinstance(raw, str) and raw.strip():
        try:
            raw = base64.b64decode(raw.strip())
        except Exception:
            return None
    parsed = _as_json(raw)
    if parsed is None or "success" not in parsed or not str(parsed.get("network") or ""):
        return None
    return parsed


def resource_origin_of(url: str) -> str:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = parts.port
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


__all__ = [
    "AUTHORITY",
    "HEADER_PAYMENT_REQUIRED",
    "HEADER_PAYMENT_RESPONSE",
    "HEADER_PAYMENT_SIGNATURE",
    "OUTCOME_DELIVERED",
    "OUTCOME_PAYMENT_REQUIRED",
    "OUTCOME_REFUSED",
    "SCHEME_EXACT",
    "X402_VERSION",
    "V2Offer",
    "V2Requirements",
    "build_accepted_requirements",
    "build_payment_payload_v2",
    "parse_payment_required",
    "parse_settlement_response",
    "payment_header_v2",
    "propose_from_v2_offer",
    "resource_origin_of",
    "select_offer",
]
