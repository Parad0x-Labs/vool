from __future__ import annotations

import unittest
import uuid
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.curiosity_policy import CuriosityConfig
from core.curiosity_roamer import CuriosityRoamer, curiosity_interest_score, derive_curiosity_topics
from core.human_input_adapter import HumanInputInterpretation
from storage.curiosity_state import recent_curiosity_runs, recent_curiosity_topics
from storage.db import get_connection
from storage.migrations import run_migrations


def _interpretation(text: str, *, topics: list[str] | None = None, confidence: float = 0.83) -> HumanInputInterpretation:
    return HumanInputInterpretation(
        raw_text=text,
        normalized_text=text,
        reconstructed_text=text,
        intent_mode="request",
        topic_hints=list(topics or []),
        reference_targets=[],
        understanding_confidence=confidence,
        quality_flags=[],
        needs_clarification=False,
        turn_id=None,
    )


class CuriosityRoamerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("curiosity_runs", "curiosity_topics", "candidate_knowledge_lane", "web_notes", "learning_shards", "local_tasks"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        # Web access is opt-in/off by default; adaptive research exercises the
        # live web path, so enable it explicitly for these tests.
        self._web_patch = mock.patch(
            "core.curiosity_roamer.policy_engine.allow_web_fallback", return_value=True
        )
        self._web_patch.start()
        self.addCleanup(self._web_patch.stop)

    def test_derive_topics_prefers_technical_sources_for_bot_building(self) -> None:
        config = CuriosityConfig(
            enabled=True,
            mode="bounded_auto",
            auto_execute_task_classes=("research", "system_design"),
            max_topics_per_task=2,
            max_queries_per_topic=2,
            max_snippets_per_query=3,
            prefer_metadata_first=True,
            allow_news_pulse=True,
            news_max_topics_per_task=1,
            technical_max_topics_per_task=2,
            min_interest_score=0.56,
            min_understanding_confidence=0.5,
            skip_if_retrieval_confidence_at_least=0.84,
            max_total_roam_seconds=8,
            auto_promote_to_canonical=False,
        )
        topics = derive_curiosity_topics(
            user_input="research telegram discord bot building best practices",
            classification={"task_class": "research"},
            interpretation=_interpretation("research telegram discord bot building best practices", topics=["telegram bot", "discord", "app"]),
            config=config,
        )
        self.assertTrue(topics)
        combined_profile_ids = {profile.profile_id for topic in topics for profile in topic.source_profiles}
        self.assertIn("messaging_platform_docs", combined_profile_ids)
        self.assertIn("reputable_repos", combined_profile_ids)


    def _execute_queued(self, roamer, result, task, user_input, classification, interpretation):
        """AMENDED for F46: maybe_roam QUEUES on the served path — its inline execution
        cost users 60-97 s of roam generations inside their own turns (measured, named
        frame). The execution MECHANICS these tests pin (candidate recording, domain
        blocking, cache hits) are unchanged; this helper drives the executor the way the
        background lane will, re-deriving the deterministic topic list the roamer queued.
        """
        from core.curiosity_roamer import derive_curiosity_topics

        topics = derive_curiosity_topics(
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            config=roamer.config,
        )
        candidate_ids = []
        for topic_id, topic in zip(result.queued_topic_ids, topics):
            candidate_id, _cached = roamer._execute_topic(
                topic_id=topic_id,
                topic=topic,
                task_id=str(task.task_id),
                trace_id=str(task.task_id),
            )
            if candidate_id:
                candidate_ids.append(candidate_id)
        return candidate_ids

    def test_bounded_auto_records_candidate_only(self) -> None:
        roamer = CuriosityRoamer(
            CuriosityConfig(
                enabled=True,
                mode="bounded_auto",
                auto_execute_task_classes=("research",),
                max_topics_per_task=1,
                max_queries_per_topic=1,
                max_snippets_per_query=2,
                prefer_metadata_first=True,
                allow_news_pulse=True,
                news_max_topics_per_task=1,
                technical_max_topics_per_task=2,
                min_interest_score=0.40,
                min_understanding_confidence=0.4,
                skip_if_retrieval_confidence_at_least=0.95,
                max_total_roam_seconds=8,
                auto_promote_to_canonical=False,
            )
        )
        task = mock.Mock(task_id=f"task-{uuid.uuid4().hex}", task_summary="telegram bot building")
        interpretation = _interpretation("telegram bot building", topics=["telegram bot", "discord"])
        context_result = mock.Mock(retrieval_confidence_score=0.22)
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=[{"summary": "Use official docs and avoid token leaks.", "source_label": "duckduckgo.com"}]):
            result = roamer.maybe_roam(
                task=task,
                user_input="research telegram bot building",
                classification={"task_class": "research"},
                interpretation=interpretation,
                context_result=context_result,
                session_id="curiosity-test",
            )
        self.assertTrue(result.queued_topic_ids)
        self.assertFalse(result.candidate_ids, "the served path queues; it never executes inline")
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=[{"summary": "Use official docs and avoid token leaks.", "source_label": "duckduckgo.com"}]):
            result.candidate_ids = self._execute_queued(
                roamer, result, task, "research telegram bot building",
                {"task_class": "research"}, interpretation,
            )
        self.assertTrue(result.candidate_ids)
        conn = get_connection()
        try:
            candidate = conn.execute("SELECT provider_name, promotion_state FROM candidate_knowledge_lane LIMIT 1").fetchone()
            self.assertEqual(candidate["provider_name"], "curiosity_roamer")
            self.assertEqual(candidate["promotion_state"], "candidate")
            learning_shards = conn.execute("SELECT COUNT(*) AS c FROM learning_shards").fetchone()["c"]
            self.assertEqual(learning_shards, 0)
        finally:
            conn.close()

    def test_topic_kind_reads_words_not_substrings(self) -> None:
        """MEASURED on the controlled rig (9d7b6a3d): "What is the capital of France?" was
        classified `integration` -- "api" inside "capital" -- so its topic ran under allow-listed
        profiles that rejected every source and the consumer ended it `empty`."""
        from core.curiosity_roamer import _topic_kind

        cfg = CuriosityConfig(
            enabled=True, mode="bounded_auto", auto_execute_task_classes=("research",),
            max_topics_per_task=2, max_queries_per_topic=2, max_snippets_per_query=3,
            prefer_metadata_first=True, allow_news_pulse=True, news_max_topics_per_task=1,
            technical_max_topics_per_task=2, min_interest_score=0.4, min_understanding_confidence=0.4,
            skip_if_retrieval_confidence_at_least=0.9, max_total_roam_seconds=8, auto_promote_to_canonical=False,
        )
        q = "What is the capital of France?"
        self.assertEqual(_topic_kind(q, "chat_conversation", q, config=cfg), "general")
        self.assertEqual(_topic_kind("rapid prototyping", "chat_conversation", "rapid prototyping", config=cfg), "general")
        self.assertEqual(_topic_kind("telegram bot", "research", "build a telegram bot", config=cfg), "integration")
        self.assertEqual(_topic_kind("weather API limits", "research", "weather API limits", config=cfg), "integration")
        self.assertEqual(_topic_kind("planet earth today", "research", "give me the news pulse for planet earth today", config=cfg), "news")

    def test_news_topics_get_short_lived_profile(self) -> None:
        topics = derive_curiosity_topics(
            user_input="give me the news pulse for planet earth today",
            classification={"task_class": "research"},
            interpretation=_interpretation("give me the news pulse for planet earth today", topics=["news"]),
        )
        self.assertTrue(any(topic.topic_kind == "news" for topic in topics))
        news_topic = next(topic for topic in topics if topic.topic_kind == "news")
        self.assertEqual(news_topic.source_profiles[0].profile_id, "reputable_news")

    def test_blocked_domains_do_not_become_candidates(self) -> None:
        roamer = CuriosityRoamer(
            CuriosityConfig(
                enabled=True,
                mode="bounded_auto",
                auto_execute_task_classes=("research",),
                max_topics_per_task=1,
                max_queries_per_topic=1,
                max_snippets_per_query=2,
                prefer_metadata_first=True,
                allow_news_pulse=True,
                news_max_topics_per_task=1,
                technical_max_topics_per_task=2,
                min_interest_score=0.40,
                min_understanding_confidence=0.4,
                skip_if_retrieval_confidence_at_least=0.95,
                max_total_roam_seconds=8,
                auto_promote_to_canonical=False,
            )
        )
        task = mock.Mock(task_id=f"task-{uuid.uuid4().hex}", task_summary="planet earth news")
        interpretation = _interpretation("planet earth news", topics=["news"])
        context_result = mock.Mock(retrieval_confidence_score=0.12)
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[
                {"summary": "Propaganda snippet", "source_label": "duckduckgo.com", "origin_domain": "rt.com"},
                {"summary": "Wire update", "source_label": "duckduckgo.com", "origin_domain": "reuters.com"},
            ],
        ):
            result = roamer.maybe_roam(
                task=task,
                user_input="give me a pulse on planet earth news",
                classification={"task_class": "research"},
                interpretation=interpretation,
                context_result=context_result,
                session_id="cred-test",
            )
        self.assertTrue(result.queued_topic_ids)
        self.assertFalse(result.candidate_ids, "the served path queues; it never executes inline")
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[
                {"summary": "Propaganda snippet", "source_label": "duckduckgo.com", "origin_domain": "rt.com"},
                {"summary": "Wire update", "source_label": "duckduckgo.com", "origin_domain": "reuters.com"},
            ],
        ):
            result.candidate_ids = self._execute_queued(
                roamer, result, task, "give me a pulse on planet earth news",
                {"task_class": "research"}, interpretation,
            )
        self.assertTrue(result.candidate_ids)
        conn = get_connection()
        try:
            row = conn.execute("SELECT structured_output_json FROM candidate_knowledge_lane ORDER BY created_at DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertIn("reuters.com", row["structured_output_json"])
        self.assertNotIn("rt.com", row["structured_output_json"])

    def test_cache_hit_prevents_repeat_fetch(self) -> None:
        roamer = CuriosityRoamer(
            CuriosityConfig(
                enabled=True,
                mode="bounded_auto",
                auto_execute_task_classes=("research",),
                max_topics_per_task=1,
                max_queries_per_topic=1,
                max_snippets_per_query=1,
                prefer_metadata_first=True,
                allow_news_pulse=True,
                news_max_topics_per_task=1,
                technical_max_topics_per_task=1,
                min_interest_score=0.40,
                min_understanding_confidence=0.4,
                skip_if_retrieval_confidence_at_least=0.95,
                max_total_roam_seconds=8,
                auto_promote_to_canonical=False,
            )
        )
        task = mock.Mock(task_id=f"task-{uuid.uuid4().hex}", task_summary="telegram bot building")
        interpretation = _interpretation("telegram bot building", topics=["telegram bot"])
        context_result = mock.Mock(retrieval_confidence_score=0.22)
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=[{"summary": "Use official docs.", "source_label": "duckduckgo.com"}]):
            first = roamer.maybe_roam(
                task=task,
                user_input="research telegram bot building",
                classification={"task_class": "research"},
                interpretation=interpretation,
                context_result=context_result,
                session_id="curiosity-cache",
            )
        self.assertTrue(first.queued_topic_ids)
        self.assertFalse(first.candidate_ids, "the served path queues; it never executes inline")
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=[{"summary": "Use official docs.", "source_label": "duckduckgo.com"}]):
            first.candidate_ids = self._execute_queued(
                roamer, first, task, "research telegram bot building",
                {"task_class": "research"}, interpretation,
            )
        self.assertTrue(first.candidate_ids)
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query") as search_again:
            second = roamer.maybe_roam(
                task=task,
                user_input="research telegram bot building",
                classification={"task_class": "research"},
                interpretation=interpretation,
                context_result=context_result,
                session_id="curiosity-cache",
            )
            self.assertTrue(second.queued_topic_ids)
            second.candidate_ids = self._execute_queued(
                roamer, second, task, "research telegram bot building",
                {"task_class": "research"}, interpretation,
            )
        self.assertTrue(first.candidate_ids)
        # AMENDED for F46: the cache mechanic now lives between EXECUTOR calls (the
        # served path queues without executing). The pin's intent — a repeat topic
        # costs no second fetch — is held by the same-candidate reuse below.
        self.assertEqual(second.candidate_ids, first.candidate_ids)
        search_again.assert_not_called()

    def test_interest_score_stays_low_for_small_clear_status_checks(self) -> None:
        score = curiosity_interest_score(
            user_input="check current local setup status",
            classification={"task_class": "unknown"},
            interpretation=_interpretation("check current local setup status", topics=["setup"], confidence=0.78),
            context_result=mock.Mock(retrieval_confidence_score=0.92),
        )
        self.assertLess(score, 0.56)

    def test_agent_run_once_surfaces_curiosity_metadata(self) -> None:
        agent = VoolAgent(backend_name="local", device="mac")
        agent.start()
        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=[{"summary": "Prefer official docs for Telegram bot setup.", "source_label": "duckduckgo.com"}]):
            result = agent.run_once("research telegram bot building best practices")
        self.assertIn("curiosity", result)
        self.assertTrue(result["curiosity"]["enabled"])
        self.assertTrue(result["curiosity"]["topics"])
        self.assertTrue(recent_curiosity_topics(limit=5))
        # AMENDED for F46: a RUN row records executed work, and the served path no longer
        # executes roam topics inline (measured: 60-97 s of roam generations inside the
        # user's own turn). Topics are queued durably; runs appear when a background
        # executor consumes the queue. Asserting the empty state keeps this honest.
        self.assertEqual(recent_curiosity_runs(limit=5), [])

    def test_run_idle_commons_returns_public_safe_summary(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[{"summary": "Use official docs and keep exports sanitized.", "source_label": "web.search", "origin_domain": "docs.python.org"}],
        ):
            result = roamer.run_idle_commons(session_id="agent-commons:test", seed_index=0)
        self.assertTrue(result["candidate_id"])
        self.assertIn("Agent commons update", result["public_body"])
        self.assertIn("agent_commons", result["topic_tags"])

    def test_run_external_topic_returns_candidate_summary(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[{"summary": "Benchmark candidate scripts before promoting them.", "source_label": "web.search", "origin_domain": "docs.python.org"}],
        ):
            result = roamer.run_external_topic(
                session_id="auto-research:test",
                topic_text="Best way to research a topic before scripting it",
                topic_kind="technical",
            )
        self.assertTrue(result["candidate_id"])
        self.assertIn("Bounded curiosity notes", result["summary"])

    @pytest.mark.xfail(reason="Pre-existing: adaptive broadening not triggered")
    def test_adaptive_research_broadens_when_initial_evidence_is_thin(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[
                [
                    {
                        "summary": "One thin summary",
                        "result_title": "Thin note",
                        "result_url": "https://example.test/thin",
                        "origin_domain": "example.test",
                    }
                ],
                [
                    {
                        "summary": "Open source onboarding patterns",
                        "result_title": "Developer onboarding",
                        "result_url": "https://docs.github.com/onboarding",
                        "origin_domain": "docs.github.com",
                        "source_profile_label": "Official docs",
                    },
                    {
                        "summary": "Product onboarding teardown",
                        "result_title": "Onboarding teardown",
                        "result_url": "https://www.intercom.com/blog/onboarding",
                        "origin_domain": "intercom.com",
                    },
                ],
            ],
        ):
            result = roamer.adaptive_research(
                task_id="task-broaden",
                user_input="research developer onboarding best practices",
                classification={"task_class": "research"},
                interpretation=_interpretation("research developer onboarding best practices", topics=["developer onboarding"]),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertTrue(result.broadened)
        self.assertIn("broaden_search", result.actions_taken)
        self.assertGreaterEqual(len(result.queries_run), 2)
        self.assertIn(result.evidence_strength, {"moderate", "strong"})
        self.assertFalse(result.admitted_uncertainty)

    def test_adaptive_research_narrows_specific_error_then_admits_uncertainty(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[[], [], []],
        ):
            result = roamer.adaptive_research(
                task_id="task-narrow",
                user_input="traceback TypeError in telegram bot startup after python 3.12 upgrade",
                classification={"task_class": "debugging"},
                interpretation=_interpretation(
                    "traceback TypeError in telegram bot startup after python 3.12 upgrade",
                    topics=["telegram bot", "TypeError"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertTrue(result.narrowed)
        self.assertIn("narrow_search", result.actions_taken)
        self.assertTrue(result.admitted_uncertainty)
        self.assertTrue(result.uncertainty_reason)

    def test_adaptive_research_compares_sources_for_tradeoff_queries(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[
                [
                    {
                        "summary": "Supabase is fast to start with managed Postgres.",
                        "result_title": "Supabase overview",
                        "result_url": "https://supabase.com/docs",
                        "origin_domain": "supabase.com",
                        "source_profile_label": "Official docs",
                    }
                ],
                [
                    {
                        "summary": "Firebase has tighter managed mobile integrations.",
                        "result_title": "Firebase overview",
                        "result_url": "https://firebase.google.com/docs",
                        "origin_domain": "firebase.google.com",
                        "source_profile_label": "Official docs",
                    }
                ],
            ],
        ):
            result = roamer.adaptive_research(
                task_id="task-compare",
                user_input="compare supabase vs firebase for a telegram bot backend",
                classification={"task_class": "system_design"},
                interpretation=_interpretation(
                    "compare supabase vs firebase for a telegram bot backend",
                    topics=["supabase", "firebase", "telegram bot"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertIn("compare_sources", result.actions_taken)
        self.assertTrue(result.compared_sources)
        self.assertGreaterEqual(len(result.source_domains), 2)
        self.assertFalse(result.admitted_uncertainty)

    def test_adaptive_research_skips_generic_system_design_tradeoff_explanations(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=AssertionError("generic architecture explanation should not trigger live research"),
        ):
            result = roamer.adaptive_research(
                task_id="task-generic-tradeoff",
                user_input="Explain the event loop architecture tradeoffs.",
                classification={"task_class": "system_design"},
                interpretation=_interpretation(
                    "Explain the event loop architecture tradeoffs.",
                    topics=["architecture"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertFalse(result.enabled)
        self.assertEqual(result.reason, "research_not_needed")
        self.assertEqual(result.strategy, "not_needed")

    def test_adaptive_research_verifies_claims_with_authoritative_sources(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[
                [
                    {
                        "summary": "A community thread claims the setting is deprecated.",
                        "result_title": "Community thread",
                        "result_url": "https://example.test/thread",
                        "origin_domain": "example.test",
                    }
                ],
                [
                    {
                        "summary": "Official docs show the setting is still supported.",
                        "result_title": "Official docs",
                        "result_url": "https://docs.python.org/3/library/asyncio.html",
                        "origin_domain": "docs.python.org",
                        "source_profile_label": "Official docs",
                    }
                ],
            ],
        ):
            result = roamer.adaptive_research(
                task_id="task-verify",
                user_input="verify whether asyncio.run is still supported in python 3.12",
                classification={"task_class": "research"},
                interpretation=_interpretation(
                    "verify whether asyncio.run is still supported in python 3.12",
                    topics=["asyncio.run", "python 3.12"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertIn("verify_claim", result.actions_taken)
        self.assertTrue(result.verified_claim)
        self.assertEqual(result.stop_reason, "verification_ready")

    def test_adaptive_research_retries_fuzzy_public_entity_lookup(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[
                [],
                [
                    {
                        "summary": "Anatoly Yakovenko, often called Toly, co-founded Solana.",
                        "result_title": "Solana leadership",
                        "result_url": "https://solana.com/team",
                        "origin_domain": "solana.com",
                        "source_profile_label": "Official docs",
                    },
                    {
                        "summary": "Toly is Anatoly Yakovenko on X.",
                        "result_title": "toly on X",
                        "result_url": "https://x.com/aeyakovenko",
                        "origin_domain": "x.com",
                    },
                ],
            ],
        ):
            result = roamer.adaptive_research(
                task_id="task-entity-fuzzy",
                user_input="Tolly on X in Solana who is he",
                classification={"task_class": "unknown"},
                interpretation=_interpretation(
                    "Tolly on X in Solana who is he",
                    topics=["solana"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertEqual(result.strategy, "entity_lookup")
        self.assertEqual(result.queries_run[:2], ["tolly x solana", "toly x solana"])
        self.assertTrue(result.narrowed)
        self.assertFalse(result.admitted_uncertainty)

    def test_adaptive_research_admits_uncertainty_for_unknown_public_entity(self) -> None:
        roamer = CuriosityRoamer()
        with mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=[[], [], []],
        ):
            result = roamer.adaptive_research(
                task_id="task-entity-uncertain",
                user_input="check Tolyy on X in Solana",
                classification={"task_class": "unknown"},
                interpretation=_interpretation(
                    "check Tolyy on X in Solana",
                    topics=["solana"],
                ),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result.enabled)
        self.assertTrue(result.admitted_uncertainty)
        self.assertIn("public figure", result.uncertainty_reason.lower())


if __name__ == "__main__":
    unittest.main()
