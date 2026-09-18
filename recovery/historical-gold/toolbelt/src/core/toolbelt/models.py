"""Typed models for the toolbelt. NO field in this module may ever carry a secret value.

The invariant is structural, not procedural: there is no ``token``/``value``/``key`` field
anywhere, so repr()/json/asdict of any model-visible object cannot leak a secret by construction.
Free-text evidence fields are scrubbed through ``core.secret_redaction.redact_secrets`` at
construction time (see :func:`evidence`), because probe stderr is the one channel that could
echo secret material back at us.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.secret_redaction import redact_secrets


def evidence(text: str) -> str:
    """Sanitize a free-text evidence string before it enters a model-visible object."""
    return redact_secrets(str(text or ""))


class PresenceState(str, Enum):
    INSTALLED = "INSTALLED"
    MISSING = "MISSING"
    BROKEN = "BROKEN"                # executable found but crashes / unusable output
    WRONG_VERSION = "WRONG_VERSION"   # present but fails the requested constraint
    UNKNOWN = "UNKNOWN"


class ScopeState(str, Enum):
    AVAILABLE_GLOBALLY = "AVAILABLE_GLOBALLY"
    AVAILABLE_IN_PROJECT_ENV = "AVAILABLE_IN_PROJECT_ENV"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE_SERVICE = "UNREACHABLE_SERVICE"  # client fine, backing daemon/service down
    UNKNOWN = "UNKNOWN"


class AuthState(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    EXPIRED = "EXPIRED"
    WRONG_ACCOUNT = "WRONG_ACCOUNT"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


class PlanStatus(str, Enum):
    READY = "READY"                            # proven usable on THIS machine, now
    IMPLEMENTATION_AVAILABLE = "IMPLEMENTATION_AVAILABLE"  # works, but authorization not decided here
    INSTALLABLE = "INSTALLABLE"                # missing; an install plan exists (proposal only)
    BLOCKED = "BLOCKED"                        # hard blocker identified (auth, version, service)
    UNKNOWN = "UNKNOWN"                        # insufficient evidence — fail closed


class InstallCategory(str, Enum):
    PROJECT_LOCAL = "PROJECT_LOCAL"   # into repo .venv / node_modules — reversible with the repo
    USER_TOOL = "USER_TOOL"           # user-scope CLI (brew, pipx, cargo install)
    SYSTEM_TOOL = "SYSTEM_TOOL"       # needs elevated/system mutation — most conservative
    RUNTIME = "RUNTIME"               # language runtime itself
    SERVICE = "SERVICE"               # background daemon (colima, docker daemon)


@dataclass(frozen=True)
class ToolPresence:
    """What exists on the machine. Existence facts only."""

    tool_id: str
    presence: PresenceState = PresenceState.UNKNOWN
    scope: ScopeState = ScopeState.UNKNOWN
    health: HealthState = HealthState.UNKNOWN
    executable_path: str | None = None
    version: str | None = None
    source: str | None = None        # coarse origin class: "system", "homebrew", "project-venv", ...
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class CredentialHandle:
    """A HANDLE to a credential, never its value.

    ``credential_id`` names the credential inside the platform's store (``core.credential_store``
    / keychain). Only an execution authority may resolve the value behind the handle.
    """

    credential_id: str
    provider: str
    source: str                       # "keychain" | "gh-store" | "env-ref" | "vault" | "broker"
    status: AuthState = AuthState.UNKNOWN
    identity: str | None = None       # account identity as safely exposed by provider/CLI
    scopes: tuple[str, ...] = ()      # granted scopes/capabilities where the API exposes them

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class OperationalCapability:
    """What an installed tool can actually DO. Presence ≠ capability."""

    capability_id: str                # e.g. "github.repo.read", "python.test.pytest"
    status: PlanStatus
    implementation: str | None = None  # e.g. "gh", "git+ssh", ".venv/bin/pytest"
    credential: CredentialHandle | None = None
    identity: str | None = None       # bound account/author identity when provable
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    authorization: str = "NOT GRANTED BY TOOLBELT"

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class ToolCapability:
    tool_id: str
    presence: ToolPresence
    capabilities: tuple[OperationalCapability, ...] = ()
    credentials: tuple[CredentialHandle, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class RepoRef:
    path: str


@dataclass(frozen=True)
class RuntimeChoice:
    kind: str                         # "PROJECT_VENV" | "GLOBAL" | "NONE"
    python_executable: str | None = None
    version: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class InstallSpec:
    """Typed install proposal fields — the authoritative representation.

    A human-readable shell hint may be DERIVED from these; the string is never
    the source of truth, so an injected hint cannot smuggle shell syntax.
    """

    tool_id: str
    manager: str                      # "brew" | "pip-project" | "npm-dev" | ...
    package: str
    version_constraint: str | None = None
    scope: InstallCategory = InstallCategory.USER_TOOL
    expected_executable: str | None = None
    mutation_class: str = "INSTALL_PACKAGE"

    def command_hint(self) -> str:
        pin = f"=={self.version_constraint}" if self.version_constraint else "<pinned>"
        return {
            "brew": f"brew install {self.package}",
            "pip-project": f".venv/bin/python -m pip install {self.package}{pin}",
            "npm-dev": f"npm install --save-dev {self.package}@{pin}",
        }.get(self.manager, f"# unmanaged: install {self.package} ({self.scope.value})")


@dataclass(frozen=True)
class InstallPlan:
    """A PROPOSAL to mutate the machine. Never executed by the toolbelt."""

    tool_id: str
    category: InstallCategory
    command_hint: str                 # human-auditable hint DERIVED from spec
    pinned_version: str | None = None
    reversible: bool = False
    requires_user_approval: bool = True
    spec: InstallSpec | None = None   # typed authority behind the hint

    @classmethod
    def from_spec(cls, spec: InstallSpec) -> "InstallPlan":
        return cls(tool_id=spec.tool_id, category=spec.scope,
                   command_hint=spec.command_hint(), pinned_version=spec.version_constraint,
                   reversible=spec.scope is InstallCategory.PROJECT_LOCAL,
                   requires_user_approval=True, spec=spec)

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)


@dataclass(frozen=True)
class CapabilityNeed:
    capability: str                   # dotted id, e.g. "python.test.pytest"
    repository: RepoRef | None = None
    constraints: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPlan:
    need: CapabilityNeed
    status: PlanStatus
    executable: str | None = None
    runtime: RuntimeChoice | None = None
    environment: str | None = None    # e.g. "project:.venv", "global"
    credential_handle: CredentialHandle | None = None
    identity: str | None = None       # bound account/author identity when provable
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    install_plan: InstallPlan | None = None

    @property
    def authorization(self) -> str:
        return "NOT GRANTED BY TOOLBELT"

    def to_dict(self) -> dict[str, Any]:
        d = _safe_dict(self)
        d["authorization"] = self.authorization
        return d


@dataclass(frozen=True)
class ToolbeltSnapshot:
    """The model-visible answer. Structurally incapable of containing a raw secret."""

    repository: RepoRef | None
    tools: tuple[ToolCapability, ...]
    runtime: RuntimeChoice | None
    plan: tuple[ToolPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return _safe_dict(self)

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)


def _safe_dict(obj: Any) -> dict[str, Any]:
    """Serialize a frozen dataclass of safe fields only.

    Guard rail: if someone later adds a suspiciously-named field to a model, serialization
    refuses rather than silently leaking.
    """
    forbidden = {"value", "secret", "token", "password", "api_key", "private_key"}
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if f.name.lower() in forbidden:
            raise ValueError(f"refusing to serialize secret-like field {obj.__class__.__name__}.{f.name}")
        v = getattr(obj, f.name)
        if v is None:
            continue
        if isinstance(v, Enum):
            out[f.name] = v.value
        elif dataclasses.is_dataclass(v) and not isinstance(v, type):
            out[f.name] = _safe_dict(v)
        elif isinstance(v, tuple):
            out[f.name] = [
                _safe_dict(x) if dataclasses.is_dataclass(x) and not isinstance(x, type) else x for x in v
            ]
        else:
            out[f.name] = v
    return out


__all__ = [
    "AuthState",
    "CapabilityNeed",
    "CredentialHandle",
    "HealthState",
    "InstallCategory",
    "InstallPlan",
    "InstallSpec",
    "OperationalCapability",
    "PlanStatus",
    "PresenceState",
    "RepoRef",
    "RuntimeChoice",
    "ScopeState",
    "ToolCapability",
    "ToolPlan",
    "ToolbeltSnapshot",
    "evidence",
]
