"""x402 (HTTP 402 Payment Required) detection and the capped proposal it turns into.

A 402 with an ``accepts[]`` block (body or ``X-Payment-Required`` header) becomes a typed
proposal of origin ``x402`` that rides the same lifecycle as any other payment: simulation,
limits, owner approval, signing, broadcast. Above the automatic cap it is a typed refusal
before a proposal exists. The runtime never pays a 402 on its own authority.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.wallet import config, custody, proposals
from core.wallet.redaction import publish_identifier
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.x402"
PAYMENT_REQUIRED = 402
_HEADER_NAMES = ("x-payment-required", "payment-required")


@dataclass(frozen=True)
class X402Request:
    amount_minor: int
    asset: str
    network: str
    pay_to: str
    resource: str
    scheme: str = "exact"
    description: str = ""
    #: the offer's EIP-712 domain facts (extra.name / extra.version) and window request —
    #: carried so an EVM v1 offer can be signed against the domain the server declared
    eip712_name: str = ""
    eip712_version: str = ""
    max_timeout_seconds: int = 0
    asset_transfer_method: str = ""
    #: the fee payer a canonical Solana offer names (extra.feePayer / feePayerKey): the resource settles a transaction
    #: that payer co-signs, so such an offer is never a plain transfer of this wallet's own
    fee_payer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"amount_minor": self.amount_minor, "asset": self.asset, "network": self.network, "pay_to": self.pay_to, "resource": self.resource, "scheme": self.scheme, "description": self.description,
                "eip712_name": self.eip712_name, "eip712_version": self.eip712_version, "max_timeout_seconds": self.max_timeout_seconds, "asset_transfer_method": self.asset_transfer_method,
                "fee_payer": self.fee_payer}


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


def _from_accepts(payload: dict[str, Any]) -> X402Request | None:
    accepts = payload.get("accepts")
    if not isinstance(accepts, list) or not accepts or not isinstance(accepts[0], dict):
        return None
    first = accepts[0]
    try:
        amount = int(str(first.get("maxAmountRequired") or first.get("amount") or "0").strip())
    except ValueError:
        return None
    pay_to = str(first.get("payTo") or "").strip()
    if amount <= 0 or not pay_to:
        return None
    extra = first.get("extra") if isinstance(first.get("extra"), dict) else {}
    try:
        timeout = int(str(first.get("maxTimeoutSeconds") or "0").strip())
    except ValueError:
        timeout = 0
    return X402Request(
        amount_minor=amount, asset=str(first.get("asset") or "SOL").strip(), network=str(first.get("network") or "").strip().lower(),
        pay_to=pay_to, resource=str(first.get("resource") or "")[:200], scheme=str(first.get("scheme") or "exact"), description=str(first.get("description") or "")[:200],
        eip712_name=str(extra.get("name") or "").strip()[:64], eip712_version=str(extra.get("version") or "").strip()[:16],
        max_timeout_seconds=timeout, asset_transfer_method=str(extra.get("assetTransferMethod") or "").strip()[:32],
        fee_payer=str(first.get("feePayerKey") or extra.get("feePayer") or "").strip()[:64],
    )


def detect_x402(status: int, headers: dict[str, Any] | None, body: Any) -> X402Request | None:
    """A payable request, or None. Anything that is not a well-formed 402 offer is not payable."""
    try:
        if int(status) != PAYMENT_REQUIRED:
            return None
    except (TypeError, ValueError):
        return None
    parsed = _as_json(body)
    found = _from_accepts(parsed) if parsed else None
    if found is not None:
        return found
    for name, value in (headers or {}).items():
        if str(name).lower() in _HEADER_NAMES:
            parsed = _as_json(value)
            found = _from_accepts(parsed) if parsed else None
            if found is not None:
                return found
    return None


def propose_from_x402(request: X402Request, *, wallet_id: str, source_context: dict[str, Any] | None = None) -> proposals.TransactionProposal:
    custody.require_enabled(source_context=source_context)
    from core.wallet import x402_v2

    if request.fee_payer and x402_v2.names_solana(request.network):
        # a canonical Solana offer: the resource's fee payer settles a transaction it co-signs. This lane would
        # broadcast a plain transfer of its own instead, which the resource never accepts, so it never proposes one
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "canonical_solana_offer_needs_paykit_lane"}, source_context=source_context)
    if not config.network_allowed(request.network):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": request.network, "reason": "x402_offer_network"}, source_context=source_context)
    cap = config.x402_cap_minor()
    if request.amount_minor > cap:
        raise wallet_fault("wallet_x402_cap_exceeded", authority=AUTHORITY, context={"amount_minor": request.amount_minor, "limit": str(cap), "asset": request.asset, "reason": "above_automatic_cap"}, source_context=source_context)
    key_material = "|".join([request.resource, request.pay_to, str(request.amount_minor), request.asset, request.network])
    idempotency_key = "x402:" + hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24]
    return proposals.propose_transaction(
        wallet_id=wallet_id, destination=request.pay_to, amount_minor=request.amount_minor, asset=request.asset, origin=proposals.ORIGIN_X402,
        memo=f"x402 {request.resource}"[:200], idempotency_key=idempotency_key, source_context=source_context,
    )


# --- the paid-resource flow: fetch -> 402 -> capped proposal -> (operator approval) -> retry with X-PAYMENT -> bound receipt ---

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from core.wallet import outbound, receipts
from core.wallet.errors import WalletFault
from core.wallet.store import connection, dumps, utcnow

OUTCOME_DELIVERED = "delivered"
OUTCOME_PAYMENT_REQUIRED = "payment_required"
OUTCOME_REFUSED = "refused"
BINDING_PAYMENT_REQUIRED = "payment_required"
BINDING_PAID = "paid"
BINDING_DELIVERED = "delivered"
ALLOW_LOOPBACK_ENV = "VOOL_WALLET_X402_ALLOW_LOOPBACK"
_BINDING_COLS = "binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, tx_signature, state, resource_status, resource_digest, resource_bytes, created_at, updated_at, version, offer_json, resource_origin, resource_method, facilitator_id, eip712_name, eip712_version, asset_transfer_method, nonce, deadline, expires_at, max_facilitator_fee_minor, max_network_fee_minor, sponsored_gas, fee_asset, max_timeout_seconds, asset_address"
#: A request's binding is never handed to another proposal while its own proposal is being prepared, waits on its
#: owner, is being paid or has paid (these states: every state but a terminal refusal), nor while that proposal still
#: holds reserved spend: see :func:`binding_guard`. A door binds its proposal BEFORE preparing it, so the request is
#: held from the moment a proposal for it exists.
PREPARING_STATES = (proposals.STATE_PROPOSED, proposals.STATE_SIMULATED, proposals.STATE_LIMITS_CHECKED)
BINDING_HELD_STATES = (*PREPARING_STATES, proposals.STATE_PENDING_APPROVAL, proposals.STATE_APPROVED, proposals.STATE_AWAITING_SIGNATURE,
                       proposals.STATE_SIGNED, proposals.STATE_BROADCAST, proposals.STATE_CONFIRMED)
#: Preparing moves a proposal on within seconds (each step is a bounded read). One still preparing this long after its
#: last step was abandoned (its process stopped mid-prepare): see :func:`end_abandoned_prepare`.
ABANDONED_PREPARE_SECONDS = 300.0


@dataclass(frozen=True)
class X402Outcome:
    status: str
    http_status: int
    body: bytes = b""
    proposal_id: str = ""
    binding_id: str = ""
    tx_signature: str = ""
    repaid: bool = False
    offer: X402Request | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "http_status": self.http_status, "proposal_id": self.proposal_id, "binding_id": self.binding_id, "tx_signature": self.tx_signature, "repaid": self.repaid, "body_bytes": len(self.body), "offer": self.offer.to_dict() if self.offer else None}


def _target_allowed(url: str) -> bool:
    """The v1 lane rides the SAME confinement as the v2 lane: the outbound origin policy
    (DNS/IP screening, private-namespace refusal, loopback only behind the explicit switch)."""
    try:
        outbound.validate_target(str(url or ""))
    except WalletFault:
        return False
    return True


def _request(url: str, *, method: str, headers: dict[str, str], timeout: float, payment_headers: dict[str, str] | None = None, payment_origin: str = "") -> dict[str, Any]:
    """One confined fetch: bounded read, redirects re-validated hop by hop, payment headers
    attached ONLY to the approved origin's hop, as unredirected headers, and a redirect AT
    that payment-carrying hop is a typed refusal — payment material never rides a redirect."""
    return outbound.fetch(url, method=method, headers=headers, timeout=timeout, payment_headers=payment_headers, payment_origin=payment_origin, payment_redirect="refuse")


def _origin_of(url: str) -> str:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = parts.port
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _request_digest(method: str, url: str) -> str:
    """The binding key for one request. The wallet mints it, so it also vouches for it here:
    registered as a public identifier the moment it exists, because receipt persistence must
    keep this commitment byte-for-byte while the canonical masker keeps masking lookalikes."""
    return publish_identifier(hashlib.sha256(f"{method.upper()}|{url}".encode()).hexdigest())


def _binding_row(row: Any) -> dict[str, Any]:
    keys = _BINDING_COLS.split(", ")
    binding = dict(zip(keys, row, strict=True))
    # the binding store is the durable record of minted request digests; the registry is
    # process-local, so every load from this trusted store re-vouches for its digest and a
    # receipt written after a restart still round-trips unchanged.
    publish_identifier(str(binding.get("request_digest") or ""))
    return binding


def binding_for_digest(request_digest: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE request_digest = ?", (str(request_digest),)).fetchone()
    return _binding_row(row) if row else None


def binding_for_proposal(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE proposal_id = ? ORDER BY created_at DESC LIMIT 1", (str(proposal_id),)).fetchone()
    return _binding_row(row) if row else None


def _upsert_binding(*, request_digest: str, url: str, method: str, offer: X402Request, proposal_id: str, state: str) -> dict[str, Any] | None:
    """Record the v1 binding. An EVM-family offer also carries its EIP-712 domain facts,
    transfer method and truthful sponsorship (EIP-3009 exact: the facilitator settles
    on-chain and pays gas); the Solana v1 lane self-broadcasts and is NOT sponsored.
    None when the request is already bound to a payment that holds it (:func:`binding_guard`)."""
    now = utcnow()
    is_evm = False
    try:
        from core.wallet import chains as _chains

        is_evm = _chains.resolve_network(offer.network).is_evm
    except Exception:
        is_evm = False
    evm_columns = ""
    evm_values: tuple = ()
    if is_evm:
        evm_columns = ", version, eip712_name, eip712_version, asset_transfer_method, max_timeout_seconds, fee_asset, max_facilitator_fee_minor, max_network_fee_minor, sponsored_gas, asset_address"
        evm_values = (1, offer.eip712_name, offer.eip712_version, offer.asset_transfer_method or "eip3009", int(offer.max_timeout_seconds or 0), offer.asset, 0, 0, 1, offer.asset)
    guard, guard_values = binding_guard()
    with connection() as conn:
        cursor = conn.execute(
            f"INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, state, created_at, updated_at{evm_columns})"
            f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?{', ?' * len(evm_values)})"
            " ON CONFLICT(request_digest) DO UPDATE SET proposal_id = excluded.proposal_id, state = excluded.state, pay_to = excluded.pay_to, amount_minor = excluded.amount_minor, updated_at = excluded.updated_at"
            + guard,
            (f"x402b-{uuid.uuid4().hex[:16]}", request_digest, url, method.upper(), offer.pay_to, int(offer.amount_minor), offer.asset, offer.network, proposal_id, state, now, now, *evm_values, *guard_values),
        )
        if cursor.rowcount != 1:
            return None
    return binding_for_digest(request_digest) or {}


def binding_guard() -> tuple[str, tuple[Any, ...]]:
    """The WHERE clause (and its parameters) of a binding upsert's ON CONFLICT update, which makes rebinding a request
    one compare-and-set: the stored binding takes another proposal only while its own proposal is in none of
    :data:`BINDING_HELD_STATES` and holds no reserved spend (principal or fee companion). Otherwise the upsert changes
    no row, and its caller learns the request is already another payment's. Two callers racing the same request,
    each past its own first check, can therefore never both bind it."""
    from core.wallet import limits

    states = ", ".join("?" for _ in BINDING_HELD_STATES)
    return (
        f" WHERE NOT EXISTS (SELECT 1 FROM wallet_proposals p WHERE p.proposal_id = wallet_x402_bindings.proposal_id AND p.state IN ({states}))"
        " AND NOT EXISTS (SELECT 1 FROM wallet_spend_ledger l WHERE l.proposal_id IN (wallet_x402_bindings.proposal_id, wallet_x402_bindings.proposal_id || ?)"
        " AND l.state = ?)",
        (*BINDING_HELD_STATES, limits._fee_hold_id(""), limits.RESERVATION_RESERVED),
    )


def reject_unbound(proposal_id: str, *, bound_proposal_id: str) -> None:
    """A proposal minted for a request that another proposal holds never becomes approvable: it is rejected while still
    proposed, and its receipt (nothing charged) commits with it. A proposal that is the bound one, or that was not
    minted just now, is left as it is."""
    if proposal_id and proposal_id != bound_proposal_id:
        from core.wallet import lifecycle

        lifecycle.end_refused(proposal_id, proposals.STATE_REJECTED, fault_code="wallet_duplicate_payment", expected_state=proposals.STATE_PROPOSED,
                              detail={"reason": "request_bound_to_another_payment", "bound_proposal_id": bound_proposal_id}, reason="request_bound_to_another_payment")


def prepare_bound(proposal_id: str, *, source_context: dict[str, Any] | None) -> proposals.TransactionProposal:
    """Prepare a proposal a door has just bound to its request. When prepare refuses without having ended the proposal
    (an inactive network, a missing wallet, an unexpected error), the proposal is rejected here under that fault, with
    its receipt, so the binding never holds the request for a proposal that cannot become approvable; the refusal is
    raised as it came. A later fetch of the same request proposes afresh."""
    from core.wallet import lifecycle

    try:
        return lifecycle.default_lifecycle(source_context=source_context).prepare(proposal_id)
    except Exception as exc:
        current = proposals.get_proposal(proposal_id)
        if current is not None and current.state in PREPARING_STATES:
            code = exc.code if isinstance(exc, WalletFault) else "wallet_dependency_unavailable"
            lifecycle.end_refused(proposal_id, proposals.STATE_REJECTED, fault_code=code, expected_state=current.state, detail={"reason": "prepare_refused", "fault": code},
                                  reason="prepare_refused")
        raise


def end_abandoned_prepare(proposal: proposals.TransactionProposal | None) -> None:
    """A request's proposal still preparing :data:`ABANDONED_PREPARE_SECONDS` after its last step was abandoned: it was
    never approved and never held spend, so it is rejected here, with its receipt and nothing charged, and the request
    is free for the next fetch instead of closed for good. A younger one, or one whose age cannot be read, keeps the
    request (fail closed). The move is a compare-and-set on the state it was read in, committed with its receipt, so a
    prepare still running cannot be overtaken: if it moved first, nothing changes; if this moves first, its next step
    finds it ended."""
    if proposal is None or proposal.state not in PREPARING_STATES:
        return
    try:
        since = datetime.fromisoformat(str(proposal.updated_at))
    except ValueError:
        return
    if since.tzinfo is None or datetime.now(timezone.utc) - since < timedelta(seconds=ABANDONED_PREPARE_SECONDS):
        return
    from core.wallet import lifecycle

    lifecycle.end_refused(proposal.proposal_id, proposals.STATE_REJECTED, fault_code="wallet_quote_expired", expected_state=proposal.state,
                          detail={"reason": "prepare_abandoned"}, reason="prepare_abandoned")


def _update_binding(request_digest: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connection() as conn:
        conn.execute(f"UPDATE wallet_x402_bindings SET {sets}, updated_at = ? WHERE request_digest = ?", (*fields.values(), utcnow(), str(request_digest)))


def parse_settlement_response_v1(header_value: Any) -> dict[str, Any] | None:
    """The official v1 ``X-PAYMENT-RESPONSE``: base64(JSON ``{success, transaction, network,
    payer[, errorReason]}``). A claim, not a proof — settlement is verified from the chain."""
    raw = str(header_value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(base64.b64decode(raw))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def payment_header_for(proposal_id: str) -> str:
    """The x402 v1 `exact` scheme payment header for a CONFIRMED proposal: base64(JSON)."""
    proposal = proposals.get_proposal(proposal_id)
    if proposal is None or not proposal.tx_signature or proposal.state not in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
        raise wallet_fault("wallet_approval_rejected", authority=AUTHORITY, context={"proposal_id": str(proposal_id), "reason": "payment_not_confirmed"})
    profile = custody.require_wallet(proposal.wallet_id)
    payload = {
        "x402Version": 1, "scheme": "exact", "network": proposal.network,
        "payload": {"signature": proposal.tx_signature, "payer": profile.public_key, "payTo": proposal.destination, "amount": str(proposal.amount_minor), "asset": proposal.asset},
    }
    return base64.b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")


def _deliver(binding: dict[str, Any], proposal: proposals.TransactionProposal, *, timeout: float, source_context: dict[str, Any] | None) -> X402Outcome:
    header = payment_header_for(proposal.proposal_id)
    approved_origin = _origin_of(binding["url"])
    answer = _request(
        binding["url"], method=binding["method"], headers={"Accept": "*/*"}, timeout=timeout,
        payment_headers={"X-PAYMENT": header}, payment_origin=approved_origin,
    )
    if _origin_of(answer["url"]) != approved_origin:
        # the payment hop redirected: the proof stays pinned to the challenged origin, so a
        # delivery from anywhere else is a typed refusal, never a silent substitute fetch.
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "payment_hop_redirected", "approved_origin": approved_origin[:80], "final_origin": _origin_of(answer["url"])[:80]}, source_context=source_context)
    status = int(answer["status"])
    body = answer["body"]
    if status == PAYMENT_REQUIRED or status >= 400:
        _update_binding(binding["request_digest"], state=BINDING_PAID, resource_status=int(status))
        return X402Outcome(status=OUTCOME_REFUSED, http_status=status, body=body, proposal_id=proposal.proposal_id, binding_id=binding["binding_id"], tx_signature=proposal.tx_signature)
    digest = publish_identifier(hashlib.sha256(body).hexdigest())
    first_delivery = binding.get("state") != BINDING_DELIVERED
    _update_binding(binding["request_digest"], state=BINDING_DELIVERED, tx_signature=proposal.tx_signature, resource_status=int(status), resource_digest=digest, resource_bytes=len(body))
    if first_delivery:
        receipts.record_receipt(proposal, state="delivered", tx_signature=proposal.tx_signature, extra={"x402": {"request_digest": binding["request_digest"], "url": binding["url"], "resource_status": int(status), "resource_digest": digest, "resource_bytes": len(body)}})
    return X402Outcome(status=OUTCOME_DELIVERED, http_status=status, body=body, proposal_id=proposal.proposal_id, binding_id=binding["binding_id"], tx_signature=proposal.tx_signature)


def fetch_paid_resource(url: str, *, wallet_id: str, source_context: dict[str, Any] | None = None, method: str = "GET", headers: dict[str, str] | None = None, timeout: float = 20.0) -> X402Outcome:
    """Fetch; on a 402 offer park a capped proposal (or reuse the parked/paid one). Never pays on its own."""
    custody.require_enabled(source_context=source_context)
    clean_url = str(url or "").strip()
    if not _target_allowed(clean_url):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"reason": "x402_target_not_public", "host": urlsplit(clean_url).hostname or ""}, source_context=source_context)
    digest = _request_digest(method, clean_url)
    bound = _bound_outcome(binding_for_digest(digest), timeout=timeout, source_context=source_context)
    if bound is not None:
        return bound
    answer = _request(clean_url, method=method, headers={**dict(headers or {}), "Accept": "*/*"}, timeout=timeout)
    status = int(answer["status"])
    body = answer["body"]
    response_headers = answer["headers"]
    if status != PAYMENT_REQUIRED:
        return X402Outcome(status=OUTCOME_DELIVERED if status < 400 else OUTCOME_REFUSED, http_status=status, body=body)
    # a v2 challenge is parsed ONLY by the v2 parser: never silently downgraded to v1
    from core.wallet import x402_v2

    v2_offer = x402_v2.parse_payment_required(response_headers, body, status=status)
    if v2_offer is not None:
        return _fetch_v2(v2_offer, clean_url, method, wallet_id, source_context=source_context)
    if _looks_like_v2(response_headers, body):
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "v2_challenge_unparseable"}, source_context=source_context)
    offer = detect_x402(status, response_headers, body)
    if offer is None:
        return X402Outcome(status=OUTCOME_REFUSED, http_status=status, body=body)
    proposal = propose_from_x402(offer, wallet_id=wallet_id, source_context=source_context)
    # bound before it is prepared: a proposal that lost the request to another payment never becomes approvable
    binding = _upsert_binding(request_digest=digest, url=clean_url, method=method, offer=offer, proposal_id=proposal.proposal_id, state=BINDING_PAYMENT_REQUIRED)
    if binding is None:
        return _lost_request(digest, proposal.proposal_id, timeout=timeout, source_context=source_context)
    prepared = prepare_bound(proposal.proposal_id, source_context=source_context)
    return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=status, body=body, proposal_id=prepared.proposal_id, binding_id=binding.get("binding_id", ""), offer=offer)


def _bound_outcome(binding: dict[str, Any] | None, *, timeout: float, source_context: dict[str, Any] | None) -> X402Outcome | None:
    """What a request this lane already bound gets before anything is sent: its parked proposal while one waits, its
    one delivery once paid with a known transaction, a typed refusal while the payment's outcome is unknown, else None
    (nothing holds the request: it may be fetched afresh)."""
    if not binding or not binding.get("proposal_id"):
        return None
    proposal = proposals.get_proposal(binding["proposal_id"])
    if proposal is None:
        return None
    if proposal.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
        if int(binding.get("version") or 1) == 2:
            # v2 delivery happens at signature submission; a re-fetch never re-sends payment
            # material, and the v1 X-PAYMENT header must never dress a v2 settlement.
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "v2_delivery_happens_at_submission"}, source_context=source_context)
        if not proposal.tx_signature:
            # a submission that learned no transaction is unknown, not failed, on either
            # wire: it may still settle, so the request is refused before anything is sent.
            # A fresh fetch here would let a resource asking on other terms park a second
            # payment for the same request.
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "payment_outcome_unknown"}, source_context=source_context)
        return _deliver(binding, proposal, timeout=timeout, source_context=source_context)
    if proposal.state in {proposals.STATE_PENDING_APPROVAL, proposals.STATE_APPROVED, proposals.STATE_AWAITING_SIGNATURE, proposals.STATE_SIGNED}:
        return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=PAYMENT_REQUIRED, proposal_id=proposal.proposal_id, binding_id=binding["binding_id"])
    end_abandoned_prepare(proposal)
    refuse_while_dispatched(proposal, authority=AUTHORITY, source_context=source_context)
    return None


def refuse_while_dispatched(proposal: proposals.TransactionProposal, *, authority: str, source_context: dict[str, Any] | None) -> None:
    """The dispatch record decides, not the state column or a transaction id: while the payment's spend is still held
    as reserved, nothing has proven what became of it (it left, or may have), so its request stays closed whatever its
    state says. A refusal before sending releases the hold; a proven outcome settles it."""
    from core.wallet import limits

    if limits.reservation_state(proposal.proposal_id) == limits.RESERVATION_RESERVED:
        raise wallet_fault("wallet_duplicate_payment", authority=authority, context={"proposal_id": proposal.proposal_id, "reason": "payment_outcome_unknown", "status": proposal.state}, source_context=source_context)


def _lost_request(digest: str, proposal_id: str, *, timeout: float, source_context: dict[str, Any] | None) -> X402Outcome:
    """Another caller bound this request first (its 402 arrived while ours was in flight): the proposal minted here is
    rejected, and this caller gets what the request's own payment gives a re-fetch."""
    binding = binding_for_digest(digest)
    reject_unbound(proposal_id, bound_proposal_id=str((binding or {}).get("proposal_id") or ""))
    bound = _bound_outcome(binding, timeout=timeout, source_context=source_context)
    if bound is not None:
        return bound
    raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "request_bound_to_another_payment"}, source_context=source_context)


