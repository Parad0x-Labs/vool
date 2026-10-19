"""Credential files stay unreadable to a sandboxed job, with or without the network.

Measured on main 48dfe06 with bwrap: a job whose read root contains the home (here a workspace and
read root under a temporary HOME) read `~/.npmrc`, `~/.pypirc`, `~/.netrc`, `~/.git-credentials`,
`~/.docker/config.json`, `~/.kube/config`, `~/.cargo/credentials.toml` and even `~/.ssh/id_rsa`:
the bwrap backend hid the home behind a tmpfs and then bound it straight back, and never applied
the secret deny list at all (only the macOS profile did). Once an approved install can reach the
network, any of those is one request away from leaving the machine.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sandbox.job_runner import JobRunner, _macos_confined_profile, _sensitive_read_deny_roots
from sandbox.resource_limits import ExecutionPolicy

SECRETS = [
    ".npmrc", ".yarnrc", ".yarnrc.yml", ".pypirc", ".netrc", ".git-credentials",
    ".config/git/credentials", ".docker/config.json", ".kube/config", ".cargo/credentials",
    ".cargo/credentials.toml", ".gem/credentials", ".config/hub", ".terraform.d/credentials.tfrc.json",
    ".azure/accessTokens.json", ".config/op/config", ".ssh/id_rsa",
]


def _bwrap_usable() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap or not sys.platform.startswith("linux"):
        return False
    probe = subprocess.run(
        [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.fixture
def home_with_secrets(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    for relative in SECRETS:
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SECRET-VALUE\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return home


DENIED_ROOTS = [
    ".npmrc", ".yarnrc", ".yarnrc.yml", ".pypirc", ".netrc", ".git-credentials", ".config/git/credentials",
    ".docker", ".kube", ".cargo/credentials", ".cargo/credentials.toml", ".gem/credentials", ".config/hub",
    ".terraform.d", ".azure", ".config/op", ".ssh",
]


def test_every_credential_path_is_on_the_deny_list(home_with_secrets: Path) -> None:
    denied = {str(path) for path in _sensitive_read_deny_roots()}
    missing = [name for name in DENIED_ROOTS if str(home_with_secrets / name) not in denied]
    assert missing == []


def test_the_macos_profile_denies_reading_each_credential_path(home_with_secrets: Path, tmp_path: Path) -> None:
    workspace = home_with_secrets / "projects" / "app"
    workspace.mkdir(parents=True)
    profile = _macos_confined_profile((workspace,), deny_network=False, read_roots=(home_with_secrets,))
    secret_clause = profile[profile.index("(deny file-read* (subpath"):]
    for name in (".npmrc", ".pypirc", ".netrc", ".git-credentials", ".docker", ".kube", ".azure", ".terraform.d"):
        assert f'(subpath "{(home_with_secrets / name).resolve()}")' in secret_clause, name


@pytest.mark.skipif(not _bwrap_usable(), reason="needs a usable bwrap backend")
@pytest.mark.parametrize("network", [False, True])
def test_a_job_cannot_read_credentials_inside_a_bound_home(home_with_secrets: Path, network: bool) -> None:
    workspace = home_with_secrets / "projects" / "app"
    workspace.mkdir(parents=True)
    runner = JobRunner(
        ExecutionPolicy(
            workspace_root=workspace,
            writable_roots=(workspace,),
            read_roots=(home_with_secrets,),
            max_seconds=30,
            max_output_kb=64,
            allow_network_egress=network,
        )
    )
    # The home is named inside the script: the argv path guard refuses an absolute path argument
    # outside the workspace, and the question here is what the KERNEL lets a runtime open() read.
    script = (
        "import pathlib, sys\n"
        "for name in sys.argv[1:]:\n"
        f"    path = pathlib.Path({str(home_with_secrets)!r}) / name\n"
        "    try:\n"
        "        print(name, 'READ' if 'SECRET-VALUE' in path.read_text() else 'EMPTY')\n"
        "    except Exception as exc:\n"
        "        print(name, type(exc).__name__)\n"
    )
    result = runner.run(["python3", "-c", script, *SECRETS], cwd=workspace)
    assert result.returncode == 0, result.stderr
    lines = dict(line.split(" ", 1) for line in result.stdout.strip().splitlines())
    assert set(lines) == set(SECRETS)
    leaked = [name for name, verdict in lines.items() if verdict == "READ"]
    assert leaked == [], f"credential files readable from the sandbox: {leaked}"
    # The workspace itself, inside the same home, stays readable and writable.
    probe = runner.run(["python3", "-c", "open('ok.txt', 'w').write('x'); print(open('ok.txt').read())"], cwd=workspace)
    assert probe.returncode == 0 and probe.stdout.strip() == "x", probe.stderr
