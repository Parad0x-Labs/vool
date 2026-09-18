"""CI truth: every CI fact belongs to an EXACT SHA.

"CI is green" is not a sentence this module can produce. What it produces is a
CiObservation carrying repo identity, the sha the checks ran against, per-check
status, and fetch time — plus `require_ci_for`, which refuses to serve CI read
at one sha as if it covered another. A rerun after a new push, or CI from an
old SHA, is detectable instead of silent.

Skipped is NOT passed; pending is NOT green; required vs optional is explicit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.remote_forge.identity import RepoIdentity

# Terminal-positive vs everything else. 'skipped' is deliberately absent.
PASSING = frozenset({"success"})
NEGATIVE = frozenset({"failure", "cancelled", "timed_out", "action_required"})


@dataclass(frozen=True)
class CheckRun:
    name: str
    conclusion: str              # success | failure | skipped | cancelled | pending | ...
    head_sha: str                # the sha THIS check ran against
    required: bool = False

    @property
    def passed(self) -> bool:
        return self.conclusion in PASSING  # skipped is not passing

    @property
    def blocking_failure(self) -> bool:
        return self.required and (self.conclusion in NEGATIVE)


@dataclass(frozen=True)
class CiObservation:
    """One CI read, bound to repo + sha + time."""

    identity: RepoIdentity
    ci_sha: str                  # THE sha this observation is about
    checks: tuple[CheckRun, ...] = ()
    fetched_at: float = field(default_factory=time.time)

    def require_ci_for(self, sha: str) -> CiObservation:
        """Refuse to serve CI read at one sha as covering a different one."""
        if sha != self.ci_sha:
            raise CiShaMismatch(
                f"CI evidence is for {self.ci_sha[:12]}, not {sha[:12]} — "
                "re-read CI after any new push"
            )
        return self

    def require_fresh(self, *, max_age_seconds: float = 120.0) -> CiObservation:
        if time.time() - self.fetched_at > max_age_seconds:
            raise StaleCiError(f"CI observation is stale (> {max_age_seconds:.0f}s); re-read")
        return self

    def verdict(self) -> dict[str, object]:
        """Honest aggregate — never a bare 'green'."""
        relevant = [c for c in self.checks]
        return {
            "ci_sha": self.ci_sha,
            "all_required_pass": all(c.passed for c in relevant if c.required)
            if relevant else False,
            "pending": [c.name for c in relevant if c.conclusion == "pending"],
            "skipped": [c.name for c in relevant if c.conclusion == "skipped"],
            "blocking_failures": [
                {"name": c.name, "conclusion": c.conclusion}
                for c in relevant if c.blocking_failure
            ],
        }


class CiShaMismatch(RuntimeError):
    pass


class StaleCiError(RuntimeError):
    pass


def failed_check_names(observation: CiObservation) -> tuple[str, ...]:
    return tuple(c.name for c in observation.checks if c.blocking_failure)
