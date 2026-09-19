"""The payment lifecycle engine: simulate -> limits -> approval -> reserve -> sign -> broadcast -> receipt.

Every RPC client is bound to ONE declared row at construction (an endpoint that row does not approve is
refused), and the endpoint's chain identity is proven before simulation and again before broadcast, so no
configuration can move a payment onto another chain. A proposal is always driven on its own row's endpoint. The state machine lives in
:mod:`core.wallet.proposals`; this module drives it and files every refusal as a typed fault.

Order of operations at approval, which the reservation tests pin: claim the proposal (CAS) ->
hold the amount against every ceiling atomically -> reserve the A6 logical effect -> open the
turn's effect -> ONLY THEN touch a signer (pocket) or hand out a signing request (external) ->
begin the effect attempt -> broadcast -> settle -> confirm -> resolve -> receipt. Any refusal or
failure before broadcast releases the hold and resolves the effect as safe to retry.
"""
from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import hmac
import json
import socket
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from core.wallet import approval as approval_module
from core.wallet import (
    chains,
    config,
    custody,
    evm,
    external_signing,
    facilitators,
    limits,
    outbound,
    proposals,
    receipts,
    reconciliation,
    redaction,
    signers,
)
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.lifecycle"
#: The effect-budget contract's two wallet operations (core.effect_budget on build/effect-budgets-p1-20260902,
#: tip a94a4a48 / implementation 4190b717): reserve BEFORE authorization, consume immediately BEFORE execution,
#: terminal exactly once. Both are opened at the claim, before any key is touched.
EFFECT_CLASS_SIGN = "wallet_sign"
EFFECT_CLASS_TRANSACTION = "wallet_transaction"
EFFECT_CLASS = EFFECT_CLASS_TRANSACTION
#: Refused approvals tolerated per proposal before it locks; the same order as a phone's PIN screen.
MAX_APPROVAL_ATTEMPTS = 5
ZERO_SIGNATURE = bytes(64)
#: outbound refusals raised AFTER the payment-bearing request left: these prove nothing
#: about non-settlement, so the submission is UNKNOWN (held, never blind-retried)
class RpcRejected(RuntimeError):
    """The RPC answered a WELL-FORMED JSON-RPC error to OUR request id: the node parsed the call and refused it.

    `validated` is True only when the response shape and id were verified; an error object with the wrong
    id, a non-integer code or no message is NOT validated and proves nothing about execution.
    """

    def __init__(self, method: str, code: int, message: str, *, validated: bool) -> None:
        super().__init__(f"rpc_error:{method}:{code}")
        self.method, self.code, self.message, self.validated = method, int(code), str(message), bool(validated)


class RpcMalformedResponse(RuntimeError):
    """The RPC answered, but not with a JSON-RPC response to our request: execution unknown."""

    def __init__(self, method: str, detail: str) -> None:
        super().__init__(f"rpc_malformed:{method}:{detail}")
        self.method, self.detail = method, detail


class RpcHttpError(RuntimeError):
    """An HTTP error status without a validated JSON-RPC error body: the node (or a proxy in front of
    it) answered after possibly executing the call. Execution unknown."""

    def __init__(self, method: str, status: int) -> None:
        super().__init__(f"rpc_http:{method}:{status}")
        self.method, self.status = method, int(status)


def _validated_rpc_error(raw: bytes, request_id: int, method: str) -> RpcRejected | None:
    """The JSON-RPC error object in `raw` when -- and only when -- it is a well-formed answer to OUR request."""
    try:
        payload = json.loads(raw or b"")
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("id") != request_id:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code, message = error.get("code"), error.get("message")
    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(message, str):
        return None
    return RpcRejected(method, code, message, validated=True)


#: JSON-RPC error codes that mean the node REFUSED `sendTransaction` before forwarding it (preflight is on:
#: `skipPreflight=False`): simulation/preflight failure, signature verification failure, invalid request or
#: params, parse error, unknown method. A code outside this set (node unhealthy, rate limited, unknown) is
#: an answer that proves nothing about execution.
_DEFINITIVE_REJECTION_CODES = frozenset({-32002, -32003, -32600, -32601, -32602, -32700})
#: OS errors that prove the request never left this machine.
_PRE_DISPATCH_ERRNOS = frozenset({errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EADDRNOTAVAIL})


@dataclass(frozen=True)
class BroadcastFate:
    """What a failed `sendTransaction` proved: `release` only on positive evidence of non-execution."""

    release: bool
    kind: str


def classify_broadcast_failure(exc: BaseException) -> BroadcastFate:
    """Release the hold ONLY on positive evidence that the transaction did not execute; otherwise UNKNOWN.

    Operator correction 2026-09-07: an HTTP response is not proof of non-execution. A node can accept and
    record the transaction and then a proxy answers 500, or the body is malformed, or a JSON-RPC error
    carries the wrong id or an unlisted code. Positive evidence is exactly two things: a connection that was
    never made (refused, unresolved host, no route), or a validated definitive rejection (a well-formed
    JSON-RPC error to OUR id whose code means the node did not forward). Everything else keeps the hold.
    """
    reason: BaseException = exc
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        inner = getattr(exc, "reason", None)
        reason = inner if isinstance(inner, BaseException) else exc
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError)) or (
        isinstance(reason, OSError) and not isinstance(reason, (TimeoutError, ConnectionResetError)) and getattr(reason, "errno", None) in _PRE_DISPATCH_ERRNOS
    ):
        return BroadcastFate(True, f"pre_dispatch:{type(reason).__name__}")
    if isinstance(exc, RpcRejected):
        if exc.validated and exc.code in _DEFINITIVE_REJECTION_CODES:
            return BroadcastFate(True, f"rejected:{exc.code}")
        return BroadcastFate(False, f"rpc_error:{exc.code}" + ("" if exc.validated else ":unvalidated"))
    if isinstance(exc, RpcHttpError):
        return BroadcastFate(False, f"http_{exc.status}")
    if isinstance(exc, urllib.error.HTTPError):
        return BroadcastFate(False, f"http_{exc.code}")
    if isinstance(exc, RpcMalformedResponse):
        return BroadcastFate(False, f"malformed_response:{exc.detail}")
    return BroadcastFate(False, type(reason).__name__)


_POST_DISPATCH_REFUSALS = frozenset({"payment_hop_redirected", "response_over_byte_limit", "redirect_refused_302", "redirect_refused_301", "redirect_refused_303", "redirect_refused_307", "redirect_refused_308", "too_many_redirects"})
_CONFIRM_BUDGET_SECONDS = 20.0
_CONFIRM_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class SimulationResult:
    ok: bool
    fee_minor: int
    reason: str
    units_consumed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "fee_minor": self.fee_minor, "reason": self.reason, "units_consumed": self.units_consumed}


class RpcClient:
    """Solana-dialect JSON-RPC over urllib, bound to one declared row: an endpoint the row does not approve
    (another row's origin, an excluded origin, loopback without the switch) is refused at construction."""

    def __init__(self, url: str, *, network: str, timeout: float = 20.0) -> None:
        clean_url = str(url or "").strip()
        try:
            spec = chains.resolve_network(network)
        except WalletFault:
            spec = None
        if spec is None or not config.rpc_url_allowed(clean_url) or not chains.rpc_origin_allowed(spec, clean_url):
            reason = "not_a_declared_network" if spec is None else "rpc_origin_not_approved_for_row"
            raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": str(network)[:80], "host": urlsplit(clean_url).hostname or "", "reason": reason})
        self.url = clean_url
        self.network = str(network)
        self.timeout = float(timeout)
        self._id = 0

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def _call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        request_id = self._id
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # an HTTP error status is an ANSWER, not proof of non-execution: only a well-formed JSON-RPC
            # error to our id inside it is a rejection the caller may act on
            raw = b""
            with contextlib.suppress(Exception):
                raw = exc.read()
            rejected = _validated_rpc_error(raw, request_id, method)
            if rejected is not None:
                raise rejected from None
            raise RpcHttpError(method, int(exc.code)) from None
        try:
            payload = json.loads(raw or b"")
        except ValueError:
            raise RpcMalformedResponse(method, "not_json") from None
        if not isinstance(payload, dict):
            raise RpcMalformedResponse(method, "not_an_object")
        if payload.get("id") != request_id:
            raise RpcMalformedResponse(method, "id_mismatch")
        if payload.get("error") is not None:
            rejected = _validated_rpc_error(raw, request_id, method)
            if rejected is not None:
                raise rejected
            raise RpcMalformedResponse(method, "error_shape")
        if "result" not in payload:
            raise RpcMalformedResponse(method, "no_result")
        return payload["result"]

    #: Every read that a broadcast depends on is taken at ONE commitment. The blockhash comes from
    #: the confirmed bank, so simulation and preflight must be evaluated against the confirmed bank
    #: too. Leaving preflight on its RPC default (finalized) asks a bank ~32 slots behind the tip
    #: about a blockhash it has not seen yet, and the node answers BlockhashNotFound -- a refusal
    #: that looks like a broken payment and is really a commitment mismatch. Proven on a local
    #: validator on 2026-09-03: identical bytes, finalized preflight -> -32002 BlockhashNotFound,
    #: confirmed preflight -> accepted.
    COMMITMENT = "confirmed"

    def latest_blockhash(self) -> str:
        result = self._call("getLatestBlockhash", [{"commitment": self.COMMITMENT}])
        return str(((result or {}).get("value") or {}).get("blockhash") or "")

    def simulate(self, raw_transaction: bytes) -> SimulationResult:
        result = self._call("simulateTransaction", [base64.b64encode(raw_transaction).decode("ascii"), {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": self.COMMITMENT}])
        value = (result or {}).get("value") or {}
        err = value.get("err")
        units = int(value.get("unitsConsumed") or 0)
        if err:
            return SimulationResult(False, 0, f"simulation_error:{next(iter(err)) if isinstance(err, dict) and err else 'unknown'}", units)
        return SimulationResult(True, 5_000, "ok", units)

    def broadcast(self, raw_transaction: bytes) -> str:
        return str(self._call("sendTransaction", [base64.b64encode(raw_transaction).decode("ascii"), {"encoding": "base64", "skipPreflight": False, "preflightCommitment": self.COMMITMENT}]) or "")

    def confirm(self, signature: str) -> bool:
        result = self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        statuses = (result or {}).get("value") or []
        status = statuses[0] if statuses else None
        return bool(status) and not status.get("err") and str(status.get("confirmationStatus") or "") in {"confirmed", "finalized"}

    def balance(self, pubkey: str) -> int:
        result = self._call("getBalance", [pubkey, {"commitment": "confirmed"}])
        return int(((result or {}).get("value")) or 0)


def _build_message(proposal: proposals.TransactionProposal, payer_pubkey: str, blockhash: str) -> Any:
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    if proposal.asset != "SOL":
        from core.wallet import svm_tokens

        if str(proposal.origin) in (proposals.ORIGIN_USEPOD, proposals.ORIGIN_DNA_FEE) and svm_tokens.is_token_transfer(proposal.network, proposal.asset):
            # a UsePod x402 token payment, or a DNA fee collection to the treasury: one TransferChecked between the two associated token accounts
            return svm_tokens.build_transfer_message(
                payer=payer_pubkey, recipient_owner=proposal.destination, asset=svm_tokens.token_asset(proposal.network, proposal.asset),
                amount_minor=int(proposal.amount_minor), blockhash=blockhash,
            )
        raise wallet_fault("wallet_simulation_failed", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "asset": proposal.asset, "reason": "asset_not_supported_in_p1"})
    payer = Pubkey.from_string(payer_pubkey)
    instruction = transfer(TransferParams(from_pubkey=payer, to_pubkey=Pubkey.from_string(proposal.destination), lamports=int(proposal.amount_minor)))
    return Message.new_with_blockhash([instruction], payer, Hash.from_string(blockhash))


def _refuse_unpayable_token(rpc: Any, spec: chains.ChainIdentity, proposal: proposals.TransactionProposal, payer: str, *, source_context: dict[str, Any] | None) -> None:
    """A UsePod token payment's own chain facts refuse typed before a simulation reports them as an error: the mint, the
    recipient's token account, the payer's token balance, and a native balance carrying this message's fee."""
    from core.wallet import svm_tokens

    message = _build_message(proposal, payer, rpc.latest_blockhash())
    answer = rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode("ascii"), {"commitment": "confirmed"}])
    fee = (answer or {}).get("value") if isinstance(answer, dict) else None
    if fee is None:
        raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "reason": "fee_unknown_for_message"}, source_context=source_context)
    svm_tokens.require_payable(
        rpc, network=spec.network, asset=proposal.asset, payer=payer, recipient_owner=proposal.destination, amount_minor=int(proposal.amount_minor),
        fee_max_minor=int(fee), source_context=source_context,
    )


def _serialize(message: Any, signature: bytes) -> bytes:
    from solders.signature import Signature
    from solders.transaction import Transaction

    return bytes(Transaction.populate(message, [Signature.from_bytes(bytes(signature))]))


def _serialize_message_and_signature(message_bytes: bytes, signature: bytes) -> bytes:
    from solders.message import Message

    return _serialize(Message.from_bytes(bytes(message_bytes)), signature)


