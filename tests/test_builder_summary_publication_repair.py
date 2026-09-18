"""The builder-summary publication repair from the owner's live beta test (2026-09-18).

Measured (pocketbot turn): the bounded builder loop executed four file writes and a
command — receipts on the ledger — and because the chat ran on a local model with no
certification run (and the cloud route was declined), the authorship publication gate
replaced the grounded builder summary with the "nothing this turn retrieved, computed
or observed backs it" refusal. The builder lane never registered its executed steps as
runtime-minted support rows, so the gate read the turn as "the lane came back empty".

The closure is the one the mixed-demand lane already applies: the controller records
one support row per executed step, raise-only, claiming no author — the gate then
adjudicates the composed summary per claim against those rows instead of refusing it
wholesale.
"""
from __future__ import annotations

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


def _ineligible_decision():
    from core.final_answer_authorship import (
        REASON_BLOCKED_BEFORE_GENERATION,
        AuthorshipDecision,
    )

    return AuthorshipDecision(
        eligible=False,
        author_role="final_answer",
        task_class="chat_conversation",
        requested_model="ollama-local:qwen3:14b",
        selected_model="ollama-local:qwen3:14b",
        reason=REASON_BLOCKED_BEFORE_GENERATION,
    )


def _ctx() -> dict[str, object]:
    return {
        "runtime_session_id": "chat-pb",
        "session_id": "chat-pb",
        "turn_id": "turn-pb",
        "cancel_turn_id": "turn-pb",
    }


_BUILDER_ROWS = [
    {
        "summary": "Updated file `pocketbot/README.md` with 32 lines.",
        "intent": "builder_step:workspace.write_file",
        "source": "bounded_builder_loop",
    },
    {
        "summary": "Updated file `pocketbot/requirements.txt` with 1 lines.",
        "intent": "builder_step:workspace.write_file",
        "source": "bounded_builder_loop",
    },
    {
        "summary": "Updated file `pocketbot/src/bot.py` with 49 lines.",
        "intent": "builder_step:workspace.write_file",
        "source": "bounded_builder_loop",
    },
    {
        "summary": "Command executed in `.`: python -m compileall -q pocketbot/src (exit 0)",
        "intent": "builder_step:sandbox.run_command",
        "source": "bounded_builder_loop",
    },
]

_COMPOSED_SUMMARY = (
    "I completed 5 bounded builder steps under `pocketbot` (at `/ws/pocketbot`) and "
    "stopped after the command completed.\n\n"
    "Artifacts:\n- changed files: `pocketbot/README.md`, `pocketbot/requirements.txt`, "
    "`pocketbot/src/bot.py`"
)


def test_a_builder_turn_with_runtime_rows_publishes_its_summary_not_the_refusal():
    from core.final_answer_authorship import (
        UNCERTIFIED_AUTHOR_NOTICE_LEAD,
        authorship_record_for_publication,
        gate_authored_content,
        record_authorship_decision,
        record_runtime_support,
    )

    ctx = _ctx()
    record_authorship_decision(
        ctx,
        _ineligible_decision(),
        blocked_model="ollama-local:qwen3:14b",
        request_text="Create a new folder named `pocketbot`...",
    )
    # What the repaired controller now records: one row per executed step.
    assert record_runtime_support(
        ctx, support_rows=_BUILDER_ROWS, request_text="Create a new folder named `pocketbot`..."
    )
    record = authorship_record_for_publication(turn_id="turn-pb")
    assert record is not None and record.supported_by_runtime
    assert not record.must_refuse, "runtime-minted rows mean the lane did not come back empty"

    shipped, payload = gate_authored_content(_COMPOSED_SUMMARY, turn_id="turn-pb")
    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in shipped, shipped
    assert "pocketbot" in shipped, shipped


def test_an_unbacked_turn_still_refuses_instead_of_inventing_an_answer():
    """Refusal control: without runtime-minted rows the same ineligible decision must
    still refuse — this repair publishes RECEIPTS, not unbacked model prose."""
    from core.final_answer_authorship import (
        UNCERTIFIED_AUTHOR_NOTICE_LEAD,
        gate_authored_content,
        record_authorship_decision,
    )

    ctx = _ctx()
    ctx["turn_id"] = "turn-ctl"
    ctx["cancel_turn_id"] = "turn-ctl"
    record_authorship_decision(
        ctx,
        _ineligible_decision(),
        blocked_model="ollama-local:qwen3:14b",
        request_text="tell me something",
    )
    shipped, payload = gate_authored_content("here is an answer with no backing", turn_id="turn-ctl")
    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in shipped
    assert payload.get("publication") in {"refused", "refused_idempotent"}


def test_the_builder_controller_registers_its_executed_steps():
    """The controller call site itself: after a loop with executed steps, the turn's
    authorship record carries runtime support."""
    from unittest import mock

    from apps.vool_agent import VoolAgent
    from core.agent_runtime.builder import controller as builder_controller
    from core.final_answer_authorship import (
        authorship_record_for_publication,
        record_runtime_support,
    )

    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    recorded: dict[str, object] = {}

    def _capture(source_context, *, support_rows=None, request_text=""):
        recorded["rows"] = list(support_rows or [])
        recorded["request_text"] = request_text
        return record_runtime_support(source_context, support_rows=support_rows, request_text=request_text)

    executed = [
        {"tool_name": "workspace.write_file", "response_text": "Updated file `pocketbot/src/bot.py` with 49 lines.", "arguments": {}},
        {"tool_name": "sandbox.run_command", "response_text": "Command executed in `.`: compileall (exit 0)", "arguments": {}},
    ]
    with mock.patch.object(
        agent, "_run_bounded_builder_loop", return_value=(executed, {}, "command_stop_after_success", None)
    ), mock.patch.object(
        agent,
        "_builder_controller_profile",
        return_value={
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": {"platform": "generic", "language": "python", "root_dir": "pocketbot"},
        },
    ), mock.patch(
        "core.final_answer_authorship.record_runtime_support", side_effect=_capture
    ):
        # `_should_run_builder_controller` gates entry; force it on for the drive.
        with mock.patch.object(agent, "_should_run_builder_controller", return_value=True), mock.patch.object(
            agent, "_builder_controller_artifacts", return_value={}
        ), mock.patch.object(
            agent, "_builder_controller_observations", return_value={}
        ), mock.patch.object(
            agent, "_builder_controller_degraded_response", return_value="degraded"
        ), mock.patch.object(
            agent, "_builder_controller_workflow_summary", return_value="summary"
        ), mock.patch.object(
            agent, "_builder_controller_direct_response", return_value=None
        ), mock.patch.object(
            agent, "_is_chat_truth_surface", return_value=False
        ), mock.patch.object(
            agent, "_action_fast_path_result", return_value={"ok": True, "response": "x"}
        ):
            builder_controller.maybe_run_builder_controller(
                agent,
                task=type("T", (), {"task_id": "task-pb"})(),
                effective_input="Create a new folder named `pocketbot`.",
                classification={"task_class": "unknown"},
                interpretation=None,
                web_notes=[],
                session_id="chat-pb",
                source_context=_ctx(),
                render_capability_truth_response_fn=lambda report: "gap",
                load_active_persona_fn=lambda _pid: None,
            )
    assert recorded.get("rows"), "the controller must register its executed steps as support"
    assert any("pocketbot/src/bot.py" in str(row.get("summary")) for row in recorded["rows"])
