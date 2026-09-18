"""A planned sub-task's failure detail must reach the reader as prose, never as internals.

Reproduces the live acceptance defect (2026-09-08, built app at 1206993d, turn "If I exchange
100 EUR to USD, roughly how much USD do I get?"): the deterministic currency figure published
correctly, the sibling narration sub-task died on a dead model lane, and `merge_outcomes`
rendered that sub-task's `error` -- minted by `_run_single` as ``f"{type(exc).__name__}: {exc}"``
-- verbatim into the merged answer:

    I could not answer this part of your message:
    - roughly how much USD do I get? (RuntimeError: qwen2.5:7b failed before producing a
    usable answer; fallback nvidia/nemotron-3.5-lightning:free failed before producing...)

The publication gate then (correctly) withheld those lines as unsupported statements and the
served notice listed them, so the exception-class token "RuntimeError:" reached the user twice.

The owning seam is composition: `merge_outcomes` and `_degraded_unit_synthesis` are where
failure text becomes answer text, so they are where internal detail must be taken back out.
The runtime's existing authority for that is `core.conductor.compose.reader_facing_reason`
(fail-closed: unrecognized shapes that still look like internals render a generic line, never
pass through). This file pins that contract at both composition seams.
"""

from __future__ import annotations

from core.agent_runtime.agent import _degraded_unit_synthesis
from core.agent_runtime.turn_planner import PlannedTask, TaskOutcome, merge_outcomes

#: The exact error shape `_run_single` minted for the live turn: exception-class prefix plus
#: the degraded-response note (which is itself reader-facing by design).
LIVE_ERROR = (
    "RuntimeError: `qwen2.5:7b` failed before producing a usable answer; fallback "
    "`nvidia/nemotron-3.5-lightning:free` failed before producing a usable answer. "
    "No cached or remembered text was substituted. Retry the turn."
)


def test_merge_outcomes_does_not_put_exception_classes_into_the_answer() -> None:
    answered = TaskOutcome(
        task=PlannedTask(index=0, request="exchange 100 EUR to USD"),
        answer="100 EUR × 1.1625 = 116.25 USD",
    )
    failed = TaskOutcome(
        task=PlannedTask(index=1, request="roughly how much USD do I get?"),
        error=LIVE_ERROR,
    )
    merged = merge_outcomes([answered, failed])
    assert "116.25" in merged
    assert "I could not answer this part of your message" in merged
    assert "roughly how much USD do I get?" in merged
    # The defect: the raw class token rode into the served answer.
    assert "RuntimeError" not in merged
    assert "(RuntimeError" not in merged


def test_merge_outcomes_keeps_reader_facing_failure_prose() -> None:
    """The note under the class prefix is written for the reader; scrubbing must keep it.

    A blanket delete would hide WHAT failed (which model, why) -- the reader-facing sentence
    survives with the internals prefix removed, so the user still learns the model lanes died.
    """
    failed = TaskOutcome(
        task=PlannedTask(index=0, request="roughly how much USD do I get?"),
        error=LIVE_ERROR,
    )
    merged = merge_outcomes([failed])
    assert "failed before producing a usable answer" in merged
    assert "qwen2.5:7b" in merged


def test_merge_outcomes_renders_unrecognized_internal_shapes_generic() -> None:
    """An error this module has never seen must fail closed, not pass through.

    A filesystem path or a bare exception shape is runtime internals by construction; the
    reader gets the request plus a generic completion phrase, never the internals.
    """
    failed = TaskOutcome(
        task=PlannedTask(index=0, request="summarize the log"),
        error="OSError: [Errno 28] No space left on device: /var/folders/xyz/vool_web0_v2.db",
    )
    merged = merge_outcomes([failed])
    assert "summarize the log" in merged
    assert "OSError" not in merged
    assert "/var/folders" not in merged
    assert "could not be completed" in merged


def test_degraded_unit_synthesis_scrubs_the_same_shapes() -> None:
    """The last-resort composer answers to the same law, not a weaker one.

    `_degraded_unit_synthesis` exists for the merge fault path; leaving it raw would put the
    leak right back the moment the primary composer fails.
    """

    class _Task:  # minimal duck-type for the degraded path
        request = "roughly how much USD do I get?"

    class _Outcome:
        task = _Task()
        ok = False
        error = LIVE_ERROR
        answer = ""

    class _Answered:
        task = _Task()
        ok = True
        error = ""
        answer = "100 EUR × 1.1625 = 116.25 USD"

    merged = _degraded_unit_synthesis([_Answered(), _Outcome()])
    assert "116.25" in merged
    assert "RuntimeError" not in merged
    assert "roughly how much USD do I get?" in merged
