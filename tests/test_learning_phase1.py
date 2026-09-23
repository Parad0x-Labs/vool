from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.learning import (
    LearningPolicy,
    load_procedure_shards,
    promote_verified_procedure,
    rank_reusable_procedures,
    record_procedure_reuse,
    summarize_procedure_metrics,
)
from core.learning.procedure_shards import ProcedureShardV1
from core.runtime_execution_tools import execute_runtime_tool


class LearningPhase1Tests(unittest.TestCase):
    def test_promote_verified_procedure_persists_local_shard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.learning.procedure_shards.data_path", return_value=Path(tmpdir)):
            shard = promote_verified_procedure(
                task_class="coding_operator",
                title="Patch and verify a repo file",
                preconditions=["workspace is writable"],
                steps=["read file", "apply diff", "run tests"],
                tool_receipts=[{"intent": "workspace.apply_unified_diff"}],
                validation={"ok": True, "tool": "workspace.run_tests"},
                rollback={"intent": "workspace.rollback_last_change"},
            )

            assert shard is not None
            loaded = load_procedure_shards()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].title, "Patch and verify a repo file")

    def test_promote_verified_procedure_refuses_unverified_validation(self) -> None:
        shard = promote_verified_procedure(
            task_class="coding_operator",
            title="Unsafe shard",
            preconditions=[],
            steps=["do thing"],
            tool_receipts=[],
            validation={"ok": False},
            rollback={},
        )

        self.assertIsNone(shard)

    def test_rank_reusable_procedures_prefers_matching_task_class(self) -> None:
        matching = ProcedureShardV1.create(
            task_class="coding_operator",
            title="Patch and verify Python tests",
            preconditions=[],
            steps=["apply diff", "run tests"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
            status=LearningPolicy.STATUS_PROMOTED,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )
        non_matching = ProcedureShardV1.create(
            task_class="research",
            title="Research and summarize",
            preconditions=[],
            steps=["search web"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="trusted_hive",
            success_signal="verified_success",
        )

        ranked = rank_reusable_procedures(
            task_class="coding_operator",
            query_text="patch and run tests",
            procedures=[non_matching, matching],
        )
        self.assertEqual(ranked[0].procedure_id, matching.procedure_id)

    def test_rank_reusable_procedures_prefers_verified_reuse_when_text_overlap_is_similar(self) -> None:
        fresh = ProcedureShardV1.create(
            task_class="coding_operator",
            title="Patch Python code and run tests",
            preconditions=[],
            steps=["apply diff", "run tests"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
            status=LearningPolicy.STATUS_PROMOTED,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )
        proven = ProcedureShardV1.create(
            task_class="coding_operator",
            title="Patch Python code and run tests",
            preconditions=[],
            steps=["apply diff", "run tests"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
            reuse_count=3,
            verified_reuse_count=2,
            status=LearningPolicy.STATUS_PROMOTED,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )

        ranked = rank_reusable_procedures(
            task_class="coding_operator",
            query_text="patch and run tests",
            procedures=[fresh, proven],
        )
        self.assertEqual(ranked[0].procedure_id, proven.procedure_id)

    def test_summarize_procedure_metrics_counts_shareability(self) -> None:
        procedures = [
            ProcedureShardV1.create(
                task_class="coding_operator",
                title="Local one",
                preconditions=[],
                steps=["run tests"],
                tool_receipts=[],
                validation={"ok": True},
                rollback={},
                privacy_class="local_private",
                shareability="local_only",
                success_signal="verified_success",
            ),
            ProcedureShardV1.create(
                task_class="coding_operator",
                title="Hive one",
                preconditions=[],
                steps=["run tests"],
                tool_receipts=[],
                validation={"ok": True},
                rollback={},
                privacy_class="policy_cleared",
                shareability="trusted_hive",
                success_signal="verified_success",
            ),
        ]

        summary = summarize_procedure_metrics(procedures)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["local_only"], 1)
        self.assertEqual(summary["trusted_hive"], 1)
        self.assertEqual(summary["reused"], 0)
        self.assertEqual(summary["total_reuse_count"], 0)
        self.assertEqual(summary["verified_reuse_count"], 0)

    def test_record_procedure_reuse_updates_persisted_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.learning.procedure_shards.data_path", return_value=Path(tmpdir)):
            shard = promote_verified_procedure(
                task_class="coding_operator",
                title="Patch and verify a repo file",
                preconditions=["workspace is writable"],
                steps=["read file", "apply diff", "run tests"],
                tool_receipts=[{"intent": "workspace.apply_unified_diff"}],
                validation={"ok": True, "tool": "workspace.run_tests"},
                rollback={"intent": "workspace.rollback_last_change"},
            )

            assert shard is not None
            updated = record_procedure_reuse(
                procedure_ids=[shard.procedure_id],
                task_class="debugging",
                verified=True,
                outcome="completed",
            )

            self.assertEqual(len(updated), 1)
            self.assertEqual(updated[0].reuse_count, 1)
            self.assertEqual(updated[0].verified_reuse_count, 1)
            self.assertEqual(updated[0].last_reuse_task_class, "debugging")
            # verified=True is a successful terminal: the label derives from the outcome TYPE,
            # not from the caller's echo of "completed".
            self.assertEqual(updated[0].last_reuse_outcome, "verified_success")

            loaded = load_procedure_shards()
            self.assertEqual(loaded[0].reuse_count, 1)
            self.assertEqual(loaded[0].verified_reuse_count, 1)

    def test_validation_success_promotes_procedure_from_tracked_mutation_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "def test_truth():\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('goodbye')",
                    "",
                ]
            )

            def _data_path(*parts: str) -> Path:
                path = Path(tmpdir) / "data"
                for part in parts:
                    path /= str(part)
                return path

            with mock.patch("core.execution.artifacts.data_path", side_effect=_data_path), mock.patch(
                "core.learning.procedure_shards.data_path",
                side_effect=_data_path,
            ):
                context = {
                    "workspace": str(workspace), "session_id": "learning-1",
                    "task_class": "debugging", "operating_mode": "auto",
                }
                applied = execute_runtime_tool(
                    "workspace.apply_unified_diff",
                    {"patch": patch_text},
                    source_context=context,
                )
                assert applied is not None
                self.assertTrue(applied.ok)

                validated = execute_runtime_tool(
                    "workspace.run_tests",
                    {"command": "python3 -m pytest -q test_app.py"},
                    source_context=context,
                )
                assert validated is not None
                self.assertTrue(validated.ok, validated)
                self.assertEqual(validated.details["procedure_shard"]["task_class"], "debugging")

                validated_again = execute_runtime_tool(
                    "workspace.run_tests",
                    {"command": "python3 -m pytest -q test_app.py"},
                    source_context=context,
                )
                assert validated_again is not None
                self.assertTrue(validated_again.ok)
                self.assertNotIn("procedure_shard", validated_again.details)

                loaded = load_procedure_shards()
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].task_class, "debugging")


if __name__ == "__main__":
    unittest.main()
