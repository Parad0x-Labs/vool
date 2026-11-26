"""The task router reads its command words as whole words.

iMac gate 43 on the installed 7eaa8608c: "if a penguin wore roller skates, could it outrun me?" held "run " inside
"outrun", was classed shell_guidance, and the model, offered only sandbox.run_command, asked approval to `echo` the
question."""
from __future__ import annotations

import pytest

from core.task_router import _SHELL_WORD_RE


@pytest.mark.parametrize("text", [
    "could a cheetah outrun a horse over a mile?",
    "in a nutshell, why is the sky blue?",
    "my brother is a frontrunner in the race, any tips for cheering?",
    "what does a hermit crab do with an empty seashell?",
])
def test_words_that_only_hold_a_command_word_are_not_command_words(text):
    assert not _SHELL_WORD_RE.search(text)


@pytest.mark.parametrize("text", [
    "run ls in my downloads folder",
    "please execute the cleanup script",
    "what command shows disk usage?",
    "open a shell and list the files",
    "how do I open the terminal on a mac?",
])
def test_command_words_still_read_as_such(text):
    assert _SHELL_WORD_RE.search(text)
