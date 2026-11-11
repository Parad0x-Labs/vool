"""Agent allowances: the bounded money an agent may pay without asking, and the one decision that says so.

An allowance is the owner's standing answer for ONE agent, on one wallet, one network and one asset: at most this
much a payment, this much a window, this much in all, this many payments, to these services, until this date. It is
never an addition to the wallet's own ceilings (``core.wallet.limits``): a payment an allowance lets through still
holds against every wallet cap and stops at the panic freeze. It only replaces the question "may I?" when every rule
below passes, and it answers "ask the owner" or "no" for everything else.

The rules, each the answer to a known way agent payments go wrong:

* **The server does not choose.** A payment is only considered on the allowance's own network and asset, to a
  service origin the owner listed. Anything else is the owner's to decide.
* **Payee pinning.** A listed service has one recipient address, pinned from a payment the owner approved (or typed
  by the owner). No pin yet, or a different recipient, is the owner's to decide -- a swapped ``payTo`` never rides
  an allowance.
* **Price-jump guard.** The same request priced more than ``PRICE_JUMP_FACTOR`` times what it last cost asks.
* **No second payment for the same thing** while the first is unsettled, undelivered or unknown.
* **Loop limits.** A minimum gap between payments and a payment count, besides the amounts.
* **Breaker.** ``BREAKER_REFUSALS`` anomalous requests in a row, or ``BREAKER_UNDELIVERED`` paid requests in a row
  that delivered nothing, pause the allowance until the owner resumes it.
* **Attenuation.** An agent's helper gets a slice cut from the parent's own allowance: never more than the parent
  has left, never a service the parent lacks, counted against both.
* **Only the owner widens.** Creating, raising or resuming needs the wallet's approval credential
  (``custody.prove_owner``). Lowering, pausing and revoking never do.

Fail closed: no allowance, an unreadable row, an expired, paused or revoked one, an unknown agent, an unknown amount
-- none of them pays. Unknown outcomes stay held. Every decision, paid or not, is written to the decision log with
the agent, the service and the reason.

The decision and the allowance's hold are written on the caller's connection, inside the caller's BEGIN IMMEDIATE,
so they commit with the wallet's own hold or not at all.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from core.wallet import custody, limits
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.allowances"

STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_REVOKED = "revoked"

DECISION_PAY = "pay"
DECISION_ASK_OWNER = "ask_owner"
DECISION_REFUSE = "refuse"

SPEND_RESERVED = "reserved"
SPEND_SETTLED = "settled"
SPEND_RELEASED = "released"
_HELD = (SPEND_RESERVED, SPEND_SETTLED)

DELIVERED_UNKNOWN = "unknown"
DELIVERED_YES = "yes"
DELIVERED_NO = "no"

HOUR, DAY, WEEK, MONTH = 3_600, 86_400, 604_800, 2_592_000
WINDOWS: tuple[int, ...] = (HOUR, DAY, WEEK, MONTH)
MAX_LIFETIME_SECONDS = 90 * DAY
MAX_SERVICES = 32
PRICE_JUMP_FACTOR = 3
BREAKER_REFUSALS = 3
BREAKER_UNDELIVERED = 2
NOTICE_THRESHOLDS: tuple[int, ...] = (50, 80, 100)
_INT64_MAX = 9_223_372_036_854_775_807

REASON_FROZEN = "frozen"
REASON_NO_ALLOWANCE = "no_allowance"
REASON_UNREADABLE = "allowance_unreadable"
REASON_PAUSED = "paused"
REASON_EXPIRED = "expired"
REASON_NEW_SERVICE = "new_service"
REASON_FIRST_PAYMENT = "first_payment_to_service"
REASON_RECIPIENT_CHANGED = "recipient_changed"
REASON_OVER_PER_PAYMENT = "over_per_payment"
REASON_OVER_ASK_THRESHOLD = "over_ask_threshold"
REASON_PRICE_JUMP = "price_jump"
REASON_REPEAT_UNCONFIRMED = "repeat_payment_unconfirmed"
REASON_TOO_SOON = "too_soon"
REASON_COUNT_USED = "payment_count_used"
REASON_WINDOW_USED = "window_used"
REASON_TOTAL_USED = "total_used"
REASON_SERVICE_TOTAL_USED = "service_total_used"
REASON_INVALID_REQUEST = "invalid_request"
REASON_WITHIN = "within_allowance"

PAUSE_OWNER = "owner"
PAUSE_BREAKER_REFUSALS = "breaker_refusals"
PAUSE_BREAKER_UNDELIVERED = "breaker_undelivered"

#: Requests that look like an attack or a loop rather than a spent budget. Only these feed the breaker.
_STRIKE_REASONS = frozenset({REASON_RECIPIENT_CHANGED, REASON_PRICE_JUMP, REASON_TOO_SOON, REASON_REPEAT_UNCONFIRMED, REASON_OVER_PER_PAYMENT})

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9:_.\-]{0,63}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class ServiceRule:
    origin: str
    total_minor: int
    #: The recipient the owner typed, when they did. Empty: pinned from the first payment the owner approves.
    pay_to: str = ""


@dataclass(frozen=True)
class AllowanceSpec:
    agent_id: str
    wallet_id: str
    network: str
    asset: str
    per_payment_minor: int
    window_seconds: int
    window_minor: int
    total_minor: int
    max_payments: int
    min_interval_seconds: int
    expires_at: float
    services: tuple[ServiceRule, ...]
    #: Above this a payment asks even inside every other limit. 0: no such threshold.
    ask_above_minor: int = 0


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str
    allowance_id: str = ""
    #: Budget thresholds (percent of the window or the total) this payment crossed, for the owner's notice.
    notices: tuple[str, ...] = ()
    #: Set when this request tripped the breaker and paused the allowance.
    paused: str = ""
    retry_after_seconds: float = 0.0

    @property
    def pay(self) -> bool:
        return self.decision == DECISION_PAY


@dataclass
class _Row:
    allowance_id: str
    parent_id: str
    agent_id: str
    wallet_id: str
    network: str
    asset: str
    state: str
    pause_reason: str
    per_payment_minor: int
    window_seconds: int
    window_minor: int
    total_minor: int
    max_payments: int
    min_interval_seconds: int
    ask_above_minor: int
    expires_at: float
    strikes_refused: int
    strikes_undelivered: int
    services: dict[str, tuple[int, str]] = field(default_factory=dict)


class AllowanceRefused(ValueError):
    """A spec that names no bounded allowance: a missing, zero, negative, unbounded or inconsistent value."""


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def normalize_origin(value: str) -> str:
    """``scheme://host[:port]`` of a service, lower-cased. https only; http is accepted for this machine alone (the
    served tests' loopback services). "" when the value is not such an origin."""
    try:
        parts = urlsplit(str(value or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    scheme = (parts.scheme or "").lower()
    if not host or parts.username or parts.password:
        return ""
    if scheme != "https" and not (scheme == "http" and host in _LOOPBACK_HOSTS):
        return ""
    default = 443 if scheme == "https" else 80
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" + (f":{port}" if port and port != default else "")


def _positive(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > _INT64_MAX:
        raise AllowanceRefused(f"{what} must be a positive whole number; unbounded is refused")
    return value


def _clean_agent(agent_id: str) -> str:
    clean = str(agent_id or "").strip().lower()
    if not _AGENT_ID.match(clean):
        raise AllowanceRefused("agent_id is not a known agent name")
    return clean


def _canonical_network(network: str) -> str:
    from core.wallet import chains

    return chains.resolve_network(str(network or "")).network


def canonical_address(network: str, address: str) -> str:
    """One spelling per recipient: an EVM address compares without its checksum case, any other address exactly."""
    from core.wallet import chains

    clean = str(address or "").strip()
    try:
        return clean.lower() if chains.resolve_network(str(network or "")).is_evm else clean
    except Exception:
        return clean


def _validated(spec: AllowanceSpec, *, now: float) -> AllowanceSpec:
    agent = _clean_agent(spec.agent_id)
    per_payment = _positive(spec.per_payment_minor, "per_payment_minor")
    window_minor = _positive(spec.window_minor, "window_minor")
    total = _positive(spec.total_minor, "total_minor")
    max_payments = _positive(spec.max_payments, "max_payments")
    if spec.window_seconds not in WINDOWS:
        raise AllowanceRefused("window_seconds must be an hour, a day, a week or a month")
    if isinstance(spec.min_interval_seconds, bool) or not isinstance(spec.min_interval_seconds, int) or not 0 <= spec.min_interval_seconds <= MONTH:
        raise AllowanceRefused("min_interval_seconds must be a whole number of seconds")
    if isinstance(spec.ask_above_minor, bool) or not isinstance(spec.ask_above_minor, int) or not 0 <= spec.ask_above_minor <= _INT64_MAX:
        raise AllowanceRefused("ask_above_minor must be a whole number")
    if not per_payment <= window_minor <= total:
        raise AllowanceRefused("per payment must fit the window, and the window must fit the total")
    try:
        expires = float(spec.expires_at)
    except (TypeError, ValueError):
        raise AllowanceRefused("expires_at is required") from None
    if expires != expires or not now < expires <= now + MAX_LIFETIME_SECONDS:
        raise AllowanceRefused("expires_at must be in the future and at most 90 days away")
    if not 1 <= len(spec.services) <= MAX_SERVICES:
        raise AllowanceRefused("an allowance names at least one service and at most 32")
    try:
        network = _canonical_network(spec.network)
    except Exception:
        raise AllowanceRefused("network is not a known network") from None
    asset = str(spec.asset or "").strip().upper()
    if not asset:
        raise AllowanceRefused("asset is required")
    services: dict[str, ServiceRule] = {}
    from core.wallet import chains

    for rule in spec.services:
        origin = normalize_origin(rule.origin)
        if not origin:
            raise AllowanceRefused("a service must be an https address")
        if origin in services:
            raise AllowanceRefused("a service is listed twice")
        service_total = _positive(rule.total_minor, "service total_minor")
        if service_total > total:
            raise AllowanceRefused("a service total must fit the allowance total")
        pay_to = str(rule.pay_to or "").strip()
        if pay_to and not chains.destination_matches_family(network, pay_to):
            raise AllowanceRefused("a service recipient is not an address on this network")
        services[origin] = ServiceRule(origin, service_total, canonical_address(network, pay_to))
    return AllowanceSpec(agent_id=agent, wallet_id=str(spec.wallet_id), network=network, asset=asset, per_payment_minor=per_payment,
                         window_seconds=int(spec.window_seconds), window_minor=window_minor, total_minor=total, max_payments=max_payments,
                         min_interval_seconds=int(spec.min_interval_seconds), expires_at=expires, services=tuple(services.values()),
                         ask_above_minor=int(spec.ask_above_minor))


def _ask_ceiling(value: int) -> int:
    return value if value > 0 else _INT64_MAX


def _loosens(current: _Row, wanted: AllowanceSpec) -> bool:
    """Whether ``wanted`` lets through anything ``current`` refuses or asks about."""
    if (wanted.per_payment_minor > current.per_payment_minor or wanted.window_minor > current.window_minor or wanted.total_minor > current.total_minor
            or wanted.max_payments > current.max_payments or wanted.min_interval_seconds < current.min_interval_seconds
            or wanted.window_seconds < current.window_seconds or wanted.expires_at > current.expires_at
            or _ask_ceiling(wanted.ask_above_minor) > _ask_ceiling(current.ask_above_minor)):
        return True
    for rule in wanted.services:
        held = current.services.get(rule.origin)
        if held is None or rule.total_minor > held[0] or (rule.pay_to and rule.pay_to != held[1]):
            return True
    return False


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def _begin_immediate(conn: Any) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:  # already inside a transaction on this handle: the lock is ours
        if "within a transaction" not in str(exc):
            raise


_COLUMNS = ("allowance_id, parent_id, agent_id, wallet_id, network, asset, state, pause_reason, per_payment_minor, window_seconds, window_minor, "
            "total_minor, max_payments, min_interval_seconds, ask_above_minor, expires_at, strikes_refused, strikes_undelivered")


def _whole(value: Any) -> int:
    """A stored amount as it must be: a non-negative integer. Anything else is a corrupt row, never zero."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("not a whole number")
    return value


def _load(conn: Any, where: str, params: tuple[Any, ...]) -> _Row | None:
    """The allowance the clause names, or None. Raises ValueError on a row that cannot be read as written."""
    found = conn.execute(f"SELECT {_COLUMNS} FROM wallet_agent_allowances WHERE {where}", params).fetchone()
    if not found:
        return None
    row = _Row(
        allowance_id=str(found[0]), parent_id=str(found[1] or ""), agent_id=str(found[2]), wallet_id=str(found[3]), network=str(found[4]),
        asset=str(found[5]), state=str(found[6]), pause_reason=str(found[7] or ""), per_payment_minor=_whole(found[8]), window_seconds=_whole(found[9]),
        window_minor=_whole(found[10]), total_minor=_whole(found[11]), max_payments=_whole(found[12]), min_interval_seconds=_whole(found[13]),
        ask_above_minor=_whole(found[14]), expires_at=float(found[15]), strikes_refused=_whole(found[16]), strikes_undelivered=_whole(found[17]),
    )
    if row.state not in (STATE_ACTIVE, STATE_PAUSED, STATE_REVOKED) or row.window_seconds not in WINDOWS or row.expires_at != row.expires_at:
        raise ValueError("unknown state, window or expiry")
    if min(row.per_payment_minor, row.window_minor, row.total_minor, row.max_payments) <= 0:
        raise ValueError("a ceiling is not positive")
    for origin, total_minor, pay_to in conn.execute("SELECT origin, total_minor, pay_to FROM wallet_agent_allowance_services WHERE allowance_id = ?", (row.allowance_id,)).fetchall():
        if _whole(total_minor) <= 0:
            raise ValueError("a service ceiling is not positive")
        row.services[str(origin)] = (int(total_minor), str(pay_to or ""))
    return row


def _write_services(conn: Any, allowance_id: str, services: tuple[ServiceRule, ...], *, keep_pins: dict[str, tuple[int, str]] | None = None) -> None:
    now = utcnow()
    conn.execute("DELETE FROM wallet_agent_allowance_services WHERE allowance_id = ?", (allowance_id,))
    for rule in services:
        pay_to = rule.pay_to or ((keep_pins or {}).get(rule.origin, (0, ""))[1])
        conn.execute("INSERT INTO wallet_agent_allowance_services (allowance_id, origin, total_minor, pay_to, pinned_at) VALUES (?, ?, ?, ?, ?)",
                     (allowance_id, rule.origin, int(rule.total_minor), pay_to, now if pay_to else ""))


def _insert(conn: Any, spec: AllowanceSpec, *, parent_id: str = "") -> str:
    allowance_id = f"alw-{uuid.uuid4().hex[:20]}"
    now = utcnow()
    conn.execute(
        "INSERT INTO wallet_agent_allowances (allowance_id, parent_id, agent_id, wallet_id, network, asset, state, per_payment_minor, window_seconds, window_minor, "
        "total_minor, max_payments, min_interval_seconds, ask_above_minor, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (allowance_id, parent_id, spec.agent_id, spec.wallet_id, spec.network, spec.asset, STATE_ACTIVE, spec.per_payment_minor, spec.window_seconds, spec.window_minor,
         spec.total_minor, spec.max_payments, spec.min_interval_seconds, spec.ask_above_minor, spec.expires_at, now, now),
    )
    _write_services(conn, allowance_id, spec.services)
    return allowance_id


def _journal(kind: str, payload: dict[str, Any], source_context: dict[str, Any] | None) -> None:
    from core.wallet import receipts

    receipts.journal_security_event(kind, payload, source_context=source_context)


def _refused(reason: str, *, source_context: dict[str, Any] | None, **context: Any) -> Exception:
    return wallet_fault("wallet_limit_exceeded", authority=AUTHORITY, context={"limit": "agent_allowance", "reason": reason, **context}, source_context=source_context)


# ---------------------------------------------------------------------------
# the owner's doors
# ---------------------------------------------------------------------------

def _require_paying_wallet(wallet_id: str, *, source_context: dict[str, Any] | None) -> None:
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    if profile.mode != custody.MODE_POCKET_SEALED:
        # a watch-only wallet cannot pay and an external signer signs every payment in the owner's own app:
        # neither has a credential here to prove, so neither can carry a standing allowance
        raise _refused("wallet_cannot_carry_allowance", source_context=source_context, wallet_id=wallet_id)


def create_allowance(spec: AllowanceSpec, *, credential: str = "", now: float | None = None, source_context: dict[str, Any] | None = None) -> str:
    """Create an agent's allowance. Always the owner's act: the wallet's approval credential is proven first. One live
    allowance per agent, wallet, network and asset -- a second is refused, never merged."""
    moment = float(now if now is not None else time.time())
    try:
        clean = _validated(spec, now=moment)
    except AllowanceRefused as exc:
        raise _refused("invalid_allowance", source_context=source_context, detail=str(exc)) from None
    _require_paying_wallet(clean.wallet_id, source_context=source_context)
    if custody.agent_of(clean.wallet_id) != clean.agent_id:
        # the owner's own wallets sign nothing unattended: an allowance is carried by the agent's own wallet
        # (core.wallet.agent_wallets), so what it can lose is what the owner put there
        raise _refused("allowance_needs_the_agents_own_wallet", source_context=source_context, agent_id=clean.agent_id)
    custody.prove_owner(clean.wallet_id, credential=credential, purpose="create a spending allowance", source_context=source_context)
    try:
        with connection() as conn:
            _begin_immediate(conn)
            allowance_id = _insert(conn, clean)
    except sqlite3.IntegrityError:
        raise _refused("allowance_exists", source_context=source_context, agent_id=clean.agent_id) from None
    _journal("wallet_agent_allowance_created", {"allowance_id": allowance_id, "agent_id": clean.agent_id, "wallet_id": clean.wallet_id}, source_context)
    return allowance_id


def change_allowance(allowance_id: str, spec: AllowanceSpec, *, credential: str = "", now: float | None = None, source_context: dict[str, Any] | None = None) -> None:
    """Replace an allowance's terms. A change that lets nothing new through needs no credential; one that does is the
    owner's act. The comparison and the write share one transaction, so terms lowered meanwhile are never put back up
    by a write that was only checked against the old ones. Agent, wallet, network and asset never change."""
    moment = float(now if now is not None else time.time())
    try:
        clean = _validated(spec, now=moment)
    except AllowanceRefused as exc:
        raise _refused("invalid_allowance", source_context=source_context, detail=str(exc)) from None
    current = _read(allowance_id, source_context=source_context)
    if current.parent_id:
        raise _refused("slice_terms_are_fixed", source_context=source_context)
    if (clean.agent_id, clean.wallet_id, clean.network, clean.asset) != (current.agent_id, current.wallet_id, current.network, current.asset):
        raise _refused("allowance_identity_is_fixed", source_context=source_context)
    proven = False
    if _loosens(current, clean):
        custody.prove_owner(current.wallet_id, credential=credential, purpose="raise a spending allowance", source_context=source_context)
        proven = True
    with connection() as conn:
        _begin_immediate(conn)
        stored = _load_or_refuse(conn, allowance_id, source_context=source_context)
        if stored.state == STATE_REVOKED:
            raise _refused("allowance_revoked", source_context=source_context)
        if not proven and _loosens(stored, clean):
            raise _refused("credential_required", source_context=source_context)
        conn.execute(
            "UPDATE wallet_agent_allowances SET per_payment_minor = ?, window_seconds = ?, window_minor = ?, total_minor = ?, max_payments = ?, min_interval_seconds = ?, "
            "ask_above_minor = ?, expires_at = ?, updated_at = ? WHERE allowance_id = ?",
            (clean.per_payment_minor, clean.window_seconds, clean.window_minor, clean.total_minor, clean.max_payments, clean.min_interval_seconds,
             clean.ask_above_minor, clean.expires_at, utcnow(), allowance_id),
        )
        _write_services(conn, allowance_id, clean.services, keep_pins=stored.services)
    _journal("wallet_agent_allowance_changed", {"allowance_id": allowance_id, "agent_id": current.agent_id, "loosened": proven}, source_context)


def _load_or_refuse(conn: Any, allowance_id: str, *, source_context: dict[str, Any] | None) -> _Row:
    try:
        row = _load(conn, "allowance_id = ?", (str(allowance_id),))
    except (ValueError, TypeError):
        raise _refused(REASON_UNREADABLE, source_context=source_context, allowance_id=allowance_id) from None
    if row is None:
        raise _refused(REASON_NO_ALLOWANCE, source_context=source_context, allowance_id=allowance_id)
    return row


def _read(allowance_id: str, *, source_context: dict[str, Any] | None) -> _Row:
    with connection() as conn:
        return _load_or_refuse(conn, allowance_id, source_context=source_context)


def pause_allowance(allowance_id: str, *, source_context: dict[str, Any] | None = None) -> None:
    """Stop an allowance paying. Never needs a credential. Its helpers' slices stop with it."""
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET state = ?, pause_reason = ?, updated_at = ? WHERE allowance_id = ? AND state = ?",
                     (STATE_PAUSED, PAUSE_OWNER, utcnow(), str(allowance_id), STATE_ACTIVE))
    _journal("wallet_agent_allowance_paused", {"allowance_id": allowance_id, "reason": PAUSE_OWNER}, source_context)


def resume_allowance(allowance_id: str, *, credential: str = "", source_context: dict[str, Any] | None = None) -> None:
    """Let a paused allowance pay again. Always the owner's act, whoever paused it: the breaker's pause is undone by
    the owner only."""
    current = _read(allowance_id, source_context=source_context)
    if current.state != STATE_PAUSED:
        raise _refused("allowance_not_paused", source_context=source_context)
    if current.parent_id:
        raise _refused("slice_terms_are_fixed", source_context=source_context)
    custody.prove_owner(current.wallet_id, credential=credential, purpose="resume a spending allowance", source_context=source_context)
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET state = ?, pause_reason = '', strikes_refused = 0, strikes_undelivered = 0, updated_at = ? WHERE allowance_id = ? AND state = ?",
                     (STATE_ACTIVE, utcnow(), str(allowance_id), STATE_PAUSED))
    _journal("wallet_agent_allowance_resumed", {"allowance_id": allowance_id}, source_context)


def revoke_allowance(allowance_id: str, *, source_context: dict[str, Any] | None = None) -> None:
    """End an allowance and every slice cut from it. Never needs a credential. What it already holds stays held."""
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET state = ?, updated_at = ? WHERE (allowance_id = ? OR parent_id = ?) AND state <> ?",
                     (STATE_REVOKED, utcnow(), str(allowance_id), str(allowance_id), STATE_REVOKED))
    _journal("wallet_agent_allowance_revoked", {"allowance_id": allowance_id}, source_context)


