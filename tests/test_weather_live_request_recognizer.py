"""`_looks_like_live_weather_request` tolerates a filler word between subject and preposition.

Found live: "Weather only for Berlin and Copenhagen." fell through to the model (which produced an
undelivered promise, not an answer) because the recognizer required the preposition immediately
after the subject word ("weather in/for/at...") with no word allowed in between.
"""

from __future__ import annotations

from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request


def test_weather_only_for_is_recognized() -> None:
    assert _looks_like_live_weather_request("weather only for berlin and copenhagen") is True


def test_weather_just_for_is_recognized() -> None:
    assert _looks_like_live_weather_request("weather just for london") is True


def test_weather_specifically_for_is_recognized() -> None:
    assert _looks_like_live_weather_request("weather specifically for paris") is True


def test_ordinary_phrasings_still_recognized_unchanged() -> None:
    for text in ("weather in kaunas", "weather for london", "current weather", "what's the forecast today"):
        assert _looks_like_live_weather_request(text) is True, text


def test_unrelated_prose_is_not_recognized() -> None:
    for text in ("the weather has been nice lately", "I love rainy weather", "weather patterns are complex"):
        assert _looks_like_live_weather_request(text) is False, text


def test_sabotage_removing_the_filler_tolerance_reproduces_the_incident() -> None:
    """Proves the fix is load-bearing: the ORIGINAL pattern (no filler tolerance) genuinely misses
    the incident phrasing -- this is not a tautological test."""
    import re

    original_pattern = re.compile(
        r"\b(?:"
        r"(?:weather|forecast|temperature|humidity|sunrise|sunset)\s+"
        r"(?:in|for|at|today|tomorrow|tonight|now|currently|this\s+(?:morning|afternoon|evening|weekend))"
        r"|(?:current|today(?:'s)?|tomorrow(?:'s)?|tonight(?:'s)?)\s+"
        r"(?:weather|forecast|temperature|humidity)"
        r"|(?:will\s+it|is\s+it|is\s+there|do\s+we)\s+"
        r"(?:rain|snow|freeze|storm|be\s+windy)"
        r"|(?:rain|snow|wind)\s+(?:in|for|at)\s+"
        r")\b",
        re.IGNORECASE,
    )
    incident_text = "weather only for berlin and copenhagen"
    assert original_pattern.search(incident_text) is None  # the bug, reproduced
    assert _looks_like_live_weather_request(incident_text) is True  # the fix
