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
from dataclasses import dataclass, replace
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


def offer_key(wire: str, *terms: Any) -> str:
    """The idempotency key of an x402 proposal, the same at every door: the fetch door at any spelling of a URL, and the
    doors that propose an offer they were shown. It names the offer alone (its wire, resource, payee, amount, asset and
    network, the last two as :func:`canonical_offer` stores them), so one offer is one proposal, approved and paid at
    most once; every request that reaches the offer is bound to that proposal (:class:`_RequestClaim`). An offer that
    differs (repriced, or naming another payee or resource) is another proposal, paid only as a request's payment
    (:func:`names_a_request`)."""
    prefix = {"v1": "x402:", "v2": "x402v2:"}[wire]
    return prefix + hashlib.sha256("|".join(str(term) for term in terms).encode("utf-8")).hexdigest()[:24]


def canonical_offer(network: str, asset: str) -> tuple[str, str]:
    """An offer's network and asset as a proposal stores them (the declared network, the asset row's symbol), so every
    spelling of one offer has one key. As given when either does not resolve: the proposal then refuses it as it
    would."""
    from core.wallet import chains

    if not chains.is_declared(network):
        return str(network), str(asset)
    spec = chains.resolve_network(network)
    try:
        return spec.network, proposals._validated_asset(spec.network, asset, source_context=None)
    except WalletFault:
        return spec.network, str(asset)


