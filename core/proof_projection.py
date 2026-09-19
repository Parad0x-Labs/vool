"""The Proof Chip's projection: one served turn's evidence, read and rendered honestly.

STRICTLY READ-ONLY. This module projects evidence that already exists — it never invents,
repairs, or writes any. It has no writers: every store it touches is opened for reading through
the seams that own them (`core.persistent_memory` for the binding row, `core.finalization` for
the committed truth, `core.execution_truth` for the authoritative execution ledger and its
independent witness, `core.runtime_continuity` for the raw event stream).

BINDING — the property the module exists to enforce. Evidence is addressed by the canonical
(session_id, request_id) pair: the same pair one conversation-log row carries for one served
turn. A query that does not join to exactly that row is refused (`ProofNotBound`) — never
answered from "the latest record", because latest-record binding is how one turn gets dressed
in another turn's work. Every subsequent read is keyed by the resolved turn identities of THAT
request and filtered by THAT session, so cross-turn and cross-session evidence cannot enter the
projection.

STATES — the honest vocabulary the chip renders:
* VERIFIED    — the finalization's own integrity re-verifies (stored bytes hash to the committed
                digest, payload available) AND no expected terminal evidence is missing AND the
                execution witness agrees with the ledger.
* RECORDED    — evidence rows exist, but integrity is not proven: no governing finalization, an
                unavailable (withheld/erased) payload, a hash mismatch, or an ambiguous replay.
* INCOMPLETE  — expected terminal evidence is missing: a retrieval that started and never
                reported completed/failed, or a witnessed execution with no authoritative fact.
* UNVERIFIED  — no trustworthy proof exists at all for the bound turn.
Row presence alone never produces VERIFIED — a corrupt row is RECORDED, and a wounded ledger is
INCOMPLETE, whatever the hash says.
These states certify record integrity and recorded execution coverage, not independent
correctness of every factual or numerical claim in the answer.

REDACT-AT-PROJECTION — local personal paths and high-confidence secrets are masked in every
string the projection returns. The stored rows are never modified by this module, so the
redaction is provably a display concern: the stored evidence stays byte-identical.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

SCHEMA = "vool.turn_proof.v1"

STATE_VERIFIED = "VERIFIED"
STATE_RECORDED = "RECORDED"
STATE_INCOMPLETE = "INCOMPLETE"
STATE_UNVERIFIED = "UNVERIFIED"

#: Retrieval families with a real started→terminal vocabulary in the event stream. A started
#: event whose family shows no terminal row is missing terminal evidence — the one INCOMPLETE
#: signal the event stream can give without re-deriving outcomes from prose.
_STARTED_TERMINAL_FAMILIES: dict[str, tuple[str, ...]] = {
    "web_retrieval": ("web_retrieval_completed", "web_retrieval_failed"),
    "fx_retrieval": ("fx_retrieval_completed", "fx_retrieval_failed"),
}

_NODE_KIND_OBSERVATION = "observation"
_NODE_KIND_DERIVED = "derived"
_NODE_KIND_OTHER = "other"

#: Receipt statuses that mean the lookup did NOT deliver. Any other status on a recorded receipt
#: is a lookup that completed -- an empty result set is a completed lookup, not a failed one.
_RECEIPT_FAILURE_STATUSES = frozenset(
    {
        "failed", "failure", "error", "errored", "timeout", "timed_out", "unavailable",
        "unreachable", "refused", "blocked", "denied", "rejected", "cancelled", "canceled",
    }
)

_EVENT_WINDOW = 400

_HOME_PATH = os.path.expanduser("~").rstrip("/")
_HOME_IN_OTHERS = re.compile(r"(?P<sep>^|[\s\"'=(:])(/(?P<who>Users|home)/(?P<name>[A-Za-z0-9._-]+))/")


class ProofNotBound(LookupError):
    """The (session_id, request_id) pair does not join to ONE served turn.

    Raised instead of guessing: the caller may have a mistyped id, a stale one, or an id from
    another session — and every one of those must look identical from the outside (refused),
    because answering any of them from SOME row is the cross-turn leak this module exists to
    make impossible.
    """


def _redact_text(value: Any) -> Any:
    """Mask personal absolute paths and high-confidence secrets in a PROJECTED string.

    Stored evidence is never modified; this runs on the way out only. A non-string passes
    through unchanged so the projection's shape stays intact.
    """
    if not isinstance(value, str):
        return value
    text = value
    if _HOME_PATH and _HOME_PATH != "/" and text.startswith(_HOME_PATH):
        text = "~" + text[len(_HOME_PATH):]
    text = _HOME_IN_OTHERS.sub(lambda m: f"{m.group('sep')}~", text)
    from core.secret_redaction import redact_secrets

    return redact_secrets(text)


def _redact_tree(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_tree(item) for item in value]
    return value


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _find_binding_row(session_id: str, request_id: str) -> dict[str, Any] | None:
    """The ONE conversation-log row that binds this session to this request id.

    This is the join that makes every other read bound: without it there is no served turn
    here, and the projection refuses rather than falling back to any other row.
    """
    from core.memory.files import conversation_log_path, load_jsonl

    try:
        rows = load_jsonl(conversation_log_path())
    except Exception:
        return None
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if _clean(row.get("session_id")) == session_id and _clean(row.get("request_id")) == request_id:
            return row
    return None


def _read_finalization(request_id: str, principal: str) -> dict[str, Any]:
    """The committed truth for this request id, with honest reasons when it cannot prove itself.

    Never raises for absent/unreadable truth: an absent row is one reading of the world
    (legacy turn), an unreadable store is another (cannot verify) — both must name themselves
    instead of collapsing into a checkmark.
    """
    reasons: list[str] = []
    row: dict[str, Any] | None = None
    try:
        from core.finalization import get_finalization_by_request_id

        row = get_finalization_by_request_id(request_id, principal=principal or "owner_local")
    except Exception as exc:
        name = type(exc).__name__
        if name == "ReplayAmbiguityRefused":
            reasons.append("ambiguous_finalization")
        else:
            reasons.append("finalization_store_unreadable")
    return {"row": row, "reasons": reasons}


def _load_session_events(session_id: str) -> list[dict[str, Any]]:
    try:
        from core.runtime_continuity import list_recent_runtime_session_events

        return list(list_recent_runtime_session_events(session_id, limit=_EVENT_WINDOW))
    except Exception:
        return []


def _resolve_turn_keys(
    *,
    explicit: str,
    finalization_row: dict[str, Any] | None,
    events: list[dict[str, Any]],
    request_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Every turn identity THIS request is known by, and the events that belong to it.

    Turn keys come from server-stamped identities only: the caller's explicit key, the
    finalization's own turn id, and the turn keys of events that carry THIS request id in
    their details. Events join when their stamped identity is one of those keys or when they
    themselves carry the request id — never by recency.
    """
    candidates: list[str] = []
    for value in (explicit, _clean((finalization_row or {}).get("turn_id"))):
        if value and value not in candidates:
            candidates.append(value)
    for event in events:
        if _clean(event.get("request_id")) != request_id:
            continue
        key = _clean(event.get("turn_key"))
        if key and key not in candidates:
            candidates.append(key)
    turn_events = [
        event
        for event in events
        if _clean(event.get("request_id")) == request_id
        or (_clean(event.get("turn_key")) and _clean(event.get("turn_key")) in candidates)
    ]
    return candidates, turn_events


