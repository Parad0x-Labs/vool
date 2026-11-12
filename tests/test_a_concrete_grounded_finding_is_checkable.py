"""A nominated bug with a concrete, source-grounded failure scenario is checkable, whatever its wording.

From the live agent-team comparison, 2026-10-07. VOOL's audit nominated the right bug in `orders.py`
("Partial line cancellation never releases stock": cancel 2 of 5 units, `stock.release` only runs when the line
reaches 0, so the 2 units stay reserved) and declared it `integrity`. The checker demoted it, because the
scenario used none of the words in its harm vocabulary ("incorrect", "corrupted", "crash", ...), then refused it
as the primary finding ("the scenario identifies no incorrect output, corruption, silent data loss, crash ...").
Three correct nominations failed that way and the audit ended "three nominated candidates failed source
checking". Whether a claim can be settled by running something depends on whether its scenario is concrete
(a literal input) and grounded (it names code that is on the cited lines), not on its vocabulary.

The retry window had a separate defect: a citation near the top of a 35-line file showed lines 1-21 only, so
the model was told its source was "truncated at line 21".

On the v0.8 line a declared crash/integrity PRIMARY no longer depends on its words at all: it keeps its class and
the source challenge decides (tests/test_a_real_bug_in_plain_words_reaches_the_challenge.py). Words still decide
where no challenge runs: a survey row's declared class (a survey row is shown unchallenged) and a candidate that
declares no class. That is where these rules apply, and where these tests look.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import _finding_defect, _source_window

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"


def _evidence() -> AuditEvidence:
    sources = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}
    return AuditEvidence(inspected_paths=tuple(sources), all_paths=tuple(sources), sources=sources,
                         workspace_root=str(FIXTURE), scoped_target="orders.py")


# Verbatim from the run's ledger (t1_shop VOOL single, counted run c1).
PARTIAL_CANCEL = {
    "title": "Partial line cancellation never releases stock", "file": "orders.py", "line_start": 25, "line_end": 28,
    "cited_line_text": "    order.lines[sku] = current - qty",
    "failure_scenario": (
        "User cancels 2 of 5 units of a SKU in a placed order. The order's line drops to 3 but stock.release is "
        "only called when the line reaches 0, so those 2 units remain reserved forever until the whole line is "
        "cancelled."
    ),
    "harm_class": "integrity",
}


REFUSED_AS_PRIMARY = "identifies no incorrect output"


def _as_survey_row(candidate: dict) -> dict:
    """The class a survey row keeps. Survey rows are shown without a challenge, so only words can back it."""
    row = dict(candidate)
    _finding_defect(row, _evidence(), row["file"], allow_observations=True)
    return row


def _undeclared(candidate: dict) -> tuple[str, dict]:
    """The checker's verdict on the same story when the model declares no class."""
    bare = dict(candidate, harm_class="")
    return _finding_defect(bare, _evidence(), bare["file"]), bare


def test_the_runs_correct_nomination_is_accepted_as_the_primary_finding():
    candidate = dict(PARTIAL_CANCEL)
    assert _finding_defect(candidate, _evidence(), "orders.py") == ""
    assert candidate["harm_class"] == "integrity"


def test_the_runs_correct_nomination_keeps_its_class_as_a_survey_row_and_when_undeclared():
    assert _as_survey_row(PARTIAL_CANCEL)["harm_class"] == "integrity"
    defect, bare = _undeclared(PARTIAL_CANCEL)
    assert defect == "" and bare["harm_class"] == "integrity"


VAGUE = {
    "title": "Ordering logic is fragile", "file": "orders.py", "line_start": 25, "line_end": 28,
    "cited_line_text": "    order.lines[sku] = current - qty",
    "failure_scenario": "Under some conditions the behaviour may be surprising for callers.",
    "harm_class": "integrity",
}
UNRELATED = dict(PARTIAL_CANCEL, failure_scenario="Calling refund(7) twice pays the customer 14 dollars.",
                 title="Refunds pay out twice")


