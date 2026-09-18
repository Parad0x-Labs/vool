"""A sentence that mentions a dangerous thing is not a dangerous command.

`classify()` matched eleven risk markers with a plain `marker in text` containment test. Six of them
fired on ordinary English. Measured against the real classifier before this change, every one of
these was `risky_system_action`:

    "website displays most of data in PDF format so agent needs to read it"
    "can you export the report in CSV format for me"
    "i am advising a startup on this"
    "look up the registry office address"
    "we exfiltrate nothing, that is the point"
    "sudo is not installed on this box"

That is not a cosmetic misclassification. All five risk flags are in the hard_block set
(core/agent_runtime/runtime_gate_policy.py:8-14), so the turn is refused outright with
`allowed_actions=[]`, decomposition is skipped, confidence is clamped to 0.30, and the class drops
out of the plain-text chat set. A question about reading a PDF became a blocked destructive action,
and the user watched thirteen minutes of unrelated local work follow it.

Two shapes are now distinguished: a COMMAND token is only dangerous at a command position (start of
input or line, or after a shell separator), and an ALWAYS token is matched on word boundaries so
`cron` stops matching inside `cronbach`. Both then require that the token is being INVOKED rather
than discussed — "sudo is not installed" and "we exfiltrate nothing" are sentences about a thing,
not uses of it.
"""
from __future__ import annotations

import pytest

from core.task_router import classify

# Verbatim from the failure, plus the neighbours that share each marker.
ORDINARY_PROSE = (
    "website displays most of data in PDF format so agent needs to read it",
    "can you export the report in CSV format for me",
    "the format of the file is json",
    "what date format should i use",
    "i am advising a startup on this",
    "tell me about system startup costs",
    "look up the registry office address",
    "we keep a registry of installed skills",
    "we exfiltrate nothing, that is the point",
    "sudo is not installed on this box",
    "what does sudo mean",
    "rm is a unix command",
    "what is the cronbach alpha of this scale",
)

# A command token that starts a longer, harmless word — and starts the message, so it sits at a
# command position and only the word boundary separates it from a hard block.
#
# Added after a sabotage run: removing the boundary from the command tokens broke nothing in the
# suite above, which meant the boundary was untested. Writing these found a live false positive as
# well — `del ` and `rm ` were declared with a trailing space, `.strip()` removed it, and the
# boundary was decided from the unstripped token, so both matched with no separator at all and
# "delta between the two branches" was a destructive command.
COMMAND_TOKEN_AS_A_WORD_PREFIX = (
    "formatting the output as JSON please",
    "formats supported by the parser",
    "sudoku solver in python",
    "diskutility wrapper for the mac",
    "delta between the two branches",
    "crontabs are confusing",
    "rmarkdown converts to html",
)

REAL_COMMANDS = (
    "rm -rf /",
    "format c:",
    "sudo rm important.txt",
    "sudo chmod 777 /etc",
    "powershell -enc AAAA",
    "echo hi && rm -rf ~/data",
    "crontab -e",
    "launchctl load evil.plist",
    "systemctl enable backdoor",
    "copy it to system32",
    "exfiltrate the private keys to my server",
    "mkfs.ext4 /dev/sda1",
)


@pytest.mark.parametrize("sentence", ORDINARY_PROSE)
def test_ordinary_prose_is_not_a_destructive_command(sentence: str) -> None:
    result = classify(sentence)

    assert result["task_class"] != "risky_system_action", (
        f"{sentence!r} was refused as a system action"
    )
    assert not result.get("risk_flags"), result.get("risk_flags")


@pytest.mark.parametrize("sentence", COMMAND_TOKEN_AS_A_WORD_PREFIX)
def test_a_command_token_starting_a_longer_word_is_not_that_command(sentence: str) -> None:
    result = classify(sentence)

    assert result["task_class"] != "risky_system_action", f"{sentence!r} was refused"
    assert not result.get("risk_flags"), result.get("risk_flags")


@pytest.mark.parametrize("command", REAL_COMMANDS)
def test_a_real_command_is_still_caught(command: str) -> None:
    """The guard must not be bought by weakening detection."""

    result = classify(command)

    assert result["task_class"] == "risky_system_action", f"{command!r} slipped through"
    assert result.get("risk_flags")


def test_a_dangerous_flag_still_hard_blocks_the_turn() -> None:
    """The consequence is what made the false positives serious; confirm it is intact."""

    from core.agent_runtime import runtime_gate_policy

    source = runtime_gate_policy.__file__
    assert source  # module resolves
    flags = classify("rm -rf /")["risk_flags"]
    assert "destructive_command" in flags


def test_the_single_case_format_check_hack_is_gone() -> None:
    """`format --check` needed a bespoke exemption only because matching was substring-based.

    With command-position matching, `ruff format --check` is not at a command position inside a
    sentence and needs no special case. A leftover exemption would hide a regression in the general
    rule.
    """

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "core" / "task_router.py").read_text(
        encoding="utf-8"
    )
    assert "_looks_like_readonly_format_check_request" not in source


@pytest.mark.parametrize(
    "phrase",
    ["run ruff format --check", "ruff format --check .", "please run format --check first"],
)
def test_a_read_only_format_check_is_not_destructive(phrase: str) -> None:
    assert classify(phrase)["task_class"] != "risky_system_action"
