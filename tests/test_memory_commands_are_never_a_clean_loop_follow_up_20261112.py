"""A forget, a "remember that" and the memory list are the memory lane's, even right after a files question.

Measured 2026-11-12 on the v0.8 candidate a3648b5f, served (the certified scripted model): "Which box did I put
storage label MINT-84 on?" was a question about the owner's files, so the clean loop (on by default since the
head-4 merge, 9c69f91b) answered it and marked the chat for follow-ups. The next message, "Forget my name. Forget
storage label MINT-84.", was short enough to be read as that follow-up: the loop's model answered it, the forget
never ran, and the honesty check replaced the answer with "Forget could not be verified on this turn". The label
stayed in memory. tests/test_forget_counts_only_what_was_forgotten_20261020.py::
test_served_forget_counts_only_memory_and_forgets_the_named_label passes on 247124ed and fails on the candidate.

Law (``core.persistent_memory.memory_command_owns_turn``, asked first by ``core.clean_loop.route.decide``): a
message with a forget order in any sentence, a "remember that" / "note that" capture, the memory list or a
memory-scope command is never admitted to the clean loop, follow-up or not. A files follow-up still is.

All names and codes are invented for this contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.clean_loop import route
from tests.test_memory_forget_served_p0 import _reply_text, served  # noqa: F401 (served is a fixture)

OWNED = (
    "Forget my name. Forget storage label MINT-84.",
    "Forget storage label MINT-84.",
    "forget KESTREL-4410",
    "please forget the locker code",
    "ok, forget KESTREL-4410",
    "can you forget my old address?",
    "I want you to forget my sister's birthday",
    "Thanks. Now forget the gate code.",
    "Remember that my locker code is 4410",
    "note that the meeting moved to Friday",
    "what do you remember?",
    "What do you remember about me?",
    "show my memories",
)
NOT_OWNED = (
    "forget it, what's in my downloads?",
    "forget about it",
    "Did I forget to save the invoice?",
    "Which box did I put storage label MINT-84 on?",
    "save the invoice to Documents",
    "keep the biggest one",
    "what did I save in Documents?",
    "and the other one?",
    "remember when we moved the audit?",
    "remember that the meeting is at 3?",
)


def _owns(text: str) -> bool:
    from core.persistent_memory import memory_command_owns_turn

    return memory_command_owns_turn(text)


@pytest.mark.parametrize("text", OWNED)
def test_the_memory_lane_owns_its_commands(text):
    assert _owns(text), text


@pytest.mark.parametrize("text", NOT_OWNED)
def test_files_asks_and_idioms_are_not_memory_commands(text):
    assert not _owns(text), text


@pytest.mark.parametrize("text", OWNED)
def test_a_memory_command_is_never_admitted_as_a_follow_up(text):
    session = "clean-loop-follow-up-memory"
    route.note_clean_turn(session)
    decision = route.decide(text, source_context={"_owner_local": True}, session_id=session,
                            resolve_brain=lambda: object(), env={})
    assert not decision.admit and decision.reason == "memory_command", decision


def test_a_files_follow_up_is_still_admitted():
    session = "clean-loop-follow-up-files"
    route.note_clean_turn(session)
    decision = route.decide("and the biggest one?", source_context={"_owner_local": True}, session_id=session,
                            resolve_brain=lambda: object(), env={})
    assert decision.admit and decision.reason == "admitted", decision


def _routes(home: Path) -> list[str]:
    log = home / "data" / "logs" / "vool_api.log"
    found = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = str(row.get("msg") or "")
        if message.startswith("clean loop route: "):
            found.append(message.removeprefix("clean loop route: "))
    return found


def test_served_memory_commands_after_a_files_question_reach_the_memory_lane(served):
    """The real daemon and /api/chat door with the certified scripted model and the clean loop at its default."""
    daemon, home = served["daemon"], served["home"]
    chat = "clean-loop-then-forget"

    def say(text: str) -> str:
        return _reply_text(daemon.chat(text, session_id=chat, mode="auto")).strip()

    assert say("Remember that storage label MINT-84 goes on the blue box.").startswith("Locked in")
    assert say("Which box did I put storage label MINT-84 on?")
    assert _routes(home)[-1] == "admitted", "probe is blind: the files question never reached the clean loop"

    forgot = say("Forget storage label MINT-84.")
    remembered = say("Remember that my harbor lantern is painted signal green.")
    listed = say("What do you remember?")

    assert forgot.startswith("Forget applied. Removed "), forgot
    assert remembered.startswith("Locked in"), remembered
    assert "signal green" in listed and "MINT-84" not in listed, listed
    assert _routes(home)[-3:] == ["memory_command"] * 3, _routes(home)
    entries = (home / "data" / "memory_entries.jsonl").read_text(encoding="utf-8")
    assert "MINT-84" not in entries and "signal green" in entries
