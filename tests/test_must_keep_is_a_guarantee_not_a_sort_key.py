"""`must_keep` must mean the item reaches the prompt, not merely that it sorts first.

Measured 2026-08-01 by building the REAL bootstrap layer (real persona via
`load_active_persona("default")`, real `create_task_record`, real `adapt_user_input`) for an
ordinary chat turn -- "what files are in this project?". The layer builds 14 items, 8 of them
`must_keep` totalling 930 tokens, against a `bootstrap_budget` of 180.

BEFORE: `must_keep` was a sort key only. `core/context_budgeter.budget_layer` ranked on
`(must_keep, priority, confidence)` and then let the `max_items` gate and the token gate treat the
flag like anything else. Four items were admitted -- persona, both safety blocks, the session
memory policy -- and the other four `must_keep` items fell out at `over_budget`:
`bootstrap-self-knowledge` (586 tokens, 3.3x the entire layer), `bootstrap-continuity` (133),
`bootstrap-session` (10) and `bootstrap-task` (23). The model answered an ordinary turn with no
task description at all.

AFTER: `must_keep` items are admitted first, are exempt from `max_items`, and are trimmed rather
than dropped. No single item may take more than half a layer, and the mandatory set is split
max-min fair (smallest first), so a cheap item is never starved by an expensive one above it.
All eight arrive; only the two oversized persona essays are shortened.

These tests are about the MECHANISM, and are written against synthetic items wherever the point
can be made that way, so they still bite if the real bootstrap layer is re-tuned.
`tests/test_the_safety_block_survives_the_bootstrap_budget.py` covers the live layer's outcome.
"""
from __future__ import annotations

import uuid

from core.bootstrap_context import build_bootstrap_context
from core.context_budgeter import budget_layer
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.prompt_assembly_report import ContextItem
from core.task_router import context_strategy, create_task_record

# Read from the shipping config rather than copied, so lowering the budget in task_router shows up
# here as shredded context instead of leaving these tests measuring a number nobody ships.
# `test_the_chat_bootstrap_budget_holds_the_mandatory_set` is the one place that names the value.
_CHAT_STRATEGY = context_strategy("chat_conversation")
LIVE_BOOTSTRAP_BUDGET = int(_CHAT_STRATEGY["bootstrap_budget"])
LIVE_MAX_BOOTSTRAP_ITEMS = int(_CHAT_STRATEGY["max_bootstrap_items"])
EXPECTED_BOOTSTRAP_BUDGET = 380


def _item(item_id: str, tokens: int, *, must_keep: bool, priority: float) -> ContextItem:
    # estimate_tokens is ceil(len / 4), so 4 chars per token is exact.
    return ContextItem(
        item_id=item_id,
        layer="bootstrap",
        source_type="synthetic",
        title=item_id,
        content="x" * (tokens * 4),
        priority=priority,
        confidence=0.9,
        must_keep=must_keep,
    )


