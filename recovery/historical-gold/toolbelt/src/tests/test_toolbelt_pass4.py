"""Toolbelt Pass #4 FINAL CLOSURE — evidence ceiling + strong-identity policy.

Closes two epistemic overclaims:
A. FAST freshness is drift screening, NOT byte identity. Same-inode/same-size/
   restored-mtime sabotage may pass FAST (contract stays honest); STRONG sha256
   detects it.
B. STATUS=PROVEN requires COMPLETE provider policy coverage. Unobserved
   governing policy (org rulesets) forces UNKNOWN — never PROVEN.
"""
from __future__ import annotations

import os

import pytest

from core.toolbelt.exe_identity import fast_identity, strong_identity
from core.toolbelt.operations import OperationStatus, parse_viewer_permission, preflight
from core.toolbelt.probes import ProbeResult
from core.toolbelt.resolve import resolve
from core.toolbelt.models import CapabilityNeed

from tests.test_toolbelt_pass3 import ApiRunner, FakeRunner, make_real_tool, tool_runner
from core.toolbelt.evidence import ToolPlanRef as _TPR
from core.toolbelt.evidence import revalidate as _reval

ROLE = parse_viewer_permission("WRITE")


# --- A. FAST vs STRONG ----------------------------------------------------------

def _inplace_same_metadata_swap(path: str) -> None:
    """Replace bytes IN PLACE keeping inode, size AND mtime_ns identical."""
    st_before = os.stat(path)
    new_body = b"#!/bin/sh\necho XXXXXXXXX\n"           # exactly same length (25)
    assert len(new_body) == os.path.getsize(path), "fixture must preserve size"
    with open(path, "r+b") as fh:                        # in place -> same inode
        fh.write(new_body)
        fh.flush()
        os.fsync(fh.fileno())
    os.utime(path, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))
    st_after = os.stat(path)
    assert (st_after.st_ino, st_after.st_size, st_after.st_mtime_ns) == \
           (st_before.st_ino, st_before.st_size, st_before.st_mtime_ns)


def test_s1_fast_may_miss_but_contract_stays_honest(tmp_path):
    tool = make_real_tool(tmp_path)
    runner = tool_runner(tool)                    # --version output unchanged
    plan = resolve(CapabilityNeed(capability="git.commit"), runner)
    ref, facts = _TPR.freeze(plan, runner)

    _inplace_same_metadata_swap(tool)
    res = _reval(ref, facts, runner)

    if res.status.value == "READY":
        # Acceptable ONLY because the contract names this layer FAST FRESHNESS.
        assert "drift screening" in __import__("core.toolbelt.exe_identity",
                                               fromlist=["__doc__"]).__doc__
        # ...and no fact claims byte identity:
        assert not any(f.property == "sha256" for f in facts)


def test_s2_strong_sha256_detects_same_metadata_sabotage(tmp_path):
    tool = make_real_tool(tmp_path)
    before = strong_identity(tool)
    assert before.sha256
    _inplace_same_metadata_swap(tool)
    after = strong_identity(tool)
    assert after.sha256 != before.sha256          # bytes changed, all stat layers equal


def test_strong_policy_documented():
    from core.toolbelt import exe_identity

    doc = exe_identity.__doc__
    assert "FAST FRESHNESS" in doc and "STRONG EXECUTABLE IDENTITY" in doc
    assert "drift DETECTION, not attestation" in doc


# --- B/C/D/E. operation evidence ceiling ----------------------------------------

PERMISSIVE_RULESETS = "[]"                                       # explicitly empty
BLOCKING_RULESETS = '[{"rule_type":"PUSH_REQUEST","scope":"repository"}]'


def test_s3_write_unprotected_but_rulesets_unseen_is_unknown():
    r = ApiRunner(branch_body='{"name":"main","protected":false}',
                  ruleset_ok=False)               # adapter cannot inspect rulesets
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="main")
    assert ev.status is OperationStatus.OPERATION_UNKNOWN
    assert not ev.coverage_complete
    assert "provider policy coverage incomplete" in ev.note


def test_s4_explicit_blocking_ruleset_is_refused():
    r = ApiRunner(branch_body='{"name":"main","protected":false}',
                  ruleset_body=BLOCKING_RULESETS)
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="main")
    assert ev.status is OperationStatus.OPERATION_REFUSED
    assert ev.coverage_complete
    assert any("blocking ruleset" in e for e in ev.evidence)


def test_s5_complete_permissive_evidence_is_proven_and_structurally_sound():
    r = ApiRunner(branch_body='{"name":"feat","protected":false}',
                  ruleset_body=PERMISSIVE_RULESETS)
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="feat")
    assert ev.status is OperationStatus.OPERATION_PROVEN
    assert ev.coverage_complete
    assert set(ev.required_policy_surfaces) <= set(ev.observed_policy_surfaces)
    assert "TOCTOU REMAINDER" in ev.full_note()   # race still stated honestly


def test_proven_without_complete_coverage_is_unconstructable():
    from core.toolbelt.operations import OperationEvidence, RepositoryRole

    with pytest.raises(ValueError, match="complete evidence coverage"):
        OperationEvidence(
            operation="github.branch.push", resource="o/r",
            repository_role=RepositoryRole.WRITE,
            status=OperationStatus.OPERATION_PROVEN,
            required_policy_surfaces=("classic_branch_protection", "rulesets"),
            observed_policy_surfaces=("classic_branch_protection",),
        )


def test_repo_read_role_proven_needs_no_extra_surfaces():
    ev = preflight("repo.read", "o/r", ApiRunner(), ROLE)
    assert ev.status is OperationStatus.OPERATION_PROVEN
    assert ev.coverage_complete                   # required surfaces: none beyond role


# --- F/G. authorization + TOCTOU remain separate ---------------------------------

def test_s6_proven_operation_evidence_still_not_platform_authorized(tmp_path):
    r = ApiRunner(branch_body='{"name":"feat","protected":false}',
                  ruleset_body=PERMISSIVE_RULESETS)
    ev = preflight("github.branch.push", "o/r", r, ROLE, branch="feat")
    assert ev.status is OperationStatus.OPERATION_PROVEN
    from core.toolbelt.models import PlanStatus

    plan = resolve(CapabilityNeed(capability="git.commit"),
                   FakeRunner(which_map={"git": "/usr/bin/git"},
                              run_map={("git", "--version"): ProbeResult(0, "git version 9", "")}))
    # Even a fully PROVEN operation carries the platform pointer, never a grant:
    assert plan.authorization == "NOT GRANTED BY TOOLBELT"
    assert "TOCTOU REMAINDER" in ev.full_note()


def test_governing_law_text_present():
    import core.toolbelt.operations as ops_mod

    assert "UNOBSERVED GOVERNING POLICY" in ops_mod.__doc__
