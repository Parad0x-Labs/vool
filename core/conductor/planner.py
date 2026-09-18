"""Decomposition: the model proposes, the runtime disposes.

The division of labour is the whole design. A model is good at reading a sentence and saying "this
message makes four requests, and the fourth needs the answers to the second and third". A model is
unreliable at enumerating entities, at inventing stable ids, and at never hallucinating a subject
the user did not write. So the model supplies *structure* and the runtime supplies *everything
that must be true*:

* **No invented content.** Every content word in every proposed clause must already appear in the
  user's message. This reuses `turn_planner.verify_plan` rather than reimplementing it -- it is the
  safety property that makes trusting a model-authored plan defensible at all.
* **No unknown operations.** An operation the registry does not serve becomes an UNRESOLVED node.
* **No entity guessing.** The operation's own recognizers expand a clause into per-entity nodes.
* **No invalid shape.** Duplicate ids, unknown dependencies and cycles reject the whole plan.

And the rule that gives this slice its name -- **fail closed**. A clause the planner names but that
no operation can serve does not vanish; it becomes an `UNRESOLVED` node that `compose_answer` is
required to carry into the answer. That is the direct fix for the measured defect: on the shipped
build the arithmetic in "What is 137 x 29? … Also get the current weather for Kaunas and Tallinn"
was not refused or reported, it was simply absent from a confident reply.

Deliberately NOT here: any punctuation, conjunction or keyword rule that splits the message itself.
`core.task_decomposer` splits on `["\\n", ".", ";", ",", " and ", " but "]` and fills the pieces from
canned per-task-class templates; that is the architecture this replaces, not a fallback it keeps.
When the model produces nothing usable, the conductor declines the turn and the existing lanes run
exactly as they do today.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from core.turn_contract import LaneProposal

from core.conductor.capabilities import (
    CapabilityDecision,
    OperationEffect,
    decide_operation_compatibility,
)
from core.conductor.graph import ConductorGraph, GraphRejectionError, build_graph
from core.conductor.node import ConductorNode
from core.conductor.obligations import (
    CanonicalObligations,
    Obligation,
    ObligationOrigin,
    ObligationState,
    canonical_subject_obligations,
    clause_residue_obligations,
)
from core.conductor.operations import register_slice_1_operations
from core.conductor.realization import (
    BoundExecutionPlan,
    CapabilityLookupState,
    NonExecutionEvidence,
    NonExecutionReason,
    RealizationBinding,
    RealizationLedger,
    bind_existing_nodes,
    plan_realizations,
    resolve_capabilities,
)
from core.conductor.registry import (
    UNRESOLVED_OPERATION,
    expand_clause,
    expand_named_clause,
    general_operations,
    known_operations,
    named_subjects,
    node_needs_generation,
    operation_catalog_text,
    operation_spec,
    realize_subject,
    value_label,
)
from core.conductor.requirement_projection import resolve_ledger
from core.conductor.requirements import RequirementLedger, capture_requirements
from core.conductor.shared_context import SharedTurnContext, extract_shared_context
from core.turn_ir import ClauseKind, TurnClause, parse_turn_ir

if TYPE_CHECKING:  # annotation only -- the conductor acquires no runtime dependency on core.semantic
    from core.semantic.canonical_text import EntitySpan

register_slice_1_operations()

#: A plan larger than this is not a message with several requests, it is a misparse.
MAX_PLANNED_CLAUSES = 8

#: The legacy live-data lane's operation VOCABULARY -- the precondition for even asking whether
#: that lane can take a plan, never the verdict. Membership alone proved nothing: the lane's
#: whole-text extraction loses entities that the conductor's clause-scoped expanders find (a run-on
#: "What is weather in Rome also ..." yields no weather subtask there), so a membership-only
#: decline silently dropped clauses. The verdict is the caller-supplied `lane_coverage_probe` in
#: `plan_conductor_turn`, which must prove per-entity coverage before the conductor lets go.
LANE_SERVED_OPERATIONS = frozenset({"weather_lookup", "market_quote", "comparison"})

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)

#: Compatibility verdict used when a proven typed frame of the operation's own family already
#: covers the clause. The proof read the exact text; the clause-kind classifier read the verb, and
#: the weaker reading may not refuse the stronger one. The operation's own expander is still the
#: admission -- this only skips the kind check, it cannot serve a clause.
_ALLOWED = CapabilityDecision(True)


def conductor_system_prompt() -> str:
    """Built from the registry, so a newly registered operation is offered without a prompt edit."""
    return (
        "You split a user's message into the separate requests it makes, so each can be answered.\n"
        "Return ONLY a JSON array. Each element is an object with:\n"
        '  "request"    - one self-contained request, in the user\'s own words\n'
        '  "operation"  - which kind of work answers it, from the list below\n'
        '  "depends_on" - array of indices of EARLIER requests whose ANSWERS this one needs\n'
        "\n"
        "Operations:\n"
        f"{operation_catalog_text()}\n"
        "\n"
        "Rules:\n"
        "- Use ONLY words that appear in the user's message. Never introduce a new place, asset,\n"
        "  entity, number or term. A request containing a word the user did not write is invalid.\n"
        "- One element per distinct kind of work. Do NOT split a list of places or assets into\n"
        "  separate elements -- 'weather for Kaunas and Tallinn' is ONE element.\n"
        "- A connected calculation is ONE quantitative_reasoning element, even when the user\n"
        "  enumerates several outputs. Keep its shared inputs, intermediate quantities, final\n"
        "  quantities and totals in that one recipe. Do not buy a separate generation for each\n"
        "  requested number. Independent calculations may remain separate. Required lookups\n"
        "  stay external dependencies, not calculations or invented supplied facts.\n"
        "- A question ABOUT the results ('which is warmer', 'which moved most', 'whether it has\n"
        "  X') is ALWAYS its own element, never folded into the lookup that feeds it, and it MUST\n"
        "  list that lookup's index in depends_on.\n"
        "- Style alone ('briefly', 'in detail', 'step by step') is not separate work. An explicit\n"
        "  request to present computed results in a table/chart or explain those results belongs\n"
        "  to result_presentation, with the computation as its dependency. Do not ask the\n"
        "  arithmetic recipe to generate prose or layout; it supplies the numbers and working.\n"
        "- Everything the user asked for must appear in exactly one element. Nothing may be left out.\n"
        "- No prose, no explanation, no markdown fence. Only the JSON array.\n"
        "\n"
        "Example. For:\n"
        '  "What is 12 x 5? Also get the weather for Riga and Vilnius and say which is colder."\n'
        "return exactly:\n"
        '[{"request":"What is 12 x 5?","operation":"calculation","depends_on":[]},\n'
        ' {"request":"get the weather for Riga and Vilnius","operation":"weather_lookup","depends_on":[]},\n'
        ' {"request":"which is colder","operation":"comparison","depends_on":[1]}]'
    )


@dataclass(frozen=True)
class ProposedClause:
    """One element of the model's reply, after parsing but before the runtime validates it.

    The last three fields are the forward compatibility slot for the typed semantic envelope. They
    default to "nothing was carried", which is exactly what today's planner produces -- `parse_clauses`
    does not set them, nothing reads them, and no node, schedule or composition observes them. A
    Phase-1 resolver that emits entity spans can fill them without this type changing again, and
    without the JSON contract in `conductor_system_prompt` changing at all.

    Appended rather than inserted, deliberately: `tests/test_conductor_plan_and_graph.py` builds two
    of these positionally with four arguments, and a field added anywhere but the end would break
    those silently by shifting what `depends_on` receives.

    What is NOT here, and will not be: any field naming a side-effect class, a permission, or an
    approval requirement. A clause is a model's proposal. Authority comes from the registry, and
    leaving it no field to write into is what makes that structural rather than a convention.
    """

    index: int
    request: str
    operation: str
    depends_on: tuple[int, ...]
    #: Where in the canonical user text this clause's entities are. Empty today. Spans rather than
    #: strings so a repeated entity is two references, not one deduplicated one.
    spans: tuple[EntitySpan, ...] = ()
    #: Which named text representation `spans` are measured against. Empty when there are none; a
    #: span without this is unresolvable, so the two travel together or not at all.
    canonical_representation: str = ""
    #: How this clause was produced -- "model" today, "certain_recognizer" when the formal fast path
    #: gains one. Provenance for the receipt, never a trust signal.
    origin: str = "model"
    #: A clause the deterministic planner already knows it cannot serve, with the FACT why ("no
    #: price source for lpg"). The node loop turns it straight into the fail-closed unresolved node
    #: instead of trying an adapter and reporting a routing excuse. Empty for model clauses.
    unresolved_reason: str = ""
    #: The RequestGraph identities this clause was projected from, when it was (see
    #: ``core.semantic.bridges.plan``). Provenance the planner may carry into its nodes; empty for
    #: a clause parsed from a model reply. Appended, defaulted, so positional construction holds.
    request_id: str = ""
    slot_ids: tuple[str, ...] = ()
    source_scope: tuple[int, int] | None = None


@dataclass
class ConductorPlan:
    plan_id: str
    original_request: str
    graph: ConductorGraph
    #: Clauses the planner named that no operation could serve. Already present in `graph` as
    #: UNRESOLVED nodes; kept separately so a caller can report them without walking the graph.
    unresolved: tuple[ConductorNode, ...] = ()
    decline_reason: str = ""
    clause_count: int = 0
    #: What the whole message established, extracted once. Every node runs with this available; see
    #: `core.conductor.shared_context` for why sharing it is safe and why sharing the raw text is not.
    shared_context: SharedTurnContext = field(default_factory=SharedTurnContext)
    #: What the USER asked for, owned by the runtime rather than by whatever the planner emitted.
    #: Built at plan time so the answer is judged against a contract instead of against itself; see
    #: `core.conductor.obligations`.
    obligations: CanonicalObligations = field(default_factory=CanonicalObligations)
    #: What the USER required, captured from their text before any resolution and independent of
    #: every clause below. The only semantic authority for requiredness; node edges are projected
    #: from it. See `core.conductor.requirements`.
    ledger: RequirementLedger = field(default_factory=RequirementLedger)
    #: The immutable realization ledger and its bindings -- exactly one per realization, either a
    #: node or typed non-execution evidence. The sole basis for accounting: a realization is never
    #: dropped by a dedupe branch, only bound to a node it shares with another realization.
    bound_plan: BoundExecutionPlan = field(default_factory=BoundExecutionPlan)
    #: requirement id -> the node ids realizing it. Derived from `bound_plan`; kept so a caller can
    #: answer "which nodes serve this requirement" without walking the bindings.
    requirement_nodes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def nodes(self) -> tuple[ConductorNode, ...]:
        return self.graph.nodes

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(node.operation for node in self.graph.nodes)

    @property
    def parallel_preferred(self) -> bool:
        """True when at least two nodes have no dependencies, so overlap is achievable at all."""
        return len(self.graph.roots) >= 2

    def requires_conductor(self) -> bool:
        """Whether this plan uses operations OUTSIDE the legacy live-data lane's vocabulary.

        A True here means no other lane can even name this work, so the conductor must keep it.
        A False is NOT a verdict that the lane will serve it -- only that the vocabulary matches.
        Whether the lane actually covers every planned entity is a separate, provable question
        answered by `plan_conductor_turn`'s `lane_coverage_probe`; deciding it by membership alone
        dropped clauses in production (the lane's whole-text extraction misses entities the
        clause-scoped expanders find).
        """
        return bool(self.operations - LANE_SERVED_OPERATIONS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "original_request": self.original_request,
            "clause_count": self.clause_count,
            "operations": sorted(self.operations),
            "parallel_preferred": self.parallel_preferred,
            "nodes": [node.to_dict() for node in self.graph.nodes],
        }


def parse_clauses(raw: str) -> list[ProposedClause]:
    """Parse the planner's reply. Returns [] for anything not a usable plan.

    Tolerates a markdown fence and surrounding prose because a model told not to emit them still
    sometimes does, and re-asking costs a whole round trip.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("["):
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, list) or not payload or len(payload) > MAX_PLANNED_CLAUSES:
        return []

    clauses: list[ProposedClause] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return []
        request = " ".join(str(item.get("request") or "").split())
        operation = str(item.get("operation") or "").strip()
        if not request or not operation:
            return []
        depends: list[int] = []
        for value in item.get("depends_on") or []:
            try:
                dep = int(value)
            except (TypeError, ValueError):
                return []
            # A dependency on itself or on a later clause is not a DAG. Rejecting here means the
            # cycle check downstream is a backstop rather than the only guard.
            if dep < 0 or dep >= index:
                return []
            depends.append(dep)
        clauses.append(
            ProposedClause(
                index=index, request=request, operation=operation, depends_on=tuple(depends)
            )
        )
    return clauses


def _verify_no_invented_content(clauses: Sequence[ProposedClause], original: str) -> bool:
    """Reuse `turn_planner.verify_plan`: a plan may only rearrange the user's own words."""
    from core.agent_runtime.turn_planner import PlannedTask, verify_plan

    tasks = [
        PlannedTask(index=clause.index, request=clause.request, depends_on=clause.depends_on)
        for clause in clauses
    ]
    return verify_plan(tasks, original)


def _clause_span(original: str, clause_text: str) -> tuple[int, int]:
    """Where `clause_text` sits in the canonical user text, or `(-1, -1)`.

    A LOCATION, not a comparison. `_verify_no_invented_content` has already proven a clause is made
    of the user's own words, so finding it yields the range it occupies -- and a realization with no
    arguments to key on binds by overlap between two ranges over one text. The
    `_normalize(a).startswith(_normalize(b)[:24])` test this replaces matched two different requests
    that happened to open with the same words.
    """
    from core.semantic.canonical_text import CanonicalText

    body = str(clause_text or "").strip()
    if not body:
        return (-1, -1)
    canonical = CanonicalText.of(original)
    found = canonical.find(body)
    return (found.start, found.end) if found is not None else (-1, -1)


def _ledger_required_edges(
    ledger_edges: dict[str, tuple[str, ...]],
    node: ConductorNode,
    edges: tuple[str, ...],
) -> tuple[str, ...]:
    """The subset of `edges` the requirement ledger says this node cannot answer without.

    Empty when the ledger has nothing to say about this node, which preserves the pre-ledger
    meaning of "all of them" for any family no frame grammar covers.
    """
    required = ledger_edges.get(node.node_id)
    if required is None:
        return ()
    return tuple(edge for edge in edges if edge in set(required))


def _normalize(text: str) -> str:
    """Comparison form for subject identity. Operations return canonical names; this only folds
    case and whitespace so two spellings of one canonical name are one subject."""
    return " ".join(str(text or "").casefold().split())


def _slug(text: str, limit: int = 32) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (cleaned or "node")[:limit]


#: How a clause points at work done ELSEWHERE IN THE SAME TURN instead of naming its own subject.
#: Closed structural vocabulary -- pronouns, plus the runtime's own words for its own work -- so it
#: decides DEPENDENCE and never an answer.
_TURN_SELF_REFERENCE_RE = re.compile(
    r"\b(?:that|those|it|its|them|they|this|these|"
    r"the\s+(?:above|previous|prior|former|latter|first|second|last|"
    r"calculation|computation|result|answer|reply|response|output|total|sum|figure))\b",
    re.IGNORECASE,
)


#: "no registered operation named 'calculation'" -- a NAMED operation the registry no longer has.
#: Deliberately does NOT match the empty name the planner leaves when it named nothing at all,
#: which is the ordinary unclaimed clause this pass exists to rescue.
_NAMED_OPERATION_MISSING_RE = re.compile(r"^no registered operation named '(?P<name>.+)'$")


