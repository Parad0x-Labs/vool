"""x402 on Solana through Solana pay-kit: the protocol is theirs, the money authority stays this wallet's.

`Solana pay-kit <https://github.com/solana-foundation/pay-kit>`_ (MIT, Solana Foundation) reads an x402 ``exact``
challenge and builds the canonical payment: a v0 transaction the resource's fee payer cosigns and settles. Its
ready-made clients pay every 402 on their own; this lane uses only its parser and its builder, and keeps every
decision about money here:

1. :func:`fetch_paid` sends the owner's request (method, body and the few replayable headers) once. A 402 that
   pay-kit parses for this wallet's own network becomes ONE capped proposal bound to that exact request, under the
   origin ``x402_paykit``. Nothing is signed; the request bytes are kept on the binding. The ordinary x402 door
   (``core.wallet.x402.fetch_paid_resource``, behind the agent tool and the wallet API) hands this lane the
   canonical Solana offers it meets (:func:`claims_challenge`, :func:`park_challenge`).
2. The owner approves it on the ordinary approval path (``PaymentLifecycle.approve_and_execute``), which hands an
   accepted decision to the lifecycle's pay-kit step: claim (hold + effect), then pay-kit builds the payment with a
   signer that refuses any message other than exactly the approved transfer (:func:`verify_payment_message`), then
   the SAME stored request is sent once with the payment header, to the challenged origin only.
3. Settlement is believed only from the chain: the transaction the resource names must carry this wallet's own
   signature over the approved message and have succeeded. Anything less leaves the hold in place as unknown.

pay-kit is an optional dependency (the ``pay`` extra, Python 3.11+). Without it, :func:`availability` says why and
every door refuses typed (``wallet_dependency_unavailable``) before any request is sent.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import uuid
from typing import Any
from urllib.parse import urlsplit

from core.wallet import chains, config, custody, proposals
from core.wallet.redaction import publish_identifier
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.paykit_x402"
PAYKIT_DISTRIBUTION = "solana-pay-kit"
#: The pay-kit release this lane was built and tested against (pyproject ``pay`` extra pins it).
PAYKIT_TESTED_VERSION = "0.10.0"
MIN_PYTHON = (3, 11)

#: The request this lane may replay with a payment: bounded, and only methods with ordinary request semantics.
MAX_REQUEST_BODY_BYTES = 64 * 1024
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
#: The only caller headers kept on the binding and replayed on the paid request. Credentials, cookies and
#: anything else the caller passes are dropped: the binding is durable and the payment is the authorization.
REPLAYED_HEADERS = frozenset({"content-type", "accept", "accept-language"})

PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
LEGACY_PAYMENT_HEADER = "X-PAYMENT"
PAYMENT_RESPONSE_HEADER = "payment-response"
LEGACY_PAYMENT_RESPONSE_HEADER = "x-payment-response"

SYSTEM_PROGRAM = "11111111111111111111111111111111"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
#: ComputeBudget instruction discriminators the payment may carry (SetComputeUnitLimit, SetComputeUnitPrice).
_SET_COMPUTE_UNIT_LIMIT = 2
_SET_COMPUTE_UNIT_PRICE = 3
_COMPUTE_BUDGET_KINDS = frozenset({_SET_COMPUTE_UNIT_LIMIT, _SET_COMPUTE_UNIT_PRICE})
_COMPUTE_BUDGET_SIZES = {_SET_COMPUTE_UNIT_LIMIT: 5, _SET_COMPUTE_UNIT_PRICE: 9}
#: When this wallet pays the fee (an unsponsored MPP charge), the most priority fee the signed message may carry on
#: top of the base signature fee. pay-kit's own default is 200,000 units at 1 micro-lamport: 0.2 lamports.
MAX_PAYER_PRIORITY_LAMPORTS = 10
_SYSTEM_TRANSFER = 2
_TOKEN_TRANSFER_CHECKED = 12

#: proposal_id -> the delivered resource body, for the caller of the approval that delivered it. Process memory
#: only: a paid response is the caller's data, never written to the wallet store (its digest and size are).
_DELIVERED_BODIES: dict[str, bytes] = {}


# --- availability ----------------------------------------------------------------------------------------------

def _import_paykit(module: str) -> Any:
    """Import one pay-kit module. Its package import materialises pay-kit's shipped DEMO signer (a server-side
    default, never used by this lane) and warns about it once per process; that warning names a key this wallet does
    not use, so it is kept out of the owner's logs here."""
    import importlib
    import logging
    import warnings

    paykit_logger = logging.getLogger("solana_pay_kit")
    level = paykit_logger.level
    paykit_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"solana_pay_kit: using the shipped demo signer")
            return importlib.import_module(module)
    finally:
        paykit_logger.setLevel(level)


