"""Served: a Manual-mode write's approval reaches the page BEFORE the turn completes.

Measured 2026-09-06 on the delivered build with a scratch daemon: the machine write fast path
streamed `task.completed` and only then `permission.required`. The chat page ignores every
non-verification event that reaches an ended run, so the approval bar never painted and the
operator read the approval prompt as a refusal. The typed order is the contract this test pins:
the actionable approval (with its id) precedes the turn's first `task.completed`.

No approval is granted; the directory is never created (asserted).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import tests._reader_served_rig as rig

TARGET = "vool_probe_never_created_" + uuid.uuid4().hex[:6]


def _typed_events(daemon, text: str, mode: str) -> list[dict]:
    session = rig.canonical_session("approval-order-" + uuid.uuid4().hex[:8])
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": uuid.uuid4().hex, "model": "reader-drive:stub",
            "model_selection": "sticky", "mode": mode}
    req = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    typed = []
    with urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("vool_event"):
                typed.append(obj["vool_event"])
    return typed


def test_manual_mode_folder_request_streams_an_actionable_approval_before_completion(tmp_path):
    with rig.CapturingProvider(default="Understood.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        daemon.register_provider()
        daemon.start()
        try:
            typed = _typed_events(daemon, f"can you create a folder on the desktop for me? Name it {TARGET}", "manual")
        finally:
            daemon.stop()
    assert not (Path.home() / "Desktop" / TARGET).exists(), "nothing was approved, nothing may be created"
    types = [ev.get("type") for ev in typed]
    assert "permission.required" in types, types
    first_completed = types.index("task.completed")
    first_permission = types.index("permission.required")
    assert first_permission < first_completed, (
        f"the approval must precede the turn's completion, or the page (which ignores events after a run "
        f"ends) never paints it: {types}"
    )
    approval = typed[first_permission].get("approval") or {}
    assert approval.get("approval_id"), "the approval is a receipt without an id and cannot be answered"
    assert approval.get("intent") == "machine.ensure_directory", approval
