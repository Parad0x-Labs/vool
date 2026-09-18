"""Unit tests for the typed task-event model that drives the live /chat status UI.

These lock the two invariants that matter: (1) raw runtime events map to the
right typed stage/type, and (2) the channel is allowlist-only so no answer text
or unknown/internal event can leak into the status UI.
"""

from __future__ import annotations

import unittest

from core.task_event_model import build_task_event, stage_for_tool


class StageForToolTests(unittest.TestCase):
    def test_tool_names_map_to_stages(self) -> None:
        self.assertEqual(stage_for_tool("web_research"), "Searching")
        self.assertEqual(stage_for_tool("workspace.search_text"), "Searching")
        self.assertEqual(stage_for_tool("fs.read_file"), "Reading")
        self.assertEqual(stage_for_tool("fs.write_file"), "Editing")
        self.assertEqual(stage_for_tool("apply_patch"), "Editing")
        self.assertEqual(stage_for_tool("pytest_runner"), "Testing")
        self.assertEqual(stage_for_tool("shell.start_server"), "Running")
        self.assertEqual(stage_for_tool("something_unmapped"), "Running")


class BuildTaskEventTests(unittest.TestCase):
    def test_tool_selected_becomes_tool_started_with_stage(self) -> None:
        ev = build_task_event(
            {"event_type": "tool_selected", "tool_name": "web_research", "summary": "Searching the web"}
        )
        self.assertEqual(ev["type"], "tool.started")
        self.assertEqual(ev["stage"], "Searching")
        self.assertEqual(ev["status"], "running")
        self.assertEqual(ev["tool"], "web_research")
        self.assertEqual(ev["summary"], "Searching the web")

    def test_tool_executed_and_failed(self) -> None:
        done = build_task_event({"event_type": "tool_executed", "tool_name": "fs.write_file", "summary": "Wrote 3 files"})
        self.assertEqual(done["type"], "tool.completed")
        self.assertEqual(done["stage"], "Editing")
        self.assertEqual(done["status"], "completed")
        failed = build_task_event({"event_type": "tool_failed", "tool_name": "pytest_runner", "message": "1 test failed"})
        self.assertEqual(failed["type"], "tool.failed")
        self.assertEqual(failed["stage"], "Repairing")
        self.assertEqual(failed["status"], "failed")

    def test_permission_required(self) -> None:
        ev = build_task_event({"event_type": "tool_preview", "tool_name": "shell.run", "message": "Approval required for shell.run"})
        self.assertEqual(ev["type"], "permission.required")
        self.assertEqual(ev["stage"], "Waiting for permission")
        self.assertEqual(ev["status"], "pending_approval")

    def test_model_changed_marks_paid_lane(self) -> None:
        ev = build_task_event(
            {
                "event_type": "model_lane_selected",
                "lane": "cloud",
                "cost_class": "paid_cloud",
                "provider_id": "openrouter",
                "model_id": "anthropic/claude-x",
                "message": "Paid specialist engaged",
            }
        )
        self.assertEqual(ev["type"], "model.changed")
        self.assertIn("model", ev)
        self.assertTrue(ev["model"]["paid"])
        self.assertEqual(ev["model"]["model_id"], "claude-x")
        self.assertEqual(ev["model"]["provider_id"], "openrouter")

    def test_local_lane_is_not_paid(self) -> None:
        ev = build_task_event(
            {"event_type": "model_lane_started", "lane": "local", "cost_class": "free_local", "model_id": "qwen3:0.6b"}
        )
        self.assertFalse(ev["model"]["paid"])
        self.assertEqual(ev["model"]["model_id"], "qwen3:0.6b")

    def test_model_event_keeps_requested_policy_selected_and_actual_identity_separate(self) -> None:
        ev = build_task_event(
            {
                "event_type": "model_lane_completed",
                "lane": "cloud",
                "provider_id": "recorded-provider",
                "model_id": "recorded/only-model",
                "requested_provider_id": "openrouter-request",
                "requested_model": "vendor/requested-model",
                "selected_provider_id": "openrouter-selected",
                "selected_model": "vendor/selected-model",
                "lane_proof": {
                    "planned_provider_id": "openrouter-policy",
                    "planned_model_id": "policy/model",
                    "actual_adapter_provider_id": "ollama-local:qwen3:8b",
                    "actual_adapter_model_id": "qwen3:8b",
                },
            }
        )

        model = ev["model"]
        self.assertEqual(model["provider_id"], "recorded-provider")
        self.assertEqual(model["model_id"], "only-model")
        self.assertEqual(model["requested_provider_id"], "openrouter-request")
        self.assertEqual(model["requested_model_id"], "vendor/requested-model")
        self.assertEqual(model["policy_provider_id"], "openrouter-policy")
        self.assertEqual(model["policy_model_id"], "policy/model")
        self.assertEqual(model["selected_provider_id"], "openrouter-selected")
        self.assertEqual(model["selected_model_id"], "vendor/selected-model")
        self.assertEqual(model["actual_adapter_provider_id"], "ollama-local:qwen3:8b")
        self.assertEqual(model["actual_adapter_model_id"], "qwen3:8b")
        self.assertNotIn("locality", model, "the cloud orchestration lane is not locality evidence")

    def test_recorded_identity_is_never_promoted_to_selected(self) -> None:
        ev = build_task_event(
            {
                "event_type": "model_lane_started",
                "provider_id": "recorded-only-p",
                "model_id": "recorded/only-m",
            }
        )

        model = ev["model"]
        self.assertEqual(model["provider_id"], "recorded-only-p")
        self.assertEqual(model["model_id"], "only-m")
        self.assertNotIn("selected_provider_id", model)
        self.assertNotIn("selected_model_id", model)

    def test_selected_and_actual_identity_require_their_own_fields(self) -> None:
        selected = build_task_event(
            {
                "event_type": "model_lane_selected",
                "selected_provider_id": "selected-p",
                "selected_model_id": "selected/m",
            }
        )["model"]
        actual = build_task_event(
            {
                "event_type": "model_lane_completed",
                "actual_adapter_provider_id": "actual-p",
                "actual_adapter_model_id": "actual/m",
            }
        )["model"]

        self.assertEqual(selected["selected_provider_id"], "selected-p")
        self.assertEqual(selected["selected_model_id"], "selected/m")
        self.assertIsNone(selected["provider_id"])
        self.assertIsNone(selected["model_id"])
        self.assertNotIn("actual_adapter_provider_id", selected)
        self.assertNotIn("actual_adapter_model_id", selected)
        self.assertEqual(actual["actual_adapter_provider_id"], "actual-p")
        self.assertEqual(actual["actual_adapter_model_id"], "actual/m")
        self.assertIsNone(actual["provider_id"])
        self.assertIsNone(actual["model_id"])
        self.assertNotIn("selected_provider_id", actual)
        self.assertNotIn("selected_model_id", actual)

    def test_partial_recorded_identity_does_not_invent_a_counterpart(self) -> None:
        provider_only = build_task_event(
            {"event_type": "model_lane_started", "provider_id": "provider-only"}
        )["model"]
        model_only = build_task_event(
            {"event_type": "model_lane_started", "model_id": "vendor/model-only"}
        )["model"]

        self.assertEqual(provider_only["provider_id"], "provider-only")
        self.assertIsNone(provider_only["model_id"])
        self.assertNotIn("selected_model_id", provider_only)
        self.assertEqual(model_only["model_id"], "model-only")
        self.assertIsNone(model_only["provider_id"])
        self.assertNotIn("selected_provider_id", model_only)

    def test_verified_free_cloud_lane_is_cloud_but_not_paid(self) -> None:
        ev = build_task_event(
            {
                "event_type": "model_lane_started",
                "lane": "human",
                "cost_class": "free_cloud",
                "provider_id": "openrouter-byok:nvidia/nemotron:free",
                "model_id": "nvidia/nemotron:free",
            }
        )
        self.assertFalse(ev["model"]["paid"])

        usage = build_task_event(
            {
                "event_type": "model_usage",
                "cost_class": "free_cloud",
                "usd_actual": 0.0,
                "output_tokens": 100,
            }
        )
        self.assertFalse(usage["cost"]["paid"])

    def test_cost_updated_carries_dollars_and_tokens(self) -> None:
        ev = build_task_event(
            {
                "event_type": "model_usage",
                "cost_class": "paid_cloud",
                "usd_actual": 0.0412,
                "output_tokens": 512,
                "prompt_tokens": 1200,
            }
        )
        self.assertEqual(ev["type"], "cloud.cost_updated")
        self.assertEqual(ev["cost"]["usd_actual"], 0.0412)
        self.assertEqual(ev["cost"]["output_tokens"], 512)
        self.assertTrue(ev["cost"]["paid"])

    def test_verification_events(self) -> None:
        started = build_task_event({"event_type": "model_lane_verifier_started", "message": "Verifying"})
        self.assertEqual(started["type"], "verification.started")
        self.assertEqual(started["stage"], "Verifying")
        cases = {
            "model_lane_verifier_started": ("running", "running"),
            "model_lane_verifier_completed": ("completed", "passed"),
            "model_lane_verifier_flagged": ("completed", "flagged"),
            "model_lane_verifier_blocked": ("blocked", "blocked"),
            "model_lane_verifier_degraded": ("degraded", "degraded"),
            "model_lane_verifier_failed": ("failed", "runtime_failed"),
        }
        for event_type, (status, review_state) in cases.items():
            with self.subTest(event_type=event_type):
                event = build_task_event({"event_type": event_type, "message": event_type})
                self.assertEqual(event["status"], status)
                self.assertEqual(event["review_state"], review_state)
        self.assertEqual(
            build_task_event({"event_type": "model_lane_verifier_flagged"})["type"],
            "verification.completed",
        )

    def test_terminal_events(self) -> None:
        self.assertEqual(build_task_event({"event_type": "task_completed"})["type"], "task.completed")
        self.assertEqual(build_task_event({"event_type": "task_failed"})["stage"], "Failed safely")
        self.assertEqual(build_task_event({"event_type": "task_interrupted"})["type"], "task.cancelled")

    def test_answer_stream_is_not_a_task_event(self) -> None:
        # The final answer deltas must never enter the status channel.
        self.assertIsNone(build_task_event({"event_type": "model_output_chunk", "message": "the answer text"}))

    def test_unknown_events_are_dropped_no_leak(self) -> None:
        # Allowlist-only: an unmapped/internal event yields nothing, so arbitrary
        # text (or hidden reasoning) can never reach the UI channel.
        self.assertIsNone(build_task_event({"event_type": "internal_scratchpad", "message": "secret chain of thought"}))
        self.assertIsNone(build_task_event({"event_type": "", "message": "x"}))
        self.assertIsNone(build_task_event({}))

    def test_file_path_and_measurable_counts_pass_through(self) -> None:
        ev = build_task_event(
            {"event_type": "tool_executed", "tool_name": "fs.write_file", "summary": "wrote", "path": "src/app.py"}
        )
        self.assertEqual(ev["path"], "src/app.py")
        counted = build_task_event(
            {"event_type": "tool_executed", "tool_name": "pytest", "summary": "run", "current": 38, "total": 42}
        )
        self.assertEqual(counted["measurable"], {"current": 38, "total": 42})

    def test_summary_is_single_line_and_clipped(self) -> None:
        long = "line one\nline two   with    spaces\t" + ("x" * 400)
        ev = build_task_event({"event_type": "tool_executed", "tool_name": "t", "summary": long})
        self.assertNotIn("\n", ev["summary"])
        self.assertLessEqual(len(ev["summary"]), 200)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Streaming output guard (leak class, fix 4).
