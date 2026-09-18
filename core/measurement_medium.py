"""A temperature of WATER is not the temperature of the air above it.

The defect this module exists to close
--------------------------------------
Measured live on c6eed761 (2026-08-14), served UI::

    U: what is the watter temperature in Balctic sea?
    A: Balctic Sea: Clear, 15 C (today's high 30 C / low 14 C).
       Source: wttr.in, observed 11:15 AM.
       tool | live_data_typed_plan | no model

Three things went wrong and only one of them is cosmetic. `_extract_weather_location` matched
`\\b(?:in|for|at)\\s+(.+)$` and took "balctic sea" as a CITY; wttr.in geolocated it to somewhere and
returned that somewhere's AIR conditions; and the runtime rendered the result as the answer, with a
source, as though the question had been answered. The sea surface was around 18 C at the time. The
user asked for water and was given air, confidently and with provenance.

The wrong-answer part is the blocker. A source attached to a value that does not answer the question
is worse than no answer: it is the shape of a correct reply, so nothing downstream doubts it, and the
`Source: [wttr.in](...)` line invites the reader to trust it more, not less.

The rule
--------
A provider answers ONE kind of measurement. `wttr.in` answers conditions of the atmosphere at a
place. A request whose subject is the temperature of a body of water is a different measurement, and
the air-weather lane must not claim it -- not to render it, and not to fetch for it.

This module answers only "is the requested measurement about water rather than air?" It does not
decide what to do instead; refusing to claim the turn is the caller's business.

On the two lists here
---------------------
The medium set and the temperature set ARE enumerations, and both are deliberate. They are closed
semantic classes -- the media a temperature can be reported in, and the words that ask for a
temperature -- not vocabulary lifted from the reported failure. Neither grows when a new sea is
named, because a sea is not a member: "Baltic" is not in either list and does not need to be.

Matching is edit-distance tolerant because the reproduction was "watter", and a runtime that answers
the wrong question for a correctly-spelled request while answering it for a typo would be worse than
one that is wrong consistently.

The threshold is set by one measured pair, not by taste. "weather" against "water" scores 0.833, and
if that matches then "what is the weather temperature in Vilnius" is read as a water request and an
ordinary air lookup is refused -- which was true at a first-cut floor of 0.82 and is pinned as a test
now. The typo shapes that must still match score higher: "watter" and "wather" are both 0.909, as is
"oceean". 0.87 is the floor that separates them, and it is where it is because of those numbers.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

#: The colloquial water-state ask: "how's the water", "hows the sea" — a temperature
#: question with no temperature word. Scoped to the medium word so "how's the weather"
#: can never match. Measured live (2026-08-30): "hows the sea near mallorca this evening"
#: carried no temperature word, so both halves failed, the turn was claimed by nobody,
#: and the model improvised an unsourced range — the exact fabrication class the
#: medium split exists to prevent.
_HOWS_THE_MEDIUM_RE = re.compile(r"how'?s\s+(?:the\s+)?(?:water|sea|ocean|seawater)\b")

#: Media a temperature can be reported in that are NOT the atmosphere. A named body of water
#: ("Baltic", "Ontario", "Como") is deliberately absent -- membership here is about the MEDIUM word,
#: so the set does not grow with geography.
_WATER_MEDIA = frozenset({"water", "sea", "ocean", "lake", "river", "marine", "sst", "seawater"})

#: Words that ask for a temperature. Kept separate from the medium so the two must co-occur.
_TEMPERATURE_WORDS = frozenset({"temperature", "temperatures", "temp", "temps", "warm", "cold", "degrees"})

#: Set by measurement: "weather"/"water" is 0.833 and must NOT match; "watter"/"water" and
#: "oceean"/"ocean" are 0.909 and must. 0.87 is the floor that separates them.
_FUZZY_FLOOR = 0.87

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _fold(text: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _matches_any(token: str, vocabulary: frozenset[str]) -> bool:
    if token in vocabulary:
        return True
    if len(token) < 4:
        # Too short for edit distance to mean anything; exact membership only.
        return False
    return any(
        SequenceMatcher(None, token, word).ratio() >= _FUZZY_FLOOR
        for word in vocabulary
        if abs(len(word) - len(token)) <= 2
    )


def requests_a_water_temperature(text: Any) -> bool:
    """Whether this request asks for the temperature OF WATER rather than of the air.

    Requires both halves: something that asks for a temperature, and a medium word saying the
    subject is water. "what is the temperature in Vilnius" has no medium and is untouched; "sea
    conditions today" has a medium but asks for no temperature and is untouched.
    """

    tokens = [token for token in _TOKEN_RE.findall(_fold(text))]
    if not tokens:
        return False
    wants_temperature = any(_matches_any(token, _TEMPERATURE_WORDS) for token in tokens)
    if not wants_temperature:
        # The colloquial form carries the temperature intent in the verb phrase,
        # not in a temperature word — check it BEFORE the medium requirement so
        # "how's the water" needs no second signal.
        if _HOWS_THE_MEDIUM_RE.search(_fold(text)):
            return True
        return False
    return any(_matches_any(token, _WATER_MEDIA) for token in tokens)


__all__ = ["requests_a_water_temperature"]


# -- a conversion between measurement units is not a price -------------------------------------------
#
# Measured on the packaged build 784cf713 (2026-09-08, acceptance slice control an_neg): "how much would
# 2 ounces of gold weigh in grams" was answered with the gold PRICE per troy ounce, sourced, by the
# live-info lane -- the asset word admitted the turn and nothing asked what measurement was wanted.
# Same rule as water-versus-air above: a lane answers ONE kind of measurement. A request that names a
# quantity in one unit and asks for it in another (or asks what it weighs) is a unit conversion, and
# the market-quote lane must not claim it. This module says only "is it a unit conversion?"; what
# answers it instead is the caller's business.
_MASS_UNITS = frozenset({"gram", "grams", "g", "kilogram", "kilograms", "kg", "kgs", "ounce", "ounces", "oz",
                         "pound", "pounds", "lb", "lbs", "tonne", "tonnes", "ton", "tons", "milligram", "milligrams", "mg", "carat", "carats"})
_LENGTH_UNITS = frozenset({"meter", "meters", "metre", "metres", "m", "centimeter", "centimeters", "centimetre", "centimetres", "cm",
                           "millimeter", "millimeters", "millimetre", "millimetres", "mm", "kilometer", "kilometers", "kilometre", "kilometres", "km",
                           "inch", "inches", "foot", "feet", "ft", "yard", "yards", "mile", "miles"})
_VOLUME_UNITS = frozenset({"liter", "liters", "litre", "litres", "l", "milliliter", "milliliters", "millilitre", "millilitres", "ml",
                           "gallon", "gallons", "pint", "pints", "quart", "quarts", "cup", "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons"})
_MEASUREMENT_UNITS = _MASS_UNITS | _LENGTH_UNITS | _VOLUME_UNITS
_CONVERSION_CUES = frozenset({"weigh", "weighs", "weighed", "weight", "convert", "converted", "conversion", "equals", "equal", "equivalent"})
_UNIT_TARGET_RE = re.compile(r"\b(?:in|to|into|as|how\s+many)\s+([a-z]+)\b")
_QUANTITY_UNIT_RE = re.compile(r"\b\d[\d.,]*\s*([a-z]+)\b")


def requests_a_unit_conversion(text: Any) -> bool:
    """Whether the request asks to express a quantity in another measurement unit (or asks its weight).

    Requires a measurement unit AND either a conversion cue ("weigh", "convert") or a second unit as
    the target ("2 ounces ... in grams"). "gold price per ounce" names a unit and asks nothing of it;
    "convert 100 usd to eur" names no measurement unit; both stay untouched.
    """
    folded = _fold(text)
    tokens = _TOKEN_RE.findall(folded)
    if not tokens:
        return False
    units_named = {token for token in tokens if token in _MEASUREMENT_UNITS}
    if not units_named:
        return False
    if any(token in _CONVERSION_CUES for token in tokens):
        return True
    quantity_units = {match.group(1) for match in _QUANTITY_UNIT_RE.finditer(folded) if match.group(1) in _MEASUREMENT_UNITS}
    target_units = {match.group(1) for match in _UNIT_TARGET_RE.finditer(folded) if match.group(1) in _MEASUREMENT_UNITS}
    return bool(quantity_units and (target_units - quantity_units))
