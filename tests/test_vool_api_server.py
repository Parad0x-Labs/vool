from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock
from urllib import request

from apps.vool_api_server import (
    PROJECT_ROOT,
    VoolAPIHandler,
    _daemon_runtime_config,
    _dispatch_post,
    _ensure_default_provider,
    _format_runtime_event_text,
    _normalize_chat_history,
    _parameter_count_for_model,
    _parameter_size_for_model,
    _run_agent,
    _stable_openclaw_session_id,
    _start_background_prewarm,
    _stream_agent_with_events,
    create_app,
    main,
)
from core.persistent_memory import augment_history_from_session_log
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_task_events import emit_runtime_event
from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.web.api.runtime import (
    RuntimeServices,
    bootstrap_runtime_services,
    build_runtime_version_stamp,
    candidate_aware_daily_runtime_model_tag,
    extract_user_message,
    log_prewarm_results,
    message_text,
    ollama_base_url,
    ollama_model_installed,
    start_ollama_model_pull,
    startup_provider_capability_truth,
    strip_openclaw_turn_scaffolding,
)


class OpenClawTurnScaffoldingTests(unittest.TestCase):
    QUEUED = "[Queued user message that arrived while the previous turn was still active]"

    def test_recovers_newest_message_from_queued_turn_merge(self) -> None:
        # Exact shape OpenClaw sends when a message arrives mid-turn: marker line,
        # newest message, blank line, then the original in-flight prompt.
        blob = f"{self.QUEUED}\nwhats the weather in Riga?\n\nhi"
        self.assertEqual(strip_openclaw_turn_scaffolding(blob), "whats the weather in Riga?")

    def test_strips_marker_and_leading_gmt_bracket_on_one_line(self) -> None:
        # The live-repro'd shape: a GMT timestamp bracket ahead of the marker, all
        # collapsed onto one line. Everything up to and including the marker is
        # scaffolding and must be dropped.
        blob = f"[Wed 2026-07-01 18:07 GMT+3]{self.QUEUED} whats the riga? hi"
        self.assertEqual(strip_openclaw_turn_scaffolding(blob), "whats the riga? hi")

    def test_strips_leading_gmt_bracket_without_marker(self) -> None:
        self.assertEqual(
            strip_openclaw_turn_scaffolding("[Wed 2026-07-01 18:07 GMT+3] whats the weather in Riga?"),
            "whats the weather in Riga?",
        )

    def test_plain_message_passes_through_untouched(self) -> None:
        self.assertEqual(
            strip_openclaw_turn_scaffolding("whats the weather in Riga?"),
            "whats the weather in Riga?",
        )

    def test_does_not_strip_legitimate_user_brackets(self) -> None:
        # A real user question that happens to contain brackets (and no GMT/marker)
        # must be left entirely alone - no false positives.
        text = "is [a] a valid variable name in python?"
        self.assertEqual(strip_openclaw_turn_scaffolding(text), text)

    def test_message_text_and_extract_user_message_apply_sanitizer(self) -> None:
        blob = f"[Wed 2026-07-01 18:07 GMT+3]{self.QUEUED} whats the riga? hi"
        self.assertEqual(message_text(blob), "whats the riga? hi")
        self.assertEqual(
            extract_user_message([{"role": "user", "content": blob}]),
            "whats the riga? hi",
        )


class DailyBootModelSelectionTests(unittest.TestCase):
    @staticmethod
    def _installed(name: str, size_bytes: int = 0) -> SimpleNamespace:
        return SimpleNamespace(name=name, size_bytes=size_bytes)

    def test_no_registration_flag_still_discovers_and_selects_installed_daily_model(self) -> None:
        env: dict[str, str] = {}
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_inventory",
            return_value=(self._installed("qwen3:4b"), self._installed("qwen2.5:7b")),
        ) as inventory:
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env=env)

        self.assertEqual(selected, "qwen2.5:7b")
        inventory.assert_called_once_with(
            env=env,
            base_url="http://127.0.0.1:11434",
            timeout_seconds=2.0,
        )

    def test_live_19gb_nothink_artifact_does_not_starve_daily_model_on_24gb_mps(self) -> None:
        inventory = (
            self._installed("qwen2.5:7b", 4_683_087_332),
            self._installed("qwen3:4b", 2_497_293_931),
            self._installed("vool-qwen3-30b-a3b:nothink", 18_556_698_002),
        )
        probe = SimpleNamespace(ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        with mock.patch("core.web.api.runtime.installed_ollama_model_inventory", return_value=inventory):
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env={}, probe=probe)

        self.assertEqual(selected, "qwen2.5:7b")

    def test_same_nothink_artifact_may_win_when_resident_budget_proves_it_fits(self) -> None:
        inventory = (
            self._installed("qwen2.5:7b", 4_683_087_332),
            self._installed("vool-qwen3-30b-a3b:nothink", 18_556_698_002),
        )
        probe = SimpleNamespace(ram_gb=32.0, vram_gb=32.0, accelerator="mps")
        with mock.patch("core.web.api.runtime.installed_ollama_model_inventory", return_value=inventory):
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env={}, probe=probe)

        self.assertEqual(selected, "vool-qwen3-30b-a3b:nothink")

    def test_boot_selection_never_claims_an_uninstalled_reliable_model(self) -> None:
        env = {"VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1"}
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_inventory",
            return_value=(self._installed("qwen3:4b"), self._installed("nomic-embed-text:latest")),
        ):
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env=env)

        self.assertEqual(selected, "qwen3:4b")

    def test_boot_selection_preserves_explicit_operator_pin(self) -> None:
        env = {"VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1"}
        with mock.patch("core.web.api.runtime.installed_ollama_model_inventory") as inventory:
            selected = candidate_aware_daily_runtime_model_tag(
                "qwen3:14b",
                env=env,
                selection_pinned=True,
            )

        self.assertEqual(selected, "qwen3:14b")
        inventory.assert_not_called()

    def test_boot_selection_fails_open_when_inventory_is_unavailable(self) -> None:
        env: dict[str, str] = {}
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_inventory",
            side_effect=OSError("ollama unavailable"),
        ):
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env=env)

        self.assertEqual(selected, "qwen3:4b")

    def test_boot_selection_fails_open_when_daily_policy_rejects_inventory(self) -> None:
        env = {"VOOL_INSTALLED_OLLAMA_MODELS": "qwen3:4b,qwen2.5:7b"}
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_inventory",
            return_value=(self._installed("qwen3:4b"), self._installed("qwen2.5:7b")),
        ), mock.patch(
            "core.web.api.runtime.select_daily_residency_model_tag",
            side_effect=ValueError("corrupt model metadata"),
        ):
            selected = candidate_aware_daily_runtime_model_tag("qwen3:4b", env=env)

        self.assertEqual(selected, "qwen3:4b")
from core.web.api.service import json_response
from tests.asgi_harness import asgi_request


