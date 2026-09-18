"""The kernel laws: invariants every lane above must satisfy, none may bypass.

Four modules, one law each. They interlock but import only downward (stdlib and each
other), never upward into lanes — a lane that wants to ship an answer comes DOWN here
to commit it, the kernel never reaches up.

Law 1 (`obligations`): a turn is a transaction over obligations; an answer can commit
only when every obligation is closed or explicitly declared unanswerable with a reason.
Law 2 (`evidence_types`): a live-world claim renders only with a machine-checked
evidence type whose reference resolves; an untyped claim is a type error, not a style note.
Law 3 (`capabilities`): a fork holds an explicit capability set; a tool call outside it
is denied with a receipt row, and tainted values cannot enter tool arguments uncountersigned.
Law 4 (`effects`): every nondeterministic effect runs through one recording interface,
so any turn replays deterministically and divergence is a named error.
"""
from core.kernel.capabilities import (
    CapabilityDenied,
    CapabilitySet,
    ForkContext,
    TaintedValue,
    check_tool_call,
)
from core.kernel.compartments import (
    CompartmentLeak,
    MalformedCompartment,
    NeedToKnowLane,
    leak_scan,
    minimized_view,
    parse_compartments,
)
from core.kernel.continuation import (
    ContinuationBundle,
    ContinuationReceipt,
    ContinuationRunner,
    TamperedBundleError,
    open_continuation,
    seal_bundle,
)
from core.kernel.counterfactual import (
    CounterfactualBundle,
    CounterfactualReceipt,
    CounterfactualRunner,
    Premise,
    open_counterfactual,
    seal_counterfactual,
)
from core.kernel.effects import DivergenceError, EffectJournal, EffectRunner
from core.kernel.flow import (
    DisclosureSpec,
    Private,
    PublicArtifact,
    cloud_view,
    disclose,
)
from core.kernel.council_flow import (
    CouncilSeat,
    EvidenceNotInScope,
    EvidenceVault,
    Verdict,
    build_capsule,
    export_public_claims,
)
from core.kernel.envelope import (
    BudgetExhausted,
    ConformanceProof,
    Envelope,
    Mandate,
    OutsideEnvelope,
    Revoked,
    verify_proof,
)
from core.kernel.boundary import (
    CapabilitySpec,
    ExecutionGate,
    ExecutionLedger,
    IntentNotResolvable,
    ResourceIdentity,
    UnknownUnresolved,
    build_gate,
    conformance_v2,
    resolve,
)
from core.kernel.broker import (
    EffectBroker,
    FileReadHandle,
    HandleRevoked,
    HttpHandle,
    OutsideHandleScope,
)
from core.kernel.wasm_sandbox import (
    SandboxInstantiationError,
    WasmPluginSandbox,
    build_plugin_wat,
)
from core.kernel.plugin_lifecycle import (
    AdmissionTicket,
    ConcurrentPolicyChange,
    LifecycleRegistry,
    PlatformPolicy,
    StaleExecutionRefused,
    TrustLineage,
)
from core.kernel.plugin_contract import (
    PluginManifest,
    PluginPackage,
    PluginRegistry,
    SigningError,
    sign_package,
    verify_package,
)
from core.kernel.evidence_types import (
    EvidenceTypeError,
    TypedClaim,
    render_typed_answer,
    validate_claims,
)
from core.kernel.stability import (
    StabilitySeal,
    UnstableCommitRefused,
    WorldVerdict,
    bisect_flip,
    classify_sweep,
    gate_commit,
)
from core.kernel.obligations import (
    CommitRefused,
    CommitResult,
    Obligation,
    TurnTransaction,
)

__all__ = [
    "CapabilityDenied",
    "CapabilitySet",
    "EffectBroker",
    "CouncilSeat",
    "CommitRefused",
    "CommitResult",
    "ContinuationBundle",
    "ContinuationReceipt",
    "ContinuationRunner",
    "CounterfactualBundle",
    "CounterfactualReceipt",
    "CounterfactualRunner",
    "DivergenceError",
    "Envelope",
    "ExecutionGate",
    "EvidenceNotInScope",
    "EvidenceVault",
    "FileReadHandle",
    "HttpHandle",
    "EffectJournal",
    "EffectRunner",
    "EvidenceTypeError",
    "DisclosureSpec",
    "ForkContext",
    "Mandate",
    "MalformedCompartment",
    "NeedToKnowLane",
    "Obligation",
    "Private",
    "PublicArtifact",
    "StabilitySeal",
    "UnstableCommitRefused",
    "WorldVerdict",
    "bisect_flip",
    "classify_sweep",
    "gate_commit",
    "Premise",
    "TaintedValue",
    "TurnTransaction",
    "TypedClaim",
    "Verdict",
    "build_capsule",
    "check_tool_call",
    "render_typed_answer",
    "validate_claims",
]