def availability() -> tuple[bool, str]:
    """(available, reason). Pure: imports pay-kit's client modules, sends nothing."""
    if sys.version_info < MIN_PYTHON:
        return False, "python_below_3_11"
    try:
        _import_paykit("solana_pay_kit.protocols.x402.client.exact.payment")
    except ImportError:
        return False, "paykit_not_installed"
    except Exception as exc:  # a broken install is unavailable, never a crash at the door
        return False, f"paykit_import_failed:{type(exc).__name__}"
    return True, "available"


def require_available(*, source_context: dict[str, Any] | None = None) -> None:
    ok, reason = availability()
    if not ok:
        raise wallet_fault("wallet_dependency_unavailable", authority=AUTHORITY, context={"reason": reason, "dependency": PAYKIT_DISTRIBUTION}, source_context=source_context)


def _payment_module() -> Any:
    return _import_paykit("solana_pay_kit.protocols.x402.client.exact.payment")


# --- the request this lane binds ---------------------------------------------------------------------------------

def request_digest(method: str, url: str, body: bytes) -> str:
    """The binding key: method, URL and the body's digest. Another body or method is another request, which
    needs its own 402 and its own approval; a payment never covers a request it was not approved for."""
    body_digest = hashlib.sha256(bytes(body or b"")).hexdigest()
    return publish_identifier(hashlib.sha256(f"paykit|{str(method).upper()}|{url}|{body_digest}".encode()).hexdigest())


def _clean_headers(headers: dict[str, str] | None) -> dict[str, str]:
    kept: dict[str, str] = {}
    for name, value in (headers or {}).items():
        lowered = str(name).strip().lower()
        if lowered in REPLAYED_HEADERS:
            kept[lowered] = str(value)[:256]
    return kept


def _clean_request(url: str, method: str, body: bytes | str | None, *, source_context: dict[str, Any] | None) -> tuple[str, str, bytes]:
    from core.wallet import x402

    clean_url = str(url or "").strip()
    if not x402._target_allowed(clean_url):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"reason": "x402_target_not_public", "host": urlsplit(clean_url).hostname or ""}, source_context=source_context)
    clean_method = str(method or "GET").strip().upper()
    if clean_method not in ALLOWED_METHODS:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "request_method_not_supported", "method": clean_method[:12]}, source_context=source_context)
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "request_body_too_large", "bytes": len(raw)}, source_context=source_context)
    return clean_url, clean_method, raw


def _origin_of(url: str) -> str:
    from core.wallet import x402

    return x402._origin_of(url)


# --- the offer -> the terms this wallet would approve ------------------------------------------------------------

