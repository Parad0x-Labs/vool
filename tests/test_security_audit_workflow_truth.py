"""security-audit.yml may not be able to report health through a crashed auditor.

F1 from the false-green census (2026-08-27): every step carried ``continue-on-error: true``,
every command ended in ``|| true``, and a crashed pip-audit printed the fallback ``echo "0"`` --
the success shape of a crash. The workflow now has exactly one status authority, the auditor's
exit code; these tests keep the masking idioms from ever coming back. The assertions run against
the parsed workflow, so explanatory comments cannot satisfy or evade them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "security-audit.yml"

RUN_BLOCK_MASKS = ("|| true", "|| echo", "|| :", "2>/dev/null", ">/dev/null 2>&1")


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


def test_the_security_audit_workflow_has_no_masking_idiom_anywhere() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for node in _walk(document):
        if isinstance(node, str):
            for mask in RUN_BLOCK_MASKS:
                assert mask not in node, f"masking idiom {mask!r} found in: {node[:120]!r}"
        elif isinstance(node, dict):
            assert "continue-on-error" not in node, (
                "continue-on-error would let a failed auditor print as healthy"
            )


def test_every_command_step_runs_under_a_failure_literal_shell() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    command_steps = [step for step in _steps(document) if "run" in step]
    assert command_steps, "no command steps found -- did the workflow move?"
    for step in command_steps:
        run = str(step["run"])
        assert "set -euo pipefail" in run, f"step {step.get('name')!r} must fail on first error"


def test_the_pip_auditor_runs_against_both_pinned_requirement_files() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    pip_steps = [
        step for step in _steps(document) if "pip_audit" in str(step.get("run") or "")
    ]
    assert len(pip_steps) == 1, "exactly one pip-audit step expected"
    run = str(pip_steps[0]["run"])
    for requirements in ("requirements.txt", "requirements-runtime.txt"):
        assert f"-r {requirements}" in run, (
            f"the auditor must audit the pinned {requirements}, not a sample of it"
        )
