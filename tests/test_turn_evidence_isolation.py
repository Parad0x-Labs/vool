"""P1: one turn cannot borrow another turn's evidence.

Three confirmed defects, one law. (1) `turn_has_current_evidence` answered from
SESSION-WIDE execution records whenever the caller arrived without a turn id, so
turn A's fetch licensed turn B's claim. (2) Resume/follow-up paths restored earlier
turns' results into a new turn's evidence channels as if they had just been
observed. (3) REPEAT_ORIGINAL's reconstruction appended a synthetic `ok=True`
observation, disarming the current-value guards on a turn that observed nothing.

The law after this family: evidence is eligible only for the exact canonical turn
and synthesis generation that consumed it. Session membership alone never proves
relevance or freshness. Recall ("check the original message and answer it") keeps
working because a recall is not a current-information request -- not because a
restored row was dressed up as a fresh observation.
"""

from __future__ import annotations

import unittest

from core.model_output_guard import turn_ran_observations
from core.observation_evidence import records_a_usable_observation
from core.unsourced_current_claim import turn_has_current_evidence

# -------------------------------------------------------------------------------------------
# Exact-turn identity for current-evidence standing (RED 1, 2, 7)
# -------------------------------------------------------------------------------------------


class ExactTurnIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        from core import execution_records

        self._records = execution_records
        self._records.clear("tei-session-a")
        self._records.clear("tei-session-b")

    def tearDown(self) -> None:
        self._records.clear("tei-session-a")
        self._records.clear("tei-session-b")

    def _observe(self, session_id: str, turn_id: str, *, ok: bool = True) -> None:
        self._records.record(
            session_id=session_id,
            intent="live_data.weather_lookup",
            arguments={},
            observation={"ok": ok, "status": "executed"} if ok else {"ok": False, "failure_class": "no observation returned"},
            turn_id=turn_id,
            ok=ok,
        )

    def test_turn_a_retrieval_cannot_ground_turn_b_in_the_same_session(self) -> None:
        self._observe("tei-session-a", "turn-a")
        self.assertFalse(
            turn_has_current_evidence(session_id="tei-session-a", turn_id="turn-b"),
            "turn A's successful fetch must not ground turn B's claim",
        )

    def test_a_turn_that_lost_its_id_grounds_nothing_from_session_records(self) -> None:
        """The confirmed defect: with turn_id empty the predicate fell back to the whole
        session's records, so the previous turn's fetch grounded this turn's claim."""
        self._observe("tei-session-a", "turn-a")
        self.assertFalse(turn_has_current_evidence(session_id="tei-session-a", turn_id=""))
        self.assertFalse(turn_has_current_evidence(session_id="tei-session-a"))

    def test_concurrent_sessions_and_turns_cannot_see_each_other(self) -> None:
        self._observe("tei-session-a", "turn-shared")
        self.assertFalse(
            turn_has_current_evidence(session_id="tei-session-b", turn_id="turn-shared"),
            "another session's evidence is invisible even for an identical turn id",
        )
        self.assertTrue(
            turn_has_current_evidence(session_id="tei-session-a", turn_id="turn-shared"),
            "control: a turn's own evidence still stands",
        )

    def test_same_turn_evidence_with_valid_bound_identity_remains_eligible(self) -> None:
        self._observe("tei-session-a", "turn-a")
        self.assertTrue(turn_has_current_evidence(session_id="tei-session-a", turn_id="turn-a"))

    def test_a_failed_observation_is_never_current_evidence_even_same_turn(self) -> None:
        self._observe("tei-session-a", "turn-a", ok=False)
        self.assertFalse(turn_has_current_evidence(session_id="tei-session-a", turn_id="turn-a"))


# -------------------------------------------------------------------------------------------
# Restored / reconstructed entries are history, never freshness authority (RED 5, 6, 9)
# -------------------------------------------------------------------------------------------


_RESTORED_NOTE = {
    "summary": "Kaunas: Clear, 21 C.",
    "source_type": "web_derived",
    "fresh": False,
    "lifecycle": "restored",
}


