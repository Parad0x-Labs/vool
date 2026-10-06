"""Same-subject supersession law (q90-binding-closure, 2026-09-29).

Recency supersession requires ONE subject. Parallel template facts — each
side carrying a distinctive non-reassignment subject token — coexist unless
the winner carries an undo/correction marker. Regression protection for the
sealed classes F16-05 (rota slots) and F10-12 (restated parking rule), and
for the supersession ladder the law must NOT relax (badge seasons, keypad
restate, ferry corrections).
"""
from core.temporal_selection import _conflicts_with_winner


def test_parallel_template_facts_coexist_under_pure_recency() -> None:
    # F16-05 class: 12 same-shape rota notes, distinct shift names
    assert not _conflicts_with_winner(
        "Rota note 1: the beacon shift runs 07:00.",
        "Rota note 11: the lighthouse shift runs 17:00.",
    )
    assert not _conflicts_with_winner(
        "Rota note 4: the estuary shift runs 10:00.",
        "Rota note 10: the keel shift runs 16:00.",
    )


def test_restatement_does_not_supersede_its_original() -> None:
    # F10-12 class: "to repeat" restates the same rule; both statements are
    # true and the capsule needs both value forms (17:00 and five)
    assert not _conflicts_with_winner(
        "Reminder one: parking is free after 17:00.",
        "Parking after five is free, to repeat.",
    )


def test_marker_corrections_still_supersede() -> None:
    assert _conflicts_with_winner(
        "Bottling day at the meadery is the 14th.",
        "Scratch that — bottling moves to the 21st.",
    )
    assert _conflicts_with_winner(
        "Ski wax service at the cabin costs 18 francs.",
        "One more change: wax service is 27 francs from now.",
    )
    assert _conflicts_with_winner(
        "The ferry departs at 23:10.",
        "The ferry now departs at 23:40.",
    )


def test_value_ladder_still_supersedes() -> None:
    # wording drift must not read a price ladder as parallel subjects
    assert _conflicts_with_winner(
        "Crane operator badge renewal costs 40 euro.",
        "Badge renewal rises to 55 euro for the 2025 season.",
    )
    assert _conflicts_with_winner(
        "Ski wax service at the cabin costs 18 francs.",
        "Wax service goes to 24 francs for the new season.",
    )


def test_hyphenated_codes_are_values_not_subjects() -> None:
    # keypad restate (F14-11 class): the code tokens are value material;
    # the ORIGINAL code must not coexist with the new one
    assert _conflicts_with_winner(
        "The observatory keypad code is 88-110.",
        "The new keypad code is 92-214.",
    )


def test_ownership_transfer_is_parallel_not_supersession() -> None:
    # F01-13 class: "9 belongs to Mai" assigns a DIFFERENT subject's locker;
    # recency must not treat it as a competing value of MY locker
    assert not _conflicts_with_winner(
        "My courier co-op locker is number 9.",
        "Make that locker 6 — 9 belongs to Mai.",
    )