#: Attempt lifecycles that are terminal but NOT fulfilment, as the completion event names them.
_UNFULFILLED_ATTEMPT_STATES = (
    "PARTIAL_SUCCESS", "FAILED_TOOL", "FAILED_PROVIDER", "FAILED_SYNTHESIS", "FAILED_VALIDATION",
    "UNSUPPORTED_ENTITY", "PENDING_RECONCILIATION", "ABANDONED",
)
_UNFULFILLED_EVENT_TYPES: dict[str, str] = {
    "task_failed": "task_failed",
    "ambiguity_adjudication_unresolved": "unresolved_adjudication",
    "stale_result_rejected": "synthesis_rejected",
}
_UNANSWERED_LEDGER_MARKERS = ("could not be answered:", "not answered:", "unavailable:")


def _fulfilment_gaps(turn_events: list[dict[str, Any]], fin_row: Any) -> list[str]:
    """Reason codes for work the turn's OWN record says did not happen, in event order."""
    gaps: list[str] = []

    def note(code: str) -> None:
        if code not in gaps:
            gaps.append(code)

    # A turn that resumed after an approval round-trip writes SEVERAL trace_completed rows for
    # the same turn key: the earlier ones are superseded attempts (blocked on the approval the
    # operator then granted), and the LAST row is the turn's terminal truth. Reading every row
    # let a superseded "blocked" trace INCOMPLETE a turn that went on to fulfil (measured on
    # the first-run pact's task receipt claim).
    last_trace_index = max(
        (
            index
            for index, event in enumerate(turn_events)
            if _clean(event.get("event_type")) == "turn.trace_completed"
        ),
        default=-1,
    )
    for index, event in enumerate(turn_events):
        event_type = _clean(event.get("event_type"))
        if event_type in _UNFULFILLED_EVENT_TYPES:
            note(_UNFULFILLED_EVENT_TYPES[event_type])
        if event_type == "turn.trace_completed" and index != last_trace_index:
            continue
        if event_type == "turn.trace_completed":
            from core.runtime_task_outcome import output_validation_outcome

            terminal = event.get("fulfillment_outcome")
            if isinstance(terminal, dict) and terminal.get("fulfillment_status") in {
                "failed", "blocked", "partially_fulfilled", "cancelled",
            }:
                note("terminal_task_unfulfilled")
            outcome = output_validation_outcome(event.get("response_control"))
            if outcome and outcome.get("fulfillment_status") != "fulfilled":
                note("output_validation_unfulfilled")
        if event_type == "runtime_attempt_completed":
            message = _clean(event.get("message"))
            state = _clean(event.get("lifecycle_state"))
            if not state and "->" in message:
                state = message.rsplit("->", 1)[-1].strip().rstrip(".").strip()
            if state in _UNFULFILLED_ATTEMPT_STATES:
                note("attempt_unfulfilled")
        if event_type == "task_completed":
            status = _clean(event.get("status"))
            if status in ("ambiguity_adjudication_unresolved", "demand_owned_mixed_turn_failed", "demand_owned_mixed_turn_degraded"):
                note("unfulfilled_route")
    try:
        published = str((fin_row or {}).get("canonical_content") or "").casefold()
    except Exception:
        published = ""
    if published and any(marker in published for marker in _UNANSWERED_LEDGER_MARKERS):
        note("unanswered_rows")
    if published:
        from core.incomplete_answer import inspect_published_answer_completeness

        completeness = inspect_published_answer_completeness(
            str((fin_row or {}).get("canonical_content") or "")
        )
        if completeness.incomplete:
            note("incomplete_answer")
    return gaps


