from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runtime.turn_planner_hook import (
    _PLANNER_MAX_OUTPUT_TOKENS,
    build_planner_ask_model,
)
from core.provider_routing import ProviderCapabilityTruth

LOCAL = SimpleNamespace(
    provider_id="ollama-local:qwen3:4b",
    provider_name="ollama-local",
    model_name="qwen3:4b",
    runtime_config={"base_url": "http://127.0.0.1:11434"},
    metadata={"cost_class": "free_local"},
)
FALLBACK = SimpleNamespace(
    provider_id="ollama-local:qwen2.5:7b",
    provider_name="ollama-local",
    model_name="qwen2.5:7b",
    runtime_config={"base_url": "http://127.0.0.1:11434"},
    metadata={"cost_class": "free_local"},
)
PAID = SimpleNamespace(
    provider_id="openrouter-byok:paid/model",
    provider_name="openrouter-byok",
    model_name="paid/model",
    runtime_config={"base_url": "https://openrouter.ai/api/v1"},
    metadata={"cost_class": "paid_cloud"},
)


class _Router:
    def __init__(self, output: str) -> None:
        self.output = output
        self.invocations: list[dict] = []

    def _invoke_manifest(self, **kwargs):
        self.invocations.append(dict(kwargs))
        return None, SimpleNamespace(output_text=self.output), None


class _Agent:
    def __init__(self, output: str) -> None:
        self.memory_router = _Router(output)


@pytest.fixture(autouse=True)
def _candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda *_args, **_kwargs: ([LOCAL, FALLBACK, PAID], "test"),
    )
    monkeypatch.setattr(
        "core.provider_routing.provider_capability_truth_for_manifest",
        lambda manifest: ProviderCapabilityTruth(
            provider_id=manifest.provider_id,
            model_id=manifest.model_name,
            role_fit="drone" if "ollama" in manifest.provider_id else "queen",
            context_window=8192,
            tool_support=(),
            structured_output_support=True,
            tokens_per_second=33.0 if "qwen3" in manifest.model_name else 0.0,
            ram_budget_gb=4.0,
            vram_budget_gb=4.0,
            quantization="q4_K_M",
            locality="local" if "ollama" in manifest.provider_id else "remote",
            privacy_class="local_private" if "ollama" in manifest.provider_id else "remote_provider",
            queue_depth=0,
            max_safe_concurrency=1,
        ),
    )
    monkeypatch.setattr(
        "core.local_inference_evidence.hydrate_capability_truth_with_benchmarks",
        lambda truth: tuple(truth),
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook._planner_default_model_tag",
        lambda: "qwen2.5:7b",
    )


def _valid_plan() -> str:
    return json.dumps(
        [
            {
                "request": "name the company with AAPL",
                "operation": "factual_explanation",
                "depends_on": [],
            },
            {
                "request": "do not look up live stock prices",
                "operation": "constraint",
                "depends_on": [],
            },
        ]
    )


def _source_plan(prompt: str) -> tuple[str, list[str]]:
    from core.turn_ir import parse_turn_ir

    clauses = parse_turn_ir(prompt).clauses
    return json.dumps([
        {"request": "", "source_clause_ids": [clause.clause_id],
         "operation": "quantitative_reasoning", "depends_on": []}
        for clause in clauses
    ]), [clause.request_text for clause in clauses]


