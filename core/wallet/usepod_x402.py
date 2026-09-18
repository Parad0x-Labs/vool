"""The wallet's x402 payment authority for UsePod: the Crypto Pilot lane pays one accountless UsePod request.

Installed once at daemon startup (:func:`install_at_boot`) under ``core.wallet.usepod_x402:v1``. The provider transport
(:mod:`core.usepod.transport`) asks it for the payer's facts BEFORE any reservation, and for a payment proof only after
the money law has reserved and claimed the operation. A proof exists only for a confirmed principal:

1. the quoted option becomes ONE Crypto Pilot proposal under the operation id (:func:`core.wallet.usepod.validate_x402_payment`);
2. the owner approves it where every pilot transfer is approved -- the chat card and approval sheet, the existing PIN or
   password or device policy -- and the lane claims, signs once, sends once and observes it;
3. the operation record says ``proof_eligible`` (a confirmed principal), and the proof carries that transaction signature.
4. after the provider's settlement, :meth:`WalletX402PaymentAuthority.chain_confirmation` reads the confirmed principal and
   the fee that transaction charged back from the transfer, for the money law's chain evidence.

The owner rejecting, cancelling or letting the approval window close before anything is signed is a typed refusal, and
the transport records the reservation as never sent. A payment still being signed, sent or confirmed when the window
closes is an unknown outcome: the transport retains the liability, and the same operation id finds the same proposal
later -- never a second payment. Nothing here signs, holds a key, keeps a second approval authority, retries a payment
or converts between assets.
"""
from __future__ import annotations

import os
import time
from typing import Any

AUTHORITY_LABEL = "core.wallet.usepod_x402:v1"
PROVIDER = "usepod"
APPROVAL_WINDOW_ENV = "VOOL_USEPOD_X402_APPROVAL_SECONDS"
DEFAULT_APPROVAL_WINDOW_SECONDS = 300.0
_POLL_SECONDS = 0.5
#: Signing, sending, seeing confirmation and delivering the settle request all happen after the owner's approval;
#: the approval window therefore closes this long before the provider's own quote expiry.
QUOTE_EXPIRY_MARGIN_SECONDS = 15.0
#: The networks UsePod documents for x402 (docs.usepod.ai/api/x402-payments): Solana Mainnet only. A Test-networks
#: selection therefore verifies none -- Mainnet is never substituted for another environment.
DOCUMENTED_NETWORKS: tuple[str, ...] = ("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",)
#: The transport's atomic unit for each asset it may quote (core.usepod.transport.ATOMIC_UNIT_BY_ASSET), compared exactly.
_UNIT_BY_ASSET = {"USDC": "usdc_microunit", "SOL": "lamport"}
#: Operation states with nothing signed or sent: a closed window may end these as a refusal.
_NOTHING_SIGNED = frozenset({"", "minting", "proposed", "expired", "released", "revoked"})


class X402PaymentOutcomeUnknown(RuntimeError):
    """A payment may have left the wallet and is not proven either way: the transport retains the liability."""


