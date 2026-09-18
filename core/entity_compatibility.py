"""Entity pairings that cannot both be true, decided from typed relations rather than phrases.

WHY THIS EXISTS. A binding made from an ambiguous token survives into reasoning and poisons
everything downstream of it. The measured shape is currency: "500 kr" is read as NOK, the next
sentence says Copenhagen, and every market-value, percentage, PPP and inflation conclusion after
that is computed against the wrong currency -- confidently, with no signal that the premise was a
guess. But nothing about the failure is monetary. The same shape is:

    Copenhagen + NOK      Shanghai + JPY        France + London     Germany + Warsaw
    Toyota + Passat       Renault + Golf        Apple + Galaxy      Samsung + iPhone

Each pair names two entities whose OWNERS disagree: Passat's make is Volkswagen, so a Toyota Passat
is not a car; Copenhagen's currency is DKK, so a Copenhagen price in NOK needs explaining.

THE RULE IS AUTHORITY, NOT CO-OCCURRENCE. Flagging every such pair would be wrong, and expensively
so: "convert 500 NOK for my Copenhagen trip" is ordinary English and a perfectly good request. What
separates it from the defect is where the binding came from.

* `STATED`   -- the user wrote the entity (an ISO code, a full currency name, a proper noun). It is
               evidence, not a guess. A stated code beside a place is NOT a contradiction.
* `INFERRED` -- the runtime picked one reading of an ambiguous token ("kr" is DKK/NOK/SEK/ISK, "¥"
               is JPY/CNY, "dollars" is USD/CAD/AUD/SGD/NZD). It is a guess wearing a code.

From that, two verdicts and no third:

* `REBIND`  -- an INFERRED binding contradicted by a STATED anchor. The anchor wins, the guess is
               invalidated by name, and the implied value is stated. This is the Copenhagen case.
* `CLARIFY` -- two STATED entities whose owners disagree and neither can yield. Nothing here
               guesses which one the user meant; the clause is reported as needing clarification.
               This is the Toyota-Passat case.

WHAT THIS MODULE DOES NOT DO. It does not resolve currencies -- `core.currency_intent` is the one
authority for that, and forking a second resolver here is exactly the failure this was written to
avoid. It reports which binding is unsafe and what the stated anchor implies; converting that into
an answer belongs to the lane that owns the domain.

The tables are deliberately small and are the extension point. Adding a city, a make or a brand is
a data edit; nothing about the decision changes. A pair this module has no data for produces no
verdict at all -- silence, not a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# ------------------------------------------------------------------------------- categories


class EntityCategory(str, Enum):
    CITY = "city"
    COUNTRY = "country"
    CURRENCY = "currency"
    VEHICLE_MODEL = "vehicle_model"
    VEHICLE_MAKE = "vehicle_make"
    PRODUCT = "product"
    BRAND = "brand"


class Authority(str, Enum):
    """Where a binding came from. The whole verdict turns on this."""

    STATED = "stated"
    INFERRED = "inferred"


class Verdict(str, Enum):
    REBIND = "rebind"
    CLARIFY = "clarify"


# ------------------------------------------------------------------------------------ tables
# Each table answers one question: who OWNS this entity? A city's owner is its country; a currency's
# owner is the country that issues it; a car model's owner is its make; a product line's owner is
# its brand. Two entities conflict when their owners resolve to different things.

_CITY_COUNTRY: dict[str, str] = {
    "copenhagen": "denmark",
    "aarhus": "denmark",
    "oslo": "norway",
    "bergen": "norway",
    "stockholm": "sweden",
    "gothenburg": "sweden",
    "reykjavik": "iceland",
    "shanghai": "china",
    "beijing": "china",
    "shenzhen": "china",
    "tokyo": "japan",
    "osaka": "japan",
    "kyoto": "japan",
    "paris": "france",
    "lyon": "france",
    "marseille": "france",
    "london": "united kingdom",
    "manchester": "united kingdom",
    "berlin": "germany",
    "munich": "germany",
    "hamburg": "germany",
    "warsaw": "poland",
    "krakow": "poland",
    "vilnius": "lithuania",
    "riga": "latvia",
    "tallinn": "estonia",
    "helsinki": "finland",
    "madrid": "spain",
    "barcelona": "spain",
    "rome": "italy",
    "milan": "italy",
    "lisbon": "portugal",
    "istanbul": "turkiye",
    "ankara": "turkiye",
    "singapore": "singapore",
    "new york": "united states",
    "toronto": "canada",
    "sydney": "australia",
}

_COUNTRY_CURRENCY: dict[str, str] = {
    "denmark": "DKK",
    "norway": "NOK",
    "sweden": "SEK",
    "iceland": "ISK",
    "china": "CNY",
    "japan": "JPY",
    "france": "EUR",
    "germany": "EUR",
    "spain": "EUR",
    "italy": "EUR",
    "portugal": "EUR",
    "lithuania": "EUR",
    "latvia": "EUR",
    "estonia": "EUR",
    "finland": "EUR",
    "united kingdom": "GBP",
    "poland": "PLN",
    "turkiye": "TRY",
    "singapore": "SGD",
    "united states": "USD",
    "canada": "CAD",
    "australia": "AUD",
}

# The tokens whose reading is a GUESS. A code produced from one of these is INFERRED, and a stated
# anchor outranks it. This list is the homograph families, not the ISO table -- `core.currency_intent`
# owns that, and duplicating it here would be the second resolver this module exists to avoid.
_AMBIGUOUS_CURRENCY_TOKEN_CODES: dict[str, tuple[str, ...]] = {
    "kr": ("DKK", "NOK", "SEK", "ISK"),
    "kroner": ("DKK", "NOK"),
    "krona": ("SEK", "ISK"),
    "kronor": ("SEK",),
    "krone": ("DKK", "NOK"),
    "¥": ("JPY", "CNY"),
    "yen": ("JPY",),
    "yuan": ("CNY",),
    "rmb": ("CNY",),
    "dollars": ("USD", "CAD", "AUD", "SGD", "NZD"),
    "dollar": ("USD", "CAD", "AUD", "SGD", "NZD"),
    "$": ("USD", "CAD", "AUD", "SGD", "NZD"),
}

_VEHICLE_MODEL_MAKE: dict[str, str] = {
    "passat": "volkswagen",
    "golf": "volkswagen",
    "polo": "volkswagen",
    "tiguan": "volkswagen",
    "corolla": "toyota",
    "camry": "toyota",
    "prius": "toyota",
    "yaris": "toyota",
    "clio": "renault",
    "megane": "renault",
    "captur": "renault",
    "civic": "honda",
    "accord": "honda",
    "focus": "ford",
    "fiesta": "ford",
    "mustang": "ford",
}

_VEHICLE_MAKES = frozenset(
    {"volkswagen", "vw", "toyota", "renault", "honda", "ford", "bmw", "audi", "peugeot"}
)
_MAKE_ALIASES = {"vw": "volkswagen"}

_PRODUCT_BRAND: dict[str, str] = {
    "iphone": "apple",
    "ipad": "apple",
    "macbook": "apple",
    "airpods": "apple",
    "galaxy": "samsung",
    "pixel": "google",
    "surface": "microsoft",
    "kindle": "amazon",
    "playstation": "sony",
    "xbox": "microsoft",
}

_BRANDS = frozenset({"apple", "samsung", "google", "microsoft", "amazon", "sony"})

# An ISO code the user typed. Uppercase-only by design: this module reads CASE as evidence and never
# normalizes the turn -- "ALL" is a code and "all" is English, and flattening them is the exact
# defect `core.currency_intent` was hardened against.
_STATED_CODE_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
_STATED_CODES = frozenset(_COUNTRY_CURRENCY.values()) | frozenset(
    code for codes in _AMBIGUOUS_CURRENCY_TOKEN_CODES.values() for code in codes
)


@dataclass(frozen=True)
class EntityMention:
    """One entity the clause names, and how strongly it names it."""

    category: EntityCategory
    value: str
    surface: str
    authority: Authority

    @property
    def stated(self) -> bool:
        return self.authority is Authority.STATED


@dataclass(frozen=True)
class EntityConflict:
    """Two entities whose owners disagree, and what to do about it."""

    verdict: Verdict
    anchor: EntityMention
    invalidated: EntityMention
    #: What the anchor implies, when anything can be implied. Empty for a CLARIFY.
    implied: str
    explanation: str

    @property
    def needs_clarification(self) -> bool:
        return self.verdict is Verdict.CLARIFY


def _word_present(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None


def _token_present(haystack: str, token: str) -> bool:
    if token.isalpha():
        return _word_present(haystack, token)
    return token in haystack


def resolve_entities(clause: str) -> tuple[EntityMention, ...]:
    """Every entity this clause names, with the authority its wording gives it.

    Case is read, never flattened: an uppercase three-letter run is a STATED code, an ambiguous
    lowercase token ("kr", "dollars") produces an INFERRED currency reading, and a place name is
    matched case-insensitively because "copenhagen" is still Copenhagen.
    """
    text = str(clause or "")
    lowered = text.lower()
    found: list[EntityMention] = []

    for city, country in _CITY_COUNTRY.items():
        if _word_present(lowered, city):
            found.append(
                EntityMention(EntityCategory.CITY, country, city, Authority.STATED)
            )
    for country in _COUNTRY_CURRENCY:
        if _word_present(lowered, country):
            found.append(
                EntityMention(EntityCategory.COUNTRY, country, country, Authority.STATED)
            )

    for code in sorted({match for match in _STATED_CODE_RE.findall(text) if match in _STATED_CODES}):
        found.append(EntityMention(EntityCategory.CURRENCY, code, code, Authority.STATED))
    stated_codes = {item.value for item in found if item.category is EntityCategory.CURRENCY}
    for token, codes in _AMBIGUOUS_CURRENCY_TOKEN_CODES.items():
        if not _token_present(lowered, token):
            continue
        # A stated code in the same clause already settles the reading; the ambiguous token is that
        # code's own word ("500 NOK in kroner"), not a competing guess.
        if stated_codes & set(codes):
            continue
        found.append(
            EntityMention(EntityCategory.CURRENCY, "|".join(codes), token, Authority.INFERRED)
        )

    for model, make in _VEHICLE_MODEL_MAKE.items():
        if _word_present(lowered, model):
            found.append(
                EntityMention(EntityCategory.VEHICLE_MODEL, make, model, Authority.STATED)
            )
    for make in _VEHICLE_MAKES:
        if _word_present(lowered, make):
            found.append(
                EntityMention(
                    EntityCategory.VEHICLE_MAKE,
                    _MAKE_ALIASES.get(make, make),
                    make,
                    Authority.STATED,
                )
            )

    for product, brand in _PRODUCT_BRAND.items():
        if _word_present(lowered, product):
            found.append(EntityMention(EntityCategory.PRODUCT, brand, product, Authority.STATED))
    for brand in _BRANDS:
        if _word_present(lowered, brand):
            found.append(EntityMention(EntityCategory.BRAND, brand, brand, Authority.STATED))

    return tuple(found)


def _currency_for_place(place: EntityMention) -> str:
    """A place mention already carries its COUNTRY as its value -- a city resolves to one when it
    is recognised -- so the currency is one lookup, not two."""
    return _COUNTRY_CURRENCY.get(place.value, "")


def _places(entities: tuple[EntityMention, ...]) -> list[EntityMention]:
    return [
        item for item in entities
        if item.category in {EntityCategory.CITY, EntityCategory.COUNTRY}
    ]


def _stated_currency_conflicts(entities: tuple[EntityMention, ...]) -> list[EntityConflict]:
    """A place and a STATED code whose issuer is a different country. Both are the user's own
    words, so neither yields and nothing here picks a winner -- this is the Copenhagen + NOK pair,
    and the only safe verdict is to ask."""
    conflicts: list[EntityConflict] = []
    for place in _places(entities):
        implied = _currency_for_place(place)
        if not implied:
            continue
        for currency in entities:
            if currency.category is not EntityCategory.CURRENCY:
                continue
            if currency.authority is not Authority.STATED or currency.value == implied:
                continue
            conflicts.append(
                EntityConflict(
                    verdict=Verdict.CLARIFY,
                    anchor=place,
                    invalidated=currency,
                    implied="",
                    explanation=(
                        f"{place.surface.title()} uses {implied}, and this names "
                        f"{currency.value}. Which one is the request about?"
                    ),
                )
            )
    return conflicts


def _place_conflicts(entities: tuple[EntityMention, ...]) -> list[EntityConflict]:
    """A country named beside a city that is not in it -- France + London, Germany + Warsaw."""
    conflicts: list[EntityConflict] = []
    countries = [item for item in entities if item.category is EntityCategory.COUNTRY]
    cities = [item for item in entities if item.category is EntityCategory.CITY]
    for country in countries:
        for city in cities:
            if city.value == country.value:
                continue
            conflicts.append(
                EntityConflict(
                    verdict=Verdict.CLARIFY,
                    anchor=country,
                    invalidated=city,
                    implied="",
                    explanation=(
                        f"{city.surface.title()} is in {city.value.title()}, not "
                        f"{country.value.title()}. Which did you mean?"
                    ),
                )
            )
    return conflicts


def _owner_conflicts(
    entities: tuple[EntityMention, ...],
    *,
    owned: EntityCategory,
    owner: EntityCategory,
    noun: str,
) -> list[EntityConflict]:
    """A named MODEL/PRODUCT beside a MAKE/BRAND that does not own it. Both are stated, so neither
    can yield -- there is no reading in which a Toyota Passat exists, and picking one for the user
    is the guess this whole module refuses to make."""
    conflicts: list[EntityConflict] = []
    for owned_item in [item for item in entities if item.category is owned]:
        for owner_item in [item for item in entities if item.category is owner]:
            if owned_item.value == owner_item.value:
                continue
            conflicts.append(
                EntityConflict(
                    verdict=Verdict.CLARIFY,
                    anchor=owner_item,
                    invalidated=owned_item,
                    implied="",
                    explanation=(
                        f"\u201c{owned_item.surface.title()}\u201d is a {noun} made by "
                        f"{owned_item.value.title()}, not {owner_item.value.title()}. "
                        "Which did you mean?"
                    ),
                )
            )
    return conflicts


def entity_conflicts(clause: str) -> tuple[EntityConflict, ...]:
    """Pairings WITHIN one clause that cannot both be true.

    Only stated-versus-stated pairs are decided here, and every verdict is CLARIFY. A clause may
    legitimately name several places and several ambiguous currency words -- "I exchanged 8,500 kr
    for $1,240, then spent $300 in Singapore" is one sentence about a trip, not a contradiction --
    so an ambiguous token is never settled from a place standing beside it. Settling is a
    cross-clause operation; see `settlements_across`.
    """
    entities = resolve_entities(clause)
    conflicts = _stated_currency_conflicts(entities)
    conflicts += _place_conflicts(entities)
    conflicts += _owner_conflicts(
        entities, owned=EntityCategory.VEHICLE_MODEL, owner=EntityCategory.VEHICLE_MAKE, noun="model"
    )
    conflicts += _owner_conflicts(
        entities, owned=EntityCategory.PRODUCT, owner=EntityCategory.BRAND, noun="product"
    )
    return tuple(conflicts)


def settlements_across(earlier: str, later: str) -> tuple[EntityConflict, ...]:
    """What a LATER clause settles about an EARLIER clause's guess.

    This is the measured shape and the reason the module exists. "500 kr to dollars" binds nothing
    on its own -- "kr" is DKK, NOK, SEK or ISK. The next clause says Copenhagen, and now it does:
    DKK, with the NOK and SEK readings invalidated by name. Every market-value, percentage, PPP or
    inflation conclusion the earlier reading would have supported is void, which is why this
    returns a REBIND rather than quietly correcting the code.

    An anchor only settles a token it can actually settle. Singapore says nothing about which "kr"
    was meant, because SGD was never one of the readings, so no verdict is produced at all.
    """
    later_entities = resolve_entities(later)
    # A clause that names money of its own is a separate monetary subject, not context for someone
    # else's. "500 kr to dollars" followed by "I'm in Copenhagen" is a question and its context;
    # followed by "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore" it is two
    # unrelated sums, and letting the second one's Singapore settle the first one's "dollars" to
    # SGD is a poisoned binding invented by the very code meant to prevent one.
    if any(item.category is EntityCategory.CURRENCY for item in later_entities):
        return ()
    settlements: list[EntityConflict] = []
    for token in resolve_entities(earlier):
        if token.category is not EntityCategory.CURRENCY or token.authority is not Authority.INFERRED:
            continue
        candidates = tuple(token.value.split("|"))
        if len(candidates) < 2:
            continue
        for place in _places(later_entities):
            implied = _currency_for_place(place)
            if not implied or implied not in candidates:
                continue
            excluded = [code for code in candidates if code != implied]
            settlements.append(
                EntityConflict(
                    verdict=Verdict.REBIND,
                    anchor=place,
                    invalidated=token,
                    implied=implied,
                    explanation=(
                        f"\u201c{token.surface}\u201d could be {'/'.join(candidates)}. "
                        f"{place.surface.title()} settles it to {implied}, so a "
                        f"{'/'.join(excluded)} reading does not hold \u2014 and neither does "
                        "anything already computed from one."
                    ),
                )
            )
    return tuple(settlements)


__all__ = [
    "Authority",
    "EntityCategory",
    "EntityConflict",
    "EntityMention",
    "Verdict",
    "entity_conflicts",
    "resolve_entities",
    "settlements_across",
]
