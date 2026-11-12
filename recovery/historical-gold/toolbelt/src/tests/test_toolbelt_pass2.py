"""Toolbelt Pass #2 — freshness, identity binding, resource permission, secret boundary.

All external observation goes through fake runners. The canary secret is planted
in probe output / fake credential worlds only; it must NEVER appear in plans,
argv, refs, serializations, logs, or exceptions.
"""
from __future__ import annotations

import logging

import pytest

from core.toolbelt.evidence import (
    DriftKind,
    ToolPlanRef,
    build_exec_argv,
    collect_plan_evidence,
    revalidate,
)
from core.toolbelt.identities import (
    ResourcePermission,
    github_identities,
    resolve_for_resource,
)
from core.toolbelt.models import CapabilityNeed, InstallSpec, PlanStatus, RepoRef
from core.toolbelt.probes import ProbeResult
from core.toolbelt.resolve import resolve
from core.toolbelt.runtime_manifest import (
    SignalVerdict,
    check_python_against_manifest,
    scan_project_signals,
)
from core.toolbelt.ssh_signing import (
    SshFact,
    SigningState,
    probe_ssh,
    resolve_signing,
)

CANARY = "VOOL_SECRET_CANARY_9f1d2c4b8e7a6f5b3d1c0a9e8f7b6d5c"


class FakeRunner:
    """Scripted probe runner keyed by argv prefix."""

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


def _gh_status(account="work-user", host="github.com", scopes="'gist', 'repo'",
               token_line=True, rc=0):
    tok = f"\ntoken: {CANARY}" if token_line else ""
    return ProbeResult(rc, f"Logged in to {host} account {account}\n"
                           f"Token scopes: {scopes}{tok}", "")


def gh_runner(repo_perm="WRITE", accounts=("work-user",), repo_ok=True):
    status = "\n".join(
        f"Logged in to github.com account {a}\nToken scopes: 'repo'\ntoken: {CANARY}"
        for a in accounts)
    run_map = {
        ("gh", "--version"): ProbeResult(0, "gh version 2.88.1", ""),
        ("gh", "auth"): ProbeResult(0, status, ""),
        ("gh", "repo"): (
            ProbeResult(0, f'{{"nameWithOwner":"o/r","viewerPermission":"{repo_perm}"}}', "")
            if repo_ok else ProbeResult(1, "", "not found")),
    }
    return FakeRunner(which_map={"gh": "/usr/local/bin/gh"}, run_map=run_map)


VENV_PY = ".venv/bin/python"


def make_venv(tmp_path, venv_version="3.12.13"):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n")

    def run(argv, *, cwd=None):
        if str(argv[0]).endswith(VENV_PY):
            if len(argv) >= 3 and argv[1] == "-m":
                return {"pytest": ProbeResult(0, "pytest 8.3.4", "")}.get(
                    argv[2], ProbeResult(1, "", f"No module named {argv[2]}"))
            return ProbeResult(0, f"Python {venv_version}", "")
        if argv[0] == "python3":
            return ProbeResult(0, "Python 3.9.6", "")
        return ProbeResult(127, "", "nf")

    r = FakeRunner(which_map={"python3": "/usr/bin/python3"})
    r.run = run  # type: ignore[method-assign]
    return r


def resolved_pytest_plan(tmp_path):
    return resolve(CapabilityNeed(capability="python.test.pytest",
                                  repository=RepoRef(path=str(tmp_path))),
                   make_venv(tmp_path))


# --- snapshot identity ------------------------------------------------------------------------

def test_plan_ref_is_deterministic_and_secretless():
    r = gh_runner()
    p = resolve(CapabilityNeed(capability="github.repo.read"), r)
    ref, facts = ToolPlanRef.freeze(p, r)
    assert ref.plan_id and len(ref.plan_id) == 16
    assert CANARY not in repr(ref) and CANARY not in repr(facts)
    # evidence keys only — no values inside the handoff object
    assert all("=" not in k for k in ref.evidence_keys)


# --- revalidation attacks ---------------------------------------------------------------------

def test_revalidate_ready_when_world_unchanged():
    r = gh_runner()
    p = resolve(CapabilityNeed(capability="github.repo.read"), r)
    _, facts = ToolPlanRef.freeze(p, r)
    res = revalidate(None, facts, r)
    assert res.status is DriftKind.READY
    assert res.may_proceed_to_gate


