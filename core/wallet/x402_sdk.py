"""x402 on Base through the x402 Foundation SDK, paid from a Crypto Pilot wallet: the protocol is theirs, the money
authority stays this wallet's.

The `x402 Foundation SDK <https://github.com/x402-foundation/x402>`_ (Apache-2.0) reads a v2 ``PAYMENT-REQUIRED``
challenge and builds the ``exact`` EVM payment: an EIP-3009 ``transferWithAuthorization`` the resource's facilitator
submits and pays the gas for. This lane uses only its schemas, its client and its header codecs, and keeps every
decision about money here:

1. A 402 the ordinary x402 door meets for a Crypto Pilot wallet on Base or Base Sepolia (:func:`claims_challenge`)
   becomes ONE capped proposal bound to that exact request, under the origin ``x402_sdk`` (:func:`park_challenge`),
   when its ``exact`` entry for the wallet's own row names the registry's EIP-3009 token. Nothing is signed. The
   request binding, its one key per request and its replay rules are the pay-kit lane's (``core.wallet.paykit_x402``).
2. The owner approves it on the Crypto Pilot sheet: one quote, one credential. The lifecycle claims it (the amount
   held; no network fee, the facilitator pays it), then the SDK builds the payment inside the signing session through
   :class:`GuardedTypedDataSigner`, which signs ONCE and only typed data that is exactly the approved transfer
   (:func:`verify_authorization_request`), then the SAME stored request goes once, with the payment, to the
   challenged origin only.
3. Settlement is believed only from the chain: a successful receipt carrying, from the token contract, this wallet's
   ``AuthorizationUsed`` for this nonce and its ``Transfer`` of exactly the approved amount to the approved payee
   (:func:`authorization_settlement`). A transaction the resource names is checked that way; otherwise the observer
   finds it by the authorization's own nonce. An authorization the chain never used is worthless once a finalized
   block is past its ``validBefore``: only then does the payment fail, with nothing moved and no fee, and the amount
   stop counting against the limits (one that never left is released then).

The SDK is an optional dependency (the ``x402`` extra: ``x402[evm]``, whose EVM package imports web3). Without it,
:func:`availability` says why and the door refuses typed (``wallet_x402_sdk_unavailable``, naming the extra) before
anything is parked. VOOL's own x402 client keeps every other wallet's offers, unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from typing import Any

from core.wallet import chains, config, custody, proposals
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.x402_sdk"
SDK_DISTRIBUTION = "x402"
#: The SDK release this lane was built and tested against (pyproject ``x402`` extra pins it).
SDK_TESTED_VERSION = "2.25.0"
MIN_PYTHON = (3, 10)

PAYMENT_REQUIRED_HEADER = "payment-required"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "payment-response"
SCHEME_EXACT = "exact"
PRIMARY_TYPE = "TransferWithAuthorization"
#: keccak("AuthorizationUsed(address,bytes32)"): the event an EIP-3009 token emits when an authorization is used
AUTHORIZATION_USED_TOPIC0 = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
#: a validBefore the SDK computed from its own clock may sit this far past ours (the two clocks are read apart)
CLOCK_SKEW_SECONDS = 30
#: the authorization's window must have at least this long left, on the chain's clock and ours, when the request leaves
SEND_MARGIN_SECONDS = 5
#: blocks per ``eth_getLogs`` read when the chain is searched for an authorization's use
LOG_SPAN_BLOCKS = 500

#: proposal_id -> the delivered resource body, for the caller of the approval that delivered it. Process memory only:
#: a paid response is the caller's data, never written to the wallet store (its digest and size are).
_DELIVERED_BODIES: dict[str, bytes] = {}


# --- availability ----------------------------------------------------------------------------------------------

def _sdk(module: str) -> Any:
    import importlib

    return importlib.import_module(module)


def availability() -> tuple[bool, str]:
    """(available, reason). Pure: imports the SDK's client modules, sends nothing."""
    if sys.version_info < MIN_PYTHON:
        return False, "python_below_3_10"
    try:
        _sdk("x402.mechanisms.evm.exact.client")
        _sdk("x402.http.utils")
    except ImportError:
        return False, "x402_sdk_not_installed"
    except Exception as exc:  # a broken install is unavailable, never a crash at the door
        return False, f"x402_sdk_import_failed:{type(exc).__name__}"
    return True, "available"


