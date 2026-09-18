from __future__ import annotations

import re
import shlex
import shutil
import sys
from typing import Any

from sandbox.network_guard import parse_command

from .artifacts import build_command_artifact, build_failure_artifact, truncate_text

_HOT_FAILURE_MARKERS = ("FAILED", "FAIL", "ERROR", "AssertionError", "Traceback", "Exception", "E   ")
#: Lines that point at a failure instead of stating it: a stack frame, a runtime's internal location, the raising
#: source line a runtime echoes above its caret, and a traceback header.
_FAILURE_POINTER_RE = re.compile(
    r'^(?:at\s+\S|File\s+"|Traceback \(most recent call last\):?$|node:[\w/.-]+:\d+(?::\d+)?$|(?:throw|raise)\s|\^+$)'
)


def is_failure_pointer_line(line: str) -> bool:
    """Whether an output line only points at a failure (where it happened) rather than stating it."""
    return bool(_FAILURE_POINTER_RE.search(" ".join(str(line or "").split())))


def failure_message_line(stdout: str, stderr: str) -> str:
    """The line of a failed command's output that states what failed; empty when it printed nothing.

    Measured on node 22: an assertion's first stderr line is `node:assert:150` and its first marked line is the
    echoed `throw new AssertionError(obj);`, while the message ("AssertionError [ERR_ASSERTION]: ... must be ...")
    sits below both; a Python traceback opens with its header. Lines that only point at the failure are set aside,
    then the first marked line wins, then the first remaining line, then the first line of any kind."""
    output = "\n".join(part for part in (str(stderr or "").strip(), str(stdout or "").strip()) if part)
    lines = [text for text in (" ".join(raw.split()).strip() for raw in output.splitlines()) if text]
    stating = [line for line in lines if not is_failure_pointer_line(line)]
    for line in stating:
        if any(marker in line for marker in _HOT_FAILURE_MARKERS):
            return line
    return (stating or lines or [""])[0]


def validation_command(intent: str, arguments: dict[str, Any]) -> str:
    override = str(arguments.get("command") or "").strip()
    if override:
        return override
    if intent == "workspace.run_tests":
        if shutil.which("pytest"):
            return "pytest -q"
        return "python3 -m pytest -q"
    if intent == "workspace.run_lint":
        if shutil.which("ruff"):
            return "ruff check ."
        return "python3 -m ruff check ."
    if intent == "workspace.run_formatter":
        apply = bool(arguments.get("apply", False))
        if shutil.which("ruff"):
            return "ruff format ." if apply else "ruff format --check ."
        return "python3 -m ruff format ." if apply else "python3 -m ruff format --check ."
    raise ValueError(f"Unsupported validation intent: {intent}")


def runtime_validation_command(command: str) -> str:
    argv = parse_command(command)
    if not argv:
        return str(command or "").strip()
    base = str(argv[0] or "").strip().lower()
    if base in {"pytest", "ruff"}:
        return shlex.join([sys.executable, "-m", base, *argv[1:]])
    if base in {"python", "python3"} and len(argv) >= 3 and argv[1] == "-m":
        module = str(argv[2] or "").strip().lower()
        if module in {"pytest", "ruff"}:
            return shlex.join([sys.executable, "-m", module, *argv[3:]])
    return str(command or "").strip()


def render_validation_result(
    intent: str,
    *,
    command: str,
    cwd: str,
    runner_result: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    status = str(runner_result.get("status") or "")
    stdout = truncate_text(str(runner_result.get("stdout") or ""), limit=2400)
    stderr = truncate_text(str(runner_result.get("stderr") or ""), limit=1600)
    returncode = int(runner_result.get("returncode", 0) or 0)
    success = bool(runner_result.get("success", False)) and status == "executed"
    command_artifact = build_command_artifact(
        command=command,
        cwd=cwd,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        status=status or "executed",
        artifact_type="validation_output",
    )
    artifacts = [command_artifact]
    failure_summary = ""
    if returncode != 0:
        failure_summary = failure_message_line(stdout, stderr)[:260]
        if failure_summary:
            artifacts.append(
                build_failure_artifact(
                    command=command,
                    cwd=cwd,
                    returncode=returncode,
                    stdout=stdout,
                    stderr=stderr,
                    summary=failure_summary,
                )
            )
    rendered = [f"{label} in `{cwd}`:", f"$ {command}", f"- Exit code: {returncode}"]
    if stdout:
        rendered.append(f"- Stdout:\n{stdout}")
    if stderr:
        rendered.append(f"- Stderr:\n{stderr}")
    return {
        "ok": success,
        "status": status or "executed",
        "response_text": "\n".join(rendered),
        "details": {
            "command": command,
            "cwd": cwd,
            "returncode": returncode,
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "artifacts": artifacts,
            "failure_summary": failure_summary,
        },
    }
