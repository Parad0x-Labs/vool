"""Check-2 re-review repair: sealed durable commit, and exact-primitive authority counters.

Two defects, each with the attack refused, the legitimate path preserved, and a causal
case that fails if the production guard goes back to what it was.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from core.workspace_authority_v3 import lifecycle as lifecycle_module
from core.workspace_authority_v3.canonical import MAX_SIGNED_64
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
    OperationIdentityBinding,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
    canonical_counter_value,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantWorkspaceStateOwner,
    HeadExpectation,
    RecoveryDecisionResult,
    WorkspaceLifecycle,
    advance_head,
    apply_persisted_recovery_decision,
    claim_pending,
    enter_recovery,
    issue_recovery_decision,
)


class LyingInt(int):
    """An ordinary int subclass whose comparisons answer in the attacker's favour.

    It tells the truth at the sealed bounds so that a range check written as
    ``minimum <= value <= maximum`` accepts it, and lies everywhere else.
    """

    def __ge__(self, other: object) -> bool:
        return True if other in (0, 1) else int.__ge__(self, other)

    def __le__(self, other: object) -> bool:
        return other == MAX_SIGNED_64

    def __lt__(self, other: object) -> bool:
        return False

    def __gt__(self, other: object) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = int.__hash__


@contextmanager
def substituted(owner: object, name: str, value: object) -> Iterator[None]:
    """Ordinary attribute substitution, always withdrawn afterwards."""

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


def _owner() -> DormantWorkspaceStateOwner:
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=AuthorityDomainId.from_authority_claim(
            AuthorityClaimProvenance.establish()
        ),
        enrollment_operation_id=EnrollmentOperationId.new(),
    )
    return DormantWorkspaceStateOwner.from_enrollment(
        enrollment=enrollment,
        incarnation_id=IncarnationId.new(),
        authority_epoch=AuthorityEpoch(1),
        head_revision=HeadRevision(0),
        workspace_sequence=WorkspaceSequence(0),
        runtime_home_owner=RuntimeHomeId.new(),
    )


def _binding(workspace_id: WorkspaceId, turn: str = "1") -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity("turn_" + turn * 64, 1, 1),
        workspace_id=workspace_id,
        authority_epoch=AuthorityEpoch(1),
        canonical_action_digest="a" * 64,
    )


def _expectation(state) -> HeadExpectation:  # type: ignore[no-untyped-def]
    snapshot = state.snapshot
    return HeadExpectation(
        snapshot.authority_epoch,
        snapshot.head_revision,
        snapshot.runtime_home_owner,
    )


def _claimed(state, binding: OperationIdentityBinding):  # type: ignore[no-untyped-def]
    return claim_pending(
        state,
        _expectation(state),
        binding.operation_id(),
        binding.mutation_id(),
    )


def _open_recovery(tmp_path, name: str):  # type: ignore[no-untyped-def]
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


# ---------------------------------------------------------------------------
# Defect 1 - the durable commit is the one sealed when the database type was created
# ---------------------------------------------------------------------------


def test_check2_class_level_commit_replacement_cannot_redirect_the_recovery_commit(tmp_path) -> None:
    """Negative: replacing the class attribute does not change what the transition calls."""

    owner, recovered, decision, database = _open_recovery(tmp_path, "class-level")
    calls: list[str] = []

    def hostile(self, state, decision):  # type: ignore[no-untyped-def]
        calls.append("class-level")
        return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.ACTIVE)

    with substituted(DormantAuthorityDatabase, "commit_recovery_decision", hostile):
        with pytest.raises(CasConflictError, match="atomically consumed"):
            apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert owner.current is recovered
    assert owner.current.snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_check2_instance_shadowing_still_cannot_redirect_the_recovery_commit(tmp_path) -> None:
    """Negative: the shadowing case Check 1 closed stays closed."""

    owner, recovered, decision, database = _open_recovery(tmp_path, "instance")
    calls: list[str] = []

    def shadow(state, decision):  # type: ignore[no-untyped-def]
        calls.append("instance")
        return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.ACTIVE)

    database.commit_recovery_decision = shadow  # type: ignore[assignment]
    with pytest.raises(CasConflictError, match="atomically consumed"):
        apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert owner.current.snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_check2_both_replacements_at_once_cannot_redirect_the_recovery_commit(tmp_path) -> None:
    """Negative: neither seam helps, together or apart."""

    owner, recovered, decision, database = _open_recovery(tmp_path, "both")
    calls: list[str] = []

    def hostile(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("hostile")
        return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.ACTIVE)

    database.commit_recovery_decision = hostile  # type: ignore[assignment]
    with substituted(DormantAuthorityDatabase, "commit_recovery_decision", hostile):
        with pytest.raises(CasConflictError, match="atomically consumed"):
            apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert owner.current.snapshot.lifecycle is WorkspaceLifecycle.RECOVERY_REQUIRED


def test_check2_legitimate_persisted_recovery_still_applies_under_replacement(tmp_path) -> None:
    """Positive: real durable state still reactivates, with both seams still replaced."""

    owner, recovered, decision, database = _open_recovery(tmp_path, "legitimate")
    assert database.persist_recovery_decision(recovered, decision).committed
    calls: list[str] = []

    def hostile(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("hostile")
        return TransactionResult(TransactionOutcome.CAS_CONFLICT)

    database.commit_recovery_decision = hostile  # type: ignore[assignment]
    with substituted(DormantAuthorityDatabase, "commit_recovery_decision", hostile):
        active = apply_persisted_recovery_decision(recovered, database, decision)

    assert calls == []
    assert active.snapshot.lifecycle is WorkspaceLifecycle.ACTIVE
    assert owner.current is active


def test_check2_a_replacement_cannot_choose_the_next_lifecycle(tmp_path) -> None:
    """Negative: the applied lifecycle stays the one the authenticated decision bound."""

    owner, recovered, decision, database = _open_recovery(tmp_path, "chosen")
    assert database.persist_recovery_decision(recovered, decision).committed

    def hostile(*args, **kwargs):  # type: ignore[no-untyped-def]
        return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.REBIND_REQUIRED)

    with substituted(DormantAuthorityDatabase, "commit_recovery_decision", hostile):
        active = apply_persisted_recovery_decision(recovered, database, decision)

    assert active.snapshot.lifecycle is WorkspaceLifecycle.ACTIVE
    assert owner.current is active


def test_check2_a_lookalike_database_is_still_refused_by_exact_type(tmp_path) -> None:
    """Positive: the exact-type database check is preserved, now against the sealed type."""

    _owner_unused, recovered, decision, _database = _open_recovery(tmp_path, "lookalike")

    class LookalikeDatabase:
        def commit_recovery_decision(self, state, decision):  # type: ignore[no-untyped-def]
            return TransactionResult(TransactionOutcome.COMMITTED_NEW, WorkspaceLifecycle.ACTIVE)

    with pytest.raises(TypeError, match="exact authority database owner"):
        apply_persisted_recovery_decision(recovered, LookalikeDatabase(), decision)


def test_check2_the_sealed_commit_is_the_one_the_database_type_was_created_with() -> None:
    """The seal is one-shot and holds the database's own function object."""

    database_type, commit = lifecycle_module._trusted_authority_recovery_commit()
    assert database_type is DormantAuthorityDatabase
    assert commit is DormantAuthorityDatabase.__dict__["commit_recovery_decision"]
    with pytest.raises(TypeError, match="already sealed"):
        lifecycle_module.seal_authority_recovery_commit(DormantAuthorityDatabase)


