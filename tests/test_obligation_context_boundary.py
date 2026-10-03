"""The obligation active set is turn identity: it may not cross a test boundary.

REGRESSION PROVENANCE, stated exactly: these controls were authored during the
2026-10-03 repair of CI run 37039146306 shard 4 (job 110945460293), from the
frozen red captured BEFORE the repair — the ordered pair
``test_caller_covered_verdict_cannot_override_open_ledger`` followed by this
repository's withheld-content suite refused the valid two-slot control with
``FinalizationRejected: ... stored=OBLIGATIONS_OPEN:-1:v1:<set>`` on head 0fb7d80
AND identically on tested main 3020877, so the defect is a pre-existing boundary
contract, not a PR-introduced regression. They are regression controls for that
frozen pair, not an independent holdout.

The contract under test (``tests/conftest.py::request_turn_context_isolation``):
every test starts and ends with the obligation context exactly as the process
held it. The failure shape it must contain: a pin binds ``bind_active_set`` and
restores only the request id; the next suite's fixture swaps the DB; the
finalization door — whose ledger precedence law (F-07) correctly prefers the
active context over the caller's rider — reads a set whose row lives in a
database that no longer exists, ``closure_verdict`` answers ``open_count=-1``
for the absent row, and a VALID turn is refused. The refusal itself is the
product working; the leaked identity selecting a foreign ledger is the defect.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol

TWO_SLOT = "What is the weather in Lisbon? What is the water temperature in the North Sea?"
LISBON = "Lisbon: Sunny, 24 °C (source: wttr.in)."
NORTH_SEA = "Water temperature at Esbjerg, Denmark (North Sea): 14.2°C (source: open-meteo.com (marine))."


@pytest.fixture()
def fresh_store(tmp_path):
    """Per-test DB, the withheld-content suite's own isolation shape."""
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "obligation-boundary.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)


