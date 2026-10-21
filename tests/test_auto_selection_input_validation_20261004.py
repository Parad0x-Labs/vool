"""Selection input contracts: the token-count domain and the prompted tool dialect.

Token counts. Both selection entry points that accept a declared size read it through one
domain: the live ranking call (``rank_provider_candidates(min_context_tokens=)``) and the
envelope requirements (``model_constraints.min_context_tokens`` / ``expected_output_tokens``).
Absent (missing, None, blank) and 0 mean "no size supplied". A usable count is a non-negative
whole number no larger than ``MAX_TOKEN_COUNT``, given as an int, a finite float (a fraction is
rounded UP, never truncated, so a requirement is never weakened) or a string of ASCII decimal
digits. Anything else (booleans, negatives, non-finite floats, separators, words, non-ASCII
digits, oversized values, containers) is malformed: no candidate can be verified against it, so
selection fails closed instead of reading it as "no requirement" or raising a conversion error.

Measured before this repair, on the imported candidate: the live channel turned ``-5``,
``False``, ``[]`` and ``{}`` into "no requirement" and truncated ``4096.5`` to admit a
4096-window model; a non-empty container raised ``TypeError``. The envelope parser raised
``ValueError`` for ``"²"`` (``str.isdigit`` admits it, ``int`` cannot read it) and for a
5000-digit string, and read Arabic-Indic digits as a count.

Prompted tool dialect. A manifest that declares ``tool_dialect: prompted`` receives no native
``tools`` on the wire; on a tools-required turn its only legitimate carrier is the
structured-output fallback (``response_format``), which the adapter builds only for an endpoint
that declares JSON mode or JSON schema. The final envelope guard refuses a call carrying neither,
so selection must not offer that lane a tool step. Native tools without JSON mode stay eligible.
"""
from __future__ import annotations

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import provider_routing
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.execution_requirements import RequiredToolsNotOfferedError
from core.model_failover import select_with_failover
from core.model_health import reset_provider_health
from core.model_output_contracts import json_schema_for_mode
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, explain_provider_exclusions
from core.orchestration.task_envelope import TaskEnvelopeV1
from core.provider_routing import rank_provider_candidates, resolve_provider_routing_plan_for_envelope
from storage.db import get_connection
from storage.model_provider_manifest import ModelProviderManifest
from tests._blackbox_served_rig import ScriptedProvider

# The domain's upper bound: far above any declared context window, small enough that a decimal
# string of it never approaches the interpreter's integer-conversion limit.
MAX_TOKEN_COUNT = 1_000_000_000

CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "tool_intent"]

# Malformed values: the reviewer's original shapes plus materially different ones.
MALFORMED = [
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(-5, id="negative-int"),
    pytest.param(-0.5, id="negative-float"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param(float("-inf"), id="neg-inf"),
    pytest.param("12,000", id="thousands-separator"),
    pytest.param("twelve thousand", id="words"),
    pytest.param("-5", id="negative-string"),
    pytest.param("+12000", id="signed-string"),
    pytest.param("1e4", id="exponent-string"),
    pytest.param("12000.5", id="fraction-string"),
    pytest.param("²", id="superscript-two"),
    pytest.param("١٢٠٠٠", id="arabic-indic-digits"),
    pytest.param("9" * 5000, id="5000-digit-string"),
    pytest.param(10**30, id="oversized-int"),
    pytest.param(MAX_TOKEN_COUNT + 1, id="bound-plus-one"),
    pytest.param(float(MAX_TOKEN_COUNT) * 10, id="oversized-float"),
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty-dict"),
    pytest.param((), id="empty-tuple"),
    pytest.param([12000], id="list-of-count"),
    pytest.param({"tokens": 12000}, id="dict-of-count"),
    pytest.param(b"12000", id="bytes"),
    pytest.param(object(), id="object"),
]


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _local(provider: str, model: str, window: int | None, capabilities: list[str] = CAPS) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="subprocess",
        license_name="MIT",
        license_reference="https://example.com/license",
        runtime_dependency="ollama",
        capabilities=capabilities,
        metadata={"context_window": window} if window else {},
    )


def _registry(*manifests: ModelProviderManifest) -> ModelRegistry:
    _clear_manifests()
    reset_provider_health()
    registry = ModelRegistry()
    for manifest in manifests:
        registry.register_manifest(manifest)
    return registry


