"""The safety block must reach the prompt on an ordinary turn.

Measured 2026-07-31 by building the REAL bootstrap layer (real persona via
`load_active_persona("default")`, real `create_task_record`, real `adapt_user_input`) for an
ordinary chat turn: 14 items, 8 of them `must_keep` totalling 930 tokens, against a
`bootstrap_budget` of 180 and `max_items` 5. Three survived. `bootstrap-safety`,
`bootstrap-conversation-safety`, `bootstrap-task`, `bootstrap-continuity` and `bootstrap-session`
were all dropped at `budget_exhausted` -- every one of them marked `must_keep=True`.

The budget was not the culprit; admission order was. Sorted by `(must_keep, priority, confidence)`
the third item admitted was `bootstrap-self-knowledge` at priority 0.97 and **586 tokens** -- 3.3x
the entire layer -- which consumed all of it alone, while `bootstrap-safety` costs 23 tokens,
ranked last at 0.88, and got nothing. A persona essay was evicting the safety policy.

Ranking the two safety blocks to the top fits both inside the UNCHANGED 180-token budget. This
test pins that outcome rather than the priority numbers, so a later re-tune is free to move the
constants as long as safety still arrives.

STILL OPEN and deliberately not asserted here: `must_keep` remains a sort key only
(`core/context_budgeter.py:68`) -- the `max_items` and token gates treat it like anything else, so
the flag still names a guarantee the code does not provide. `bootstrap-task` also still does not
fit. Both need a ranking design decision from the lane owner.
"""
from __future__ import annotations

import uuid

from core.bootstrap_context import build_bootstrap_context
from core.context_budgeter import budget_layer
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.task_router import create_task_record

# The live values in core/task_router.py. Pinned here so the test measures the real shipping
# condition rather than a comfortable one.
LIVE_BOOTSTRAP_BUDGET = 180
LIVE_MAX_BOOTSTRAP_ITEMS = 5


def _ordinary_turn_layer():
    session_id = f"openclaw:safety-budget:{uuid.uuid4().hex}"
    interpretation = adapt_user_input("what files are in this project?", session_id=session_id)
    return build_bootstrap_context(
        persona=load_active_persona("default"),
        task=create_task_record("what files are in this project?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        session_id=session_id,
    )


def test_both_safety_blocks_reach_the_prompt_on_an_ordinary_turn() -> None:
    resolved = budget_layer(
        _ordinary_turn_layer(),
        token_budget=LIVE_BOOTSTRAP_BUDGET,
        max_items=LIVE_MAX_BOOTSTRAP_ITEMS,
    )
    included = {item.item_id for item in resolved.included}

    assert "bootstrap-safety" in included, (
        "the safety policy was evicted from an ordinary turn; included instead: " + ", ".join(sorted(included))
    )
    assert "bootstrap-conversation-safety" in included, (
        "the rule separating discussion from action was evicted; included instead: "
        + ", ".join(sorted(included))
    )


def test_no_single_bootstrap_item_may_consume_the_whole_layer() -> None:
    """The mechanism behind the failure, stated independently of which item it was.

    `bootstrap-self-knowledge` was 586 tokens against a 180-token layer. Any must_keep item that
    large will starve everything ranked below it, whatever the ranking is.
    """

    items = _ordinary_turn_layer()
    resolved = budget_layer(items, token_budget=LIVE_BOOTSTRAP_BUDGET, max_items=LIVE_MAX_BOOTSTRAP_ITEMS)
    for item in resolved.included:
        assert item.token_count <= LIVE_BOOTSTRAP_BUDGET, (
            f"{item.item_id} alone is {item.token_count} tokens against a {LIVE_BOOTSTRAP_BUDGET}-token layer"
        )


def test_the_safety_block_is_still_cheap() -> None:
    """It survives because it is small and ranked, not because the budget grew.

    If the safety content ever balloons, the fix above stops being free and this says so.
    """

    safety = [i for i in _ordinary_turn_layer() if i.item_id == "bootstrap-safety"]
    assert safety, "bootstrap-safety is no longer built at all"
    assert safety[0].token_count <= 60, (
        f"bootstrap-safety grew to {safety[0].token_count} tokens; it fit in the unchanged 180-token "
        "layer at 23. Re-measure the layer before assuming it still arrives."
    )


def test_bootstrap_execution_guidance_uses_current_mode_not_legacy_defaults() -> None:
    from core.mode_permission_policy import set_active_mode

    for mode, label in (("manual", "Manual"), ("plan", "Plan"), ("auto", "Auto"),
                        ("review_edits", "Review edits")):
        session_id = f"bootstrap-mode:{uuid.uuid4().hex}"
        set_active_mode(session_id=session_id, mode=mode)
        items = build_bootstrap_context(
            persona=load_active_persona("default"),
            task=create_task_record("Repair the project and run its tests."),
            classification={"task_class": "debugging"},
            interpretation=adapt_user_input("Repair the project and run its tests.", session_id=session_id),
            session_id=session_id,
            include_private_context=False,
        )
        safety = next(i.content for i in items if i.item_id == "bootstrap-safety")
        persona = next(i.content for i in items if i.item_id == "bootstrap-persona")
        assert f"Operating mode: {label}." in safety
        assert "advice_only" not in safety
        assert "advice_first" not in persona
        if mode == "plan":
            assert "Read and plan only" in safety
        else:
            assert "Use tools for requested work" in safety
        assert "runtime" in safety and "approval" in safety


def test_bootstrap_without_session_state_cannot_claim_ungranted_bypass() -> None:
    session_id = f"bootstrap-mode:{uuid.uuid4().hex}"
    items = build_bootstrap_context(
        persona=load_active_persona("default"),
        task=create_task_record("Repair the project."),
        classification={"task_class": "debugging"},
        interpretation=adapt_user_input("Repair the project.", session_id=session_id),
        session_id=session_id,
        source_context={"operating_mode": "bypass_permissions"},
        include_private_context=False,
    )
    safety = next(i.content for i in items if i.item_id == "bootstrap-safety")
    assert "Operating mode: Manual." in safety
    assert "requests approval" in safety
