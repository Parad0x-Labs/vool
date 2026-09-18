"""MILESTONE 5 — the one permission/effect gateway's typed receipt layer.

THE CONVERGENCE DECISION (slice 1, from the seam survey):
`core.mode_permission_policy.decide_tool_call` is THE gateway — the most complete
policy inputs (mode matrix + revision, session/task identity, workspace-root and
project scoping with symlink resolution, contract-declared side-effect classes,
bounded grants with a durable approval lifecycle), the only typed three-valued
decision, and the widest real coverage (every model tool call via
`tool_intent_executor`, every live-data subtask via `evaluate_approval_policy`,
the semantic A0 door). The other seams converge ONTO it; nothing here re-decides.

WHAT THIS MODULE ADDS (the M5 contract's receipt half):
an `EffectReceipt` — one typed, immutable record per ATTEMPTED effect, including
DENIAL. The survey found denials leaving no record everywhere: the fetch door
raises with no trace, the frontdoor lane exits return `[]` silently, and the
existing retrieval receipts record only fetches PERFORMED. A denial with no
receipt is unprovable downstream — the A6 "unknown external outcome" class and
the M6 taxonomy both need the denial as a first-class fact.

R2 — PER-TURN EFFECT TRUTH (what changed and why)
-------------------------------------------------
The receipt channel was ONE process-global list and the policy was rebuilt at
every door. Both are turn-scoped state, and holding turn-scoped state in module
globals produced five separate untruths:

* two turns running at once appended to the same list, so each could read — and
  report — the other's effects;
* either turn's scope exit reset the shared list, so a turn that finished first
  drained a turn still running;
* a nested scope's exit closed the channel its parent was still writing to;
* the receipts a scope did collect were returned and then dropped, so nothing
  downstream — `TurnResult` included — could read what the turn had done;
* the policy was re-derived per effect from live state, so two doors in one turn
  could decide under two different policies and a mode raised mid-turn widened
  the turn already in flight.

So the channel is now an `EffectLedger`: one bounded, lock-guarded object per
turn, held by reference in a ContextVar exactly like `_FetchLedger` — worker
tasks that inherit the turn's context through `copy_context` mutate the SAME
ledger, and a scope that closes restores its parent's binding instead of
destroying it. The turn's `TurnPolicy` is frozen ONCE onto that ledger, and the
network, command and filesystem gates all consume that one frozen object.

Slice 1's effect class: NETWORK FETCH, normalized at the one outbound HTTP door
(`remote_fetch_policy.open_remote` — ~45 call sites already converge there).
Every door outcome — allowed-then-attempted, or denied — emits a receipt into the
turn's ledger.

Later slices migrate the remaining effect classes (remote API, public write,
financial, provider call, helper delegation) onto this receipt shape and retire
their per-lane record formats, and emit the started/succeeded/failed/cancelled
half of the lifecycle vocabulary at the doors that can observe an outcome.

R2b2b — DURABLE LIFECYCLE TRUTH (the slice that emits that half, for the one
transport that serves the web lane)
---------------------------------------------------------------------------------
Through R2b1 the account ended at `authorized`: the receipt was written before
the socket and nothing after it, so an authorized fetch that timed out read
identically to one that returned a page — and every append minted a NEW
effect id, so even a later state could not be attributed to the same logical
effect. `EffectLedger` now keeps an effect REGISTRY (bounded like the receipt
account) and hands the door an `EffectLifecycle` handle — the one typed
authority for one logical effect's attempts:

* `open_effect` registers the logical effect (its authorized — or denied —
  receipt) and returns the handle; a `retry_of` that names a REGISTERED effect
  of this ledger continues THAT effect instead of minting a second one;
* `begin_attempt` appends `started` and is called immediately before the real
  transport call — the socket must be able to see `started` on the record;
* `succeed` / `fail` / `cancel` append exactly ONE terminal per attempt,
  idempotently: a duplicate terminal reports the first outcome and appends
  nothing;
* identity (request_id, turn_id, policy_id, scope) comes from the OWNING
  ledger, never from the ambient context — a worker thread holding only the
  handle still records under the turn's canonical identity;
* failure reasons are TYPE-DERIVED tokens (`safe_transport_failure_reason`) —
  never exception text, which can carry the secret-bearing URL.

The lifecycle authority is effect-class agnostic but is WIRED only at the
HTTP/remote-fetch transport in this slice (`remote_fetch_policy.open_remote`,
which `tools/web/http_fetch.http_fetch_text` — the served web lane's page
transport — now goes through instead of its own raw urlopen). Other effect
classes keep their R2/R2b1 behavior; unifying them is later work.

R2b2c — DURABLE BACKGROUND EFFECT EVIDENCE (AMENDED: atomic + honest)
---------------------------------------------------------------------
R2b2a1's named scopes were honest about their retention truth: "scope-local,
handed to no one at close, not durable". A relay daemon's effect account died
with its process — and its `run_forever` scope never even closes. The
`BackgroundEffectJournal` below is the ONE durable publisher for background
scope evidence, at this ledger authority (no relay writes its own records;
the low-level send helpers stay dumb):

* a scope opened `durable=True` (the three relay daemon scopes, and nothing
  else) publishes every lifecycle entry AS IT OCCURS into the journal's OWN
  tables — queryable mid-flight and across restart, with the scope's name
  and a per-open daemon instance (`bgi:…`);
* ATOMIC (the amendment's first law): an accepted event creates its visible
  row AND its hash witness in ONE transaction, or creates neither. The
  R2b2c-era journal rode `storage.event_log.append_event`, which commits the
  visible row before the witness — a mid-commit failure left an unauditable
  orphan. The journal no longer uses that path, and never grows the global
  `event_hash_chain`;
* SEGMENTED, CHECKPOINTED retention (the amendment's second law): the cap
  prunes visible rows AND their witnesses together, in the prune
  transaction, advancing a checkpoint accumulator (the pruned tail's hash +
  cumulative pruned count). BOTH stores stay bounded; the retained chain
  verifies FROM the checkpoint hash across the pruned boundary; individual
  pruned events are NOT recoverable and nothing claims they are;
* the query exposes the whole truth: visible rows, row↔witness PARITY, chain
  verification recomputed from the checkpoint, retained/pruned counts,
  checkpoint state and the persistence-failure account. Lifecycle ORDERING
  is ordering — it is never called a cryptographic chain;
* persistence failure NEVER blocks, widens or falsifies a transport: it is
  counted on the ONE locked-singleton journal (`background_effect_
  persistence_status`) and the query reports it instead of inventing
  evidence; a conflicting redelivery under one event_id is refused visibly;
* reasons pass a last-line sanitizer before disk: length-capped, and any
  URL/token/webhook/bearer text is redacted to a marker; the payload
  whitelist has no request_id/turn_id/session keys at all.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import threading
from contextlib import contextmanager
from contextlib import suppress as contextlib_suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.policy_projection import (
    FrozenMapping,
    project_context,
    render_mutable,
    unreadable_policy_inputs,
)

EFFECT_NETWORK_FETCH = "network_fetch"
EFFECT_COMMAND = "command"

DECISION_ALLOWED = "allowed"
DECISION_DENIED = "denied"
#: A command whose execution policy allows only simulation: neither executed
#: nor refused — a third, mechanically distinct outcome (M5's denied/unknown/
#: cancelled distinction starts here).
DECISION_SIMULATED = "simulated"

#: The typed lifecycle vocabulary. `decision` says what the gateway RULED;
#: `lifecycle` says where the effect actually got to. They are separate axes on
#: purpose: an authorized fetch that then fails is `authorized`+`failed`, and
#: collapsing the two is how "we allowed it" came to read as "it happened".
LIFECYCLE_DENIED = "denied"
LIFECYCLE_AUTHORIZED = "authorized"
LIFECYCLE_STARTED = "started"
LIFECYCLE_SUCCEEDED = "succeeded"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_CANCELLED = "cancelled"
LIFECYCLE_UNKNOWN = "unknown"
LIFECYCLE_SIMULATED = "simulated"

LIFECYCLE_STATES = frozenset(
    {
        LIFECYCLE_DENIED,
        LIFECYCLE_AUTHORIZED,
        LIFECYCLE_STARTED,
        LIFECYCLE_SUCCEEDED,
        LIFECYCLE_FAILED,
        LIFECYCLE_CANCELLED,
        LIFECYCLE_UNKNOWN,
        LIFECYCLE_SIMULATED,
    }
)

#: The terminal half of the lifecycle vocabulary: an attempt that began may end
#: in exactly one of these, and no other state may follow one of them.
LIFECYCLE_TERMINALS = frozenset(
    {LIFECYCLE_SUCCEEDED, LIFECYCLE_FAILED, LIFECYCLE_CANCELLED}
)

#: The lifecycle a bare gateway decision implies when the caller states no other.
_DECISION_LIFECYCLE = {
    DECISION_ALLOWED: LIFECYCLE_AUTHORIZED,
    DECISION_DENIED: LIFECYCLE_DENIED,
    DECISION_SIMULATED: LIFECYCLE_SIMULATED,
}

#: A turn cannot make more effect attempts than this and still be a turn. The cap
#: bounds a retry storm's memory; `truncated` says the account stopped being
#: complete, so a short list can never read as the whole story.
MAX_RECEIPTS_PER_TURN = 256

#: R2b2c — the HARD retention bound on DURABLE background-effect events. The
#: in-memory ledger ceiling does not govern durability (a `run_forever` scope
#: can outlive any memory account); this cap does, and every event it removes
#: is counted in the durable retention account row.
BACKGROUND_EFFECT_EVENT_CAP = 4096

#: The key a closing scope publishes the turn's finalized receipts under, on the
#: turn's own source_context — the same way the turn already carries its
#: retrieval receipts. Facts only: nothing reads these back for policy, which
#: would make accounting an authority.
EFFECT_RECEIPTS_CONTEXT_KEY = "effect_receipts"
EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY = "effect_receipts_truncated"
#: R2b1: the exact number of receipts the bounded ledger dropped, published
#: beside the truncation flag — a flag alone cannot be checked against how
#: much account was lost.
EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY = "effect_receipts_dropped"


class PolicyUnavailableError(RuntimeError):
    """The turn's policy could not be built or consulted; the effect is denied."""