def pin_service(allowance_id: str, origin: str, pay_to: str, *, conn: Any = None) -> bool:
    """Pin a listed service's recipient from a payment the OWNER approved. Only an unpinned listed service of a live
    top-level allowance takes a pin; a pin is never replaced here (a new recipient is a change of terms). True when
    this call pinned."""
    if conn is None:
        with connection() as own:
            return pin_service(allowance_id, origin, pay_to, conn=own)
    found = conn.execute("SELECT network FROM wallet_agent_allowances WHERE allowance_id = ?", (str(allowance_id),)).fetchone()
    clean_origin, clean_pay_to = normalize_origin(origin), canonical_address(str(found[0]) if found else "", pay_to)
    if not found or not clean_origin or not clean_pay_to:
        return False
    cursor = conn.execute(
        "UPDATE wallet_agent_allowance_services SET pay_to = ?, pinned_at = ? WHERE allowance_id = ? AND origin = ? AND pay_to = '' "
        "AND allowance_id IN (SELECT allowance_id FROM wallet_agent_allowances WHERE state <> ? AND parent_id = '')",
        (clean_pay_to, utcnow(), str(allowance_id), clean_origin, STATE_REVOKED),
    )
    return cursor.rowcount == 1


# ---------------------------------------------------------------------------
# attenuation
# ---------------------------------------------------------------------------

