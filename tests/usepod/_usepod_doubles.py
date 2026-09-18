"""TEST DOUBLES for the two authorities the UsePod provider transport calls but does not own.

Neither class is a monetary authority or a wallet. Each records what the provider asked for, and each
carries a ``label`` beginning ``test_double:`` that is written into every receipt it touches, so no
evidence produced with them can be read as completed payment or budget integration.
"""
from __future__ import annotations

import hashlib
import itertools
import time
from collections.abc import Callable
from typing import Any

from core.usepod.monetary import MonetaryAuthorityRefusedError, MonetaryReservation
from core.usepod.transport import PaymentAuthorityRefusedError, X402PaymentProof
from tests.usepod.strict_usepod_service import DOCUMENTED_MAINNET_NETWORK

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SYNTHETIC_PAYER_WALLET = "9SynthPayer" + "1" * 33


def synthetic_signature(seed: str) -> str:
    """An 88-character base58 string: the SHAPE of a Solana signature, from no chain at all."""
    digest = hashlib.sha512(seed.encode("utf-8")).digest() + hashlib.sha512(("x" + seed).encode("utf-8")).digest()
    return "".join(_BASE58[byte % 58] for byte in digest)[:88]


class RecordingMonetaryTestDouble:
    """TEST DOUBLE -- records reserve / mark_dispatched / settle / retain_unknown / release_unsent."""

    label = "test_double:recording_monetary_authority"

    def __init__(self, *, refuse: str = "") -> None:
        self.refuse = refuse
        self.calls: list[tuple[str, Any]] = []
        self._ids = itertools.count(1)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def reserve(self, liability: Any) -> MonetaryReservation:
        if self.refuse:
            self.calls.append(("refused", liability))
            raise MonetaryAuthorityRefusedError(self.refuse)
        self.calls.append(("reserve", liability))
        return MonetaryReservation(f"test-double-res-{next(self._ids)}", self.label, liability, time.time())

    def mark_dispatched(self, reservation: MonetaryReservation) -> None:
        self.calls.append(("mark_dispatched", reservation))

    def settle(self, reservation: MonetaryReservation, evidence: Any) -> None:
        self.calls.append(("settle", evidence))

    def retain_unknown(self, reservation: MonetaryReservation, evidence: Any) -> None:
        self.calls.append(("retain_unknown", evidence))

    def release_unsent(self, reservation: MonetaryReservation, *, reason: str) -> None:
        self.calls.append(("release_unsent", reason))


class SyntheticChainPaymentTestDouble:
    """TEST DOUBLE -- not a wallet. 'Pays' by telling the strict local service a synthetic signature confirmed."""

    label = "test_double:synthetic_chain_payment"
    #: The network the SYNTHETIC service quotes on; a real authority lists what it verified.
    networks = (DOCUMENTED_MAINNET_NETWORK,)

    def __init__(
        self,
        service: Any,
        *,
        refuse: str = "",
        raise_unknown: bool = False,
        mutate_proof: Callable[[X402PaymentProof], X402PaymentProof] | None = None,
        underpay_by: int = 0,
    ) -> None:
        self.service = service
        self.refuse = refuse
        self.raise_unknown = raise_unknown
        self.mutate_proof = mutate_proof
        self.underpay_by = underpay_by
        self.calls: list[dict[str, Any]] = []
        #: Everything handed out, so a test can resend an already-paid operation with the SAME proof.
        self.issued: list[dict[str, Any]] = []
        self._counter = itertools.count(1)

    def wallet_facts(self, *, network: str, asset: str, atomic_unit: str):
        """The wallet-facts the production transport now requires before any reservation or
        proof: this double publishes its payer and a funded fee rail. TEST facts, not a wallet."""
        from core.usepod.transport import X402WalletFacts

        return X402WalletFacts(
            payer_account=SYNTHETIC_PAYER_WALLET,
            fee_network=network,
            fee_asset="native",
            fee_decimals=9,
            fee_max_atomic=100_000,
            principal_balance_atomic=50_000_000,
            fee_balance_atomic=1_000_000,
        )

    def obtain_proof(self, *, quote: Any, option: Any, envelope: Any, liability: Any, reservation: Any) -> X402PaymentProof:
        self.calls.append({"quote_id": quote.quote_id, "operation_id": envelope.operation_id, "amount": option.amount_atomic, "asset": option.asset})
        if self.refuse:
            raise PaymentAuthorityRefusedError(self.refuse)
        if self.raise_unknown:
            raise TimeoutError("synthetic broadcast confirmation timed out")
        signature = synthetic_signature(f"{envelope.operation_id}:{next(self._counter)}")
        self.service.record_chain_payment(signature, asset=option.asset, amount_atomic=int(option.amount_atomic) - self.underpay_by)
        proof = X402PaymentProof(
            operation_id=envelope.operation_id,
            envelope_binding_sha256=envelope.binding_sha256,
            quote_header_sha256=quote.header_sha256,
            quote_id=quote.quote_id,
            network=option.network,
            asset=option.asset,
            pay_to=option.pay_to,
            amount_atomic=int(option.amount_atomic),
            payer_wallet=SYNTHETIC_PAYER_WALLET,
            signature=signature,
            authority_label=self.label,
            issued_at=time.time(),
        )
        handed = self.mutate_proof(proof) if self.mutate_proof else proof
        self.issued.append({"quote": quote, "option": option, "reservation": reservation, "proof": handed})
        return handed


__all__ = ["SYNTHETIC_PAYER_WALLET", "RecordingMonetaryTestDouble", "SyntheticChainPaymentTestDouble", "synthetic_signature"]
