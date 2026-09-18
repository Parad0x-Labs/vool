"""Independent ground-truth fixtures + oracle for multipart-prompt completeness.

Why this file exists
---------------------
An audit found `core.plain_task_routing.ordinary_plain_request_count` -- the count that
`core/ordinary_chat_response_guard.py:198` treats as the number of `required_parts` a reply must
cover -- silently returns a WRONG count on exactly the multipart shapes real users send: numbered
lists, dashed lists, lettered lists, and semicolon prose all measured `0` (see below), which makes
`ordinary_multi_part_answer_complete()` short-circuit `True` for every reply, including one that
answered a single part out of four. A planned repair hoists a per-turn obligation/enumeration
ledger to replace that count. Its top risk, per the audit: a hoisted 1-of-4 ledger could report
"1/1 satisfied" and still let three tasks vanish under a label reading COMPLETE.

This file is the fixture family + oracle that repair must be checked against. It is deliberately
independent of `core/` -- it imports nothing from `core`, `apps`, `adapters`, `storage`, `tools`,
or `ops`. Its ground-truth part counts and its part-presence oracle are authored by hand and
verified by construction (see the self-tests at the bottom), so a future repair that reports
"complete" cannot be trusted merely because it agrees with the routing function whose vacuity
started this. It must agree with THIS oracle instead.

Measured facts (recorded, not asserted -- verified 2026-08-17 against SHA 12927f3d with
`~/vool/.venv-c1-py312/bin/python`, calling `core.plain_task_routing.ordinary_plain_request_count`
directly on each fixture's `.prompt` below):

    shape             ordinary_plain_request_count()   true part_count (this file)
    ----------------  -------------------------------  ----------------------------
    numbered          0                                 4
    newline_numbered  0                                 4
    dashed            0                                 4
    lettered          0                                 4
    comma_prose       2                                 4
    semicolon_prose   0                                 4

Root causes observed while measuring (informational, not asserted by this file):
  * `ordinary_plain_requests()` returns `()` -- the WHOLE prompt, not just the bad clause -- the
    first time any extracted clause fails `_ORDINARY_REQUEST_HEAD_RE`. A bare list-marker fragment
    ("2.", "3.") is produced whenever `_split_unquoted_sentences()` treats the marker's own
    trailing "." as a sentence terminator before the marker can be glued to its content, which is
    what zeroes every list-shaped prompt above (numbered/newline_numbered/dashed/lettered).
  * `_ORDINARY_REQUEST_HEAD_RE` does not include "reverse" in its verb list, so a
    "Reverse the string TESLA." clause never matches on its own -- which is what additionally zeroes
    `semicolon_prose` (semicolons split it into its own failing clause) even though its Bitcoin
    clause is phrased to dodge the operational-request short-circuit that zeroes the others.
  * `comma_prose` measures `2` (not `4`) only because unpunctuated commas are never split points in
    that module; only an "and X" connector before a recognized verb splits, so two of the four
    tasks stay glued into one counted clause.

`ordinary_multi_part_answer_complete(prompt, "Bitcoin: 63422.0 USD")` measures `True` for every
`required_parts == 0` shape above (short-circuits before ever inspecting the reply) and `False` for
`comma_prose` (required_parts=2, and the reply carries no "1."/"2." markers at all) -- but even
`comma_prose`'s count is wrong: `ordinary_multi_part_answer_complete(comma_prose.prompt,
"1. Bitcoin: 63422 USD\\n2. 888")` measures `True` despite tasks 3 and 4 (reverse, date) being
completely absent from that reply, because required_parts=2 only ever demands markers 1 and 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------
# Ground-truth model
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MultipartTask:
    """One of the four independent tasks a fixture's prompt asks for.

    `index` is the task's 1-based ground-truth position (stable across every shape below, so a
    future repair's per-task ledger can be compared task-for-task across shapes, not just by
    count). `prompt_text` is the verbatim clause as it appears in the owning fixture's `.prompt`
    -- asserted by a self-test below, not merely claimed. `signature` is the oracle's own textual
    detector for whether a candidate REPLY addressed this task; it does not care whether the
    answer is numerically correct, only whether the reply shows an attempt at this task's content
    -- that mirrors what the guard's completeness check is actually supposed to certify (coverage,
    not correctness).
    """

    index: int
    label: str
    prompt_text: str
    signature: re.Pattern[str]


@dataclass(frozen=True)
class MultipartPromptFixture:
    """A multipart prompt in one real-world shape, plus its independently declared ground truth."""

    shape: str
    prompt: str
    tasks: tuple[MultipartTask, ...]
    part_count: int  # declared independently of len(tasks); a self-test proves they agree


# ---------------------------------------------------------------------------------------------
# Shared per-task oracle signatures
# ---------------------------------------------------------------------------------------------
#
# Every fixture below asks the SAME four underlying tasks (a live-price lookup, a multiplication,
# a string reversal, a date offset), just phrased for its shape. The signatures are therefore
# shared: one hand-authored, purely textual regex per task, independent of any shape, numbering
# scheme, or product code. A signature matches when the reply shows an attempt at that task's
# content -- topic + a value for the price lookup, the arithmetic result for the multiplication,
# the reversed literal for the string task, a date-shaped token for the date task.

_BITCOIN_PRICE_SIGNATURE = re.compile(
    r"\bbitcoin\b.{0,80}?\$?\d[\d,]*(?:\.\d+)?|\$?\d[\d,]*(?:\.\d+)?.{0,80}?\bbitcoin\b",
    re.IGNORECASE | re.DOTALL,
)
_MULTIPLY_37_24_SIGNATURE = re.compile(
    r"\b888\b|37\s*(?:x|\*|×)\s*24\s*=?\s*888",
    re.IGNORECASE,
)
_REVERSE_TESLA_SIGNATURE = re.compile(r"\balset\b", re.IGNORECASE)
_DATE_PLUS_30_DAYS_SIGNATURE = re.compile(
    r"\b20\d{2}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}/(?:20)?\d{2}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*(?:20\d{2})?\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:20\d{2})?\b",
    re.IGNORECASE,
)


def _tasks(*, texts: tuple[str, str, str, str]) -> tuple[MultipartTask, ...]:
    """Build the four ground-truth tasks for one shape from its shape-specific clause text."""

    labels = ("bitcoin_price", "multiply_37x24", "reverse_tesla", "date_plus_30_days")
    signatures = (
        _BITCOIN_PRICE_SIGNATURE,
        _MULTIPLY_37_24_SIGNATURE,
        _REVERSE_TESLA_SIGNATURE,
        _DATE_PLUS_30_DAYS_SIGNATURE,
    )
    return tuple(
        MultipartTask(index=i + 1, label=labels[i], prompt_text=texts[i], signature=signatures[i])
        for i in range(4)
    )


# ---------------------------------------------------------------------------------------------
# Fixtures: every real-use shape, same four tasks, ground truth declared independently
# ---------------------------------------------------------------------------------------------

_PREAMBLE = "Answer ALL four tasks in order:"
_TRAILER_NUMBERED = "Output exactly four numbered lines, 1 through 4."
_TRAILER_LETTERED = "Output exactly four labeled lines, A through D."

# Shape 1: inline numbered -- this is prompt P1 from the audit, verbatim.
NUMBERED = MultipartPromptFixture(
    shape="numbered",
    prompt=(
        f"{_PREAMBLE} 1. Look up the current live Bitcoin price in USD. "
        "2. Calculate 37 x 24. 3. Reverse the string TESLA. "
        "4. Using your system date, calculate the date exactly 30 days from today. "
        f"{_TRAILER_NUMBERED}"
    ),
    tasks=_tasks(
        texts=(
            "Look up the current live Bitcoin price in USD.",
            "Calculate 37 x 24.",
            "Reverse the string TESLA.",
            "Using your system date, calculate the date exactly 30 days from today.",
        )
    ),
    part_count=4,
)

# Shape 2: same numbering, but one task per physical line (distinct from shape 1's single
# paragraph -- the routing module's sentence splitter treats "\n" as a delimiter too, so this is a
# genuinely different code path, not cosmetic).
NEWLINE_NUMBERED = MultipartPromptFixture(
    shape="newline_numbered",
    prompt=(
        f"{_PREAMBLE}\n1. Look up the current live Bitcoin price in USD.\n"
        "2. Calculate 37 x 24.\n3. Reverse the string TESLA.\n"
        "4. Using your system date, calculate the date exactly 30 days from today.\n"
        f"{_TRAILER_NUMBERED}"
    ),
    tasks=_tasks(
        texts=(
            "Look up the current live Bitcoin price in USD.",
            "Calculate 37 x 24.",
            "Reverse the string TESLA.",
            "Using your system date, calculate the date exactly 30 days from today.",
        )
    ),
    part_count=4,
)

# Shape 3: dashed bullets.
DASHED = MultipartPromptFixture(
    shape="dashed",
    prompt=(
        f"{_PREAMBLE}\n- Look up the current live Bitcoin price in USD.\n"
        "- Calculate 37 x 24.\n- Reverse the string TESLA.\n"
        "- Using your system date, calculate the date exactly 30 days from today.\n"
        f"{_TRAILER_NUMBERED}"
    ),
    tasks=_tasks(
        texts=(
            "Look up the current live Bitcoin price in USD.",
            "Calculate 37 x 24.",
            "Reverse the string TESLA.",
            "Using your system date, calculate the date exactly 30 days from today.",
        )
    ),
    part_count=4,
)

# Shape 4: lettered bullets (A./B./C./D.).
LETTERED = MultipartPromptFixture(
    shape="lettered",
    prompt=(
        f"{_PREAMBLE}\nA. Look up the current live Bitcoin price in USD.\n"
        "B. Calculate 37 x 24.\nC. Reverse the string TESLA.\n"
        "D. Using your system date, calculate the date exactly 30 days from today.\n"
        f"{_TRAILER_LETTERED}"
    ),
    tasks=_tasks(
        texts=(
            "Look up the current live Bitcoin price in USD.",
            "Calculate 37 x 24.",
            "Reverse the string TESLA.",
            "Using your system date, calculate the date exactly 30 days from today.",
        )
    ),
    part_count=4,
)

# Shape 5: comma-separated prose (the phrasing of task 1 dodges the "look up ... current/price"
# operational-request short-circuit that zeros every shape above uniformly -- this is what surfaces
# the SEPARATE undercount-not-zero failure mode: required_parts measures 2, not 4).
COMMA_PROSE = MultipartPromptFixture(
    shape="comma_prose",
    prompt=(
        "What is the current Bitcoin price in USD, calculate 37 x 24, "
        "reverse the string TESLA, and what is the date exactly 30 days from today?"
    ),
    tasks=_tasks(
        texts=(
            "What is the current Bitcoin price in USD",
            "calculate 37 x 24",
            "reverse the string TESLA",
            "what is the date exactly 30 days from today",
        )
    ),
    part_count=4,
)

# Shape 6: semicolon-separated prose.
SEMICOLON_PROSE = MultipartPromptFixture(
    shape="semicolon_prose",
    prompt=(
        "What is the current Bitcoin price in USD; calculate 37 x 24; "
        "reverse the string TESLA; using your system date, calculate the date exactly 30 days "
        "from today."
    ),
    tasks=_tasks(
        texts=(
            "What is the current Bitcoin price in USD",
            "calculate 37 x 24",
            "reverse the string TESLA",
            "using your system date, calculate the date exactly 30 days from today",
        )
    ),
    part_count=4,
)

ALL_FIXTURES: tuple[MultipartPromptFixture, ...] = (
    NUMBERED,
    NEWLINE_NUMBERED,
    DASHED,
    LETTERED,
    COMMA_PROSE,
    SEMICOLON_PROSE,
)

# The measured real reply from the audit: it answers only the Bitcoin-price task (part 1 of 4).
MEASURED_REAL_REPLY_1_OF_4 = "Bitcoin: 63422.0 USD"

# A hand-written reply that genuinely covers all four tasks, used to prove the oracle can reach
# 4/4 and is not vacuous in the other direction (a signature that never fires).
HAND_WRITTEN_COMPLETE_REPLY = (
    "1. Bitcoin price: $63,422.00\n"
    "2. 37 x 24 = 888\n"
    "3. TESLA reversed is ALSET\n"
    "4. 30 days from today is 2026-09-16\n"
)


# ---------------------------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------------------------


def present_task_indices(fixture: MultipartPromptFixture, reply_text: str) -> frozenset[int]:
    """Which of `fixture`'s declared parts does `reply_text` visibly address?

    Purely textual: each task's `signature` regex is checked against the raw reply text. No
    product code (`core`, `apps`, `adapters`, `storage`, `tools`, `ops`) is imported or called
    anywhere in this module. This is the independent oracle a future obligation/enumeration repair
    must agree with -- not the routing module whose count started this investigation.
    """

    text = str(reply_text or "")
    return frozenset(task.index for task in fixture.tasks if task.signature.search(text))


def missing_task_indices(fixture: MultipartPromptFixture, reply_text: str) -> frozenset[int]:
    all_indices = frozenset(task.index for task in fixture.tasks)
    return all_indices - present_task_indices(fixture, reply_text)


def oracle_report(fixture: MultipartPromptFixture, reply_text: str) -> dict[str, object]:
    """A small, inspectable verdict a repair's tests can assert against directly."""

    present = present_task_indices(fixture, reply_text)
    all_indices = frozenset(task.index for task in fixture.tasks)
    return {
        "shape": fixture.shape,
        "required_count": fixture.part_count,
        "present_indices": present,
        "missing_indices": all_indices - present,
        "present_count": len(present),
        "complete": present == all_indices,
    }


