"""Logs group — the bounded operator utility over the Liquefy cold-log projection (C11).

Binds to ``core.liquefy.operator`` (health / search / retrieve / verify / rebuild /
restore). Authority law is unchanged and restated on every result: the
hash-chained Blackbox journal stays the authority; this group only operates the
additive projection (``core.liquefy``), whose redaction runs before persistence
and whose cold reads verify before serve. Every result envelope carries source
tier, event identity, time bounds, truncation/limit truth and verification
status — there is no raw query language on any surface.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OperatorAuthority,
)
from core.liquefy.operator import (
    OperatorInputError,
    projection_health,
    rebuild_projection,
    restore_projection,
    retrieve_event,
    search_events,
    verify_segments,
)
from core.liquefy.store import (
    EntryNotFound,
    LiquefyStoreError,
    SegmentCorruptError,
    SegmentKeyError,
    SegmentMissingError,
)


@dataclass(frozen=True)
class HealthInput:
    deep: bool = False


@dataclass(frozen=True)
class SearchInput:
    time_from: str = ""
    time_to: str = ""
    source: str = ""
    kind: str = ""
    event_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    turn_id: str = ""
    outcome: str = ""
    fault: str = ""
    text: str = ""
    limit: int = 50


@dataclass(frozen=True)
class EventInput:
    event_id: str = ""
    seq: int = 0
    session_id: str = ""
    trace_id: str = ""
    turn_id: str = ""


@dataclass(frozen=True)
class VerifyInput:
    segment_id: str = ""
    deep: bool = False
    column: str = ""


@dataclass(frozen=True)
class RebuildInput:
    batch_size: int = 200
    max_entries: int = 0
    resume: bool = True


@dataclass(frozen=True)
class RestoreInput:
    pass


# -- availability probes (machine evidence, never constants) ------------------


def _probe_projection_lane(context: dict) -> tuple[bool, str]:
    from core.liquefy import hooks

    if not hooks.enabled():
        return False, "projection lane disabled (VOOL_LIQUEFY_LOGS=0)"
    store = hooks.get_default_store()
    if store is None:
        return False, "projection store unavailable (home unwritable)"
    return True, f"projection lane enabled at {store.root}"


def _probe_rebuild_sources(context: dict) -> tuple[bool, str]:
    lane_ok, lane_reason = _probe_projection_lane(context)
    if not lane_ok:
        return False, lane_reason
    try:
        from core.blackbox.store import store_root

        root = store_root()
        if not (root / "journal.jsonl").exists():
            return False, f"no authoritative journal at {root}"
        return True, f"authoritative journal present at {root}"
    except Exception as exc:
        return False, f"journal store unreachable: {exc}"


# -- permission gate (operator-principal law, ops_surfaces pattern) ------------


def _gate_operator_principal(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="projection maintenance is an operator decision")
    return AuthorityDecision(granted=True)


# -- typed error mapping -------------------------------------------------------


def _fault_for_store_error(exc: Exception, command: str) -> HandlerFault:
    if isinstance(exc, OperatorInputError):
        return HandlerFault(
            fault_code="fault_validation",
            summary=str(exc),
            detail={"reason": str(exc), "command": command},
        )
    if isinstance(exc, (SegmentCorruptError, SegmentKeyError)):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"projection failed closed: {exc}",
            detail={
                "reason": str(exc),
                "segment_id": getattr(exc, "segment_id", ""),
                "fail_closed": True,
                "command": command,
            },
        )
    if isinstance(exc, SegmentMissingError):
        return HandlerFault(
            fault_code="unavailable",
            summary=f"projection segment missing: {exc}",
            detail={"reason": str(exc), "fail_closed": True, "command": command},
        )
    if isinstance(exc, (EntryNotFound, LookupError)):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"not found: {exc}",
            detail={"reason": str(exc), "not_found": True, "command": command},
        )
    if isinstance(exc, LiquefyStoreError):
        return HandlerFault(
            fault_code="fault_tool",
            summary=str(exc),
            detail={"reason": str(exc), "command": command},
        )
    return HandlerFault(
        fault_code="internal",
        summary=f"{command} failed: {exc}",
        detail={"error": str(exc), "command": command},
    )


def _identity_summary(events: list[dict], *, cap: int = 8) -> str:
    parts = []
    for event in events[:cap]:
        label = str(event.get("event_id") or f"seq:{event.get('seq')}")
        outcome = str(event.get("outcome") or "").strip()
        parts.append(f"{label}[{outcome}]" if outcome else label)
    tail = f" +{len(events) - cap} more" if len(events) > cap else ""
    return ", ".join(parts) + tail


# -- handlers (thin adapters over the authority) -------------------------------


def _handle_health(inp, ctx):
    try:
        payload = projection_health(deep=bool(inp.deep), requester=f"command:{ctx.projection}")
    except (LiquefyStoreError, OperatorInputError) as exc:
        return _fault_for_store_error(exc, "logs.health")
    sink_failures = int((payload.get("sink") or {}).get("failure_count") or 0)
    verified = (payload.get("verification") or {}).get("status")
    journal_ok = ((payload.get("authority") or {}).get("journal_chain") or {}).get("ok")
    summary = f"projection {verified}; journal chain {'ok' if journal_ok else 'FAILED'}; sink failures {sink_failures}"
    return HandlerOk(data=payload, summary=summary)


def _handle_search(inp, ctx):
    try:
        payload = search_events(
            time_from=inp.time_from,
            time_to=inp.time_to,
            source=inp.source,
            kind=inp.kind,
            event_id=inp.event_id,
            session_id=inp.session_id,
            trace_id=inp.trace_id,
            turn_id=inp.turn_id,
            outcome=inp.outcome,
            fault=inp.fault,
            text=inp.text,
            limit=inp.limit,
            requester=f"command:{ctx.projection}",
        )
    except (LiquefyStoreError, OperatorInputError) as exc:
        return _fault_for_store_error(exc, "logs.search")
    truth = "TRUNCATED" if payload["truncated"] else "complete"
    summary = (
        f"{payload['returned']} event(s) ({truth}, limit {payload['limit']}, "
        f"scanned {payload['scanned_events']}): {_identity_summary(payload['events'])}"
    )
    errors = [
        str(event["payload"].get("error"))
        for event in payload["events"]
        if isinstance(event.get("payload"), dict) and event["payload"].get("error")
    ]
    if errors:
        summary += f"; first error: {errors[0][:160]}"
    return HandlerOk(data=payload, summary=summary)


def _handle_event(inp, ctx):
    try:
        payload = retrieve_event(
            event_id=inp.event_id,
            seq=int(inp.seq or 0),
            session_id=inp.session_id,
            trace_id=inp.trace_id,
            turn_id=inp.turn_id,
            requester=f"command:{ctx.projection}",
        )
    except (LiquefyStoreError, OperatorInputError, LookupError) as exc:
        return _fault_for_store_error(exc, "logs.event")
    event = payload["event"]
    return HandlerOk(
        data=payload,
        summary=(
            f"event {event.get('event_id') or event.get('seq')} "
            f"(tier {payload['tier']}, ts {event.get('ts')}, verified)"
        ),
    )


def _handle_verify(inp, ctx):
    try:
        payload = verify_segments(
            segment_id=inp.segment_id,
            deep=bool(inp.deep),
            column=inp.column,
            requester=f"command:{ctx.projection}",
        )
    except (LiquefyStoreError, OperatorInputError) as exc:
        return _fault_for_store_error(exc, "logs.verify")
    status = (payload.get("verification") or {}).get("status")
    scope = payload.get("segment_id") or payload.get("scope")
    if status != "verified":
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"segment verification FAILED for {scope}",
            detail=payload,
        )
    return HandlerOk(data=payload, summary=f"segment {scope} verified (fail-closed path intact)")


def _handle_rebuild(inp, ctx):
    try:
        payload = rebuild_projection(
            batch_size=inp.batch_size,
            max_entries=inp.max_entries,
            resume=bool(inp.resume),
            requester=f"command:{ctx.projection}",
        )
    except (LiquefyStoreError, OperatorInputError) as exc:
        return _fault_for_store_error(exc, "logs.rebuild")
    if payload["status"] == "cancelled":
        return HandlerFault(
            fault_code="fault_cancelled",
            summary=f"rebuild cancelled at journal seq {payload['last_blackbox_seq']} (resumable)",
            detail=payload,
        )
    return HandlerOk(
        data=payload,
        summary=(
            f"rebuild {payload['status']}: appended {payload['appended']}, "
            f"skipped {payload['skipped_already_projected']} already projected, "
            f"journal entries {payload['journal_entries']}"
        ),
        receipts=(
            {
                "kind": "liquefy_rebuild",
                "appended": payload["appended"],
                "last_blackbox_seq": payload["last_blackbox_seq"],
            },
        ),
    )


def _handle_restore(inp, ctx):
    try:
        payload = restore_projection(requester=f"command:{ctx.projection}")
    except (LiquefyStoreError, OperatorInputError) as exc:
        return _fault_for_store_error(exc, "logs.restore")
    if (payload.get("verification") or {}).get("status") != "verified":
        return HandlerFault(
            fault_code="fault_validation",
            summary="restore finished but the sealed chain FAILED verification",
            detail=payload,
        )
    return HandlerOk(
        data=payload,
        summary=(
            f"restore verified: {payload['export']['events']} event(s), export sha {payload['export']['sha256'][:12]}, "
            f"torn tails repaired {len(payload['quarantined_tails'])}, orphans resealed {len(payload['orphan_segments'])}"
        ),
        receipts=(
            {
                "kind": "liquefy_restore",
                "export_sha256": payload["export"]["sha256"],
                "events": payload["export"]["events"],
            },
        ),
    )


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="logs",
            description=(
                "bounded operator utility over the Liquefy cold-log projection: "
                "health, typed search, retrieval, verification, rebuild and restore "
                "(the hash-chained journal stays the authority)"
            ),
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.health",
            group="logs",
            description="Projection health: tiers, cursor, verification, sink failure counts, receipts, journal chain",
            input_schema=HealthInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.logs_group:_handle_health"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_projection_lane"),
            fault_bindings=(
                FaultBinding(when="segment_corrupt", fault_code="fault_validation", remediation=("vool logs.verify",)),
                FaultBinding(when="segment_missing", fault_code="unavailable", remediation=("vool logs.restore",)),
            ),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.search",
            group="logs",
            description=(
                "Typed bounded search over the projection: time/source/kind/event/turn/session/trace/outcome "
                "filters; results carry tier, identity, time bounds, truncation truth, verification status"
            ),
            input_schema=SearchInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.logs_group:_handle_search"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_projection_lane"),
            fault_bindings=(
                FaultBinding(
                    when="segment_corrupt",
                    fault_code="fault_validation",
                    remediation=("vool logs.verify", "vool logs.health"),
                ),
            ),
            exit_codes=(0, 2, 10, 42),
            next_actions=(NextAction(command_id="logs.event", label="Retrieve one referenced event"),),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.event",
            group="logs",
            description="Retrieve ONE projection event by seq/event_id/turn/session/trace reference (tier + verification stated)",
            input_schema=EventInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.logs_group:_handle_event"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_projection_lane"),
            fault_bindings=(
                FaultBinding(when="event_missing", fault_code="fault_validation", remediation=("vool logs.search",)),
            ),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.verify",
            group="logs",
            description="Verify a segment/proof (header, chain position, deep blob read, PCC disclosure) or the whole chain",
            input_schema=VerifyInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.logs_group:_handle_verify"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_projection_lane"),
            fault_bindings=(
                FaultBinding(
                    when="segment_corrupt",
                    fault_code="fault_validation",
                    remediation=("vool logs.restore", "vool logs.health"),
                ),
            ),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.rebuild",
            group="logs",
            description=(
                "Rebuild the projection from the authoritative journal (read-only over the journal; "
                "idempotent, resumable, bounded, duplicate-free)"
            ),
            input_schema=RebuildInput,
            effects="idempotent_write",
            capabilities=frozenset({"logs.rebuild"}),
            permission=OperatorAuthority(
                kind="logs.rebuild",
                verifier="core.command_registry.groups.logs_group:_gate_operator_principal",
            ),
            handler=Handler("core.command_registry.groups.logs_group:_handle_rebuild"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_rebuild_sources"),
            fault_bindings=(
                FaultBinding(when="journal_corrupt", fault_code="fault_validation", remediation=("vool blackbox.verify",)),
            ),
            exit_codes=(0, 2, 10, 20, 21, 40, 42, 45),
            next_actions=(
                NextAction(command_id="logs.health", label="Projection health after rebuild"),
                NextAction(command_id="logs.verify", label="Verify sealed segments"),
            ),
        )
    )
    reg.add(
        CommandSpec(
            command_id="logs.restore",
            group="logs",
            description=(
                "Restore/recover the projection per cursor laws: quarantine torn hot tails, reseal orphan "
                "segments, verify the chain and report the deterministic export digest"
            ),
            input_schema=RestoreInput,
            effects="idempotent_write",
            capabilities=frozenset({"logs.restore"}),
            permission=OperatorAuthority(
                kind="logs.restore",
                verifier="core.command_registry.groups.logs_group:_gate_operator_principal",
            ),
            handler=Handler("core.command_registry.groups.logs_group:_handle_restore"),
            availability=Availability("core.command_registry.groups.logs_group:_probe_projection_lane"),
            fault_bindings=(
                FaultBinding(
                    when="segment_corrupt",
                    fault_code="fault_validation",
                    remediation=("vool logs.verify",),
                ),
            ),
            exit_codes=(0, 2, 10, 20, 21, 42),
            next_actions=(NextAction(command_id="logs.health", label="Projection health after restore"),),
        )
    )
