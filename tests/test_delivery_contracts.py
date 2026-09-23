from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def test_ci_authoritative_gate_no_longer_relies_on_pythonpath_hack() -> None:
    """The CI gate runs through the installed package and the repo's own ops tooling -- no step
    anywhere in the workflow may carry a PYTHONPATH env (the hack this test once removed), and
    the fast verify leg's checks (pinned ruff, canonical collection) are the repo's own
    entry points. The full authoritative EXECUTION is the shard matrix this verify leg feeds."""
    workflow = _load_yaml(".github/workflows/ci.yml")
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps") or []:
            env = step.get("env") or {}
            assert not env.get("PYTHONPATH"), f"job {job_name} step {step.get('name')} PYTHONPATH hack"
    verify_job = workflow["jobs"]["verify"]
    runs = "\n".join(str(step.get("run") or "") for step in verify_job["steps"])
    assert "ruff check ." in runs
    assert "ops/pytest_manifest.py" in runs


def test_ci_routes_macos_only_suites_to_the_macos_job_and_nowhere_else() -> None:
    """Genuinely macOS-only suites are ROUTED to a macOS runner, never silently skipped.

    The Linux shard resolver filters exactly the file set the `macos` job runs; this pins the
    two lists to each other so a future edit cannot drop a file on the floor (skipped
    everywhere) or run it twice (macOS-only assertions failing red on Linux shards)."""
    workflow = _load_yaml(".github/workflows/ci.yml")
    macos_job = workflow["jobs"]["macos"]
    assert macos_job["runs-on"] == "macos-latest"

    run_step = next(step for step in macos_job["steps"] if step.get("name") == "Run the macOS-only suites")
    run_files = sorted(
        token for token in str(run_step["run"]).split() if token.startswith("tests/")
    )

    resolver = next(
        step for step in workflow["jobs"]["tests"]["steps"] if step.get("name") == "Resolve this shard's test files"
    )
    resolver_text = "\n".join(str(resolver["run"]).splitlines())
    assert "macos_only = {" in resolver_text
    routed = sorted(
        line.strip().rstrip(",").strip('"')
        for line in resolver_text.splitlines()
        if line.strip().startswith('"tests/')
    )

    assert routed == run_files, (
        f"macos routing drifted: resolver routes {routed}, macos job runs {run_files}"
    )
    # The routing must also be load-bearing: the resolver refuses to continue if any of the
    # routed files disappears from collection, so a rename cannot silently un-route a suite.
    assert "macos routing drifted" in resolver_text


def test_ci_shard_step_measures_and_partitions_through_the_tested_resolver() -> None:
    """The shard run step is wrapped by ops/pytest_timing.py for per-file evidence.

    Three things must hold together or the measurement is not trustworthy: the
    resolver's partition is duration-aware THROUGH ops/shard_resolver.py over the
    committed evidence snapshot (ops/shard_weights.json) -- never an inline
    reimplementation that could drift from the tested module (whose teeth live in
    tests/test_shard_resolver.py: exact-once coverage, untrusted-data validation,
    and the documented size round-robin safe fallback); the wrapped command runs
    the SAME pytest argv over the SAME file list in the SAME order; and the
    artifact upload stays `if: always()` so a red shard still uploads its timing
    manifest -- evidence beside the verdict, never a substitute for it (the
    wrapper's exit status IS pytest's own)."""
    workflow = _load_yaml(".github/workflows/ci.yml")

    resolver = next(
        step
        for step in workflow["jobs"]["tests"]["steps"]
        if step.get("name") == "Resolve this shard's test files"
    )
    resolver_text = str(resolver["run"])
    # The partition authority is the tested resolver module over the committed
    # snapshot -- the workflow step invokes it, it does not restate it.
    assert "python ops/shard_resolver.py" in resolver_text
    assert "--weights ops/shard_weights.json" in resolver_text
    assert '--output ".verification-logs/shard-${{ matrix.shard }}-files.txt"' in resolver_text
    # Canonical collection still feeds it: pytest's own collector decides files.
    assert "--collect-only" in resolver_text
    # No partition logic may be re-inlined beside the tested module: the old
    # size round-robin exists only inside ops.shard_resolver / ops.shard_plan.
    assert "index % shards == shard" not in resolver_text

    run_step = next(
        step
        for step in workflow["jobs"]["tests"]["steps"]
        if step.get("name") == "Run shard ${{ matrix.shard }}"
    )
    run_text = " ".join(str(run_step["run"]).split())
    assert "python ops/pytest_timing.py" in run_text
    assert '--output ".verification-logs/shard-${{ matrix.shard }}-timing.json"' in run_text
    # The pytest invocation the wrapper receives is the one the shard always ran.
    assert "-- -q --tb=short -p no:cacheprovider" in run_text
    assert "$(tr '\\n' ' ' < .verification-logs/shard-${{ matrix.shard }}-files.txt)" in run_text
    # Timing capture must not swallow failures: no continue-on-error, no || true.
    assert not run_step.get("continue-on-error")

    upload = next(
        step for step in workflow["jobs"]["tests"]["steps"] if step.get("name") == "Upload shard log"
    )
    assert upload.get("if") == "always()"
    assert upload.get("with", {}).get("include-hidden-files") is True


