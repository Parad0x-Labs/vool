"""Runtime configuration refuses absent, remote and non-socket endpoints."""
from pathlib import Path

import pytest

from core import plugin_container
from core.plugin_executor import ConfinementUnavailableError


@pytest.mark.parametrize("value", ["", "tcp://example.org:2375", "ssh://example.org", "relative.sock"])
def test_container_runtime_never_uses_a_remote_or_ambient_daemon(monkeypatch, value):
    monkeypatch.setenv("VOOL_PLUGIN_DOCKER_SOCKET", value)
    assert not plugin_container.available()
    with pytest.raises(ConfinementUnavailableError, match="unavailable"):
        plugin_container.command(["true"], working_directory=Path.cwd(), writable_roots=())


@pytest.mark.parametrize("kind", ["missing", "file", "directory"])
def test_container_endpoint_must_be_a_local_unix_socket(tmp_path, monkeypatch, kind):
    endpoint = tmp_path / "socket"
    if kind == "file":
        endpoint.write_text("not a daemon")
    elif kind == "directory":
        endpoint.mkdir()
    monkeypatch.setenv("VOOL_PLUGIN_DOCKER_SOCKET", str(endpoint))
    assert not plugin_container.available()
