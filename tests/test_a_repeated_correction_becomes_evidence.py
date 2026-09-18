"""Repetition is evidence, and evidence is a set of turns -- never a counter.

The operator's ask: "if I repeatedly say 'when I ask for reports, format them like this', mature
memory should make that stick." VOOL's memory record was already richer than the competition's
(authority, confidence, status, expires_at, review_after, superseded_record_id, last_confirmed_at)
and still could not do this, because nothing anywhere counted repetition. A correction made three
times was stored exactly like a remark made once.

WHY A SET OF TURN IDS RATHER THAN A COUNT. Researched live 2026-08-15: Hermes runs a background
self-improvement review that "may quietly save a memory or update a skill", and OpenClaw appends to
MEMORY.md with no consolidation (its own tracker calls memory management "in chaos", openclaw#43747,
and truncates silently at ~20K chars, openclaw#45415). A background writer with no per-turn
attribution is the exact shape shown to enable silent memory pollution (arXiv 2603.23064), where
pre-compaction flushes and heartbeat prompts are all found insufficient.

A counter can be inflated by a background job re-saving its own conclusion. A set of distinct turn
ids cannot, and every element can be replayed against the exchange that produced it. That is the
whole design, and these tests pin it from both directions:

  * a directive asked three DIFFERENT ways across three turns is ONE directive with strength 3;
  * the same turn folded five times is strength 1;
  * an unattributed write is refused outright.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.correction_evidence import (
    DEFAULT_EVIDENCE_BUDGET,
    DEFAULT_STALE_AFTER_DAYS,
    PROMOTION_THRESHOLD,
    accumulate_correction,
    correction_directive,
    enforce_evidence_budget,
    promotion_proposals,
    retirement_proposals,
)


def _fold(*turns: tuple[str, str]) -> dict:
    evidence: dict = {}
    for turn_id, text in turns:
        evidence = accumulate_correction(evidence, text=text, turn_id=turn_id)
    return evidence


# ---------------------------------------------------------------------------------------------
# Detecting a correction of CONDUCT, not a statement about the world
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "I told you already, always give me the short version",
        "stop adding markdown headers to every report",
        "from now on format reports as a table",
        "no, I said use metric units",
        "please never apologise at the start of an answer",
        "every time I ask for a summary keep it under 5 lines",
        "how many times do I have to say use ISO dates",
        "in future, put the conclusion first",
    ),
)
def test_a_behavioural_correction_is_recognised(text: str) -> None:
    assert correction_directive(text) is not None, text


@pytest.mark.parametrize(
    "text",
    (
        # Ordinary requests. Treating these as standing rules would rewrite the operator's
        # defaults from a single passing sentence.
        "what is the capital of france",
        "summarise this report for me",
        "write a python function that returns 42",
        # The cue words appear, but describe the world rather than instruct the runtime.
        "I told him to stop by the office",
        "stop words are removed by the tokenizer",
        "the build always fails on tuesdays",
        "never mind",
        # Corrects THIS turn but names no rule worth carrying forward.
        "no",
        "stop it",
    ),
)
def test_an_ordinary_turn_is_not_a_standing_rule(text: str) -> None:
    """The dangerous direction: a passing remark promoted into a permanent behaviour change."""

    assert correction_directive(text) is None, text


# ---------------------------------------------------------------------------------------------
# Repetition across turns, asked differently each time
# ---------------------------------------------------------------------------------------------


def test_one_directive_asked_three_ways_is_one_directive() -> None:
    """The operator's actual ask. Nobody repeats themselves verbatim, so an exact-key match filed
    three phrasings as three unrelated one-off remarks -- measured, and the reason directives are
    matched by word overlap instead."""

    evidence = _fold(
        ("turn-1", "always give me the short version"),
        ("turn-2", "I told you already, give me the short version always"),
        ("turn-3", "short version, always please"),
    )

    assert len(evidence) == 1
    (only,) = evidence.values()
    assert only.strength == 3
    assert only.ready_for_promotion is True


def test_distinct_directives_never_merge() -> None:
    """Overlap matching must not collapse unrelated rules onto one entry, which would let evidence
    for one instruction promote a different one."""

    evidence = _fold(
        ("turn-1", "always give me the short version"),
        ("turn-2", "stop adding markdown headers to reports"),
        ("turn-3", "from now on use metric units"),
    )

    assert len(evidence) == 3
    assert all(item.strength == 1 for item in evidence.values())
    assert promotion_proposals(evidence) == []


def test_a_near_miss_directive_is_not_the_same_directive() -> None:
    """Sharing one word is not agreement: "short version" and "short deadline" are different rules."""

    evidence = _fold(
        ("turn-1", "always give me the short version"),
        ("turn-2", "always use the short deadline"),
    )

    assert len(evidence) == 2


# ---------------------------------------------------------------------------------------------
# The anti-pollution primitive
# ---------------------------------------------------------------------------------------------


def test_the_same_turn_can_never_vote_twice() -> None:
    """A background writer replaying its own conclusion must not manufacture confidence.

    This is the specific failure shape in arXiv 2603.23064: a background process with memory write
    access and no attribution. Folding one turn five times is one piece of evidence, so the
    promotion threshold cannot be reached by a loop talking to itself.
    """

    evidence: dict = {}
    for _ in range(5):
        evidence = accumulate_correction(
            evidence, text="always give me the short version", turn_id="turn-1"
        )

    (only,) = evidence.values()
    assert only.strength == 1
    assert only.ready_for_promotion is False
    assert promotion_proposals(evidence) == []


def test_an_unattributed_write_is_refused() -> None:
    """An entry that cannot name the turn that taught it is invalid by construction."""

    for missing in ("", "   ", None):
        with pytest.raises(ValueError, match="turn_id is required"):
            accumulate_correction({}, text="always give me the short version", turn_id=missing)  # type: ignore[arg-type]


def test_evidence_names_the_turns_so_a_proposal_can_be_audited() -> None:
    """A proposal the operator cannot check is a proposal they have to take on trust."""

    evidence = _fold(
        ("turn-a", "always give me the short version"),
        ("turn-b", "give me the short version always"),
        ("turn-c", "short version always please"),
    )
    (proposal,) = promotion_proposals(evidence)

    assert set(proposal.evidence_turn_ids) == {"turn-a", "turn-b", "turn-c"}


# ---------------------------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------------------------


def test_below_the_threshold_nothing_is_proposed() -> None:
    """Two is a coincidence; one is a remark."""

    evidence = _fold(
        ("turn-1", "always give me the short version"),
        ("turn-2", "give me the short version always"),
    )

    assert promotion_proposals(evidence) == []
    assert PROMOTION_THRESHOLD == 3


def test_proposals_are_ordered_by_strength() -> None:
    evidence = _fold(
        ("t1", "always give me the short version"),
        ("t2", "give me the short version always"),
        ("t3", "short version always please"),
        ("t4", "the short version, always"),
        ("t5", "always use ISO dates"),
        ("t6", "use ISO dates always"),
        ("t7", "ISO dates, always please"),
    )
    proposals = promotion_proposals(evidence)

    assert len(proposals) == 2
    assert proposals[0].strength >= proposals[1].strength


def test_a_repeated_turn_never_grows_the_evidence_list() -> None:
    """Strength dedupes, but the STORED list must not grow either.

    Unbounded append is the bloat failure this whole design exists to avoid -- OpenClaw's own
    tracker calls it "memory management is in chaos" (openclaw#43747), where entries accumulate and
    are never consolidated. A background writer folding its conclusion a thousand times must leave
    one turn id behind, not a thousand.
    """

    evidence: dict = {}
    for _ in range(50):
        evidence = accumulate_correction(
            evidence, text="always give me the short version", turn_id="turn-1"
        )

    (only,) = evidence.values()
    assert only.evidence_turn_ids == ("turn-1",)
    assert len(only.evidence_turn_ids) == 1


def test_the_overlap_ratio_is_what_keeps_directives_apart() -> None:
    """The merge rule stated directly, so a change to it cannot pass unnoticed.

    Sharing a single word out of two is 0.5 and must not merge; sharing both is 1.0 and must.
    """

    apart = _fold(
        ("t1", "always use metric units"),
        ("t2", "always use ISO dates"),
    )
    assert len(apart) == 2

    together = _fold(
        ("t1", "always use metric units"),
        ("t2", "use metric units always"),
    )
    assert len(together) == 1


# ---------------------------------------------------------------------------------------------
# A reversal is not agreement -- polarity and supersession
# ---------------------------------------------------------------------------------------------


def test_a_reversal_never_promotes_the_rule_it_reversed() -> None:
    """The defect this file's own first version shipped, and the worst kind of "learning".

    "always use metric units" then "never use metric units" twice merged into ONE entry with
    strength 3, and the proposal carried the FIRST text -- so an operator who changed their mind
    would have had the rule they withdrew promoted, backed by the strength of their own
    objections to it. Polarity was in the noise list, so it was discarded before matching.

    This is precisely the failure mode criticised in OpenClaw's memory: remembering everything
    that was said while understanding none of it.
    """

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "never use metric units"),
        ("t2", "never use metric units please"),
    )

    assert len(evidence) == 2, "a reversal must not merge into the directive it reverses"
    proposals = promotion_proposals(evidence)
    assert not any("always" in item.text for item in proposals)


def test_the_reversed_directive_is_kept_and_names_the_turn_that_withdrew_it() -> None:
    """Superseded, never deleted: "why does it think that?" has to stay answerable, and the
    answer is a turn id."""

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "never use metric units"),
    )
    reversed_entry = next(item for item in evidence.values() if "always" in item.text)

    assert reversed_entry.superseded_by_turn_id == "t1"
    assert reversed_entry.evidence_turn_ids == ("t0",)


def test_a_superseded_directive_can_never_be_promoted() -> None:
    """Even with enough evidence behind it before the reversal."""

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "use metric units always"),
        ("t2", "metric units, always"),
        ("t3", "never use metric units"),
    )
    withdrawn = next(item for item in evidence.values() if item.superseded_by_turn_id)

    assert withdrawn.strength >= PROMOTION_THRESHOLD
    assert withdrawn.ready_for_promotion is True
    assert withdrawn not in promotion_proposals(evidence)


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("stop adding markdown headers", "never add markdown headers"),
        ("never add markdown headers", "don't add markdown headers please"),
        ("always use ISO dates", "use ISO dates always"),
    ),
)
def test_directives_that_agree_still_merge(first: str, second: str) -> None:
    """The control: polarity separates opposites, and must not separate agreement.

    "stop adding X", "never add X" and "don't add X" are one rule stated three ways -- splitting
    them would make repetition uncountable again, which is the whole thing this module is for.
    """

    evidence = _fold(("a", first), ("b", second))

    assert len(evidence) == 1
    (only,) = evidence.values()
    assert only.strength == 2
    assert only.superseded_by_turn_id == ""


def test_an_unrelated_negative_does_not_supersede() -> None:
    """Supersession is per TOPIC. A new prohibition about something else must leave an existing
    rule standing, or one complaint would silently retire every unrelated instruction."""

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "never add markdown headers"),
    )

    assert all(item.superseded_by_turn_id == "" for item in evidence.values())
    assert len(evidence) == 2


def test_a_negative_that_merely_shares_a_word_does_not_supersede() -> None:
    """The case that makes the topic-overlap threshold load-bearing rather than decorative.

    "never use imperial units" shares exactly one word -- "units" -- with "always use metric units
    for reports", and means something entirely compatible with it. One shared word against a
    two-word topic scores 0.5, under the 0.6 threshold -- and without that threshold a single prohibition would retire any rule that
    happened to mention one of the same nouns.

    Written after a sabotage that removed the threshold SURVIVED the first version of these tests:
    every "unrelated" case there shared zero words, so the check above it caught them and the
    threshold itself was never exercised.
    """

    evidence = _fold(
        ("t0", "always use metric units for reports"),
        ("t1", "never use imperial units"),
    )

    standing = next(item for item in evidence.values() if "metric" in item.text)
    assert standing.superseded_by_turn_id == "", (
        "a prohibition about a different subject retired an unrelated standing rule"
    )


# ---------------------------------------------------------------------------------------------
# The budget reports what it costs -- never a silent trim
# ---------------------------------------------------------------------------------------------


def test_a_budgeted_drop_names_exactly_what_it_dropped() -> None:
    """A bound that cannot be observed is indistinguishable from data loss.

    OpenClaw truncates MEMORY.md at ~20K characters with NO warning (openclaw#45415), so the
    operator finds out by noticing the agent forgot something they relied on. Having a cap is not
    the differentiator -- reporting it is.
    """

    evidence: dict = {}
    for index in range(6):
        evidence = accumulate_correction(
            evidence, text=f"always use widget{index} everywhere", turn_id=f"t{index}"
        )

    outcome = enforce_evidence_budget(evidence, budget=4)

    assert outcome.within_budget is False
    assert len(outcome.kept) == 4
    assert len(outcome.dropped) == 2
    assert "dropped to stay within a budget of 4" in outcome.reason
    for item in outcome.dropped:
        assert item.directive_key in outcome.reason


def test_the_strongest_directives_are_the_last_to_go() -> None:
    """Eviction is by how little an entry carries, so a rule asked for repeatedly outlives one
    mentioned once. Dropping by insertion order would discard exactly the instructions the
    operator cared most about."""

    evidence: dict = {}
    for index in range(5):
        evidence = accumulate_correction(
            evidence, text=f"always use widget{index} everywhere", turn_id=f"t{index}"
        )
    for turn in ("extra-1", "extra-2"):
        evidence = accumulate_correction(
            evidence, text="always use widget0 everywhere", turn_id=turn
        )

    outcome = enforce_evidence_budget(evidence, budget=2)

    surviving = " ".join(item.text for item in outcome.kept.values())
    assert "widget0" in surviving


def test_a_superseded_directive_is_evicted_before_a_live_one() -> None:
    """Withdrawn rules are kept for readability, but they are the cheapest thing to lose."""

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "never use metric units"),
        ("t2", "always put the conclusion first"),
    )

    outcome = enforce_evidence_budget(evidence, budget=2)

    assert all(item.superseded_by_turn_id == "" for item in outcome.kept.values())
    assert any(item.superseded_by_turn_id for item in outcome.dropped)


def test_under_budget_nothing_is_touched() -> None:
    """The control: a budget must not become a reason to lose data that fits."""

    evidence = _fold(
        ("t0", "always use metric units"),
        ("t1", "always put the conclusion first"),
    )

    outcome = enforce_evidence_budget(evidence, budget=DEFAULT_EVIDENCE_BUDGET)

    assert outcome.within_budget is True
    assert outcome.kept == evidence
    assert outcome.dropped == ()


def test_a_negative_budget_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        enforce_evidence_budget({}, budget=-1)


# ---------------------------------------------------------------------------------------------
# Decay: bloat handled without anyone gardening a file
# ---------------------------------------------------------------------------------------------

_T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)


def test_a_directive_nobody_has_asked_for_in_months_is_proposed_for_retirement() -> None:
    """OpenClaw's own tracker records entries accumulating with no consolidation (openclaw#43747),
    so context is burned on rules the operator moved on from. Re-confirmation is what keeps a
    directive alive, and asking for it again is all re-confirmation takes."""

    evidence = accumulate_correction(
        {}, text="always use metric units", turn_id="t0", at=_T0
    )

    stale = retirement_proposals(
        evidence, now=_T0 + dt.timedelta(days=DEFAULT_STALE_AFTER_DAYS + 1)
    )

    assert [item.directive_key for item in stale] == list(evidence)


def test_asking_for_it_again_keeps_it_alive() -> None:
    """The whole point: a rule that still matters never goes stale, because using it re-stamps it."""

    evidence = accumulate_correction({}, text="always use metric units", turn_id="t0", at=_T0)
    later = _T0 + dt.timedelta(days=DEFAULT_STALE_AFTER_DAYS - 1)
    evidence = accumulate_correction(
        evidence, text="use metric units always", turn_id="t1", at=later
    )

    assert retirement_proposals(evidence, now=later + dt.timedelta(days=2)) == []


def test_a_fresh_directive_is_never_proposed() -> None:
    evidence = accumulate_correction({}, text="always use metric units", turn_id="t0", at=_T0)

    assert retirement_proposals(evidence, now=_T0 + dt.timedelta(days=1)) == []


def test_the_boundary_is_the_boundary() -> None:
    """Pinned exactly, because an off-by-one here quietly retires live instructions."""

    evidence = accumulate_correction({}, text="always use metric units", turn_id="t0", at=_T0)

    just_inside = _T0 + dt.timedelta(days=DEFAULT_STALE_AFTER_DAYS) - dt.timedelta(seconds=1)
    exactly_on = _T0 + dt.timedelta(days=DEFAULT_STALE_AFTER_DAYS)

    assert retirement_proposals(evidence, now=just_inside) == []
    assert len(retirement_proposals(evidence, now=exactly_on)) == 1


def test_a_withdrawn_directive_is_not_also_proposed_for_retirement() -> None:
    """Two mechanisms competing to remove the same record is how a lifecycle becomes a race.

    Superseded entries are already unpromotable and are kept only so a withdrawn rule stays
    readable; the budget disposes of them.
    """

    evidence = accumulate_correction({}, text="always use metric units", turn_id="t0", at=_T0)
    evidence = accumulate_correction(
        evidence, text="never use metric units", turn_id="t1", at=_T0
    )

    stale = retirement_proposals(
        evidence, now=_T0 + dt.timedelta(days=DEFAULT_STALE_AFTER_DAYS + 1)
    )

    assert all(item.superseded_by_turn_id == "" for item in stale)


def test_an_unreadable_timestamp_never_causes_a_silent_retirement() -> None:
    """A parse failure is not evidence of staleness. Proposing retirement on malformed data would
    delete instructions because a field was corrupt."""

    from core.correction_evidence import CorrectionEvidence

    evidence = {
        "yes|metric:units": CorrectionEvidence(
            directive_key="yes|metric:units",
            text="always use metric units",
            evidence_turn_ids=("t0",),
            last_confirmed_at="not-a-timestamp",
        )
    }

    assert retirement_proposals(evidence, now=_T0 + dt.timedelta(days=9999)) == []


def test_a_negative_window_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        retirement_proposals({}, now=_T0, stale_after_days=-1)
