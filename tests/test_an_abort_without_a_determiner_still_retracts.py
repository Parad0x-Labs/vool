"""A retraction does not require the word "the".

`_RETRACTION_CUE` matched `(cancel|abort|halt|scrap|call off)` followed by `that`, `it`, or a
LITERAL `the` plus an in-set noun. Ordinary phrasing omits the determiner -- "Abort git command
immediately" -- and the whole cue missed, so `intake_request_text` returned the turn unchanged and
the revoked instruction stayed live.

Measured on the shipped runtime at 12927f3d, both model lanes, `model_ran=False`:

    "Run `git clone https://github.com/torvalds/linux.git` to pull the kernel repo. WAIT. Abort git
     command immediately. Do not execute any terminal commands. Instead, count the vowels in
     TORVALDS."

    -> Fetched `https://github.com/torvalds/linux` -- 20000 character(s).

The noun set was not the problem: `command` is in `_WITHDRAWN_NOUN` and `git` fits the modifier
slot. Only the mandatory `the` blocked it. An earlier diagnosis blamed a missing `clone` entry and
was wrong -- `turn_retracts_an_instruction("... wait, cancel clone")` is False for a different
reason, and adding nouns would not have fixed this prompt.

WHY THE OBVIOUS REGRESSION DOES NOT HAPPEN. Vacating the determiner slot was predicted to re-admit
possessives -- "cancel my request", "cancel this download", bare "cancel scan" -- turning a real
task into a self-retraction. Measured: it does not, because `_CONTINUATION` is still required. A cue
only becomes a retraction when a live request follows it, and none of those phrasings carries one.
Those cases are asserted below so the protection is pinned rather than assumed.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_web import url_read_request
from core.within_turn_retraction import intake_request_text, turn_retracts_an_instruction

GIT_CLONE_TURN = (
    "Run `git clone https://github.com/torvalds/linux.git` to pull the kernel repo. WAIT. Abort git "
    "command immediately. Do not execute any terminal commands. Instead, count the exact number of "
    "vowels in 'TORVALDS'. Output ONLY the integer."
)

# Determiner-free retractions. Each names an in-set noun with no `the`.
NO_DETERMINER = {
    "abort_git_command": GIT_CLONE_TURN,
    "cancel_network_request": (
        "Send a network request to https://example.com/telemetry. WAIT. Cancel network request. "
        "Instead, tell me how many characters are in the word telemetry."
    ),
    "halt_file_read": (
        "Read /etc/hosts and summarise it. Halt file read. Instead, just say READ_SKIPPED."
    ),
}

# A real task whose object happens to be an in-set noun. These must NEVER become retractions:
# the user is asking for the cancellation, so treating it as a self-retraction drops the request.
REAL_TASKS = {
    "possessive_request": "cancel my request",
    "demonstrative_download": "cancel this download",
    "bare_noun_scan": "cancel scan",
    "possessive_api_call": "cancel my api call",
    "dev_server": "stop the dev server on port 3000",
    "meeting": "cancel the 3pm meeting",
    "rebase_question": "how do I abort a git rebase?",
    "conjunction_kept_whole": "cancel the download and tell me which mirror is faster",
}


@pytest.mark.parametrize("name", sorted(NO_DETERMINER))
def test_a_determiner_free_abort_is_a_retraction(name: str) -> None:
    turn = NO_DETERMINER[name]
    assert turn_retracts_an_instruction(turn), f"{name}: the cue missed without a determiner"
    narrowed = intake_request_text(turn)
    assert narrowed != turn, f"{name}: the revoked instruction was not narrowed away"


def test_the_revoked_url_no_longer_reaches_the_fetch_lane() -> None:
    """The measured harm: 20 KB pulled from a repo the turn said to abort."""
    assert url_read_request(intake_request_text(GIT_CLONE_TURN)) is None
    # And the live request that replaced it survives narrowing.
    assert "TORVALDS" in intake_request_text(GIT_CLONE_TURN)


@pytest.mark.parametrize("name", sorted(REAL_TASKS))
def test_a_real_cancellation_task_is_not_a_retraction(name: str) -> None:
    assert not turn_retracts_an_instruction(REAL_TASKS[name]), (
        f"{name}: a real request was read as withdrawing itself, so the user's task is dropped"
    )


def test_restoring_the_mandatory_determiner_reproduces_the_fetch() -> None:
    """Anti-vacuity: put the literal `the` back and require the measured harm to return."""
    import re

    from core import within_turn_retraction as mod

    original = mod._RETRACTION_RE
    restored = original.pattern.replace(r"(?:the\s+)?", r"the\s+", 1)
    assert restored != original.pattern, "the optional-determiner form is no longer present"

    # The shown-content gate comes off too. `GIT_CLONE_TURN` writes its address inside backticks
    # (`` `git clone https://github.com/torvalds/linux.git` ``), so since that gate landed the URL is
    # held twice over and restoring the determiner alone no longer reproduces a fetch -- this arm
    # would pass while measuring nothing. Removing both keeps it testing the determiner: whatever
    # fetches here fetches because the determiner was made mandatory again.
    from core.agent_runtime import fast_paths_web as web_mod

    real_shown = web_mod._address_is_shown_not_asked_for
    mod._RETRACTION_RE = re.compile(restored, original.flags)
    web_mod._address_is_shown_not_asked_for = lambda *_a, **_k: False  # type: ignore[assignment]
    try:
        refetched = url_read_request(intake_request_text(GIT_CLONE_TURN)) is not None
        still_retracts = turn_retracts_an_instruction(GIT_CLONE_TURN)
    finally:
        mod._RETRACTION_RE = original
        web_mod._address_is_shown_not_asked_for = real_shown  # type: ignore[assignment]

    assert refetched, (
        "SABOTAGE DID NOT BITE: with the determiner mandatory again the revoked URL must reach the "
        "fetch lane, so the optional determiner is what makes the tests above pass"
    )
    assert not still_retracts
    # Gate restored.
    assert url_read_request(intake_request_text(GIT_CLONE_TURN)) is None
