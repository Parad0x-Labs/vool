"""The deterministic (lexical) ``RequestGraph`` producer: TODAY's reading, with stable ids.

This is not a new interpretation. It is the reading the runtime already acts on -- the demand
splitter's units, the whole-turn prohibition recognizer, the within-turn retraction detector, the
execution-requirements classifier and the conductor's closed formal grammars -- assembled ONCE into
a graph whose identities are the ones the obligation ledger already mints:

    RequestId == SlotId == DemandUnit.unit_id   ("u1", "u2", ...)

so a demand obligation ``ob:<attempt>:demand:u2`` and the graph slot ``u2`` are the same thing, and
the publication sweep can read the frozen slot set from the graph instead of re-lexing the request.

Every existing recognizer is REUSED, never re-implemented: this module holds no vocabulary of its
own beyond a small presentation-cue table. Where the heuristic reading is weak (no operand roles
outside the formal grammars, a retracted clause read as a request, "no web" minted as a unit) the
graph says so faithfully, because that gap is exactly what the model reading is measured against.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.graph_builder import GraphBuildError, RequestGraphBuilder, content_tokens, operand
from core.semantic.request_graph import (
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    Operand,
    OutputFormat,
    Quantity,
    RequestGraph,
    RequestId,
    SemanticRole,
)

PRODUCER_NAME = "lexical"
PRODUCER_VERSION = "vool.lexical_request_graph.v1"

#: Presentation cues the demand splitter records per unit (``DemandUnit.output_constraint``),
#: mapped onto the renderer contract. Accounting of a stated cue, not recognition of one.
_PRESENTATION_CUES: tuple[tuple[str, OutputFormat, bool], ...] = (
    ("json", OutputFormat.JSON, False),
    ("table", OutputFormat.TABLE, False),
    ("bullet", OutputFormat.LIST, False),
    ("list", OutputFormat.LIST, False),
    ("just the number", OutputFormat.LITERAL, True),
    ("only the number", OutputFormat.LITERAL, True),
    ("number only", OutputFormat.LITERAL, True),
    ("nothing else", OutputFormat.LITERAL, True),
    ("only", OutputFormat.LITERAL, True),
)

_FORMAL_ROLE_TO_SEMANTIC: dict[str, SemanticRole] = {
    "base_currency": SemanticRole.SOURCE,
    "quote_currency": SemanticRole.TARGET,
    "expression": SemanticRole.SUBJECT,
    "field_name": SemanticRole.OTHER,
    "field_value": SemanticRole.OTHER,
}


def _turn_text(turn: Any) -> str:
    text = getattr(turn, "user_text", None)
    return str(text if text is not None else turn or "")


def _unit_span(canonical: CanonicalText, unit: Any, start: int, end: int) -> EntitySpan | None:
    """The unit's span on the canonical text: its recorded offsets when they still address the
    same words, else a search for its text (normalization may have shifted offsets)."""
    if 0 <= start <= end <= canonical.length and canonical.text[start:end].strip() == str(unit.text).strip():
        stripped_lead = len(canonical.text[start:end]) - len(canonical.text[start:end].lstrip())
        stripped_tail = len(canonical.text[start:end]) - len(canonical.text[start:end].rstrip())
        return canonical.span(start + stripped_lead, end - stripped_tail)
    return canonical.find(str(unit.text))


def _formal_operands(builder: RequestGraphBuilder, frame: Any, within: EntitySpan) -> tuple[Operand, ...]:
    """Operands from one closed-grammar frame (FX pair with amount, an arithmetic expression)."""
    canonical = builder.canonical
    by_role: dict[str, list[Any]] = {}
    for role in frame.roles:
        by_role.setdefault(role.role_name, []).append(role)
    operands: list[Operand] = []
    amount = None
    for role in by_role.get("amount", ()):
        raw = role.value_span.resolve(canonical)
        amount = Quantity(raw=raw, exact=raw.replace(",", "").strip())
    for name, roles in by_role.items():
        if name == "amount":
            continue
        semantic = _FORMAL_ROLE_TO_SEMANTIC.get(name, SemanticRole.OTHER)
        for role in roles:
            surface = role.value_span.resolve(canonical)
            try:
                mention = builder.add_mention(
                    surface, within=within, kind=name, role=semantic,
                    entity_key=surface.upper() if name.endswith("currency") else "",
                )
            except GraphBuildError:
                continue
            quantity = None
            if name == "base_currency" and amount is not None:
                quantity = Quantity(raw=f"{amount.raw} {surface}", exact=amount.exact, currency=surface.upper())
            operands.append(operand(semantic, mention_id=mention, quantity=quantity))
    return tuple(operands)


def _presentation(builder: RequestGraphBuilder, cue: str) -> None:
    lowered = cue.lower()
    for needle, fmt, literal in _PRESENTATION_CUES:
        if needle in lowered:
            builder.set_presentation(fmt=fmt, literal_output=literal, brevity=cue)
            return
    builder.set_presentation(fmt=OutputFormat.PROSE, brevity=cue)


def lexical_request_graph(
    turn: Any,
    *,
    turn_id: str = "",
    interpretation: Any = None,
    source_context: Mapping[str, Any] | None = None,
) -> RequestGraph:
    """Build today's reading of ``turn`` (a ``TurnRequest`` or a string) as a RequestGraph.

    ``interpretation`` may be the ``RequestInterpretation`` a caller already computed for this text
    (the turn door computes it for the obligation mint); when omitted it is read once here.
    ``source_context`` is accepted for signature stability and deliberately UNUSED: construction is
    pure -- no turn state is read, frozen or registered (a test counts lifecycle registrations).
    """
    from core.agent_runtime.answer_coverage import interpret_request
    from core.conductor.semantic_proof import formal_grammar_evidence
    from core.execution_requirements import classify_requirements
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.turn_prohibitions import prohibitions_from_text
    from core.within_turn_retraction import retraction_cue_span

    text = _turn_text(turn)
    canonical = CanonicalText.of(text)
    reading = interpretation if interpretation is not None else interpret_request(text)
    builder = RequestGraphBuilder(canonical, turn_id=str(turn_id or getattr(turn, "turn_id", "") or ""))
    del source_context  # PURE: nothing here may read or freeze turn state (see classify_requirements)

    # The frozen prohibitions ride the TurnRequest; inherit them rather than re-read the text so a
    # tightened or conserved policy on the request is the one the graph records.
    frozen = getattr(turn, "prohibitions", None)
    prohibitions = frozen if frozen is not None and hasattr(frozen, "clauses") else prohibitions_from_text(text)
    negative_spans: list[EntitySpan] = []
    for clause in prohibitions.clauses:
        span = canonical.find(clause)
        if span is not None:
            negative_spans.append(span)

    frames = tuple(formal_grammar_evidence(canonical))

    # -- requests and their single slot (the ledger's demand grain) --------------------------
    request_ids: dict[str, RequestId] = {}
    unit_spans: dict[str, EntitySpan | None] = {}
    live_units = list(getattr(reading, "requests", ()))
    for unit in live_units:
        group_start, group_end = reading.group_span(unit.unit_id)
        span = _unit_span(canonical, unit, group_start, group_end) or _unit_span(canonical, unit, unit.start, unit.end)
        rid = builder.add_request(str(unit.text), span=span, request_id=str(unit.unit_id), state=InterpretationState.RESOLVED)
        request_ids[str(unit.unit_id)] = rid
        unit_spans[str(unit.unit_id)] = span

        state = InterpretationState.RESOLVED
        reason = ""
        if span is not None and any(span.start >= n.start and span.end <= n.end for n in negative_spans):
            state = InterpretationState.RETRIEVAL_FORBIDDEN
            reason = "the unit is a prohibition clause, not a request"
        elif getattr(unit, "unresolved_refs", ()):
            state = InterpretationState.NEEDS_INPUT
            reason = "unbound reference: " + ", ".join(str(r) for r in unit.unresolved_refs)
        else:
            # The classifier's own reading of the unit: a DIRECT unit that needs no tool and no
            # current information is answerable without one. (FX pairs are live: see below.)
            try:
                own = classify_requirements(str(unit.text))
                direct = (
                    own.answer_mode == "DIRECT" and not own.tools_required
                    and not own.current_information_required
                )
            except Exception:
                direct = False
            has_fx = span is not None and any(
                f.family == "fx_quote" and f.frame_scope.start >= span.start and f.frame_scope.end <= span.end
                for f in frames
            )
            if direct and not has_fx:
                state = InterpretationState.ANSWERABLE_WITHOUT_TOOL

        operands: tuple[Operand, ...] = ()
        if span is not None:
            for frame in frames:
                if frame.frame_scope.start >= span.start and frame.frame_scope.end <= span.end:
                    operands = operands + _formal_operands(builder, frame, span)
        builder.add_slot(rid, expected=str(unit.text), state=state, operands=operands, reason=reason, slot_id=str(unit.unit_id))

        for attached in reading.attached_to(unit.unit_id):
            if getattr(attached, "kind", "") == "constraint":
                builder.add_constraint(
                    ConstraintKind.USER_RESTRICTION, detail=str(attached.text),
                    span=_unit_span(canonical, attached, attached.start, attached.end), scope=(rid,),
                )
        if getattr(unit, "freshness", ""):
            cue_span = canonical.find(str(unit.freshness), start=span.start if span else 0) if span else None
            builder.add_constraint(ConstraintKind.FRESHNESS, detail=str(unit.freshness), span=cue_span, scope=(rid,))
        if getattr(unit, "output_constraint", ""):
            builder.add_constraint(ConstraintKind.OUTPUT_FORMAT, detail=str(unit.output_constraint), scope=(rid,))

    # -- unresolved fragments the splitter itself flagged: represented, never dropped -----------
    for unit in getattr(reading, "unresolved", ()):
        if str(unit.unit_id) in request_ids:
            continue
        span = _unit_span(canonical, unit, unit.start, unit.end)
        builder.add_request(str(unit.text), span=span, unresolved=True, request_id=f"unresolved:{unit.unit_id}")

    # -- dependencies the splitter recorded ("with it" -> the request it refers back to) ---------
    for unit in live_units:
        for dep in getattr(unit, "depends_on", ()):
            target = request_ids.get(str(dep))
            if target is None or target == request_ids[str(unit.unit_id)]:
                continue
            builder.add_dependency(request_ids[str(unit.unit_id)], target, kind=DependencyKind.VALUE, value_ref=target)  # type: ignore[arg-type]

    # -- prohibitions, at the whole-turn grain the recognizer reads them ------------------------
    for clause, span in zip(prohibitions.clauses, [canonical.find(c) for c in prohibitions.clauses], strict=True):
        sub = analyze_retrieval_constraints(clause)
        if sub.forbids_all_tools:
            targets = ["tools"]
        elif sub.forbids_external_retrieval:
            targets = ["web"]
        else:
            targets = sorted(sub.prohibited_toolsets) or [clause.strip().lower()]
        for target in targets:
            builder.add_prohibition(target, span=span)
    if "tools" in prohibitions.families:
        builder.add_constraint(ConstraintKind.TOOL_FORBIDDEN, detail="explicit_tool_prohibition")
    elif "web" in prohibitions.families:
        builder.add_constraint(ConstraintKind.RETRIEVAL_FORBIDDEN, detail="explicit_retrieval_prohibition")
    for toolset in sorted(prohibitions.prohibited_toolsets):
        if "web" not in prohibitions.families and "tools" not in prohibitions.families:
            builder.add_constraint(ConstraintKind.RETRIEVAL_FORBIDDEN, detail=toolset)
    for family in ("write", "spend", "local_model"):
        if family in prohibitions.families:
            builder.add_constraint(ConstraintKind.USER_RESTRICTION, detail=f"explicit_{family}_prohibition")

    # -- a within-turn retraction: the withdrawn requests are superseded, the cue is source ------
    cue = retraction_cue_span(text)
    if cue is not None:
        cue_start, cue_end = cue
        cue_span = canonical.span(cue_start, cue_end) if cue_end > cue_start else None
        withdrawn = [
            u for u in live_units
            if unit_spans.get(str(u.unit_id)) is not None and unit_spans[str(u.unit_id)].end <= cue_end
        ]
        if withdrawn:
            for u in withdrawn:
                tokens = content_tokens(str(u.text))
                builder.add_retraction(tokens[0][2] if tokens else "instruction",
                                       supersedes=request_ids[str(u.unit_id)], supersedes_kind="request",
                                       span=cue_span)
        else:
            # The splitter already dropped the withdrawn text (it reads the live remainder). The
            # withdrawn instruction is still SOURCE: represent it as a request the retraction
            # supersedes -- no slot, so it is no obligation -- rather than let it vanish.
            withdrawn_text = text[:cue_start].strip(" ,;:.!-\t\r\n")
            tokens = content_tokens(withdrawn_text)
            if tokens:
                w_span = canonical.find(withdrawn_text)
                wid = builder.add_request(withdrawn_text, span=w_span, request_id="withdrawn:0")
                builder.add_retraction(tokens[0][2], supersedes=wid, supersedes_kind="request", span=cue_span)
            else:
                builder.add_retraction("instruction", span=cue_span)

    # -- retrieval obligations, per request where the classifier reads one -----------------------
    required: list[RequestId] = []
    for unit in live_units:
        span = unit_spans.get(str(unit.unit_id))
        try:
            req = classify_requirements(str(unit.text))
            live = bool(req.tools_required) or req.answer_mode in {"LIVE_DATA", "GROUNDED"}
        except Exception:
            live = False
        if not live and span is not None:
            live = any(f.family == "fx_quote" and f.frame_scope.start >= span.start and f.frame_scope.end <= span.end for f in frames)
        if live:
            required.append(request_ids[str(unit.unit_id)])
    if required:
        builder.add_constraint(ConstraintKind.RETRIEVAL_REQUIRED, scope=tuple(required))
    else:
        try:
            whole = classify_requirements(text)
            if live_units and (bool(whole.tools_required) or whole.answer_mode in {"LIVE_DATA", "GROUNDED"}):
                builder.add_constraint(ConstraintKind.RETRIEVAL_REQUIRED, detail="whole_turn")
        except Exception:
            pass

    # -- presentation: the first stated output cue -----------------------------------------------
    for unit in live_units:
        cue = str(getattr(unit, "output_constraint", "") or "")
        if cue:
            _presentation(builder, cue)
            break

    return builder.build()


__all__ = ["PRODUCER_NAME", "PRODUCER_VERSION", "lexical_request_graph"]
