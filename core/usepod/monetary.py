"""The monetary boundary a paid UsePod dispatch must pass.

This is the CONSUMER side of a contract whose implementation belongs to VOOL's monetary authority
(the atomic reservation / settlement owner being built separately). It states exactly what the
provider transport hands over and when:

1. ``reserve(liability)`` BEFORE any byte leaves: the maximum this one operation can cost, in an
   asset-qualified integer unit, with the basis that produced it. Refusal or absence stops the call.
2. ``mark_dispatched(reservation)`` immediately before the transport writes the request.
3. Exactly one terminal:
   * ``settle(reservation, evidence)`` -- a response arrived and usage evidence was read;
   * ``retain_unknown(reservation, evidence)`` -- the request may have been served and billed but the
     outcome is not known (timeout after send, broken stream, error after send). The liability stays
     reserved; nothing here converts UNKNOWN into free;
   * ``release_unsent(reservation, reason)`` -- proven that nothing was sent.

By default :func:`monetary_authority` now returns the production authority backed by VOOL's
reviewed money law (:mod:`core.usepod.money_law` over :mod:`core.effect_budget_money`): a paid
dispatch is refused with the money law's typed codes until an operator grant and fresh provider
liquidity exist -- still never uncapped. An explicitly installed authority (a labelled test
double) replaces it, and every installed authority carries a label written into every receipt, so
a test double can never be mistaken for the real authority. :func:`reset_monetary_authority`
restores the explicit UNAVAILABLE authority, which is what the test suite's isolation relies on.

Money here is integers in the asset's atomic unit (USDC microunits). There is no float and no FX.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

ASSET_USDC = "USDC"
UNIT_USDC_MICROUNIT = "usdc_microunit"
ACCOUNT_KIND_PREPAID_TOKEN = "usepod_prepaid_token_balance"
ACCOUNT_KIND_X402_PAYER = "x402_payer_wallet"
NETWORK_USEPOD_ACCOUNT = "usepod_internal_account_ledger"

OUTCOME_COMPLETED = "completed"
OUTCOME_ROUTE_POLICY_VIOLATED = "completed_on_unpermitted_route"
OUTCOME_ROUTE_UNVERIFIED = "completed_route_unverified"
OUTCOME_FAILED_AFTER_SEND = "failed_after_send"
OUTCOME_PARTIAL_STREAM = "partial_stream"
OUTCOME_UNKNOWN = "outcome_unknown"

EXACT_COST_NOT_SUPPLIED = "not_supplied_by_provider"


class MonetaryAuthorityUnavailableError(RuntimeError):
    code = "monetary_authority_unavailable"

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class MonetaryAuthorityRefusedError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "monetary_authority_refused")
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


@dataclass(frozen=True)
class ProviderLiability:
    """The most one provider operation can cost, and exactly why that is the most."""

    operation_id: str
    provider_id: str
    transport_mode: str
    asset: str
    unit: str
    account_kind: str
    #: A credential fingerprint for a prepaid token; "" for x402 until the wallet names a payer.
    account_ref: str
    network: str
    max_amount_atomic: int
    model_id: str
    route_approval_id: str
    envelope_binding_sha256: str
    basis: Mapping[str, Any]
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.max_amount_atomic, bool) or not isinstance(self.max_amount_atomic, int) or self.max_amount_atomic < 1:
            raise ValueError("a liability must be a positive integer in the asset's atomic unit")
        if not self.operation_id or not self.asset or not self.unit:
            raise ValueError("a liability needs an operation id, an asset and a unit")

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "provider_id": self.provider_id,
            "transport_mode": self.transport_mode,
            "asset": self.asset,
            "unit": self.unit,
            "account_kind": self.account_kind,
            "account_ref": self.account_ref,
            "network": self.network,
            "max_amount_atomic": self.max_amount_atomic,
            "model_id": self.model_id,
            "route_approval_id": self.route_approval_id,
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "basis": dict(self.basis),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MonetaryReservation:
    reservation_id: str
    authority_label: str
    liability: ProviderLiability
    granted_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "authority_label": self.authority_label,
            "max_amount_atomic": self.liability.max_amount_atomic,
            "asset": self.liability.asset,
            "unit": self.liability.unit,
            "granted_at": self.granted_at,
        }


@dataclass(frozen=True)
class SettlementEvidence:
    """What is known about one operation's cost after the response. Missing stays missing."""

    operation_id: str
    outcome: str
    http_status: int | None
    usage: Mapping[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    #: Reported usage priced at the APPROVED ceilings: the most this call can have been charged if the
    #: usage is true. None when usage was not reported -- never derived from a guess.
    upper_bound_cost_atomic: int | None
    #: The provider's own exact charge, when it supplies one. The documented surfaces supply none.
    exact_cost_atomic: int | None
    exact_cost_state: str
    route: Mapping[str, Any]
    balance_remaining_raw: str | None
    usage_exceeds_liability_bound: bool
    detail: str = ""
    #: x402 only: surplus the provider credited for this operation, from its PAYMENT-RESPONSE.
    #: Provider credit on the provider's own account -- never a wallet refund. None when the
    #: provider supplied no payment response.
    provider_credit_atomic: int | None = None
    #: The provider account the surplus was credited to (the quote's pay-to).
    provider_credit_account: str = ""
    #: How the reservation's transport mode was established, for authority-side line mapping.
    recorded_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "usage": dict(self.usage),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "upper_bound_cost_atomic": self.upper_bound_cost_atomic,
            "exact_cost_atomic": self.exact_cost_atomic,
            "exact_cost_state": self.exact_cost_state,
            "route": dict(self.route),
            "balance_remaining_raw": self.balance_remaining_raw,
            "usage_exceeds_liability_bound": self.usage_exceeds_liability_bound,
            "provider_credit_atomic": self.provider_credit_atomic,
            "provider_credit_account": self.provider_credit_account,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }


