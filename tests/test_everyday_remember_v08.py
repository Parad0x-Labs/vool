"""Remember asks in everyday words: what is saved, what replaces what, and what is never stored.

Measured on the v0.8 line (f4f048f7 in the head-to-head, again on 066ac524 with a fresh home): an everyday ask
("hey, few things to remember for me pls. ...", "work stuff to keep in mind: ...") was answered "Got it -- noted"
and nothing reached memory; the facts said on the next turns of that list were lost; an update said in a later chat
("small update: ... is now ...") was never kept; a question ("remind me what my manager is called") was indexed as
a confirmed memory; and a saved fact was answered "Nothing was saved" when a planner call ran in the same turn.
Recall of a saved fact failed two more ways: "what's my manager's name?" was answered "I don't have that saved",
and "who is my dentist?" was refused as a web lookup.

All names, places and dates here are invented for this contract.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from core.memory import everyday_remember as everyday
from core.memory.entries import list_memory_entries
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

CHAT = "everyday-remember-chat"
LATER_CHAT = "everyday-remember-later"


def _say(text: str, chat: str = CHAT) -> tuple[bool, str]:
    handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat))
    append_conversation_event(
        session_id=chat, user_input=text, assistant_output=str(reply or "ok"), persist_now=True,
        access_policy=_policy(chat),
    )
    return handled, str(reply or "")


def _facts(chat: str = CHAT) -> list[str]:
    rows = list_memory_entries(access_policy=_policy(chat), limit=200)
    return [str(row.get("fact") or row.get("text") or "") for row in rows]


# --- the recognizers -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fact", "opens_list"),
    [
        ("ok so a couple of things for you to remember: my locker is 214", "my locker is 214", True),
        ("garden stuff to keep in mind: the tomatoes go in after 20 may", "the tomatoes go in after 20 may", True),
        ("keep in mind that I can't eat shellfish", "I can't eat shellfish", False),
        ("could you remember this for me: the boiler was serviced in june", "the boiler was serviced in june", False),
        ("note this down - the alarm code changes monthly", "the alarm code changes monthly", False),
        ("for future reference, the bike pump is in the shed", "the bike pump is in the shed", False),
        ("a few things to note down", "", True),
        ("keep in mind to water the ferns on sundays", "water the ferns on sundays", False),
    ],
)
def test_an_everyday_lead_carries_its_fact(text, fact, opens_list):
    lead = everyday.everyday_lead(text)
    assert lead is not None
    assert lead.fact == fact
    assert lead.opens_list is opens_list


@pytest.mark.parametrize(
    "text",
    [
        "make a note to water the ferns",  # a reminder, another lane
        "what should I keep in mind when buying a kayak?",
        "my brother moved to Ghent",
        "write me a poem about things to remember",
    ],
)
def test_other_text_is_not_an_everyday_lead(text):
    assert everyday.everyday_lead(text) is None


def test_a_list_stays_open_only_across_statements():
    opener = "few things to remember for me: my locker is 214"
    assert everyday.list_is_open(["also our cat is called Brindle", opener])
    assert not everyday.list_is_open(["can you tell me a joke", opener])
    assert not everyday.list_is_open(["what's the capital of Peru?", opener])


@pytest.mark.parametrize(
    "text",
    ["remind me what my manager is called", "what time does my class start?", "do you know my locker number"],
)
def test_a_question_is_a_question_without_its_mark(text):
    assert everyday.is_question(text)


def test_an_update_finds_the_fact_it_replaces():
    saved = [
        {"fact": "our book club meets on thursdays at the library"},
        {"fact": "my locker is 214 in the east wing"},
    ]
    picked = everyday.updated_entry("quick update: book club moved to wednesdays", saved)
    assert picked is saved[0]
    assert everyday.updated_entry("quick update: the weather changed", saved) is None


# --- the memory lane, in process -------------------------------------------------------------------------------


def test_an_everyday_list_is_saved_turn_by_turn(isolated_home):
    handled, reply = _say("hey, couple of things to remember for me pls. my locker is 214 in the east wing")
    assert handled and "Locked in" in reply
    handled, reply = _say("also our book club meets on thursdays at the library")
    assert handled and "Locked in" in reply
    handled, _ = _say("can you tell me a joke")
    assert not handled
    handled, _ = _say("my bike is a green Gazelle")  # the joke closed the list
    assert not handled
    facts = _facts()
    assert "my locker is 214 in the east wing" in facts
    assert "our book club meets on thursdays at the library" in facts
    assert not any("Gazelle" in fact or "joke" in fact for fact in facts)


def test_a_lead_alone_opens_the_list(isolated_home):
    handled, reply = _say("a few things to note down")
    assert handled and reply == "Go ahead. I'll remember what you tell me."
    handled, _ = _say("my dentist is Dr. Vasquez on Mill Lane")
    assert handled
    assert "my dentist is Dr. Vasquez on Mill Lane" in _facts()


def test_the_lead_words_never_end_up_in_the_fact(isolated_home):
    assert _say("keep in mind that I can't eat shellfish")[0]
    assert _say("note this down: the boiler was serviced in june")[0]
    assert _say("keep in mind to buy milk tomorrow")[0]
    facts = _facts()
    assert "I can't eat shellfish" in facts
    assert "the boiler was serviced in june" in facts
    assert "buy milk tomorrow" in facts
    assert not any(fact.startswith(("in mind", "this down")) for fact in facts)


def test_an_update_in_a_later_chat_replaces_the_saved_fact(isolated_home):
    assert _say("garden stuff to keep in mind: our book club meets on thursdays at the library")[0]
    handled, reply = _say("small update: book club moved to wednesdays, same place", chat=LATER_CHAT)
    assert handled
    assert reply.startswith("Updated. I now have: book club moved to wednesdays, same place.")
    facts = _facts(LATER_CHAT)
    assert any(fact.startswith("book club moved to wednesdays, same place") for fact in facts)
    assert "our book club meets on thursdays at the library" not in facts


def test_a_question_is_never_saved(isolated_home):
    assert _say("couple of things to remember: my manager is Ottoline")[0]
    handled, _ = _say("remind me what my manager is called")
    assert not handled
    handled, _ = _say("what's my manager's name?")
    assert not handled
    assert _facts() == ["my manager is Ottoline"]


def test_an_update_with_nothing_saved_is_not_this_lanes(isolated_home):
    handled, _ = _say("small update: the ferry now leaves at 7")
    assert not handled
    assert _facts() == []


# --- the reply survives the claim gate -------------------------------------------------------------------------


def test_the_claim_gate_keeps_a_recorded_render_and_only_that():
    from core.action_claim_gate import gate_action_claims
    from core.agent_runtime.response import record_deterministic_render
    from core.final_answer_authorship import model_authored_turn
    from core.turn_model_call_ledger import begin_turn, record_provider_call, record_provider_call_outcome

    reply = "Locked in. I’ll remember that."
    context: dict[str, Any] = {}
    begin_turn(context)
    call = record_provider_call(context, provider_id="ollama-local", model_id="planner", call_role="planner")
    assert record_provider_call_outcome(context, call, outcome="completed")  # the clause-split planner ran
    assert model_authored_turn(turn_id="everyday-gate", source_context=context)[0]
    record_deterministic_render(context, reply, route="memory_command")
    kept, record = gate_action_claims(reply, turn_id="everyday-gate", source_context=context)
    assert kept == reply and record["publication"] == "stood_down:recorded_runtime_render"
    model_text = "I'll remember that your locker is 214."
    gated, record = gate_action_claims(model_text, turn_id="everyday-gate", source_context=context)
    assert record["publication"] != "stood_down:recorded_runtime_render"


# --- recall of a saved fact ------------------------------------------------------------------------------------


def test_the_name_word_is_the_answer_slot_not_a_term():
    from core.agent_runtime.cross_chat_recall import _asked_terms, as_question

    assert _asked_terms(as_question("what's my manager's name?")) == ("manager",)
    assert _asked_terms(as_question("what is my dentist called?")) == ("dentist",)
    assert _asked_terms(as_question("what's my name?"))  # a name alone is still asked for


@pytest.mark.parametrize(
    ("text", "public"),
    [
        ("who is my dentist?", False),
        ("who's my sister's husband", False),
        ("remind me who my manager is", False),
        ("who is my MP?", True),
        ("who is my local councillor?", True),
        ("who is my favourite actor married to?", True),
        ("who is the mayor of Riga?", True),
    ],
)
def test_who_is_my_x_is_not_a_public_lookup(text, public):
    from core.execution_requirements import requirements_for

    assert bool(requirements_for(text).current_information_required) is public


# --- served: the running app, a fresh home, the chat page's own streamed turns ----------------------------------

MODEL = "stub-private:4b"


def _stream(base_url: str, text: str, session: str) -> str:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session, "model": MODEL}
    request = Request(f"{base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    return str(commit.get("canonical_content") or answer)


def test_served_everyday_remember_list_update_and_question(tmp_path: Path):
    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_private_chat_served_workflow import _certify, _Provider

    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Sure, noted."})
    chats = [
        ["garden stuff to keep in mind: the allotment gate code is 5512",
         "my plot neighbour is Wilhelmina and the harvest fair is on september 9",
         "also i always water before 8am"],
        ["quick update: the allotment gate code is now 6620"],
        ["remind me what my plot neighbour is called"],
    ]
    replies: list[tuple[str, str]] = []
    with provider:
        run_in_home(home, SEED.format(root=repo, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            for n, turns in enumerate(chats):
                for text in turns:
                    replies.append((text, _stream(daemon.base_url, text, f"everyday-served-{n}")))
            with urlopen(f"{daemon.base_url}/api/memory/entries?limit=200", timeout=60) as response:
                facts = [str(row.get("fact") or "") for row in json.load(response).get("entries") or []]
    for text, reply in replies[:4]:
        assert "Nothing was saved" not in reply, (text, reply)
    assert replies[1][1].startswith("Locked in."), replies[1]
    assert replies[3][1].startswith("Updated. I now have: the allotment gate code is now 6620"), replies[3]
    assert "my plot neighbour is Wilhelmina and the harvest fair is on september 9" in facts
    assert "i always water before 8am" in facts
    assert any(fact.startswith("the allotment gate code is now 6620") for fact in facts)
    assert "the allotment gate code is 5512" not in facts
    assert not any("remind me" in fact for fact in facts)
    db = next(home.rglob("vool_memory.db"))
    with sqlite3.connect(str(db)) as connection:
        nodes = [str(row[0]) for row in connection.execute("select content from memory_nodes")]
    assert not any("remind me what my plot neighbour" in node for node in nodes), nodes
