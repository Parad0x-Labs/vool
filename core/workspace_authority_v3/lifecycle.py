"""Pure lifecycle, idempotency, and head-CAS contracts for dormant Phase 0."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from types import FunctionType
from typing import Any, ClassVar

from core.enum_compat import StrEnum
from core.workspace_authority_v3.base import (
    AuthorityRecord,
    ContractValidationError,
    _canonical_enum_value,
    require_digest,
    require_nonnegative,
    require_positive,
    sealed_authority_record_digest,
)
from core.workspace_authority_v3.canonical import canonical_bytes
from core.workspace_authority_v3.identity import (
    AuthorityEpoch,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OperationId,
    OperationIdentityBinding,
    RecoveryCycleId,
    RecoveryDecisionId,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    _canonical_binding_mutation_id,
    _canonical_binding_operation_id,
    _canonical_counter_value,
    _canonical_invocation_fields,
    _canonical_operation_binding_fields,
    _canonical_operation_binding_payload,
    _require_owned_identity_text,
    require_exact_authority_value,
    require_owned_identity,
    seal_canonical_enum,
)


def _canonical_identity_or_none(value: object, expected_type: type) -> str | None:
    """Canonical owner-issued identity text, or ``None`` for a genuinely absent field."""

    if value is None:
        return None
    return _require_owned_identity_text(value, expected_type)


def _same_identity(left: object, right: object, expected_type: type) -> bool:
    """Compare two identities by owner-controlled text, never through ``__eq__``."""

    return _canonical_identity_or_none(left, expected_type) == _require_owned_identity_text(
        right,
        expected_type,
    )


def _same_counter(left: object, right: object, expected_type: type, field_name: str) -> bool:
    """Compare two authority counters by sealed integer value, never through ``__eq__``."""

    return _counter_of(left, expected_type, field_name) == _counter_of(
        right,
        expected_type,
        field_name,
    )


def _counter_of(value: object, expected_type: type, field_name: str) -> int:
    require_exact_authority_value(value, expected_type, field_name)
    return _canonical_counter_value(value)


def _exact_generation(value: object, field_name: str, *, positive: bool) -> int:
    """An exact ``int`` generation, so ordering/equality cannot dispatch to a subclass."""

    if type(value) is not int:
        raise ContractValidationError(f"{field_name} must be an exact non-negative integer")
    return require_positive(value, field_name) if positive else require_nonnegative(value, field_name)


def _enum_value_of(member: object, expected_type: type, field_name: str) -> Any:
    require_exact_authority_value(member, expected_type, field_name)
    return _canonical_enum_value(member)


class WorkspaceLifecycle(StrEnum):
    UNENROLLED = "UNENROLLED"
    CLAIMED_UNREGISTERED = "CLAIMED_UNREGISTERED"
    ACTIVE = "ACTIVE"
    REBIND_REQUIRED = "REBIND_REQUIRED"
    OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"
    OWNED_BY_OTHER_AUTHORITY = "OWNED_BY_OTHER_AUTHORITY"
    OWNED_BY_OTHER_RUNTIME_HOME = "OWNED_BY_OTHER_RUNTIME_HOME"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNSUPPORTED_AUTHORITY = "UNSUPPORTED_AUTHORITY"


seal_canonical_enum(WorkspaceLifecycle)


class MutationLifecycle(StrEnum):
    CLAIMED = "CLAIMED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    PREPARED = "PREPARED"
    APPLYING = "APPLYING"
    APPLIED_NORMAL = "APPLIED_NORMAL"
    ABORTED_NO_EFFECT_PROVEN = "ABORTED_NO_EFFECT_PROVEN"
    ABORTED_COMPENSATED = "ABORTED_COMPENSATED"
    RECOVERED_RUNTIME_PROVEN = "RECOVERED_RUNTIME_PROVEN"
    RECOVERED_EFFECT_CAUSALITY_UNKNOWN = "RECOVERED_EFFECT_CAUSALITY_UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


seal_canonical_enum(MutationLifecycle)


_WORKSPACE_TRANSITIONS: dict[WorkspaceLifecycle, frozenset[WorkspaceLifecycle]] = {
    WorkspaceLifecycle.UNENROLLED: frozenset(
        {WorkspaceLifecycle.CLAIMED_UNREGISTERED, WorkspaceLifecycle.OWNED_BY_OTHER_AUTHORITY, WorkspaceLifecycle.UNSUPPORTED_AUTHORITY}
    ),
    WorkspaceLifecycle.CLAIMED_UNREGISTERED: frozenset(
        {WorkspaceLifecycle.ACTIVE, WorkspaceLifecycle.OWNERSHIP_CONFLICT, WorkspaceLifecycle.RECOVERY_REQUIRED}
    ),
    WorkspaceLifecycle.ACTIVE: frozenset(
        {
            WorkspaceLifecycle.REBIND_REQUIRED,
            WorkspaceLifecycle.OWNERSHIP_CONFLICT,
            WorkspaceLifecycle.OWNED_BY_OTHER_RUNTIME_HOME,
            WorkspaceLifecycle.RECOVERY_REQUIRED,
            WorkspaceLifecycle.UNSUPPORTED_AUTHORITY,
        }
    ),
    WorkspaceLifecycle.REBIND_REQUIRED: frozenset(
        {WorkspaceLifecycle.ACTIVE, WorkspaceLifecycle.OWNERSHIP_CONFLICT, WorkspaceLifecycle.RECOVERY_REQUIRED}
    ),
    WorkspaceLifecycle.OWNERSHIP_CONFLICT: frozenset({WorkspaceLifecycle.REBIND_REQUIRED, WorkspaceLifecycle.RECOVERY_REQUIRED}),
    WorkspaceLifecycle.OWNED_BY_OTHER_AUTHORITY: frozenset({WorkspaceLifecycle.UNENROLLED}),
    WorkspaceLifecycle.OWNED_BY_OTHER_RUNTIME_HOME: frozenset({WorkspaceLifecycle.REBIND_REQUIRED}),
    WorkspaceLifecycle.RECOVERY_REQUIRED: frozenset(),
    WorkspaceLifecycle.UNSUPPORTED_AUTHORITY: frozenset({WorkspaceLifecycle.UNENROLLED}),
}

_MUTATION_TRANSITIONS: dict[MutationLifecycle, frozenset[MutationLifecycle]] = {
    MutationLifecycle.CLAIMED: frozenset(
        {MutationLifecycle.AWAITING_APPROVAL, MutationLifecycle.AUTHORIZED, MutationLifecycle.ABORTED_NO_EFFECT_PROVEN}
    ),
    MutationLifecycle.AWAITING_APPROVAL: frozenset({MutationLifecycle.AUTHORIZED, MutationLifecycle.ABORTED_NO_EFFECT_PROVEN}),
    MutationLifecycle.AUTHORIZED: frozenset({MutationLifecycle.PREPARED, MutationLifecycle.ABORTED_NO_EFFECT_PROVEN}),
    MutationLifecycle.PREPARED: frozenset({MutationLifecycle.APPLYING, MutationLifecycle.ABORTED_NO_EFFECT_PROVEN}),
    MutationLifecycle.APPLYING: frozenset(
        {
            MutationLifecycle.APPLIED_NORMAL,
            MutationLifecycle.ABORTED_COMPENSATED,
            MutationLifecycle.RECOVERED_RUNTIME_PROVEN,
            MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN,
            MutationLifecycle.RECOVERY_REQUIRED,
        }
    ),
    MutationLifecycle.APPLIED_NORMAL: frozenset(),
    MutationLifecycle.ABORTED_NO_EFFECT_PROVEN: frozenset(),
    MutationLifecycle.ABORTED_COMPENSATED: frozenset(),
    MutationLifecycle.RECOVERED_RUNTIME_PROVEN: frozenset(),
    MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN: frozenset({MutationLifecycle.RECOVERY_REQUIRED}),
    MutationLifecycle.RECOVERY_REQUIRED: frozenset(
        {
            MutationLifecycle.ABORTED_NO_EFFECT_PROVEN,
            MutationLifecycle.ABORTED_COMPENSATED,
            MutationLifecycle.RECOVERED_RUNTIME_PROVEN,
            MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN,
        }
    ),
}


def _build_lifecycle_transition_boundary() -> tuple[Any, ...]:
    """Own the transition tables as sealed canonical values, closed to later substitution.

    Membership is decided on the value captured when the enum was sealed, so replacing
    ``__eq__``/``__hash__`` on a lifecycle enum, or rebinding the declarative tables above,
    cannot admit an illegal transition.
    """

    def seal(
        table: dict[Any, frozenset[Any]],
        member_type: type,
        kind: str,
    ) -> dict[str, frozenset[str]]:
        sealed: dict[str, frozenset[str]] = {}
        for current, targets in table.items():
            key = _enum_value_of(current, member_type, f"{kind} lifecycle")
            if not isinstance(key, str):
                raise TypeError(f"{kind} lifecycle values must be canonical strings")
            sealed[key] = frozenset(
                _enum_value_of(target, member_type, f"{kind} lifecycle") for target in targets
            )
        return sealed

    workspace_targets = seal(_WORKSPACE_TRANSITIONS, WorkspaceLifecycle, "workspace")
    mutation_targets = seal(_MUTATION_TRANSITIONS, MutationLifecycle, "mutation")

    def workspace_targets_of(current: object) -> frozenset[str]:
        key = _enum_value_of(current, WorkspaceLifecycle, "workspace lifecycle")
        targets = workspace_targets.get(key)
        if targets is None:
            raise ContractValidationError(f"unknown workspace lifecycle state: {key}")
        return targets

    def validate_workspace(current: object, target: object) -> None:
        target_value = _enum_value_of(target, WorkspaceLifecycle, "workspace lifecycle")
        if target_value not in workspace_targets_of(current):
            current_value = _enum_value_of(current, WorkspaceLifecycle, "workspace lifecycle")
            raise ContractValidationError(
                f"illegal workspace lifecycle transition: {current_value} -> {target_value}"
            )

    def workspace_can_enter_recovery(current: object) -> bool:
        recovery = _enum_value_of(
            WorkspaceLifecycle.RECOVERY_REQUIRED,
            WorkspaceLifecycle,
            "workspace lifecycle",
        )
        try:
            targets = workspace_targets_of(current)
        except ContractValidationError:
            return False
        return recovery in targets

    def validate_mutation(current: object, target: object) -> None:
        current_value = _enum_value_of(current, MutationLifecycle, "mutation lifecycle")
        target_value = _enum_value_of(target, MutationLifecycle, "mutation lifecycle")
        targets = mutation_targets.get(current_value)
        if targets is None:
            raise ContractValidationError(f"unknown mutation lifecycle state: {current_value}")
        if target_value not in targets:
            raise ContractValidationError(
                f"illegal mutation lifecycle transition: {current_value} -> {target_value}"
            )

    return validate_workspace, workspace_can_enter_recovery, validate_mutation


(
    _validate_workspace_transition,
    _workspace_can_enter_recovery,
    _validate_mutation_transition,
) = _build_lifecycle_transition_boundary()


def validate_workspace_transition(current: WorkspaceLifecycle, target: WorkspaceLifecycle) -> None:
    _validate_workspace_transition(current, target)


def validate_mutation_transition(current: MutationLifecycle, target: MutationLifecycle) -> None:
    _validate_mutation_transition(current, target)


@dataclass(frozen=True, slots=True)
class DormantOperation:
    operation_id: OperationId
    mutation_id: MutationId
    identity_binding: OperationIdentityBinding
    plan_digest: str

    def __post_init__(self) -> None:
        require_owned_identity(self.operation_id, OperationId)
        require_owned_identity(self.mutation_id, MutationId)
        if type(self.identity_binding) is not OperationIdentityBinding:
            raise TypeError("dormant operations require an exact identity binding")
        OperationIdentityBinding.__post_init__(self.identity_binding)
        require_digest(self.plan_digest, "plan_digest")
        if not _same_identity(
            self.operation_id,
            _canonical_binding_operation_id(self.identity_binding),
            OperationId,
        ):
            raise ContractValidationError("operation_id does not match its canonical identity binding")
        if not _same_identity(
            self.mutation_id,
            _canonical_binding_mutation_id(self.identity_binding),
            MutationId,
        ):
            raise ContractValidationError("mutation_id does not match its stable invocation owner")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DormantOperation is final")


@dataclass(frozen=True, slots=True)
class OperationRegistration:
    operation: DormantOperation
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.operation) is not DormantOperation or not isinstance(self.replayed, bool):
            raise TypeError("operation registration requires an exact dormant operation and replay flag")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("OperationRegistration is final")


class IdempotencyConflictError(ContractValidationError):
    pass


class RecoveryDecisionResult(StrEnum):
    REACTIVATE = "REACTIVATE"
    REQUIRE_REBIND = "REQUIRE_REBIND"


seal_canonical_enum(RecoveryDecisionResult)


@dataclass(frozen=True, slots=True)
class RecoveryDecision(AuthorityRecord):
    """A state-bound recovery conclusion; persistence is proven by a separate capability."""

    RECORD_TYPE: ClassVar[str] = "RecoveryDecision"

    recovery_decision_id: RecoveryDecisionId
    recovery_cycle_id: RecoveryCycleId
    recovery_generation: int
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    expected_head_revision: HeadRevision
    prior_state: WorkspaceLifecycle
    result: RecoveryDecisionResult
    evidence_digest: str

    def __post_init__(self) -> None:
        typed = (
            (self.recovery_decision_id, RecoveryDecisionId, "recovery_decision_id"),
            (self.recovery_cycle_id, RecoveryCycleId, "recovery_cycle_id"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.expected_head_revision, HeadRevision, "expected_head_revision"),
        )
        for value, expected_type, field_name in typed:
            require_exact_authority_value(value, expected_type, field_name)
        if type(self.recovery_generation) is not int:
            raise TypeError("recovery_generation must be an exact integer")
        if self.recovery_generation <= 0:
            raise ContractValidationError("recovery_generation must be positive")
        if type(self.prior_state) is not WorkspaceLifecycle or self.prior_state is WorkspaceLifecycle.RECOVERY_REQUIRED:
            raise ContractValidationError("recovery decision must bind the lifecycle that entered recovery")
        if type(self.result) is not RecoveryDecisionResult:
            raise TypeError("recovery decision result must be an exact RecoveryDecisionResult")
        require_digest(self.evidence_digest, "evidence_digest")


@dataclass(frozen=True, slots=True)
class PersistedRecoveryDecision:
    """Passive durable-write result; application trusts database state, never this value."""

    decision: RecoveryDecision
    record_digest: str

    def __post_init__(self) -> None:
        if type(self.decision) is not RecoveryDecision:
            raise TypeError("persisted decision result requires exact RecoveryDecision")
        require_digest(self.record_digest, "record_digest")
        if sealed_authority_record_digest(self.decision) != self.record_digest:
            raise ContractValidationError("persisted recovery decision digest mismatch")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("PersistedRecoveryDecision is final")


class DormantOperationRegistry:
    """Pure replay model. It owns no filesystem effect and issues no execution authority."""

    execution_authority = False

    def __init__(self) -> None:
        self._operations: dict[str, tuple[DormantOperation, bytes]] = {}
        self._lock = threading.Lock()

    def register(self, binding: OperationIdentityBinding, plan_digest: str) -> OperationRegistration:
        """Replay or reject by canonical fingerprint; never by caller-replaceable equality."""

        if type(binding) is not OperationIdentityBinding:
            raise TypeError("operation registration requires an exact identity binding")
        OperationIdentityBinding.__post_init__(binding)
        require_digest(plan_digest, "plan_digest")
        (
            invocation,
            bound_workspace_id,
            bound_authority_epoch,
            bound_action_digest,
        ) = _canonical_operation_binding_fields(binding)
        owned_binding = OperationIdentityBinding(
            invocation_identity=InvocationIdentity(*_canonical_invocation_fields(invocation)),
            workspace_id=bound_workspace_id,
            authority_epoch=AuthorityEpoch(_canonical_counter_value(bound_authority_epoch)),
            canonical_action_digest=bound_action_digest,
        )
        candidate = DormantOperation(
            operation_id=_canonical_binding_operation_id(owned_binding),
            mutation_id=_canonical_binding_mutation_id(owned_binding),
            identity_binding=owned_binding,
            plan_digest=plan_digest,
        )
        candidate_fingerprint = _dormant_operation_fingerprint(candidate)
        # The key is the canonical identity text, so replay lookup cannot be redirected by
        # a replaced ``__hash__``/``__eq__`` on the identity type.
        key = _require_owned_identity_text(candidate.operation_id, OperationId)
        with self._lock:
            registered = self._operations.get(key)
            if registered is not None:
                existing, fingerprint = registered
                if _dormant_operation_fingerprint(existing) != fingerprint:
                    raise ContractValidationError("registered dormant operation changed after ownership")
                if fingerprint != candidate_fingerprint:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return OperationRegistration(existing, replayed=True)
            self._operations[key] = (candidate, candidate_fingerprint)
            return OperationRegistration(candidate, replayed=False)


def _dormant_operation_fingerprint(operation: DormantOperation) -> bytes:
    if type(operation) is not DormantOperation:
        raise TypeError("operation fingerprint requires exact DormantOperation")
    DormantOperation.__post_init__(operation)
    return canonical_bytes(
        {
            "identity_binding": _canonical_operation_binding_payload(operation.identity_binding),
            "mutation_id": _require_owned_identity_text(operation.mutation_id, MutationId),
            "operation_id": _require_owned_identity_text(operation.operation_id, OperationId),
            "plan_digest": operation.plan_digest,
        }
    )


class CasConflictError(ContractValidationError):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryCycleBinding:
    recovery_cycle_id: RecoveryCycleId
    generation: int
    expected_recovery_decision_id: RecoveryDecisionId
    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    expected_head_revision: HeadRevision
    entered_from: WorkspaceLifecycle

    def __post_init__(self) -> None:
        typed_values = (
            (self.recovery_cycle_id, RecoveryCycleId, "recovery_cycle_id"),
            (self.expected_recovery_decision_id, RecoveryDecisionId, "expected_recovery_decision_id"),
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.expected_head_revision, HeadRevision, "expected_head_revision"),
            (self.entered_from, WorkspaceLifecycle, "entered_from"),
        )
        for value, expected_type, field_name in typed_values:
            require_exact_authority_value(value, expected_type, field_name)
        _exact_generation(self.generation, "generation", positive=True)
        if not _workspace_can_enter_recovery(self.entered_from):
            raise ContractValidationError("recovery cycle entered_from cannot transition to recovery")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RecoveryCycleBinding is final")


@dataclass(frozen=True, slots=True)
class HeadState:
    """Passive stored/read-only head representation; it grants no transition authority."""

    workspace_id: WorkspaceId
    incarnation_id: IncarnationId
    authority_epoch: AuthorityEpoch
    head_revision: HeadRevision
    workspace_sequence: WorkspaceSequence
    runtime_home_owner: RuntimeHomeId
    lifecycle: WorkspaceLifecycle
    pending_operation_id: OperationId | None = None
    pending_mutation_id: MutationId | None = None
    recovery_generation: int = 0
    recovery_cycle: RecoveryCycleBinding | None = None

    def __post_init__(self) -> None:
        typed_values = (
            (self.workspace_id, WorkspaceId, "workspace_id"),
            (self.incarnation_id, IncarnationId, "incarnation_id"),
            (self.authority_epoch, AuthorityEpoch, "authority_epoch"),
            (self.head_revision, HeadRevision, "head_revision"),
            (self.workspace_sequence, WorkspaceSequence, "workspace_sequence"),
            (self.runtime_home_owner, RuntimeHomeId, "runtime_home_owner"),
            (self.lifecycle, WorkspaceLifecycle, "lifecycle"),
        )
        for value, expected_type, field_name in typed_values:
            require_exact_authority_value(value, expected_type, field_name)
        if self.pending_operation_id is not None:
            require_owned_identity(self.pending_operation_id, OperationId)
        if self.pending_mutation_id is not None:
            require_owned_identity(self.pending_mutation_id, MutationId)
        if (self.pending_operation_id is None) != (self.pending_mutation_id is None):
            raise ContractValidationError("pending operation and mutation identities must be present or absent together")
        if self.pending_operation_id is not None and self.lifecycle is not WorkspaceLifecycle.ACTIVE:
            raise ContractValidationError("only an ACTIVE workspace may contain a pending mutation")
        _exact_generation(self.recovery_generation, "recovery_generation", positive=False)
        if self.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED:
            if type(self.recovery_cycle) is not RecoveryCycleBinding:
                raise ContractValidationError("RECOVERY_REQUIRED must own an exact recovery cycle")
            cycle = self.recovery_cycle
            RecoveryCycleBinding.__post_init__(cycle)
            if (
                cycle.generation != self.recovery_generation
                or not _same_identity(cycle.workspace_id, self.workspace_id, WorkspaceId)
                or not _same_identity(cycle.incarnation_id, self.incarnation_id, IncarnationId)
                or not _same_counter(
                    cycle.authority_epoch,
                    self.authority_epoch,
                    AuthorityEpoch,
                    "authority_epoch",
                )
                or not _same_counter(
                    cycle.expected_head_revision,
                    self.head_revision,
                    HeadRevision,
                    "head_revision",
                )
            ):
                raise ContractValidationError("recovery cycle does not bind the exact head state")
            if self.pending_operation_id is not None:
                raise ContractValidationError("a recovery head cannot retain a pending mutation")
        elif self.recovery_cycle is not None:
            raise ContractValidationError("only RECOVERY_REQUIRED may own an open recovery cycle")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("HeadState is a final read-only representation")

    @property
    def healthy(self) -> bool:
        return self.lifecycle is WorkspaceLifecycle.ACTIVE


def _head_state_fingerprint(snapshot: HeadState) -> bytes:
    if type(snapshot) is not HeadState:
        raise TypeError("head fingerprint requires an exact HeadState")
    HeadState.__post_init__(snapshot)
    cycle = snapshot.recovery_cycle
    cycle_payload = None
    if cycle is not None:
        RecoveryCycleBinding.__post_init__(cycle)
        cycle_payload = {
            "authority_epoch": _canonical_counter_value(cycle.authority_epoch),
            "entered_from": _canonical_enum_value(cycle.entered_from),
            "expected_head_revision": _canonical_counter_value(cycle.expected_head_revision),
            "expected_recovery_decision_id": _require_owned_identity_text(
                cycle.expected_recovery_decision_id,
                RecoveryDecisionId,
            ),
            "generation": cycle.generation,
            "incarnation_id": _require_owned_identity_text(cycle.incarnation_id, IncarnationId),
            "recovery_cycle_id": _require_owned_identity_text(cycle.recovery_cycle_id, RecoveryCycleId),
            "workspace_id": _require_owned_identity_text(cycle.workspace_id, WorkspaceId),
        }
    return canonical_bytes(
        {
            "authority_epoch": _canonical_counter_value(snapshot.authority_epoch),
            "head_revision": _canonical_counter_value(snapshot.head_revision),
            "incarnation_id": _require_owned_identity_text(snapshot.incarnation_id, IncarnationId),
            "lifecycle": _canonical_enum_value(snapshot.lifecycle),
            "pending_mutation_id": (
                None
                if snapshot.pending_mutation_id is None
                else _require_owned_identity_text(snapshot.pending_mutation_id, MutationId)
            ),
            "pending_operation_id": (
                None
                if snapshot.pending_operation_id is None
                else _require_owned_identity_text(snapshot.pending_operation_id, OperationId)
            ),
            "recovery_cycle": cycle_payload,
            "recovery_generation": snapshot.recovery_generation,
            "runtime_home_owner": _require_owned_identity_text(snapshot.runtime_home_owner, RuntimeHomeId),
            "workspace_id": _require_owned_identity_text(snapshot.workspace_id, WorkspaceId),
            "workspace_sequence": _canonical_counter_value(snapshot.workspace_sequence),
        }
    )


class OwnedHeadState:
    """Closed transition capability minted by one DormantWorkspaceStateOwner.

    The properties below are a caller-facing read view.  They are never the base of an
    authority-bearing transition: every transition re-derives its next state from the
    snapshot the owner authenticated under its own lock (see
    ``_build_workspace_state_boundary``), so replacing a property here changes what a
    caller reads and nothing the owner accepts.
    """

    __slots__ = ("_fingerprint", "_owner", "_snapshot")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("OwnedHeadState is minted only by DormantWorkspaceStateOwner")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("OwnedHeadState is final")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("owned head states are immutable")

    @property
    def snapshot(self) -> HeadState:
        return self._snapshot

    @property
    def workspace_id(self) -> WorkspaceId:
        return self._snapshot.workspace_id

    @property
    def incarnation_id(self) -> IncarnationId:
        return self._snapshot.incarnation_id

    @property
    def authority_epoch(self) -> AuthorityEpoch:
        return self._snapshot.authority_epoch

    @property
    def head_revision(self) -> HeadRevision:
        return self._snapshot.head_revision

    @property
    def workspace_sequence(self) -> WorkspaceSequence:
        return self._snapshot.workspace_sequence

    @property
    def runtime_home_owner(self) -> RuntimeHomeId:
        return self._snapshot.runtime_home_owner

    @property
    def lifecycle(self) -> WorkspaceLifecycle:
        return self._snapshot.lifecycle

    @property
    def pending_operation_id(self) -> OperationId | None:
        return self._snapshot.pending_operation_id

    @property
    def pending_mutation_id(self) -> MutationId | None:
        return self._snapshot.pending_mutation_id

    @property
    def recovery_generation(self) -> int:
        return self._snapshot.recovery_generation

    @property
    def recovery_cycle(self) -> RecoveryCycleBinding | None:
        return self._snapshot.recovery_cycle

    @property
    def healthy(self) -> bool:
        return self._snapshot.healthy


def _build_authority_commit_boundary() -> tuple[Any, ...]:
    """Own the durable recovery commit this lifecycle boundary is defined over.

    The storage module seals its own operation as the authority database type is created,
    which is the last moment at which nothing else can have touched it.  The transition
    then calls the captured function object and never re-reads a class entry, so neither
    instance shadowing nor class-level assignment afterwards can redirect it.
    """

    sealed: dict[str, Any] = {}

    def seal_recovery_commit(database_type: object) -> None:
        if sealed:
            raise TypeError("the authority database recovery commit is already sealed")
        if not isinstance(database_type, type):
            raise TypeError("sealing the recovery commit requires the authority database type")
        commit = database_type.__dict__.get("commit_recovery_decision")
        if type(commit) is not FunctionType:
            raise TypeError("the authority database recovery commit is not the contract operation")
        sealed["database_type"] = database_type
        sealed["commit"] = commit

    def trusted_recovery_commit() -> tuple[type, FunctionType]:
        if not sealed:
            raise TypeError("the authority database recovery commit was never sealed")
        return sealed["database_type"], sealed["commit"]

    return seal_recovery_commit, trusted_recovery_commit


(
    seal_authority_recovery_commit,
    _trusted_authority_recovery_commit,
) = _build_authority_commit_boundary()


def _build_workspace_state_boundary() -> tuple[object, ...]:
    # Types and derivations trusted by this boundary are captured now, at module build
    # time, so a later rebinding of the module-level name cannot change what is
    # constructed, accepted, or fingerprinted here.
    head_state_type = HeadState
    workspace_lifecycle_type = WorkspaceLifecycle
    owned_head_state_type = OwnedHeadState
    recovery_cycle_type = RecoveryCycleBinding
    recovery_decision_type = RecoveryDecision
    recovery_result_type = RecoveryDecisionResult
    head_state_fingerprint = _head_state_fingerprint
    record_digest_of = sealed_authority_record_digest

    class _WorkspaceOwnerState:
        __slots__ = (
            "current",
            "current_fingerprint",
            "current_snapshot",
            "lock",
            "next_recovery_generation",
            "owner",
        )

        def __init__(self, owner: DormantWorkspaceStateOwner) -> None:
            self.current: OwnedHeadState | None = None
            self.current_fingerprint = b""
            self.current_snapshot: HeadState | None = None
            self.lock = threading.RLock()
            self.next_recovery_generation = 0
            self.owner = owner

    owners: dict[str, DormantWorkspaceStateOwner] = {}
    owner_states: dict[int, _WorkspaceOwnerState] = {}
    decisions: dict[tuple[int, str], tuple[RecoveryDecision, str]] = {}
    # One serialization point for the shared registries.  Enrollment reads and publishes
    # under it, so concurrent issuance for one workspace resolves to exactly one accepted
    # owner and leaves no partially published state behind.
    registry_lock = threading.RLock()

    def require_owner_state(owner: object) -> _WorkspaceOwnerState:
        if type(owner) is not DormantWorkspaceStateOwner:
            raise TypeError("lifecycle state requires an exact workspace owner")
        with registry_lock:
            owner_state = owner_states.get(id(owner))
            if owner_state is None or owner_state.owner is not owner:
                raise TypeError("workspace lifecycle owner was not issued from enrollment provenance")
            snapshot = owner_state.current_snapshot
            if snapshot is None or owners.get(
                _require_owned_identity_text(snapshot.workspace_id, WorkspaceId)
            ) is not owner:
                raise TypeError("workspace lifecycle owner was not issued from enrollment provenance")
            return owner_state

    def mint_current_state(
        owner_state: _WorkspaceOwnerState,
        snapshot: HeadState,
    ) -> OwnedHeadState:
        if type(snapshot) is not head_state_type:
            raise TypeError("authoritative head state requires an exact HeadState snapshot")
        fingerprint = head_state_fingerprint(snapshot)
        state = object.__new__(owned_head_state_type)
        object.__setattr__(state, "_owner", owner_state.owner)
        object.__setattr__(state, "_snapshot", snapshot)
        object.__setattr__(state, "_fingerprint", fingerprint)
        owner_state.current_snapshot = snapshot
        owner_state.current_fingerprint = fingerprint
        owner_state.current = state
        return state

    def authenticate_current_locked(
        owner_state: _WorkspaceOwnerState,
        state: object,
    ) -> tuple[OwnedHeadState, HeadState]:
        """Return the current capability together with the snapshot it authenticates.

        The second element is the owner's own stored snapshot, which is the only base a
        transition may derive its next state from.  Callers must not re-read the public
        ``OwnedHeadState`` property layer afterwards.
        """

        current = owner_state.current
        snapshot = owner_state.current_snapshot
        if (
            current is None
            or snapshot is None
            or type(state) is not owned_head_state_type
            or state._owner is not owner_state.owner
            or current is not state
            or state._snapshot is not snapshot
            or state._fingerprint != owner_state.current_fingerprint
            or head_state_fingerprint(snapshot) != owner_state.current_fingerprint
        ):
            raise CasConflictError("head state is not the current value owned by this lifecycle owner")
        return state, snapshot

    def require_current_locked(
        owner_state: _WorkspaceOwnerState,
        state: object,
    ) -> OwnedHeadState:
        return authenticate_current_locked(owner_state, state)[0]

    def derive_head_state(snapshot: HeadState, **changes: object) -> HeadState:
        """Build the next head state explicitly from the authenticated snapshot's fields."""

        if type(snapshot) is not head_state_type:
            raise TypeError("head derivation requires the exact authenticated snapshot")
        fields: dict[str, object] = {
            "workspace_id": snapshot.workspace_id,
            "incarnation_id": snapshot.incarnation_id,
            "authority_epoch": snapshot.authority_epoch,
            "head_revision": snapshot.head_revision,
            "workspace_sequence": snapshot.workspace_sequence,
            "runtime_home_owner": snapshot.runtime_home_owner,
            "lifecycle": snapshot.lifecycle,
            "pending_operation_id": snapshot.pending_operation_id,
            "pending_mutation_id": snapshot.pending_mutation_id,
            "recovery_generation": snapshot.recovery_generation,
            "recovery_cycle": snapshot.recovery_cycle,
        }
        unknown = sorted(set(changes) - set(fields))
        if unknown:
            raise TypeError(f"unknown head state field(s): {unknown}")
        fields.update(changes)
        return head_state_type(**fields)  # type: ignore[arg-type]

    def require_open_recovery_cycle(snapshot: HeadState) -> RecoveryCycleBinding:
        cycle = snapshot.recovery_cycle
        if snapshot.lifecycle is not workspace_lifecycle_type.RECOVERY_REQUIRED or cycle is None:
            raise CasConflictError("recovery decision is replayed or workspace is not awaiting recovery")
        if type(cycle) is not recovery_cycle_type:
            raise CasConflictError("recovery decision is replayed or workspace is not awaiting recovery")
        return cycle

    def require_decision_locked(
        owner_state: _WorkspaceOwnerState,
        snapshot: HeadState,
        decision: RecoveryDecision,
    ) -> RecoveryCycleBinding:
        cycle = require_open_recovery_cycle(snapshot)
        if type(decision) is not recovery_decision_type:
            raise TypeError("recovery decision must be an exact owner-issued RecoveryDecision")
        if (
            not _same_identity(
                decision.recovery_decision_id,
                cycle.expected_recovery_decision_id,
                RecoveryDecisionId,
            )
            or not _same_identity(
                decision.recovery_cycle_id,
                cycle.recovery_cycle_id,
                RecoveryCycleId,
            )
            or _exact_generation(decision.recovery_generation, "recovery_generation", positive=True)
            != _exact_generation(cycle.generation, "generation", positive=True)
            or not _same_identity(decision.workspace_id, cycle.workspace_id, WorkspaceId)
            or not _same_identity(decision.incarnation_id, cycle.incarnation_id, IncarnationId)
            or not _same_counter(
                decision.authority_epoch,
                cycle.authority_epoch,
                AuthorityEpoch,
                "authority_epoch",
            )
            or not _same_counter(
                decision.expected_head_revision,
                cycle.expected_head_revision,
                HeadRevision,
                "expected_head_revision",
            )
            or decision.prior_state is not cycle.entered_from
        ):
            raise CasConflictError("recovery decision does not bind the exact open recovery cycle")
        with registry_lock:
            issued = decisions.get(
                (
                    id(owner_state.owner),
                    _require_owned_identity_text(cycle.recovery_cycle_id, RecoveryCycleId),
                )
            )
        if (
            issued is None
            or issued[0] is not decision
            or record_digest_of(decision) != issued[1]
        ):
            raise CasConflictError("recovery decision was not issued by this state owner")
        return cycle

    def issue_owner(
        owner: DormantWorkspaceStateOwner,
        *,
        enrollment: WorkspaceEnrollmentProvenance,
        incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
        head_revision: HeadRevision,
        workspace_sequence: WorkspaceSequence,
        runtime_home_owner: RuntimeHomeId,
    ) -> DormantWorkspaceStateOwner:
        if type(owner) is not DormantWorkspaceStateOwner:
            raise TypeError("workspace state owner issuance requires the exact owner type")
        workspace_id = WorkspaceId.from_enrollment(enrollment)
        owner_key = _require_owned_identity_text(workspace_id, WorkspaceId)
        initial = head_state_type(
            workspace_id=workspace_id,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
            head_revision=head_revision,
            workspace_sequence=workspace_sequence,
            runtime_home_owner=runtime_home_owner,
            lifecycle=workspace_lifecycle_type.ACTIVE,
        )
        # Everything above is private to this call; nothing is published until the whole
        # check-and-publish runs as one atomic region below.
        owner_state = _WorkspaceOwnerState(owner)
        mint_current_state(owner_state, initial)
        with registry_lock:
            if owner_key in owners or id(owner) in owner_states:
                raise CasConflictError("workspace already has a lifecycle state owner")
            owner_states[id(owner)] = owner_state
            owners[owner_key] = owner
        return owner

    def current(owner: DormantWorkspaceStateOwner) -> OwnedHeadState:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            if owner_state.current is None:
                raise CasConflictError("workspace lifecycle owner has no current head state")
            return owner_state.current

    def require_current(
        owner: DormantWorkspaceStateOwner,
        state: object,
    ) -> OwnedHeadState:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            return require_current_locked(owner_state, state)

    def claim_pending(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
        expectation: HeadExpectation,
        operation_id: OperationId,
        mutation_id: MutationId,
    ) -> OwnedHeadState:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            _current, snapshot = authenticate_current_locked(owner_state, state)
            if type(expectation) is not HeadExpectation:
                raise TypeError("head claim requires an exact HeadExpectation")
            HeadExpectation.__post_init__(expectation)
            require_owned_identity(operation_id, OperationId)
            require_owned_identity(mutation_id, MutationId)
            if snapshot.lifecycle is not workspace_lifecycle_type.ACTIVE:
                raise CasConflictError("workspace is not healthy")
            if not _same_counter(
                snapshot.authority_epoch,
                expectation.authority_epoch,
                AuthorityEpoch,
                "authority_epoch",
            ):
                raise CasConflictError("authority epoch mismatch")
            if not _same_counter(
                snapshot.head_revision,
                expectation.head_revision,
                HeadRevision,
                "head_revision",
            ):
                raise CasConflictError("head revision mismatch")
            if not _same_identity(
                snapshot.runtime_home_owner,
                expectation.runtime_home_owner,
                RuntimeHomeId,
            ):
                raise CasConflictError("runtime-home owner mismatch")
            if snapshot.pending_operation_id is not None or snapshot.pending_mutation_id is not None:
                raise CasConflictError("a pending operation or mutation already exists")
            next_snapshot = derive_head_state(
                snapshot,
                pending_operation_id=operation_id,
                pending_mutation_id=mutation_id,
            )
            return mint_current_state(owner_state, next_snapshot)

    def advance_head(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
        *,
        operation_id: OperationId,
        mutation_id: MutationId,
        new_head_revision: HeadRevision,
        new_workspace_sequence: WorkspaceSequence,
    ) -> OwnedHeadState:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            _current, snapshot = authenticate_current_locked(owner_state, state)
            require_owned_identity(operation_id, OperationId)
            require_owned_identity(mutation_id, MutationId)
            next_revision = _counter_of(new_head_revision, HeadRevision, "new_head_revision")
            next_sequence = _counter_of(
                new_workspace_sequence,
                WorkspaceSequence,
                "new_workspace_sequence",
            )
            if snapshot.lifecycle is not workspace_lifecycle_type.ACTIVE:
                raise CasConflictError("workspace is not healthy")
            if not _same_identity(
                snapshot.pending_operation_id,
                operation_id,
                OperationId,
            ) or not _same_identity(snapshot.pending_mutation_id, mutation_id, MutationId):
                raise CasConflictError("pending identity mismatch")
            if next_revision <= _counter_of(snapshot.head_revision, HeadRevision, "head_revision"):
                raise CasConflictError("head revision must advance")
            if next_sequence <= _counter_of(
                snapshot.workspace_sequence,
                WorkspaceSequence,
                "workspace_sequence",
            ):
                raise CasConflictError("workspace sequence must advance")
            next_snapshot = derive_head_state(
                snapshot,
                head_revision=new_head_revision,
                workspace_sequence=new_workspace_sequence,
                pending_operation_id=None,
                pending_mutation_id=None,
            )
            return mint_current_state(owner_state, next_snapshot)

    def enter_recovery(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
    ) -> OwnedHeadState:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            _current, snapshot = authenticate_current_locked(owner_state, state)
            _validate_workspace_transition(
                snapshot.lifecycle,
                workspace_lifecycle_type.RECOVERY_REQUIRED,
            )
            generation = (
                _exact_generation(
                    owner_state.next_recovery_generation,
                    "recovery_generation",
                    positive=False,
                )
                + 1
            )
            cycle = recovery_cycle_type(
                recovery_cycle_id=RecoveryCycleId.new(),
                generation=generation,
                expected_recovery_decision_id=RecoveryDecisionId.new(),
                workspace_id=snapshot.workspace_id,
                incarnation_id=snapshot.incarnation_id,
                authority_epoch=snapshot.authority_epoch,
                expected_head_revision=snapshot.head_revision,
                entered_from=snapshot.lifecycle,
            )
            next_snapshot = derive_head_state(
                snapshot,
                lifecycle=workspace_lifecycle_type.RECOVERY_REQUIRED,
                pending_operation_id=None,
                pending_mutation_id=None,
                recovery_generation=generation,
                recovery_cycle=cycle,
            )
            owner_state.next_recovery_generation = generation
            return mint_current_state(owner_state, next_snapshot)

    def issue_decision(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
        *,
        result: RecoveryDecisionResult,
        evidence_digest: str,
    ) -> RecoveryDecision:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            _current, snapshot = authenticate_current_locked(owner_state, state)
            if (
                snapshot.lifecycle is not workspace_lifecycle_type.RECOVERY_REQUIRED
                or snapshot.recovery_cycle is None
                or type(snapshot.recovery_cycle) is not recovery_cycle_type
            ):
                raise CasConflictError("workspace does not own an open recovery cycle")
            if type(result) is not recovery_result_type:
                raise TypeError("recovery result must be an exact RecoveryDecisionResult")
            require_digest(evidence_digest, "evidence_digest")
            cycle = snapshot.recovery_cycle
            candidate = recovery_decision_type(
                recovery_decision_id=cycle.expected_recovery_decision_id,
                recovery_cycle_id=cycle.recovery_cycle_id,
                recovery_generation=cycle.generation,
                workspace_id=snapshot.workspace_id,
                incarnation_id=snapshot.incarnation_id,
                authority_epoch=snapshot.authority_epoch,
                expected_head_revision=snapshot.head_revision,
                prior_state=cycle.entered_from,
                result=result,
                evidence_digest=evidence_digest,
            )
            key = (id(owner), _require_owned_identity_text(cycle.recovery_cycle_id, RecoveryCycleId))
            digest = record_digest_of(candidate)
            with registry_lock:
                existing = decisions.get(key)
                if existing is not None:
                    if existing[1] != digest or record_digest_of(existing[0]) != existing[1]:
                        raise CasConflictError("recovery cycle already owns a different decision")
                    return existing[0]
                decisions[key] = (candidate, digest)
            return candidate

    def require_decision(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
        decision: RecoveryDecision,
    ) -> RecoveryCycleBinding:
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            _current, snapshot = authenticate_current_locked(owner_state, state)
            return require_decision_locked(owner_state, snapshot, decision)

    def apply_persisted_decision(
        owner: DormantWorkspaceStateOwner,
        state: OwnedHeadState,
        database: object,
        decision: RecoveryDecision,
    ) -> OwnedHeadState:
        # Importing the storage module is what seals its commit, so this import runs
        # before the sealed pair is requested.
        from core.workspace_authority_v3.database import (
            TransactionOutcome,
            TransactionResult,
        )

        # The database type and its commit are the objects captured when the type was
        # created.  Nothing is read off a class or an instance here, so neither instance
        # shadowing nor class-level assignment can redirect this transition.
        database_type, commit = _trusted_authority_recovery_commit()
        if type(database) is not database_type:
            raise TypeError("recovery application requires the exact authority database owner")
        committed_outcomes = frozenset(
            _enum_value_of(outcome, TransactionOutcome, "transaction outcome")
            for outcome in (TransactionOutcome.COMMITTED_NEW, TransactionOutcome.COMMITTED_REPLAY)
        )
        owner_state = require_owner_state(owner)
        with owner_state.lock:
            current_state, snapshot = authenticate_current_locked(owner_state, state)
            require_decision_locked(owner_state, snapshot, decision)
            # The durable transition is fixed by the authenticated decision, so a commit
            # result can consume the cycle but cannot choose a different next lifecycle.
            expected_lifecycle = (
                workspace_lifecycle_type.ACTIVE
                if decision.result is recovery_result_type.REACTIVATE
                else workspace_lifecycle_type.REBIND_REQUIRED
            )
            consumed = commit(database, current_state, decision)
            # The lifecycle arm below is defence in depth: while the trusted binding above
            # holds, the commit result cannot be attacker-chosen, so no test can kill this
            # arm on its own.  It states the contract the derivation already relies on.
            if (
                type(consumed) is not TransactionResult
                or _enum_value_of(consumed.outcome, TransactionOutcome, "transaction outcome")
                not in committed_outcomes
                or consumed.value is not expected_lifecycle
            ):
                raise CasConflictError(
                    "recovery decision was not atomically consumed for this exact cycle"
                )
            _current, snapshot = authenticate_current_locked(owner_state, current_state)
            require_decision_locked(owner_state, snapshot, decision)
            next_snapshot = derive_head_state(
                snapshot,
                lifecycle=expected_lifecycle,
                recovery_cycle=None,
            )
            return mint_current_state(owner_state, next_snapshot)

    return (
        issue_owner,
        current,
        require_current,
        claim_pending,
        advance_head,
        enter_recovery,
        issue_decision,
        require_decision,
        apply_persisted_decision,
    )