def _terms_from_requirement(requirement: dict[str, Any], *, wallet_network: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The cost-bearing facts of one pay-kit-selected offer, judged by THIS wallet's registry. Refuses typed
    before any proposal: another network, an unregistered asset, a missing fee payer, a malformed payee. (A fee payer
    that is this wallet itself is refused at signing, by :func:`verify_payment_message`.)"""
    def refuse(reason: str, **context: Any) -> Exception:
        return wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)

    payment = _payment_module()
    if str(requirement.get("scheme") or "exact") != "exact":
        raise refuse("offer_scheme_not_exact")
    offer_network = payment._offer_network_caip2(requirement) or ""
    try:
        spec = chains.resolve_network(offer_network)
    except Exception:
        raise refuse("offer_network_not_declared", network=str(offer_network)[:64]) from None
    if spec.network != chains.resolve_network(wallet_network).network or not spec.is_svm:
        raise refuse("offer_network_differs_from_wallet", network=spec.network[:64])
    amount = int(payment._amount_of(requirement) or 0)
    if amount <= 0:
        raise refuse("offer_amount_invalid")
    extra = requirement.get("extra") if isinstance(requirement.get("extra"), dict) else {}
    raw_asset = str(requirement.get("currency") or requirement.get("asset") or "").strip()
    if not raw_asset:
        raise refuse("offer_asset_missing")
    if raw_asset.upper() == "SOL":
        asset = chains.native_asset(spec.network)
    else:
        label = "devnet" if spec.testnet else "mainnet"
        mint = _import_paykit("solana_pay_kit._paycore.mints").resolve_stablecoin_mint(raw_asset, label) or raw_asset
        try:
            asset = chains.asset_for(spec.network, mint)
        except Exception:
            raise refuse("offer_asset_not_registered", asset=raw_asset[:48]) from None
        if asset.native or asset.address != mint:
            # a symbol the registry knows under ANOTHER mint is not this asset: the mint is the identity
            raise refuse("offer_asset_not_registered", asset=raw_asset[:48])
    pay_to = str(requirement.get("payTo") or requirement.get("recipient") or "").strip()
    if not chains.destination_matches_family(spec.network, pay_to):
        raise refuse("offer_payee_invalid")
    fee_payer = str(requirement.get("feePayerKey") or extra.get("feePayer") or "").strip()
    if not fee_payer or not chains.destination_matches_family(spec.network, fee_payer):
        # the canonical Solana exact scheme is fee-sponsored: the resource's fee payer cosigns and pays the fee. An
        # offer without one would have THIS wallet pay a fee the offer sets, which this lane does not take.
        raise refuse("offer_fee_payer_missing")
    return {"network": spec.network, "asset": asset.symbol, "mint": "" if asset.native else asset.address, "decimals": int(asset.decimals),
            "amount_minor": amount, "pay_to": pay_to, "fee_payer": fee_payer}


# --- the one fetch ---------------------------------------------------------------------------------------------------

def existing_outcome(digest: str, *, source_context: dict[str, Any] | None = None) -> Any:
    """What a request this lane already bound gets, before anything is sent: its parked proposal while one waits,
    a typed refusal once it was paid (its payment is delivered once and never sent again), else None."""
    from core.wallet import x402

    binding = x402.binding_for_digest(digest)
    if not binding or not binding.get("proposal_id") or int(binding.get("version") or 1) not in x402.PAYKIT_BINDING_VERSIONS:
        return None
    parked = proposals.get_proposal(binding["proposal_id"])
    if parked is not None and parked.state in {proposals.STATE_PENDING_APPROVAL, proposals.STATE_APPROVED, proposals.STATE_AWAITING_SIGNATURE, proposals.STATE_SIGNED}:
        return x402.X402Outcome(status=x402.OUTCOME_PAYMENT_REQUIRED, http_status=402, proposal_id=parked.proposal_id, binding_id=binding["binding_id"])
    if parked is not None and parked.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
        # this exact request was paid: its payment was delivered once and is never sent again
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": parked.proposal_id, "reason": "paykit_request_already_paid"}, source_context=source_context)
    return None


def _parse_challenge(headers: dict[str, Any] | None, body: bytes | None, *, network: str) -> tuple[dict[str, Any] | None, int]:
    """pay-kit's reading of a 402 (v1 or v2), preferring this wallet's network. (None, 0) when it reads no offer."""
    payment = _payment_module()
    text = body.decode("utf-8", "replace") if body else None
    try:
        requirement, wire_version = payment.parse_x402_challenge_with_version(dict(headers or {}), text, payment.ChallengeSelection(network=network))
    except Exception:  # a malformed challenge is no offer, never a crash at the door
        return None, 0
    return (dict(requirement), int(wire_version or 2)) if requirement is not None else (None, 0)


def claims_challenge(headers: dict[str, Any] | None, body: bytes | None, *, wallet_id: str) -> bool:
    """Whether a 402 the ordinary x402 door met belongs to this lane: pay-kit is installed, the wallet is a Solana
    wallet, and pay-kit reads a canonical Solana offer, one whose fee payer (the resource's) settles it. VOOL's own
    v1 Solana offers name no fee payer and stay on that lane, unchanged. Reads only."""
    if not availability()[0]:
        return False
    profile = custody.get_wallet(str(wallet_id or ""))
    if profile is None:
        return False
    try:
        spec = chains.resolve_network(profile.network)
    except Exception:
        return False
    if not spec.is_svm:
        return False
    requirement, _version = _parse_challenge(headers, body, network=spec.network)
    if requirement is None:
        return False
    extra = requirement.get("extra") if isinstance(requirement.get("extra"), dict) else {}
    if not str(requirement.get("feePayerKey") or extra.get("feePayer") or "").strip():
        return False
    try:
        return chains.resolve_network(_payment_module()._offer_network_caip2(requirement) or "").is_svm
    except Exception:
        return False


def fetch_paid(url: str, *, wallet_id: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | str | None = b"",
               source_context: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
    """Send the owner's request once; on a 402 park ONE capped proposal bound to exactly this request. Never pays."""
    from core.wallet import outbound, x402

    require_available(source_context=source_context)
    custody.require_enabled(source_context=source_context)
    custody.require_wallet(wallet_id, source_context=source_context)
    clean_url, clean_method, raw_body = _clean_request(url, method, body, source_context=source_context)
    replay_headers = _clean_headers(headers)
    already = existing_outcome(request_digest(clean_method, clean_url, raw_body), source_context=source_context)
    if already is not None:
        return already
    answer = outbound.fetch(clean_url, method=clean_method, headers={**replay_headers, "Accept": replay_headers.get("accept", "*/*")}, body=raw_body or None, timeout=timeout)
    status = int(answer["status"])
    if status != 402:
        return x402.X402Outcome(status=x402.OUTCOME_DELIVERED if status < 400 else x402.OUTCOME_REFUSED, http_status=status, body=answer["body"])
    return park_challenge(answer, url=clean_url, method=clean_method, headers=headers, body=raw_body, wallet_id=wallet_id, source_context=source_context)


def refuse_pilot_lane(wallet_id: str, terms: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> None:
    """A Crypto Pilot wallet, or a row whose native coin moves only through the Pilot lane, pays from its quote sheet as
    a plain transfer, which a pay-kit resource never sees: such a payment would buy nothing. Refused before any proposal."""
    from core.wallet import transfers

    if transfers.is_pilot_transfer_parts(wallet_id=wallet_id, network=terms["network"], asset=terms["asset"]):
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "paykit_pilot_lane_not_supported", "network": terms["network"]}, source_context=source_context)


def park_challenge(answer: dict[str, Any], *, url: str, method: str, headers: dict[str, str] | None, body: bytes, wallet_id: str,
                   source_context: dict[str, Any] | None = None) -> Any:
    """The 402 the owner's request met (``answer``, already received: nothing is sent here) -> ONE capped proposal
    bound to that exact request, or a typed refusal. Never signs."""
    from core.wallet import x402

    require_available(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    clean_url, clean_method, raw_body = _clean_request(url, method, body, source_context=source_context)
    replay_headers = _clean_headers(headers)
    digest = request_digest(clean_method, clean_url, raw_body)
    status = int(answer["status"])
    requirement, wire_version = _parse_challenge(answer.get("headers"), answer.get("body"), network=chains.resolve_network(profile.network).network)
    if requirement is None:
        from core.wallet import paykit_mpp

        if paykit_mpp.solana_challenges(answer.get("headers")):
            # no x402 offer, but an MPP one: the same request binding, the MPP lane's terms
            return paykit_mpp.park_challenge(answer, url=clean_url, method=clean_method, headers=headers, body=raw_body, wallet_id=wallet_id, source_context=source_context)
        return x402.X402Outcome(status=x402.OUTCOME_REFUSED, http_status=status, body=answer["body"])
    terms = _terms_from_requirement(requirement, wallet_network=profile.network, source_context=source_context)
    refuse_pilot_lane(wallet_id, terms, source_context=source_context)
    cap = config.x402_cap_minor()
    if terms["amount_minor"] > cap:
        raise wallet_fault("wallet_x402_cap_exceeded", authority=AUTHORITY, context={"amount_minor": terms["amount_minor"], "limit": str(cap), "asset": terms["asset"], "reason": "above_automatic_cap"}, source_context=source_context)
    idempotency_key = "x402pk:" + hashlib.sha256(f"{digest}|{terms['pay_to']}|{terms['amount_minor']}|{terms['mint'] or terms['asset']}|{terms['network']}".encode()).hexdigest()[:24]
    proposal = proposals.propose_transaction(
        wallet_id=wallet_id, destination=terms["pay_to"], amount_minor=terms["amount_minor"], asset=terms["asset"], origin=proposals.ORIGIN_X402_PAYKIT,
        memo=f"x402 {clean_method} {clean_url}"[:200], idempotency_key=idempotency_key, source_context=source_context, network=terms["network"],
    )
    _upsert_binding(request_digest_value=digest, url=clean_url, method=clean_method, body=raw_body, headers=replay_headers, terms=terms,
                    offer={"x402Version": int(wire_version), "requirement": requirement}, version=x402.BINDING_VERSION_PAYKIT, proposal_id=proposal.proposal_id)
    from core.wallet import lifecycle

    prepared = lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    binding = x402.binding_for_digest(digest) or {}
    return x402.X402Outcome(status=x402.OUTCOME_PAYMENT_REQUIRED, http_status=status, body=answer["body"], proposal_id=prepared.proposal_id, binding_id=str(binding.get("binding_id") or ""))


def _upsert_binding(*, request_digest_value: str, url: str, method: str, body: bytes, headers: dict[str, str], terms: dict[str, Any],
                    offer: dict[str, Any], version: int, proposal_id: str) -> None:
    """Bind one request (method, URL, body, replayable headers) to its parked proposal and the offer it met. The
    same request re-parked after a dead proposal takes the new offer whole, its fee facts included (the MPP lane then
    sets the fee this wallet pays, if any); ``version`` names the lane (x402 or MPP)."""
    from core.wallet import x402
    from core.wallet.store import connection, dumps, utcnow

    now = utcnow()
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, state, created_at, updated_at,"
            " version, offer_json, resource_origin, resource_method, sponsored_gas, fee_asset, asset_address, request_body_b64, request_headers_json, fee_payer)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(request_digest) DO UPDATE SET proposal_id = excluded.proposal_id, state = excluded.state, pay_to = excluded.pay_to, version = excluded.version,"
            " amount_minor = excluded.amount_minor, asset = excluded.asset, network = excluded.network, offer_json = excluded.offer_json,"
            " fee_payer = excluded.fee_payer, asset_address = excluded.asset_address, request_headers_json = excluded.request_headers_json,"
            " sponsored_gas = excluded.sponsored_gas, fee_asset = excluded.fee_asset, max_network_fee_minor = 0, max_facilitator_fee_minor = 0,"
            " tx_signature = '', resource_status = 0, resource_digest = '',"
            " resource_bytes = 0, updated_at = excluded.updated_at",
            (f"x402b-{uuid.uuid4().hex[:16]}", request_digest_value, url, method, terms["pay_to"], int(terms["amount_minor"]), terms["asset"], terms["network"],
             proposal_id, x402.BINDING_PAYMENT_REQUIRED, now, now, int(version), dumps(offer), _origin_of(url), method, 0 if terms.get("payer_pays_fee") else 1, terms["asset"],
             terms["mint"], base64.b64encode(body).decode("ascii"), dumps(headers), terms["fee_payer"]),
        )


def binding_for(proposal_id: str) -> dict[str, Any] | None:
    """The pay-kit binding (x402 or MPP) of this proposal, or None. Another lane's binding is never this lane's."""
    from core.wallet import x402

    binding = x402.binding_for_proposal(str(proposal_id))
    if binding and int(binding.get("version") or 1) in x402.PAYKIT_BINDING_VERSIONS:
        return binding
    return None


def delivered_body(proposal_id: str) -> bytes | None:
    """The resource body the approval of this proposal delivered, handed out once."""
    return _DELIVERED_BODIES.pop(str(proposal_id), None)


# --- the guard on the one signature -------------------------------------------------------------------------------

def _ata(owner: str, mint: str, token_program: str) -> str:
    from solders.pubkey import Pubkey

    address, _bump = Pubkey.find_program_address(
        [bytes(Pubkey.from_string(owner)), bytes(Pubkey.from_string(token_program)), bytes(Pubkey.from_string(mint))],
        Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM),
    )
    return str(address)


