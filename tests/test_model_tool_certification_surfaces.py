from __future__ import annotations

import json

from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from apps.vool_cli import build_parser
from core.local_model_tool_certification import ADD_TOOL
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from storage.model_provider_manifest import ModelProviderManifest


def _manifest(base_url: str = "http://127.0.0.1:11434") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen-test:4b",
        source_type="http",
        adapter_type="openai_compatible",
        runtime_config={"base_url": base_url},
        metadata={"runtime_family": "ollama"},
    )


def _json(response) -> dict:
    return json.loads(bytes(response.body or b"{}").decode("utf-8"))


def test_cli_surface_requires_exact_provider_and_model_and_manual_run_flag() -> None:
    args = build_parser().parse_args(
        [
            "model-tool-certification",
            "--provider-name",
            "ollama-local",
            "--model-name",
            "qwen-test:4b",
            "--run",
            "--json",
        ]
    )
    assert args.provider_name == "ollama-local"
    assert args.model_name == "qwen-test:4b"
    assert args.run is True
    assert args.json is True


def test_get_status_surface_is_observe_only(monkeypatch) -> None:
    manifest = _manifest()
    monkeypatch.setattr("core.model_registry.ModelRegistry.get_manifest", lambda *_: manifest)
    monkeypatch.setattr(
        "core.local_model_tool_certification.certification_status",
        lambda _manifest: {"state": "verified", "observe_only": True, "routing_effect": "none"},
    )
    response = dispatch_get(
        path="/api/model-tool-certification",
        query={"provider_name": ["ollama-local"], "model_name": ["qwen-test:4b"]},
        runtime=RuntimeServices(),
        model_name="vool",
    )
    assert response.status == 200
    assert _json(response) == {"state": "verified", "observe_only": True, "routing_effect": "none"}


def test_run_surface_is_loopback_only_and_explicit(monkeypatch) -> None:
    manifest = _manifest()
    monkeypatch.setattr("core.model_registry.ModelRegistry.get_manifest", lambda *_: manifest)
    calls: list[str] = []
    monkeypatch.setattr(
        "core.local_model_tool_certification.run_local_model_tool_certification",
        lambda _manifest, timeout_seconds: calls.append(_manifest.provider_id)
        or {"state": "verified", "observe_only": True, "routing_effect": "none"},
    )
    body = {"provider_name": "ollama-local", "model_name": "qwen-test:4b"}
    denied = dispatch_post(
        path="/api/model-tool-certification/run",
        body=body,
        headers={},
        runtime=RuntimeServices(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="10.0.0.4",
    )
    allowed = dispatch_post(
        path="/api/model-tool-certification/run",
        body=body,
        headers={},
        runtime=RuntimeServices(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    assert denied.status == 403
    assert allowed.status == 200
    assert calls == ["ollama-local:qwen-test:4b"]


def test_adapter_exchange_refuses_remote_endpoint_before_requests(monkeypatch) -> None:
    adapter = OpenAICompatibleAdapter(_manifest("https://api.openai.com/v1"))
    calls: list[str] = []
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.requests.post",
        lambda *args, **kwargs: calls.append(str(args[0])),
    )
    try:
        adapter.tool_certification_exchange(
            messages=[{"role": "user", "content": "probe"}],
            tools=(),
            tool_choice=None,
            max_output_tokens=1,
            timeout_seconds=1,
        )
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("remote certification exchange was not refused")
    assert calls == []


def test_adapter_exchange_uses_the_real_ollama_tool_dialect_and_sealed_payload(monkeypatch) -> None:
    adapter = OpenAICompatibleAdapter(_manifest())
    captured: dict = {}

    class Permit:
        def __init__(self, payload):
            self.payload = payload

        def consume(self):
            return self.payload

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"message": {"role": "assistant", "tool_calls": []}}

    def fake_seal(**kwargs):
        captured["operation"] = kwargs["operation"]
        return Permit(kwargs["payload"])

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("adapters.openai_compatible_adapter.seal_direct_provider_invocation", fake_seal)
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", fake_post)
    result = adapter.tool_certification_exchange(
        messages=[{"role": "user", "content": "probe"}],
        tools=(ADD_TOOL,),
        tool_choice="required",
        max_output_tokens=200,
        timeout_seconds=3,
    )

    assert result["dialect"] == "ollama"
    assert captured["operation"] == "local_tool_certification"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["options"]["num_predict"] == 200
    assert captured["payload"]["tools"][0]["function"]["name"] == "vool_probe_add"
    assert captured["payload"]["tools"][0]["function"]["strict"] is False
    assert captured["payload"]["tool_choice"] == "required"
