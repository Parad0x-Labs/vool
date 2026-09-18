"""Conformance harness for claim predicates -- the instrument, not the gate.

`tests/claim_predicate_census.py` holds the census and the runner. This module asserts the
instrument's OWN integrity and prints the ranked divergence table:

* every censused predicate imports and is callable (a census row that cannot run is not a row);
* every predicate has ground truth in BOTH directions -- at least one SHOULD CLAIM and one
  SHOULD NOT CLAIM case. A harness that only measures false positives is the mistake that has
  already cost this project two rounds, so it is asserted rather than hoped for;
* every mandated hard-input class is present in the corpus (Section 0.4: a test built around a
  friendly input proves nothing);
* the instrument can DETECT A BROKEN PREDICATE -- proved by breaking one in memory and measuring
  that the report changes, then restoring it and measuring that the report goes back.

It deliberately does NOT fail on measured divergence. The divergence is the current state of the
tree; a red harness on day one carries no information and gets deleted or skipped by the next
person. Promotion to a gate happens per-predicate, after a repair, by adding the pred_id to
`GATED_PREDICATES` below -- which is empty today, on purpose.
"""
from __future__ import annotations

import json

import pytest

from tests import claim_predicate_census as census

#: Predicates whose divergence is repaired and must stay repaired. Empty by design: nothing has
#: been repaired yet. Adding a pred_id here turns its row into a hard gate.
GATED_PREDICATES: frozenset[str] = frozenset()


@pytest.fixture(scope="module")
def report() -> dict:
    return census.run_conformance()


# -------------------------------------------------------------------------------------------------
# Census integrity
# -------------------------------------------------------------------------------------------------


def test_every_censused_predicate_is_callable() -> None:
    """A census row that cannot be executed is an assertion, not a measurement."""
    broken: list[str] = []
    for pred in census.PREDICATES:
        try:
            pred.call("hello")
        except Exception as exc:
            broken.append(f"{pred.pred_id} ({pred.where}): {type(exc).__name__}: {exc}")
    assert not broken, "censused predicates that will not run:\n  " + "\n  ".join(broken)


def test_census_rows_are_unique_and_attributed() -> None:
    seen: set[str] = set()
    for pred in census.PREDICATES:
        assert pred.pred_id not in seen, f"duplicate pred_id {pred.pred_id}"
        seen.add(pred.pred_id)
        assert pred.consumers, f"{pred.pred_id}: no consumer recorded -- unconsumed is not a claim"
        assert pred.severity in census.SEVERITY, f"{pred.pred_id}: bad severity"
        assert pred.blast in census.BLAST, f"{pred.pred_id}: bad blast radius"
        assert pred.divergence in (
            census.DIV_NONE, census.DIV_NARROWER, census.DIV_WIDER, census.DIV_DIFFERENT
        ), f"{pred.pred_id}: bad divergence class"


def test_every_predicate_has_ground_truth_in_both_directions() -> None:
    """Both directions, per predicate. This is the anti-one-sidedness assertion."""
    missing: list[str] = []
    for pred in census.PREDICATES:
        claims = [c for c in pred.cases if c.verdict == census.CLAIM]
        refusals = [c for c in pred.cases if c.verdict == census.NO_CLAIM]
        if not claims:
            missing.append(f"{pred.pred_id}: no SHOULD-CLAIM case")
        if not refusals:
            missing.append(f"{pred.pred_id}: no SHOULD-NOT-CLAIM case")
    assert not missing, "one-directional predicates:\n  " + "\n  ".join(missing)


def test_every_counted_case_names_who_decided() -> None:
    """Ground truth without attribution is an opinion. Contested cases carry a reason instead."""
    bad: list[str] = []
    for pred in census.PREDICATES:
        for case in pred.cases:
            if case.verdict == census.CONTESTED:
                if len(case.why) < 30:
                    bad.append(f"{pred.pred_id}/{case.case_id}: CONTESTED without a real reason")
                continue
            if case.basis not in ("name", "consumer", "both"):
                bad.append(f"{pred.pred_id}/{case.case_id}: basis {case.basis!r} is not admissible")
            if len(case.why) < 20:
                bad.append(f"{pred.pred_id}/{case.case_id}: no justification")
    assert not bad, "cases without attributable ground truth:\n  " + "\n  ".join(bad)