def require_available(*, source_context: dict[str, Any] | None = None) -> None:
    ok, reason = availability()
    if not ok:
        raise wallet_fault("wallet_x402_sdk_unavailable", authority=AUTHORITY, context={"reason": reason, "dependency": SDK_DISTRIBUTION}, source_context=source_context)


# --- which wallets and rows this lane pays on --------------------------------------------------------------------

def eip3009_asset(network: Any, address: str = "") -> chains.AssetSpec | None:
    """The registry's EIP-3009 token on this row (an asset with a pinned EIP-712 name and version), or None. With
    ``address``, only that contract: the address is the token's identity, never its symbol."""
    try:
        spec = chains.resolve_network(network)
    except Exception:
        return None
    if not spec.is_evm:
        return None
    for asset in spec.assets.values():
        if asset.native or not asset.eip712_name or not asset.eip712_version:
            continue
        if not address or asset.address.lower() == str(address).strip().lower():
            return asset
    return None


def lane_rows() -> list[chains.ChainIdentity]:
    """The rows this lane can pay on: EVM rows with a registered EIP-3009 token whose Crypto Pilot lane is ready."""
    from core.wallet import capabilities

    return [spec for spec in chains.all_networks() if spec.is_evm and eip3009_asset(spec.network) is not None and capabilities.pilot_transfer_ready(spec)]


def pilot_wallet_row(wallet_id: str) -> chains.ChainIdentity | None:
    """The row of a Crypto Pilot wallet whose setup is finished, when it is one this lane pays on; else None. Reads only."""
    if custody.seal_policy(str(wallet_id or "")) != custody.PILOT_SEAL_POLICY or custody.setup_state(str(wallet_id or "")) not in {"", "ready"}:
        return None
    profile = custody.get_wallet(str(wallet_id or ""))
    if profile is None:
        return None
    try:
        spec = chains.resolve_network(profile.network)
    except Exception:
        return None
    return spec if spec.network in {row.network for row in lane_rows()} else None


# --- the challenge ---------------------------------------------------------------------------------------------------

