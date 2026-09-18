"""Asking for a nicer layout is not asking to erase a volume.

Measured on the served surface, in a session about car specifications::

    U: format the answer with tables and grpahs pls
    -> task_class=risky_system_action  risk_flags=['destructive_command']

`destructive_command` is in the hard_block set (core/agent_runtime/runtime_gate_policy.py:9-15),
so `default_gate` returns `mode="blocked"`, `allowed_actions=[]`, and `requires_user_approval=False`
-- a refusal with no approval path. The user asked for tables five times across that session and was
refused every time without being told why.

The cause is narrow and was half-fixed already. An earlier repair established that a COMMAND token
is only dangerous at a command position -- start of input or line, or after a shell separator -- so
"PDF format so agent needs to read it" stopped firing. But "format the answer..." *starts* with the
word, so it satisfies command position exactly as `format c:` does. Position cannot separate them.

What separates them is the argument. `format` erases something only when pointed at a device or
volume, so it now requires one on the same line. `disk` on its own is deliberately not a device
marker -- "disk usage" is ordinary English.

Scope: plain-English destructive phrasing ("format my external drive") is out of this predicate and
is classified exactly as "delete all my files" already is. That is the pre-existing design for this
table -- COMMAND tokens are judged as commands -- and is asserted below as a control so a future
edit does not quietly change it while thinking it is fixing this.
"""

from __future__ import annotations

import pytest

from core.task_router import classify


def _flags(text: str) -> list[str]:
    return list(classify(text).get("risk_flags") or [])


def _blocked(text: str) -> bool:
    return "destructive_command" in _flags(text)


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproductions, verbatim including the typo
# ---------------------------------------------------------------------------------------------


def test_the_reported_formatting_request_is_not_a_destructive_command() -> None:
    result = classify("format the answer with tables and grpahs pls")

    assert result["task_class"] != "risky_system_action"
    assert not result.get("risk_flags"), result.get("risk_flags")


def test_the_second_reported_phrasing_is_also_clean() -> None:
    assert not _flags("format the answer with tables and graphs pleas")


# ---------------------------------------------------------------------------------------------
# CLEAN -- the same request, none of the reported wording
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "format this as a donut chart",
        "format the results as a matrix",
        "format your reply so I can paste it into Word",
        "format the list alphabetically",
        "format that in markdown",
        "format the numbers to two decimal places",
        "format the citation in APA style",
        "format the timestamps as ISO 8601",
    ),
)
def test_layout_requests_are_never_destructive(request_text: str) -> None:
    assert not _flags(request_text), request_text


@pytest.mark.parametrize(
    "request_text",
    (
        "format teh answer as a table",
        "FORMAT THE ANSWER WITH TABLES",
        "  format the answer with a chart  ",
        "format\tthe answer as bullets",
    ),
)
def test_sloppy_variants_of_the_same_request(request_text: str) -> None:
    """Typos, shouting and stray whitespace are how the request actually arrives."""

    assert not _flags(request_text), request_text


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- detection must not have been bought by weakening it
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    (
        "format c:",
        "format c: /fs:ntfs",
        "format /q d:",
        "format d:\\",
        "format /dev/sda1",
        "format /dev/disk2",
        "format disk2",
        "format disk0s1",
        "format sda",
        "format nvme0n1",
        "format mmcblk0",
    ),
)
def test_a_real_disk_format_is_still_a_destructive_command(command: str) -> None:
    assert _blocked(command), f"{command!r} slipped through"


@pytest.mark.parametrize(
    "command",
    (
        "format /dev/rdisk3",
        "format /dev/loop0",
        "format /dev/mapper/vg0-root",
    ),
)
def test_a_dev_node_no_other_marker_can_reach_is_still_caught(command: str) -> None:
    """`/dev/...` is load-bearing on its own, and the family above did not prove it.

    Added after a sabotage run: deleting the `/dev/` alternative left every test green, because
    `/dev/sda1` and `/dev/disk2` are also caught by the kernel-name markers. These three are not --
    `\\bdisk\\d+` cannot match inside `rdisk3`, and `loop0` and `mapper` match nothing else. macOS
    erases through `/dev/rdisk`, so this is the live path, not a curiosity.
    """

    assert _blocked(command), f"{command!r} slipped through"


@pytest.mark.parametrize("command", ("rm important.txt", "del important.txt"))
def test_a_plain_delete_command_needs_no_device_argument(command: str) -> None:
    """The new requirement applies to `format` alone, and nothing else may inherit it.

    Added after a sabotage run: extending `_REQUIRES_DEVICE_ARGUMENT` to `rm` left the family green,
    because every `rm` case here also carried `-rf` or `sudo` and was flagged by another entry. A
    bare delete is the case that actually degrades.
    """

    assert _blocked(command), f"{command!r} lost its flag"


