"""Work 1 of the provider-answer recovery: the selected model's answer budget.

Root cause this pins (capture 2026-09-16, base 4c78bf4): an owner-pinned OpenRouter model that
the catalog declares reasoning-capable — `deepseek/deepseek-v4-flash-0731`,
`z-ai/glm-5.3-flash`, names no marker and the byok manifest carried no `supported_parameters`
— was sent the prompt table's un-reserved chat ceiling (240–520 tokens) as `max_tokens`. The
model spent the whole ceiling reasoning and returned `content: ""` with `finish_reason:
"length"`; the adapter raised `EmptyProviderResponseError` and no answer was published. The
capture itself carries no raw envelope (and the Activity panel dropped the `empty_reply` facts
the server recorded), so the classification rests on the code path plus the policy modules' own
recorded measurements of the identical failure shape.

The repair:
  * ONE authority (`core.output_budget_policy.manifest_declares_reasoning`) reads the manifest
    declaration, the cached OpenRouter catalog row on the byok lane, and the legacy markers;
  * the byok manifest registration stamps the catalog's `supported_parameters`, context length
    and completion cap into the persisted manifest;
  * the adapter resolves a thinking model's chat ceiling through the lane policy
    (`lane_resolved_output_tokens`), matching what `core.cloud_broker.lane_output_budget` already
    did for the AUTO path;
  * the paid reservation sizes the completion tokens it holds from the SAME function, so the
    funds cover the ceiling actually sent.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.normalized_provider_result import EmptyProviderResponseError
from core.openrouter_catalog import OpenRouterModel
from core.output_budget_policy import (
    PAID_CLOUD,
    lane_resolved_output_tokens,
    manifest_declares_reasoning,
)

_MARKERS = ("qwen3", "nemotron", "deepseek-r", "qwq", "reason")


def _catalog_row(model_id: str, *, supported_parameters=("reasoning", "tools")) -> OpenRouterModel:
    return OpenRouterModel(
        model_id=model_id,
        name=model_id,
        context_length=131072,
        prompt_usd_per_token=1.0e-7,
        completion_usd_per_token=4.0e-7,
        request_usd=None,
        supported_parameters=tuple(supported_parameters),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-09-16T00:00:00+00:00",
        max_output_tokens=16384,
    )


def _byok_manifest(model_name: str, *, metadata_overrides=None):
    metadata = {
        "runtime_family": "openai-compatible",
        "cost_class": "paid_cloud",
        "context_window": 128000,
    }
    metadata.update(metadata_overrides or {})
    return SimpleNamespace(
        provider_name="openrouter-byok",
        provider_id=f"openrouter-byok:{model_name}",
        model_name=model_name,
        adapter_type="openai_compatible",
        metadata=metadata,
        runtime_config={"base_url": "https://openrouter.ai/api", "api_path": "/v1/chat/completions"},
    )


def _request(max_output_tokens: int, *, tools=None, output_mode: str = "plain_text") -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt="answer this",
        output_mode=output_mode,
        messages=[{"role": "user", "content": "answer this"}],
        max_output_tokens=max_output_tokens,
        tools=tools,
    )


class _PayloadHandler(http.server.BaseHTTPRequestHandler):
    bodies: list[bytes] = []
    response_body: bytes = b"{}"
    status: int = 200

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _PayloadHandler.bodies.append(self.rfile.read(length))
        body = _PayloadHandler.response_body
        self.send_response(_PayloadHandler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def payload_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PayloadHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=5)
    _PayloadHandler.bodies = []
    _PayloadHandler.response_body = b"{}"
    _PayloadHandler.status = 200


def _live_adapter(manifest, port: int) -> OpenAICompatibleAdapter:
    runtime_config = dict(manifest.runtime_config)
    runtime_config["base_url"] = f"http://127.0.0.1:{port}"
    runtime_config["timeout_seconds"] = 5.0
    import dataclasses

    if dataclasses.is_dataclass(manifest):
        manifest = dataclasses.replace(manifest, runtime_config=runtime_config)
    else:
        manifest = SimpleNamespace(**{**manifest.__dict__, "runtime_config": runtime_config})
    return OpenAICompatibleAdapter(manifest)


# --- the counterexample: the wire ceiling on the byok lane -----------------------------------


def test_byok_reasoning_model_sends_the_resolved_ceiling_not_the_prompt_table_number(monkeypatch, payload_server) -> None:
    """A catalog-declared reasoning model with NO name marker leaves the 240-token table behind."""
    manifest = _byok_manifest("deepseek/deepseek-v4-flash-0731")
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id) if model_id == manifest.model_name else None,
    )
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "A: … B: …"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    adapter = _live_adapter(manifest, payload_server)
    response = adapter.run_text_task(_request(240))
    sent = json.loads(_PayloadHandler.bodies[-1])
    # 240 asked -> paid-cloud target 760 -> +2048 reasoning reserve. The base failure sent 240.
    assert sent["max_tokens"] == 760 + 2048
    assert response.output_text.startswith("A:")


def test_byok_manifest_declaration_alone_widens_the_ceiling(monkeypatch, payload_server) -> None:
    """The manifest's own stamp (no catalog in play) is enough — persisted lanes keep the fix."""
    manifest = _byok_manifest(
        "z-ai/glm-5.3-flash",
        metadata_overrides={"supported_parameters": ["reasoning", "tools"]},
    )
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "review"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    adapter = _live_adapter(manifest, payload_server)
    adapter.run_text_task(_request(520))
    sent = json.loads(_PayloadHandler.bodies[-1])
    assert sent["max_tokens"] == 760 + 2048


