"""Runtime-attempt and agent-node events must carry the CLIENT turn id.

The chat page groups the Activity ledger strictly by `client_turn_id` and files every untagged
event under "Between turns". `emit_runtime_event` stamps the tag, but two emitters write through
`append_runtime_event` directly, BELOW that seam: the shared agent-node emitter
(apps/vool_agent.py::_agent_node_emitter, used by both the live-data runner and the conductor)
and the runtime-attempt lifecycle events (core/runtime_continuity.py). Observed live 2026-08-14
(session openclaw:ed890df3): exactly those rows orphaned while every neighbouring event was
tagged.

Everything here drives the REAL emission path -- the real emitter closure through the real
live-data runner worker threads, and the real attempt CRUD -- against a real on-disk ledger.
No canned events, no mocked emission.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.agent_runtime.live_data_plan import LiveDataPlan, LiveDataSubtask
from core.agent_runtime.live_data_runner import run_live_data_plan
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_session_events,
    reset_runtime_continuity_state,
    update_runtime_attempt,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


def _noop_subtask(subtask_id: str) -> LiveDataSubtask:
    # An operation the runner does not know: `_run_one` returns FAILED without touching the
    # network, but `_run_one_timed` still emits the real agent_node_started/completed pair around
    # it -- the exact emission path production uses, with zero external dependencies.
    return LiveDataSubtask(
        subtask_id=subtask_id,
        entity=subtask_id,
        operation="offline_test_noop",
        arguments={},
        required_result_fields=(),
        tool="none",
        tool_intent="none",
    )


class _LedgerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "ledger.db"
        run_migrations(db_path=db_path)
        configure_runtime_continuity_db_path(str(db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()


class AgentNodeEventsCarryTheTurnTests(_LedgerCase):
    def _emitter(self, session_id: str, source_context: dict | None):
        from apps.vool_agent import VoolAgent
        from core.agent_runtime.runtime_checkpoint_support import RuntimeCheckpointSupportMixin
        from core.agent_runtime.tool_result_workflow_surface import ToolResultWorkflowSurfaceMixin

        # The production emitter path without booting an agent: the node emitter's closure
        # routes through `self._emit_runtime_event` (the durable-plus-streaming seam, from
        # ToolResultWorkflowSurfaceMixin) and its checkpoint resolver (from
        # RuntimeCheckpointSupportMixin) -- both stateless adapters, so a carrier of exactly
        # those two mixins executes the real emission code, not a stand-in. The old bare
        # object() predates the streaming route and died with AttributeError at the seam.
        carrier = type(
            "_NodeEmitterCarrier",
            (RuntimeCheckpointSupportMixin, ToolResultWorkflowSurfaceMixin),
            {},
        )
        # The real emitter factory, bound through the real class with a carrier that satisfies
        # exactly the surface its streaming route reads -- the production closure, not a
        # reimplementation of it.
        return VoolAgent._agent_node_emitter(carrier(), session_id, source_context)

    def test_node_events_carry_the_client_turn_id_through_the_real_runner(self) -> None:
        emit = self._emitter("s-node", {"cancel_turn_id": "turn-77", "session_id": "s-node"})
        run_live_data_plan(
            LiveDataPlan(
                plan_id="p1", attempt_id="a1", original_request="x",
                subtasks=(_noop_subtask("livedata-x:noop:one"), _noop_subtask("livedata-x:noop:two")),
            ),
            emit_node_event=emit,
        )
        events = list_runtime_session_events("s-node")
        node_events = [e for e in events if e["event_type"] in ("agent_node_started", "agent_node_completed")]
        self.assertEqual(len(node_events), 4, events)
        for event in node_events:
            self.assertEqual(event.get("client_turn_id"), "turn-77", event)

    def test_a_context_without_a_turn_id_invents_nothing(self) -> None:
        # Negative control: no cancel_turn_id -> the events stay untagged, never a fabricated id.
        for context in (None, {}, {"cancel_turn_id": "   "}, {"cancel_turn_id": ""}):
            emit = self._emitter("s-untagged", context)
            emit("agent_node_started", {"node_id": "n1", "operation": "noop"})
        events = list_runtime_session_events("s-untagged")
        self.assertEqual(len(events), 4)
        for event in events:
            self.assertNotIn("client_turn_id", event, event)

    def test_concurrent_emitters_never_cross_tag(self) -> None:
        # Worst case: two turns' emitters interleave from worker threads (the runner uses a real
        # thread pool). Each event must carry ITS OWN turn's id -- captured at emitter creation,
        # not read from shared state mid-run.
        import threading

        emit_a = self._emitter("s-conc", {"cancel_turn_id": "turn-A"})
        emit_b = self._emitter("s-conc", {"cancel_turn_id": "turn-B"})

        def drive(emit, node_prefix: str) -> None:
            for index in range(5):
                emit("agent_node_started", {"node_id": f"{node_prefix}-{index}", "operation": "noop"})

        threads = [
            threading.Thread(target=drive, args=(emit_a, "a")),
            threading.Thread(target=drive, args=(emit_b, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        events = list_runtime_session_events("s-conc")
        self.assertEqual(len(events), 10)
        for event in events:
            expected = "turn-A" if str(event.get("node_id", "")).startswith("a-") else "turn-B"
            self.assertEqual(event.get("client_turn_id"), expected, event)

    def test_an_emitter_never_overrides_a_tag_the_detail_already_carries(self) -> None:
        emit = self._emitter("s-keep", {"cancel_turn_id": "turn-outer"})
        emit("agent_node_started", {"node_id": "n1", "operation": "noop", "client_turn_id": "turn-inner"})
        events = list_runtime_session_events("s-keep")
        self.assertEqual(events[0].get("client_turn_id"), "turn-inner")


class RuntimeAttemptEventsCarryTheTurnTests(_LedgerCase):
    def test_the_attempt_lifecycle_events_are_all_tagged(self) -> None:
        attempt = create_runtime_attempt(
            session_id="s-att", original_request="weather in paris and berlin",
            answer_mode="LIVE_DATA", client_turn_id="turn-9",
        )
        attempt_id = attempt["attempt_id"]
        update_runtime_attempt(attempt_id, plan_id="p1", lifecycle_state="PLANNED", client_turn_id="turn-9")
        update_runtime_attempt(attempt_id, lifecycle_state="RUNNING", client_turn_id="turn-9")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt_id, subtask_id="w1", plan_id="p1",
            operation="weather_lookup", lifecycle_state="SUCCEEDED",
        )
        finalize_runtime_attempt(attempt_id, client_turn_id="turn-9")

        events = list_runtime_session_events("s-att")
        attempt_events = [e for e in events if str(e["event_type"]).startswith("runtime_attempt_")]
        self.assertGreaterEqual(len(attempt_events), 4, events)
        self.assertIn("runtime_attempt_created", [e["event_type"] for e in attempt_events])
        self.assertIn("runtime_attempt_completed", [e["event_type"] for e in attempt_events])
        for event in attempt_events:
            self.assertEqual(event.get("client_turn_id"), "turn-9", event)

    def test_callers_without_a_turn_stay_untagged(self) -> None:
        # Negative control: the retry dispatcher and the restart sweep call these functions with
        # no client turn in scope -- their events must stay untagged, never inherit or invent one.
        attempt = create_runtime_attempt(
            session_id="s-untagged-att", original_request="req", answer_mode="LIVE_DATA",
        )
        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")
        finalize_runtime_attempt(attempt["attempt_id"])
        events = list_runtime_session_events("s-untagged-att")
        self.assertGreaterEqual(len(events), 2)
        for event in events:
            self.assertNotIn("client_turn_id", event, event)

    def test_a_blank_turn_id_is_not_stamped(self) -> None:
        attempt = create_runtime_attempt(
            session_id="s-blank", original_request="req", answer_mode="LIVE_DATA", client_turn_id="   ",
        )
        finalize_runtime_attempt(attempt["attempt_id"], client_turn_id="")
        for event in list_runtime_session_events("s-blank"):
            self.assertNotIn("client_turn_id", event, event)


if __name__ == "__main__":
    unittest.main()