class PaymentLifecycle:
    def __init__(self, *, rpc: RpcClient, source_context: dict[str, Any] | None = None) -> None:
        self.rpc = rpc
        self.source_context = source_context if isinstance(source_context, dict) else None

    # -- helpers ---------------------------------------------------------------------------------
    def _expire_open_request_or_refuse(self, proposal: proposals.TransactionProposal, record: dict[str, Any], *, evidence: str) -> None:
        """The ONE submit-door expiry transition, CAS-gated exactly like the reaper and the
        reject route (every ``expire_signing_request`` caller follows this law).

        A WON CAS (open -> expired by THIS call) proves no VOOL-mediated dispatch happened:
        the request was never consumed, and the app's only dispatch door CAS-consumes the
        request before any payment material leaves — so the release, the ``applied=False``
        resolution and the EXPIRED transition are ours to make. A LOST CAS means another
        actor owns the request's fate: CONSUMED is a submission in flight (observed silence
        proves nothing — no release, no guessed non-execution, no state overwrite), and the
        refusal says so; EXPIRED means the work is already done and the refusal reports it.

        Scope: this governs VOOL-MEDIATED submission only. It does not and cannot revoke a
        signature the user's external wallet may hold independently of this request."""
        if not external_signing.expire_signing_request(record["request_id"]):
            latest = external_signing.get_signing_request(record["request_id"]) or {}
            if latest.get("state") == external_signing.STATE_CONSUMED:
                raise self._fault("wallet_duplicate_payment", proposal, reason="signing_request_in_flight", status=proposal.state)
            raise self._fault("wallet_approval_rejected", proposal, reason="signing_request_expired", status=proposal.state)
        limits.release_spend(proposal.proposal_id)
        reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=evidence, source="mechanical")
        proposals.transition(proposal.proposal_id, proposals.STATE_EXPIRED, detail={"reason": "signing_request_expired"}, fault_code="wallet_approval_rejected")
        raise self._fault("wallet_approval_rejected", proposal, reason="signing_request_expired")

    def _load(self, proposal_id: str) -> proposals.TransactionProposal:
        proposal = proposals.get_proposal(proposal_id)
        if proposal is None:
            raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"proposal_id": str(proposal_id)}, source_context=self.source_context)
        return proposal

    def _require_not_frozen(self, proposal: proposals.TransactionProposal, *, door: str) -> None:
        """The panic freeze is a brake on every door that can release a spend-authorising
        signature or a broadcast — not only on new claims. Checked immediately before the
        effect; the hold and the signing request stay exactly as they were."""
        if limits.is_frozen():
            raise self._fault("wallet_limit_exceeded", proposal, limit="frozen", reason=f"panic_freeze_blocks_{door}")

    def reap_stale_signing_requests(self) -> int:
        """Apply the signing-request TTL to requests a crash or restart left open: expire
        each, release its hold and terminally mark its proposal. Idempotent."""
        from core.wallet.store import connection

        with connection() as conn:
            rows = conn.execute("SELECT request_id FROM wallet_signing_requests WHERE state = ?", (external_signing.STATE_OPEN,)).fetchall()
        reaped = 0
        for (request_id,) in rows:
            record = external_signing.get_signing_request(request_id)
            if record is None or not external_signing.is_expired(record):
                continue
            # the CAS win IS the no-dispatch proof: we transitioned open -> expired
            # ourselves, so no submission ever consumed this request (a consumed request
            # is a signature in flight — observed silence proves nothing, so no release).
            if not external_signing.expire_signing_request(request_id):
                continue
            proposal = proposals.get_proposal(record["proposal_id"])
            if proposal is not None and proposal.state == proposals.STATE_AWAITING_SIGNATURE and not proposal.tx_signature:
                limits.release_spend(proposal.proposal_id)
                reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence="signing request expired (reaper)", source="mechanical")
                proposals.transition(proposal.proposal_id, proposals.STATE_EXPIRED, detail={"reason": "signing_request_expired"}, fault_code="wallet_approval_rejected")
                receipts.record_receipt(proposal, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected")
            reaped += 1
        return reaped

    def _fault(self, code: str, proposal: proposals.TransactionProposal, **context: Any) -> WalletFault:
        return wallet_fault(code, authority=AUTHORITY, context={"proposal_id": proposal.proposal_id, "wallet_id": proposal.wallet_id, "network": proposal.network, **context}, source_context=self.source_context)

    # -- the row's endpoint ------------------------------------------------------------------------------
    def _rpc_for(self, network: str) -> RpcClient:
        """The Solana client for this row: the operator's declared endpoint for the row when there is one, else the
        engine's own client when it serves the row, else a client for the row's declared endpoint. Never another
        row's endpoint."""
        spec = chains.resolve_network(network)
        if config.network_rpc_override(spec.network):
            # the per-row declaration (VOOL_WALLET_RPC_URLS) is the authority for every read and send on the row;
            # the engine's default client was built for the legacy devnet setting and must not outrank it
            return RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
        with contextlib.suppress(WalletFault):
            if chains.resolve_network(self.rpc.network).network == spec.network:
                return self.rpc
        return RpcClient(chains.network_rpc_url(spec.network), network=spec.network)

    @staticmethod
    def _require_svm_identity(rpc: RpcClient, spec: chains.ChainIdentity) -> None:
        """One bounded getGenesisHash read, cached per endpoint: a proof of one endpoint never vouches for another."""
        chains.verify_chain_identity(spec, lambda method: rpc._call(method, []), scope=chains.endpoint_scope("lifecycle", rpc.url))

    def _prove_svm_endpoint(self, proposal: proposals.TransactionProposal, rpc: RpcClient, spec: chains.ChainIdentity) -> str:
        """"" when the endpoint proved the row's genesis; "rpc_failure:<kind>" when it could not be read (nothing
        learned, nothing sent). An endpoint that answers ANOTHER chain's genesis fails the proposal, typed."""
        try:
            self._require_svm_identity(rpc, spec)
        except WalletFault as exc:
            reason = str(exc.context.get("reason") or exc.code)
            if exc.code == "wallet_chain_identity_mismatch" and reason.startswith("identity_probe_failed"):
                return f"rpc_failure:{reason.partition(':')[2] or 'unknown'}"
            proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": reason}, fault_code=exc.code)
            raise
        return ""

    def _prove_before_claim(self, proposal: proposals.TransactionProposal) -> RpcClient:
        spec = chains.resolve_network(proposal.network)
        rpc = self._rpc_for(spec.network)
        failure = self._prove_svm_endpoint(proposal, rpc, spec)
        if failure:
            raise self._fault("wallet_broadcast_failed", proposal, reason=f"endpoint_unreadable_before_claim:{failure}", status=proposal.state)
        return rpc

    def _require_signable_row(self, proposal: proposals.TransactionProposal) -> chains.ChainIdentity:
        """No claim, signature or submission for a row outside the active network environment (an approval
        never crosses a switch: an unsent request needs a fresh preview), nor for a Crypto Pilot row whose
        transfer lane has not landed. Typed, before any key, hold or socket."""
        from core.wallet import capabilities, environment, transfers

        spec = environment.require_active(proposal.network, source_context=self.source_context)
        if transfers.is_pilot_transfer(proposal) or custody.seal_policy(proposal.wallet_id) == custody.PILOT_SEAL_POLICY:
            # a Crypto Pilot transfer moves only through the pilot lane (quote, approval sheet, one dispatch), whatever
            # the row's readiness: on a ready row the legacy doors name the sheet, elsewhere they keep the lane stop
            reason = capabilities.REASON_PILOT_TRANSFER_NEEDS_QUOTE_APPROVAL if capabilities.pilot_transfer_ready(spec) else capabilities.REASON_PILOT_LANE_INCOMPLETE
            raise self._fault("wallet_network_disabled", proposal, reason=reason)
        if not capabilities.transfer_lane_ready(spec):
            raise self._fault("wallet_network_disabled", proposal, reason=capabilities.REASON_PILOT_LANE_INCOMPLETE)
        return spec

    def _host_for(self, network: str) -> str:
        try:
            return self._rpc_for(network).host
        except Exception:
            return ""

    def _base(self, proposal: proposals.TransactionProposal) -> dict[str, Any]:
        return {"proposal_id": proposal.proposal_id, "wallet_id": proposal.wallet_id, "network": proposal.network, "asset": proposal.asset, "amount_minor": proposal.amount_minor, "destination": proposal.destination, "origin": proposal.origin, "host": self._host_for(proposal.network)}

    def _message_for(self, proposal: proposals.TransactionProposal, payer_pubkey: str, *, rpc: RpcClient | None = None) -> Any:
        return _build_message(proposal, payer_pubkey, (rpc or self._rpc_for(proposal.network)).latest_blockhash())

    # -- stage 1: prepare -------------------------------------------------------------------------
    def prepare(self, proposal_id: str) -> proposals.TransactionProposal:
        """proposed -> simulated -> limits_checked -> pending_approval (or a typed terminal refusal)."""
        proposal = self._load(proposal_id)
        if proposal.state == proposals.STATE_PENDING_APPROVAL:
            return proposal
        if proposal.state != proposals.STATE_PROPOSED:
            raise self._fault("wallet_duplicate_payment", proposal, reason="already_prepared", status=proposal.state)
        profile = custody.require_wallet(proposal.wallet_id, source_context=self.source_context)
        custody.require_network(proposal.network, source_context=self.source_context)
        from core.wallet import environment

        spec = environment.require_active(proposal.network, source_context=self.source_context)
        from core.wallet import transfers

        if spec.is_evm and transfers.is_pilot_transfer(proposal) and custody.seal_policy(proposal.wallet_id) == custody.PILOT_SEAL_POLICY:
            # a native-coin transfer from a Crypto Pilot wallet: the chain's identity, the stack and the asset are
            # proven here; the exact fee is the quote's, minted on the approval sheet. Nothing is signed here.
            simulation = self._prepare_evm_pilot(spec, proposal)
        elif spec.is_evm:
            # the EVM family has no local transaction simulation: the "simulation" is the
            # chain-identity proof plus the asset/scheme/facilitator checks, each a bounded
            # read or a pure registry check. Nothing is signed here.
            simulation = self._simulate_evm(spec, proposal)
        else:
            # the row's own endpoint proves its genesis before any read a payment depends on (blockhash, simulation)
            try:
                rpc = self._rpc_for(spec.network)
            except WalletFault as exc:
                proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": str(exc.context.get("reason") or exc.code)}, fault_code=exc.code)
                raise
            failure = self._prove_svm_endpoint(proposal, rpc, spec)
            if failure:
                simulation = SimulationResult(False, 0, failure)
            else:
                from core.wallet import svm_tokens

                if str(proposal.origin) in (proposals.ORIGIN_USEPOD, proposals.ORIGIN_DNA_FEE) and svm_tokens.is_token_transfer(spec.network, proposal.asset):
                    try:
                        _refuse_unpayable_token(rpc, spec, proposal, profile.public_key, source_context=self.source_context)
                    except WalletFault as exc:
                        proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": str(exc.context.get("reason") or exc.code)}, fault_code=exc.code)
                        raise
                try:
                    simulation = rpc.simulate(_serialize(self._message_for(proposal, profile.public_key, rpc=rpc), ZERO_SIGNATURE))
                except WalletFault:
                    proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, fault_code="wallet_simulation_failed")
                    raise
                except Exception as exc:
                    simulation = SimulationResult(False, 0, f"rpc_failure:{type(exc).__name__}")
        if not simulation.ok:
            fault_code = "wallet_dependency_unavailable" if str(simulation.reason).startswith("dependency:") else "wallet_simulation_failed"
            proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail=simulation.to_dict(), simulation=simulation.to_dict(), fault_code=fault_code)
            raise self._fault(fault_code, proposal, reason=simulation.reason)
        proposals.transition(proposal.proposal_id, proposals.STATE_SIMULATED, detail=simulation.to_dict(), simulation=simulation.to_dict())
        binding = v2_binding_for(proposal.proposal_id)
        fee_minor = _reserved_fee_for(binding) if binding else 0
        verdict = limits.check_limits(proposal.wallet_id, proposal.asset, proposal.amount_minor, proposal.destination, fee_minor=fee_minor, chain=spec.network)
        if not verdict.ok:
            proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"limit": verdict.limit, "reason": verdict.reason}, fault_code="wallet_limit_exceeded")
            raise self._fault("wallet_limit_exceeded", proposal, limit=verdict.limit, reason=verdict.reason, amount_minor=proposal.amount_minor, asset=proposal.asset)
        proposals.transition(proposal.proposal_id, proposals.STATE_LIMITS_CHECKED, detail={"reason": verdict.reason})
        prepared = proposals.transition(proposal.proposal_id, proposals.STATE_PENDING_APPROVAL, detail={"awaiting": "owner_approval"})
        return prepared or self._load(proposal_id)

    # -- stage 2: claim (approval -> hold -> effect) ------------------------------------------------
    def _claim(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, *, method: str, payer_message: Any = None, message_digest: str = "", fee_minor: int = 0, chain: str = "") -> tuple[proposals.TransactionProposal, Any]:
        """CAS the proposal to approved, hold the amount PLUS every non-sponsored maximum
        fee, reserve the effect, open the turn effect. Nothing signed yet."""
        # The signature is the durable evidence that this proposal ALREADY reached the chain, and it
        # outlives the state column: a restored, rewound or corrupted row can say pending_approval
        # again, and the door and the CAS both read that column. This check reads the broadcast
        # itself, so one proposal is at most one broadcast even when its state is lying. It sits in
        # _claim because that is the single choke point both the pocket and the external-signer lanes
        # pass through, before any key is touched.
        if proposal.tx_signature:
            raise self._fault("wallet_duplicate_payment", proposal, reason="already_broadcast", status=proposal.state)
        self._require_signable_row(proposal)
        claimed = proposals.transition(proposal.proposal_id, proposals.STATE_APPROVED, detail={"method": method}, expected_state=proposals.STATE_PENDING_APPROVAL)
        if claimed is None:
            raise self._fault("wallet_duplicate_payment", proposal, reason="already_claimed_by_another_approval")
        proposal = claimed
        verdict = limits.reserve_spend(wallet_id=proposal.wallet_id, asset=proposal.asset, amount_minor=proposal.amount_minor, destination=proposal.destination, proposal_id=proposal.proposal_id, fee_minor=fee_minor, chain=str(chain or spec_network(proposal.network)))
        if not verdict.ok:
            proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"limit": verdict.limit, "reason": verdict.reason}, fault_code="wallet_limit_exceeded")
            raise self._fault("wallet_limit_exceeded", proposal, limit=verdict.limit, reason=verdict.reason, amount_minor=proposal.amount_minor, asset=proposal.asset)
        if not message_digest:
            message_digest = hashlib.sha256(bytes(payer_message or b"")).hexdigest()
        reservation = reconciliation.reserve_payment_effect(proposal, message_digest=message_digest, source_context=self.source_context)
        if str(reservation.get("outcome") or "") != "reserved":
            limits.release_spend(proposal.proposal_id)
            proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"reason": "effect_in_flight"}, fault_code="wallet_duplicate_payment")
            raise self._fault("wallet_duplicate_payment", proposal, reason="effect_in_flight")
        try:
            sign_effect = self._open_effect(self._base(proposal), effect_class=EFFECT_CLASS_SIGN)
            tx_effect = self._open_effect(self._base(proposal), effect_class=EFFECT_CLASS_TRANSACTION)
        except Exception as exc:
            limits.release_spend(proposal.proposal_id)
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence="effect gateway refused before signing", source="mechanical")
            proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"reason": "effect_gateway_refused"}, fault_code="wallet_limit_exceeded")
            raise self._fault("wallet_limit_exceeded", proposal, reason=f"effect_gateway_refused:{type(exc).__name__}") from exc
        return proposal, {"sign": sign_effect, "transaction": tx_effect}

    def _abort_before_broadcast(self, proposal: proposals.TransactionProposal, effects: Any, *, state: str, fault_code: str, reason: str) -> None:
        limits.release_spend(proposal.proposal_id)
        reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=reason, source="mechanical")
        for effect in (effects or {}).values():
            self._cancel_effect(effect, reason=reason)
        proposals.transition(proposal.proposal_id, state, detail={"reason": reason}, fault_code=fault_code)
        receipts.record_receipt(proposal, state=state, fault_code=fault_code)

    # -- stage 3a: pocket approval -> sign in-process -> broadcast -------------------------------------
    def approve_and_execute(self, proposal_id: str, *, approver: approval_module.Approver) -> receipts.WalletReceipt:
        """pending_approval -> approved -> signed -> broadcast -> confirmed, with exactly one winner per proposal."""
        proposal = self._load(proposal_id)
        if proposal.state != proposals.STATE_PENDING_APPROVAL:
            raise self._fault("wallet_duplicate_payment", proposal, reason="not_awaiting_approval", status=proposal.state)
        self._require_signable_row(proposal)
        profile = custody.require_wallet(proposal.wallet_id, source_context=self.source_context)
        recipient = dict(getattr(proposal, "recipient", {}) or {})
        if recipient.get("resolution") == "contact":
            # a destination resolved from Contacts is approved only while Contacts still holds exactly that destination
            from core.contacts import verify_snapshot

            check = verify_snapshot(recipient)
            if not check.ok:
                raise self._fault("wallet_recipient_refused", proposal, reason=f"contact_{check.status}")
        # the endpoint is proven before the owner is asked: a lying endpoint never costs a PIN attempt
        rpc = self._prove_before_claim(proposal) if chains.resolve_network(proposal.network).is_svm else None
        challenge = self._challenge_for(proposal, profile)
        decision = approver.approve(challenge)
        accepted = approval_module.decision_binds(decision, challenge)
        device_signer = None
        if accepted and profile.mode == custody.MODE_POCKET_SEALED:
            method = getattr(profile, "approval_method", custody.APPROVAL_PIN)
            if method == custody.APPROVAL_DEVICE:
                # the owner's method is the device: only a device decision counts, and the prompt happens NOW,
                # before anything is claimed -- a cancelled prompt is a counted refusal, the proposal stays pending
                accepted = decision.method == approval_module.METHOD_DEVICE
                if accepted:
                    try:
                        device_signer = signers.signer_for(profile, proposal_id=proposal.proposal_id)
                    except WalletFault as exc:
                        attempts = proposals.record_approval_refusal(proposal.proposal_id, method=decision.method, reason=exc.code)
                        raise self._fault(exc.code, proposal, method=decision.method, reason=str(exc.context.get("reason") or exc.code), status=str(attempts)) from None
            else:
                # PIN or password wallets: whatever seam produced the decision (PIN box, password box, a platform
                # biometric seam carrying the unlock), the wallet's own secret must verify
                accepted = custody.verify_secret(profile.wallet_id, decision.pin_unlock)
        if not accepted:
            # A refused approval is a COUNTED attempt, not the end of the proposal: a mistyped PIN must not
            # burn a payment the owner still wants, and the fault + security event are filed either way.
            # The proposal is locked (rejected) once MAX_APPROVAL_ATTEMPTS refusals accumulate.
            attempts = proposals.record_approval_refusal(proposal.proposal_id, method=decision.method, reason=decision.reason or "approval_not_accepted")
            if attempts >= MAX_APPROVAL_ATTEMPTS:
                proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"method": decision.method, "reason": "approval_attempts_exhausted", "attempts": attempts}, fault_code="wallet_approval_rejected")
                raise self._fault("wallet_approval_rejected", proposal, method=decision.method, reason="approval_attempts_exhausted", status=str(attempts))
            raise self._fault("wallet_approval_rejected", proposal, method=decision.method, reason=decision.reason or "challenge_mismatch_or_wrong_pin", status=str(attempts))
        message = self._message_for(proposal, profile.public_key, rpc=rpc)
        proposal, effects = self._claim(proposal, profile, method=decision.method, payer_message=message, fee_minor=max(_reserved_fee_for(challenge.binding_view()), _svm_fee_for(proposal)), chain=spec_network(proposal.network))
        try:
            signer = device_signer or signers.signer_for(profile, pin=decision.pin_unlock or None, proposal_id=proposal.proposal_id)
            self._begin_attempt(effects["sign"])  # consume immediately BEFORE the signing execution
            raw = _serialize(message, signer.sign(bytes(message)))
        except WalletFault as exc:
            self._close_effect(effects["sign"], ok=False, reason=exc.code)
            effects = {"transaction": effects["transaction"]}
            self._abort_before_broadcast(proposal, effects, state=proposals.STATE_FAILED, fault_code=exc.code, reason=exc.code)
            raise
        self._close_effect(effects["sign"], ok=True, reason="signed")
        proposals.transition(proposal.proposal_id, proposals.STATE_SIGNED, detail={"signer": profile.mode})
        return self._broadcast(proposal, raw, effects["transaction"])

    # -- the Crypto Pilot approval: one quote, approval v3, the credential -------------------------------------
    def _count_approval_refusal(self, proposal: proposals.TransactionProposal, decision: Any, *, reason: str) -> int:
        """The legacy door's attempt law, shared: every refusal is counted and the proposal locks at the maximum."""
        attempts = proposals.record_approval_refusal(proposal.proposal_id, method=decision.method, reason=reason)
        if attempts >= MAX_APPROVAL_ATTEMPTS:
            proposals.transition(proposal.proposal_id, proposals.STATE_REJECTED, detail={"method": decision.method, "reason": "approval_attempts_exhausted", "attempts": attempts}, fault_code="wallet_approval_rejected")
        return attempts

    def _refuse_pilot_approval(self, proposal: proposals.TransactionProposal, decision: Any, *, reason: str) -> None:
        attempts = self._count_approval_refusal(proposal, decision, reason=reason)
        final = "approval_attempts_exhausted" if attempts >= MAX_APPROVAL_ATTEMPTS else reason
        raise self._fault("wallet_approval_rejected", proposal, method=decision.method, reason=final, status=str(attempts))

    def approve_pilot_transfer(self, proposal_id: str, *, quote_id: str, quote_digest: str, approver: approval_module.Approver) -> dict[str, Any]:
        """The Crypto Pilot approving request: gates, chain revalidation, ONE credential, the claim transaction, the
        effect reservation, signing, ONE send and a bounded observation. Nothing is signed before the claim commits,
        nothing is sent before the signed identity is durable, and no key exists while the network is read.

        Returns ``{"transfer": <read model>, "duplicate": bool}``; every refusal is a typed fault."""
        from core.wallet import capabilities, environment, quotes, settlement, transfers

        proposal = self._load(proposal_id)
        existing = transfers.get_transfer_by_id(proposal.proposal_id)
        if existing is not None:
            return self._repeat_pilot_approval(proposal, existing, quote_id=quote_id, quote_digest=quote_digest)
        if proposal.state != proposals.STATE_PENDING_APPROVAL:
            raise self._fault("wallet_duplicate_payment", proposal, reason="not_awaiting_approval", status=proposal.state)
        if not transfers.is_pilot_transfer(proposal):
            raise self._fault("wallet_quote_mismatch", proposal, reason="not_a_pilot_transfer")
        spec = environment.require_active(proposal.network, source_context=self.source_context)
        profile = custody.require_wallet(proposal.wallet_id, source_context=self.source_context)
        quote = quotes.require_open_quote(quote_id, proposal=proposal, quote_digest=quote_digest, account_address=profile.public_key, source_context=self.source_context)
        recipient = dict(getattr(proposal, "recipient", {}) or {})
        if recipient:
            # the quote the owner reviewed must name the same Contacts binding, and Contacts must still hold that exact destination
            if str(quote["fields"].get("recipient_fingerprint") or "") != str(recipient.get("fingerprint") or ""):
                raise self._fault("wallet_quote_mismatch", proposal, reason="quote_recipient_binding_differs")
            if recipient.get("resolution") == "contact":
                from core.contacts import verify_snapshot

                check = verify_snapshot(recipient)
                if not check.ok:
                    raise self._fault("wallet_recipient_refused", proposal, reason=f"contact_{check.status}")
        if proposal.origin == proposals.ORIGIN_USEPOD:
            # the provider's durable requirement, before any credential: pinned fields equal, window still open
            from core.wallet import usepod

            usepod.require_binding(proposal, moment=time.time(), quote_fields=quote["fields"], source_context=self.source_context)
        companion: dict[str, Any] | None = None
        if proposal.origin == proposals.ORIGIN_USEPOD:
            # the collection of previously accrued DNA fees planned to ride THIS approval, before any credential: the
            # companion stands (offered, unexpired, bound to this payment at the version the sheet showed)
            from core.wallet import dna_fees

            companion = dna_fees.require_companion_binding(proposal, quote_fields=quote["fields"], moment=time.time(), source_context=self.source_context)
        if proposal.origin == proposals.ORIGIN_DNA_FEE:
            # a fee collection is approved WITH its provider payment, never on a sheet of its own
            raise self._fault("wallet_quote_mismatch", proposal, reason="dna_fee_collection_rides_its_payment")
        challenge = approval_module.ApprovalChallengeV3.for_quote(proposal, quote)
        decision = approver.approve(challenge)
        if not approval_module.decision_binds_v3(decision, challenge):
            self._refuse_pilot_approval(proposal, decision, reason=decision.reason or "challenge_mismatch")
        if custody.seal_policy(profile.wallet_id) != custody.PILOT_SEAL_POLICY:
            # only a Crypto Pilot wallet moves funds through this lane; another custody is refused before any prompt
            raise self._fault("wallet_signing_unavailable", proposal, reason="pilot_approval_needs_pilot_wallet", mode=profile.mode)
        if not capabilities.pilot_transfer_ready(spec):
            # the credential is proven first (a wrong PIN is a counted attempt on any row), then the lane stops typed
            self._prove_pilot_credential(proposal, profile, decision)
            raise self._fault("wallet_network_disabled", proposal, reason=capabilities.REASON_PILOT_LANE_INCOMPLETE, quote_id=quote["quote_id"])
        self._require_not_frozen(proposal, door="pilot_approval")
        if spec.is_evm:
            from core.wallet.store import connection

            with connection() as conn:
                live = transfers.live_for_account(conn, spec.network, profile.public_key)
            if live is not None:
                # an EVM account sends one transfer at a time: the next waits, and no credential attempt is consumed
                raise self._fault("wallet_duplicate_payment", proposal, reason="previous_transfer_in_flight", previous_proposal_id=str(live["proposal_id"]), previous_state=str(live["state"]))
        marker_key = f"approving:{proposal.proposal_id}"
        marker = transfers.take_marker(marker_key, ttl_seconds=settlement.APPROVAL_MARKER_SECONDS)
        if marker is None:
            raise self._fault("wallet_duplicate_payment", proposal, reason="approval_in_progress")
        try:
            baseline = settlement.control_baseline()
            fresh = self._revalidate_svm(proposal, profile, spec, quote, companion=companion) if spec.is_svm else self._revalidate_evm(proposal, profile, spec, quote)
            return self._claim_sign_and_send(proposal, profile, spec, quote, challenge, decision, baseline, fresh, companion=companion)
        finally:
            transfers.release_marker(marker_key, marker)

    def _repeat_pilot_approval(self, proposal: proposals.TransactionProposal, existing: dict[str, Any], *, quote_id: str, quote_digest: str) -> dict[str, Any]:
        """The same approval arriving again (a retried request, a reloaded sheet): the consumed quote's id and digest
        answer with the transfer as it is; no credential, no claim, no send. Anything else is a duplicate payment attempt."""
        from core.wallet import quotes, transfers

        record = quotes.get_quote(quote_id)
        if record is not None and record["proposal_id"] == proposal.proposal_id and record["quote_id"] == existing["quote_id"] and hmac.compare_digest(str(quote_digest or ""), str(record["digest"])):
            answer: dict[str, Any] = {"transfer": transfers.latest_receipt(proposal.proposal_id), "duplicate": True}
            if proposal.origin == proposals.ORIGIN_USEPOD:
                from core.wallet import dna_fees

                answer["companion"] = dna_fees.companion_outcome(proposal.proposal_id)
            return answer
        raise self._fault("wallet_duplicate_payment", proposal, reason="transfer_already_claimed", status=str(existing["state"]))

    def _prove_pilot_credential(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, decision: Any) -> None:
        from core.wallet import pilot_custody

        try:
            pilot_custody.prove_credential(profile.wallet_id, decision.pin_unlock, source_context=self.source_context)
        except WalletFault as exc:
            if exc.code != "wallet_unlock_throttled":
                # a wrong credential is a counted attempt on this proposal too; a throttled one never reached the seal
                self._count_approval_refusal(proposal, decision, reason=exc.code)
            raise

    def _revalidate_svm(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, spec: chains.ChainIdentity, quote: dict[str, Any], *, companion: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fresh chain facts right before the claim, while no key exists: the endpoint proves the row's chain, the
        blockhash has enough validity left for the claim and the send, and the balance still covers the maximum
        debit of EVERY transaction the approval covers (the payment and, when planned, the fee collection) with a
        rent-exempt remainder. A mismatch supersedes the quote and asks for a fresh preview."""
        from core.wallet import quotes, settlement

        fields = quote["fields"]
        companion_fields = dict(companion["fields"]) if companion else {}
        extra_principal = int(companion_fields.get("amount_minor") or 0) if companion and str(companion_fields.get("asset") or "") == str(proposal.asset) else 0
        extra_fee = int(companion_fields.get("fee_max_minor") or 0) if companion else 0
        rpc = self._rpc_for(spec.network)
        try:
            self._require_svm_identity(rpc, spec)
            latest = rpc._call("getLatestBlockhash", [{"commitment": RpcClient.COMMITMENT}])
            height = int(rpc._call("getBlockHeight", [{"commitment": RpcClient.COMMITMENT}]))
            balance = rpc.balance(profile.public_key)
            blockhash = str(latest["value"]["blockhash"])
            last_valid = int(latest["value"].get("lastValidBlockHeight") or 0)
            slot = int(((latest.get("context") or {}).get("slot")) or 0)
        except WalletFault:
            raise
        except Exception as exc:
            raise self._fault("wallet_quote_unavailable", proposal, reason=f"revalidation_read_failed:{type(exc).__name__}") from None
        amount, fee_max = int(proposal.amount_minor), int(fields["fee_max_minor"])
        rent_minimum = int(fields.get("rent_minimum_minor") or 0)

        def expired(reason: str, **context: Any) -> WalletFault:
            quotes.supersede_quote(quote["quote_id"], reason=reason)
            return self._fault("wallet_quote_expired", proposal, reason=reason, quote_id=quote["quote_id"], **context)

        if not blockhash or last_valid - height < settlement.SVM_CLAIM_MARGIN_BLOCKS:
            raise expired("blockhash_validity_too_short", blocks_left=last_valid - height)
        if fields.get("token_transfer"):
            # the fee and the rent-exempt remainder are native; the principal is re-read in the token itself
            from core.wallet import svm_tokens

            if balance - fee_max - extra_fee < rent_minimum:
                raise expired("fee_balance_changed", balance_minor=balance, fee_max_minor=fee_max + extra_fee, rent_minimum_minor=rent_minimum)
            try:
                token_balance = svm_tokens.token_balance_minor(rpc, owner=profile.public_key, asset=svm_tokens.token_asset(spec.network, proposal.asset))
            except WalletFault:
                raise
            except Exception as exc:
                raise self._fault("wallet_quote_unavailable", proposal, reason=f"revalidation_read_failed:{type(exc).__name__}") from None
            if token_balance < amount + extra_principal:
                raise expired("token_balance_changed", balance_minor=token_balance, amount_minor=amount + extra_principal)
        else:
            if balance < amount + fee_max + extra_principal + extra_fee:
                raise expired("balance_changed", balance_minor=balance, max_total_minor=amount + fee_max + extra_principal + extra_fee)
            remainder = balance - amount - fee_max - extra_principal - extra_fee
            if remainder == 0 or remainder < rent_minimum:
                raise expired("remainder_below_rent_minimum", remainder_minor=remainder, rent_minimum_minor=rent_minimum)
        message = _build_message(proposal, profile.public_key, blockhash)
        companion_message = _build_message(companion["proposal"], profile.public_key, blockhash) if companion else None
        return {"rpc": rpc, "blockhash": blockhash, "slot": slot, "last_valid": last_valid, "balance": balance, "message": message, "companion_message": companion_message, "read_at": time.time()}

    def _revalidate_evm(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, spec: chains.ChainIdentity, quote: dict[str, Any]) -> dict[str, Any]:
        """Fresh chain facts right before an EVM claim, while no key exists: the endpoint proves the row's chain id,
        the account's pending and latest nonce both equal the quote's, the base fee is under the ceiling and the
        balance covers the maximum debit. A mismatch supersedes the quote and asks for a fresh preview."""
        from core.wallet import quotes

        fields = quote["fields"]
        params = dict(fields.get("tx_params") or {})
        url = chains.network_rpc_url(spec.network)
        rpc = RpcClient(url, network=spec.network)
        try:
            chains.verify_chain_identity(spec, lambda method: rpc._call(method, []), scope=chains.endpoint_scope("lifecycle", url))
            pending_count = int(str(rpc._call("eth_getTransactionCount", [profile.public_key, "pending"])), 16)
            latest_count = int(str(rpc._call("eth_getTransactionCount", [profile.public_key, "latest"])), 16)
            block = rpc._call("eth_getBlockByNumber", ["latest", False]) or {}
            base_fee = int(str(block.get("baseFeePerGas") or "0x0"), 16)
            block_number = int(str(block.get("number") or "0x0"), 16)
            balance = int(str(rpc._call("eth_getBalance", [profile.public_key, "latest"])), 16)
        except WalletFault:
            raise
        except Exception as exc:
            raise self._fault("wallet_quote_unavailable", proposal, reason=f"revalidation_read_failed:{type(exc).__name__}") from None
        nonce = int(params.get("nonce", -1))
        amount, fee_max = int(proposal.amount_minor), int(fields["fee_max_minor"])

        def expired(reason: str, **context: Any) -> WalletFault:
            quotes.supersede_quote(quote["quote_id"], reason=reason)
            return self._fault("wallet_quote_expired", proposal, reason=reason, quote_id=quote["quote_id"], **context)

        if nonce < 0 or pending_count != nonce or latest_count != nonce:
            raise expired("nonce_changed", quoted_nonce=nonce, pending_count=pending_count, latest_count=latest_count)
        if spec.fee_model != chains.FEE_BSC and base_fee > int(params["max_fee_per_gas"]):
            raise expired("base_fee_above_ceiling", base_fee_wei=base_fee, max_fee_per_gas=int(params["max_fee_per_gas"]))
        if balance < amount + fee_max:
            raise expired("balance_changed", balance_minor=balance, max_total_minor=amount + fee_max)
        return {"rpc": rpc, "url": url, "params": params, "nonce": nonce, "block_number": block_number, "balance": balance, "read_at": time.time()}

    def _claim_pilot(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, spec: chains.ChainIdentity, quote: dict[str, Any], challenge: Any, baseline: dict[str, Any], fresh: dict[str, Any], *, companion: dict[str, Any] | None = None) -> dict[str, Any]:
        """The claim: one BEGIN IMMEDIATE that checks the freeze, the environment and the control epochs, consumes the
        quote, moves the proposal, holds the amount plus the fee ceiling and inserts the transfer row -- and, when the
        approval covers a fee collection, claims that collection in the SAME transaction (its ledger record at the
        version the sheet showed, its own hold, its own transfer row under the same challenge). A refusal rolls
        everything back, so the quote stays open, the proposal stays approvable and the collection stays offered."""
        from core.wallet import controls, environment, quotes, settlement, transfers
        from core.wallet.store import connection

        fields = quote["fields"]
        now = time.time()
        with connection() as conn:
            limits._begin_immediate(conn)
            if limits._frozen(conn):
                raise self._fault("wallet_limit_exceeded", proposal, limit="frozen", reason="panic_freeze_blocks_pilot_approval")
            active = environment._active(conn)
            if active.environment != spec.environment:
                raise self._fault("wallet_environment_inactive", proposal, reason="environment_changed_since_the_quote", active_environment=active.environment)
            epochs = controls.epochs(conn)
            if epochs != baseline["epochs"]:
                raise self._fault("wallet_approval_rejected", proposal, reason="controls_changed_during_approval")
            try:
                quotes._consume(conn, quote["quote_id"], quote["digest"], now)
            except quotes.QuoteConsumeError:
                raise self._fault("wallet_quote_expired", proposal, reason="quote_no_longer_open", quote_id=quote["quote_id"]) from None
            identity = ({"blockhash": fresh["blockhash"], "blockhash_slot": fresh["slot"], "last_valid_block_height": fresh["last_valid"]}
                        if spec.is_svm else {"nonce": int(fresh["nonce"])})
            if proposal.origin == proposals.ORIGIN_USEPOD:
                # inside the claim transaction: the requirement is re-read on this connection; a refusal rolls back
                from core.wallet import usepod

                usepod.check_binding(conn, proposal, moment=now, quote_fields=fields)
            if spec.is_evm and transfers.live_for_account(conn, spec.network, profile.public_key) is not None:
                raise self._fault("wallet_duplicate_payment", proposal, reason="previous_transfer_in_flight")
            try:
                claimed = transfers.insert_claim(
                    conn, proposal=proposal, quote_id=quote["quote_id"], quote_digest=quote["digest"], challenge_digest=challenge.digest,
                    environment=spec.environment, family=spec.family, from_address=profile.public_key, fee_max_minor=int(fields["fee_max_minor"]),
                    owner_token=settlement.new_owner_token(), lease_until=now + settlement.SIGNER_BUDGET_SECONDS,
                    dispatch_deadline=now + settlement.DISPATCH_DEADLINE_SECONDS, epochs=epochs, enabled_generation=int(baseline["enabled_generation"]),
                    now=now, **identity,
                )
                if companion:
                    # inside the same claim transaction: the collection record moves to claimed under THIS quote at the
                    # version the sheet showed, then the collection's own transfer row (its own hold, the same challenge)
                    from core.wallet import dna_fees

                    dna_fees.check_companion_claim(conn, companion["proposal"], quote_id=quote["quote_id"], companion_fields=companion["fields"], now=now)
                    claimed["companion_claim"] = transfers.insert_claim(
                        conn, proposal=companion["proposal"], quote_id=f"{quote['quote_id']}#collection", quote_digest=quote["digest"], challenge_digest=challenge.digest,
                        environment=spec.environment, family=spec.family, from_address=profile.public_key, fee_max_minor=int(companion["fields"].get("fee_max_minor") or 0),
                        owner_token=settlement.new_owner_token(), lease_until=now + settlement.SIGNER_BUDGET_SECONDS,
                        dispatch_deadline=now + settlement.DISPATCH_DEADLINE_SECONDS, epochs=epochs, enabled_generation=int(baseline["enabled_generation"]),
                        now=now, **identity,
                    )
                return claimed
            except sqlite3.IntegrityError as clash:
                raise self._fault("wallet_duplicate_payment", proposal, reason="account_slot_in_use", detail=str(clash)[:80]) from None
            except limits.LimitRefusedError as refused:
                raise self._fault("wallet_limit_exceeded", proposal, limit=refused.verdict.limit, reason=refused.verdict.reason, amount_minor=proposal.amount_minor, asset=proposal.asset) from None
            except limits.HoldStateConflictError as conflict:
                raise self._fault("wallet_duplicate_payment", proposal, reason=f"hold_conflict:{conflict}") from None
            except proposals.ProposalTransitionError:
                raise self._fault("wallet_duplicate_payment", proposal, reason="already_claimed_by_another_approval") from None

    def _claim_sign_and_send(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile, spec: chains.ChainIdentity, quote: dict[str, Any], challenge: Any, decision: Any, baseline: dict[str, Any], fresh: dict[str, Any], *, companion: dict[str, Any] | None = None) -> dict[str, Any]:
        """ONE credential, ONE signing session: the payment and, when the approval covers one, the collection of
        previously accrued DNA fees. Both are claimed in one transaction and signed in the same session (two
        messages, two signatures, one unlock: the old challenge is never reused for the second transaction); the
        collection is transmitted only after the payment's bytes left, and never when they did not. Two separately
        journalled transactions under one correctly scoped approval, never an atomic on-chain pair."""
        from core.vool_wallet import b58encode
        from core.wallet import pilot_custody, quotes, settlement, transfers
        from core.wallet.store import connection

        if spec.is_svm:
            message = fresh["message"]
            message_bytes = bytes(message)
            companion_message = fresh.get("companion_message") if companion else None
            companion_bytes = bytes(companion_message) if companion_message is not None else b""

            def produce(signer: Any) -> tuple[bytes, str]:
                signature = signer.sign_svm(message_bytes)
                return _serialize(message, signature), b58encode(bytes(signature))

            def produce_companion(signer: Any) -> tuple[bytes, str]:
                signature = signer.sign_svm(companion_bytes)
                return _serialize(companion_message, signature), b58encode(bytes(signature))
        else:
            params = dict(fresh["params"])
            message_bytes = quotes.unsigned_type2_bytes(**params)
            companion = None  # the EVM family composes nothing: no fee lane rides it
            companion_bytes = b""

            def produce(signer: Any) -> tuple[bytes, str]:
                return signer.sign_evm_type2(params)

            def produce_companion(signer: Any) -> tuple[bytes, str]:
                raise self._fault("wallet_signing_unavailable", proposal, reason="no_companion_on_evm")
        pid = proposal.proposal_id
        cid = str(companion["proposal"].proposal_id) if companion else ""
        companion_tx = ""
        companion_effects: dict[str, Any] = {}

        def release_both(reason: str) -> None:
            settlement.release_claimed(pid, reason=reason)
            if cid:
                settlement.release_claimed(cid, reason=f"payment_{reason}"[:120])

        with contextlib.ExitStack() as stack:
            try:
                signer = stack.enter_context(pilot_custody.signing_session(profile.wallet_id, decision.pin_unlock, source_context=self.source_context))
            except WalletFault as exc:
                if exc.code != "wallet_unlock_throttled":
                    self._count_approval_refusal(proposal, decision, reason=exc.code)
                raise
            # the key is open from here to the end of the block: only local work happens inside
            claimed = self._claim_pilot(proposal, profile, spec, quote, challenge, baseline, fresh, companion=companion)
            companion_claim = claimed.get("companion_claim") if companion else None
            effects: dict[str, Any] = {}
            try:
                reservation = reconciliation.reserve_payment_effect(proposal, message_digest=hashlib.sha256(message_bytes).hexdigest(), source_context=self.source_context)
                if str(reservation.get("outcome") or "") != "reserved":
                    raise self._fault("wallet_duplicate_payment", proposal, reason="effect_in_flight")
                effects = {"sign": self._open_effect(self._base(proposal), effect_class=EFFECT_CLASS_SIGN), "transaction": self._open_effect(self._base(proposal), effect_class=EFFECT_CLASS_TRANSACTION)}
                raw, tx_id = produce(signer)
                companion_raw = b""
                companion_reservation: dict[str, Any] = {}
                if companion:
                    companion_reservation = reconciliation.reserve_payment_effect(companion["proposal"], message_digest=hashlib.sha256(companion_bytes).hexdigest(), source_context=self.source_context)
                    if str(companion_reservation.get("outcome") or "") != "reserved":
                        raise self._fault("wallet_duplicate_payment", proposal, reason="companion_effect_in_flight")
                    companion_effects = {"sign": self._open_effect(self._base(companion["proposal"]), effect_class=EFFECT_CLASS_SIGN), "transaction": self._open_effect(self._base(companion["proposal"]), effect_class=EFFECT_CLASS_TRANSACTION)}
                    companion_raw, companion_tx = produce_companion(signer)
                with connection() as conn:
                    limits._begin_immediate(conn)
                    transfers.transition(
                        conn, pid, transfers.STATE_SIGNED, expected_state=transfers.STATE_CLAIMED, now=time.time(), owner_token=claimed["owner_token"],
                        tx_id=tx_id, raw_b64=base64.b64encode(raw).decode("ascii"), lease_until=time.time() + settlement.SIGNER_BUDGET_SECONDS,
                        effect_instance_id=str(reservation.get("effect_instance_id") or ""),
                    )
                    if companion:
                        transfers.transition(
                            conn, cid, transfers.STATE_SIGNED, expected_state=transfers.STATE_CLAIMED, now=time.time(), owner_token=companion_claim["owner_token"],
                            tx_id=companion_tx, raw_b64=base64.b64encode(companion_raw).decode("ascii"), lease_until=time.time() + settlement.SIGNER_BUDGET_SECONDS,
                            effect_instance_id=str(companion_reservation.get("effect_instance_id") or ""),
                        )
            except transfers.TransferTransitionError as exc:
                for effect in (*effects.values(), *companion_effects.values()):
                    self._cancel_effect(effect, reason=str(exc))
                if "cancel_requested" in str(exc):
                    settlement.finalize_cancel(pid)
                    if cid:
                        settlement.release_claimed(cid, reason="payment_cancelled_before_signing")
                    raise self._fault("wallet_approval_rejected", proposal, reason="owner_cancelled_before_signing") from None
                release_both(f"sign_cas_lost:{exc}")
                raise self._fault("wallet_duplicate_payment", proposal, reason=f"sign_cas_lost:{exc}") from None
            except WalletFault as exc:
                for effect in (*effects.values(), *companion_effects.values()):
                    self._cancel_effect(effect, reason=exc.code)
                release_both(exc.code)
                raise
            except Exception as exc:
                for effect in (*effects.values(), *companion_effects.values()):
                    self._cancel_effect(effect, reason=type(exc).__name__)
                release_both(f"signing_failed:{type(exc).__name__}")
                raise self._fault("wallet_signing_unavailable", proposal, reason=f"signing_failed:{type(exc).__name__}") from exc
        # the key is gone: everything below reads the network
        if pilot_custody.credential_generation(profile.wallet_id) != int(getattr(signer, "generation", 1)):
            # a recovery or credential change committed while these bytes were being signed: the credential that opened
            # the key is no longer the wallet's, so the signed bytes never reach the transmit site
            for effect in effects.values():
                self._cancel_effect(effect, reason="credential_rotated_before_send")
            settlement.revoke_signed(pid, reason="credential_rotated_before_send")
            raise self._fault("wallet_signing_unavailable", proposal, reason="credential_rotated_before_send", status=transfers.STATE_SIGNED_REVOKED)
        base = self._base(proposal)
        receipts.journal_intended({**base, "state": proposals.STATE_SIGNED, "tx_id": tx_id}, source_context=self.source_context)
        self._begin_attempt(effects.get("transaction"))
        try:
            self.send_signed(pid, origin="approve", rpc=fresh["rpc"])
        except WalletFault as exc:
            self._close_effect(effects.get("transaction"), ok=False, reason=exc.code)
            if cid:
                # the payment's bytes never left: the collection's signed bytes never leave either; the debt goes back
                for effect in companion_effects.values():
                    self._cancel_effect(effect, reason=f"payment_not_sent:{exc.code}")
                self._companion_not_sent(cid, reason=f"payment_not_sent:{exc.code}")
            raise
        companion_view: dict[str, Any] | None = None
        if cid:
            companion_view = self._send_companion(companion, fresh, effects=companion_effects, tx_id=companion_tx)
        view = settlement.observe_bounded(pid, rpc=fresh["rpc"], budget_seconds=_CONFIRM_BUDGET_SECONDS) or transfers.latest_receipt(pid)
        self._close_effect(effects.get("sign"), ok=True, reason="signed")
        self._close_effect(effects.get("transaction"), ok=view["state"] not in (transfers.STATE_FAILED_ON_CHAIN,), reason=view["state"])
        receipts.journal_terminal({**base, "state": view["state"], "tx_id": view.get("tx_id") or tx_id, "evidence_kind": view.get("evidence_kind")}, source_context=self.source_context)
        receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=view["state"] == transfers.STATE_CONFIRMED, status=view["state"], tx_signature=view.get("tx_id") or "")
        result: dict[str, Any] = {"transfer": view, "duplicate": False}
        if cid:
            if companion_view is not None and str(companion_view.get("state") or "") in (transfers.STATE_UNKNOWN, transfers.STATE_PENDING, transfers.STATE_DISPATCHING):
                companion_view = settlement.observe_bounded(cid, rpc=fresh["rpc"], budget_seconds=_CONFIRM_BUDGET_SECONDS) or companion_view
            from core.wallet import dna_fees

            result["companion"] = {"transfer": companion_view, **(dna_fees.companion_outcome(pid) or {})}
        elif proposal.origin == proposals.ORIGIN_USEPOD:
            result["companion"] = None
        return result

    def _send_companion(self, companion: dict[str, Any], fresh: dict[str, Any], *, effects: dict[str, Any], tx_id: str) -> dict[str, Any]:
        """Transmit the collection signed with the payment, after the payment's bytes left. A send that fails before
        the bytes leave ends the collection as never sent (the debt goes back); a sent one is observed like any
        transfer. Nothing here can undo or resend the payment."""
        from core.wallet import settlement, transfers

        proposal = companion["proposal"]
        cid = proposal.proposal_id
        base = self._base(proposal)
        receipts.journal_intended({**base, "state": proposals.STATE_SIGNED, "tx_id": tx_id}, source_context=self.source_context)
        self._begin_attempt(effects.get("transaction"))
        try:
            self.send_signed(cid, origin="approve", rpc=fresh["rpc"])
        except WalletFault as exc:
            self._close_effect(effects.get("transaction"), ok=False, reason=exc.code)
            self._companion_not_sent(cid, reason=f"collection_not_sent:{exc.code}")
            return transfers.latest_receipt(cid) or {"proposal_id": cid, "state": "not_sent", "reason": exc.code}
        view = settlement.observe_bounded(cid, rpc=fresh["rpc"], budget_seconds=_CONFIRM_BUDGET_SECONDS) or transfers.latest_receipt(cid) or {"proposal_id": cid, "state": transfers.STATE_UNKNOWN}
        # the collection's signature is public exactly like the payment's (lifecycle's own transmit
        # site publishes its broadcast); without this the free-text redactor masks the companion's
        # base58 signature out of receipts as a key-shaped run — caught by the correction's first
        # served run (Goal 2 stage 2)
        redaction.publish_identifier(view.get("tx_id") or tx_id)
        self._close_effect(effects.get("sign"), ok=True, reason="signed")
        self._close_effect(effects.get("transaction"), ok=str(view.get("state") or "") not in (transfers.STATE_FAILED_ON_CHAIN,), reason=str(view.get("state") or ""))
        receipts.journal_terminal({**base, "state": str(view.get("state") or ""), "tx_id": view.get("tx_id") or tx_id, "evidence_kind": view.get("evidence_kind")}, source_context=self.source_context)
        receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=str(view.get("state") or "") == transfers.STATE_CONFIRMED, status=str(view.get("state") or ""), tx_signature=str(view.get("tx_id") or ""))
        return view

    def _companion_not_sent(self, cid: str, *, reason: str) -> None:
        """A collection signed with the payment whose bytes never left: revoke the signed row and release it as never
        sent (the hold goes back, the ledger gives the debt back through the transfer projection). Refusals of the
        effect record's release proof leave the row for the observer, exactly as for any revoked transfer."""
        from core.wallet import settlement, transfers

        with contextlib.suppress(Exception):
            settlement.revoke_signed(cid, reason=reason)
        with contextlib.suppress(Exception):
            settlement.apply_transition(cid, transfers.STATE_RELEASED, transfers.STATE_SIGNED_REVOKED, detail={"why": reason})

    # -- the pilot lane's only transmit site -----------------------------------------------------------------------
    def _owner_resend(self, proposal_id: str, *, baseline: dict[str, Any] | None, rpc: RpcClient | None) -> dict[str, Any]:
        """D9: the owner's resend of the same signed bytes (EVM). The guard is the resend's own: not frozen now, Crypto
        on now, the row's environment active, the control epochs equal the reading taken before the credential, the row
        still ``unknown`` under the same token and attempt count, the effect record's instance still the stored one in
        dispatched or unknown. One CAS rotates the token and counts the attempt; then the stored bytes go out once."""
        from core.runtime_continuity import get_unresolved_effect
        from core.wallet import config as wallet_config
        from core.wallet import controls, environment, settlement, transfers
        from core.wallet.store import connection

        proposal = self._load(proposal_id)
        row = transfers.get_transfer_by_id(proposal.proposal_id)
        if row is None or str(row["state"]) != transfers.STATE_UNKNOWN or not row.get("raw_b64"):
            raise self._fault("wallet_duplicate_payment", proposal, reason="resend_needs_an_unknown_transfer", status=str(row["state"]) if row else "none")
        spec = chains.resolve_network(row["network"])
        if not spec.is_evm:
            raise self._fault("wallet_network_disabled", proposal, reason="resend_is_evm_only", family=spec.family)
        pid = proposal.proposal_id
        baseline = baseline or settlement.control_baseline()
        reason = settlement.resend_available(pid)
        if reason:
            raise self._fault("wallet_broadcast_failed", proposal, reason=f"resend_unavailable:{reason}", status=transfers.STATE_UNKNOWN)
        logical = reconciliation.logical_effect_id(pid)
        record = get_unresolved_effect(logical) or {}
        instance = str(row.get("effect_instance_id") or "")
        if str(record.get("state") or "") not in {"dispatched", "unknown"} or str(record.get("effect_instance_id") or "") != instance:
            raise self._fault("wallet_broadcast_failed", proposal, reason=f"resend_effect_record_mismatch:{record.get('state') or 'none'}", status=transfers.STATE_UNKNOWN)
        url = chains.network_rpc_url(spec.network)
        client = rpc or RpcClient(url, network=spec.network)
        try:
            chains.verify_chain_identity(spec, lambda method: client._call(method, []), scope=chains.endpoint_scope("lifecycle", url))
        except Exception as exc:
            raise self._fault("wallet_broadcast_failed", proposal, reason=f"endpoint_unreadable_before_send:{type(exc).__name__}", status=transfers.STATE_UNKNOWN) from None
        now = time.time()
        new_token = settlement.new_owner_token()
        with connection() as conn:
            limits._begin_immediate(conn)
            if limits._frozen(conn):
                raise self._fault("wallet_limit_exceeded", proposal, limit="frozen", reason="panic_freeze_blocks_resend")
            if not wallet_config.wallet_enabled():
                raise self._fault("wallet_disabled", proposal, reason="crypto_off_blocks_resend")
            if environment._active(conn).environment != str(row["environment"]):
                raise self._fault("wallet_environment_inactive", proposal, reason="environment_changed_before_resend")
            epochs = controls.epochs(conn)
            if epochs != baseline["epochs"]:
                raise self._fault("wallet_approval_rejected", proposal, reason="controls_changed_during_resend")
            if not transfers.claim_resend(conn, pid, owner_token=str(row["owner_token"]), attempts_sent=int(row.get("attempts_sent") or 0), new_token=new_token, epochs=epochs, now=now):
                raise self._fault("wallet_duplicate_payment", proposal, reason="resend_cas_lost")
        raw = base64.b64decode(str(row["raw_b64"] or ""))
        kind, detail, returned = self._transmit_evm(client, raw, str(row["tx_id"] or ""))
        with connection() as conn:
            fresh = transfers.get_transfer(conn, pid) or row
            transfers.update_columns(conn, pid, evidence_kind=kind, offered_exit_json="{}", evidence_json=settlement.appended_evidence(fresh, "resend_answer", answer=kind, detail=detail, returned=returned))
        receipts.journal_security_event("wallet_transfer_resent", {"proposal_id": pid, "network": row["network"], "answer": kind}, source_context=self.source_context)
        return transfers.latest_receipt(pid) or {}

    def send_signed(self, proposal_id: str, *, origin: str = "approve", rpc: RpcClient | None = None, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        """Transmit a signed pilot transfer exactly once. Before the bytes leave: the endpoint proves the row's chain,
        the blockhash is still valid, the effect record is marked dispatched, and one guarded transaction checks the
        freeze, the environment, the control epochs, the deadline, the cancel flag and the owner token while moving
        the row to ``dispatching``. After the send the row is ``unknown`` with what the node answered; nothing is
        ever released here."""
        from core.runtime_continuity import get_unresolved_effect, mark_effect_dispatched
        from core.wallet import controls, environment, settlement, transfers
        from core.wallet.store import connection

        if origin == "owner_resend":
            return self._owner_resend(proposal_id, baseline=baseline, rpc=rpc)
        if origin != "approve":
            raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"proposal_id": str(proposal_id), "reason": "unknown_send_origin", "origin": str(origin)}, source_context=self.source_context)
        proposal = self._load(proposal_id)
        row = transfers.get_transfer_by_id(proposal.proposal_id)
        if row is None:
            raise self._fault("wallet_not_found", proposal, reason="no_transfer_row")
        if row["state"] != transfers.STATE_SIGNED:
            raise self._fault("wallet_duplicate_payment", proposal, reason=f"not_signed:{row['state']}", status=str(row["state"]))
        spec = chains.resolve_network(row["network"])
        pid = proposal.proposal_id
        if spec.is_svm:
            client = rpc or self._rpc_for(spec.network)
            try:
                self._require_svm_identity(client, spec)
                height = int(client._call("getBlockHeight", [{"commitment": RpcClient.COMMITMENT}]))
            except Exception as exc:
                settlement.record_evidence(pid, "pre_send_read_failed", error=type(exc).__name__)
                raise self._fault("wallet_broadcast_failed", proposal, reason=f"endpoint_unreadable_before_send:{type(exc).__name__}", status=transfers.STATE_SIGNED) from None
            if height + settlement.SVM_SEND_MARGIN_BLOCKS >= int(row.get("last_valid_block_height") or 0):
                settlement.revoke_signed(pid, reason="blockhash_validity_consumed")
                raise self._fault("wallet_broadcast_failed", proposal, reason="signed_not_sent:blockhash_validity_consumed", status=transfers.STATE_SIGNED_REVOKED)
        else:
            url = chains.network_rpc_url(spec.network)
            client = rpc or RpcClient(url, network=spec.network)
            try:
                chains.verify_chain_identity(spec, lambda method: client._call(method, []), scope=chains.endpoint_scope("lifecycle", url))
                latest_count = int(str(client._call("eth_getTransactionCount", [str(row["from_address"]), "latest"])), 16)
            except Exception as exc:
                settlement.record_evidence(pid, "pre_send_read_failed", error=type(exc).__name__)
                raise self._fault("wallet_broadcast_failed", proposal, reason=f"endpoint_unreadable_before_send:{type(exc).__name__}", status=transfers.STATE_SIGNED) from None
            if latest_count > int(row.get("nonce") if row.get("nonce") is not None else -1):
                # the account's slot was used by another transaction: these bytes can never land
                settlement.revoke_signed(pid, reason="nonce_used_elsewhere")
                raise self._fault("wallet_broadcast_failed", proposal, reason="signed_not_sent:nonce_used_elsewhere", status=transfers.STATE_SIGNED_REVOKED)
        if proposal.origin == proposals.ORIGIN_USEPOD:
            from core.wallet import usepod

            if usepod.binding_expired(proposal, moment=time.time()):
                # the provider's window closed between the signature and the transmit: these bytes never leave
                settlement.revoke_signed(pid, reason="provider_requirement_expired")
                usepod.mark_expired(pid, detail="window_closed_before_send", expire_proposal=False)
                raise self._fault("wallet_broadcast_failed", proposal, reason="signed_not_sent:provider_requirement_expired", status=transfers.STATE_SIGNED_REVOKED)
        logical = reconciliation.logical_effect_id(pid)
        instance = str(row.get("effect_instance_id") or "")
        if not mark_effect_dispatched(logical_effect_id=logical, effect_instance_id=instance, claimed_by=AUTHORITY):
            record = get_unresolved_effect(logical) or {}
            if not (str(record.get("state") or "") == "dispatched" and str(record.get("effect_instance_id") or "") == instance):
                settlement.revoke_signed(pid, reason="effect_mark_refused")
                raise self._fault("wallet_broadcast_failed", proposal, reason="signed_not_sent:effect_mark_refused", status=transfers.STATE_SIGNED_REVOKED)
        now = time.time()
        try:
            with connection() as conn:
                limits._begin_immediate(conn)
                if limits._frozen(conn):
                    raise self._fault("wallet_limit_exceeded", proposal, limit="frozen", reason="panic_freeze_blocks_send")
                if environment._active(conn).environment != str(row["environment"]):
                    raise self._fault("wallet_environment_inactive", proposal, reason="environment_changed_before_send")
                epochs = controls.epochs(conn)
                if (epochs["freeze"], epochs["enabled"], epochs["environment"]) != (int(row["epoch_freeze"]), int(row["epoch_enabled"]), int(row["epoch_environment"])):
                    raise self._fault("wallet_approval_rejected", proposal, reason="controls_changed_before_send")
                if now >= float(row.get("dispatch_deadline") or 0):
                    raise self._fault("wallet_broadcast_failed", proposal, reason="dispatch_deadline_passed")
                transfers.transition(conn, pid, transfers.STATE_DISPATCHING, expected_state=transfers.STATE_SIGNED, now=now, owner_token=str(row["owner_token"]),
                                     dispatch_claimed_at=now, attempts_sent=int(row.get("attempts_sent") or 0) + 1)
        except transfers.TransferTransitionError as exc:
            if "cancel_requested" in str(exc):
                settlement.finalize_cancel(pid)
                raise self._fault("wallet_approval_rejected", proposal, reason="owner_cancelled_before_sending") from None
            raise self._fault("wallet_duplicate_payment", proposal, reason=f"send_cas_lost:{exc}") from None
        except WalletFault:
            # the guard refused after the effect mark: the bytes never left, the row leaves the sendable state
            settlement.revoke_signed(pid, reason="send_guard_refused")
            raise
        raw = base64.b64decode(str(row["raw_b64"] or ""))
        kind, detail, returned = (self._transmit_svm if spec.is_svm else self._transmit_evm)(client, raw, str(row["tx_id"] or ""))
        with connection() as conn:
            limits._begin_immediate(conn)
            transfers.transition(conn, pid, transfers.STATE_UNKNOWN, expected_state=transfers.STATE_DISPATCHING, now=time.time(), owner_token=str(row["owner_token"]),
                                 evidence_kind=kind, evidence_json=settlement.appended_evidence(row, "send_answer", answer=kind, detail=detail, returned=returned))
        if detail.startswith("id_mismatch"):
            receipts.journal_security_event("wallet_signature_invalid", {"proposal_id": pid, "network": row["network"], "reason": "node_returned_another_id"}, source_context=self.source_context)
        return transfers.latest_receipt(pid) or {}

    @staticmethod
    def _transmit_evm(client: RpcClient, raw: bytes, tx_id: str) -> tuple[str, str, str]:
        """One eth_sendRawTransaction; the answer classified into the evidence kinds the read model labels. A refusal
        is only an affirmative txpool answer that the bytes were not accepted this time; every kind keeps the hold."""
        try:
            returned = str(client._call("eth_sendRawTransaction", ["0x" + raw.hex()]) or "")
        except RpcRejected as exc:
            if not exc.validated:
                return "no_answer", f"unvalidated_error:{exc.code}", ""
            message = exc.message.lower()
            if any(text in message for text in ("already known", "already imported", "known transaction", "alreadyknown")):
                return "already_known", exc.message[:200], ""
            if any(text in message for text in _EVM_REFUSAL_TEXTS):
                return "refused", f"{exc.code}:{exc.message[:200]}", ""
            return "other_error", f"{exc.code}:{exc.message[:200]}", ""
        except Exception as exc:
            return "no_answer", classify_broadcast_failure(exc).kind, ""
        if returned.lower() == tx_id.lower():
            return "submitted", "", returned
        return "no_answer", "id_mismatch", returned[:120]

    @staticmethod
    def _transmit_svm(client: RpcClient, raw: bytes, tx_id: str) -> tuple[str, str, str]:
        """One sendTransaction; the answer classified into the evidence kinds the read model labels. Every kind keeps
        the hold: a validated refusal means the node did not forward the bytes this time, never that they cannot land."""
        try:
            returned = str(client.broadcast(raw) or "")
        except RpcRejected as exc:
            if not exc.validated:
                return "no_answer", f"unvalidated_error:{exc.code}", ""
            if "already been processed" in exc.message or "AlreadyProcessed" in exc.message:
                return "already_known", exc.message[:200], ""
            if exc.code in (-32002, -32003):
                return "refused", f"{exc.code}:{exc.message[:200]}", ""
            return "other_error", f"{exc.code}:{exc.message[:200]}", ""
        except Exception as exc:
            return "no_answer", classify_broadcast_failure(exc).kind, ""
        if returned == tx_id:
            return "submitted", "", returned
        return "no_answer", "id_mismatch", returned[:120]

    # -- stage 3b: external approval -> signing request -> submitted signature -> broadcast --------------
    def request_external_signature(self, proposal_id: str) -> dict[str, Any]:
        """For an external-signer wallet: claim, hold, then hand out the exact bytes to sign. Owner-local only.

        A proposal already ``awaiting_signature`` whose request is still OPEN and unexpired
        RESUMES: the same request view is returned (no new claim, no new hold, the same
        one-consume fence) — the recovery seam for a process or UI that died between approve
        and submit. A proposal stranded at ``approved`` with no open request (a crash between
        the claim and the request opening) re-opens the request the claim already paid for.
        Neither path releases or re-reserves anything."""
        proposal = self._load(proposal_id)
        self._require_signable_row(proposal)
        if proposal.state == proposals.STATE_AWAITING_SIGNATURE and not proposal.tx_signature:
            record = external_signing.open_request_for_proposal(proposal.proposal_id)
            if record is not None and not external_signing.is_expired(record):
                view = external_signing.request_view(record)
                view["resume"] = True
                return view
            if record is not None:
                raise self._fault("wallet_approval_rejected", proposal, reason="signing_request_expired", status=proposal.state)
        if proposal.state == proposals.STATE_APPROVED and not proposal.tx_signature and external_signing.open_request_for_proposal(proposal.proposal_id) is None:
            # the claim (hold + effect reservation) already happened; only the request never
            # opened. Open it now and walk to awaiting_signature — nothing is re-reserved.
            return self._open_external_request_after_claim(proposal)
        if proposal.state != proposals.STATE_PENDING_APPROVAL:
            raise self._fault("wallet_duplicate_payment", proposal, reason="not_awaiting_approval", status=proposal.state)
        profile = custody.require_wallet(proposal.wallet_id, source_context=self.source_context)
        if profile.mode != custody.MODE_EXTERNAL_SIGNER:
            raise self._fault("wallet_signing_unavailable", proposal, reason="not_an_external_signer_wallet")
        if chains.resolve_network(proposal.network).is_evm:
            return self._request_evm_signature(proposal, profile)
        rpc = self._prove_before_claim(proposal)
        message = self._message_for(proposal, profile.public_key, rpc=rpc)
        proposal, effects = self._claim(proposal, profile, method="external_signer", payer_message=message, fee_minor=_svm_fee_for(proposal), chain=spec_network(proposal.network))
        try:
            self._begin_attempt(effects["sign"])  # the signing execution starts when the bytes leave for the wallet
            record = external_signing.open_signing_request(proposal, public_key=profile.public_key, message=bytes(message), unsigned_transaction=_serialize(message, ZERO_SIGNATURE))
        except Exception as exc:
            self._close_effect(effects["sign"], ok=False, reason="signing_request_failed")
            self._abort_before_broadcast(proposal, {"transaction": effects["transaction"]}, state=proposals.STATE_FAILED, fault_code="wallet_signing_unavailable", reason=f"signing_request_failed:{type(exc).__name__}")
            raise self._fault("wallet_signing_unavailable", proposal, reason="signing_request_failed") from exc
        # the sign effect stays open (its outcome is the external wallet's answer); the transaction effect is
        # re-opened by submit_external_signature on the request thread that broadcasts
        self._cancel_effect(effects["transaction"], reason="awaiting external signature; re-opened at submit")
        proposals.transition(proposal.proposal_id, proposals.STATE_AWAITING_SIGNATURE, detail={"request_id": record["request_id"]})
        return external_signing.request_view(record)

    def _open_external_request_after_claim(self, proposal: proposals.TransactionProposal) -> dict[str, Any]:
        """Resume a crash between the claim and the request opening: the hold and the A6
        reservation already exist, so this only opens the request and walks the state."""
        profile = custody.require_wallet(proposal.wallet_id, source_context=self.source_context)
        if profile.mode != custody.MODE_EXTERNAL_SIGNER:
            raise self._fault("wallet_signing_unavailable", proposal, reason="not_an_external_signer_wallet")
        if chains.resolve_network(proposal.network).is_evm:
            return self._request_evm_signature_opened(proposal, profile)
        message = self._message_for(proposal, profile.public_key)
        try:
            record = external_signing.open_signing_request(proposal, public_key=profile.public_key, message=bytes(message), unsigned_transaction=_serialize(message, ZERO_SIGNATURE))
        except Exception as exc:
            raise self._fault("wallet_signing_unavailable", proposal, reason=f"signing_request_failed:{type(exc).__name__}") from exc
        proposals.transition(proposal.proposal_id, proposals.STATE_AWAITING_SIGNATURE, detail={"request_id": record["request_id"], "resumed": True})
        view = external_signing.request_view(record)
        view["resume"] = True
        return view

    def _request_evm_signature_opened(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile) -> dict[str, Any]:
        """The EVM tail of an approved-zombie resume: rebuild the typed data (fresh nonce,
        clamped window, bound into the binding) and open the request — the claim's hold and
        reservation are untouched."""
        from core.wallet import x402 as wallet_x402
        from core.wallet import x402_v2

        binding = evm_binding_for(proposal.proposal_id) or {}
        if not binding:
            raise self._fault("wallet_signing_unavailable", proposal, reason="no_x402_binding")
        spec = chains.resolve_network(proposal.network)
        asset = chains.asset_for(spec.network, proposal.asset)
        entry = x402_v2.V2Requirements(
            scheme="exact", network=spec.network, amount_minor=proposal.amount_minor, asset=asset.address, pay_to=proposal.destination,
            resource_url=str(binding.get("url") or ""), resource_method=str(binding.get("resource_method") or "GET"),
            eip712_name=str(binding.get("eip712_name") or ""), eip712_version=str(binding.get("eip712_version") or ""),
            asset_transfer_method=str(binding.get("asset_transfer_method") or evm.TRANSFER_METHOD_EIP3009),
        )
        import os
        import time as _time

        name = entry.eip712_name or asset.eip712_name
        version = entry.eip712_version or asset.eip712_version
        if not name or not version:
            raise self._fault("x402_scheme_unavailable", proposal, reason="eip712_domain_unpinned")
        if asset.eip712_name and asset.eip712_version and (name != asset.eip712_name or version != asset.eip712_version):
            raise self._fault("x402_scheme_unavailable", proposal, reason="eip712_domain_conflicts_registry_pin")
        now = int(_time.time())
        nonce = "0x" + os.urandom(32).hex()
        window = min(max(60, int(binding.get("max_timeout_seconds") or 60)), config.x402_max_window_seconds())
        typed_data = evm.authorization_for_proposal(proposal, account=profile.public_key, nonce=nonce, valid_after=now - 60, valid_before=now + window, token_name=name, token_version=version)
        wallet_x402._update_binding(str(binding.get("request_digest") or ""), nonce=nonce, deadline=now + window, expires_at=now + window)
        try:
            record = evm.open_evm_signing_request(proposal_id=proposal.proposal_id, profile=profile, typed_data=typed_data, amount_minor=proposal.amount_minor, asset=proposal.asset)
        except Exception as exc:
            raise self._fault("wallet_signing_unavailable", proposal, reason=f"signing_request_failed:{type(exc).__name__}") from exc
        _remember_typed_data(proposal.proposal_id, record["request_id"], typed_data)
        proposals.transition(proposal.proposal_id, proposals.STATE_AWAITING_SIGNATURE, detail={"request_id": record["request_id"], "resumed": True})
        view = external_signing.request_view(record)
        view["resume"] = True
        return view

    def submit_external_signature(self, request_id: str, *, signature_b58: str = "", signed_transaction_b64: str = "", signature_hex: str = "") -> receipts.WalletReceipt:
        record = external_signing.get_signing_request(request_id)
        if record is None:
            raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "signing_request_unknown"}, source_context=self.source_context)
        proposal_for_freeze = proposals.get_proposal(record["proposal_id"])
        if proposal_for_freeze is not None:
            self._require_not_frozen(proposal_for_freeze, door="submission")
            self._require_signable_row(proposal_for_freeze)
        if record.get("family") == external_signing.FAMILY_EVM:
            return self._submit_evm_signature(record, signature_hex=signature_hex)
        proposal = self._load(record["proposal_id"])
        if record["state"] == external_signing.STATE_OPEN and external_signing.is_expired(record):
            self._expire_open_request_or_refuse(proposal, record, evidence="signing request expired")
        signature = external_signing.verify_submission(record, signature_b58=signature_b58, signed_transaction_b64=signed_transaction_b64)
        # The replay fence: compare-and-set open -> consumed. A replayed answer, however valid, loses here.
        external_signing.consume_signing_request(record["request_id"])
        raw = _serialize_message_and_signature(base64.b64decode(record["message_b64"]), signature)
        signed = proposals.transition(proposal.proposal_id, proposals.STATE_SIGNED, detail={"signer": custody.MODE_EXTERNAL_SIGNER, "request_id": record["request_id"]}, expected_state=proposals.STATE_AWAITING_SIGNATURE)
        if signed is None:
            raise self._fault("wallet_duplicate_payment", proposal, reason="proposal_not_awaiting_signature", status=proposal.state)
        sign_effect = self._open_effect(self._base(signed), effect_class=EFFECT_CLASS_SIGN)
        self._begin_attempt(sign_effect)
        self._close_effect(sign_effect, ok=True, reason="externally_signed")
        effect = self._open_effect(self._base(signed), effect_class=EFFECT_CLASS_TRANSACTION)
        return self._broadcast(signed, raw, effect)

    # -- stage 4: broadcast ----------------------------------------------------------------------------
    def _broadcast(self, proposal: proposals.TransactionProposal, raw: bytes, effect: Any) -> receipts.WalletReceipt:
        self._require_not_frozen(proposal, door="broadcast")
        base = self._base(proposal)
        spec = chains.resolve_network(proposal.network)
        try:
            rpc = self._rpc_for(spec.network)
            self._require_svm_identity(rpc, spec)
        except WalletFault as exc:
            # nothing left this process: the endpoint did not prove this row's chain, so the hold is released
            reason = f"endpoint_not_proven:{exc.context.get('reason') or exc.code}"
            limits.release_spend(proposal.proposal_id)
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=reason, source="mechanical")
            proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": reason}, fault_code=exc.code)
            receipts.journal_terminal({**base, "state": proposals.STATE_FAILED, "fault_code": exc.code}, source_context=self.source_context)
            self._cancel_effect(effect, reason=reason)
            receipts.record_receipt(proposal, state=proposals.STATE_FAILED, fault_code=exc.code)
            raise
        receipts.journal_intended({**base, "state": proposals.STATE_SIGNED}, source_context=self.source_context)
        self._begin_attempt(effect)
        try:
            tx_signature = redaction.publish_identifier(rpc.broadcast(raw))
        except Exception as exc:
            fate = classify_broadcast_failure(exc)
            if not fate.release:
                # no positive evidence of non-execution: UNKNOWN, the mirror of the EVM lane's
                # post-dispatch branch -- the hold stays, the effect stays unresolved, no blind retry.
                reason = f"broadcast_unknown:{fate.kind}"
                proposals.transition(proposal.proposal_id, proposals.STATE_BROADCAST, detail={"reason": reason, "settlement": "submitted_unknown"}, tx_signature="")
                self._close_effect(effect, ok=False, reason=reason)
                receipts.record_receipt(proposal, state=proposals.STATE_BROADCAST, extra={"settlement": "submitted_unknown"})
                raise self._fault("wallet_broadcast_failed", proposal, reason=reason) from None
            # positive evidence the transaction did not execute: the hold is released and the effect resolved
            reason = f"broadcast_{fate.kind}"
            limits.release_spend(proposal.proposal_id)
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=reason, source="provider")
            proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": reason}, fault_code="wallet_broadcast_failed")
            receipts.journal_terminal({**base, "state": proposals.STATE_FAILED, "fault_code": "wallet_broadcast_failed"}, source_context=self.source_context)
            self._close_effect(effect, ok=False, reason=reason)
            receipts.record_receipt(proposal, state=proposals.STATE_FAILED, fault_code="wallet_broadcast_failed")
            receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=False, status="failed")
            raise self._fault("wallet_broadcast_failed", proposal, reason=reason) from None
        limits.settle_spend(proposal.proposal_id)
        proposals.transition(proposal.proposal_id, proposals.STATE_BROADCAST, detail={"tx_signature": tx_signature}, tx_signature=tx_signature)
        confirmed = self._await_confirmation(tx_signature, rpc=rpc)
        final_state = proposals.STATE_CONFIRMED if confirmed else proposals.STATE_BROADCAST
        if confirmed:
            proposals.transition(proposal.proposal_id, proposals.STATE_CONFIRMED, detail={"tx_signature": tx_signature})
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=True, evidence=tx_signature, source="provider")
        receipts.journal_terminal({**base, "state": final_state, "tx_signature": tx_signature}, source_context=self.source_context)
        self._close_effect(effect, ok=True, reason=final_state)
        receipt = receipts.record_receipt(proposal, state=final_state, tx_signature=tx_signature)
        receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=True, status=final_state, tx_signature=tx_signature)
        return receipt

    def _await_confirmation(self, tx_signature: str, *, rpc: RpcClient | None = None) -> bool:
        deadline = time.monotonic() + _CONFIRM_BUDGET_SECONDS
        client = rpc or self.rpc
        while True:
            with contextlib.suppress(Exception):
                if client.confirm(tx_signature):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_CONFIRM_POLL_SECONDS)

    # -- the turn's effect ledger ------------------------------------------------------------------------
    def _open_effect(self, base: dict[str, Any], *, effect_class: str = EFFECT_CLASS_TRANSACTION) -> Any:
        """Register one contract effect with the turn's ledger BEFORE its execution. A gateway refusal propagates."""
        try:
            from core.effect_gateway import DECISION_ALLOWED, EffectReceipt, current_effect_ledger
        except Exception:
            return None
        ledger = current_effect_ledger()
        if ledger is None:
            return None
        return ledger.open_effect(EffectReceipt(effect_class=effect_class, decision=DECISION_ALLOWED, reason="wallet_owner_approved", host=str(base.get("host") or ""), decided_by=AUTHORITY, provider_id=str(base.get("network") or ""), keyed_or_keyless="keyed"))

    @staticmethod
    def _cancel_effect(effect: Any, *, reason: str) -> None:
        if effect is None:
            return
        with contextlib.suppress(Exception):
            effect.cancel(reason=reason)

    @staticmethod
    def _begin_attempt(effect: Any) -> None:
        if effect is None:
            return
        with contextlib.suppress(Exception):
            effect.begin_attempt()

    @staticmethod
    def _close_effect(effect: Any, *, ok: bool | None, reason: str) -> None:
        if effect is None or ok is None:
            return
        with contextlib.suppress(Exception):
            if ok:
                effect.succeed(status=reason)
            else:
                effect.fail(reason=reason)


    # -- the EVM family lanes ---------------------------------------------------------------------
    def _simulate_evm(self, spec: chains.ChainIdentity, proposal: proposals.TransactionProposal) -> SimulationResult:
        """The EVM 'simulation': prove the chain's identity with ONE bounded read, then the
        registry checks (registered EIP-3009 asset, provable transfer method, verified
        facilitator). Any refusal fails the proposal typed, before any approval exists."""
        from core.remote_fetch_policy import RemoteFetchRefusedError

        def probe(method: str):
            answer = outbound.rpc_call(chains.network_rpc_url(spec.network), payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": []}, spec=spec, timeout=15.0)
            return answer.get("result")

        try:
            chains.verify_chain_identity(spec, probe, scope="lifecycle")
        except WalletFault as exc:
            return SimulationResult(False, 0, f"chain_identity:{exc.context.get('reason', 'mismatch')}")
        except RemoteFetchRefusedError as exc:
            return SimulationResult(False, 0, f"chain_identity:{type(exc).__name__}")
        try:
            evm.require_evm_dependencies("simulate")
        except WalletFault:
            # the stack is genuinely absent: a TRUTHFUL capability state at the preparation
            # stage, so the offer never parks payable for a journey that cannot execute
            return SimulationResult(False, 0, "dependency:evm_stack_absent")
        try:
            asset = chains.asset_for(spec.network, proposal.asset)
        except WalletFault as exc:
            return SimulationResult(False, 0, f"asset:{exc.context.get('reason', 'unregistered')}")
        if not asset.address:
            return SimulationResult(False, 0, "asset:no_eip3009_token")
        binding = v2_binding_for(proposal.proposal_id)
        method = (binding or {}).get("asset_transfer_method") or evm.TRANSFER_METHOD_EIP3009
        try:
            evm.require_asset_transfer_method(spec.network, method)
        except WalletFault as exc:
            return SimulationResult(False, 0, f"transfer_method:{exc.context.get('reason', 'unavailable')}")
        try:
            facilitators.require_capability(spec.network, "exact", source_context=self.source_context)
        except WalletFault as exc:
            return SimulationResult(False, 0, f"facilitator:{exc.context.get('reason', 'none_verified')}")
        return SimulationResult(True, 0, "chain_identity_and_capability_verified")

    def _prepare_evm_pilot(self, spec: chains.ChainIdentity, proposal: proposals.TransactionProposal) -> SimulationResult:
        """The pilot preparation of a native EVM transfer: ONE bounded identity read on the row's endpoint, the EVM
        stack present, the asset the row's native coin. The fee is not known here; the quote prices it."""
        from core.remote_fetch_policy import RemoteFetchRefusedError

        def probe(method: str):
            answer = outbound.rpc_call(chains.network_rpc_url(spec.network), payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": []}, spec=spec, timeout=15.0)
            return answer.get("result")

        try:
            chains.verify_chain_identity(spec, probe, scope="lifecycle")
        except WalletFault as exc:
            return SimulationResult(False, 0, f"chain_identity:{exc.context.get('reason', 'mismatch')}")
        except RemoteFetchRefusedError as exc:
            return SimulationResult(False, 0, f"chain_identity:{type(exc).__name__}")
        try:
            evm.require_evm_dependencies("prepare")
        except WalletFault:
            return SimulationResult(False, 0, "dependency:evm_stack_absent")
        native = chains.native_asset(spec.network)
        if str(proposal.asset).upper() not in {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}:
            return SimulationResult(False, 0, "asset:native_coin_transfers_only")
        return SimulationResult(True, 0, "pilot_native_transfer_prepared")

    def _challenge_for(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile) -> approval_module.ApprovalChallenge:
        """The operator-visible challenge: EVERY cost-bearing fact bound, x402 fields from
        the binding record when one exists. A changed byte anywhere invalidates approval."""
        spec = chains.resolve_network(proposal.network)
        asset = chains.asset_for(spec.network, proposal.asset)
        decimals = asset.decimals
        human = _human_amount(proposal.amount_minor, decimals)
        binding = v2_binding_for(proposal.proposal_id) or {}
        facilitator_fee = _minor(binding.get("max_facilitator_fee_minor"))
        network_fee = _minor(binding.get("max_network_fee_minor"))
        sponsored = bool(binding.get("sponsored_gas"))
        max_total = proposal.amount_minor + (0 if sponsored else facilitator_fee + network_fee)
        challenge = approval_module.ApprovalChallenge(
            proposal.proposal_id, proposal.wallet_id, proposal.amount_minor, proposal.asset, proposal.destination, proposal.network,
            account_id=profile.public_key, chain=spec.network, scheme="exact", scheme_version=int(binding.get("version") or 1),
            asset_address=asset.address, asset_decimals=decimals, human_amount=human, payee=proposal.destination,
            resource_origin=str(binding.get("resource_origin") or ""), resource_method=str(binding.get("resource_method") or ""),
            facilitator=str(binding.get("facilitator_id") or ""), nonce=str(binding.get("nonce") or ""),
            deadline=_minor(binding.get("deadline")), expiry=_minor(binding.get("expires_at")),
            max_facilitator_fee_minor=0 if sponsored else facilitator_fee,
            max_network_fee_minor=0 if sponsored else network_fee,
            fee_asset=str(binding.get("fee_asset") or proposal.asset), sponsored_gas=sponsored,
            max_total_minor=max_total, idempotency_key=proposal.idempotency_key or proposal.proposal_id,
        )
        return challenge

    def _request_evm_signature(self, proposal: proposals.TransactionProposal, profile: custody.WalletProfile) -> dict[str, Any]:
        """The EVM external lane: claim, hold principal + fees, then hand out ONE EIP-1193
        eth_signTypedData_v4 request whose typed data binds the approved terms exactly."""
        import os
        import time as _time

        from core.wallet import x402_v2

        binding = evm_binding_for(proposal.proposal_id) or {}
        if not binding:
            raise self._fault("wallet_signing_unavailable", proposal, reason="no_x402_binding")
        spec = chains.resolve_network(proposal.network)
        asset = chains.asset_for(spec.network, proposal.asset)
        entry = x402_v2.V2Requirements(
            scheme="exact", network=spec.network, amount_minor=proposal.amount_minor, asset=asset.address, pay_to=proposal.destination,
            resource_url=str(binding.get("url") or ""), resource_method=str(binding.get("resource_method") or "GET"),
            eip712_name=str(binding.get("eip712_name") or ""), eip712_version=str(binding.get("eip712_version") or ""),
            asset_transfer_method=str(binding.get("asset_transfer_method") or evm.TRANSFER_METHOD_EIP3009),
        )
        name = entry.eip712_name or asset.eip712_name
        version = entry.eip712_version or asset.eip712_version
        if not name or not version:
            raise self._fault("x402_scheme_unavailable", proposal, reason="eip712_domain_unpinned")
        if asset.eip712_name and asset.eip712_version and (name != asset.eip712_name or version != asset.eip712_version):
            # belt-and-braces behind selection: the registry pin is the domain authority
            raise self._fault("x402_scheme_unavailable", proposal, reason="eip712_domain_conflicts_registry_pin", offered_name=name[:32], offered_version=version[:16])
        now = int(_time.time())
        nonce = "0x" + os.urandom(32).hex()
        # the validity window is LOCAL policy: the offer's maxTimeoutSeconds is a request,
        # and the signed authorization is a bearer instrument, so the window is clamped
        window = min(max(60, int(binding.get("max_timeout_seconds") or 60)), config.x402_max_window_seconds())
        valid_before = now + window
        typed_data = evm.authorization_for_proposal(
            proposal, account=profile.public_key, nonce=nonce, valid_after=now - 60,
            valid_before=valid_before,
            token_name=name, token_version=version,
        )
        # the exact instrument is persisted BEFORE the challenge is built, so the approval
        # digest binds the nonce and deadline the signer is actually handed
        from core.wallet import x402 as wallet_x402

        wallet_x402._update_binding(
            str(binding.get("request_digest") or ""), nonce=nonce, deadline=valid_before, expires_at=valid_before,
        )
        challenge = self._challenge_for(proposal, profile)
        fee_minor = _reserved_fee_for(challenge.binding_view())
        try:
            self._begin_attempt_evm_placeholder = None
            proposal, effects = self._claim(proposal, profile, method="external_signer", message_digest=evm.typed_data_digest(typed_data), fee_minor=fee_minor, chain=spec.network)
        except WalletFault:
            raise
        try:
            self._begin_attempt(effects["sign"])
            record = evm.open_evm_signing_request(proposal_id=proposal.proposal_id, profile=profile, typed_data=typed_data, amount_minor=proposal.amount_minor, asset=proposal.asset)
        except Exception as exc:
            self._close_effect(effects["sign"], ok=False, reason="signing_request_failed")
            self._abort_before_broadcast(proposal, {"transaction": effects["transaction"]}, state=proposals.STATE_FAILED, fault_code="wallet_signing_unavailable", reason=f"signing_request_failed:{type(exc).__name__}")
            raise self._fault("wallet_signing_unavailable", proposal, reason="signing_request_failed") from exc
        self._cancel_effect(effects["transaction"], reason="awaiting external signature; re-opened at submit")
        _remember_typed_data(proposal.proposal_id, record["request_id"], typed_data)
        proposals.transition(proposal.proposal_id, proposals.STATE_AWAITING_SIGNATURE, detail={"request_id": record["request_id"], "family": "evm"})
        return external_signing.request_view(record)

    def _submit_evm_signature(self, record: dict[str, Any], *, signature_hex: str = "") -> receipts.WalletReceipt:
        """Verify the external answer over the EXACT typed data, then submit ONCE: the
        PAYMENT-SIGNATURE header to the approved resource origin, settlement proven from the
        chain before the terminal receipt. A cancellation or bad answer is a refusal that
        burns nothing; an UNKNOWN settlement blocks blind retry."""
        import base64 as _b64
        import json as _json


        proposal = self._load(record["proposal_id"])
        if record["state"] == external_signing.STATE_OPEN and external_signing.is_expired(record):
            self._expire_open_request_or_refuse(proposal, record, evidence="evm signing request expired")
        spec = chains.resolve_network(proposal.network)
        evm.verify_evm_submission(record, signature_hex=signature_hex)
        # the replay fence: compare-and-set open -> consumed, same as the Solana lane
        external_signing.consume_signing_request(record["request_id"])
        typed_data = _typed_data_for(record["proposal_id"], record["request_id"])
        if typed_data is None:
            typed_data = _json.loads(_b64.b64decode(record["typed_data_b64"]).decode("utf-8"))
        signed = proposals.transition(proposal.proposal_id, proposals.STATE_SIGNED, detail={"signer": custody.MODE_EXTERNAL_SIGNER, "request_id": record["request_id"]}, expected_state=proposals.STATE_AWAITING_SIGNATURE)
        if signed is None:
            raise self._fault("wallet_duplicate_payment", proposal, reason="proposal_not_awaiting_signature", status=proposal.state)
        sign_effect = self._open_effect(self._base(signed), effect_class=EFFECT_CLASS_SIGN)
        self._begin_attempt(sign_effect)
        self._close_effect(sign_effect, ok=True, reason="externally_signed")
        effect = self._open_effect(self._base(signed), effect_class=EFFECT_CLASS_TRANSACTION)
        return self._submit_evm_payment(signed, record, typed_data, signature_hex, spec, effect)

    def _submit_evm_payment(self, proposal: proposals.TransactionProposal, record: dict[str, Any], typed_data: dict[str, Any], signature_hex: str, spec: chains.ChainIdentity, effect: Any) -> receipts.WalletReceipt:
        """One approved submission: header to the approved origin, then independent chain
        settlement verification. Exactly one attempt; UNKNOWN blocks a blind retry."""

        from core.wallet import x402_v2

        binding = evm_binding_for(proposal.proposal_id) or {}
        entry = x402_v2.V2Requirements(
            scheme="exact", network=spec.network, amount_minor=proposal.amount_minor, asset=str(binding.get("asset_address") or ""), pay_to=proposal.destination,
            resource_url=str(binding.get("url") or ""), resource_method=str(binding.get("resource_method") or "GET"),
            eip712_name=str(binding.get("eip712_name") or ""), eip712_version=str(binding.get("eip712_version") or ""),
            asset_transfer_method=str(binding.get("asset_transfer_method") or evm.TRANSFER_METHOD_EIP3009),
        )
        self._require_not_frozen(proposal, door="evm_submission")
        payment_origin = x402_v2.resource_origin_of(entry.resource_url)
        if int(binding.get("version") or 1) == 1:
            # the OFFICIAL v1 wire: X-PAYMENT carries the EIP-3009 authorization payload
            legacy_names = spec.legacy_names or (spec.network,)
            header = evm.v1_payment_header(typed_data, signature_hex=signature_hex, network_name=legacy_names[0])
            header_name, _response_name = "X-PAYMENT", "x-payment-response"
        else:
            payload = x402_v2.build_payment_payload_v2(entry, family=chains.FAMILY_EVM, account=record["public_key"], signature_hex=signature_hex, typed_data=typed_data)
            header = x402_v2.payment_header_v2(payload, resource_url=entry.resource_url)
            header_name, _response_name = x402_v2.HEADER_PAYMENT_SIGNATURE, x402_v2.HEADER_PAYMENT_RESPONSE.lower()
        base = self._base(proposal)
        receipts.journal_intended({**base, "state": proposals.STATE_SIGNED}, source_context=self.source_context)
        self._begin_attempt(effect)
        try:
            answer = outbound.fetch(
                entry.resource_url, method=str(binding.get("resource_method") or "GET"), headers={"Accept": "*/*"},
                payment_headers={header_name: header}, payment_origin=payment_origin, payment_redirect="refuse", timeout=20.0,
            )
        except WalletFault as exc:
            reason = f"submit_refused:{exc.code}"
            post_dispatch = exc.code == "wallet_outbound_refused" and str(exc.context.get("reason") or "") in _POST_DISPATCH_REFUSALS
            if post_dispatch:
                # the refusal happened AFTER the payment material left this machine (the
                # origin answered our payment-bearing request with a redirect, or its paid
                # body exceeded the byte cap): the authorization may settle with no local
                # receipt, so this is UNKNOWN — the hold stays, nothing releases, no retry.
                reason = f"submit_unknown:{exc.context.get('reason')}"
                proposals.transition(proposal.proposal_id, proposals.STATE_BROADCAST, detail={"reason": reason, "settlement": "submitted_unknown"}, tx_signature="")
                self._close_effect(effect, ok=False, reason=reason)
                receipts.record_receipt(proposal, state=proposals.STATE_BROADCAST, extra={"settlement": "submitted_unknown"})
                raise self._fault("wallet_broadcast_failed", proposal, reason=reason) from None
            limits.release_spend(proposal.proposal_id)
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=reason, source="mechanical")
            proposals.transition(proposal.proposal_id, proposals.STATE_FAILED, detail={"reason": reason}, fault_code=exc.code if exc.code == "wallet_outbound_refused" else "wallet_broadcast_failed")
            self._close_effect(effect, ok=False, reason=reason)
            receipts.record_receipt(proposal, state=proposals.STATE_FAILED, fault_code=exc.code)
            receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=False, status="failed")
            raise self._fault("wallet_broadcast_failed", proposal, reason=reason) from None
        except Exception as exc:  # transport UNKNOWN: the request may have arrived; never blind-retry
            reason = f"submit_unknown:{type(exc).__name__}"
            proposals.transition(proposal.proposal_id, proposals.STATE_BROADCAST, detail={"reason": reason, "settlement": "submitted_unknown"}, tx_signature="")
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=False, evidence=reason, source="provider")
            self._close_effect(effect, ok=False, reason=reason)
            receipts.record_receipt(proposal, state=proposals.STATE_BROADCAST, extra={"settlement": "submitted_unknown"})
            raise self._fault("wallet_broadcast_failed", proposal, reason=reason) from None
        from core.wallet import x402 as wallet_x402

        answer_headers = answer.get("headers") or {}
        settlement = x402_v2.parse_settlement_response(answer_headers.get(x402_v2.HEADER_PAYMENT_RESPONSE.lower()))
        if settlement is None:
            settlement = wallet_x402.parse_settlement_response_v1(answer_headers.get("x-payment-response"))
        tx_hash = str((settlement or {}).get("transaction") or "")
        limits.settle_spend(proposal.proposal_id)
        proposals.transition(proposal.proposal_id, proposals.STATE_BROADCAST, detail={"settlement": "submitted", "resource_status": answer["status"]}, tx_signature=tx_hash)
        settled_state, verified = self._verify_evm_settlement(spec, proposal, binding, tx_hash, payer=str(record.get("public_key") or ""))
        final_state = proposals.STATE_CONFIRMED if settled_state == "settled" else proposals.STATE_BROADCAST
        if final_state == proposals.STATE_CONFIRMED:
            proposals.transition(proposal.proposal_id, proposals.STATE_CONFIRMED, detail={"tx_signature": tx_hash, "settlement": settled_state}, tx_signature=tx_hash)
            reconciliation.resolve_payment_effect(proposal.proposal_id, applied=True, evidence=tx_hash, source="provider")
        receipts.journal_terminal({**base, "state": final_state, "tx_signature": redaction.publish_identifier(tx_hash)}, source_context=self.source_context)
        self._close_effect(effect, ok=final_state == proposals.STATE_CONFIRMED, reason=final_state)
        receipt = receipts.record_receipt(proposal, state=final_state, tx_signature=tx_hash, extra={"x402_v2": {"resource_status": answer["status"], "settlement": settled_state, "verified_terms": verified, "resource_digest": hashlib.sha256(answer["body"]).hexdigest(), "resource_bytes": len(answer["body"]), "settlement_delivered": final_state == proposals.STATE_CONFIRMED}})
        receipts.register_execution(source_context=self.source_context, proposal=proposal, ok=final_state == proposals.STATE_CONFIRMED, status=final_state, tx_signature=tx_hash)
        return receipt

    def _verify_evm_settlement(self, spec: chains.ChainIdentity, proposal: proposals.TransactionProposal, binding: dict[str, Any], tx_hash: str, *, payer: str = "") -> tuple[str, bool]:
        """Independent settlement proof from the chain itself: the receipt's USDC Transfer
        log must come FROM our payer, pay the approved payee at least the approved amount
        under the approved token. Returns (settlement_state, terms_ok)."""
        settled_state, terms_ok = evm.verify_settlement(spec, tx_hash, asset_address=str(binding.get("asset_address") or ""), pay_to=proposal.destination, amount_minor=proposal.amount_minor, payer=payer)
        return settled_state, terms_ok


