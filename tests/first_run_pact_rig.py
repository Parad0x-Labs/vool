"""Shared rig for the First-Run Pact test family.

Isolated home per test, real runtime services, the REAL served dispatch seam
(``core.web.api.service.dispatch_get/dispatch_post``) — nothing on the path under
test is mocked. Modeled on ``tests/operator_profile_rig.py``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

CANONICAL_SESSION = "openclaw:" + "0a1b2c3d4e5f6a7b8c9d"  # exactly the page-minted shape (20 hex)


class PactRig:
    """One isolated home + real runtime + the real served dispatchers."""

    def __init__(self, home: Path, runtime: RuntimeServices, workspace: Path):
        self.home = home
        self.runtime = runtime
        self.workspace = workspace

    # --- served surface ----------------------------------------------------

    def get(self, path: str, *, query: dict | None = None):
        from urllib.parse import parse_qsl, urlsplit

        parts = urlsplit(path)
        parsed_query: dict[str, list[str]] = {k: [v] for k, v in parse_qsl(parts.query)}
        for key, value in (query or {}).items():
            parsed_query[key] = value if isinstance(value, list) else [value]
        response = dispatch_get(
            path=parts.path, query=parsed_query, runtime=self.runtime, model_name="vool",
            client_host="127.0.0.1",
        )
        return response.status, json.loads(response.body)

    def post(self, path: str, body: dict[str, Any] | None = None, *, headers: dict | None = None, client_host: str = "127.0.0.1"):
        response = dispatch_post(
            path=path, body=body or {}, headers=headers or {"Content-Type": "application/json"},
            runtime=self.runtime, model_name="vool",
            workspace_root_provider=lambda: self.workspace, client_host=client_host,
        )
        if response.stream is not None:
            raw = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode() for chunk in response.stream)
            frames = [json.loads(line) for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
            return response.status, frames
        return response.status, json.loads(response.body)

    # --- pact conveniences --------------------------------------------------

    def pact(self) -> dict[str, Any]:
        status, payload = self.get("/api/onboarding/pact")
        assert status == 200, (status, payload)
        return payload

    def walk_to(self, state: str) -> dict[str, Any]:
        """Drive the pure steps: begin → naming → … → provider_choice → Local Only → local_task."""
        if state not in {"welcome", "naming", "facts", "boundaries", "provider_choice", "local_task"}:
            raise ValueError(f"walk_to cannot drive evidence-locked state {state!r}")
        snap = self.pact()
        if snap["state"] == "absent":
            status, payload = self.post("/api/onboarding/pact/begin", {})
            assert status == 200, payload
            snap = self.pact()
        if snap["state"] == "welcome" and state != "welcome":
            status, payload = self.post("/api/onboarding/pact/advance", {"to": "naming", "expect_revision": snap["revision"]})
            assert status == 200, payload
            snap = self.pact()
        for step in ("naming", "facts", "boundaries", "provider_choice"):
            if state == step:
                return snap
            if snap["state"] == step:
                following = self._next(step)
                if following == "local_task":
                    break  # the Local Only choice happens between provider_choice and local_task
                status, payload = self.post("/api/onboarding/pact/advance", {"to": following, "expect_revision": snap["revision"]})
                assert status == 200, (step, payload)
                snap = self.pact()
                if state == following:
                    return snap
        if state == "local_task" and snap["state"] == "provider_choice":
            status, payload = self.post("/api/onboarding/pact/choice/local-only", {})
            assert status == 200, payload
            snap = self.pact()
            status, payload = self.post("/api/onboarding/pact/advance", {"to": "local_task", "expect_revision": snap["revision"]})
            assert status == 200, (state, payload)
            snap = self.pact()
        return snap

    @staticmethod
    def _next(step: str) -> str:
        order = {"welcome": "naming", "naming": "facts", "facts": "boundaries", "boundaries": "provider_choice", "provider_choice": "local_task"}
        return order[step]

    # --- real chat turn (deterministic workspace write; zero model calls) ----

    def run_task_turn(self, prompt: str, *, turn_id: str = "pact-rig-task", approve: bool = True):
        """A REAL /api/chat turn. Returns (request_id, status_frames, commit)."""
        status, frames = self.post("/api/chat", {
            "model": "vool-local-only", "stream": True, "stream_task_events": True,
            "session_id": CANONICAL_SESSION, "turn_id": turn_id,
            "messages": [{"role": "user", "content": prompt}],
        })
        commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
        approval_id = self._pending_approval_id(prompt)
        if commit and (commit.get("status") == "answer_present") and approval_id and approve:
            approve_status, _ = self.post("/api/mode", {
                "op": "resolve_approval", "approval_id": approval_id, "decision": "allow",
                "session_id": CANONICAL_SESSION,
            })
            assert approve_status == 200
            status, frames = self.post("/api/chat", {
                "model": "vool-local-only", "stream": True, "stream_task_events": True,
                "session_id": CANONICAL_SESSION, "turn_id": turn_id, "approval_token": approval_id,
                "messages": [{"role": "user", "content": prompt}],
            })
            commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
        return (commit or {}).get("request_id", ""), frames, commit

    def _pending_approval_id(self, prompt: str) -> str | None:
        path = self.home / "data" / "pending_approvals.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        for token, entry in data.items():
            if isinstance(entry, dict) and entry.get("status", "pending") == "pending":
                return token
        return None if not data else next(iter(data), None)

    def canonical_session(self) -> str:
        return CANONICAL_SESSION


@pytest.fixture
def pact_rig(tmp_path, monkeypatch):
    """Isolated home + real bootstrapped runtime; cleans up behind itself."""
    home = tmp_path / "pact-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "file")
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    monkeypatch.setenv("VOOL_DISABLE_MESH_DAEMON", "1")
    monkeypatch.setenv("VOOL_SKIP_PROVIDER_PREWARM", "1")
    monkeypatch.setenv("VOOL_PUBLIC_HIVE_ENABLED", "0")

    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)

    import storage.db as sdb

    db_path = home / "data" / "vool_web0_v2.db"
    sdb.configure_default_db_path(db_path)

    # Reset the seams prior runtimes leave behind (the operator_profile_rig discipline):
    # a lazy singleton holding last test's DB or table flag must never serve this test.
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    import core.task_state_machine as _task_state
    import core.trace_id as _trace_id

    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False
        _module._init_table()
    try:
        from core import operator_profile as _op

        _op.reset_table_cache_for_tests()
    except Exception:
        pass
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", None)
    try:
        from core.model_health import reset_provider_health

        reset_provider_health()
    except Exception:
        pass

    from apps.vool_api_server import _bootstrap

    runtime = _bootstrap(run_prewarm=False)

    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    rig = PactRig(home, runtime, workspace)
    yield rig

    # Cleanup proof: the rig releases its own overrides; the home dies with tmp_path.
    try:
        from core.semantic.semantic_admissions import clear_execution_context

        clear_execution_context()
    except Exception:
        pass
    try:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
    except Exception:
        pass
    try:
        sdb.configure_default_db_path(None)
    except Exception:
        pass
    try:
        configure_runtime_home(None)
    except Exception:
        pass
    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False
