"""The one door an agent's payment takes when nobody is asked: allowance first, then the wallet's own lane.

Order, and why:

1. The payment is a proposal the wallet's own doors parked from a 402 a request really met (x402, pay-kit, the x402
   SDK), on the AGENT'S OWN wallet (``core.wallet.agent_wallets``). A direct send, a top-up, a fee collection or a
   proposal on one of the owner's wallets never rides: those are the owner's to approve.
2. Every fact the allowance judges is read from the wallet's own records -- the proposal and the request binding the
   parking door wrote -- never from the caller: the service is the origin the request was sent to, the recipient and
   the amount are the offer's, and the proposal must name that same recipient and amount.
3. ``allowances.authorize`` holds the amount against the agent's allowance and commits BEFORE any credential exists.
4. Only then is the machine credential taken and the ordinary Crypto Pilot approval run: the same quote, claim,
   wallet ceilings, freeze check, signing guard, one send and receipt as a payment the owner approves by hand.
5. A refusal before the wallet claimed the transfer gives the allowance's hold back. After the claim the hold follows
   what the chain shows (:func:`reconcile`): moved counts, failed counts nothing, unknown stays held.

When the allowance does not say yes the proposal stays parked exactly as it was. The owner may approve it themselves
(:func:`approve_as_owner`, their own wallet's credential); a first approved payment to a listed service pins that
service's recipient, which is how a service becomes payable without asking.
"""
from __future__ import annotations

import time
from typing import Any

from core.wallet import agent_wallets, allowances, custody, proposals
from core.wallet.security import wallet_fault
from core.wallet.store import connection

AUTHORITY = "core.wallet.agent_pay"

#: The origins a request's own 402 parks. Nothing else is a service payment.
SERVICE_ORIGINS = (proposals.ORIGIN_X402, proposals.ORIGIN_X402_PAYKIT, proposals.ORIGIN_MPP_PAYKIT, proposals.ORIGIN_X402_SDK)

REASON_NOT_AGENT_WALLET = "not_an_agent_wallet"
REASON_NOT_SERVICE_PAYMENT = "not_a_service_payment"
REASON_NO_REQUEST = "no_request_on_record"
REASON_RECORD_MISMATCH = "payment_differs_from_its_request"
REASON_NOT_AWAITING = "not_awaiting_approval"


