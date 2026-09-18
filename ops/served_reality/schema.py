"""Typed result/proof schemas for the served-reality bench.

Every case the bench runs produces exactly one ``CaseResult`` — a
machine-readable record whose identity block pins the request / turn /
attempt / model / tool / effect / evidence identities observed on the wire and
in the product's own served surfaces. A run produces one ``RunManifest``
plus a JSONL of case results.

The JSON Schema documents in ``schemas/`` describe the serialized form;
this module is the authority. ``selftest`` validates the schemas against
these shapes.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"

# Verdicts a case can carry. PASS and SKIP_EXPECTED are the only non-failing
# outcomes; SKIP_UNEXPECTED is a FAILURE (an unexpectedly skipped required
# case is a failed run — a bench that lets required work slip through
# unproven is lying about coverage).
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_SKIP_EXPECTED = "SKIP_EXPECTED"
VERDICT_SKIP_UNEXPECTED = "SKIP_UNEXPECTED"

# Who caused a non-PASS outcome. The bench must never blur these: a product
# defect (VOOL), a scripted provider outage (PROVIDER), model-content
# inadequacy (MODEL) and a broken bench (HARNESS) are different facts with
# different owners.
FAILURE_CLASS_VOOL = "VOOL"
FAILURE_CLASS_PROVIDER = "PROVIDER"
FAILURE_CLASS_MODEL = "MODEL"
FAILURE_CLASS_HARNESS = "HARNESS"

FAILURE_CLASSES = (FAILURE_CLASS_VOOL, FAILURE_CLASS_PROVIDER, FAILURE_CLASS_MODEL, FAILURE_CLASS_HARNESS)

# A case is skipped only when its own declaration says it is optional for a
# recorded reason. Anything else is SKIP_UNEXPECTED -> failing.
KNOWN_SKIP_REASONS = ("live_provider_not_enabled",)


@dataclass
class TurnIdentity:
    """The identity set one observed turn must expose.

    Every field is optional at the dataclass level because the product may
    legitimately not produce one (a refused turn has no attempt id) — but the
    CASE asserts which fields are REQUIRED for its verdict, and the collected
    values are carried in the proof regardless.
    """

    request_id: str | None = None
    finalization_id: str | None = None
    turn_id: str | None = None
    client_turn_id: str | None = None
    session_id: str | None = None
    attempt_id: str | None = None
    model_lane: str | None = None
    model_call_id: str | None = None
    tool_names: list[str] = field(default_factory=list)
    effect_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    receipt_ids: list[str] = field(default_factory=list)
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WireRef:
    """A pointer to retained exact wire bytes for one HTTP exchange."""

    exchange_id: str
    direction: str  # "daemon" | "stub" | "live"
    method: str
    path: str
    status: int | None
    artifacts_file: str  # relative path inside the run dir
    byte_range: list[int]  # [start, end) inside that file

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseResult:
    case_id: str
    title: str
    required: bool
    verdict: str
    failure_class: str | None = None
    reason: str = ""
    detail: str = ""
    identities: list[TurnIdentity] = field(default_factory=list)
    wire_refs: list[WireRef] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    duration_s: float = 0.0
    bench_sha: str = ""
    product_sha: str = ""
    provider_mode: str = "deterministic"  # or "live:<name>"

    @property
    def is_failure(self) -> bool:
        if self.verdict == VERDICT_FAIL:
            return True
        if self.verdict == VERDICT_SKIP_UNEXPECTED:
            return True
        if self.verdict == VERDICT_SKIP_EXPECTED and self.required:
            # A required case can never be skipped for a known reason either;
            # only optional cases may skip.
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["is_failure"] = self.is_failure
        payload["schema_version"] = SCHEMA_VERSION
        return payload

    def assertion(self, name: str, ok: bool, *, expected: Any = None, observed: Any = None, note: str = "") -> None:
        """Record one named assertion outcome. ``ok=False`` does not itself
        flip the verdict — the case driver decides the final verdict from its
        assertions; this only keeps the proof trail honest and complete."""
        self.assertions.append(
            {
                "name": name,
                "ok": bool(ok),
                "expected": _jsonable(expected),
                "observed": _jsonable(observed),
                "note": note,
            }
        )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


@dataclass
class RunManifest:
    run_id: str
    created_at: float
    bench_version: str = SCHEMA_VERSION
    product_sha: str = ""
    product_branch: str = ""
    product_dirty: bool | None = None
    staged_from_clean_tree: bool | None = None
    staging_archive_sha256: str = ""
    app_census_unchanged: bool | None = None
    home_path: str = ""
    daemon_port: int | None = None
    daemon_pid: int | None = None
    provider_mode: str = "deterministic"
    live_providers: list[str] = field(default_factory=list)
    network_mutation_attempts: int = 0
    case_count: int = 0
    failures: int = 0
    unexpected_skips: int = 0
    outcome: str = "incomplete"  # pass | fail | harness_error
    python_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        return payload


def new_run_id() -> str:
    return f"sr-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def write_results(run_dir: Path, manifest: RunManifest, results: list[CaseResult]) -> dict[str, Path]:
    """Write manifest.json + results.jsonl. Returns the paths written."""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    results_path = run_dir / "results.jsonl"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    with results_path.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")
    return {"manifest": manifest_path, "results": results_path}