def _names_its_own_subject(request_text: str) -> bool:
    """Whether this clause carries its own subject rather than pointing at a sibling's.

    Measured, because the two signals already available cannot tell these apart -- both
    `_asks_for_explanation` and `_stable_knowledge_capability_accepts` answer True for BOTH:

        "From memory: what is the boiling point of water at sea level in Celsius?"   <- independent
        "Explain the calculation briefly."                                           <- dependent

    Serving the dependent one as generic prose, with none of its sibling's computed value, is
    exactly the harm the narrow unclaimed-clause gate exists to prevent.
    """

    return not bool(_TURN_SELF_REFERENCE_RE.search(str(request_text or "")))


def _derived_family_recognizes(
    request: str, shared: SharedTurnContext
) -> bool:
    """Whether a DERIVED family's own recognizer claims this clause's shape.

    A derived operation (comparison and its kind) answers a question ABOUT its siblings'
    results. Its expander recognizing the clause — even though it cannot run without the
    dependencies — is the runtime's own proof that this is dependency-shaped work, and the
    knowledge family must not answer it standalone: "which city is warmer" rescued as prose
    is an invented answer about cities nobody named (pinned in the plan_and_graph family).
    """
    from core.conductor.registry import known_operations

    for spec in known_operations():
        if not spec.is_derived:
            continue
        argument_sets, _error = _try_expand(spec, request, shared)
        if argument_sets:
            return True
    return False


def _unresolved_node(
    plan_id: str, clause: ProposedClause, reason: str, dependency_ids: tuple[str, ...]
) -> ConductorNode:
    """The fail-closed node. Carries the user's own clause so the answer can name what it missed.

    A runtime-owned clause that arrives here with its OWN reason is a STATED REFUSAL -- the
    purchase arm declining one share of an allocation with the identifying question, the
    "no price source" row for an asset nothing quotes -- not a clause the planner could not bind.
    It is marked so the composer keeps its row beside the quotes it sits next to (FINDINGS F15:
    the BASE question and the parts-mismatch rows were dropped as "covered" by the BTC quote).
    """
    arguments: dict[str, Any] = {}
    if str(getattr(clause, "origin", "") or "") == _RUNTIME_OWNED and str(reason or "").strip():
        arguments["stated_refusal"] = True
    return ConductorNode(
        node_id=f"{plan_id}:unresolved:{clause.index}",
        operation=UNRESOLVED_OPERATION,
        request_text=clause.request,
        arguments=arguments,
        depends_on=dependency_ids,
        unresolved_reason=reason,
        can_run_in_parallel=False,
    )


def _try_expand(
    spec: Any,
    clause_text: str,
    shared: SharedTurnContext,
    *,
    explicitly_named: bool = False,
    dependency_values: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], str]:
    """`(argument_sets, error)`. An adapter fault is this clause's problem, not the plan's."""
    try:
        expand = expand_named_clause if explicitly_named else expand_clause
        return list(expand(spec, clause_text, shared, dependency_values) or []), ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"[:160]


def _resolve_clause(
    clause: ProposedClause,
    shared: SharedTurnContext,
    semantic_clause: TurnClause,
    dependency_effects: tuple[OperationEffect, ...] = (),
    dependency_values: tuple[str, ...] = (),
    proven_families: frozenset[str] = frozenset(),
) -> tuple[Any, list[dict[str, Any]], str]:
    """Which operation actually serves this clause, and with what arguments.

    The planner NAMES an operation; it does not get the last word on whether one exists that can
    serve the clause. A named operation is tried first and always wins when it expands. When it
    declines -- or when the model named something no registry entry serves at all -- the general
    operations are offered the clause in turn, and the first that expands takes it.

    That order matters in both directions. Trying general operations first would let a catch-all
    swallow "get the weather for Kaunas" from the adapter that does it properly. Not trying them at
    all is the measured defect: on the currency message the planner named `calculation` for
    "Calculate how much money they effectively spent during the trip", the arithmetic evaluator
    found no expression *inside that clause* -- every number is in the story sentence before it --
    and the clause died as "calculation found nothing to act on in this request". Seven clauses,
    seven of those, and the plan was declined whole.
    """
    # One exception to named-first, declared on the spec: an operation whose recognizer is the
    # runtime's own deterministic CLAIM on the clause binds before the planner's naming is
    # consulted. The planner names operations from a catalog, and a catalog the model reads has
    # no machine vocabulary -- measured at 1f8dba98: "how much free disk space do I have?" came
    # back named `quantitative_reasoning`, the named operation expanded, and the node failed
    # against a fact the host could have stated exactly. The claim registry
    # (`core.agent_runtime.intent_claims`) already proves what that clause is; this pass is what
    # propagates that proof into the plan instead of letting a model rename it.
    for spec in general_operations():
        if not spec.outranks_planner_naming:
            continue
        decision = decide_operation_compatibility(
            spec.capability,
            semantic_clause,
            dependency_effects=dependency_effects,
        )
        if not decision.allowed:
            continue
        argument_sets, _error = _try_expand(
            spec, clause.request, shared, dependency_values=dependency_values
        )
        if argument_sets:
            return spec, argument_sets, ""
    named = operation_spec(clause.operation)
    if (named is not None and named.capability is not None
            and named.capability.effect is OperationEffect.COMPUTED_VALUE
            and shared.has_numbers):
        from core.conductor.operations import governed_computation_clause

        # Interrogative grammar must not erase the user's explicit computation
        # instruction when the proposed effect agrees with it. No new authority
        # is granted: the operation still passes compatibility and expansion.
        if governed_computation_clause(clause.request, shared):
            semantic_clause = replace(semantic_clause, kind=ClauseKind.COMPUTE)
    # F41 — the plan's KNOW-family server. A plain knowledge question inside a mixed turn
    # carries no explanation-shape cue, so EVERY family's cue-gated capability refused it and
    # the clause died unresolved beside siblings that answered (measured served, two texts:
    # "In what year did the Berlin Wall fall?" named market_quote; "who wrote the novel 1984?"
    # named factual_explanation and refused by the shape gate itself). Demoting the whole turn
    # was measured worse (F11): the ordinary lane's whole-turn grounding gate traded the partial
    # answer for a total refusal. So the re-route stays per-clause: the typed KNOW verdict
    # admits the knowledge family HERE, on the named path only, with the freshness guard
    # unchanged -- a current/latest-bound question keeps dying (turn 19's refusal is correct).
    # The UNCLAIMED fallback below keeps its narrow admission; nothing widens there.
    knowledge_spec = None
    knowledge_admits = False
    if semantic_clause.kind is ClauseKind.KNOW:
        from core.conductor.operations import (
            knowledge_clause_admits_named,
            named_knowledge_operation,
        )

        knowledge_spec = named_knowledge_operation()
        if knowledge_spec is not None:
            knowledge_admits = knowledge_clause_admits_named(semantic_clause)
    argument_sets: list[dict[str, Any]] = []
    if named is not None:
        # A proven typed frame of THIS family inside THIS clause outranks the generic clause-kind
        # check: the proof read the exact text ("1000 TRY to USD" is a formal fx_quote frame), the
        # kind classifier read the verb ("Convert" -> TRANSFORM), and letting the weaker reading
        # refuse the stronger one produced a refused clause node BESIDE a projected node for the
        # same requirement -- one request, two verdicts, both printed. Measured at 5b901925.
        if named.name in proven_families or (named is knowledge_spec and knowledge_admits):
            decision = _ALLOWED
        else:
            decision = decide_operation_compatibility(
                named.capability,
                semantic_clause,
                dependency_effects=dependency_effects,
            )
        if decision.allowed:
            argument_sets, error = _try_expand(
                named,
                clause.request,
                shared,
                explicitly_named=True,
                dependency_values=dependency_values,
            )
            if argument_sets:
                return named, argument_sets, ""
            first_error = error or f"{clause.operation} found nothing to act on in this request"
        else:
            first_error = f"{clause.operation} is unavailable for this request: {decision.reason}"
    else:
        first_error = f"no registered operation named {clause.operation!r}"
    if (
        knowledge_admits
        and named is not None
        and named is not knowledge_spec
        and not argument_sets
        and not clause.depends_on
        and _names_its_own_subject(clause.request)
        and not _asks_for_a_purchasable_amount(clause.request)
        and not _derived_family_recognizes(clause.request, shared)
    ):
        # The re-typing completion, the same shape as the market_quote->quantitative_reasoning
        # one above: a clause whose NAMED, REGISTERED family could not serve it gets ONE more
        # attempt through the family the runtime's own typing says can serve it. Five guards
        # keep the pins this file already holds: an unregistered or empty naming stays
        # unresolved (removal fails closed); a clause with declared dependencies is never
        # answered standalone; a clause pointing at a sibling's work ("Explain the calculation
        # briefly.") is not rescued as generic prose; a clause a DERIVED family's own
        # recognizer claims ("which city is warmer") is dependency-shaped work, not knowledge;
        # and a purchasable-amount ask ("tell me how much gold I can buy") needs its live
        # price, so knowledge answering it is fabrication by another door.
        retyped_sets, _retype_error = _try_expand(
            knowledge_spec,
            clause.request,
            shared,
            explicitly_named=True,
            dependency_values=dependency_values,
        )
        if retyped_sets:
            return knowledge_spec, retyped_sets, ""

    for spec in general_operations():
        if named is not None and spec.name == named.name:
            continue
        if spec.is_derived and not clause.depends_on:
            continue
        decision = (
            _ALLOWED
            if spec.name in proven_families
            else decide_operation_compatibility(
                spec.capability,
                semantic_clause,
                dependency_effects=dependency_effects,
            )
        )
        if not decision.allowed:
            continue
        argument_sets, _error = _try_expand(
            spec, clause.request, shared, dependency_values=dependency_values
        )
        if argument_sets:
            return spec, argument_sets, ""
    return None, [], first_error


def _alignment_key(text: str) -> str:
    """Comparison form for model clauses and Turn IR clauses; never used as request content."""

    clean = " ".join(str(text or "").casefold().split()).strip()
    clean = re.sub(r"^(?:and|also|then|plus)\s+", "", clean)
    return clean.rstrip(".!?;:")


def _proven_families_for_clause(ledger: RequirementLedger, clause_text: str) -> frozenset[str]:
    """The families the turn's own semantic proof established INSIDE this clause.

    `capture_requirements` runs before any clause is read, so this is the turn's proof consulted
    against a model's clause, never the other way around. A requirement whose surface sits inside
    the clause text is proof that this clause carries that family's request -- the difference
    between "Convert 1000 TRY to USD." (a formal fx_quote frame the kind classifier misreads as
    TRANSFORM) and "Convert this document to Spanish" (no fx frame, correctly refused).
    """

    key = _alignment_key(clause_text)
    if not key:
        return frozenset()
    return frozenset(
        requirement.family
        for requirement in ledger.requirements
        if _alignment_key(requirement.surface) and _alignment_key(requirement.surface) in key
    )


def _semantic_clauses(
    clauses: Sequence[ProposedClause], original_request: str
) -> dict[int, TurnClause]:
    """Align model structure to the runtime-owned Turn IR.

    Explicit A/B/list boundaries come from the untouched original request. A model clause that
    does not align exactly is parsed through Turn IR on its own text as a conservative fallback;
    conductor never introduces a competing clause-kind classifier.
    """

    turn = parse_turn_ir(original_request)
    available = list(turn.clauses)
    used: set[str] = set()
    aligned: dict[int, TurnClause] = {}
    for proposed in clauses:
        key = _alignment_key(proposed.request)
        match = next(
            (
                candidate
                for candidate in available
                if candidate.clause_id not in used
                and _alignment_key(candidate.request_text) == key
            ),
            None,
        )
        if match is None:
            fallback = parse_turn_ir(proposed.request)
            if fallback.clauses:
                match = fallback.clauses[0]
        if match is not None:
            used.add(match.clause_id)
            aligned[proposed.index] = match
    return aligned


def _registered_effect_can_serve(
    clause: TurnClause,
    shared: SharedTurnContext,
) -> bool:
    """Whether a real typed effect capability should keep ownership of this action.

    The unavailable-action fallback must never shadow a printer, emergency-call, smart-home, or
    other integration once one is registered with a compatible SIDE_EFFECT contract and a domain
    resolver that accepts this exact clause.  Registration is not authorization; this function
    merely declines the no-capability fallback so the existing action/admission path remains in
    charge of permission and execution.
    """

    for spec in known_operations():
        capability = spec.capability
        if capability is None or capability.effect not in {
            OperationEffect.SIDE_EFFECT,
            OperationEffect.LIVE_OBSERVATION,
        }:
            continue
        decision = decide_operation_compatibility(capability, clause)
        if not decision.allowed:
            continue
        arguments, _error = _try_expand(
            spec,
            clause.request_text,
            shared,
            explicitly_named=True,
        )
        if arguments:
            return True
    return False


def _deterministic_knowledge_operation(
    clause: TurnClause,
    shared: SharedTurnContext,
) -> str:
    """A compatible knowledge operation that needs neither a model nor a tool."""

    for spec in known_operations():
        capability = spec.capability
        if (
            spec.needs_generation
            or spec.tool_intent
            or capability is None
            or capability.effect is not OperationEffect.KNOWLEDGE_ANSWER
            or not decide_operation_compatibility(capability, clause).allowed
        ):
            continue
        argument_sets, _error = _try_expand(
            spec,
            clause.request_text,
            shared,
            explicitly_named=True,
        )
        if argument_sets:
            return spec.name
    return ""


_AVIATION_EMERGENCY_CONTEXT_RE = re.compile(
    r"^\s*(?:my|our|the)\s+(?:flight|aircraft|airplane|plane)\b"
    r"[^.!?;\n]{0,100}\b(?:crash(?:ing|ed)?|emergency|impact)\b",
    re.IGNORECASE,
)


def _deterministic_reviewed_knowledge_plan(original: str) -> tuple[ProposedClause, ...]:
    """Plan a closed multi-fact reviewed answer without invoking a planner or answering model."""

    turn = parse_turn_ir(original)
    if len(turn.clauses) < 2:
        return ()
    shared = extract_shared_context(original)
    proposals: list[ProposedClause] = []
    saw_aviation_context = False
    for index, clause in enumerate(turn.clauses):
        if re.match(
            r"^\s*(?:do\s+not|don't|without)\s+"
            r"(?:search|browse|look\s+up|check|use|call|trigger|fetch)\b",
            clause.request_text,
            re.IGNORECASE,
        ):
            continue
        if clause.kind is ClauseKind.UNKNOWN and _AVIATION_EMERGENCY_CONTEXT_RE.search(
            clause.request_text
        ):
            saw_aviation_context = True
            continue
        if clause.kind is not ClauseKind.KNOW:
            return ()
        operation = _deterministic_knowledge_operation(clause, shared)
        if operation != "reviewed_safe_knowledge":
            return ()
        proposals.append(
            ProposedClause(
                index=index,
                request=clause.request_text,
                operation=operation,
                depends_on=(),
                origin="certain_recognizer",
            )
        )
    if len(proposals) < 2 or not saw_aviation_context:
        return ()
    return tuple(proposals)



def _purchase_partition(original: str) -> tuple[str, tuple[str, ...]]:
    """`(purchase_span, foreign_units)`: the execution units that belong to the purchase, joined as
    one span, and the units that belong to nothing -- a request no registered lane claims and the
    purchase does not cover ("lpg price" beside "how much silver ... if i sell 1 bnb"). A foreign unit
    that a registered lane DOES claim (weather, a file) still empties the span: that turn is a mixed
    plan for the model planner, whose nodes can serve it. Measured 2026-09-08 on the operator's live
    turn: the arm abstained on the whole message because of the unknown asset, the free model planned
    the derivation, and its incomplete reply became "internal fault"."""
    result = _purchase_span_text(original, _collect_foreign=True)
    return result if isinstance(result, tuple) else (str(result), ())


