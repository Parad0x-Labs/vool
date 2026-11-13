"""A survey row is shown High only when its story is grounded in the code it cites, never by words alone.

Real-model audit of 3.21, attempt 6 on 238d00ad (Mac, qwen3:8b, 2026-11-13). Survey rows are shown with no
challenge, and the harm vocabulary still backed their class on its own:
- pricing.py:16-18 "the subtotal function does not support line item adjustments ... which can lead to
  incorrect pricing" (a feature request) and pricing.py:37 "rounding at the end can lead to incorrect final
  prices due to floating-point precision issues" (a hunch) were served High;
- orders.py:23 went High on "raises an error", which describes the guard that is right;
- the real bugs reached High only through the same words, and the grounded rule backed 0 rows, because it
  needed a literal input and the model wrote none.

Grounding now also covers a story that says the state its cited lines write is left wrong, in the source's own
names. Every row below is verbatim from attempts 5 and 6.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import _finding_defect, _scenario_is_concrete_and_grounded

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"
SOURCES = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}


def _evidence() -> AuditEvidence:
    return AuditEvidence(inspected_paths=tuple(SOURCES), all_paths=tuple(SOURCES), sources=SOURCES,
                         workspace_root=str(FIXTURE), scoped_target="orders.py")


def _survey_class(path: str, start: int, end: int, title: str, scenario: str) -> str:
    row = {"title": title, "file": path, "line_start": start, "line_end": end,
           "cited_line_text": SOURCES[path].splitlines()[start - 1], "failure_scenario": scenario,
           "harm_class": "integrity"}
    _finding_defect(row, _evidence(), path, allow_observations=True)
    return row["harm_class"]


NOT_HIGH = [
    ("pricing.py", 16, 18, "No Support for Line Item Adjustments",
     "The subtotal function does not support line item adjustments, such as quantity changes or price "
     "modifications, which can lead to incorrect pricing."),
    ("pricing.py", 37, 37, "Rounding Errors in Final Calculation",
     "Rounding at the end can lead to incorrect final prices due to floating-point precision issues, especially "
     "when multiple operations are involved."),
    ("orders.py", 23, 23, "Cancel Line Does Not Handle Partial Cancellations",
     "The code raises an error if the canceled quantity exceeds the ordered quantity, but it does not handle "
     "partial cancellations correctly."),
    ("stock.py", 20, 21, "No Notification for Failed Reserve",
     "The reserve method raises an exception when not enough stock is available, but there is no mechanism to "
     "notify the caller of the failure in a non-exceptional way."),
    ("stock.py", 19, 21, "No Support for Multiple Reservations",
     "The reserve method does not support multiple reservations for the same SKU, which could lead to incorrect "
     "stock reservations."),
    ("pricing.py", 33, 34, "Member Discount Applied Incorrectly",
     "The member discount is applied after the coupon, which may not reflect the intended business logic of "
     "applying the discount before the coupon."),
    # Authored from attempt 6's business-logic hunch, worded against the state pricing.py:34 writes: naming the
    # written state does not turn an intent into a defect.
    ("pricing.py", 34, 34, "Member Rate Order",
     "The discounted amount is not adjusted to the intended business logic of applying the member rate first."),
    # Attempt 4: the clamp on the cited line refuses it, however it is worded.
    ("stock.py", 25, 25, "No Check for Negative Reserved Quantity",
     "The release method uses max(0, ...) to prevent negative reserved quantities, but this could lead to "
     "inconsistencies if the reserved quantity is not properly tracked."),
]

HIGH = [
    ("stock.py", 28, 29, "No Check for Reserved Stock in Ship Method",
     "The ship method does not check if the reserved stock is sufficient before reducing on_hand, which could "
     "lead to incorrect stock levels."),
    ("orders.py", 25, 25, "Cancel Line Does Not Reflect Updated Quantity",
     "The quantity is updated in the order lines, but the stock is not adjusted for the new quantity, leading to "
     "inconsistencies."),
    ("stock.py", 29, 29, "No Check for Negative On-Hand Quantity",
     "The ship method does not check if the on_hand quantity is sufficient before reducing it, which could lead "
     "to negative on-hand values."),
]


@pytest.mark.parametrize(("path", "start", "end", "title", "scenario"), NOT_HIGH, ids=[r[3] for r in NOT_HIGH])
def test_a_hunch_a_feature_request_or_a_correct_guard_is_not_high(path, start, end, title, scenario):
    assert _survey_class(path, start, end, title, scenario) not in {"integrity", "crash"}


@pytest.mark.parametrize(("path", "start", "end", "title", "scenario"), HIGH, ids=[r[3] for r in HIGH])
def test_a_real_bug_is_high_through_the_grounded_rule(path, start, end, title, scenario):
    assert _scenario_is_concrete_and_grounded(scenario, _evidence(), path, start, end)
    assert _survey_class(path, start, end, title, scenario) == "integrity"


def test_the_state_must_be_one_the_cited_lines_write():
    # The same story cited at the guard line (orders.py:23 writes nothing) is not grounded there.
    scenario = HIGH[1][4]
    assert not _scenario_is_concrete_and_grounded(scenario, _evidence(), "orders.py", 23, 23)