def _looks_like_v2(headers: dict[str, Any] | None, body: Any) -> bool:
    for name in (headers or {}):
        if str(name).lower() == x402_v2_header_name():
            return True
    parsed = _as_json(body)
    return isinstance(parsed, dict) and int(parsed.get("x402Version") or 0) == 2


def lifecycle_default_engine():
    """The one lifecycle engine for this lane's flows (test and API convenience)."""
    from core.wallet import lifecycle

    return lifecycle.default_lifecycle()


def x402_v2_header_name() -> str:
    from core.wallet import x402_v2

    return x402_v2.HEADER_PAYMENT_REQUIRED.lower()


def _fetch_v2(v2_offer, clean_url: str, method: str, wallet_id: str, *, source_context: dict[str, Any] | None) -> X402Outcome:
    """The v2 lane: deterministic selection, capped proposal, binding with the full offer
    evidence, lifecycle prepare. Never pays; above the cap refuses before the proposal."""
    from core.wallet import facilitators, x402_v2

    entry, reason = x402_v2.select_offer(v2_offer, wallet_id=wallet_id, source_context=source_context)
    if entry is None:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": reason}, source_context=source_context)
    proposal = x402_v2.propose_from_v2_offer(v2_offer, entry, wallet_id=wallet_id, source_context=source_context)
    digest = _request_digest(method, clean_url)
    facilitator = ""
    try:
        capability = facilitators.require_capability(entry.network, entry.scheme, source_context=source_context)
        facilitator = f"{capability.facilitator_id}@{capability.origin}"
    except Exception:
        facilitator = ""  # prepare() re-checks and refuses typed when missing
    if _upsert_binding_v2(
        request_digest=digest, url=clean_url, method=method, proposal_id=proposal.proposal_id,
        entry=entry, facilitator=facilitator,
    ) is None:
        return _lost_request(digest, proposal.proposal_id, timeout=20.0, source_context=source_context)
    prepare_bound(proposal.proposal_id, source_context=source_context)
    binding = binding_for_digest(digest) or {}
    return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=PAYMENT_REQUIRED, proposal_id=proposal.proposal_id, binding_id=binding.get("binding_id", ""), offer=_v2_to_v1_view(v2_offer))