def propose_from_x402(request: X402Request, *, wallet_id: str, source_context: dict[str, Any] | None = None, claim: Any = None) -> proposals.TransactionProposal:
    """The capped proposal for a v1 offer, keyed by the offer (:func:`offer_key`). The fetch door passes its ``claim`` on
    the request it fetched (see :func:`proposals.propose_transaction`); an offer proposed on its own names no request."""
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
    network, asset = canonical_offer(request.network, request.asset)
    idempotency_key = offer_key("v1", request.resource, request.pay_to, str(request.amount_minor), asset, network)
    return proposals.propose_transaction(
        wallet_id=wallet_id, destination=request.pay_to, amount_minor=request.amount_minor, asset=request.asset, origin=proposals.ORIGIN_X402,
        memo=f"x402 {request.resource}"[:200], idempotency_key=idempotency_key, source_context=source_context, claim=claim,
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
#: The binding version of the pay-kit lane (core.wallet.paykit_x402): the wire version (x402 v1 or v2) is in offer_json.
BINDING_VERSION_PAYKIT = 3
#: The binding version of an MPP charge built by Solana pay-kit (core.wallet.paykit_mpp): offer_json holds the challenge.
BINDING_VERSION_PAYKIT_MPP = 4
PAYKIT_BINDING_VERSIONS = (BINDING_VERSION_PAYKIT, BINDING_VERSION_PAYKIT_MPP)
ALLOW_LOOPBACK_ENV = "VOOL_WALLET_X402_ALLOW_LOOPBACK"
_BINDING_COLS = "binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, tx_signature, state, resource_status, resource_digest, resource_bytes, created_at, updated_at, version, offer_json, resource_origin, resource_method, facilitator_id, eip712_name, eip712_version, asset_transfer_method, nonce, deadline, expires_at, max_facilitator_fee_minor, max_network_fee_minor, sponsored_gas, fee_asset, max_timeout_seconds, asset_address, request_body_b64, request_headers_json, fee_payer"
#: A request's binding is never handed to another proposal while its own proposal is being prepared, waits on its
#: owner, is being paid or has paid (these states: every state but a terminal refusal), nor while that proposal still
#: holds reserved spend or its amount has settled as moved: see :func:`binding_guard`. A door binds its proposal BEFORE preparing it, so the request is
#: held from the moment a proposal for it exists.
PREPARING_STATES = (proposals.STATE_PROPOSED, proposals.STATE_SIMULATED, proposals.STATE_LIMITS_CHECKED)
BINDING_HELD_STATES = (*PREPARING_STATES, proposals.STATE_PENDING_APPROVAL, proposals.STATE_APPROVED, proposals.STATE_AWAITING_SIGNATURE,
                       proposals.STATE_SIGNED, proposals.STATE_BROADCAST, proposals.STATE_CONFIRMED)
#: Preparing moves a proposal on within seconds (each step is a bounded read). One still preparing this long after its
#: last step was abandoned (its process stopped mid-prepare): see :func:`end_abandoned_prepare`.
ABANDONED_PREPARE_SECONDS = 300.0
#: The event a binding leaves on the proposal it names, when it is written: a proposal a request was ever bound to is
#: paid only while a request's binding still names it (:func:`owns_its_request`).
EVENT_REQUEST_CLAIMED = "x402_request_claimed"
#: The event a binding leaves on its proposal when a later payment for the same request replaces it: the whole binding
#: as it was, and the proposal that replaced it.
EVENT_BINDING_REPLACED = "x402_binding_replaced"
#: What a binding column holds when an offer says nothing about it: the table's own defaults. A binding is always
#: written whole, so a replaced binding keeps nothing of the payment before it.
_BINDING_DEFAULTS: dict[str, Any] = {
    "tx_signature": "", "resource_status": 0, "resource_digest": "", "resource_bytes": 0, "version": 1, "offer_json": "", "resource_origin": "",
    "resource_method": "GET", "facilitator_id": "", "eip712_name": "", "eip712_version": "", "asset_transfer_method": "", "nonce": "", "deadline": 0,
    "expires_at": 0, "max_facilitator_fee_minor": 0, "max_network_fee_minor": 0, "sponsored_gas": 0, "fee_asset": "", "max_timeout_seconds": 60, "asset_address": "",
    "request_body_b64": "", "request_headers_json": "{}", "fee_payer": "",
}


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
    """The payment's own binding: the request it was parked for, the first binding that named it. Other requests that
    reach the same offer are bound to it later and get that payment's outcome (:func:`_bound_outcome`), but its
    approval, the authorization it signs, its submission and its delivery here are for this request. A binding naming
    a live payment is never handed to another (:func:`binding_guard`), so this one stays its own while it is paid."""
    with connection() as conn:
        row = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE proposal_id = ? ORDER BY created_at ASC, rowid ASC LIMIT 1", (str(proposal_id),)).fetchone()
    return _binding_row(row) if row else None


def _v1_terms(offer: X402Request) -> dict[str, Any]:
    """A v1 binding's terms. An EVM-family offer also carries its EIP-712 domain facts, transfer method and truthful
    sponsorship (EIP-3009 exact: the facilitator settles on-chain and pays gas); the Solana v1 lane self-broadcasts and
    is NOT sponsored."""
    terms: dict[str, Any] = {"pay_to": offer.pay_to, "amount_minor": int(offer.amount_minor), "asset": offer.asset, "network": offer.network}
    try:
        from core.wallet import chains as _chains

        is_evm = _chains.resolve_network(offer.network).is_evm
    except Exception:
        is_evm = False
    if is_evm:
        terms.update(version=1, eip712_name=offer.eip712_name, eip712_version=offer.eip712_version, asset_transfer_method=offer.asset_transfer_method or "eip3009",
                     max_timeout_seconds=int(offer.max_timeout_seconds or 0), fee_asset=offer.asset, max_facilitator_fee_minor=0, max_network_fee_minor=0, sponsored_gas=1,
                     asset_address=offer.asset)
    return terms


def _claim_request(conn: Any, *, request_digest: str, url: str, method: str, proposal_id: str, terms: dict[str, Any]) -> bool:
    """Bind the request to ``proposal_id`` on the offer's ``terms``, on the caller's connection: a first binding, or a
    whole replacement of one whose payment holds nothing (:func:`binding_guard`). Every column is written, wire, chain,
    asset, fees, resource and payment result alike, so nothing of a replaced payment's offer stays; the replaced binding
    is kept, whole, as an event on its own proposal. False when another payment holds the request."""
    columns = _BINDING_COLS.split(", ")
    now = utcnow()
    row = {**_BINDING_DEFAULTS, **terms, "binding_id": f"x402b-{uuid.uuid4().hex[:16]}", "request_digest": request_digest, "url": url, "method": method.upper(),
           "proposal_id": proposal_id, "state": BINDING_PAYMENT_REQUIRED, "created_at": now, "updated_at": now}
    replaced = conn.execute(f"SELECT {_BINDING_COLS} FROM wallet_x402_bindings WHERE request_digest = ?", (request_digest,)).fetchone()
    guard, guard_values = binding_guard()
    cursor = conn.execute(
        f"INSERT INTO wallet_x402_bindings ({_BINDING_COLS}) VALUES ({', '.join('?' for _ in columns)})"
        f" ON CONFLICT(request_digest) DO UPDATE SET {', '.join(f'{column} = excluded.{column}' for column in columns if column != 'request_digest')}" + guard,
        (*(row[column] for column in columns), *guard_values),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute("INSERT INTO wallet_proposal_events (proposal_id, state, detail_json, created_at) VALUES (?, ?, ?, ?)",
                 (proposal_id, EVENT_REQUEST_CLAIMED, dumps({"request_digest": request_digest, "url": url}), now))
    if replaced is not None:
        old = _binding_row(replaced)
        conn.execute("INSERT INTO wallet_proposal_events (proposal_id, state, detail_json, created_at) VALUES (?, ?, ?, ?)",
                     (old["proposal_id"], EVENT_BINDING_REPLACED, dumps({"binding": old, "replaced_by": proposal_id}), now))
    return True


class _RequestClaim:
    """The fetch door's claim on the request it fetched, run on the offer's proposal by
    :func:`proposals.propose_transaction`: inside the transaction that mints a new one, or in one immediate transaction
    that re-reads the proposal the offer already has (proposed through another door, or fetched at another spelling).

    The request is bound to that proposal (or already is), so it is the request's one payment and the offer's. When
    another payment holds the request, a NEW proposal is rejected there with its receipt, never approvable and never
    seen unbound; an existing one is left as it is (another request's payment, or an offer proposed on its own). An
    existing proposal whose prepare was abandoned is ended there instead (:func:`_end_abandoned_on`), so a fresh one is
    minted. ``bound`` names the proposal the request was bound to; ``minted`` says whether this door minted it."""

    def __init__(self, request_digest: str, url: str, method: str, terms: dict[str, Any], *, source_context: dict[str, Any] | None) -> None:
        self.request_digest = request_digest
        self.url = url
        self.method = method
        self.terms = terms
        self.source_context = source_context
        self.bound = ""
        self.minted = False

    def __call__(self, conn: Any, proposal: proposals.TransactionProposal, minted: bool) -> None:
        if not minted and _end_abandoned_on(conn, proposal, source_context=self.source_context):
            return
        holder = conn.execute("SELECT proposal_id FROM wallet_x402_bindings WHERE request_digest = ?", (self.request_digest,)).fetchone()
        if (holder is not None and str(holder[0]) == proposal.proposal_id) or _claim_request(
                conn, request_digest=self.request_digest, url=self.url, method=self.method, proposal_id=proposal.proposal_id, terms=self.terms):
            self.bound, self.minted = proposal.proposal_id, minted
            return
        if minted:
            from core.wallet import lifecycle

            lifecycle.end_refused(proposal.proposal_id, proposals.STATE_REJECTED, fault_code="wallet_duplicate_payment", expected_state=proposals.STATE_PROPOSED,
                                  detail={"reason": "request_bound_to_another_payment", "bound_proposal_id": str(holder[0] if holder else "")}, conn=conn,
                                  reason="request_bound_to_another_payment")


def owns_its_request(proposal: proposals.TransactionProposal, *, conn: Any = None) -> bool:
    """False for a proposal a request was bound to (:data:`EVENT_REQUEST_CLAIMED`) that no request's binding names any
    more: such a proposal is paid only as a request's payment. A proposal no request was ever bound to (an offer
    proposed on its own, any other payment) claims no request here. On ``conn`` when given, so an approval door can
    read it inside its own claim."""
    if conn is None:
        with connection() as own:
            return owns_its_request(proposal, conn=own)
    pid = str(proposal.proposal_id)
    if conn.execute("SELECT 1 FROM wallet_proposal_events WHERE proposal_id = ? AND state = ? LIMIT 1", (pid, EVENT_REQUEST_CLAIMED)).fetchone() is None:
        return True
    return conn.execute("SELECT 1 FROM wallet_x402_bindings WHERE proposal_id = ? LIMIT 1", (pid,)).fetchone() is not None


def names_a_request(proposal: proposals.TransactionProposal, *, conn: Any = None) -> bool:
    """True when the proposal is not an x402 payment, or a request's binding names it. An x402 offer is paid only as
    the payment of a request this wallet fetched: one proposed on its own (the model's ``x402.propose``, the owner's
    ``/api/wallet/x402/propose``) waits, approvable by no door, until a fetch whose 402 is that offer binds its request
    to it; the request's binding is what keeps one request to one payment (:func:`binding_guard`), so a copy of a
    fetched offer, spelled or priced otherwise, is never a second payment for it. On ``conn`` when given."""
    if proposal.origin != proposals.ORIGIN_X402:
        return True
    if conn is None:
        with connection() as own:
            return names_a_request(proposal, conn=own)
    return conn.execute("SELECT 1 FROM wallet_x402_bindings WHERE proposal_id = ? LIMIT 1", (str(proposal.proposal_id),)).fetchone() is not None


def binding_guard() -> tuple[str, tuple[Any, ...]]:
    """The WHERE clause (and its parameters) of a binding upsert's ON CONFLICT update, which makes rebinding a request
    one compare-and-set: the stored binding takes another proposal only while its own proposal is in none of
    :data:`BINDING_HELD_STATES`, holds no reserved spend (principal or fee companion) and has no amount settled as moved
    (a payment that failed on chain settles its amount as 0). Otherwise the upsert changes no row, and its caller learns
    the request is already another payment's. Two callers racing the same request, each past its own first check, can
    therefore never both bind it, and a paid request stays closed even when its proposal row says otherwise."""
    from core.wallet import limits

    states = ", ".join("?" for _ in BINDING_HELD_STATES)
    return (
        f" WHERE NOT EXISTS (SELECT 1 FROM wallet_proposals p WHERE p.proposal_id = wallet_x402_bindings.proposal_id AND p.state IN ({states}))"
        " AND NOT EXISTS (SELECT 1 FROM wallet_spend_ledger l WHERE l.proposal_id IN (wallet_x402_bindings.proposal_id, wallet_x402_bindings.proposal_id || ?)"
        " AND l.state = ?)"
        " AND NOT EXISTS (SELECT 1 FROM wallet_spend_ledger l WHERE l.proposal_id = wallet_x402_bindings.proposal_id AND l.state = ? AND l.amount_minor > 0)",
        (*BINDING_HELD_STATES, limits._fee_hold_id(""), limits.RESERVATION_RESERVED, limits.RESERVATION_SETTLED),
    )


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


def end_abandoned_prepare(proposal: proposals.TransactionProposal | None, *, source_context: dict[str, Any] | None = None) -> None:
    """A request's proposal still preparing :data:`ABANDONED_PREPARE_SECONDS` after its last step was abandoned (its
    process stopped mid-prepare) is rejected here, with its receipt and nothing charged, and the request is free for the
    next fetch instead of closed for good. A younger one, or one whose age cannot be read, keeps the request (fail
    closed).

    Its state column is not proof that nothing left: in one transaction, before anything is written, the dispatch record
    must show no hold or settled spend (principal or fee), no transaction id, no payment effect unresolved or applied and
    no signing request open or consumed. Any of these means the payment was claimed and may have left (the row was
    rewound or restored): it is not ended, and the request is refused as a payment whose outcome is unknown. The
    expiry is a compare-and-set on the state it was read in, committed with its receipt, so a prepare still running
    cannot be overtaken: if it moved first, nothing changes; if this moves first, its next step finds it ended."""
    if proposal is None or proposal.state not in PREPARING_STATES or not _abandoned(proposal):
        return
    from core.wallet import limits

    with connection() as conn:
        limits._begin_immediate(conn)
        _end_abandoned_on(conn, proposal, source_context=source_context)


def _abandoned(proposal: proposals.TransactionProposal) -> bool:
    """Still preparing :data:`ABANDONED_PREPARE_SECONDS` after its last step; False when its age cannot be read."""
    if proposal.state not in PREPARING_STATES:
        return False
    try:
        since = datetime.fromisoformat(str(proposal.updated_at))
    except ValueError:
        return False
    return since.tzinfo is not None and datetime.now(timezone.utc) - since >= timedelta(seconds=ABANDONED_PREPARE_SECONDS)


def _end_abandoned_on(conn: Any, proposal: proposals.TransactionProposal, *, source_context: dict[str, Any] | None) -> bool:
    """:func:`end_abandoned_prepare` on the caller's connection, inside its immediate transaction: True when the
    abandoned prepare was ended here, False when it is not abandoned (or moved on first). Raises, writing nothing, when
    the dispatch record shows the payment may have left."""
    if not _abandoned(proposal):
        return False
    from core.wallet import lifecycle

    evidence = _dispatch_evidence(conn, proposal.proposal_id)
    if evidence:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "payment_outcome_unknown", "status": proposal.state,
                                                                                   "evidence": evidence}, source_context=source_context)
    return lifecycle.end_refused(proposal.proposal_id, proposals.STATE_REJECTED, fault_code="wallet_quote_expired", expected_state=proposal.state,
                                 detail={"reason": "prepare_abandoned"}, conn=conn, reason="prepare_abandoned") is not None