#: The `ProposedClause.origin` of a clause the RUNTIME minted from its own recognizers. The value
#: `build_plan_from_clauses` reads to decide that a decomposition is runtime-owned: no bounded
#: semantic proposer is consulted for it (a model call whose frames would only be discarded) and
#: no requirement is projected over it (its skips are reviewed decisions, not omissions).
#:
#: Measured on the owner's turn (2026-09-10, build 772256a7): the purchase arm minted the correct
#: five clauses but left `origin` at its default "model", so the plan builder bought a 6.8 s
#: proposer call and PROJECTED two extra `quantitative_reasoning` nodes over the same asks --
#: "how much of gold", with no roles and no payment leg -- one of which took the single
#: generation slot for 66 s and starved both role-bound derivations to the plan deadline.
_RUNTIME_OWNED = "certain_recognizer"


def _purchase_span_text(original: str, *, _collect_foreign: bool = False):
    """The execution units that BELONG to the purchase, as one span -- or "" when any unit does not.

    THE LAW THIS ENFORCES: the deterministic minter returns a COMPLETE clause list for the whole
    turn, so whatever it claims, it owes. Measured at HEAD: "how much gold can I buy with 10 bnb
    and what is the weather in Rome" minted two market quotes and one calculation clause carrying
    the WHOLE MESSAGE -- Rome included -- with no weather node anywhere in the plan. The Rome ask
    was absorbed into the calculation's text, where nothing could ever serve it, while the span
    made the plan look complete. `conductor_lane_proposal` then reported nothing unclaimed.

    A unit belongs to the purchase when it carries the purchase shape itself, or names one of the
    assets the purchase is about. A unit that does neither is a different request, and this arm
    has no node for it -- so the turn is left for the model planner, which can mint a MIXED plan.

    This is not a new law. `agent.py` and `turn_frontdoor.py` already require per-unit coverage
    before their lanes claim a turn; this applies it to the one claimant that bypassed it.
    """
    text = str(original or "").strip()
    if not text:
        return ""
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.agent_runtime.demand_ownership import (
            a_registered_lane_claims,
            execution_unit_spans,
        )
        from core.agent_runtime.fast_live_info_price import price_assets_named
        from core.conductor.operations import _PURCHASE_VERB_RE
    except Exception:
        return text
    units = tuple(execution_unit_spans(text))
    named = {a.casefold() for a in (price_assets_named(text) or ())}
    # The purchase's OWN roles name assets too. Measured live 2026-09-07: "what is the price of
    # oil now and how much of oil i can buy if i have 1 btc or 1 eth?" -- "oil" is not in the fast
    # alias table `price_assets_named` reads, so the price-of-oil unit "belonged" to nothing and
    # the whole turn went unclaimed; the roles resolver had resolved oil as the target all along.
    try:
        from core.conductor.operations import distributed_purchase_roles, resolve_purchase_roles

        for candidate in (text, *(str(getattr(u, "text", "") or "") for u in units)):
            roles = resolve_purchase_roles(candidate) if _PURCHASE_VERB_RE.search(candidate) else None
            if roles is not None:
                for role in (roles.target, roles.payment):
                    if role is not None and role.text:
                        named.add(str(role.text).casefold())
                # The fan-out this arm will actually plan. When the single-target resolution
                # reports an ambiguity, its `target` is None -- so with only the two roles above,
                # an ELLIPTICAL sibling ask ("... if I sell 1 eth? also how much of gold") named
                # no asset and its unit read as foreign to the purchase it belongs to (owner turn,
                # 2026-09-10: the second conversion left the plan as an unresolved lookup). The
                # distribution's own role sets are the authority for what this purchase serves.
                for item in distributed_purchase_roles(candidate):
                    for role in (item.target, item.payment):
                        if role is not None and role.text:
                            named.add(str(role.text).casefold())
    except Exception:
        pass

    # An ALLOCATION names every target and its payer itself; the split cue and the "how much of
    # each" ask are its own parts, not foreign units (FINDINGS F15: the question fragment was
    # adjudicated for entity ambiguity because nothing said it belonged to the purchase).
    allocation_ask = False
    try:
        from core.conductor.operations import _QUANTITY_ASK_CUES, _clause_has, allocation_purchase_roles

        allocation = allocation_purchase_roles(text)
        if allocation is not None:
            allocation_ask = True
            named.add(str(allocation.payment_text).casefold())
            for target in allocation.targets:
                for word in str(target.text).split():
                    if len(word) >= 2:
                        named.add(word.casefold())
    except Exception:
        allocation_ask = False

    def belongs_to_the_purchase(body: str) -> bool:
        folded = body.casefold()
        if allocation_ask and _clause_has(body, _QUANTITY_ASK_CUES):
            return True
        return bool(_PURCHASE_VERB_RE.search(body)) or any(a in folded for a in named)

    # THE MINT GRAIN, DELIBERATELY -- read here, not merged into the execution grain.
    #
    # The execution grain FUSES a HEADLESS sibling ask: "...in RUB? Also weather in Rome"
    # is ONE execution unit, because after the leader "also" the next token is a SUBJECT
    # and not a demand head. So the per-unit loop below sees a single unit that belongs,
    # and claims a turn carrying an ask no node here can serve.
    #
    # Widening the execution split to separate it was measured and REJECTED: any predicate
    # admitting a headless live-data or currency subject as a boundary shreds conjoined
    # asks -- "btc and eth price" becomes "btc" + "and eth price" -- and through
    # `lane_may_claim_whole_turn` that strips the finalize right from the very lane serving
    # those turns today (7 of 11 probed shapes). The fusion rule is load-bearing.
    #
    # So the DISAGREEMENT between the two grains is read at this claimant instead. The
    # question put to each minted unit the purchase does not serve is the registry's, not a
    # word list's: does a registered lane claim this? "Also weather in Rome" is claimed by
    # live_info_fast_path and live_data_typed_plan; "I have 1eth", "i want to sell it",
    # "using it all?" and "thanks" are claimed by nobody. That is the whole discriminator.
    def _out(span: str, foreign: tuple[str, ...]):
        return (span, foreign) if _collect_foreign else span

    for minted in demand_units(text):
        body = str(getattr(minted, "text", "") or "")
        if belongs_to_the_purchase(body):
            continue
        if a_registered_lane_claims(body):
            # A minted demand a registered lane will claim, that this purchase does not
            # serve. Claiming the turn would swallow it.
            return _out("", ())
    if len(units) < 2:
        return _out(text, ())
    kept: list[str] = []
    foreign: list[str] = []
    kept_spans: list[tuple[int, int]] = []
    for unit in units:
        body = str(getattr(unit, "text", "") or "")
        if not belongs_to_the_purchase(body):
            # A unit this arm has no node for and no registered lane claims. With
            # `_collect_foreign` it is carried as an explicitly unresolved clause (the plan
            # stays complete and the reader is told the fact); otherwise the arm stands down.
            if _collect_foreign:
                foreign.append(body.strip())
                continue
            return ""
        kept.append(body.strip())
        try:
            kept_spans.append((int(unit.start), int(unit.end)))  # type: ignore[attr-defined]
        except Exception:
            kept_spans = []
    joined = " ".join(kept).strip() or text
    if kept_spans and len(kept_spans) == len(kept):
        # THE SPAN IS A SUBSTRING OF WHAT THE USER WROTE, not a reconstruction from unit texts.
        # The mint grain strips leading connectors, so joining unit texts deleted the additive
        # "also" of an elliptical sibling ask ("... if I sell 1 eth? also how much of gold") and
        # the coordination the roles machinery reads vanished from the scoped span (measured on
        # the owner's turn class, 2026-09-10: the fan-out saw "... holding? how much silver ..."
        # and declined as an exclusive choice). When the kept units are contiguous, slice the
        # original between the first kept start and the last kept end so every boundary marker
        # between them is preserved verbatim; a gap (a dropped foreign unit inside) keeps the
        # text join, which never re-admits a unit this arm refused.
        lo, hi = min(s for s, _e in kept_spans), max(e for _s, e in kept_spans)
        non_kept_inside = any(
            lo <= int(getattr(unit, "start", -1)) and int(getattr(unit, "end", -1)) <= hi
            for unit in units
            if (int(getattr(unit, "start", -1)), int(getattr(unit, "end", -1))) not in kept_spans
        )
        if not non_kept_inside:
            joined = str(text[lo:hi]).strip() or joined
    return _out(joined, tuple(foreign))

def _deterministic_purchasable_amount_plan(
    original: str,
) -> tuple[ProposedClause, ...]:
    """Plan the purchasable-amount shape model-free — solo OR mixed into a
    multi-request message.

    Measured live 2026-08-30 (solo): "how much of gold can I get with 10 bnb"
    served two bare quotes and no figure — the conductor declined the single
    request at the several-requests boundary. Measured live the same day
    (mixed): "price of btc? also if i have 1 btc how much bnb i can buy" — the
    model-driven clause planner died with the local model (qwen low-memory) and
    the conductor declined, so the typed lane quoted both assets and DISCLOSED
    the derivation as unclaimed. The figure is derivable from live prices
    either way, so the plan is minted here deterministically: quote the target
    (from "how much <target>", first fallback: first non-payment alias), quote
    the payment asset, quote any OTHER asset the message names (a mixed message
    serves every ask), compute on a declared dependency. () otherwise.
    """
    from core.conductor.operations import _PURCHASE_VERB_RE, allocation_purchase_roles

    text = str(original or "").strip()
    if not text or not _PURCHASE_VERB_RE.search(text):
        return ()
    # Everything below plans against the purchase's OWN span. A turn carrying a unit this arm
    # has no node for is not claimed at all -- see `_purchase_span_text`.
    scoped, foreign_units = _purchase_partition(text)
    if not scoped:
        return ()
    allocation = allocation_purchase_roles(scoped)
    if allocation is not None:
        # ONE sum, several parts, several targets: the allocation reading owns the whole span
        # (measured 2026-09-10 23:48 on c647b707, FINDINGS F15: read as four fragments, the
        # allocation was never represented and three of four outputs were lost).
        purchase = _allocation_plan(scoped, allocation, extract_shared_context(original))
    else:
        purchase = _purchase_plan_for_span(scoped, extract_shared_context(original))
    if purchase:
        scope = _clause_span(original, scoped)
        if scope[0] >= 0:
            purchase = tuple(replace(item, source_scope=scope) for item in purchase)
    if not purchase or not foreign_units:
        return purchase
    extra: list[ProposedClause] = []
    for offset, unit_text in enumerate(foreign_units):
        asks_price = bool(re.search(r"\b(?:price|prices|quote|cost|rate)\b", unit_text, re.IGNORECASE))
        subject = re.sub(r"\b(?:the|a|an|and|also|plus|current|latest|today'?s?|price|prices|quote|cost|rate)\b", " ", unit_text, flags=re.IGNORECASE)
        subject = " ".join(subject.split()).strip(" ?.,") or unit_text.strip(" ?.,")
        reason = (
            f"no price source for {subject}: it is not an asset this runtime can quote"
            if asks_price
            else f"nothing in this runtime can look up {unit_text.strip(' ?.,')}"
        )
        extra.append(
            ProposedClause(
                len(purchase) + offset,
                unit_text,
                "market_quote" if asks_price else "lookup",
                (),
                origin=_RUNTIME_OWNED,
                unresolved_reason=reason,
            )
        )
    return (*purchase, *extra)


def _allocation_plan(text: str, allocation: Any, shared: SharedTurnContext | None = None) -> tuple[ProposedClause, ...]:
    """The runtime-owned clauses of an allocation: one quote per priced asset, one fx bridge when
    a currency sum must be read in the prices' currency, and ONE output clause per target.

    Every target the user named gets exactly one clause, so every obligation is conserved:

    * a priced asset (crypto / commodity) -> a derivation "how much <asset> can I buy with <share>
      <payer>" over its quote (and the fx bridge for a currency payer, or the payer's own quote
      for an asset payer) -- the same normal form and binding the single purchase uses;
    * a currency target -> an fx conversion of the share ("convert 250 EUR to CNY");
    * a target nothing resolves -> an UNRESOLVED clause stating why: a ticker earns the one
      identifying question (`unresolved_ticker_question`), anything else "no price source";
    * a payer nothing resolves -> every derivation carries the identifying question for the payer,
      while the targets' quotes are still served (the rest of the task is retained);
    * a parts/targets mismatch -> quotes served, every derivation refuses with the mismatch.

    No price is ever invented: figures come from the quote and fx nodes the derivations depend on.
    """
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from core.conductor.operations import unresolved_ticker_question

    clauses: list[ProposedClause] = []

    def add(request: str, operation: str, deps: Sequence[int] = (), reason: str = "") -> int:
        clauses.append(
            ProposedClause(
                len(clauses), request, operation, tuple(int(d) for d in deps),
                origin=_RUNTIME_OWNED, unresolved_reason=reason,
            )
        )
        return len(clauses) - 1

    payment = allocation.payment
    share = allocation.share
    share_text = f"{share:.15g}" if share is not None else ""
    unit_text = f"{allocation.quantity_unit} " if allocation.quantity_unit else ""
    payer_word = payment.text if payment is not None else allocation.payment_text
    pay_code = payment.key if payment is not None and payment.kind == "currency" else ""
    payer_question = (
        unresolved_ticker_question(allocation.payment_text)
        if payment is None and allocation.payment_ticker_shaped
        else (
            f"'{allocation.payment_text}' is not an asset or currency this runtime can price, "
            "so no share of it can be converted"
            if payment is None
            else ""
        )
    )

    # Quotes: one per canonical asset, the payer's own when it is a priced asset.
    quote_words: list[str] = []
    if payment is not None and payment.kind != "currency":
        quote_words.append(payment.text)
    priced_targets = [t for t in allocation.targets if t.role is not None and t.role.kind in ("crypto", "commodity")]
    quote_words.extend(t.role.text for t in priced_targets)
    price_index: dict[str, int] = {}
    for alias in _unbound_price_aliases(quote_words, text, shared):
        resolved = _resolve_price_alias(alias)
        key = str(resolved[0]) if resolved is not None else f"alias:{alias}"
        price_index[key] = add(f"price of {alias}", "market_quote")

    # The fx bridge: a currency sum is divided by prices quoted in USD, so the share is stated in
    # USD by a declared fx leg (the binding reads the leg's converted amount; nothing crosses
    # currencies silently). Minted once, shared by every derivation.
    fx_bridge: int | None = None
    if payment is not None and payment.kind == "crypto" and allocation.valuation_currency:
        deps = [price_index[payment.key]] if payment.key in price_index else []
        if allocation.valuation_currency != "USD":
            deps.append(add(f"how much is 1 USD in {allocation.valuation_currency}", "fx_quote"))
        add(f"how much is {allocation.quantity:g} {payment.text} in {allocation.valuation_currency}", "quantitative_reasoning", deps)
        if share is not None and not allocation.problem:
            add(f"how much is {share:.15g} {payment.text} in {allocation.valuation_currency}", "quantitative_reasoning", deps)
    if pay_code and pay_code != "USD" and share_text and priced_targets and not allocation.problem:
        # Phrased as the KNOW question the fx capability serves ("how much is 250 EUR in USD");
        # the imperative "convert ..." parses as a TRANSFORM clause the capability gate refuses.
        fx_bridge = add(f"how much is {share_text} {pay_code} in USD", "fx_quote")

    for target in allocation.targets:
        normal_form = " ".join(
            f"how much {target.text} can I buy with {share_text} {unit_text}{payer_word}".split()
        )
        if allocation.problem:
            add(normal_form, "quantitative_reasoning", (), reason=allocation.problem)
            continue
        role = target.role
        if role is not None and role.kind in ("crypto", "commodity"):
            deps: list[int] = [price_index[role.key]] if role.key in price_index else []
            if payment is None:
                add(normal_form, "quantitative_reasoning", deps, reason=payer_question)
                continue
            if payment.kind == "currency":
                if fx_bridge is not None:
                    deps.append(fx_bridge)
            else:
                if payment.key in price_index:
                    deps.append(price_index[payment.key])
            add(normal_form, "quantitative_reasoning", deps)
        elif role is not None and role.kind == "currency":
            if payment is None:
                add(f"how much is {share_text} {payer_word} in {role.key}", "fx_quote", (), reason=payer_question)
            elif payment.kind == "currency":
                add(f"how much is {share_text} {pay_code} in {role.key}", "fx_quote")
            else:
                add(
                    normal_form,
                    "quantitative_reasoning",
                    (),
                    reason=(
                        f"turning a share of {payment.entity} into {role.key} needs a sale price "
                        "in that currency, which this runtime does not derive"
                    ),
                )
        else:
            reason = (
                unresolved_ticker_question(target.text.split()[-1])
                if target.ticker_shaped
                else (
                    f"no price source for {target.text}: it is not an asset this runtime can quote, "
                    f"so its {share_text} {pay_code or payer_word} share cannot be converted"
                ).replace("its  ", "its ")
            )
            add(normal_form, "quantitative_reasoning", (), reason=reason)
    return tuple(clauses)


