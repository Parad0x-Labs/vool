"""Housekeeping over the operator's own chats: find by content, pin, rename, move, and the
plain-words reading of those asks.

Transcripts are seeded through the production writer (``append_conversation_event``) and governed
payloads through the production finalization seams, never hand-built reader rows. Each test pins
one failure the feature must not have: a false "done", a guess on an ambiguous ask, a governed or
deleted chat leaking into results, a sentence about something else being taken over.
"""
from __future__ import annotations

import json
import types

import pytest

from core import chat_housekeeping as hk
from core import runtime_paths
from core.agent_runtime import housekeeping_lane as lane

CURRENT = "openclaw:hk000000000000000000cur"
STAKING = "openclaw:hk000000000000000000stk"
GARDEN = "openclaw:hk000000000000000000grd"
WALLET = "openclaw:hk000000000000000000wal"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _seed(session: str, user: str, assistant: str) -> None:
    from core.persistent_memory import append_conversation_event

    append_conversation_event(
        session_id=session, user_input=user, assistant_output=assistant,
        source_context={"surface": "api", "platform": "api"},
    )


def _seed_world() -> None:
    _seed(STAKING, "how do I stake SOL with a validator?", "Pick a validator with low commission, then delegate from your wallet.")
    _seed(STAKING, "what about unstaking time?", "Deactivation takes one epoch, about two days.")
    _seed(GARDEN, "my tomatoes have yellow leaves", "Likely overwatering; let the soil dry between waterings.")
    _seed(WALLET, "how do I back up a wallet seed phrase", "Write it on paper twice and keep the copies apart.")
    _seed(CURRENT, "hello", "Hi! What can I do for you?")


def _plan(text: str):
    plan = lane.parse_housekeeping(text)
    assert plan is not None, text
    return plan


# ---------------------------------------------------------------------------------------------
# Reading the operator's words
# ---------------------------------------------------------------------------------------------


def test_the_owners_own_sentence_is_a_search_then_a_pin() -> None:
    plan = _plan(
        "vool i was talking or mentioning somethign or u did soemthing in the past about solana staking "
        "for me i dont remeber  where and what project or chat it was, can u find it for me and pin it?"
    )
    assert [a.kind for a in plan.actions] == ["search", "pin_chat"]
    assert plan.actions[0].value == "solana staking"   # request words and their typos are gone


@pytest.mark.parametrize(
    "text",
    [
        "find the file we discussed earlier",          # a file ask, not a chat ask
        "find where we use the logger in the code",
        "rename the variable to x",                    # no chat named
        "how do I rename the chat to something else?",  # asks HOW, does not ask to do it
        "what did we say about solana?",                # recall of content, not of location
        "rename this chat to Budget and explain inflation",  # two demands: left to the planners
        "where did you say the eiffel tower is?",        # a fact question, not a chat search
        "search the web for kubernetes tutorials",
        "look up the price of gold",
    ],
)
def test_sentences_about_something_else_are_never_claimed_whole(text: str) -> None:
    assert lane.parse_housekeeping(text) is None


@pytest.mark.parametrize(
    ("text", "query"),
    [
        # Missed by the first live run (2026-10-05): "dig it up", "i forget wich chat".
        ("hey vool, somewhere we talked about primer for the kitchen cupboards, i forget wich chat. dig it up and pin it",
         "primer kitchen cupboards"),
        ("where did I ask about the landlord and the heating? pin both", "landlord heating"),
        ("can you pull up that old chat where you helped me with the heating email", "heating email"),
        ("take me back to the conversation about back stretches", "back stretches"),
        ("I forgot which project the tax receipts chat was in", "tax receipts"),
    ],
)
def test_request_phrasing_never_becomes_search_terms(text: str, query: str) -> None:
    assert _plan(text).actions[0].value == query


def test_a_move_and_a_rename_in_one_sentence_are_two_actions() -> None:
    # Live run 3 (2026-10-05): the rename ran on into the project name and the move failed.
    plan = _plan("put this chat in the taxes 2026 project and call this chat Tidy-up log")
    assert [(a.kind, a.value) for a in plan.actions] == [("move_chat", "taxes 2026"), ("rename_chat", "Tidy-up log")]
    assert _plan("rename this chat to Sales and call notes").actions[0].value == "Sales and call notes"


