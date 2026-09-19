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

    def to_dict(self) -> dict[str, Any]:
        return {"amount_minor": self.amount_minor, "asset": self.asset, "network": self.network, "pay_to": self.pay_to, "resource": self.resource, "scheme": self.scheme, "description": self.description,
                "eip712_name": self.eip712_name, "eip712_version": self.eip712_version, "max_timeout_seconds": self.max_timeout_seconds, "asset_transfer_method": self.asset_transfer_method}


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
    return hashlib.sha256(f"{method.upper()}|{url}".encode()).hexdigest()


def _binding_row(row: Any) -> dict[str, Any]:
    keys = _BINDING_COLS.split(", ")
    return dict(zip(keys, row, strict=True))


def binding_for_digest(request_digest: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE request_digest = ?", (str(request_digest),)).fetchone()
    return _binding_row(row) if row else None


def binding_for_proposal(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE proposal_id = ? ORDER BY created_at DESC LIMIT 1", (str(proposal_id),)).fetchone()
    return _binding_row(row) if row else None


def _upsert_binding(*, request_digest: str, url: str, method: str, offer: X402Request, proposal_id: str, state: str) -> dict[str, Any]:
    """Record the v1 binding. An EVM-family offer also carries its EIP-712 domain facts,
    transfer method and truthful sponsorship (EIP-3009 exact: the facilitator settles
    on-chain and pays gas); the Solana v1 lane self-broadcasts and is NOT sponsored."""
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
    with connection() as conn:
        conn.execute(
            f"INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, state, created_at, updated_at{evm_columns})"
            f" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?{', ?' * len(evm_values)})"
            " ON CONFLICT(request_digest) DO UPDATE SET proposal_id = excluded.proposal_id, state = excluded.state, pay_to = excluded.pay_to, amount_minor = excluded.amount_minor, updated_at = excluded.updated_at",
            (f"x402b-{uuid.uuid4().hex[:16]}", request_digest, url, method.upper(), offer.pay_to, int(offer.amount_minor), offer.asset, offer.network, proposal_id, state, now, now, *evm_values),
        )
    return binding_for_digest(request_digest) or {}


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
    digest = hashlib.sha256(body).hexdigest()
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
    binding = binding_for_digest(digest)
    if binding and binding.get("proposal_id"):
        proposal = proposals.get_proposal(binding["proposal_id"])
        if int(binding.get("version") or 1) == 2 and proposal is not None and proposal.tx_signature and proposal.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
            # v2 delivery happens at signature submission; a re-fetch never re-sends payment
            # material, and the v1 X-PAYMENT header must never dress a v2 settlement.
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "v2_delivery_happens_at_submission"}, source_context=source_context)
        if proposal is not None and proposal.tx_signature and proposal.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
            return _deliver(binding, proposal, timeout=timeout, source_context=source_context)
        if proposal is not None and proposal.state in {proposals.STATE_PENDING_APPROVAL, proposals.STATE_APPROVED, proposals.STATE_AWAITING_SIGNATURE, proposals.STATE_SIGNED}:
            return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=PAYMENT_REQUIRED, proposal_id=proposal.proposal_id, binding_id=binding["binding_id"])
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
    from core.wallet import lifecycle

    prepared = lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    binding = _upsert_binding(request_digest=digest, url=clean_url, method=method, offer=offer, proposal_id=prepared.proposal_id, state=BINDING_PAYMENT_REQUIRED)
    return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=status, body=body, proposal_id=prepared.proposal_id, binding_id=binding.get("binding_id", ""), offer=offer)


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
    _upsert_binding_v2(
        request_digest=digest, url=clean_url, method=method, proposal_id=proposal.proposal_id,
        entry=entry, facilitator=facilitator,
    )
    from core.wallet import lifecycle as wallet_lifecycle

    wallet_lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    binding = binding_for_digest(digest) or {}
    return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=PAYMENT_REQUIRED, proposal_id=proposal.proposal_id, binding_id=binding.get("binding_id", ""), offer=_v2_to_v1_view(v2_offer))


def _v2_to_v1_view(offer: Any) -> Any:
    return None  # the v2 offer rides the binding record; the outcome view stays summary-level


def _upsert_binding_v2(*, request_digest: str, url: str, method: str, proposal_id: str, entry: Any, facilitator: str) -> dict[str, Any]:
    from core.wallet import chains, x402_v2

    now = utcnow()
    asset = chains.asset_for(entry.network, entry.asset)
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, state, created_at, updated_at,"
            " version, offer_json, resource_origin, resource_method, facilitator_id, eip712_name, eip712_version, asset_transfer_method, max_timeout_seconds, fee_asset, max_facilitator_fee_minor, max_network_fee_minor, sponsored_gas, asset_address)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(request_digest) DO UPDATE SET proposal_id = excluded.proposal_id, offer_json = excluded.offer_json, facilitator_id = excluded.facilitator_id, updated_at = excluded.updated_at",
            (
                f"x402b-{uuid.uuid4().hex[:16]}", request_digest, url, method.upper(), entry.pay_to, int(entry.amount_minor), asset.symbol, entry.network, proposal_id, BINDING_PAYMENT_REQUIRED, now, now,
                2, dumps(entry.to_dict()), x402_v2.resource_origin_of(url), entry.resource_method, facilitator,
                entry.eip712_name, entry.eip712_version, entry.asset_transfer_method, int(entry.max_timeout_seconds), asset.symbol, 0, 0,
                # EIP-3009 exact: the payer signs an authorization and the FACILITATOR settles
                # on-chain and pays gas — sponsored is the truthful record, and the payer's
                # reserved fee is genuinely zero. A future payer-gas method records 0 here.
                1 if str(entry.asset_transfer_method or "eip3009") == "eip3009" else 0,
                asset.address,
            ),
        )
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
