"""Where an answer was processed: one receipt per payload that actually left for a model.

THE GAP THIS CLOSES
-------------------
Every answer already carried a plain line ("Answered by a local model · qwen3:8b") read from the
turn's model-call ledger. That line names the model that WROTE the answer. It cannot say what else
the turn sent where: a local answer can sit on top of a cloud routing call, a cloud review or a cloud
memory summary, and a mixed turn was printed as plain "cloud". Nothing said how much text left the
computer for that one answer, or whether saved memories, files or the chat history were in it.

THE SEAM
--------
`ProviderInvocationPermit.consume()` (core/provider_invocation_gateway.py) is the one place every
model payload is handed to transport: chat adapters, the intent arbiter, summarisers, embeddings and
reviewers all seal a permit and consume it immediately before I/O. A consumed permit is a payload
that was actually dispatched, so the receipt is written there, from the sealed manifest (provider,
model, operation, which private context kinds the payload carried) and the disclosure wire (the
exact destination and the exact byte count). Nothing here is read from the answer's text or from
the model's own words.

Where the destination is unknown (a permit sealed without a destination) the receipt says
``unknown``; it never defaults to "this computer". A loopback address is this computer, a private
network address is the owner's own network, anything else is online.

The receipt rides on the turn's runtime event log (`provider_dispatch`), so the per-answer proof
(`core.proof_projection`) reads it back for the answer it belongs to, after a restart too.
"""
from __future__ import annotations

import contextlib
import ipaddress
from typing import Any
from urllib.parse import urlparse

EVENT_TYPE = "provider_dispatch"
WITHDRAWN_EVENT_TYPE = "provider_dispatch_withdrawn"

WHERE_THIS_COMPUTER = "this_computer"
WHERE_OWN_NETWORK = "own_network"
WHERE_ONLINE = "online"
WHERE_UNKNOWN = "unknown"

_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


def _where_of_url(url: str, cloud_destination: Any) -> str:
    host = str(urlparse(str(url or "")).hostname or "").strip().lower().strip("[]")
    if cloud_destination is True:
        return WHERE_ONLINE
    if not host:
        return WHERE_UNKNOWN
    if host in _LOOPBACK_NAMES:
        return WHERE_THIS_COMPUTER
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.is_loopback:
            return WHERE_THIS_COMPUTER
        # The one locality rule VOOL's local-only mode keeps (private, link-local, CGNAT such as a Tailscale
        # 100.64/10 address): the chip said "online" for a model the person runs on their own network (trust audit
        # of 900c7bc19) while local-only mode called the same address local.
        try:
            from core.auto_local_only_mode import _address_is_internal

            internal = bool(_address_is_internal(address))
        except Exception:
            internal = bool(address.is_private or address.is_link_local)
        return WHERE_OWN_NETWORK if internal else WHERE_ONLINE
    if host.endswith(".local"):
        # A multicast-DNS name ("mymac.local") is on the local network by definition (RFC 6762), resolvable here or
        # not.
        return WHERE_OWN_NETWORK
    # A name: the existing locality authority resolves it (unresolvable is not local).
    try:
        from core.auto_local_only_mode import endpoint_is_local

        return WHERE_OWN_NETWORK if endpoint_is_local(url) else WHERE_ONLINE
    except Exception:
        return WHERE_UNKNOWN


def _host_of(url: str) -> str:
    return str(urlparse(str(url or "")).hostname or "").strip().lower()


def _carried(manifest: Any) -> list[str]:
    """Which kinds of the owner's information the sealed payload carried, from the manifest.

    Classified with the context loader's own source-type sets: this chat's dialogue is
    ``history``; every other remembered carrier is ``memory``; a tool's output is ``tool_results``.
    Policy and identity text VOOL builds for every turn is none of these.
    """
    from core.tiered_context_loader import _MEMORY_DELIVERY_SOURCE_TYPES

    dialogue = {"dialogue_turn", "recent_dialogue", "dialogue_continuity", "active_mission"}
    types = {
        str((item or {}).get("source_type") or "")
        for item in (getattr(manifest, "selected_sources", None) or ())
        if isinstance(item, dict)
    }
    kinds: list[str] = []
    if (getattr(manifest, "user_profile_used", False) or getattr(manifest, "semantic_retrieval_used", False)
            or (types & (_MEMORY_DELIVERY_SOURCE_TYPES - dialogue))):
        kinds.append("memory")
    if "tool_observation" in types or getattr(manifest, "action_receipts_used", False):
        kinds.append("tool_results")
    if getattr(manifest, "history_used", False) or (types & dialogue):
        kinds.append("history")
    return kinds


def _turn_context() -> dict[str, Any] | None:
    """The live context of the turn this dispatch runs under, or None outside a turn."""
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
        live = getattr(ledger, "source_context", None) if ledger is not None else None
        if isinstance(live, dict) and live:
            return live
    except Exception:
        pass
    try:
        from core.remote_fetch_policy import current_remote_fetch_context

        bound = current_remote_fetch_context()
        return bound or None
    except Exception:
        return None


def dispatch_row(manifest: Any, wire: Any, body_bytes: int) -> dict[str, Any]:
    """The receipt of one dispatched payload. Pure; the caller supplies the sealed facts."""
    destination = str(getattr(wire, "destination", "") or "") if wire is not None else ""
    where = _where_of_url(destination, getattr(wire, "cloud_destination", None)) if wire is not None else WHERE_UNKNOWN
    return {
        "provider_id": str(getattr(manifest, "provider_id", "") or ""),
        "model_id": str(getattr(manifest, "model_id", "") or ""),
        "operation": str(getattr(manifest, "operation", "") or ""),
        "where": where,
        "host": _host_of(destination) if where in {WHERE_ONLINE, WHERE_OWN_NETWORK} else "",
        "bytes": max(0, int(body_bytes or 0)),
        "carried": _carried(manifest),
        "dispatch_id": str(getattr(manifest, "manifest_id", "") or ""),
    }


