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

#: A time anchor that closes the clause: end of text, or punctuation that ends a clause.
TIME_ANCHOR_AT_END = (
    r"(?:right\s+now|now|today|tonight|tomorrow|currently|this\s+(?:morning|afternoon|evening|"
    r"weekend))\s*(?:$|[?.!;,])"
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

#: Weather verbs in every form a person asks with: "rain", "raining", "rainy", "going to rain".
WEATHER_VERB = (
    r"(?:rain(?:ing|y)?|snow(?:ing|y)?|freez(?:e|ing)|storm(?:ing|y)?|hail(?:ing)?|"
    r"sleet(?:ing)?|drizzl(?:e|ing)|pour(?:ing)?|thunder(?:ing)?|be\s+windy|windy|sunny|cloudy|"
    r"foggy)"
)
