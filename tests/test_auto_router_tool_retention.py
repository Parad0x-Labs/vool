"""A side-effecting tool executes exactly once, and its result survives model recovery.

Mission case 4 of the 2026-10-02 Auto-reliability lane: a real structured tool call is executed
through the production tool loop (`agent._maybe_execute_model_tool_intent` ->
`core.tool_intent_executor.execute_tool_intent`, the single seam every dispatch crosses); the
CONTINUATION call that should turn the tool's observation into the user's answer then hits a
persistent transient fault on the primary model. The router's bounded same-model retry runs, the
candidate loop falls over to the backup model, and the backup answers USING the observation the
executed tool produced -- the side-effecting tool itself is never re-executed just because the
author model changed.

The controlled side-effect fixture is the real ``workspace.write_file`` handler, wrapped with a
counter at the executor seam (the wrapper only counts; the real handler does the work into a real
workspace directory).
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

import requests

from adapters.base_adapter import ModelResponse
from apps.vool_agent import VoolAgent
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter, ModelExecutionDecision
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from storage.db import get_connection
from storage.migrations import run_migrations
from tests.test_auto_router_transient_recovery import FIFTY_THREE

FIFTY_THREE_PERSISTENT = requests.HTTPError(
    '503 Server Error: Service Unavailable for url: http://127.0.0.1:1234/v1/chat/completions'
    ' — provider said: {"error":"model overloaded"}'
)


def _tool_call(intent: str, **arguments):
    return SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent)


class _HybridRouter:
    """Call 1 is the scripted tool decision (the model asks for the side effect); every later
    call delegates to the REAL MemoryFirstRouter whose adapter seam carries the fault schedule,
    so the continuation's retry and fallback run through production code."""

    def __init__(self, real: MemoryFirstRouter):
        self.real = real
        self.calls = 0
        self.last_context: dict = {}

    def resolve_tool_intent(self, **kwargs):
        self.calls += 1
        self.last_context = dict(kwargs.get("source_context") or {})
        if self.calls == 1:
            return ModelExecutionDecision(
                source="scripted",
                task_hash="h",
                used_model=True,
                confidence=0.8,
                structured_output={
                    "intent": "workspace.write_file",
                    "arguments": {"path": "side-effect.txt", "content": "MARKER_SIDE_EFFECT_7f3a"},
                },
                tool_calls=(
                    _tool_call(
                        "workspace.write_file",
                        path="side-effect.txt",
                        content="MARKER_SIDE_EFFECT_7f3a",
                    ),
                ),
                validation_state="validated",
                provider_id="local-qwen-http:qwen-local",
            )
        return self.real.resolve_tool_intent(**kwargs)


