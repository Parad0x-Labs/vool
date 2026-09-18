"""ARGUS repair D1, 2026-08-06: the isolated-external proof root, made runtime-owned.

ARGUS's finding: `audit_tool_permission_denial`'s `workspace_write` + `external_temp_root` branch
returned `""` (allow) purely because the STAMPED policy dict said `proof_write_scope ==
"external_temp_root"` — a scope NAME, never checked against what workspace or path the CURRENT call
actually targeted. That policy dict is stamped once on `source_context["audit_execution_policy"]`
and carried for the rest of the turn; any call that reaches the tool boundary with the ORIGINAL
(audited-repository) `source_context` instead of the isolated override — a future bug, a model tool
call the stepped driver did not route through `_run_tool` — was allowed to write into the audited
repository even though `repository_write=False`, because the enforcement check never looked past the
scope string.

Every test below drives the REAL production enforcement boundary (`execute_runtime_tool`), not the
policy helper directly, per the repair mandate: "Exercise execute_runtime_tool or the actual
production enforcement boundary, not only policy helpers."
"""
from __future__ import annotations

import os

from core.agent_runtime import audit_policy
from core.agent_runtime.audit_policy import (
    isolated_proof_root_is_registered,
    register_isolated_proof_root,
    release_isolated_proof_root,
)
from core.runtime_execution_tools import execute_runtime_tool

_EXTERNAL_TEMP_ROOT_POLICY = {
    "repository_write": False,
    "repository_test_creation": False,
    "read_only_commands": True,
    "temporary_external_files": True,
    "isolated_reproduction": True,
    "network_research": False,
    "model_substitution": False,
    "conflicts": [],
    "proof_authorized": True,
    "proof_write_scope": "external_temp_root",
    "write_scope": "none",
    "reason": "test fixture",
}


def _ctx(*, workspace: str, run_id: str | None) -> dict:
    ctx = {"workspace": workspace, "audit_execution_policy": dict(_EXTERNAL_TEMP_ROOT_POLICY)}
    if run_id is not None:
        ctx["_isolated_proof_run_id"] = run_id
    return ctx


def _audited_repo(tmp_path) -> tuple[object, str]:
    repo_root = tmp_path / "audited-repo"
    subject = repo_root / "api" / "apache" / "liquefy_apache_repetition_v1.py"
    subject.parent.mkdir(parents=True)
    subject.write_text("original real source\n")
    return subject, subject.read_bytes()


def test_write_under_the_runtime_created_proof_root_is_allowed(tmp_path):
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    run_id = register_isolated_proof_root(str(proof_root), str(tmp_path / "audited-repo"))
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "test_x.py", "content": "print('ok')\n"},
            source_context=_ctx(workspace=str(proof_root), run_id=run_id),
        )
        assert result is not None and result.ok is True, getattr(result, "response_text", result)
        written = proof_root / "test_x.py"
        assert written.is_file()
        assert written.read_text() == "print('ok')\n"
    finally:
        release_isolated_proof_root(run_id)


def test_write_to_an_audited_source_path_is_denied_even_with_a_valid_binding(tmp_path):
    """The exact D1 exploit shape: the stamped policy says external_temp_root and the run id IS a
    real, live binding — but THIS call's active workspace is the audited repository, not the bound
    proof root. The scope name alone must not be enough."""
    subject, before = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "api/apache/liquefy_apache_repetition_v1.py", "content": "PWNED\n"},
            source_context=_ctx(workspace=repo_root, run_id=run_id),
        )
        assert result is not None and result.ok is False
        assert result.status == "permission_denied"
        assert subject.read_bytes() == before, "the audited source file's bytes changed"
    finally:
        release_isolated_proof_root(run_id)


def test_creating_a_new_file_in_the_audited_repository_is_denied(tmp_path):
    _, _ = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "generated_by_attack.py", "content": "x = 1\n"},
            source_context=_ctx(workspace=repo_root, run_id=run_id),
        )
        assert result is not None and result.ok is False
        assert result.status == "permission_denied"
        assert not (tmp_path / "audited-repo" / "generated_by_attack.py").exists()
    finally:
        release_isolated_proof_root(run_id)


def test_path_traversal_from_the_proof_root_is_denied(tmp_path):
    _, _ = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "../audited-repo/traversed.py", "content": "x = 1\n"},
            source_context=_ctx(workspace=str(proof_root), run_id=run_id),
        )
        assert result is not None and result.ok is False
        assert result.status == "permission_denied"
        assert not (tmp_path / "audited-repo" / "traversed.py").exists()
    finally:
        release_isolated_proof_root(run_id)