def delegate_slice(parent_allowance_id: str, spec: AllowanceSpec, *, now: float | None = None, source_context: dict[str, Any] | None = None) -> str:
    """Cut a helper agent a slice of ``parent``'s allowance. Needs no credential because it can only narrow: every
    ceiling at most what the parent has left, no later expiry, no service the parent lacks, same wallet, network and
    asset. One level deep. What the helper spends counts against the parent too."""
    moment = float(now if now is not None else time.time())
    try:
        clean = _validated(spec, now=moment)
    except AllowanceRefused as exc:
        raise _refused("invalid_allowance", source_context=source_context, detail=str(exc)) from None
    try:
        with connection() as conn:
            _begin_immediate(conn)
            parent = _load_or_refuse(conn, parent_allowance_id, source_context=source_context)
            if parent.parent_id or parent.state != STATE_ACTIVE or parent.expires_at <= moment:
                raise _refused("parent_cannot_delegate", source_context=source_context)
            if clean.agent_id == parent.agent_id or (clean.wallet_id, clean.network, clean.asset) != (parent.wallet_id, parent.network, parent.asset):
                raise _refused("slice_outside_parent", source_context=source_context)
            left_total = parent.total_minor - _held(conn, parent.allowance_id)
            left_window = parent.window_minor - _held(conn, parent.allowance_id, since=moment - parent.window_seconds)
            left_count = parent.max_payments - _held_count(conn, parent.allowance_id)
            wider = (clean.per_payment_minor > parent.per_payment_minor or clean.total_minor > left_total or clean.window_minor > left_window
                     or clean.max_payments > left_count or clean.expires_at > parent.expires_at or clean.window_seconds != parent.window_seconds
                     or clean.min_interval_seconds < parent.min_interval_seconds or _ask_ceiling(clean.ask_above_minor) > _ask_ceiling(parent.ask_above_minor))
            for rule in clean.services:
                held = parent.services.get(rule.origin)
                wider = wider or held is None or rule.total_minor > held[0] or bool(rule.pay_to)
            if wider:
                raise _refused("slice_wider_than_parent", source_context=source_context)
            slice_id = _insert(conn, clean, parent_id=parent.allowance_id)
    except sqlite3.IntegrityError:
        raise _refused("allowance_exists", source_context=source_context, agent_id=clean.agent_id) from None
    _journal("wallet_agent_allowance_delegated", {"allowance_id": slice_id, "parent_id": parent_allowance_id, "agent_id": clean.agent_id}, source_context)
    return slice_id


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------

