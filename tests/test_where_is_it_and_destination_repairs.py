"""The where-is-it / destination repairs from the owner's live beta test (2026-09-18).

Three measured defects, one per section:

A. "ok and where is the foldeR?!" after the chat just built `finalbot` was misrouted to
   ``machine.find_folder`` with the discourse filler "ok" as the search name, and answered
   with a whole-disk listing of unrelated system folders. The bare follow-up "folder"
   searched the disk for folders literally named "folder". The runtime already knows where
   it wrote every file: a location question about the chat's own recent creations is
   answered from the mutation receipts, and filler-only residues never launch a search.
B. "I completed 5 bounded builder steps under `finalbot`" named no location; the folder had
   landed inside VOOL's internal workspace (``~/.vool_runtime/workspace``) and the operator
   had to ask. Build/folder responses and approval asks now state the ABSOLUTE destination.
C. The widened-output retry after a reasoning-exhausted empty response inherited the first
   attempt's transport timeout and died at the transfer watchdog (129s attempt, then the
   retry killed at a flat 180s). The retry's transport budget now matches its widened
   ceiling, still clamped by the enclosing turn deadline.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import os_consent_gate, runtime_paths
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import (
        active_default_db_path,
        configure_default_db_path,
        reset_default_connection,
    )
    from storage.migrations import run_migrations

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    yield
    os_consent_gate.set_consent_override_for_tests(None)
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


# ============================================================================================
# A1 -- a filler is never a folder name
# ============================================================================================

def test_a_filler_residue_is_not_a_folder_name():
    from core.runtime_execution_tools import _find_folder_name_from_phrase

    assert _find_folder_name_from_phrase("ok and where is the foldeR?!") == ("", "")
    assert _find_folder_name_from_phrase("folder") == ("", "")
    assert _find_folder_name_from_phrase("ok where are my files") == ("", "")


def test_real_names_still_derive():
    from core.runtime_execution_tools import _find_folder_name_from_phrase

    # The documented cases keep working: a scope word narrows, a plain name survives.
    assert _find_folder_name_from_phrase("whats in my invoices folder on the desktop") == (
        "invoices",
        "~/Desktop",
    )
    assert _find_folder_name_from_phrase("my-notes-folder")[0] == "my-notes-folder"


def test_the_frontdoor_clears_a_filler_find_folder_argument():
    """The arbiter's filler span ("ok") must not survive as the find_folder name: the family
    falls open to the model turn instead of walking the disk."""
    from core.agent_runtime import turn_frontdoor as frontdoor

    agent = SimpleNamespace()
    decision = SimpleNamespace(family="find_folder", argument="ok")
    result = frontdoor._execute_arbitrated_family(
        agent,
        decision=decision,
        effective_input="ok and where is the foldeR?!",
        session_id="chat-x",
        source_surface="api",
        source_context={"runtime_session_id": "chat-x"},
    )
    assert result is None, "a filler-named find_folder must fall open, not search"


# ============================================================================================
# A2 -- "where is the folder?" answered from this chat's mutation receipts
# ============================================================================================

def _seed_mutations(session_id: str, paths: list[str]) -> None:
    from core.runtime_continuity import append_runtime_event

    for path in paths:
        append_runtime_event(
            session_id=session_id,
            event_type="workspace_mutation_completed",
            message=f"Updated file `{path}` with 30 lines.",
            details={"path": path, "action": "updated"},
        )


def _agent() -> Any:
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="test", persona_id="default")


def test_a_where_question_after_a_build_is_answered_from_receipts(tmp_path):
    agent = _agent()
    _seed_mutations("chat-a", ["finalbot/README.md", "finalbot/src/bot.py"])
    result = agent._maybe_answer_location_followup_turn(
        effective_input="ok and where is the foldeR?!",
        session_id="chat-a",
        source_context={"workspace": str(tmp_path / "ws")},
    )
    assert result is not None
    text = str(result.get("response") or result.get("content") or "")
    assert str((tmp_path / "ws" / "finalbot").resolve()) in text, text
    assert "finalbot/README.md" in text and "finalbot/src/bot.py" in text, text


def test_a_where_question_with_no_receipts_falls_through():
    agent = _agent()
    assert (
        agent._maybe_answer_location_followup_turn(
            effective_input="where is the folder?",
            session_id="chat-empty",
            source_context={},
        )
        is None
    )


def test_a_non_location_turn_is_untouched():
    agent = _agent()
    _seed_mutations("chat-a", ["finalbot/README.md"])
    assert (
        agent._maybe_answer_location_followup_turn(
            effective_input="now add a /balance command to the bot",
            session_id="chat-a",
            source_context={},
        )
        is None
    )


# ============================================================================================
# B -- the destination is stated, absolutely
# ============================================================================================

def test_the_degraded_builder_summary_names_the_absolute_location():
    from core.agent_runtime.builder.support import controller_degraded_response

    agent = SimpleNamespace()
    text = controller_degraded_response(
        agent,
        target={"root_dir": "finalbot"},
        executed_steps=[{"tool_name": "sandbox.run_command", "arguments": {"command": "python -m compileall -q finalbot/src"}, "observation": {}}],
        stop_reason="command_stop_after_success",
        failed_execution=None,
        effective_input="create finalbot",
        session_id="chat-a",
        workspace_root="/Users/owner/.vool_runtime/workspace",
    )
    assert "under `finalbot` (at `/Users/owner/.vool_runtime/workspace/finalbot`)" in text, text


def test_the_created_directory_reply_names_the_absolute_location():
    from core.agent_runtime.builder.support import controller_degraded_response

    agent = SimpleNamespace()
    text = controller_degraded_response(
        agent,
        target={"root_dir": "finalbot"},
        executed_steps=[{"tool_name": "workspace.ensure_directory", "arguments": {"path": "finalbot"}, "observation": {"path": "finalbot"}}],
        stop_reason="workspace_stop_after_directory_bootstrap",
        failed_execution=None,
        effective_input="create finalbot",
        session_id="chat-a",
        workspace_root="/Users/owner/.vool_runtime/workspace",
    )
    assert "`finalbot` (at `/Users/owner/.vool_runtime/workspace/finalbot`)" in text, text


def test_the_approval_ask_shows_the_absolute_write_destination(tmp_path):
    from core.mode_permission_policy import _affected_resources_display

    displayed = _affected_resources_display(
        {"path": "finalbot/README.md", "content": "x"},
        {"workspace": str(tmp_path / "ws")},
    )
    assert displayed == [f"{tmp_path / 'ws'}/finalbot/README.md"]


def test_the_approval_ask_never_rewrites_non_path_fields(tmp_path):
    from core.mode_permission_policy import _affected_resources_display

    displayed = _affected_resources_display(
        {"recipient": "someone@example.com", "url": "https://example.com/x"},
        {"workspace": str(tmp_path / "ws")},
    )
    assert sorted(displayed) == ["https://example.com/x", "someone@example.com"]


# ============================================================================================
# C -- the widened retry's transport budget matches its widened ceiling
# ============================================================================================

def test_the_transport_override_widens_but_still_respects_the_turn_deadline():
    from core import runtime_active_clock
    from core.provider_call_deadline import (
        PROVIDER_DEADLINE_KEY,
        PROVIDER_TRANSPORT_TIMEOUT_KEY,
        effective_timeout_seconds,
    )

    request = SimpleNamespace(
        metadata={
            PROVIDER_TRANSPORT_TIMEOUT_KEY: 360.0,
            PROVIDER_DEADLINE_KEY: runtime_active_clock.monotonic() + 60.0,
        }
    )
    # Widened transport (360s) but the enclosing turn only has 60s left: authority wins.
    assert effective_timeout_seconds(request, 180.0) == pytest.approx(60.0, abs=2.0)

    request_no_deadline = SimpleNamespace(metadata={PROVIDER_TRANSPORT_TIMEOUT_KEY: 360.0})
    assert effective_timeout_seconds(request_no_deadline, 180.0) == 360.0

    plain = SimpleNamespace(metadata={})
    assert effective_timeout_seconds(plain, 180.0) == 180.0


def test_the_widened_retry_carries_the_transport_override():
    """The retry request itself must carry the widened transport floor."""
    from core.memory_first_router import MemoryFirstRouter

    source = (
        Path("core/memory_first_router.py").read_text(encoding="utf-8")
    )
    assert '"_provider_transport_timeout_seconds": _widened_transport' in source
    assert "_widened_transport = min(2.0 * _configured_transport, 600.0)" in source