class VoolAPIServerModelMetadataTests(unittest.TestCase):
    @staticmethod
    def _server_with_runtime(runtime: RuntimeServices | None = None) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), VoolAPIHandler)
        server.vool_runtime = runtime or RuntimeServices(display_name="VOOL")
        return server

    def test_create_app_keeps_runtime_services_in_app_state(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})

        app = create_app(runtime)

        self.assertIs(app.state.runtime, runtime)
        self.assertEqual(app.state.model_name, "vool")

    def test_create_app_emits_request_id_header_and_echoes_client_request_id(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)

        status, headers, _ = asgi_request(app, method="GET", path="/healthz", headers={"X-Request-ID": "req-api-123"})

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-api-123")
        self.assertEqual(headers["x-correlation-id"], "req-api-123")

    def test_runtime_receipts_endpoint_is_fail_soft_for_unknown_session(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)

        status, _headers, body = asgi_request(
            app, method="GET", path="/api/runtime/receipts?session=openclaw:no-such-session"
        )

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["session_id"], "openclaw:no-such-session")
        self.assertEqual(payload["receipts"], [])
        self.assertIsNone(payload["chain_verified"])

    def test_create_app_generates_request_id_when_missing(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)

        status, headers, _ = asgi_request(app, method="GET", path="/healthz")

        self.assertEqual(status, 200)
        self.assertTrue(headers["x-request-id"])
        self.assertEqual(headers["x-correlation-id"], headers["x-request-id"])

    def test_create_app_passes_the_server_request_id_to_post_dispatch(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)
        received: dict[str, object] = {}

        def capture_post_dispatcher(**kwargs: object):
            received.update(kwargs)
            return json_response(200, {"ok": True})

        app.state.post_dispatcher = capture_post_dispatcher
        status, headers, _ = asgi_request(
            app,
            method="POST",
            path="/api/chat",
            headers={"X-Request-ID": "req-context-trace-123"},
            body=json.dumps({"message": "hello"}).encode("utf-8"),
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-context-trace-123")
        self.assertEqual(received["request_id"], "req-context-trace-123")

    def test_create_app_v1_models_returns_openai_shape_with_provider_models(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_parameter_size="14B")
        app = create_app(runtime)

        with mock.patch(
            "apps.vool_api_server.runtime_capability_snapshot",
            return_value={
                "provider_capability_truth": [
                    {"provider_id": "openai-compatible-remote:gpt-mock", "model_id": "gpt-mock"},
                    {"provider_id": "kimi-remote:kimi-mock", "model_id": "kimi-mock"},
                ]
            },
        ):
            status, _, body = asgi_request(app, method="GET", path="/v1/models")

        payload = json.loads(body.decode("utf-8"))
        ids = [item["id"] for item in payload["data"]]
        self.assertEqual(status, 200)
        self.assertEqual(payload["object"], "list")
        self.assertIn("vool", ids)
        self.assertIn("gpt-mock", ids)
        self.assertIn("kimi-mock", ids)
        self.assertIn("openai-compatible-remote:gpt-mock", ids)

    def test_create_app_api_tags_reports_each_provider_model_size(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_parameter_size="8B")
        app = create_app(runtime)

        with mock.patch(
            "apps.vool_api_server.runtime_capability_snapshot",
            return_value={
                "provider_capability_truth": [
                    {"provider_id": "ollama-local:qwen2.5:0.5b", "model_id": "qwen2.5:0.5b"},
                    {"provider_id": "ollama-local:qwen2.5:32b", "model_id": "qwen2.5:32b"},
                ]
            },
        ):
            status, _, body = asgi_request(app, method="GET", path="/api/tags")

        payload = json.loads(body.decode("utf-8"))
        sizes = {item["model"]: item["details"]["parameter_size"] for item in payload["models"]}
        self.assertEqual(status, 200)
        self.assertEqual(sizes["vool"], "8B")
        self.assertEqual(sizes["qwen2.5:0.5b"], "0.5B")
        self.assertEqual(sizes["ollama-local:qwen2.5:0.5b"], "0.5B")
        self.assertEqual(sizes["qwen2.5:32b"], "32B")
        self.assertEqual(sizes["ollama-local:qwen2.5:32b"], "32B")

    def test_runtime_capabilities_prefers_attached_runtime_provider_truth(self) -> None:
        runtime = RuntimeServices(
            display_name="VOOL",
            provider_capability_truth=(
                {
                    "schema": "vool.provider_capability.v1",
                    "provider_id": "ollama-local:qwen3:14b",
                    "model_id": "qwen3:14b",
                    "role_fit": "queen",
                    "locality": "local",
                    "availability_state": "ready",
                },
            ),
        )
        app = create_app(runtime)

        with mock.patch(
            "apps.vool_api_server.runtime_capability_snapshot",
            return_value={
                "provider_capability_truth": [
                    {
                        "provider_id": "ollama-local:qwen3:8b",
                        "model_id": "qwen3:8b",
                    }
                ]
            },
        ):
            status, _, body = asgi_request(app, method="GET", path="/api/runtime/capabilities")

        payload = json.loads(body.decode("utf-8"))
        provider_ids = [item["provider_id"] for item in payload["provider_capability_truth"]]
        self.assertEqual(status, 200)
        self.assertEqual(provider_ids, ["ollama-local:qwen3:14b"])

    def test_startup_provider_capability_truth_uses_measured_benchmark_hydration(self) -> None:
        manifest_truth = ProviderCapabilityTruth(
            provider_id="ollama-local:qwen3:8b",
            model_id="qwen3:8b",
            role_fit="drone",
            context_window=8192,
            tool_support=("structured_json",),
            structured_output_support=True,
            tokens_per_second=0.0,
            ram_budget_gb=12.0,
            vram_budget_gb=0.0,
            quantization="",
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=1,
        )
        measured_truth = ProviderCapabilityTruth(
            provider_id="ollama-local:qwen3:8b",
            model_id="qwen3:8b",
            role_fit="drone",
            context_window=4096,
            tool_support=("structured_json",),
            structured_output_support=True,
            tokens_per_second=19.5,
            ram_budget_gb=12.0,
            vram_budget_gb=0.0,
            quantization="q4_K_M",
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=1,
            measurement_source="local_inference_benchmark",
            measured_at="2026-06-16T10:00:00+00:00",
        )

        with mock.patch(
            "core.web.api.runtime.build_provider_registry_snapshot",
            return_value=SimpleNamespace(capability_truth=(manifest_truth,)),
        ), mock.patch(
            "core.web.api.runtime.hydrate_capability_truth_with_benchmarks",
            return_value=(measured_truth,),
        ):
            truth = startup_provider_capability_truth(
                mock.Mock(),
                runtime_home="/tmp/runtime",
                requested_profile="balanced",
                env={},
            )

        self.assertEqual(truth[0]["provider_id"], "ollama-local:qwen3:8b")
        self.assertEqual(truth[0]["tokens_per_second"], 19.5)
        self.assertEqual(truth[0]["measurement_source"], "local_inference_benchmark")

    def test_startup_provider_truth_does_not_report_ready_after_model_pull_failure(self) -> None:
        manifest_truth = ProviderCapabilityTruth(
            provider_id="ollama-local:qwen2.5:7b",
            model_id="qwen2.5:7b",
            role_fit="coder",
            context_window=32768,
            tool_support=("workspace.read_file",),
            structured_output_support=True,
            tokens_per_second=20.0,
            ram_budget_gb=8.0,
            vram_budget_gb=0.0,
            quantization="q4",
            locality="local",
            privacy_class="private",
            queue_depth=0,
            max_safe_concurrency=1,
            availability_state="ready",
        )
        with mock.patch(
            "core.web.api.runtime.build_provider_registry_snapshot",
            return_value=SimpleNamespace(capability_truth=(manifest_truth,)),
        ), mock.patch(
            "core.web.api.runtime.hydrate_capability_truth_with_benchmarks",
            return_value=(manifest_truth,),
        ):
            truth = startup_provider_capability_truth(
                mock.Mock(),
                runtime_home="/tmp/runtime",
                model_tag="qwen2.5:7b",
                env={},
                model_pull_status="failed",
                model_pull_detail="pull failed",
            )

        self.assertEqual(truth[0]["availability_state"], "blocked")
        self.assertEqual(truth[0]["last_error"], "pull failed")

    def test_create_app_runtime_operator_snapshot_endpoint_returns_snapshot_payload(self) -> None:
        from core.context_namespace import ensure_chat_namespace

        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)
        session_id = "openclaw:0123456789abcdef0123"
        ensure_chat_namespace(session_id)

        with mock.patch(
            "core.web.api.service.build_runtime_operator_snapshot",
            return_value={
                "session_id": "openclaw:snapshot",
                "memory_lifecycle": {"relevant_memory_count": 0},
                "session": {"execution_history": {"latest_tool": "workspace.read_file"}},
            },
        ):
            status, _, body = asgi_request(
                app,
                method="GET",
                path="/api/runtime/operator-snapshot",
                query_string=(
                    b"session=openclaw%3A0123456789abcdef0123"
                    b"&query=what%20time"
                ),
            )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["session_id"], "openclaw:snapshot")
        self.assertEqual(payload["memory_lifecycle"]["relevant_memory_count"], 0)
        self.assertEqual(payload["session"]["execution_history"]["latest_tool"], "workspace.read_file")

    def test_dispatch_post_marks_chat_requests_as_api_surface_and_carries_requested_model(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        seen_contexts: list[dict[str, Any]] = []

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            seen_contexts.append(dict(source_context or {}))
            return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            for path in ("/v1/chat/completions", "/api/chat"):
                response = _dispatch_post(
                    path=path,
                    body={
                        "model": "openai-compatible-remote:gpt-mock",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                    headers={"content-type": "application/json"},
                    runtime=runtime,
                    model_name="vool",
                    workspace_root_provider=lambda: "/tmp",
                )
                self.assertEqual(response.status, 200)

        self.assertEqual(len(seen_contexts), 2)
        for source_context in seen_contexts:
            self.assertEqual(source_context["surface"], "api")
            self.assertEqual(source_context["platform"], "api")
            self.assertEqual(source_context["requested_model"], "openai-compatible-remote:gpt-mock")
            self.assertEqual(source_context["workspace_binding"], "default")

    def test_dispatch_post_preserves_inbound_source_context_and_nested_workspace(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        seen_contexts: list[dict[str, Any]] = []

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            seen_contexts.append(dict(source_context or {}))
            return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "messages": [{"role": "user", "content": "hello"}],
                    "source_context": {
                        "surface": "openclaw",
                        "platform": "openclaw",
                        "workspace": "/tmp/nested-workspace",
                        "subject": "openclaw integration",
                    },
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp/default-workspace",
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(seen_contexts), 1)
        source_context = seen_contexts[0]
        self.assertEqual(source_context["surface"], "openclaw")
        self.assertEqual(source_context["platform"], "openclaw")
        self.assertEqual(source_context["workspace"], "/tmp/nested-workspace")
        self.assertEqual(source_context["workspace_root"], "/tmp/nested-workspace")
        self.assertEqual(source_context["workspace_binding"], "explicit")
        self.assertEqual(source_context["subject"], "openclaw integration")

    def test_dispatch_post_clamps_explicit_exact_reply_for_openclaw_smoke(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            # R-5: exact-response-control is applied inside the sealing
            # context BEFORE A2 admission; the boundary no longer clamps.
            return {"response": "OPENCLAW_VOOL_OK", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            response = _dispatch_post(
                path="/v1/chat/completions",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "Reply exactly OPENCLAW_VOOL_OK"}],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["choices"][0]["message"]["content"], "OPENCLAW_VOOL_OK")

    def test_dispatch_post_clamps_timestamped_exact_reply_for_openclaw_cli(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            # R-5: exact-response-control applied inside the sealing context.
            return {"response": "OPENCLAW_VOOL_OK", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [
                        {
                            "role": "user",
                            "content": "[Tue 2026-06-30 11:00 GMT+3] Reply exactly OPENCLAW_VOOL_OK",
                        }
                    ],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["message"]["content"], "OPENCLAW_VOOL_OK")

    def test_dispatch_post_does_not_clamp_file_read_exactly_requests_without_target(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {"response": "file body\nline two", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "Now read the whole file back exactly."}],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["message"]["content"], "file body\nline two")

    def test_dispatch_post_answers_web0_null_registration_from_project_grounding(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        with mock.patch("apps.vool_api_server._run_agent") as run_agent_mock:
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [
                        {
                            "role": "user",
                            "content": "[Tue 2026-06-30 14:29 GMT+3] can we buy a .null address? answer in 3 bullets",
                        }
                    ],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status, 200)
        # The scripted web0/.null topic-answer interception was REMOVED by the owner's
        # instruction (2026-09-16, core/web/api/service.py): it preempted real questions on
        # keyword evidence far weaker than its authority. These tests now pin the CURRENT
        # contract: .null registration questions reach the ordinary agent lanes (the model is
        # invoked), and no topic matcher answers them wholesale.
        run_agent_mock.assert_called_once()

    def test_pasted_fee_review_reaches_agent_through_both_chat_transports(self) -> None:
        prompt = (
            "Review this pasted Python snippet only. Do not use tools, files, shell commands, or browsing.\n\n"
            "VOOL charges a 0.1% service fee and must preserve fractions of a micro-USDC between payments. "
            "Find the bug, give corrected Python using integer arithmetic, and show two short examples, "
            "including repeated tiny payments. Keep the answer under 250 words.\n\n"
            "def accrue_fee(payment_micro, carry=0):\n"
            "    fee_micro = payment_micro * 10 // 10_000\n    return fee_micro, carry"
        )
        seen = []

        class ReviewAgent:
            def run_once(self, user_text, **kwargs):
                seen.append(user_text)
                return {"response": "The fractional remainder is discarded.", "confidence": 1.0}

        # Only the downstream agent is a double. Both early grounding owners and the
        # buffered/streamed request handling are production code; no live model or spend.
        with mock.patch("apps.vool_api_server._agent", ReviewAgent()):
            for stream in (False, True):
                response = _dispatch_post(
                    path="/api/chat",
                    body={"model": "vool", "stream": stream,
                          "messages": [{"role": "user", "content": prompt}]},
                    headers={"content-type": "application/json"},
                    runtime=RuntimeServices(display_name="VOOL"),
                    model_name="vool", workspace_root_provider=lambda: "/tmp",
                )
                raw = b"".join(response.stream or ()) if stream else response.body
                self.assertEqual(response.status, 200)
                self.assertIn(b"fractional remainder is discarded", raw)
                self.assertNotIn(b"null_registrar", raw)
        self.assertEqual(seen, [prompt, prompt])

    def test_dispatch_post_streams_web0_null_registration_grounding_without_model(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        with mock.patch("apps.vool_api_server._run_agent") as run_agent_mock, mock.patch(
            "apps.vool_api_server._stream_agent_with_events"
        ) as stream_mock:
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "Can we register a .null domain?"}],
                    "stream": True,
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        body = b"".join(response.stream or ())
        streamed_text = "".join(
            str(json.loads(line.decode("utf-8")).get("message", {}).get("content") or "")
            for line in body.splitlines()
            if line.strip()
        )

        self.assertEqual(response.status, 200)
        self.assertIn("application/x-ndjson", response.content_type)
        # The scripted web0/.null topic-answer interception was REMOVED by the owner's
        # instruction (2026-09-16, core/web/api/service.py): it preempted real questions on
        # keyword evidence far weaker than its authority. These tests now pin the CURRENT
        # contract: .null registration questions reach the ordinary agent lanes (the model is
        # invoked), and no topic matcher answers them wholesale.
        stream_mock.assert_called_once()
        self.assertEqual(streamed_text, "")  # the mocked lane streams nothing; content is the lane's job now

    def test_dispatch_post_answers_named_web0_null_registration_as_workflow(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")

        with mock.patch("apps.vool_api_server._run_agent") as run_agent_mock:
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [
                        {
                            "role": "user",
                            "content": "right. can you help me to buy a .null name? I want for example test123.null",
                        }
                    ],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))
        text = payload["message"]["content"]

        self.assertEqual(response.status, 200)
        # The scripted web0/.null topic-answer interception was REMOVED by the owner's
        # instruction (2026-09-16, core/web/api/service.py): it preempted real questions on
        # keyword evidence far weaker than its authority. These tests now pin the CURRENT
        # contract: .null registration questions reach the ordinary agent lanes (the model is
        # invoked), and no topic matcher answers them wholesale.
        run_agent_mock.assert_called_once()

    def test_dispatch_post_answers_runtime_model_status_from_runtime_truth(self) -> None:
        runtime = RuntimeServices(
            display_name="VOOL",
            runtime_model_tag="gemma3:4b",
            runtime_parameter_size="4B",
            provider_capability_truth=(
                {
                    "provider_id": "ollama-local:gemma3:4b",
                    "model_id": "gemma3:4b",
                    "availability_state": "ready",
                },
                {
                    "provider_id": "ollama-local:qwen2.5:7b",
                    "model_id": "qwen2.5:7b",
                    "availability_state": "ready",
                },
            ),
        )

        with mock.patch("apps.vool_api_server._run_agent") as run_agent_mock:
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "what standard LLM are you using now?"}],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))
        text = payload["message"]["content"]

        self.assertEqual(response.status, 200)
        run_agent_mock.assert_not_called()
        self.assertIn("Boot/default local model: `gemma3:4b`", text)
        self.assertIn("`qwen2.5:7b`", text)
        self.assertIn("Routing can select `qwen2.5:7b`", text)
        self.assertNotIn("nomic-embed-text", text)
        # States there is no single fixed model and points at the per-reply footer as the truth,
        # so the answer can't contradict the footer the way a model-guessed identity does.
        self.assertIn("no single fixed model", text)
        self.assertIn("footer", text)

    def test_dispatch_post_reports_explicit_composer_model_instead_of_local_defaults(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_model_tag="qwen3:8b")
        selected = "nvidia/nemotron-3-ultra-550b-a55b:free"

        with mock.patch("apps.vool_api_server._run_agent") as run_agent_mock:
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": selected,
                    "messages": [
                        {"role": "user", "content": "Which model did I select for this turn?"}
                    ],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        payload = json.loads(response.body.decode("utf-8"))
        text = payload["message"]["content"]
        self.assertEqual(response.status, 200)
        run_agent_mock.assert_not_called()
        self.assertIn(f"Selected model for this turn: `{selected}`", text)
        self.assertIn("explicit composer pin overrides", text)
        self.assertNotIn("Boot/default local model", text)

    def test_model_identity_followup_is_answered_deterministically_not_by_the_model(self) -> None:
        # "ok so which one is active?" has no literal "model"/"llm" word; it must still be caught so
        # it never falls through to the model, which would invent a model name (the 7b/8b mismatch).
        from core.web.api.service import _looks_like_runtime_model_status_question

        self.assertTrue(_looks_like_runtime_model_status_question("ok so which one is active?"))
        self.assertTrue(_looks_like_runtime_model_status_question("what are you running on?"))
        self.assertFalse(_looks_like_runtime_model_status_question("which model should i download"))

    def test_dispatch_post_rehydrates_history_from_session_log_when_client_history_is_sparse(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        seen_contexts: list[dict[str, Any]] = []

        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            seen_contexts.append(dict(source_context or {}))
            return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent), mock.patch(
            "core.persistent_memory.recent_conversation_events",
            return_value=[
                {
                    "user": "Create a folder named alpha inside /tmp/work.",
                    "assistant": "I completed 1 bounded builder step under `alpha`.",
                },
                {
                    "user": "Inside /tmp/work/alpha create adder.py with exactly this code: def add(a: int, b: int) -> int: return a + b",
                    "assistant": "I completed 3 bounded builder steps under `tmp/work/alpha`.",
                },
            ],
        ):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "conversationId": "launcher-proof",
                    "messages": [{"role": "user", "content": "Now read the whole file back exactly."}],
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp/work",
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(seen_contexts), 1)
        source_context = seen_contexts[0]
        self.assertEqual(source_context["client_history_message_count"], 1)
        self.assertEqual(len(source_context["client_conversation_history"]), 1)
        self.assertGreater(source_context["history_message_count"], 1)
        self.assertEqual(source_context["conversation_history"][-1]["content"], "Now read the whole file back exactly.")
        self.assertEqual(source_context["conversation_history"][0]["content"], "Create a folder named alpha inside /tmp/work.")

    def test_create_app_keeps_health_responsive_while_post_dispatch_blocks(self) -> None:
        runtime = RuntimeServices(display_name="VOOL", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)
        entered = threading.Event()
        release = threading.Event()

        def blocking_post_dispatcher(**_: object):
            entered.set()
            release.wait(timeout=5)
            return json_response(200, {"ok": True})

        app.state.post_dispatcher = blocking_post_dispatcher

        import uvicorn

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        post_thread: threading.Thread | None = None
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    with request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.05)
            else:
                self.fail("uvicorn test server did not become healthy")

            post_result: dict[str, object] = {}

            def send_blocking_post() -> None:
                req = request.Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": "vool",
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(req, timeout=5) as response:
                    post_result["status"] = response.status
                    post_result["body"] = response.read().decode("utf-8")

            post_thread = threading.Thread(target=send_blocking_post, daemon=True)
            post_thread.start()
            self.assertTrue(entered.wait(timeout=2.0))

            started = time.perf_counter()
            with request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            latency = time.perf_counter() - started

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertLess(latency, 0.5)

            release.set()
            post_thread.join(timeout=3.0)
            self.assertEqual(post_result["status"], 200)
        finally:
            release.set()
            server.should_exit = True
            if post_thread is not None:
                post_thread.join(timeout=1.0)
            thread.join(timeout=2.0)

    def test_create_app_streaming_v1_chat_completions_uses_openai_sse(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        stream_chunks = iter(
            (
                b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":"stream"},"done":false}\n',
                b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":" ok"},"done":false}\n',
                b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","eval_count":2}\n',
            )
        )

        with mock.patch("apps.vool_api_server._stream_agent_with_events", return_value=stream_chunks):
            response = _dispatch_post(
                path="/v1/chat/completions",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        status = response.status
        body = b"".join(response.stream or ())
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", response.content_type)
        self.assertIn('data: {"id":"chatcmpl-', text)
        self.assertIn('"object":"chat.completion.chunk"', text)
        self.assertIn('"content":"stream"', text)
        self.assertIn('"content":" ok"', text)
        self.assertIn("data: [DONE]", text)

    def test_runtime_events_request_rides_the_typed_channel_except_on_v1(self) -> None:
        """A7 W4 leaves the typed `vool_event` channel as the only lawful carrier for runtime
        events, so a `stream_runtime_events` request is handed to the stream as a typed-channel
        request -- except on /v1, where extra lines would corrupt the OpenAI SSE transcript. The
        flag used to be accepted on both paths and then do nothing."""
        runtime = RuntimeServices(display_name="VOOL")
        seen: list[tuple[bool, bool]] = []

        def fake_stream(
            _text: str,
            *,
            session_id: str | None = None,
            source_context: dict[str, Any] | None = None,
            model: str = "",
            include_runtime_events: bool = False,
            emit_task_events: bool = False,
            **_kwargs: Any,
        ):
            del session_id, source_context, model
            seen.append((bool(include_runtime_events), bool(emit_task_events)))
            return iter(
                (
                    b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"}\n',
                )
            )

        for path in ("/v1/chat/completions", "/api/chat"):
            with mock.patch("apps.vool_api_server._stream_agent_with_events", side_effect=fake_stream):
                response = _dispatch_post(
                    path=path,
                    body={
                        "model": "vool",
                        "messages": [{"role": "user", "content": "hello"}],
                        "stream": True,
                        "stream_runtime_events": True,
                    },
                    headers={"content-type": "application/json"},
                    runtime=runtime,
                    model_name="vool",
                    workspace_root_provider=lambda: "/tmp",
                )
            self.assertEqual(response.status, 200)
            b"".join(response.stream or ())

        self.assertEqual(len(seen), 2, seen)
        v1_runtime_events, v1_task_events = seen[0]
        native_runtime_events, _native_task_events = seen[1]
        self.assertFalse(v1_runtime_events, "the /v1 SSE transcript carries no typed lines")
        self.assertFalse(v1_task_events)
        self.assertTrue(native_runtime_events, "the native path serves the request on the typed channel")

    def test_create_app_streaming_api_chat_preserves_ollama_ndjson(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        stream_chunks = iter(
            (
                b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":"stream"},"done":false}\n',
                b'{"model":"vool","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","eval_count":1}\n',
            )
        )

        with mock.patch("apps.vool_api_server._stream_agent_with_events", return_value=stream_chunks):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "vool",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
            )

        status = response.status
        body = b"".join(response.stream or ())
        self.assertEqual(status, 200)
        self.assertIn("application/x-ndjson", response.content_type)
        self.assertIn(b'"content":"stream"', body)
        self.assertNotIn(b"data: [DONE]", body)

    def test_daemon_runtime_config_uses_env_overrides_for_isolated_acceptance(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "VOOL_DAEMON_BIND_HOST": "127.0.0.1",
                "VOOL_DAEMON_BIND_PORT": "60220",
                "VOOL_DAEMON_ADVERTISE_HOST": "127.0.0.1",
                "VOOL_DAEMON_HEALTH_BIND_HOST": "127.0.0.1",
                "VOOL_DAEMON_HEALTH_PORT": "0",
            },
            clear=False,
        ):
            config = _daemon_runtime_config(capacity=3, local_worker_threads=6)

        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.bind_port, 60220)
        self.assertEqual(config.advertise_host, "127.0.0.1")
        self.assertEqual(config.health_bind_host, "127.0.0.1")
        self.assertEqual(config.health_bind_port, 0)
        self.assertEqual(config.capacity, 3)
        self.assertEqual(config.local_worker_threads, 6)

    def test_daemon_runtime_config_ignores_invalid_integer_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "VOOL_DAEMON_BIND_PORT": "nope",
                "VOOL_DAEMON_HEALTH_PORT": "still-nope",
            },
            clear=False,
        ):
            config = _daemon_runtime_config(capacity=2, local_worker_threads=4)

        self.assertEqual(config.bind_port, 49152)
        self.assertEqual(config.health_bind_port, 0)

    def test_parameter_size_for_model_uses_runtime_tag(self) -> None:
        self.assertEqual(_parameter_size_for_model("qwen2.5:14b"), "14B")
        self.assertEqual(_parameter_size_for_model("ollama/qwen2.5:0.5b"), "0.5B")
        self.assertEqual(_parameter_size_for_model("ollama-local:qwen2.5:0.5b"), "0.5B")

    def test_parameter_count_for_model_handles_fractional_billion_sizes(self) -> None:
        self.assertEqual(_parameter_count_for_model("qwen2.5:32b"), 32_000_000_000)
        self.assertEqual(_parameter_count_for_model("qwen2.5:0.5b"), 500_000_000)

    def test_prioritize_project_root_on_sys_path_prefers_local_checkout(self) -> None:
        with mock.patch.object(sys, "path", ["/tmp/shadow-core", str(PROJECT_ROOT), "/tmp/elsewhere"]):
            from apps import vool_api_server

            vool_api_server._prioritize_project_root_on_sys_path()
            self.assertEqual(sys.path[0], str(PROJECT_ROOT))
            self.assertEqual(sys.path.count(str(PROJECT_ROOT)), 1)

    def test_runtime_version_stamp_uses_build_source_metadata_when_git_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True)
            (config_dir / "build-source.json").write_text(
                json.dumps(
                    {
                        "branch": "main",
                        "ref": "main",
                        "commit": "1234567890abcdef1234567890abcdef12345678",
                        "dirty_state": True,
                        "source_url": "https://github.com/Parad0x-Labs/vool-hive-mind/archive/refs/heads/main.tar.gz",
                    }
                ),
                encoding="utf-8",
            )

            stamp = build_runtime_version_stamp(
                project_root=project_root,
                runtime_model_tag="qwen2.5:14b",
                workstation_version="test-workstation",
            )

        self.assertEqual(stamp["branch"], "main")
        self.assertEqual(stamp["commit"], "1234567890ab")
        self.assertEqual(stamp["dirty"], True)
        self.assertTrue(str(stamp["build_id"]).endswith(".dirty"))
        self.assertEqual(stamp["default_model_tag"], "qwen2.5:14b")
        self.assertEqual(stamp["model_tag"], stamp["default_model_tag"])

    def test_runtime_version_stamp_ignores_unborn_git_repo_and_uses_build_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True)
            (config_dir / "build-source.json").write_text(
                json.dumps(
                    {
                        "branch": "codex/honest-ollama-prewarm-bootstrap",
                        "ref": "codex/honest-ollama-prewarm-bootstrap",
                        "commit": "b7672501d12def8844d5d7f9c70bad87b005c28a",
                        "dirty_state": False,
                        "source_url": "https://github.com/Parad0x-Labs/vool-hive-mind/archive/refs/heads/codex/honest-ollama-prewarm-bootstrap.tar.gz",
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", str(project_root)], check=True, capture_output=True, text=True)

            stamp = build_runtime_version_stamp(
                project_root=project_root,
                runtime_model_tag="qwen2.5:14b",
                workstation_version="test-workstation",
            )

        self.assertEqual(stamp["branch"], "codex/honest-ollama-prewarm-bootstrap")
        self.assertEqual(stamp["commit"], "b7672501d12d")
        self.assertEqual(stamp["dirty"], False)

    def test_bootstrap_runtime_services_hydrates_public_hive_auth_into_active_runtime_home(self) -> None:
        runtime_home = Path("/tmp/vool-runtime-home")
        config_home = runtime_home / "config"
        auth_target = config_home / "agent-bootstrap.json"
        probe = mock.Mock(accelerator="cpu", gpu_name=None)
        boot = mock.Mock(backend_selection=mock.Mock(backend_name="TorchMPSBackend", device="mps"))
        agent = mock.Mock()
        daemon = mock.Mock(config=mock.Mock(bind_port=49152))
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        persona = mock.Mock(persona_id="default")

        with mock.patch("core.web.api.runtime.bootstrap_runtime_mode", return_value=boot), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch("core.web.api.runtime.get_local_peer_id", return_value="peer-123"), mock.patch(
            "core.credit_ledger.ensure_starter_credits",
            return_value=False,
        ), mock.patch("core.web.api.runtime.active_config_home_dir", return_value=config_home), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": False, "status": "missing_remote_config_path", "watch_host": "hive.example.test"},
        ) as ensure_auth, mock.patch("core.web.api.runtime.probe_machine", return_value=probe), mock.patch(
            "core.web.api.runtime.default_runtime_model_tag",
            return_value="qwen3:8b",
        ), mock.patch("core.web.api.runtime.ensure_ollama_model"), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-27T00:00:00.000000Z", "build_id": "0.4.0+test"},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch("core.web.api.runtime.ModelRegistry", return_value=model_registry), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ), mock.patch("core.web.api.runtime.load_active_persona", return_value=persona), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="VOOL",
        ), mock.patch("core.web.api.runtime.ensure_openclaw_registration", return_value=True), mock.patch(
            "core.web.api.runtime.VoolAgent",
            return_value=agent,
        ), mock.patch("core.web.api.runtime.resolve_local_worker_capacity", return_value=(3, 3)), mock.patch(
            "core.web.api.runtime.VoolDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        ensure_auth.assert_called_once_with(
            project_root=PROJECT_ROOT,
            target_path=auth_target,
        )
        self.assertEqual(runtime.public_hive_auth["status"], "missing_remote_config_path")

    def test_ensure_default_provider_registers_kimi_when_key_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "KIMI_API_KEY": "test-key",
                "KIMI_BASE_URL": "https://kimi.example/v1",
                "VOOL_KIMI_MODEL": "kimi-latest",
            },
            clear=False,
        ), mock.patch("core.runtime_provider_defaults.installed_ollama_model_names", return_value=[]):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("kimi-remote", "kimi-latest"), manifests)
        self.assertEqual(manifests[("kimi-remote", "kimi-latest")].runtime_config["base_url"], "https://kimi.example/v1")

    def test_ensure_default_provider_registers_vllm_when_base_url_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "VLLM_BASE_URL": "http://127.0.0.1:8100/v1",
                "VOOL_VLLM_MODEL": "qwen2.5:32b-vllm",
                "VLLM_CONTEXT_WINDOW": "65536",
            },
            clear=False,
        ), mock.patch("core.runtime_provider_defaults.installed_ollama_model_names", return_value=[]):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("vllm-local", "qwen2.5:32b-vllm"), manifests)
        self.assertEqual(manifests[("vllm-local", "qwen2.5:32b-vllm")].runtime_config["base_url"], "http://127.0.0.1:8100/v1")
        self.assertEqual(manifests[("vllm-local", "qwen2.5:32b-vllm")].metadata["context_window"], 65536)

    def test_ensure_default_provider_registers_llamacpp_when_base_url_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "LLAMACPP_BASE_URL": "http://127.0.0.1:8090/v1",
                "VOOL_LLAMACPP_MODEL": "qwen2.5:14b-gguf",
                "LLAMACPP_CONTEXT_WINDOW": "16384",
            },
            clear=False,
        ), mock.patch("core.runtime_provider_defaults.installed_ollama_model_names", return_value=[]):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("llamacpp-local", "qwen2.5:14b-gguf"), manifests)
        self.assertEqual(manifests[("llamacpp-local", "qwen2.5:14b-gguf")].runtime_config["base_url"], "http://127.0.0.1:8090/v1")
        self.assertEqual(manifests[("llamacpp-local", "qwen2.5:14b-gguf")].metadata["context_window"], 16384)

    def test_ensure_default_provider_adds_honest_ollama_prewarm_config(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "VOOL_CONTEXT_BUCKET": "",
                "VOOL_OLLAMA_CONTEXT_WINDOW": "",
                "VOOL_ADAPTIVE_CONTEXT": "",
            },
            clear=False,
        ), mock.patch(
            "core.hardware_tier.probe_machine",
            return_value=SimpleNamespace(ram_gb=8.0),
        ), mock.patch("core.runtime_provider_defaults.installed_ollama_model_names", return_value=[]):
            _ensure_default_provider(registry, "qwen2.5:14b")

        manifest = manifests[("ollama-local", "qwen2.5:14b")]
        self.assertEqual(manifest.runtime_config["prewarm"]["strategy"], "ollama_chat")
        self.assertEqual(manifest.runtime_config["prewarm"]["keep_alive"], "15m")
        self.assertNotIn("api_path", manifest.runtime_config)
        # qwen2.5 does not reason, and Ollama answers HTTP 400 when the flag is present at all.
        self.assertNotIn("think", manifest.runtime_config)
        # Pin the probe to an 8 GB host so this remains stable on both KAS's 31.8 GB box and
        # Loop's 7.95 GB box. A 14B model on the conservative A bucket is capped at 4096.
        self.assertEqual(manifest.runtime_config["context_window"], 4096)
        self.assertEqual(manifest.runtime_config["prewarm"]["options"]["num_ctx"], 4096)
        self.assertEqual(manifest.metadata["context_window"], 4096)

    def test_bootstrap_runtime_services_runs_provider_prewarm_logging(self) -> None:
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock()
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        runtime_home = "/tmp/runtime-home"

        with mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": ""}, clear=False), mock.patch(
            "core.web.api.runtime.bootstrap_runtime_mode",
            return_value=mock.Mock(
                backend_selection=mock.Mock(backend_name="mlx", device="mps"),
                context=SimpleNamespace(paths=SimpleNamespace(runtime_home=runtime_home)),
            ),
        ), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch("core.credit_ledger.ensure_starter_credits", return_value=False), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": True, "status": "ok"},
        ), mock.patch(
            "core.web.api.runtime.probe_machine",
            return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
        ), mock.patch(
            "core.web.api.runtime.candidate_aware_daily_runtime_model_tag",
            return_value="qwen2.5:7b",
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model",
        ), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-28T00:00:00.000000Z", "build_id": "test", "branch": "main", "commit": "abc123", "dirty": False},
        ) as build_stamp, mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch(
            "core.web.api.runtime.ModelRegistry",
            return_value=model_registry,
        ), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ), mock.patch(
            "core.web.api.runtime.active_install_profile_id",
            return_value="local-only",
        ), mock.patch(
            "core.web.api.runtime.log_prewarm_results",
        ) as log_prewarm, mock.patch(
            "core.web.api.runtime.load_active_persona",
            return_value=persona,
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="VOOL",
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration",
            return_value=True,
        ), mock.patch(
            "core.web.api.runtime.VoolAgent",
            return_value=agent,
        ), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity",
            return_value=(3, 3),
        ), mock.patch(
            "core.web.api.runtime.VoolDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        self.assertEqual(runtime.runtime_model_tag, "qwen2.5:7b")
        self.assertEqual(build_stamp.call_args.kwargs["runtime_model_tag"], "qwen2.5:7b")
        log_prewarm.assert_called_once_with(
            model_registry,
            model_tag="qwen2.5:7b",
            runtime_home=runtime_home,
            requested_profile="local-only",
        )

    def test_bootstrap_runtime_services_defers_prewarm_when_disabled(self) -> None:
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock()
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        runtime_home = "/tmp/runtime-home"

        with mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": ""}, clear=False), mock.patch(
            "core.web.api.runtime.bootstrap_runtime_mode",
            return_value=mock.Mock(
                backend_selection=mock.Mock(backend_name="mlx", device="mps"),
                context=SimpleNamespace(paths=SimpleNamespace(runtime_home=runtime_home)),
            ),
        ), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch("core.credit_ledger.ensure_starter_credits", return_value=False), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": True, "status": "ok"},
        ), mock.patch(
            "core.web.api.runtime.probe_machine",
            return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
        ), mock.patch(
            "core.web.api.runtime.default_runtime_model_tag",
            return_value="qwen3:8b",
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model",
        ), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-28T00:00:00.000000Z", "build_id": "test", "branch": "main", "commit": "abc123", "dirty": False},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch(
            "core.web.api.runtime.ModelRegistry",
            return_value=model_registry,
        ), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ), mock.patch(
            "core.web.api.runtime.active_install_profile_id",
            return_value="local-only",
        ), mock.patch(
            "core.web.api.runtime.log_prewarm_results",
        ) as log_prewarm, mock.patch(
            "core.web.api.runtime.load_active_persona",
            return_value=persona,
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="VOOL",
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration",
            return_value=True,
        ), mock.patch(
            "core.web.api.runtime.VoolAgent",
            return_value=agent,
        ), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity",
            return_value=(3, 3),
        ), mock.patch(
            "core.web.api.runtime.VoolDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
                run_prewarm=False,
            )
            # Deferred: NOT warmed inline, so the port can bind first...
            log_prewarm.assert_not_called()
            self.assertIsNotNone(runtime.deferred_prewarm)
            # ...but the deferred closure warms with exactly the same arguments when invoked later.
            runtime.deferred_prewarm()

        log_prewarm.assert_called_once_with(
            model_registry,
            model_tag="qwen3:8b",
            runtime_home=runtime_home,
            requested_profile="local-only",
        )

    def test_start_background_prewarm_runs_deferred_closure(self) -> None:
        done = threading.Event()
        _start_background_prewarm(SimpleNamespace(deferred_prewarm=done.set))
        self.assertTrue(done.wait(timeout=5.0), "deferred prewarm was not run on the background thread")

    def test_start_background_prewarm_is_noop_when_nothing_deferred(self) -> None:
        # None or a missing attribute -> no thread, no crash.
        _start_background_prewarm(SimpleNamespace(deferred_prewarm=None))
        _start_background_prewarm(SimpleNamespace())

    def test_start_background_prewarm_never_crashes_on_prewarm_error(self) -> None:
        ran = threading.Event()

        def boom() -> None:
            ran.set()
            raise RuntimeError("prewarm failed")

        _start_background_prewarm(SimpleNamespace(deferred_prewarm=boom))
        self.assertTrue(ran.wait(timeout=5.0))  # worker ran; the raised error is swallowed, not propagated

    def test_bootstrap_runtime_services_uses_env_selected_model_for_live_boot(self) -> None:
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock(config=mock.Mock(bind_port=49152))
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []

        with mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": "qwen3:8b"}, clear=False), mock.patch(
            "core.web.api.runtime.bootstrap_runtime_mode",
            return_value=mock.Mock(backend_selection=mock.Mock(backend_name="mlx", device="mps")),
        ), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch(
            "core.credit_ledger.ensure_starter_credits",
            return_value=False,
        ), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": True, "status": "ok"},
        ), mock.patch(
            "core.web.api.runtime.probe_machine",
            return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
        ), mock.patch(
            "core.web.api.runtime.default_runtime_model_tag",
            return_value="deepseek-r1:8b",
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model",
        ) as ensure_model, mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-28T00:00:00.000000Z", "build_id": "test", "branch": "main", "commit": "abc123", "dirty": False},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch(
            "core.web.api.runtime.ModelRegistry",
            return_value=model_registry,
        ), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ) as ensure_provider, mock.patch(
            "core.web.api.runtime.log_prewarm_results",
        ), mock.patch(
            "core.web.api.runtime.load_active_persona",
            return_value=persona,
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="VOOL",
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration",
            return_value=True,
        ), mock.patch(
            "core.web.api.runtime.VoolAgent",
            return_value=agent,
        ), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity",
            return_value=(3, 3),
        ), mock.patch(
            "core.web.api.runtime.VoolDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        self.assertEqual(runtime.runtime_model_tag, "qwen3:8b")
        self.assertTrue(runtime.model_pull.wait(timeout=5))
        ensure_model.assert_called_once_with("qwen3:8b", progress=runtime.model_pull)
        ensure_provider.assert_called_once()
        provider_args, provider_kwargs = ensure_provider.call_args
        self.assertEqual(provider_args[:2], (model_registry, "qwen3:8b"))
        self.assertIn("env", provider_kwargs)
        self.assertIn("runtime_home", provider_kwargs)

    def test_bootstrap_runtime_services_uses_persisted_provider_env_selected_model(self) -> None:
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock(config=mock.Mock(bind_port=49152))
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        runtime_home = "/tmp/runtime-home"
        provider_env = {
            "VOOL_INSTALL_PROFILE": "local-only",
            "VOOL_OLLAMA_MODEL": "gemma3:4b",
        }

        with mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": ""}, clear=False), mock.patch(
            "core.web.api.runtime.bootstrap_runtime_mode",
            return_value=mock.Mock(
                backend_selection=mock.Mock(backend_name="mlx", device="mps"),
                context=SimpleNamespace(paths=SimpleNamespace(runtime_home=runtime_home)),
            ),
        ), mock.patch(
            "core.web.api.runtime.merge_provider_env",
            return_value=provider_env,
        ), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch(
            "core.credit_ledger.ensure_starter_credits",
            return_value=False,
        ), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": True, "status": "ok"},
        ), mock.patch(
            "core.web.api.runtime.probe_machine",
            return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
        ), mock.patch(
            "core.web.api.runtime.default_runtime_model_tag",
            return_value="qwen2.5:7b",
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model",
        ) as ensure_model, mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-28T00:00:00.000000Z", "build_id": "test", "branch": "main", "commit": "abc123", "dirty": False},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch(
            "core.web.api.runtime.ModelRegistry",
            return_value=model_registry,
        ), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ) as ensure_provider, mock.patch(
            "core.web.api.runtime.log_prewarm_results",
        ), mock.patch(
            "core.web.api.runtime.load_active_persona",
            return_value=persona,
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="VOOL",
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration",
            return_value=True,
        ), mock.patch(
            "core.web.api.runtime.VoolAgent",
            return_value=agent,
        ), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity",
            return_value=(3, 3),
        ), mock.patch(
            "core.web.api.runtime.VoolDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        self.assertEqual(runtime.runtime_model_tag, "gemma3:4b")
        self.assertTrue(runtime.model_pull.wait(timeout=5))
        ensure_model.assert_called_once_with("gemma3:4b", progress=runtime.model_pull)
        provider_args, provider_kwargs = ensure_provider.call_args
        self.assertEqual(provider_args[:2], (model_registry, "gemma3:4b"))
        self.assertEqual(provider_kwargs["env"], provider_env)

    def test_bootstrap_runtime_services_uses_installed_profile_record_selected_model(self) -> None:
        # VOOL_OLLAMA_MODEL is absent from both the live environment and provider-env.sh
        # (e.g. it was never refreshed after install), but the cross-platform install-profile
        # record was updated later (e.g. via `vool_cli install-profile --set`). The persisted
        # record must still win over the global default.
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock(config=mock.Mock(bind_port=49152))
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        runtime_home = "/tmp/runtime-home"
        provider_env = {"VOOL_INSTALL_PROFILE": "local-only"}

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": ""}, clear=False))
            stack.enter_context(
                mock.patch(
                    "core.web.api.runtime.bootstrap_runtime_mode",
                    return_value=mock.Mock(
                        backend_selection=mock.Mock(backend_name="mlx", device="mps"),
                        context=SimpleNamespace(paths=SimpleNamespace(runtime_home=runtime_home)),
                    ),
                )
            )
            stack.enter_context(mock.patch("core.web.api.runtime.merge_provider_env", return_value=provider_env))
            stack.enter_context(
                mock.patch("core.web.api.runtime.installed_profile_selected_model", return_value="gemma3:4b")
            )
            stack.enter_context(mock.patch("core.web.api.runtime.is_first_boot", return_value=False))
            stack.enter_context(mock.patch("core.credit_ledger.ensure_starter_credits", return_value=False))
            stack.enter_context(
                mock.patch("core.web.api.runtime.ensure_public_hive_auth", return_value={"ok": True, "status": "ok"})
            )
            stack.enter_context(
                mock.patch(
                    "core.web.api.runtime.probe_machine",
                    return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
                )
            )
            stack.enter_context(
                mock.patch("core.web.api.runtime.default_runtime_model_tag", return_value="qwen2.5:7b")
            )
            ensure_model = stack.enter_context(mock.patch("core.web.api.runtime.ensure_ollama_model"))
            stack.enter_context(
                mock.patch(
                    "core.web.api.runtime.build_runtime_version_stamp",
                    return_value={
                        "started_at": "2026-03-28T00:00:00.000000Z",
                        "build_id": "test",
                        "branch": "main",
                        "commit": "abc123",
                        "dirty": False,
                    },
                )
            )
            stack.enter_context(mock.patch("core.web.api.runtime.ComputeModeDaemon", return_value=compute_daemon))
            stack.enter_context(mock.patch("core.web.api.runtime.ModelRegistry", return_value=model_registry))
            ensure_provider = stack.enter_context(mock.patch("core.web.api.runtime.ensure_default_provider"))
            stack.enter_context(mock.patch("core.web.api.runtime.log_prewarm_results"))
            stack.enter_context(mock.patch("core.web.api.runtime.load_active_persona", return_value=persona))
            stack.enter_context(mock.patch("core.web.api.runtime.get_agent_display_name", return_value="VOOL"))
            stack.enter_context(mock.patch("core.web.api.runtime.ensure_openclaw_registration", return_value=True))
            stack.enter_context(mock.patch("core.web.api.runtime.VoolAgent", return_value=agent))
            stack.enter_context(
                mock.patch("core.web.api.runtime.resolve_local_worker_capacity", return_value=(3, 3))
            )
            stack.enter_context(mock.patch("core.web.api.runtime.VoolDaemon", return_value=daemon))

            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        self.assertEqual(runtime.runtime_model_tag, "gemma3:4b")
        self.assertTrue(runtime.model_pull.wait(timeout=5))
        ensure_model.assert_called_once_with("gemma3:4b", progress=runtime.model_pull)
        provider_args, _provider_kwargs = ensure_provider.call_args
        self.assertEqual(provider_args[:2], (model_registry, "gemma3:4b"))

    def test_log_prewarm_results_treats_timeout_without_background_as_info(self) -> None:
        registry = mock.Mock()
        snapshot = mock.Mock(
            prewarm_results=[
                {
                    "ok": True,
                    "provider_id": "ollama-local:qwen2.5:14b",
                    "status": "timed_out",
                    "reason": "cold_start_timeout",
                    "keep_alive": "15m",
                    "timeout_seconds": 45.0,
                }
            ]
        )

        with mock.patch("core.web.api.runtime.build_provider_registry_snapshot", return_value=snapshot), self.assertLogs(
            "vool.api", level="INFO"
        ) as captured:
            log_prewarm_results(registry)

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "INFO")
        self.assertIn("Provider prewarm timed out; continuing without background warming", captured.output[0])

    def test_normalize_chat_history_keeps_full_user_assistant_sequence(self) -> None:
        history = _normalize_chat_history(
            [
                {"role": "system", "content": "You are VOOL."},
                {"role": "user", "content": [{"type": "text", "text": "first turn"}]},
                {"role": "assistant", "content": "reply one"},
                {"role": "user", "content": "second turn"},
                {"role": "tool", "content": "ignore this"},
            ]
        )
        self.assertEqual(
            history,
            [
                {"role": "system", "content": "You are VOOL."},
                {"role": "user", "content": "first turn"},
                {"role": "assistant", "content": "reply one"},
                {"role": "user", "content": "second turn"},
            ],
        )

    def test_normalize_chat_history_preserves_multiline_code_blocks(self) -> None:
        history = _normalize_chat_history(
            [
                {
                    "role": "user",
                    "content": (
                        "Inside /tmp/workspace/alpha create adder.py with exactly this code:\n\n"
                        "def add(a: int, b: int) -> int:\n"
                        "    return a + b\n"
                    ),
                }
            ]
        )

        self.assertEqual(
            history,
            [
                {
                    "role": "user",
                    "content": (
                        "Inside /tmp/workspace/alpha create adder.py with exactly this code:\n\n"
                        "def add(a: int, b: int) -> int:\n"
                        "    return a + b"
                    ),
                }
            ],
        )

    def test_session_id_prefers_explicit_openclaw_identifiers(self) -> None:
        session_id = _stable_openclaw_session_id(
            body={"conversationId": "abc-123"},
            history=[{"role": "user", "content": "hello"}],
            headers={},
        )
        self.assertTrue(session_id.startswith("openclaw:"))
        self.assertEqual(
            session_id,
            _stable_openclaw_session_id(
                body={"conversationId": "abc-123"},
                history=[{"role": "user", "content": "different"}],
                headers={},
            ),
        )

    def test_session_id_passes_through_a_canonical_client_id_verbatim(self) -> None:
        # The /chat sidebar mints canonical ids (openclaw:<20 hex>) and re-sends them to resume a
        # thread. A canonical value must NOT be re-hashed, or a reopened conversation would key to a
        # brand-new id and lose its persisted transcript.
        canonical = "openclaw:0123456789abcdef0123"
        result = _stable_openclaw_session_id(
            body={"session_id": canonical},
            history=[{"role": "user", "content": "resume this"}],
            headers={},
        )
        self.assertEqual(result, canonical)

    def test_remote_canonical_session_id_cannot_resume_a_desktop_namespace(self) -> None:
        canonical = "openclaw:0123456789abcdef0123"
        first = _stable_openclaw_session_id(
            body={"session_id": canonical},
            history=[{"role": "user", "content": "resume this"}],
            headers={},
            allow_canonical_resume=False,
        )
        second = _stable_openclaw_session_id(
            body={"session_id": canonical},
            history=[{"role": "user", "content": "different turn"}],
            headers={},
            allow_canonical_resume=False,
        )

        self.assertNotEqual(first, canonical)
        self.assertEqual(first, second)

    def test_session_id_hashes_a_non_canonical_client_id(self) -> None:
        # A non-canonical client id (wrong length / uppercase / not hex) is still hashed, so the
        # passthrough cannot be used to smuggle an arbitrary raw string in as the session id.
        for raw in ("openclaw:not-hex-value", "openclaw:0123456789ABCDEF0123", "my-thread-42"):
            result = _stable_openclaw_session_id(
                body={"session_id": raw},
                history=[{"role": "user", "content": "hello"}],
                headers={},
            )
            self.assertTrue(result.startswith("openclaw:"))
            self.assertNotEqual(result, raw)

    def test_stateless_identical_openings_get_isolated_session_ids(self) -> None:
        first_id = "openclaw:" + ("1" * 20)
        second_id = "openclaw:" + ("2" * 20)
        request = {
            "body": {"model": "qwen2.5:7b"},
            "history": [{"role": "user", "content": "Hi"}],
            "headers": {},
        }
        with mock.patch(
            "core.web.api.runtime.secrets.token_hex",
            side_effect=[first_id.removeprefix("openclaw:"), second_id.removeprefix("openclaw:")],
        ):
            first = _stable_openclaw_session_id(**request)
            second = _stable_openclaw_session_id(**request)

        self.assertEqual(first, first_id)
        self.assertEqual(second, second_id)
        self.assertNotEqual(first, second)

        marker = "PRIVATE_CHAT_ONE_MARKER"

        def fake_recent_events(session_id: str, *, limit: int) -> list[dict[str, str]]:
            del limit
            if session_id == first:
                return [{"user": "Hi", "assistant": marker}]
            return []

        with mock.patch(
            "core.persistent_memory.recent_conversation_events",
            side_effect=fake_recent_events,
        ):
            first_history = augment_history_from_session_log(
                [{"role": "user", "content": "continue"}],
                session_id=first,
                user_text="continue",
            )
            second_history = augment_history_from_session_log(
                [{"role": "user", "content": "Hi"}],
                session_id=second,
                user_text="Hi",
            )

        self.assertIn(marker, " ".join(item["content"] for item in first_history))
        self.assertNotIn(marker, " ".join(item["content"] for item in second_history))

    def test_format_runtime_event_text_adds_newline(self) -> None:
        self.assertEqual(
            _format_runtime_event_text({"message": "Running real tool workspace.read_file."}),
            "Running real tool workspace.read_file.\n",
        )
        self.assertEqual(
            _format_runtime_event_text({"event_type": "model_output_chunk", "message": "hello"}),
            "hello",
        )
        lane_text = _format_runtime_event_text(
            {
                "event_type": "model_lane_started",
                "message": "Using ollama-local:qwen3:8b.",
                "lane": "daily",
                "provider_id": "ollama-local:qwen3:8b",
                "model_id": "qwen3:8b",
                "cost_class": "free_local",
                "tokens_per_second": 19.5,
                "queue_depth": 0,
                "private_path": "/tmp/secret",
            }
        )
        self.assertTrue(lane_text.startswith("VOOL_RUNTIME_EVENT "))
        self.assertIn('"lane":"daily"', lane_text)
        self.assertIn('"tokens_per_second":19.5', lane_text)
        # cost_class lets a client badge the lane Local vs Cloud from the moment it starts.
        self.assertIn('"cost_class":"free_local"', lane_text)
        self.assertNotIn("private_path", lane_text)
        proof_text = _format_runtime_event_text(
            {
                "event_type": "model_lane_proof",
                "schema": "vool.model_lane_proof.v1",
                "turn_id": "turn-1",
                "session_id": "session-1",
                "task_class": "debugging",
                "complexity": "hard",
                "lane": "deep",
                "phase": "failed",
                "planned_provider_id": "ollama-local:qwen3:14b",
                "planned_model_id": "qwen3:14b",
                "provider_id": "ollama-local:qwen3:8b",
                "model_id": "qwen3:8b",
                "actual_adapter_provider_id": "ollama-local:qwen3:8b",
                "actual_adapter_model_id": "qwen3:8b",
                "backend": "ollama",
                "measurement_source": "local_inference_benchmark",
                "verifier_status": "blocked",
                "verifier_provider_id": "",
                "verifier_model_id": "",
                "kv_cache_status": "ollama=not_supported_keep_alive_only",
                "speculative_status": "inactive",
                "eagle_status": "unsupported_by_backend",
                "mismatch": True,
                "failure_reason": "planned_adapter_mismatch",
                "private_path": "/tmp/secret",
            }
        )
        self.assertTrue(proof_text.startswith("VOOL_RUNTIME_EVENT "))
        self.assertIn('"schema":"vool.model_lane_proof.v1"', proof_text)
        self.assertIn('"actual_adapter_provider_id":"ollama-local:qwen3:8b"', proof_text)
        self.assertIn('"failure_reason":"planned_adapter_mismatch"', proof_text)
        self.assertNotIn("private_path", proof_text)

    def test_format_runtime_event_text_surfaces_model_usage(self) -> None:
        # The per-response usage event must carry tokens + cost + cost_class so the trace rail
        # and any streaming client can show what this turn spent and which lane was billed.
        usage_text = _format_runtime_event_text(
            {
                "event_type": "model_usage",
                "message": "openrouter-byok:anthropic/claude used 2450 tokens.",
                "provider_id": "openrouter-byok:anthropic/claude",
                "model_id": "anthropic/claude",
                "cost_class": "paid_cloud",
                "prompt_tokens": 1800,
                "output_tokens": 650,
                "usd_actual": 0.0123,
                "private_path": "/tmp/secret",
            }
        )
        self.assertTrue(usage_text.startswith("VOOL_RUNTIME_EVENT "))
        self.assertIn('"cost_class":"paid_cloud"', usage_text)
        self.assertIn('"prompt_tokens":1800', usage_text)
        self.assertIn('"output_tokens":650', usage_text)
        self.assertIn('"usd_actual":0.0123', usage_text)
        self.assertNotIn("private_path", usage_text)

    def test_stream_agent_with_events_emits_progress_before_final_response(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Running real tool workspace.search_text.",
            )
            emit_runtime_event(
                source_context,
                event_type="tool_executed",
                message="Finished workspace.search_text. Search matches for \"tool_intent\".",
            )
            return {"response": "Grounded final answer."}

        # N-1 rebind (A7 W3/W4): progress travels ONLY on the typed
        # `vool_event` channel (emit_task_events=True) — it is never muxed
        # into assistant content bytes, which are exclusively the committed
        # A7 canonical replay.
        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "find tool intent wiring",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="vool",
                    include_runtime_events=True,
                    emit_task_events=True,
                )
            )

        payloads = [json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        task_texts = [
            str(p.get("vool_event", {}).get("summary") or p.get("vool_event", {}).get("message") or "")
            for p in payloads
            if isinstance(p.get("vool_event"), dict)
        ]
        joined_events = "\n".join(task_texts)
        self.assertIn("Running real tool workspace.search_text.", joined_events)
        self.assertIn('Finished workspace.search_text. Search matches for "tool_intent".', joined_events)
        # Content channel carries ONLY committed canonical bytes — no prose muxing.
        content_lines = [
            str((p.get("message") or {}).get("content") or "")
            for p in payloads
            if p.get("done") is not True or p.get("message")
        ]
        joined_content = "".join(content_lines)
        self.assertIn("Grounded final answer.", joined_content)
        self.assertNotIn("Running real tool", joined_content)

    def test_stream_agent_with_events_omits_progress_by_default(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="task_received",
                message="Received request: find tool intent wiring",
            )
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Running real tool workspace.search_text.",
            )
            return {"response": "Clean final answer."}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "find tool intent wiring",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="vool",
                )
            )

        payloads = [line for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        contents = [json.loads(line)["message"]["content"] for line in payloads]
        joined = "".join(contents)
        self.assertNotIn("Received request:", joined)
        self.assertNotIn("Running real tool workspace.search_text.", joined)
        self.assertIn("Clean final answer.", joined)

    def test_stream_agent_with_events_emits_typed_task_events(self) -> None:
        # The live-status channel: progress rides on dedicated `vool_event` NDJSON
        # lines carrying typed stages/tool/lane/cost, and the answer text stays clean.
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Using web_research",
                details={"tool_name": "web_research"},
            )
            emit_runtime_event(
                source_context,
                event_type="tool_executed",
                message="Finished web_research. 3 matches found.",
                details={"tool_name": "web_research", "status": "executed"},
            )
            emit_runtime_event(
                source_context,
                event_type="model_usage",
                message="usage",
                details={"cost_class": "paid_cloud", "usd_actual": 0.04, "output_tokens": 128},
            )
            return {"response": "Final grounded answer."}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "find the thing",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="vool",
                    emit_task_events=True,
                )
            )

        objs = [json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        task_events = [obj["vool_event"] for obj in objs if "vool_event" in obj]
        answer = "".join(obj["message"]["content"] for obj in objs if "message" in obj)
        types = [event["type"] for event in task_events]

        self.assertIn("tool.started", types)
        self.assertIn("tool.completed", types)
        self.assertIn("cloud.cost_updated", types)
        # a terminal completion event so the card can collapse to a verified summary
        self.assertEqual(task_events[-1]["type"], "task.completed")
        # the answer is delivered and the progress text never leaks into it
        self.assertIn("Final grounded answer.", answer)
        self.assertNotIn("Using web_research", answer)
        self.assertNotIn("3 matches found", answer)
        # the searching stage and the paid-cost dollars are carried on the typed events
        self.assertIn("Searching", [event.get("stage") for event in task_events])
        cost_events = [event for event in task_events if event["type"] == "cloud.cost_updated"]
        self.assertTrue(cost_events)
        self.assertEqual(cost_events[0]["cost"]["usd_actual"], 0.04)
        self.assertTrue(cost_events[0]["cost"]["paid"])

    def test_stream_agent_with_events_no_typed_events_unless_requested(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Using web_research",
                details={"tool_name": "web_research"},
            )
            return {"response": "Clean answer."}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "x",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="vool",
                )
            )

        objs = [json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        self.assertFalse(any("vool_event" in obj for obj in objs))
        answer = "".join(obj["message"]["content"] for obj in objs if "message" in obj)
        self.assertIn("Clean answer.", answer)

    def test_stream_agent_with_events_prefers_live_model_chunks_over_fake_replay(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                {"runtime_event_stream_id": str((source_context or {}).get("runtime_event_stream_id") or "")},
                event_type="model_output_chunk",
                message="Hello",
            )
            emit_runtime_event(
                {"runtime_event_stream_id": str((source_context or {}).get("runtime_event_stream_id") or "")},
                event_type="model_output_chunk",
                message=" world",
            )
            return {"response": "Hello world"}

        with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "say hello",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="vool",
                )
            )

        payloads = [json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        contents = [payload["message"]["content"] for payload in payloads]
        assert contents.count("Hello") == 1
        assert contents.count(" world") == 1
        assert "Hello world" not in contents
        assert payloads[-1]["done"] is True

    def test_run_agent_injects_runtime_session_id_into_source_context(self) -> None:
        seen: dict[str, object] = {}

        class FakeAgent:
            def run_once(self, user_text: str, *, session_id_override: str | None = None, source_context: dict | None = None) -> dict:
                seen["user_text"] = user_text
                seen["session_id_override"] = session_id_override
                seen["source_context"] = dict(source_context or {})
                return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._agent", FakeAgent()):
            result = _run_agent(
                "inspect the repo",
                session_id="openclaw:test-session",
                source_context={"conversation_history": []},
            )

        # The answer's BODY, with the turn's provenance line removed. Every response carries one
        # since `core/response_provenance.py`, including this fake's -- a result recording no
        # model, no route and no usage gets the `runtime | no model | build V` line, which is the
        # truthful reading of "nothing was recorded". This test is about session-id injection, so
        # it asserts the body exactly and lets the footer be the footer's own test's business
        # (tests/test_every_response_says_what_produced_it.py).
        from core.response_provenance import strip_provenance_footer

        self.assertEqual(strip_provenance_footer(result["response"]), "ok")
        self.assertEqual(seen["session_id_override"], "openclaw:test-session")
        source_context = dict(seen["source_context"])  # type: ignore[arg-type]
        self.assertEqual(source_context["runtime_session_id"], "openclaw:test-session")
        self.assertEqual(source_context["platform"], "openclaw")
        self.assertIn("workspace", source_context)
        self.assertIn("workspace_root", source_context)

    def test_run_agent_falls_back_to_project_root_when_cwd_is_gone(self) -> None:
        seen: dict[str, object] = {}

        class FakeAgent:
            def run_once(self, user_text: str, *, session_id_override: str | None = None, source_context: dict | None = None) -> dict:
                seen["source_context"] = dict(source_context or {})
                return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.vool_api_server._agent", FakeAgent()), mock.patch(
            "apps.vool_api_server.Path.cwd",
            side_effect=FileNotFoundError,
        ):
            _run_agent("inspect the repo")

        source_context = dict(seen["source_context"])  # type: ignore[arg-type]
        self.assertEqual(source_context["workspace"], str(PROJECT_ROOT))
        self.assertEqual(source_context["workspace_root"], str(PROJECT_ROOT))

    def test_healthz_exposes_runtime_version_headers_and_payload(self) -> None:
        stamp = {
            "release_version": "0.4.0-closed-test",
            "build_id": "0.4.0-closed-test+abc123def456.dirty",
            "started_at": "2026-03-14T10:00:00.000000Z",
            "commit": "abc123def456",
            "dirty": True,
            "branch": "feature/local-bootstrap",
        }
        server = self._server_with_runtime(
            RuntimeServices(
                display_name="VOOL",
                runtime_version_stamp=stamp,
                public_hive_auth={
                    "ok": False,
                    "status": "missing_remote_config_path",
                    "watch_host": "hive.example.test",
                    "remote_config_path": "/etc/vool-hive-mind/watch-config.json",
                    "next_step": "python -m ops.ensure_public_hive_auth --watch-host hive.example.test --remote-config-path /etc/vool-hive-mind/watch-config.json",
                },
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with mock.patch(
                "apps.vool_api_server.runtime_capability_snapshot",
                return_value={"feature_flags": {"public_hive_enabled": True}, "capabilities": [{"name": "local_runtime"}]},
            ), request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.headers.get("X-Vool-Runtime-Version"), "0.4.0-closed-test")
                self.assertEqual(response.headers.get("X-Vool-Runtime-Build"), "0.4.0-closed-test+abc123def456.dirty")
                self.assertEqual(response.headers.get("X-Vool-Runtime-Commit"), "abc123def456")
                self.assertEqual(response.headers.get("X-Vool-Runtime-Dirty"), "1")
                self.assertEqual(payload["runtime"]["branch"], "feature/local-bootstrap")
                self.assertEqual(payload["runtime"]["build_id"], "0.4.0-closed-test+abc123def456.dirty")
                self.assertEqual(payload["capabilities"]["feature_flags"]["public_hive_enabled"], True)
                self.assertEqual(payload["capabilities"]["capabilities"][0]["name"], "local_runtime")
                self.assertEqual(payload["capabilities"]["public_hive_auth"]["status"], "missing_remote_config_path")
                self.assertEqual(payload["capabilities"]["public_hive_auth"]["watch_host"], "hive.example.test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_runtime_version_route_returns_current_runtime_stamp(self) -> None:
        stamp = {
            "release_version": "0.4.0-closed-test",
            "build_id": "0.4.0-closed-test+abc123def456",
            "started_at": "2026-03-14T10:00:00.000000Z",
            "commit": "abc123def456",
            "dirty": False,
            "branch": "feature/local-bootstrap",
        }
        server = self._server_with_runtime(RuntimeServices(display_name="VOOL", runtime_version_stamp=stamp))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/api/runtime/version", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["release_version"], "0.4.0-closed-test")
                self.assertEqual(payload["build_id"], "0.4.0-closed-test+abc123def456")
                self.assertEqual(payload["branch"], "feature/local-bootstrap")
                self.assertEqual(response.headers.get("X-Vool-Runtime-Dirty"), "0")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_runtime_capabilities_route_returns_current_runtime_capability_snapshot(self) -> None:
        server = self._server_with_runtime(
            RuntimeServices(
                display_name="VOOL",
                public_hive_auth={
                    "ok": True,
                    "status": "synced_from_ssh",
                },
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            snapshot = {
                "mode": "api_server",
                "feature_flags": {"helper_mesh_enabled": True},
                "capabilities": [{"name": "helper_mesh", "state": "partial"}],
            }
            with mock.patch("apps.vool_api_server.runtime_capability_snapshot", return_value=snapshot), request.urlopen(
                f"http://127.0.0.1:{port}/api/runtime/capabilities",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["mode"], "api_server")
                self.assertEqual(payload["feature_flags"]["helper_mesh_enabled"], True)
                self.assertEqual(payload["capabilities"][0]["name"], "helper_mesh")
                self.assertEqual(payload["capabilities"][0]["state"], "implemented")
                self.assertEqual(payload["public_hive_auth"]["status"], "synced_from_ssh")
                self.assertEqual(payload["public_hive_auth"]["ok"], True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_main_runs_uvicorn_with_factory_app_and_shutdown(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        fake_uvicorn = mock.Mock()
        fake_server = mock.Mock()
        fake_uvicorn.Config.return_value = mock.sentinel.config
        fake_uvicorn.Server.return_value = fake_server

        with mock.patch("apps.vool_api_server._bootstrap", return_value=runtime), mock.patch.dict(
            "sys.modules",
            {"uvicorn": fake_uvicorn},
        ), mock.patch(
            "sys.argv",
            ["vool-api-server", "--bind", "127.0.0.1", "--port", "18080"],
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        fake_uvicorn.Config.assert_called_once()
        _, kwargs = fake_uvicorn.Config.call_args
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 18080)
        self.assertEqual(kwargs["access_log"], False)
        self.assertIsNotNone(fake_uvicorn.Config.call_args.args[0])
        fake_server.run.assert_called_once()
        self.assertTrue(hasattr(runtime, "shutdown"))

    @unittest.skipUnless(os.environ.get("VOOL_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_live_trace_route_carries_workstation_deploy_proof(self) -> None:
        server = self._server_with_runtime()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/trace", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers.get("X-Vool-Workstation-Version"), VOOL_WORKSTATION_DEPLOYMENT_VERSION)
                self.assertEqual(response.headers.get("X-Vool-Workstation-Surface"), "trace-rail")
                self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, body)
                self.assertIn('data-workstation-surface="trace-rail"', body)
                self.assertIn("Trace workstation v1", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


class Web0BrowserRouteTests(unittest.TestCase):
    def test_web0_alias_serves_the_null_browser_page(self) -> None:
        from core.null_browser_page import render_null_browser_html
        from core.web.meet.routes import resolve_static_route

        expected = render_null_browser_html().encode("utf-8")
        status, content_type, body = resolve_static_route("/web0")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertEqual(body, expected)

    def test_web0_and_null_browser_are_identical(self) -> None:
        from core.web.meet.routes import resolve_static_route

        self.assertEqual(resolve_static_route("/web0")[2], resolve_static_route("/null-browser")[2])

    def test_web0_page_posts_back_to_api_null(self) -> None:
        # The page must call POST /api/null so the browse action actually resolves a
        # .null name through the running VOOL API - guard against the endpoint drifting.
        from core.null_browser_page import render_null_browser_html

        self.assertIn("/api/null", render_null_browser_html())

    def test_web0_page_is_a_browser_with_address_bar_and_resolve_call(self) -> None:
        from core.null_browser_page import render_null_browser_html

        html = render_null_browser_html()
        self.assertIn('value="web0.null"', html)          # default address
        self.assertIn("/api/web0/resolve", html)          # resolves names
        self.assertIn("sandbox=", html)                   # untrusted Arweave content is sandboxed
        self.assertIn("/api/null", html)                  # advanced dispatch preserved
        # Auto-open the default site on load: the init (…setNav()) must fire go() so the browser
        # lands on web0.null instead of an empty "type an address" placeholder.
        self.assertIn("go();", html.split("setNav();")[-1])

    def test_web0_page_offers_open_in_real_tab_for_wallet_connect(self) -> None:
        # Wallets (Phantom) refuse to inject into the sandboxed cross-origin preview iframe, so a
        # ".null" site's "Connect" dead-ends at "Get Phantom". The browser must offer an
        # open-in-a-real-tab escape hatch that loads the site's real gateway URL top-level, where
        # the wallet provider is available. Guard the affordance and that it does NOT weaken the
        # sandbox to "fix" injection.
        from core.null_browser_page import render_null_browser_html

        html = render_null_browser_html()
        self.assertIn('id="opentab"', html)                                  # the affordance exists
        self.assertIn('getElementById("opentab").onclick', html)             # it is wired
        self.assertIn('window.open(currentGatewayUrl, "_blank"', html)       # opens the real URL top-level
        # The sandbox must stay intact — we solve wallet connect by escaping to a real tab, not by
        # removing the sandbox that isolates untrusted Arweave content.
        self.assertIn('sandbox="allow-scripts allow-forms allow-popups allow-same-origin"', html)

    def test_normalize_web0_name_variants(self) -> None:
        from core.web.api.service import _normalize_web0_name

        self.assertEqual(_normalize_web0_name("web0.null"), "web0")
        self.assertEqual(_normalize_web0_name("WEB0.NULL"), "web0")
        self.assertEqual(_normalize_web0_name("null://web0.null/some/path?x=1"), "web0")
        self.assertEqual(_normalize_web0_name("https://web0.null"), "web0")
        self.assertEqual(_normalize_web0_name(""), "")

    def test_web0_resolve_returns_gateway_url_for_a_name_with_content(self) -> None:
        from core.web.api.service import _web0_resolve_response

        rec = SimpleNamespace(owner="9vDnXsPoOwner", arweave_txid="ETIGvFIIa7DXt72Lr", x402_endpoint="")
        with mock.patch("core.null_resolver.resolve_null_domain", return_value=rec), mock.patch(
            "core.policy_engine.local_only_mode", return_value=False
        ):
            resp = _web0_resolve_response({"name": ["web0.null"]})
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["name"], "web0")
        self.assertEqual(body["gateway_url"], "https://arweave.net/ETIGvFIIa7DXt72Lr")
        self.assertTrue(body["has_content"])

    def test_web0_resolve_reports_registered_but_no_content(self) -> None:
        from core.web.api.service import _web0_resolve_response

        rec = SimpleNamespace(owner="ownerpubkey", arweave_txid=None, x402_endpoint="")
        with mock.patch("core.null_resolver.resolve_null_domain", return_value=rec), mock.patch(
            "core.policy_engine.local_only_mode", return_value=False
        ):
            resp = _web0_resolve_response({"name": ["parad0x"]})
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["has_content"])
        self.assertEqual(body["gateway_url"], "")

    def test_web0_resolve_missing_name_is_400(self) -> None:
        from core.web.api.service import _web0_resolve_response

        resp = _web0_resolve_response({})
        self.assertEqual(resp.status, 400)

    def test_web0_resolve_blocked_under_local_only_mode(self) -> None:
        from core.web.api.service import _web0_resolve_response

        with mock.patch("core.policy_engine.local_only_mode", return_value=True):
            resp = _web0_resolve_response({"name": ["web0.null"]})
        self.assertEqual(resp.status, 403)
        self.assertFalse(json.loads(resp.body)["ok"])

    def test_web0_resolve_unregistered_is_404(self) -> None:
        from core.web.api.service import _web0_resolve_response

        with mock.patch("core.null_resolver.resolve_null_domain", return_value=None), mock.patch(
            "core.policy_engine.local_only_mode", return_value=False
        ):
            resp = _web0_resolve_response({"name": ["definitely-not-registered"]})
        self.assertEqual(resp.status, 404)

    @unittest.skipUnless(os.environ.get("VOOL_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_live_web0_route_served_by_api_server(self) -> None:
        server = VoolAPIServerModelMetadataTests._server_with_runtime()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/web0", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers.get("X-Vool-Workstation-Surface"), "web0-browser")
                self.assertIn("/api/null", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


def test_usage_footer_formats_local_and_cloud() -> None:
    from core.web.api.runtime import _format_usage_footer

    assert (
        _format_usage_footer({"cost_class": "free_local", "model_id": "qwen2.5:7b", "prompt_tokens": 800, "output_tokens": 403})
        == "`local | qwen2.5:7b | 1,203 tok`"
    )
    assert (
        _format_usage_footer(
            {"cost_class": "paid_cloud", "model_id": "anthropic/claude", "prompt_tokens": 1800, "output_tokens": 650, "usd_actual": 0.0123}
        )
        == "`cloud | claude | 2,450 tok | $0.0123`"
    )
    assert _format_usage_footer({"prompt_tokens": 0, "output_tokens": 0}) == ""
    assert _format_usage_footer(None) == ""


def test_finalize_turn_usage_appends_footer_and_attaches_summary(monkeypatch) -> None:
    from core import memory_first_router as mfr
    from core.web.api import runtime as rt

    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "1")
    mfr.record_turn_usage(
        {"cost_class": "paid_cloud", "model_id": "anthropic/claude", "provider_id": "openrouter-byok:anthropic/claude",
         "prompt_tokens": 100, "output_tokens": 200, "usd_actual": 0.005}
    )
    try:
        result = rt._finalize_turn_usage({"response": "Here is the fix."})
    finally:
        mfr.reset_turn_usage()
    assert result["usage_summary"]["cost_class"] == "paid_cloud"
    assert "Here is the fix." in result["response"]
    assert result["response"].rstrip().endswith("`cloud | claude | 300 tok | $0.0050`")


def test_finalize_turn_usage_respects_disable_flag(monkeypatch) -> None:
    from core import memory_first_router as mfr
    from core.web.api import runtime as rt

    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "0")
    mfr.record_turn_usage({"cost_class": "free_local", "model_id": "qwen2.5:7b", "prompt_tokens": 10, "output_tokens": 20})
    try:
        result = rt._finalize_turn_usage({"response": "Hello."})
    finally:
        mfr.reset_turn_usage()
    assert result["response"] == "Hello."  # no footer when disabled
    assert result["usage_summary"]["model_id"] == "qwen2.5:7b"  # summary still attached


def test_chat_builders_use_real_tokens_from_usage_summary() -> None:
    from core.web.api.runtime import openai_chat_response

    payload = openai_chat_response({"response": "ok", "usage_summary": {"prompt_tokens": 123, "output_tokens": 45}}, "m")
    assert payload["usage"]["prompt_tokens"] == 123
    assert payload["usage"]["completion_tokens"] == 45
    assert payload["usage"]["total_tokens"] == 168


def test_runtime_version_response_detects_and_reports_the_build() -> None:
    from types import SimpleNamespace

    from core.web.api.service import _looks_like_runtime_version_question, runtime_version_response

    # Detects version questions, ignores model questions and unrelated "version" mentions.
    assert _looks_like_runtime_version_question("version") is True
    assert _looks_like_runtime_version_question("what version are you running") is True
    assert _looks_like_runtime_version_question("what model are you using") is False
    assert _looks_like_runtime_version_question("what is the latest version of python") is False

    rt = SimpleNamespace(
        runtime_version_stamp={
            "release_version": "0.4.0-closed-test",
            "build_id": "0.4.0-closed-test+abc123",
            "commit": "abc123def456",
            "channel_name": "closed-test",
            "dirty": True,
        },
        runtime_model_tag="qwen2.5:7b",
    )
    result = runtime_version_response("vool version", rt)
    assert result is not None
    assert result["source"] == "runtime_version"
    assert result["deterministic"] is True
    assert result["runtime_release_version"] == "0.4.0-closed-test"
    assert "0.4.0-closed-test" in result["response"]
    assert "abc123def456" in result["response"]
    assert "uncommitted changes" in result["response"]  # dirty flag surfaced
    assert runtime_version_response("hello there", rt) is None


def test_web0_opentab_warns_before_leaving_the_read_only_preview() -> None:
    # Viewing a .null site is read-only; only the "open directly" button leaves the sandbox for
    # the raw Arweave origin where the SITE (not VOOL) may prompt the wallet. Warn first so a
    # scary Phantom dialog is not mistaken for VOOL asking to sign.
    from core.null_browser_page import render_null_browser_html

    html = render_null_browser_html()
    assert "window.confirm(" in html
    assert "those prompts come from the site, not " in html
    assert "read-only and never touches your wallet" in html
    # the read-only preview must stay sandboxed (no wallet injection into the iframe)
    assert "sandbox=" in html


def test_host_header_allowlist_blocks_dns_rebinding() -> None:
    from core.web.api.runtime import host_header_allowed

    # Loopback / localhost (with or without port, IPv6 bracketed or bare) and an absent Host pass.
    for allowed in ("127.0.0.1", "127.0.0.1:11435", "localhost", "localhost:11435", "[::1]", "[::1]:11435", "::1", ""):
        assert host_header_allowed(allowed) is True, allowed
    # A rebound external domain or an unexpected LAN host is rejected.
    for blocked in ("evil.com", "evil.com:11435", "attacker.example", "192.168.1.50", "vool.local"):
        assert host_header_allowed(blocked) is False, blocked


def test_host_header_allowlist_honors_env_override(monkeypatch) -> None:
    from core.web.api.runtime import host_header_allowed

    assert host_header_allowed("vool.lan") is False
    monkeypatch.setenv("VOOL_ALLOWED_HOSTS", "vool.lan, 192.168.1.50")
    assert host_header_allowed("vool.lan") is True
    assert host_header_allowed("192.168.1.50:11435") is True
    assert host_header_allowed("other.host") is False


def test_chat_session_endpoint_validation(tmp_path) -> None:
    from types import SimpleNamespace

    from core.persistent_memory import append_conversation_event
    from core.runtime_paths import configure_runtime_home
    from core.web.api import service

    configure_runtime_home(tmp_path / "home")
    try:
        sid = "openclaw:0123456789abcdef0123"
        append_conversation_event(
            session_id=sid, user_input="hi", assistant_output="hello",
            source_context={"surface": "api", "platform": "api"},
        )
        runtime = SimpleNamespace(runtime_version_stamp={})

        def call(body, headers=None):
            return service.dispatch_post(
                path="/api/chat/session", body=body, headers=headers or {}, runtime=runtime,
                model_name="vool", workspace_root_provider=lambda: ".",
                client_host="127.0.0.1",
            )

        # Happy paths.
        assert call({"session_id": sid, "title": "Renamed"}).status == 200
        assert call({"session_id": sid, "archived": True}).status == 200
        assert call({"session_id": sid, "title": "café ☕ 日本語"}).status == 200  # unicode
        assert call({"session_id": sid, "title": "   "}).status == 200  # blank clears override
        # Body shape.
        assert call([1, 2]).status == 400  # non-dict body
        assert call({"title": "x"}).status == 400  # missing session_id
        assert call({"session_id": "", "title": "x"}).status == 400  # blank session_id
        assert call({"session_id": 123, "title": "x"}).status == 400  # non-string session_id
        assert call({"session_id": sid}).status == 400  # no supported change
        assert call({"session_id": sid, "title": "x", "foo": 1}).status == 400  # unknown field
        # Session existence.
        assert call({"session_id": "openclaw:ffffffffffffffffffff", "title": "x"}).status == 404
        # Title validation.
        assert call({"session_id": sid, "title": 5}).status == 400  # non-string
        assert call({"session_id": sid, "title": None}).status == 400  # explicit null != missing
        assert call({"session_id": sid, "title": "a\nb"}).status == 400  # newline
        assert call({"session_id": sid, "title": "a\tb"}).status == 400  # control char
        assert call({"session_id": sid, "title": "x" * 5000}).status == 400  # overlong
        # archived validation (real boolean only; explicit null distinct from missing).
        assert call({"session_id": sid, "archived": "true"}).status == 400
        assert call({"session_id": sid, "archived": 1}).status == 400
        assert call({"session_id": sid, "archived": None}).status == 400
        # Content-type + Origin + size.
        assert call({"session_id": sid, "title": "x"}, {"Content-Type": "text/plain"}).status == 415
        assert call({"session_id": sid, "title": "x"}, {"Content-Type": "application/json"}).status == 200
        assert call({"session_id": sid, "title": "x"}, {"Origin": "http://evil.example"}).status == 403
        assert call({"session_id": sid, "title": "x"}, {"Origin": "http://127.0.0.1:11435"}).status == 200
        assert call({"session_id": sid, "title": "x" * 70000}).status == 413  # oversized body
    finally:
        configure_runtime_home(None)


def test_vool_chat_page_is_self_contained_and_wired() -> None:
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "<title>VOOL</title>" in html
    assert "/api/chat" in html  # posts to the chat endpoint
    assert "/api/runtime/version" in html  # shows the running build
    assert "commit + (dirty ? '+dirty' : '')" in html  # never hide an uncommitted runtime behind a clean commit badge
    assert "verEl.title = v.build_id" in html  # full runtime identity remains inspectable
    # Header is clean by default: neither Trace nor Web0 sits in the HEADER; both are reachable
    # under Settings -> Advanced, which the Settings modal provides. The Web0 header link was gated
    # on a WEB0_ENABLED flag no caller ever set, so it rendered permanently invisible; it is gone
    # rather than shipping as an element that can never appear.
    header_markup = html.split("<header>", 1)[1].split("</header>", 1)[0]
    assert 'href="/trace"' not in header_markup
    assert 'href="/web0"' not in header_markup
    assert "/trace" in html  # diagnostics IS reachable -- under Settings -> Advanced
    assert "/web0" in html   # the .null browser IS reachable -- under Settings -> Advanced
    assert 'id="web0Link"' not in html
    assert "stream: true" in html
    # Sessions sidebar (Claude/Codex-style): list + reload endpoints, a New-chat control, and a
    # client-pinned session id sent with each turn so a reopened thread keys to the same memory.
    assert 'id="sidebar"' in html
    assert "/api/chat/sessions" in html  # thread list
    assert "/api/chat/history" in html  # transcript reload
    assert "/api/chat/session" in html  # rename / archive endpoint
    assert "session_id: run.chatId" in html  # pins the OWNING thread on every POST
    assert "localStorage" in html
    # Copyable + selectable messages, and session management (rename/archive).
    assert "msg-copy" in html
    assert "user-select" in html
    assert "startRename" in html and "archiveSession" in html
    # Settings surface (BYOK cloud key), projects grouping, hover message actions, live tokens.
    assert 'id="settingsBtn"' in html and 'id="settingsOverlay"' in html
    assert "/api/settings/credentials" in html and "llm.cloud.openrouter" in html
    assert 'id="newProject"' in html and "openProjectMenu" in html and "project_id" in html
    # Hover-gated message actions. Copy only: the thumbs were removed because there is no feedback
    # route on the server -- rateMsg wrote a localStorage key nothing ever read back.
    assert "msg-actions" in html and "msg-copy" in html
    assert "rateMsg" not in html
    assert "tc-tokens" in html  # live token estimate during a run
    # Live execution UX: opt into the typed task-event channel, demux it, and drive a
    # status card + execution panel from that one source, with the card's working mark
    # (VOOL's own hand-drawn logo) animated purely by CSS.
    assert "stream_task_events: true" in html  # opt into the typed channel
    assert "obj.vool_event" in html  # demux typed events from the answer stream
    assert "applyTaskEvent" in html  # the card/panel state machine
    assert "task-card" in html  # inline live status card
    assert 'id="xpanel"' in html  # right-side execution panel
    assert "TC_MARK_SVG" in html and 'class="tc-mark"' in html  # the animated VOOL working mark
    assert "tc-v" in html and "tc-ol" in html and "tc-or" in html and "tc-l" in html  # staged parts
    assert "/api/runtime/receipts" in html  # Receipts tab data source
    assert "AbortController" in html  # Stop control cancels the turn
    # Accessibility: reduced motion, screen-reader status, focus.
    assert "prefers-reduced-motion" in html
    assert "sr-only" in html
    # Self-contained: it must work inside an offline packaged bundle with no CDN/network. A
    # documentation example may contain a literal https:// URL; only executable external assets
    # would violate this contract.
    assert "http://" not in html
    assert '<script src="http' not in html and '<link rel="stylesheet" href="http' not in html
    # Embedded PNG bytes are opaque: their base64 alphabet can contain "cdn".
    # Keep checking page code/URLs for external dependencies.
    import re
    page_code = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", html)
    assert "cdn" not in page_code.lower()


class OllamaModelPullTests(unittest.TestCase):
    """First run must reach a running server, not ERR_CONNECTION_REFUSED behind a multi-GB pull."""

    def test_installed_check_matches_the_exact_tag_not_a_substring(self) -> None:
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_names",
            return_value=("qwen3:4b-instruct", "qwen2.5:7b"),
        ):
            # The old `active_model in <ollama list stdout>` check called this installed and skipped
            # the pull, leaving the runtime pointed at a tag Ollama does not have.
            self.assertFalse(ollama_model_installed("qwen3:4b"))
            self.assertTrue(ollama_model_installed("qwen3:4b-instruct"))

    def test_installed_check_resolves_an_implicit_latest_tag(self) -> None:
        with mock.patch(
            "core.web.api.runtime.installed_ollama_model_names",
            return_value=("nomic-embed-text:latest",),
        ):
            # The other direction: a bare name IS the :latest row, and must not trigger a re-pull.
            self.assertTrue(ollama_model_installed("nomic-embed-text"))
            self.assertTrue(ollama_model_installed("nomic-embed-text:latest"))
            self.assertFalse(ollama_model_installed("nomic-embed-text:v1.5"))
            self.assertFalse(ollama_model_installed(""))

    def test_installed_check_reports_absent_when_ollama_is_unreachable(self) -> None:
        with mock.patch("core.web.api.runtime.installed_ollama_model_names", return_value=()):
            self.assertFalse(ollama_model_installed("qwen3:4b"))

    def test_base_url_normalizes_ollamas_bare_host_port_convention(self) -> None:
        self.assertEqual(ollama_base_url({}), "http://127.0.0.1:11434")
        self.assertEqual(ollama_base_url({"OLLAMA_HOST": "127.0.0.1:11434"}), "http://127.0.0.1:11434")
        self.assertEqual(
            ollama_base_url({"VOOL_RAW_OLLAMA_API_URL": "http://box:9/"}), "http://box:9"
        )

    def test_pull_does_not_block_the_caller_and_publishes_live_progress(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_pull(model_tag, *, progress=None):
            started.set()
            progress.update(status="pulling", percent=42, detail="pulling manifest")
            release.wait(timeout=10)
            progress.update(status="ready", percent=100, detail="pull complete")

        with mock.patch("core.web.api.runtime.ensure_ollama_model", side_effect=slow_pull):
            began = time.monotonic()
            progress = start_ollama_model_pull("qwen3:4b")
            elapsed = time.monotonic() - began

            # The bind path continues immediately; the pull is still running behind it.
            self.assertLess(elapsed, 1.0)
            self.assertTrue(started.wait(timeout=5))
            self.assertEqual(progress.public["status"], "pulling")
            self.assertEqual(progress.public["percent"], 42)
            self.assertFalse(progress.wait(timeout=0.01))

            release.set()
            self.assertTrue(progress.wait(timeout=5))
            self.assertEqual(progress.status, "ready")

    def test_pull_thread_reports_failure_and_always_releases_waiters(self) -> None:
        # A failed pull must not leave the deferred prewarm blocked forever.
        with mock.patch("core.web.api.runtime.ensure_ollama_model", side_effect=RuntimeError("no such model")):
            progress = start_ollama_model_pull("vool-missing:1b")
            self.assertTrue(progress.wait(timeout=5))

        self.assertEqual(progress.status, "failed")
        self.assertIn("no such model", progress.public["detail"])

    def test_bootstrap_binds_the_port_while_the_model_is_still_downloading(self) -> None:
        # The #30 regression: bootstrap runs BEFORE uvicorn binds, so it must not sit on the pull.
        release = threading.Event()
        pulling = threading.Event()

        def slow_pull(model_tag, *, progress=None):
            pulling.set()
            progress.update(status="pulling", percent=7, detail="pulling manifest")
            release.wait(timeout=30)
            progress.update(status="ready", percent=100, detail="pull complete")

        persona = mock.Mock(persona_id="default")
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []

        with mock.patch.dict(os.environ, {"VOOL_OLLAMA_MODEL": "qwen3:4b"}, clear=False), mock.patch(
            "core.web.api.runtime.bootstrap_runtime_mode",
            return_value=mock.Mock(backend_selection=mock.Mock(backend_name="mlx", device="mps")),
        ), mock.patch("core.web.api.runtime.is_first_boot", return_value=False), mock.patch(
            "core.credit_ledger.ensure_starter_credits", return_value=False
        ), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth", return_value={"ok": True, "status": "ok"}
        ), mock.patch(
            "core.web.api.runtime.probe_machine", return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU")
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model", side_effect=slow_pull
        ), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-07-17T00:00:00.000000Z", "build_id": "test"},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon", return_value=mock.Mock()
        ), mock.patch("core.web.api.runtime.ModelRegistry", return_value=model_registry), mock.patch(
            "core.web.api.runtime.ensure_default_provider"
        ), mock.patch(
            "core.web.api.runtime.log_prewarm_results"
        ), mock.patch(
            "core.web.api.runtime.load_active_persona", return_value=persona
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name", return_value="VOOL"
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration", return_value=True
        ), mock.patch("core.web.api.runtime.VoolAgent", return_value=mock.Mock()), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity", return_value=(3, 3)
        ), mock.patch(
            "core.web.api.runtime.VoolDaemon", return_value=mock.Mock(config=mock.Mock(bind_port=49152))
        ), mock.patch(
            "core.web.api.runtime.startup_provider_capability_truth", return_value=tuple()
        ):
            began = time.monotonic()
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
                run_prewarm=False,
            )
            elapsed = time.monotonic() - began
            try:
                self.assertTrue(pulling.wait(timeout=5))
                # Bootstrap returned -> the caller binds the port -> the window loads.
                self.assertLess(elapsed, 5.0)
                self.assertIsNotNone(runtime.model_pull)
                self.assertEqual(runtime.model_pull.status, "pulling")

                # /healthz shallow-copies the stamp per request, so a client polling during the
                # pull is served live progress instead of a refused connection.
                served = dict(runtime.runtime_version_stamp)
                self.assertEqual(served["model_pull"]["status"], "pulling")
                self.assertEqual(served["model_pull"]["percent"], 7)
                self.assertEqual(served["model_pull"]["model"], "qwen3:4b")
            finally:
                release.set()
            self.assertTrue(runtime.model_pull.wait(timeout=10))
            self.assertEqual(dict(runtime.runtime_version_stamp)["model_pull"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()


def test_build_id_carries_a_platform_tag_so_apple_is_unique_vs_the_exe(tmp_path, monkeypatch):
    """The macOS build and the Windows .exe are built from the same commit, so release+commit alone
    would collide. A runtime-detected platform tag makes each lane's build id unique with no
    per-lane config edit."""
    import subprocess

    from core.web.api.runtime import _platform_release_tag, build_runtime_version_stamp

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, text=True)
    for plat, expected in (("darwin", "macos"), ("win32", "windows"), ("linux", "linux")):
        monkeypatch.setattr("sys.platform", plat)
        assert _platform_release_tag() == expected
        stamp = build_runtime_version_stamp(project_root=tmp_path, runtime_model_tag="qwen3:8b", workstation_version="t")
        assert stamp["platform"] == expected
        assert f"+{expected}" in stamp["build_id"], stamp["build_id"]