def test_an_ungrounded_vague_claim_is_still_not_promoted():
    assert _as_survey_row(VAGUE)["harm_class"] not in {"integrity", "crash"}
    defect, _ = _undeclared(VAGUE)
    assert REFUSED_AS_PRIMARY in defect
    # As a declared primary it is not backed by its words, so only the challenge can show it.
    primary = dict(VAGUE)
    assert _finding_defect(primary, _evidence(), "orders.py") == "" and primary.get("claim_needs_challenge") is True


def test_a_concrete_claim_that_names_no_code_on_its_lines_is_not_promoted():
    assert _as_survey_row(UNRELATED)["harm_class"] not in {"integrity", "crash"}
    defect, _ = _undeclared(UNRELATED)
    assert REFUSED_AS_PRIMARY in defect


def test_a_citation_near_the_top_of_a_short_file_shows_the_whole_file():
    window = _source_window(_evidence(), "orders.py", 1, 1)
    numbers = [int(line.split(":", 1)[0]) for line in window.splitlines()]
    assert numbers[0] == 1 and numbers[-1] == _evidence().line_count("orders.py")


# ---- review of 6ad76ec7 (independent, 2026-10-07): a literal plus a nearby identifier is not grounding.
# Each story below has a number and names code on the cited lines, yet states no wrong outcome; 9adff83 refused
# all four and they must stay refused. Wording verbatim from the review's probes.
NOT_A_BUG = [
    ("With 2 orders, order.lines may be hard to read for new developers.", "integrity"),
    ("Line 26 uses current as a variable name, which shadows nothing but is 1 letter off a builtin.", "crash"),
    ("For 10000 SKUs, updating order.lines could be slow.", ""),
    ("The 3 lines that update order.lines are clear and correct.", "integrity"),
    # Authored: a consequence word does not rescue a story that only says what might happen.
    ("With 2 orders, order.lines may be hard to follow, so a new developer could misread it.", "integrity"),
    # ---- review of 1b91da89 (independent, 2026-10-07): a connective is not an outcome. Each of these passed the
    # connective rule ("so", "then", "still", "returns", "results in", "keeps", "never") as the top-severity
    # primary. Wording verbatim from the review's probes.
    ("The 3 lines that update order.lines are clear, so the logic is correct.", "integrity"),
    ("With 2 orders, order.lines still works exactly as intended.", "integrity"),
    ("With qty 2, cancel_line returns normally and order.lines is updated correctly.", "integrity"),
    ("Line 26 sets current then order.lines; this is fine for 1 or 2 SKUs.", "integrity"),
    ("For 10000 SKUs, updating order.lines takes a while, so it is slow.", ""),
    ("Calling cancel_line 10000 times results in 10000 dict writes to order.lines.", ""),
    ("With 50000 SKUs, order.lines keeps growing in memory.", "integrity"),
    ("One refactor would make order.lines cleaner, so 2 functions could share it.", "integrity"),
    ("With 2 orders, order.lines will be confusing, so developers misread it.", "integrity"),
    ("Possibly, with 2 orders, order.lines leaves things in an odd state.", "integrity"),
    ("Line 25 never uses a type hint on current, so 1 linter rule fails.", "crash"),
    # Authored for the v0.8 port: a contrast word does not rescue a story that only says what might happen.
    ("With 2 orders, order.lines might be slower than a list, but it works.", "integrity"),
]


@pytest.mark.parametrize("scenario, declared", NOT_A_BUG)
def test_a_story_with_a_number_and_a_name_but_no_wrong_outcome_is_not_promoted(scenario, declared):
    candidate = dict(PARTIAL_CANCEL, failure_scenario=scenario, harm_class=declared, title="not a bug")
    row = _as_survey_row(candidate)
    assert row["harm_class"] not in {"integrity", "crash"}, (scenario, row["harm_class"])
    defect, bare = _undeclared(candidate)
    assert REFUSED_AS_PRIMARY in defect, (scenario, bare["harm_class"])


