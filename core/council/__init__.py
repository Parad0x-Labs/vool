"""The in-app council: role-based multi-model adjudication over a real problem.

Seats are ROLES, not personalities — the model behind a seat is replaceable, the role's
responsibility and information diet are not. Voting seats adjudicate; advisors report
without a vote. Round 1 is blind (every seat gathers its own evidence); later rounds are
cross-visible. One receipt-backed deterministic counterexample beats any vote count.

The council produces adjudications and candidates. It is NOT the authorization system:
promotion, merge, and spend stay with the operator, and a seat model that the operator
has not already price-accepted fails typed instead of self-authorizing the spend.

The council-mode FEATURES recovered 2026-08-29 from `experiment/council-mode` are exported
alongside the shipping design, not in place of it: an event-driven turn manager (typed EventBus
with a monotonic sequence), a quorum DAG, a sealed judge, structured claim identity, and truthful
cancellation. `CouncilOrchestrator` remains the entry point and the seat-based design is unchanged.
"""

from core.council.capsule import CapsuleMaterial, ContextCapsule, KernelFact, TaskInput
from core.council.claims import Claim, Delta, Dispute, extract_claims, extract_delta
from core.council.cost_ladder import (
    BudgetExhausted,
    CostPolicy,
    CostTier,
    DEFAULT_LADDER,
    ExhaustionReason,
    OperatorEscalation,
    SpendCeilings,
    SpendMeter,
    mint_operator_escalation,
    next_tier,
)
from core.council.gate0 import (
    GATE0_CHECK_ORDER,
    Gate0,
    Gate0Check,
    Gate0Decision,
    Gate0Facts,
    Gate0Reason,
)
from core.council.model_provenance import (
    ModelIdentity,
    ModelProvenanceError,
    ProvenanceRecord,
    collapse_by_family,
    independent_family_count,
    resolve_family,
)
from core.council.policy import (
    AdjudicationAuthorization,
    EscalationGate,
    EscalationPolicy,
    EscalationRefused,
    QUALIFYING_KINDS,
)
from core.council.runtime import (
    Ballot,
    ChallengeRecord,
    CouncilRuntime,
    CouncilTranscript,
    DoubleCommitRefused,
    KernelFactTampered,
    LiveSeatError,
    RoleViolation,
    SeatModel,
    SealedReceipt,
    Verdict,
    assemble_answer,
    verify_commit,
)
from core.council.seats import (
    ANSWERING_ROLES,
    CouncilSpec,
    CouncilSpecError,
    Role,
    SeatSpec,
    Tier,
)
from core.council.orchestrator import CouncilOrchestrator, CouncilRunError
from core.council.roles import ROLE_REGISTRY, RoleSpec, role_brief
from core.council.run_store import CouncilRunStore
from core.council.task_contract import (
    CompletionRecord,
    CounterexampleClaim,
    IntegrationGreen,
    IntegrationGreenRefused,
    MutationGrant,
    MutationPermission,
    MutationProof,
    MutationProofRefused,
    PromotionRefused,
    SeatEnvelope,
    TaskContract,
    TaskContractError,
    TaskDone,
    TaskDoneRefused,
    TaskSeat,
    close_obligation,
    close_task,
    mark_integration_green,
    mint_mutation_grant,
    verify_mutation_proof,
)
from core.council.task_dag import (
    DAGCycleRefused,
    DuplicateTaskRefused,
    OverlappingWriterRefused,
    StaleLeaseRefused,
    TaskDagError,
    TaskLedger,
    TaskStatus,
    WriterLease,
    paths_overlap,
    scopes_overlap,
)

__all__ = [
    "ANSWERING_ROLES",
    "AdjudicationAuthorization",
    "Ballot",
    "BudgetExhausted",
    "DAGCycleRefused",
    "DEFAULT_LADDER",
    "DuplicateTaskRefused",
    "ExhaustionReason",
    "GATE0_CHECK_ORDER",
    "Gate0",
    "Gate0Check",
    "Gate0Decision",
    "Gate0Facts",
    "Gate0Reason",
    "CapsuleMaterial",
    "ChallengeRecord",
    "Claim",
    "CompletionRecord",
    "ContextCapsule",
    "CounterexampleClaim",
    "CouncilOrchestrator",
    "CouncilRunError",
    "CouncilRunStore",
    "CouncilRuntime",
    "CouncilSpec",
    "CouncilSpecError",
    "CouncilTranscript",
    "CostPolicy",
    "CostTier",
    "Delta",
    "Dispute",
    "DoubleCommitRefused",
    "EscalationGate",
    "EscalationPolicy",
    "EscalationRefused",
    "IntegrationGreen",
    "IntegrationGreenRefused",
    "KernelFact",
    "KernelFactTampered",
    "ModelIdentity",
    "ModelProvenanceError",
    "MutationGrant",
    "MutationPermission",
    "MutationProof",
    "MutationProofRefused",
    "OperatorEscalation",
    "OverlappingWriterRefused",
    "PromotionRefused",
    "QUALIFYING_KINDS",
    "ROLE_REGISTRY",
    "Role",
    "RoleSpec",
    "RoleViolation",
    "SeatEnvelope",
    "SeatModel",
    "SeatSpec",
    "SpendCeilings",
    "SpendMeter",
    "StaleLeaseRefused",
    "TaskContract",
    "TaskContractError",
    "TaskDagError",
    "TaskDone",
    "TaskDoneRefused",
    "TaskInput",
    "TaskLedger",
    "TaskSeat",
    "TaskStatus",
    "Tier",
    "Verdict",
    "WriterLease",
    "assemble_answer",
    "close_obligation",
    "close_task",
    "collapse_by_family",
    "extract_claims",
    "extract_delta",
    "independent_family_count",
    "mark_integration_green",
    "mint_mutation_grant",
    "mint_operator_escalation",
    "next_tier",
    "paths_overlap",
    "resolve_family",
    "role_brief",
    "scopes_overlap",
    "verify_commit",
    "verify_mutation_proof",
]
