"""A topic word must not claim a whole turn — the clock and the disk, measured on the daemon.

Both cases below were found by a blind QA pass against the running daemon at 12128c2 and each was
reproduced here at 0.2s before the fix, with no model call:

    "Our team keeps a registry of every vendor contract we've signed. What columns should that
     registry have so it stays useful over time?"          -> "Current time is 08:02 EEST."   (4/4)

    "What's a sensible file naming convention for a shared drive that has many versions of the
     same document?"  -> "Drive space: / 62.1 GB free of 460.4 GB (86.5% used)..."            (2/2)

The two have DIFFERENT root causes, and the second one is not the word "drive":

* the clock's broad heuristic asked only for a bare `\\btime\\b` plus "what" somewhere in the
  message, so the duration adverbial in "so it stays useful over time" was read as the clock.
  Swapping "registry" for "tracker" still misfired, which is what proves the noun was innocent;

* the disk case is TWO defects in series. `core/input_normalizer.py` rewrote the ordinary English
  word "shared" -> "shard" (a mesh-vocabulary word `difflib` scores at 0.909), and
  `_DISK_STRONG_PHRASES` then matched "hard drive" as a BARE SUBSTRING of "a s|hard drive|". Either
  one alone is harmless; together they answered a filing-convention question with this machine's
  free space. Both halves are fixed and both are pinned here separately, because a test that only
  covered the pair would pass again the moment either half came back on its own.

The rule in every case is what the sentence ASKS FOR. A clock answer is right for "what time is
it" and wrong for a sentence that happens to contain "over time"; disk space is right for "how much
space is left on my drive" and wrong for a sentence about naming files on a shared drive.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.fast_paths_utility import date_time_fast_path
from core.agent_runtime.intent_claims import (
    FAMILY_DISK_USAGE,
    FAMILY_MACHINE_SPECS,
    near_miss,
    probe_claims,
)
from core.execution.constants import machine_diagnostics_intent
from core.input_normalizer import normalize_user_text

# The two verbatim sentences the tester sent, character for character.
CLOCK_OVERCLAIM = (
    "Our team keeps a registry of every vendor contract we've signed. What columns should that "
    "registry have so it stays useful over time?"
)
DISK_OVERCLAIM = (
    "What's a sensible file naming convention for a shared drive that has many versions of the "
    "same document?"
)
# The two controls the tester isolated: same sentence, one element changed, answered correctly.
CLOCK_CONTROL = (
    "Our team keeps a registry of every vendor contract we've signed. What columns should that "
    "registry have?"
)
DISK_CONTROL = (
    "What's a sensible file naming convention for a shared folder that has many versions of the "
    "same document?"
)


def _clock(text: str) -> str | None:
    return date_time_fast_path(None, text, source_surface="openclaw")


# ---------------------------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------------------------
def test_the_reported_clock_overclaim_is_not_a_clock_question() -> None:
    assert _clock(CLOCK_OVERCLAIM) is None


def test_the_testers_control_still_answers_normally() -> None:
    # Removing the trailing clause answered correctly in 11.8s before the fix; it must keep doing so.
    assert _clock(CLOCK_CONTROL) is None


def test_the_trigger_was_over_time_and_not_the_noun() -> None:
    # The tester swapped the noun and the misfire survived, which is how "over time" was isolated.
    assert _clock(CLOCK_OVERCLAIM.replace("registry", "tracker")) is None


@pytest.mark.parametrize(
    "phrase",
    [
        # A blocklist of these would never be finishable; the fix is a positive ASK rule instead.
        "what can we do to cut the time it takes to onboard a new hire",
        "what is our time to market for this feature",
        "what happens every time we deploy on a friday",
        "what is the response time of the api right now",
        "at the same time, what should we do about the backlog",
        "how do we reduce the time spent in meetings",
        "what did the team ship in record time",
    ],
)
def test_time_as_a_topic_never_answers_with_the_clock(phrase: str) -> None:
    assert _clock(phrase) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "what time is it",
        "what's the date",
        "what time is it right now",
        "whats the time",
        "what is the current time",
        "time now",
        "what day is it today",
        "what's today's date",
    ],
)
def test_a_real_clock_or_date_question_still_answers(phrase: str) -> None:
    answer = _clock(phrase)
    assert answer, f"the runtime owns this fact and must still answer it: {phrase!r}"
    assert "Current time is" in answer or "Today is" in answer


def test_a_named_timezone_still_reaches_the_clock() -> None:
    # Vilnius, not Tokyo: _UTILITY_TIMEZONE_ALIASES only carries Vilnius today, so Tokyo would
    # have asserted nothing about the timezone branch (unmodified main answers it with LOCAL time).
    answer = _clock("what time is it in vilnius")
    assert answer and "Vilnius" in answer


def test_the_timezone_branch_needs_the_clock_sense_too() -> None:
    # A named timezone plus a topical "time" is still not a clock question.
    assert _clock("how did our vilnius team's delivery time trend over the quarter") is None


# ---------------------------------------------------------------------------------------------
# The disk — half one: the normalizer must not corrupt the user's word
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "phrase",
    [
        DISK_OVERCLAIM,
        DISK_CONTROL,
        "put it on the shared drive",
        "we keep it in a shared folder",
        "this is a shared document",
    ],
)
def test_shared_is_never_rewritten_to_shard(phrase: str) -> None:
    result = normalize_user_text(phrase)
    assert "shared" not in result.replacements, result.replacements
    assert "shard" not in result.normalized_text.lower()


def test_the_typo_corrector_still_corrects_a_real_typo() -> None:
    # Protecting an English word must not turn the corrector off.
    assert normalize_user_text("what is my machine scpecs?").replacements == {"scpecs": "specs"}


# ---------------------------------------------------------------------------------------------
# The disk — half two: the phrase table matches words, not substrings of words
# ---------------------------------------------------------------------------------------------
def test_the_reported_disk_overclaim_is_not_a_disk_question() -> None:
    assert machine_diagnostics_intent(DISK_OVERCLAIM) is None
    assert machine_diagnostics_intent(normalize_user_text(DISK_OVERCLAIM).normalized_text) is None


def test_the_testers_shared_folder_control_still_answers_normally() -> None:
    assert machine_diagnostics_intent(DISK_CONTROL) is None
    assert machine_diagnostics_intent(normalize_user_text(DISK_CONTROL).normalized_text) is None


@pytest.mark.parametrize(
    "phrase",
    [
        # The corrupted form, pinned on its own: even if "shared" -> "shard" ever comes back, a
        # phrase table that matches inside other words is a defect in its own right.
        "what is a sensible file naming convention for a shard drive",
        "the workspace left over from the last run",  # "work|space left|"
        "we sharded the database across a drive pool",
    ],
)
def test_a_disk_phrase_inside_another_word_is_not_a_disk_question(phrase: str) -> None:
    assert machine_diagnostics_intent(phrase) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "how much disk space do i have left",
        "how much space is left on my drive",
        "my hard drive is nearly full",
        "how many drives do i have",
        "what's my disk usage",
        "how much free space on my disk?",
    ],
)
def test_a_real_disk_question_still_routes_to_the_disk_tool(phrase: str) -> None:
    assert machine_diagnostics_intent(phrase) == "machine.disk_usage", phrase


# ---------------------------------------------------------------------------------------------
# The disk — half three: the arbiter's rescue lane cannot conjure a drive report
# ---------------------------------------------------------------------------------------------
def _gate(text: str, gate: str = ""):
    """One of the front door's TWO arbitration call sites.

    They are separate because the two signals want opposite positions: competing readings must be
    settled BEFORE the deterministic lanes (one of them would otherwise claim a turn that is not
    theirs), and a near-miss only AFTER they have all declined -- those are answered by the
    workspace, identity and overview lanes for free, and arbitrating first bought a model call to
    rediscover an operation the runtime already owned. A near-miss case must therefore name the
    near-miss gate, or it is asking the ambiguity call site a question that is not its to answer.
    """
    from core.agent_runtime.turn_frontdoor import (
        _ARBITRATE_ON_AMBIGUITY,
        _maybe_arbitrate_intent,
    )

    return _maybe_arbitrate_intent(
        mock.Mock(),
        effective_input=text,
        session_id="openclaw:testtesttesttest0000",
        source_surface="chat",
        source_context={},
        gate=gate or _ARBITRATE_ON_AMBIGUITY,
    )


@pytest.fixture(autouse=True)
def _arbiter_env(tmp_path, monkeypatch):
    from core import runtime_paths

    runtime_paths.configure_runtime_home(tmp_path / "home")
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    yield
    runtime_paths.configure_runtime_home(None)


def test_the_advice_question_stays_out_of_the_arbiter() -> None:
    # This is an ordinary naming-convention question.  Naming a shared drive does not authorize
    # an arbiter/model tool choice or a machine read.
    claims = probe_claims(DISK_OVERCLAIM)
    assert claims == []
    assert near_miss(DISK_OVERCLAIM, claims) is False


@pytest.mark.parametrize("family", [FAMILY_DISK_USAGE, FAMILY_MACHINE_SPECS])
def test_a_near_miss_pick_of_a_zero_argument_machine_read_is_declined(family: str) -> None:
    # Asserted on the CALL, not via a raising side_effect: the gate wraps everything in
    # `except Exception: return None`, so a sabotage that let the tool run would have swallowed the
    # AssertionError and returned None anyway — the test would have passed while the bug was live.
    from core.intent_arbiter import ArbiterDecision

    execution = mock.Mock(ok=True, details={}, response_text="Drive space: ...")
    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision(family)),
        mock.patch(
            "core.runtime_execution_tools.execute_runtime_tool", return_value=execution
        ) as run_tool,
        mock.patch(
            "core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result",
            return_value={"ok": "machine read ran"},
        ),
    ):
        result = _gate(DISK_OVERCLAIM)
    run_tool.assert_not_called()
    assert result is None


def test_a_near_miss_pick_that_carries_an_argument_still_dispatches() -> None:
    # The rescue lane keeps working for the case it was built for — a typo'd folder request.
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_NEAR_MISS
    from core.intent_arbiter import ArbiterDecision

    execution = mock.Mock(ok=True, details={}, response_text="Found it")
    with (
        mock.patch(
            "core.intent_arbiter.arbitrate",
            return_value=ArbiterDecision("find_folder", "oken hunter"),
        ),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=execution),
        mock.patch(
            "core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result",
            return_value={"ok": 1},
        ),
    ):
        result = _gate(
            "Ok. please fint the oken hunter folder on desktop and we will analyse it",
            gate=_ARBITRATE_ON_NEAR_MISS,
        )
    assert result == {"ok": 1}


def test_an_ambiguous_message_keeps_the_arbiters_authority() -> None:
    # Two families claimed, so the arbiter is choosing between REAL readings — including a
    # zero-argument one. The near-miss rule must not reach this case.
    from core.intent_arbiter import ArbiterDecision

    ambiguous = "right, can you check Token hunter folder on this machine and run audit"
    assert len({c.family for c in probe_claims(ambiguous)}) >= 2
    execution = mock.Mock(ok=True, details={}, response_text="specs")
    with (
        mock.patch(
            "core.intent_arbiter.arbitrate",
            return_value=ArbiterDecision(FAMILY_MACHINE_SPECS),
        ),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=execution),
        mock.patch(
            "core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result",
            return_value={"ok": 2},
        ),
    ):
        assert _gate(ambiguous) == {"ok": 2}