def verify_payment_message(message: bytes, *, payer: str, pay_to: str, amount_minor: int, mint: str, decimals: int, fee_payer: str,
                           payer_pays_fee: bool = False) -> dict[str, Any]:
    """Prove the bytes pay-kit asks this wallet to sign move EXACTLY the approved amount of the approved asset from
    this wallet to the approved payee, and nothing else: a v0 message with no lookup tables, the resource's fee
    payer first, this wallet signing only as the transfer authority, and no instruction beyond compute-budget
    settings, one transfer and one memo. Returns the facts it proved; raises ``wallet_signature_invalid`` otherwise.

    ``payer_pays_fee`` (an unsponsored MPP charge): this wallet is the fee payer and the only signer, and the
    priority fee the compute-budget settings would charge it is bounded by :data:`MAX_PAYER_PRIORITY_LAMPORTS`."""
    from solders.message import MessageV0

    def refuse(reason: str) -> Exception:
        return wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": reason})

    data = bytes(message or b"")
    if len(data) < 2 or data[0] != 0x80:
        raise refuse("paykit_message_not_v0")
    try:
        parsed = MessageV0.from_bytes(data[1:])
    except Exception:
        raise refuse("paykit_message_unparseable") from None
    if bytes(parsed) != data[1:]:
        raise refuse("paykit_message_not_canonical")
    if list(parsed.address_table_lookups):
        raise refuse("paykit_message_uses_lookup_tables")
    keys = [str(key) for key in parsed.account_keys]
    required = int(parsed.header.num_required_signatures)
    if payer_pays_fee:
        if not keys or keys[0] != payer:
            raise refuse("paykit_fee_payer_not_the_offer")
        if set(keys[:required]) != {payer}:
            raise refuse("paykit_unexpected_signers")
    else:
        if not keys or keys[0] != fee_payer or fee_payer == payer:
            raise refuse("paykit_fee_payer_not_the_offer")
        if set(keys[:required]) != {fee_payer, payer}:
            raise refuse("paykit_unexpected_signers")
    transfers = 0
    memos = 0
    budget = 0
    unit_limit = 200_000  # the runtime default when no SetComputeUnitLimit is present
    unit_price = 0
    for instruction in parsed.instructions:
        program = keys[int(instruction.program_id_index)]
        accounts = [keys[int(i)] for i in bytes(instruction.accounts)]
        body = bytes(instruction.data)
        if program == COMPUTE_BUDGET_PROGRAM:
            if not body or body[0] not in _COMPUTE_BUDGET_KINDS or accounts or len(body) != _COMPUTE_BUDGET_SIZES[body[0]]:
                raise refuse("paykit_unexpected_compute_budget_instruction")
            if body[0] == _SET_COMPUTE_UNIT_LIMIT:
                unit_limit = int.from_bytes(body[1:5], "little")
            else:
                unit_price = int.from_bytes(body[1:9], "little")
            budget += 1
            continue
        if program == MEMO_PROGRAM:
            if accounts:
                raise refuse("paykit_memo_names_accounts")
            memos += 1
            continue
        if program == SYSTEM_PROGRAM and not mint:
            if len(body) != 12 or int.from_bytes(body[:4], "little") != _SYSTEM_TRANSFER:
                raise refuse("paykit_unexpected_system_instruction")
            if accounts != [payer, pay_to]:
                raise refuse("paykit_transfer_parties_differ")
            if int.from_bytes(body[4:12], "little") != int(amount_minor):
                raise refuse("paykit_transfer_amount_differs")
            transfers += 1
            continue
        if program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM) and mint:
            if len(body) != 10 or body[0] != _TOKEN_TRANSFER_CHECKED:
                raise refuse("paykit_unexpected_token_instruction")
            if int.from_bytes(body[1:9], "little") != int(amount_minor):
                raise refuse("paykit_transfer_amount_differs")
            if body[9] != int(decimals):
                raise refuse("paykit_transfer_decimals_differ")
            if accounts != [_ata(payer, mint, program), mint, _ata(pay_to, mint, program), payer]:
                raise refuse("paykit_transfer_parties_differ")
            transfers += 1
            continue
        raise refuse("paykit_unexpected_program")
    if transfers != 1 or memos > 1 or budget > 2:
        raise refuse("paykit_instruction_count_differs")
    if payer_pays_fee and unit_limit * unit_price > MAX_PAYER_PRIORITY_LAMPORTS * 1_000_000:
        # the priority fee is micro-lamports per compute unit, paid by the fee payer: here, this wallet
        raise refuse("paykit_priority_fee_above_bound")
    return {"fee_payer": payer if payer_pays_fee else fee_payer, "payer": payer, "pay_to": pay_to, "amount_minor": int(amount_minor), "mint": mint,
            "payer_pays_fee": bool(payer_pays_fee), "message_digest": hashlib.sha256(data).hexdigest()}


