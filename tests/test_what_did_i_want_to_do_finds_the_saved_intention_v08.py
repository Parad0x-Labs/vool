""""what did i want to do for Pickle?" finds "book the vet for Pickle", never "I don't have that saved".

Measured on the real local model (held-out set, fresh home): after "keep in mind to book the vet for Pickle" was saved,
"what did i want to do for Pickle?" was answered "I don't have that saved, and I looked through your other chats". The
recall lane counted "want" as a term the saved fact had to hold. All names are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import _asked_terms, as_question, saved_memory_answers
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


@pytest.mark.parametrize("asked", ["what did i want to do for Pickle?", "what was I supposed to do for Pickle?",
                                   "what did i need to do for pickle"])
def test_the_intention_frame_is_not_a_term(isolated_home, asked):
    assert _asked_terms(as_question(asked)) == ("pickl",)
    assert _say("keep in mind to book the vet for Pickle", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert saved_memory_answers(asked, session_id="ask", source_context={})


def test_a_want_question_with_no_other_subject_keeps_its_terms():
    assert _asked_terms(as_question("what do i want?")) == ("want",)
