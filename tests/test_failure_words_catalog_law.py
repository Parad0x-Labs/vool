"""Catalog law for the failure words: when VOOL looks broken, it speaks the person's language.

The moment a person most needs plain words is the moment something failed, so the failure
sentences are catalog keys with real translations in every locale, the rewritten cloud/model
values carry no status codes or jargon, and the served pages actually carry the translated
failure words (a bundle that only ships English would leave the worst moment untranslated).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html

CATALOGS = Path(__file__).resolve().parent.parent / "core" / "i18n" / "catalogs"
LOCALES = ["ar", "de", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans"]

NEW_CHAT_KEYS = [
    "chat.offline_bar", "chat.offline_retry", "chat.back_online",
    "chat.load_failed_title", "chat.load_failed_text", "chat.load_failed_retry",
    "chat.rename_failed_offline", "chat.rename_failed",
    "chat.delete_failed_offline", "chat.delete_failed",
    "chat.archive_failed_offline", "chat.archive_failed",
    "chat.unarchive_failed_offline", "chat.unarchive_failed",
    "chat.try_again",
]
NEW_SETTINGS_KEYS = [
    "settings.row_loading", "settings.row_saving", "settings.row_saved", "settings.row_cleared",
    "settings.retry", "settings.row_unavailable", "settings.not_responding", "settings.row_not_saved",
    "settings.export_failed", "settings.preview_failed",
    "settings.import_failed",
]
REWRITTEN_KEYS = [
    "cloud.err_401", "cloud.err_402", "cloud.err_403", "cloud.err_404",
    "cloud.err_429", "cloud.err_500", "cloud.err_502", "cloud.err_503",
    "header.run_prov_unanswered", "model.reason_price_rose", "model.reason_paid_classified",
    "session.project_delete_confirm", "msg.review_flagged", "setup.step.thinking.later",
    "settings.group.keys.title",
]

# The old values these keys replace -- pinned so a merge cannot quietly bring the jargon back.
RETIRED_EN_VALUES = {
    "cloud.err_401": "Key rejected (401 Unauthorized)",
    "cloud.err_429": "Rate limited (429)",
    "cloud.err_503": "Service unavailable (503)",
    "header.run_prov_unanswered": "UNKNOWN — server did not answer",
    "model.reason_price_rose": "ABOVE",
    "model.reason_paid_classified": "PAID",
    "session.project_delete_confirm": "VOOL will FORGET",
    "msg.review_flagged": "⚠ review flagged",
    "setup.step.thinking.later": "API Keys",
}
JARGON = ("endpoint", "provider", "base url")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
CAPS_RUN_RE = re.compile("[A-Z]{3,}")


def _load(name: str) -> dict:
    return json.loads((CATALOGS / f"{name}.json").read_text(encoding="utf-8"))


def _messages(name: str) -> dict:
    data = _load(name)
    assert isinstance(data.get("messages"), dict), f"{name}.json lost its messages object"
    return data["messages"]


def test_every_locale_carries_the_same_key_set_with_the_new_failure_keys() -> None:
    en = _messages("en")
    for key in NEW_CHAT_KEYS + NEW_SETTINGS_KEYS + REWRITTEN_KEYS:
        assert key in en, f"en.json is missing the new failure key {key}"
    for loc in LOCALES:
        msgs = _messages(loc)
        assert set(msgs) == set(en), (
            f"{loc}.json key set diverges from en: missing={sorted(set(en) - set(msgs))[:5]} "
            f"extra={sorted(set(msgs) - set(en))[:5]}"
        )


def test_en_catalog_stays_flat_and_never_gains_a_source_hash() -> None:
    data = _load("en")
    assert "source_catalog_sha256" not in data, "en.json must not carry its own source hash"
    for key, value in data["messages"].items():
        assert isinstance(value, str), f"en.json leaf {key} is not a flat string"


@pytest.mark.parametrize("locale", ["en", *LOCALES])
def test_cloud_error_values_carry_no_codes_or_jargon_in_any_locale(locale: str) -> None:
    msgs = _messages(locale)
    for key in [k for k in REWRITTEN_KEYS if k.startswith("cloud.err_")]:
        value = msgs[key]
        assert not re.search(r"\d", value), f"{locale} {key} still carries a status code: {value!r}"
        for word in JARGON:
            assert word not in value.lower(), f"{locale} {key} still says '{word}': {value!r}"
    # the en values specifically retired the coded phrasings wholesale
    if locale == "en":
        for key, fragment in RETIRED_EN_VALUES.items():
            assert fragment not in msgs[key], f"{key} still carries the retired wording {fragment!r}"


@pytest.mark.parametrize("locale", ["en", *LOCALES])
def test_rewritten_values_never_shout_in_caps_or_lead_with_a_warning_sign(locale: str) -> None:
    msgs = _messages(locale)
    for key in ("header.run_prov_unanswered", "model.reason_price_rose", "model.reason_paid_classified",
                "session.project_delete_confirm"):
        for run in CAPS_RUN_RE.findall(msgs[key].replace("VOOL", "")):
            assert not run, f"{locale} {key} shouts in caps: {msgs[key]!r}"
    flagged = msgs["msg.review_flagged"]
    assert not EMOJI_RE.search(flagged[:3]), f"{locale} msg.review_flagged still leads with a mark: {flagged!r}"


def test_the_served_chat_page_carries_the_failure_words_of_a_non_english_locale() -> None:
    """The German and Japanese bundles must actually reach the served page, not just disk."""
    for loc in ("de", "ja"):
        html = render_vool_chat_html(ui_locale=loc)
        for key in ("chat.offline_bar", "chat.load_failed_title", "chat.load_failed_text", "chat.try_again"):
            value = _messages(loc)[key]
            assert value in html, f"{loc} bundle did not reach the served chat page for {key}"


def test_the_served_settings_page_carries_the_german_row_words() -> None:
    html = render_vool_settings_html(ui_locale="de")
    for key in ("settings.row_unavailable", "settings.row_not_saved", "settings.export_failed"):
        value = _messages("de")[key]
        assert value in html, f"the de bundle did not reach the served settings page for {key}"


def test_the_setup_pointer_names_your_keys_not_api_keys() -> None:
    en = _messages("en")
    pointer = en["setup.step.thinking.later"]
    assert "Your keys" in pointer, f"the setup pointer must point at Settings → Your keys: {pointer!r}"
    assert "API Keys" not in pointer
    # the pointer names groups by their real titles, so the renamed title and it must agree
    assert en["settings.group.keys.title"] == "Your keys"
    assert en["settings.group.models.title"] == "Models & Providers", "the models group title was not this job's to rename"
    for loc in LOCALES:
        pointer = _messages(loc)["setup.step.thinking.later"]
        assert "API Keys" not in pointer, f"{loc} still points at the old key-group name"
        assert _messages(loc)["settings.group.keys.title"].strip(), f"{loc} lost its keys group title"