class _PublicKeyOnly:
    """What pay-kit reads from ``signer.keypair``: the public key, and nothing that could sign."""

    def __init__(self, public_key: str) -> None:
        from solders.pubkey import Pubkey

        self._pubkey = Pubkey.from_string(public_key)

    def pubkey(self) -> Any:
        return self._pubkey


class GuardedSigner:
    """The signer handed to pay-kit: this wallet's own signer behind :func:`verify_payment_message`, good for ONE
    signature. The key never leaves the wallet's signer; pay-kit only ever sees the public key and the signature."""

    def __init__(self, signer: Any, *, terms: dict[str, Any]) -> None:
        self._signer = signer
        self._terms = dict(terms)
        self.keypair = _PublicKeyOnly(signer.public_key)
        self.signed_message: bytes = b""
        self.signature: bytes = b""
        self.proof: dict[str, Any] = {}

    def sign(self, message: bytes) -> bytes:
        if self.signed_message:
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "paykit_second_signature_refused"})
        self.proof = verify_payment_message(bytes(message), payer=self._signer.public_key, **self._terms)
        signature = bytes(self._signer.sign(bytes(message)))
        self.signed_message = bytes(message)
        self.signature = signature
        return signature

    # the shape pay-kit's MPP builders call (a solders ``Keypair``'s): the same one guarded signature
    def pubkey(self) -> Any:
        return self.keypair.pubkey()

    def sign_message(self, message: bytes) -> Any:
        from solders.signature import Signature

        return Signature.from_bytes(self.sign(bytes(message)))


