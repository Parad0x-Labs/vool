"""Repair 4 (Mnemosyne review, 2026-08-06): origin_user_turn_id / trigger_user_turn_id /
origin_conversation_event_id were empty on every production-created attempt and retry --
`create_runtime_attempt` and `execute_attempt_retry` both supported these fields, but
apps/vool_agent.py never supplied them. Fixed by threading the real, persisted
`dialogue_turns.turn_id` (core.human_input_adapter.adapt_user_input -> record_dialogue_turn)
through `source_context["_canonical_user_turn_id"]` -- the existing carrier for internal,
non-provider-facing turn metadata -- down to every attempt-creation call site.

Driven through the REAL production entry point (`VoolAgent.run_once`), matching the discipline
`test_attempt_followup_resolution.py` already established.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    latest_runtime_attempt,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations

_SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}
_INCIDENT_TEXT = "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas"


class RetryTurnIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "turnid.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self.agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _submit_incident(self) -> str:
        from core.live_quote_contract import LiveQuoteResult
        from core.weather_result_contract import WeatherResult

        def fake_crypto(coin_ids, **_kwargs):
            return [LiveQuoteResult(
                asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64781.0, currency="USD",
                as_of="2026-08-06 16:20 UTC", source_label="CoinGecko", source_url="https://x",
                kind="crypto", change_percent=0.43,
            )]

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4320.7, currency="USD",
                as_of="2026-08-06 07:30 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.36,
            )]

        def fake_weather(location: str, **_kwargs):
            if "atlantis" in location.lower():
                from urllib.error import HTTPError

                raise HTTPError("https://wttr.in/atlantisxyzabc123", 500, "Internal Server Error", None, None)
            return WeatherResult(
                location=location, place_label=location.title(), condition="Sunny", temperature_c=28.0,
                feels_like_c=27.0, humidity_pct=40.0, wind_kmph=8.0, source_label="wttr.in",
                source_url="https://x", observed_at="02:35 PM",
            )

        with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            result = self.agent.run_once(_INCIDENT_TEXT, source_context=dict(_SOURCE_CONTEXT))
        return result["session_id"]

    def test_first_attempt_has_real_matching_origin_and_trigger_turn_ids(self) -> None:
        sid = self._submit_incident()
        gen1 = latest_runtime_attempt(sid)
        self.assertTrue(str(gen1.get("origin_user_turn_id") or "").strip(), "origin_user_turn_id was empty")
        self.assertTrue(str(gen1.get("trigger_user_turn_id") or "").strip(), "trigger_user_turn_id was empty")
        self.assertTrue(str(gen1.get("origin_conversation_event_id") or "").strip())
        self.assertEqual(gen1["origin_user_turn_id"], gen1["trigger_user_turn_id"])
        # A real UUID-shaped dialogue turn id, not a placeholder.
        self.assertRegex(gen1["origin_user_turn_id"], r"^[0-9a-f-]{36}$")

    def test_retry_keeps_origin_turn_and_mints_a_new_trigger_turn(self) -> None:
        sid = self._submit_incident()
        gen1 = latest_runtime_attempt(sid)

        def fake_weather(location: str, **_kwargs):
            from core.weather_result_contract import WeatherResult

            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=22.0,
                feels_like_c=21.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="04:00 PM",
            )

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            self.agent.run_once("Retry the exact failed request now.", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        gen2 = latest_runtime_attempt(sid)

        self.assertEqual(gen2["execution_generation"], 2)
        self.assertEqual(gen2["parent_attempt_id"], gen1["attempt_id"])
        # The first answering attempt is a child of the ingress root. A retry must
        # preserve that chain root, not turn its immediate parent into a new root.
        self.assertEqual(gen2["root_attempt_id"], gen1["root_attempt_id"])
        from core.runtime_continuity import get_runtime_attempt

        root = get_runtime_attempt(gen1["root_attempt_id"])
        self.assertIsNotNone(root)
        self.assertEqual(root["root_attempt_id"], root["attempt_id"])
        self.assertEqual(root["attempt_role"], "turn_root")
        # Repair 4's specific spec: SAME origin turn, DIFFERENT trigger turn for a genuine new retry.
        self.assertEqual(gen2["origin_user_turn_id"], gen1["origin_user_turn_id"])
        self.assertNotEqual(gen2["trigger_user_turn_id"], gen1["trigger_user_turn_id"])
        self.assertTrue(str(gen2.get("trigger_user_turn_id") or "").strip())

    def test_sabotage_dropping_turn_wiring_leaves_origin_turn_empty(self) -> None:
        """Mutation: remove the origin/trigger wiring (return "" from the carrier this repair
        added) -- the production-path assertion above must turn red without it."""
        with mock.patch.object(VoolAgent, "_canonical_user_turn_id", staticmethod(lambda source_context: "")):
            sid = self._submit_incident()
        gen1 = latest_runtime_attempt(sid)
        self.assertEqual(gen1.get("origin_user_turn_id"), "", "sabotage should have reproduced the empty-turn-id defect")

        # Control: the REAL wiring, unpatched, produces a non-empty id for the identical request.
        reset_runtime_continuity_state()
        sid2 = self._submit_incident()
        gen1_control = latest_runtime_attempt(sid2)
        self.assertTrue(str(gen1_control.get("origin_user_turn_id") or "").strip())


if __name__ == "__main__":
    unittest.main()