def _header(headers: dict[str, Any] | None, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def _raw_challenge(headers: dict[str, Any] | None) -> dict[str, Any] | None:
    """The v2 ``PAYMENT-REQUIRED`` header as JSON, read without the SDK (so a missing install can still be named)."""
    raw = _header(headers, PAYMENT_REQUIRED_HEADER)
    if not raw:
        return None
    try:
        decoded = json.loads(base64.b64decode(raw.encode("ascii"), validate=False).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(decoded, dict) or int(decoded.get("x402Version") or 0) != 2 or not isinstance(decoded.get("accepts"), list):
        return None
    return decoded


def names_challenge(headers: dict[str, Any] | None, *, network: str) -> bool:
    """Whether a 402 carries a v2 ``exact`` entry on this EVM row. Reads only, needs no SDK."""
    decoded = _raw_challenge(headers)
    if decoded is None:
        return False
    try:
        wanted = chains.resolve_network(network).network
    except Exception:
        return False
    for entry in decoded["accepts"]:
        if not isinstance(entry, dict) or str(entry.get("scheme") or "") != SCHEME_EXACT:
            continue
        try:
            if chains.resolve_network(str(entry.get("network") or "")).network == wanted:
                return True
        except Exception:
            continue
    return False


def claims_challenge(headers: dict[str, Any] | None, body: bytes | None, *, wallet_id: str) -> bool:
    """Whether a 402 the ordinary x402 door met belongs to this lane: the wallet is a Crypto Pilot wallet, ready, on a
    row this lane pays on, and the 402 carries a v2 ``exact`` entry on that row. Whether the SDK is installed is not
    asked here: the door then refuses typed, naming the extra, instead of parking a payment the Pilot sheet cannot
    pay. VOOL's own x402 client keeps every other wallet's offers. Reads only."""
    spec = pilot_wallet_row(wallet_id)
    return spec is not None and names_challenge(headers, network=spec.network)


def _decode_challenge(headers: dict[str, Any] | None) -> Any:
    """The SDK's reading of the 402's ``PAYMENT-REQUIRED`` header: a v2 ``PaymentRequired``, or None."""
    raw = _header(headers, PAYMENT_REQUIRED_HEADER)
    if not raw:
        return None
    try:
        payment_required = _sdk("x402.http.utils").decode_payment_required_header(raw)
    except Exception:  # a malformed challenge is no offer, never a crash at the door
        return None
    return payment_required if int(getattr(payment_required, "x402_version", 0) or 0) == 2 else None


def _terms_from_entry(entry: Any, *, wallet_network: str, payer: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The cost-bearing facts of the SDK-read ``exact`` entry on this wallet's row, judged by THIS wallet's registry.
    Refused typed before any proposal: another network, a token the registry does not pin, another EIP-712 domain,
    a transfer method other than EIP-3009, a malformed or self payee, a window longer than this wallet's own."""
    def refuse(reason: str, **context: Any) -> Exception:
        return wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)

    if str(getattr(entry, "scheme", "") or "") != SCHEME_EXACT:
        raise refuse("offer_scheme_not_exact")
    try:
        spec = chains.resolve_network(str(getattr(entry, "network", "") or ""))
    except Exception:
        raise refuse("offer_network_not_declared", network=str(getattr(entry, "network", ""))[:64]) from None
    if spec.network != chains.resolve_network(wallet_network).network or not spec.is_evm:
        raise refuse("offer_network_differs_from_wallet", network=spec.network[:64])
    asset = eip3009_asset(spec.network, str(getattr(entry, "asset", "") or ""))
    if asset is None:
        raise refuse("offer_asset_not_registered", asset=str(getattr(entry, "asset", ""))[:48])
    extra = dict(getattr(entry, "extra", None) or {})
    method = str(extra.get("assetTransferMethod") or "eip3009").strip().lower()
    if method != "eip3009":
        # Permit2 pulls through an allowance this wallet would first have to grant: another instrument, not this lane's
        raise refuse("offer_transfer_method_not_eip3009", method=method[:24])
    name, version = extra.get("name"), extra.get("version")
    if (name is not None and str(name) != asset.eip712_name) or (version is not None and str(version) != asset.eip712_version):
        raise refuse("offer_token_domain_differs", name=str(name or "")[:48], version=str(version or "")[:16])
    try:
        amount = int(str(getattr(entry, "amount", "") or "0"))
    except ValueError:
        raise refuse("offer_amount_invalid") from None
    if amount <= 0:
        raise refuse("offer_amount_invalid")
    pay_to = str(getattr(entry, "pay_to", "") or "").strip()
    if not chains.destination_matches_family(spec.network, pay_to):
        raise refuse("offer_payee_invalid")
    if pay_to.lower() == str(payer).lower():
        raise refuse("offer_payee_is_this_wallet")
    window = int(getattr(entry, "max_timeout_seconds", 0) or 0)
    if window <= 0 or window > config.x402_max_window_seconds():
        # the SDK signs validBefore = now + this window; the signed authorization is a bearer instrument, so its live
        # window (and how long an unsettled payment keeps its hold) is this wallet's policy, never the site's
        raise refuse("offer_window_outside_policy", window=window, limit=config.x402_max_window_seconds())
    return {"network": spec.network, "asset": asset.symbol, "asset_address": asset.address, "decimals": int(asset.decimals), "amount_minor": amount,
            "pay_to": pay_to, "eip712_name": asset.eip712_name, "eip712_version": asset.eip712_version, "max_timeout_seconds": window}


def _entry_for(payment_required: Any, network: str) -> Any:
    """The challenge's ``exact`` entry on this row (the first, as the SDK's own client selects it), or None."""
    for entry in list(getattr(payment_required, "accepts", None) or []):
        try:
            same = chains.resolve_network(str(getattr(entry, "network", "") or "")).network == network
        except Exception:
            continue
        if same and str(getattr(entry, "scheme", "") or "") == SCHEME_EXACT:
            return entry
    return None


def offer_key(resource: str, method: str, body: bytes, terms: dict[str, Any]) -> str:
    """The idempotency key of an SDK x402 proposal: the offer (the resource it names, its payee, amount, token and
    network) and the request's method and body, never the URL it was fetched at, as the other x402 lanes key theirs."""
    material = json.dumps([resource, method, hashlib.sha256(bytes(body or b"")).hexdigest(), terms["pay_to"].lower(), int(terms["amount_minor"]),
                           terms["asset_address"].lower(), terms["network"]])
    return "x402sdk:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def park_challenge(answer: dict[str, Any], *, url: str, method: str, headers: dict[str, str] | None, body: bytes, wallet_id: str,
                   source_context: dict[str, Any] | None = None) -> Any:
    """The 402 the owner's request met (``answer``, already received: nothing is sent here) -> the offer's ONE capped
    proposal with that exact request bound to it, or a typed refusal. Never signs."""
    from core.wallet import paykit_x402, x402
    from core.wallet.store import dumps

    require_available(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    clean_url, clean_method, raw_body = paykit_x402._clean_request(url, method, body, source_context=source_context)
    replay_headers = paykit_x402._clean_headers(headers)
    digest = paykit_x402.request_digest(clean_method, clean_url, raw_body)
    already = paykit_x402.existing_outcome(digest, source_context=source_context)
    if already is not None:
        # this request is already parked or paid: a later caller can never rebind it to another proposal
        return already
    status = int(answer["status"])
    spec = pilot_wallet_row(wallet_id)
    if spec is None:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "x402_sdk_pilot_lane_not_supported", "network": str(profile.network)[:64]},
                           source_context=source_context)
    payment_required = _decode_challenge(answer.get("headers"))
    entry = _entry_for(payment_required, spec.network) if payment_required is not None else None
    if entry is None:
        return x402.X402Outcome(status=x402.OUTCOME_REFUSED, http_status=status, body=answer["body"])
    terms = _terms_from_entry(entry, wallet_network=spec.network, payer=profile.public_key, source_context=source_context)
    cap = config.x402_cap_minor()
    if terms["amount_minor"] > cap:
        raise wallet_fault("wallet_x402_cap_exceeded", authority=AUTHORITY, context={"amount_minor": terms["amount_minor"], "limit": str(cap), "asset": terms["asset"], "reason": "above_automatic_cap"}, source_context=source_context)
    resource_info = getattr(payment_required, "resource", None)
    resource = str(getattr(resource_info, "url", "") or "")
    requirement = entry.model_dump(by_alias=True, exclude_none=True)
    offer = {"x402Version": 2, "requirement": requirement, "resource": resource_info.model_dump(by_alias=True, exclude_none=True) if resource_info is not None else None}
    binding = {
        "pay_to": terms["pay_to"], "amount_minor": int(terms["amount_minor"]), "asset": terms["asset"], "network": terms["network"], "version": x402.BINDING_VERSION_X402_SDK,
        "offer_json": dumps(offer), "resource_origin": paykit_x402._origin_of(clean_url), "resource_method": clean_method, "eip712_name": terms["eip712_name"],
        "eip712_version": terms["eip712_version"], "asset_transfer_method": "eip3009", "max_timeout_seconds": int(terms["max_timeout_seconds"]),
        "max_facilitator_fee_minor": 0, "max_network_fee_minor": 0, "sponsored_gas": 1, "fee_asset": terms["asset"], "asset_address": terms["asset_address"],
        "request_body_b64": base64.b64encode(raw_body).decode("ascii"), "request_headers_json": dumps(replay_headers),
    }
    # the proposal and its claim on the request commit together, as on the other x402 lanes: nothing ever sees it unbound
    claim = x402._RequestClaim(digest, clean_url, clean_method, binding, source_context=source_context)
    proposal = proposals.propose_transaction(
        wallet_id=wallet_id, destination=terms["pay_to"], amount_minor=terms["amount_minor"], asset=terms["asset"], origin=proposals.ORIGIN_X402_SDK,
        memo=f"x402 {clean_method} {resource}"[:200], idempotency_key=offer_key(resource, clean_method, raw_body, terms), source_context=source_context,
        network=terms["network"], claim=claim,
    )
    return paykit_x402.parked(proposal, claim, http_status=status, body=answer["body"], source_context=source_context)


