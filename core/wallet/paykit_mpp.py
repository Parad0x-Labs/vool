"""MPP charges on Solana through Solana pay-kit, under the same money authority as the pay-kit x402 lane.

An MPP (Machine Payments Protocol) 402 carries ``WWW-Authenticate: Payment ...`` naming a method, an intent and a
base64url request. For ``method="solana"`` and ``intent="charge"`` pay-kit builds the charge transaction through any
signer object (``pubkey()`` + ``sign_message()``), so this wallet hands it the same guarded signer the x402 lane
uses: the key never leaves the wallet, and the one signature covers exactly the approved transfer.

1. The request binding, cap and parking are the x402 lane's (:mod:`core.wallet.paykit_x402`): ONE capped proposal,
   origin ``mpp_paykit``, bound to the exact request, nothing signed. The challenge itself is kept on the binding.
2. On approval the lifecycle's pay-kit step builds ``Authorization: Payment <credential>`` with the guarded
   signer, sends the stored request once to the challenged origin, and believes settlement only from the chain.
3. A sponsored charge (the challenge names the server's fee payer) costs this wallet exactly the amount. An
   unsponsored one makes this wallet the fee payer: the network fee is reserved with the amount, shown at approval,
   and the signed message may carry at most a bounded priority fee.

Not taken here, refused typed: ``session`` intents (payment channels), charges with ``splits`` (more than one
payee, where the approval sheet shows one), ``localnet``, and a challenge that has expired.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from core.wallet import chains, config, custody, proposals
from core.wallet.paykit_x402 import (
    MAX_PAYER_PRIORITY_LAMPORTS,
    _clean_headers,
    _clean_request,
    _import_paykit,
    _run,
    _upsert_binding,
    availability,
    existing_outcome,
    lost_request,
    refuse_pilot_lane,
    request_digest,
    require_available,
)
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.paykit_mpp"
METHOD_SOLANA = "solana"
INTENT_CHARGE = "charge"
AUTHORIZATION_HEADER = "Authorization"
_WWW_AUTHENTICATE = "www-authenticate"
_RECEIPT = "payment-receipt"
#: MPP's Solana network slugs -> this wallet's rows. ``localnet`` names no row this wallet declares.
_NETWORKS = {"devnet": chains.SOLANA_DEVNET, "mainnet": chains.SOLANA_MAINNET, "mainnet-beta": chains.SOLANA_MAINNET}


def _mpp(module: str) -> Any:
    return _import_paykit(f"solana_pay_kit.protocols.mpp.{module}")


def solana_challenges(headers: dict[str, Any] | None) -> list[Any]:
    """The ``method="solana"`` Payment challenges a 402 carries, parsed by pay-kit; [] for anything else. Reads only."""
    if not availability()[0]:
        return []
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = lowered.get(_WWW_AUTHENTICATE)
    if not raw:
        return []
    try:
        found = _mpp("core.headers").parse_www_authenticate_all([str(raw)])
    except Exception:  # a malformed challenge is no offer, never a crash at the door
        return []
    return [c for c in found if str(c.method) == METHOD_SOLANA]


def names_solana_challenge(headers: dict[str, Any] | None) -> bool:
    """Whether a 402 carries a ``Payment`` challenge naming ``method="solana"``, read WITHOUT pay-kit (the scheme token
    and the method parameter only), so a door that cannot pay it can still say what it needs. Reads only."""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    compact = "".join(str(lowered.get(_WWW_AUTHENTICATE) or "").lower().split())
    if not (compact.startswith("payment") or ",payment" in compact):
        return False
    return 'method="solana"' in compact or "method=solana," in compact or compact.endswith("method=solana")


def claims_challenge(headers: dict[str, Any] | None, *, wallet_id: str) -> bool:
    """Whether a 402 the ordinary x402 door met is an MPP Solana challenge for this lane: pay-kit is installed, the
    wallet is a Solana wallet, and the 402 names a Solana Payment challenge. Reads only."""
    profile = custody.get_wallet(str(wallet_id or ""))
    if profile is None:
        return False
    try:
        if not chains.resolve_network(profile.network).is_svm:
            return False
    except Exception:
        return False
    return bool(solana_challenges(headers))


def _charge_of(challenge: Any) -> tuple[Any, Any]:
    request = _mpp("intents.charge").ChargeRequest.from_dict(_mpp("core.base64url").decode_json(challenge.request))
    details = _import_paykit("solana_pay_kit._paycore.solana").MethodDetails.from_dict(request.method_details or {})
    return request, details


def _terms_from_challenge(challenge: Any, *, wallet_network: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The cost-bearing facts of one MPP Solana charge, judged by THIS wallet's registry. Refuses typed before any
    proposal: another intent, an expired challenge, another network, splits, an unregistered asset or decimals that
    differ from the registry's, a malformed payee, a sponsored charge that names no fee payer."""
    def refuse(reason: str, **context: Any) -> Exception:
        return wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)

    if str(challenge.intent) != INTENT_CHARGE:
        raise refuse("mpp_intent_not_supported", intent=str(challenge.intent)[:24])
    if challenge.expires and challenge.is_expired():
        raise refuse("mpp_challenge_expired")
    try:
        request, details = _charge_of(challenge)
    except Exception:
        raise refuse("mpp_request_unparseable") from None
    network = _NETWORKS.get(str(details.network or ""))
    if network is None:
        raise refuse("mpp_network_not_declared", network=str(details.network)[:24])
    spec = chains.resolve_network(network)
    if spec.network != chains.resolve_network(wallet_network).network:
        raise refuse("offer_network_differs_from_wallet", network=spec.network[:64])
    if details.splits:
        # the approval sheet shows ONE payee; a charge split across several is not approved here
        raise refuse("mpp_splits_not_supported", splits=len(details.splits))
    try:
        amount = int(str(request.amount))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise refuse("offer_amount_invalid")
    currency = str(request.currency or "").strip()
    if not currency:
        raise refuse("offer_asset_missing")
    if currency.upper() == "SOL":
        asset = chains.native_asset(spec.network)
    else:
        mint = _import_paykit("solana_pay_kit._paycore.solana").resolve_mint(currency, str(details.network)) or currency
        try:
            asset = chains.asset_for(spec.network, mint)
        except Exception:
            raise refuse("offer_asset_not_registered", asset=currency[:48]) from None
        if asset.native or asset.address != mint:
            raise refuse("offer_asset_not_registered", asset=currency[:48])
        if details.decimals is None or int(details.decimals) != int(asset.decimals):
            raise refuse("mpp_decimals_differ_from_registry")
    pay_to = str(request.recipient or "").strip()
    if not chains.destination_matches_family(spec.network, pay_to):
        raise refuse("offer_payee_invalid")
    fee_payer = ""
    if details.fee_payer:
        fee_payer = str(details.fee_payer_key or "").strip()
        if not fee_payer or not chains.destination_matches_family(spec.network, fee_payer):
            raise refuse("offer_fee_payer_missing")
    return {"network": spec.network, "asset": asset.symbol, "mint": "" if asset.native else asset.address, "decimals": int(asset.decimals),
            "amount_minor": amount, "pay_to": pay_to, "fee_payer": fee_payer, "payer_pays_fee": not fee_payer}