#
# The shipped chat UI posts stream:true. On that path streamed chunks are unretractable and the
# guarded answer was discarded once any chunk had shipped, so the output guard had no effect on the
# surface real users actually see. These drive the REAL transport generator -- not the helper -- so
# they fail if the wiring is removed even while the unit tests stay green.
# ---------------------------------------------------------------------------

def _drive_transport(chunks):
    """Return the concatenated content the CLIENT would receive for these model chunks."""
    import json as _json

    from core.web.api import runtime as rt

    def fake_run_agent(_rt, _text, *, session_id, source_context):
        from core.runtime_task_events import emit_runtime_event

        for chunk in chunks:
            emit_runtime_event(source_context, event_type="model_output_chunk", message=chunk)
        return {"response": "".join(chunks), "usage_summary": {}}

    shown = ""
    for raw in rt.stream_agent_with_events(
        None,
        "go",
        session_id="openclaw:aaaabbbbccccdddd2222",
        model="test",
        source_context={"surface": "openclaw"},
        run_agent_provider=fake_run_agent,
    ):
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        for part in line.splitlines():
            part = part.strip()
            if not part.startswith("{"):
                continue
            try:
                obj = _json.loads(part)
            except Exception:
                continue
            shown += ((obj.get("message") or {}).get("content") or "")
    return shown


def test_streamed_tool_call_never_reaches_the_client():
    # The live incident, delivered the way a provider delivers it: the envelope is split across
    # chunks, so no single chunk is detectable on its own.
    shown = _drive_transport(
        [
            "Let me actually audit ",
            "the code now.\n\nExploring:\n\n",
            '{"tool": "ba',
            'sh", "args": {"comm',
            'and": "find . -name \'*.py\'"}}',
        ]
    )
    assert '"tool"' not in shown, "the tool call reached the client over the stream"
    assert "find ." not in shown
    assert "Let me actually audit the code now." in shown  # the real answer still arrives


def test_clean_prose_streams_through_unchanged():
    # The latency/correctness guarantee for every ordinary turn: nothing withheld, nothing lost.
    shown = _drive_transport(["Paris is ", "the capital ", "of France."])
    assert shown == "Paris is the capital of France."