def _requirement_from_binding(binding: dict[str, Any]) -> tuple[dict[str, Any], int]:
    offer = json.loads(str(binding.get("offer_json") or "{}"))
    return dict(offer.get("requirement") or {}), int(offer.get("x402Version") or 2)


def build_payment_header(binding: dict[str, Any], signer: GuardedSigner, *, blockhash: str) -> tuple[str, str]:
    """(header name, value) for the stored offer, built by pay-kit, signed through the guard."""
    payment = _payment_module()
    requirement, wire_version = _requirement_from_binding(binding)
    legacy = wire_version == 1
    builder = payment.build_payment_header_legacy if legacy else payment.build_payment_header
    value = _run(builder(signer, None, requirement, recent_blockhash_provider=lambda: blockhash))
    return (LEGACY_PAYMENT_HEADER if legacy else PAYMENT_SIGNATURE_HEADER), value


def _run(coroutine: Any) -> Any:
    """Run pay-kit's coroutine to completion from synchronous wallet code, also when this thread already runs a loop
    (the served API): then it runs on a short-lived thread of its own, and its exception surfaces here unchanged."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def terms_for(binding: dict[str, Any], proposal: proposals.TransactionProposal) -> dict[str, Any]:
    """The approved terms the signature guard enforces: the PROPOSAL's payee and amount (what the owner approved),
    the registry's mint and decimals, and the offer's fee payer recorded at parking."""
    spec = chains.resolve_network(proposal.network)
    asset = chains.asset_for(spec.network, proposal.asset)
    return {"pay_to": proposal.destination, "amount_minor": int(proposal.amount_minor), "mint": "" if asset.native else asset.address,
            "decimals": int(asset.decimals), "fee_payer": str(binding.get("fee_payer") or "")}


def settlement_claim(headers: dict[str, Any] | None) -> dict[str, Any]:
    """The resource's settlement answer (v2 ``PAYMENT-RESPONSE`` or v1 ``X-PAYMENT-RESPONSE``): a claim, never proof."""
    from core.wallet import x402

    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    for name in (PAYMENT_RESPONSE_HEADER, LEGACY_PAYMENT_RESPONSE_HEADER):
        parsed = x402.parse_settlement_response_v1(lowered.get(name))
        if parsed:
            return parsed
    return {}


