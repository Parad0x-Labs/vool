from __future__ import annotations

from dataclasses import replace

import pytest

from core.workspace_authority_v3.canonical import (
    CanonicalizationError,
    canonical_bytes,
    parse_strict_json,
    typed_sha256,
)
from core.workspace_authority_v3.identity import (
    AuthorityDomainId,
    AuthorityEpoch,
    BackupGenerationId,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    InvocationIdentity,
    MutationId,
    OperationId,
    OperationIdentityBinding,
    PermissionDecisionId,
    RecoveryDecisionId,
    RuntimeHomeId,
    ToolCallId,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import (
    CasConflictError,
    DormantOperationRegistry,
    HeadExpectation,
    HeadState,
    IdempotencyConflictError,
    MutationLifecycle,
    WorkspaceLifecycle,
    advance_head,
    claim_pending,
    enter_recovery,
    validate_mutation_transition,
)
from tests.workspace_authority_v3_fixtures import active_state_owner, owned_identity


def _opaque(identity_type: type, character: str):
    return owned_identity(identity_type, character)


def _binding(*, action: str = "a") -> OperationIdentityBinding:
    return OperationIdentityBinding(
        invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 7, 2),
        workspace_id=_opaque(WorkspaceId, "2"),
        authority_epoch=AuthorityEpoch(3),
        canonical_action_digest=action * 64,
    )


def _head(**overrides: object) -> HeadState:
    values = {
        "incarnation_id": _opaque(IncarnationId, "2"),
        "authority_epoch": AuthorityEpoch(3),
        "head_revision": HeadRevision(10),
        "workspace_sequence": WorkspaceSequence(90),
        "runtime_home_owner": _opaque(RuntimeHomeId, "4"),
    }
    lifecycle = overrides.pop("lifecycle", WorkspaceLifecycle.ACTIVE)
    pending_operation_id = overrides.pop("pending_operation_id", None)
    pending_mutation_id = overrides.pop("pending_mutation_id", None)
    values.update(overrides)
    owner = active_state_owner("core-head", **values)
    state = owner.current
    if lifecycle is not WorkspaceLifecycle.ACTIVE:
        return enter_recovery(state)
    if pending_operation_id is not None and pending_mutation_id is not None:
        return claim_pending(
            state,
            HeadExpectation(state.authority_epoch, state.head_revision, state.runtime_home_owner),
            pending_operation_id,
            pending_mutation_id,
        )
    return state


def test_all_identity_contracts_are_typed_opaque_and_not_interchangeable() -> None:
    identity_types = (
        AuthorityDomainId,
        WorkspaceId,
        IncarnationId,
        RuntimeHomeId,
        EnrollmentOperationId,
        OperationId,
        MutationId,
        ToolCallId,
        PermissionDecisionId,
        BackupGenerationId,
        RecoveryDecisionId,
    )
    identities = tuple(_opaque(identity_type, format(index + 1, "x")) for index, identity_type in enumerate(identity_types))
    assert len(set(identities)) == len(identities)
    assert OperationId != MutationId
    assert not isinstance(_opaque(OperationId, "a"), MutationId)


@pytest.mark.parametrize(
    "forbidden",
    (
        "/tmp/workspace",
        "workspace-from-session-123",
        "project:chat:turn",
        "2026-08-10T12:00:00Z",
        "workspace_1234",
    ),
)
def test_workspace_identity_rejects_path_session_project_and_timestamp_shapes(forbidden: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        WorkspaceId(forbidden)


def test_operation_identity_binds_every_required_dimension() -> None:
    baseline = _binding()
    variants = (
        replace(baseline, invocation_identity=InvocationIdentity(f"turn_{'2' * 64}", 7, 2)),
        replace(baseline, invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 8, 2)),
        replace(baseline, invocation_identity=InvocationIdentity(f"turn_{'1' * 64}", 7, 3)),
        replace(baseline, workspace_id=_opaque(WorkspaceId, "3")),
        replace(baseline, authority_epoch=AuthorityEpoch(4)),
        replace(baseline, canonical_action_digest="b" * 64),
    )
    assert all(variant.operation_id() != baseline.operation_id() for variant in variants[:3])
    assert all(variant.operation_id() == baseline.operation_id() for variant in variants[3:])
    assert all(variant.digest() != baseline.digest() for variant in variants)


