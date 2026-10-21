"""Auto completion reliability: a recoverable provider fault finishes the SAME turn.

Reproduced on 2026-10-02 against base dca4679f (all cases RED before the repair):

* A transient transport fault on the serving lane — HTTP 503/429, a dropped connection, a
  mid-stream disconnect — ended the turn as ``no_provider_available`` whenever no other
  candidate was eligible (single local model, or every candidate hit the same passing fault).
  The user had to repeat the request. The router's bounded same-model retry existed but its
  admission gate matched ``EMPTY_PROVIDER_RESPONSE`` only, so the transport classes the
  taxonomy itself marks retryable (``RETRYABLE_ERROR_CLASSES``) never reached it.
* The 429 an OpenAI-compatible provider actually sends — "429 Client Error: Too Many Requests"
  — classified as None (the marker table knows "rate limit"/"quota" but not the canonical
  status text, and the typed ``[retry_after=Ns]`` marker the adapter appends carried no class),
  so even a future consumer of ``is_retryable`` would have failed closed for real 429s.
* The typed Retry-After was parsed into the message but nothing on System A consumed it.
* The streamed twin of the retry wrapper had no transport retry at all — only the
  exhaustion-widening re-stream — so a mid-stream disconnect fell straight through.

The repair widens the EXISTING gate (no second ladder): transport-fault retry rides the same
``_EMPTY_RESPONSE_RETRY_POLICY`` attempt allowance, the same free-lane cost law, the same
cancellation check, and the enclosing turn's absolute deadline. Non-retryable failures (auth,
policy, malformed tool calls) stay honest refusals.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

import requests

from adapters.base_adapter import ModelRequest, ModelResponse, ModelStreamChunk
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.normalized_provider_result import ProviderErrorClass, classify_error_class
from core.tiered_context_loader import TieredContextResult
from storage.db import get_connection
from storage.migrations import run_migrations

# Exception shapes exactly as the OpenAI-compatible adapter raises them after
# _raise_for_status_with_cause (requests.HTTPError, status line + provider body excerpt, and
# the typed [retry_after=Ns] marker for 429) -- so the router decides on the same text a real
# loopback 503/429 produces.
FIFTY_THREE = requests.HTTPError(
    '503 Server Error: Service Unavailable for url: http://127.0.0.1:1234/v1/chat/completions'
    ' — provider said: {"error":"model overloaded"}'
)
FOUR_TWENTY_NINE = requests.HTTPError(
    '429 Client Error: Too Many Requests for url: http://127.0.0.1:1234/v1/chat/completions'
    ' — provider said: {"error":{"code":429}} [retry_after=2s]'
)
AUTH_REFUSAL = requests.HTTPError(
    '401 Client Error: Unauthorized for url: http://127.0.0.1:1234/v1/chat/completions'
    ' — provider said: {"error":{"message":"invalid API key"}}'
)


def _good_answer(provider_id: str, model: str) -> ModelResponse:
    return ModelResponse(
        output_text="The resilient topology uses regional coordinators.",
        confidence=0.8,
        usage={"prompt_tokens": 20, "completion_tokens": 12},
        provider_id=provider_id,
        model_name=model,
    )


class AutoTransientRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)
        self.persona = load_active_persona("default")
        self.interpretation = HumanInputInterpretation(
            raw_text="design swarm topology",
            normalized_text="design swarm topology",
            reconstructed_text="design swarm topology",
            intent_mode="request",
            topic_hints=["swarm"],
            reference_targets=[],
            understanding_confidence=0.72,
            quality_flags=[],
        )

    # ---------------------------------------------------------------- rig

    def _register_local(self, *, name: str = "local-qwen-http", model: str = "qwen-local", port: int = 1234):
        manifest = self.registry.register_manifest(
            {
                "provider_name": name,
                "model_name": model,
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": f"http://127.0.0.1:{port}"},
                "enabled": True,
                "metadata": {"orchestration_role": "drone"},
            }
        )
        from tests._authorship_certification import certify_for_authorship

        certify_for_authorship(manifest)
        return manifest

    def _context_result(self) -> TieredContextResult:
        result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(
                retrieval_confidence="low",
                swarm_metadata_consulted=False,
                cold_archive_opened=False,
            ),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        result.report.to_dict.return_value = {}
        result.report.total_tokens_used.return_value = 0
        result.assembled_context = lambda: "No strong local memory."
        return result

    def _adapter(self, manifest, *, text_outcomes=None, stream_outcomes=None):
        """A mock adapter at the registry build seam.

        ``text_outcomes``/``stream_outcomes`` are side_effect lists for the buffered and
        streamed task methods; each entry is an exception to raise or a ModelResponse to return.
        """
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.get_license_metadata.return_value = {
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
        }
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.supports_streaming.return_value = stream_outcomes is not None
        adapter.run_text_task.side_effect = list(text_outcomes or [])
        adapter.run_structured_task.side_effect = list(text_outcomes or [])
        if stream_outcomes is not None:
            adapter.stream_text_task.side_effect = [
                outcome if isinstance(outcome, Exception) else iter([_as_chunks(outcome)])
                for outcome in stream_outcomes
            ]
        return adapter

    def _resolve(self, manifests, adapters, *, turn_id: str, source_context=None):
        by_id = {m.provider_id: a for m, a in zip(manifests, adapters, strict=True)}
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=list(manifests),
        ), mock.patch.object(
            self.registry,
            "build_adapter",
            side_effect=lambda manifest: by_id[manifest.provider_id],
        ), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ):
            from core.task_router import classify, create_task_record

            task = create_task_record("design swarm topology with resilient regions")
            classification = classify(task.task_summary, context=self.interpretation.as_context())
            return self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context=source_context
                or {"surface": "api", "session_id": "auto-transient-suite", "turn_id": turn_id},
            )

    # ---------------------------------------------------------------- classification truth

    def test_a_real_429_message_classifies_as_rate_limit(self) -> None:
        self.assertIs(classify_error_class(FOUR_TWENTY_NINE), ProviderErrorClass.PROVIDER_RATE_LIMIT)

    def test_a_real_503_message_classifies_as_provider_internal(self) -> None:
        self.assertIs(classify_error_class(FIFTY_THREE), ProviderErrorClass.PROVIDER_INTERNAL_ERROR)

    def test_an_auth_refusal_stays_non_retryable(self) -> None:
        from core.normalized_provider_result import is_retryable

        self.assertIsNone(classify_error_class(AUTH_REFUSAL))
        self.assertFalse(is_retryable(classify_error_class(AUTH_REFUSAL)))

    # ---------------------------------------------------------------- D1: single-lane transient faults

    def test_auto_turn_survives_a_transient_503_on_the_only_lane(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(manifest, text_outcomes=[FIFTY_THREE, _good_answer(manifest.provider_id, manifest.model_name)])
        result = self._resolve([manifest], [adapter], turn_id="turn-503-once")
        self.assertTrue(result.used_model, msg=f"source={result.source} details={result.details}")
        self.assertIn("resilient topology", result.output_text or "")
        self.assertEqual(adapter.run_text_task.call_count, 2)

    def test_auto_turn_survives_a_transient_429_with_retry_after(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(manifest, text_outcomes=[FOUR_TWENTY_NINE, _good_answer(manifest.provider_id, manifest.model_name)])
        started = time.monotonic()
        result = self._resolve([manifest], [adapter], turn_id="turn-429-once")
        elapsed = time.monotonic() - started
        self.assertTrue(result.used_model, msg=f"source={result.source} details={result.details}")
        self.assertEqual(adapter.run_text_task.call_count, 2)
        # The provider asked for 2s; the same-turn wait honours it without giving the endpoint
        # the whole turn.
        self.assertLess(elapsed, 10.0)

    def test_auto_turn_survives_a_dropped_connection(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(
            manifest,
            text_outcomes=[
                requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected('remote end closed connection without response'))"),
                _good_answer(manifest.provider_id, manifest.model_name),
            ],
        )
        result = self._resolve([manifest], [adapter], turn_id="turn-conn-once")
        self.assertTrue(result.used_model, msg=f"source={result.source} details={result.details}")
        self.assertEqual(adapter.run_text_task.call_count, 2)

    # ---------------------------------------------------------------- D4: mid-stream disconnect

    def test_streamed_turn_survives_a_mid_stream_disconnect(self) -> None:
        """Driven at the same seam production planner sub-turns use (`_invoke_manifest`), with
        streaming actually selected: a request that does not defer, a live stream id, and an
        adapter that streams. The ordinary-chat guard defers real composer turns to the buffered
        lane by design; this is the streamed arm's own contract. The lane declares its per-call
        ceiling as VOOL's local lanes do: the retry shares that one window with the first call."""
        manifest = self._with_ceiling(self._register_local(), 30)

        def _failing_stream():
            yield ModelStreamChunk(delta_text="The resil")
            raise requests.exceptions.ConnectionError("peer closed the stream mid-flight")

        good = _good_answer(manifest.provider_id, manifest.model_name)
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.get_license_metadata.return_value = {"license_name": "Apache-2.0"}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.supports_streaming.return_value = True
        adapter.stream_text_task.side_effect = [_failing_stream(), _as_chunks_iter(good)]
        from core.task_router import create_task_record

        task = create_task_record("describe the resilient topology")
        with mock.patch.object(
            self.registry, "build_adapter", return_value=adapter
        ), mock.patch("core.memory_first_router.should_probe_health", return_value=False):
            built, response, error = self.router._invoke_manifest(
                manifest=manifest,
                request=ModelRequest(task_kind="chat", prompt="describe the resilient topology", output_mode="plain_text"),
                output_mode="plain_text",
                task=task,
                source_context={
                    "surface": "api",
                    "session_id": "auto-transient-suite",
                    "turn_id": "turn-stream-disconnect",
                    "runtime_event_stream_id": "stream-turn-stream-disconnect",
                },
            )
        self.assertIsNone(error, msg=error)
        self.assertIsNotNone(response)
        self.assertEqual(adapter.stream_text_task.call_count, 2, msg="the disconnect must be retried on the same model")
        self.assertIn("resilient topology", response.output_text)

    # ---------------------------------------------------------------- the retry shares the first call's window

    def _with_ceiling(self, manifest, seconds: float):
        return self.registry.register_manifest(
            {**manifest.model_dump(), "runtime_config": {**manifest.runtime_config, "timeout_seconds": seconds}}
        )

    def test_a_call_that_spent_its_whole_window_is_not_retried_for_a_second_one(self) -> None:
        """No turn deadline is bound on an ordinary turn: the per-call ceiling is the only bound.

        A timeout that used the whole ceiling leaves the pair no room, so the same model is not
        handed a second full window below the candidate loop's budget check.
        """
        from core.provider_call_deadline import effective_timeout_seconds, local_manifest_call_timeout_seconds

        manifest = self._with_ceiling(self._register_local(), 0.4)
        good = _good_answer(manifest.provider_id, manifest.model_name)
        calls: list[float] = []

        def _hang_until_the_ceiling(request, *_args, **_kwargs):
            calls.append(time.monotonic())
            if len(calls) > 1:
                return good
            # What the real adapter does: wait out the ceiling it computes for this request,
            # then raise the transport's read timeout.
            time.sleep(effective_timeout_seconds(request, local_manifest_call_timeout_seconds(manifest, 0.4)))
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=0.4)")

        adapter = self._adapter(manifest)
        adapter.run_text_task.side_effect = _hang_until_the_ceiling
        adapter.run_structured_task.side_effect = _hang_until_the_ceiling
        result = self._resolve([manifest], [adapter], turn_id="turn-timeout-window")
        self.assertEqual(len(calls), 1, msg="the timed-out call was re-run for a second full window")
        self.assertFalse(result.used_model)

    def test_a_fast_transport_fault_is_retried_inside_the_first_calls_window(self) -> None:
        from core.provider_call_deadline import PROVIDER_DEADLINE_KEY, local_manifest_call_timeout_seconds
        from core.runtime_active_clock import monotonic as active_monotonic

        manifest = self._with_ceiling(self._register_local(), 30)
        good = _good_answer(manifest.provider_id, manifest.model_name)
        seen: list[tuple[float, float]] = []

        def _refuse_then_answer(request, *_args, **_kwargs):
            deadline = float((getattr(request, "metadata", None) or {}).get(PROVIDER_DEADLINE_KEY) or 0.0)
            seen.append((active_monotonic(), deadline))
            if len(seen) == 1:
                raise requests.exceptions.ConnectionError("connection refused")
            return good

        adapter = self._adapter(manifest)
        adapter.run_text_task.side_effect = _refuse_then_answer
        adapter.run_structured_task.side_effect = _refuse_then_answer
        result = self._resolve([manifest], [adapter], turn_id="turn-fast-fault-window")
        self.assertTrue(result.used_model, msg=f"source={result.source} details={result.details}")
        self.assertEqual(len(seen), 2)
        (first_at, _first_deadline), (_retry_at, retry_deadline) = seen
        self.assertGreater(retry_deadline, 0.0, msg="the retry ran with no bound at all")
        self.assertLessEqual(
            retry_deadline,
            first_at + local_manifest_call_timeout_seconds(manifest, 30),
            msg="the retry was given a window beyond the one the first attempt started",
        )

    def test_every_attempt_of_a_retried_call_reaches_the_invocation_seam(self) -> None:
        """The seam contract: each adapter entry is recorded, the retry included."""
        import os

        from core.semantic import reach as semantic_reach
        from core.task_router import create_task_record

        manifest = self._with_ceiling(self._register_local(), 30)
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.run_structured_task.side_effect = [
            ConnectionError("connection refused"),
            ConnectionError("connection refused"),
        ]
        with mock.patch.dict(os.environ, {"VOOL_SEMANTIC_REACH": "1"}):
            with semantic_reach.observing_turn(session_id="seam", turn_id="seam-retry") as recorder:
                self.assertIsNotNone(recorder)
                with mock.patch.object(self.registry, "build_adapter", return_value=adapter):
                    self.router._invoke_manifest(
                        manifest=manifest,
                        request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
                        output_mode="tool_intent",
                        task=create_task_record("show the current directory"),
                        source_context={"requested_model": manifest.model_name, "_owner_local": True},
                    )
                attempted = recorder.provider_calls_attempted
        self.assertEqual(adapter.run_structured_task.call_count, 2)
        self.assertEqual(attempted, (f"{manifest.provider_id}:{manifest.model_name}",) * 2)

    # ---------------------------------------------------------------- honesty controls

    def test_an_auth_refusal_is_not_retried_and_stays_failed(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(manifest, text_outcomes=[AUTH_REFUSAL, AUTH_REFUSAL])
        result = self._resolve([manifest], [adapter], turn_id="turn-auth")
        self.assertFalse(result.used_model)
        self.assertEqual(adapter.run_text_task.call_count, 1)

    def test_a_persistent_503_is_bounded_not_infinite(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(manifest, text_outcomes=[FIFTY_THREE] * 5)
        result = self._resolve([manifest], [adapter], turn_id="turn-503-forever")
        self.assertFalse(result.used_model)
        # One retry on the existing two-attempt policy, then the honest terminal.
        self.assertEqual(adapter.run_text_task.call_count, 2)
        self.assertEqual(result.source, "no_provider_available")

    def test_cancellation_during_the_retry_wait_stops_dispatch(self) -> None:
        manifest = self._register_local()
        good = _good_answer(manifest.provider_id, manifest.model_name)
        cancel_after_first_fault = _CancelAfterFirstFault()
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.get_license_metadata.return_value = {"license_name": "Apache-2.0"}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.supports_streaming.return_value = False

        def _fault_then_cancel(*_args, **_kwargs):
            cancel_after_first_fault.fire()
            raise FIFTY_THREE

        adapter.run_text_task.side_effect = [_fault_then_cancel, good]
        adapter.run_structured_task.side_effect = [_fault_then_cancel, good]
        result = self._resolve(
            [manifest],
            [adapter],
            turn_id="turn-cancel-wait",
            source_context={
                "surface": "api",
                "session_id": "auto-transient-suite",
                "turn_id": "turn-cancel-wait",
                "cancel_event": cancel_after_first_fault.is_set,
            },
        )
        self.assertFalse(result.used_model)
        # The fault fired, cancellation landed, and no second dispatch left the seam.
        self.assertEqual(adapter.run_text_task.call_count, 1)

    def test_healthy_lane_completes_with_one_dispatch(self) -> None:
        manifest = self._register_local()
        adapter = self._adapter(manifest, text_outcomes=[_good_answer(manifest.provider_id, manifest.model_name)])
        result = self._resolve([manifest], [adapter], turn_id="turn-healthy")
        self.assertTrue(result.used_model)
        self.assertFalse(result.failover_used)
        self.assertEqual(adapter.run_text_task.call_count, 1)

    def test_multi_candidate_auto_prefers_same_model_retry_before_switching(self) -> None:
        primary = self._register_local()
        backup = self._register_local(name="local-backup-http", model="qwen-backup", port=2234)
        primary_adapter = self._adapter(
            primary, text_outcomes=[FIFTY_THREE, _good_answer(primary.provider_id, primary.model_name)]
        )
        backup_adapter = self._adapter(
            backup, text_outcomes=[_good_answer(backup.provider_id, backup.model_name)]
        )
        result = self._resolve([primary, backup], [primary_adapter, backup_adapter], turn_id="turn-retry-then-no-switch")
        self.assertTrue(result.used_model)
        # The transient fault recovered on the SAME model: the decision records no failed
        # candidate (attempted=[] below) and no failover, and the primary answered.
        self.assertFalse(result.failover_used)
        self.assertEqual(result.model_name, primary.model_name)
        self.assertEqual(result.details.get("attempted") or [], [])
        self.assertEqual(primary_adapter.run_text_task.call_count, 2)
        # The backup is NOT needed as a fallback. An auxiliary dispatch lane (the planner /
        # tool-intent probe that also runs on healthy multi-candidate turns) may probe a
        # candidate once; what must never happen is a fallback LADDER walking onto the backup
        # because the primary's transient fault was misread as permanent.
        self.assertLessEqual(backup_adapter.run_text_task.call_count, 1)
        self.assertLessEqual(backup_adapter.run_structured_task.call_count, 1)


class _CancelAfterFirstFault:
    """A cancel token that lands AFTER the first provider fault, before any retry."""

    def __init__(self) -> None:
        self._fired = False

    def fire(self) -> None:
        self._fired = True

    def is_set(self) -> bool:
        return self._fired


def _as_chunks_iter(response: ModelResponse):
    return iter(list(_as_chunks(response)))


def _as_chunks(response: ModelResponse):
    text = response.output_text
    mid = max(1, len(text) // 2)
    for part in (text[:mid], text[mid:]):
        if part:
            yield ModelStreamChunk(delta_text=part)
    yield ModelStreamChunk(delta_text="", done=True, usage=response.usage or None)


if __name__ == "__main__":
    unittest.main()