# ---------------------------------------------------------------------------
# Defect 2 - authority counters hold exact primitive integers
# ---------------------------------------------------------------------------


def test_check2_lying_int_subclass_would_defeat_a_range_check_written_as_comparisons() -> None:
    """The attack input is real: this subclass passes ``minimum <= v <= maximum``."""

    hostile = LyingInt(1)
    assert 0 <= hostile <= MAX_SIGNED_64
    assert 1 <= hostile <= MAX_SIGNED_64
    assert (hostile <= 10) is False
    assert hostile == 999


@pytest.mark.parametrize("counter_type", [AuthorityEpoch, HeadRevision, WorkspaceSequence])
def test_check2_counters_reject_an_int_subclass(counter_type: type) -> None:
    """Negative: no caller-provided subclass survives inside an authority counter."""

    with pytest.raises(TypeError, match="exact integer"):
        counter_type(LyingInt(1))


def test_check2_counters_reject_out_of_range_values_even_when_comparison_lies() -> None:
    with pytest.raises(TypeError, match="exact integer"):
        AuthorityEpoch(LyingInt(0))
    with pytest.raises(TypeError, match="exact integer"):
        HeadRevision(LyingInt(-5))
    with pytest.raises(TypeError, match="exact integer"):
        HeadRevision(LyingInt(MAX_SIGNED_64 + 1))
    with pytest.raises(ValueError, match="outside its sealed range"):
        AuthorityEpoch(0)
    with pytest.raises(ValueError, match="outside its sealed range"):
        HeadRevision(-1)
    with pytest.raises(ValueError, match="outside its sealed range"):
        HeadRevision(MAX_SIGNED_64 + 1)
    with pytest.raises(TypeError, match="exact integer"):
        HeadRevision(True)


