"""P1 — SESSION EFFECT BUDGETS, ENFORCED AT THE ONE REAL GATE.

The premise: a budget the model can read in a prompt is prose. A budget is a
NUMBER IN A STORE that the one authorization gate (`EffectLedger.open_effect`
in `core.effect_gateway`) consults BEFORE an effect is authorized, and that no
participant in the turn can raise. This module owns that number; the gateway
owns the enforcement point; nothing else in the runtime writes either.

THE LAWS
---------
1. ONE AUTHORITY. `core.effect_budget` is the only owner of budget truth: the
   rule store, the reservation store, the receipts, the adjustment surface.
   Lanes do not keep their own counts — the wallet lane, when it integrates,
   reserves at the same gateway seam, under the classes already declared here.

2. CLASSES ARE A CLOSED CATALOG, matched to the gateway's effect classes:
   `file_write` (writes AND deletes — both are the same irreversible class of
   harm), `command` (shell/process execution), `network_fetch` (outbound
   requests), `public_write` (messages/posts on public surfaces),
   `provider_call` (model provider calls and spend), and the two wallet
   classes (`wallet_proposal`, `wallet_broadcast`) declared now so the lane
   that later integrates has no vocabulary to invent.

3. SCOPES: `turn`, `session`, `project`, `window`. Precedence is INTERSECTION,
   not override: every rule configured for the class applies independently and
   the effect must satisfy ALL of them — the most restrictive budget always
   wins because every budget is a ceiling. Evaluation order is most-specific
   first (turn, session, project, window) so a refusal names the tightest rule
   that refused it. FAIL CLOSED: a configured rule whose usage cannot be read
   (store unavailable, corrupt row) refuses the effect with a typed code; an
   unbudgeted class (no rules at all) passes — a budget is a configured limit,
   not a default deny, and this module says so rather than pretending a fresh
   install budgeted everything at zero.

4. RESERVATION LIFECYCLE — the unit accounting that makes concurrency honest:
   `reserved` (authorized, the unit is HELD and no other effect can take it)
   → `consumed` (a transport attempt actually began; the unit is spent)
   → `released` (the authorization never executed — the unit returns).
   A reservation transitions at most once out of `reserved`. Consuming a
   released reservation is a typed STATE refusal, not a silent spend — an
   effect whose turn already ended and rolled back does not get to run
   unbudgeted after the fact.

5. ATOMIC RESERVATION. One `BEGIN IMMEDIATE` transaction reads every rule,
   checks every limit, and — only if all pass — writes the reservation and
   increments every counter. Two concurrent effects racing for the last unit
   serialize at the store; exactly one wins, the other receives the typed
   `EFFECT_BUDGET_EXCEEDED` refusal naming the rule. No check-then-act gap
   exists anywhere in the module: the check and the write are one
   transaction.

6. OPERATOR-ONLY ADJUSTMENT. The only write to the rule store is
   `apply_operator_adjustment`, which requires an `OperatorBudgetToken`.
   Tokens are granted by `grant_operator_budget_authority`, which REFUSES to
   mint inside an active effect scope (a turn or named background scope):
   models, skills and plugins execute inside those scopes and the operator
   surface does not, so "cannot raise their own budgets" is a mechanical
   check, not a convention. Every grant, adjustment, and REFUSED adjustment
   attempt is a durable, attributed event.

7. DURABLE RECEIPTS. Reservation, consumption, release, reconciliation,
   adjustment, and refusal each write an append-only event row in the same
   transaction as the state change they describe. There is no code path that
   changes budget state without its receipt.

8. RESTART CONTINUITY + RECONCILIATION. All state is in the runtime store.
   After a restart the budgets hold exactly as they ended. Each process
   registers an instance row (`ebi:…`) on first use and refreshes it on every
   reserve; reservations belong to instances. `reconcile_stale_reservations`
   releases reservations whose owning instance is gone (cleanly closed, or
   unseen for longer than the staleness TTL) — the rollback path for an
   effect that was AUTHORIZED (unit held) but never executed because its
   process died. It runs once per process on first budget use, and is public
   for the operator and the tests.

9. BOUNDED STORES. Settled reservations older than the retention window and
   turn-scope counters untouched for as long are pruned in the reserve
   transaction (never live reservations); events older than the event
   retention are pruned with them. The store cannot grow without bound from
   ordinary traffic.

WHAT THIS MODULE DELIBERATELY IS NOT: it does not decide permissions (the
mode matrix at `decide_tool_call` does), does not record effect lifecycles
(the gateway's ledger does — this module's receipts are BUDGET facts), and
does not expose any raise path outside the operator token.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: The closed class catalog (law 2). Wallet classes have no door yet; they are
#: declared so the integrating lane inherits the vocabulary, not invents it.
BUDGET_CLASS_FILE_WRITE = "file_write"
BUDGET_CLASS_COMMAND = "command"
BUDGET_CLASS_NETWORK_FETCH = "network_fetch"
BUDGET_CLASS_PUBLIC_WRITE = "public_write"
BUDGET_CLASS_PROVIDER_CALL = "provider_call"
BUDGET_CLASS_WALLET_PROPOSAL = "wallet_proposal"
BUDGET_CLASS_WALLET_BROADCAST = "wallet_broadcast"
#: AMENDMENT vocabulary — the concurrently-built wallet adapter's two real
#: operations. Both vocabularies stay in the catalog so whichever the wallet
#: lane converges on is already budget-class-legal; the INTEGRATION CONTRACT
#: (`wallet_effect_budget_contract`) is written against these two.
BUDGET_CLASS_WALLET_TRANSACTION = "wallet_transaction"
BUDGET_CLASS_WALLET_SIGN = "wallet_sign"

BUDGET_CLASSES = frozenset(
    {
        BUDGET_CLASS_FILE_WRITE,
        BUDGET_CLASS_COMMAND,
        BUDGET_CLASS_NETWORK_FETCH,
        BUDGET_CLASS_PUBLIC_WRITE,
        BUDGET_CLASS_PROVIDER_CALL,
        BUDGET_CLASS_WALLET_PROPOSAL,
        BUDGET_CLASS_WALLET_BROADCAST,
        BUDGET_CLASS_WALLET_TRANSACTION,
        BUDGET_CLASS_WALLET_SIGN,
    }
)

#: Map the gateway's effect-class strings onto budget classes. Anything not
#: mapped is unbudgeted by name and passes (law 3's unbudgeted leg) — an
#: unknown effect class is not silently counted under a neighbor.
_GATEWAY_CLASS_MAP = {
    "network_fetch": BUDGET_CLASS_NETWORK_FETCH,
    "command": BUDGET_CLASS_COMMAND,
    "file_write": BUDGET_CLASS_FILE_WRITE,
    "public_write": BUDGET_CLASS_PUBLIC_WRITE,
    "provider_call": BUDGET_CLASS_PROVIDER_CALL,
    # the wallet classes are mapped NOW so the moment the wallet lane routes
    # its effects through the gateway's open_effect seam, budgets bind — no
    # vocabulary invention and no adapter duplication on this side
    "wallet_transaction": BUDGET_CLASS_WALLET_TRANSACTION,
    "wallet_sign": BUDGET_CLASS_WALLET_SIGN,
    "wallet_proposal": BUDGET_CLASS_WALLET_PROPOSAL,
    "wallet_broadcast": BUDGET_CLASS_WALLET_BROADCAST,
}

SCOPE_TURN = "turn"
SCOPE_SESSION = "session"
SCOPE_PROJECT = "project"
SCOPE_WINDOW = "window"
#: P3 scope vocabulary, shared with the monetary law (`core.effect_budget_money`):
#: one request, one task, one agent, one provider identity, and everything.
#: An identity a caller does not pass binds nothing, exactly like the
#: original three — existing callers pass nothing new and behave as before.
SCOPE_REQUEST = "request"
SCOPE_TASK = "task"
SCOPE_AGENT = "agent"
SCOPE_PROVIDER = "provider"
SCOPE_GLOBAL = "global"

#: Evaluation order = most specific first (law 3). Intersection semantics:
#: every configured rule applies; this order only decides which rule a
#: multi-rule refusal names first.
SCOPE_PRECEDENCE = (
    SCOPE_REQUEST,
    SCOPE_TURN,
    SCOPE_TASK,
    SCOPE_AGENT,
    SCOPE_SESSION,
    SCOPE_PROJECT,
    SCOPE_PROVIDER,
    SCOPE_GLOBAL,
    SCOPE_WINDOW,
)
BUDGET_SCOPES = frozenset(SCOPE_PRECEDENCE)
#: The identity field each identity-bound scope reads (window and global have
#: no identity: one bucket per rule).
_SCOPE_IDENTITY_FIELDS = {
    SCOPE_REQUEST: "request_id",
    SCOPE_TURN: "turn_id",
    SCOPE_TASK: "task_id",
    SCOPE_AGENT: "agent_id",
    SCOPE_SESSION: "session_id",
    SCOPE_PROJECT: "project_key",
    SCOPE_PROVIDER: "provider_id",
}

#: Typed refusal/fault codes — the contract every caller and receipt carries.
REFUSAL_BUDGET_EXCEEDED = "EFFECT_BUDGET_EXCEEDED"
REFUSAL_STORE_UNAVAILABLE = "EFFECT_BUDGET_STORE_UNAVAILABLE"
REFUSAL_AUTHORITY = "EFFECT_BUDGET_AUTHORITY_REFUSED"
REFUSAL_STATE = "EFFECT_BUDGET_STATE_ERROR"
REFUSAL_UNKNOWN_CLASS = "EFFECT_BUDGET_UNKNOWN_CLASS"

RESERVATION_RESERVED = "reserved"
RESERVATION_CONSUMED = "consumed"
RESERVATION_RELEASED = "released"

#: An instance unseen for this long is dead even if it never closed cleanly
#: (crash, kill -9). Reservations it still holds are reconciled away.
INSTANCE_STALE_SECONDS = 6 * 3600.0
# the proof a reconciliation cites when it acts on an instance's death
INSTANCE_DEATH_CLOSED = "instance_closed"
INSTANCE_DEATH_STALE = "heartbeat_stale"
INSTANCE_DEATH_PROCESS_GONE = "process_gone"
#: Settled reservations and untouched turn counters older than this are
#: pruned (law 9). Live reservations are NEVER pruned.
RESERVATION_RETENTION_SECONDS = 7 * 24 * 3600.0
EVENT_RETENTION_SECONDS = 30 * 24 * 3600.0

_RESERVATIONS_TABLE = "effect_budget_reservations"
_COUNTERS_TABLE = "effect_budget_counters"
_EVENTS_TABLE = "effect_budget_events"
_RULES_TABLE = "effect_budget_rules"
_INSTANCES_TABLE = "effect_budget_instances"


class EffectBudgetRefusedError(RuntimeError):
    """The typed refusal every budget gate raises.

    `code` is one of the REFUSAL_* constants; `rule` names the refusing rule
    (class/scope) when the refusal is a limit; `detail` is the safe,
    non-secret explanation. Never carries exception text from the store.
    """

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        rule: str = "",
    ) -> None:
        self.code = str(code or REFUSAL_STATE)
        self.detail = str(detail or "")
        self.rule = str(rule or "")
        super().__init__(f"{self.code}: {self.detail}" + (f" [{self.rule}]" if self.rule else ""))


@dataclass(frozen=True)
class BudgetRule:
    """One configured ceiling: `limit` units of `budget_class` per `scope`.

    `window_seconds` is meaningful only for SCOPE_WINDOW — the rolling window
    the limit is measured over. Turn/session/project windows are the life of
    that identity and carry no window field.
    """

    budget_class: str
    scope: str
    limit: int
    window_seconds: float = 0.0

    @property
    def rule_id(self) -> str:
        window = f":w{int(self.window_seconds)}" if self.scope == SCOPE_WINDOW else ""
        return f"{self.budget_class}/{self.scope}{window}"


@dataclass(frozen=True)
class OperatorBudgetToken:
    """The only credential that can change a budget.

    Minted exclusively by `grant_operator_budget_authority`, which refuses
    inside any active effect scope (law 6). Carries its grant attribution on
    its face so every adjustment names who authorized it.
    """

    token_id: str
    granted_by: str = "operator"
    note: str = ""
    granted_at: str = ""


@dataclass(frozen=True)
class ReservationReceipt:
    """The durable receipt for one reservation (law 7's shape)."""

    reservation_id: str
    budget_class: str
    units: int
    rules: tuple[BudgetRule, ...]
    unbudgeted: bool = False
    effect_id: str = ""
    owner_ref: str = ""


@dataclass(frozen=True)
class RuleStatus:
    """Remaining-budget status for one rule under one identity."""

    rule: BudgetRule
    limit: int
    used: int  #: consumed + reserved-active (the units already spoken for)
    remaining: int
    applies: bool = True  #: False when this scope's identity is absent


@dataclass(frozen=True)
class PreviewResult:
    """The read-only answer to "would this effect be authorized?".

    Advisory by construction: it reserves nothing, and a concurrent effect
    may take the remaining units between preview and reserve — only
    `reserve_effect_units` is authoritative.
    """

    allowed: bool
    code: str = ""
    rule: str = ""
    detail: str = ""
    per_rule: tuple[RuleStatus, ...] = field(default_factory=tuple)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _utcnow_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# the store seam
# ---------------------------------------------------------------------------

_STORE_LOCK = threading.RLock()
#: In-process observability for store failures that could not be journaled
#: (the journal itself is in the failed store) — the journal's own pattern.
_STORE_FAILURES = {"count": 0, "last_error": ""}


def budget_store_status() -> dict[str, Any]:
    """What the store could not journal, counted — never a silent failure."""
    with _STORE_LOCK:
        return dict(_STORE_FAILURES)


def _budget_connection() -> sqlite3.Connection:
    """The one connection seam — tests inject an unavailable store from here
    (the same law as the background journal's `_journal_connection`)."""
    from storage.db import get_connection

    return get_connection()


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RULES_TABLE} (
            budget_class TEXT NOT NULL,
            scope TEXT NOT NULL,
            window_seconds REAL NOT NULL DEFAULT 0,
            limit_units INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            adjusted_at TEXT NOT NULL,
            adjusted_by TEXT NOT NULL,
            PRIMARY KEY (budget_class, scope, window_seconds)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_COUNTERS_TABLE} (
            budget_class TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            reserved_units INTEGER NOT NULL DEFAULT 0,
            consumed_units INTEGER NOT NULL DEFAULT 0,
            updated_epoch REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (budget_class, scope, scope_key)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RESERVATIONS_TABLE} (
            reservation_id TEXT PRIMARY KEY,
            budget_class TEXT NOT NULL,
            units INTEGER NOT NULL,
            state TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            owner_ref TEXT NOT NULL DEFAULT '',
            effect_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL DEFAULT '',
            created_epoch REAL NOT NULL,
            settled_epoch REAL,
            last_active_epoch REAL NOT NULL,
            settle_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_EVENTS_TABLE} (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_kind TEXT NOT NULL,
            code TEXT NOT NULL DEFAULT '',
            budget_class TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT '',
            scope_key TEXT NOT NULL DEFAULT '',
            units INTEGER NOT NULL DEFAULT 0,
            reservation_id TEXT NOT NULL DEFAULT '',
            detail_json TEXT NOT NULL DEFAULT '{{}}',
            instance_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_INSTANCES_TABLE} (
            instance_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            last_seen_epoch REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'live'
        )
        """
    )
    _ensure_added_columns(conn)


#: Columns added after these tables first shipped (P3). Additive and
#: idempotent: an older store gains them on first touch, with defaults that
#: mean "no identity" / "liveness unknown" — exactly the old behavior.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    (_RESERVATIONS_TABLE, "request_id", "TEXT NOT NULL DEFAULT ''"),
    (_RESERVATIONS_TABLE, "task_id", "TEXT NOT NULL DEFAULT ''"),
    (_RESERVATIONS_TABLE, "agent_id", "TEXT NOT NULL DEFAULT ''"),
    (_RESERVATIONS_TABLE, "provider_id", "TEXT NOT NULL DEFAULT ''"),
    (_INSTANCES_TABLE, "pid", "INTEGER NOT NULL DEFAULT 0"),
    (_INSTANCES_TABLE, "hostname", "TEXT NOT NULL DEFAULT ''"),
)


def _ensure_added_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        present = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column in present:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            # a concurrent process added it between the read and the ALTER
            if "duplicate column" not in str(exc).lower():
                raise


# ---------------------------------------------------------------------------
# the process instance (law 8)
# ---------------------------------------------------------------------------

_INSTANCE: dict[str, str] = {}
_RECONCILED_THIS_PROCESS: dict[str, bool] = {}
_HOSTNAME: dict[str, str] = {}


def _hostname() -> str:
    if "name" not in _HOSTNAME:
        try:
            _HOSTNAME["name"] = str(socket.gethostname() or "")[:255]
        except Exception:
            _HOSTNAME["name"] = ""
    return _HOSTNAME["name"]


def _current_instance(conn: sqlite3.Connection) -> str:
    """This process's budget instance, registered (or refreshed) in the store.

    P3: the instance records its pid and host, and a forked child mints its
    own instance instead of reserving under its parent's — a child that dies
    must never make its living parent's reservations look dead, and the
    reverse."""
    pid = os.getpid()
    instance_id = _INSTANCE.get("id")
    if not instance_id or _INSTANCE.get("pid") != str(pid):
        instance_id = "ebi:" + secrets.token_hex(4)
        _INSTANCE["id"] = instance_id
        _INSTANCE["pid"] = str(pid)
    conn.execute(
        f"INSERT INTO {_INSTANCES_TABLE} (instance_id, started_at, last_seen_epoch, state, pid, hostname) "
        "VALUES (?, ?, ?, 'live', ?, ?) "
        f"ON CONFLICT(instance_id) DO UPDATE SET last_seen_epoch=excluded.last_seen_epoch, state='live', "
        "pid=excluded.pid, hostname=excluded.hostname",
        (instance_id, _utcnow_iso(), _utcnow_epoch(), pid, _hostname()),
    )
    return instance_id


def current_budget_instance_id() -> str:
    """The instance id this process reserves under (without touching the
    store — a read for evidence and tests)."""
    return _INSTANCE.get("id") or ""


def shutdown_effect_budget_instance() -> None:
    """Clean shutdown: mark this instance closed so a later restart's
    reconciliation releases its outstanding reservations immediately."""
    instance_id = _INSTANCE.get("id")
    if not instance_id:
        return
    try:
        conn = _budget_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _ensure_tables(conn)
            conn.execute(
                f"UPDATE {_INSTANCES_TABLE} SET state='closed' WHERE instance_id=?",
                (instance_id,),
            )
            conn.execute(
                f"INSERT INTO {_EVENTS_TABLE} (event_kind, instance_id, created_at) "
                "VALUES ('instance_closed', ?, ?)",
                (instance_id, _utcnow_iso()),
            )
            conn.commit()
        finally:
            pass
    except Exception as exc:  # counted, never raised into a shutdown path
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"shutdown: {type(exc).__name__}: {exc}"


def _instance_death_reason(row: sqlite3.Row, now_epoch: float) -> str:
    """The proof that an instance is no longer live, or "" while it is: a
    clean shutdown, no heartbeat within the staleness TTL, or a same-host pid
    the kernel reports absent."""
    if str(row["state"]) == "closed":
        return INSTANCE_DEATH_CLOSED
    if (now_epoch - float(row["last_seen_epoch"])) >= INSTANCE_STALE_SECONDS:
        return INSTANCE_DEATH_STALE
    if _instance_process_gone(row):
        return INSTANCE_DEATH_PROCESS_GONE
    return ""


def _instance_is_live(row: sqlite3.Row, now_epoch: float) -> bool:
    return not _instance_death_reason(row, now_epoch)


def _instance_process_gone(row: sqlite3.Row) -> bool:
    """P3: a killed process is dead NOW, not after the staleness TTL — when
    that can be proven. Proof is narrow on purpose: POSIX only (on Windows
    `os.kill(pid, 0)` would terminate the process), same host only, and only
    a pid the kernel reports as absent. A reused pid makes a dead instance
    look alive, never the reverse, so every doubt keeps the instance live."""
    if os.name != "posix":
        return False
    try:
        keys = set(row.keys())
    except Exception:
        return False
    if "pid" not in keys or "hostname" not in keys:
        return False
    try:
        pid = int(row["pid"] or 0)
    except (TypeError, ValueError):
        return False
    host = str(row["hostname"] or "")
    if pid <= 0 or pid == os.getpid() or not host or host != _hostname():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def reconcile_stale_reservations(*, force: bool = False) -> list[dict[str, Any]]:
    """The rollback for authorized-but-never-executed effects after a death.

    Releases every reservation still `reserved` whose owning instance is not
    live (closed, absent, or unseen past the staleness TTL). Runs once per
    process on first budget use; `force=True` reruns it (operator/tests).
    Every release is its own durable `reconciled_release` event.
    """
    if not force and _RECONCILED_THIS_PROCESS.get("done"):
        return []
    _RECONCILED_THIS_PROCESS["done"] = True
    released: list[dict[str, Any]] = []
    now_epoch = _utcnow_epoch()
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(conn)
        instance_rows = {
            str(row["instance_id"]): row
            for row in conn.execute(f"SELECT * FROM {_INSTANCES_TABLE}").fetchall()
        }
        stale = []
        for row in conn.execute(
            f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE state=?",
            (RESERVATION_RESERVED,),
        ).fetchall():
            instance_row = instance_rows.get(str(row["instance_id"]))
            # an absent instance row is stale by definition (pruned, or
            # written by a store that lost its instance table)
            if instance_row is None or not _instance_is_live(instance_row, now_epoch):
                stale.append(row)
        for row in stale:
            # an absent instance row is stale by definition (already pruned
            # or written by a store that lost the instance table)
            conn.execute(
                f"UPDATE {_RESERVATIONS_TABLE} SET state=?, settled_epoch=?, "
                "settle_reason=?, last_active_epoch=? WHERE reservation_id=? "
                "AND state=?",
                (
                    RESERVATION_RELEASED,
                    now_epoch,
                    "reconciled: owning instance is no longer live",
                    now_epoch,
                    row["reservation_id"],
                    RESERVATION_RESERVED,
                ),
            )
            _counter_adjust(conn, row, sign=-1, field_name="reserved_units")
            conn.execute(
                f"INSERT INTO {_EVENTS_TABLE} (event_kind, budget_class, units, "
                "reservation_id, detail_json, instance_id, created_at) "
                "VALUES ('reconciled_release', ?, ?, ?, ?, ?, ?)",
                (
                    str(row["budget_class"]),
                    int(row["units"]),
                    str(row["reservation_id"]),
                    json.dumps({"reason": "dead-instance reconciliation"}),
                    str(row["instance_id"]),
                    _utcnow_iso(),
                ),
            )
            released.append(
                {
                    "reservation_id": str(row["reservation_id"]),
                    "budget_class": str(row["budget_class"]),
                    "units": int(row["units"]),
                    "instance_id": str(row["instance_id"]),
                }
            )
        conn.commit()
    except Exception:
        conn.rollback()
        _RECONCILED_THIS_PROCESS["done"] = False
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    # P3: the monetary half runs with the same liveness law. Its outcomes are
    # its own receipts (released only when never claimed; UNKNOWN when a dead
    # claimant may have paid) and never widen this unit account.
    try:
        from core.effect_budget_money import reconcile_money_liabilities

        reconcile_money_liabilities(force=force)
    except Exception as exc:
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"money reconcile: {type(exc).__name__}: {exc}"
    return released


# ---------------------------------------------------------------------------
# rules: the operator-only adjustment surface (law 6)
# ---------------------------------------------------------------------------


def grant_operator_budget_authority(note: str = "") -> OperatorBudgetToken:
    """Mint the one credential that can change a budget.

    REFUSED inside any active effect scope (a turn ledger, or a named
    background scope): models, skills and plugins execute inside those scopes,
    the operator surface does not, and a participant minting the credential
    that raises its own ceiling is the exact hazard law 6 exists for. The
    refusal — like the grant — is a durable event.
    """
    from core.effect_gateway import current_effect_ledger

    if current_effect_ledger() is not None:
        _journal_authority_refusal(
            "token grant",
            "an operator budget token cannot be minted inside an active "
            "effect scope (models/skills/plugins run there; the operator "
            "surface does not)",
        )
    token = OperatorBudgetToken(
        token_id="obt:" + secrets.token_hex(8),
        note=str(note or ""),
        granted_at=_utcnow_iso(),
    )
    try:
        conn = _budget_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _ensure_tables(conn)
            conn.execute(
                f"INSERT INTO {_EVENTS_TABLE} (event_kind, detail_json, created_at) "
                "VALUES ('authority_grant', ?, ?)",
                (
                    json.dumps({"token_id": token.token_id, "note": token.note}),
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except Exception as exc:
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"authority grant: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            "the budget store could not journal the operator grant; "
            "no credential is issued against an unjournaled grant",
        ) from exc
    return token


def _journal_authority_refusal(action: str, why: str) -> None:
    """A refused adjustment/grant attempt is itself a durable fact (law 6)."""
    try:
        conn = _budget_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _ensure_tables(conn)
            conn.execute(
                f"INSERT INTO {_EVENTS_TABLE} (event_kind, code, detail_json, created_at) "
                "VALUES ('authority_refused', ?, ?, ?)",
                (
                    REFUSAL_AUTHORITY,
                    json.dumps({"action": action, "why": why}),
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except Exception as exc:
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"authority refusal journal: {type(exc).__name__}: {exc}"
    raise EffectBudgetRefusedError(REFUSAL_AUTHORITY, why)


@dataclass(frozen=True)
class BudgetAdjustment:
    """One operator decision: set (or remove) one rule's limit.

    `new_limit=None` REMOVES the rule (unbudgets that scope); a limit of 0
    forbids the class under that scope entirely. Both are operator decisions
    of the same shape.
    """

    budget_class: str
    scope: str
    new_limit: int | None
    window_seconds: float = 0.0
    note: str = ""


def _token_was_granted(token: OperatorBudgetToken) -> bool:
    """A token is real only if its grant is a durable fact in the store — the
    dataclass alone proves nothing (it is constructible by anyone), so a
    forged or synthetic token id has no adjustment power."""
    try:
        conn = _budget_connection()
        _ensure_tables(conn)
        for row in conn.execute(
            f"SELECT detail_json FROM {_EVENTS_TABLE} WHERE event_kind='authority_grant'"
        ).fetchall():
            try:
                detail = json.loads(str(row["detail_json"] or "{}"))
            except Exception:
                continue
            if str(detail.get("token_id") or "") == token.token_id:
                return True
        return False
    except Exception as exc:
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"token verify: {type(exc).__name__}: {exc}"
        return False


def apply_operator_adjustment(
    token: OperatorBudgetToken, adjustments: list[BudgetAdjustment]
) -> list[BudgetRule]:
    """The ONLY write to the rule store. Token required — and the token must
    carry a DURABLE GRANT (a forged dataclass proves nothing); every
    adjustment is a durable, attributed event carrying the token id."""
    if not isinstance(token, OperatorBudgetToken) or not token.token_id:
        _journal_authority_refusal(
            "adjustment",
            "a budget adjustment requires an OperatorBudgetToken; "
            "no participant of a turn holds one",
        )
    if not _token_was_granted(token):
        _journal_authority_refusal(
            "adjustment",
            f"token {token.token_id!r} has no durable grant record — "
            "a forged or synthetic token cannot adjust budgets",
        )
    from core.effect_gateway import current_effect_ledger

    if current_effect_ledger() is not None:
        _journal_authority_refusal(
            "adjustment",
            "budgets cannot be adjusted inside an active effect scope",
        )
    for adjustment in adjustments:
        if adjustment.budget_class not in BUDGET_CLASSES:
            raise EffectBudgetRefusedError(
                REFUSAL_UNKNOWN_CLASS,
                f"{adjustment.budget_class!r} is not in the budget class catalog",
            )
        if adjustment.scope not in BUDGET_SCOPES:
            raise EffectBudgetRefusedError(
                REFUSAL_STATE, f"{adjustment.scope!r} is not a budget scope"
            )
        if adjustment.scope == SCOPE_WINDOW and float(adjustment.window_seconds) <= 0:
            raise EffectBudgetRefusedError(
                REFUSAL_STATE, "a window rule requires window_seconds > 0"
            )
        if adjustment.new_limit is not None and int(adjustment.new_limit) < 0:
            raise EffectBudgetRefusedError(
                REFUSAL_STATE, "a limit cannot be negative (use 0 to forbid, None to unbudget)"
            )
    applied: list[BudgetRule] = []
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(conn)
        for adjustment in adjustments:
            window = (
                float(adjustment.window_seconds) if adjustment.scope == SCOPE_WINDOW else 0.0
            )
            if adjustment.new_limit is None:
                conn.execute(
                    f"DELETE FROM {_RULES_TABLE} WHERE budget_class=? AND scope=? "
                    "AND window_seconds=?",
                    (adjustment.budget_class, adjustment.scope, window),
                )
            else:
                conn.execute(
                    f"INSERT INTO {_RULES_TABLE} (budget_class, scope, window_seconds, "
                    "limit_units, note, adjusted_at, adjusted_by) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(budget_class, scope, window_seconds) DO UPDATE SET "
                    "limit_units=excluded.limit_units, note=excluded.note, "
                    "adjusted_at=excluded.adjusted_at, adjusted_by=excluded.adjusted_by",
                    (
                        adjustment.budget_class,
                        adjustment.scope,
                        window,
                        int(adjustment.new_limit),
                        str(adjustment.note or ""),
                        _utcnow_iso(),
                        token.token_id,
                    ),
                )
                applied.append(
                    BudgetRule(
                        budget_class=adjustment.budget_class,
                        scope=adjustment.scope,
                        limit=int(adjustment.new_limit),
                        window_seconds=window,
                    )
                )
            conn.execute(
                f"INSERT INTO {_EVENTS_TABLE} (event_kind, budget_class, scope, units, "
                "detail_json, created_at) VALUES ('adjustment', ?, ?, ?, ?, ?)",
                (
                    adjustment.budget_class,
                    adjustment.scope,
                    int(adjustment.new_limit) if adjustment.new_limit is not None else -1,
                    json.dumps(
                        {
                            "token_id": token.token_id,
                            "note": str(adjustment.note or ""),
                            "action": "removed" if adjustment.new_limit is None else "set",
                            "window_seconds": window,
                        }
                    ),
                    _utcnow_iso(),
                ),
            )
        conn.commit()
    except EffectBudgetRefusedError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"adjustment: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store refused the adjustment: {type(exc).__name__}",
        ) from exc
    return applied


def active_budgets(budget_class: str = "") -> list[BudgetRule]:
    """The configured rules (all classes, or one), read-only."""
    conn = _budget_connection()
    _ensure_tables(conn)
    if budget_class:
        rows = conn.execute(
            f"SELECT * FROM {_RULES_TABLE} WHERE budget_class=?", (budget_class,)
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM {_RULES_TABLE}").fetchall()
    rules = [
        BudgetRule(
            budget_class=str(row["budget_class"]),
            scope=str(row["scope"]),
            limit=int(row["limit_units"]),
            window_seconds=float(row["window_seconds"] or 0.0),
        )
        for row in rows
    ]
    rules.sort(key=lambda r: (r.budget_class, SCOPE_PRECEDENCE.index(r.scope)))
    return rules


# ---------------------------------------------------------------------------
# usage, reserve, consume, release — the atomic core (laws 4, 5, 7)
# ---------------------------------------------------------------------------


def _scope_key(rule: BudgetRule, identity: dict[str, str]) -> str | None:
    """The identity a rule binds to, or None when this scope's identity is
    absent — a rule that cannot bind does not apply, and says so."""
    field_name = _SCOPE_IDENTITY_FIELDS.get(rule.scope)
    if field_name is not None:
        return identity.get(field_name) or None
    if rule.scope == SCOPE_GLOBAL:
        return "global"
    # one rolling window per (class, window size) — the window dimension is
    # the identity; turn/session/project do not subdivide it
    return f"w{int(rule.window_seconds)}"


def _rule_usage(
    conn: sqlite3.Connection, rule: BudgetRule, scope_key: str, now_epoch: float
) -> int:
    """Units already spoken for under one rule: consumed + reserved-active."""
    if rule.scope == SCOPE_WINDOW:
        cutoff = now_epoch - float(rule.window_seconds)
        row = conn.execute(
            f"SELECT COALESCE(SUM(units), 0) AS used FROM {_RESERVATIONS_TABLE} "
            "WHERE budget_class=? AND state IN (?, ?) AND last_active_epoch >= ? "
            "AND EXISTS (SELECT 1 FROM json_each("
            f"{_RESERVATIONS_TABLE}.rules_json) je "
            "WHERE je.value->>'scope'='window' "
            "AND CAST(je.value->>'window_seconds' AS REAL)=?)",
            (
                rule.budget_class,
                RESERVATION_RESERVED,
                RESERVATION_CONSUMED,
                cutoff,
                float(rule.window_seconds),
            ),
        ).fetchone()
        return int(row["used"] or 0)
    row = conn.execute(
        f"SELECT reserved_units, consumed_units FROM {_COUNTERS_TABLE} "
        "WHERE budget_class=? AND scope=? AND scope_key=?",
        (rule.budget_class, rule.scope, scope_key),
    ).fetchone()
    if row is None:
        return 0
    return int(row["reserved_units"] or 0) + int(row["consumed_units"] or 0)


def _counter_adjust(
    conn: sqlite3.Connection, reservation_row: sqlite3.Row | dict[str, Any], *, sign: int, field_name: str
) -> None:
    """Adjust every non-window counter a reservation touched, in the caller's
    transaction (BEGIN IMMEDIATE already serializes writers, so a plain
    read-modify-write here cannot race). Window rules carry no counter —
    their usage IS the row scan."""
    units = int(reservation_row["units"]) * (1 if sign > 0 else -1)
    for rule in _rules_of(reservation_row):
        if rule.scope == SCOPE_WINDOW:
            continue
        scope_key = _scope_key_for_reservation(reservation_row, rule)
        if scope_key is None:
            continue
        conn.execute(
            f"INSERT INTO {_COUNTERS_TABLE} (budget_class, scope, scope_key, "
            "reserved_units, consumed_units, updated_epoch) VALUES (?, ?, ?, 0, 0, ?) "
            "ON CONFLICT(budget_class, scope, scope_key) DO NOTHING",
            (rule.budget_class, rule.scope, scope_key, _utcnow_epoch()),
        )
        conn.execute(
            f"UPDATE {_COUNTERS_TABLE} SET {field_name}={field_name}+?, "
            "updated_epoch=? WHERE budget_class=? AND scope=? AND scope_key=?",
            (units, _utcnow_epoch(), rule.budget_class, rule.scope, scope_key),
        )


def _scope_key_for_reservation(
    reservation_row: sqlite3.Row | dict[str, Any], rule: BudgetRule
) -> str | None:
    row = reservation_row
    field_name = _SCOPE_IDENTITY_FIELDS.get(rule.scope)
    if field_name is not None:
        try:
            return str(row[field_name] or "") or None
        except (IndexError, KeyError):
            return None
    if rule.scope == SCOPE_GLOBAL:
        return "global"
    return f"w{int(rule.window_seconds)}"


def _rules_of(reservation_row: sqlite3.Row | dict[str, Any]) -> tuple[BudgetRule, ...]:
    try:
        raw = json.loads(str(reservation_row["rules_json"] or "[]"))
    except Exception:
        return ()
    rules = []
    for item in raw:
        try:
            rules.append(
                BudgetRule(
                    budget_class=str(item["budget_class"]),
                    scope=str(item["scope"]),
                    limit=int(item["limit"]),
                    window_seconds=float(item.get("window_seconds") or 0.0),
                )
            )
        except Exception:
            continue
    return tuple(rules)


def _prune_stores(conn: sqlite3.Connection, now_epoch: float) -> None:
    """Law 9: bounded stores. Never prunes a live reservation."""
    settled_cutoff = now_epoch - RESERVATION_RETENTION_SECONDS
    conn.execute(
        f"DELETE FROM {_RESERVATIONS_TABLE} WHERE state != ? AND created_epoch < ?",
        (RESERVATION_RESERVED, settled_cutoff),
    )
    conn.execute(
        f"DELETE FROM {_COUNTERS_TABLE} WHERE scope IN (?, ?) AND updated_epoch < ? "
        "AND (reserved_units + consumed_units) = 0",
        (SCOPE_TURN, SCOPE_REQUEST, settled_cutoff),
    )
    # money receipts (`money_*`) are the audit trail of payments and follow
    # the monetary law's own, longer retention — never this 30-day prune
    conn.execute(
        f"DELETE FROM {_EVENTS_TABLE} WHERE created_at < ? AND event_kind NOT LIKE 'money\\_%' ESCAPE '\\'",
        (_iso_from_epoch(now_epoch - EVENT_RETENTION_SECONDS),),
    )


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


def _identity_of(
    *,
    turn_id: str = "",
    session_id: str = "",
    project_key: str = "",
    request_id: str = "",
    task_id: str = "",
    agent_id: str = "",
    provider_id: str = "",
) -> dict[str, str]:
    return {
        "turn_id": str(turn_id or ""),
        "session_id": str(session_id or ""),
        "project_key": str(project_key or ""),
        "request_id": str(request_id or ""),
        "task_id": str(task_id or ""),
        "agent_id": str(agent_id or ""),
        "provider_id": str(provider_id or ""),
    }


def _extended_identity(identity: dict[str, str]) -> dict[str, str]:
    """The P3 identity fields a receipt names — only the ones present, so a
    caller that passes none writes exactly the receipt it always wrote."""
    return {
        name: identity[name]
        for name in ("request_id", "task_id", "agent_id", "provider_id")
        if identity.get(name)
    }


def reserve_effect_units(
    budget_class: str,
    *,
    effect_id: str = "",
    owner_ref: str = "",
    turn_id: str = "",
    session_id: str = "",
    project_key: str = "",
    units: int = 1,
    request_id: str = "",
    task_id: str = "",
    agent_id: str = "",
    provider_id: str = "",
) -> ReservationReceipt:
    """Reserve `units` under EVERY configured rule for the class — atomically.

    The ONE enforcement primitive: all rule reads, all limit checks, the
    reservation row, every counter update, and the durable receipt are a
    single BEGIN IMMEDIATE transaction. Any rule over limit → nothing is
    written but the refusal receipt, and the typed refusal names the first
    (most specific) refusing rule. Unbudgeted class → pass, no rows, no
    events — an honest nothing, not a fabricated unlimited receipt.

    Store failure with rules unreadable → REFUSAL_STORE_UNAVAILABLE (fail
    closed: the gate cannot know the class was unbudgeted).
    """
    if budget_class not in BUDGET_CLASSES:
        raise EffectBudgetRefusedError(
            REFUSAL_UNKNOWN_CLASS, f"{budget_class!r} is not in the budget class catalog"
        )
    units = int(units)
    if units < 1:
        raise EffectBudgetRefusedError(REFUSAL_STATE, "units must be >= 1")
    identity = _identity_of(
        turn_id=turn_id,
        session_id=session_id,
        project_key=project_key,
        request_id=request_id,
        task_id=task_id,
        agent_id=agent_id,
        provider_id=provider_id,
    )
    try:
        reconcile_stale_reservations()
    except EffectBudgetRefusedError:
        raise
    except Exception:
        # the reconcile is housekeeping, never a bypass: its failure is
        # counted inside, and the reserve below fails closed on its own
        # store consult — a raw store error never leaks through this gate
        pass
    now_epoch = _utcnow_epoch()
    reservation_id = "ebr:" + secrets.token_hex(8)
    try:
        conn = _budget_connection()
        conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(conn)
        instance_id = _current_instance(conn)
        rules = [
            BudgetRule(
                budget_class=str(row["budget_class"]),
                scope=str(row["scope"]),
                limit=int(row["limit_units"]),
                window_seconds=float(row["window_seconds"] or 0.0),
            )
            for row in conn.execute(
                f"SELECT * FROM {_RULES_TABLE} WHERE budget_class=?", (budget_class,)
            ).fetchall()
        ]
        rules.sort(key=lambda r: SCOPE_PRECEDENCE.index(r.scope))
        if rules:
            failing: BudgetRule | None = None
            usage_by_rule: dict[str, int] = {}
            for rule in rules:
                scope_key = _scope_key(rule, identity)
                if scope_key is None:
                    continue  # this scope's identity is absent: the rule does not bind
                used = _rule_usage(conn, rule, scope_key, now_epoch)
                usage_by_rule[rule.rule_id] = used
                if used + units > rule.limit:
                    failing = failing or rule
            if failing is not None:
                conn.execute(
                    f"INSERT INTO {_EVENTS_TABLE} (event_kind, code, budget_class, "
                    "scope, units, detail_json, instance_id, created_at) "
                    "VALUES ('refused', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        REFUSAL_BUDGET_EXCEEDED,
                        budget_class,
                        failing.scope,
                        units,
                        json.dumps(
                            {
                                "rule": failing.rule_id,
                                "limit": failing.limit,
                                "used": usage_by_rule.get(failing.rule_id, 0),
                                "turn_id": identity["turn_id"],
                                "session_id": identity["session_id"],
                                "project_key": identity["project_key"],
                                **_extended_identity(identity),
                            }
                        ),
                        instance_id,
                        _utcnow_iso(),
                    ),
                )
                conn.commit()
                raise EffectBudgetRefusedError(
                    REFUSAL_BUDGET_EXCEEDED,
                    f"{usage_by_rule.get(failing.rule_id, 0)} of {failing.limit} units "
                    f"already spoken for under {failing.rule_id}",
                    rule=failing.rule_id,
                )
        if not rules:
            # unbudgeted class: pass with NO rows, NO events — an honest
            # nothing, not a fabricated unlimited receipt
            conn.rollback()
            return ReservationReceipt(
                reservation_id="",
                budget_class=budget_class,
                units=units,
                rules=(),
                unbudgeted=True,
                effect_id=str(effect_id or ""),
                owner_ref=str(owner_ref or ""),
            )
        reservation_row = {
            "reservation_id": reservation_id,
            "budget_class": budget_class,
            "units": units,
            "state": RESERVATION_RESERVED,
            "rules_json": json.dumps(
                [
                    {
                        "budget_class": r.budget_class,
                        "scope": r.scope,
                        "limit": r.limit,
                        "window_seconds": r.window_seconds,
                    }
                    for r in rules
                ]
            ),
            "instance_id": instance_id,
            "owner_ref": str(owner_ref or ""),
            "effect_id": str(effect_id or ""),
            "turn_id": identity["turn_id"],
            "session_id": identity["session_id"],
            "project_key": identity["project_key"],
            "request_id": identity["request_id"],
            "task_id": identity["task_id"],
            "agent_id": identity["agent_id"],
            "provider_id": identity["provider_id"],
            "created_epoch": now_epoch,
            "last_active_epoch": now_epoch,
        }
        conn.execute(
            f"INSERT INTO {_RESERVATIONS_TABLE} (reservation_id, budget_class, units, "
            "state, rules_json, instance_id, owner_ref, effect_id, turn_id, session_id, "
            "project_key, request_id, task_id, agent_id, provider_id, created_epoch, "
            "last_active_epoch) "
            "VALUES (:reservation_id, :budget_class, :units, :state, :rules_json, "
            ":instance_id, :owner_ref, :effect_id, :turn_id, :session_id, :project_key, "
            ":request_id, :task_id, :agent_id, :provider_id, :created_epoch, :last_active_epoch)",
            reservation_row,
        )
        _counter_adjust(conn, reservation_row, sign=1, field_name="reserved_units")
        conn.execute(
            f"INSERT INTO {_EVENTS_TABLE} (event_kind, budget_class, units, "
            "reservation_id, detail_json, instance_id, created_at) "
            "VALUES ('reserved', ?, ?, ?, ?, ?, ?)",
            (
                budget_class,
                units,
                reservation_id,
                json.dumps(
                    {
                        "rules": [r.rule_id for r in rules],
                        "effect_id": reservation_row["effect_id"],
                        "owner_ref": reservation_row["owner_ref"],
                        "turn_id": identity["turn_id"],
                        "session_id": identity["session_id"],
                        "project_key": identity["project_key"],
                        **_extended_identity(identity),
                    }
                ),
                instance_id,
                _utcnow_iso(),
            ),
        )
        _prune_stores(conn, now_epoch)
        conn.commit()
    except EffectBudgetRefusedError:
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"reserve: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store could not be consulted; the effect is refused "
            f"rather than run unbudgeted ({type(exc).__name__})",
        ) from exc
    return ReservationReceipt(
        reservation_id=reservation_id,
        budget_class=budget_class,
        units=units,
        rules=tuple(rules),
        unbudgeted=not rules,
        effect_id=str(effect_id or ""),
        owner_ref=str(owner_ref or ""),
    )


def bind_reservation_effect(reservation_id: str, effect_id: str) -> None:
    """Bind a reservation to the gateway-minted effect id (the id does not
    exist when the reservation is made — the ledger mints it at append).
    Idempotent; a settled reservation stays settled."""
    if not reservation_id or not effect_id:
        return
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"UPDATE {_RESERVATIONS_TABLE} SET effect_id=? WHERE reservation_id=? "
            "AND state=?",
            (str(effect_id), str(reservation_id), RESERVATION_RESERVED),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def consume_reservation(reservation_id: str) -> bool:
    """reserved → consumed. Returns True when this call consumed it, False
    when it was already consumed (idempotent per reservation).

    FAIL CLOSED on a released reservation: consuming a unit whose
    authorization was rolled back is a typed STATE refusal — the effect does
    not run unbudgeted after its rollback.
    """
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE reservation_id=?",
            (str(reservation_id),),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise EffectBudgetRefusedError(
                REFUSAL_STATE,
                f"reservation {reservation_id!r} does not exist; "
                "an unbudgeted effect cannot be consumed into existence",
            )
        state = str(row["state"])
        if state == RESERVATION_CONSUMED:
            conn.commit()  # idempotent: already consumed, nothing to write
            return False
        if state == RESERVATION_RELEASED:
            conn.rollback()
            raise EffectBudgetRefusedError(
                REFUSAL_STATE,
                f"reservation {reservation_id!r} was released "
                f"({str(row['settle_reason'] or 'released')}); a released unit "
                "cannot be consumed — the effect must re-reserve",
            )
        now_epoch = _utcnow_epoch()
        conn.execute(
            f"UPDATE {_RESERVATIONS_TABLE} SET state=?, settled_epoch=?, "
            "settle_reason='consumed by transport attempt', last_active_epoch=? "
            "WHERE reservation_id=? AND state=?",
            (
                RESERVATION_CONSUMED,
                now_epoch,
                now_epoch,
                str(reservation_id),
                RESERVATION_RESERVED,
            ),
        )
        _counter_adjust(conn, row, sign=-1, field_name="reserved_units")
        _counter_adjust(conn, row, sign=1, field_name="consumed_units")
        conn.execute(
            f"INSERT INTO {_EVENTS_TABLE} (event_kind, budget_class, units, "
            "reservation_id, instance_id, created_at) VALUES ('consumed', ?, ?, ?, ?, ?)",
            (
                str(row["budget_class"]),
                int(row["units"]),
                str(reservation_id),
                str(row["instance_id"]),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return True
    except EffectBudgetRefusedError:
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"consume: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store could not record the consumption ({type(exc).__name__})",
        ) from exc


def release_reservation(reservation_id: str, *, reason: str = "released") -> bool:
    """reserved → released (the unit returns). Idempotent; False when nothing
    was released (already settled)."""
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        full = conn.execute(
            f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE reservation_id=?",
            (str(reservation_id),),
        ).fetchone()
        if full is None or str(full["state"]) != RESERVATION_RESERVED:
            conn.commit()
            return False
        now_epoch = _utcnow_epoch()
        conn.execute(
            f"UPDATE {_RESERVATIONS_TABLE} SET state=?, settled_epoch=?, "
            "settle_reason=?, last_active_epoch=? WHERE reservation_id=? AND state=?",
            (
                RESERVATION_RELEASED,
                now_epoch,
                str(reason or "released"),
                now_epoch,
                str(reservation_id),
                RESERVATION_RESERVED,
            ),
        )
        _counter_adjust(conn, full, sign=-1, field_name="reserved_units")
        conn.execute(
            f"INSERT INTO {_EVENTS_TABLE} (event_kind, budget_class, units, "
            "reservation_id, detail_json, instance_id, created_at) "
            "VALUES ('released', ?, ?, ?, ?, ?, ?)",
            (
                str(full["budget_class"]),
                int(full["units"]),
                str(reservation_id),
                json.dumps({"reason": str(reason or "released")}),
                str(full["instance_id"]),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"release: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store could not record the release ({type(exc).__name__})",
        ) from exc


def release_unconsumed_for_owner(owner_ref: str, *, reason: str, quiet: bool = False) -> int:
    """The turn-scope-close rollback: release every reservation this owner
    still holds (an authorized effect that never executed). Returns how many.

    `quiet=True` is for the scope-close paths: a release failure there must
    not break closing a turn, and a HELD unit can never widen anything — the
    failure is counted on the store status instead (held-units conservatism).
    """
    if not owner_ref:
        return 0
    try:
        rows = reservation_rows(state=RESERVATION_RESERVED, owner_ref=str(owner_ref))
    except Exception as exc:
        if not quiet:
            raise EffectBudgetRefusedError(
                REFUSAL_STORE_UNAVAILABLE,
                f"the budget store could not run the owner rollback ({type(exc).__name__})",
            ) from exc
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"owner release: {type(exc).__name__}: {exc}"
        return 0
    released = 0
    for row in rows:
        try:
            if release_reservation(str(row["reservation_id"]), reason=reason):
                released += 1
        except EffectBudgetRefusedError:
            if not quiet:
                raise
            with _STORE_LOCK:
                _STORE_FAILURES["count"] += 1
                _STORE_FAILURES["last_error"] = f"owner release: reservation {row['reservation_id']}"
    return released


def effect_reservation_states(effect_id: str) -> list[str]:
    """The states of every reservation bound to one gateway effect id."""
    if not effect_id:
        return []
    conn = _budget_connection()
    _ensure_tables(conn)
    return [
        str(row["state"])
        for row in conn.execute(
            f"SELECT state FROM {_RESERVATIONS_TABLE} WHERE effect_id=?",
            (str(effect_id),),
        ).fetchall()
    ]


def release_effect_reservations(effect_id: str, *, reason: str = "reserved-then-never-run") -> int:
    """Release every still-RESERVED reservation bound to one gateway effect id.

    The rollback law's other half: an authorization that never began an
    attempt (a door refused before execution -- high-risk target, incomplete
    preimage capture, unavailable key) returns its unit to the pool. CONSUMED
    units are spent and never refunded; RELEASED rows are already returned.
    Returns how many reservations this call released.
    """
    if not effect_id:
        return 0
    conn = _budget_connection()
    _ensure_tables(conn)
    rows = conn.execute(
        f"SELECT reservation_id FROM {_RESERVATIONS_TABLE} WHERE effect_id=? AND state=?",
        (str(effect_id), RESERVATION_RESERVED),
    ).fetchall()
    released = 0
    for row in rows:
        if release_reservation(str(row["reservation_id"]), reason=reason):
            released += 1
    return released


def consume_effect_reservations(effect_id: str, *, budget_class: str = "") -> int:
    """Consume every OPEN reservation bound to one gateway effect id.

    The retry law: a retry continues an effect whose reservation is already
    consumed (or still reserved from an attempt that never began) — it never
    reserves a SECOND unit for the same logical effect. Returns how many
    reservations this call consumed.

    FAIL CLOSED twice: rows exist but every one is RELEASED → typed STATE
    refusal (the effect's authorization was rolled back; it must re-reserve
    through the gateway, not spend a returned unit); the class IS budgeted
    (rules exist for `budget_class`) but the effect has NO rows at all →
    typed STATE refusal — the hole this closes is 'skip the reserve, begin
    the attempt anyway'.
    """
    if not effect_id:
        return 0
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(conn)
        rows = conn.execute(
            f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE effect_id=?",
            (str(effect_id),),
        ).fetchall()
        if not rows:
            budgeted = False
            if budget_class:
                budgeted = bool(
                    conn.execute(
                        f"SELECT 1 FROM {_RULES_TABLE} WHERE budget_class=? LIMIT 1",
                        (str(budget_class),),
                    ).fetchone()
                )
            conn.rollback()
            if budgeted:
                raise EffectBudgetRefusedError(
                    REFUSAL_STATE,
                    f"effect {effect_id!r} of budgeted class {budget_class!r} has "
                    "no reservation — an attempt cannot begin on a skipped reserve",
                )
            return 0
        open_rows = [row for row in rows if str(row["state"]) == RESERVATION_RESERVED]
        consumed_any = any(str(row["state"]) == RESERVATION_CONSUMED for row in rows)
        conn.rollback()  # scan only; each consume takes its own transaction
        if not open_rows and not consumed_any:
            raise EffectBudgetRefusedError(
                REFUSAL_STATE,
                f"effect {effect_id!r} holds only released reservations; "
                "a rolled-back effect cannot spend its returned unit — "
                "re-reserve at the gateway",
            )
        consumed_now = 0
        for row in open_rows:
            if consume_reservation(str(row["reservation_id"])):
                consumed_now += 1
        return consumed_now
    except EffectBudgetRefusedError:
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"consume effect: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store could not record the consumption ({type(exc).__name__})",
        ) from exc


# ---------------------------------------------------------------------------
# preview + status (law: read-only, advisory)
# ---------------------------------------------------------------------------


def budget_status(
    budget_class: str,
    *,
    turn_id: str = "",
    session_id: str = "",
    project_key: str = "",
    request_id: str = "",
    task_id: str = "",
    agent_id: str = "",
    provider_id: str = "",
) -> list[RuleStatus]:
    """Remaining-budget status per configured rule, under one identity.
    Unbudgeted class → empty list (nothing configured, nothing to report)."""
    if budget_class not in BUDGET_CLASSES:
        raise EffectBudgetRefusedError(
            REFUSAL_UNKNOWN_CLASS, f"{budget_class!r} is not in the budget class catalog"
        )
    identity = _identity_of(
        turn_id=turn_id,
        session_id=session_id,
        project_key=project_key,
        request_id=request_id,
        task_id=task_id,
        agent_id=agent_id,
        provider_id=provider_id,
    )
    now_epoch = _utcnow_epoch()
    conn = _budget_connection()
    _ensure_tables(conn)
    statuses: list[RuleStatus] = []
    for rule in active_budgets(budget_class):
        scope_key = _scope_key(rule, identity)
        if scope_key is None:
            statuses.append(
                RuleStatus(rule=rule, limit=rule.limit, used=0, remaining=rule.limit, applies=False)
            )
            continue
        used = _rule_usage(conn, rule, scope_key, now_epoch)
        statuses.append(
            RuleStatus(
                rule=rule,
                limit=rule.limit,
                used=used,
                remaining=max(0, rule.limit - used),
                applies=True,
            )
        )
    return statuses


def preview_effect(
    budget_class: str,
    *,
    turn_id: str = "",
    session_id: str = "",
    project_key: str = "",
    units: int = 1,
    request_id: str = "",
    task_id: str = "",
    agent_id: str = "",
    provider_id: str = "",
) -> PreviewResult:
    """Would this reserve succeed, and what would remain? Reserves NOTHING.

    Advisory by construction: only `reserve_effect_units` is authoritative —
    between preview and reserve another effect may take the remaining units."""
    statuses = tuple(
        budget_status(
            budget_class,
            turn_id=turn_id,
            session_id=session_id,
            project_key=project_key,
            request_id=request_id,
            task_id=task_id,
            agent_id=agent_id,
            provider_id=provider_id,
        )
    )
    if not statuses:
        return PreviewResult(allowed=True, detail="unbudgeted class: no rules configured")
    for status in statuses:
        if not status.applies:
            continue
        if status.used + int(units) > status.limit:
            return PreviewResult(
                allowed=False,
                code=REFUSAL_BUDGET_EXCEEDED,
                rule=status.rule.rule_id,
                detail=(
                    f"{status.used} of {status.limit} units already spoken for; "
                    f"{int(units)} more would exceed {status.rule.rule_id}"
                ),
                per_rule=statuses,
            )
    return PreviewResult(allowed=True, per_rule=statuses)


# ---------------------------------------------------------------------------
# the wallet integration contract (AMENDMENT) — typed, executable, no adapter
# ---------------------------------------------------------------------------

#: The ordered law every integrating lane follows at the one real gate. The
#: wallet lane consumes THIS contract — it does not build a lane-specific
#: counter, its own refusal code, or a second authority.
WALLET_CONTRACT_SEQUENCE = (
    "reserve_before_authorization",
    "consume_immediately_before_execution",
    "reconcile_terminal_exactly_once",
    "unknown_outcomes_keep_their_units",
    "every_step_is_a_durable_receipt",
)


@dataclass(frozen=True)
class EffectBudgetContract:
    """The typed contract between this authority and an integrating lane.

    `budget_classes` — the classes the lane's effects reserve under.
    `refusal_codes` — the ONLY refusal vocabulary the lane may surface.
    `sequence` — the ordered lifecycle law (see WALLET_CONTRACT_SEQUENCE).
    `api` — the exact callables the lane uses; there is no other write path.
    """

    budget_classes: tuple[str, ...]
    refusal_codes: tuple[str, ...]
    sequence: tuple[str, ...]
    api: tuple[str, ...]

    @property
    def contract_id(self) -> str:
        return "effect-budget-contract/v1"


def wallet_effect_budget_contract() -> EffectBudgetContract:
    """The ONE integration contract for the wallet lane's two operations.

    DEPENDENCY TRUTH: end-to-end wallet budget enforcement is BLOCKED until
    the wallet lane converges — the adapter is concurrently built elsewhere
    and is NOT duplicated here. What exists and is executable NOW: the two
    classes in the catalog, the gateway effect-class mapping (budgets bind
    the moment wallet effects traverse `EffectLedger.open_effect`), and this
    contract + its conformance tests (`tests/effect_budget/
    test_wallet_contract_conformance.py`) which the wallet lane MUST keep
    green as it wires its doors.
    """
    return EffectBudgetContract(
        budget_classes=(
            BUDGET_CLASS_WALLET_TRANSACTION,
            BUDGET_CLASS_WALLET_SIGN,
        ),
        refusal_codes=(
            REFUSAL_BUDGET_EXCEEDED,
            REFUSAL_STORE_UNAVAILABLE,
            REFUSAL_AUTHORITY,
            REFUSAL_STATE,
            REFUSAL_UNKNOWN_CLASS,
        ),
        sequence=WALLET_CONTRACT_SEQUENCE,
        api=(
            "reserve_effect_units",
            "consume_effect_reservations",
            "release_reservation",
            "release_unconsumed_for_owner",
            "reconcile_stale_reservations",
            "effect_reservation_states",
            "budget_events",
        ),
    )


# ---------------------------------------------------------------------------
# post-execution cost truth (AMENDMENT) — measured spend vs reserved estimate
# ---------------------------------------------------------------------------


def reconcile_effect_cost(
    effect_id: str,
    *,
    actual_units: int,
    reservation_id: str = "",
) -> dict[str, Any]:
    """Reconcile an effect's MEASURED cost against its reserved estimate.

    Lookup by `effect_id` (the gateway/ledger path) or directly by
    `reservation_id` (a direct-leg reservation with no gateway effect id).

    For spend-weighted classes (provider_call today): the reservation held an
    ESTIMATE; the call has already happened when the true cost is known.
    Reconciliation writes the truth — and the one law the amendment names:

    THE CEILING CANNOT BE EXCEEDED SILENTLY. If `actual_units` exceeds what
    was consumed, the delta is charged to the same rules the reservation was
    reserved under, and when that pushes usage OVER a configured limit the
    event carries `over_limit: true` — a durable, queryable OVERAGE, visible
    to the operator and binding every future reserve (which will refuse
    until the budget resets or the operator adjusts it). A downward
    reconciliation (actual < estimate) records the truth and does NOT
    auto-refund — returning measured-but-unspent units is an explicit
    operator decision, never a silent one.

    Returns the reconciliation record. Nothing consumed to reconcile →
    typed STATE refusal.
    """
    effect_id = str(effect_id or "")
    reservation_id = str(reservation_id or "")
    actual_units = int(actual_units)
    if not effect_id and not reservation_id:
        raise EffectBudgetRefusedError(
            REFUSAL_STATE, "reconciliation needs an effect id or reservation id"
        )
    if actual_units < 0:
        raise EffectBudgetRefusedError(REFUSAL_STATE, "actual_units cannot be negative")
    conn = _budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_tables(conn)
        if reservation_id:
            row = conn.execute(
                f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE reservation_id=? AND state=?",
                (reservation_id, RESERVATION_CONSUMED),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT * FROM {_RESERVATIONS_TABLE} WHERE effect_id=? AND state=?",
                (effect_id, RESERVATION_CONSUMED),
            ).fetchone()
        if row is None:
            conn.rollback()
            raise EffectBudgetRefusedError(
                REFUSAL_STATE,
                f"effect {effect_id!r} has no consumed reservation; only an executed "
                "effect's cost can be reconciled",
            )
        reserved_units = int(row["units"])
        delta = actual_units - reserved_units
        over_limit = False
        if delta > 0:
            # charge the delta under the same rules, in this transaction —
            # counters move BEFORE the limit check reads them below, so the
            # overage is charged truthfully even past the ceiling (the call
            # happened; lying about it downward would be the silent pass)
            charged = dict(row)
            charged["units"] = delta
            for rule in _rules_of(row):
                if rule.scope == SCOPE_WINDOW:
                    continue
                scope_key = _scope_key_for_reservation(row, rule)
                if scope_key is None:
                    continue
                conn.execute(
                    f"INSERT INTO {_COUNTERS_TABLE} (budget_class, scope, scope_key, "
                    "reserved_units, consumed_units, updated_epoch) VALUES (?, ?, ?, 0, 0, ?) "
                    "ON CONFLICT(budget_class, scope, scope_key) DO NOTHING",
                    (rule.budget_class, rule.scope, scope_key, _utcnow_epoch()),
                )
                conn.execute(
                    f"UPDATE {_COUNTERS_TABLE} SET consumed_units=consumed_units+?, "
                    "updated_epoch=? WHERE budget_class=? AND scope=? AND scope_key=?",
                    (delta, _utcnow_epoch(), rule.budget_class, rule.scope, scope_key),
                )
            # the reservation row itself carries the reconciled truth
            conn.execute(
                f"UPDATE {_RESERVATIONS_TABLE} SET units=?, last_active_epoch=? "
                "WHERE reservation_id=?",
                (actual_units, _utcnow_epoch(), row["reservation_id"]),
            )
            now_epoch = _utcnow_epoch()
            for rule in _rules_of(row):
                scope_key = _scope_key_for_reservation(row, rule)
                if scope_key is None:
                    continue
                if _rule_usage(conn, rule, scope_key, now_epoch) > rule.limit:
                    over_limit = True
        record = {
            "effect_id": effect_id,
            "reservation_id": str(row["reservation_id"]),
            "budget_class": str(row["budget_class"]),
            "reserved_units": reserved_units,
            "actual_units": actual_units,
            "delta": delta,
            "over_limit": over_limit,
        }
        conn.execute(
            f"INSERT INTO {_EVENTS_TABLE} (event_kind, code, budget_class, units, "
            "reservation_id, detail_json, instance_id, created_at) "
            "VALUES ('cost_reconciled', ?, ?, ?, ?, ?, ?, ?)",
            (
                REFUSAL_BUDGET_EXCEEDED if over_limit else "",
                str(row["budget_class"]),
                delta,
                str(row["reservation_id"]),
                json.dumps(record),
                str(row["instance_id"]),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return record
    except EffectBudgetRefusedError:
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        with _STORE_LOCK:
            _STORE_FAILURES["count"] += 1
            _STORE_FAILURES["last_error"] = f"cost reconcile: {type(exc).__name__}: {exc}"
        raise EffectBudgetRefusedError(
            REFUSAL_STORE_UNAVAILABLE,
            f"the budget store could not record the cost reconciliation "
            f"({type(exc).__name__})",
        ) from exc





def reservation_rows(state: str = "", owner_ref: str = "") -> list[dict[str, Any]]:
    """Durable reservation rows (copies), optionally filtered."""
    conn = _budget_connection()
    _ensure_tables(conn)
    query = f"SELECT * FROM {_RESERVATIONS_TABLE}"
    clauses: list[str] = []
    params: list[Any] = []
    if state:
        clauses.append("state=?")
        params.append(state)
    if owner_ref:
        clauses.append("owner_ref=?")
        params.append(owner_ref)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def budget_events(event_kind: str = "", limit: int = 0) -> list[dict[str, Any]]:
    """The durable budget event journal (reservation/consumption/release/
    reconciliation/adjustment/grant/refusal), newest last."""
    conn = _budget_connection()
    _ensure_tables(conn)
    query = f"SELECT * FROM {_EVENTS_TABLE}"
    params: list[Any] = []
    if event_kind:
        query += " WHERE event_kind=?"
        params.append(event_kind)
    query += " ORDER BY seq"
    if limit and int(limit) > 0:
        query += " LIMIT ?"
        params.append(int(limit))
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def gateway_budget_class(effect_class: str) -> str:
    """The budget class a gateway effect class maps onto ('' = unbudgeted
    by name — passes without touching the store)."""
    return _GATEWAY_CLASS_MAP.get(str(effect_class or ""), "")


def reset_effect_budget_process_state() -> None:
    """Tests only: forget the process instance + once-per-process reconcile
    flag so a test can simulate a fresh boot against the same store."""
    _INSTANCE.clear()
    _RECONCILED_THIS_PROCESS.clear()
    try:
        from core.effect_budget_money import reset_money_process_state

        reset_money_process_state()
    except Exception:
        pass


__all__ = [
    "BUDGET_CLASSES",
    "BUDGET_SCOPES",
    "BudgetAdjustment",
    "BudgetRule",
    "EVENT_RETENTION_SECONDS",
    "EffectBudgetRefusedError",
    "INSTANCE_STALE_SECONDS",
    "OperatorBudgetToken",
    "PreviewResult",
    "REFUSAL_AUTHORITY",
    "REFUSAL_BUDGET_EXCEEDED",
    "REFUSAL_STATE",
    "REFUSAL_STORE_UNAVAILABLE",
    "REFUSAL_UNKNOWN_CLASS",
    "RESERVATION_CONSUMED",
    "RESERVATION_RELEASED",
    "RESERVATION_RESERVED",
    "RESERVATION_RETENTION_SECONDS",
    "ReservationReceipt",
    "RuleStatus",
    "SCOPE_AGENT",
    "SCOPE_GLOBAL",
    "SCOPE_PRECEDENCE",
    "SCOPE_PROJECT",
    "SCOPE_PROVIDER",
    "SCOPE_REQUEST",
    "SCOPE_SESSION",
    "SCOPE_TASK",
    "SCOPE_TURN",
    "SCOPE_WINDOW",
    "WALLET_CONTRACT_SEQUENCE",
    "EffectBudgetContract",
    "active_budgets",
    "apply_operator_adjustment",
    "bind_reservation_effect",
    "budget_events",
    "budget_status",
    "budget_status",
    "budget_store_status",
    "wallet_effect_budget_contract",
    "reconcile_effect_cost",
    "consume_reservation",
    "consume_effect_reservations",
    "current_budget_instance_id",
    "effect_reservation_states",
    "gateway_budget_class",
    "grant_operator_budget_authority",
    "preview_effect",
    "reconcile_stale_reservations",
    "release_reservation",
    "release_unconsumed_for_owner",
    "reset_effect_budget_process_state",
    "reservation_rows",
    "reserve_effect_units",
    "shutdown_effect_budget_instance",
]
