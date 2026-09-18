from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.runtime_task_events import (
    configure_runtime_event_store,
    emit_runtime_event,
    list_runtime_session_events,
    list_runtime_sessions,
    reset_runtime_event_state,
)
from core.runtime_task_rail import render_runtime_task_rail_html
from storage.migrations import run_migrations


class RuntimeTaskEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-events.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_event_store(str(self._db_path))
        reset_runtime_event_state()

    def tearDown(self) -> None:
        reset_runtime_event_state()
        configure_runtime_event_store(None)
        self._tmp.cleanup()

    def test_runtime_session_store_tracks_recent_sessions_and_events(self) -> None:
        context = {"runtime_session_id": "openclaw:test-session"}
        emit_runtime_event(
            context,
            event_type="task_received",
            message="Received request: inspect the repo",
            details={"request_preview": "inspect the repo"},
        )
        emit_runtime_event(
            context,
            event_type="tool_selected",
            message="Running real tool workspace.search_text.",
            details={"tool_name": "workspace.search_text", "task_class": "debugging"},
        )

        sessions = list_runtime_sessions(limit=10)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "openclaw:test-session")
        self.assertEqual(sessions[0]["event_count"], 2)
        self.assertEqual(sessions[0]["request_preview"], "inspect the repo")
        self.assertEqual(sessions[0]["task_class"], "debugging")
        self.assertEqual(sessions[0]["execution_history"]["latest_tool"], "workspace.search_text")
        self.assertEqual(sessions[0]["execution_history"]["bounded_execution"]["tool_attempt_count"], 1)
        self.assertEqual(sessions[0]["execution_history"]["timeline"][0]["value"], "accepted")

        events = list_runtime_session_events("openclaw:test-session", after_seq=0, limit=10)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["seq"], 1)
        self.assertEqual(events[1]["seq"], 2)
        self.assertEqual(events[1]["tool_name"], "workspace.search_text")

    def test_terminal_result_labels_match_success_and_failure_outcomes(self) -> None:
        from core.web.api.runtime import _final_task_event

        completed = _final_task_event({"status": "completed"})
        failed = _final_task_event({"status": "failed"})

        self.assertEqual(completed["stage"], "Completed")
        self.assertNotEqual(completed["stage"], "Failed safely")
        self.assertEqual(failed["stage"], "Failed safely")

    def test_events_are_tagged_with_client_turn_id_for_turn_scoped_activity(self) -> None:
        # The Activity panel scopes the ledger to the CURRENT turn by client_turn_id; the server must
        # stamp it from source_context["cancel_turn_id"] (the per-turn id the /api/chat body carries).
        context = {"runtime_session_id": "openclaw:turn-scope", "cancel_turn_id": "turn-abc-123"}
        emit_runtime_event(context, event_type="scope_resolved", message="Auditing /repo",
                           details={"workspace_root": "/repo"})
        # A caller that already set client_turn_id keeps its own value (additive, never overridden).
        emit_runtime_event(context, event_type="tool_selected", message="tool",
                           details={"tool_name": "workspace.list_tree", "client_turn_id": "explicit"})
        # No turn id in context -> no tag (legacy events stay untagged, not mislabeled).
        emit_runtime_event({"runtime_session_id": "openclaw:turn-scope"},
                           event_type="task_completed", message="done")

        events = list_runtime_session_events("openclaw:turn-scope", after_seq=0, limit=10)
        self.assertEqual(events[0]["client_turn_id"], "turn-abc-123")
        self.assertEqual(events[1]["client_turn_id"], "explicit")
        self.assertNotIn("client_turn_id", events[2])

    def test_tool_selection_persists_redacted_arguments(self) -> None:
        from core.tool_arg_redaction import redact_tool_arguments

        context = {"runtime_session_id": "openclaw:redacted-tool-args"}
        emit_runtime_event(
            context,
            event_type="tool_selected",
            message="Running machine.read_file.",
            details={
                "tool_name": "machine.read_file",
                "tool_args": redact_tool_arguments(
                    {"path": "notes.txt", "api_key": "must-not-persist", "content": "private body"}
                ),
            },
        )
        event = list_runtime_session_events("openclaw:redacted-tool-args", after_seq=0, limit=10)[0]
        self.assertIn("path=notes.txt", event["tool_args"])
        self.assertIn("api_key: [redacted]", event["tool_args"])
        self.assertNotIn("must-not-persist", event["tool_args"])
        self.assertNotIn("private body", event["tool_args"])

    def test_runtime_task_rail_html_contains_polling_endpoints(self) -> None:
        html = render_runtime_task_rail_html()
        self.assertIn("VOOL Task Rail", html)
        self.assertIn("VOOL Trace Rail", html)
        self.assertIn("VOOL Operator Workstation", html)
        self.assertIn("Trace workstation v1", html)
        self.assertIn("workstation v1", html)
        self.assertIn("wk-topbar", html)
        self.assertIn(">Overview<", html)
        self.assertIn(">Hive<", html)
        self.assertIn(">Trace<", html)
        self.assertIn(">Human<", html)
        self.assertIn(">Agent<", html)
        self.assertIn(">Raw<", html)
        self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, html)
        self.assertIn('data-workstation-surface="trace-rail"', html)
        self.assertIn("http://127.0.0.1:11435/trace", html)
        self.assertIn("/api/runtime/sessions", html)
        self.assertIn("/api/runtime/events", html)
        self.assertIn("selectedStepTitle", html)
        self.assertIn("selectedStepMeta", html)
        self.assertIn("traceRawPanel", html)
        self.assertIn("trace-center-shell", html)
        self.assertIn("session rail", html)
        self.assertIn("selected-step center", html)
        self.assertIn("data-event-seq", html)
        self.assertIn("Retries / Queries", html)
        self.assertIn("max-width: none;", html)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", html)
        self.assertIn("grid-template-columns: minmax(280px, 320px) minmax(0, 1.45fr) minmax(300px, 360px);", html)
        self.assertNotIn("__WORKSTATION_STYLES__", html)
        self.assertNotIn("__WORKSTATION_HEADER__", html)
        self.assertNotIn("__WORKSTATION_SCRIPT__", html)
        self.assertNotIn("__TASK_RAIL_CLIENT_SCRIPT__", html)


if __name__ == "__main__":
    unittest.main()
