from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import pytest

from core import conversation_summarizer, provider_invocation_gateway
from core.context_manifest import build_context_manifest
from core.context_scope import ContextAccessPolicy, annotate_and_filter
from core.prompt_assembly_report import ContextItem
from core.provenance_store import load_manifest, store_manifest
from core.provider_invocation_gateway import (
    ProviderInvocationValidationError,
    load_provider_manifest,
    payload_hash,
    seal_direct_provider_invocation,
)
from core.runtime_paths import configure_runtime_home

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _CallSite(NamedTuple):
    path: str
    qualname: str
    seal_call: str
    transport_call: str


_SEALED_CALL_SITES = (
    _CallSite(
        "adapters/generic_openai_cloud_provider.py",
        "GenericOpenAICloudProvider.send_request",
        "seal_provider_invocation(",
        "transport.request_json(",
    ),
    _CallSite(
        "adapters/openrouter_cloud_provider.py",
        "OpenRouterCloudProvider.send_request",
        "seal_provider_invocation(",
        "transport.request_json(",
    ),
    _CallSite(
        "adapters/openai_compatible_adapter.py",
        "OpenAICompatibleAdapter._invoke_openai_compatible",
        "seal_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "adapters/openai_compatible_adapter.py",
        "OpenAICompatibleAdapter._invoke_ollama_chat",
        "seal_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "adapters/openai_compatible_adapter.py",
        "OpenAICompatibleAdapter._stream_openai_compatible",
        "seal_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "adapters/openai_compatible_adapter.py",
        "OpenAICompatibleAdapter._stream_ollama_chat",
        "seal_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "adapters/local_subprocess_adapter.py",
        "LocalSubprocessAdapter._invoke_with_env",
        "seal_provider_invocation(",
        "subprocess.Popen(",
    ),
    _CallSite(
        "adapters/optional_transformers_adapter.py",
        "OptionalTransformersAdapter.invoke",
        "seal_provider_invocation(",
        "raw = model(",
    ),
    _CallSite(
        "adapters/peft_lora_adapter.py",
        "PeftLoRAAdapter.invoke",
        "seal_provider_invocation(",
        "output_ids = model.generate(",
    ),
    _CallSite(
        "core/agent_runtime/builder/controller.py",
        "build_ollama_generate_fn",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/autonomous_topic_research.py",
        "_generate_refinement_queries",
        "seal_direct_provider_invocation(",
        '__import__("requests").post(',
    ),
    _CallSite(
        "core/backend_acceleration_truth.py",
        "_probe_llamacpp_generation",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/brain_hive_research.py",
        "_derive_questions_via_model",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/conversation_summarizer.py",
        "_call_ollama",
        "seal_direct_provider_invocation(",
        "urllib.request.urlopen(",
    ),
    _CallSite(
        "core/craft_upgrade.py",
        "ollama_client",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/embedding_service.py",
        "_ollama_embed",
        "seal_direct_provider_invocation(",
        "urllib.request.urlopen(",
    ),
    _CallSite(
        "core/fact_extractor.py",
        "FactExtractor._call_model",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/gpu_inference_probe.py",
        "verify_gpu_inference",
        "seal_direct_provider_invocation(",
        "status, parsed, error_text = post(",
    ),
    _CallSite(
        "core/llamacpp_capability_probe.py",
        "_post_completion_tokens_per_second",
        "seal_direct_provider_invocation(",
        "urllib.request.urlopen(",
    ),
    _CallSite(
        "core/task_router.py",
        "_classify_via_model",
        "seal_direct_provider_invocation(",
        "_req.post(",
    ),
    _CallSite(
        "core/intent_arbiter.py",
        "arbitrate",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/intent_arbiter.py",
        "prewarm_async._warm",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
    _CallSite(
        "core/media_tools.py",
        "_generate",
        "seal_direct_provider_invocation(",
        "open_remote(",
    ),
    _CallSite(
        "core/adaptation_autopilot.py",
        "_generate_eval_outputs",
        "seal_direct_provider_invocation(",
        "model.generate(",
    ),
    _CallSite(
        "core/semantic_judge.py",
        "evaluate_semantic_agreement",
        "seal_direct_provider_invocation(",
        "result = judge(",
    ),
    _CallSite(
        "adapters/openai_compatible_adapter.py",
        "OpenAICompatibleAdapter.prewarm",
        "seal_direct_provider_invocation(",
        "requests.post(",
    ),
)

_ADAPTER_DELEGATIONS = (
    ("core/llm_reasoning.py", "invoke_llm_reasoning", "invoke_provider_execution_boundary("),
    ("core/llm_reasoning.py", "grade_output", "invoke_provider_execution_boundary("),
    (
        "core/media_analysis_pipeline.py",
        "MediaAnalysisPipeline.analyze",
        "invoke_provider_execution_boundary(",
    ),
    (
        "core/model_teacher_pipeline.py",
        "ModelTeacherPipeline._run_candidate",
        "invoke_provider_execution_boundary(",
    ),
)


@pytest.fixture
def provider_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _qualified_node_source(path: str, qualname: str) -> str:
    source = (_REPO_ROOT / path).read_text(encoding="utf-8")
    parent: ast.AST = ast.parse(source)
    for part in qualname.split("."):
        children = getattr(parent, "body", ())
        parent = next(
            node
            for node in children
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == part
        )
    segment = ast.get_source_segment(source, parent)
    assert segment is not None
    return segment


def test_direct_transport_is_blocked_when_sealing_fails(monkeypatch) -> None:
    transport = mock.Mock(side_effect=AssertionError("transport must not run"))
    monkeypatch.setattr(
        conversation_summarizer,
        "seal_direct_provider_invocation",
        mock.Mock(side_effect=RuntimeError("manifest_store_failed")),
    )
    monkeypatch.setattr(conversation_summarizer.urllib.request, "urlopen", transport)

    with pytest.raises(RuntimeError, match="manifest_store_failed"):
        conversation_summarizer._call_ollama(
            "local-test-model",
            [{"role": "user", "content": "do not send"}],
        )

    transport.assert_not_called()


def test_direct_manifest_records_identity_without_prompt_or_secret(
    provider_home,
) -> None:
    raw_prompt = "RAW-PROMPT-MUST-NOT-BE-STORED-814"
    secret = "sk-test-MUST-NOT-BE-STORED-247"
    payload = {
        "model": "local-test-model",
        "messages": [{"role": "user", "content": raw_prompt}],
        "api_key": secret,
    }

    context_manifest = build_context_manifest(
        task_id="task-direct-identity",
        trace_id="request-direct-100",
        evidence_items=[],
        source_metadata=[],
        chat_id="chat-direct-200",
        project_id="project-direct-300",
        capsule_version="none",
    )
    store_manifest(context_manifest)

    permit = seal_direct_provider_invocation(
        provider_id="ollama:direct-test",
        model_id="local-test-model",
        operation="chat",
        payload=payload,
        request_id="request-direct-100",
        context_manifest={
            "context_manifest_id": context_manifest.manifest_id,
            "chat_id": "chat-direct-200",
            "project_id": "project-direct-300",
            "capsule_version": "none",
            "items_included": [],
            "items_excluded": [],
        },
        max_output_tokens=64,
        header_names=("Authorization", "Content-Type"),
    )

    manifest = load_provider_manifest(permit.manifest.manifest_id)
    assert manifest is not None
    assert manifest["request_id"] == "request-direct-100"
    assert manifest["context_manifest_id"] == context_manifest.manifest_id
    assert manifest["context_trace_id"] == "request-direct-100"
    assert manifest["provider_messages"] == (
        {
            "role": "user",
            "content_hash": hashlib.sha256(
                json.dumps(
                    raw_prompt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                ).hexdigest(),
            },
        )
    assert manifest["chat_id"] == "chat-direct-200"
    assert manifest["project_id"] == "project-direct-300"
    assert manifest["provider_id"] == "ollama:direct-test"
    assert manifest["model_id"] == "local-test-model"
    assert manifest["operation"] == "chat"
    assert manifest["payload_hash"] == payload_hash(payload)
    assert permit.consume() == payload

    encoded_manifest = json.dumps(manifest, sort_keys=True)
    assert raw_prompt not in encoded_manifest
    assert secret not in encoded_manifest


def test_direct_empty_context_gets_a_signed_context_manifest_before_io(
    provider_home,
) -> None:
    permit = seal_direct_provider_invocation(
        provider_id="ollama:direct-empty",
        model_id="local-test-model",
        operation="chat",
        payload={"prompt": "minimal provider payload"},
        request_id="request-direct-empty-100",
    )

    assert permit.manifest.context_manifest_id
    assert permit.manifest.context_trace_id == "request-direct-empty-100"
    context_manifest = load_manifest(permit.manifest.context_manifest_id)
    assert context_manifest is not None
    assert context_manifest["selected_items"] == []
    assert context_manifest["excluded_items"] == []
    assert "context_manifest_auto_empty" in context_manifest["redaction_markers"]


def test_direct_nonempty_context_is_sealed_into_context_manifest(
    provider_home,
) -> None:
    permit = seal_direct_provider_invocation(
        provider_id="ollama:direct-unlinked",
        model_id="local-test-model",
        operation="chat",
        payload={"prompt": "context must be linked"},
        request_id="request-direct-unlinked-100",
        context_manifest={
            "items_included": [
                {
                    "item_id": "item-1",
                    "source_type": "dialogue_turn",
                    "scope": "chat",
                    "source_id": "chat-1",
                    "content_hash": "hash-1",
                    "reason": "current_chat",
                }
            ]
        },
    )

    context_manifest = load_manifest(permit.manifest.context_manifest_id)
    assert context_manifest is not None
    assert len(context_manifest["selected_items"]) == 1


def test_required_context_manifest_link_is_verified_before_provider_sealing(
    provider_home,
) -> None:
    context_manifest = build_context_manifest(
        task_id="task-linked-trace",
        trace_id="trace-linked-context",
        evidence_items=[],
        source_metadata=[],
        chat_id="chat-linked-context",
        access_policy={
            "chat_id": "chat-linked-context",
            "namespace_state": "active",
        },
    )
    store_manifest(context_manifest)
    context = {
        "context_manifest_required": True,
        "context_manifest_id": context_manifest.manifest_id,
        "context_manifest_trace_id": context_manifest.trace_id,
        "chat_id": "chat-linked-context",
        "items_included": [],
        "items_excluded": [],
    }

    permit = seal_direct_provider_invocation(
        provider_id="ollama:direct-test",
        model_id="local-test-model",
        operation="chat",
        payload={"messages": [{"role": "user", "content": "hello"}]},
        request_id="request-linked-context",
        context_manifest=context,
    )

    assert permit.manifest.context_manifest_id == context_manifest.manifest_id
    assert permit.manifest.context_trace_id == context_manifest.trace_id

    context["context_manifest_trace_id"] = "trace-mismatch"
    with pytest.raises(
        ProviderInvocationValidationError,
        match="context manifest trace id does not match",
    ):
        seal_direct_provider_invocation(
            provider_id="ollama:direct-test",
            model_id="local-test-model",
            operation="chat",
            payload={"messages": [{"role": "user", "content": "hello"}]},
            request_id="request-linked-context-mismatch",
            context_manifest=context,
        )


def test_provider_manifest_rejects_unproven_selected_context(
    provider_home,
) -> None:
    with pytest.raises(
        ProviderInvocationValidationError,
        match="selected context source 0 is missing content_hash",
    ):
        seal_direct_provider_invocation(
            provider_id="ollama:direct-test",
            model_id="local-test-model",
            operation="chat",
            payload={
                "messages": [{"role": "user", "content": "hello"}]
            },
            request_id="request-unproven-context",
            context_manifest={
                "chat_id": "chat-unproven",
                "capsule_version": "none",
                "items_included": [
                    {
                        "item_id": "legacy-unproven",
                        "source_type": "runtime_memory",
                        "reason": "legacy_retrieval",
                        "metadata": {
                            "scope": "chat",
                            "source_id": "memory-unproven",
                        },
                    }
                ],
                "items_excluded": [],
            },
        )


def test_legacy_user_heuristic_is_normalized_before_manifest_sealing(
    provider_home,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        provider_invocation_gateway.signer,
        "sign",
        lambda _payload: base64.b64encode(b"x" * 64).decode(),
    )
    monkeypatch.setattr(
        provider_invocation_gateway.signer,
        "get_local_peer_id",
        lambda: "test-peer",
    )
    monkeypatch.setattr(
        provider_invocation_gateway.signer,
        "verify",
        lambda *_args: True,
    )
    policy = ContextAccessPolicy.for_request(
        session_id="chat:legacy-user-heuristic",
        source_context={"surface": "local"},
    )
    legacy = ContextItem(
        item_id="user-heuristic-response_style-concise_direct",
        layer="relevant",
        source_type="user_heuristic",
        title="User heuristic response style",
        content="Keep answers concise and direct.",
        metadata={
            "scope": "user_profile",
            "source": "user_heuristic",
            "source_id": "profile:confirmed",
            "status": "active",
            "provenance": {"kind": "direct_user_observation"},
        },
    )
    allowed, denied = annotate_and_filter([legacy], policy)

    assert not denied
    permit = seal_direct_provider_invocation(
        provider_id="ollama:direct-test",
        model_id="local-test-model",
        operation="chat",
        payload={"messages": [{"role": "user", "content": "hello"}]},
        request_id="request-legacy-user-heuristic",
        context_manifest={
            "chat_id": policy.chat_id,
            "capsule_version": "none",
            "items_included": [
                allowed[0].to_record(
                    included=True,
                    reason="inferred_user_heuristic",
                )
            ],
            "items_excluded": [],
        },
    )

    assert permit.manifest.selected_sources[0]["content_hash"]


@pytest.mark.parametrize(
    "call_site",
    _SEALED_CALL_SITES,
    ids=lambda item: f"{item.path}:{item.qualname}",
)
def test_identified_inference_call_site_seals_before_transport(
    call_site: _CallSite,
) -> None:
    source = _qualified_node_source(call_site.path, call_site.qualname)
    seal_offset = source.find(call_site.seal_call)
    transport_offset = source.find(call_site.transport_call)

    assert seal_offset >= 0
    assert transport_offset >= 0
    assert seal_offset < transport_offset
    assert "permit.consume()" in source


@pytest.mark.parametrize(
    ("path", "qualname", "adapter_call"),
    _ADAPTER_DELEGATIONS,
    ids=lambda item: str(item),
)
def test_orchestrated_model_paths_delegate_through_canonical_invocation_seam(
    path: str,
    qualname: str,
    adapter_call: str,
) -> None:
    source = _qualified_node_source(path, qualname)

    assert adapter_call in source
    assert "requests.post(" not in source
    assert "urllib.request.urlopen(" not in source
    assert "subprocess.Popen(" not in source


def test_paid_media_generation_is_gateway_sealed() -> None:
    source = _qualified_node_source("core/media_tools.py", "_generate")

    assert "seal_direct_provider_invocation(" in source
    assert "permit.consume()" in source
    # F-05 repair: the paid media call goes through THE one outbound door
    # (veto + per-turn reporting); a raw urlopen fallback here would be a
    # model-content-bearing socket that bypasses policy.
    assert "open_remote(" in source
    assert "urllib.request.urlopen" not in source