def test_set5_02_conductor_decline_and_generic_planner_share_one_helper_call() -> None:
    agent = _Agent(_valid_plan())
    context: dict[str, object] = {"runtime_event_stream_id": "never-copy"}

    conductor = build_planner_ask_model(agent, context)
    generic = build_planner_ask_model(agent, context)

    first = conductor("conductor schema", "AAPL request")
    second = generic("different generic schema", "AAPL request")

    assert first == second == _valid_plan()
    assert len(agent.memory_router.invocations) == 1
    call = agent.memory_router.invocations[0]
    # The planner shares residency with the answer. qwen3's apparent 33 tok/s are reasoning tokens;
    # qwen2.5 is the reliable daily candidate and must load first so answer admission does not fail.
    assert call["manifest"] is FALLBACK
    assert call["output_mode"] == "json_object"
    assert _PLANNER_MAX_OUTPUT_TOKENS <= call["request"].max_output_tokens < 512
    assert call["request"].reasoning_mode == "disabled"
    assert call["request"].allow_response_control_retry is False
    assert call["request"].allow_provider_retry is False
    schema = call["request"].contract["json_schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["requests"]["type"] == "array"
    assert call["request"].metadata["auxiliary_call_cap"] == 1
    assert "runtime_event_stream_id" not in call["source_context"]


@pytest.mark.parametrize("filename", ["portfolio_last_run.txt", "portfolio_owner_format.txt"])
def test_planner_capacity_can_preserve_the_entire_owner_request(filename: str) -> None:
    from core.prompt_budget import estimate_text_tokens

    prompt = (Path(__file__).parent / "fixtures" / filename).read_text()
    reply, source_texts = _source_plan(prompt)
    assert estimate_text_tokens(reply) > 192
    agent = _Agent(reply)
    bound = json.loads(build_planner_ask_model(agent, {})("planner", prompt))
    assert [item["request"] for item in bound] == source_texts
    request = agent.memory_router.invocations[0]["request"]
    assert request.prompt == prompt, "context and assumptions must reach the model unchanged"
    assert request.max_output_tokens >= estimate_text_tokens(reply)
    assert request.allow_provider_retry is False


@pytest.mark.parametrize("limit", [256, 1024])
def test_planner_capacity_obeys_the_selected_provider_limit(monkeypatch, limit: int) -> None:
    monkeypatch.setitem(FALLBACK.runtime_config, "max_output_tokens", limit)
    prompt = (Path(__file__).parent / "fixtures" / "portfolio_last_run.txt").read_text()
    agent = _Agent(_valid_plan())
    build_planner_ask_model(agent, {})("planner", prompt)
    assert agent.memory_router.invocations[0]["request"].max_output_tokens == limit


def test_planner_preserves_new_domain_inputs_and_all_dependent_outputs() -> None:
    from core.prompt_budget import estimate_text_tokens

    prompt = (
        "A factory receives 960 litres of concentrate. Allocate 30% to the north line, "
        "then send 40% of the remainder to the west line and everything left to the south line. "
        "The north line loses 2% in processing, west loses 5%, and south loses 3%. "
        "Calculate each line's initial volume, usable volume and discarded volume. "
        "Then calculate the combined discarded volume and the percentage of the original "
        "delivery this represents. Show the allocations and losses separately in a table, "
        "including the total row. Finally explain why the loss percentages cannot simply "
        "be added together, using the calculated amounts rather than an invented example."
    )
    reply, source_texts = _source_plan(prompt)
    assert estimate_text_tokens(reply) > 192
    agent = _Agent(reply)
    bound = json.loads(build_planner_ask_model(agent, {})("planner", prompt))
    assert [item["request"] for item in bound] == source_texts
    assert agent.memory_router.invocations[0]["request"].prompt == prompt
    assert agent.memory_router.invocations[0]["request"].max_output_tokens >= estimate_text_tokens(reply)


def test_planner_capacity_respects_context_window_share(monkeypatch) -> None:
    monkeypatch.setitem(FALLBACK.runtime_config, "context_window", 512)
    prompt = (Path(__file__).parent / "fixtures" / "portfolio_last_run.txt").read_text()
    agent = _Agent(_valid_plan())
    build_planner_ask_model(agent, {})("planner", prompt)
    assert agent.memory_router.invocations[0]["request"].max_output_tokens == 256


@pytest.mark.parametrize(
    "bad_output",
    [
        "",
        '[{"request":"first","depends_on":[]}',
        "```json\n[]\n```",
        "Here is the plan: []",
        "{}",
        "[]",
    ],
)
def test_invalid_or_truncated_planner_output_is_cached_fail_open_not_retried(
    bad_output: str,
) -> None:
    agent = _Agent(bad_output)
    context: dict[str, object] = {}

    first = build_planner_ask_model(agent, context)
    second = build_planner_ask_model(agent, context)

    assert first("conductor", "prompt") == ""
    assert second("generic", "prompt") == ""
    assert len(agent.memory_router.invocations) == 1


def test_shared_planner_call_cap_is_thread_safe() -> None:
    agent = _Agent(_valid_plan())
    context: dict[str, object] = {}
    callers = [build_planner_ask_model(agent, context) for _ in range(12)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda ask: ask("schema", "multipart request"),
                callers,
            )
        )

    assert results == [_valid_plan()] * 12
    assert len(agent.memory_router.invocations) == 1


def test_failed_first_candidate_does_not_fan_out_to_local_fallback_or_paid_cloud() -> None:
    agent = _Agent(_valid_plan())

    def _fail(**kwargs):
        agent.memory_router.invocations.append(dict(kwargs))
        return None, None, "provider timeout"

    agent.memory_router._invoke_manifest = _fail
    context: dict[str, object] = {}
    ask = build_planner_ask_model(agent, context)

    assert ask("schema", "multipart request") == ""
    assert ask("schema again", "multipart request") == ""
    assert [row["manifest"] for row in agent.memory_router.invocations] == [FALLBACK]


def test_manual_local_pin_is_never_reordered_for_residency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _Agent(_valid_plan())
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda _context: SimpleNamespace(pinned=True),
    )
    ask = build_planner_ask_model(agent, {})

    assert ask("schema", "multipart request") == _valid_plan()
    assert [row["manifest"] for row in agent.memory_router.invocations] == [LOCAL]