# ---------------------------------------------------------------------------------------------
# Self-tests: prove the fixtures and the oracle are correct, not the routing module.
#
# Per the brief, this file must NOT assert anything about `ordinary_plain_request_count` being
# wrong -- that belongs to the routing module's own (currently absent) coverage, and asserting it
# here would make this a red test the moment the count is fixed. Everything below only checks
# internal consistency of this file's own ground truth and its own oracle.
# ---------------------------------------------------------------------------------------------


def test_every_fixture_declares_four_ground_truth_tasks() -> None:
    for fixture in ALL_FIXTURES:
        assert fixture.part_count == 4, fixture.shape
        assert len(fixture.tasks) == 4, fixture.shape


def test_declared_part_count_matches_the_task_list_by_construction() -> None:
    """The count field and the task list are authored independently; prove they never drift."""

    for fixture in ALL_FIXTURES:
        assert fixture.part_count == len(fixture.tasks), (
            f"{fixture.shape}: declared part_count={fixture.part_count} but "
            f"tasks list has {len(fixture.tasks)} entries"
        )


def test_task_indexes_are_sequential_one_based() -> None:
    for fixture in ALL_FIXTURES:
        assert tuple(task.index for task in fixture.tasks) == (1, 2, 3, 4), fixture.shape


def test_task_labels_are_stable_and_unique_across_every_shape() -> None:
    """A future repair keying a per-task ledger by label needs the same four labels everywhere."""

    expected_labels = ("bitcoin_price", "multiply_37x24", "reverse_tesla", "date_plus_30_days")
    for fixture in ALL_FIXTURES:
        assert tuple(task.label for task in fixture.tasks) == expected_labels, fixture.shape


