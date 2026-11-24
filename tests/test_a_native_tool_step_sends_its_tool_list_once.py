"""A tool step that hands the model its tools as native functions does not list them again in prose.

Intel smoke on 94b9dad5d (2026-11-24, qwen3:8b on a CPU that reads 7 to 10 prompt tokens a second): a
contacts step's prompt was 3,972 tokens and about 1,100 of them were the prose catalog listing every
offered tool with the same arguments and descriptions the payload's native ``tools`` carried. A cold step
took 7 to 9 minutes. A lane handed native functions now gets the catalog without that list (the name
mapping and the closing rules stay); a lane that reads about its tools only in prose keeps all of it.
Which tools, how many, and the question are drawn at random.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.capability_graph import model_visible_specs
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.prompt_normalizer import _tool_intent_catalog_text
from core.tool_catalog_wire import CLOSING_RULES_START, PROSE_ONLY_START, catalog_for_native_wire
from core.tool_offer_assembly import ToolOffer

_RNG = random.Random()


def _offer() -> tuple[ToolOffer, list[dict]]:
    specs = [dict(spec) for spec in model_visible_specs() if spec.get("intent") and spec.get("description")]
    picked = _RNG.sample(specs, _RNG.randint(3, min(8, len(specs))))
    return ToolOffer(specs=tuple(picked)), picked


def _request(catalog: str, tools: tuple) -> ModelRequest:
    question = _RNG.choice(("what is my locker number?", "kas mano spintelės numeris?", "find Ana's phone"))
    messages = [{"role": "system", "content": "You are VOOL."}, {"role": "system", "content": catalog},
                {"role": "user", "content": question}]
    return ModelRequest(task_kind="tool_intent", prompt=question, messages=messages, output_mode="tool_intent",
                        tools=tools, tool_choice="required", max_output_tokens=64, reasoning_mode="disabled")


def _adapter(*, family: str, dialect: str = "") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="catalog-fixture", model_name="fixture-model", metadata={"runtime_family": family},
        runtime_config={"base_url": "http://127.0.0.1:11434", "timeout_seconds": 2.0,
                        **({"tool_dialect": dialect} if dialect else {})},
    ))


def _catalog_sent(payload: dict) -> str:
    return next(m["content"] for m in payload["messages"] if "When native functions are supplied" in str(m["content"]))


def _listed(text: str, specs: list[dict]) -> list[str]:
    return [spec["intent"] for spec in specs if f"- {spec['intent']}" in text]


def test_the_catalog_lists_every_offered_tool_in_prose():
    offer, specs = _offer()
    catalog = _tool_intent_catalog_text(offer=offer)
    assert _listed(catalog, specs) == [spec["intent"] for spec in specs]
    assert PROSE_ONLY_START in catalog and CLOSING_RULES_START in catalog


def test_the_local_lane_with_native_tools_sends_the_list_once(monkeypatch):
    import adapters.openai_compatible_adapter as oca

    monkeypatch.setattr(oca, "flag_enabled", lambda name: True)
    offer, specs = _offer()
    catalog = _tool_intent_catalog_text(offer=offer)
    payload = _adapter(family="ollama")._build_ollama_payload(
        _request(catalog, build_cloud_tool_definitions(specs)), force_json=False, stream=False)
    sent = _catalog_sent(payload)
    assert payload["tools"] and len(payload["tools"]) == len(specs)
    assert _listed(sent, specs) == [] and PROSE_ONLY_START not in sent
    # The model still reads which function is which operation, and how to finish.
    assert all(definition.name in sent for definition in build_cloud_tool_definitions(specs))
    assert CLOSING_RULES_START in sent
    # What was left out is in the native definitions, word for word.
    descriptions = " ".join(tool["function"]["description"] for tool in payload["tools"])
    assert all(str(spec["description"]).strip()[:60] in descriptions for spec in specs)
    assert len(sent) < len(catalog)


def test_a_lane_that_only_reads_prose_keeps_the_whole_list(monkeypatch):
    import adapters.openai_compatible_adapter as oca

    monkeypatch.setattr(oca, "flag_enabled", lambda name: False)
    offer, specs = _offer()
    catalog = _tool_intent_catalog_text(offer=offer)
    payload = _adapter(family="ollama")._build_ollama_payload(
        _request(catalog, build_cloud_tool_definitions(specs)), force_json=False, stream=False)
    assert not payload.get("tools")
    assert _catalog_sent(payload) == catalog


@pytest.mark.parametrize("dialect,native", [("native", True), ("prompted", False)])
def test_the_openai_shaped_lane_follows_whether_it_sends_native_tools(monkeypatch, dialect, native):
    import adapters.openai_compatible_adapter as oca

    monkeypatch.setattr(oca, "flag_enabled", lambda name: True)
    offer, specs = _offer()
    catalog = _tool_intent_catalog_text(offer=offer)
    adapter = _adapter(family="", dialect=dialect)
    payload = adapter._build_openai_payload(
        _request(catalog, build_cloud_tool_definitions(specs)), force_json=False, stream=False)
    sent = _catalog_sent(payload)
    assert bool(payload.get("tools")) is native
    assert (sent == catalog_for_native_wire(catalog)) if native else (sent == catalog)


def test_text_without_a_catalog_is_left_as_it_is():
    text = _RNG.choice(("You are VOOL.", "Without native functions, return JSON", ""))
    assert catalog_for_native_wire(text) == text
