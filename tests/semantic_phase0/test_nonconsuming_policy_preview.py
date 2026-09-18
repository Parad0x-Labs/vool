"""A preview of the permission decision CONSUMES nothing; the real decision consumes exactly once.

Drives the REAL engine (`core.mode_permission_policy`): a manual-mode write that requires approval,
an approval token minted by `resolve_approval`, and the same call previewed N times versus decided
once. Every store the engine can spend from is snapshotted around the preview and must be
byte-identical after it: the approval token, the task approvals, the persisted pending-approval
mirror, approval events. Then the admission preview: same laws, never a grant. A sabotage control
routes the preview through the consuming decision and proves the test bites.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core import mode_permission_policy as mpp
from core.mode_permission_policy import (
    PermissionEffect,
    decide_tool_call,
    preview_tool_call,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
)
from core.semantic import types as semantic_types
from core.semantic.admission import admit, preview_admission
from core.semantic.canonical_text import CanonicalText
from core.semantic.types import IntentProposal, ReasonCode


@pytest.fixture(autouse=True)
def _clean_mode_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _context(tmp_path, session: str = "chat-a", mode: str = "manual", **extra):
    set_active_mode(session, mode, project_id="", client_turn_id=str(extra.get("cancel_turn_id") or "turn-a"))
    return {"runtime_session_id": session, "operating_mode": mode, "workspace_root": str(tmp_path), **extra}


def _snapshot() -> dict:
    with mpp._LOCK:
        approvals = json.dumps(mpp._APPROVALS, sort_keys=True, default=str)
        task_approvals = json.dumps(mpp._TASK_APPROVALS, sort_keys=True, default=str)
    path = mpp._pending_approvals_path()
    mirror = pathlib.Path(path).read_text() if path is not None and pathlib.Path(path).exists() else ""
    return {"approvals": approvals, "task_approvals": task_approvals, "mirror": mirror,
            "grants": len(list(semantic_types._ISSUED_GRANTS))}


WRITE = "workspace.write_file"
ARGS = {"path": "a.txt", "content": "one"}


def test_preview_reports_what_the_decision_would_do_and_spends_nothing(tmp_path) -> None:
    ctx = _context(tmp_path)
    before = _snapshot()
    for _ in range(5):
        preview = preview_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=ctx)
        assert preview.effect is PermissionEffect.REQUIRE_APPROVAL
        assert preview.would_request_approval is True and preview.would_consume_token is False
        assert not preview.allowed
    assert _snapshot() == before, "a preview created or persisted an approval request"
    # The real decision DOES create the request: that is the one consuming point.
    decision = decide_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=ctx)
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL and decision.approval_request is not None
    assert _snapshot() != before


def test_preview_sees_a_matching_token_but_leaves_it_unspent(tmp_path) -> None:
    ctx = _context(tmp_path)
    request = decide_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow")
    approved = {**ctx, "mode_approval_token": request["approval_id"]}
    before = _snapshot()
    for _ in range(5):
        preview = preview_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=approved)
        assert preview.effect is PermissionEffect.ALLOW and preview.would_consume_token is True
    assert _snapshot() == before, "a preview spent the token"
    # Exactly one consumption, at the real decision, and the second real decision no longer allows.
    assert decide_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=approved).effect is PermissionEffect.ALLOW
    assert decide_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=approved).effect is PermissionEffect.REQUIRE_APPROVAL


def test_preview_and_decision_agree_on_every_branch(tmp_path) -> None:
    for mode, intent, expected in (
        ("plan", WRITE, PermissionEffect.DENY),
        ("manual", "workspace.read_file", PermissionEffect.ALLOW),
        ("manual", WRITE, PermissionEffect.REQUIRE_APPROVAL),
    ):
        reset_mode_permission_state()
        ctx = _context(tmp_path, mode=mode)
        args = {"path": "a.txt"} if intent != WRITE else ARGS
        preview = preview_tool_call(intent=intent, arguments=args, task_id="t", source_context=ctx)
        decision = decide_tool_call(intent=intent, arguments=args, task_id="t", source_context=ctx)
        assert preview.effect is decision.effect is expected, (mode, intent)
        assert preview.actions == tuple(decision.actions)


def _proposal(op: str, args: dict) -> IntentProposal:
    return IntentProposal(index=0, request_text="write a.txt", operation=op, arguments=args)


def test_admission_preview_mints_no_grant_and_admits_nothing(tmp_path) -> None:
    ctx = _context(tmp_path)
    canonical = CanonicalText.of("write a.txt")
    before = _snapshot()
    for _ in range(3):
        preview = preview_admission(_proposal(WRITE, ARGS), canonical=canonical, task_id="task-a", source_context=ctx)
        assert preview.would_admit is False and preview.reason is ReasonCode.PERMISSION_DENIED
        assert preview.permission_effect == "require_approval" and preview.would_request_approval is True
    assert _snapshot() == before
    read = preview_admission(_proposal("workspace.read_file", {"path": "a.txt"}), canonical=canonical, task_id="task-a", source_context=ctx)
    assert read.would_admit is True and read.reason is ReasonCode.ADMITTED and read.permission_effect == "allow"
    assert _snapshot() == before, "an admission preview minted a grant or spent state"
    # And the real admission still admits and spends exactly once.
    result = admit(_proposal("workspace.read_file", {"path": "a.txt"}), canonical=canonical, task_id="task-a", source_context=ctx)
    assert result.admitted is True
    assert _snapshot()["grants"] == before["grants"] + 1


def test_admission_preview_refuses_bad_shape_and_unknown_operations_like_admit(tmp_path) -> None:
    ctx = _context(tmp_path)
    canonical = CanonicalText.of("do a thing")
    unknown = preview_admission(_proposal("no.such.op", {}), canonical=canonical, source_context=ctx)
    assert unknown.would_admit is False and unknown.reason is ReasonCode.UNKNOWN_OPERATION
    malformed = preview_admission(_proposal("workspace.read_file", {"path": ""}), canonical=canonical, source_context=ctx)
    assert malformed.would_admit is False and malformed.reason is ReasonCode.ARGUMENT_EXPANSION_FAILED
    assert "non-empty" in malformed.detail


def test_sabotage_a_preview_routed_through_the_consuming_decision_is_caught(tmp_path, monkeypatch) -> None:
    """Control: if preview_tool_call ever consumed, this file's snapshot comparison reds."""
    ctx = _context(tmp_path)
    request = decide_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=ctx).approval_request
    resolve_approval(request["approval_id"], decision="allow")
    approved = {**ctx, "mode_approval_token": request["approval_id"]}
    before = _snapshot()
    monkeypatch.setattr(mpp, "_matching_approval_covers", mpp._consume_matching_approval)
    preview_tool_call(intent=WRITE, arguments=ARGS, task_id="task-a", source_context=approved)
    assert _snapshot() != before, "the sabotaged preview should have spent the token; the snapshot did not notice"
