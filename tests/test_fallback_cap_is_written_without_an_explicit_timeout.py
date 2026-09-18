"""A candidate with no explicit timeout still gets the budget cap written on its call.

Measured 2026-09-07 (fallback budget characterization, ten reds): the router only materialized the
per-candidate cap when it was LOWER than the manifest's existing timeout; a manifest that carried no
timeout at all kept none, so the adapter fell back to its own default (180 s) and one hanging candidate
could outlive the whole 60 s budget -- the exact hang the cap exists to bound. New scenarios, not the
characterization's: an explicit timeout above the budget is capped to it; one below is kept.
"""
from __future__ import annotations

import pytest

from tests.test_fallback_budget_characterization import BUDGET, FLOOR, _Scenario


def _with_timeout(scenario: _Scenario, index: int, seconds: float | None) -> None:
    manifest = scenario.manifests[index]
    config = dict(manifest.runtime_config or {})
    if seconds is None:
        config.pop("timeout_seconds", None)
    else:
        config["timeout_seconds"] = seconds
    scenario.manifests[index] = manifest.model_copy(update={"runtime_config": config})


def test_a_manifest_with_no_timeout_receives_the_whole_budget_as_its_cap():
    scenario = _Scenario(delays=[999.0])
    _with_timeout(scenario, 0, None)
    scenario.run()
    assert scenario.cap(0) == pytest.approx(BUDGET)


def test_an_explicit_timeout_above_the_budget_is_capped_to_the_budget_minus_the_reserve():
    scenario = _Scenario(delays=[999.0, None])
    _with_timeout(scenario, 0, 180.0)
    scenario.run()
    assert scenario.cap(0) == pytest.approx(BUDGET - FLOOR)
    assert scenario.was_attempted(1) is True


def test_an_explicit_timeout_below_the_budget_is_kept():
    scenario = _Scenario(delays=[999.0, None])
    _with_timeout(scenario, 0, 12.0)
    scenario.run()
    assert scenario.cap(0) == pytest.approx(12.0)
