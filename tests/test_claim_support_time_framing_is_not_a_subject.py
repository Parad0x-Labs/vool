"""Time framing ("Right now", "Today", "Now") is not a claim subject.

Measured on the served daemon: "Search my workspace files for the launch date and tell me what it is
right now." ran `workspace.search_text`, which returned "The launch date is 14 March". The answer
"Right now beta.md says the launch date is 14 March." was refused as `subject_absent_from_source`.
Sentence position capitalised "Right", the request supplied "right", and no source says "right".

The subject check itself must stay as strong as it was. That matters most for a name the user
typed in lowercase ("vilnius"), which only the subject check ties to the source. The falsifiers
below fail if the framing exemption leaks into real subjects.
"""
from __future__ import annotations

import pytest

from core.claim_support import match_claims

LAUNCH_SOURCE = [{"summary": "- beta.md:2 The launch date is 14 March and the owner is the ops team.",
                  "origin_domain": "workspace.search_text"}]
KAUNAS_SOURCE = [{"summary": "Kaunas has a population of 580,000 people as of 2026.", "origin_domain": "stats.example"}]
VILNIUS_SOURCE = [{"summary": "Vilnius has a population of 580,000 people as of 2026.", "origin_domain": "stats.example"}]


def _status(answer: str, notes: list[dict], request: str) -> tuple[str, tuple[str, ...]]:
    claim_map = match_claims(answer=answer, notes=notes, request_text=request)
    [claim] = claim_map.as_dict()["claims"]
    return claim["status"], tuple(claim["reasons"])


@pytest.mark.parametrize(
    ("request_text", "answer"),
    [
        ("What is the launch date in my workspace right now?", "Right now beta.md says the launch date is 14 March."),
        ("What is the launch date in my workspace right now?", "Right now the launch date is 14 March."),
        ("launch date now?", "Now the launch date is 14 March."),
        ("launch date today?", "Today the launch date is 14 March."),
        ("launch date at the moment?", "At the moment the launch date is 14 March."),
    ],
)
def test_a_claim_opening_with_time_framing_is_supported_by_its_source(request_text: str, answer: str) -> None:
    status, reasons = _status(answer, LAUNCH_SOURCE, request_text)
    assert status == "supported", reasons


def test_an_invented_value_behind_time_framing_is_still_refused() -> None:
    status, _ = _status("Right now the launch date is 3 April.", LAUNCH_SOURCE, "launch date right now?")
    assert status == "unsupported"


def test_an_invented_subject_behind_time_framing_is_still_refused() -> None:
    status, _ = _status("Right now Dana Whitfield owns the launch.", LAUNCH_SOURCE, "who owns the launch right now?")
    assert status == "unsupported"


@pytest.mark.parametrize(
    "answer",
    [
        "Vilnius has 580,000 people right now.",
        "Right now Vilnius has 580,000 people.",
        "Today Vilnius has 580,000 people.",
    ],
)
def test_a_lowercase_typed_city_is_still_bound_to_the_source_that_names_it(answer: str) -> None:
    """Falsifier: the request names "vilnius" in lowercase; the only source is about Kaunas with the
    same number. A claim about Vilnius must not borrow Kaunas's figure, framed or not."""
    status, reasons = _status(answer, KAUNAS_SOURCE, "population of vilnius right now")
    assert status == "unsupported", reasons
    assert "subject_absent_from_source" in reasons


def test_an_answer_naming_a_different_city_than_its_source_is_refused() -> None:
    status, _ = _status("Right now Kaunas has 580,000 people.", VILNIUS_SOURCE, "population of vilnius right now")
    assert status == "unsupported"