def test_verified_free_byok_reasoning_model_gets_the_free_cloud_target(monkeypatch, payload_server) -> None:
    manifest = _byok_manifest("z-ai/glm-5.3-flash:free")
    free_row = _catalog_row("z-ai/glm-5.3-flash:free", supported_parameters=("reasoning",))
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: free_row)
    # `is_verified_free_cloud_manifest` reads the free verdict through `safe_all_models`, not the
    # row cache, so both catalog surfaces are pointed at the same free row.
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda allow_network=True: ((free_row,), 0.0))
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    adapter = _live_adapter(manifest, payload_server)
    adapter.run_text_task(_request(240))
    sent = json.loads(_PayloadHandler.bodies[-1])
    # The verified-free lane matches what the broker's AUTO path already sends: 1800 + 2048.
    assert sent["max_tokens"] == 1800 + 2048


def test_non_reasoning_byok_model_keeps_the_callers_ceiling(monkeypatch, payload_server) -> None:
    manifest = _byok_manifest("openai/gpt-4.1-mini")
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id, supported_parameters=("tools",)),
    )
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    adapter = _live_adapter(manifest, payload_server)
    adapter.run_text_task(_request(240))
    sent = json.loads(_PayloadHandler.bodies[-1])
    assert sent["max_tokens"] == 240


def test_unclassified_lane_keeps_the_legacy_reserve_arithmetic(monkeypatch, payload_server) -> None:
    """A marker-named model on a lane with no cost class still gets base + 2048, as before."""
    runtime_config = {"base_url": f"http://127.0.0.1:{payload_server}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0}
    manifest = SimpleNamespace(
        provider_name="fixture-cloud",
        provider_id="fixture-cloud:qwen3-8b",
        model_name="qwen3-8b",
        adapter_type="openai_compatible",
        metadata={"runtime_family": "openai-compatible"},
        runtime_config=runtime_config,
    )
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    adapter = _live_adapter(manifest, payload_server)
    adapter.run_text_task(_request(300))
    sent = json.loads(_PayloadHandler.bodies[-1])
    assert sent["max_tokens"] == 300 + 2048


# --- the one reasoning authority ---------------------------------------------------------------


def test_manifest_declares_reasoning_sources_in_order() -> None:
    # Catalog declaration on the byok lane, for a name no marker matches.
    row = _catalog_row("deepseek/deepseek-v4-flash-0731")
    assert "reasoning" in row.supported_parameters
    assert not any(marker in row.model_id for marker in _MARKERS)
    # Manifest declaration wins without any catalog.
    declared = _byok_manifest("some/vendor-model", metadata_overrides={"supported_parameters": ["include_reasoning"]})
    assert manifest_declares_reasoning(declared) is True
    # No-think opt-out still opts out even with a declaration.
    nothink = _byok_manifest("vendor/model-nothink", metadata_overrides={"supported_parameters": ["reasoning"]})
    assert manifest_declares_reasoning(nothink) is False
    # Legacy markers still cover local tags no catalog describes.
    assert manifest_declares_reasoning(_byok_manifest("qwen3:8b")) is True
    # Nothing declared, no markers: not reasoning.
    assert manifest_declares_reasoning(_byok_manifest("openai/gpt-4.1-mini")) is False


def test_manifest_declares_reasoning_reads_the_cached_catalog_on_the_byok_lane(monkeypatch) -> None:
    manifest = _byok_manifest("z-ai/glm-5.3-flash")
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: _catalog_row(model_id))
    assert manifest_declares_reasoning(manifest) is True
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id, supported_parameters=("tools",)),
    )
    assert manifest_declares_reasoning(manifest) is False
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    assert manifest_declares_reasoning(manifest) is False


