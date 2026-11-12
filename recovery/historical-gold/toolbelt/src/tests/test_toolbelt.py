"""Toolbelt adversarial + leak-canary tests.

All external observation goes through fake runners — no real subprocesses, no real
credentials. The canary secret VOOL_SECRET_CANARY_9f1d2c4b is placed behind the
credential abstraction only; the invariant under test is that it NEVER surfaces in
any model-visible object, serialization, log, exception, or receipt.
"""
from __future__ import annotations

import logging

import pytest

from core.toolbelt.credentials import gh_credential_handle
from core.toolbelt.inventory import build_inventory, probe_tool, resolve_runtime
from core.toolbelt.models import (
    AuthState,
    CapabilityNeed,
    HealthState,
    PlanStatus,
    PresenceState,
    RepoRef,
    ToolbeltSnapshot,
)
from core.toolbelt.probes import ProbeResult, extract_version
from core.toolbelt.resolve import resolve
from core.toolbelt.version_spec import satisfies

CANARY = "VOOL_SECRET_CANARY_9f1d2c4b8e7a6f5b3d1c0a9e8f7b6d5c"


class FakeRunner:
    """Deterministic probe runner: scripted which() and per-command ProbeResults."""

    def __init__(self, which_map=None, run_map=None):
        self.which_map = which_map or {}
        self.run_map = run_map or {}

    def which(self, name):
        return self.which_map.get(name)

    def run(self, argv, *, cwd=None):
        key = tuple(argv[:2])
        if key in self.run_map:
            return self.run_map[key]
        if argv[0] in self.run_map:
            rc, out, err = self.run_map[argv[0]]
            return ProbeResult(rc, out, err)
        return ProbeResult(127, "", "executable not found")


def _gh_authed_runner(repo_read_ok=True):
    """gh installed, healthy, authenticated as 'work-user'; optional repo read."""
    return FakeRunner(
        which_map={"gh": "/usr/local/bin/gh"},
        run_map={
            ("gh", "--version"): ProbeResult(0, "gh version 2.88.1", ""),
            ("gh", "auth"): ProbeResult(
                0, f"Logged in to github.com account work-user\nToken scopes: 'gist', 'repo'\ntoken: {CANARY}", ""),
            ("gh", "repo"): (
                ProbeResult(0, '{"nameWithOwner":"saul/vool"}', "") if repo_read_ok
                else ProbeResult(1, "", "read denied")),
        },
    )


# --- version spec -----------------------------------------------------------------------------

def test_version_satisfies_basic():
    assert satisfies("3.12.13", ">=3.12,<3.13")
    assert not satisfies("3.9.6", ">=3.12")
    assert satisfies("22.1.0", ">=22")
    assert satisfies("2.50.1", ">=2.40")


def test_version_fails_closed():
    assert not satisfies(None, ">=1")
    assert not satisfies("garbage", ">=1")
    assert not satisfies("3.12", "~=3")  # unparsable clause fails closed


def test_extract_version_variants():
    assert extract_version("git", ProbeResult(0, "git version 2.50.1 (Apple Git-155)", "")) == "2.50.1"
    assert extract_version("node", ProbeResult(0, "v22.14.0", "")) == "22.14.0"
    assert extract_version("py", ProbeResult(0, "", "Python 3.9.6")) == "3.9.6"
    assert extract_version("x", ProbeResult(1, "", "boom")) is None


# --- presence != capability -------------------------------------------------------------------

def test_missing_tool_is_not_available():
    p = probe_tool("git", FakeRunner())  # nothing on PATH
    assert p.presence is PresenceState.MISSING


def test_executable_exists_but_crashes_is_broken():
    r = FakeRunner(which_map={"weirdctl": "/usr/bin/weirdctl"})
    # no scripted output → command not handled → 127 path vs crash path both fail closed
    p = probe_tool("weirdctl", r)
    assert p.presence in (PresenceState.BROKEN, PresenceState.MISSING)
    assert p.presence is not PresenceState.INSTALLED


def test_installed_but_unauthenticated_blocks_github_read():
    # gh installed and healthy but auth status shows no login.
    r = FakeRunner(
        which_map={"gh": "/usr/local/bin/gh"},
        run_map={
            ("gh", "--version"): ProbeResult(0, "gh version 2.88.1", ""),
            ("gh", "auth"): ProbeResult(4, "", "not logged in"),
            ("gh", "repo"): ProbeResult(1, "", "not logged in"),
        },
    )
    plan = resolve(CapabilityNeed(capability="github.repo.read"), r)
    assert plan.status is PlanStatus.BLOCKED
    assert any("unauthenticated" in b for b in plan.blockers)


def test_gh_identity_and_scopes_parsed_safely():
    handle = gh_credential_handle(_gh_authed_runner())
    assert handle.status is AuthState.AUTHENTICATED
    assert handle.identity == "work-user@github.com"
    assert handle.scopes == ("gist", "repo")
    assert CANARY not in repr(handle)


def test_docker_client_without_daemon_is_service_unavailable():
    r = FakeRunner(
        which_map={"docker": "/usr/local/bin/docker"},
        run_map={
            ("docker", "--version"): ProbeResult(0, "Docker version 27.3.1", ""),
            ("docker", "info"): ProbeResult(
                1, "", "Cannot connect to the Docker API at unix:///x.sock"),
        },
    )
    plan = resolve(CapabilityNeed(capability="docker.build"), r)
    assert plan.status is PlanStatus.BLOCKED
    assert any("SERVICE_UNAVAILABLE" in b for b in plan.blockers)


