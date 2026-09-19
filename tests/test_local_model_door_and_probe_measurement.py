"""Regression pins for the served model path this session repaired.

1. The certification probe must measure a thinking model in a mode the runtime actually
   serves (the adapter's own think flag + reasoning budget), not in an unconfigured mode
   whose budget reasoning starves before the first tool call.
2. A recovery-case failure is a result-continuation verdict, never a fresh emission one.
3. The local-model door (Settings → Models): listing, registration, and its guards.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.local_model_tool_certification import run_local_model_tool_certification
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from storage.model_provider_manifest import ModelProviderManifest
from tests.test_local_model_tool_certification import ProbeExchange, _manifest


def _json(response) -> dict:
    return json.loads(bytes(response.body or b"{}").decode("utf-8"))


def _ollama_manifest(model_name: str, **runtime_config: Any) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="local_qwen_provider",
        runtime_config={"base_url": "http://127.0.0.1:11434", **runtime_config},
        metadata={"runtime_family": "ollama"},
    )


class _CapturePost:
    """requests.post double that records the sealed payload and answers an empty 200."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, url: str, json=None, headers=None, timeout=None):
        self.payloads.append({"url": url, "json": json})

        class _Response:
            status_code = 200
            ok = True

            @staticmethod
            def json():
                return {"message": {"role": "assistant", "content": "", "tool_calls": []}}

        return _Response()


def _exchange(monkeypatch, manifest: ModelProviderManifest) -> _CapturePost:
    capture = _CapturePost()
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.seal_direct_provider_invocation",
        lambda **kwargs: type("Permit", (), {"consume": lambda self: kwargs["payload"]})(),
    )
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.requests.post", capture
    )
    OpenAICompatibleAdapter(manifest).tool_certification_exchange(
        messages=[{"role": "user", "content": "probe"}],
        tools=(),
        tool_choice=None,
        max_output_tokens=384,
        timeout_seconds=3,
    )
    return capture


def test_probe_exchange_measures_a_thinking_model_in_its_serving_mode(monkeypatch) -> None:
    """qwen3 measured without the think flag burns the whole budget on reasoning.

    Every other native-ollama payload in the adapter stamps the flag; the certification
    exchange must too, or the probe reports a capable model as emitting nothing.
    """

    capture = _exchange(monkeypatch, _ollama_manifest("qwen3:8b"))
    payload = capture.payloads[0]["json"]
    assert payload["think"] is False
    assert payload["options"]["num_predict"] == 384


def test_probe_exchange_reserves_reasoning_budget_when_think_is_on(monkeypatch) -> None:
    capture = _exchange(monkeypatch, _ollama_manifest("qwen3:8b", think=True))
    payload = capture.payloads[0]["json"]
    assert payload["think"] is True
    assert payload["options"]["num_predict"] == 384 + 2048


def test_probe_exchange_omits_the_think_key_for_non_thinking_models(monkeypatch) -> None:
    """qwen2.5:7b answers HTTP 400 when `think` is present at all -- omitting is the contract."""

    capture = _exchange(monkeypatch, _ollama_manifest("qwen2.5:7b"))
    payload = capture.payloads[0]["json"]
    assert "think" not in payload


class RecoveryRetryAnswersInProse(ProbeExchange):
    """Emission and sealed continuation both pass; the recovery retry writes prose."""

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        tools = tuple(kwargs.get("tools") or ())
        intents = {tool.intent for tool in tools}
        messages = list(kwargs.get("messages") or [])
        if intents == {"probe.echo"} and any(
            message.get("role") == "tool"
            and "synthetic_validation_error" in str(message.get("content") or "")
            for message in messages
        ):
            return {
                "status_code": 200,
                "latency_ms": 1.0,
                "dialect": "ollama",
                "body": {"message": {"role": "assistant", "content": '{"error": "synthetic_validation_error"}'}},
            }
        return super().__call__(**kwargs)


def test_recovery_retry_failure_is_a_result_continuation_verdict(tmp_path: Path) -> None:
    """The recovery retry's no-calls answer used to be filed under model_emission.

    That mislabel reported a model that HAD emitted parallel calls and preserved the sealed
    nonce as failing to emit anything -- the wrong capability, the wrong repair.
    """

    result = run_local_model_tool_certification(
        _manifest(),
        exchange=RecoveryRetryAnswersInProse(),
        backend_version="0.21.2",
        db_path=tmp_path / "recovery.db",
    )
    assert result["state"] == "degraded"
    assert result["stages"]["model_emission"]["state"] == "passed"
    assert result["stages"]["result_continuation"]["state"] == "failed"
    assert result["stages"]["result_continuation"]["failure_code"] == "missing_tool_calls"


def test_local_models_listing_names_registered_and_certified_state(monkeypatch) -> None:
    manifest = _ollama_manifest("qwen3:8b")
    monkeypatch.setattr(
        "core.web.api.runtime.installed_ollama_model_inventory",
        lambda **kwargs: (
            type("Item", (), {"name": "qwen3:8b", "size_bytes": 5_200_000_000})(),
            type("Item", (), {"name": "nomic-embed-text:latest", "size_bytes": 274_000_000})(),
        ),
    )
    monkeypatch.setattr(
        "core.model_registry.ModelRegistry.get_manifest",
        lambda self, provider_name, model_name: manifest
        if model_name == "qwen3:8b"
        else None,
    )
    monkeypatch.setattr(
        "core.local_model_tool_certification.certification_status",
        lambda _m: {"state": "verified"},
    )
    response = dispatch_get(
        path="/api/models/local",
        query={},
        runtime=RuntimeServices(),
        model_name="vool",
        client_host="127.0.0.1",
        headers={},
    )
    assert response.status == 200
    payload = _json(response)
    # The embedding model is not a text lane and must not be offered for registration.
    assert [m["model_name"] for m in payload["models"]] == ["qwen3:8b"]
    entry = payload["models"][0]
    assert entry["registered"] is True
    assert entry["certification_state"] == "verified"


def test_local_model_registration_door_guards_and_registers(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.web.api.runtime.installed_ollama_model_names",
        lambda **kwargs: ["qwen3:8b", "qwen3:4b-instruct"],
    )
    registered: list[str] = []
    manifest = _ollama_manifest("qwen3:8b")

    def fake_register(registry, *, model_tag, env=None):
        registered.append(model_tag)
        return manifest

    monkeypatch.setattr(
        "core.runtime_provider_defaults.register_installed_local_model", fake_register
    )
    base = dict(
        path="/api/models/local/register",
        headers={"Content-Type": "application/json"},
        runtime=RuntimeServices(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )

    remote = dispatch_post(body={"model_name": "qwen3:8b"}, client_host="10.0.0.4", **base)
    assert remote.status == 403

    unknown_field = dispatch_post(
        body={"model_name": "qwen3:8b", "enabled": True}, client_host="127.0.0.1", **base
    )
    assert unknown_field.status == 400

    # Substring matches must not register: `qwen3:4b-instruct` is installed, `qwen3:4b` is not.
    not_installed = dispatch_post(
        body={"model_name": "qwen3:4b"}, client_host="127.0.0.1", **base
    )
    assert not_installed.status == 404

    ok = dispatch_post(body={"model_name": "qwen3:8b"}, client_host="127.0.0.1", **base)
    assert ok.status == 200
    assert registered == ["qwen3:8b"]
    payload = _json(ok)
    assert payload["ok"] is True
    assert payload["provider_id"] == "ollama-local:qwen3:8b"
    assert payload["certification_url"] == "/api/model-tool-certification/run"
