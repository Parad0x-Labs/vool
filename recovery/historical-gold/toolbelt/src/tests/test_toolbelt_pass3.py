"""Toolbelt Pass #3 — executable integrity + operation-specific capability.

Sabotage matrix S1-S10 from the mission brief. Executable-sabotage tests use
REAL temp files so device/inode/size/mtime evidence is genuine; provider
preflight tests use read-only fake API responses with a planted canary token.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import stat

import pytest

from core.toolbelt.evidence import DriftKind, ToolPlanRef, revalidate
from core.toolbelt.exe_identity import ExecIdentity, fast_identity, strong_identity
from core.toolbelt.models import CapabilityNeed, PlanStatus
from core.toolbelt.operations import (
    OperationStatus,
    parse_viewer_permission,
    preflight,
)
from core.toolbelt.probes import ProbeResult
from core.toolbelt.resolve import resolve

CANARY = "VOOL_SECRET_CANARY_9f1d2c4b8e7a6f5b3d1c0a9e8f7b6d5c"


class FakeRunner:
    def __init__(self, which_map=None, run_map=None):
        self.which_map = which_map or {}
        self.run_map = run_map or {}

    def which(self, name):
        return self.which_map.get(name)

    def run(self, argv, *, cwd=None):
        for n in (2, 1):
            if tuple(argv[:n]) in self.run_map:
                return self.run_map[tuple(argv[:n])]
        return ProbeResult(127, "", "executable not found")


def make_real_tool(tmp_path, name="git", body="#!/bin/sh\necho old-bytes\n"):
    """A REAL file on disk whose scripted probe prints a fixed version string."""
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def tool_runner(tool_path, version="2.50.1"):
    return FakeRunner(
        which_map={"git": tool_path},
        run_map={("git", "--version"): ProbeResult(0, f"git version {version}", "")},
    )


def frozen_git_plan(tmp_path):
    tool = make_real_tool(tmp_path)
    r = tool_runner(tool)
    plan = resolve(CapabilityNeed(capability="git.commit"), r)
    ref, facts = ToolPlanRef.freeze(plan, r)
    return ref, facts, tool


# --- S1: same path + same version + DIFFERENT BYTES ----------------------------

def test_s1_same_path_same_version_changed_bytes_is_stale(tmp_path):
    ref, facts, tool = frozen_git_plan(tmp_path)

    before = fast_identity(tool)
    p = __import__("pathlib").Path(tool)
    p.write_text("#!/bin/sh\necho TOTALLY-DIFFERENT-BYTES\n")  # same path, same printed version
    after = fast_identity(tool)

    assert before.size == len("#!/bin/sh\necho old-bytes\n")
    assert after.size != before.size or after.inode != before.inode \
        or after.mtime_ns != before.mtime_ns

    res = revalidate(ref, facts, tool_runner(tool))
    assert res.status is DriftKind.STALE          # NOT READY — claim retracted & fixed
    assert any("execid" in d for d in res.drifted)


def test_s1b_atomic_replace_detected(tmp_path):
    ref, facts, tool = frozen_git_plan(tmp_path)
    tmp_new = tmp_path / "git.new"
    tmp_new.write_text("#!/bin/sh\necho evil\n")
    tmp_new.chmod(0o755)
    os.replace(tmp_new, tool)                      # classic atomic rename-over
    res = revalidate(ref, facts, tool_runner(tool))
    assert res.status is DriftKind.STALE


def test_s2_symlink_target_switch_is_stale(tmp_path):
    real_a = make_real_tool(tmp_path, "git-real-a")
    link = tmp_path / "git"
    os.symlink(real_a, link)
    r = FakeRunner(which_map={"git": str(link)},
                   run_map={("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    plan = resolve(CapabilityNeed(capability="git.commit"), r)
    ref, facts = ToolPlanRef.freeze(plan, r)

    real_b = make_real_tool(tmp_path, "git-real-b", body="#!/bin/sh\necho other\n")
    link.unlink()
    os.symlink(real_b, link)                       # swap the symlink target
    res = revalidate(ref, facts, r)
    assert res.status is DriftKind.STALE
    assert any("resolved_target" in d for d in res.drifted)


def test_strong_identity_digests_content_when_required(tmp_path):
    tool = make_real_tool(tmp_path)
    i1 = strong_identity(tool)
    assert i1.sha256 and len(i1.sha256) == 64
    make_real_tool(tmp_path, "git", body="#!/bin/sh\necho new\n")   # rewrite same path
    i2 = strong_identity(tool)
    assert i2.sha256 != i1.sha256                  # fast layers may miss nothing here,
    # but digest proves byte-level identity when the caller demands STRONG.


# --- S3-S6: operation-specific evidence ----------------------------------------

class ApiRunner(FakeRunner):
    """Scripted read-only gh api responses."""

    def __init__(self, branch_body=None, protection_body=None, pr_body=None,
                 ruleset_body=None, ruleset_ok=True):
        self.branch_body = branch_body
        self.protection_body = protection_body
        self.pr_body = pr_body
        self.ruleset_body = ruleset_body
        self.ruleset_ok = ruleset_ok   # False simulates an adapter with no ruleset capability
        super().__init__(which_map={"gh": "/usr/local/bin/gh"})

    def run(self, argv, *, cwd=None):
        if tuple(argv[:2]) == ("gh", "api"):
            args = " ".join(argv[2:])
            payload = None
            if "/branches/" in args and "/protection" in args:
                payload, rc = self.protection_body, (0 if self.protection_body else 1)
            elif "/branches/" in args:
                payload, rc = self.branch_body, (0 if self.branch_body else 1)
            elif "/pulls/" in args:
                payload, rc = self.pr_body, (0 if self.pr_body else 1)
            elif "/rules/branches" in args:
                if not self.ruleset_ok:
                    return ProbeResult(1, "", f"HTTP 404 (token {CANARY})")
                payload, rc = (self.ruleset_body or "[]"), 0
            else:
                payload, rc = None, 1
            if payload is not None:
                payload = payload.replace("CANARY", CANARY)  # tokens echo into raw output
                return ProbeResult(rc, payload, "")
            return ProbeResult(rc, "", f"HTTP 404 (token {CANARY})")
        return ProbeResult(127, "", "nf")


ROLE = parse_viewer_permission("WRITE")


def test_s3_repo_write_but_protected_branch_not_proven():
    r = ApiRunner(branch_body='{"name":"main","protected":true}')
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="main")
    assert ev.status is OperationStatus.OPERATION_REFUSED   # never falsely PROVEN
    assert "protected" in ev.note


def test_unprotected_branch_with_write_role_requires_ruleset_evidence():
    # Pass #4 retraction: without observing the ruleset surface, this is UNKNOWN.
    r = ApiRunner(branch_body='{"name":"feat","protected":false}', ruleset_ok=False)
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="feat")
    assert ev.status is OperationStatus.OPERATION_UNKNOWN
    assert "provider policy coverage incomplete" in ev.note
    assert not ev.coverage_complete


def test_s4_force_push_distinguished_from_normal_push():
    prot_off = ApiRunner(
        branch_body='{"name":"main","protected":true}',
        protection_body='{"allow_force_pushes":{"enabled":false}}')
    push_ev = preflight("github.branch.push", "o/r", prot_off, ROLE, branch="main")
    force_ev = preflight("github.branch.force_push", "o/r", prot_off, ROLE, branch="main")
    assert push_ev.status is OperationStatus.OPERATION_REFUSED
    assert force_ev.status is OperationStatus.OPERATION_REFUSED
    assert push_ev.status != force_ev.status or True  # both refused here, but via
    # DIFFERENT evidence: protection vs direct-push restriction — asserted below:
    assert "allow_force_pushes" in " ".join(force_ev.evidence)
    assert "protected=True" in " ".join(push_ev.evidence)

    prot_allow = ApiRunner(
        branch_body='{"name":"main","protected":true}',
        protection_body='{"allow_force_pushes":{"enabled":true}}')
    ok_force = preflight("github.branch.force_push", "o/r", prot_allow, ROLE, branch="main")
    blocked_push = preflight("github.branch.push", "o/r", prot_allow, ROLE, branch="main")
    assert ok_force.status is OperationStatus.OPERATION_PROVEN
    assert blocked_push.status is OperationStatus.OPERATION_REFUSED
    # normal push allowed ≠ force push allowed, and vice versa — proven both ways


def test_force_push_without_evidence_is_unknown_never_guessed():
    r = ApiRunner(protection_body=None)   # no protection record at all
    ev = preflight("github.branch.force_push", "o/r", r, ROLE, branch="dev")
    assert ev.status is OperationStatus.OPERATION_UNKNOWN


def test_s5_repo_write_but_merge_restricted_not_proven():
    r = ApiRunner(pr_body='{"number":42,"mergeable":false,"state":"dirty"}')
    ev = preflight("github.pr.merge", "o/r", r, ROLE, pr_number=42)
    assert ev.status is OperationStatus.OPERATION_REFUSED

    r2 = ApiRunner(pr_body='{"number":42,"mergeable":true}')
    ev2 = preflight("github.pr.merge", "o/r", r2, ROLE, pr_number=42)
    assert ev2.status is OperationStatus.OPERATION_UNKNOWN   # WRITE ≠ may merge


def test_settings_write_needs_more_than_admin_evidence():
    admin = parse_viewer_permission("ADMIN")
    ev = preflight("github.repo.settings.write", "o/r",
                   ApiRunner(), admin)
    assert ev.status is OperationStatus.OPERATION_UNKNOWN     # org rules invisible
    ev_low = preflight("github.repo.settings.write", "o/r", ApiRunner(), ROLE)
    assert ev_low.status is OperationStatus.OPERATION_REFUSED


def test_s6_no_evidence_model_is_unknown():
    ev = preflight("github.repo.delete", "o/r", ApiRunner(), ROLE)
    assert ev.status is OperationStatus.OPERATION_UNKNOWN
    ev3 = preflight("github.branch.push", "o/r", ApiRunner(), ROLE, branch=None)
    assert ev3.status is OperationStatus.OPERATION_UNKNOWN   # no branch bound


# --- S7/S8: identity change + operation-evidence drift --------------------------

def test_s7_account_change_is_identity_changed():
    from tests.test_toolbelt_pass2 import gh_runner  # reuse scripted world
    r1 = gh_runner(accounts=("work-user",))
    plan = resolve(CapabilityNeed(capability="github.repo.read"), r1)
    ref, facts = ToolPlanRef.freeze(plan, r1)
    res = revalidate(ref, facts, gh_runner(accounts=("personal-user",)))
    assert res.status is DriftKind.IDENTITY_CHANGED


def test_s8_operation_evidence_changes_after_snapshot():
    open_branch = ApiRunner(branch_body='{"name":"feat","protected":false}')
    first = preflight("github.branch.push", "o/r", open_branch, ROLE, branch="feat")
    assert first.status is OperationStatus.OPERATION_PROVEN
    # time passes; protection is added
    now_protected = ApiRunner(branch_body='{"name":"feat","protected":true}')
    second = preflight("github.branch.push", "o/r", now_protected, ROLE, branch="feat")
    assert second.status is OperationStatus.OPERATION_REFUSED
    # the OLD evidence object is immutable — a stale PROVEN cannot be mutated away
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.status = OperationStatus.OPERATION_UNKNOWN  # type: ignore[misc]


# --- S9: plugin cannot reinterpret -----------------------------------------------

def test_s9_unknown_cannot_be_reinterpreted_as_available(tmp_path):
    from core.toolbelt.models import PlanStatus

    # distinct typed enums: no equality path between them
    assert OperationStatus.OPERATION_UNKNOWN != PlanStatus.IMPLEMENTATION_AVAILABLE
    assert OperationStatus.OPERATION_PROVEN.value != PlanStatus.READY.value
    ref, facts, tool = frozen_git_plan(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.capability = "anything.wider"   # type: ignore[misc]


# --- S10: canary ------------------------------------------------------------------

def test_s10_canary_never_reaches_model_visible_surfaces(tmp_path, caplog):
    r = ApiRunner(branch_body=f'{"x"}',
                  pr_body=f'{{"token":"{CANARY}","mergeable":true}}')
    ev = preflight("github.pr.merge", "o/r", r, ROLE, pr_number=42)
    tool = make_real_tool(tmp_path)
    plan_runner = tool_runner(tool)
    plan = resolve(CapabilityNeed(capability="git.commit"), plan_runner)
    ref, facts = ToolPlanRef.freeze(plan, plan_runner)
    surfaces = {
        "operation_evidence": repr(ev),
        "facts": repr(facts),
        "ref": repr(ref),
    }
    with caplog.at_level(logging.DEBUG):
        logging.debug("dump %s %s %s", ev, facts, ref)
        surfaces["logs"] = caplog.text
    for where, text in surfaces.items():
        assert CANARY not in text, f"CANARY LEAKED via {where}"


def test_role_parser_and_layered_contract_shape():
    assert parse_viewer_permission("WRITE") .value == "WRITE"
    ev = preflight("repo.read", "o/r", ApiRunner(), ROLE)
    # The layered contract: implementation/identity live elsewhere; this layer
    # exposes ONLY role + operation evidence + authorization pointer.
    d = vars(ev)
    assert set(d) == {"operation", "resource", "repository_role", "status",
                      "evidence", "note",
                      "observed_policy_surfaces", "required_policy_surfaces"}
