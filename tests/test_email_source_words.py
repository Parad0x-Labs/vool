"""Settings > Email: why the default account is the one in use, in words, never the source token.

Measured on the Part B base (56aa94be, plus the keyed English the lead landed): the badge on
the default row printed "default · chat" and the default summary line printed "(chat scope)" --
the internal def.source id reached the page raw, and EMAIL_SOURCE_TEXT held plain strings no
locale could translate, while EMAIL_STATE_TEXT one screen up already showed the keyed shape.

The rules this file guards:

- source lane: every EMAIL_SOURCE_TEXT value is a keyed call (the EMAIL_STATE_TEXT shape, not a
  raw string that could bypass the catalog), one helper emailSourceText(source) turns any id
  into words -- known ids through the table, an unknown or missing id through the keyed
  emailadd.source.other sentence -- so no raw source token reaches a row: the badge, the default
  summary line and the Reading uses / Sending uses lines all read through the helper, and the
  word "scope" is gone from the summary;
- drift lane: each inline English fallback equals the catalog -- one wording, one source;
- catalog lane: the five emailadd.source.* keys exist in English and all 16 shipped locales,
  really translated, in the position the English file gives them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CATALOGS = REPO / "core" / "i18n" / "catalogs"
LOCALES = ("ar", "de", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans")

#: The keys this change added, in the order en.json carries them right after emailadd.service.other.
SOURCE_KEYS = (
    "emailadd.source.chat",
    "emailadd.source.global",
    "emailadd.source.legacy_default",
    "emailadd.source.only_account",
    "emailadd.source.other",
)

#: The badge and the summary line exactly as the page must build them (raw ids never concatenated).
BADGE_LINE = "'default' + (def.source && def.source !== 'global' ? ' · ' + emailSourceText(def.source) : '')"
SUMMARY_LINE = "'Default: ' + def.account + ' (' + emailSourceText(def.source || 'global') + ')'"


def _page() -> str:
    from core.vool_settings_page import render_vool_settings_html

    return render_vool_settings_html()


def _en() -> dict[str, str]:
    return json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))["messages"]


def _placeholders(value: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", value))


def _source_table() -> dict[str, str]:
    """The EMAIL_SOURCE_TEXT block as id -> inline English fallback, read off the shipped page."""
    page = _page()
    return dict(re.findall(r"(\w+): \(\) => T\('emailadd\.source\.\w+', '([^']*)'\)", page))


# ---- source lane: keyed table, one helper, no raw token -----------------------------------------


def test_the_source_table_is_keyed_like_the_state_table_not_plain_strings() -> None:
    page = _page()
    # Every value is a function returning the keyed text -- a locale can translate each id.
    assert "global: () => T('emailadd.source.global', 'your default')," in page
    assert "chat: () => T('emailadd.source.chat', 'chosen for this chat')," in page
    assert "only_account: () => T('emailadd.source.only_account', 'the only account')," in page
    assert "legacy_default: () => T('emailadd.source.legacy_default', 'used because it is named “default”')" in page
    # The plain-string table no locale could translate is gone for good.
    assert "global: 'your default'" not in page
    assert "chat: 'chosen for this chat'" not in page
    assert "only_account: 'the only account'" not in page
    assert set(_source_table()) == {"global", "chat", "only_account", "legacy_default"}


def test_one_helper_resolves_every_id_and_an_unknown_id_gets_the_keyed_other_sentence() -> None:
    page = _page()
    assert "function emailSourceText(source)" in page
    # Known ids resolve through the table; anything else gets the one keyed sentence.
    assert "EMAIL_SOURCE_TEXT[source] ? EMAIL_SOURCE_TEXT[source]() : T('emailadd.source.other', 'chosen another way');" in page


def test_an_unknown_source_id_renders_as_the_keyed_other_sentence_never_the_id() -> None:
    # Simulated render, no browser: the helper's two arms, with the table's fallbacks (pinned to
    # the catalog below), turn an id the page does not know into the other sentence.
    en = _en()
    table = _source_table()
    for source, rendered in (("bluebird", table.get("bluebird", en["emailadd.source.other"])),
                             (None, table.get(None, en["emailadd.source.other"]))):
        assert rendered == en["emailadd.source.other"], f"{source!r} did not fall back to the keyed sentence"
        assert "bluebird" not in rendered


def test_the_badge_and_the_summary_read_through_the_helper_and_carry_no_raw_token() -> None:
    page = _page()
    assert BADGE_LINE in page
    assert SUMMARY_LINE in page
    # The old raw passthroughs are gone for good: nothing concatenates def.source itself,
    # and the summary no longer appends the word "scope" to it.
    assert "+ def.source" not in page
    assert "def.source +" not in page
    assert "(def.source || 'global') + ' scope)'" not in page
    assert "' scope)'" not in page
    # The rule, not the examples: take the two lines away and no def.source read is left --
    # every read that remains feeds emailSourceText.
    assert "def.source" not in page.replace(BADGE_LINE, "").replace(SUMMARY_LINE, "")


def test_the_reading_and_sending_lines_read_through_the_helper() -> None:
    page = _page()
    assert "' — ' + emailSourceText(s.source)" in page
    # The old form (`EMAIL_SOURCE_TEXT[s.source]` uninvoked) would now concatenate the
    # function itself instead of words, so it is gone for good.
    assert "EMAIL_SOURCE_TEXT[s.source] ?" not in page
    assert "' — ' + EMAIL_SOURCE_TEXT[s.source]" not in page
    assert "s.source ||" not in page
    # No other s.source read exists to bypass the helper: the line's own presence check
    # (s.source ?) and its helper feed are the only reads, and the feed goes through words.
    assert not re.findall(r"\bs\.source\b", page.replace("(s.source ? ' — ' + emailSourceText(s.source) : '')", ""))


# ---- drift lane: the inline English fallbacks are the catalog, not a second wording --------------


@pytest.mark.parametrize("key", SOURCE_KEYS)
def test_the_page_fallback_english_equals_the_catalog(key: str) -> None:
    page = _page()
    en = _en()
    quoted = re.escape(en[key])
    patterns = (
        rf"T\('{re.escape(key)}', '{quoted}'\)",
        rf"T\('{re.escape(key)}', \"{quoted}\"\)",
    )
    assert any(re.search(p, page) for p in patterns), (
        f"the page's inline English for {key} drifted from en.json -- one wording, one source"
    )


# ---- catalog lane: translated, complete, in place --------------------------------------------------


def test_every_locale_carries_the_five_source_keys_really_translated() -> None:
    en = _en()
    problems: list[str] = []
    for tag in ("en", *LOCALES):
        messages = json.loads((CATALOGS / f"{tag}.json").read_text(encoding="utf-8"))["messages"]
        for key in SOURCE_KEYS:
            if key not in messages:
                problems.append(f"{tag}: {key} missing")
                continue
            if _placeholders(messages[key]) != _placeholders(en[key]):
                problems.append(f"{tag}: {key} placeholders {_placeholders(messages[key])} != {_placeholders(en[key])}")
        if tag != "en":
            for key in SOURCE_KEYS:  # copied English is not a completed translation
                if messages.get(key) == en[key]:
                    problems.append(f"{tag}: {key} is untranslated English")
    assert not problems, "; ".join(problems)


def test_the_source_keys_sit_where_the_english_file_puts_them() -> None:
    for tag in ("en", *LOCALES):
        keys = list(json.loads((CATALOGS / f"{tag}.json").read_text(encoding="utf-8"))["messages"])
        anchor = keys.index("emailadd.service.other")
        assert keys[anchor + 1:anchor + 6] == list(SOURCE_KEYS), f"{tag}: the five keys moved"