class EffectStateError(RuntimeError):
    """A lifecycle transition the effect's state does not permit (R2b2b) — e.g.
    beginning a transport attempt on an effect the gateway DENIED."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_transport_failure_reason(exc: BaseException | None) -> str:
    """A failure reason built from exception TYPE NAMES only (R2b2b, req 6).

    Exception text is not receipt-safe: a URLError's message can embed the full
    request URL — query-string secrets included. The type-derived token names
    the failure class (and `timeout`, and an HTTP status class when urllib
    raises one) without repeating anything the caller sent or received.
    """
    if exc is None:
        return "failed"
    parts: list[str] = [type(exc).__name__]
    chain: list[BaseException] = [exc]
    inner = getattr(exc, "reason", None)
    if isinstance(inner, BaseException):
        chain.append(inner)
        parts.append(type(inner).__name__)
    if any(isinstance(item, TimeoutError) for item in chain):
        parts.insert(0, "timeout")
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        parts.insert(0, f"http_{code}")
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return ":".join(seen)


def _is_cancellation(exc: BaseException) -> bool:
    """The CancelledError family is CANCELLATION — a third terminal, distinct
    from failure. asyncio's and concurrent.futures' CancelledError are NOT the
    same class on this interpreter (3.12), so the family is matched by name as
    well as by asyncio's base class."""
    try:
        import asyncio

        if isinstance(exc, asyncio.CancelledError):
            return True
    except Exception:  # pragma: no cover - asyncio is stdlib
        pass
    return type(exc).__name__ == "CancelledError"


@dataclass(frozen=True)
class EffectReceipt:
    """One attempted effect, typed — the denial is a first-class outcome.

    Fields are deliberately receipt-shaped, not result-shaped: no response
    bodies, no full URLs (host only — the URL can carry query-string secrets;
    the ledger's address record stays where it is, in the fetch ledger).

    Identity fields are OWNED BY THE RECEIPT'S LEDGER: a caller may state them,
    but the recording ledger replaces any stated value with its canonical
    identity and names the replacement in `identity_note` (R2b1) — a stated
    identity is never silently trusted. `scope_name` is the named non-turn
    scope a receipt was recorded under, when it was one (empty for turns).
    """

    effect_class: str
    decision: str
    lifecycle: str = ""
    reason: str = ""
    host: str = ""
    mode: str = ""
    decided_by: str = ""
    recorded_at: str = ""
    request_id: str = ""
    turn_id: str = ""
    effect_id: str = ""
    policy_id: str = ""
    scope_name: str = ""
    identity_note: str = ""
    #: WHO the effect went to, when the caller can name it. `host` alone cannot
    #: answer that: `api.search.brave.com` reached with the user's key and a
    #: keyless scraper reading Brave's HTML are different facts about what that
    #: key did, and the ledger recorded the same host for both. Empty for an
    #: effect with no provider identity (an ordinary page fetch), never guessed.
    provider_id: str = ""
    #: Whether a stored credential paid for this effect. "keyed" | "keyless" | "".
    keyed_or_keyless: str = ""
    #: R2b2b — which transport ATTEMPT of the logical effect this entry belongs
    #: to. 0 = no attempt yet (the policy decision, or a terminal that landed
    #: before any socket, such as a denial); n = the n-th begun attempt.
    attempt: int = 0

    def __post_init__(self) -> None:
        decision = str(self.decision or "")
        if decision not in _DECISION_LIFECYCLE:
            raise ValueError(f"unknown effect decision: {decision!r}")
        lifecycle = str(self.lifecycle or "") or _DECISION_LIFECYCLE[decision]
        if lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"unknown effect lifecycle: {lifecycle!r}")
        object.__setattr__(self, "lifecycle", lifecycle)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_class": self.effect_class,
            "decision": self.decision,
            "lifecycle": self.lifecycle,
            "reason": self.reason,
            "host": self.host,
            "mode": self.mode,
            "decided_by": self.decided_by,
            "recorded_at": self.recorded_at,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "effect_id": self.effect_id,
            "policy_id": self.policy_id,
            "scope_name": self.scope_name,
            "identity_note": self.identity_note,
            "provider_id": self.provider_id,
            "keyed_or_keyless": self.keyed_or_keyless,
            "attempt": int(self.attempt or 0),
        }


class _JournalConflictError(RuntimeError):
    """A redelivery under one event_id named a DIFFERENT payload than the
    recorded one. A visible refusal, not a persistence failure: counted as
    its own class and NOTHING is written."""