def park_challenge(answer: dict[str, Any], *, url: str, method: str, headers: dict[str, str] | None, body: bytes, wallet_id: str,
                   source_context: dict[str, Any] | None = None) -> Any:
    """The MPP 402 the owner's request met (already received: nothing is sent here) -> ONE capped proposal bound to
    that exact request, or a typed refusal. Never signs."""
    from core.wallet import x402

    require_available(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    clean_url, clean_method, raw_body = _clean_request(url, method, body, source_context=source_context)
    replay_headers = _clean_headers(headers)
    digest = request_digest(clean_method, clean_url, raw_body)
    already = existing_outcome(digest, source_context=source_context)
    if already is not None:
        # this request is already parked or paid: a later caller can never rebind it to another proposal
        return already
    status = int(answer["status"])
    challenges = solana_challenges(answer.get("headers"))
    if not challenges:
        return x402.X402Outcome(status=x402.OUTCOME_REFUSED, http_status=status, body=answer["body"])
    # a charge the wallet can take is preferred over a session it cannot; the refusal names the first challenge's reason
    challenge = next((c for c in challenges if str(c.intent) == INTENT_CHARGE), challenges[0])
    terms = _terms_from_challenge(challenge, wallet_network=profile.network, source_context=source_context)
    refuse_pilot_lane(wallet_id, terms, source_context=source_context)
    cap = config.x402_cap_minor()
    if terms["amount_minor"] > cap:
        raise wallet_fault("wallet_x402_cap_exceeded", authority=AUTHORITY, context={"amount_minor": terms["amount_minor"], "limit": str(cap), "asset": terms["asset"], "reason": "above_automatic_cap"}, source_context=source_context)
    idempotency_key = "mpppk:" + hashlib.sha256(f"{digest}|{challenge.id}|{terms['pay_to']}|{terms['amount_minor']}|{terms['mint'] or terms['asset']}|{terms['network']}".encode()).hexdigest()[:24]
    proposal = proposals.propose_transaction(
        wallet_id=wallet_id, destination=terms["pay_to"], amount_minor=terms["amount_minor"], asset=terms["asset"], origin=proposals.ORIGIN_MPP_PAYKIT,
        memo=f"mpp {clean_method} {clean_url}"[:200], idempotency_key=idempotency_key, source_context=source_context, network=terms["network"],
    )
    if not _upsert_binding(request_digest_value=digest, url=clean_url, method=clean_method, body=raw_body, headers=replay_headers, terms=terms,
                           offer={"protocol": "mpp", "challenge": dataclasses.asdict(challenge)}, version=x402.BINDING_VERSION_PAYKIT_MPP, proposal_id=proposal.proposal_id):
        return lost_request(digest, proposal.proposal_id, source_context=source_context)
    # an unsponsored charge's fee is bound by prepare itself, before the proposal can be approved: the approval shows
    # it, and the claim reserves exactly that fee with the amount
    prepared = x402.prepare_bound(proposal.proposal_id, source_context=source_context)
    binding = x402.binding_for_digest(digest) or {}
    return x402.X402Outcome(status=x402.OUTCOME_PAYMENT_REQUIRED, http_status=status, body=answer["body"], proposal_id=prepared.proposal_id, binding_id=str(binding.get("binding_id") or ""))


def payer_fee_minor(proposal: proposals.TransactionProposal) -> int:
    """The fee an unsponsored charge costs this wallet at most: the simulated signature fee plus the priority bound."""
    try:
        simulated = int((proposal.simulation or {}).get("fee_minor") or 0)
    except (TypeError, ValueError):
        simulated = 0
    return max(0, simulated) + MAX_PAYER_PRIORITY_LAMPORTS


def challenge_from_binding(binding: dict[str, Any]) -> Any:
    stored = dict((json.loads(str(binding.get("offer_json") or "{}")) or {}).get("challenge") or {})
    return _mpp("core.types").PaymentChallenge(**{f.name: stored.get(f.name, f.default if f.default is not dataclasses.MISSING else "")
                                                   for f in dataclasses.fields(_mpp("core.types").PaymentChallenge)})


def terms_for(binding: dict[str, Any], proposal: proposals.TransactionProposal, *, payer: str) -> dict[str, Any]:
    """The approved terms the signature guard enforces: the PROPOSAL's payee and amount (what the owner approved), the
    registry's mint and decimals, and the challenge's fee payer recorded at parking (none: this wallet pays the fee)."""
    spec = chains.resolve_network(proposal.network)
    asset = chains.asset_for(spec.network, proposal.asset)
    fee_payer = str(binding.get("fee_payer") or "")
    return {"pay_to": proposal.destination, "amount_minor": int(proposal.amount_minor), "mint": "" if asset.native else asset.address,
            "decimals": int(asset.decimals), "fee_payer": fee_payer or payer, "payer_pays_fee": not fee_payer}


class _RpcForPayKit:
    """The two reads pay-kit's charge builder may make, answered by this wallet's own proven endpoint."""

    def __init__(self, rpc: Any) -> None:
        self._rpc = rpc

    async def get_latest_blockhash(self, commitment: Any = None) -> Any:
        from types import SimpleNamespace

        from solders.hash import Hash

        return SimpleNamespace(value=SimpleNamespace(blockhash=Hash.from_string(self._rpc.latest_blockhash())))

    async def get_account(self, pubkey: Any) -> Any:
        from types import SimpleNamespace

        result = self._rpc._call("getAccountInfo", [str(pubkey), {"encoding": "base64"}])
        value = (result or {}).get("value") if isinstance(result, dict) else None
        return SimpleNamespace(value=SimpleNamespace(owner=str(value["owner"])) if isinstance(value, dict) and value.get("owner") else None)


def build_authorization(binding: dict[str, Any], signer: Any, *, rpc: Any, proposal: proposals.TransactionProposal) -> tuple[str, str]:
    """(header name, value) for the stored challenge, built by pay-kit, signed through the guard. pay-kit's own
    amount and network guards are armed with the approved amount and the challenge's network."""
    challenge = challenge_from_binding(binding)
    _request, details = _charge_of(challenge)
    value = _run(_mpp("client.charge").build_credential_header(
        signer, _RpcForPayKit(rpc), challenge, max_amount_base_units=int(proposal.amount_minor), expected_network=str(details.network),
    ))
    return AUTHORIZATION_HEADER, value


def settlement_claim(headers: dict[str, Any] | None) -> dict[str, Any]:
    """The resource's ``Payment-Receipt``: a claim, never proof. {} when absent, malformed or not a success."""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = lowered.get(_RECEIPT)
    if not raw:
        return {}
    try:
        receipt = _mpp("core.headers").parse_receipt(str(raw))
    except Exception:
        return {}
    if not receipt.is_success():
        return {}
    return {"transaction": str(receipt.reference or receipt.tx_hash or ""), "challenge_id": str(receipt.challenge_id or "")}
