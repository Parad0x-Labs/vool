"""TOOLSMITH closure item 3: canonical mutation Activity events.

Rides the EXISTING runtime session-event pipeline (`core.runtime_task_events.emit_runtime_event`
-> `core.runtime_continuity.append_runtime_event` -> the `runtime_session_events` table the
Activity panel and SSE task rail already read) rather than a new audit store. Every
write_file/replace_in_file/apply_unified_diff/rollback_last_change call now emits exactly one
`workspace_mutation_*` event carrying: session ID (implicit -- the event is stored under it), task
ID, tool intent, canonical target, permission decision, start/completion time, result state,
before_hash/after_hash, a size-bounded diff summary, rollback ID, error class, conflict reason, and
the daemon's own commit SHA. No raw file content is ever included.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    list_runtime_session_events,
    reset_runtime_continuity_state,
)
from core.runtime_execution_tools import execute_runtime_tool
from storage.migrations import run_migrations


class MutationActivityEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-continuity.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self._workspace_tmp = tempfile.TemporaryDirectory()
        self.workspace = self._workspace_tmp.name

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()
        self._workspace_tmp.cleanup()

    def _events(self, session_id: str) -> list[dict]:
        return list_runtime_session_events(session_id, after_seq=0, limit=200)

    def _mutation_events(self, session_id: str) -> list[dict]:
        return [row for row in self._events(session_id) if str(row.get("event_type") or "").startswith("workspace_mutation")]

    def test_successful_write_emits_workspace_mutation_completed_with_required_fields(self) -> None:
        session_id = "toolsmith-activity-write"
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "a.txt", "content": "hello"},
            source_context={"workspace": self.workspace, "session_id": session_id, "task_id": "task-1"},
        )
        assert result is not None and result.ok

        events = self._mutation_events(session_id)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "workspace_mutation_completed")
        self.assertEqual(event["tool_intent"], "workspace.write_file")
        self.assertEqual(event["canonical_target"], "a.txt")
        self.assertEqual(event["task_id"], "task-1")
        self.assertEqual(event["permission_decision"], "allowed")
        self.assertEqual(event["result_state"], "executed")
        self.assertTrue(event["ok"])
        self.assertTrue(event["operation_started_at"])
        self.assertTrue(event["operation_completed_at"])
        # This call created a.txt (it did not exist in a fresh tempdir workspace) -- before_hash
        # empty is correct ONLY because the event itself says so via `action`, not accepted
        # unconditionally regardless of what kind of write this was.
        self.assertEqual(event["action"], "created")
        self.assertEqual(event["before_hash"], "")
        self.assertEqual(len(event["after_hash"]), 64)
        self.assertEqual(event["error_class"], "")
        self.assertTrue(event["daemon_sha"], "daemon_sha must be populated (git rev-parse HEAD)")
        self.assertEqual(event["workspace_identity"], str(Path(self.workspace).resolve()))
        # `diff_summary` legitimately shows a bounded SNIPPET of what changed (that's the point of
        # a diff) -- what must never appear is the FULL raw content under its own key, and the
        # snippet itself must stay within the same size bound every other file_diff artifact uses.
        self.assertNotIn("before_text", event)
        self.assertNotIn("after_text", event)
        self.assertNotIn("content", event)
        self.assertLessEqual(len(event["diff_summary"]), 1700)
        self.assertTrue(event["diff_summary"].strip(), "diff_summary must never be silently empty")
        self.assertIn("+hello", event["diff_summary"], "a real create must show meaningful diff content, not just a placeholder")

    def test_modification_emits_a_real_64_char_before_hash_not_an_empty_one(self) -> None:
        """Sabotage target companion: distinguishes 'before_hash is empty because this was a
        create' (tested above) from 'before_hash is empty because something dropped it' -- for a
        MODIFICATION of a file that already existed, before_hash must be a real 64-char sha256,
        never empty."""
        session_id = "toolsmith-activity-modify"
        path = Path(self.workspace) / "a.txt"
        path.write_text("original content", encoding="utf-8")

        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "a.txt", "content": "modified content"},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert result is not None and result.ok

        event = self._mutation_events(session_id)[0]
        self.assertEqual(event["action"], "updated")
        self.assertEqual(len(event["before_hash"]), 64, "a modification of an EXISTING file must never report an empty before_hash")
        self.assertEqual(len(event["after_hash"]), 64)
        self.assertNotEqual(event["before_hash"], event["after_hash"])
        self.assertTrue(event["diff_summary"].strip())
        self.assertIn("-original content", event["diff_summary"])
        self.assertIn("+modified content", event["diff_summary"])

    def test_failed_write_emits_workspace_mutation_failed_with_error_class(self) -> None:
        session_id = "toolsmith-activity-failed-write"
        path = Path(self.workspace) / "config.py"
        path.write_text("TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n", encoding="utf-8")

        result = execute_runtime_tool(
            "workspace.replace_in_file",
            {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert result is not None and not result.ok
        self.assertEqual(result.status, "ambiguous_match")

        events = self._mutation_events(session_id)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "workspace_mutation_failed")
        self.assertEqual(event["result_state"], "ambiguous_match")
        self.assertEqual(event["error_class"], "ambiguous_match")
        self.assertFalse(event["ok"])

    def test_rollback_success_emits_rollback_completed_with_rollback_id(self) -> None:
        session_id = "toolsmith-activity-rollback"
        path = Path(self.workspace) / "a.txt"
        path.write_text("original", encoding="utf-8")
        written = execute_runtime_tool(
            "workspace.write_file",
            {"path": "a.txt", "content": "changed"},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert written is not None and written.ok
        write_event = self._mutation_events(session_id)[-1]
        expected_rollback_id = write_event["rollback_id"]
        self.assertTrue(expected_rollback_id)

        reverted = execute_runtime_tool(
            "workspace.rollback_last_change", {}, source_context={"workspace": self.workspace, "session_id": session_id}
        )
        assert reverted is not None and reverted.ok

        events = self._mutation_events(session_id)
        self.assertEqual(len(events), 2)
        rollback_event = events[-1]
        self.assertEqual(rollback_event["event_type"], "workspace_mutation_rollback_completed")
        self.assertEqual(rollback_event["rollback_id"], expected_rollback_id)
        # rollback_last_change's own details carry no file_diff artifact -- diff_summary must
        # still say something structured about why, never a silent empty string.
        self.assertTrue(rollback_event["diff_summary"].strip())
        self.assertEqual(rollback_event["diff_summary"], "(no diff: this operation does not produce a file diff)")

    def test_stale_revert_conflict_emits_rollback_conflict_event(self) -> None:
        session_id = "toolsmith-activity-conflict"
        path = Path(self.workspace) / "shared.txt"
        path.write_text("original", encoding="utf-8")
        written = execute_runtime_tool(
            "workspace.write_file",
            {"path": "shared.txt", "content": "vool-changed"},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert written is not None and written.ok
        path.write_text("external edit", encoding="utf-8")

        reverted = execute_runtime_tool(
            "workspace.rollback_last_change", {}, source_context={"workspace": self.workspace, "session_id": session_id}
        )
        assert reverted is not None and not reverted.ok
        self.assertEqual(reverted.status, "stale_revert_conflict")

        events = self._mutation_events(session_id)
        conflict_event = events[-1]
        self.assertEqual(conflict_event["event_type"], "workspace_mutation_rollback_conflict")
        self.assertEqual(conflict_event["conflict_reason"], "stale_revert_conflict")
        self.assertEqual(conflict_event["result_state"], "stale_revert_conflict")

    def test_no_raw_file_content_in_any_mutation_event(self) -> None:
        session_id = "toolsmith-activity-no-content-leak"
        secret_content = "API_KEY=sk-super-secret-value-should-never-be-in-activity"
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "secrets.env", "content": secret_content},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert result is not None and result.ok
        events = self._mutation_events(session_id)
        blob = str(events)
        self.assertNotIn("sk-super-secret-value", blob)
        self.assertNotIn(secret_content, blob)


class MutationActivitySabotageTests(unittest.TestCase):
    """Each case: strip one required field/behavior from the emitter, confirm the corresponding
    assertion above would go red, then restore. Implemented by re-running one focused check
    inline against a monkey-patched emitter rather than duplicating the whole test body."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-continuity.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self._workspace_tmp = tempfile.TemporaryDirectory()
        self.workspace = self._workspace_tmp.name

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()
        self._workspace_tmp.cleanup()

    def _write(self, session_id: str) -> None:
        result = execute_runtime_tool(
            "workspace.write_file",
            {"path": "a.txt", "content": "hello"},
            source_context={"workspace": self.workspace, "session_id": session_id},
        )
        assert result is not None and result.ok

    def _last_event(self, session_id: str) -> dict:
        events = list_runtime_session_events(session_id, after_seq=0, limit=200)
        mutation_events = [row for row in events if str(row.get("event_type") or "").startswith("workspace_mutation")]
        return mutation_events[-1]

    def test_sabotage_disabling_activity_persistence_leaves_no_event(self) -> None:
        with mock.patch("core.runtime_task_events.emit_runtime_event", return_value=None):
            self._write("sabotage-disabled")
        events = list_runtime_session_events("sabotage-disabled", after_seq=0, limit=200)
        self.assertEqual(events, [], "disabling persistence must leave zero events -- proves the real path writes one")

    def test_sabotage_stripping_rollback_id_is_detected(self) -> None:
        """Mutates the REAL `record_workspace_mutation` return value (a genuine production
        symbol/input, not a hand-written parallel emitter) so the mutation record it hands back
        has no ID -- the REAL, unmodified `_emit_mutation_activity_event_impl` then runs on that
        input and is proven to compute an empty rollback_id from it."""
        import core.runtime_execution_tools as ret

        # Control: the real path sets rollback_id -- establishes the assertion below is
        # meaningful (not vacuously true) before sabotaging it.
        self._write("sabotage-rollback-id-control")
        control_event = self._last_event("sabotage-rollback-id-control")
        self.assertTrue(control_event["rollback_id"], "control: real path sets rollback_id")

        with mock.patch.object(ret, "record_workspace_mutation", return_value={"mutation_id": "", "session_key": "sabotaged"}):
            self._write("sabotage-rollback-id")
        sabotaged_event = self._last_event("sabotage-rollback-id")
        self.assertEqual(sabotaged_event["rollback_id"], "", "with a real mutation record carrying no ID, the real emitter must be shown reporting an empty rollback_id")

    def test_sabotage_stripping_hashes_is_detected(self) -> None:
        """Mutates the REAL `content_sha256` (the actual hashing function both `_write_file` and
        `build_file_diff_artifact` call -- two independent module-level references to the same
        underlying algorithm, so both must be patched to actually break hashing end to end) to
        always return an empty string -- a genuine production symbol, not a fake emitter -- and
        lets the real write path and the real `_emit_mutation_activity_event_impl` run on top of
        that broken dependency."""
        import core.execution.artifacts as artifacts_module
        import core.runtime_execution_tools as ret

        with mock.patch.object(ret, "content_sha256", return_value=""), mock.patch.object(artifacts_module, "content_sha256", return_value=""):
            self._write("sabotage-hashes")
        event = self._last_event("sabotage-hashes")
        self.assertEqual(event["after_hash"], "", "with the real hashing function broken, the real emitter must be shown reporting an empty hash instead of a 64-char one")

    def test_sabotage_removing_permission_decision_classification_is_detected(self) -> None:
        """Mutates the REAL module-level classification set `_MUTATION_PERMISSION_DENIAL_STATUSES`
        (the actual lookup table `_emit_mutation_activity_event_impl` uses to decide "denied" vs
        "allowed") and drives a REAL failing write through the REAL dispatcher and REAL emitter to
        show the set is load-bearing.

        Honest architecture note found while writing this test, not glossed over: `disabled` and
        `permission_denied` -- the set's two REAL literal members -- are both intercepted upstream
        of `_dispatch_with_mutation_activity` (a `contract.supported` gate at
        runtime_execution_tools.py:~2109 for `disabled`, and the `audit_tool_permission_denial`
        gate at ~2082 for `permission_denied`), so neither ever reaches the emitter through the
        public dispatcher for `workspace.write_file` -- `permission_decision` can currently only
        ever observably read "allowed" in a REAL Activity event, regardless of this set's contents.
        That is a real, separate finding (the classification set is effectively dead for its own
        two members) worth flagging for its own review, not something this repair should silently
        paper over or expand into fixing -- ANVIL's ask here was to make the SABOTAGE test honest,
        not to redesign the permission-denial classification. This test therefore proves the
        classification MECHANISM (the set membership check) is real and load-bearing using
        `ambiguous_match` -- a status that DOES reach the emitter -- rather than the two statuses
        that structurally cannot.
        """
        import core.runtime_execution_tools as ret

        path = Path(self.workspace) / "config.py"
        path.write_text("TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n", encoding="utf-8")

        # Control: ambiguous_match is NOT in the real denial set -- correctly classified "allowed"
        # (this dispatcher's own policy checks passed; the tool itself simply refused on ambiguity).
        control = execute_runtime_tool(
            "workspace.replace_in_file",
            {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
            source_context={"workspace": self.workspace, "session_id": "sabotage-permission-control"},
        )
        assert control is not None and not control.ok and control.status == "ambiguous_match"
        control_event = self._last_event("sabotage-permission-control")
        self.assertEqual(control_event["permission_decision"], "allowed", "control: ambiguous_match is correctly classified before sabotage")

        with mock.patch.object(ret, "_MUTATION_PERMISSION_DENIAL_STATUSES", frozenset({"ambiguous_match"})):
            sabotaged = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
                source_context={"workspace": self.workspace, "session_id": "sabotage-permission"},
            )
        assert sabotaged is not None and not sabotaged.ok and sabotaged.status == "ambiguous_match"
        sabotaged_event = self._last_event("sabotage-permission")
        self.assertEqual(
            sabotaged_event["permission_decision"], "denied",
            "with ambiguous_match added to the real denial set, the real emitter must be shown reclassifying it as denied -- proving the set is genuinely read, not ignored",
        )

    def test_sabotage_reporting_success_after_a_failed_write_is_detected(self) -> None:
        """Calls the REAL `_replace_in_file` for a genuine, normally-reached failure
        (`ambiguous_match`, returned normally rather than via an exception) and forces only the
        real result object's `.ok` attribute to True afterward -- simulating exactly the class of
        bug ANVIL is checking for (the handler's own success/failure determination is wrong) --
        then lets the REAL, unmodified `_emit_mutation_activity_event_impl` run on that
        doctored-but-otherwise-real result. (`disabled`, used in an earlier version of this test,
        cannot reach the emitter at all -- see the note in the sibling test above -- so it cannot
        demonstrate this specific failure mode; `ambiguous_match` genuinely can.)"""
        import core.runtime_execution_tools as ret

        path = Path(self.workspace) / "config.py"
        path.write_text("TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n", encoding="utf-8")

        real_replace_in_file = ret._replace_in_file

        def _replace_in_file_with_flipped_ok(*args, **kwargs):
            real_result = real_replace_in_file(*args, **kwargs)
            real_result.ok = True  # SABOTAGE: the replace was genuinely ambiguous and refused;
            # only the success flag is forced true here, on the real result object, before it
            # reaches the real emitter -- nothing about the emitter itself is replaced.
            return real_result

        with mock.patch.object(ret, "_replace_in_file", side_effect=_replace_in_file_with_flipped_ok):
            result = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "config.py", "old_text": "TIMEOUT = 30", "new_text": "TIMEOUT = 60"},
                source_context={"workspace": self.workspace, "session_id": "sabotage-false-success"},
            )
        assert result is not None
        self.assertTrue(result.ok, "control: the sabotage really did flip the caller-visible result to ok=True")
        self.assertEqual(path.read_text(encoding="utf-8"), "TIMEOUT = 30\nother = 1\nTIMEOUT = 30\n", "the file itself was never actually touched -- only the reported result lied")

        event = self._last_event("sabotage-false-success")
        self.assertEqual(
            event["event_type"], "workspace_mutation_completed",
            "with the result's own ok flag wrongly forced true, the real emitter must be shown reporting workspace_mutation_completed for a replace that was actually refused",
        )


if __name__ == "__main__":
    unittest.main()
