"""RequestGraph <-> the conductor's semantic frames (``core.conductor.semantic_proof``).

The conductor already carries operand roles by meaning (``SemanticRoleEvidence`` with explicit
coordination membership) and a validation step that re-reads every surface from the canonical text.
This bridge lets a graph feed that pipeline without a second ontology: a live request becomes an
AFFIRMED frame whose roles are the slot operands' mentions, a prohibition becomes a NEGATED frame,
and a proven turn proof becomes a graph whose requests are its frames.
"""
from __future__ import annotations

from typing import Any

from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_builder import GraphBuildError, RequestGraphBuilder, operand
from core.semantic.request_graph import InterpretationState, Quantity, RequestGraph, SemanticRole

#: Frame role names -> operand roles by meaning. Anything unlisted is OTHER (never dropped).
ROLE_NAME_TO_SEMANTIC: dict[str, SemanticRole] = {
    "subject": SemanticRole.SUBJECT, "location": SemanticRole.SUBJECT, "place": SemanticRole.SUBJECT,
    "city": SemanticRole.SUBJECT, "asset": SemanticRole.SUBJECT, "expression": SemanticRole.SUBJECT,
    "source": SemanticRole.SOURCE, "from": SemanticRole.SOURCE, "base_currency": SemanticRole.SOURCE,
    "target": SemanticRole.TARGET, "to": SemanticRole.TARGET, "quote_currency": SemanticRole.TARGET,
    "payment": SemanticRole.PAYMENT, "with": SemanticRole.PAYMENT,
    "destination": SemanticRole.DESTINATION,
    "comparison_a": SemanticRole.COMPARISON_A, "a": SemanticRole.COMPARISON_A, "left": SemanticRole.COMPARISON_A,
    "comparison_b": SemanticRole.COMPARISON_B, "b": SemanticRole.COMPARISON_B, "right": SemanticRole.COMPARISON_B,
    "result_ref": SemanticRole.RESULT_REF,
}


#: Operand role -> the role NAME a registered frame family speaks. The closed FX grammar names its
#: roles base_currency/quote_currency/amount; a family not listed keeps the semantic role's value.
SEMANTIC_TO_ROLE_NAME_BY_FAMILY: dict[str, dict[SemanticRole, str]] = {
    "fx_quote": {SemanticRole.SOURCE: "base_currency", SemanticRole.TARGET: "quote_currency"},
    "calculation": {SemanticRole.SUBJECT: "expression"},
}


def _role_name(family: str, role: SemanticRole) -> str:
    return SEMANTIC_TO_ROLE_NAME_BY_FAMILY.get(family, {}).get(role, role.value)


def frames_from_graph(graph: RequestGraph) -> tuple[Any, ...]:
    """Every live request as an AFFIRMED ``SemanticFrameEvidence``; every prohibition as NEGATED."""
    from core.conductor.semantic_proof import (
        Polarity,
        ProofProvenance,
        SemanticFrameEvidence,
        SemanticRoleEvidence,
    )

    mentions = {m.id: m for m in graph.mentions}
    superseded = {r.supersedes for r in graph.retractions if r.supersedes}
    whole = graph.canonical.span(0, graph.canonical.length)
    frames: list[Any] = []
    for request in graph.requests:
        if request.unresolved or request.id in superseded:
            continue
        scope = request.span or whole
        roles: list[Any] = []
        family = request.family or "request"
        for slot in graph.slots_for(request.id):
            ordinal_by_role: dict[str, int] = {}
            for op in slot.operands:
                mention = mentions.get(op.mention_id) if op.mention_id else None
                if mention is None:
                    continue
                name = _role_name(family, op.role)
                ordinal = ordinal_by_role.get(name, 0)
                ordinal_by_role[name] = ordinal + 1
                roles.append(SemanticRoleEvidence(
                    role_name=name, value_span=mention.span,
                    coordination_group_id=f"{slot.id}:{name}", member_ordinal=ordinal,
                ))
                if op.quantity is not None and op.quantity.raw and family == "fx_quote":
                    amount = graph.canonical.find(op.quantity.exact or op.quantity.raw, start=scope.start)
                    if amount is not None and amount.end <= scope.end:
                        roles.append(SemanticRoleEvidence(
                            role_name="amount", value_span=amount,
                            coordination_group_id=f"{slot.id}:amount", member_ordinal=0,
                        ))
        frames.append(SemanticFrameEvidence(
            frame_id=str(request.id), frame_scope=scope, predicate_span=scope,
            family=family, polarity=Polarity.AFFIRMED, roles=tuple(roles),
            provenance=ProofProvenance.BOUNDED_MODEL,
        ))
    for prohibition in graph.prohibitions:
        scope = prohibition.span or whole
        frames.append(SemanticFrameEvidence(
            frame_id=str(prohibition.id), frame_scope=scope, predicate_span=scope,
            family=prohibition.target, polarity=Polarity.NEGATED, provenance=ProofProvenance.BOUNDED_MODEL,
        ))
    return tuple(frames)


def graph_from_proof(proof: Any, *, turn_id: str = "") -> RequestGraph:
    """A proven turn (``SemanticTurnProof``) as a graph: one request + slot per AFFIRMED/UNRESOLVED
    frame with its roles as operands, one prohibition per NEGATED frame. Unclaimed text is conserved."""
    from core.conductor.semantic_proof import Polarity

    canonical: CanonicalText = proof.canonical
    builder = RequestGraphBuilder(canonical, turn_id=turn_id)
    for frame in proof.frames:
        if frame.polarity is Polarity.NEGATED:
            builder.add_prohibition(str(frame.family), span=frame.scope)
            continue
        unresolved = frame.polarity is not Polarity.AFFIRMED
        rid = builder.add_request(
            frame.surface, span=frame.scope, family=str(frame.family), unresolved=unresolved,
            state=InterpretationState.UNRESOLVED if unresolved else InterpretationState.RESOLVED,
        )
        if unresolved:
            continue
        operands = []
        amount = next((r for r in frame.roles if str(r.role_name) in {"amount", "quantity"}), None)
        for role in frame.roles:
            if role is amount:
                continue  # a quantity is not an entity; it rides on the operand it measures
            semantic = ROLE_NAME_TO_SEMANTIC.get(str(role.role_name), SemanticRole.OTHER)
            try:
                mention = builder.add_mention(role.surface, within=frame.scope, kind=str(role.role_name), role=semantic)
            except GraphBuildError:
                continue
            quantity = None
            if amount is not None and semantic in {SemanticRole.SOURCE, SemanticRole.SUBJECT} and not any(
                op.quantity is not None for op in operands
            ):
                raw = str(amount.surface)
                quantity = Quantity(raw=f"{raw} {role.surface}", exact=raw.replace(",", "").strip(),
                                    currency=role.surface.upper() if str(role.role_name).endswith("currency") else "")
            operands.append(operand(semantic, mention_id=mention, quantity=quantity))
        builder.add_slot(rid, expected=frame.surface, operands=tuple(operands))
    return builder.build()


__all__ = ["ROLE_NAME_TO_SEMANTIC", "SEMANTIC_TO_ROLE_NAME_BY_FAMILY", "frames_from_graph", "graph_from_proof"]
