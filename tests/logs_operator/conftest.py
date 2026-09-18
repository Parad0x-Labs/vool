"""Fixtures for the logs operator-utility pack (C11).

The root tests/conftest pins ``VOOL_LIQUEFY_LOGS=0`` so the projection can
never touch real data; every fixture here opts back in with an explicit
per-test home, and gives each test its own authoritative Blackbox journal whose
projection sink is deliberately DISCONNECTED — so ``logs.rebuild`` always has
real work to do over a real journal.

Rich synthetic fixtures carry the four operator questions as data: what
happened (effect_intended/terminal pairs), why it failed (outcome=failed with
error payloads), which provider/tool ran (tool/provider fields), and what fault
occurred (coverage_gap). All timestamps are pinned ISO-8601 UTC so the typed
time filters assert on exact windows.
"""
from __future__ import annotations

import json

import pytest

from core.liquefy.store import LiquefyLogStore

SECRET_TOKEN = "AKIAIOSFODNN7EXAMPLE"  # canonical AWS access-key test shape, redacted at ingest


def _ts(minute: int) -> str:
    return f"2026-09-03T10:{minute:02d}:00+00:00"


def journal_entries() -> list[dict]:
    """Eight journal entries answering the four operator questions exactly."""
    return [
        {
            "schema": "blackbox_effect_v1", "kind": "effect_intended",
            "effect_id": "eff-write-01", "turn_id": "turn-alpha", "session_id": "sess-ops",
            "trace_id": "trace-a", "ts": _ts(1),
            "root": "/tmp/ws", "path": "reports/week.md", "operation": "create",
            "intent": "workspace.write_file", "tool": "workspace__write_file",
            "provider": "qwen-local", "intended": {"after_sha256": "ab" * 32},
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_terminal",
            "effect_id": "eff-write-01", "turn_id": "turn-alpha", "session_id": "sess-ops",
            "trace_id": "trace-a", "ts": _ts(2),
            "root": "/tmp/ws", "path": "reports/week.md", "operation": "create",
            "outcome": "succeeded", "status": "ok",
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_intended",
            "effect_id": "eff-push-02", "turn_id": "turn-beta", "session_id": "sess-ops",
            "trace_id": "trace-b", "ts": _ts(3),
            "root": "/tmp/ws", "path": "reports/week.md", "operation": "push",
            "intent": "repo.push", "tool": "repo__push",
            "provider": "cloud-anthropic", "intended": {"after_sha256": "cd" * 32},
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_terminal",
            "effect_id": "eff-push-02", "turn_id": "turn-beta", "session_id": "sess-ops",
            "trace_id": "trace-b", "ts": _ts(4),
            "root": "/tmp/ws", "path": "reports/week.md", "operation": "push",
            "outcome": "failed", "status": "remote_rejected",
            "error": "pre-receive hook declined: protected branch",
        },
        {
            "schema": "blackbox_effect_v1", "kind": "coverage_gap",
            "effect_id": "gap-shell-03", "turn_id": "turn-beta", "session_id": "sess-ops",
            "trace_id": "trace-b", "ts": _ts(5),
            "paths": ["/tmp/ws/scripts/deploy.sh"], "handler": "sandbox.run_command",
            "error": "handler reported paths the recorder had not pre-snapshotted",
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_intended",
            "effect_id": "eff-skip-04", "turn_id": "turn-gamma", "session_id": "sess-other",
            "trace_id": "trace-c", "ts": _ts(6),
            "root": "/tmp/other", "path": "notes.txt", "operation": "create",
            "intent": "workspace.write_file", "tool": "workspace__write_file",
            "provider": "qwen-local", "intended": {"after_sha256": "ef" * 32},
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_terminal",
            "effect_id": "eff-skip-04", "turn_id": "turn-gamma", "session_id": "sess-other",
            "trace_id": "trace-c", "ts": _ts(7),
            "root": "/tmp/other", "path": "notes.txt", "operation": "create",
            "outcome": "refused", "status": "permission_denied",
            "error": "mode manual refuses writes outside the workspace root",
        },
        {
            "schema": "blackbox_effect_v1", "kind": "effect_intended",
            "effect_id": "eff-secret-05", "turn_id": "turn-delta", "session_id": "sess-ops",
            "trace_id": "trace-d", "ts": _ts(8),
            "root": "/tmp/ws", "path": "deploy/env.list", "operation": "create",
            "intent": "workspace.write_file", "tool": "workspace__write_file",
            "provider": "qwen-local",
            "payload_note": f"deployment used {SECRET_TOKEN} during bootstrap",
        },
    ]


@pytest.fixture(autouse=True)
def _lane_enabled(monkeypatch, tmp_path):
    """Opt this lane into the projection under a per-test home (overrides the
    tests/conftest pin for the duration of each test here)."""
    home = tmp_path / "liquefy-logs"
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "1")
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS_HOME", str(home))
    from core.liquefy import hooks as liquefy_hooks

    liquefy_hooks.reset_default_store()
    yield home
    liquefy_hooks.reset_default_store()


@pytest.fixture()
def blackbox(tmp_path, monkeypatch):
    """An authoritative journal, sink DISCONNECTED from the projection."""
    root = tmp_path / "blackbox-authority"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(root))
    from core.blackbox.store import BlackboxStore, reset_default_store

    reset_default_store()
    store = BlackboxStore(root)
    yield store
    reset_default_store()


@pytest.fixture()
def seeded(blackbox):
    """Journal entries appended to the authority; returns the full entries."""
    appended = [blackbox.append(entry) for entry in journal_entries()]
    return {"store": blackbox, "entries": appended}


@pytest.fixture()
def projection(tmp_path):
    """A dedicated projection store root for direct-store scenarios."""
    root = tmp_path / "projection"
    return LiquefyLogStore(root)


def write_events(store: LiquefyLogStore, entries: list[dict]) -> list[int]:
    """Route journal-shaped entries through the projection ingest mapping."""
    from core.liquefy.hooks import blackbox_entry_to_event

    return store.append([blackbox_entry_to_event(entry) for entry in entries])


def read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
