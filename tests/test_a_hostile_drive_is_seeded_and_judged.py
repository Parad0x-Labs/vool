"""The hostile driver must be replayable by seed, and its judges must be able to say NO.

Two ways a random driver silently becomes worthless:

- UNSEEDED RANDOMNESS: a failure it finds cannot be reproduced, so the finding dies in the
  terminal. Same seed must mean byte-identical sessions.
- VACUOUS JUDGES: a judge that passes everything converts the whole drive into noise that
  reads as coverage. Every judge must demonstrably fail on the defect it exists to catch -
  the exact defects measured on the served app tonight (withdrawn tool ran, empty answer,
  markdown fence under a raw-only contract, offset-dropped clock).

All offline - builders and judges are pure; no network, no app.
"""

from __future__ import annotations

import datetime as dt
import random

from ops.hostile_user_drive import (
    Turn,
    _judge_contains,
    _judge_raw_code,
    build_sessions,
    judge_clock,
    perturb,
)


def _texts(seed: int) -> list[str]:
    return [t.text for s in build_sessions(seed, 5) for t in s]


def test_the_same_seed_replays_the_same_sessions() -> None:
    assert _texts(7) == _texts(7)


def test_a_different_seed_is_a_different_exam() -> None:
    assert _texts(7) != _texts(8)


def test_sessions_are_multi_turn_conversations() -> None:
    sessions = build_sessions(3, 8)
    assert all(len(s) >= 2 for s in sessions)


def test_perturbation_never_corrupts_a_protected_literal() -> None:
    rng = random.Random(0)
    for _ in range(200):
        out = perturb(rng, "if you cannot, output exactly ERR_KRNVTZ and nothing else",
                      protect="ERR_KRNVTZ")
        assert "ERR_KRNVTZ" in out


def test_the_retraction_judge_fails_when_the_withdrawn_tool_ran() -> None:
    """The measured turn-3 defect: search ran anyway. The judge must catch it from the
    payload, not the prose."""

    retraction_turns = [t for s in build_sessions(11, 20) for t in s if t.intent == "retraction"]
    assert retraction_turns, "the generator stopped producing retraction turns"
    turn = retraction_turns[0]

    ok, why = turn.judge("42 plus whatever", {"provenance": {"tool": "workspace.search_text"}})
    assert not ok
    assert "workspace.search_text" in why


def test_the_retraction_judge_fails_on_an_empty_answer() -> None:
    """The other half of turn 3: tokens spent, nothing delivered."""

    turn = next(t for s in build_sessions(11, 20) for t in s if t.intent == "retraction")

    ok, why = turn.judge("", {})
    assert not ok
    assert "empty" in why


def test_the_raw_code_judge_rejects_fences_and_emptiness() -> None:
    assert _judge_raw_code("def f():\n    return 42", {})[0]
    assert not _judge_raw_code("```python\ndef f(): ...\n```", {})[0]
    assert not _judge_raw_code("   ", {})[0]


def test_the_clock_judge_catches_the_offset_dropped_answer() -> None:
    """The measured 01:23 defect: '5h from now in tokyo' answered with the CURRENT time.
    Pinned clock, so this cannot flake at any hour."""

    now = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc)  # Tokyo now = 21:00
    judge = judge_clock("Asia/Tokyo", offset_min=300, now=now)     # expected 02:00 next day

    ok, _ = judge("21:00", {})          # the defect: current time echoed back
    assert not ok
    ok, _ = judge("02:00", {})          # the correct offset answer, across midnight
    assert ok
    ok, _ = judge("02:02", {})          # within tolerance
    assert ok
    ok, why = judge("no clock here", {})
    assert not ok
    assert "HH:MM" in why


def test_advisory_turns_never_hard_fail_the_drive() -> None:
    """Weather and terse follow-ups depend on network and prose; they are recorded, not
    gating - a judge guessing prose is the canned-answer defect in reverse."""

    turns = [t for s in build_sessions(5, 20) for t in s if t.intent in {"weather", "followup"}]
    assert turns, "the generator stopped producing advisory turns"
    assert all(not t.hard for t in turns)


def test_every_generated_turn_carries_a_judge() -> None:
    for session in build_sessions(9, 10):
        for turn in session:
            assert isinstance(turn, Turn)
            ok, _ = turn.judge("plausible text 4242", {})
            assert isinstance(ok, bool)


def test_a_numeric_judge_reads_past_a_thousands_separator() -> None:
    """A correct answer must never be reported as a failure.

    Measured on the live drive: "91 times 69 is 6,279." was judged a hard failure because the
    judge did a bare substring check for "6279". A false failure costs the same attention as a
    missed defect and teaches the operator to distrust the driver.
    """

    judge = _judge_contains("6279")

    assert judge("91 times 69 is 6,279.", {})[0]
    assert judge("6 279", {})[0]
    assert judge("6279", {})[0]
    # Still a real judge: a genuinely wrong number fails.
    assert not judge("91 times 69 is 648.", {})[0]


def test_the_clock_judge_accepts_a_two_clause_answer_but_not_a_now_only_answer() -> None:
    """A correct shifted reply states BOTH clocks; reading only the first called it a failure.

    Measured on seeds 4242 and 90210: "Current time in Vilnius is 03:18 EEST; 5 hours ago it was
    22:18 EEST" is right, and was reported as wrong. The judge must read every clock in the reply
    while still rejecting a reply that states only the current time -- which is the actual defect.
    """

    now = dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc)   # Tokyo now = 21:00
    judge = judge_clock("Asia/Tokyo", offset_min=180, now=now)      # expected 00:00 next day

    assert judge("Current time in Tokyo is 21:00 JST; in 3 hours it will be 00:00 JST.", {})[0]
    assert judge("00:00", {})[0]
    # The defect itself: only the current clock, stated confidently.
    ok, why = judge("Current time in Tokyo is 21:00 JST.", {})
    assert not ok
    assert "21:00" in why
