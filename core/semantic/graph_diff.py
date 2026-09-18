"""Per-axis comparison of a CANDIDATE RequestGraph against a GOLD RequestGraph (Differential V3).

The V2 scorer compared clause COUNTS, so unrelated invented questions scored 20/20 when their counts
matched -- it measured nothing about meaning. This comparator scores every semantic axis
INDEPENDENTLY and requires STRICT whole-turn correctness (every axis must pass), so a reading cannot
win by getting a count right, and a report can say WHICH axis a producer gets wrong.

Two graphs of the same turn carry independent ids (different producers mint different ids), so
nothing here compares ids across graphs. Alignment is by SOURCE POSITION on the shared canonical
text: a candidate request is matched to the gold request whose source span it best overlaps, and
within an aligned pair slots are matched by meaning (expected text, then operand role/value pairs,
then order). A candidate that invents unrelated requests overlaps nothing, so the gold requests read
as MISSING and the candidate ones as INVENTED -- both axes fail. That is how the review's exploit is
caught. Mentions are compared by span, so the same surface at a different OCCURRENCE is a different
entity.

Nothing here is authoritative over execution; it produces a measurement, not a decision.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from core.semantic.canonical_text import EntitySpan
from core.semantic.graph_builder import content_tokens
from core.semantic.request_graph import (
    ConstraintKind,
    InterpretationState,
    Operand,
    RequestGraph,
    Slot,
)

# Axis names -- each is scored independently. Kept as constants so tests and reports share one vocab.
AXES = (
    "slot_coverage",         # every gold request AND every gold slot is present; none invented
    "request_meaning",       # aligned requests are the same request (not an unrelated invention)
    "roles",                 # each entity carries the same role by MEANING, not by position
    "operands",              # operand values (mention/quantity/result-ref) match per role
    "quantities",            # quantity identity (value/unit/currency/asset/chain) matches
    "entity_identity",       # aligned mentions are the same occurrence and the same entity key
    "constraints",           # time/location/freshness/output/user-restriction constraints, scoped
    "prohibitions",          # every gold prohibition is represented (and none invented)
    "prohibition_scope",     # each prohibition applies to the same requests
    "retractions",           # every gold retraction is represented
    "retraction_target",     # each retraction supersedes the same request/slot
    "ambiguity",             # gold ambiguity is preserved with the same alternatives, not collapsed
    "interpretation_state",  # aligned requests/slots carry the same typed state (no false UNKNOWN,
                             # no invented certainty, no false NEEDS_INPUT)
    "dependencies",          # dependency edges match (no dropped/invented/rewired edge)
    "dependency_target",     # each gold edge's target and value reference are the aligned ones
    "retrieval_required",    # retrieval-required/freshness applies to the same requests
    "retrieval_forbidden",   # retrieval/tool-forbidden applies to the same requests
    "tool_conflict",         # the candidate never demands retrieval where gold forbids it
    "presentation",          # requested output contract preserved
    "source_binding",        # every content stretch gold accounts for is accounted for by the candidate
)

_REQUIRED_KINDS = frozenset({ConstraintKind.RETRIEVAL_REQUIRED, ConstraintKind.FRESHNESS})
_FORBIDDEN_KINDS = frozenset({ConstraintKind.RETRIEVAL_FORBIDDEN, ConstraintKind.TOOL_FORBIDDEN})
_SCOPED_KINDS = frozenset(
    {
        ConstraintKind.TIME, ConstraintKind.LOCATION, ConstraintKind.OUTPUT_FORMAT,
        ConstraintKind.USER_RESTRICTION,
        # A recency cue is also a scoped constraint in its own right: it is folded into the
        # retrieval-required set, but dropping the cue while keeping retrieval must still fail.
        ConstraintKind.FRESHNESS,
    }
)
_WHOLE_TURN = "*"


@dataclass(frozen=True)
class AxisResult:
    axis: str
    matched: bool
    detail: str = ""


@dataclass(frozen=True)
class GraphComparison:
    axes: tuple[AxisResult, ...]
    aligned_pairs: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    missing_requests: tuple[str, ...] = field(default_factory=tuple)
    invented_requests: tuple[str, ...] = field(default_factory=tuple)
    aligned_slots: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    missing_slots: tuple[str, ...] = field(default_factory=tuple)
    invented_slots: tuple[str, ...] = field(default_factory=tuple)

    @property
    def by_axis(self) -> dict[str, AxisResult]:
        return {a.axis: a for a in self.axes}

    @property
    def whole_turn_correct(self) -> bool:
        """Strict: every axis passes. This is the promotion-relevant verdict, not a per-axis average."""
        return all(a.matched for a in self.axes)

    def failed_axes(self) -> tuple[str, ...]:
        return tuple(a.axis for a in self.axes if not a.matched)

    def to_dict(self) -> dict:
        return {
            "whole_turn_correct": self.whole_turn_correct,
            "axes": {a.axis: {"matched": a.matched, "detail": a.detail} for a in self.axes},
            "missing_requests": list(self.missing_requests),
            "invented_requests": list(self.invented_requests),
            "missing_slots": list(self.missing_slots),
            "invented_slots": list(self.invented_slots),
        }


# -- alignment ---------------------------------------------------------------


def _span_range(request, graph: RequestGraph) -> tuple[int, int] | None:
    if request.span is not None:
        return (request.span.start, request.span.end)
    found = graph.canonical.find(request.source_text) if request.source_text else None
    return (found.start, found.end) if found is not None else None


def _overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> int:
    if a is None or b is None:
        return 0
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def align_requests(gold: RequestGraph, cand: RequestGraph) -> tuple[
    list[tuple[str, str]], list[str], list[str]
]:
    """Match candidate requests to gold requests by best source-span overlap.

    Returns (aligned_pairs, missing_gold_ids, invented_cand_ids). Source-position overlap is only
    meaningful when both graphs describe the SAME turn, so a candidate whose canonical differs from
    gold's aligns to nothing -- every gold request MISSING, every candidate request INVENTED.
    """
    if gold.canonical.digest != cand.canonical.digest:
        return [], [r.id for r in gold.requests], [r.id for r in cand.requests]
    gold_ranges = {r.id: _span_range(r, gold) for r in gold.requests}
    cand_ranges = {r.id: _span_range(r, cand) for r in cand.requests}
    candidates = []
    for gid, gr in gold_ranges.items():
        for cid, cr in cand_ranges.items():
            ov = _overlap(gr, cr)
            if ov > 0:
                candidates.append((ov, gid, cid))
    aligned: list[tuple[str, str]] = []
    used_gold: set[str] = set()
    used_cand: set[str] = set()
    for _ov, gid, cid in sorted(candidates, key=lambda t: -t[0]):
        if gid in used_gold or cid in used_cand:
            continue
        aligned.append((gid, cid))
        used_gold.add(gid)
        used_cand.add(cid)
    missing = [gid for gid in gold_ranges if gid not in used_gold]
    invented = [cid for cid in cand_ranges if cid not in used_cand]
    return aligned, missing, invented


def _operand_kind(op: Operand) -> str:
    if op.result_ref is not None:
        return "result_ref"
    if op.mention_id is not None and op.quantity is not None:
        return "mention+quantity"
    if op.mention_id is not None:
        return "mention"
    if op.quantity is not None:
        return "quantity"
    return "empty"


def _mention_span(op: Operand, graph: RequestGraph) -> EntitySpan | None:
    if op.mention_id is None:
        return None
    for m in graph.mentions:
        if m.id == op.mention_id:
            return m.span
    return None


def _mention_key(op: Operand, graph: RequestGraph) -> str:
    if op.mention_id is None:
        return ""
    for m in graph.mentions:
        if m.id == op.mention_id:
            return m.entity_key.strip().casefold()
    return ""


def _operand_value(op: Operand, graph: RequestGraph) -> str:
    span = _mention_span(op, graph)
    if span is not None:
        try:
            return span.resolve(graph.canonical).strip().casefold()
        except Exception:
            return ""
    if op.quantity is not None:
        q = op.quantity
        return f"qty:{q.exact}:{(q.asset or q.currency or q.unit).casefold()}"
    if op.result_ref is not None:
        return "result_ref"
    return ""


def _role_value_pairs(slot: Slot, graph: RequestGraph) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((op.role.value, _operand_value(op, graph)) for op in slot.operands))


def _slot_signature(slot: Slot, graph: RequestGraph) -> tuple:
    """A slot's MEANING: its operands by role and value, and its quantity identity.

    The free-text ``expected`` label is deliberately NOT part of it: two producers describe one slot
    in different words ("gold spot price" / "the price of gold"), and a meaning axis that compared
    phrasing would fail every producer but the one that wrote the gold. The label decides only when
    a slot carries no operand and no quantity, because then the words are all there is."""
    q = slot.quantity
    qsig = (q.exact, q.unit, q.currency, q.asset, q.chain) if q is not None else ()
    pairs = _role_value_pairs(slot, graph)
    label = slot.expected.strip().casefold() if not pairs and not qsig else ""
    return (label, pairs, qsig)


def align_slots(
    gold_slots: Iterable[Slot], cand_slots: Iterable[Slot], gold: RequestGraph, cand: RequestGraph
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Match slots of one aligned request pair by MEANING, then by order for the remainder.

    Score = expected-text match (weight 3) + shared (role, value) operand pairs. Leftover slots pair
    by order only while both sides still have slots; whatever remains is missing/invented.
    """
    g_slots = list(gold_slots)
    c_slots = list(cand_slots)
    scored = []
    for gi, gs in enumerate(g_slots):
        g_pairs = set(_role_value_pairs(gs, gold))
        for ci, cs in enumerate(c_slots):
            score = 0
            if gs.expected.strip() and gs.expected.strip().casefold() == cs.expected.strip().casefold():
                score += 3
            score += len(g_pairs & set(_role_value_pairs(cs, cand)))
            if score > 0:
                scored.append((score, gi, ci))
    aligned: list[tuple[str, str]] = []
    used_g: set[int] = set()
    used_c: set[int] = set()
    for _score, gi, ci in sorted(scored, key=lambda t: (-t[0], t[1], t[2])):
        if gi in used_g or ci in used_c:
            continue
        aligned.append((g_slots[gi].id, c_slots[ci].id))
        used_g.add(gi)
        used_c.add(ci)
    rest_g = [i for i in range(len(g_slots)) if i not in used_g]
    rest_c = [i for i in range(len(c_slots)) if i not in used_c]
    for gi, ci in zip(rest_g, rest_c, strict=False):
        aligned.append((g_slots[gi].id, c_slots[ci].id))
        used_g.add(gi)
        used_c.add(ci)
    missing = [g_slots[i].id for i in range(len(g_slots)) if i not in used_g]
    invented = [c_slots[i].id for i in range(len(c_slots)) if i not in used_c]
    return aligned, missing, invented