def binding_for(proposal_id: str) -> dict[str, Any] | None:
    """The SDK lane's binding of this proposal, or None. Another lane's binding is never this lane's."""
    from core.wallet import x402

    binding = x402.binding_for_proposal(str(proposal_id))
    if binding and int(binding.get("version") or 1) == x402.BINDING_VERSION_X402_SDK:
        return binding
    return None


def delivered_body(proposal_id: str) -> bytes | None:
    """The resource body the approval of this proposal delivered, handed out once."""
    return _DELIVERED_BODIES.pop(str(proposal_id), None)


def pilot_quote_block(binding: dict[str, Any], proposal: proposals.TransactionProposal) -> dict[str, Any]:
    """The SDK lane's facts a Crypto Pilot quote shows and its digest binds: the one request the payment is for, the
    token contract and its EIP-712 domain, the authorization's window, and that the site's facilitator pays the
    network fee (this wallet pays none). The approval reads the binding again and compares, so a binding that changed
    after the preview is refused before the credential."""
    return {
        "protocol": "x402", "built_by": "x402 Foundation SDK", "origin": str(proposal.origin), "binding_id": str(binding.get("binding_id") or ""),
        "request_digest": str(binding.get("request_digest") or ""), "url": str(binding.get("url") or ""), "method": str(binding.get("method") or "GET"),
        "resource_origin": str(binding.get("resource_origin") or ""), "token": str(binding.get("asset_address") or ""),
        "eip712_name": str(binding.get("eip712_name") or ""), "eip712_version": str(binding.get("eip712_version") or ""),
        "window_seconds": int(binding.get("max_timeout_seconds") or 0), "sponsored": True, "payer_fee_max_minor": 0,
    }


