"""Asking for the TEXT of an artifact is authoring, not an action (MF-13).

Measured live 2026-08-15 (session dbac716c..., receipts in runtime_session_events):

- 12:07 "Draft an urgent email... WAIT. Cancel the email. Do not send anything... Instead, write a
  bash one-liner that finds all `.log` files in `/var/log` modified in the last 24 hours and
  forcefully deletes them." -> task_classified integration_orchestration -> autopilot routed
  output_mode=tool_intent -> the model, forced to emit a tool call for a text request, produced a
  meaningless operator.list_tools -> approval gate pended it -> final answer "I wasn't able to turn
  that into a completed action." (three separate submissions, same outcome)
- 10:44 "write me the exact bash find command..." -> "Manual mode requires approval..."
- 10:44 "draft a polite email to David..." -> refused for lacking a verified outbound channel
  nobody asked it to use.

The invariant: a request whose deliverable is the TEXT of an artifact (command, script, query,
email, message, code) is generative; only a request to EXECUTE, SEND, or MATERIALIZE it is an
action. Two seams repaired together: the authoring channel in
core.instructional_request.asks_for_instructions_not_execution, and the classification ladder in
core.task_router.classify, which previously sent these turns to the action classes on the words
"email"/"command"/"shell".
"""

from __future__ import annotations

import pytest

from core.instructional_request import asks_for_instructions_not_execution as asks_how
from core.task_router import classify

_ACTION_CLASSES = {"integration_orchestration", "shell_guidance", "file_inspection"}

MEASURED_BASH_TURN = (
    "Draft an urgent email to the dev team telling them the production Postgres database is down "
    "and we are losing data! WAIT. Cancel the email. Do not send anything, do not use email tools. "
    "Instead, write a bash one-liner that finds all `.log` files in `/var/log` modified in the "
    "last 24 hours and forcefully deletes them."
)


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproductions
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn",
    (
        MEASURED_BASH_TURN,
        "write a bash one-liner that finds all `.log` files in /var/log modified in the last "
        "24 hours and forcefully deletes them",
        "write me the exact bash find command to list files over 100MB",
        "draft a polite email to David asking to move our meeting to Thursday",
    ),
)
def test_the_measured_turns_are_authoring_requests(turn: str) -> None:
    assert asks_how(turn), turn


@pytest.mark.parametrize(
    "turn",
    (
        MEASURED_BASH_TURN,
        "write me the exact bash find command to list files over 100MB",
        "draft a polite email to David asking to move our meeting to Thursday",
    ),
)
def test_the_measured_turns_never_classify_as_actions(turn: str) -> None:
    assert classify(turn)["task_class"] not in _ACTION_CLASSES, turn


# ---------------------------------------------------------------------------------------------
# CLEAN VARIANTS -- other artifacts, other verbs, none from the reports
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn",
    (
        "write a TS interface for User with id, name, and optional email",
        "Write a raw SQLite query that creates a table named insurance",
        "compose a short reply to the landlord about the deposit",
        "draft a quick SMS to my wife saying I'll be late",
        "generate a regex that matches ISO dates",
        "can you write a python function that deduplicates a list",
        "write a kubectl command to drain a node",
        "draft an email to the whole team about the schedule change, do not send it",
    ),
)
def test_authoring_across_artifacts_and_verbs(turn: str) -> None:
    assert asks_how(turn), turn
    assert classify(turn)["task_class"] not in _ACTION_CLASSES, turn


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- real actions and observations stay on the action path
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn",
    (
        "write an email to Bob about the outage and send it",
        "draft the reply and then send it to David",
        "run the find command in the terminal for me",
        "execute this query against the production database",
        "check my inbox for anything urgent from the dev team",
        "read file config.py and tell me what it does",
        "write a script and save it to my project folder",
    ),
)
def test_actions_and_observations_are_not_authoring(turn: str) -> None:
    assert not asks_how(turn), turn


