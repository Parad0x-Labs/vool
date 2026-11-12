"""The plain-words retranslation laws for the catalog keys the 2026-10-29 English
plain-words fix changed (History/Records tabs, Wait and send, Auto backup, company
wording, word pieces, the daily use guide, ...).

Guards in this file answer one concrete question each:

- every shipped locale's entry for each of these keys is ACCEPTED by the loader —
  a rejected entry silently serves English, so structural parity (placeholders,
  html tag multiset) is asserted both through acceptance and directly;
- no locale still says what the OLD English said: the retired engineering words
  (queue, fallback, token, provider, budget as standalone Latin words) may not
  survive in these strings, and the entry may not be a copy of English;
- the two renamed panel tabs stay two DIFFERENT everyday words in every locale —
  if a locale's History and Records collapsed to one word, the tab pair broke;
- the short controls (the queue button, the backup label, the tab names) stay
  short enough to be controls at all.
"""

from __future__ import annotations

import re

import pytest

from core.i18n.catalog import (
    CATALOGS_DIR,
    MessageCatalog,
    clear_catalog_cache,
    placeholders_of,
)

# The 20 keys whose English changed in the plain-words fix (settingsfix..pwf).
PLAIN_WORDS_KEYS = (
    "cloud.check_key",
    "cloud.failed",
    "composer.queue",
    "header.model_auto_explainer",
    "header.model_connect_note",
    "header.model_local_only_explainer",
    "header.run_local_title",
    "header.run_prov_fallback",
    "model.auto_fallback_label",
    "notif.row.provider",
    "panel.tab_event_log",
    "panel.tab_receipts",
    "proof.title",
    "queue.collapse_title",
    "run.queue_returned_summary",
    "settings.row.daily_token_budget.label",
    "settings.row.models_overview.help",
    "settings.row.usage.label",
    "settings.usage.tokens_many",
    "settings.usage.tokens_one",
)

# Words the plain-words fix retired from the English. A locale may transliterate a
# concept into its own script, but a standalone Latin-word leak here means the old
# meaning survived (or an untranslated fragment did).
_RETIRED_LATIN_WORDS = re.compile(
    r"\b(fallback|queue|token|tokens|provider|budget|receipts|evidence)\b",
    re.IGNORECASE,
)

SHORT_CONTROL_KEYS = (
    "composer.queue",
    "header.run_prov_fallback",
    "model.auto_fallback_label",
    "notif.row.provider",
    "panel.tab_event_log",
    "panel.tab_receipts",
)


def _shipped_locales() -> list[str]:
    return sorted(p.stem for p in CATALOGS_DIR.glob("*.json") if p.stem != "en")


def _en() -> MessageCatalog:
    return MessageCatalog("en")


@pytest.fixture()
def catalogs() -> dict[str, MessageCatalog]:
    clear_catalog_cache()
    en = _en()
    return {loc: MessageCatalog(loc) for loc in _shipped_locales()} | {"en": en}


def test_every_locale_entry_is_accepted_by_the_loader(catalogs) -> None:
    """A rejected entry silently serves English — for these keys that would undo the
    plain-words fix in that locale, so acceptance itself is the first law."""
    problems = []
    for loc, cat in catalogs.items():
        if loc == "en":
            continue
        for key in PLAIN_WORDS_KEYS:
            if key not in cat.locale_keys:
                reason = cat.diagnostic.rejected_keys.get(key, "missing from the locale file")
                problems.append(f"{loc}: {key} not accepted ({reason})")
    assert not problems, "; ".join(problems[:8])


def test_placeholders_and_html_tags_match_english_exactly(catalogs) -> None:
    problems = []
    tag_re = re.compile(r"</?[a-zA-Z][^>]*>")
    for loc, cat in catalogs.items():
        for key in PLAIN_WORDS_KEYS:
            value = cat.text(key)
            source = _en().text(key)
            if placeholders_of(value) != placeholders_of(source):
                problems.append(
                    f"{loc}: {key} placeholders {sorted(placeholders_of(value))} "
                    f"!= en {sorted(placeholders_of(source))}"
                )
            if sorted(tag_re.findall(value)) != sorted(tag_re.findall(source)):
                problems.append(f"{loc}: {key} tag multiset != en")
    assert not problems, "; ".join(problems[:8])


def test_no_entry_is_copied_english(catalogs) -> None:
    en = _en()
    copied = [
        f"{loc}: {key}"
        for loc, cat in catalogs.items()
        if loc != "en"
        for key in PLAIN_WORDS_KEYS
        if cat.text(key) == en.text(key)
    ]
    assert not copied, "; ".join(copied[:8])


def test_the_retired_engineering_words_did_not_survive(catalogs) -> None:
    """The English retired queue/fallback/token/provider/budget wording from these
    strings; a locale that still carries one (as a standalone Latin word) kept the
    old meaning or leaked an untranslated fragment."""
    problems = []
    # Placeholder spans ({tokens} and friends) are mandatory structure, not prose —
    # strip them before scanning, or the scan flags the very names the law requires.
    prose = lambda text: re.sub(r"\{\w+\}", "", text)  # noqa: E731
    for loc, cat in catalogs.items():
        if loc == "en":
            continue
        for key in PLAIN_WORDS_KEYS:
            match = _RETIRED_LATIN_WORDS.search(prose(cat.text(key)))
            if match:
                problems.append(f"{loc}: {key} still says {match.group(0)!r}")
    assert not problems, "; ".join(problems[:8])


def test_the_two_renamed_tabs_stay_two_different_words(catalogs) -> None:
    """History and Records are different things in English; if a locale's two tab
    labels collapsed to the same word, the person cannot tell the tabs apart."""
    same = [
        loc
        for loc, cat in catalogs.items()
        if cat.text("panel.tab_event_log") == cat.text("panel.tab_receipts")
    ]
    assert not same, f"tab labels collapsed in: {', '.join(same)}"


def test_short_controls_stay_short(catalogs) -> None:
    """A button or tab label that grew past this is no longer a control. English is
    the yardstick with headroom for the longer grammar of other languages."""
    limit = 32
    problems = [
        f"{loc}: {key} is {len(cat.text(key))} chars: {cat.text(key)!r}"
        for loc, cat in catalogs.items()
        for key in SHORT_CONTROL_KEYS
        if len(cat.text(key)) > limit
    ]
    assert not problems, "; ".join(problems)


def test_english_still_carries_the_plain_words_meaning() -> None:
    """Pin the English these translations are made from: if the source drifts again,
    the locale parity above is measuring the wrong thing."""
    en = _en()
    expected = {
        "composer.queue": "Wait and send",
        "model.auto_fallback_label": "Auto backup",
        "notif.row.provider": "company",
        "panel.tab_event_log": "History",
        "panel.tab_receipts": "Records",
        "settings.row.usage.label": "Usage",
        "settings.row.daily_token_budget.label": "Daily cloud use guide",
    }
    drifted = [f"{k} != {expected[k]!r}" for k, v in expected.items() if en.text(k) != v]
    assert not drifted, "; ".join(drifted)
