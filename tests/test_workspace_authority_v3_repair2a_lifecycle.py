"""Repair #2A lane A: lifecycle issuance, trusted derivation, dispatch and comparison.

Each defect gets a positive case (normal behaviour is preserved), a negative case (the
attack is refused), and, where the guard is a serialization point, a bounded causal
concurrency case that fails if the serialization is removed.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from core.workspace_authority_v3 import lifecycle as lifecycle_module
from core.workspace_authority_v3.database import (
    DormantAuthorityDatabase,
    TransactionOutcome,
    TransactionResult,
)
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OpaqueIdentity,
    OperationId,
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperationRegistry,
    DormantWorkspaceStateOwner,
    HeadExpectation,
    HeadState,
    IdempotencyConflictError,
    OwnedHeadState,
    RecoveryDecisionResult,
    WorkspaceLifecycle,
    advance_head,
    apply_persisted_recovery_decision,
    claim_pending,
    enter_recovery,
    issue_recovery_decision,
    validate_workspace_transition,
)

_TURN = "turn_" + "1" * 64
_SECOND_TURN = "turn_" + "2" * 64


@contextmanager
def substituted(owner: object, name: str, value: object) -> Iterator[None]:
    """Ordinary class/instance attribute substitution, always withdrawn afterwards."""

    missing = object()
    original = owner.__dict__.get(name, missing) if isinstance(owner, type) else missing
    setattr(owner, name, value)
    try:
        yield
    finally:
        if isinstance(owner, type) and original is missing:
            delattr(owner, name)
        elif isinstance(owner, type):
            setattr(owner, name, original)
        else:
            delattr(owner, name)


@contextmanager
def tight_switch_interval() -> Iterator[None]:
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(original)


def _enrollment() -> WorkspaceEnrollmentProvenance:
    return WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=AuthorityDomainId.from_authority_claim(
            AuthorityClaimProvenance.establish()
        ),
        enrollment_operation_id=EnrollmentOperationId.new(),
    )


def _issuance_kwargs() -> dict[str, object]:
    return {
        "enrollment": _enrollment(),
        "incarnation_id": IncarnationId.new(),
        "authority_epoch": AuthorityEpoch(1),
        "head_revision": HeadRevision(0),
        "workspace_sequence": WorkspaceSequence(0),
        "runtime_home_owner": RuntimeHomeId.new(),
    }


def _owner() -> DormantWorkspaceStateOwner:
    return DormantWorkspaceStateOwner.from_enrollment(**_issuance_kwargs())  # type: ignore[arg-type]


def _binding(
    workspace_id: WorkspaceId,
    *,
    turn: str = _TURN,
    ordinal: int = 1,
    epoch: int = 1,
) -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(turn, 1, ordinal),
        workspace_id=workspace_id,
        authority_epoch=AuthorityEpoch(epoch),
        canonical_action_digest="a" * 64,
    )


def _expectation(state: OwnedHeadState) -> HeadExpectation:
    snapshot = state.snapshot
    return HeadExpectation(
        snapshot.authority_epoch,
        snapshot.head_revision,
        snapshot.runtime_home_owner,
    )


def _race_issuance(workers: int) -> tuple[list[DormantWorkspaceStateOwner], list[BaseException]]:
    """Drive ``workers`` concurrent public issuances of ONE logical workspace enrollment."""

    kwargs = _issuance_kwargs()
    barrier = threading.Barrier(workers)
    accepted: list[DormantWorkspaceStateOwner] = []
    refused: list[BaseException] = []
    guard = threading.Lock()

    def issue(_index: int) -> None:
        barrier.wait()
        try:
            owner = DormantWorkspaceStateOwner.from_enrollment(**kwargs)  # type: ignore[arg-type]
        except BaseException as exc:  # the refusal itself is the assertion
            with guard:
                refused.append(exc)
            return
        with guard:
            accepted.append(owner)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(issue, range(workers)))
    return accepted, refused


# ---------------------------------------------------------------------------
# D1 - one-owner issuance is linearized
# ---------------------------------------------------------------------------


def test_d1_single_enrollment_still_issues_one_usable_owner() -> None:
    """Positive: the uncontended path is unchanged."""

    kwargs = _issuance_kwargs()
    owner = DormantWorkspaceStateOwner.from_enrollment(**kwargs)  # type: ignore[arg-type]
    assert owner.current.lifecycle is WorkspaceLifecycle.ACTIVE
    with pytest.raises(CasConflictError, match="already has"):
        DormantWorkspaceStateOwner.from_enrollment(**kwargs)  # type: ignore[arg-type]
    assert owner.current.lifecycle is WorkspaceLifecycle.ACTIVE


def test_d1_a_refused_issuance_publishes_no_partial_owner_state() -> None:
    """Negative: a losing contender holds nothing the lifecycle boundary will honour."""

    kwargs = _issuance_kwargs()
    winner = DormantWorkspaceStateOwner.from_enrollment(**kwargs)  # type: ignore[arg-type]
    loser = object.__new__(DormantWorkspaceStateOwner)
    with pytest.raises(CasConflictError, match="already has"):
        lifecycle_module._issue_workspace_state_owner(loser, **kwargs)
    with pytest.raises(TypeError, match="not issued from enrollment provenance"):
        assert loser.current
    assert winner.current.lifecycle is WorkspaceLifecycle.ACTIVE


def test_d1_concurrent_issuance_accepts_exactly_one_owner_per_workspace() -> None:
    """Causal: bounded contention, repeated, with the scheduler pushed to interleave."""

    workers = 8
    rounds = 40
    with tight_switch_interval():
        for round_index in range(rounds):
            accepted, refused = _race_issuance(workers)
            assert len(accepted) == 1, (
                f"round {round_index}: {len(accepted)} owners issued for one workspace"
            )
            assert len(refused) == workers - 1
            assert all(isinstance(error, CasConflictError) for error in refused)
            assert accepted[0].current.lifecycle is WorkspaceLifecycle.ACTIVE


def test_d1_concurrent_issuance_is_linearized_with_the_window_forced_open() -> None:
    """Causal: a real delay between the existence check and publication changes nothing."""

    original_post_init = HeadState.__post_init__

    def slow_post_init(self: HeadState) -> None:
        original_post_init(self)
        threading.Event().wait(0.02)

    with substituted(HeadState, "__post_init__", slow_post_init):
        accepted, refused = _race_issuance(6)

    assert len(accepted) == 1
    assert len(refused) == 5
    assert all(isinstance(error, CasConflictError) for error in refused)
    assert accepted[0].current.lifecycle is WorkspaceLifecycle.ACTIVE


@contextmanager
def _stepped_issuance(delay: float) -> Iterator[None]:
    """Make every line of the issuance path a scheduling point for the other threads.

    Issuance that is genuinely serialized still admits exactly one owner while its whole
    check-and-publish region is stepped line by line; issuance that only reads and writes
    two dictionaries in quick succession does not.
    """

    previous = getattr(threading, "_trace_hook", None)

    def step(frame, event, arg):  # type: ignore[no-untyped-def]
        threading.Event().wait(delay)
        return step

    def hook(frame, event, arg):  # type: ignore[no-untyped-def]
        if frame.f_code.co_name == "issue_owner":
            return step
        return previous(frame, event, arg) if previous is not None else None

    threading.settrace(hook)
    try:
        yield
    finally:
        threading.settrace(previous)


def test_d1_concurrent_issuance_survives_a_stepped_check_and_publish_region() -> None:
    """Causal: the serialization point itself, not just the statement order, is load-bearing."""

    with _stepped_issuance(0.001):
        accepted, refused = _race_issuance(5)

    assert len(accepted) == 1, f"{len(accepted)} owners issued for one workspace"
    assert len(refused) == 4
    assert all(isinstance(error, CasConflictError) for error in refused)
    assert accepted[0].current.lifecycle is WorkspaceLifecycle.ACTIVE


# ---------------------------------------------------------------------------
# D2 - transitions derive from the authenticated snapshot, not the public view
# ---------------------------------------------------------------------------


def test_d2_transitions_are_unchanged_without_substitution() -> None:
    """Positive: claim -> advance -> recover still walks the real head."""

    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    claimed = claim_pending(
        initial,
        _expectation(initial),
        binding.operation_id(),
        binding.mutation_id(),
    )
    assert claimed.snapshot.pending_operation_id == binding.operation_id()
    advanced = advance_head(
        claimed,
        operation_id=binding.operation_id(),
        mutation_id=binding.mutation_id(),
        new_head_revision=HeadRevision(1),
        new_workspace_sequence=WorkspaceSequence(1),
    )
    assert advanced.snapshot.head_revision == HeadRevision(1)
    recovered = enter_recovery(advanced)
    assert recovered.snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED
    assert recovered.snapshot.recovery_cycle is not None
    assert recovered.snapshot.recovery_cycle.expected_head_revision == HeadRevision(1)


def test_d2_claim_pending_does_not_read_a_replaced_snapshot_property() -> None:
    """Negative: the mutation base stays the snapshot authenticated under the owner lock."""

    owner = _owner()
    initial = owner.current
    real_snapshot = initial.snapshot
    forged = replace(real_snapshot, head_revision=HeadRevision(99))
    binding = _binding(real_snapshot.workspace_id)
    expectation = HeadExpectation(
        real_snapshot.authority_epoch,
        real_snapshot.head_revision,
        real_snapshot.runtime_home_owner,
    )

    with substituted(OwnedHeadState, "snapshot", property(lambda _self: forged)):
        claimed = claim_pending(
            initial,
            expectation,
            binding.operation_id(),
            binding.mutation_id(),
        )
        minted = claimed._snapshot

    assert minted.head_revision == HeadRevision(0)
    assert minted.workspace_id == real_snapshot.workspace_id
    assert owner.current._snapshot.head_revision == HeadRevision(0)


def test_d2_advance_head_does_not_read_a_replaced_snapshot_property() -> None:
    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    claimed = claim_pending(
        initial,
        _expectation(initial),
        binding.operation_id(),
        binding.mutation_id(),
    )
    forged = replace(
        claimed._snapshot,
        workspace_sequence=WorkspaceSequence(500),
    )

    with substituted(OwnedHeadState, "snapshot", property(lambda _self: forged)):
        advanced = advance_head(
            claimed,
            operation_id=binding.operation_id(),
            mutation_id=binding.mutation_id(),
            new_head_revision=HeadRevision(1),
            new_workspace_sequence=WorkspaceSequence(1),
        )

    assert advanced._snapshot.workspace_sequence == WorkspaceSequence(1)
    assert owner.current._snapshot.workspace_sequence == WorkspaceSequence(1)


def test_d2_enter_recovery_binds_the_authenticated_head_not_a_replaced_view() -> None:
    owner = _owner()
    initial = owner.current
    forged = replace(initial.snapshot, head_revision=HeadRevision(77))

    with substituted(OwnedHeadState, "snapshot", property(lambda _self: forged)):
        recovered = enter_recovery(initial)
        cycle = recovered._snapshot.recovery_cycle

    assert cycle is not None
    assert cycle.expected_head_revision == HeadRevision(0)
    assert recovered._snapshot.head_revision == HeadRevision(0)


def test_d2_a_replaced_health_view_cannot_reopen_a_recovering_workspace() -> None:
    """Negative: the CAS predicate reads lifecycle from the authenticated snapshot."""

    owner = _owner()
    recovered = enter_recovery(owner.current)
    binding = _binding(recovered.snapshot.workspace_id)
    expectation = _expectation(recovered)

    with substituted(OwnedHeadState, "healthy", property(lambda _self: True)):
        with substituted(
            OwnedHeadState,
            "lifecycle",
            property(lambda _self: WorkspaceLifecycle.ACTIVE),
        ):
            with pytest.raises(CasConflictError, match="not healthy"):
                claim_pending(
                    recovered,
                    expectation,
                    binding.operation_id(),
                    binding.mutation_id(),
                )

    assert owner.current._snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_d2_a_replaced_epoch_view_cannot_satisfy_the_head_expectation() -> None:
    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    stale = HeadExpectation(
        AuthorityEpoch(9),
        HeadRevision(41),
        initial.snapshot.runtime_home_owner,
    )

    with substituted(
        OwnedHeadState,
        "authority_epoch",
        property(lambda _self: AuthorityEpoch(9)),
    ):
        with substituted(
            OwnedHeadState,
            "head_revision",
            property(lambda _self: HeadRevision(41)),
        ):
            with pytest.raises(CasConflictError, match="epoch mismatch"):
                claim_pending(initial, stale, binding.operation_id(), binding.mutation_id())

    assert owner.current is initial


# ---------------------------------------------------------------------------
# D3 - the recovery transition calls the contract's own database operation
# ---------------------------------------------------------------------------


def _recovery_fixture(tmp_path, name: str):
    owner = _owner()
    recovered = enter_recovery(owner.current)
    decision = issue_recovery_decision(
        recovered,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="c" * 64,
    )
    database = DormantAuthorityDatabase(tmp_path / f"{name}.sqlite3")
    assert database.register_recovery_cycle(recovered).committed
    return owner, recovered, decision, database


def test_d3_shadowed_database_method_cannot_consume_a_recovery_cycle(tmp_path) -> None:
    """Negative: an instance attribute is not the durable commit this transition needs."""

    owner, recovered, decision, database = _recovery_fixture(tmp_path, "shadow")
    calls: list[str] = []

    def shadow(state, decision) -> TransactionResult[WorkspaceLifecycle]:
        calls.append("shadow")
        return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.ACTIVE)

    database.commit_recovery_decision = shadow  # type: ignore[assignment]
    with pytest.raises(CasConflictError, match="atomically consumed"):
        apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert owner.current is recovered
    assert owner.current._snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_d3_the_trusted_commit_still_applies_while_a_shadow_is_installed(tmp_path) -> None:
    """Positive: real durable state still reactivates, and the shadow is never called."""

    owner, recovered, decision, database = _recovery_fixture(tmp_path, "trusted")
    calls: list[str] = []

    def shadow(state, decision) -> TransactionResult[WorkspaceLifecycle]:
        calls.append("shadow")
        return TransactionResult(TransactionOutcome.CAS_CONFLICT)

    assert database.persist_recovery_decision(recovered, decision).committed
    database.commit_recovery_decision = shadow  # type: ignore[assignment]
    active = apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert active._snapshot.lifecycle is WorkspaceLifecycle.ACTIVE
    assert owner.current is active


def test_d3_a_shadow_cannot_choose_a_lifecycle_the_decision_did_not_bind(tmp_path) -> None:
    """Negative: the next lifecycle comes from the authenticated decision, not the call."""

    owner, recovered, decision, database = _recovery_fixture(tmp_path, "chosen")
    assert database.persist_recovery_decision(recovered, decision).committed
    database.commit_recovery_decision = lambda state, decision: TransactionResult(  # type: ignore[assignment]
        TransactionOutcome.COMMITTED_NEW,
        WorkspaceLifecycle.REBIND_REQUIRED,
    )
    active = apply_persisted_recovery_decision(recovered, database, decision)
    assert active._snapshot.lifecycle is WorkspaceLifecycle.ACTIVE
    assert owner.current is active


# ---------------------------------------------------------------------------
# D4 - idempotency and CAS decisions compare canonical owner-controlled values
# ---------------------------------------------------------------------------


def test_d4_conflicting_binding_is_rejected_when_equality_always_agrees() -> None:
    """Negative: a cross-workspace binding cannot replay a different operation."""

    registry = DormantOperationRegistry()
    first = _binding(_owner().current.snapshot.workspace_id)
    conflicting = _binding(_owner().current.snapshot.workspace_id)
    registered = registry.register(first, "b" * 64)
    assert registered.replayed is False

    with substituted(OperationIdentityBinding, "__eq__", lambda _self, _other: True):
        with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
            registry.register(conflicting, "b" * 64)
        with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
            registry.register(first, "9" * 64)


def test_d4_identical_binding_still_replays_when_equality_always_disagrees() -> None:
    """Positive: a genuine replay is still a replay, not a false conflict."""

    registry = DormantOperationRegistry()
    workspace_id = _owner().current.snapshot.workspace_id
    first = registry.register(_binding(workspace_id), "b" * 64)

    with substituted(OperationIdentityBinding, "__eq__", lambda _self, _other: False):
        replayed = registry.register(_binding(workspace_id), "b" * 64)

    assert replayed.replayed is True
    assert replayed.operation is first.operation


def test_d4_replay_lookup_is_not_redirected_by_replaced_identity_hashing() -> None:
    """Negative: the replay index is canonical text, not a virtual hash/eq pair."""

    registry = DormantOperationRegistry()
    workspace_id = _owner().current.snapshot.workspace_id
    first = registry.register(_binding(workspace_id, turn=_TURN), "b" * 64)

    with substituted(OpaqueIdentity, "__eq__", lambda _self, _other: True):
        with substituted(OpaqueIdentity, "__hash__", lambda _self: 7):
            other = registry.register(_binding(workspace_id, turn=_SECOND_TURN), "b" * 64)
            same = registry.register(_binding(workspace_id, turn=_TURN), "b" * 64)

    assert other.replayed is False
    assert other.operation is not first.operation
    assert same.replayed is True
    assert same.operation is first.operation


def test_d4_stale_head_expectation_is_refused_when_counter_equality_always_agrees() -> None:
    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    stale = HeadExpectation(
        AuthorityEpoch(9),
        HeadRevision(41),
        initial.snapshot.runtime_home_owner,
    )

    with substituted(AuthorityEpoch, "__eq__", lambda _self, _other: True):
        with substituted(HeadRevision, "__eq__", lambda _self, _other: True):
            with pytest.raises(CasConflictError, match="epoch mismatch"):
                claim_pending(initial, stale, binding.operation_id(), binding.mutation_id())

    assert owner.current is initial


def test_d4_correct_head_expectation_is_accepted_when_equality_always_disagrees() -> None:
    """Positive: canonical comparison keeps the legitimate CAS path open."""

    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    expectation = _expectation(initial)

    with substituted(AuthorityEpoch, "__eq__", lambda _self, _other: False):
        with substituted(HeadRevision, "__eq__", lambda _self, _other: False):
            with substituted(RuntimeHomeId, "__eq__", lambda _self, _other: False):
                claimed = claim_pending(
                    initial,
                    expectation,
                    binding.operation_id(),
                    binding.mutation_id(),
                )

    assert claimed._snapshot.pending_operation_id == binding.operation_id()


def test_d4_head_regression_is_refused_when_ordering_always_agrees() -> None:
    """Negative: monotonicity is decided on sealed integers, not on ``__le__``."""

    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    claimed = claim_pending(
        initial,
        _expectation(initial),
        binding.operation_id(),
        binding.mutation_id(),
    )

    with substituted(HeadRevision, "__le__", lambda _self, _other: False):
        with substituted(WorkspaceSequence, "__le__", lambda _self, _other: False):
            with pytest.raises(CasConflictError, match="head revision must advance"):
                advance_head(
                    claimed,
                    operation_id=binding.operation_id(),
                    mutation_id=binding.mutation_id(),
                    new_head_revision=HeadRevision(0),
                    new_workspace_sequence=WorkspaceSequence(0),
                )

    assert owner.current is claimed


def test_d4_genuine_advance_survives_ordering_substitution() -> None:
    """Positive: a real advance is not blocked by a hostile ``__le__``."""

    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    claimed = claim_pending(
        initial,
        _expectation(initial),
        binding.operation_id(),
        binding.mutation_id(),
    )

    with substituted(HeadRevision, "__le__", lambda _self, _other: True):
        with substituted(WorkspaceSequence, "__le__", lambda _self, _other: True):
            advanced = advance_head(
                claimed,
                operation_id=binding.operation_id(),
                mutation_id=binding.mutation_id(),
                new_head_revision=HeadRevision(1),
                new_workspace_sequence=WorkspaceSequence(1),
            )

    assert advanced._snapshot.head_revision == HeadRevision(1)
    assert advanced._snapshot.pending_operation_id is None


def test_d4_pending_identity_mismatch_survives_replaced_identity_equality() -> None:
    owner = _owner()
    initial = owner.current
    binding = _binding(initial.snapshot.workspace_id)
    claimed = claim_pending(
        initial,
        _expectation(initial),
        binding.operation_id(),
        binding.mutation_id(),
    )
    foreign = _binding(initial.snapshot.workspace_id, turn=_SECOND_TURN)

    with substituted(OpaqueIdentity, "__eq__", lambda _self, _other: True):
        with pytest.raises(CasConflictError, match="pending identity mismatch"):
            advance_head(
                claimed,
                operation_id=foreign.operation_id(),
                mutation_id=foreign.mutation_id(),
                new_head_revision=HeadRevision(1),
                new_workspace_sequence=WorkspaceSequence(1),
            )

    assert owner.current is claimed


def test_d4_foreign_recovery_decision_is_refused_when_equality_always_agrees(tmp_path) -> None:
    owner_a, recovered_a, decision_a, database = _recovery_fixture(tmp_path, "foreign")
    owner_b = _owner()
    recovered_b = enter_recovery(owner_b.current)
    decision_b = issue_recovery_decision(
        recovered_b,
        result=RecoveryDecisionResult.REACTIVATE,
        evidence_digest="d" * 64,
    )
    assert database.persist_recovery_decision(recovered_a, decision_a).committed

    with substituted(OpaqueIdentity, "__eq__", lambda _self, _other: True):
        with pytest.raises(CasConflictError):
            apply_persisted_recovery_decision(recovered_a, database, decision_b)

    assert owner_a.current is recovered_a
    assert owner_a.current._snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_d4_illegal_transition_is_refused_when_lifecycle_equality_always_agrees() -> None:
    """Negative: the transition table is keyed by the sealed canonical value."""

    with substituted(WorkspaceLifecycle, "__eq__", lambda _self, _other: True):
        with substituted(WorkspaceLifecycle, "__hash__", lambda _self: 11):
            with pytest.raises(Exception, match="illegal workspace lifecycle transition"):
                validate_workspace_transition(
                    WorkspaceLifecycle.RECOVERY_REQUIRED,
                    WorkspaceLifecycle.ACTIVE,
                )
            validate_workspace_transition(
                WorkspaceLifecycle.ACTIVE,
                WorkspaceLifecycle.RECOVERY_REQUIRED,
            )


def test_d4_head_state_rejects_a_generation_whose_equality_is_not_an_integer() -> None:
    """Negative: recovery generations compare as exact integers."""

    class Agreeable(int):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = int.__hash__

    owner = _owner()
    snapshot = owner.current.snapshot
    with pytest.raises(Exception, match="exact non-negative integer"):
        replace(snapshot, recovery_generation=Agreeable(3))


def test_phase0_stays_dormant_after_the_lifecycle_repair() -> None:
    assert DormantOperationRegistry.execution_authority is False
    assert DormantWorkspaceStateOwner.execution_authority is False
    assert lifecycle_module.OperationId is OperationId
    assert lifecycle_module.MutationId is MutationId