def test_check2_legitimate_exact_int_counters_still_work() -> None:
    """Positive: the ordinary counter model is untouched."""

    epoch = AuthorityEpoch(1)
    revision = HeadRevision(0)
    sequence = WorkspaceSequence(MAX_SIGNED_64)
    for counter, expected in ((epoch, 1), (revision, 0), (sequence, MAX_SIGNED_64)):
        value = canonical_counter_value(counter)
        assert value == expected
        assert type(value) is int


def test_check2_canonical_counter_value_never_returns_a_subclass() -> None:
    """Negative: a counter forced past its constructor still cannot export a subclass."""

    forced = object.__new__(HeadRevision)
    object.__setattr__(forced, "value", LyingInt(3))
    with pytest.raises(TypeError, match="exact integer"):
        canonical_counter_value(forced)


def test_check2_advance_head_cannot_regress_from_ten_to_one() -> None:
    """Negative: monotonicity is decided on exact integers."""

    owner = _owner()
    workspace_id = owner.current.snapshot.workspace_id
    first = _binding(workspace_id, "1")
    ten = advance_head(
        _claimed(owner.current, first),
        operation_id=first.operation_id(),
        mutation_id=first.mutation_id(),
        new_head_revision=HeadRevision(10),
        new_workspace_sequence=WorkspaceSequence(10),
    )
    assert canonical_counter_value(ten.snapshot.head_revision) == 10

    second = _binding(workspace_id, "2")
    claimed = _claimed(ten, second)
    with pytest.raises(TypeError, match="exact integer"):
        advance_head(
            claimed,
            operation_id=second.operation_id(),
            mutation_id=second.mutation_id(),
            new_head_revision=HeadRevision(LyingInt(1)),
            new_workspace_sequence=WorkspaceSequence(LyingInt(1)),
        )
    with pytest.raises(CasConflictError, match="head revision must advance"):
        advance_head(
            claimed,
            operation_id=second.operation_id(),
            mutation_id=second.mutation_id(),
            new_head_revision=HeadRevision(1),
            new_workspace_sequence=WorkspaceSequence(1),
        )
    assert canonical_counter_value(owner.current.snapshot.head_revision) == 10


