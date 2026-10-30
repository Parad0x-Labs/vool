""""whats on my desktop right now?" reads the machine's own storage; the world's files keep their lookup.

The hand-back of phone-reply-fix-20261028 stood any "what's ... files/documents" ask down from grounding, which also
turned current-news asks about the world's documents into a no-lookup answer (review 2026-10-28).
"""
from __future__ import annotations

import pytest

from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for


@pytest.mark.parametrize("text", ["whats on my desktop right now?", "whats in my downloads right now?",
                                  "show me the downloads folder now", "any new photos in downloads right now?"])
def test_the_owners_own_files_are_read_not_looked_up(text):
    assert answer_mode_for(text) is AnswerMode.DIRECT


@pytest.mark.parametrize("text", ["show me the latest documents the court published today",
                                  "check the new JFK files right now, what do they say?",
                                  "list the documents released by the White House this week",
                                  "what's the current price of bitcoin?"])
def test_the_worlds_files_and_news_keep_their_lookup(text):
    assert answer_mode_for(text) is AnswerMode.GROUNDED