class RestoredEvidenceFreshnessTests(unittest.TestCase):
    def test_a_note_marked_restored_is_not_a_usable_observation(self) -> None:
        self.assertFalse(records_a_usable_observation(_RESTORED_NOTE))

    def test_a_restored_note_cannot_ground_a_current_claim(self) -> None:
        """At base the mark was ignored: the note's summary alone grounded the claim."""
        self.assertFalse(turn_has_current_evidence(notes=[_RESTORED_NOTE]))

    def test_a_restored_entry_in_a_receipt_channel_is_not_an_observation(self) -> None:
        restored_receipt = {
            "schema": "vool.web_retrieval_receipt.v1",
            "status": "available",
            "source_count": 1,
            "fresh": False,
            "lifecycle": "restored",
        }
        self.assertFalse(records_a_usable_observation(restored_receipt))
        self.assertFalse(turn_ran_observations({"web_retrieval_receipts": [restored_receipt]}))

    def test_an_entry_marked_fresh_false_with_a_successful_outcome_still_loses_standing(self) -> None:
        """The restored mark outranks the original outcome: an entry carried forward from a
        turn that DID observe is still not a THIS-turn observation."""
        carried = {"ok": True, "status": "executed", "fresh": False, "source_turn_id": "turn-a"}
        self.assertFalse(records_a_usable_observation(carried))

    def test_unmarked_entries_keep_the_standing_they_always_had(self) -> None:
        """The safe unknown direction is untouched: only an entry that AFFIRMATIVELY marks
        itself restored loses standing."""
        self.assertTrue(records_a_usable_observation({"summary": "Kaunas: Clear, 21 C."}))
        self.assertTrue(records_a_usable_observation("a plain note"))
        self.assertTrue(records_a_usable_observation({"ok": True, "status": "executed"}))

    def test_turn_ran_observations_ignores_a_restored_tool_observation(self) -> None:
        context = {"runtime_tool_observations": [{"ok": True, "status": "restored", "fresh": False}]}
        self.assertFalse(turn_ran_observations(context))


_RECALL_TABLE = (
    "**Weather**\n\n| City | Current Temp (C) |\n| --- | --- |\n| Kaunas | 28 |\n\n"
    "Warmest current city: Kaunas (28 C)"
)


class RestoredProvenanceStandDownTests(unittest.TestCase):
    """The final-output backstop must recognize restored provenance by NAME -- never by a
    restored entry posing as a successful observation. A recall's readings are stored facts
    re-rendered with their own timestamps, so "any numbers I gave would be invented" is false
    for that text; the same text on a turn with no restored provenance is still replaced."""

    def _validate(self, source_context: dict) -> str:
        from core.agent_runtime.response import _validate_final_chat_output

        return _validate_final_chat_output(_RECALL_TABLE, source_context=source_context)

    def test_a_recall_with_restored_provenance_is_not_replaced(self) -> None:
        context = {
            "runtime_tool_observations": [{
                "schema": "tool_observation_v1",
                "intent": "attempt.reconstruct",
                "ok": False,
                "fresh": False,
                "lifecycle": "restored",
                "source_turn_id": "turn-original",
            }]
        }
        self.assertIn("Kaunas", self._validate(context))

    def test_the_same_text_without_restored_provenance_is_still_replaced(self) -> None:
        self.assertNotIn("Kaunas", self._validate({}))

    def test_has_restored_evidence_provenance_needs_an_affirmative_mark(self) -> None:
        from core.model_output_guard import has_restored_evidence_provenance

        self.assertFalse(has_restored_evidence_provenance({}))
        self.assertFalse(has_restored_evidence_provenance({"runtime_tool_observations": [{"ok": True}]}))
        self.assertTrue(
            has_restored_evidence_provenance({"web_retrieval_receipts": [{"fresh": False}]})
        )
        self.assertTrue(
            has_restored_evidence_provenance({"runtime_tool_observations": [{"lifecycle": "restored"}]})
        )


# -------------------------------------------------------------------------------------------
# REPEAT_ORIGINAL reconstruction: recall keeps its history, loses its manufactured evidence
# -------------------------------------------------------------------------------------------


def _succeeded_attempt() -> dict:
    return {
        "attempt_id": "attempt-isolation0001",
        "plan_id": "plan-isolation",
        "original_request_snapshot": "what is the weather in Kaunas right now?",
        "lifecycle_state": "SUCCEEDED",
        "trigger_user_turn_id": "turn-original",
        "origin_user_turn_id": "turn-original",
        "execution_generation": 1,
    }


def _succeeded_weather_rows() -> list[dict]:
    return [
        {
            "subtask_id": "plan-isolation:weather_lookup:kaunas",
            "operation": "weather_lookup",
            "entity_type": "city",
            "entity_key": "kaunas",
            "arguments": {"location": "kaunas"},
            "lifecycle_state": "SUCCEEDED",
            "result_summary": {
                "condition": "Clear",
                "temperature_c": 21.0,
                "source": "wttr.in",
                "observed_at": "2026-09-01 09:00 UTC",
            },
            "failure_class": "",
            "failure_reason": "",
            "approval_state": "",
        }
    ]