def _window_registry() -> ModelRegistry:
    # Equal scores tie-break on provider name, descending: small, then unknown, then wide.
    return _registry(
        _local("ccc-local", "smallwin:8b", 4096),
        _local("bbb-local", "unknownwin:8b", None),
        _local("aaa-local", "widewin:8b", 32768),
    )


def _live(registry: ModelRegistry, **kwargs) -> list[str]:
    ranked = rank_provider_candidates(
        registry, task_kind="normalization_assist", output_mode="plain_text", swarm_size=4, **kwargs
    )
    return [manifest.model_name for manifest in ranked]


def _envelope(task_id: str, **constraints) -> TaskEnvelopeV1:
    return TaskEnvelopeV1(
        task_id=task_id,
        parent_task_id="",
        role="researcher",
        goal="Summarize the attached report",
        model_constraints=dict(constraints),
    )


def _plan(registry: ModelRegistry, **constraints):
    return resolve_provider_routing_plan_for_envelope(
        registry,
        envelope=_envelope("t-input-domain", **constraints),
        task_kind="summarization",
        output_mode="plain_text",
        swarm_size=4,
    )


# --------------------------------------------------------------------------------------------
# The domain itself
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0),
        ("", 0),
        ("   ", 0),
        (0, 0),
        (0.0, 0),
        ("0", 0),
        (1, 1),
        (12000, 12000),
        (12000.0, 12000),
        (4096.5, 4097),
        (0.01, 1),
        ("12000", 12000),
        (" 12000 ", 12000),
        ("0012000", 12000),
        (MAX_TOKEN_COUNT, MAX_TOKEN_COUNT),
        (str(MAX_TOKEN_COUNT), MAX_TOKEN_COUNT),
    ],
)
def test_usable_counts_parse_and_fractions_round_up(value, expected) -> None:
    assert provider_routing.parse_token_count(value) == expected


@pytest.mark.parametrize("value", MALFORMED)
def test_malformed_counts_are_rejected_without_raising(value) -> None:
    assert provider_routing.parse_token_count(value) is None


def test_the_owner_publishes_the_same_bound() -> None:
    assert provider_routing.MAX_TOKEN_COUNT == MAX_TOKEN_COUNT


# --------------------------------------------------------------------------------------------
# Live ranking channel
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("absent", [None, "", 0, 0.0, "0"])
def test_live_absent_or_zero_size_leaves_the_ranking_unchanged(absent) -> None:
    registry = _window_registry()
    assert _live(registry, min_context_tokens=absent) == ["smallwin:8b", "unknownwin:8b", "widewin:8b"]


def test_live_exact_window_boundary_is_inclusive() -> None:
    registry = _window_registry()
    assert _live(registry, min_context_tokens=4096) == ["smallwin:8b", "widewin:8b", "unknownwin:8b"]
    assert _live(registry, min_context_tokens=4097) == ["widewin:8b", "unknownwin:8b"]


def test_live_fractional_size_is_never_truncated_below_the_requirement() -> None:
    registry = _window_registry()
    # int(4096.5) == 4096 would admit the 4096-window model for a call that does not fit in it.
    assert _live(registry, min_context_tokens=4096.5) == ["widewin:8b", "unknownwin:8b"]


@pytest.mark.parametrize("spelled", ["12000", " 12000 ", 12000.0])
def test_live_other_valid_spellings_match_the_integer(spelled) -> None:
    registry = _window_registry()
    assert _live(registry, min_context_tokens=spelled) == _live(registry, min_context_tokens=12000)
    assert _live(registry, min_context_tokens=12000) == ["widewin:8b", "unknownwin:8b"]


def test_live_maximum_count_is_valid_and_keeps_only_unknown_windows() -> None:
    registry = _window_registry()
    assert _live(registry, min_context_tokens=MAX_TOKEN_COUNT) == ["unknownwin:8b"]


@pytest.mark.parametrize("value", MALFORMED)
def test_live_malformed_size_fails_closed_without_raising(value) -> None:
    registry = _window_registry()
    assert _live(registry, min_context_tokens=value) == []


