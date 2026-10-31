"""The one-call owner-local lane (core.one_call_lane), driven over a real socket.

`apps.vool_api_server` in its own process and VOOL_HOME, an Ollama-dialect stub as the only model
(no Ollama, no paid call). Everything between the request and the reply is production code. One
daemon runs with the lane on (its default) and one with ``VOOL_OWNER_LOCAL_ONE_CALL_LANE=0``.

The stub is a scripted model, so these prove what VOOL does around the model -- which tools it is
offered, how many calls a turn costs, what happens to prose, a length cut, a source with nothing
connected and a write -- not what a real local model would choose.

Each daemon boots in ~15-30 s.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import (
    REPO_ROOT,
    SEED_MANIFEST,
    CutReply,
    ScriptedProvider,
    ServedDaemon,
    run_in_home,
)

MODEL = "qwen3-stub:2b"

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''

# Owner-local asks no word list in VOOL was written for: slang, and six languages. Each maps to
# the catalog tool the scripted model calls when (and only when) the round offers it.
UNUSUAL_ASKS: dict[str, tuple[str, dict[str, Any]]] = {
    "anything hogging the box rn?": ("machine__list_processes", {}),
    "mi compu va lentísima, ¿quién se está comiendo la memoria?": ("machine__list_processes", {}),
    "wie voll ist eigentlich meine Platte?": ("machine__disk_usage", {}),
    "pokaż co zapisałem o dentyście": ("operator__find_notes", {"query": "dentysta"}),
    "メモに歯医者のこと書いてた？": ("operator__find_notes", {"query": "歯医者"}),
    "ar turiu priminimų šiai savaitei?": ("operator__list_reminders", {}),
}
KNOWLEDGE_ASK = "at what temperature does water boil at sea level?"
KNOWLEDGE_REPLY = "Water boils at 100 °C at sea level."
CUT_ASK = "give me a long poem about autumn leaves"
CUT_TEXT = "Leaves fall and drift across the"
CLAIM_ASK = "did i jot anything down about the plumber"
CLAIM_PROSE = "I checked your notes: the plumber comes on Tuesday at 9."
CLAIM_AFTER_TOOL = "Your notes have nothing about the plumber."
CALENDAR_ASK = "¿qué tengo en la agenda mañana?"
MAIL_ASK = "hab ich heute eine Mail von der Bank bekommen?"
NOT_CONNECTED_REPLY = "That is not connected yet, so I can't see it. You can add it in Settings."
GUESS_REPLY = "Tomorrow you have a dentist appointment at 10."
WRITE_ASK = "put plumber tuesday 9 into a new file plumber.txt"
WRITE_CALL = {
    "id": "call-w", "type": "function",
    "function": {"name": "workspace__write_file", "arguments": {"path": "plumber.txt", "content": "plumber tuesday 9\n"}},
}
FINAL_AFTER_TOOL = "Done looking."


def _last_user(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _all_text(body: dict[str, Any]) -> str:
    parts = [str(body.get("system") or "")]
    parts += [str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict)]
    return "\n".join(parts)


def _tool_names(body: dict[str, Any]) -> set[str]:
    return {str(((t or {}).get("function") or {}).get("name") or "") for t in body.get("tools") or [] if isinstance(t, dict)}


def _reply_fn(body: dict[str, Any]) -> Any:
    """The scripted model. Answers from the ask found in the prompt and the tools it was offered."""
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
    from core.one_call_lane import RETRY_INSTRUCTION

    text = _all_text(body)
    if AMBIGUITY_SYSTEM_PROMPT in text:
        return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
    # A tool's result returns as a role "tool" message or as a tool observation in a user message.
    has_tool_result = any(
        isinstance(m, dict) and (m.get("role") == "tool" or "tool_observation_v1" in str(m.get("content") or ""))
        for m in body.get("messages") or []
    )
    offered = _tool_names(body)
    last_user = _last_user(body)
    if RETRY_INSTRUCTION in last_user:
        return {"id": "call-r", "type": "function", "function": {"name": "operator__find_notes", "arguments": {"query": "plumber"}}}
    for ask, (name, arguments) in UNUSUAL_ASKS.items():
        if ask in text:
            if has_tool_result:
                return FINAL_AFTER_TOOL
            if name in offered:
                return {"id": "call-1", "type": "function", "function": {"name": name, "arguments": arguments}}
            return "I can't see that from here."
    if CLAIM_ASK in text:
        return CLAIM_AFTER_TOOL if has_tool_result else CLAIM_PROSE
    if CUT_ASK in text:
        return CutReply(CUT_TEXT)
    if CALENDAR_ASK in text or MAIL_ASK in text:
        return NOT_CONNECTED_REPLY if "Not connected on this computer" in text else GUESS_REPLY
    if WRITE_ASK in text:
        if "workspace.write_file" in text and has_tool_result:
            return FINAL_AFTER_TOOL
        if "workspace__write_file" in offered:
            return WRITE_CALL
        if "capability__expand_family" in offered and "expand_family" not in text:
            # Writes are not in the catalog: the model asks for the family that holds them.
            return {"id": "call-x", "type": "function", "function": {"name": "capability__expand_family", "arguments": {"family": "workspace"}}}
        return "I can't write files here."
    return KNOWLEDGE_REPLY


def _reply_text(payload: dict[str, Any]) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or payload.get("response") or "")


def _events(home: Path) -> list[tuple[str, dict[str, Any]]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute("SELECT event_type, details_json FROM runtime_session_events ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return [(str(row[0]), json.loads(row[1] or "{}")) for row in rows]


class _Turn:
    def __init__(self, reply: str, calls: list[dict[str, Any]], events: list[tuple[str, dict[str, Any]]]) -> None:
        self.reply = reply
        self.calls = calls
        self.events = events

    @property
    def executed(self) -> list[str]:
        return [str(d.get("tool_name") or "") for kind, d in self.events if kind == "tool_executed"]

    @property
    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


class _Rig:
    def __init__(self, root: Path, *, lane: bool) -> None:
        self.home = root / "home"
        self.provider = ScriptedProvider({MODEL: KNOWLEDGE_REPLY}, reply_fn=_reply_fn)
        env = {
            "OLLAMA_HOST": self.provider.base_url,
            "VOOL_OLLAMA_URL": self.provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{self.provider.base_url}/api/chat",
            "HOME": str(root / "owner-home"),
            "VOOL_OWNER_LOCAL_ONE_CALL_LANE": "1" if lane else "0",
        }
        self.daemon = ServedDaemon(self.home, env_extra=env)
        self._n = 0

    def start(self) -> None:
        self.provider.__enter__()
        try:
            self.daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the lane
            self.provider.__exit__(None, None, None)
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(self.home, _SEED.format(root=REPO_ROOT, base_url=self.provider.base_url, registered=[MODEL], model=MODEL))
        self.provider.reset()

    def stop(self) -> None:
        self.daemon.stop()
        self.provider.__exit__(None, None, None)

    def turn(self, ask: str, *, mode: str = "auto") -> _Turn:
        self._n += 1
        self.last_session = f"one-call-{self._n}"
        calls_before = len(self.provider.calls)
        events_before = len(_events(self.home))
        payload = self.daemon.chat(ask, session_id=self.last_session, model=MODEL, mode=mode, timeout=240)
        calls = list(self.provider.calls[calls_before:])
        events = _events(self.home)[events_before:]
        return _Turn(_reply_text(payload), calls, events)


@pytest.fixture(scope="module")
def lane_on(tmp_path_factory: pytest.TempPathFactory):
    rig = _Rig(tmp_path_factory.mktemp("one-call-on"), lane=True)
    rig.start()
    yield rig
    rig.stop()


@pytest.fixture(scope="module")
def lane_off(tmp_path_factory: pytest.TempPathFactory):
    rig = _Rig(tmp_path_factory.mktemp("one-call-off"), lane=False)
    rig.start()
    yield rig
    rig.stop()


#: The post-turn fact extractor (core.fact_extractor) runs in the background after the reply is
#: committed; it is memory, not part of answering the turn, and this lane leaves it as it was.
_FACT_EXTRACTOR = "You are a local memory extraction agent"


def _chat_calls(turn: _Turn) -> list[dict[str, Any]]:
    """The model calls that answered the turn (the background fact extractor excluded)."""
    return [
        c for c in turn.calls
        if "/api/chat" in str(c.get("path") or "") and not str(c.get("prompt") or "").startswith(_FACT_EXTRACTOR)
    ]


@pytest.mark.parametrize("ask", list(UNUSUAL_ASKS))
def test_an_owner_ask_no_word_list_covers_reaches_the_model_holding_its_tool(lane_on: _Rig, ask: str) -> None:
    name, _ = UNUSUAL_ASKS[ask]
    turn = lane_on.turn(ask)
    calls = _chat_calls(turn)

    assert "one_call_lane" in turn.kinds, turn.kinds
    assert calls, "no model call"
    # The first model call of the turn already holds the catalog: no planner split, no ambiguity
    # probe, no tools-less helper call ran before it.
    assert name in calls[0]["tools"], (ask, calls[0]["tools"])
    assert turn.executed == [name.replace("__", ".")], (turn.executed, turn.reply)
    # One call per step: the tool round, then at most the answer round (after a read tool the
    # loop's synthesis answers from the tool's evidence without tools).
    assert len(calls) <= 2, [c["prompt"][:80] for c in calls]
    # think off on every round; the local runner gets every core.
    assert all(c["think"] is not True for c in calls), [c["think"] for c in calls]
    assert all(c["options"].get("num_thread") == (os.cpu_count() or 1) for c in calls), [c["options"] for c in calls]
    assert turn.reply.strip(), turn.reply


def test_a_prose_reply_on_a_tool_round_is_the_answer_in_one_call(lane_on: _Rig) -> None:
    turn = lane_on.turn(KNOWLEDGE_ASK)
    calls = _chat_calls(turn)

    assert len(calls) == 1, [c["prompt"][:80] for c in calls]
    assert calls[0]["tools"], "the round offered no tools"
    assert KNOWLEDGE_REPLY in turn.reply, turn.reply
    assert turn.executed == [], turn.executed


def test_a_reply_the_output_limit_cut_is_not_passed_off_as_complete(lane_on: _Rig) -> None:
    from core.one_call_lane import CUT_NOTE

    turn = lane_on.turn(CUT_ASK)
    calls = _chat_calls(turn)

    assert len(calls) == 1, [c["prompt"][:80] for c in calls]
    assert CUT_TEXT in turn.reply, turn.reply
    assert CUT_NOTE in turn.reply, turn.reply


def test_prose_that_claims_a_read_no_tool_made_gets_one_retry_with_tools(lane_on: _Rig) -> None:
    from core.one_call_lane import RETRY_INSTRUCTION

    turn = lane_on.turn(CLAIM_ASK)
    calls = _chat_calls(turn)

    retries = [c for c in calls if any(RETRY_INSTRUCTION in m["content"] for m in c["messages"])]
    assert len(retries) == 1, [c["prompt"][:80] for c in calls]
    assert turn.executed == ["operator.find_notes"], (turn.executed, turn.reply)
    assert CLAIM_AFTER_TOOL in turn.reply, turn.reply
    assert "plumber comes on Tuesday" not in turn.reply, turn.reply


@pytest.mark.parametrize("ask", [CALENDAR_ASK, MAIL_ASK])
def test_a_source_with_nothing_connected_is_not_offered_and_the_reply_says_so(lane_on: _Rig, ask: str) -> None:
    turn = lane_on.turn(ask)
    calls = _chat_calls(turn)

    assert calls, "the ask never reached the model"
    offered = {name for c in calls for name in c["tools"]}
    assert not {n for n in offered if n.startswith("email__") or "agenda" in n or "calendar" in n}, sorted(offered)
    assert any("Not connected on this computer" in c["full_prompt"] or "Not connected on this computer" in c["system"] for c in calls)
    assert NOT_CONNECTED_REPLY in turn.reply, turn.reply
    assert GUESS_REPLY not in turn.reply, turn.reply
    assert turn.executed == [], turn.executed


def test_a_write_the_model_proposes_still_waits_for_approval(lane_on: _Rig) -> None:
    from tests._blackbox_served_rig import pending_session_approvals

    # Manual mode: the permission matrix asks before any file is created.
    turn = lane_on.turn(WRITE_ASK, mode="manual")
    calls = _chat_calls(turn)

    assert calls and "workspace__write_file" in {n for c in calls for n in c["tools"]}, [c["tools"] for c in calls]
    assert "workspace.write_file" not in turn.executed, (turn.executed, turn.reply)
    assert pending_session_approvals(lane_on.home, lane_on.last_session), (turn.kinds, turn.reply)


def test_flag_off_restores_the_earlier_path(lane_off: _Rig) -> None:
    from core.compute_mode import get_active_compute_budget  # noqa: F401  (import check only)

    knowledge = lane_off.turn(KNOWLEDGE_ASK)
    assert "one_call_lane" not in knowledge.kinds, knowledge.kinds
    calls = _chat_calls(knowledge)
    assert calls, "no model call"
    # The balanced budget's thread count, not every core.
    assert all(c["options"].get("num_thread") != (os.cpu_count() or 1) or (os.cpu_count() or 1) == 1 for c in calls if c["options"]), [
        c["options"] for c in calls
    ]
    # The word recognizer answers a calendar ask in another language before any model call.
    calendar = lane_off.turn(CALENDAR_ASK)
    assert "one_call_lane" not in calendar.kinds, calendar.kinds
    assert not _chat_calls(calendar), [c["prompt"][:80] for c in _chat_calls(calendar)]
    assert NOT_CONNECTED_REPLY not in calendar.reply, calendar.reply
