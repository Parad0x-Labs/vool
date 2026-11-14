"""Two misses of the job 19 fold on the Mac (ff3afc71, real local model, fake HOME, fresh home).

- H2H memory set m-p11: with "my manager is Pieter and the quarterly review is on december 2" saved, "whats Pieter's
  last name?" was answered "Your manager's last name is Pieter." The saved facts never gave it.
- heldout2 g11: with "cancel the magazine subscription" saved, "what was i going to cancel?" was answered "I don't
  have that saved, and I looked through your other chats and projects": "going" was read as a term the fact must hold.
All names are invented.
"""
from __future__ import annotations

from core.agent_runtime.cross_chat_recall import (
    _asked_terms,
    as_question,
    cross_chat_recall,
    saved_memory_rows,
    untold_name_part,
)
from tests.test_a_saved_fact_answers_ahead_of_calendar_and_web_v08 import OWNER, _seed
from tests.test_memory_forget_resurrection_p0 import isolated_home  # isolated_home is a fixture


def test_a_last_name_never_given_is_said_to_be_untold(isolated_home):
    _seed("my manager is Pieter and the quarterly review is on december 2")
    # The recall lane still hands it on (test_a_question_no_other_lane_claims_goes_on_to_the_model); the agent then
    # says it was never told instead of letting the model guess.
    untold = untold_name_part("whats Pieter's last name?", session_id="ask", source_context=OWNER)
    assert untold is not None and untold.startswith("You haven't told me Pieter's last name."), untold
    assert "my manager is Pieter" in untold
    assert untold_name_part("whats Pieter's last name?", session_id="ask", source_context={"_owner_local": False}) is None


def test_a_last_name_that_is_saved_is_not_called_untold(isolated_home):
    _seed("our landlord is called Feodora Lask")
    assert untold_name_part("what is Feodora's last name?", session_id="ask", source_context=OWNER) is None


def test_going_to_is_the_frame_of_an_intention(isolated_home):
    assert _asked_terms(as_question("what was i going to cancel?")) == ("cancel",)
    assert "plan" in _asked_terms(as_question("what's the plan for saturday?"))
    _seed("to cancel the magazine subscription")
    assert saved_memory_rows("what was i going to cancel?", session_id="ask", source_context=OWNER)
    found = cross_chat_recall("what was i going to cancel?", session_id="ask", source_context=OWNER)
    assert found is None or not found.response.startswith("I don't have that saved"), found
