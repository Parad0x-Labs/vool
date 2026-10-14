"""The Intel CI workflow is strict by default: a wheel that is not the recorded release wheel fails the run.

The workflow runs on macOS Intel hardware this suite never sees, so these tests pin the
semantics a real dispatch gets from .github/workflows/macos-intel.yml: `record_wheel`
(default false) is the only opt-out, it re-records the wheel instead of shipping it, and
the package it produces is marked not release-grade rather than released.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/macos-intel.yml"
SECURITY_AUDIT = ROOT / ".github/workflows/security-audit.yml"

BUILD_STEP = "Build the x86_64 app and DMG on Intel"
CHECK_STEP = "Check the cryptography wheel (slice, no dynamic OpenSSL, imports, legacy provider)"
BUILD_CRYPTO_STEP = "Build cryptography natively for x86_64 (static OpenSSL, no-module)"
AUDIT_STEP = "pip-audit the lean set the bundle installs"
RELEASE_PACKAGE = "macos-x86_64-package"
RECORD_PACKAGE = "macos-x86_64-package-NOT-RELEASE-GRADE"


def _steps(job: str) -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"][job]["steps"]


def _step(job: str, name: str) -> dict:
    return next(step for step in _steps(job) if step.get("name") == name)


def _artifacts(job: str, action: str) -> list[tuple[str, str | None, dict]]:
    """(artifact name, if condition, whole step) for every upload/download step in a job."""
    return [
        (step["with"]["name"], step.get("if"), step)
        for step in _steps(job)
        if str(step.get("uses", "")).startswith(f"actions/{action}-artifact")
    ]


_TOKEN = re.compile(r"==|!=|&&|\|\||'[^']*'|true|false|[A-Za-z_][A-Za-z0-9_.]*")


def _truthy(value: object) -> bool:
    # GitHub falsy values only: false, null, empty string, zero.
    return value not in (False, None, "", 0)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _loose_eq(left: object, right: object) -> bool:
    left_num, right_num = _number(left), _number(right)
    if left_num is not None and right_num is not None:
        return left_num == right_num
    return str(left) == str(right)


def _operand(token: str, inputs: dict) -> object:
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith("'"):
        return token[1:-1]
    if token.startswith("inputs."):
        return inputs.get(token[len("inputs.") :])
    raise AssertionError(f"expression operand kind this suite does not model: {token!r}")


def _comparison(tokens: list[str], pos: int, inputs: dict) -> tuple[object, int]:
    value = _operand(tokens[pos], inputs)
    pos += 1
    while pos < len(tokens) and tokens[pos] in {"==", "!="}:
        operator, right = tokens[pos], _operand(tokens[pos + 1], inputs)
        value = _loose_eq(value, right) if operator == "==" else not _loose_eq(value, right)
        pos += 2
    return value, pos


def _conjunction(tokens: list[str], pos: int, inputs: dict) -> tuple[object, int]:
    value, pos = _comparison(tokens, pos, inputs)
    while pos < len(tokens) and tokens[pos] == "&&":
        right, pos = _comparison(tokens, pos + 1, inputs)
        value = value if not _truthy(value) else right
    return value, pos


def _disjunction(tokens: list[str], pos: int, inputs: dict) -> tuple[object, int]:
    value, pos = _conjunction(tokens, pos, inputs)
    while pos < len(tokens) and tokens[pos] == "||":
        right, pos = _conjunction(tokens, pos + 1, inputs)
        value = value if _truthy(value) else right
    return value, pos


def evaluate(expression: str, inputs: dict) -> object:
    """Evaluate the GitHub expression operators this workflow uses, for the inputs given.

    Models the documented semantics of `==`/`!=`, `&&` and `||` for the operand kinds that
    appear in this file (booleans, string literals, inputs.NAME, absent inputs as null):
    && returns its first falsy operand else the last, || its first truthy operand else the
    last, and comparisons cast to numbers the way GitHub does (null -> 0, true -> 1).
    """
    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(text, pos)
        assert match, f"unsupported expression syntax {expression!r} at {text[pos:]!r}"
        tokens.append(match.group())
        pos = match.end()
    value, consumed = _disjunction(tokens, 0, inputs)
    assert consumed == len(tokens), f"unparsed tail in expression {expression!r}"
    return value


def test_record_wheel_is_an_opt_in_dispatch_input() -> None:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = doc.get(True) or doc.get("on")  # pyyaml parses the bare `on:` key as boolean True
    record_wheel = trigger["workflow_dispatch"]["inputs"]["record_wheel"]
    assert record_wheel["type"] == "boolean"
    assert record_wheel["default"] is False


def test_default_run_exports_release_provenance_to_the_build_step() -> None:
    raw = _step("build", BUILD_STEP)["env"]["VOOL_REQUIRE_RELEASE_PROVENANCE"]
    assert evaluate(raw, {"record_wheel": False}) == "1"  # default dispatch
    assert evaluate(raw, {}) == "1"  # pull_request: no inputs context at all
    assert evaluate(raw, {"record_wheel": True}) == "0"  # an explicit record run


def test_a_sha_mismatch_fails_the_run_unless_record_wheel_is_true(tmp_path: Path) -> None:
    """The comparison tail of the real check step, executed against a wheel that is not the record."""
    check = _step("build", CHECK_STEP)
    assert "if" not in check, "the wheel check must run on every path, cache hits included"
    assert check["env"]["RECORD_WHEEL"] == "${{ inputs.record_wheel }}"
    tail = check["run"]
    tail = tail[tail.index('recorded="') :]
    stub = tmp_path / "cryptography-50.0.2-cp311-abi3-macosx_14_0_x86_64.whl"
    stub.write_bytes(b"not the recorded release wheel")

    def run_tail(record_wheel: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "WHL": str(stub), "RECORD_WHEEL": record_wheel}
        return subprocess.run(["bash", "-c", tail], cwd=ROOT, env=env, capture_output=True, text=True)

    default = run_tail("false")  # what a default dispatch renders for the input
    assert default.returncode != 0
    assert "NOT release-grade" in default.stdout
    pull_request = run_tail("")  # pull_request renders the empty inputs context
    assert pull_request.returncode != 0
    record = run_tail("true")
    assert record.returncode == 0
    assert "record_wheel run" in record.stdout


def test_a_record_run_never_uploads_the_package_under_the_release_name() -> None:
    uploads = _artifacts("build", "upload")
    release = [entry for entry in uploads if entry[0] == RELEASE_PACKAGE]
    assert len(release) == 1, "exactly one step may upload the release package name"
    assert evaluate(release[0][1], {"record_wheel": False}) is True
    assert evaluate(release[0][1], {"record_wheel": True}) is False
    marked = [entry for entry in uploads if entry[0] == RECORD_PACKAGE]
    assert len(marked) == 1
    assert RECORD_PACKAGE != RELEASE_PACKAGE
    assert evaluate(marked[0][1], {"record_wheel": True}) is True
    assert evaluate(marked[0][1], {"record_wheel": False}) is False
    enabled_in_record_mode = {
        name for name, condition, _step_definition in uploads if _truthy(evaluate(condition, {"record_wheel": True}))
    }
    assert enabled_in_record_mode == {RECORD_PACKAGE, "intel-crypto-record"}


def test_the_record_artifact_carries_the_wheel_and_its_record_entry() -> None:
    record = [entry for entry in _artifacts("build", "upload") if entry[0] == "intel-crypto-record"]
    assert len(record) == 1
    assert evaluate(record[0][1], {"record_wheel": True}) is True
    assert evaluate(record[0][1], {"record_wheel": False}) is False
    path = record[0][2]["with"]["path"]
    assert "crypto-wheel/*.whl" in path
    assert "built_wheels_entry.json" in path


def test_smoke_still_tests_the_record_package_under_its_marked_name() -> None:
    downloads = _artifacts("smoke", "download")
    assert {name for name, _condition, _step_definition in downloads} == {RELEASE_PACKAGE, RECORD_PACKAGE}
    for name, condition, _step_definition in downloads:
        enabled_when_record = name == RECORD_PACKAGE
        assert evaluate(condition, {"record_wheel": enabled_when_record}) is True
        assert evaluate(condition, {"record_wheel": not enabled_when_record}) is False


def test_a_record_run_builds_its_wheel_fresh_instead_of_restoring_the_cache() -> None:
    cache = next(step for step in _steps("build") if str(step.get("uses", "")).startswith("actions/cache"))
    assert evaluate(cache["if"], {"record_wheel": False}) is True  # default: cache as before
    assert evaluate(cache["if"], {"record_wheel": True}) is False  # record: no restore and no save
    build_crypto = _step("build", BUILD_CRYPTO_STEP)
    # With the cache step skipped its cache-hit output is empty, so this condition still builds.
    assert "cache-hit != 'true'" in build_crypto["if"]
    assert "inputs." not in build_crypto["if"]


def test_inputs_are_never_interpolated_into_run_text() -> None:
    bad: list[str] = []

    def walk(node: object, allowed: bool, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, allowed or key in {"if", "env"}, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, allowed, f"{path}[{index}]")
        elif isinstance(node, str) and "${{ inputs." in node and not allowed:
            bad.append(path)

    walk(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")), False, "")
    assert bad == []


def test_pip_audit_runs_the_security_audit_policy_over_the_installed_lean_set() -> None:
    audit = _step("build", AUDIT_STEP)
    assert "continue-on-error" not in audit
    script = audit["run"]
    assert 'uv pip freeze --python "$RUNNER_TEMP/sys-venv/bin/python"' in script
    assert '-m pip_audit -r "$RUNNER_TEMP/lean-frozen.txt"' in script
    assert '--format=json -o "$RUNNER_TEMP/pip-audit-lean.json"' in script
    assert "--ignore" not in script, "no ignore is added beyond security-audit.yml's (it has none)"
    assert "|| true" not in script and "continue-on-error" not in script
    security = yaml.safe_load(SECURITY_AUDIT.read_text(encoding="utf-8"))
    security_step = next(
        step for step in security["jobs"]["audit"]["steps"] if step.get("name") == "pip-audit (pinned requirements)"
    )
    install = next(
        line for line in security_step["run"].splitlines() if "pip install" in line and "pip-audit" in line
    )
    # Same tool, same version policy: the identical pip-audit install command, in this
    # workflow's audit venv instead of the runner python.
    assert " -m " + install.strip().split(" -m ", 1)[1] in script


@pytest.mark.parametrize("expression,inputs,expected", [
    ("inputs.record_wheel != true", {"record_wheel": False}, True),
    ("inputs.record_wheel != true", {"record_wheel": True}, False),
    ("inputs.record_wheel != true", {}, True),  # absent input behaves as null -> 0 != 1
    ("inputs.record_wheel == true && 'a' || 'b'", {}, "b"),
    ("${{ inputs.record_wheel && '0' || '1' }}", {"record_wheel": False}, "1"),
    ("${{ inputs.record_wheel && '0' || '1' }}", {"record_wheel": True}, "0"),
])
def test_expression_model_matches_github_semantics(expression: str, inputs: dict, expected: object) -> None:
    assert evaluate(expression, inputs) == expected