def test_replay_returns_the_same_operation_and_mutation_but_plan_conflict_fails() -> None:
    registry = DormantOperationRegistry()
    first = registry.register(_binding(), "b" * 64)
    replay = registry.register(_binding(), "b" * 64)
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.operation is first.operation
    assert replay.operation.mutation_id == first.operation.mutation_id
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        registry.register(_binding(), "c" * 64)
    assert registry.execution_authority is False


def test_unknown_causality_can_never_transition_to_applied() -> None:
    with pytest.raises(ValueError, match="illegal mutation lifecycle transition"):
        validate_mutation_transition(
            MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN,
            MutationLifecycle.APPLIED_NORMAL,
        )
    with pytest.raises(ValueError, match="illegal mutation lifecycle transition"):
        validate_mutation_transition(MutationLifecycle.RECOVERY_REQUIRED, MutationLifecycle.APPLIED_NORMAL)
    validate_mutation_transition(
        MutationLifecycle.RECOVERED_EFFECT_CAUSALITY_UNKNOWN,
        MutationLifecycle.RECOVERY_REQUIRED,
    )


def test_head_cas_checks_epoch_revision_owner_health_and_both_pending_slots() -> None:
    state = _head()
    expectation = HeadExpectation(AuthorityEpoch(3), HeadRevision(10), _opaque(RuntimeHomeId, "4"))
    operation_id = _opaque(OperationId, "5")
    mutation_id = _opaque(MutationId, "6")
    claimed = claim_pending(state, expectation, operation_id, mutation_id)
    assert claimed.pending_operation_id == operation_id
    assert claimed.pending_mutation_id == mutation_id

    hostile_states = (
        (_head(authority_epoch=AuthorityEpoch(4)), expectation),
        (_head(head_revision=HeadRevision(11)), expectation),
        (_head(runtime_home_owner=_opaque(RuntimeHomeId, "7")), expectation),
        (_head(lifecycle=WorkspaceLifecycle.REBIND_REQUIRED), expectation),
        (
            _head(
                pending_operation_id=_opaque(OperationId, "8"),
                pending_mutation_id=_opaque(MutationId, "9"),
            ),
            expectation,
        ),
    )
    for hostile_state, hostile_expectation in hostile_states:
        with pytest.raises(CasConflictError):
            claim_pending(hostile_state, hostile_expectation, operation_id, mutation_id)


def test_head_and_sequence_gaps_are_explicitly_legal() -> None:
    operation_id = _opaque(OperationId, "5")
    mutation_id = _opaque(MutationId, "6")
    state = _head(pending_operation_id=operation_id, pending_mutation_id=mutation_id)
    advanced = advance_head(
        state,
        operation_id=operation_id,
        mutation_id=mutation_id,
        new_head_revision=HeadRevision(1000),
        new_workspace_sequence=WorkspaceSequence(9000),
    )
    assert advanced.head_revision == HeadRevision(1000)
    assert advanced.workspace_sequence == WorkspaceSequence(9000)
    assert advanced.pending_operation_id is None


def test_canonical_profile_rejects_ambiguous_json_and_is_key_order_independent() -> None:
    assert canonical_bytes({"b": 2, "a": 1}) == canonical_bytes({"a": 1, "b": 2})
    for payload in ('{"a":1,"a":2}', '{"n":1.0}', '{"n":9223372036854775808}'):
        with pytest.raises(CanonicalizationError):
            parse_strict_json(payload)


def test_typed_hash_domains_never_alias_for_the_same_payload() -> None:
    payload = canonical_bytes({"phase": "DORMANT_PHASE0"})
    domains = ("ENROLLMENT", "OPERATION", "CURSOR", "BACKUP")
    assert len({typed_sha256(domain, payload) for domain in domains}) == len(domains)