def _held(conn: Any, root_or_slice: str, *, since: float | None = None, origin: str | None = None, own_only: bool = False) -> int:
    """Everything held (reserved + settled) under an allowance: a top-level allowance counts its slices' spends too."""
    column = "allowance_id" if own_only else "root_id"
    sql = f"SELECT COALESCE(SUM(amount_minor), 0) FROM wallet_agent_spends WHERE {column} = ? AND state IN (?, ?)"
    params: list[Any] = [root_or_slice, *_HELD]
    if since is not None:
        sql += " AND spent_at >= ?"
        params.append(float(since))
    if origin is not None:
        sql += " AND origin = ?"
        params.append(origin)
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def _held_count(conn: Any, root_or_slice: str, *, own_only: bool = False) -> int:
    column = "allowance_id" if own_only else "root_id"
    return int(conn.execute(f"SELECT COUNT(*) FROM wallet_agent_spends WHERE {column} = ? AND state IN (?, ?)", (root_or_slice, *_HELD)).fetchone()[0] or 0)


def _log(conn: Any, *, agent_id: str, allowance_id: str, proposal_id: str, origin: str, pay_to: str, amount_minor: int, decision: str, reason: str, now: float) -> None:
    conn.execute(
        "INSERT INTO wallet_agent_decisions (agent_id, allowance_id, proposal_id, origin, pay_to, amount_minor, decision, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (agent_id[:64], allowance_id, str(proposal_id)[:64], origin[:255], pay_to[:128], amount_minor, decision, reason, float(now)),
    )


