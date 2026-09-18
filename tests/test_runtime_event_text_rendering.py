"""Renderer guarantees for the untyped runtime-event text channel.

Incident this pins: an audit turn failed on three provider lanes. For each lane the router
emitted `model.call_failed` carrying reason="prompt_budget_exceeded", error_kind="prompt_shape"
and the prompt_budget telemetry (num_ctx / available_prompt_tokens /
estimated_prompt_tokens_before). What reached the user was three copies of the bare sentence
"Prompt did not fit ...'s context window." and nothing else. The persisted ledger kept every
field; the loss was in the projection.

Two independent faults produced that, both measured against the code before the fix:

1. `format_runtime_event_text` gated its allowlist renderer on `event_type.startswith("model_")`.
   The lane/routing family is underscored (`model_lane_proof`, a749073, 2026-06-18); the per-call
   family is dotted (`model.call_failed`, 496ce19, 2026-07-14) and core/cloud_broker.py emits the
   dotted names as well. `"model.call_failed".startswith("model_")` is False, so all four dotted
   types skipped the renderer entirely and fell through to the bare-message tail.
2. The allowlist permitted rejection_reason / failure_reason / error but not reason, error_kind or
   prompt_budget, so even renamed to an underscored type the event still lost its cause.
"""

from __future__ import annotations

import json
import unittest

from core.runtime_task_events import emit_runtime_event, reset_runtime_event_state
from core.web.api.runtime import format_runtime_event_text, stream_agent_with_events

# The exact payload the router builds at core/memory_first_router.py:1226 when the prompt budget
# guard rejects a turn, minus the identity fields that vary per run.
PROMPT_BUDGET_FAILURE = {
    "event_type": "model.call_failed",
    "message": "Prompt did not fit anthropic-byok's context window.",
    "provider_id": "anthropic-byok",
    "model_id": "claude-sonnet-4",
    "reason": "prompt_budget_exceeded",
    "error_kind": "prompt_shape",
    "provider_health_recorded": False,
    "prompt_budget": {
        "num_ctx": 8192,
        "available_prompt_tokens": 6144,
        "estimated_prompt_tokens_before": 11803,
        "estimated_prompt_tokens_after": 11803,
        "grounding_dropped": True,
        "status": "rejected",
        "dropped_memory": 4,
        "retrieval_present": True,
        "system_prompt_preserved": True,
    },
}


def rendered_payload(text: str) -> dict:
    """Decode one VOOL_RUNTIME_EVENT line back into the dict a client would parse."""
    prefix = "VOOL_RUNTIME_EVENT "
    assert text.startswith(prefix), f"not a rendered runtime event line: {text!r}"
    return json.loads(text[len(prefix) :])