def verify_settlement_on_chain(rpc: Any, tx_signature: str, *, signed_message: bytes, payer: str, attempts: int = 10, wait_seconds: float = 0.5) -> str:
    """'settled' only when the chain holds a SUCCESSFUL transaction under ``tx_signature`` whose message is exactly
    the one this wallet signed and which carries this wallet's signature over it; 'failed' when that transaction
    failed; otherwise 'unknown'. Reads only."""
    import time

    from solders.message import to_bytes_versioned
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction

    if not tx_signature:
        return "unknown"
    for attempt in range(max(1, int(attempts))):
        try:
            answer = rpc._call("getTransaction", [tx_signature, {"encoding": "base64", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        except Exception:
            answer = None
        if isinstance(answer, dict) and isinstance(answer.get("transaction"), list) and answer["transaction"]:
            try:
                tx = VersionedTransaction.from_bytes(base64.b64decode(str(answer["transaction"][0])))
            except Exception:
                return "unknown"
            if bytes(to_bytes_versioned(tx.message)) != bytes(signed_message):
                return "unknown"
            keys = list(tx.message.account_keys)
            try:
                index = keys.index(Pubkey.from_string(payer))
            except ValueError:
                return "unknown"
            if index >= len(tx.signatures) or not tx.signatures[index].verify(Pubkey.from_string(payer), bytes(signed_message)):
                return "unknown"
            if str(tx.signatures[0]) != str(tx_signature):
                return "unknown"
            meta = answer.get("meta") if isinstance(answer.get("meta"), dict) else {}
            return "failed" if meta.get("err") is not None else "settled"
        if attempt + 1 < attempts:
            time.sleep(wait_seconds)
    return "unknown"