def test_each_task_prompt_text_is_a_verbatim_substring_of_its_prompt() -> None:
    """The ground-truth clause is asserted to actually be in the prompt it claims to summarize."""

    for fixture in ALL_FIXTURES:
        for task in fixture.tasks:
            assert task.prompt_text in fixture.prompt, (
                f"{fixture.shape} task {task.index} ({task.label}): "
                f"{task.prompt_text!r} not found verbatim in prompt"
            )


def test_fixture_prompts_are_all_distinct() -> None:
    prompts = [fixture.prompt for fixture in ALL_FIXTURES]
    assert len(prompts) == len(set(prompts))


def test_oracle_finds_four_of_four_on_a_hand_written_complete_reply() -> None:
    for fixture in ALL_FIXTURES:
        present = present_task_indices(fixture, HAND_WRITTEN_COMPLETE_REPLY)
        assert present == frozenset({1, 2, 3, 4}), (
            f"{fixture.shape}: expected 4/4 on the complete reply, oracle found {sorted(present)}"
        )
        report = oracle_report(fixture, HAND_WRITTEN_COMPLETE_REPLY)
        assert report["complete"] is True
        assert report["present_count"] == 4


def test_oracle_finds_one_of_four_on_the_measured_real_reply() -> None:
    """The audit's actual production reply: it answers the Bitcoin-price task only."""

    for fixture in ALL_FIXTURES:
        present = present_task_indices(fixture, MEASURED_REAL_REPLY_1_OF_4)
        assert present == frozenset({1}), (
            f"{fixture.shape}: expected exactly part 1/4 on {MEASURED_REAL_REPLY_1_OF_4!r}, "
            f"oracle found {sorted(present)}"
        )
        report = oracle_report(fixture, MEASURED_REAL_REPLY_1_OF_4)
        assert report["complete"] is False
        assert report["present_count"] == 1
        assert report["missing_indices"] == frozenset({2, 3, 4})


