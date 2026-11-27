"""Every call to a local Ollama model names the one runner and the one residency that model is served on, and the
turn's plan names the model the turn runs on.

Owner's 24 GB iMac, 2026-11-27 (installed 0.8 build, Ollama with OLLAMA_CONTEXT_LENGTH=262144): qwen3:8b ran at
about 4 tokens a second, the runner was unloaded and reloaded every turn, the embedding model reloaded 13 times in a
day, and the lane proof read planned qwen3:4b, ran qwen3:8b (`planned_adapter_mismatch`). Three causes, each pinned
here:

* lanes that posted without the shared residency (the embedding lane, the summary and memory lanes on the chat
  model), so Ollama dropped the model to its own 5-minute default after them;
* the task classifier posting the hardware tier's suggested tag (qwen3.5:9b on a 24 GB Mac) through Ollama's
  OpenAI-compatible door, which takes no window, so a second large model loaded at the server's own context;
* the plan sizing a model's fit by one rule and the call's memory gate by another: the plan picked a model the gate
  then could not load, after the gate had unloaded every resident model to try.

All model names are the ones on the report; nothing here runs a model.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from core.runtime_provider_defaults import ollama_direct_runner_options

RUNNER_KEYS = ("num_ctx", "num_thread", "num_gpu")


class _Sealed(Exception):
    pass


def _capture_seal(monkeypatch: pytest.MonkeyPatch, module: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _seal(**kwargs: Any):
        captured.update(kwargs)
        raise _Sealed()

    monkeypatch.setattr(f"{module}.seal_direct_provider_invocation", _seal)
    return captured


@pytest.fixture
def warm(monkeypatch: pytest.MonkeyPatch):
    """The governor allows residency (the real one reads live memory pressure, which other jobs move)."""
    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: True)
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda: True)


def _runner(options: dict[str, Any]) -> dict[str, Any]:
    return {key: options[key] for key in RUNNER_KEYS if key in options}


def test_the_task_classifier_asks_the_classifier_model_on_its_own_runner(warm, monkeypatch) -> None:
    from core import intent_arbiter, task_router

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("core.ollama_endpoint.ollama_base_url", lambda *a, **k: "http://127.0.0.1:11434")
    monkeypatch.setattr(task_router.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(intent_arbiter, "_resolve_model", lambda: "qwen3:0.6b")
    # The tier this machine would be offered for download is not the model that classifies.
    monkeypatch.setattr("core.hardware_tier.recommended_ollama_model", lambda *a, **k: "qwen3.5:9b")
    sealed = _capture_seal(monkeypatch, "core.task_router")
    assert task_router._classify_via_model("Kannst du mir die Notizen von gestern zusammenfassen?") == ""
    payload = sealed["payload"]
    assert sealed["destination_url"] == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "qwen3:0.6b"
    assert _runner(payload["options"]) == _runner(ollama_direct_runner_options("qwen3:0.6b", bundle_role="classifier"))
    assert _runner(payload["options"]) == _runner(intent_arbiter._arbiter_runner_options("qwen3:0.6b"))
    assert payload["keep_alive"] == "30m"


def test_with_no_classifier_model_the_classifier_asks_nothing(warm, monkeypatch) -> None:
    from core import intent_arbiter, task_router

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("core.ollama_endpoint.ollama_base_url", lambda *a, **k: "http://127.0.0.1:11434")
    monkeypatch.setattr(task_router.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(intent_arbiter, "_resolve_model", lambda: "")
    monkeypatch.setattr(
        "core.task_router.seal_direct_provider_invocation",
        lambda **_k: pytest.fail("posted with no classifier model"),
    )
    assert task_router._classify_via_model("Primink man rytoj paskambinti mamai") == ""


@pytest.mark.parametrize("pressure_allows", [True, False])
def test_an_embedding_holds_the_residency_every_other_lane_holds(monkeypatch, pressure_allows) -> None:
    from core import embedding_service
    from core.resource_governor import serving_keep_alive

    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda: True)
    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: pressure_allows)
    sealed = _capture_seal(monkeypatch, "core.embedding_service")
    assert embedding_service._ollama_embed_batch(["Wo liegt der Ersatzschlüssel?"], "nomic-embed-text") is None
    payload = sealed["payload"]
    assert payload.get("keep_alive") == serving_keep_alive()
    assert ("keep_alive" in payload) is pressure_allows


def test_the_research_refinement_names_its_runner_on_ollamas_own_door(warm, monkeypatch) -> None:
    from core import autonomous_topic_research

    monkeypatch.setattr("core.brain_hive_research._ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr("core.hardware_tier.recommended_ollama_model", lambda *a, **k: "qwen3:8b")
    sealed = _capture_seal(monkeypatch, "core.autonomous_topic_research")
    queries = autonomous_topic_research._generate_refinement_queries(
        title="Lagerung von Saatgut", topic={"topic_id": "t-1"},
        first_pass_results=[{"query": "saatgut lagern", "snippet_count": 0}],
    )
    assert queries == []
    payload = sealed["payload"]
    assert sealed["destination_url"] == "http://127.0.0.1:11434/api/chat"
    assert _runner(payload["options"]) == _runner(ollama_direct_runner_options("qwen3:8b", bundle_role="general"))
    assert payload["keep_alive"] == "30m"


def test_the_summary_and_memory_lanes_keep_the_chat_models_residency(warm, monkeypatch) -> None:
    from core import conversation_summarizer, fact_extractor

    sealed = _capture_seal(monkeypatch, "core.conversation_summarizer")
    with pytest.raises(_Sealed):
        conversation_summarizer._call_ollama("qwen3:8b", [{"role": "user", "content": "Santrauka, prašau."}])
    assert sealed["payload"]["keep_alive"] == "30m"

    sealed = _capture_seal(monkeypatch, "core.fact_extractor")
    extractor = fact_extractor.FactExtractor.__new__(fact_extractor.FactExtractor)
    extractor._model = "qwen3:0.6b"
    extractor._ollama_url = "http://127.0.0.1:11434"
    extractor._model_client = None
    extractor._session_id = extractor._project_id = ""
    monkeypatch.setattr(fact_extractor.FactExtractor, "_local_endpoint_answers", lambda self: True)
    with contextlib.suppress(_Sealed):
        extractor._call_model("turn 1 user: mein Fahrrad ist grün")
    assert sealed["payload"]["keep_alive"] == "30m"
    assert _runner(sealed["payload"]["options"]) == _runner(
        ollama_direct_runner_options("qwen3:0.6b", bundle_role="lightweight_utility")
    )


def test_under_memory_pressure_no_lane_extends_residency(monkeypatch) -> None:
    from core import intent_arbiter

    monkeypatch.setattr("core.resource_governor.may_keep_warm", lambda: False)
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setattr(intent_arbiter, "_local_models_disabled", lambda: False)
    intent_arbiter.reset_breaker()
    sealed = _capture_seal(monkeypatch, "core.intent_arbiter")
    assert intent_arbiter.arbitrate_followup("", "", "", prompt="Question?", model="qwen3:8b", runner_role="general") is None
    assert "keep_alive" not in sealed["payload"]


# --- the plan names the model the turn runs on -------------------------------------------------------------------


def _manifest(model_name: str):
    from core.model_registry import ModelProviderManifest

    return ModelProviderManifest(
        provider_name="ollama-local", model_name=model_name, source_type="http", adapter_type="openai_compatible",
        runtime_config={"base_url": "http://127.0.0.1:11434"}, metadata={"runtime_family": "ollama"},
    )


def _tight_machine(monkeypatch, *, available_gb: float, resident: list[dict[str, Any]]) -> list[list[str]]:
    import core.resource_governor as governor

    unloaded: list[list[str]] = []
    monkeypatch.setattr(governor, "_local_ollama_base", lambda _b=None: "http://127.0.0.1:11434")
    monkeypatch.setattr(governor.sr, "snapshot", lambda **_k: SimpleNamespace(available_ram_gb=available_gb))
    monkeypatch.setattr(governor, "resident_models", lambda **_k: list(resident))
    monkeypatch.setattr(governor, "estimated_model_gb", lambda name, **_k: {"qwen3:8b": 5.2, "qwen3:4b": 2.6}.get(name, 0.0))
    monkeypatch.setattr(governor, "_unload_models", lambda names, **_k: unloaded.append(list(names)) or list(names))
    monkeypatch.setattr(governor.time, "sleep", lambda _s: None)
    return unloaded


def _plan(text: str, manifests, unready):
    from core.local_inference_autopilot import build_local_inference_autopilot_plan
    from core.provider_routing import provider_capability_truth_for_manifest

    return build_local_inference_autopilot_plan(
        user_text=text, task_kind="normalization_assist", output_mode="plain_text", provider_role="auto",
        capability_truth=tuple(provider_capability_truth_for_manifest(m) for m in manifests), source_context={},
        default_model_tag="qwen3:8b", resident_model_tags=("qwen3:8b",), unready_local_models=unready,
    )


def test_on_a_tight_machine_the_plan_keeps_the_resident_model(monkeypatch) -> None:
    from core import memory_first_router as router

    manifests = [_manifest("qwen3:8b"), _manifest("qwen3:4b")]
    _tight_machine(monkeypatch, available_gb=0.1, resident=[{"name": "qwen3:8b", "size_gb": 5.2}])
    unready = router._local_models_that_cannot_load_now(manifests)
    assert unready == frozenset({"qwen3:4b"})
    # A short hello plans the small model's lane: on this machine that model cannot load beside the resident one.
    assert _plan("hi", manifests, None).selected_model == "qwen3:4b"
    assert _plan("hi", manifests, unready).selected_model == "qwen3:8b"
    # The call's own gate agrees: the resident model serves, the other one would not load.
    import core.resource_governor as governor

    assert governor.plan_model_load("qwen3:8b").ok is True


def test_with_room_to_load_nothing_is_held_back(monkeypatch) -> None:
    from core import memory_first_router as router

    manifests = [_manifest("qwen3:8b"), _manifest("qwen3:4b")]
    _tight_machine(monkeypatch, available_gb=9.0, resident=[{"name": "qwen3:8b", "size_gb": 5.2}])
    assert router._local_models_that_cannot_load_now(manifests) == frozenset()


def test_when_no_local_model_can_load_the_plan_is_left_to_the_gate(monkeypatch) -> None:
    from core import memory_first_router as router

    manifests = [_manifest("qwen3:8b"), _manifest("qwen3:4b")]
    _tight_machine(monkeypatch, available_gb=0.1, resident=[])
    # Naming every local model would hand the turn to another lane by a momentary reading: nothing is named.
    assert router._local_models_that_cannot_load_now(manifests) == frozenset()


def test_a_model_the_memory_gate_benched_is_not_planned_first(monkeypatch) -> None:
    from core import memory_first_router as router

    manifests = [_manifest("qwen3:8b"), _manifest("qwen3:4b")]
    _tight_machine(monkeypatch, available_gb=9.0, resident=[{"name": "qwen3:8b", "size_gb": 5.2}])
    monkeypatch.setattr(router, "_RAM_GATE_COOLDOWN", {})
    router._note_ram_gate_refusal("ollama-local:qwen3:4b")
    assert router._local_models_that_cannot_load_now(manifests) == frozenset({"qwen3:4b"})


def test_a_load_no_unloading_can_make_room_for_unloads_nothing(monkeypatch) -> None:
    import core.resource_governor as governor

    unloaded = _tight_machine(monkeypatch, available_gb=0.1, resident=[{"name": "qwen3:0.6b", "size_gb": 1.0}])
    decision = governor.plan_model_load("qwen3:8b")
    assert decision.ok is False
    assert unloaded == []  # unloading 1 GB could never make room for 5.2 GB plus the floor


def test_a_load_unloading_can_make_room_for_still_reclaims(monkeypatch) -> None:
    import core.resource_governor as governor

    readings = iter([0.1, 9.5])
    unloaded = _tight_machine(monkeypatch, available_gb=0.1, resident=[{"name": "gpt-oss:20b", "size_gb": 13.0}])
    monkeypatch.setattr(governor.sr, "snapshot", lambda **_k: SimpleNamespace(available_ram_gb=next(readings)))
    assert governor.plan_model_load("qwen3:8b").ok is True
    assert unloaded == [["gpt-oss:20b"]]