# --- registration stamps the catalog facts ----------------------------------------------------


class _RecordingRegistry:
    def __init__(self):
        self.manifests: dict[tuple[str, str], object] = {}

    def get_manifest(self, provider, model):
        return self.manifests.get((provider, model))

    def register_manifest(self, manifest):
        self.manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest


def _pin_runtime_home(monkeypatch, tmp_path) -> None:
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", tmp_path, raising=False)


def test_registration_stamps_catalog_facts_into_the_byok_manifest(monkeypatch, tmp_path) -> None:
    from core.runtime_provider_defaults import _register_openrouter_manifest

    _pin_runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id) if model_id == "z-ai/glm-5.3-flash" else None,
    )
    registry = _RecordingRegistry()
    _register_openrouter_manifest(
        registry,
        model_name="z-ai/glm-5.3-flash",
        env={"OPENROUTER_API_KEY": "sk-or-test"},
        api_key_env="OPENROUTER_API_KEY",
        capabilities=["summarize"],
    )
    manifest = registry.get_manifest("openrouter-byok", "z-ai/glm-5.3-flash")
    assert list(manifest.metadata["supported_parameters"]) == ["reasoning", "tools"]
    assert manifest.metadata["context_window"] == 131072
    assert manifest.metadata["max_output_tokens"] == 16384


def test_registration_restamps_a_persisted_manifest_that_predates_the_stamp(monkeypatch, tmp_path) -> None:
    """A lane registered before this repair carries no declaration; registration refreshes it."""
    from core.runtime_provider_defaults import _register_openrouter_manifest

    _pin_runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id),
    )
    registry = _RecordingRegistry()
    stale = _byok_manifest("deepseek/deepseek-v4-flash-0731")
    stale.enabled = True
    stale.capabilities = ["summarize"]
    stale.runtime_config["supports_json_schema"] = True
    stale.runtime_config["base_url"] = "https://openrouter.ai/api"
    registry.manifests[("openrouter-byok", "deepseek/deepseek-v4-flash-0731")] = stale
    _register_openrouter_manifest(
        registry,
        model_name="deepseek/deepseek-v4-flash-0731",
        env={},
        api_key_env="",
        capabilities=["summarize"],
    )
    refreshed = registry.get_manifest("openrouter-byok", "deepseek/deepseek-v4-flash-0731")
    assert refreshed is not stale
    assert "reasoning" in list(refreshed.metadata["supported_parameters"])


def test_registration_without_catalog_knowledge_changes_nothing(monkeypatch, tmp_path) -> None:
    from core.runtime_provider_defaults import _register_openrouter_manifest

    _pin_runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    registry = _RecordingRegistry()
    _register_openrouter_manifest(
        registry,
        model_name="openai/gpt-4.1-mini",
        env={"OPENROUTER_API_KEY": "sk-or-test"},
        api_key_env="OPENROUTER_API_KEY",
        capabilities=["summarize"],
    )
    manifest = registry.get_manifest("openrouter-byok", "openai/gpt-4.1-mini")
    assert "supported_parameters" not in manifest.metadata
    assert manifest.metadata["context_window"] == 128000