def test_a_title_keeps_its_own_commas_and_ands() -> None:
    plan = _plan("rename this chat to Pin, Archive & Restore tips and pin it")
    assert [(a.kind, a.value) for a in plan.actions] == [("rename_chat", "Pin, Archive & Restore tips"), ("pin_chat", "")]
    assert _plan('Rename this caht to "Q3 plan, final" and archive it').actions[0].value == "Q3 plan, final"


def test_a_whole_housekeeping_message_binds_every_demand_unit() -> None:
    from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

    assert lane_may_claim_whole_turn("rename this chat to Budget 2026 and pin it", "chat_housekeeping")
    assert not lane_may_claim_whole_turn("rename this chat to Budget and explain inflation", "chat_housekeeping")


def test_a_pronoun_fragment_of_another_request_is_not_a_chat_rename() -> None:
    """"Name it X" after "create a folder on the desktop" names the folder, not this chat: claiming
    the fragment took the folder request off the approval lane (manual mode never asked)."""
    from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

    assert not lane.covers_unit("Name it NULLATEST")
    assert not lane_may_claim_whole_turn(
        "can you create a folder on the desktop for me? Name it NULLATEST", "chat_housekeeping"
    )
    assert lane.covers_unit("rename this chat to Budget 2026")
    assert lane.covers_unit("pin 2")


# ---------------------------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------------------------


def test_search_finds_the_chat_by_its_content_with_title_and_snippet() -> None:
    _seed_world()
    result = hk.search_chats("solana staking validator", current_session_id=CURRENT)
    assert result.ok and result.hits
    top = result.hits[0]
    assert top.session_id == STAKING
    assert top.title == "how do I stake SOL with a validator?"
    assert "validator" in top.snippet.lower()
    assert result.unique


def test_search_never_returns_a_deleted_chat() -> None:
    _seed_world()
    from core.memory.entries import delete_conversation_session

    assert delete_conversation_session(WALLET)
    result = hk.search_chats("wallet seed phrase")
    assert all(hit.session_id != WALLET for hit in result.hits)


def test_a_withheld_answer_is_neither_matched_nor_quoted() -> None:
    from core.finalization import AVAILABILITY_WITHHELD, finalize_answer, set_availability
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    secret = "The vault combination is kept with the zephyrine ledger."
    reset_admission()
    admit_semantic_result({"response": secret, "route_reason": "model_lane"}, turn_id="hk-withheld")
    token = set_request_context("req-hk-withheld-0001")
    try:
        commit = finalize_answer(turn_id="hk-withheld", canonical_content=secret)
        assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD, reason="hk-test")
        _seed(GARDEN, "where is the combination", secret)
    finally:
        _CURRENT_REQUEST_ID.reset(token)
    result = hk.search_chats("zephyrine ledger")
    assert result.hits == ()
    assert result.governed_turns_skipped >= 1


def test_an_empty_query_is_refused_not_guessed() -> None:
    _seed_world()
    assert hk.search_chats("where did we talk about it").error == "empty_query"


def test_a_handled_housekeeping_request_is_not_found_by_the_next_search() -> None:
    _seed_world()
    request = "which chat did we talk about tomatoes in?"
    _seed(CURRENT, request, "Found it: my tomatoes have yellow leaves.")
    hk.record_handled_request(CURRENT, request)
    hits = hk.search_chats("tomatoes", current_session_id=CURRENT).hits
    assert [hit.session_id for hit in hits] == [GARDEN]


# ---------------------------------------------------------------------------------------------
# Actions: read back, refuse what can't be done
# ---------------------------------------------------------------------------------------------


def test_find_it_and_pin_it_pins_the_chat_and_the_matching_message() -> None:
    _seed_world()
    run = lane.execute_plan(_plan("which chat did we talk about staking with a validator? pin it"), session_id=CURRENT)
    assert hk.pinned_chat_ids().keys() == {STAKING}
    from core import message_pins

    # The chat's first question is already its title, so the answer is what gets quoted and pinned.
    assert [(p["role"], p["text"]) for p in message_pins.list_pins(STAKING)] == [
        ("assistant", "Pick a validator with low commission, then delegate from your wallet.")
    ]
    text = "\n".join(run.lines)
    assert "Found it" in text and "Pinned" in text and not run.any_failure


