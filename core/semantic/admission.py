"""Admission: the runtime's decision about whether a proposed intent may run.

**Nothing in the shipped turn path calls this yet.** It exists in Phase 0 so the contract is pinned
by tests before any behaviour depends on it, and so the sabotage suite can assert now -- not after
someone builds the resolver -- that admission has no way to skip the permission gate and no way to
fall through to chat.

The order below is the design, not an implementation detail. Each check answers a different
question, and each has its own typed refusal:

1. **Shape** -- is this a proposal at all?                    → `MALFORMED_PROPOSAL`
2. **Existence** -- does the registry serve this operation?   → `UNKNOWN_OPERATION`
3. **Reference** -- do the spans bind to this turn's text?    → `BAD_SPAN`
4. **Dependency** -- are the clause references real?          → `INVALID_DEPENDENCY`
5. **Arguments** -- do they fit the declared schema?          → `ARGUMENT_EXPANSION_FAILED`
6. **Capability** -- can the runtime currently serve it?      → `CAPABILITY_UNSUPPORTED`
7. **Declaration** -- does the contract state a side effect?  → `SIDE_EFFECT_CLASS_UNDECLARED`
8. **Permission** -- may it run, per the real policy?         → `PERMISSION_DENIED` / admit

Four properties this file is written to guarantee, each pinned by a sabotage test:

* **There is exactly one exit that admits**, and it is downstream of `decide_tool_call`. Not "we
  call the policy in the normal case" -- there is no other case. Deleting the call cannot produce a
  build where admission still admits.
* **The model never supplies authority.** `side_effect_class` and `approval_requirement` are read
  off the registered contract. An `IntentProposal` has no field to carry them, so a model that
  tries to declare its own operation harmless has nowhere to write it.
* **Registration is not authorization.** Step 2 finding a contract is what lets step 8 *ask*; it is
  never what answers. A newly registered capability is describable immediately and runnable only if
  the policy says so.
* **Ambiguity is not a licence.** `admit_all` loops and asks per candidate. There is no batch call,
  no "the first one passed so the rest are fine", and a partial result is reported as partial.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import FrameType, GeneratorType
from typing import Any

from core.semantic.canonical_text import CanonicalText, SpanBindingError
from core.semantic.types import (
    AdmissionResult,
    IntentProposal,
    PermissionRecord,
    ReasonCode,
    ValidatedIntent,
    _admission_outcome_claim,
    _new_admission_outcome_claim,
)

#: Side-effect classes a contract may declare. A contract declaring none reaches
#: `SIDE_EFFECT_CLASS_UNDECLARED` rather than being treated as harmless -- "unspecified" must never
#: resolve to "safe", because that is the direction in which a mistake is unrecoverable.
_UNDECLARED_SIDE_EFFECTS = frozenset({"", "unknown", "none", "unspecified"})


def _default_contract_lookup(operation: str) -> Any | None:
    """Resolve an operation name against the registry, plugins included.

    Reads `core.tool_registry.registered_tools()` rather than `runtime_tool_contract_map()` so a
    capability registered at runtime is visible here exactly as a built-in is. A registry that only
    sees what shipped cannot generalize past it, which is the whole complaint this architecture
    answers.
    """
    try:
        from core.tool_registry import registered_tools

        name = str(operation or "").strip()
        for contract in registered_tools():
            if str(getattr(contract, "intent", "")) == name:
                return contract
    except Exception:
        return None
    return None


#: The one policy this module will accept an answer from. Named as data so the receipt, the tests
#: and the refusal messages all quote the same string.
PERMISSION_POLICY = "core.mode_permission_policy.decide_tool_call"


def _consult_permission_policy(
    *, intent: str, arguments: dict[str, Any], task_id: str, source_context: dict[str, Any]
) -> PermissionRecord:
    """Ask the REAL permission policy, and mint the record from its own decision object.

    **Not injectable, and that is the fix.** This used to be a `permission_check` parameter on
    `admit`, which meant any caller could hand in a callable returning
    `PermissionRecord(allowed=True, policy="fabricated.policy")` and be admitted without a policy
    ever being consulted. A hostile review demonstrated it. Naming a policy is not evidence of
    having asked one, and an injection point on the authority seam is not a test affordance -- it is
    the authority seam being optional.

    A second hostile review then broke what replaced it: `grant_from_policy_decision(decision, ...)`
    accepted the engine's decision type FROM A CALLER, and the engine's decision type is
    constructible. Taking a caller's object and type-checking it does not make it authority.

    So authority is now a side effect of THIS FUNCTION having run. It calls `decide_tool_call`
    itself, reads the returned decision as evidence, and mints a grant through
    `core.semantic.types._issue_grant` -- which registers the grant instance privately and binds it
    to this one operation.

    **What that does and does not buy, stated accurately.** It does NOT make an allowing record
    unconstructible: `_issue_grant` is importable, and a hostile reviewer used it to build one.
    Python has no private, and pretending otherwise in a comment is how a false claim survives three
    reviews. What holds is the property that matters -- **a caller-constructed or imported
    permission value cannot cause an admission** -- because `admit` takes no permission parameter
    and builds its own record here, from the real policy, for the exact operation about to run.
    Tests that need a refusal drive the real policy into refusing rather than substituting for it.

    Every failure path denies. A permission engine that cannot answer denies; a gate that fails open
    is not a gate.
    """
    from core.semantic import types as semantic_types

    operation = str(intent or "")
    if not operation:
        return PermissionRecord.refusal(policy=PERMISSION_POLICY, detail="no operation identity to authorize")
    try:
        from core.mode_permission_policy import PermissionDecision, decide_tool_call

        decision = decide_tool_call(
            intent=operation,
            arguments=dict(arguments or {}),
            task_id=str(task_id or ""),
            source_context=dict(source_context or {}),
        )
    except Exception as exc:
        return PermissionRecord.refusal(
            policy=PERMISSION_POLICY,
            operation=operation,
            detail=f"permission policy could not be consulted: {type(exc).__name__}: {exc}"[:200],
        )

    # The decision object is EVIDENCE, read here and nowhere else. It is never accepted from a
    # caller: a hostile review constructed the engine's own type by hand and minted a grant with it,
    # so the only thing that turns a decision into authority is having made this call.
    if not isinstance(decision, PermissionDecision):
        return PermissionRecord.refusal(
            policy=PERMISSION_POLICY,
            operation=operation,
            detail=(
                f"the permission policy returned {type(decision).__name__}, not its own "
                "PermissionDecision -- refusing rather than trusting a substitute"
            ),
        )

    effect = str(getattr(decision.effect, "value", decision.effect) or "").lower()
    actions = tuple(str(getattr(a, "value", a)) for a in getattr(decision, "actions", ()) or ())
    detail = str(getattr(decision, "reason", "") or "")
    requires_approval = getattr(decision, "approval_request", None) is not None

    if effect != "allow" or not bool(getattr(decision, "allowed", False)):
        return PermissionRecord(
            policy=PERMISSION_POLICY, operation=operation, effect=effect or "deny",
            actions=actions, detail=detail, requires_approval=requires_approval,
        )
    try:
        digest = semantic_types.arguments_digest(arguments)
    except TypeError as exc:
        return PermissionRecord.refusal(
            policy=PERMISSION_POLICY,
            operation=operation,
            detail=f"permission arguments have no canonical typed identity: {exc}"[:200],
        )
    # Minted here, bound to THIS operation and exact typed arguments, and registered by the types
    # module. The admitted result itself still requires production issuance below.
    return PermissionRecord(
        policy=PERMISSION_POLICY, operation=operation, effect="allow", actions=actions,
        detail=detail, requires_approval=requires_approval,
        grant=semantic_types._issue_grant(operation, digest),
    )


def _validate_arguments(contract: Any, arguments: dict[str, Any]) -> tuple[bool, str]:
    """Reject arguments that are not executable-shaped for this contract.

    The undeclared-keys rule mirrors the runtime dispatcher's own check
    (`core.runtime_execution_tools.execute_runtime_tool` refuses unknown keys against `input_schema`),
    so admission cannot pass something the executor would then reject. On top of it the executable
    schema DERIVED from the contract's own prose (`core.semantic.executable_schema`) checks required
    keys, declared types, ceilings and per-name domain rules -- deterministically, from the contract
    that already governs execution, never from a second hand-written registry. Where that prose
    cannot be read the schema says so and only the undeclared-keys rule applies.
    """
    from core.semantic.executable_schema import schema_from_contract, validate_arguments

    schema = dict(getattr(contract, "input_schema", {}) or {})
    unknown = sorted(str(key) for key in arguments if str(key) not in schema)
    if unknown:
        allowed = sorted(str(key) for key in schema)
        return False, f"undeclared argument(s): {', '.join(unknown)}; declared: {', '.join(allowed) or 'none'}"
    executable = schema_from_contract(str(getattr(contract, "intent", "") or ""), schema)
    problems = validate_arguments(executable, arguments)
    if problems:
        return False, "; ".join(problems)
    return True, ""


def _binding_problems(graph: Any, slot_id: Any, arguments: dict[str, Any]) -> tuple[str, ...]:
    """Arguments must derive from the request the graph recorded; a permitted operation on
    invented operands is still the wrong call. Empty when no graph binding was supplied."""
    if graph is None or not slot_id:
        return ()
    from core.semantic.executable_schema import arguments_bind_to_slot

    return arguments_bind_to_slot(graph, slot_id, arguments)


@dataclass(frozen=True)
class AdmissionPreview:
    """What ``admit`` WOULD decide, computed without consuming anything.

    Same validation steps, same typed reasons; the permission step is the engine's non-consuming
    preview. Never a grant, never an admitted result, never a token spent or an approval request
    created. It exists so a planner or a shadow comparison can ask "would this run?" without the
    asking being the running.
    """

    would_admit: bool
    reason: ReasonCode
    detail: str = ""
    permission_effect: str = ""
    would_consume_token: bool = False
    would_request_approval: bool = False
    intent: ValidatedIntent | None = None


def _prevalidate(
    proposal: IntentProposal,
    *,
    canonical: CanonicalText,
    resolved_clause_count: int | None,
    admitted_clauses: frozenset[int] | None,
    contract_lookup: Callable[[str], Any | None] | None,
    graph: Any = None,
    slot_id: Any = None,
) -> tuple[AdmissionResult | None, ValidatedIntent | None, dict[str, Any]]:
    """Steps 1-7 of admission (everything before the permission policy), shared by ``admit`` and
    ``preview_admission``. Returns ``(rejection, intent, arguments)``: exactly one of the first two
    is set. Pure: consults no policy, mints nothing."""
    lookup = contract_lookup or _default_contract_lookup

    # 1. Shape.
    if not isinstance(proposal, IntentProposal):
        return AdmissionResult.reject(
            ReasonCode.MALFORMED_PROPOSAL, detail=f"not an IntentProposal: {type(proposal).__name__}"
        ), None, {}
    operation = str(proposal.operation or "").strip()
    if not operation or not str(proposal.request_text or "").strip():
        return AdmissionResult.reject(
            ReasonCode.MALFORMED_PROPOSAL, detail="a proposal needs both an operation and a request"
        ), None, {}
    try:
        arguments = dict(proposal.arguments or {})
    except Exception:
        return AdmissionResult.reject(
            ReasonCode.MALFORMED_PROPOSAL, detail="arguments are not a mapping"
        ), None, {}

    # 2. Existence. The registry's word, never the model's.
    contract = lookup(operation)
    if contract is None:
        return AdmissionResult.reject(
            ReasonCode.UNKNOWN_OPERATION, detail=f"no registered contract serves {operation!r}"
        ), None, arguments
    # CANONICAL IDENTITY. The contract handed back must BE the operation asked for. A hostile review
    # supplied a lookup that returned `machine.inspect_specs` for `evil.delete_everything`, and the
    # clause was admitted under the authorized capability's grant while naming the unauthorized one.
    # The operation authorized has to be exactly the operation executed, so a lookup that answers
    # with a different identity is refused rather than followed.
    canonical_intent = str(getattr(contract, "intent", "") or "")
    if canonical_intent != operation:
        return AdmissionResult.reject(
            ReasonCode.UNKNOWN_OPERATION,
            detail=(
                f"the registry answered {operation!r} with a contract whose identity is "
                f"{canonical_intent!r}; one operation may not be authorized under another's name"
            ),
        ), None, arguments

    # 3. Reference. A span that does not bind names a substring of some other text.
    for span in proposal.spans:
        try:
            span.resolve(canonical)
        except SpanBindingError as exc:
            return AdmissionResult.reject(ReasonCode.BAD_SPAN, detail=str(exc)[:200]), None, arguments
        except Exception as exc:
            return AdmissionResult.reject(
                ReasonCode.BAD_SPAN, detail=f"{type(exc).__name__}: {exc}"[:200]
            ), None, arguments

    # 4. Dependency.
    for dependency in proposal.depends_on:
        if not isinstance(dependency, int) or dependency < 0 or dependency >= proposal.index:
            return AdmissionResult.reject(
                ReasonCode.INVALID_DEPENDENCY,
                detail=f"clause {proposal.index} depends on {dependency!r}, which is not an earlier clause",
            ), None, arguments
        if resolved_clause_count is None or dependency >= resolved_clause_count:
            return AdmissionResult.reject(
                ReasonCode.INVALID_DEPENDENCY,
                detail=f"clause {dependency} has not been resolved, so its answer cannot be depended on",
            ), None, arguments
        # Existing-and-earlier is not the same as ANSWERED. `admitted_clauses` carries which earlier
        # clauses actually produced an answer; without it, a dependent whose prerequisite was
        # refused still admitted and would run on an answer that does not exist.
        if admitted_clauses is None or dependency not in admitted_clauses:
            return AdmissionResult.reject(
                ReasonCode.PREREQUISITE_FAILED,
                detail=(
                    f"clause {dependency} did not produce an answer, so clause {proposal.index} "
                    "cannot depend on it"
                ),
            ), None, arguments

    # 5. Arguments: executable-shaped for the contract, and bound to the request when a graph is given.
    ok, detail = _validate_arguments(contract, arguments)
    if not ok:
        return AdmissionResult.reject(ReasonCode.ARGUMENT_EXPANSION_FAILED, detail=detail), None, arguments
    binding = _binding_problems(graph, slot_id, arguments)
    if binding:
        return AdmissionResult.reject(
            ReasonCode.ARGUMENT_EXPANSION_FAILED, detail="; ".join(binding)
        ), None, arguments

    # 6. Capability. Registered is not the same as currently servable.
    if not bool(getattr(contract, "supported", False)):
        return AdmissionResult.reject(
            ReasonCode.CAPABILITY_UNSUPPORTED,
            detail=str(getattr(contract, "unsupported_reason", "") or "the runtime cannot serve this")[:200],
        ), None, arguments

    # 7. Declaration. Read off the contract; the proposal has no field that could supply it.
    side_effect_class = str(getattr(contract, "side_effect_class", "") or "").strip().lower()
    if side_effect_class in _UNDECLARED_SIDE_EFFECTS:
        return AdmissionResult.reject(
            ReasonCode.SIDE_EFFECT_CLASS_UNDECLARED,
            detail=f"{operation!r} declares no side-effect class, so nothing can authorize it",
        ), None, arguments

    intent = ValidatedIntent(
        operation=operation,
        request_text=str(proposal.request_text),
        arguments=arguments,
        canonical=canonical,
        spans=tuple(proposal.spans),
        side_effect_class=side_effect_class,
        tool_intent=str(getattr(contract, "intent", operation)),
        approval_requirement=str(getattr(contract, "approval_requirement", "") or ""),
        depends_on=tuple(proposal.depends_on),
    )
    return None, intent, arguments


def preview_admission(
    proposal: IntentProposal,
    *,
    canonical: CanonicalText,
    task_id: str = "",
    source_context: dict[str, Any] | None = None,
    resolved_clause_count: int | None = None,
    admitted_clauses: frozenset[int] | None = None,
    contract_lookup: Callable[[str], Any | None] | None = None,
    graph: Any = None,
    slot_id: Any = None,
) -> AdmissionPreview:
    """Would ``admit`` admit this? Pure. Consumes no approval, mints no grant, records nothing."""
    rejection, intent, arguments = _prevalidate(
        proposal, canonical=canonical, resolved_clause_count=resolved_clause_count,
        admitted_clauses=admitted_clauses, contract_lookup=contract_lookup, graph=graph, slot_id=slot_id,
    )
    if rejection is not None or intent is None:
        return AdmissionPreview(
            would_admit=False,
            reason=rejection.reason if rejection is not None else ReasonCode.MALFORMED_PROPOSAL,
            detail=rejection.detail if rejection is not None else "",
        )
    try:
        from core.mode_permission_policy import preview_tool_call

        preview = preview_tool_call(
            intent=intent.tool_intent, arguments=dict(arguments), task_id=str(task_id or ""),
            source_context=dict(source_context or {}),
        )
    except Exception as exc:
        return AdmissionPreview(
            would_admit=False, reason=ReasonCode.PERMISSION_DENIED,
            detail=f"permission policy could not be previewed: {type(exc).__name__}: {exc}"[:200], intent=intent,
        )
    effect = str(getattr(preview.effect, "value", preview.effect) or "").lower()
    allowed = bool(getattr(preview, "allowed", False)) and effect == "allow"
    return AdmissionPreview(
        would_admit=allowed,
        reason=ReasonCode.ADMITTED if allowed else ReasonCode.PERMISSION_DENIED,
        detail=str(getattr(preview, "reason", "") or ""),
        permission_effect=effect,
        would_consume_token=bool(getattr(preview, "would_consume_token", False)),
        would_request_approval=bool(getattr(preview, "would_request_approval", False)),
        intent=intent,
    )


def admit(
    proposal: IntentProposal,
    *,
    canonical: CanonicalText,
    task_id: str = "",
    source_context: dict[str, Any] | None = None,
    resolved_clause_count: int | None = None,
    admitted_clauses: frozenset[int] | None = None,
    contract_lookup: Callable[[str], Any | None] | None = None,
    graph: Any = None,
    slot_id: Any = None,
) -> AdmissionResult:
    """Decide whether one proposed intent may run. Always returns a typed result; never raises.

    `resolved_clause_count` is how many clauses have already been validated in this turn, used to
    check `depends_on`. `None` means "dependencies are not checkable here", and a proposal that
    declares dependencies in that case is refused rather than admitted on trust. With ``graph`` and
    ``slot_id`` the arguments must also derive from the request that slot records.
    """
    context = dict(source_context or {})
    rejection, intent, arguments = _prevalidate(
        proposal, canonical=canonical, resolved_clause_count=resolved_clause_count,
        admitted_clauses=admitted_clauses, contract_lookup=contract_lookup, graph=graph, slot_id=slot_id,
    )
    if rejection is not None or intent is None:
        return rejection if rejection is not None else AdmissionResult.reject(ReasonCode.MALFORMED_PROPOSAL)

    # 8. Permission. The only route to an admit runs through here.
    permission_consultation = _consult_permission_policy
    permission = permission_consultation(
        intent=intent.tool_intent,
        arguments=arguments,
        task_id=str(task_id or ""),
        source_context=context,
    )
    if not isinstance(permission, PermissionRecord):
        return AdmissionResult.reject(
            ReasonCode.PERMISSION_DENIED,
            intent=intent,
            detail=f"permission check returned {type(permission).__name__}, not a decision",
        )
    # Consume atomically. ``AdmissionResult`` has no public admitted constructor: the permission
    # record cannot be supplied by a caller, and the only record reaching this line is the one the
    # production consultation immediately above returned for these exact arguments.
    authorized, why_not = permission.consume_authorization(intent)
    if not authorized:
        return AdmissionResult.reject(
            ReasonCode.PERMISSION_DENIED,
            intent=intent,
            permission=permission,
            detail=permission.detail or why_not or "the permission policy refused this call",
        )
    # Deliberately no admitted-result factory that accepts permission state. Such an API recreates
    # the bypass even if the value's class looks private. The admitted
    # record is materialized at this one site only, after the production call and atomic consume.
    result = object.__new__(AdmissionResult)
    object.__setattr__(result, "reason", ReasonCode.ADMITTED)
    object.__setattr__(result, "intent", intent)
    object.__setattr__(result, "permission", permission)
    object.__setattr__(result, "detail", "")
    # The claim object is made only for this exact result and retained as a fast local in this
    # completed frame. Replacing it on the result cannot mint a fresh resolution occasion because
    # the historical admission verifier requires this identity relationship to remain exact.
    claim = _new_admission_outcome_claim(result)
    object.__setattr__(result, "_outcome_claim", claim)
    # A completed production frame is not caller-constructible. The frame owns ``result`` in its
    # fast locals, so copying or aliasing this frame onto another object does not transfer authority.
    object.__setattr__(result, "_occasion", sys._getframe())
    return result


# Seal the getter directly to this exact production code identity and the real frame type. The
# verifier has no closure and consults no helper global, so there is no caller-reachable collection
# or rebinding point that can register another object. Replacing production bytecode itself remains
# arbitrary code modification, not result construction or authority-data mutation.
_admitted_getter = AdmissionResult.admitted.fget
if _admitted_getter is None:  # pragma: no cover - import-time invariant
    raise RuntimeError("AdmissionResult.admitted must be a property")
_authority_constants = list(_admitted_getter.__code__.co_consts)
_authority_marker = (
    "__VOOL_ADMIT_CODE__",
    "__VOOL_FRAME_TYPE__",
    "__VOOL_ADMIT_GLOBALS__",
    "__VOOL_PERMISSION_CONSULTATION__",
    "__VOOL_OUTCOME_CLAIM_GENERATOR_CODE__",
    "__VOOL_GENERATOR_TYPE__",
)
if _authority_constants.count(_authority_marker) != 1:  # pragma: no cover - import-time invariant
    raise RuntimeError("AdmissionResult authority marker is missing or ambiguous")
_authority_constants[_authority_constants.index(_authority_marker)] = (
    admit.__code__,
    FrameType,
    globals(),
    _consult_permission_policy,
    _admission_outcome_claim.__code__,
    GeneratorType,
)
_admitted_getter.__code__ = _admitted_getter.__code__.replace(co_consts=tuple(_authority_constants))
del _admitted_getter, _authority_constants, _authority_marker


def admit_all(
    proposals: Sequence[IntentProposal],
    *,
    canonical: CanonicalText,
    task_id: str = "",
    source_context: dict[str, Any] | None = None,
    contract_lookup: Callable[[str], Any | None] | None = None,
) -> tuple[AdmissionResult, ...]:
    """Admit every proposal INDEPENDENTLY. One result per proposal, in order, always.

    The independence is the point, and it is the hostile-review correction that an AMBIGUOUS
    "just run both readings" must not become one permission check covering two operations. Each
    candidate asks the policy about itself; a candidate that passes tells you nothing about the one
    beside it, which may touch a different file with a different side-effect class.

    The returned tuple is always the same length as `proposals`. A clause cannot drop out of this
    function -- not by refusal, not by exception, not by an early return.
    """
    results: list[AdmissionResult] = []
    admitted: set[int] = set()
    for position, proposal in enumerate(proposals):
        try:
            outcome = admit(
                proposal,
                canonical=canonical,
                task_id=task_id,
                source_context=source_context,
                # Position bounds which clauses EXIST yet; `admitted_clauses` bounds which of them
                # actually produced an answer. Counting positions alone let a dependent admit after
                # its prerequisite was rejected -- found by a hostile review.
                resolved_clause_count=position,
                admitted_clauses=frozenset(admitted),
                contract_lookup=contract_lookup,
            )
            if outcome.admitted:
                admitted.add(getattr(proposal, "index", position))
            results.append(outcome)
        except Exception as exc:
            # A fault admitting one clause must not silently shorten the list; it becomes that
            # clause's typed refusal so the count still matches and nothing disappears.
            results.append(
                AdmissionResult.reject(
                    ReasonCode.MALFORMED_PROPOSAL,
                    detail=f"admission raised for clause {position}: {type(exc).__name__}: {exc}"[:200],
                )
            )
    return tuple(results)


__all__ = ["AdmissionPreview", "admit", "admit_all", "preview_admission"]
