"""A whole-message tool call is still caught; a one-word answer followed by a parenthetical gloss is an answer."""
import pytest

from core.model_output_guard import answer_is_tool_invocation


@pytest.mark.parametrize("text", [
    'search_web("Berlin 7-day weather forecast")',
    'workspace.identity()',
    'weather_lookup(city="Riga")',
    "fetch_url('https://example.com')",
    'search_web("x").',
    'search({"query": "tide table for the north cove"})',
])
def test_a_message_that_is_a_call_is_a_tool_invocation(text):
    assert answer_is_tool_invocation(text)


@pytest.mark.parametrize("text", [
    "Lisbon (she also mentions the tram ride).",
    "Harbour market (the noisy one near the pier that Ines described).",
    "Swimming (twice a week).",
    "Oslo (in the spring).",
])
def test_a_glossed_short_answer_is_not_a_tool_invocation(text):
    assert not answer_is_tool_invocation(text)
