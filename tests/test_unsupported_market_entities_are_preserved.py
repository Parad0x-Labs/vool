"""Every explicitly requested market entity produces a subtask -- resolved or UNSUPPORTED_ENTITY,
never silent omission.

Found live (step 4 novel-case testing): "Ethereum, Solana, oil, and copper" answered only the two
crypto assets; "Bitcoin and Qwertycoin999xyz" answered only Bitcoin. Neither corrupted a sibling,
but the user had no way to tell "not fetchable" from "the runtime never understood the request" --
that gap is what this fix closes.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.live_data_plan import (
    SubtaskLifecycle,
    build_live_data_plan,
)
from core.agent_runtime.live_data_render import render_live_data_answer
from core.agent_runtime.live_data_runner import run_live_data_plan
from core.live_quote_contract import LiveQuoteResult
from tools.web.web_research import _extract_market_entity_candidates

# --- extraction-level: recognized crypto / commodity / precious metal / unsupported / mixed ---


def test_recognized_crypto_is_extracted() -> None:
    assert "ethereum" in _extract_market_entity_candidates("price for Ethereum")


def test_recognized_commodity_is_extracted() -> None:
    assert "oil" in _extract_market_entity_candidates("market data for oil")


def test_recognized_precious_metal_is_extracted() -> None:
    assert "gold" in _extract_market_entity_candidates("current price for gold")


def test_unsupported_asset_is_extracted_not_dropped() -> None:
    assert "qwertycoin999xyz" in _extract_market_entity_candidates("price of Qwertycoin999xyz")


def test_mixed_supported_and_unsupported_list_extracts_all() -> None:
    candidates = _extract_market_entity_candidates("price for Ethereum, Solana, oil, and copper")
    assert candidates == ["ethereum", "solana", "oil", "copper"]


def test_punctuation_and_conjunction_variations() -> None:
    variations = [
        "price for Bitcoin, Ethereum, and Solana",  # oxford comma
        "price for Bitcoin, Ethereum and Solana",  # no oxford comma
        "price for Bitcoin & Ethereum & Solana",  # ampersand
        "price for Bitcoin and Ethereum and Solana",  # repeated "and"
    ]
    for text in variations:
        candidates = _extract_market_entity_candidates(text)
        assert set(candidates) >= {"bitcoin", "ethereum", "solana"}, text


# --- plan-level: unsupported entities become explicit typed subtasks ---


def test_unsupported_commodities_become_explicit_subtasks_alongside_resolved_crypto() -> None:
    plan = build_live_data_plan(
        "Current price and daily change for Ethereum, Solana, oil, and copper, plus weather in Riga, Helsinki, and Prague.",
        plan_id="p", attempt_id="a",
    )
    assert plan is not None
    assert len(plan.subtasks) == 7  # requested cardinality: 4 market (2 resolved + 2 unsupported) + 3 weather
    market = plan.market_subtasks()
    assert len(market) == 4
    resolved = {t.arguments.get("asset_key") for t in market if t.operation == "market_quote"}
    assert resolved == {"ethereum", "solana"}
    unsupported = {t.arguments.get("requested_text") for t in market if t.operation == "unsupported_market_entity"}
    assert unsupported == {"oil", "copper"}


def test_one_recognized_plus_one_unknown_asset_both_produce_subtasks() -> None:
    plan = build_live_data_plan("Price of Bitcoin and Qwertycoin999xyz.", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.market_subtasks()) == 2
    kinds = {t.operation for t in plan.market_subtasks()}
    assert kinds == {"market_quote", "unsupported_market_entity"}


def test_no_duplicate_subtask_when_a_candidate_is_already_resolved() -> None:
    """A candidate the primary extraction already covered must not ALSO appear as unsupported."""
    plan = build_live_data_plan("Market data only for Bitcoin and gold.", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.market_subtasks()) == 2  # not 4 -- no duplication


# --- runner-level: unsupported entities never reach the network, resolve immediately ---


def test_unsupported_entities_never_reach_the_network_and_resolve_immediately() -> None:
    plan = build_live_data_plan("Price of Bitcoin and Qwertycoin999xyz.", plan_id="p", attempt_id="a")
    crypto_calls: list[list[str]] = []

    def _tracking_crypto(coin_ids, **_kwargs):
        crypto_calls.append(list(coin_ids))
        return [LiveQuoteResult(asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64000.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="crypto", change_percent=0.1)]

    with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_tracking_crypto):
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)

    # Only bitcoin was ever fetched -- the unsupported entity never generated a network call.
    assert all("qwertycoin999xyz" not in call for call in crypto_calls)
    unsupported_outcome = next(o for o in outcomes if o.subtask.operation == "unsupported_market_entity")
    assert unsupported_outcome.state is SubtaskLifecycle.UNSUPPORTED_ENTITY
    assert unsupported_outcome.started_at is None  # never entered the timed execution path at all


# --- render-level: unsupported entities render as unavailable, never silently vanish ---


def test_render_shows_unsupported_entities_as_unavailable_rows() -> None:
    plan = build_live_data_plan(
        "Current price and daily change for Ethereum, Solana, oil, and copper.", plan_id="p", attempt_id="a"
    )

    def fake_crypto(coin_ids, **_kwargs):
        table = {
            "ethereum": LiveQuoteResult(asset_key="ethereum", asset_name="Ethereum", symbol="ETH", value=1900.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="crypto", change_percent=1.5),
            "solana": LiveQuoteResult(asset_key="solana", asset_name="Solana", symbol="SOL", value=73.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="crypto", change_percent=-0.5),
        }
        return [table[c] for c in coin_ids if c in table]

    with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto):
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
    rendered = render_live_data_answer(plan, outcomes)

    assert "Ethereum" in rendered and "1,900.00" in rendered
    assert "Solana" in rendered and "73.00" in rendered
    assert "Oil" in rendered
    assert "Copper" in rendered
    assert rendered.count("unavailable") == 2  # oil and copper, each their own row
    # The unsupported rows must not be silently absent, and must not corrupt the resolved ones.
    assert "Largest absolute 24-hour mover: Ethereum" in rendered


def test_sabotage_reverting_to_silent_omission_reproduces_the_incident() -> None:
    """Proves the fix is load-bearing: patching _extract_market_entity_candidates to find nothing
    reproduces the exact silent-omission bug for oil/copper while bitcoin/ethereum/solana-style
    resolved entities are completely unaffected."""
    with mock.patch("tools.web.web_research._extract_market_entity_candidates", return_value=[]):
        plan = build_live_data_plan(
            "Current price and daily change for Ethereum, Solana, oil, and copper.", plan_id="p", attempt_id="a"
        )
    assert plan is not None
    assert len(plan.market_subtasks()) == 2  # the bug, reproduced: oil/copper silently gone
    assert {t.arguments["asset_key"] for t in plan.market_subtasks()} == {"ethereum", "solana"}
