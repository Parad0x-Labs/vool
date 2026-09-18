"""CI failure drill-down: from 'red' to the exact failed step, log and SHA.

Layering on core.repoops.ci (SHA-bound observations): this module adds the
typed run tree — WorkflowRun -> Job -> Step -> Log/Artifact — with every node
bound to repository identity, run id, commit SHA and fetch time.

Two laws enforced here:

1. SHA SCOPING: a log/artifact/job is evidence for exactly one (run_id, sha).
   Accessors refuse cross-SHA or cross-run reads instead of serving them.
2. LOGS ARE DATA: log and artifact contents are wrapped in kernel
   TaintedValue at ingestion. Instruction-shaped text inside a CI log stays
   untrusted task data; kernel Law 3 refuses it in tool arguments without an
   countersign, so it can never become permission or instruction.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from core.kernel.capabilities import TaintedValue
from core.platform.broker import EffectOutcome, EffectRequest
from core.remote_forge.identity import RepoIdentity


class CiEvidenceMismatch(RuntimeError):
    """CI evidence does not belong to the requested SHA/run."""


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- evidence model


@dataclass(frozen=True)
class Step:
    number: int
    name: str
    conclusion: str              # success | failure | skipped | cancelled | ...
    head_sha: str

    @property
    def failed(self) -> bool:
        return self.conclusion == "failure"


@dataclass(frozen=True)
class Job:
    job_id: str
    name: str
    run_id: str
    head_sha: str
    conclusion: str              # success | failure | cancelled | skipped | ...
    required: bool = True
    steps: tuple[Step, ...] = ()

    @property
    def failed(self) -> bool:
        return self.conclusion == "failure"

    @property
    def blocking_failure(self) -> bool:
        # cancelled is NOT a failure verdict and skipped is NOT a pass;
        # both are their own facts surfaced verbatim.
        return self.required and self.conclusion == "failure"


@dataclass(frozen=True)
class WorkflowRun:
    repo_key: tuple
    run_id: str
    workflow_name: str
    event: str                   # push | pull_request | workflow_dispatch | ...
    head_sha: str
    jobs: tuple[Job, ...] = ()
    fetched_at: float = field(default_factory=_now)

    def job(self, job_id: str) -> Job:
        for j in self.jobs:
            if j.job_id == job_id:
                return j
        raise KeyError(f"job {job_id!r} not in run {self.run_id}")

    def require_for(self, sha: str) -> WorkflowRun:
        if self.head_sha != sha:
            raise CiEvidenceMismatch(
                f"run {self.run_id} is for SHA {self.head_sha[:12]}, not {sha[:12]}"
            )
        return self


@dataclass(frozen=True)
class LogData:
    """A CI log: provenance + content as UNTRUSTED DATA."""

    run_id: str
    job_id: str
    step_name: str
    head_sha: str
    truncated: bool              # honest flag: analysis must know evidence is partial
    _content: TaintedValue = field(repr=False)

    @property
    def text(self) -> TaintedValue:
        return self._content     # stays tainted; callers see provenance-carrying data

    def require_for(self, *, run_id: str, job_id: str, sha: str) -> LogData:
        if (run_id, job_id, sha) != (self.run_id, self.job_id, self.head_sha):
            raise CiEvidenceMismatch(
                f"log belongs to run={self.run_id} job={self.job_id} "
                f"sha={self.head_sha[:12]}; asked run={run_id} job={job_id} sha={sha[:12]}"
            )
        return self


@dataclass(frozen=True)
class ArtifactMetadata:
    run_id: str
    name: str
    head_sha: str
    size_bytes: int
    is_binary: bool

    def require_for(self, *, run_id: str, sha: str) -> ArtifactMetadata:
        if (run_id, sha) != (self.run_id, self.head_sha):
            raise CiEvidenceMismatch(
                f"artifact {self.name!r} belongs to run={self.run_id} "
                f"sha={self.head_sha[:12]}; asked run={run_id} sha={sha[:12]}"
            )
        return self


@dataclass(frozen=True)
class ArtifactData(ArtifactMetadata):
    _content: bytes = field(repr=False)

    @property
    def content(self) -> bytes:
        """Raw bytes. DATA ONLY — never executed by this layer."""
        return self._content


# ---------------------------------------------------------------- drill-down


@dataclass(frozen=True)
class FailureReport:
    """WHAT failed, WHERE, AT WHICH SHA, and the evidence chain that says so."""

    identity: RepoIdentity
    ci_sha: str
    check_name: str
    workflow_run_id: str
    workflow_name: str
    job_id: str
    job_name: str
    failed_step: Step | None
    category: str               # deterministic classification incl. 'unknown'
    log_excerpt: str            # short quote FOR HUMANS; full log stays tainted
    log_truncated: bool
    evidence_refs: tuple[str, ...]


# Deterministic classifiers. Order matters; UNKNOWN is the honest fallback.
_CLASSIFIERS: tuple[tuple[str, re.Pattern], ...] = (
    ("test_failure", re.compile(r"^(?:FAILED|ERROR)\s+\S+::\S+", re.M)),
    ("timeout", re.compile(r"\b(?:timed?\s*out|The job running on .* has exceeded)", re.I)),
    ("dependency_install", re.compile(r"\b(?:No matching distribution|pip install error|"
                                      r"npm ERR!.*install|unresolved dependency)", re.I)),
    ("build_failure", re.compile(r"\b(?:compilation error|Build FAILED|error: linking)\b", re.I)),
    ("lint_failure", re.compile(r"\b(?:ruff|flake8|eslint)\b.*\bfailed\b|\bF\d{3}\b", re.I)),
    ("cancelled", re.compile(r"\bThe job was canceled\b", re.I)),
)


def classify_failure(log_text: str) -> str:
    """Deterministic classification over LOG DATA. Returns 'unknown' freely."""
    for category, pattern in _CLASSIFIERS:
        if pattern.search(str(log_text)):
            return category
    return "unknown"


def drill_down(identity: RepoIdentity, ci_sha: str, run: WorkflowRun,
               logs: dict[str, LogData]) -> FailureReport | None:
    """Walk status -> failing required check -> job -> step -> log.

    Never infers failure from a workflow NAME; only `failure` conclusions on
    required jobs count. Returns None when nothing required failed.
    """
    run.require_for(ci_sha)
    for job in run.jobs:
        if not job.blocking_failure:
            continue
        failed_steps = [s for s in job.steps if s.failed]
        step = failed_steps[0] if failed_steps else None
        key = f"{job.job_id}:{step.name}" if step else job.job_id
        log = logs.get(key)
        excerpt, category, truncated = "", "unknown", False
        if log is not None:
            log.require_for(run_id=run.run_id, job_id=job.job_id, sha=ci_sha)
            raw = str(log.text)          # analysis reads the payload; taint survives upstream
            category = classify_failure(raw)
            first_lines = [ln for ln in raw.splitlines() if ln.strip()][:3]
            excerpt = " | ".join(ln[:160] for ln in first_lines)
            truncated = log.truncated
        return FailureReport(
            identity=identity, ci_sha=ci_sha,
            check_name=job.name, workflow_run_id=run.run_id,
            workflow_name=run.workflow_name, job_id=job.job_id,
            job_name=job.name, failed_step=step, category=category,
            log_excerpt=excerpt, log_truncated=truncated,
            evidence_refs=(f"run:{run.run_id}", f"job:{job.job_id}",
                           *(f"step:{s.number}" for s in failed_steps[:1]),
                           *(f"log:{key}" if log else (),)),
        )
    return None


# ---------------------------------------------------------------- CI mutations (typed, authorized)


@dataclass(frozen=True)
class RerunFailedJobs:
    run_id: str
    head_sha: str
    permission: str = "ci.rerun"


@dataclass(frozen=True)
class RerunWorkflow:
    run_id: str
    head_sha: str
    permission: str = "ci.rerun"


@dataclass(frozen=True)
class CancelWorkflow:
    run_id: str
    head_sha: str
    permission: str = "ci.cancel"


@dataclass(frozen=True)
class WorkflowDispatch:
    workflow_name: str
    ref_sha: str                  # dispatch targets THIS sha — recorded honestly
    permission: str = "ci.dispatch"


class FakeCIRunControl:
    """Sandbox run-control provider. Mutations are broker-admitted upstream;
    this object just behaves like a host: reruns keep the SHA and mint a NEW
    run id (an attack case), dispatch records the sha it actually ran."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self._counter = 0

    def seed(self, run_id: str, workflow: str, sha: str,
             jobs: tuple[Job, ...]) -> WorkflowRun:
        run = WorkflowRun(repo_key=("github", "acme", "api"), run_id=run_id,
                          workflow_name=workflow, event="push", head_sha=sha,
                          jobs=jobs)
        self.runs[run_id] = {"run": run, "conclusion": "in_progress"}
        return run

    def rerun_failed_jobs(self, actor_fork, broker, action: RerunFailedJobs):
        return self._mutate(
            actor_fork, broker, action, "ci.rerun_failed_jobs",
            lambda _run: {"new_run_id": self._reseed(action.run_id, action.head_sha)})

    def cancel_workflow(self, actor_fork, broker, action: CancelWorkflow):
        def _cancel(run):
            run["conclusion"] = "cancelled"
            return {"cancelled": True}

        return self._mutate(actor_fork, broker, action, "ci.cancel_workflow", _cancel)

    def dispatch(self, actor_fork, broker, action: WorkflowDispatch):
        import hashlib

        key = hashlib.sha256(
            f"dispatch:{action.workflow_name}:{action.ref_sha}".encode()).hexdigest()[:16]
        new_id = f"disp-{key}"

        def apply(_run=None):
            run = self.seed(new_id, action.workflow_name, action.ref_sha, ())
            return {"new_run_id": new_id, "dispatched_sha": run.head_sha}

        return self._admit(actor_fork, broker, "ci.workflow_dispatch",
                           "ci.dispatch",
                           {"workflow": action.workflow_name, "sha": action.ref_sha},
                           apply)

    # -- plumbing -----------------------------------------------------------------
    def _reseed(self, old_run_id: str, sha: str) -> str:
        self._counter += 1
        new_id = f"{old_run_id}-rerun{self._counter}"
        old = self.runs[old_run_id]
        self.runs[new_id] = {"run": WorkflowRun(  # SAME sha, NEW run id
            repo_key=old["run"].repo_key, run_id=new_id,
            workflow_name=old["run"].workflow_name, event="push",
            head_sha=sha, jobs=()), "conclusion": "queued"}
        return new_id

    def _mutate(self, fork, broker, action, effect_id, apply_fn):
        run = self.runs[action.run_id]
        return self._admit(fork, broker, effect_id, action.permission,
                           {"run_id": action.run_id, "sha": action.head_sha},
                           lambda: apply_fn(run))

    def _admit(self, fork, broker, effect_id, token, params, apply_fn):
        import hashlib
        import json

        request = EffectRequest(
            effect_id=effect_id, required_capability=token, params=params,
            idempotency_key=hashlib.sha256(
                json.dumps(params, sort_keys=True).encode()).hexdigest()[:32])
        receipt = broker.execute(fork, request, lambda: EffectOutcome.from_domain(
            "ok", reason="applied", evidence=dict(apply_fn())))
        return receipt
