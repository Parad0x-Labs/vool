"""A JavaScript fixture through the same projection: the PR description follows the journal
whatever the language and whatever the project layout.

Different bug class from the Python fixtures (a string-transformation defect, not an arithmetic
one), different layout (``utils/`` package, tests in ``test/``, ``node --test`` as the runner).
Nothing Python-shaped is asserted here; if the projection were matching known strings from the
Python journeys it would fail this file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _node_available() -> None:
    from shutil import which

    if which("node") is None:
        pytest.skip("node is not installed; the JS journey cannot run here")


JS_BUGGY = "export function slug(text) {\n  return text.trim();\n}\n"
JS_FIXED = (
    "export function slug(text) {\n"
    "  return text.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');\n"
    "}\n"
)
JS_TEST = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert';\n"
    "import { slug } from '../utils/slug.mjs';\n\n"
    "test('slug slugifies', () => {\n"
    "  assert.strictEqual(slug('  Hello World!  '), 'hello-world');\n"
    "});\n"
)


@pytest.fixture
def js_repo(tmp_path, monkeypatch) -> Path:
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "js-repo"
    (root / "utils").mkdir(parents=True)
    (root / "test").mkdir(parents=True)
    (root / "utils" / "slug.mjs").write_text(JS_BUGGY, encoding="utf-8")
    (root / "test" / "slug.test.mjs").write_text(JS_TEST, encoding="utf-8")
    (root / "package.json").write_text('{"name": "js-repo", "type": "module"}\n', encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=fixture-author", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "js fixture: slug defect")
    yield root
    code_task_runtime().reset()
    reset_mode_permission_state()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, timeout=60, capture_output=True)


def _ctx(root: Path) -> dict:
    return {"workspace": str(root), "workspace_root": str(root), "session_id": "js-pr-desc", "operating_mode": "auto"}


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def test_javascript_journey_projects_a_truthful_pr_description(js_repo: Path) -> None:
    ctx = _ctx(js_repo)
    command = "node --test test/slug.test.mjs"
    opened = _door("code.task.open", {"objective": "Find why the slug test fails and repair the root cause"}, ctx)
    task_id = opened.details["task_id"]

    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "utils/slug.mjs"}},
        ctx,
    )
    repro = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": command}},
        ctx,
    )
    assert repro.details["tool_result"]["success"] is False, "the JS defect must genuinely fail"

    _door(
        "code.task.identify",
        {"task_id": task_id, "path": "utils/slug.mjs", "line": 2, "reason": "slug only trims instead of slugifying"},
        ctx,
    )
    import hashlib

    before = hashlib.sha256((js_repo / "utils" / "slug.mjs").read_bytes()).hexdigest()
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": "utils/slug.mjs", "content": JS_FIXED, "expected_hash": before},
            "rationale": "Owner utils/slug.mjs: slug only trims instead of slugifying.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": "utils/slug.mjs", "content": JS_FIXED}},
        ctx,
    )
    narrow = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "narrow", "intent": "workspace.run_tests", "arguments": {"command": command}},
        ctx,
    )
    assert narrow.details["tool_result"]["success"] is True, narrow.details
    cumulative = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests", "arguments": {"command": command}},
        ctx,
    )
    assert cumulative.details["tool_result"]["success"] is True, cumulative.details
    _door("code.task.step", {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}}, ctx)

    description = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert description.ok, description.response_text
    # The projection follows THIS journal: the JS owner path, the node runner, and none of the
    # Python fixtures' strings.
    assert "utils/slug.mjs" in description.details["title"]
    assert "node --test test/slug.test.mjs" in description.details["body"]
    assert "stats.py" not in description.details["body"]
    assert "calc.py" not in description.details["body"]
    assert "no pull request was opened" in description.details["body"].lower()


def test_javascript_pr_description_refuses_until_the_js_tests_are_green(js_repo: Path) -> None:
    """The evidence gate is language-blind: a JS task mid-journey gets the same refusal."""
    ctx = _ctx(js_repo)
    opened = _door("code.task.open", {"objective": "repair the slug"}, ctx)
    refused = _door("code.task.pr_description", {"task_id": opened.details["task_id"]}, ctx)
    assert refused.ok is False
    assert refused.status == "insufficient_evidence"
