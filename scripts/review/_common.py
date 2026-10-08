"""Shared setup for the reviewer demos: an isolated VOOL home and the real tool executor.

Each demo runs entirely inside fresh temporary directories. It sets ``VOOL_HOME`` before any
runtime module is imported, so nothing is read from or written to an existing VOOL install,
and it never calls a model or the network.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEMO_HOME = Path(tempfile.mkdtemp(prefix="vool-review-home-"))
os.environ["VOOL_HOME"] = str(DEMO_HOME)
os.environ.setdefault("VOOL_KEY_STORAGE_MODE", "file")
os.environ.setdefault("VOOL_CREDENTIAL_STORE", "vault")


def new_workspace(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"vool-review-{prefix}-"))


def run_tool(intent: str, arguments: dict[str, Any], *, context: dict[str, Any], task_id: str):
    """Call one tool through the runtime's real executor (permission check included)."""
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id=task_id,
        session_id=str(context["runtime_session_id"]),
        source_context=context,
        hive_activity_tracker=HiveActivityTracker(
            config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)
        ),
    )


def show(label: str, execution: Any) -> None:
    details = execution.details or {}
    permission = details.get("permission") or {}
    print(f"  {label}: status={execution.status}")
    if permission:
        print(
            f"    permission: {permission.get('effect')} (mode={permission.get('mode')}, "
            f"actions={permission.get('actions')}) - {permission.get('reason')}"
        )
    elif details.get("operating_mode"):
        print(f"    mode={details.get('operating_mode')} executed={details.get('executed')}")


class Checks:
    """Collects PASS/FAIL lines; the demo exits non-zero if any check fails."""

    def __init__(self) -> None:
        self.failed = 0

    def check(self, condition: bool, what: str) -> None:
        print(f"  [{'PASS' if condition else 'FAIL'}] {what}")
        if not condition:
            self.failed += 1

    def finish(self, name: str) -> int:
        verdict = "PASS" if self.failed == 0 else f"FAIL ({self.failed} check(s) failed)"
        print(f"\n{name}: {verdict}")
        return 0 if self.failed == 0 else 1
