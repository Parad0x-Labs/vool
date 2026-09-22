"""Residue-2: tool-loop effect obligations are load-bearing.

A reserved mutating effect joins the turn's active obligation set at RESERVE
time and is discharged ONLY from A6 reconciled evidence; an unknown outcome
stays open and blocks finalization. Driven through the REAL executor path
(execute_tool_intent with a stubbed runtime tool handler).
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol


@pytest.fixture()
def fresh_store(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    reset_mode_permission_state()
    sdb.configure_default_db_path(tmp_path / "res2.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    # Enable a real mutating runtime-lane intent for the driven path.
    monkeypatch.setattr(
        "core.policy_engine.get",
        lambda key, default=None: True if key == "email.send_enabled" else default,
    )
    yield
    reset_mode_permission_state()
    ol.clear_active_set()
    sdb.configure_default_db_path(None)


def _bind_turn_set():
    obset = ol.open_obligation_set(
        obligations=[{"obligation_id": "ob:answer", "text": "t", "kind": "prose"}]
    )
    ol.bind_active_set(obset["set_id"], obset["version"])
    return obset


def _approved_context(payload):
    from core.mode_permission_policy import decide_tool_call, resolve_approval

    context = {"runtime_session_id": "s1", "operating_mode": "manual"}
    decision = decide_tool_call(**payload, task_id="t1", source_context=context)
    assert decision.approval_request is not None, decision
    token = decision.approval_request["approval_id"]
    assert resolve_approval(token, decision="allow")
    return {**context, "mode_approval_token": token}


def _drive_email_send(monkeypatch, *, ok: bool):
    """Drive the REAL execute_tool_intent runtime lane with a stubbed handler."""
    from types import SimpleNamespace

    import core.tool_intent_executor as tie

    _exec = SimpleNamespace(
        handled=True,
        ok=ok,
        status="tool_executed" if ok else "tool_failed",
        response_text="stub",
        details={},
    )

    calls = []
    def fake_runtime_tool(intent, arguments, **kw):
        calls.append(intent)
        return _exec

    monkeypatch.setattr(tie, "execute_authorized_runtime_tool", fake_runtime_tool)
    payload = {
        "intent": "email.send",
        "arguments": {"to": "a@b.c", "subject": "s", "body": "b"},
    }
    execution = tie.execute_tool_intent(
        payload,
        task_id="t1",
        session_id="s1",
        source_context=_approved_context(payload),
        hive_activity_tracker=None,
        public_hive_bridge=None,
        checkpoint_id="cp-1",
    )
    assert calls == ["email.send"], execution
    return execution


def test_effect_registered_at_reserve_and_discharged_on_a6_evidence(fresh_store, monkeypatch):
    _bind_turn_set()
    execution = _drive_email_send(monkeypatch, ok=True)
    assert execution is not None
    sid, ver = ol.active_set()
    verdict = ol.closure_verdict(sid, ver)
    assert verdict["open_count"] == 1, verdict  # only the prose obligation remains
    # RED MUTATION target (delete the A6-evidence discharge call): the effect
    # obligation would still be planned here.
    ol.record_disposition(
        sid, ver, "ob:answer", "satisfied", evidence_source="served_bytes"
    )
    final_verdict = ol.closure_verdict(sid, ver)
    assert final_verdict["covered"] is True, final_verdict


def test_unknown_outcome_keeps_effect_open_blocks_closure(fresh_store, monkeypatch):
    _bind_turn_set()

    import core.tool_intent_executor as tie

    calls = []
    def fake_runtime_tool(intent, arguments, **kw):
        calls.append(intent)
        raise tie.EffectOutcomeUnknown(reason="transport_timeout", detail="d")

    monkeypatch.setattr(tie, "execute_authorized_runtime_tool", fake_runtime_tool)
    payload = {
        "intent": "email.send",
        "arguments": {"to": "a@b.c", "subject": "s", "body": "b"},
    }
    execution = tie.execute_tool_intent(
        payload,
        task_id="t1",
        session_id="s1",
        source_context=_approved_context(payload),
        hive_activity_tracker=None,
        public_hive_bridge=None,
        checkpoint_id="cp-1",
    )
    assert calls == ["email.send"], execution
    sid, ver = ol.active_set()
    verdict = ol.closure_verdict(sid, ver)
    assert verdict["open_count"] >= 1, "UNKNOWN must stay open (blocks closure)"
    # Even with the prose served, UNKNOWN keeps the set structurally open.
    ol.record_disposition(
        sid, ver, "ob:answer", "satisfied", evidence_source="served_bytes"
    )
    assert ol.closure_verdict(sid, ver)["covered"] is False