def _crossed(before: int, after: int, ceiling: int, label: str) -> list[str]:
    return [f"{label}:{mark}" for mark in NOTICE_THRESHOLDS if before * 100 < ceiling * mark <= after * 100]


def authorize(conn: Any, *, agent_id: str, proposal_id: str, wallet_id: str, network: str, asset: str, amount_minor: int, origin: str, pay_to: str,
              request_digest: str, now: float | None = None) -> Decision:
    """Decide whether this agent's payment rides its allowance, and hold it there when it does.

    ``conn`` is the caller's connection inside BEGIN IMMEDIATE: the decision, the hold and the log line commit with
    the caller's own writes (the wallet's hold) or not at all. Every fact here is the caller's typed record of the
    payment -- the agent the SERVER stamped on the run, the proposal, the origin it contacted, the recipient and
    amount the offer named, the digest of the request -- never a model's words.

    ``pay``: held; the caller goes on to the wallet's own ceilings. ``ask_owner``: this payment is the owner's to
    approve as any other. ``refuse``: not now, and asking would not help (the freeze, a payment too soon after the last).
    """
    moment = float(now if now is not None else time.time())
    agent = str(agent_id or "").strip().lower()
    clean_origin = normalize_origin(origin)
    clean_pay_to = canonical_address(network, pay_to)
    digest = str(request_digest or "").strip()
    amount = amount_minor if isinstance(amount_minor, int) and not isinstance(amount_minor, bool) else 0
    log_amount = amount if 0 <= amount <= _INT64_MAX else 0

    def answer(decision: str, reason: str, row: _Row | None = None, **extra: Any) -> Decision:
        paused = ""
        if row is not None and decision != DECISION_PAY and reason in _STRIKE_REASONS:
            strikes = row.strikes_refused + 1
            if strikes >= BREAKER_REFUSALS:
                paused = PAUSE_BREAKER_REFUSALS
                conn.execute("UPDATE wallet_agent_allowances SET state = ?, pause_reason = ?, strikes_refused = ?, updated_at = ? WHERE allowance_id = ? AND state = ?",
                             (STATE_PAUSED, paused, strikes, utcnow(), row.allowance_id, STATE_ACTIVE))
            else:
                conn.execute("UPDATE wallet_agent_allowances SET strikes_refused = ? WHERE allowance_id = ?", (strikes, row.allowance_id))
        _log(conn, agent_id=agent, allowance_id=row.allowance_id if row else "", proposal_id=proposal_id, origin=clean_origin or str(origin or ""), pay_to=clean_pay_to,
             amount_minor=log_amount, decision=decision, reason=reason, now=moment)
        return Decision(decision, reason, allowance_id=row.allowance_id if row else "", paused=paused, **extra)

    if limits._frozen(conn):
        return answer(DECISION_REFUSE, REASON_FROZEN)
    if not _AGENT_ID.match(agent) or not clean_origin or not clean_pay_to or not digest or not str(proposal_id or "").strip() or not 0 < amount <= _INT64_MAX:
        return answer(DECISION_ASK_OWNER, REASON_INVALID_REQUEST)
    try:
        canonical = _canonical_network(network)
        row = _load(conn, "agent_id = ? AND wallet_id = ? AND network = ? AND asset = ? AND state <> ?", (agent, str(wallet_id), canonical, str(asset or "").strip().upper(), STATE_REVOKED))
        root = row if row is None or not row.parent_id else _load(conn, "allowance_id = ?", (row.parent_id,))
    except Exception:
        return answer(DECISION_ASK_OWNER, REASON_UNREADABLE)
    if row is None or root is None:
        return answer(DECISION_ASK_OWNER, REASON_NO_ALLOWANCE)
    if row.state != STATE_ACTIVE or root.state != STATE_ACTIVE:
        return answer(DECISION_ASK_OWNER, REASON_PAUSED if STATE_REVOKED not in (row.state, root.state) else REASON_NO_ALLOWANCE, row)
    if moment >= row.expires_at or moment >= root.expires_at:
        return answer(DECISION_ASK_OWNER, REASON_EXPIRED, row)
    service = row.services.get(clean_origin)
    root_service = root.services.get(clean_origin)
    if service is None or root_service is None:
        return answer(DECISION_ASK_OWNER, REASON_NEW_SERVICE, row)
    pinned = root_service[1]  # a slice pays the recipient its parent's owner pinned, never one of its own
    if not pinned:
        return answer(DECISION_ASK_OWNER, REASON_FIRST_PAYMENT, row)
    if pinned != clean_pay_to:
        return answer(DECISION_ASK_OWNER, REASON_RECIPIENT_CHANGED, row)
    if amount > min(row.per_payment_minor, root.per_payment_minor):
        return answer(DECISION_ASK_OWNER, REASON_OVER_PER_PAYMENT, row)
    if amount > min(_ask_ceiling(row.ask_above_minor), _ask_ceiling(root.ask_above_minor)):
        return answer(DECISION_ASK_OWNER, REASON_OVER_ASK_THRESHOLD, row)
    existing = conn.execute("SELECT state FROM wallet_agent_spends WHERE proposal_id = ?", (str(proposal_id),)).fetchone()
    if existing is not None:
        # this very proposal already rode the allowance: its hold stands, and a released one is not revived here
        return answer(DECISION_PAY, REASON_WITHIN, row) if str(existing[0]) in _HELD else answer(DECISION_ASK_OWNER, REASON_REPEAT_UNCONFIRMED, row)
    same = conn.execute(
        "SELECT state, delivered, amount_minor FROM wallet_agent_spends WHERE root_id = ? AND request_digest = ? AND state IN (?, ?) ORDER BY id DESC LIMIT 1",
        (root.allowance_id, digest, *_HELD),
    ).fetchone()
    if same is not None:
        if str(same[0]) != SPEND_SETTLED or str(same[1]) != DELIVERED_YES:
            return answer(DECISION_ASK_OWNER, REASON_REPEAT_UNCONFIRMED, row)
        if amount > int(same[2]) * PRICE_JUMP_FACTOR:
            return answer(DECISION_ASK_OWNER, REASON_PRICE_JUMP, row)
    gap = max(row.min_interval_seconds, root.min_interval_seconds)
    if gap:
        last = conn.execute("SELECT MAX(spent_at) FROM wallet_agent_spends WHERE allowance_id = ? AND state IN (?, ?)", (row.allowance_id, *_HELD)).fetchone()[0]
        if last is not None and float(last) + gap > moment:
            return answer(DECISION_REFUSE, REASON_TOO_SOON, row, retry_after_seconds=float(last) + gap - moment)
    notices: list[str] = []
    for scope, own_only in ((row, True), (root, False)) if row is not root else ((root, False),):
        count = _held_count(conn, scope.allowance_id, own_only=own_only)
        if count + 1 > scope.max_payments:
            return answer(DECISION_ASK_OWNER, REASON_COUNT_USED, row)
        in_window = _held(conn, scope.allowance_id, since=moment - scope.window_seconds, own_only=own_only)
        if in_window + amount > scope.window_minor:
            return answer(DECISION_ASK_OWNER, REASON_WINDOW_USED, row)
        in_total = _held(conn, scope.allowance_id, own_only=own_only)
        if in_total + amount > scope.total_minor:
            return answer(DECISION_ASK_OWNER, REASON_TOTAL_USED, row)
        to_service = _held(conn, scope.allowance_id, origin=clean_origin, own_only=own_only)
        if to_service + amount > scope.services[clean_origin][0]:
            return answer(DECISION_ASK_OWNER, REASON_SERVICE_TOTAL_USED, row)
        if scope is root:
            notices = _crossed(in_window, in_window + amount, scope.window_minor, "window") + _crossed(in_total, in_total + amount, scope.total_minor, "total")
    conn.execute(
        "INSERT INTO wallet_agent_spends (allowance_id, root_id, agent_id, proposal_id, origin, request_digest, pay_to, amount_minor, state, delivered, spent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row.allowance_id, root.allowance_id, agent, str(proposal_id), clean_origin, digest, clean_pay_to, amount, SPEND_RESERVED, DELIVERED_UNKNOWN, moment),
    )
    if row.strikes_refused:
        conn.execute("UPDATE wallet_agent_allowances SET strikes_refused = 0 WHERE allowance_id = ?", (row.allowance_id,))
    return answer(DECISION_PAY, REASON_WITHIN, row, notices=tuple(notices))


