"""Agent-node rows must reach the typed channel and the companion classifier (P0).

Root defect (audit 2026-08-31): ``_DIRECT_MAP`` had no rows for ``agent_node_started`` /
``agent_node_completed``, so ``build_task_event`` returned ``None`` for the very events that
carry the lane nodes' real work (weather_lookup, market_quote, ...) -- emitted live around the
actual fetch (``core/agent_runtime/live_data_runner.py``, ``core/conductor/scheduler.py``) and
already routed onto the streaming ``vool_event`` channel. The served companion's classifier
has a dedicated arm for these frames; it simply never received one.

The row contract pinned here is the one both lanes emit:

* ``agent_node_started``  -- schema, lane, node_id, operation, depends_on, started_at_iso
* ``agent_node_completed``-- the above plus state, ok, failure_reason, result_fields,
  rendered, rendered_truncated, started_at_iso, completed_at_iso, duration_s

Only the typed frame's contract keys may survive; raw ledger fields (node ids, plan ids,
rendered output, failure tracebacks, timing stamps) must never ride the UI channel.
"""

from __future__ import annotations

import json
import unittest

from core.task_event_model import build_task_event


def _started_row(**overrides):
    """Real live_data lane shape (session openclaw-453c…, seq 8)."""
    row = {
        "event_type": "agent_node_started",
        "message": "livedata-6ba63565a316:weather:london started (weather_lookup)",
        "schema": "agent_node_started_v1",
        "lane": "live_data",
        "node_id": "livedata-6ba63565a316:weather:london",
        "operation": "weather_lookup",
        "depends_on": [],
        "started_at_iso": "2026-08-30T17:04:14.150417+00:00",
        "seq": 8,
        "client_turn_id": "a5fd25a0-1f0b-4ace-86bc-0d347efbfd72",
        "turn_key": "a5fd25a0-1f0b-4ace-86bc-0d347efbfd72",
    }
    row.update(overrides)
    return row


def _completed_row(**overrides):
    """Real live_data lane shape (session openclaw-453c…, seq 11), succeeded."""
    row = {
        "event_type": "agent_node_completed",
        "message": "livedata-6ba63565a316:weather:london succeeded in 0.4s",
        "schema": "agent_node_completed_v1",
        "lane": "live_data",
        "node_id": "livedata-6ba63565a316:weather:london",
        "operation": "weather_lookup",
        "state": "succeeded",
        "ok": True,
        "failure_reason": "",
        "result_fields": ["temperature_c", "conditions"],
        "rendered": "",
        "rendered_truncated": False,
        "started_at_iso": "2026-08-30T17:04:14.150417+00:00",
        "completed_at_iso": "2026-08-30T17:04:14.506677+00:00",
        "duration_s": 0.35625966699990386,
        "seq": 11,
        "client_turn_id": "a5fd25a0-1f0b-4ace-86bc-0d347efbfd72",
    }
    row.update(overrides)
    return row


def _failed_conductor_row(**overrides):
    """Conductor lane completion that failed (real field names from node_receipt)."""
    row = {
        "event_type": "agent_node_completed",
        "message": "conductor-b7cec50be660:quote:ny failed in 1.2s",
        "schema": "agent_node_completed_v1",
        "lane": "conductor",
        "plan_id": "conductor-b7cec50be660",
        "node_id": "conductor-b7cec50be660:quote:ny",
        "operation": "market_quote",
        "tool_intent": "fetch_market_quote",
        "depends_on": ["conductor-b7cec50be660:plan:ny"],
        "needs_generation": False,
        "state": "failed",
        "ok": False,
        "failure_code": "node_exception",
        "failure_reason": "ConnectTimeout: provider read timed out calling https://api.market.example/v1?key=sk-secret123",
        "failure_detail_full": "ConnectTimeout: provider read timed out calling https://api.market.example/v1?key=sk-secret123 after 30.0s",
        "failure_traceback": "Traceback (most recent call last): ...",
        "receipt_id": "rcpt-123",
        "rendered": "",
        "rendered_truncated": False,
        "started_at_iso": "2026-08-30T16:39:44.000000+00:00",
        "completed_at_iso": "2026-08-30T16:39:45.200000+00:00",
        "duration_s": 1.2,
        "seq": 12,
    }
    row.update(overrides)
    return row


# Every raw row field that is NOT part of the typed frame contract. If any of these
# appears on a frame, the allowlist boundary has been breached.
RAW_ONLY_FIELDS = [
    "schema", "lane", "node_id", "plan_id", "depends_on", "needs_generation",
    "tool_intent", "started_at_iso", "completed_at_iso", "duration_s",
    "result_fields", "rendered", "rendered_truncated", "state", "ok",
    "failure_code", "failure_detail_full", "failure_traceback", "receipt_id",
    "client_turn_id", "turn_key",
]


