from __future__ import annotations

from core.agent_runtime.fast_paths_utility import date_time_fast_path


def _fires(text: str) -> bool:
    return date_time_fast_path(None, text, source_surface="api", session_id="t") is not None


def test_current_time_queries_still_fire() -> None:
    for q in (
        "what time is it",
        "what's the time",
        "what is the current time",
        "what time is it in tokyo",
        "current time",
        "time now",
    ):
        assert _fires(q), q


def test_event_or_possessive_time_does_not_fire() -> None:
    # Regression: "what time is my flight now" and "my time off ... what role" previously matched
    # the broad "time + what/now" rule and returned "Current time is HH:MM" instead of answering.
    for q in (
        "what time is my flight now",
        "priya just approved my time off. what role does she have relative to me?",
        "what is the meeting time",
        "when is the flight departure time",
    ):
        assert not _fires(q), q
