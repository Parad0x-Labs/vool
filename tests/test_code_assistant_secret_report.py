"""Secrets cannot leave through the task report (P10): every textual field the report
assembles — commands, reasons, receipts, proposals, test output — is scrubbed by THE redaction
authority (``core.secret_redaction``: high-precision vendor shapes + the exact-value registry),
and the report says HOW MANY redactions happened rather than hiding that it acted.

Commits belong to the later KAS/RepoOps lane; the REPORT is this lane's publish surface, so it
is the seam the P10 row lands on first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_code_assistant_task_runtime import BUGGY, FIXED, TEST, _ctx, _door, _git

SHAPED = "sk-ant-abcdefghijklmnop123456"  # vendor-shaped fake key
CUSTOM = "zk9-customgateway-0123456789abcdef"  # no known shape — caught by the exact registry


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.mode_permission_policy import reset_mode_permission_state
    from core.secret_redaction import clear_exact_secrets_for_tests

    reset_mode_permission_state()
    clear_exact_secrets_for_tests()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    clear_exact_secrets_for_tests()


def _task_with_secret_command(root: Path, secret: str) -> object:
    ctx = _ctx(root)
    opened = _door("code.task.open", {"objective": f"repair calc using {secret}"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "r", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": f"subtract instead of add; key {secret}"}, ctx)
    _door(
        "code.task.propose",
        {"task_id": task_id, "proposal_id": "p", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED}},
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "w", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED}},
        ctx,
    )
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "n", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}},
        ctx,
    )
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "c", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q"}},
        ctx,
    )
    _door("code.task.step", {"task_id": task_id, "step_id": "d", "intent": "workspace.git_diff"}, ctx)
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.ok, report.response_text
    return report


def test_report_scrubs_vendor_shaped_secrets(repo: Path) -> None:
    report = _task_with_secret_command(repo, SHAPED)
    payload = json.dumps(report.details)
    assert SHAPED not in payload, "a vendor-shaped key must not leave through the report"
    assert report.details.get("redactions", 0) >= 1, "the report must say how many fields it redacted"


def test_report_scrubs_registered_exact_secrets(repo: Path) -> None:
    from core.secret_redaction import register_exact_secret

    register_exact_secret(CUSTOM)
    report = _task_with_secret_command(repo, CUSTOM)
    payload = json.dumps(report.details)
    assert CUSTOM not in payload, "an exact-registered custom key must not leave through the report"
    assert report.details.get("redactions", 0) >= 1


def test_report_without_secrets_declares_zero_redactions(repo: Path) -> None:
    from core.mode_permission_policy import reset_mode_permission_state
    from core.secret_redaction import clear_exact_secrets_for_tests

    reset_mode_permission_state()
    clear_exact_secrets_for_tests()
    ctx = _ctx(repo)
    task_id = _door("code.task.open", {"objective": "plain objective"}, ctx).details["task_id"]
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.ok
    assert report.details.get("redactions") == 0


@pytest.mark.parametrize('secret', [SHAPED, CUSTOM])
def test_pr_description_scrubs_chat_and_durable_body(repo: Path, secret: str) -> None:
    from core.secret_redaction import register_exact_secret
    from core.code_assistant.task_runtime import code_task_runtime
    from tests.code_assistant.test_pr_description import _drive_to_report, _ctx as pr_context

    register_exact_secret(secret)
    ctx = pr_context(repo)
    task_id = _drive_to_report(repo, ctx, module='calc', fixed=FIXED, test_path='test_calc.py',
                               reason=f'add subtracts instead of adding; reference {secret}')
    prepared = _door('code.task.pr_description', {'task_id': task_id}, ctx)
    assert prepared.ok, prepared.response_text
    assert secret not in prepared.response_text
    assert secret not in json.dumps(prepared.details)
    task, error = code_task_runtime()._require(task_id)
    assert error is None
    assert secret not in json.dumps(task.pr_description)
    assert prepared.details['body'] == task.pr_description['body']