@pytest.mark.parametrize(
    "command",
    (
        "rm -rf /",
        "sudo chmod 777 /etc",
        "mkfs.ext4 /dev/sda1",
        "diskutil eraseDisk JHFS+ blank disk2",
        "echo hi && rm -rf ~/data",
        "crontab -e",
    ),
)
def test_the_other_risk_tokens_are_untouched(command: str) -> None:
    """The change is scoped to one token; every other detection must be exactly as it was."""

    assert classify(command)["task_class"] == "risky_system_action", command
    assert classify(command).get("risk_flags"), command


@pytest.mark.parametrize(
    "sentence",
    (
        "website displays most of data in PDF format so agent needs to read it",
        "can you export the report in CSV format for me",
        "formatting the output as JSON please",
        "ruff format --check .",
    ),
)
def test_the_earlier_prose_repair_still_holds(sentence: str) -> None:
    """The previous fix for this token stays green; this one is layered on top of it."""

    assert not _flags(sentence), sentence


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL near-misses
# ---------------------------------------------------------------------------------------------


def test_a_device_FAR_from_the_token_does_not_arm_it() -> None:
    """The argument window, exercised.

    Written first as a line bound -- "a device on a later line is a separate command" -- and it
    failed: `redact_text` collapses newlines to spaces before the predicate runs, so a whole message
    is one line by then and the guard was unreachable. The bound that holds is distance in tokens.
    Without it, any message that mentioned a device anywhere would re-arm every `format` above it.
    """

    assert not _blocked("format the answer as a table\nthe volume is /dev/disk2")
    assert not _blocked("format the answer as a table, and the volume is /dev/disk2")


def test_a_device_argument_next_to_the_token_still_arms_it() -> None:
    """The other side of that boundary, so the window cannot be silently narrowed to nothing."""

    assert _blocked("format the volume /dev/disk2")


def test_a_format_after_a_shell_separator_is_still_a_command() -> None:
    assert _blocked("cd /tmp && format c:")
    assert not _blocked("cd /tmp && format the output as csv")


def test_disk_alone_is_not_a_device_marker() -> None:
    """`disk` without a digit is ordinary English and must not arm the token.

    This is the deliberate boundary of `_DEVICE_ARGUMENT`. Widening it to a bare `disk` would put
    "format the disk usage summary as a table" back into a hard block.
    """

    assert not _blocked("format the disk usage summary as a table")
    assert not _blocked("format the answer about disk space")


def test_a_word_starting_with_format_is_still_not_the_command() -> None:
    """The word boundary from the earlier repair must survive this one."""

    assert not _flags("formats supported by the parser")
    assert not _flags("formatter settings for the repo")


def test_plain_english_destruction_is_out_of_scope_and_unchanged() -> None:
    """Control on the SCOPE, not an endorsement.

    "format my external drive" carries no device token and is classified exactly as "delete all my
    files" already is. Both are `unknown` here; consent for that class lives downstream. Pinned so a
    later edit cannot claim to have fixed this defect while quietly changing that behaviour instead.
    """

    assert classify("format my external drive")["task_class"] == classify(
        "delete all my files"
    )["task_class"]


# ---------------------------------------------------------------------------------------------
# The CONSEQUENCE -- the classification is not the harm; the gate is
# ---------------------------------------------------------------------------------------------


class _GateDecision:
    def __init__(self, *, mode, reason, requires_user_approval, allowed_actions):
        self.mode = mode
        self.reason = reason
        self.requires_user_approval = requires_user_approval
        self.allowed_actions = allowed_actions


class _Agent:
    GateDecision = _GateDecision


class _Plan:
    risk_flags: list[str] = []


def _gate_mode(text: str) -> str:
    from core.agent_runtime.runtime_gate_policy import default_gate

    return default_gate(_Agent(), _Plan(), classify(text)).mode


def test_the_formatting_request_is_no_longer_refused_by_the_gate() -> None:
    """What the user actually experienced. The predicate being right is not the same as the turn
    being allowed to run."""

    assert _gate_mode("format the answer with tables and grpahs pls") != "blocked"


def test_a_real_disk_format_is_still_refused_by_the_gate() -> None:
    """The safety property this change must not have cost."""

    assert _gate_mode("format c:") == "blocked"


@pytest.mark.parametrize(
    "request_text",
    (
        "format the answer with tables and grpahs pls",
        "format this as a donut chart",
        "format your reply so I can paste it into Word",
    ),
)
def test_the_request_lands_in_the_ordinary_chat_lane(request_text: str) -> None:
    """Not merely unblocked -- routed where an ordinary question goes.

    `chat_surface_execution_task_class` maps `unknown` to `chat_conversation`, so on the served
    surface these execute as plain chat turns. Asserting only "not blocked" would leave room for a
    future edit to park them in some degraded class and still pass.
    """

    from core.task_router import chat_surface_execution_task_class

    assert (
        chat_surface_execution_task_class(classify(request_text)["task_class"])
        == "chat_conversation"
    ), request_text