#: The receipt's fields as flat runtime-event details: names, sizes and destinations, never words.
ROUTE_KEYS = ("route_dispatch_id", "route_where", "route_host", "route_provider", "route_model",
              "route_operation", "route_bytes", "route_carried")


def flat_details(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_dispatch_id": row["dispatch_id"],
        "route_where": row["where"],
        "route_host": row["host"],
        "route_provider": row["provider_id"],
        "route_model": row["model_id"],
        "route_operation": row["operation"],
        "route_bytes": int(row["bytes"]),
        "route_carried": ",".join(row["carried"]),
    }


def note_dispatch(manifest: Any, wire: Any, body_bytes: int) -> None:
    """Record one consumed permit on its turn's event log. Observation only: never raises."""
    try:
        context = _turn_context()
        if not context:
            return
        if not str(context.get("runtime_session_id") or context.get("session_id") or "").strip():
            return
        row = dispatch_row(manifest, wire, body_bytes)
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            context,
            event_type=EVENT_TYPE,
            message=f"{row['provider_id'] or 'model'} request sent ({row['where']}).",
            details=flat_details(row),
        )
    except Exception:
        return


def note_withdrawn(dispatch_id: str) -> None:
    """A dispatch refused at the byte boundary after its permit was consumed: nothing was sent."""
    try:
        call_id = str(dispatch_id or "").strip()
        context = _turn_context()
        if not call_id or not context:
            return
        if not str(context.get("runtime_session_id") or context.get("session_id") or "").strip():
            return
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            context,
            event_type=WITHDRAWN_EVENT_TYPE,
            message="A model request was stopped before it was sent.",
            details={"route_dispatch_id": call_id},
        )
    except Exception:
        return


def route_of_events(turn_events: list[dict[str, Any]]) -> dict[str, Any]:
    """The answer's route from its own dispatch receipts, grouped by (where, host, model).

    ``summary`` is ``local`` (every dispatch stayed on this computer or the owner's network),
    ``cloud`` (every dispatch went online), ``mixed`` (both), ``none`` (no model payload was sent)
    or ``unknown`` (a dispatch's destination was not recorded and nothing else decides it).
    """
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    withdrawn = {
        str(event.get("route_dispatch_id") or "")
        for event in turn_events
        if str(event.get("event_type") or "") == WITHDRAWN_EVENT_TYPE and event.get("route_dispatch_id")
    }
    for event in turn_events:
        if str(event.get("event_type") or "") != EVENT_TYPE:
            continue
        if not event.get("route_where"):
            continue
        row = {
            "dispatch_id": str(event.get("route_dispatch_id") or ""),
            "where": str(event.get("route_where") or ""),
            "host": str(event.get("route_host") or ""),
            "provider_id": str(event.get("route_provider") or ""),
            "model_id": str(event.get("route_model") or ""),
            "operation": str(event.get("route_operation") or ""),
            "bytes": event.get("route_bytes") or 0,
            "carried": [kind for kind in str(event.get("route_carried") or "").split(",") if kind],
        }
        dispatch_id = row["dispatch_id"]
        if dispatch_id and dispatch_id in withdrawn:
            continue
        ident = dispatch_id or f"seq:{event.get('seq')}"
        if ident in seen:
            continue
        seen.add(ident)
        where = str(row.get("where") or WHERE_UNKNOWN)
        # One line per model and place: the same model reached through two registrations (an
        # "ollama" and an "ollama-local" id for one local server) is one line, not two.
        key = (where, str(row.get("host") or ""), str(row.get("model_id") or "") or str(row.get("provider_id") or ""), "")
        group = groups.get(key)
        if group is None:
            group = {"where": key[0], "host": key[1], "provider": str(row.get("provider_id") or ""),
                     "model": str(row.get("model_id") or ""),
                     "calls": 0, "bytes": 0, "carried": [], "operations": []}
            groups[key] = group
            order.append(key)
        group["calls"] += 1
        with contextlib.suppress(TypeError, ValueError):
            group["bytes"] += max(0, int(row.get("bytes") or 0))
        for kind in row.get("carried") or []:
            if isinstance(kind, str) and kind not in group["carried"]:
                group["carried"].append(kind)
        operation = str(row.get("operation") or "")
        if operation and operation not in group["operations"]:
            group["operations"].append(operation)
    rows = [groups[key] for key in order]
    online = [row for row in rows if row["where"] == WHERE_ONLINE]
    nearby = [row for row in rows if row["where"] in {WHERE_THIS_COMPUTER, WHERE_OWN_NETWORK}]
    unknown = [row for row in rows if row["where"] == WHERE_UNKNOWN]
    if not rows:
        summary = "none"
    elif online and (nearby or unknown):
        summary = "mixed"
    elif online:
        summary = "cloud"
    elif unknown:
        summary = "unknown"
    else:
        summary = "local"
    carried_online: list[str] = []
    for row in online + unknown:
        for kind in row["carried"]:
            if kind not in carried_online:
                carried_online.append(kind)
    return {
        "summary": summary,
        "calls": rows,
        "online_bytes": sum(row["bytes"] for row in online),
        "unknown_bytes": sum(row["bytes"] for row in unknown),
        "carried_online": carried_online,
    }


__all__ = ["EVENT_TYPE", "WITHDRAWN_EVENT_TYPE", "dispatch_row", "note_dispatch", "note_withdrawn", "route_of_events"]