class BackgroundEffectJournal:
    """The ONE durable publisher for background-scope effect evidence
    (R2b2c, AMENDED: atomic and honest hash-journal retention).

    Lives at the ledger authority on purpose: relays and other background
    lanes pass `durable=True` at their scope's owning entry point and nothing
    else — no lane writes its own records, and the low-level send helpers
    stay dumb. Every lifecycle entry a durable scope's ledger appends (or
    drops to its memory ceiling) is published AS IT OCCURS, because a
    `run_forever` scope never closes to hand anything over.

    AMENDED persistence contract — the R2b2c gaps this closes:

    * ATOMIC — an accepted event creates its visible row AND its hash
      witness in ONE transaction, or creates neither. (R2b2c rode
      `storage.event_log.append_event`, which commits the visible row before
      the witness is written: a mid-commit failure left an unauditable
      orphan.)
    * SEGMENTED, CHECKPOINTED WITNESS — the journal owns three tables
      (visible rows, witness rows, one checkpoint accumulator). Retention
      prunes visible rows AND their witnesses in lockstep, inside the same
      transaction, and advances the checkpoint: the pruned tail's hash and
      the cumulative pruned count. The RETAINED chain verifies from the
      checkpoint hash across the pruned boundary. Storage is bounded —
      visible AND witness rows together. Individual pruned events are NOT
      recoverable; only their count and the accumulator's continuity are.
      The GLOBAL `event_hash_chain` is never grown by this journal.
    * IDEMPOTENT, CONFLICT-VISIBLE — the event id is deterministic per
      (effect, attempt, lifecycle); identical redelivery writes nothing;
      a CONFLICTING payload under one event_id is refused, counted as a
      conflict, and changes nothing.
    * NO TURN IDENTITY — the payload whitelist carries no request_id,
      turn_id or session keys; a background effect owns none, and the
      absence must be provable, not empty-stringed.
    * NEVER AUTHORIZES, NEVER FALSIFIES — a persistence failure is counted
      on the ONE locked-singleton journal and never raises into the effect
      path: transport behavior is governed by the effect policy alone, and
      the evidence account reports the failures instead of inventing
      outcomes.
    """

    CATEGORY = "background_effect"
    _VISIBLE_TABLE = "background_effect_journal_v1"
    _WITNESS_TABLE = "background_effect_witness_v1"
    _CHECKPOINT_TABLE = "background_effect_checkpoint_v1"
    _UNSAFE_REASON_MARKERS = ("token", "webhook", "bearer", "authorization")
    _WITNESS_ALGORITHM = (
        "sha256 over canonical {prev_hash, payload}; segmented with a "
        "checkpoint accumulator — verification starts at the checkpoint hash"
    )

    def __init__(self, cap: int = BACKGROUND_EFFECT_EVENT_CAP) -> None:
        self.cap = max(1, int(cap))
        #: RLock: the publish transaction and the observability counters
        #: share it; verification reads outside it (tables are committed).
        self._lock = threading.RLock()
        self._failures = 0
        self._conflicts = 0
        self._last_error = ""

    # -- observability -----------------------------------------------------
    def persistence_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "failures": self._failures,
                "conflicts": self._conflicts,
                "last_error": self._last_error,
                "cap": self.cap,
            }

    def _note_failure(self, exc: BaseException) -> None:
        with self._lock:
            self._failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _note_conflict(self, exc: BaseException) -> None:
        with self._lock:
            self._conflicts += 1
            self._last_error = f"conflict: {type(exc).__name__}: {exc}"

    # -- storage -----------------------------------------------------------
    def _journal_connection(self):
        """The journal's connection seam — the one place tests inject an
        unavailable store from."""
        from storage.db import get_connection

        return get_connection()

    def _ensure_tables(self, conn: Any) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._VISIBLE_TABLE} (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                scope_name TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._WITNESS_TABLE} (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._CHECKPOINT_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                head_hash TEXT NOT NULL,
                pruned_events INTEGER NOT NULL,
                cap INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"INSERT OR IGNORE INTO {self._CHECKPOINT_TABLE} "
            "(id, head_hash, pruned_events, cap, updated_at) VALUES (1, '', 0, ?, ?)",
            (int(self.cap), _utcnow()),
        )

    @staticmethod
    def _witness_hash(prev_hash: str, payload_json: str) -> str:
        """sha256 over the canonical wrapper of the PREDECESSOR HASH and the
        EXACT stored payload string — any byte of either changing breaks
        verification."""
        import hashlib

        raw = json.dumps(
            {"prev_hash": str(prev_hash or ""), "payload": str(payload_json)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    # -- the publish path ----------------------------------------------------
    def record(
        self,
        entry: dict[str, Any],
        *,
        scope_name: str,
        scope_instance: str,
        dropped_from_ledger: bool = False,
    ) -> bool:
        """Publish one lifecycle entry durably and ATOMICALLY: visible row
        and witness commit together or not at all. Returns whether the store
        accepted it; refusal (conflict, corrupt/unavailable store) is a
        visible count, never an exception into the effect path."""
        import json as _json

        event_id = self._event_id_for(entry)
        payload = self._payload_for(
            entry,
            scope_name=scope_name,
            scope_instance=scope_instance,
            dropped_from_ledger=dropped_from_ledger,
        )
        payload_json = _json.dumps(payload, sort_keys=True)
        try:
            with self._lock:
                conn = self._journal_connection()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    self._ensure_tables(conn)
                    self._publish_locked(
                        conn,
                        event_id,
                        payload_json,
                        scope_name=str(scope_name or ""),
                        effect_id=str(entry.get("effect_id") or ""),
                    )
                    self._enforce_retention(conn)
                    conn.commit()
                    return True
                except _JournalConflictError as exc:
                    with contextlib_suppress(Exception):
                        conn.rollback()
                    self._note_conflict(exc)
                    return False
                except Exception:
                    with contextlib_suppress(Exception):
                        conn.rollback()
                    raise
                finally:
                    conn.close()
        except Exception as exc:
            self._note_failure(exc)
            return False

    def _publish_locked(
        self,
        conn: Any,
        event_id: str,
        payload_json: str,
        *,
        scope_name: str,
        effect_id: str,
    ) -> None:
        """The acceptance rules, inside the publish transaction.

        * witnessed already, same payload → redelivery: ensure the visible
          half exists, write nothing else;
        * witnessed or visible already with a DIFFERENT payload → conflict,
          refused visibly, nothing written;
        * visible without a witness (a torn record) and same payload → the
          witness is written and parity is HEALED;
        * fresh → witness first, visible row second: an injected failure in
          the witness write rolls the transaction back with it, so no
          unauditable orphan can exist.
        """
        witness = conn.execute(
            f"SELECT payload_json FROM {self._WITNESS_TABLE} WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        visible = conn.execute(
            f"SELECT payload_json FROM {self._VISIBLE_TABLE} WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if witness is not None:
            if str(witness["payload_json"]) != payload_json:
                raise _JournalConflictError(
                    f"event_id {event_id!r} is already witnessed with a different payload"
                )
            if visible is None:
                conn.execute(
                    f"INSERT INTO {self._VISIBLE_TABLE} "
                    "(event_id, scope_name, effect_id, payload_json, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (event_id, scope_name, effect_id, payload_json, _utcnow()),
                )
            return
        if visible is not None and str(visible["payload_json"]) != payload_json:
            raise _JournalConflictError(
                f"event_id {event_id!r} is already visible with a different payload"
            )
        self._append_witness(conn, event_id, payload_json)
        if visible is None:
            conn.execute(
                f"INSERT INTO {self._VISIBLE_TABLE} "
                "(event_id, scope_name, effect_id, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (event_id, scope_name, effect_id, payload_json, _utcnow()),
            )

    def _append_witness(self, conn: Any, event_id: str, payload_json: str) -> None:
        """The witness half of an atomic publish — one seam, so an injected
        mid-commit failure here provably rolls the visible row back with it."""
        head = conn.execute(
            f"SELECT event_hash FROM {self._WITNESS_TABLE} ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if head is not None:
            prev_hash = str(head["event_hash"])
        else:
            checkpoint = conn.execute(
                f"SELECT head_hash FROM {self._CHECKPOINT_TABLE} WHERE id = 1"
            ).fetchone()
            prev_hash = str(checkpoint["head_hash"]) if checkpoint is not None else ""
        event_hash = self._witness_hash(prev_hash, payload_json)
        conn.execute(
            f"INSERT INTO {self._WITNESS_TABLE} "
            "(event_id, prev_hash, event_hash, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (event_id, prev_hash, event_hash, payload_json, _utcnow()),
        )

    def _event_id_for(self, entry: dict[str, Any]) -> str:
        """Deterministic per (effect, attempt, lifecycle): a duplicate write
        names the SAME event, so redelivery is a no-op and a conflicting
        redelivery is detectable."""
        return "bge:{effect}:{attempt}:{lifecycle}".format(
            effect=str(entry.get("effect_id") or ""),
            attempt=int(entry.get("attempt") or 0),
            lifecycle=str(entry.get("lifecycle") or ""),
        )

    def _safe_reason(self, reason: Any) -> str:
        """The last line before disk. Upstream reasons are type-derived
        tokens (R2b2b), but a leaky one — a URL, a token fragment, an
        Authorization header — is redacted here rather than persisted."""
        text = str(reason or "")
        if not text:
            return ""
        if len(text) > 80:
            return "reason_redacted"
        lowered = text.lower()
        if "://" in text or any(marker in lowered for marker in self._UNSAFE_REASON_MARKERS):
            return "reason_redacted"
        return text

    def _payload_for(
        self,
        entry: dict[str, Any],
        *,
        scope_name: str,
        scope_instance: str,
        dropped_from_ledger: bool,
    ) -> dict[str, Any]:
        """The durable schema — a WHITELIST, so nothing else (secrets, URLs,
        headers, bodies, turn identity) can ride along even by accident."""
        return {
            "kind": "effect",
            "effect_id": str(entry.get("effect_id") or ""),
            "attempt": int(entry.get("attempt") or 0),
            "lifecycle": str(entry.get("lifecycle") or ""),
            "effect_class": str(entry.get("effect_class") or ""),
            "decision": str(entry.get("decision") or ""),
            "reason": self._safe_reason(entry.get("reason")),
            "host": str(entry.get("host") or ""),
            "mode": str(entry.get("mode") or ""),
            "decided_by": str(entry.get("decided_by") or ""),
            "recorded_at": str(entry.get("recorded_at") or ""),
            "scope_name": str(scope_name or ""),
            "scope_instance": str(scope_instance or ""),
            "dropped_from_ledger": bool(dropped_from_ledger),
        }

    # -- the hard retention bound -------------------------------------------
    def _enforce_retention(self, conn: Any) -> None:
        """Prune the oldest visible rows AND their witnesses — in lockstep,
        inside the publish transaction — and advance the checkpoint so the
        retained chain still verifies from the accumulator. Both stores stay
        bounded together; neither outlives the cap the account claims."""
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {self._VISIBLE_TABLE}").fetchone()
        overflow = int(row["n"]) - self.cap
        if overflow <= 0:
            return
        victims = conn.execute(
            f"SELECT event_id FROM {self._VISIBLE_TABLE} ORDER BY seq ASC LIMIT ?",
            (overflow,),
        ).fetchall()
        if not victims:
            return
        event_ids = [str(item["event_id"]) for item in victims]
        marks = ",".join("?" for _ in event_ids)
        newest_pruned = conn.execute(
            f"SELECT event_hash FROM {self._WITNESS_TABLE} "
            f"WHERE event_id IN ({marks}) ORDER BY seq DESC LIMIT 1",
            event_ids,
        ).fetchone()
        conn.execute(
            f"DELETE FROM {self._VISIBLE_TABLE} WHERE event_id IN ({marks})",
            event_ids,
        )
        conn.execute(
            f"DELETE FROM {self._WITNESS_TABLE} WHERE event_id IN ({marks})",
            event_ids,
        )
        new_head = str(newest_pruned["event_hash"]) if newest_pruned is not None else None
        self._advance_checkpoint(conn, pruned=len(event_ids), new_head=new_head)

    def _advance_checkpoint(self, conn: Any, *, pruned: int, new_head: str | None) -> None:
        """The accumulator: the pruned tail's hash plus the cumulative pruned
        count, updated IN the prune transaction — a frozen accumulator would
        orphan the retained chain (its oldest witness links to a hash
        nobody attests)."""
        checkpoint = conn.execute(
            f"SELECT head_hash, pruned_events FROM {self._CHECKPOINT_TABLE} WHERE id = 1"
        ).fetchone()
        if checkpoint is None:
            head = new_head or ""
            previous = 0
        else:
            head = new_head if new_head is not None else str(checkpoint["head_hash"])
            previous = int(checkpoint["pruned_events"])
        conn.execute(
            f"INSERT INTO {self._CHECKPOINT_TABLE} "
            "(id, head_hash, pruned_events, cap, updated_at) VALUES (1, ?, ?, ?, ?)"
            "ON CONFLICT(id) DO UPDATE SET head_hash = excluded.head_hash,"
            " pruned_events = excluded.pruned_events, cap = excluded.cap,"
            " updated_at = excluded.updated_at",
            (head, previous + int(pruned), int(self.cap), _utcnow()),
        )

    # -- verification -------------------------------------------------------
    def witness_state(self) -> dict[str, Any]:
        """The cryptographic account: parity between visible rows and
        witnesses (same ids, same payloads) and full recomputation of the
        retained chain from the checkpoint hash. Ordering was never this —
        the R2b2c amendment exists because ordering survived a dead witness."""
        state: dict[str, Any] = {
            "parity_ok": False,
            "chain_verified": False,
            "retained_witnesses": 0,
            "retained_events": 0,
            "head_hash": "",
            "tail_hash": "",
            "algorithm": self._WITNESS_ALGORITHM,
            "query_error": "",
        }
        try:
            conn = self._journal_connection()
            try:
                self._ensure_tables(conn)
                checkpoint = conn.execute(
                    f"SELECT head_hash, pruned_events FROM {self._CHECKPOINT_TABLE}"
                    " WHERE id = 1"
                ).fetchone()
                head_hash = str(checkpoint["head_hash"]) if checkpoint is not None else ""
                witnesses = conn.execute(
                    f"SELECT event_id, prev_hash, event_hash, payload_json"
                    f" FROM {self._WITNESS_TABLE} ORDER BY seq ASC"
                ).fetchall()
                visibles = conn.execute(
                    f"SELECT event_id, payload_json FROM {self._VISIBLE_TABLE}"
                    " ORDER BY seq ASC"
                ).fetchall()
                visible_payloads = {
                    str(row["event_id"]): str(row["payload_json"]) for row in visibles
                }
                parity = len(visible_payloads) == len(witnesses)
                prev = head_hash
                verified = True
                for row in witnesses:
                    payload_json = str(row["payload_json"])
                    parity = parity and visible_payloads.get(str(row["event_id"])) == payload_json
                    if str(row["prev_hash"]) != prev or self._witness_hash(
                        prev, payload_json
                    ) != str(row["event_hash"]):
                        verified = False
                    prev = str(row["event_hash"])
                parity = parity and len(visible_payloads) == len(
                    {str(row["event_id"]) for row in witnesses}
                )
                state.update(
                    {
                        "parity_ok": bool(parity),
                        "chain_verified": bool(verified),
                        "retained_witnesses": len(witnesses),
                        "retained_events": len(visibles),
                        "head_hash": head_hash,
                        "tail_hash": prev,
                    }
                )
            finally:
                conn.close()
        except Exception as exc:
            state["query_error"] = f"{type(exc).__name__}: {exc}"
        return state


_BACKGROUND_EFFECT_JOURNAL: BackgroundEffectJournal | None = None
#: R2b2c amendment — lazy construction raced: two first-access threads could
#: each build a journal, and the failure accounting of whichever assigned
#: last was silently overwritten. Initialization (and reset) hold this lock;
#: double-checked so the hot path stays lock-free once constructed.
_JOURNAL_INIT_LOCK = threading.Lock()


def _background_effect_journal() -> BackgroundEffectJournal:
    global _BACKGROUND_EFFECT_JOURNAL
    journal = _BACKGROUND_EFFECT_JOURNAL
    if journal is None:
        with _JOURNAL_INIT_LOCK:
            if _BACKGROUND_EFFECT_JOURNAL is None:
                _BACKGROUND_EFFECT_JOURNAL = BackgroundEffectJournal()
            journal = _BACKGROUND_EFFECT_JOURNAL
    return journal


def reset_background_effect_journal() -> None:
    """Drop the in-process journal state (failure/conflict counters). Durable
    evidence lives in the store, so a "restart" is exactly this plus a fresh
    query — the reconstructability the R2b2c tests pin. Takes the init lock:
    a reset racing a first access must not hand out a journal the next line
    erases."""
    global _BACKGROUND_EFFECT_JOURNAL
    with _JOURNAL_INIT_LOCK:
        _BACKGROUND_EFFECT_JOURNAL = None


def background_effect_evidence(
    *,
    scope_name: str = "",
    effect_id: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    """Durable background-effect evidence, read FRESH from the journal's own
    tables every call (no cache: a restarted process reconstructs the same
    account).

    The report is the whole honesty:

    * `events` — the visible rows, newest-last within the limit, each
      carrying the preserved effect fields, the store timestamp, the scope
      and the daemon instance;
    * `witness` — the cryptographic account: row↔witness PARITY and full
      chain verification recomputed from the checkpoint hash (ordering was
      never this — see the amendment);
    * `checkpoint` — the retention accumulator: the pruned tail's hash, the
      cumulative pruned count, the cap in force;
    * `kept`/`dropped`/`truncated` — the retention account in one place;
    * `persistence_failures`/`persistence_last_error` — the write-side
      account, conflicts included.

    A store that cannot be read says so in `query_error` and returns NO
    events — an unreadable account never becomes an invented one.
    """
    journal = _background_effect_journal()
    status = journal.persistence_status()
    report: dict[str, Any] = {
        "events": (),
        "kept": 0,
        "dropped": 0,
        "truncated": False,
        "cap": status["cap"],
        "persistence_failures": status["failures"],
        "persistence_last_error": status["last_error"],
        "query_error": "",
        "witness": journal.witness_state(),
        "checkpoint": {},
    }
    try:
        conn = journal._journal_connection()
        try:
            journal._ensure_tables(conn)
            where = ["1=1"]
            params: list[Any] = []
            if scope_name:
                where.append("scope_name = ?")
                params.append(str(scope_name))
            if effect_id:
                where.append("effect_id = ?")
                params.append(str(effect_id))
            clause = " AND ".join(where)
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {journal._VISIBLE_TABLE} WHERE {clause}",
                params,
            ).fetchone()
            report["kept"] = int(row["n"])
            checkpoint = conn.execute(
                f"SELECT head_hash, pruned_events, cap, updated_at"
                f" FROM {journal._CHECKPOINT_TABLE} WHERE id = 1"
            ).fetchone()
            if checkpoint is not None:
                report["checkpoint"] = {
                    "head_hash": str(checkpoint["head_hash"]),
                    "pruned_events": int(checkpoint["pruned_events"]),
                    "cap": int(checkpoint["cap"]),
                    "updated_at": str(checkpoint["updated_at"]),
                }
                report["dropped"] = int(checkpoint["pruned_events"])
            rows = conn.execute(
                f"SELECT event_id, scope_name, effect_id, payload_json, created_at"
                f" FROM {journal._VISIBLE_TABLE} WHERE {clause}"
                " ORDER BY seq DESC LIMIT ?",
                [*params, max(1, min(int(limit), 2000))],
            ).fetchall()
            events = []
            for stored in reversed(rows):
                try:
                    payload = json.loads(str(stored["payload_json"]))
                except Exception:
                    payload = {"kind": "unreadable_payload"}
                events.append(
                    {
                        **payload,
                        "event_id": str(stored["event_id"]),
                        "stored_at": str(stored["created_at"]),
                        "scope_name": str(stored["scope_name"]),
                        "effect_id": str(stored["effect_id"] or "")
                        or str(payload.get("effect_id") or ""),
                    }
                )
            report["events"] = tuple(events)
            report["truncated"] = report["dropped"] > 0
        finally:
            conn.close()
    except Exception as exc:
        report["query_error"] = f"{type(exc).__name__}: {exc}"
    return report


def background_effect_persistence_status() -> dict[str, Any]:
    """The write-side account: how many durable writes were refused by a
    corrupt or unavailable store, and the last refusal. Observable by
    design — a persistence failure never blocks or widens a transport, so
    this is where it is seen."""
    return _background_effect_journal().persistence_status()


def _freeze_decision_context(value: Any) -> FrozenMapping:
    """The turn's decision context, PROJECTED once at policy freeze.

    R2b1 made this a deep copy, because a shallow one shared every nested object
    with the caller's live context and a later mutation rewrote the turn's
    policy truth mid-flight. That isolation is kept, and is now structural: the
    projection is immutable, so no gate can write through it into what the next
    gate reads.

    What is gone is `copy.deepcopy`, which was a copier standing in for a
    projector. Asked to copy a `threading.Lock`, an open client or a generator —
    ordinary cargo in a working turn context — it raised; the raise was recorded
    as `policy_error`; and every gate reads a non-empty `policy_error` as a
    denial. So one unrelated runtime handle revoked the turn's whole network
    authority. Reproduced live on 0.5.0 as `cannot pickle '_thread.lock'
    object`, and with it the settings search test and every chat request for
    current information.

    `core.policy_projection` separates the two cases that must not share a fate:
    a POLICY INPUT that cannot be carried still fails the policy closed (see
    `from_source_context`), while CARGO becomes an opaque type marker — no live
    reference retained, no value invented.
    """
    return project_context(value)


@dataclass(frozen=True)
class TurnPolicy:
    """The ONE scoped policy input every gateway consult shares (M5 task 3).

    Frozen, derived ONCE per turn from the turn's server-side context — never
    assembled ad hoc at each effect site (the overlapping-composition weakness
    this milestone removes). R2 gave it an identity (`policy_id`) so a receipt
    can name the policy its decision was made under, and a snapshot of the
    decision context (`decision_context`) so the consult a door makes at minute
    two is made against the same inputs as the consult at minute zero.

    Fields are honest about their derivation: `principal` is the request's trust
    principal; `mode`/`mode_revision`/`mode_policy_active` are the mode state as
    it stood when the turn began; `workspace_root` the turn's binding;
    `network_allowed`/`spend_allowed` carry the turn's explicit restrictions
    (empty string = undeclared, the M2 policy-slot law); `autonomy_mode` the
    turn's effective autonomy; `approval_required` defaults to the gateway's own
    decision. `policy_error` is non-empty when construction could not read the
    inputs it needs — every gate treats that as a denial, never as a default.
    REMOVAL CONDITION for `from_source_context`: when callers hold a typed
    TurnRequest they pass its fields directly and this constructor shrinks to
    the egress adapters.
    """

    principal: str = ""
    mode: str = ""
    workspace_root: str = ""
    network_allowed: str = ""
    spend_allowed: str = ""
    approval_required: str = ""
    side_effect_class: str = ""
    policy_id: str = ""
    mode_revision: int = 0
    mode_policy_active: bool = False
    autonomy_mode: str = ""
    policy_error: str = ""
    decision_context: FrozenMapping = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # R2b1: the snapshot is FROZEN at construction, so later caller mutation
        # of the live context cannot rewrite this turn's policy truth. It is a
        # projection rather than a deep copy — see `_freeze_decision_context`.
        object.__setattr__(
            self, "decision_context", _freeze_decision_context(self.decision_context)
        )

    def decision_context_copy(self) -> dict[str, Any]:
        """A fresh mutable rendering for consults — a gate never holds the
        frozen original, so one gate's mutation cannot rewrite what the next
        gate reads. `decide_tool_call` takes a `source_context` it may normalize
        into, so this has to be a real `dict`, not the frozen projection."""
        return render_mutable(self.decision_context)

    @classmethod
    def from_source_context(cls, source_context: dict[str, Any] | None) -> TurnPolicy:
        context = dict(source_context) if isinstance(source_context, dict) else {}
        policy_id = "tp:" + secrets.token_hex(8)
        errors: list[str] = []
        try:
            frozen_context = _freeze_decision_context(context)
            # FAIL CLOSED, NARROWED TO WHAT IT PROTECTS. A gate consulting an
            # opaque marker would be deciding on a value it cannot see, so an
            # unreadable POLICY INPUT still denies. Cargo does not: the deepcopy
            # this replaces could not tell the two apart, and treated every
            # runtime handle in the turn context as a revoked permission.
            unreadable = unreadable_policy_inputs(frozen_context)
            if unreadable:
                errors.append(
                    "decision_context policy inputs could not be projected: "
                    + ", ".join(unreadable)
                )
        except Exception as exc:
            frozen_context = FrozenMapping({})
            errors.append(f"decision_context freeze: {type(exc).__name__}: {exc}")
        try:
            from core.request_trust import request_is_owner_local

            principal = "owner_local" if request_is_owner_local(context) else "remote_untrusted"
        except Exception as exc:  # the principal is required input, not a nicety
            principal = ""
            errors.append(f"principal: {type(exc).__name__}: {exc}")
        mode = ""
        revision = 0
        policy_active = False
        try:
            from core.mode_permission_policy import active_mode_state, mode_policy_is_active

            state = active_mode_state(context)
            mode = str(state.get("mode") or "")
            revision = int(state.get("revision") or 0)
            policy_active = bool(mode_policy_is_active(context))
        except Exception as exc:
            errors.append(f"mode: {type(exc).__name__}: {exc}")
        autonomy = ""
        try:
            from core.execution_gate import effective_autonomy_mode

            autonomy = str(effective_autonomy_mode() or "")
        except Exception as exc:
            errors.append(f"autonomy: {type(exc).__name__}: {exc}")
        return cls(
            principal=principal,
            mode=mode,
            workspace_root=str(context.get("workspace_root") or ""),
            network_allowed=str(context.get("allow_remote_fetch") or ""),
            spend_allowed="",
            side_effect_class="",
            policy_id=policy_id,
            mode_revision=revision,
            mode_policy_active=policy_active,
            autonomy_mode=autonomy,
            policy_error="; ".join(errors),
            decision_context=frozen_context,
        )


class EffectLedger:
    """One turn's effect account, owned by reference so worker tasks reach it.

    The same ownership argument the fetch ledger makes: a list in a ContextVar
    cannot be reported into from a pool worker, because a worker that inherits
    the turn's context and rebinds the variable rebinds only its own copy.
    Holding a mutable object means every context inheriting the binding — parent
    and workers alike — records into the SAME ledger, while two concurrent turns
    hold two ledgers and can never see each other's effects.

    `parent` is the binding this scope displaced. Closing restores it, so a
    nested scope can never close or drain the turn that contains it.
    """

    __slots__ = (
        "_dropped",
        "_effects",
        "_effects_dropped",
        "_effects_truncated",
        "_entries",
        "_lock",
        "_memo",
        "_request_id",
        "_seq",
        "_truncated",
        "_turn_id",
        "durable",
        "ledger_id",
        "parent",
        "policy",
        "scope_instance",
        "scope_name",
        "source_context",
    )

    def __init__(
        self,
        *,
        source_context: dict[str, Any] | None = None,
        parent: EffectLedger | None = None,
        scope_name: str = "",
        durable: bool = False,
        scope_instance: str = "",
    ) -> None:
        self._entries: list[dict[str, Any]] = []
        #: RLock, not Lock: a lifecycle transition holds it across its guard,
        #: registry write and append (one atomic check-and-record), and the
        #: append path re-enters the same lock.
        self._lock = threading.RLock()
        self._truncated = False
        self._dropped = 0
        self._seq = 0
        self._turn_id = ""
        self._request_id = ""
        self._memo: dict[str, tuple[str, str]] = {}
        #: R2b2b — the effect REGISTRY: one live record per LOGICAL effect this
        #: ledger owns (attempt count, current-attempt terminal), kept so the
        #: ledger can answer how an effect ended even under receipt truncation
        #: and so terminal writes are idempotent. Bounded by the same ceiling
        #: as the receipt account; eviction is FIFO and flagged.
        self._effects: dict[str, dict[str, Any]] = {}
        self._effects_truncated = False
        self._effects_dropped = 0
        self.ledger_id = secrets.token_hex(4)
        self.parent = parent
        #: R2b1: the ledger — not the caller — owns the identity every receipt
        #: it records carries. Empty for a turn scope (identity comes from the
        #: turn contract); set for a NAMED non-turn scope, whose receipts name
        #: the scope and no turn.
        self.scope_name = str(scope_name or "")
        #: R2b2c: durable background scopes publish every lifecycle entry to
        #: the runtime-event store as it occurs (their `run_forever` scopes
        #: never close to hand anything over), stamped with this per-open
        #: daemon instance so concurrent workers of one scope never blur.
        self.durable = bool(durable and self.scope_name)
        self.scope_instance = str(scope_instance or "")
        #: The LIVE turn context, not a copy: the turn's identity is minted after
        #: the HTTP door opens this scope, and a snapshot taken here would never
        #: see it. Read-only from this side.
        self.source_context = source_context if isinstance(source_context, dict) else None
        self.policy = TurnPolicy.from_source_context(source_context)

    # -- identity ---------------------------------------------------------
    def _resolve_identity(self) -> tuple[str, str]:
        """This turn's (turn_id, request_id), resolved from the live context.

        Monotonic: once a field is known it is frozen, so every receipt of the
        turn agrees, and a later re-read cannot re-attribute earlier effects.
        """
        turn_id = self._turn_id
        request_id = self._request_id
        if turn_id and request_id:
            return turn_id, request_id
        context = self.source_context if isinstance(self.source_context, dict) else {}
        try:
            from core.turn_contract import TURN_REQUEST_KEY

            request = context.get(TURN_REQUEST_KEY)
        except Exception:
            request = None
        if request is not None:
            turn_id = turn_id or str(getattr(request, "turn_id", "") or "")
            request_id = request_id or str(getattr(request, "request_id", "") or "")
        turn_id = turn_id or str(context.get("turn_id") or "")
        request_id = request_id or str(context.get("request_id") or "")
        if not request_id:
            try:
                from core.semantic.semantic_admissions import current_request_id

                request_id = str(current_request_id() or "")
            except Exception:
                request_id = ""
        self._turn_id = turn_id
        self._request_id = request_id
        return turn_id, request_id

    # -- receipts ---------------------------------------------------------
    def record(self, receipt: EffectReceipt) -> None:
        """Append one receipt under THIS ledger's canonical identity.

        R2b1: the ledger — not the caller — owns request_id, turn_id, policy_id
        and the effect_id mint. A caller-stated identity is never trusted: any
        stated value that differs from the canonical one is replaced and the
        replacement is RECORDED on the receipt (`identity_note`), so an
        attempted forgery is a visible fact, never a silent mis-attribution.
        """
        self._append_entry(receipt.to_dict())

    def _append_entry(self, entry: dict[str, Any], *, keep_effect_id: str = "") -> str:
        """The one append path: canonicalize identity, mint or continue the
        effect id, apply the ceiling. R2b2b: `keep_effect_id` is how a
        REGISTERED effect's lifecycle continues under ONE logical id — the
        ledger verifies it against its own registry before keeping it, so a
        foreign or stale id is still replaced, never trusted.

        Returns the effect id the entry carries. The id is minted (and the
        dropped count raised) even when the ceiling drops the DETAIL, so the
        logical effect still exists for the registry and its outcome stays
        answerable.

        R2b2c: a DURABLE background scope additionally publishes the entry to
        the runtime-event store as it occurs — including one the memory
        ceiling dropped, because for a `run_forever` scope the durable journal
        is the only account that outlives it. The publish happens OUTSIDE the
        ledger lock and can never fail the effect (the journal counts its own
        failures).
        """
        turn_id, request_id = self._resolve_identity()
        canonical = {
            "turn_id": turn_id,
            "request_id": request_id,
            "policy_id": self.policy.policy_id,
        }
        with self._lock:
            self._seq += 1
            minted = f"eff:{self.ledger_id}-{self._seq:04d}"
            if keep_effect_id and keep_effect_id in self._effects:
                minted = keep_effect_id
            prepared = dict(entry)
            conflicts: list[str] = []
            for field, value in canonical.items():
                stated = str(prepared.get(field) or "")
                if stated and stated != value:
                    conflicts.append(f"{field}={stated!r} replaced by ledger")
                prepared[field] = value
            stated_effect = str(prepared.get("effect_id") or "")
            if stated_effect and stated_effect != minted:
                conflicts.append(f"effect_id={stated_effect!r} replaced by ledger mint")
            prepared["effect_id"] = minted
            prepared["scope_name"] = self.scope_name
            prepared["recorded_at"] = prepared.get("recorded_at") or _utcnow()
            prepared["attempt"] = int(prepared.get("attempt") or 0)
            if conflicts:
                note = str(prepared.get("identity_note") or "")
                prepared["identity_note"] = (note + "; " if note else "") + (
                    "identity replaced by ledger: " + "; ".join(conflicts)
                )
            if len(self._entries) >= MAX_RECEIPTS_PER_TURN:
                self._truncated = True
                self._dropped += 1
                kept = False
            else:
                self._entries.append(prepared)
                kept = True
        if self.durable:
            _background_effect_journal().record(
                prepared,
                scope_name=self.scope_name,
                scope_instance=self.scope_instance,
                dropped_from_ledger=not kept,
            )
        return minted

    def entries(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._entries)

    def finalized(self) -> list[dict[str, Any]]:
        """This turn's receipts, with any identity that was still unminted when
        an early effect ran filled in from the turn's settled identity."""
        turn_id, request_id = self._resolve_identity()
        with self._lock:
            for entry in self._entries:
                entry["turn_id"] = entry["turn_id"] or turn_id
                entry["request_id"] = entry["request_id"] or request_id
            return [dict(item) for item in self._entries]

    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    # -- the effect registry (R2b2b) --------------------------------------
    def _register_effect(self, effect_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Register one logical effect, bounded by the receipt ceiling (FIFO
        eviction, flagged) so a retry storm cannot grow the registry without
        bound either."""
        with self._lock:
            record = self._effects.get(effect_id)
            if record is not None:
                return record
            while len(self._effects) >= MAX_RECEIPTS_PER_TURN:
                oldest = next(iter(self._effects))
                self._effects.pop(oldest)
                self._effects_truncated = True
                self._effects_dropped += 1
            record = {
                "effect_class": str(entry.get("effect_class") or ""),
                "decision": str(entry.get("decision") or ""),
                "host": str(entry.get("host") or ""),
                "mode": str(entry.get("mode") or ""),
                # Carried onto the registry, not only the receipt: the outcome
                # account survives receipt truncation, and an effect whose
                # provider is only on a dropped receipt is an effect nobody can
                # attribute.
                "provider_id": str(entry.get("provider_id") or ""),
                "keyed_or_keyless": str(entry.get("keyed_or_keyless") or ""),
                "attempts": 0,
                "terminal_attempt": None,
                "terminal": "",
                "recorded_at": str(entry.get("recorded_at") or ""),
            }
            self._effects[effect_id] = record
            return record

    def _effect_record(self, effect_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._effects.get(effect_id)
            return dict(record) if record is not None else None

    def _terminal_for(self, effect_id: str, attempt: int) -> str:
        """The terminal already recorded for this attempt, if any — the
        idempotence read: a duplicate terminal write reports the FIRST
        outcome and appends nothing."""
        with self._lock:
            record = self._effects.get(effect_id)
            if not record or record.get("terminal_attempt") != attempt:
                return ""
            return str(record.get("terminal") or "")

    def _terminal_transition(
        self, effect_id: str, attempt: int, lifecycle: str, entry: dict[str, Any]
    ) -> str:
        """ONE atomic terminal transition: idempotence guard → registry write
        → append. Returns the EFFECTIVE terminal — the first terminal this
        logical effect's attempt (or any newer attempt) recorded.

        Guard law: a terminal binds the attempt it landed on. A duplicate for
        the SAME attempt reports the first outcome; a STALE transition (an
        older attempt than the one already terminalized) is refused the same
        way; only a NEWER attempt's terminal supersedes — which is what a
        retry's outcome is.
        """
        with self._lock:
            record = self._effects.get(effect_id)
            if record is None:
                raise EffectStateError(
                    f"effect {effect_id!r} is not registered on this ledger; "
                    "a terminal outcome cannot be recorded for an effect no "
                    "ledger owns"
                )
            current_attempt = record.get("terminal_attempt")
            if current_attempt is not None and attempt <= int(current_attempt):
                return str(record.get("terminal") or lifecycle)
            record["terminal_attempt"] = attempt
            record["terminal"] = lifecycle
            self._append_entry(entry, keep_effect_id=effect_id)
            return lifecycle

    def _begin_attempt(self, effect_id: str) -> int:
        with self._lock:
            record = self._effects.get(effect_id)
            if record is None:
                raise EffectStateError(
                    f"effect {effect_id!r} is not registered on this ledger; "
                    "an attempt cannot begin for an effect no ledger owns"
                )
            record["attempts"] = int(record.get("attempts") or 0) + 1
            return record["attempts"]

    # -- the budget gate (P1) ------------------------------------------------
    def _budget_identity(self) -> dict[str, str]:
        """The identity budget scopes bind to, resolved by the OWNING ledger:
        turn from the canonical turn identity, session from the turn request,
        project from the turn's workspace binding. A named (non-turn) scope
        has no turn/session — those rules honestly do not bind it; window
        rules still do."""
        turn_id, request_id = self._resolve_identity()
        session_id = ""
        context = self.source_context if isinstance(self.source_context, dict) else {}
        request = None
        try:
            from core.turn_contract import TURN_REQUEST_KEY

            request = context.get(TURN_REQUEST_KEY)
        except Exception:
            request = None
        if request is not None:
            session_id = str(getattr(request, "session_id", "") or "")
        session_id = session_id or str(context.get("session_id") or "")
        project_key = str(getattr(self.policy, "workspace_root", "") or "")
        project_key = project_key or str(context.get("workspace_root") or "")
        # P3: the request, task and agent a budget or a liability binds to —
        # read from the same owning context, never from the caller's word
        extras = getattr(request, "extras", None) if request is not None else None
        extras = extras if isinstance(extras, dict) else {}
        return {
            "turn_id": turn_id,
            "session_id": session_id,
            "project_key": project_key or "default",
            "request_id": str(request_id or ""),
            "task_id": str(context.get("task_id") or extras.get("task_id") or ""),
            "agent_id": str(context.get("agent_id") or extras.get("agent_id") or ""),
        }

    def _reserve_budget_units(self, entry: dict[str, Any], *, retry_of: str = "") -> str:
        """Reserve this effect's units at the budget authority (the P1 gate).

        Returns the reservation id, or "" for an unbudgeted class (no mapped
        budget class, or a retry whose effect already holds its reservation).
        A budget refusal becomes a DENIED receipt on THIS ledger and raises
        the typed `EffectBudgetRefusedError` — the door's caller receives the
        typed code, the turn's account receives the denial fact.
        """
        from core.effect_budget import (
            EffectBudgetRefusedError,
            effect_reservation_states,
            gateway_budget_class,
            reserve_effect_units,
        )

        budget_class = gateway_budget_class(str(entry.get("effect_class") or ""))
        if not budget_class:
            return ""
        if retry_of:
            states = effect_reservation_states(retry_of)
            if any(state in ("reserved", "consumed") for state in states):
                return ""  # the logical effect already holds its unit
        identity = self._budget_identity()
        try:
            receipt = reserve_effect_units(
                budget_class,
                owner_ref=self.ledger_id,
                turn_id=identity["turn_id"],
                session_id=identity["session_id"],
                project_key=identity["project_key"],
                request_id=identity["request_id"],
                task_id=identity["task_id"],
                agent_id=identity["agent_id"],
                provider_id=str(entry.get("provider_id") or ""),
            )
        except EffectBudgetRefusedError as refusal:
            denied = dict(entry)
            denied["decision"] = DECISION_DENIED
            denied["lifecycle"] = LIFECYCLE_DENIED
            denied["reason"] = f"{refusal.code}: {refusal.detail}"
            denied["decided_by"] = "core.effect_budget"
            if refusal.rule:
                denied["reason"] += f" [rule {refusal.rule}]"
            self._append_entry(denied)
            raise
        return receipt.reservation_id if not receipt.unbudgeted else ""

    def _reserve_money_at_gate(
        self, entry: dict[str, Any], money: Any, *, unit_reservation_id: str
    ) -> Any:
        """P3 — reserve this effect's MONETARY maximum at the money law of
        the one budget authority, after its unit. Identity comes from THIS
        ledger (a conflicting stated identity is refused, never replaced). A
        refusal returns the unit and is recorded here as a denial carrying
        the money code; a retry never rides a claimed, unknown or settled
        payment."""
        from dataclasses import replace as _replace

        from core import effect_budget_money as ebm
        from core.effect_budget import EffectBudgetRefusedError, release_reservation

        try:
            owned = dict(self._budget_identity())
            if owned.get("project_key") == "default":
                owned["project_key"] = ""
            owned["provider_id"] = str(entry.get("provider_id") or "")
            request = ebm.bind_owned_identity(money, owned=owned, owner_ref=self.ledger_id)
            if not request.identity.project_key:
                request = _replace(
                    request, identity=_replace(request.identity, project_key="default")
                )
            money_receipt = ebm.reserve_liability(request)
            if money_receipt.state != ebm.LIABILITY_RESERVED:
                raise EffectBudgetRefusedError(
                    ebm.MONEY_CLAIM_CONFLICT,
                    f"operation {money_receipt.operation_id} is already "
                    f"{money_receipt.state}; a retry never rides a claimed, unknown "
                    "or settled payment",
                )
            return money_receipt
        except Exception as refusal:
            if unit_reservation_id:
                with contextlib.suppress(Exception):
                    release_reservation(
                        unit_reservation_id,
                        reason="money authority refused the effect at the gate",
                    )
            denied = dict(entry)
            denied["decision"] = DECISION_DENIED
            denied["lifecycle"] = LIFECYCLE_DENIED
            code = str(getattr(refusal, "code", "") or type(refusal).__name__)
            denied["reason"] = f"{code}: {getattr(refusal, 'detail', '')}"[:300]
            denied["decided_by"] = "core.effect_budget_money"
            self._append_entry(denied)
            raise

    def open_effect(
        self, receipt: EffectReceipt, *, retry_of: str = "", money: Any = None
    ) -> EffectLifecycle:
        """Register one LOGICAL effect and return its typed lifecycle authority.

        The receipt is the effect's policy-decision record (authorized, or
        denied — a denial is a registered effect that never begins an attempt).
        `retry_of` naming a REGISTERED effect of THIS ledger continues that
        effect: the standing authorization is not re-appended (the door still
        re-consults the frozen ceiling), the next attempt gets a new attempt
        number under the SAME logical id. A `retry_of` this ledger does not
        know is refused the linkage — a fresh effect is minted and the refused
        link is stated on the receipt, never silently followed.

        P1 — SESSION EFFECT BUDGETS: an AUTHORIZED effect of a budgeted class
        reserves its units at `core.effect_budget` HERE, before this ledger
        authorizes it — this method is the one real gate every effect class
        converges on. A budget refusal rewrites the decision to a DENIED
        receipt on this ledger (a denial is a first-class fact) and raises the
        typed `EffectBudgetRefusedError` to the door. A retry of an effect
        that already holds a reservation does not reserve a second unit.

        P3 — MONEY: `money` (a `core.effect_budget_money.LiabilityRequest`)
        reserves the effect's full monetary maximum after its unit, under the
        ledger's own identity. The unit law's retry rule does NOT carry over:
        a unit is an attempt count, money is a payment, and a retry of a
        payment that may have gone out is refused until evidence settles it or
        proves it never left.
        """
        entry = receipt.to_dict()
        with self._lock:
            known_retry = bool(retry_of) and retry_of in self._effects
        reservation_needed = str(entry.get("decision") or "") == DECISION_ALLOWED
        reservation_id = ""
        money_receipt = None
        if reservation_needed:
            reservation_id = self._reserve_budget_units(
                entry, retry_of=retry_of if known_retry else ""
            )
            if money is not None:
                money_receipt = self._reserve_money_at_gate(
                    entry, money, unit_reservation_id=reservation_id
                )
        if known_retry:
            effect_id = retry_of
            if receipt.decision == DECISION_DENIED:
                # the retry was refused before any socket; the refusal stays
                # visible under the SAME logical id (the effect's recorded
                # terminal — its last real attempt's — is not rewritten)
                self._append_entry(entry, keep_effect_id=effect_id)
        else:
            if retry_of:
                entry["identity_note"] = "; ".join(
                    part
                    for part in (
                        str(entry.get("identity_note") or ""),
                        f"retry_of={retry_of!r} is not an effect of this "
                        "ledger; a new logical effect was minted",
                    )
                    if part
                )
            effect_id = self._append_entry(entry)
        if reservation_id:
            from core.effect_budget import bind_reservation_effect

            bind_reservation_effect(reservation_id, effect_id)
        if money_receipt is not None:
            from core.effect_budget_money import bind_liability_effect

            bind_liability_effect(money_receipt.liability_id, effect_id)
        self._register_effect(effect_id, entry)
        return EffectLifecycle(
            self,
            effect_id,
            effect_class=str(entry.get("effect_class") or receipt.effect_class),
            decision=str(entry.get("decision") or receipt.decision),
            host=str(entry.get("host") or receipt.host),
            mode=str(entry.get("mode") or receipt.mode),
            decided_by=str(entry.get("decided_by") or receipt.decided_by),
            money_liability_id=money_receipt.liability_id if money_receipt is not None else "",
        )

    def effect_outcomes(self) -> tuple[dict[str, Any], ...]:
        """The ledger's answer to the three questions: what was attempted,
        whether transport ran, and how it ended — one entry per LOGICAL effect,
        derived from the registry (complete even when receipt detail truncated).
        """
        turn_id, request_id = self._resolve_identity()
        with self._lock:
            records = list(self._effects.items())
        outcomes: list[dict[str, Any]] = []
        for effect_id, record in records:
            attempts = int(record.get("attempts") or 0)
            terminal_attempt = record.get("terminal_attempt")
            terminal = str(record.get("terminal") or "")
            unresolved = False
            if terminal_attempt is not None and terminal_attempt == attempts:
                lifecycle = terminal or LIFECYCLE_UNKNOWN
            elif attempts > 0:
                # an attempt began and has not ended — the honest state is
                # `started`, flagged, never rounded up to any terminal
                lifecycle = LIFECYCLE_STARTED
                unresolved = True
            elif terminal_attempt is not None:
                lifecycle = terminal or LIFECYCLE_UNKNOWN
            else:
                lifecycle = _DECISION_LIFECYCLE.get(
                    str(record.get("decision") or ""), LIFECYCLE_UNKNOWN
                )
            outcomes.append(
                {
                    "effect_id": effect_id,
                    "effect_class": str(record.get("effect_class") or ""),
                    "decision": str(record.get("decision") or ""),
                    "host": str(record.get("host") or ""),
                    "mode": str(record.get("mode") or ""),
                    "provider_id": str(record.get("provider_id") or ""),
                    "keyed_or_keyless": str(record.get("keyed_or_keyless") or ""),
                    "lifecycle": lifecycle,
                    "unresolved": unresolved,
                    "transport_ran": attempts > 0,
                    "attempts": attempts,
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "scope_name": self.scope_name,
                    "recorded_at": str(record.get("recorded_at") or ""),
                }
            )
        return tuple(outcomes)

    def effects_truncated(self) -> bool:
        with self._lock:
            return self._effects_truncated

    # -- the turn's frozen gateway verdicts --------------------------------
    def frozen_decision(self, effect_class: str) -> tuple[str, str] | None:
        with self._lock:
            return self._memo.get(effect_class)

    def freeze_decision(self, effect_class: str, verdict: tuple[str, str]) -> None:
        with self._lock:
            self._memo.setdefault(effect_class, verdict)


class EffectLifecycle:
    """ONE logical effect's durable lifecycle authority (R2b2b).

    ``authorized → started → exactly one of succeeded | failed | cancelled``

    Handed out by `EffectLedger.open_effect` — never constructed ad hoc at a
    call site, so no caller hand-writes a lifecycle record. The handle holds
    the OWNING LEDGER by reference, which is the whole cross-thread contract:
    identity (request_id, turn_id, policy_id, scope) is read from the ledger
    at append time, so a worker thread holding only the handle records under
    the turn's canonical identity even with no ambient context binding.

    The handle is a STATELESS view of the ledger's registry except for its
    attempt cursor: `begin_attempt` advances it and appends `started`;
    terminal methods append at most one terminal per attempt (idempotent —
    a duplicate reports the first outcome) and return the EFFECTIVE terminal.
    """

    __slots__ = (
        "_attempt",
        "_decided_by",
        "_decision",
        "_effect_class",
        "_host",
        "_ledger",
        "_mode",
        "_money_claim",
        "_money_liability_id",
        "effect_id",
    )

    def __init__(
        self,
        ledger: EffectLedger,
        effect_id: str,
        *,
        effect_class: str,
        decision: str,
        host: str = "",
        mode: str = "",
        decided_by: str = "",
        money_liability_id: str = "",
    ) -> None:
        self._ledger = ledger
        self.effect_id = str(effect_id or "")
        self._effect_class = str(effect_class or "")
        self._decision = str(decision or "")
        self._host = str(host or "")
        self._mode = str(mode or "")
        self._decided_by = str(decided_by or "")
        self._attempt = 0
        #: P3 — the monetary liability this effect reserved at the gate, and
        #: the dispatch claim its current attempt holds (None before a claim)
        self._money_liability_id = str(money_liability_id or "")
        self._money_claim: Any = None

    # -- state ------------------------------------------------------------
    @property
    def effect_class(self) -> str:
        return self._effect_class

    @property
    def decision(self) -> str:
        return self._decision

    @property
    def attempts(self) -> int:
        record = self._ledger._effect_record(self.effect_id)
        return int(record.get("attempts") or 0) if record else 0

    @property
    def transport_ran(self) -> bool:
        return self.attempts > 0

    @property
    def money_liability_id(self) -> str:
        return self._money_liability_id

    def outcome(self) -> dict[str, Any] | None:
        for entry in self._ledger.effect_outcomes():
            if entry.get("effect_id") == self.effect_id:
                return entry
        return None

    # -- transitions -------------------------------------------------------
    def _consume_budget_for_attempt(self) -> None:
        """Consume this effect's budget reservation for the attempt about to
        run. Unbudgeted class → no-op; a budgeted class whose reservation is
        gone refuses the attempt (fail closed at the one real gate)."""
        from core.effect_budget import (
            consume_effect_reservations,
            gateway_budget_class,
        )

        budget_class = gateway_budget_class(self._effect_class)
        if not budget_class:
            return
        consume_effect_reservations(self.effect_id, budget_class=budget_class)

    def _entry(self, lifecycle: str, reason: str, attempt: int) -> dict[str, Any]:
        return EffectReceipt(
            effect_class=self._effect_class,
            decision=self._decision,
            lifecycle=lifecycle,
            reason=reason,
            host=self._host,
            mode=self._mode,
            decided_by=self._decided_by,
            attempt=attempt,
        ).to_dict()

    def begin_attempt(self) -> int:
        """Append `started` for a NEW attempt and return its number. Called by
        the transport seam immediately BEFORE the real transport call — the
        socket must be able to see `started` already on the record.

        P1 — the budget CONSUMPTION happens here, before the `started` entry:
        the unit is spent the moment a transport attempt actually begins. A
        released reservation (an authorization rolled back before it ran)
        refuses the attempt with the typed budget code — a returned unit is
        never spent after the fact.
        """
        if self._decision == DECISION_DENIED:
            raise EffectStateError(
                "a denied effect cannot begin a transport attempt — "
                "`authorized` never implies an attempt may run"
            )
        claim = self._claim_money_for_attempt()
        try:
            self._consume_budget_for_attempt()
        except BaseException:
            # nothing left this process: the claimant's own proof
            self._money_unsent_locally(claim, why="unit-refused")
            raise
        self._attempt = self._ledger._begin_attempt(self.effect_id)
        self._ledger._append_entry(
            self._entry(
                LIFECYCLE_STARTED,
                f"transport attempt {self._attempt}",
                self._attempt,
            ),
            keep_effect_id=self.effect_id,
        )
        return self._attempt

    def record_terminal(
        self, lifecycle: str, *, reason: str = "", status: Any = None
    ) -> str:
        """Record the attempt's ONE terminal outcome; idempotent per attempt.

        `status` (an int HTTP status) is folded into the reason as a safe
        `http_NNN` token. Returns the EFFECTIVE terminal — the first terminal
        this effect recorded for this attempt — so a duplicate write is
        visible to its caller without becoming a second entry.
        """
        if lifecycle not in LIFECYCLE_TERMINALS:
            raise ValueError(f"not a terminal lifecycle: {lifecycle!r}")
        if not reason and isinstance(status, int):
            reason = f"http_{status}"
        entry = self._entry(lifecycle, reason or lifecycle, self._attempt)
        return self._ledger._terminal_transition(
            self.effect_id, self._attempt, lifecycle, entry
        )

    def succeed(self, *, status: Any = None, reason: str = "") -> str:
        """The transport call returned. `status` when int becomes `http_NNN`."""
        if not reason and not isinstance(status, int):
            reason = "transport returned"
        terminal = self.record_terminal(LIFECYCLE_SUCCEEDED, reason=reason, status=status)
        self._money_after_attempt(dispatched=True, reason=reason)
        return terminal

    def fail(self, *, exc: BaseException | None = None, reason: str = "") -> str:
        """The transport call raised (anything outside the CancelledError
        family). The reason is a type-derived token — never exception text."""
        if not reason:
            reason = safe_transport_failure_reason(exc)
        terminal = self.record_terminal(LIFECYCLE_FAILED, reason=reason)
        self._money_after_attempt(dispatched=False, reason=reason)
        return terminal

    def cancel(self, *, reason: str = "") -> str:
        """The attempt was cancelled (the CancelledError family, or an explicit
        cancellation by the owning logic) — a terminal distinct from failure.

        A cancellation also RETURNS the unit. ``release_effect_reservations``
        touches only rows still in ``reserved``, so a cancel after
        ``begin_attempt`` (which consumed the unit) releases nothing and a spend
        is never refunded — but a cancel by logic that authorized and then chose
        not to run gives the unit back instead of stranding it.

        Without this, a lane that opens an effect, cancels it and re-opens later
        (the wallet's external-signer lanes do exactly that while the operator's
        wallet is signing) charged the budget twice per payment and left one
        reservation live forever; live reservations are never pruned.
        """
        terminal = self.record_terminal(LIFECYCLE_CANCELLED, reason=reason or "cancelled")
        with contextlib.suppress(Exception):
            from core.effect_budget import release_effect_reservations

            release_effect_reservations(
                self.effect_id, reason=reason or "cancelled-before-attempt"
            )
        if self._money_liability_id:
            # P3: money cancelled before its claim returns; money cancelled
            # after its claim may already have left — UNKNOWN, maximum held
            with contextlib.suppress(Exception):
                from core import effect_budget_money as ebm

                if self._money_claim is None:
                    ebm.release_unclaimed(
                        self._money_liability_id, reason=reason or "cancelled-before-attempt"
                    )
                else:
                    ebm.record_unknown(
                        self._money_liability_id,
                        self._money_claim.claim_token,
                        reason=reason or "cancelled after the dispatch claim",
                    )
        return terminal

    # -- P3: the monetary half of the attempt --------------------------------
    def _claim_money_for_attempt(self) -> Any:
        """Persist monetary dispatch ownership BEFORE the attempt. A proven-
        unsent earlier attempt is re-reserved (every check re-run) before its
        new claim; anything already claimed refuses — a second attempt at a
        payment that may have gone out is a second payment."""
        if not self._money_liability_id:
            return None
        from core import effect_budget_money as ebm
        from core.effect_budget import EffectBudgetRefusedError

        executor = f"effect_gateway:{self._effect_class}:{self.effect_id}"
        try:
            claim = ebm.claim_dispatch(self._money_liability_id, executor=executor)
        except EffectBudgetRefusedError as refusal:
            if refusal.code != ebm.MONEY_STATE_ERROR:
                raise
            current = ebm.liability(self._money_liability_id) or {}
            if current.get("state") != ebm.LIABILITY_UNSENT:
                raise
            ebm.retry_unsent(self._money_liability_id)
            claim = ebm.claim_dispatch(self._money_liability_id, executor=executor)
        self._money_claim = claim
        return claim

    def _money_unsent_locally(self, claim: Any, *, why: str) -> None:
        if claim is None:
            return
        with contextlib.suppress(Exception):
            from core import effect_budget_money as ebm

            ebm.record_unsent(
                self._money_liability_id,
                claim.claim_token,
                evidence=ebm.UnsentEvidence(
                    proof_kind=ebm.UNSENT_LOCAL_REFUSAL,
                    evidence_id=f"{self.effect_id}:{why}",
                    source="mechanical",
                ),
            )

    def _money_after_attempt(self, *, dispatched: bool, reason: str) -> None:
        """succeed: the request left — pending, maximum held until settled.
        fail after a claim: UNKNOWN unless the door already proved it never
        left. Store trouble here is never a release: the claim stays, and
        dead-instance reconciliation turns an orphaned claim into UNKNOWN."""
        claim = self._money_claim
        if claim is None or not self._money_liability_id:
            return
        with contextlib.suppress(Exception):
            from core import effect_budget_money as ebm

            if dispatched:
                ebm.record_dispatched(
                    self._money_liability_id,
                    claim.claim_token,
                    evidence_id=f"{self.effect_id}:attempt-{self._attempt}",
                )
            else:
                ebm.record_unknown(
                    self._money_liability_id,
                    claim.claim_token,
                    reason=str(reason or "attempt failed after the dispatch claim")[:200],
                )

    def _require_money(self) -> Any:
        from core import effect_budget_money as ebm
        from core.effect_budget import EffectBudgetRefusedError

        if not self._money_liability_id:
            raise EffectBudgetRefusedError(
                ebm.MONEY_STATE_ERROR, "this effect carries no monetary liability"
            )
        return ebm

    def money_unsent(self, *, proof_kind: str, evidence_id: str, source: str = "mechanical") -> str:
        """Positive proof this attempt never left (no connection was made, a
        validated rejection before execution): the maximum is released, and a
        later `begin_attempt` re-reserves it with every check re-run."""
        ebm = self._require_money()
        claim = self._money_claim
        return ebm.record_unsent(
            self._money_liability_id,
            claim.claim_token if claim is not None else "",
            evidence=ebm.UnsentEvidence(proof_kind=proof_kind, evidence_id=evidence_id, source=source),
        )

    def money_unknown(self, *, reason: str) -> str:
        ebm = self._require_money()
        claim = self._money_claim
        return ebm.record_unknown(
            self._money_liability_id, claim.claim_token if claim is not None else "", reason=reason
        )

    def settle_money(self, evidence: Any) -> dict[str, Any]:
        ebm = self._require_money()
        return ebm.settle_liability(self._money_liability_id, evidence)


_EFFECT_LEDGER: ContextVar[EffectLedger | None] = ContextVar(
    "vool_effect_receipt_ledger", default=None
)


def open_effect_receipt_scope(source_context: dict[str, Any] | None = None) -> EffectLedger:
    """Open this turn's effect ledger and freeze the turn's policy onto it.

    Called by the fetch turn scope — one ledger per turn, inherited by worker
    tasks through `copy_context`. A nested open displaces the binding and keeps
    the parent, which closing restores.
    """
    ledger = EffectLedger(source_context=source_context, parent=_EFFECT_LEDGER.get())
    _EFFECT_LEDGER.set(ledger)
    return ledger


@contextmanager
def named_background_effect_scope(
    name: str, *, durable: bool = False, source_context: dict[str, Any] | None = None
):
    """The EXPLICIT, NAMED, NON-TURN authorization scope (R2b1, amended).

    Effect doors fail closed when no ledger is active: an effect that cannot be
    attributed to a turn must not be authorized. The one sanctioned alternative
    is this scope, opened at the OWNING ENTRY POINT of legitimate background
    work (bootstrap probes, install steps, the watch poller, the self-update
    check, a DIRECT `execute_tool_intent` call) — never inside the universal
    door, which would manufacture the authority required to pass itself.

    `source_context` (additive) lets the owning entry point hand the scope the
    caller's real context, so the ledger freezes the policy the caller actually
    runs under (their session mode, workspace root) instead of a context-free
    default. Existing callers pass nothing and keep that default.

    The name is part of the law: blank or whitespace-only names are refused,
    because a nameless exemption is the general fail-open under another name.
    Every receipt the scope records carries the name and NO turn identity.

    RETENTION TRUTH: the scope's in-memory account is scope-local. It exists
    while the `with` block runs, is handed to no caller at close, and is not
    durable — its purpose is attribution at the doors, not bookkeeping.

    R2b2c — `durable=True` opts a scope into the ONE durable publisher: every
    lifecycle entry is written to the runtime-event store AS IT OCCURS (a
    `run_forever` scope never closes to hand anything over), stamped with a
    per-open daemon instance, bounded by the hard retention cap, and
    queryable across restart (`background_effect_evidence`). Wired for the
    three relay daemon scopes and nothing else; inside an active turn the
    deferral below still applies and the turn's ledger owns the effect.

    DEFERRAL: opened inside an active turn, this yields the turn's ledger
    untouched — work that fires inside a turn is the turn's effect, and the
    turn's frozen policy (veto included) governs it.
    """
    scope_name = str(name or "").strip()
    if not scope_name:
        raise ValueError(
            "named_background_effect_scope requires a specific, non-empty scope "
            "name (e.g. 'self_update.check') — a nameless exemption is a "
            "general fail-open"
        )
    current = _EFFECT_LEDGER.get()
    if current is not None:
        yield current
        return
    ledger = EffectLedger(
        source_context=source_context if isinstance(source_context, dict) else None,
        parent=None,
        scope_name=scope_name,
        durable=bool(durable),
        scope_instance=("bgi:" + secrets.token_hex(4)) if durable else "",
    )
    token = _EFFECT_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _EFFECT_LEDGER.reset(token)
        # P1 — same rollback law as close_effect_receipt_scope: a scope that
        # ends while still holding reservations never executed them.
        from core.effect_budget import release_unconsumed_for_owner

        release_unconsumed_for_owner(
            ledger.ledger_id,
            reason="background scope closed: authorized effect never executed",
            quiet=True,
        )
        with contextlib.suppress(Exception):
            from core.effect_budget_money import release_unclaimed_for_owner as _release_money

            _release_money(
                ledger.ledger_id, reason="background scope closed: payment never claimed for dispatch"
            )


def close_effect_receipt_scope() -> list[dict[str, Any]]:
    """Close THIS scope and return ONLY its own receipts.

    Restores the binding this scope displaced, so a nested scope's exit leaves
    the turn that contains it open and holding every receipt it had recorded.

    P1 — closing also runs the budget rollback: any reservation this ledger
    still holds is an AUTHORIZED effect that never executed, and its units
    return to the pool (quietly on store failure — a held unit never widens
    anything, and closing a turn must not break on the budget store).
    """
    ledger = _EFFECT_LEDGER.get()
    if ledger is None:
        return []
    _EFFECT_LEDGER.set(ledger.parent)
    from core.effect_budget import release_unconsumed_for_owner

    release_unconsumed_for_owner(
        ledger.ledger_id,
        reason="owner scope closed: authorized effect never executed",
        quiet=True,
    )
    # P3: never-claimed money returns with the scope; claimed money never does
    with contextlib.suppress(Exception):
        from core.effect_budget_money import release_unclaimed_for_owner

        release_unclaimed_for_owner(
            ledger.ledger_id, reason="owner scope closed: payment never claimed for dispatch"
        )
    return ledger.finalized()


def current_effect_ledger() -> EffectLedger | None:
    return _EFFECT_LEDGER.get()


def effect_receipts() -> tuple[dict[str, Any], ...]:
    """The turn's receipts so far (read-only view for tests and trace builders)."""
    ledger = _EFFECT_LEDGER.get()
    return ledger.entries() if ledger is not None else ()


def effect_receipts_truncated() -> bool:
    """Whether the per-effect detail stopped being kept for this turn."""
    ledger = _EFFECT_LEDGER.get()
    return ledger.truncated() if ledger is not None else False


def effect_receipts_dropped() -> int:
    """Exactly how many receipts the current turn's ledger dropped to stay
    bounded — the checkable second half of the truncation account (R2b1)."""
    ledger = _EFFECT_LEDGER.get()
    return ledger.dropped() if ledger is not None else 0


def effect_outcomes() -> tuple[dict[str, Any], ...]:
    """The current turn's per-effect outcome account (R2b2b): what was
    attempted, whether transport ran, and how it ended — one entry per LOGICAL
    effect, denials included. Empty outside a scope, which is a fact about the
    reader's position, never an invented account."""
    ledger = _EFFECT_LEDGER.get()
    return ledger.effect_outcomes() if ledger is not None else ()


def record_effect_receipt(receipt: EffectReceipt) -> None:
    """Append one receipt to the OWNING turn's ledger. No ledger open (an effect
    outside any turn scope — startup warmup, health probes) means there is no
    turn to record onto: the receipt is dropped, never invented onto a foreign
    turn."""
    ledger = _EFFECT_LEDGER.get()
    if ledger is None:
        return
    ledger.record(receipt)


def current_turn_policy() -> TurnPolicy | None:
    """The one policy this turn was frozen with, or None outside a turn scope.

    Every effect gate consumes THIS object. A gate that builds its own is the
    defect R2 removed: two doors in one turn deciding under two policies.
    """
    ledger = _EFFECT_LEDGER.get()
    return ledger.policy if ledger is not None else None


def decide_network_fetch(source_context: dict[str, Any] | None = None) -> tuple[str, str]:
    """The gateway consult for the network-fetch effect class — (decision, reason).

    Wraps `decide_tool_call` on a read-only retrieval intent, so the fetch door
    and the web.* tool path CANNOT disagree: same matrix, same mode revision,
    same grants. Slice 1 enforces only the DENY leg at the door (approval
    requirements stay with the lanes that can surface an approval card; the
    fetch door cannot). No mode policy active = allowed — the frontdoor lane's
    surface-trust default, and a POSITIVE reading of the policy, not a failure.

    R2 — two laws replace the old `except Exception -> allowed`:

    * FAIL CLOSED. A policy that cannot be built or consulted denies. The old
      handler returned ALLOWED on any exception, and it fired on every single
      call: both consult helpers were invoked with the wrong arity, so the door
      never once reached the matrix and every fetch was allowed by a TypeError.
    * THE TURN'S FIRST VERDICT IS A CEILING. It is frozen on the turn's ledger,
      so raising the mode mid-turn cannot widen the turn already in flight
      (invariant 10). It is a ceiling and not a freeze in both directions: a
      live DENY still binds, because narrowing must take effect immediately —
      that is what an expiring bypass grant does, and it must keep working.

    R2b1 — NO LEDGER, NO AUTHORIZATION. Outside any turn (or named) scope this
    consult DENIES with a typed reason: an effect that cannot be attributed to
    an active ledger must not be authorized. The consult always reads the
    ACTIVE scope's frozen policy — the `source_context` argument is accepted
    for call-shape compatibility and is no longer a policy input, because a
    per-call derivation was exactly the two-policies-one-turn defect R2 removed.
    """
    ledger = _EFFECT_LEDGER.get()
    if ledger is None:
        return (
            DECISION_DENIED,
            "no active turn ledger: a network fetch outside a turn scope is denied "
            "(decide_network_fetch fails closed; non-turn fetches run under "
            "named_background_effect_scope, which the open_remote door provides)",
        )
    try:
        policy = ledger.policy
    except Exception as exc:
        return DECISION_DENIED, f"policy construction failed closed: {type(exc).__name__}: {exc}"
    if policy.policy_error:
        return DECISION_DENIED, f"policy construction failed closed: {policy.policy_error}"
    frozen = ledger.frozen_decision(EFFECT_NETWORK_FETCH)
    if frozen is not None and frozen[0] == DECISION_DENIED:
        return frozen
    context = policy.decision_context_copy()
    try:
        from core import mode_permission_policy

        if not policy.mode_policy_active:
            verdict = (
                DECISION_ALLOWED,
                f"no mode policy active (principal={policy.principal})",
            )
        else:
            decision = mode_permission_policy.decide_tool_call(
                intent="web.research",
                arguments={},
                task_id="",
                source_context=context,
            )
            if decision.allowed:
                verdict = (DECISION_ALLOWED, str(getattr(decision, "reason", "") or ""))
            else:
                verdict = (DECISION_DENIED, str(getattr(decision, "reason", "") or "denied"))
    except Exception as exc:
        verdict = (
            DECISION_DENIED,
            f"gateway consult failed closed: {type(exc).__name__}: {exc}",
        )
    ledger.freeze_decision(EFFECT_NETWORK_FETCH, verdict)
    return verdict


def consume_turn_policy(gate: str) -> TurnPolicy:
    """The frozen policy, for a gate that must fail closed without one.

    Raises `PolicyUnavailableError` when there is NO active ledger or the
    turn's policy was built from inputs it could not resolve. R2b1: outside any
    scope there is no turn to attribute the effect to, so the gate is denied —
    the old "return an empty undeclared policy" was the fail-open that let
    unscoped commands and machine writes authorize. Non-turn effects have
    exactly one sanctioned path: `named_background_effect_scope`, under which
    this returns that scope's policy and the gates keep their own checks.
    """
    policy = current_turn_policy()
    if policy is None:
        raise PolicyUnavailableError(
            f"{gate}: no active turn ledger: an effect outside a turn scope is denied "
            "(the sanctioned non-turn path is named_background_effect_scope)"
        )
    if policy.policy_error:
        raise PolicyUnavailableError(f"{gate}: {policy.policy_error}")
    return policy


__all__ = [
    "BACKGROUND_EFFECT_EVENT_CAP",
    "DECISION_ALLOWED",
    "DECISION_DENIED",
    "DECISION_SIMULATED",
    "EFFECT_COMMAND",
    "EFFECT_NETWORK_FETCH",
    "EFFECT_RECEIPTS_CONTEXT_KEY",
    "EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY",
    "EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY",
    "LIFECYCLE_AUTHORIZED",
    "LIFECYCLE_CANCELLED",
    "LIFECYCLE_DENIED",
    "LIFECYCLE_FAILED",
    "LIFECYCLE_SIMULATED",
    "LIFECYCLE_STARTED",
    "LIFECYCLE_STATES",
    "LIFECYCLE_SUCCEEDED",
    "LIFECYCLE_TERMINALS",
    "LIFECYCLE_UNKNOWN",
    "MAX_RECEIPTS_PER_TURN",
    "BackgroundEffectJournal",
    "EffectLedger",
    "EffectLifecycle",
    "EffectReceipt",
    "EffectStateError",
    "PolicyUnavailableError",
    "TurnPolicy",
    "background_effect_evidence",
    "background_effect_persistence_status",
    "close_effect_receipt_scope",
    "consume_turn_policy",
    "current_effect_ledger",
    "current_turn_policy",
    "decide_network_fetch",
    "effect_outcomes",
    "effect_receipts",
    "effect_receipts_dropped",
    "effect_receipts_truncated",
    "named_background_effect_scope",
    "open_effect_receipt_scope",
    "record_effect_receipt",
    "reset_background_effect_journal",
    "safe_transport_failure_reason",
]