class AgentNodeFrameTests(unittest.TestCase):
    def test_agent_node_started_row_builds_a_frame(self) -> None:
        ev = build_task_event(_started_row())
        self.assertIsNotNone(ev, "agent_node_started is real execution work; dropping it starved the companion")
        self.assertEqual(ev["type"], "agent_node_started")
        self.assertEqual(ev["status"], "running")

    def test_agent_node_completed_row_builds_a_frame(self) -> None:
        ev = build_task_event(_completed_row())
        self.assertIsNotNone(ev, "agent_node_completed is real execution work; dropping it starved the companion")
        self.assertEqual(ev["type"], "agent_node_completed")
        self.assertEqual(ev["status"], "completed")

    def test_raw_type_preserved_exactly(self) -> None:
        self.assertEqual(build_task_event(_started_row())["raw_type"], "agent_node_started")
        self.assertEqual(build_task_event(_completed_row())["raw_type"], "agent_node_completed")

    def test_operation_rides_the_canonical_tool_field(self) -> None:
        """The classifier consumes ``ev.operation || ev.tool`` through the tool-rule table;
        the canonical frame field is ``tool``, and it must carry the node's operation."""
        started = build_task_event(_started_row())
        self.assertEqual(started["tool"], "weather_lookup")
        done = build_task_event(_completed_row(operation="market_quote"))
        self.assertEqual(done["tool"], "market_quote")

    def test_stage_derives_from_the_operation(self) -> None:
        started = build_task_event(_started_row())
        self.assertEqual(started["stage"], "Searching")  # weather_lookup -> lookup needle

    def test_seq_identity_survives(self) -> None:
        ev = build_task_event(_started_row())
        self.assertEqual(ev["seq"], 8)

    def test_summary_is_the_curated_message(self) -> None:
        ev = build_task_event(_started_row())
        self.assertEqual(ev["summary"], "livedata-6ba63565a316:weather:london started (weather_lookup)")

    def test_failed_node_is_not_success_and_names_the_cause(self) -> None:
        """Req 5: a failed node must never read as a successful completion, and the frame
        must carry the safe diagnostic cause the row already holds."""
        ev = build_task_event(_failed_conductor_row())
        self.assertIsNotNone(ev)
        self.assertEqual(ev["status"], "failed")
        diag = ev.get("diagnostics") or {}
        self.assertTrue(diag.get("reason"), "a failed node owes its cause")
        # sanitizer boundary holds: no URL, no credential, no traceback text
        self.assertNotIn("https://", diag["reason"])
        self.assertNotIn("sk-secret123", diag["reason"])
        self.assertNotIn("failure_traceback", ev)
        self.assertNotIn("failure_detail_full", ev)

    def test_cancelled_and_never_attempted_nodes_are_not_success(self) -> None:
        for state in ("cancelled", "skipped", "dependency_failed"):
            ev = build_task_event(_completed_row(state=state))
            self.assertIsNotNone(ev, state)
            self.assertNotEqual(
                ev["status"], "completed", f"state={state} must never read as a successful completion"
            )

    def test_unknown_node_state_makes_no_success_claim(self) -> None:
        ev = build_task_event(_completed_row(state="something_new"))
        if ev is not None:
            self.assertNotEqual(ev["status"], "completed")

    def test_frames_carry_only_contract_keys(self) -> None:
        """Req 6: the allowlist/redaction boundary -- raw ledger payload must not leak."""
        for row in (_started_row(), _completed_row(), _failed_conductor_row()):
            ev = build_task_event(row)
            self.assertIsNotNone(ev)
            for field in RAW_ONLY_FIELDS:
                self.assertNotIn(field, ev, f"raw field {field!r} leaked onto the UI channel")

    def test_existing_rows_unchanged_byte_shape(self) -> None:
        """Req 7: the additive rows must not perturb the existing mappings."""
        ev = build_task_event({"event_type": "tool_selected", "tool_name": "web_research", "summary": "s"})
        self.assertEqual(
            list(ev.keys()),
            ["type", "raw_type", "stage", "summary", "tool", "status"],
            "tool_selected frame shape is load-bearing for existing consumers",
        )


class AgentNodeCompanionClassificationTests(unittest.TestCase):
    """The frames must classify through the REAL served companion classifier under node."""

    def test_frames_classify_web_research_through_the_served_companion(self) -> None:
        import re
        import shutil

        from core.companion_presentation_fragment import render_companion_fragment
        from tests.chat_page_js_harness import DOM, run_node

        frames = [
            build_task_event(_started_row()),
            build_task_event(_completed_row()),
            build_task_event(_completed_row(operation="market_quote")),
        ]
        self.assertTrue(all(f is not None for f in frames), "frames must exist before they can classify")

        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        fragment = render_companion_fragment()
        script_body = re.findall(r"<script[^>]*>(.*?)</script>", fragment, re.DOTALL)
        self.assertTrue(script_body, "fragment must carry one script")
        program = (
            DOM
            + "\n;(function(){\n"
            + script_body[0]
            + "\nconst FRAMES = "
            + json.dumps(frames)
            + """;
// The stub's exit report merges globalThis.__out into its single stdout JSON line.
globalThis.__out = {
  frames: FRAMES.map((f) => ({ type: f.type, tool: f.tool, category: window.VoolCompanion.classify(f) })),
};
"""
            + "\n})();\n"
        )
        result = run_node(program, timeout=90)
        categories = [row["category"] for row in result["frames"]]
        self.assertEqual(
            categories,
            ["WEB_RESEARCH", "WEB_RESEARCH", "WEB_RESEARCH"],
            "node operations are remote retrievals; the served classifier must say so",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
