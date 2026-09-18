from __future__ import annotations

import json
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest import mock

from apps.vool_agent import VoolAgent
from core.active_mission import current_active_mission_slots
from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.final_response_store import store_final_response
from core.human_input_adapter import HumanInputInterpretation, adapt_user_input
from core.identity_manager import load_active_persona
from core.knowledge_registry import record_remote_holder
from core.persistent_memory import (
    append_conversation_event,
    conversation_log_path,
    memory_entries_path,
    memory_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.runtime_continuity import (
    create_runtime_checkpoint,
    store_tool_receipt,
    update_runtime_checkpoint,
)
from core.task_router import classify, create_task_record
from core.tiered_context_loader import (
    TieredContextLoader,
    _current_chat_corrections,
    _requests_adjacent_tool_state,
)
from network.signer import get_local_peer_id
from storage.context_access_log import recent_context_access
from storage.db import get_connection
from storage.dialogue_memory import record_dialogue_turn
from storage.migrations import run_migrations
from storage.shard_fetch_receipts import record_fetch_receipt
from storage.shard_reuse_outcomes import record_shard_reuse_outcomes
from storage.swarm_memory import save_sniffed_context


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interpretation(text: str, *, confidence: float = 0.72, topics: list[str] | None = None) -> HumanInputInterpretation:
    return HumanInputInterpretation(
        raw_text=text,
        normalized_text=text,
        reconstructed_text=text,
        intent_mode="request",
        topic_hints=list(topics or []),
        reference_targets=[],
        understanding_confidence=confidence,
        quality_flags=[],
        needs_clarification=confidence < 0.45,
        turn_id=None,
    )


class TieredContextLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            existing_tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            for table in (
                "local_tasks",
                "learning_shards",
                "finalized_responses",
                "dialogue_sessions",
                "dialogue_turns",
                "adaptive_lexicon",
                "knowledge_manifests",
                "knowledge_holders",
                "presence_leases",
                "index_deltas",
                "payment_status_markers",
                "context_access_log",
                "shard_fetch_receipts",
                "shard_reuse_outcomes",
                "sniffed_context",
                "runtime_tool_receipts",
                "runtime_checkpoints",
                "runtime_session_events",
                "runtime_sessions",
                "active_mission_slots",
            ):
                if table in existing_tables:
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        for path in (memory_path(), conversation_log_path(), memory_entries_path(), session_summaries_path(), user_heuristics_path()):
            if path.exists():
                path.unlink()
        self.loader = TieredContextLoader()
        self.persona = load_active_persona("default")
        self.session_id = f"ctx-{uuid.uuid4().hex}"
        ensure_chat_namespace(
            self.session_id,
            grant_confirmed_profile=True,
        )

    def _grant_shared_context(self) -> None:
        grant_context_import(
            self.session_id,
            scope="chat",
            source_id="shared:swarm",
        )

    def _grant_cold_context(self) -> None:
        grant_context_import(
            self.session_id,
            scope="chat",
            source_id=f"cold:{self.session_id}",
        )

    def test_tool_state_followup_gate_neutralizes_quoted_fenced_and_json_literals(self) -> None:
        for text in (
            'The report says "Run the tests."',
            "```sh\nrun the tests\n```\nExplain this sample.",
            '{"instruction":"Open that file."}',
            "The literal command is `Try again.`",
        ):
            with self.subTest(text=text):
                self.assertFalse(_requests_adjacent_tool_state(text))
        for text in ("Run the tests.", "Open that file.", "Try again.", "Revert that change."):
            with self.subTest(text=text):
                self.assertTrue(_requests_adjacent_tool_state(text))

    def test_recent_chat_correction_is_reused_for_later_retrieval(self) -> None:
        with mock.patch(
            "core.tiered_context_loader.recent_dialogue_turns",
            return_value=[
                {
                    "normalized_input": (
                        "I changed my mind: I prefer a compact notebook after all."
                    )
                }
            ],
        ):
            corrections = _current_chat_corrections(
                self.session_id,
                current_user_text="What is my current preference?",
            )

        self.assertEqual(
            [(correction.fact_key, correction.value) for correction in corrections],
            [("preference", "a compact notebook after all")],
        )

    def _store_action_receipt(
        self,
        *,
        receipt_id: str,
        checkpoint_id: str,
        summary: str,
        project_id: str = "",
    ) -> None:
        store_tool_receipt(
            receipt_key=receipt_id,
            session_id=self.session_id,
            checkpoint_id=checkpoint_id,
            tool_name="web.search",
            idempotency_key=f"action:{receipt_id}",
            arguments={},
            execution={
                "action_record": {
                    "receipt_id": receipt_id,
                    "origin": {
                        "chat_id": self.session_id,
                        "project_id": project_id,
                    },
                    "result": {"summary": summary},
                }
            },
        )

    def _insert_local_shard(
        self,
        *,
        problem_class: str,
        summary: str,
        resolution_pattern: list[str] | None = None,
        origin_session_id: str | None = None,
        share_scope: str = "local_only",
        source_type: str = "local_generated",
        source_node_id: str | None = None,
    ) -> str:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_session_id, share_scope, created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 0.92, 0.81, 0, 0, 'active', '[]', ?, NULL, '', ?, ?, ?, ?)
                """,
                (
                    shard_id,
                    problem_class,
                    f"sig-{uuid.uuid4().hex}",
                    summary,
                    json.dumps(resolution_pattern or ["review_problem", "choose_safe_next_step"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    source_type,
                    source_node_id or get_local_peer_id(),
                    now,
                    self.session_id if origin_session_id is None else origin_session_id,
                    share_scope,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return shard_id

    def test_bootstrap_only_path(self) -> None:
        task = create_task_record("quick check status")
        interpretation = _interpretation("quick check status", confidence=0.78, topics=["status"])
        result = self.loader.load(
            task=task,
            classification=classify("quick check status", context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.bootstrap_items)
        self.assertFalse(result.relevant_items)
        self.assertEqual(result.report.cold_tokens_used, 0)
        self.assertGreater(result.report.bootstrap_tokens_used, 0)

    def test_relevant_retrieval_under_budget(self) -> None:
        self._grant_shared_context()
        self._insert_local_shard(
            problem_class="security_hardening",
            summary="Harden local credentials and prevent password leaks from Telegram automation.",
            resolution_pattern=["identify_sensitive_surfaces", "remove_secret_exposure_paths"],
        )
        task = create_task_record("harden local credentials so passwords never leak")
        interpretation = _interpretation("harden local credentials so passwords never leak", topics=["security hardening", "password leak"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(any(item.source_type == "local_shard" for item in result.relevant_items))
        self.assertLessEqual(result.report.relevant_tokens_used, result.report.relevant_budget)

    def test_learning_shards_exclude_other_chat_shared_and_legacy_rows_before_ranking(self) -> None:
        current_marker = f"current-private-{uuid.uuid4().hex}"
        other_marker = f"other-private-{uuid.uuid4().hex}"
        shared_marker = f"shared-without-grant-{uuid.uuid4().hex}"
        legacy_marker = f"legacy-unscoped-{uuid.uuid4().hex}"
        current_id = self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {current_marker}",
        )
        self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {other_marker}",
            origin_session_id=f"other-chat-{uuid.uuid4().hex}",
        )
        self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {shared_marker}",
            origin_session_id=f"origin-chat-{uuid.uuid4().hex}",
            share_scope="hive_mind",
        )
        self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {legacy_marker}",
            origin_session_id="",
        )

        task = create_task_record("harden local credentials")
        interpretation = _interpretation(
            "harden local credentials",
            topics=["security hardening"],
        )
        with mock.patch(
            "core.tiered_context_loader.rank",
            side_effect=lambda candidates, _task: list(candidates),
        ) as rank_shards, mock.patch(
            "core.shard_matcher.latest_receipts_for_shards",
        ) as receipt_lookup, mock.patch(
            "core.shard_matcher.summarize_reuse_outcomes_for_shards",
        ) as matcher_reuse_lookup, mock.patch(
            "core.tiered_context_loader.summarize_reuse_outcomes_for_shards",
        ) as loader_reuse_lookup:
            result = self.loader.load(
                task=task,
                classification=classify(
                    task.task_summary,
                    context=interpretation.as_context(),
                ),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )

        fetched_ids = {
            str(candidate.get("shard_id") or "")
            for candidate in rank_shards.call_args.args[0]
        }
        self.assertEqual(fetched_ids, {current_id})
        self.assertEqual(
            {str(candidate.get("shard_id") or "") for candidate in result.local_candidates},
            {current_id},
        )
        rendered = result.assembled_context()
        self.assertIn(current_marker, rendered)
        self.assertNotIn(other_marker, rendered)
        self.assertNotIn(shared_marker, rendered)
        self.assertNotIn(legacy_marker, rendered)
        receipt_lookup.assert_not_called()
        matcher_reuse_lookup.assert_not_called()
        loader_reuse_lookup.assert_not_called()

    def test_learning_shards_query_shared_rows_only_with_explicit_grant(self) -> None:
        current_marker = f"current-with-shared-{uuid.uuid4().hex}"
        other_marker = f"other-with-shared-{uuid.uuid4().hex}"
        shared_marker = f"shared-granted-{uuid.uuid4().hex}"
        current_id = self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {current_marker}",
        )
        self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {other_marker}",
            origin_session_id=f"other-chat-{uuid.uuid4().hex}",
        )
        shared_id = self._insert_local_shard(
            problem_class="security_hardening",
            summary=f"Credential hardening evidence {shared_marker}",
            origin_session_id="",
            share_scope="public_knowledge",
            source_type="peer_received",
            source_node_id=f"peer-{uuid.uuid4().hex}",
        )
        self._grant_shared_context()

        task = create_task_record("harden local credentials")
        interpretation = _interpretation(
            "harden local credentials",
            topics=["security hardening"],
        )
        with mock.patch(
            "core.tiered_context_loader.rank",
            side_effect=lambda candidates, _task: list(candidates),
        ) as rank_shards, mock.patch(
            "core.shard_matcher.latest_receipts_for_shards",
            return_value={},
        ) as receipt_lookup, mock.patch(
            "core.shard_matcher.summarize_reuse_outcomes_for_shards",
            return_value={},
        ) as matcher_reuse_lookup, mock.patch(
            "core.tiered_context_loader.summarize_reuse_outcomes_for_shards",
            return_value={},
        ) as loader_reuse_lookup:
            result = self.loader.load(
                task=task,
                classification=classify(
                    task.task_summary,
                    context=interpretation.as_context(),
                ),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )

        fetched_ids = {
            str(candidate.get("shard_id") or "")
            for candidate in rank_shards.call_args.args[0]
        }
        self.assertEqual(fetched_ids, {current_id, shared_id})
        self.assertEqual(receipt_lookup.call_args.args[0], [shared_id])
        self.assertEqual(matcher_reuse_lookup.call_args.args[0], [shared_id])
        self.assertEqual(loader_reuse_lookup.call_args.args[0], [shared_id])
        rendered = result.assembled_context()
        self.assertIn(current_marker, rendered)
        self.assertIn(shared_marker, rendered)
        self.assertNotIn(other_marker, rendered)
        metadata_by_id = {
            str(candidate.get("shard_id") or ""): candidate
            for candidate in result.local_candidates
        }
        self.assertEqual(
            str(metadata_by_id[current_id].get("origin_session_id") or ""),
            self.session_id,
        )
        self.assertEqual(
            str(metadata_by_id[shared_id].get("share_scope") or ""),
            "public_knowledge",
        )
        current_item = next(
            item
            for item in result.relevant_items
            if str(item.metadata.get("shard_id") or "") == current_id
        )
        shared_item = next(
            item
            for item in result.relevant_items
            if str(item.metadata.get("shard_id") or "") == shared_id
        )
        self.assertEqual(current_item.metadata["origin_chat_id"], self.session_id)
        self.assertEqual(shared_item.metadata["origin_chat_id"], "shared:swarm")

    def test_relevant_retrieval_includes_structured_runtime_tool_observation(self) -> None:
        prior = adapt_user_input("latest qwen release notes", session_id=self.session_id)
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="latest qwen release notes",
            source_context={
                "runtime_session_id": self.session_id,
                "surface": "openclaw",
                "platform": "openclaw",
                "_canonical_user_turn_id": prior.turn_id,
            },
        )
        self._store_action_receipt(
            receipt_id="tool-receipt-qwen-1",
            checkpoint_id=checkpoint["checkpoint_id"],
            summary="web.search: Found Qwen release notes.",
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "executed_steps": [
                    {
                        "tool_name": "web.search",
                        "status": "executed",
                        "summary": "Found Qwen release notes.",
                    }
                ],
                "last_tool_response": {
                    "handled": True,
                    "ok": True,
                    "status": "executed",
                    "response_text": 'Search results for "latest qwen release notes": ...',
                    "tool_name": "web.search",
                    "receipt": {
                        "receipt_id": "tool-receipt-qwen-1",
                        "safe_summary": "web.search: Found Qwen release notes.",
                    },
                    "details": {
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "web.search",
                            "tool_surface": "web",
                            "ok": True,
                            "status": "executed",
                            "query": "latest qwen release notes",
                            "results": [
                                {
                                    "title": "Qwen release notes",
                                    "url": "https://example.test/qwen",
                                    "snippet": "Fresh update summary",
                                }
                            ],
                        }
                    },
                },
            },
            status="completed",
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input="latest qwen release notes",
            assistant_output="I found the latest Qwen release notes.",
            source_context={},
        )
        interpretation = adapt_user_input("Open that result.", session_id=self.session_id)
        task = create_task_record(interpretation.normalized_text)
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        tool_items = [item for item in result.relevant_items if item.source_type == "tool_observation"]
        self.assertTrue(tool_items)
        self.assertEqual(tool_items[0].metadata["parent_user_turn_id"], prior.turn_id)
        self.assertIn('"receipt_id": "tool-receipt-qwen-1"', result.assembled_context())
        self.assertIn('"safe_summary": "web.search: Found Qwen release notes."', result.assembled_context())
        self.assertNotIn('"query": "latest qwen release notes"', result.assembled_context())
        self.assertNotIn("Real tool result from", result.assembled_context())

    def test_forced_true_unrelated_turn_cannot_revive_stale_tool_or_mission_state(self) -> None:
        database_request = "Inspect the database migration with budget 0.05 SOL."
        database_turn = adapt_user_input(database_request, session_id=self.session_id)
        self.assertTrue(current_active_mission_slots(self.session_id))
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text=database_request,
            source_context={
                "runtime_session_id": self.session_id,
                "_canonical_user_turn_id": database_turn.turn_id,
            },
        )
        self._store_action_receipt(
            receipt_id="tool-receipt-stale-database",
            checkpoint_id=checkpoint["checkpoint_id"],
            summary="database: stale migration result",
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "last_tool_response": {
                    "tool_name": "database.inspect",
                    "receipt": {"receipt_id": "tool-receipt-stale-database"},
                }
            },
            status="completed",
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input=database_request,
            assistant_output="The database migration completed.",
            source_context={},
        )
        adapt_user_input("Write a haiku about rain.", session_id=self.session_id)
        append_conversation_event(
            session_id=self.session_id,
            user_input="Write a haiku about rain.",
            assistant_output="Rain taps on the glass.",
            source_context={},
        )
        unrelated = adapt_user_input("Explain photosynthesis.", session_id=self.session_id)
        self.assertFalse(unrelated.is_continuation)
        mutated = replace(unrelated, is_continuation=True)
        task = create_task_record(mutated.normalized_text)

        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=mutated.as_context()),
            interpretation=mutated,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        self.assertFalse(
            any(item.source_type == "tool_observation" for item in result.relevant_items)
        )
        self.assertFalse(
            any(item.source_type == "active_mission" for item in result.bootstrap_items)
        )
        self.assertNotIn("tool-receipt-stale-database", result.assembled_context())

    def test_forced_true_unrelated_turn_cannot_authorize_imported_session_summary(self) -> None:
        source_session = f"ctx-imported-summary-{uuid.uuid4().hex}"
        ensure_chat_namespace(source_session)
        grant_context_import(
            self.session_id,
            scope="chat",
            source_id=f"chat:{source_session}",
        )
        stale_marker = "STALE-IMPORTED-DATABASE-SUMMARY-915"
        adapt_user_input("Write a haiku about rain.", session_id=self.session_id)
        append_conversation_event(
            session_id=self.session_id,
            user_input="Write a haiku about rain.",
            assistant_output="Rain taps on the glass.",
            source_context={},
        )
        unrelated = adapt_user_input("Explain photosynthesis.", session_id=self.session_id)
        self.assertFalse(unrelated.is_continuation)
        mutated = replace(unrelated, is_continuation=True)
        task = create_task_record(mutated.normalized_text)

        with mock.patch(
            "core.tiered_context_loader.search_session_summaries",
            return_value=[
                {
                    "session_id": source_session,
                    "project_id": "",
                    "summary": stale_marker,
                    "score": 1.0,
                    "status": "active",
                }
            ],
        ) as summary_loader:
            result = self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=mutated.as_context()),
                interpretation=mutated,
                persona=self.persona,
                session_id=self.session_id,
                total_context_budget=5000,
            )

        summary_loader.assert_not_called()
        self.assertEqual(
            result.report.access_policy["source_authority"]["session_summary"],
            "relevance_denied",
        )
        self.assertNotIn(stale_marker, result.assembled_context())

    def test_forced_true_unrelated_turn_does_not_enable_other_non_transcript_sources(self) -> None:
        self._grant_shared_context()
        self._grant_cold_context()
        unrelated = adapt_user_input("Explain photosynthesis.", session_id=self.session_id)
        self.assertFalse(unrelated.is_continuation)
        mutated = replace(unrelated, is_continuation=True)
        task = create_task_record(mutated.normalized_text)

        with (
            mock.patch(
                "core.tiered_context_loader._user_heuristic_items",
                return_value=[],
            ) as heuristics,
            mock.patch(
                "core.tiered_context_loader._shared_swarm_context_items",
                side_effect=AssertionError("continuation enabled shared context"),
            ) as shared_context,
            mock.patch(
                "core.tiered_context_loader._swarm_metadata_items",
                side_effect=AssertionError("continuation enabled swarm metadata"),
            ) as swarm_metadata,
            mock.patch(
                "core.tiered_context_loader._cold_context_items",
                side_effect=AssertionError("continuation enabled cold context"),
            ) as cold_context,
        ):
            result = self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=mutated.as_context()),
                interpretation=mutated,
                persona=self.persona,
                session_id=self.session_id,
                total_context_budget=5000,
            )

        heuristics.assert_called_once()
        shared_context.assert_not_called()
        swarm_metadata.assert_not_called()
        cold_context.assert_not_called()
        source_authority = result.report.access_policy["source_authority"]
        self.assertEqual(source_authority["user_heuristic"], "relevance_denied")
        self.assertEqual(source_authority["shared_context"], "relevance_denied")
        self.assertEqual(source_authority["cold_context"], "relevance_denied")

    def test_intervening_exchange_breaks_implicit_tool_receipt_binding(self) -> None:
        tool_turn = adapt_user_input("Inspect the migration.", session_id=self.session_id)
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="Inspect the migration.",
            source_context={
                "runtime_session_id": self.session_id,
                "_canonical_user_turn_id": tool_turn.turn_id,
            },
        )
        self._store_action_receipt(
            receipt_id="tool-receipt-before-intervening-turn",
            checkpoint_id=checkpoint["checkpoint_id"],
            summary="migration: old result",
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "last_tool_response": {
                    "tool_name": "migration.inspect",
                    "receipt": {"receipt_id": "tool-receipt-before-intervening-turn"},
                }
            },
            status="completed",
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input="Inspect the migration.",
            assistant_output="The migration inspection finished.",
            source_context={},
        )
        adapt_user_input("Tell me a joke.", session_id=self.session_id)
        append_conversation_event(
            session_id=self.session_id,
            user_input="Tell me a joke.",
            assistant_output="A short joke.",
            source_context={},
        )
        current = adapt_user_input("Run the tests.", session_id=self.session_id)
        task = create_task_record(current.normalized_text)

        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=current.as_context()),
            interpretation=current,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        self.assertFalse(
            any(item.source_type == "tool_observation" for item in result.relevant_items)
        )
        self.assertNotIn("tool-receipt-before-intervening-turn", result.assembled_context())

    def test_adjacent_checkpoint_with_unknown_lineage_is_not_imported(self) -> None:
        adapt_user_input("Inspect the migration.", session_id=self.session_id)
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="Inspect the migration.",
            source_context={"runtime_session_id": self.session_id},
        )
        self._store_action_receipt(
            receipt_id="tool-receipt-unknown-lineage",
            checkpoint_id=checkpoint["checkpoint_id"],
            summary="migration: unknown lineage",
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "last_tool_response": {
                    "tool_name": "migration.inspect",
                    "receipt": {"receipt_id": "tool-receipt-unknown-lineage"},
                }
            },
            status="completed",
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input="Inspect the migration.",
            assistant_output="The migration inspection finished.",
            source_context={},
        )
        current = adapt_user_input("Run the tests.", session_id=self.session_id)
        task = create_task_record(current.normalized_text)

        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=current.as_context()),
            interpretation=current,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        self.assertFalse(
            any(item.source_type == "tool_observation" for item in result.relevant_items)
        )

    def test_receipt_from_another_checkpoint_cannot_borrow_adjacent_lineage(self) -> None:
        prior = adapt_user_input("Inspect the migration.", session_id=self.session_id)
        causal_checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="Inspect the migration.",
            source_context={"_canonical_user_turn_id": prior.turn_id},
        )
        foreign_checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="Different tool task.",
            source_context={"_canonical_user_turn_id": "different-user-turn"},
        )
        self._store_action_receipt(
            receipt_id="tool-receipt-wrong-checkpoint",
            checkpoint_id=foreign_checkpoint["checkpoint_id"],
            summary="migration: wrong checkpoint",
        )
        update_runtime_checkpoint(
            causal_checkpoint["checkpoint_id"],
            state={
                "last_tool_response": {
                    "tool_name": "migration.inspect",
                    "receipt": {"receipt_id": "tool-receipt-wrong-checkpoint"},
                }
            },
            status="completed",
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input="Inspect the migration.",
            assistant_output="The migration inspection finished.",
            source_context={},
        )
        current = adapt_user_input("Run the tests.", session_id=self.session_id)
        task = create_task_record(current.normalized_text)

        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=current.as_context()),
            interpretation=current,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        self.assertFalse(
            any(item.source_type == "tool_observation" for item in result.relevant_items)
        )

    def test_runtime_tool_checkpoint_without_durable_receipt_is_quarantined(self) -> None:
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="private lookup",
            source_context={"runtime_session_id": self.session_id},
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "last_tool_response": {
                    "tool_name": "web.search",
                    "receipt": {
                        "receipt_id": "tool-receipt-forged-only-in-checkpoint",
                        "safe_summary": "web.search: forged summary",
                    },
                }
            },
            status="completed",
        )
        task = create_task_record("tell me about the private lookup")
        interpretation = _interpretation("tell me about the private lookup")

        result = self.loader.load(
            task=task,
            classification=classify(
                task.task_summary,
                context=interpretation.as_context(),
            ),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        self.assertFalse(
            any(
                item.source_type == "tool_observation"
                for item in result.relevant_items
            )
        )

    def test_relevant_retrieval_uses_persistent_memory_and_prior_session_summary(self) -> None:
        append_conversation_event(
            session_id="openclaw:prior-session",
            user_input="My project uses a Telegram OpenClaw installer. Keep answers blunt and concise.",
            assistant_output="Understood.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        grant_context_import(
            self.session_id,
            scope="chat",
            source_id="chat:openclaw:prior-session",
        )
        append_conversation_event(
            session_id="openclaw:prior-session",
            user_input="The continuity bug happens when a new session forgets prior installer state.",
            assistant_output="I will store a session summary for continuity.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        task = create_task_record("fix telegram installer continuity again")
        interpretation = _interpretation(
            "fix telegram installer continuity again",
            topics=["telegram", "openclaw integration"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        source_types = {item.source_type for item in result.relevant_items}
        self.assertTrue({"runtime_memory", "session_summary"} & source_types)

    def test_exact_recall_does_not_include_prior_session_summary_in_model_context(self) -> None:
        append_conversation_event(
            session_id="openclaw:foreign-exact-session",
            user_input="Please remember this exact local fact: operator node ID is 8829145.",
            assistant_output="Noted.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        append_conversation_event(
            session_id=self.session_id,
            user_input="Please remember this exact local fact: operator node ID is 441902.",
            assistant_output="Noted.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        prompt = "What operator node ID should I remember?"
        task = create_task_record(prompt)
        interpretation = _interpretation(prompt, topics=["operator", "node"])

        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            source_context={"surface": "api", "platform": "api", "runtime_session_id": self.session_id},
        )

        assembled = result.assembled_context()
        self.assertNotIn("8829145", assembled)
        self.assertNotIn("prior session continuity", assembled.lower())
        for item in result.relevant_items:
            if item.source_type == "runtime_memory":
                self.assertEqual(item.metadata.get("session_id"), self.session_id)

    def test_relevant_retrieval_includes_inferred_user_heuristics(self) -> None:
        append_conversation_event(
            session_id="openclaw:heuristic-session",
            user_input="I am building Telegram bots in Python. Use official docs and GitHub repos first.",
            assistant_output="Understood.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        task = create_task_record("design a telegram bot runtime")
        interpretation = _interpretation(
            "design a telegram bot runtime",
            topics=["telegram bot", "github"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        heuristic_items = [item for item in result.relevant_items if item.source_type == "user_heuristic"]
        self.assertTrue(heuristic_items)
        self.assertTrue(any("official documentation" in item.content.lower() or "github" in item.content.lower() for item in heuristic_items))

    def test_over_budget_trimming_behavior(self) -> None:
        long_text = "context " * 500
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw=long_text,
            rendered=long_text,
            status="complete",
            confidence=0.8,
        )
        task = create_task_record("system design context trimming")
        interpretation = _interpretation("system design context trimming", topics=["swarm", "memory"])
        result = self.loader.load(
            task=task,
            classification=classify("swarm memory system design", context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=220,
        )
        self.assertTrue(result.report.items_excluded or result.report.trimming_decisions)

    def test_cold_context_blocked_by_default(self) -> None:
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw="Old archive item",
            rendered="Old archive item about earlier system state",
            status="complete",
            confidence=0.7,
        )
        task = create_task_record("design current swarm topology")
        interpretation = _interpretation("design current swarm topology", topics=["swarm"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertFalse(result.cold_items)
        self.assertFalse(result.report.cold_archive_opened)

    def test_independent_turn_does_not_reintroduce_recent_dialogue_context(self) -> None:
        record_dialogue_turn(
            self.session_id,
            raw_input="Why might someone enjoy rain on a window?",
            normalized_input="Why might someone enjoy rain on a window?",
            reconstructed_input="Why might someone enjoy rain on a window?",
            speaker_role="user",
            topic_hints=["rain"],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )
        task = create_task_record(
            "For this chat, I prefer sketches to polished diagrams. Please remember it."
        )
        interpretation = _interpretation(task.task_summary, topics=["preference"], confidence=0.8)
        interpretation.is_continuation = False

        with mock.patch(
            "core.tiered_context_loader._runtime_tool_observation_items",
            side_effect=AssertionError("unrelated turn read stale tool state"),
        ):
            result = self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=interpretation.as_context()),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )

        self.assertFalse(
            any(item.source_type == "dialogue_turn" for item in result.relevant_items)
        )

    def test_cold_context_explicitly_allowed(self) -> None:
        self._grant_cold_context()
        for index in range(8):
            marker = f"CURRENT-CHAT-ARCHIVE-{index}"
            record_dialogue_turn(
                self.session_id,
                raw_input=marker,
                normalized_input=marker,
                reconstructed_input=marker,
                topic_hints=["archive", "meet"],
                reference_targets=[],
                understanding_confidence=0.9,
                quality_flags=[],
            )
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw="Historical meet topology archive",
            rendered="FOREIGN-GLOBAL-FINAL-RESPONSE-8871",
            status="complete",
            confidence=0.78,
        )
        task = create_task_record("show previous archive for meet topology")
        interpretation = _interpretation("show previous archive for meet topology", topics=["archive", "meet and greet"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.cold_decision.allow)
        self.assertTrue(result.report.cold_archive_opened)
        self.assertTrue(result.cold_items)
        self.assertNotIn(
            "FOREIGN-GLOBAL-FINAL-RESPONSE-8871",
            result.assembled_context(),
        )

    def test_low_confidence_relevant_retrieval_falls_back_safely(self) -> None:
        task = create_task_record("unclear thing maybe that one")
        interpretation = _interpretation("unclear thing maybe that one", confidence=0.31, topics=[])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertEqual(result.report.retrieval_confidence, "low")
        self.assertTrue(result.bootstrap_items)
        self.assertFalse(result.local_candidates)

    def test_swarm_metadata_consulted_without_auto_fetching_full_payload(self) -> None:
        self._grant_shared_context()
        record_remote_holder(
            shard_id=f"remote-{uuid.uuid4().hex}",
            holder_peer_id=f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            content_hash=f"remote-{uuid.uuid4().hex}",
            version=1,
            freshness_ts=_now(),
            ttl_seconds=600,
            topic_tags=["swarm", "knowledge", "replication"],
            summary_digest="digest-swarm-knowledge",
            size_bytes=128,
            metadata={"problem_class": "system_design"},
            fetch_route={"method": "request_shard", "shard_id": "remote"},
            trust_weight=0.64,
            home_region="us",
        )
        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.report.swarm_metadata_consulted)
        self.assertTrue(result.swarm_metadata)
        self.assertTrue(all(item.get("metadata_only") for item in result.swarm_metadata))

    def test_explicit_swarm_query_requests_live_remote_fetch(self) -> None:
        self._grant_shared_context()
        task = create_task_record("use swarm memory from peers for this telegram bot architecture")
        interpretation = _interpretation(
            "use swarm memory from peers for this telegram bot architecture",
            topics=["swarm memory", "telegram bot"],
        )
        with mock.patch(
            "core.tiered_context_loader.consult_relevant_swarm_metadata",
            return_value={
                "consulted": True,
                "fetched": 1,
                "items": [
                    {
                        "shard_id": "remote-shard",
                        "holder_peer_id": "peer-remote-1234567890",
                        "home_region": "eu",
                        "topic_tags": ["swarm", "telegram"],
                        "fetch_route": {"method": "request_shard"},
                        "trust_weight": 0.72,
                        "relevance_score": 2.4,
                        "problem_class": "system_design",
                        "utility_score": 0.84,
                        "quality_score": 0.78,
                        "metadata_only": False,
                        "fetched": True,
                    }
                ],
                "metadata_only": False,
            },
        ) as consult_swarm:
            result = self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=interpretation.as_context()),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )

        consult_swarm.assert_called_once()
        self.assertTrue(consult_swarm.call_args.kwargs["allow_fetch"])
        self.assertTrue(any(item.source_type == "swarm_remote_context" for item in result.relevant_items))

    def test_cached_remote_shard_surfaces_reuse_citation(self) -> None:
        self._grant_shared_context()
        shard_id = f"remote-cache-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_session_id, share_scope, created_at, updated_at
                ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, 0.88, 0.56, 0, 0, 'active', '[]', ?, NULL, ?, '', 'public_knowledge', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Remote swarm replication notes that were already fetched and cached locally",
                    json.dumps(["compare topology", "validate holder state"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    now,
                    "signed",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        receipt_id = record_fetch_receipt(
            shard_id=shard_id,
            source_peer_id=f"peer-holder-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            source_node_id=f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            query_id=f"query-{uuid.uuid4().hex}",
            manifest_id=f"manifest-{uuid.uuid4().hex}",
            content_hash=f"content-{uuid.uuid4().hex}",
            version=1,
            summary_digest="digest-remote-cache",
            validation_state="signature_and_manifest_verified",
            accepted=True,
            details={"reason": "test"},
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertTrue(remote_items)
        citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        self.assertEqual(citation["receipt_id"], receipt_id)
        self.assertEqual(citation["validation_state"], "signature_and_manifest_verified")
        snippets = result.context_snippets()
        self.assertTrue(any(dict(item.get("citation") or {}).get("receipt_id") == receipt_id for item in snippets))

    def test_cached_remote_shard_surfaces_reuse_outcome_summary(self) -> None:
        self._grant_shared_context()
        shard_id = f"remote-cache-summary-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_session_id, share_scope, created_at, updated_at
                ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, 0.91, 0.61, 0, 0, 'active', '[]', ?, NULL, ?, '', 'public_knowledge', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Remote shard with proven downstream reuse",
                    json.dumps(["inspect topology", "reuse holder evidence"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    now,
                    "signed",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        receipt_id = record_fetch_receipt(
            shard_id=shard_id,
            source_peer_id=f"peer-holder-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            source_node_id=f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            query_id=f"query-{uuid.uuid4().hex}",
            manifest_id=f"manifest-{uuid.uuid4().hex}",
            content_hash=f"content-{uuid.uuid4().hex}",
            version=1,
            summary_digest="digest-remote-cache-summary",
            validation_state="signature_and_manifest_verified",
            accepted=True,
            details={"reason": "test"},
        )
        record_shard_reuse_outcomes(
            citations=[
                {
                    "kind": "remote_shard",
                    "shard_id": shard_id,
                    "receipt_id": receipt_id,
                    "source_peer_id": "peer-holder",
                    "source_node_id": "peer-origin",
                    "manifest_id": "manifest-1",
                    "content_hash": "content-1",
                    "validation_state": "signature_and_manifest_verified",
                    "quality_backed": True,
                }
            ],
            task_id="task-reuse-summary",
            session_id=self.session_id,
            task_class="system_design",
            response_class="generic_conversation",
            success=True,
            durable=True,
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertTrue(remote_items)
        citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        reuse_outcomes = dict(citation.get("reuse_outcomes") or {})
        self.assertEqual(reuse_outcomes["total_count"], 1)
        self.assertEqual(reuse_outcomes["success_count"], 1)
        self.assertEqual(reuse_outcomes["durable_count"], 1)
        self.assertEqual(reuse_outcomes["selected_count"], 1)
        self.assertEqual(reuse_outcomes["answer_backed_count"], 1)
        self.assertEqual(reuse_outcomes["quality_backed_count"], 1)
        self.assertEqual(reuse_outcomes["last_receipt_id"], receipt_id)
        self.assertIn("Previously improved clean answers for this task class in 1 turns", remote_items[0].content)

    def test_cached_remote_shard_with_better_reuse_history_ranks_first(self) -> None:
        self._grant_shared_context()
        now = _now()

        def _insert_remote_shard(shard_id: str, summary: str, peer_id: str) -> str:
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO learning_shards (
                        shard_id, schema_version, problem_class, problem_signature,
                        summary, resolution_pattern_json, environment_tags_json,
                        source_type, source_node_id, quality_score, trust_score,
                        local_validation_count, local_failure_count,
                        quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                        signature, origin_session_id, share_scope, created_at, updated_at
                    ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, 0.86, 0.58, 0, 0, 'active', '[]', ?, NULL, ?, '', 'public_knowledge', ?, ?)
                    """,
                    (
                        shard_id,
                        f"sig-{uuid.uuid4().hex}",
                        summary,
                        json.dumps(["compare topology", "validate holder state"]),
                        json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                        peer_id,
                        now,
                        "signed",
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            return record_fetch_receipt(
                shard_id=shard_id,
                source_peer_id=peer_id,
                source_node_id=peer_id,
                query_id=f"query-{uuid.uuid4().hex}",
                manifest_id=f"manifest-{uuid.uuid4().hex}",
                content_hash=f"content-{uuid.uuid4().hex}",
                version=1,
                summary_digest=f"digest-{shard_id}",
                validation_state="signature_and_manifest_verified",
                accepted=True,
                details={"reason": "test"},
            )

        favored_shard_id = f"remote-favored-{uuid.uuid4().hex}"
        plain_shard_id = f"remote-plain-{uuid.uuid4().hex}"
        favored_receipt = _insert_remote_shard(
            favored_shard_id,
            "Remote shard with proven downstream success for swarm replication notes",
            f"peer-favored-{uuid.uuid4().hex}",
        )
        _insert_remote_shard(
            plain_shard_id,
            "Remote shard with similar swarm replication notes but no proven reuse yet",
            f"peer-plain-{uuid.uuid4().hex}",
        )

        record_shard_reuse_outcomes(
            citations=[
                {
                    "kind": "remote_shard",
                    "shard_id": favored_shard_id,
                    "receipt_id": favored_receipt,
                    "source_peer_id": "peer-favored",
                    "source_node_id": "peer-favored",
                    "manifest_id": "manifest-favored",
                    "content_hash": "content-favored",
                    "validation_state": "signature_and_manifest_verified",
                    "quality_backed": True,
                }
            ],
            task_id="task-rank-favored",
            session_id=self.session_id,
            task_class="system_design",
            response_class="generic_conversation",
            success=True,
            durable=True,
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertGreaterEqual(len(remote_items), 2)
        top_citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        self.assertEqual(top_citation["shard_id"], favored_shard_id)
        self.assertEqual(dict(top_citation.get("reuse_outcomes") or {}).get("quality_backed_count"), 1)

    def test_cached_remote_shard_does_not_get_reuse_credit_from_other_task_class(self) -> None:
        self._grant_shared_context()
        now = _now()

        def _insert_remote_shard(
            shard_id: str,
            summary: str,
            peer_id: str,
            *,
            quality_score: float,
            trust_score: float,
        ) -> str:
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO learning_shards (
                        shard_id, schema_version, problem_class, problem_signature,
                        summary, resolution_pattern_json, environment_tags_json,
                        source_type, source_node_id, quality_score, trust_score,
                        local_validation_count, local_failure_count,
                        quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                        signature, origin_session_id, share_scope, created_at, updated_at
                    ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, ?, ?, 0, 0, 'active', '[]', ?, NULL, ?, '', 'public_knowledge', ?, ?)
                    """,
                    (
                        shard_id,
                        f"sig-{uuid.uuid4().hex}",
                        summary,
                        json.dumps(["compare topology", "validate holder state"]),
                        json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                        peer_id,
                        quality_score,
                        trust_score,
                        now,
                        "signed",
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            return record_fetch_receipt(
                shard_id=shard_id,
                source_peer_id=peer_id,
                source_node_id=peer_id,
                query_id=f"query-{uuid.uuid4().hex}",
                manifest_id=f"manifest-{uuid.uuid4().hex}",
                content_hash=f"content-{uuid.uuid4().hex}",
                version=1,
                summary_digest=f"digest-{shard_id}",
                validation_state="signature_and_manifest_verified",
                accepted=True,
                details={"reason": "test"},
            )

        unrelated_shard_id = f"remote-unrelated-{uuid.uuid4().hex}"
        plain_shard_id = f"remote-plain-{uuid.uuid4().hex}"
        unrelated_receipt = _insert_remote_shard(
            unrelated_shard_id,
            "Remote shard with stronger but unrelated coding reuse history",
            f"peer-unrelated-{uuid.uuid4().hex}",
            quality_score=0.80,
            trust_score=0.55,
        )
        _insert_remote_shard(
            plain_shard_id,
            "Remote shard with slightly better direct swarm replication notes",
            f"peer-plain-{uuid.uuid4().hex}",
            quality_score=0.86,
            trust_score=0.60,
        )

        record_shard_reuse_outcomes(
            citations=[
                {
                    "kind": "remote_shard",
                    "shard_id": unrelated_shard_id,
                    "receipt_id": unrelated_receipt,
                    "source_peer_id": "peer-unrelated",
                    "source_node_id": "peer-unrelated",
                    "manifest_id": "manifest-unrelated",
                    "content_hash": "content-unrelated",
                    "validation_state": "signature_and_manifest_verified",
                    "quality_backed": True,
                }
            ],
            task_id="task-unrelated",
            session_id=self.session_id,
            task_class="coding",
            response_class="generic_conversation",
            success=True,
            durable=True,
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertGreaterEqual(len(remote_items), 2)
        top_citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        unrelated_citation = next(
            dict(item.metadata.get("reuse_citation") or {})
            for item in remote_items
            if dict(item.metadata.get("reuse_citation") or {}).get("shard_id") == unrelated_shard_id
        )
        self.assertEqual(top_citation["shard_id"], plain_shard_id)
        self.assertFalse(dict(unrelated_citation.get("reuse_outcomes") or {}))
        unrelated_item = next(
            item for item in remote_items if dict(item.metadata.get("reuse_citation") or {}).get("shard_id") == unrelated_shard_id
        )
        self.assertNotIn("Previously improved clean answers for this task class", unrelated_item.content)

    def test_shared_swarm_context_is_reused_in_live_retrieval(self) -> None:
        self._grant_shared_context()
        save_sniffed_context(
            parent_peer_id=f"peer-{uuid.uuid4().hex}",
            prompt_data={"task_summary": "telegram installer continuity bug"},
            result_data={"summary": "Persist session summaries and replay relevant memory into chat."},
        )
        task = create_task_record("how do we fix telegram installer continuity")
        interpretation = _interpretation(
            "how do we fix telegram installer continuity",
            topics=["telegram", "installer"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(any(item.source_type == "swarm_context" for item in result.relevant_items))

    def test_archival_store_is_never_read_into_active_context(self) -> None:
        task = create_task_record("run safe local status check")
        interpretation = _interpretation("run safe local status check", topics=["status"])
        with mock.patch(
            "core.liquefy_bridge.lookup_cold_archive_candidates",
            side_effect=AssertionError("archival store must stay inactive"),
        ) as cold_lookup:
            self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=interpretation.as_context()),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )
            cold_lookup.assert_not_called()

        archive_task = create_task_record("show previous archive for swarm topology")
        archive_interpretation = _interpretation("show previous archive for swarm topology", topics=["archive", "swarm"])
        self._grant_cold_context()
        with mock.patch(
            "core.liquefy_bridge.lookup_cold_archive_candidates",
            side_effect=AssertionError("archival store must stay inactive"),
        ) as cold_lookup:
            self.loader.load(
                task=archive_task,
                classification=classify(archive_task.task_summary, context=archive_interpretation.as_context()),
                interpretation=archive_interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )
            cold_lookup.assert_not_called()

    def test_prompt_assembly_report_records_included_and_excluded_items(self) -> None:
        long_text = "history " * 400
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw=long_text,
            rendered=long_text,
            status="complete",
            confidence=0.9,
        )
        task = create_task_record("show previous archive history")
        interpretation = _interpretation("show previous archive history", topics=["archive"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=240,
        )
        self.assertTrue(result.report.items_included)
        self.assertTrue(result.report.items_excluded or result.report.trimming_decisions)
        self.assertTrue(recent_context_access(limit=5))

    def test_existing_local_first_agent_flow_still_works(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="local-test", persona_id="default")
        agent.start()
        with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.request_relevant_holders", return_value=[]
        ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
            result = agent.run_once("pls harden tg setup so no passwords leak")
        self.assertIn("response", result)
        self.assertIn("prompt_assembly_report", result)
        self.assertEqual(result["route"], "deterministic:telegram_setup_intent")
        self.assertFalse(result["model_execution"]["used_model"])


if __name__ == "__main__":
    unittest.main()