def _one_alias_per_asset(ordered: Sequence[str]) -> tuple[str, ...]:
    """The aliases to quote, one per CANONICAL asset, first spelling kept.

    The arm gathers alias texts from three sources (the resolved roles, the message's own price
    asks, the fast alias table), and "oil", "crude" and "brent" are three spellings of one asset.
    Deduplicating by spelling minted `market_quote:brent_crude` beside `market_quote:brent_crude_0`
    and served the same quote twice (measured on the c5b8483a rig, controls G1/G7, and identical at
    772256a7). An alias the tables cannot resolve is kept as written, so an unpriceable name still
    reaches its truthful "no price source" row.
    """
    from core.agent_runtime.live_data_plan import _resolve_price_alias

    kept: list[str] = []
    seen_keys: set[str] = set()
    for alias in ordered:
        spelling = str(alias or "").strip().casefold()
        if not spelling:
            continue
        resolved = _resolve_price_alias(spelling)
        key = str(resolved[0]) if resolved is not None else f"alias:{spelling}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        kept.append(spelling)
    return tuple(kept)


def _unbound_price_aliases(aliases, text: str, shared: SharedTurnContext | None):
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from core.conductor.supplied_prices import supplied_prices

    bound = supplied_prices(shared if shared is not None else extract_shared_context(text), text)
    return tuple(alias for alias in _one_alias_per_asset(aliases)
                 if (resolved := _resolve_price_alias(alias)) is None or resolved[0] not in bound)


def _purchase_plan_for_span(text: str, shared: SharedTurnContext | None = None) -> tuple[ProposedClause, ...]:
    """The purchase clauses for an already-scoped span (see `_purchase_partition`)."""
    from core.agent_runtime.fast_live_info_price import price_assets_named
    from core.conductor.operations import (
        _QUANTITY_ASK_CUES,
        _clause_has,
        alternative_purchase_roles,
        distributed_purchase_roles,
        resolve_purchase_roles,
    )
    aliases = price_assets_named(text)
    if not aliases:
        return ()
    alias_folds = {a.casefold() for a in aliases}

    # ROLES, NOT ORDER. Which asset is bought and which pays is decided once, by grammar and the
    # alias tables, in `resolve_purchase_roles`; the computation reads the same decision by
    # canonical key. The list below is the set of quotes this arm owes -- its order carries no
    # meaning, and the derivation binds nothing by position.
    roles = resolve_purchase_roles(text)
    if roles is None:
        return ()
    payment = roles.payment
    target = roles.target
    payment_is_currency = payment is not None and payment.kind == "currency"
    if payment is None:
        # The payment leg names nothing this runtime can price: not this shape.
        return ()
    if not payment_is_currency and payment.text not in alias_folds:
        # A GLUED amount ("1eth") is invisible to the presence-based alias extractor; the
        # resolved payment asset is quoted all the same.
        aliases = [*aliases, payment.text]
        alias_folds.add(payment.text)
    alternatives = alternative_purchase_roles(text) if not roles.problem else ()
    if len(alternatives) >= 2:
        # "or" lists several PAYERS for one target ("... if i have 1 btc or 1 eth"): one derivation
        # per payer over the same target. Measured live 2026-09-07: the second payer was quoted
        # and never derived. Each derivation's clause text is the grammar's normal form for its
        # payer, so the computation resolves exactly the roles minted here.
        ordered = [target.text] if target is not None else []
        ordered.extend(
            item.payment.text
            for item in alternatives
            if item.payment is not None and item.payment.kind not in ("currency", "unpriced")
        )
        ordered.extend(a.casefold() for a in aliases if a.casefold() not in set(ordered))
        price_clauses = tuple(
            ProposedClause(index, f"price of {alias}", "market_quote", (), origin=_RUNTIME_OWNED)
            for index, alias in enumerate(_unbound_price_aliases(ordered, text, shared))
        )
        derivations = tuple(
            ProposedClause(
                len(price_clauses) + offset,
                " ".join(
                    f"how much {item.target.text} can I buy with {item.quantity:g} "
                    f"{item.quantity_unit} {item.payment.text}".split()
                ),
                "quantitative_reasoning",
                tuple(range(len(price_clauses))),
                origin=_RUNTIME_OWNED,
            )
            for offset, item in enumerate(alternatives)
        )
        return (*price_clauses, *derivations)
    distributed = distributed_purchase_roles(text) if roles.problem else ()
    if len(distributed) >= 2:
        # "and" distributes ONE predicate over several targets ("how much gold and how much
        # silver can I buy with one bitcoin"): one derivation per target, each over the same
        # payment leg, every quote the turn owes served once. Each derivation's clause text is the
        # grammar's normal form for its target, so the computation resolves exactly the
        # single-target roles minted here -- no position, no first alias.
        targets = [item.target for item in distributed if item.target is not None]
        ordered = [role.text for role in targets]
        if not payment_is_currency:
            ordered.append(payment.text)
        ordered.extend(a.casefold() for a in aliases if a.casefold() not in set(ordered))
        price_clauses = tuple(
            ProposedClause(index, f"price of {alias}", "market_quote", (), origin=_RUNTIME_OWNED)
            for index, alias in enumerate(_unbound_price_aliases(ordered, text, shared))
        )
        quantity_text = f"{roles.quantity:g}" if roles.quantity is not None else ""
        # The stated unit rides the normal form ("with 1 kg silver"), so the computation resolves
        # the same dimensioned leg the grammar read -- never a bare count of a metal.
        derivations = tuple(
            ProposedClause(
                len(price_clauses) + offset,
                " ".join(
                    f"how much {role.text} can I buy with {quantity_text} "
                    f"{roles.quantity_unit} {payment.text}".split()
                ),
                "quantitative_reasoning",
                tuple(range(len(price_clauses))),
                origin=_RUNTIME_OWNED,
            )
            for offset, role in enumerate(targets)
        )
        return (*price_clauses, *derivations)
    if roles.problem or target is None:
        # A purchase ask whose TARGET is missing or ambiguous. Its quotes are served; the
        # derivation is minted so that it REFUSES with the reason, rather than being handed to a
        # model planner that would have to guess the target. Only a figure ask earns that refusal
        # -- a text that merely mentions a purchase and a payment is not claimed here.
        if not _clause_has(text, _QUANTITY_ASK_CUES):
            return ()
        ordered = [a.casefold() for a in aliases]
    elif payment_is_currency:
        # A currency has no market price to look up, and quoting one as an asset would be a
        # second defect. Only the TARGET is quoted; the payment amount rides the request, and
        # the binding divides it once its currency matches the price's.
        ordered = [target.text] + [a.casefold() for a in aliases if a.casefold() != target.text]
    else:
        ordered = [target.text, payment.text] + [
            a.casefold() for a in aliases if a.casefold() not in (target.text, payment.text)
        ]
    price_clauses = tuple(
        ProposedClause(index, f"price of {alias}", "market_quote", (), origin=_RUNTIME_OWNED)
        for index, alias in enumerate(_unbound_price_aliases(ordered, text, shared))
    )
    return (
        *price_clauses,
        ProposedClause(
            len(price_clauses),
            text,
            "quantitative_reasoning",
            tuple(range(len(price_clauses))),
            origin=_RUNTIME_OWNED,
        ),
    )


_FX_CHAIN_RE = re.compile(
    r"\b(\d[\d.,]*)\s+([a-z]{3})\s+to\s+([a-z]{3})\b"
    r"[^.?!]*?\b(?:and\s+then|then|after\s+that)\s+to\s+([a-z]{3})\b",
    re.IGNORECASE,
)


def _deterministic_fx_chain_plan(original: str) -> tuple[ProposedClause, ...]:
    """Plan "N AAA to BBB and then to CCC" deterministically, as a dependency chain.

    Measured live (operator turn 4, 2026-08-30): the currency fast path parsed
    only the first leg and honestly disclosed the second as unclaimed — the chain
    was never served. Fiat endpoints chain through fx_quote legs where leg 2
    consumes leg 1's converted amount; a crypto/commodity endpoint chains through
    BBB->USD + the asset's own market quote + a quantitative node over both —
    the same dependency shape the solo purchasable-amount fix proved live.
    Returns () for anything that is not exactly this shape.
    """
    from decimal import Decimal, InvalidOperation

    from core.agent_runtime.live_data_plan import _resolve_price_alias

    text = " ".join(str(original or "").split())
    match = _FX_CHAIN_RE.search(text)
    if match is None:
        return ()
    amount, base_code, mid_code, end_code = match.groups()
    try:
        Decimal(amount.replace(",", ""))
    except InvalidOperation:
        return ()
    base_u, mid_u = base_code.upper(), mid_code.upper()
    end_resolved = _resolve_price_alias(end_code.lower())
    if end_resolved is None:
        # Fiat->fiat->fiat: two legs, the second consumes the first's output.
        # Leg 2's text is a rate-lookup phrase ("gbp to usd rate") — the fx
        # converter parses no amountless "convert X to Y" (measured); the
        # chained-amount wiring in _fx_run supplies the amount at run time.
        return (
            ProposedClause(
                0, f"convert {amount} {base_u} to {mid_u}", "fx_quote", (), origin=_RUNTIME_OWNED
            ),
            ProposedClause(
                1,
                f"{mid_u} to {end_code.upper()} rate",
                "fx_quote",
                (0,),
                origin=_RUNTIME_OWNED,
            ),
        )
    end_key, _kind, end_name = end_resolved
    # Fiat->fiat->asset: bridge to USD, quote the asset, derive the amount.
    return (
        ProposedClause(
            0, f"convert {amount} {base_u} to {mid_u}", "fx_quote", (), origin=_RUNTIME_OWNED
        ),
        ProposedClause(1, f"{mid_u} to USD rate", "fx_quote", (0,), origin=_RUNTIME_OWNED),
        ProposedClause(2, f"price of {end_key}", "market_quote", (), origin=_RUNTIME_OWNED),
        ProposedClause(3, text, "quantitative_reasoning", (1, 2), origin=_RUNTIME_OWNED),
    )


def _deterministic_unavailable_action_plan(
    original: str,
) -> tuple[ProposedClause, ...]:
    """Plan the narrow mixed shape that must not depend on a planner model.

    Turn IR owns clause boundaries and kinds. This adapter only claims a message when at least one
    clause is a typed unavailable effect and every sibling is independently servable as exact
    arithmetic, stable explanation, or in-chat content generation. Anything else returns empty
    and leaves the ordinary planner/runtime untouched.
    """

    from core.action_availability import unavailable_action_report

    turn = parse_turn_ir(original)
    if len(turn.clauses) < 2:
        return ()
    shared = extract_shared_context(original)
    proposals: list[ProposedClause] = []
    unavailable_count = 0
    safe_count = 0
    for index, clause in enumerate(turn.clauses):
        operation = ""
        if re.match(
            r"^\s*(?:do\s+not|don't|without)\s+"
            r"(?:search|browse|look\s+up|check|use|call|trigger|fetch)\b",
            clause.request_text,
            re.IGNORECASE,
        ):
            # An answer/tool constraint, not another question. It still governs the whole turn
            # through the existing retrieval/action policy; it must not become a fake KNOW node.
            continue
        if re.match(
            r"^\s*(?:give|provide)\s+(?:me\s+)?(?:the\s+)?"
            r"(?:explanation|answer|response)\s*[.!?]*$",
            clause.request_text,
            re.IGNORECASE,
        ):
            # A delivery modifier for an explanation already represented by its own sibling, not
            # a second independent knowledge request.
            continue
        if re.match(
            r"^\s*(?:write|give|provide|return|create)\s+(?:me\s+)?(?:the\s+)?"
            r"(?:script|code|program|snippet|recipe|poem|story)\s*[.!?]*$",
            clause.request_text,
            re.IGNORECASE,
        ) and any(proposal.operation == "safe_content_generation" for proposal in proposals):
            # A closing "just ... write the script" restates the already represented artifact;
            # treating it as a second generation node produces two unrelated drafts and lets the
            # unconstrained duplicate evade the first node's exact-shape contract.
            continue
        if re.match(
            r"^\s*(?:just\s+)?(?:do|show|give|provide)\s+(?:me\s+)?(?:the\s+)?"
            r"(?:math|calculation|arithmetic|working)\s*[.!?]*$",
            clause.request_text,
            re.IGNORECASE,
        ) and any(proposal.operation == "calculation" for proposal in proposals):
            # A trailing request to show/do the math restates the already represented arithmetic
            # sibling. It is not a free-standing knowledge question.
            continue
        if unavailable_action_report(clause.request_text) is not None:
            if _registered_effect_can_serve(clause, shared):
                # A real capability exists. Do not pre-empt its authorization/execution path with
                # a synthetic "unavailable" answer, and do not mix two competing action planners.
                return ()
            operation = "unavailable_action"
            unavailable_count += 1
        elif clause.kind is ClauseKind.COMPUTE:
            operation = "calculation"
            safe_count += 1
        elif clause.kind is ClauseKind.KNOW:
            operation = "factual_explanation"
            operation = _deterministic_knowledge_operation(clause, shared) or operation
            safe_count += 1
        elif clause.kind is ClauseKind.CREATE:
            operation = "safe_content_generation"
            safe_count += 1
        else:
            return ()
        proposals.append(
            ProposedClause(
                index=index,
                request=clause.request_text,
                operation=operation,
                depends_on=(),
                origin="certain_recognizer",
            )
        )
    if unavailable_count < 1 or safe_count < 1:
        return ()
    return tuple(proposals)


def _same_purchase_target(left: str, right: str) -> bool:
    """Whether two clauses ask about the same purchasable asset -- a plan that already carries the
    derivation for it has split the quote and the amount on purpose, and the quote stays a quote."""
    from core.conductor.operations import resolve_purchase_roles

    try:
        a = resolve_purchase_roles(str(left or ""))
        b = resolve_purchase_roles(str(right or ""))
    except Exception:
        return False
    return bool(a and b and a.target and b.target and a.target.key == b.target.key)


