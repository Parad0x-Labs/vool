"""UsePod top-up: the crypto side of the provider-payments boundary.

The UsePod owner hands over one exact payment requirement; this module turns it into ONE Crypto Pilot proposal under
a durable operation record keyed by provider and correlation id, and reports that operation. The record pins the
requirement field by field (provider, correlation id, network, asset, recipient, amount, expiry, resource) and is
consulted by the quote, the approval, the claim and the send: a still-valid wallet quote never pays an expired
provider request, a refreshed quote or a restart cannot extend the provider's window, and a replay of the same
requirement finds the original operation before any account is resolved, whatever account is selected now. The
sheet, the trusted approval, the signing session, the single send and the settlement are the transfer lane's own.
Nothing here fetches, retries or serialises the provider's request, keeps a second budget, or converts currencies;
what the sibling tasks own is refused typed, never substituted.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from core.wallet import amounts, capabilities, chains, environment, limits, proposals, svm_tokens, transfers
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.usepod"
ORIGIN_USEPOD = "usepod"
KEY_PREFIX = "usepod:"
MAX_CORRELATION_ID = 96
MAX_PROVIDER = 120
MAX_RESOURCE = 200
#: an operation reserved for minting is the minter's for this long; a minter that died leaves it for the next request
MINT_LEASE_SECONDS = 30.0

STATE_MINTING = "minting"
STATE_PROPOSED = "proposed"
STATE_EXPIRED = "expired"
#: derived from the transfer record's own state: local steps, transmitted-but-unproven sends and failed executions are
#: never paid; only a confirmed principal is (a bounded fee does not negate it)
STATE_SIGNING = "signing"
STATE_REVOKED = "revoked"
STATE_SENDING = "sending"
STATE_UNKNOWN = "unknown"
STATE_PENDING = "pending"
STATE_PAID = "paid"
STATE_FAILED = "failed"
STATE_RELEASED = "released"
#: the requirement digest's rule set: 1 lowercased every recipient; 2 uses the row family's canonical recipient
DIGEST_VERSION = 2

_COLS = ("operation_key, provider, correlation_id, authority, network, asset, pay_to, amount_minor, expires_at, resource, requirement_digest, "
         "proposal_id, wallet_id, state, mint_token, mint_lease_until, detail, created_at, updated_at, digest_version")
_COL_NAMES = tuple(name.strip() for name in _COLS.split(","))


def clock() -> float:
    """Read at call time, never bound at import: the gates must see the clock the process has now."""
    return time.time()


class RequirementError(ValueError):
    """The requirement is not a well-formed UsePod payment requirement (a shape defect, not a money decision)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TopUpRequirement:
    correlation_id: str
    provider: str
    network: str
    asset: str
    pay_to: str
    amount: str = ""
    amount_minor: Any = None
    expires_at: float = 0.0
    resource: str = ""
    payer_wallet: str = ""


def provider_of(resource: str, explicit: str = "") -> str:
    """The provider identity an operation is scoped to: the explicit provider, else the resource's origin."""
    clean = str(explicit or "").strip().lower()
    if clean:
        return clean[:MAX_PROVIDER]
    parts = urlsplit(str(resource or "").strip())
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}".lower()[:MAX_PROVIDER]
    return ""


def operation_key(provider: str, correlation_id: str) -> str:
    return f"{provider}|{correlation_id}"


def proposal_idempotency_key(key: str) -> str:
    """The proposal's idempotency key for one operation: provider-scoped, within the key length the proposal store keeps."""
    return KEY_PREFIX + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:40]