def _dispatch_evidence(conn: Any, proposal_id: str) -> str:
    """What, on the caller's connection, shows a payment was claimed and may have left: "" when nothing does."""
    from core.runtime_continuity import _UNRESOLVED_ACTIVE_STATES, _UNRESOLVED_TERMINAL_APPLIED
    from core.wallet import external_signing, limits, reconciliation

    pid = str(proposal_id)
    if conn.execute("SELECT 1 FROM wallet_proposals WHERE proposal_id = ? AND tx_signature <> ''", (pid,)).fetchone():
        return "transaction_id"
    if conn.execute("SELECT 1 FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) AND state IN (?, ?)",
                    (pid, limits._fee_hold_id(pid), limits.RESERVATION_RESERVED, limits.RESERVATION_SETTLED)).fetchone():
        return "spend_held_or_settled"
    effect_states = (*_UNRESOLVED_ACTIVE_STATES, _UNRESOLVED_TERMINAL_APPLIED)
    if conn.execute(f"SELECT 1 FROM runtime_unresolved_effects WHERE logical_effect_id = ? AND state IN ({', '.join('?' for _ in effect_states)})",
                    (reconciliation.logical_effect_id(pid), *effect_states)).fetchone():
        return "payment_effect"
    if conn.execute("SELECT 1 FROM wallet_signing_requests WHERE proposal_id = ? AND state IN (?, ?)",
                    (pid, external_signing.STATE_OPEN, external_signing.STATE_CONSUMED)).fetchone():
        return "signing_request"
    return ""


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


