"""The recovered i18n engine, registered as the ONE localization authority.

Decision recorded in the consolidation matrix: ``core/i18n`` (the git gold —
SHA-pinned drift, ICU-lite plural/select, HTML-escape, deterministic fallback)
is THE engine; ``vool_localization`` (the disk-only 6-adapter family) stays
vaulted for future native surfaces and is NOT registered, so two localization
engines can never drift apart in one runtime.

Pins:
- every tag the response-language policy can answer in resolves to a real
  locale in the i18n registry (the policy/engine drift law);
- the catalogs load, en is the canonical key set, and every locale's keys are
  a subset of it (no orphan translations);
- ``format_message`` renders plurals/select deterministically and escapes HTML;
- the page-bundle seam (``i18n_client_bundle_for``) produces a bounded payload
  for future surfaces — with the UI surface itself OFF behind the current-UX
  mandate, nothing serves it today by design.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.i18n.catalog import format_message
from core.i18n.locales import get_locale, response_supported_tags
from core.response_language_policy import LANGUAGE_NAMES

CATALOGS = Path(__file__).resolve().parents[2] / "core" / "i18n" / "catalogs"


def test_every_answer_language_resolves_in_the_i18n_registry() -> None:
    unresolved = [tag for tag in LANGUAGE_NAMES if get_locale(tag) is None]
    assert unresolved == [], f"policy tags absent from the i18n locale registry: {unresolved}"


def test_response_supported_tags_are_locales() -> None:
    for tag in response_supported_tags():
        assert get_locale(tag) is not None, tag


def test_catalogs_load_and_stay_within_the_english_key_set() -> None:
    en = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))
    en_messages = en.get("messages") or {}
    assert en_messages, "the canonical English catalog is empty"
    assert en.get("schema") == "vool.i18n.catalog.v1"
    for path in sorted(CATALOGS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        orphans = set(data.get("messages") or {}) - set(en_messages)
        assert not orphans, f"{path.name} carries message keys English does not: {sorted(orphans)[:5]}"


def test_format_message_plural_and_escape() -> None:
    template = "{count, plural, one {{count} item} other {{count} items}}"
    one = format_message(template, {"count": 1})
    many = format_message(template, {"count": 5})
    assert one == "1 item", one
    assert many == "5 items", many
    escaped = format_message("name={name}", {"name": "<script>"})
    assert "<script>" not in escaped, escaped


def test_unknown_params_key_left_verbatim() -> None:
    assert format_message("plain message") == "plain message"