class RuntimeEventTextRenderingTests(unittest.TestCase):
    def test_a_failed_model_call_carries_its_reason_to_the_ui(self) -> None:
        payload = rendered_payload(format_runtime_event_text(dict(PROMPT_BUDGET_FAILURE)))

        self.assertEqual(payload["reason"], "prompt_budget_exceeded")
        self.assertEqual(payload["error_kind"], "prompt_shape")
        self.assertEqual(payload["provider_id"], "anthropic-byok")
        self.assertEqual(payload["event_type"], "model.call_failed")

    def test_a_dotted_model_event_reaches_the_renderer_at_all(self) -> None:
        # The whole regression in one assertion: before the fix this returned the bare message
        # line "Prompt did not fit anthropic-byok's context window.\n".
        text = format_runtime_event_text(dict(PROMPT_BUDGET_FAILURE))

        self.assertTrue(text.startswith("VOOL_RUNTIME_EVENT "))
        self.assertTrue(text.endswith("\n"))

    def test_every_dotted_per_call_event_type_is_rendered(self) -> None:
        # The four dotted types the router and core/cloud_broker.py emit today. A new one must
        # not be able to slip back under the prefix test.
        for event_type in (
            "model.call_started",
            "model.call_completed",
            "model.call_failed",
            "model.response_constraint_retry",
        ):
            with self.subTest(event_type=event_type):
                text = format_runtime_event_text(
                    {"event_type": event_type, "message": "x", "provider_id": "ollama-local", "model_id": "qwen3:8b"}
                )
                self.assertTrue(text.startswith("VOOL_RUNTIME_EVENT "), event_type)
                self.assertEqual(rendered_payload(text)["model_id"], "qwen3:8b")

    def test_a_dotted_event_renders_the_same_fields_as_its_underscored_sibling(self) -> None:
        dotted = rendered_payload(format_runtime_event_text(dict(PROMPT_BUDGET_FAILURE)))
        underscored = rendered_payload(
            format_runtime_event_text({**PROMPT_BUDGET_FAILURE, "event_type": "model_routing_failed"})
        )

        self.assertEqual(
            {key: value for key, value in dotted.items() if key != "event_type"},
            {key: value for key, value in underscored.items() if key != "event_type"},
        )

    def test_the_underscored_lane_family_keeps_rendering(self) -> None:
        # Widening the prefix test must not cost the family that already worked.
        text = format_runtime_event_text(
            {
                "event_type": "model_lane_proof",
                "message": "lane proof",
                "schema": "vool.model_lane_proof.v1",
                "lane": "deep",
                "mismatch": True,
                "failure_reason": "planned_adapter_mismatch",
            }
        )
        payload = rendered_payload(text)

        self.assertEqual(payload["schema"], "vool.model_lane_proof.v1")
        self.assertEqual(payload["failure_reason"], "planned_adapter_mismatch")
        self.assertTrue(payload["mismatch"])

    def test_the_budget_numbers_that_explain_the_rejection_survive(self) -> None:
        budget = rendered_payload(format_runtime_event_text(dict(PROMPT_BUDGET_FAILURE)))["prompt_budget"]

        # The window, the allowance and the demand: 11803 tokens needed against 6144 available in
        # an 8192 window is the whole diagnosis the audit turn had to be traced by hand to reach.
        self.assertEqual(budget["num_ctx"], 8192)
        self.assertEqual(budget["available_prompt_tokens"], 6144)
        self.assertEqual(budget["estimated_prompt_tokens_before"], 11803)
        self.assertEqual(budget["status"], "rejected")
        self.assertTrue(budget["grounding_dropped"])

    def test_the_budget_dict_is_projected_rather_than_passed_through(self) -> None:
        # core/prompt_budget.py owns that dict and builds 13 keys. Only the named ones belong on a
        # one-line text event, so a key added upstream cannot reach a user's screen unreviewed.
        budget = rendered_payload(format_runtime_event_text(dict(PROMPT_BUDGET_FAILURE)))["prompt_budget"]

        self.assertNotIn("dropped_memory", budget)
        self.assertNotIn("retrieval_present", budget)
        self.assertNotIn("system_prompt_preserved", budget)

    def test_an_unvetted_budget_key_never_reaches_the_line(self) -> None:
        text = format_runtime_event_text(
            {
                **PROMPT_BUDGET_FAILURE,
                "prompt_budget": {"num_ctx": 4096, "dropped_message_preview": "PRIVATE-TRANSCRIPT"},
            }
        )

        self.assertNotIn("PRIVATE-TRANSCRIPT", text)
        self.assertNotIn("dropped_message_preview", text)
        self.assertEqual(rendered_payload(text)["prompt_budget"], {"num_ctx": 4096})

    def test_a_call_that_failed_without_budget_telemetry_omits_the_key(self) -> None:
        # circuit_open and health-check failures (core/memory_first_router.py:992, :1019) carry no
        # budget at all; an empty dict on the line would read as "measured, and it was nothing".
        payload = rendered_payload(
            format_runtime_event_text(
                {
                    "event_type": "model.call_failed",
                    "message": "Model call failed with ollama-local.",
                    "provider_id": "ollama-local",
                    "model_id": "qwen3:8b",
                    "reason": "circuit_open",
                    "prompt_budget": {},
                }
            )
        )

        self.assertEqual(payload["reason"], "circuit_open")
        self.assertNotIn("prompt_budget", payload)

    def test_a_private_field_on_a_dotted_event_is_still_scrubbed(self) -> None:
        # Widening the prefix test brings dotted events under the allowlist for the first time;
        # the allowlist must still be the thing deciding what leaves.
        text = format_runtime_event_text(
            {**PROMPT_BUDGET_FAILURE, "private_path": "/Users/someone/keys", "api_key": "sk-live-abc"}
        )

        self.assertNotIn("private_path", text)
        self.assertNotIn("/Users/someone/keys", text)
        self.assertNotIn("sk-live-abc", text)

    def test_streamed_model_output_is_still_raw_text(self) -> None:
        # The answer itself must never be wrapped in an event envelope, and it must not gain a
        # trailing newline per chunk.
        self.assertEqual(
            format_runtime_event_text({"event_type": "model_output_chunk", "message": "hello"}),
            "hello",
        )

    def test_a_non_model_event_still_renders_as_a_plain_message_line(self) -> None:
        self.assertEqual(
            format_runtime_event_text(
                {"event_type": "tool_selected", "message": "Running real tool workspace.read_file."}
            ),
            "Running real tool workspace.read_file.\n",
        )

    def test_an_empty_event_renders_nothing(self) -> None:
        self.assertEqual(format_runtime_event_text({}), "")
        self.assertEqual(format_runtime_event_text({"event_type": "status", "message": "   "}), "")


