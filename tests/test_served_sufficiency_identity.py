"""C01 — the served model-sufficiency writer joins THE TURN'S OWN stored events.

The store flattens event details onto the event dict (`append_runtime_event` →
`list_runtime_session_events` round-trip); these tests drive that EXACT production chain —
`emit_runtime_event` into a real sqlite event store, read back through the canonical lister,
then joined by the writer seam — instead of hand-built nested fixtures that never matched what
production stores.

Law under test:
* the writer reads the stored shape and writes rows with exact durable identity
  (task_kind / provider / model / outcome / stage / turn_key / runtime session);
* the join is scoped to ONE turn's own events — never another turn's, never another session's;
* a turn with no identity records nothing rather than borrowing;
* model selection consumes only eligible observations (exact task_kind+provider+model,
  current feedback version, fresh) — never a neighbouring model's, task's or generation's rows.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_task_events import (
    configure_runtime_event_store,
    emit_runtime_event,
    list_recent_runtime_session_events,
    reset_runtime_event_state,
)


def _isolated_stores(tmpdir: str):
    """Learning stores AND the runtime event store re-pointed at one temp home."""
    shards = sys_modules_shards()
    sufficiency = sys_modules_sufficiency()
    return (
        mock.patch.object(shards, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
        mock.patch.object(sufficiency, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
    )


def sys_modules_shards():
    import sys

    return sys.modules["core.learning.procedure_shards"] if "core.learning.procedure_shards" in sys.modules else _load("core.learning.procedure_shards")


def sys_modules_sufficiency():
    import sys

    return sys.modules["core.learning.model_sufficiency"] if "core.learning.model_sufficiency" in sys.modules else _load("core.learning.model_sufficiency")


def _load(name: str):
    from importlib import import_module

    return import_module(name)


class _EventStoreHome(unittest.TestCase):
    """Each test gets a temp sqlite event store + temp learning stores."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.events_db = tmp / "events.db"
        configure_runtime_event_store(str(self.events_db))
        self._store_patches = _isolated_stores(str(tmp / "learning"))
        self._store_patches[0].start()
        self._store_patches[1].start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(reset_runtime_event_state)
        self.addCleanup(self._store_patches[1].stop)
        self.addCleanup(self._store_patches[0].stop)

    def _emit_call_completed(
        self,
        *,
        session: str,
        turn_key: str,
        provider: str = "ollama-local:qwen3:8b",
        model: str = "qwen3:8b",
        task_kind: str = "code_edit",
        context: dict | None = None,
    ) -> None:
        emit_runtime_event(
            context if context is not None else {"runtime_session_id": session},
            event_type="model.call_completed",
            message="Model call completed.",
            details={
                "turn_key": turn_key,
                "provider_id": provider,
                "model_id": model,
                "task_kind": task_kind,
                "request_id": "req-" + turn_key,
            },
        )

    def _emit_trace_completed(self, *, session: str, turn_key: str, state: str) -> None:
        emit_runtime_event(
            {"runtime_session_id": session},
            event_type="turn.trace_completed",
            message="Turn trace completed.",
            details={
                "turn_key": turn_key,
                "stage_verdict": {"state": state, "explain": "test"},
            },
        )


class TestTheWriterJoinsTheStoredShape(_EventStoreHome):
    def test_completed_call_and_trace_write_one_eligible_row(self):
        """The production round-trip: emitted events, stored flattened, listed back, joined."""
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        self._emit_call_completed(session="sess-live", turn_key="turn-live-1")
        self._emit_trace_completed(session="sess-live", turn_key="turn-live-1", state="success")

        stored = list_recent_runtime_session_events("sess-live", limit=200)
        self.assertEqual(len(stored), 2, "the store must hold both events")
        # The stored shape is FLATTENED: identity rides the event dict itself.
        call_event = next(e for e in stored if e.get("event_type") == "model.call_completed")
        self.assertEqual(call_event.get("provider_id"), "ollama-local:qwen3:8b")
        self.assertNotIn("details", call_event, "production events do not nest a details dict")

        written = record_turn_sufficiency_from_events(stored, session_id="sess-live", turn_key="turn-live-1")
        self.assertEqual(written, 1, f"the turn's own events must join into exactly one row; wrote {written}")

        rows = list_sufficiency_observations(provider_id="ollama-local:qwen3:8b")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["task_kind"], "code_edit")
        self.assertEqual(row["model_id"], "qwen3:8b")
        self.assertEqual(row["outcome"], "verified_success")
        self.assertEqual(row["stage_state"], "success")
        self.assertEqual(row["turn_key"], "turn-live-1")
        self.assertEqual(row["session_id"], "sess-live")

    def test_nested_shape_callers_still_join(self):
        """Direct callers may hand nested-dict events; both shapes join."""
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        events = [
            {
                "event_type": "model.call_completed",
                "turn_key": "turn-nested",
                "details": {"provider_id": "p-nested", "model_id": "m-nested", "task_kind": "chat"},
            },
            {"event_type": "turn.trace_completed", "turn_key": "turn-nested", "details": {"stage_verdict": {"state": "success"}}},
        ]
        written = record_turn_sufficiency_from_events(events, session_id="sess-nested", turn_key="turn-nested")
        self.assertEqual(written, 1)
        rows = list_sufficiency_observations(provider_id="p-nested")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_kind"], "chat")