def _asks_for_a_purchasable_amount(request_text: str) -> bool:
    from core.conductor.operations import asks_for_a_purchasable_amount

    return asks_for_a_purchasable_amount(request_text)


def _demand_only(request_text: str) -> str:
    """`request_text` with any CONSTRAINT clause of its own dropped.

    A planner may hand back one clause spanning a constraint AND the demand after it -- measured on
    build a9618aae, acceptance turn 7, where the proposed clause was "Do NOT search the web for
    this. From memory: what is the boiling point of water at sea level in Celsius?" and the whole
    span was then treated as one request. Two harms followed: the reader was shown an instruction
    the runtime had OBEYED inside "Could not be answered", and the answerable half was judged by a
    text carrying the prohibition's "this", which reads as a reference to a sibling's work.

    Turn IR already owns clause boundaries and already classifies a prohibition as
    `ClauseKind.CONSTRAINT` (see `core.turn_ir`), so this only re-uses that verdict: keep the
    clauses that ask for something, drop the ones that only constrain. A span that is ALL
    constraint, or that Turn IR cannot split, is returned unchanged -- this trims, it never
    invents, and `_verify_no_invented_content` still sees a subset of the user's own words.
    """

    text = str(request_text or "").strip()
    if not text:
        return text
    try:
        parsed = parse_turn_ir(text).clauses
    except Exception:
        return text
    if len(parsed) < 2:
        return text
    kept = [clause for clause in parsed if clause.kind is not ClauseKind.CONSTRAINT]
    if not kept or len(kept) == len(parsed):
        return text
    return " ".join(str(clause.request_text or "").strip() for clause in kept).strip() or text


