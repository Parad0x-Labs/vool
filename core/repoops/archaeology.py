"""Failure-side repository archaeology and local reproduction planning.

ARCHAEOLOGY is deterministic evidence collection — file paths, test locations,
configs, runtime declarations, changed files, CODEOWNERS. It decides nothing;
model reasoning consumes the bundle and proposes the repair.

REPRODUCTION translates CI evidence into a Toolbelt CAPABILITY NEED
(``python.test.pytest`` + cwd + exact target + runtime constraints). The fake
adapter here is a stand-in for Toolbelt's later resolution; it runs exactly the
named target, nothing more, and binds its result to the worktree state so
"tests green but tree changed afterwards" is detectable.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from core.repoops.ci_analysis import FailureReport
from core.repoops.identity import RepositoryWorkspace, WorkspaceError

# ---------------------------------------------------------------- archaeology

_CONFIG_FILES = ("pyproject.toml", "setup.cfg", "setup.py", "package.json",
                 "Makefile", "tox.ini", "requirements.txt", ".python-version")
_OWNER_FILES = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class RepoArchaeology:
    """Deterministic repo facts around one failure. No conclusions attached."""

    workspace_key: str
    failure_category: str
    changed_files: tuple[str, ...]
    candidate_source_files: tuple[str, ...]
    candidate_test_files: tuple[str, ...]
    build_configs_present: tuple[str, ...]
    runtime_declarations: tuple[str, ...]
    codeowners_file: str | None


def _git(root: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True,
                          text=True, timeout=30)
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def collect_archaeology(ws: RepositoryWorkspace, failure: FailureReport | None,
                        *, diff_base: str = "HEAD~1") -> RepoArchaeology:
    root = ws.root
    tracked = set(_git(root, "ls-files").splitlines())

    def existing(*paths: str) -> tuple[str, ...]:
        return tuple(p for p in paths if p in tracked)

    try:
        changed = tuple(l for l in _git(root, "diff", "--name-only",
                                        diff_base).splitlines() if l.strip())
    except WorkspaceError:
        changed = ()

    # Heuristics only — evidence, not verdicts.
    tests = sorted(p for p in tracked
                   if re.search(r"(^|/)(tests?|spec)/.*\.py$", p) or p.startswith("test_"))
    sources = sorted(
        p for p in changed if p.endswith(".py") and p not in tests
    ) or sorted(p for p in tracked if "/" not in p and p.endswith(".py"))
    owners = next((f for f in _OWNER_FILES if f in tracked), None)

    return RepoArchaeology(
        workspace_key=ws.key,
        failure_category=failure.category if failure else "none",
        changed_files=changed,
        candidate_source_files=tuple(sources[:20]),
        candidate_test_files=tuple(tests[:20]),
        build_configs_present=existing(*_CONFIG_FILES),
        runtime_declarations=existing(".python-version", "pyproject.toml"),
        codeowners_file=owners,
    )


# ---------------------------------------------------------------- reproduction


@dataclass(frozen=True)
class ReproductionNeed:
    """A Toolbelt CAPABILITY NEED. RepoOps does NOT implement the capability."""

    capability: str              # e.g. 'python.test.pytest'
    cwd: str                     # workspace root
    target: str                  # exact pytest node id / command target
    runtime_constraints: dict[str, str]   # declared python version etc.


@dataclass(frozen=True)
class LocalRunResult:
    """LOCAL truth: exit-status owned, bound to the exact worktree state."""

    need_capability: str
    target: str
    passed: bool                 # exit status owns this; skip != pass
    exit_code: int
    bound_sha: str               # HEAD when the run happened
    bound_dirty_files: tuple[str, ...]    # dirty paths at run time


def plan_reproduction(ws: RepositoryWorkspace, report: FailureReport,
                      log_text: str) -> ReproductionNeed:
    """Extract the exact failing target from LOG DATA and name the capability.

    Parses lines like ``FAILED tests/test_app.py::test_divide``; refuses to
    invent a target when the log names none (truncated logs included).
    """
    match = re.search(r"^(?:FAILED|ERROR)\s+(\S+::\S+)", str(log_text), re.M)
    if not match:
        raise ReproductionPlanError(
            "CI log names no runnable test target; cannot plan reproduction "
            "(log truncated or non-test failure)"
        )
    constraints: dict[str, str] = {}
    version_file = os.path.join(ws.root, ".python-version")
    if os.path.exists(version_file):
        with open(version_file) as f:
            constraints["python"] = f.read().strip()
    return ReproductionNeed(capability="python.test.pytest", cwd=ws.root,
                            target=match.group(1),
                            runtime_constraints=constraints)


class ReproductionPlanError(RuntimeError):
    pass


class FakeCapabilityAdapter:
    """Stand-in for Toolbelt resolution: runs ONLY the planned pytest target
    via `python -m pytest <target>` in the workspace. Not general shell."""

    def resolve_and_run(self, need: ReproductionNeed) -> LocalRunResult:
        import sys

        if not need.capability.startswith("python.test."):
            raise ReproductionPlanError(f"adapter handles python.test.* only, "
                                        f"got {need.capability!r}")
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", need.target, "-q", "--no-header"],
            cwd=need.cwd, capture_output=True, text=True, timeout=300)
        ws_state = _git(need.cwd, "status", "--porcelain")
        head = _git(need.cwd, "rev-parse", "HEAD")
        return LocalRunResult(
            need_capability=need.capability, target=need.target,
            passed=(proc.returncode == 0 and "no tests ran" not in proc.stdout),
            exit_code=proc.returncode,
            bound_sha=head,
            bound_dirty_files=tuple(
                ln.split(None, 1)[1] for ln in ws_state.splitlines() if ln.strip()))

    def verify_binding_still_valid(self, result: LocalRunResult,
                                   ws: RepositoryWorkspace) -> bool:
        """Local evidence survives only while the tree it ran on survives."""
        current = ws.read_local_state()
        return (current.head_sha == result.bound_sha
                and current.changed_paths == result.bound_dirty_files)


__all__ = [
    "FakeCapabilityAdapter",
    "LocalRunResult",
    "RepoArchaeology",
    "ReproductionNeed",
    "ReproductionPlanError",
    "collect_archaeology",
    "plan_reproduction",
]
