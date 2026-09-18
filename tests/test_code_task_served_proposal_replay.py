"""Served proof that a revision under a reused proposal_id is refused and recovers (coding revision 3, P2).

Real daemon, real ``/api/chat``, the production ``/api/mode`` operator Allow and resume, real ``node``
checks and real bytes on disk. The MODEL IS SCRIPTED -- ``ObservedRepairModel`` from
``tests/test_code_task_served_units.py``, which documents exactly what it may read. Here its first repair
is wrong, and after the focused check fails it previews the corrected repair under the SAME proposal_id
it already used and that already ran. The runtime refuses that as ``proposal_id_conflict``; the tool loop
returns the refusal to the model inside the bounded correction budget; the model reads ``differences``
on the refused line and previews the revision under a new id, which is approved, lands and is verified.
The recorded proposal is never rewritten.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_code_task_served_boundary import _boot, served_factory  # noqa: F401  (served_factory is a fixture)
from tests.test_code_task_served_units import (
    TEMPERATURE_FIXED,
    TEMPERATURE_WRONG,
    WRONG_FIRST_FILES,
    ObservedRepairModel,
    _assert_completed,
    _converse,
    _effects,
    _verifications,
)

pytestmark = [pytest.mark.served]


def test_served_revision_under_a_reused_proposal_id_is_refused_and_lands_under_a_new_id(served_factory) -> None:  # noqa: F811
    model = ObservedRepairModel(
        repro="node check_all.js", owner="temperature.js", reads=["temperature.js"],
        repairs=[{"pid": "conversion", "path": "temperature.js", "content": TEMPERATURE_WRONG,
                  "rationale": "Owner temperature.js: the conversion factor is inverted."}],
        focused={"conversion": "node check_boiling.js", "conversion-2": "node check_boiling.js"},
        full="node check_all.js",
        recoveries=[{"on": "verification_failed",
                     "identify": {"path": "temperature.js", "reason": "the first repair still fails the boiling check"},
                     "reads": ["temperature.js"],
                     "repairs": [{"pid": "conversion", "reuse": True, "path": "temperature.js",
                                  "content": TEMPERATURE_FIXED,
                                  "rationale": "Owner temperature.js: multiply by 9/5 and add 32."}]}],
    )
    rig = _boot(served_factory, WRONG_FIRST_FILES, model)
    outcome = _converse(rig, "served-reused-proposal-id", "Fix the temperature bug in this project and explain the change.",
                        model)
    journal = _assert_completed(outcome, rig, model)
    workspace: Path = rig["workspace"]
    assert (workspace / "temperature.js").read_text() == TEMPERATURE_FIXED
    for name in ("check_all.js", "check_boiling.js", "package.json"):
        assert (workspace / name).read_text() == WRONG_FIRST_FILES[name], name
    assert "conflict:conversion->conversion-2" in model.log, model.log
    recorded, revision = journal["proposals"]["conversion"], journal["proposals"]["conversion-2"]
    assert recorded["arguments"]["content"] == TEMPERATURE_WRONG and recorded["consumed_by"]  # never rewritten
    assert revision["arguments"]["content"] == TEMPERATURE_FIXED and revision["consumed_by"]
    assert _verifications(rig["store_dir"]) == [
        ("narrow_test", "conversion", False, 1, True),
        ("narrow_test", "conversion-2", True, 2, True),
        ("cumulative", "conversion-2", True, 2, True),
    ], model.log
    assert _effects(rig["store_dir"]) == [
        ("run_tests:node check_all.js", "ok"),
        ("write_file:temperature.js", "ok"),
        ("run_tests:node check_boiling.js", "ok"),
        ("write_file:temperature.js", "ok"),
        ("run_tests:node check_boiling.js", "ok"),
        ("run_tests:node check_all.js", "ok"),
    ], model.log
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)
