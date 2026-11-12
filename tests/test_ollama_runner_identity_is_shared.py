"""Every native-Ollama payload the runtime sends names ONE runner, and the planner's budgets
cover the runner that is actually resident.

MEASURED 2026-09-10 (rig s51, daemon at 6249a0d9, qwen3:8b certified through the product door
25 s earlier): the first conductor turn's clause-split call on qwen3:8b died at its 15 s budget
(`model.call_failed`), the conductor declined and the plain lane refused the whole turn. Direct
timing of the same call: 20.3 s on a cold Ollama runner (load 14.0 s + prompt eval 5.0 s +
generation 1.1 s) and 1.4 s warm. The runner was cold BECAUSE certification had loaded the model
with `num_ctx` alone while serving stamps `num_thread`/`num_gpu` too -- Ollama keys its runner on
those options and reloaded 9 GB for a call that needed a second.
"""

from __future__ import annotations

from typing import Any

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from storage.model_provider_manifest import ModelProviderManifest

RUNNER_KEYS = ("num_thread", "num_ctx", "num_gpu")


def _manifest(**runtime_config: Any) -> ModelProviderManifest:
    from tests.test_local_model_door_and_probe_measurement import _ollama_manifest

    return _ollama_manifest("qwen3:8b", context_window=24576, **runtime_config)


def _runner_keys(options: dict[str, Any]) -> dict[str, Any]:
    return {key: options.get(key) for key in RUNNER_KEYS if key in options}


class _Stop(Exception):
    pass


def _certification_payload(adapter: OpenAICompatibleAdapter, monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def _seal(**kwargs: Any):
        captured.update(kwargs.get("payload") or {})
        raise _Stop()

    monkeypatch.setattr("adapters.openai_compatible_adapter.seal_direct_provider_invocation", _seal)
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter._strict_tool_certification_loopback", lambda url: True
    )
    with pytest.raises(_Stop):
        adapter.tool_certification_exchange(
            messages=[{"role": "user", "content": "add 2 and 3"}], tools=(), tool_choice=None,
            max_output_tokens=64, timeout_seconds=5.0,
        )
    return captured


def test_serving_certification_and_prewarm_name_one_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenAICompatibleAdapter(_manifest(num_gpu=99))
    request = ModelRequest(
        task_kind="normalization_assist", prompt="split this", system_prompt="s",
        temperature=0.0, max_output_tokens=32, output_mode="plain_text",
    )
    serving = _runner_keys(adapter._build_ollama_payload(request, force_json=False, stream=False)["options"])
    certification = _runner_keys(_certification_payload(adapter, monkeypatch)["options"])
    _url, prewarm_payload = adapter._ollama_prewarm_request(
        base_url="http://127.0.0.1:11434", strategy="ollama_chat", prewarm_config={}
    )
    prewarm = _runner_keys(prewarm_payload["options"])
    assert set(serving) == set(RUNNER_KEYS), serving
    assert certification == serving, (certification, serving)
    assert prewarm == serving, (prewarm, serving)


def test_a_configured_prewarm_option_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenAICompatibleAdapter(_manifest())
    _url, payload = adapter._ollama_prewarm_request(
        base_url="http://127.0.0.1:11434", strategy="ollama_chat",
        prewarm_config={"options": {"num_ctx": 4096}},
    )
    assert payload["options"]["num_ctx"] == 4096
    assert "num_thread" in payload["options"]


def test_an_openai_dialect_call_on_an_ollama_runtime_shares_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenAICompatibleAdapter(_manifest(num_gpu=99))
    monkeypatch.setattr(adapter, "_runtime_family", lambda: "ollama")
    request = ModelRequest(
        task_kind="normalization_assist", prompt="split this", system_prompt="s",
        temperature=0.0, max_output_tokens=32, output_mode="plain_text",
    )
    payload = adapter._build_openai_payload(request, force_json=False, stream=False)
    native = adapter._build_ollama_payload(request, force_json=False, stream=False)
    assert _runner_keys(payload["options"]) == _runner_keys(native["options"])


