"""What the receiving model actually gets, at the wire, for every provider dialect.

The attachment authority decides; the adapters translate. An OpenAI-compatible endpoint receives
content parts on the current user message; a native Ollama endpoint receives the flattened text
plus its `images` field. A model that cannot read images gets a truthful note and the runtime
records the omission -- it never ships bytes the provider would silently drop, and it never lies
that the image was seen. A text-only turn produces byte-identical payloads to the base build.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_paths
from storage.model_provider_manifest import ModelProviderManifest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


IMAGE_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40).decode()
TEXT_ATTACHMENT = {
    "kind": "text",
    "attachment_id": "att_" + "1" * 32,
    "name": "notes.txt",
    "media_type": "text/plain",
    "text": "alpha beta gamma",
    "size_bytes": 16,
    "truncated": False,
}
IMAGE_ATTACHMENT = {
    "kind": "image",
    "attachment_id": "att_" + "2" * 32,
    "name": "shot.png",
    "media_type": "image/png",
    "data_url": "data:image/png;base64," + IMAGE_B64,
    "size_bytes": 48,
}
MESSAGES = [
    {"role": "system", "content": "You are VOOL."},
    {"role": "user", "content": "earlier question"},
    {"role": "assistant", "content": "earlier answer"},
    {"role": "user", "content": "what do the attachments say?"},
]


def _manifest(*, provider: str, model: str, base_url: str, capabilities: list[str], metadata: dict[str, Any] | None = None, family: str = "openai-compatible") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        weight_location="external",
        redistribution_allowed=True,
        runtime_dependency="openai-compatible",
        capabilities=capabilities,
        runtime_config={"base_url": base_url, "api_path": "/v1/chat/completions"},
        metadata={"runtime_family": family, **(metadata or {})},
        enabled=True,
    )


def _request(attachments: list[dict[str, Any]]) -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt="what do the attachments say?",
        system_prompt="You are VOOL.",
        messages=[dict(m) for m in MESSAGES],
        attachments=list(attachments),
        max_output_tokens=64,
        metadata={},
    )


def test_openai_compatible_payload_carries_text_as_data_and_images_only_to_capable_models() -> None:
    capable = OpenAICompatibleAdapter(_manifest(provider="vision-cloud", model="see-1", base_url="https://api.example.test", capabilities=["multimodal"]))
    payload = capable._build_openai_payload(_request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT]), force_json=False, stream=False)
    messages = payload["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "earlier question"  # history untouched
    parts = messages[-1]["content"]
    assert isinstance(parts, list) and parts[0] == {"type": "text", "text": "what do the attachments say?"}
    assert any(p.get("type") == "image_url" and p["image_url"]["url"] == IMAGE_ATTACHMENT["data_url"] for p in parts)
    assert any(p.get("type") == "text" and "notes.txt" in p["text"] and "alpha beta gamma" in p["text"] for p in parts)
    request = _request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT])
    capable._build_openai_payload(request, force_json=False, stream=False)
    delivery = request.metadata["attachment_delivery"]
    assert {(d["attachment_id"], d["outcome"]) for d in delivery} == {(TEXT_ATTACHMENT["attachment_id"], "read"), (IMAGE_ATTACHMENT["attachment_id"], "sent")}

    # A model the catalog says reads text only: the image is withheld and the note says it cannot.
    blind = OpenAICompatibleAdapter(
        _manifest(provider="text-cloud", model="say-1", base_url="https://api.example.test", capabilities=["summarize"], metadata={"input_modalities": ["text"]})
    )
    request = _request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT])
    payload = blind._build_openai_payload(request, force_json=False, stream=False)
    parts = payload["messages"][-1]["content"]
    assert not any(p.get("type") == "image_url" for p in parts)
    assert IMAGE_B64 not in str(payload)
    assert any("shot.png" in p.get("text", "") and "cannot" in p.get("text", "").lower() for p in parts)
    delivery = {d["attachment_id"]: d for d in request.metadata["attachment_delivery"]}
    assert delivery[IMAGE_ATTACHMENT["attachment_id"]]["outcome"] == "omitted"
    assert delivery[IMAGE_ATTACHMENT["attachment_id"]]["reason"] == "model_has_no_image_input"
    assert delivery[TEXT_ATTACHMENT["attachment_id"]]["outcome"] == "read"
    # A model nobody has stated anything about is NOT rounded up to "reads images".
    unknown = OpenAICompatibleAdapter(_manifest(provider="mystery-cloud", model="who-1", base_url="https://api.example.test", capabilities=["summarize"]))
    request = _request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT])
    payload = unknown._build_openai_payload(request, force_json=False, stream=False)
    parts = payload["messages"][-1]["content"]
    assert not any(p.get("type") == "image_url" for p in parts)
    assert any("shot.png" in p.get("text", "") and "unknown" in p.get("text", "").lower() for p in parts)
    delivery = {d["attachment_id"]: d for d in request.metadata["attachment_delivery"]}
    assert delivery[IMAGE_ATTACHMENT["attachment_id"]]["reason"] == "model_image_support_unknown"


def test_openrouter_capability_comes_from_the_live_catalog_not_from_the_generic_byok_manifest(monkeypatch) -> None:
    from core import openrouter_catalog

    class _Row:
        def __init__(self, model_id: str, modalities: tuple[str, ...]) -> None:
            self.model_id = model_id
            self.input_modalities = modalities

    monkeypatch.setattr(
        openrouter_catalog,
        "safe_all_models",
        lambda allow_network=False: ((_Row("vendor/sees:free", ("text", "image")), _Row("vendor/blind:free", ("text",))), 0),
    )
    from core import chat_attachments

    sees = _manifest(provider="openrouter-byok", model="vendor/sees:free", base_url="https://openrouter.ai/api/v1", capabilities=["summarize"])
    blind = _manifest(provider="openrouter-byok", model="vendor/blind:free", base_url="https://openrouter.ai/api/v1", capabilities=["summarize"])
    unknown = _manifest(provider="openrouter-byok", model="vendor/unlisted", base_url="https://openrouter.ai/api/v1", capabilities=["summarize"])
    assert chat_attachments.manifest_supports_images(sees) is True
    assert chat_attachments.manifest_supports_images(blind) is False
    assert chat_attachments.manifest_supports_images(unknown) is None
    local = _manifest(provider="ollama-local", model="qwen2.5:7b", base_url="http://127.0.0.1:11434", capabilities=["summarize"], family="ollama")
    assert chat_attachments.manifest_supports_images(local) is None
    local_vision = _manifest(provider="ollama-local", model="llava:7b", base_url="http://127.0.0.1:11434", capabilities=["multimodal"], family="ollama")
    assert chat_attachments.manifest_supports_images(local_vision) is True


def test_native_ollama_payload_flattens_parts_and_moves_images_to_the_native_field() -> None:
    local_vision = OpenAICompatibleAdapter(
        _manifest(provider="ollama-local", model="llava:7b", base_url="http://127.0.0.1:11434", capabilities=["multimodal"], family="ollama")
    )
    request = _request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT])
    payload = local_vision._build_ollama_payload(request, force_json=False, stream=False)
    last = payload["messages"][-1]
    assert isinstance(last["content"], str)
    assert last["content"].startswith("what do the attachments say?")
    assert "notes.txt" in last["content"] and "alpha beta gamma" in last["content"]
    assert last["images"] == [IMAGE_B64]
    assert all(isinstance(m["content"], str) and "images" not in m for m in payload["messages"][:-1])

    local_blind = OpenAICompatibleAdapter(
        _manifest(provider="ollama-local", model="qwen2.5:7b", base_url="http://127.0.0.1:11434", capabilities=["summarize"], family="ollama")
    )
    request = _request([TEXT_ATTACHMENT, IMAGE_ATTACHMENT])
    payload = local_blind._build_ollama_payload(request, force_json=False, stream=False)
    last = payload["messages"][-1]
    assert "images" not in last and IMAGE_B64 not in str(payload)
    assert "shot.png" in last["content"]
    delivery = {d["attachment_id"]: d for d in request.metadata["attachment_delivery"]}
    assert delivery[IMAGE_ATTACHMENT["attachment_id"]]["outcome"] == "omitted"
    assert delivery[IMAGE_ATTACHMENT["attachment_id"]]["reason"] == "model_image_support_unknown"


def test_a_text_only_request_produces_the_base_builds_payload_exactly() -> None:
    adapter = OpenAICompatibleAdapter(_manifest(provider="text-cloud", model="say-1", base_url="https://api.example.test", capabilities=["summarize"]))
    request = _request([])
    payload = adapter._build_openai_payload(request, force_json=False, stream=False)
    assert payload["messages"] == MESSAGES
    assert "attachment_delivery" not in request.metadata
    local = OpenAICompatibleAdapter(_manifest(provider="ollama-local", model="qwen2.5:7b", base_url="http://127.0.0.1:11434", capabilities=["summarize"], family="ollama"))
    payload = local._build_ollama_payload(_request([]), force_json=False, stream=False)
    assert payload["messages"] == MESSAGES


def test_prompt_budget_prices_an_image_part_as_an_image_not_as_a_megabyte_of_text() -> None:
    from core.prompt_budget import estimate_message_tokens

    big = "data:image/png;base64," + ("A" * 400_000)
    with_image = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": big}}]}]
    as_text = [{"role": "user", "content": "look\n" + big}]
    image_tokens = estimate_message_tokens(with_image)
    assert image_tokens < estimate_message_tokens(as_text) / 20
    assert image_tokens > estimate_message_tokens([{"role": "user", "content": "look"}])


def test_the_router_hands_the_turns_attachments_to_the_request_and_records_delivery(monkeypatch) -> None:
    from core import chat_attachments
    from tests.test_chat_attachments_authority import tiny_png

    session = "openclaw:eeeeeeeeeeeeeeeeeeee"
    text = chat_attachments.stage_attachment(session_id=session, declared_name="notes.txt", declared_type="text/plain", data=b"alpha beta gamma")
    image = chat_attachments.stage_attachment(session_id=session, declared_name="shot.png", declared_type="image/png", data=tiny_png())
    chat_attachments.bind_to_turn(session_id=session, turn_id="turn-9", attachment_ids=[text["id"], image["id"]])
    source_context = {"runtime_session_id": session, "attachment_turn_id": "turn-9", "cancel_turn_id": "turn-9"}
    entries = chat_attachments.model_attachments_from_source_context(source_context)
    assert [e["kind"] for e in entries] == ["text", "image"]
    # A context that names no attachment turn yields nothing and reads nothing.
    assert chat_attachments.model_attachments_from_source_context({"runtime_session_id": session}) == []
    # A forged context naming a turn that does not own these attachments yields nothing.
    assert chat_attachments.model_attachments_from_source_context({"runtime_session_id": "openclaw:ffffffffffffffffffff", "attachment_turn_id": "turn-9"}) == []
    # The router's request builder consults exactly this seam.
    from core import memory_first_router

    seen: list[dict[str, Any]] = []

    def _spy(context, **_kwargs):
        seen.append(dict(context or {}))
        return entries

    monkeypatch.setattr(memory_first_router.chat_attachments, "model_attachments_from_source_context", _spy)
    router = memory_first_router.MemoryFirstRouter()
    from types import SimpleNamespace

    request = router._build_request(
        task=SimpleNamespace(task_id="task-9", task_summary="what do the attachments say?", prompt="what do the attachments say?"),
        classification={"task_class": "chat", "confidence": 0.9},
        interpretation=SimpleNamespace(raw_text="what do the attachments say?", normalized_text="what do the attachments say?", user_text="what do the attachments say?", is_continuation=False),
        context_result=SimpleNamespace(
            assembled_context=lambda **_: "",
            context_snippets=lambda: [],
            swarm_metadata=[],
            report=SimpleNamespace(retrieval_confidence=0.0, to_dict=lambda: {}),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="chat",
        surface="openclaw",
        source_context=source_context,
    )
    assert seen and seen[0]["attachment_turn_id"] == "turn-9"
    assert [a["kind"] for a in request.attachments] == ["text", "image"]


def test_the_external_media_review_never_posts_a_chat_attachment_id_as_an_image_url(monkeypatch) -> None:
    """Measured 2026-09-02: the candidate-only media review sent `image_url: "attachment:att_…"`
    to OpenRouter for every photo turn and the provider answered 400, which ended the turn. A
    chat attachment reaches the model through the canonical carrier, never through this lane."""
    from core.media_analysis_pipeline import MediaAnalysisPipeline

    class _Registry:
        def __init__(self) -> None:
            self.selected = 0

        def select_manifest(self, request):
            self.selected += 1
            raise AssertionError("no provider may be selected for a chat attachment")

        def build_adapter(self, manifest):
            raise AssertionError("no adapter may be built for a chat attachment")

    registry = _Registry()
    pipeline = MediaAnalysisPipeline(registry)
    items = [
        {
            "reference": "attachment:att_" + "a" * 32,
            "media_kind": "image",
            "source_kind": "web",
            "source_domain": "",
            "credibility": {"score": 0.5},
            "social_policy": {"platform": "unknown", "allowed_for_orientation": True},
            "requires_multimodal": True,
            "blocked": False,
            "metadata": {"origin": "chat_attachment", "attachment_id": "att_" + "a" * 32, "name": "photo.png"},
        }
    ]
    result = pipeline.analyze(task_id="task-1", task_summary="describe the photo", evidence_items=items)
    assert result.used_provider is False
    assert result.reason == "chat_attachments_delivered_by_model_lane"
    assert registry.selected == 0
    # And a review that DOES run for genuinely external media never takes the turn down with it:
    # the turn's call site catches the review's failure and records it on the result.
    import inspect

    from core.agent_runtime import turn_reasoning

    call_site = inspect.getsource(turn_reasoning).split("media_analysis = agent.media_pipeline.analyze(", 1)[1].split("media_context_snippets", 1)[0]
    assert "except Exception as exc:" in call_site and "analysis_error" in call_site
