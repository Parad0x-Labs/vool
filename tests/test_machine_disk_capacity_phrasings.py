"""A capacity question about this host has to reach the disk, whatever words it is asked in.

Measured live 2026-07-31 against the running daemon on d51250a, driving ordinary phrasings a person
would type. The disk phrase tables enumerated wordings rather than the question being asked, so 20 of
22 ordinary capacity questions never reached `df`:

  "am i running out of storage"              -> "I couldn't get a usable model response in this run"
  "whats the storage situation looking like"  -> "I couldn't get a usable model response in this run"
  "do i have room for a 50gb download"        -> "I couldn't get a usable model response in this run"
  "how many gb are left on this machine"      -> answered with the SPEC SHEET ("RAM: 24.0 GiB")

The last one is the reason this file exists: a wrong number delivered confidently is worse than the
refusal, and the right number was one `df` call away. The fix matches the RELATION a capacity
question expresses -- remaining, exhaustion, fullness, fit -- instead of the strings it is spelled
with, so these tests are written as the relation too: each phrasing below is a different wording of
the same four asks.
"""

from __future__ import annotations

import pytest

from core.execution.constants import machine_diagnostics_intent

# Verbatim from the live run that failed, plus the same relations spelled other ways.
REACHES_THE_DISK = [
    # -- the four measured failures, verbatim --
    "am i running out of storage",
    "how many gb are left on this machine",
    "whats the storage situation looking like",
    "do i have room for a 50gb download",
    # -- remaining --
    "how many gb do i have left",
    "whats left on my drive",
    "how much is left on my ssd",
    "how much room is left on my ssd",
    "how much space have i got left",
    "how much storage is left",
    # -- exhaustion --
    "am i low on disk space",
    "am i nearly out of space",
    # -- fullness --
    "how full is my disk",
    "how full is my ssd",
    "is my disk getting full",
    "is my drive nearly full",
    "how much of my disk is used",
    # -- fit --
    "do i have enough space for a 30gb file",
    # -- state --
    "whats my storage looking like",
    # -- second live round, 2026-07-31, against the first version of this fix. Six more ordinary
    # phrasings still missed: the amount words alone did not cover naming the DEVICE as the thing
    # running out ("running out of disk"), the modifier leading its noun ("remaining capacity",
    # "free room"), "the state of my storage", or the first-person word order "space i have left".
    "tell me how much space i have left please",
    "have i got enough disk for a 20gb video",
    "whats the state of my storage",
    "how much free room is on the drive",
    "am i running out of disk",
    "whats my remaining capacity on this mac",
    "is my mac nearly out of room",
    "how much of the ssd have i used up",
]

# The same relations about something that is not a storage device on this host. These are what stop
# the widening above from becoming "any sentence with the word space in it runs df".
STAYS_OFF_THE_DISK = [
    "do i have room for another meeting",
    "how much room is left in the venue",
    "how much room is left in the car",
    "is there enough room in the fridge",
    "is there room for one more person",
    "how many gb does the free tier give you",
    "is there room in the budget for a 50gb plan",
    "do we have room for a 50gb plan in the account",
    "am i running out of time",
    "are we running out of milk",
    "our warehouse is running out of floor space",
    "am i running low on patience",
    "am i low on coffee",
    "is my calendar looking full",
    "how full is my inbox",
    "free space in my calendar",
    "how much storage does my cloud plan give me",
    # Prose that merely contains the words -- write/explain/history, not a measurement.
    "write onboarding copy about why disk space matters",
    "how much did a hard drive hold in the 1990s",
    "the drummer has a hard drive full of samples",
    "fix the grammar in this sentence: the server is running out of space",
    # A cleanup verb makes it a write request, which belongs to the operator action lane.
    "clean up disk space",
    # Widening the relation to let the modifier lead its noun ("free room", "remaining capacity")
    # puts "free" next to a great many nouns that are not storage. These are the ones that would
    # start reading `df` if that widening were done without the ambiguity anchor.
    "how many free seats are left",
    "is there space for a free upgrade",
    "we are running out of runway",
    "how much free time do i have left",
]


@pytest.mark.parametrize("phrasing", REACHES_THE_DISK)
def test_a_capacity_question_about_this_host_reaches_the_disk(phrasing: str) -> None:
    assert machine_diagnostics_intent(phrasing) == "machine.disk_usage", (
        f"{phrasing!r} asks how much room this machine has left and the runtime can answer it "
        f"from df; routing it anywhere else refuses the user an answer it holds."
    )


@pytest.mark.parametrize("phrasing", STAYS_OFF_THE_DISK)
def test_the_same_relation_about_something_else_does_not_read_the_disk(phrasing: str) -> None:
    assert machine_diagnostics_intent(phrasing) is None, (
        f"{phrasing!r} is not a question about storage on this host; answering it with a drive "
        f"report is the failure mode the phrase tables were narrowed to prevent."
    )


def test_the_spec_sheet_is_not_an_answer_to_how_much_room_is_left() -> None:
    """The measured mis-answer, pinned as its own case.

    "how many gb are left on this machine" was answered with the hardware spec sheet -- RAM, chip,
    core count. RAM is a number in gigabytes, which is exactly why the wrong family claimed it, and
    exactly why it is worth a dedicated regression: the failure is not that no tool ran, it is that
    a confident answer to a different question was returned.
    """
    assert machine_diagnostics_intent("how many gb are left on this machine") == "machine.disk_usage"