def test_binary_replaced_after_discovery_is_stale():
    r1 = FakeRunner(which_map={"git": "/usr/bin/git"},
                    run_map={("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    p = resolve(CapabilityNeed(capability="git.commit"), r1)
    _, facts = ToolPlanRef.freeze(p, r1)
    r2 = FakeRunner(which_map={"git": "/usr/local/bin/git"},
                    run_map={("git", "--version"): ProbeResult(0, "git version 2.51.0", "")})
    res = revalidate(None, facts, r2)
    assert res.status is DriftKind.STALE
    assert not res.may_proceed_to_gate


def test_executable_removed_after_discovery_is_unavailable():
    r1 = FakeRunner(which_map={"git": "/usr/bin/git"},
                    run_map={("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    p = resolve(CapabilityNeed(capability="git.commit"), r1)
    _, facts = ToolPlanRef.freeze(p, r1)
    res = revalidate(None, facts, FakeRunner())  # git gone entirely
    assert res.status is DriftKind.UNAVAILABLE


def test_venv_recreated_with_new_python_is_stale(tmp_path):
    r1 = make_venv(tmp_path, "3.12.13")
    p = resolved_pytest_plan(tmp_path)
    _, facts = ToolPlanRef.freeze(p, r1)
    r2 = make_venv(tmp_path, "3.11.8")   # venv recreated on older python
    res = revalidate(None, facts, r2)
    assert res.status is DriftKind.STALE
    assert any("version changed" in d for d in res.drifted)


def test_credential_revoked_after_discovery_is_unavailable():
    r1 = gh_runner()
    p = resolve(CapabilityNeed(capability="github.repo.read"), r1)
    _, facts = ToolPlanRef.freeze(p, r1)
    r2 = FakeRunner(which_map={"gh": "/usr/local/bin/gh"},
                    run_map={("gh", "--version"): ProbeResult(0, "gh version 2.88.1", ""),
                             ("gh", "auth"): ProbeResult(4, "", "not logged in")})
    res = revalidate(None, facts, r2)
    assert res.status is DriftKind.UNAVAILABLE


def test_github_account_switch_is_identity_changed():
    r1 = gh_runner(accounts=("work-user",))
    p = resolve(CapabilityNeed(capability="github.repo.read"), r1)
    _, facts = ToolPlanRef.freeze(p, r1)
    r2 = gh_runner(accounts=("personal-user",))
    res = revalidate(None, facts, r2)
    assert res.status is DriftKind.IDENTITY_CHANGED
    assert any("identity changed" in d for d in res.drifted)


def test_stale_plan_cannot_silently_execute(tmp_path):
    r1 = gh_runner(accounts=("work-user",))
    p = resolve(CapabilityNeed(capability="github.repo.read"), r1)
    ref, facts = ToolPlanRef.freeze(p, r1)
    r2 = gh_runner(accounts=("other-user",))
    verdict = revalidate(ref, facts, r2)
    assert not verdict.may_proceed_to_gate  # boundary refuses; toolbelt still grants nothing


# --- multi-identity ---------------------------------------------------------------------------

def test_two_valid_accounts_is_ambiguous_never_guessed():
    ids = github_identities(gh_runner(accounts=("personal", "work")))
    assert len(ids) == 2
    res = resolve_for_resource("github.push", "github.com/org/repo",
                               gh_runner(accounts=("personal", "work")))
    assert res.ambiguous and res.selected is None
    assert "AMBIGUOUS_IDENTITY" in res.note


def test_single_account_binds_and_proves_resource_permission():
    res = resolve_for_resource("github.push", "github.com/o/r", gh_runner(repo_perm="WRITE"))
    assert res.selected is not None and not res.ambiguous
    assert res.resource_permission is ResourcePermission.PROVEN
    assert res.scope_hint == ("repo",)          # scope is a HINT layer only
    assert "authorization" in res.note.lower()


def test_scope_exists_but_repo_permission_denied_is_refused():
    res = resolve_for_resource("github.push", "github.com/o/r", gh_runner(repo_perm="READ"))
    # token HAS repo scope, but provider metadata says viewer is READ-only here
    assert res.scope_hint == ("repo",)
    assert res.resource_permission is ResourcePermission.REFUSED


def test_no_provider_evidence_is_unknown_not_guessed():
    res = resolve_for_resource("github.push", "github.com/o/r", gh_runner(repo_ok=False))
    assert res.resource_permission is ResourcePermission.UNKNOWN


# --- project runtime manifests ----------------------------------------------------------------

def test_manifest_conflict_is_reported_not_arbitrated(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    (tmp_path / ".python-version").write_text("3.11\n")
    env = scan_project_signals(tmp_path)
    assert env.verdict is SignalVerdict.CONFLICT
    assert env.python_requirement is None          # no arbitrary winner
    assert check_python_against_manifest("3.12.13", env) is SignalVerdict.CONFLICT


def test_manifest_agreement_selects_requirement(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    env = scan_project_signals(tmp_path)
    assert env.python_requirement == ">=3.12"
    assert check_python_against_manifest("3.12.13", env) is SignalVerdict.SATISFIED
    assert check_python_against_manifest("3.9.6", env) is SignalVerdict.UNSATISFIED


def test_runtime_drift_detected_via_manifest(tmp_path):
    make_venv(tmp_path, "3.11.8")
    (tmp_path / ".python-version").write_text("3.12\n")
    env = scan_project_signals(tmp_path)
    assert check_python_against_manifest("3.11.8", env) is SignalVerdict.UNSATISFIED


# --- executable identity / PATH shadowing ------------------------------------------------------

def test_realpath_binding_detects_shadowed_git():
    r1 = FakeRunner(which_map={"git": "/usr/bin/git"},
                    run_map={("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    p = resolve(CapabilityNeed(capability="git.commit"), r1)
    facts = collect_plan_evidence(p, r1)
    realpath_fact = next(f for f in facts if f.key() == "tool:git.realpath")
    assert realpath_fact.value == "/usr/bin/git"
    # a project-local wrapper named `git` earlier in PATH changes the fresh fact -> STALE
    r2 = FakeRunner(which_map={"git": "/tmp/evil/git"},
                    run_map={("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    res = revalidate(None, facts, r2)
    assert res.status is DriftKind.STALE


def test_build_exec_argv_contains_only_paths_and_literals():
    argv = build_exec_argv("/usr/bin/git", ("commit", "-m", "msg"))
    assert argv == ["/usr/bin/git", "commit", "-m", "msg"]
    assert CANARY not in " ".join(argv)


def test_argv_canary_never_leaks_from_tainted_world(tmp_path, caplog):
    """Fake gh echoes the canary; every constructed command/ref must stay clean."""
    r = gh_runner()
    p = resolve(CapabilityNeed(capability="github.repo.read"), r)
    ref, facts = ToolPlanRef.freeze(p, r)
    argv = build_exec_argv(p.executable or "gh", ("repo", "view"))
    surfaces = {"argv": " ".join(argv), "ref": repr(ref), "facts": repr(facts)}
    with caplog.at_level(logging.DEBUG):
        logging.debug("%s %s", ref, argv)
        surfaces["logs"] = caplog.text
    for where, text in surfaces.items():
        assert CANARY not in text, f"CANARY LEAKED via {where}"


# --- install plan safety -----------------------------------------------------------------------

def test_install_spec_is_typed_hint_is_derived():
    spec = InstallSpec(tool_id="ruff", manager="pip-project", package="ruff",
                       version_constraint="0.15.16", scope=__import__(
                           "core.toolbelt.models", fromlist=["InstallCategory"]
                       ).InstallCategory.PROJECT_LOCAL)
    from core.toolbelt.models import InstallPlan

    plan = InstallPlan.from_spec(spec)
    assert plan.command_hint == ".venv/bin/python -m pip install ruff==0.15.16"
    assert plan.requires_user_approval and plan.reversible
    assert plan.spec.manager == "pip-project"     # typed fields are the authority


def test_install_hint_shell_injection_is_contained():
    from core.toolbelt.models import InstallPlan, InstallCategory

    evil = InstallSpec(tool_id="x", manager="unknown-manager", package="jq; rm -rf /",
                       scope=InstallCategory.USER_TOOL)
    hint = InstallPlan.from_spec(evil).command_hint
    assert hint.startswith("# unmanaged:")      # never an executable shell line


# --- SSH ----------------------------------------------------------------------------------------

def test_ssh_key_present_but_transport_rejected_are_distinct_facts(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAA fake")
    r = FakeRunner(
        which_map={"ssh-keygen": "/usr/bin/ssh-keygen", "ssh": "/usr/bin/ssh"},
        run_map={
            ("ssh-keygen", "-lf"): ProbeResult(0, "256 SHA256:abc123 fake (ED25519)", ""),
            ("ssh",): ProbeResult(255, "",
                                       "git@github.com: Permission denied (publickey)."),
        })
    cap = probe_ssh(r, home=tmp_path)
    assert SshFact.KEY_PRESENT in cap.facts
    assert SshFact.TRANSPORT_REJECTED in cap.facts
    assert cap.github_identity is None
    assert cap.resource_access == "RESOURCE_ACCESS_UNKNOWN"
    assert "AAA" not in repr(cap)                 # key material never captured


def test_ssh_transport_accepted_does_not_imply_push_permission(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAA fake")
    r = FakeRunner(
        which_map={"ssh-keygen": "/usr/bin/ssh-keygen", "ssh": "/usr/bin/ssh"},
        run_map={
            ("ssh-keygen", "-lf"): ProbeResult(0, "256 SHA256:abc123 fake (ED25519)", ""),
            ("ssh",): ProbeResult(1, "", "Hi some-user! "
                                              "You've successfully authenticated..."),
        })
    cap = probe_ssh(r, home=tmp_path)
    assert SshFact.TRANSPORT_ACCEPTED in cap.facts
    assert cap.github_identity == "some-user"
    assert cap.resource_access == "RESOURCE_ACCESS_UNKNOWN"  # -T success ≠ may push repo X


# --- signing -----------------------------------------------------------------------------------

def test_signing_unconfigured_configured_verified_are_distinct():
    unconf = resolve_signing(FakeRunner())
    assert unconf.state is SigningState.UNCONFIGURED
    assert unconf.status is PlanStatus.UNKNOWN

    conf_runner = FakeRunner(run_map={
        ("git", "config"): ProbeResult(0, "ssh", ""),  # both config calls succeed
    })
    configured = resolve_signing(conf_runner)
    assert configured.state is SigningState.CONFIGURED
    assert configured.format == "ssh"
    assert configured.key_handle_id              # handle, not key bytes

    verified = resolve_signing(conf_runner, verify_probe_ok=True)
    assert verified.state is SigningState.VERIFIED
    assert verified.status is PlanStatus.READY
