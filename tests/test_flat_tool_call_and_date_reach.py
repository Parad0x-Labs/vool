"""Guards for two defects found in live manual testing.

1. A FLAT tool call -- {"action": "read_file", "path": "RESEARCH_STATUS.md"} -- reached the user
   verbatim. The detector required a nested args object, so a call carrying its arguments as SIBLING
   keys was not a "tool call" as far as the guard was concerned.
2. "what date is today?" was answered "I don't have a real-time clock" by a cloud model, while the
   runtime has a deterministic clock. The date matcher was a literal marker list: 5 of 10 natural
   phrasings missed, including that one.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import date_time_fast_path
from core.model_output_guard import foreign_markers, scrub_foreign_markers

# --- flat tool-call leak ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "leak",
    [
        '{"action": "read_file", "path": "RESEARCH_STATUS.md"}',   # the exact observed leak
        '{"intent": "workspace.read_file", "path": "x.md"}',
        '{"action": "web.search", "query": "btc price"}',
        '{"tool": "bash", "args": {"cmd": "ls"}}',                 # nested form still caught
    ],
)
def test_flat_tool_calls_are_detected(leak):
    assert foreign_markers(leak), f"{leak} reaches the user verbatim"
    assert scrub_foreign_markers(leak) == ""


@pytest.mark.parametrize(
    "clean",
    [
        '{"name": "myapp", "version": "1.0.0"}',        # package.json
        '{"name": "my_app", "version": "1.0"}',         # snake_case name, but no argument sibling
        '{"summary": "done", "bullets": ["a", "b"]}',   # a real structured answer
        '{"status": "ok", "count": 3}',
        '{"command": "the build succeeded"}',           # not a tool identifier
        '{"title": "read_file", "note": "a doc"}',      # tool-ish value, but title/note are not args
    ],
)
def test_ordinary_json_is_not_swept_up(clean):
    assert foreign_markers(clean) == [], f"false positive on {clean}"


def test_prose_then_flat_call_keeps_the_prose():
    text = 'Let me read that file.\n\n{"action": "read_file", "path": "RESEARCH_STATUS.md"}'
    assert foreign_markers(text)
    assert "Let me read that file." in scrub_foreign_markers(text)
    assert "read_file" not in scrub_foreign_markers(text)


# --- date fast path ----------------------------------------------------------------------------

DATE_PHRASINGS = [
    "what date is today?", "what is the date today", "whats the date", "what day is it",
    "todays date", "what is today's date?", "what is the date", "tell me the date",
    "what day is today", "what is today",
    # Weekday phrasings. Found live: "which day of the week are we on right now?" fell through to the
    # model, which answered "We're on a Wednesday" on a Tuesday. Every one of these needs a
    # present-tense subject (it / today / we), which is what keeps the negatives below out.
    "which day of the week are we on right now?", "which day of the week is it?",
    "what day of the week is it today?", "what day are we on?", "what day of the week are we on",
    "which day is it?", "what's today?", "what day of the week is this",
]

NOT_A_DATE_QUESTION = [
    "what is the release date of python 3.13",
    "whats the due date for this task",
    "update the date field in config.json",
    "the date format is wrong",
    "what day is the meeting?",
    "what day are you free?",
    "what day of the week was I born?",
    "which day should we ship?",
    "what day works for you?",
]


@pytest.mark.parametrize("phrasing", DATE_PHRASINGS)
def test_date_is_answered_deterministically(phrasing):
    answer = date_time_fast_path(None, phrasing, source_surface="openclaw")
    assert answer, f"{phrasing!r} falls through to a model that has no clock"
    assert "Today is" in answer


@pytest.mark.parametrize("phrasing", NOT_A_DATE_QUESTION)
def test_date_shaped_prose_is_not_hijacked(phrasing):
    assert date_time_fast_path(None, phrasing, source_surface="openclaw") is None


def test_measured_reach():
    hits = sum(1 for p in DATE_PHRASINGS if date_time_fast_path(None, p, source_surface="openclaw"))
    assert hits == len(DATE_PHRASINGS), f"date fast-path reach fell to {hits}/{len(DATE_PHRASINGS)}"
    false_positives = sum(1 for p in NOT_A_DATE_QUESTION if date_time_fast_path(None, p, source_surface="openclaw"))
    assert false_positives == 0
