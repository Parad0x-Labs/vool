"""Advice beside a live price, with Web lookups off: the advice is answered, the prices are named as needing the
web (coordinator decision 2026-11-13; CI #681 current-input-mixed-price was refused whole)."""
from __future__ import annotations

import pytest

from core.model_output_guard import unverified_live_value_notice, web_off_live_value_notice
from core.online_asks import advice_beside_live_prices


@pytest.mark.parametrize("ask", [
    "Can you recommend accessories for my current photography setup and tell me their prices today?",
    "suggest a laptop for coding and what does it cost right now?",
    "Can you recommend a coffee grinder? What are prices like today?",
])
def test_advice_beside_a_live_price_is_a_mixed_ask(ask):
    assert advice_beside_live_prices(ask)


@pytest.mark.parametrize("ask", [
    "what is the price of gold today",
    "price of gold and bitcoin",
    "whats the weather in London today",
    "When does the next train to Vilnius leave?",
    "recommend a hotel in Rome",
])
def test_an_ask_that_is_all_live_value_or_all_advice_is_not_mixed(ask):
    assert not advice_beside_live_prices(ask)


def test_the_notice_names_web_lookups_instead_of_promising_a_fetch():
    answer = "I suggest a padded case. " + unverified_live_value_notice(("price",))
    said = web_off_live_value_notice(answer)
    assert said.startswith("I suggest a padded case. Current prices need Web lookups, which are off"), said
    assert "Settings, Privacy & Permissions" in said
    assert "fetch the real data" not in said


def test_a_part_notice_and_other_text_are_kept_as_said():
    part = unverified_live_value_notice(("price",), "and tell me their prices today", part_of_turn=True)
    assert "fetch the real data" not in web_off_live_value_notice(part)
    assert web_off_live_value_notice("Nothing to change here.") == "Nothing to change here."


def test_the_reply_edge_keeps_the_advice_and_says_prices_need_web_lookups(monkeypatch):
    from core.agent_runtime import reply_edge

    monkeypatch.setattr("core.remote_fetch_policy.effective_web_available", lambda *_a, **_k: False)
    ask = "suggest a good tripod for travel and tell me what it costs today"
    table = ("| Tripod | Price (USD) | Weight |\n| --- | --- | --- |\n| Peak Design Travel | $379 | 1.27 kg |\n\n"
             "Prices can vary by retailer.")
    said = reply_edge.web_off_prices_reply(table, ask, {})
    assert "Peak Design Travel" in said and "1.27 kg" in said and "$" not in said, said
    assert said.endswith(reply_edge.WEB_OFF_PRICES_LINE), said
    assert reply_edge.web_off_prices_reply(table, "recommend a hotel in Rome", {}) == table