def build_plan_from_clauses(
    clauses: Sequence[ProposedClause],
    *,
    original_request: str,
    plan_id: str,
    shared_context: SharedTurnContext | None = None,
    propose_semantics: Callable[[str, str], str] | None = None,
) -> ConductorPlan:
    """Expand validated clauses into typed nodes and a validated DAG.

    A clause becomes one node per entity its operation recognizes. The clause->nodes mapping is
    kept so a derived clause depending on clause 1 depends on *every* node clause 1 produced --
    a comparison over two cities must wait for both, not for whichever the model listed.

    `shared_context` is extracted once from the whole message and handed to every operation that
    declared it wants it. Operations that did not declare it are called exactly as before.
    """
    # CAPTURE FIRST. Before any clause is read, before any resolver runs, before any node exists.
    # The ledger's cardinality is a function of what the turn PROVED about itself, so nothing below
    # can shrink it -- which is the property the two previous candidates could not express.
    #
    # `propose_semantics` is the bounded semantic proposer. Absent, only the closed formal grammars
    # run: a smaller ledger, never a wrong one, and never a provider call for a role nobody proved.
    #
    # OWNERSHIP is decided here rather than after capture, because it decides whether asking is
    # worth anything. A runtime-owned decomposition realizes nothing -- this layer has no opinion
    # about a turn a deterministic recognizer already owns -- so proposing over it buys a model call
    # whose result is discarded. Measured: it spent one on every deterministic unavailable-action
    # turn, which are exactly the turns whose contract is that no model is consulted at all.
    # A constraint is not part of the request it precedes. Trimmed BEFORE anything reads the
    # clause, so every later pass -- resolution, the obligation floor, the reader's disclosure --
    # sees the demand alone rather than the demand welded to an instruction already obeyed.
    clauses = tuple(
        clause if clause.request == _demand_only(clause.request)
        else replace(clause, request=_demand_only(clause.request))
        for clause in clauses
    )
    runtime_owned_decomposition = bool(clauses) and all(
        clause.origin != "model" for clause in clauses
    )
    ledger = resolve_ledger(
        capture_requirements(
            original_request,
            propose=None if runtime_owned_decomposition else propose_semantics,
        )
    )
    shared = shared_context if shared_context is not None else extract_shared_context(original_request)
    semantic_clauses = _semantic_clauses(clauses, original_request)
    nodes: list[ConductorNode] = []
    unresolved: list[ConductorNode] = []
    clause_nodes: dict[int, tuple[str, ...]] = {}
    clause_effects: dict[int, tuple[OperationEffect, ...]] = {}
    clause_values: dict[int, tuple[str, ...]] = {}
    obligations: list[Obligation] = []
    realized_by: dict[str, str] = {}
    plan_time_states: dict[str, ObligationState] = {}
    node_clause_deps: dict[str, tuple[int, ...]] = {}
    served_nodes: dict[str, dict[str, str]] = {}
    node_entities: dict[str, str] = {}
    served_by_operation: dict[str, set[str]] = {}
    clause_subjects: dict[str, set[str]] = {}
    operation_clauses: dict[str, list[int]] = {}
    used_ids: set[str] = set()

    for clause in clauses:
        semantic_clause = semantic_clauses.get(clause.index)
        if semantic_clause is None:
            node = _unresolved_node(
                plan_id,
                clause,
                "request could not be aligned to the runtime turn representation",
                (),
            )
            nodes.append(node)
            unresolved.append(node)
            clause_nodes[clause.index] = (node.node_id,)
            clause_effects[clause.index] = ()
            continue
        dependency_ids: tuple[str, ...] = tuple(
            node_id
            for dep_index in clause.depends_on
            for node_id in clause_nodes.get(dep_index, ())
        )
        dependency_effects: tuple[OperationEffect, ...] = tuple(
            effect
            for dep_index in clause.depends_on
            for effect in clause_effects.get(dep_index, ())
        )
        # What this clause's dependencies WILL hand it. Known here, at plan time, because the
        # producing operations declare their exports -- so a clause whose every figure arrives from
        # a live sibling can be admitted on that basis instead of being refused for stating no
        # numbers of its own.
        dependency_values: tuple[str, ...] = tuple(
            label
            for dep_index in clause.depends_on
            for label in clause_values.get(dep_index, ())
        )

        if (
            clause.operation != "quantitative_reasoning"
            and _asks_for_a_purchasable_amount(clause.request)
            and not any(
                other.operation == "quantitative_reasoning"
                and (clause.index in other.depends_on or _same_purchase_target(other.request, clause.request))
                for other in clauses
            )
        ):
            # A model that reads "how much gold does 240 usd buy me" as a QUOTE has named the price,
            # not the amount asked for. The clause is a derivation: the completion passes below mint
            # the price producer and wire the money edge, exactly as for a clause the model labelled
            # correctly. Measured 2026-09-07: the quote alone shipped and the amount was never computed.
            clause = replace(clause, operation="quantitative_reasoning")
        if str(getattr(clause, "unresolved_reason", "") or "").strip():
            node = _unresolved_node(plan_id, clause, str(clause.unresolved_reason).strip(), dependency_ids)
            nodes.append(node)
            unresolved.append(node)
            node_clause_deps[node.node_id] = tuple(clause.depends_on)
            clause_nodes[clause.index] = (node.node_id,)
            clause_effects[clause.index] = ()
            continue
        named = operation_spec(clause.operation)
        proven = _proven_families_for_clause(ledger, clause.request)
        if named is not None and named.is_derived and not dependency_ids:
            # A comparison with nothing to compare, or a conclusion with nothing to conclude from,
            # can only be answered by invention. Fail it closed rather than run it -- but only after
            # the general operations have had the clause, since one of them may serve it outright.
            spec, argument_sets, reason = _resolve_clause(
                ProposedClause(
                    index=clause.index, request=clause.request, operation="", depends_on=()
                ),
                shared,
                semantic_clause,
                dependency_effects,
                dependency_values,
                proven,
            )
            if spec is None:
                reason = f"{clause.operation} needs earlier results and none were named"
        else:
            spec, argument_sets, reason = _resolve_clause(
                clause,
                shared,
                semantic_clause,
                dependency_effects,
                dependency_values,
                proven,
            )

        if spec is None or not argument_sets:
            node = _unresolved_node(plan_id, clause, reason, dependency_ids)
            nodes.append(node)
            unresolved.append(node)
            node_clause_deps[node.node_id] = tuple(clause.depends_on)
            clause_nodes[clause.index] = (node.node_id,)
            clause_effects[clause.index] = ()
            continue

        produced: list[str] = []
        entities: list[str] = []
        for position, arguments in enumerate(argument_sets):
            entity = str(arguments.get("entity") or f"{spec.name}_{position}")
            node_id = f"{plan_id}:{spec.name}:{_slug(entity)}"
            if node_id in used_ids:
                node_id = f"{node_id}_{position}"
            used_ids.add(node_id)
            nodes.append(
                ConductorNode(
                    node_id=node_id,
                    operation=spec.name,
                    request_text=clause.request,
                    arguments={**dict(arguments), **(
                        {"price_source_scope": clause.source_scope}
                        if clause.origin != "model" and clause.source_scope is not None else {}
                    )},
                    depends_on=dependency_ids,
                    required_result_fields=spec.required_result_fields,
                    tool_intent=spec.tool_intent,
                    needs_generation=node_needs_generation(spec, arguments),
                    can_run_in_parallel=spec.can_run_in_parallel,
                    clause_span=_clause_span(original_request, clause.request),
                )
            )
            produced.append(node_id)
            entities.append(entity)
            node_clause_deps[node_id] = tuple(clause.depends_on)
            node_entities[node_id] = entity

        # What this clause actually served, recorded per OPERATION rather than per clause. The
        # canonical obligation pass below needs to know what the whole PLAN serves: two clauses may
        # split one domain between them, and a per-clause view reported the half served by the
        # sibling clause as omitted.
        served_by_operation.setdefault(spec.name, set()).update(
            _normalize(entity) for entity in entities
        )
        for entity, produced_id in zip(entities, produced, strict=False):
            served_nodes.setdefault(spec.name, {}).setdefault(_normalize(entity), produced_id)
        clause_subjects.setdefault(spec.name, set()).update(
            _normalize(subject) for subject in named_subjects(spec, clause.request)
        )
        operation_clauses.setdefault(spec.name, []).append(clause.index)

        clause_nodes[clause.index] = tuple(produced)
        clause_effects[clause.index] = (
            (spec.capability.effect,) if spec.capability is not None else ()
        )
        clause_values[clause.index] = tuple(
            value_label(entity, field_name)
            for entity in entities
            for field_name in spec.exported_value_fields
        )

    # A SUPPORTED SIBLING MUST NOT TAKE AN INDEPENDENTLY ANSWERABLE CLAUSE DOWN WITH IT.
    #
    # `factual_explanation` is the registry's general knowledge server ("Last. Everything above
    # answers something specific; this answers what is left") and it carries TWO resolvers: the
    # broad `expand_named_arguments`, and the narrow `expand_arguments` that the unclaimed-clause
    # chain uses. The narrow one is correct WHERE IT IS USED, because that same chain also decides
    # whether the conductor CLAIMS a message at all. Widening it was built on 2026-09-09 and
    # reverted: it broke five guards, including both seven-node-failure sabotage pins and
    # "a message with no figures is left to the lanes that already answer it" (FINDINGS F19).
    #
    # So the two decisions are separated at the CALL SITE rather than inside the resolver. This
    # pass runs only after every clause has been resolved, and only in a plan that ALREADY serves
    # something else -- so it can never turn a declined message into a claimed one: a plan with
    # nothing else served still has every node unresolved and is still declined below. The guards
    # exercise `expand_clause` directly and are untouched by construction.
    #
    # Measured on build ef2b7384, acceptance turn 7: "Do NOT search the web for this. From memory:
    # what is the boiling point of water at sea level in Celsius? Also, what is 10 percent of 250?"
    # served "10% of 250 = 25." beside "could not be answered" for a question that needs no
    # retrieval at all, with closure demand_indeterminate=2.
    if any(node.operation != UNRESOLVED_OPERATION for node in nodes):
        for position, stranded in enumerate(list(nodes)):
            if stranded.operation != UNRESOLVED_OPERATION:
                continue
            prefix = f"{plan_id}:unresolved:"
            if not stranded.node_id.startswith(prefix) or stranded.depends_on:
                # Obligation-floor nodes and dependent clauses are not this pass's business.
                continue
            if _NAMED_OPERATION_MISSING_RE.match(str(stranded.unresolved_reason or "")):
                # The REGISTRY lost the operation that owns this clause. That must stay visible:
                # removal fails closed, and papering over it with the general knowledge server
                # answers "What is 137 x 29?" as prose instead of arithmetic -- the catch-all
                # swallowing a clause an adapter does properly, which `_resolve_clause` names as
                # the reason general operations are tried LAST. Caught by
                # tests/test_conductor_plan_and_graph.py::
                # test_removing_an_operation_makes_its_clause_unresolved_rather_than_absent.
                continue
            try:
                clause_index = int(stranded.node_id[len(prefix):])
            except ValueError:
                continue
            request_text = str(stranded.request_text or "")
            if not _names_its_own_subject(request_text):
                continue
            try:
                own_clauses = parse_turn_ir(request_text).clauses
            except Exception:
                continue
            if len(own_clauses) != 1 or own_clauses[0].kind is not ClauseKind.KNOW:
                continue
            semantic_own = own_clauses[0]
            for spec in general_operations():
                if spec.expand_named_arguments is None or spec.is_derived:
                    continue
                if spec.capability is not None:
                    decision = decide_operation_compatibility(
                        spec.capability, semantic_own, dependency_effects=()
                    )
                    if not decision.allowed:
                        continue
                argument_sets, _rescue_error = _try_expand(
                    spec, request_text, shared, explicitly_named=True
                )
                if not argument_sets:
                    continue
                arguments = dict(argument_sets[0])
                entity = str(arguments.get("entity") or f"{spec.name}_{clause_index}")
                node_id = f"{plan_id}:{spec.name}:{_slug(entity)}"
                if node_id in used_ids:
                    node_id = f"{node_id}_{clause_index}"
                used_ids.add(node_id)
                rescued_node = ConductorNode(
                    node_id=node_id,
                    operation=spec.name,
                    request_text=request_text,
                    arguments=arguments,
                    required_result_fields=spec.required_result_fields,
                    tool_intent=spec.tool_intent,
                    needs_generation=node_needs_generation(spec, arguments),
                    can_run_in_parallel=spec.can_run_in_parallel,
                    clause_span=_clause_span(original_request, request_text),
                )
                nodes[position] = rescued_node
                unresolved[:] = [item for item in unresolved if item.node_id != stranded.node_id]
                node_clause_deps.pop(stranded.node_id, None)
                node_clause_deps[node_id] = ()
                node_entities.pop(stranded.node_id, None)
                node_entities[node_id] = entity
                served_by_operation.setdefault(spec.name, set()).add(_normalize(entity))
                served_nodes.setdefault(spec.name, {}).setdefault(_normalize(entity), node_id)
                clause_subjects.setdefault(spec.name, set()).update(
                    _normalize(subject) for subject in named_subjects(spec, request_text)
                )
                operation_clauses.setdefault(spec.name, []).append(clause_index)
                clause_nodes[clause_index] = (node_id,)
                clause_effects[clause_index] = (
                    (spec.capability.effect,) if spec.capability is not None else ()
                )
                clause_values[clause_index] = tuple(
                    value_label(entity, field_name)
                    for field_name in spec.exported_value_fields
                )
                break

    # THE OBLIGATION FLOOR, and it REALIZES rather than reports.
    #
    # One pass over the whole plan, reading the USER'S REQUEST -- not any clause. The clause is
    # where the previous version looked, and it is why the floor saw nothing when a planner dropped
    # "and Nairobi" from its own clause text: the only text it consulted no longer named the place,
    # so the obligation was never created and the plan agreed with itself completely.
    #
    # Whatever the request names and the plan does not serve is handed back to the operation that
    # owns it:
    #
    #   realizable  -> a REAL execution node, run exactly as a planned one is
    #   not         -> CAPABILITY_UNAVAILABLE, fixed at plan time, nothing attempted
    #
    # "The planner did not create a node" is not a state a serviceable request may come to rest in.
    # Reporting such a request as unserved tells a reader their question failed when nothing ever
    # tried it.
    for operation_name, clause_indices in sorted(operation_clauses.items()):
        spec = operation_spec(operation_name)
        if spec is None or spec.subjects_named is None:
            continue
        served = served_by_operation.get(operation_name, set())
        for obligation in canonical_subject_obligations(
            original_request,
            operation_name,
            named_subjects(spec, original_request),
            plan_id=plan_id,
        ):
            key = _normalize(obligation.subject)
            if key in served:
                # Already served by the plan. It is still an OBLIGATION -- the user asked for it --
                # and recording only the omissions is what left a planned-but-failed subject with no
                # obligation at all, so nothing could say what became of it. Origin says the planner
                # got this one right; the state still comes from what its node actually did.
                node_id = served_nodes.get(operation_name, {}).get(key, "")
                if node_id:
                    obligations.append(obligation)
                    realized_by[obligation.obligation_id] = node_id
                continue
            served.add(key)
            obligation = replace(
                obligation,
                origin=(
                    # The clause named it and the recognizer still produced no node, versus the
                    # clause never naming it at all. Different defects, different owners, and the
                    # answer says which.
                    ObligationOrigin.RECOGNIZER_OMITTED
                    if key in clause_subjects.get(operation_name, set())
                    else ObligationOrigin.PLANNER_OMITTED
                ),
            )
            arguments = realize_subject(spec, obligation.subject)
            if arguments is not None:
                entity = str(arguments.get("entity") or obligation.subject)
                node_id = f"{plan_id}:{spec.name}:{_slug(entity)}"
                if node_id in used_ids:
                    continue
                used_ids.add(node_id)
                nodes.append(
                    ConductorNode(
                        node_id=node_id,
                        operation=spec.name,
                        request_text=obligation.text,
                        arguments=dict(arguments),
                        required_result_fields=spec.required_result_fields,
                        tool_intent=spec.tool_intent,
                        needs_generation=node_needs_generation(spec, arguments),
                        can_run_in_parallel=spec.can_run_in_parallel,
                        obligation_id=obligation.obligation_id,
                    )
                )
                realized_by[obligation.obligation_id] = node_id
                node_entities[node_id] = entity
            else:
                node_id = f"{plan_id}:unavailable:{_slug(obligation.subject)}"
                if node_id in used_ids:
                    continue
                used_ids.add(node_id)
                node = ConductorNode(
                    node_id=node_id,
                    operation=UNRESOLVED_OPERATION,
                    request_text=obligation.text,
                    unresolved_reason=f"nothing in this runtime can look up {obligation.text}",
                    can_run_in_parallel=False,
                    obligation_id=obligation.obligation_id,
                )
                nodes.append(node)
                unresolved.append(node)
                realized_by[obligation.obligation_id] = node_id
                plan_time_states[obligation.obligation_id] = (
                    ObligationState.CAPABILITY_UNAVAILABLE
                )
                node_entities[node_id] = obligation.subject
            obligations.append(obligation)
            # The realized node joins its operation's clause sets, so a dependent waits for it
            # exactly as it waits for a subject the planner did remember. This is the opposite of
            # the residue rule it replaces, and the difference is real: a note about a dropped
            # subject must not cancel siblings, but a REQUIRED input that never arrives must stop
            # the node needing it from answering as though it had. Left out of the dependency set,
            # the dependent runs, finds one operand missing, and the model writes
            # `bitcoin_price / 1` -- a fabricated answer wearing a success label.
            for index in clause_indices:
                clause_nodes[index] = (*clause_nodes.get(index, ()), node_id)
                if arguments is not None:
                    clause_values[index] = (*clause_values.get(index, ()), *(
                        value_label(str(arguments.get("entity") or obligation.subject), name)
                        for name in spec.exported_value_fields
                    ))

    # REQUIREMENT-FIRST REALIZATION. The order below is the F4 contract, and it is an order:
    #
    #     requirements -> realizations -> WorkSpec -> capability lookup -> BIND -> PROJECT -> state
    #
    # Realizations exist before any argument does, so a requirement whose projector returns nothing,
    # whose family nobody registered, or which has no ordinary subject slots at all still has
    # somewhere for its outcome to be recorded. Deriving them from argument sets is what left the
    # derived "ratio" frame with no realization while the node computing it ran and succeeded.
    #
    # OWNERSHIP decides who services a requirement. A planner omission decides nothing. This used to
    # gate on "does the plan already contain a node of this family", which made a model's failure to
    # mention a family into a terminal state for every requirement in it: "Get the price of Gold and
    # the weather in Oslo", planned as weather only, silently dropped Gold even though Gold was
    # captured, resolved and serviceable. What legitimately owns a turn is a RUNTIME decomposition,
    # whose skips are its own reviewed decisions; a model-proposed plan owns nothing, it proposes.
    # `runtime_owned_decomposition` was computed at the top of this function, before capture.
    realization_ledger = (
        RealizationLedger() if runtime_owned_decomposition else resolve_capabilities(
            plan_realizations(ledger)
        )
    )

    # BIND first, against the nodes that already exist, by the OPERATION's own execution key. A
    # realization bound here needs no projection, and one left unbound is genuinely unserved rather
    # than merely spelled differently. Surface-overlap matching and `_SINGLE_NODE_FAMILIES` are
    # gone: they made "calculation" and "137 x 29" different work, and two genuinely different
    # conversions the same work.
    bound: dict[str, tuple[str, ...]] = dict(bind_existing_nodes(realization_ledger, nodes))
    non_execution: dict[str, NonExecutionEvidence] = {}

    for realization in realization_ledger.realizations:
        if realization.realization_id in bound:
            continue
        lookup = realization.lookup
        if lookup is None or lookup.state is not CapabilityLookupState.AVAILABLE:
            # The ONLY source of a non-execution state. `CAPABILITY_UNAVAILABLE` arrives here
            # because a typed lookup returned it, never because a projection came back empty, a
            # binding was missed or an exception was swallowed.
            non_execution[realization.realization_id] = lookup.non_execution  # type: ignore[assignment]
            continue
        # An explicit UNRESOLVED node for this requirement is a REFUSAL the registry made after
        # being offered the clause. Binding to it keeps the accounting; projecting past it would
        # override a decision with a second attempt at the same work.
        declined = next(
            (
                node.node_id
                for node in nodes
                if node.operation == UNRESOLVED_OPERATION
                and node.obligation_id == realization.requirement_id
            ),
            "",
        )
        if declined:
            bound[realization.realization_id] = (declined,)
            continue
        if realization.family == "fx_quote":
            # ISO discipline at the REALIZATION seam (the recorded 2026-08-30 lead):
            # the formal frame grammar can read a purchasable-amount ask as a
            # conversion ("10 BNB to gold") and this path mints nodes directly from
            # the frame's own arguments — the expander-level rejection never runs.
            # A non-fiat pair (digits/spaces in the code, or a code that resolves as
            # a MARKET asset: btc/eth/bnb are three letters too) is a misread frame:
            # record it non-executable instead of minting a node that can only die.
            from core.agent_runtime.live_data_plan import _resolve_price_alias
            from core.conductor.fresh_data_operations import _is_iso_currency_code

            fx_base = str((realization.arguments or {}).get("base") or "")
            fx_quote = str((realization.arguments or {}).get("quote") or "")
            if (
                not _is_iso_currency_code(fx_base)
                or not _is_iso_currency_code(fx_quote)
                or _resolve_price_alias(fx_base.strip().lower()) is not None
                or _resolve_price_alias(fx_quote.strip().lower()) is not None
            ):
                # A misread frame must not override work that really served its
                # requirement: the fx reading of "10 bnb to gold" and the
                # quantitative node both answer the derivation requirement, and
                # non-execution evidence on a SERVED requirement makes the
                # renderer suppress the served figure (measured live). Bind to
                # the serving node; a misread frame with no requirement match
                # binds to a real quote node (its operands ARE real work) so no
                # refusal row can displace the turn's actual answers.
                served = next(
                    (
                        node.node_id
                        for node in nodes
                        if node.obligation_id == realization.requirement_id
                    ),
                    "",
                ) or next(
                    (node.node_id for node in nodes if node.operation == "market_quote"),
                    "",
                )
                if served:
                    bound[realization.realization_id] = (served,)
                    continue
                non_execution[realization.realization_id] = NonExecutionEvidence(
                    NonExecutionReason.CAPABILITY_UNAVAILABLE,
                    "fx frame read a non-fiat pair; conversions serve three-letter "
                    "fiat codes only (crypto/commodity pairs belong to market quotes)",
                )
                continue
        node_id = f"{plan_id}:{realization.family}:{_slug(realization.display_subject)}"
        if node_id in used_ids:
            bound[realization.realization_id] = (node_id,)
            continue
        spec = operation_spec(realization.family)
        if spec is None:
            # Unreachable while the lookup is AVAILABLE, which is exactly why it is stated: if it
            # ever becomes reachable it is a defect in the lookup, not a capability answer.
            non_execution[realization.realization_id] = NonExecutionEvidence(
                NonExecutionReason.INTEGRITY_DEFECT,
                f"{realization.family} passed capability lookup with no registered operation",
            )
            continue
        # ADMISSION. A family the proof established still has to be SERVABLE by the operation that
        # would run it. This projection took `spec` for metadata only -- required fields, tool
        # intent, generation flag -- and minted the node regardless of what that operation thinks
        # of the clause, so a family proved over a request its own operation refuses became a node
        # that could not work. Measured 2026-09-09 on acceptance turn 18: "In what year did the
        # Berlin Wall fall?" was projected as `quantitative_reasoning`, whose gate declines it (no
        # quantity cue, no purchase or fx shape, and no numbers of its own); the node then bought a
        # generation asking for an ARITHMETIC PLAN for a history question and reported "the model
        # returned no arithmetic plan for this clause". A correct model would not have produced one
        # either -- the request was never arithmetic. So ask the operation the same question the
        # named and general clause paths ask it (`_try_expand`), and record a non-execution rather
        # than mint work that cannot run.
        #
        # Scoped to the case that can actually waste a model call on a request the operation
        # refuses: an operation that BUYS A GENERATION, for a realization whose adapter produced no
        # typed arguments. A realization that carries arguments was already resolved by its own
        # adapter (an fx pair, a market symbol), and re-gating that on the raw surface would
        # decline work that is correctly bound; a deterministic operation costs no call and keeps
        # its existing behaviour.
        if spec.needs_generation and not realization.arguments:
            admitted, _admission_error = _try_expand(
                spec, realization.source_surface, shared, dependency_values=()
            )
            if not admitted:
                non_execution[realization.realization_id] = NonExecutionEvidence(
                    NonExecutionReason.CAPABILITY_UNAVAILABLE,
                    f"{realization.family} does not serve this request",
                )
                continue
        used_ids.add(node_id)
        nodes.append(
            ConductorNode(
                node_id=node_id,
                operation=realization.family,
                request_text=realization.source_surface,
                arguments=dict(realization.arguments),
                required_result_fields=spec.required_result_fields,
                tool_intent=spec.tool_intent,
                needs_generation=node_needs_generation(spec, realization.arguments),
                can_run_in_parallel=spec.can_run_in_parallel,
                obligation_id=realization.requirement_id,
                clause_span=realization.frame_span,
            )
        )
        node_entities[node_id] = realization.display_subject
        bound[realization.realization_id] = (node_id,)

    # Exactly one binding per realization, in the ledger's own order. `BoundExecutionPlan` refuses
    # to exist otherwise, which is where I2, I3 and I4 are actually enforced -- not in a check
    # somebody has to remember to call.
    bound_plan = BoundExecutionPlan(
        realization_ledger=realization_ledger,
        bindings=tuple(
            RealizationBinding(
                realization_id=realization.realization_id,
                bound_node_ids=bound.get(realization.realization_id, ()),
                non_execution=(
                    None
                    if bound.get(realization.realization_id)
                    else non_execution.get(
                        realization.realization_id,
                        NonExecutionEvidence(
                            NonExecutionReason.INTEGRITY_DEFECT,
                            "realization was neither bound nor given non-execution evidence",
                        ),
                    )
                ),
            )
            for realization in realization_ledger.realizations
        ),
    )

    # Requirement edges, expressed over the nodes that ended up serving each requirement, and over
    # the SLOTS a derived frame narrowed to. Both come from the bound plan, so identity is by
    # realization id rather than by any comparison of text.
    requirement_nodes: dict[str, tuple[str, ...]] = {}
    slot_nodes: dict[str, str] = {}
    # A requirement the binder could not bind BY FAMILY, but whose span an executable node covers,
    # is served by that node. This is the rule `demands_this_plan_cannot_execute` already states for
    # demand units -- "the primary authority is the GEOMETRIC one ... because wording must never
    # decide which node serves which demand" -- applied to requirements, which had only the
    # family-keyed binding.
    #
    # Measured on acceptance turn 7: the proposer labelled the boiling-point clause
    # `quantitative_reasoning`, no node of that family exists, so the requirement bound to nothing
    # and was reported as `is not something this runtime can look up` -- while a
    # `factual_explanation` node covering the very same span had been planned and dispatched. The
    # reader was told the runtime cannot do a thing it was at that moment doing, and compose then
    # suppressed the answered segment because an unserved row claimed its slot.
    _executable_spans = [
        (int(node.clause_span[0]), int(node.clause_span[1]), node.node_id)
        for node in nodes
        if node.operation != UNRESOLVED_OPERATION
        and getattr(node, "clause_span", None)
        and int(node.clause_span[0]) >= 0
        and int(node.clause_span[1]) > int(node.clause_span[0])
    ]
    _requirement_span = {
        requirement.requirement_id: (int(requirement.start), int(requirement.end))
        for requirement in ledger.requirements
    }

    def _covering_node_ids(requirement_id: str) -> tuple[str, ...]:
        """Executable nodes whose clause span lies INSIDE this requirement's span.

        Containment, not mere overlap: a node that merely touches the edge of a requirement is not
        evidence that the requirement was served, and only a node the requirement fully contains
        can be said to be doing that requirement's work.
        """

        span = _requirement_span.get(requirement_id)
        if span is None:
            return ()
        start, end = span
        return tuple(
            node_id
            for node_start, node_end, node_id in _executable_spans
            if node_start >= start and node_end <= end
        )

    for realization in realization_ledger.realizations:
        served = bound.get(realization.realization_id, ())
        if not served:
            served = _covering_node_ids(realization.requirement_id)
        if not served:
            continue
        requirement_nodes[realization.requirement_id] = (
            *requirement_nodes.get(realization.requirement_id, ()),
            *served,
        )
        for slot_id in realization.owned_slot_ids:
            slot_nodes.setdefault(slot_id, served[0])

    ledger_edges: dict[str, tuple[str, ...]] = {}
    for requirement in ledger.requirements:
        if not requirement.requires:
            continue
        # Slot bindings first: a derived frame naming two of three subjects requires those two.
        # Falling back to the whole requirement only when it named none of them is the safe reading
        # of an anaphor -- and over-requiring is as wrong as under-requiring, because it refuses
        # answers the user can have.
        narrowed = tuple(
            slot_nodes[slot_id]
            for slot_id in requirement.input_bindings
            if slot_id in slot_nodes
        )
        prerequisite_nodes = narrowed or tuple(
            node_id
            for prerequisite in requirement.requires
            for node_id in requirement_nodes.get(prerequisite, ())
        )
        for node_id in requirement_nodes.get(requirement.requirement_id, ()):
            ledger_edges[node_id] = prerequisite_nodes

    # Dependency edges are rebuilt from the FINAL obligation-aware clause sets.
    #
    # They were first computed inside the clause loop, before realization existed, so a prerequisite
    # the runtime realized afterwards was not in any dependent's `depends_on` -- the dependent ran
    # anyway, found one operand missing, and answered from the operands it did have. Measured: a
    # total asked over four parts was answered from three, labelled as the total.
    #
    # This is requirement 4 stated as code: execution nodes IMPLEMENT obligations, they are not the
    # source of truth for which prerequisites exist. A prerequisite the planner never mentioned is
    # still a prerequisite, so the edge set is derived after every obligation has been realized.
    rebuilt: list[ConductorNode] = []
    for node in nodes:
        dep_indices = node_clause_deps.get(node.node_id)
        if not dep_indices:
            rebuilt.append(node)
            continue
        edges = tuple(
            dependency_id
            for dep_index in dep_indices
            for dependency_id in clause_nodes.get(dep_index, ())
            if dependency_id != node.node_id
        )
        # Which of those this node cannot answer WITHOUT: the ones its own clause NAMES. A clause
        # that names none of its inputs -- "the difference between them" -- is anaphoric and
        # requires all of them, which is both the safe reading and what every node did before this
        # existed.
        # REQUIREDNESS COMES FROM THE REQUIREMENT LEDGER, never from matching text against node
        # entities. The regex that used to live here asked whether a clause mentioned a node's
        # entity, which makes `BTC` and `Bitcoin` two different answers to one question and makes an
        # unresolved subject -- which has no entity to match -- silently not required. The ledger
        # binds prerequisites by requirement id at capture time, before any surface is resolved, so
        # neither can happen.
        required = _ledger_required_edges(ledger_edges, node, edges)
        rebuilt.append(replace(node, depends_on=edges, required_node_ids=required))
    nodes = rebuilt

    # C2 (AUD-20260829-003): a derived-quantity clause that NAMES an asset needs that
    # asset's live price as an operand, whether or not the requirement ledger bound it.
    # Measured live: "how much gold can I buy with it" planned a quantitative node that
    # depended on the fx quote alone while the gold market node ran as an unrelated
    # sibling -- the derivation could not ground its second operand and died as an
    # "internal fault". The plan itself already names the price's producer; this wires
    # the edge the clause's own asset mention implies. Containment against the node's
    # canonical asset key/name only -- no fuzzy matching, no new vocabulary.
    _market_args = [
        (node, str(node.arguments.get("asset_key") or ""), str(node.arguments.get("entity") or ""))
        for node in nodes
        if node.operation == "market_quote"
    ]
    if any(node.operation == "quantitative_reasoning" for node in nodes):
        # Completion's second half: a quantity clause can NAME an asset without any
        # price keyword ("how much platinum could I grab for it") -- the market
        # extractors gate on a price keyword, so no price node was planned, the
        # derivation had no producer to depend on, and the honest outcome was a
        # refusal for a figure the runtime could have fetched. `price_assets_named`
        # is presence-based, which is exactly the right question HERE (the clause is
        # already a quantity ask; the asset mention is the missing producer's name).
        from core.agent_runtime.fast_live_info_price import price_assets_named
        from core.agent_runtime.live_data_plan import _resolve_price_alias
        from core.conductor.supplied_prices import price_source_scope, supplied_prices

        _have_keys = {asset_key for _n, asset_key, _e in _market_args}
        _minted: list[ConductorNode] = []
        for node in nodes:
            if node.operation != "quantitative_reasoning":
                continue
            clause_text = " ".join(
                str(node.arguments.get("clause") or node.request_text or "").casefold().split()
            )
            named_assets: list[tuple[str, str, str]] = []
            for alias in price_assets_named(clause_text):
                resolved = _resolve_price_alias(str(alias).lower())
                if resolved is not None:
                    named_assets.append(resolved)
            # The roles resolver names the TARGET by grammar ("how much gold i get" has no price
            # keyword and no alias the presence reader accepts, so it named nothing here and the
            # derivation ran with no producer -- measured 2026-09-07, sloppy-wording coverage).
            try:
                from core.conductor.operations import resolve_purchase_roles as _roles_for_completion

                _roles = _roles_for_completion(clause_text)
            except Exception:
                _roles = None
            if _roles is not None and _roles.target is not None and _roles.target.key:
                named_assets.append((_roles.target.key, _roles.target.kind or "asset", _roles.target.entity or _roles.target.key))
            supplied = supplied_prices(shared, price_source_scope(node.arguments, clause_text, shared))
            for asset_key, kind, asset_name in named_assets:
                if asset_key in supplied:
                    continue
                if asset_key in _have_keys:
                    continue
                _have_keys.add(asset_key)
                _minted.append(
                    ConductorNode(
                        node_id=f"{plan_id}:market_quote:{asset_key}",
                        operation="market_quote",
                        request_text=clause_text,
                        arguments={"entity": asset_name, "asset_key": asset_key, "kind": kind},
                        required_result_fields=("price", "currency"),
                        tool_intent="web.research",
                    )
                )
        # A LIST, as every later pass expects: the obligation floor below appends to `nodes`, and a
        # plan whose derivation named no priced asset (a calculation beside its assumptions) left a
        # tuple here -- `AttributeError: 'tuple' object has no attribute 'append'`, measured
        # 2026-09-07 as the red in test_sabotage_cutting_the_dependency_edge_empties_the_derived_facts.
        nodes = list(tuple(nodes) + tuple(_minted))
        # Recompute: the edge-completion pass below must see the freshly minted
        # market nodes, not the pre-mint snapshot.
        _market_args = [
            (node, str(node.arguments.get("asset_key") or ""), str(node.arguments.get("entity") or ""))
            for node in nodes
            if node.operation == "market_quote"
        ]
    if _market_args:
        from core.conductor.supplied_prices import price_source_scope, supplied_prices
        _completed: list[ConductorNode] = []
        for node in nodes:
            if node.operation != "quantitative_reasoning":
                _completed.append(node)
                continue
            clause_text = " ".join(
                str(node.arguments.get("clause") or node.request_text or "").casefold().split()
            )
            _extra = [
                market_node.node_id
                for market_node, asset_key, entity in _market_args
                if market_node.node_id not in node.depends_on
                and asset_key not in supplied_prices(shared, price_source_scope(node.arguments, clause_text, shared))
                and (
                    (asset_key and asset_key.replace("-", " ") in clause_text)
                    or (entity and entity.casefold() in clause_text)
                )
            ]
            # The MONEY edge. A purchase clause with no payment leg of its own ("how much gold i get",
            # "how much silver does that buy") spends the amount the turn already holds -- the fx leg
            # planned before it. The binding already reads that amount by currency from a declared
            # dependency; when the model's plan declares none, the derivation ran with nothing to
            # spend and faulted "(missing: steps, values)" (measured 2026-09-07, sloppy-wording
            # coverage). Complete the edge exactly as the price edges above are completed: the
            # nearest fx node planned before this clause, and only when no fx dependency exists.
            if not any(str(dep).split(":")[-2:-1] == ["fx_quote"] or ":fx_quote" in str(dep) for dep in node.depends_on):
                try:
                    from core.conductor.operations import resolve_purchase_roles

                    roles = resolve_purchase_roles(clause_text)
                except Exception:
                    roles = None
                if roles is not None and roles.payment is None and roles.shape == "direct":
                    position = next((i for i, n in enumerate(nodes) if n.node_id == node.node_id), len(nodes))
                    fx_before = [n.node_id for n in nodes[:position] if n.operation == "fx_quote"]
                    if fx_before:
                        _extra = list(_extra) + [fx_before[-1]]
            if _extra:
                _completed.append(
                    replace(
                        node,
                        depends_on=tuple(node.depends_on) + tuple(_extra),
                        required_node_ids=tuple(
                            dict.fromkeys(tuple(node.required_node_ids) + tuple(_extra))
                        ),
                    )
                )
            else:
                _completed.append(node)
        nodes = _completed

    # What the MODEL never carried forward at all, measured against the user's own request. The
    # dual of `_verify_no_invented_content`: that one makes an invented request impossible, this
    # one makes a dropped one visible. Enforcing only the first is how a plan that answers nothing
    # the user asked passes every check this runtime had.
    #
    # Model-proposed clauses only, and the boundary is the point. A deterministic plan is built by
    # this module from Turn IR with skips it makes deliberately and documents -- a retrieval
    # prohibition ("Do NOT search the web for tech support"), a delivery restatement, an execution
    # directive. Those are the runtime's own reviewed decisions about what is not a request, and
    # measuring residue over them makes the floor second-guess itself: it reported "Do NOT search
    # the web for tech support" as an unanswered obligation, which tells the reader an instruction
    # the runtime obeyed had failed. A model's omissions are what this rule was written for, and
    # `ProposedClause.origin` already carries which is which.
    #
    # Reported rather than merely recorded. A dropped sentence cannot be served -- nothing knows
    # which operation it wanted -- but the reader must still be told it went unanswered, and the
    # alternative to an UNRESOLVED node here is a turn the caller refuses whole, which loses the
    # siblings that did work along with the one that did not.
    model_clauses = [clause.request for clause in clauses if clause.origin == "model"]
    for obligation in clause_residue_obligations(
        original_request, model_clauses, plan_id=plan_id
    ) if model_clauses else ():
        node_id = f"{plan_id}:unplanned:{_slug(obligation.text)}"
        if node_id in used_ids:
            continue
        used_ids.add(node_id)
        node = ConductorNode(
            node_id=node_id,
            operation=UNRESOLVED_OPERATION,
            request_text=obligation.text,
            unresolved_reason="no part of the plan covered this request",
            can_run_in_parallel=False,
            obligation_id=obligation.obligation_id,
        )
        nodes.append(node)
        unresolved.append(node)
        realized_by[obligation.obligation_id] = node_id
        # A dropped sentence names no operation, so nothing can realize it. That is a genuine
        # terminal state rather than a missing one, and it is recorded as such instead of leaving
        # the obligation to be inferred from a node's free text.
        plan_time_states[obligation.obligation_id] = ObligationState.CAPABILITY_UNAVAILABLE
        obligations.append(replace(obligation, origin=ObligationOrigin.CLAUSE_OMITTED))

    return ConductorPlan(
        plan_id=plan_id,
        original_request=original_request,
        graph=build_graph(nodes),
        unresolved=tuple(unresolved),
        clause_count=len(clauses),
        shared_context=shared,
        ledger=ledger,
        bound_plan=bound_plan,
        requirement_nodes=dict(requirement_nodes),
        obligations=CanonicalObligations(
            request=original_request,
            obligations=tuple(obligations),
            realized_by=dict(realized_by),
            plan_time_states=dict(plan_time_states),
        ),
    )