def fetch_paid_resource(url: str, *, wallet_id: str, source_context: dict[str, Any] | None = None, method: str = "GET", headers: dict[str, str] | None = None, timeout: float = 20.0, body: bytes = b"") -> X402Outcome:
    """Fetch; on a 402 offer park a capped proposal (or reuse the parked/paid one). Never pays on its own.

    This lane binds and replays a request by method and URL only, so a request body cannot ride it: one is refused
    typed before anything is sent, never silently dropped. A request with a body pays through the pay-kit lane
    (:func:`core.wallet.paykit_x402.fetch_paid`), whose binding carries the body.

    With the optional ``pay`` extra installed, a canonical Solana x402 offer (one naming the resource's fee payer) or
    an MPP Solana challenge that this request meets is handed to the pay-kit lane, from the 402 already received:
    nothing is sent twice."""
    custody.require_enabled(source_context=source_context)
    if body:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "request_body_needs_paykit_lane"}, source_context=source_context)
    clean_url = str(url or "").strip()
    if not _target_allowed(clean_url):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"reason": "x402_target_not_public", "host": urlsplit(clean_url).hostname or ""}, source_context=source_context)
    from core.wallet import paykit_x402

    paykit_already = paykit_x402.existing_outcome(paykit_x402.request_digest(str(method or "GET").upper(), clean_url, b""), source_context=source_context)
    if paykit_already is not None:
        return paykit_already
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
    if paykit_x402.claims_challenge(response_headers, body, wallet_id=wallet_id):
        # a canonical Solana offer (the resource's fee payer settles it): pay-kit builds it, this wallet approves and
        # signs it. Only with the optional `pay` extra; VOOL's own v1 Solana offers name no fee payer and stay here.
        return paykit_x402.park_challenge(answer, url=clean_url, method=method, headers=headers, body=b"", wallet_id=wallet_id, source_context=source_context)
    from core.wallet import paykit_mpp

    if paykit_mpp.claims_challenge(response_headers, wallet_id=wallet_id):
        # an MPP Solana challenge (WWW-Authenticate: Payment): the same pay-kit lane, its MPP terms
        return paykit_mpp.park_challenge(answer, url=clean_url, method=method, headers=headers, body=b"", wallet_id=wallet_id, source_context=source_context)
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
    # the offer's one proposal and this request's claim on it commit together: a new proposal is never seen unbound,
    # and one that lost the request to another payment is rejected in that same transaction, never approvable
    claim = _RequestClaim(digest, clean_url, method, _v1_terms(offer), source_context=source_context)
    proposal = propose_from_x402(offer, wallet_id=wallet_id, source_context=source_context, claim=claim)
    return _parked(proposal, claim, http_status=status, body=body, offer=offer, timeout=timeout, source_context=source_context)


