"""A stipulation the user returns to is still theirs; one they retract is not.

Measured live on b465bcfa, local lane, three turns:

    U: assume our ci build takes 9 min and we run it 40x a day. how much ci time a week, 5 day week
    A: You spend 1800 minutes of CI time per week.          <- correct, 9 x 40 x 5
    U: unrelated - good pre-commit hook for trailing whitespace?
    A: <a pre-commit hook>                                   <- correct
    U: ok back to the ci math, what if we cut runs to 25 a day
    A: <a shell script to LIMIT ci runs to 25/day>           <- WRONG; 9 x 25 x 5 = 1125 was asked

`stipulated_frame_active` walked back exactly one user turn and returned. The intervening pre-commit
question is what it found, so the CI stipulation was out of reach and the model was handed a turn
with no frame -- it then read "cut runs to 25 a day" as an instruction to implement a limit.

Confirmed deterministically before any change: frame active directly after the setup, inactive after
one intervening turn.

The original one-turn bound was deliberate, and its reason is preserved: it stops "an old fictional
exercise from disabling retrieval for an unrelated conversation later in the same session". So the
reach-back requires SUBJECT OVERLAP -- an unrelated conversation shares no content terms and still
gets nothing. Only a turn that names the stipulated subject can reach past one turn, and only within
a bounded window.
"""

from __future__ import annotations

import pytest

from core.stipulated_frame import stipulated_frame_active

_SETUP = "assume our ci build takes 9 min and we run it 40x a day. how much ci time a week, 5 day week"

_HISTORY = [
    {"role": "user", "content": _SETUP},
    {"role": "assistant", "content": "You spend 1800 minutes of CI time per week."},
    {"role": "user", "content": "unrelated - good pre-commit hook for trailing whitespace?"},
    {"role": "assistant", "content": "A good pre-commit hook for trailing whitespace..."},
]


def _active(text: str, history=None) -> bool:
    return stipulated_frame_active(
        text, source_context={"conversation_history": list(_HISTORY if history is None else history)}
    )


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction
# ---------------------------------------------------------------------------------------------


def test_a_return_to_the_stipulated_subject_reaches_past_one_turn() -> None:
    assert _active("ok back to the ci math, what if we cut runs to 25 a day")


# ---------------------------------------------------------------------------------------------
# CLEAN -- other returns, other wordings, other subjects
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    (
        "ok back to the ci math, what if we cut runs to 25 a day",
        "what about that build number again",
        "returning to the ci build time, what about fewer runs",
        "as for the ci build, what if it drops to 6 min",
    ),
)
def test_an_explicit_return_to_the_stipulated_subject_keeps_the_frame(question: str) -> None:
    assert _active(question), question


@pytest.mark.parametrize(
    "question",
    (
        "and the ci time if the build drops to 6 min?",
        "recalculate the ci minutes for a 4 day week",
        # A stemming boundary rather than a rule: "runs" is four characters and the shared stemmer
        # only strips a trailing "s" past length four, so it never matches "run" in the setup.
        # Left here rather than tuned away -- widening the stemmer to satisfy one phrasing is how a
        # general rule turns into a fixture.
        "returning to the ci figures, what about 25 runs",
    ),
)
def test_a_subject_mention_without_a_return_cue_does_not_reach_back(question: str) -> None:
    """A stated boundary, and the safe side of it.

    These read to a human as continuing the CI thread, and subject overlap alone would let them.
    Overlap alone was tried and REVERTED: after "Assume it is 2040 and Buster is president" and an
    intervening haiku, "Who is the current president?" also overlaps -- on "president" -- and would
    have been answered from the fiction. tests/test_stipulated_frame_contract.py caught it.

    So the reach-back needs the user to say they are coming back. Declining here costs nothing new:
    the turn simply behaves as it did before this repair existed. Reaching wrongly would answer a
    real-world question from a stipulated one, which is the failure the one-turn bound was written
    for in the first place.
    """

    assert not _active(question), question


def test_a_different_stipulated_subject_behaves_the_same_way() -> None:
    """No CI, no build times -- the rule is about subject overlap, not this scenario."""

    history = [
        {"role": "user", "content": "assume the euro is at 1.20 dollars for this whole conversation"},
        {"role": "assistant", "content": "Understood."},
        {"role": "user", "content": "unrelated - whats a good name for a python package"},
        {"role": "assistant", "content": "Some options..."},
    ]

    assert _active("back to the euro, what is 300 of them in dollars", history)


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the original guard must survive intact
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    (
        "what is the current price of bitcoin",
        "whats the weather in Vilnius",
        "how do I bake sourdough bread",
        "what is the latest openssl release",
    ),
)
def test_an_unrelated_later_turn_gets_no_frame(question: str) -> None:
    """The reason the one-turn bound existed: an old frame must not disable retrieval later."""

    assert not _active(question), question


def test_the_immediate_follow_up_is_unchanged() -> None:
    """The case the original docstring was written for still decides outright."""

    assert _active("what if we cut runs to 25 a day", _HISTORY[:2])


def test_no_history_means_no_frame() -> None:
    assert not _active("what if we cut runs to 25 a day", [])


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL -- retraction must not be reactivated by its own subject
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "retraction",
    (
        "forget the assumptions, how long does our build actually take",
        "drop those numbers - what does our build actually take",
        "ignore my assumptions, what is the real ci time",
        "clear the stipulations and tell me the actual build time",
    ),
)
def test_a_retraction_is_never_reactivated_by_subject_overlap(retraction: str) -> None:
    """Introduced BY the reach-back and caught by this family.

    A retraction names the very subject it is cancelling, so subject overlap would have reactivated
    the frame the user just dropped. Before the reach-back existed the walk stopped early and got
    this right by accident; once it could reach, the exit vocabulary had to cover the stipulation
    itself and not only the scenario wrapper.
    """

    assert not _active(retraction), retraction


def test_the_scenario_wrapper_exits_still_work() -> None:
    assert not _active("back to reality, what does our ci build actually take")
    assert not _active("end the scenario - what is our real build time")


def test_the_reach_back_is_bounded() -> None:
    """A frame cannot govern a whole session, however often its subject is mentioned."""

    long_history = list(_HISTORY)
    for n in range(8):
        long_history.append({"role": "user", "content": f"unrelated question number {n}"})
        long_history.append({"role": "assistant", "content": f"answer {n}"})

    assert not _active("ok back to the ci math, what if we cut runs to 25 a day", long_history)