def approval_window_seconds() -> float:
    raw = str(os.environ.get(APPROVAL_WINDOW_ENV) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_APPROVAL_WINDOW_SECONDS
    except ValueError:
        value = DEFAULT_APPROVAL_WINDOW_SECONDS
    return min(max(value, 1.0), 3600.0)


def payment_deadline(created_at: float, window_seconds: float, quote_expires_at: float | None) -> float:
    """When the owner's approval must have happened: the configured window from the sealed envelope, never later
    than the quote's own expiry less the margin the payment itself needs."""
    deadline = float(created_at) + float(window_seconds)
    if quote_expires_at is not None:
        deadline = min(deadline, float(quote_expires_at) - QUOTE_EXPIRY_MARGIN_SECONDS)
    return deadline


def _refused(code: str, detail: str = "") -> Exception:
    from core.usepod.transport import PaymentAuthorityRefusedError

    return PaymentAuthorityRefusedError(code, detail)


def pilot_wallets(network: str) -> list[dict[str, Any]]:
    """Ready Crypto Pilot wallets on this exact row (setup finished, pilot seal)."""
    from core.wallet import chains, custody

    canonical = chains.resolve_network(network).network
    found = []
    for entry in custody.list_wallets():
        try:
            if chains.resolve_network(entry.get("network") or "").network != canonical:
                continue
        except Exception:
            continue
        if str(entry.get("seal_policy") or "") != custody.PILOT_SEAL_POLICY or str(entry.get("setup_state") or "") not in {"", "ready"}:
            continue
        found.append(entry)
    return found


def payer_wallet(network: str) -> dict[str, Any]:
    """The one wallet that pays on this row: its only ready pilot wallet, or the owner's default among several."""
    from core.wallet import custody

    wallets = pilot_wallets(network)
    if not wallets:
        raise _refused("x402_no_ready_pilot_wallet_on_network", network)
    if len(wallets) > 1:
        default = custody.default_wallet()
        wallets = [entry for entry in wallets if default is not None and entry.get("wallet_id") == default.wallet_id]
        if len(wallets) != 1:
            raise _refused("x402_payer_ambiguous", "several ready pilot wallets on this network and none is the default")
    return wallets[0]


def _address(entry: dict[str, Any]) -> str:
    return str(entry.get("public_key") or entry.get("address") or "")


def _rpc(network: str) -> Any:
    from core.wallet import chains, lifecycle

    return lifecycle.RpcClient(chains.network_rpc_url(network), network=network)


def verified_networks() -> tuple[str, ...]:
    """The documented UsePod x402 networks this wallet can pay on now: Crypto is on, the row belongs to the active
    environment and is ready for pilot transfers, a ready pilot wallet exists there, and the row's endpoint proved its
    genesis (one read, cached per endpoint)."""
    from core.wallet import capabilities, chains, config, environment

    try:
        if not config.wallet_enabled():
            return ()
        active = environment.active_environment().environment
    except Exception:
        return ()
    verified: list[str] = []
    for network in DOCUMENTED_NETWORKS:
        try:
            spec = chains.resolve_network(network)
            if spec.environment != active or not capabilities.pilot_transfer_ready(spec) or not pilot_wallets(spec.network):
                continue
            rpc = _rpc(spec.network)
            chains.verify_chain_identity(spec, lambda method, rpc=rpc: rpc._call(method, []), scope=chains.endpoint_scope("usepod_x402", rpc.url))
        except Exception:
            continue
        verified.append(spec.network)
    return tuple(verified)


def _fee_for_message_shape(rpc: Any, spec: Any, payer: str, asset: str) -> int:
    """The fee of a one-signature transfer of this asset from this payer, as the row prices that message now."""
    import base64

    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    from core.wallet import chains, svm_tokens

    blockhash = rpc.latest_blockhash()
    if asset == chains.native_asset(spec.network).symbol:
        message = Message.new_with_blockhash([transfer(TransferParams(from_pubkey=Pubkey.from_string(payer), to_pubkey=Pubkey.from_string(payer), lamports=1))], Pubkey.from_string(payer), Hash.from_string(blockhash))
    else:
        message = svm_tokens.build_transfer_message(payer=payer, recipient_owner=payer, asset=svm_tokens.token_asset(spec.network, asset), amount_minor=1, blockhash=blockhash)
    answer = rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode("ascii"), {"commitment": "confirmed"}])
    fee = (answer or {}).get("value") if isinstance(answer, dict) else None
    if fee is None:
        raise _refused("x402_fee_unknown", spec.network)
    return int(fee)