def parse_requirement(payload: Any) -> TopUpRequirement:
    """The exact fields the boundary accepts (delivery/USEPOD-BOUNDARY.md). Amounts are exact decimals or integer
    atomic units; a binary float is refused at the boundary (contract 3). The expiry is required, finite and positive:
    a requirement without an acceptance window, or with NaN or infinity for one, is not accepted as "never expires"."""
    body = dict(payload) if isinstance(payload, dict) else {}
    correlation = str(body.get("correlation_id") or "").strip()
    if not correlation or len(correlation) > MAX_CORRELATION_ID or any(c.isspace() for c in correlation) or "|" in correlation:
        raise RequirementError("correlation_id_required")
    network = str(body.get("network") or "").strip()
    if not network:
        raise RequirementError("network_required")
    asset = str(body.get("asset") or "").strip().upper()
    if not asset:
        raise RequirementError("asset_required")
    pay_to = str(body.get("pay_to") or body.get("payTo") or "").strip()
    if not pay_to:
        raise RequirementError("pay_to_required")
    raw_amount, raw_minor = body.get("amount"), body.get("amount_minor")
    if isinstance(raw_amount, float) or isinstance(raw_minor, float):
        raise RequirementError("amount_must_not_be_a_float")
    if isinstance(raw_amount, bool) or isinstance(raw_minor, bool):
        raise RequirementError("amount_required")
    amount = str(raw_amount).strip() if raw_amount not in (None, "") else ""
    if not amount and raw_minor in (None, ""):
        raise RequirementError("amount_required")
    raw_expiry = body.get("expires_at")
    if raw_expiry is None or (isinstance(raw_expiry, str) and not raw_expiry.strip()) or isinstance(raw_expiry, bool):
        raise RequirementError("expires_at_required")
    try:
        expires_at = float(raw_expiry)
    except (TypeError, ValueError):
        raise RequirementError("expires_at_not_a_number") from None
    if not math.isfinite(expires_at):
        raise RequirementError("expires_at_not_finite")
    if expires_at <= 0:
        raise RequirementError("expires_at_not_positive")
    resource = str(body.get("resource") or "").strip()[:MAX_RESOURCE]
    provider = provider_of(resource, str(body.get("provider") or ""))
    if not provider:
        raise RequirementError("provider_required")
    payer = str(body.get("payer_wallet") or "").strip()[:64]
    return TopUpRequirement(
        correlation_id=correlation, provider=provider, network=network, asset=asset, pay_to=pay_to, amount=amount, amount_minor=raw_minor,
        expires_at=expires_at, resource=resource, payer_wallet=payer,
    )


def canonical_recipient(spec: Any, pay_to: str) -> str | None:
    """The recipient's identity on this row's chain. EVM: the lowercase hex address (EIP-55 case is a checksum, not an
    identity). Solana: the exact Base58 string — case IS identity — accepted only when it decodes to 32 bytes. None when
    the value is not a key of the row's family."""
    value = str(pay_to or "").strip()
    if spec.is_evm:
        return value.lower() if chains.destination_matches_family(spec.network, value) else None
    if not chains.destination_matches_family(spec.network, value):
        return None
    try:
        from core.vool_wallet import b58decode

        return value if len(b58decode(value)) == 32 else None
    except Exception:
        return None


