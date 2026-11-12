"""Closed word classes shared by the weather-request recognizer and the place extractor.

The classifier (`core.agent_runtime.fast_live_info_mode_classifier`) decides whether a message asks
for the weather; the extractor (`tools.web.web_research`) finds the place. When the two read a
phrasing differently, a recognized ask mints no lookup (or a lookup is minted for prose), so the
prepositionless and progressive-verb shapes both use the definitions here. No imports: both
readers load this at module import time.

0.7.8 sweep 2026-11-12: "weather paris now" and "is it raining in paris today" reached the model,
which had nothing to answer with -- neither reader knew a place can follow the weather word with no
preposition, or that "raining" asks the same thing as "rain".
"""

from __future__ import annotations

#: Politeness that may close an ask after its time word ("paris weather now pls").
POLITE_TAIL = r"(?:\s+(?:please|pls|plz|thanks|thx|ty))?"

#: A time anchor that closes the clause: end of text, or punctuation that ends a clause. The chat
#: shorthands ("rn", "atm") and "at the moment" ask the same thing as "now" (app bugs 2026-11-12:
#: "paris weather rn" and "is it raining in Paris at the moment" found no place and failed).
TIME_ANCHOR_AT_END = (
    # "for today" asks the same as "today" ("Paris forecast for today?", app bugs 2026-11-12 rerun:
    # the ask was claimed as weather but no place was found, so nothing was looked up).
    r"(?:for\s+)?"
    r"(?:right\s+this\s+minute|right\s+now|now|rn|atm|at\s+(?:the|this)\s+moment|this\s+minute|"
    r"today|tonight|tomorrow|currently|this\s+(?:morning|afternoon|evening|weekend))"
    + POLITE_TAIL
    + r"\s*(?:$|[?.!;,])"
)

#: A time word right after "in/for/at" is not a place: "forecast for today", "weather for tonight".
#: A negative lookahead for the scanner's preposition shape, so the place-first shape reads the
#: place before the weather word instead.
NOT_A_TIME = (
    r"(?!(?:today|tonight|tomorrow|now|right\s+now|this\s+(?:morning|afternoon|evening|weekend|week)|"
    r"the\s+(?:day|week|weekend))\b)"
)

#: Words that can never be the place in a prepositionless weather ask: function words, pronouns,
#: auxiliaries, request verbs, time words and words that describe the weather itself (closed
#: classes, no place vocabulary). A negative lookahead to put in front of one place word.
NOT_A_PLACE = (
    r"(?!(?:the|a|an|this|that|these|those|my|your|our|their|his|her|its|some|any|no|what|which|"
    r"how|is|are|was|were|be|been|being|has|have|had|does|did|do|will|would|can|could|should|"
    r"must|shall|may|might|in|for|at|like|around|over|of|on|with|about|from|by|to|and|or|but|"
    r"so|very|really|too|today|tomorrow|tonight|now|currently|current|right|only|just|nice|good|"
    r"bad|great|lovely|terrible|awful|perfect|strange|weird|crazy|cold|hot|warm|cool|mild|wet|"
    r"dry|sunny|rainy|cloudy|windy|stormy|snowy|foggy|humid|beautiful|horrible|gloomy|grey|"
    r"gray|fine|such|same|patterns?|forecasts?|reports?|conditions?|news|apps?|stations?|data|"
    r"changes?|tell|show|give|get|check|please|pls|me|us|i|we|you|they|it|he|she|"
    r"weather|forecast)\b)"
)

#: One place word, and a place of one to three words, for the prepositionless shapes.
PLACE_WORD = NOT_A_PLACE + r"[A-Za-z][A-Za-z'.-]*"
PLACE = PLACE_WORD + r"(?:\s+" + PLACE_WORD + r"){0,2}"

#: A message that STARTS with the place and the weather word, search-box style: "paris weather",
#: "tokyo weather pls", "paris weather, gold price and ...". Anchored at the start of the line and
#: closed by the end of the clause, so prose that only mentions the weather ("I love this weather",
#: "such nice weather today") never reads as a place. The place is the first group.
PLACE_FIRST_AT_START = (
    r"^\s*(" + PLACE + r")\s+(?:weather|forecast)" + POLITE_TAIL + r"\s*(?:$|[?.!;,])"
)

#: "how cold is it in oslo", "how warm it is out in tallinn": the air at a place, asked by how it
#: feels. The words up to the preposition; the place follows. A named place is required, so "how
#: hot is it in the oven" (an article) and "how hot is the sun" (no in/at) stay out.
_FEEL = r"(?:cold|warm|hot|chilly|freezing|windy|humid|sunny|rainy|wet|snowy)"
#: The same ask as a yes/no question is the same lookup: "is it cold in Berlin right now", "isn't it
#: freezing in Toronto" (app bugs 2026-11-12 rerun: reached the model, which had nothing to read).
FEEL_ASK = (
    r"(?:how\s+" + _FEEL + r"\s+(?:is\s+it|it\s+is|it'?s)"
    r"|(?:is|isn'?t)\s+it\s+(?:(?:very|really|too|so|still)\s+)?" + _FEEL + r")\s+"
    r"(?:(?:out|outside|over|up|down)\s+)?(?:in|at)\s+(?!(?:the|my|your|our|this|that|a|an|here|there)\b)"
)

#: "what's it like outside in Paris", "how's it outside in Oslo": the air at a place, asked by what
#: it is like out there. "outside" (or "out") is required: "what's it like living in Paris" and
#: "what is it like to work in Berlin" ask about life there, not the weather. Same rerun as above.
OUTSIDE_ASK = (
    r"(?:what(?:'?s|\s+is)\s+it\s+like|how(?:'?s|\s+is)\s+it(?:\s+looking)?)\s+(?:outside|out(?:doors)?)\s+"
    r"(?:in|at)\s+(?!(?:the|my|your|our|this|that|a|an|here|there)\b)"
)

#: Weather verbs in every form a person asks with: "rain", "raining", "rainy", "going to rain".
WEATHER_VERB = (
    r"(?:rain(?:ing|y)?|snow(?:ing|y)?|freez(?:e|ing)|storm(?:ing|y)?|hail(?:ing)?|"
    r"sleet(?:ing)?|drizzl(?:e|ing)|pour(?:ing)?|thunder(?:ing)?|be\s+windy|windy|sunny|cloudy|"
    r"foggy)"
)
