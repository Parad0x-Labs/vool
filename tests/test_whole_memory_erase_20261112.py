""""Forget everything you know about me" erases everything VOOL keeps about the person.

Found by the to-do thread (2026-11-12) on the v0.8 candidate:
1. "erase everything", "erase my memory" and "delete all my data" reached no erase at all.
2. With memory non-empty, "forget everything you know about me" was taken as a one-item forget: the memory lane looked
   for a saved entry carrying those words, removed whichever one did (or none), and put the words on the forget list.
   The profile seat, which runs first, took the same words as its own forget-all and cleared only the profile.

Law (``core.persistent_memory.whole_memory_erase_request``): a whole message that asks to erase everything VOOL keeps
about the person is a whole erase. The memory lane owns the turn; the profile seat leaves it to the lane. Every saved
entry this chat can see and every fact learned from chats is erased exactly as "Forget <it>" would erase it (the same
stores, the same verification, the same forget list), and the profile's own forget-all runs too. The reply says what
went and that chats and files are kept. A team agent's turn erases nothing. "Forget everything I told you about my
sister" stays a topic forget, "forget my profile" stays the profile's, and "delete all my files" is not memory.

All names, codes and sentences are invented for this contract.
"""
from __future__ import annotations

import json

import pytest

from tests.test_agent_turns_teach_the_owner_nothing_20261112 import (  # fixtures
    AGENT_CHAT,
    OWNER_CHAT,
    _context,
    _profile_items,
    _served_turn,
    agent_mode,
    env,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend  # fixture

pytestmark = pytest.mark.usefixtures("_hash_backend")

SAVED = (
    "my bike lock code is KESTREL-7731",
    "my neighbour's surname is Hargreave",
    "I like my coffee black",
)
SAVED_VALUES = ("KESTREL-7731", "Hargreave", "coffee black")
LEARNED = "Favourite climbing crag: Stanage Edge"

WHOLE_ERASE = (
    "forget everything you know about me",
    "Forget everything you know about me.",
    "forget everything",
    "Forget everything I told you",
    "forget all of it",
    "erase everything",
    "Erase everything!",
    "erase my memory",
    "wipe your memory",
    "delete all my data",
    "Delete all my personal data please",
    "delete everything you know about me",
    "clear all your memories",
    "Can you forget everything about me?",
    "please erase all my memories",
    "I want you to forget everything you’ve learned about me",
)
NOT_WHOLE_ERASE = (
    "forget everything I told you about my sister",
    "forget my bike lock code",
    "forget my whole profile",
    "forget my name",
    "delete everything",
    "delete all my files",
    "clear all",
    "erase the spreadsheet",
    "what do you remember about me?",
    "how do I forget everything about a bad day?",
    "forget it",
)


def _entries() -> str:
    from core.memory.files import load_jsonl, memory_entries_path

    return json.dumps(load_jsonl(memory_entries_path()))


def _say(text: str, chat: str = OWNER_CHAT, *, agent=None) -> str:
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(text, session_id=chat, source_context=_context(chat, agent=agent))
    assert handled, text
    return reply


def _learned_blocks() -> str:
    from core.context_retrieval import _open_memory
    from core.fact_extractor import ALLOWED_BLOCKS

    store = _open_memory()
    try:
        return "\n".join(store.block_read(block) or "" for block in sorted(ALLOWED_BLOCKS))
    finally:
        store.close()


def _seed() -> None:
    from core import operator_profile as profile
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _open_memory

    for chat in (OWNER_CHAT, AGENT_CHAT):
        ensure_chat_namespace(chat, grant_current_receipts=False)
    for fact in SAVED:
        assert "Locked in" in _say(f"Remember that {fact}")
    store = _open_memory()
    try:
        store.block_append("user_profile", LEARNED)
    finally:
        store.close()
    change = profile.remember(profile.principal_for_request(_context(OWNER_CHAT)), "preferred_name", "Noor",
                              session_id=OWNER_CHAT)
    assert change.kind == "saved", change
    for value in SAVED_VALUES:
        assert value in _entries()
    assert LEARNED in _learned_blocks()
    assert _profile_names() == ["Noor"]


def _profile_names() -> list[str]:
    from core import operator_profile as profile

    principal = profile.principal_for_request(_context(OWNER_CHAT))
    return [item.value_text for item in profile.list_items(principal, session_id=OWNER_CHAT)
            if item.category == "preferred_name"]


@pytest.mark.parametrize("text", WHOLE_ERASE)
def test_whole_erase_wordings_are_recognized_and_owned_by_the_memory_lane(text):
    from core.persistent_memory import memory_command_owns_turn, whole_memory_erase_request

    assert whole_memory_erase_request(text)
    assert memory_command_owns_turn(text)


@pytest.mark.parametrize("text", NOT_WHOLE_ERASE)
def test_other_wordings_are_not_a_whole_erase(text):
    from core.persistent_memory import whole_memory_erase_request

    assert not whole_memory_erase_request(text)


@pytest.mark.parametrize("text", ("forget everything you know about me", "erase everything", "erase my memory",
                                  "delete all my data"))
def test_a_whole_erase_removes_every_saved_entry_learned_fact_and_the_profile(env, text):
    from core.persistent_memory import list_memory_entries

    _seed()

    reply = _say(text)

    # 3 saved entries and 1 learned fact, then the profile's own forget-all
    assert reply.startswith("Forget applied. Removed 4 memory entries"), reply
    assert "Forgotten: preferred name -> Noor" in reply, reply
    assert "Your chats and files are kept" in reply, reply
    assert list_memory_entries(chat_id=OWNER_CHAT, limit=100) == []
    for value in SAVED_VALUES:
        assert value not in _entries(), value
    assert LEARNED not in _learned_blocks()
    assert _profile_names() == []
    assert _say("What do you remember?") == "Memory is currently empty. Tell me what to remember."


def test_a_whole_erase_with_nothing_saved_says_so(env):
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(OWNER_CHAT, grant_current_receipts=False)

    reply = _say("forget everything you know about me")

    assert reply.startswith("There was nothing saved in memory to erase."), reply
    assert "Your chats and files are kept" in reply, reply


def test_a_topic_forget_beside_saved_memories_stays_a_topic_forget(env):
    _seed()

    reply = _say("Forget my bike lock code is KESTREL-7731")

    assert reply.startswith("Forget applied"), reply
    assert "KESTREL-7731" not in _entries()
    assert "Hargreave" in _entries() and "coffee black" in _entries()
    assert _profile_names() == ["Noor"]


def test_an_agents_whole_erase_erases_nothing(env, agent_mode):
    from core.persistent_memory import AGENT_TURN_FORGET_REFUSED

    _seed()

    reply = _say("forget everything you know about me", AGENT_CHAT, agent=agent_mode)

    assert reply == AGENT_TURN_FORGET_REFUSED, reply
    for value in SAVED_VALUES:
        assert value in _entries(), value
    assert LEARNED in _learned_blocks()
    assert _profile_names() == ["Noor"]


def test_the_profile_seat_leaves_a_whole_erase_to_the_memory_lane(env):
    from core.operator_profile_turn import observe_profile_turn
    from tests.operator_profile_rig import make_agent

    _seed()
    agent = make_agent()

    assert observe_profile_turn(agent, "forget everything you know about me", session_id=OWNER_CHAT,
                                source_context=_context(OWNER_CHAT)) is None
    assert _profile_names() == ["Noor"]
    # the profile's own forget wording is still the profile's
    taken = observe_profile_turn(agent, "forget my name", session_id=OWNER_CHAT, source_context=_context(OWNER_CHAT))
    assert taken is not None
    assert _profile_names() == []


def _no_forget_left_open() -> None:
    """Every forget operation in the home is complete, so memory reads are served again (raises otherwise)."""
    from core.memory import revocation

    revocation.snapshot(None)


@pytest.mark.parametrize("text", ("forget everything you know about me", "erase everything", "erase my memory",
                                  "delete all my data"))
def test_served_a_whole_erase_clears_memory_and_profile(env, text):
    """The real /api/chat door, with saved memories and a profile name present."""
    from tests.operator_profile_rig import make_agent

    agent = make_agent()
    for fact in SAVED:
        assert "Locked in" in _served_turn(agent, f"Remember that {fact}", "owner-own-chat")
    _served_turn(agent, "my name is Noor", "owner-own-chat")
    assert ("preferred_name", "Noor") in _profile_items(agent)

    reply = _served_turn(agent, text, "owner-own-chat")

    assert reply.startswith("Forget applied. Removed 3 memory entries"), reply
    for value in SAVED_VALUES:
        assert value not in _entries(), (value, reply)
    assert [item for item in _profile_items(agent) if item[0] == "preferred_name"] == []
    _no_forget_left_open()
    assert _served_turn(agent, "What do you remember?", "owner-own-chat") == (
        "Memory is currently empty. Tell me what to remember.")
    # Measured in the real app: "erase everything" left its forget admission open, so the next round's memory was
    # withheld and its erase answered "could not be verified". A second round must work the same way.
    assert "Locked in" in _served_turn(agent, "Remember that my gate code is OSPREY-5520", "owner-own-chat")
    again = _served_turn(agent, text, "owner-own-chat")
    assert again.startswith("Forget applied. Removed 1 memory entr"), again
    assert "OSPREY-5520" not in _entries()
    _no_forget_left_open()


@pytest.mark.parametrize("text", ("erase everything", "forget everything you know about me"))
def test_served_a_whole_erase_with_nothing_saved_leaves_no_forget_open(env, text):
    from tests.operator_profile_rig import make_agent

    agent = make_agent()
    reply = _served_turn(agent, text, "owner-own-chat")

    assert reply.startswith("There was nothing saved in memory to erase."), reply
    _no_forget_left_open()
    assert "Locked in" in _served_turn(agent, "Remember that my gate code is OSPREY-5520", "owner-own-chat")
    assert "OSPREY-5520" in _served_turn(agent, "What do you remember?", "owner-own-chat")