class ThreeLaneFailureStreamTests(unittest.TestCase):
    """Drive the real stream, because a formatter unit test alone did not catch this."""

    def setUp(self) -> None:
        reset_runtime_event_state()
        self.addCleanup(reset_runtime_event_state)

    def test_each_failed_lane_names_its_own_cause_in_the_stream(self) -> None:
        lanes = ("anthropic-byok", "openrouter-byok", "ollama-local")

        def fake_run_agent(_runtime, _user_text, *, session_id=None, source_context=None):
            stream_only = {"runtime_event_stream_id": str((source_context or {}).get("runtime_event_stream_id") or "")}
            for provider_id in lanes:
                emit_runtime_event(
                    stream_only,
                    event_type="model.call_failed",
                    message=f"Prompt did not fit {provider_id}'s context window.",
                    details={
                        "provider_id": provider_id,
                        "model_id": "audit-model",
                        "reason": "prompt_budget_exceeded",
                        "error_kind": "prompt_shape",
                        "prompt_budget": {"num_ctx": 8192, "available_prompt_tokens": 6144, "status": "rejected"},
                    },
                )
            return {"response": "Every lane refused the prompt."}

        # A7 W3/W4: progress and failures travel ONLY on the typed `vool_event` channel; the
        # content channel is the committed answer bytes. Asking for runtime events (the legacy
        # flag) is served there -- the flag must not be accepted and then do nothing.
        chunks = list(
            stream_agent_with_events(
                None,
                "audit the tooling surface",
                session_id="openclaw:test",
                source_context={"conversation_history": []},
                model="vool",
                include_runtime_events=True,
                run_agent_provider=fake_run_agent,
            )
        )

        payloads = [
            json.loads(line)
            for line in b"".join(chunks).decode("utf-8").splitlines()
            if line.strip()
        ]
        streamed = "".join(
            str((payload.get("message") or {}).get("content") or "") for payload in payloads if "message" in payload
        )
        failures = [
            event
            for event in (payload.get("vool_event") for payload in payloads)
            if isinstance(event, dict) and event.get("raw_type") == "model.call_failed"
        ]

        self.assertEqual([event["model"]["provider_id"] for event in failures], list(lanes))
        for event in failures:
            self.assertEqual(event["status"], "failed")
            self.assertEqual(event["diagnostics"]["reason"], "prompt_budget_exceeded")
            self.assertEqual(event["diagnostics"]["error_kind"], "prompt_shape")
            self.assertEqual(event["diagnostics"]["prompt_budget"]["available_prompt_tokens"], 6144)
        self.assertIn("Every lane refused the prompt.", streamed)
        self.assertNotIn("VOOL_RUNTIME_EVENT", streamed)


if __name__ == "__main__":
    unittest.main()
