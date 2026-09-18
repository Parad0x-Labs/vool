"""Canonical serialization and digest of a ``RequestGraph``.

Two consumers, two policies, one encoding:

* **The obligation ledger** persists the whole graph beside the request text it already stores, so
  the publication sweep can read the frozen slot set back instead of re-lexing the request. That
  path passes ``include_text=True`` and gets a payload it can rebuild a graph from.
* **Telemetry** (the shadow observation, the resolution receipt) never carries user text. It uses
  ``graph_digest`` -- a hash over the text-free payload -- and ``graph_builder.graph_summary``.
  Note that even the text-free payload carries request ``source_text`` and mention ``surface``
  values (they ARE user words); it exists to be hashed and to be stored where the request text is
  already stored, never to be logged.

The digest covers EVERY meaningful field: ids, spans, states, operands, quantities, constraints,
prohibitions, retractions, dependencies, presentation, uncovered source and shape. Changing any of
them changes the digest -- which is what lets a gold corpus be frozen on its full annotation rather
than on a handful of counts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.request_graph import (
    Ambiguity,
    Constraint,
    ConstraintId,
    ConstraintKind,
    DependencyEdge,
    DependencyId,
    DependencyKind,
    InterpretationState,
    Mention,
    MentionId,
    Operand,
    OutputFormat,
    PresentationContract,
    Prohibition,
    Quantity,
    Request,
    RequestGraph,
    RequestGraphError,
    RequestId,
    Retraction,
    SemanticRole,
    Slot,
    SlotId,
    TurnId,
)
from core.semantic.types import RequestShape

SERIALIZATION_SCHEMA = "vool.request_graph_serialization.v1"
_DIGEST_CHARS = 16


class GraphSerializationError(ValueError):
    """A payload that is not a graph, or a graph that does not match the text it claims."""


def _span(span: EntitySpan | None) -> dict[str, Any] | None:
    return span.to_dict() if span is not None else None


def _quantity(q: Quantity | None) -> dict[str, Any] | None:
    if q is None:
        return None
    return {
        "raw": q.raw, "exact": q.exact, "unit": q.unit, "currency": q.currency,
        "asset": q.asset, "chain": q.chain, "alternatives": list(q.alternatives),
    }


def graph_to_dict(graph: RequestGraph, *, include_text: bool) -> dict[str, Any]:
    """The complete, order-stable payload. ``include_text`` adds the canonical text itself."""
    canonical: dict[str, Any] = dict(graph.canonical.to_dict())
    if include_text:
        canonical["text"] = graph.canonical.text
    return {
        "schema": graph.schema,
        "serialization": SERIALIZATION_SCHEMA,
        "turn_id": str(graph.turn_id),
        "shape": graph.shape.value,
        "canonical": canonical,
        "requests": [
            {
                "id": r.id, "source_text": r.source_text, "span": _span(r.span),
                "parent_id": r.parent_id, "slot_ids": list(r.slot_ids),
                "state": r.state.value, "unresolved": bool(r.unresolved),
                # Sparse: a capability hint is not meaning, so an absent one adds no key (and moves
                # no digest); a present one is persisted for the planner bridge.
                **({"family": r.family} if r.family else {}),
            }
            for r in graph.requests
        ],
        "slots": [
            {
                "id": s.id, "request_id": s.request_id, "expected": s.expected, "state": s.state.value,
                "operands": [
                    {
                        "role": o.role.value, "mention_id": o.mention_id,
                        "quantity": _quantity(o.quantity), "result_ref": o.result_ref,
                    }
                    for o in s.operands
                ],
                "quantity": _quantity(s.quantity),
                "ambiguity": (
                    {
                        "alternatives": list(s.ambiguity.alternatives), "reason": s.ambiguity.reason,
                        "needs_clarification": bool(s.ambiguity.needs_clarification),
                    }
                    if s.ambiguity is not None else None
                ),
                "reason": s.reason,
            }
            for s in graph.slots
        ],
        "mentions": [
            {
                "id": m.id, "span": _span(m.span), "surface": m.surface, "role": m.role.value,
                "entity_key": m.entity_key, "alternatives": list(m.alternatives),
                "occurrence": int(m.occurrence),
            }
            for m in graph.mentions
        ],
        "constraints": [
            {"id": c.id, "kind": c.kind.value, "detail": c.detail, "span": _span(c.span), "scope": list(c.scope)}
            for c in graph.constraints
        ],
        "prohibitions": [
            {"id": p.id, "target": p.target, "scope": list(p.scope), "span": _span(p.span)}
            for p in graph.prohibitions
        ],
        "retractions": [
            {
                "id": r.id, "target": r.target, "supersedes": r.supersedes, "span": _span(r.span),
                "supersedes_kind": r.supersedes_kind,
            }
            for r in graph.retractions
        ],
        "dependencies": [
            {
                "id": d.id, "from_request": d.from_request, "to_request": d.to_request,
                "kind": d.kind.value, "value_ref": d.value_ref,
            }
            for d in graph.dependencies
        ],
        "presentation": (
            {
                "fmt": graph.presentation.fmt.value, "fields": list(graph.presentation.fields),
                "ordering": graph.presentation.ordering, "units": graph.presentation.units,
                "brevity": graph.presentation.brevity,
                "literal_output": bool(graph.presentation.literal_output),
            }
            if graph.presentation is not None else None
        ),
        "uncovered_source": list(graph.uncovered_source),
    }


def canonical_json(payload: Any) -> str:
    """Deterministic encoding: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def graph_digest(graph: RequestGraph) -> str:
    """sha256 (16 hex chars) over the TEXT-FREE payload. Any meaningful field moves it."""
    encoded = canonical_json(graph_to_dict(graph, include_text=False)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:_DIGEST_CHARS]


