"""Build the typed machine inventory and project-environment resolution."""
from __future__ import annotations

from pathlib import Path

from core.toolbelt.models import (
    HealthState,
    PresenceState,
    RuntimeChoice,
    ScopeState,
    ToolCapability,
    ToolPresence,
)
from core.toolbelt.probes import (
    VERSION_COMMANDS,
    ProbeRunner,
    classify_source,
    extract_version,
    project_venv_python,
)

INVENTORY_TOOLS = (
    "git", "gh", "python3", "uv", "pip", "pipx", "node", "npm", "pnpm", "yarn",
    "bun", "go", "cargo", "rustc", "docker", "make", "cmake", "ninja",
    "xcodebuild", "swift", "pytest", "ruff", "eslint", "jq", "curl",
)


def probe_tool(tool_id: str, runner: ProbeRunner) -> ToolPresence:
    """One tool's existence/health facts. PATH presence alone is NOT health."""
    path = runner.which(tool_id)
    if path is None:
        return ToolPresence(tool_id=tool_id, presence=PresenceState.MISSING,
                            scope=ScopeState.NONE, source=None,
                            notes=("not on PATH",))
    argv = list(VERSION_COMMANDS.get(tool_id, [tool_id, "--version"]))
    result = runner.run(argv)
    if result.returncode == 127:
        return ToolPresence(tool_id=tool_id, presence=PresenceState.MISSING,
                            scope=ScopeState.UNKNOWN, executable_path=path,
                            notes=("vanished between which() and run()",))
    if not result.ok:
        return ToolPresence(
            tool_id=tool_id, presence=PresenceState.BROKEN, scope=ScopeState.AVAILABLE_GLOBALLY,
            health=HealthState.DEGRADED, executable_path=path, source=classify_source(path),
            notes=(f"version probe failed rc={result.returncode}: {result.stderr.strip()[:120]}",),
        )
    version = extract_version(tool_id, result)
    return ToolPresence(
        tool_id=tool_id, presence=PresenceState.INSTALLED, scope=ScopeState.AVAILABLE_GLOBALLY,
        health=HealthState.HEALTHY, executable_path=path, version=version,
        source=classify_source(path),
    )


def resolve_runtime(repo_path: str | Path, runner: ProbeRunner) -> RuntimeChoice:
    """Project-specific execution wins over global. The wrong-Python trap, answered."""
    venv_python = project_venv_python(repo_path)
    if venv_python is not None:
        result = runner.run([str(venv_python), "--version"])
        version = extract_version("python3", result) if result.ok else None
        if version:
            global_result = runner.run(["python3", "--version"])
            global_version = extract_version("python3", global_result)
            note = ""
            if global_version and global_version != version:
                note = f" (global python3 is {global_version} — NOT release evidence)"
            return RuntimeChoice(kind="PROJECT_VENV", python_executable=str(venv_python),
                                 version=version, reason=f"repo .venv python{note}")
        return RuntimeChoice(kind="NONE", reason="repo .venv exists but its python is broken")
    result = runner.run(["python3", "--version"])
    version = extract_version("python3", result) if result.ok else None
    if version:
        return RuntimeChoice(kind="GLOBAL", python_executable=runner.which("python3"),
                             version=version, reason="no project .venv found")
    return RuntimeChoice(kind="NONE", reason="no usable python found")


def build_inventory(repo_path: str | Path, runner: ProbeRunner | None = None,
                    tools: tuple[str, ...] = INVENTORY_TOOLS) -> tuple[ToolCapability, ...]:
    runner = runner or ProbeRunner()
    out = []
    for tool_id in tools:
        presence = probe_tool(tool_id, runner)
        # A tool that only lives inside the repo's environment reports the project scope.
        local_dir = Path(repo_path) / ".venv" / "bin" / tool_id
        if presence.presence is PresenceState.MISSING and local_dir.exists():
            presence = ToolPresence(
                tool_id=tool_id, presence=PresenceState.INSTALLED,
                scope=ScopeState.AVAILABLE_IN_PROJECT_ENV, health=HealthState.UNKNOWN,
                executable_path=str(local_dir), source="project-venv",
                notes=("present in project env only",),
            )
        out.append(ToolCapability(tool_id=tool_id, presence=presence))
    return tuple(out)


__all__ = ["INVENTORY_TOOLS", "build_inventory", "probe_tool", "resolve_runtime"]