def settle(proposal_id: str, *, amount_moved: bool, conn: Any = None) -> bool:
    """The payment's outcome is known. A payment that moved nothing counts nothing; one that moved stays counted.
    True when a reserved hold changed. An outcome nobody knows is never settled here: it stays held."""
    if conn is None:
        with connection() as own:
            return settle(proposal_id, amount_moved=amount_moved, conn=own)
    cursor = conn.execute("UPDATE wallet_agent_spends SET state = ?, amount_minor = CASE WHEN ? THEN amount_minor ELSE 0 END WHERE proposal_id = ? AND state = ?",
                          (SPEND_SETTLED, 1 if amount_moved else 0, str(proposal_id), SPEND_RESERVED))
    return cursor.rowcount == 1


def release(proposal_id: str, *, conn: Any = None) -> bool:
    """The payment provably never left (refused or failed before any signature went out): the hold is given back."""
    if conn is None:
        with connection() as own:
            return release(proposal_id, conn=own)
    cursor = conn.execute("UPDATE wallet_agent_spends SET state = ? WHERE proposal_id = ? AND state = ?", (SPEND_RELEASED, str(proposal_id), SPEND_RESERVED))
    return cursor.rowcount == 1


def record_delivery(proposal_id: str, *, delivered: bool, conn: Any = None) -> str:
    """Whether the paid request delivered what was paid for. ``BREAKER_UNDELIVERED`` paid requests in a row that
    delivered nothing pause the allowance. Returns the pause reason when this call paused it, else ""."""
    if conn is None:
        with connection() as own:
            _begin_immediate(own)
            return record_delivery(proposal_id, delivered=delivered, conn=own)
    found = conn.execute("SELECT allowance_id, delivered FROM wallet_agent_spends WHERE proposal_id = ? AND state IN (?, ?)", (str(proposal_id), *_HELD)).fetchone()
    if not found or str(found[1]) != DELIVERED_UNKNOWN:
        return ""  # not an allowance payment, or already answered: a delivery is recorded once
    conn.execute("UPDATE wallet_agent_spends SET delivered = ? WHERE proposal_id = ?", (DELIVERED_YES if delivered else DELIVERED_NO, str(proposal_id)))
    if delivered:
        conn.execute("UPDATE wallet_agent_allowances SET strikes_undelivered = 0 WHERE allowance_id = ?", (str(found[0]),))
        return ""
    strikes = int(conn.execute("SELECT strikes_undelivered FROM wallet_agent_allowances WHERE allowance_id = ?", (str(found[0]),)).fetchone()[0] or 0) + 1
    if strikes >= BREAKER_UNDELIVERED:
        conn.execute("UPDATE wallet_agent_allowances SET state = ?, pause_reason = ?, strikes_undelivered = ?, updated_at = ? WHERE allowance_id = ? AND state = ?",
                     (STATE_PAUSED, PAUSE_BREAKER_UNDELIVERED, strikes, utcnow(), str(found[0]), STATE_ACTIVE))
        return PAUSE_BREAKER_UNDELIVERED
    conn.execute("UPDATE wallet_agent_allowances SET strikes_undelivered = ? WHERE allowance_id = ?", (strikes, str(found[0])))
    return ""


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

