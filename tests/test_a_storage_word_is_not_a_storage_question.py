"""A storage word in a sentence is not a request for this machine's storage.

Eighth instance of the defect this repo keeps finding: a topic word claims a whole turn. Measured
on the deployed build 2026-07-30 (commit e3f59af), eight ordinary sentences out of twelve were
answered with a drive report in about half a second each -- a warehouse out of floor space, a
drummer's hard drive of samples, what a 1990s hard drive held, a grammar fix on a sentence about a
full server, a request to write onboarding copy about why disk space matters. One of them, a
question about a basement included in the rent, was answered with a scan of the home directory,
a ranking of its largest folders, and an offer to delete temp files.

The fix is the rule set by `_greeting_is_the_whole_message`: decide on what the sentence ASKS FOR.
So the negative cases below are deliberately NOT a blocklist -- each one is here because of its
SHAPE (prose with another subject, a request to produce text, a general-knowledge question, a
topic outside the clause carrying the ask), and the positives prove the shape test did not buy
that by refusing real questions.
"""
from __future__ import annotations

import pytest

from core.execution.constants import (
    local_fact_capability_required,
    machine_diagnostics_intent,
)
from core.operator.parser import parse_operator_action_intent

# Verbatim from the 2026-07-30 live run. Each was answered with local drive numbers.
ORDINARY_PROSE_THAT_MENTIONS_STORAGE = (
    "Our warehouse is out of floor space so the pallets of hard drive spares are stacked in the corridor.",
    "The band needs more stage space before the drummer can set up his hard drive of samples.",
    "In the 1990s a typical hard drive held 40 megabytes and cost more than the rest of the machine.",
    "Can you explain the difference between disk space and memory to someone who has never used a computer?",
    "My landlord says the storage space in the basement is included in the rent, is that normal in Lithuania?",
    "Write a paragraph for our onboarding doc explaining why disk space matters when you record video.",
    'Review this sentence for grammar: "Their is not enough disk space left on the server for the backup."',
    "Why did early Macs use a floppy disk instead of a hard disk, historically speaking?",
    "The recipe says to leave space between the cookies on the tray so they do not fuse together.",
    "I am drafting an email to the vendor asking how much storage their plan includes per user per month.",
    "Our team drive at work is a shared drive on the office NAS, how should we name files on it?",
    "What does the phrase \"give me some space\" mean when a colleague says it in a meeting?",
)

# Retest set: written after the fix landed, never used to shape it.
MORE_ORDINARY_PROSE = (
    "The moving company charges by the cubic metre of storage space, which seems steep to me.",
    "He kept the backup tapes next to an old hard disk he never got round to throwing out.",
    "Summarise this support ticket: the customer says the upload failed because of disk space.",
    "Translate into Lithuanian: there is not enough space left in the overhead locker.",
    "In chess, giving your bishop more space usually matters more than material.",
    "Our storage unit rental went up again, and the drive over there takes forty minutes.",
    "Document the deployment step where we check that the volume has been mounted correctly.",
    "A partition in the office would give the designers more space to spread out drawings.",
    "The parking garage on Gedimino has no space on the lower level after eight in the morning.",
    "What is a reasonable amount of free space to leave on a build server, as a rule of thumb?",
)

# Real requests, in deliberately varied shapes: a question, a bare noun phrase, an imperative,
# a complaint, a named drive. A fix that stops over-claiming by under-claiming is not a fix.
REAL_STORAGE_QUESTIONS = (
    "how much disk space do i have left",
    "disk space",
    "disk usage",
    "what's my free space right now",
    "how many drives are attached to this machine?",
    "show me the drive space",
    "how much space is left on the ssd",
    "do i still have room on my hard drive",
    "list my drives",
    "how much storage do i have",
    "whats the disk usage on this mac",
    "give me a drive space report",
    "what's left on my hard drive?",
    "my hard drive is nearly full",
    "total space used on C:",
    "disk space on C:",
    "what is my D drive total space?",
    "how much free space is on my drive?",
    "what's my total free space",
    "how much storage is left",
    # An apostrophe people drop constantly. Measured live 2026-07-30: "whats my free space right
    # now" was refused with "I can only answer that by inspecting your drives on your machine, and
    # that tool didn't run" while "what's my free space right now" answered from the disk in 0.2s.
    # The backstop was right that a drive read was needed; the read simply had no route.
    "whats my free space right now",
    "whats my free space",
    "whats my total free space",
    "whats my available space",
)


@pytest.mark.parametrize("prose", ORDINARY_PROSE_THAT_MENTIONS_STORAGE + MORE_ORDINARY_PROSE)
def test_ordinary_prose_does_not_run_the_disk_tool(prose: str) -> None:
    assert machine_diagnostics_intent(prose) is None, prose


@pytest.mark.parametrize("prose", ORDINARY_PROSE_THAT_MENTIONS_STORAGE + MORE_ORDINARY_PROSE)
def test_ordinary_prose_does_not_run_the_operator_storage_scan(prose: str) -> None:
    intent = parse_operator_action_intent(prose)
    assert getattr(intent, "kind", None) != "inspect_disk_usage", prose


@pytest.mark.parametrize("question", REAL_STORAGE_QUESTIONS)
def test_a_real_storage_question_still_reaches_the_disk_tool(question: str) -> None:
    assert machine_diagnostics_intent(question) == "machine.disk_usage", question


@pytest.mark.parametrize("prose", ORDINARY_PROSE_THAT_MENTIONS_STORAGE + MORE_ORDINARY_PROSE)
def test_ordinary_prose_is_not_refused_as_an_unrun_machine_read(prose: str) -> None:
    """The fabrication backstop REFUSES a turn, so a false positive is worse than a mis-route.

    After the two lanes above were narrowed, the basement question was still answered "I can only
    answer that by inspecting your drives on your machine, and that tool didn't run" -- measured
    live 2026-07-30. `\\bmy\\b` had matched "my landlord" and "storage" had matched the drive topic.
    """
    assert local_fact_capability_required(prose) is None, prose


@pytest.mark.parametrize(
    ("question", "capability"),
    (
        ("how much free space do i have", "your drives"),
        ("how many drives do i have", "your drives"),
        ("what is my screen resolution", "the display"),
        ("what cpu do i have", "this machine's hardware"),
        ("how much ram does this machine have", "this machine's hardware"),
        ("whats my biggest folder", "the largest files or folders"),
        ("what processes are running on my machine", "running processes"),
    ),
)
def test_a_real_local_fact_question_still_requires_its_tool(question: str, capability: str) -> None:
    assert local_fact_capability_required(question) == capability, question


def test_the_operator_scan_still_claims_the_asks_it_owns() -> None:
    for ask in (
        "what is eating space on this mac",
        "find large files",
        "show me disk bloat",
        "biggest files on my drive",
        "check disk space usage",
        "space usage report please",
    ):
        intent = parse_operator_action_intent(ask)
        assert getattr(intent, "kind", None) == "inspect_disk_usage", ask