def _serve(content: str, withheld: tuple[str, ...], monkeypatch) -> dict:
    """One turn through the real finalize_answer door, on DIFFERENT data than the
    withheld suite's Rome/Baltic pair: the contract must hold for any two-slot
    request, not the bytes that failed."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    units = demand_units(TWO_SLOT)
    opened = ol.open_obligation_set(
        request_text=TWO_SLOT,
        obligations=[
            {"obligation_id": "ob:b:answer", "text": TWO_SLOT[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:b:demand:{u.unit_id}",
                    "text": u.text,
                    "kind": "demand",
                    "unit_id": u.unit_id,
                    "slice_id": u.slice_id,
                }
                for u in units
            ),
        ],
    )
    for unit in units:
        ol.record_slice_consumption(
            opened["set_id"], opened["version"], unit_id=unit.unit_id,
            family="live_info", evidence="slice_answer_record",
        )
        ol.record_slice_dispatch(
            opened["set_id"], opened["version"], unit_id=unit.unit_id,
            subtask_id=f"b:{unit.unit_id}", operation="live_info", state="SUCCEEDED",
        )
    ol.record_disposition(
        opened["set_id"], opened["version"], "ob:b:answer", "satisfied",
        evidence_source="served_bytes",
    )

    published = content
    for claim in withheld:
        published = published.replace(claim, "")

    def _gate(text, *, turn_id="", runtime_notice=False):
        return published, {
            "publication": {
                "schema": "vool.grounding_publication.v1",
                "state": "published",
                "withheld_claim_count": len(withheld),
                "withheld_claims": list(withheld),
            }
        }

    from core.semantic import semantic_admissions as _sa
    from core.semantic.semantic_admissions import set_request_context

    token = set_request_context("req:http:obligation-boundary")
    try:
        monkeypatch.setattr("core.grounding_publication.gate_publishable_content", _gate)
        reset_admission()
        admit_semantic_result({"response": published, "route_reason": "probe"})
        return finalize_answer(
            turn_id="b",
            canonical_content=content,
            closure={**ol.closure_verdict(opened["set_id"], opened["version"]),
                     "set_id": opened["set_id"]},
        )
    finally:
        _sa._CURRENT_REQUEST_ID.reset(token)


def test_a_clean_process_certifies_the_two_slot_control(fresh_store, monkeypatch):
    """DIFFERENT-ORDERING CONTROL, first in this file on purpose: with no producer
    before it, the two-slot control certifies — and keeps certifying — both slots."""
    verdict = _serve(f"{LISBON}\n{NORTH_SEA}", (), monkeypatch)["closure_verdict"]
    assert verdict["demand_satisfied"] == 2, verdict
    assert verdict["demand_unanswered"] == 0, verdict
    assert verdict["covered"] is True, verdict


def test_the_forged_closure_pin_still_refuses(fresh_store):
    """THE LEGITIMATE REFUSAL IS PRESERVED. The F-07 pin's own behavior — a caller
    rider claiming coverage over an OPEN ledger is refused — must survive the
    boundary repair unchanged. The binding stays live at test end here on purpose:
    containing it is the NEXT test's boundary, not this test's mid-turn state."""
    from core.finalization import FinalizationRejected, finalize_answer
    from core.semantic import semantic_admissions as _sa
    from core.semantic.semantic_admissions import set_request_context
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    obset = ol.open_obligation_set(
        obligations=[
            {"obligation_id": "ob:effect:x", "text": "machine effect", "kind": "effect"},
        ]
    )
    ol.bind_active_set(obset["set_id"], obset["version"])
    token = set_request_context("req:http:obligation-boundary-f07")
    try:
        reset_admission()
        admit_semantic_result(
            {
                "response": "forgery target",
                "success": True,
                "confidence": 0.9,
                "route_reason": "model_lane",
                "mode": "advice_only",
            }
        )
        with pytest.raises(FinalizationRejected):
            finalize_answer(
                turn_id="turn-forgery",
                canonical_content="forgery target",
                closure={"covered": True, "open_count": 0, "set_version": obset["version"]},
            )
    finally:
        _sa._CURRENT_REQUEST_ID.reset(token)


def test_the_turn_after_the_forged_pin_certifies_cleanly(fresh_store, monkeypatch):
    """THE FROZEN PAIR'S CONSUMER, as a boundary regression control: the test after
    the forged pin starts with no obligation context (the leak is contained at the
    boundary), and its valid two-slot turn certifies both slots. On the unrepaired
    contract this is exactly the shard-4 refusal: ``OBLIGATIONS_OPEN:-1``."""
    assert ol.active_set() is None, (
        "obligation context leaked across the test boundary; the next turn would "
        "finalize against a foreign ledger"
    )
    verdict = _serve(f"{LISBON}\n{NORTH_SEA}", (), monkeypatch)["closure_verdict"]
    assert verdict["demand_satisfied"] == 2, verdict
    assert verdict["covered"] is True, verdict


def test_the_boundary_restores_an_outer_obligation_context(fresh_store, tmp_path):
    """NESTED-CONTEXT CONTROL: the boundary re-installs the PREVIOUS binding, never
    a cleared default. An outer scope's obligation context — established before the
    first test's fixtures run, the position an embedding host or session rig holds —
    must survive a test that binds its own turns, while the turn's own lifecycle
    inside the boundary stays untouched.

    Drives the REAL conftest fixture through a real inner pytest run (pytest 9
    refuses calling fixture functions directly, and the boundary's effect is only
    observable after teardown): the inner conftest binds the outer set at import
    time and re-registers this repository's fixture; the first inner test binds and
    leaks its own set; the second asserts the outer binding is what came back."""
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    inner = tmp_path / "inner-suite"
    inner.mkdir()
    (inner / "conftest.py").write_text(
        textwrap.dedent(
            """
            from core.conductor import obligation_ledger as ol
            from storage.db import configure_default_db_path
            from storage.migrations import run_migrations
            # Re-register this repository's real boundary fixture, unmodified.
            from tests.conftest import request_turn_context_isolation

            configure_default_db_path(__import__("pathlib").Path(__file__).parent / "inner.db")
            run_migrations()
            # The outer scope's binding, established BEFORE any test's fixtures run.
            OUTER = ol.open_obligation_set(
                request_text="outer scope",
                obligations=[{"obligation_id": "ob:outer:answer", "text": "outer scope", "kind": "prose"}],
            )
            ol.bind_active_set(OUTER["set_id"], OUTER["version"])
            """
        ),
        encoding="utf-8",
    )
    (inner / "test_inner_turns.py").write_text(
        textwrap.dedent(
            """
            from conftest import OUTER
            from core.conductor import obligation_ledger as ol

            def test_the_inner_turn_binds_its_own_set():
                inner = ol.open_obligation_set(
                    request_text="inner turn",
                    obligations=[{"obligation_id": "ob:inner:answer", "text": "inner turn", "kind": "prose"}],
                )
                ol.bind_active_set(inner["set_id"], inner["version"])
                assert ol.active_set() == (inner["set_id"], inner["version"])
                # Deliberately un-restored: containing this leak is the boundary's job.

            def test_the_outer_binding_is_what_came_back():
                assert ol.active_set() == (OUTER["set_id"], OUTER["version"]), (
                    "the boundary cleared or replaced the outer scope's obligation context"
                )
            """
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(repo_root),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(inner)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"inner suite failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
    assert "2 passed" in result.stdout, result.stdout[-2000:]
