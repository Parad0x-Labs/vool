"""Turn a model's graph reply into a ``RequestGraph`` -- tolerant, pure, and source-conserving.

The reply is JSON the model was asked for (see ``core.semantic.resolver`` for the contract). Every
value in it is a POINTER (a verbatim substring, a key the reply itself minted, an enum) or a
quantity; nothing in it is trusted. This module locates every substring in the real text, remaps
the reply's own keys onto ids the builder mints (never positions), and above all preserves what it
could not interpret:

* a malformed entry, an entry whose text cannot be found, an entry past the request cap -- none of
  these disappear. The builder's source conservation turns every stretch the parsed records do not
  cover into an UNRESOLVED request with that fragment, so a dropped clause is visible as an
  obligation rather than silently gone;
* dependencies name KEYS, so skipping an entry cannot shift another entry's dependency;
* an entity the reply names that is not in the text mints no span; the slot goes UNRESOLVED with a
  reason;
* a repeated surface binds by OCCURRENCE inside the request's own span, then to the next unclaimed
  occurrence -- never "the first global match" twice;
* an EMPTY decomposition is not "nothing was asked": the whole text becomes unresolved obligations.

Abstention (``ParsedGraph.abstained``) is reserved for a reply that is not a graph at all: empty,
not JSON, too large. Those are the backend's failure, reported as such, and the deterministic
reading stays authoritative for the turn.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.graph_builder import GraphBuildError, RequestGraphBuilder
from core.semantic.request_graph import (
    Ambiguity,
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    Operand,
    OutputFormat,
    Quantity,
    RequestGraph,
    RequestId,
    SemanticRole,
    SlotId,
)


@dataclass(frozen=True)
class ParsedGraph:
    """The outcome of parsing one reply. Exactly one of ``graph`` / ``abstained`` is meaningful."""

    graph: RequestGraph | None
    abstained: bool
    reason: str = ""
    #: Repairs and preservations applied, as "kind:detail" strings. Text-free by construction (keys
    #: and counts only) so they can travel in telemetry.
    notes: tuple[str, ...] = ()


_STATE_ALIASES: dict[str, InterpretationState] = {
    "resolved": InterpretationState.RESOLVED,
    "ok": InterpretationState.RESOLVED,
    "ambiguous": InterpretationState.AMBIGUOUS,
    "unresolved": InterpretationState.UNRESOLVED,
    "unknown": InterpretationState.UNKNOWN_MEANING,
    "unknown_meaning": InterpretationState.UNKNOWN_MEANING,
    "answerable_without_tool": InterpretationState.ANSWERABLE_WITHOUT_TOOL,
    "no_tool": InterpretationState.ANSWERABLE_WITHOUT_TOOL,
    "direct": InterpretationState.ANSWERABLE_WITHOUT_TOOL,
    "unsupported": InterpretationState.UNSUPPORTED_CAPABILITY,
    "unsupported_capability": InterpretationState.UNSUPPORTED_CAPABILITY,
    "tool_forbidden": InterpretationState.TOOL_FORBIDDEN,
    "retrieval_forbidden": InterpretationState.RETRIEVAL_FORBIDDEN,
    "needs_input": InterpretationState.NEEDS_INPUT,
    "needs_clarification": InterpretationState.NEEDS_INPUT,
}

_ROLE_ALIASES: dict[str, SemanticRole] = {r.value: r for r in SemanticRole}
_ROLE_ALIASES.update({"from": SemanticRole.SOURCE, "to": SemanticRole.TARGET, "pay_with": SemanticRole.PAYMENT,
                      "with": SemanticRole.PAYMENT, "result": SemanticRole.RESULT_REF, "a": SemanticRole.COMPARISON_A,
                      "b": SemanticRole.COMPARISON_B})

_CONSTRAINT_ALIASES: dict[str, ConstraintKind] = {k.value: k for k in ConstraintKind}
_CONSTRAINT_ALIASES.update({"fresh": ConstraintKind.FRESHNESS, "recency": ConstraintKind.FRESHNESS,
                            "place": ConstraintKind.LOCATION, "when": ConstraintKind.TIME,
                            "format": ConstraintKind.OUTPUT_FORMAT, "no_tools": ConstraintKind.TOOL_FORBIDDEN,
                            "no_retrieval": ConstraintKind.RETRIEVAL_FORBIDDEN, "no_web": ConstraintKind.RETRIEVAL_FORBIDDEN,
                            "needs_retrieval": ConstraintKind.RETRIEVAL_REQUIRED, "live": ConstraintKind.RETRIEVAL_REQUIRED})

_DEPENDENCY_ALIASES: dict[str, DependencyKind] = {k.value: k for k in DependencyKind}
_DEPENDENCY_ALIASES.update({"needs_result": DependencyKind.VALUE, "after": DependencyKind.ORDERING,
                            "if": DependencyKind.CONDITIONAL, "condition": DependencyKind.CONDITIONAL})

_FORMAT_ALIASES: dict[str, OutputFormat] = {f.value: f for f in OutputFormat}
_FORMAT_ALIASES.update({"text": OutputFormat.PROSE, "markdown": OutputFormat.PROSE, "bullets": OutputFormat.LIST,
                        "number_only": OutputFormat.LITERAL, "raw": OutputFormat.LITERAL, "none": OutputFormat.UNSPECIFIED,
                        "": OutputFormat.UNSPECIFIED})

_NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                 "seven": "7", "eight": "8", "nine": "9", "ten": "10", "a": "1", "an": "1", "half": "0.5"}
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


# -- JSON extraction ----------------------------------------------------------


def _first_json_object(text: str) -> str:
    """The first brace-balanced ``{...}`` region, honoring string literals. ``""`` if none."""
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def extract_reply_object(reply: str) -> tuple[Mapping[str, Any] | None, str]:
    """(object, abstain_reason). Strips a code fence, tries the whole text, then the first object."""
    text = str(reply or "").strip()
    if not text:
        return None, "empty_reply"
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    for candidate in (text, _first_json_object(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, Mapping):
            return parsed, ""
        if isinstance(parsed, list):
            # A bare array is read as the requests list (the old clause-array contract).
            return {"requests": parsed}, ""
    return None, "no_json_object"


# -- small readers -------------------------------------------------------------


def _str(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _ref(value: Any) -> str:
    """A request reference: a key the reply minted, or a legacy positional index (``0`` -> ``#0``)."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return f"#{value}"
    text = _str(value)
    return f"#{text}" if text.isdigit() else text


def _keys(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_str(v) for v in value if _str(v)]
    single = _str(value)
    return [single] if single else []


def _state(value: Any, default: InterpretationState) -> tuple[InterpretationState, str]:
    raw = _str(value).lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return default, ""
    state = _STATE_ALIASES.get(raw)
    if state is None:
        return InterpretationState.UNRESOLVED, f"unrecognized state {raw!r}"
    return state, ""


def _exact_number(raw: str) -> str:
    match = _NUMBER_RE.search(raw)
    if match:
        return match.group(0).replace(",", "").lstrip("+")
    for word, digit in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", raw.lower()):
            return digit
    return ""


def _quantity(value: Any) -> Quantity | None:
    if not isinstance(value, Mapping):
        raw = _str(value)
        if not raw:
            return None
        return Quantity(raw=raw, exact=_exact_number(raw))
    raw = _str(value.get("raw") or value.get("text"))
    exact = _str(value.get("exact") or value.get("value") or value.get("amount"))
    if not exact and raw:
        exact = _exact_number(raw)
    unit = _str(value.get("unit"))
    currency = _str(value.get("currency")).upper()
    asset = _str(value.get("asset") or value.get("token"))
    chain = _str(value.get("chain"))
    if not any((raw, exact, unit, currency, asset)):
        return None
    return Quantity(raw=raw or exact, exact=exact, unit=unit, currency=currency, asset=asset, chain=chain,
                    alternatives=tuple(_keys(value.get("alternatives"))))


def _locate(canonical: CanonicalText, text: Any) -> EntitySpan | None:
    needle = _str(text)
    if not needle:
        return None
    found = canonical.find(needle)
    if found is not None:
        return found
    index = canonical.text.casefold().find(canonical.normalize(needle).casefold())
    if index < 0:
        return None
    return canonical.span(index, index + len(canonical.normalize(needle)))


# -- the parser ----------------------------------------------------------------


def parse_graph_reply(
    reply: Any,
    *,
    canonical: CanonicalText,
    turn_id: str = "",
    operations: Sequence[str] = (),
    max_requests: int = 24,
    max_reply_chars: int = 200_000,
) -> ParsedGraph:
    """Parse one reply. Never raises; a parser fault is an abstention with its reason."""
    if not isinstance(reply, str):
        return ParsedGraph(None, True, "reply_not_text")
    if len(reply) > int(max_reply_chars):
        return ParsedGraph(None, True, "reply_too_large")
    payload, reason = extract_reply_object(reply)
    if payload is None:
        return ParsedGraph(None, True, reason)
    try:
        return _build(payload, canonical=canonical, turn_id=turn_id, operations=tuple(operations),
                      max_requests=int(max_requests))
    except Exception as exc:  # a parser fault is never a fabricated graph
        return ParsedGraph(None, True, f"parser_error:{type(exc).__name__}")


def _build(payload: Mapping[str, Any], *, canonical: CanonicalText, turn_id: str, operations: tuple[str, ...],
           max_requests: int) -> ParsedGraph:
    builder = RequestGraphBuilder(canonical, turn_id=turn_id)
    notes: list[str] = []
    request_ids: dict[str, RequestId] = {}
    slot_ids: dict[str, SlotId] = {}
    pending_dependencies: list[tuple[str, str, str, str]] = []  # (from_key, to_key, kind, slot_key)
    known_ops = {str(op) for op in operations}

    entries = payload.get("requests")
    if not isinstance(entries, list):
        entries = []
        notes.append("requests_not_a_list")

    for position, entry in enumerate(entries):
        if position >= max_requests:
            notes.append(f"request_cap:{len(entries) - max_requests}")
            break  # the remaining text is conserved by build(), never dropped
        if not isinstance(entry, Mapping):
            notes.append(f"malformed_entry:{position}")
            continue
        key = _str(entry.get("key") or entry.get("id")) or f"#{position}"
        if key.isdigit():
            key = f"#{key}"
        source = _str(entry.get("source") or entry.get("request") or entry.get("text"))
        span = _locate(canonical, source) if source else None
        if source and span is None:
            notes.append(f"unlocated_request:{position}")
        state, state_note = _state(entry.get("state"), InterpretationState.RESOLVED)
        family = _str(entry.get("operation") or entry.get("family"))
        if family.lower() in {"", "null", "none", "unknown"}:
            family = ""
        elif known_ops and family not in known_ops:
            notes.append(f"operation_off_menu:{position}")  # preserved; admission decides
        rid = builder.add_request(
            source or f"(request {key})", span=span, state=state, request_id=None, family=family,
            unresolved=(state is InterpretationState.UNRESOLVED),
        )
        if state_note:
            notes.append(f"request_state:{position}:{state_note}")
        request_ids[key] = rid
        req_span = builder.request_span(rid)

        slots = entry.get("slots")
        if not isinstance(slots, list) or not slots:
            # No slots named: one default slot for the request. A legacy clause-array reply carries
            # its entities beside the clause; they become that slot's (role-less) operands so the
            # spans survive the projection.
            legacy_entities = [
                {"role": "other", "text": e.get("text"), "kind": e.get("kind")}
                for e in (entry.get("entities") or ())
                if isinstance(e, Mapping) and _str(e.get("text"))
            ]
            slots = [{"expected": source, "operands": legacy_entities}]
            notes.append(f"slot_defaulted:{position}")
        for s_position, slot_entry in enumerate(slots):
            if not isinstance(slot_entry, Mapping):
                slot_entry = {"expected": source}
                notes.append(f"malformed_slot:{position}:{s_position}")
            slot_key = _str(slot_entry.get("key") or slot_entry.get("id")) or f"{key}/s{s_position}"
            slot_state, slot_note = _state(slot_entry.get("state"), state if state is not InterpretationState.UNRESOLVED else InterpretationState.RESOLVED)
            reasons: list[str] = []
            if slot_note:
                reasons.append(slot_note)
            given_reason = _str(slot_entry.get("reason"))
            if given_reason:
                reasons.append(given_reason)
            operands: list[Operand] = []
            for operand_entry in slot_entry.get("operands") or ():
                if not isinstance(operand_entry, Mapping):
                    continue
                role = _ROLE_ALIASES.get(_str(operand_entry.get("role")).lower().replace(" ", "_"), SemanticRole.OTHER)
                result_of = _str(operand_entry.get("result_of") or operand_entry.get("result_ref"))
                if result_of:
                    ref = slot_ids.get(result_of)
                    if ref is None:
                        reasons.append(f"result reference {result_of!r} names no earlier slot")
                        notes.append(f"invalid_result_ref:{position}:{s_position}")
                        slot_state = InterpretationState.UNRESOLVED
                        continue
                    operands.append(Operand(role=SemanticRole.RESULT_REF if role is SemanticRole.OTHER else role, result_ref=ref))
                    continue
                text = _str(operand_entry.get("text") or operand_entry.get("surface"))
                quantity = _quantity(operand_entry.get("quantity"))
                mention_id = None
                if text:
                    occurrence = operand_entry.get("occurrence")
                    occ = int(occurrence) if isinstance(occurrence, int) and not isinstance(occurrence, bool) and occurrence >= 0 else None
                    kind = _str(operand_entry.get("kind"))
                    entity = _str(operand_entry.get("entity") or operand_entry.get("entity_key"))
                    try:
                        mention_id = builder.add_mention(text, within=req_span, occurrence=occ, kind=kind, role=role, entity_key=entity)
                    except GraphBuildError:
                        try:
                            mention_id = builder.add_mention(text, occurrence=occ, kind=kind, role=role, entity_key=entity)
                        except GraphBuildError:
                            reasons.append(f"entity {text!r} is not in the message")
                            notes.append(f"unfindable_entity:{position}:{s_position}")
                            if slot_state is InterpretationState.RESOLVED:
                                slot_state = InterpretationState.UNRESOLVED
                            if quantity is None:
                                continue
                if mention_id is None and quantity is None:
                    continue
                operands.append(Operand(role=role, mention_id=mention_id, quantity=quantity))
            ambiguity = None
            amb = slot_entry.get("ambiguity")
            if isinstance(amb, Mapping) and _keys(amb.get("alternatives")):
                ambiguity = Ambiguity(alternatives=tuple(_keys(amb.get("alternatives"))), reason=_str(amb.get("reason")),
                                      needs_clarification=bool(amb.get("needs_clarification", True)))
            sid = builder.add_slot(
                rid, expected=_str(slot_entry.get("expected")) or source, state=slot_state, operands=tuple(operands),
                quantity=_quantity(slot_entry.get("quantity")), ambiguity=ambiguity, reason="; ".join(reasons),
            )
            slot_ids[slot_key] = sid

        for dep in entry.get("depends_on") or ():
            if isinstance(dep, Mapping):
                pending_dependencies.append((key, _ref(dep.get("request") or dep.get("on")), _str(dep.get("kind")), _str(dep.get("slot"))))
            else:
                pending_dependencies.append((key, _ref(dep), "", ""))

    for dep in payload.get("dependencies") or ():
        if isinstance(dep, Mapping):
            pending_dependencies.append((_str(dep.get("from")), _str(dep.get("to")), _str(dep.get("kind")), _str(dep.get("slot"))))

    for from_key, to_key, kind_raw, slot_key in pending_dependencies:
        frm, to = request_ids.get(from_key), request_ids.get(to_key)
        if frm is None or to is None or frm == to:
            notes.append(f"invalid_dependency:{from_key}->{to_key}")
            continue
        kind = _DEPENDENCY_ALIASES.get(kind_raw.lower().replace(" ", "_"), DependencyKind.ORDERING)
        value_ref = slot_ids.get(slot_key)
        if kind is DependencyKind.VALUE and value_ref is None:
            value_ref = next(iter(builder_slots_for(builder, to)), None)
            if value_ref is None:
                notes.append(f"value_dependency_without_slot:{from_key}->{to_key}")
        try:
            builder.add_dependency(frm, to, kind=kind, value_ref=value_ref)
        except GraphBuildError:
            notes.append(f"invalid_dependency:cycle:{from_key}->{to_key}")

    for c_position, entry in enumerate(payload.get("constraints") or ()):
        if not isinstance(entry, Mapping):
            notes.append(f"malformed_constraint:{c_position}")
            continue
        kind_raw = _str(entry.get("kind")).lower().replace(" ", "_")
        kind = _CONSTRAINT_ALIASES.get(kind_raw)
        detail = _str(entry.get("detail")) or _str(entry.get("text"))
        if kind is None:
            kind = ConstraintKind.USER_RESTRICTION
            detail = f"{kind_raw}: {detail}".strip(": ")
            notes.append(f"constraint_kind_preserved_as_restriction:{c_position}")
        scope = tuple(request_ids[k] for k in _keys(entry.get("requests") or entry.get("scope")) if k in request_ids)
        builder.add_constraint(kind, detail=detail, span=_locate(canonical, entry.get("text")), scope=scope)

    for p_position, entry in enumerate(payload.get("prohibitions") or ()):
        if not isinstance(entry, Mapping):
            notes.append(f"malformed_prohibition:{p_position}")
            continue
        target = _str(entry.get("target")).lower() or _str(entry.get("text")).lower()
        if not target:
            notes.append(f"prohibition_without_target:{p_position}")
            continue
        scope = tuple(request_ids[k] for k in _keys(entry.get("requests") or entry.get("scope")) if k in request_ids)
        builder.add_prohibition(target, scope=scope, span=_locate(canonical, entry.get("text")))

    for r_position, entry in enumerate(payload.get("retractions") or ()):
        if not isinstance(entry, Mapping):
            notes.append(f"malformed_retraction:{r_position}")
            continue
        target = _str(entry.get("target")).lower() or _str(entry.get("text")).lower()
        supersedes_key = _str(entry.get("supersedes") or entry.get("request"))
        slot_key = _str(entry.get("slot"))
        supersedes = None
        kind = "request"
        if slot_key and slot_ids.get(slot_key) is not None:
            supersedes, kind = slot_ids[slot_key], "slot"
        elif request_ids.get(_ref(supersedes_key)) is not None:
            supersedes = request_ids[_ref(supersedes_key)]
        elif slot_ids.get(supersedes_key) is not None:
            supersedes, kind = slot_ids[supersedes_key], "slot"
        elif supersedes_key or slot_key:
            notes.append(f"retraction_target_unknown:{r_position}")
        builder.add_retraction(target or "request", supersedes=supersedes, supersedes_kind=kind,
                               span=_locate(canonical, entry.get("text")))

    presentation = payload.get("presentation")
    if isinstance(presentation, Mapping):
        fmt = _FORMAT_ALIASES.get(_str(presentation.get("format") or presentation.get("fmt")).lower(), OutputFormat.UNSPECIFIED)
        builder.set_presentation(fmt=fmt, fields=tuple(_keys(presentation.get("fields"))),
                                 ordering=_str(presentation.get("ordering")), units=_str(presentation.get("units")),
                                 brevity=_str(presentation.get("brevity")), literal_output=bool(presentation.get("literal_output")))

    graph = builder.build()
    if graph.uncovered_source:
        notes.append(f"unresolved_source_stretches:{len(graph.uncovered_source)}")
    return ParsedGraph(graph, False, "", tuple(notes))


def builder_slots_for(builder: RequestGraphBuilder, request_id: RequestId) -> tuple[SlotId, ...]:
    """The slot ids a request has accumulated so far (read-back for VALUE dependencies)."""
    draft = builder._requests.get(request_id)  # parser and builder are one seam
    return tuple(draft.slot_ids) if draft is not None else ()


__all__ = ["ParsedGraph", "extract_reply_object", "parse_graph_reply"]
