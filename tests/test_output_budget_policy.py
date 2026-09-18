"""Guards on the per-lane output budget policy and the intent the prompt layer carries into it.

Incident these exist for: `_max_output_tokens` in core/prompt_normalizer.py took ONE argument, the
output mode, so every lane got the same table -- 240 tokens for plain_text, 700 for tool_intent.
Measured 2026-07-28 against `nvidia/nemotron-3-ultra-550b-a55b:free`, plain_text turns went out at
`max_tokens` 284 ("Hey"), 440 and 356; the model spent all of them reasoning and returned empty
`content`, which reached the user as "I couldn't get a live model response". On a tool turn against
`nvidia/nemotron-3-nano-30b-a3b:free`, 512 tokens ended `finish_reason: "length"` with no call while
3000 returned the call.

`_max_output_tokens` calls `resolve_output_budget` whenever it is handed a `LaneCapability`; every
caller passes None today, so these tests also pin that the default budget table is untouched --
`_max_output_tokens` with no capability must still return exactly what
tests/test_generation_settings_profiles.py:152-181 pins.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from core.output_budget_policy import (
    LaneCapability,
    OutputBudgetIntent,
    ResolvedOutputBudget,
    resolve_output_budget,
)
from core.prompt_normalizer import _generation_profile, _max_output_tokens, normalize_prompt


def _chat_intent(base: int = 240, *, floor: int = 220, ceiling: int = 520) -> OutputBudgetIntent:
    return OutputBudgetIntent(
        output_mode="plain_text",
        base_tokens=base,
        floor=floor,
        ceiling=ceiling,
        reason="adaptive_chat_length",
    )


def _tool_intent(base: int = 700) -> OutputBudgetIntent:
    return OutputBudgetIntent(
        output_mode="tool_intent",
        base_tokens=base,
        floor=base,
        ceiling=0,
        reason="fixed_mode_table:tool_intent",
    )


def test_a_free_local_lane_keeps_the_budget_the_prompt_layer_asked_for() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=8192, cost_class="free_local", runtime_family="ollama"),
    )

    assert resolved.tokens == 240
    assert resolved.source == "intent_base"
    assert resolved.capped_by == "none"
    assert resolved.intent_base == 240


def test_an_unpriced_remote_lane_is_never_inflated_above_the_asked_budget() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=32000, cost_class="remote_unknown"),
    )

    assert resolved.tokens == 240
    assert resolved.source == "intent_base"


def test_a_cost_class_the_policy_does_not_recognise_is_treated_as_unpriced() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=32000, cost_class="enterprise_committed_spend"),
    )

    assert resolved.tokens == 240
    assert resolved.source == "intent_base"


def test_a_free_cloud_chat_turn_is_lifted_off_the_local_sized_table() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved.tokens > 284, "284 is the budget that produced the empty 550B reply"
    assert resolved.source == "free_cloud_target"


def test_a_free_cloud_tool_turn_reaches_the_measured_three_thousand_token_floor() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved.tokens == 3000
    assert resolved.source == "free_cloud_target"
    assert resolved.capped_by == "none"


def test_a_paid_tool_turn_gets_the_same_measured_floor_because_truncation_bills_the_full_input() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=128000, cost_class="paid_cloud"),
    )

    assert resolved.tokens == 3000
    assert resolved.source == "paid_cloud_target"


def test_a_paid_chat_turn_is_lifted_less_than_the_free_one() -> None:
    free = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=240),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )
    paid = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=240),
        LaneCapability(context_window=128000, cost_class="paid_cloud"),
    )

    assert 240 < paid.tokens < free.tokens


def test_a_lift_never_lowers_a_budget_the_prompt_layer_asked_higher_for() -> None:
    resolved = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=4000),
        LaneCapability(context_window=128000, cost_class="paid_cloud"),
    )

    assert resolved.tokens == 4000
    assert resolved.source == "intent_base"


def test_an_intent_ceiling_holds_an_exact_answer_turn_to_its_own_length() -> None:
    resolved = resolve_output_budget(
        OutputBudgetIntent(
            output_mode="plain_text",
            base_tokens=32,
            floor=32,
            ceiling=32,
            reason="exact_output_target",
        ),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved.tokens == 32
    assert resolved.capped_by == "intent_ceiling"


def test_an_intent_floor_raises_a_budget_no_cost_class_lift_reached() -> None:
    resolved = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=90, floor=220),
        LaneCapability(context_window=8192, cost_class="free_local"),
    )

    assert resolved.tokens == 220
    assert resolved.source == "intent_floor"
    assert resolved.intent_base == 90


def test_a_provider_declared_output_limit_binds_and_names_itself() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=128000, max_output_tokens=1024, cost_class="free_cloud"),
    )

    assert resolved.tokens == 1024
    assert resolved.capped_by == "capability_max_output_tokens"


def test_an_unknown_provider_output_limit_never_reads_as_zero_tokens_allowed() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=128000, max_output_tokens=0, cost_class="free_cloud"),
    )

    assert resolved.tokens == 3000


def test_output_never_takes_more_than_half_the_window_it_shares_with_the_prompt() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=4096, cost_class="free_cloud"),
    )

    assert resolved.tokens == 2048
    assert resolved.capped_by == "context_window_share"


def test_an_unknown_context_window_applies_no_window_cap() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=0, cost_class="free_cloud"),
    )

    assert resolved.tokens == 3000
    assert resolved.capped_by == "none"


def test_a_reasoning_lane_is_paid_for_its_thinking_on_top_of_the_answer_budget() -> None:
    answer_only = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )
    with_reasoning = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=128000, cost_class="free_cloud", thinking_capable=True),
    )

    assert with_reasoning.reserve_applied == 2048
    assert with_reasoning.tokens == answer_only.tokens + 2048


def test_a_lane_that_does_not_reason_is_given_no_reserve() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=128000, cost_class="free_cloud", thinking_capable=False),
    )

    assert resolved.reserve_applied == 0


def test_the_reasoning_reserve_is_added_once_not_once_per_cap() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(
            context_window=128000,
            max_output_tokens=64000,
            cost_class="free_cloud",
            thinking_capable=True,
        ),
    )

    assert resolved.reserve_applied == 2048


def test_a_lane_that_reserves_for_itself_does_not_take_the_reserve_twice() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(
            context_window=8192,
            cost_class="free_local",
            thinking_capable=True,
            runtime_family="ollama",
        ),
    )

    assert resolved.reserve_applied == 0, "the Ollama lane adds its own measured reserve downstream"
    assert resolved.tokens == 240


def test_the_reasoning_reserve_is_trimmed_to_the_room_the_lane_actually_has() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(
            context_window=128000,
            max_output_tokens=700,
            cost_class="free_cloud",
            thinking_capable=True,
        ),
    )

    assert resolved.tokens == 700
    assert resolved.reserve_applied == 180
    assert resolved.capped_by == "capability_max_output_tokens"


def test_a_budget_the_caller_left_unset_stays_unset_rather_than_gaining_a_ceiling() -> None:
    resolved = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=0),
        LaneCapability(context_window=128000, cost_class="free_cloud", thinking_capable=True),
    )

    assert resolved.tokens == 0
    assert resolved.reserve_applied == 0


def test_a_negative_budget_is_read_as_unset_rather_than_as_a_negative_ceiling() -> None:
    resolved = resolve_output_budget(
        OutputBudgetIntent(output_mode="plain_text", base_tokens=-40),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved.tokens == 0


def test_every_resolved_budget_reports_the_base_it_started_from() -> None:
    resolved = resolve_output_budget(
        _chat_intent(base=317),
        LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved.intent_base == 317


@pytest.mark.parametrize("cls", [OutputBudgetIntent, LaneCapability, ResolvedOutputBudget])
def test_the_policy_records_are_frozen_so_a_resolved_budget_cannot_drift(cls: type) -> None:
    assert cls.__dataclass_params__.frozen is True


def test_the_policy_module_imports_without_the_router_or_any_adapter() -> None:
    probe = (
        "import sys; import core.output_budget_policy; "
        "print([m for m in sys.modules if m.startswith('adapters') "
        "or m.startswith('core.model_router') or m.startswith('storage')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]", result.stdout


@pytest.mark.parametrize(
    ("output_mode", "expected"),
    [
        ("plain_text", 240),
        ("summary_block", 220),
        ("json_object", 220),
        ("action_plan", 320),
        ("tool_intent", 700),
        ("unknown_mode", 240),
    ],
)
def test_the_default_budget_table_is_unchanged_when_no_lane_is_known(output_mode: str, expected: int) -> None:
    assert _max_output_tokens(output_mode) == expected


def test_naming_a_lane_resolves_the_mode_budget_through_the_policy() -> None:
    resolved = _max_output_tokens(
        "tool_intent",
        capability=LaneCapability(context_window=128000, cost_class="free_cloud"),
    )

    assert resolved == 3000
    assert _max_output_tokens("tool_intent") == 700, "the default path must not move"


@pytest.mark.parametrize(
    ("output_mode", "task_kind", "expected_base"),
    [
        ("plain_text", "conversation", 240),
        ("summary_block", "summarization", 220),
        ("action_plan", "action_plan", 320),
        ("tool_intent", "tool_intent", 700),
    ],
)
def test_a_generation_profile_carries_the_budget_it_requested_as_an_intent(
    output_mode: str,
    task_kind: str,
    expected_base: int,
) -> None:
    profile = _generation_profile(
        surface="cli",
        task_kind=task_kind,
        output_mode=output_mode,
        task_class="system_design",
        user_text="Work out the next step.",
    )

    intent = profile["output_budget_intent"]

    assert profile["max_output_tokens"] == expected_base
    assert intent["base"] == expected_base
    assert intent["output_mode"] == output_mode
    assert intent["reason"] == f"fixed_mode_table:{output_mode}"


def test_an_adaptive_chat_profile_carries_the_length_band_it_clamps_itself_to() -> None:
    profile = _generation_profile(
        surface="openclaw",
        task_kind="conversation",
        output_mode="plain_text",
        task_class="chat_conversation",
        user_text="Is boredom useful, or a sign I need better constraints?",
    )

    intent = profile["output_budget_intent"]

    assert profile["profile_id"] == "chat_plain_text"
    assert intent["base"] == profile["max_output_tokens"]
    assert (intent["floor"], intent["ceiling"]) == (220, 520)
    assert intent["reason"] == "adaptive_chat_length"


def test_an_exact_answer_profile_carries_a_ceiling_equal_to_its_own_length() -> None:
    profile = _generation_profile(
        surface="openclaw",
        task_kind="conversation",
        output_mode="plain_text",
        task_class="chat_conversation",
        user_text="Reply with exactly GREENLOOP-WARMUP-4 and nothing else.",
    )

    intent = profile["output_budget_intent"]

    assert profile["profile_id"] == "chat_exact_plain_text"
    assert intent["ceiling"] == profile["max_output_tokens"]
    assert intent["reason"] == "exact_output_target"


def _context_result() -> SimpleNamespace:
    return SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.3,
        assembled_context=lambda *_args, **_kwargs: "",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=0.3,
            total_tokens_used=lambda: 10,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )


def _normalized(prompt: str, *, task_class: str) -> SimpleNamespace:
    return normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary=prompt),
        classification={"task_class": task_class, "risk_flags": []},
        interpretation=SimpleNamespace(
            reconstructed_text=prompt,
            topic_hints=[],
            understanding_confidence=0.8,
        ),
        context_result=_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="trace-1",
        surface="openclaw",
        source_context={"surface": "openclaw", "platform": "openclaw", "conversation_history": []},
    )


def test_a_normalized_turn_carries_the_intent_through_request_metadata() -> None:
    request = _normalized("What should I do about the flaky deploy?", task_class="chat_conversation")

    profile = dict(request.metadata.get("generation_profile") or {})
    intent = dict(profile.get("output_budget_intent") or {})

    assert intent["base"] == profile["max_output_tokens"] == request.max_output_tokens


def test_a_creative_turn_carries_the_raised_budget_and_not_the_chat_band_it_outgrew() -> None:
    request = _normalized(
        "Write a short story about a lighthouse keeper who stops writing the log.",
        task_class="creative_ideation",
    )

    profile = dict(request.metadata.get("generation_profile") or {})
    intent = dict(profile.get("output_budget_intent") or {})

    assert profile["max_output_tokens"] >= 1800
    assert intent["base"] == profile["max_output_tokens"]
    assert intent["ceiling"] == 0, "a 520-token chat band must not clamp a creative answer"


def test_a_lane_with_room_for_a_tool_call_reports_no_shortfall() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=32000, cost_class="free_cloud"),
    )

    assert resolved.tokens == 3000
    assert resolved.tool_call_floor_shortfall == 0


def test_a_small_context_window_cuts_a_tool_budget_under_the_floor_and_says_so() -> None:
    """The 4k lane. `context_window // 2` is 2048, under the 3000 that produced the call.

    The cap itself is correct and stays -- taking more of the window fails the turn closed in
    core/prompt_budget.py. What must not happen is the caller spending the input and reading the
    empty `tool_calls` as the model declining.
    """

    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=4096, cost_class="free_cloud"),
    )

    assert resolved.tokens == 2048
    assert resolved.capped_by == "context_window_share"
    assert resolved.tool_call_floor_shortfall == 952


def test_a_provider_declared_output_cap_under_the_floor_is_reported() -> None:
    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=32000, max_output_tokens=1024, cost_class="paid_cloud"),
    )

    assert resolved.tokens == 1024
    assert resolved.capped_by == "capability_max_output_tokens"
    assert resolved.tool_call_floor_shortfall == 1976


def test_a_local_lane_that_is_never_lifted_still_reports_the_tool_call_gap() -> None:
    """free_local is deliberately not lifted -- `_cost_class_target` returns 0 for it.

    That is a spend decision, not evidence the 700-token table reaches a call. The gap is reported
    so choosing this lane for a tool turn is a decision someone made rather than one that happened.
    """

    resolved = resolve_output_budget(
        _tool_intent(),
        LaneCapability(context_window=32000, cost_class="free_local", runtime_family="ollama"),
    )

    assert resolved.tokens == 700
    assert resolved.capped_by == "none"
    assert resolved.tool_call_floor_shortfall == 2300


def test_a_chat_turn_never_carries_a_tool_call_shortfall() -> None:
    resolved = resolve_output_budget(
        _chat_intent(),
        LaneCapability(context_window=4096, cost_class="free_cloud"),
    )

    assert resolved.tool_call_floor_shortfall == 0
