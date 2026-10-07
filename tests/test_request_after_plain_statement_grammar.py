"""An imperative after a plain statement is a request; history, questions and quotes are not.

The requested-action grammar (requested_action_clauses) required an established request before a
clause-initial imperative, so "you are acting weird but create a hello world file ..." -- a plain
remark followed by an ordinary command -- wrote nothing. A "can't you ..." lead and a detached
period ("save it as . txt") cut the request the same way. The conservative history law stays: an
imperative after a description of past actions, after another request, inside a question, or
inside quoted text is not claimed.
"""

import pytest

from core.agent_runtime.fast_paths_machine import (
    _AFFIRMATIVE_MACHINE_WRITE_RE,
    _extract_machine_transcript_export_target,
    _has_affirmative_machine_write_verb,
)
from core.instructional_request import requested_action_clauses

CLAIMED = (
    # original reported wordings
    "you are acting weird but create hello world file and save it as .txt in Marchtest folder",
    "you are acting weird but on my desktop make VOOLProbe and save hello_world.txt with text hello world",
    # clean paraphrases, different vocabulary and shape
    "This is taking forever, but make a notes file on my Desktop",
    "I'm tired today, but create a folder called drafts on my Desktop",
    "The last answer was wrong but write a file called todo.txt on my Desktop with text buy oats",
    "It's raining in Tallinn but make a folder named Trips in Documents",
    "My laptop is slow but save a file called log.txt on my Desktop",
    # sloppy, user-typed variants
    "ur being weird but make folder on desktop called stuff",
    "ugh this is broken but create file test.txt on desktop",
    "ok whatever but save notes.txt on my desktop",
    "this sucks but make a folder on desktop pls",
    "you r slow but write hi.txt on my Desktop with text hi",
    # negative-question request lead
    "can't you save notes.txt on my desktop?",
    "couldn't you make a folder called Ledger on my Desktop",
    "cant u save quotes.txt on my desktop",
)

NOT_CLAIMED = (
    # history and description stay conservative (existing law)
    "Tell me what I saved yesterday, then create a folder on my Desktop",
    "What did I make on my Desktop yesterday?",
    "I remember Morgan asking us to create a folder on the Desktop",
    # questions, including an auxiliary-led question without its question mark
    "Did you create a folder on my Desktop and make a file",
    "you are acting weird but did you create the folder on my Desktop",
    # the negated request keeps its negation in front of the verb
    "could you not make a folder on my Desktop",
    "you are acting weird but don't create anything on my Desktop",
    # quoted text is reported, not requested
    "> I'm bored.\n> Then create a folder on my Desktop.",
)


@pytest.mark.parametrize("text", CLAIMED)
def test_an_imperative_after_a_plain_statement_is_a_write_request(text: str) -> None:
    assert _has_affirmative_machine_write_verb(text)


@pytest.mark.parametrize("text", NOT_CLAIMED)
def test_history_questions_negations_and_quotes_are_not_write_requests(text: str) -> None:
    assert not _has_affirmative_machine_write_verb(text)


def test_a_detached_period_does_not_end_the_request() -> None:
    clauses = requested_action_clauses(
        "create hello world file and save it as . txt in Marchtest folder", _AFFIRMATIVE_MACHINE_WRITE_RE,
    )
    assert any("Marchtest folder" in clause for clause in clauses)
    # an attached period still ends a sentence, so a description after it is not swallowed
    clauses = requested_action_clauses(
        "create a file called a.txt on my Desktop. What did Morgan save there?", _AFFIRMATIVE_MACHINE_WRITE_RE,
    )
    assert clauses and all("Morgan" not in clause for clause in clauses)


@pytest.mark.parametrize("text", (
    "can't you export this all chat as .txt to Marchtest folder that is on desktop?",
    "this is slow but export this chat as .txt to Documents",
    "cant u dump this conversation to Documents as notes.txt",
))
def test_a_transcript_export_request_after_a_lead_or_remark_names_its_destination(text: str) -> None:
    assert _extract_machine_transcript_export_target(text)


@pytest.mark.parametrize("text", (
    "Did Morgan export our conversation to Desktop?",
    "you are acting weird but did you export this chat to Documents",
))
def test_a_question_about_an_export_is_not_an_export(text: str) -> None:
    assert _extract_machine_transcript_export_target(text) == ""
