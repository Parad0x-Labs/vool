"""An Auto turn's own local model is the ordinary answer's pick, never the first big model registered.

On a 24 GB iMac (installed 1e7b35e24, 2026-11-27) the clean loop's Auto pick was the first ready local model in
registry order, qwen3.8:27b (18 GB), while the ordinary answer was written by qwen3:8b. Every yes/no judgement of a
message asks that pick (`core.agent_runtime.turn_judge`), so each message loaded the 27b, pushed qwen3:8b out and
loaded it again to answer: 90-116 s per check and 8 GB of swap.

All names and values here are invented for this contract.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import memory_first_router
from core.clean_loop import brain
from storage.model_provider_manifest import ModelProviderManifest


def _local(name: str, billions: float) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="local_qwen_provider", license_name="Apache-2.0",
        license_reference="https://example.invalid/license", weight_location="external",
        runtime_dependency="ollama", capabilities=["summarize", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"runtime_family": "ollama", "cost_class": "free_local", "deployment_class": "local",
                  "parameter_billions": billions},
    )


BIG = _local("qwen3.8:27b", 27.0)
DAILY = _local("qwen3:8b", 8.0)
SMALLER = _local("qwen3:4b", 4.0)


@pytest.fixture
def machine(monkeypatch):
    reading = {"free": 11.0, "resident": ("qwen3:8b",), "default": "qwen3:8b"}
    monkeypatch.setattr(memory_first_router, "_local_model_free_memory_gb", lambda: reading["free"])
    monkeypatch.setattr(memory_first_router, "_recent_resident_model_tags", lambda: reading["resident"])
    monkeypatch.setattr(memory_first_router, "_cached_default_model_tag", lambda: reading["default"])
    return reading


def test_a_big_model_registered_first_is_not_the_auto_pick(machine):
    assert brain.auto_local_manifest([BIG, DAILY]) is DAILY


def test_the_pick_does_not_depend_on_registry_order_or_on_what_is_resident(machine):
    for resident in (("qwen3:8b",), ("qwen3.8:27b",), ()):
        machine["resident"] = resident
        assert brain.auto_local_manifest([BIG, DAILY]) is DAILY
        assert brain.auto_local_manifest([DAILY, BIG]) is DAILY


def test_with_free_memory_unknown_the_everyday_size_cap_still_holds(machine):
    machine["free"] = None
    assert brain.auto_local_manifest([BIG, DAILY]) is DAILY


def test_a_model_that_cannot_load_next_to_what_is_loaded_gives_way_to_one_that_can(machine):
    # Free memory holds the 4b but not the 8b, and the 8b is not loaded: the 4b answers rather than a swap.
    machine.update(free=5.0, resident=("nomic-embed-text:latest",), default="qwen3:8b")
    assert brain.auto_local_manifest([DAILY, SMALLER]) is SMALLER
    # Loaded already, the 8b costs nothing new and stays the pick.
    machine["resident"] = ("qwen3:8b",)
    assert brain.auto_local_manifest([DAILY, SMALLER]) is DAILY


def test_the_only_ready_model_is_the_pick_whatever_its_size(machine):
    assert brain.auto_local_manifest([BIG]) is BIG
    assert brain.auto_local_manifest([]) is None


def test_an_unreadable_machine_keeps_the_registry_order(machine, monkeypatch):
    def broken():
        raise OSError("no reading")

    monkeypatch.setattr(memory_first_router, "_local_model_free_memory_gb", broken)
    assert brain.auto_local_manifest([BIG, DAILY]) is BIG


def test_resolve_local_manifest_on_auto_names_the_everyday_pick(machine, monkeypatch):
    import core.cloud_only_default as cloud_only_default
    import core.local_model_policy as local_model_policy
    import core.local_model_presence as local_model_presence

    monkeypatch.setattr(local_model_policy, "local_models_enabled", lambda: True)
    monkeypatch.setattr(cloud_only_default, "default_for_auto_turn", lambda **_: False)
    monkeypatch.setattr(local_model_presence, "local_model_running", lambda: True)
    monkeypatch.setattr(brain, "_ready", lambda manifest: True)
    monkeypatch.delenv(brain.MODEL_ENV, raising=False)
    router = SimpleNamespace(registry=SimpleNamespace(list_manifests=lambda enabled_only=True: [BIG, DAILY]))
    assert brain.resolve_local_manifest(router, {"requested_model": "auto"}) is DAILY
    assert brain.resolve_local_manifest(router, {}) is DAILY


def test_a_model_the_person_picks_is_the_one_that_answers_even_a_big_one(machine, monkeypatch):
    monkeypatch.delenv(brain.MODEL_ENV, raising=False)
    router = SimpleNamespace(
        registry=SimpleNamespace(list_manifests=lambda enabled_only=True: [DAILY, BIG]),
        _requested_model_manifest=lambda context: BIG,
    )
    assert brain.resolve_local_manifest(router, {"requested_model": "qwen3.8:27b"}) is BIG
