"""A final answer that quotes page text which wraps across lines is grounded in that page.

Found on the real app (2026-11-11): the browser opened a page whose heading is split over three
lines ("the web,\\nwithout the\\nrent."). The model answered with the heading, and the runtime
rejected the answer as ungrounded, because the observation was handed to the overlap check as JSON,
where each line break became a backslash and "n" glued to the next word.
"""
from __future__ import annotations

from core.agent_runtime.research_tool_loop_facade import _observation_words
from core.model_output_guard import is_ungrounded

PAGE = {"ok": True, "status": "executed", "url": "https://example.org/",
        "page": {"title": "Harbor", "text": "Harbor\nsearch\nthe web,\nwithout the\nrent.\n\nTimetables below."},
        "nodes": {"items": [{"label": "Departures", "tag": "a"}]}}


def test_an_answer_quoting_a_wrapped_heading_is_grounded():
    answer = 'The page heading is "the web, without the rent."'
    assert is_ungrounded(answer, [_observation_words(PAGE)]) is False


def test_an_answer_that_ignores_the_page_is_still_ungrounded():
    answer = "Paris is the capital of France and has many museums to visit."
    assert is_ungrounded(answer, [_observation_words(PAGE)]) is True


def test_every_key_and_value_of_a_nested_observation_is_kept():
    words = _observation_words(PAGE)
    for token in ("status", "executed", "without", "Timetables", "Departures", "items"):
        assert token in words, token
    assert "\\n" not in words
