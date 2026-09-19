from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.memory_first_router import MemoryFirstRouter
from core.provider_invocation_gateway import (
    load_provider_manifest,
    payload_hash,
)
from core.runtime_paths import configure_runtime_home
from core.vool_memory import VoolMemory
from storage.db import get_connection
from storage.model_provider_manifest import ModelProviderManifest


@pytest.fixture
def provider_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            metadata={
                "runtime_family": "ollama",
                "deployment_class": "local",
            },
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "timeout_seconds": 5.0,
                "temperature": 0.2,
                "think": False,
            },
        )
    )


def _request(secret_marker: str) -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt=secret_marker,
        system_prompt="Answer only from the supplied current-chat context.",
        messages=[
            {
                "role": "system",
                "content": "Current-chat marker CURRENT-CHAT-811.",
            },
            {"role": "user", "content": secret_marker},
        ],
        trace_id="trace-provider-boundary",
        context={
            "chat_id": "chat:current",
            "project_id": "project-current",
            "capsule_version": "none",
            "items_included": [
                {
                    "item_id": "current-item",
                    "source_type": "runtime_memory",
                    "reason": "current_chat_scope",
                    "metadata": {
                        "scope": "chat",
                        "source_id": "memory-current",
                        "content_hash": "hash-current",
                    },
                }
            ],
            "items_excluded": [
                {
                    "item_id": "foreign-item",
                    "source_type": "runtime_memory",
                    "reason": "cross_chat_denied",
                    "metadata": {
                        "scope": "chat",
                        "source_id": "memory-foreign",
                        "content_hash": "hash-foreign",
                    },
                }
            ],
        },
        metadata={
            "memory_prompt": {
                "enabled": True,
                "runtime_home": "must-not-be-read",
            }
        },
    )


