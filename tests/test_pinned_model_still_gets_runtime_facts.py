"""Selecting a model changes who reasons. It does not disable VOOL.

Measured on the installed app with a cloud model pinned in the composer:

    turn                total    model    local work before the model
    "hi"                68.2s     1.9s    65.9s
    "wat tiem is now?"  72.3s     4.5s    67.4s

Sixty-six seconds of history reconstruction, summarization, embedding and model-load contention to
answer "hi" — a turn whose answer comes from a curated phrase table and needs no model at all. The
clock question went the same way and was classified `research` en route.

The cause was ordering. `explicit_model_owns_semantic_turn` returns early for a pinned model, and
the greeting path sat 335 lines BELOW that gate, so a pinned turn could never reach it.

The principle it was defending is still right — a fast path must not answer instead of the model the
operator selected — but it does not apply to these two. A greeting has no reasoning content, and the
clock is a fact the runtime holds and no model does. Everything genuinely semantic stays below the
gate and still belongs to the selected model.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.agent_runtime.turn_frontdoor import explicit_model_owns_semantic_turn

FRONTDOOR = Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
# The gate was `if explicit_model_owns_semantic_turn(source_context): return {"result": None}`.
# That early return is gone — it meant "no fast path claimed this turn", which routed a pinned turn
# into the LOCAL builder, the opposite of what the gate existed to do. It is now a flag the
# judgement handlers consult, so "above the gate" is measured from where the flag is computed.
# See tests/test_pinned_model_does_not_hand_over_the_runtime.py.
_GATE = "model_owns_judgement = explicit_model_owns_semantic_turn(source_context)"
PINNED = {
    "requested_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "surface": "api",
    "workspace": "/tmp/project",
    "project_id": "p",
}


def _source() -> str:
    return FRONTDOOR.read_text(encoding="utf-8")


def test_a_pinned_model_still_trips_the_ownership_gate() -> None:
    """The gate itself is unchanged — only what runs before it."""

    assert explicit_model_owns_semantic_turn(PINNED) is True
    assert explicit_model_owns_semantic_turn({"requested_model": "vool"}) is False
    assert explicit_model_owns_semantic_turn({}) is False


def test_a_greeting_is_answered_before_the_gate() -> None:
    source = _source()
    gate = source.index(_GATE)
    greeting = source.index("smalltalk = agent._smalltalk_fast_path")

    assert greeting < gate, (
        "a greeting must not wait on the model pipeline; it cost 68.2s when it did"
    )


def test_the_workspace_fact_is_answered_before_the_gate() -> None:
    source = _source()
    gate = source.index(_GATE)
    identity = source.index("workspace_identity_first")

    assert identity < gate


def test_the_greeting_path_was_moved_not_duplicated() -> None:
    """Two copies would answer twice, or diverge silently."""

    assert _source().count("smalltalk = agent._smalltalk_fast_path") == 1


def test_capability_truth_is_a_runtime_fact_and_runs_before_the_greeting() -> None:
    """Corrected after the first attempt broke three contract tests.

    "What can you do on this machine right now?" is a fact about the runtime, not a judgement about
    content, so it belongs above the gate with the clock and the workspace. It must also run BEFORE
    the greeting path, whose help branch claims "what can you do" as well — lifting smalltalk alone
    preempted the grounded capability answer and three contract tests caught it immediately.
    """

    source = _source()
    gate = source.index(_GATE)
    capability = source.index("agent._maybe_handle_capability_truth_request(")
    greeting = source.index("smalltalk = agent._smalltalk_fast_path")

    assert capability < greeting < gate


def test_judgement_handlers_still_sit_below_the_gate() -> None:
    """The gate's purpose survives: a fast path must not answer FOR the selected model.

    A folder overview is an interpretation of what a project IS — that is reasoning, and it belongs
    to whichever model the operator picked. The distinction is fact versus judgement, not
    fast versus slow.
    """

    source = _source()
    gate = source.index(_GATE)
    assert source.index("agent._maybe_handle_folder_overview_request(") > gate


def test_the_move_is_explained_where_it_was_made() -> None:
    """A reordering with no stated reason is one the next person undoes."""

    source = _source()
    gate = source.index(_GATE)
    # A character budget re-tightens every time a lane above the gate grows a comment, and each
    # widening reads as though the measurement moved when it did not. What the test is actually
    # about is that the measurement sits inline ABOVE the gate, in the same function, rather than
    # in a commit message nobody will read -- so that is what is asserted.
    #
    # Previous fixed windows: 7000, then 9000 (the gate's early-return note), now unbounded.
    window = source[:gate]
    assert "68.2s" in window, "the measurement that motivated the move must be recorded inline"
    assert re.search(r"changes who reasons", window)


def test_the_clock_itself_runs_before_the_gate_not_merely_the_clock_fact() -> None:
    """The correction that mattered most.

    A previous commit moved the greeting, capability truth and workspace identity above the gate and
    its comment spoke of "the clock" — but `_date_time_fast_path` stayed at line 586, below it. The
    authoritative clock in the system prompt made the ANSWER correct while the ROUTE was unchanged,
    so a pinned cloud model still paid a full provider round trip: 2.4-2.9s for a simple clock
    question and 83.7s for a composite one.

    A comment describing work that was not done is the failure this whole effort exists to remove.
    """

    source = _source()
    gate = source.index(_GATE)
    clock = source.index("date_time_status = agent._date_time_fast_path")

    assert clock < gate, "the clock HANDLER must be above the gate, not just a comment about it"
    # Two calls now: the direct question, and the retry that resolves a follow-up fragment such as
    # "and date?". Both must be above the gate, and there must still be exactly one place that
    # returns the result, or a follow-up could answer twice.
    assert source.count("date_time_status = agent._date_time_fast_path") == 2
    assert all(
        pos < gate
        for pos in _all_positions(source, "date_time_status = agent._date_time_fast_path")
    )
    assert source.count('reason="date_time_fast_path",') == 1


def _all_positions(text: str, needle: str) -> list[int]:
    out, start = [], 0
    while (found := text.find(needle, start)) != -1:
        out.append(found)
        start = found + 1
    return out


def test_a_clock_follow_up_fragment_is_resolved_locally() -> None:
    """Measured on the installed build: "what time is now?" answered in ~23ms, then "and date?" was
    billed `cloud | nemotron | 2,078 tok` — a provider round trip for a fact just supplied locally.

    The follow-up needs BOTH signals. The wording alone is not enough ("date" appears in ordinary
    sentences) and the prior turn alone is not enough (the next message is usually about something
    else). Requiring both is what keeps "and what did Caesar do on that date?" with the model.
    """

    from core.agent_runtime.fast_paths_utility import is_runtime_fact_followup

    for fragment in ("and date?", "and time?", "date?", "what about the date", "ok and date?",
                     "then also time?", "and the timezone", "and day?"):
        assert is_runtime_fact_followup(fragment, recent_utility_kind="time"), fragment

    for fragment in ("and date?", "date?", "and the timezone"):
        assert not is_runtime_fact_followup(fragment, recent_utility_kind=""), (
            f"{fragment!r} must not be local when the previous turn was not a clock turn"
        )

    for sentence in (
        "and what did Caesar do on that date?",
        "and how do I format a date in python?",
        "tell me about the release date of that library and why it slipped",
    ):
        assert not is_runtime_fact_followup(sentence, recent_utility_kind="time"), sentence


def test_the_follow_up_is_resolved_before_the_gate() -> None:
    source = _source()
    assert source.index("is_runtime_fact_followup") < source.index(_GATE)
