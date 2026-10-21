from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

from core.command_line import CommandLineRefused, parse_command_line
from core.execution_gate import ExecutionGate
from core.liquefy_bridge import execution_payload_within_size_limit
from sandbox.job_runner import (
    HardLinkedWritableRootError,
    JobRunner,
    KernelIsolationUnavailableError,
)
from sandbox.network_guard import command_uses_network, parse_command
from sandbox.resource_limits import ExecutionPolicy, path_args_within_roots, path_within_roots


def _base_command_name(raw: str) -> str:
    name = Path(str(raw or "")).name.lower()
    if os.name == "nt" and name.endswith(".exe"):
        return name[:-4]
    return name


_SHELL = "/bin/sh"


def _segment_base_command(argv: list[str]) -> str:
    """The program one command runs: its first word, or the word after `env NAME=value ...`."""
    base_cmd = _base_command_name(str(argv[0] or "")) if argv else ""
    if base_cmd == "env":
        index = 1
        while index < len(argv):
            token = str(argv[index] or "")
            if "=" in token and not token.startswith("-"):
                index += 1
                continue
            base_cmd = _base_command_name(str(argv[index] or "")) if index < len(argv) else "env"
            break
    return base_cmd


def _network_grant_reason(cmd: str) -> str:
    """Why this command may reach the network, or "" when it may not.

    Only when the permission decision behind this dispatch covers the exact command (the same check
    the gate makes), the command itself installs a dependency or uses the network, and the decision
    is the operator's own approval for it -- or the turn is in Bypass. Anything unreadable is "".
    """
    try:
        from core.effect_gateway import consume_turn_policy
        from core.mode_permission_policy import OperatingMode, PermissionAction, command_actions

        policy = consume_turn_policy("sandbox_runner.network_grant")
        authorization = ExecutionGate._covering_authorization(cmd, policy_mode=policy.mode)
        if authorization is None:
            return ""
        # The network is for installs only, and only when EVERY command in the line installs:
        # `pip install x && python run.py` would otherwise give run.py the network too.
        from core.command_line import parse_command_line

        chain = parse_command_line(cmd)
        segments = list(chain.segment_texts()) if chain is not None else [cmd]
        if not all(PermissionAction.INSTALL_DEPENDENCIES in command_actions(segment) for segment in segments):
            return ""
        if authorization.operator_granted:
            return "you approved this command"
        if authorization.mode is OperatingMode.BYPASS_PERMISSIONS:
            return "Bypass permissions is on"
    except Exception:
        return ""
    return ""


def _git_identity_env() -> dict[str, str]:
    """The operator's own git name and email, for a git command in the sandbox.

    The sandbox points HOME at a private folder, so git cannot see ~/.gitconfig and every commit
    failed with "Author identity unknown". Only these two values cross; nothing else of the
    operator's git configuration (credentials helpers, hooks, aliases) does.
    """
    values: dict[str, str] = {}
    for key, names in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")),
                       ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        try:
            found = subprocess.run(["git", "config", "--global", "--get", key], capture_output=True,
                                   text=True, timeout=5, check=False).stdout.strip()
        except Exception:
            found = ""
        if found and "\n" not in found:
            for name in names:
                values[name] = found
    return values


def _network_isolation_mode_from_env() -> str:
    raw = str(os.environ.get("VOOL_SANDBOX_NETWORK_MODE") or "").strip().lower()
    if raw in {"os_enforced", "heuristic_only"}:
        return raw
    unsafe = str(os.environ.get("VOOL_SANDBOX_UNSAFE_EXPLICIT") or "").strip().lower()
    if unsafe in {"1", "true", "yes", "on"}:
        return "heuristic_only"
    return "auto"


