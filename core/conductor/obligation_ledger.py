"""K-05 producer side: durable cross-lane obligation ledger.

Promotes `CanonicalObligations` from a plan-time object to a consultable,
durable, cross-lane authority. Built at intake BEFORE routing; source
cardinality is immutable (planners may decompose into derived work freely, but
the minted set never grows or shrinks).

Closure verdict (minimum durable type per CANONICAL_SCHEMA_CONTRACT):

    { "covered": bool, "open_count": int, "set_version": str }

Closure = STRUCTURALLY TERMINAL (zero ABSENT/PLANNED), NOT all-success:
GATED/FAILED/SUPERSEDED/CANCELLED permit finalization.

MODEL-PROSE-NOT-EVIDENCE: a machine-effect obligation can only reach SATISFIED
from A6 reconciled evidence — never from result shape, and never from model
prose. The disposition API enforces the allowlist mechanically.

RSS (AUD-20260829-003): a third kind, `demand`, holds ONE requested slot of the
turn, minted from the request itself at intake. Its obligation id carries the
demand unit it is about (`ob:<attempt>:demand:<unit_id>`), which is the
obligation<->slot mapping the ledger did not have. Lanes never disposition a
demand obligation — they record consumption RECEIPTS, and the finalization
closure sweep is the sole writer of demand dispositions, for every terminal
state. Before that gate, the ledger's entire evidence enforcement covered one
(kind, state) pair out of twelve, and a lane could buy closure by declaring its
own unattempted slots `unsupported`.
"""
from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

# Dispositions prose/model output may NEVER assign to a machine-effect
# obligation (allowlist enforced in record_disposition).
_PROSE_FORBIDDEN_STATES = frozenset({"satisfied"})
_EFFECT_STATE_PREFIX = "effect:"

_ACTIVE_SET: ContextVar[tuple[str, str] | None] = ContextVar(
    "active_obligation_set", default=None
)

#: The ONLY evidence source permitted to disposition a demand-kind obligation.
#: Reserved for `core.finalization`'s closure sweep — see `record_disposition`.
RSS_SWEEP_EVIDENCE = "rss_closure_sweep"

#: The single hardcoded reason is GONE from the sweep (PLAN-discharge-channel.md §4 B4): a
#: non-satisfied state now names its evidence — see the three sourced reasons below. This
#: constant survives only for two legacy READERS outside the sweep (attempt_retry's rerun
#: rendering and refused_slot_register's re-stamp) that describe their own no-lane claim.
RSS_REASON_NO_LANE = "no answering lane claimed this part of the request"

#: C-2: a refusal names its evidence. `not dispatched` — the dispatch record exists for the
#: turn and no dispatch row names this unit's clause.
RSS_REASON_NOT_DISPATCHED = "not dispatched"

#: `dispatched, failed (receipt X)` — a subtask was dispatched FOR this unit's clause and its
#: execution failed; {receipt} names the failed subtask id(s) (attempt-store rows), which is
#: the record the claim is read from.
RSS_REASON_DISPATCHED_FAILED = "dispatched, failed (receipt {receipt})"

#: `dispatched, unverifiable` — work ran for or beside this unit but nothing attests an
#: answer (no lane receipt over a dispatch row, or an unbindable dispatch).
RSS_REASON_DISPATCH_UNVERIFIABLE = "dispatched, unverifiable"

