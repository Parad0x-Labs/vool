"""Change-attributed asset clauses must reach the market lane (live defect 2026-08-29).

The reported turn: "1000 usd to eur and then to gold? also btc price and 24
change on eth" planned gold+bitcoin and silently dropped eth — the eth clause's
only market anchor was "change", which was in no market vocabulary. This family
pins the semantic class, not the reported string (CLAUDE.md §6b): change-
attributed asset mentions authorize their asset, paraphrased or mistyped, while
non-market senses of "change" stay out of the lane.
"""

from __future__ import annotations

import pytest

from core.market_intent import (
    market_quote_intent_present,
    market_semantics_present,
    market_term_spans,
)
from core.semantic_claim_authority import mention_is_market_authorized

REPORTED_QUERY = "1000 usd to eur and then to gold? also btc price and 24 change on eth"


def _span_of(text: str, needle: str) -> tuple[int, int]:
    index = text.lower().index(needle)
    return index, index + len(needle)


# ── the vocabulary admits the change family as market movement ──────────────


@pytest.mark.parametrize(
    "text",
    [
        REPORTED_QUERY,
        "24h change on eth?",
        "what is the day change on gold?",
        "how much has silver changed today?",
        "is ethereum changing today?",
        "btc price change in the last 24 hours",
        "give me the % change on solana",
        "weekly change for cardano",
    ],
)
def test_change_attributed_market_questions_carry_market_semantics(text: str) -> None:
    assert market_semantics_present(text) is True
    assert market_quote_intent_present(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "climate change is the defining issue of our time",
        "can you help me change my booking?",
        "the game changer in mobile phones was the touchscreen",
        "let's change the subject.",
        "how do I change my password?",
    ],
)
def test_non_market_senses_of_change_are_not_market_semantics(text: str) -> None:
    # The act-of-altering sense stays out of the market lane: bare "change" is
    # deliberately NOT in the vocabulary, only windowed forms are.
    assert market_semantics_present(text) is False


def test_bare_transitive_change_is_deliberately_not_a_market_anchor() -> None:
    # Documented residual, kept as a pinned negative: the windowed family means
    # "oil changed the twentieth century economy" carries no market anchor.
    text = "oil changed the twentieth century economy"
    assert market_semantics_present(text) is False


# ── binding: the change anchor authorizes its asset through the real window ──


@pytest.mark.parametrize(
    ("text", "asset"),
    [
        ("24 change on eth", "eth"),
        ("24h change on eth", "eth"),
        ("what's the day change on gold?", "gold"),
        ("sol change today", "sol"),
        ("weekly change for cardano", "cardano"),
        ("did eth change today", "eth"),
    ],
)
def test_change_anchor_binds_its_asset_without_preauthorized_domain(
    text: str, asset: str
) -> None:
    spans = market_term_spans(text)
    assert spans, "no market anchor found to bind with"
    start, end = _span_of(text, asset)
    assert mention_is_market_authorized(text, start, end) is True, (
        f"asset {asset!r} in {text!r} could not bind to a change anchor"
    )


def test_the_exact_reported_query_resolves_all_three_assets() -> None:
    from core.agent_runtime.fast_live_info_price import price_assets_named

    resolved = price_assets_named(REPORTED_QUERY, domain_already_authorized=True)
    lowered = {name.lower() for name in resolved}
    assert {"gold", "btc", "eth"} <= lowered


def test_plan_for_the_reported_query_contains_an_eth_subtask() -> None:
    # THE reported drop: the plan built for the mixed turn named gold and
    # bitcoin only; eth never became a subtask, not even an unsupported one.
    from core.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(
        "also btc price and 24 change on eth, plus gold please",
        plan_id="p1",
        attempt_id="a1",
    )
    entities = {
        getattr(task, "entity", "").lower() for task in plan.subtasks
    }
    assert {"bitcoin", "ethereum", "gold"} <= entities, (
        f"plan dropped a named asset: {sorted(entities)}"
    )


# ── honesty of the derived mover line across measurement windows ────────────


def _outcome(entity: str, change, window: str):
    from core.live_data_plan import LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome

    subtask = LiveDataSubtask(
        subtask_id=f"p1:market:{entity}",
        operation="market_quote",
        entity=entity,
        arguments={},
        required_result_fields=("price",),
        tool="market_prices",
        tool_intent="web.research",
    )
    result = (
        {"price": 1.0, "change_24h_pct": change}
        if window is None
        else {"price": 1.0, "change_24h_pct": change, "change_window": window}
    )
    return SubtaskOutcome(
        subtask=subtask, state=SubtaskLifecycle.SUCCEEDED, result=result
    )


def test_mover_comparisons_decline_mixed_measurement_windows() -> None:
    # A session change (Yahoo commodity) must not compete against a trailing
    # 24h change under a "24-hour" label — the derived line is declined.
    from core.agent_runtime.live_data_render import largest_absolute_mover

    outcomes = [_outcome("gold", -3.43, "session"), _outcome("bitcoin", -3.86, "24h")]
    assert largest_absolute_mover(outcomes) is None


def test_mover_compares_like_windows_and_labels_the_window() -> None:
    from core.agent_runtime.live_data_render import (
        _single_change_window,
        largest_absolute_mover,
    )

    outcomes = [_outcome("sol", 5.1, "session"), _outcome("ada", -2.0, "session")]
    mover = largest_absolute_mover(outcomes)
    assert mover is not None and mover[0] == "sol"
    assert _single_change_window(outcomes, None) == "session"


def test_mover_with_no_window_recorded_still_comparands_as_24h() -> None:
    from core.agent_runtime.live_data_render import (
        _single_change_window,
        largest_absolute_mover,
    )

    outcomes = [_outcome("btc", -3.86, "24h"), _outcome("eth", -1.2, None)]
    # eth carries no window field (legacy outcome shape): the default reading
    # is 24h, so the comparison proceeds rather than declining.
    mover = largest_absolute_mover(outcomes)
    assert mover is not None and mover[0] == "btc"
    assert _single_change_window(outcomes, None) == "24h"
