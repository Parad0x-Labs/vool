"""P3 — THE MONETARY LAW OF THE ONE BUDGET AUTHORITY.

`core.effect_budget` owns budget truth. Its unit law counts attempts: a unit is
`reserved`, then `consumed` the moment an attempt begins, and never refunded.
That law is right for counting effects and wrong for money. A paid call can
cost less than its ceiling, can be proven never sent, or can end with nobody
knowing whether it was paid; a payment moves one asset while its fee moves
another; a provider can keep the unused part of a paid cap as credit. This
module is the SAME authority's monetary law — same store seam, same process
instance registry, same operator token, same event journal. It never reads a
consumed unit as a settled payment, and it keeps no ledger anywhere else.

THE LAWS
--------
1. EXACT MONEY. An amount is a non-negative integer of atomic units of one
   asset identity (network, asset id, decimals); the symbol is display only.
   No float carries money. A ceiling counts lines of its own asset; a
   cross-asset ceiling counts another asset only through an operator-granted
   conservative conversion bound (rounded up) and refuses when a needed bound
   is missing or expired. No rate is ever inferred.

2. FLOWS ARE SEPARATE FACTS. Gross wallet outflow, network fee, consumed
   inference expense, provider-credit debit and provider-credit credit are
   separate lines. Paying a 0.20 cap and consuming 0.03 is an outflow of 0.20,
   an expense of 0.03 and — when the provider says so — a credit of 0.17 at
   the provider account, never a wallet refund. A top-up is outflow plus
   credit, never expense; spending that credit is expense plus credit debit,
   never outflow.

3. INTERSECTING CEILINGS. Operator money rules bind one flow and one asset
   under a scope (request, turn, task, agent, session, project, provider,
   account, global), optionally over a rolling window. Every applicable rule
   must pass; none is an allowance added to another.

4. CONSUMABLE ECONOMIC AUTHORITY. Every liability names a grant. A grant is
   minted only with a durably granted operator token outside every effect
   scope — a model, skill or plugin cannot mint or widen one — and binds
   provider, account, models, routes, network, asset, payer, amounts, fees,
   count, frequency, concurrency, identity and expiry. The transaction that
   writes a liability is the transaction that consumes its grant. Revocation
   and expiry stop new reservations and dispatch claims; they never erase a
   liability that may have been paid.

5. RESERVE THE MAXIMUM, ATOMICALLY. One BEGIN IMMEDIATE transaction reads the
   operation's existing liability, the grant, every rule, the grant's own
   consumption and the liquidity observations, and writes the liability, its
   lines and its receipt only when everything passes.

6. DISPATCH OWNERSHIP BEFORE EFFECT. `claim_dispatch` moves reserved to
   dispatching with a fencing token before any signing, broadcast or provider
   request. Exactly one executor wins.

7. UNKNOWN IS NOT FREE. A liability leaves the held set only as `released`
   (never claimed), `unsent` (claimed, then positive proof it never went out)
   or `settled` (verified evidence). A crash, a lost body, a partial stream, a
   store failure or a revoked grant leaves the maximum held as `unknown`.

8. EVIDENCE-BOUND, IDEMPOTENT RECONCILIATION. Settlement names a closed
   evidence kind and an evidence id. Replaying the same evidence changes
   nothing; conflicting evidence is refused and journaled; a flow never
   settles twice; an amount above the maximum is recorded and flagged, never
   silent; an actual that nobody proved stays bounded at the maximum instead
   of being fabricated from a balance delta.

9. LIQUIDITY IS ITS OWN CHECK. A wallet debit (principal or fee, each in its
   own asset) needs a fresh balance observation covering every held debit; a
   prepaid credit debit needs a fresh verified provider balance unless its
   grant explicitly opts out.

10. FAIL CLOSED ON THE STORE. An unreadable store refuses with a typed code;
    a corrupt row refuses what it could affect and is reported, and never
    reads as zero.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from typing import Any

from core import effect_budget as _eb
from core.effect_budget import EffectBudgetRefusedError, OperatorBudgetToken

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

MONEY_SCHEMA_VERSION = 1

FLOW_WALLET_OUTFLOW = "wallet_outflow"
FLOW_NETWORK_FEE = "network_fee"
FLOW_INFERENCE_EXPENSE = "inference_expense"
FLOW_PROVIDER_CREDIT_DEBIT = "provider_credit_debit"
FLOW_PROVIDER_CREDIT_CREDIT = "provider_credit_credit"
#: The native DNA x402 service fee on ONE accepted provider payment, in the paid asset, debited from the payer
#: later through a separate collection transfer. The line holds the whole-unit CEILING of the fee at
#: reservation so consents and liquidity checks include it; the exact sub-atomic obligation lives in the
#: service-fee ledger (core.service_fee_ledger) and is written by settle_liability itself, never by a caller's
#: actuals.
FLOW_SERVICE_FEE = "service_fee"
MONEY_FLOWS = frozenset(
    {
        FLOW_WALLET_OUTFLOW,
        FLOW_NETWORK_FEE,
        FLOW_INFERENCE_EXPENSE,
        FLOW_PROVIDER_CREDIT_DEBIT,
        FLOW_PROVIDER_CREDIT_CREDIT,
        FLOW_SERVICE_FEE,
    }
)
WALLET_DEBIT_FLOWS = frozenset({FLOW_WALLET_OUTFLOW, FLOW_NETWORK_FEE, FLOW_SERVICE_FEE})

OP_INFERENCE_PREPAID = "inference_prepaid"
OP_INFERENCE_X402 = "inference_x402"
OP_PROVIDER_TOPUP = "provider_topup"
OP_WALLET_PAYMENT = "wallet_payment"

#: The lines each operation kind carries, the flow its grant consumption
#: counts, and the pairs that must share asset and maximum.
_OPERATION_SCHEMA: dict[str, dict[str, Any]] = {
    OP_INFERENCE_PREPAID: {
        "required": (FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT),
        "optional": (),
        "bound": FLOW_INFERENCE_EXPENSE,
        "equal": ((FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT),),
        "wallet": False,
        "inference": True,
    },
    OP_INFERENCE_X402: {
        "required": (FLOW_WALLET_OUTFLOW, FLOW_INFERENCE_EXPENSE),
        "optional": (FLOW_NETWORK_FEE, FLOW_SERVICE_FEE),
        "bound": FLOW_WALLET_OUTFLOW,
        "equal": ((FLOW_WALLET_OUTFLOW, FLOW_INFERENCE_EXPENSE),),
        "wallet": True,
        "inference": True,
    },
    OP_PROVIDER_TOPUP: {
        "required": (FLOW_WALLET_OUTFLOW, FLOW_PROVIDER_CREDIT_CREDIT),
        "optional": (FLOW_NETWORK_FEE,),
        "bound": FLOW_WALLET_OUTFLOW,
        "equal": ((FLOW_WALLET_OUTFLOW, FLOW_PROVIDER_CREDIT_CREDIT),),
        "wallet": True,
        "inference": False,
    },
    OP_WALLET_PAYMENT: {
        "required": (FLOW_WALLET_OUTFLOW,),
        "optional": (FLOW_NETWORK_FEE,),
        "bound": FLOW_WALLET_OUTFLOW,
        "equal": (),
        "wallet": True,
        "inference": False,
    },
}
OPERATION_KINDS = frozenset(_OPERATION_SCHEMA)
WALLET_OPERATION_KINDS = frozenset(kind for kind, schema in _OPERATION_SCHEMA.items() if schema["wallet"])

GRANT_TASK_ENVELOPE = "task_envelope"
GRANT_SINGLE_PAYMENT = "single_payment"
GRANT_AUTO_TOPUP = "auto_topup"
GRANT_PROVIDER_BUDGET = "provider_budget"
_GRANT_KIND_OPERATIONS: dict[str, frozenset[str]] = {
    GRANT_PROVIDER_BUDGET: frozenset({OP_INFERENCE_PREPAID}),
    GRANT_TASK_ENVELOPE: frozenset({OP_INFERENCE_PREPAID, OP_INFERENCE_X402, OP_WALLET_PAYMENT}),
    GRANT_SINGLE_PAYMENT: frozenset(OPERATION_KINDS),
    GRANT_AUTO_TOPUP: frozenset({OP_PROVIDER_TOPUP}),
}
GRANT_KINDS = frozenset(_GRANT_KIND_OPERATIONS)
GRANT_ACTIVE = "active"
GRANT_REVOKED = "revoked"
CREDIT_LIQUIDITY_REQUIRED = "required"
CREDIT_LIQUIDITY_NOT_REQUIRED = "not_required"

#: A grant is a bounded envelope, never a standing permission.
MAX_GRANT_LIFETIME_SECONDS = 30 * 24 * 3600.0
#: How old a balance observation may be and still cover a debit.
LIQUIDITY_OBSERVATION_TTL_SECONDS = 300.0
CREDIT_OBSERVATION_TTL_SECONDS = 900.0
#: Closed (released/unsent/settled) liabilities older than this are pruned;
#: held liabilities never are.
MONEY_RETENTION_SECONDS = 400 * 24 * 3600.0

SCOPE_ACCOUNT = "account"
MONEY_SCOPES = (
    _eb.SCOPE_REQUEST,
    _eb.SCOPE_TURN,
    _eb.SCOPE_TASK,
    _eb.SCOPE_AGENT,
    _eb.SCOPE_SESSION,
    _eb.SCOPE_PROJECT,
    _eb.SCOPE_PROVIDER,
    SCOPE_ACCOUNT,
    _eb.SCOPE_GLOBAL,
)
_SCOPE_COLUMNS = {
    _eb.SCOPE_REQUEST: "m.request_id",
    _eb.SCOPE_TURN: "m.turn_id",
    _eb.SCOPE_TASK: "m.task_id",
    _eb.SCOPE_AGENT: "m.agent_id",
    _eb.SCOPE_SESSION: "m.session_id",
    _eb.SCOPE_PROJECT: "m.project_key",
    _eb.SCOPE_PROVIDER: "m.provider_id",
    SCOPE_ACCOUNT: "l.account",
    _eb.SCOPE_GLOBAL: "",
}

LIABILITY_RESERVED = "reserved"
LIABILITY_DISPATCHING = "dispatching"
LIABILITY_PENDING = "pending"
LIABILITY_UNKNOWN = "unknown"
LIABILITY_SETTLED = "settled"
LIABILITY_UNSENT = "unsent"
LIABILITY_RELEASED = "released"
HELD_STATES = (LIABILITY_RESERVED, LIABILITY_DISPATCHING, LIABILITY_PENDING, LIABILITY_UNKNOWN)
CLOSED_STATES = (LIABILITY_UNSENT, LIABILITY_RELEASED)
LIABILITY_STATES = frozenset((*HELD_STATES, LIABILITY_SETTLED, *CLOSED_STATES))

LINE_HELD = "held"
LINE_EXACT = "exact"
LINE_BOUNDED = "bounded"
LINE_STATES = frozenset({LINE_HELD, LINE_EXACT, LINE_BOUNDED})

EVIDENCE_CHAIN_CONFIRMATION = "chain_confirmation"
EVIDENCE_PROVIDER_USAGE_RECEIPT = "provider_usage_receipt"
EVIDENCE_PROVIDER_BILLING_STATEMENT = "provider_billing_statement"
#: The provider's own operation-bound record that THIS operation was refused before service --
#: an admission refusal. It proves a zero charge (nothing was served, so nothing was metered)
#: and nothing else: every actual it carries must be zero, and only a prepaid-account operation
#: qualifies (a wallet-paid operation's money moves at payment; a refusal after payment is the
#: paid-retry path, not an admission refusal). NOT a contradiction kind: it can never show
#: money moved after a close.
EVIDENCE_PROVIDER_REFUSAL_RECORD = "provider_refusal_record"
EVIDENCE_USAGE_PRICED = "usage_priced"
EVIDENCE_OPERATOR_ATTESTATION = "operator_attestation"
EVIDENCE_BALANCE_DELTA = "balance_delta"
_EVIDENCE_FLOWS: dict[str, frozenset[str]] = {
    EVIDENCE_CHAIN_CONFIRMATION: frozenset({FLOW_WALLET_OUTFLOW, FLOW_NETWORK_FEE}),
    EVIDENCE_PROVIDER_USAGE_RECEIPT: frozenset(
        {FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT, FLOW_PROVIDER_CREDIT_CREDIT}
    ),
    EVIDENCE_PROVIDER_BILLING_STATEMENT: frozenset(
        {FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT, FLOW_PROVIDER_CREDIT_CREDIT}
    ),
    EVIDENCE_PROVIDER_REFUSAL_RECORD: frozenset({FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT}),
    EVIDENCE_USAGE_PRICED: frozenset({FLOW_INFERENCE_EXPENSE}),
    EVIDENCE_OPERATOR_ATTESTATION: frozenset(MONEY_FLOWS),
}
SETTLEMENT_EVIDENCE_KINDS = frozenset(_EVIDENCE_FLOWS)
#: Settlement that proves the money moved even if a liability had been closed.
_CONTRADICTION_EVIDENCE = frozenset(
    {
        EVIDENCE_CHAIN_CONFIRMATION,
        EVIDENCE_PROVIDER_USAGE_RECEIPT,
        EVIDENCE_PROVIDER_BILLING_STATEMENT,
        EVIDENCE_OPERATOR_ATTESTATION,
    }
)
EVIDENCE_SOURCES = frozenset({"provider", "mechanical", "user"})

#: Positive proof, from the executor that claimed dispatch, that nothing left.
UNSENT_LOCAL_REFUSAL = "local_refusal_before_send"
UNSENT_CONNECTION_NOT_ESTABLISHED = "connection_not_established"
UNSENT_VALIDATED_REJECTION = "validated_rejection_before_execution"
UNSENT_SIGNING_REQUEST_EXPIRED = "signing_request_expired_unconsumed"
#: Proof from outside the executor (provider or chain) that nothing landed.
UNSENT_PROVIDER_NOT_RECEIVED = "provider_confirmed_not_received"
UNSENT_CHAIN_EXPIRED_NOT_LANDED = "chain_expired_not_landed"
UNSENT_OPERATOR_ATTESTATION = "operator_attestation"
_CLAIMANT_UNSENT_PROOFS = frozenset(
    {
        UNSENT_LOCAL_REFUSAL,
        UNSENT_CONNECTION_NOT_ESTABLISHED,
        UNSENT_VALIDATED_REJECTION,
        UNSENT_SIGNING_REQUEST_EXPIRED,
    }
)
_EXTERNAL_UNSENT_PROOFS = frozenset(
    {UNSENT_PROVIDER_NOT_RECEIVED, UNSENT_CHAIN_EXPIRED_NOT_LANDED, UNSENT_SIGNING_REQUEST_EXPIRED}
)

FUNDING_MANUAL = "manual"
FUNDING_ASK_BELOW_THRESHOLD = "ask_below_threshold"
FUNDING_AUTO = "auto"
FUNDING_MODES = frozenset({FUNDING_MANUAL, FUNDING_ASK_BELOW_THRESHOLD, FUNDING_AUTO})

MONEY_BUDGET_EXCEEDED = "MONEY_BUDGET_EXCEEDED"
MONEY_AUTHORITY_REQUIRED = "MONEY_AUTHORITY_REQUIRED"
MONEY_AUTHORITY_INVALID = "MONEY_AUTHORITY_INVALID"
MONEY_AUTHORITY_EXHAUSTED = "MONEY_AUTHORITY_EXHAUSTED"
MONEY_AUTHORITY_EXPIRED = "MONEY_AUTHORITY_EXPIRED"
MONEY_AUTHORITY_REVOKED = "MONEY_AUTHORITY_REVOKED"
MONEY_AUTHORITY_RATE_LIMITED = "MONEY_AUTHORITY_RATE_LIMITED"
MONEY_LIQUIDITY_UNVERIFIED = "MONEY_LIQUIDITY_UNVERIFIED"
MONEY_LIQUIDITY_INSUFFICIENT = "MONEY_LIQUIDITY_INSUFFICIENT"
MONEY_CONVERSION_UNAVAILABLE = "MONEY_CONVERSION_UNAVAILABLE"
MONEY_FROZEN = "MONEY_FROZEN"
MONEY_RESERVATION_EXPIRED = "MONEY_RESERVATION_EXPIRED"
MONEY_STATE_ERROR = "MONEY_STATE_ERROR"
MONEY_CLAIM_CONFLICT = "MONEY_CLAIM_CONFLICT"
MONEY_SETTLEMENT_CONFLICT = "MONEY_SETTLEMENT_CONFLICT"
MONEY_EVIDENCE_INSUFFICIENT = "MONEY_EVIDENCE_INSUFFICIENT"
MONEY_IDEMPOTENCY_CONFLICT = "MONEY_IDEMPOTENCY_CONFLICT"
MONEY_IDENTITY_CONFLICT = "MONEY_IDENTITY_CONFLICT"
MONEY_INVALID_REQUEST = "MONEY_INVALID_REQUEST"
MONEY_STORE_UNAVAILABLE = "MONEY_STORE_UNAVAILABLE"
MONEY_STORE_CORRUPT = "MONEY_STORE_CORRUPT"
MONEY_REFUSAL_CODES = (
    MONEY_BUDGET_EXCEEDED,
    MONEY_AUTHORITY_REQUIRED,
    MONEY_AUTHORITY_INVALID,
    MONEY_AUTHORITY_EXHAUSTED,
    MONEY_AUTHORITY_EXPIRED,
    MONEY_AUTHORITY_REVOKED,
    MONEY_AUTHORITY_RATE_LIMITED,
    MONEY_LIQUIDITY_UNVERIFIED,
    MONEY_LIQUIDITY_INSUFFICIENT,
    MONEY_CONVERSION_UNAVAILABLE,
    MONEY_FROZEN,
    MONEY_RESERVATION_EXPIRED,
    MONEY_STATE_ERROR,
    MONEY_CLAIM_CONFLICT,
    MONEY_SETTLEMENT_CONFLICT,
    MONEY_EVIDENCE_INSUFFICIENT,
    MONEY_IDEMPOTENCY_CONFLICT,
    MONEY_IDENTITY_CONFLICT,
    MONEY_INVALID_REQUEST,
    MONEY_STORE_UNAVAILABLE,
    MONEY_STORE_CORRUPT,
    _eb.REFUSAL_AUTHORITY,
)

_META_TABLE = "effect_budget_money_meta"
_RULES_TABLE = "effect_budget_money_rules"
_BOUNDS_TABLE = "effect_budget_money_conversion_bounds"
_GRANTS_TABLE = "effect_budget_money_grants"
_LIABILITIES_TABLE = "effect_budget_money_liabilities"
_LINES_TABLE = "effect_budget_money_lines"
_EVIDENCE_TABLE = "effect_budget_money_evidence"
_LIQUIDITY_TABLE = "effect_budget_money_liquidity"
_FUNDING_TABLE = "effect_budget_money_funding_policies"

MAX_ATOMIC_DIGITS = 78
_ATOMIC_RE = re.compile(r"(0|[1-9][0-9]{0,77})")
_NETWORK_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,31}:[A-Za-z0-9._\-]{1,64}")
_ASSET_RE = re.compile(r"[A-Za-z0-9._:\-]{1,128}")
_ACCOUNT_RE = re.compile(r"[A-Za-z0-9._:\-@/#+]{1,200}")
_IDENT_RE = re.compile(r"[^\s\x00-\x1f\x7f]{0,200}")
_OPERATION_ID_RE = re.compile(r"[A-Za-z0-9._:\-/#@+=]{1,200}")
_CORRELATION_KEY_RE = re.compile(r"[a-z0-9_]{1,40}")
_SECRET_KEY_WORDS = ("token", "secret", "key", "authorization", "password", "pin", "url", "cookie")
_DECIMAL_RE = re.compile(r"([0-9]+)(?:\.([0-9]+))?")


def _now() -> float:
    return _eb._utcnow_epoch()


# ---------------------------------------------------------------------------
# exact amounts
# ---------------------------------------------------------------------------


class _CorruptRowError(RuntimeError):
    """A persisted money row that cannot be read as what it claims to be."""

    def __init__(self, what: str, *, liability_id: str = "") -> None:
        self.liability_id = str(liability_id or "")
        super().__init__(what)


def _invalid(detail: str) -> EffectBudgetRefusedError:
    return EffectBudgetRefusedError(MONEY_INVALID_REQUEST, detail)


def require_atomic(value: Any, *, what: str, allow_zero: bool = False) -> int:
    """An integer of atomic units, or a typed refusal. A float is refused
    outright: money never rides a binary float, even one that looks whole."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(
            f"{what} must be an integer of atomic units, not {type(value).__name__}"
        )
    if value < 0 or (value == 0 and not allow_zero):
        raise _invalid(f"{what} must be {'>= 0' if allow_zero else '> 0'} atomic units")
    if len(str(value)) > MAX_ATOMIC_DIGITS:
        raise _invalid(f"{what} exceeds {MAX_ATOMIC_DIGITS} digits")
    return value


def parse_decimal_amount(text: Any, *, decimals: int) -> int:
    """Atomic units for an exact decimal string ("0.20" at 6 decimals is 200000).

    Refuses floats, signs, exponents and precision beyond the asset — never
    rounds. The UsePod price-ceiling convention (USDC microunits per million
    tokens) is an integer already and does not come through here.
    """
    if isinstance(text, bool) or not isinstance(text, (str, int)):
        raise _invalid(f"a decimal amount must be a string or int, not {type(text).__name__}")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 36:
        raise _invalid("decimals must be an int in 0..36")
    raw = str(text).strip()
    match = _DECIMAL_RE.fullmatch(raw)
    if match is None:
        raise _invalid(f"{raw[:40]!r} is not a plain non-negative decimal")
    whole, fraction = match.group(1), match.group(2) or ""
    if len(fraction) > decimals:
        if fraction[decimals:].strip("0"):
            raise _invalid(
                f"{raw[:40]!r} has more precision than the asset's {decimals} decimals"
            )
        fraction = fraction[:decimals]
    value = int(whole) * 10**decimals + int(fraction.ljust(decimals, "0") or "0")
    return require_atomic(value, what="amount", allow_zero=True)