# --- project env vs global --------------------------------------------------------------------

VENV_PY = ".venv/bin/python"


def _venv_runner(tmp_path, venv_version="3.12.13"):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n")

    def run(argv, *, cwd=None):
        if str(argv[0]).endswith(VENV_PY):
            if len(argv) >= 3 and argv[1] == "-m":
                mod = argv[2]
                return {"pytest": ProbeResult(0, "pytest 8.3.4", "")}.get(
                    mod, ProbeResult(1, "", f"No module named {mod}"))
            return ProbeResult(0, f"Python {venv_version}", "")
        if argv[0] == "python3":
            return ProbeResult(0, "Python 3.9.6", "")
        return ProbeResult(127, "", "nf")

    r = FakeRunner(which_map={"python3": "/usr/bin/python3"})
    r.run = run  # type: ignore[method-assign]
    return r


def test_project_venv_wins_over_wrong_global_python(tmp_path):
    rt = resolve_runtime(tmp_path, _venv_runner(tmp_path))
    assert rt.kind == "PROJECT_VENV"
    assert rt.version == "3.12.13"
    assert "NOT release evidence" in rt.reason  # global 3.9.6 called out explicitly


def test_python_capability_requires_project_venv(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = FakeRunner(which_map={"python3": "/usr/bin/python3"},
                   run_map={("python3", "--version"): ProbeResult(0, "Python 3.9.6", "")})
    plan = resolve(CapabilityNeed(capability="python.test.pytest",
                                  repository=RepoRef(path=str(empty))), r)
    assert plan.status is PlanStatus.UNKNOWN
    assert any("refusing to trust global runtime" in b for b in plan.blockers)


def test_python_module_missing_from_project_env(tmp_path):
    plan = resolve(CapabilityNeed(capability="python.lint.ruff",
                                  repository=RepoRef(path=str(tmp_path))),
                   _venv_runner(tmp_path))
    assert plan.status is PlanStatus.BLOCKED
    assert any("ruff" in b for b in plan.blockers)


def test_python_module_present_in_project_env(tmp_path):
    plan = resolve(CapabilityNeed(capability="python.test.pytest",
                                  repository=RepoRef(path=str(tmp_path))),
                   _venv_runner(tmp_path))
    assert plan.status is PlanStatus.READY
    assert plan.environment.startswith("project:")


def test_version_compatibility_block(tmp_path):
    plan = resolve(CapabilityNeed(capability="python.test", constraints={"python": ">=3.12"},
                                  repository=RepoRef(path=str(tmp_path))),
                   _venv_runner(tmp_path, venv_version="3.9.1"))
    assert plan.status is PlanStatus.BLOCKED
    assert any("mismatch" in b for b in plan.blockers)


# --- install proposals ------------------------------------------------------------------------

def test_missing_tool_proposes_install_never_executes():
    plan = resolve(CapabilityNeed(capability="jq.query"), FakeRunner())
    assert plan.status is PlanStatus.INSTALLABLE
    assert plan.install_plan.command_hint == "brew install jq"
    assert plan.install_plan.requires_user_approval


# --- secret leak canary -----------------------------------------------------------------------

def test_platform_redactor_scrubs_canary():
    from core.secret_redaction import redact_secrets

    scrubbed = redact_secrets(f"token={CANARY}")
    assert CANARY not in scrubbed


def test_canary_secret_never_surfaces_anywhere(tmp_path, caplog):
    """Run every toolbelt path over a canary-tainted world; assert the canary appears
    NOWHERE in model-visible objects, JSON, logs, exceptions, or receipts."""
    r = _venv_runner(tmp_path)
    snapshot = ToolbeltSnapshot(
        repository=RepoRef(path=str(tmp_path)),
        tools=build_inventory(tmp_path, r),
        runtime=resolve_runtime(tmp_path, r),
        plan=(
            resolve(CapabilityNeed(capability="github.repo.read"), _gh_authed_runner()),
            resolve(CapabilityNeed(capability="python.test.pytest",
                                   repository=RepoRef(path=str(tmp_path))), r),
        ),
    )
    surfaces = {
        "repr": repr(snapshot),
        "json": snapshot.to_json(),
        "dict": str(snapshot.to_dict()),
    }
    with caplog.at_level(logging.DEBUG):
        logging.debug("snapshot dump: %s", snapshot.to_json())
        try:
            raise RuntimeError(f"failed resolving: {snapshot!r}")
        except RuntimeError as exc:
            surfaces["exception"] = str(exc)
        surfaces["logs"] = caplog.text
    for where, text in surfaces.items():
        assert CANARY not in text, f"CANARY LEAKED via {where}"


def test_serialization_refuses_secret_like_fields():
    """Structural guard rail: adding a token field must break serialization loudly."""
    from dataclasses import dataclass

    from core.toolbelt.models import _safe_dict

    @dataclass(frozen=True)
    class Sneaky:
        tool_id: str = "x"
        token: str = CANARY

    with pytest.raises(ValueError, match="refusing to serialize"):
        _safe_dict(Sneaky())


# --- fail-closed / stale-evidence -------------------------------------------------------------

def test_tool_removed_between_which_and_run_fails_closed():
    """which() succeeds, then the binary vanishes → MISSING, never AVAILABLE."""
    r = FakeRunner(which_map={"jq": "/opt/homebrew/bin/jq"},
                   run_map={("jq", "--version"): ProbeResult(127, "", "executable not found")})
    p = probe_tool("jq", r)
    assert p.presence is PresenceState.MISSING
    assert any("vanished" in n for n in p.notes)