@pytest.mark.parametrize("value", [-5, False, [], {"tokens": 12000}, "²"])
def test_live_malformed_size_fails_closed_after_role_rescoring(value) -> None:
    registry = _window_registry()
    ranked = rank_provider_candidates(
        registry,
        task_kind="tool_intent",
        output_mode="tool_intent",
        role="drone",
        swarm_size=4,
        min_context_tokens=value,
    )
    assert ranked == []


# --------------------------------------------------------------------------------------------
# Envelope requirements: same domain, malformed names itself and rejects every candidate
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["min_context_tokens", "expected_output_tokens"])
@pytest.mark.parametrize("value", MALFORMED)
def test_envelope_malformed_count_rejects_every_candidate_and_names_the_field(field, value) -> None:
    registry = _window_registry()

    plan = _plan(registry, **{field: value})

    assert plan.selected is None
    assert plan.candidates == ()
    assert {row["reason"] for row in plan.rejected_candidates} == {"malformed_requirement"}
    assert len(plan.rejected_candidates) == 3
    assert field in plan.routing_requirements["malformed_constraints"]
    assert any(field in note and "failed closed" in note for note in plan.selection_notes)


@pytest.mark.parametrize("absent", [None, "", 0, "0"])
def test_envelope_absent_or_zero_count_is_no_requirement(absent) -> None:
    registry = _window_registry()

    plan = _plan(registry, min_context_tokens=absent, expected_output_tokens=absent)

    assert plan.routing_requirements["malformed_constraints"] == ()
    assert plan.routing_requirements["required_window_tokens"] == 0
    assert {m.model_name for m in plan.candidates} == {"smallwin:8b", "unknownwin:8b", "widewin:8b"}


def test_envelope_window_boundary_counts_payload_plus_completion() -> None:
    registry = _window_registry()

    fits = _plan(registry, min_context_tokens="4000", expected_output_tokens=96)
    overflows = _plan(registry, min_context_tokens=4000, expected_output_tokens=96.2)

    assert fits.routing_requirements["required_window_tokens"] == 4096
    assert "smallwin:8b" in {m.model_name for m in fits.candidates}
    assert overflows.routing_requirements["required_window_tokens"] == 4097
    assert "smallwin:8b" not in {m.model_name for m in overflows.candidates}
    assert {"provider_id": "ccc-local:smallwin:8b", "reason": "context_window_too_small"} in overflows.rejected_candidates


def test_envelope_and_live_channel_read_the_same_count() -> None:
    registry = _window_registry()
    for value in (4096, 4096.5, "12000", MAX_TOKEN_COUNT):
        plan = _plan(registry, min_context_tokens=value)
        assert plan.routing_requirements["min_context_tokens"] == provider_routing.parse_token_count(value)
        assert {m.model_name for m in plan.candidates} == set(_live(registry, min_context_tokens=value))


# --------------------------------------------------------------------------------------------
# Prompted tool dialect: selection agrees with what the endpoint can actually carry
# --------------------------------------------------------------------------------------------

TOOLS = build_cloud_tool_definitions(
    [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
)


def _endpoint(provider: str, model: str, base_url: str, **runtime) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.com/license",
        weight_location="external",
        runtime_dependency="openai-compatible-server",
        capabilities=CAPS,
        runtime_config={"base_url": base_url, "api_path": "/v1/chat/completions", "timeout_seconds": 10, **runtime},
        metadata={"runtime_family": "openai-compatible", "cost_class": "free_local"},
    )


def _tool_request() -> ModelRequest:
    # The router's tool-step request shape: tool_intent output, native tool definitions, the
    # contract schema for the mode and the turn's tools-required flag.
    return ModelRequest(
        task_kind="tool_intent",
        prompt="list the files in the project folder",
        messages=[{"role": "user", "content": "list the files in the project folder"}],
        output_mode="tool_intent",
        contract={"json_schema": json_schema_for_mode("tool_intent")},
        tools=TOOLS,
        tool_choice="required",
        tools_required=True,
    )