def format_atomic(value: int, decimals: int) -> str:
    """The exact decimal for an atomic integer, trailing zeros removed."""
    number = int(value)
    places = int(decimals)
    sign = "-" if number < 0 else ""
    digits = str(abs(number)).rjust(places + 1, "0")
    whole, fraction = (digits[:-places], digits[-places:]) if places else (digits, "")
    fraction = fraction.rstrip("0")
    return f"{sign}{whole}.{fraction}" if fraction else f"{sign}{whole}"


def _atomic_text(value: int) -> str:
    return str(int(value))


def _atomic_value(text: Any, *, what: str, liability_id: str = "") -> int:
    raw = "" if text is None else str(text)
    if not _ATOMIC_RE.fullmatch(raw):
        raise _CorruptRowError(f"{what} is not a canonical atomic amount", liability_id=liability_id)
    return int(raw)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-int(numerator) // int(denominator))


# ---------------------------------------------------------------------------
# typed request shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetIdentity:
    """One asset: `network` (CAIP-2 style), `asset` ("native", a mint or
    contract id compared exactly, or an ISO code), `decimals`. `symbol` is a
    display label and never part of identity — a token calling itself USDC is
    not the USDC mint."""

    network: str
    asset: str
    decimals: int
    symbol: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        network = str(self.network or "").strip()
        asset = str(self.asset or "").strip()
        if not _NETWORK_RE.fullmatch(network):
            raise _invalid(f"asset network {network[:80]!r} is not a namespace:reference id")
        if not _ASSET_RE.fullmatch(asset):
            raise _invalid(f"asset id {asset[:80]!r} is not a valid asset identifier")
        if isinstance(self.decimals, bool) or not isinstance(self.decimals, int):
            raise _invalid("asset decimals must be an int")
        if not 0 <= self.decimals <= 36:
            raise _invalid("asset decimals must be within 0..36")
        object.__setattr__(self, "network", network)
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "symbol", str(self.symbol or "")[:16])

    @property
    def key(self) -> str:
        return f"{self.network}|{self.asset}|{self.decimals}"

    @classmethod
    def from_key(cls, key: str, *, symbol: str = "") -> AssetIdentity:
        parts = str(key or "").split("|")
        if len(parts) != 3 or not parts[2].isdigit():
            raise _invalid(f"{str(key)[:120]!r} is not an asset key")
        return cls(network=parts[0], asset=parts[1], decimals=int(parts[2]), symbol=symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "asset": self.asset,
            "decimals": self.decimals,
            "symbol": self.symbol,
            "key": self.key,
        }


def _asset_key_is_valid(key: str) -> bool:
    try:
        AssetIdentity.from_key(key)
    except EffectBudgetRefusedError:
        return False
    return True


def _clean_ident(value: Any, *, what: str) -> str:
    text = str(value or "").strip()
    if not _IDENT_RE.fullmatch(text):
        raise _invalid(f"{what} must be <= 200 printable characters without whitespace")
    return text


def _clean_account(value: Any, *, what: str, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise _invalid(f"{what} is required")
        return ""
    if "://" in text or not _ACCOUNT_RE.fullmatch(text):
        raise _invalid(f"{what} must be an account reference, never a URL")
    return text


@dataclass(frozen=True)
class MoneyIdentity:
    """Who a liability belongs to. Inside a turn the effect ledger owns
    request/turn/session/project and refuses a conflicting stated value."""

    request_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    project_key: str = ""
    provider_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "turn_id",
            "task_id",
            "agent_id",
            "session_id",
            "project_key",
            "provider_id",
        ):
            object.__setattr__(self, name, _clean_ident(getattr(self, name), what=name))

    def to_dict(self) -> dict[str, str]:
        return {
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_key": self.project_key,
            "provider_id": self.provider_id,
        }


@dataclass(frozen=True)
class MoneyLine:
    """One flow's maximum liability in one asset against one account."""

    flow: str
    asset: AssetIdentity
    max_atomic: int
    account: str

    def __post_init__(self) -> None:
        if self.flow not in MONEY_FLOWS:
            raise _invalid(f"{str(self.flow)[:40]!r} is not a money flow")
        if not isinstance(self.asset, AssetIdentity):
            raise _invalid("a money line needs an AssetIdentity")
        require_atomic(self.max_atomic, what=f"{self.flow} maximum")
        object.__setattr__(self, "account", _clean_account(self.account, what=f"{self.flow} account"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow": self.flow,
            "asset_key": self.asset.key,
            "account": self.account,
            "max_atomic": _atomic_text(self.max_atomic),
        }


@dataclass(frozen=True)
class LiabilityRequest:
    """One monetary operation's maximum liability, before dispatch.

    `operation_id` is the durable idempotency id: the same id with the same
    economic terms returns the existing liability; different terms are a
    typed conflict. `correlation` carries public identifiers only (quote id,
    request-envelope digest, provider manifest id, price version) and refuses
    URL-shaped or secret-named entries.
    """

    operation_id: str
    operation_kind: str
    grant_id: str
    lines: tuple[MoneyLine, ...]
    identity: MoneyIdentity = field(default_factory=MoneyIdentity)
    provider_account: str = ""
    model_id: str = ""
    route: str = ""
    network: str = ""
    payer_account: str = ""
    expires_epoch: float = 0.0
    correlation: Mapping[str, str] = field(default_factory=dict)
    effect_id: str = ""
    owner_ref: str = ""


@dataclass(frozen=True)
class LiabilityReceipt:
    liability_id: str
    operation_id: str
    operation_kind: str
    state: str
    grant_id: str
    grant_version: int
    attempt: int
    lines: tuple[dict[str, Any], ...]
    idempotent: bool = False
    effect_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "liability_id": self.liability_id,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
            "state": self.state,
            "grant_id": self.grant_id,
            "grant_version": self.grant_version,
            "attempt": self.attempt,
            "lines": [dict(line) for line in self.lines],
            "idempotent": self.idempotent,
            "effect_id": self.effect_id,
        }


@dataclass(frozen=True)
class DispatchClaim:
    """The fencing token for one dispatch attempt. Only its holder may record
    that the attempt was dispatched or never sent."""

    liability_id: str
    claim_token: str
    attempt: int
    executor: str


@dataclass(frozen=True)
class CreditLine:
    """Provider-side credit a settlement created (x402 surplus) or confirmed
    (a top-up landing). `verified` means the evidence verified the provider
    account identity; unverified credit is recorded and never usable."""

    asset: AssetIdentity
    account: str
    amount_atomic: int
    verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.asset, AssetIdentity):
            raise _invalid("a credit line needs an AssetIdentity")
        require_atomic(self.amount_atomic, what="credit amount", allow_zero=True)
        object.__setattr__(self, "account", _clean_account(self.account, what="credit account"))


@dataclass(frozen=True)
class SettlementEvidence:
    evidence_kind: str
    evidence_id: str
    source: str
    actuals: Mapping[str, int] = field(default_factory=dict)
    credits: tuple[CreditLine, ...] = ()
    public_detail: Mapping[str, str] = field(default_factory=dict)
    operator_token: OperatorBudgetToken | None = None


@dataclass(frozen=True)
class UnsentEvidence:
    proof_kind: str
    evidence_id: str
    source: str
    operator_token: OperatorBudgetToken | None = None


@dataclass(frozen=True)
class MoneyGrantSpec:
    """The operator's bounded economic authorization. Every field is a
    ceiling or a binding; nothing here widens by default."""

    kind: str
    operation_kinds: tuple[str, ...]
    provider_id: str
    asset: AssetIdentity
    max_total_atomic: int
    per_operation_max_atomic: int
    expires_epoch: float
    provider_account: str = ""
    models: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    network: str = ""
    payer_account: str = ""
    fee_asset: AssetIdentity | None = None
    max_fee_total_atomic: int = 0
    per_operation_max_fee_atomic: int = 0
    renewal_seconds: int = 0
    max_operations: int = 0
    min_interval_seconds: float = 0.0
    max_concurrent: int = 0
    task_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    not_before_epoch: float = 0.0
    credit_liquidity: str = CREDIT_LIQUIDITY_REQUIRED
    approval_ref: str = ""
    note: str = ""


@dataclass(frozen=True)
class MoneyGrant:
    grant_id: str
    version: int
    state: str
    spec: dict[str, Any]
    granted_by: str
    granted_at: str
    revoked_epoch: float | None = None
    revoke_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "version": self.version,
            "state": self.state,
            "spec": dict(self.spec),
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "revoked_epoch": self.revoked_epoch,
            "revoke_reason": self.revoke_reason,
        }


@dataclass(frozen=True)
class MoneyRuleAdjustment:
    flow: str
    asset: AssetIdentity
    scope: str
    new_limit_atomic: int | None
    scope_value: str = ""
    window_seconds: float = 0.0
    cross_asset: bool = False
    note: str = ""


@dataclass(frozen=True)
class MoneyRule:
    rule_key: str
    flow: str
    asset_key: str
    scope: str
    scope_value: str
    window_seconds: float
    limit_atomic: int
    cross_asset: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_key": self.rule_key,
            "flow": self.flow,
            "asset_key": self.asset_key,
            "scope": self.scope,
            "scope_value": self.scope_value,
            "window_seconds": self.window_seconds,
            "limit_atomic": _atomic_text(self.limit_atomic),
            "cross_asset": self.cross_asset,
        }


@dataclass(frozen=True)
class FundingDecision:
    mode: str
    action: str
    reason: str
    available_verified_credit_atomic: int | None = None
    auto_grant_id: str = ""
    topup_atomic: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "action": self.action,
            "reason": self.reason,
            "available_verified_credit_atomic": (
                None
                if self.available_verified_credit_atomic is None
                else _atomic_text(self.available_verified_credit_atomic)
            ),
            "auto_grant_id": self.auto_grant_id,
            "topup_atomic": _atomic_text(self.topup_atomic),
        }


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------


def ensure_money_tables(conn: Any) -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        f"INSERT OR IGNORE INTO {_META_TABLE} (key, value) VALUES ('schema_version', ?)",
        (str(MONEY_SCHEMA_VERSION),),
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RULES_TABLE} (
            rule_key TEXT PRIMARY KEY,
            flow TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_value TEXT NOT NULL DEFAULT '',
            window_seconds REAL NOT NULL DEFAULT 0,
            limit_atomic TEXT NOT NULL,
            cross_asset INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            adjusted_at TEXT NOT NULL,
            adjusted_by TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_BOUNDS_TABLE} (
            from_asset_key TEXT NOT NULL,
            to_asset_key TEXT NOT NULL,
            numerator TEXT NOT NULL,
            denominator TEXT NOT NULL,
            expires_epoch REAL NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            granted_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_asset_key, to_asset_key)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_GRANTS_TABLE} (
            grant_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            spec_digest TEXT NOT NULL,
            granted_by TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            not_before_epoch REAL NOT NULL,
            expires_epoch REAL NOT NULL,
            revoked_epoch REAL,
            revoked_by TEXT NOT NULL DEFAULT '',
            revoke_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LIABILITIES_TABLE} (
            liability_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE,
            operation_kind TEXT NOT NULL,
            state TEXT NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 1,
            attempt INTEGER NOT NULL DEFAULT 0,
            grant_id TEXT NOT NULL,
            grant_version INTEGER NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            agent_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL DEFAULT '',
            provider_id TEXT NOT NULL DEFAULT '',
            provider_account TEXT NOT NULL DEFAULT '',
            model_id TEXT NOT NULL DEFAULT '',
            route TEXT NOT NULL DEFAULT '',
            network TEXT NOT NULL DEFAULT '',
            payer_account TEXT NOT NULL DEFAULT '',
            effect_id TEXT NOT NULL DEFAULT '',
            owner_ref TEXT NOT NULL DEFAULT '',
            instance_id TEXT NOT NULL,
            claim_token TEXT NOT NULL DEFAULT '',
            claim_instance_id TEXT NOT NULL DEFAULT '',
            executor TEXT NOT NULL DEFAULT '',
            spec_digest TEXT NOT NULL,
            correlation_json TEXT NOT NULL DEFAULT '{{}}',
            dispatch_evidence_json TEXT NOT NULL DEFAULT '{{}}',
            created_epoch REAL NOT NULL,
            updated_epoch REAL NOT NULL,
            claimed_epoch REAL,
            dispatched_epoch REAL,
            settled_epoch REAL,
            expires_epoch REAL NOT NULL DEFAULT 0,
            unknown_reason TEXT NOT NULL DEFAULT '',
            close_reason TEXT NOT NULL DEFAULT '',
            over_cap INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for column in ("state", "grant_id", "task_id", "session_id", "provider_id", "effect_id", "owner_ref"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_LIABILITIES_TABLE}_{column} "
            f"ON {_LIABILITIES_TABLE}({column})"
        )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LINES_TABLE} (
            line_id INTEGER PRIMARY KEY AUTOINCREMENT,
            liability_id TEXT NOT NULL,
            flow TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            account TEXT NOT NULL,
            max_atomic TEXT NOT NULL,
            actual_atomic TEXT,
            line_state TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            evidence_id TEXT NOT NULL DEFAULT '',
            created_epoch REAL NOT NULL,
            resolved_epoch REAL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_LINES_TABLE}_liability ON {_LINES_TABLE}(liability_id)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_LINES_TABLE}_flow ON {_LINES_TABLE}(flow, asset_key)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_LINES_TABLE}_account ON {_LINES_TABLE}(account, asset_key)"
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_EVIDENCE_TABLE} (
            liability_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            source TEXT NOT NULL,
            digest TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_epoch REAL NOT NULL,
            PRIMARY KEY (liability_id, evidence_id)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LIQUIDITY_TABLE} (
            account TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            balance_atomic TEXT NOT NULL,
            observed_epoch REAL NOT NULL,
            source TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            recorded_epoch REAL NOT NULL,
            PRIMARY KEY (account, asset_key)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_FUNDING_TABLE} (
            provider_id TEXT NOT NULL,
            provider_account TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            mode TEXT NOT NULL,
            threshold_atomic TEXT NOT NULL,
            topup_atomic TEXT NOT NULL,
            auto_grant_id TEXT NOT NULL DEFAULT '',
            adjusted_at TEXT NOT NULL,
            adjusted_by TEXT NOT NULL,
            PRIMARY KEY (provider_id, provider_account, asset_key)
        )
        """
    )
    from core import service_fee_ledger

    service_fee_ledger.ensure_tables(conn)
    # R1 (one-use spend consent): a narrowly defined consent identity on the grant itself.
    # Empty for every historical/descriptive approval_ref -- the partial index only constrains
    # grants minted AS the consumption of one operator consent, so nothing global changes.
    with suppress(sqlite3.OperationalError):
        conn.execute(f"ALTER TABLE {_GRANTS_TABLE} ADD COLUMN consent_id TEXT NOT NULL DEFAULT ''")
    with suppress(sqlite3.OperationalError):
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_GRANTS_TABLE}_consent_once ON {_GRANTS_TABLE} (consent_id) "
            "WHERE consent_id != ''"
        )


class _RefusalError(Exception):
    """A refusal raised inside a write transaction: the transaction rolls back,
    the refusal itself is journaled in its own transaction, and the caller
    receives the typed `EffectBudgetRefusedError`."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        rule: str = "",
        liability_id: str = "",
        flow: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.rule = rule
        self.liability_id = liability_id
        self.flow = flow
        self.context = dict(context or {})


class _Txn:
    __slots__ = ("conn", "deferred", "instance_id")

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.deferred: EffectBudgetRefusedError | None = None
        self.instance_id = ""


def _note_failure(action: str, exc: BaseException) -> None:
    with _eb._STORE_LOCK:
        _eb._STORE_FAILURES["count"] += 1
        _eb._STORE_FAILURES["last_error"] = f"money {action}: {type(exc).__name__}: {exc}"


def _journal(
    conn: Any,
    kind: str,
    *,
    code: str = "",
    flow: str = "",
    scope: str = "",
    scope_key: str = "",
    liability_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        f"INSERT INTO {_eb._EVENTS_TABLE} (event_kind, code, budget_class, scope, scope_key, "
        "units, reservation_id, detail_json, instance_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (
            str(kind),
            str(code or ""),
            str(flow or ""),
            str(scope or ""),
            str(scope_key or ""),
            str(liability_id or ""),
            json.dumps(detail or {}, sort_keys=True, default=str),
            _eb.current_budget_instance_id(),
            _eb._utcnow_iso(),
        ),
    )


def _journal_refusal_best_effort(conn: Any, refusal: _RefusalError, action: str) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        _eb._ensure_tables(conn)
        _journal(
            conn,
            "money_refused",
            code=refusal.code,
            flow=refusal.flow,
            liability_id=refusal.liability_id,
            detail={
                "action": action,
                "detail": refusal.detail[:500],
                "rule": refusal.rule,
                **refusal.context,
            },
        )
        conn.commit()
    except Exception as exc:
        with suppress(Exception):
            conn.rollback()
        _note_failure("refusal journal", exc)


@contextmanager
def _write_txn(action: str) -> Iterator[_Txn]:
    """One BEGIN IMMEDIATE transaction on the budget store, closed on exit.

    `_RefusalError` rolls back and is journaled on its own; a corrupt row becomes
    MONEY_STORE_CORRUPT; any other store failure becomes
    MONEY_STORE_UNAVAILABLE with nothing changed. `txn.deferred` is raised
    after a successful commit — the pattern for a refusal whose own state
    change (a released never-sent reservation) must persist.
    """
    try:
        conn = _eb._budget_connection()
    except Exception as exc:
        _note_failure(action, exc)
        raise EffectBudgetRefusedError(
            MONEY_STORE_UNAVAILABLE,
            f"the budget store could not be opened for {action} ({type(exc).__name__}); "
            "no money state was read or changed",
        ) from exc
    txn = _Txn(conn)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _eb._ensure_tables(conn)
            ensure_money_tables(conn)
            yield txn
            conn.commit()
        except _RefusalError as refusal:
            with suppress(Exception):
                conn.rollback()
            _journal_refusal_best_effort(conn, refusal, action)
            raise EffectBudgetRefusedError(refusal.code, refusal.detail, rule=refusal.rule) from None
        except EffectBudgetRefusedError:
            with suppress(Exception):
                conn.rollback()
            raise
        except _CorruptRowError as corrupt:
            with suppress(Exception):
                conn.rollback()
            _journal_refusal_best_effort(
                conn,
                _RefusalError(MONEY_STORE_CORRUPT, str(corrupt), liability_id=corrupt.liability_id),
                action,
            )
            raise EffectBudgetRefusedError(
                MONEY_STORE_CORRUPT,
                f"{corrupt}; the {action} is refused rather than reading a corrupt row as zero",
            ) from None
        except Exception as exc:
            with suppress(Exception):
                conn.rollback()
            _note_failure(action, exc)
            raise EffectBudgetRefusedError(
                MONEY_STORE_UNAVAILABLE,
                f"the budget store refused {action} ({type(exc).__name__}); nothing was changed",
            ) from exc
        if txn.deferred is not None:
            raise txn.deferred
    finally:
        with suppress(Exception):
            conn.close()


@contextmanager
def _read_conn(action: str) -> Iterator[Any]:
    try:
        conn = _eb._budget_connection()
    except Exception as exc:
        _note_failure(action, exc)
        raise EffectBudgetRefusedError(
            MONEY_STORE_UNAVAILABLE,
            f"the budget store could not be opened for {action} ({type(exc).__name__})",
        ) from exc
    try:
        try:
            _eb._ensure_tables(conn)
            ensure_money_tables(conn)
            conn.commit()
            yield conn
        except EffectBudgetRefusedError:
            raise
        except _CorruptRowError as corrupt:
            raise EffectBudgetRefusedError(MONEY_STORE_CORRUPT, str(corrupt)) from None
        except Exception as exc:
            _note_failure(action, exc)
            raise EffectBudgetRefusedError(
                MONEY_STORE_UNAVAILABLE,
                f"the budget store could not answer {action} ({type(exc).__name__})",
            ) from exc
    finally:
        with suppress(Exception):
            conn.close()


# ---------------------------------------------------------------------------
# operator authority (the unit law's token, checked inside the transaction)
# ---------------------------------------------------------------------------


def _token_granted_in(conn: Any, token: Any) -> bool:
    if not isinstance(token, OperatorBudgetToken) or not token.token_id:
        return False
    for row in conn.execute(
        f"SELECT detail_json FROM {_eb._EVENTS_TABLE} WHERE event_kind='authority_grant'"
    ).fetchall():
        try:
            detail = json.loads(str(row["detail_json"] or "{}"))
        except Exception:
            continue
        if str(detail.get("token_id") or "") == token.token_id:
            return True
    return False


def _require_operator(conn: Any, token: Any, action: str) -> str:
    """Token durably granted, and no effect scope active: models, skills and
    plugins run inside effect scopes; the operator surface does not."""
    from core.effect_gateway import current_effect_ledger

    if current_effect_ledger() is not None:
        raise _RefusalError(
            _eb.REFUSAL_AUTHORITY,
            f"{action} cannot run inside an active effect scope "
            "(models, skills and plugins run there; the operator surface does not)",
            context={"action": action},
        )
    if not _token_granted_in(conn, token):
        raise _RefusalError(
            _eb.REFUSAL_AUTHORITY,
            f"{action} requires an OperatorBudgetToken with a durable grant record",
            context={"action": action},
        )
    return str(token.token_id)


# ---------------------------------------------------------------------------
# rules and conversion bounds (operator only)
# ---------------------------------------------------------------------------


def _rule_key(flow: str, asset_key: str, scope: str, scope_value: str, window: float, cross: bool) -> str:
    return f"{flow}|{asset_key}|{scope}|{scope_value}|w{int(window)}|x{1 if cross else 0}"