# --- module-level helpers for the multichain lanes ---------------------------------------------------

#: txpool answers that mean the node did not accept the bytes this time (geth/nethermind/erigon/reth wording);
#: anything else validated is an error that may still let the transaction through
_EVM_REFUSAL_TEXTS = (
    "insufficient funds", "intrinsic gas too low", "max fee per gas less than block base fee", "invalid chain id", "invalid sender",
    "nonce too high", "exceeds block gas limit", "txpool is full", "transaction underpriced", "gas limit reached", "fee cap less than block base fee",
)


def capabilities_reason_incomplete() -> str:
    from core.wallet import capabilities

    return capabilities.REASON_PILOT_LANE_INCOMPLETE


def spec_network(network: str) -> str:
    return chains.resolve_network(network).network


def _human_amount(amount_minor: int, decimals: int) -> str:
    try:
        from decimal import Decimal

        value = Decimal(int(amount_minor)) / (Decimal(10) ** int(decimals))
        return format(value.normalize(), "f")
    except Exception:
        return ""


def _minor(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def v2_binding_for(proposal_id: str) -> dict[str, Any] | None:
    """The v2 x402 binding record for this proposal, or None. A v1 binding is NOT a v2
    binding: the lanes never blur."""
    from core.wallet import x402 as wallet_x402

    binding = wallet_x402.binding_for_proposal(str(proposal_id))
    if binding and int(binding.get("version") or 1) == 2:
        return binding
    return None


def evm_binding_for(proposal_id: str) -> dict[str, Any] | None:
    """The x402 binding for this proposal when it rides an EVM network — v1 OR v2 wire.
    The EVM signing/settlement engine is one engine; the wire version is a binding fact."""
    from core.wallet import chains
    from core.wallet import x402 as wallet_x402

    binding = wallet_x402.binding_for_proposal(str(proposal_id))
    if not binding:
        return None
    try:
        if chains.resolve_network(str(binding.get("network") or "")).is_evm:
            return binding
    except Exception:
        return None
    return None


def _reserved_fee_for(view: dict[str, Any]) -> int:
    """Every non-sponsored maximum fee: sponsored gas is reported truthfully as zero."""
    if view.get("sponsored_gas"):
        return 0
    return _minor(view.get("max_facilitator_fee_minor")) + _minor(view.get("max_network_fee_minor"))


def _svm_fee_for(proposal: proposals.TransactionProposal) -> int:
    """The Solana lane pays its own transaction fee at broadcast: the simulation's fee is a
    reserved cost, not a surprise. Zero on the EVM family (the facilitator settles there)."""
    try:
        if chains.resolve_network(proposal.network).is_evm:
            return 0
        return max(0, int((proposal.simulation or {}).get("fee_minor") or 0))
    except Exception:
        return 0


#: proposal_id -> (request_id -> typed data) for the EVM lane. The typed data is exactly
#: what the request stored; this in-process map only spares a re-parse, and the record's
#: stored bytes remain the verification source.
_TYPED_DATA_MEMORY: dict[tuple[str, str], dict[str, Any]] = {}


def _remember_typed_data(proposal_id: str, request_id: str, typed_data: dict[str, Any]) -> None:
    _TYPED_DATA_MEMORY[(str(proposal_id), str(request_id))] = typed_data


def _typed_data_for(proposal_id: str, request_id: str) -> dict[str, Any] | None:
    return _TYPED_DATA_MEMORY.get((str(proposal_id), str(request_id)))


def default_lifecycle(*, source_context: dict[str, Any] | None = None) -> PaymentLifecycle:
    return PaymentLifecycle(rpc=RpcClient(config.testnet_rpc_url(), network=config.NETWORK_SOLANA_DEVNET), source_context=source_context)


RpcBroadcaster = RpcClient  # the broadcaster IS the row-bound client; one construction-time origin refusal
