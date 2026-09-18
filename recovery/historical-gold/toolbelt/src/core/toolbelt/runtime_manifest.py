"""Project runtime discovery beyond ".venv exists" — deterministic manifest signals.

No single file is absolute truth. Conflicting manifests are reported explicitly as
ENVIRONMENT_CONFLICT, not silently arbitrated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.toolbelt.version_spec import satisfies

_REQUIRES_PYTHON = re.compile(r"^\s*requires-python\s*=\s*[\"']([^\"']+)[\"']", re.M)


class SignalVerdict(str, Enum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    CONFLICT = "ENVIRONMENT_CONFLICT"


@dataclass(frozen=True)
class RuntimeSignal:
    source: str                 # "pyproject.toml", ".python-version", ...
    requirement: str            # e.g. ">=3.12" (empty = presence-only signal)
    kind: str                   # "python" | "node" | "rust" | "go" | "docker" | ...


@dataclass(frozen=True)
class ProjectEnvironment:
    signals: tuple[RuntimeSignal, ...]
    python_requirement: str | None      # the effective requirement IF unambiguous
    verdict: SignalVerdict
    conflicts: tuple[str, ...] = ()

    @property
    def note(self) -> str:
        return "; ".join(self.conflicts) if self.conflicts else "no conflicts"


def scan_project_signals(repo_path: str | Path) -> ProjectEnvironment:
    """Read-only deterministic scan of the repo's declared environment expectations."""
    root = Path(repo_path)
    signals: list[RuntimeSignal] = []
    conflicts: list[str] = []

    py_reqs: list[tuple[str, str]] = []   # (source, requirement)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        m = _REQUIRES_PYTHON.search(pyproject.read_text(errors="replace"))
        if m:
            py_reqs.append(("pyproject.toml", m.group(1)))
            signals.append(RuntimeSignal("pyproject.toml", m.group(1), "python"))
    for req in sorted(root.glob("requirements*.txt")):
        signals.append(RuntimeSignal(req.name, "", "python"))

    pyver = root / ".python-version"
    if pyver.is_file():
        v = pyver.read_text().strip()
        if v:
            py_reqs.append((".python-version", f"=={v.lstrip('v')}" if not any(
                c in v for c in "<>=!") else v))
            signals.append(RuntimeSignal(".python-version", v, "python"))

    if (root / "uv.lock").is_file() or (root / "poetry.lock").is_file():
        lock = "uv.lock" if (root / "uv.lock").is_file() else "poetry.lock"
        signals.append(RuntimeSignal(lock, "", "python"))
    for pkg, locks in (("node", ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")),
                       ("rust", ("Cargo.toml",)),
                       ("rust-toolchain", ("rust-toolchain", "rust-toolchain.toml")),
                       ("go", ("go.mod",)),
                       ("docker", ("Dockerfile", "compose.yml", "docker-compose.yml")),
                       ):
        for name in locks:
            if (root / name).is_file():
                signals.append(RuntimeSignal(name, "", pkg))

    # Python requirement arbitration: multiple DECLARED requirements must agree.
    python_requirement: str | None = None
    if len(py_reqs) == 1:
        python_requirement = py_reqs[0][1]
    elif len(py_reqs) > 1:
        # Same constraint spelled twice is fine; genuinely different specs conflict.
        specs = {r for _, r in py_reqs}
        if len(specs) > 1:
            for src, r in py_reqs:
                conflicts.append(f"{src}: {r}")
            verdict = SignalVerdict.CONFLICT
            return ProjectEnvironment(signals=tuple(signals), python_requirement=None,
                                      verdict=verdict, conflicts=tuple(conflicts))
        python_requirement = py_reqs[0][1]

    return ProjectEnvironment(signals=tuple(signals), python_requirement=python_requirement,
                              verdict=SignalVerdict.SATISFIED, conflicts=())


def check_python_against_manifest(version: str | None,
                                  env: ProjectEnvironment) -> SignalVerdict:
    """Does a concrete interpreter satisfy what THIS project declares? Fails closed."""
    if env.verdict is SignalVerdict.CONFLICT:
        return SignalVerdict.CONFLICT
    if env.python_requirement is None:
        return SignalVerdict.SATISFIED  # nothing declared; .venv choice already proven elsewhere
    return (SignalVerdict.SATISFIED if satisfies(version, env.python_requirement)
            else SignalVerdict.UNSATISFIED)


__all__ = ["ProjectEnvironment", "RuntimeSignal", "SignalVerdict",
           "check_python_against_manifest", "scan_project_signals"]