# -- reading back --------------------------------------------------------------


def _read_span(payload: Any, *, where: str) -> EntitySpan | None:
    if payload is None:
        return None
    span = EntitySpan.from_dict(payload)
    if span is None:
        raise GraphSerializationError(f"{where}: unreadable span {payload!r}")
    return span


def _read_quantity(payload: Any) -> Quantity | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise GraphSerializationError(f"quantity is not an object: {payload!r}")
    return Quantity(
        raw=str(payload.get("raw") or ""), exact=str(payload.get("exact") or ""),
        unit=str(payload.get("unit") or ""), currency=str(payload.get("currency") or ""),
        asset=str(payload.get("asset") or ""), chain=str(payload.get("chain") or ""),
        alternatives=tuple(str(a) for a in (payload.get("alternatives") or ())),
    )


def _enum(cls: Any, value: Any, *, where: str) -> Any:
    try:
        return cls(value)
    except ValueError as exc:
        raise GraphSerializationError(f"{where}: {exc}") from exc


def graph_from_dict(payload: Any, *, canonical: CanonicalText | None = None) -> RequestGraph:
    """Rebuild a graph. The text comes from the payload (``include_text=True``) or from
    ``canonical``; when both are present they must agree, and a payload whose canonical digest does
    not match the text it is rebuilt against is refused -- spans over the wrong text mean nothing."""
    if not isinstance(payload, dict):
        raise GraphSerializationError("graph payload is not an object")
    if str(payload.get("schema") or "") != RequestGraph.schema:
        raise GraphSerializationError(f"unsupported graph schema {payload.get('schema')!r}")
    if str(payload.get("serialization") or "") != SERIALIZATION_SCHEMA:
        # An unknown serialization is not readable "with defaults": its fields may mean something
        # else. It stays an opaque historical artifact until an explicit migration exists.
        raise GraphSerializationError(f"unsupported graph serialization {payload.get('serialization')!r}")
    identity = payload.get("canonical")
    if not isinstance(identity, dict):
        raise GraphSerializationError("graph payload carries no canonical identity")
    text_present = "text" in identity
    if text_present:
        embedded = CanonicalText.of(identity["text"], representation=str(identity.get("representation") or "user_text.nfc.v1"))
        if str(identity.get("digest") or "") != embedded.digest:
            raise GraphSerializationError("payload text does not match its own canonical digest")
        if canonical is not None and canonical.digest != embedded.digest:
            raise GraphSerializationError("payload text and the supplied canonical disagree")
        canonical = canonical or embedded
    if canonical is None:
        raise GraphSerializationError("payload has no text and no canonical was supplied")
    if str(identity.get("digest") or "") != canonical.digest:
        raise GraphSerializationError("payload canonical digest does not match the supplied text")
    try:
        return RequestGraph(
            turn_id=TurnId(str(payload.get("turn_id") or "")),
            canonical=canonical,
            requests=tuple(
                Request(
                    id=RequestId(str(r["id"])), source_text=str(r.get("source_text") or ""),
                    span=_read_span(r.get("span"), where=f"request {r.get('id')}"),
                    parent_id=(RequestId(str(r["parent_id"])) if r.get("parent_id") else None),
                    slot_ids=tuple(SlotId(str(s)) for s in (r.get("slot_ids") or ())),
                    state=_enum(InterpretationState, r.get("state"), where=f"request {r.get('id')}"),
                    unresolved=bool(r.get("unresolved")),
                    family=str(r.get("family") or ""),
                )
                for r in payload.get("requests") or ()
            ),
            slots=tuple(
                Slot(
                    id=SlotId(str(s["id"])), request_id=RequestId(str(s["request_id"])),
                    expected=str(s.get("expected") or ""),
                    state=_enum(InterpretationState, s.get("state"), where=f"slot {s.get('id')}"),
                    operands=tuple(
                        Operand(
                            role=_enum(SemanticRole, o.get("role"), where=f"slot {s.get('id')} operand"),
                            mention_id=(MentionId(str(o["mention_id"])) if o.get("mention_id") else None),
                            quantity=_read_quantity(o.get("quantity")),
                            result_ref=(SlotId(str(o["result_ref"])) if o.get("result_ref") else None),
                        )
                        for o in (s.get("operands") or ())
                    ),
                    quantity=_read_quantity(s.get("quantity")),
                    ambiguity=(
                        Ambiguity(
                            alternatives=tuple(str(a) for a in (s["ambiguity"].get("alternatives") or ())),
                            reason=str(s["ambiguity"].get("reason") or ""),
                            needs_clarification=bool(s["ambiguity"].get("needs_clarification")),
                        )
                        if isinstance(s.get("ambiguity"), dict) else None
                    ),
                    reason=str(s.get("reason") or ""),
                )
                for s in payload.get("slots") or ()
            ),
            mentions=tuple(
                Mention(
                    id=MentionId(str(m["id"])),
                    span=_read_span(m.get("span"), where=f"mention {m.get('id')}"),  # type: ignore[arg-type]
                    surface=str(m.get("surface") or ""),
                    role=_enum(SemanticRole, m.get("role", SemanticRole.OTHER.value), where=f"mention {m.get('id')}"),
                    entity_key=str(m.get("entity_key") or ""),
                    alternatives=tuple(str(a) for a in (m.get("alternatives") or ())),
                    occurrence=int(m.get("occurrence") or 0),
                )
                for m in payload.get("mentions") or ()
            ),
            constraints=tuple(
                Constraint(
                    id=ConstraintId(str(c["id"])), kind=_enum(ConstraintKind, c.get("kind"), where=f"constraint {c.get('id')}"),
                    detail=str(c.get("detail") or ""), span=_read_span(c.get("span"), where=f"constraint {c.get('id')}"),
                    scope=tuple(RequestId(str(r)) for r in (c.get("scope") or ())),
                )
                for c in payload.get("constraints") or ()
            ),
            prohibitions=tuple(
                Prohibition(
                    id=ConstraintId(str(p["id"])), target=str(p.get("target") or ""),
                    scope=tuple(RequestId(str(r)) for r in (p.get("scope") or ())),
                    span=_read_span(p.get("span"), where=f"prohibition {p.get('id')}"),
                )
                for p in payload.get("prohibitions") or ()
            ),
            retractions=tuple(
                Retraction(
                    id=ConstraintId(str(r["id"])), target=str(r.get("target") or ""),
                    supersedes=(str(r["supersedes"]) if r.get("supersedes") else None),  # type: ignore[arg-type]
                    span=_read_span(r.get("span"), where=f"retraction {r.get('id')}"),
                    supersedes_kind=str(r.get("supersedes_kind") or "request"),
                )
                for r in payload.get("retractions") or ()
            ),
            dependencies=tuple(
                DependencyEdge(
                    id=DependencyId(str(d["id"])), from_request=RequestId(str(d["from_request"])),
                    to_request=RequestId(str(d["to_request"])),
                    kind=_enum(DependencyKind, d.get("kind"), where=f"dependency {d.get('id')}"),
                    value_ref=(SlotId(str(d["value_ref"])) if d.get("value_ref") else None),
                )
                for d in payload.get("dependencies") or ()
            ),
            presentation=(
                PresentationContract(
                    fmt=_enum(OutputFormat, payload["presentation"].get("fmt"), where="presentation"),
                    fields=tuple(str(f) for f in (payload["presentation"].get("fields") or ())),
                    ordering=str(payload["presentation"].get("ordering") or ""),
                    units=str(payload["presentation"].get("units") or ""),
                    brevity=str(payload["presentation"].get("brevity") or ""),
                    literal_output=bool(payload["presentation"].get("literal_output")),
                )
                if isinstance(payload.get("presentation"), dict) else None
            ),
            uncovered_source=tuple(str(u) for u in (payload.get("uncovered_source") or ())),
            shape=_enum(RequestShape, payload.get("shape", RequestShape.UNKNOWN.value), where="shape"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, GraphSerializationError):
            raise
        if isinstance(exc, RequestGraphError):
            raise GraphSerializationError(f"payload violates a graph law: {exc}") from exc
        raise GraphSerializationError(f"malformed graph payload: {type(exc).__name__}: {exc}") from exc


__all__ = [
    "SERIALIZATION_SCHEMA",
    "GraphSerializationError",
    "canonical_json",
    "graph_digest",
    "graph_from_dict",
    "graph_to_dict",
]
