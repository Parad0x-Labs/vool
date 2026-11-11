"""The clock fast path answers every place a two-city ask names, including abbreviations.

Measured 2026-11-03 (build 8af687ca, served chats): "What time in NY now?" resolved no zone
(the tzdb table knows "new york", no abbreviation), the fail-closed rule declined the lane, and
a model answered the user's local clock labeled "New York (EST)" — the exact confident-wrong
answer the fail-closed law exists to prevent. Two-city asks fared no better: "time in riga and
NY now" answered Riga only in the served trace; "what time is it in new york and vilnius now?"
resolved New York and dropped Vilnius, because the coordinated second city arrives without its
own "in" and the locative matcher only reads phrase-final places.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as _tz

from core.agent_runtime.fast_paths_utility import (
    date_time_fast_path,
    extract_utility_timezone,
    extract_utility_timezones_all,
)

_NOW = datetime(2026, 11, 4, 17, 26, tzinfo=_tz.utc)


def _fast_path(text: str) -> str | None:
    return date_time_fast_path(object(), text, source_surface="channel", session_id="s", now_utc=_NOW)


def test_ny_abbreviation_resolves_to_new_york() -> None:
    assert extract_utility_timezone("what time in ny now?") == ("America/New_York", "New York")
    answer = _fast_path("what time in ny now?")
    assert answer is not None
    assert "New York" in answer and "12:26" in answer


def test_nyc_abbreviation_resolves_to_new_york() -> None:
    assert extract_utility_timezone("what time is it in nyc?") == ("America/New_York", "New York")


def test_abbreviation_is_never_matched_inside_a_word() -> None:
    assert extract_utility_timezone("what time is it in many places?") == ("", "")
    assert extract_utility_timezones_all("what time is it in many places?") == []


def test_two_city_ask_answers_every_named_place() -> None:
    answer = _fast_path("time in ny and vilnius now")
    assert answer is not None
    assert "New York" in answer and "Vilnius" in answer
    assert "12:26" in answer and "19:26" in answer


def test_bare_second_city_without_its_own_in_is_not_dropped() -> None:
    assert [label for _zone, label in extract_utility_timezones_all(
        "what time is it in new york and vilnius now?")] == ["New York", "Vilnius"]


def test_served_failure_shape_riga_and_ny_now_answers_both() -> None:
    answer = _fast_path("time in riga and NY now")
    assert answer is not None
    assert "Riga" in answer and "New York" in answer


def test_prose_after_a_coordinated_city_stays_unresolved() -> None:
    """Strict whole-remainder matching: a coordinated clause that is not a place never resolves.

    The real place the ask names still resolves — only the prose half stays out.
    """
    resolved = extract_utility_timezones_all("what time is it in tokyo and the market now?")
    assert [label for _zone, label in resolved] == ["Tokyo"]


def test_single_city_and_non_clock_asks_keep_their_previous_behaviour() -> None:
    assert _fast_path("what time is it in tokyo?") is not None
    assert _fast_path("what is capital of lithuania?") is None
    assert _fast_path("release date of the album?") is None
