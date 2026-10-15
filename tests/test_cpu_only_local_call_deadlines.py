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
call; the router's fallback budget on an ordinary turn scales with the local call it bounds and
its written cap is never scaled a second time; and on a GPU machine every value stays exactly what
it was. CPU-only is faked the way
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


# --- The ordinary chat path: the router's fallback budget and the call it caps ---------------
#
# A plain question and a memory recall turn are not conductor turns: no turn deadline is bound on
# them, and the only wall clock over the local call is the router's sequential fallback budget,
# which writes the remaining window into the manifest copy the adapter receives. These drive the
# real ``MemoryFirstRouter._execute_provider_task`` with the real adapter and record the read
# timeout of the real HTTP call (``requests.post`` is the only thing replaced), the same fake
# clock as tests/test_fallback_budget_characterization.py advancing by each hang.


class _RouterClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.start = self.now

    def monotonic(self) -> float:
        return self.now


def _router_manifest(name: str, *, local: bool):
    from storage.model_provider_manifest import ModelProviderManifest

    # Local fixtures are classified local by ``deployment_class`` and sit off loopback on purpose,
    # like the fallback-budget characterization fixtures: the tool-certification probe reaches only
    # loopback endpoints, and these are budget scenarios, not certified local models.
    return ModelProviderManifest(
        provider_name=f"fixture-{name}", model_name=f"candidate-{name}", source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://example.invalid", weight_location="external",
        runtime_dependency="ollama" if local else "remote", capabilities=["summarize", "format", "structured_json"],
        runtime_config={
            "base_url": "http://fixture.invalid:9" if local else "https://cloud.fixture.invalid/v1",
            "api_path": "/v1/chat/completions",
            "timeout_seconds": 180,
        },
        metadata={"deployment_class": "local" if local else "remote", "orchestration_role": "queen"},
    )


def _drive_router(monkeypatch, manifests, *, output_mode: str = "plain_text"):
    """Every candidate hangs until its own read timeout. Returns (read timeouts, elapsed, decision)."""
    import requests

    from core import memory_first_router
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(memory_first_router, "_NO_USABLE_GPU_CACHE", None)
    clock = _RouterClock()
    reads: list[float] = []

    def post(*args, **kwargs):
        read = kwargs["timeout"][1]
        reads.append(read)
        clock.now += read
        raise requests.exceptions.ReadTimeout(f"Read timed out. (read timeout={read})")

    router = MemoryFirstRouter()
    for manifest in manifests:
        router.registry.register_manifest(manifest)

    def build_adapter(manifest):
        adapter = OpenAICompatibleAdapter(manifest)
        adapter.health_check = lambda: {"ok": True}
        return adapter

    plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=manifests[0].provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1", "lane": "daily",
            "selected_provider_id": manifests[0].provider_id, "selected_model": manifests[0].model_name,
            "verifier_required": False, "verifier_provider_id": None, "verifier_model": None,
            "explicit_heavy": False, "prefix_cache": {"backend": "none", "supported": False}, "warnings": [],
        },
    )
    with mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=list(manifests)
    ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
        "core.memory_first_router.build_local_inference_autopilot_plan", return_value=plan
    ), mock.patch("core.memory_first_router.time.monotonic", side_effect=clock.monotonic), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", side_effect=post
    ):
        decision = router._execute_provider_task(
            task=SimpleNamespace(task_id="cpu-budget-task", task_summary="what did I say about my sister?"),
            classification={"task_class": "daily"},
            interpretation=SimpleNamespace(reconstructed_text="what did I say about my sister?"),
            context_result=SimpleNamespace(
                retrieval_confidence_score=0.2,
                report=SimpleNamespace(
                    retrieval_confidence=0.2,
                    to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
                ),
            ),
            persona=SimpleNamespace(),
            task_hash="cpu-budget-hash",
            task_kind="chat",
            output_mode=output_mode,
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            source_context={
                "surface": "openclaw",
                "_canonical_user_turn_id": "cpu-budget-turn",
                "session_id": "cpu-budget-session",
                "runtime_session_id": "cpu-budget-session",
            },
        )
    return reads, clock.now - clock.start, decision


def test_cpu_only_ordinary_turn_gives_a_local_call_the_scaled_window(monkeypatch):
    """The reported failure: the local answer was cut at 180 s on CPU-only. It now runs to the
    scaled ceiling, and that is still the whole budget, not more."""
    _machine(monkeypatch, "cpu")
    reads, elapsed, decision = _drive_router(monkeypatch, [_router_manifest("a", local=True)])
    assert reads == [pytest.approx(180.0 * local_compute_budget.CPU_ONLY_FACTOR)]
    assert elapsed == pytest.approx(180.0 * local_compute_budget.CPU_ONLY_FACTOR)
    assert decision.used_model is not True


def test_cpu_only_router_cap_is_not_scaled_twice(monkeypatch):
    """The cap the router writes is real seconds left in its budget. Scaling it again in the
    adapter let the first of two hanging local candidates run 4 x its cap and outlive the whole
    budget, so the successor's reserved window never came."""
    _machine(monkeypatch, "cpu")
    budget = 180.0 * local_compute_budget.CPU_ONLY_FACTOR
    reads, elapsed, _decision = _drive_router(
        monkeypatch, [_router_manifest("a", local=True), _router_manifest("b", local=True)]
    )
    assert reads[0] == pytest.approx(budget - 5.0)
    assert len(reads) == 2 and reads[1] == pytest.approx(5.0)
    assert elapsed <= budget + 5.0


def test_cpu_only_cloud_only_chain_keeps_todays_budget_and_call_timeout(monkeypatch):
    _machine(monkeypatch, "cpu")
    reads, elapsed, _decision = _drive_router(monkeypatch, [_router_manifest("c", local=False)])
    assert reads == [pytest.approx(180.0)]
    assert elapsed == pytest.approx(180.0)


@pytest.mark.parametrize("accelerator", ["mps", "cuda"])
def test_gpu_ordinary_turn_keeps_todays_budget_and_caps(monkeypatch, accelerator):
    _machine(monkeypatch, accelerator)
    reads, elapsed, _decision = _drive_router(
        monkeypatch, [_router_manifest("a", local=True), _router_manifest("b", local=True)]
    )
    assert reads == [pytest.approx(55.0), pytest.approx(5.0)]
    assert elapsed == pytest.approx(60.0)