@runtime_checkable
class MonetaryAuthority(Protocol):
    label: str

    def reserve(self, liability: ProviderLiability) -> MonetaryReservation: ...

    def mark_dispatched(self, reservation: MonetaryReservation) -> None: ...

    def settle(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None: ...

    def retain_unknown(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None: ...

    def release_unsent(self, reservation: MonetaryReservation, *, reason: str) -> None: ...


class UnavailableMonetaryAuthority:
    """The authority in force until a real one is installed: every paid dispatch is refused."""

    label = "unavailable:monetary_authority_not_integrated"

    def reserve(self, liability: ProviderLiability) -> MonetaryReservation:
        raise MonetaryAuthorityUnavailableError("no monetary authority is integrated; a paid UsePod dispatch cannot be reserved")

    def mark_dispatched(self, reservation: MonetaryReservation) -> None:
        raise MonetaryAuthorityUnavailableError("no reservation can exist without an authority")

    def settle(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None:
        raise MonetaryAuthorityUnavailableError("no reservation can exist without an authority")

    def retain_unknown(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None:
        raise MonetaryAuthorityUnavailableError("no reservation can exist without an authority")

    def release_unsent(self, reservation: MonetaryReservation, *, reason: str) -> None:
        raise MonetaryAuthorityUnavailableError("no reservation can exist without an authority")


_LOCK = threading.Lock()
#: None is the PRODUCTION sentinel: the first caller gets the money-law authority
#: (:mod:`core.usepod.money_law`), constructed lazily so importing this module costs nothing.
#: An explicit install (a labelled test double, or an explicit unavailable) replaces it;
#: :func:`reset_monetary_authority` returns to the explicit unavailable authority, which is what
#: the UsePod test suite's isolation relies on.
_INSTALLED: Any = None
_PRODUCTION: Any = None


def _production_authority() -> Any:
    global _PRODUCTION
    if _PRODUCTION is None:
        from core.usepod.money_law import EffectBudgetMonetaryAuthority

        _PRODUCTION = EffectBudgetMonetaryAuthority()
    return _PRODUCTION


def monetary_authority() -> MonetaryAuthority:
    with _LOCK:
        return _INSTALLED if _INSTALLED is not None else _production_authority()


def install_monetary_authority(authority: MonetaryAuthority, *, label: str) -> MonetaryAuthority:
    """Install the authority paid dispatch will use. Returns the previous one.

    ``label`` must be non-empty and must equal ``authority.label``: the label is what receipts carry,
    so an authority cannot present itself under a name it was not installed with.
    """
    clean = str(label or "").strip()
    if not clean or str(getattr(authority, "label", "") or "") != clean:
        raise ValueError("a monetary authority must be installed under its own non-empty label")
    if not isinstance(authority, MonetaryAuthority):
        raise TypeError("object does not implement the monetary authority contract")
    global _INSTALLED
    with _LOCK:
        previous = _INSTALLED
        _INSTALLED = authority
    return previous


def reset_monetary_authority() -> None:
    global _INSTALLED
    with _LOCK:
        _INSTALLED = UnavailableMonetaryAuthority()


__all__ = [
    "ACCOUNT_KIND_PREPAID_TOKEN",
    "ACCOUNT_KIND_X402_PAYER",
    "ASSET_USDC",
    "EXACT_COST_NOT_SUPPLIED",
    "NETWORK_USEPOD_ACCOUNT",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED_AFTER_SEND",
    "OUTCOME_PARTIAL_STREAM",
    "OUTCOME_ROUTE_POLICY_VIOLATED",
    "OUTCOME_ROUTE_UNVERIFIED",
    "OUTCOME_UNKNOWN",
    "UNIT_USDC_MICROUNIT",
    "MonetaryAuthority",
    "MonetaryAuthorityRefusedError",
    "MonetaryAuthorityUnavailableError",
    "MonetaryReservation",
    "ProviderLiability",
    "SettlementEvidence",
    "UnavailableMonetaryAuthority",
    "install_monetary_authority",
    "monetary_authority",
    "reset_monetary_authority",
]
