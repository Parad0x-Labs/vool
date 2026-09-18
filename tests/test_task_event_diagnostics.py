"""Guard: a failure that reaches the UI must say WHY it failed.

The incident. One audit turn lost three provider lanes to the same prompt-budget rejection. For
each lane the router emitted, with the failure:

    reason="prompt_budget_exceeded", error_kind="prompt_shape",
    provider_health_recorded=False, prompt_budget={...13 keys of telemetry...}

The event that reached the browser carried exactly nine keys -- type, raw_type, stage, summary,
tool, status, model, seq, ts -- and not one of the four. The persisted ledger had kept all of them;
the loss was purely in this projection, which is a hand-written output dict, so a field survives
only if the constructor reads it. The user saw three failures and no cause, and recovering the
cause by hand took hours.

These tests drive `build_task_event` with the real emitted payloads (core/memory_first_router.py
lines ~1226-1238 and ~1256-1263) and with the real telemetry keys `core/prompt_budget.py` produces,
and pin both halves of the guarantee: the cause arrives, and nothing else does.
"""

from __future__ import annotations

import json
import unittest

from core.task_event_model import build_task_event

# The rejection telemetry as core/prompt_budget.py builds it, with the `error` key the rejecting
# adapter adds on top (adapters/openai_compatible_adapter.py) -- the free-text field that must
# never ride out to the browser.
REJECTED_BUDGET = {
    "num_ctx": 8192,
    "output_reserve_tokens": 2048,
    "available_prompt_tokens": 6144,
    "estimated_prompt_tokens_before": 31402,
    "estimated_prompt_tokens_after": 9211,
    "dropped_retrieved_messages": 3,
    "dropped_history_messages": 12,
    "dropped_context_summaries": 1,
    "dropped_memory": True,
    "retrieval_present": True,
    "grounding_dropped": True,
    "system_prompt_preserved": True,
    "status": "rejected",
    "error": "prompt_budget_exceeded: protected system prompt and current turn require 9211 "
    "tokens, but only 6144 are available (num_ctx=8192, output_reserve=2048)",
}


def prompt_budget_rejection(provider_id: str = "openrouter", model_id: str = "anthropic/claude-x") -> dict:
    """One lane of the incident, exactly as the router emits it."""
    return {
        "event_type": "model.call_failed",
        "message": f"Prompt did not fit {provider_id}'s context window.",
        "provider_id": provider_id,
        "model_id": model_id,
        "lane": "cloud",
        "cost_class": "paid_cloud",
        "reason": "prompt_budget_exceeded",
        "error_kind": "prompt_shape",
        "provider_health_recorded": False,
        "prompt_budget": REJECTED_BUDGET,
        "seq": 41,
    }


class FailedCallCarriesItsCauseTests(unittest.TestCase):
    def test_a_failed_model_call_carries_its_reason_to_the_ui(self) -> None:
        event = build_task_event(prompt_budget_rejection())
        diagnostics = event["diagnostics"]
        self.assertEqual(diagnostics["reason"], "prompt_budget_exceeded")
        self.assertEqual(diagnostics["error_kind"], "prompt_shape")

    def test_the_three_lanes_of_the_incident_each_name_their_own_provider_and_cause(self) -> None:
        # The turn that started this: three lanes, three failures, one cause each. Reading the
        # stream must be enough to see that all three died the same way.
        lanes = [
            prompt_budget_rejection("openrouter", "anthropic/claude-x"),
            prompt_budget_rejection("groq", "llama-3.3-70b"),
            prompt_budget_rejection("ollama", "qwen3:8b"),
        ]
        events = [build_task_event(lane) for lane in lanes]
        self.assertEqual(
            [event["diagnostics"]["reason"] for event in events],
            ["prompt_budget_exceeded"] * 3,
        )
        self.assertEqual(
            [event["model"]["provider_id"] for event in events],
            ["openrouter", "groq", "ollama"],
        )

    def test_provider_health_recorded_false_survives_the_projection(self) -> None:
        # False is the whole point of the field: a prompt-shape rejection deliberately leaves the
        # provider's health untouched so a provider that is answering fine keeps its circuit
        # closed. A truthiness check would have dropped it and left the user reading a lane
        # failure that looks like a dead provider.
        event = build_task_event(prompt_budget_rejection())
        self.assertIn("provider_health_recorded", event["diagnostics"])
        self.assertIs(event["diagnostics"]["provider_health_recorded"], False)

    def test_the_prompt_budget_summary_says_what_did_not_fit(self) -> None:
        budget = build_task_event(prompt_budget_rejection())["diagnostics"]["prompt_budget"]
        self.assertEqual(budget["status"], "rejected")
        self.assertEqual(budget["num_ctx"], 8192)
        self.assertEqual(budget["available_prompt_tokens"], 6144)
        # The number that explains the rejection: 9211 still needed against 6144 available.
        self.assertEqual(budget["required_prompt_tokens"], 9211)

    def test_the_prompt_budget_summary_says_what_was_already_thrown_away(self) -> None:
        # Without these a reader cannot tell a prompt that is merely long from one that is
        # unshrinkable -- this lane had already shed 12 history messages, 3 retrieved blocks, a
        # summary and the memory prefix, and STILL did not fit.
        budget = build_task_event(prompt_budget_rejection())["diagnostics"]["prompt_budget"]
        self.assertEqual(budget["dropped_history_messages"], 12)
        self.assertEqual(budget["dropped_retrieved_messages"], 3)
        self.assertEqual(budget["dropped_context_summaries"], 1)
        self.assertIs(budget["dropped_memory"], True)
        self.assertIs(budget["system_prompt_preserved"], True)

    def test_the_gateways_token_field_name_is_understood_too(self) -> None:
        # core/provider_invocation_gateway.py names the same number `final_input_tokens`; a
        # summary that only knew one spelling would show a rejection with nothing to compare.
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "prompt_budget": {"status": "rejected", "available_prompt_tokens": 4000, "final_input_tokens": 7777},
            }
        )
        self.assertEqual(event["diagnostics"]["prompt_budget"]["required_prompt_tokens"], 7777)