class WalletX402PaymentAuthority:
    """The Crypto Pilot wallet as UsePod's x402 payment authority (see the module docstring)."""

    label = AUTHORITY_LABEL

    @property
    def networks(self) -> tuple[str, ...]:
        return verified_networks()

    def wallet_facts(self, *, network: str, asset: str, atomic_unit: str) -> Any:
        """Payer identity, the fee asset and ceiling in SOL, and both balances net of what other in-flight transfers
        hold. A SOL payment names its fee in SOL too, so the money law checks principal and fee against one balance."""
        from core.usepod.transport import X402WalletFacts
        from core.wallet import chains, quotes, svm_tokens

        clean_asset = str(asset or "").strip().upper()
        if str(network) not in self.networks:
            raise _refused("x402_network_not_verified_by_wallet", str(network))
        if _UNIT_BY_ASSET.get(clean_asset) != str(atomic_unit or ""):
            raise _refused("x402_unit_does_not_match_asset", f"{clean_asset}:{atomic_unit}")
        spec = chains.resolve_network(network)
        native = chains.native_asset(spec.network)
        wallet = payer_wallet(spec.network)
        payer, wallet_id = _address(wallet), str(wallet["wallet_id"])
        rpc = _rpc(spec.network)
        answer = rpc._call("getBalance", [payer, {"commitment": "confirmed"}])
        lamports = (answer or {}).get("value") if isinstance(answer, dict) else None
        if lamports is None:
            raise _refused("x402_balance_unknown", spec.network)
        fee_balance = int(lamports) - quotes._held_elsewhere(wallet_id, spec.network, "", asset=native.symbol)
        fee_max = _fee_for_message_shape(rpc, spec, payer, clean_asset)
        if clean_asset == native.symbol:
            principal = fee_balance
        elif svm_tokens.is_token_transfer(spec.network, clean_asset):
            token = svm_tokens.token_asset(spec.network, clean_asset)
            svm_tokens.require_mint(rpc, token)
            principal = svm_tokens.token_balance_minor(rpc, owner=payer, asset=token) - quotes._held_elsewhere(wallet_id, spec.network, "", asset=token.symbol)
        else:
            raise _refused("x402_asset_not_registered_on_row", clean_asset)
        return X402WalletFacts(
            payer_account=payer, fee_network=spec.network, fee_asset=native.symbol, fee_decimals=native.decimals, fee_max_atomic=fee_max,
            principal_balance_atomic=max(0, principal), fee_balance_atomic=max(0, fee_balance),
        )

    def obtain_proof(self, *, quote: Any, option: Any, envelope: Any, liability: Any, reservation: Any) -> Any:
        from core.usepod.transport import X402PaymentProof
        from core.wallet import usepod
        from core.wallet.errors import WalletFault

        if str(option.network) not in self.networks:
            raise _refused("x402_network_not_verified_by_wallet", str(option.network))
        wallet = payer_wallet(str(option.network))
        payer = _address(wallet)
        if payer != str(liability.account_ref):
            raise _refused("x402_payer_changed_since_reservation")
        window = approval_window_seconds()
        requirement = usepod.parse_requirement({
            "correlation_id": str(envelope.operation_id), "provider": PROVIDER, "network": str(option.network), "asset": str(option.asset),
            "pay_to": str(option.pay_to), "amount_minor": int(option.amount_atomic or 0),
            # derived from the sealed envelope and the quote, so a replay of the same operation repeats the same
            # requirement exactly; the owner cannot approve a payment the provider would no longer honour
            "expires_at": payment_deadline(envelope.created_at, window, getattr(option, "expires_at_epoch", None)),
            "resource": f"{envelope.origin}{envelope.path_template}", "payer_wallet": str(wallet["wallet_id"]),
        })
        try:
            usepod.validate_x402_payment(requirement)
        except WalletFault as exc:
            # refused before any approval or signature: the reservation is released as never sent
            raise _refused(exc.code, str(exc.context.get("reason") or "")) from None
        while True:
            record = usepod.status_for(requirement.correlation_id, provider=PROVIDER) or {}
            operation = record.get("operation") or {}
            state = str(operation.get("state") or "")
            if operation.get("proof_eligible"):
                signature = str((record.get("transfer") or {}).get("tx_id") or "")
                return X402PaymentProof(
                    operation_id=str(envelope.operation_id), envelope_binding_sha256=envelope.binding_sha256, quote_header_sha256=quote.header_sha256,
                    quote_id=quote.quote_id, network=str(option.network), asset=str(option.asset), pay_to=str(option.pay_to),
                    amount_atomic=int(option.amount_atomic or 0), payer_wallet=payer, signature=signature, authority_label=self.label, issued_at=time.time(),
                )
            proposal_state = str(record.get("proposal_state") or "")
            if proposal_state in {"rejected", "cancelled"} or state in {"released", "revoked"}:
                raise _refused("wallet_approval_rejected", proposal_state or state)
            if state == "failed" or proposal_state == "failed":
                # a failed execution may have spent a network fee: held for reconciliation, never released as unsent
                raise X402PaymentOutcomeUnknown(f"payment_failed_on_chain:{requirement.correlation_id}")
            if time.time() >= requirement.expires_at:
                if state in _NOTHING_SIGNED:
                    proposal_id = str(record.get("proposal_id") or "")
                    if proposal_id:
                        usepod.mark_expired(proposal_id, detail="x402_approval_window_closed", expire_proposal=True)
                    raise _refused("x402_approval_window_closed", requirement.correlation_id)
                raise X402PaymentOutcomeUnknown(f"payment_in_flight_at_deadline:{state}")
            time.sleep(_POLL_SECONDS)

    def chain_confirmation(self, *, proof: Any) -> Any:
        """The chain's own record of a proven payment, from the pilot transfer the proof names: the principal the operation
        moved and the fee the transaction charged (``None`` until recorded). None when the operation is not proof-eligible
        or the proof names another transaction."""
        from core.usepod.transport import X402ChainConfirmation
        from core.wallet import chains, usepod

        record = usepod.status_for(str(proof.operation_id), provider=PROVIDER) or {}
        operation = record.get("operation") or {}
        transfer = record.get("transfer") or {}
        if not operation.get("proof_eligible") or not proof.signature or str(transfer.get("tx_id") or "") != str(proof.signature):
            return None
        charged = transfer.get("charged_fee_minor")
        return X402ChainConfirmation(
            signature=str(proof.signature),
            wallet_outflow_atomic=int(record["amount_minor"]),
            network_fee_atomic=None if charged in (None, "") else int(charged),
            fee_asset=chains.native_asset(str(record["network"])).symbol,
        )


    def service_fee_collection_outcome(self, *, proof: Any) -> dict[str, Any]:
        """After the provider's settlement of a PAID operation: what happened to the collection of previously accrued
        DNA fees that was planned to ride THIS payment's approval (collected, in flight, failed, deferred), for the
        receipt. A read: nothing is offered, signed or sent here. Failures are reported, never raised into the
        provider call that just completed."""
        from core.wallet import dna_fees, usepod

        record = usepod.status_for(str(proof.operation_id), provider=PROVIDER) or {}
        proposal_id = str(record.get("proposal_id") or "")
        if not proposal_id:
            return {"planned": False, "reason": "operation_not_paid"}
        try:
            outcome = dna_fees.companion_outcome(proposal_id)
        except Exception as exc:
            return {"planned": False, "reason": f"outcome_unreadable:{getattr(exc, 'code', '') or type(exc).__name__}"}
        if outcome is None:
            return {"planned": False, "reason": "not_planned_with_this_payment"}
        return {"planned": True, **outcome}


def install_at_boot() -> str:
    """Install the wallet authority for the UsePod x402 lane (idempotent); returns its label."""
    from core.usepod.transport import install_payment_authority

    authority = WalletX402PaymentAuthority()
    install_payment_authority(authority, label=authority.label)
    return authority.label


__all__ = [
    "AUTHORITY_LABEL", "APPROVAL_WINDOW_ENV", "DOCUMENTED_NETWORKS", "QUOTE_EXPIRY_MARGIN_SECONDS", "WalletX402PaymentAuthority",
    "X402PaymentOutcomeUnknown", "approval_window_seconds", "install_at_boot", "payer_wallet", "payment_deadline", "pilot_wallets",
    "verified_networks",
]