def test_a_side_effecting_tool_runs_once_and_its_result_survives_recovery() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.execute("DELETE FROM candidate_knowledge_lane")
        conn.commit()
    finally:
        conn.close()

    workspace = pathlib.Path(tempfile.mkdtemp())

    registry = ModelRegistry()
    real_router = MemoryFirstRouter(registry)

    primary = registry.register_manifest(
        {
            "provider_name": "local-qwen-http", "model_name": "qwen-local", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False,
            "redistribution_allowed": True, "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:1234"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    backup = registry.register_manifest(
        {
            "provider_name": "local-backup-http", "model_name": "qwen-backup", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False,
            "redistribution_allowed": True, "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:2234"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(primary)
    certify_for_authorship(backup)

    # The continuation model lane: the primary answers 503 persistently (bounded retry exhausts,
    # the outer candidate loop falls to the backup), and the backup's reply is captured with the
    # prompt it was handed so the retention claim is asserted on the exact bytes the runtime sent.
    backup_prompts: list[str] = []

    primary_adapter = mock.Mock()
    primary_adapter.health_check.return_value = {"ok": True}
    primary_adapter.get_license_metadata.return_value = {"license_name": "Apache-2.0"}
    primary_adapter.estimate_cost_class.return_value = "free_local"
    primary_adapter.supports_streaming.return_value = False
    primary_adapter.run_text_task.side_effect = [FIFTY_THREE_PERSISTENT] * 4
    primary_adapter.run_structured_task.side_effect = [FIFTY_THREE_PERSISTENT] * 4

    def _backup_text(request, *args, **kwargs):
        import json as _json

        backup_prompts.append(
            _json.dumps(
                {
                    "prompt": str(getattr(request, "prompt", "") or ""),
                    "messages": list(getattr(request, "messages", None) or []),
                    "context": dict(getattr(request, "context", None) or {}),
                },
                default=str,
            )
        )
        return ModelResponse(
            output_text="The file was written; its content marker is MARKER_SIDE_EFFECT_7f3a.",
            confidence=0.8,
            provider_id=backup.provider_id,
            model_name=backup.model_name,
        )

    backup_adapter = mock.Mock()
    backup_adapter.health_check.return_value = {"ok": True}
    backup_adapter.get_license_metadata.return_value = {"license_name": "Apache-2.0"}
    backup_adapter.estimate_cost_class.return_value = "free_local"
    backup_adapter.supports_streaming.return_value = False
    backup_adapter.run_text_task.side_effect = _backup_text
    backup_adapter.run_structured_task.side_effect = _backup_text

    by_id = {primary.provider_id: primary_adapter, backup.provider_id: backup_adapter}

    hybrid = _HybridRouter(real_router)
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = hybrid
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(
        handled=False, stop_after=False, next_payload=None, reason=""
    )
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None

    dispatch_counts: list[str] = []
    dispatch_results: list[Any] = []
    from core import tool_intent_executor

    real_execute = tool_intent_executor.execute_tool_intent

    def _counting_execute(*args, **kwargs):
        spec = kwargs.get("intent") or (args[0] if args else "")
        if isinstance(spec, dict):
            intent = str(spec.get("intent") or "")
        else:
            intent = str(spec or "")
        dispatch_counts.append(intent)
        outcome = real_execute(*args, **kwargs)
        dispatch_results.append(outcome)
        return outcome

    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    from tests._toolchain_fixtures import internal_scope

    source_context = {
        "session_id": session,
        "runtime_session_id": session,
        "surface": "api",
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "workspace_binding": "project",
        "project_id": workspace.name,
        "turn_id": "turn-tool-retention",
        # The real permission gate stays engaged: the write is authorized through the typed
        # internal-authority scope the gate itself validates (modeless callers default to
        # MANUAL and the write would land as pending_approval, executing nothing).
        **internal_scope(
            "auto-router-reliability-tool-retention",
            "create_files",
            "modify_files",
            intents=("workspace.write_file",),
        ),
    }

    from core.tiered_context_loader import TieredContextResult

    context_result = TieredContextResult(
        bootstrap_items=[],
        relevant_items=[],
        cold_items=[],
        local_candidates=[],
        swarm_metadata=[],
        report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
        retrieval_confidence_score=0.15,
        cold_decision=mock.Mock(allow=False),
    )
    context_result.report.to_dict.return_value = {}
    context_result.report.total_tokens_used.return_value = 0
    context_result.assembled_context = lambda: "No strong local memory."

    with mock.patch(
        "core.memory_first_router.rank_provider_candidates",
        return_value=[primary, backup],
    ), mock.patch.object(
        registry, "build_adapter", side_effect=lambda manifest: by_id[manifest.provider_id]
    ), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ), mock.patch(
        "core.tool_intent_executor.execute_tool_intent", side_effect=_counting_execute
    ):
        result = agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="t1"),
            effective_input="write the marker file and tell me what it says",
            classification={"task_class": "debugging"},
            interpretation=HumanInputInterpretation(
                raw_text="write the marker file and tell me what it says",
                normalized_text="write the marker file and tell me what it says",
                reconstructed_text="write the marker file and tell me what it says",
                intent_mode="request",
                topic_hints=[],
                reference_targets=[],
                understanding_confidence=0.7,
                quality_flags=[],
            ),
            context_result=context_result,
            persona=load_active_persona("default"),
            session_id=session,
            source_context=source_context,
            surface="api",
        )

    # ---- the side effect ran exactly once, through the real handler ----
    assert dispatch_counts.count("workspace.write_file") == 1, dispatch_counts
    write_outcomes = [
        r
        for r in dispatch_results
        if getattr(r, "tool_name", "") == "workspace.write_file" and getattr(r, "status", "") == "executed"
    ]
    assert write_outcomes, dispatch_results
    written = workspace / "side-effect.txt"
    assert written.exists(), "the real handler must have written the file into the turn's workspace"
    assert "MARKER_SIDE_EFFECT_7f3a" in written.read_text(encoding="utf-8")

    # ---- the continuation recovered on a usable answer (fallback model) ----
    details = (result or {}).get("details") or {}
    assert (result or {}).get("reply") or details.get("tool_steps"), "no usable turn output"

    # ---- the observation reached the model that answered ----
    assert backup_prompts, "the backup model was never consulted for the continuation"
    assert any("MARKER_SIDE_EFFECT_7f3a" in prompt for prompt in backup_prompts), (
        "the executed tool's result did not survive the recovery to the answering model"
    )
    assert primary_adapter.run_text_task.call_count + primary_adapter.run_structured_task.call_count == 2, (
        "the primary must get exactly its bounded retry pair, no more"
    )