def test_a_symlink_from_the_proof_root_into_the_audited_repository_is_denied(tmp_path):
    _, _ = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    escape_link = proof_root / "escape_link"
    os.symlink(repo_root, escape_link)
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "escape_link/hacked.py", "content": "x = 1\n"},
            source_context=_ctx(workspace=str(proof_root), run_id=run_id),
        )
        assert result is not None and result.ok is False
        assert result.status == "permission_denied"
        assert not (tmp_path / "audited-repo" / "hacked.py").exists()
    finally:
        release_isolated_proof_root(run_id)


def test_a_forged_run_id_that_was_never_registered_is_denied(tmp_path):
    _, _ = _audited_repo(tmp_path)
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": "test_x.py", "content": "x = 1\n"},
        source_context=_ctx(workspace=str(proof_root), run_id="forged-never-registered-run-id"),
    )
    assert result is not None and result.ok is False
    assert result.status == "permission_denied"
    assert not (proof_root / "test_x.py").exists()


def test_a_context_with_no_proof_root_binding_at_all_is_denied(tmp_path):
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": "test_x.py", "content": "x = 1\n"},
        source_context=_ctx(workspace=str(proof_root), run_id=None),
    )
    assert result is not None and result.ok is False
    assert result.status == "permission_denied"
    assert not (proof_root / "test_x.py").exists()


def test_a_proof_root_registered_inside_the_audited_repository_is_denied(tmp_path):
    subject, before = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    bad_proof_root = tmp_path / "audited-repo" / ".proof_scratch"
    bad_proof_root.mkdir()
    run_id = register_isolated_proof_root(str(bad_proof_root), repo_root)
    try:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "test_x.py", "content": "x = 1\n"},
            source_context=_ctx(workspace=str(bad_proof_root), run_id=run_id),
        )
        assert result is not None and result.ok is False
        assert result.status == "permission_denied"
        assert subject.read_bytes() == before
    finally:
        release_isolated_proof_root(run_id)


def test_release_removes_the_binding_and_a_stale_run_id_is_then_denied(tmp_path):
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    repo_root = str(tmp_path / "audited-repo")
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    assert isolated_proof_root_is_registered(run_id) is True

    ok_result = execute_runtime_tool(
        "workspace.write_file",
        {"path": "before_cleanup.py", "content": "x = 1\n"},
        source_context=_ctx(workspace=str(proof_root), run_id=run_id),
    )
    assert ok_result is not None and ok_result.ok is True

    release_isolated_proof_root(run_id)
    assert isolated_proof_root_is_registered(run_id) is False

    stale_result = execute_runtime_tool(
        "workspace.write_file",
        {"path": "after_cleanup.py", "content": "x = 1\n"},
        source_context=_ctx(workspace=str(proof_root), run_id=run_id),
    )
    assert stale_result is not None and stale_result.ok is False
    assert stale_result.status == "permission_denied"
    assert not (proof_root / "after_cleanup.py").exists()


def test_sabotage_unconditional_allow_lets_the_attack_through(tmp_path, monkeypatch):
    """Reverts D1 by monkeypatching the REAL production function `audit_policy.
    _external_temp_root_denial` back to the old unconditional-allow shape (`if scope ==
    external_temp_root: return ""`), then re-runs the exact audited-source-write attack through the
    REAL `execute_runtime_tool` boundary and proves it now succeeds — the regression this repair
    exists to prevent. This mutates production code via monkeypatch, not a self-contained assertion
    on a hand-built dict (the D7 vacuous-sabotage pattern this repair round explicitly bans)."""
    subject, before = _audited_repo(tmp_path)
    repo_root = str(tmp_path / "audited-repo")
    proof_root = tmp_path / "proof-root"
    proof_root.mkdir()
    run_id = register_isolated_proof_root(str(proof_root), repo_root)
    try:
        monkeypatch.setattr(audit_policy, "_external_temp_root_denial", lambda *a, **k: "")

        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "api/apache/liquefy_apache_repetition_v1.py", "content": "PWNED\n"},
            source_context=_ctx(workspace=repo_root, run_id=run_id),
        )
        assert result is not None and result.ok is True, (
            "sabotage setup failed to reproduce the unconditional-allow bug"
        )
        assert subject.read_bytes() != before, (
            "the real repository-integrity test must fail under the sabotaged (pre-D1) behavior"
        )
    finally:
        release_isolated_proof_root(run_id)
        subject.write_bytes(before)