def allowance_view(allowance_id: str, *, now: float | None = None) -> dict[str, Any] | None:
    """What the owner's card shows: the terms, what is used and what is left. None when there is no such allowance;
    ``{"unreadable": True}`` for a row that cannot be read as written (it pays nothing)."""
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        try:
            row = _load(conn, "allowance_id = ?", (str(allowance_id),))
        except (ValueError, TypeError):
            return {"allowance_id": str(allowance_id), "unreadable": True}
        if row is None:
            return None
        own_only = bool(row.parent_id)
        used_window = _held(conn, row.allowance_id, since=moment - row.window_seconds, own_only=own_only)
        used_total = _held(conn, row.allowance_id, own_only=own_only)
        payments = _held_count(conn, row.allowance_id, own_only=own_only)
        services = [
            {"origin": origin, "total_minor": total, "used_minor": _held(conn, row.allowance_id, origin=origin, own_only=own_only), "pay_to": pay_to, "pinned": bool(pay_to)}
            for origin, (total, pay_to) in sorted(row.services.items())
        ]
    return {
        "allowance_id": row.allowance_id, "parent_id": row.parent_id, "agent_id": row.agent_id, "wallet_id": row.wallet_id, "network": row.network, "asset": row.asset,
        "state": STATE_ACTIVE if row.state == STATE_ACTIVE and moment < row.expires_at else ("expired" if row.state == STATE_ACTIVE else row.state),
        "pause_reason": row.pause_reason, "per_payment_minor": row.per_payment_minor, "window_seconds": row.window_seconds, "window_minor": row.window_minor,
        "window_used_minor": used_window, "total_minor": row.total_minor, "total_used_minor": used_total, "max_payments": row.max_payments, "payments": payments,
        "min_interval_seconds": row.min_interval_seconds, "ask_above_minor": row.ask_above_minor, "expires_at": row.expires_at, "services": services,
    }


