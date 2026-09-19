from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.execution_gate import ExecutionGate
from core.local_operator_actions import (
    OperatorActionIntent,
    OperatorActionResult,
    list_operator_tools,
    operator_capability_ledger,
    parse_operator_action_intent,
)
from core.operator import (
    OperatorActionIntent as ExtractedOperatorActionIntent,
)
from core.operator import (
    OperatorActionResult as ExtractedOperatorActionResult,
)
from core.operator import (
    list_operator_tools as extracted_list_operator_tools,
)
from core.operator import (
    operator_capability_ledger as extracted_operator_capability_ledger,
)
from core.operator import (
    parse_operator_action_intent as extracted_parse_operator_action_intent,
)
from core.operator import storage as operator_storage
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import data_path
from core.user_preferences import maybe_handle_preference_command
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.replica_table import holders_for_shard


def _count_learning_shards() -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM learning_shards").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _count_pending_actions() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM operator_action_requests WHERE status = 'pending_approval'"
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


class OperatorActionTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        prefs_path = data_path("user_preferences.json")
        if prefs_path.exists():
            prefs_path.unlink()
        conn = get_connection()
        try:
            for table in ("operator_action_requests", "learning_shards", "local_tasks", "knowledge_holders", "knowledge_manifests"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def test_facade_exports_share_extracted_operator_symbols(self) -> None:
        self.assertIs(OperatorActionIntent, ExtractedOperatorActionIntent)
        self.assertIs(OperatorActionResult, ExtractedOperatorActionResult)
        self.assertEqual(
            parse_operator_action_intent('list tools please'),
            extracted_parse_operator_action_intent('list tools please'),
        )

        tools = [
            {
                "tool_id": "schedule_calendar_event",
                "category": "calendar",
                "destructive": True,
                "available": False,
                "description": "Create local calendar events.",
            }
        ]
        with mock.patch("core.local_operator_actions.list_operator_tools", return_value=tools):
            ledger = operator_capability_ledger()

        self.assertEqual(list_operator_tools(), extracted_list_operator_tools())
        self.assertEqual(ledger, extracted_operator_capability_ledger(tools=tools))

    def test_disk_inspection_creates_cleanup_preview(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "big.bin").write_bytes(b"x" * 4096)
            temp_root = root / "temp"
            temp_root.mkdir()
            (temp_root / "cache.bin").write_bytes(b"y" * 2048)
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                result = agent.run_once(
                    f'find disk bloat in "{root}"',
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
        # Read-result truth (v6 storage-gate path-words, served): a disk read that STAGED a
        # cleanup approval is `tool_preview` in substance -- its actionable outcome is the
        # approval it holds, and painting it as a finished read would hide that approval. The
        # scan's own text and the pending action still ride the same response.
        self.assertEqual(result["mode"], "tool_preview")
        self.assertIn("Safe temp cleanup preview", result["response"])
        self.assertGreaterEqual(_count_pending_actions(), 1)

    def test_operator_parser_does_not_confuse_workspace_path_with_disk_space_request(self) -> None:
        self.assertIsNone(
            parse_operator_action_intent(
                "Create a folder named alpha inside /tmp/openclaw_workspace_truth_live."
            )
        )

    def test_candidate_cleanup_roots_keeps_nested_target_over_parent_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            target = temp_root / "nested"
            target.mkdir()

            roots = operator_storage.candidate_cleanup_roots(
                str(target),
                env={"TEMP": str(temp_root), "TMP": str(temp_root)},
                gettempdir_fn=lambda: str(temp_root),
                is_temp_cleanup_path_fn=lambda _path: True,
                expandvars_fn=lambda value: value,
            )

        self.assertEqual([str(target.resolve())], [str(root.resolve()) for root in roots])

    def test_cleanup_temp_files_executes_and_promotes_learning_shard(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / "one.tmp").write_bytes(b"a" * 1024)
            (temp_root / "two.tmp").write_bytes(b"b" * 2048)
            nested = temp_root / "nested"
            nested.mkdir()
            (nested / "three.tmp").write_bytes(b"c" * 1024)
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                preview = agent.run_once(
                    f'find disk bloat in "{temp_root}"',
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
                before_shards = _count_learning_shards()
                result = agent.run_once(
                    "fuck it, clean all temp files",
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
                remaining_entries = list(temp_root.iterdir())
        # The disk read stages the cleanup approval (`tool_preview`, the v6 storage-gate
        # contract); the cleanup that follows is the mutating action under test.
        self.assertEqual(preview["mode"], "tool_preview")
        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Temp cleanup finished.", result["response"])
        self.assertEqual(before_shards + 1, _count_learning_shards())
        self.assertEqual(remaining_entries, [])

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT shard_id, share_scope
                FROM learning_shards
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        from core import policy_engine
        expected_scope = str(policy_engine.get("shards.default_share_scope", "local_only"))
        self.assertEqual(str(row["share_scope"]), expected_scope)

    def test_ask_mode_refuses_a_mutating_operator_action_and_writes_nothing(self) -> None:
        # Mode enforcement: Ask is read-only. A read (disk inspect) is allowed, but the mutating
        # cleanup is refused before dispatch -- honest message, and the temp files are NOT deleted.
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / "one.tmp").write_bytes(b"a" * 1024)
            (temp_root / "two.tmp").write_bytes(b"b" * 2048)
            ask_ctx = {"surface": "openclaw", "platform": "openclaw", "operating_mode": "ask"}
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                preview = agent.run_once(f'find disk bloat in "{temp_root}"', source_context=ask_ctx)
                result = agent.run_once("clean all temp files", source_context=ask_ctx)
                remaining = list(temp_root.iterdir())
        # The read still runs in Ask mode; staging the cleanup proposal makes it a preview
        # (the v6 storage-gate contract), but nothing was executed to stage it.
        self.assertEqual(preview["mode"], "tool_preview")
        # The mutation is refused with a read-only message, nothing executed, nothing deleted.
        self.assertEqual(result.get("route"), "mode_read_only")
        self.assertIn("read-only", result["response"].lower())
        self.assertEqual(result.get("mode"), "tool_failed")
        self.assertTrue(remaining)  # temp files untouched -- nothing was deleted

    def test_hive_scope_can_republish_existing_session_shard(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        session_id = "openclaw:test-hive-share"
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / "one.tmp").write_bytes(b"a" * 1024)
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                preview = agent.run_once(
                    f'find disk bloat in "{temp_root}"',
                    session_id_override=session_id,
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
                result = agent.run_once(
                    "fuck it, clean all temp files",
                    session_id_override=session_id,
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
        # The disk read stages the cleanup approval (`tool_preview` per the v6 storage-gate
        # contract); the cleanup itself is the mutation and, once run, is `tool_executed`.
        self.assertEqual(preview["mode"], "tool_preview")
        self.assertEqual(result["mode"], "tool_executed")

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT shard_id, share_scope
                FROM learning_shards
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        shard_id = str(row["shard_id"])
        from core import policy_engine
        expected_scope = str(policy_engine.get("shards.default_share_scope", "local_only"))
        self.assertEqual(str(row["share_scope"]), expected_scope)

        handled, response = maybe_handle_memory_command("shared pack", session_id=session_id)
        self.assertTrue(handled)
        self.assertIn("SHARED PACK", response)

        conn = get_connection()
        try:
            updated = conn.execute(
                "SELECT share_scope FROM learning_shards WHERE shard_id = ?",
                (shard_id,),
            ).fetchone()
        finally:
            conn.close()
        assert updated is not None
        self.assertEqual(str(updated["share_scope"]), "hive_mind")
        holders = holders_for_shard(shard_id)
        self.assertTrue(any(holder["access_mode"] == "hive_mind" for holder in holders))

    def test_list_tools_reports_discovered_inventory(self) -> None:
        # `operator.list_tools` is now THE tool navigator: the real catalog by
        # family, availability, reason and count — not eight operator-only rows.
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        result = agent.run_once(
            "list tools",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertEqual(result["mode"], "tool_executed")
        response = result["response"]
        self.assertIn("Tool families on this runtime:", response)
        self.assertIn("- operator: 20 available", response)  # 7 legacy + calendar/notes vertical + its read kinds declared 2026-09-15
        self.assertIn("- workspace:", response)
        self.assertIn("capability.expand_family", response)
        # Disabled lanes stay visible as unavailable metadata with their reason. The per-tool
        # lines are sampled per family, so the stable pin is the family's own unavailable count
        # plus the send flag its reason names.
        self.assertIn("- email: 0 available", response)
        self.assertIn("(set email.send_enabled)", response)

    def test_inspect_processes_reports_top_rows(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        stdout = (
            '"python.exe","10","Console","1","1,024 K"\n'
            '"chrome.exe","99","Console","1","2,048 K"\n'
            if os.name == "nt"
            else "  10 15.0  2.5 python\n  99  4.0 10.0 chrome\n"
        )
        fake_completed = mock.Mock(
            returncode=0,
            stdout=stdout,
        )
        with mock.patch("core.local_operator_actions.subprocess.run", return_value=fake_completed):
            result = agent.run_once(
                "what processes are eating memory",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Top running processes", result["response"])
        self.assertIn("chrome", result["response"])

    def test_inspect_services_reports_rows(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        fake_rows = [
            {"name": "ssh.service", "state": "running", "detail": "OpenSSH server daemon"},
            {"name": "cron.service", "state": "running", "detail": "Regular background program processing daemon"},
        ]
        with mock.patch("core.local_operator_actions._inspect_services", return_value=fake_rows):
            result = agent.run_once(
                "what services are running",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Visible services", result["response"])
        self.assertIn("ssh.service", result["response"])

    def test_move_path_executes_and_promotes_learning_shard(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source_dir = base / "source"
            destination_dir = base / "archive"
            source_dir.mkdir()
            destination_dir.mkdir()
            source_file = source_dir / "report.txt"
            source_file.write_text("hello", encoding="utf-8")

            preview = agent.run_once(
                f'move "{source_file}" to "{destination_dir}"',
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            action_id = preview["response"].strip().split()[-1]
            before_shards = _count_learning_shards()
            result = agent.run_once(
                f"approve move {action_id}",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            moved_file = destination_dir / "report.txt"
            moved_exists = moved_file.exists()
            source_exists = source_file.exists()

        self.assertEqual(preview["mode"], "tool_preview")
        self.assertIn("Move preview ready.", preview["response"])
        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Move finished.", result["response"])
        self.assertEqual(before_shards + 1, _count_learning_shards())
        self.assertFalse(source_exists)
        self.assertTrue(moved_exists)

    def test_schedule_calendar_event_executes_without_micro_approval_by_default(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            def _fake_data_path(*parts: str) -> Path:
                return base.joinpath(*parts)

            with mock.patch("core.local_operator_actions.data_path", side_effect=_fake_data_path):
                before_shards = _count_learning_shards()
                result = agent.run_once(
                    'schedule a meeting "Ops Sync" on 2026-03-08 15:30 for 45m',
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
                outbox = base / "calendar_outbox"
                files = list(outbox.glob("*.ics"))
                ics_content = files[0].read_text(encoding="utf-8") if files else ""

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Calendar event created.", result["response"])
        self.assertNotIn("Workflow:", result["response"])
        self.assertEqual(before_shards + 1, _count_learning_shards())
        self.assertEqual(len(files), 1)
        self.assertIn("SUMMARY:Ops Sync", ics_content)

    def test_execution_gate_exposes_command_evaluator(self) -> None:
        # R2b1: with no turn ledger active the door denies with a typed reason;
        # the evaluator itself is exercised inside a turn scope, as in production.
        from core.remote_fetch_policy import remote_fetch_policy_scope

        unscoped = ExecutionGate.evaluate_command("ls")
        self.assertEqual(unscoped["decision"], "blocked")
        self.assertIn("no active turn ledger", unscoped["reason"])
        with remote_fetch_policy_scope({"surface": "openclaw"}):
            decision = ExecutionGate.evaluate_command("ls")
        self.assertIn(decision["decision"], {"simulate_only", "sandbox", "advice_only"})
        self.assertIn("reason", decision)

    def test_autonomy_modes_change_calendar_approval_requirements(self) -> None:
        maybe_handle_preference_command("set autonomy balanced")
        decision = ExecutionGate.evaluate_local_action(
            "schedule_calendar_event",
            destructive=True,
            user_approved=False,
            writes_workspace=True,
        )
        self.assertTrue(decision.requires_user_approval)

        maybe_handle_preference_command("set autonomy hands_off")
        decision = ExecutionGate.evaluate_local_action(
            "schedule_calendar_event",
            destructive=True,
            user_approved=False,
            writes_workspace=True,
        )
        self.assertFalse(decision.requires_user_approval)

    def test_tool_list_contains_new_operator_surfaces(self) -> None:
        tool_ids = {tool["tool_id"] for tool in list_operator_tools()}
        self.assertIn("inspect_processes", tool_ids)
        self.assertIn("inspect_services", tool_ids)
        self.assertIn("move_path", tool_ids)
        self.assertIn("schedule_calendar_event", tool_ids)

    def test_operator_capability_ledger_marks_outward_and_privacy_sensitive_actions(self) -> None:
        ledger = {entry["capability_id"]: entry for entry in operator_capability_ledger()}
        self.assertTrue(ledger["operator.discord_post"]["outward_facing"])
        self.assertTrue(ledger["operator.discord_post"]["privacy_sensitive"])
        self.assertTrue(ledger["operator.discord_post"]["requires_approval"])
        self.assertFalse(ledger["operator.inspect_disk_usage"]["outward_facing"])
        self.assertFalse(ledger["operator.inspect_disk_usage"]["privacy_sensitive"])
        self.assertEqual(ledger["operator.schedule_calendar_event"]["support_level"], "partial")
        self.assertIn("local .ics event", ledger["operator.schedule_calendar_event"]["partial_reason"].lower())

    def test_execution_gate_exposes_local_action_guardrails(self) -> None:
        cleanup = ExecutionGate.local_action_guardrails("cleanup_temp_files", destructive=True)
        outbound = ExecutionGate.local_action_guardrails("discord_post", destructive=True)
        inspect = ExecutionGate.local_action_guardrails("inspect_disk_usage", destructive=False)

        self.assertTrue(cleanup["destructive"])
        self.assertFalse(cleanup["outward_facing"])
        self.assertFalse(cleanup["privacy_sensitive"])
        self.assertTrue(outbound["destructive"])
        self.assertTrue(outbound["outward_facing"])
        self.assertTrue(outbound["privacy_sensitive"])
        self.assertFalse(inspect["destructive"])
        self.assertFalse(inspect["outward_facing"])
        self.assertFalse(inspect["privacy_sensitive"])

    def test_execution_gate_keeps_outward_privacy_actions_hard_gated(self) -> None:
        decision = ExecutionGate.evaluate_local_action(
            "discord_post",
            destructive=True,
            user_approved=False,
        )
        self.assertEqual(decision.mode, "blocked")
        self.assertIn("not in the allowed local action set", decision.reason.lower())


if __name__ == "__main__":
    unittest.main()
