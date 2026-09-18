"""A PR description prepared from EVIDENCE, not from the model's imagination.

The lane owes the operator a reviewable pull-request title and body for a finished repair.
At the base commit nothing derives one: `code.task.report` reports verdicts, but no
projection assembles the PR-ready text from the journal. The law this pack pins:

* every line of the title and body is assembled from JOURNALED facts (defect, diff paths,
  executed commands and their outcomes, task stage) -- never invented, never model prose;
* a task whose evidence is incomplete (no green narrow+cumulative run, no inspected diff)
  refuses with `insufficient_evidence` rather than laundering unverified work into a
  reviewable-looking PR description;
* the description says what VOOL did NOT do: no pull request was opened.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch) -> Path:
    from core.code_assistant.task_runtime import code_task_runtime

    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    from core.code_assistant.fixture import build_fixture_repo

    yield build_fixture_repo(tmp_path / "repo")
    code_task_runtime().reset()


BUGGY = "def median(xs):\n    return xs[len(xs) // 2]\n"
FIXED = "def median(xs):\n    s = sorted(xs)\n    n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n"
TEST = (
    "from gadget import median\n\n"
    "def test_odd():\n    assert median([3, 1, 2]) == 2\n\n"
    "def test_even():\n    assert median([2, 1, 3, 4]) == 2.5\n"
)


def _ctx(root: Path) -> dict:
    return {"workspace": str(root), "workspace_root": str(root), "session_id": "pr-desc-session", "operating_mode": "auto"}


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, f"{intent} is not contracted at the production door"
    return result


@pytest.fixture
def novel_repo(tmp_path, monkeypatch) -> Path:
    """A second, differently-shaped fixture: another bug class, other file names, another
    command -- so passing here cannot be passing by matching a known string."""
    from core.code_assistant.task_runtime import code_task_runtime

    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "gadget"
    root.mkdir()
    (root / "gadget.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_gadget.py").write_text(TEST, encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=60)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, timeout=60)
    subprocess.run(
        ["git", "-c", "user.name=fixture-author", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "novel fixture: initial state"],
        cwd=root, check=True, timeout=60,
    )
    yield root
    code_task_runtime().reset()


def _drive_to_report(root: Path, ctx: dict, *, module: str, fixed: str, test_path: str,
                     reason: str = "median indexes the unsorted list and ignores even lengths") -> str:
    """The full root-cause journey on whichever fixture repo it is given."""
    opened = _door("code.task.open", {"objective": f"Find why test_{module} fails and repair the root cause"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]

    read = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": f"{module}.py"}},
        ctx,
    )
    assert read.ok, read.response_text

    repro = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": f"python -m pytest -q {test_path}"}},
        ctx,
    )
    assert repro.details["tool_result"]["success"] is False, "the fixture defect must genuinely fail"

    ident = _door(
        "code.task.identify",
        {"task_id": task_id, "path": f"{module}.py", "line": 2, "reason": reason},
        ctx,
    )
    assert ident.ok, ident.response_text

    before_hash = __import__("hashlib").sha256((root / f"{module}.py").read_bytes()).hexdigest()
    proposal = _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": f"{module}.py", "content": fixed, "expected_hash": before_hash},
            "rationale": f"Owner {module}.py: {reason}.",
        },
        ctx,
    )
    assert proposal.ok, proposal.response_text
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}, ctx).ok

    mutated = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": f"{module}.py", "content": fixed}},
        ctx,
    )
    assert mutated.ok, mutated.response_text

    narrow = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "narrow", "intent": "workspace.run_tests", "arguments": {"command": f"python -m pytest -q {test_path}"}},
        ctx,
    )
    assert narrow.details["tool_result"]["success"] is True, narrow.details

    cumulative = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q"}},
        ctx,
    )
    assert cumulative.details["tool_result"]["success"] is True, cumulative.details

    diff = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}},
        ctx,
    )
    assert diff.ok, diff.response_text
    return task_id


# ---------------------------------------------------------------------------
# Contracted and offered
# ---------------------------------------------------------------------------


def test_pr_description_is_contracted_read_only_in_the_one_registry() -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    assert "code.task.pr_description" in contracts
    contract = contracts["code.task.pr_description"]
    assert contract.handler == "runtime"
    assert contract.side_effect_class == "read_only"
    assert contract.approval_requirement == "none"


def test_pr_description_seated_at_the_report_stage() -> None:
    from core.code_assistant.task_runtime import STAGE_CONTROL_INTENTS

    assert "code.task.pr_description" in STAGE_CONTROL_INTENTS["report"]


# ---------------------------------------------------------------------------
# The projection itself
# ---------------------------------------------------------------------------


def test_pr_description_assembles_only_journaled_facts(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    from core.code_assistant.fixture import FIXED_STATS_PY

    task_id = _drive_to_report(fixture_repo, ctx, module="stats", fixed=FIXED_STATS_PY, test_path="tests/test_stats.py")

    result = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert result.ok, result.response_text
    title = result.details["title"]
    body = result.details["body"]

    # Title names the journaled defect owner, not canned prose.
    assert "stats.py" in title
    # Body: what changed (from the inspected diff), why (from identify), evidence (executed
    # commands with outcomes), state, provenance.
    assert "stats.py" in body
    assert "median" in body
    assert "python -m pytest -q tests/test_stats.py" in body
    assert "python -m pytest -q" in body
    assert "passed" in body
    # It never claims the PR exists or that remote CI ran.
    assert "no pull request was opened" in body.lower()
    assert "failed after the repair" not in body
    assert "opens no pull request" not in body  # one clear sentence, not boilerplate noise
    # Deterministic assembly from the journal: same task, same description.
    again = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert again.details["title"] == title and again.details["body"] == body


def test_pr_description_on_a_differently_shaped_fixture(novel_repo: Path) -> None:
    """Novel input: another module, another command set, another defect wording. The
    projection must follow the journal, not the first fixture's strings."""
    ctx = _ctx(novel_repo)
    task_id = _drive_to_report(novel_repo, ctx, module="gadget", fixed=FIXED, test_path="test_gadget.py")

    result = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert result.ok, result.response_text
    assert "gadget.py" in result.details["title"]
    assert "test_gadget.py" in result.details["body"]
    assert "stats.py" not in result.details["body"]


