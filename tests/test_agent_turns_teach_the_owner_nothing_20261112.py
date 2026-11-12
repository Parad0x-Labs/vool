"""A team agent's turn teaches the owner's memory nothing, whichever learner reads it.

Each agent of a VOOL team runs its own chat session through /api/chat, and the orchestrator's brief arrives
as that session's "user" message. The chat line (2026-11-12, agent-spending test) marked those rows
``agent_session`` and kept them out of search and recall, but every learner that reads a turn still ran on
them: the statement index, captured facts and shorthand, user heuristics, the feedback signal, the session
summary and the dense profile at the transcript writer, and in front of it the profile seat (names,
standing instructions, response style), an explicit "remember", the model-backed extractor for a session the
team holds, and the adapter corpus built from the conversation log. So a brief could be filed as the owner's
own fact.

Law (``core.request_trust.turn_speaks_for_owner``): a turn is an agent's when its request says an agent wrote
it (``turn_author: "agent"``) or when a team holds its session (``core.agent_team.gate``); either alone is
enough. An agent's turn is logged and marked ``agent_session``, and no learner reads it. The profile is still
READ for it, and it cannot forget or undo what the owner saved. Every case runs the same words as the owner
first, so each check proves the learner is live and only the agent's copy is cut.

All names, codes and sentences are invented for this contract.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_team import gate
from core.request_trust import OWNER_LOCAL_KEY, TURN_AUTHOR_KEY
from tests.operator_profile_rig import (
    body_json,
    canonical_session,
    chat_body,
    make_agent,
    profile_env_generator,
    served_get,
    served_post,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("_hash_backend")

OWNER_CHAT = "openclaw:" + "b1" * 10
AGENT_CHAT = "openclaw:" + "a7" * 10
TEAM_AGENT = "reviewer-1"

#: How a turn comes to be an agent's: the team holds the session, or the request says an agent wrote it.
AGENT_MODES = ("team holds the session", "request says an agent wrote it")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


@pytest.fixture(params=AGENT_MODES)
def agent_mode(request):
    held: list[str] = []

    def hold(session: str) -> None:
        if request.param == AGENT_MODES[0] and session not in held:
            gate.bind_session(session, object(), TEAM_AGENT)
            held.append(session)

    yield SimpleNamespace(name=request.param, hold=hold,
                          declares=request.param == AGENT_MODES[1])
    for session in held:
        gate.unbind_session(session)


def _context(session: str, *, agent: Any = None) -> dict[str, Any]:
    ctx: dict[str, Any] = {"surface": "openclaw", "platform": "api", OWNER_LOCAL_KEY: True,
                           "session_id": session, "chat_id": session, "runtime_session_id": session}
    if agent is not None:
        agent.hold(session)
        if agent.declares:
            ctx[TURN_AUTHOR_KEY] = "agent"
    return ctx


def _write(session: str, user: str, assistant: str = "Noted, I will keep that in mind.", *, agent: Any = None,
           stopped: bool = False) -> None:
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(session, grant_current_receipts=False)
    if stopped:
        from core.persistent_memory import (
            TRANSCRIPT_COMMIT_STATE_FIELD,
            TRANSCRIPT_COMMIT_STATE_STOPPED,
            _write_conversation_event,
        )

        _write_conversation_event(session_id=session, user_input=user, assistant_output="",
                                  source_context=_context(session, agent=agent),
                                  extra_fields={TRANSCRIPT_COMMIT_STATE_FIELD: TRANSCRIPT_COMMIT_STATE_STOPPED})
        return
    from core.persistent_memory import append_conversation_event

    append_conversation_event(session_id=session, user_input=user, assistant_output=assistant,
                              source_context=_context(session, agent=agent))


def _log_rows(session: str) -> list[dict[str, Any]]:
    from core.memory.files import conversation_log_path, load_jsonl

    return [row for row in load_jsonl(conversation_log_path()) if str(row.get("session_id") or "") == session]


def _facts(session: str) -> list[str]:
    from core.memory.files import load_jsonl, memory_entries_path

    return [str(row.get("text") or "") for row in load_jsonl(memory_entries_path())
            if session in {str(row.get("session_id") or ""), str(row.get("origin_chat_id") or "")}]


def _heuristics(session: str) -> list[dict[str, Any]]:
    from core.memory.files import load_jsonl, user_heuristics_path

    return [row for row in load_jsonl(user_heuristics_path()) if str(row.get("origin_chat_id") or "") == session]


def _summaries(session: str) -> list[str]:
    from core.memory.files import load_jsonl, session_summaries_path

    return [str(row.get("summary") or "") for row in load_jsonl(session_summaries_path())
            if str(row.get("session_id") or "") == session]


def _db_rows(sql: str, *args: Any) -> list[tuple[Any, ...]]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [tuple(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _indexed(word: str) -> list[str]:
    from core.runtime_paths import active_vool_home

    path = Path(active_vool_home()) / "data" / "memory" / "vool_memory.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [str(row[0]) for row in conn.execute("SELECT tags FROM memory_nodes WHERE content LIKE ?",
                                                     (f"%{word}%",))]
    finally:
        conn.close()


# --- who is speaking --------------------------------------------------------------------------------------

def test_a_turn_is_an_agents_when_either_signal_says_so():
    from core.request_trust import turn_speaks_for_owner

    assert turn_speaks_for_owner({}, OWNER_CHAT)
    assert turn_speaks_for_owner({TURN_AUTHOR_KEY: "owner"}, OWNER_CHAT)
    assert not turn_speaks_for_owner({TURN_AUTHOR_KEY: "agent"}, OWNER_CHAT)
    gate.bind_session(AGENT_CHAT, object(), TEAM_AGENT)
    try:
        assert not turn_speaks_for_owner({}, AGENT_CHAT)
        # Claiming the owner grants nothing while the team holds the session.
        assert not turn_speaks_for_owner({TURN_AUTHOR_KEY: "owner"}, AGENT_CHAT)
        assert not turn_speaks_for_owner({"session_id": AGENT_CHAT})
        assert turn_speaks_for_owner({}, OWNER_CHAT)
    finally:
        gate.unbind_session(AGENT_CHAT)
    assert turn_speaks_for_owner({}, AGENT_CHAT)


# --- the transcript writer: every learner is cut, the row is kept and marked ------------------------------

BRIEF_FACTS = ('I live in Porto. Keep answers concise and brutally honest. '
               'When I say "ship it" I mean "deploy to the staging box".')
AGENT_FACTS = BRIEF_FACTS.replace("Porto", "Faro").replace("ship it", "land it")


def test_an_agent_row_is_logged_and_marked_and_an_owner_row_is_not(env, agent_mode):
    _write(OWNER_CHAT, "Keep the gate log in the blue binder.")
    _write(AGENT_CHAT, "Keep the gate log in the blue binder.", agent=agent_mode)

    owner_rows, agent_rows = _log_rows(OWNER_CHAT), _log_rows(AGENT_CHAT)
    assert len(owner_rows) == 1 and not owner_rows[0].get("agent_session"), owner_rows
    # The agent's own session history is unchanged: its turn is logged, and the row says whose it is.
    assert len(agent_rows) == 1 and agent_rows[0].get("agent_session") is True, agent_rows
    assert "blue binder" in agent_rows[0]["user"]


def test_no_fact_or_shorthand_is_captured_from_an_agent_brief(env, agent_mode):
    _write(OWNER_CHAT, BRIEF_FACTS)
    owner = " ".join(_facts(OWNER_CHAT)).casefold()
    assert "porto" in owner and "ship it" in owner, owner  # the learner is live

    _write(AGENT_CHAT, AGENT_FACTS, agent=agent_mode)

    assert _facts(AGENT_CHAT) == []
    assert "faro" not in json.dumps(_facts(OWNER_CHAT)).casefold()
    lexicon = _db_rows("SELECT term, scope FROM adaptive_lexicon")
    assert ("ship it", OWNER_CHAT) in lexicon and not [row for row in lexicon if row[1] == AGENT_CHAT], lexicon


def test_no_fact_is_captured_from_a_stopped_agent_turn(env, agent_mode):
    _write(OWNER_CHAT, BRIEF_FACTS, stopped=True)
    assert "porto" in " ".join(_facts(OWNER_CHAT)).casefold()

    _write(AGENT_CHAT, AGENT_FACTS, agent=agent_mode, stopped=True)

    assert _facts(AGENT_CHAT) == [] and _heuristics(AGENT_CHAT) == []
    assert [row.get("agent_session") for row in _log_rows(AGENT_CHAT)] == [True]


BRIEF_HABITS = ("I am building Telegram bots in Python. Use official docs and good GitHub repos first. "
                "Keep answers concise and direct.")


def test_no_heuristic_is_learned_from_an_agent_brief(env, agent_mode):
    _write(OWNER_CHAT, BRIEF_HABITS)
    assert {row["signal"] for row in _heuristics(OWNER_CHAT)} >= {"python", "official_docs"}

    _write(AGENT_CHAT, BRIEF_HABITS.replace("Python", "Rust"), agent=agent_mode)

    assert _heuristics(AGENT_CHAT) == []
    from core.memory.files import load_jsonl, user_heuristics_path

    assert "rust" not in json.dumps(load_jsonl(user_heuristics_path())).casefold()


def test_the_dense_profile_is_not_rebuilt_from_an_agent_brief(env, agent_mode):
    from core.memory.learning import load_operator_dense_profile

    _write(OWNER_CHAT, BRIEF_HABITS)
    before = load_operator_dense_profile()
    assert before.get("last_session_id") == OWNER_CHAT and "python" in before.get("preferred_stacks", []), before

    _write(AGENT_CHAT, BRIEF_HABITS.replace("Python", "Rust"), agent=agent_mode)

    after = load_operator_dense_profile()
    assert after.get("last_session_id") == OWNER_CHAT, after
    assert after.get("preferred_stacks") == before.get("preferred_stacks"), after


def test_no_feedback_signal_is_taken_from_an_agent_brief(env, agent_mode):
    rejection = "No, that's wrong, try again with the second file."
    _write(OWNER_CHAT, rejection)
    assert _db_rows("SELECT COUNT(*) FROM response_feedback WHERE session_id = ?", OWNER_CHAT) == [(1,)]

    _write(AGENT_CHAT, rejection, agent=agent_mode)

    assert _db_rows("SELECT COUNT(*) FROM response_feedback WHERE session_id = ?", AGENT_CHAT) == [(0,)]


def test_no_session_summary_is_written_from_an_agent_brief(env, agent_mode):
    ask = "Review the gate log parser in the blue binder project and list what breaks."
    _write(OWNER_CHAT, ask)
    assert any("blue binder" in summary for summary in _summaries(OWNER_CHAT))

    _write(AGENT_CHAT, ask, agent=agent_mode)

    assert _summaries(AGENT_CHAT) == []


def test_no_statement_from_an_agent_brief_enters_the_statement_index(env, agent_mode):
    _write(OWNER_CHAT, "My harbor lantern is painted signal green and hangs by the north gate.")
    owner_tags = _indexed("signal green")
    assert owner_tags and all('"user"' in tags for tags in owner_tags), owner_tags  # the index is live

    _write(AGENT_CHAT, "My harbor lantern is painted ember orange and hangs by the south gate.", agent=agent_mode)

    assert _indexed("ember orange") == []


# --- the learners in front of the writer ------------------------------------------------------------------

def test_the_model_backed_extractor_skips_a_session_the_team_holds(env, monkeypatch):
    import core.web.api.runtime as runtime_api

    triggered: list[list[dict]] = []
    monkeypatch.setattr("core.fact_extractor.FactExtractor.trigger_async",
                        lambda self, messages: triggered.append(messages))
    from core.context_namespace import ensure_chat_namespace

    for session in (OWNER_CHAT, AGENT_CHAT):
        ensure_chat_namespace(session, grant_current_receipts=False)

    def extract(session: str) -> None:
        runtime_api.schedule_memory_extraction(
            SimpleNamespace(runtime_home=str(env["home"])), user_text=BRIEF_HABITS,
            assistant_output="Noted.", session_id=session, source_context=_context(session))

    extract(OWNER_CHAT)
    assert len(triggered) == 1  # the learner is live
    gate.bind_session(AGENT_CHAT, object(), TEAM_AGENT)
    try:
        extract(AGENT_CHAT)
    finally:
        gate.unbind_session(AGENT_CHAT)
    assert len(triggered) == 1, triggered


def test_the_adapter_corpus_skips_agent_rows(env, agent_mode):
    from core.adaptation_dataset import _conversation_examples

    reply = "Here is the parser review with the three breaking inputs listed in order."
    _write(OWNER_CHAT, "Review the gate log parser and list what breaks.", reply)
    _write(AGENT_CHAT, "Review the lantern log parser and list what breaks.", reply, agent=agent_mode)

    sessions = [example.metadata.get("session_id") for example in _conversation_examples(limit=20, filters={})]

    assert OWNER_CHAT in sessions and AGENT_CHAT not in sessions, sessions


# --- the served chat door: what the team actually sends ---------------------------------------------------

def _served_turn(agent: Any, text: str, handle: str, *, mode: Any = None) -> str:
    body = chat_body(text, handle)
    if mode is not None:
        mode.hold(canonical_session(handle))
        if mode.declares:
            body[TURN_AUTHOR_KEY] = "agent"
    response = served_post(agent, "/api/chat", body)
    assert int(response.status) == 200, (response.status, getattr(response, "body", b"")[:300])
    return str(((body_json(response).get("message") or {}).get("content")) or "")


def _profile_items(agent: Any) -> list[tuple[str, str]]:
    items = body_json(served_get(agent, "/api/profile", {}))["items"]
    return sorted((str(item["category"]), str(item["value"])) for item in items)


PROFILE_BRIEFS = (
    "my name is Noor",
    "Hi, I'm Noor, what is the capital of Portugal?",
    "From now on always answer in one short line.",
    "Can you help me answer this?\n\nHi Noor,\n\nMy name is Piotr and I am writing about the invoice.\n"
    "Could you confirm the amount?\n\nBest regards,\nPiotr Nowak",
)


def test_an_agent_brief_saves_nothing_to_the_profile_and_forgets_nothing_from_it(env, agent_mode):
    from core.standing_instructions import _load as standing_rows

    agent = make_agent()
    assert "Marta" in _served_turn(agent, "my name is Marta", "owner-own-chat")  # the seat is live
    saved = _profile_items(agent)
    assert ("preferred_name", "Marta") in saved, saved

    for number, brief in enumerate(PROFILE_BRIEFS):
        _served_turn(agent, brief, f"team-agent-chat-{number}", mode=agent_mode)
    _served_turn(agent, "forget my name", "team-agent-chat-forget", mode=agent_mode)

    assert _profile_items(agent) == saved
    assert standing_rows() == []
    # The profile is still READ for an agent's turn.
    assert "Marta" in _served_turn(agent, "what's my name?", "team-agent-chat-read", mode=agent_mode)


def test_an_agent_brief_that_says_remember_is_not_filed_as_the_owners_fact(env, agent_mode):
    from core.persistent_memory import AGENT_TURN_REMEMBER_REFUSED

    agent = make_agent()
    assert "Locked in" in _served_turn(agent, "Remember that my storage locker code is KESTREL-7731", "owner-own-chat")

    reply = _served_turn(agent, "Remember that my storage locker code is OSPREY-5520", "team-agent-chat",
                         mode=agent_mode)

    assert reply == AGENT_TURN_REMEMBER_REFUSED, reply
    from core.memory.files import load_jsonl, memory_entries_path

    texts = json.dumps(load_jsonl(memory_entries_path()))
    assert "KESTREL-7731" in texts and "OSPREY" not in texts, texts
