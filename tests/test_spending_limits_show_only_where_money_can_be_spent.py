"""Spending limits are shown only where money can be spent.

A computer that only runs local models has nothing to pay for, so Usage & Budgets carries no paid model rows and the new
chat screen asks nothing about limits. Once a cloud key is kept or the wallet is on, the rows come back and the new chat
screen asks once: set your own limits, or keep the built-in ones. The built-in limits are enforced either way.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from core import ai_spend


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setattr("core.runtime_paths.data_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(ai_spend, "person_limits", lambda: {})
    return tmp_path


def _possible(*, key=False, wallet=False, spent=False):
    return mock.patch.multiple(
        ai_spend,
        _a_cloud_key_is_kept=lambda: key,
        _the_wallet_is_on=lambda: wallet,
        _anything_was_spent=lambda: spent,
    )


@pytest.mark.parametrize(
    ("key", "wallet", "spent", "expected"),
    [
        (False, False, False, False),
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
    ],
)
def test_paid_use_is_possible_only_with_a_key_the_wallet_or_spend(data_home, key, wallet, spent, expected) -> None:
    with _possible(key=key, wallet=wallet, spent=spent):
        assert ai_spend.paid_use_possible() is expected
        assert ai_spend.limits_prompt_due() is expected


def test_a_check_that_fails_counts_as_money_can_be_spent(data_home) -> None:
    def broken() -> bool:
        raise OSError("unreadable")

    with mock.patch.multiple(ai_spend, _a_cloud_key_is_kept=broken, _the_wallet_is_on=lambda: False,
                             _anything_was_spent=lambda: False):
        assert ai_spend.paid_use_possible() is True


def test_limits_of_their_own_keep_the_limits_shown_and_end_the_ask(data_home, monkeypatch) -> None:
    monkeypatch.setattr(ai_spend, "person_limits", lambda: {"daily": 2.0})
    with _possible():
        assert ai_spend.paid_use_possible() is True
        assert ai_spend.limits_prompt_due() is False


def test_keeping_the_built_in_limits_ends_the_ask_and_is_remembered(data_home) -> None:
    with _possible(key=True):
        assert ai_spend.limits_prompt_due() is True
        ai_spend.keep_default_limits()
        assert ai_spend.limits_prompt_due() is False
        assert ai_spend.paid_use_possible() is True
    stored = json.loads((data_home / "ai_spend_prompt.json").read_text(encoding="utf-8"))
    assert stored == {"kept_defaults": True}
    assert not list(data_home.glob(".ai_spend_prompt.*"))


def test_a_damaged_prompt_file_asks_again(data_home) -> None:
    (data_home / "ai_spend_prompt.json").write_text("{not json", encoding="utf-8")
    with _possible(wallet=True):
        assert ai_spend.limits_prompt_due() is True


def test_the_built_in_limits_stay_enforced_while_hidden(data_home) -> None:
    from core.paid_call_reservation import spend_limits

    with _possible():
        assert ai_spend.paid_use_possible() is False
        limits = spend_limits()
    assert float(limits.daily_usd) > 0 and float(limits.monthly_usd) > 0 and float(limits.per_call_usd) > 0


def _budgets(possible: bool) -> dict:
    from core import vool_settings_page

    with mock.patch.object(vool_settings_page, "_paid_use_possible", return_value=possible):
        groups = vool_settings_page.settings_groups()
    return next(group for group in groups if group["id"] == "budgets")


def test_local_only_settings_carry_no_paid_rows_no_paid_card_and_no_stand_in() -> None:
    group = _budgets(False)
    ids = {row["id"] for row in group["rows"]}
    assert not ids & {"spend", "daily_token_budget"}
    assert "spend" not in {card["id"] for card in group["cards"]}
    assert "ram_reserve_pct" in ids
    every_card = {card["id"] for card in group["cards"]}
    assert all(row.get("card") in every_card for row in group["rows"])


def test_settings_show_the_paid_rows_once_money_can_be_spent() -> None:
    group = _budgets(True)
    ids = {row["id"] for row in group["rows"]}
    assert {"spend", "daily_token_budget"} <= ids
    assert "spend" in {card["id"] for card in group["cards"]}


def test_the_overview_says_whether_to_ask(data_home) -> None:
    with _possible():
        local = ai_spend.overview()
    with _possible(key=True):
        paid = ai_spend.overview()
    assert local["paid_use_possible"] is False and local["limits_prompt_due"] is False
    assert paid["paid_use_possible"] is True and paid["limits_prompt_due"] is True


def test_every_locale_words_the_card() -> None:
    from core.i18n.catalog import CATALOGS_DIR

    keys = ("spend_card.title", "spend_card.body", "spend_card.set", "spend_card.keep")
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in keys:
            assert messages.get(key), (path.name, key)
        assert "{daily}" in messages["spend_card.body"] and "{monthly}" in messages["spend_card.body"], path.name


def test_the_new_chat_screen_asks_only_when_due() -> None:
    from core.vool_chat_page import render_vool_chat_html

    page = render_vool_chat_html()
    start = page.index("async function loadSpendCard()")
    body = page[start:page.index("\n}\n", start)]
    assert "limits_prompt_due" in body
    assert "/api/spend/keep-defaults" in body
    assert "openSettings('budgets/spend')" in body
