"""Catalog additions for the second failure-words job: the worst moments keep speaking plain words.

The eight new failure keys must exist with real translations in every locale, every locale keeps
exactly en's key set, the rewritten values drop the status code and the retired "API Keys" group
name (each pointer now names the locale's own keys-group title), and no locale lost its source
hash while en stays flat and hash-free. The provider-site islands that legitimately say
"API Keys" stay pinned as they were.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOGS = Path(__file__).resolve().parent.parent / "core" / "i18n" / "catalogs"
LOCALES = ["ar", "de", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans"]

NEW_FW2_KEYS = [
    "chat.still_unreachable",
    "chat.session_export_failed",
    "chat.session_preview_failed",
    "chat.session_import_failed",
    "chat.model_switch_failed",
    "chat.send_failed",
    "chat.result_failed",
    "settings.store_mismatch",
]
NEW_FW2_EN_VALUES = {
    "chat.still_unreachable": "Still can't reach VOOL. It will keep trying.",
    "chat.session_export_failed": "The chat list could not be saved to a file.",
    "chat.session_preview_failed": "The preview could not be loaded.",
    "chat.session_import_failed": "The chats could not be brought back. Nothing was changed.",
    "chat.model_switch_failed": "That model could not be picked. The old one stays.",
    "chat.send_failed": "The message could not be sent.",
    "chat.result_failed": "This answer could not be shown. Try again.",
    "settings.store_mismatch": "What Settings stored does not match what was saved. Try again.",
}
REWRITTEN_FW2_KEYS = [
    "attach.doc_gone_http",
    "vmw.err.key_missing",
    "vmw.err.key_unreadable",
    "vmw.err.key_rejected",
    "vcs.recovery.revoked",
    "keyguide.provider.helius.step2",
]
REWRITTEN_FW2_EN_VALUES = {
    "attach.doc_gone_http": "This document is no longer available.",
    "vmw.err.key_missing": "No saved key serves this chain. Add one in Settings → Your keys.",
    "vmw.err.key_unreadable": "The saved key could not be read from this computer. Check Settings → Your keys, then Resume.",
    "vmw.err.key_rejected": "The saved key was refused. Replace it in Settings → Your keys, then Resume.",
}
# Provider-site references and pinned islands that keep their "API Keys" wording on purpose.
PINNED_API_KEYS_ISLANDS = [
    "addons.manage_api_keys",
    "addons.no_key",
    "first_run.fact_10",
    "keys.copy_again",
    "keyguide.provider.etherscan.step1",
    "keyguide.provider.moralis.step2",
]


def _load(name: str) -> dict:
    return json.loads((CATALOGS / f"{name}.json").read_text(encoding="utf-8"))


def _messages(name: str) -> dict:
    data = _load(name)
    assert isinstance(data.get("messages"), dict), f"{name}.json lost its messages object"
    return data["messages"]


def test_every_catalog_carries_the_eight_new_keys_with_en_exact_values() -> None:
    en = _messages("en")
    for key in NEW_FW2_KEYS:
        assert key in en, f"en.json is missing the new failure key {key}"
        assert en[key] == NEW_FW2_EN_VALUES[key], f"en {key} drifted from the agreed wording"
    for loc in LOCALES:
        msgs = _messages(loc)
        for key in NEW_FW2_KEYS:
            assert key in msgs, f"{loc}.json is missing the new failure key {key}"
            assert msgs[key].strip(), f"{loc}.json left {key} empty or untranslated"


def test_every_locale_keeps_exactly_the_en_key_set() -> None:
    en = _messages("en")
    for loc in LOCALES:
        msgs = _messages(loc)
        assert set(msgs) == set(en), (
            f"{loc}.json key set diverges from en: missing={sorted(set(en) - set(msgs))[:5]} "
            f"extra={sorted(set(msgs) - set(en))[:5]}"
        )


@pytest.mark.parametrize("locale", ["en", *LOCALES])
def test_new_keys_are_placeholder_free_plain_sentences(locale: str) -> None:
    msgs = _messages(locale)
    for key in NEW_FW2_KEYS:
        assert "{" not in msgs[key], f"{locale} {key} carries a placeholder: {msgs[key]!r}"


@pytest.mark.parametrize("locale", ["en", *LOCALES])
def test_rewritten_values_carry_no_http_code_and_no_api_keys_group_name(locale: str) -> None:
    msgs = _messages(locale)
    for key in REWRITTEN_FW2_KEYS:
        value = msgs[key]
        assert "(HTTP" not in value, f"{locale} {key} still shows an HTTP status: {value!r}"
        assert "HTTP {" not in value, f"{locale} {key} still interpolates a status: {value!r}"
        assert "API Keys" not in value, f"{locale} {key} still names the retired API Keys group: {value!r}"
        assert "API" not in value, f"{locale} {key} still leans on the API word: {value!r}"
    if locale == "en":
        for key, expected in REWRITTEN_FW2_EN_VALUES.items():
            assert msgs[key] == expected, f"en {key} drifted from the agreed wording"
        assert "under Your keys" in msgs["vcs.recovery.revoked"]
        assert msgs["keyguide.provider.helius.step2"] == (
            "Open Your keys and copy your key. It looks like 8 characters, a dash, "
            "then more groups of letters and numbers."
        )


@pytest.mark.parametrize("locale", LOCALES)
def test_each_locale_points_at_its_own_keys_group_wording(locale: str) -> None:
    msgs = _messages(locale)
    group = msgs["settings.group.keys.title"]
    assert group.strip(), f"{locale} lost its keys group title"
    for key in ("vmw.err.key_missing", "vmw.err.key_unreadable", "vmw.err.key_rejected",
                "vcs.recovery.revoked", "keyguide.provider.helius.step2"):
        assert group in msgs[key], (
            f"{locale} {key} does not name this locale's keys group {group!r}: {msgs[key]!r}"
        )


def test_the_pinned_provider_islands_keep_their_api_keys_wording() -> None:
    en = _messages("en")
    for key in PINNED_API_KEYS_ISLANDS:
        assert "API Keys" in en[key] or "API keys" in en[key], (
            f"the pinned island {key} was scrubbed of its provider-site wording"
        )


def test_no_locale_lost_its_source_hash_and_en_stays_flat() -> None:
    en_data = _load("en")
    assert "source_catalog_sha256" not in en_data, "en.json must not carry its own source hash"
    for key, value in en_data["messages"].items():
        assert isinstance(value, str), f"en.json leaf {key} is not a flat string"
    for loc in LOCALES:
        data = _load(loc)
        stamp = data.get("source_catalog_sha256")
        assert isinstance(stamp, str) and re.fullmatch(r"[0-9a-f]{64}", stamp), (
            f"{loc}.json lost or mangled its source_catalog_sha256"
        )
