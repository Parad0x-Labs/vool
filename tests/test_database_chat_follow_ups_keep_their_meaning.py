"""Follow-ups in a database chat keep their meaning: Database run 16b on the Mac (2026-11-12, qwen3:8b).

Three chat-line faults from that run's prompt capture, each with its measured wording first:
- The resolver read "list the members, newest first" as a pointer into an unprinted list and "hey what's
  the oldest book in there" as a pointer at an unresolved folder. Its notes ("ask which list or which item",
  "ask which folder") reached the prompt, the model replied "The message refers to a list of ...", and that
  reply then sat in the history and the recall records for the next asks. Every new ask also carried
  "Earlier in this chat the user asked: ... It ended failed." about an unrelated request.
- "ok and how many are still out on loan" and "sorry, what was the oldest book again?" spent a planner call
  and were answered by the conductor from the earlier reply, with no tool.
- "does Yara have anything checked out right now" ran the database query, then the answer was withheld
  because "the pages I found don't state it": "right now" read as a request for the web.
"""
from __future__ import annotations

import json

import pytest

from core import conversation_state as cs

DB_RECORD = cs.PendingRequest("how many titles in the books tabel by Murakami", cs.FAILED, "",
                              {"tool": "vool-database.query"}, 0)
FOLDER_RECORD = cs.PendingRequest("list the pics in my downloads", cs.REFUSED, "blocked_by_mode",
                                  {"tool": "machine.list_directory"}, 0)
LISTED_RECORD = cs.PendingRequest("list the pics in my downloads", cs.DONE, "",
                                  {"tool": "machine.list_directory",
                                   "listed": [{"path": "/d/a.jpg"}, {"path": "/d/b.jpg"}]}, 0)


@pytest.mark.parametrize("asked", ["list the members, newest first", "show the loans, oldest first",
                                   "list every book, most recent first", "sort the members alphabetically first"])
def test_an_order_word_before_first_is_an_order_not_a_pointer(asked):
    resolution = cs.resolve("db-chat", asked, pending=DB_RECORD, cleared=None)
    assert resolution.kind == "none", resolution


@pytest.mark.parametrize("asked", ["hey what's the oldest book in there", "anything by Austen in there?",
                                   "what else is in there"])
def test_in_there_after_a_database_ask_is_not_a_folder(asked):
    resolution = cs.resolve("db-chat", asked, pending=DB_RECORD, cleared=None)
    assert resolution.kind == "none", resolution
    assert "folder" not in resolution.note


def test_a_folder_record_and_a_named_folder_still_ask_which_folder():
    assert cs.resolve("c", "what is in there", pending=FOLDER_RECORD, cleared=None).kind == "referent_unresolved"
    assert cs.resolve("c", "grab 3 newest ones in that folder", pending=DB_RECORD, cleared=None).kind == "referent_unresolved"
    assert cs.resolve("c", "what is in there", pending=None).kind == "referent_unresolved"


def test_a_printed_list_is_still_picked_from():
    assert cs.resolve("c", "open the last one", pending=LISTED_RECORD, cleared=None).kind == "pick"
    assert cs.resolve("c", "the second one", pending=LISTED_RECORD, cleared=None).kind == "pick"


@pytest.mark.parametrize("asked", ["is Ines holding any books at the moment", "which member joined most recently",
                                   "has Tomas given everything back"])
def test_a_new_ask_does_not_carry_how_an_unrelated_request_ended(asked):
    resolution = cs.resolve("db-chat", asked, pending=DB_RECORD, cleared=None)
    assert resolution.kind == "none" and resolution.note == "", resolution


@pytest.mark.parametrize("asked", ["why did it fail?", "what went wrong", "ok"])
def test_a_follow_up_about_the_last_request_still_gets_how_it_ended(asked):
    resolution = cs.resolve("db-chat", asked, pending=DB_RECORD, cleared=None)
    assert "It ended failed" in resolution.note, resolution


@pytest.mark.parametrize("asked", ["ok and how many are still out on loan", "sorry, what was the oldest book again?",
                                   "ok, which member joined most recently"])
def test_a_courtesy_before_one_question_buys_no_planner_call(asked):
    from core.agent_runtime.turn_planner import plan_turn
    from core.conductor.planner import plan_conductor_turn

    calls: list[str] = []

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return json.dumps([{"request": asked, "operation": "factual_explanation", "depends_on": []}])

    assert plan_conductor_turn(asked, ask_model=ask_model, plan_id="t") is None
    assert plan_turn(asked, ask_model=ask_model) == []
    assert calls == []


@pytest.fixture()
def database_chat(monkeypatch):
    import core.database_work_state as state

    monkeypatch.setattr(state, "_registered", lambda intents: tuple(intents))
    monkeypatch.setattr(state, "_ASKED", {})
    state.note_database_demand({"runtime_session_id": "db-chat"})
    return state


@pytest.mark.parametrize(("asked", "current_elsewhere"), [("does Yara have anything checked out right now", True),
                                                          ("is Ines holding any books at the moment", False),
                                                          ("how many books are out on loan right now", True)])
def test_right_now_in_a_database_chat_is_the_database_not_the_web(asked, current_elsewhere, database_chat):
    from core.execution_requirements import requirements_for

    requirements = requirements_for(asked, source_context={"runtime_session_id": "db-chat"})
    assert requirements.answer_mode == "DIRECT", requirements
    assert requirements.current_information_required is False, requirements
    # Outside a database chat the same words keep today's reading.
    elsewhere = requirements_for(asked, source_context={"runtime_session_id": "another-chat"})
    assert elsewhere.current_information_required is current_elsewhere, elsewhere


@pytest.mark.parametrize("asked", ["whats the weather in Vilnius right now", "what is the latest news about libraries",
                                   "search the web for the current price of a library card",
                                   "whats the bitcoin price right now"])
def test_a_world_ask_in_a_database_chat_keeps_its_lookup(asked, database_chat):
    from core.execution_requirements import requirements_for

    requirements = requirements_for(asked, source_context={"runtime_session_id": "db-chat"})
    assert requirements.current_information_required is True, requirements