def test_oracle_finds_nothing_on_an_empty_or_unrelated_reply() -> None:
    """Adversarial case (mandate 0.4): an oracle that always finds something is as useless as one
    that never does. Confirm a reply with none of the four signatures reports 0/4, and that an
    empty reply does not crash the signature scan.
    """

    unrelated_reply = "The weather in Paris is mild this week with a light breeze."
    for fixture in ALL_FIXTURES:
        assert present_task_indices(fixture, unrelated_reply) == frozenset()
        assert present_task_indices(fixture, "") == frozenset()
        assert present_task_indices(fixture, None) == frozenset()  # type: ignore[arg-type]


def test_oracle_does_not_cross_contaminate_between_tasks() -> None:
    """A reply that only ever answers task 2 must not accidentally satisfy any other task."""

    only_multiply = "The answer to your math question is 888."
    for fixture in ALL_FIXTURES:
        present = present_task_indices(fixture, only_multiply)
        assert present == frozenset({2}), (
            f"{fixture.shape}: expected only part 2/4 on {only_multiply!r}, found {sorted(present)}"
        )


def test_missing_task_indices_is_the_complement_of_present() -> None:
    for fixture in ALL_FIXTURES:
        present = present_task_indices(fixture, MEASURED_REAL_REPLY_1_OF_4)
        missing = missing_task_indices(fixture, MEASURED_REAL_REPLY_1_OF_4)
        all_indices = frozenset(task.index for task in fixture.tasks)
        assert present | missing == all_indices
        assert present & missing == frozenset()


def test_shapes_cover_every_named_real_use_family() -> None:
    """Guards against silently dropping a shape from the family in a future edit."""

    shapes = {fixture.shape for fixture in ALL_FIXTURES}
    assert shapes == {
        "numbered",
        "newline_numbered",
        "dashed",
        "lettered",
        "comma_prose",
        "semicolon_prose",
    }