(
    _issue_workspace_state_owner,
    _current_workspace_state,
    _require_current_workspace_state,
    _claim_workspace_pending,
    _advance_workspace_head,
    _enter_workspace_recovery,
    _issue_workspace_recovery_decision,
    _require_workspace_recovery_decision,
    _apply_persisted_workspace_recovery_decision,
) = _build_workspace_state_boundary()


class DormantWorkspaceStateOwner:
    """Linearizable owner for dormant head transitions and recovery-cycle issuance."""

    __slots__ = ()
    execution_authority = False

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("DormantWorkspaceStateOwner must be issued from enrollment provenance")

    @classmethod
    def from_enrollment(
        cls,
        *,
        enrollment: WorkspaceEnrollmentProvenance,
        incarnation_id: IncarnationId,
        authority_epoch: AuthorityEpoch,
        head_revision: HeadRevision,
        workspace_sequence: WorkspaceSequence,
        runtime_home_owner: RuntimeHomeId,
    ) -> DormantWorkspaceStateOwner:
        if cls is not DormantWorkspaceStateOwner:
            raise TypeError("DormantWorkspaceStateOwner is final")
        owner = object.__new__(cls)
        return _issue_workspace_state_owner(
            owner,
            enrollment=enrollment,
            incarnation_id=incarnation_id,
            authority_epoch=authority_epoch,
            head_revision=head_revision,
            workspace_sequence=workspace_sequence,
            runtime_home_owner=runtime_home_owner,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("DormantWorkspaceStateOwner is final")

    @property
    def current(self) -> OwnedHeadState:
        return _current_workspace_state(self)

    def _require_current(self, state: object) -> OwnedHeadState:
        return _require_current_workspace_state(self, state)

    def claim_pending(
        self,
        state: OwnedHeadState,
        expectation: HeadExpectation,
        operation_id: OperationId,
        mutation_id: MutationId,
    ) -> OwnedHeadState:
        return _claim_workspace_pending(
            self,
            state,
            expectation,
            operation_id,
            mutation_id,
        )

    def advance_head(
        self,
        state: OwnedHeadState,
        *,
        operation_id: OperationId,
        mutation_id: MutationId,
        new_head_revision: HeadRevision,
        new_workspace_sequence: WorkspaceSequence,
    ) -> OwnedHeadState:
        return _advance_workspace_head(
            self,
            state,
            operation_id=operation_id,
            mutation_id=mutation_id,
            new_head_revision=new_head_revision,
            new_workspace_sequence=new_workspace_sequence,
        )

    def enter_recovery(self, state: OwnedHeadState) -> OwnedHeadState:
        return _enter_workspace_recovery(self, state)

    def issue_recovery_decision(
        self,
        state: OwnedHeadState,
        *,
        result: RecoveryDecisionResult,
        evidence_digest: str,
    ) -> RecoveryDecision:
        return _issue_workspace_recovery_decision(
            self,
            state,
            result=result,
            evidence_digest=evidence_digest,
        )

    def _require_issued_decision(
        self,
        state: OwnedHeadState,
        decision: RecoveryDecision,
    ) -> RecoveryCycleBinding:
        return _require_workspace_recovery_decision(self, state, decision)


@dataclass(frozen=True, slots=True)
class HeadExpectation:
    authority_epoch: AuthorityEpoch
    head_revision: HeadRevision
    runtime_home_owner: RuntimeHomeId

    def __post_init__(self) -> None:
        require_exact_authority_value(self.authority_epoch, AuthorityEpoch, "authority_epoch")
        require_exact_authority_value(self.head_revision, HeadRevision, "head_revision")
        require_owned_identity(self.runtime_home_owner, RuntimeHomeId)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("HeadExpectation is final")


def require_owned_head_state(state: object) -> OwnedHeadState:
    if type(state) is not OwnedHeadState or type(state._owner) is not DormantWorkspaceStateOwner:
        raise TypeError("head transitions require an owner-issued OwnedHeadState")
    return _require_current_workspace_state(state._owner, state)


def claim_pending(
    state: OwnedHeadState,
    expectation: HeadExpectation,
    operation_id: OperationId,
    mutation_id: MutationId,
) -> OwnedHeadState:
    """Apply the complete dormant CAS predicate through the exact lifecycle owner."""

    owned = require_owned_head_state(state)
    return owned._owner.claim_pending(owned, expectation, operation_id, mutation_id)


def advance_head(
    state: OwnedHeadState,
    *,
    operation_id: OperationId,
    mutation_id: MutationId,
    new_head_revision: HeadRevision,
    new_workspace_sequence: WorkspaceSequence,
) -> OwnedHeadState:
    owned = require_owned_head_state(state)
    return owned._owner.advance_head(
        owned,
        operation_id=operation_id,
        mutation_id=mutation_id,
        new_head_revision=new_head_revision,
        new_workspace_sequence=new_workspace_sequence,
    )


def enter_recovery(state: OwnedHeadState) -> OwnedHeadState:
    owned = require_owned_head_state(state)
    return owned._owner.enter_recovery(owned)


def issue_recovery_decision(
    state: OwnedHeadState,
    *,
    result: RecoveryDecisionResult,
    evidence_digest: str,
) -> RecoveryDecision:
    owned = require_owned_head_state(state)
    return owned._owner.issue_recovery_decision(
        owned,
        result=result,
        evidence_digest=evidence_digest,
    )


def require_owned_recovery_decision(
    state: OwnedHeadState,
    decision: RecoveryDecision,
) -> RecoveryDecision:
    """Require the exact decision object issued for the owner's current cycle."""

    owned = require_owned_head_state(state)
    _require_workspace_recovery_decision(owned._owner, owned, decision)
    return decision


def apply_persisted_recovery_decision(
    state: OwnedHeadState,
    database: object,
    decision: RecoveryDecision,
) -> OwnedHeadState:
    """Commit or reconcile one exact durable decision, then advance its owner state."""

    owned = require_owned_head_state(state)
    return _apply_persisted_workspace_recovery_decision(
        owned._owner,
        owned,
        database,
        decision,
    )


__all__ = [
    "CasConflictError",
    "DormantOperation",
    "DormantOperationRegistry",
    "DormantWorkspaceStateOwner",
    "HeadExpectation",
    "HeadState",
    "IdempotencyConflictError",
    "MutationLifecycle",
    "OperationRegistration",
    "OwnedHeadState",
    "PersistedRecoveryDecision",
    "RecoveryCycleBinding",
    "RecoveryDecision",
    "RecoveryDecisionResult",
    "WorkspaceLifecycle",
    "advance_head",
    "apply_persisted_recovery_decision",
    "claim_pending",
    "enter_recovery",
    "issue_recovery_decision",
    "require_owned_head_state",
    "require_owned_recovery_decision",
    "seal_authority_recovery_commit",
    "validate_mutation_transition",
    "validate_workspace_transition",
]
