"""Repair 10 (Mnemosyne review, 2026-08-06): CONTINUE_ATTEMPT recorded resolution_confidence=0.9,
fallback_used=False -- a "successful resolution" -- and then returned None, falling through to
ordinary handling without ever using that resolution. Fixed: CONTINUE_ATTEMPT is now recorded
honestly as a fallback (resolution_confidence=0.0, fallback_used=True), since the attempt it
classified against is never actually used to answer the turn.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
from storage.migrations import run_migrations

_SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}
_INCIDENT_TEXT = "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas"


class ContinueBookkeepingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "continue.db"
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

    def test_continue_classifies_but_falls_through_and_records_a_fallback_not_a_success(self) -> None:
        """Driven directly against `_maybe_answer_attempt_followup_turn` -- the real production
        method this repair fixed. Note: routed through the FULL turn pipeline (`run_once`),
        "continue" after a completed request is intercepted earlier by the pre-existing "try
        again"/checkpoint-resume mechanism (`_prepare_runtime_checkpoint`), which substitutes the
        ORIGINAL request text before this resolver ever sees the literal word "continue" -- that
        substitution is a separate, older subsystem this repair does not touch. Calling the
        resolver method directly with the literal text isolates the exact bug this repair fixes:
        what CONTINUE_ATTEMPT records once it IS reached."""
        from core.runtime_continuity import list_runtime_session_events

        sid = self._submit_incident()
        result = self.agent._maybe_answer_attempt_followup_turn(
            effective_input="continue", session_id=sid, source_context=dict(_SOURCE_CONTEXT),
        )
        self.assertIsNone(result, "CONTINUE_ATTEMPT has no dedicated handler -- must fall through")

        import sqlite3

        events = list_runtime_session_events(sid)
        followup_events = [e for e in events if e["event_type"] in ("runtime_follow_up_resolved", "runtime_follow_up_unresolved")]
        continue_events = [e for e in followup_events if e.get("resolution_intent") == "CONTINUE_ATTEMPT"]
        self.assertTrue(continue_events, "CONTINUE_ATTEMPT should have been classified and recorded")

        # The Activity event's own `event_type` is chosen purely by whether an attempt_id was
        # resolved (a separate, general-purpose convention record_followup_resolution already had)
        # -- this repair still records WHICH attempt CONTINUE_ATTEMPT classified against, for
        # audit purposes, so that stays "resolved". The actual signal Repair 10 fixes is
        # fallback_used/resolution_confidence on the durable row itself, checked directly here.
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT resolution_confidence, fallback_used FROM runtime_followup_resolutions "
                "WHERE session_id = ? AND resolution_intent = 'CONTINUE_ATTEMPT' "
                "ORDER BY created_at DESC LIMIT 1",
                (sid,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "CONTINUE_ATTEMPT should have a durable resolution row")
        # The defect: this used to be resolution_confidence=0.9, fallback_used=0 (False).
        self.assertEqual(row["fallback_used"], 1, "must record fallback_used=True for an intent with no handler")
        self.assertEqual(row["resolution_confidence"], 0.0, "must not claim confidence for a resolution that was never used")

    def test_sabotage_recording_a_false_success_would_mislabel_the_event(self) -> None:
        """Sabotage: reproduce the pre-Repair-10 shape directly -- record CONTINUE_ATTEMPT as if
        it were a successful resolution (fallback_used=False) -- and confirm that lands as
        `runtime_follow_up_resolved`, the exact mislabeling this repair removed."""
        from core.runtime_continuity import record_followup_resolution

        sid = self._submit_incident()
        sabotaged = record_followup_resolution(
            session_id=sid, resolved_attempt_id="attempt-fake", resolution_intent="CONTINUE_ATTEMPT",
            resolution_reason="sabotaged: claims success", resolution_confidence=0.9, fallback_used=False,
        )
        self.assertEqual(sabotaged["fallback_used"], False)

        from core.runtime_continuity import list_runtime_session_events

        events = list_runtime_session_events(sid)
        sabotaged_event = next(e for e in events if e.get("resolution_id") == sabotaged["resolution_id"])
        self.assertEqual(sabotaged_event["event_type"], "runtime_follow_up_resolved", "sabotage should mislabel this as resolved")


if __name__ == "__main__":
    unittest.main()