def _rule_from_row(row: Any) -> MoneyRule:
    flow = str(row["flow"])
    scope = str(row["scope"])
    asset_key = str(row["asset_key"])
    if flow not in MONEY_FLOWS or scope not in MONEY_SCOPES or not _asset_key_is_valid(asset_key):
        raise _CorruptRowError(f"money rule {str(row['rule_key'])[:120]!r} is malformed")
    try:
        window = float(row["window_seconds"] or 0.0)
    except (TypeError, ValueError):
        raise _CorruptRowError("money rule window is malformed") from None
    return MoneyRule(
        rule_key=str(row["rule_key"]),
        flow=flow,
        asset_key=asset_key,
        scope=scope,
        scope_value=str(row["scope_value"] or ""),
        window_seconds=window,
        limit_atomic=_atomic_value(row["limit_atomic"], what="money rule limit"),
        cross_asset=bool(int(row["cross_asset"] or 0)),
    )


def apply_operator_money_adjustment(
    token: OperatorBudgetToken, adjustments: list[MoneyRuleAdjustment]
) -> list[MoneyRule]:
    """The only write to the money rule store. `new_limit_atomic=None` removes
    a rule; 0 forbids the flow under that scope."""
    for adjustment in adjustments:
        if not isinstance(adjustment, MoneyRuleAdjustment):
            raise _invalid("adjustments must be MoneyRuleAdjustment values")
        if adjustment.flow not in MONEY_FLOWS:
            raise _invalid(f"{adjustment.flow!r} is not a money flow")
        if adjustment.scope not in MONEY_SCOPES:
            raise _invalid(f"{adjustment.scope!r} is not a money scope")
        if not isinstance(adjustment.asset, AssetIdentity):
            raise _invalid("a money rule needs an AssetIdentity")
        if adjustment.new_limit_atomic is not None:
            require_atomic(adjustment.new_limit_atomic, what="money rule limit", allow_zero=True)
        if float(adjustment.window_seconds) < 0:
            raise _invalid("window_seconds cannot be negative")
        _clean_ident(adjustment.scope_value, what="scope_value")
    applied: list[MoneyRule] = []
    with _write_txn("money rule adjustment") as txn:
        token_id = _require_operator(txn.conn, token, "money rule adjustment")
        for adjustment in adjustments:
            window = float(adjustment.window_seconds)
            scope_value = str(adjustment.scope_value or "").strip()
            key = _rule_key(
                adjustment.flow,
                adjustment.asset.key,
                adjustment.scope,
                scope_value,
                window,
                bool(adjustment.cross_asset),
            )
            if adjustment.new_limit_atomic is None:
                txn.conn.execute(f"DELETE FROM {_RULES_TABLE} WHERE rule_key=?", (key,))
                action = "removed"
            else:
                txn.conn.execute(
                    f"INSERT INTO {_RULES_TABLE} (rule_key, flow, asset_key, scope, scope_value, "
                    "window_seconds, limit_atomic, cross_asset, note, adjusted_at, adjusted_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(rule_key) DO UPDATE SET "
                    "limit_atomic=excluded.limit_atomic, note=excluded.note, "
                    "adjusted_at=excluded.adjusted_at, adjusted_by=excluded.adjusted_by",
                    (
                        key,
                        adjustment.flow,
                        adjustment.asset.key,
                        adjustment.scope,
                        scope_value,
                        window,
                        _atomic_text(adjustment.new_limit_atomic),
                        1 if adjustment.cross_asset else 0,
                        str(adjustment.note or "")[:200],
                        _eb._utcnow_iso(),
                        token_id,
                    ),
                )
                applied.append(
                    MoneyRule(
                        rule_key=key,
                        flow=adjustment.flow,
                        asset_key=adjustment.asset.key,
                        scope=adjustment.scope,
                        scope_value=scope_value,
                        window_seconds=window,
                        limit_atomic=int(adjustment.new_limit_atomic),
                        cross_asset=bool(adjustment.cross_asset),
                    )
                )
                action = "set"
            _journal(
                txn.conn,
                "money_rule_adjusted",
                flow=adjustment.flow,
                scope=adjustment.scope,
                scope_key=scope_value,
                detail={
                    "rule_key": key,
                    "action": action,
                    "limit_atomic": (
                        None
                        if adjustment.new_limit_atomic is None
                        else _atomic_text(adjustment.new_limit_atomic)
                    ),
                    "token_id": token_id,
                    "note": str(adjustment.note or "")[:200],
                },
            )
    return applied


def money_rules() -> list[MoneyRule]:
    with _read_conn("money rules read") as conn:
        return [_rule_from_row(row) for row in conn.execute(f"SELECT * FROM {_RULES_TABLE}").fetchall()]


def set_conversion_bound(
    token: OperatorBudgetToken,
    *,
    from_asset: AssetIdentity,
    to_asset: AssetIdentity,
    numerator: int,
    denominator: int,
    expires_epoch: float,
    note: str = "",
) -> dict[str, Any]:
    """An operator-declared conservative bound: one atomic unit of `from_asset`
    is worth AT MOST numerator/denominator atomic units of `to_asset` until
    `expires_epoch`. Used only by cross-asset rules, rounded up."""
    if not isinstance(from_asset, AssetIdentity) or not isinstance(to_asset, AssetIdentity):
        raise _invalid("conversion bounds need AssetIdentity values")
    if from_asset.key == to_asset.key:
        raise _invalid("a conversion bound needs two different assets")
    require_atomic(numerator, what="bound numerator")
    require_atomic(denominator, what="bound denominator")
    now = _now()
    if float(expires_epoch) <= now or float(expires_epoch) > now + MAX_GRANT_LIFETIME_SECONDS:
        raise _invalid("a conversion bound must expire in the future and within the grant lifetime cap")
    with _write_txn("conversion bound") as txn:
        token_id = _require_operator(txn.conn, token, "conversion bound")
        txn.conn.execute(
            f"INSERT INTO {_BOUNDS_TABLE} (from_asset_key, to_asset_key, numerator, denominator, "
            "expires_epoch, note, granted_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(from_asset_key, to_asset_key) DO UPDATE SET numerator=excluded.numerator, "
            "denominator=excluded.denominator, expires_epoch=excluded.expires_epoch, "
            "note=excluded.note, granted_by=excluded.granted_by, created_at=excluded.created_at",
            (
                from_asset.key,
                to_asset.key,
                _atomic_text(numerator),
                _atomic_text(denominator),
                float(expires_epoch),
                str(note or "")[:200],
                token_id,
                _eb._utcnow_iso(),
            ),
        )
        _journal(
            txn.conn,
            "money_conversion_bound_set",
            detail={
                "from": from_asset.key,
                "to": to_asset.key,
                "numerator": _atomic_text(numerator),
                "denominator": _atomic_text(denominator),
                "expires_epoch": float(expires_epoch),
                "token_id": token_id,
            },
        )
    return {
        "from_asset_key": from_asset.key,
        "to_asset_key": to_asset.key,
        "numerator": _atomic_text(numerator),
        "denominator": _atomic_text(denominator),
        "expires_epoch": float(expires_epoch),
    }


def conversion_bound_view(from_asset: AssetIdentity, to_asset: AssetIdentity) -> dict[str, Any] | None:
    """The operator's declared bound for ``from_asset`` -> ``to_asset`` as public facts (numerator, denominator,
    expiry), or None when none is declared or it has expired. A read; nothing is widened here."""
    if not isinstance(from_asset, AssetIdentity) or not isinstance(to_asset, AssetIdentity):
        raise _invalid("conversion bounds need AssetIdentity values")
    now = _now()
    with _read_conn("conversion bound read") as conn:
        row = conn.execute(
            f"SELECT numerator, denominator, expires_epoch, note FROM {_BOUNDS_TABLE} WHERE from_asset_key=? AND to_asset_key=?",
            (from_asset.key, to_asset.key),
        ).fetchone()
    if row is None:
        return None
    try:
        expires = float(row["expires_epoch"])
    except (TypeError, ValueError):
        return None
    if expires <= now:
        return None
    return {
        "from_asset_key": from_asset.key, "to_asset_key": to_asset.key,
        "numerator": _atomic_value(row["numerator"], what="conversion bound numerator"), "denominator": _atomic_value(row["denominator"], what="conversion bound denominator"),
        "expires_epoch": expires, "note": str(row["note"] or ""),
    }


def convert_up_bound(amount: int, from_asset: AssetIdentity, to_asset: AssetIdentity) -> int | None:
    """``amount`` atomic units of ``from_asset`` valued in ``to_asset`` under the operator's conservative bound,
    rounded UP; None when no unexpired bound exists (the caller must treat the value as unknown)."""
    if not isinstance(from_asset, AssetIdentity) or not isinstance(to_asset, AssetIdentity):
        raise _invalid("conversion bounds need AssetIdentity values")
    if from_asset.key == to_asset.key:
        return require_atomic(amount, what="amount", allow_zero=True)
    with _read_conn("conversion bound read") as conn:
        return _convert_up(conn, require_atomic(amount, what="amount", allow_zero=True), from_asset.key, to_asset.key, _now())


def _convert_up(conn: Any, amount: int, from_key: str, to_key: str, now: float) -> int | None:
    if from_key == to_key:
        return int(amount)
    row = conn.execute(
        f"SELECT numerator, denominator, expires_epoch FROM {_BOUNDS_TABLE} "
        "WHERE from_asset_key=? AND to_asset_key=?",
        (from_key, to_key),
    ).fetchone()
    if row is None:
        return None
    try:
        expires = float(row["expires_epoch"])
    except (TypeError, ValueError):
        raise _CorruptRowError("conversion bound expiry is malformed") from None
    if expires <= now:
        return None
    numerator = _atomic_value(row["numerator"], what="conversion bound numerator")
    denominator = _atomic_value(row["denominator"], what="conversion bound denominator")
    if denominator == 0:
        raise _CorruptRowError("conversion bound denominator is zero")
    return _ceil_div(int(amount) * numerator, denominator)


# ---------------------------------------------------------------------------
# grants (consumable economic authority)
# ---------------------------------------------------------------------------


def _validate_grant_spec(spec: MoneyGrantSpec, now: float) -> dict[str, Any]:
    if not isinstance(spec, MoneyGrantSpec):
        raise _invalid("grant_money_authority needs a MoneyGrantSpec")
    if spec.kind not in GRANT_KINDS:
        raise _invalid(f"{spec.kind!r} is not a grant kind")
    operations = tuple(dict.fromkeys(str(op) for op in (spec.operation_kinds or ())))
    if not operations:
        raise _invalid("a grant must name the operation kinds it authorizes")
    allowed = _GRANT_KIND_OPERATIONS[spec.kind]
    for op in operations:
        if op not in OPERATION_KINDS:
            raise _invalid(f"{op!r} is not an operation kind")
        if op not in allowed:
            raise _invalid(f"a {spec.kind} grant cannot authorize {op}")
    if spec.kind == GRANT_SINGLE_PAYMENT and len(operations) != 1:
        raise _invalid("a single_payment grant authorizes exactly one operation kind")
    provider_id = _clean_ident(spec.provider_id, what="provider_id")
    if not provider_id:
        raise _invalid("a grant must bind a provider (or payee authority) id")
    if not isinstance(spec.asset, AssetIdentity):
        raise _invalid("a grant needs its principal AssetIdentity")
    max_total = require_atomic(spec.max_total_atomic, what="max_total_atomic")
    per_operation = require_atomic(spec.per_operation_max_atomic, what="per_operation_max_atomic")
    if per_operation > max_total:
        raise _invalid("per_operation_max_atomic cannot exceed max_total_atomic")
    try:
        expires = float(spec.expires_epoch)
        not_before = float(spec.not_before_epoch or 0.0)
        min_interval = float(spec.min_interval_seconds or 0.0)
    except (TypeError, ValueError):
        raise _invalid("grant times must be numbers") from None
    provider_budget = spec.kind == GRANT_PROVIDER_BUDGET
    if expires <= now and not (provider_budget and expires == 0):
        raise _invalid("a grant must expire in the future")
    if expires > now + MAX_GRANT_LIFETIME_SECONDS:
        raise _invalid("a grant cannot outlive the lifetime cap; there is no standing money permission")
    if not_before and expires and not_before >= expires:
        raise _invalid("not_before_epoch must be before expires_epoch")
    if min_interval < 0:
        raise _invalid("min_interval_seconds cannot be negative")
    for name in ("max_operations", "max_concurrent"):
        value = getattr(spec, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _invalid(f"{name} must be a non-negative int")
    max_operations = int(spec.max_operations)
    max_concurrent = int(spec.max_concurrent)
    if spec.kind == GRANT_SINGLE_PAYMENT:
        max_operations = 1
        max_concurrent = 1
    if spec.kind == GRANT_AUTO_TOPUP:
        if min_interval <= 0:
            raise _invalid("an auto_topup grant needs a frequency limit (min_interval_seconds > 0)")
        max_concurrent = 1
    inference = any(_OPERATION_SCHEMA[op]["inference"] for op in operations)
    wallet = any(_OPERATION_SCHEMA[op]["wallet"] for op in operations)
    credit = any(op in (OP_INFERENCE_PREPAID, OP_PROVIDER_TOPUP) for op in operations)
    models = tuple(dict.fromkeys(_clean_ident(m, what="model") for m in (spec.models or ()) if str(m).strip()))
    routes = tuple(dict.fromkeys(_clean_ident(r, what="route") for r in (spec.routes or ()) if str(r).strip()))
    if isinstance(spec.renewal_seconds, bool) or spec.renewal_seconds not in (0, 86400):
        raise _invalid("renewal_seconds must be 0 or 86400")
    if spec.renewal_seconds and not provider_budget:
        raise _invalid("renewal belongs only to an explicit provider budget")
    if provider_budget and (models or routes):
        raise _invalid("provider budgets cover all provider models; route prices remain separately enforced")
    if inference and not provider_budget and (not models or not routes):
        raise _invalid("an inference grant must list its exact models and routes")
    network = _clean_ident(spec.network, what="network")
    payer = _clean_account(spec.payer_account, what="payer_account", required=wallet)
    if wallet and not network:
        raise _invalid("a wallet-debit grant must bind its network")
    provider_account = _clean_account(spec.provider_account, what="provider_account", required=credit)
    fee_asset_key = ""
    max_fee_total = 0
    per_operation_fee = 0
    if spec.fee_asset is not None:
        if not isinstance(spec.fee_asset, AssetIdentity):
            raise _invalid("fee_asset must be an AssetIdentity")
        fee_asset_key = spec.fee_asset.key
        max_fee_total = require_atomic(spec.max_fee_total_atomic, what="max_fee_total_atomic", allow_zero=True)
        per_operation_fee = require_atomic(
            spec.per_operation_max_fee_atomic, what="per_operation_max_fee_atomic", allow_zero=True
        )
        if per_operation_fee > max_fee_total:
            raise _invalid("per_operation_max_fee_atomic cannot exceed max_fee_total_atomic")
    elif spec.max_fee_total_atomic or spec.per_operation_max_fee_atomic:
        raise _invalid("fee maxima need a fee_asset")
    identity = {
        "task_id": _clean_ident(spec.task_id, what="task_id"),
        "session_id": _clean_ident(spec.session_id, what="session_id"),
        "agent_id": _clean_ident(spec.agent_id, what="agent_id"),
    }
    if spec.kind == GRANT_TASK_ENVELOPE and not (identity["task_id"] or identity["session_id"]):
        raise _invalid("a task_envelope grant must bind a task or a session")
    if spec.credit_liquidity not in (CREDIT_LIQUIDITY_REQUIRED, CREDIT_LIQUIDITY_NOT_REQUIRED):
        raise _invalid("credit_liquidity must be 'required' or 'not_required'")
    approval_ref = str(spec.approval_ref or "")
    if len(approval_ref) > 128 or "://" in approval_ref:
        raise _invalid("approval_ref must be a short opaque reference")
    return {
        **({"renewal_seconds": spec.renewal_seconds, "period_anchor": now} if provider_budget else {}),
        "kind": spec.kind,
        "operation_kinds": list(operations),
        "provider_id": provider_id,
        "provider_account": provider_account,
        "models": list(models),
        "routes": list(routes),
        "network": network,
        "asset_key": spec.asset.key,
        "payer_account": payer,
        "max_total_atomic": _atomic_text(max_total),
        "per_operation_max_atomic": _atomic_text(per_operation),
        "fee_asset_key": fee_asset_key,
        "max_fee_total_atomic": _atomic_text(max_fee_total),
        "per_operation_max_fee_atomic": _atomic_text(per_operation_fee),
        "max_operations": max_operations,
        "min_interval_seconds": min_interval,
        "max_concurrent": max_concurrent,
        **identity,
        "not_before_epoch": not_before,
        "expires_epoch": expires,
        "credit_liquidity": spec.credit_liquidity,
        "approval_ref": approval_ref,
        "note": str(spec.note or "")[:200],
    }


def _canonical_digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_GRANT_SPEC_FIELDS = (
    "kind",
    "operation_kinds",
    "provider_id",
    "provider_account",
    "models",
    "routes",
    "network",
    "asset_key",
    "payer_account",
    "max_total_atomic",
    "per_operation_max_atomic",
    "fee_asset_key",
    "max_fee_total_atomic",
    "per_operation_max_fee_atomic",
    "max_operations",
    "min_interval_seconds",
    "max_concurrent",
    "task_id",
    "session_id",
    "agent_id",
    "not_before_epoch",
    "expires_epoch",
    "credit_liquidity",
    "approval_ref",
    "note",
)


def _grant_from_row(row: Any) -> MoneyGrant:
    grant_id = str(row["grant_id"])
    try:
        spec = json.loads(str(row["spec_json"]))
    except Exception:
        raise _CorruptRowError(f"grant {grant_id!r} spec is unreadable") from None
    if not isinstance(spec, dict) or any(name not in spec for name in _GRANT_SPEC_FIELDS):
        raise _CorruptRowError(f"grant {grant_id!r} spec is incomplete")
    if _canonical_digest(spec) != str(row["spec_digest"]):
        raise _CorruptRowError(f"grant {grant_id!r} spec does not match its digest")
    state = str(row["state"])
    if state not in (GRANT_ACTIVE, GRANT_REVOKED) or spec.get("kind") not in GRANT_KINDS:
        raise _CorruptRowError(f"grant {grant_id!r} state or kind is malformed")
    for name in ("max_total_atomic", "per_operation_max_atomic", "max_fee_total_atomic", "per_operation_max_fee_atomic"):
        _atomic_value(spec.get(name), what=f"grant {name}")
    if not isinstance(spec.get("operation_kinds"), list) or not isinstance(spec.get("models"), list) or not isinstance(spec.get("routes"), list):
        raise _CorruptRowError(f"grant {grant_id!r} lists are malformed")
    try:
        version = int(row["version"])
        revoked_epoch = None if row["revoked_epoch"] is None else float(row["revoked_epoch"])
        float(spec["expires_epoch"])
        float(spec["not_before_epoch"])
        float(spec["min_interval_seconds"])
        int(spec["max_operations"])
        int(spec["max_concurrent"])
    except (TypeError, ValueError):
        raise _CorruptRowError(f"grant {grant_id!r} numbers are malformed") from None
    return MoneyGrant(
        grant_id=grant_id,
        version=version,
        state=state,
        spec=spec,
        granted_by=str(row["granted_by"]),
        granted_at=str(row["granted_at"]),
        revoked_epoch=revoked_epoch,
        revoke_reason=str(row["revoke_reason"] or ""),
    )


def grant_money_authority(token: OperatorBudgetToken, spec: MoneyGrantSpec, *, consent_id: str = "") -> MoneyGrant:
    """Mint one bounded economic authorization. Operator token required;
    refused inside an effect scope; every refusal and grant is journaled.

    ``consent_id`` is the ONE-USE consumption contract for an operator consent (a Settings spend
    approval): when given, exactly one grant may ever exist for it. The check, the insert and the
    partial unique index all live in the same BEGIN IMMEDIATE transaction -- concurrent confirms,
    retries after a lost reply and replays after revocation all return the SAME grant (whatever
    its current state) and never mint fresh spend authority. Empty (the default) keeps the
    historical behaviour for every caller that uses ``approval_ref`` as a descriptive reference.
    """
    now = _now()
    canonical = _validate_grant_spec(spec, now)
    clean_consent = str(consent_id or "").strip()
    grant_id = "mga:" + secrets.token_hex(10)
    digest = _canonical_digest(canonical)
    while True:
        try:
            with _write_txn("money grant") as txn:
                token_id = _require_operator(txn.conn, token, "money grant")
                granted_at = _eb._utcnow_iso()
                if clean_consent:
                    existing = txn.conn.execute(
                        f"SELECT * FROM {_GRANTS_TABLE} WHERE consent_id=?", (clean_consent,)
                    ).fetchone()
                    if existing is not None:
                        # Idempotent consumption: the consent already bought exactly this grant.
                        return _grant_from_row(existing)
                if canonical["kind"] == GRANT_PROVIDER_BUDGET:
                    carried = txn.conn.execute(
                        f"SELECT m.liability_id FROM {_LIABILITIES_TABLE} m JOIN {_GRANTS_TABLE} g ON g.grant_id=m.grant_id "
                        f"WHERE m.state IN ({','.join('?' for _ in HELD_STATES)}) "
                        "AND json_extract(g.spec_json, '$.provider_id')=? AND json_extract(g.spec_json, '$.provider_account')=?",
                        (*HELD_STATES, canonical["provider_id"], canonical["provider_account"]),
                    ).fetchall()
                    canonical["carried_liabilities"] = [str(row["liability_id"]) for row in carried]
                    digest = _canonical_digest(canonical)
                    for old_row in txn.conn.execute(f"SELECT * FROM {_GRANTS_TABLE} WHERE state=?", (GRANT_ACTIVE,)).fetchall():
                        old = _grant_from_row(old_row)
                        if old.spec["provider_id"] == canonical["provider_id"] and old.spec["provider_account"] == canonical["provider_account"] and OP_INFERENCE_PREPAID in old.spec["operation_kinds"]:
                            txn.conn.execute(f"UPDATE {_GRANTS_TABLE} SET state=?, revoked_epoch=? WHERE grant_id=?", (GRANT_REVOKED, now, old.grant_id))
                            _journal(txn.conn, "money_grant_replaced", detail={"grant_id": old.grant_id, "replacement": grant_id})
                txn.conn.execute(
                    f"INSERT INTO {_GRANTS_TABLE} (grant_id, version, kind, state, spec_json, spec_digest, "
                    "granted_by, granted_at, not_before_epoch, expires_epoch, consent_id) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        grant_id,
                        canonical["kind"],
                        GRANT_ACTIVE,
                        json.dumps(canonical, sort_keys=True),
                        digest,
                        token_id,
                        granted_at,
                        float(canonical["not_before_epoch"]),
                        float(canonical["expires_epoch"]),
                        clean_consent,
                    ),
                )
                _journal(
                    txn.conn,
                    "money_grant_minted",
                    detail={
                        "grant_id": grant_id,
                        "kind": canonical["kind"],
                        "provider_id": canonical["provider_id"],
                        "operation_kinds": canonical["operation_kinds"],
                        "asset_key": canonical["asset_key"],
                        "max_total_atomic": canonical["max_total_atomic"],
                        "expires_epoch": canonical["expires_epoch"],
                        "token_id": token_id,
                        **({"consent_id": clean_consent} if clean_consent else {}),
                    },
                )
        except sqlite3.IntegrityError:
            # The partial unique index lost a one-consent race: the other writer's grant IS the
            # consent's grant. Re-read and return it; the loop runs at most twice.
            if not clean_consent:
                raise
            continue
        break
    return MoneyGrant(
        grant_id=grant_id,
        version=1,
        state=GRANT_ACTIVE,
        spec=canonical,
        granted_by=token_id,
        granted_at=granted_at,
    )