#: Dispatch-record states that are OBSERVED negatives: the lane looked at this clause and
#: provably did not serve it (no fetch target registered counts — nothing was ever fetchable).
#: SKIPPED/CANCELLED/PLANNED are deliberately NOT here: why they happened is unresolved.
_FAILED_DISPATCH_STATES = frozenset({"FAILED", "UNSUPPORTED_ENTITY"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def bind_active_set(set_id: str, version: str) -> Any:
    """Bind the turn's active obligation set for ANY lane to consult."""
    return _ACTIVE_SET.set((str(set_id), str(version)))


def clear_active_set() -> None:
    _ACTIVE_SET.set(None)


def active_set() -> tuple[str, str] | None:
    return _ACTIVE_SET.get()


def open_obligation_set(
    *,
    set_id: str = "",
    obligations: list[dict[str, Any]] | None = None,
    request_text: str = "",
    request_id: str = "",
    request_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mint one immutable set version from intake. Returns {set_id, version}.

    ``request_text`` is the turn's raw request, frozen beside the obligations so the
    finalization sweep can re-derive the demand decomposition after the turn's execution
    scope has exited — the documented condition under which the ContextVar binding is gone
    and only the payload survives.

    ``request_id`` is the A0 identity the turn was admitted under. It is what lets the
    sweep find this set from the five `finalize_answer` call sites that pass no closure
    dict and hold no ContextVar binding: the request id IS readable there, from the same
    seam finalization already consults for its own identity fields.

    ``request_graph`` is the turn's RequestGraph (``core.semantic.graph_serialization``
    payload, text included -- this snapshot already stores the request text). With it the
    sweep READS the frozen slot set the door minted instead of re-deriving it; a snapshot
    written without it is served by the bounded legacy re-derivation, and says so.
    """
    clean_set_id = str(set_id or "").strip() or f"obset-{uuid.uuid4().hex[:16]}"
    version = f"v1:{uuid.uuid4().hex[:16]}"
    snapshot = {
        "set_id": clean_set_id,
        "version": version,
        "request_text": str(request_text or ""),
        "request_id": str(request_id or ""),
        **({"request_graph": dict(request_graph)} if isinstance(request_graph, dict) and request_graph else {}),
        # RSS consumption receipts: facts written by answering lanes, never verdicts. The
        # sweep is the only reader that turns them into dispositions.
        "consumption": [],
        # Source cardinality is frozen here; dispositions live outside.
        "obligations": [
            {
                "obligation_id": str(item.get("obligation_id") or "").strip(),
                "text": str(item.get("text") or ""),
                "kind": str(item.get("kind") or "prose"),
                # 'prose' closable by served truth; 'effect' ONLY by A6 evidence;
                # 'demand' ONLY by the RSS closure sweep (see record_disposition).
                "state": "planned",
                # The obligation_id <-> demand-unit mapping, durable at intake. Before RSS
                # there was no such mapping anywhere in the tree, so no discharge check could
                # name WHICH slot a disposition was about.
                **(
                    {"unit_id": str(item.get("unit_id") or "")}
                    if item.get("unit_id")
                    else {}
                ),
                **(
                    {"slice_id": str(item.get("slice_id") or "")}
                    if item.get("slice_id")
                    else {}
                ),
            }
            for item in (obligations or [])
        ],
    }
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO obligation_sets (set_id, version, status, snapshot_json, created_at)
            VALUES (?, ?, 'OPEN', ?, ?)
            """,
            (clean_set_id, version, json.dumps(snapshot), _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"set_id": clean_set_id, "version": version}


def record_disposition(
    set_id: str,
    version: str,
    obligation_id: str,
    state: str,
    *,
    evidence_source: str = "model_prose",
    evidence_ref: str = "",
) -> bool:
    """Assign a terminal disposition to ONE obligation inside a set version.

    MODEL-PROSE-NOT-EVIDENCE: an effect-kind obligation can only be satisfied
    from A6 reconciled evidence (`evidence_source='a6_reconciled'`); any other
    source claiming satisfaction is refused (False).

    RSS-SWEEP-IS-SOLE-WRITER: a demand-kind obligation may be dispositioned ONLY
    by the finalization closure sweep (`evidence_source='rss_closure_sweep'`),
    and that rule covers EVERY terminal state, not only `satisfied`. Measured
    before this gate existed (AUD-20260829-003 / E012): the ledger's whole
    evidence enforcement was one `(kind, state)` pair out of twelve, so a lane
    could buy closure by looping over its own open obligations and writing
    `unsupported` / `gated` / `superseded` for slots it never attempted — with
    no code change to this module and nothing to stop it. The gaming path is now
    refused mechanically: the default `evidence_source` is `model_prose`, so a
    lane that tries it gets False for every slot it names.
    """
    allowed_terminal = {
        "satisfied",
        "failed",
        "gated",
        "unsupported",
        "superseded",
        "cancelled",
        # RSS: a requested slot the turn shipped without answering. Structurally
        # terminal (closure is terminality, never all-success) and rendered.
        "unanswered",
        # RSS: the served answer shows some of what this slot asked for and not the
        # rest. The runtime can prove neither that it answered nor that it did not,
        # so it claims nothing, renders nothing, and does not count as covered.
        "indeterminate",
    }
    if state not in allowed_terminal:
        raise ValueError(f"not a terminal obligation disposition: {state!r}")
    kind = _obligation_kind(set_id, version, obligation_id)
    if kind == "effect" and state == "satisfied" and evidence_source != "a6_reconciled":
        return False  # prose/result-shape closure of a machine effect: refused
    if kind == "demand" and evidence_source != RSS_SWEEP_EVIDENCE:
        return False  # any lane disposing its own demand slots: refused, all states
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
        if row is None:
            return False
        snapshot = json.loads(row["snapshot_json"])
        changed = False
        for item in snapshot["obligations"]:
            if item["obligation_id"] == obligation_id and item["state"] in (
                "planned",
                "absent",
            ):
                item["state"] = state
                item["evidence_source"] = str(evidence_source)
                item["evidence_ref"] = str(evidence_ref)
                changed = True
        if not changed:
            return False
        conn.execute(
            "UPDATE obligation_sets SET snapshot_json = ? WHERE set_id = ? AND version = ?",
            (json.dumps(snapshot), set_id, version),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _obligation_kind(set_id: str, version: str, obligation_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return ""
    for item in json.loads(row["snapshot_json"])["obligations"]:
        if item["obligation_id"] == obligation_id:
            return str(item.get("kind") or "prose")
    return ""


_BLOCKING_STATES = {"planned", "absent"}
_PERMITTED_TERMINAL_STATES = {
    "satisfied",
    "failed",
    "gated",
    "unsupported",
    "superseded",
    "cancelled",
    "unanswered",
    "indeterminate",
}


def closure_verdict(set_id: str, version: str) -> dict[str, Any]:
    """The minimum durable closure certificate.

    covered=True iff every obligation is structurally terminal (zero
    PLANNED/ABSENT). Terminal-but-failed obligations still permit closure —
    closure ≠ all-success.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"covered": False, "open_count": -1, "set_version": version}
    snapshot = json.loads(row["snapshot_json"])
    open_count = sum(
        1 for item in snapshot["obligations"] if item["state"] in _BLOCKING_STATES
    )
    return {"covered": open_count == 0, "open_count": open_count, "set_version": version}


# ------------------------------------------------------------------ RSS: demand accounting


def _read_snapshot(set_id: str, version: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row["snapshot_json"])


def demand_obligations(set_id: str, version: str) -> tuple[dict[str, Any], ...]:
    """Every demand obligation in the set version, in minted order."""
    snapshot = _read_snapshot(set_id, version)
    if snapshot is None:
        return ()
    return tuple(
        dict(item)
        for item in snapshot.get("obligations") or []
        if str(item.get("kind") or "") == "demand"
    )


def set_for_request(request_id: str) -> tuple[str, str] | None:
    """The newest obligation set minted under this A0 request id, if any.

    The third way to address a turn's set, after the ContextVar and the payload's closure
    dict. Without it the closure sweep runs on one door out of six: `channel_gateway.py`,
    `service.py` (x3) and `presence.py` construct their content inline and call
    `finalize_answer` with neither a binding nor a verdict.
    """
    clean = str(request_id or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT set_id, version, snapshot_json FROM obligation_sets ORDER BY rowid DESC"
        ).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"])
        except Exception:
            continue
        if str(snapshot.get("request_id") or "") == clean:
            return (str(row["set_id"]), str(row["version"]))
    return None


def request_text(set_id: str, version: str) -> str:
    """The turn's frozen request text, for re-deriving demand grain after scope exit."""
    snapshot = _read_snapshot(set_id, version)
    return str((snapshot or {}).get("request_text") or "")


def snapshot_request_graph(set_id: str, version: str) -> dict[str, Any] | None:
    """The persisted RequestGraph payload beside the request text, or None for a pre-graph set.

    Returned together with ``request_text`` under the keys ``core.semantic.bridges.ledger``
    reads, so the sweep asks one function for the frozen slot set and learns which source
    answered (the graph, or the legacy re-derivation)."""
    snapshot = _read_snapshot(set_id, version)
    if snapshot is None:
        return None
    return {
        "request_text": str(snapshot.get("request_text") or ""),
        "request_graph": snapshot.get("request_graph"),
    }


def record_slice_consumption(
    set_id: str,
    version: str,
    *,
    unit_id: str,
    family: str = "",
    evidence: str = "",
    excerpt: str = "",
) -> bool:
    """Record ONE fact: a lane served this demand unit. Never a disposition.

    Receipts are the missing half of "discharge from consumption". They name the
    unit (not the whole turn), carry which reading produced them, and are durable
    beside the obligations they refer to. They close nothing on their own — the
    finalization sweep is what turns a receipt into a `satisfied` disposition,
    holding the finalized bytes while it does. Idempotent per (unit_id, evidence).

    The honest limit, recorded rather than papered over: a receipt is still
    written by the lane whose work it attests. RSS narrows the trust boundary
    from "any bytes at all close the whole request" to "these bytes close this
    unit"; it does not make the attestation independent.
    """
    clean_unit = str(unit_id or "").strip()
    if not clean_unit:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
        if row is None:
            return False
        snapshot = json.loads(row["snapshot_json"])
        receipts = list(snapshot.get("consumption") or [])
        entry = {
            "unit_id": clean_unit,
            "family": str(family or ""),
            "evidence": str(evidence or ""),
            "excerpt": str(excerpt or "")[:240],
        }
        if any(
            item.get("unit_id") == entry["unit_id"]
            and item.get("evidence") == entry["evidence"]
            for item in receipts
        ):
            return True
        entry["recorded_at"] = _utcnow()
        receipts.append(entry)
        snapshot["consumption"] = receipts
        conn.execute(
            "UPDATE obligation_sets SET snapshot_json = ? WHERE set_id = ? AND version = ?",
            (json.dumps(snapshot), set_id, version),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def consumption_receipts(set_id: str, version: str) -> tuple[dict[str, Any], ...]:
    """Every consumption receipt recorded against this set version."""
    snapshot = _read_snapshot(set_id, version)
    if snapshot is None:
        return ()
    return tuple(dict(item) for item in snapshot.get("consumption") or [])


def record_slice_dispatch(
    set_id: str,
    version: str,
    *,
    unit_id: str,
    subtask_id: str = "",
    operation: str = "",
    state: str = "",
    failure_reason: str = "",
) -> bool:
    """Record ONE fact: a lane dispatched work for this demand unit's clause. Never a disposition.

    This is the other half of the discharge channel (PLAN-discharge-channel.md §4 B4): the
    receipts say what a lane SERVED; the dispatch record says what was ATTEMPTED. Between
    them the sweep can tell the three honest non-satisfied stories apart — never dispatched,
    dispatched but failed, dispatched but unverifiable — where one hardcoded reason used to
    cover them all.

    ``unit_id`` may be empty: an UNBINDABLE dispatch (the subtask ran but its span could not
    be bound to any clause) is still a fact the sweep needs, because it makes "not
    dispatched" unprovable for every unit no dispatch row names. Idempotent per
    (unit_id, subtask_id, state).
    """
    row = {
        "unit_id": str(unit_id or "").strip(),
        "subtask_id": str(subtask_id or ""),
        "operation": str(operation or ""),
        "state": str(state or ""),
        "failure_reason": str(failure_reason or "")[:240],
    }
    conn = get_connection()
    try:
        result = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
        if result is None:
            return False
        snapshot = json.loads(result["snapshot_json"])
        dispatches = list(snapshot.get("dispatches") or [])
        if any(
            item.get("unit_id") == row["unit_id"]
            and item.get("subtask_id") == row["subtask_id"]
            and item.get("state") == row["state"]
            for item in dispatches
        ):
            return True
        row["recorded_at"] = _utcnow()
        dispatches.append(row)
        snapshot["dispatches"] = dispatches
        conn.execute(
            "UPDATE obligation_sets SET snapshot_json = ? WHERE set_id = ? AND version = ?",
            (json.dumps(snapshot), set_id, version),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def slice_dispatches(set_id: str, version: str) -> tuple[dict[str, Any], ...]:
    """Every dispatch record stored against this set version."""
    snapshot = _read_snapshot(set_id, version)
    if snapshot is None:
        return ()
    return tuple(dict(item) for item in snapshot.get("dispatches") or [])


def consumed_unit_ids(set_id: str, version: str) -> frozenset[str]:
    """The demand units at least one consumption receipt names."""
    return frozenset(
        str(item.get("unit_id") or "")
        for item in consumption_receipts(set_id, version)
        if item.get("unit_id")
    )


def sweep_demand_obligations(
    set_id: str,
    version: str,
    *,
    states: dict[str, str] | None = None,
    receipts_outrank_bytes: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Drive EVERY open demand obligation to a terminal state. The sole writer.

    Evidence precedence (PLAN-discharge-channel.md §4 B4), per unit:

    1. a lane-attested receipt (evidence ``slice_answer_record``) -> ``satisfied``;
    2. else, when the turn carries a dispatch record: a failed dispatch for this unit ->
       ``unanswered`` ("dispatched, failed (receipt X)"); a dispatch without a receipt, or
       an unbindable dispatch -> ``indeterminate`` ("dispatched, unverifiable"); no dispatch
       at all for this unit -> ``unanswered`` ("not dispatched"). This is the ONLY source of
       provable ``unanswered`` — never the text ladder, which stays capped at
       ``indeterminate`` from Phase A;
    3. else (no dispatch record — the pre-B4 regime): prose receipts close, then the
       ``states`` verdicts, then ``indeterminate``. Absence of evidence never accuses.

    Every outcome is structurally terminal, so after this call the set can never be the
    reason finalization refuses: RSS is fail-visible, never fail-dead.

    Returns one row per demand obligation: ``{unit_id, text, state, reason}``.
    """
    consumed = consumed_unit_ids(set_id, version)
    # Evidence tiers (PLAN-discharge-channel.md §4 B4). A receipt written by the lane whose
    # execution it attests (evidence == "slice_answer_record") outranks everything. A receipt
    # read off the served bytes (served_answer_evidence, ride-alongs, test receipts) is
    # evidence about PROSE, and on a turn that carries a dispatch record the record outranks
    # the prose — C-7: presentation may never change a verdict; the "Silver:" table header
    # may not buy coverage for a slot the record shows was never served.
    lane_attested: set[str] = set()
    byte_attested: set[str] = set()
    ride_along: set[str] = set()
    for receipt in consumption_receipts(set_id, version):
        unit = str(receipt.get("unit_id") or "")
        if not unit:
            continue
        evidence = str(receipt.get("evidence") or "")
        if evidence == "slice_answer_record":
            lane_attested.add(unit)
        elif evidence == "ride_along_no_object":
            ride_along.add(unit)
        else:
            byte_attested.add(unit)
    dispatch_rows = slice_dispatches(set_id, version)
    unit_dispatches: dict[str, list[dict[str, Any]]] = {}
    unbindable_dispatches = 0
    for row in dispatch_rows:
        unit = str(row.get("unit_id") or "")
        if unit:
            unit_dispatches.setdefault(unit, []).append(row)
        else:
            unbindable_dispatches += 1
    supplied = dict(states or {})
    rows: list[dict[str, Any]] = []
    for item in demand_obligations(set_id, version):
        unit_id = str(item.get("unit_id") or "")
        persisted = str(item.get("state") or "")
        reason = ""
        unit_rows = unit_dispatches.get(unit_id) or []
        failed = [
            row for row in unit_rows
            if str(row.get("state") or "") in _FAILED_DISPATCH_STATES
        ]
        if unit_id in lane_attested and receipts_outrank_bytes:
            state = "satisfied"
        elif unit_id in lane_attested:
            # A RECEIPT FILED AGAINST BYTES THAT WERE THEN EDITED. `receipts_outrank_bytes` is
            # False only when this turn's publication gate withheld a statement, so what the
            # lane attested is no longer necessarily in the answer. The caller's ladder reading
            # of the PUBLISHED bytes decides instead: a slot still present in the answer stays
            # satisfied, and one whose bytes went with the withheld statement does not.
            #
            # Never `unanswered` from here: a lane did run, so absence of bytes is not proof
            # nothing was dispatched.
            state = supplied.get(unit_id) or "indeterminate"
            reason = "" if state == "satisfied" else RSS_REASON_DISPATCH_UNVERIFIABLE
        elif dispatch_rows:
            # The dispatch record exists for this turn, so it answers whether THIS unit
            # was ever invoked for — provable unanswered lives HERE, never in the text
            # ladder (which stays capped at indeterminate from Phase A).
            if failed:
                state = "unanswered"
                reason = RSS_REASON_DISPATCHED_FAILED.format(
                    receipt=", ".join(
                        sorted({str(row.get("subtask_id") or "") for row in failed if row.get("subtask_id")})
                    )
                    or "unknown subtask"
                )
            elif unit_id in ride_along:
                # A rider (`ride_along_no_object`, written by the finalization sweep for
                # units that name no thing to look up) dispatches nothing BY DESIGN, so
                # 'not dispatched' is not a fact about it — accusing one through dispatch
                # absence would render "give me a summary" / "which one is warmer?" as
                # refused work on every typed turn. It rides the turn instead: satisfied
                # when the turn lane-attested any OTHER unit, silent otherwise.
                state = "satisfied" if (lane_attested - {unit_id}) else "indeterminate"
                reason = ""
            elif unit_rows:
                state = "indeterminate"
                reason = RSS_REASON_DISPATCH_UNVERIFIABLE
            elif unbindable_dispatches:
                # Something ran that cannot be bound to a clause: 'not dispatched' is
                # unprovable for this unit, and the record does not serve it either.
                state = "indeterminate"
                reason = RSS_REASON_DISPATCH_UNVERIFIABLE
            else:
                state = "unanswered"
                reason = RSS_REASON_NOT_DISPATCHED
        elif unit_id in consumed:
            # No dispatch record — the pre-B4 evidence regime: prose receipts still
            # close, because nothing observed contradicts them.
            state = "satisfied"
        else:
            state = supplied.get(unit_id) or "indeterminate"
            reason = "" if state == "satisfied" else RSS_REASON_DISPATCH_UNVERIFIABLE
        # Rows are derived from current EVIDENCE, so a re-sweep of an already-terminal
        # set reproduces both the state and its reason (idempotent reads); the
        # disposition write itself stays restricted to states still blocking.
        if persisted in _BLOCKING_STATES and state != persisted:
            record_disposition(
                set_id,
                version,
                str(item.get("obligation_id") or ""),
                state,
                evidence_source=RSS_SWEEP_EVIDENCE,
                evidence_ref=unit_id,
            )
        rows.append(
            {
                "unit_id": unit_id,
                "text": str(item.get("text") or ""),
                "state": state,
                "reason": reason,
            }
        )
    return tuple(rows)


def demand_census(set_id: str, version: str) -> dict[str, int]:
    """Minted cardinality beside the per-state counts.

    Property 4 of the remedy: a vacuous one-slot closure must be distinguishable
    by inspection from a genuinely complete four-slot turn. `open_count` alone
    could not do that — it is zero in both cases.
    """
    minted = 0
    satisfied = 0
    unanswered = 0
    indeterminate = 0
    still_open = 0
    for item in demand_obligations(set_id, version):
        minted += 1
        state = str(item.get("state") or "")
        if state == "satisfied":
            satisfied += 1
        elif state in _BLOCKING_STATES:
            still_open += 1
        elif state == "indeterminate":
            indeterminate += 1
        else:
            unanswered += 1
    return {
        "demand_minted": minted,
        "demand_satisfied": satisfied,
        "demand_unanswered": unanswered,
        "demand_indeterminate": indeterminate,
        "demand_open": still_open,
    }


def attempt_id_of_set(set_id: str, version: str) -> str:
    """The runtime attempt this set's obligations were minted against, or "".

    The run_once spine mints `ob:{attempt_id}:answer` / `ob:{attempt_id}:demand:{unit}`
    (apps/vool_agent.py, R-6/RSS), so the attempt identity is carried by the obligation
    ids themselves -- an id link, not a text match. Entrance-minted sets
    (`ob:entrance:{unit}`) bind no attempt and return "". This is what lets the
    finalization sweep -- the one seam where the demand census is terminal -- hand the
    census to the attempt row that owns the turn's coarse status (M3B root cause D).
    """
    for item in demand_obligations(set_id, version):
        obligation_id = str(item.get("obligation_id") or "")
        if obligation_id.startswith("ob:") and ":demand:" in obligation_id:
            return obligation_id[len("ob:") : obligation_id.index(":demand:")]
    return ""


def assert_prose_cannot_close_pending_effect(set_id: str, version: str) -> bool:
    """Red-mutation guard helper: returns True when a pending effect obligation
    exists that model prose may not close (the loop must keep working)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    for item in json.loads(row["snapshot_json"])["obligations"]:
        if item.get("kind") == "effect" and item["state"] in _BLOCKING_STATES:
            return True
    return False


def register_effect_obligation(
    set_id: str,
    version: str,
    obligation_id: str,
    *,
    text: str = "",
) -> bool:
    """Register ONE machine-effect obligation against the turn's active set.

    R-residue-2: effects decided by the tool loop belong to the turn's
    obligation set so finalization cannot close over them without A6
    reconciled evidence. Idempotent per obligation id; the set snapshot grows
    only here, never from model prose.
    """
    clean = str(obligation_id or "").strip()
    if not clean:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (set_id, version),
        ).fetchone()
        if row is None:
            return False
        snapshot = json.loads(row["snapshot_json"])
        if any(item["obligation_id"] == clean for item in snapshot["obligations"]):
            return True
        snapshot["obligations"].append(
            {
                "obligation_id": clean,
                "text": str(text or ""),
                "kind": "effect",
                "state": "planned",
            }
        )
        conn.execute(
            "UPDATE obligation_sets SET snapshot_json = ? WHERE set_id = ? AND version = ?",
            (json.dumps(snapshot), set_id, version),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def discharge_effect_obligation(
    set_id: str,
    version: str,
    logical_effect_id: str,
    *,
    applied: bool | None,
) -> bool:
    """Discharge an effect obligation FROM A6 RECONCILED EVIDENCE ONLY.

    applied=True  -> satisfied (a6_reconciled)
    applied=False -> failed (a6_reconciled; structurally terminal)
    applied=None  -> UNKNOWN: stays planned and BLOCKS closure
    """
    obligation_id = f"ob:effect:{logical_effect_id}"
    if applied is True:
        return record_disposition(
            set_id, version, obligation_id, "satisfied", evidence_source="a6_reconciled"
        )
    if applied is False:
        return record_disposition(
            set_id, version, obligation_id, "failed", evidence_source="a6_reconciled"
        )
    return False  # unknown stays open, by law