def test_contested_cases_are_excluded_from_the_counts(report: dict) -> None:
    """A contested case must not silently land on either side of the ledger."""
    contested = sum(
        1 for pred in census.PREDICATES for c in pred.cases if c.verdict == census.CONTESTED
    )
    assert contested > 0, "no contested cases at all -- either every question is settled, or the " \
                          "census is picking sides it should be declining"
    assert report["contested_excluded"] == contested
    counted = sum(
        1 for pred in census.PREDICATES
        for c in pred.cases if c.verdict in (census.CLAIM, census.NO_CLAIM)
    )
    assert report["directional_cases"] == counted


# -------------------------------------------------------------------------------------------------
# Hard inputs (Section 0.4)
# -------------------------------------------------------------------------------------------------


def test_every_mandated_hard_input_class_is_present() -> None:
    present: set[str] = set()
    for pred in census.PREDICATES:
        for case in pred.cases:
            present |= set(case.tags)
    for _sid, _text, tags in census.SHARED_HARD_CORPUS:
        present |= set(tags)
    missing = [tag for tag in census.MANDATED_TAGS if tag not in present]
    assert not missing, f"hard-input classes absent from the corpus: {missing}"


def test_hard_inputs_are_not_only_in_the_shared_corpus() -> None:
    """The shared rows carry no ground truth, so a hard class present only there is unjudged."""
    directional: set[str] = set()
    for pred in census.PREDICATES:
        for case in pred.cases:
            if case.verdict in (census.CLAIM, census.NO_CLAIM):
                directional |= set(case.tags)
    for tag in (
        census.TAG_SLOPPY, census.TAG_NEGATION, census.TAG_QUOTED,
        census.TAG_NUMBERED, census.TAG_BULLETED, census.TAG_CANCEL, census.TAG_CONTINUATION,
    ):
        assert tag in directional, f"{tag} appears in no case that carries ground truth"


def test_negation_is_judged_against_more_than_one_predicate() -> None:
    """`Do not X` is the single most load-bearing hard class: it is the shape that turns a wrong
    claim into an S5 write past a denial. One predicate's worth of negation coverage is not
    coverage."""
    with_negation = {
        pred.pred_id
        for pred in census.PREDICATES
        for case in pred.cases
        if census.TAG_NEGATION in case.tags and case.verdict in (census.CLAIM, census.NO_CLAIM)
    }
    assert len(with_negation) >= 8, f"only {len(with_negation)} predicates judged under negation"


# -------------------------------------------------------------------------------------------------
# ANTI-VACUITY: prove the instrument can fail
# -------------------------------------------------------------------------------------------------


