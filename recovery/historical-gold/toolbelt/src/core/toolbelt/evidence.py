"""Snapshot identity, freshness and revalidation (Pass #2).

Laws:
* A capability SNAPSHOT MAY GO STALE — every resolved plan carries typed evidence
  facts plus an ``observed_at`` timestamp and a content-derived ``plan_id``.
* Revalidation compares FRESH probes against the recorded evidence and fails
  closed: any drift means STALE / UNAVAILABLE / IDENTITY_CHANGED — never a silent
  "still fine".
* Executables bind to their realpath at discovery; PATH changes after resolution
  cannot silently redirect an operation.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from core.toolbelt.exe_identity import fast_identity
from core.toolbelt.inventory import probe_tool, resolve_runtime
from core.toolbelt.models import PresenceState, RepoRef, ToolPlan
from core.toolbelt.probes import ProbeRunner


class DriftKind(str, Enum):
    READY = "READY"                        # re-probed evidence matches the snapshot
    STALE = "STALE"                        # tool/runtime facts changed
    UNAVAILABLE = "UNAVAILABLE"            # something the plan depended on is gone/down
    IDENTITY_CHANGED = "IDENTITY_CHANGED"  # bound account/credential identity changed


@dataclass(frozen=True)
class EvidenceFact:
    """One typed, secretless observation the plan depends on."""

    subject: str        # "tool:gh", "runtime", "credential:github:X", "service:docker"
    property: str       # "realpath" | "version" | "identity" | "daemon_up"
    value: str

    def key(self) -> str:
        return f"{self.subject}.{self.property}"


def _capability_tool(capability: str) -> str | None:
    if capability.startswith("github."):
        return "gh"
    if capability.startswith("docker."):
        return "docker"
    if capability.startswith("git"):
        return "git"
    return None


def collect_plan_evidence(plan: ToolPlan, runner: ProbeRunner,
                          repo_path: str | None = None) -> tuple[EvidenceFact, ...]:
    """Typed evidence for one resolved plan: exact executable realpath, version,
    runtime path/version, credential identity."""
    facts: list[EvidenceFact] = []
    tool = _capability_tool(plan.need.capability)
    if tool:
        p = probe_tool(tool, runner)
        if p.executable_path:
            facts.append(EvidenceFact(f"tool:{tool}", "realpath",
                                      os.path.realpath(p.executable_path)))
            # FAST FRESHNESS layer: dev/ino/size/mtime_ns detect same-path byte
            # replacement that a realpath+version pair would miss (Pass #3).
            ident = fast_identity(p.executable_path)
            if ident is not None:
                facts += [EvidenceFact(f"execid:{p.executable_path}", prop, val)
                          for prop, val in ident.facts(p.executable_path)]
        facts.append(EvidenceFact(f"tool:{tool}", "version", p.version or ""))
    if plan.runtime and plan.runtime.python_executable:
        facts.append(EvidenceFact("runtime", "path",
                                  os.path.realpath(plan.runtime.python_executable)))
        if plan.runtime.version:
            facts.append(EvidenceFact("runtime", "version", plan.runtime.version))
    h = plan.credential_handle
    if h is not None:
        if h.identity:
            facts.append(EvidenceFact(f"credential:{h.credential_id}", "identity", h.identity))
        facts.append(EvidenceFact(f"credential:{h.credential_id}", "status", h.status.value))
    return tuple(facts)


@dataclass(frozen=True)
class ToolPlanRef:
    """What Git Ninja / a plugin holds instead of raw discovery freedom.

    Carries NO secrets, NO environment dumps — just the plan's fingerprint so the
    execution boundary can ask for revalidation of exactly this resolution.
    """

    plan_id: str
    observed_at: str                       # UTC ISO-8601
    evidence_keys: tuple[str, ...]         # fact keys only, values stay in the broker side
    capability: str
    repository: RepoRef | None = None

    @classmethod
    def freeze(cls, plan: ToolPlan, runner: ProbeRunner) -> tuple["ToolPlanRef", tuple[EvidenceFact, ...]]:
        facts = collect_plan_evidence(plan, runner)
        digest_src = "\n".join(sorted(f"{f.key()}={f.value}" for f in facts))
        plan_id = hashlib.sha256(digest_src.encode()).hexdigest()[:16]
        ref = cls(plan_id=plan_id,
                  observed_at=datetime.now(UTC).isoformat(timespec="seconds"),
                  evidence_keys=tuple(sorted({f.key() for f in facts})),
                  capability=plan.need.capability,
                  repository=plan.need.repository)
        return ref, facts


@dataclass(frozen=True)
class RevalidationResult:
    status: DriftKind
    drifted: tuple[str, ...] = ()          # human-readable drift explanations
    checked_at: str = ""

    @property
    def may_proceed_to_gate(self) -> bool:
        """Only a fully-fresh plan is even ELIGIBLE for the authorization boundary."""
        return self.status is DriftKind.READY


def revalidate(ref: ToolPlanRef | None, facts: tuple[EvidenceFact, ...],
               runner: ProbeRunner) -> RevalidationResult:
    """Re-probe the world and diff it against the frozen evidence.

    The authoritative comparison is by fact KEY; a changed VALUE for the same key
    is drift. A missing subject (binary removed, credential gone, daemon down) is
    UNAVAILABLE. An identity-value change is IDENTITY_CHANGED.
    """
    old = {f.key(): f.value for f in facts}
    drifted: list[str] = []
    status = DriftKind.READY

    def demote(new: DriftKind) -> None:
        nonlocal status
        order = [DriftKind.READY, DriftKind.STALE, DriftKind.IDENTITY_CHANGED, DriftKind.UNAVAILABLE]
        if order.index(new) > order.index(status):
            status = new

    for key, old_value in sorted(old.items()):
        subject, prop = key.rsplit(".", 1)
        fresh = _probe_fact(subject, prop, runner,
                            repo_path=ref.repository.path
                            if (ref and ref.repository) else None)
        if fresh is None:
            drifted.append(f"{key}: gone ({old_value[:40]})")
            demote(DriftKind.UNAVAILABLE)
        elif fresh != old_value:
            label = "identity changed" if prop == "identity" else f"{prop} changed"
            drifted.append(f"{key}: {label} ({old_value[:40]} -> {fresh[:40]})")
            demote(DriftKind.IDENTITY_CHANGED if prop == "identity" else DriftKind.STALE)

    # Service-backed tools also re-check daemon liveness on revalidation.
    if any(k.startswith("tool:docker") for k in old):
        daemon = runner.run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if not daemon.ok:
            drifted.append("service:docker.daemon_up: down")
            demote(DriftKind.UNAVAILABLE)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    return RevalidationResult(status=status, drifted=tuple(drifted), checked_at=now)


def _probe_fact(subject: str, prop: str, runner: ProbeRunner,
                repo_path: str | None = None) -> str | None:
    """Fresh observation for one evidence key; None means the subject is gone."""
    from core.toolbelt.credentials import gh_credential_handle

    if subject.startswith("execid:"):
        # Fresh fast-fingerprint of the exact frozen path; None -> file gone.
        # Paths may contain dots, so the property is matched by known suffix.
        raw = subject.split(":", 1)[1]
        ident = fast_identity(raw.rsplit(".", 1)[0])  # props never contain dots
        if ident is None:
            return None
        return dict(ident.facts("")).get(prop)
    if subject.startswith("tool:"):
        p = probe_tool(subject.split(":", 1)[1], runner)
        if p.presence is not PresenceState.INSTALLED:
            return None
        return {"realpath": p.executable_path and os.path.realpath(p.executable_path),
                "version": p.version or ""}.get(prop)
    if subject == "runtime":
        rt = resolve_runtime(repo_path or os.getcwd(), runner)
        return {"path": rt.python_executable and os.path.realpath(rt.python_executable),
                "version": rt.version or ""}.get(prop)
    if subject.startswith("credential:"):
        from core.toolbelt.identities import github_identities

        cid = subject.split(":", 1)[1]
        handle = gh_credential_handle(runner)
        handles = [handle] if handle else []
        match = next((h for h in handles if h.credential_id == cid), None)
        if match is not None:
            return {"identity": match.identity or "",
                    "status": match.status.value}.get(prop)
        # The frozen credential is gone. If SOME other account is now active,
        # that is an identity SWITCH, not mere unavailability.
        others = [h for h in github_identities(runner) if h.credential_id != cid]
        if others:
            return {"identity": f"SWITCHED:{others[0].identity or others[0].credential_id}",
                    "status": "SWITCHED_ACCOUNT"}.get(prop)
        return None
    return None


def build_exec_argv(executable_path: str, args: tuple[str, ...]) -> list[str]:
    """The ONLY sanctioned way to turn a plan into a command.

    argv contains the frozen realpath and literal arguments — never environment
    values, never credential material. The execution boundary injects secrets
    OUTSIDE this structure.
    """
    return [str(executable_path), *args]


__all__ = [
    "DriftKind", "EvidenceFact", "RevalidationResult", "ToolPlanRef",
    "build_exec_argv", "collect_plan_evidence", "revalidate",
]
