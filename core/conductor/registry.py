"""The open registry of node kinds. Contains no domain knowledge of its own.

This is the seam that decides whether the conductor generalizes or calcifies. Two closed
vocabularies in the tree show what calcifying looks like: `LiveDataSubtask.operation` admits
exactly `"market_quote" | "weather_lookup"`, and `core.orchestration.role_contracts.TaskRole` is a
six-value `Literal` whose lookup raises `KeyError` on anything else. Adding a domain to either
means editing the type.

Here an operation is registered, not enumerated. `core.conductor.operations` registers the slice-1
set; a plugin, a future domain, or a test can register another without this module changing. That
is the architectural reading of the blast-radius rule: the boundary is normalized so the pieces
behind it move independently.

An `OperationSpec` owns four things and the conductor owns none of them:

* `expand_arguments` -- turn this node's clause into zero or more typed argument sets. Zero means
  "I cannot serve this", which the planner turns into an UNRESOLVED node rather than a dropped
  one. More than one is a fan-out: "weather for Kaunas and Tallinn" is one clause and two nodes.
* `run` -- do the work and return a result mapping.
* `render` -- turn that result into user-facing text. Deterministic where the operation permits;
  a comparison over numbers is arithmetic and must never be delegated to a model.
* the contract fields -- what the node must produce, which permission-checkable intent it runs as,
  and whether it drives a model generation.

Entity enumeration belongs to the operation, not to the planner, and that is a reliability
decision as much as an architectural one. Asking a model to emit one node per city works when the
model is strong and fails quietly when it is not; the domain already owns recognizers that do it
deterministically. The planner's job is only to say "this clause is a weather request" -- which
entities are in it is the weather adapter's business.

`expand_arguments` is deliberately handed ONLY the node's own clause. A weather adapter never sees
"inspect the provider retry implementation", so it cannot mistake it for a place name -- the
contamination class that put `location='price of bitcoin'` into a real subtask on main is
unreachable by construction rather than by a better regex.

The one widening, and why it does not undo that: an operation may declare `wants_shared_context`,
which adds a second argument carrying the message's TYPED facts -- numbers with their units,
ambiguous currency tokens, and what the message leaves undetermined. It is never the message text,
so there is nothing in it a recognizer could turn into an entity; it is opt-in, so every adapter
above keeps exactly the input it had; and it exists because a message that states its numbers in
one sentence and asks about them in six others fails all six without it. See
`core.conductor.shared_context`.
"""
from __future__ import annotations

import ast
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotation only -- the registry gains no import-time dependency
    from core.conductor.capabilities import OperationCapability
    from core.conductor.shared_context import SharedTurnContext
    from core.runtime_task_outcome import FulfillmentStatus

#: Reserved so a registration cannot shadow the conductor's own fail-closed sentinel.
UNRESOLVED_OPERATION = "unresolved"


class NotAuthorizedError(RuntimeError):
    """Raised when something tries to execute a capability that was only ever described.

    Its own type rather than a bare `RuntimeError` so a caller cannot confuse "this operation is
    broken" with "this operation was never authorized" -- and so a test can assert the second
    specifically.
    """