def test_harness_detects_a_broken_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break one predicate in memory, measure that the report says so, restore, measure it stops.

    The subject is `analyze_retrieval_constraints(...).has_prohibition` -- the retrieval-denial
    detector. Sabotaging it to answer "no prohibition" is exactly the S5 shape ("act past a
    denial"), so if the harness cannot see this, it cannot see the thing it was built for.

    It is deliberately NOT `is_opted_out`: that predicate already misses `Never write anything to
    disk.` at HEAD, so its control is red and a sabotage there could not be attributed. Picking a
    subject with a clean control is the whole point of asserting the control first.

    Both halves are asserted. A harness that reports divergence unconditionally would pass the
    broken half and fail the restored half, which is why the restored half is measured too.
    """
    subject = "retrieval_prohibition"
    baseline = census.run_conformance()
    baseline_row = next(r for r in baseline["rows"] if r["pred_id"] == subject)
    assert baseline_row["missed_claims"] == 0 and baseline_row["false_claims"] == 0, (
        f"control failed before the sabotage: {subject} already diverges at HEAD, so this proof "
        f"cannot attribute anything. row={baseline_row['failures']}"
    )
    assert baseline_row["should_claim_total"] >= 4, "sabotage subject has too little ground truth"

    import core.retrieval_constraints as rc

    real = rc.analyze_retrieval_constraints

    def no_prohibition(text: str):
        from dataclasses import replace as _replace

        return _replace(real(text), has_prohibition=False)

    monkeypatch.setattr(rc, "analyze_retrieval_constraints", no_prohibition)
    broken = census.run_conformance()
    broken_row = next(r for r in broken["rows"] if r["pred_id"] == subject)

    assert broken_row["missed_claims"] == baseline_row["should_claim_total"], (
        "the harness did not report the sabotage: every SHOULD-CLAIM case must be reported as a "
        f"missed claim, got {broken_row['missed_claims']} of {baseline_row['should_claim_total']}"
    )
    assert broken_row["rank_score"] > baseline_row["rank_score"], (
        "the ranking did not move, so a broken denial detector would not rise to the top of the "
        "repair list"
    )
    assert broken["divergent_cases"] > baseline["divergent_cases"]
    directions = {f["direction"] for f in broken_row["failures"]}
    assert directions == {"MISSED_CLAIM"}, (
        f"sabotage misattributed: expected only MISSED_CLAIM rows, got {directions}"
    )

    monkeypatch.undo()
    restored = census.run_conformance()
    restored_row = next(r for r in restored["rows"] if r["pred_id"] == subject)
    assert restored_row["missed_claims"] == 0, "the sabotage outlived the monkeypatch"
    assert restored_row["rank_score"] == baseline_row["rank_score"]
    assert restored["divergent_cases"] == baseline["divergent_cases"]


def test_harness_detects_a_predicate_that_starts_claiming_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction of the same proof: an over-claiming predicate must also be visible.

    Measuring only false negatives would repeat, in mirror image, the one-sidedness this harness
    exists to correct.
    """
    baseline = census.run_conformance()
    base_row = next(r for r in baseline["rows"] if r["pred_id"] == "looks_like_code_audit_request")
    assert base_row["false_claims"] == 0, (
        f"control failed before the sabotage: {base_row['failures']}"
    )

    import core.agent_runtime.workspace_audit as wa

    monkeypatch.setattr(wa, "looks_like_code_audit_request", lambda *_a, **_k: True)
    broken = census.run_conformance()
    broken_row = next(
        r for r in broken["rows"] if r["pred_id"] == "looks_like_code_audit_request"
    )
    assert broken_row["false_claims"] == base_row["should_not_total"] >= 2
    assert broken_row["shared_claim_rate"] == 1.0, (
        "the shared-corpus observation did not move, so a predicate that claims every turn would "
        "not show up as one"
    )
    assert {f["direction"] for f in broken_row["failures"]} == {"FALSE_CLAIM"}

    monkeypatch.undo()
    restored_row = next(
        r for r in census.run_conformance()["rows"]
        if r["pred_id"] == "looks_like_code_audit_request"
    )
    assert restored_row["false_claims"] == 0
    assert restored_row["shared_claim_rate"] == base_row["shared_claim_rate"]


def test_a_raising_predicate_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`slice_families` already treats a raising probe as "did not claim". The instrument must not
    inherit that: a predicate that blows up is a finding with its own row."""

    import core.currency_intent as ci

    def boom(*_a, **_k):
        raise RuntimeError("sabotage")

    monkeypatch.setattr(ci, "fx_rate_lookup_intent", boom)
    broken = census.run_conformance()
    row = next(r for r in broken["rows"] if r["pred_id"] == "fx_rate_lookup_intent")
    assert row["errors"] > 0, "a raising predicate was recorded as a clean non-claim"
    assert any("RuntimeError: sabotage" in (f.get("error") or "") for f in row["failures"])
    assert broken["errors"] > 0


def test_is_plain_task_guard_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """A censused claim about a consumer, measured rather than read.

    The census row for `is_plain_task` says its only guard changes nothing. That is a causal claim,
    so it is executed: force the predicate True, then False, and compare the consumer's output to
    HEAD across a corpus that includes real channel-action prompts. `channel_actions.py:43` reads
    `if is_plain_task(raw) and not _EXPLICIT_CHANNEL_ACTION_RE.search(raw): return None, None` and
    line 45 reads `if not _EXPLICIT_CHANNEL_ACTION_RE.search(raw): return None, None` -- the second
    subsumes the first, so the predicate cannot move the outcome.

    Recorded because it is the one shape the census would otherwise over-report: a predicate whose
    divergence has no blast radius at all, because its consumer never acts on it.
    """
    import core.channel_actions as ca

    corpus = (
        "Translate this to French: hello",
        "post this to discord: hello team",
        "send a telegram message saying hi",
        "Explain what a mutex is",
        "What is the capital of France?",
        "post to dc: build shipped",
        "announce on telegram that the build shipped",
    )
    head = [ca.parse_channel_post_intent(text) for text in corpus]
    for forced in (True, False):
        monkeypatch.setattr(ca, "is_plain_task", lambda *_a, _v=forced, **_k: _v)
        assert [ca.parse_channel_post_intent(text) for text in corpus] == head, (
            f"forcing is_plain_task={forced} changed its consumer's output, so the census row "
            "claiming the guard is inert is wrong and must be corrected"
        )
        monkeypatch.undo()
    assert [ca.parse_channel_post_intent(text) for text in corpus] == head


def test_whole_turn_authority_without_consumption_is_measured(
    capsys: pytest.CaptureFixture,
) -> None:
    """Quantify the #1 ranked divergence over every corpus text this module holds.

    `coverage_for(...).covers_whole_turn` is True whenever no OTHER registered family objects --
    including when this family consumed nothing at all. `turn_frontdoor.py:1122` and
    `web/api/runtime.py:1563` both read that as "this family answered the turn". This measures how
    often the two readings come apart across every family the coverage module registers.

    Reported, not gated. The number is the argument for the repair, not a threshold to defend.
    """
    from core.agent_runtime import answer_coverage as ac

    families = [
        ac.FAMILY_CURRENCY, ac.FAMILY_ASSISTANT_IDENTITY, ac.FAMILY_RECEIPT_LOCATION,
        ac.FAMILY_WORKSPACE_AUDIT, ac.FAMILY_PROJECT_BUILD, ac.FAMILY_FILE_WRITE,
        ac.FAMILY_LIVE_INFO,
    ]
    texts = {text for _sid, text, _tags in census.SHARED_HARD_CORPUS}
    texts |= {case.text for pred in census.PREDICATES for case in pred.cases}

    whole_empty = whole_consumed = sliced = 0
    for text in sorted(texts):
        for family in families:
            coverage = ac.coverage_for(text, family)
            if coverage.covers_whole_turn and not coverage.consumed:
                whole_empty += 1
            elif coverage.covers_whole_turn:
                whole_consumed += 1
            else:
                sliced += 1
    pairs = whole_empty + whole_consumed + sliced
    assert pairs == len(texts) * len(families)

    with capsys.disabled():
        print(
            f"\nWHOLE-TURN AUTHORITY WITHOUT CONSUMPTION: {whole_empty}/{pairs} family-turn pairs "
            f"({100 * whole_empty / pairs:.1f}%) over {len(texts)} texts x {len(families)} "
            f"families\n  whole_turn having consumed at least one clause: {whole_consumed}"
            f"\n  slice scope: {sliced}"
        )

    assert whole_empty > 0, (
        "the divergence this harness ranks first did not reproduce -- either it was repaired, or "
        "the corpus no longer reaches it. Re-derive the ranking before trusting it."
    )


# -------------------------------------------------------------------------------------------------
# The report itself
# -------------------------------------------------------------------------------------------------


def test_report_is_machine_readable_and_ranked(report: dict) -> None:
    dest = census.write_report(report)
    with open(dest, encoding="utf-8") as fh:
        round_tripped = json.load(fh)
    assert round_tripped["predicates_censused"] == len(census.PREDICATES)
    scores = [r["rank_score"] for r in round_tripped["rows"]]
    assert scores == sorted(scores, reverse=True), "the report is not ranked"
    for row in round_tripped["rows"]:
        assert row["consumers"], f"{row['pred_id']}: no consumers in the report"
        assert row["counted_cases"] >= 2


def test_divergence_is_reported_not_gated(report: dict, capsys: pytest.CaptureFixture) -> None:
    """Prints the ranked table. Passes whatever the divergence is -- by design, and only for
    predicates that have not been promoted into GATED_PREDICATES."""
    dest = census.write_report(report)
    table = census.render_table(report)
    with capsys.disabled():
        print(table)
        print(f"machine-readable report: {dest}")
        print("(override the path with VOOL_PREDICATE_REPORT=/path/to/report.json)")

    regressions = [
        row for row in report["rows"]
        if row["pred_id"] in GATED_PREDICATES and (row["divergent_cases"] or row["errors"])
    ]
    assert not regressions, (
        "predicates promoted to gated status have regressed: "
        + ", ".join(r["pred_id"] for r in regressions)
    )
