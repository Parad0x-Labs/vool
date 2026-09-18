"""A greeting matched on the first four words must not claim the rest of the sentence.

An independent tester, given no idea what this codebase had changed and no suggested wording, sent

    "how are you doing on disk space right now? and ram too"

and got back "Running clean. What do you need?" — the greeting matcher claimed the turn on its
opening words and the actual question was dropped on the floor.

The guard in place was a hand-maintained blocklist of topic words ("create ", "file ", "price ",
"weather ", ...). `disk`, `ram`, `space` and `cpu` were simply not on it, and no list of forbidden
subjects can ever be finished — the same shape as the substring lists that made ordinary prose look
like a destructive command and a question about building look like an instruction to build.

Asking what REMAINS after the greeting inverts the problem: the caller no longer has to predict
every subject a user might raise, only whether they raised one.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import _greeting_is_the_whole_message

GREETINGS = (
    "how are you doing",
    "hows it going",
    "how are ya",
    "how are you doing today",
    "hey how are you doing mate",
    "how are you doing so far",
    "hows it going man",
    "you alive or what",
    "everything ok",
    "whats good",
    "how are you doing, all good?",
)

# A greeting that turns into a request. Every one of these is a real question wearing a hello.
GREETING_THEN_A_QUESTION = (
    "how are you doing on disk space right now? and ram too",
    "how are you doing with the migration",
    "how are you doing on time for the release",
    "hows it going with the api build",
    "how are you doing at reading pdfs",
    "how is everything going with my files",
    "hey how are you doing, can you check the workspace",
    "hows it going — what version of python is installed",
)


@pytest.mark.parametrize("text", GREETINGS)
def test_a_bare_greeting_is_still_a_greeting(text: str) -> None:
    """The fix must not be bought by refusing to recognise hello."""

    assert _greeting_is_the_whole_message(text), text


@pytest.mark.parametrize("text", GREETING_THEN_A_QUESTION)
def test_a_greeting_followed_by_a_request_is_not_a_greeting(text: str) -> None:
    assert not _greeting_is_the_whole_message(text), text


def test_the_topic_blocklist_is_gone() -> None:
    """A list of forbidden subjects can never be complete; this one shipped without disk or ram."""

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "fast_paths_utility.py"
    ).read_text(encoding="utf-8")

    position = source.index("_STATUS_CHECK_RE.search(phrase)")
    window = source[position:position + 400]
    assert "_greeting_is_the_whole_message" in window
    assert '"price ",' not in window and '"weather ",' not in window


def test_the_subject_the_tester_actually_used() -> None:
    """Pinned verbatim, because this is the sentence that exposed it."""

    assert not _greeting_is_the_whole_message(
        "how are you doing on disk space right now? and ram too"
    )