class RepeatOriginalReconstructionTests(unittest.TestCase):
    def _render(self) -> tuple[str, dict]:
        from core.attempt_followup import render_reconstructed_answer

        source_context: dict = {}
        rendered = render_reconstructed_answer(
            _succeeded_attempt(), _succeeded_weather_rows(), source_context=source_context
        )
        return rendered, source_context

    def test_recall_still_renders_the_stored_reading(self) -> None:
        """Timeless follow-up recall keeps working: the stored rows render as history."""
        rendered, _ = self._render()
        self.assertIn("Kaunas", rendered)
        self.assertIn("21", rendered)
        self.assertIn("2026-09-01 09:00 UTC", rendered)

    def test_reconstruction_writes_no_synthetic_successful_observation(self) -> None:
        """RED 6: the reconstruction used to append `ok: bool(succeeded)` -- a manufactured
        ok=True observation on a turn that fetched nothing."""
        _, source_context = self._render()
        entries = [
            entry
            for entry in source_context.get("runtime_tool_observations", [])
            if entry.get("intent") == "attempt.reconstruct"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIsNot(entry.get("ok"), True, "a reconstruction must not manufacture a successful observation")
        self.assertIs(entry.get("fresh"), False)
        self.assertEqual(entry.get("lifecycle"), "restored")

    def test_restored_evidence_preserves_its_source_identity(self) -> None:
        """The restored entry carries whose turn produced it, which evidence set it belongs
        to, and which generation -- so no reader can mistake it for this turn's own."""
        _, source_context = self._render()
        entry = next(
            entry
            for entry in source_context["runtime_tool_observations"]
            if entry.get("intent") == "attempt.reconstruct"
        )
        self.assertEqual(entry.get("source_turn_id"), "turn-original")
        self.assertEqual(entry.get("generation"), 1)
        self.assertIn("attempt-isolation0001", str(entry.get("evidence_set_id")))

    def test_the_reconstructed_turn_holds_no_freshness_authority(self) -> None:
        """RED 5: the restore armed `turn_ran_observations`, disarming the current-value
        guards. The reconstructed turn observed nothing and must read as such."""
        _, source_context = self._render()
        self.assertFalse(turn_ran_observations(source_context))

    def test_a_recall_phrase_is_not_a_current_information_request(self) -> None:
        """Why recall survives without the manufactured evidence: the guards convict only a
        turn that REQUIRED a current observation, and a recall phrase is not one."""
        from core.execution_requirements import requirements_for

        self.assertFalse(
            requirements_for("check the original message and answer it").current_information_required
        )


# -------------------------------------------------------------------------------------------
# Retry reuse: same canonical turn, same bound evidence set, policy still permits (RED 3, 4)
# -------------------------------------------------------------------------------------------


_NOW_ISO = "2026-09-01T12:00:00+00:00"


def _succeeded_row(operation: str, *, minutes_old: int) -> dict:
    from datetime import datetime, timedelta

    completed = datetime.fromisoformat(_NOW_ISO) - timedelta(minutes=minutes_old)
    return {
        "subtask_id": f"row-{operation}",
        "operation": operation,
        "entity_type": "city",
        "entity_key": "kaunas",
        "arguments": {"location": "kaunas"},
        "lifecycle_state": "SUCCEEDED",
        "result_summary": {"condition": "Clear", "source": "wttr.in", "observed_at": "09:00 AM"},
        "failure_class": "",
        "completed_at": completed.isoformat(),
    }


class RetryEvidenceReuseTests(unittest.TestCase):
    def _plan(self, rows: list[dict], *, same_canonical_turn: bool):
        from core.agent_runtime.attempt_retry import plan_retry_generation

        return plan_retry_generation(
            rows, plan_id="plan-isolation", now_iso=_NOW_ISO, same_canonical_turn=same_canonical_turn
        )

    def test_same_turn_retry_reuses_a_policy_permitted_result(self) -> None:
        """A no-TTL operation's SUCCEEDED row carries forward within the same canonical turn."""
        carry, rerun = self._plan([_succeeded_row("custom_lookup", minutes_old=1)], same_canonical_turn=True)
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_cross_turn_reuse_requires_a_freshness_policy(self) -> None:
        """RED 4: with no freshness policy there is nothing that PERMITS carrying the result
        across a turn boundary -- so a new-generation retry retrieves again."""
        carry, rerun = self._plan([_succeeded_row("custom_lookup", minutes_old=1)], same_canonical_turn=False)
        self.assertEqual(len(carry), 0, "no policy permits cross-turn reuse")
        self.assertEqual(len(rerun), 1)
        self.assertIn("_refresh_reason", rerun[0])

    def test_cross_turn_reuse_still_honours_the_stated_ttl_policy(self) -> None:
        """Within a stated freshness window the result carries forward across turns; past it,
        the existing staleness rule already forces a refresh."""
        carry, rerun = self._plan([_succeeded_row("weather_lookup", minutes_old=5)], same_canonical_turn=False)
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

        carry, rerun = self._plan([_succeeded_row("weather_lookup", minutes_old=90)], same_canonical_turn=False)
        self.assertEqual(len(carry), 0)
        self.assertEqual(len(rerun), 1)

    def test_deterministic_outcomes_are_never_refreshed_across_turns(self) -> None:
        """An unsupported entity is a planning fact, not time-based evidence."""
        row = _succeeded_row("unsupported_market_entity", minutes_old=5000)
        row["lifecycle_state"] = "UNSUPPORTED_ENTITY"
        carry, rerun = self._plan([row], same_canonical_turn=False)
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)


if __name__ == "__main__":
    unittest.main()