def _parked(proposal: proposals.TransactionProposal, claim: _RequestClaim, *, http_status: int, timeout: float, source_context: dict[str, Any] | None,
            body: bytes = b"", offer: X402Request | None = None) -> X402Outcome:
    """What the fetch door answers once the offer's proposal and the request's claim on it are settled. A proposal this
    door minted is prepared here. One the offer already had is never prepared here (the door that proposed it does
    that): the request gets what that payment gives a re-fetch, its proposal while it waits, its one delivery once
    paid, and a typed refusal while it is still being prepared (nothing is approvable yet, and nothing new is parked).
    A request another payment holds gets what that payment gives it (:func:`_lost_request`)."""
    if claim.bound != proposal.proposal_id:
        return _lost_request(claim.request_digest, timeout=timeout, source_context=source_context)
    if claim.minted:
        proposal = prepare_bound(proposal.proposal_id, source_context=source_context)
    else:
        bound = _bound_outcome(binding_for_digest(claim.request_digest), timeout=timeout, source_context=source_context)
        if bound is not None:
            return bound
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "payment_still_preparing"}, source_context=source_context)
    binding = binding_for_digest(claim.request_digest) or {}
    return X402Outcome(status=OUTCOME_PAYMENT_REQUIRED, http_status=http_status, body=body, proposal_id=proposal.proposal_id, binding_id=binding.get("binding_id", ""), offer=offer)


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
    end_abandoned_prepare(proposal, source_context=source_context)
    refuse_while_dispatched(proposal, authority=AUTHORITY, source_context=source_context)
    return None