def _v2_to_v1_view(offer: Any) -> Any:
    return None  # the v2 offer rides the binding record; the outcome view stays summary-level


def _upsert_binding_v2(*, request_digest: str, url: str, method: str, proposal_id: str, entry: Any, facilitator: str) -> dict[str, Any] | None:
    """Record the v2 binding with the full offer evidence; None when the request is already bound to a payment that
    holds it (:func:`binding_guard`)."""
    from core.wallet import chains, x402_v2

    now = utcnow()
    asset = chains.asset_for(entry.network, entry.asset)
    guard, guard_values = binding_guard()
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, state, created_at, updated_at,"
            " version, offer_json, resource_origin, resource_method, facilitator_id, eip712_name, eip712_version, asset_transfer_method, max_timeout_seconds, fee_asset, max_facilitator_fee_minor, max_network_fee_minor, sponsored_gas, asset_address)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(request_digest) DO UPDATE SET proposal_id = excluded.proposal_id, offer_json = excluded.offer_json, facilitator_id = excluded.facilitator_id, updated_at = excluded.updated_at"
            + guard,
            (
                f"x402b-{uuid.uuid4().hex[:16]}", request_digest, url, method.upper(), entry.pay_to, int(entry.amount_minor), asset.symbol, entry.network, proposal_id, BINDING_PAYMENT_REQUIRED, now, now,
                2, dumps(entry.to_dict()), x402_v2.resource_origin_of(url), entry.resource_method, facilitator,
                entry.eip712_name, entry.eip712_version, entry.asset_transfer_method, int(entry.max_timeout_seconds), asset.symbol, 0, 0,
                # EIP-3009 exact: the payer signs an authorization and the FACILITATOR settles
                # on-chain and pays gas — sponsored is the truthful record, and the payer's
                # reserved fee is genuinely zero. A future payer-gas method records 0 here.
                1 if str(entry.asset_transfer_method or "eip3009") == "eip3009" else 0,
                asset.address,
                *guard_values,
            ),
        )
        if cursor.rowcount != 1:
            return None
    return binding_for_digest(request_digest) or {}


def retry_paid_resource(proposal_id: str, *, source_context: dict[str, Any] | None = None, timeout: float = 20.0) -> X402Outcome:
    """After the operator approved and the payment confirmed: retry the request with X-PAYMENT and bind the receipt. Never repays.

    v2 lane: the one approved submission happens at signature submission
    (lifecycle.submit_external_signature); a retry call here refuses rather than sending a
    second payment for the same binding."""
    custody.require_enabled(source_context=source_context)
    proposal = proposals.get_proposal(proposal_id)
    binding = binding_for_proposal(proposal_id)
    if proposal is None or binding is None:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"proposal_id": str(proposal_id), "reason": "no_x402_binding"}, source_context=source_context)
    if int(binding.get("version") or 1) == 2:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "v2_delivery_happens_at_submission"}, source_context=source_context)
    if not proposal.tx_signature or proposal.state not in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
        raise wallet_fault("wallet_approval_rejected", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "payment_not_confirmed", "status": proposal.state}, source_context=source_context)
    return _deliver(binding, proposal, timeout=timeout, source_context=source_context)