# Concrete wrong outcomes on the same fixture, in plain words: each states what goes wrong, not that it might.
REAL_BUGS = [
    PARTIAL_CANCEL,
    {"title": "The last unit in stock can never be reserved", "file": "stock.py", "line_start": 17, "line_end": 17,
     "cited_line_text": "        return self.available(sku) > qty",
     "failure_scenario": "With 5 units available and qty 5, self.available(sku) > qty is False, so the last 5 "
                         "units can never be reserved and reserve() refuses an order that stock covers.",
     "harm_class": "integrity"},
    {"title": "Members get the coupon twice", "file": "pricing.py", "line_start": 35, "line_end": 35,
     "cited_line_text": "        discounted = apply_coupon(discounted, coupon_pct)",
     "failure_scenario": "A member with a 10% coupon on a 100.00 basket is charged as if the coupon were applied "
                         "twice: apply_coupon runs on the already-discounted amount, so the total comes out lower "
                         "than one coupon allows.",
     "harm_class": "integrity"},
    {"title": "Cancelling part of a line leaves stock reserved", "file": "orders.py", "line_start": 25, "line_end": 28,
     "cited_line_text": "    order.lines[sku] = current - qty",
     "failure_scenario": "cancel 1 of 4 then the other 3: after the first call stock.release is not called and "
                         "the 1 unit stays reserved; only the final call releases, and it releases current (3), "
                         "not 4.",
     "harm_class": "integrity"},
    # ---- review of 1b91da89: real bugs, wording verbatim from the review's probes. The connective rule refused
    # the negative stock ("yields") and the expected-versus-actual pair ("but should be"); 9adff83 and 6ad76ec7
    # both refused the boundary arrow, because `self.available` on the cited line did not ground `available`.
    {"title": "partial_becomes", "file": "orders.py", "line_start": 25, "line_end": 28,
     "cited_line_text": "    order.lines[sku] = current - qty",
     "failure_scenario": "Cancel 2 of 5: order.lines[sku] becomes 3 but stock.release is not called, so reserved "
                         "stays at 5 instead of 3.",
     "harm_class": "integrity"},
    {"title": "partial_equals", "file": "orders.py", "line_start": 25, "line_end": 28,
     "cited_line_text": "    order.lines[sku] = current - qty",
     "failure_scenario": "After cancel_line with qty 2 on a line of 5, stock.reserved equals 5 although only 3 "
                         "units are still ordered.",
     "harm_class": "integrity"},
    {"title": "ship_negative_yields", "file": "stock.py", "line_start": 27, "line_end": 29,
     "cited_line_text": "        self.on_hand[sku] = self.on_hand.get(sku, 0) - qty",
     "failure_scenario": "ship('A', 5) with on_hand 3 yields on_hand -2; nothing checks that qty <= on_hand.",
     "harm_class": "integrity"},
    {"title": "ship_release_wrong", "file": "stock.py", "line_start": 27, "line_end": 29,
     "cited_line_text": "        self.release(sku, qty)",
     "failure_scenario": "ship('A', 2) on a SKU that was never reserved releases 0 and still drops on_hand by 2, so "
                         "stock that was promised to another order is shipped.",
     "harm_class": "integrity"},
    {"title": "coupon_double_plain", "file": "pricing.py", "line_start": 32, "line_end": 35,
     "cited_line_text": "        discounted = apply_coupon(discounted, coupon_pct)",
     "failure_scenario": "total([Line('A', 100.0, 1)], coupon_pct=0.1, is_member=True) is 87.21 but should be "
                         "96.9 x 0.9: apply_coupon is applied a second time to discounted.",
     "harm_class": "integrity"},
    {"title": "can_reserve_boundary_arrow", "file": "stock.py", "line_start": 17, "line_end": 17,
     "cited_line_text": "        return self.available(sku) > qty",
     "failure_scenario": "can_reserve('A', 5) with available 5 -> False, should be True.",
     "harm_class": "integrity"},
]


@pytest.mark.parametrize("candidate", REAL_BUGS, ids=lambda c: c["title"])
def test_a_concrete_wrong_outcome_tied_to_the_cited_code_is_accepted_and_keeps_its_class(candidate):
    assert _finding_defect(dict(candidate), _evidence(), candidate["file"]) == ""
    assert _as_survey_row(candidate)["harm_class"] == "integrity", candidate["failure_scenario"]
    defect, bare = _undeclared(candidate)
    assert defect == "" and bare["harm_class"] == "integrity", (defect, candidate["failure_scenario"])