def revoke_money_authority(token: OperatorBudgetToken, grant_id: str, *, reason: str = "") -> MoneyGrant:
    """Revoke a grant: new reservations and dispatch claims under it are
    refused from this commit on. Liabilities it already authorized keep their
    state — a payment that may have gone out is still reconciled."""
    with _write_txn("money grant revocation") as txn:
        token_id = _require_operator(txn.conn, token, "money grant revocation")
        row = txn.conn.execute(
            f"SELECT * FROM {_GRANTS_TABLE} WHERE grant_id=?", (str(grant_id),)
        ).fetchone()
        if row is None:
            raise _RefusalError(MONEY_AUTHORITY_INVALID, f"grant {str(grant_id)[:80]!r} does not exist")
        grant = _grant_from_row(row)
        if grant.state == GRANT_REVOKED:
            return grant
        now = _now()
        txn.conn.execute(
            f"UPDATE {_GRANTS_TABLE} SET state=?, version=version+1, revoked_epoch=?, revoked_by=?, "
            "revoke_reason=? WHERE grant_id=? AND state=?",
            (GRANT_REVOKED, now, token_id, str(reason or "")[:200], grant.grant_id, GRANT_ACTIVE),
        )
        held = txn.conn.execute(
            f"SELECT COUNT(*) AS n FROM {_LIABILITIES_TABLE} WHERE grant_id=? AND state IN (?, ?, ?, ?)",
            (grant.grant_id, *HELD_STATES),
        ).fetchone()
        _journal(
            txn.conn,
            "money_grant_revoked",
            detail={
                "grant_id": grant.grant_id,
                "token_id": token_id,
                "reason": str(reason or "")[:200],
                "held_liabilities_retained": int(held["n"] or 0),
            },
        )
        return replace(
            grant,
            version=grant.version + 1,
            state=GRANT_REVOKED,
            revoked_epoch=now,
            revoke_reason=str(reason or "")[:200],
        )


def money_grant(grant_id: str) -> MoneyGrant | None:
    with _read_conn("money grant read") as conn:
        row = conn.execute(f"SELECT * FROM {_GRANTS_TABLE} WHERE grant_id=?", (str(grant_id),)).fetchone()
        return _grant_from_row(row) if row is not None else None


def money_grants(*, active_only: bool = False) -> list[MoneyGrant]:
    with _read_conn("money grants read") as conn:
        grants = [_grant_from_row(row) for row in conn.execute(f"SELECT * FROM {_GRANTS_TABLE}").fetchall()]
    if active_only:
        now = _now()
        grants = [
            grant
            for grant in grants
            if grant.state == GRANT_ACTIVE and (not float(grant.spec["expires_epoch"]) or float(grant.spec["expires_epoch"]) > now)
        ]
    return grants


def grant_headroom(grant_id: str) -> dict[str, Any] | None:
    """What a grant can still authorize, from the SAME usage arithmetic a reservation applies (``_grant_usage``): the
    principal and network-fee authority left, and the operations left (``None`` where the grant does not count them).
    A read for callers choosing between consents -- it never reserves, and the reservation transaction still decides.
    None for an unknown grant."""
    grant = money_grant(grant_id)
    if grant is None:
        return None
    spec = grant.spec
    with _read_conn("grant headroom read") as conn:
        usage = _grant_usage(conn, grant)
    max_total = _atomic_value(spec["max_total_atomic"], what="grant total")
    fees_left = None
    if spec.get("fee_asset_key"):
        fees_left = max(0, _atomic_value(spec["max_fee_total_atomic"], what="grant fee total") - int(usage["fees"]))
    max_operations = int(spec.get("max_operations") or 0)
    return {
        "grant_id": grant.grant_id,
        "state": grant.state,
        "principal_left_atomic": max(0, max_total - int(usage["principal"])),
        "fees_left_atomic": fees_left,
        "operations_left": max(0, max_operations - int(usage["operations"])) if max_operations else None,
        "open_operations": int(usage["concurrent"]),
    }


# ---------------------------------------------------------------------------
# liabilities: validation helpers
# ---------------------------------------------------------------------------


def _validate_request(request: LiabilityRequest, now: float) -> dict[str, Any]:
    if not isinstance(request, LiabilityRequest):
        raise _invalid("reserve_liability needs a LiabilityRequest")
    operation_id = str(request.operation_id or "").strip()
    if not _OPERATION_ID_RE.fullmatch(operation_id):
        raise _invalid("operation_id must be a durable id of 1..200 safe characters")
    schema = _OPERATION_SCHEMA.get(str(request.operation_kind or ""))
    if schema is None:
        raise _invalid(f"{str(request.operation_kind)[:40]!r} is not an operation kind")
    grant_id = str(request.grant_id or "").strip()
    if not grant_id:
        raise EffectBudgetRefusedError(
            MONEY_AUTHORITY_REQUIRED,
            "every monetary liability needs an operator-granted economic authorization (grant_id)",
        )
    if not isinstance(request.identity, MoneyIdentity):
        raise _invalid("identity must be a MoneyIdentity")
    lines = tuple(request.lines or ())
    if not lines or not all(isinstance(line, MoneyLine) for line in lines):
        raise _invalid("a liability needs MoneyLine values")
    by_flow: dict[str, MoneyLine] = {}
    for line in lines:
        if line.flow in by_flow:
            raise _invalid(f"{line.flow} appears twice")
        by_flow[line.flow] = line
    required = set(schema["required"])
    optional = set(schema["optional"])
    missing = required - set(by_flow)
    extra = set(by_flow) - required - optional
    if missing:
        raise _invalid(f"{request.operation_kind} needs lines for {sorted(missing)}")
    if extra:
        raise _invalid(f"{request.operation_kind} does not carry {sorted(extra)}")
    for left, right in schema["equal"]:
        if by_flow[left].asset.key != by_flow[right].asset.key or by_flow[left].max_atomic != by_flow[right].max_atomic:
            raise _invalid(f"{left} and {right} must share one asset and one maximum")
    provider_account = _clean_account(
        request.provider_account,
        what="provider_account",
        required=request.operation_kind in (OP_INFERENCE_PREPAID, OP_INFERENCE_X402, OP_PROVIDER_TOPUP),
    )
    payer = _clean_account(request.payer_account, what="payer_account", required=bool(schema["wallet"]))
    network = _clean_ident(request.network, what="network")
    if schema["wallet"]:
        if not network:
            raise _invalid(f"{request.operation_kind} must name its payment network")
        for flow in WALLET_DEBIT_FLOWS & set(by_flow):
            if by_flow[flow].account != payer:
                raise _invalid(f"{flow} must debit the payer account")
    for flow in (FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT, FLOW_PROVIDER_CREDIT_CREDIT):
        if flow in by_flow and by_flow[flow].account != provider_account:
            raise _invalid(f"{flow} must name the provider account")
    model_id = _clean_ident(request.model_id, what="model_id")
    route = _clean_ident(request.route, what="route")
    if schema["inference"] and (not model_id or not route):
        raise _invalid(f"{request.operation_kind} must name its model and route")
    if not request.identity.provider_id:
        raise _invalid("a liability must name its provider (or payee authority) id")
    try:
        expires = float(request.expires_epoch or 0.0)
    except (TypeError, ValueError):
        raise _invalid("expires_epoch must be a number") from None
    if expires and expires <= now:
        raise EffectBudgetRefusedError(
            MONEY_RESERVATION_EXPIRED, "the operation's quote or reservation window already expired"
        )
    correlation: dict[str, str] = {}
    raw_correlation = request.correlation or {}
    if not isinstance(raw_correlation, Mapping) or len(raw_correlation) > 16:
        raise _invalid("correlation must be a mapping of at most 16 public identifiers")
    for key, value in raw_correlation.items():
        name = str(key)
        text = str(value)
        if not _CORRELATION_KEY_RE.fullmatch(name) or any(word in name for word in _SECRET_KEY_WORDS):
            raise _invalid(f"correlation key {name[:40]!r} is not a public identifier name")
        if len(text) > 256 or "://" in text:
            raise _invalid(f"correlation value for {name!r} must be a short public identifier, never a URL")
        correlation[name] = text
    economic_terms = {
        "operation_kind": request.operation_kind,
        "grant_id": grant_id,
        "lines": sorted((line.to_dict() for line in lines), key=lambda item: item["flow"]),
        "identity": request.identity.to_dict(),
        "provider_account": provider_account,
        "model_id": model_id,
        "route": route,
        "network": network,
        "payer_account": payer,
    }
    return {
        "operation_id": operation_id,
        "schema": schema,
        "grant_id": grant_id,
        "by_flow": by_flow,
        "provider_account": provider_account,
        "payer_account": payer,
        "network": network,
        "model_id": model_id,
        "route": route,
        "expires_epoch": expires,
        "correlation": correlation,
        "spec_digest": _canonical_digest(economic_terms),
        "effect_id": _clean_ident(request.effect_id, what="effect_id"),
        "owner_ref": _clean_ident(request.owner_ref, what="owner_ref"),
    }


def _line_amount(row: Any, *, liability_id: str) -> int:
    """What one persisted line holds against ceilings: its exact actual when
    proven, otherwise its maximum. Corrupt rows raise; they never read as 0."""
    state = str(row["line_state"])
    if state not in LINE_STATES:
        raise _CorruptRowError(f"line state {state[:20]!r} is malformed", liability_id=liability_id)
    maximum = _atomic_value(row["max_atomic"], what="line maximum", liability_id=liability_id)
    if state == LINE_EXACT:
        return _atomic_value(row["actual_atomic"], what="line actual", liability_id=liability_id)
    return maximum


def _state_of(row: Any, *, liability_id: str) -> str:
    state = str(row["state"])
    if state not in LIABILITY_STATES:
        raise _CorruptRowError(f"liability state {state[:20]!r} is malformed", liability_id=liability_id)
    return state


def _scope_value(scope: str, facts: dict[str, Any], identity: MoneyIdentity, line: MoneyLine) -> str | None:
    if scope == _eb.SCOPE_GLOBAL:
        return "global"
    if scope == SCOPE_ACCOUNT:
        return line.account or None
    value = {
        _eb.SCOPE_REQUEST: identity.request_id,
        _eb.SCOPE_TURN: identity.turn_id,
        _eb.SCOPE_TASK: identity.task_id,
        _eb.SCOPE_AGENT: identity.agent_id,
        _eb.SCOPE_SESSION: identity.session_id,
        _eb.SCOPE_PROJECT: identity.project_key,
        _eb.SCOPE_PROVIDER: identity.provider_id,
    }.get(scope, "")
    return value or None


def _rule_usage(conn: Any, rule: MoneyRule, scope_value: str, now: float) -> int:
    column = _SCOPE_COLUMNS[rule.scope]
    sql = (
        f"SELECT l.max_atomic, l.actual_atomic, l.line_state, l.asset_key, m.state, m.settled_epoch, "
        f"m.liability_id FROM {_LINES_TABLE} l JOIN {_LIABILITIES_TABLE} m "
        "ON m.liability_id = l.liability_id WHERE l.flow = ? AND m.state NOT IN (?, ?)"
    )
    params: list[Any] = [rule.flow, *CLOSED_STATES]
    if column:
        sql += f" AND {column} = ?"
        params.append(scope_value)
    if not rule.cross_asset:
        sql += " AND l.asset_key = ?"
        params.append(rule.asset_key)
    cutoff = now - float(rule.window_seconds) if rule.window_seconds > 0 else None
    total = 0
    for row in conn.execute(sql, params).fetchall():
        liability_id = str(row["liability_id"])
        state = _state_of(row, liability_id=liability_id)
        if cutoff is not None and state == LIABILITY_SETTLED:
            try:
                settled_epoch = float(row["settled_epoch"] or 0.0)
            except (TypeError, ValueError):
                raise _CorruptRowError("settled_epoch is malformed", liability_id=liability_id) from None
            if settled_epoch < cutoff:
                continue
        amount = _line_amount(row, liability_id=liability_id)
        asset_key = str(row["asset_key"])
        if asset_key != rule.asset_key:
            converted = _convert_up(conn, amount, asset_key, rule.asset_key, now)
            if converted is None:
                raise _RefusalError(
                    MONEY_CONVERSION_UNAVAILABLE,
                    f"cross-asset rule {rule.rule_key} holds {asset_key} with no current conversion "
                    "bound; the total cannot be compared, so it is refused",
                    rule=rule.rule_key,
                    flow=rule.flow,
                )
            amount = converted
        total += amount
    return total


def _check_rules(conn: Any, facts: dict[str, Any], identity: MoneyIdentity, now: float) -> None:
    by_flow: dict[str, MoneyLine] = facts["by_flow"]
    placeholders = ",".join("?" for _ in by_flow)
    rows = conn.execute(
        f"SELECT * FROM {_RULES_TABLE} WHERE flow IN ({placeholders})", tuple(by_flow)
    ).fetchall()
    rules = sorted(
        (_rule_from_row(row) for row in rows),
        key=lambda rule: (MONEY_SCOPES.index(rule.scope), rule.rule_key),
    )
    for rule in rules:
        line = by_flow[rule.flow]
        if line.asset.key == rule.asset_key:
            need = line.max_atomic
        elif rule.cross_asset:
            converted = _convert_up(conn, line.max_atomic, line.asset.key, rule.asset_key, now)
            if converted is None:
                raise _RefusalError(
                    MONEY_CONVERSION_UNAVAILABLE,
                    f"rule {rule.rule_key} spans assets but no current conversion bound maps "
                    f"{line.asset.key} to {rule.asset_key}; no rate is guessed",
                    rule=rule.rule_key,
                    flow=rule.flow,
                )
            need = converted
        else:
            continue
        scope_value = _scope_value(rule.scope, facts, identity, line)
        if scope_value is None:
            continue
        if rule.scope_value and rule.scope_value != scope_value:
            continue
        used = _rule_usage(conn, rule, scope_value, now)
        if used + need > rule.limit_atomic:
            raise _RefusalError(
                MONEY_BUDGET_EXCEEDED,
                f"{rule.flow} {used} held/settled + {need} requested would exceed "
                f"{rule.limit_atomic} under {rule.rule_key}",
                rule=rule.rule_key,
                flow=rule.flow,
                context={
                    "used_atomic": _atomic_text(used),
                    "requested_atomic": _atomic_text(need),
                    "limit_atomic": _atomic_text(rule.limit_atomic),
                    "scope_value": scope_value,
                },
            )


def _grant_row(conn: Any, grant_id: str) -> MoneyGrant:
    row = conn.execute(f"SELECT * FROM {_GRANTS_TABLE} WHERE grant_id=?", (grant_id,)).fetchone()
    if row is None:
        raise _RefusalError(MONEY_AUTHORITY_INVALID, f"grant {grant_id[:80]!r} does not exist")
    return _grant_from_row(row)


