"""The one authoritative record of what VOOL actually executed on a turn.

Before this module, an execution left several unrelated traces and every consumer rebuilt
"what happened" from a different one:

* the runtime event ledger (`runtime_session_events`) carried a `tool_executed` row whose SHAPE
  depended on which of sixteen call sites emitted it -- the live-data lane wrote a thin
  `{tool_name, summary}`, the web lane wrote a full signed `action_record`;
* `runtime_tool_receipts` held a row only for the paths that happened to write one (45 rows against
  162 real executions, and not one of the 128 live-data executions);
* the signed honesty ledger asked `runtime_tool_receipts` what ran, so it signed `executed_tools: []`
  over turns that had genuinely hit the network -- 2,399 receipts, 2 of them non-empty;
* the Activity panel asked the raw event stream a fourth question and answered "No tool ran" over
  turns whose retrieval was recorded under a different event type.

None of those disagreed because a counter was wrong. They disagreed because there was no shared
answer to two prior questions: *which turn is this* and *what counts as an execution*. Each store
invented its own identifier, so the stores could not be joined at all -- 809 turn receipts and 807
truth-metric records shared exactly zero identifiers -- and each consumer invented its own predicate.

So this module owns exactly those two questions, and nothing else:

* :func:`resolve_turn_key` -- ONE canonical turn identity, derived from the identifiers the runtime
  already carries, resolved in one place instead of at every call site;
* :func:`record_execution` -- ONE authoritative fact per real execution, keyed by that identity and
  deduplicated by content, so the same execution recorded twice is still one fact.

"What counts as an execution" is answered by ONE typed registry (`_EXECUTION_EVENT_RULES`, read
through :func:`classify_runtime_event`), which also answers WHICH KIND of execution it was: an
explicit tool, a model call, a governed retrieval, or a governed local effect. The categories are
not interchangeable -- the confirmed defect this vocabulary exists for is a turn that performed
governed web retrieval through the research lane while `ran_tool` read false and every surface
derived "nothing ran"; the registry records that retrieval as what it is instead of hiding it or
dressing it up as a tool. Every secondary view then DERIVES from the recorded facts
(:func:`executed_tools`, :func:`turn_ran_tool`, :func:`turn_ran_retrieval`, :func:`turn_ran_effect`,
:func:`model_calls`, :func:`turn_execution_summary`) rather than reconstructing reality from a store
it happens to know about. Views that derive from one source cannot contradict each other; that is a
property of the shape, not of a check that has to be maintained.

That leaves one failure the shape cannot rule out: the ledger being INCOMPLETE. If an execution never
reaches :func:`record_execution`, every derived view is wrong together and agrees perfectly. So
:func:`verify_execution_truth` does not compare the views against each other -- that would always
pass. It compares the authoritative ledger against an INDEPENDENT witness: the raw event stream,
written by a different code path for a different reason. An execution the witness saw and the ledger
missed is the one thing that can silently un-ground every claim downstream, so that is what the gate
is built to catch.

Recording is best-effort and never raises: observability must not be able to fail a turn. The GATE is
strict, because a gate that shrugs is not a gate.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA = "vool.execution_fact.v1"

# What kind of execution a fact records. These are the four things a turn can actually DO that a
# downstream claim can be grounded in, and they are deliberately NOT interchangeable:
#
# * `tool`      -- an explicit typed tool invocation (the LLM or a typed plan asked for it by name);
# * `model`     -- a real provider generation;
# * `retrieval` -- governed network retrieval, receipt-backed, whether or not any tool was invoked
#                  (the adaptive-research lane fetches with no tool event at all);
# * `effect`    -- a governed local effect, receipt-backed (today: workspace mutations).
#
# The original two-kind vocabulary conflated these: a turn that performed governed web retrieval
# through the research lane produced no `tool` fact, so `ran_tool` was false and every surface
# derived "nothing ran" over work that had genuinely gone out to the network. The fix is not to
# pretend the retrieval was a tool -- that would corrupt `ran_tool` in the other direction -- but to
# record it under its own category. Adding a further kind means a new class of claim became
# groundable, which is a design decision, not a logging detail.
KIND_TOOL = "tool"
KIND_MODEL = "model"
KIND_RETRIEVAL = "retrieval"
KIND_EFFECT = "effect"

# How a recorded fact turned out. `refused` is kept apart from `failed` on purpose: a refusal is a
# policy decision that stopped the execution BEFORE it ran, so it must never count as evidence that
# anything executed -- while a failure is an execution that ran and broke, which is still a run.
OUTCOME_EXECUTED = "executed"
OUTCOME_FAILED = "failed"
OUTCOME_REFUSED = "refused"

# Statuses that mean "policy stopped this before it executed", across the vocabularies the emitting
# lanes already use (`refused` from the retrieval receipts, `permission_denied`/`disabled` from the
# workspace mutation ledger). Matched against a fact's own status, never inferred from prose.
_REFUSED_STATUSES = frozenset({"refused", "permission_denied", "disabled", "denied"})


@dataclass(frozen=True)
class EventRule:
    """How one runtime event type maps into the execution ledger.

    `kind` is the primary execution category of the fact(s) the event records. `ok` is the fixed
    success polarity the event type itself proves, or None when the event's own details carry the
    outcome (a terminal that reports both success and refusal under one type).
    """

    kind: str
    ok: bool | None = None


# THE typed event -> execution-category registry: the single definition of "an execution happened"
# in the runtime. Consumers ask this module, never the raw event stream, so a new execution-bearing
# event type is added here once instead of in each view. Before this table existed the definition
# was two hardcoded tool-event names, so every receipt-backed retrieval and workspace effect was
# invisible to execution truth while being fully visible in the event stream.
#
# Deliberately absent: `tool_selected` (a selection is not an execution), `*_started` events (an
# execution is recorded at its terminal, where the outcome is known -- recording the start would
# double-count every run), `live_data_plan_*` (planning, not executing), and the paid-reservation
# spend events (spend governance evidence, not an execution of anything).
_EXECUTION_EVENT_RULES: dict[str, EventRule] = {
    "tool_executed": EventRule(KIND_TOOL, True),
    "tool_failed": EventRule(KIND_TOOL, False),
    "model_lane_proof": EventRule(KIND_MODEL, None),
    "web_retrieval_completed": EventRule(KIND_RETRIEVAL, None),
    "web_retrieval_failed": EventRule(KIND_RETRIEVAL, None),
    "fx_retrieval_completed": EventRule(KIND_RETRIEVAL, True),
    "fx_retrieval_failed": EventRule(KIND_RETRIEVAL, False),
    "workspace_mutation_completed": EventRule(KIND_EFFECT, True),
    "workspace_mutation_failed": EventRule(KIND_EFFECT, None),
    "workspace_mutation_rollback_completed": EventRule(KIND_EFFECT, True),
    "workspace_mutation_rollback_conflict": EventRule(KIND_EFFECT, False),
}

# Typed tools whose execution IS a governed retrieval. A `web.search` invocation is both things at
# once -- an explicit tool the model asked for by name, and a network retrieval -- so its fact
# carries both categories rather than forcing one truth to impersonate the other. Matched on the
# tool name the emitting lane already stamps; prefixes cover the live-data plan's per-operation
# names (`live_data.weather_lookup`, `live_data.market_quote`).
_RETRIEVAL_TOOL_NAMES = frozenset({"web.search", "web.fetch", "web.browser_render", "web.research", "web.ddg_instant"})
_RETRIEVAL_TOOL_PREFIXES = ("live_data.",)

# Identity resolution, in priority order, as (field, where-to-look).
#
# `client_turn_id` wins: it is the identifier the PRODUCT uses for a turn -- it arrives per turn in
# the /api/chat body, scopes the Activity panel, and is what cancel addresses.
#
# The ordering of the two `turn_id` entries is the part that is easy to get backwards, and getting it
# backwards was measured in a live drive: `checkpoints.prepare_runtime_checkpoint` stamps ONE ambient
# `turn_id` into the source context for the whole turn, but individual lanes mint their own and pass
# it in the event's details -- the fast path emits `model_lane_proof` with `turn_id=response_id`. When
# details were preferred wholesale, that one event filed itself under a synthetic key of its own while
# the other ten events of the same turn filed under the ambient one. So the AMBIENT id (context) beats
# a lane-local `turn_id` (details), and the lane-local value survives only as a last resort for paths
# that run with no checkpoint at all.
#
# `client_turn_id` is exempt from that caution and read from either side, because `emit_runtime_event`
# copies it from the context onto the payload -- both sides carry the same value by construction.
#
# `turn_key` itself comes FIRST. Once the emit path has stamped an event with the resolved identity,
# that value IS the identity and every later reader must take it rather than re-deriving. A reader
# re-deriving looks equivalent and is not: the writer resolves with the source context in hand, a
# reader holding only a stored row does not, so the two fall to different fallbacks and file the same
# event under different keys. That was measured against the live API -- the events endpoint rebuilt
# identity from each row and produced twenty turn keys (checkpoint ids, request ids) for a session
# with three real turns, none of which matched the facts written for them. Two independent
# derivations of one identity is the exact defect this module exists to remove, so it must not be
# re-created between this module's own writer and its own readers.
_TURN_ID_FIELDS = (
    ("turn_key", "both"),
    ("client_turn_id", "both"),
    ("cancel_turn_id", "both"),
    ("turn_id", "context"),
    ("checkpoint_id", "both"),
    ("turn_id", "details"),
    ("request_id", "both"),
)

_LOCK = threading.RLock()


@dataclass(frozen=True)
class ExecutionFact:
    """One thing the runtime actually did, on one turn."""

    fact_id: str
    turn_key: str
    session_id: str
    kind: str
    name: str
    ok: bool
    status: str
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    # Every execution category this fact belongs to. Always includes `kind`; a typed `web.search`
    # invocation carries ("retrieval", "tool"). Rows written before the category vocabulary existed
    # load as (kind,), which is exactly what they meant.
    categories: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "turn_key": self.turn_key,
            "session_id": self.session_id,
            "kind": self.kind,
            "name": self.name,
            "ok": self.ok,
            "status": self.status,
            "detail": dict(self.detail),
            "created_at": self.created_at,
            "categories": list(self.categories or (self.kind,)),
        }

    @property
    def outcome(self) -> str:
        """`executed`, `failed`, or `refused` -- the one bucket downstream claims sort facts by.

        Refusal is read off the fact's own status, never inferred: a policy refusal is recorded by
        the emitting lane in its status vocabulary, and it must not be counted as an execution in
        either direction (not a success, and not a broken run either -- nothing ran).
        """
        if _text(self.status).lower() in _REFUSED_STATUSES:
            return OUTCOME_REFUSED
        return OUTCOME_EXECUTED if self.ok else OUTCOME_FAILED

    def in_category(self, category: str) -> bool:
        return category in (self.categories or (self.kind,))


@dataclass(frozen=True)
class TruthVerdict:
    """Whether the authoritative ledger accounts for every execution an independent witness saw."""

    consistent: bool
    turn_key: str
    recorded: int
    witnessed: int
    missing: tuple[str, ...] = ()
    detail: str = ""
    # The same missing executions, category-qualified as "kind:name", so a consumer deciding how to
    # fail closed can tell a missing retrieval (something DID run) from a missing model proof
    # (nothing tool-shaped ran) without re-parsing the bare names in `missing`.
    missing_qualified: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "consistent": self.consistent,
            "turn_key": self.turn_key,
            "recorded": self.recorded,
            "witnessed": self.witnessed,
            "missing": list(self.missing),
            "detail": self.detail,
            "missing_qualified": list(self.missing_qualified),
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_turn_key(
    source_context: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """The canonical identity of the turn in flight, resolved once for every producer.

    Reads the identifiers the runtime already carries, in a fixed priority, from the two places a
    caller has them. Returns "" when the turn genuinely has no identity yet -- a caller must not
    invent one, because a synthesized id would join to nothing and quietly re-create the fragmentation
    this function exists to remove.
    """
    context = source_context or {}
    detail = details or {}
    for name, where in _TURN_ID_FIELDS:
        found = ""
        if where in ("both", "details"):
            found = _text(detail.get(name))
        if not found and where in ("both", "context"):
            found = _text(context.get(name))
        if found:
            return found
    return ""


def _fact_id(turn_key: str, kind: str, name: str, status: str, dedupe: str) -> str:
    """Deterministic identity so ONE execution yields ONE fact even if recorded twice.

    Emission is not guaranteed to happen once: a retry, a re-entrant emit, or two call sites both
    reporting the same run would each add a row, and a duplicated fact inflates every derived count
    at once. Hashing the content makes the second write a no-op instead of a second execution.
    """
    material = "\x1f".join((SCHEMA, turn_key, kind, name, status, dedupe))
    return "exec-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _dedupe_token(detail: dict[str, Any] | None) -> str:
    """The most specific per-execution identifier available, for content-addressed dedupe.

    Falls back to the empty string, which makes two otherwise identical executions on one turn
    collapse into one fact. That is the safe direction: under-counting a repeated identical call is
    a lesser wrong than letting one call be counted twice as evidence for two separate claims.
    """
    source = detail or {}
    for name in (
        "receipt_id",
        "tool_call_id",
        "action_id",
        "subtask_id",
        "retrieval_id",
        "rollback_id",
        "request_id",
        "manifest_id",
    ):
        found = _text(source.get(name))
        if found:
            return found
    return ""


@dataclass(frozen=True)
class ExecutionSpec:
    """One fact the classifier says a runtime event proves. Pure data; recording is separate."""

    kind: str
    categories: tuple[str, ...]
    name: str
    ok: bool
    status: str
    dedupe: str
    detail: dict[str, Any] = field(default_factory=dict)


def _tool_categories(name: str) -> tuple[str, ...]:
    """The full nature of one typed tool invocation. A retrieval-natured tool is both at once."""
    if name in _RETRIEVAL_TOOL_NAMES or name.startswith(_RETRIEVAL_TOOL_PREFIXES):
        return (KIND_RETRIEVAL, KIND_TOOL)
    return (KIND_TOOL,)


def _detail_subset(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source.get(key) for key in keys if source.get(key)}


def _retrieval_spec_from_receipt(receipt: dict[str, Any], *, failed_event: bool) -> ExecutionSpec | None:
    """One retrieval fact from one `vool.web_retrieval_receipt.v1`-shaped payload.

    The receipt's own status is the outcome authority: `refused` stays refused (policy stopped the
    fetch -- nothing executed), `failed` is an execution that ran and broke, and `available` /
    `unavailable` are both successful executions (a search that genuinely ran and found nothing is
    a run, and its emptiness is carried by the status, not erased by it).
    """
    status = _text(receipt.get("status")).lower()
    if not status or status == "started":
        return None
    name = _text(receipt.get("kind")) or "web_retrieval"
    ok = status in {"available", "unavailable"} and not failed_event
    return ExecutionSpec(
        kind=KIND_RETRIEVAL,
        categories=(KIND_RETRIEVAL,),
        name=name,
        ok=ok,
        status=status,
        dedupe=_text(receipt.get("retrieval_id")),
        detail=_detail_subset(
            receipt,
            # `subtask_id`: the plan node this retrieval served, so the proof projection unions the
            # receipt's fact with the node's own completion row by a runtime-owned identifier.
            ("retrieval_id", "action", "provider_id", "keyed_or_keyless", "source_count", "task_id", "failure_class", "subtask_id"),
        ),
    )


def classify_runtime_event(event_type: str, details: dict[str, Any] | None) -> list[ExecutionSpec]:
    """Every execution fact one runtime event proves, per the typed registry. Pure; never writes.

    This is the ONE place an event name is interpreted as an execution. The recorder and the
    independent witness both call it, so they cannot disagree about what counts -- and a new
    execution-bearing event type added to the registry is recorded and witnessed by existing.
    """
    kind_name = _text(event_type)
    rule = _EXECUTION_EVENT_RULES.get(kind_name)
    if rule is None:
        return []
    detail = dict(details or {})

    if rule.kind == KIND_TOOL:
        name = _text(detail.get("tool_name")) or _text(detail.get("intent"))
        if not name:
            return []
        ok = bool(rule.ok)
        return [
            ExecutionSpec(
                kind=KIND_TOOL,
                categories=_tool_categories(name),
                name=name,
                ok=ok,
                status=_text(detail.get("status")) or ("executed" if ok else "failed"),
                dedupe=_dedupe_token(detail),
                # `subtask_id` / `node_id`: the plan node this execution WAS, so a consumer that also
                # reads node completions (the proof projection) unions the two records by a
                # runtime-owned identifier instead of counting one lookup twice.
                detail=_detail_subset(
                    detail,
                    ("receipt_id", "tool_call_id", "action_id", "summary", "checkpoint_id", "subtask_id", "node_id"),
                ),
            )
        ]

    if rule.kind == KIND_MODEL:
        # A completed lane proof from a real provider is a model call. The fast path emits the same
        # event type with `runtime-fast-path` as the provider to record that NO model ran, so the
        # provider is what separates the two -- not the message text, which is prose and drifts.
        if _text(detail.get("phase")) != "completed":
            return []
        provider = _text(detail.get("actual_adapter_provider_id")) or _text(detail.get("provider_id"))
        if not provider or provider == "runtime-fast-path":
            return []
        model = _text(detail.get("actual_adapter_model_id")) or _text(detail.get("model_id"))
        return [
            ExecutionSpec(
                kind=KIND_MODEL,
                categories=(KIND_MODEL,),
                name=f"{provider}:{model}".strip(":"),
                ok=not _text(detail.get("failure_reason")),
                status="completed",
                dedupe=_dedupe_token(detail),
                detail=_detail_subset(detail, ("turn_id", "lane", "task_class", "checkpoint_id")),
            )
        ]

    if rule.kind == KIND_RETRIEVAL:
        failed_event = kind_name.endswith("_failed")
        receipts = detail.get("receipts")
        if isinstance(receipts, list) and receipts:
            # The live-data plan lane publishes one batch terminal carrying every receipt of the
            # executed plan. Each receipt is one remote fetch, so each becomes one fact, deduped by
            # its own retrieval_id -- NOT one fact for the batch, which would report three fetches
            # as one, and not receipts + a batch fact, which would count one fetch twice.
            specs = [
                _retrieval_spec_from_receipt(receipt, failed_event=failed_event)
                for receipt in receipts
                if isinstance(receipt, dict)
            ]
            return [spec for spec in specs if spec is not None]
        if kind_name.startswith("fx_retrieval"):
            spec = ExecutionSpec(
                kind=KIND_RETRIEVAL,
                categories=(KIND_RETRIEVAL,),
                name=_text(detail.get("kind")) or "fx_quote",
                ok=bool(rule.ok),
                status=_text(detail.get("status")) or ("available" if rule.ok else "failed"),
                dedupe=_text(detail.get("retrieval_id")),
                detail=_detail_subset(detail, ("retrieval_id", "base", "quote", "source", "failure_class")),
            )
            return [spec]
        single = _retrieval_spec_from_receipt(detail, failed_event=failed_event)
        return [single] if single is not None else []

    if rule.kind == KIND_EFFECT:
        name = _text(detail.get("tool_intent")) or "workspace_mutation"
        status = _text(detail.get("result_state")).lower()
        if _text(detail.get("permission_decision")).lower() == "denied" or status in _REFUSED_STATUSES:
            # Policy stopped the mutation before it touched the workspace: recorded as refused, and
            # `outcome` keeps it out of every "this ran" answer.
            ok = False
            status = status if status in _REFUSED_STATUSES else "refused"
        else:
            ok = bool(rule.ok) if rule.ok is not None else bool(detail.get("ok"))
            status = status or ("executed" if ok else "failed")
        return [
            ExecutionSpec(
                kind=KIND_EFFECT,
                categories=(KIND_EFFECT,),
                name=name,
                ok=ok,
                status=status,
                dedupe=_text(detail.get("rollback_id")) or _dedupe_token(detail),
                detail=_detail_subset(
                    detail,
                    ("tool_intent", "canonical_target", "action", "result_state", "rollback_id", "task_id"),
                ),
            )
        ]

    return []


def _conn():
    from core.runtime_continuity import _conn as _runtime_conn

    return _runtime_conn()


def _ensure_table(conn) -> bool:
    """True when the fact table is usable. Older DBs predate it; migrations create it."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_facts' LIMIT 1"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def record_execution(
    *,
    session_id: str,
    turn_key: str,
    kind: str,
    name: str,
    ok: bool,
    status: str = "",
    detail: dict[str, Any] | None = None,
    categories: tuple[str, ...] = (),
    dedupe: str = "",
) -> str:
    """Record one authoritative execution fact. Best-effort; never raises.

    Returns the fact id, or "" when nothing was recorded (no identity, no session, or no table).
    Recording must never be able to fail a turn -- an observability write that can raise turns a
    working execution into a failed one, which is a strictly worse outcome than a missing record.
    The missing record is what :func:`verify_execution_truth` is for.

    `categories` is the fact's full nature per the classifier; it always ends up containing `kind`,
    so a caller that passes nothing records a plain single-category fact. `dedupe` overrides the
    detail-derived token when the classifier already resolved the most specific identifier.
    """
    turn = _text(turn_key)
    session = _text(session_id)
    tool_name = _text(name)
    if not turn or not session or not tool_name:
        return ""
    clean_kind = _text(kind) or KIND_TOOL
    clean_status = _text(status) or ("executed" if ok else "failed")
    payload = dict(detail or {})
    clean_categories = sorted({clean_kind, *(_text(item) for item in categories if _text(item))})
    payload["categories"] = clean_categories
    fact_id = _fact_id(turn, clean_kind, tool_name, clean_status, _text(dedupe) or _dedupe_token(payload))
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return ""
                conn.execute(
                    """
                    INSERT OR IGNORE INTO execution_facts (
                        fact_id, turn_key, session_id, kind, name, ok, status, detail_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        turn,
                        session,
                        clean_kind,
                        tool_name,
                        1 if ok else 0,
                        clean_status,
                        json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str),
                        _utcnow(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return ""
    return fact_id


def _fact_from_row(row) -> ExecutionFact:
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except Exception:
        detail = {}
    clean_detail = detail if isinstance(detail, dict) else {}
    kind = str(row["kind"])
    raw_categories = clean_detail.get("categories")
    categories = tuple(
        sorted({kind, *(str(item) for item in raw_categories if str(item or "").strip())})
        if isinstance(raw_categories, list)
        else (kind,)
    )
    return ExecutionFact(
        fact_id=str(row["fact_id"]),
        turn_key=str(row["turn_key"]),
        session_id=str(row["session_id"]),
        kind=kind,
        name=str(row["name"]),
        ok=bool(row["ok"]),
        status=str(row["status"]),
        detail=clean_detail,
        created_at=str(row["created_at"]),
        categories=categories,
    )


def facts_for_turn(turn_key: str, *, session_id: str = "", limit: int = 200) -> list[ExecutionFact]:
    """Every authoritative fact for one turn. The single source every view below derives from."""
    turn = _text(turn_key)
    if not turn:
        return []
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                sql = "SELECT * FROM execution_facts WHERE turn_key = ?"
                args: list[Any] = [turn]
                if _text(session_id):
                    sql += " AND session_id = ?"
                    args.append(_text(session_id))
                sql += " ORDER BY created_at ASC LIMIT ?"
                args.append(max(1, int(limit)))
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    return [_fact_from_row(row) for row in rows]


def recent_session_facts(session_id: str, *, name_prefix: str = "", limit: int = 64) -> list[ExecutionFact]:
    """The session's most recent facts, newest first -- for CONTINUITY, never as a turn's evidence.

    Every view below answers "what did THIS turn do" and stays turn-scoped (see
    :func:`executed_tools` for why a session-wide read is the wrong evidence for a turn). This read
    answers a different question for a follow-up turn that names no tool -- "what has this session
    been doing" -- so routing can keep a conversation's tools reachable from the ledger instead of
    from the wording. `name_prefix` narrows to one tool namespace ("email."). Bounded; never raises.
    """
    session = _text(session_id)
    if not session:
        return []
    prefix = _text(name_prefix)
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                sql = "SELECT * FROM execution_facts WHERE session_id = ?"
                args: list[Any] = [session]
                if prefix:
                    sql += " AND substr(name, 1, ?) = ?"
                    args.extend([len(prefix), prefix])
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(max(1, min(int(limit), 200)))
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        return [_fact_from_row(row) for row in rows]
    except Exception:
        return []


# --------------------------------------------------------------------------------------------
# Derived views. Each is a projection of the SAME facts -- never a second store, never a second
# predicate. Two views can disagree only if they read different sources, so they read one.
# --------------------------------------------------------------------------------------------


def executed_tools(turn_key: str, *, session_id: str = "") -> list[dict[str, Any]]:
    """Ground truth for the signed honesty ledger: what tools actually ran on THIS turn.

    Turn-scoped on purpose. The previous implementation read the last 8 tool receipts for the whole
    SESSION, so a turn could be signed as backed by a tool that ran several turns earlier -- a
    receipt that is worse than an empty one, because it looks like evidence.
    """
    return [
        {"tool": fact.name, "status": fact.status, "receipt_key": fact.fact_id}
        for fact in facts_for_turn(turn_key, session_id=session_id)
        if fact.in_category(KIND_TOOL) and fact.ok
    ]


def turn_ran_tool(turn_key: str, *, session_id: str = "") -> bool:
    """Whether this turn executed any EXPLICIT tool -- what Activity's "No tool ran" derives from.

    Category-scoped, not kind-scoped, so a typed `web.search` counts through either face of its
    fact -- and a receipt-only retrieval (the research lane, which invokes no tool) does NOT: that
    is :func:`turn_ran_retrieval`'s answer, and pretending it was a tool would be the same
    conflation this module exists to remove, in the flattering direction.
    """
    return any(fact.in_category(KIND_TOOL) for fact in facts_for_turn(turn_key, session_id=session_id))


def turn_ran_retrieval(turn_key: str, *, session_id: str = "") -> bool:
    """Whether governed retrieval actually executed this turn (a refused fetch never ran)."""
    return any(
        fact.in_category(KIND_RETRIEVAL) and fact.outcome != OUTCOME_REFUSED
        for fact in facts_for_turn(turn_key, session_id=session_id)
    )


def turn_ran_effect(turn_key: str, *, session_id: str = "") -> bool:
    """Whether a governed local effect actually executed this turn (a denied mutation never ran)."""
    return any(
        fact.in_category(KIND_EFFECT) and fact.outcome != OUTCOME_REFUSED
        for fact in facts_for_turn(turn_key, session_id=session_id)
    )


def model_calls(turn_key: str, *, session_id: str = "") -> int:
    """How many model calls this turn actually made, from the same facts as everything else."""
    return sum(1 for fact in facts_for_turn(turn_key, session_id=session_id) if fact.kind == KIND_MODEL)


def _operation_view(fact: ExecutionFact) -> dict[str, Any]:
    return {
        "name": fact.name,
        "kind": fact.kind,
        "categories": list(fact.categories or (fact.kind,)),
        "ok": fact.ok,
        "status": fact.status,
        "fact_id": fact.fact_id,
    }


def turn_execution_summary(turn_key: str, *, session_id: str = "") -> dict[str, Any]:
    """One shape carrying every derived answer, so a caller cannot take half of them from elsewhere.

    The legacy fields (`ran_tool`, `tool_count`, `executed_tools`, ...) keep their exact meaning and
    are DERIVED from the same canonical classification as the new ones -- never computed from a
    second source, which is how the fields would start disagreeing again. `witness` carries the
    independent gate's verdict so a consumer holding this summary can fail closed instead of
    reporting "nothing ran" over a turn whose event stream proves otherwise.
    """
    facts = facts_for_turn(turn_key, session_id=session_id)
    tools = [fact for fact in facts if fact.in_category(KIND_TOOL)]
    models = [fact for fact in facts if fact.kind == KIND_MODEL]
    verdict = verify_execution_truth(turn_key, session_id=session_id)
    return {
        "schema": SCHEMA,
        "turn_key": turn_key,
        "ran_model": bool(models),
        "ran_tool": bool(tools),
        "ran_retrieval": any(
            fact.in_category(KIND_RETRIEVAL) and fact.outcome != OUTCOME_REFUSED for fact in facts
        ),
        "ran_effect": any(
            fact.in_category(KIND_EFFECT) and fact.outcome != OUTCOME_REFUSED for fact in facts
        ),
        "tool_count": len(tools),
        "tool_names": sorted({fact.name for fact in tools}),
        "executed_tools": [
            {"tool": fact.name, "status": fact.status, "receipt_key": fact.fact_id}
            for fact in tools
            if fact.ok
        ],
        "failed_tools": [fact.name for fact in tools if not fact.ok],
        "model_calls": len(models),
        "model_names": sorted({fact.name for fact in models}),
        # Receipt-backed records, counted by their PRIMARY kind so one physical fetch is one
        # retrieval even when its typed invocation also produced a dual-category tool fact.
        "retrieval_count": sum(1 for fact in facts if fact.kind == KIND_RETRIEVAL),
        "effect_count": sum(1 for fact in facts if fact.kind == KIND_EFFECT),
        "executed_operations": [
            _operation_view(fact) for fact in facts if fact.outcome == OUTCOME_EXECUTED
        ],
        "failed_operations": [_operation_view(fact) for fact in facts if fact.outcome == OUTCOME_FAILED],
        "refused_operations": [_operation_view(fact) for fact in facts if fact.outcome == OUTCOME_REFUSED],
        "fact_count": len(facts),
        "witness": {
            "consistent": verdict.consistent,
            "missing": list(verdict.missing_qualified),
        },
    }


# --------------------------------------------------------------------------------------------
# The truth gate.
# --------------------------------------------------------------------------------------------


def witnessed_executions(turn_key: str, session_id: str) -> list[tuple[str, str]]:
    """What an INDEPENDENT witness saw this turn execute: (kind, name).

    The witness is the raw runtime event stream, written by `append_runtime_event` on a different
    code path for a different purpose (driving the live Activity feed). It is deliberately NOT
    derived from the fact ledger: a gate that checks a source against itself proves nothing, and a
    gate that checks the derived views against each other proves nothing either, because they all
    read the one source and would agree while being wrong together.
    """
    turn = _text(turn_key)
    session = _text(session_id)
    if not turn or not session:
        return []
    try:
        from core.runtime_continuity import list_runtime_session_events

        events = list_runtime_session_events(session, limit=400)
    except Exception:
        return []
    seen: list[tuple[str, str]] = []
    for event in events:
        kind_name = _text(event.get("event_type"))
        if kind_name not in _EXECUTION_EVENT_RULES:
            continue
        if resolve_turn_key(None, dict(event)) != turn:
            continue
        # The witness interprets the event through the SAME classifier the recorder used, so the
        # two cannot hold different opinions of what counts as an execution -- the only thing the
        # gate is allowed to catch is a record that never happened, not a vocabulary drift.
        for spec in classify_runtime_event(kind_name, dict(event)):
            seen.append((spec.kind, spec.name))
    return seen


def _file_evidence_corruption_fault(
    turn: str, session_id: str, missing_qualified: tuple[str, ...]
) -> None:
    """File the typed fault for a witness check this gate just failed. Best-effort, never raises.

    The gate OWNS this mapping: it is the only place where the ledger and the independent
    witness are compared, so it is the only place allowed to declare this turn's execution
    evidence unreliable. The recording reuses the fault plane's identity helper, so the
    fault row joins to this turn's execution facts by construction.
    """
    try:
        from core.faults.recorder import record_fault
        from core.faults.records import FaultRecord

        record_fault(
            FaultRecord.for_code(
                "evidence_corruption",
                authority="core.execution_truth",
                turn_key=turn,
                session_id=session_id,
                dedupe=turn,
                evidence_refs=tuple(missing_qualified),
                context={
                    "status": "witness_check_failed",
                    "missing_count": len(missing_qualified),
                    "operation": "verify_execution_truth",
                },
            )
        )
    except Exception:
        # The gate's verdict is strict; the fault RECORDING stays observability.
        pass


def verify_execution_truth(turn_key: str, *, session_id: str = "") -> TruthVerdict:
    """Fail when a real execution never became an authoritative fact.

    This is the load-bearing check. Dropping a `record_execution` call cannot be caught by comparing
    the derived views -- they all read the ledger, so they would agree on the same wrong answer. It
    is caught here, by holding the ledger against a witness that was written independently.
    """
    turn = _text(turn_key)
    if not turn:
        return TruthVerdict(True, "", 0, 0, (), "no turn identity to verify")
    facts = facts_for_turn(turn, session_id=session_id)
    recorded = {(fact.kind, fact.name) for fact in facts}
    witnessed = witnessed_executions(turn, session_id)
    missing_pairs = sorted({pair for pair in witnessed if pair not in recorded})
    if missing_pairs:
        missing = tuple(sorted({name for _, name in missing_pairs}))
        qualified = tuple(f"{kind}:{name}" for kind, name in missing_pairs)
        _file_evidence_corruption_fault(turn, _text(session_id), qualified)
        return TruthVerdict(
            consistent=False,
            turn_key=turn,
            recorded=len(recorded),
            witnessed=len(set(witnessed)),
            missing=missing,
            detail=(
                f"{len(missing)} execution(s) observed by the runtime event stream have no "
                f"authoritative execution fact: {', '.join(missing)}"
            ),
            missing_qualified=qualified,
        )
    return TruthVerdict(
        consistent=True,
        turn_key=turn,
        recorded=len(recorded),
        witnessed=len(set(witnessed)),
        detail="every witnessed execution has an authoritative fact",
    )


def note_runtime_event(
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    session_id: str,
    details: dict[str, Any] | None,
) -> str:
    """Turn a runtime event into an authoritative fact when the event IS an execution.

    Called from the one place every runtime event passes through, so a new tool call site records a
    fact by existing rather than by remembering to. That is the whole reason the hook lives at the
    choke point instead of at the sixteen emitters: the emitters are where the shapes diverged.
    """
    detail = dict(details or {})
    turn = resolve_turn_key(source_context, detail)
    if not turn:
        return ""
    recorded = ""
    for spec in classify_runtime_event(_text(event_type), detail):
        fact_id = record_execution(
            session_id=session_id,
            turn_key=turn,
            kind=spec.kind,
            name=spec.name,
            ok=spec.ok,
            status=spec.status,
            detail=spec.detail,
            categories=spec.categories,
            dedupe=spec.dedupe,
        )
        recorded = fact_id or recorded
    return recorded


__all__ = [
    "KIND_EFFECT",
    "KIND_MODEL",
    "KIND_RETRIEVAL",
    "KIND_TOOL",
    "OUTCOME_EXECUTED",
    "OUTCOME_FAILED",
    "OUTCOME_REFUSED",
    "SCHEMA",
    "EventRule",
    "ExecutionFact",
    "ExecutionSpec",
    "TruthVerdict",
    "classify_runtime_event",
    "executed_tools",
    "facts_for_turn",
    "model_calls",
    "note_runtime_event",
    "recent_session_facts",
    "record_execution",
    "resolve_turn_key",
    "turn_execution_summary",
    "turn_ran_effect",
    "turn_ran_retrieval",
    "turn_ran_tool",
    "verify_execution_truth",
    "witnessed_executions",
]
