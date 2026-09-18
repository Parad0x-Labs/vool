from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from core.adaptation_dataset import build_adaptation_corpus
from core.lora_training_pipeline import promote_adaptation_job, run_adaptation_job
from core.model_registry import ModelRegistry
from storage.adaptation_store import (
    create_adaptation_corpus,
    create_adaptation_job,
    list_adaptation_job_events,
    update_adaptation_job,
)
from storage.brain_hive_store import create_post, create_topic
from storage.db import get_connection
from storage.migrations import run_migrations

_TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None


class AdaptationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "adaptation_job_events",
                "adaptation_jobs",
                "adaptation_corpora",
                "model_provider_manifests",
                "finalized_responses",
                "hive_posts",
                "hive_topic_claims",
                "hive_topics",
                "local_tasks",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    def test_build_adaptation_corpus_collects_chat_final_and_hive_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            convo = Path(tmpdir) / "conversation.jsonl"
            convo.write_text(
                json.dumps(
                    {
                        "session_id": "openclaw:test",
                        "user": "Explain how VOOL should coordinate Hive task routing.",
                        "assistant": "VOOL should claim the task, run bounded research, and post the result back to Hive.",
                        "share_scope": "local_only",
                        "ts": "2026-03-10T08:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            topic_id = create_topic(
                created_by_agent_id="agent:test",
                title="Agent Commons",
                summary="Shared watcher and task flow UX",
                topic_tags=["ux"],
                status="researching",
                visibility="agent_public",
                evidence_mode="candidate_only",
                linked_task_id=None,
            )
            create_post(
                topic_id=topic_id,
                author_agent_id="agent:test",
                post_kind="analysis",
                stance="propose",
                body="Use a trace view so the operator can see claim, query, artifact, and result state.",
                evidence_refs=[],
            )
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO local_tasks (
                        task_id, session_id, task_class, task_summary, redacted_input_hash,
                        environment_os, environment_shell, environment_runtime, environment_version_hint,
                        plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
                    ) VALUES (
                        'task-final', 'openclaw:test', 'research', 'Summarize Hive trace UX improvements', 'hash',
                        'macOS', 'zsh', 'python', '3.9',
                        'default', 'local_only', 0.9, 'success', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO finalized_responses (
                        parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score
                    ) VALUES (
                        'task-final', 'raw', 'Show the live ladder: claim, bounded queries, artifacts, result.', 'success', 0.9
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            corpus = create_adaptation_corpus(label="mixed-corpus")
            with mock.patch("core.adaptation_dataset.conversation_log_path", return_value=convo), mock.patch(
                "core.adaptation_dataset.ensure_memory_files", return_value=None
            ):
                result = build_adaptation_corpus(corpus["corpus_id"])

            self.assertEqual(result.corpus_id, corpus["corpus_id"])
            self.assertGreaterEqual(result.example_count, 3)
            rows = [json.loads(line) for line in Path(result.output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
            sources = {row["source"] for row in rows}
            self.assertIn("conversation", sources)
            self.assertIn("final_response", sources)
            self.assertIn("hive_post", sources)

    @pytest.mark.skipif(not _TORCH_AVAILABLE, reason="torch not installed")
    def test_run_adaptation_job_fails_cleanly_when_base_model_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir) / "corpus.jsonl"
            corpus_path.write_text(
                json.dumps(
                    {
                        "instruction": "Explain swarm task routing.",
                        "output": "Claim work, run bounded queries, and submit the result.",
                        "source": "conversation",
                        "metadata": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            corpus = create_adaptation_corpus(label="ready", output_path=str(corpus_path))
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE adaptation_corpora SET example_count = 1, latest_build_at = CURRENT_TIMESTAMP WHERE corpus_id = ?",
                    (corpus["corpus_id"],),
                )
                conn.commit()
            finally:
                conn.close()
            job = create_adaptation_job(corpus_id=corpus["corpus_id"], base_model_ref=str(Path(tmpdir) / "missing-base-model"))
            result = run_adaptation_job(job["job_id"])
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["error_text"])
            events = list_adaptation_job_events(job["job_id"])
            self.assertTrue(any(item["event_type"] == "job_failed" for item in events))

    def test_promote_adaptation_job_registers_enabled_provider_manifest(self) -> None:
        corpus = create_adaptation_corpus(label="promotion-corpus")
        job = create_adaptation_job(
            corpus_id=corpus["corpus_id"],
            base_model_ref="/tmp/base-model",
            adapter_provider_name="vool-adapted",
            adapter_model_name="qwen-lora-test",
        )
        update_adaptation_job(
            job["job_id"],
            status="completed",
            registered_manifest={
                "provider_name": "vool-adapted",
                "model_name": "qwen-lora-test",
                "source_type": "local_path",
                "adapter_type": "peft_lora_adapter",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "runtime_dependency": "transformers+peft",
                "capabilities": ["summarize", "classify"],
                "runtime_config": {
                    "base_model_ref": "/tmp/base-model",
                    "adapter_path": "/tmp/adapter-model",
                },
                "metadata": {"adaptation_promoted": False},
                "enabled": False,
            },
        )
        promoted = promote_adaptation_job(job["job_id"])
        manifest = ModelRegistry().get_manifest("vool-adapted", "qwen-lora-test")
        self.assertIsNotNone(manifest)
        self.assertTrue(manifest.enabled)
        self.assertEqual(promoted["status"], "promoted")


if __name__ == "__main__":
    unittest.main()