# --- the money seam: the reservation holds the same ceiling the wire carries --------------------


def test_lane_resolution_is_one_number_for_the_wire_and_the_reservation(monkeypatch) -> None:
    manifest = _byok_manifest("z-ai/glm-5.3-flash")
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id),
    )
    resolved = lane_resolved_output_tokens(manifest, base_tokens=520)
    assert resolved == 760 + 2048
    from core.output_budget_policy import manifest_lane_capability

    capability = manifest_lane_capability(manifest)
    assert capability.cost_class == PAID_CLOUD
    assert capability.thinking_capable is True


def test_reservation_sizes_completion_tokens_from_the_lane_resolution(monkeypatch, tmp_path) -> None:
    """`reserve_owner_pick_paid_call` must project the RESOLVED ceiling, not the prompt table's."""
    from core import paid_call_reservation

    manifest = _byok_manifest("z-ai/glm-5.3-flash")
    monkeypatch.setattr(
        "core.openrouter_catalog.cached_catalog_row",
        lambda model_id: _catalog_row(model_id),
    )
    captured: dict[str, object] = {}

    def _fake_authorize(*, context, task_id, model_id, local_model_id):
        captured["completion_tokens"] = context.get("paid_call_completion_tokens")
        return None

    monkeypatch.setattr(paid_call_reservation, "_authorize", _fake_authorize)
    task = SimpleNamespace(
        task_id="task-1",
        prompt_tokens=1800,
        max_output_tokens=520,
        request=None,
    )
    paid_call_reservation.reserve_owner_pick_paid_call(
        manifest=manifest,
        task=task,
        source_context={"turn_id": "turn-1"},
        task_kind="chat",
    )
    assert captured["completion_tokens"] == 760 + 2048


# --- the envelope matrix: what extraction does with every realistic reply shape -----------------


def _envelope_adapter(monkeypatch, payload_server, *, model_name="z-ai/glm-5.3-flash", declared=True) -> OpenAICompatibleAdapter:
    overrides = {"supported_parameters": ["reasoning", "tools"]} if declared else {}
    manifest = _byok_manifest(model_name, metadata_overrides=overrides)
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    return _live_adapter(manifest, payload_server)


def test_valid_plain_text_is_returned(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"id": "resp-1", "model": "z-ai/glm-5.3-flash", "choices": [{"message": {"content": "A: The design must…"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 900, "completion_tokens": 210, "total_tokens": 1110}}
    ).encode("utf-8")
    response = _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))
    assert response.output_text == "A: The design must…"
    assert response.finish_reason == "stop"
    assert response.usage["completion_tokens"] == 210
    assert response.provider_attested_model == "z-ai/glm-5.3-flash"


def test_content_block_list_is_joined(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": [{"type": "text", "text": "part one."}, {"type": "text", "text": "part two."}]}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    response = _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))
    assert response.output_text == "part one.\npart two."


def test_length_limited_reasoning_only_reply_carries_its_facts(monkeypatch, payload_server) -> None:
    """The counterexample shape: empty content, finish length, all completion tokens reasoning."""
    _PayloadHandler.response_body = json.dumps(
        {"id": "resp-2", "model": "deepseek/deepseek-v4-flash-0731",
         "choices": [{"message": {"content": "", "reasoning": "<internal chain>"}, "finish_reason": "length"}],
         "usage": {"prompt_tokens": 1450, "completion_tokens": 520, "total_tokens": 1970,
                   "completion_tokens_details": {"reasoning_tokens": 520}}}
    ).encode("utf-8")
    adapter = _envelope_adapter(monkeypatch, payload_server)
    with pytest.raises(EmptyProviderResponseError) as raised:
        adapter.run_text_task(_request(520))
    facts = getattr(raised.value, "diagnostics", None)
    assert isinstance(facts, dict)
    assert facts["finish_reason"] == "length"
    assert facts["completion_tokens"] == 520
    assert facts["reasoning_tokens"] == 520
    assert facts["max_tokens_sent"] == 760 + 2048
    assert facts["content_present"] is False
    assert facts["reasoning_present"] is True
    from core.normalized_provider_result import classify_empty_reply

    assert classify_empty_reply(facts) == "output_budget_exhausted"
    # Structural facts only: no field carries the provider's reasoning text.
    assert "<internal chain>" not in json.dumps(facts)


def test_null_content_with_stop_and_reasoning_classifies_reasoning_only(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": None, "reasoning": "chain of thought"}, "finish_reason": "stop"}],
         "usage": {"completion_tokens": 80, "completion_tokens_details": {"reasoning_tokens": 80}}}
    ).encode("utf-8")
    with pytest.raises(EmptyProviderResponseError) as raised:
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))
    from core.normalized_provider_result import classify_empty_reply

    assert classify_empty_reply(raised.value.diagnostics) == "reasoning_only_no_answer"