def refuse_while_dispatched(proposal: proposals.TransactionProposal, *, authority: str, source_context: dict[str, Any] | None) -> None:
    """The dispatch record decides, not the state column or a transaction id: while the payment's spend is still held
    as reserved, nothing has proven what became of it (it left, or may have), so its request stays closed whatever its
    state says. A refusal before sending releases the hold; a proven outcome settles it. A settled amount that moved
    is a payment made: when the proposal's row says it did not pay (a rewound or restored row), the record and the row
    disagree, and the request stays closed as a payment whose outcome is unknown."""
    from core.wallet import limits

    with connection() as conn:
        row = conn.execute("SELECT state, amount_minor FROM wallet_spend_ledger WHERE proposal_id = ?", (str(proposal.proposal_id),)).fetchone()
    held = row is not None and str(row[0]) == limits.RESERVATION_RESERVED
    paid = row is not None and str(row[0]) == limits.RESERVATION_SETTLED and int(row[1] or 0) > 0
    if held or paid:
        raise wallet_fault("wallet_duplicate_payment", authority=authority, context={"proposal_id": proposal.proposal_id, "reason": "payment_outcome_unknown", "status": proposal.state,
                                                                                 **({"evidence": "spend_settled"} if paid else {})}, source_context=source_context)


def _lost_request(digest: str, *, timeout: float, source_context: dict[str, Any] | None) -> X402Outcome:
    """Another payment holds this request (its 402 arrived while ours was in flight, on other terms): nothing approvable
    was minted here, and this caller gets what the request's own payment gives a re-fetch."""
    binding = binding_for_digest(digest)
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
    # the paid retry replays the CALLER's request: an offer that names another method than the one this request used
    # is refused, and an offer naming none is bound to the caller's own method (never a silent GET)
    declared_method = str((entry.raw or {}).get("method") or "").strip().upper()
    if declared_method and declared_method != str(method or "GET").upper():
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "offer_method_differs_from_request", "offered": declared_method[:12], "requested": str(method or "GET").upper()[:12]}, source_context=source_context)
    entry = replace(entry, resource_method=str(method or "GET").upper())
    digest = _request_digest(method, clean_url)
    facilitator = ""
    try:
        capability = facilitators.require_capability(entry.network, entry.scheme, source_context=source_context)
        facilitator = f"{capability.facilitator_id}@{capability.origin}"
    except Exception:
        facilitator = ""  # prepare() re-checks and refuses typed when missing
    claim = _RequestClaim(digest, clean_url, method, _v2_terms(entry, url=clean_url, facilitator=facilitator), source_context=source_context)
    proposal = x402_v2.propose_from_v2_offer(v2_offer, entry, wallet_id=wallet_id, source_context=source_context, claim=claim)
    return _parked(proposal, claim, http_status=PAYMENT_REQUIRED, offer=_v2_to_v1_view(v2_offer), timeout=20.0, source_context=source_context)