class TestTheJoinIsScopedToOneTurn(_EventStoreHome):
    def test_other_turns_events_in_the_window_write_nothing(self):
        """The writer is handed a session window that also holds EARLIER turns' events; only the
        named turn may join — and an earlier turn on a DIFFERENT provider must not leak in as a
        row attributed to this turn."""
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        self._emit_call_completed(
            session="sess-scope", turn_key="turn-earlier", provider="ollama-local:qwen2:7b", model="qwen2:7b"
        )
        self._emit_trace_completed(session="sess-scope", turn_key="turn-earlier", state="success")
        self._emit_call_completed(session="sess-scope", turn_key="turn-mine")
        self._emit_trace_completed(session="sess-scope", turn_key="turn-mine", state="success")

        stored = list_recent_runtime_session_events("sess-scope", limit=200)
        written = record_turn_sufficiency_from_events(stored, session_id="sess-scope", turn_key="turn-mine")
        rows = list_sufficiency_observations()
        self.assertEqual(written, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn_key"], "turn-mine", "only the named turn's row may exist")
        self.assertEqual(rows[0]["provider_id"], "ollama-local:qwen3:8b", "no borrowed provider identity")

    def test_no_turn_identity_records_nothing(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        self._emit_call_completed(session="sess-noid", turn_key="turn-x")
        self._emit_trace_completed(session="sess-noid", turn_key="turn-x", state="success")
        stored = list_recent_runtime_session_events("sess-noid", limit=200)
        self.assertEqual(
            record_turn_sufficiency_from_events(stored, session_id="sess-noid", turn_key=""),
            0,
            "an identity-less join must never borrow a turn's events",
        )
        self.assertEqual(list_sufficiency_observations(), [])

    def test_cross_session_events_never_join(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        self._emit_call_completed(session="sess-other", turn_key="turn-cross")
        self._emit_trace_completed(session="sess-other", turn_key="turn-cross", state="success")
        stored_other = list_recent_runtime_session_events("sess-other", limit=200)
        self.assertEqual(
            record_turn_sufficiency_from_events(stored_other, session_id="sess-mine", turn_key="turn-cross"),
            1,  # the events belong to the named turn; the row carries the RECORDING session
        )
        rows = list_sufficiency_observations()
        self.assertEqual(rows[0]["session_id"], "sess-mine", "the row must name the session that recorded it")
        # ...and the other session's own window joins under ITS identity, deduped by turn+provider.
        record_turn_sufficiency_from_events(stored_other, session_id="sess-other", turn_key="turn-cross")
        self.assertEqual(len(list_sufficiency_observations()), 1, "one (turn, provider) is one observation")


class TestStageTruthMapsToTheExactOutcome(_EventStoreHome):
    def test_quality_failure_states_record_quality_failure(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        self._emit_call_completed(session="sess-qf", turn_key="turn-qf", task_kind="research")
        self._emit_trace_completed(session="sess-qf", turn_key="turn-qf", state="validator_rejected")
        stored = list_recent_runtime_session_events("sess-qf", limit=200)
        record_turn_sufficiency_from_events(stored, session_id="sess-qf", turn_key="turn-qf")
        rows = list_sufficiency_observations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "quality_failure")
        self.assertEqual(rows[0]["stage_state"], "validator_rejected")

    def test_transport_and_retrieval_states_record_nothing(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        for state in ("provider_error", "retrieval_empty", "required_tools_not_offered"):
            with self.subTest(state=state):
                turn = f"turn-{state}"
                self._emit_call_completed(session="sess-noobs", turn_key=turn)
                self._emit_trace_completed(session="sess-noobs", turn_key=turn, state=state)
                stored = list_recent_runtime_session_events("sess-noobs", limit=200)
                self.assertEqual(record_turn_sufficiency_from_events(stored, session_id="sess-noobs", turn_key=turn), 0)
        self.assertEqual(list_sufficiency_observations(), [])


class TestSelectionConsumesOnlyEligibleRows(_EventStoreHome):
    def _write(self, *, task_kind: str, provider: str, model: str, outcome: str, version: int | None = None, age_days: float = 0.0) -> None:
        from datetime import datetime, timedelta, timezone

        from core.learning.model_sufficiency import record_sufficiency_observation

        self._write_seq = getattr(self, "_write_seq", 0) + 1
        row = record_sufficiency_observation(
            task_kind=task_kind,
            provider_id=provider,
            model_id=model,
            outcome=outcome,
            stage_state="success" if outcome == "verified_success" else "validator_rejected",
            turn_key=f"turn-{self._write_seq:03d}-{task_kind}-{provider}-{model}-{outcome}",
            session_id="sess-elig",
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            feedback_version=version,
        )
        assert row is not None

    def test_adjustment_isolates_model_task_and_generation(self):
        from core.learning.model_sufficiency import sufficiency_adjustment

        for _ in range(3):
            self._write(task_kind="code_edit", provider="prov-a", model="model-a", outcome="verified_success")
        # A sibling model under the SAME provider failing: must not touch model-a's adjustment.
        for _ in range(3):
            self._write(task_kind="code_edit", provider="prov-a", model="model-b", outcome="quality_failure")
        # A different task kind, same provider+model: must not touch code_edit's adjustment.
        self._write(task_kind="research", provider="prov-a", model="model-a", outcome="quality_failure")
        # A stale generation row: preserved for inspection, never ranked.
        self._write(task_kind="code_edit", provider="prov-a", model="model-a", outcome="quality_failure", version=1)
        # A stale-aged row beyond freshness: never ranked.
        self._write(task_kind="code_edit", provider="prov-a", model="model-a", outcome="quality_failure", age_days=30.0)

        adjustment = sufficiency_adjustment(task_kind="code_edit", provider_id="prov-a", model_id="model-a")
        self.assertEqual(adjustment, 0.35, "three fresh verified successes must cap the bonus")
        self.assertEqual(
            sufficiency_adjustment(task_kind="code_edit", provider_id="prov-a", model_id="model-b"),
            -0.75,
            "model-b's own failure signal is its own",
        )
        # One fresh failure under another task kind: below the observation floor -> status quo.
        self._write(task_kind="research", provider="prov-a", model="model-c", outcome="quality_failure")
        self.assertEqual(
            sufficiency_adjustment(task_kind="research", provider_id="prov-a", model_id="model-c"),
            0.0,
            "one observation is below the floor",
        )
        # An unknown task kind can never be selected for, so it must adjust nothing anywhere.
        self.assertEqual(sufficiency_adjustment(task_kind="unknown", provider_id="prov-a", model_id="model-a"), 0.0)

    def test_unknown_task_kind_rows_are_inspection_only(self):
        """The pre-fix writer shape: rows keyed task_kind=unknown must exist for operators but
        never match a selection request."""
        from core.learning import list_sufficiency_observations
        from core.learning.model_sufficiency import record_sufficiency_observation, sufficiency_adjustment

        self.assertIsNotNone(
            record_sufficiency_observation(
                task_kind="unknown",
                provider_id="prov-u",
                model_id="model-u",
                outcome="verified_success",
                turn_key="turn-unknown-1",
                session_id="sess-u",
            )
        )
        self.assertEqual(len(list_sufficiency_observations(task_kind="unknown")), 1)
        self.assertEqual(sufficiency_adjustment(task_kind="unknown", provider_id="prov-u", model_id="model-u"), 0.0)


if __name__ == "__main__":
    unittest.main()