def _digest_of(requirement: TopUpRequirement, amount_minor: int, network: str, recipient: str) -> str:
    canonical = json.dumps({
        "provider": requirement.provider, "correlation_id": requirement.correlation_id, "network": network, "asset": requirement.asset,
        "pay_to": recipient, "amount_minor": int(amount_minor), "expires_at": repr(float(requirement.expires_at)), "resource": requirement.resource,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def requirement_digest(requirement: TopUpRequirement, amount_minor: int, network: str) -> str:
    """Version 2: the content a replay must repeat exactly, with the recipient in its chain's canonical identity."""
    spec = chains.resolve_network(network)
    recipient = canonical_recipient(spec, requirement.pay_to)
    return _digest_of(requirement, amount_minor, spec.network, recipient if recipient is not None else requirement.pay_to)


def legacy_requirement_digest(requirement: TopUpRequirement, amount_minor: int, network: str) -> str:
    """Version 1 (records written before correction 2): every recipient lowercased. Used only to certify such a record."""
    return _digest_of(requirement, amount_minor, network, requirement.pay_to.lower())


def _row(record: Any) -> dict[str, Any] | None:
    return None if record is None else dict(zip(_COL_NAMES, tuple(record), strict=True))


def _fetch(conn: Any, key: str) -> dict[str, Any] | None:
    return _row(conn.execute(f"SELECT {_COLS} FROM wallet_usepod_operations WHERE operation_key = ?", (key,)).fetchone())


def operation_for_proposal(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        return _row(conn.execute(f"SELECT {_COLS} FROM wallet_usepod_operations WHERE proposal_id = ?", (str(proposal_id),)).fetchone())


def _exact_minor(requirement: TopUpRequirement, native: Any, *, source_context: dict[str, Any] | None) -> int:
    minor: int | None = None
    if requirement.amount_minor not in (None, ""):
        try:
            minor = int(requirement.amount_minor)
        except (TypeError, ValueError):
            raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY, context={"limit": "amount", "reason": "amount_minor_not_an_integer"}, source_context=source_context) from None
    if requirement.amount:
        parsed = amounts.parse_human_amount(requirement.amount, decimals=native.decimals, symbol=native.symbol, source_context=source_context)
        if minor is not None and minor != parsed:
            raise wallet_fault("wallet_limit_exceeded", authority=AUTHORITY, context={"limit": "amount", "reason": "amount_and_amount_minor_disagree"}, source_context=source_context)
        minor = parsed
    if minor is None or minor <= 0:
        raise wallet_fault("wallet_amount_invalid", authority=AUTHORITY, context={"reason": "amount_must_be_positive"}, source_context=source_context)
    return minor


def validate_topup(requirement: TopUpRequirement, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """One requirement -> one operation -> one pilot proposal, prepared for the sheet.

    Refused typed, writing nothing: a row outside the active environment, a row that is not ready, a token instead of
    the row's native coin, a recipient whose shape is another family's, an expired requirement, an amount that is not
    an exact positive decimal within storage, no signing account on the row. A replay of the same operation returns
    the original result whatever account is selected now; the same id with other content, or a different named payer,
    is a typed refusal; a second request while the first is still minting is a typed refusal to retry."""
    return _mint_operation(requirement, accept_tokens=False, memo_label="UsePod top-up", source_context=source_context)


def validate_x402_payment(requirement: TopUpRequirement, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The accountless x402 payment for ONE UsePod request, under the same one-operation law as a top-up. The quoted asset
    may also be a token registered on the row (USDC on Solana): the lane then moves it with ``TransferChecked``, holds its
    principal in the token and its fee in the native coin, and counts it paid only when the confirmed transaction's own
    token balances show the principal moved. The payer is the wallet the payment authority named."""
    return _mint_operation(requirement, accept_tokens=True, memo_label="UsePod x402 payment", source_context=source_context)


def _mint_operation(requirement: TopUpRequirement, *, accept_tokens: bool, memo_label: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    spec = chains.resolve_network(requirement.network)
    environment.require_active(spec.network, source_context=source_context)
    if not capabilities.pilot_transfer_ready(spec):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "usepod_row_not_ready"}, source_context=source_context)
    native = chains.native_asset(spec.network)
    if requirement.asset in {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}:
        paid = native
    elif accept_tokens and svm_tokens.is_token_transfer(spec.network, requirement.asset):
        paid = svm_tokens.token_asset(spec.network, requirement.asset, source_context=source_context)
    else:
        reason = "x402_asset_not_registered_on_row" if accept_tokens else "usepod_asset_not_native"
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "asset": requirement.asset[:12], "reason": reason}, source_context=source_context)
    if transfers.destination_family(requirement.pay_to) != spec.family:
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={"network": spec.network, "reason": "recipient_shape_mismatch"}, source_context=source_context)
    recipient = canonical_recipient(spec, requirement.pay_to)
    if recipient is None:
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={"network": spec.network, "reason": "recipient_not_a_key_of_this_row"}, source_context=source_context)
    if requirement.expires_at <= clock():
        raise wallet_fault("wallet_quote_expired", authority=AUTHORITY, context={"reason": "usepod_requirement_expired", "expires_at": requirement.expires_at}, source_context=source_context)
    minor = _exact_minor(requirement, paid, source_context=source_context)
    digest = _digest_of(requirement, minor, spec.network, recipient)
    key = operation_key(requirement.provider, requirement.correlation_id)
    token = uuid.uuid4().hex
    now = clock()
    # F2: the operation record is reserved BEFORE any account is resolved; a replay finds it whatever is selected now
    with connection() as conn:
        limits._begin_immediate(conn)
        existing = _fetch(conn, key)
        if existing is None:
            conn.execute(
                "INSERT INTO wallet_usepod_operations (operation_key, provider, correlation_id, authority, network, asset, pay_to, amount_minor, expires_at, resource, "
                "requirement_digest, proposal_id, wallet_id, state, mint_token, mint_lease_until, detail, created_at, updated_at, digest_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, '', ?, ?, ?)",
                (key, requirement.provider, requirement.correlation_id, AUTHORITY, spec.network, paid.symbol, recipient, int(minor), float(requirement.expires_at),
                 requirement.resource, digest, STATE_MINTING, token, now + MINT_LEASE_SECONDS, utcnow(), utcnow(), DIGEST_VERSION),
            )
            reserved = True
        else:
            _certify(conn, existing, requirement, digest, minor, spec, recipient, source_context=source_context)
            reserved = _take_over_if_lawful(conn, existing, requirement, token=token, now=now, source_context=source_context)
    if not reserved:
        return _replay(existing, requirement, source_context=source_context)
    try:
        resolved = transfers.resolve_request(
            destination=recipient, asset=native.symbol, amount_minor=minor, network=spec.network, wallet_id=requirement.payer_wallet, source_context=source_context,
        )
        memo = f"{memo_label} {requirement.resource}".strip()[:proposals.MAX_MEMO]
        proposal = proposals.propose_transaction(
            wallet_id=resolved["wallet_id"], destination=resolved["destination"], amount_minor=int(resolved["amount_minor"]), asset=paid.symbol,
            origin=ORIGIN_USEPOD, memo=memo, idempotency_key=proposal_idempotency_key(key), source_context=source_context, network=spec.network,
        )
        if proposal.state == proposals.STATE_PROPOSED:
            from core.wallet import lifecycle

            proposal = lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    except BaseException:
        # the mint did not complete: the reservation goes, so the same requirement can be made again; no liability exists
        with connection() as conn:
            conn.execute("DELETE FROM wallet_usepod_operations WHERE operation_key = ? AND mint_token = ? AND state = ?", (key, token, STATE_MINTING))
        raise
    with connection() as conn:
        limits._begin_immediate(conn)
        bound = conn.execute(
            "UPDATE wallet_usepod_operations SET proposal_id = ?, wallet_id = ?, state = ?, mint_token = '', mint_lease_until = 0, updated_at = ? "
            "WHERE operation_key = ? AND mint_token = ? AND state = ?",
            (proposal.proposal_id, resolved["wallet_id"], STATE_PROPOSED, utcnow(), key, token, STATE_MINTING),
        ).rowcount
    if bound != 1:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "operation_taken_over_during_mint", "correlation_id": requirement.correlation_id}, source_context=source_context)
    record = operation_for_proposal(proposal.proposal_id) or {}
    return _result(record, proposal, duplicate=False, display_name=resolved["display_name"], amount_human=amounts.format_minor(minor, paid.decimals))


def _certify(conn: Any, existing: dict[str, Any], requirement: TopUpRequirement, digest: str, minor: int, spec: Any, recipient: str, *, source_context: dict[str, Any] | None) -> None:
    """On the reserving connection: the existing record must be THIS request's content. A version-2 record compares
    digests. A version-1 record (written before correction 2, recipient lowercased in its digest) is certified by its
    stored recipient in the row's canonical identity: equal → the record is upgraded in place (digest version 2); a
    recipient that differs, by case on Solana included, is changed content; a stored recipient that is not a key of
    the row's family cannot be certified and is refused with a recovery reason — never replayed as a new payment."""
    changed = wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={
        "reason": "same_operation_different_content", "correlation_id": requirement.correlation_id, "provider": requirement.provider, "proposal_id": existing["proposal_id"],
    }, source_context=source_context)
    if int(existing.get("digest_version") or 1) >= DIGEST_VERSION:
        if existing["requirement_digest"] != digest:
            raise changed
        return
    stored = canonical_recipient(spec, str(existing["pay_to"] or ""))
    if stored is None:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={
            "reason": "legacy_record_uncertified", "correlation_id": requirement.correlation_id, "provider": requirement.provider, "proposal_id": existing["proposal_id"],
            "recovery": "the record's stored recipient is not a key of its row; the operation cannot be replayed — the provider issues a new correlation id, the operator inspects the record",
        }, source_context=source_context)
    if stored != recipient or existing["requirement_digest"] != legacy_requirement_digest(requirement, minor, spec.network):
        raise changed
    conn.execute(
        "UPDATE wallet_usepod_operations SET requirement_digest = ?, digest_version = ?, pay_to = ?, updated_at = ? WHERE operation_key = ?",
        (digest, DIGEST_VERSION, stored, utcnow(), existing["operation_key"]),
    )
    existing["requirement_digest"], existing["digest_version"], existing["pay_to"] = digest, DIGEST_VERSION, stored


def _take_over_if_lawful(conn: Any, existing: dict[str, Any], requirement: TopUpRequirement, *, token: str, now: float, source_context: dict[str, Any] | None) -> bool:
    """On the reserving connection: whether THIS request may mint for an operation record that already exists.
    True only for a dead mint lease (the minter died before binding); everything else is answered by the record."""
    if str(existing["state"]) != STATE_MINTING:
        return False
    if float(existing["mint_lease_until"] or 0) > now:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "operation_in_progress", "correlation_id": requirement.correlation_id, "retry_after_seconds": int(MINT_LEASE_SECONDS)}, source_context=source_context)
    conn.execute(
        "UPDATE wallet_usepod_operations SET state = ?, mint_token = ?, mint_lease_until = ?, proposal_id = '', wallet_id = '', updated_at = ? WHERE operation_key = ?",
        (STATE_MINTING, token, now + MINT_LEASE_SECONDS, utcnow(), existing["operation_key"]),
    )
    return True