def test_pin_it_after_an_ambiguous_search_asks_and_pins_nothing_then_pin_2_works() -> None:
    _seed_world()
    _seed("openclaw:hk00000000000000000tripa", "plan the trip budget for Lisbon", "Flights, rooms, food: start with flights.")
    _seed("openclaw:hk00000000000000000tripb", "trip budget for Rome in May", "Rome in May: book rooms early.")
    run = lane.execute_plan(_plan("find the chat where we talked about the trip budget and pin it"), session_id=CURRENT)
    assert hk.pinned_chat_ids() == {}
    assert any("haven't pinned anything" in line for line in run.lines)
    listed = hk.recalled_results(CURRENT)["hits"]
    second = listed[1]["session_id"]
    lane.execute_plan(_plan("pin 2"), session_id=CURRENT)
    assert hk.pinned_chat_ids().keys() == {second}


def test_pin_both_after_a_two_hit_search_pins_both_without_saying_it_did_not() -> None:
    _seed_world()
    _seed("openclaw:hk00000000000000000tripa", "plan the trip budget for Lisbon", "Flights, rooms, food: start with flights.")
    _seed("openclaw:hk00000000000000000tripb", "trip budget for Rome in May", "Rome in May: book rooms early.")
    run = lane.execute_plan(_plan("where did we talk about the trip budget? pin both"), session_id=CURRENT)
    assert set(hk.pinned_chat_ids()) == {"openclaw:hk00000000000000000tripa", "openclaw:hk00000000000000000tripb"}
    assert not any("haven't pinned" in line for line in run.lines)


def test_pin_it_with_nothing_to_point_at_asks() -> None:
    _seed_world()
    run = lane.execute_plan(_plan("pin it"), session_id=CURRENT)
    assert hk.pinned_chat_ids() == {}
    assert "this chat, or my last answer" in "\n".join(run.lines)


def test_rename_this_chat_on_its_first_message_and_pin_it() -> None:
    fresh = "openclaw:hk00000000000000000fresh"
    run = lane.execute_plan(_plan("rename this chat to Budget 2026 and pin it"), session_id=fresh)
    assert not run.any_failure, run.lines
    from core.memory.entries import list_conversation_sessions

    row = next(r for r in list_conversation_sessions(limit=1000) if r["session_id"] == fresh)
    assert row["title"] == "Budget 2026"
    assert fresh in hk.pinned_chat_ids()


def test_actions_on_a_chat_that_does_not_exist_fail_typed() -> None:
    assert hk.rename_chat("openclaw:nope", "x").error == "chat_not_found"
    assert hk.set_chat_pinned("openclaw:nope", True).error == "chat_not_found"
    assert hk.rename_chat(CURRENT, "   ", allow_new_chat=True).error == "empty_title"


def test_move_to_an_unknown_or_ambiguous_project_changes_nothing(tmp_path) -> None:
    _seed_world()
    from core import project_store

    for name in ("Web0 site", "Web0 wallet"):
        folder = tmp_path / name.replace(" ", "_")
        folder.mkdir()
        assert project_store.create_project(name, str(folder))[0]
    assert hk.move_chat_to_project(STAKING, "garden").error == "project_not_found"
    ambiguous = hk.move_chat_to_project(STAKING, "web0")
    assert ambiguous.error == "ambiguous_project" and set(ambiguous.after["candidates"]) == {"Web0 site", "Web0 wallet"}
    assert hk.describe_chat(STAKING)["project_id"] == ""
    moved = hk.move_chat_to_project(STAKING, "web0 wallet")
    assert moved.ok and hk.describe_chat(STAKING)["project_name"] == "Web0 wallet"
    back = hk.move_chat_to_project(STAKING, "general")
    assert back.ok and hk.describe_chat(STAKING)["project_id"] == ""