def terms_for(binding: dict[str, Any], proposal: proposals.TransactionProposal, *, payer: str) -> dict[str, Any]:
    """The approved terms the signature guard enforces: the PROPOSAL's payee and amount (what the owner approved),
    the registry's token on the proposal's row and its EIP-712 pin, the row's chain id, and the window bound at parking."""
    spec = chains.resolve_network(proposal.network)
    asset = eip3009_asset(spec.network, str(binding.get("asset_address") or ""))
    if asset is None or asset.symbol != str(proposal.asset):
        raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "x402_sdk_token_not_registered"})
    return {"payer": str(payer), "pay_to": str(proposal.destination), "amount_minor": int(proposal.amount_minor), "chain_id": int(spec.chain_id),
            "token": asset.address, "eip712_name": asset.eip712_name, "eip712_version": asset.eip712_version, "window_seconds": int(binding.get("max_timeout_seconds") or 0)}


# --- the guard on the one signature -------------------------------------------------------------------------------

def _field_pairs(fields: Any) -> list[tuple[str, str]]:
    pairs = []
    for field in list(fields or []):
        if isinstance(field, dict):
            pairs.append((str(field.get("name")), str(field.get("type"))))
        else:
            pairs.append((str(getattr(field, "name", "")), str(getattr(field, "type", ""))))
    return pairs


def _nonce_hex(value: Any) -> str:
    raw = bytes(value) if isinstance(value, (bytes, bytearray)) else bytes.fromhex(str(value or "").removeprefix("0x")) if str(value or "") else b""
    if len(raw) != 32:
        raise ValueError("nonce_not_32_bytes")
    return "0x" + raw.hex()