def _v2_to_v1_view(offer: Any) -> Any:
    return None  # the v2 offer rides the binding record; the outcome view stays summary-level


def _v2_terms(entry: Any, *, url: str, facilitator: str) -> dict[str, Any]:
    """A v2 binding's terms: the full offer evidence."""
    from core.wallet import chains, x402_v2

    asset = chains.asset_for(entry.network, entry.asset)
    return {
        "pay_to": entry.pay_to, "amount_minor": int(entry.amount_minor), "asset": asset.symbol, "network": entry.network, "version": 2, "offer_json": dumps(entry.to_dict()),
        "resource_origin": x402_v2.resource_origin_of(url), "resource_method": entry.resource_method, "facilitator_id": facilitator, "eip712_name": entry.eip712_name,
        "eip712_version": entry.eip712_version, "asset_transfer_method": entry.asset_transfer_method, "max_timeout_seconds": int(entry.max_timeout_seconds),
        "fee_asset": asset.symbol, "max_facilitator_fee_minor": 0, "max_network_fee_minor": 0,
        # EIP-3009 exact: the payer signs an authorization and the FACILITATOR settles on-chain and pays gas — sponsored
        # is the truthful record, and the payer's reserved fee is genuinely zero. A future payer-gas method records 0 here.
        "sponsored_gas": 1 if str(entry.asset_transfer_method or "eip3009") == "eip3009" else 0, "asset_address": asset.address,
    }


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
    if int(binding.get("version") or 1) in PAYKIT_BINDING_VERSIONS:
        # the pay-kit lane delivers exactly once, at approval: its signed payment is a one-shot instrument the
        # resource settles, so a retry here would replay it (or, worse, re-send it with another request). A retry
        # hands back the body that approval delivered, once, and sends nothing.
        from core.wallet import paykit_x402

        delivered = paykit_x402.delivered_body(proposal.proposal_id)
        if delivered is not None:
            return X402Outcome(status=OUTCOME_DELIVERED, http_status=int(binding.get("resource_status") or 200), body=delivered, proposal_id=proposal.proposal_id,
                               binding_id=str(binding.get("binding_id") or ""), tx_signature=proposal.tx_signature)
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "paykit_delivery_happens_at_approval"}, source_context=source_context)
    if not proposal.tx_signature or proposal.state not in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}:
        raise wallet_fault("wallet_approval_rejected", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "payment_not_confirmed", "status": proposal.state}, source_context=source_context)
    return _deliver(binding, proposal, timeout=timeout, source_context=source_context)
