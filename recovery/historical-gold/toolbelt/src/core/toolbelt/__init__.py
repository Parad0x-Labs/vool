"""VOOL TOOLBELT — typed machine-capability layer.

Answers: "WHAT CAN THIS MACHINE ACTUALLY DO RIGHT NOW?" for agents such as Git Ninja.

Laws enforced here:

* SECRETS ARE HANDLES, NOT CONTEXT — no dataclass in this package has a field that can hold a
  secret value. Credential discovery yields ``CredentialHandle`` objects (name/provider/source/
  status/scopes only); resolving the actual value is the platform's job (``core.credential_store``
  behind an execution gate), never the toolbelt's.
* TOOLS PROVIDE IMPLEMENTATION. PLATFORM PROVIDES AUTHORITY. — resolution ends at
  ``implementation available``; authorization is explicitly out of scope and marked as such.
* Fail closed: insufficient evidence resolves to ``UNKNOWN``, never to a guess.

Reused primitives (not duplicated): ``core.credential_store`` (secret storage),
``core.cloud_credential_broker`` (BYOK resolution), ``core.secret_redaction.redact_secrets``
(scrubbing probe stderr before it becomes evidence text).
"""
from core.toolbelt.models import (
    AuthState,
    CapabilityNeed,
    CredentialHandle,
    HealthState,
    InstallCategory,
    InstallPlan,
    OperationalCapability,
    PlanStatus,
    PresenceState,
    RepoRef,
    RuntimeChoice,
    ScopeState,
    ToolCapability,
    ToolPlan,
    ToolbeltSnapshot,
)
from core.toolbelt.version_spec import satisfies

__all__ = [
    "AuthState",
    "CapabilityNeed",
    "CredentialHandle",
    "HealthState",
    "InstallCategory",
    "InstallPlan",
    "OperationalCapability",
    "PlanStatus",
    "PresenceState",
    "RepoRef",
    "RuntimeChoice",
    "ScopeState",
    "ToolCapability",
    "ToolPlan",
    "ToolbeltSnapshot",
    "satisfies",
]
