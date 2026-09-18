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
from core.kernel.effects import DivergenceError, EffectJournal, EffectRunner
from core.kernel.evidence_types import (
    EvidenceTypeError,
    TypedClaim,
    render_typed_answer,
    validate_claims,
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
    "CommitRefused",
    "CommitResult",
    "DivergenceError",
    "EffectJournal",
    "EffectRunner",
    "EvidenceTypeError",
    "ForkContext",
    "Obligation",
    "TaintedValue",
    "TurnTransaction",
    "TypedClaim",
    "check_tool_call",
    "render_typed_answer",
    "validate_claims",
]
