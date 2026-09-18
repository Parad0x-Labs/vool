"""Approval: a typed challenge bound to ONE proposal, answered by a PIN or a biometric seam.

The challenge digest binds EVERY cost-bearing fact the operator is shown: the proposal and
wallet identity, the account, the canonical chain and its verified identity, the scheme and
version, the exact asset (address/mint, symbol, decimals), the principal in atomic and
human units, the payee, the resource origin and method, the facilitator, the nonce,
deadline and expiry, the maximum facilitator fee, the maximum network fee (or the truthful
sponsored status), the maximum total, and the idempotency/payment identifier. Any changed
field — by a byte — invalidates the approval: a decision answers ONE challenge and nothing
else. The biometric seam is an abstract class the platform layer implements; this build
ships the unavailable default, which approves nothing.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

METHOD_PIN = "pin"
METHOD_PASSWORD = "password"
METHOD_DEVICE = "device"
METHOD_BIOMETRIC = "biometric"

#: Digest domain tag: a challenge digest is never valid as any other hash.
_DIGEST_DOMAIN = "wallet-approval-challenge-v2"


@dataclass(frozen=True)
class ApprovalChallenge:
    # legacy positional core (the finished Solana lane constructs these positionally)
    proposal_id: str
    wallet_id: str
    amount_minor: int
    asset: str
    destination: str
    network: str
    # the full multichain binding; every field participates in the digest
    account_id: str = ""
    chain: str = ""
    scheme: str = "exact"
    scheme_version: int = 1
    asset_address: str = ""
    asset_decimals: int = 0
    human_amount: str = ""
    payee: str = ""
    resource_origin: str = ""
    resource_method: str = ""
    facilitator: str = ""
    nonce: str = ""
    deadline: int = 0
    expiry: int = 0
    max_facilitator_fee_minor: int = 0
    max_network_fee_minor: int = 0
    fee_asset: str = ""
    sponsored_gas: bool = False
    max_total_minor: int = 0
    idempotency_key: str = ""

    def _canonical_fields(self) -> list[str]:
        return [
            _DIGEST_DOMAIN,
            str(self.proposal_id), str(self.wallet_id), str(self.account_id),
            str(self.chain or self.network), str(self.network),
            str(self.scheme), str(self.scheme_version),
            str(self.asset_address or self.asset), str(self.asset), str(self.asset_decimals),
            str(int(self.amount_minor)), str(self.human_amount),
            str(self.destination), str(self.payee or self.destination),
            str(self.resource_origin), str(self.resource_method),
            str(self.facilitator), str(self.nonce), str(int(self.deadline)), str(int(self.expiry)),
            str(int(self.max_facilitator_fee_minor)), str(int(self.max_network_fee_minor)),
            str(self.fee_asset), "1" if self.sponsored_gas else "0",
            str(int(self.max_total_minor)), str(self.idempotency_key),
        ]

    @property
    def digest(self) -> str:
        canonical = "|".join(self._canonical_fields())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def binding_view(self) -> dict[str, Any]:
        """The public-safe card of everything this approval would bind. Never a secret."""
        return {
            "proposal_id": self.proposal_id, "wallet_id": self.wallet_id, "account_id": self.account_id,
            "chain": self.chain or self.network, "network": self.network, "scheme": self.scheme,
            "scheme_version": self.scheme_version, "asset": self.asset, "asset_address": self.asset_address,
            "asset_decimals": self.asset_decimals, "amount_minor": self.amount_minor, "human_amount": self.human_amount,
            "destination": self.destination, "payee": self.payee, "resource_origin": self.resource_origin,
            "resource_method": self.resource_method, "facilitator": self.facilitator, "nonce": self.nonce,
            "deadline": self.deadline, "expiry": self.expiry,
            "max_facilitator_fee_minor": self.max_facilitator_fee_minor,
            "max_network_fee_minor": self.max_network_fee_minor, "fee_asset": self.fee_asset,
            "sponsored_gas": self.sponsored_gas, "max_total_minor": self.max_total_minor,
            "idempotency_key": self.idempotency_key, "challenge_digest": self.digest,
        }

    def summary(self) -> str:
        human = self.human_amount or f"{self.amount_minor} minor units"
        where = f" on {self.chain or self.network}" + (f" via {self.resource_origin}" if self.resource_origin else "")
        return f"Pay {human} {self.asset} to {self.destination}{where}"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    method: str
    challenge_digest: str
    #: For pocket wallets the approval also unlocks the seal; external signers ignore it. Never persisted.
    pin_unlock: str = ""
    reason: str = ""


class Approver(Protocol):
    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision: ...


class PinApprover:
    def __init__(self, pin: str) -> None:
        self._pin = str(pin or "")

    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision:
        return ApprovalDecision(approved=bool(self._pin), method=METHOD_PIN, challenge_digest=challenge.digest, pin_unlock=self._pin)


class PasswordApprover:
    """A wallet-only password (10+ characters) instead of a PIN; `pin_unlock` carries the secret string."""

    def __init__(self, password: str) -> None:
        self._password = str(password or "")

    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision:
        return ApprovalDecision(approved=bool(self._password), method=METHOD_PASSWORD, challenge_digest=challenge.digest, pin_unlock=self._password)


class DeviceApprover:
    """Touch ID or the Mac password: the decision binds the challenge; the prompt itself happens at signing time,
    when the device path opens the seal -- a cancelled prompt is a counted refusal, never a failed payment."""

    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision:
        return ApprovalDecision(approved=True, method=METHOD_DEVICE, challenge_digest=challenge.digest, pin_unlock="")


class BiometricApprover(ABC):
    """Platform seam: Touch ID / Face ID / Windows Hello implement this; the runtime never sees biometrics."""

    @abstractmethod
    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision: ...


class UnavailableBiometric(BiometricApprover):
    def approve(self, challenge: ApprovalChallenge) -> ApprovalDecision:
        return ApprovalDecision(approved=False, method=METHOD_BIOMETRIC, challenge_digest=challenge.digest, reason="biometric_unavailable")


_DIGEST_DOMAIN_V3 = "wallet-approval-challenge-v3"


@dataclass(frozen=True)
class ApprovalChallengeV3:
    """A Crypto Pilot transfer approval: everything the sheet showed, bound to one quote. A v2 challenge never
    satisfies it and it never satisfies a v2 challenge (different domain, different field list)."""

    proposal_id: str
    wallet_id: str
    network: str
    environment: str
    chain_identity: str
    account_id: str
    destination: str
    asset: str
    amount_minor: int
    human_amount: str
    quote_id: str
    quote_digest: str
    fee_model: str
    fee_max_minor: int
    gas_asset: str
    max_total_minor: int
    quote_expires_at: float
    idempotency_key: str = ""
    #: the exact finite set of actions this approval covers when the quote composes more than one transaction
    #: (a provider payment and the collection of accrued DNA fees riding it): kind, proposal, recipient, amount,
    #: asset, network-fee ceiling and, for a collection, its ledger identity and version. Empty for one transfer,
    #: so a single-transfer digest is exactly what it was.
    action_set: str = ""

    @classmethod
    def for_quote(cls, proposal: Any, quote: dict[str, Any]) -> ApprovalChallengeV3:
        fields = quote["fields"]
        actions = fields.get("actions") if isinstance(fields.get("actions"), list) else []
        action_set = ""
        if len(actions) > 1:
            action_set = ";".join(
                "|".join(str(action.get(name, "")) for name in ("kind", "proposal_id", "to", "amount_minor", "asset", "network_fee_max_minor", "gas_asset", "collection_id", "state_version"))
                for action in actions if isinstance(action, dict)
            )
        return cls(
            proposal_id=str(proposal.proposal_id), wallet_id=str(proposal.wallet_id), network=str(fields["network"]),
            environment=str(fields["environment"]), chain_identity=str(fields["chain_identity"]), account_id=str(fields["from_address"]),
            destination=str(fields["to_address"]), asset=str(fields["asset"]), amount_minor=int(fields["amount_minor"]),
            human_amount=str(fields["amount_human"]), quote_id=str(quote["quote_id"]), quote_digest=str(quote["digest"]),
            fee_model=str(fields["fee_model"]), fee_max_minor=int(fields["fee_max_minor"]), gas_asset=str(fields["gas_asset"]),
            max_total_minor=int(fields["max_total_minor"]), quote_expires_at=float(fields["expires_at"]),
            idempotency_key=str(getattr(proposal, "idempotency_key", "") or proposal.proposal_id), action_set=action_set,
        )

    def _canonical_fields(self) -> list[str]:
        fields = [
            _DIGEST_DOMAIN_V3, self.proposal_id, self.wallet_id, self.network, self.environment, self.chain_identity, self.account_id,
            self.destination, self.asset, str(int(self.amount_minor)), self.human_amount, self.quote_id, self.quote_digest, self.fee_model,
            str(int(self.fee_max_minor)), self.gas_asset, str(int(self.max_total_minor)), repr(float(self.quote_expires_at)), self.idempotency_key,
        ]
        if self.action_set:
            fields.append("actions:" + self.action_set)
        return fields

    @property
    def digest(self) -> str:
        return hashlib.sha256("|".join(self._canonical_fields()).encode("utf-8")).hexdigest()

    def binding_view(self) -> dict[str, Any]:
        return {**{name: getattr(self, name) for name in self.__dataclass_fields__}, "challenge_digest": self.digest}


def decision_binds_v3(decision: ApprovalDecision, challenge: ApprovalChallengeV3) -> bool:
    return bool(decision.approved) and decision.challenge_digest == challenge.digest and decision.challenge_digest == hashlib.sha256("|".join(challenge._canonical_fields()).encode("utf-8")).hexdigest()


def decision_binds(decision: ApprovalDecision, challenge: ApprovalChallenge) -> bool:
    """True only when the decision approves THIS exact challenge: same digest, and the
    digest itself must be reproducible from the challenge's own fields."""
    return bool(decision.approved) and decision.challenge_digest == challenge.digest and decision.challenge_digest == hashlib.sha256("|".join(challenge._canonical_fields()).encode("utf-8")).hexdigest()


__all__ = ["METHOD_BIOMETRIC", "METHOD_PIN", "ApprovalChallenge", "ApprovalDecision", "Approver", "BiometricApprover", "PinApprover", "UnavailableBiometric", "decision_binds"]
