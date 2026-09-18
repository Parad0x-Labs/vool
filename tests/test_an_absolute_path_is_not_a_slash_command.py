"""A leading "/" opens a command or an absolute path, and the runtime has to tell them apart.

Measured on c6eed761: `ui_command_fast_path` claimed EVERY message beginning with "/", and on macOS
and Linux every absolute path does. So a user pasting a path got a canned non-answer::

    /Users/<user>/project/core/task_router.py - whats the first thing this file does
      -> "That slash command is not wired here. Use plain language, the `New session` button..."

    /etc/hosts what is in this file                 -> same canned string
    /usr/local/bin/python --version, is that right? -> same canned string

A fixed string standing in for a real request is the failure mode this repo bans outright, and it
sat on the most ordinary thing a developer types.

The distinction pinned here is structural, not a list: a command is ONE leading token ("/new",
"/trace"), a path carries a separator inside that token. No command name and no directory prefix is
enumerated, so neither a new command nor a new layout needs a change here.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import ui_command_fast_path


def _claimed(text: str) -> bool:
    return bool(ui_command_fast_path(" ".join(text.split()).lower(), source_surface="api"))


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "/Users/someone/project/core/task_router.py — whats the first thing this file does",
        "/etc/hosts what is in this file",
        "/usr/local/bin/python --version, is that right?",
    ),
)
def test_the_measured_paths_are_no_longer_swallowed(request_text: str) -> None:
    assert not _claimed(request_text), request_text


# ---------------------------------------------------------------------------------------------
# CLEAN -- other absolute paths, none of them in the reproduction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "/var/log/system.log — anything odd in here",
        "/tmp/build.log tail it for me",
        "/opt/homebrew/bin/git which version is this",
        "/home/deploy/app/settings.py does this set DEBUG",
        "/System/Library/CoreServices whats in there",
    ),
)
def test_any_absolute_path_reaches_the_lanes_that_can_read_it(request_text: str) -> None:
    assert not _claimed(request_text), request_text


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- real commands must still be claimed
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ("/new", "/clear", "/reset", "/trace", "/rail", "/help", "/whatever", "/new please")
)
def test_real_slash_commands_are_still_claimed(command: str) -> None:
    assert _claimed(command), command


def test_the_wired_commands_still_return_their_own_guidance() -> None:
    """The claim must not become generic: /new and /trace each have their own answer."""

    new_session = ui_command_fast_path("/new", source_surface="api")
    trace = ui_command_fast_path("/trace", source_surface="api")

    assert new_session and "New session" in new_session
    assert trace and "/trace" in trace
    assert new_session != trace


def test_a_path_mentioned_mid_sentence_was_never_affected() -> None:
    """Control on the original guard: only a LEADING slash ever reached this path."""

    assert not _claimed("what does /var/log/system.log contain")
    assert not _claimed("open /etc/hosts for me")


def test_an_empty_or_bare_slash_does_not_crash() -> None:
    assert not _claimed("")
    assert ui_command_fast_path("/", source_surface="api") is not None


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_a_single_segment_path_is_indistinguishable_and_reads_as_a_command() -> None:
    """Stated boundary rather than a hidden one.

    "/tmp" alone carries no second separator, so it reads as a command. That is genuinely
    ambiguous -- the string is identical in shape to "/new" -- and the cost is a guidance message
    for a bare directory name, not a swallowed question. Anything with a path's actual shape
    ("/tmp/x", or a trailing question) is unaffected.
    """

    assert _claimed("/tmp")
    assert not _claimed("/tmp/build.log")


def test_a_windows_style_path_is_not_claimed() -> None:
    """It never starts with "/", so it was never at risk -- pinned so a future rewrite keeps that."""

    assert not _claimed(r"C:\Users\someone\project\main.py what does this do")
