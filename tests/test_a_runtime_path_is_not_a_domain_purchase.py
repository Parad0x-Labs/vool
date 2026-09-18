"""A build request was answered with the `.null` name-registration blurb. Twice, in one second.

Found by a QA build drive on 2026-07-30 against the live daemon. Verbatim::

    hey, could you knock together /Users/<me>/.vool_runtime/workspace/r2/roman.py that converts
    an integer to roman numerals? cheers

came back with ICANN, SOL, the on-chain registration fee and the registrar program id — for a
request to write a Python file. Reproduced on demand in 1s, so this is a deterministic matcher, not
a model wobble.

TWO independent bare-substring collisions, and it needed both to fire:

    ".null" matched inside the FILESYSTEM PATH ".vool_runtime"
    "get"   matched inside the ordinary English word "to-get-her"

Both are the same mistake this codebase keeps removing: a keyword matcher reading text inside a
path, and a marker list matched by containment rather than as words. `prose_only()` was written for
the first one and this matcher simply was not calling it.

The tightening has to survive in BOTH directions, which is why the real questions are pinned here in
the same file: `.null` carries no leading boundary (a name is written `alice.null`), and the
buy/register stems stay open at the end so "registering" and "buying" still count.
"""
from __future__ import annotations

import pytest

from core.web0_project_grounding import looks_like_web0_null_registration_question as asks_to_register

WS = "/Users/example-user/.vool_runtime/workspace"


# ---------------------------------------------------------------------------
# 1. A path on this disk is not a name to buy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        f"hey, could you knock together {WS}/r2/roman.py that converts an integer to roman numerals? cheers",
        f"create {WS}/r1/palindrome.py with a function that checks if a string is a palindrome",
        f"knock together a script in {WS}/x and get it working",
        f"list the files in {WS}",
        f"read {WS}/notes.md and get me a summary",
        f"write {WS}/qa/fizz.py, then we can put it all together",
        "put it all together and get me a summary",
        "get the tests passing, then we can talk",
    ],
)
def test_a_build_request_is_not_a_name_registration_question(prompt: str) -> None:
    assert asks_to_register(prompt) is False


def test_the_two_collisions_are_pinned_separately() -> None:
    """Either half alone must be harmless, so the pair cannot come back one at a time."""

    # ".null" inside a path, with a genuine buy word present.
    assert asks_to_register(f"buy me a coffee and read {WS}/a.py") is False
    # "get" inside "together", with a genuine .null token present.
    assert asks_to_register("lets put alice.null and bob together") is False


def test_a_directory_that_merely_starts_with_null_is_not_a_null_name() -> None:
    assert asks_to_register("register the handler in /srv/.nullary/config.py") is False
    assert asks_to_register("can i buy nullable.py") is False


@pytest.mark.parametrize(
    "prompt",
    [
        "what is .vool_runtime for, and can i get rid of it?",
        "can i get rid of the .vool_runtime folder",
        "is .nullable a real package i can get",
        "should i buy a .nullary licence",
    ],
)
def test_a_longer_word_starting_with_null_is_not_a_null_name(prompt: str) -> None:
    """No path here, so `prose_only` cannot help: these are bare tokens in ordinary prose. Only the
    trailing boundary distinguishes `.null` from `.vool_runtime`, `.nullable` and `.nullary`.
    Written because a sabotage that removed that boundary left the rest of this file green."""

    assert asks_to_register(prompt) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "get me the contents of /Users/me/.null/config.py",
        "buy nothing, just read /srv/web0/notes.md",
        "register the route in /opt/null_registrar/app.py",
    ],
)
def test_a_marker_spelled_exactly_inside_a_path_is_still_only_a_path(prompt: str) -> None:
    """The word boundaries alone cannot save these: `/.null/`, `/web0/` and `/null_registrar/` are
    the markers spelled exactly, with a non-word character on each side. Only refusing to read
    filesystem paths as prose does. Written because a sabotage that removed `prose_only` left every
    other case in this file green -- the boundary check was silently covering for it."""

    assert asks_to_register(prompt) is False


# ---------------------------------------------------------------------------
# 2. The real question still gets the real answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "how do i register a .null name?",
        "can i buy a .null domain?",
        "what does .null cost to register",
        "is alice.null available?",
        "resolve bob.null for me",
        "how much to register a dot null name",
        "can i buy a web0 name",
        "whats the null registrar program id and can i register there",
        "is registering a .null name free?",
        "i am buying a .null name",
        "has alice.null been claimed?",
        "how much does it cost to buy a .null name?",
        "i want to reserve my-name.null",
    ],
)
def test_a_real_registration_question_still_reaches_the_grounded_answer(prompt: str) -> None:
    assert asks_to_register(prompt) is True


def test_a_name_carries_no_leading_boundary() -> None:
    """`alice.null` is how a name is written. Demanding a non-word character before the dot loses
    every real question, which a first attempt at this fix did."""

    assert asks_to_register("is alice.null available?") is True
    assert asks_to_register("is .null available?") is True


def test_the_buy_stems_survive_inflection() -> None:
    """A word boundary at BOTH ends dropped "registering", which a gauntlet test caught."""

    for prompt in ("is registering a .null name free?", "i am buying a .null name", "who resolved alice.null"):
        assert asks_to_register(prompt) is True