class NothingElseRidesAlongTests(unittest.TestCase):
    def test_the_whole_telemetry_dict_is_not_dumped_into_the_event(self) -> None:
        # A dict merge would have carried `error` (raw exception text) plus every key added to the
        # telemetry later. Copying is by allowlist, so unknown keys cannot arrive by accident.
        budget = build_task_event(prompt_budget_rejection())["diagnostics"]["prompt_budget"]
        self.assertNotIn("error", budget)
        self.assertNotIn("estimated_prompt_tokens_before", budget)
        self.assertNotIn("retrieval_present", budget)
        self.assertLess(len(budget), len(REJECTED_BUDGET))

    def test_a_future_telemetry_key_cannot_reach_the_browser(self) -> None:
        # The failure mode this pins: someone adds a key to the budget telemetry that holds prompt
        # text, and it ships to every browser because the projection copied the dict wholesale.
        leaky = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "prompt_budget": {**REJECTED_BUDGET, "trimmed_text": "the user's private prompt body"},
            }
        )
        self.assertNotIn("private prompt body", json.dumps(leaky))
        self.assertNotIn("trimmed_text", leaky["diagnostics"]["prompt_budget"])

    def test_a_reason_carrying_an_api_key_is_masked_before_it_reaches_the_browser(self) -> None:
        # The router's general failure branch emits `reason=str(exc)`, and a provider's 401 body
        # quotes the key it rejected.
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "Model call failed with openrouter.",
                "reason": "401 Unauthorized: incorrect api key provided: sk-or-v1-" + "a" * 40,
            }
        )
        reason = event["diagnostics"]["reason"]
        self.assertNotIn("sk-or-v1", reason)
        self.assertIn("401", reason)  # the part a user can act on stays

    def test_a_reason_carrying_the_endpoint_url_drops_the_url(self) -> None:
        # `response.raise_for_status()` interpolates the URL verbatim, query string included, and
        # a proxy base_url can carry the credential there. The lane already names the provider.
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "reason": "Client error '429 Too Many Requests' for url "
                "'https://gateway.example.com/v1/chat/completions?access_token=abcd1234efgh5678'",
            }
        )
        reason = event["diagnostics"]["reason"]
        self.assertNotIn("gateway.example.com", reason)
        self.assertNotIn("abcd1234efgh5678", reason)
        self.assertIn("429", reason)

    def test_a_reason_carrying_an_operator_path_keeps_only_the_filename(self) -> None:
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "reason": "FileNotFoundError: /Users/operator/Desktop/vool/models/qwen3.gguf",
            }
        )
        reason = event["diagnostics"]["reason"]
        self.assertNotIn("operator", reason)
        self.assertIn("qwen3.gguf", reason)  # still names what was missing

    def test_a_windows_operator_path_is_reduced_too(self) -> None:
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "reason": r"OSError: cannot open C:\Users\kas\AppData\vool\model.bin",
            }
        )
        reason = event["diagnostics"]["reason"]
        self.assertNotIn("AppData", reason)
        self.assertNotIn(r"C:\Users", reason)
        self.assertIn("model.bin", reason)

    def test_a_slashed_model_id_in_a_reason_is_left_intact(self) -> None:
        # The path rule must not bite the one identifier a reader needs: "openrouter/anthropic/
        # claude-x" is not a filesystem path.
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "reason": "no endpoints found for openrouter/anthropic/claude-x",
            }
        )
        self.assertIn("openrouter/anthropic/claude-x", event["diagnostics"]["reason"])

    def test_a_reason_is_one_bounded_line(self) -> None:
        # A stack trace or a multi-kilobyte provider body must not become the status card.
        event = build_task_event(
            {
                "event_type": "model.call_failed",
                "message": "failed",
                "reason": "Traceback (most recent call last):\n  File x, line 1\n" + "y" * 4000,
            }
        )
        reason = event["diagnostics"]["reason"]
        self.assertNotIn("\n", reason)
        self.assertLessEqual(len(reason), 200)

    def test_an_error_kind_is_bounded_too(self) -> None:
        event = build_task_event(
            {"event_type": "model.call_failed", "message": "failed", "error_kind": "k" * 500}
        )
        self.assertLessEqual(len(event["diagnostics"]["error_kind"]), 64)

    def test_a_prompt_budget_that_is_not_a_dict_is_ignored(self) -> None:
        for junk in ("rejected", 7, ["rejected"], None, {}):
            event = build_task_event(
                {"event_type": "model.call_failed", "message": "failed", "prompt_budget": junk}
            )
            self.assertIsNone(event.get("diagnostics"), f"prompt_budget={junk!r} produced a block")


