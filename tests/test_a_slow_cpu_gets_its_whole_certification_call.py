"""A machine with no GPU gets the whole certification call it was given (2026-11-25).

Intel smoke on 4e06fdc5c (i7-8700B, qwen3:8b on the CPU): the cold runner took 231 s to load, and the first sealed
probe call ended at exactly 300 s with no exchange, so the model was left "degraded" and could not write answers.
The certifier had scaled the 150 s per-call budget to 600 s for a CPU-only machine, but the adapter's own ceiling
was the GPU-host 300 s and cut it back. The ceiling now scales by the same one rule; a GPU host keeps 300 s.
"""
from __future__ import annotations

from typing import Any

import pytest

from adapters.openai_compatible_adapter import OpenAICompatibleAdapter


class _Sent(Exception):
    pass


def _read_timeout(monkeypatch: pytest.MonkeyPatch, *, cpu_only: bool, asked: float) -> float:
    from tests.test_local_model_door_and_probe_measurement import _ollama_manifest

    adapter = OpenAICompatibleAdapter(_ollama_manifest("qwen3:8b", context_window=24576))
    seen: dict[str, Any] = {}

    class _Http:
        def post(self, endpoint, **kwargs):
            seen.update(kwargs)
            raise _Sent()

    class _Permit:
        def consume(self):
            return {}

    monkeypatch.setattr("adapters.openai_compatible_adapter._strict_tool_certification_loopback", lambda url: True)
    monkeypatch.setattr("adapters.openai_compatible_adapter.seal_direct_provider_invocation", lambda **_: _Permit())
    monkeypatch.setattr("core.local_compute_budget.local_inference_is_cpu_only", lambda: cpu_only)
    monkeypatch.setattr(adapter, "_http", lambda: _Http())
    with pytest.raises(_Sent):
        adapter.tool_certification_exchange(
            messages=[{"role": "user", "content": "add 4 and 5"}], tools=(), tool_choice=None,
            max_output_tokens=64, timeout_seconds=asked,
        )
    return float(seen["timeout"][1])


def test_a_cpu_only_machine_keeps_the_scaled_call_budget(monkeypatch):
    assert _read_timeout(monkeypatch, cpu_only=True, asked=600.0) == 600.0


def test_a_gpu_host_keeps_its_measured_ceiling(monkeypatch):
    assert _read_timeout(monkeypatch, cpu_only=False, asked=600.0) == 300.0
    assert _read_timeout(monkeypatch, cpu_only=False, asked=150.0) == 150.0


def test_the_cpu_ceiling_is_still_a_ceiling(monkeypatch):
    assert _read_timeout(monkeypatch, cpu_only=True, asked=5000.0) == 1200.0