#: Effects whose presence in a plan means the turn needs live/world evidence a single answering
#: model cannot supply. A plain-shaped multi-part turn whose plan carries none of these belongs to
#: the plain lane (one prompt, one completeness contract); one that carries any of them must NOT be
#: demoted there -- the plain lane would let the model invent the observation.
_LIVE_TURN_EFFECTS = frozenset(
    {
        OperationEffect.LIVE_OBSERVATION,
        OperationEffect.WORKSPACE_EVIDENCE,
        OperationEffect.SIDE_EFFECT,
        OperationEffect.ACTION_STATUS,
    }
)


def _turn_requires_live_evidence(original: str) -> bool:
    """Whether the requirements authority classifies `original` as needing live data.

    The requirements module is the stated authority ("a component may not decide that a turn
    requiring evidence can proceed without it"); the plain-task SHAPE guess may not override it.
    Lazy import and fail-toward-False: when the authority cannot be consulted, behaviour degrades
    to exactly the pre-repair decline, never to a new claim.
    """
    try:
        from core.execution_requirements import requirements_for

        return requirements_for(original).answer_mode == "LIVE_DATA"
    except Exception:
        return False


def _plan_carries_live_effects(plan: ConductorPlan) -> bool:
    """Whether any planned node's registered capability produces live/world evidence."""
    for node in plan.nodes:
        spec = operation_spec(node.operation)
        capability = getattr(spec, "capability", None) if spec is not None else None
        if capability is not None and capability.effect in _LIVE_TURN_EFFECTS:
            return True
    return False


