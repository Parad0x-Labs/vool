from __future__ import annotations

import copy
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by compatibility CI on Python 3.10
    import tomli as tomllib

from ops.pytest_shards import validate_pytest_args

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_GATE_COMMAND = (
    "python",
    "ops/verify.py",
    "--workers",
    "4",
    "--pytest-arg=--tb=short",
    "--log-dir=.verification-logs",
    "--tail-lines=200",
)


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _assert_authoritative_contract(workflow: dict[str, Any]) -> None:
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    for event in ("push", "pull_request"):
        assert event in triggers
        assert "paths-ignore" not in triggers[event]
    verify = workflow["jobs"]["verify"]
    assert "if" not in verify

    setup_steps = [
        step for step in verify["steps"] if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert len(setup_steps) == 1
    assert setup_steps[0]["with"]["python-version"] == "3.12.13"

    gate_step = _named_step(verify, "Run authoritative verification")
    assert "if" not in gate_step
    command = tuple(shlex.split(gate_step["run"]))
    assert command == EXPECTED_GATE_COMMAND
    forwarded = tuple(
        token.removeprefix("--pytest-arg=")
        for token in command
        if token.startswith("--pytest-arg=")
    )
    validate_pytest_args(forwarded)

    upload = _named_step(verify, "Upload complete verification logs")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == ".verification-logs/"
    assert workflow["jobs"]["build"]["needs"] == ["verify"]

    for job in workflow["jobs"].values():
        assert not job.get("continue-on-error", False)
        for step in job.get("steps", []):
            assert not step.get("continue-on-error", False)
            command_text = str(step.get("run", ""))
            assert "|| true" not in command_text
            assert "| tail" not in command_text
            assert "| tee" not in command_text


def test_push_and_pr_ci_use_the_exact_authoritative_gate() -> None:
    _assert_authoritative_contract(_load_workflow())


@pytest.mark.parametrize(
    "mutation",
    ("floating_python", "collect_only", "step_disabled", "job_disabled"),
)
def test_contract_mutations_are_rejected(mutation: str) -> None:
    workflow = copy.deepcopy(_load_workflow())
    verify = workflow["jobs"]["verify"]
    if mutation == "floating_python":
        setup = next(step for step in verify["steps"] if "setup-python" in step.get("uses", ""))
        setup["with"]["python-version"] = "3.12"
    elif mutation == "collect_only":
        gate = _named_step(verify, "Run authoritative verification")
        gate["run"] += " --pytest-arg=--collect-only"
    elif mutation == "step_disabled":
        _named_step(verify, "Run authoritative verification")["if"] = False
    else:
        verify["if"] = False

    with pytest.raises((AssertionError, ValueError)):
        _assert_authoritative_contract(workflow)


def test_verification_dependencies_are_exactly_pinned() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = set(project["project"]["optional-dependencies"]["dev"])

    assert "pytest==9.1.0" in dev
    assert "ruff==0.15.16" in dev
