"""A6 wiring for payments: the logical effect `wallet.pay` is reserved before the key is touched
and resolved from the chain, never from the model. The registered resolver answers a stranded
UNKNOWN from the proposal row plus an RPC signature-status probe."""
from __future__ import annotations

import contextlib
import json
from typing import Any

from core.effect_reconciliation import EffectResolution, ResolutionOutcome, register_effect_resolver
from core.runtime_continuity import compute_logical_effect_id, reserve_logical_effect, resolve_unresolved_effect
from core.wallet import proposals

EFFECT_INTENT = "wallet.pay"
RESOLUTION_APPLIED = "CONFIRMED_APPLIED"
RESOLUTION_FAILED_SAFE = "CONFIRMED_FAILED_SAFE_TO_RETRY"


def logical_effect_id(proposal_id: str) -> str:
    return compute_logical_effect_id(intent=EFFECT_INTENT, arguments={"proposal_id": str(proposal_id)})


def reserve_payment_effect(proposal: proposals.TransactionProposal, *, message_digest: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    context = source_context if isinstance(source_context, dict) else {}
    return reserve_logical_effect(
        intent=EFFECT_INTENT,
        arguments={"proposal_id": proposal.proposal_id},
        resource_identity=proposal.proposal_id,
        expected_evidence={"proposal_id": proposal.proposal_id, "message_digest": message_digest, "network": proposal.network},
        session_id=str(context.get("session_id") or ""),
        turn_id=str(context.get("turn_id") or ""),
        reconcilability="reconcilable",
    )


def resolve_payment_effect(proposal_id: str, *, applied: bool, evidence: str, source: str) -> None:
    with contextlib.suppress(Exception):
        resolve_unresolved_effect(logical_effect_id=logical_effect_id(proposal_id), resolution=RESOLUTION_APPLIED if applied else RESOLUTION_FAILED_SAFE, source=source, evidence=str(evidence or ""), resolved_by="core.wallet.lifecycle")


def _expected(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("expected_evidence")
    if raw is None:
        raw = row.get("expected_evidence_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _payment_resolver(row: dict[str, Any]) -> EffectResolution:
    proposal_id = str(_expected(row).get("proposal_id") or row.get("resource_identity") or "")
    proposal = proposals.get_proposal(proposal_id) if proposal_id else None
    if proposal is None:
        return EffectResolution(outcome=ResolutionOutcome.STILL_UNKNOWN, source="mechanical", evidence="proposal row not found")
    from core.wallet import transfers

    transfer = transfers.get_transfer_by_id(proposal_id)
    if transfer is not None:
        # a Crypto Pilot transfer: the transfer row is the evidence, written only from the chain or a never-sent proof
        state = str(transfer["state"])
        if state == transfers.STATE_CONFIRMED:
            return EffectResolution(outcome=ResolutionOutcome.APPLIED, source="provider", evidence=str(transfer.get("tx_id") or ""))
        if state == transfers.STATE_FAILED_ON_CHAIN:
            return EffectResolution(outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY, source="provider", evidence=f"failed on chain: {transfer.get('tx_id') or ''}")
        if state in transfers.NEVER_SENT_STATES:
            return EffectResolution(outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY, source="mechanical", evidence=f"transfer {state}: the bytes never left")
        return EffectResolution(outcome=ResolutionOutcome.STILL_UNKNOWN, source="mechanical", evidence=f"transfer {state}")
    if proposal.tx_signature:
        from core.wallet import chains
        from core.wallet.lifecycle import RpcClient

        try:
            # reconciled on the proposal's own row, whatever network environment is active now
            confirmed = RpcClient(chains.network_rpc_url(proposal.network), network=proposal.network).confirm(proposal.tx_signature)
        except Exception:
            confirmed = False
        if confirmed:
            return EffectResolution(outcome=ResolutionOutcome.APPLIED, source="provider", evidence=proposal.tx_signature)
        return EffectResolution(outcome=ResolutionOutcome.STILL_UNKNOWN, source="provider", evidence=f"signature not confirmed yet: {proposal.tx_signature}")
    if proposal.state in (proposals.STATE_FAILED, proposals.STATE_REJECTED, proposals.STATE_EXPIRED):
        return EffectResolution(outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY, source="mechanical", evidence=f"proposal terminal without broadcast: {proposal.state}")
    return EffectResolution(outcome=ResolutionOutcome.STILL_UNKNOWN, source="mechanical", evidence=f"proposal in flight: {proposal.state}")


register_effect_resolver(EFFECT_INTENT, _payment_resolver)
