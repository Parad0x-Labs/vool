"""Served visibility for tool-call recovery: typed rejection on every tool_intent turn, and a
durable Activity record of the resolution state.

Amendment to the P0: parsing into CloudToolCall is necessary but insufficient. On the SERVED
path a rejected recovery must surface as the typed failure the router already classifies — on
every tool_intent turn, not only when `tools_required` was stamped — and the resolution state
(parsed / repaired / rejected) must land in a DURABLE record, not die with the in-memory
request. The persistence seam mirrors the existing `prompt_budget` precedent: the adapter writes
`request.metadata`, and the router's model routing events read it back into
`runtime_session_events.details_json` via `emit_runtime_event`.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_flags
from core.cloud_tool_call_contract import MalformedToolArgumentsError, ToolCallParseError, build_cloud_tool_definitions
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.task_router import create_task_record
from storage.db import get_connection
from storage.migrations import run_migrations

TOOLS = build_cloud_tool_definitions(
    [
        {
            "intent": "workspace.read_file",
            "description": "Read a workspace file.",
            "arguments": {"path": "string"},
        }
    ]
)


# --- seam 1: the Ollama lane raises typed on ANY tool_intent turn, not only tools_required ----


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    body: bytes = b"{}"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        cls = type(self)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cls.body)))
        self.end_headers()
        self.wfile.write(cls.body)


@pytest.fixture()
def fixture_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=5)
    _FixtureHandler.body = b"{}"


def test_rejected_recovery_raises_typed_even_when_tools_not_required(fixture_server) -> None:
    """The served router's resolve_tool_intent stamps output_mode=tool_intent but not always
    tools_required. A rejected recovery (markup expressed a call, the call is broken) must still
    fail typed — silently handing the broken markup downstream reproduces the Path C mush this
    P0 exists to kill."""

    _FixtureHandler.body = json.dumps(
        {"message": {"content": '<tool_call>{"arguments": {"path": "x.txt"}}</tool_call>'}}
    ).encode("utf-8")
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-ollama",
            provider_id="fixture-ollama:model",
            model_name="model",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": f"http://127.0.0.1:{fixture_server}", "timeout_seconds": 5.0},
        )
    )
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="read the file",
        output_mode="tool_intent",
        messages=[{"role": "user", "content": "read the file"}],
        tools=TOOLS,
        tools_required=False,
    )
    with runtime_flags.override("ollama_native_tools", True):
        with pytest.raises(ToolCallParseError):
            adapter.run_structured_task(request)
    resolution = request.metadata.get("tool_call_resolution") or {}
    assert resolution.get("state") == "rejected"
    assert resolution.get("rejection_kind") == "missing_name"


# --- seam 2: the router persists the resolution into the durable model routing events --------


@pytest.fixture(autouse=True)
def _clean_state():
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _manifest(registry: ModelRegistry):
    return registry.register_manifest(
        {
            "provider_name": "local-qwen-http", "model_name": "qwen-local", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:1234"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )


def _invoke(adapter_behavior):
    registry = ModelRegistry()
    manifest = _manifest(registry)
    router = MemoryFirstRouter(registry)
    task = create_task_record("read the probe file")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = adapter_behavior
    events: list[tuple[str, dict]] = []

    def _record(_source_context, *, event_type, message, details):
        events.append((event_type, dict(details or {})))

    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        with mock.patch("core.memory_first_router.emit_runtime_event", side_effect=_record):
            router._invoke_manifest(
                manifest=manifest,
                request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
                output_mode="tool_intent",
                task=task,
                source_context={"requested_model": manifest.model_name, "_owner_local": True},
            )
    return events


def test_router_persists_resolution_on_tool_parse_failure() -> None:
    rejected = {
        "state": "rejected",
        "dialect": "hermes",
        "repair_applied": "",
        "rejection_kind": "missing_name",
        "detail": "a tool call was expressed with no tool name",
        "call_count": 0,
        "call_ids": [],
    }

    def _fail(request: ModelRequest):
        request.metadata["tool_call_resolution"] = dict(rejected)
        raise MalformedToolArgumentsError("synthetic rejected recovery")

    events = _invoke(_fail)
    failed = [details for event_type, details in events if event_type == "model.call_failed"]
    assert failed, f"no model.call_failed event observed; events={[(t, sorted(d)) for t, d in events]}"
    assert failed[-1].get("tool_call_resolution") == rejected, (
        "the rejected resolution must ride the durable model.call_failed event; "
        f"details carried keys {sorted(failed[-1])}"
    )


def test_router_persists_resolution_on_completed_call() -> None:
    parsed = {
        "state": "parsed",
        "dialect": "hermes",
        "repair_applied": "",
        "rejection_kind": "",
        "detail": "",
        "call_count": 1,
        "call_ids": ["rec_f7132514a6f4"],
    }

    def _succeed(request: ModelRequest):
        request.metadata["tool_call_resolution"] = dict(parsed)
        return ModelResponse(
            output_text='{"intent": "workspace.read_file", "arguments": {"path": "x.txt"}}',
            confidence=0.8,
            raw_response={},
            usage={},
            provider_id="local-qwen-http:qwen-local",
            model_name="qwen-local",
            output_mode="tool_intent",
        )

    events = _invoke(_succeed)
    completed = [details for event_type, details in events if event_type == "model.call_completed"]
    assert completed, f"no model.call_completed event observed; events={[(t, sorted(d)) for t, d in events]}"
    assert completed[-1].get("tool_call_resolution") == parsed


def test_an_event_without_a_resolution_does_not_carry_an_empty_field() -> None:
    def _succeed(_request: ModelRequest):
        return ModelResponse(
            output_text='{"intent": "workspace.read_file", "arguments": {"path": "x.txt"}}',
            confidence=0.8,
            raw_response={},
            usage={},
            provider_id="local-qwen-http:qwen-local",
            model_name="qwen-local",
            output_mode="tool_intent",
        )

    events = _invoke(_succeed)
    completed = [details for event_type, details in events if event_type == "model.call_completed"]
    assert completed
    assert "tool_call_resolution" not in completed[-1]


# --- seam 3: a rejected call must not end as a SUCCEEDED plain-chat turn ---------------------


def test_router_stamps_the_rejection_on_source_context() -> None:
    """The facade decides what a zero-steps turn becomes AFTER the router unwound, so the
    rejection has to travel on the one object both layers share — source_context (the same
    bridge `_record_model_provenance` already uses)."""

    rejected = {
        "state": "rejected",
        "dialect": "hermes",
        "repair_applied": "",
        "rejection_kind": "missing_name",
        "detail": "a tool call was expressed with no tool name",
        "call_count": 0,
        "call_ids": [],
    }

    def _fail(request: ModelRequest):
        request.metadata["tool_call_resolution"] = dict(rejected)
        raise MalformedToolArgumentsError("synthetic rejected recovery")

    registry = ModelRegistry()
    manifest = _manifest(registry)
    router = MemoryFirstRouter(registry)
    task = create_task_record("read the probe file")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = _fail
    source_context = {"requested_model": manifest.model_name, "_owner_local": True}
    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context=source_context,
        )
    marker = source_context.get("last_tool_call_rejection") or {}
    assert marker.get("error_kind") == "malformed_tool_arguments"
    assert (marker.get("resolution") or {}).get("rejection_kind") == "missing_name"


def test_a_rejected_call_with_no_executed_tool_is_a_typed_turn_failure() -> None:
    """Live-measured 2026-09-01 (drive B, isolated daemon 11442): a missing-name call was
    rejected typed at the router, no tool ran — and the turn then fell through to plain chat,
    which narrated an un-run plan and closed SUCCEEDED/fulfilled. A refused call with nothing
    executed must terminate as a typed tool failure, not as a successful non-answer."""

    from apps.vool_agent import VoolAgent

    class _RejectingRouter:
        def __init__(self):
            self.calls = 0

        def resolve(self, **_kwargs):  # plain-chat synthesis must not be reached
            raise AssertionError("a rejected zero-step turn must not fall through to plain chat")

        def resolve_tool_intent(self, **kwargs):
            self.calls += 1
            context = kwargs.get("source_context")
            if isinstance(context, dict):
                # Exactly what memory_first_router's ToolCallParseError branch stamps.
                context["last_tool_call_rejection"] = {
                    "error_kind": "malformed_tool_arguments",
                    "provider_id": "ollama-local:qwen2.5:7b",
                    "resolution": {
                        "state": "rejected",
                        "rejection_kind": "missing_name",
                        "detail": "a tool call was expressed with no tool name",
                        "dialect": "hermes",
                        "call_count": 0,
                        "call_ids": [],
                    },
                }
            return SimpleNamespace(
                output_text="",
                provider_id="ollama-local:qwen2.5:7b",
                used_model=False,
                confidence=0.0,
                trust_score=0.0,
                validation_state="contract_failed",
                details={},
                source="provider_execution",
                model_name="qwen2.5:7b",
                provider_name="ollama-local",
                cache_hit=False,
                candidate_id=None,
                failover_used=False,
                structured_output=None,
                tool_calls=(),
                task_hash="h",
            )

    router = _RejectingRouter()
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    events: list[dict] = []
    agent._emit_runtime_event = lambda _context, **payload: events.append(payload)

    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t-rejected"),
        effective_input="what passphrase is written inside secret_of_the_day.log?",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(
            local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.0
        ),
        persona=SimpleNamespace(),
        session_id="openclaw:aaaaaaaaaaaaaaaaaaaa",
        source_context={"surface": "api", "workspace": "", "workspace_root": ""},
        surface="api",
    )

    assert result is not None, "a rejected call must not fall through to plain chat as None"
    assert result.get("mode") == "tool_failed"
    assert result.get("success") is False
    assert result.get("task_outcome") == "failed"
    assert result.get("status") == "tool_call_rejected"
    assert "missing_name" in json.dumps(result.get("details") or {})
    response_text = str(result.get("response") or "")
    assert response_text, "the typed failure must carry an honest user-facing sentence"
    assert "passphrase" not in response_text.lower() or "could not" in response_text.lower()


def test_action_fast_path_result_carries_its_success_verdict() -> None:
    """run_once closes the turn-root attempt from `result.get("success", True)` — a default that
    turns an ABSENT verdict into SUCCEEDED. The action-fast-path payload omitted the key, so a
    typed tool failure served through it closed its attempt SUCCEEDED while its own checkpoint
    said failed (measured live 2026-09-01, drive B, isolated daemon 11442). The payload must
    carry the verdict it was built from."""

    from core.agent_runtime.fast_command_surface import action_fast_path_result

    agent = mock.Mock()
    agent._turn_result.return_value = SimpleNamespace(response_class=SimpleNamespace(value="action"))
    agent._decorate_chat_response.return_value = "I could not complete this."
    agent.backend_name = "test-backend"
    agent.device = "cpu"

    result = action_fast_path_result(
        agent,
        task_id="t1",
        session_id="s1",
        user_input="read the probe file",
        response="I could not complete this.",
        confidence=0.2,
        source_context={"surface": "api"},
        reason="model_tool_intent_tool_call_rejected",
        success=False,
        details={"error_kind": "malformed_tool_call"},
        mode_override="tool_failed",
        task_outcome="failed",
        workflow_summary="",
        append_conversation_event_fn=lambda **_kw: None,
        audit_logger_module=mock.Mock(),
        explicit_planner_style_requested_fn=lambda _text: False,
    )
    assert result.get("success") is False, (
        "the fast-path result must carry its own success verdict; an absent key closes the "
        f"turn attempt SUCCEEDED by default (keys: {sorted(result)})"
    )

    succeeded = action_fast_path_result(
        agent,
        task_id="t2",
        session_id="s1",
        user_input="read the probe file",
        response="done",
        confidence=0.9,
        source_context={"surface": "api"},
        reason="workspace_runtime_fast_path",
        success=True,
        details={},
        workflow_summary="",
        append_conversation_event_fn=lambda **_kw: None,
        audit_logger_module=mock.Mock(),
        explicit_planner_style_requested_fn=lambda _text: False,
    )
    assert succeeded.get("success") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
