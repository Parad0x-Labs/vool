"""Public, unauthenticated, read-only network retrieval does not require Manual-mode approval.

Product decision (explicit operator authorization): a weather or market-price lookup is public
data with no side effects, distinct from every OTHER thing USE_NETWORK/ACCESS_EXTERNAL_PROVIDERS
covers -- sending, publishing, purchasing, and any authenticated call. Manual approval stays
required for: repository/workspace modification, side-effecting shell execution, authenticated or
private data, wallet transactions/signing, send/publish/purchase/submit, OS/system changes,
destructive operations, and material paid usage beyond configured automatic limits.

This is implemented as a capability/effect policy driven by each tool's own declared
`side_effect_class` (`core/runtime_tool_contracts.py`), never by matching a word like "weather" or
"price" in the user's prompt. The scope is deliberately narrow: `web.*` only (search/research/
fetch/browser-render), not a blanket `side_effect_class == "read_only"` rule -- `email.read` and
`x.trending` ALSO declare `side_effect_class="read_only"` in their contracts, but they read a
locally-configured, authenticated account, not public data. A blanket rule would have exempted
those too, directly against the "authenticated or private data still requires approval" contract --
confirmed live below, not asserted.
"""

from __future__ import annotations

import pytest

from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    OperatingMode,
    PermissionAction,
    PermissionEffect,
    actions_for_tool,
    decide_tool_call,
    reset_mode_permission_state,
    set_active_mode,
)


@pytest.fixture(autouse=True)
def _clean_mode_state() -> None:
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _manual_context(session: str = "chat-a") -> dict[str, object]:
    set_active_mode(session, "manual", project_id="", client_turn_id="turn-a")
    return {"runtime_session_id": session, "operating_mode": "manual", "workspace_root": "/tmp"}


@pytest.mark.parametrize("intent", ["web.search", "web.research", "web.fetch", "browser.render"])
def test_public_web_read_tools_are_classified_as_public_read_only_retrieval(intent: str) -> None:
    assert actions_for_tool(intent) == (PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL,)


@pytest.mark.parametrize("intent", ["web.search", "web.research", "web.fetch", "browser.render"])
def test_public_web_read_tools_are_allowed_without_approval_in_manual_mode(intent: str) -> None:
    decision = decide_tool_call(
        intent=intent, arguments={"query": "weather in Kaunas"}, task_id="t1", source_context=_manual_context()
    )
    assert decision.effect is PermissionEffect.ALLOW
    assert decision.allowed is True


def test_a_weather_and_a_market_lookup_both_run_without_approval() -> None:
    """The literal benchmark shape: two independent public read-only lookups, same turn."""
    ctx = _manual_context()
    weather = decide_tool_call(
        intent="web.search", arguments={"query": "weather in Kaunas, Tallinn, and Warsaw"}, task_id="t1", source_context=ctx
    )
    market = decide_tool_call(
        intent="web.research", arguments={"query": "gold silver bitcoin bnb price"}, task_id="t2", source_context=ctx
    )
    assert weather.allowed and market.allowed


def test_browser_automation_is_not_exempted_by_the_web_dot_prefix() -> None:
    """`browser.*` (arbitrary automation -- can click, submit, navigate) stays gated, unlike the
    pure-fetch `web.*` family."""
    decision = decide_tool_call(
        intent="browser.navigate", arguments={"url": "https://example.com"}, task_id="t1", source_context=_manual_context()
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL


def test_authenticated_reads_are_not_exempted_despite_also_declaring_read_only() -> None:
    """email.read and x.trending both declare side_effect_class="read_only" in their own
    contracts, same as web.search -- but they read a LOCALLY-CONFIGURED, AUTHENTICATED account,
    not public data. If the fix were a blanket read_only rule instead of a web.*-scoped one, these
    would incorrectly skip approval too. Confirmed live, not merely asserted from the contract
    text: both still classify to actions the Manual matrix gates."""
    for intent in ("email.read", "x.trending"):
        decision = decide_tool_call(intent=intent, arguments={}, task_id="t1", source_context=_manual_context())
        assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, intent
        assert PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL not in actions_for_tool(intent)


def test_mutating_and_destructive_actions_are_unaffected() -> None:
    ctx = _manual_context()
    for intent, args in (
        ("workspace.write_file", {"path": "x.txt", "content": "y"}),
        ("sandbox.run_command", {"command": "rm -rf /tmp/whatever"}),
        ("wallet.send", {}),
        ("email.send", {"to": "a@b.com", "body": "hi"}),
    ):
        decision = decide_tool_call(intent=intent, arguments=args, task_id="t1", source_context=ctx)
        assert decision.effect in (PermissionEffect.REQUIRE_APPROVAL, PermissionEffect.DENY), intent


def test_plan_mode_still_denies_public_web_reads_outright() -> None:
    """The new action is scoped to Manual/Review-edits only -- Plan mode's existing flat network
    denial must be untouched, not silently widened by a shared allow-set."""
    assert MODE_PERMISSION_MATRIX[OperatingMode.PLAN][PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL] is PermissionEffect.DENY


def test_review_edits_mode_also_allows_public_web_reads() -> None:
    """Review-edits shares Manual's read-only-network exemption -- both modes already share an
    identical base matrix construction for everything except edit review itself."""
    set_active_mode("chat-review", "review_edits", project_id="", client_turn_id="turn-a")
    ctx = {"runtime_session_id": "chat-review", "operating_mode": "review_edits", "workspace_root": "/tmp"}
    decision = decide_tool_call(intent="web.search", arguments={"query": "x"}, task_id="t1", source_context=ctx)
    assert decision.allowed is True


def test_auto_mode_still_allows_public_web_reads_too() -> None:
    """Regression guard for a real bug caught by tests/gauntlet/test_tool_surface_invariants.py's
    golden diff during development: web.search/web.research/web.fetch used to resolve to
    {USE_NETWORK, USE_BROWSER}, both already ALLOW in Auto mode. Once they resolved to the new
    PUBLIC_READ_ONLY_RETRIEVAL action instead, Auto's matrix construction did not list that new
    action in its own allow-set, so it silently fell through to DENY by default -- Auto (meant to
    be the MORE permissive mode) became MORE restrictive than Manual for these exact tools. Fixed
    by adding PUBLIC_READ_ONLY_RETRIEVAL to Auto's allow-set explicitly.
    """
    set_active_mode("chat-auto", "auto", project_id="", client_turn_id="turn-a")
    ctx = {"runtime_session_id": "chat-auto", "operating_mode": "auto", "workspace_root": "/tmp"}
    for intent in ("web.search", "web.research", "web.fetch"):
        decision = decide_tool_call(intent=intent, arguments={"query": "x"}, task_id="t1", source_context=ctx)
        assert decision.allowed is True, intent
