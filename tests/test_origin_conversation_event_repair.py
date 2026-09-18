"""Final Repair 3 (Mnemosyne final review, 2026-08-07): generation 1 carried
origin_conversation_event_id, but every retry generation lost it back to an empty string --
`_execute_attempt_retry_locked`'s call to `create_runtime_attempt` simply never passed it. Fixed by
propagating `parent_attempt["origin_conversation_event_id"]` into every child generation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_attempt_subtasks,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class OriginConversationEventIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "origin_event.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _make_gen1_with_failed_weather(self) -> dict:
        parent = create_runtime_attempt(
            session_id="s1", original_request="weather in Kaunas", answer_mode="LIVE_DATA",
            origin_user_turn_id="turn-origin-1", trigger_user_turn_id="turn-origin-1",
            origin_conversation_event_id="turn-origin-1",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
            retryable=True, retry_reason="transient tool failure",
        )
        return finalize_runtime_attempt(parent["attempt_id"])

    def _fake_weather(self, condition: str = "Clear"):
        from core.weather_result_contract import WeatherResult

        def fetch(location: str, **_kwargs):
            return WeatherResult(
                location=location, place_label=location, condition=condition, temperature_c=20.0,
                feels_like_c=19.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="01:00 PM",
            )
        return fetch

    def test_generation_1_carries_a_non_empty_origin_conversation_event(self) -> None:
        gen1 = self._make_gen1_with_failed_weather()
        self.assertEqual(gen1["origin_conversation_event_id"], "turn-origin-1")

    def test_generations_1_through_3_share_the_same_origin_conversation_event(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        gen1 = self._make_gen1_with_failed_weather()
        subtasks1 = list_runtime_attempt_subtasks(gen1["attempt_id"])

        # Generation 2: genuinely new retry, still fails, so it stays retryable for generation 3.
        def still_failing(location: str, **_kwargs):
            raise Exception("still down")

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=still_failing):
            result2 = execute_attempt_retry(
                gen1, subtasks1, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-origin-2", resolution_intent="RETRY_ATTEMPT",
            )
        gen2 = result2["attempt"]
        subtasks2 = list_runtime_attempt_subtasks(gen2["attempt_id"])

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather("Sunny")):
            result3 = execute_attempt_retry(
                gen2, subtasks2, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-origin-3", resolution_intent="RETRY_ATTEMPT",
            )
        gen3 = result3["attempt"]

        # The core Final Repair 3 assertion: all three generations share the same non-empty
        # origin conversation event.
        for generation, label in ((gen1, "gen1"), (gen2, "gen2"), (gen3, "gen3")):
            with self.subTest(label=label):
                self.assertEqual(generation["origin_conversation_event_id"], "turn-origin-1")

        # Retained alongside it: immutable origin turn, new trigger turn per genuine retry,
        # correct root/parent, incrementing generation.
        self.assertEqual(gen1["origin_user_turn_id"], "turn-origin-1")
        self.assertEqual(gen2["origin_user_turn_id"], "turn-origin-1")
        self.assertEqual(gen3["origin_user_turn_id"], "turn-origin-1")

        self.assertEqual(gen1["trigger_user_turn_id"], "turn-origin-1")
        self.assertEqual(gen2["trigger_user_turn_id"], "turn-origin-2")
        self.assertEqual(gen3["trigger_user_turn_id"], "turn-origin-3")

        self.assertEqual(gen2["root_attempt_id"], gen1["attempt_id"])
        self.assertEqual(gen3["root_attempt_id"], gen1["attempt_id"])
        self.assertEqual(gen2["parent_attempt_id"], gen1["attempt_id"])
        self.assertEqual(gen3["parent_attempt_id"], gen2["attempt_id"])

        self.assertEqual(gen1["execution_generation"], 1)
        self.assertEqual(gen2["execution_generation"], 2)
        self.assertEqual(gen3["execution_generation"], 3)

    def test_replay_of_the_same_trigger_returns_the_existing_child_with_the_same_origin_event(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        gen1 = self._make_gen1_with_failed_weather()
        subtasks1 = list_runtime_attempt_subtasks(gen1["attempt_id"])

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather()):
            first = execute_attempt_retry(
                gen1, subtasks1, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-origin-replay", resolution_intent="RETRY_ATTEMPT",
            )
            second = execute_attempt_retry(
                gen1, subtasks1, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-origin-replay", resolution_intent="RETRY_ATTEMPT",
            )

        self.assertEqual(first["attempt"]["attempt_id"], second["attempt"]["attempt_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["attempt"]["origin_conversation_event_id"], "turn-origin-1")
        self.assertEqual(second["attempt"]["origin_conversation_event_id"], "turn-origin-1")


class SabotageOriginConversationEventTests(unittest.TestCase):
    """Sabotage: drop the propagation -- generations 2+ must reproduce the empty-string defect."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "sabotage_origin_event.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_sabotage_dropping_propagation_reproduces_the_empty_origin_event(self) -> None:
        from core.runtime_continuity import create_runtime_attempt as real_create_runtime_attempt

        parent = real_create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            origin_user_turn_id="turn-1", trigger_user_turn_id="turn-1", origin_conversation_event_id="turn-1",
        )

        # Sabotage: the exact pre-repair call shape -- origin_conversation_event_id simply omitted.
        sabotaged_child = real_create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            origin_user_turn_id=parent["origin_user_turn_id"], trigger_user_turn_id="turn-2",
            root_attempt_id=parent["attempt_id"], parent_attempt_id=parent["attempt_id"],
            execution_generation=2,
        )
        self.assertEqual(sabotaged_child["origin_conversation_event_id"], "", "sabotage should reproduce the empty-string defect")

        # Control: the REAL retry path (with propagation) does not lose it.
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import (
            finalize_runtime_attempt,
            list_runtime_attempt_subtasks,
            upsert_runtime_attempt_subtask,
        )
        from core.weather_result_contract import WeatherResult

        parent2 = real_create_runtime_attempt(
            session_id="s1", original_request="weather in Kaunas", answer_mode="LIVE_DATA",
            origin_user_turn_id="turn-3", trigger_user_turn_id="turn-3", origin_conversation_event_id="turn-3",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent2["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500",
            retryable=True, retry_reason="transient tool failure",
        )
        parent2 = finalize_runtime_attempt(parent2["attempt_id"])
        subtasks2 = list_runtime_attempt_subtasks(parent2["attempt_id"])

        def fake_weather(location: str, **_kwargs):
            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=20.0,
                feels_like_c=19.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="01:00 PM",
            )

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            result = execute_attempt_retry(
                parent2, subtasks2, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-4", resolution_intent="RETRY_ATTEMPT",
            )
        self.assertEqual(result["attempt"]["origin_conversation_event_id"], "turn-3", "the real code must not lose it")


if __name__ == "__main__":
    unittest.main()
