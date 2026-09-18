"""R-9a/H-9: the BYOK direct lane traverses the canonical egress gate.

A memory-derived LOCAL_ONLY segment stamped onto a message must be dropped at
a remote (cloud) endpoint and kept on a loopback endpoint — the same gate
function the generic cloud provider uses, proving ONE egress authority.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.egress_gate import SEGMENT_META_KEY


def _adapter_for(base_url: str):
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    class _Manifest:
        provider_id = "byok-test"
        model_name = "m"
        runtime_config = {"base_url": base_url, "api_key": "k"}
        metadata: dict = {}
        capabilities: list = []

        def __getattr__(self, name):  # tolerate auxiliary attribute reads
            object.__getattribute__(self, "_defaults").setdefault(name, None)
            return self._defaults[name]

        _defaults: dict = {}

    manifest = _Manifest()
    adapter = OpenAICompatibleAdapter.__new__(OpenAICompatibleAdapter)
    adapter.manifest = manifest
    return adapter


def _request_with_local_only_memory():
    from adapters.base_adapter import ModelRequest

    req = ModelRequest(task_kind="chat", prompt="q", metadata={}, context={})
    req.messages = [
        {"role": "user", "content": "public question"},
        {
            "role": "system",
            "content": "private memory block",
            SEGMENT_META_KEY: {
                "segment_id": "s1",
                "authority_class": "memory",
                "principal_scope": "owner_local",
                "exposure_class": "LOCAL_ONLY",
            },
        },
    ]
    return req


def test_byok_remote_endpoint_drops_local_only_segment():
    adapter = _adapter_for("https://openrouter.ai/api/v1")
    payload = adapter._build_openai_payload(_request_with_local_only_memory(), force_json=False, stream=False)
    contents = [str(m.get("content") or "") for m in payload["messages"]]
    assert "public question" in contents
    assert "private memory block" not in contents  # dropped, not redacted


def test_loopback_endpoint_keeps_local_only_segment():
    adapter = _adapter_for("http://127.0.0.1:11434/v1")
    payload = adapter._build_openai_payload(_request_with_local_only_memory(), force_json=False, stream=False)
    contents = [str(m.get("content") or "") for m in payload["messages"]]
    assert "private memory block" in contents


def test_malformed_segment_stamp_refused_at_cloud():
    """RED MUTATION target: a partial stamp must fail closed at the boundary."""
    from core.egress_gate import EgressRefused

    adapter = _adapter_for("https://openrouter.ai/api/v1")
    req = _request_with_local_only_memory()
    req.messages[1][SEGMENT_META_KEY] = {"segment_id": "partial"}
    with pytest.raises(EgressRefused):
        adapter._build_openai_payload(req, force_json=False, stream=False)