def _tool_rank(registry: ModelRegistry, **request) -> list[str]:
    return [
        m.model_name
        for m in registry.rank_manifests(ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent", **request))
    ]


def test_prompted_dialect_without_structured_output_is_refused_at_the_wire() -> None:
    """Deciding receipt for the endpoint contract: the real adapter builds the real payload and
    the final guard refuses it before any byte reaches the scripted endpoint."""
    with ScriptedProvider({}, default='{"intent": "sandbox.run_command", "arguments": {"command": "ls"}}') as provider:
        manifest = _endpoint("prompt-local", "promptish:8b", provider.base_url, tool_dialect="prompted")
        adapter = OpenAICompatibleAdapter(manifest)

        with pytest.raises(RequiredToolsNotOfferedError, match="neither native tools nor"):
            adapter.invoke(_tool_request())

        assert provider.calls == []


def test_prompted_dialect_without_structured_output_is_not_eligible_for_a_tool_step() -> None:
    with ScriptedProvider({}) as provider:
        registry = _registry(
            _endpoint("prompt-local", "promptish:8b", provider.base_url, tool_dialect="prompted"),
        )
        live = rank_provider_candidates(
            registry, task_kind="tool_intent", output_mode="tool_intent", role="drone", swarm_size=4, min_trust=0.45
        )
        assert _tool_rank(registry) == []
        assert live == []
        # The same lane still serves a plain chat turn: only the tool contract is unreachable.
        assert [
            m.model_name
            for m in registry.rank_manifests(ModelSelectionRequest(task_kind="normalization_assist", output_mode="plain_text"))
        ] == ["promptish:8b"]


def test_prompted_exclusion_receipt_names_the_missing_carrier() -> None:
    manifest = _endpoint("prompt-local", "promptish:8b", "http://127.0.0.1:9", tool_dialect="prompted")
    rows = explain_provider_exclusions([manifest], ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"))
    assert rows[0]["status"] == "prompted_tool_dialect_without_structured_output"


def test_a_pin_or_failover_never_reaches_the_prompted_lane_without_a_carrier() -> None:
    with ScriptedProvider({}) as provider:
        prompted = _endpoint("prompt-local", "promptish:8b", provider.base_url, tool_dialect="prompted")
        native = _endpoint("native-local", "nativeish:8b", provider.base_url, tool_dialect="native")
        registry = _registry(prompted, native)

        pinned = _tool_rank(registry, preferred_provider="prompt-local", preferred_model="promptish:8b")
        decision = select_with_failover(
            registry,
            ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"),
            attempted_provider_ids=["native-local:nativeish:8b"],
        )

        assert "promptish:8b" not in pinned
        assert decision.selected is None


@pytest.mark.parametrize(
    ("runtime", "carrier"),
    [
        pytest.param({"tool_dialect": "native"}, "tools", id="native-without-json-mode"),
        pytest.param({"tool_dialect": "prompted", "supports_json_mode": True}, "response_format", id="prompted-json-mode"),
        pytest.param({"tool_dialect": "prompted", "supports_json_schema": True}, "response_format", id="prompted-json-schema"),
    ],
)
def test_supported_tool_carriers_are_eligible_and_reach_the_wire(runtime, carrier) -> None:
    reply = '{"intent": "sandbox.run_command", "arguments": {"command": "ls"}}'
    if carrier == "tools":
        reply = {"function": {"name": TOOLS[0].name, "arguments": {"command": "ls"}}}
    with ScriptedProvider({}, default=reply) as provider:
        manifest = _endpoint("carrier-local", "carrier:8b", provider.base_url, **runtime)
        registry = _registry(manifest)

        assert _tool_rank(registry) == ["carrier:8b"]

        adapter = OpenAICompatibleAdapter(manifest)
        payload = adapter._build_openai_payload(_tool_request(), force_json=True, stream=False)
        assert payload.get(carrier)
        adapter.invoke(_tool_request())
        assert len(provider.calls) == 1
        assert provider.calls[0]["model"] == "carrier:8b"
        if carrier == "tools":
            assert provider.calls[0]["tools"] == [TOOLS[0].name]


def test_undeclared_dialect_keeps_the_adapter_as_the_deciding_owner() -> None:
    # No declared dialect: whether native tools are sent is decided per call by the adapter
    # (catalog facts, loopback probe). Selection does not guess; the final guard stays the
    # backstop for a call that ends up carrying neither.
    manifest = _endpoint("plain-endpoint", "undeclared:8b", "http://127.0.0.1:9")
    registry = _registry(manifest)
    assert _tool_rank(registry) == ["undeclared:8b"]
