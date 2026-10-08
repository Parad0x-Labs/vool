"""Executor-owned provenance for deterministic forget replies."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType


class ForgetOutcome(str):
    """A transport value. Constructing this class does not confer authority."""

    __slots__ = ("request", "session", "turn_key")

    def __new__(cls, response: str, *, request: str, session: str, turn_key: str = ""):
        value = super().__new__(cls, response)
        object.__setattr__(value, "request", str(request or ""))
        object.__setattr__(value, "session", str(session or "").strip())
        object.__setattr__(value, "turn_key", str(turn_key or ""))
        return value

    def __setattr__(self, name, value):
        raise AttributeError("memory command results are immutable")

    def __delattr__(self, name):
        raise AttributeError("memory command results are immutable")


class ForgetNoMatch(ForgetOutcome):
    """Legacy result shape, subject to the same executor registration check."""

    __slots__ = ()

    def __new__(cls, target: str, *, request: str, session: str, turn_key: str = ""):
        return super().__new__(
            cls, f'Nothing was removed. Forget found no memory in this chat that matched "{target}".',
            request=request, session=session, turn_key=turn_key,
        )


def command_turn_key(source_context: dict | None) -> str:
    from core.execution_truth import resolve_turn_key
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    context = source_context or {}
    request = context.get(TURN_REQUEST_KEY)
    if isinstance(request, TurnRequest):
        return str(request.turn_id or "").strip()
    return str(resolve_turn_key(dict(context), None) or "")


def _owner_identity(request: str, session: str, source_context: dict | None) -> tuple[str, ...]:
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    context = source_context or {}
    owner = context.get(TURN_REQUEST_KEY)
    session = str(session or "").strip()
    if isinstance(owner, TurnRequest):
        if not owner.request_id.strip() or not owner.turn_id.strip() or owner.session_id != session:
            return ()
        return ("request", owner.request_id, owner.turn_id, session, owner.user_text)
    turn_key = command_turn_key(context)
    if not turn_key or not session:
        return ()
    return ("turn", turn_key, session, str(request or ""))


@dataclass(frozen=True)
class _ForgetExecution:
    outcome: ForgetOutcome
    identity: tuple[str, ...]
    request: str
    command: str
    argument: str
    session: str
    turn_key: str
    response: str
    status: str
    stores: Mapping[str, object]


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


class _ForgetOutcomeRegistry:
    """Held by the memory executor; result attributes never act as credentials."""

    def __init__(self):
        self._lock = RLock()
        self._executions: OrderedDict[tuple[str, ...], _ForgetExecution] = OrderedDict()
        self._admissions: dict[tuple[str, ...], tuple] = {}
        self._admission_results: dict[tuple[str, ...], dict[str, str]] = {}
        self._admission_expected: dict[tuple[str, ...], frozenset[str]] = {}

    def admission(self, *, request, session, source_context):
        identity = _owner_identity(request, session, source_context)
        with self._lock:
            return self._admissions.get(identity)

    def admit(self, operations, *, request, session, source_context):
        identity = _owner_identity(request, session, source_context)
        if not identity:
            raise RuntimeError("forget admission requires its owning turn")
        with self._lock:
            self._admissions[identity] = tuple(operations)
            self._admission_results[identity] = {}
            self._admission_expected[identity] = forget_demand_targets(request)

    def admission_ready(self, *, request, session, source_context):
        identity = _owner_identity(request, session, source_context)
        with self._lock:
            results = self._admission_results.get(identity, {})
            expected = self._admission_expected.get(identity, frozenset())
            return (bool(expected) and expected <= results.keys()
                    and all(status in {"applied", "no_match"} for status in results.values()))

    def release_admission(self, *, request, session, source_context):
        identity = _owner_identity(request, session, source_context)
        with self._lock:
            self._admissions.pop(identity, None)
            self._admission_results.pop(identity, None)
            self._admission_expected.pop(identity, None)

    def turn_not_started(self, source_context):
        from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

        owner = (source_context or {}).get(TURN_REQUEST_KEY)
        if not isinstance(owner, TurnRequest):
            return False
        identity = _owner_identity(owner.user_text, owner.session_id, source_context)
        with self._lock:
            execution = self._executions.get(identity)
            return execution is not None and execution.status == "not_started"

    def record(
        self, response: str, *, request: str, session: str, source_context: dict | None,
        argument: str, status: str, stores: Mapping[str, object] | None = None,
    ) -> ForgetOutcome:
        identity = _owner_identity(request, session, source_context)
        owning_request = identity[-1] if identity else str(request or "")
        turn_key = command_turn_key(source_context)
        # The executor has already rendered the complete result, including the exact target.
        # Bypass the legacy template constructor only here, before registering this object.
        cls = ForgetNoMatch if status == "no_match" else ForgetOutcome
        outcome = ForgetOutcome.__new__(
            cls, str(response), request=owning_request, session=session, turn_key=turn_key,
        )
        if identity:
            execution = _ForgetExecution(
                outcome=outcome, identity=identity, request=owning_request,
                command=str(request), argument=str(argument), session=str(session).strip(),
                turn_key=turn_key, response=str(response), status=str(status),
                stores=_freeze(dict(stores or {})),
            )
            with self._lock:
                self._executions[identity] = execution
                if identity in self._admission_results:
                    from core.persistent_memory import _normalize_forget_target
                    from core.vool_memory import _normalize_forget_token

                    self._admission_results[identity][_normalize_forget_token(_normalize_forget_target(argument))] = str(status)
                self._executions.move_to_end(identity)
                # Eviction revokes authority; a retained or serialized reply never restores it.
                while len(self._executions) > 512:
                    self._executions.popitem(last=False)
        return outcome

    def verified(
        self, result: dict, *, user_input: str, effective_input: str | None,
        session_id: str | None, source_context: dict | None,
    ) -> _ForgetExecution | None:
        outcome = result.get("_memory_command_outcome", result.get("response"))
        if type(outcome) not in (ForgetOutcome, ForgetNoMatch):
            return None
        for request in (user_input, effective_input):
            identity = _owner_identity(str(request or ""), str(session_id or ""), source_context)
            if not identity:
                continue
            with self._lock:
                execution = self._executions.get(identity)
            if execution is None or execution.outcome is not outcome:
                continue
            if (
                str(outcome) == execution.response
                and getattr(outcome, "request", None) == execution.request
                and getattr(outcome, "session", None) == execution.session
                and getattr(outcome, "turn_key", None) == execution.turn_key
            ):
                return replace(execution)
        return None


def verified_forget_outcome(
    result: dict, *, user_input: str, effective_input: str | None = None,
    session_id: str | None = None, source_context: dict | None = None,
) -> _ForgetExecution | None:
    from core.persistent_memory import _forget_outcomes

    return _forget_outcomes.verified(
        result, user_input=user_input, effective_input=effective_input,
        session_id=session_id, source_context=source_context,
    )


def is_bound_forget_no_match(
    result: dict, *, user_input: str, effective_input: str | None,
    session_id: str | None, source_context: dict | None,
) -> bool:
    execution = verified_forget_outcome(
        result, user_input=user_input, effective_input=effective_input,
        session_id=session_id, source_context=source_context,
    )
    return bool(
        execution is not None and execution.status == "no_match"
        and str(result.get("response") or "") == execution.response
    )


def requires_forget_outcome(
    result: dict, *, user_input: str, effective_input: str | None = None,
    source_context: dict | None = None,
) -> bool:
    """Use the memory command parser and profile ownership, never reply vocabulary."""
    from core.agent_runtime.answer_coverage import (
        _DEMAND_UNIT_SPLIT_CONNECTORS,
        _SEQUENCE_CONNECTORS,
        _UNIT_TOKEN_RE,
        _literal_spans,
        turn_slices,
    )
    from core.operator_profile_interpretation import _clause_spans, interpret_profile_turn
    from core.operator_profile_turn import _remaining_memory_text
    from core.persistent_memory import _FORGET_RE

    if isinstance(result.get("_memory_command_outcome", result.get("response")), ForgetOutcome):
        return True
    # Include the profile grammar's contrastive conjunction in the shared
    # demand grammar's connector set; the command parser owns the verb set.
    connectors = {*_DEMAND_UNIT_SPLIT_CONNECTORS, *_SEQUENCE_CONNECTORS, "but"}
    for text in (user_input, effective_input):
        command = str(text or "").strip()
        # The profile owner consumes its own clauses before the memory command lane.
        # Reconstruct only that structural split when finalization sees the full request.
        profile_spans = [
            (proposal.source_start, proposal.source_end)
            for proposal in interpret_profile_turn(command)
        ]
        if profile_spans:
            command = _remaining_memory_text(command, profile_spans)
        # Profile and memory clauses share the conjunction grammar. Protect
        # literal source spans before either parser subdivides the request.
        instruction_text = list(command)
        for start, end in _literal_spans(command):
            instruction_text[start:end] = " " * (end - start)
        for clause in turn_slices("".join(instruction_text)):
            candidates = [candidate for candidate, _, _ in _clause_spans(clause.text)]
            candidates.extend(
                clause.text[token.end():].lstrip()
                for token in _UNIT_TOKEN_RE.finditer(clause.text)
                if token.group(0).lower() in connectors
            )
            for candidate in candidates:
                if _FORGET_RE.match(candidate):
                    return True
    return False


def forget_demand_targets(user_text: str) -> frozenset[str]:
    """Expected forget operands from the runtime's canonical request-unit owner."""
    from core.agent_runtime.answer_coverage import _literal_spans, demand_units, turn_slices
    from core.operator_profile_interpretation import _clause_spans, interpret_profile_turn
    from core.operator_profile_turn import _remaining_memory_text
    from core.persistent_memory import _FORGET_RE, _normalize_forget_target
    from core.vool_memory import _normalize_forget_token
    from core.within_turn_retraction import intake_request_text

    command = intake_request_text(str(user_text or ""))
    spans = [(item.source_start, item.source_end) for item in interpret_profile_turn(command)]
    if spans:
        command = _remaining_memory_text(command, spans)
    targets = set()
    for unit in demand_units(command) or turn_slices(command):
        for candidate, _, _ in _clause_spans(unit.text):
            instruction = list(candidate)
            for start, end in _literal_spans(candidate):
                instruction[start:end] = " " * (end - start)
            # The request-unit owner may retain its leading conjunction. Only
            # that connector is skipped; operands remain byte-derived.
            import re
            lead = re.match(r"^(?:(?:and|also|plus|then|but)\s+)*", candidate, re.IGNORECASE).end()
            if _FORGET_RE.match("".join(instruction)[lead:]) is None:
                continue
            match = _FORGET_RE.match(candidate[lead:])
            if match is not None:
                targets.add(_normalize_forget_token(_normalize_forget_target(match.group(1))))
    return frozenset(targets)
