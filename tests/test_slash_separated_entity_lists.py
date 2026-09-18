""""/" joins a list the same way "and"/"&" do.

Found live (Checkpoint 6.5, item 5 production proof, running the ORIGINAL benchmark that started
this whole engagement): "market prices for gold/silver/BTC/BNB + weather for Kaunas/Tallinn/Warsaw"
produced only 5 subtasks, not 7. The market side correctly resolved all four assets, because
`price_assets_named` matches each alias by regex word-boundary anywhere in the raw text and never
needed an explicit delimiter list. The weather side used an explicit split-on-known-delimiters
approach that had never seen "/", so "Kaunas/Tallinn/Warsaw" stayed one candidate clause, no join
word was detected, and it was returned whole as a single (bogus) location -- the same "several
things collapsed into one malformed query" shape as the original incident this architecture exists
to prevent, just reached through a delimiter the split lists had not been taught yet.
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import build_live_data_plan
from tools.web.web_research import _extract_market_entity_candidates, _extract_weather_locations


def test_slash_separated_weather_cities_split_into_three_locations() -> None:
    locations = _extract_weather_locations("weather for Kaunas/Tallinn/Warsaw")
    assert locations == ["kaunas", "tallinn", "warsaw"]


def test_slash_separated_unsupported_market_candidates_split() -> None:
    candidates = _extract_market_entity_candidates("price for oil/copper/madeupcoin999")
    assert candidates == ["oil", "copper", "madeupcoin999"]


def test_the_original_benchmark_produces_exactly_seven_subtasks() -> None:
    """The exact text that started this engagement: 4 assets + 3 cities, slash-separated on both
    sides. Must produce 4 market subtasks and 3 SEPARATE weather subtasks, never one merged city
    string standing in for three requested locations."""
    text = "market prices for gold/silver/BTC/BNB + weather for Kaunas/Tallinn/Warsaw"
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.subtasks) == 7
    market_keys = {t.arguments["asset_key"] for t in plan.market_subtasks() if t.operation == "market_quote"}
    assert market_keys == {"gold", "silver", "bitcoin", "binancecoin"}
    weather_locations = {t.arguments["location"] for t in plan.weather_subtasks()}
    assert weather_locations == {"kaunas", "tallinn", "warsaw"}


def test_a_bare_slash_free_location_is_still_one_location() -> None:
    """The control: a location with no "/" at all must not be affected by the new delimiter."""
    locations = _extract_weather_locations("weather in Vilnius, Lithuania")
    assert locations == ["vilnius, lithuania"]


def test_sabotage_reverting_the_slash_delimiter_reproduces_the_collapse() -> None:
    """Proves the fix is load-bearing: without "/" in the split/join-word detection, the exact
    benchmark text collapses three cities into one bogus location again."""
    import re

    def _old_extract_weather_locations(query: str) -> list[str]:
        clean = re.sub(r"[\?\!\.]+", " ", str(query or "")).strip()
        clean = re.sub(r"[ \t]+", " ", clean)
        lowered = clean.lower()
        clause = lowered
        for pattern in (
            r"\b(?:weather|forecast|temperature|rain|snow|wind|humidity)\s+(?:in|for|at)\s+(.+)$",
            r"\b(?:what is|what's|tell me|show me)\s+the\s+(?:weather|forecast)\s+(?:in|for|at)\s+(.+)$",
            r"\b(?:in|for|at)\s+(.+)$",
        ):
            match = re.search(pattern, lowered)
            if match:
                clause = match.group(1).strip()
                break
        else:
            clause = lowered
        has_join_word = bool(re.search(r"\band\b|&|;", clause))
        parts = re.split(r",|;|\band\b|&", clause) if has_join_word else [clause]
        return [p.strip() for p in parts if p.strip()]

    reproduced = _old_extract_weather_locations("weather for Kaunas/Tallinn/Warsaw")
    assert reproduced == ["kaunas/tallinn/warsaw"]  # the bug, reproduced: one bogus location
    fixed = _extract_weather_locations("weather for Kaunas/Tallinn/Warsaw")
    assert fixed == ["kaunas", "tallinn", "warsaw"]  # the fix: three real ones