class EveryFailureShapedLaneReportsItsCauseTests(unittest.TestCase):
    def test_failures_other_than_model_call_failed_carry_their_cause(self) -> None:
        # The projection is shared by every lane, so the guarantee is "a failure states its
        # cause", not "prompt-budget rejections state their cause".
        failing_types = [
            "model.call_failed",
            "model.paid_failed",
            "tool_failed",
            "task_failed",
            "model_lane_verifier_failed",
            "permission_denied",
        ]
        for event_type in failing_types:
            with self.subTest(event_type=event_type):
                event = build_task_event(
                    {"event_type": event_type, "message": "it stopped", "reason": "context_window_exhausted"}
                )
                self.assertEqual(event["diagnostics"]["reason"], "context_window_exhausted")

    def test_a_lane_that_stays_running_while_it_repairs_still_reports_its_cause(self) -> None:
        # model_lane_failed / model_lane_contract_failed keep status "running" because the router
        # expects to recover on the next lane. The user still watched a lane die, so the cause is
        # owed -- gating on status alone would have lost exactly these.
        for event_type in ("model_lane_failed", "model_lane_contract_failed"):
            with self.subTest(event_type=event_type):
                event = build_task_event(
                    {
                        "event_type": event_type,
                        "message": "lane failed",
                        "reason": "provider_circuit_open",
                        "error_kind": "provider_health",
                    }
                )
                self.assertEqual(event["status"], "running")
                self.assertEqual(event["stage"], "Repairing")
                self.assertEqual(event["diagnostics"]["error_kind"], "provider_health")


class TheExistingShapeIsUntouchedTests(unittest.TestCase):
    def test_a_successful_call_carries_no_diagnostics_block(self) -> None:
        # model.call_completed also emits `prompt_budget`. A success has no cause to report, and
        # every extra field on the hot path is a field that can leak.
        event = build_task_event(
            {
                "event_type": "model.call_completed",
                "message": "Model call completed with openrouter.",
                "provider_id": "openrouter",
                "prompt_budget": {**REJECTED_BUDGET, "status": "trimmed"},
            }
        )
        self.assertNotIn("diagnostics", event)

    def test_a_failure_with_no_cause_gets_no_empty_block(self) -> None:
        event = build_task_event({"event_type": "task_failed", "message": "Stopped safely"})
        self.assertNotIn("diagnostics", event)

    def test_the_keys_of_a_non_failure_event_are_unchanged(self) -> None:
        event = build_task_event(
            {"event_type": "tool_selected", "tool_name": "web_research", "summary": "Searching the web"}
        )
        self.assertEqual(
            set(event),
            {"type", "raw_type", "stage", "summary", "tool", "status"},
        )

    def test_a_failure_event_keeps_every_field_it_already_had(self) -> None:
        event = build_task_event(prompt_budget_rejection())
        self.assertEqual(event["type"], "model.call_failed")
        self.assertEqual(event["raw_type"], "model.call_failed")
        self.assertEqual(event["stage"], "Repairing")
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["summary"], "Prompt did not fit openrouter's context window.")
        self.assertEqual(event["model"]["model_id"], "claude-x")
        self.assertTrue(event["model"]["paid"])
        self.assertEqual(event["seq"], 41)

    def test_the_event_is_json_serialisable_for_the_ndjson_channel(self) -> None:
        # It ships as one `vool_event` NDJSON line; a non-serialisable value would break the
        # stream for the whole turn, not just this event.
        line = json.dumps({"vool_event": build_task_event(prompt_budget_rejection())})
        self.assertNotIn("\n", line)
        self.assertEqual(
            json.loads(line)["vool_event"]["diagnostics"]["prompt_budget"]["required_prompt_tokens"],
            9211,
        )

    def test_the_allowlist_still_drops_unknown_events(self) -> None:
        self.assertIsNone(build_task_event({"event_type": "internal_scratchpad", "reason": "chain of thought"}))


if __name__ == "__main__":
    unittest.main()