def _grant_usage(conn: Any, grant: MoneyGrant, *, exclude_liability: str = "") -> dict[str, Any]:
    carried = tuple(grant.spec.get("carried_liabilities") or ())
    rows = conn.execute(
        f"SELECT m.liability_id, m.state, m.operation_kind, m.created_epoch, m.settled_epoch, l.flow, l.max_atomic, "
        f"l.actual_atomic, l.line_state FROM {_LIABILITIES_TABLE} m LEFT JOIN {_LINES_TABLE} l "
        "ON l.liability_id = m.liability_id WHERE m.grant_id = ?" + (
            f" OR m.liability_id IN ({','.join('?' for _ in carried)})" if carried else ""),
        (grant.grant_id, *carried),
    ).fetchall()
    period = int(grant.spec.get("renewal_seconds") or 0)
    anchor = float(grant.spec.get("period_anchor") or 0)
    period_start = anchor + max(0, int((_now() - anchor) // period)) * period if period else 0
    seen: dict[str, dict[str, Any]] = {}
    principal = 0
    fees = 0
    for row in rows:
        liability_id = str(row["liability_id"])
        if liability_id == exclude_liability:
            continue
        state = _state_of(row, liability_id=liability_id)
        # Unresolved commitments carry across renewal. Only completed costs age out.
        if period and state == LIABILITY_SETTLED and float(row["settled_epoch"] or row["created_epoch"]) < period_start:
            continue
        kind = str(row["operation_kind"])
        if kind not in _OPERATION_SCHEMA:
            raise _CorruptRowError("operation kind is malformed", liability_id=liability_id)
        entry = seen.setdefault(liability_id, {"state": state, "created": row["created_epoch"], "lines": 0})
        if row["flow"] is None:
            continue
        entry["lines"] += 1
        if state in CLOSED_STATES:
            continue
        flow = str(row["flow"])
        amount = _line_amount(row, liability_id=liability_id)
        if flow == _OPERATION_SCHEMA[kind]["bound"] or flow == FLOW_SERVICE_FEE:
            # the service fee is spend in the paid asset: it consumes the same envelope as the principal
            principal += amount
        elif flow == FLOW_NETWORK_FEE:
            fees += amount
    operations = 0
    concurrent = 0
    last_created = 0.0
    for liability_id, entry in seen.items():
        if entry["lines"] == 0:
            raise _CorruptRowError("a liability has no lines", liability_id=liability_id)
        if entry["state"] not in CLOSED_STATES:
            operations += 1
        if entry["state"] in HELD_STATES:
            concurrent += 1
        if entry["state"] != LIABILITY_RELEASED:
            try:
                last_created = max(last_created, float(entry["created"] or 0.0))
            except (TypeError, ValueError):
                raise _CorruptRowError("created_epoch is malformed", liability_id=liability_id) from None
    return {
        "principal": principal,
        "fees": fees,
        "operations": operations,
        "concurrent": concurrent,
        "last_created": last_created,
    }


def _check_grant(
    conn: Any,
    grant: MoneyGrant,
    request: LiabilityRequest,
    facts: dict[str, Any],
    now: float,
    *,
    exclude_liability: str = "",
) -> None:
    spec = grant.spec
    by_flow: dict[str, MoneyLine] = facts["by_flow"]
    context = {"grant_id": grant.grant_id, "grant_version": grant.version}
    if grant.state == GRANT_REVOKED:
        raise _RefusalError(MONEY_AUTHORITY_REVOKED, f"grant {grant.grant_id} was revoked", context=context)
    if now < float(spec["not_before_epoch"] or 0.0):
        raise _RefusalError(MONEY_AUTHORITY_INVALID, f"grant {grant.grant_id} is not valid yet", context=context)
    if float(spec["expires_epoch"]) and now >= float(spec["expires_epoch"]):
        raise _RefusalError(MONEY_AUTHORITY_EXPIRED, f"grant {grant.grant_id} expired", context=context)

    def mismatch(what: str) -> _RefusalError:
        return _RefusalError(
            MONEY_AUTHORITY_INVALID,
            f"grant {grant.grant_id} does not authorize this {what}",
            context={**context, "mismatch": what},
        )

    if request.operation_kind not in spec["operation_kinds"]:
        raise mismatch("operation kind")
    if request.identity.provider_id != spec["provider_id"]:
        raise mismatch("provider")
    if spec["provider_account"] and facts["provider_account"] != spec["provider_account"]:
        raise mismatch("provider account")
    schema = facts["schema"]
    if schema["inference"] and spec["kind"] != GRANT_PROVIDER_BUDGET:
        if facts["model_id"] not in spec["models"]:
            raise mismatch("model")
        if facts["route"] not in spec["routes"]:
            raise mismatch("route")
    if schema["wallet"]:
        if facts["network"] != spec["network"]:
            raise mismatch("network")
        if facts["payer_account"] != spec["payer_account"]:
            raise mismatch("payer account")
    bound_line = by_flow[schema["bound"]]
    if bound_line.asset.key != spec["asset_key"]:
        raise mismatch("asset")
    for name in ("task_id", "session_id", "agent_id"):
        if spec[name] and getattr(request.identity, name) != spec[name]:
            raise mismatch(name.replace("_id", ""))
    per_operation = _atomic_value(spec["per_operation_max_atomic"], what="grant per-operation maximum")
    service_fee_line = by_flow.get(FLOW_SERVICE_FEE)
    if service_fee_line is not None and service_fee_line.asset.key != bound_line.asset.key:
        raise mismatch("service fee asset")
    principal_need = bound_line.max_atomic + (service_fee_line.max_atomic if service_fee_line is not None else 0)
    if principal_need > per_operation:
        raise _RefusalError(
            MONEY_AUTHORITY_EXHAUSTED,
            f"{principal_need} (principal{' plus service fee ceiling' if service_fee_line is not None else ''}) exceeds the grant's per-operation maximum {per_operation}",
            context=context,
        )
    fee_line = by_flow.get(FLOW_NETWORK_FEE)
    if fee_line is not None:
        if not spec["fee_asset_key"] or fee_line.asset.key != spec["fee_asset_key"]:
            raise mismatch("network fee asset")
        per_fee = _atomic_value(spec["per_operation_max_fee_atomic"], what="grant per-operation fee")
        if fee_line.max_atomic > per_fee:
            raise _RefusalError(
                MONEY_AUTHORITY_EXHAUSTED,
                f"fee {fee_line.max_atomic} exceeds the grant's per-operation fee maximum {per_fee}",
                context=context,
            )
    usage = _grant_usage(conn, grant, exclude_liability=exclude_liability)
    max_total = _atomic_value(spec["max_total_atomic"], what="grant total")
    if usage["principal"] + principal_need > max_total:
        raise _RefusalError(
            MONEY_AUTHORITY_EXHAUSTED,
            f"grant {grant.grant_id} has {max_total - usage['principal']} of {max_total} left; "
            f"{principal_need} requested",
            context={**context, "used_atomic": _atomic_text(usage["principal"])},
        )
    if fee_line is not None:
        max_fees = _atomic_value(spec["max_fee_total_atomic"], what="grant fee total")
        if usage["fees"] + fee_line.max_atomic > max_fees:
            raise _RefusalError(
                MONEY_AUTHORITY_EXHAUSTED,
                f"grant {grant.grant_id} fee authority exhausted",
                context={**context, "fees_used_atomic": _atomic_text(usage["fees"])},
            )
    if int(spec["max_operations"]) and usage["operations"] + 1 > int(spec["max_operations"]):
        raise _RefusalError(
            MONEY_AUTHORITY_EXHAUSTED,
            f"grant {grant.grant_id} authorized its {spec['max_operations']} operation(s) already",
            context=context,
        )
    if int(spec["max_concurrent"]) and usage["concurrent"] + 1 > int(spec["max_concurrent"]):
        raise _RefusalError(
            MONEY_AUTHORITY_RATE_LIMITED,
            f"grant {grant.grant_id} already holds {usage['concurrent']} open operation(s)",
            context=context,
        )
    interval = float(spec["min_interval_seconds"] or 0.0)
    if interval and usage["last_created"] and now - usage["last_created"] < interval:
        raise _RefusalError(
            MONEY_AUTHORITY_RATE_LIMITED,
            f"grant {grant.grant_id} allows one operation every {interval:g}s",
            context=context,
        )


def _committed_debits(conn: Any, account: str, asset_key: str, flows: tuple[str, ...], since: float) -> int:
    placeholders = ",".join("?" for _ in flows)
    rows = conn.execute(
        f"SELECT l.max_atomic, l.actual_atomic, l.line_state, m.state, m.settled_epoch, m.liability_id "
        f"FROM {_LINES_TABLE} l JOIN {_LIABILITIES_TABLE} m ON m.liability_id = l.liability_id "
        f"WHERE l.account = ? AND l.asset_key = ? AND l.flow IN ({placeholders}) AND m.state NOT IN (?, ?)",
        (account, asset_key, *flows, *CLOSED_STATES),
    ).fetchall()
    total = 0
    for row in rows:
        liability_id = str(row["liability_id"])
        state = _state_of(row, liability_id=liability_id)
        if state == LIABILITY_SETTLED:
            try:
                settled_epoch = float(row["settled_epoch"] or 0.0)
            except (TypeError, ValueError):
                raise _CorruptRowError("settled_epoch is malformed", liability_id=liability_id) from None
            if settled_epoch <= since:
                continue
        total += _line_amount(row, liability_id=liability_id)
    return total


def _verified_credits_since(conn: Any, account: str, asset_key: str, since: float) -> int:
    rows = conn.execute(
        f"SELECT l.max_atomic, l.actual_atomic, l.line_state, m.liability_id FROM {_LINES_TABLE} l "
        f"JOIN {_LIABILITIES_TABLE} m ON m.liability_id = l.liability_id "
        "WHERE l.account = ? AND l.asset_key = ? AND l.flow = ? AND m.state = ? AND l.verified = 1 "
        "AND l.line_state = ? AND COALESCE(l.resolved_epoch, 0) > ?",
        (account, asset_key, FLOW_PROVIDER_CREDIT_CREDIT, LIABILITY_SETTLED, LINE_EXACT, since),
    ).fetchall()
    return sum(_line_amount(row, liability_id=str(row["liability_id"])) for row in rows)


def _observation(conn: Any, account: str, asset_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {_LIQUIDITY_TABLE} WHERE account=? AND asset_key=?", (account, asset_key)
    ).fetchone()
    if row is None:
        return None
    try:
        observed = float(row["observed_epoch"])
    except (TypeError, ValueError):
        raise _CorruptRowError("liquidity observation time is malformed") from None
    return {
        "balance": _atomic_value(row["balance_atomic"], what="liquidity balance"),
        "observed_epoch": observed,
        "verified": bool(int(row["verified"] or 0)),
        "source": str(row["source"] or ""),
    }


def _check_liquidity(conn: Any, grant: MoneyGrant, facts: dict[str, Any], now: float) -> None:
    by_flow: dict[str, MoneyLine] = facts["by_flow"]
    needs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for flow, line in by_flow.items():
        if flow in WALLET_DEBIT_FLOWS:
            kind = "wallet"
        elif flow == FLOW_PROVIDER_CREDIT_DEBIT and grant.spec["credit_liquidity"] == CREDIT_LIQUIDITY_REQUIRED:
            kind = "credit"
        else:
            continue
        entry = needs.setdefault((kind, line.account, line.asset.key), {"need": 0, "flows": []})
        entry["need"] += line.max_atomic
        entry["flows"].append(flow)
    for (kind, account, asset_key), entry in needs.items():
        label = "network fee asset" if entry["flows"] == [FLOW_NETWORK_FEE] else ("provider credit" if kind == "credit" else "wallet asset")
        observation = _observation(conn, account, asset_key)
        ttl = LIQUIDITY_OBSERVATION_TTL_SECONDS if kind == "wallet" else CREDIT_OBSERVATION_TTL_SECONDS
        context = {"account": account, "asset_key": asset_key, "flows": entry["flows"]}
        if observation is None or now - observation["observed_epoch"] > ttl or (kind == "credit" and not observation["verified"]):
            raise _RefusalError(
                MONEY_LIQUIDITY_UNVERIFIED,
                f"no fresh{' verified' if kind == 'credit' else ''} balance observation for the "
                f"{label} {asset_key} at {account}; a debit is never authorized against an unknown balance",
                flow=entry["flows"][0],
                context=context,
            )
        if kind == "wallet":
            committed = _committed_debits(conn, account, asset_key, tuple(sorted(WALLET_DEBIT_FLOWS)), observation["observed_epoch"])
            credits = 0
        else:
            committed = _committed_debits(conn, account, asset_key, (FLOW_PROVIDER_CREDIT_DEBIT,), observation["observed_epoch"])
            credits = _verified_credits_since(conn, account, asset_key, observation["observed_epoch"])
        available = observation["balance"] - committed + credits
        if available < entry["need"]:
            raise _RefusalError(
                MONEY_LIQUIDITY_INSUFFICIENT,
                f"{label} {asset_key} at {account}: {max(available, 0)} available after held debits, "
                f"{entry['need']} needed",
                flow=entry["flows"][0],
                context={**context, "available_atomic": _atomic_text(max(available, 0)), "needed_atomic": _atomic_text(entry["need"])},
            )


def _wallet_frozen() -> bool:
    """The canonical panic freeze. An unreadable freeze reads as frozen for
    wallet debits: a brake nobody can read is a brake that stays on."""
    try:
        from core.wallet import limits

        return bool(limits.is_frozen())
    except Exception:
        return True


def _lines_of(conn: Any, liability_id: str) -> list[Any]:
    rows = conn.execute(
        f"SELECT * FROM {_LINES_TABLE} WHERE liability_id=? ORDER BY line_id", (liability_id,)
    ).fetchall()
    if not rows:
        raise _CorruptRowError("a liability has no lines", liability_id=liability_id)
    for row in rows:
        if str(row["flow"]) not in MONEY_FLOWS:
            raise _CorruptRowError("a line flow is malformed", liability_id=liability_id)
        _line_amount(row, liability_id=liability_id)
    return rows


def _receipt(conn: Any, row: Any, *, idempotent: bool = False) -> LiabilityReceipt:
    liability_id = str(row["liability_id"])
    lines = tuple(
        {
            "flow": str(line["flow"]),
            "asset_key": str(line["asset_key"]),
            "account": str(line["account"]),
            "max_atomic": str(line["max_atomic"]),
            "actual_atomic": None if line["actual_atomic"] is None else str(line["actual_atomic"]),
            "line_state": str(line["line_state"]),
            "verified": bool(int(line["verified"] or 0)),
        }
        for line in _lines_of(conn, liability_id)
    )
    return LiabilityReceipt(
        liability_id=liability_id,
        operation_id=str(row["operation_id"]),
        operation_kind=str(row["operation_kind"]),
        state=_state_of(row, liability_id=liability_id),
        grant_id=str(row["grant_id"]),
        grant_version=int(row["grant_version"]),
        attempt=int(row["attempt"] or 0),
        lines=lines,
        idempotent=idempotent,
        effect_id=str(row["effect_id"] or ""),
    )


def _liability_row(conn: Any, liability_id: str) -> Any:
    row = conn.execute(
        f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (str(liability_id),)
    ).fetchone()
    if row is None:
        raise _RefusalError(MONEY_STATE_ERROR, f"liability {str(liability_id)[:80]!r} does not exist")
    _state_of(row, liability_id=str(liability_id))
    return row


def _transition(
    conn: Any,
    row: Any,
    new_state: str,
    *,
    sets: dict[str, Any] | None = None,
) -> None:
    columns = {"state": new_state, "updated_epoch": _now(), **(sets or {})}
    assignments = ", ".join(f"{name}=?" for name in columns)
    cursor = conn.execute(
        f"UPDATE {_LIABILITIES_TABLE} SET {assignments}, state_version=state_version+1 "
        "WHERE liability_id=? AND state=? AND state_version=?",
        (*columns.values(), str(row["liability_id"]), str(row["state"]), int(row["state_version"])),
    )
    if cursor.rowcount != 1:
        raise _RefusalError(
            MONEY_CLAIM_CONFLICT,
            f"liability {row['liability_id']} changed under this transition; nothing was written",
            liability_id=str(row["liability_id"]),
        )


# ---------------------------------------------------------------------------
# reserve, claim, dispatch, unsent, unknown, release, retry
# ---------------------------------------------------------------------------


def reserve_liability(request: LiabilityRequest) -> LiabilityReceipt:
    """Reserve an operation's full maximum liability — atomically.

    The existing liability for the operation id, the grant, every money rule,
    the grant's own consumption and the liquidity observations are all read
    and the liability, its lines and its receipt all written in ONE
    BEGIN IMMEDIATE transaction. A replay with identical economic terms
    returns the existing liability; different terms are refused.
    """
    now = _now()
    facts = _validate_request(request, now)
    by_flow: dict[str, MoneyLine] = facts["by_flow"]
    frozen = bool(facts["schema"]["wallet"]) and _wallet_frozen()
    if frozen:
        raise EffectBudgetRefusedError(
            MONEY_FROZEN, "the wallet panic freeze is on; no wallet debit can be reserved"
        )
    with suppress(EffectBudgetRefusedError):
        reconcile_money_liabilities()
    with _write_txn("money reservation") as txn:
        conn = txn.conn
        instance_id = _eb._current_instance(conn)
        existing = conn.execute(
            f"SELECT * FROM {_LIABILITIES_TABLE} WHERE operation_id=?", (facts["operation_id"],)
        ).fetchone()
        if existing is not None:
            if str(existing["spec_digest"]) != facts["spec_digest"]:
                raise _RefusalError(
                    MONEY_IDEMPOTENCY_CONFLICT,
                    f"operation {facts['operation_id']} already holds a liability with different "
                    "economic terms; an operation id names exactly one liability",
                    liability_id=str(existing["liability_id"]),
                )
            existing_state = _state_of(existing, liability_id=str(existing["liability_id"]))
            if existing_state in CLOSED_STATES:
                return _rereserve_locked(conn, existing, request, frozen=frozen, owner_ref=facts["owner_ref"])
            return _receipt(conn, existing, idempotent=True)
        grant = _grant_row(conn, facts["grant_id"])
        _check_grant(conn, grant, request, facts, now)
        _check_rules(conn, facts, request.identity, now)
        _check_liquidity(conn, grant, facts, now)
        liability_id = "mli:" + secrets.token_hex(10)
        identity = request.identity
        conn.execute(
            f"INSERT INTO {_LIABILITIES_TABLE} (liability_id, operation_id, operation_kind, state, "
            "grant_id, grant_version, request_id, turn_id, task_id, agent_id, session_id, project_key, "
            "provider_id, provider_account, model_id, route, network, payer_account, effect_id, "
            "owner_ref, instance_id, spec_digest, correlation_json, created_epoch, updated_epoch, "
            "expires_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                liability_id,
                facts["operation_id"],
                request.operation_kind,
                LIABILITY_RESERVED,
                grant.grant_id,
                grant.version,
                identity.request_id,
                identity.turn_id,
                identity.task_id,
                identity.agent_id,
                identity.session_id,
                identity.project_key,
                identity.provider_id,
                facts["provider_account"],
                facts["model_id"],
                facts["route"],
                facts["network"],
                facts["payer_account"],
                facts["effect_id"],
                facts["owner_ref"],
                instance_id,
                facts["spec_digest"],
                json.dumps(facts["correlation"], sort_keys=True),
                now,
                now,
                facts["expires_epoch"],
            ),
        )
        for flow in sorted(by_flow):
            line = by_flow[flow]
            conn.execute(
                f"INSERT INTO {_LINES_TABLE} (liability_id, flow, asset_key, account, max_atomic, "
                "actual_atomic, line_state, created_epoch) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (liability_id, flow, line.asset.key, line.account, _atomic_text(line.max_atomic), LINE_HELD, now),
            )
        _journal(
            conn,
            "money_reserved",
            liability_id=liability_id,
            detail={
                "operation_id": facts["operation_id"],
                "operation_kind": request.operation_kind,
                "grant_id": grant.grant_id,
                "grant_version": grant.version,
                "lines": [by_flow[flow].to_dict() for flow in sorted(by_flow)],
                "identity": identity.to_dict(),
                "model_id": facts["model_id"],
                "route": facts["route"],
                "correlation": facts["correlation"],
            },
        )
        _prune_money(conn, now)
        row = conn.execute(
            f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (liability_id,)
        ).fetchone()
        return _receipt(conn, row)


def bind_liability_effect(liability_id: str, effect_id: str) -> None:
    """Bind a reserved liability to the gateway-minted effect id (minted after
    the reservation). A no-op for anything but a reserved liability."""
    if not liability_id or not effect_id:
        return
    with _write_txn("money effect binding") as txn:
        txn.conn.execute(
            f"UPDATE {_LIABILITIES_TABLE} SET effect_id=? WHERE liability_id=? AND state=?",
            (_clean_ident(effect_id, what="effect_id"), str(liability_id), LIABILITY_RESERVED),
        )


def _operation_kind_of(liability_id: str) -> str:
    """A read-only pre-read of an immutable fact, so the wallet freeze is
    consulted only for wallet debits and never from inside a write
    transaction (the wallet store is the same database in production). An
    unreadable store answers "" and the write path fails with its own code."""
    try:
        conn = _eb._budget_connection()
    except Exception:
        return ""
    try:
        if not _money_store_present(conn):
            return ""
        row = conn.execute(
            f"SELECT operation_kind FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (str(liability_id),)
        ).fetchone()
        return str(row["operation_kind"]) if row is not None else ""
    except Exception:
        return ""
    finally:
        with suppress(Exception):
            conn.close()


def claim_dispatch(liability_id: str, *, executor: str) -> DispatchClaim:
    """reserved -> dispatching, with a fencing token, BEFORE any signing,
    broadcast or provider request. One winner; a second claimant receives
    MONEY_CLAIM_CONFLICT. A claim refused for revocation, expiry, reservation
    expiry or the wallet freeze releases the never-sent reservation in the
    same transaction and then raises."""
    executor_name = _clean_ident(executor, what="executor") or "unnamed-executor"
    frozen = _wallet_frozen() if _operation_kind_of(liability_id) in WALLET_OPERATION_KINDS else False
    with _write_txn("money dispatch claim") as txn:
        conn = txn.conn
        row = _liability_row(conn, liability_id)
        state = str(row["state"])
        if state in (LIABILITY_DISPATCHING, LIABILITY_PENDING, LIABILITY_UNKNOWN, LIABILITY_SETTLED):
            raise _RefusalError(
                MONEY_CLAIM_CONFLICT,
                f"liability {liability_id} is already {state}; exactly one executor dispatches an operation",
                liability_id=str(liability_id),
            )
        if state in CLOSED_STATES:
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} is {state}; it must be reserved again before dispatch",
                liability_id=str(liability_id),
            )
        now = _now()
        grant = _grant_row(conn, str(row["grant_id"]))
        refusal: tuple[str, str] | None = None
        if grant.state == GRANT_REVOKED:
            refusal = (MONEY_AUTHORITY_REVOKED, f"grant {grant.grant_id} was revoked before dispatch")
        elif float(grant.spec["expires_epoch"]) and now >= float(grant.spec["expires_epoch"]):
            refusal = (MONEY_AUTHORITY_EXPIRED, f"grant {grant.grant_id} expired before dispatch")
        elif float(row["expires_epoch"] or 0.0) and float(row["expires_epoch"]) <= now:
            refusal = (MONEY_RESERVATION_EXPIRED, "the operation's quote window expired before dispatch")
        elif frozen and str(row["operation_kind"]) in (OP_INFERENCE_X402, OP_PROVIDER_TOPUP, OP_WALLET_PAYMENT):
            refusal = (MONEY_FROZEN, "the wallet panic freeze is on; the payment was not dispatched")
        if refusal is not None:
            _transition(conn, row, LIABILITY_RELEASED, sets={"close_reason": f"claim refused: {refusal[0]}"})
            _journal(
                conn,
                "money_released",
                code=refusal[0],
                liability_id=str(liability_id),
                detail={"reason": refusal[1], "never_dispatched": True},
            )
            txn.deferred = EffectBudgetRefusedError(refusal[0], refusal[1])
            return DispatchClaim(str(liability_id), "", int(row["attempt"] or 0), executor_name)
        instance_id = _eb._current_instance(conn)
        token = secrets.token_hex(16)
        attempt = int(row["attempt"] or 0) + 1
        _transition(
            conn,
            row,
            LIABILITY_DISPATCHING,
            sets={
                "claim_token": token,
                "claim_instance_id": instance_id,
                "executor": executor_name,
                "claimed_epoch": now,
                "attempt": attempt,
            },
        )
        _journal(
            conn,
            "money_dispatch_claimed",
            liability_id=str(liability_id),
            detail={"attempt": attempt, "executor": executor_name, "claim_instance_id": instance_id},
        )
        return DispatchClaim(str(liability_id), token, attempt, executor_name)


def _require_claim(row: Any, claim_token: str) -> None:
    if not claim_token or str(row["claim_token"] or "") != str(claim_token):
        raise _RefusalError(
            MONEY_CLAIM_CONFLICT,
            f"liability {row['liability_id']} is owned by another dispatch claim",
            liability_id=str(row["liability_id"]),
        )


def record_dispatched(liability_id: str, claim_token: str, *, evidence_id: str = "", detail: Mapping[str, str] | None = None) -> str:
    """dispatching -> pending: the request or transaction left (a signature,
    an HTTP request id). The maximum stays held until settlement."""
    public = {str(k)[:40]: str(v)[:256] for k, v in (detail or {}).items() if "://" not in str(v)}
    with _write_txn("money dispatch record") as txn:
        row = _liability_row(txn.conn, liability_id)
        state = str(row["state"])
        _require_claim(row, claim_token)
        if state == LIABILITY_PENDING:
            return state
        if state not in (LIABILITY_DISPATCHING, LIABILITY_UNKNOWN):
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} is {state}; only a dispatching operation becomes pending",
                liability_id=str(liability_id),
            )
        _transition(
            txn.conn,
            row,
            LIABILITY_PENDING,
            sets={
                "dispatched_epoch": _now(),
                "dispatch_evidence_json": json.dumps(
                    {"evidence_id": _clean_ident(evidence_id, what="evidence_id"), **public}, sort_keys=True
                ),
            },
        )
        _journal(
            txn.conn,
            "money_dispatched",
            liability_id=str(liability_id),
            detail={"evidence_id": str(evidence_id or ""), "from_state": state},
        )
        return LIABILITY_PENDING


def record_unknown(liability_id: str, claim_token: str = "", *, reason: str) -> str:
    """dispatching|pending -> unknown: the outcome cannot be proven (lost body,
    timeout after send, partial stream, store failure mid-dispatch). The
    maximum stays held; only evidence converts it."""
    clean_reason = str(reason or "outcome_unknown")[:200]
    with _write_txn("money unknown record") as txn:
        row = _liability_row(txn.conn, liability_id)
        state = str(row["state"])
        if state in (LIABILITY_UNKNOWN, LIABILITY_SETTLED) or state in CLOSED_STATES:
            return state
        if state == LIABILITY_RESERVED:
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} was never claimed; release it instead",
                liability_id=str(liability_id),
            )
        _require_claim(row, claim_token)
        _transition(txn.conn, row, LIABILITY_UNKNOWN, sets={"unknown_reason": clean_reason})
        _journal(
            txn.conn,
            "money_unknown",
            liability_id=str(liability_id),
            detail={"reason": clean_reason, "from_state": state},
        )
        return LIABILITY_UNKNOWN


