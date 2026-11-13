"""The authorship lane's identity read no longer storms the engine per turn.

Measured on the model-free baseline: a plain turn paid ~10 identity triples (version + tags +
show per ranked candidate, per turn) -- roughly half its own wall time on an instant engine.
The read is now reused per (endpoint, model, manifest identity metadata) for five minutes; a
fresh read still happens after the window, when the endpoint or model changes, or when the
manifest row is rewritten with other identity metadata.
"""
from __future__ import annotations

import adapters.openai_compatible_adapter as adapter_module
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from tests.test_local_model_door_and_probe_measurement import _ollama_manifest


def _cached_adapter_calls(monkeypatch, *, endpoint: str, model: str) -> list[int]:
    calls: list[int] = []

    def _fake_read(self) -> dict[str, str]:
        calls.append(1)
        return {"backend_version": "0.12.0", "model_digest": "x", "template_hash": "y",
                "quantization": "q8_0"}

    monkeypatch.setattr(OpenAICompatibleAdapter, "_read_tool_certification_runtime_identity", _fake_read)
    manifest = _ollama_manifest(model, base_url=endpoint)
    adapter = OpenAICompatibleAdapter(manifest)
    for _ in range(5):
        adapter.tool_certification_runtime_identity()
    return calls


def test_identity_is_read_once_per_endpoint_and_model(monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "_certificate_identity_cache", {})
    calls = _cached_adapter_calls(monkeypatch, endpoint="http://127.0.0.1:11434", model="qwen3:8b")
    assert len(calls) == 1, "five status checks on one lane must reuse one identity read"


def test_a_different_endpoint_or_model_reads_again(monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "_certificate_identity_cache", {})
    first = _cached_adapter_calls(monkeypatch, endpoint="http://127.0.0.1:11434", model="qwen3:8b")
    second = _cached_adapter_calls(monkeypatch, endpoint="http://127.0.0.1:11435", model="qwen3:8b")
    third = _cached_adapter_calls(monkeypatch, endpoint="http://127.0.0.1:11434", model="qwen3:4b")
    assert len(first) == 1 and len(second) == 1 and len(third) == 1


def test_an_expired_window_reads_again(monkeypatch) -> None:
    monkeypatch.setattr(adapter_module, "_certificate_identity_cache", {})
    calls = _cached_adapter_calls(monkeypatch, endpoint="http://127.0.0.1:11434", model="qwen3:8b")
    assert len(calls) == 1
    # Age the only cache entry past the window.
    assert len(adapter_module._certificate_identity_cache) == 1
    for key in list(adapter_module._certificate_identity_cache):
        adapter_module._certificate_identity_cache[key] = (0.0, {"backend_version": "old"})
    manifest = _ollama_manifest("qwen3:8b", base_url="http://127.0.0.1:11434")
    identity = OpenAICompatibleAdapter(manifest).tool_certification_runtime_identity()
    assert identity["backend_version"] == "0.12.0"
    assert len(calls) == 2


def test_a_rewritten_manifest_row_reads_again(monkeypatch) -> None:
    """A row rewritten in place with other identity metadata is read again, not served the old
    row's cached identity. Found on the served forget proof: with (endpoint, model) as the only
    key, the certified model's certificate stopped matching after a restart and the model was
    passed over. The manifest's identity metadata is part of the key."""
    monkeypatch.setattr(adapter_module, "_certificate_identity_cache", {})
    calls: list[str] = []

    def _read(self) -> dict[str, str]:
        digest = str(self.manifest.metadata.get("model_digest") or "unknown")
        calls.append(digest)
        return {"backend_version": "0.12.0", "model_digest": digest, "template_hash": "y",
                "quantization": "q8_0"}

    monkeypatch.setattr(OpenAICompatibleAdapter, "_read_tool_certification_runtime_identity", _read)
    bare = _ollama_manifest("qwen3:8b", base_url="http://127.0.0.1:11434")
    assert OpenAICompatibleAdapter(bare).tool_certification_runtime_identity()["model_digest"] == "unknown"
    seeded = bare.model_copy(update={"metadata": {**bare.metadata, "model_digest": "sha256:new"}})
    assert OpenAICompatibleAdapter(seeded).tool_certification_runtime_identity()["model_digest"] == "sha256:new"
    assert OpenAICompatibleAdapter(seeded).tool_certification_runtime_identity()["model_digest"] == "sha256:new"
    assert calls == ["unknown", "sha256:new"]
