"""One runner and one residency policy per local model.

Ollama keys a loaded runner on the model plus its options (num_ctx, num_gpu, num_thread) and
keeps it loaded for the last call's keep_alive. BASE sized light lanes (the intent arbiter,
fact extraction) at half the serving window and sent serving calls with no keep_alive at all,
so one model thrashed between two runners and unloaded after five idle minutes. These tests
pin the local-speed contract: every lane of one model names one window, a serving call holds
the residency a twenty-minute idle needs, the governor can stand that down, and the
extractor never posts a tag the host did not install.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from tests.test_local_model_door_and_probe_measurement import _ollama_manifest


def _chat_request() -> ModelRequest:
    return ModelRequest(
        task_kind="normalization_assist", prompt="split this", system_prompt="s",
        temperature=0.0, max_output_tokens=32, output_mode="plain_text",
    )


def test_light_lanes_size_to_the_serving_window_of_the_same_model() -> None:
    """classifier and lightweight_utility lanes must not name a second runner of one model."""
    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    for role in ("classifier", "lightweight_utility", "tiny", "router"):
        assert _ollama_context_window_for_bundle_role(role, model_tag="qwen3:8b", bucket="C") == (
            _ollama_context_window_for_bundle_role("general", model_tag="qwen3:8b", bucket="C")
        ), role


def test_heavy_lanes_keep_their_larger_window() -> None:
    """Unifying light lanes upward must not invert the reasoning lane (its own pinned contract)."""
    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    assert _ollama_context_window_for_bundle_role(
        "heavy_reasoning", model_tag="qwen3:8b", bucket="D"
    ) > _ollama_context_window_for_bundle_role(
        "lightweight_utility", model_tag="qwen3:8b", bucket="D"
    )


def test_a_serving_call_holds_residency_the_governor_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real governor reads LIVE memory pressure (other jobs can make it stand down
    # mid-suite), so the positive case pins the governor's allowance, not the machine's state.
    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: True)
    adapter = OpenAICompatibleAdapter(_ollama_manifest("qwen3:8b"))
    payload = adapter._build_ollama_payload(_chat_request(), force_json=False, stream=False)
    assert payload["keep_alive"] == "30m"


def test_the_governor_stands_the_residency_extension_down_under_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenAICompatibleAdapter(_ollama_manifest("qwen3:8b"))
    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: False)
    payload = adapter._build_ollama_payload(_chat_request(), force_json=False, stream=False)
    assert "keep_alive" not in payload  # BASE behaviour: Ollama's own default decides


def test_a_manifest_keep_alive_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenAICompatibleAdapter(_ollama_manifest("qwen3:8b", keep_alive="7m"))
    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: False)
    payload = adapter._build_ollama_payload(_chat_request(), force_json=False, stream=False)
    assert payload["keep_alive"] == "7m"


def test_may_keep_warm_reads_policy_and_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.resource_governor as governor

    monkeypatch.setattr(
        "core.local_model_policy.local_models_enabled", lambda: False, raising=True
    )
    assert governor.may_keep_warm() is False
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda: True, raising=True)
    monkeypatch.setattr(
        governor.sr, "snapshot", lambda **_: SimpleNamespace(memory_pressure="warn")
    )
    assert governor.may_keep_warm() is False
    monkeypatch.setattr(
        governor.sr, "snapshot", lambda **_: SimpleNamespace(memory_pressure="nominal")
    )
    assert governor.may_keep_warm() is True


def test_may_keep_warm_fails_open_on_an_unknown_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.resource_governor as governor

    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda: True, raising=True)

    def _boom(**_: Any) -> None:
        raise RuntimeError("no sensor")

    monkeypatch.setattr(governor.sr, "snapshot", _boom)
    assert governor.may_keep_warm() is True  # the governor's law: never block on ignorance


def test_fact_extraction_never_posts_a_model_the_host_did_not_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.web.api.runtime import _fact_extraction_lane, _installed_fact_extraction_model

    monkeypatch.setattr(
        "core.runtime_provider_defaults.default_runtime_model_tag", lambda **_: "qwen3:8b"
    )
    monkeypatch.setenv("VOOL_INSTALLED_OLLAMA_MODELS", "qwen3:8b,qwen3:4b,nomic-embed-text:latest")
    assert _installed_fact_extraction_model() == "qwen3:4b"  # the smaller installed model
    # The lane registers the tag when the store lacks it, then rides its adapter: one runner
    # key with every other lane on that model.
    model, client = _fact_extraction_lane()
    assert model == "qwen3:4b"
    assert client is not None
    monkeypatch.setenv("VOOL_INSTALLED_OLLAMA_MODELS", "nomic-embed-text:latest")
    assert _installed_fact_extraction_model() == ""  # nothing chat-capable: extractor decides


def test_fact_extraction_sizes_against_the_serving_model_and_never_the_suggested_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The suggested tag (the model the Settings Download button offers) may not be installed at all.

    Measured on the v0.8 line, qwen3:8b and qwen3:4b installed: the suggested tag was qwen3.5:4b,
    nothing installed was smaller, so every extraction batch was posted to qwen3.5:4b.
    """
    from core.web.api.runtime import _fact_extraction_lane, _installed_fact_extraction_model

    monkeypatch.setattr(
        "core.runtime_provider_defaults.default_runtime_model_tag", lambda **_: "qwen3.5:4b"
    )
    monkeypatch.setenv("VOOL_INSTALLED_OLLAMA_MODELS", "qwen3:8b,qwen3:4b,nomic-embed-text:latest")
    # Sized against the model boot serves: the smaller installed model.
    assert _installed_fact_extraction_model("qwen3:8b") == "qwen3:4b"
    assert _fact_extraction_lane("qwen3:8b")[0] == "qwen3:4b"
    # Nothing installed is smaller than the serving model: the serving model itself.
    assert _installed_fact_extraction_model("qwen3:4b") == "qwen3:4b"
    # No serving model known, and the suggested tag is not installed: still an installed model.
    assert _installed_fact_extraction_model() == "qwen3:4b"
    monkeypatch.setenv("VOOL_INSTALLED_OLLAMA_MODELS", "qwen3:8b,nomic-embed-text:latest")
    assert _installed_fact_extraction_model() == "qwen3:8b"
    assert _installed_fact_extraction_model("qwen3.5:4b") == "qwen3:8b"


