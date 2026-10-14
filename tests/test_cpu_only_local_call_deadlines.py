"""A CPU-only machine stops cutting local model calls at GPU-sized per-call timeouts.

The turn deadlines were scaled for CPU-only hosts in an earlier round (the conductor turn, its
planner phase, the ambiguity probe), but the per-call transport timeout a local provider's HTTP
call actually runs under stayed at the manifest's GPU-measured 180 s (30 s when a manifest carries
none), and the local admission queue waited at a flat 240 s. On the Intel acceptance host (i7-8700B,
qwen3:8b, 3.2 tok/s generation) that cut recall turns mid-generation while the enclosing turn still
had budget -- the reported "local recall can time out; use a cloud model".

These pin the one rule that was missing: on a CPU-only machine a LOCAL provider's per-call timeout
and the queue wait are scaled by ``CPU_ONLY_FACTOR``; a cloud provider's timeout never is; an
explicit ``VOOL_LOCAL_MODEL_QUEUE_WAIT`` wins over scaling; a bound turn deadline still covers the
call; and on a GPU machine every value stays exactly what it was. CPU-only is faked the way
``tests/test_local_compute_budget.py`` fakes it: a patched ``probe_machine`` and a cleared cache.
No model runs anywhere in this file.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import local_compute_budget, runtime_active_clock
from core.hardware_tier import MachineProbe
from core.provider_call_deadline import (
    DEFAULT_CLEANUP_MARGIN_SECONDS,
    bind_provider_deadline,
    copy_deadline_to_request_metadata,
)


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    local_compute_budget.local_inference_is_cpu_only.cache_clear()
    yield
    local_compute_budget.local_inference_is_cpu_only.cache_clear()


def _machine(monkeypatch, accelerator: str) -> None:
    def probe() -> MachineProbe:
        return MachineProbe(cpu_cores=8, ram_gb=16.0, gpu_name=None, vram_gb=None, accelerator=accelerator)

    monkeypatch.setattr("core.hardware_tier.probe_machine", probe)
    local_compute_budget.local_inference_is_cpu_only.cache_clear()


def _local_adapter(*, timeout_seconds: float | None = 180.0) -> OpenAICompatibleAdapter:
    """Mirrors the manifest the runtime registers for Ollama (core/runtime_provider_defaults.py):
    an http source on loopback with deployment_class local, so the routing layer's own locality
    rule classifies it local."""
    runtime_config: dict[str, object] = {"base_url": "http://127.0.0.1:11434/v1"}
    if timeout_seconds is not None:
        runtime_config["timeout_seconds"] = timeout_seconds
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            source_type="http",
            metadata={"runtime_family": "ollama", "deployment_class": "local"},
            runtime_config=runtime_config,
        )
    )


def _cloud_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="openrouter-byok:anthropic/claude",
            model_name="anthropic/claude",
            source_type="http",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1",
                "api_path": "/chat/completions",
                "timeout_seconds": 180.0,
            },
        )
    )


def _request(prompt: str = "hello", metadata: dict | None = None) -> ModelRequest:
    request = ModelRequest(
        task_kind="chat",
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    if metadata is not None:
        request.metadata = dict(metadata)
    return request


def _post_read_timeout(adapter: OpenAICompatibleAdapter, request: ModelRequest) -> float:
    """The read timeout the adapter's real HTTP call carries, captured the way the existing
    adapter tests capture it (tests/test_openai_compatible_adapter.py::_post_kwargs)."""
    response = mock.Mock()
    response.raise_for_status.return_value = None
    # One payload that satisfies both the Ollama shape and the OpenAI-compatible shape, so the
    # helper drives whichever invoke path each adapter's manifest selects.
    response.json.return_value = {
        "message": {"content": "ok"},
        "choices": [{"message": {"content": "ok"}}],
    }
    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=response
    ) as post, mock.patch("core.local_inference_evidence.record_ollama_generate_benchmark"):
        adapter.run_text_task(request)
    connect, read = post.call_args.kwargs["timeout"]
    assert connect == 10.0
    return read


def test_a_cpu_only_machine_gives_a_local_provider_four_times_the_call_timeout(monkeypatch):
    _machine(monkeypatch, "cpu")
    read = _post_read_timeout(_local_adapter(timeout_seconds=180.0), _request())
    assert read == 180.0 * local_compute_budget.CPU_ONLY_FACTOR


def test_a_cpu_only_machine_scales_the_thirty_second_default_when_the_manifest_has_no_timeout(monkeypatch):
    _machine(monkeypatch, "cpu")
    read = _post_read_timeout(_local_adapter(timeout_seconds=None), _request())
    assert read == 30.0 * local_compute_budget.CPU_ONLY_FACTOR


def test_a_cpu_only_machine_never_scales_a_cloud_provider(monkeypatch):
    _machine(monkeypatch, "cpu")
    read = _post_read_timeout(_cloud_adapter(), _request())
    assert read == 180.0


def test_the_turn_deadline_still_covers_the_scaled_call_on_a_cpu_only_machine(monkeypatch):
    """The nesting that must hold once the call may ask for 720 s: the conductor turn binds its
    provider deadline before the adapter runs (core/agent_runtime/agent.py does exactly this
    binding), and the clamp keeps the call inside it."""
    _machine(monkeypatch, "cpu")
    turn_deadline = runtime_active_clock.monotonic() + local_compute_budget.local_compute_seconds(75.0)
    context = bind_provider_deadline(
        None,
        turn_deadline_monotonic=turn_deadline,
        reason="conductor plan deadline",
    )
    metadata = copy_deadline_to_request_metadata({}, context)
    read = _post_read_timeout(_local_adapter(timeout_seconds=180.0), _request(metadata=metadata))
    # The scaled 720 s ceiling yields to the bound turn deadline: 300 s turn minus the 3 s
    # cleanup margin, less whatever this test itself spent.
    assert read < 180.0 * local_compute_budget.CPU_ONLY_FACTOR
    assert read <= local_compute_budget.local_compute_seconds(75.0) - DEFAULT_CLEANUP_MARGIN_SECONDS
    assert read >= local_compute_budget.local_compute_seconds(75.0) - DEFAULT_CLEANUP_MARGIN_SECONDS - 10.0


def test_the_local_queue_wait_scales_four_times_on_a_cpu_only_machine(monkeypatch):
    from core import local_model_admission

    _machine(monkeypatch, "cpu")
    monkeypatch.delenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", raising=False)
    assert local_model_admission.configured_queue_wait_seconds() == 240.0 * local_compute_budget.CPU_ONLY_FACTOR


def test_an_explicit_queue_wait_override_wins_over_scaling(monkeypatch):
    from core import local_model_admission

    _machine(monkeypatch, "cpu")
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "600")
    assert local_model_admission.configured_queue_wait_seconds() == 600.0
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "not-a-number")
    assert local_model_admission.configured_queue_wait_seconds() == 240.0 * local_compute_budget.CPU_ONLY_FACTOR
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "0")
    assert local_model_admission.configured_queue_wait_seconds() == 240.0 * local_compute_budget.CPU_ONLY_FACTOR


@pytest.mark.parametrize("accelerator", ["mps", "cuda"])
def test_a_gpu_machine_keeps_every_value_exactly_as_before(monkeypatch, accelerator):
    from core import local_model_admission

    _machine(monkeypatch, accelerator)
    assert _post_read_timeout(_local_adapter(timeout_seconds=180.0), _request()) == 180.0
    assert _post_read_timeout(_local_adapter(timeout_seconds=None), _request()) == 30.0
    assert _post_read_timeout(_cloud_adapter(), _request()) == 180.0
    monkeypatch.delenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", raising=False)
    assert local_model_admission.configured_queue_wait_seconds() == 240.0
