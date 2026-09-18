"""Typed CommandSpec — the one declaration every operator surface projects from.

Laws (VOOL Operator Command Centre Blueprint §4.1, §4.2):
- every operator-invocable action is declared once, here;
- input/output are typed dataclasses — raw dict schemas are ``--check`` failures;
- availability is machine evidence (predicate returns ``(bool, reason)``),
  constant-true predicates on mutating commands are ``--check`` failures;
- permission is enforced at dispatch, never by handler convention;
- handlers bind by owned dotted path, resolved lazily so import failures are
  ``--check`` findings (unbound), not import crashes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

SIDE_EFFECT_CLASSES = (
    "read_only",
    "idempotent_write",
    "mutating",
    "destructive",
    "spend",
    "external_send",
)

LIFECYCLE_STATES = ("stable", "preview", "developer", "deprecated")

#: typed exit-code vocabulary (envelope.py owns the int mapping)
FAULT_CODES = (
    "ok",
    "usage",
    "unknown_command",
    "unavailable",
    "permission_required",
    "permission_denied",
    "conflict",
    "fault_tool",
    "fault_provider",
    "fault_validation",
    "fault_synthesis",
    "fault_partial",
    "fault_cancelled",
    "internal",
)


# ---------------------------------------------------------------------------
# Permission requirement — enforced by the dispatch seam, not by handlers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenRead:
    """Read-only command; the transport being operator-local is not authorization,
    but read-only commands carry no side effect to gate."""


@dataclass(frozen=True)
class ApprovalGate:
    """Mutation that requires an explicit operator approval before dispatch.

    ``verifier`` is a dotted path ``module:function`` receiving
    ``(typed_input, context)`` and returning one of:

    - ``ApprovalDecision(required=True, approval_id=...)`` → exit 20, approval minted
    - ``ApprovalDecision(required=False)`` → gate passed, handler runs
    """

    kind: str
    verifier: str


@dataclass(frozen=True)
class OperatorAuthority:
    """Mutation gated on an operator token the model cannot mint or carry
    (the Blackbox authority pattern). ``verifier`` is ``module:function``
    receiving ``(typed_input, context)`` and returning ``AuthorityDecision``."""

    kind: str
    verifier: str


PermissionRequirement = OpenRead | ApprovalGate | OperatorAuthority


@dataclass(frozen=True)
class ApprovalDecision:
    required: bool
    approval_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AuthorityDecision:
    granted: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Availability — machine evidence, never a constant claim
# ---------------------------------------------------------------------------

AvailabilityFn = Callable[[Mapping[str, Any]], tuple[bool, str]]
#: dotted path to AvailabilityFn; registry resolves lazily so a broken probe
#: is an ``--check`` finding (orphaned), not an import crash.
AvailabilityRef = str


@dataclass(frozen=True)
class Availability:
    probe: AvailabilityRef
    #: evidence key the probe's reason is filed under in fault.detail


# ---------------------------------------------------------------------------
# Fault binding — failure mode → typed fault code + remediation pointers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FaultBinding:
    when: str                    # short failure-mode name, e.g. "chain_corrupt"
    fault_code: str              # member of FAULT_CODES
    remediation: tuple[str, ...] = ()  # typeable command ids or literal invocations


# ---------------------------------------------------------------------------
# Handler binding — owned module path, resolved at dispatch/check time
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Handler:
    dotted: str                  # "core.blackbox.operator:status"
    #: mutating handlers must return receipts; check enforces via effects class


# ---------------------------------------------------------------------------
# Breadcrumbs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NextAction:
    command_id: str              # must exist in the registry — else --check orphan
    label: str


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandSpec:
    command_id: str              # stable dotted id; never renamed, only deprecated
    group: str                   # registry group id, e.g. "blackbox"
    description: str             # one line, required — empty = --check failure
    aliases: tuple[str, ...] = ()
    input_schema: type | None = None    # typed dataclass; None = no input accepted
    output_schema: type | None = None   # typed dataclass
    effects: str = "read_only"          # SIDE_EFFECT_CLASSES member
    permission: PermissionRequirement = field(default_factory=OpenRead)
    capabilities: frozenset[str] = frozenset()   # capability/named-scope ids consumed
    availability: Availability | None = None    # None = structural truth (always on)
    fault_bindings: tuple[FaultBinding, ...] = ()
    exit_codes: tuple[int, ...] = ()    # typed vocabulary the handler may emit
    handler: Handler | None = None      # None = unbound, --check failure
    lifecycle: str = "stable"           # LIFECYCLE_STATES member
    next_actions: tuple[NextAction, ...] = ()
    platforms: frozenset[str] = frozenset({"macos", "linux", "windows"})
    #: ONLY explicitly model-offerable commands project into the model tool
    #: vocabulary (operator.command.* contracts). Operator-only commands stay
    #: invisible to the model even though they are registered.
    model_offerable: bool = False


@dataclass(frozen=True)
class GroupSpec:
    group_id: str
    description: str
    aliases: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Execution results a handler may return
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HandlerOk:
    data: Any = None             # instance of output_schema (or None)
    summary: str = ""
    receipts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class HandlerFault:
    fault_code: str              # FAULT_CODES member (non-"ok")
    summary: str
    detail: Mapping[str, Any] | None = None
