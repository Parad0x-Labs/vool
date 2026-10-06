"""Question action frames must not masquerade as absent source attributes."""
from __future__ import annotations

import pytest

from tests.lifecycle.test_grant_capsule_recall_20260929 import (
    _capsule,
    _store,
)
from tests.lifecycle.test_grant_capsule_recall_20260929 import (
    fresh_profile as fresh_profile,
)


@pytest.mark.parametrize("departure", ["head out", "set out", "set off", "go out"])
def test_departure_advice_frame_keeps_actual_topic_evidence(fresh_profile, departure):
    home = fresh_profile
    _store(home, "departure", "I want to catch the meteor shower before sunrise.",
           "The pre-dawn window is the one to aim for.")
    capsule = _capsule(home, "departure", f"When should we {departure} for the meteor shower?")
    assert "meteor shower before sunrise" in capsule
    assert "user said" in capsule


def test_departure_frame_keeps_unknown_real_attributes_absent(fresh_profile):
    home = fresh_profile
    _store(home, "departure", "I want to catch the meteor shower before sunrise.",
           "The pre-dawn window is the one to aim for.")
    capsule = _capsule(home, "departure",
                       "When should we head out for meteor shower humidity and particle pollution measurements?")
    assert not capsule


def test_head_noun_is_not_a_departure_frame(fresh_profile):
    home = fresh_profile
    _store(home, "sculpture", "The sculpture base has a blue mosaic.", "The base is blue.")
    capsule = _capsule(home, "sculpture", "What is the sculpture head repair cost?")
    assert not capsule


def test_head_noun_supported_value_still_reaches_capsule(fresh_profile):
    home = fresh_profile
    _store(home, "sculpture", "The sculpture head repair cost is 95 euros.", "Noted.")
    capsule = _capsule(home, "sculpture", "What is the sculpture head repair cost?")
    assert "95 euros" in capsule