def _replay(existing: dict[str, Any], requirement: TopUpRequirement, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The same requirement again: the original operation's result, never a second proposal or liability."""
    proposal = proposals.get_proposal(str(existing["proposal_id"])) if existing["proposal_id"] else None
    if proposal is None:
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "operation_in_progress", "correlation_id": requirement.correlation_id}, source_context=source_context)
    if requirement.payer_wallet and requirement.payer_wallet != str(existing["wallet_id"]):
        raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={
            "reason": "operation_bound_to_another_payer", "correlation_id": requirement.correlation_id, "proposal_id": proposal.proposal_id, "payer_wallet": str(existing["wallet_id"]),
        }, source_context=source_context)
    spec = chains.resolve_network(proposal.network)
    decimals = chains.asset_for(spec.network, proposal.asset).decimals
    return _result(existing, proposal, duplicate=True, display_name=spec.display_name, amount_human=amounts.format_minor(int(proposal.amount_minor), decimals))


def _result(record: dict[str, Any], proposal: Any, *, duplicate: bool, display_name: str, amount_human: str) -> dict[str, Any]:
    return {
        "correlation_id": str(record.get("correlation_id") or ""), "provider": str(record.get("provider") or ""), "proposal_id": proposal.proposal_id, "state": proposal.state,
        "duplicate": duplicate, "wallet_id": proposal.wallet_id, "network": proposal.network, "display_name": display_name, "asset": proposal.asset,
        "amount_minor": str(int(proposal.amount_minor)), "amount_human": amount_human, "pay_to": proposal.destination, "expires_at": float(record.get("expires_at") or 0),
        "operation": public_operation(record),
    }


