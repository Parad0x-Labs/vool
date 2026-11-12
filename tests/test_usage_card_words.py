"""The usage card's words resolve through the catalog, and the daily cloud use
guide's help names its unit and its softness (words only; nothing behavioral).

Guards in this file answer one concrete question each:

- the chat page's usage card no longer paints raw English literals into the DOM:
  every card label and table header goes through pageT() with a catalog key,
  so a shipped locale renders its own words;
- the guide's help says what the unit is (word pieces) and what the code does
  (a soft guide — core/trust_projection.py reports it under soft_guides with
  enforced=False), and no longer claims "no limit";
- every new key ships in English and in all 16 locale catalogs, as a real
  translation (a copied English value would be rejected by the completion
  laws), and each entry passes the loader's own structural check.
"""
from __future__ import annotations

import json

from core.i18n.catalog import CATALOGS_DIR, _validate_locale_entry
from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()

CARD_KEYS = (
    "settings.usage.total_word_pieces",
    "settings.usage.col_model",
    "settings.usage.col_word_pieces",
    "settings.usage.col_answers",
    "settings.usage.col_cost",
)
HELP_KEY = "settings.row.daily_token_budget.help"
LOCALES = (
    "ar", "de", "es", "fr", "he", "hi", "ja", "ko",
    "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans",
)


def _messages(tag: str) -> dict[str, str]:
    return json.loads((CATALOGS_DIR / f"{tag}.json").read_text(encoding="utf-8"))["messages"]


def test_usage_card_words_render_through_the_catalog():
    for key in CARD_KEYS:
        assert f"pageT('{key}'" in HTML, key
    # The old raw-English render path is gone: no literal straight into the card
    # or the table header row.
    assert "usageCard('', 'Total word pieces'" not in HTML
    assert "['Model', 'Word pieces', 'Answers', 'Cost']" not in HTML


def test_budget_help_names_the_unit_and_the_softness():
    help_text = _messages("en")[HELP_KEY]
    assert "word pieces" in help_text
    assert "does not stop answers" in help_text
    assert "no limit" not in help_text


def test_every_catalog_carries_the_new_keys_as_real_translations():
    en = _messages("en")
    for tag in ("en", *LOCALES):
        messages = _messages(tag)
        for key in (*CARD_KEYS, HELP_KEY):
            assert key in messages, f"{tag}: {key} missing"
    for tag in LOCALES:
        messages = _messages(tag)
        for key in (*CARD_KEYS, HELP_KEY):
            assert messages[key] != en[key], f"{tag}: {key} copies English"


def test_locale_entries_pass_the_loader_s_own_structural_check():
    en = _messages("en")
    for tag in LOCALES:
        messages = _messages(tag)
        for key in (*CARD_KEYS, HELP_KEY):
            reason = _validate_locale_entry(key, messages[key], en[key], is_html=False, locale=tag)
            assert reason is None, f"{tag}: {key}: {reason}"