def test_fact_extraction_rides_an_adapter_manifest_never_a_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane's client takes an adapter path -- a persisted manifest or an in-memory one.

    Registering the tag would add a ranked candidate every tool turn then tries (measured:
    each tool round ran twice); the register door stays a person's deliberate act.
    """
    from core.model_registry import ModelRegistry
    from core.web.api.runtime import _fact_extraction_lane, _lane_manifest_for

    monkeypatch.setattr(
        "core.runtime_provider_defaults.default_runtime_model_tag", lambda **_: "qwen3:8b"
    )
    monkeypatch.setenv("VOOL_INSTALLED_OLLAMA_MODELS", "qwen3:8b,qwen3:4b,nomic-embed-text:latest")
    before = {(m.provider_name, m.model_name) for m in ModelRegistry().list_manifests()}
    model, client = _fact_extraction_lane()
    assert model == "qwen3:4b" and client is not None
    after = {(m.provider_name, m.model_name) for m in ModelRegistry().list_manifests()}
    assert after == before, "the lane must not register anything"
    assert _lane_manifest_for("qwen3:4b").runtime_config["context_window"] > 0


def test_the_thread_stamp_never_follows_the_live_budget() -> None:
    """A presence-driven thread flip re-keys the runner and reloads the model (measured: 5->9).

    The stamp is the machine's fixed count across a presence flip and an explicitly-set budget
    alike, the same count the direct-to-engine lanes send (core.compute_mode).
    """
    from adapters import openai_compatible_adapter as adapter_module
    from core.compute_mode import (
        ComputeBudget,
        get_active_compute_budget,
        ollama_runner_thread_count,
        set_active_compute_budget,
    )

    machine = {"num_thread": ollama_runner_thread_count()}
    before = get_active_compute_budget()
    try:
        set_active_compute_budget(ComputeBudget(
            mode="balanced", cpu_threads=5, gpu_memory_fraction=0.0, worker_pool_cap=1,
            reason="User present — balanced mode"))
        first = adapter_module.shared_ollama_thread_option()
        set_active_compute_budget(ComputeBudget(
            mode="max_push", cpu_threads=9, gpu_memory_fraction=0.0, worker_pool_cap=1,
            reason="User idle 130s (>= 120s threshold)"))
        second = adapter_module.shared_ollama_thread_option()
        set_active_compute_budget(ComputeBudget(
            mode="balanced", cpu_threads=ollama_runner_thread_count() + 3, gpu_memory_fraction=0.0,
            worker_pool_cap=1, reason="test"))
        third = adapter_module.shared_ollama_thread_option()
    finally:
        set_active_compute_budget(before)
    assert first == second == third == machine
