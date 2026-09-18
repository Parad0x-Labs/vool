"""Elliptical machine-read follow-ups must stay grounded in real tools, never fabricate.

Live defects this covers:
  - "ok what about D?" after a C: disk read fabricated a D: figure instead of re-running the
    disk tool scoped to D:.
  - "the biggest single file in that drive" scanned C: (default) instead of the drive just
    discussed, and returned folders instead of the largest file.
"""
from __future__ import annotations

import types
from unittest import mock

import pytest

import core.agent_runtime.fast_paths_machine as fp
import core.machine_diagnostics as md
from core.execution.constants import (
    elliptical_drive_followup,
    largest_kind,
    resolve_drive_scope,
)
from core.runtime_execution_tools import execute_runtime_tool


# --------------------------------------------------------------------------- pure resolvers
def test_elliptical_drive_followup_matches_bare_drive_only() -> None:
    assert elliptical_drive_followup("ok what about D?") == "D:\\"
    assert elliptical_drive_followup("and D:") == "D:\\"
    assert elliptical_drive_followup("how about the E drive") == "E:\\"
    assert elliptical_drive_followup("drive F") == "F:\\"
    # Must NOT hijack ordinary questions that merely start with a filler word.
    assert elliptical_drive_followup("what about dinner?") is None
    assert elliptical_drive_followup("what about my calendar?") is None
    assert elliptical_drive_followup("how much free space do i have?") is None
    assert elliptical_drive_followup("") is None


def test_resolve_drive_scope_prefers_explicit_then_anaphora() -> None:
    assert resolve_drive_scope("free space on C:", last_drive="D:\\") == "C:\\"
    assert resolve_drive_scope("biggest file on that drive", last_drive="D:\\") == "D:\\"
    assert resolve_drive_scope("biggest file in there", last_drive="D:\\") == "D:\\"
    # No prior drive -> no fabricated scope (tool default / all drives).
    assert resolve_drive_scope("biggest file on that drive", last_drive=None) is None
    assert resolve_drive_scope("biggest files", last_drive="D:\\") is None


def test_largest_kind_distinguishes_files_and_folders() -> None:
    assert largest_kind("biggest single file on D:") == "files"
    assert largest_kind("largest folders on C:") == "folders"
    assert largest_kind("biggest items on C:") == "both"
    assert largest_kind("biggest files and folders") == "both"


# ------------------------------------------------------------------------------- routing
class _FakeAgent:
    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        pass

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "task_id": "t"}


def _fake_exec(calls):
    def _exec(intent, args=None, *, source_context=None):
        calls.append((intent, dict(args or {})))
        return types.SimpleNamespace(
            ok=True,
            response_text=f"{intent} ok for {(args or {}).get('drive', '')}",
            status="executed",
            details={"observation": {}},
        )
    return _exec


@pytest.fixture(autouse=True)
def _clear_followup_state():
    fp.reset_machine_followup_state()
    yield
    fp.reset_machine_followup_state()


def _ask(agent, text, session="s1"):
    return fp.maybe_handle_direct_machine_read_request(
        agent, text, session_id=session, source_surface="api", source_context={"session_id": session}
    )


def test_disk_then_elliptical_followup_reruns_disk_scoped_to_new_drive(monkeypatch) -> None:
    agent = _FakeAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))
    assert _ask(agent, "disk space on C:") is not None
    assert calls[-1] == ("machine.disk_usage", {"drive": "C:\\"})
    # The elliptical follow-up re-runs the tool for D:, it does NOT fall through to the model.
    assert _ask(agent, "ok what about D?") is not None
    assert calls[-1] == ("machine.disk_usage", {"drive": "D:\\"})


def test_largest_file_resolves_that_drive_anaphora_and_files_kind(monkeypatch) -> None:
    agent = _FakeAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))
    _ask(agent, "disk space on D:", session="s2")
    assert _ask(agent, "what's the biggest single file in that drive?", session="s2") is not None
    assert calls[-1] == ("machine.find_largest", {"drive": "D:\\", "kind": "files"})


def test_bare_drive_letter_without_a_prior_read_is_not_hijacked(monkeypatch) -> None:
    agent = _FakeAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))
    # No prior machine read for this session -> the fast path declines; the normal pipeline runs.
    assert _ask(agent, "ok what about D?", session="s3") is None
    assert calls == []


# --------------------------------------------------------------------------- executor kind
def test_find_largest_executor_routes_files_kind_to_largest_files(monkeypatch) -> None:
    seen: dict = {}

    def fake_files(root, **kwargs):
        seen["root"] = root
        return {"root": root, "complete": True,
                "entries": [{"name": "big.pak", "size_gb": 9.0, "kind": "file", "complete": True, "delete_safety": "review"}]}

    monkeypatch.setattr(md, "largest_files", fake_files)
    with mock.patch("core.runtime_execution_tools._is_windows_platform", return_value=True):
        res = execute_runtime_tool("machine.find_largest", {"drive": "D:\\", "kind": "files"}, source_context={})
    assert seen["root"] == "D:\\"
    assert res.ok and "files on D:\\" in res.response_text