def plan_conductor_turn(
    text: str,
    *,
    ask_model: Callable[[str, str], str],
    plan_id: str = "",
    propose_semantics: Callable[[str, str], str] | None = None,
    lane_coverage_probe: Callable[[ConductorPlan], bool] | None = None,
) -> ConductorPlan | None:
    """The plan for `text`, or None when the conductor should not claim this turn.

    Returning None is always safe ONLY when some other lane actually serves the whole message, so
    the two declines that used to be shape guesses are now conditioned on proof:

    - the plain-task demotion no longer fires on a turn the requirements authority marks
      LIVE_DATA (a run-on "weather ... convert ... how much gold" is plain-SHAPED but needs live
      evidence; demoting it dropped every clause the narrow lane could not see);
    - a plan whose operations sit inside `LANE_SERVED_OPERATIONS` is handed to the legacy lane
      only when `lane_coverage_probe` PROVES that lane covers every planned entity. No probe, a
      raising probe, or a False all keep the plan here -- fail closed, toward the lane that
      accounts for every clause.

    Every other failure -- an exception, an unparseable reply, an invented word, a single-clause
    plan, a graph that is not a DAG -- still yields None.
    """
    from core.agent_runtime.turn_planner import turn_may_hold_several_requests
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    original = str(text or "").strip()
    deterministic = _deterministic_unavailable_action_plan(original)
    if not deterministic:
        deterministic = _deterministic_reviewed_knowledge_plan(original)
    if not deterministic:
        deterministic = _deterministic_purchasable_amount_plan(original)
    if not deterministic:
        deterministic = _deterministic_fx_chain_plan(original)
    if deterministic:
        try:
            plan = build_plan_from_clauses(
                deterministic,
                original_request=original,
                plan_id=plan_id or f"conductor-{uuid.uuid4().hex[:12]}",
                shared_context=extract_shared_context(original),
                propose_semantics=propose_semantics,
            )
        except GraphRejectionError:
            return None
        if (
            bool(plan.nodes)
            and plan.requires_conductor()
            and not all(node.operation == UNRESOLVED_OPERATION for node in plan.nodes)
        ):
            return plan

    # Field-complete research is a single semantic request but still needs the conductor's typed
    # coverage contract: the ordinary model lane can collapse one missing field into a generic
    # whole-answer retry. Its deterministic parser is sufficient authority to build one node
    # without spending a planner call or loosening the ordinary single-request boundary.
    if original:
        from core.fresh_data.research import parse_structured_research_request

        if original.casefold().startswith("what is ") and parse_structured_research_request(
            original
        ) is not None:
            try:
                research_plan = build_plan_from_clauses(
                    (ProposedClause(0, original, "structured_research", ()),),
                    original_request=original,
                    plan_id=plan_id or f"conductor-{uuid.uuid4().hex[:12]}",
                    shared_context=extract_shared_context(original),
                    propose_semantics=propose_semantics,
                )
            except GraphRejectionError:
                return None
            if research_plan.nodes and all(
                node.operation != UNRESOLVED_OPERATION for node in research_plan.nodes
            ):
                return research_plan
    # Several ordinary knowledge/writing questions belong to one answering model. Decomposing them
    # here changes their meaning into operation names: the observed title clause became a workspace
    # search and the explanation clause became a fact adapter with no evidence. Decline before the
    # planner call; the plain-task lane below supplies one prompt plus a completeness contract.
    #
    # UNLESS the requirements authority says the turn needs live data. The plain-task test is a
    # SHAPE guess over head verbs; "What is weather in Rome also I have 100 USD convert to RUB and
    # tell me how much gold I can buy" matches it, and demoting that turn to the plain lane (or to
    # any narrow typed lane behind it) silently dropped every clause the lane could not express.
    # A plain-SHAPED plan that turns out to carry no live-effect node is still declined below,
    # after planning -- so pure explanation/writing turns keep today's path at the cost of one
    # planner call.
    if not original:
        return None
    plain_shaped = is_ordinary_multi_part_plain_task(original)
    if plain_shaped and not _turn_requires_live_evidence(original):
        return None
    if not turn_may_hold_several_requests(original):
        return None

    try:
        raw = ask_model(conductor_system_prompt(), original)
    except Exception:
        return None

    clauses = parse_clauses(raw)
    single_computation = False
    if len(clauses) == 1 and clauses[0].operation == "quantitative_reasoning":
        sources = parse_turn_ir(original).clauses
        shared = extract_shared_context(original)
        ledger = capture_requirements(original)
        single_computation = len(sources) >= 2
        for source in sources:
            spec, arguments, _error = _resolve_clause(
                ProposedClause(0, source.request_text, "quantitative_reasoning", ()),
                shared, source,
                proven_families=_proven_families_for_clause(ledger, source.request_text),
            )
            if (spec is None or spec.name != "quantitative_reasoning" or not arguments
                    or _alignment_key(source.request_text) not in _alignment_key(clauses[0].request)):
                single_computation = False
                break
    if len(clauses) < 2 and not single_computation:
        # One request, or nothing usable. The ordinary single-turn path is correct.
        return None
    if not _verify_no_invented_content(clauses, original):
        return None

    try:
        plan = build_plan_from_clauses(
            clauses,
            original_request=original,
            plan_id=plan_id or f"conductor-{uuid.uuid4().hex[:12]}",
            shared_context=extract_shared_context(original),
            propose_semantics=propose_semantics,
        )
    except GraphRejectionError:
        return None

    if single_computation and (
        len(plan.nodes) != 1 or plan.nodes[0].operation != "quantitative_reasoning"
    ):
        # A grouped proposal cannot absorb a different capability projected from the source.
        return None
    if len(plan.nodes) < 2 and not single_computation:
        return None
    # The plain lane keeps pure prose turns: a plain-SHAPED message whose plan carries no
    # live-effect node (no observation, no workspace evidence, no side effect, no action status)
    # answers better as one prompt than as operation names. This is what keeps "explain how
    # currency conversion works and why gold is priced in USD" out of the conductor even though
    # the requirements classifier over-marks it LIVE_DATA.
    if plain_shaped and not _plan_carries_live_effects(plan):
        return None
    if not plan.requires_conductor():
        # Every operation is inside the legacy live-data lane's vocabulary. Hand the turn over
        # ONLY on per-entity proof that the lane serves every planned node; a membership-only
        # decline dropped clauses in production (the lane's whole-text extraction found one
        # subtask where this plan holds three). No probe, or a probe that raises, keeps the plan.
        covered = False
        if lane_coverage_probe is not None:
            try:
                covered = bool(lane_coverage_probe(plan))
            except Exception:
                covered = False
        if covered:
            return None
    # A plan whose every node is unresolved answers nothing. Declining lets the ordinary path try,
    # which is strictly better than emitting a page of "could not".
    if all(node.operation == UNRESOLVED_OPERATION for node in plan.nodes):
        return None
    # NOT extended to "a plan that serves a MINORITY of the turn". That extension was built and
    # measured on 2026-09-09, and it made the reader's outcome WORSE, so it is recorded here rather
    # than kept. It routed acceptance turn 18 exactly as intended -- the turn left the conductor for
    # `ordinary_plain_text_chat` -- but that lane applies its grounding gate to the WHOLE turn, so
    # the one clause needing current information ("the exact middle name of the current Emperor of
    # Japan") refused the entire reply: "I can't publish an answer to this: it needed current
    # information." The conductor's per-clause structure had at least published "5+5 = 10." beside
    # honest unserved rows. Trading a partial answer for a total refusal is not completeness.
    # Whatever finally answers a plain knowledge clause stranded beside an unserviceable sibling
    # must keep per-clause granularity; demoting the turn to a whole-turn gate cannot. FINDINGS F11.
    return plan


__all__ = [
    "LANE_SERVED_OPERATIONS",
    "MAX_PLANNED_CLAUSES",
    "ConductorPlan",
    "ProposedClause",
    "build_plan_from_clauses",
    "conductor_system_prompt",
    "parse_clauses",
    "plan_conductor_turn",
]


# ------------------------------------------------------------------ M3 slice 3: the typed claim


#: The conductor lane's route identity — the one home for the literal the product
#: result used to scatter (M3 slice 3's displacement, matching the other lanes).
CONDUCTOR_LANE_ID = "conductor_multi_intent_plan"


def conductor_lane_proposal(plan: Any, canonical_units: Any) -> LaneProposal:
    """The conductor's typed claim against the SPINE's canonical demand set.

    The binding is GEOMETRIC, not lexical: a conductor node carries `clause_span`
    (a code-point range over the original request) and a canonical unit carries
    `start`/`end` (its own range over the same text) — a claimed unit is one whose
    span OVERLAPS a node's span. Overlap between two ranges over one text is a
    proven relation, the same doctrine `clause_span` itself documents; nothing is
    matched by wording. Units no node covers are NAMED as unclaimed.
    """
    claimed: list[str] = []
    for unit in canonical_units or ():
        for node in plan.nodes:
            start, end = getattr(node, "clause_span", (-1, -1)) or (-1, -1)
            if start < 0 or end <= start:
                continue
            if int(unit.end) > int(start) and int(unit.start) < int(end):
                claimed.append(unit.unit_id)
                break
    minted = [unit.unit_id for unit in canonical_units or ()]
    unclaimed = tuple(u for u in minted if u not in claimed)
    return LaneProposal(
        lane_id=CONDUCTOR_LANE_ID,
        obligations_claimed=tuple(dict.fromkeys(claimed)),
        unclaimed_obligations=unclaimed,
        required_capabilities=("conductor",),
        confidence=0.88,
    )


def _normalized_demand_text(value: str) -> str:
    """Casefolded, punctuation-free words — used ONLY to bind a SPANLESS node."""
    import re as _re

    return " ".join(_re.findall(r"[0-9a-z]+", str(value or "").casefold()))


def demands_this_plan_cannot_execute(plan: Any, canonical_units: Any) -> tuple[tuple[str, str], ...]:
    """The (unit_id, text) of every demand unit this plan can execute NOTHING for.

    Not "some node is unresolved" — the conductor legitimately ships a truthful
    PARTIAL, and a plan may carry unresolved nodes beside executable ones for the
    same demand. The claim-time fact that matters is stronger and rarer: a demand
    for which the plan holds no executable node at all, while holding at least one
    unresolved node that is about it.

    BINDING. The primary authority is the GEOMETRIC one `conductor_lane_proposal`
    uses — node `clause_span` against unit `start`/`end` over the same original
    text — because wording must never decide which node serves which demand.
    Measured on a real served plan, though, an UNRESOLVED node carries
    `clause_span = (-1, -1)`: the planner never bound a span for a clause it could
    not bind an operation to. Geometry alone therefore cannot see exactly the nodes
    this function exists to find, so a spanless node falls back to matching its own
    `request_text` (the planner's verbatim copy of the clause) against the unit's
    text, normalized to words. The fallback is scoped to spanless nodes and can only
    ADD a blocked demand that has no executable node by span — an executable node
    always wins, so the partial-shipping contract is untouched.

    A unit with no node of either kind is deliberately absent: that is the
    `unclaimed` case the proposal already reports, a different fact from "the plan
    tried to cover this and cannot".

    Every verdict is reached while the plan is BUILT, before a single node is
    dispatched. Pure, and defensive about shape: an object exposing no nodes yields
    (), so a caller can only under-report a gap, never invent one.
    """
    from core.conductor.registry import UNRESOLVED_OPERATION

    nodes = getattr(plan, "nodes", ()) or ()
    spanned: list[tuple[int, int, bool]] = []
    spanless: list[tuple[str, bool]] = []
    for node in nodes:
        unresolved = str(getattr(node, "operation", "") or "") == UNRESOLVED_OPERATION
        span = getattr(node, "clause_span", (-1, -1)) or (-1, -1)
        try:
            start, end = int(span[0]), int(span[1])
        except Exception:
            start, end = -1, -1
        if start >= 0 and end > start:
            spanned.append((start, end, unresolved))
        else:
            spanless.append((_normalized_demand_text(getattr(node, "request_text", "")), unresolved))

    blocked: list[tuple[str, str]] = []
    for unit in canonical_units or ():
        try:
            unit_start, unit_end = int(unit.start), int(unit.end)
        except Exception:
            continue
        unit_text = str(getattr(unit, "text", "") or "")
        overlapping = [
            unresolved
            for start, end, unresolved in spanned
            if unit_end > start and unit_start < end
        ]
        if any(not unresolved for unresolved in overlapping):
            # An executable node is bound to this demand: the plan can run something
            # for it, and that is the conductor's work to keep.
            continue
        normalized_unit = _normalized_demand_text(unit_text)
        matching = [
            unresolved
            for text, unresolved in spanless
            if text and normalized_unit and (text in normalized_unit or normalized_unit in text)
        ]
        if any(not unresolved for unresolved in matching):
            continue
        if not overlapping and not matching:
            continue
        if all(overlapping) and all(matching):
            blocked.append((str(unit.unit_id), unit_text))
    return tuple(blocked)


def demands_this_plan_served(
    plan: Any, outcomes: Any, canonical_units: Any
) -> tuple[tuple[str, str], ...]:
    """The (unit_id, node_id) of every demand unit a node of this plan actually SERVED.

    The positive twin of `demands_this_plan_cannot_execute`, and deliberately its neighbour: one
    function says which demands the plan could run nothing for, this one says which it ran
    something for, and both answer by GEOMETRY -- node `clause_span` against the unit's own
    `start`/`end` -- so wording never decides which node serves which demand.

    Why it exists (measured served, 2026-09-09, build d6be47f9). RSS's discharge channel is
    written by the lane whose work it attests: every deterministic fast-path lane in
    `turn_frontdoor` files a receipt for the slices it answered, and the finalization sweep turns
    a receipt into `satisfied`. The conductor -- the lane that exists to serve the MULTI-demand
    turns the census was built to account for -- filed none. Acceptance turn 7 answered both of
    its demands ("The boiling point ... is 100 degrees." / "10% of 250 = 25.") and certified
    `demand_minted: 2, demand_satisfied: 0, demand_indeterminate: 2`, with the ledger snapshot
    holding `consumption: []`. The reader got the answer; the accounting credited nothing.

    CONTAINMENT, never overlap, and NEVER a node that spans several demands.
    `demands_this_plan_cannot_execute` may use bare overlap because it looks for the ABSENCE of
    executable work, where an overlapping node is enough to prove work exists. A receipt is the
    opposite claim -- that this node's result ANSWERS this unit -- so it needs more, not less.

    Two shapes qualify, and only two:

        node inside unit          turn 7 (build 9e437955): node (45, 104) inside unit
                                  u1 [32, 104), the unit carrying the framing "From memory:"
                                  that the planner's clause does not. The node sits wholly
                                  inside this demand and touches no other.
        node over exactly ONE     a clause whose span holds a single demand and some non-demand
        demand                    text. There is no other demand the node could have served
                                  instead.

    A node that spans TWO OR MORE demands certifies NONE of them. Span containment establishes
    possible ownership, not fulfilment: one node can cover two asks and answer one, and neither
    its success flag nor its id distinguishes those cases. Measured directly --
    "What is the weather in Rome and what is the water temperature in the Baltic Sea?" mints two
    independent units, and a single succeeded node over the whole message with a Rome-only result
    used to certify both. That is the RED-1 NEW-4 defect arriving through a new door.

    The remaining limit, stated rather than hidden: for the exactly-one shape this still reasons
    from the clause the node ran for, not from the node's result. Tightening it further needs
    per-unit result binding, which the plan does not carry today. What it must NEVER do is
    compensate for a faulty demand split by handing out extra receipts -- a dependent instruction
    broken into fragments is a mint defect and belongs at the mint.

    Only a SUCCEEDED outcome produces a pair. A node that failed, was skipped or never ran leaves
    its unit to the sweep, which must stay free to report the gap.
    """
    succeeded = {
        str(getattr(getattr(outcome, "node", None), "node_id", "") or "")
        for outcome in (outcomes or ())
        if bool(getattr(outcome, "fulfilled", getattr(outcome, "succeeded", False)))
    }
    succeeded.discard("")
    if not succeeded:
        return ()
    spanned: list[tuple[int, int, str]] = []
    for node in getattr(plan, "nodes", ()) or ():
        node_id = str(getattr(node, "node_id", "") or "")
        if node_id not in succeeded:
            continue
        span = getattr(node, "clause_span", (-1, -1)) or (-1, -1)
        try:
            start, end = int(span[0]), int(span[1])
        except Exception:
            continue
        if start >= 0 and end > start:
            spanned.append((start, end, node_id))
    return demands_served_by_spans(spanned, canonical_units)


def demands_served_by_spans(
    served_spans: Any, canonical_units: Any
) -> tuple[tuple[str, str], ...]:
    """The ONE geometry: which demand units a set of (start, end, label) work spans served.

    Split out of `demands_this_plan_served` so the conductor and the planned-sub-turn lane bind
    the same way. A lane that executed one requested slot supplies the span it executed and its
    own label; the rule below is the whole authority, and there is no second copy of it.

    Both qualifying shapes and the refusal are documented on `demands_this_plan_served`.
    """
    unit_spans: list[tuple[int, int, str]] = []
    for unit in canonical_units or ():
        try:
            unit_start, unit_end = int(unit.start), int(unit.end)
        except Exception:
            continue
        if unit_end > unit_start:
            unit_spans.append((unit_start, unit_end, str(unit.unit_id)))

    spans: list[tuple[int, int, str]] = []
    for item in served_spans or ():
        try:
            start, end, label = int(item[0]), int(item[1]), str(item[2])
        except Exception:
            continue
        if start >= 0 and end > start and label:
            spans.append((start, end, label))

    def _demands_inside(start: int, end: int) -> list[str]:
        return [
            unit_id
            for unit_start, unit_end, unit_id in unit_spans
            if unit_start >= start and unit_end <= end
        ]

    served: list[tuple[str, str]] = []
    for unit_start, unit_end, unit_id in unit_spans:
        for start, end, label in spans:
            inside_the_unit = start >= unit_start and end <= unit_end
            covers_the_unit = start <= unit_start and end >= unit_end
            if inside_the_unit or (covers_the_unit and len(_demands_inside(start, end)) == 1):
                served.append((unit_id, label))
                break
    return tuple(served)


def declined_conductor_proposal(reason: str) -> LaneProposal:
    """The conductor's typed decline (plan refused/unparseable/already-served)."""
    return LaneProposal(
        lane_id=CONDUCTOR_LANE_ID,
        refusal_reason=str(reason or "declined"),
        required_capabilities=("conductor",),
        terminal_eligibility="none",
    )
