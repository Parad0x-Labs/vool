"""An ordinal that belongs to the question's own subject is not a pointer into a list.

Measured 2026-11-13: "Which finishes first in a kiln, a slow glaze firing or a bisque firing?" was read as a pointer
into a list the chat never printed, so the turn was marked as pointing at nothing and the clean loop stood aside.
"what was the first man on the moon called" took the same path. A pointer carries a pointer noun ("the second one")
or stands under "the" and ends its clause there ("open the third").
"""
from __future__ import annotations

import pytest

from core import conversation_state as cs

LISTED = cs.PendingRequest(
    "list my downloads", cs.DONE, "executed",
    {"tool": "machine.list_directory", "path": "~/Downloads",
     "listed": [{"path": f"~/Downloads/{n}.png", "name": f"{n}.png"} for n in "abc"]}, 0,
)


@pytest.mark.parametrize("message", [
    "Which finishes first in a kiln, a slow glaze firing or a bisque firing?",
    "what was the first man on the moon called", "who came second in the 2024 tour de france",
    "is the first amendment about speech", "which is the last file i edited", "what is the first thing i should do",
    "the second world war started when", "who was the third president", "my first job was in a bakery", "when is the last train",
])
@pytest.mark.parametrize("pending", [None, LISTED], ids=["no-list", "fresh-list"])
def test_an_ordinal_of_the_questions_own_subject_points_at_nothing(message: str, pending) -> None:
    assert cs.resolve("s", message, pending=pending, cleared=None).kind == "none", message


@pytest.mark.parametrize("message", [
    "the third one", "open the second one", "show me the first one", "the last one please", "the 2nd", "whats in the second",
    "open the third", "the first two", "number 2", "delete the second file", "second one", "the third, please",
    "the first one is blurry, show the second",
])
def test_a_pointer_still_picks_from_the_list_or_says_there_is_none(message: str) -> None:
    assert cs.resolve("s", message, pending=LISTED, cleared=None).kind == "pick", message
    assert cs.resolve("s", message, pending=None, cleared=None).kind == "referent_unresolved", message
