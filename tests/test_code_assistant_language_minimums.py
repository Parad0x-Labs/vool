"""Language minimums: the SAME root-cause task runtime repairs JavaScript, TypeScript and
shell defects — one door, one staged contract, whatever the language.

Each journey is the canonical positive flow (reproduce → identify → propose → approve →
mutate → narrow test → report) through ``execute_runtime_tool`` on a disposable repository
whose test runner is the language's own: ``node --test`` for JS, Node's native type-stripping
for TS (v22.18+), and a bash harness for the shell case. Green here is a row a served model
could reach with the same intents — the runtime never learns the language, only the command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_code_assistant_task_runtime import _ctx, _door, _git, _sha


@pytest.fixture(autouse=True)
def _node_available() -> None:
    if shutil_which("node") is None:
        pytest.skip("node is not installed; the JS/TS language minimums cannot run here")


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _journey(root: Path, ctx: dict, *, owner: str, fixed: str, buggy: str, test_command: str, narrow_command: str | None = None) -> dict:
    """Drive one full root-cause task and return its report details."""
    opened = _door("code.task.open", {"objective": f"Find why the {owner} test fails and repair the root cause"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]

    repro = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "r", "intent": "workspace.run_tests", "arguments": {"command": test_command}},
        ctx,
    )
    assert repro.details["executed"] is True, repro.response_text
    assert repro.details["tool_result"]["success"] is False, "the seeded defect must reproduce"
    # A blocked command also reads success=False; pin that the failure is the TEST failing,
    # not a policy refusal wearing the same flag.
    assert repro.details["tool_result"].get("returncode", 0) != 0, repro.details["tool_result"]

    # Canonical contract: the owning file is READ through the boundary before its repair is
    # proposed -- the same law every Python journey obeys, not a Python-only courtesy.
    owner_read = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "o", "intent": "workspace.read_file", "arguments": {"path": owner}},
        ctx,
    )
    assert owner_read.ok, owner_read.response_text
    _door("code.task.identify", {"task_id": task_id, "path": owner, "line": 2, "reason": "wrong operator"}, ctx)
    proposal = _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": owner, "content": fixed, "expected_hash": _sha(buggy)},
            # The canonical contract refuses a proposal with no rationale naming the owner and
            # the root cause. Language coverage does not get a weaker proposal contract.
            "rationale": f"Owner {owner}: the wrong operator is used, so the expected result is never produced.",
        },
        ctx,
    )
    assert proposal.ok, proposal.response_text
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    mutation = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "w", "intent": "workspace.write_file", "arguments": {"path": owner, "content": fixed}},
        ctx,
    )
    assert mutation.ok, mutation.response_text
    assert (root / owner).read_text(encoding="utf-8") == fixed

    narrow = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "n", "intent": "workspace.run_tests", "arguments": {"command": narrow_command or test_command}},
        ctx,
    )
    assert narrow.details["executed"] is True, narrow.response_text
    assert narrow.details["tool_result"]["success"] is True, narrow.response_text

    cumulative = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "c", "intent": "workspace.run_tests", "arguments": {"command": narrow_command or test_command}},
        ctx,
    )
    assert cumulative.details["executed"] is True, cumulative.response_text
    assert cumulative.details["tool_result"]["success"] is True, cumulative.response_text

    diff = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "d", "intent": "workspace.git_diff"},
        ctx,
    )
    assert diff.details["executed"] is True, diff.response_text

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.ok, report.response_text
    return report.details


# --------------------------------------------------------------------------- JavaScript


JS_BUGGY = "export function add(a, b) {\n  return a - b;\n}\n"
JS_FIXED = "export function add(a, b) {\n  return a + b;\n}\n"
JS_TEST = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert';\n"
    "import { add } from '../calc.mjs';\n\n"
    "test('add adds', () => {\n  assert.strictEqual(add(2, 3), 5);\n});\n"
)


def test_javascript_root_cause_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    (root / "test").mkdir(parents=True)
    (root / "calc.mjs").write_text(JS_BUGGY, encoding="utf-8")
    (root / "test" / "calc.test.mjs").write_text(JS_TEST, encoding="utf-8")
    (root / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")

    details = _journey(
        root,
        _ctx(root),
        owner="calc.mjs",
        buggy=JS_BUGGY,
        fixed=JS_FIXED,
        test_command="node --test test/calc.test.mjs",
    )
    assert details["verdict"] == "completed", details
    assert "calc.mjs" in details["files_changed"]


# --------------------------------------------------------------------------- TypeScript


TS_BUGGY = "export function median(values: number[]): number {\n  return values[Math.floor(values.length / 2)];\n}\n"
TS_FIXED = (
    "export function median(values: number[]): number {\n"
    "  const ordered = [...values].sort((a, b) => a - b);\n"
    "  return ordered[Math.floor(ordered.length / 2)];\n"
    "}\n"
)
TS_TEST = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert';\n"
    "import { median } from '../stats.ts';\n\n"
    "test('median of an unsorted list', () => {\n  assert.strictEqual(median([5, 1, 3]), 3);\n});\n"
)


def test_typescript_root_cause_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.mode_permission_policy import reset_mode_permission_state

    node = subprocess.run(["node", "--version"], capture_output=True, text=True)
    major = int(node.stdout.strip().lstrip("v").split(".")[0]) if node.returncode == 0 else 0
    if major < 22 or (major == 22 and int(node.stdout.strip().lstrip("v").split(".")[1]) < 18):
        pytest.skip("node lacks default type-stripping for .ts imports")

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    (root / "test").mkdir(parents=True)
    (root / "stats.ts").write_text(TS_BUGGY, encoding="utf-8")
    (root / "test" / "stats.test.ts").write_text(TS_TEST, encoding="utf-8")
    (root / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")

    details = _journey(
        root,
        _ctx(root),
        owner="stats.ts",
        buggy=TS_BUGGY,
        fixed=TS_FIXED,
        test_command="node --test test/stats.test.ts",
    )
    assert details["verdict"] == "completed", details
    assert "stats.ts" in details["files_changed"]


# --------------------------------------------------------------------------- Shell


SH_BUGGY = '#!/usr/bin/env bash\nset -euo pipefail\necho "release notes: $(cat version.txt) entries"\n'
SH_FIXED = '#!/usr/bin/env bash\nset -euo pipefail\nnotes=$(wc -l < version.txt | tr -d " ")\necho "release notes: ${notes} entries"\n'
# The runner is node (the sandbox whitelist ships interpreters — node, python — but not bash;
# adding bash is an operator policy decision, named in the evidence, not widened here). The
# DEFECT and the REPAIR are pure shell: the script prints the file's CONTENT where its consumer
# counts lines.
SH_HARNESS = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert';\n"
    "import { execFile } from 'node:child_process';\n"
    "import { promisify } from 'node:util';\n\n"
    "const run = promisify(execFile);\n\n"
    "test('summarize counts the entries', async () => {\n"
    "  const { stdout } = await run('./summarize.sh', [], { cwd: '.' });\n"
    "  assert.strictEqual(stdout.trim(), 'release notes: 3 entries');\n"
    "});\n"
)


def test_shell_root_cause_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seeded bug: summarize.sh cats the file (its CONTENT) where the consumer counts
    lines — the focused harness fails, and the repair is a one-file shell fix."""
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    (root / "test").mkdir(parents=True)
    (root / "version.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "summarize.sh").write_text(SH_BUGGY, encoding="utf-8")
    (root / "summarize.sh").chmod(0o755)
    (root / "test" / "summarize.test.mjs").write_text(SH_HARNESS, encoding="utf-8")
    (root / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")

    details = _journey(
        root,
        _ctx(root),
        owner="summarize.sh",
        buggy=SH_BUGGY,
        fixed=SH_FIXED,
        test_command="node --test test/summarize.test.mjs",
    )
    assert details["verdict"] == "completed", details
    assert "summarize.sh" in details["files_changed"]