def record_unsent(liability_id: str, claim_token: str = "", *, evidence: UnsentEvidence) -> str:
    """-> unsent, on POSITIVE proof nothing left. From dispatching the claimant
    may prove it with a local proof kind; from pending or unknown only a
    provider/chain proof or an operator attestation counts. The maximum is
    released; the operation may be retried with `retry_unsent`."""
    if not isinstance(evidence, UnsentEvidence):
        raise _invalid("record_unsent needs UnsentEvidence")
    proof = str(evidence.proof_kind or "")
    if proof not in _CLAIMANT_UNSENT_PROOFS and proof not in _EXTERNAL_UNSENT_PROOFS and proof != UNSENT_OPERATOR_ATTESTATION:
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, f"{proof[:40]!r} is not a proof that nothing was sent")
    if str(evidence.source or "") not in EVIDENCE_SOURCES:
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, "unsent proof must come from provider, mechanical or user evidence — never a model")
    evidence_id = _clean_ident(evidence.evidence_id, what="evidence_id")
    with _write_txn("money unsent record") as txn:
        conn = txn.conn
        row = _liability_row(conn, liability_id)
        state = str(row["state"])
        if state == LIABILITY_UNSENT:
            return state
        if state in (LIABILITY_RESERVED, LIABILITY_RELEASED, LIABILITY_SETTLED):
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} is {state}; unsent applies to a claimed operation",
                liability_id=str(liability_id),
            )
        if proof == UNSENT_OPERATOR_ATTESTATION:
            _require_operator(conn, evidence.operator_token, "unsent attestation")
        elif state == LIABILITY_DISPATCHING and proof in _CLAIMANT_UNSENT_PROOFS:
            _require_claim(row, claim_token)
        elif proof not in _EXTERNAL_UNSENT_PROOFS or evidence.source == "user":
            raise _RefusalError(
                MONEY_EVIDENCE_INSUFFICIENT,
                f"liability {liability_id} is {state}; only provider or chain proof (or an operator "
                "attestation) shows it never landed",
                liability_id=str(liability_id),
            )
        _transition(conn, row, LIABILITY_UNSENT, sets={"close_reason": f"unsent: {proof}"})
        _journal(
            conn,
            "money_unsent",
            liability_id=str(liability_id),
            detail={"proof_kind": proof, "evidence_id": evidence_id, "source": evidence.source, "from_state": state},
        )
        return LIABILITY_UNSENT


def release_unclaimed(liability_id: str, *, reason: str = "released before dispatch") -> bool:
    """reserved -> released: the operation was authorized and never claimed.
    Idempotent; False when nothing was released."""
    with _write_txn("money release") as txn:
        row = txn.conn.execute(
            f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (str(liability_id),)
        ).fetchone()
        if row is None or str(row["state"]) != LIABILITY_RESERVED:
            return False
        _transition(txn.conn, row, LIABILITY_RELEASED, sets={"close_reason": str(reason or "")[:200]})
        _journal(
            txn.conn,
            "money_released",
            liability_id=str(liability_id),
            detail={"reason": str(reason or "")[:200], "never_dispatched": True},
        )
        return True


def _money_store_present(conn: Any) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_LIABILITIES_TABLE,)
    ).fetchone()
    return row is not None


def _owner_holds_unclaimed(owner_ref: str) -> bool:
    """Cheap read before the write lock: every turn closes a scope, and a turn
    that reserved no money must not pay a write transaction to learn that.
    An unreadable store answers True so the write path fails with its code."""
    try:
        conn = _eb._budget_connection()
    except Exception:
        return True
    try:
        if not _money_store_present(conn):
            return False
        return (
            conn.execute(
                f"SELECT 1 FROM {_LIABILITIES_TABLE} WHERE owner_ref=? AND state=? LIMIT 1",
                (str(owner_ref), LIABILITY_RESERVED),
            ).fetchone()
            is not None
        )
    except Exception:
        return True
    finally:
        with suppress(Exception):
            conn.close()


def bind_owned_identity(
    request: LiabilityRequest,
    *,
    owned: Mapping[str, str],
    owner_ref: str = "",
    effect_id: str = "",
) -> LiabilityRequest:
    """Fill a request's identity from the identity its effect scope OWNS.

    A stated value that disagrees with an owned one is refused, never
    replaced: money is never re-attributed to a different request, turn,
    session, project, task, agent or provider than the scope it runs in."""
    if not isinstance(request, LiabilityRequest):
        raise _invalid("a LiabilityRequest is required")
    stated = request.identity if isinstance(request.identity, MoneyIdentity) else MoneyIdentity()
    merged: dict[str, str] = {}
    for name in ("request_id", "turn_id", "task_id", "agent_id", "session_id", "project_key", "provider_id"):
        owned_value = str((owned or {}).get(name) or "")
        stated_value = str(getattr(stated, name) or "")
        if owned_value and stated_value and owned_value != stated_value:
            raise EffectBudgetRefusedError(
                MONEY_IDENTITY_CONFLICT,
                f"stated {name} {stated_value[:80]!r} conflicts with the owning scope's "
                f"{owned_value[:80]!r}; money is never re-attributed",
            )
        merged[name] = owned_value or stated_value
    return replace(
        request,
        identity=MoneyIdentity(**merged),
        owner_ref=str(owner_ref or request.owner_ref or ""),
        effect_id=str(effect_id or request.effect_id or ""),
    )


def release_unclaimed_for_owner(owner_ref: str, *, reason: str) -> int:
    """The scope-close rollback for money: every never-claimed liability this
    owner holds returns. Claimed ones are untouched — they may have gone out."""
    if not owner_ref or not _owner_holds_unclaimed(owner_ref):
        return 0
    with _write_txn("money owner rollback") as txn:
        rows = txn.conn.execute(
            f"SELECT * FROM {_LIABILITIES_TABLE} WHERE owner_ref=? AND state=?",
            (str(owner_ref), LIABILITY_RESERVED),
        ).fetchall()
        for row in rows:
            _transition(txn.conn, row, LIABILITY_RELEASED, sets={"close_reason": str(reason or "")[:200]})
            _journal(
                txn.conn,
                "money_released",
                liability_id=str(row["liability_id"]),
                detail={"reason": str(reason or "")[:200], "never_dispatched": True, "owner_ref": str(owner_ref)},
            )
        return len(rows)


def retry_unsent(liability_id: str) -> LiabilityReceipt:
    """unsent -> reserved again for legitimate retry: grant validity, rules,
    grant consumption and liquidity are all re-checked in this transaction —
    capacity another operation took meanwhile is not taken back."""
    frozen = _wallet_frozen() if _operation_kind_of(liability_id) in WALLET_OPERATION_KINDS else False
    with _write_txn("money unsent retry") as txn:
        conn = txn.conn
        row = _liability_row(conn, liability_id)
        state = str(row["state"])
        if state != LIABILITY_UNSENT:
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} is {state}; only a proven-unsent operation is retried",
                liability_id=str(liability_id),
            )
        request = _request_from_row(conn, row)
        return _rereserve_locked(conn, row, request, frozen=frozen, owner_ref=str(row["owner_ref"] or ""))


def _rereserve_locked(
    conn: Any, row: Any, request: LiabilityRequest, *, frozen: bool, owner_ref: str
) -> LiabilityReceipt:
    """released|unsent -> reserved under the SAME operation id. Nothing was
    ever paid for it, so the operation may be authorized again — with the
    grant, every rule, the grant's consumption and liquidity re-checked in
    this transaction. Capacity another operation took meanwhile stays taken."""
    liability_id = str(row["liability_id"])
    now = _now()
    if request.expires_epoch and float(request.expires_epoch) <= now:
        raise _RefusalError(
            MONEY_RESERVATION_EXPIRED,
            "the operation's quote window expired; a new quote needs a new operation id",
            liability_id=liability_id,
        )
    facts = _validate_request(request, now)
    if facts["schema"]["wallet"] and frozen:
        raise _RefusalError(MONEY_FROZEN, "the wallet panic freeze is on; no wallet debit can be reserved", liability_id=liability_id)
    grant = _grant_row(conn, facts["grant_id"])
    _check_grant(conn, grant, request, facts, now, exclude_liability=liability_id)
    _check_rules(conn, facts, request.identity, now)
    _check_liquidity(conn, grant, facts, now)
    instance_id = _eb._current_instance(conn)
    from_state = str(row["state"])
    _transition(
        conn,
        row,
        LIABILITY_RESERVED,
        sets={
            "instance_id": instance_id,
            "claim_token": "",
            "claim_instance_id": "",
            "close_reason": "",
            "owner_ref": str(owner_ref or ""),
            "grant_version": grant.version,
        },
    )
    _journal(
        conn,
        "money_rereserved",
        liability_id=liability_id,
        detail={"from_state": from_state, "attempts_so_far": int(row["attempt"] or 0), "grant_version": grant.version},
    )
    refreshed = conn.execute(
        f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (liability_id,)
    ).fetchone()
    return _receipt(conn, refreshed)


def _request_from_row(conn: Any, row: Any) -> LiabilityRequest:
    liability_id = str(row["liability_id"])
    lines = []
    for line in _lines_of(conn, liability_id):
        if str(line["flow"]) == FLOW_PROVIDER_CREDIT_CREDIT and str(row["operation_kind"]) != OP_PROVIDER_TOPUP:
            continue
        try:
            asset = AssetIdentity.from_key(str(line["asset_key"]))
        except EffectBudgetRefusedError:
            raise _CorruptRowError("line asset key is malformed", liability_id=liability_id) from None
        lines.append(
            MoneyLine(
                flow=str(line["flow"]),
                asset=asset,
                max_atomic=_atomic_value(line["max_atomic"], what="line maximum", liability_id=liability_id),
                account=str(line["account"]),
            )
        )
    try:
        correlation = json.loads(str(row["correlation_json"] or "{}"))
    except Exception:
        raise _CorruptRowError("correlation is unreadable", liability_id=liability_id) from None
    return LiabilityRequest(
        operation_id=str(row["operation_id"]),
        operation_kind=str(row["operation_kind"]),
        grant_id=str(row["grant_id"]),
        lines=tuple(lines),
        identity=MoneyIdentity(
            request_id=str(row["request_id"] or ""),
            turn_id=str(row["turn_id"] or ""),
            task_id=str(row["task_id"] or ""),
            agent_id=str(row["agent_id"] or ""),
            session_id=str(row["session_id"] or ""),
            project_key=str(row["project_key"] or ""),
            provider_id=str(row["provider_id"] or ""),
        ),
        provider_account=str(row["provider_account"] or ""),
        model_id=str(row["model_id"] or ""),
        route=str(row["route"] or ""),
        network=str(row["network"] or ""),
        payer_account=str(row["payer_account"] or ""),
        expires_epoch=float(row["expires_epoch"] or 0.0),
        correlation=correlation if isinstance(correlation, dict) else {},
        effect_id=str(row["effect_id"] or ""),
        owner_ref=str(row["owner_ref"] or ""),
    )


# ---------------------------------------------------------------------------
# settlement and reconciliation
# ---------------------------------------------------------------------------


def _evidence_payload(evidence: SettlementEvidence) -> dict[str, Any]:
    actuals = {}
    for flow, amount in dict(evidence.actuals or {}).items():
        if flow not in MONEY_FLOWS:
            raise _invalid(f"{str(flow)[:40]!r} is not a money flow")
        actuals[flow] = _atomic_text(require_atomic(amount, what=f"{flow} actual", allow_zero=True))
    credits = []
    for credit in evidence.credits or ():
        if not isinstance(credit, CreditLine):
            raise _invalid("credits must be CreditLine values")
        credits.append(
            {
                "asset_key": credit.asset.key,
                "account": credit.account,
                "amount_atomic": _atomic_text(credit.amount_atomic),
                "verified": bool(credit.verified),
            }
        )
    public = {}
    for key, value in dict(evidence.public_detail or {}).items():
        name, text = str(key), str(value)
        if not _CORRELATION_KEY_RE.fullmatch(name) or any(word in name for word in _SECRET_KEY_WORDS) or "://" in text or len(text) > 256:
            raise _invalid(f"public_detail {name[:40]!r} is not public billing metadata")
        public[name] = text
    return {
        "evidence_kind": evidence.evidence_kind,
        "evidence_id": evidence.evidence_id,
        "source": evidence.source,
        "actuals": actuals,
        "credits": sorted(credits, key=lambda item: (item["account"], item["asset_key"])),
        "public_detail": public,
    }


def settle_liability(liability_id: str, evidence: SettlementEvidence, *, claim_token: str = "") -> dict[str, Any]:
    """Apply verified settlement evidence. Idempotent per evidence id;
    conflicting evidence is refused and changes nothing; flows the evidence
    does not prove stay bounded at their maximum; an actual above the
    maximum is recorded, flagged over_cap and journaled.

    ``claim_token`` is the DISPATCH-LIFECYCLE ownership fence: an adapter invocation that holds
    a claim handle may only settle while THAT handle is the liability's current claim generation
    (a retried operation claims a new generation, and the old handle is stale). Checked inside
    this transaction. Reconciliation callers (late external evidence on an UNKNOWN outcome)
    pass no token and keep the evidence-driven path -- unknown outcomes stay closable by
    evidence, which is the law's own recovery route."""
    if not isinstance(evidence, SettlementEvidence):
        raise _invalid("settle_liability needs SettlementEvidence")
    kind = str(evidence.evidence_kind or "")
    if kind == EVIDENCE_BALANCE_DELTA:
        raise EffectBudgetRefusedError(
            MONEY_EVIDENCE_INSUFFICIENT,
            "a balance delta is not settlement evidence: a shared balance cannot say what one operation cost",
        )
    if kind not in SETTLEMENT_EVIDENCE_KINDS:
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, f"{kind[:40]!r} is not a settlement evidence kind")
    if str(evidence.source or "") not in EVIDENCE_SOURCES:
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, "settlement evidence must come from provider, mechanical or user sources — never a model")
    if not _OPERATION_ID_RE.fullmatch(str(evidence.evidence_id or "")):
        raise _invalid("evidence_id must be a public identifier (a signature, a receipt id)")
    payload = _evidence_payload(evidence)
    digest = _canonical_digest(payload)
    allowed_flows = _EVIDENCE_FLOWS[kind]
    with _write_txn("money settlement") as txn:
        conn = txn.conn
        row = _liability_row(conn, liability_id)
        state = str(row["state"])
        prior = conn.execute(
            f"SELECT digest FROM {_EVIDENCE_TABLE} WHERE liability_id=? AND evidence_id=?",
            (str(liability_id), payload["evidence_id"]),
        ).fetchone()
        if prior is not None:
            if str(prior["digest"]) == digest:
                return {"liability_id": str(liability_id), "state": state, "idempotent": True}
            raise _RefusalError(
                MONEY_SETTLEMENT_CONFLICT,
                f"evidence {payload['evidence_id']} was already applied to {liability_id} with a different payload",
                liability_id=str(liability_id),
            )
        if kind == EVIDENCE_OPERATOR_ATTESTATION:
            _require_operator(conn, evidence.operator_token, "settlement attestation")
        if state == LIABILITY_RESERVED:
            raise _RefusalError(
                MONEY_STATE_ERROR,
                f"liability {liability_id} was never claimed; nothing may settle without a dispatch claim",
                liability_id=str(liability_id),
            )
        if claim_token and state in (LIABILITY_DISPATCHING, LIABILITY_PENDING):
            _require_claim(row, claim_token)
        contradiction = state in CLOSED_STATES
        if contradiction and kind not in _CONTRADICTION_EVIDENCE:
            raise _RefusalError(
                MONEY_SETTLEMENT_CONFLICT,
                f"liability {liability_id} is {state}; only chain, provider or operator evidence can show it was paid after all",
                liability_id=str(liability_id),
            )
        lines = {str(line["flow"]): line for line in _lines_of(conn, liability_id)}
        operation_kind = str(row["operation_kind"])
        now = _now()
        actuals = {flow: int(text) for flow, text in payload["actuals"].items()}
        if FLOW_SERVICE_FEE in actuals:
            raise _RefusalError(
                MONEY_EVIDENCE_INSUFFICIENT,
                "the service fee is accrued by the money law from the accepted payment itself; no evidence may state it",
                liability_id=str(liability_id),
                flow=FLOW_SERVICE_FEE,
            )
        if kind == EVIDENCE_PROVIDER_REFUSAL_RECORD:
            if str(evidence.source or "") != "provider":
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    "an admission-refusal record is the provider's own answer; only the provider source may carry one",
                    liability_id=str(liability_id),
                )
            if state == LIABILITY_SETTLED:
                raise _RefusalError(
                    MONEY_SETTLEMENT_CONFLICT,
                    f"liability {liability_id} already settled; an admission refusal cannot revise a completed settlement",
                    liability_id=str(liability_id),
                )
            detail_map = dict(evidence.public_detail or {})
            refusal_code = str(detail_map.get("refusal_code") or "").strip()
            refusal_status = str(detail_map.get("refusal_status") or "").strip()
            if not refusal_code or not refusal_status.isdigit() or not 100 <= int(refusal_status) < 500:
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    "an admission-refusal record must name the provider's typed refusal code and its HTTP status",
                    liability_id=str(liability_id),
                )
            if operation_kind != OP_INFERENCE_PREPAID:
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    f"an admission-refusal record does not prove a {operation_kind} operation was never charged",
                    liability_id=str(liability_id),
                )
            charged_flows = [flow for flow, amount in actuals.items() if int(amount) != 0]
            if charged_flows:
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    f"an admission-refusal record proves a zero charge; {', '.join(charged_flows)} is not zero",
                    liability_id=str(liability_id),
                )
            uncovered = [
                flow
                for flow in (FLOW_INFERENCE_EXPENSE, FLOW_PROVIDER_CREDIT_DEBIT)
                if flow in lines and flow not in actuals
            ]
            if uncovered:
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    f"an admission-refusal record must close every expense line it can prove; {', '.join(uncovered)} unproven",
                    liability_id=str(liability_id),
                )
        for flow in actuals:
            if flow not in allowed_flows:
                raise _RefusalError(
                    MONEY_EVIDENCE_INSUFFICIENT,
                    f"{kind} evidence cannot prove {flow}",
                    liability_id=str(liability_id),
                    flow=flow,
                )
            if flow not in lines:
                raise _RefusalError(
                    MONEY_SETTLEMENT_CONFLICT,
                    f"liability {liability_id} carries no {flow} line",
                    liability_id=str(liability_id),
                    flow=flow,
                )
            existing = lines[flow]
            if str(existing["line_state"]) == LINE_EXACT:
                if _atomic_value(existing["actual_atomic"], what="line actual", liability_id=str(liability_id)) != actuals[flow]:
                    raise _RefusalError(
                        MONEY_SETTLEMENT_CONFLICT,
                        f"{flow} of {liability_id} already settled at a different amount",
                        liability_id=str(liability_id),
                        flow=flow,
                    )
        credits = list(payload["credits"])
        if credits:
            if FLOW_PROVIDER_CREDIT_CREDIT not in allowed_flows:
                raise _RefusalError(MONEY_EVIDENCE_INSUFFICIENT, f"{kind} evidence cannot create provider credit", liability_id=str(liability_id))
            if operation_kind not in (OP_INFERENCE_X402, OP_PROVIDER_TOPUP):
                raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, f"{operation_kind} does not create provider credit", liability_id=str(liability_id))
            if len(credits) != 1:
                raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, "one settlement creates at most one credit line", liability_id=str(liability_id))
            credit = credits[0]
            if credit["account"] != str(row["provider_account"]):
                raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, "credit must land at the liability's provider account", liability_id=str(liability_id))
            reference_line = lines.get(FLOW_WALLET_OUTFLOW)
            if reference_line is None or credit["asset_key"] != str(reference_line["asset_key"]):
                raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, "credit must be in the paid asset", liability_id=str(liability_id))

        def known(flow: str) -> int | None:
            if flow in actuals:
                return actuals[flow]
            line = lines.get(flow)
            if line is not None and str(line["line_state"]) == LINE_EXACT:
                return _atomic_value(line["actual_atomic"], what="line actual", liability_id=str(liability_id))
            return None

        if credits:
            credit_amount = int(credits[0]["amount_atomic"])
            paid = known(FLOW_WALLET_OUTFLOW)
            if operation_kind == OP_INFERENCE_X402:
                consumed = known(FLOW_INFERENCE_EXPENSE)
                if paid is not None and consumed is not None and credit_amount > paid - consumed:
                    raise _RefusalError(
                        MONEY_SETTLEMENT_CONFLICT,
                        "surplus credit cannot exceed what was paid minus what was consumed",
                        liability_id=str(liability_id),
                    )
            if paid is not None and credit_amount > paid:
                raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, "credit cannot exceed what was paid", liability_id=str(liability_id))
        over_cap = False
        applied_flows: list[str] = []
        for flow, amount in actuals.items():
            line = lines[flow]
            if str(line["line_state"]) == LINE_EXACT:
                continue
            maximum = _atomic_value(line["max_atomic"], what="line maximum", liability_id=str(liability_id))
            over_cap = over_cap or amount > maximum
            conn.execute(
                f"UPDATE {_LINES_TABLE} SET actual_atomic=?, line_state=?, evidence_id=?, resolved_epoch=? WHERE line_id=?",
                (_atomic_text(amount), LINE_EXACT, payload["evidence_id"], now, int(line["line_id"])),
            )
            applied_flows.append(flow)
        if credits:
            credit = credits[0]
            amount = int(credit["amount_atomic"])
            verified = 1 if credit["verified"] and kind in (EVIDENCE_PROVIDER_USAGE_RECEIPT, EVIDENCE_PROVIDER_BILLING_STATEMENT, EVIDENCE_OPERATOR_ATTESTATION) else 0
            existing_credit = lines.get(FLOW_PROVIDER_CREDIT_CREDIT)
            if existing_credit is None:
                conn.execute(
                    f"INSERT INTO {_LINES_TABLE} (liability_id, flow, asset_key, account, max_atomic, actual_atomic, "
                    "line_state, verified, evidence_id, created_epoch, resolved_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(liability_id), FLOW_PROVIDER_CREDIT_CREDIT, credit["asset_key"], credit["account"], _atomic_text(amount), _atomic_text(amount), LINE_EXACT, verified, payload["evidence_id"], now, now),
                )
            else:
                current_state = str(existing_credit["line_state"])
                current_actual = None if current_state != LINE_EXACT else _atomic_value(existing_credit["actual_atomic"], what="credit actual", liability_id=str(liability_id))
                if current_actual is not None and current_actual != amount:
                    raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, "provider credit already recorded at a different amount", liability_id=str(liability_id))
                maximum = _atomic_value(existing_credit["max_atomic"], what="credit maximum", liability_id=str(liability_id))
                over_cap = over_cap or amount > maximum
                conn.execute(
                    f"UPDATE {_LINES_TABLE} SET actual_atomic=?, line_state=?, verified=MAX(verified, ?), evidence_id=?, resolved_epoch=? WHERE line_id=?",
                    (_atomic_text(amount), LINE_EXACT, verified, payload["evidence_id"], now, int(existing_credit["line_id"])),
                )
            applied_flows.append(FLOW_PROVIDER_CREDIT_CREDIT)
        new_state = state
        service_fee: dict[str, Any] | None = None
        if FLOW_SERVICE_FEE in lines and kind in _CONTRADICTION_EVIDENCE and (state in HELD_STATES or contradiction or state == LIABILITY_SETTLED):
            # The payment is now proven accepted (a provider receipt or statement, an operator attestation, or the
            # chain's own confirmation of the outflow): the exact fee on it becomes OWED, once. Admission depends on
            # the operation's PAYMENT evidence and its existing accrual, never on whether THIS call is the one that
            # moves the liability into settled: a weaker settlement (usage priced at the ceiling) that came first
            # left the liability settled without an accrual, and the stronger proof arriving later admits it now.
            # Bounded or unknown outcomes never reach here, so reservation is never counted as revenue; an accrual
            # that already exists is never re-run on a later item with a different basis (exactly once).
            service_fee = _accrue_service_fee(conn, row, lines, actuals, payload["evidence_id"], kind, payload["source"], now, late=(state == LIABILITY_SETTLED))
        if state in HELD_STATES or contradiction:
            new_state = LIABILITY_SETTLED
            conn.execute(
                f"UPDATE {_LINES_TABLE} SET line_state=? WHERE liability_id=? AND line_state=?",
                (LINE_BOUNDED, str(liability_id), LINE_HELD),
            )
            _transition(
                conn,
                row,
                LIABILITY_SETTLED,
                sets={"settled_epoch": now, "over_cap": 1 if (over_cap or int(row["over_cap"] or 0)) else 0},
            )
        elif over_cap:
            conn.execute(
                f"UPDATE {_LIABILITIES_TABLE} SET over_cap=1, updated_epoch=? WHERE liability_id=?",
                (now, str(liability_id)),
            )
        if service_fee is not None and not service_fee.get("idempotent"):
            applied_flows.append(FLOW_SERVICE_FEE)
        conn.execute(
            f"INSERT INTO {_EVIDENCE_TABLE} (liability_id, evidence_id, evidence_kind, source, digest, payload_json, created_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(liability_id), payload["evidence_id"], kind, payload["source"], digest, json.dumps(payload, sort_keys=True), now),
        )
        _journal(
            conn,
            "money_settled" if new_state != state else "money_evidence_applied",
            liability_id=str(liability_id),
            detail={
                "evidence_kind": kind,
                "evidence_id": payload["evidence_id"],
                "source": payload["source"],
                "flows": applied_flows,
                "from_state": state,
                "over_cap": over_cap,
            },
        )
        if over_cap:
            _journal(
                conn,
                "money_overage",
                code=MONEY_BUDGET_EXCEEDED,
                liability_id=str(liability_id),
                detail={"evidence_id": payload["evidence_id"], "flows": applied_flows},
            )
        if contradiction:
            _journal(
                conn,
                "money_contradiction",
                code=MONEY_SETTLEMENT_CONFLICT,
                liability_id=str(liability_id),
                detail={"closed_state": state, "evidence_kind": kind, "evidence_id": payload["evidence_id"]},
            )
        return {
            "liability_id": str(liability_id),
            "state": new_state,
            "idempotent": False,
            "over_cap": over_cap,
            "contradiction": contradiction,
            "flows": applied_flows,
            "service_fee": service_fee,
        }