def test_check2_advance_head_still_advances_normally() -> None:
    """Positive: a genuine advance is unaffected."""

    owner = _owner()
    binding = _binding(owner.current.snapshot.workspace_id)
    advanced = advance_head(
        _claimed(owner.current, binding),
        operation_id=binding.operation_id(),
        mutation_id=binding.mutation_id(),
        new_head_revision=HeadRevision(1),
        new_workspace_sequence=WorkspaceSequence(1),
    )
    assert canonical_counter_value(advanced.snapshot.head_revision) == 1
    assert advanced.snapshot.pending_operation_id is None


def test_check2_head_expectation_cannot_be_satisfied_by_a_lying_subclass() -> None:
    """Negative: a hostile expectation cannot stand in for the wrong current head."""

    owner = _owner()
    state = owner.current
    binding = _binding(state.snapshot.workspace_id)

    with pytest.raises(TypeError, match="exact integer"):
        HeadExpectation(
            AuthorityEpoch(LyingInt(9)),
            HeadRevision(LyingInt(41)),
            state.snapshot.runtime_home_owner,
        )

    wrong = HeadExpectation(AuthorityEpoch(9), HeadRevision(41), state.snapshot.runtime_home_owner)
    with pytest.raises(CasConflictError, match="epoch mismatch"):
        claim_pending(state, wrong, binding.operation_id(), binding.mutation_id())
    assert owner.current is state


def test_check2_recovery_cycle_binding_holds_exact_counters(tmp_path) -> None:
    """Positive: the recovery cycle still binds the head, on exact integers."""

    _owner_unused, recovered, decision, database = _open_recovery(tmp_path, "cycle")
    cycle = recovered.snapshot.recovery_cycle
    assert cycle is not None
    assert type(canonical_counter_value(cycle.expected_head_revision)) is int
    assert type(cycle.generation) is int
    assert canonical_counter_value(cycle.authority_epoch) == 1
    assert database.persist_recovery_decision(recovered, decision).committed
    assert apply_persisted_recovery_decision(recovered, database, decision).snapshot.lifecycle is (
        WorkspaceLifecycle.ACTIVE
    )


def test_check2_recovery_generation_exact_int_rule_is_intact() -> None:
    """Positive: the generation rule from the previous repair still holds."""

    owner = _owner()
    recovered = enter_recovery(owner.current)
    assert type(recovered.snapshot.recovery_generation) is int
    assert recovered.snapshot.recovery_generation == 1
    with pytest.raises(Exception, match="exact non-negative integer"):
        lifecycle_module._exact_generation(LyingInt(1), "recovery_generation", positive=True)


def test_check2_invocation_ordinals_use_the_same_exact_primitive_rule() -> None:
    """Direct analogue: the canonical invocation ordinals are authority-bearing too."""

    with pytest.raises(ValueError, match="non-negative signed-64 integer"):
        InvocationIdentity("turn_" + "1" * 64, LyingInt(1), 1)
    with pytest.raises(ValueError, match="non-negative signed-64 integer"):
        InvocationIdentity("turn_" + "1" * 64, 1, LyingInt(1))
    ordinary = InvocationIdentity("turn_" + "1" * 64, 1, 1)
    assert ordinary.canonical_payload()["controller_generation"] == 1


def test_check2_phase0_stays_dormant() -> None:
    from core.workspace_authority_v3 import EXECUTION_AUTHORITY

    assert EXECUTION_AUTHORITY is False
    assert DormantWorkspaceStateOwner.execution_authority is False
    assert DormantAuthorityDatabase.execution_authority is False


# ---------------------------------------------------------------------------
# Check-2 re-review repair: sealed-field reads inside the identity validators.
#
# The exact-primitive rule above is only worth as much as the read it validates.
# ``__post_init__`` used late-bound attribute lookup, so replacing a field descriptor
# with a property could present an acceptable value while a hostile one stayed stored.
# ---------------------------------------------------------------------------


def _owned_workspace() -> WorkspaceId:
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    return WorkspaceId.from_enrollment(
        WorkspaceEnrollmentProvenance.establish(
            authority_domain_id=domain,
            enrollment_operation_id=EnrollmentOperationId.new(),
        )
    )