#: the transfer record's state → what the operation is, for the provider and for a future proof adapter
_OPERATION_STATE_OF_TRANSFER: dict[str, str] = {
    transfers.STATE_CLAIMED: STATE_SIGNING, transfers.STATE_SIGNED: STATE_SIGNING, transfers.STATE_SIGNED_REVOKED: STATE_REVOKED,
    transfers.STATE_DISPATCHING: STATE_SENDING, transfers.STATE_UNKNOWN: STATE_UNKNOWN, transfers.STATE_STOPPED_WAITING: STATE_UNKNOWN,
    transfers.STATE_PENDING: STATE_PENDING, transfers.STATE_CONFIRMED: STATE_PAID, transfers.STATE_FAILED_ON_CHAIN: STATE_FAILED,
    transfers.STATE_RELEASED: STATE_RELEASED, transfers.STATE_DISCARDED: STATE_RELEASED, transfers.STATE_CANCELLED: STATE_RELEASED,
}


def public_operation(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    state, transfer_state = _projection(record)
    return {
        "correlation_id": record["correlation_id"], "provider": record["provider"], "state": state, "transfer_state": transfer_state,
        "record_state": str(record["state"]), "paid": state == STATE_PAID, "proof_eligible": state == STATE_PAID,
        "proposal_id": record["proposal_id"], "wallet_id": record["wallet_id"],
        "network": record["network"], "asset": record["asset"], "pay_to": record["pay_to"], "amount_minor": str(int(record["amount_minor"])), "expires_at": float(record["expires_at"]),
        "resource": record["resource"], "requirement_digest": record["requirement_digest"], "digest_version": int(record.get("digest_version") or 1), "detail": record["detail"],
    }


def _projection(record: dict[str, Any]) -> tuple[str, str]:
    """(operation state, transfer state): once a transfer record exists its state is the payment truth — a local
    signature, revoked bytes, a transmitted-but-unproven send and a failed execution are never paid; the record's own
    state (minting, proposed, expired) answers only while no transfer record exists, and stays visible as
    ``record_state`` (a window that closed after the bytes were revoked is both revoked and expired)."""
    if record["proposal_id"]:
        row = transfers.get_transfer_by_id(str(record["proposal_id"]))
        if row is not None:
            transfer_state = str(row["state"])
            return _OPERATION_STATE_OF_TRANSFER.get(transfer_state, STATE_UNKNOWN), transfer_state
    return str(record["state"]), ""


def derived_state(record: dict[str, Any]) -> str:
    """The operation's state as the owner sees it: paid only for a confirmed principal transfer."""
    return _projection(record)[0]


# --- the gates the money owners consult ----------------------------------------------------------------------------

def binding_expired(proposal: Any, *, moment: float) -> bool:
    record = operation_for_proposal(proposal.proposal_id)
    return record is None or float(record["expires_at"]) <= float(moment)


def check_binding(conn: Any, proposal: Any, *, moment: float, quote_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pinned requirement against the proposal (and the quote when given), on the caller's connection, read only.
    Raises typed: no record (`provider_requirement_missing`), a field that differs (`provider_requirement_changed`),
    a closed window (`provider_requirement_expired`, code wallet_quote_expired)."""
    record = _row(conn.execute(f"SELECT {_COLS} FROM wallet_usepod_operations WHERE proposal_id = ?", (str(proposal.proposal_id),)).fetchone())
    if record is None:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "provider_requirement_missing"})
    spec = chains.resolve_network(proposal.network)
    pinned = canonical_recipient(spec, str(record["pay_to"] or ""))
    same_to = pinned is not None and pinned == canonical_recipient(spec, str(proposal.destination))
    same = same_to and str(record["network"]) == spec.network and str(record["asset"]).upper() == str(proposal.asset).upper() and int(record["amount_minor"]) == int(proposal.amount_minor)
    if same and quote_fields:
        quoted_to = canonical_recipient(spec, str(quote_fields.get("to_address") or ""))
        same = quoted_to == pinned and int(quote_fields.get("amount_minor") or -1) == int(record["amount_minor"]) and str(quote_fields.get("network") or "") == str(record["network"])
    if not same:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "provider_requirement_changed", "correlation_id": record["correlation_id"]})
    if float(record["expires_at"]) <= float(moment):
        raise wallet_fault("wallet_quote_expired", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "provider_requirement_expired", "correlation_id": record["correlation_id"], "expires_at": float(record["expires_at"])})
    return record


def require_binding(proposal: Any, *, moment: float, quote_fields: dict[str, Any] | None = None, expiry_code: str = "wallet_quote_expired", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The gate at the quote and at the approval: on a closed window the request is OVER — the proposal expires (a
    terminal refusal), its open quote is superseded, the operation is marked expired — and the caller gets the typed
    refusal under ``expiry_code``. No credential is asked, nothing is signed."""
    from core.wallet.errors import WalletFault

    try:
        with connection() as conn:
            return check_binding(conn, proposal, moment=moment, quote_fields=quote_fields)
    except WalletFault as exc:
        if exc.context.get("reason") == "provider_requirement_expired":
            mark_expired(proposal.proposal_id, detail="window_closed_before_payment", expire_proposal=True)
            raise wallet_fault(expiry_code, authority=AUTHORITY, context=dict(exc.context), source_context=source_context) from None
        raise


def mark_expired(proposal_id: str, *, detail: str, expire_proposal: bool) -> None:
    from core.wallet import quotes, receipts

    with connection() as conn:
        conn.execute("UPDATE wallet_usepod_operations SET state = ?, detail = ?, updated_at = ? WHERE proposal_id = ? AND state = ?",
                     (STATE_EXPIRED, str(detail)[:120], utcnow(), str(proposal_id), STATE_PROPOSED))
    if not expire_proposal:
        return
    proposal = proposals.get_proposal(proposal_id)
    if proposal is None or proposal.state not in transfers.OPEN_PROPOSAL_STATES:
        return
    open_quote = quotes.open_quote_for(proposal_id)
    if open_quote is not None:
        quotes.supersede_quote(open_quote["quote_id"], reason="provider_requirement_expired")
    moved = proposals.transition(proposal_id, proposals.STATE_EXPIRED, detail={"reason": "provider_requirement_expired"}, fault_code="wallet_quote_expired")
    if moved is not None:
        receipts.record_receipt(moved, state=proposals.STATE_EXPIRED, fault_code="wallet_quote_expired")
        from core.wallet import dna_fees

        # the collection planned to ride this payment goes back to accrued, under the ledger's fence
        dna_fees.release_companion_for_payment(proposal_id, reason="payment_window_closed")


def status_for(correlation_id: str, *, provider: str = "") -> dict[str, Any] | None:
    """The record for a correlation id (and provider, when several providers share the id): the operation, the
    proposal's state and, once claimed, the transfer view (never bytes). Deterministic: the operation record decides."""
    clean = str(correlation_id or "").strip()
    if not clean:
        return None
    wanted = provider_of("", provider)
    with connection() as conn:
        if wanted:
            rows = [_fetch(conn, operation_key(wanted, clean))]
        else:
            rows = [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM wallet_usepod_operations WHERE correlation_id = ? ORDER BY created_at ASC", (clean,)).fetchall()]
    rows = [r for r in rows if r]
    if not rows:
        return None
    if len(rows) > 1:
        return {"correlation_id": clean, "ambiguous": True, "providers": [r["provider"] for r in rows]}
    record = rows[0]
    proposal = proposals.get_proposal(str(record["proposal_id"])) if record["proposal_id"] else None
    return {
        "correlation_id": clean, "provider": record["provider"], "proposal_id": record["proposal_id"] or None, "proposal_state": proposal.state if proposal else record["state"],
        "network": record["network"], "amount_minor": str(int(record["amount_minor"])), "asset": record["asset"], "pay_to": record["pay_to"], "expires_at": float(record["expires_at"]),
        "operation": public_operation(record), "transfer": transfers.latest_receipt(str(record["proposal_id"])) if record["proposal_id"] else None,
    }


__all__ = [
    "AUTHORITY", "DIGEST_VERSION", "KEY_PREFIX", "MINT_LEASE_SECONDS", "ORIGIN_USEPOD", "RequirementError", "TopUpRequirement", "binding_expired", "canonical_recipient",
    "check_binding", "clock", "derived_state", "legacy_requirement_digest", "mark_expired", "operation_for_proposal", "operation_key", "parse_requirement",
    "proposal_idempotency_key", "provider_of", "public_operation", "require_binding", "requirement_digest", "status_for", "validate_topup",
    "validate_x402_payment",
]