def _service_fee_terms(row: Any) -> dict[str, str]:
    """The fee identity a liability was reserved under, from its public correlation: policy, rate and treasury."""
    try:
        correlation = json.loads(str(row["correlation_json"] or "{}"))
    except (TypeError, ValueError):
        correlation = {}
    if not isinstance(correlation, dict):
        correlation = {}
    return {
        "policy_id": str(correlation.get("fee_policy") or ""),
        "rate_bps": str(correlation.get("fee_rate_bps") or ""),
        "treasury_owner": str(correlation.get("fee_treasury") or ""),
    }


def _accrue_service_fee(conn: Any, row: Any, lines: dict[str, Any], actuals: dict[str, int], evidence_id: str, kind: str, source: str, now: float, *, late: bool = False) -> dict[str, Any]:
    """Inside the settlement transaction: the exact fee on the accepted payment, once per liability, and the
    service-fee line settled EXACT at the whole units this settlement made collectible (the fraction rides the
    ledger's carry). The basis is the provider payment: the proven wallet outflow when exact, else its held
    maximum -- an x402 wallet pays its quoted cap exactly.

    Exactly once: an accrual that already exists is returned as idempotent and is NEVER re-run, even when this
    evidence item demonstrates a different basis; the difference is journaled as a fact for reconciliation, not
    applied. ``late`` marks the admission of a payment proof that arrived after a weaker settlement."""
    from core import service_fee_ledger

    liability_id = str(row["liability_id"])
    if str(row["operation_kind"]) != OP_INFERENCE_X402:
        raise _RefusalError(MONEY_STATE_ERROR, f"{row['operation_kind']} carries no service fee", liability_id=liability_id, flow=FLOW_SERVICE_FEE)
    terms = _service_fee_terms(row)
    if not terms["policy_id"] or not terms["treasury_owner"] or not terms["rate_bps"].isdigit():
        raise _RefusalError(MONEY_STATE_ERROR, "the liability carries a service fee line without its fee policy, rate and treasury", liability_id=liability_id, flow=FLOW_SERVICE_FEE)
    outflow = lines[FLOW_WALLET_OUTFLOW]
    if FLOW_WALLET_OUTFLOW in actuals:
        basis = int(actuals[FLOW_WALLET_OUTFLOW])
    elif str(outflow["line_state"]) == LINE_EXACT:
        basis = _atomic_value(outflow["actual_atomic"], what="outflow actual", liability_id=liability_id)
    else:
        basis = _atomic_value(outflow["max_atomic"], what="outflow maximum", liability_id=liability_id)
    fee_line = lines[FLOW_SERVICE_FEE]
    existing = service_fee_ledger.accrual_for_liability(conn, liability_id)
    if existing is not None:
        accrued_basis = int(existing["basis_atomic"])
        position = service_fee_ledger.owed(conn, str(existing["identity_key"]))
        if accrued_basis != basis:
            _journal(
                conn,
                "money_service_fee_basis_noted",
                flow=FLOW_SERVICE_FEE,
                liability_id=liability_id,
                detail={"evidence_id": str(evidence_id), "evidence_kind": str(kind), "accrued_basis_atomic": _atomic_text(accrued_basis), "demonstrated_basis_atomic": _atomic_text(basis), "applied": False},
            )
        return {
            "policy_id": terms["policy_id"], "rate_bps": int(terms["rate_bps"]), "basis_atomic": accrued_basis, "fee_numerator": int(existing["numerator"]),
            "identity_key": str(existing["identity_key"]), "newly_representable_atomic": 0, "owed_after": position, "idempotent": True,
            "basis_differs": accrued_basis != basis, "demonstrated_basis_atomic": basis,
        }
    try:
        accrual = service_fee_ledger.accrue(
            conn,
            liability_id=liability_id,
            operation_id=str(row["operation_id"]),
            payer_account=str(row["payer_account"]),
            network=str(row["network"]),
            asset_key=str(fee_line["asset_key"]),
            treasury_owner=terms["treasury_owner"],
            policy_id=terms["policy_id"],
            basis_atomic=basis,
            rate_bps=int(terms["rate_bps"]),
            evidence_id=str(evidence_id),
            evidence_kind=str(kind),
            source=str(source),
            now=now,
        )
    except service_fee_ledger.ServiceFeeLedgerError as exc:
        raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, f"service fee: {exc.code}: {exc.detail}", liability_id=liability_id, flow=FLOW_SERVICE_FEE) from None
    representable = int(accrual["newly_representable_atomic"])
    maximum = _atomic_value(fee_line["max_atomic"], what="service fee maximum", liability_id=liability_id)
    if str(fee_line["line_state"]) != LINE_EXACT:
        conn.execute(
            f"UPDATE {_LINES_TABLE} SET actual_atomic=?, line_state=?, evidence_id=?, resolved_epoch=? WHERE line_id=?",
            (_atomic_text(min(representable, maximum)), LINE_EXACT, str(evidence_id), now, int(fee_line["line_id"])),
        )
    position = dict(accrual.get("owed_after") or {})
    _journal(
        conn,
        "money_service_fee_accrued",
        flow=FLOW_SERVICE_FEE,
        liability_id=liability_id,
        detail={
            "operation_id": str(row["operation_id"]),
            "policy_id": terms["policy_id"],
            "rate_bps": int(terms["rate_bps"]),
            "basis_atomic": _atomic_text(basis),
            "fee_numerator": str(accrual.get("numerator") or "0"),
            "newly_representable_atomic": representable,
            "owed_atomic_after": int(position.get("owed_atomic") or 0),
            "carry_numerator_after": int(position.get("carry_numerator") or 0),
            "treasury_owner": terms["treasury_owner"],
            "idempotent": bool(accrual.get("idempotent")),
            "late_admission": bool(late),
        },
    )
    return {
        "policy_id": terms["policy_id"],
        "rate_bps": int(terms["rate_bps"]),
        "basis_atomic": basis,
        "fee_numerator": int(accrual.get("numerator") or 0),
        "identity_key": str(accrual.get("identity_key") or ""),
        "newly_representable_atomic": representable,
        "owed_after": position,
        "idempotent": bool(accrual.get("idempotent")),
        "late_admission": bool(late),
    }


def service_fee_for_liability(liability_id: str) -> dict[str, Any] | None:
    """The service-fee facts of one liability: the reserved ceiling line, the exact accrual (numerator) once the
    payment was accepted, any reversals, and the identity's position now. None when the liability carries no fee."""
    from core import service_fee_ledger

    with _read_conn("service fee read") as conn:
        row = conn.execute(f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (str(liability_id),)).fetchone()
        if row is None:
            return None
        line = conn.execute(f"SELECT * FROM {_LINES_TABLE} WHERE liability_id=? AND flow=?", (str(liability_id), FLOW_SERVICE_FEE)).fetchone()
        if line is None:
            return None
        terms = _service_fee_terms(row)
        entries = service_fee_ledger.entries_for_liability(conn, str(liability_id))
        accrual = next((entry for entry in entries if entry["kind"] == service_fee_ledger.ENTRY_ACCRUAL), None)
        reversals = [entry for entry in entries if entry["kind"] == service_fee_ledger.ENTRY_REVERSAL]
        asset = AssetIdentity.from_key(str(line["asset_key"]))
        decimals = int(asset.decimals)
        state = str(row["state"])
        if accrual is not None:
            fee_state = "accrued_owed"
        elif state in HELD_STATES:
            fee_state = "reserved_not_owed"
        elif state == LIABILITY_SETTLED:
            fee_state = "settled_without_accrual"
        else:
            fee_state = "released_not_owed"
        numerator = int(accrual["numerator"]) if accrual is not None else 0
        net_numerator = numerator + sum(int(entry["numerator"]) for entry in reversals)
        position = service_fee_ledger.owed(conn, str(accrual["identity_key"])) if accrual is not None else None
        basis = int(accrual["basis_atomic"]) if accrual is not None else _atomic_value(line["max_atomic"], what="service fee maximum", liability_id=str(liability_id))
        return {
            "state": fee_state,
            "liability_state": state,
            "policy_id": terms["policy_id"],
            "rate_bps": int(terms["rate_bps"]) if terms["rate_bps"].isdigit() else None,
            "treasury_owner": terms["treasury_owner"],
            "asset_key": str(line["asset_key"]),
            "asset": asset.asset,
            "decimals": decimals,
            "reserved_ceiling_atomic": _atomic_value(line["max_atomic"], what="service fee maximum", liability_id=str(liability_id)),
            "line_state": str(line["line_state"]),
            "line_actual_atomic": None if line["actual_atomic"] is None else _atomic_value(line["actual_atomic"], what="service fee actual", liability_id=str(liability_id)),
            "basis_atomic": basis if accrual is not None else None,
            "fee_numerator": numerator if accrual is not None else None,
            "fee_exact": service_fee_ledger.numerator_decimal(numerator, decimals) if accrual is not None else None,
            "fee_exact_atomic": service_fee_ledger.numerator_atomic_text(numerator) if accrual is not None else None,
            "reversed_numerator": sum(int(entry["numerator"]) for entry in reversals),
            "net_numerator": net_numerator,
            "reversals": [{"evidence_id": entry["evidence_id"], "refunded_basis_atomic": int(entry["basis_atomic"]), "numerator": int(entry["numerator"])} for entry in reversals],
            "identity_key": str(accrual["identity_key"]) if accrual is not None else "",
            "position": position,
        }


def service_fee_position(identity_key: str) -> dict[str, Any]:
    """One identity's exact position (owed, carry, held by open collections, collectible) -- a read."""
    from core import service_fee_ledger

    with _read_conn("service fee position") as conn:
        return service_fee_ledger.owed(conn, str(identity_key))


def reverse_service_fee(liability_id: str, evidence: SettlementEvidence, *, refunded_basis_atomic: int) -> dict[str, Any]:
    """Reverse the fee on a PROVEN refund of one operation's payment: provider or operator evidence only, idempotent
    per evidence id, bounded by the operation's own accrual. Journaled like every money transition."""
    from core import service_fee_ledger

    if not isinstance(evidence, SettlementEvidence):
        raise _invalid("reverse_service_fee needs SettlementEvidence")
    kind = str(evidence.evidence_kind or "")
    if kind not in (EVIDENCE_PROVIDER_USAGE_RECEIPT, EVIDENCE_PROVIDER_BILLING_STATEMENT, EVIDENCE_OPERATOR_ATTESTATION):
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, f"{kind[:40]!r} does not prove a provider refund")
    if str(evidence.source or "") not in ("provider", "user"):
        raise EffectBudgetRefusedError(MONEY_EVIDENCE_INSUFFICIENT, "a refund is proven by the provider or attested by the operator, never inferred")
    if not _OPERATION_ID_RE.fullmatch(str(evidence.evidence_id or "")):
        raise _invalid("evidence_id must be a public identifier")
    refunded = require_atomic(refunded_basis_atomic, what="refunded amount")
    with _write_txn("service fee reversal") as txn:
        conn = txn.conn
        row = _liability_row(conn, liability_id)
        if kind == EVIDENCE_OPERATOR_ATTESTATION:
            _require_operator(conn, evidence.operator_token, "service fee reversal")
        try:
            outcome = service_fee_ledger.reverse(
                conn, liability_id=str(liability_id), refunded_basis_atomic=refunded, evidence_id=str(evidence.evidence_id), evidence_kind=kind, source=str(evidence.source),
            )
        except service_fee_ledger.ServiceFeeLedgerError as exc:
            raise _RefusalError(MONEY_SETTLEMENT_CONFLICT, f"service fee reversal: {exc.code}: {exc.detail}", liability_id=str(liability_id), flow=FLOW_SERVICE_FEE) from None
        _journal(
            conn,
            "money_service_fee_reversed",
            flow=FLOW_SERVICE_FEE,
            liability_id=str(liability_id),
            detail={"operation_id": str(row["operation_id"]), "evidence_id": str(evidence.evidence_id), "evidence_kind": kind, "refunded_basis_atomic": _atomic_text(refunded), "numerator": str(outcome.get("numerator") or 0), "idempotent": bool(outcome.get("idempotent"))},
        )
        return outcome


# the proofs a reconciliation cites; the first three are the unit law's instance-death proofs
RECONCILE_PROOF_INSTANCE_ABSENT = "instance_absent"
RECONCILE_PROOF_QUOTE_EXPIRED = "quote_window_expired"
RECONCILE_DEATH_PROOFS = (
    _eb.INSTANCE_DEATH_CLOSED,
    _eb.INSTANCE_DEATH_STALE,
    _eb.INSTANCE_DEATH_PROCESS_GONE,
    RECONCILE_PROOF_INSTANCE_ABSENT,
)

_MONEY_RECONCILED: dict[str, bool] = {}


def reconcile_money_liabilities(*, force: bool = False) -> list[dict[str, Any]]:
    """The monetary half of dead-instance reconciliation, plus the providers' evidence-backed
    hold recovery.

    reserved liabilities of a dead reserving instance, or past their quote
    window, never had a dispatch claim: released. dispatching or pending
    liabilities of a dead claimant may have been paid: unknown, maximum held.
    Unknown holds whose own recorded refusal is a provider admission refusal
    settle at their proven zero through the provider authority's recovery.
    Runs once per process on first money use; `force=True` reruns it.
    Every transition journals and returns the instance that judged it
    (registered only when it changes something), the instance it judged and
    the proof relied on: `instance_closed`, `heartbeat_stale`, `process_gone`,
    `instance_absent` or `quote_window_expired`.
    """
    if not force and _MONEY_RECONCILED.get("done"):
        return []
    _MONEY_RECONCILED["done"] = True
    changes: list[dict[str, Any]] = []
    try:
        probe = _eb._budget_connection()
        try:
            if not _money_store_present(probe):
                return []
        finally:
            with suppress(Exception):
                probe.close()
    except Exception:
        pass
    try:
        with _write_txn("money reconciliation") as txn:
            conn = txn.conn
            now = _now()
            instances = {
                str(row["instance_id"]): row
                for row in conn.execute(f"SELECT * FROM {_eb._INSTANCES_TABLE}").fetchall()
            }

            def death_proof(instance_id: str) -> str:
                instance = instances.get(str(instance_id or ""))
                if instance is None:
                    # never registered (its registering write rolled back) or pruned
                    return RECONCILE_PROOF_INSTANCE_ABSENT
                return _eb._instance_death_reason(instance, now)

            reconciler: dict[str, str] = {}

            def judged_by() -> str:
                if "id" not in reconciler:
                    reconciler["id"] = _eb._current_instance(conn)
                return reconciler["id"]

            rows = conn.execute(
                f"SELECT * FROM {_LIABILITIES_TABLE} WHERE state IN (?, ?, ?)",
                (LIABILITY_RESERVED, LIABILITY_DISPATCHING, LIABILITY_PENDING),
            ).fetchall()
            for row in rows:
                state = str(row["state"])
                liability_id = str(row["liability_id"])
                if state == LIABILITY_RESERVED:
                    try:
                        expired = bool(float(row["expires_epoch"] or 0.0)) and float(row["expires_epoch"]) <= now
                    except (TypeError, ValueError):
                        continue
                    judged = str(row["instance_id"])
                    proof = RECONCILE_PROOF_QUOTE_EXPIRED if expired else death_proof(judged)
                    if not proof:
                        continue
                    reason = "reconciled: quote window expired before dispatch" if expired else "reconciled: reserving instance died before any dispatch claim"
                    evidence = {"reason": reason, "judged_instance_id": judged, "proof": proof, "reconciler_instance_id": judged_by()}
                    _transition(conn, row, LIABILITY_RELEASED, sets={"close_reason": reason})
                    _journal(conn, "money_reconciled_release", liability_id=liability_id, detail=evidence)
                    changes.append({"liability_id": liability_id, "from": state, "to": LIABILITY_RELEASED, **evidence})
                else:
                    judged = str(row["claim_instance_id"])
                    proof = death_proof(judged)
                    if not proof:
                        continue
                    reason = "reconciled: dispatch claimant died; the payment may have gone out"
                    evidence = {"reason": reason, "judged_instance_id": judged, "proof": proof, "reconciler_instance_id": judged_by()}
                    _transition(conn, row, LIABILITY_UNKNOWN, sets={"unknown_reason": reason})
                    _journal(conn, "money_reconciled_unknown", liability_id=liability_id, detail={**evidence, "from_state": state})
                    changes.append({"liability_id": liability_id, "from": state, "to": LIABILITY_UNKNOWN, **evidence})
    except Exception:
        _MONEY_RECONCILED["done"] = False
        raise
    # Provider-owned recovery of evidence-backed holds: the UsePod authority settles unknown
    # prepaid holds whose own recorded refusal is a documented admission refusal at their proven
    # zero. It reads only recorded facts and settles through the same law (idempotent, conflict
    # refusing), so it runs as part of the normal reconciliation rather than as a separate door.
    try:
        from core.usepod.money_law import recover_admission_refusal_holds

        changes.extend(recover_admission_refusal_holds())
    except ImportError:
        pass
    return changes