# -- scoped-constraint helpers -------------------------------------------------


def _expand_scope(scope: tuple[str, ...], graph: RequestGraph) -> frozenset[str]:
    if scope:
        return frozenset(scope)
    ids = frozenset(r.id for r in graph.requests)
    return ids if ids else frozenset({_WHOLE_TURN})


def _map_ids(ids: Iterable[str], mapping: dict[str, str]) -> frozenset[str]:
    return frozenset(mapping.get(i, i) for i in ids)


def _requests_under(graph: RequestGraph, kinds: frozenset[ConstraintKind]) -> frozenset[str]:
    out: set[str] = set()
    for c in graph.constraints:
        if c.kind in kinds:
            out |= _expand_scope(c.scope, graph)
    return frozenset(out)


def _covered_token_indices(graph: RequestGraph) -> frozenset[int]:
    intervals: list[tuple[int, int]] = []
    for r in graph.requests:
        rng = _span_range(r, graph)
        if rng is not None:
            intervals.append(rng)
    for m in graph.mentions:
        intervals.append((m.span.start, m.span.end))
    for holder in (*graph.constraints, *graph.prohibitions, *graph.retractions):
        if holder.span is not None:
            intervals.append((holder.span.start, holder.span.end))
    covered: set[int] = set()
    for index, (start, end, _word) in enumerate(content_tokens(graph.canonical.text)):
        if any(start < i_end and i_start < end for i_start, i_end in intervals):
            covered.add(index)
    return frozenset(covered)


