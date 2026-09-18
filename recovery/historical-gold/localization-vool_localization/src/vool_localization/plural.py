"""CLDR-lite cardinal plural rules for the locales in the pass-001 scope.

Deliberately explicit tables instead of a rules engine: the set of locales we
ship is small and auditable. Adding a locale means adding its rule function
and a test — no silent defaults.

Notes captured for reviewers:
    * pt-BR and pt-PT share cardinal categories (one/other); they differ in
      formatting and terminology, not plurals.
    * ar has six categories (zero/one/two/few/many/other) — the stress case
      that kills naive "singular/plural" systems.
"""
from __future__ import annotations

OTHER = "other"


def plural_en(n: float) -> str:
    return "one" if n == 1 else OTHER


def plural_pt(n: float) -> str:
    # CLDR pt / pt-BR / pt-PT: 'one' only for exactly 1 (no decimals).
    return "one" if n == 1 else OTHER


def plural_ar(n: float) -> str:
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    mod100 = n % 100
    if 3 <= mod100 <= 10:
        return "few"
    if 11 <= mod100 <= 99:
        return "many"
    return OTHER


RULES: dict[str, object] = {
    "en": plural_en,
    "pt": plural_pt,      # base for pt-BR, pt-PT
    "pt-br": plural_pt,
    "pt-pt": plural_pt,
    "ar": plural_ar,
    "qya-pseudo": plural_en,  # pseudo-locale keeps source-like categories
}


def resolve_rule(locale_tag: str):
    tag = locale_tag.strip().lower().replace("_", "-")
    if tag in RULES:
        return RULES[tag]
    lang = tag.split("-", 1)[0]
    if lang in RULES:
        return RULES[lang]
    return RULES["en"]
