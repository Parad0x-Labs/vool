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


# ---- real-model audit attempt 3 (qwen3:8b through /api/chat, 2026-11-12, e706d5dc): the rule backed a story
# the cited line contradicts and refused the real bug every time it was found. Wording verbatim from the rows.
SHIP_NEGATIVE_BASE = {"title": "", "file": "stock.py", "line_start": 29, "line_end": 29,
                      "cited_line_text": "        self.on_hand[sku] = self.on_hand.get(sku, 0) - qty",
                      "harm_class": "integrity"}
CONTRADICTED_BY_ITS_LINE = [
    # stock.py:25 is `self.reserved[sku] = max(0, self.reserved[sku] - qty)`: reserved cannot go negative. The
    # story's only "input" was the 0 it copied from that max(0, ...), and "but" read as the wrong outcome.
    {"title": "No Check for Negative Reserved Quantity", "file": "stock.py", "line_start": 25, "line_end": 25,
     "cited_line_text": "        self.reserved[sku] = max(0, self.reserved[sku] - qty)",
     "failure_scenario": "The release method uses max(0, ...) to prevent negative reserved quantities, but this does "
                         "not account for cases where the reserved quantity is less than the qty being released, "
                         "leading to potential negative values.",
     "harm_class": "integrity"},
    # Authored: the same claim with a concrete input still contradicts the clamp on its line.
    {"title": "release goes negative", "file": "stock.py", "line_start": 25, "line_end": 25,
     "cited_line_text": "        self.reserved[sku] = max(0, self.reserved[sku] - qty)",
     "failure_scenario": "release('A', 5) with reserved 3 leaves reserved at -2 instead of 0.",
     "harm_class": "integrity"},
    # stock.py:20 calls can_reserve, which refuses qty <= 0; the story says nothing stops a negative qty.
    {"title": "No Validation for Negative Qty in Reserve", "file": "stock.py", "line_start": 20, "line_end": 20,
     "cited_line_text": "        if not self.can_reserve(sku, qty):",
     "failure_scenario": "The reserve method calls can_reserve, which checks if qty is positive, but does not "
                         "validate that qty is not negative, leading to potential negative reserved quantities.",
     "harm_class": "integrity"},
]


# Authored: a literal copied out of the cited code is the source speaking, not an input the story gives.
COPIED_LITERAL = [
    "The line calls self.on_hand.get(sku, 0) - qty, but nothing logs the change.",
    "on_hand.get(sku, 0) is used here, but the default should be documented.",
]


@pytest.mark.parametrize("scenario", COPIED_LITERAL)
def test_a_literal_copied_from_the_cited_code_is_not_an_input(scenario):
    candidate = dict(SHIP_NEGATIVE_BASE, failure_scenario=scenario, title="not a bug")
    assert _as_survey_row(candidate)["harm_class"] not in {"integrity", "crash"}, scenario
    defect, _ = _undeclared(candidate)
    assert REFUSED_AS_PRIMARY in defect, scenario


@pytest.mark.parametrize("candidate", CONTRADICTED_BY_ITS_LINE, ids=lambda c: c["title"])
def test_a_story_its_own_cited_line_contradicts_is_not_promoted(candidate):
    assert _as_survey_row(candidate)["harm_class"] not in {"integrity", "crash"}, candidate["failure_scenario"]
    defect, bare = _undeclared(candidate)
    assert REFUSED_AS_PRIMARY in defect, (bare["harm_class"], candidate["failure_scenario"])


SHIP_NEGATIVE = dict(SHIP_NEGATIVE_BASE, title="No Check for Negative on_hand")

# stock.py:29 lets ship drive on_hand below zero. The model found it in both builds and words it as a condition on
# the code's own names with a hedged bad value; the rule wanted a number and read "could become" as speculation.
SHIP_NEGATIVE_AS_FOUND = [
    "If qty exceeds the available stock, on_hand could become negative, which is not valid.",
    "If qty exceeds the available quantity, on_hand could become negative, which is invalid.",
    "If the qty exceeds the available stock, the on_hand could become negative, which is invalid. The code does "
    "not check for this scenario.",
    # Authored: other comparisons a model writes for the same bug.
    "When qty is greater than on_hand, on_hand ends up below zero.",
    "If qty > on_hand[sku], ship leaves on_hand negative.",
]