def _constraint_text(c, graph: RequestGraph) -> str:
    if c.detail.strip():
        return c.detail.strip().casefold()
    if c.span is not None:
        try:
            return c.span.resolve(graph.canonical).strip().casefold()
        except Exception:
            return ""
    return ""


# -- the comparison ----------------------------------------------------------


def compare_graphs(gold: RequestGraph, cand: RequestGraph) -> GraphComparison:
    aligned, missing, invented = align_requests(gold, cand)
    gid_to_cid = dict(aligned)
    same_turn = gold.canonical.digest == cand.canonical.digest
    gold_slots_by_req = {r.id: gold.slots_for(r.id) for r in gold.requests}
    cand_slots_by_req = {r.id: cand.slots_for(r.id) for r in cand.requests}
    gold_slot = {s.id: s for s in gold.slots}
    cand_slot = {s.id: s for s in cand.slots}

    slot_pairs: list[tuple[str, str]] = []
    missing_slots: list[str] = []
    invented_slots: list[str] = []
    for gid, cid in aligned:
        pairs, miss, inv = align_slots(gold_slots_by_req[gid], cand_slots_by_req[cid], gold, cand)
        slot_pairs.extend(pairs)
        missing_slots.extend(miss)
        invented_slots.extend(inv)
    for gid in missing:
        missing_slots.extend(s.id for s in gold_slots_by_req[gid])
    for cid in invented:
        invented_slots.extend(s.id for s in cand_slots_by_req[cid])
    sid_to_cid = dict(slot_pairs)
    coverage_ok = not missing and not invented and not missing_slots and not invented_slots

    axes: list[AxisResult] = []

    def add(axis: str, ok: bool, detail: str = "") -> None:
        axes.append(AxisResult(axis, bool(ok), detail if not ok else ""))

    add("slot_coverage", coverage_ok,
        f"missing_requests={missing} invented_requests={invented} "
        f"missing_slots={missing_slots} invented_slots={invented_slots}")

    # request_meaning: aligned requests must carry the same slot meaning signatures.
    meaning_ok = not missing and not invented
    for gid, cid in aligned:
        gsig = sorted(_slot_signature(s, gold) for s in gold_slots_by_req[gid])
        csig = sorted(_slot_signature(s, cand) for s in cand_slots_by_req[cid])
        if gsig != csig:
            meaning_ok = False
    add("request_meaning", meaning_ok, "aligned requests differ in slot meaning")

    # roles: the role attached to each ENTITY (by span) must match; quantity-only roles by multiset.
    roles_ok = coverage_ok
    for gsid, csid in slot_pairs:
        gs, cs = gold_slot[gsid], cand_slot[csid]
        c_entities = [(_mention_span(op, cand), op.role.value) for op in cs.operands if op.mention_id is not None]
        for op in gs.operands:
            g_span = _mention_span(op, gold)
            if g_span is None:
                continue
            for c_span, c_role in c_entities:
                if c_span is not None and same_turn and c_span.overlaps(g_span) and c_role != op.role.value:
                    roles_ok = False
        g_q_roles = sorted(op.role.value for op in gs.operands if op.mention_id is None and op.quantity is not None)
        c_q_roles = sorted(op.role.value for op in cs.operands if op.mention_id is None and op.quantity is not None)
        if g_q_roles != c_q_roles:
            roles_ok = False
        if sorted((op.role.value, _operand_kind(op)) for op in gs.operands) != sorted(
            (op.role.value, _operand_kind(op)) for op in cs.operands
        ):
            roles_ok = False
    add("roles", roles_ok, "an entity carries a different role")

    # operands: role -> value identity (catches swapped target/payment at the same count).
    operands_ok = coverage_ok
    for gsid, csid in slot_pairs:
        if _role_value_pairs(gold_slot[gsid], gold) != _role_value_pairs(cand_slot[csid], cand):
            operands_ok = False
    add("operands", operands_ok, "operand values differ per role")

    # quantities: dimensional identity per aligned slot.
    def _qsigs(slot: Slot):
        out = []
        for op in slot.operands:
            if op.quantity is not None:
                q = op.quantity
                out.append((q.exact, q.unit.casefold(), q.currency.casefold(), q.asset.casefold(), q.chain.casefold()))
        if slot.quantity is not None:
            q = slot.quantity
            out.append((q.exact, q.unit.casefold(), q.currency.casefold(), q.asset.casefold(), q.chain.casefold()))
        return sorted(out)

    quantities_ok = coverage_ok
    for gsid, csid in slot_pairs:
        if _qsigs(gold_slot[gsid]) != _qsigs(cand_slot[csid]):
            quantities_ok = False
    add("quantities", quantities_ok, "quantity identity differs")

    # entity_identity: same occurrence (span) and same entity key per aligned operand role.
    identity_ok = coverage_ok
    for gsid, csid in slot_pairs:
        gs, cs = gold_slot[gsid], cand_slot[csid]
        # Several operands may share a role ("gold or silver" is two TARGETs), so a candidate
        # operand is matched by role AND by the occurrence it points at, never by role alone.
        cand_ops = [(op, _mention_span(op, cand)) for op in cs.operands if op.mention_id is not None]
        for op in gs.operands:
            g_span = _mention_span(op, gold)
            if g_span is None:
                continue
            match = next(
                (c_op for c_op, c_span in cand_ops
                 if c_op.role is op.role and c_span is not None and same_turn and c_span.overlaps(g_span)),
                None,
            )
            if match is None:
                identity_ok = False
                continue
            g_key = _mention_key(op, gold)
            if g_key and _mention_key(match, cand) != g_key:
                identity_ok = False
    add("entity_identity", identity_ok, "a mention is a different occurrence or entity")

    # constraints: scoped time/location/output/user-restriction constraints (kind, text, scope).
    def _scoped(graph: RequestGraph, mapping: dict[str, str] | None):
        out = []
        for c in graph.constraints:
            if c.kind in _SCOPED_KINDS:
                scope = _expand_scope(c.scope, graph)
                out.append((c.kind.value, _constraint_text(c, graph), _map_ids(scope, mapping or {})))
        return sorted(out, key=lambda t: (t[0], t[1], sorted(t[2])))

    add("constraints", _scoped(gold, gid_to_cid) == _scoped(cand, None), "scoped constraints differ")

    # prohibitions / retractions: targets must be represented (by target string, case-folded).
    def _targets(items):
        return sorted(str(getattr(i, "target", "")).strip().casefold() for i in items)

    add("prohibitions", _targets(gold.prohibitions) == _targets(cand.prohibitions), "prohibition targets differ")

    def _by_target(items):
        out: dict[str, list] = {}
        for i in items:
            out.setdefault(str(i.target).strip().casefold(), []).append(i)
        return out

    scope_ok = True
    cand_prohib = _by_target(cand.prohibitions)
    for target, golds in _by_target(gold.prohibitions).items():
        cands = cand_prohib.get(target, [])
        g_scopes = sorted(sorted(_map_ids(_expand_scope(p.scope, gold), gid_to_cid)) for p in golds)
        c_scopes = sorted(sorted(_expand_scope(p.scope, cand)) for p in cands)
        if g_scopes != c_scopes:
            scope_ok = False
    add("prohibition_scope", scope_ok, "a prohibition applies to different requests")

    add("retractions", _targets(gold.retractions) == _targets(cand.retractions), "retraction targets differ")

    target_ok = True
    cand_retr = _by_target(cand.retractions)
    for target, golds in _by_target(gold.retractions).items():
        cands = cand_retr.get(target, [])
        g_sup = sorted(str(sid_to_cid.get(r.supersedes, gid_to_cid.get(r.supersedes, r.supersedes)) or "") for r in golds)
        c_sup = sorted(str(r.supersedes or "") for r in cands)
        if g_sup != c_sup:
            target_ok = False
    add("retraction_target", target_ok, "a retraction supersedes a different request/slot")

    # ambiguity: gold-ambiguous slots stay ambiguous, with the same alternatives.
    ambiguity_ok = coverage_ok
    for gsid, csid in slot_pairs:
        gs, cs = gold_slot[gsid], cand_slot[csid]
        g_amb = gs.state is InterpretationState.AMBIGUOUS or gs.ambiguity is not None
        c_amb = cs.state is InterpretationState.AMBIGUOUS or cs.ambiguity is not None
        if g_amb and not c_amb:
            ambiguity_ok = False
        elif g_amb and gs.ambiguity is not None and cs.ambiguity is not None:
            if {a.casefold() for a in gs.ambiguity.alternatives} != {a.casefold() for a in cs.ambiguity.alternatives}:
                ambiguity_ok = False
    add("ambiguity", ambiguity_ok, "ambiguity collapsed or alternatives differ")

    # interpretation_state: typed states agree on aligned requests and slots.
    state_ok = coverage_ok
    gold_req = {r.id: r for r in gold.requests}
    cand_req = {r.id: r for r in cand.requests}
    for gid, cid in aligned:
        if gold_req[gid].state is not cand_req[cid].state:
            state_ok = False
    for gsid, csid in slot_pairs:
        if gold_slot[gsid].state is not cand_slot[csid].state:
            state_ok = False
    add("interpretation_state", state_ok, "typed interpretation states differ")

    # dependencies: match edges by aligned endpoints + kind (no dropped/invented/rewired edge).
    def _edge_set(graph: RequestGraph, mapping: dict[str, str] | None):
        out = set()
        for d in graph.dependencies:
            frm = mapping.get(d.from_request, d.from_request) if mapping else d.from_request
            to = mapping.get(d.to_request, d.to_request) if mapping else d.to_request
            out.add((frm, to, d.kind.value))
        return out

    add("dependencies", _edge_set(gold, gid_to_cid) == _edge_set(cand, None), "dependency edges differ")

    # dependency_target: for each gold edge, the candidate edge from the same request+kind must point
    # at the aligned target and the aligned value slot.
    dep_target_ok = True
    for d in gold.dependencies:
        frm = gid_to_cid.get(d.from_request, d.from_request)
        wanted_to = gid_to_cid.get(d.to_request, d.to_request)
        wanted_ref = sid_to_cid.get(d.value_ref, d.value_ref) if d.value_ref else None
        siblings = [e for e in cand.dependencies if e.from_request == frm and e.kind is d.kind]
        if siblings and not any(e.to_request == wanted_to and (e.value_ref or None) == wanted_ref for e in siblings):
            dep_target_ok = False
    add("dependency_target", dep_target_ok, "a dependency points at a different target or value")

    # retrieval_required / retrieval_forbidden: the SAME requests carry the obligation.
    g_required = _map_ids(_requests_under(gold, _REQUIRED_KINDS), gid_to_cid)
    c_required = _requests_under(cand, _REQUIRED_KINDS)
    add("retrieval_required", g_required == c_required, "retrieval-required scope differs")
    g_forbidden = _map_ids(_requests_under(gold, _FORBIDDEN_KINDS), gid_to_cid)
    c_forbidden = _requests_under(cand, _FORBIDDEN_KINDS)
    add("retrieval_forbidden", g_forbidden == c_forbidden, "retrieval-forbidden scope differs")

    # tool_conflict: the candidate demands retrieval on a request gold forbids it for.
    conflict = g_forbidden & c_required
    whole_turn_forbidden = _WHOLE_TURN in g_forbidden and bool(c_required)
    add("tool_conflict", not conflict and not whole_turn_forbidden,
        f"retrieval demanded where forbidden: {sorted(conflict)}")

    # presentation: requested format + fields + literal-output must match (renderer contract).
    def _pres(graph: RequestGraph):
        p = graph.presentation
        return (p.fmt.value, tuple(p.fields), p.literal_output) if p is not None else ("unspecified", (), False)

    add("presentation", _pres(gold) == _pres(cand), "presentation contract differs")

    # source_binding: every content token gold accounts for, the candidate accounts for too.
    if same_turn:
        binding_ok = _covered_token_indices(gold) <= _covered_token_indices(cand)
    else:
        binding_ok = False
    add("source_binding", binding_ok, "the candidate drops source the gold accounts for")

    return GraphComparison(
        axes=tuple(axes),
        aligned_pairs=tuple(aligned),
        missing_requests=tuple(missing),
        invented_requests=tuple(invented),
        aligned_slots=tuple(slot_pairs),
        missing_slots=tuple(missing_slots),
        invented_slots=tuple(invented_slots),
    )


__all__ = ["AXES", "AxisResult", "GraphComparison", "align_requests", "align_slots", "compare_graphs"]
