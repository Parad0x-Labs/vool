""""where does Tobiah live?" is answered by the saved fact, not read as a live-data question.

Measured on the real local model (held-out set, fresh home): with "my brother Tobiah lives in Quillon" saved, "where does
Tobiah live?" was asked back "the biblical Tobiah, the historical one, or a modern person?": "live" was one of the
now-words that keep a saved fact from answering, so the entity check did not stand down. All names are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import saved_memory_answers
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


def test_where_someone_lives_is_answered_by_the_saved_fact(isolated_home):
    assert _say("keep in mind that my brother Tobiah lives in Quillon", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert saved_memory_answers("where does Tobiah live?", session_id="ask", source_context={})
    assert saved_memory_answers("where does my brother Tobiah live?", session_id="ask", source_context={})


@pytest.mark.parametrize("asked", ["is the Quillon match live?", "what are the live scores for Quillon?",
                                   "where does Tobiah live now?"])
def test_a_live_or_now_question_is_still_not_a_saved_fact_question(isolated_home, asked):
    assert _say("keep in mind that my brother Tobiah lives in Quillon and plays for the Quillon match team", "seed")[0]
    _policy("ask")
    assert not saved_memory_answers(asked, session_id="ask", source_context={})