@pytest.mark.parametrize("scenario", SHIP_NEGATIVE_AS_FOUND)
def test_the_real_ship_bug_in_the_models_words_keeps_its_class(scenario):
    candidate = dict(SHIP_NEGATIVE, failure_scenario=scenario)
    assert _as_survey_row(candidate)["harm_class"] == "integrity", scenario
    defect, bare = _undeclared(candidate)
    assert defect == "" and bare["harm_class"] == "integrity", (defect, scenario)


# A condition on the code's names is an input, but it still needs a bad value, and a hedge on a quality is still
# only a hunch. None of these may be promoted.
CONDITION_WITHOUT_A_BAD_VALUE = [
    "If qty exceeds the available stock, ship could be slow.",
    "If qty is greater than on_hand, the code may be hard to read.",
    "When qty exceeds on_hand, on_hand is updated correctly.",
    "If qty is negative, on_hand might be confusing to read.",
    "If qty exceeds the stock, ship might take longer to run.",
]


@pytest.mark.parametrize("scenario", CONDITION_WITHOUT_A_BAD_VALUE)
def test_a_condition_without_a_bad_value_is_not_promoted(scenario):
    candidate = dict(SHIP_NEGATIVE, failure_scenario=scenario, title="not a bug")
    assert _as_survey_row(candidate)["harm_class"] not in {"integrity", "crash"}, scenario
    defect, _ = _undeclared(candidate)
    assert REFUSED_AS_PRIMARY in defect, scenario


# ---- real-model audit attempt 4 (qwen3:8b through /api/chat, 2026-11-13, 6c030f6a): stock.py:20 was served High
# through the harm vocabulary ("incorrect") and the new rule both, though reserve() refuses exactly that input:
# can_reserve, which line 20 calls, returns False for qty <= 0 and reserve raises. Words may not back a claim
# whose condition the cited code, or the function it calls, already refuses. Wording verbatim from the row.
GUARDED_BY_THE_CODE = [
    {"title": "No Validation for Negative qty in Reserve", "file": "stock.py", "line_start": 20, "line_end": 20,
     "cited_line_text": "        if not self.can_reserve(sku, qty):",
     "failure_scenario": "The can_reserve method checks if qty is positive, but if qty is negative, it could lead "
                         "to incorrect reservations. The code does not handle negative quantities properly.",
     "harm_class": "integrity"},
    # Authored: the same claim on the guard's own lines, and the guard of orders.py's cancel_line.
    {"title": "can_reserve accepts zero", "file": "stock.py", "line_start": 15, "line_end": 17,
     "cited_line_text": "        if qty <= 0:",
     "failure_scenario": "If qty is zero, can_reserve returns True and the recorded reservation is wrong.",
     "harm_class": "integrity"},
    {"title": "cancel more than ordered", "file": "orders.py", "line_start": 23, "line_end": 25,
     "cited_line_text": "    if qty > current:",
     "failure_scenario": "If qty exceeds current, order.lines[sku] becomes negative and the order is corrupted.",
     "harm_class": "integrity"},
]


@pytest.mark.parametrize("candidate", GUARDED_BY_THE_CODE, ids=lambda c: c["title"])
def test_words_do_not_back_a_claim_the_cited_code_already_refuses(candidate):
    assert _as_survey_row(candidate)["harm_class"] not in {"integrity", "crash"}, candidate["failure_scenario"]
    defect, bare = _undeclared(candidate)
    assert REFUSED_AS_PRIMARY in defect, (bare["harm_class"], candidate["failure_scenario"])


# The same words still back real bugs whose condition nothing refuses: ship() calls release(), which clamps
# reserved but guards nothing, and the harm vocabulary keeps its say there.
UNGUARDED_REAL_BUGS = [
    "If the on_hand is already zero and qty is positive, this line could result in a negative on_hand value, which "
    "is invalid.",
    "If the quantity shipped exceeds the available stock, the on_hand value could become negative, which is "
    "incorrect. The code does not validate this scenario.",
]


@pytest.mark.parametrize("scenario", UNGUARDED_REAL_BUGS)
def test_an_unguarded_real_bug_keeps_its_class(scenario):
    candidate = dict(SHIP_NEGATIVE, failure_scenario=scenario)
    assert _as_survey_row(candidate)["harm_class"] == "integrity", scenario
    defect, bare = _undeclared(candidate)
    assert defect == "" and bare["harm_class"] == "integrity", (defect, scenario)
