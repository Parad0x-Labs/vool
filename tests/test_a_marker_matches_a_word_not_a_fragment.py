"""A live-lookup marker must match a WORD, not a fragment inside another word.

THE MEASURED DEFECT (2026-08-18, found by CI on `bfc06744`).

`_LIVE_LOOKUP_HINT_MARKERS` contains `browse`, and the test was `marker in lowered` — a bare
substring. The noun `browser` contains it:

    "create hive mind task: Task: Standalone VOOL browser integration."
        -> live_info_mode() == "fresh_lookup"

so the live-info lane claimed a hive-create turn.

WHY IT SURFACED WHEN IT DID, which is the part worth keeping. The false claim was already there and
was **harmless** for as long as every search provider failed: live-info claimed the turn, the
providers returned nothing, the lane declined, and the hive lane ran after it. `d7b73c22` added a
browser-backed provider that actually returns results — and the same false claim then produced an
answer and ended the turn, so the hive lane never ran.

Bisected: passes at `960c0240`, fails at `d7b73c22`. `live_info_mode` returns `fresh_lookup` at
**both**. Nothing about the detection changed; what changed is that the capability behind it started
working. A capability detector had been acting as an authority gate, and the bug was masked by the
capability being broken.

MEASURED BLAST of the repair: across 805 corpus prompts, claims lost = 0, claims gained = 0.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import _names_a_marker, live_info_mode

HIVE = "create hive mind task: Task: Standalone VOOL browser integration."


def test_the_measured_case_no_longer_claims_a_live_lookup() -> None:
    assert live_info_mode(None, HIVE, interpretation=None) == ""


@pytest.mark.parametrize(
    "text",
    (
        HIVE,
        "create hive mind task: Task: VOOL browser plugin.",
        "hive mind task: build the browser bridge.",
    ),
)
def test_a_marker_inside_a_longer_word_does_not_claim(text: str) -> None:
    """`browser` contains `browse`. Under the old substring test each of these returned
    `fresh_lookup`; under word boundaries none does."""
    assert live_info_mode(None, text, interpretation=None) == ""


def test_the_boundary_is_what_changes_these(monkeypatch) -> None:
    """Pinned as a differential so the case cannot silently start passing for another reason:
    with the substring test restored, the same text claims."""
    import core.agent_runtime.fast_live_info_mode_classifier as classifier

    monkeypatch.setattr(classifier, "_names_a_marker",
                        lambda text, markers: any(m in text for m in markers))
    assert classifier.live_info_mode(None, HIVE, interpretation=None) == "fresh_lookup"


# A SEPARATE, PRE-EXISTING OVER-CLAIM, recorded rather than fixed here.
#
# `looks_like_explicit_lookup_request` (core/task_router.py) claims bare phrases such as
# "the browser extension crashed" and "my browsers are all open" on its own, independently of these
# markers. That predicate is not touched by this change and needs its own blast measurement before
# anything is altered; noting it so the next reader does not mistake it for this defect returning.
@pytest.mark.parametrize(
    "text",
    ("the browser extension crashed", "my browsers are all open"),
)
def test_a_second_predicate_still_claims_these_and_that_is_known(text: str) -> None:
    from core.task_router import looks_like_explicit_lookup_request

    assert looks_like_explicit_lookup_request(text.lower()) is True
    assert live_info_mode(None, text, interpretation=None) == "fresh_lookup"


@pytest.mark.parametrize(
    "text",
    (
        "browse the web for the ECB rate decision",
        "what is the weather in Vilnius?",
        "what's the latest news on the rate decision",
        "get me the current bitcoin price",
    ),
)
def test_a_marker_used_as_a_word_still_claims(text: str) -> None:
    assert live_info_mode(None, text, interpretation=None) != ""


def test_multi_word_markers_still_match() -> None:
    """Boundaries are alphanumeric-only on purpose: a marker containing a space must still match,
    which a naive `\\b` word-boundary regex would break."""
    assert _names_a_marker("please look it up for me", ("look it up",)) is True
    assert _names_a_marker("how much is left", ("how much",)) is True


def test_a_marker_at_a_string_edge_still_matches() -> None:
    assert _names_a_marker("browse", ("browse",)) is True
    assert _names_a_marker("now browse", ("browse",)) is True
    assert _names_a_marker("browse now", ("browse",)) is True


def test_punctuation_is_a_boundary_but_letters_are_not() -> None:
    assert _names_a_marker("browse, then stop", ("browse",)) is True
    assert _names_a_marker("(browse)", ("browse",)) is True
    assert _names_a_marker("browser", ("browse",)) is False
    assert _names_a_marker("browse2", ("browse",)) is False


def test_sabotage_restoring_the_substring_match_reopens_the_false_claim(monkeypatch) -> None:
    """Revert to `marker in lowered` and the hive-create turn is claimed as a live lookup again."""
    import core.agent_runtime.fast_live_info_mode_classifier as classifier

    monkeypatch.setattr(classifier, "_names_a_marker",
                        lambda text, markers: any(m in text for m in markers))

    assert classifier.live_info_mode(None, HIVE, interpretation=None) == "fresh_lookup"


def test_sabotage_does_not_break_the_legitimate_claims(monkeypatch) -> None:
    """The sabotage above must reopen the FALSE claim without also disabling real ones, or it would
    pass by breaking the lane rather than by reverting the boundary."""
    import core.agent_runtime.fast_live_info_mode_classifier as classifier

    monkeypatch.setattr(classifier, "_names_a_marker",
                        lambda text, markers: any(m in text for m in markers))

    assert classifier.live_info_mode(None, "what is the weather in Vilnius?", interpretation=None) != ""