def test_explicit_refusal_field_is_distinct_from_empty(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "", "refusal": "I cannot answer that."}, "finish_reason": "stop"}],
         "usage": {"completion_tokens": 12}}
    ).encode("utf-8")
    with pytest.raises(EmptyProviderResponseError) as raised:
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))
    assert raised.value.diagnostics["refusal_present"] is True
    assert raised.value.diagnostics["reasoning_present"] is False


def test_upstream_empty_stop_with_zero_tokens_classifies_upstream_empty(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
         "usage": {"completion_tokens": 0, "total_tokens": 640}}
    ).encode("utf-8")
    with pytest.raises(EmptyProviderResponseError) as raised:
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))
    from core.normalized_provider_result import classify_empty_reply

    assert classify_empty_reply(raised.value.diagnostics) == "upstream_empty"


def test_content_equal_to_reasoning_reads_as_empty(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": "unfinished reasoning tail", "reasoning": "unfinished reasoning tail"}, "finish_reason": "stop"}]}
    ).encode("utf-8")
    with pytest.raises(EmptyProviderResponseError):
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))


def test_error_body_with_200_names_the_provider_error(monkeypatch, payload_server) -> None:
    _PayloadHandler.response_body = json.dumps(
        {"error": {"message": "Upstream error from z-ai: internal", "code": 502}}
    ).encode("utf-8")
    with pytest.raises(EmptyProviderResponseError, match="did not include choices"):
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))


def test_malformed_content_type_is_malformed_not_empty(monkeypatch, payload_server) -> None:
    from core.normalized_provider_result import MalformedProviderResponseError

    _PayloadHandler.response_body = json.dumps(
        {"choices": [{"message": {"content": 17}}]}
    ).encode("utf-8")
    with pytest.raises(MalformedProviderResponseError, match="did not include textual content"):
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))


def test_http_429_body_surfaces_the_provider_cause(monkeypatch, payload_server) -> None:
    from core import provider_http as requests_module

    _PayloadHandler.status = 429
    _PayloadHandler.response_body = json.dumps(
        {"error": {"message": "Rate limit exceeded for this key", "code": 429}}
    ).encode("utf-8")
    with pytest.raises(requests_module.HTTPError, match="provider said"):
        _envelope_adapter(monkeypatch, payload_server).run_text_task(_request(520))


def test_timeout_is_typed_as_provider_timeout(monkeypatch, payload_server) -> None:
    from core import provider_http as requests_module
    from core.normalized_provider_result import classify_error_class

    class _DeadlineRequest(ModelRequest):
        pass

    _PayloadHandler.response_body = b"{}"
    adapter = _envelope_adapter(monkeypatch, payload_server)

    def _explode(*args, **kwargs):
        raise requests_module.exceptions.Timeout("read timed out")

    monkeypatch.setattr(requests_module, "post", _explode)
    with pytest.raises(requests_module.exceptions.Timeout):
        adapter.run_text_task(_request(520))
    assert classify_error_class(requests_module.exceptions.Timeout("read timed out")) is not None