def _missing_terminals(turn_events: list[dict[str, Any]]) -> bool:
    for prefix, terminals in _STARTED_TERMINAL_FAMILIES.items():
        started = 0
        terminal = 0
        for event in turn_events:
            event_type = _clean(event.get("event_type"))
            if event_type == f"{prefix}_started":
                started += 1
            elif event_type in terminals:
                terminal += 1
        if started > terminal:
            return True
    return False


def _iso_ms(first: str, last: str) -> int | None:
    try:
        start = datetime.fromisoformat(first)
        end = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return None
    delta = (end - start).total_seconds() * 1000
    return int(delta) if delta >= 0 else None


def _host_of(url: Any) -> str:
    try:
        return urlparse(str(url or "")).hostname or ""
    except Exception:
        return ""


_MULTI_PART_TLDS = frozenset({"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.jp", "co.nz", "com.br", "co.in", "co.za", "com.sg", "com.hk", "com.tr", "com.mx", "com.ar"})


def _source_identity(host: str) -> str:
    """One source per organisation: the registrable domain of a host.

    `query1.finance.yahoo.com`, `finance.yahoo.com` and `www.yahoo.com` are one source (Yahoo);
    `api.coingecko.com` and `www.coingecko.com` are one (CoinGecko). A receipt that names the API
    endpoint and a node that names the human page are the SAME lookup's source, not two.
    """
    labels = [part for part in str(host or "").lower().strip(".").split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    tail = ".".join(labels[-2:])
    if tail in _MULTI_PART_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail


def _node_kind(operation: str, *, observed: bool, depends_on: list[str]) -> str:
    """What a node WAS, read from the operation's own declared effect when the registry knows it.

    Observation: the node read the world (a live quote, a weather reading, a clock). Derived: the
    node computed over other nodes' results (arithmetic, a comparison). Neither is inferred from
    prose; when the registry cannot be consulted the row's own shape decides -- a named source is
    an observation, a dependency list is a derivation -- and anything else stays `other`.
    """
    try:
        from core.conductor.evidence import _effect, operation_is_an_observation
        from core.conductor.operations import OperationEffect

        if operation and operation_is_an_observation(operation):
            return _NODE_KIND_OBSERVATION
        effect = _effect(operation) if operation else None
        if effect in {OperationEffect.COMPUTED_VALUE, OperationEffect.DERIVED_ANALYSIS}:
            return _NODE_KIND_DERIVED
        if effect is not None:
            return _NODE_KIND_OTHER
    except Exception:
        pass
    if observed:
        return _NODE_KIND_OBSERVATION
    if depends_on:
        return _NODE_KIND_DERIVED
    return _NODE_KIND_OTHER


def _coverage_of(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and the one label the chip shows beside the state word.

    The label says what was LOOKED UP and whether every derived step was fed by lookups of this
    turn. It never restates the integrity state and never claims a lookup that did not complete.
    """
    lookups = [row for row in observations if row.get("kind") == _NODE_KIND_OBSERVATION]
    derived = [row for row in observations if row.get("kind") == _NODE_KIND_DERIVED]
    succeeded = {str(row.get("node_id")) for row in observations if row.get("ok")}
    for row in derived:
        deps = [str(dep) for dep in (row.get("depends_on") or [])]
        row["bound"] = bool(row.get("ok")) and all(dep in succeeded for dep in deps)
    total = len(lookups)
    pending = sum(1 for row in lookups if row.get("pending"))
    ok_count = sum(1 for row in lookups if row.get("ok"))
    failed = total - pending - ok_count
    cached = sum(1 for row in lookups if row.get("cached"))
    derived_failed = sum(1 for row in derived if not row.get("ok"))
    unbound = sum(1 for row in derived if row.get("ok") and not row.get("bound"))
    noun = "lookup" if total == 1 else "lookups"
    if total == 0:
        label = "no lookups"
    elif pending:
        label = f"{pending} of {total} {noun} pending"
    elif failed:
        label = f"{failed} of {total} {noun} failed"
    else:
        label = f"{ok_count}/{total} {noun}"
    # A derived step that FAILED (or never ran) is named as failed, distinct from one that ran
    # but whose operand is not a lookup of this turn (unbound). The two were conflated: a
    # deadline-expired derivation counted as "unbound", which reads as a provenance gap rather
    # than an unanswered ask.
    if derived_failed:
        label += f" · {derived_failed} of {len(derived)} derived step{'s' if len(derived) != 1 else ''} failed"
    if unbound:
        label += f" · {unbound} derived step{'s' if unbound != 1 else ''} unbound"
    return {
        "observations": {"total": total, "succeeded": ok_count, "failed": failed, "cached": cached, "pending": pending},
        "derived": {
            "total": len(derived),
            "bound": len(derived) - unbound - derived_failed,
            "unbound": unbound,
            "failed": derived_failed,
        },
        "label": label,
    }


#: Stage windows read from event identities the runtime already emits. Each window is the span
#: between the FIRST stamp of its opening events and the LAST stamp of its closing events; a stage
#: none of whose events occurred has no row. Nothing here is measured by this module -- no
#: first-token time, no wall clock of its own -- it only subtracts stamps that exist.
_TIMELINE_STAGES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("total", ("task_received", "task_resumed"), ("task_completed", "task_failed", "task_cancelled")),
    ("classification", ("task_received", "task_resumed"), ("task_classified",)),
    ("retrieval", ("web_retrieval_started", "agent_node_started"), ("web_retrieval_completed", "web_retrieval_failed", "agent_node_completed")),
    ("synthesis", ("model.call_started",), ("model.call_completed", "model.call_failed")),
)


def _timeline_of(turn_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stamps: dict[str, list[str]] = {}
    for event in turn_events:
        created = _clean(event.get("created_at"))
        kind = _clean(event.get("event_type"))
        if created and kind:
            stamps.setdefault(kind, []).append(created)
    rows: list[dict[str, Any]] = []
    for stage, opening, closing in _TIMELINE_STAGES:
        starts = [stamp for kind in opening for stamp in stamps.get(kind, ())]
        ends = [stamp for kind in closing for stamp in stamps.get(kind, ())]
        if not starts or not ends:
            continue
        started_at, ended_at = min(starts), max(ends)
        rows.append({"stage": stage, "started_at": started_at, "ended_at": ended_at, "ms": _iso_ms(started_at, ended_at)})
    return rows


def _projection_of_events(turn_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Model/provider, cost, source and lookup readings from the turn's OWN event rows."""
    model = ""
    provider = ""
    tokens: int | None = None
    usage_details: list[dict[str, Any]] = []
    seen_usage: set[str] = set()
    sources: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    started_nodes: dict[str, str] = {}
    receipt_nodes: dict[str, dict[str, Any]] = {}
    claims: dict[str, Any] | None = None
    stamps: list[str] = []
    for event in turn_events:
        created = _clean(event.get("created_at"))
        if created:
            stamps.append(created)
        event_type = _clean(event.get("event_type"))
        if event_type == "agent_node_started":
            node_id = _clean(event.get("node_id"))
            if node_id:
                started_nodes.setdefault(node_id, _clean(event.get("operation")))
        if event_type == "conductor_plan_completed":
            receipt = event.get("receipt") if isinstance(event.get("receipt"), dict) else {}
            for entry in receipt.get("nodes") or []:
                if not isinstance(entry, dict):
                    continue
                node_id = _clean(entry.get("node_id"))
                if node_id:
                    receipt_nodes.setdefault(node_id, dict(entry))
        if event_type == "agent_node_completed":
            # The conductor's and the live-data lane's own completion rows: what each node read
            # (`observed`: bounded values + the source it named), whether it succeeded, what it
            # depended on, how long it took. One row per node id; the first completion stands.
            node_id = _clean(event.get("node_id"))
            if node_id and node_id not in seen_nodes:
                seen_nodes.add(node_id)
                operation = _clean(event.get("operation"))
                observed = event.get("observed") if isinstance(event.get("observed"), dict) else {}
                raw_deps = event.get("depends_on")
                depends_on = [_clean(dep) for dep in raw_deps if _clean(dep)] if isinstance(raw_deps, list) else []
                ok = bool(event.get("ok"))
                host = _clean(observed.get("origin_domain")) or _host_of(observed.get("source_url"))
                duration = event.get("duration_s")
                row = {
                    "node_id": node_id,
                    "operation": operation,
                    "kind": _node_kind(operation, observed=bool(observed), depends_on=depends_on),
                    "ok": ok,
                    "pending": False,
                    "state": _clean(event.get("state")),
                    "failure_code": _clean(event.get("failure_code")) if not ok else "",
                    "host": host if ok else "",
                    "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
                    "depends_on": depends_on,
                    "cached": bool(event.get("cached") or event.get("cache_hit")),
                }
                observations.append(row)
                if ok and row["kind"] == _NODE_KIND_OBSERVATION and host:
                    sources.append({"host": host, "provider": operation, "status": "observed", "operation": operation, "subtask_id": node_id})
        if not model:
            candidate = _clean(event.get("model_id")) or _clean(event.get("model_name"))
            if event_type.startswith("model_") and candidate:
                model = candidate
        if not provider:
            candidate = _clean(event.get("provider_id"))
            if event_type.startswith("model_") and candidate:
                provider = candidate
        if event_type == "model_usage":
            identity = _clean(event.get("response_id")) or _clean(event.get("model_call_id"))
            if identity and identity in seen_usage:
                continue
            if identity:
                seen_usage.add(identity)
            snapshot = event.get("turn_usage_details")
            if isinstance(snapshot, list) and snapshot:
                usage_details = [dict(d) for d in snapshot if isinstance(d, dict)]
            try:
                counted = int(event.get("prompt_tokens") if event.get("prompt_tokens") is not None else event.get("input_tokens") or 0) + int(event.get("output_tokens") or 0)
            except (TypeError, ValueError):
                counted = 0
            if counted:
                tokens = (tokens or 0) + counted
        if event_type in {"web_retrieval_completed", "fx_retrieval_completed"}:
            # Two receipt shapes share these event names: a flat typed receipt
            # (`vool.web_retrieval_receipt.v1`: provider_id, source_domains, ...) and the
            # live-data plan's nested `{receipts: [...]}`. Both are read as what they are —
            # sources this turn actually retrieved from — never reconstructed from prose.
            flat = _clean(event.get("schema")) == "vool.web_retrieval_receipt.v1"
            receipt_rows: list[dict[str, Any]] = []
            if flat:
                receipt_rows.append(dict(event))
            else:
                nested = event.get("receipts")
                if isinstance(nested, list):
                    receipt_rows = [dict(item) for item in nested if isinstance(item, dict)]
            for receipt in receipt_rows:
                domains = receipt.get("source_domains")
                host = ""
                if isinstance(domains, list) and domains:
                    host = _clean(domains[0])
                if not host:
                    host = _host_of(receipt.get("url") or receipt.get("source"))
                sources.append(
                    {
                        "host": host,
                        "provider": _clean(receipt.get("provider_id") or receipt.get("provider_label")),
                        "status": _clean(receipt.get("status")),
                        "operation": _clean(receipt.get("action") or receipt.get("operation")),
                        # The plan node this receipt belongs to, when the receipt names it: the
                        # projection joins it to the node's own completion row instead of
                        # counting one lookup as two sources.
                        "subtask_id": _clean(receipt.get("subtask_id")),
                    }
                )
        if event_type == "grounding_publication" and claims is None:
            record = event.get("record") if isinstance(event.get("record"), dict) else {}
            publication = (
                event.get("publication")
                if isinstance(event.get("publication"), dict)
                else record.get("publication") if isinstance(record.get("publication"), dict) else {}
            )
            if publication:
                claims = {
                    "evidence_set_id": _clean(publication.get("evidence_set_id")),
                    "supported_claim_count": int(publication.get("supported_claim_count") or 0),
                    "withheld_claims": [
                        _redact_text(item) for item in (publication.get("withheld_claims") or []) if item
                    ],
                    "claim_support": _redact_tree(publication.get("claim_support") or {}),
                }
    # The plan receipt is the ledger of EVERY node the plan held, including the ones that never
    # started -- a derivation that waited for a generation slot until the plan deadline expired
    # leaves no started/completed row of its own. Measured on the owner's turn (2026-09-10,
    # 772256a7): four derivations failed at the deadline and the chip read "3/3 lookups · 1 derived
    # step unbound", because only the one that had started was visible. A receipt row is read the
    # same way a completion row is; a node that also completed keeps its own row.
    for node_id, entry in receipt_nodes.items():
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        operation = _clean(entry.get("operation")) or started_nodes.get(node_id, "")
        raw_deps = entry.get("depends_on")
        depends_on = [_clean(dep) for dep in raw_deps if _clean(dep)] if isinstance(raw_deps, list) else []
        ok = bool(entry.get("ok"))
        duration = entry.get("duration_s")
        observations.append(
            {
                "node_id": node_id,
                "operation": operation,
                "kind": _node_kind(operation, observed=False, depends_on=depends_on),
                "ok": ok,
                "pending": False,
                "state": "succeeded" if ok else ("never_started" if node_id not in started_nodes else "failed"),
                "failure_code": _clean(entry.get("failure_code")) if not ok else "",
                "host": "",
                "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
                "depends_on": depends_on,
                "cached": False,
            }
        )
    for node_id, operation in started_nodes.items():
        if node_id in seen_nodes:
            continue
        # Started, never completed: a pending lookup is reported as pending, never as a source.
        observations.append({
            "node_id": node_id,
            "operation": operation,
            "kind": _node_kind(operation, observed=False, depends_on=[]) if operation else _NODE_KIND_OBSERVATION,
            "ok": False,
            "pending": True,
            "state": "pending",
            "failure_code": "",
            "host": "",
            "duration_s": None,
            "depends_on": [],
            "cached": False,
        })
    # Receipts that name no node the projection holds are lookups of this turn as well: the
    # live-info fast path's quote fetch, the research lane's page reads. Measured on the built
    # candidate 60a91da3 (2026-09-07): "4 sources · no lookups" on a turn that read seven pages,
    # and "1/1 lookup" on a turn that fetched gold through the fast path and silver through a node.
    held = {str(row.get("node_id")) for row in observations}
    for index, source in enumerate(sources):
        subtask = _clean(source.get("subtask_id"))
        if subtask and subtask in held:
            continue
        host = _clean(source.get("host"))
        status = _clean(source.get("status")).lower()
        if not host and not status:
            continue
        ok = status not in _RECEIPT_FAILURE_STATUSES
        observations.append(
            {
                "node_id": f"receipt:{index}",
                "operation": _clean(source.get("operation")) or _clean(source.get("provider")) or "retrieval",
                "kind": _NODE_KIND_OBSERVATION,
                "ok": ok,
                "pending": False,
                "state": status or ("succeeded" if ok else "failed"),
                "failure_code": status if not ok else "",
                "host": host if ok else "",
                "duration_s": None,
                "depends_on": [],
                "cached": False,
            }
        )
    # Unique sources: one identity per organisation actually read (registrable domain). A receipt
    # and a node completion that name the same subtask are one lookup: the receipt row is kept in
    # the list (it is a real record) but contributes no second identity. A row without any host
    # keeps its own identity so it is never collapsed into another.
    node_ids = {str(row.get("node_id")) for row in observations}
    identities: set[str] = set()
    for index, source in enumerate(sources):
        host = _clean(source.get("host"))
        subtask = _clean(source.get("subtask_id"))
        if not host and subtask and subtask in node_ids:
            continue  # the node's own row already carries this lookup's identity
        identities.add(_source_identity(host) if host else f"receipt:{index}:{_clean(source.get('provider'))}:{_clean(source.get('operation'))}")
    elapsed_ms = _iso_ms(min(stamps), max(stamps)) if len(set(stamps)) >= 2 else None
    return {
        "model": model,
        "provider": provider,
        "tokens": (sum(int(d["input_tokens"]) + int(d["output_tokens"]) for d in usage_details)
                   if usage_details and all(d.get("input_tokens") is not None and d.get("output_tokens") is not None for d in usage_details)
                   else tokens),
        "usage_details": usage_details,
        "sources": sources,
        "unique_sources": len(identities),
        "observations": observations,
        "coverage": _coverage_of(observations),
        "claims": claims,
        "elapsed_ms": elapsed_ms,
        "timeline": _timeline_of(turn_events),
    }


def build_turn_proof(
    *,
    session_id: str,
    request_id: str,
    principal: str = "owner_local",
    turn_key: str = "",
) -> dict[str, Any]:
    """Project one bound turn's proof. Raises ProofNotBound when the pair binds to nothing."""
    session = _clean(session_id)
    request = _clean(request_id)
    if not session or not request:
        raise ProofNotBound("session_id and request_id are both required")
    binding_row = _find_binding_row(session, request)
    if binding_row is None:
        raise ProofNotBound(
            "no served turn binds this session to this request id; refusing to project any evidence"
        )

    reasons: list[str] = []
    finalization = _read_finalization(request, principal)
    reasons.extend(finalization["reasons"])
    fin_row = finalization["row"]

    events = _load_session_events(session)
    turn_keys, turn_events = _resolve_turn_keys(
        explicit=_clean(turn_key), finalization_row=fin_row, events=events, request_id=request
    )

    facts: list[Any] = []
    seen_fact_ids: set[str] = set()
    witness = {"consistent": None, "missing": []}
    if turn_keys:
        from core.execution_truth import facts_for_turn, verify_execution_truth

        for key in turn_keys:
            for fact in facts_for_turn(key, session_id=session):
                if fact.fact_id not in seen_fact_ids:
                    seen_fact_ids.add(fact.fact_id)
                    facts.append(fact)
        verdict = verify_execution_truth(turn_keys[0], session_id=session)
        witness = {"consistent": bool(verdict.consistent), "missing": list(verdict.missing_qualified)}
        if not verdict.consistent:
            reasons.append("witness_inconsistent")

    # The row-presence checks that decide whether VERIFIED is even on the table.
    # Availability is normalized: the store's own constants are uppercase
    # (`core.finalization.AVAILABILITY_AVAILABLE == "AVAILABLE"`), and a case-sensitive
    # comparison mislabeled every live AVAILABLE row as unavailable — found on the wire proof.
    availability = _clean((fin_row or {}).get("availability")).upper() or "AVAILABLE"
    content_hash_ok: bool | None = None
    if fin_row is not None:
        if availability != "AVAILABLE":
            reasons.append("payload_unavailable")
        else:
            stored_bytes = str(fin_row.get("canonical_content") or "")
            stored_hash = _clean(fin_row.get("content_hash"))
            if not stored_hash:
                reasons.append("content_hash_missing")
                content_hash_ok = False
            else:
                actual = "sha256:" + hashlib.sha256(stored_bytes.encode("utf-8")).hexdigest()
                content_hash_ok = actual == stored_hash
                if not content_hash_ok:
                    reasons.append("content_hash_mismatch")
    else:
        reasons.append("no_finalization_row")

    projections = _projection_of_events(turn_events)
    if _missing_terminals(turn_events) or projections["coverage"]["observations"]["pending"]:
        # A retrieval family that started without a terminal, or a conductor / live-data node
        # that started and never completed (matched by node id, never by count): the turn's
        # record is not finished, so its bytes cannot be VERIFIED yet.
        reasons.append("missing_terminal")

    # FULFILMENT GAPS (FINDINGS F14.6 / F15, owner-observed 2026-09-10): the badge read VERIFIED
    # on an incomplete research turn, a partial allocation, a failed chat reply and a mis-bound
    # "why?". Record integrity (the bytes hash-match their finalization row) is necessary for
    # VERIFIED and was the whole test; it is not sufficient. A turn whose own record says part
    # of the work did not happen -- a task_failed row, an unresolved adjudication, a rejected
    # synthesis, an attempt that closed short of SUCCEEDED, a "could not be answered" ledger --
    # is INCOMPLETE, and the reasons name which.
    gaps = _fulfilment_gaps(turn_events, fin_row)
    reasons.extend(gap for gap in gaps if gap not in reasons)

    has_rows = bool(fin_row or facts or turn_events)
    if not has_rows:
        state = STATE_UNVERIFIED
    elif "missing_terminal" in reasons or "witness_inconsistent" in reasons or gaps:
        state = STATE_INCOMPLETE
    elif fin_row is not None and not reasons:
        state = STATE_VERIFIED
    else:
        state = STATE_RECORDED

    def _action_row(fact: Any) -> dict[str, Any]:
        return {
            "name": fact.name,
            "kind": fact.kind,
            "status": fact.status,
            "outcome": fact.outcome,
            "ok": bool(fact.ok),
            "fact_id": fact.fact_id,
        }

    actions = [_action_row(fact) for fact in facts]
    refusals = [_action_row(fact) for fact in facts if fact.outcome == "refused"]
    receipts = [f"fact:{fact.kind}:{fact.name}:{fact.fact_id}" for fact in facts]
    # Every conductor / live-data node is one action the runtime performed for this turn,
    # whether it observed, derived, failed or is still pending -- counted beside the execution
    # facts, never invented from the answer's prose. A node whose execution the fact ledger
    # already holds (the live-data lane files one fact per subtask, carrying the subtask id) is
    # counted ONCE: the union is keyed on runtime-owned identifiers, never on names.
    node_rows = projections["observations"]
    fact_identities: set[str] = set()
    for fact in facts:
        detail = getattr(fact, "detail", None) or {}
        for key in ("subtask_id", "node_id", "receipt_id"):
            value = _clean(detail.get(key)) if isinstance(detail, dict) else ""
            if value:
                fact_identities.add(value)
    node_only_actions = [
        row
        for row in node_rows
        if row["node_id"] not in fact_identities and not str(row["node_id"]).startswith("receipt:")
    ]
    for row in node_rows:
        if str(row["node_id"]).startswith("receipt:"):
            continue  # a receipt-derived lookup row: its receipt is already listed above
        receipts.append(f"node:{row['kind']}:{row['operation']}:{row['node_id']}")
    if fin_row is not None:
        receipts.insert(0, _clean(fin_row.get("finalization_id")))
    if projections["claims"] and projections["claims"].get("evidence_set_id"):
        receipts.append("evidence_set:" + str(projections["claims"]["evidence_set_id"]))

    from core.response_usage_details import usage_display_segments

    proof = {
        "schema": SCHEMA,
        "bound": True,
        "session_id": session,
        "request_id": request,
        "state": state,
        "state_reasons": reasons,
        "verification_scope": "record_integrity",
        "verification_explanation": (
            "Checks saved output integrity and recorded execution evidence. "
            "These checks do not independently verify every factual or numerical claim."
        ),
        "compact": {
            "actions": len(actions) + len(node_only_actions),
            "sources": projections["unique_sources"],
            "cost": {
                "tokens": projections["tokens"],
                "usd": None,
                "display": " · ".join(usage_display_segments(projections["usage_details"])),
                "source": "provider_usage_events" if projections["tokens"] is not None else "",
            },
            "state": state,
            "state_label": "Record " + state.lower(),
            "coverage": projections["coverage"],
        },
        "expanded": {
            "turn_keys": turn_keys,
            "model": _redact_text(projections["model"]),
            "provider": _redact_text(projections["provider"]),
            "actions": _redact_tree(actions),
            "sources": _redact_tree(projections["sources"]),
            "observations": _redact_tree(node_rows),
            "coverage": projections["coverage"],
            "claims": _redact_tree(projections["claims"]),
            "refusals": _redact_tree(refusals),
            "elapsed_ms": projections["elapsed_ms"],
            "timeline": projections["timeline"],
            "receipts": _redact_tree(receipts),
            "finalization": {
                "finalization_id": _clean((fin_row or {}).get("finalization_id")),
                "availability": availability,
                "content_hash_ok": content_hash_ok,
            },
            "witness": witness,
        },
    }
    return _redact_tree(proof)


__all__ = [
    "SCHEMA",
    "STATE_INCOMPLETE",
    "STATE_RECORDED",
    "STATE_UNVERIFIED",
    "STATE_VERIFIED",
    "ProofNotBound",
    "build_turn_proof",
]