# ---------------------------------------------------------------------------
# The refusal edges: incomplete evidence never becomes a PR description
# ---------------------------------------------------------------------------


def test_pr_description_refuses_before_the_evidence_exists(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    opened = _door("code.task.open", {"objective": "repair the median bug"}, ctx)
    task_id = opened.details["task_id"]

    refused = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert refused.ok is False
    assert refused.status == "insufficient_evidence"
    # The refusal names exactly what is missing, so the next act is actionable.
    missing = refused.details["missing"]
    assert missing


def test_pr_description_refuses_when_tests_are_not_green(fixture_repo: Path) -> None:
    """A mutation with a failing narrow test must not be laundered into reviewable prose."""
    ctx = _ctx(fixture_repo)
    opened = _door("code.task.open", {"objective": "repair the median bug"}, ctx)
    task_id = opened.details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_stats.py"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "stats.py", "line": 2, "reason": "indexes unsorted"}, ctx)

    refused = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert refused.ok is False
    assert refused.status == "insufficient_evidence"


def test_pr_description_reports_open_risks_from_the_journal(fixture_repo: Path) -> None:
    """When the journal records failures, the description carries them instead of hiding
    them behind the green runs that followed."""
    ctx = _ctx(fixture_repo)
    from core.code_assistant.fixture import FIXED_STATS_PY

    task_id = _drive_to_report(fixture_repo, ctx, module="stats", fixed=FIXED_STATS_PY, test_path="tests/test_stats.py")

    result = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert result.ok
    # The reproduced failure is part of the evidence narrative, named as a failure.
    assert "failed" in result.details["body"].lower() or "failure" in result.details["body"].lower()