def verify_authorization_request(domain: Any, types: Any, primary_type: str, message: dict[str, Any], *, terms: dict[str, Any], now: float) -> dict[str, Any]:
    """The typed data the SDK asks this wallet to sign, held to the approved terms field by field. Returns VOOL's own
    typed-data form of it (``evm.transfer_with_authorization_typed_data``), whose digest the key signs. Refuses typed
    (``wallet_signature_invalid``) anything else: another primary type or field list, another token, chain or domain,
    another payer, payee or amount, an authorization valid before now or for longer than the approved window, a nonce
    that is not 32 bytes."""
    from core.wallet import evm

    def refuse(reason: str) -> Exception:
        return wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": reason})

    if str(primary_type) != PRIMARY_TYPE:
        raise refuse("x402_sdk_primary_type_not_transfer_with_authorization")
    wanted = [(field["name"], field["type"]) for field in evm.TRANSFER_WITH_AUTHORIZATION_TYPES]
    fields = dict(types or {}).get(PRIMARY_TYPE)
    if _field_pairs(fields) != wanted or set(dict(types or {})) - {PRIMARY_TYPE, "EIP712Domain"}:
        raise refuse("x402_sdk_type_fields_differ")
    get = (lambda name, alt: domain.get(name, domain.get(alt))) if isinstance(domain, dict) else (lambda name, alt: getattr(domain, alt, getattr(domain, name, None)))
    seen_domain = (str(get("name", "name")), str(get("version", "version")), int(get("chainId", "chain_id") or 0), str(get("verifyingContract", "verifying_contract") or "").lower())
    if seen_domain != (terms["eip712_name"], terms["eip712_version"], int(terms["chain_id"]), terms["token"].lower()):
        raise refuse("x402_sdk_domain_not_the_registry_token")
    message = dict(message or {})
    try:
        valid_after, valid_before, value = int(message.get("validAfter")), int(message.get("validBefore")), int(message.get("value"))
        nonce = _nonce_hex(message.get("nonce"))
    except (TypeError, ValueError):
        raise refuse("x402_sdk_message_malformed") from None
    if str(message.get("from") or "").lower() != terms["payer"].lower():
        raise refuse("x402_sdk_payer_not_this_wallet")
    if str(message.get("to") or "").lower() != terms["pay_to"].lower():
        raise refuse("x402_sdk_payee_not_approved")
    if value != int(terms["amount_minor"]):
        raise refuse("x402_sdk_amount_not_approved")
    if valid_after < 0 or valid_after > now:
        raise refuse("x402_sdk_valid_after_in_the_future")
    if valid_before <= now or valid_before > now + int(terms["window_seconds"]) + CLOCK_SKEW_SECONDS:
        raise refuse("x402_sdk_window_not_approved")
    return evm.transfer_with_authorization_typed_data(
        chain_id=int(terms["chain_id"]), token=terms["token"], token_name=terms["eip712_name"], token_version=terms["eip712_version"],
        from_address=str(message["from"]), to=str(message["to"]), value=value, valid_after=valid_after, valid_before=valid_before, nonce=nonce,
    )


class GuardedTypedDataSigner:
    """The signer handed to the SDK (its ``ClientEvmSigner``: ``address`` and ``sign_typed_data``): this wallet's own
    Crypto Pilot session behind :func:`verify_authorization_request`, good for ONE signature. The key never leaves the
    session; the SDK only ever sees the address and the signature."""

    def __init__(self, session: Any, *, terms: dict[str, Any], clock: Any = time.time) -> None:
        from eth_utils import to_checksum_address

        self._session = session
        self._terms = dict(terms)
        self._clock = clock
        self._address = to_checksum_address(str(terms["payer"]))
        self.typed_data: dict[str, Any] = {}
        self.signature = ""

    @property
    def address(self) -> str:
        return self._address

    def sign_typed_data(self, domain: Any, types: Any, primary_type: str, message: dict[str, Any]) -> bytes:
        if self.signature:
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "x402_sdk_second_signature_refused"})
        typed = verify_authorization_request(domain, types, primary_type, message, terms=self._terms, now=float(self._clock()))
        signature = str(self._session.sign_evm_typed_data(typed))
        self.typed_data, self.signature = typed, signature
        return bytes.fromhex(signature.removeprefix("0x"))

    def authorization(self) -> dict[str, Any]:
        """What the row keeps of the signed authorization: what the observer needs to find or rule out its use."""
        message = self.typed_data.get("message") or {}
        return {"kind": "eip3009", "token": self._terms["token"], "nonce": str(message.get("nonce") or ""), "valid_after": int(message.get("validAfter") or 0),
                "valid_before": int(message.get("validBefore") or 0)}


