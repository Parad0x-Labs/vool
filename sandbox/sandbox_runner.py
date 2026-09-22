from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from pathlib import Path

from core.execution_gate import ExecutionGate
from core.liquefy_bridge import execution_payload_within_size_limit
from sandbox.job_runner import (
    HardLinkedWritableRootError,
    JobRunner,
    KernelIsolationUnavailableError,
)
from sandbox.network_guard import command_uses_network, parse_command
from sandbox.resource_limits import ExecutionPolicy


def _base_command_name(raw: str) -> str:
    name = Path(str(raw or "")).name.lower()
    if os.name == "nt" and name.endswith(".exe"):
        return name[:-4]
    return name


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

    ALLOWED_COMMANDS = [
        "dir", "ls", "pwd",
        "echo", "cat", "type", "head", "tail", "wc",
        "rg", "grep", "find", "sed",
        "npm", "npx", "yarn", "pnpm",
        "cargo", "python", "python3", "pytest",
        "ruff",
        "pip", "pip3",
        "git", "node", "nodejs", "env",
        "mkdir", "touch", "cp", "mv",
    ]

    #: Tools whose work is legitimately slow and chatty, so they get a longer clock and a
    #: bigger output cap. The name says what the set IS for now: it used to be read as
    #: "commands allowed to leave the sandbox", which is not something a name can decide.
    _PACKAGE_TOOL_COMMANDS = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "cargo"}

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

        # A HARD policy block must be reported before the APPROVABLE gate. Network egress being
        # disabled cannot be approved away, so surfacing "needs your approval" for a command that
        # would be refused regardless is misleading -- the user could approve it and still fail.
        # (This ordering started mattering once destructive-git classification became deny-by-default:
        # `git pull` is genuinely a mutation AND a network command, and the network refusal is the
        # one that actually governs.)
        if not self.job_runner.policy.allow_network_egress:
            with contextlib.suppress(Exception):
                if command_uses_network(parse_command(cmd)):
                    return {
                        "error": "Network egress is disabled by execution policy.",
                        "status": "blocked_by_policy",
                    }

        gate_result = self.gate.evaluate_command(cmd)

        if gate_result["decision"] == "blocked":
            return {"error": gate_result["reason"], "status": "blocked_by_policy"}

        if gate_result["decision"] == "advice_only":
            return {"error": gate_result["reason"], "status": "user_action_required"}

        if gate_result["decision"] == "simulate_only":
            return {"error": gate_result["reason"], "status": "simulate_only"}

        # Optional Simulator check could go here if decision == "simulate"

        # Step 2: Validate whitelist
        allowed = False
        argv = parse_command(cmd)
        if not argv:
            return {"error": "Empty command.", "status": "blocked_by_policy"}
        base_cmd = _base_command_name(str(argv[0] or ""))
        if base_cmd == "env":
            index = 1
            while index < len(argv):
                token = str(argv[index] or "")
                if "=" in token and not token.startswith("-"):
                    index += 1
                    continue
                base_cmd = _base_command_name(str(argv[index] or "")) if index < len(argv) else "env"
                break
        if base_cmd in self.ALLOWED_COMMANDS:
            allowed = True

        if not allowed:
            return {
                "error": f"Base command '{base_cmd}' not in Sandbox whitelist.",
                "status": "blocked_by_policy",
                "allowed": self.ALLOWED_COMMANDS,
            }

        # Step 3: Proceed with execution
        try:
            runner = self.job_runner
            if base_cmd in self._PACKAGE_TOOL_COMMANDS:
                # A package tool gets a LONGER CLOCK and a bigger output cap, and nothing else.
                #
                # It used to get `allow_network_egress=True`, which one layer down meant "run
                # with no confinement profile at all" — so `cargo build` (build.rs, proc
                # macros), `npm run <script>` and `pip uninstall` executed third-party code
                # against the operator's home with only a scan of their argv tokens in front of
                # them. Being a package manager is not a reason to trust a command with the
                # network, and trusting a command with the network was never a reason to stop
                # confining it. Egress now comes from POLICY (`ExecutionPolicy.allow_network_egress`,
                # set by whoever configures this runner) and never from the base command's name.
                runner = JobRunner(
                    ExecutionPolicy(
                        workspace_root=self._workspace_path,
                        writable_roots=(self._workspace_path,),
                        max_seconds=180,
                        max_output_kb=512,
                        allow_network_egress=self.job_runner.policy.allow_network_egress,
                        network_isolation_mode=self.job_runner.policy.network_isolation_mode,
                    )
                )
            print(f"  [SANDBOX RUN] Executing: {cmd}")
            result = runner.run(argv, cwd=target_cwd, cancel_event=cancel_event)
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
