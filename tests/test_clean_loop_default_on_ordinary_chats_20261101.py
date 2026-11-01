"""Default-on delivery 2026-11-01: ordinary chats keep the old pipeline, private chats keep theirs.

THE MEASURED DEFECT (delivery/clean-loop-08-default-on-20261101 @ 9c69f91b)
---------------------------------------------------------------------------
With the clean loop default ON, the no-trace contract
(``tests/test_private_chat_leaves_no_trace_v072.py``) failed twice over.

1. Ordinary chat: the control ("My cat is called Biscuit.") lost its ``turn_routing_events.jsonl``
   row. The route's ownership matcher admitted any bare possessive ("my cat ...", "what is my
   speed?") as an ask about the owner's own files or computer, so the clean loop claimed the turn
   and it never reached the routing-plan mint that writes the turn's provenance. The matcher now
   counts ownership only with a file action ("did i move the audit", "i saved the invoice").

2. Private chat: "The surprise party is at Quillmere Hall." was ADMITTED (it shares a word with a
   file name), and "pin this chat" after it came back as the brain's answer instead of the switch's
   refusal — the loop never fenced a private turn, so a private chat's words reached the loop's
   brain (with ``VOOL_CLEAN_LOOP_CLOUD`` on by default, potentially the owner's cloud model) and
   its pin/rename/move asks lost the private machinery's "private chat is on" refusal. The route
   now leaves every private turn to the private-chat machinery (``turn_is_private``).
"""
from __future__ import annotations

from core.clean_loop import route
from core.private_chat import PRIVATE_TURN_KEY

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}
#: The stamp observe_private_turn leaves on a private turn's source context (core.private_chat).
PRIVATE = {**OWNER, PRIVATE_TURN_KEY: True}


def _local_brain():
    return object()


def test_an_ordinary_my_statement_is_not_claimed_by_the_clean_loop():
    """A bare possessive with no file, folder or computer word is ordinary chat, and a private
    chat's turn is never this loop's to answer.

    Fails before the fix: the matcher read "my" alone as ownership and the loop admitted the turn
    (brain and all), so the turn never reached the routing-plan mint, its
    ``turn_routing_events.jsonl`` row was never written, and the no-trace contract's ordinary-chat
    control lost the store the scan must find. A private turn was not fenced at all: a private
    sentence sharing a word with one of the owner's file names was admitted, the session was noted
    as a clean session, and the next "pin this chat" was answered by the brain instead of the
    private machinery's refusal. The file-action asks at the end guard the other edge: ownership
    still counts when it comes with one of the owner's own file actions.
    """
    for ask, context, want_admit, want_reason in (
        ("My cat is called Biscuit.", OWNER, False, "not_about_own_files"),
        ("If I travel 90 miles in two hours, what is my speed?", OWNER, False, "not_about_own_files"),
        ("The surprise party is at Quillmere Hall.", PRIVATE, False, "private_turn"),
        ("pin this chat", PRIVATE, False, "private_turn"),
        ("did i move the audit", OWNER, True, "admitted"),
        ("i saved the invoice somewhere", OWNER, True, "admitted"),
        ("how much do i owe the plumber?", OWNER, True, "admitted"),
    ):
        decision = route.decide(ask, source_context=context, resolve_brain=_local_brain, env=ON)
        assert (decision.admit, decision.reason) == (want_admit, want_reason), (ask, decision.reason)