def build_payment_header(binding: dict[str, Any], signer: GuardedTypedDataSigner) -> tuple[str, str]:
    """(header name, value) for the stored offer, built by the SDK's own client, signed through the guard. The SDK
    sees ONE accepts entry, the bound one, so it can select nothing else."""
    schemas = _sdk("x402.schemas")
    offer = json.loads(str(binding.get("offer_json") or "{}"))
    requirement = dict(offer.get("requirement") or {})
    payment_required = schemas.PaymentRequired.model_validate({"x402Version": 2, "accepts": [requirement], "resource": offer.get("resource"), "error": ""})
    client = _sdk("x402.client").x402ClientSync().register(str(requirement.get("network") or ""), _sdk("x402.mechanisms.evm.exact.client").ExactEvmScheme(signer))
    payload = client.create_payment_payload(payment_required)
    return PAYMENT_SIGNATURE_HEADER, _sdk("x402.http.utils").encode_payment_signature_header(payload)


# --- what the resource answers, and what the chain proves -------------------------------------------------------

def settlement_claim(headers: dict[str, Any] | None) -> dict[str, Any]:
    """The resource's ``PAYMENT-RESPONSE`` read by the SDK: {transaction, success, network, payer}. A claim, never proof."""
    raw = _header(headers, PAYMENT_RESPONSE_HEADER)
    if not raw:
        return {}
    try:
        settled = _sdk("x402.http.utils").decode_payment_response_header(raw)
    except Exception:
        return {}
    tx = str(getattr(settled, "transaction", "") or "")
    return {"transaction": tx if chains.EVM_TX_RE.match(tx) else "", "success": bool(getattr(settled, "success", False)), "network": str(getattr(settled, "network", "") or ""),
            "payer": str(getattr(settled, "payer", "") or "")}


def _topic_address(address: str) -> str:
    return "0x" + str(address).lower().removeprefix("0x").rjust(64, "0")


def _log_value(log: dict[str, Any]) -> int | None:
    try:
        return int(str(log.get("data") or "0x").removeprefix("0x") or "", 16)
    except ValueError:
        return None


def proves_authorization(receipt: Any, *, token: str, payer: str, pay_to: str, amount_minor: int, nonce: str) -> bool:
    """Whether ``receipt`` is this payment: it succeeded, and the token contract logged both ``AuthorizationUsed(payer,
    nonce)`` and ``Transfer(payer, pay_to, exactly amount_minor)``. A reverted receipt used nothing (the authorization
    stays usable until its window closes), and another payment of the same amount to the same payee names another
    nonce."""
    from core.wallet import evm

    if not isinstance(receipt, dict) or str(receipt.get("status") or "").lower() not in {"0x1", "1"}:
        return False
    used = transferred = False
    payer_topic, payee_topic, nonce_topic = _topic_address(payer), _topic_address(pay_to), str(nonce).lower()
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict) or str(log.get("address") or "").lower() != str(token).lower():
            continue
        topics = [str(topic).lower() for topic in (log.get("topics") or [])]
        if len(topics) == 3 and topics[0] == AUTHORIZATION_USED_TOPIC0 and topics[1] == payer_topic and topics[2] == nonce_topic:
            used = True
        elif len(topics) == 3 and topics[0] == evm.TRANSFER_TOPIC0 and topics[1] == payer_topic and topics[2] == payee_topic and _log_value(log) == int(amount_minor):
            transferred = True
    return used and transferred