@dataclass(frozen=True)
class NodeContext:
    """Everything an operation may read while running. Per-node, never shared mutable state.

    `dependency_results` is how a derived node reads what it waited for: the *structured results*
    of its dependencies, keyed by node id -- never their rendered prose. A comparison computed from
    a rendered table is a comparison computed from a model's formatting decisions.
    """

    session_id: str = ""
    source_context: Mapping[str, Any] = field(default_factory=dict)
    dependency_results: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    timeout_s: float = 8.0
    #: What the WHOLE message established -- its numbers, its ambiguous units, and what it leaves
    #: undetermined -- extracted once at plan time and never re-derived per node. Present for every
    #: node; only an operation that declares `wants_shared_context` is written to expect it, so a
    #: clause-scoped adapter stays clause-scoped whether this is populated or not.
    shared_context: SharedTurnContext | None = None
    #: Values earlier nodes computed, flattened from `dependency_results` along DECLARED edges only.
    #: Dependency-ordered rather than wall-clock-ordered, so what a node can read is a function of
    #: the graph and not of which sibling happened to finish first.
    derived_facts: Mapping[str, Any] = field(default_factory=dict)
    #: `(system_prompt, prompt) -> reply`. Injected for the same reason `run_tool_intent` is: an
    #: operation that needs a model must not reach for one, and absent means a node that needs it
    #: fails with a stated reason rather than inventing its answer.
    run_generation: Callable[[str, str], str] | None = None
    #: Injected by the caller so an operation that touches the filesystem runs through the one
    #: permission-gated, receipt-emitting seam (`core.tool_intent_executor.execute_tool_intent`)
    #: rather than reaching for a handler directly. Injected rather than imported so the operation
    #: stays testable and so the conductor never quietly acquires the agent as a dependency.
    #: Absent means an operation that needs it fails with a stated reason -- never silently.
    run_tool_intent: Callable[..., Any] | None = None
    #: `(event_type, detail) -> None`. How a node reports that it started and finished WHILE the
    #: plan is still running. Without it the runtime learns what every node did only after the
    #: whole plan returns, so a panel watching a running plan shows a roster and then nothing.
    #:
    #: Injected for the same reason as the two callables above -- the conductor must not acquire
    #: the event store as a dependency, and a test must be able to observe emissions without one.
    #:
    #: Observation must never change the work: the scheduler calls this defensively, so an emitter
    #: that raises, blocks briefly, or is absent leaves the node's outcome byte-for-byte identical.
    #: A node that failed because its observer failed would be a defect invented by the telemetry.
    emit_node_event: Callable[[str, Mapping[str, Any]], None] | None = None
    run_structured_generation: Callable[[str, str, Mapping[str, Any]], str] | None = None

    def generate_json(self, system: str, prompt: str, schema: Mapping[str, Any]) -> str:
        """Request structured output without granting its contents numeric authority."""
        if self.run_structured_generation is not None:
            return self.run_structured_generation(system, prompt, schema)
        if self.run_generation is None:
            raise ValueError("no generation seam available")
        return self.run_generation(system, prompt)


