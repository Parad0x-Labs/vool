"""An audit turn's own reads are stamped with the turn that read them, so this turn's readers see them.

`_register_audit_reads` registered the stepped audit's file reads as execution records with no source
context, so every one of them carried turn_id "" (UNATTRIBUTED by `core.execution_records`, never "the
current turn"). The turn-scoped readers (`records_for_turn`, the grounding harvest, the inspection
honesty gate that judges this turn against this turn's records) therefore never saw the audit's reads.
A resumed-capsule audit did not read this turn and stamps nothing. Pack 2b item 07, with its test.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core import execution_records
from core.agent_runtime.stepped_audit import _register_audit_reads


@pytest.fixture()
def session(monkeypatch):
    monkeypatch.setenv("VOOL_EXECUTION_RECORDS", "1")
    session_id = f"audit-stamp-{uuid.uuid4().hex[:8]}"
    yield session_id
    execution_records.clear(session_id)


def _evidence(*paths: str):
    return SimpleNamespace(sources=list(paths))


def test_an_audit_turns_reads_carry_the_turn_id_and_reach_the_turns_readers(session):
    _register_audit_reads(_evidence("pricing.py", "stock.py"), session, {"session_id": session, "turn_id": "turn-audit-1"})
    mine = execution_records.records_for_turn(session, "turn-audit-1")
    assert sorted(str(r.arguments.get("path")) for r in mine) == ["pricing.py", "stock.py"], mine
    assert all(r.intent == "workspace.read_file" and r.ok for r in mine), mine
    assert execution_records.unattributed_count(session) == 0


def test_a_resumed_capsule_audit_stamps_nothing_on_this_turn(session):
    _register_audit_reads(_evidence("pricing.py"), session, None)
    assert execution_records.records_for_turn(session, "turn-audit-2") == ()
    assert any(str(r.arguments.get("path")) == "pricing.py" for r in execution_records.records_for(session))
