from __future__ import annotations

from functools import cache

from core.workspace_authority_v3.contracts import (
    DormantKeyAuthority,
    KeyFamily,
    KeyReference,
    KeyRingMetadata,
)
from core.workspace_authority_v3.identity import (
    AuthorityClaimProvenance,
    AuthorityDomainId,
    AuthorityEpoch,
    EnrollmentOperationId,
    HeadRevision,
    IncarnationId,
    OpaqueIdentity,
    RuntimeHomeId,
    WorkspaceEnrollmentProvenance,
    WorkspaceId,
    WorkspaceSequence,
)
from core.workspace_authority_v3.lifecycle import DormantWorkspaceStateOwner


@cache
def enrollment_root(seed: str) -> tuple[AuthorityDomainId, WorkspaceId, EnrollmentOperationId]:
    """Issue one coherent authority-domain/workspace/enrollment identity triple."""

    del seed
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    provenance = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return domain, WorkspaceId.from_enrollment(provenance), enrollment_operation


@cache
def owned_identity(identity_type: type[OpaqueIdentity], seed: str) -> OpaqueIdentity:
    """Return an owner-issued identity while retaining deterministic fixture equality by seed."""

    if identity_type is AuthorityDomainId:
        return enrollment_root(f"domain:{seed}")[0]
    if identity_type is WorkspaceId:
        return enrollment_root(f"workspace:{seed}")[1]
    return identity_type.new()


def ring_workspace_id() -> WorkspaceId:
    """Issue a fresh owned workspace identity to anchor one logical key ring."""

    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    provenance = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=EnrollmentOperationId.new(),
    )
    return WorkspaceId.from_enrollment(provenance)


def key_authority(
    metadata: KeyRingMetadata,
    material: bytes,
    *,
    workspace_id: WorkspaceId | None = None,
    authority_domain: AuthorityDomainId | str | None = None,
) -> DormantKeyAuthority:
    """Own test key material under the exact typed references in one metadata snapshot.

    Each call without an explicit workspace anchors a fresh logical ring, because one
    logical ring admits exactly one authority owner.  Without an explicit
    ``authority_domain`` the ring inherits the anchor workspace's own enrollment domain,
    so a ring that must authenticate records for a specific domain has to name it.
    """

    return DormantKeyAuthority(
        metadata,
        {
            KeyReference(item.family, item.key_id, item.generation): material
            for item in metadata.generations
            if item.family is not KeyFamily.JOURNAL_EFFECT_RECEIPT
        },
        workspace_id=ring_workspace_id() if workspace_id is None else workspace_id,
        authority_domain=authority_domain,
    )


def active_state_owner(
    seed: str,
    *,
    incarnation_id: IncarnationId | None = None,
    authority_epoch: AuthorityEpoch | None = None,
    head_revision: HeadRevision | None = None,
    workspace_sequence: WorkspaceSequence | None = None,
    runtime_home_owner: RuntimeHomeId | None = None,
) -> DormantWorkspaceStateOwner:
    """Create the unique lifecycle owner for a newly enrolled test workspace."""

    del seed
    domain = AuthorityDomainId.from_authority_claim(AuthorityClaimProvenance.establish())
    enrollment_operation = EnrollmentOperationId.new()
    enrollment = WorkspaceEnrollmentProvenance.establish(
        authority_domain_id=domain,
        enrollment_operation_id=enrollment_operation,
    )
    return DormantWorkspaceStateOwner.from_enrollment(
        enrollment=enrollment,
        incarnation_id=IncarnationId.new() if incarnation_id is None else incarnation_id,
        authority_epoch=AuthorityEpoch(1) if authority_epoch is None else authority_epoch,
        head_revision=HeadRevision(0) if head_revision is None else head_revision,
        workspace_sequence=(
            WorkspaceSequence(0) if workspace_sequence is None else workspace_sequence
        ),
        runtime_home_owner=RuntimeHomeId.new() if runtime_home_owner is None else runtime_home_owner,
    )
