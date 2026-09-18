from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def test_ci_authoritative_gate_no_longer_relies_on_pythonpath_hack() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    verify_job = workflow["jobs"]["verify"]
    run_step = next(
        step
        for step in verify_job["steps"]
        if step.get("name") == "Run authoritative verification"
    )

    assert run_step.get("env") in (None, {})
    assert "ops/verify.py" in run_step["run"]


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