def list_allowances(*, agent_id: str = "", include_revoked: bool = False, now: float | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        sql, params = "SELECT allowance_id FROM wallet_agent_allowances WHERE 1 = 1", []
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(str(agent_id).strip().lower())
        if not include_revoked:
            sql += " AND state <> ?"
            params.append(STATE_REVOKED)
        ids = [str(found[0]) for found in conn.execute(sql + " ORDER BY created_at ASC", params).fetchall()]
    return [view for view in (allowance_view(allowance_id, now=now) for allowance_id in ids) if view is not None]


def decisions(*, agent_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """The decision log, newest first: every request an agent made of an allowance, paid or not, and why."""
    with connection() as conn:
        sql, params = "SELECT agent_id, allowance_id, proposal_id, origin, pay_to, amount_minor, decision, reason, created_at FROM wallet_agent_decisions", []
        if agent_id:
            sql += " WHERE agent_id = ?"
            params.append(str(agent_id).strip().lower())
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 1000)))).fetchall()
    names = ("agent_id", "allowance_id", "proposal_id", "origin", "pay_to", "amount_minor", "decision", "reason", "created_at")
    return [dict(zip(names, found, strict=True)) for found in rows]


__all__ = [
    "AllowanceSpec",
    "Decision",
    "ServiceRule",
    "allowance_view",
    "authorize",
    "canonical_address",
    "change_allowance",
    "create_allowance",
    "decisions",
    "delegate_slice",
    "list_allowances",
    "normalize_origin",
    "pause_allowance",
    "pin_service",
    "record_delivery",
    "release",
    "resume_allowance",
    "revoke_allowance",
    "settle",
]