def _fault(code: str, reason: str, *, source_context: dict[str, Any] | None, **context: Any) -> Exception:
    return wallet_fault(code, authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def _same_address(network: str, left: str, right: str) -> bool:
    from core.wallet import chains

    a, b = str(left or "").strip(), str(right or "").strip()
    return bool(a) and (a.lower() == b.lower() if chains.resolve_network(network).is_evm else a == b)


def _facts(proposal: proposals.TransactionProposal) -> tuple[dict[str, Any] | None, str]:
    """The payment as the wallet's own records state it: (facts, "") or (None, why it cannot ride)."""
    from core.wallet import x402

    if str(proposal.origin) not in SERVICE_ORIGINS:
        return None, REASON_NOT_SERVICE_PAYMENT
    binding = x402.binding_for_proposal(proposal.proposal_id)
    if not binding or not str(binding.get("request_digest") or "") or not str(binding.get("url") or ""):
        return None, REASON_NO_REQUEST
    origin = allowances.normalize_origin(str(binding["url"]))
    pay_to = str(binding.get("pay_to") or "")
    try:
        amount = int(binding.get("amount_minor"))
    except (TypeError, ValueError):
        return None, REASON_RECORD_MISMATCH
    if not origin or amount != int(proposal.amount_minor) or not _same_address(proposal.network, pay_to, proposal.destination):
        return None, REASON_RECORD_MISMATCH
    return {"origin": origin, "pay_to": str(proposal.destination), "amount_minor": amount, "request_digest": str(binding["request_digest"])}, ""


def _outcome(decision: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"decision": decision, "reason": reason, "paid": False, "transfer": None, "delivered": None, "notices": [], "paused": "", "retry_after_seconds": 0.0, **extra}


def _log_unridden(agent_id: str, proposal: proposals.TransactionProposal, reason: str, now: float) -> None:
    with connection() as conn:
        allowances._log(conn, agent_id=str(agent_id or "").strip().lower(), allowance_id="", proposal_id=proposal.proposal_id, origin="", pay_to=str(proposal.destination),
                        amount_minor=int(proposal.amount_minor) if 0 <= int(proposal.amount_minor) <= allowances._INT64_MAX else 0,
                        decision=allowances.DECISION_ASK_OWNER, reason=reason, now=now)


def _delivered(result: dict[str, Any]) -> bool | None:
    for lane in ("x402_sdk", "paykit"):
        block = result.get(lane)
        if isinstance(block, dict) and "delivered" in block:
            return bool(block["delivered"])
    return None


def reconcile(proposal_id: str, *, delivered: bool | None = None) -> str:
    """Bring the allowance's hold to what the wallet's transfer row shows now. Safe to call any number of times.
    Returns the transfer's state ("" when the wallet never claimed it)."""
    from core.wallet import transfers

    row = transfers.get_transfer_by_id(proposal_id)
    state = str((row or {}).get("state") or "")
    if state == transfers.STATE_CONFIRMED:
        allowances.settle(proposal_id, amount_moved=True)
        if delivered is not None:
            allowances.record_delivery(proposal_id, delivered=delivered)
    elif state == transfers.STATE_FAILED_ON_CHAIN:
        allowances.settle(proposal_id, amount_moved=False)
    elif state in (transfers.STATE_RELEASED, transfers.STATE_DISCARDED, transfers.STATE_CANCELLED):
        allowances.release(proposal_id)  # the wallet proved nothing left: its own hold was given back the same way
    return state


def pay(proposal_id: str, *, agent_id: str, now: float | None = None, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pay a parked service payment for ``agent_id`` if, and only if, its allowance says yes.

    ``agent_id`` is the identity the SERVER stamped on the run that met the 402 -- the caller is VOOL's own run
    coordinator, never a model's tool arguments. Returns ``decision`` (``pay`` / ``ask_owner`` / ``refuse``), ``reason``,
    ``paid`` (the chain showed the money moved), the transfer read model and whether the paid request delivered. A
    wallet refusal after the allowance said yes is raised as the wallet's own typed fault."""
    custody.require_enabled(source_context=source_context)
    moment = float(now if now is not None else time.time())
    proposal = proposals.get_proposal(str(proposal_id or ""))
    if proposal is None:
        raise _fault("wallet_not_found", "no_such_proposal", source_context=source_context)
    wallet = agent_wallets.agent_wallet(proposal.wallet_id)
    if wallet is None or not wallet["ready"]:
        _log_unridden(agent_id, proposal, REASON_NOT_AGENT_WALLET, moment)
        return _outcome(allowances.DECISION_ASK_OWNER, REASON_NOT_AGENT_WALLET)
    from core.wallet import transfers

    if transfers.get_transfer_by_id(proposal.proposal_id) is not None or proposal.state != proposals.STATE_PENDING_APPROVAL:
        # already claimed (by this door or the owner) or not approvable: nothing is decided twice
        state = reconcile(proposal.proposal_id)
        return _outcome(allowances.DECISION_REFUSE, REASON_NOT_AWAITING, paid=state == transfers.STATE_CONFIRMED, transfer_state=state)
    facts, why = _facts(proposal)
    if facts is None:
        _log_unridden(agent_id, proposal, why, moment)
        return _outcome(allowances.DECISION_ASK_OWNER, why)
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(proposal.proposal_id, source_context=source_context)  # every network read happens before any hold or credential
    with connection() as conn:
        allowances._begin_immediate(conn)
        decision = allowances.authorize(conn, agent_id=agent_id, proposal_id=proposal.proposal_id, wallet_id=proposal.wallet_id, network=proposal.network, asset=proposal.asset,
                                        now=moment, **facts)
    if not decision.pay:
        return _outcome(decision.decision, decision.reason, paused=decision.paused, retry_after_seconds=decision.retry_after_seconds, allowance_id=decision.allowance_id)
    try:
        credential = agent_wallets.signing_credential(proposal.wallet_id, source_context=source_context)
        try:
            result = lifecycle.default_lifecycle(source_context=source_context).approve_pilot_transfer(
                proposal.proposal_id, quote_id=str(quote["quote_id"]), quote_digest=str(quote["digest"]), approver=approval.PasswordApprover(credential))
        finally:
            del credential
    except BaseException:
        if transfers.get_transfer_by_id(proposal.proposal_id) is None:
            allowances.release(proposal.proposal_id)  # the wallet never claimed it: nothing was signed, nothing can have left
        else:
            reconcile(proposal.proposal_id)
        raise
    delivered = _delivered(result)
    state = reconcile(proposal.proposal_id, delivered=delivered)
    return _outcome(allowances.DECISION_PAY, decision.reason, paid=state == transfers.STATE_CONFIRMED, transfer=result.get("transfer"), delivered=delivered,
                    notices=list(decision.notices), allowance_id=decision.allowance_id, transfer_state=state)


def approve_as_owner(proposal_id: str, *, quote_id: str, quote_digest: str, credential: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The owner approves a payment parked on an agent's wallet themselves: their OWN wallet's credential is proven,
    then the ordinary approval runs. It rides no allowance and counts against none. When the chain shows it paid a
    service the agent's allowance lists without a recipient yet, that recipient is pinned: the owner has now approved
    paying it there."""
    custody.require_enabled(source_context=source_context)
    proposal = proposals.get_proposal(str(proposal_id or ""))
    if proposal is None:
        raise _fault("wallet_not_found", "no_such_proposal", source_context=source_context)
    wallet = agent_wallets.agent_wallet(proposal.wallet_id)
    if wallet is None:
        raise _fault("wallet_setup_state_invalid", REASON_NOT_AGENT_WALLET, source_context=source_context)
    custody.prove_owner(proposal.wallet_id, credential=credential, purpose="approve a payment from an agent's wallet", source_context=source_context)
    from core.wallet import approval, lifecycle, transfers

    machine = agent_wallets.signing_credential(proposal.wallet_id, source_context=source_context)
    try:
        result = lifecycle.default_lifecycle(source_context=source_context).approve_pilot_transfer(
            proposal.proposal_id, quote_id=str(quote_id or ""), quote_digest=str(quote_digest or ""), approver=approval.PasswordApprover(machine))
    finally:
        del machine
    pinned = False
    if str((result.get("transfer") or {}).get("state") or "") == transfers.STATE_CONFIRMED:
        facts, _why = _facts(proposal)
        if facts is not None:
            for view in allowances.list_allowances(agent_id=wallet["agent_id"]):
                if view.get("wallet_id") == proposal.wallet_id and not view.get("parent_id") and view.get("asset") == str(proposal.asset).upper():
                    pinned = allowances.pin_service(view["allowance_id"], facts["origin"], facts["pay_to"]) or pinned
    return {**result, "pinned_service": pinned}


#: The one agent every installation has: VOOL's own assistant, on a turn the owner wrote at this computer.
ASSISTANT_AGENT = "assistant"
MAX_BODY_CHARS = 4_000

_PLAIN = {
    allowances.REASON_WITHIN: "It was inside the spending allowance.",
    allowances.REASON_FROZEN: "Spending is frozen, so nothing was paid.",
    allowances.REASON_NO_ALLOWANCE: "There is no spending allowance for this, so it needs your approval.",
    allowances.REASON_UNREADABLE: "The spending allowance could not be read, so it needs your approval.",
    allowances.REASON_PAUSED: "The spending allowance is paused, so it needs your approval.",
    allowances.REASON_EXPIRED: "The spending allowance has ended, so it needs your approval.",
    allowances.REASON_NEW_SERVICE: "This service is not on the allowance's list, so it needs your approval.",
    allowances.REASON_FIRST_PAYMENT: "This is the first payment to this service, so it needs your approval.",
    allowances.REASON_RECIPIENT_CHANGED: "This service now asks to be paid at a different address than before, so it needs your approval.",
    allowances.REASON_OVER_PER_PAYMENT: "It costs more than the allowance permits for one payment, so it needs your approval.",
    allowances.REASON_OVER_ASK_THRESHOLD: "It costs more than the amount you asked to be consulted about, so it needs your approval.",
    allowances.REASON_PRICE_JUMP: "The price is more than three times what it last cost, so it needs your approval.",
    allowances.REASON_REPEAT_UNCONFIRMED: "An earlier payment for the same thing has not been confirmed as delivered, so it needs your approval.",
    allowances.REASON_TOO_SOON: "It is too soon after the last payment, so nothing was paid yet.",
    allowances.REASON_COUNT_USED: "The allowance's number of payments is used up, so it needs your approval.",
    allowances.REASON_WINDOW_USED: "The allowance for this period is used up, so it needs your approval.",
    allowances.REASON_TOTAL_USED: "The allowance's total is used up, so it needs your approval.",
    allowances.REASON_SERVICE_TOTAL_USED: "The allowance for this service is used up, so it needs your approval.",
    allowances.REASON_INVALID_REQUEST: "The payment request could not be checked, so it needs your approval.",
    REASON_NOT_AGENT_WALLET: "This payment is from your own wallet, so it needs your approval.",
    REASON_NOT_SERVICE_PAYMENT: "Only payments a service asks for can use an allowance, so this needs your approval.",
    REASON_NO_REQUEST: "The payment is not tied to a request, so it needs your approval.",
    REASON_RECORD_MISMATCH: "The payment does not match what the service asked for, so it needs your approval.",
    REASON_NOT_AWAITING: "This payment was already decided.",
}


def explain(reason: str) -> str:
    """The reason in the owner's words. An unknown reason never reads as permission."""
    return _PLAIN.get(str(reason or ""), "It could not be checked against the allowance, so it needs your approval.")


def agent_for_turn(source_context: dict[str, Any] | None) -> str:
    """The agent identity the SERVER gives this turn, or "" when no agent may pay unattended on it. Only the owner's
    own turn at this computer is the assistant's: a remote or phone turn, a turn that did not come from the owner's
    session, and a turn another agent wrote are nobody's, and pay nothing without the owner."""
    from core.request_trust import OWNER_LOCAL_KEY, turn_is_owner_authored, turn_is_remote

    stamped = isinstance(source_context, dict) and source_context.get(OWNER_LOCAL_KEY) is True
    # stricter than the tools' own rule: money needs the explicit owner stamp, never the absence of a remote one
    if not stamped or turn_is_remote(source_context) or not turn_is_owner_authored(source_context):
        return ""
    return ASSISTANT_AGENT


def fetch_for_turn(url: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Fetch a paid resource for the agent this turn belongs to, from that agent's own wallet, paying only what its
    allowance permits. None when this turn has no agent or that agent has no ready wallet whose lane meets the offer:
    the caller then does what it did before any allowance existed (parks the offer on the owner's wallet).

    Returns ``agent_id``, ``wallet_id``, ``status`` (the fetch outcome), ``proposal_id``, ``payment`` (:func:`pay`'s
    answer, None when nothing asked for payment) and ``body`` (the delivered resource, bytes, when one was)."""
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    agent = agent_for_turn(source_context)
    if not agent:
        return None
    for wallet in agent_wallets.list_agent_wallets():
        if wallet["agent_id"] != agent or not wallet["ready"]:
            continue
        try:
            outcome = x402.fetch_paid_resource(url, wallet_id=wallet["wallet_id"], source_context=source_context)
        except WalletFault:
            continue  # this wallet's lane does not meet what the resource asks: the next wallet, else the owner's
        answer: dict[str, Any] = {"agent_id": agent, "wallet_id": wallet["wallet_id"], "status": outcome.status, "http_status": outcome.http_status,
                                  "proposal_id": outcome.proposal_id, "payment": None, "body": outcome.body if outcome.status == x402.OUTCOME_DELIVERED else b""}
        if outcome.status != x402.OUTCOME_PAYMENT_REQUIRED or not outcome.proposal_id:
            return answer
        payment = pay(outcome.proposal_id, agent_id=agent, source_context=source_context)
        answer["payment"] = payment
        if payment["paid"] and payment["delivered"] is not False:
            try:
                answer["body"] = x402.retry_paid_resource(outcome.proposal_id, source_context=source_context).body
            except WalletFault:
                answer["body"] = b""  # paid, and the body was already handed out or never came: the receipt says which
        return answer
    return None


__all__ = ["ASSISTANT_AGENT", "agent_for_turn", "approve_as_owner", "explain", "fetch_for_turn", "pay", "reconcile"]
