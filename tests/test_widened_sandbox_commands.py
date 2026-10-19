"""The widened command set: more tools, chained lines, and the network for an approved install.

Measured on main 48dfe06 (Bypass, the real executor and the bwrap sandbox): `make`, `go`, `uv`,
`diff`, `jq`, `tsc` were "not in Sandbox whitelist"; `pytest -q | tail -3` and `cd sub && npm test`
were "Compound shell syntax is not allowed"; and an install could never reach a registry, even
after the operator approved it, because every job was built with the network off.

These pin what now runs, and every refusal that has to stay: shells and command runners, syntax
outside the chain grammar, recursive deletes in any spelling, a `cd` out of the workspace, and the
network for anything the operator did not approve as an install.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.mode_permission_policy import PermissionAction, command_actions
from tests.test_approved_commands_run_in_the_served_path import (
    SANDBOX,
    _Turn,
    chat_autonomy,
    workspace,
)

# ------------------------------------------------------------------------------------------------
# The classifier: a chain does everything its commands do; installs are named in every tool
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("python3 -m pytest -q 2>&1 | tail -5", {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS, PermissionAction.RUN_SAFE_COMMANDS}),
        ("cat notes.txt | wc -l", {PermissionAction.RUN_SAFE_COMMANDS}),
        ("pytest -q && git push", {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS, PermissionAction.GIT_PUSH}),
        ("ls && rm build.log", {PermissionAction.RUN_SAFE_COMMANDS, PermissionAction.DELETE_FILES}),
        ("go get example.com/x", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("go mod download", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("bun add left-pad", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("deno cache main.ts", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("uv pip install x", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("uvx ruff", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("bunx cowsay", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("go test ./...", {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("echo hi > out && ls", {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
    ],
)
def test_each_command_names_what_it_does(command: str, expected: set) -> None:
    assert set(command_actions(command)) == expected


# ------------------------------------------------------------------------------------------------
# New tools
# ------------------------------------------------------------------------------------------------

TOOL_RUNS = [
    ("diff", "diff notes.txt notes.txt", 0),
    ("jq", "jq -n 1", 0),
    ("sort", "sort notes.txt", 0),
    ("uniq", "uniq notes.txt", 0),
    ("make", "make -f Makefile.test hello", 0),
    ("go", "go version", 0),
    ("gofmt", "gofmt -l .", 0),
]


@SANDBOX
@pytest.mark.parametrize(("tool", "command", "returncode"), TOOL_RUNS)
def test_a_new_tool_runs_in_auto_when_installed(workspace, chat_autonomy, tool, command, returncode) -> None:
    if not shutil.which(tool):
        pytest.skip(f"{tool} is not installed on this host")
    (workspace / "Makefile.test").write_text("hello:\n\t@echo hello-from-make\n", encoding="utf-8")
    execution = _Turn(workspace, "auto").command(command)
    assert execution.status == "executed", (command, execution.status, execution.response_text)
    assert execution.details["returncode"] == returncode


@SANDBOX
def test_rm_asks_in_auto_and_deletes_only_after_allow(workspace, chat_autonomy) -> None:
    turn = _Turn(workspace, "auto")
    first = turn.command("rm notes.txt")
    assert first.status == "pending_approval"
    assert (workspace / "notes.txt").exists()
    second = turn.command("rm notes.txt", approval_token=turn.approve(first))
    assert second.status == "executed", second.response_text
    assert not (workspace / "notes.txt").exists()


@SANDBOX
@pytest.mark.parametrize("command", ["rm -rf build", "rm -fr build", "rm -r -f build", "rm -R build", "rm --recursive build",
                                     "rm --rec build", "ls && rm -r build"])
def test_a_recursive_delete_is_refused_in_bypass_in_every_spelling(workspace, chat_autonomy, command) -> None:
    (workspace / "build").mkdir()
    (workspace / "build" / "x").write_text("x", encoding="utf-8")
    execution = _Turn(workspace, "bypass_permissions").command(command)
    assert execution.status == "blocked_by_policy", (command, execution.status, execution.response_text)
    assert (workspace / "build" / "x").exists()


@SANDBOX
@pytest.mark.parametrize("command", ["bash -c 'touch RAN'", "sh -c 'touch RAN'", "xargs touch RAN", "docker run x",
                                     "env bash -c 'touch RAN'", "ls && bash -c 'touch RAN'"])
def test_shells_and_command_runners_stay_refused_in_bypass(workspace, chat_autonomy, command) -> None:
    execution = _Turn(workspace, "bypass_permissions").command(command)
    assert execution.status == "blocked_by_policy", (command, execution.status, execution.response_text)
    assert not (workspace / "RAN").exists()


# ------------------------------------------------------------------------------------------------
# Chained lines
# ------------------------------------------------------------------------------------------------


@SANDBOX
def test_chained_lines_run_with_the_shells_meaning(workspace, chat_autonomy) -> None:
    (workspace / "sub").mkdir()
    turn = _Turn(workspace, "auto")

    piped = turn.command("python3 -c 'print(1); print(2); print(3)' 2>&1 | tail -1")
    assert piped.status == "executed" and piped.details["stdout"].strip() == "3", piped.response_text

    skipped = turn.command("false && mkdir SHOULD_NOT")
    assert skipped.details["returncode"] == 1 and not (workspace / "SHOULD_NOT").exists()

    fallback = turn.command("false || mkdir FALLBACK")
    assert fallback.status == "executed" and (workspace / "FALLBACK").is_dir()

    inside = turn.command("cd sub && mkdir made_in_sub")
    assert inside.status == "executed", inside.response_text
    assert (workspace / "sub" / "made_in_sub").is_dir()

    # A pipeline reports its last command's status, as sh does.
    status = turn.command("false | true")
    assert status.status == "executed" and status.details["returncode"] == 0


@SANDBOX
@pytest.mark.parametrize(
    "command",
    [
        "echo hi > OUT && ls",
        "ls && echo $HOME > OUT",
        "ls && touch OUT &",
        "ls && (touch OUT)",
        "ls && touch `echo OUT`",
        "ls | xargs touch",
        "cd .. && touch OUT",
        "cd / && ls",
        "cd sub ; touch OUT",
        "ls && curl http://127.0.0.1:9 -o OUT",
        "mkdir OUT && bash -c 'echo hi'",
    ],
)
def test_syntax_or_a_command_outside_the_rules_refuses_the_whole_line(workspace, chat_autonomy, command) -> None:
    (workspace / "sub").mkdir()
    execution = _Turn(workspace, "bypass_permissions").command(command)
    assert execution.status == "blocked_by_policy", (command, execution.status, execution.response_text)
    assert not (workspace / "OUT").exists()
    assert not (workspace.parent / "OUT").exists()


@SANDBOX
def test_a_chain_that_pushes_asks_in_auto(workspace, chat_autonomy) -> None:
    execution = _Turn(workspace, "auto").command("git status && git push origin main")
    assert execution.status == "pending_approval"
    assert "git push" in execution.details["approval_request"]["expected_side_effects"]


@SANDBOX
def test_a_path_argument_in_any_chained_command_must_stay_in_the_workspace(workspace, chat_autonomy) -> None:
    execution = _Turn(workspace, "bypass_permissions").command("ls && cp notes.txt ../escaped.txt")
    assert execution.status == "blocked_by_policy", execution.response_text
    assert not (workspace.parent / "escaped.txt").exists()


# ------------------------------------------------------------------------------------------------
# The network: only for an install the operator approved, or in Bypass
# ------------------------------------------------------------------------------------------------


class _Registry:
    """A package index on 127.0.0.1 that records every request and has no packages."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        registry = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def do_GET(self) -> None:
                registry.paths.append(self.path)
                body = b"<html><body></body></html>"
                self.send_response(404 if "nothing-here" not in self.path else 200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def install(self) -> str:
        return (f"pip install --no-cache-dir --index-url http://127.0.0.1:{self.port}/simple "
                "--retries 0 --timeout 5 nothing-here")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def registry():
    if not shutil.which("pip") and not shutil.which("pip3"):
        pytest.skip("pip is not on PATH")
    served = _Registry()
    yield served
    served.close()


@SANDBOX
def test_auto_asks_before_an_install_and_it_reaches_the_registry_only_after_allow(workspace, chat_autonomy, registry) -> None:
    turn = _Turn(workspace, "auto")
    first = turn.command(registry.install())
    assert first.status == "pending_approval"
    assert registry.paths == []
    second = turn.command(registry.install(), approval_token=turn.approve(first))
    assert second.status in {"executed", "command_failed"}, second.response_text
    assert registry.paths, "the approved install never reached the registry"
    assert second.details.get("network_egress") == "granted: you approved this command"


@SANDBOX
def test_manual_approval_of_an_install_opens_the_network_for_that_install(workspace, registry) -> None:
    turn = _Turn(workspace, "manual")
    first = turn.command(registry.install())
    second = turn.command(registry.install(), approval_token=turn.approve(first))
    assert second.status in {"executed", "command_failed"}, second.response_text
    assert registry.paths


@SANDBOX
def test_bypass_reaches_the_registry_for_an_install(workspace, chat_autonomy, registry) -> None:
    execution = _Turn(workspace, "bypass_permissions").command(registry.install())
    assert execution.status in {"executed", "command_failed"}, execution.response_text
    assert registry.paths
    assert execution.details.get("network_egress") == "granted: Bypass permissions is on"


@SANDBOX
def test_a_command_that_is_not_an_install_stays_offline_even_in_bypass(workspace, chat_autonomy, registry) -> None:
    script = (
        "import urllib.request\n"
        f"urllib.request.urlopen('htt' + 'p://127.0.0.1:{registry.port}/leak', timeout=3)\n"
    )
    (workspace / "fetch.py").write_text(script, encoding="utf-8")
    execution = _Turn(workspace, "bypass_permissions").command("python3 fetch.py")
    assert execution.status in {"executed", "command_failed"}
    assert execution.details["returncode"] != 0
    assert registry.paths == [], "a non-install command reached the network"
    assert "network_egress" not in execution.details


@SANDBOX
def test_one_approved_install_does_not_open_the_network_for_another_command(workspace, chat_autonomy, registry) -> None:
    turn = _Turn(workspace, "auto")
    token = turn.approve(turn.command(registry.install()))
    other = registry.install().replace("nothing-here", "something-else")
    execution = turn.command(other, approval_token=token)
    assert execution.status == "pending_approval"
    assert registry.paths == []


def test_git_push_is_never_given_the_network() -> None:
    """A push is outward-facing and is not an install; no grant here may open the network for it."""
    assert PermissionAction.INSTALL_DEPENDENCIES not in command_actions("git push origin main")
    assert PermissionAction.USE_NETWORK not in command_actions("git push origin main")


def test_the_job_environment_accepts_only_git_identity_from_a_caller(tmp_path) -> None:
    from sandbox.job_runner import JobRunner
    from sandbox.resource_limits import ExecutionPolicy

    runner = JobRunner(ExecutionPolicy(workspace_root=tmp_path, writable_roots=(tmp_path,), max_seconds=10))
    if not shutil.which("bwrap") or subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True).returncode:
        pytest.skip("needs a usable bwrap backend")
    result = runner.run(
        ["env"], cwd=tmp_path,
        extra_env={"GIT_AUTHOR_NAME": "Ada", "LD_PRELOAD": "/tmp/x.so", "GITHUB_TOKEN": "t", "PATH": "/evil"},
    )
    assert "GIT_AUTHOR_NAME=Ada" in result.stdout
    assert "LD_PRELOAD" not in result.stdout and "GITHUB_TOKEN" not in result.stdout
    assert "PATH=/evil" not in result.stdout