@pytest.mark.parametrize(
    ("turn", "expected_class"),
    (
        ("check my inbox for anything urgent from the dev team", "integration_orchestration"),
        ("read file server.py and tell me what it does", "file_inspection"),
        ("run ls in the terminal and show me the output", "shell_guidance"),
    ),
)
def test_real_action_turns_keep_their_action_classes(turn: str, expected_class: str) -> None:
    assert classify(turn)["task_class"] == expected_class, turn


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_destructive_content_inside_the_artifact_does_not_make_it_an_action() -> None:
    # The described BEHAVIOUR of the artifact ("deletes them") is content, not a request to act.
    turn = "write a shell one-liner that wipes every temp file older than a week"
    assert asks_how(turn)
    assert classify(turn)["task_class"] not in _ACTION_CLASSES


def test_the_prior_instructional_channel_is_unchanged() -> None:
    assert asks_how("give me the git command to squash the last 3 commits. raw text only")
    assert asks_how("how do I mount a volume in docker")
    assert not asks_how("how do I check my repo's status here")


def test_writing_a_file_is_still_a_machine_action() -> None:
    # "file"/"folder" are deliberately outside the artifact-noun set.
    assert not asks_how("write a file called notes.txt with hello in it")
    assert not asks_how("create a folder called drafts on my desktop")


# ---------------------------------------------------------------------------------------------
# THIRD SEAM -- the machine-write guard (found by the post-fix live drive, not by review)
# ---------------------------------------------------------------------------------------------


def test_an_authoring_turn_with_a_home_path_is_not_a_machine_write() -> None:
    """Live drive on d042dc79: the classification fix held, and THIS pre-classification seam
    claimed the fresh phrasing on "write" + "~/Documents" and refused it as an unprovable file
    write ("I won't pretend I created or changed files...")."""
    from core.agent_runtime.fast_paths_machine import looks_like_safe_machine_write_request

    assert not looks_like_safe_machine_write_request(
        "write me a one-liner for zsh that counts how many .txt files sit under ~/Documents"
    )
    assert not looks_like_safe_machine_write_request(
        "draft a note about what's on my desktop for the handover email"
    )


def test_a_real_file_write_still_reaches_the_write_lane() -> None:
    from core.agent_runtime.fast_paths_machine import looks_like_safe_machine_write_request

    assert looks_like_safe_machine_write_request(
        "write a file called notes.txt with hello in it on my desktop"
    )


# ---------------------------------------------------------------------------------------------
# FOURTH SEAM -- the tool-intent attempt decision (found by the second live drive)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "turn",
    (
        # Live 13:08 on 54500378: classification was CORRECT (chat_conversation) and this seam
        # still routed output_mode=tool_intent on "shell"/"Desktop", shipping "I wasn't able to
        # turn that into a completed action" over a plain generative answer.
        "write me a quick shell command that renames every .png on my Desktop to lowercase",
        "write me a one-liner for zsh that counts how many .txt files sit under ~/Documents",
        "draft a powershell script that zips the folders under my Documents",
    ),
)
def test_an_authoring_turn_never_attempts_a_tool_intent(turn: str) -> None:
    from core.execution.planner import should_attempt_tool_intent

    assert not should_attempt_tool_intent(
        turn, task_class="chat_conversation", source_context={"surface": "api"}
    )


@pytest.mark.parametrize(
    ("turn", "task_class"),
    (
        ("read file server.py and tell me what it does", "file_inspection"),
        ("create a file named log.txt on my desktop with hello inside", "chat_conversation"),
        ("search the web for the latest node LTS version", "research"),
    ),
)
def test_real_tool_turns_still_attempt_tool_intents(turn: str, task_class: str) -> None:
    from core.execution.planner import should_attempt_tool_intent

    assert should_attempt_tool_intent(
        turn, task_class=task_class, source_context={"surface": "api"}
    )