def _prune_money(conn: Any, now: float) -> None:
    cutoff = now - MONEY_RETENTION_SECONDS
    stale = [
        str(row["liability_id"])
        for row in conn.execute(
            f"SELECT liability_id FROM {_LIABILITIES_TABLE} WHERE state IN (?, ?, ?) AND updated_epoch < ?",
            (LIABILITY_SETTLED, LIABILITY_UNSENT, LIABILITY_RELEASED, cutoff),
        ).fetchall()
    ]
    for liability_id in stale:
        conn.execute(f"DELETE FROM {_LINES_TABLE} WHERE liability_id=?", (liability_id,))
        conn.execute(f"DELETE FROM {_EVIDENCE_TABLE} WHERE liability_id=?", (liability_id,))
        conn.execute(f"DELETE FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (liability_id,))


def reset_money_process_state() -> None:
    """Tests only: forget the once-per-process reconciliation flag."""
    _MONEY_RECONCILED.clear()


# ---------------------------------------------------------------------------
# liquidity observations and funding policy
# ---------------------------------------------------------------------------


def record_liquidity_observation(
    *,
    account: str,
    asset: AssetIdentity,
    balance_atomic: int,
    source: str,
    verified: bool,
    observed_epoch: float | None = None,
) -> None:
    """A balance read by its owner (wallet RPC, provider balance header with a
    verified account identity). Monotonic: an older observation never
    replaces a newer one. Liquidity is a safety check, never authority."""
    clean_account = _clean_account(account, what="account")
    if not isinstance(asset, AssetIdentity):
        raise _invalid("an observation needs an AssetIdentity")
    balance = require_atomic(balance_atomic, what="balance", allow_zero=True)
    clean_source = str(source or "").strip()
    if not clean_source or len(clean_source) > 120 or "://" in clean_source:
        raise _invalid("source must name the reader (e.g. 'rpc:getBalance'), never a URL")
    observed = _now() if observed_epoch is None else float(observed_epoch)
    if observed > _now() + 5.0:
        raise _invalid("an observation cannot come from the future")
    with _write_txn("liquidity observation") as txn:
        txn.conn.execute(
            f"INSERT INTO {_LIQUIDITY_TABLE} (account, asset_key, balance_atomic, observed_epoch, source, verified, recorded_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account, asset_key) DO UPDATE SET "
            "balance_atomic=excluded.balance_atomic, observed_epoch=excluded.observed_epoch, source=excluded.source, "
            "verified=excluded.verified, recorded_epoch=excluded.recorded_epoch "
            "WHERE excluded.observed_epoch >= effect_budget_money_liquidity.observed_epoch",
            (clean_account, asset.key, _atomic_text(balance), observed, clean_source, 1 if verified else 0, _now()),
        )


def set_funding_policy(
    token: OperatorBudgetToken,
    *,
    provider_id: str,
    provider_account: str,
    asset: AssetIdentity,
    mode: str,
    threshold_atomic: int = 0,
    topup_atomic: int = 0,
    auto_grant_id: str = "",
) -> dict[str, Any]:
    if mode not in FUNDING_MODES:
        raise _invalid(f"{mode!r} is not a funding mode")
    clean_provider = _clean_ident(provider_id, what="provider_id")
    clean_account = _clean_account(provider_account, what="provider_account")
    if not isinstance(asset, AssetIdentity):
        raise _invalid("a funding policy needs an AssetIdentity")
    threshold = require_atomic(threshold_atomic, what="threshold", allow_zero=True)
    topup = require_atomic(topup_atomic, what="top-up amount", allow_zero=True)
    if mode != FUNDING_MANUAL and (threshold <= 0 or topup <= 0):
        raise _invalid("ask_below_threshold and auto need a threshold and a top-up amount")
    if mode == FUNDING_AUTO and not auto_grant_id:
        raise _invalid("auto funding needs its own auto_topup grant")
    with _write_txn("funding policy") as txn:
        token_id = _require_operator(txn.conn, token, "funding policy")
        if mode == FUNDING_AUTO:
            grant = _grant_row(txn.conn, str(auto_grant_id))
            if grant.spec["kind"] != GRANT_AUTO_TOPUP:
                raise _RefusalError(MONEY_AUTHORITY_INVALID, "auto funding must name an auto_topup grant")
        txn.conn.execute(
            f"INSERT INTO {_FUNDING_TABLE} (provider_id, provider_account, asset_key, mode, threshold_atomic, topup_atomic, "
            "auto_grant_id, adjusted_at, adjusted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider_id, provider_account, asset_key) DO UPDATE SET mode=excluded.mode, "
            "threshold_atomic=excluded.threshold_atomic, topup_atomic=excluded.topup_atomic, "
            "auto_grant_id=excluded.auto_grant_id, adjusted_at=excluded.adjusted_at, adjusted_by=excluded.adjusted_by",
            (clean_provider, clean_account, asset.key, mode, _atomic_text(threshold), _atomic_text(topup), str(auto_grant_id or ""), _eb._utcnow_iso(), token_id),
        )
        _journal(
            txn.conn,
            "money_funding_policy_set",
            detail={"provider_id": clean_provider, "provider_account": clean_account, "asset_key": asset.key, "mode": mode, "token_id": token_id},
        )
    return {"provider_id": clean_provider, "provider_account": clean_account, "asset_key": asset.key, "mode": mode}


def evaluate_funding_policy(*, provider_id: str, provider_account: str, asset: AssetIdentity) -> FundingDecision:
    """Read-only: should VOOL ask, stay quiet, or is a bounded auto top-up
    eligible right now? Grants nothing — the top-up itself still reserves
    under its own grant and claims dispatch."""
    with _read_conn("funding policy read") as conn:
        row = conn.execute(
            f"SELECT * FROM {_FUNDING_TABLE} WHERE provider_id=? AND provider_account=? AND asset_key=?",
            (str(provider_id), str(provider_account), asset.key),
        ).fetchone()
        if row is None or str(row["mode"]) == FUNDING_MANUAL:
            return FundingDecision(mode=FUNDING_MANUAL, action="manual_only", reason="top-ups happen only when the owner starts one")
        mode = str(row["mode"])
        threshold = _atomic_value(row["threshold_atomic"], what="funding threshold")
        topup = _atomic_value(row["topup_atomic"], what="funding top-up")
        now = _now()
        observation = _observation(conn, str(provider_account), asset.key)
        if observation is None or not observation["verified"] or now - observation["observed_epoch"] > CREDIT_OBSERVATION_TTL_SECONDS:
            return FundingDecision(mode=mode, action="none", reason="no fresh verified provider balance; nothing is decided on an unknown balance", topup_atomic=topup)
        committed = _committed_debits(conn, str(provider_account), asset.key, (FLOW_PROVIDER_CREDIT_DEBIT,), observation["observed_epoch"])
        available = observation["balance"] - committed + _verified_credits_since(conn, str(provider_account), asset.key, observation["observed_epoch"])
        if available >= threshold:
            return FundingDecision(mode=mode, action="none", reason="verified credit is at or above the threshold", available_verified_credit_atomic=max(available, 0), topup_atomic=topup)
        if mode == FUNDING_ASK_BELOW_THRESHOLD:
            return FundingDecision(mode=mode, action="ask", reason="verified credit fell below the threshold; ask the owner", available_verified_credit_atomic=max(available, 0), topup_atomic=topup)
        grant_id = str(row["auto_grant_id"] or "")
        grant_row = conn.execute(f"SELECT * FROM {_GRANTS_TABLE} WHERE grant_id=?", (grant_id,)).fetchone()
        if grant_row is None:
            return FundingDecision(mode=mode, action="auto_blocked", reason="the auto_topup grant no longer exists", available_verified_credit_atomic=max(available, 0), auto_grant_id=grant_id, topup_atomic=topup)
        grant = _grant_from_row(grant_row)
        if grant.state != GRANT_ACTIVE or now >= float(grant.spec["expires_epoch"]):
            return FundingDecision(mode=mode, action="auto_blocked", reason="the auto_topup grant is revoked or expired; ask the owner", available_verified_credit_atomic=max(available, 0), auto_grant_id=grant_id, topup_atomic=topup)
        usage = _grant_usage(conn, grant)
        max_total = _atomic_value(grant.spec["max_total_atomic"], what="grant total")
        interval = float(grant.spec["min_interval_seconds"] or 0.0)
        if usage["principal"] + topup > max_total:
            return FundingDecision(mode=mode, action="auto_blocked", reason="the auto_topup grant is exhausted", available_verified_credit_atomic=max(available, 0), auto_grant_id=grant_id, topup_atomic=topup)
        if usage["concurrent"] >= 1 or (interval and usage["last_created"] and now - usage["last_created"] < interval):
            return FundingDecision(mode=mode, action="auto_blocked", reason="an auto top-up is in flight or the frequency limit has not elapsed", available_verified_credit_atomic=max(available, 0), auto_grant_id=grant_id, topup_atomic=topup)
        return FundingDecision(mode=mode, action="auto_eligible", reason="verified credit below threshold and the auto_topup grant has room", available_verified_credit_atomic=max(available, 0), auto_grant_id=grant_id, topup_atomic=topup)


# ---------------------------------------------------------------------------
# reads: liabilities and projections
# ---------------------------------------------------------------------------


def liability(liability_id: str) -> dict[str, Any] | None:
    with _read_conn("liability read") as conn:
        row = conn.execute(f"SELECT * FROM {_LIABILITIES_TABLE} WHERE liability_id=?", (str(liability_id),)).fetchone()
        if row is None:
            return None
        receipt = _receipt(conn, row).to_dict()
        receipt.update(
            {
                "unknown_reason": str(row["unknown_reason"] or ""),
                "close_reason": str(row["close_reason"] or ""),
                "over_cap": bool(int(row["over_cap"] or 0)),
                "provider_id": str(row["provider_id"] or ""),
                "provider_account": str(row["provider_account"] or ""),
                "model_id": str(row["model_id"] or ""),
                "route": str(row["route"] or ""),
                "task_id": str(row["task_id"] or ""),
                "session_id": str(row["session_id"] or ""),
                "agent_id": str(row["agent_id"] or ""),
                "executor": str(row["executor"] or ""),
                "evidence": [
                    {"evidence_id": str(item["evidence_id"]), "evidence_kind": str(item["evidence_kind"]), "source": str(item["source"])}
                    for item in conn.execute(
                        f"SELECT evidence_id, evidence_kind, source FROM {_EVIDENCE_TABLE} WHERE liability_id=? ORDER BY created_epoch",
                        (str(liability_id),),
                    ).fetchall()
                ],
            }
        )
    if any(line["flow"] == FLOW_SERVICE_FEE for line in receipt["lines"]):
        receipt["service_fee"] = service_fee_for_liability(str(liability_id))
    return receipt


def liability_for_operation(operation_id: str) -> dict[str, Any] | None:
    with _read_conn("liability read") as conn:
        row = conn.execute(f"SELECT liability_id FROM {_LIABILITIES_TABLE} WHERE operation_id=?", (str(operation_id),)).fetchone()
    return liability(str(row["liability_id"])) if row is not None else None


def liabilities(*, state: str = "", grant_id: str = "", effect_id: str = "", limit: int = 500) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("state", state), ("grant_id", grant_id), ("effect_id", effect_id)):
        if value:
            clauses.append(f"{column}=?")
            params.append(str(value))
    query = f"SELECT liability_id FROM {_LIABILITIES_TABLE}"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_epoch LIMIT ?"
    params.append(max(1, min(int(limit), 5000)))
    with _read_conn("liabilities read") as conn:
        ids = [str(row["liability_id"]) for row in conn.execute(query, params).fetchall()]
    out = []
    for liability_id in ids:
        found = liability(liability_id)
        if found is not None:
            out.append(found)
    return out


def money_projection(*, provider_id: str = "", task_id: str = "", session_id: str = "", grant_id: str = "") -> dict[str, Any]:
    """The four independent money facts, per asset and account, with residual
    uncertainty separated from settled truth. Amounts are exact decimal
    strings of atomic units. A corrupt row is listed, never summed as zero."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("m.provider_id", provider_id), ("m.task_id", task_id), ("m.session_id", session_id), ("m.grant_id", grant_id)):
        if value:
            clauses.append(f"{column}=?")
            params.append(str(value))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    buckets: dict[str, dict[str, dict[str, int]]] = {flow: {} for flow in MONEY_FLOWS}
    states: dict[str, int] = {}
    unknown: list[dict[str, Any]] = []
    corrupt: list[str] = []
    credit_verified: dict[str, int] = {}
    credit_unverified: dict[str, int] = {}
    over_cap: list[str] = []
    with _read_conn("money projection") as conn:
        rows = conn.execute(
            f"SELECT m.liability_id, m.state, m.operation_kind, m.unknown_reason, m.grant_id, m.over_cap, "
            f"l.flow, l.asset_key, l.account, l.max_atomic, l.actual_atomic, l.line_state, l.verified "
            f"FROM {_LIABILITIES_TABLE} m JOIN {_LINES_TABLE} l ON l.liability_id = m.liability_id{where}",
            params,
        ).fetchall()
        seen_states: dict[str, str] = {}
        for row in rows:
            liability_id = str(row["liability_id"])
            try:
                state = _state_of(row, liability_id=liability_id)
                amount = _line_amount(row, liability_id=liability_id)
                maximum = _atomic_value(row["max_atomic"], what="line maximum", liability_id=liability_id)
            except _CorruptRowError:
                if liability_id not in corrupt:
                    corrupt.append(liability_id)
                continue
            flow = str(row["flow"])
            if flow not in MONEY_FLOWS:
                if liability_id not in corrupt:
                    corrupt.append(liability_id)
                continue
            seen_states[liability_id] = state
            if int(row["over_cap"] or 0) and liability_id not in over_cap:
                over_cap.append(liability_id)
            key = f"{row['account']}|{row['asset_key']}"
            bucket = buckets[flow].setdefault(key, {"settled_exact": 0, "settled_bounded": 0, "held": 0, "unknown": 0})
            line_state = str(row["line_state"])
            if state in CLOSED_STATES:
                continue
            if state == LIABILITY_SETTLED:
                if line_state == LINE_EXACT:
                    bucket["settled_exact"] += amount
                else:
                    bucket["settled_bounded"] += maximum
            elif state == LIABILITY_UNKNOWN:
                bucket["unknown"] += maximum
            else:
                bucket["held"] += maximum
            if flow == FLOW_PROVIDER_CREDIT_CREDIT and state == LIABILITY_SETTLED and line_state == LINE_EXACT:
                target = credit_verified if int(row["verified"] or 0) else credit_unverified
                target[key] = target.get(key, 0) + amount
        for state in seen_states.values():
            states[state] = states.get(state, 0) + 1
        for row in conn.execute(
            f"SELECT m.liability_id, m.operation_kind, m.unknown_reason, m.grant_id FROM {_LIABILITIES_TABLE} m "
            f"{where + (' AND ' if where else ' WHERE ')}m.state=?",
            (*params, LIABILITY_UNKNOWN),
        ).fetchall():
            unknown.append(
                {
                    "liability_id": str(row["liability_id"]),
                    "operation_kind": str(row["operation_kind"]),
                    "reason": str(row["unknown_reason"] or ""),
                    "grant_id": str(row["grant_id"]),
                }
            )

    def render(values: dict[str, dict[str, int]]) -> dict[str, dict[str, str]]:
        return {key: {name: _atomic_text(amount) for name, amount in bucket.items()} for key, bucket in sorted(values.items())}

    credit_accounts = set(credit_verified) | set(credit_unverified) | set(buckets[FLOW_PROVIDER_CREDIT_DEBIT])
    provider_credit = {}
    for key in sorted(credit_accounts):
        debit = buckets[FLOW_PROVIDER_CREDIT_DEBIT].get(key, {"settled_exact": 0, "settled_bounded": 0, "held": 0, "unknown": 0})
        spent = debit["settled_exact"] + debit["settled_bounded"] + debit["held"] + debit["unknown"]
        provider_credit[key] = {
            "verified_credited": _atomic_text(credit_verified.get(key, 0)),
            "unverified_credited_not_usable": _atomic_text(credit_unverified.get(key, 0)),
            "debited_or_held": _atomic_text(spent),
            "usable_verified_remaining": _atomic_text(max(credit_verified.get(key, 0) - spent, 0)),
        }
    return {
        "gross_wallet_outflow": render(buckets[FLOW_WALLET_OUTFLOW]),
        "network_fees": render(buckets[FLOW_NETWORK_FEE]),
        "inference_expense": render(buckets[FLOW_INFERENCE_EXPENSE]),
        "provider_credit_debits": render(buckets[FLOW_PROVIDER_CREDIT_DEBIT]),
        "provider_credit": provider_credit,
        "liability_states": dict(sorted(states.items())),
        "unknown_liabilities": unknown,
        "over_cap_liabilities": over_cap,
        "corrupt_liabilities": corrupt,
    }


def money_contract() -> dict[str, Any]:
    """The typed contract provider and crypto callers integrate against."""
    return {
        "contract_id": "effect-budget-money-contract/v1",
        "flows": sorted(MONEY_FLOWS),
        "operation_kinds": {kind: {"required": list(schema["required"]), "optional": list(schema["optional"]), "grant_consumption_flow": schema["bound"]} for kind, schema in sorted(_OPERATION_SCHEMA.items())},
        "grant_kinds": {kind: sorted(ops) for kind, ops in sorted(_GRANT_KIND_OPERATIONS.items())},
        "scopes": list(MONEY_SCOPES),
        "states": {"held": list(HELD_STATES), "settled": LIABILITY_SETTLED, "closed": list(CLOSED_STATES)},
        "sequence": [
            "grant_money_authority (operator, outside effect scopes)",
            "record_liquidity_observation (wallet debits; verified provider credit)",
            "reserve_liability (atomic: grant + rules + liquidity + idempotency)",
            "claim_dispatch (fencing token) BEFORE signing/broadcast/provider request",
            "record_dispatched | record_unsent(proof) | record_unknown",
            "settle_liability(evidence) — idempotent, conflict-refusing, bounded when unproven",
            "reconcile_money_liabilities (dead instances; each transition journals its judge, the judged instance and the proof)",
        ],
        "settlement_evidence_kinds": {kind: sorted(flows) for kind, flows in sorted(_EVIDENCE_FLOWS.items())},
        "unsent_proofs": {"claimant": sorted(_CLAIMANT_UNSENT_PROOFS), "external": sorted(_EXTERNAL_UNSENT_PROOFS), "operator": UNSENT_OPERATOR_ATTESTATION},
        "reconciliation": {
            "released_when_never_claimed": [*RECONCILE_DEATH_PROOFS, RECONCILE_PROOF_QUOTE_EXPIRED],
            "unknown_when_claimant_gone": list(RECONCILE_DEATH_PROOFS),
            "journal_detail": ["reason", "judged_instance_id", "proof", "reconciler_instance_id"],
        },
        "refusal_codes": list(MONEY_REFUSAL_CODES),
    }


__all__ = [
    "CLOSED_STATES",
    "CREDIT_LIQUIDITY_NOT_REQUIRED",
    "CREDIT_LIQUIDITY_REQUIRED",
    "EVIDENCE_BALANCE_DELTA",
    "EVIDENCE_CHAIN_CONFIRMATION",
    "EVIDENCE_OPERATOR_ATTESTATION",
    "EVIDENCE_PROVIDER_BILLING_STATEMENT",
    "EVIDENCE_PROVIDER_USAGE_RECEIPT",
    "EVIDENCE_USAGE_PRICED",
    "FLOW_INFERENCE_EXPENSE",
    "FLOW_NETWORK_FEE",
    "FLOW_PROVIDER_CREDIT_CREDIT",
    "FLOW_PROVIDER_CREDIT_DEBIT",
    "FLOW_SERVICE_FEE",
    "FLOW_WALLET_OUTFLOW",
    "FUNDING_ASK_BELOW_THRESHOLD",
    "FUNDING_AUTO",
    "FUNDING_MANUAL",
    "GRANT_AUTO_TOPUP",
    "GRANT_PROVIDER_BUDGET",
    "GRANT_SINGLE_PAYMENT",
    "GRANT_TASK_ENVELOPE",
    "HELD_STATES",
    "LIABILITY_DISPATCHING",
    "LIABILITY_PENDING",
    "LIABILITY_RELEASED",
    "LIABILITY_RESERVED",
    "LIABILITY_SETTLED",
    "LIABILITY_UNKNOWN",
    "LIABILITY_UNSENT",
    "MONEY_FLOWS",
    "MONEY_REFUSAL_CODES",
    "MONEY_SCOPES",
    "OP_INFERENCE_PREPAID",
    "OP_INFERENCE_X402",
    "OP_PROVIDER_TOPUP",
    "OP_WALLET_PAYMENT",
    "SCOPE_ACCOUNT",
    "UNSENT_CHAIN_EXPIRED_NOT_LANDED",
    "UNSENT_CONNECTION_NOT_ESTABLISHED",
    "UNSENT_LOCAL_REFUSAL",
    "UNSENT_OPERATOR_ATTESTATION",
    "UNSENT_PROVIDER_NOT_RECEIVED",
    "UNSENT_SIGNING_REQUEST_EXPIRED",
    "UNSENT_VALIDATED_REJECTION",
    "AssetIdentity",
    "CreditLine",
    "DispatchClaim",
    "FundingDecision",
    "LiabilityReceipt",
    "LiabilityRequest",
    "MoneyGrant",
    "MoneyGrantSpec",
    "MoneyIdentity",
    "MoneyLine",
    "MoneyRule",
    "MoneyRuleAdjustment",
    "SettlementEvidence",
    "UnsentEvidence",
    "apply_operator_money_adjustment",
    "bind_liability_effect",
    "claim_dispatch",
    "conversion_bound_view",
    "convert_up_bound",
    "ensure_money_tables",
    "evaluate_funding_policy",
    "format_atomic",
    "grant_headroom",
    "grant_money_authority",
    "liabilities",
    "liability",
    "liability_for_operation",
    "money_contract",
    "money_grant",
    "money_grants",
    "money_projection",
    "money_rules",
    "parse_decimal_amount",
    "reconcile_money_liabilities",
    "record_dispatched",
    "record_liquidity_observation",
    "record_unknown",
    "record_unsent",
    "release_unclaimed",
    "release_unclaimed_for_owner",
    "require_atomic",
    "reserve_liability",
    "reset_money_process_state",
    "retry_unsent",
    "reverse_service_fee",
    "revoke_money_authority",
    "service_fee_for_liability",
    "service_fee_position",
    "set_conversion_bound",
    "set_funding_policy",
    "settle_liability",
]