class SandboxRunner:
    """
    Acts as the single point of OS-level command execution.
    Requires an explicit allowance from the local ExecutionGate prior to launching any process.
    """

    #: Never add a shell (`sh`, `bash`, `zsh`), `xargs`, `eval`, `nohup`, `sudo` or `docker`: each
    #: runs an arbitrary command line of its own and walks around this list and the per-command
    #: checks a chained line gets.
    ALLOWED_COMMANDS = [
        "dir", "ls", "pwd",
        "echo", "cat", "type", "head", "tail", "wc",
        "rg", "grep", "find", "sed",
        "diff", "jq", "sort", "uniq",
        "npm", "npx", "yarn", "pnpm", "bun", "deno",
        "cargo", "python", "python3", "pytest",
        "ruff", "black", "mypy",
        "tsc", "eslint", "prettier",
        "pip", "pip3", "uv",
        "make", "go", "gofmt",
        "git", "node", "nodejs", "env",
        "mkdir", "touch", "cp", "mv", "rm", "rmdir",
        # The shell's own no-ops, so `false && ...` and `... || true` mean what they say.
        "true", "false",
    ]

    #: Tools whose work is legitimately slow and chatty, so they get a longer clock and a
    #: bigger output cap. The name says what the set IS for now: it used to be read as
    #: "commands allowed to leave the sandbox", which is not something a name can decide.
    _PACKAGE_TOOL_COMMANDS = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "cargo", "uv", "go", "bun", "deno"}

    #: Retained under the old name so nothing importing it breaks. Same members, and no
    #: longer any grant attached to membership.
    _PACKAGE_INSTALL_COMMANDS = _PACKAGE_TOOL_COMMANDS

    def __init__(self, gate: ExecutionGate, workspace_path: str, *, network_isolation_mode: str | None = None, read_roots: tuple[Path, ...] = ()):
        self.gate = gate
        self.workspace = workspace_path
        self._workspace_path = Path(workspace_path).resolve()
        from sandbox.resource_limits import default_read_roots

        self.job_runner = JobRunner(
            ExecutionPolicy(
                workspace_root=self._workspace_path,
                writable_roots=(self._workspace_path,),
                read_roots=(*default_read_roots(), *read_roots),
                max_seconds=120,
                max_output_kb=256,
                allow_network_egress=False,
                network_isolation_mode=network_isolation_mode or _network_isolation_mode_from_env(),
            )
        )

    def run_command(
        self, cmd: str, *, cwd: str | None = None, cancel_event: threading.Event | None = None
    ) -> dict:
        """
        Takes an arbitrary shell command, asks the Execution Gate, and runs it if allowed.
        """
        # Step 1: bound the payload. This is a SIZE CAP, not a policy decision — the real
        # pre-execution gate is `self.gate.evaluate_command(cmd)` below. It used to be called
        # `apply_local_execution_safety` and reported "safety guard blocked execution", which read
        # as a security refusal for what is a 5 MiB limit.
        target_cwd = cwd or self.workspace
        if not execution_payload_within_size_limit(
            sandbox_context={"workspace": target_cwd}, payload={"cmd": cmd}
        ):
            return {
                "error": "command payload exceeded the local size cap",
                "status": "blocked_by_policy",
            }

        # A chained line is split by the one grammar the gate also uses, and each command in it
        # is held to every check below on its own. The shell that finally runs it only ever sees
        # the rebuilt, quoted script (core.command_line).
        try:
            chain = parse_command_line(cmd)
        except CommandLineRefused as exc:
            return {"error": str(exc), "status": "blocked_by_policy"}
        if chain is not None and os.name == "nt":
            return {"error": "Chained commands are not supported on Windows; run them one at a time.",
                    "status": "blocked_by_policy"}
        try:
            segments = [list(segment.argv) for segment in chain.segments] if chain is not None else [parse_command(cmd)]
        except ValueError:
            segments = [[]]

        # The network opens only for a command the operator approved themselves (or in Bypass),
        # and only when that command installs a dependency or uses the network by its own
        # classification. Auto's standing allowance never opens it.
        egress_reason = "" if self.job_runner.policy.allow_network_egress else _network_grant_reason(cmd)
        allow_egress = self.job_runner.policy.allow_network_egress or bool(egress_reason)

        # A HARD policy block must be reported before the APPROVABLE gate. Network egress being
        # disabled cannot be approved away, so surfacing "needs your approval" for a command that
        # would be refused regardless is misleading -- the user could approve it and still fail.
        # (This ordering started mattering once destructive-git classification became deny-by-default:
        # `git pull` is genuinely a mutation AND a network command, and the network refusal is the
        # one that actually governs.)
        if not allow_egress:
            with contextlib.suppress(Exception):
                if any(command_uses_network(segment) for segment in segments if segment):
                    return {
                        "error": "Network egress is disabled by execution policy.",
                        "status": "blocked_by_policy",
                    }

        gate_result = self.gate.evaluate_command(cmd, cwd=str(target_cwd))

        if gate_result["decision"] == "blocked":
            return {"error": gate_result["reason"], "status": "blocked_by_policy"}

        if gate_result["decision"] == "advice_only":
            return {"error": gate_result["reason"], "status": "user_action_required"}

        if gate_result["decision"] == "simulate_only":
            return {"error": gate_result["reason"], "status": "simulate_only"}

        # Optional Simulator check could go here if decision == "simulate"

        # Step 2: Validate whitelist, every command of a chained line on its own
        if not segments or not all(segments):
            return {"error": "Empty command.", "status": "blocked_by_policy"}
        bases = [_segment_base_command(segment) for segment in segments]
        for base in bases:
            if base not in self.ALLOWED_COMMANDS:
                return {
                    "error": f"Base command '{base}' not in Sandbox whitelist.",
                    "status": "blocked_by_policy",
                    "allowed": self.ALLOWED_COMMANDS,
                }
        base_cmd = bases[0]

        argv = segments[0]
        if chain is not None:
            # The script's own leading `cd` moves the shell; the job starts where any other does.
            segment_cwd = Path(target_cwd).resolve()
            if chain.cd is not None:
                segment_cwd = (segment_cwd / chain.cd).resolve()
            roots = (self._workspace_path,)
            if not path_within_roots(segment_cwd, roots):
                return {"error": f"`cd {chain.cd}` leaves the workspace, so the line was not run.",
                        "status": "blocked_by_policy"}
            for segment in segments:
                # The job runner checks the argv it is handed, which for a chain is the shell's;
                # each command's own path arguments are checked here, from where it runs.
                escaping = path_args_within_roots(segment, roots, cwd=segment_cwd)
                if escaping is not None:
                    return {
                        "error": f"Sandbox execution blocked: Path argument '{escaping}' escapes allowed workspace roots.",
                        "status": "blocked_by_policy",
                    }
            argv = [_SHELL, "-c", chain.shell_script()]

        # Step 3: Proceed with execution
        try:
            runner = self.job_runner
            program_reads = tuple(
                Path(found).resolve() for found in (shutil.which(segment[0]) for segment in segments) if found
            ) if chain is not None else ()
            package_tool = any(base in self._PACKAGE_TOOL_COMMANDS for base in bases)
            if package_tool or allow_egress != self.job_runner.policy.allow_network_egress or program_reads:
                # A package tool gets a LONGER CLOCK and a bigger output cap, and nothing else.
                #
                # It used to get `allow_network_egress=True`, which one layer down meant "run
                # with no confinement profile at all" — so `cargo build` (build.rs, proc
                # macros), `npm run <script>` and `pip uninstall` executed third-party code
                # against the operator's home with only a scan of their argv tokens in front of
                # them. Being a package manager is not a reason to trust a command with the
                # network, and trusting a command with the network was never a reason to stop
                # confining it. Egress comes from POLICY or from the operator's own approval of
                # this exact install (`_network_grant_reason`), never from the base command's
                # name, and an egress job keeps the same filesystem confinement.
                base_policy = self.job_runner.policy
                runner = JobRunner(
                    ExecutionPolicy(
                        workspace_root=self._workspace_path,
                        writable_roots=(self._workspace_path,),
                        read_roots=(*tuple(base_policy.read_roots or ()), *program_reads),
                        max_seconds=180 if package_tool else base_policy.max_seconds,
                        max_output_kb=512 if package_tool else base_policy.max_output_kb,
                        allow_network_egress=allow_egress,
                        network_isolation_mode=base_policy.network_isolation_mode,
                    )
                )
            extra_env = _git_identity_env() if "git" in bases else {}
            print(f"  [SANDBOX RUN] Executing: {cmd}")
            result = runner.run(argv, cwd=target_cwd, cancel_event=cancel_event, extra_env=extra_env)
            # FIVE distinct terminal outcomes, never collapsed into "it failed". A job the
            # operator cancelled, a job that ran out of clock and a job whose command genuinely
            # exited non-zero are three different things to answer for, and the first two used
            # to arrive as `status: "executed", success: false` — indistinguishable from the
            # third — because the runner stopped raising TimeoutExpired once it took over the
            # teardown itself.
            if result.returncode == 124:
                status = "timed_out"
            elif result.returncode == JobRunner.CANCELLED_RETURNCODE:
                status = "cancelled"
            else:
                status = "executed"
            return {
                "cmd": cmd,
                "cwd": str(target_cwd),
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "success": (result.returncode == 0),
                "status": status,
                "base_command": base_cmd,
                **({"network_egress": f"granted: {egress_reason}"} if egress_reason else {}),
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after 120s: {cmd}", "status": "timed_out"}
        except HardLinkedWritableRootError as e:
            # Its own status: nothing the operator typed is wrong, and the command did not
            # fail. A file in the writable root has a second name, so confinement could not be
            # honestly claimed for this run.
            return {
                "error": f"Sandbox refused: {e!s}",
                "status": "blocked_by_hard_link",
            }
        except KernelIsolationUnavailableError as e:
            # Deliberately ahead of the ValueError arm below (it is a subclass) and deliberately its
            # own status. "this host cannot isolate" is not "policy refused your command", and it is
            # certainly not "your command failed" -- which is what the operator used to be told, for
            # every command, on any host where the namespace syscall is denied.
            return {"error": f"Sandbox isolation unavailable: {e!s}", "status": "sandbox_unavailable"}
        except ValueError as e:
            return {"error": f"Sandbox execution blocked: {e!s}", "status": "blocked_by_policy"}
        except Exception as e:
            return {"error": f"Sandbox execution failure: {e!s}"}
