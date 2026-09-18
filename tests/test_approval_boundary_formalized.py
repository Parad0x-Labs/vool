"""The full approval boundary, verified explicitly by category -- not just spot-checked.

Automatically allowed in Manual mode (the product decision): public unauthenticated web
search/research/fetch/browser-render -- which covers weather and market retrieval, since both
resolve through those same intents (verified: `tools_intent` on every LiveDataSubtask is
"web.research").

Still approval-required, verified one category at a time: authenticated email read, authenticated
social/account access, private connector data, filesystem/workspace writes, side-effecting shell
commands, wallet/signing, sending/publishing, purchases, OS/settings changes.

The boundary is NOT "any web.* intent" -- it is "a web.* intent whose own contract declares
side_effect_class=='read_only'". Proven by direct contrast: email.read/x.trending declare the same
side_effect_class but are NOT web.*-prefixed, so they never reach the check at all; if the check
were ever loosened to a bare side_effect_class test (no prefix scope), they would incorrectly pass
-- covered by the dedicated sabotage in test_public_read_only_retrieval_skips_manual_approval.py.
"""

from __future__ import annotations

import pytest

from core.mode_permission_policy import (
    PermissionEffect,
    decide_tool_call,
    reset_mode_permission_state,
    set_active_mode,
)


@pytest.fixture(autouse=True)
def _clean_mode_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _manual_ctx(session: str = "chat-boundary") -> dict[str, object]:
    set_active_mode(session, "manual", project_id="", client_turn_id="turn-a")
    return {"runtime_session_id": session, "operating_mode": "manual", "workspace_root": "/tmp"}


# --- automatically allowed ---

@pytest.mark.parametrize("intent", ["web.search", "web.research", "web.fetch", "browser.render"])
def test_public_unauthenticated_web_retrieval_is_allowed(intent: str) -> None:
    decision = decide_tool_call(intent=intent, arguments={"query": "x"}, task_id="t", source_context=_manual_ctx())
    assert decision.allowed is True, intent


# --- still approval-required, one category at a time ---

def test_authenticated_email_read_requires_approval() -> None:
    decision = decide_tool_call(intent="email.read", arguments={}, task_id="t", source_context=_manual_ctx())
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL


def test_authenticated_social_account_access_requires_approval() -> None:
    decision = decide_tool_call(intent="x.trending", arguments={}, task_id="t", source_context=_manual_ctx())
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL


def test_private_connector_data_requires_approval() -> None:
    """email.read/x.trending ARE the private-connector examples this codebase actually has today
    (locally-configured IMAP account / bearer token) -- covered above under their own names; this
    test documents the category explicitly rather than silently relying on the two above."""
    for intent in ("email.read", "x.trending"):
        decision = decide_tool_call(intent=intent, arguments={}, task_id="t", source_context=_manual_ctx())
        assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, intent


def test_filesystem_or_workspace_writes_require_approval() -> None:
    for intent, args in (
        ("workspace.write_file", {"path": "x.txt", "content": "y"}),
        ("workspace.apply_unified_diff", {"path": "x.txt", "diff": "..."}),
        ("machine.write_file", {"path": "x.txt", "content": "y"}),
    ):
        decision = decide_tool_call(intent=intent, arguments=args, task_id="t", source_context=_manual_ctx())
        assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, intent


def test_side_effecting_shell_commands_require_approval() -> None:
    decision = decide_tool_call(
        intent="sandbox.run_command", arguments={"command": "rm -rf /tmp/whatever"}, task_id="t", source_context=_manual_ctx()
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL


def test_wallet_and_signing_actions_require_approval() -> None:
    for intent in ("wallet.send", "pay.x402"):
        decision = decide_tool_call(intent=intent, arguments={}, task_id="t", source_context=_manual_ctx())
        assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, intent


def test_sending_and_publishing_require_approval() -> None:
    for intent, args in (
        ("email.send", {"to": "a@b.com", "body": "hi"}),
        ("email.reply", {"to": "a@b.com", "body": "hi"}),
        ("web0.publish", {}),
    ):
        decision = decide_tool_call(intent=intent, arguments=args, task_id="t", source_context=_manual_ctx())
        assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, intent


def test_purchases_require_approval() -> None:
    decision = decide_tool_call(intent="marketplace.purchase_knowledge", arguments={}, task_id="t", source_context=_manual_ctx())
    assert decision.effect in (PermissionEffect.REQUIRE_APPROVAL, PermissionEffect.DENY)


def test_os_and_settings_changes_require_approval() -> None:
    for intent in ("settings.update", "secret.store"):
        decision = decide_tool_call(intent=intent, arguments={}, task_id="t", source_context=_manual_ctx())
        assert decision.effect in (PermissionEffect.REQUIRE_APPROVAL, PermissionEffect.DENY), intent


def test_mixed_plan_preserves_the_public_lookup_and_the_pending_action_within_one_turn() -> None:
    """Within a single turn, a mixed batch (one public lookup + one approval-required action)
    already preserves both -- see test_a_subtask_requiring_approval_is_not_executed_and_is_
    preserved_not_dropped in test_live_data_runner.py for the full drive.

    NOT yet true, and not claimed here: "approval or denial resumes the SAME PLAN" across turns.
    LiveDataPlan today only models market_quote/weather_lookup operations, so there is no typed
    representation of an arbitrary approval-required action (e.g. a workspace write) living
    alongside a market/weather subtask in the SAME plan object, and even if there were, resuming
    it on the next turn needs the plan to survive somewhere the next turn can read it back -- which
    depends on turn/attempt persistence (step 7 of the current work order), not built yet. This is
    an honest scope boundary, not a gap papered over: do not read this test file as proving
    cross-turn resume.
    """
    pytest.skip(
        "cross-turn plan resume depends on turn/attempt persistence (step 7), not built yet -- "
        "see test_live_data_runner.py for the within-one-turn preservation property this test "
        "file documents but does not re-test"
    )


def test_the_boundary_is_the_contract_not_the_prefix_alone() -> None:
    """Direct proof the check is `web.* AND side_effect_class=='read_only'`, not `web.*` alone: no
    web.*-prefixed intent that mutates or sends exists in this registry to test against directly,
    so this asserts the actual mechanism instead -- the same contract lookup a non-read-only web.*
    tool would hit if one existed."""
    from core.mode_permission_policy import _web_tool_is_public_read_only

    assert _web_tool_is_public_read_only("web.search") is True
    assert _web_tool_is_public_read_only("web.research") is True
    # A web.*-prefixed name with no registered contract at all must fail closed (not error, not
    # default-allow) -- `side_effect_class_for_intent` returns "" for an unknown intent, which is
    # never "read_only".
    assert _web_tool_is_public_read_only("web.nonexistent_intent") is False
