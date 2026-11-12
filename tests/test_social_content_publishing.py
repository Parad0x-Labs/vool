"""Publishing boundary and channel audit proofs (Checkpoint 6).

No test here makes a real network request or writes to a real account.
"""
from __future__ import annotations

import pytest

from core.social_content import publishing, review
from storage import social_content_store as store


@pytest.fixture()
def clean_store():
    from storage.db import get_connection

    conn = get_connection()
    drop = (
        "DROP TABLE IF EXISTS social_content_records;"
        "DROP TABLE IF EXISTS social_content_draft_versions;"
        "DROP TABLE IF EXISTS social_content_decisions;"
    )
    conn.executescript(drop)
    yield store
    conn.executescript(drop)


def _draft(platform="x", body="copy ready bytes"):
    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    return store.save_draft(
        campaign_id=campaign["campaign_id"], platform=platform, body=body
    )


@pytest.mark.parametrize("platform", ["x", "linkedin", "bluesky", "mastodon", "threads"])
def test_unsupported_platforms_return_typed_unavailable(clean_store, platform):
    draft = _draft(platform=platform)
    result = publishing.handoff(
        store, draft_id=draft["draft_id"], destination_platform=platform
    )
    assert result["outcome"] == "publishing_unavailable"
    assert result["copy_ready_bytes"] == "copy ready bytes"
    assert "no network call was made" in result["summary"]
    assert publishing.publishing_tools_available() == []


def test_telegram_discord_handoff_refused_even_when_approved(clean_store):
    """The canonical path's ungated direct-fallback means the full authority
    chain cannot be proven end to end — so the handoff is typed refused, never
    a direct execution."""
    draft = _draft(platform="telegram")
    unapproved = publishing.handoff(
        store, draft_id=draft["draft_id"], destination_platform="telegram"
    )
    assert unapproved["outcome"] == "handoff_refused"
    assert "approval is required" in unapproved["summary"]

    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    approved = publishing.handoff(
        store, draft_id=draft["draft_id"], destination_platform="discord"
    )
    assert approved["outcome"] == "handoff_refused"
    assert approved["release_critical_handoff"] is True
    assert "direct-fallback" in approved["summary"]
    assert approved["copy_ready_bytes"] == "copy ready bytes"


def test_scheduling_is_typed_unavailable(clean_store):
    draft = _draft()
    result = publishing.schedule(store, draft_id=draft["draft_id"], when="tomorrow 9am")
    assert result["outcome"] == "scheduling_unavailable"
    assert "local planning data" in result["summary"]


def test_no_post_is_a_successful_terminal_decision(clean_store):
    draft = _draft()
    decision = store.record_decision(
        draft_id=draft["draft_id"], draft_version=1,
        content_hash=draft["content_hash"],
        platform="x", decision="NO_POST", reason="evidence too weak",
    )
    assert decision["decision"] == "NO_POST"
    assert store.approval_for(
        draft_id=draft["draft_id"], version=1,
        content_hash=draft["content_hash"], platform="x",
    ) is None  # NO_POST never acts as an approval


def test_fake_connector_cannot_be_smuggled_into_the_offer(clean_store):
    ok, why = publishing.offer_is_safe(
        ("workspace.write_file", "channel.post", "web.search")
    )
    assert not ok and "smuggled" in why
    ok, _ = publishing.offer_is_safe(("workspace.write_file",))
    assert ok


def test_fake_plugin_connector_is_refused_by_contract_validation(clean_store):
    """A plugin/skill body cannot grant a publishing tool: tool-intents must
    exist in the runtime tool contract map; an invented connector is refused
    at load with a typed violation."""
    from core.native_skill_library import SkillContract, contract_violations

    contract = SkillContract(
        id="fake-connector", name="fake-connector", description="posts for you",
        version="1.0.0", risk_class="read_only",
        task_families=(), capability_families=("communication",),
        tool_intents=("channel.post",), permitted_tools=(), prerequisites=(),
        expected_outputs=("posted",), verification=("deterministic_evidence",),
        stopping_conditions=("stop",), incompatible_with=(), capabilities=(),
        priority=100, source="plugin:fake", path="skills/fake/SKILL.md", body="x",
    )
    violations = contract_violations(contract)
    assert any("tool names this runtime does not have" in v for v in violations)


def test_remote_channel_request_cannot_mutate_or_publish(clean_store):
    """A Telegram/Discord-originated turn (remote principal) cannot approve,
    change campaign state, or inherit owner-local authority."""
    from core.command_registry.execute import ExecutionContext, execute_command

    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    for command, payload in (
        ("campaign.update", {"campaign_id": campaign["campaign_id"],
                             "expected_version": 1, "status": "active"}),
        ("draft.approve", {"draft_id": "draft-x", "draft_version": 1,
                           "decision": "approved"}),
        ("metrics.import", {"project_id": "p1", "metric_name": "x", "value": 1}),
    ):
        env = execute_command(
            command, payload, context=ExecutionContext(principal="remote_untrusted")
        )
        assert (not env.ok) and env.fault.code in (
            "permission_denied", "permission_required"
        ), command
    # and campaign state is unchanged
    assert store.get_campaign(campaign["campaign_id"])["status"] == "draft"


def test_channel_audit_is_recorded_honestly(clean_store):
    assert "direct-fallback" in publishing.DIRECT_FALLBACK_AUDIT
    assert publishing.NO_CONNECTOR_PLATFORMS == ("x", "linkedin", "bluesky", "mastodon", "threads")