def authorization_settlement(client: Any, tx_hash: str, *, token: str, payer: str, pay_to: str, amount_minor: int, nonce: str) -> tuple[str, dict[str, Any] | None]:
    """('settled', receipt) when the chain holds a receipt under ``tx_hash`` that :func:`proves_authorization`; else
    ('unknown', None). Reads only; one read."""
    if not chains.EVM_TX_RE.match(str(tx_hash or "")):
        return "unknown", None
    try:
        receipt = client._call("eth_getTransactionReceipt", [str(tx_hash)])
    except Exception:
        return "unknown", None
    if proves_authorization(receipt, token=token, payer=payer, pay_to=pay_to, amount_minor=amount_minor, nonce=nonce):
        return "settled", receipt
    return "unknown", None


def authorization_logs(client: Any, *, token: str, payer: str, nonce: str, from_block: int, valid_before: int) -> list[dict[str, Any]]:
    """The token's ``AuthorizationUsed(payer, nonce)`` logs from ``from_block`` on: where the chain says this
    authorization was used, whoever submitted it. The token takes it only in a block stamped before ``valid_before``,
    so the walk ends with the first span whose last block is stamped at or past it, however far the chain has moved
    since; each read spans at most :data:`LOG_SPAN_BLOCKS` (public endpoints refuse wide ranges). Reads only; raises
    when the node does not answer."""
    latest = int(str(client._call("eth_blockNumber", [])), 16)
    topics = [AUTHORIZATION_USED_TOPIC0, _topic_address(payer), str(nonce).lower()]
    found: list[dict[str, Any]] = []
    start = max(0, int(from_block))
    while start <= latest:
        end = min(latest, start + LOG_SPAN_BLOCKS - 1)
        answer = client._call("eth_getLogs", [{"address": str(token), "fromBlock": hex(start), "toBlock": hex(end), "topics": topics}])
        found.extend(log for log in (answer or []) if isinstance(log, dict))
        if valid_before > 0 and int(str((client._call("eth_getBlockByNumber", [hex(end), False]) or {})["timestamp"]), 16) >= int(valid_before):
            break
        start = end + 1
    return found


#: authorizationState(address,bytes32)
_AUTHORIZATION_STATE_SELECTOR = "0xe94a0102"
#: balanceOf(address)
_BALANCE_OF_SELECTOR = "0x70a08231"


def authorization_used(client: Any, *, token: str, payer: str, nonce: str, block: str) -> bool | None:
    """The token's own ``authorizationState(payer, nonce)`` at ``block``: True once used (or cancelled), False while
    still usable; None when the node did not answer a word."""
    data = _AUTHORIZATION_STATE_SELECTOR + _topic_address(payer)[2:] + str(nonce).lower().removeprefix("0x").rjust(64, "0")
    try:
        answer = str(client._call("eth_call", [{"to": str(token), "data": data}, str(block)]) or "")
        return int(answer.removeprefix("0x") or "x", 16) != 0
    except Exception:
        return None


def token_balance(client: Any, *, token: str, owner: str, block: str = "latest") -> int:
    """The token's ``balanceOf(owner)``. Raises on an unreadable answer: a balance is never guessed."""
    data = _BALANCE_OF_SELECTOR + _topic_address(owner)[2:]
    answer = str(client._call("eth_call", [{"to": str(token), "data": data}, str(block)]) or "")
    return int(answer.removeprefix("0x"), 16)


__all__ = [
    "AUTHORITY", "AUTHORIZATION_USED_TOPIC0", "LOG_SPAN_BLOCKS", "SDK_TESTED_VERSION", "SEND_MARGIN_SECONDS", "GuardedTypedDataSigner", "authorization_logs",
    "authorization_settlement", "authorization_used", "availability", "binding_for", "build_payment_header", "claims_challenge", "delivered_body",
    "eip3009_asset", "lane_rows", "names_challenge", "park_challenge", "pilot_quote_block", "pilot_wallet_row", "proves_authorization",
    "require_available", "settlement_claim", "terms_for", "token_balance", "verify_authorization_request",
]
