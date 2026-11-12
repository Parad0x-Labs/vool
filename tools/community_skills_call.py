"""`vool-community call`: run one VOOL tool in a throwaway profile, through the permission check.

A skill author who names a tool in `allowed-tools` wants to see what that tool answers, and
what a person would be asked to approve, before shipping the skill. Two calls look like the
way in and are not:

* `core.runtime_execution_tools.execute_runtime_tool` is the inner door for one group of
  tools. For a tool another lane owns (`web.search`, every `operator.*` tool) it returns
  None, so `result.status` raises AttributeError in the author's script.
* `core.execution.operator_tools.execute_operator_tool` and other lane functions sit BEHIND
  the Manual/Auto approval check, so a call there runs the action with no approval at all.

The call a real turn makes is `core.tool_intent_executor.execute_tool_intent`. This command
makes exactly that call, in a profile it creates and deletes, and always prints one plain
JSON result: never None, never a traceback.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["call_tool", "run_call"]

_ISOLATED_ENV = ("HOME", "VOOL_HOME", "VOOL_PLUGINS_DIR", "VOOL_BUNDLED_PLUGINS_DIR")
MODES = ("manual", "auto")


def call_tool(intent: str, arguments: dict[str, Any], *, mode: str = "manual",
              approve: bool = False, workspace: str = "") -> dict[str, Any]:
    """One tool call through the permission check, in the CURRENT profile; a plain dict.

    Run it only inside a throwaway profile (`run_call` makes one). With `approve`, an
    approval request is allowed once and the same call is made again, the way a person
    clicking **Allow once** would.
    """

    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    session = "skill-author-call"
    context = {"runtime_session_id": session, "operating_mode": mode, "cancel_turn_id": "author-turn"}
    if workspace:
        # The chat's project folder: workspace.* tools read and write only inside it.
        context.update(workspace_root=workspace, workspace=workspace)

    def once():
        return execute_tool_intent({"intent": intent, "arguments": dict(arguments)},
                                   task_id="author-task", session_id=session, source_context=context,
                                   hive_activity_tracker=tracker)

    result = once()
    asked = (result.details or {}).get("approval_request") if result is not None else None
    approved = False
    if approve and isinstance(asked, dict) and asked.get("approval_id"):
        from core.mode_permission_policy import resolve_approval

        approved = resolve_approval(str(asked["approval_id"]), decision="allow") is not None
        if approved:
            result = once()
    if result is None:  # defensive: the executor answers every intent today
        return {"tool": intent, "ok": False, "status": "no_result", "approval_needed": False,
                "reply": "VOOL returned no result for this tool."}
    return {
        "tool": intent,
        "mode": mode,
        "ok": bool(result.ok),
        "status": str(result.status or ""),
        "outcome": str(result.mode or ""),
        "approval_needed": isinstance(asked, dict) and not approved,
        "approved_once": approved,
        "reply": str(result.response_text or ""),
    }


#: The folders machine.* tools may use; the throwaway home has each of them, empty.
HOME_FOLDERS = ("Desktop", "Downloads", "Documents")


def parse_seed(spec: str) -> tuple[Path, str]:
    """`SRC:DEST` -> (source file, destination). DEST is `~/...` (the throwaway home) or a
    relative path (the throwaway project folder). Raises ValueError with words an author can act on."""

    source, sep, dest = str(spec or "").rpartition(":")
    if not sep or not source or not dest:
        raise ValueError(f"--seed-file {spec!r} must be SRC:DEST, like ./invoice.pdf:~/Downloads/invoice.pdf")
    path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError(f"--seed-file source {source!r} is not a file")
    parts = Path(dest.removeprefix("~/")).parts
    if Path(dest).is_absolute() or ".." in parts or not parts or (dest.startswith("~") and not dest.startswith("~/")):
        raise ValueError(f"--seed-file destination {dest!r} must be ~/<path> or a relative path inside the project folder")
    return path, dest


def _place_seeds(seeds: list[tuple[Path, str]], *, home: Path, workspace: Path) -> None:
    for source, dest in seeds:
        target = home / dest[2:] if dest.startswith("~/") else workspace / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def run_call(intent: str, arguments_json: str, *, mode: str = "manual", approve: bool = False,
             seed_files: tuple[str, ...] = ()) -> int:
    """The `call` command: a fresh profile, one call, one JSON line; the exit code.

    The throwaway home has empty ~/Desktop, ~/Downloads and ~/Documents folders. Each
    `seed_files` entry `SRC:DEST` copies a file of the author's into it first, so a read of
    `~/Downloads/invoice.pdf` has something to read.
    """

    from core.registry.codec import canonical

    def say(payload: dict[str, Any]) -> None:
        print(canonical(payload).decode("ascii"))

    if mode not in MODES:
        say({"status": "refused", "reason": "--mode must be manual or auto"})
        return 1
    try:
        arguments = json.loads(arguments_json or "{}")
    except ValueError as exc:
        say({"status": "refused", "reason": f"--args is not JSON: {exc}"})
        return 1
    if not isinstance(arguments, dict):
        say({"status": "refused", "reason": "--args must be a JSON object, like '{\"query\": \"tea\"}'"})
        return 1
    try:
        seeds = [parse_seed(spec) for spec in seed_files]
    except ValueError as exc:
        say({"status": "refused", "reason": str(exc)})
        return 1
    from core.runtime_tool_contracts import runtime_tool_contract_map

    if str(intent) not in runtime_tool_contract_map():
        say({"status": "refused", "reason": f"VOOL has no tool named {intent!r}; see "
             "docs/guides/skill-tools-and-limits.md for the names a skill may use"})
        return 1

    saved = {name: os.environ.get(name) for name in _ISOLATED_ENV}
    root = Path(tempfile.mkdtemp(prefix="community-call-"))
    try:
        home = root / "home"
        (root / "profile" / "plugins").mkdir(parents=True)
        (root / "bundled").mkdir()
        home.mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        for name in HOME_FOLDERS:
            (home / name).mkdir()
        _place_seeds(seeds, home=home, workspace=workspace)
        os.environ.update({"HOME": str(home), "VOOL_HOME": str(home / "vool"),
                           "VOOL_PLUGINS_DIR": str(root / "profile"),
                           "VOOL_BUNDLED_PLUGINS_DIR": str(root / "bundled")})
        from core import runtime_paths
        from storage.migrations import run_migrations

        runtime_paths.configure_runtime_home(home / "vool")
        here = os.getcwd()
        os.chdir(workspace)  # a relative path never lands in the author's own folder
        try:
            run_migrations()
            report = call_tool(str(intent), arguments, mode=mode, approve=approve, workspace=str(workspace))
        finally:
            os.chdir(here)
            runtime_paths.configure_runtime_home(None)
    except Exception as exc:  # an author gets words, never a traceback
        detail = " ".join(str(exc).split())[:300]
        say({"tool": str(intent), "ok": False, "status": "error",
             "reply": f"the call could not finish: {detail or type(exc).__name__}"})
        return 1
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(root, ignore_errors=True)
    say(report)
    return 0
