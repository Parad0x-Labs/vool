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


_FAKE_CLIENT = r'''
import json, os, sys
argv = sys.argv[1:]
if "image" in argv and "inspect" in argv:
    print("linux")
    raise SystemExit(0)
if "run" not in argv:
    raise SystemExit(1)
with open(os.environ["FAKE_CLIENT_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv, "environment": dict(os.environ)}) + "\n")
names = [argv[i + 1] for i, token in enumerate(argv) if token == "--env"]
tail = argv[argv.index("--entrypoint") + 2:]
_isolated, _flag, bootstrap, no_fork, args, wanted = tail[1:]
args = json.loads(args)
args[0] = PYTHON if args[0] == "/usr/local/bin/python3" else args[0]
# Stands in for the container: only the named variables cross, stdin passes through.
environment = {name: os.environ[name] for name in names if name in os.environ}
os.execve(PYTHON, [PYTHON, "-I", "-c", bootstrap, no_fork, json.dumps(args), wanted], environment)
'''

_KEYED_SERVER = r'''
import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") is None:
        continue
    if message["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": os.environ.get("SAMPLE_ACCESS", "")}]}
    else:
        result = {"serverInfo": {"name": "keyed"}, "tools": [{"name": "echo"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


def test_isolated_runtime_key_never_enters_a_host_environment_or_argv(tmp_path, monkeypatch):
    import json
    import secrets
    import socket
    import sys
    import tempfile

    from core import plugin_executor
    from core.mcp_client import MCPStdioClient

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    client = bin_dir / "docker"
    client.write_text(f"#!{sys.executable}\nPYTHON = {sys.executable!r}\n" + _FAKE_CLIENT)
    client.chmod(0o755)
    endpoint = Path(tempfile.mkdtemp(prefix="vool-sock-", dir="/tmp")) / "d.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    log = tmp_path / "client.log"
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin")
    monkeypatch.setenv("VOOL_PLUGIN_DOCKER_SOCKET", str(endpoint))
    monkeypatch.setenv("FAKE_CLIENT_LOG", str(log))
    plugin_container._PROBE.clear()
    package = tmp_path / "package"
    package.mkdir()
    (package / "server.py").write_text(_KEYED_SERVER)
    monkeypatch.setattr(plugin_executor, "confined_argv", lambda argv, **options: plugin_container.command(
        argv, working_directory=options["working_directory"], writable_roots=options["writable_roots"]))
    value = secrets.token_urlsafe(32)
    try:
        server = MCPStdioClient(sys.executable, [str(package / "server.py")], env={"SAMPLE_ACCESS": value},
                                env_allowlist=("FAKE_CLIENT_LOG",), cwd=str(package), timeout=15.0,
                                private_environment=True)
        with server:
            server.list_tools()
            assert server.call_tool_text("echo") == value
    finally:
        listener.close()
        endpoint.unlink(missing_ok=True)
        endpoint.parent.rmdir()
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows and value not in json.dumps(rows)