def test_ci_build_job_smokes_the_built_wheel_outside_repo_checkout() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    build_job = workflow["jobs"]["build"]
    build_step = next(step for step in build_job["steps"] if step.get("name") == "Build package")
    smoke_step = next(step for step in build_job["steps"] if step.get("name") == "Smoke install built wheel")

    assert "python -m build" in build_step["run"]
    assert "python -m venv /tmp/vool-wheel-venv" in smoke_step["run"]
    assert "pip install dist/*.whl" in smoke_step["run"]
    assert "cd /tmp" in smoke_step["run"]
    assert "import apps.vool_api_server" in smoke_step["run"]
    assert "import apps.brain_hive_watch_server" in smoke_step["run"]
    assert "import core.tool_intent_executor" in smoke_step["run"]
    assert "import tools.registry" in smoke_step["run"]
    assert "import relay.channel_outbound" in smoke_step["run"]
    assert "import installer.register_openclaw_agent" in smoke_step["run"]


def test_dockerfile_builds_from_wheel_as_non_root_and_uses_healthz() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim AS build" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile
    assert "python -m build --wheel" in dockerfile
    assert "pip install --no-cache-dir /tmp/dist/*.whl" in dockerfile
    assert "USER vool" in dockerfile
    assert "http://localhost:11435/healthz" in dockerfile


def test_compose_default_avoids_cli_restart_loops_and_has_service_healthchecks() -> None:
    compose = _load_yaml("docker-compose.yml")
    services = compose["services"]

    agent_two = services["agent-2"]
    assert agent_two["profiles"] == ["oneshot"]
    assert agent_two["restart"] == "no"
    assert "--input" in list(agent_two["command"])

    for service_name, expected_probe in {
        "meet-eu": "/v1/readyz",
        "agent-1": "/healthz",
        "brain-hive-watch": "/healthz",
    }.items():
        service = services[service_name]
        probe = " ".join((service.get("healthcheck") or {}).get("test") or [])
        assert expected_probe in probe


def test_compose_integration_only_services_are_profile_gated() -> None:
    compose = _load_yaml("docker-compose.yml")
    services = compose["services"]

    assert services["meet-us"]["profiles"] == ["integration"]
    assert services["daemon-1"]["profiles"] == ["integration"]
    daemon_probe = " ".join((services["daemon-1"].get("healthcheck") or {}).get("test") or [])
    assert "/healthz" in daemon_probe


def test_runtime_dependency_lists_cover_yaml_and_psutil() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    runtime_requirements = (REPO_ROOT / "requirements-runtime.txt").read_text(encoding="utf-8")

    for marker in ('"psutil>=5.9"', '"pyyaml>=6.0"'):
        assert marker in pyproject
    for marker in ("psutil>=5.9", "pyyaml>=6.0"):
        assert marker in requirements
        assert marker in runtime_requirements
    assert 'proof = [' in pyproject
    assert '"pytest>=7.0"' in pyproject
    assert "pytest>=7.0" in requirements
    assert "pytest>=7.0" in runtime_requirements
