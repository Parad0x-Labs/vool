"""Resolve a typed CapabilityNeed into a ToolPlan.

Resolution ends at "implementation available". Authorization is a platform concern and is
never granted here. Insufficient evidence fails closed to UNKNOWN.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.toolbelt.credentials import gh_credential_handle
from core.toolbelt.inventory import probe_tool, resolve_runtime
from core.toolbelt.models import (
    AuthState,
    CapabilityNeed,
    HealthState,
    InstallCategory,
    InstallPlan,
    InstallSpec,
    PlanStatus,
    PresenceState,
    RepoRef,
    ToolPlan,
)
from core.toolbelt.probes import ProbeRunner
from core.toolbelt.version_spec import satisfies

# Missing-tool install proposals: typed specs (PROPOSAL ONLY — never executed).
_INSTALL_SPECS: dict[str, InstallSpec] = {
    "jq": InstallSpec(tool_id="jq", manager="brew", package="jq", expected_executable="jq"),
    "gh": InstallSpec(tool_id="gh", manager="brew", package="gh", expected_executable="gh"),
    "ninja": InstallSpec(tool_id="ninja", manager="brew", package="ninja"),
    "pipx": InstallSpec(tool_id="pipx", manager="brew", package="pipx"),
    "ruff": InstallSpec(tool_id="ruff", manager="pip-project", package="ruff",
                        scope=InstallCategory.PROJECT_LOCAL, expected_executable="ruff"),
    "eslint": InstallSpec(tool_id="eslint", manager="npm-dev", package="eslint",
                          scope=InstallCategory.PROJECT_LOCAL),
}


def _version_of(tool_id: str, runner: ProbeRunner) -> str | None:
    return probe_tool(tool_id, runner).version


def resolve(need: CapabilityNeed, runner: ProbeRunner | None = None) -> ToolPlan:
    runner = runner or ProbeRunner()
    cap = need.capability
    repo = need.repository
    constraints = dict(need.constraints or {})
    repo_path = repo.path if repo else os.getcwd()

    if cap.startswith("python.") or cap == "python":
        return _resolve_python(cap, constraints, repo_path, runner)

    if cap == "github.repo.read" or cap.startswith("github."):
        return _resolve_github(cap, runner)

    if cap.startswith("docker."):
        return _resolve_docker(cap, runner)

    if cap.startswith("git."):
        return _resolve_git(cap, constraints, runner)

    if cap.startswith("signing") or cap == "git.commit.sign":
        return _resolve_signing(runner)

    # Generic single-tool capability: "<tool>" or "<tool>.<op>"
    tool_id = cap.split(".", 1)[0]
    presence = probe_tool(tool_id, runner)
    if presence.presence is not PresenceState.INSTALLED:
        if presence.presence is PresenceState.WRONG_VERSION:
            return ToolPlan(need=need, status=PlanStatus.BLOCKED,
                            executable=presence.executable_path, blockers=("wrong version",))
        spec = _INSTALL_SPECS.get(tool_id)
        install = InstallPlan.from_spec(spec) if spec else None
        return ToolPlan(
            need=need, status=PlanStatus.INSTALLABLE if install else PlanStatus.UNKNOWN,
            executable=None, blockers=(f"{tool_id} missing",), install_plan=install,
        )
    version = presence.version
    req = constraints.get(tool_id) or constraints.get("version")
    if req and not satisfies(version, req):
        return ToolPlan(
            need=need, status=PlanStatus.BLOCKED, executable=presence.executable_path,
            evidence=(f"installed {tool_id} {version}",),
            blockers=(f"version mismatch: have {version}, need {req} — installed != compatible",),
        )
    return ToolPlan(
        need=need, status=PlanStatus.IMPLEMENTATION_AVAILABLE,
        executable=presence.executable_path, environment="global",
        evidence=(f"{tool_id} {version} at {presence.executable_path}",),
    )


def _resolve_python(cap: str, constraints: dict[str, str], repo_path: str,
                    runner: ProbeRunner) -> ToolPlan:
    runtime = resolve_runtime(repo_path, runner)
    if runtime.kind != "PROJECT_VENV":
        return ToolPlan(need=_with_repo(cap, repo_path), status=PlanStatus.UNKNOWN,
                        runtime=runtime,
                        blockers=(f"no project venv; refusing to trust global runtime ({runtime.reason})",))
    req = constraints.get("python")
    ev = [f"project .venv python {runtime.version} at {runtime.python_executable}"]
    blockers: list[str] = []
    if req and not satisfies(runtime.version, req):
        blockers.append(f"python mismatch: project venv has {runtime.version}, need {req}")
    inner = cap[len("python."):] if "." in cap else ""
    if inner:
        module = inner.split(".")[-1]  # "python.lint.ruff" probes ruff, not lint
        result = runner.run([str(Path(repo_path) / ".venv" / "bin" / "python"), "-m", module, "--version"])
        if not result.ok:
            blockers.append(f".venv lacks {module}: {result.stderr.strip()[:100]}")
        else:
            ev.append(f".venv provides {module}: {(result.stdout or result.stderr).strip().splitlines()[0][:60]}")
    if blockers:
        return ToolPlan(need=_with_repo(cap, repo_path), status=PlanStatus.BLOCKED,
                        runtime=runtime, environment=f"project:{repo_path}/.venv",
                        evidence=tuple(ev), blockers=tuple(blockers))
    return ToolPlan(need=_with_repo(cap, repo_path), status=PlanStatus.READY,
                    executable=runtime.python_executable, runtime=runtime,
                    environment=f"project:{repo_path}/.venv", evidence=tuple(ev))


def _resolve_github(cap: str, runner: ProbeRunner) -> ToolPlan:
    presence = probe_tool("gh", runner)
    handle = gh_credential_handle(runner)
    blockers: list[str] = []
    if presence.presence is not PresenceState.INSTALLED:
        return ToolPlan(need=CapabilityNeed(capability=cap), status=PlanStatus.UNKNOWN,
                        blockers=("gh missing",))
    if handle is None or handle.status is not AuthState.AUTHENTICATED:
        blockers.append("gh installed but unauthenticated — executable != capability")
    # Prove repo read only when run inside an actual repo with a remote.
    if cap == "github.repo.read" and not blockers:
        result = runner.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        if result.ok and result.stdout.strip():
            ev = (f"read repo '{result.stdout.strip()}' via gh as {handle.identity}",)
        else:
            blockers.append(f"repo read failed: {result.stderr.strip()[:100]}")
    else:
        ev = ()
    write_ops = any(s in handle.scopes for s in ("repo", "workflow")) if handle else False
    if cap in {"github.repo.create", "github.push"} and not blockers:
        # Implementation exists; authorization is NOT ours. Report honestly as unknown-until-gated.
        ev = ev + (f"scopes grant write primitives ({'yes' if write_ops else 'unknown'}); "
                   "authorization decided by ExecutionGate, not toolbelt",)
    status = PlanStatus.BLOCKED if blockers else (
        PlanStatus.READY if cap == "github.repo.read" else PlanStatus.IMPLEMENTATION_AVAILABLE)
    return ToolPlan(need=CapabilityNeed(capability=cap), status=status,
                    executable=presence.executable_path, credential_handle=handle,
                    identity=handle.identity if handle else None,
                    evidence=ev, blockers=tuple(blockers))


def _resolve_docker(cap: str, runner: ProbeRunner) -> ToolPlan:
    presence = probe_tool("docker", runner)
    if presence.presence is not PresenceState.INSTALLED:
        return ToolPlan(need=CapabilityNeed(capability=cap), status=PlanStatus.UNKNOWN,
                        blockers=("docker client missing",))
    daemon = runner.run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if not daemon.ok:
        return ToolPlan(
            need=CapabilityNeed(capability=cap), status=PlanStatus.BLOCKED,
            executable=presence.executable_path,
            evidence=(f"client {presence.version} present",),
            blockers=(f"CLIENT_PRESENT but SERVICE_UNAVAILABLE: {daemon.stderr.strip()[:120]}",
                      "do not start/restart services automatically"),
        )
    return ToolPlan(need=CapabilityNeed(capability=cap), status=PlanStatus.READY,
                    executable=presence.executable_path,
                    evidence=(f"daemon up: {daemon.stdout.strip()[:40]}",))


def _resolve_git(cap: str, constraints: dict[str, str], runner: ProbeRunner) -> ToolPlan:
    base = resolve(CapabilityNeed("git"), runner) if cap != "git" else None
    plan = base if base else ToolPlan(need=CapabilityNeed(capability=cap), status=PlanStatus.UNKNOWN)
    if plan.status not in (PlanStatus.IMPLEMENTATION_AVAILABLE,):
        return plan
    req = constraints.get("git")
    if req and not satisfies(plan_exec_version(plan, runner), req):
        return ToolPlan(need=CapabilityNeed(capability=cap), status=PlanStatus.BLOCKED,
                        executable=plan.executable,
                        blockers=(f"git version mismatch: need {req}",))
    if cap == "git.commit.sign":
        return _resolve_signing(runner, git_plan=plan)
    return plan


def plan_exec_version(plan: ToolPlan, runner: ProbeRunner) -> str | None:
    if not plan.executable:
        return None
    return extract_version_safe(plan.executable, runner)


def extract_version_safe(executable: str, runner: ProbeRunner) -> str | None:
    from core.toolbelt.probes import extract_version

    result = runner.run([executable, "--version"])
    return extract_version("git", result) if result.ok else None


def _resolve_signing(runner: ProbeRunner, git_plan: ToolPlan | None = None) -> ToolPlan:
    fmt = runner.run(["git", "config", "--get", "gpg.format"])
    signkey = runner.run(["git", "config", "--get", "user.signingkey"])
    configured = fmt.ok and fmt.stdout.strip()
    if not configured:
        return ToolPlan(need=CapabilityNeed(capability="git.commit.sign"),
                        status=PlanStatus.UNKNOWN,
                        executable=git_plan.executable if git_plan else None,
                        evidence=("commit signing not configured in git config",),
                        blockers=("no gpg.format/signingkey configured — CONFIGURED ≠ VERIFIED, "
                                  "and absence here means UNKNOWN",))
    return ToolPlan(need=CapabilityNeed(capability="git.commit.sign"),
                    status=PlanStatus.IMPLEMENTATION_AVAILABLE,
                    evidence=(f"signing format={fmt.stdout.strip()}, key configured",),
                    blockers=("signature creation not exercised (mutation); VERIFIED requires a real commit",))


def _with_repo(cap: str, repo_path: str) -> CapabilityNeed:
    return CapabilityNeed(capability=cap, repository=RepoRef(path=repo_path))


__all__ = ["resolve"]
