"""A Manual-mode write that needs approval must leave an ACTIONABLE approval on screen.

Reported 2026-09-06 on the delivered build: "create a folder on the desktop" in Manual mode read
as a refusal; the operator switched the mode to get the folder. The machine write fast path
returns `pending_approval`, emits `task_pending_approval` (typed `permission.required`) and then
completes the turn. This drives the REAL page under the node DOM harness with exactly that typed
sequence and asserts the permission bar is visible and holds the request after the turn ended.
"""
from __future__ import annotations

import json

from tests.test_multichat_navigation_unlock import _drive

A = "sess-approval-a"
APPROVAL = {
    "approval_id": "appr-1", "intent": "machine.ensure_directory", "action": "Create directory ~/Desktop/vool_test",
    "affected_resources": ["~/Desktop/vool_test"], "expected_side_effects": "one new empty directory",
    "reversible": True, "scope_options": ["once"],
}


def _run(events_js: str) -> dict:
    return _drive(f"""
setDisplayedChat('{A}');
runTurn('can you create a folder on the desktop for me? name it vool_test', null, {{ chatId: '{A}' }});
await tick();
__streams['{A}'].push(ev({{ type: 'task.started', seq: 1, stage: 'Understanding' }}));
{events_js}
__streams['{A}'].close();
await tick(6);
const st = chatState('{A}');
out({{
  barHidden: permBarEl ? permBarEl.hidden : null,
  barText: permBarEl ? String(permMsgEl.textContent) : '',
  pending: !!st.pendingApproval,
  pendingId: st.pendingApproval ? st.pendingApproval.approval_id : null,
  runStatus: st.run ? st.run.status : null,
  runEnded: st.run ? st.run.ended : null,
  permissionFlag: st.run ? st.run.permission : null,
}});
""")


def test_fast_path_pending_approval_leaves_the_permission_bar_visible_after_the_turn_ends() -> None:
    data = _run(f"""
__streams['{A}'].push(ev({{ type: 'permission.required', seq: 2, stage: 'Waiting for permission', summary: 'machine.ensure_directory needs your approval', approval: {json.dumps(APPROVAL)} }}));
__streams['{A}'].push(chunk('`machine.ensure_directory` needs your approval before it runs. Manual mode requires approval for this exact action.'));
__streams['{A}'].push(ev({{ type: 'task.completed', seq: 3, summary: 'Completed' }}));
""")
    assert data["pending"] is True and data["pendingId"] == "appr-1", data
    assert data["barHidden"] is False, f"the approval bar must stay on screen for the operator to act: {data}"
    assert "vool_test" in data["barText"] or "directory" in data["barText"].lower(), data


def test_a_lifecycle_receipt_without_a_token_never_raises_an_unanswerable_bar() -> None:
    """`task_pending_approval` without an approval id is a receipt, not a request; it must not
    paint a bar the operator cannot answer."""
    data = _run(f"""
__streams['{A}'].push(ev({{ type: 'permission.required', seq: 2, stage: 'Waiting for permission', summary: 'needs approval' }}));
__streams['{A}'].push(ev({{ type: 'task.completed', seq: 3, summary: 'Completed' }}));
""")
    assert data["pending"] is False
    assert data["barHidden"] in (True, None), data
