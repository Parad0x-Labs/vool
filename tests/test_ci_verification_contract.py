from __future__ import annotations

import copy
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by compatibility CI on Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The authoritative gate is the shard matrix the migration-era CI adopted (owner commits
# 9fd7ee7/006d874 + PR #11's hidden-file artifact law): the verify job lints with the PINNED
# ruff and produces the canonical pytest collection, every collected file runs in exactly one
# of the ten Linux shards (round-robin by size) or the routed macOS job, and every job's logs
# upload unconditionally. The single-job `ops/verify.py` gate this suite pinned before the
# migration no longer exists in the workflow; the weakening law it enforced — exact commands,
# no disabled steps, no swallowed output, exact dependency pins — is restated here against the
# current structure.
#: The authoritative pytest argv every shard forwards through the timing wrapper: collection
#: flags only -- selection comes from the resolver's file list appended after these tokens.
#: (Before the timing wrapper this was `python -m pytest` + these args; the wrapper runs
#: pytest.main in-process with the SAME argv, so the args themselves are unchanged law.)
EXPECTED_SHARD_PYTEST_PREFIX = (
    "-q",
    "--tb=short",
    "-p",
    "no:cacheprovider",
)
#: The measurement wrapper every Linux shard runs its pytest through (PR #35): it forwards the
#: EXACT authoritative argv after its own `--` separator, its exit status IS pytest's, and it
#: writes the per-file timing manifest into .verification-logs/ beside the shard's file list.
#: Pinned token for token like the pytest prefix itself: the wrapper is a measurement seam, not
#: a place to hide command changes -- behind it, the invocation must still forward exactly
#: EXPECTED_SHARD_PYTEST_PREFIX and still consume exactly the resolver's file list.
EXPECTED_SHARD_TIMING_PREFIX = (
    "python",
    "ops/pytest_timing.py",
    "--output",
    ".verification-logs/shard-${{ matrix.shard }}-timing.json",
    "--",
)
#: The virtual-display wrapper the Linux shards run their pytest under: the wallet-handoff
#: contract opens one genuinely headful Chromium session (a handoff needs a VISIBLE browser), and
#: these runners have no display, so the headful lane cannot open without a framebuffer. Pinned
#: token for token: the wrapper is a display server, not a place to hide command changes -- the
#: invocation behind it must still pass through the pinned timing wrapper and the exact
#: authoritative pytest argv, and still consume exactly the resolver's file list for its shard.
EXPECTED_SHARD_DISPLAY_PREFIX = (
    "xvfb-run",
    "-a",
    "--server-args=-screen 0 1280x1024x24",
)
EXPECTED_LINT_COMMAND = "python -m ruff check ."
EXPECTED_COLLECTION_COMMAND = (
    "python ops/pytest_manifest.py --repo-root . --output .verification-logs/pytest-manifest.json -- -q"
)
SHARD_FILE_LIST_REF = "shard-${{ matrix.shard }}-files.txt"
EXPECTED_SHARDS = [str(index) for index in range(10)]


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _assert_upload_step(job: dict[str, Any], name: str) -> None:
    upload = _named_step(job, name)
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == ".verification-logs/"
    # PR #11 law: upload-artifact v7 silently drops dot-directories — without this flag the
    # .verification-logs/ uploads were EMPTY all along and every shard diagnosis ran blind.
    assert upload["with"].get("include-hidden-files") is True


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

    lint = _named_step(verify, "Lint (pinned ruff)")
    assert "if" not in lint
    assert lint["run"].strip() == EXPECTED_LINT_COMMAND
    collection = _named_step(verify, "Canonical pytest collection")
    assert "if" not in collection
    assert collection["run"].strip() == EXPECTED_COLLECTION_COMMAND

    tests = workflow["jobs"]["tests"]
    assert tests["needs"] == "verify" or tests["needs"] == ["verify"]
    assert "if" not in tests
    matrix_shards = tests["strategy"]["matrix"]["shard"]
    assert matrix_shards == EXPECTED_SHARDS, "every shard must stay in the matrix: none may be dropped"

    resolver = _named_step(tests, "Resolve this shard's test files")
    assert "if" not in resolver
    run_step = _named_step(tests, "Run shard ${{ matrix.shard }}")
    assert "if" not in run_step
    command_text = str(run_step["run"])
    assert "--collect-only" not in command_text, "a shard run weakened to collection-only executes nothing"
    tokens = shlex.split(command_text)
    if tuple(tokens[: len(EXPECTED_SHARD_DISPLAY_PREFIX)]) == EXPECTED_SHARD_DISPLAY_PREFIX:
        tokens = tokens[len(EXPECTED_SHARD_DISPLAY_PREFIX) :]
    assert tuple(tokens[: len(EXPECTED_SHARD_TIMING_PREFIX)]) == EXPECTED_SHARD_TIMING_PREFIX, (
        "the shard must run through the timing wrapper with its manifest in .verification-logs/"
    )
    tokens = tokens[len(EXPECTED_SHARD_TIMING_PREFIX) :]
    assert tuple(tokens[: len(EXPECTED_SHARD_PYTEST_PREFIX)]) == EXPECTED_SHARD_PYTEST_PREFIX
    assert command_text.rstrip().endswith(
        f"$(tr '\\n' ' ' < .verification-logs/{SHARD_FILE_LIST_REF})"
    ), "the run must consume exactly the resolver's file list for its own shard"
    validate_forwarded = tuple(
        token for token in tokens if token.startswith("--tb=")
    )
    from ops.pytest_shards import validate_pytest_args

    validate_pytest_args(validate_forwarded)
    _assert_upload_step(tests, "Upload shard log")

    macos = workflow["jobs"]["macos"]
    assert macos["needs"] == "verify" or macos["needs"] == ["verify"]
    assert "if" not in macos
    macos_run = _named_step(macos, "Run the macOS-only suites")
    assert "if" not in macos_run
    _assert_upload_step(macos, "Upload macos job log")

    assert workflow["jobs"]["build"]["needs"] == ["verify", "tests", "macos"]

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
        gate = _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")
        gate["run"] += " --collect-only"
    elif mutation == "step_disabled":
        _named_step(workflow["jobs"]["tests"], "Run shard ${{ matrix.shard }}")["if"] = False
    else:
        verify["if"] = False

    with pytest.raises((AssertionError, ValueError)):
        _assert_authoritative_contract(workflow)


def test_verification_dependencies_are_exactly_pinned() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = set(project["project"]["optional-dependencies"]["dev"])

    # Exactly pinned, whatever the version currently is: a floor like ">=9.1" lets a new pytest
    # change collection semantics the shard manifest was built against. The version itself lives
    # only in pyproject — the gate (ops/verify.py) and the install-surface contract read it from
    # there, so a dependabot bump lands in one place. Copies of the literal here broke exactly
    # that on every past bump.
    for tool in ("pytest", "ruff"):
        exact = {item for item in dev if re.fullmatch(rf"{tool}==[0-9][0-9A-Za-z.\-]*", item)}
        assert exact, f"{tool} must carry an exact == pin in the dev extra, not a floor"
        assert len(exact) == 1, f"{tool} is declared more than once in the dev extra"
