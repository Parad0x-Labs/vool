"""Black-box contracts captured from the 2026-08-12 live milestone smoke.

These are deliberately cross-boundary tests.  Nearby unit tests already prove that the individual
helpers can pass in isolation; these fixtures preserve the missing joins between streaming and the
canonical result, display metadata and model history, per-turn polling and a chat-wide ledger,
preference events and task lifecycle, output validation and retry, clause parsing and constraint
scope, and conductor planning and effect compatibility.

Each incident is an expected failure on the captured build.  ``strict=True`` makes a repaired
contract turn into XPASS (and therefore fail) until the implementing change removes the marker.
That keeps the corpus safe to land before production changes without letting a fix pass unnoticed.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

INCIDENTS = json.loads(
    (Path(__file__).parent / "fixtures" / "master_live_incidents.json").read_text(
        encoding="utf-8"
    )
)


def _known_incident(owner: str):
    return pytest.mark.xfail(
        strict=True,
        reason=f"2026-08-12 live contract not implemented yet; owner: {owner}",
    )


def _shipped_client_visible_content(*, speculative: str, canonical: str) -> str:
    """Drive transport and apply the shipped client's terminal-commit authority.

    Deltas are deliberately speculative.  A legacy concatenating client keeps those bytes, while
    VOOL's client replaces them when the final frame carries ``vool_response_commit``.
    """

    from core.runtime_task_events import emit_runtime_event
    from core.web.api import runtime as api_runtime

    def fake_run_agent(_runtime, _text, *, session_id, source_context):
        emit_runtime_event(
            source_context,
            event_type="model_output_chunk",
            message=speculative,
        )
        return {"response": canonical, "usage_summary": {}}

    visible = ""
    for raw in api_runtime.stream_agent_with_events(
        None,
        INCIDENTS["stream_commit_divergence"]["prompt"],
        session_id="openclaw:master-live-stream-commit",
        model="fixture-model",
        source_context={"surface": "openclaw"},
        run_agent_provider=fake_run_agent,
    ):
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        for part in line.splitlines():
            if not part.strip().startswith("{"):
                continue
            payload = json.loads(part)
            visible += str((payload.get("message") or {}).get("content") or "")
            commit = payload.get("vool_response_commit")
            if isinstance(commit, dict) and commit.get("type") == "response.commit":
                visible = str(commit.get("canonical_content") or "")
    return visible


def test_visible_stream_is_reconciled_to_the_canonical_final_response() -> None:
    incident = INCIDENTS["stream_commit_divergence"]

    visible = _shipped_client_visible_content(
        speculative=incident["speculative_text"],
        canonical=incident["canonical_text"],
    )

    assert visible == incident["canonical_text"], incident["invariant"]


@pytest.fixture(scope="module")
def chat_page():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    manager, browser = served_browser.launch_chromium()

    page = browser.new_page()

    def route_all(route):
        if route.request.resource_type == "document":
            route.fulfill(
                status=200,
                content_type="text/html",
                body=render_vool_chat_html(),
            )
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/*", route_all)
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(150)
    try:
        yield page
    finally:
        browser.close()
        manager.stop()


def test_provenance_footer_never_enters_next_turn_model_history(chat_page) -> None:
    incident = INCIDENTS["provenance_history_contamination"]
    outbound = chat_page.evaluate(
        """(fixture) => {
            const owner = chatState(displayedChat);
            owner.history = [];
            const run = adoptRun(displayedChat, newRun(displayedChat));
            recordUserMessage(displayedChat, fixture.prompt, '');
            recordAssistantMessage(
                run,
                fixture.canonical_text + '\\n\\n' + fixture.provenance_footer
            );
            return buildTurnRequestBody(run, 'fixture-model').messages;
        }""",
        incident,
    )

    assistant = [message for message in outbound if message["role"] == "assistant"][-1]
    assert assistant["content"] == incident["canonical_text"], incident["invariant"]
    assert incident["provenance_footer"] not in json.dumps(outbound)


def test_activity_repoll_across_turns_never_duplicates_backend_sequence(chat_page) -> None:
    incident = INCIDENTS["activity_replay"]
    result = chat_page.evaluate(
        """(fixture) => {
            const owner = chatState(displayedChat);
            resetChatLedger(owner);
            const backend = [];
            for (let seq = 1; seq <= fixture.event_count; seq++) {
              backend.push({
                session_id: displayedChat,
                seq: seq,
                client_turn_id: 'turn-' + (Math.floor((seq - 1) / 40) + 1),
                event_type: seq % 40 === 1 ? 'task_received' : 'model.call_completed',
                message: 'event ' + seq,
              });
            }

            // Initial chat hydration followed by two complete historical replays, the exact shape
            // produced when the shipped client gave every new run an independent cursor at zero.
            backend.forEach((event) => appendToChatLedger(owner, event));
            for (let poll = 0; poll < 2; poll++) {
              const run = newRun(displayedChat);
              backend.forEach((event) => {
                const key = String(event.seq);
                if (!run.ledgerSeen[key]) appendToChatLedger(owner, event);
                run.ledgerSeen[key] = 1;
              });
            }
            return {
              rendered: owner.chatLedger.length,
              unique: new Set(owner.chatLedger.map((event) => event.seq)).size,
            };
        }""",
        incident,
    )

    assert result == {
        "rendered": incident["event_count"],
        "unique": incident["event_count"],
    }, incident["invariant"]


@pytest.fixture()
def runtime_continuity_db(tmp_path: Path) -> Iterator[None]:
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.migrations import run_migrations

    db_path = tmp_path / "master-live-runtime.db"
    run_migrations(db_path=db_path)
    configure_runtime_continuity_db_path(str(db_path))
    reset_runtime_continuity_state()
    try:
        yield
    finally:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)


def test_preference_only_mode_sync_cannot_create_a_running_task(runtime_continuity_db) -> None:
    from core.runtime_continuity import append_runtime_event, list_runtime_sessions

    incident = INCIDENTS["draft_mode_sync"]
    session_id = "openclaw:master-live-empty-draft"
    append_runtime_event(
        session_id=session_id,
        event_type=incident["event_type"],
        message="Mode changed to Manual.",
        details={"active_mode": "manual"},
    )

    session = next(row for row in list_runtime_sessions(limit=10) if row["session_id"] == session_id)
    assert session["status"] != "running", incident["invariant"]


def test_generic_response_control_fallback_remains_a_retry_target(runtime_continuity_db) -> None:
    from core.agent_runtime.checkpoints import finalize_runtime_checkpoint
    from core.ordinary_chat_response_guard import ordinary_chat_safe_fallback
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        latest_failed_checkpoint,
    )
    from core.runtime_continuity import (
        finalize_runtime_checkpoint as persist_final_checkpoint,
    )

    incident = INCIDENTS["unfulfilled_fallback_retry"]
    session_id = "openclaw:master-live-unfulfilled"
    checkpoint = create_runtime_checkpoint(
        session_id=session_id,
        request_text=incident["prompt"],
        source_context={"runtime_session_id": session_id},
    )
    finalize_runtime_checkpoint(
        {
            "runtime_session_id": session_id,
            "runtime_checkpoint_id": checkpoint["checkpoint_id"],
            "response_control": {
                "fallback_applied": True,
                "ordinary_chat_output": {
                    "allowed": False,
                    "reasons": [incident["failure_reason"]],
                },
            },
        },
        status="completed",
        final_response=ordinary_chat_safe_fallback(),
        finalize_runtime_checkpoint_fn=persist_final_checkpoint,
    )

    failed = latest_failed_checkpoint(session_id)
    assert failed is not None, incident["invariant"]
    assert failed["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert failed["request_text"] == incident["prompt"]


def test_alphabetic_clause_constraint_is_not_promoted_to_the_whole_turn() -> None:
    from core.plain_task_routing import ordinary_plain_request_count
    from core.response_constraints import parse_response_constraint

    incident = INCIDENTS["alphabetic_clause_constraint"]
    prompt = incident["prompt"]
    constraint = parse_response_constraint(prompt)

    assert ordinary_plain_request_count(prompt) == incident["request_count"]
    assert constraint is None or constraint.exact_sentences is None, incident["invariant"]
    assert constraint is None or constraint.max_sentences is None, incident["invariant"]


def _sink_plan():
    from core.conductor.planner import build_plan_from_clauses, parse_clauses

    incident = INCIDENTS["impossible_action_with_know_sibling"]
    model_plan = json.dumps(
        [
            {
                "request": "Shut off the overflowing sink in my kitchen",
                "operation": "workspace_investigation",
                "depends_on": [],
            },
            {
                "request": "Explain the physics of why closing the valve stops the flow",
                "operation": "factual_explanation",
                "depends_on": [],
            },
        ]
    )

    # Enter below the ordinary/conductor eligibility gate.  This is the deterministic boundary
    # implicated by the live trace: given the two clauses the planner proposed, the runtime must
    # validate their effects rather than accepting a syntactically real but semantically unrelated
    # workspace tool.  Eligibility itself is intentionally not under test here.
    return build_plan_from_clauses(
        parse_clauses(model_plan),
        original_request=incident["prompt"],
        plan_id="master-live-sink",
    )


def test_impossible_physical_action_cannot_be_satisfied_by_workspace_search() -> None:
    incident = INCIDENTS["impossible_action_with_know_sibling"]
    plan = _sink_plan()

    assert not any(node.tool_intent == "workspace.search_text" for node in plan.nodes), (
        incident["invariant"]
    )


def test_impossible_action_does_not_suppress_independent_know_clause() -> None:
    incident = INCIDENTS["impossible_action_with_know_sibling"]
    plan = _sink_plan()
    know_nodes = [
        node
        for node in plan.nodes
        if "physics" in node.request_text.lower() or "valve" in node.request_text.lower()
    ]
    assert know_nodes and all(not node.unresolved_reason for node in know_nodes), (
        incident["invariant"]
    )