def test_rename_project_keeps_its_folder_and_refuses_a_missing_one(tmp_path) -> None:
    from core import project_store

    folder = tmp_path / "proj"
    folder.mkdir()
    ok, project = project_store.create_project("Old name", str(folder))
    assert ok
    renamed = hk.rename_project("old name", "New name")
    assert renamed.ok and project_store.get_project(project["id"])["root"] == project["root"]
    folder.rmdir()
    assert hk.rename_project("new name", "Third").error == "project_folder_missing"
    assert project_store.get_project(project["id"])["name"] == "New name"


def test_a_corrupt_pin_store_recovers_from_its_backup_and_keeps_the_evidence() -> None:
    _seed_world()
    assert hk.set_chat_pinned(STAKING, True).ok
    assert hk.set_chat_pinned(GARDEN, True).ok
    hk.chat_pins_path().write_text("{not json", encoding="utf-8")
    assert set(hk.pinned_chat_ids()) == {STAKING, GARDEN}
    assert list(hk.chat_pins_path().parent.glob("chat_pins.json.corrupt-*"))


def test_the_chat_we_are_in_is_never_archived_but_another_one_is_and_comes_back() -> None:
    _seed_world()
    run = lane.execute_plan(_plan("archive this chat"), session_id=CURRENT)
    assert run.any_failure and hk.describe_chat(CURRENT)["archived"] is False
    lane.execute_plan(_plan("find the chat about tomatoes and archive it"), session_id=CURRENT)
    assert hk.describe_chat(GARDEN)["archived"] is True
    # An archived chat takes no turns (the chat door answers 409), so it comes back from another chat.
    lane.execute_plan(_plan("find the chat about tomatoes and restore it"), session_id=CURRENT)
    assert hk.describe_chat(GARDEN)["archived"] is False


def test_mutations_are_refused_when_the_turn_forbids_actions() -> None:
    _seed_world()
    run = lane.execute_plan(_plan("pin this chat"), session_id=CURRENT, mutations_allowed=False)
    assert hk.pinned_chat_ids() == {}
    assert "didn't pin" in run.lines[0]


# ---------------------------------------------------------------------------------------------
# HTTP: the sidebar listing and the pin-chat door
# ---------------------------------------------------------------------------------------------


def _post(path: str, body: dict, host: str = "127.0.0.1"):
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path=path, body=body, headers={"content-type": "application/json"},
        runtime=types.SimpleNamespace(runtime_version_stamp={}), model_name="vool",
        workspace_root_provider=lambda: ".", client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _sessions():
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/api/chat/sessions", query={}, runtime=types.SimpleNamespace(runtime_version_stamp={}),
                       model_name="vool", client_host="127.0.0.1")
    return json.loads(res.body.decode("utf-8"))


def test_pin_chat_door_is_owner_local_and_typed() -> None:
    _seed_world()
    assert _post("/api/chat/pin-chat", {"session_id": STAKING, "pinned": True}, host="203.0.113.9")[0] == 403
    assert _post("/api/chat/pin-chat", {"session_id": STAKING, "pinned": "yes"})[0] == 400
    assert _post("/api/chat/pin-chat", {"session_id": STAKING, "extra": 1, "pinned": True})[0] == 400
    assert _post("/api/chat/pin-chat", {"session_id": "openclaw:nope", "pinned": True})[0] == 404
    status, payload = _post("/api/chat/pin-chat", {"session_id": STAKING, "pinned": True})
    assert status == 200 and payload["pinned"] is True and payload["changed"] is True


def test_a_pinned_chat_is_listed_even_outside_the_newest_100() -> None:
    _seed(STAKING, "the oldest chat mentions kubernetes", "noted")
    for index in range(101):
        _seed(f"openclaw:hkfill{index:020d}", f"filler {index}", "ok")
    assert all(row["session_id"] != STAKING for row in _sessions()["sessions"])
    assert hk.set_chat_pinned(STAKING, True).ok
    rows = _sessions()["sessions"]
    pinned = [row for row in rows if row["pinned"]]
    assert [row["session_id"] for row in pinned] == [STAKING]
    assert len([row for row in rows if not row["pinned"]]) == 100
