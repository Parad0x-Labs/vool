"""VOOL internationalization authority (lane: vool-i18n-docs-p1-20260904).

One typed locale registry (:mod:`core.i18n.locales`) and one deterministic message
catalog (:mod:`core.i18n.catalog`). The registry EXTENDS the canonical response-language
authority ``core.response_language_policy`` — it imports that module's closed language
table and never re-declares a competing list of languages.

Prime law enforced here (see docs/i18n/README.md): these are INDEPENDENT claims —
(1) a provider can answer in a language, (2) VOOL can explicitly request/prefer that
response language, (3) VOOL can infer the input language, (4) the deterministic UI is
localized, (5) documentation exists in that language, (6) tool/coding intent routes
equivalently, (7) OCR/speech works, (8) the complete served product journey is proven.
Each LocaleSpec field is capability-specific and evidence-backed; nothing is inferred
from one capability to another.

Runtime model translation NEVER generates security, permission, payment, recovery,
fault or update UI: the catalog is static data resolved by pure functions, and the
authority seams (core.faults, core.mode_permission_policy reasons, core.wallet warnings,
core.updater messages) are deliberately not routed through any model-backed path.
"""
from core.i18n.catalog import (
    CatalogDiagnostic,
    MessageCatalog,
    catalog_diagnostic_note,
    catalog_for,
    clear_catalog_cache,
    format_message,
)
from core.i18n.locales import (
    DIRECTION_LTR,
    DIRECTION_RTL,
    LocaleSpec,
    all_locale_tags,
    capability_matrix,
    docs_supported_tags,
    endonym_for,
    extended_tags_for,
    fallback_chain,
    get_locale,
    inferable_tags,
    negotiate_ui_locale,
    response_supported_tags,
    ui_catalog_tags,
)

__all__ = [
    "DIRECTION_LTR",
    "DIRECTION_RTL",
    "CatalogDiagnostic",
    "LocaleSpec",
    "Message",
    "MessageCatalog",
    "all_locale_tags",
    "capability_matrix",
    "catalog_diagnostic_note",
    "catalog_for",
    "clear_catalog_cache",
    "docs_supported_tags",
    "endonym_for",
    "extended_tags_for",
    "fallback_chain",
    "format_message",
    "get_locale",
    "inferable_tags",
    "negotiate_ui_locale",
    "response_supported_tags",
    "ui_catalog_tags",
]