def _operation_binding(workspace_id: WorkspaceId) -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity("turn_" + "1" * 64, 1, 1),
        workspace_id=workspace_id,
        authority_epoch=AuthorityEpoch(4),
        canonical_action_digest="a" * 64,
    )


@pytest.mark.parametrize("field", ("controller_generation", "persisted_tool_ordinal"))
def test_check2_replaced_descriptor_cannot_hide_a_stored_int_subclass(field: str) -> None:
    """The reported defect: a property must not mask what the instance actually holds."""

    invocation = InvocationIdentity("turn_" + "4" * 64, 1, 1)
    object.__setattr__(invocation, field, LyingInt(9))
    with substituted(InvocationIdentity, field, property(lambda self: 1)):
        assert getattr(invocation, field) == 1  # the replaced descriptor does lie
        with pytest.raises(ValueError, match="non-negative signed-64 integer"):
            InvocationIdentity.__post_init__(invocation)


def test_check2_replaced_turn_id_descriptor_cannot_hide_a_stored_value() -> None:
    invocation = InvocationIdentity("turn_" + "4" * 64, 1, 1)
    object.__setattr__(invocation, "canonical_turn_id", "not-a-turn-id")
    with substituted(
        InvocationIdentity,
        "canonical_turn_id",
        property(lambda self: "turn_" + "4" * 64),
    ):
        with pytest.raises(ValueError, match="opaque canonical turn identity"):
            InvocationIdentity.__post_init__(invocation)


@pytest.mark.parametrize(
    ("field", "hostile"),
    (
        ("canonical_action_digest", "NOT-A-DIGEST"),
        ("authority_epoch", "not-an-epoch"),
        ("workspace_id", "not-a-workspace"),
        ("invocation_identity", "not-an-invocation"),
    ),
)
def test_check2_operation_binding_validator_reads_the_sealed_field(
    field: str,
    hostile: object,
) -> None:
    """Same defect shape on the adjacent validator over the same sealed-slot mechanism."""

    workspace_id = _owned_workspace()
    reference = _operation_binding(workspace_id)
    binding = _operation_binding(workspace_id)
    object.__setattr__(binding, field, hostile)
    with substituted(OperationIdentityBinding, field, property(lambda self: getattr(reference, field))):
        with pytest.raises((TypeError, ValueError)):
            OperationIdentityBinding.__post_init__(binding)


def test_check2_sealed_field_validation_preserves_the_legitimate_path() -> None:
    workspace_id = _owned_workspace()
    invocation = InvocationIdentity("turn_" + "1" * 64, 2, 3)
    InvocationIdentity.__post_init__(invocation)
    assert invocation.canonical_payload() == {
        "canonical_turn_id": "turn_" + "1" * 64,
        "controller_generation": 2,
        "persisted_tool_ordinal": 3,
    }
    binding = _operation_binding(workspace_id)
    OperationIdentityBinding.__post_init__(binding)
    assert binding.digest() == _operation_binding(workspace_id).digest()
    assert binding.operation_id() == _operation_binding(workspace_id).operation_id()
    assert InvocationIdentity("turn_" + "1" * 64, 0, 0).controller_generation == 0


def test_check2_validators_do_not_consult_late_bound_attribute_lookup() -> None:
    """A descriptor that raises on attribute access must not stop validation working."""

    def explode(_self: object) -> object:
        raise AssertionError("validator used late-bound attribute lookup")

    invocation = InvocationIdentity("turn_" + "1" * 64, 1, 1)
    with substituted(InvocationIdentity, "controller_generation", property(explode)):
        InvocationIdentity.__post_init__(invocation)

    workspace_id = _owned_workspace()
    binding = _operation_binding(workspace_id)
    with substituted(OperationIdentityBinding, "canonical_action_digest", property(explode)):
        OperationIdentityBinding.__post_init__(binding)
