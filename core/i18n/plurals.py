"""CLDR cardinal plural categories: static data and pure functions.

``{n, plural, one {…} other {…}}`` in a catalog message selects its branch by the
RENDERING locale's CLDR cardinal category for the count — not by a single
``n === 1`` test. Grammatical categories are not optional decoration: Polish
counts 2-4 ("few") and 5+ ("many") take different forms, Arabic distributes
counts over six categories, and a formatter that flattens them makes every such
locale render ungrammatical counts. This module owns that selection for every
UI-catalog locale VOOL ships, as transcribed rules (static data, no dependency)
so the deterministic-catalog law holds: nothing here fetches, parses prose, or
consults a model.

Rules transcribed from unicode-org/cldr ``common/supplemental/plurals.xml``
(cardinal rules; CLDR 46). The catalog formatter counts INTEGERS
(``format_message`` coerces its operand with ``int()``), so the operands that
cannot occur for a plain integer count — visible fraction digits (``v``/``f``)
and compact-decimal exponents (``e``) — are folded to their integer-only
consequences:

- the ``f != 0`` "many" of Lithuanian (fractions only) is unreachable here;
- the ``e``/million "many" of Spanish/French/Portuguese fires exactly for
  non-zero integer millions, so it is kept as an integer rule;
- a rule mentioning ``v != 0`` (Hebrew "one" for 1.x fractions) is dropped for
  the same reason — integers always have ``v = 0``.

Selection is by the locale's PRIMARY subtag (``zh-Hans`` → ``zh``): CLDR defines
cardinal categories per language, not per script or region, for every locale
this project ships.
"""
from __future__ import annotations

#: Every category name CLDR defines for cardinals. A plural branch outside this
#: set is a typo, not a category, and is rejected at catalog load time.
VALID_PLURAL_CATEGORIES = frozenset({"zero", "one", "two", "few", "many", "other"})


def _is_million(n: int) -> bool:
    """The es/fr/pt compact-million "many": a non-zero integer multiple of 10**6."""
    return n != 0 and n % 1_000_000 == 0


def _category_default(n: int) -> str:
    return "other"


def _category_one_other(n: int) -> str:
    # de, en, tr: i = 1 and v = 0
    return "one" if n == 1 else "other"


def _category_es(n: int) -> str:
    if n == 1:
        return "one"
    return "many" if _is_million(n) else "other"


def _category_fr_pt(n: int) -> str:
    # fr, pt: i = 0..1 → one (0 and 1 count as singular)
    if 0 <= n <= 1:
        return "one"
    return "many" if _is_million(n) else "other"


def _category_hi_vi(n: int) -> str:
    # hi, vi: i = 0 or n = 1 → one
    return "one" if n in (0, 1) else "other"


def _category_he(n: int) -> str:
    if n == 1:
        return "one"
    return "two" if n == 2 else "other"


def _category_ar(n: int) -> str:
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    hundreds = n % 100
    if 3 <= hundreds <= 10:
        return "few"
    if 11 <= hundreds <= 99:
        return "many"
    return "other"


def _category_pl(n: int) -> str:
    if n == 1:
        return "one"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "few"
    return "many"


def _category_ru_uk(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "few"
    return "many"


def _category_lt(n: int) -> str:
    if n % 10 == 1 and n % 100 not in range(11, 20):
        return "one"
    if n % 10 in range(2, 10) and n % 100 not in range(11, 20):
        return "few"
    return "other"


#: Primary-subtag rule table. Locales absent here have "other" only (the CLDR
#: no-plural class: ja, ko, zh, id, th, …) and resolve through the default.
_PLURAL_RULES: dict[str, object] = {
    "ar": _category_ar,
    "de": _category_one_other,
    "en": _category_one_other,
    "es": _category_es,
    "fr": _category_fr_pt,
    "he": _category_he,
    "hi": _category_hi_vi,
    "lt": _category_lt,
    "pl": _category_pl,
    "pt": _category_fr_pt,
    "ru": _category_ru_uk,
    "tr": _category_one_other,
    "uk": _category_ru_uk,
    "vi": _category_hi_vi,
}

#: The categories a NON-NEGATIVE INTEGER count can reach, per primary. A plural
#: translation for a locale must carry every category in this set (plus the
#: required ``other``), or the formatter would silently flatten a grammatical
#: distinction into ``other`` — the exact defect this table exists to prevent.
_INTEGER_CATEGORIES: dict[str, frozenset[str]] = {
    "ar": frozenset({"zero", "one", "two", "few", "many", "other"}),
    "de": frozenset({"one", "other"}),
    "en": frozenset({"one", "other"}),
    "es": frozenset({"one", "many", "other"}),
    "fr": frozenset({"one", "many", "other"}),
    "he": frozenset({"one", "two", "other"}),
    "hi": frozenset({"one", "other"}),
    "lt": frozenset({"one", "few", "other"}),
    "pl": frozenset({"one", "few", "many", "other"}),
    "pt": frozenset({"one", "many", "other"}),
    "ru": frozenset({"one", "few", "many", "other"}),
    "tr": frozenset({"one", "other"}),
    "uk": frozenset({"one", "few", "many", "other"}),
    "vi": frozenset({"one", "other"}),
}


def _primary(locale: str) -> str:
    return str(locale or "").strip().split("-")[0].lower() or "en"


def plural_category(locale: str, n: int) -> str:
    """The locale's CLDR cardinal category for the integer count ``n``.

    Deterministic and total: an unknown locale resolves through the no-plural
    default (``other``), and any count a message can carry maps to exactly one
    category of that locale's set.
    """
    rule = _PLURAL_RULES.get(_primary(locale), _category_default)
    return rule(int(n))


def integer_categories(locale: str) -> frozenset[str]:
    """Categories reachable by a non-negative integer count in ``locale``.

    Unknown primaries have none to reach beyond ``other``; the caller still
    requires the ``other`` branch separately (it is the universal fallback).
    """
    return _INTEGER_CATEGORIES.get(_primary(locale), frozenset({"other"}))


__all__ = [
    "VALID_PLURAL_CATEGORIES",
    "integer_categories",
    "plural_category",
]