def test_exact_buffered_payload_is_sealed_without_foreign_memory(
    provider_home,
) -> None:
    memory = VoolMemory(runtime_home=provider_home)
    memory.block_write(
        "project_context",
        "Foreign marker FOREIGN-PROVIDER-992 must never leave.",
    )
    memory.close()
    adapter = _adapter()
    marker = "RAW-PROMPT-MUST-NOT-BE-STORED-741"
    request = _request(marker)
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "message": {"content": "safe"},
        "eval_count": 1,
    }

    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        return_value=response,
    ) as post_mock:
        result = adapter.run_text_task(request)

    outbound = post_mock.call_args.kwargs["json"]
    manifest = load_provider_manifest(
        request.metadata["provider_manifest_id"]
    )
    assert result.output_text == "safe"
    assert "FOREIGN-PROVIDER-992" not in str(outbound)
    assert outbound["messages"][0]["content"] == (
        "Current-chat marker CURRENT-CHAT-811."
    )
    assert manifest is not None
    assert manifest["payload_hash"] == payload_hash(outbound)
    assert manifest["chat_id"] == "chat:current"
    assert manifest["project_id"] == "project-current"
    assert manifest["capsule_version"] == "none"
    assert manifest["selected_sources"][0]["source_id"] == "memory-current"
    assert manifest["history_used"] is False
    assert manifest["project_context_used"] is False
    assert manifest["user_profile_used"] is False
    assert manifest["semantic_retrieval_used"] is True
    assert manifest["action_receipts_used"] is False
    assert manifest["provider_messages"] == (
        {
            "role": "system",
            "content_hash": hashlib.sha256(
                json.dumps(
                    "Current-chat marker CURRENT-CHAT-811.",
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
        },
        {
            "role": "user",
            "content_hash": hashlib.sha256(
                json.dumps(
                    marker,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
        },
    )
    assert marker not in json.dumps(manifest)


def test_manifest_storage_failure_prevents_network_io(provider_home) -> None:
    adapter = _adapter()
    request = _request("do not send")

    with (
        mock.patch(
            "adapters.openai_compatible_adapter.seal_provider_invocation",
            side_effect=RuntimeError("manifest_store_failed"),
        ),
        mock.patch(
            "adapters.openai_compatible_adapter.requests.post"
        ) as post_mock,
        pytest.raises(RuntimeError, match="manifest_store_failed"),
    ):
        adapter.run_text_task(request)

    post_mock.assert_not_called()


def test_verifier_manifest_preserves_primary_context_lineage(
    provider_home,
) -> None:
    router = MemoryFirstRouter()
    primary_manifest = ModelProviderManifest(
        provider_name="primary-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local"},
    )
    verifier_manifest = ModelProviderManifest(
        provider_name="verifier-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local"},
    )
    primary_request = ModelRequest(
        task_kind="action_plan",
        prompt="Inspect the current project state.",
        trace_id="trace-verifier-lineage",
        context={
            "chat_id": "chat-verifier-lineage",
            "project_id": "project-verifier-lineage",
            "capsule_version": "capsule-verifier-lineage",
            "items_included": [
                {
                    "item_id": "dialogue-current",
                    "source_type": "recent_dialogue",
                    "scope": "chat",
                    "source_id": "chat-turn-current",
                    "content_hash": "hash-dialogue-current",
                    "reason": "current_chat_scope",
                },
                {
                    "item_id": "memory-current",
                    "source_type": "semantic_memory",
                    "scope": "chat",
                    "source_id": "memory-current",
                    "content_hash": "hash-memory-current",
                    "reason": "semantic_retrieval",
                },
                {
                    "item_id": "project-current",
                    "source_type": "project_context",
                    "scope": "project",
                    "source_id": "project-current",
                    "content_hash": "hash-project-current",
                    "reason": "explicit_project_grant",
                },
            ],
            "items_excluded": [],
        },
    )
    primary_text = "Primary answer content must stay out of the manifest."
    primary_response = ModelResponse(
        output_text=primary_text,
        response_id="response-primary-lineage",
        model_call_id="model-call-primary-lineage",
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "VERDICT: PASS - correct and safe.",
                }
            }
        ],
        "usage": {"completion_tokens": 4},
    }

    with (
        mock.patch.object(
            router.registry,
            "build_adapter",
            return_value=OpenAICompatibleAdapter(verifier_manifest),
        ),
        mock.patch(
            "core.memory_first_router.should_probe_health",
            return_value=False,
        ),
        mock.patch(
            "adapters.openai_compatible_adapter.requests.post",
            return_value=response,
        ) as post_mock,
    ):
        status = router._verify_primary_response(
            primary_manifest=primary_manifest,
            primary_request=primary_request,
            primary_response=primary_response,
            ranked_manifests=[primary_manifest, verifier_manifest],
            autopilot_plan={
                "verifier_required": True,
                "verifier_provider_id": verifier_manifest.provider_id,
                "verifier_model": verifier_manifest.model_name,
            },
            task=SimpleNamespace(task_id="task-verifier-lineage"),
            classification={"task_class": "debugging"},
            task_kind="action_plan",
            output_mode="plain_text",
            source_context={"surface": "openclaw"},
            failed_provider_ids=set(),
        )

    outbound = post_mock.call_args.kwargs["json"]
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT manifest_id
            FROM provider_invocation_manifests
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    manifest = load_provider_manifest(str(row["manifest_id"]))
    assert status == "independent_completed"
    assert manifest is not None
    assert manifest["payload_hash"] == payload_hash(outbound)
    assert manifest["chat_id"] == "chat-verifier-lineage"
    assert manifest["project_id"] == "project-verifier-lineage"
    assert manifest["capsule_version"] == "capsule-verifier-lineage"
    assert manifest["history_used"] is True
    assert manifest["project_context_used"] is True
    assert manifest["semantic_retrieval_used"] is True
    selected = {
        item["source_type"]: item
        for item in manifest["selected_sources"]
    }
    assert selected["recent_dialogue"]["source_id"] == (
        "chat-turn-current"
    )
    assert selected["semantic_memory"]["source_id"] == "memory-current"
    derived = selected["derived_provider_response"]
    assert derived["source_id"] == "response-primary-lineage"
    assert derived["content_hash"] == hashlib.sha256(
        primary_text.encode("utf-8")
    ).hexdigest()
    assert derived["reason"] == "verifier_primary_response"
    assert derived["source_class"] == "provider_response"
    assert derived["source"] == "primary_provider"
    assert derived["provenance_kind"] == "derived_model_output"
    assert primary_text in json.dumps(outbound)
    assert primary_text not in json.dumps(manifest)
    assert len(primary_request.context["items_included"]) == 3


def test_stream_is_sealed_before_iterable_is_returned(provider_home) -> None:
    adapter = _adapter()
    request = _request("stream safely")
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        '{"message":{"content":"ok"},"done":false}',
        '{"message":{"content":""},"done":true,"eval_count":1}',
    ]

    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        return_value=response,
    ) as post_mock:
        chunks = adapter.stream_text_task(request)
        manifest_id = request.metadata.get("provider_manifest_id")
        assert manifest_id
        assert post_mock.call_count == 1
        manifest = load_provider_manifest(str(manifest_id))
        assert manifest is not None
        assert len(manifest["provider_messages"]) == 2
        assert all("content" not in item for item in manifest["provider_messages"])
        assert [chunk.delta_text for chunk in chunks] == ["ok", ""]

    response.close.assert_called_once()