@dataclass(frozen=True)
class OperationSpec:
    """One registered node kind."""

    name: str
    #: One line shown to the planner model so it can choose this operation. Keep it behavioural.
    description: str
    #: Clause -> zero or more typed argument sets. Empty means this operation cannot serve the
    #: clause, which becomes an UNRESOLVED node. Each returned mapping should carry an
    #: ``entity`` key naming what the node is about; it is used for the node id and the answer.
    #: Called as ``(clause)``, or as ``(clause, shared_context)`` when `wants_shared_context` is
    #: set -- always through `expand_clause`, never directly.
    expand_arguments: Callable[..., list[dict[str, Any]]]
    run: Callable[[Any, NodeContext], dict[str, Any]]
    render: Callable[[Any, Mapping[str, Any]], str]
    required_result_fields: tuple[str, ...] = ()
    tool_intent: str = ""
    needs_generation: bool = False
    #: For an operation that DECLARES `needs_generation`, the arguments under which a node of it
    #: will nevertheless never buy a generation -- computed at plan time from the node's own
    #: typed arguments (a purchase derivation whose roles are bound). Read by
    #: `node_needs_generation`, which is what the planner stamps on the node; the scheduler then
    #: dispatches such a node as ordinary work instead of queueing it behind the one generation
    #: slot. None means the spec's flag stands for every node.
    model_free_arguments: Callable[[Mapping[str, Any]], bool] | None = None
    can_run_in_parallel: bool = True
    #: A derived operation consumes its dependencies' results and must declare at least one
    #: dependency. The planner rejects a derived node with no `depends_on`, because a comparison
    #: with nothing to compare is a hallucination waiting to be rendered.
    is_derived: bool = False
    #: Whether `expand_arguments` is called as `(clause, shared_context)` instead of `(clause)`.
    #:
    #: OPT-IN, and that is the whole safety argument for sharing anything at all. The clause-only
    #: rule this module's docstring states exists because a weather adapter run over the whole
    #: message produced `location='price of bitcoin'`. Widening the input for every adapter would
    #: bring that back. Widening it only where an operation declares it reasons over the message's
    #: facts leaves every existing adapter byte-for-byte as scoped as it was.
    wants_shared_context: bool = False
    #: Whether this operation may be offered a clause that no operation the planner NAMED could
    #: serve. A general operation is the runtime's last attempt before failing a clause closed --
    #: it never pre-empts a named operation, and it must still decline by returning `[]`.
    serves_unclaimed_clause: bool = False
    #: Order in which general operations are offered a clause; lower goes first. Declared rather
    #: than alphabetical because "which general operation gets first refusal" is a design decision
    #: -- a catch-all sorted ahead of a specific one takes clauses it would answer worse.
    general_priority: int = 100
    #: Semantic result contract. Registration and argument extraction are not evidence that an
    #: operation can satisfy a clause; the planner admits a model-proposed operation only through
    #: this declaration. ``None`` therefore fails closed at plan time.
    capability: OperationCapability | None = None
    #: A named operation may admit a stronger resolver than a general fallback.  This is useful for
    #: knowledge generation: an explicitly proposed, capability-compatible explanation can answer
    #: an independent KNOW clause, while the same operation must not become a catch-all for every
    #: unclaimed clause. General fallback always uses ``expand_arguments``.
    expand_named_arguments: Callable[..., list[dict[str, Any]]] | None = None
    #: Result fields this operation hands DOWNSTREAM as typed values, to nodes that declare an edge
    #: to it. Named on the dependent side as ``<entity>_<field>`` -- ``bitcoin_price``,
    #: ``riga_temperature_c`` -- so two siblings of the same operation never collide and a model
    #: reading the briefing sees the name it must write.
    #:
    #: This exists because the projection used to be a single hard-coded key. `_derived_facts`
    #: harvested ``result["values"]`` and nothing else, which only computed-value operations emit,
    #: so a SUCCEEDED live observation contributed NOTHING to the node that declared a dependency on
    #: it. Measured at 866cf12a: a market quote returned 64000.0, and the dependent node one line
    #: below it reported that the price "is not a fact this message established". Declaring the
    #: export on the producer is what makes the contract a property of the operation rather than of
    #: a shape some operations happen to return.
    exported_value_fields: tuple[str, ...] = ()
    #: Clause -> the subjects of THIS operation's domain that the clause NAMES, before any
    #: serviceability, authority or plausibility filter. Optional; ``None`` means this operation
    #: enumerates no subjects and no residue is computed for it.
    #:
    #: Deliberately NOT `expand_arguments`. That callable decides what can be SERVED, and asking it
    #: whether it served everything answers yes by construction. This one reports what the user
    #: WROTE, so the difference between the two is the obligation the runtime would otherwise drop.
    subjects_named: Callable[[str], Sequence[str]] | None = None
    #: Subject -> the typed argument set that serves it, or None when this operation cannot.
    #:
    #: The capability probe, and the half that makes the obligation floor ENFORCING rather than
    #: merely observant. `expand_arguments` answers "what work is in this clause"; it is written for
    #: clause-shaped text and returns nothing for a bare subject -- `_market_expand("Gold")` is
    #: empty while the runtime can quote gold perfectly well. Without a separate probe the only
    #: honest thing the floor could say about an obligation the planner dropped was "unserved",
    #: which is a report about a request that was never attempted.
    #:
    #: Returning None is a real answer: it means no capability exists, and the obligation comes to
    #: rest in CAPABILITY_UNAVAILABLE rather than being silently retried or silently lost.
    realize_subject: Callable[[str], dict[str, Any] | None] | None = None
    #: Typed arguments -> the identity of the WORK they describe. One producer, used for a node the
    #: planner built and for a node projected from a requirement, so the two agree by construction
    #: rather than by a text comparison between them.
    #:
    #: Defaults to every typed argument except display-only ones. Surface overlap and entity-only
    #: keys are what this replaces: they made "calculation" and "137 x 29" different work, and two
    #: genuinely different conversions the same work.
    execution_key: Callable[[Mapping[str, Any]], str] | None = None
    #: Whether `expand_arguments` is called with a third argument carrying the value labels this
    #: clause's DEPENDENCIES will export.
    #:
    #: Opt-in for the same reason `wants_shared_context` is: an operation that admits a clause on
    #: the strength of what its dependencies will supply must be able to see them at plan time.
    #: Without this a derived numeric node is admitted against the MESSAGE alone, so a request
    #: whose every figure comes from a live lookup is refused before any lookup runs -- the node is
    #: UNRESOLVED at plan time and no amount of correct propagation downstream can revive it.
    wants_dependency_values: bool = False
    #: Whether this operation's own recognizer is the runtime's deterministic claim on the clause,
    #: binding BEFORE the planner's named operation is tried. False for everything whose claim is
    #: a heuristic reading; True only where declining to bind would hand a clause the runtime has
    #: already proven to a model-chosen operation that cannot serve it -- measured at 1f8dba98,
    #: where the planner named `quantitative_reasoning` for "how much free disk space do I have?"
    #: and the named-operation-wins rule made the mis-binding stick. The expander is still the
    #: admission: this flag orders the question, it does not answer it.
    outranks_planner_naming: bool = False
    #: This operation is the plan's KNOW-family server. When the planner NAMES it for a
    #: clause -- or names another family that cannot serve one -- a typed KNOW clause is
    #: admitted on its TurnIR kind verdict (with the freshness guard) instead of the
    #: capability's explanation-SHAPE cue. The UNCLAIMED fallback admission stays narrow;
    #: nothing broadens there. Measured harm of the narrow-only rule (F41, served on
    #: c9200e0c and 24440e8d): "In what year did the Berlin Wall fall?" and
    #: "who wrote the novel 1984?" died unresolved inside mixed turns because a plain
    #: question carries no explanation-shape cue, and demoting the whole turn to the
    #: ordinary lane traded the partial answer for a total grounding refusal (F11).
    serves_named_know_clauses: bool = False
    #: Result-level fulfillment, separate from successful execution/field presence.
    #: Partial results must retain their valid output and disclose their gaps.
    assess_fulfillment: Callable[[Mapping[str, Any]], FulfillmentStatus] | None = None
    #: Exact rendered fragments representing declared dependencies. Optional and
    #: presentation-only: this cannot change execution or fulfillment verdicts.
    represented_dependency_segments: Callable[[Any, Mapping[str, Any], NodeContext], Mapping[str, str]] | None = None

    @classmethod
    def from_contract(
        cls,
        contract: Any,
        *,
        expand_arguments: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> OperationSpec:
        """Project a registered tool contract into an operation kind. Registry truth only.

        This exists so a capability that is registered -- built in, or arriving from a plugin
        manifest at runtime -- can be *described* to a planner without anyone editing a vocabulary
        list. Hand-maintained vocabulary beside a registry is how the two drift, and it is the
        specific mechanism that makes a runtime unable to generalize past the tools someone
        remembered to type out.

        **What is projected is exactly what the contract declares, and nothing else.**

        * ``description`` is the contract's own line, verbatim. No synonyms are generated, no
          trigger phrases are inferred, no keywords are extracted. A projection that invented
          phrasings would be building the lexical authority this architecture exists to retire, one
          layer down where it is harder to see.
        * ``required_result_fields`` are the ``output_schema`` keys the contract does NOT mark
          ``optional``. That marker is the repo's existing declared convention -- the same substring
          test `core.cloud_tool_call_contract._argument_schema` already reads -- so this is a
          projection of a declaration, not a guess about prose.
        * ``can_run_in_parallel`` is **False**, always. It used to follow ``read_only``, which a
          hostile review correctly called a fabrication: read-only is a claim about side effects and
          says nothing about concurrency safety. A read-only tool can share a cursor, a rate limit,
          a cache or a file handle, and running two at once is still wrong. No contract field
          declares parallel safety, so the projection does not know it and does not guess.
        * ``is_derived`` is False. A tool contract declares nothing about depending on another
          clause's answer, and inventing a dependency relation is exactly the fabrication this
          method must not do.

        **What is NOT projected: any authority — including an executable.** `OperationSpec` has no
        permission field, and this method adds none. The projected ``run`` raises
        `NotAuthorizedError` unconditionally: an earlier version called ``NodeContext.run_tool_intent``
        on the reasoning that the conductor injects a permission-gated executor there, but that
        executor is whatever the caller passed. Treating an arbitrary injected callable as proof of
        authorization is `registration != authorization` reintroduced one layer down. Phase 0 has no
        path that executes a projected operation; Phase 1 must route execution through
        `core.semantic.admission`, which consults the real `decide_tool_call` and cannot be handed a
        substitute.

        `expand_arguments` is injected, and defaults to a projector that returns ``[]`` -- "I cannot
        serve this clause". That is the correct Phase-0 answer: turning a clause into typed arguments
        needs either a resolver reading entity spans or natural-language extraction, and this phase
        has neither. Returning `[]` makes the planner fail the clause CLOSED as UNRESOLVED. The
        alternative -- guessing arguments from the clause string -- would be the hidden NLP this
        method is written to exclude.
        """
        intent = str(getattr(contract, "intent", "") or "").strip()
        if not intent:
            raise ValueError("a contract must declare an intent to be projected into an operation")
        output_schema = dict(getattr(contract, "output_schema", {}) or {})
        required = tuple(
            str(key)
            for key, declared in sorted(output_schema.items())
            if "optional" not in str(declared).lower()
        )
        def _run(_node: Any, _context: NodeContext) -> dict[str, Any]:
            # A PROJECTION IS A DESCRIPTION, NOT AN EXECUTABLE HANDLE.
            #
            # This used to call `context.run_tool_intent`, on the reasoning that the conductor injects
            # its own permission-gated executor there. A hostile review named the flaw: the executor
            # is whatever the caller passed. Treating an arbitrary injected callable as proof of
            # authorization means "somebody handed us a function" stands in for "the permission policy
            # said yes" -- which is the exact confusion `registration != authorization` exists to
            # prevent, reintroduced one layer down.
            #
            # Phase 0 has no path that executes a projected operation, so the correct projection has
            # no executable at all. Authorizing one is Phase-1 work and must go through
            # `core.semantic.admission`, which consults the real `decide_tool_call` and cannot be
            # handed a substitute.
            raise NotAuthorizedError(
                f"{intent} is projected for description only; a projection carries no authority to "
                "execute. Route execution through core.semantic.admission and the real permission policy."
            )

        def _render(_node: Any, result: Mapping[str, Any]) -> str:
            # Deterministic and dull on purpose. A projected operation has no domain renderer, and
            # asking a model to narrate a tool result is how "I ran these" becomes evidence.
            return str(result.get("response_text") or result.get("summary") or "").strip()

        return cls(
            name=intent,
            description=str(getattr(contract, "description", "") or ""),
            expand_arguments=expand_arguments or (lambda _clause: []),
            run=_run,
            render=_render,
            required_result_fields=required,
            tool_intent=intent,
            needs_generation=False,
            # NEVER inferred. This was `read_only`, which is a claim about side effects and says
            # nothing about concurrency safety: a read-only tool can share a cursor, a rate limit, a
            # cache or a file handle, and two of them at once is still wrong. No contract field
            # declares parallel safety, so the projection cannot know it and must not guess.
            can_run_in_parallel=False,
            is_derived=False,
        )


_LOCK = threading.RLock()
_OPERATIONS: dict[str, OperationSpec] = {}


def register_operation(spec: OperationSpec, *, replace: bool = False) -> None:
    """Register a node kind. Raises on a duplicate unless `replace` is set (tests do)."""
    name = str(spec.name or "").strip()
    if not name:
        raise ValueError("operation name must be non-empty")
    if name == UNRESOLVED_OPERATION:
        raise ValueError(f"{UNRESOLVED_OPERATION!r} is reserved for the fail-closed sentinel")
    with _LOCK:
        if name in _OPERATIONS and not replace:
            raise ValueError(f"operation {name!r} is already registered")
        if spec.serves_named_know_clauses:
            # Singleton-role declaration: two operations claiming the plan's KNOW-family
            # server is a configuration ambiguity this contract refuses to resolve by order
            # — picking the first would be fail-open dressed as fail-closed. `replace=True`
            # re-registering the SAME role for a different name is the same conflict.
            existing = [
                other_name
                for other_name, other in _OPERATIONS.items()
                if other_name != name and other.serves_named_know_clauses
            ]
            if existing:
                raise ValueError(
                    "serves_named_know_clauses is a singleton role; already declared by "
                    f"{sorted(existing)}"
                )
        _OPERATIONS[name] = spec


def unregister_operation(name: str) -> None:
    """Remove a node kind. Exists for tests and for the mutation that proves the registry is load-bearing."""
    with _LOCK:
        _OPERATIONS.pop(str(name or "").strip(), None)


def operation_spec(name: str) -> OperationSpec | None:
    """The spec for `name`, or None when nothing serves it. Never raises -- an unknown operation is
    a fail-closed UNRESOLVED node, not an exception that loses the whole plan."""
    with _LOCK:
        return _OPERATIONS.get(str(name or "").strip())


def known_operations() -> tuple[OperationSpec, ...]:
    with _LOCK:
        return tuple(_OPERATIONS[name] for name in sorted(_OPERATIONS))


def general_operations() -> tuple[OperationSpec, ...]:
    """Operations that may be offered a clause no named operation could serve, in a stable order.

    Ordered by declared priority and then by name -- never by registration order. A plan that
    changed shape because two modules happened to import in a different sequence would be a plan
    nobody could reproduce.
    """
    return tuple(
        sorted(
            (spec for spec in known_operations() if spec.serves_unclaimed_clause),
            key=lambda spec: (spec.general_priority, spec.name),
        )
    )


def _call_expander(
    resolver: Callable[..., list[dict[str, Any]]],
    spec: OperationSpec,
    clause: str,
    shared_context: SharedTurnContext | None,
    dependency_values: Sequence[str],
) -> list[dict[str, Any]]:
    """Invoke an expander with exactly the arguments its spec declared it wants.

    Dispatching on the declaration rather than inspecting the callable's signature is deliberate:
    an operation registered from a plugin, a lambda, or a `functools.partial` all answer the
    declaration identically, and none of them answer signature introspection identically.
    """
    if spec.wants_shared_context and spec.wants_dependency_values:
        return list(resolver(clause, shared_context, tuple(dependency_values)) or [])
    if spec.wants_shared_context:
        return list(resolver(clause, shared_context) or [])
    return list(resolver(clause) or [])


def expand_clause(
    spec: OperationSpec,
    clause: str,
    shared_context: SharedTurnContext | None = None,
    dependency_values: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Call `spec.expand_arguments` with exactly the inputs that spec declared it wants."""
    return _call_expander(spec.expand_arguments, spec, clause, shared_context, dependency_values)


def node_needs_generation(spec: OperationSpec, arguments: Mapping[str, Any] | None) -> bool:
    """Whether ONE node of `spec`, built with these arguments, will buy a generation.

    The spec's `needs_generation` is the operation's general truth; `model_free_arguments` is the
    operation's own declaration of the arguments that make a particular node model-free. A
    declaration that raises is treated as "not declared", so a fault in the predicate can only
    keep a node on the conservative (generation) path, never take a real generation node off it.
    """
    if not spec.needs_generation:
        return False
    predicate = spec.model_free_arguments
    if predicate is None:
        return True
    try:
        return not bool(predicate(dict(arguments or {})))
    except Exception:
        return True


def expand_named_clause(
    spec: OperationSpec,
    clause: str,
    shared_context: SharedTurnContext | None = None,
    dependency_values: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Expand a capability-compatible operation the model explicitly named.

    This does not trust the model: the planner must run semantic admission first.  It only lets an
    operation keep a narrow general-fallback recognizer while exposing its full named behavior.
    """

    resolver = spec.expand_named_arguments or spec.expand_arguments
    return _call_expander(resolver, spec, clause, shared_context, dependency_values)


_SUBJECT_SLUG_RE = re.compile(r"[^a-z0-9]+")


def value_label(entity: str, field_name: str) -> str:
    """The name a dependent node refers to one exported value by.

    Entity-scoped, always, even when only one node of an operation exists. Two market quotes in
    one plan both export ``price``; an unscoped name would let whichever finished last silently
    overwrite the other, and a plan that answers differently depending on network timing is a plan
    no receipt can account for.
    """
    slug = _SUBJECT_SLUG_RE.sub("_", str(entity or "").casefold()).strip("_")
    return f"{slug}_{field_name}" if slug else str(field_name)


def exported_values(spec: OperationSpec, entity: str, result: Mapping[str, Any]) -> dict[str, float]:
    """The numeric values `result` hands downstream, under their dependent-side names.

    Numeric only, and silently skipping what will not convert. A dependent binds these as symbols
    in grounded arithmetic, where a string operand is not a weaker input but an unevaluable one --
    and an export that raised here would turn one operation's odd field into a whole plan's fault.
    """
    out: dict[str, float] = {}
    for field_name in spec.exported_value_fields:
        if field_name not in result:
            continue
        try:
            out[value_label(entity, field_name)] = float(result[field_name])
        except (TypeError, ValueError):
            continue
    return out


def computed_dependency_bindings(
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Scope validated computation outputs to their declared dependency edge.

    Display labels are not identifiers and need not be unique. The scheduler
    supplies only successful dependency results; local recipe step_N names never
    cross this boundary. Units travel only with runtime-established dimensions.
    """
    from core.conductor.quantity_units import display_unit

    bindings: dict[str, dict[str, Any]] = {}
    for dependency, (node_id, result) in enumerate(results.items(), start=1):
        steps = result.get("steps")
        if not isinstance(steps, list):
            continue
        recorded_bindings = result.get("expression_bindings")
        source_symbols = {name: value for name, value in recorded_bindings.items()
                          if not re.fullmatch(r"step_\d+", str(name))} if isinstance(recorded_bindings, Mapping) else {}
        for position, step in enumerate(steps, start=1):
            if not isinstance(step, Mapping) or isinstance(step.get("value"), bool):
                continue
            try:
                value = float(step["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            unit_dimensions = (
                step.get("unit_dimensions")
                if step.get("unit_authority") in {"source_expression", "dimensionless_expression"}
                else None
            )
            unit = display_unit(unit_dimensions) if unit_dimensions else ""
            if unit_dimensions == {} and step.get("unit") in {"%", "percent", "ratio"}:
                unit = str(step["unit"])
            binding = {
                "value": value, "label": str(step.get("label") or ""),
                "node_id": node_id, "step_id": str(step.get("step_id") or ""),
                "unit_dimensions": unit_dimensions,
                "unit": unit,
            }
            expression = str(step.get("expression") or "")
            if expression and step.get("unit_authority") in {"source_expression", "dimensionless_expression"}:
                try:
                    names = {part.id for part in ast.walk(ast.parse(expression, mode="eval"))
                             if isinstance(part, ast.Name)}
                    scoped = {name: float(source_symbols[name]) for name in sorted(names)
                              if name in source_symbols and not isinstance(source_symbols[name], bool)
                              and math.isfinite(float(source_symbols[name]))}
                except (SyntaxError, TypeError, ValueError, OverflowError):
                    pass
                else:
                    binding["derivation"] = {"expression": expression, "source_scope_bindings": scoped}
            bindings[f"dependency_{dependency}_value_{position}"] = binding
            # Original step identities survive rejected holes; list positions are not aliases.
            step_id = str(step.get("step_id") or "")
            if re.fullmatch(r"step_[1-9]\d*", step_id):
                source_symbols[step_id] = value
    return bindings


#: Arguments that name a thing for a reader rather than identifying the work. Excluded from the
#: default key so a display label can differ without splitting one piece of work into two.
_DISPLAY_ONLY_ARGUMENTS = frozenset({"entity", "clause", "fact_labels", "fact_values", "roles"})


def execution_key(spec: OperationSpec, arguments: Mapping[str, Any]) -> str:
    """The identity of the work `arguments` describe, under this operation.

    Operation-owned: a spec may declare its own producer, and otherwise every typed argument that
    is not display-only participates. Two nodes with the same key are the same work and may be
    merged; two with different keys never may, however similar their text.

    An operation whose own key producer raises is NOT quietly given the default key. That fallback
    made two identities for one piece of work -- the planner's node keyed one way and the
    requirement's realization the other -- so the same work was scheduled twice and both halves
    were reported. A broken key producer is a defect, and it surfaces as one; see
    `core.conductor.realization.lookup_capability`, which is where it becomes a typed
    `CapabilityLookupResult.DEFECT` rather than an answer about the world.
    """
    if spec.execution_key is not None:
        return f"{spec.name}:{spec.execution_key(arguments)}"
    typed = {
        str(name): str(value)
        for name, value in sorted(dict(arguments or {}).items())
        if name not in _DISPLAY_ONLY_ARGUMENTS and value not in (None, "")
    }
    if not typed:
        # Nothing typed to key on: fall back to the display label so the node still has an
        # identity, rather than colliding with every other node of its family.
        typed = {"entity": str(dict(arguments or {}).get("entity") or "")}
    return f"{spec.name}:" + "|".join(f"{k}={v}" for k, v in typed.items())


class OperationDefectError(RuntimeError):
    """An operation's own callable raised. A defect in the runtime, not a fact about the world.

    Its own type because the alternative was measured lying: `except Exception: return None` here
    made a resolver that crashed indistinguishable from a subject nobody could identify, so a bug
    in a realizer was reported to the reader as "Palladium could not be identified". The two
    sentences are about different things and only one of them was true.
    """


def realize_subject(spec: OperationSpec, subject: str) -> dict[str, Any] | None:
    """The argument set that serves `subject`, or None when this operation cannot serve it.

    None is a real answer -- no capability binds this subject. An exception is NOT translated into
    that answer; it is re-raised as `OperationDefectError` and becomes a typed defect upstream.
    """
    if spec.realize_subject is None:
        return None
    try:
        arguments = spec.realize_subject(subject)
    except Exception as exc:
        raise OperationDefectError(
            f"{spec.name}.realize_subject({subject!r}) raised {type(exc).__name__}: {exc}"
        ) from exc
    return dict(arguments) if isinstance(arguments, dict) and arguments else None


def named_subjects(spec: OperationSpec, clause: str) -> tuple[str, ...]:
    """Subjects of this operation's domain that `clause` names. Never raises.

    A recognizer fault must not lose the plan: an operation whose subject enumerator throws is
    treated as enumerating nothing, which is exactly the behaviour registries had before it was
    declared.
    """
    if spec.subjects_named is None:
        return ()
    try:
        return tuple(str(item) for item in (spec.subjects_named(clause) or ()) if str(item).strip())
    except Exception:
        return ()


def operation_catalog_text() -> str:
    """The registry rendered for the planner prompt.

    Generated from the registry rather than written out, so a newly registered operation is offered
    to the planner without anyone remembering to update a prompt. A hand-maintained catalog beside
    a registry is how the two drift.
    """
    lines: list[str] = []
    for spec in known_operations():
        suffix = " (derived: requires depends_on)" if spec.is_derived else ""
        lines.append(f"  {spec.name} - {spec.description}{suffix}")
    return "\n".join(lines)


__all__ = [
    "UNRESOLVED_OPERATION",
    "NodeContext",
    "NotAuthorizedError",
    "OperationDefectError",
    "OperationSpec",
    "execution_key",
    "expand_clause",
    "expand_named_clause",
    "exported_values",
    "general_operations",
    "known_operations",
    "named_subjects",
    "node_needs_generation",
    "operation_catalog_text",
    "operation_spec",
    "realize_subject",
    "register_operation",
    "unregister_operation",
    "value_label",
]