def test_the_planner_budgets_cover_a_cold_runner() -> None:
    """The measured cold cost of the clause split (20.3 s, qwen3:8b) sets these floors."""
    from core.agent_runtime.turn_planner_hook import _PLANNER_TIMEOUT_SECONDS
    from core.conductor.scheduler import DEFAULT_PLAN_DEADLINE_S, PLANNER_PHASE_CAP_S

    COLD_CLAUSE_SPLIT_S = 20.3
    SERVED_WARM_SEMANTIC_PROOF_S = 12.6  # dad61973, three sessions, 12.4-12.6 s
    assert _PLANNER_TIMEOUT_SECONDS > COLD_CLAUSE_SPLIT_S
    assert PLANNER_PHASE_CAP_S >= COLD_CLAUSE_SPLIT_S + SERVED_WARM_SEMANTIC_PROOF_S, (
        "the phase cap must cover a cold clause split followed by the semantic proof"
    )
    assert DEFAULT_PLAN_DEADLINE_S - PLANNER_PHASE_CAP_S > COLD_CLAUSE_SPLIT_S, "the node phase must also survive a cold runner"


def test_the_runner_does_not_change_when_the_user_steps_away() -> None:
    """Ollama reloads a resident model whose runner options change (server/sched.go, needsReload).

    The compute budget turns to max_push after two idle minutes; a thread count read from it gave
    the chat model a new runner at every switch, so the first message after a pause reloaded the
    model and the next one reloaded it back.
    """
    from core.compute_mode import compute_budget, get_active_compute_budget, set_active_compute_budget

    adapter = OpenAICompatibleAdapter(_manifest())
    request = ModelRequest(
        task_kind="normalization_assist", prompt="split this", system_prompt="s",
        temperature=0.0, max_output_tokens=32, output_mode="plain_text",
    )
    before = get_active_compute_budget()
    try:
        set_active_compute_budget(compute_budget(idle_seconds=0, cpu_cores=10))
        present = _runner_keys(adapter._build_ollama_payload(request, force_json=False, stream=False)["options"])
        set_active_compute_budget(compute_budget(idle_seconds=600, cpu_cores=10))
        away = _runner_keys(adapter._build_ollama_payload(request, force_json=False, stream=False)["options"])
    finally:
        set_active_compute_budget(before)
    assert present == away, (present, away)


@pytest.mark.parametrize("forced_num_gpu", ["", "99"])
def test_direct_lanes_name_the_runner_the_adapter_serves(forced_num_gpu: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper model's direct lanes (memory extraction, intent arbiter) and the chat model's
    (summarizer, builder) send the runner keys the adapter sends for the same registered model.

    Measured on the v0.8 line (bb35ff83) against a recording Ollama, before this: the memory
    extraction lane sent num_ctx alone, and qwen3:0.6b reloaded 48 times in a 42-turn chat.
    """
    from core.agent_runtime.builder.controller import builder_runner_options
    from core.conversation_summarizer import _summarizer_runner_options
    from core.fact_extractor import _direct_lane_runner_options
    from core.intent_arbiter import _arbiter_runner_options
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _ensure_local_ollama_provider

    env = {"VOOL_CONTEXT_BUCKET": "D"}
    if forced_num_gpu:
        env["VOOL_OLLAMA_NUM_GPU"] = forced_num_gpu
    for key in ("VOOL_OLLAMA_CONTEXT_WINDOW", "VOOL_ADAPTIVE_CONTEXT", "OLLAMA_KV_CACHE_TYPE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if not forced_num_gpu:
        monkeypatch.delenv("VOOL_OLLAMA_NUM_GPU", raising=False)

    def served(model_tag: str, role: str) -> dict[str, Any]:
        registry = ModelRegistry()
        _ensure_local_ollama_provider(registry, model_tag=model_tag, bundle_role=role, env=env)
        manifest = registry.get_manifest("ollama-local", model_tag)
        assert manifest is not None
        return _runner_keys(OpenAICompatibleAdapter(manifest)._ollama_runner_options(None))

    helper = served("qwen3:0.6b", "lightweight_utility")
    assert _direct_lane_runner_options("qwen3:0.6b") == helper
    assert _arbiter_runner_options("qwen3:0.6b") == helper
    chat = served("qwen3:30b-instruct", "general")
    assert _summarizer_runner_options("qwen3:30b-instruct") == chat
    assert builder_runner_options("qwen3:30b-instruct") == chat
    assert ("num_gpu" in helper) is bool(forced_num_gpu)
