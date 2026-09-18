from __future__ import annotations

import plistlib

from core import openclaw_locator as locator


def _write_openclaw_home(home, token: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "openclaw.json").write_text(
        f'{{"gateway":{{"auth":{{"token":"{token}"}}}}}}',
        encoding="utf-8",
    )


def test_discover_openclaw_paths_honors_env_state_dir(monkeypatch, tmp_path) -> None:
    env_home = tmp_path / "env-state"
    dot_home = tmp_path / ".openclaw"
    _write_openclaw_home(env_home, "env-token")
    _write_openclaw_home(dot_home, "dot-token")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(env_home))

    paths = locator.discover_openclaw_paths()

    assert paths.home == env_home
    assert paths.source == "env_state_dir"
    assert locator.load_gateway_token(paths) == "env-token"


def test_discover_openclaw_paths_prefers_launch_agent_state_dir(monkeypatch, tmp_path) -> None:
    dot_home = tmp_path / ".openclaw"
    default_home = tmp_path / ".openclaw-default"
    _write_openclaw_home(dot_home, "dot-token")
    _write_openclaw_home(default_home, "default-token")
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    with (launch_agents / locator.OPENCLAW_GATEWAY_LAUNCH_AGENT).open("wb") as handle:
        plistlib.dump(
            {
                "Label": "ai.openclaw.gateway",
                "EnvironmentVariables": {
                    "OPENCLAW_STATE_DIR": str(default_home),
                },
            },
            handle,
        )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)

    paths = locator.discover_openclaw_paths()

    assert paths.home == default_home
    assert paths.source == "launchd_state_dir"
    assert locator.load_gateway_token(paths) == "default-token"


def test_discover_openclaw_paths_uses_dot_home_default_when_default_home_missing(
    monkeypatch,
    tmp_path,
) -> None:
    default_home = tmp_path / ".openclaw-default"
    _write_openclaw_home(default_home, "default-token")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)

    paths = locator.discover_openclaw_paths()

    assert paths.home == default_home
    assert paths.source == "dot_home_default"
    assert locator.load_gateway_token(paths) == "default-token"