def _ordinary_turn_layer() -> list[ContextItem]:
    session_id = f"openclaw:must-keep:{uuid.uuid4().hex}"
    interpretation = adapt_user_input("what files are in this project?", session_id=session_id)
    return build_bootstrap_context(
        persona=load_active_persona("default"),
        task=create_task_record("what files are in this project?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        session_id=session_id,
    )


# --------------------------------------------------------------------------------------------
# 1. must_keep is never dropped
# --------------------------------------------------------------------------------------------


def test_a_must_keep_item_is_not_dropped_by_the_max_items_gate() -> None:
    """Six mandatory items, room for one. All six must arrive; none may be a drop reason."""

    items = [_item(f"required-{n}", 5, must_keep=True, priority=0.9 - n / 100) for n in range(6)]
    resolved = budget_layer(items, token_budget=200, max_items=1)

    assert {item.item_id for item in resolved.included} == {f"required-{n}" for n in range(6)}
    dropped = {item.item_id for item, reason in resolved.excluded if reason == "max_items_exceeded"}
    assert not dropped, f"must_keep items were dropped by the item-count gate: {sorted(dropped)}"


def test_a_must_keep_item_is_trimmed_rather_than_dropped_when_it_will_not_fit() -> None:
    """The exact shape of the live bug: one huge mandatory item ahead of a cheap one."""

    items = [
        _item("essay", 600, must_keep=True, priority=0.97),
        _item("task", 23, must_keep=True, priority=0.92),
    ]
    resolved = budget_layer(items, token_budget=180, max_items=5)

    included = {item.item_id: item for item in resolved.included}
    assert "task" in included, "the cheap mandatory item was starved by the essay above it"
    assert "essay" in included, "the oversized mandatory item was dropped instead of trimmed"
    assert included["essay"].token_count < 600, "the essay was admitted whole and busted the layer"
    assert resolved.used_tokens <= 180, f"the layer overran its budget at {resolved.used_tokens} tokens"


def test_every_must_keep_item_survives_the_real_bootstrap_layer() -> None:
    """All 8 mandatory items on an ordinary turn, against the live budget."""

    items = _ordinary_turn_layer()
    required = {item.item_id for item in items if item.must_keep}
    assert len(required) >= 6, f"the bootstrap layer stopped building mandatory items: {sorted(required)}"

    resolved = budget_layer(items, token_budget=LIVE_BOOTSTRAP_BUDGET, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
    included = {item.item_id for item in resolved.included}
    missing = required - included
    assert not missing, f"must_keep items never reached the prompt: {sorted(missing)}"


def test_the_task_description_reaches_the_prompt_on_an_ordinary_turn() -> None:
    """The user-visible symptom: the model was answering with no description of the task.

    Asserted on content, not just presence -- a `bootstrap-task` trimmed down to an ellipsis
    would satisfy a membership check while still telling the model nothing.
    """

    items = _ordinary_turn_layer()
    built = [item for item in items if item.item_id == "bootstrap-task"]
    assert built, "bootstrap-task is no longer built at all"

    resolved = budget_layer(items, token_budget=LIVE_BOOTSTRAP_BUDGET, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
    admitted = [item for item in resolved.included if item.item_id == "bootstrap-task"]
    assert admitted, "bootstrap-task was dropped; the model runs with no task description"
    assert admitted[0].content == built[0].content, (
        "bootstrap-task arrived trimmed. It costs 23 tokens; if the layer cannot afford that, the "
        "budget is wrong, not the task block."
    )


# --------------------------------------------------------------------------------------------
# 2. no single item may starve the layer
# --------------------------------------------------------------------------------------------


def test_one_oversized_item_may_not_consume_the_whole_layer() -> None:
    """Even alone, a mandatory item is capped at half the layer -- the rest stays spendable."""

    items = [
        _item("essay", 5000, must_keep=True, priority=1.0),
        _item("cheap-optional", 20, must_keep=False, priority=0.5),
    ]
    resolved = budget_layer(items, token_budget=380, max_items=5)

    included = {item.item_id: item for item in resolved.included}
    assert included["essay"].token_count <= 190, (
        f"one item took {included['essay'].token_count} of a 380-token layer; the share cap is half"
    )
    assert "cheap-optional" in included, "the essay starved the optional tail despite the share cap"


def test_a_cheap_mandatory_item_is_not_starved_by_an_expensive_one_ranked_above_it() -> None:
    """Ranking must not decide who eats. The mandatory set is split smallest-first."""

    items = [
        _item("expensive-and-top-ranked", 900, must_keep=True, priority=1.0),
        _item("cheap-and-bottom-ranked", 12, must_keep=True, priority=0.1),
    ]
    resolved = budget_layer(items, token_budget=180, max_items=5)

    included = {item.item_id: item for item in resolved.included}
    assert included["cheap-and-bottom-ranked"].token_count == 12, (
        "the cheap item was trimmed to pay for the expensive one ranked above it"
    )


def test_no_included_bootstrap_item_exceeds_the_layer_it_lives_in() -> None:
    items = _ordinary_turn_layer()
    resolved = budget_layer(items, token_budget=LIVE_BOOTSTRAP_BUDGET, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
    for item in resolved.included:
        assert item.token_count <= LIVE_BOOTSTRAP_BUDGET // 2, (
            f"{item.item_id} took {item.token_count} tokens of a {LIVE_BOOTSTRAP_BUDGET}-token layer"
        )


def test_the_layer_never_overruns_its_token_budget() -> None:
    items = _ordinary_turn_layer()
    for budget in (1, 40, 180, LIVE_BOOTSTRAP_BUDGET, 2000):
        resolved = budget_layer(items, token_budget=budget, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
        assert resolved.used_tokens <= budget, (
            f"the layer spent {resolved.used_tokens} tokens against a budget of {budget}"
        )
        assert sum(item.token_count for item in resolved.included) == resolved.used_tokens


# --------------------------------------------------------------------------------------------
# 3. the budget that makes it work, and the boundaries of the guarantee
# --------------------------------------------------------------------------------------------


def test_the_safety_rule_arrives_whole_and_not_cut_at_its_own_exception() -> None:
    """Why `bootstrap_budget` was raised to 380, stated as the failure it prevents.

    `bootstrap-conversation-safety` is two sentences: the first grants discussion of sensitive
    topics, the second denies that this is permission to act. At a 180-token layer the trim landed
    between them, leaving the model the permission without the limit. Presence is not enough here;
    this asserts the limiting half survives.
    """

    items = _ordinary_turn_layer()
    resolved = budget_layer(items, token_budget=LIVE_BOOTSTRAP_BUDGET, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
    blocks = [item for item in resolved.included if item.item_id == "bootstrap-conversation-safety"]
    assert blocks, "the conversation safety rule was evicted"
    assert "not confuse" in blocks[0].content, (
        "the conversation safety rule arrived truncated to its permissive first sentence: "
        + blocks[0].content
    )


def test_the_chat_bootstrap_budget_holds_the_mandatory_set() -> None:
    """Ties the constant in task_router to the layer it has to pay for.

    If either side moves -- the budget down, or the mandatory content up -- this says so before a
    turn ships with shredded context.
    """

    assert LIVE_BOOTSTRAP_BUDGET == EXPECTED_BOOTSTRAP_BUDGET, (
        f"bootstrap_budget moved to {LIVE_BOOTSTRAP_BUDGET}; re-measure the layer before shipping it"
    )

    resolved = budget_layer(
        _ordinary_turn_layer(),
        token_budget=LIVE_BOOTSTRAP_BUDGET,
        max_items=LIVE_MAX_BOOTSTRAP_ITEMS,
    )
    trimmed = {item.item_id for item, reason in resolved.excluded if reason == "trimmed_to_fit"}
    assert trimmed <= {"bootstrap-self-knowledge", "bootstrap-continuity"}, (
        "the budget now trims mandatory blocks it used to hold whole: " + ", ".join(sorted(trimmed))
    )


def test_raising_the_bootstrap_budget_took_nothing_from_the_relevant_layer() -> None:
    """The raise spends slack the strategy already reserved, not the relevant layer's tokens.

    Every task class declares total >= bootstrap + relevant; before the raise, 200 of those tokens
    were allocated to no layer at all.
    """

    from core.context_budgeter import ContextBudget, normalize_budget

    for task_class in (
        "chat_conversation",
        "shell_guidance",
        "file_inspection",
        "system_design",
        "research",
        "security_hardening",
        "debugging",
    ):
        strategy = context_strategy(task_class)
        budget = normalize_budget(
            ContextBudget(
                total_tokens=int(strategy["total_context_budget"]),
                bootstrap_tokens=int(strategy["bootstrap_budget"]),
                relevant_tokens=int(strategy["relevant_budget"]),
                cold_tokens=int(strategy["cold_budget"]),
            )
        )
        assert budget.relevant_tokens == int(strategy["relevant_budget"]), (
            f"{task_class}: bootstrap at {strategy['bootstrap_budget']} clamped the relevant layer "
            f"from {strategy['relevant_budget']} to {budget.relevant_tokens}"
        )


def test_a_disabled_layer_stays_disabled_even_for_must_keep() -> None:
    """The one boundary of the guarantee. The cold layer ships at `cold_budget: 0`.

    A zero budget means the layer is switched off; must_keep must not switch it back on.
    """

    resolved = budget_layer(
        [_item("required", 5, must_keep=True, priority=1.0)],
        token_budget=0,
        max_items=2,
    )
    assert resolved.included == []
    assert resolved.used_tokens == 0
    assert [reason for _, reason in resolved.excluded] == ["budget_exhausted"]


def test_no_mandatory_item_is_ever_admitted_empty() -> None:
    """The other boundary: more mandatory items than tokens.

    A titled section with no content is worse than an honest drop -- it costs prompt space and
    tells the model nothing. Whatever survives a starved layer must carry text.
    """

    items = [_item(f"required-{n}", 40, must_keep=True, priority=0.9) for n in range(8)]
    for budget in (1, 3, 7, 40):
        resolved = budget_layer(items, token_budget=budget, max_items=5)
        assert resolved.used_tokens <= budget
        for item in resolved.included:
            assert item.content.strip(), f"{item.item_id} was admitted with no content at budget {budget}"


def test_optional_items_still_answer_to_the_max_items_gate() -> None:
    """must_keep bypasses the gate; nothing else does."""

    items = [_item(f"optional-{n}", 4, must_keep=False, priority=0.9 - n / 100) for n in range(9)]
    resolved = budget_layer(items, token_budget=1000, max_items=3)

    assert len(resolved.included) == 3, [item.item_id for item in resolved.included]
    assert sum(1 for _, reason in resolved.excluded if reason == "max_items_exceeded") == 6


def test_max_items_counts_the_optional_tail_and_not_the_mandatory_set() -> None:
    """The gate has to be measured on a MIXED layer, which is the only place it differs.

    The live bootstrap layer carries 8 mandatory items against `max_bootstrap_items: 5`. If the
    mandatory set counted toward the gate it would be exceeded before any optional item was
    considered, and the constant would silently mean "never admit optional bootstrap context"
    instead of "admit at most five".
    """

    items = [_item(f"required-{n}", 4, must_keep=True, priority=0.99) for n in range(8)]
    items += [_item(f"optional-{n}", 4, must_keep=False, priority=0.5) for n in range(4)]
    resolved = budget_layer(items, token_budget=1000, max_items=3)

    included = {item.item_id for item in resolved.included}
    assert len([i for i in included if i.startswith("required-")]) == 8, "the gate ate mandatory items"
    admitted_optional = sorted(i for i in included if i.startswith("optional-"))
    assert len(admitted_optional) == 3, (
        f"expected the gate to admit 3 optional items past the 8 mandatory ones, got {admitted_optional}"
    )
